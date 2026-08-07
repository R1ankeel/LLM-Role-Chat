"""LoRA Sprint 4 — REST API (Plans/LoRA.md §3 Sprint 4).

Маппинг на тест-матрицу (ТЗ §36: 22–31):

| № | Покрытие |
|---|----------|
| 22–26 | Registry API: GET/POST/PUT/DELETE `/api/lora`, коды ошибок (404/409/422), файл не удаляется |
| 27–31 | Chat config API: GET/PUT `/api/chats/{id}/lora`, атомарность, `enabled=true` + `adapter_id=null` |

Ключевые требования Sprint 4:
- две группы endpoints разделены по §2.6 (registry vs конфигурация чата);
- PUT конфигурации чата валидирует ссылку на адаптер и UNIQUE(chat_id);
- `enabled=true` + `adapter_id=null` сохраняется как допустимое состояние (§2.4);
- DELETE регистрации при использовании чатами → 409 со списком чатов;
  физический файл не трогается.
"""

from __future__ import annotations

import hashlib
import os
import struct

from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import crud, schemas
from app.database import get_async_db
from app.routers.lora import router as lora_router

GGUF_MAGIC = b"GGUF"
_HEADER = GGUF_MAGIC + struct.pack("<IQ", 1, 0) + struct.pack("<Q", 0)


def make_gguf(path: str, payload: bytes = b"fake-tensor-data") -> str:
    with open(path, "wb") as f:
        f.write(_HEADER + payload)
    return path


def sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        digest.update(f.read())
    return digest.hexdigest()


async def _make_client(session_factory) -> AsyncClient:
    app = FastAPI()
    app.include_router(lora_router)

    async def override_db():
        async with session_factory() as db:
            yield db

    app.dependency_overrides[get_async_db] = override_db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _session_factory(db_engine):
    return async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )


async def _seed_chat(db_engine) -> int:
    session_factory = _session_factory(db_engine)
    async with session_factory() as db:
        chat = await crud.create_chat(
            db, schemas.ChatCreate(name="Test Chat", general_prompt="scene")
        )
        return chat.id


async def _seed_adapter(db_engine, tmp_path, name="Dark Goetia RU") -> tuple[int, str]:
    session_factory = _session_factory(db_engine)
    gguf_path = make_gguf(str(tmp_path / f"{name}.gguf"))
    async with session_factory() as db:
        adapter = await crud.create_lora_adapter(
            db,
            schemas.LoRAAdapterCreate(
                name=name,
                path=gguf_path,
                format="auto",
                base_model="goetia-26b",
                base_model_identity="Naphula/Goetia-26B-A4B-v1.3-Absolute-Heretic-ARA",
            ),
        )
        return adapter.id, gguf_path


# ------------------------------ Registry: GET/POST ------------------------------


async def test_get_lora_empty_list(db_engine):
    session_factory = _session_factory(db_engine)
    async with await _make_client(session_factory) as client:
        resp = await client.get("/lora")
        assert resp.status_code == 200
        assert resp.json() == []


