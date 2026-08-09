"""LoRA adapters + chat lora config (Sprint 4)."""



from __future__ import annotations



import json

import logging

from typing import Any

from sqlalchemy import select, update

from sqlalchemy.exc import IntegrityError

from sqlalchemy.ext.asyncio import AsyncSession

from .. import models

from .. import schemas

from ..lora_validation import LoRAInUseError, LoRAValidationError, validate_adapter_path



logger = logging.getLogger(__name__)

def _normalize_lora_metadata(metadata: Any) -> str:
    """metadata (dict) → JSON-строка для колонки `metadata`."""
    if metadata is None:
        return "{}"
    if isinstance(metadata, str):
        return metadata
    return json.dumps(metadata, ensure_ascii=False)

async def list_lora_adapters(
    db: AsyncSession, skip: int = 0, limit: int = 100
) -> list[models.LoRAAdapter]:
    """Все зарегистрированные адаптеры (registry, §2.6)."""
    stmt = (
        select(models.LoRAAdapter)
        .order_by(models.LoRAAdapter.created_at.desc(), models.LoRAAdapter.id.desc())
        .offset(max(0, int(skip)))
        .limit(max(0, int(limit)))
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())

async def get_lora_adapter(
    db: AsyncSession, adapter_id: int
) -> models.LoRAAdapter | None:
    """Адаптер по id (registry)."""
    return await db.get(models.LoRAAdapter, adapter_id)

async def create_lora_adapter(
    db: AsyncSession, create: schemas.LoRAAdapterCreate
) -> models.LoRAAdapter:
    """Регистрация адаптера: валидация пути (§2.7) + запись.

    ``sha256`` содержимого файла вычисляется при регистрации (blob-диджест
    §2.2); ``format`` нормализуется в фактически определённый ("gguf").
    Физический файл не копируется и не удаляется.
    """
    file_info = validate_adapter_path(create.path, create.format)
    adapter = models.LoRAAdapter(
        name=create.name.strip(),
        path=create.path,
        format=file_info.detected_format,
        base_model=create.base_model or "",
        base_model_identity=create.base_model_identity,
        enabled=create.enabled,
        description=create.description or "",
        source=create.source or "",
        metadata_json=_normalize_lora_metadata(create.metadata),
        sha256=file_info.sha256,
    )
    db.add(adapter)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise LoRAValidationError(
            f"Не удалось сохранить регистрацию LoRA-адаптера: {exc}"
        ) from exc
    await db.refresh(adapter)
    logger.info(
        "lora_adapter_created id=%s name=%s sha256=%s",
        adapter.id,
        adapter.name,
        adapter.sha256,
    )
    return adapter

async def update_lora_adapter(
    db: AsyncSession,
    adapter_id: int,
    update: schemas.LoRAAdapterUpdate,
) -> models.LoRAAdapter | None:
    """Частичное обновление регистрации адаптера.

    При изменении ``path``/``format`` выполняется повторная валидация (§2.7) и
    пересчёт sha256. Ошибка валидации → ``LoRAValidationError``.
    """
    adapter = await get_lora_adapter(db, adapter_id)
    if adapter is None:
        return None
    data = update.model_dump(exclude_unset=True)

    path_changed = "path" in data or "format" in data
    if path_changed:
        new_path = data.get("path", adapter.path)
        new_format = data.get("format", adapter.format)
        file_info = validate_adapter_path(new_path, new_format)
        data["format"] = file_info.detected_format
        data["sha256"] = file_info.sha256

    if "metadata" in data:
        data["metadata_json"] = _normalize_lora_metadata(data["metadata"])
        del data["metadata"]
    if "name" in data and isinstance(data["name"], str):
        data["name"] = data["name"].strip()

    for field, value in data.items():
        setattr(adapter, field, value)
    await db.commit()
    await db.refresh(adapter)
    return adapter

