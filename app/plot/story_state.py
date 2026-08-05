"""Current Story State write/read-path (Plans/update20.md §16.2/§16.4, Sprint 8).

Write-path (пост-раунд, детерминированный, без LLM): ``story_states``
обновляется из записанных ``story_events`` — summary (последние события),
активные ``story_threads`` (из важных событий, дедупликация по имени),
``progress``. ``story_phase`` движок НЕ меняет сам (задаётся пользователем
через API; предложение смены — Sprint 9 consolidation).

Read-path: ``build_story_block`` рендерит блок STORY (фаза + активные потоки
top-K + прогресс) для контекста при включённом ``story_enabled``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .. import crud
from ..config import settings

logger = logging.getLogger(__name__)


def _parse_json(value: Any) -> Any:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


async def _sync_threads_from_events(
    db: Any, chat_id: int, round_id: str | None, events: list
) -> tuple[int, int]:
    """Создать/обновить активные story_threads из важных story_events.

    Идемпотентно: существующая линия находится по имени (casefold),
    importance растёт (max), actors объединяются. Возвращает (created, updated).
    """
    threshold = float(settings.story_thread_min_importance or 0.0)
    created = 0
    updated = 0
    for ev in events:
        if float(ev.importance or 0.0) < threshold:
            continue
        name = (ev.event or "").strip()
        if not name:
            continue
        actors = ev.actors_json if hasattr(ev, "actors_json") else None
        actors_list = (
            actors
            if isinstance(actors, list)
            else _parse_json(getattr(ev, "actors", "[]"))
        )
        thread = await crud.find_story_thread_by_name(db, chat_id, name)
        if thread is None:
            thread = await crud.create_story_thread(
                db,
                chat_id=chat_id,
                name=name[:100],
                actors=actors_list,
                importance=float(ev.importance or 0.0),
                created_round_id=round_id,
            )
            created += 1
        else:
            await crud.update_story_thread(
                db,
                thread.id,
                importance=float(ev.importance or 0.0),
                actors=actors_list,
            )
            updated += 1
        if thread.id is not None and ev.story_thread_id != thread.id:
            await crud.set_story_event_thread(db, ev.id, thread.id)
    return created, updated


def _build_current_story(
    state: Any,
    events: list,
    active_threads: list,
    total_events: int,
    round_id: str | None,
) -> dict:
    """Собрать структурированный current_story (§16.2) из существующего + нового.

    Сохраняет ``completed_goals``/``characters``/``phase`` (пользовательские/
    будущих спринтов); обновляет summary/active_threads/progress.
    """
    current = _parse_json(getattr(state, "current_story", "{}"))
    if not isinstance(current, dict):
        current = {}

    # summary — последние сюжетные события (хронологический порядок).
    chronological = list(reversed(events))
    max_events = max(1, int(settings.story_summary_max_events or 20))
    summary = [
        (ev.event or "").strip()
        for ev in chronological[-max_events:]
        if (ev.event or "").strip()
    ]

    current["summary"] = summary
    current["active_threads"] = [
        (t.name or "").strip() for t in active_threads if (t.name or "").strip()
    ]
    current["completed_goals"] = current.get("completed_goals") or []
    current["progress"] = {
        "story_events": int(total_events),
        "active_threads": len(active_threads),
        "last_round": round_id,
    }
    current["phase"] = current.get("phase") or (getattr(state, "story_phase", "") or "")
    current["characters"] = current.get("characters") or {}
    return current


async def update_story_state_from_round(
    db: Any,
    chat_id: int,
    round_id: str | None,
    characters: list | None = None,
) -> dict:
    """Пост-раунд обновление story_state из story_events (Sprint 8).

    Вызывается стадией ``story`` pipeline ПОСЛЕ ``story_events`` (write-path).
    No-op при отключённом ``story_enabled``; падение не роняет раунд.
    """
    if not settings.story_enabled:
        return {"ok": True, "stage": "story", "skipped": "flag off"}
    if not round_id:
        return {"ok": True, "stage": "story", "skipped": "no round"}
    try:
        state = await crud.get_or_create_story_state(db, chat_id, round_id=round_id)
        limit = max(int(settings.story_summary_max_events or 20), 50)
        events = await crud.get_story_events_for_chat(db, chat_id, limit=limit)
        active_threads = await crud.get_active_story_threads(db, chat_id)
        created, updated = await _sync_threads_from_events(
            db, chat_id, round_id, events
        )
        active_threads = await crud.get_active_story_threads(db, chat_id)
        total_events = await crud.count_story_events(db, chat_id)
        current = _build_current_story(
            state, events, active_threads, total_events, round_id
        )
        state = await crud.update_story_state(
            db,
            chat_id,
            current_story=current,
            updated_round_id=round_id,
        )
        return {
            "ok": True,
            "stage": "story",
            "state": state is not None,
            "events": len(events),
            "threads_created": created,
            "threads_updated": updated,
        }
    except Exception as exc:  # noqa: BLE001 — стадия не должна ронять раунд
        logger.warning("Post-round pipeline: story state stage failed: %s", exc)
        return {"ok": False, "stage": "story", "error": str(exc)}


async def build_story_block(db: Any, chat_id: int) -> str:
    """STORY block (Sprint 8): фаза + активные потоки top-K + прогресс.

    Пусто при отсутствии story_state / отключённом флаге. Рендер data-only
    (активные потоки не раздувают контекст — top-K ``story_threads_max``).
    """
    try:
        if not settings.story_enabled:
            return ""
        from ..prompt_builder import build_story_block as _render

        state = await crud.get_story_state(db, chat_id)
        if state is None:
            return ""
        threads = await crud.get_active_story_threads(
            db, chat_id, top_k=int(settings.story_threads_max or 5)
        )
        return _render(state, threads)
    except Exception as exc:  # noqa: BLE001 — блок не должен ронять контекст
        logger.warning(
            "Failed to build story block for chat %s: %s", chat_id, exc
        )
        return ""


def story_state_to_dict(state: Any) -> dict:
    """story_state → dict для API (original_plot/current_story/story_phase)."""
    return {
        "id": getattr(state, "id", None),
        "chat_id": getattr(state, "chat_id", None),
        "original_plot": getattr(state, "original_plot", "") or "",
        "current_story": _parse_json(getattr(state, "current_story", "{}")),
        "story_phase": getattr(state, "story_phase", "") or "",
        "updated_round_id": getattr(state, "updated_round_id", None),
        "version": getattr(state, "version", 1),
        "updated_at": getattr(state, "updated_at", None),
    }


def thread_to_dict(thread: Any) -> dict:
    """story_thread → dict для API."""
    return {
        "id": getattr(thread, "id", None),
        "chat_id": getattr(thread, "chat_id", None),
        "name": getattr(thread, "name", "") or "",
        "actors": _parse_json(getattr(thread, "actors", "[]")),
        "importance": getattr(thread, "importance", 0),
        "status": getattr(thread, "status", "active"),
        "created_round_id": getattr(thread, "created_round_id", None),
        "updated_at": getattr(thread, "updated_at", None),
    }


def story_event_to_dict(event: Any) -> dict:
    """story_event → dict для API."""
    return {
        "id": getattr(event, "id", None),
        "event_id": getattr(event, "event_id", None),
        "chat_id": getattr(event, "chat_id", None),
        "round_id": getattr(event, "round_id", None),
        "event": getattr(event, "event", "") or "",
        "actors": _parse_json(getattr(event, "actors", "[]")),
        "location": getattr(event, "location", "") or "",
        "cause": getattr(event, "cause", "") or "",
        "consequences": getattr(event, "consequences", "") or "",
        "importance": getattr(event, "importance", 0),
        "story_thread_id": getattr(event, "story_thread_id", None),
        "created_at": getattr(event, "created_at", None),
    }
