"""Чат CRUD: create/get/list/update/delete + clear-операции (Sprint 4)."""



from __future__ import annotations



from sqlalchemy import delete, select

from sqlalchemy.ext.asyncio import AsyncSession

from .. import models

from .. import schemas

from .characters import _sync_player_character_location, get_characters_by_chat

# ------------------------------ Chat ------------------------------
async def create_chat(db: AsyncSession, chat: schemas.ChatCreate) -> models.Chat:
    # player_name не является колонкой Chat — он уходит на именование
    # автоматически создаваемого player-персонажа (см. routers/chats.py).
    db_chat = models.Chat(**chat.model_dump(exclude={"player_name"}))
    db.add(db_chat)
    await db.commit()
    await db.refresh(db_chat)
    return db_chat

async def get_chat(db: AsyncSession, chat_id: int) -> models.Chat | None:
    return await db.get(models.Chat, chat_id)

async def get_chats(db: AsyncSession, skip: int = 0, limit: int = 100) -> list[models.Chat]:
    stmt = (
        select(models.Chat)
        .order_by(models.Chat.created_at.desc(), models.Chat.id.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())

async def update_chat(
    db: AsyncSession, chat_id: int, chat_update: schemas.ChatUpdate
) -> models.Chat | None:
    db_chat = await get_chat(db, chat_id)
    if db_chat is None:
        return None
    update_data = chat_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_chat, field, value)
    if "player_location" in update_data:
        await _sync_player_character_location(
            db, db_chat, update_data["player_location"]
        )
    await db.commit()
    await db.refresh(db_chat)
    return db_chat

async def delete_chat(db: AsyncSession, chat_id: int) -> bool:
    db_chat = await get_chat(db, chat_id)
    if db_chat is None:
        return False
    await db.delete(db_chat)
    await db.commit()
    return True

async def clear_chat_messages(db: AsyncSession, chat_id: int) -> bool:
    """Delete all messages for a chat. Returns False if chat not found."""
    if await get_chat(db, chat_id) is None:
        return False
    await db.execute(delete(models.Message).where(models.Message.chat_id == chat_id))
    await db.commit()
    return True

async def clear_chat_memories(db: AsyncSession, chat_id: int) -> bool:
    """Delete all memories for all characters in a chat. Returns False if chat not found."""
    if await get_chat(db, chat_id) is None:
        return False
    character_ids = [c.id for c in await get_characters_by_chat(db, chat_id)]
    if character_ids:
        await db.execute(
            delete(models.Memory).where(models.Memory.character_id.in_(character_ids))
        )
    await db.commit()
    return True

async def clear_chat_relationships(db: AsyncSession, chat_id: int) -> None:
    """Delete all relationship data (edges, events, issues) for a chat."""
    rel_ids = select(models.CharacterRelationship.id).where(
        models.CharacterRelationship.chat_id == chat_id
    )
    await db.execute(
        delete(models.RelationshipEvent).where(
            models.RelationshipEvent.relationship_id.in_(rel_ids)
        )
    )
    await db.execute(
        delete(models.RelationshipIssue).where(
            models.RelationshipIssue.relationship_id.in_(rel_ids)
        )
    )
    await db.execute(
        delete(models.CharacterRelationship).where(
            models.CharacterRelationship.chat_id == chat_id
        )
    )
    await db.commit()

async def clear_chat_world_events(db: AsyncSession, chat_id: int) -> None:
    """Delete all world events (WPE journal) for a chat."""
    await db.execute(
        delete(models.WorldEvent).where(models.WorldEvent.chat_id == chat_id)
    )
    await db.commit()

async def clear_chat_threads(db: AsyncSession, chat_id: int) -> None:
    """Delete all threads (and participant states) for a chat."""
    thread_ids = select(models.Thread.id).where(models.Thread.chat_id == chat_id)
    await db.execute(
        delete(models.ThreadParticipantState).where(
            models.ThreadParticipantState.thread_id.in_(thread_ids)
        )
    )
    await db.execute(delete(models.Thread).where(models.Thread.chat_id == chat_id))
    await db.commit()

async def clear_chat_memory_jobs(db: AsyncSession, chat_id: int) -> None:
    """Delete all memory processing jobs for a chat."""
    await db.execute(
        delete(models.MemoryJob).where(models.MemoryJob.chat_id == chat_id)
    )
    await db.commit()
