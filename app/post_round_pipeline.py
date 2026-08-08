"""Post-round pipeline orchestrator (Plans/update20.md §15, Sprint 1).

Выносит пост-раундную обработку из ``chat_engine`` в оркестратор изолированных
стадий. Каждая стадия — отдельная функция, обёрнутая в try/except: падение
одной НЕ ломает раунд (graceful degradation).

Стадии (порядок из §15, Sprint 3 добавил character_state после relationships):
1. presence round pass   — ``compute_and_save_presence_for_round`` (locally);
2. event extraction      — ``event_service`` (LLM/Sensors) → ``crud.save_round_events``;
3. memory extraction     — ``memory_service.process_post_round`` (background);
4. relationships         — ``relationship_analyzer`` (background, если включён);
5. character state       — ``character_state.update_states_from_round`` (Sprint 3):
   детерминированные эмоции/стресс/mood из world_events раунда + relationship
   deltas (которые к этому моменту уже могут быть закоммичены фоновым анализатором);
6. beliefs               — ``belief_service.update_beliefs_from_round`` (Sprint 5):
   детерминированные beliefs из событий раунда, которые персонаж реально
   воспринял (presence+attention); только direct_observation путь;
7. story                 — Sprint 8: story_events (проекция world_events) +
   story_state (summary, активные story_threads, progress); только при
   story_enabled + chats.story_enabled.

Memory и relationship — внешние коллбеки (инъекция), чтобы избежать циклической
зависимости ``pipeline → chat_engine``; ``chat_engine`` передаёт свои функции.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from . import crud
from . import perception
from . import schemas
from . import witness_model
from .config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Presence/attention computation (Sprint 1, §7.1).
# Перенесено из ``crud.py``: присутствие/внимание пересчитываются сервисом
# поверх чистого ``crud`` (однонаправленно сервис → crud). crud больше не
# импортирует ``perception``/``witness_model``/``attention``/``sensors_service``.
# ---------------------------------------------------------------------------

def _attention_score_for(
    *,
    message,
    character_id: int,
    presence: str,
    character_names: dict[int, str],
    rel_targets: set[int],
    anchor_authors: set[int],
    sensors_significance: float | None = None,
) -> float:
    """Детерминированный attention score пары (персонаж, событие) (§11).

    Sensors ``significance`` (если передан) применяется как подсказка в рамках
    caps — Sensors не решает доступность информации (presence) и не принимает
    решение о внимании.
    """
    from . import attention

    author_id = getattr(message, "character_id", None)
    anchor_active = False
    if author_id is not None:
        try:
            anchor_active = int(author_id) in anchor_authors
        except (TypeError, ValueError):
            pass
    score = attention.compute_attention_score(
        presence=presence,
        event=perception.event_from_message(message),
        observer={
            "character_id": character_id,
            "name": character_names.get(character_id, ""),
        },
        character_names=character_names,
        relationship_target_ids=rel_targets,
        anchor_active=anchor_active,
    )
    if sensors_significance is not None:
        score = attention.apply_sensors_significance(score, sensors_significance)
    return score


def _round_text_snippet(round_messages: list, max_len: int = 1500) -> str:
    """Короткий текст раунда для sensor-задачи (минимальный контекст §5.1.7)."""
    parts: list[str] = []
    for message in round_messages:
        role = getattr(message, "role", None)
        content = str(getattr(message, "content", "") or "")
        if not content:
            continue
        if role == "user":
            parts.append(f"Игрок: {content}")
        elif role == "system":
            parts.append(f"Система: {content}")
        else:
            name = getattr(getattr(message, "character", None), "name", None) or ""
            parts.append(f"{name}: {content}")
    snippet = "\n".join(parts)
    return snippet[:max_len]


def _build_perception_world_state(
    locations: list,
    thread_deliveries: set[int] | frozenset[int] | None = None,
) -> perception.PerceptionWorldState | None:
    """Build the pure world snapshot for the two-channel cutover (Фаза 4).

    ``thread_deliveries`` (Фаза 6) — id персонажей, которым событие доставлено
    через тред/удалённый канал; источник ``remote_status=delivered`` (§4).
    """
    return perception.PerceptionWorldState(
        adjacency=perception.build_permeability_index(locations or []),
        thread_deliveries=frozenset(thread_deliveries or ()),
    )


async def _chat_world_state_for_characters(
    db: AsyncSession, characters: list
) -> perception.PerceptionWorldState | None:
    """World snapshot from the chat of ``characters`` (None if flag off / no chat)."""
    if not settings.world_engine_perception_enabled:
        return None
    chat_id = None
    for character in characters:
        chat_id = getattr(character, "chat_id", None)
        if chat_id is not None:
            break
    if chat_id is None:
        return None
    locations = await crud.get_chat_locations(db, chat_id)
    return _build_perception_world_state(locations)


async def _chat_world_state_for_message(
    db: AsyncSession, message, characters: list
) -> perception.PerceptionWorldState | None:
    """World snapshot scoped to one event (Фаза 6): включает доставки тредов.

    ``thread_deliveries`` события вычисляются из ``ThreadParticipantState``
    (см. ``thread_delivery_ids_for_message``), чтобы `perceive()` мог отдать
    ``remote_status=delivered`` адресату независимо от локации (Golden #6).
    """
    if not settings.world_engine_perception_enabled:
        return None
    chat_id = getattr(message, "chat_id", None)
    if chat_id is None:
        for character in characters:
            chat_id = getattr(character, "chat_id", None)
            if chat_id is not None:
                break
    if chat_id is None:
        return None
    locations = await crud.get_chat_locations(db, chat_id)
    deliveries = await crud.thread_delivery_ids_for_message(db, message)
    return _build_perception_world_state(locations, thread_deliveries=deliveries)


async def compute_and_save_presence_for_message(
    db: AsyncSession,
    message,
    characters: list,
    character_names: dict[int, str] | None = None,
) -> dict[int, str]:
    """Compute and persist presence for one event for all characters.

    Returns {character_id: presence} for the given message.

    Cutover (WPE 3.0 Фаза 4): при ``WORLD_ENGINE_PERCEPTION_ENABLED``
    presence пишется через двухканальный ``perceive()`` (Renderer
    ``witness_model.perceive_presence_for_character``), а не через legacy
    ``can_character_perceive_event``. Откат — выключить флаг.

    Фаза 6: world-state строится по событию (включая ``thread_deliveries``),
    а при ``WORLD_ENGINE_PARTIAL_PERCEPTION_ENABLED`` голосовая атрибуция
    ``voice_known`` берётся из отношений наблюдателя (WPE.md §4).
    """
    names = character_names or {c.id: c.name for c in characters}
    locations = {c.id: getattr(c, "location", "") or "" for c in characters}
    message_id = getattr(message, "id", None)
    if message_id is None:
        return {}

    world_state = await _chat_world_state_for_message(db, message, characters)
    known_voices = None
    if settings.world_engine_partial_perception_enabled:
        chat_id = getattr(message, "chat_id", None)
        if chat_id is not None:
            known_voices = await crud._known_voices_for_chat(db, chat_id)

    # Sprint 4 (§11): attention score считается детерминированно вместе с
    # presence (только для включённого флага). Sensors perception-proposal не
    # вызывается на синхронном пути — только в пост-раунд presence pass.
    attention_ctx: dict[int, dict[str, set[int]]] = {}
    if settings.attention_enabled:
        chat_id = getattr(message, "chat_id", None)
        if chat_id is None:
            for character in characters:
                chat_id = getattr(character, "chat_id", None)
                if chat_id is not None:
                    break
        if chat_id is not None:
            attention_ctx = await crud._attention_context_for_chat(
                db, chat_id, [c.id for c in characters]
            )

    records: list[schemas.MessagePresenceCreate] = []
    result: dict[int, str] = {}
    for character in characters:
        if world_state is not None:
            presence = witness_model.perceive_presence_for_character(
                message,
                character,
                world_state,
                voice_known=witness_model.voice_familiarity(
                    character.id,
                    getattr(message, "character_id", None),
                    known_voices,
                ),
            )
        else:
            presence = witness_model.compute_mvp_presence(
                message,
                character.id,
                names,
                viewer_location=locations.get(character.id, ""),
                viewer_location_id=getattr(character, "location_id", None),
                character_locations=locations,
            )
        result[character.id] = presence
        attention = None
        if attention_ctx:
            ctx = attention_ctx.get(character.id, {})
            attention = _attention_score_for(
                message=message,
                character_id=character.id,
                presence=presence,
                character_names=names,
                rel_targets=ctx.get("rel_targets", set()),
                anchor_authors=ctx.get("anchor_authors", set()),
            )
        records.append(
            schemas.MessagePresenceCreate(
                message_id=message_id,
                character_id=character.id,
                presence=presence,
                attention=attention,
            )
        )
    await crud.upsert_message_presence_batch(db, records)
    return result


async def compute_and_save_presence_for_round(
    db: AsyncSession,
    round_messages: list,
    character_ids: list[int],
    character_names: dict[int, str],
    *,
    characters: list | None = None,
    character_locations: dict[int, str] | None = None,
    client: Any = None,
) -> None:
    """Persist perception-based presence for all messages in a completed round.

    Cutover (WPE 3.0 Фаза 4): при ``WORLD_ENGINE_PERCEPTION_ENABLED``
    presence пишется через ``perceive()`` (см. Фаза 4 / Golden #14).

    Фаза 6: для событий удалённых каналов доставки тредов подставляются
    по-событийно, voice familiarity — из отношений при включённом
    ``WORLD_ENGINE_PARTIAL_PERCEPTION_ENABLED``.

    Sprint 4 (§11): вместе с presence детерминированно пишется attention score.
    Sensors perception-proposal (§5.1.3) вызывается только здесь (пост-раунд,
    один вызов на раунд при ``sensors_perception_enabled``): предложенная
    ``significance`` применяется как подсказка к attention в рамках
    ``SENSORS_PERCEPTION_SIGNIFICANCE_CAP``; доступность информации (presence)
    Sensors не определяет. Недоступен Sensors → детерминированный путь.
    """
    if characters is None:
        characters = []
        for cid in character_ids:
            char = await crud.get_character(db, cid)
            if char is not None:
                characters.append(char)

    locations = character_locations or {
        c.id: getattr(c, "location", "") or "" for c in characters
    }
    if not locations and character_ids:
        locations = {cid: "" for cid in character_ids}

    world_state = await _chat_world_state_for_characters(db, characters)
    known_voices = None
    chat_id = None
    if settings.world_engine_partial_perception_enabled and characters:
        chat_id = getattr(characters[0], "chat_id", None)
        if chat_id is not None:
            known_voices = await crud._known_voices_for_chat(db, chat_id)
    if chat_id is None and characters:
        chat_id = getattr(characters[0], "chat_id", None)

    # Sprint 4 (§11): attention-контекст персонажей + Sensors perception-подсказка
    # (significance раунда, один вызов, только пост-раунд).
    attention_ctx: dict[int, dict[str, set[int]]] = {}
    sensors_significance: float | None = None
    if settings.attention_enabled:
        attention_ctx = await crud._attention_context_for_chat(db, chat_id, character_ids)
        if chat_id is not None and client is not None:
            try:
                from .sensors_service import sensors_service

                if sensors_service.is_enabled("perception"):
                    minimal_context = _round_text_snippet(round_messages)
                    if minimal_context:
                        sensors_result = await sensors_service.run(
                            client, task="perception", minimal_context=minimal_context
                        )
                        if sensors_result is not None:
                            sensors_significance = sensors_result.get("significance")
            except Exception:  # noqa: BLE001 — Sensors не должен ронять раунд
                logger.warning(
                    "[chat_id=%s] Sensors perception proposal failed; "
                    "deterministic attention only",
                    chat_id,
                )

    records: list[schemas.MessagePresenceCreate] = []
    for message in round_messages:
        message_id = getattr(message, "id", None)
        if message_id is None:
            continue
        deliveries = frozenset()
        if world_state is not None and settings.world_engine_threads_enabled:
            deliveries = await crud.thread_delivery_ids_for_message(db, message)
        for character_id in character_ids:
            if world_state is not None:
                character = next(
                    (c for c in characters if c.id == character_id), None
                )
                if character is None:
                    continue
                message_world_state = (
                    perception.PerceptionWorldState(
                        adjacency=world_state.adjacency,
                        thread_deliveries=deliveries,
                    )
                    if deliveries
                    else world_state
                )
                presence = witness_model.perceive_presence_for_character(
                    message,
                    character,
                    message_world_state,
                    voice_known=witness_model.voice_familiarity(
                        character.id,
                        getattr(message, "character_id", None),
                        known_voices,
                    ),
                )
            else:
                character = next(
                    (c for c in characters if c.id == character_id), None
                )
                presence = witness_model.compute_mvp_presence(
                    message,
                    character_id,
                    character_names,
                    viewer_location=locations.get(character_id, ""),
                    viewer_location_id=(
                        getattr(character, "location_id", None)
                        if character is not None
                        else None
                    ),
                    character_locations=locations,
                )
            attention = None
            if attention_ctx:
                ctx = attention_ctx.get(character_id, {})
                attention = _attention_score_for(
                    message=message,
                    character_id=character_id,
                    presence=presence,
                    character_names=character_names,
                    rel_targets=ctx.get("rel_targets", set()),
                    anchor_authors=ctx.get("anchor_authors", set()),
                    sensors_significance=sensors_significance,
                )
            records.append(
                schemas.MessagePresenceCreate(
                    message_id=message_id,
                    character_id=character_id,
                    presence=presence,
                    attention=attention,
                )
            )
    await crud.upsert_message_presence_batch(db, records)


async def _stage_presence(
    client: Any,
    db,
    *,
    round_messages: list[Any],
    character_ids: list[int],
    character_names: dict[int, str],
    characters: list[Any],
    character_locations: dict[int, str],
) -> dict:
    """Stage 1: presence round pass (perception witness rows for the round).

    Sprint 4 (§11): с presence детерминированно пишется attention; Sensors
    perception-proposal (§5.1.3) вызывается здесь (пост-раунд, один вызов на
    раунд) только при ``attention_enabled`` — движок сам решает доступность.
    """
    try:
        await compute_and_save_presence_for_round(
            db,
            round_messages,
            character_ids,
            character_names,
            characters=characters,
            character_locations=character_locations,
            client=client,
        )
        return {"ok": True, "stage": "presence"}
    except Exception as exc:  # noqa: BLE001 — стадия не должна ронять раунд
        logger.warning("Post-round pipeline: presence stage failed: %s", exc)
        return {"ok": False, "stage": "presence", "error": str(exc)}


async def _stage_event_extraction(
    client: Any,
    db,
    *,
    chat_id: int,
    model_name: str,
    round_messages: list[Any],
    character_names: dict[int, str],
    round_id: str | None,
) -> dict:
    """Stage 2: round event extraction (§15). No-op при отключённом флаге."""
    if not settings.event_extraction_enabled:
        return {"ok": True, "stage": "event_extraction", "skipped": "flag off"}
    try:
        from . import event_service

        extracted = await event_service.extract_round_events(
            client,
            db,
            chat_id,
            round_messages,
            round_id=round_id,
            character_names=character_names,
            model_name=model_name,
        )
        if not extracted.events:
            return {
                "ok": True,
                "stage": "event_extraction",
                "written": 0,
                "sensors_used": extracted.sensors_used,
            }
        report = await crud.save_round_events(
            db, chat_id, extracted.events, round_id=round_id
        )
        return {
            "ok": True,
            "stage": "event_extraction",
            "written": report.written_events,
            "links": report.written_links,
            "skipped": report.skipped_below_importance,
            "sensors_used": extracted.sensors_used,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Post-round pipeline: event extraction stage failed: %s", exc)
        return {"ok": False, "stage": "event_extraction", "error": str(exc)}


async def _stage_memory(
    memory_processor: Callable[..., Awaitable[Any]] | None,
    *,
    client: Any,
    chat_id: int,
    model_name: str,
    round_snapshots: list[dict],
    character_snapshots: list[dict],
) -> dict:
    """Stage 3: post-round memory extraction (background task, non-blocking)."""
    if memory_processor is None:
        return {"ok": True, "stage": "memory", "skipped": "no processor"}
    try:
        asyncio.create_task(
            memory_processor(
                client, chat_id, round_snapshots, character_snapshots, model_name
            )
        )
        return {"ok": True, "stage": "memory", "scheduled": True}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Post-round pipeline: memory stage failed: %s", exc)
        return {"ok": False, "stage": "memory", "error": str(exc)}


async def _stage_relationships(
    relationship_analyzer: Callable[..., Awaitable[Any]] | None,
    *,
    client: Any,
    chat_id: int,
    model_name: str,
    round_snapshots: list[dict],
    character_snapshots: list[dict],
    round_id: str | None,
) -> dict:
    """Stage 4: relationship analysis (background, только если движок включён)."""
    if relationship_analyzer is None or not settings.relationship_analyzer_enabled:
        return {
            "ok": True,
            "stage": "relationships",
            "skipped": "analyzer off",
        }
    try:
        asyncio.create_task(
            relationship_analyzer(
                client,
                chat_id,
                model_name,
                round_snapshots,
                character_snapshots,
                round_id=round_id,
            )
        )
        return {"ok": True, "stage": "relationships", "scheduled": True}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Post-round pipeline: relationships stage failed: %s", exc)
        return {"ok": False, "stage": "relationships", "error": str(exc)}


async def _stage_beliefs(
    client: Any,
    db,
    *,
    chat_id: int,
    round_id: str | None,
    characters: list[Any],
) -> dict:
    """Stage 6: belief update (Sprint 5, Plans/update20.md §9).

    Детерминированные beliefs из world events раунда (stage 2), которые персонаж
    реально воспринял (presence stage 1 + attention). Только direct_observation
    путь: LLM-suggestion beliefs — под benchmark gate (§27), не здесь. No-op при
    отключённом флаге ``beliefs_enabled``; падение стадии не роняет раунд.
    """
    if not settings.beliefs_enabled:
        return {
            "ok": True,
            "stage": "beliefs",
            "skipped": "flag off",
        }
    if not characters or not round_id:
        return {
            "ok": True,
            "stage": "beliefs",
            "skipped": "no characters/round",
        }
    try:
        from . import belief_service

        report = await belief_service.update_beliefs_from_round(
            db,
            chat_id,
            round_id,
            characters,
            client=client,
        )
        return {
            "ok": True,
            "stage": "beliefs",
            "characters": report["characters"],
            "written": report["written"],
            "updated": report["updated"],
            "skipped": report["skipped"],
        }
    except Exception as exc:  # noqa: BLE001 — стадия не должна ронять раунд
        logger.warning("Post-round pipeline: beliefs stage failed: %s", exc)
        return {"ok": False, "stage": "beliefs", "error": str(exc)}


async def _stage_story(
    client: Any,
    db,
    *,
    chat_id: int,
    round_id: str | None,
    character_names: dict[int, str],
) -> dict:
    """Stage 7: story capture (Sprint 8, Plans/update20.md §16).

    Детерминированный write-path: ``story_events`` (проекция extraction
    world_events раунда) → ``story_state`` (summary, активные story_threads,
    progress). Затем Sprint 9: LLM-консолидация (``story_consolidation``)
    при включённом флаге и срабатывании trigger. Только при включённых
    ``story_enabled`` (canary) И ``chats.story_enabled`` (перчатовый тумблер
    пользователя). Исходный ``general_prompt``/``original_plot`` НЕ меняются;
    падение любой части стадии не роняет раунд.
    """
    if not settings.story_enabled:
        return {
            "ok": True,
            "stage": "story",
            "skipped": "flag off",
        }
    chat = None
    try:
        chat = await crud.get_chat(db, chat_id)
        if chat is None or not getattr(chat, "story_enabled", False):
            return {
                "ok": True,
                "stage": "story",
                "skipped": "chat story disabled",
            }
    except Exception as exc:  # noqa: BLE001 — стадия не должна ронять раунд
        logger.warning("Post-round pipeline: story stage (chat check) failed: %s", exc)
        return {"ok": False, "stage": "story", "error": str(exc)}
    try:
        from .plot import story_events, story_state

        events_report = await story_events.write_story_events_from_round(
            db, chat_id, round_id, character_names
        )
        state_report = await story_state.update_story_state_from_round(
            db, chat_id, round_id
        )

        consolidation_report = {}
        if settings.story_consolidation_enabled:
            from .plot import story_consolidation

            consolidation_report = await story_consolidation.maybe_consolidate_story(
                db,
                client,
                chat_id=chat_id,
                round_id=round_id,
                model_name=getattr(chat, "model_name", None),
            )

        return {
            "ok": bool(events_report.get("ok") and state_report.get("ok")),
            "stage": "story",
            "events_written": events_report.get("written", 0),
            "threads_created": state_report.get("threads_created", 0),
            "threads_updated": state_report.get("threads_updated", 0),
            "skipped": events_report.get("skipped")
            or state_report.get("skipped"),
            "consolidation": consolidation_report,
        }
    except Exception as exc:  # noqa: BLE001 — стадия не должна ронять раунд
        logger.warning("Post-round pipeline: story stage failed: %s", exc)
        return {"ok": False, "stage": "story", "error": str(exc)}


async def _stage_story_threads(
    db,
    *,
    chat_id: int,
) -> dict:
    """Stage 8: story thread archiving (Sprint 10, Plans/update20.md §19/§21).

    Детерминированно архивирует завершённые активные story_threads: линия
    считается завершённой, если её имя пересекается с ``completed_goals`` из
    ``story_state.current_story`` (token overlap ≥ ``story_thread_archive_overlap``).
    No-op при выключенном ``story_enabled``; падение стадии не роняет раунд.
    """
    if not settings.story_enabled:
        return {
            "ok": True,
            "stage": "story_threads",
            "skipped": "flag off",
        }
    try:
        from .plot import story_threads

        archived = await story_threads.archive_completed_threads(db, chat_id)
        return {
            "ok": True,
            "stage": "story_threads",
            "archived": archived,
        }
    except Exception as exc:  # noqa: BLE001 — стадия не должна ронять раунд
        logger.warning(
            "Post-round pipeline: story_threads stage failed: %s", exc
        )
        return {"ok": False, "stage": "story_threads", "error": str(exc)}


async def _stage_plans(
    db,
    *,
    chat_id: int,
    round_id: str | None,
    characters: list[Any],
) -> dict:
    """Stage 9: NPC plans update (Sprint 10, Plans/update20.md §22).

    Детерминированный пост-раунд: для каждого персонажа с активным планом
    сопоставить события раунда с целью/блокировкой и продвинуть план
    (next_step / done / снятие блокировки). Только при ``npc_plans_enabled``.
    """
    if not settings.npc_plans_enabled:
        return {
            "ok": True,
            "stage": "plans",
            "skipped": "flag off",
        }
    if not round_id or not characters:
        return {
            "ok": True,
            "stage": "plans",
            "skipped": "no characters/round",
        }
    try:
        from . import npc_plans

        round_events = await crud.get_story_round_world_events(
            db, chat_id, round_id
        )
        updated = 0
        for character in characters:
            plan = await crud.get_active_npc_plan(db, chat_id, character.id)
            if plan is None:
                continue
            report = await npc_plans.update_plan_from_round(
                db, plan, round_events, round_id=round_id
            )
            if report.get("status") or report.get("next_step_changed") or report.get("unblocked"):
                updated += 1
        return {
            "ok": True,
            "stage": "plans",
            "updated": updated,
        }
    except Exception as exc:  # noqa: BLE001 — стадия не должна ронять раунд
        logger.warning("Post-round pipeline: plans stage failed: %s", exc)
        return {"ok": False, "stage": "plans", "error": str(exc)}


async def _stage_crisis(
    db,
    *,
    chat_id: int,
    round_id: str | None,
    characters: list[Any],
    character_names: dict | None = None,
) -> dict:
    """Stage 10: crisis engine (Sprint 11, Plans/update20.md §19).

    Мягкое обнаружение кризисов: детерминированный pressure → кандидат →
    resolution (story_event + story_thread «Кризис» + boost). No-op при
    выключенном ``crisis_engine_enabled``; падение стадии не роняет раунд.
    """
    if not settings.crisis_engine_enabled:
        return {
            "ok": True,
            "stage": "crisis",
            "skipped": "flag off",
        }
    if not round_id or not characters:
        return {
            "ok": True,
            "stage": "crisis",
            "skipped": "no characters/round",
        }
    try:
        from .plot import crisis_engine

        report = await crisis_engine.run_crisis_engine(
            db,
            chat_id=chat_id,
            round_id=round_id,
            characters=characters,
            character_names=character_names or {},
        )
        return {"ok": True, "stage": "crisis", "crisis": report}
    except Exception as exc:  # noqa: BLE001 — стадия не должна ронять раунд
        logger.warning("Post-round pipeline: crisis stage failed: %s", exc)
        return {"ok": False, "stage": "crisis", "error": str(exc)}


async def _stage_character_state(
    client: Any,
    db,
    *,
    chat_id: int,
    round_id: str | None,
    characters: list[Any],
) -> dict:
    """Stage 5: character state update (Sprint 3, Plans/update20.md §23).

    Детерминированное обновление ``character_states`` через ``emotion_engine``
    из relationship deltas раунда + world events (события идут из stage 2).
    Стадия только ПОСЛЕ relationships/story нет — перед story, чтобы события
    раунда (world_events, stage 2) уже были в БД. No-op при отключённом флаге
    ``character_state_enabled``; падение стадии не роняет раунд.
    """
    if not settings.character_state_enabled:
        return {
            "ok": True,
            "stage": "character_state",
            "skipped": "flag off",
        }
    if not characters or not round_id:
        return {
            "ok": True,
            "stage": "character_state",
            "skipped": "no characters/round",
        }
    try:
        from . import character_state

        report = await character_state.update_states_from_round(
            db,
            chat_id,
            round_id,
            characters,
            client=client,
        )
        return {
            "ok": True,
            "stage": "character_state",
            "states": report["states"],
            "updated": report["updated"],
            "sensors_used": report["sensors_used"],
        }
    except Exception as exc:  # noqa: BLE001 — стадия не должна ронять раунд
        logger.warning("Post-round pipeline: character_state stage failed: %s", exc)
        return {"ok": False, "stage": "character_state", "error": str(exc)}


async def _stage_adaptive_consolidation(
    db,
    *,
    chat_id: int,
    model_name: str,
    round_id: str | None,
) -> dict:
    """Stage 11: adaptive consolidation trigger (Sprint 12, Plans/update20.md §20).

    Score-based replacement of the 24h timer: evaluates soft/hard/critical for
    the chat since the last consolidation and, when triggered, marks the
    ``consolidation_state`` baseline and enqueues a background job. Critical
    events trigger an immediate hard consolidation. Idle chats are skipped
    (score≈0). No-op when ``adaptive_consolidation_enabled`` is off; the stage
    never blocks or fails the round.
    """
    if not settings.adaptive_consolidation_enabled:
        return {
            "ok": True,
            "stage": "adaptive_consolidation",
            "skipped": "flag off",
        }
    try:
        from . import memory_service
        from . import task_queue

        decision = await memory_service.schedule_adaptive_consolidation(
            db,
            chat_id=chat_id,
            model_name=model_name,
            round_id=round_id,
        )
        # Enqueued jobs are dispatched fire-and-forget so a critical event
        # triggers an immediate hard consolidation (never blocks the round).
        job = decision.get("job")
        if job is not None:
            asyncio.create_task(task_queue.memory_job_queue.run_job(job))
        return {
            "ok": True,
            "stage": "adaptive_consolidation",
            "decision": decision,
        }
    except Exception as exc:  # noqa: BLE001 — стадия не должна ронять раунд
        logger.warning(
            "Post-round pipeline: adaptive_consolidation stage failed: %s", exc
        )
        return {
            "ok": False,
            "stage": "adaptive_consolidation",
            "error": str(exc),
        }


async def run_post_round_pipeline(
    *,
    client: Any,
    db,
    chat_id: int,
    model_name: str,
    round_messages: list[Any],
    character_ids: list[int],
    character_names: dict[int, str],
    characters: list[Any],
    character_locations: dict[int, str],
    round_id: str | None,
    round_snapshots: list[dict],
    character_snapshots: list[dict],
    memory_processor: Callable[..., Awaitable[Any]] | None = None,
    relationship_analyzer: Callable[..., Awaitable[Any]] | None = None,
    stages: set[str] | None = None,
) -> dict:
    """Оркестратор пост-раундных стадий (§15, Sprint 1, +character_state Sprint 3,
    +beliefs Sprint 5, +story Sprint 8, +story_threads/plans Sprint 10,
    +crisis Sprint 11).

    Вызывается из ``chat_engine.process_user_message_streaming`` ПОСЛЕ генерации
    раунда и scene extraction. Каждая стадия изолирована: исключение одной не
    влияет на остальные и не роняет раунд. Возвращает отчёт по стадиям.
    """
    enabled = stages or {
        "presence",
        "event_extraction",
        "memory",
        "relationships",
        "character_state",
        "beliefs",
        "story",
        "story_threads",
        "plans",
        "crisis",
        "adaptive_consolidation",
    }
    report: dict[str, Any] = {}

    if "presence" in enabled:
        report["presence"] = await _stage_presence(
            client,
            db,
            round_messages=round_messages,
            character_ids=character_ids,
            character_names=character_names,
            characters=characters,
            character_locations=character_locations,
        )

    if "event_extraction" in enabled:
        report["event_extraction"] = await _stage_event_extraction(
            client,
            db,
            chat_id=chat_id,
            model_name=model_name,
            round_messages=round_messages,
            character_names=character_names,
            round_id=round_id,
        )

    if "memory" in enabled:
        report["memory"] = await _stage_memory(
            memory_processor,
            client=client,
            chat_id=chat_id,
            model_name=model_name,
            round_snapshots=round_snapshots,
            character_snapshots=character_snapshots,
        )

    if "relationships" in enabled:
        report["relationships"] = await _stage_relationships(
            relationship_analyzer,
            client=client,
            chat_id=chat_id,
            model_name=model_name,
            round_snapshots=round_snapshots,
            character_snapshots=character_snapshots,
            round_id=round_id,
        )

    if "character_state" in enabled:
        report["character_state"] = await _stage_character_state(
            client,
            db,
            chat_id=chat_id,
            round_id=round_id,
            characters=characters,
        )

    if "beliefs" in enabled:
        report["beliefs"] = await _stage_beliefs(
            client,
            db,
            chat_id=chat_id,
            round_id=round_id,
            characters=characters,
        )

    if "story" in enabled:
        report["story"] = await _stage_story(
            client,
            db,
            chat_id=chat_id,
            round_id=round_id,
            character_names=character_names,
        )

    if "story_threads" in enabled:
        report["story_threads"] = await _stage_story_threads(
            db,
            chat_id=chat_id,
        )

    if "plans" in enabled:
        report["plans"] = await _stage_plans(
            db,
            chat_id=chat_id,
            round_id=round_id,
            characters=characters,
        )

    if "crisis" in enabled:
        report["crisis"] = await _stage_crisis(
            db,
            chat_id=chat_id,
            round_id=round_id,
            characters=characters,
            character_names=character_names,
        )

    if "adaptive_consolidation" in enabled:
        report["adaptive_consolidation"] = await _stage_adaptive_consolidation(
            db,
            chat_id=chat_id,
            model_name=model_name,
            round_id=round_id,
        )

    failed = [k for k, v in report.items() if not v.get("ok")]
    if failed:
        logger.warning(
            "[chat_id=%d] Post-round pipeline completed with failed stages: %s",
            chat_id,
            failed,
        )
    return report
