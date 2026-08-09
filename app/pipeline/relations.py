"""Анализ отношений после раунда (decomposition.md §4.2, Milestone 6A).

Вынесено из ``app/chat_engine.py`` без изменения поведения (тела функций
перенесены 1:1 из фасада):
- ``_analyze_and_update_relationships`` — пост-раундный анализ всех пар;
- ``_run_sensors_relationship_proposal`` — sensors-предложения по отношениям;
- ``_run_per_pair_analysis`` + evidence/constrain — попарный fallback,
  детерминированный гейт адмиссибилити и сдерживание дельт;
- hearsay caps (``_hearsay_effective_cap`` / ``_compute_hearsay_effective_cap``).

Модуль импортирует только публичные API модулей (``crud``, ``schemas``,
``relationship_service``, ``relationship_analyzer``, ``perception`` и т.д.).
Фасад ``app/chat_engine.py`` реэкспортирует эти функции для совместимости
(патчи тестов, ``routers/relationships.py``); снятие фасада — спринт 10.
"""

import logging
from datetime import datetime
from types import SimpleNamespace

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import crud
from .. import models
from .. import perception
from .. import relationship_analyzer
from .. import relationship_service
from .. import schemas
from ..config import settings
from ..database import AsyncSessionLocal
from ..relationship_interpreter import TRUST_LOW, interpret as _interpret_rel

logger = logging.getLogger("app.chat_engine.pipeline.relations")


