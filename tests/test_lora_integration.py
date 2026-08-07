"""LoRA Sprint 3 — интеграция в основную генерацию (Plans/LoRA.md §3 Sprint 3).

Маппинг на тест-матрицу (ТЗ §36: 21) и задачи спринта:

- основной вызов ``ollama_client.generate`` в ``process_user_message_streaming``
  и ``regenerate_message_streaming`` идёт на runtime-модель при
  ``lora_enabled=true`` + выбранном адаптере;
- служебные LLM-вызовы (``extract_scene_state``, ``run_post_round_pipeline``)
  получают ``chat.model_name`` (без LoRA);
- при ``lora_enabled=false`` / без выбранного адаптера поведение идентично
  текущему (base-модель, ни одного обращения к runtime);
- ошибка LoRA до начала генерации: ``Incompatible`` → ``RuntimeError`` до
  генерации, конфигурация чата не изменяется, без silent fallback;
- статус ``Unknown``: при первом применении клиенту уходит ``lora_warning``
  (не блокирует, не silent fallback); повторно — не дублируется.
"""

from __future__ import annotations

import json
import struct

import httpx
import pytest
from unittest.mock import MagicMock, patch

from app import chat_engine
from app import crud
from app import schemas
from app.lora_manager import LoRAManager
from tests.conftest import create_characters

GGUF_MAGIC = b"GGUF"
_HEADER = GGUF_MAGIC + struct.pack("<IQ", 1, 0) + struct.pack("<Q", 0)


def make_gguf(path: str, payload: bytes = b"fake-tensor-data") -> str:
    with open(path, "wb") as f:
        f.write(_HEADER + payload)
    return path


class OllamaFake:
    """Fake Ollama через httpx.MockTransport (как в test_lora_runtime)."""

    def __init__(self, version: str = "0.32.6"):
        self.version = version
        self.models: list[str] = []
        self.blobs: set[str] = set()
        self.calls = {"tags": 0, "version": 0, "create": 0, "blob_post": 0}

    async def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.method.upper()
        path = request.url.path
        if path == "/api/tags" and method == "GET":
            self.calls["tags"] += 1
            return httpx.Response(
                200, json={"models": [{"name": m} for m in self.models]}
            )
        if path == "/api/version" and method == "GET":
            self.calls["version"] += 1
            return httpx.Response(200, json={"version": self.version})
        if path.startswith("/api/blobs/"):
            digest = path.rsplit("/", 1)[-1]
            if method == "HEAD":
                exists = digest in self.blobs
                return httpx.Response(
                    200 if exists else 404,
                    json={} if exists else {"error": "blob not found"},
                )
            if method == "POST":
                self.calls["blob_post"] += 1
                self.blobs.add(digest)
                return httpx.Response(200, json={"status": "success"})
        if path == "/api/create" and method == "POST":
            self.calls["create"] += 1
            body = json.loads(request.content.decode("utf-8"))
            self.models.append(body["model"])
            return httpx.Response(200, json={"status": "success"})
        return httpx.Response(404, json={"error": f"unhandled {method} {path}"})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(self.handler),
            base_url="http://ollama:11434",
        )


@pytest.fixture
def reset_lora_warnings():
    """Сбрасывает in-process флаг «чату уже показано Unknown-предупреждение»."""
    chat_engine._lora_unknown_warned_chats.clear()
    yield
    chat_engine._lora_unknown_warned_chats.clear()


async def _enable_lora(db_session, chat, adapter_id: int) -> None:
    await crud.put_chat_lora_config(
        db_session, chat.id, schemas.ChatLoRAConfig(enabled=True, adapter_id=adapter_id)
    )


async def _setup_adapter(db_session, tmp_path) -> tuple:
    gguf_path = make_gguf(str(tmp_path / "dark.gguf"))
    adapter = await crud.create_lora_adapter(
        db_session,
        schemas.LoRAAdapterCreate(
            name="Dark Goetia RU",
            path=gguf_path,
            format="auto",
            base_model="goetia-26b",
            base_model_identity="Naphula/Goetia-26B-A4B-v1.3-Absolute-Heretic-ARA",
        ),
    )
    return adapter, gguf_path


async def _set_chat_model(db_session, chat, model: str = "goetia-26b") -> None:
    chat.model_name = model
    await db_session.flush()


async def _run_round(db_session, chat, fake_client, *, lora_manager):
    events = []
    async for event in chat_engine.process_user_message_streaming(
        fake_client,
        db_session,
        chat.id,
        "Привет всем",
        lora_manager=lora_manager,
    ):
        events.append(event)
    return events


# ------------------- ТЗ §36:21 — служебные вызовы без LoRA -------------------


