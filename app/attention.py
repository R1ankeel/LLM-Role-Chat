"""Attention layer (Plans/update20.md §11, Sprint 4).

«Воспринято ≠ вошло в сознание». Детерминированная оценка внимания для пары
(персонаж, событие): attention score в ``[0, 1]``, который управляет только
тем, что идёт в память/реакцию, но НЕ меняет presence-лестницу (что видно в
recent history — по-прежнему решает ``perceive()``/presence).

Score (§11):

```text
attention = w_volume  × громкость(стимулы)
          + w_distance × same > adjacent > remote
          + w_relevance × важность события
          + w_personal  × упоминание имени/обращение
          + w_emotional × эмоциональный якорь активен
          + w_novelty   × новое vs повтор
          + w_relationship × в событии участвует target отношения
          + w_address   × addressed=true
```

Пороги:
- ``attention < ATTENTION_LOW`` — «слышал фоном»: в память НЕ идёт, в реакцию
  НЕ идёт (рендерится как атмосфера, если вообще);
- ``ATTENTION_LOW <= attention < ATTENTION_HIGH`` — «заметил»: в память
  (с пониженной важностью), в реакцию — опционально;
- ``attention >= ATTENTION_HIGH`` — «в центре внимания»: в память, в recency tail.

Модуль чистый: без БД и LLM. Sensors perception-proposal (§5.1.3) может только
поднять score в рамках ``SENSORS_PERCEPTION_SIGNIFICANCE_CAP``
(``apply_sensors_significance``) — Sensors не определяет окончательный набор
информации и не принимает решение о внимании.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from .config import settings
from .perception import LOUD_STIMULUS_TYPES, parse_target_ids
from .stimuli import parse_stimuli

# Бакеты внимания (для фильтров и observability).
LOW = "low"
MEDIUM = "medium"
HIGH = "high"

# Presence → расстояние (same > adjacent > remote), компонента w_distance.
# «told» — явно рассказали, почти как co-presence; «mentioned»/«audible» —
# соседство (стена/крик); «absent» — вне внимания.
_PRESENCE_DISTANCE: dict[str, float] = {
    "present": 1.0,
    "told": 0.85,
    "mentioned": 0.7,
    "audible": 0.6,
    "absent": 0.0,
}


def _get_attr(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _name_mentioned(content: str, name: str) -> bool:
    """Детерминированное «имя встречается в тексте» (личная значимость)."""
    if not name or not content:
        return False
    pattern = rf"(?<!\w){re.escape(name)}(?!\w)"
    return bool(re.search(pattern, content, flags=re.IGNORECASE))


def _volume_factor(stimuli: Any, audio_level: str = "full") -> float:
    """Громкость события: громкий стимул → 1.0; muffled-шум → 0.2; иначе 0.4."""
    loud = any(s.type in LOUD_STIMULUS_TYPES for s in parse_stimuli(stimuli))
    if loud:
        return 1.0
    if audio_level == "muffled":
        return 0.2
    return 0.4


def _distance_factor(presence: str) -> float:
    return _PRESENCE_DISTANCE.get(presence, 0.0)


def _relevance_factor(
    event: Mapping[str, Any],
    observer_id: Any,
    *,
    sensors_significance: float | None = None,
) -> float:
    """Важность события для персонажа.

    Своя речь / игрок — важнее (это драйвер раунда); обычное событие персонажа
    — среднее; система — фон. Sensors ``significance`` (0..1) поднимает
    relevance до своего значения (только вверх), если он выше детерминированной
    оценки — подсказка §5.1.3, а не решение о доступности.
    """
    role = str(event.get("role") or "").strip().lower()
    author_id = event.get("character_id")
    try:
        is_own = author_id is not None and int(author_id) == int(observer_id)
    except (TypeError, ValueError):
        is_own = False
    if is_own:
        base = 1.0
    elif role == "user":
        base = 0.8
    elif role == "system":
        base = 0.4
    else:
        base = 0.5
    if sensors_significance is not None:
        try:
            sig = max(0.0, min(1.0, float(sensors_significance)))
        except (TypeError, ValueError):
            sig = 0.0
        base = max(base, sig)
    return base


def _personal_salience(content: str, viewer_name: str) -> float:
    return 1.0 if _name_mentioned(content, viewer_name) else 0.0


def _relationship_relevance(
    event: Mapping[str, Any],
    observer_id: Any,
    relationship_target_ids: set[int] | None,
) -> float:
    """В событии участвует target отношения наблюдателя (автор или адресат)."""
    if not relationship_target_ids:
        return 0.0
    author_id = event.get("character_id")
    candidates: set[int] = set()
    if author_id is not None:
        try:
            candidates.add(int(author_id))
        except (TypeError, ValueError):
            pass
    try:
        targets = parse_target_ids(event.get("target_character_ids"))
    except (TypeError, ValueError):
        targets = []
    candidates.update(int(t) for t in targets)
    try:
        candidates.discard(int(observer_id))
    except (TypeError, ValueError):
        pass
    return 1.0 if candidates & relationship_target_ids else 0.0


def attention_bucket(score: float | None) -> str:
    """Бакет внимания по score. ``None`` (attention off / нет данных) → HIGH:
    при отключённом attention всё воспринятое ведёт себя как раньше (legacy)."""
    if score is None:
        return HIGH
    if score < settings.attention_low:
        return LOW
    if score < settings.attention_high:
        return MEDIUM
    return HIGH


def attention_weights() -> dict[str, float]:
    """Веса компонентов (§11) из настроек (сумма нормируется на 1.0)."""
    raw = {
        "volume": settings.attention_weight_volume,
        "distance": settings.attention_weight_distance,
        "relevance": settings.attention_weight_relevance,
        "personal": settings.attention_weight_personal,
        "emotional": settings.attention_weight_emotional,
        "novelty": settings.attention_weight_novelty,
        "relationship": settings.attention_weight_relationship,
        "address": settings.attention_weight_address,
    }
    total = sum(max(0.0, float(v)) for v in raw.values())
    if total <= 0.0:
        return {k: 1.0 / len(raw) for k in raw}
    return {k: max(0.0, float(v)) / total for k, v in raw.items()}


def compute_attention_score(
    *,
    presence: str,
    event: Mapping[str, Any],
    observer: Mapping[str, Any],
    character_names: Mapping[int, str] | None = None,
    relationship_target_ids: set[int] | None = None,
    anchor_active: bool = False,
    novelty: float | None = None,
    sensors_significance: float | None = None,
) -> float:
    """Детерминированный attention score пары (персонаж, событие).

    Входы (все из существующей perception-логики, без новых данных):
    - ``presence`` — legacy-лестница (present/mentioned/audible/absent/told);
    - ``event`` — ``location/stimuli/target_character_ids/character_id/role/content``
      (см. ``perception.event_from_message``);
    - ``observer`` — ``character_id/name``;
    - ``character_names`` — имя наблюдателя для детекта упоминания имени;
    - ``relationship_target_ids`` — targets отношений наблюдателя
      (компонента w_relationship);
    - ``anchor_active`` — активен ли эмоциональный якорь по автору события;
    - ``novelty`` — 1.0 новое / 0.0 повтор (по умолчанию 1.0);
    - ``sensors_significance`` — подсказка Sensors §5.1.3 (relevance вверх).

    Собственная речь всегда в центре внимания.
    """
    observer_id = observer.get("character_id")
    observer_name = (
        observer.get("name")
        or (character_names or {}).get(
            _as_int(observer_id), ""
        )
    )

    if _is_own_event(event, observer_id):
        return 1.0

    viewer_name = str(observer_name or "")
    content = str(event.get("content") or "")
    audio_level = str(event.get("audio_level") or "full")

    w = attention_weights()
    components = {
        "volume": _volume_factor(event.get("stimuli"), audio_level),
        "distance": _distance_factor(presence),
        "relevance": _relevance_factor(
            event, observer_id, sensors_significance=sensors_significance
        ),
        "personal": _personal_salience(content, viewer_name),
        "emotional": 1.0 if anchor_active else 0.0,
        "novelty": 1.0 if novelty is None else max(0.0, min(1.0, float(novelty))),
        "relationship": _relationship_relevance(
            event, observer_id, relationship_target_ids
        ),
        "address": 1.0 if _is_addressed(event, observer_id) else 0.0,
    }
    return max(0.0, min(1.0, sum(w[k] * components[k] for k in components)))


def apply_sensors_significance(
    score: float,
    significance: float | None,
    *,
    cap: float | None = None,
) -> float:
    """Применить Sensors perception-подсказку §5.1.3 к attention score.

    Sensors может поднять score не более чем на ``SENSORS_PERCEPTION_SIGNIFICANCE_CAP``
    × significance — это только подсказка; решение о внимании (пороги) и о
    доступности информации (presence) остаётся за движком.
    """
    if significance is None:
        return score
    try:
        sig = max(0.0, min(1.0, float(significance)))
    except (TypeError, ValueError):
        return score
    cap_val = settings.sensors_perception_significance_cap if cap is None else cap
    return max(0.0, min(1.0, float(score) + max(0.0, float(cap_val)) * sig))


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_own_event(event: Mapping[str, Any], observer_id: Any) -> bool:
    author_id = event.get("character_id")
    if author_id is None or observer_id is None:
        return False
    try:
        return int(author_id) == int(observer_id)
    except (TypeError, ValueError):
        return False


def _is_addressed(event: Mapping[str, Any], observer_id: Any) -> bool:
    if observer_id is None:
        return False
    targets = parse_target_ids(event.get("target_character_ids"))
    try:
        return int(observer_id) in targets
    except (TypeError, ValueError):
        return False