async def _analyze_and_update_relationships(
    client: httpx.AsyncClient,
    chat_id: int,
    model_name: str,
    round_snapshots: list[dict],
    character_snapshots: list[dict],
    round_id: str | None = None,
) -> dict:
    """Analyze relationships for all character pairs and apply deltas (Sprint 4).

    Only NPCs are analyzed as relationship *sources*. The player is a valid
    *target* (bots -> player) but never a source (player -> bots is not tracked).

    The default path uses the batch analyzer: a single LLM call covers every
    pair with evidence (§8). Every proposed delta/issue is then passed through
    the deterministic evidence gate (§8.3) — a pair without evidence rejects
    everything. When the batch fails or is disabled, the per-pair analyzer is
    used (§8.4); the fallback never disables evidence-gating.

    ``round_id`` is the stable per-turn anchor from §6, computed once per user
    message in ``process_user_message_streaming``.

    Opens its own DB session instead of borrowing the caller's, so the
    connection is always returned to the pool when the task finishes.

    Single-transaction contract: all deltas/issues/decay/pruning are staged in
    the session and committed with ONE ``flush()`` + ``commit()`` at the end.
    Returns an observability summary dict (counts per action); background
    callers ignore it, the on-demand API endpoint returns it.
    """
    # Stable round anchor (docs/relations.md §6). Kept for direct calls.
    if round_id is None:
        round_id = f"round_{chat_id}_{datetime.utcnow().isoformat()}"

    summary: dict = {
        "round_id": round_id,
        "analyzed_pairs": 0,
        "applied_deltas": 0,
        "created_issues": 0,
        "resolved_issues": 0,
        "created_events": 0,
        "decay_events": 0,
        "pruned_events": 0,
    }
    affected_relationship_ids: set[int] = set()
    try:
        async with AsyncSessionLocal() as db:
            player = await crud.get_player_character(db, chat_id)
            player_id = player.id if player else None

            # Rebuild lightweight character objects from precomputed snapshots
            all_chars = [
                SimpleNamespace(
                    id=snap["id"],
                    name=snap["name"],
                    location=snap.get("location") or "",
                    is_player=False,
                )
                for snap in character_snapshots
            ]
            if player:
                all_chars.append(player)

            character_names = {c.id: c.name for c in all_chars}
            character_locations = {
                c.id: getattr(c, "location", "") or "" for c in all_chars
            }

            # Only NPCs are sources; targets include the player
            sources = [c for c in all_chars if not getattr(c, "is_player", False)]

            # Issues mentioned this round: those passed to the analyzer for an
            # analyzed pair, plus those selected into each source's
            # generation-context `<open_issue data>` block (§7.4 salience).
            mentioned_issue_ids: set[int] = set()

            # Per-pair analysis inputs, shared by the batch prompt and the
            # per-pair fallback.
            pairs: list[dict] = []
            for source_char in sources:
                for target_char in all_chars:
                    if source_char.id == target_char.id:
                        continue

                    pair_ctx = _build_pair_relationship_context(
                        round_snapshots,
                        source_char,
                        target_char,
                        character_names,
                        character_locations,
                        player_id=player_id,
                    )

                    # Deterministic hearsay reliability (§12): resolve the
                    # effective delta cap from stored edges (trust in the
                    # teller, teller->target valence). Stored on pair_ctx so
                    # both the batch path and the per-pair fallback apply it.
                    if (
                        pair_ctx.get("hearsay")
                        and pair_ctx.get("hearsay_source") is not None
                    ):
                        try:
                            pair_ctx["hearsay_effective_cap"] = (
                                await _compute_hearsay_effective_cap(
                                    db,
                                    source_char.id,
                                    pair_ctx["hearsay_source"],
                                    target_char.id,
                                )
                            )
                        except Exception as exc:
                            logger.warning(
                                "[chat_id=%d] Failed to compute hearsay cap "
                                "for %d->%d: %s",
                                chat_id, source_char.id, target_char.id, exc,
                            )

                    # Reciprocity pipeline (Sprint 7, §10): belief-driven cap
                    # multiplier for this directed pair. Applied by
                    # ``_constrain_pair_delta`` in both the batch and the
                    # per-pair fallback paths.
                    if settings.reciprocity_enabled:
                        try:
                            pair_ctx["reciprocity_belief_multiplier"] = (
                                await relationship_service.compute_reciprocity_belief_multiplier(
                                    db,
                                    source_char.id,
                                    target_char.name,
                                )
                            )
                        except Exception as exc:
                            logger.warning(
                                "[chat_id=%d] Failed to compute belief multiplier "
                                "for %d->%d: %s",
                                chat_id, source_char.id, target_char.id, exc,
                            )
                            pair_ctx["reciprocity_belief_multiplier"] = 1.0

                    if (
                        not pair_ctx["any_evidence"]
                        and settings.relationship_analyze_only_interacting_pairs
                    ):
                        continue

                    rel = await relationship_service.get_relationship(
                        db, source_char.id, target_char.id,
                    )
                    if rel is None:
                        rel = await relationship_service.get_or_create_relationship(
                            db, chat_id, source_char.id, target_char.id,
                        )

                    recent_events = await relationship_service.get_recent_events(
                        db, rel, limit=settings.relationship_max_events_in_prompt,
                    )
                    events_text = "\n".join(
                        f"  - {e.description}" for e in recent_events if e.description
                    )

                    open_issues = await relationship_service.list_open_issues(db, rel)
                    open_issues_payload = [
                        {
                            "id": issue.id,
                            "issue_type": issue.issue_type,
                            "text": issue.text,
                        }
                        for issue in open_issues
                    ]
                    mentioned_issue_ids.update(issue.id for issue in open_issues)

                    # Trajectory (docs/relations.md §11): snapshot-based from LLM events
                    trajectory_events = await relationship_service.get_trajectory_events(
                        db, rel.id, window=settings.relationship_trajectory_window,
                    )
                    trajectory_text = relationship_service.build_trajectory_block(
                        trajectory_events,
                        source_char.name,
                        target_char.name,
                    )

                    # Anti-inflation (§27.3): snapshot-based recent gains per
                    # metric from the same trajectory events, consumed by the
                    # saturation guard inside _constrain_pair_delta.
                    pair_ctx["recent_gains"] = {
                        metric: relationship_service.trajectory_metric_gain(
                            trajectory_events, metric,
                        )
                        for metric in (
                            "affection", "trust", "attraction",
                            "resentment", "jealousy",
                        )
                    }

                    # Triadic MVP (§13): build third-party notes for target's
                    # relationships with characters mentioned in this round.
                    third_party_notes: list[str] = []
                    third_party_ids = pair_ctx.get("third_party_ids", [])
                    for third_id in third_party_ids:
                        if third_id == source_char.id or third_id == target_char.id:
                            continue
                        third_rel = await relationship_service.get_relationship(
                            db, target_char.id, third_id,
                        )
                        if third_rel is None:
                            continue
                        third_name = character_names.get(third_id, f"ID:{third_id}")
                        target_name = character_names.get(target_char.id, f"ID:{target_char.id}")
                        # Format: [третье лицо] {target} ↔ {third}: {type}, {метрика}={значение}
                        # Show relationship_type + top non-zero metric
                        metrics = [
                            ("привязанность", third_rel.affection),
                            ("доверие", third_rel.trust),
                            ("влечение", third_rel.attraction),
                            ("обида", third_rel.resentment),
                            ("ревность", third_rel.jealousy),
                        ]
                        non_zero = [(name, val) for name, val in metrics if val > 0]
                        if non_zero:
                            # Pick highest metric
                            top_metric = max(non_zero, key=lambda x: x[1])
                            metric_str = f"{top_metric[0]}={top_metric[1]}"
                        else:
                            metric_str = "нейтральное"
                        note = f"[третье лицо] {target_name} ↔ {third_name}: {third_rel.relationship_type}, {metric_str}"
                        third_party_notes.append(note)

                    pairs.append(
                        {
                            "source_char": source_char,
                            "target_char": target_char,
                            "source_id": source_char.id,
                            "target_id": target_char.id,
                            "source_name": source_char.name,
                            "target_name": target_char.name,
                            "pair_ctx": pair_ctx,
                            "mode": _evidence_mode(pair_ctx),
                            "rel": rel,
                            "affection": rel.affection,
                            "trust": rel.trust,
                            "attraction": rel.attraction,
                            "resentment": rel.resentment,
                            "jealousy": rel.jealousy,
                            "current_type": rel.relationship_type,
                            "recent_events_text": events_text,
                            "open_issues": open_issues_payload,
                            "third_party_notes": third_party_notes,
                            "trajectory": trajectory_text,
                        }
                    )

            # Issues selected into per-source generation contexts count as
            # mentioned even when the pair itself had no analysis evidence.
            if settings.relationship_issues_enabled:
                for source_char in sources:
                    for issue in await relationship_service.list_top_open_issues_for_character(
                        db, chat_id, source_char.id,
                        limit=settings.relationship_max_issues_in_prompt,
                    ):
                        mentioned_issue_ids.add(issue.id)

            if settings.relationship_batch_enabled and pairs:
                pair_by_key = {
                    (p["source_id"], p["target_id"]): p for p in pairs
                }
                known_pairs = set(pair_by_key)
                scene_text = _build_batch_scene_summary(
                    round_snapshots,
                    character_names,
                    character_locations,
                    player_id=player_id,
                )
                prompt_pairs = [
                    {
                        "source_name": p["source_name"],
                        "target_name": p["target_name"],
                        "source_id": p["source_id"],
                        "target_id": p["target_id"],
                        "mode": p["mode"],
                        "current_type": p["current_type"],
                        "affection": p["affection"],
                        "trust": p["trust"],
                        "attraction": p["attraction"],
                        "resentment": p["resentment"],
                        "jealousy": p["jealousy"],
                        "interaction_summary": p["pair_ctx"]["interaction_summary"],
                        "recent_events_text": p["recent_events_text"],
                        "open_issues": p["open_issues"],
                        "excerpt": p["pair_ctx"]["excerpt"],
                        "hearsay_cap": p["pair_ctx"].get("hearsay_effective_cap"),
                        "hearsay_source_name": (
                            character_names.get(p["pair_ctx"]["hearsay_source"], "")
                            if p["pair_ctx"].get("hearsay_source") is not None
                            else ""
                        ),
                        "third_party_notes": p.get("third_party_notes", []),
                        "trajectory": p.get("trajectory", ""),
                    }
                    for p in pairs
                ]
                try:
                    deltas, orphan_issues = (
                        await relationship_analyzer.analyze_batch_relationships(
                            client=client,
                            model_name=model_name,
                            scene_text=scene_text,
                            pairs=prompt_pairs,
                            known_pairs=known_pairs,
                        )
                    )
                except Exception as exc:
                    logger.warning(
                        "[chat_id=%d] Batch relationship analysis failed: %s",
                        chat_id, exc,
                    )
                    deltas = None

                summary["analyzed_pairs"] = len(pairs)

                if deltas is None:
                    if settings.relationship_batch_fallback:
                        logger.info(
                            "[chat_id=%d] Falling back to per-pair analysis",
                            chat_id,
                        )
                        applied, affected = await _run_per_pair_analysis(
                            db, chat_id, client, model_name, pairs,
                            round_id=round_id,
                        )
                        summary["applied_deltas"] = applied
                        affected_relationship_ids.update(affected)
                    else:
                        logger.warning(
                            "[chat_id=%d] Batch failed and fallback disabled; "
                            "skipping relationship update", chat_id,
                        )
                else:
                    # Issues for edges with no metric delta (§8.1): apply them
                    # directly without touching the edge's metrics/type.
                    for issue in orphan_issues:
                        p = pair_by_key.get(
                            (issue.source_character_id, issue.target_character_id)
                        )
                        if p is None:
                            continue
                        affected_relationship_ids.add(p["rel"].id)
                        applied_issues = await relationship_service.apply_issue_deltas(
                            db, [issue], rel=p["rel"], round_id=round_id,
                        )
                        for applied_issue in applied_issues:
                            if applied_issue.state == "open":
                                summary["created_issues"] += 1
                            elif applied_issue.state == "resolved":
                                summary["resolved_issues"] += 1
                    # Evidence-gated metric deltas (§8.3).
                    for delta in deltas:
                        p = pair_by_key.get(
                            (delta.source_character_id, delta.target_character_id)
                        )
                        if p is None:
                            continue
                        gated = _constrain_pair_delta(delta, p["rel"], p["pair_ctx"])
                        if gated is None:
                            continue
                        affected_relationship_ids.add(p["rel"].id)
                        await relationship_service.apply_delta(
                            db, gated, chat_id, round_id=round_id,
                        )
                        summary["applied_deltas"] += 1
            else:
                applied, affected = await _run_per_pair_analysis(
                    db, chat_id, client, model_name, pairs,
                    round_id=round_id,
                )
                summary["applied_deltas"] = applied
                affected_relationship_ids.update(affected)

            # Sensors relationship hook (Sprint 7, §5.1.3): SensorsModel
            # предлагает дельты для пары; применяются ТОЛЬКО через существующую
            # систему правил (evidence gating, _constrain_pair_delta, caps).
            # Sensors отношения напрямую не меняет. 0–1 вызов на раунд (§24.1).
            try:
                sensors_applied, sensors_affected = (
                    await _run_sensors_relationship_proposal(
                        db, chat_id, client, pairs, round_id=round_id,
                    )
                )
                summary["applied_deltas"] += sensors_applied
                affected_relationship_ids.update(sensors_affected)
            except Exception as exc:
                logger.warning(
                    "[chat_id=%d] Sensors relationship proposal failed: %s",
                    chat_id, exc,
                )

            # Deterministic salience tick: advance counters for unmentioned
            # open issues, reset mentioned ones (§7.4, Sprint 1 п.7).
            try:
                await relationship_service.tick_open_issues(
                    db, chat_id, round_id=round_id, mentioned_ids=mentioned_issue_ids,
                )
            except Exception as exc:
                logger.warning(
                    "[chat_id=%d] Issue salience tick failed: %s", chat_id, exc
                )

            # Apply deterministic decay (Sprint 3 item 16, docs/relations.md §18).
            # Runs after LLM deltas and issue tick, using current round_id.
            try:
                decay_events = await relationship_service.apply_decay(
                    db, chat_id, round_id=round_id,
                )
                summary["decay_events"] = len(decay_events)
                if decay_events:
                    logger.debug(
                        "[chat_id=%d] Created %d decay events",
                        chat_id, len(decay_events),
                    )
            except Exception as exc:
                logger.warning(
                    "[chat_id=%d] Decay application failed: %s", chat_id, exc
                )

            # Event pruning (Sprint 4 item 3): fold old events of every pair
            # that changed this round into a single archive entry.
            for rel_id in affected_relationship_ids:
                try:
                    archive = await relationship_service.prune_relationship_events(
                        db, rel_id,
                    )
                    if archive is not None:
                        summary["pruned_events"] += 1
                except Exception as exc:
                    logger.warning(
                        "[chat_id=%d] Pruning failed for rel %d: %s",
                        chat_id, rel_id, exc,
                    )

            # Count LLM events created this round (visible only after flush).
            try:
                await db.flush()
                count_stmt = (
                    select(func.count())
                    .select_from(models.RelationshipEvent)
                    .where(
                        models.RelationshipEvent.round_id == round_id,
                        models.RelationshipEvent.kind == "llm",
                    )
                )
                summary["created_events"] = (
                    (await db.execute(count_stmt)).scalar() or 0
                )
            except Exception as exc:
                logger.warning(
                    "[chat_id=%d] Failed to flush/count events: %s", chat_id, exc
                )

            # Single flush + commit for the whole round (Sprint 4 item 2).
            await db.commit()
            logger.info(
                "relationship_analysis_complete",
                extra={"chat_id": chat_id, **summary},
            )
            return summary
    except Exception:
        logger.exception("[chat_id=%d] Relationship analysis failed", chat_id)
        summary["error"] = "relationship analysis failed"
        return summary


