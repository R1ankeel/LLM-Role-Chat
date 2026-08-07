"""LoRA Sprint 1 — модель данных, миграции и CRUD (Plans/LoRA.md §3 Sprint 1).

Маппинг на тест-матрицу (ТЗ §36: 1–13):

| № | Покрытие |
|---|----------|
| 1–4 | БД: таблицы, UNIQUE(chat_id), миграция/backfill идемпотентна, lora_enabled=false по умолчанию |
| 5–13 | CRUD: create/update/delete, валидация пути (§2.7), атомарный PUT конфигурации, delete с usage → 409 |

Ключевые требования Sprint 1:
- свежая и существующая БД мигрируются идемпотентно;
- в чате невозможен второй адаптер (UNIQUE(chat_id));
- невалидный путь не принимается (422-эквивалент — ``LoRAValidationError``);
- ``DELETE`` удаляет ТОЛЬКО регистрацию, физический файл на диске не трогается.
"""

from __future__ import annotations

import hashlib
import os
import struct
import tempfile

import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.models  # noqa: F401
from app import crud, models, schemas
from app.database import Base, ensure_schema
from app.lora_validation import LoRAInUseError, LoRAValidationError

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


# ------------------------------ БД / миграции ------------------------------


@pytest.fixture
def fresh_db(tmp_path):
    """Свежая БД: create_all + ensure_schema (аналог init_db)."""
    tmp = tmp_path / "fresh.db"
    engine = create_engine(f"sqlite:///{tmp}")
    Base.metadata.create_all(engine)
    ensure_schema(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def test_lora_tables_and_constraints_exist(fresh_db):
    insp = inspect(fresh_db)
    assert insp.has_table("lora_adapters")
    assert insp.has_table("chat_lora_adapters")
    # UNIQUE(chat_id) — не более одного адаптера на чат (MVP §2.5)
    chat_lora_pk = insp.get_pk_constraint("chat_lora_adapters")
    unique_ixs = [ix["column_names"] for ix in insp.get_indexes("chat_lora_adapters")]
    uniques = [ix["column_names"] for ix in insp.get_unique_constraints("chat_lora_adapters")]
    assert chat_lora_pk["constrained_columns"] == ["id"]
    assert ["chat_id"] in uniques or ["chat_id"] in unique_ixs
    # индекс по FK adapter_id
    assert any(cols == ["adapter_id"] for cols in unique_ixs + [c["column_names"] for c in insp.get_indexes("chat_lora_adapters")])


def test_lora_adapters_columns(fresh_db):
    cols = {c["name"] for c in inspect(fresh_db).get_columns("lora_adapters")}
    expected = {
        "id", "name", "path", "format", "base_model", "base_model_identity",
        "enabled", "description", "source", "metadata", "sha256",
        "created_at", "updated_at",
    }
    assert expected <= cols
    # weight/order_index НЕ создаются (MVP §2.5)
    assert "weight" not in cols
    assert "order_index" not in cols


async def test_chat_lora_enabled_default_false(db_session):
    """Новый чат получает lora_enabled=false (модель + fresh create_all)."""
    db_chat = await crud.create_chat(
        db_session, schemas.ChatCreate(name="Чат 1", general_prompt="")
    )
    assert db_chat.lora_enabled is False


def test_existing_db_migrates_idempotently(tmp_path):
    """«Прод»-БД без LoRA-таблиц и без lora_enabled мигрируется без дата-потерь."""
    tmp = tmp_path / "prod.db"
    engine = create_engine(f"sqlite:///{tmp}")
    with engine.begin() as conn:
        # минимальная «прод»-схема (аналог tests/test_sprint1_schema.py):
        # ensure_schema безусловно инспектирует memories/characters/messages/chats
        # и создаёт индексы по created_at — колонки нужны в минимальных таблицах
        conn.execute(text("CREATE TABLE chats (id INTEGER PRIMARY KEY, name TEXT, created_at DATETIME)"))
        conn.execute(
            text("CREATE TABLE characters (id INTEGER PRIMARY KEY, chat_id INTEGER, "
                 "name TEXT, personality TEXT, traits TEXT, is_player INTEGER, "
                 "order_index INTEGER, created_at DATETIME)")
        )
        conn.execute(
            text("CREATE TABLE messages (id INTEGER PRIMARY KEY, chat_id INTEGER, character_id INTEGER, role TEXT, content TEXT, timestamp DATETIME)")
        )
        conn.execute(
            text("CREATE TABLE memories (id INTEGER PRIMARY KEY, chat_id INTEGER, character_id INTEGER, content TEXT, importance REAL, created_at DATETIME)")
        )
        conn.execute(text("INSERT INTO chats (id, name, created_at) VALUES (1, 'Старый чат', CURRENT_TIMESTAMP)"))
    ensure_schema(engine)
    ensure_schema(engine)  # повторный запуск безопасен

    insp = inspect(engine)
    assert insp.has_table("lora_adapters")
    assert insp.has_table("chat_lora_adapters")
    cols = {c["name"] for c in insp.get_columns("chats")}
    assert "lora_enabled" in cols
    with engine.connect() as conn:
        row = conn.execute(text("SELECT id, name, lora_enabled FROM chats WHERE id = 1")).fetchone()
        assert row is not None
        assert row.name == "Старый чат"
        assert row.lora_enabled == 0  # backfill: существующие чаты получают false
    engine.dispose()


# ------------------------------ CRUD: валидация пути ------------------------------


@pytest.mark.parametrize(
    "bad_path",
    [
        "relative/path.gguf",  # не абсолютный
        # абсолютный, но файл не существует (unix-абсолют + win-абсолют)
        "/definitely/missing.gguf",
        "C:/definitely/missing.gguf",
    ],
)
async def test_create_adapter_rejects_bad_path(db_session, bad_path):
    with pytest.raises(LoRAValidationError):
        await crud.create_lora_adapter(
            db_session,
            schemas.LoRAAdapterCreate(name="A", path=bad_path, format="auto"),
        )


async def test_create_adapter_rejects_directory(db_session, tmp_path):
    with pytest.raises(LoRAValidationError):
        await crud.create_lora_adapter(
            db_session,
            schemas.LoRAAdapterCreate(name="A", path=str(tmp_path), format="auto"),
        )


async def test_create_adapter_rejects_non_gguf(db_session, tmp_path):
    bad = tmp_path / "not_lora.gguf"
    bad.write_bytes(b"<html>not a model</html>")
    with pytest.raises(LoRAValidationError) as exc_info:
        await crud.create_lora_adapter(
            db_session,
            schemas.LoRAAdapterCreate(name="A", path=str(bad), format="auto"),
        )
    assert "GGUF" in str(exc_info.value)


async def test_create_adapter_rejects_safetensors(db_session, tmp_path):
    """safetensors → ошибка (supports_safetensors=false, §2.5)."""
    safetensors = tmp_path / "adapter.safetensors"
    safetensors.write_bytes(b"\x00" * 100)
    with pytest.raises(LoRAValidationError) as exc_info:
        await crud.create_lora_adapter(
            db_session,
            schemas.LoRAAdapterCreate(
                name="A", path=str(safetensors), format="safetensors"
            ),
        )
    assert "safetensors" in str(exc_info.value).lower()


async def test_create_adapter_rejects_truncated_gguf(db_session, tmp_path):
    truncated = tmp_path / "truncated.gguf"
    truncated.write_bytes(b"GGUF" + b"\x00" * 4)  # магия есть, заголовок обрезан
    with pytest.raises(LoRAValidationError):
        await crud.create_lora_adapter(
            db_session,
            schemas.LoRAAdapterCreate(name="A", path=str(truncated), format="auto"),
        )


# ------------------------------ CRUD: create / list / get ------------------------------


async def test_create_adapter_valid_gguf(db_session, tmp_path):
    gguf_path = make_gguf(str(tmp_path / "dark.gguf"))
    adapter = await crud.create_lora_adapter(
        db_session,
        schemas.LoRAAdapterCreate(
            name="Dark Goetia RU",
            path=gguf_path,
            format="auto",
            base_model="goetia-26b",
            base_model_identity="Naphula/Goetia-26B-A4B-v1.3-Absolute-Heretic-ARA",
            description="RU-адаптер",
            metadata={"version": 1, "source": "test"},
        ),
    )
    assert adapter.id > 0
    assert adapter.name == "Dark Goetia RU"
    assert adapter.path == gguf_path
    assert adapter.format == "gguf"  # auto → фактический формат
    assert adapter.base_model_identity == "Naphula/Goetia-26B-A4B-v1.3-Absolute-Heretic-ARA"
    assert adapter.sha256 == sha256_of(gguf_path)
    assert adapter.enabled is True
    # metadata (JSON) читается как dict через LoRAAdapterRead
    read = schemas.LoRAAdapterRead.model_validate(adapter)
    assert read.metadata == {"version": 1, "source": "test"}


async def test_list_and_get_adapters(db_session, tmp_path):
    a1 = await crud.create_lora_adapter(
        db_session,
        schemas.LoRAAdapterCreate(
            name="Alpha", path=make_gguf(str(tmp_path / "alpha.gguf"))
        ),
    )
    a2 = await crud.create_lora_adapter(
        db_session,
        schemas.LoRAAdapterCreate(
            name="Beta", path=make_gguf(str(tmp_path / "beta.gguf"))
        ),
    )
    all_adapters = await crud.list_lora_adapters(db_session)
    ids = {a.id for a in all_adapters}
    assert {a1.id, a2.id} <= ids

    fetched = await crud.get_lora_adapter(db_session, a2.id)
    assert fetched is not None and fetched.name == "Beta"
    assert await crud.get_lora_adapter(db_session, 999999) is None


# ------------------------------ CRUD: update ------------------------------


async def test_update_adapter_metadata_only_no_revalidation(db_session, tmp_path):
    gguf_path = make_gguf(str(tmp_path / "a.gguf"))
    adapter = await crud.create_lora_adapter(
        db_session, schemas.LoRAAdapterCreate(name="A", path=gguf_path)
    )
    updated = await crud.update_lora_adapter(
        db_session,
        adapter.id,
        schemas.LoRAAdapterUpdate(name="A v2", description="new desc"),
    )
    assert updated is not None
    assert updated.name == "A v2"
    assert updated.description == "new desc"
    assert updated.sha256 == adapter.sha256  # путь не менялся — sha256 не пересчитывался


async def test_update_adapter_path_revalidates(db_session, tmp_path):
    old_path = make_gguf(str(tmp_path / "old.gguf"))
    new_path = make_gguf(str(tmp_path / "new.gguf"), payload=b"different-content")
    adapter = await crud.create_lora_adapter(
        db_session, schemas.LoRAAdapterCreate(name="A", path=old_path)
    )
    updated = await crud.update_lora_adapter(
        db_session,
        adapter.id,
        schemas.LoRAAdapterUpdate(path=new_path),
    )
    assert updated is not None
    assert updated.path == new_path
    assert updated.sha256 == sha256_of(new_path)  # sha256 пересчитан
    assert updated.sha256 != sha256_of(old_path)


async def test_update_adapter_invalid_path_keeps_old(db_session, tmp_path):
    old_path = make_gguf(str(tmp_path / "old.gguf"))
    adapter = await crud.create_lora_adapter(
        db_session, schemas.LoRAAdapterCreate(name="A", path=old_path)
    )
    with pytest.raises(LoRAValidationError):
        await crud.update_lora_adapter(
            db_session, adapter.id, schemas.LoRAAdapterUpdate(path="relative.gguf")
        )
    refreshed = await crud.get_lora_adapter(db_session, adapter.id)
    assert refreshed is not None
    assert refreshed.path == old_path  # старый путь сохранён


async def test_update_missing_adapter_returns_none(db_session, tmp_path):
    updated = await crud.update_lora_adapter(
        db_session, 999999, schemas.LoRAAdapterUpdate(name="X")
    )
    assert updated is None


# ------------------------------ CRUD: delete (только регистрация) ------------------------------


async def test_delete_unused_adapter_keeps_file(db_session, tmp_path):
    gguf_path = make_gguf(str(tmp_path / "to_delete.gguf"))
    adapter = await crud.create_lora_adapter(
        db_session, schemas.LoRAAdapterCreate(name="A", path=gguf_path)
    )
    assert os.path.exists(gguf_path)
    deleted = await crud.delete_lora_adapter(db_session, adapter.id)
    assert deleted is True
    assert await crud.get_lora_adapter(db_session, adapter.id) is None
    # физический файл пользователя НЕ удаляется (§2.7, задача 8)
    assert os.path.exists(gguf_path)


async def test_delete_used_adapter_raises_in_use(db_session, chat, tmp_path):
    gguf_path = make_gguf(str(tmp_path / "used.gguf"))
    adapter = await crud.create_lora_adapter(
        db_session, schemas.LoRAAdapterCreate(name="Used", path=gguf_path)
    )
    await crud.put_chat_lora_config(
        db_session, chat.id, schemas.ChatLoRAConfig(enabled=True, adapter_id=adapter.id)
    )
    with pytest.raises(LoRAInUseError) as exc_info:
        await crud.delete_lora_adapter(db_session, adapter.id)
    # 409 со списком чатов
    assert exc_info.value.chats == [(chat.id, chat.name)]
    assert "Используется чатами" in str(exc_info.value) or "используется" in str(exc_info.value).lower()
    # адаптер остался в registry
    assert await crud.get_lora_adapter(db_session, adapter.id) is not None
    # файл цел
    assert os.path.exists(gguf_path)


async def test_delete_missing_adapter_returns_false(db_session):
    assert await crud.delete_lora_adapter(db_session, 999999) is False


# ------------------------------ CRUD: конфигурация чата ------------------------------


async def test_get_chat_lora_config_default(db_session, chat):
    config = await crud.get_chat_lora_config(db_session, chat.id)
    assert config is not None
    assert config.enabled is False
    assert config.adapter_id is None


async def test_get_chat_lora_config_missing_chat_returns_none(db_session):
    assert await crud.get_chat_lora_config(db_session, 999999) is None


async def test_put_chat_lora_config_enable_with_adapter(db_session, chat, tmp_path):
    gguf_path = make_gguf(str(tmp_path / "a.gguf"))
    adapter = await crud.create_lora_adapter(
        db_session, schemas.LoRAAdapterCreate(name="A", path=gguf_path)
    )
    config = await crud.put_chat_lora_config(
        db_session, chat.id, schemas.ChatLoRAConfig(enabled=True, adapter_id=adapter.id)
    )
    assert config.enabled is True
    assert config.adapter_id == adapter.id

    fetched = await crud.get_chat_lora_config(db_session, chat.id)
    assert fetched is not None
    assert fetched.enabled is True
    assert fetched.adapter_id == adapter.id

    link = await crud.get_chat_lora_adapter(db_session, chat.id)
    assert link is not None and link.adapter_id == adapter.id


async def test_put_chat_lora_config_enabled_true_null_adapter(db_session, chat):
    """enabled=true + adapter_id=null — допустимое состояние (§2.4)."""
    config = await crud.put_chat_lora_config(
        db_session, chat.id, schemas.ChatLoRAConfig(enabled=True, adapter_id=None)
    )
    assert config.enabled is True
    assert config.adapter_id is None
    fetched = await crud.get_chat_lora_config(db_session, chat.id)
    assert fetched.enabled is True
    assert fetched.adapter_id is None
    assert await crud.get_chat_lora_adapter(db_session, chat.id) is None


async def test_put_chat_lora_config_atomic_swap(db_session, chat, tmp_path):
    """Атомарная замена: смена адаптера не оставляет два линка (UNIQUE)."""
    a = await crud.create_lora_adapter(
        db_session, schemas.LoRAAdapterCreate(name="A", path=make_gguf(str(tmp_path / "a.gguf")))
    )
    b = await crud.create_lora_adapter(
        db_session, schemas.LoRAAdapterCreate(name="B", path=make_gguf(str(tmp_path / "b.gguf")))
    )
    await crud.put_chat_lora_config(
        db_session, chat.id, schemas.ChatLoRAConfig(enabled=True, adapter_id=a.id)
    )
    await crud.put_chat_lora_config(
        db_session, chat.id, schemas.ChatLoRAConfig(enabled=True, adapter_id=b.id)
    )
    fetched = await crud.get_chat_lora_config(db_session, chat.id)
    assert fetched.adapter_id == b.id
    # ровно одна связка на чат
    result = await db_session.execute(
        select(models.ChatLoRAAdapter).where(models.ChatLoRAAdapter.chat_id == chat.id)
    )
    links = list(result.scalars().all())
    assert len(links) == 1 and links[0].adapter_id == b.id
    # адаптер A больше не используется
    assert await crud.list_adapter_usage_chats(db_session, a.id) == []
    assert await crud.list_adapter_usage_chats(db_session, b.id) == [(chat.id, chat.name)]


async def test_put_chat_lora_config_invalid_adapter(db_session, chat):
    with pytest.raises(LoRAValidationError):
        await crud.put_chat_lora_config(
            db_session, chat.id, schemas.ChatLoRAConfig(enabled=True, adapter_id=999999)
        )


async def test_put_chat_lora_config_missing_chat(db_session, tmp_path):
    gguf_path = make_gguf(str(tmp_path / "a.gguf"))
    adapter = await crud.create_lora_adapter(
        db_session, schemas.LoRAAdapterCreate(name="A", path=gguf_path)
    )
    with pytest.raises(LoRAValidationError):
        await crud.put_chat_lora_config(
            db_session, 999999, schemas.ChatLoRAConfig(enabled=True, adapter_id=adapter.id)
        )


async def test_unique_chat_lora_adapter_violation(db_session, chat, tmp_path):
    """Второй адаптер на тот же чат физически невозможен (UNIQUE(chat_id))."""
    a = await crud.create_lora_adapter(
        db_session, schemas.LoRAAdapterCreate(name="A", path=make_gguf(str(tmp_path / "a.gguf")))
    )
    b = await crud.create_lora_adapter(
        db_session, schemas.LoRAAdapterCreate(name="B", path=make_gguf(str(tmp_path / "b.gguf")))
    )
    db_session.add(models.ChatLoRAAdapter(chat_id=chat.id, adapter_id=a.id))
    await db_session.commit()
    with pytest.raises(IntegrityError):
        db_session.add(models.ChatLoRAAdapter(chat_id=chat.id, adapter_id=b.id))
        await db_session.commit()


async def test_chat_delete_cascades_lora_link(db_engine, tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    async_session = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    async with async_session() as db:
        chat = await crud.create_chat(db, schemas.ChatCreate(name="C"))
        gguf_path = make_gguf(str(tmp_path / "a.gguf"))
        adapter = await crud.create_lora_adapter(
            db, schemas.LoRAAdapterCreate(name="A", path=gguf_path)
        )
        await crud.put_chat_lora_config(
            db, chat.id, schemas.ChatLoRAConfig(enabled=True, adapter_id=adapter.id)
        )
        assert await crud.get_chat_lora_adapter(db, chat.id) is not None
        await crud.delete_chat(db, chat.id)
        assert await crud.get_chat_lora_adapter(db, chat.id) is None
        # registry не затронут
        assert await crud.get_lora_adapter(db, adapter.id) is not None
