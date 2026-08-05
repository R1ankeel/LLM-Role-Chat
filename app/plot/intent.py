"""NPC Intent — детерминированный слой перед генерацией (Plans/update20.md §21, Sprint 10).

Intent формируется ПРАВИЛАМИ (без LLM) из ``character_states.active_goal`` +
активного плана + открытых issues + beliefs + story threads. LLM реализует
intent естественным языком и НЕ изобретает состояние мира. Intent — тенденция,
не команда (риск Sprint 10, по образцу behavior drivers). Не каждый ход имеет
intent: если у персонажа нет цели — блок отсутствует (§21).

Write-path: ``intents`` (только при ``npc_intent_enabled``); read-path —
блок ``ACTIVE GOAL`` в контексте (рендер из текущего intent, топ-N истории).
"""

from __future__ import annotations

import logging
from typing import Any

from .. import crud
from ..config import settings
from . import plot_pressure

logger = logging.getLogger(__name__)

_APPROACH_LABELS = {
    "direct": "direct",
    "indirect": "indirect",
    "avoid": "avoid",
    "delay": "delay",
}


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _parse_actors(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(a) for a in raw]
    if isinstance(raw, str):
        import json

        try:
            parsed = json.loads(raw)
            return [str(a) for a in parsed] if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []
    return []


def compute_intent(
    *,
    character_state: Any = None,
    active_plan: Any = None,
    open_issues: list | None = None,
    issue_targets: dict | None = None,
    beliefs: list | None = None,
    story_threads: list | None = None,
    story_pressure: float = 0.0,
    character_name: str = "",
    target_names: dict | None = None,
    risk_avoid: float | None = None,
    risk_delay: float | None = None,
    min_urgency: float | None = None,
) -> dict | None:
    """Детерминированно сформировать intent NPC (§21).

    Возвращает dict {goal, target, target_name, approach, urgency, emotion,
    risk} или None, если у персонажа нет цели. Источник цели — по приоритету:
    активный план > ``active_goal`` (character_state) > топ-open-issue >
    активный story_thread (участие персонажа, затем самый важный).
    """
    target_names = target_names or {}
    issues = list(open_issues or [])
    threads = list(story_threads or [])
    bels = list(beliefs or [])
    risk_avoid = settings.intent_risk_avoid if risk_avoid is None else float(risk_avoid)
    risk_delay = settings.intent_risk_delay if risk_delay is None else float(risk_delay)
    min_urgency = settings.intent_min_urgency if min_urgency is None else float(min_urgency)

    # ---- источник цели (по приоритету) ----
    goal = None
    target_id = None
    source = "none"
    priority_score = 0.0

    plan = active_plan
    if plan is not None and (getattr(plan, "status", "active") in ("active", "blocked")):
        goal_text = (getattr(plan, "goal", "") or "").strip()
        if goal_text:
            goal = goal_text
            source = "plan"
            priority_score = (getattr(plan, "priority", 5) or 5) / 10.0

    if goal is None and character_state is not None:
        active_goal = (getattr(character_state, "active_goal", "") or "").strip()
        if active_goal:
            goal = active_goal[:500]
            source = "active_goal"
            priority_score = 0.5

    if goal is None and issues:
        top = issues[0]
        issue_text = (getattr(top, "text", "") or "").strip()
        if issue_text:
            goal = issue_text[:500]
            source = "issue"
            priority_score = (getattr(top, "importance", 5) or 5) / 10.0
            target_id = (issue_targets or {}).get(getattr(top, "id", None))

    if goal is None and threads:
        # линия с участием персонажа (по имени), затем самый важный
        ordered = sorted(
            threads,
            key=lambda t: (getattr(t, "importance", 0) or 0),
            reverse=True,
        )
        thread = None
        for candidate in ordered:
            actors = _parse_actors(getattr(candidate, "actors", "[]"))
            if character_name and character_name in actors:
                thread = candidate
                break
        if thread is None and ordered:
            thread = ordered[0]
        if thread is not None:
            thread_name = (getattr(thread, "name", "") or "").strip()
            if thread_name:
                goal = f"Продолжить линию: {thread_name}"[:500]
                source = "thread"
                priority_score = (getattr(thread, "importance", 0) or 0) / 10.0

    if not goal:
        return None

    # ---- urgency (0..1) ----
    urgency = _clamp01(priority_score + float(story_pressure) * 0.5)

    # ---- risk (0..1) ----
    stress = 0.3
    if character_state is not None:
        stress_val = getattr(character_state, "stress", None)
        if stress_val is not None:
            try:
                stress = float(stress_val)
            except (TypeError, ValueError):
                stress = 0.3
    blocked = bool(
        plan is not None
        and (getattr(plan, "blocked_by", "") or "").strip()
    )
    risk = _clamp01(
        stress * 0.6 + (0.3 if blocked else 0.0) + float(story_pressure) * 0.3
    )

    # ---- approach ----
    target_name = target_names.get(target_id, "") if target_id is not None else ""
    suspicion = any(
        (getattr(b, "type", "") == "suspicion")
        and (
            (getattr(b, "object", "") or "") == target_name
            or not target_name
        )
        and float(getattr(b, "confidence", 0.5) or 0.5) >= 0.5
        for b in bels
    )
    approach = "direct"
    if blocked:
        approach = "delay"
    elif suspicion:
        approach = "indirect"
    elif risk >= risk_avoid:
        approach = "avoid"
    elif risk >= risk_delay and urgency < 0.5:
        approach = "delay"

    # Не каждый ход имеет intent: слабая цель-кандидат (issue/thread) ниже
    # порога настойчивости intent не формирует.
    if urgency < min_urgency and source in ("issue", "thread"):
        return None

    emotion = ""
    if character_state is not None:
        emotion = (getattr(character_state, "mood", "") or "").strip()

    intent: dict = {
        "goal": goal,
        "target": target_id,
        "approach": approach,
        "urgency": round(urgency, 3),
        "emotion": emotion,
        "risk": round(risk, 3),
    }
    if target_id is not None:
        intent["target_name"] = target_name
    intent["source"] = source
    return intent