@pytest.mark.asyncio
async def test_main_generation_uses_runtime_model_service_calls_base(
    db_session, chat, tmp_path, reset_lora_warnings
):
    """Основной `generate()` идёт на runtime-модель; scene state и post-round
    pipeline получают `chat.model_name` (без LoRA). На первом применении
    (Unknown) клиенту уходит `lora_warning`."""
    await create_characters(db_session, chat.id, 1)
    adapter, gguf = await _setup_adapter(db_session, tmp_path)
    await _set_chat_model(db_session, chat)
    await _enable_lora(db_session, chat, adapter.id)

    generate_models: list[str] = []
    scene_models: list[str] = []
    pipeline_models: list[str] = []

    async def fake_generate(**kwargs):
        generate_models.append(kwargs["model_name"])
        yield {"type": "response", "text": "Reply with enough text for validation."}

    async def fake_scene_state(client, model_name, round_history_text,
                               current_scene_state, character_names, locations):
        scene_models.append(model_name)
        return None

    async def fake_pipeline(**kwargs):
        pipeline_models.append(kwargs["model_name"])
        return {}

    fake = OllamaFake()
    manager = LoRAManager()
    async with fake.client() as fake_client:
        with patch(
            "app.chat_engine.ollama_client.generate", side_effect=fake_generate
        ), patch(
            "app.chat_engine.ollama_client.extract_scene_state",
            side_effect=fake_scene_state,
        ), patch(
            "app.post_round_pipeline.run_post_round_pipeline",
            side_effect=fake_pipeline,
        ), patch("app.chat_engine.asyncio.create_task"), patch(
            "app.chat_engine.asyncio.to_thread",
        ) as to_thread:
            to_thread.return_value = None
            events = await _run_round(
                db_session, chat, fake_client, lora_manager=manager
            )

    # Основной ответ персонажа — runtime-модель LoRA (не базовая).
    assert len(generate_models) == 1
    assert generate_models[0].startswith("goetia-26b-lora-")
    assert generate_models[0] != "goetia-26b"
    assert fake.calls["create"] == 1  # ровно 1× create на конфигурацию

    # Служебные вызовы — базовая модель без LoRA.
    assert scene_models == ["goetia-26b"]
    assert pipeline_models == ["goetia-26b"]

    # Unknown (chat.base_model_identity=NULL) → предупреждение на первом применении.
    warnings = [e for e in events if e["type"] == "lora_warning"]
    assert len(warnings) == 1
    assert warnings[0]["kind"] == "compatibility_unknown"
    assert "не подтверждена" in warnings[0]["detail"]


@pytest.mark.asyncio
async def test_unknown_warning_fires_only_once(
    db_session, chat, tmp_path, reset_lora_warnings
):
    """Предупреждение Unknown уходит клиенту только при ПЕРВОМ применении."""
    await create_characters(db_session, chat.id, 1)
    adapter, gguf = await _setup_adapter(db_session, tmp_path)
    await _set_chat_model(db_session, chat)
    await _enable_lora(db_session, chat, adapter.id)

    async def fake_generate(**kwargs):
        yield {"type": "response", "text": "Reply with enough text for validation."}

    fake = OllamaFake()
    manager = LoRAManager()
    with patch(
        "app.chat_engine.ollama_client.generate", side_effect=fake_generate
    ), patch(
        "app.chat_engine.ollama_client.extract_scene_state",
        MagicMock(return_value=None),
    ), patch(
        "app.post_round_pipeline.run_post_round_pipeline",
        MagicMock(return_value={}),
    ), patch("app.chat_engine.asyncio.create_task"), patch(
        "app.chat_engine.asyncio.to_thread", return_value=None
    ):
        async with fake.client() as fake_client:
            first = await _run_round(db_session, chat, fake_client, lora_manager=manager)
            second = await _run_round(db_session, chat, fake_client, lora_manager=manager)

    first_warnings = [e for e in first if e["type"] == "lora_warning"]
    second_warnings = [e for e in second if e["type"] == "lora_warning"]
    assert len(first_warnings) == 1
    assert len(second_warnings) == 0
    assert fake.calls["create"] == 1  # второй раунд — кэш-хит, без create


@pytest.mark.asyncio
async def test_lora_disabled_identical_to_current_behavior(
    db_session, chat, tmp_path
):
    """lora_enabled=false → base-модель, runtime-обращений нет (критерий готовности)."""
    await create_characters(db_session, chat.id, 1)
    adapter, gguf = await _setup_adapter(db_session, tmp_path)
    await _set_chat_model(db_session, chat)
    # конфигурация НЕ включена (lora_enabled=false по умолчанию)

    generate_models: list[str] = []

    async def fake_generate(**kwargs):
        generate_models.append(kwargs["model_name"])
        yield {"type": "response", "text": "Reply with enough text for validation."}

    fake = OllamaFake()
    manager = LoRAManager()
    async with fake.client() as fake_client:
        with patch(
            "app.chat_engine.ollama_client.generate", side_effect=fake_generate
        ), patch(
            "app.chat_engine.ollama_client.extract_scene_state",
            MagicMock(return_value=None),
        ), patch(
            "app.post_round_pipeline.run_post_round_pipeline",
            MagicMock(return_value={}),
        ), patch("app.chat_engine.asyncio.create_task"), patch(
            "app.chat_engine.asyncio.to_thread", return_value=None
        ):
            events = await _run_round(db_session, chat, fake_client, lora_manager=manager)

    assert generate_models == ["goetia-26b"]
    assert fake.calls["create"] == 0
    assert fake.calls["tags"] == 0  # к Ollama не обращаемся вообще
    assert not [e for e in events if e["type"] == "lora_warning"]