async def get_chat_lora_adapter(
    db: AsyncSession, chat_id: int
) -> models.ChatLoRAAdapter | None:
    """Выбранный адаптер чата (не более одного, UNIQUE(chat_id))."""
    stmt = (
        select(models.ChatLoRAAdapter)
        .where(models.ChatLoRAAdapter.chat_id == chat_id)
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalars().first()

async def get_chat_lora_config(
    db: AsyncSession, chat_id: int
) -> schemas.ChatLoRAConfig | None:
    """Конфигурация LoRA чата (§2.6): {enabled, adapter_id}.

    ``None`` — чат не найден (роутер отдаёт 404). ``enabled=true`` +
    ``adapter_id=null`` — допустимое состояние (§2.4).
    """
    db_chat = await db.get(models.Chat, chat_id)
    if db_chat is None:
        return None
    link = await get_chat_lora_adapter(db, chat_id)
    return schemas.ChatLoRAConfig(
        enabled=bool(db_chat.lora_enabled),
        adapter_id=link.adapter_id if link else None,
    )

async def put_chat_lora_config(
    db: AsyncSession, chat_id: int, config: schemas.ChatLoRAConfig
) -> schemas.ChatLoRAConfig:
    """Атомарная замена конфигурации LoRA чата ({enabled, adapter_id}).

    - чат должен существовать (иначе ``LoRAValidationError`` → 422);
    - ссылка на несуществующий адаптер → ``LoRAValidationError`` (422);
    - UNIQUE(chat_id) гарантирует не более одной связки — «вставить/удалить
      единственную связь» одним транзакционным блоком;
    - ``enabled=true`` + ``adapter_id=null`` сохраняется как допустимое
      состояние (§2.4): пустая runtime-модель не создаётся.
    """
    db_chat = await db.get(models.Chat, chat_id)
    if db_chat is None:
        raise LoRAValidationError(f"Чат {chat_id} не найден")

    if config.adapter_id is not None:
        adapter = await get_lora_adapter(db, config.adapter_id)
        if adapter is None:
            raise LoRAValidationError(
                f"LoRA-адаптер {config.adapter_id} не найден"
            )

    existing = await get_chat_lora_adapter(db, chat_id)
    if config.adapter_id is None:
        # снять адаптер: удаляем единственную связку
        if existing is not None:
            await db.delete(existing)
    elif existing is not None:
        # атомарная замена: обновляем единственную связку на месте —
        # delete+insert одного chat_id упёрлось бы в UNIQUE(chat_id)
        existing.adapter_id = config.adapter_id
    else:
        db.add(models.ChatLoRAAdapter(chat_id=chat_id, adapter_id=config.adapter_id))
    db_chat.lora_enabled = bool(config.enabled)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise LoRAValidationError(
            f"Не удалось сохранить конфигурацию LoRA чата: {exc}"
        ) from exc
    return schemas.ChatLoRAConfig(
        enabled=bool(db_chat.lora_enabled),
        adapter_id=config.adapter_id,
    )

async def list_adapter_usage_chats(
    db: AsyncSession, adapter_id: int
) -> list[tuple[int, str]]:
    """Чаты, использующие адаптер: [(chat_id, name), ...] (для 409)."""
    stmt = (
        select(models.ChatLoRAAdapter.chat_id, models.Chat.name)
        .join(models.Chat, models.Chat.id == models.ChatLoRAAdapter.chat_id)
        .where(models.ChatLoRAAdapter.adapter_id == adapter_id)
        .order_by(models.Chat.id)
    )
    result = await db.execute(stmt)
    return [(int(chat_id), name or "") for chat_id, name in result.all()]

async def delete_lora_adapter(
    db: AsyncSession, adapter_id: int
) -> bool:
    """Удаляет ТОЛЬКО регистрацию адаптера из registry (§2.7).

    Физический файл пользователя НЕ затрагивается — он не удаляется ни при
    каком условии. Если адаптер используется хотя бы одним чатом →
    ``LoRAInUseError`` (роутер отдаёт 409 со списком чатов).
    """
    adapter = await get_lora_adapter(db, adapter_id)
    if adapter is None:
        return False
    usage = await list_adapter_usage_chats(db, adapter_id)
    if usage:
        chat_names = ", ".join(f"{chat_id} ({name})" for chat_id, name in usage)
        raise LoRAInUseError(
            f"LoRA-адаптер '{adapter.name}' используется чатами: {chat_names}",
            chats=usage,
        )
    await db.delete(adapter)
    await db.commit()
    return True