def intent_to_dict(row: Any) -> dict:
    """intents → dict (для API/debug, Sprint 10)."""
    return {
        "id": getattr(row, "id", None),
        "chat_id": getattr(row, "chat_id", None),
        "character_id": getattr(row, "character_id", None),
        "goal": getattr(row, "goal", "") or "",
        "target": getattr(row, "target", None),
        "approach": getattr(row, "approach", "direct") or "direct",
        "urgency": getattr(row, "urgency", 0.0) or 0.0,
        "emotion": getattr(row, "emotion", "") or "",
        "risk": getattr(row, "risk", 0.0) or 0.0,
        "created_round_id": getattr(row, "created_round_id", None),
    }


async def _load_open_issues(
    db: Any, chat_id: int, character_id: int
) -> tuple[list, dict]:
    """Top-open-issues персонажа + карта issue.id → target_character_id.

    Возвращает ``(issues, issue_targets)``: target резолвится по направленному
    отношению (``crud.get_relationship_target_id``); SQLAlchemy-модель issue
    НЕ мутируется (нет колонки target_id).
    """
    from ..relationship_service import list_top_open_issues_for_character

    issues = await list_top_open_issues_for_character(
        db, chat_id, character_id, limit=3
    )
    targets: dict = {}
    for issue in issues:
        rel_id = getattr(issue, "relationship_id", None)
        if rel_id is None:
            continue
        try:
            targets[issue.id] = await crud.get_relationship_target_id(db, rel_id)
        except Exception:  # noqa: BLE001 — target опционален
            targets[issue.id] = None
    return issues, targets


async def compute_intent_for_character(
    db: Any,
    chat_id: int,
    character: Any,
    *,
    round_id: str | None = None,
    character_state: Any = None,
    character_names: dict | None = None,
) -> dict | None:
    """Сформировать intent для персонажа (§21) и записать его в ``intents``.

    No-op при выключенном ``npc_intent_enabled`` (canary — read-path не
    читает intents до включения флага). Падение не роняет раунд (вызывающий
    оборачивает в try/except).
    """
    if not settings.npc_intent_enabled:
        return None
    if character_state is None:
        try:
            character_state = await crud.get_character_state(db, character.id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to load character state for intent: %s", exc)
            character_state = None

    active_plan = None
    if settings.npc_plans_enabled:
        try:
            active_plan = await crud.get_active_npc_plan(db, chat_id, character.id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to load active plan for intent: %s", exc)

    open_issues: list = []
    issue_targets: dict = {}
    if settings.relationship_issues_enabled:
        try:
            open_issues, issue_targets = await _load_open_issues(
                db, chat_id, character.id
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to load open issues for intent: %s", exc)

    beliefs: list = []
    if settings.beliefs_enabled:
        try:
            beliefs = await crud.get_beliefs_for_character(
                db, character.id, top_k=20
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to load beliefs for intent: %s", exc)

    story_threads: list = []
    if settings.story_enabled:
        try:
            story_threads = await crud.get_active_story_threads(
                db, chat_id, top_k=int(settings.story_threads_max or 5)
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to load story threads for intent: %s", exc)

    story_pressure = plot_pressure.compute_story_pressure(
        issues_score=plot_pressure.issues_score_from_issues(open_issues),
        goals_blocked=plot_pressure.goals_blocked_score(
            has_goal=bool(
                (character_state is not None
                 and (getattr(character_state, "active_goal", "") or "").strip())
                or (active_plan is not None)
            ),
            plan_blocked=bool(
                active_plan is not None
                and (getattr(active_plan, "blocked_by", "") or "").strip()
            ),
        ),
        stagnation_rounds=0,
        recent_intensity=0.0,
    )

    intent = compute_intent(
        character_state=character_state,
        active_plan=active_plan,
        open_issues=open_issues,
        issue_targets=issue_targets,
        beliefs=beliefs,
        story_threads=story_threads,
        story_pressure=story_pressure,
        character_name=(getattr(character, "name", "") or "").strip(),
        target_names=character_names or {},
    )
    if intent:
        try:
            await crud.save_intent(
                db,
                chat_id=chat_id,
                character_id=character.id,
                goal=intent["goal"],
                target=intent.get("target"),
                approach=intent.get("approach") or "direct",
                urgency=float(intent.get("urgency") or 0.0),
                emotion=intent.get("emotion") or "",
                risk=float(intent.get("risk") or 0.0),
                created_round_id=round_id,
            )
        except Exception as exc:  # noqa: BLE001 — запись intent не роняет раунд
            logger.warning(
                "[chat_id=%d] Failed to save intent for %s: %s",
                chat_id, character.name, exc,
            )
    return intent
