"""Story History write-path (Plans/update20.md §16.3, Sprint 8).

Проекция канонических ``world_events`` раунда (Sprint 1) в ``story_events``:
детерминированно, без LLM. Пишутся ТОЛЬКО extraction-события с
``importance >= story_event_min_importance``; запись идемпотентна
(повторный прогон pipeline не дублирует события по ``event_id``).

Поля-надстройки (``cause`` из ``event_links``, ``actors``) — проекционная
разметка, каноническая истина остаётся в ``world_events``.
"""

from __future__ import annotations

import logging
from typing import Any

from .. import crud
from ..config import settings

logger = logging.getLogger(__name__)

_THREAD_NAME_MAX = 100


def render_event_line(event: dict, character_names: dict[int, str]) -> str:
    """Человекочитаемая строка события для ``story_events.event`` / thread name.

    Вид: ``Актёр: действие объект → Цель1, Цель2``. Детерминированно из
    ``action``/``event_type``; пустой action деградирует в ``event_type``.
    """
    action = event.get("action") or {}
    if not isinstance(action, dict):
        action = {}
    verb = str(action.get("action") or "").strip()
    obj = str(action.get("object") or "").strip()
    if verb or obj:
        text = verb
        if obj:
            text = f"{text} {obj}".strip() if text else obj
    else:
        text = str(event.get("event_type") or "событие").strip()

    actor_id = event.get("character_id")
    actor = character_names.get(actor_id, "") if actor_id is not None else ""
    if actor:
        text = f"{actor}: {text}"

    targets = [
        character_names[tid]
        for tid in (event.get("target_character_ids") or [])
        if tid in character_names
    ]
    if targets:
        text = f"{text} → {', '.join(targets)}"
    return text.strip()


def _thread_name_for(event_line: str) -> str:
    """Имя сюжетной линии = строка события, обрезанная до разумной длины."""
    return event_line[:_THREAD_NAME_MAX].strip() or "событие"


async def write_story_events_from_round(
    db: Any,
    chat_id: int,
    round_id: str | None,
    character_names: dict[int, str],
) -> dict:
    """Записать story_events из extraction world_events раунда (§16.3).

    No-op при отключённом ``story_enabled``; идемпотентно (skip уже
    записанных event_id); падение не роняет раунд (стадия в try/except).
    """
    if not settings.story_enabled:
        return {"ok": True, "stage": "story_events", "skipped": "flag off"}
    if not round_id:
        return {"ok": True, "stage": "story_events", "skipped": "no round"}
    try:
        candidates = await crud.get_story_round_world_events(db, chat_id, round_id)
        if not candidates:
            return {"ok": True, "stage": "story_events", "written": 0}

        existing_ids = await crud.get_story_event_ids_for_chat(db, chat_id)
        min_importance = float(settings.story_event_min_importance or 0.0)

        to_write = [
            ev for ev in candidates
            if ev.get("id") not in existing_ids
            and float(ev.get("importance") or 0.0) >= min_importance
        ]
        if not to_write:
            return {
                "ok": True,
                "stage": "story_events",
                "written": 0,
                "skipped_below_importance": len(candidates),
            }

        cause_map = await crud.get_caused_by_ids_for_events(
            db, chat_id, [ev["id"] for ev in to_write]
        )
        cause_ids = {
            cid
            for ids in cause_map.values()
            for cid in ids
        }
        cause_texts = await crud.get_world_events_by_ids(db, list(cause_ids))

        written = 0
        skipped_below = len(candidates) - len(to_write)
        for ev in to_write:
            line = render_event_line(ev, character_names)
            cause_parts = [
                render_event_line(cause_texts[cid], character_names)
                for cid in cause_map.get(ev["id"], [])
                if cid in cause_texts
            ]
            await crud.create_story_event(
                db,
                event_id=ev.get("id"),
                chat_id=chat_id,
                round_id=round_id,
                event=line,
                actors=[
                    character_names[cid]
                    for cid in [ev.get("character_id")]
                    + list(ev.get("target_character_ids") or [])
                    if cid is not None and cid in character_names
                ],
                location=ev.get("location") or "",
                cause="; ".join(cause_parts)[:4000],
                consequences="",
                importance=float(ev.get("importance") or 0.0),
            )
            written += 1
        logger.info(
            "[chat_id=%d] story_events written=%d skipped_below=%d (round=%s)",
            chat_id, written, skipped_below, round_id,
        )
        return {
            "ok": True,
            "stage": "story_events",
            "written": written,
            "skipped_below_importance": skipped_below,
        }
    except Exception as exc:  # noqa: BLE001 — стадия не должна ронять раунд
        logger.warning(
            "Post-round pipeline: story_events stage failed: %s", exc
        )
        return {"ok": False, "stage": "story_events", "error": str(exc)}
