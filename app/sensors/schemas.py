"""Sensors Model — JSON-схемы аналитических задач (Plans/update20.md §5.1).

Каждый тип анализа имеет собственную JSON-схему. Схемы используются двумя
способами:

- как ``format`` при вызове Ollama (Ollama native JSON-schema);
- локально для валидации результата перед передачей движку
  (``validate_sensor_result`` — лёгкий валидатор без внешних зависимостей).

Sensors возвращает **предложения**; итоговое изменение состояния всегда
выполняет движок по своим правилам (§5.1.4). Слой не пишет в БД.
"""

from __future__ import annotations

from typing import Any

# ----------------------------- JSON-схемы задач -----------------------------

# Perception: что персонаж *потенциально* может воспринять. Окончательный
# набор доступной информации решает `perceive()`/presence-лестница (движок).
PERCEPTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["potential_visual", "potential_audio", "addressed", "notice", "significance"],
    "properties": {
        "potential_visual": {"type": "boolean"},
        "potential_audio": {"type": "boolean"},
        "addressed": {"type": "boolean"},
        "notice": {"type": "string"},
        "significance": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "additionalProperties": False,
}

# Event classification: тип события, участники, значимость, слышимость/видимость.
# Движок решает важность/салиенс и запись события.
EVENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["event_type", "source_character", "targets", "importance", "audibility", "visibility"],
    "properties": {
        "event_type": {"type": "string"},
        "source_character": {"type": ["string", "null"]},
        "targets": {"type": "array", "items": {"type": "string"}},
        "importance": {"type": "number", "minimum": 0, "maximum": 10},
        "audibility": {"type": "string", "enum": ["none", "muffled", "full"]},
        "visibility": {"type": "string", "enum": ["none", "partial", "full"]},
        "requires_processing": {"type": "boolean"},
    },
    "additionalProperties": False,
}

# Emotion / Mood: предложение эмоции. Движок применяет в рамках caps — Sensors
# НЕ задаёт mood напрямую.
EMOTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["emotion", "intensity", "confidence"],
    "properties": {
        "emotion": {"type": "string"},
        "intensity": {"type": "number", "minimum": 0, "maximum": 1},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "mood_delta": {"type": "number", "minimum": -1, "maximum": 1},
    },
    "additionalProperties": False,
}

# Memory extraction: кандидаты. Движок прогоняет их через существующую валидацию
# (`validate_extracted_facts`), witness-фильтр и лимиты перед записью.
MEMORY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["facts"],
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["text", "importance"],
                "properties": {
                    "text": {"type": "string"},
                    "importance": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "additionalProperties": False,
            },
        }
    },
    "additionalProperties": False,
}

# Relationship analysis: дельты для пары source→target. Применяются только через
# существующую систему правил (evidence gating, caps, decay, normalization).
RELATIONSHIP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["affection_delta", "trust_delta", "resentment_delta", "jealousy_delta", "attraction_delta"],
    "properties": {
        "affection_delta": {"type": "integer", "minimum": -20, "maximum": 20},
        "trust_delta": {"type": "integer", "minimum": -20, "maximum": 20},
        "resentment_delta": {"type": "integer", "minimum": -20, "maximum": 20},
        "jealousy_delta": {"type": "integer", "minimum": -20, "maximum": 20},
        "attraction_delta": {"type": "integer", "minimum": -20, "maximum": 20},
    },
    "additionalProperties": False,
}

SENSOR_SCHEMAS: dict[str, dict[str, Any]] = {
    "perception": PERCEPTION_SCHEMA,
    "event": EVENT_SCHEMA,
    "emotion": EMOTION_SCHEMA,
    "memory": MEMORY_SCHEMA,
    "relationship": RELATIONSHIP_SCHEMA,
}


def get_schema(task: str) -> dict[str, Any] | None:
    """Вернуть JSON-схему задачи (None для неизвестной задачи)."""
    return SENSOR_SCHEMAS.get(task)


# ----------------------------- Лёгкий валидатор -----------------------------

def _check_type(value: Any, type_spec: Any) -> bool:
    """Проверка типа по JSON-schema type (строка или список строк)."""
    allowed = [type_spec] if isinstance(type_spec, str) else list(type_spec)
    for t in allowed:
        if t == "string" and isinstance(value, str):
            return True
        if t == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        if t == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if t == "boolean" and isinstance(value, bool):
            return True
        if t == "array" and isinstance(value, list):
            return True
        if t == "null" and value is None:
            return True
    return False


def _check_properties(value: Any, props: dict[str, Any]) -> bool:
    """Рекурсивная проверка свойств по JSON-schema keywords (подмножество)."""
    if not isinstance(value, dict):
        return False
    for prop_name, prop_schema in props.items():
        if prop_name not in value:
            # required проверяется отдельно; отсутствующее необязательное — ок
            continue
        prop_value = value[prop_name]
        if not _check_type(prop_value, prop_schema.get("type", "string")):
            return False
        if isinstance(prop_value, (int, float)) and not isinstance(prop_value, bool):
            minimum = prop_schema.get("minimum")
            maximum = prop_schema.get("maximum")
            if minimum is not None and prop_value < minimum:
                return False
            if maximum is not None and prop_value > maximum:
                return False
        enum = prop_schema.get("enum")
        if enum is not None and prop_value not in enum:
            return False
        # вложенные объекты
        if isinstance(prop_value, dict) and "properties" in prop_schema:
            if not _check_properties(prop_value, prop_schema["properties"]):
                return False
        # элементы массива
        if isinstance(prop_value, list) and "items" in prop_schema:
            items = prop_schema["items"]
            if "properties" in items:
                for item in prop_value:
                    if not _check_properties(item, items["properties"]):
                        return False
    return True


def validate_sensor_result(result: Any, schema: dict[str, Any]) -> dict[str, Any] | None:
    """Валидировать результат Sensors по JSON-схеме.

    Возвращает ``result`` (dict) при успехе, иначе None — результат
    отбрасывается, движок использует детерминированный путь (§5.1.8).
    """
    if not isinstance(result, dict) or schema is None:
        return None
    if schema.get("type", "object") != "object":
        return None
    required = schema.get("required", [])
    for key in required:
        if key not in result:
            return None
        if result[key] is None:
            # null допустим только если схема явно разрешает тип null
            prop = schema.get("properties", {}).get(key, {})
            allowed = prop.get("type", "string")
            types = [allowed] if isinstance(allowed, str) else list(allowed)
            if "null" not in types:
                return None
    if not _check_properties(result, schema.get("properties", {})):
        return None
    additional_properties = schema.get("additionalProperties", True)
    if additional_properties is False:
        allowed = set(schema.get("properties", {}).keys())
        if set(result.keys()) - allowed:
            return None
    return result
