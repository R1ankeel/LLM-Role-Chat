"""WPE 3.0 (Plans/WPE.md) Фаза 3 — shadow Perception (2 канала) + классификация.

Двухканальный ``perception.perceive()`` (И13) и Address Resolution
(``addressed`` / ``remote_status``) запускаются в shadow: результат
логируется (``[WPE-P3] shadow …``), в сборку контекста не идёт. Каждое
расхождение со старым ``perception.can_character_perceive_event``
классифицируется по четырём категориям v2 (§7 Фаза 2):

- ``regression``            — old=present, new=absent   (блокирует переход, чинится)
- ``fix``                   — old=absent,  new=present  (ожидаемое исправление)
- ``expected_expansion``    — old=absent,  new=partial  (ожидаемое расширение)
- ``expected_model_change`` — old=local,   new=remote/partial (смена модели, И13)

Новые ``expected_model_change`` под-категории от И13: ``GLASS`` (стекло,
visual=full/audio=none), ``SHOUT_THROUGH_WALL`` (крик из-за стены,
audio=full/visual=none вне одной локации), ``INVISIBLE`` (невидимость,
audio=full/visual=none в одной локации), ``WALL`` (audio=muffled/visual=none).

Критерий выхода (§10, Plans/WPE.md): все существенные классы расхождений
выявлены, каждый закреплён golden-тестом, необъяснимых расхождений нет —
счётчик ``WPE_SHADOW_STATS["unexplained"]`` должен оставаться нулевым.
Откат: флаг ``WORLD_ENGINE_EVENTS_ENABLED`` выключается, dual-write можно
оставить.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Mapping
from typing import Any

from . import crud
from . import perception
from .config import settings

logger = logging.getLogger(__name__)

# Категории v2 (§7 Фаза 2).
REGRESSION = "regression"
FIX = "fix"
EXPECTED_EXPANSION = "expected_expansion"
EXPECTED_MODEL_CHANGE = "expected_model_change"

_CATEGORIES = (
    REGRESSION,
    FIX,
    EXPECTED_EXPANSION,
    EXPECTED_MODEL_CHANGE,
)

# Старые presence-уровни, означающие «событие видно наблюдателю».
_OLD_PRESENT = frozenset({"present", "mentioned", "audible"})

# Известные под-категории (И13-комбо + системные). Всё, что выходит за
# этот набор, считается «необъяснимым расхождением» (нарушение критерия выхода).
_KNOWN_SUBLABELS = frozenset(
    {
        "OLD_PRESENT_NEW_ABSENT",  # regression
        "OLD_ABSENT_NEW_PRESENT",  # fix
        "REMOTE_DELIVERED",        # expected_model_change
        "ADDRESSED_DELIVERED",     # expected_model_change
        "GLASS",                   # стекло: visual=full, audio=none
        "SHOUT_THROUGH_WALL",      # крик из-за стены: audio=full, visual=none (не одна локация)
        "INVISIBLE",               # невидимость: audio=full, visual=none (одна локация)
        "WALL",                    # стена: audio=muffled, visual=none
        "ADDRESSED_PARTIAL",       # адресация при частичном канале
        "PARTIAL",                 # прочее частичное восприятие
    }
)

# Shadow-метрики критерия выхода (§10). Чисто наблюдательные: никак не
# влияют на контекст. Сброс/чтение — как в Фазе 2 (`WPE_TOOLS_STATS`).
WPE_SHADOW_STATS: dict[str, Any] = {
    "events": 0,
    "observers": 0,
    "matched": 0,
    "diverged": 0,
    "by_category": {c: 0 for c in _CATEGORIES},
    "by_sublabel": {},
    "unexplained": 0,
}


def wpe_shadow_stats_snapshot() -> dict[str, Any]:
    """Снимок shadow-метрик (для отчёта критерия выхода §10)."""
    return copy.deepcopy(WPE_SHADOW_STATS)


def _result_attr(result: Any, name: str, default: Any = None) -> Any:
    """Duck-typing чтение поля PerceptionResult (dict или ORM-подобный)."""
    if isinstance(result, dict):
        return result.get(name, default)
    return getattr(result, name, default)


def _partial_sublabel(result: Any, same_location: bool) -> str:
    visual = _result_attr(result, "visual_level", "none")
    audio = _result_attr(result, "audio_level", "none")
    addressed = bool(_result_attr(result, "addressed", False))
    if visual == "full" and audio == "none":
        return "GLASS"
    if visual == "none" and audio == "full":
        return "INVISIBLE" if same_location else "SHOUT_THROUGH_WALL"
    if visual == "none" and audio == "muffled":
        return "WALL"
    if addressed:
        return "ADDRESSED_PARTIAL"
    return "PARTIAL"


def classify_shadow_discrepancy(
    *,
    old_presence: str,
    result: Any,
    same_location: bool = False,
) -> tuple[str, str] | None:
    """Classify old vs new perception for one observer. ``None`` — совпало.

    Возвращает ``(category, sublabel)`` по сетке v2 (§7 Фаза 2):
    (old=present, new=absent) → regression; (old=absent, new=present) → fix;
    (old=absent, new=partial) → expected_expansion; частичное восприятие при
    старом present / доставка по каналу → expected_model_change (И13).
    """
    old_present = old_presence in _OLD_PRESENT
    visual = _result_attr(result, "visual_level", "none")
    audio = _result_attr(result, "audio_level", "none")
    remote = _result_attr(result, "remote_status", "none")
    addressed = bool(_result_attr(result, "addressed", False))

    new_full = visual == "full" and audio == "full"
    new_absent = visual == "none" and audio == "none"

    if old_present and new_absent:
        return (REGRESSION, "OLD_PRESENT_NEW_ABSENT")
    if not old_present and new_full:
        if remote == "delivered":
            return (EXPECTED_MODEL_CHANGE, "REMOTE_DELIVERED")
        if addressed:
            return (EXPECTED_MODEL_CHANGE, "ADDRESSED_DELIVERED")
        return (FIX, "OLD_ABSENT_NEW_PRESENT")
    if not new_full and not new_absent:
        sublabel = _partial_sublabel(result, same_location)
        category = EXPECTED_MODEL_CHANGE if old_present else EXPECTED_EXPANSION
        return (category, sublabel)
    return None


def _record(category: str, sublabel: str) -> None:
    WPE_SHADOW_STATS["diverged"] += 1
    WPE_SHADOW_STATS["by_category"][category] = (
        WPE_SHADOW_STATS["by_category"].get(category, 0) + 1
    )
    by_sublabel = WPE_SHADOW_STATS["by_sublabel"]
    by_sublabel[sublabel] = by_sublabel.get(sublabel, 0) + 1
    if sublabel not in _KNOWN_SUBLABELS:
        WPE_SHADOW_STATS["unexplained"] += 1


def _same_location(event: Mapping[str, Any], observer: Mapping[str, Any]) -> bool:
    """«Одна локация» для И13-подкатегорий (зеркалит perceive: общая сцена + каноника)."""
    event_loc = event.get("location") or ""
    observer_loc = observer.get("location") or ""
    if perception.is_shared_scene(event_loc) or perception.is_shared_scene(observer_loc):
        return True
    return perception.same_canonical_location(
        event_location=event_loc,
        observer_location=observer_loc,
        event_location_id=event.get("location_id"),
        observer_location_id=observer.get("location_id"),
    )


async def run_shadow_perception(
    db: Any,
    message: Any,
    characters: list[Any] | None = None,
    character_names: Mapping[int, str] | None = None,
) -> None:
    """Shadow-прогон двухканального ``perceive()`` по одному событию (Фаза 3).

    Чисто наблюдательный: логирует расхождения, обновляет ``WPE_SHADOW_STATS``,
    в сборку контекста ничего не подаёт. Ошибки shadow не должны ломать
    сохранение сообщения — вызов обёрнут в try/except на стороне вызывающего
    (``maybe_run_shadow_perception``).
    """
    if not settings.world_engine_events_enabled:
        return

    chat_id = getattr(message, "chat_id", None)
    if chat_id is None:
        return

    event = perception.event_from_message(message)
    chars = list(characters) if characters is not None else await crud.get_characters_by_chat(db, chat_id)
    names = (
        dict(character_names)
        if character_names is not None
        else {c.id: getattr(c, "name", "") or "" for c in chars}
    )

    locations = await crud.get_chat_locations(db, chat_id)
    adjacency = perception.build_adjacency_index(locations)
    world_state = perception.PerceptionWorldState(
        adjacency=perception.build_permeability_index(locations)
    )

    WPE_SHADOW_STATS["events"] += 1
    for character in chars:
        cid = character.id
        observer_location = getattr(character, "location", "") or ""
        observer = {"character_id": cid, "location": observer_location}

        try:
            old_presence, old_reason = perception.can_character_perceive_event(
                viewer_character_id=cid,
                viewer_location=observer_location,
                event=event,
                viewer_name=names.get(cid, "") or "",
                adjacency_index=adjacency,
            )
        except Exception:  # shadow не должен падать из-за legacy-кода
            old_presence, old_reason = "absent", "SHADOW_OLD_ERROR"

        new_result = perception.perceive(
            world_state=world_state,
            event=event,
            observer=observer,
        )

        WPE_SHADOW_STATS["observers"] += 1
        classification = classify_shadow_discrepancy(
            old_presence=old_presence,
            result=new_result,
            same_location=_same_location(event, observer),
        )
        if classification is None:
            WPE_SHADOW_STATS["matched"] += 1
            continue

        category, sublabel = classification
        _record(category, sublabel)
        logger.info(
            "[WPE-P3] shadow divergence chat=%s msg=%s char=%s(id=%s) "
            "old=%s/%s new=%s/%s remote=%s addressed=%s cat=%s sub=%s",
            chat_id,
            event.get("id"),
            names.get(cid, cid),
            cid,
            old_presence,
            old_reason,
            new_result.visual_level,
            new_result.audio_level,
            new_result.remote_status,
            new_result.addressed,
            category,
            sublabel,
        )


async def maybe_run_shadow_perception(db: Any, message: Any) -> None:
    """Shadow-триггер для сервисного слоя (Sprint 1, §7.1 decomposition.md).

    Перенесён из ``crud.create_message``: crud больше не вызывает shadow
    (однонаправленная зависимость сервис → crud). Флаг-гард и try/except
    сохранены 1:1 — shadow не должен ломать сохранение сообщения.
    """
    if not settings.world_engine_events_enabled:
        return
    try:
        await run_shadow_perception(db, message)
    except Exception:
        logger.exception(
            "[WPE-P3] shadow perception failed chat_id=%s msg_id=%s",
            getattr(message, "chat_id", None),
            getattr(message, "id", None),
        )
