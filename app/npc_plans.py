"""Долгоживущие маленькие планы NPC (Plans/update20.md §22, Sprint 10).

НЕ GOAP/planner: «я хочу сделать X, но сейчас мне мешает Y». Один активный
план на персонажа (обычно). Создание — детерминированное (из intent/цели);
обновление ``next_step``/``blocked_by`` — пост-раунд по событиям. В контексте —
компактный блок ``ACTIVE PLAN``. Флаг ``npc_plans_enabled``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from . import crud
from .config import settings
from .plot.story_threads import significant_tokens, token_overlap

logger = logging.getLogger(__name__)

# Порог overlap события с целью/блокировкой для детерминированного
# продвижения плана (минимальный совпадающий значимый токен).
_PLAN_EVENT_OVERLAP = 0.4


def plan_to_dict(plan: Any) -> dict:
    """npc_plan → dict (для API/debug, Sprint 10)."""
    return {
        "id": getattr(plan, "id", None),
        "chat_id": getattr(plan, "chat_id", None),
        "character_id": getattr(plan, "character_id", None),
        "goal": getattr(plan, "goal", "") or "",
        "next_step": getattr(plan, "next_step", "") or "",
        "blocked_by": getattr(plan, "blocked_by", "") or "",
        "priority": getattr(plan, "priority", 5) or 5,
        "status": getattr(plan, "status", "active") or "active",
        "created_round_id": getattr(plan, "created_round_id", None),
        "updated_at": getattr(plan, "updated_at", None),
    }


def build_active_plan_block(plan: Any) -> str:
    """ACTIVE PLAN блок (§23, Sprint 10). Компактная строка, данные-не-приказ."""
    if plan is None:
        return ""
    goal = (getattr(plan, "goal", "") or "").strip()
    if not goal:
        return ""
    parts = [f"План: {goal}"]
    next_step = (getattr(plan, "next_step", "") or "").strip()
    if next_step:
        parts.append(f"Следующий шаг: {next_step}")
    blocked_by = (getattr(plan, "blocked_by", "") or "").strip()
    if blocked_by:
        parts.append(f"Препятствие: {blocked_by}")
    return (
        "<active_plan data>\n"
        + "\n".join(f"- {part}" for part in parts)
        + "\n(это твой текущий план и то, что тебе мешает, а не приказ — ты сам решаешь, как действовать)\n"
        + "</active_plan data>"
    )


def _event_text(event: dict) -> str:
    """Текст события из action-структуры world_event (для сопоставления)."""
    action = event.get("action") or {}
    if isinstance(action, str):
        try:
            action = json.loads(action)
        except (json.JSONDecodeError, TypeError):
            action = {}
    if not isinstance(action, dict):
        action = {}
    actor = (action.get("actor") or "").strip()
    verb = (action.get("action") or "").strip()
    target = (action.get("target") or "").strip()
    obj = (action.get("object") or "").strip()
    parts = [p for p in (actor, verb, target, obj) if p]
    if parts:
        return " ".join(parts)
    return (event.get("event_type") or "").strip()


async def get_or_create_active_plan(
    db: Any,
    chat_id: int,
    character_id: int,
    goal: str,
    *,
    next_step: str = "",
    priority: int = 5,
    round_id: str | None = None,
) -> Any:
    """Вернуть активный план персонажа или создать новый (один активный, §22).

    Если у персонажа уже есть живой план (active|blocked) — возвращается он,
    второй НЕ создаётся (даже при другой цели). Новый создаётся только при
    отсутствии живого плана. Canary: работает только при ``npc_plans_enabled``.
    """
    if not settings.npc_plans_enabled:
        return None
    existing = await crud.get_active_npc_plan(db, chat_id, character_id)
    if existing is not None:
        return existing
    return await crud.create_npc_plan(
        db,
        chat_id=chat_id,
        character_id=character_id,
        goal=(goal or "")[:500],
        next_step=(next_step or "")[:500],
        priority=priority,
        created_round_id=round_id,
    )


async def update_plan_from_round(
    db: Any,
    plan: Any,
    round_events: list[dict],
    *,
    round_id: str | None = None,
    resolve_importance: float | None = None,
) -> dict:
    """Обновить ``next_step``/``blocked_by``/``status`` плана по событиям раунда.

    Детерминированно (§22 «пост-раунд по событиям»):
      - событие, пересекающееся с целью плана (token overlap ≥ 0.4):
        * важность ≥ ``npc_plan_resolve_importance`` → план ``done``
          (цель достигнута);
        * иначе → ``next_step`` = текст события (сделанный шаг),
          блокировка снимается;
      - событие, пересекающееся с ``blocked_by`` и достаточно важное →
        блокировка снимается.
    Возвращает отчёт {status, next_step_changed, unblocked}. Не менят план,
    если совпадений нет. ``round_events`` — list[dict] с ключами action/
    importance (из ``crud.get_round_world_events``).
    """
    if plan is None:
        return {"status": "none", "next_step_changed": False, "unblocked": False}
    if resolve_importance is None:
        resolve_importance = float(settings.npc_plan_resolve_importance or 7.0)
    goal_tokens = significant_tokens((getattr(plan, "goal", "") or "").strip())
    blocked_tokens = significant_tokens(
        (getattr(plan, "blocked_by", "") or "").strip()
    )
    status_changed = ""
    next_step_changed = False
    unblocked = False

    for event in round_events or []:
        text = _event_text(event)
        if not text:
            continue
        importance = event.get("importance")
        try:
            importance = float(importance) if importance is not None else 0.0
        except (TypeError, ValueError):
            importance = 0.0
        text_tokens = significant_tokens(text)
        if not text_tokens:
            continue

        if goal_tokens:
            hits = len(goal_tokens & text_tokens)
            overlap = hits / len(goal_tokens)
            if overlap >= _PLAN_EVENT_OVERLAP:
                if importance >= resolve_importance:
                    await crud.update_npc_plan(
                        db, plan.id, status="done"
                    )
                    status_changed = "done"
                else:
                    await crud.update_npc_plan(
                        db, plan.id, next_step=text[:500], blocked_by=""
                    )
                    next_step_changed = True
                    unblocked = bool(blocked_tokens)
                break

        if blocked_tokens and not status_changed and not next_step_changed:
            hits = len(blocked_tokens & text_tokens)
            overlap = hits / len(blocked_tokens)
            if overlap >= _PLAN_EVENT_OVERLAP and importance >= resolve_importance:
                await crud.update_npc_plan(db, plan.id, blocked_by="")
                unblocked = True
                break

    return {
        "status": status_changed,
        "next_step_changed": next_step_changed,
        "unblocked": unblocked,
    }
