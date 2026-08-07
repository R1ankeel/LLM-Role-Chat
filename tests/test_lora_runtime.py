"""LoRA Sprint 2 — runtime-слой (Plans/LoRA.md §3 Sprint 2).

Маппинг на тест-матрицу (ТЗ §36: 14–21):

| № | Покрытие |
|---|----------|
| 14–15 | runtime key/name: детерминированный, чувствителен к base identity + adapter_id + sha256 файла; формат `{slug}-lora-{hash8}` |
| 16–17 | семантика `lora_enabled` (§2.4): enabled=false → base; enabled=true без адаптера → base, runtime-модель НЕ создаётся |
| 18–19 | resolve с выбранным адаптером: Compatible → runtime-модель, ровно 1× create; кэш-хит на повторном resolve |
| 20–21 | несовместимость/ошибки: Incompatible → RuntimeError (не silent fallback); Unknown → не блокирует; missing файла → ошибка; повторный запуск (list_models) не пересоздаёт модель; смена адаптера → новая runtime-модель |

Ключевые требования:
- для одной конфигурации `ollama create` выполняется максимум 1 раз;
- смена адаптера даёт новый ключ;
- повторный запуск не пересоздаёт модель (сверка `list_models`);
- ни один код не вызывает `ollama create` на каждое сообщение.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct

import httpx
import pytest
import pytest_asyncio

from app import crud, ollama_client, schemas
from app.lora_manager import (
    CompatibilityStatus,
    LoRAManager,
    check_compatibility,
    runtime_key,
    runtime_name,
    validate,
)

GGUF_MAGIC = b"GGUF"
_HEADER = GGUF_MAGIC + struct.pack("<IQ", 1, 0) + struct.pack("<Q", 0)


def make_gguf(path: str, payload: bytes = b"fake-tensor-data") -> str:
    """Записать минимально валидный GGUF-файл (магические байты + заголовок)."""
    with open(path, "wb") as f:
        f.write(_HEADER + payload)
    return path


def sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        digest.update(f.read())
    return digest.hexdigest()


class OllamaFake:
    """Fake Ollama через httpx.MockTransport: in-memory models + blobs."""

    def __init__(self, version: str = "0.32.6"):
        self.version = version
        self.models: list[str] = []
        self.blobs: set[str] = set()
        self.created_payloads: list[dict] = []
        self.requests: list[httpx.Request] = []
        self.calls = {
            "tags": 0,
            "version": 0,
            "create": 0,
            "blob_head": 0,
            "blob_post": 0,
            "delete": 0,
        }

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
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
                self.calls["blob_head"] += 1
                if digest in self.blobs:
                    return httpx.Response(200, json={})
                return httpx.Response(404, json={"error": "blob not found"})
            if method == "POST":
                self.calls["blob_post"] += 1
                self.blobs.add(digest)
                return httpx.Response(200, json={"status": "success"})
        if path == "/api/create" and method == "POST":
            self.calls["create"] += 1
            body = json.loads(request.content.decode("utf-8"))
            self.created_payloads.append(body)
            self.models.append(body["model"])
            return httpx.Response(200, json={"status": "success"})
        if path == "/api/delete" and method == "DELETE":
            self.calls["delete"] += 1
            body = json.loads(request.content.decode("utf-8"))
            name = body.get("model")
            if name in self.models:
                self.models.remove(name)
                return httpx.Response(200, json={"status": "success"})
            return httpx.Response(404, json={"error": "model not found"})
        return httpx.Response(404, json={"error": f"unhandled {method} {path}"})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(self.handler),
            base_url="http://ollama:11434",
        )


@pytest_asyncio.fixture
async def lora_setup(db_session, tmp_path):
    """Адаптер с явной identity + чат с включённой LoRA и выбранным адаптером.

    Возвращает ``(chat, adapter, gguf_path)``. Identity адаптера задана явно,
    ``chat.base_model_identity`` — NULL → compatibility = Unknown (§2.3).
    """
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
    chat = await crud.create_chat(
        db_session, schemas.ChatCreate(name="Чат", general_prompt="")
    )
    chat.model_name = "goetia-26b"
    await db_session.flush()
    await crud.put_chat_lora_config(
        db_session,
        chat.id,
        schemas.ChatLoRAConfig(enabled=True, adapter_id=adapter.id),
    )
    return chat, adapter, gguf_path


# ------------------------------ runtime key / name (§2.2) ------------------------------


def test_runtime_key_deterministic_and_sensitive():
    k1 = runtime_key(base_identity="base", adapter_id=1, file_sha256="aaa")
    assert k1 == runtime_key(base_identity="base", adapter_id=1, file_sha256="aaa")
    assert len(k1) == 64  # sha256 hex
    # любое изменение конфигурации → новый ключ (§2.2)
    assert k1 != runtime_key(base_identity="other", adapter_id=1, file_sha256="aaa")
    assert k1 != runtime_key(base_identity="base", adapter_id=2, file_sha256="aaa")
    assert k1 != runtime_key(base_identity="base", adapter_id=1, file_sha256="bbb")


def test_runtime_name_format():
    key = "a" * 64
    name = runtime_name("goetia-26b", key)
    assert name == f"goetia-26b-lora-{key[:8]}"
    # тег модели нормализуется в slug
    assert runtime_name("goetia-26b:latest", key) == f"goetia-26b-latest-lora-{key[:8]}"
    # пустая база → 'model'
    assert runtime_name("", key).startswith("model-lora-")


# ------------------------------ семантика lora_enabled (§2.4) ------------------------------


async def test_resolve_enabled_false_uses_base_model(db_session, tmp_path):
    gguf_path = make_gguf(str(tmp_path / "a.gguf"))
    await crud.create_lora_adapter(
        db_session, schemas.LoRAAdapterCreate(name="A", path=gguf_path)
    )
    chat = await crud.create_chat(
        db_session, schemas.ChatCreate(name="C", general_prompt="")
    )
    chat.model_name = "goetia-26b"
    await db_session.flush()  # lora_enabled по умолчанию False

    fake = OllamaFake()
    manager = LoRAManager()
    async with fake.client() as client:
        model, info = await manager.resolve(db_session, client, chat)

    assert model == "goetia-26b"
    assert info.runtime_used is False
    assert fake.calls["create"] == 0
    assert fake.calls["tags"] == 0  # к Ollama не обращаемся вообще


async def test_resolve_enabled_true_no_adapter_uses_base_model(db_session):
    """enabled=true + адаптер не выбран: base-модель, runtime-модель НЕ создаётся."""
    chat = await crud.create_chat(
        db_session, schemas.ChatCreate(name="C", general_prompt="")
    )
    chat.model_name = "goetia-26b"
    await db_session.flush()
    await crud.put_chat_lora_config(
        db_session, chat.id, schemas.ChatLoRAConfig(enabled=True, adapter_id=None)
    )

    fake = OllamaFake()
    manager = LoRAManager()
    async with fake.client() as client:
        model, info = await manager.resolve(db_session, client, chat)

    assert model == "goetia-26b"
    assert info.runtime_used is False
    assert info.adapter_id is None
    assert fake.calls["create"] == 0
    assert fake.calls["tags"] == 0


# ------------------------------ resolve: runtime-модель ------------------------------


async def test_resolve_compatible_creates_runtime_model_once(lora_setup, db_session):
    """Compatible → runtime-модель; повторный resolve = кэш-хит, create 1×."""
    chat, adapter, gguf = lora_setup
    chat.base_model_identity = adapter.base_model_identity  # explicit на обоих
    await db_session.flush()

    fake = OllamaFake()
    manager = LoRAManager()
    async with fake.client() as client:
        model1, info1 = await manager.resolve(db_session, client, chat)
        model2, info2 = await manager.resolve(db_session, client, chat)

    assert info1.compatibility.status is CompatibilityStatus.COMPATIBLE
    assert model1 == model2
    assert model1.startswith("goetia-26b-lora-")
    assert len(model1.rsplit("-", 1)[1]) == 8  # hash8
    assert info1.runtime_used is True
    assert info1.created is True
    assert info2.created is False

    # максимум 1× create на конфигурацию; кэш-хит не трогает даже /api/tags
    assert fake.calls["create"] == 1
    assert fake.calls["tags"] == 1
    assert fake.calls["version"] == 1  # check_capabilities закэширован на инстансе

    # структурный payload POST /api/create: from + adapters (без modelfile)
    payload = fake.created_payloads[0]
    assert payload["model"] == model1
    assert payload["from"] == "goetia-26b"
    assert payload["stream"] is False
    assert len(payload["adapters"]) == 1  # ровно один адаптер (§2.5)
    filename, digest = next(iter(payload["adapters"].items()))
    assert digest == f"sha256:{adapter.sha256}"
    assert "modelfile" not in payload


async def test_resolve_unknown_proceeds_with_warning(lora_setup, db_session):
    """Unknown (fallback identity на model_name) — НЕ блокирует, runtime-модель создаётся."""
    chat, adapter, gguf = lora_setup  # chat.base_model_identity=NULL → Unknown

    fake = OllamaFake()
    manager = LoRAManager()
    async with fake.client() as client:
        model, info = await manager.resolve(db_session, client, chat)

    assert info.compatibility.status is CompatibilityStatus.UNKNOWN
    assert info.runtime_used is True
    assert info.created is True
    assert fake.calls["create"] == 1


async def test_resolve_incompatible_raises(lora_setup, db_session):
    """Incompatible (обе identity explicit, не совпадают) → явная ошибка, create нет."""
    chat, adapter, gguf = lora_setup
    chat.base_model_identity = "Completely/Different-Base-Model"
    await db_session.flush()

    fake = OllamaFake()
    manager = LoRAManager()
    async with fake.client() as client:
        with pytest.raises(RuntimeError, match="несовместим"):
            await manager.resolve(db_session, client, chat)

    assert fake.calls["create"] == 0


async def test_resolve_missing_adapter_file_raises(lora_setup, db_session):
    """Файл адаптера удалён после регистрации → RuntimeError, create нет."""
    chat, adapter, gguf = lora_setup
    os.remove(gguf)

    fake = OllamaFake()
    manager = LoRAManager()
    async with fake.client() as client:
        with pytest.raises(RuntimeError, match="не найден"):
            await manager.resolve(db_session, client, chat)

    assert fake.calls["create"] == 0
    assert fake.calls["blob_post"] == 0


# ------------------------------ кэш: перезапуск / смена адаптера ------------------------------


async def test_fresh_manager_reuses_existing_model_via_list_models(lora_setup, db_session):
    """Повторный запуск (новый инстанс менеджера) сверяется с list_models — create нет."""
    chat, adapter, gguf = lora_setup
    key = runtime_key(
        base_identity="goetia-26b",  # fallback base identity (model_name)
        adapter_id=adapter.id,
        file_sha256=adapter.sha256,
    )
    name = runtime_name("goetia-26b", key)
    fake = OllamaFake()
    fake.models.append(name)  # модель уже существует в Ollama (прошлый запуск)

    manager = LoRAManager()
    async with fake.client() as client:
        model, info = await manager.resolve(db_session, client, chat)

    assert model == name
    assert info.created is False
    assert fake.calls["create"] == 0
    assert fake.calls["tags"] >= 1  # сверка list_models была
    assert fake.calls["blob_post"] == 0


async def test_adapter_change_gives_new_runtime_model(db_session, tmp_path):
    """Смена адаптера → новый ключ → новая runtime-модель (без «залипания»)."""
    g1 = make_gguf(str(tmp_path / "a.gguf"), payload=b"content-a")
    g2 = make_gguf(str(tmp_path / "b.gguf"), payload=b"content-b-different")
    a1 = await crud.create_lora_adapter(
        db_session, schemas.LoRAAdapterCreate(name="A1", path=g1)
    )
    a2 = await crud.create_lora_adapter(
        db_session, schemas.LoRAAdapterCreate(name="A2", path=g2)
    )
    chat = await crud.create_chat(
        db_session, schemas.ChatCreate(name="C", general_prompt="")
    )
    chat.model_name = "goetia-26b"
    await db_session.flush()

    fake = OllamaFake()
    manager = LoRAManager()
    async with fake.client() as client:
        await crud.put_chat_lora_config(
            db_session, chat.id, schemas.ChatLoRAConfig(enabled=True, adapter_id=a1.id)
        )
        m1, _ = await manager.resolve(db_session, client, chat)
        await crud.put_chat_lora_config(
            db_session, chat.id, schemas.ChatLoRAConfig(enabled=True, adapter_id=a2.id)
        )
        m2, _ = await manager.resolve(db_session, client, chat)

    assert m1 != m2
    assert len(fake.models) == 2  # обе runtime-модели созданы и остаются в Ollama
    assert fake.calls["create"] == 2


# ------------------------------ OllamaClient: create/blob/delete ------------------------------


async def test_create_model_rejects_multiple_adapters(tmp_path):
    fake = OllamaFake()
    async with fake.client() as client:
        with pytest.raises(RuntimeError, match="ровно один"):
            await ollama_client.create_model(
                client,
                "m",
                "base",
                {"a.gguf": "sha256:1", "b.gguf": "sha256:2"},
            )
    assert fake.calls["create"] == 0


async def test_upload_adapter_file_skips_post_if_blob_exists(tmp_path):
    gguf_path = make_gguf(str(tmp_path / "a.gguf"))
    fake = OllamaFake()
    fake.blobs.add("sha256:existing")
    async with fake.client() as client:
        uploaded = await ollama_client.upload_adapter_file(client, gguf_path, "existing")
    assert uploaded is False
    assert fake.calls["blob_head"] == 1
    assert fake.calls["blob_post"] == 0


async def test_upload_adapter_file_posts_bytes_on_404(tmp_path):
    gguf_path = make_gguf(str(tmp_path / "a.gguf"))
    fake = OllamaFake()
    async with fake.client() as client:
        uploaded = await ollama_client.upload_adapter_file(client, gguf_path, "sha256:abc")
    assert uploaded is True
    assert fake.calls["blob_post"] == 1
    assert "sha256:abc" in fake.blobs


async def test_upload_adapter_file_missing_file_raises(tmp_path):
    missing = str(tmp_path / "gone.gguf")
    fake = OllamaFake()
    async with fake.client() as client:
        with pytest.raises(RuntimeError, match="прочитать"):
            await ollama_client.upload_adapter_file(client, missing, "sha256:abc")


async def test_delete_model_uses_request_delete_with_body():
    """httpx 0.28.1: Client.delete не принимает body → request("DELETE", json=...)."""
    fake = OllamaFake()
    async with fake.client() as client:
        await ollama_client.delete_model(client, "some-model")
        await ollama_client.delete_model(client, "missing-model")  # 404 — успех
    assert fake.calls["delete"] == 2
    delete_reqs = [r for r in fake.requests if r.url.path == "/api/delete"]
    assert delete_reqs[0].method == "DELETE"
    assert json.loads(delete_reqs[0].content) == {"model": "some-model"}


async def test_check_capabilities_cached():
    fake = OllamaFake()
    manager = LoRAManager()
    async with fake.client() as client:
        caps1 = await manager.check_capabilities(client)
        caps2 = await manager.check_capabilities(client)
    assert caps1.supports_lora is True
    assert caps1.supports_safetensors is False  # GGUF-only (§2.5)
    assert caps1 is caps2
    assert fake.calls["version"] == 1  # кэширование на инстансе


async def test_check_capabilities_unreachable_raises():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://x") as client:
        with pytest.raises(RuntimeError, match="недоступна"):
            await ollama_client.check_capabilities(client)


# ------------------------------ validate / compatibility (§2.3, §2.7) ------------------------------


async def test_validate_missing_file_and_compat(lora_setup, db_session, tmp_path):
    chat, adapter, gguf = lora_setup
    # путь цел → path_ok, compatibility Unknown (fallback identity)
    result = validate(adapter, chat)
    assert result.path_ok is True
    assert result.compatibility.status is CompatibilityStatus.UNKNOWN

    os.remove(gguf)
    result = validate(adapter, chat)
    assert result.path_ok is False
    assert "не найден" in result.path_error


async def test_compatibility_high_confidence_match(lora_setup, db_session):
    chat, adapter, gguf = lora_setup
    chat.base_model_identity = adapter.base_model_identity
    await db_session.flush()
    assert (
        check_compatibility(adapter, chat).status is CompatibilityStatus.COMPATIBLE
    )


async def test_compatibility_model_name_fallback_is_unknown(lora_setup, db_session):
    """Локальное имя (goetia-26b) vs HF identity — Unknown, НЕ Incompatible (§2.3)."""
    chat, adapter, gguf = lora_setup
    result = check_compatibility(adapter, chat)
    assert result.status is CompatibilityStatus.UNKNOWN
    assert result.base_identity == "goetia-26b"


def test_runtime_key_uses_db_sha256(lora_setup, db_session):
    """runtime key опирается на хранимый sha256 содержимого файла (§2.2)."""
    chat, adapter, gguf = lora_setup
    k1 = runtime_key(
        base_identity="goetia-26b", adapter_id=adapter.id, file_sha256=adapter.sha256
    )
    k2 = runtime_key(
        base_identity="goetia-26b",
        adapter_id=adapter.id,
        file_sha256=sha256_of(gguf),
    )
    assert k1 == k2  # sha256 совпадает с содержимым файла