@pytest.mark.asyncio
async def test_lora_enabled_without_adapter_uses_base_model(
    db_session, chat, tmp_path
):
    """enabled=true + адаптер не выбран → base-модель, runtime-модель НЕ создаётся."""
    await create_characters(db_session, chat.id, 1)
    await _set_chat_model(db_session, chat)
    await crud.put_chat_lora_config(
        db_session, chat.id, schemas.ChatLoRAConfig(enabled=True, adapter_id=None)
    )

    generate_models: list[str] = []

    async def fake_generate(**kwargs):
        generate_models.append(kwargs["model_name"])
        yield {"type": "response", "text": "Reply with enough text for validation."}

    fake = OllamaFake()
    manager = LoRAManager()
    async with fake.client() as fake_client:
        with patch(
            "app.chat_engine.ollama_client.generate", side_effect=fake_generate
        ), patch(
            "app.chat_engine.ollama_client.extract_scene_state",
            MagicMock(return_value=None),
        ), patch(
            "app.post_round_pipeline.run_post_round_pipeline",
            MagicMock(return_value={}),
        ), patch("app.chat_engine.asyncio.create_task"), patch(
            "app.chat_engine.asyncio.to_thread", return_value=None
        ):
            await _run_round(db_session, chat, fake_client, lora_manager=manager)

    assert generate_models == ["goetia-26b"]
    assert fake.calls["create"] == 0


@pytest.mark.asyncio
async def test_incompatible_blocks_before_generation_config_unchanged(
    db_session, chat, tmp_path, reset_lora_warnings
):
    """Incompatible → RuntimeError ДО генерации; конфиг чата не меняется; без
    silent fallback (create и generate не вызываются)."""
    await create_characters(db_session, chat.id, 1)
    adapter, gguf = await _setup_adapter(db_session, tmp_path)
    await _set_chat_model(db_session, chat)
    chat.base_model_identity = "Completely/Different-Base-Model"
    await db_session.flush()
    await _enable_lora(db_session, chat, adapter.id)

    generate_called = []

    async def fake_generate(**kwargs):
        generate_called.append(True)
        yield {"type": "response", "text": "never"}

    fake = OllamaFake()
    manager = LoRAManager()
    async with fake.client() as fake_client:
        with patch(
            "app.chat_engine.ollama_client.generate", side_effect=fake_generate
        ), patch("app.chat_engine.asyncio.create_task"), patch(
            "app.chat_engine.asyncio.to_thread", return_value=None
        ):
            with pytest.raises(RuntimeError, match="несовместим"):
                await _run_round(db_session, chat, fake_client, lora_manager=manager)

    assert generate_called == []
    assert fake.calls["create"] == 0
    # конфигурация чата не изменена
    config = await crud.get_chat_lora_config(db_session, chat.id)
    assert config.enabled is True
    assert config.adapter_id == adapter.id


# ------------------- Перегенерация ответа персонажа -------------------


async def _make_round_messages(db_session, chat, characters):
    user_msg = await crud.create_message(
        db_session,
        schemas.MessageCreate(chat_id=chat.id, role="user", content="Hello everyone"),
    )
    replies = []
    for c in characters:
        replies.append(
            await crud.create_message(
                db_session,
                schemas.MessageCreate(
                    chat_id=chat.id,
                    character_id=c.id,
                    role="character",
                    content=f"Reply from {c.name}.",
                ),
            )
        )
    return user_msg, replies


@pytest.mark.asyncio
async def test_regeneration_uses_runtime_model(db_session, chat, tmp_path):
    """Перегенерация ответа персонажа идёт на runtime-модель (задача 2)."""
    characters = await create_characters(db_session, chat.id, 1)
    _, replies = await _make_round_messages(db_session, chat, characters)
    target = replies[0]

    adapter, gguf = await _setup_adapter(db_session, tmp_path)
    await _set_chat_model(db_session, chat)
    await _enable_lora(db_session, chat, adapter.id)

    generate_models: list[str] = []

    async def fake_generate(**kwargs):
        generate_models.append(kwargs["model_name"])
        yield {"type": "token", "text": "New "}
        yield {"type": "response", "text": "New reply with enough text."}

    fake = OllamaFake()
    manager = LoRAManager()
    async with fake.client() as fake_client:
        with patch(
            "app.chat_engine.ollama_client.generate", side_effect=fake_generate
        ), patch.object(chat_engine.settings, "embedding_enabled", False), patch.object(
            chat_engine.settings, "context_enabled", False
        ):
            events = []
            async for event in chat_engine.regenerate_message_streaming(
                fake_client,
                db_session,
                chat.id,
                target.id,
                lora_manager=manager,
            ):
                events.append(event)

    assert len(generate_models) == 1
    assert generate_models[0].startswith("goetia-26b-lora-")
    assert fake.calls["create"] == 1
    # новая реплика сохранена, старая удалена
    message_events = [e for e in events if e["type"] == "message"]
    assert len(message_events) == 1
    assert message_events[0]["message"]["id"] != target.id