async def _run_sensors_relationship_proposal(
    db: AsyncSession,
    chat_id: int,
    client: httpx.AsyncClient,
    pairs: list[dict],
    *,
    round_id: str,
) -> tuple[int, set[int]]:
    """Sensors relationship hook (Sprint 7, §5.1.3).

    SensorsModel предлагает ``{affection_delta, trust_delta, resentment_delta,
    jealousy_delta, attraction_delta}`` для пары source→target. Дельты
    применяются ТОЛЬКО через существующую систему правил: evidence gating
    (``_constrain_pair_delta``), caps, normalize, decay. Sensors отношения
    напрямую не меняет и не пишет в БД (§5.1.4).

    Один вызов на раунд для наиболее «значимой» пары (direct > observed >
    hearsay; среди равных — самая длинная выдержка). Возвращает
    ``(applied_count, affected_relationship_ids)``.
    """
    from ..sensors_service import sensors_service

    if not settings.sensors_relationship_enabled:
        return 0, set()
    if not sensors_service.is_enabled("relationship"):
        return 0, set()

    candidates = [
        p for p in pairs if _evidence_mode(p["pair_ctx"]) != "none"
    ]
    if not candidates:
        return 0, set()

    mode_rank = {"direct": 3, "observed": 2, "hearsay": 1}
    candidates.sort(
        key=lambda p: (
            mode_rank.get(_evidence_mode(p["pair_ctx"]), 0),
            len(p["pair_ctx"].get("excerpt") or ""),
        ),
        reverse=True,
    )
    best = candidates[0]

    minimal_context = best["pair_ctx"].get("excerpt") or "взаимодействия не было"
    current_state = (
        f"{best['source_name']} -> {best['target_name']}: "
        f"affection={best['affection']}, trust={best['trust']}, "
        f"attraction={best['attraction']}, resentment={best['resentment']}, "
        f"jealousy={best['jealousy']}, type={best['current_type']}"
    )
    result = await sensors_service.run(
        client,
        task="relationship",
        minimal_context=minimal_context,
        current_state=current_state,
    )
    if result is None:
        return 0, set()

    delta = schemas.RelationshipDelta(
        source_character_id=best["source_id"],
        target_character_id=best["target_id"],
        delta_affection=int(result.get("affection_delta", 0)),
        delta_trust=int(result.get("trust_delta", 0)),
        delta_attraction=int(result.get("attraction_delta", 0)),
        delta_resentment=int(result.get("resentment_delta", 0)),
        delta_jealousy=int(result.get("jealousy_delta", 0)),
        reason="Sensors relationship analysis",
        description="",
        importance=5,
    )
    gated = _constrain_pair_delta(delta, best["rel"], best["pair_ctx"])
    if gated is None:
        return 0, set()
    await relationship_service.apply_delta(db, gated, chat_id, round_id=round_id)
    return 1, {best["rel"].id}