async def test_create_and_list_adapters(db_engine, tmp_path):
    session_factory = _session_factory(db_engine)
    gguf_path = make_gguf(str(tmp_path / "dark.gguf"))
    async with await _make_client(session_factory) as client:
        resp = await client.post(
            "/lora",
            json={
                "name": "Dark Goetia RU",
                "path": gguf_path,
                "format": "auto",
                "base_model": "goetia-26b",
                "base_model_identity": "Naphula/Goetia-26B-A4B-v1.3-Absolute-Heretic-ARA",
                "description": "RU-адаптер",
                "metadata": {"version": 1},
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "Dark Goetia RU"
        assert body["path"] == gguf_path
        assert body["format"] == "gguf"  # auto → фактический
        assert body["sha256"] == sha256_of(gguf_path)
        assert body["base_model_identity"] == "Naphula/Goetia-26B-A4B-v1.3-Absolute-Heretic-ARA"
        assert body["metadata"] == {"version": 1}
        adapter_id = body["id"]

        lst = await client.get("/lora")
        assert lst.status_code == 200
        assert [a["id"] for a in lst.json()] == [adapter_id]


async def test_create_adapter_invalid_path_422(db_engine, tmp_path):
    session_factory = _session_factory(db_engine)
    async with await _make_client(session_factory) as client:
        resp = await client.post(
            "/lora", json={"name": "A", "path": "relative.gguf"}
        )
        assert resp.status_code == 422
        assert "абсолютным" in resp.json()["detail"]


async def test_create_adapter_safetensors_422(db_engine, tmp_path):
    session_factory = _session_factory(db_engine)
    safetensors = tmp_path / "adapter.safetensors"
    safetensors.write_bytes(b"\x00" * 100)
    async with await _make_client(session_factory) as client:
        resp = await client.post(
            "/lora",
            json={"name": "A", "path": str(safetensors), "format": "safetensors"},
        )
        assert resp.status_code == 422
        assert "safetensors" in resp.json()["detail"].lower()


async def test_create_adapter_validation_errors_are_422(db_engine, tmp_path):
    """Проверка конвенции кодов: 422 для невалидного пути/файла (задача 3)."""
    session_factory = _session_factory(db_engine)
    bad = tmp_path / "not_lora.gguf"
    bad.write_bytes(b"<html>not a model</html>")
    async with await _make_client(session_factory) as client:
        for path in ["C:/definitely/missing.gguf", str(bad)]:
            resp = await client.post("/lora", json={"name": "A", "path": path})
            assert resp.status_code == 422


# ------------------------------ Registry: PUT ------------------------------


async def test_update_adapter(db_engine, tmp_path):
    adapter_id, _ = await _seed_adapter(db_engine, tmp_path)
    session_factory = _session_factory(db_engine)
    async with await _make_client(session_factory) as client:
        resp = await client.put(
            f"/lora/{adapter_id}",
            json={"name": "Dark Goetia RU v2", "description": "обновлено"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "Dark Goetia RU v2"
        assert body["description"] == "обновлено"
        assert body["sha256"] == sha256_of(  # путь не менялся — sha256 тот же
            body["path"]
        )


async def test_update_adapter_path_revalidates(db_engine, tmp_path):
    adapter_id, old_path = await _seed_adapter(db_engine, tmp_path, name="old")
    new_path = make_gguf(str(tmp_path / "new.gguf"), payload=b"different")
    session_factory = _session_factory(db_engine)
    async with await _make_client(session_factory) as client:
        resp = await client.put(f"/lora/{adapter_id}", json={"path": new_path})
        assert resp.status_code == 200
        body = resp.json()
        assert body["path"] == new_path
        assert body["sha256"] == sha256_of(new_path)
        assert body["sha256"] != sha256_of(old_path)


async def test_update_adapter_invalid_path_422(db_engine, tmp_path):
    adapter_id, _ = await _seed_adapter(db_engine, tmp_path)
    session_factory = _session_factory(db_engine)
    async with await _make_client(session_factory) as client:
        resp = await client.put(
            f"/lora/{adapter_id}", json={"path": "relative.gguf"}
        )
        assert resp.status_code == 422


async def test_update_missing_adapter_404(db_engine):
    session_factory = _session_factory(db_engine)
    async with await _make_client(session_factory) as client:
        resp = await client.put("/lora/999999", json={"name": "X"})
        assert resp.status_code == 404


# ------------------------------ Registry: DELETE ------------------------------


async def test_delete_unused_adapter_keeps_file(db_engine, tmp_path):
    adapter_id, gguf_path = await _seed_adapter(db_engine, tmp_path)
    assert os.path.exists(gguf_path)
    session_factory = _session_factory(db_engine)
    async with await _make_client(session_factory) as client:
        resp = await client.delete(f"/lora/{adapter_id}")
        assert resp.status_code == 204

        lst = await client.get("/lora")
        assert lst.json() == []
    # физический файл пользователя НЕ удаляется (§2.7)
    assert os.path.exists(gguf_path)


async def test_delete_used_adapter_409_with_chats(db_engine, chat, tmp_path):
    adapter_id, gguf_path = await _seed_adapter(db_engine, tmp_path)
    session_factory = _session_factory(db_engine)
    # привязать адаптер к чату
    async with session_factory() as db:
        await crud.put_chat_lora_config(
            db, chat.id, schemas.ChatLoRAConfig(enabled=True, adapter_id=adapter_id)
        )

    async with await _make_client(session_factory) as client:
        resp = await client.delete(f"/lora/{adapter_id}")
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["message"]
        assert {"chat_id": chat.id, "name": chat.name} in detail["chats"]

        # регистрация осталась в registry
        lst = await client.get("/lora")
        assert [a["id"] for a in lst.json()] == [adapter_id]
    # файл цел
    assert os.path.exists(gguf_path)


async def test_delete_missing_adapter_404(db_engine):
    session_factory = _session_factory(db_engine)
    async with await _make_client(session_factory) as client:
        resp = await client.delete("/lora/999999")
        assert resp.status_code == 404


# ------------------------------ Chat config: GET ------------------------------


async def test_get_chat_lora_config_default(db_engine):
    chat_id = await _seed_chat(db_engine)
    session_factory = _session_factory(db_engine)
    async with await _make_client(session_factory) as client:
        resp = await client.get(f"/chats/{chat_id}/lora")
        assert resp.status_code == 200
        assert resp.json() == {"enabled": False, "adapter_id": None}


async def test_get_chat_lora_config_missing_chat_404(db_engine):
    session_factory = _session_factory(db_engine)
    async with await _make_client(session_factory) as client:
        resp = await client.get("/chats/999999/lora")
        assert resp.status_code == 404


# ------------------------------ Chat config: PUT ------------------------------


async def test_put_chat_lora_config_enable_with_adapter(db_engine, tmp_path):
    chat_id = await _seed_chat(db_engine)
    adapter_id, _ = await _seed_adapter(db_engine, tmp_path)
    session_factory = _session_factory(db_engine)
    async with await _make_client(session_factory) as client:
        resp = await client.put(
            f"/chats/{chat_id}/lora",
            json={"enabled": True, "adapter_id": adapter_id},
        )
        assert resp.status_code == 200
        assert resp.json() == {"enabled": True, "adapter_id": adapter_id}

        # настройки фронта грузятся одним GET
        fetched = await client.get(f"/chats/{chat_id}/lora")
        assert fetched.json() == {"enabled": True, "adapter_id": adapter_id}


async def test_put_chat_lora_config_enabled_true_null_adapter(db_engine):
    """enabled=true + adapter_id=null — допустимое состояние (§2.4)."""
    chat_id = await _seed_chat(db_engine)
    session_factory = _session_factory(db_engine)
    async with await _make_client(session_factory) as client:
        resp = await client.put(
            f"/chats/{chat_id}/lora",
            json={"enabled": True, "adapter_id": None},
        )
        assert resp.status_code == 200
        assert resp.json() == {"enabled": True, "adapter_id": None}

        fetched = await client.get(f"/chats/{chat_id}/lora")
        assert fetched.json() == {"enabled": True, "adapter_id": None}


async def test_put_chat_lora_config_disable(db_engine, tmp_path):
    chat_id = await _seed_chat(db_engine)
    adapter_id, _ = await _seed_adapter(db_engine, tmp_path)
    session_factory = _session_factory(db_engine)
    async with await _make_client(session_factory) as client:
        await client.put(
            f"/chats/{chat_id}/lora",
            json={"enabled": True, "adapter_id": adapter_id},
        )
        resp = await client.put(
            f"/chats/{chat_id}/lora",
            json={"enabled": False, "adapter_id": None},
        )
        assert resp.status_code == 200
        assert resp.json() == {"enabled": False, "adapter_id": None}
        fetched = await client.get(f"/chats/{chat_id}/lora")
        assert fetched.json() == {"enabled": False, "adapter_id": None}


async def test_put_chat_lora_config_atomic_swap(db_engine, tmp_path):
    """Атомарная замена: смена адаптера не оставляет половинчатую конфигурацию."""
    chat_id = await _seed_chat(db_engine)
    a_id, _ = await _seed_adapter(db_engine, tmp_path, name="Alpha")
    b_id, _ = await _seed_adapter(db_engine, tmp_path, name="Beta")
    session_factory = _session_factory(db_engine)
    async with await _make_client(session_factory) as client:
        await client.put(
            f"/chats/{chat_id}/lora",
            json={"enabled": True, "adapter_id": a_id},
        )
        resp = await client.put(
            f"/chats/{chat_id}/lora",
            json={"enabled": True, "adapter_id": b_id},
        )
        assert resp.status_code == 200
        fetched = await client.get(f"/chats/{chat_id}/lora")
        assert fetched.json() == {"enabled": True, "adapter_id": b_id}
    # ровно одна связка на чат (UNIQUE), старый адаптер больше не используется
    async with session_factory() as db:
        assert await crud.list_adapter_usage_chats(db, a_id) == []
        assert await crud.list_adapter_usage_chats(db, b_id) == [(chat_id, "Test Chat")]


async def test_put_chat_lora_config_invalid_adapter_422(db_engine):
    chat_id = await _seed_chat(db_engine)
    session_factory = _session_factory(db_engine)
    async with await _make_client(session_factory) as client:
        resp = await client.put(
            f"/chats/{chat_id}/lora",
            json={"enabled": True, "adapter_id": 999999},
        )
        assert resp.status_code == 422
        assert "не найден" in resp.json()["detail"]


async def test_put_chat_lora_config_missing_chat_404(db_engine):
    session_factory = _session_factory(db_engine)
    async with await _make_client(session_factory) as client:
        resp = await client.put(
            "/chats/999999/lora", json={"enabled": True, "adapter_id": None}
        )
        assert resp.status_code == 404
