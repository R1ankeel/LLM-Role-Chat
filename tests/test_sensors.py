"""Sensors Model — инфраструктура (Plans/update20.md §5.1, Sprint 0).

Покрывает §5.1.10:
- ``SENSORS_MODEL`` читается из ``.env`` (пустая → Sensors выключен);
- Sensors использует именно ``SENSORS_MODEL``, а не основную модель;
- основная модель продолжает использоваться для генерации персонажей;
- Sensors не подменяет генерацию персонажа;
- корректный JSON Sensors успешно обрабатывается;
- некорректный JSON не ломает игровой цикл (возврат к fallback);
- ошибка/timeout Sensors не приводит к падению основного цикла;
- Sensors не изменяет БД напрямую (только возвращает предложение);
- результат Sensors проходит через игровые правила (валидация caps);
- при отключённом Sensors функциональность основной генерации продолжает работать.

Sprint 0: слой заведён, НЕ подключён ни к одному процессу.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from unittest.mock import MagicMock

from app.config import Settings
from app.sensors.schemas import (
    EVENT_SCHEMA,
    RELATIONSHIP_SCHEMA,
    get_schema,
    validate_sensor_result,
)
from app.sensors_service import SensorsService


# ---------------------------------------------------------------------------
# Конфигурация: SENSORS_MODEL из .env, изоляция от основной модели
# ---------------------------------------------------------------------------

def test_sensors_model_empty_from_env_default_disables(monkeypatch):
    monkeypatch.delenv("SENSORS_MODEL", raising=False)
    monkeypatch.delenv("SENSORS_ENABLED", raising=False)
    s = Settings(_env_file=None)
    assert s.sensors_model == ""
    assert s.sensors_enabled is False
    assert s.sensors_event_enabled is False


def test_sensors_model_reads_custom_value(monkeypatch):
    monkeypatch.setenv("SENSORS_MODEL", "some-9b-model")
    s = Settings(_env_file=None)
    assert s.sensors_model == "some-9b-model"


def test_sensors_enabled_flags_default_off():
    s = Settings(_env_file=None)
    assert s.sensors_enabled is False
    assert s.sensors_event_enabled is False
    assert s.sensors_perception_enabled is False
    assert s.sensors_emotion_enabled is False
    assert s.sensors_memory_enabled is False
    assert s.sensors_relationship_enabled is False


def test_sensors_service_uses_sensors_model_not_default(monkeypatch):
    monkeypatch.setenv("SENSORS_MODEL", "sensors-test-9b")
    monkeypatch.setenv("DEFAULT_MODEL", "main-model-30b")
    s = Settings(_env_file=None)
    service = SensorsService(model=s.sensors_model, enabled=True)
    assert service.model == "sensors-test-9b"
    assert service.model != s.default_model
    # Основная модель — отдельная настройка, Sensors на неё не влияет
    assert s.default_model == "main-model-30b"


def test_sensors_service_empty_model_disabled():
    service = SensorsService(model="", enabled=True)
    assert service.is_enabled("event") is False


def test_sensors_model_not_used_outside_service():
    """R15: `sensors_model` (атрибут) не используется вне config/sensors_service."""
    allowed = {"config.py", "sensors_service.py"}
    root = Path(__file__).resolve().parent.parent / "app"
    offenders = []
    for py in sorted(root.rglob("*.py")):
        if py.name in allowed:
            continue
        if "sensors_model" in py.read_text(encoding="utf-8"):
            offenders.append(str(py))
    assert not offenders, f"sensors_model используется вне Sensors-слоя: {offenders}"


# ---------------------------------------------------------------------------
# Включённость (master + per-task + model)
# ---------------------------------------------------------------------------

@pytest.fixture
def enabled_service(monkeypatch):
    monkeypatch.setattr("app.sensors_service.settings.sensors_enabled", True)
    return SensorsService(model="sensors-test-9b", enabled=True)


def test_is_enabled_requires_master_and_task_flags(enabled_service, monkeypatch):
    # per-task флаг выключен → задача неактивна
    monkeypatch.setattr("app.sensors_service.settings.sensors_event_enabled", False)
    assert enabled_service.is_enabled("event") is False
    # per-task флаг включён → активна
    monkeypatch.setattr("app.sensors_service.settings.sensors_event_enabled", True)
    assert enabled_service.is_enabled("event") is True
    # мастер-флаг выключен → неактивна даже при per-task on
    service = SensorsService(model="sensors-test-9b", enabled=False)
    assert service.is_enabled("event") is False
    # пустая модель → неактивна
    service = SensorsService(model="", enabled=True)
    assert service.is_enabled("event") is False


def test_unknown_task_never_enabled(enabled_service):
    assert enabled_service.is_enabled("unknown_task") is False


# ---------------------------------------------------------------------------
# Prompt / схемы
# ---------------------------------------------------------------------------

def test_build_prompt_returns_short_messages():
    service = SensorsService(model="m", enabled=True)
    messages = service.build_prompt("event", "Событие: Анна говорит с Петром.")
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "Событие: Анна говорит с Петром." in messages[1]["content"]


def test_schemas_registered_for_all_tasks():
    for task in ("perception", "event", "emotion", "memory", "relationship"):
        assert get_schema(task) is not None
    assert get_schema("нет_такой") is None


# ---------------------------------------------------------------------------
# Валидация результата (через игровые правила/caps)
# ---------------------------------------------------------------------------

def test_validate_valid_event_result():
    result = {
        "event_type": "speech",
        "source_character": "Анна",
        "targets": ["Пётр"],
        "importance": 0.6,
        "audibility": "full",
        "visibility": "full",
    }
    validated = validate_sensor_result(result, EVENT_SCHEMA)
    assert validated == result


def test_validate_missing_required_rejected():
    result = {"event_type": "speech", "targets": []}
    assert validate_sensor_result(result, EVENT_SCHEMA) is None


def test_validate_unknown_field_rejected():
    result = {
        "event_type": "speech",
        "source_character": None,
        "targets": [],
        "importance": 0.5,
        "audibility": "full",
        "visibility": "full",
        "extra": True,
    }
    assert validate_sensor_result(result, EVENT_SCHEMA) is None


def test_validate_caps_relationship_deltas():
    """Результат Sensors ограничен caps — движок не получает дельты сверх лимита."""
    ok = {
        "affection_delta": 20,
        "trust_delta": -20,
        "resentment_delta": 5,
        "jealousy_delta": 0,
        "attraction_delta": 10,
    }
    assert validate_sensor_result(ok, RELATIONSHIP_SCHEMA) == ok
    bad = dict(ok)
    bad["affection_delta"] = 21  # сверх лимита [-20,20]
    assert validate_sensor_result(bad, RELATIONSHIP_SCHEMA) is None


def test_validate_wrong_type_rejected():
    result = {
        "event_type": "speech",
        "source_character": None,
        "targets": [],
        "importance": "high",
        "audibility": "full",
        "visibility": "full",
    }
    assert validate_sensor_result(result, EVENT_SCHEMA) is None


# ---------------------------------------------------------------------------
# Invoke / run (graceful degradation, «не пишет в БД», «не подменяет генерацию»)
# ---------------------------------------------------------------------------

def _fake_client(content: str, *, error: Exception | None = None, extra_fields: dict | None = None):
    """Хттпx-клиент с подменой post (паттерн tests/test_ollama_chat.py)."""
    captured: dict = {}

    async def fake_post(url, json=None, **kwargs):
        if error is not None:
            raise error
        captured["url"] = url
        captured["payload"] = json
        response = MagicMock()
        response.raise_for_status = MagicMock()
        message = {"role": "assistant", "content": content}
        if extra_fields:
            message.update(extra_fields)
        response.json.return_value = {"message": message}
        return response

    client = MagicMock()
    client.post = fake_post
    return client, captured


@pytest.mark.asyncio
async def test_run_returns_validated_result(enabled_service, monkeypatch):
    monkeypatch.setattr("app.sensors_service.settings.sensors_event_enabled", True)
    payload = (
        '{"event_type": "speech", "source_character": "Анна", "targets": ["Пётр"], '
        '"importance": 0.6, "audibility": "full", "visibility": "full"}'
    )
    client, captured = _fake_client(payload)
    result = await enabled_service.run(
        client, task="event", minimal_context="Анна говорит с Петром."
    )
    assert result is not None
    assert result["event_type"] == "speech"
    # вызов пошёл на Sensors-модель, не на основную
    assert captured["payload"]["model"] == "sensors-test-9b"
    assert captured["payload"].get("format") == EVENT_SCHEMA


@pytest.mark.asyncio
async def test_run_handles_markdown_fence(enabled_service, monkeypatch):
    monkeypatch.setattr("app.sensors_service.settings.sensors_event_enabled", True)
    payload = (
        '```json\n{"event_type": "move", "source_character": null, "targets": [], '
        '"importance": 0.4, "audibility": "none", "visibility": "none"}\n```'
    )
    client, _ = _fake_client(payload)
    result = await enabled_service.run(client, task="event", minimal_context="Пётр вышел.")
    assert result is not None
    assert result["event_type"] == "move"


@pytest.mark.asyncio
async def test_invalid_json_returns_none_no_crash(enabled_service, monkeypatch):
    monkeypatch.setattr("app.sensors_service.settings.sensors_event_enabled", True)
    client, _ = _fake_client("это не JSON {")
    result = await enabled_service.run(client, task="event", minimal_context="x")
    assert result is None


@pytest.mark.asyncio
async def test_schema_invalid_result_returns_none(enabled_service, monkeypatch):
    monkeypatch.setattr("app.sensors_service.settings.sensors_event_enabled", True)
    payload = '{"event_type": "speech", "importance": 99}'  # нет обязательных полей
    client, _ = _fake_client(payload)
    result = await enabled_service.run(client, task="event", minimal_context="x")
    assert result is None


@pytest.mark.asyncio
async def test_timeout_returns_none_no_crash(enabled_service, monkeypatch):
    monkeypatch.setattr("app.sensors_service.settings.sensors_event_enabled", True)
    client, _ = _fake_client("{}", error=TimeoutError("timeout"))
    result = await enabled_service.run(client, task="event", minimal_context="x")
    assert result is None


@pytest.mark.asyncio
async def test_request_error_returns_none_no_crash(enabled_service, monkeypatch):
    monkeypatch.setattr("app.sensors_service.settings.sensors_event_enabled", True)
    import httpx

    client, _ = _fake_client("{}", error=httpx.ConnectError("ollama недоступна"))
    result = await enabled_service.run(client, task="event", minimal_context="x")
    assert result is None


@pytest.mark.asyncio
async def test_run_disabled_does_not_call_llm(monkeypatch):
    """При off Sensors — вызовов нет, поведение legacy."""
    service = SensorsService(model="sensors-test-9b", enabled=False)
    client = MagicMock()

    async def boom(*args, **kwargs):
        raise AssertionError("Sensors не должен вызываться при off")

    client.post = boom
    result = await service.run(client, task="event", minimal_context="x")
    assert result is None


@pytest.mark.asyncio
async def test_run_empty_model_does_not_call_llm(monkeypatch):
    service = SensorsService(model="", enabled=True)
    client = MagicMock()

    async def boom(*args, **kwargs):
        raise AssertionError("Sensors не должен вызываться без модели")

    client.post = boom
    assert await service.run(client, task="event", minimal_context="x") is None


@pytest.mark.asyncio
async def test_sensors_does_not_write_to_db(enabled_service, monkeypatch, db_session):
    """Sensors только возвращает предложение и не пишет в БД."""
    monkeypatch.setattr("app.sensors_service.settings.sensors_event_enabled", True)
    payload = (
        '{"event_type": "speech", "source_character": "Анна", "targets": [], '
        '"importance": 0.5, "audibility": "full", "visibility": "full"}'
    )
    client, _ = _fake_client(payload)
    from sqlalchemy import text

    result = await enabled_service.run(client, task="event", minimal_context="x")
    assert result is not None
    counts = {}
    for table in ("memories", "chats", "world_events", "character_relationships"):
        counts[table] = (await db_session.execute(text(f"SELECT COUNT(*) FROM {table}"))).scalar()
    # run не трогает БД — количество строк во всех таблицах осталось нулевым
    assert all(v == 0 for v in counts.values())


@pytest.mark.asyncio
async def test_generation_uses_main_model_not_sensors(monkeypatch):
    """Генерация персонажей использует основную модель (chat.model_name)."""
    from app.config import settings as app_settings
    from app.ollama_client import _build_generation_messages

    service = SensorsService(model="sensors-test-9b", enabled=True)
    assert service.model == "sensors-test-9b"
    assert app_settings.default_model != "sensors-test-9b"
    # В существующем коде генерации Sensors-модель не фигурирует
    root = Path(__file__).resolve().parent.parent / "app"
    generation_files = {"chat_engine.py", "ollama_client.py", "prompt_builder.py"}
    for name in generation_files:
        src = (root / name).read_text(encoding="utf-8")
        assert "sensors_model" not in src, f"{name} не должен использовать sensors_model"