async def _run_per_pair_analysis(
    db: AsyncSession,
    chat_id: int,
    client: httpx.AsyncClient,
    model_name: str,
    pairs: list[dict],
    *,
    round_id: str,
) -> tuple[int, set[int]]:
    """Per-pair relationship analysis (docs/relations.md §8.4 fallback path).

    Applies the same deterministic evidence gating (§8.3) as the batch path —
    the fallback never disables gating. Returns ``(applied_delta_count,
    affected_relationship_ids)`` for the caller's observability summary.
    """
    applied = 0
    affected: set[int] = set()
    for p in pairs:
        deltas = await relationship_analyzer.analyze_relationships(
            client=client,
            model_name=model_name,
            source_name=p["source_name"],
            target_name=p["target_name"],
            current_type=p["current_type"],
            affection=p["affection"],
            trust=p["trust"],
            attraction=p["attraction"],
            resentment=p["resentment"],
            jealousy=p["jealousy"],
            recent_events_text=p["recent_events_text"],
            round_text=p["pair_ctx"]["excerpt"],
            source_character_id=p["source_id"],
            target_character_id=p["target_id"],
            interaction_summary=p["pair_ctx"]["interaction_summary"],
            direct_interaction=p["pair_ctx"]["direct_interaction"],
            observed_target=p["pair_ctx"]["observed_target"],
            hearsay=p["pair_ctx"].get("hearsay", False),
            hearsay_cap=p["pair_ctx"].get("hearsay_effective_cap"),
            open_issues=p["open_issues"],
            third_party_notes=p.get("third_party_notes"),
        )
        for delta in deltas:
            gated = _constrain_pair_delta(delta, p["rel"], p["pair_ctx"])
            if gated is None:
                continue
            affected.add(p["rel"].id)
            await relationship_service.apply_delta(
                db, gated, chat_id, round_id=round_id,
            )
            applied += 1
    return applied, affected


