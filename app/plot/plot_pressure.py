"""Story pressure — детерминированная мера напряжённости сюжета (Plans/update20.md §19, Sprint 10).

Только правила, без LLM. Используется intent-слоем (urgency/risk) и (в Sprint 11)
crisis engine. НЕ форсирует сюжет: pressure — сигнал, а не команда
(запрещён паттерн ``if trust<30: force_argument``).

Компоненты (§19):
    pressure = w_issues × unresolved_issues_score
             + w_goals × goals_blocked_score
             + w_stagnation × stagnation_score
             + w_recent × recent_intensity_score
(сумма весов нормируется).
"""

from __future__ import annotations

from typing import Any, Iterable

from ..config import settings

_CLAMPED = 1.0


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def default_weights() -> dict[str, float]:
    """Веса компонентов pressure из настроек (Sprint 10)."""
    return {
        "issues": settings.plot_pressure_weight_issues,
        "goals": settings.plot_pressure_weight_goals,
        "stagnation": settings.plot_pressure_weight_stagnation,
        "recent": settings.plot_pressure_weight_recent,
    }


def compute_story_pressure(
    *,
    issues_score: float = 0.0,
    goals_blocked: float = 0.0,
    stagnation_rounds: int = 0,
    recent_intensity: float = 0.0,
    weights: dict[str, float] | None = None,
) -> float:
    """Story pressure 0..1 из детерминированных компонентов (§19).

    ``stagnation_rounds`` — число раундов без важных событий (нормируется на
    ``plot_pressure_goal_blocked_rounds``); ``recent_intensity`` — интенсивность
    недавних событий (0..1). Пустой вклад пропускается (не «съедает» долю).
    """
    weights = weights or default_weights()
    stagnation_score = _clamp01(
        max(0, int(stagnation_rounds))
        / max(1, int(settings.plot_pressure_goal_blocked_rounds))
    )
    components = {
        "issues": _clamp01(issues_score),
        "goals": _clamp01(goals_blocked),
        "stagnation": stagnation_score,
        "recent": _clamp01(recent_intensity),
    }
    total = 0.0
    wsum = 0.0
    for name, weight in weights.items():
        if weight <= 0:
            continue
        total += float(weight) * components.get(name, 0.0)
        wsum += float(weight)
    if wsum <= 0:
        return 0.0
    return _clamp01(total / wsum)


def issues_score_from_issues(issues: Iterable[Any]) -> float:
    """0..1: нерешённость открытых issues (importance × salience, §19 w_issues).

    Аналог proactive boost (§7.4): каждая issue даёт ``(importance/10) ×
    salience``, где salience затухает по ``rounds_since_last_mention``.
    Нормировка: сумма делится на 3 (≈3 значимых конфликта = максимум).
    """
    issues = list(issues)
    if not issues:
        return 0.0
    decay_rounds = max(1, int(settings.issue_salience_decay_rounds))
    total = 0.0
    for issue in issues:
        importance = max(0, min(10, int(getattr(issue, "importance", 5) or 5)))
        rounds_since = max(
            0, int(getattr(issue, "rounds_since_last_mention", 0) or 0)
        )
        salience = _clamp01(1.0 - rounds_since / decay_rounds)
        total += (importance / 10.0) * salience
    return _clamp01(total / 3.0)


def goals_blocked_score(
    *,
    has_goal: bool,
    plan_blocked: bool,
    goal_rounds: int = 0,
    blocked_rounds: int | None = None,
) -> float:
    """0..1: личная цель персонажа блокируется (§19 w_goals).

    Без цели — 0. Блокирующий фактор плана даёт базовый вклад; долгое
    неразрешение цели растягивает score к 1 по ``goal_rounds``.
    """
    if not has_goal:
        return 0.0
    blocked_rounds = blocked_rounds or settings.plot_pressure_goal_blocked_rounds
    base = 0.35 if plan_blocked else 0.0
    time_score = max(0, int(goal_rounds)) / max(1, int(blocked_rounds))
    return _clamp01(base + time_score)


def recent_intensity_score(events_importance: Iterable[float]) -> float:
    """0..1: средняя важность недавних сюжетных событий (окно, §19 w_recent)."""
    importances = [float(v) for v in events_importance]
    if not importances:
        return 0.0
    return _clamp01(sum(importances) / (10.0 * len(importances)))
