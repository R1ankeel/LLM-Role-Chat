"""Deterministic emotion engine (Plans/update20.md §8, Sprint 3).

Чистый модуль БЕЗ зависимостей от БД/LLM: обновление эмоций/настроения/стресса
персонажа из relationship deltas и событий раунда по фиксированным правилам.

Правила (детерминированные, не LLM-фантазия):
- trust↓ → suspicion / hurt;  trust↑ → relief / warmth;
- resentment↑ → resentment (обида);
- jealousy↑ → tension / suspicion;
- affection↑ → warmth;        affection↓ → hurt;
- attraction↑ → warmth;       attraction↓ → hurt.
- Стресс: эмоциональная салиенсность событий раунда + негативные дельты;
  базовый уровень 0.1, мягкое затухание к нему.
- Настроение (mood) ВСЕГДА выводится из emotional_state + stress —
  Sensors/LLM не задаёт mood напрямую (§5.1.3).
- Sensors-предложение `{emotion, intensity, confidence, mood_delta}` применяется
  ТОЛЬКО в рамках caps (`SENSORS_EMOTION_INTENSITY_CAP` за раунд, эмоция
  клампится в [0,1]); `mood_delta` лишь слегка двигает стресс (Sensors не
  задаёт настроение).

Масштабы:
- relationship deltas — как в движке отношений, диапазон [-20, +20];
- интенсивности эмоций — [0, 1];
- стресс — [0, 1].
"""

from __future__ import annotations

from typing import Any

# Стандартный вокабуляр эмоций (ключи `emotional_state`).
EMOTIONS: tuple[str, ...] = (
    "warmth",       # теплота (affection/attraction ↑)
    "relief",       # облегчение (trust ↑)
    "hope",         # надежда (trust ↑ / attraction ↑)
    "suspicion",    # подозрение (trust ↓ / jealousy ↑)
    "tension",      # напряжение (jealousy ↑ / стресс)
    "resentment",   # обида (resentment ↑)
    "hurt",         # боль (affection ↓ / trust ↓)
    "fear",         # страх (стресс высокий + угроза)
)

EMOTION_MIN = 0.0
EMOTION_MAX = 1.0
STRESS_MIN = 0.0
STRESS_MAX = 1.0

# Сколько интенсивности эмоции может добавиться за один раунд (кап).
EMOTION_ROUND_CAP = 0.4
# Сколько стресса может добавиться за один раунд.
STRESS_ROUND_CAP = 0.2
# Затухание старых эмоций за раунд (доля).
EMOTION_DECAY = 0.10
# Стресс мягко возвращается к базовому уровню.
STRESS_BASELINE = 0.10
STRESS_DECAY = 0.05
# Sensors-предложение не может сдвинуть эмоцию сильнее этого порога за раунд.
SENSOR_EMOTION_INTENSITY_CAP = 0.3
# Пороги для вывода mood из состояния.
MOOD_STRESS_HIGH = 0.70
MOOD_STRESS_MED = 0.45
MOOD_DOMINANT_THRESHOLD = 0.50
# Интенсивность ниже порога не рендерится в блок YOUR STATE.
RENDER_INTENSITY_THRESHOLD = 0.10