def _text_mentions_name(content: str, name: str) -> bool:
    if not name or not content:
        return False
    import re
    pattern = rf"(?<!\w){re.escape(name)}(?!\w)"
    return bool(re.search(pattern, content, flags=re.IGNORECASE))


def _build_pair_relationship_context(
    round_snapshots: list[dict],
    source,
    target,
    character_names: dict[int, str],
    character_locations: dict[int, str],
    player_id: int | None = None,
    max_lines: int | None = None,
) -> dict:
    """Build a pair-specific excerpt of the round for relation source -> target.

    Only lines the *source* could perceive and that concern the *target* are
    kept, so events aimed at other characters are not misattributed. Each line
    is annotated with the speaker and addressees.

    Returns: excerpt, interaction_summary, direct_interaction, observed_target,
    any_evidence, third_party_ids (for Triadic MVP).
    """
    if max_lines is None:
        max_lines = settings.relationship_max_pair_context_lines

    source_name = character_names.get(source.id, f"ID:{source.id}")
    target_name = character_names.get(target.id, f"ID:{target.id}")
    source_location = character_locations.get(source.id, "") or ""

    co_located_ids = [
        cid for cid, loc in character_locations.items()
        if (loc or "") == source_location and cid != source.id
    ]
    co_present = target.id in co_located_ids
    only_two_present = co_present and len(co_located_ids) == 1

    lines: list[str] = []
    summary: list[str] = []
    direct_interaction = False
    observed_target = False
    hearsay = False
    hearsay_source = None
    third_party_ids: set[int] = set()

    for snap in round_snapshots:
        role = (snap.get("role") or "").strip().lower()
        if role == "system":
            continue

        author_id = snap.get("character_id")
        if role == "user":
            author_id = player_id
        if author_id is None:
            continue

        content = snap.get("content") or ""
        targets = perception.parse_target_ids(snap.get("target_character_ids"))

        if author_id == source.id:
            # Source's own speech. Direct when explicitly addressed or when the
            # target is present (they are actually talking to/with them).
            # A name mention without co-presence is only reflection (weak).
            explicit_address = target.id in targets
            mentions_target = _text_mentions_name(content, target_name)
            if explicit_address or (co_present and mentions_target) or only_two_present:
                direct_interaction = True
            elif mentions_target:
                observed_target = True
            else:
                continue
        elif author_id == target.id:
            # Target's speech: direct when addressed to the source or said
            # face-to-face; otherwise observed through the source's perception.
            explicit_address = source.id in targets
            mentions_source = _text_mentions_name(content, source_name)
            presence = perception.can_character_perceive_event(
                viewer_character_id=source.id,
                viewer_location=source_location,
                event=snap,
                viewer_name=source_name,
                viewer_location_id=getattr(source, "location_id", None),
            )[0]
            if explicit_address or (co_present and mentions_source) or only_two_present:
                direct_interaction = True
            elif presence != "absent":
                observed_target = True
            else:
                continue
            # Target speaks about others -> those are third parties relevant to target
            for tid in targets:
                if tid != source.id and tid != target.id:
                    third_party_ids.add(tid)
            # Also check content for name mentions
            for cid, cname in character_names.items():
                if cid != source.id and cid != target.id and _text_mentions_name(content, cname):
                    third_party_ids.add(cid)
        else:
            # Third party speaks; relevant only if perceived and about the target
            presence = perception.can_character_perceive_event(
                viewer_character_id=source.id,
                viewer_location=source_location,
                event=snap,
                viewer_name=source_name,
                viewer_location_id=getattr(source, "location_id", None),
            )[0]
            if presence == "absent":
                continue
            involves_target = (target.id in targets) or _text_mentions_name(content, target_name)
            if not involves_target:
                continue
            # Hearsay (§12): the author X directly addresses the source and
            # talks about the target — a second-hand report, not a direct
            # observation of the target's behavior.
            if source.id in targets and _text_mentions_name(content, target_name):
                hearsay = True
                hearsay_source = author_id
            else:
                observed_target = True
            # Third party is relevant to target (they're talking about target)
            third_party_ids.add(author_id)
            # Also check if they mention other characters
            for tid in targets:
                if tid != source.id and tid != target.id:
                    third_party_ids.add(tid)
            for cid, cname in character_names.items():
                if cid != source.id and cid != target.id and cid != author_id and _text_mentions_name(content, cname):
                    third_party_ids.add(cid)

        speaker = character_names.get(author_id, f"ID:{author_id}")
        addressee_names = [character_names.get(t, f"ID:{t}") for t in targets]
        addressee_text = ", ".join(addressee_names) if addressee_names else "(всем)"
        if hearsay:
            lines.append(
                f"[слух от {speaker}] {speaker} (id={author_id}) -> "
                f"{addressee_text}: {content}"
            )
        else:
            lines.append(f"{speaker} (id={author_id}) -> {addressee_text}: {content}")
        summary.append(f"{speaker} -> {addressee_text}")

    excerpt = "\n".join(lines[-max_lines:])
    return {
        "excerpt": excerpt,
        "interaction_summary": "\n".join(summary[:max_lines]) or "взаимодействия не было",
        "direct_interaction": direct_interaction,
        "observed_target": observed_target,
        "hearsay": hearsay,
        "hearsay_source": hearsay_source,
        "any_evidence": bool(excerpt.strip()),
        "third_party_ids": list(third_party_ids),
    }


