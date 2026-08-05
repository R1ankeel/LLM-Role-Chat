"""Post-round pipeline orchestrator (Plans/update20.md §15, Sprint 1).

Выносит пост-раундную обработку из ``chat_engine`` в оркестратор изолированных
стадий. Каждая стадия — отдельная функция, обёрнутая в try/except: падение
одной НЕ ломает раунд (graceful degradation).

Стадии (порядок из §15, Sprint 3 добавил character_state после relationships):
1. presence round pass   — ``crud.compute_and_save_presence_for_round``;
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

from . import crud
from .config import settings

logger = logging.getLogger(__name__)


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
        await crud.compute_and_save_presence_for_round(
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
