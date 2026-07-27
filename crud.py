"""CRUD-функции для работы с базой данных."""

from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

import models
import schemas


# ------------------------------ Chat ------------------------------
def create_chat(db: Session, chat: schemas.ChatCreate) -> models.Chat:
    db_chat = models.Chat(**chat.model_dump())
    db.add(db_chat)
    db.commit()
    db.refresh(db_chat)
    return db_chat


def get_chat(db: Session, chat_id: int) -> models.Chat | None:
    return db.get(models.Chat, chat_id)


def get_chats(db: Session, skip: int = 0, limit: int = 100) -> list[models.Chat]:
    stmt = (
        select(models.Chat)
        .order_by(models.Chat.created_at.desc(), models.Chat.id.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(db.scalars(stmt).all())


def update_chat(
    db: Session, chat_id: int, chat_update: schemas.ChatUpdate
) -> models.Chat | None:
    db_chat = get_chat(db, chat_id)
    if db_chat is None:
        return None
    for field, value in chat_update.model_dump(exclude_unset=True).items():
        setattr(db_chat, field, value)
    db.commit()
    db.refresh(db_chat)
    return db_chat


def delete_chat(db: Session, chat_id: int) -> bool:
    db_chat = get_chat(db, chat_id)
    if db_chat is None:
        return False
    db.delete(db_chat)
    db.commit()
    return True


def clear_chat_messages(db: Session, chat_id: int) -> bool:
    """Delete all messages for a chat. Returns False if chat not found."""
    if get_chat(db, chat_id) is None:
        return False
    db.execute(delete(models.Message).where(models.Message.chat_id == chat_id))
    db.commit()
    return True


# ---------------------------- Character ----------------------------
def _order_index_taken(
    db: Session, chat_id: int, order_index: int, exclude_id: int | None = None
) -> bool:
    stmt = select(models.Character).where(
        models.Character.chat_id == chat_id,
        models.Character.order_index == order_index,
    )
    if exclude_id is not None:
        stmt = stmt.where(models.Character.id != exclude_id)
    return db.scalars(stmt).first() is not None


def create_character(
    db: Session, chat_id: int, character: schemas.CharacterCreate
) -> models.Character:
    if _order_index_taken(db, chat_id, character.order_index):
        raise ValueError(
            f"order_index={character.order_index} уже занят в этом чате"
        )
    db_character = models.Character(chat_id=chat_id, **character.model_dump())
    db.add(db_character)
    db.commit()
    db.refresh(db_character)
    return db_character


def get_character(db: Session, character_id: int) -> models.Character | None:
    return db.get(models.Character, character_id)


def get_characters_by_chat(db: Session, chat_id: int) -> list[models.Character]:
    stmt = (
        select(models.Character)
        .where(models.Character.chat_id == chat_id)
        .order_by(models.Character.order_index, models.Character.id)
    )
    return list(db.scalars(stmt).all())


def update_character(
    db: Session, character_id: int, character_update: schemas.CharacterUpdate
) -> models.Character | None:
    db_character = get_character(db, character_id)
    if db_character is None:
        return None
    update_data = character_update.model_dump(exclude_unset=True)
    new_index = update_data.get("order_index")
    if new_index is not None and new_index != db_character.order_index:
        if _order_index_taken(
            db, db_character.chat_id, new_index, exclude_id=db_character.id
        ):
            raise ValueError(f"order_index={new_index} уже занят в этом чате")
    for field, value in update_data.items():
        setattr(db_character, field, value)
    db.commit()
    db.refresh(db_character)
    return db_character


def delete_character(db: Session, character_id: int) -> bool:
    db_character = get_character(db, character_id)
    if db_character is None:
        return False
    db.delete(db_character)
    db.commit()
    return True


# ----------------------------- Message -----------------------------
def create_message(db: Session, message: schemas.MessageCreate) -> models.Message:
    db_message = models.Message(**message.model_dump())
    db.add(db_message)
    db.commit()
    db.refresh(db_message)
    return db_message


def get_messages_by_chat(
    db: Session, chat_id: int, limit: int | None = None
) -> list[models.Message]:
    stmt = select(models.Message).where(models.Message.chat_id == chat_id)
    if limit is None:
        stmt = stmt.order_by(models.Message.timestamp, models.Message.id)
        return list(db.scalars(stmt).all())
    stmt = stmt.order_by(
        models.Message.timestamp.desc(), models.Message.id.desc()
    ).limit(limit)
    messages = list(db.scalars(stmt).all())
    messages.reverse()
    return messages


def get_messages_paginated(
    db: Session, chat_id: int, limit: int = 50, offset: int = 0
) -> list[models.Message]:
    stmt = (
        select(models.Message)
        .where(models.Message.chat_id == chat_id)
        .order_by(models.Message.timestamp, models.Message.id)
        .offset(offset)
        .limit(limit)
    )
    return list(db.scalars(stmt).all())


# ----------------------------- Memory ------------------------------
MAX_MEMORIES_PER_CHARACTER = 20


def _memory_exists(db: Session, character_id: int, content: str) -> bool:
    stmt = select(models.Memory).where(
        models.Memory.character_id == character_id,
        models.Memory.content.like(f"%{content}%"),
    )
    return db.scalars(stmt).first() is not None


def create_memory(db: Session, memory: schemas.MemoryCreate) -> models.Memory:
    if _memory_exists(db, memory.character_id, memory.content):
        return None
    db_memory = models.Memory(**memory.model_dump())
    db.add(db_memory)
    db.commit()
    db.refresh(db_memory)
    return db_memory


def _count_memories_for_character(db: Session, character_id: int) -> int:
    stmt = (
        select(func.count())
        .select_from(models.Memory)
        .where(models.Memory.character_id == character_id)
    )
    return db.scalar(stmt) or 0


def _delete_oldest_memories(db: Session, character_id: int, keep_count: int) -> None:
    stmt = (
        select(models.Memory.id)
        .where(models.Memory.character_id == character_id)
        .order_by(models.Memory.created_at, models.Memory.id)
    )
    ids = list(db.scalars(stmt).all())
    if len(ids) <= keep_count:
        return
    delete_ids = ids[: len(ids) - keep_count]
    delete_stmt = delete(models.Memory).where(models.Memory.id.in_(delete_ids))
    db.execute(delete_stmt)
    db.commit()


def ensure_memory_limit(db: Session, character_id: int) -> None:
    count = _count_memories_for_character(db, character_id)
    if count > MAX_MEMORIES_PER_CHARACTER:
        _delete_oldest_memories(db, character_id, MAX_MEMORIES_PER_CHARACTER)


def get_memories_by_character(
    db: Session, character_id: int, limit: int | None = None
) -> list[models.Memory]:
    stmt = (
        select(models.Memory)
        .where(models.Memory.character_id == character_id)
        .order_by(models.Memory.created_at.desc(), models.Memory.id.desc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    result = list(db.scalars(stmt).all())
    result.reverse()
    return result


def delete_memory(db: Session, memory_id: int) -> bool:
    db_memory = db.get(models.Memory, memory_id)
    if db_memory is None:
        return False
    db.delete(db_memory)
    db.commit()
    return True