def _evidence_mode(pair_ctx: dict) -> str:
    """Deterministic evidence mode for a pair: direct | observed | hearsay | none.

    Precedence: direct > observed > hearsay > none. ``hearsay`` means the
    source only heard a second-hand report about the target (§12). ``none``
    means the source had no perceivable evidence about the target this round;
    every LLM-proposed delta for such a pair is rejected (§8.3).
    """
    if pair_ctx.get("direct_interaction"):
        return "direct"
    if pair_ctx.get("observed_target"):
        return "observed"
    if pair_ctx.get("hearsay"):
        return "hearsay"
    return "none"


def evidence_mode_from_perception(result) -> str:
    """Perception adapter (WPE.md §4, Golden #14): `PerceptionResult` → evidence.

    Identity rule: the mode derived from the two-channel perception result is
    the same evidence gate the relationship layer applies via ``_evidence_mode``
    on pair context. ``perceive()`` and the pair layer must never disagree about
    admissibility (direct ≥ observed ≥ hearsay ≥ none).

    - visual full + audio full (одна локация / общая сцена) → "direct";
    - addressed или remote delivered → "direct" (явная адресация = прямой контакт);
    - visual full (стекло: действия видны, текст не слышен) → "observed";
    - audio full/muffled (соседство, шум) → "hearsay";
    - none/none (И11) → "none".
    """
    visual = getattr(result, "visual_level", "none") or "none"
    audio = getattr(result, "audio_level", "none") or "none"
    addressed = bool(getattr(result, "addressed", False))
    remote = str(getattr(result, "remote_status", "none")) == "delivered"
    if visual == "full" and audio == "full":
        return "direct"
    if addressed or remote:
        return "direct"
    if visual == "full":
        return "observed"
    if audio in ("full", "muffled"):
        return "hearsay"
    return "none"


