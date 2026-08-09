"""Форматирование отношений для промпта (Milestone 6B, decomposition.md §4.4).

Вынесено из ``app/relationship_service.py`` без изменения поведения (тела
функций перенесены 1:1). Сборка блоков ``<relationships>``,
``<behavior_drivers>`` и ``<epistemic_mask>``; чтение событий/убеждений —
через ``crud`` (app-уровень) и sibling ``crud.py``/``issues.py``.
"""

import logging
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import crud
from ..config import settings
from ..models import CharacterRelationship, RelationshipEvent
from ..prompt_builder import (
    build_behavior_drivers_block as _wrap_drivers_block,
    build_epistemic_mask_block as _wrap_epistemic_block,
)
from ..relationship_interpreter import (
    format_interpretation,
    format_interpretation_from_other,
    interpret,
    weighted_behavior_drivers,
)
from .crud import list_received_relationships, list_relationships_for_character
from .issues import list_open_issues

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Formatting for prompt
# ---------------------------------------------------------------------------
async def get_recent_events(
    db: AsyncSession,
    rel: CharacterRelationship,
    limit: int = 5,
) -> list[RelationshipEvent]:
    stmt = (
        select(RelationshipEvent)
        .where(RelationshipEvent.relationship_id == rel.id)
        .order_by(RelationshipEvent.timestamp.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


def format_relationship_for_prompt(
    rel: CharacterRelationship,
    target_name: str,
    events: list[RelationshipEvent],
    open_issues: Iterable[Any] = (),
) -> str:
    """Format one relationship for the generation prompt.

    Uses the deterministic interpreter instead of raw metrics: the character
    model gets semantic labels, never numbers (docs/relations.md §4-§5).
    Open issues (Sprint 1 п.5) bias the interpretation toward an unresolved
    hook without leaking raw issue text into this block (that is the separate
    ``<open_issue data>`` block, §14).
    """
    interp = interpret(rel, open_issues=open_issues)
    lines = [f"{target_name}: {rel.relationship_type}"]
    text = format_interpretation(interp, target_name)
    if text:
        lines.append(f"  {text}")
    if rel.description:
        lines.append(f"  описание: {rel.description}")
    if events:
        for ev in reversed(events):
            if ev.description:
                lines.append(f"  - {ev.description}")
    return "\n".join(lines)


async def build_relationships_block(
    db: AsyncSession,
    chat_id: int,
    character_id: int,
    character_name: str,
    all_characters: dict[int, str],
    max_events: int = 5,
) -> str:
    rels = await list_relationships_for_character(db, character_id, chat_id=chat_id)
    if not rels:
        return ""

    # Sprint 7 (§7/§13): anchor activation — топ-K эмоциональных якорей каждого
    # отношения (importance × recency, дедуп по event_id) рендерятся в блок
    # отношений при `anchors_enabled`. Блок ≤ RELATIONSHIP_ANCHOR_MAX.
    anchors_by_rel: dict[int, list] = {}
    if settings.anchors_enabled:
        try:
            anchors_by_rel = await crud.get_anchors_for_relationships(
                db, [r.id for r in rels], limit=None,
            )
        except Exception as exc:  # noqa: BLE001 — блок не роняет генерацию
            logger.warning(
                "[chat_id=%d] Failed to load anchors for character %d: %s",
                chat_id, character_id, exc,
            )
            anchors_by_rel = {}

    blocks: list[str] = [f"Отношения {character_name} к другим персонажам:"]
    for rel in rels:
        target_name = all_characters.get(rel.target_character_id, f"ID:{rel.target_character_id}")
        events = await get_recent_events(db, rel, limit=max_events)
        open_issues = await list_open_issues(db, rel)
        block = format_relationship_for_prompt(rel, target_name, events, open_issues=open_issues)
        if settings.anchors_enabled:
            anchors = anchors_by_rel.get(rel.id, [])
            if anchors:
                try:
                    top_anchors = crud.select_top_anchors(
                        anchors, settings.relationship_anchor_max,
                    )
                except Exception:
                    top_anchors = anchors[: settings.relationship_anchor_max]
                for anchor in top_anchors:
                    emotion = (anchor.emotion or "").strip() or "нейтрально"
                    block += f"\n  якорь: {emotion} (важность {anchor.importance:.1f})"
        blocks.append(block)
    return "\n".join(blocks)


async def build_behavior_drivers_block(
    db: AsyncSession,
    chat_id: int,
    character_id: int,
    character_name: str,
    all_characters: dict[int, str],
    max_drivers: int | None = None,
) -> str:
    """Build the top-K behavior drivers block for one character (Sprint 1 п.3-4).

    Aggregates deterministic tendency drivers across all outgoing relationships
    of the character, keeps the most significant ``relationship_drivers_max``,
    and wraps them in ``<behavior_drivers>…</behavior_drivers>``.

    Args:
        db: database session.
        chat_id: chat scope.
        character_id: source character id.
        character_name: source character name (kept for signature symmetry
            with :func:`build_relationships_block`).
        all_characters: {character_id: name} for name resolution.
        max_drivers: cap on returned tendencies; defaults to
            ``settings.relationship_drivers_max``.
    """
    if max_drivers is None:
        max_drivers = settings.relationship_drivers_max
    rels = await list_relationships_for_character(db, character_id, chat_id=chat_id)
    if not rels:
        return ""

    # Sprint 7 (§10): behavior drivers учитывают beliefs — убеждение персонажа
    # о другом (subject = имя цели) добавляет тенденцию с весом по confidence.
    beliefs_by_subject: dict[str, dict] = {}
    if settings.beliefs_enabled:
        beliefs_by_subject = await _beliefs_by_subject(db, character_id)

    candidates: list[tuple[int, str]] = []
    for rel in rels:
        target_name = all_characters.get(rel.target_character_id, f"ID:{rel.target_character_id}")
        open_issues = await list_open_issues(db, rel)
        interp = interpret(rel, open_issues=open_issues)
        candidates.extend(weighted_behavior_drivers(interp, target_name))

        belief = beliefs_by_subject.get((target_name or "").strip().lower())
        if belief:
            marker = "Ты знаешь" if belief.get("type") == "fact" else (
                "Ты подозреваешь" if belief.get("type") == "suspicion" else "Ты полагаешь"
            )
            snippet = f"{belief.get('predicate', '')} {belief.get('object', '')}".strip()
            if snippet:
                confidence = float(belief.get("confidence", 0.5) or 0.5)
                weight = max(1, round(4 * confidence))
                candidates.append(
                    (weight, f"{marker}, что {target_name} {snippet} (уверенность {confidence:.2f})")
                )

    candidates.sort(key=lambda item: (-item[0], item[1]))
    top = [text for _, text in candidates[:max(0, int(max_drivers))]]
    return _wrap_drivers_block(top)


async def _beliefs_by_subject(
    db: AsyncSession, character_id: int
) -> dict[str, dict]:
    """Beliefs персонажа, ключ — subject (lowercased). Sprint 5, §9."""
    try:
        beliefs = await crud.get_beliefs_for_character(
            db,
            character_id,
            top_k=settings.beliefs_top_k,
            min_confidence=settings.beliefs_render_confidence,
        )
        out: dict[str, dict] = {}
        for b in beliefs:
            subject = (b.subject or "").strip().lower()
            if subject:
                out[subject] = {
                    "predicate": b.predicate or "",
                    "object": b.object or "",
                    "type": b.type or "belief",
                    "confidence": b.confidence or 0.5,
                }
        return out
    except Exception as exc:  # noqa: BLE001 — маска не роняет генерацию
        logger.warning(
            "Failed to load beliefs for epistemic mask (character=%s): %s",
            character_id, exc,
        )
        return {}


async def compute_reciprocity_belief_multiplier(
    db: AsyncSession,
    source_character_id: int,
    target_name: str,
) -> float:
    """Belief-driven cap multiplier for a directed pair (Sprint 7, §10).

    Reciprocity pipeline: if the *source* has a belief about the target
    (``subject`` matches the target's name), a strong confidence dampens the
    delta cap — the character acts on what it believes, not on the raw event.

    multiplier = clamp(1 - dampening * max_confidence, min, 1.0)

    Returns 1.0 when ``reciprocity_enabled``/``beliefs_enabled`` is off, no
    matching belief exists, or confidence is too low.
    """
    if not settings.reciprocity_enabled or not settings.beliefs_enabled:
        return 1.0
    try:
        beliefs = await crud.get_beliefs_for_character(db, source_character_id)
    except Exception as exc:  # noqa: BLE001 — никогда не роняет раунд
        logger.warning(
            "Failed to load beliefs for reciprocity multiplier (character=%s): %s",
            source_character_id, exc,
        )
        return 1.0

    target_key = (target_name or "").strip().lower()
    if not target_key:
        return 1.0
    max_confidence = 0.0
    for belief in beliefs:
        subject = (belief.subject or "").strip().lower()
        if subject and subject == target_key:
            max_confidence = max(
                max_confidence, float(getattr(belief, "confidence", 0.0) or 0.0)
            )
    if max_confidence <= 0.0:
        return 1.0
    multiplier = 1.0 - settings.reciprocity_belief_dampening * max_confidence
    return max(settings.reciprocity_belief_multiplier_min, min(1.0, multiplier))


def _epistemic_belief_line(source_name: str, belief: dict) -> str:
    """Рендер убеждения вместо «неизвестно» в mask (Sprint 5, §9).

    Не раскрывает числа/метрики отношения — только уверенность убеждения.
    """
    predicate = belief.get("predicate") or ""
    obj = belief.get("object") or ""
    conf = belief.get("confidence", 0.5)
    marker = "Ты знаешь" if belief.get("type") == "fact" else (
        "Ты подозреваешь" if belief.get("type") == "suspicion" else "Ты полагаешь"
    )
    snippet = f"{predicate} {obj}".strip() or "что-то о нём"
    return (
        f"{marker} об отношении {source_name} к тебе: {snippet} "
        f"(уверенность {conf:.2f})"
    )


async def build_epistemic_mask_block(
    db: AsyncSession,
    chat_id: int,
    character_id: int,
    character_name: str,
    all_characters: dict[int, str],
    evidenced_target_ids: Iterable[int] = (),
    max_edges: int | None = None,
) -> str:
    """Build the ``<epistemic_mask>`` block for one character (Sprint 2 item 10).

    A character only *knows* how another treats them when it had direct or
    observed evidence of that other's behavior this round (docs/relations.md
    §10). Incoming edges (source -> this character) with evidence are shown as
    an interpretation WITHOUT any numbers; edges without evidence are explicitly
    marked unknown. Foreign internal metrics are never leaked into the prompt.

    Args:
        db: database session.
        chat_id: chat scope.
        character_id: the viewing character (target of the incoming edges).
        character_name: the viewing character's name.
        all_characters: {character_id: name} for name resolution.
        evidenced_target_ids: ids of characters whose behavior this character
            perceived this round (mode direct/observed, computed in chat_engine).
        max_edges: cap on returned lines; defaults to
            ``settings.relationship_epistemic_max``.
    """
    if not settings.relationship_epistemic_mask_enabled:
        return ""
    if max_edges is None:
        max_edges = settings.relationship_epistemic_max
    evidenced = set(int(i) for i in evidenced_target_ids)

    # Sprint 5 (§9): при beliefs_enabled маска читает убеждения персонажа
    # вместо «неизвестно». Блок WHAT YOU KNOW рендерится отдельно
    # (context_builder / chat_engine); здесь — только подстановка source.
    beliefs_by_subject: dict[str, dict] = {}
    if settings.beliefs_enabled:
        beliefs_by_subject = await _beliefs_by_subject(db, character_id)

    received = await list_received_relationships(db, character_id)
    if not received:
        return ""

    known_lines: list[str] = []
    unknown_lines: list[str] = []
    for rel in received:
        source_name = all_characters.get(
            rel.source_character_id, f"ID:{rel.source_character_id}"
        )
        if source_name == character_name:
            continue
        if rel.source_character_id in evidenced:
            interp = interpret(rel)
            text = format_interpretation_from_other(interp, source_name)
            known_lines.append(f"Известное тебе отношение {source_name} к тебе: {text}")
        else:
            belief = beliefs_by_subject.get((source_name or "").strip().lower())
            if belief is not None:
                unknown_lines.append(
                    _epistemic_belief_line(source_name, belief)
                )
            else:
                unknown_lines.append(f"Тебе неизвестно, как {source_name} относится к тебе.")

    lines = known_lines + unknown_lines
    if max_edges and len(lines) > max_edges:
        lines = lines[: max(0, int(max_edges))]
    return _wrap_epistemic_block(lines)