# Эмоция, которая активируется ростом метрики (0..1 множитель после нормировки).
_RULES: tuple[tuple[str, str, float], ...] = (
    # (metric, emotion, weight) — рост метрики усиливает эмоцию
    ("affection", "warmth", 0.25),
    ("affection", "hope", 0.10),
    ("attraction", "warmth", 0.10),
    ("attraction", "hope", 0.05),
    ("trust", "relief", 0.25),
    ("trust", "hope", 0.10),
    # рост негативных метрик
    ("resentment", "resentment", 0.30),
    ("jealousy", "tension", 0.30),
    ("jealousy", "suspicion", 0.10),
)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def clamp01(value: float) -> float:
    """Clamp 0..1 (принимает числа; NaN/нечисло → 0.0)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    if f != f:  # NaN
        return 0.0
    return _clamp(f, 0.0, 1.0)


def _norm_delta(delta: float) -> float:
    """Нормировка relationship delta [-20,20] → [0,1]."""
    try:
        d = float(delta)
    except (TypeError, ValueError):
        return 0.0
    return _clamp(abs(d) / 20.0, 0.0, 1.0)


def _norm_event_salience(salience: float) -> float:
    """Нормировка emotional_salience события [0,1] с гарантией валидности."""
    return clamp01(salience)


# ---------------------------------------------------------------------------
# Базовые операции над emotional_state
# ---------------------------------------------------------------------------

def normalize_emotional_state(raw: Any) -> dict[str, float]:
    """Привести JSON emotional_state к dict {emotion: intensity 0..1}.

    Неизвестные/невалидные ключи отбрасываются, значения клампятся.
    """
    out: dict[str, float] = {}
    if not isinstance(raw, dict):
        return out
    for key, value in raw.items():
        if not isinstance(key, str) or key not in EMOTIONS:
            continue
        out[key] = clamp01(value)
    return out


def decay_emotional_state(emotional_state: dict[str, float]) -> dict[str, float]:
    """Старые эмоции затухают (EMOTION_DECAY за раунд)."""
    out: dict[str, float] = {}
    for key, value in emotional_state.items():
        next_value = value * (1.0 - EMOTION_DECAY)
        if next_value > RENDER_INTENSITY_THRESHOLD:
            out[key] = next_value
    return out


def _add_emotion(emotional_state: dict[str, float], emotion: str, delta: float) -> None:
    if delta <= 0 or emotion not in EMOTIONS:
        return
    emotional_state[emotion] = clamp01(emotional_state.get(emotion, 0.0) + delta)


# ---------------------------------------------------------------------------
# Relationship deltas → эмоции
# ---------------------------------------------------------------------------

def relationship_emotion_deltas(
    deltas: list[dict[str, Any]],
    *,
    round_cap: float = EMOTION_ROUND_CAP,
) -> dict[str, float]:
    """Агрегировать relationship deltas раунда в прирост эмоций (0..round_cap).

    ``deltas`` — список записей с метриками: ``delta_affection``,
    ``delta_trust``, ``delta_attraction``, ``delta_resentment``,
    ``delta_jealousy`` (диапазон [-20,20]). Возвращает dict emotion → +intensity
    (положительные приросты, уже с учётом капа на одну эмоцию).
    """
    acc: dict[str, float] = {}
    for raw in deltas or []:
        if not isinstance(raw, dict):
            continue
        # позитивный прирост эмоций от роста метрик
        for metric, emotion, weight in _RULES:
            value = raw.get(metric) or raw.get(f"delta_{metric}") or 0.0
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            if value > 0:
                acc[emotion] = acc.get(emotion, 0.0) + _norm_delta(value) * weight
        # негативные движения метрик
        trust = _norm_delta(raw.get("delta_trust") or raw.get("trust") or 0.0)
        if float(raw.get("delta_trust") or 0.0) < 0:
            acc["suspicion"] = acc.get("suspicion", 0.0) + trust * 0.30
            acc["hurt"] = acc.get("hurt", 0.0) + trust * 0.10
        affection = _norm_delta(raw.get("delta_affection") or raw.get("affection") or 0.0)
        if float(raw.get("delta_affection") or 0.0) < 0:
            acc["hurt"] = acc.get("hurt", 0.0) + affection * 0.25
        attraction = _norm_delta(raw.get("delta_attraction") or raw.get("attraction") or 0.0)
        if float(raw.get("delta_attraction") or 0.0) < 0:
            acc["hurt"] = acc.get("hurt", 0.0) + attraction * 0.10

    capped: dict[str, float] = {}
    for emotion, value in acc.items():
        capped[emotion] = _clamp(value, 0.0, round_cap)
    return capped


# ---------------------------------------------------------------------------
# Стресс
# ---------------------------------------------------------------------------

def stress_delta(
    round_events: list[dict[str, Any]],
    deltas: list[dict[str, Any]],
    *,
    round_cap: float = STRESS_ROUND_CAP,
) -> float:
    """Прирост стресса за раунд из событий (emotional_salience) + негативных дельт.

    Отрицательные движения trust/affection и рост jealousy/resentment поднимают
    стресс; эмоционально заряженные события — пропорционально салиенсу.
    """
    stress = 0.0
    for event in round_events or []:
        if not isinstance(event, dict):
            continue
        salience = _norm_event_salience(event.get("emotional_salience", 0.0))
        importance = 0.0
        try:
            importance = float(event.get("importance") or 0.0)
        except (TypeError, ValueError):
            importance = 0.0
        # события с emotional_salience > 0.5 считаются эмоционально заряженными
        if salience > 0.5:
            stress += salience * 0.10 + _clamp(importance / 10.0, 0.0, 0.05)
    for raw in deltas or []:
        if not isinstance(raw, dict):
            continue
        if float(raw.get("delta_trust") or 0.0) < 0:
            stress += _norm_delta(raw["delta_trust"]) * 0.06
        if float(raw.get("delta_affection") or 0.0) < 0:
            stress += _norm_delta(raw["delta_affection"]) * 0.06
        if float(raw.get("delta_resentment") or 0.0) > 0:
            stress += _norm_delta(raw["delta_resentment"]) * 0.04
        if float(raw.get("delta_jealousy") or 0.0) > 0:
            stress += _norm_delta(raw["delta_jealousy"]) * 0.06
    return _clamp(stress, 0.0, round_cap)


def decay_stress(stress: float | None) -> float:
    """Стресс мягко возвращается к STRESS_BASELINE."""
    if stress is None:
        return STRESS_BASELINE
    base = clamp01(stress)
    return clamp01(base + (STRESS_BASELINE - base) * STRESS_DECAY)


# ---------------------------------------------------------------------------
# Настроение (всегда детерминированно выводится из состояния)
# ---------------------------------------------------------------------------

def derive_mood(
    emotional_state: dict[str, float],
    stress: float | None,
    *,
    high_threshold: float = MOOD_STRESS_HIGH,
    med_threshold: float = MOOD_STRESS_MED,
) -> str:
    """Детерминированный label настроения из эмоций + стресса.

    Строка из вокабуляра: neutral/tense/hopeful/warm/wary/resentful/hurt/panicked.
    """
    stress_value = clamp01(stress if stress is not None else 0.0)
    if stress_value >= high_threshold:
        return "panicked"
    if stress_value >= med_threshold:
        return "tense"

    dominant: tuple[float, str] = (0.0, "neutral")
    for emotion in EMOTIONS:
        intensity = emotional_state.get(emotion, 0.0)
        if intensity > dominant[0]:
            dominant = (intensity, emotion)
    _, emotion = dominant
    if emotion == "neutral" or dominant[0] < MOOD_DOMINANT_THRESHOLD:
        # нет доминирующей эмоции → спокойное/настороженное по уровню стресса
        return "neutral" if stress_value < 0.25 else "tense"

    _MOOD_BY_EMOTION = {
        "warmth": "warm",
        "relief": "hopeful",
        "hope": "hopeful",
        "suspicion": "wary",
        "tension": "tense",
        "resentment": "resentful",
        "hurt": "hurt",
        "fear": "panicked",
    }
    return _MOOD_BY_EMOTION.get(emotion, "neutral")


# ---------------------------------------------------------------------------
# Sensors-предложение (только в рамках caps)
# ---------------------------------------------------------------------------

def apply_sensors_proposal(
    emotional_state: dict[str, float],
    proposal: dict[str, Any] | None,
    *,
    intensity_cap: float = SENSOR_EMOTION_INTENSITY_CAP,
) -> dict[str, float]:
    """Применить Sensors-предложение `{emotion, intensity, confidence}` в caps.

    Sensors НЕ задаёт эмоцию напрямую и НЕ задаёт mood (mood всегда выводит
    движок). Предложение сдвигает текущую интенсивность к предложенной в
    пределах ``intensity_cap`` × confidence; невалидное/пустое предложение —
    no-op.
    """
    if not isinstance(proposal, dict):
        return dict(emotional_state)
    emotion = (proposal.get("emotion") or "").strip()
    if emotion not in EMOTIONS:
        return dict(emotional_state)
    try:
        target = clamp01(float(proposal.get("intensity") or 0.0))
        confidence = clamp01(float(proposal.get("confidence") or 0.0))
    except (TypeError, ValueError):
        return dict(emotional_state)

    current = emotional_state.get(emotion, 0.0)
    shift = (target - current) * confidence
    shift = _clamp(shift, -intensity_cap, intensity_cap)
    if abs(shift) < 1e-6:
        return dict(emotional_state)
    out = dict(emotional_state)
    next_value = clamp01(current + shift)
    if next_value > RENDER_INTENSITY_THRESHOLD:
        out[emotion] = next_value
    else:
        out.pop(emotion, None)
    return out


# ---------------------------------------------------------------------------
# Полный детерминированный update
# ---------------------------------------------------------------------------

def compute_state_update(
    *,
    emotional_state: dict[str, float] | None,
    stress: float | None,
    mood: str | None = None,
    relationship_deltas: list[dict[str, Any]] | None = None,
    round_events: list[dict[str, Any]] | None = None,
    sensors_proposal: dict[str, Any] | None = None,
    emotion_round_cap: float = EMOTION_ROUND_CAP,
    stress_round_cap: float = STRESS_ROUND_CAP,
    sensors_intensity_cap: float = SENSOR_EMOTION_INTENSITY_CAP,
) -> dict[str, Any]:
    """Вычислить новое состояние персонажа за один раунд (чистая функция).

    Возвращает ``{emotional_state, mood, stress}``. Детерминированно: decay
    старых эмоций → прирост из relationship deltas → Sensors-нормализация в
    caps → стресс (decay к baseline + прирост из событий/дельт) → mood.
    """
    current_emotions = normalize_emotional_state(emotional_state)
    current_stress = clamp01(stress if stress is not None else STRESS_BASELINE)

    # 1. затухание старых эмоций
    new_emotions = decay_emotional_state(current_emotions)
    # 2. прирост из relationship deltas
    for emotion, delta in relationship_emotion_deltas(
        relationship_deltas or [], round_cap=emotion_round_cap
    ).items():
        _add_emotion(new_emotions, emotion, delta)
    # 3. Sensors-нормализация в рамках caps (если предложение пришло)
    if sensors_proposal:
        new_emotions = apply_sensors_proposal(
            new_emotions, sensors_proposal, intensity_cap=sensors_intensity_cap
        )
    # 4. стресс: затухание к baseline + прирост раунда
    new_stress = decay_stress(current_stress)
    new_stress = clamp01(
        new_stress + stress_delta(round_events or [], relationship_deltas or [],
                                  round_cap=stress_round_cap)
    )
    # 5. mood — всегда выводится движком (Sensors mood напрямую не задаёт)
    new_mood = derive_mood(new_emotions, new_stress)
    return {
        "emotional_state": new_emotions,
        "mood": new_mood,
        "stress": new_stress,
    }