def _build_batch_scene_summary(
    round_snapshots: list[dict],
    character_names: dict[int, str],
    character_locations: dict[int, str],
    player_id: int | None = None,
    max_lines: int = 30,
) -> str:
    """Compressed social scene for the batch prompt (docs/relations.md §8.2.2).

    Who is where, then who said what to whom (global view), capped to
    ``max_lines`` lines.
    """
    lines: list[str] = []
    for cid, loc in character_locations.items():
        lines.append(f"{character_names.get(cid, f'ID:{cid}')}: {loc or '?'}")
    for snap in round_snapshots:
        role = (snap.get("role") or "").strip().lower()
        if role == "system":
            continue
        author_id = snap.get("character_id")
        if role == "user":
            author_id = player_id
        if author_id is None:
            continue
        speaker = character_names.get(author_id, f"ID:{author_id}")
        targets = perception.parse_target_ids(snap.get("target_character_ids"))
        addressee_names = [character_names.get(t, f"ID:{t}") for t in targets]
        addressee_text = ", ".join(addressee_names) if addressee_names else "(всем)"
        lines.append(f"{speaker} -> {addressee_text}: {snap.get('content') or ''}")
    return "\n".join(lines[-max_lines:])


def _constrain_pair_delta(
    delta: schemas.RelationshipDelta,
    rel: models.CharacterRelationship,
    pair_ctx: dict,
) -> schemas.RelationshipDelta | None:
    """Deterministic evidence gating + caps (docs/relations.md §8.3, §9, §27).

    - mode ``none``: REJECT (returns ``None``) — no evidence means no right to
      change anything; the LLM never decides admissibility.
    - mode ``direct``: deltas are already clamped to ±MAX_DELTA by the schema;
      the type may change (the transition graph is validated in apply_delta).
    - mode ``observed``: deltas capped to relationship_reflection_delta_cap and
      the relationship type is frozen (unless configured otherwise).
    - mode ``hearsay``: deltas capped to the deterministic effective hearsay
      cap (§12), always weaker than direct/observed, and the type is frozen.

    Anti-inflation (§27): the per-mode cap is further narrowed to
    ``min(cap, cap_by_importance[importance])`` and, for positive deltas, the
    saturation guard dampens repeated growth when the metric already gained a
    lot over the recent trajectory window. Deterministic in all modes.
    """
    mode = _evidence_mode(pair_ctx)
    if mode == "none":
        logger.warning(
            "Evidence gating: rejecting delta for %d->%d (mode=none)",
            delta.source_character_id, delta.target_character_id,
        )
        return None
    if mode == "direct":
        # Direct evidence: the schema already clamps to ±MAX_DELTA and the
        # belief multiplier (§10) does NOT apply — only observed/hearsay caps
        # are dampened by beliefs.
        cap = settings.relationship_max_delta
    elif mode == "hearsay":
        cap = pair_ctx.get("hearsay_effective_cap")
        if cap is None:
            cap = settings.relationship_hearsay_cap
        cap = max(1, int(cap))
        # Reciprocity pipeline (Sprint 7, §10): a strong belief of the source
        # about the target dampens the delta cap (deterministic multiplier by
        # confidence). Computed per-pair and stashed on pair_ctx.
        cap = max(1, int(cap * _belief_multiplier(pair_ctx)))
    else:
        cap = settings.relationship_reflection_delta_cap
        cap = max(1, int(cap * _belief_multiplier(pair_ctx)))
    # Anti-inflation (§27.2): cap by the delta's importance in every mode.
    importance_cap = settings.relationship_cap_by_importance.get(
        delta.importance, settings.relationship_max_delta,
    )
    cap = max(1, int(min(cap, importance_cap)))
    # Anti-inflation (§27.3): saturation guard for positive deltas — uses
    # snapshot-based recent gains stashed on pair_ctx by the caller.
    recent_gains: dict = pair_ctx.get("recent_gains") or {}
    updates: dict = {}
    for metric in ("affection", "trust", "attraction", "resentment", "jealousy"):
        raw_value = getattr(delta, f"delta_{metric}")
        value = raw_value
        if value > 0 and metric in recent_gains:
            value = relationship_service.apply_saturation_guard(
                value,
                recent_gains[metric],
                settings.relationship_saturation_threshold,
                factor=settings.relationship_saturation_factor,
            )
        updates[f"delta_{metric}"] = max(-cap, min(cap, value))
    if (
        mode != "direct"
        and settings.relationship_type_change_requires_interaction
        and delta.relationship_type != rel.relationship_type
    ):
        updates["relationship_type"] = rel.relationship_type
    return delta.model_copy(update=updates)


