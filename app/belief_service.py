"""Belief System — знания/убеждения персонажа (Plans/update20.md §9, Sprint 5).

Персонаж НЕ автоматически знает World Truth: в контекст попадают ТОЛЬКО его
beliefs (subject/predicate/object, source, confidence 0..1, type fact|belief|
suspicion). Обновление — пост-раунд детерминированно через pipeline:

    world_event → perceive → attention → belief update

- только из событий, которые персонаж реально воспринял (presence из
  ``message_presence``, attention из той же строки) — изоляция R2;
- source от presence: present → direct_observation, mentioned → heard,
  audible → rumor, told → told_by (confidence по trust believer→teller),
  absent → belief НЕ пишется;
- attention gating (Sprint 4): при ``attention_enabled`` событие с
  ``attention < attention_low`` («слышал фоном») в belief НЕ идёт;
- type: fact (высокая уверенность + direct/world-confirmed), belief,
  suspicion (низкая уверенность / неподтверждённый слух без world_truth_ref).

Постепенное замещение MVP epistemic mask: при ``beliefs_enabled=true`` mask
читает beliefs; при false — mask остаётся fallback (canary, §26).

Потребители: ``context_builder`` (блок WHAT YOU KNOW, рендер по флагу
``beliefs_enabled``), ``chat_engine`` (belief-aware epistemic evidence),
debug-API (§29.1).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from . import crud
from .config import settings

logger = logging.getLogger(__name__)

# Источники (§9): direct_observation | heard | told_by | inference | rumor | memory
BELIEF_SOURCES = (
    "direct_observation",
    "heard",
    "told_by",
    "inference",
    "rumor",
    "memory",
)
BELIEF_TYPES = ("fact", "belief", "suspicion")

# Presence → источник (WPE presence-лестница → §9).
# present — прямое наблюдение; mentioned — упоминание (услышал); audible —
# muffled/анонимный (слух); told — сообщил другой персонаж; absent — НЕ писать.
_PRESENCE_TO_SOURCE: dict[str, str | None] = {
    "present": "direct_observation",
    "mentioned": "heard",
    "audible": "rumor",
    "told": "told_by",
    "absent": None,
}

# Базовые уверенности по источнику (детерминированные таблицы §9).
_BASE_CONFIDENCE: dict[str, float] = {
    "direct_observation": 0.85,
    "heard": 0.7,
    "told_by": 0.5,
    "inference": 0.6,
    "rumor": 0.3,
    "memory": 0.5,
}

# Порог для type="fact" (высокая уверенность при прямом наблюдении/подтверждении).
_FACT_CONFIDENCE = 0.75
_BELIEF_CONFIDENCE = 0.5


# ---------------------------------------------------------------------------
# Детерминированные правила (чистые функции)
# ---------------------------------------------------------------------------

def source_for_presence(presence: str | None) -> str | None:
    """Источник belief по presence (None → belief не пишется)."""
    if not presence:
        return None
    return _PRESENCE_TO_SOURCE.get(presence)


def base_confidence(source: str) -> float:
    """Базовая уверенность источника (0..1)."""
    return _BASE_CONFIDENCE.get(source, 0.5)


def told_by_confidence(trust: float | None) -> float:
    """Уверенность told_by зависит от trust(believer→teller) (аналог hearsay).

    trust 0..100 → 0.2..0.8: низкое доверие почти обнуляет, высокое — усиливает.
    Отсутствующее отношение = нейтральное (trust 50 → 0.5).
    """
    if trust is None:
        return 0.5
    t = max(0.0, min(100.0, float(trust)))
    return 0.2 + 0.6 * (t / 100.0)


def compute_confidence(source: str, *, trust: float | None = None) -> float:
    """Итоговая уверенность для источника (§9)."""
    if source == "told_by":
        return told_by_confidence(trust)
    return base_confidence(source)


def belief_type(source: str, confidence: float, *, confirmed: bool = False) -> str:
    """type: fact (знает) | belief (полагает) | suspicion (подозревает).

    fact — высокая уверенность И (прямое наблюдение ИЛИ подтверждение миром);
    suspicion — низкая уверенность либо слух/инференс без подтверждения.
    """
    if confidence >= _FACT_CONFIDENCE and (source == "direct_observation" or confirmed):
        return "fact"
    if confidence >= _BELIEF_CONFIDENCE:
        return "belief"
    return "suspicion"


def merge_confidence(current: float, new: float) -> float:
    """Слияние уверенности при повторном наблюдении (детерминированное).

    Повторное наблюдение усиливает (идём к новому значению), но не выходит за
    0..1; слабый новый источник не обнуляет сильное существующее убеждение.
    """
    return min(1.0, max(float(current), float(new)))


def triplet_from_event(event: dict[str, Any], character_names: dict[int, str]) -> tuple[str, str, str]:
    """Триплет (subject, predicate, object) из структурированного события.

    Берёт ``action`` (JSON {"actor", "action", "target", "object"}, Sprint 1);
    фолбэк — имя автора + event_type. Пустые поля не возвращаются.
    """
    action = event.get("action") or {}
    if not isinstance(action, dict):
        action = {}
    actor = (action.get("actor") or "").strip()
    subject = actor or character_names.get(event.get("character_id"), "")
    predicate = (action.get("action") or event.get("event_type") or "действие").strip()
    obj = (action.get("object") or action.get("target") or "").strip()
    return subject, predicate, obj


# ---------------------------------------------------------------------------
# Сбор входов раунда
# ---------------------------------------------------------------------------

async def collect_round_inputs(
    db: AsyncSession,
    chat_id: int,
    round_id: str,
) -> dict[str, Any]:
    """Собрать входы за раунд: world events + presence/attention карты.

    - events: ``crud.get_round_world_events`` (все события раунда, §9);
    - presence/attention: по ``message_id`` события (привязка к восприятию).
    """
    return {
        "events": await crud.get_round_world_events(db, round_id),
    }


# ---------------------------------------------------------------------------
# Обновление beliefs за раунд
# ---------------------------------------------------------------------------

async def update_beliefs_from_round(
    db: AsyncSession,
    chat_id: int,
    round_id: str,
    characters: list[Any],
    *,
    inputs: dict[str, Any] | None = None,
    client: Any = None,
) -> dict[str, Any]:
    """Детерминированно обновить beliefs всех персонажей за раунд (§9).

    Для каждого персонажа: события раунда, которые он реально воспринял
    (presence != absent, attention gating) → belief update. Только
    direct_observation путь (LLM-suggestion под benchmark gate §27 — не здесь).
    Возвращает отчёт {beliefs, written, updated, skipped}.
    """
    report = {"characters": 0, "written": 0, "updated": 0, "skipped": 0}
    if not characters or not round_id:
        return report

    if inputs is None:
        inputs = await collect_round_inputs(db, chat_id, round_id)

    events = inputs.get("events", [])
    if not events:
        return report

    for character in characters:
        report["characters"] += 1
        seen: set[tuple[str, str, str]] = set()
        for event in events:
            message_id = event.get("message_id")
            if message_id is None:
                continue
            presence = await crud.get_presence_for_message(db, message_id, character.id)
            source = source_for_presence(presence)
            if source is None:
                report["skipped"] += 1
                continue
            if settings.attention_enabled:
                attention = await crud.get_attention_for_message(
                    db, message_id, character.id
                )
                if attention is not None and attention < settings.attention_low:
                    report["skipped"] += 1
                    continue
            subject, predicate, obj = triplet_from_event(event, {})
            if not subject or not predicate:
                report["skipped"] += 1
                continue
            key = (subject, predicate, obj)
            if key in seen:
                report["skipped"] += 1
                continue
            seen.add(key)

            trust = await _trust_to_teller(db, character.id, event.get("character_id"))
            confidence = compute_confidence(source, trust=trust)
            confirmed = source == "direct_observation"
            btype = belief_type(source, confidence, confirmed=confirmed)

            existing = await crud._find_belief(db, character.id, subject, predicate, obj)
            if existing is not None:
                merged = merge_confidence(existing.confidence, confidence)
                await crud.upsert_belief(
                    db,
                    chat_id,
                    character.id,
                    subject=subject,
                    predicate=predicate,
                    object=obj,
                    source=source,
                    confidence=merged,
                    type=btype,
                    world_truth_ref=(
                        event.get("id") if confirmed else existing.world_truth_ref
                    ),
                )
                report["updated"] += 1
            else:
                await crud.upsert_belief(
                    db,
                    chat_id,
                    character.id,
                    subject=subject,
                    predicate=predicate,
                    object=obj,
                    source=source,
                    confidence=confidence,
                    type=btype,
                    world_truth_ref=event.get("id") if confirmed else None,
                )
                report["written"] += 1
    return report


async def _trust_to_teller(
    db: AsyncSession, believer_id: int, teller_id: int | None
) -> float | None:
    """trust(believer→teller), 0..100; None, если отношения нет/себя сам."""
    if teller_id is None or teller_id == believer_id:
        return None
    from .relationship_service import get_relationship

    try:
        rel = await get_relationship(db, believer_id, teller_id)
    except Exception as exc:  # noqa: BLE001 — belief не должен ронять раунд
        logger.warning("Belief trust lookup failed: %s", exc)
        return None
    return float(getattr(rel, "trust", 50.0)) if rel is not None else None