def _belief_multiplier(pair_ctx: dict) -> float:
    """Belief-driven cap multiplier for observed/hearsay caps (§10)."""
    return float(pair_ctx.get("reciprocity_belief_multiplier") or 1.0)


# ---------------------------------------------------------------------------
# Hearsay reliability (docs/relations.md §12, Sprint 2 item 12)
# ---------------------------------------------------------------------------
def _hearsay_effective_cap(
    *,
    trust: int | None,
    hostility_high: bool,
    base_cap: int,
) -> int:
    """Deterministic hearsay cap: LLM cannot grade a rumor's reliability.

    ``trust`` is the source's trust in the teller (``None`` when no edge
    exists → treat as neutral). A hostile teller->target valence makes the
    report a gossip and lowers the cap further. Floor is 1 so the delta stays
    non-zero (weak but allowed).
    """
    cap = max(1, int(base_cap))
    if trust is not None and trust < TRUST_LOW:
        cap = max(1, int(cap / 2))
    if hostility_high:
        cap = max(1, int(cap * 0.7))
    return cap


async def _compute_hearsay_effective_cap(
    db: AsyncSession,
    source_id: int,
    teller_id: int,
    target_id: int,
) -> int:
    """Resolve the deterministic hearsay cap for pair source -> target (§12).

    - ``trust(source -> teller)``: the main reliability factor; low trust
      halves the cap.
    - ``valence(teller -> target)``: hostile (resentment/jealousy-derived)
      halves it further via the 0.7 gossip multiplier.
    Missing edges are treated as neutral (no penalty).
    """
    trust = None
    rel_teller = await relationship_service.get_relationship(db, source_id, teller_id)
    if rel_teller is not None:
        trust = int(getattr(rel_teller, "trust", 50) or 50)

    hostility_high = False
    rel_valence = await relationship_service.get_relationship(db, teller_id, target_id)
    if rel_valence is not None:
        hostility_high = _interpret_rel(rel_valence).hostility == "high"

    return _hearsay_effective_cap(
        trust=trust,
        hostility_high=hostility_high,
        base_cap=settings.relationship_hearsay_cap,
    )
