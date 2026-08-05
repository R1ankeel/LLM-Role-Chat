"""CRUD-функции для работы с базой данных (Async)."""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass, field as dataclass_field
from datetime import datetime
from typing import Any, Literal, Optional, Tuple

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from . import embedding_service
from . import memory_service
from . import models
from . import perception
from . import schemas
from . import witness_model
from .config import settings
from .database import memory_content_hash

logger = logging.getLogger(__name__)


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


async def _sync_player_character_location(
    db: AsyncSession, db_chat: models.Chat, location: str
) -> None:
    """Sync the player character's ``location`` with ``chats.player_location``.

    The location of the player exists in two places — the chat (edited via the
    right panel "Локация игрока") and the player character's card (edited via
    the character card "Локация"). Both feed the LLM prompt (presence /
    isolation vs the scene block), so they must stay in sync. ``player_location``
    is the source of truth; the player character mirrors it.
    """
    player = await get_player_character(db, db_chat.id)
    if player is None:
        return
    player.location = location
    player.location_id = None
    if location.strip():
        locations = await get_chat_locations(db, db_chat.id)
        resolved = resolve_location_name(locations, location)
        if resolved is not None:
            player.location_id = resolved.id


async def _sync_chat_player_location(
    db: AsyncSession, db_character: models.Character
) -> None:
    """Sync ``chats.player_location`` with the player character's ``location``.

    Reverse direction of ``_sync_player_character_location``: when the player
    character's location is edited from the character card, the chat-level
    ``player_location`` (used for presence/isolation in prompts) follows it.
    """
    if not db_character.is_player:
        return
    db_chat = await get_chat(db, db_character.chat_id)
    if db_chat is None or db_chat.player_location == db_character.location:
        return
    db_chat.player_location = db_character.location


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


# ---------------------------- Character ----------------------------
async def _order_index_taken(
    db: AsyncSession, chat_id: int, order_index: int, exclude_id: int | None = None
) -> bool:
    stmt = select(models.Character).where(
        models.Character.chat_id == chat_id,
        models.Character.order_index == order_index,
    )
    if exclude_id is not None:
        stmt = stmt.where(models.Character.id != exclude_id)
    result = await db.execute(stmt)
    return result.scalars().first() is not None


async def create_character(
    db: AsyncSession, chat_id: int, character: schemas.CharacterCreate
) -> models.Character:
    if await _order_index_taken(db, chat_id, character.order_index):
        raise ValueError(
            f"order_index={character.order_index} уже занят в этом чате"
        )
    char_data = character.model_dump()
    initial_rels = char_data.pop("initial_relationships", [])
    char_data.pop("is_player", None)  # prevent setting is_player from API
    # avatar грузится только через upload endpoint, а не при создании
    char_data.pop("avatar_url", None)
    char_data.pop("avatar_crop", None)
    db_character = models.Character(chat_id=chat_id, **char_data)
    db.add(db_character)
    await db.commit()
    await db.refresh(db_character)

    if initial_rels:
        for rel in initial_rels:
            rel_exists = await db.execute(
                select(models.CharacterRelationship).where(
                    models.CharacterRelationship.source_character_id == db_character.id,
                    models.CharacterRelationship.target_character_id == rel["target_id"],
                )
            )
            if rel_exists.scalar_one_or_none() is not None:
                continue
            db_rel = models.CharacterRelationship(
                chat_id=chat_id,
                source_character_id=db_character.id,
                target_character_id=rel["target_id"],
                relationship_type=rel.get("relationship_type", models.DEFAULT_RELATIONSHIP_TYPE),
                affection=rel.get("affection", models.DEFAULT_AFFECTION),
                trust=rel.get("trust", models.DEFAULT_TRUST),
                attraction=rel.get("attraction", models.DEFAULT_ATTRACTION),
                resentment=rel.get("resentment", models.DEFAULT_RESENTMENT),
                jealousy=rel.get("jealousy", models.DEFAULT_JEALOUSY),
                description=rel.get("description", ""),
                initial_description=rel.get("description", ""),
            )
            db.add(db_rel)
        await db.commit()
        await db.refresh(db_character)

    return db_character


async def get_character(db: AsyncSession, character_id: int) -> models.Character | None:
    return await db.get(models.Character, character_id)


async def get_characters_by_chat(
    db: AsyncSession, chat_id: int, include_player: bool = False
) -> list[models.Character]:
    stmt = (
        select(models.Character)
        .where(models.Character.chat_id == chat_id)
        .order_by(models.Character.order_index, models.Character.id)
    )
    if not include_player:
        stmt = stmt.where(models.Character.is_player == False)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_player_character(
    db: AsyncSession, chat_id: int
) -> models.Character | None:
    stmt = select(models.Character).where(
        models.Character.chat_id == chat_id,
        models.Character.is_player == True,
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def create_player_character(
    db: AsyncSession, chat_id: int, name: str = "Игрок"
) -> models.Character:
    existing = await get_player_character(db, chat_id)
    if existing:
        return existing
    db_chat = await get_chat(db, chat_id)
    player_location = ""
    if db_chat is not None:
        player_location = getattr(db_chat, "player_location", "") or ""
    player = models.Character(
        chat_id=chat_id,
        name=name,
        is_player=True,
        order_index=9999,
        location=player_location,
    )
    db.add(player)
    await db.commit()
    await db.refresh(player)

    # Create NPC->Player relationships for all NPCs.
    # Player->NPC relationships are intentionally not tracked.
    npcs = await get_characters_by_chat(db, chat_id)
    for npc in npcs:
        rel_npc_player = models.CharacterRelationship(
            chat_id=chat_id,
            source_character_id=npc.id,
            target_character_id=player.id,
        )
        db.add(rel_npc_player)
    await db.commit()
    await db.refresh(player)
    return player


async def update_character(
    db: AsyncSession, character_id: int, character_update: schemas.CharacterUpdate
) -> models.Character | None:
    db_character = await get_character(db, character_id)
    if db_character is None:
        return None
    update_data = character_update.model_dump(exclude_unset=True)
    new_index = update_data.get("order_index")
    if new_index is not None and new_index != db_character.order_index:
        if await _order_index_taken(
            db, db_character.chat_id, new_index, exclude_id=db_character.id
        ):
            raise ValueError(f"order_index={new_index} уже занят в этом чате")
    for field, value in update_data.items():
        setattr(db_character, field, value)
    if "location" in update_data:
        await _sync_chat_player_location(db, db_character)
    await db.commit()
    await db.refresh(db_character)
    return db_character


async def delete_character(db: AsyncSession, character_id: int) -> bool:
    db_character = await get_character(db, character_id)
    if db_character is None:
        return False
    if db_character.is_player:
        raise ValueError("Нельзя удалить игрока")
    await db.delete(db_character)
    await db.commit()
    return True


# ----------------------------- Message -----------------------------
def _build_world_event(
    db_message: models.Message, *, round_id: str | None = None
) -> models.WorldEvent:
    """Dual-write: `WorldEvent` рядом с `Message` (WPE.md Фаза 3).

    Событие копирует поля сообщения (legacy-bridge: строковая локация).
    ``event_type``: ``speech`` для user/character, ``system`` для системных.
    ``round_id`` для user-сообщения выводится так же, как в chat_engine
    (``r{chat_id}-m{message_id}``) — тот же раунд, что и остальные реплики.
    """
    role = (db_message.role or "").strip().lower()
    event_type = "system" if role == "system" else "speech"
    if round_id is None and role == "user":
        round_id = f"r{db_message.chat_id}-m{db_message.id}"
    return models.WorldEvent(
        chat_id=db_message.chat_id,
        character_id=db_message.character_id,
        message_id=db_message.id,
        event_type=event_type,
        location=db_message.location or "",
        round_id=round_id,
        target_character_ids=db_message.target_character_ids or "[]",
    )


async def create_message(
    db: AsyncSession,
    message: schemas.MessageCreate,
    *,
    round_id: str | None = None,
) -> models.Message:
    """Create a message; with `WORLD_ENGINE_EVENTS_ENABLED` also writes its
    `WorldEvent` atomically (same transaction, WPE.md Фаза 3 dual-write).

    The dual-write is a no-op by default (flag off). Shadow perception
    (`run_shadow_perception`) runs after commit and never affects the result.
    """
    kwargs = message.orm_kwargs() if hasattr(message, "orm_kwargs") else message.model_dump()
    if "target_character_ids" in kwargs and not isinstance(
        kwargs["target_character_ids"], str
    ):
        kwargs["target_character_ids"] = perception.serialize_target_ids(
            kwargs["target_character_ids"]
        )
    db_message = models.Message(**kwargs)
    db.add(db_message)
    await db.flush()
    if settings.world_engine_events_enabled:
        db.add(_build_world_event(db_message, round_id=round_id))
    if settings.world_engine_threads_enabled:
        await ensure_message_thread_delivery(db, db_message)
    await db.commit()
    await db.refresh(db_message)
    if settings.world_engine_events_enabled:
        try:
            from . import wpe_shadow

            await wpe_shadow.run_shadow_perception(db, db_message)
        except Exception:
            logger.exception(
                "[WPE-P3] shadow perception failed chat_id=%s msg_id=%s",
                db_message.chat_id,
                db_message.id,
            )
    return db_message


async def get_messages_by_chat(
    db: AsyncSession, chat_id: int, limit: int | None = None
) -> list[models.Message]:
    stmt = (
        select(models.Message)
        .where(models.Message.chat_id == chat_id)
        .options(selectinload(models.Message.character))
    )
    if limit is None:
        stmt = stmt.order_by(models.Message.timestamp, models.Message.id)
        result = await db.execute(stmt)
        return list(result.scalars().all())
    stmt = stmt.order_by(
        models.Message.timestamp.desc(), models.Message.id.desc()
    ).limit(limit)
    result = await db.execute(stmt)
    messages = list(result.scalars().all())
    messages.reverse()
    return messages


async def get_messages_paginated(
    db: AsyncSession, chat_id: int, limit: int = 50, offset: int = 0
) -> list[models.Message]:
    stmt = (
        select(models.Message)
        .where(models.Message.chat_id == chat_id)
        .options(selectinload(models.Message.character))
        .order_by(models.Message.timestamp, models.Message.id)
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def count_messages_after(
    db: AsyncSession, chat_id: int, after_message_id: int
) -> int:
    """Count messages in chat with id strictly greater than after_message_id."""
    stmt = (
        select(func.count())
        .select_from(models.Message)
        .where(
            models.Message.chat_id == chat_id,
            models.Message.id > after_message_id,
        )
    )
    result = await db.execute(stmt)
    return result.scalar() or 0


async def get_messages_since(
    db: AsyncSession,
    chat_id: int,
    after_message_id: int,
    limit: int | None = None,
) -> list[models.Message]:
    """Return messages with id > after_message_id in chronological order."""
    stmt = (
        select(models.Message)
        .where(
            models.Message.chat_id == chat_id,
            models.Message.id > after_message_id,
        )
        .options(selectinload(models.Message.character))
        .order_by(models.Message.timestamp, models.Message.id)
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def delete_message(
    db: AsyncSession, message_id: int, *, cascade_after: bool = False
) -> bool:
    """Delete a single message from the chat.

    ``cascade_after`` deletes the message and every message that follows it in
    the same chat (used for player messages: replies without the player's line
    are meaningless). Presence records are removed by the FK ``CASCADE``.
    Returns False if the message does not exist.
    """
    db_message = await db.get(models.Message, message_id)
    if db_message is None:
        return False
    if cascade_after:
        await db.execute(
            delete(models.Message).where(
                models.Message.chat_id == db_message.chat_id,
                models.Message.id >= message_id,
            )
        )
    else:
        await db.delete(db_message)
    await db.commit()
    return True


# ----------------------------- Memory ------------------------------
async def _memory_exists(db: AsyncSession, character_id: int, content_hash: str) -> bool:
    stmt = select(models.Memory.id).where(
        models.Memory.character_id == character_id,
        models.Memory.content_hash == content_hash,
    )
    result = await db.execute(stmt)
    return result.scalars().first() is not None


async def create_memory(db: AsyncSession, memory: schemas.MemoryCreate, source_message_ids: list[int] | None = None) -> models.Memory | None:
    content_hash = memory_content_hash(memory.content)
    if await _memory_exists(db, memory.character_id, content_hash):
        return None
    import json
    data = memory.model_dump()
    # Sprint 2 (§7): пустое memory_type → default 'semantic' (риск миграции —
    # существующие строки получают 'semantic', type НЕ входит в content_hash).
    data["memory_type"] = data.get("memory_type") or "semantic"
    db_memory = models.Memory(
        **data,
        content_hash=content_hash,
        source_message_ids=json.dumps(source_message_ids or []),
    )
    db.add(db_memory)
    await db.commit()
    await db.refresh(db_memory)
    return db_memory


async def _count_memories_for_character(db: AsyncSession, character_id: int) -> int:
    stmt = (
        select(func.count())
        .select_from(models.Memory)
        .where(models.Memory.character_id == character_id)
    )
    result = await db.execute(stmt)
    return result.scalar() or 0


async def _delete_lowest_value_memories(
    db: AsyncSession, character_id: int, keep_count: int
) -> None:
    """Drop lowest-importance memories first, then oldest (P1 extraction quality)."""
    stmt = select(models.Memory).where(models.Memory.character_id == character_id)
    result = await db.execute(stmt)
    memories = list(result.scalars().all())
    if len(memories) <= keep_count:
        return

    def sort_key(mem: models.Memory):
        importance = mem.importance if mem.importance is not None else 0.5
        created = mem.created_at or datetime.min
        return (importance, created, mem.id)

    memories.sort(key=sort_key)
    to_delete = memories[: len(memories) - keep_count]
    delete_ids = [m.id for m in to_delete]
    if not delete_ids:
        return
    await db.execute(delete(models.Memory).where(models.Memory.id.in_(delete_ids)))
    await db.commit()


async def _delete_oldest_memories(db: AsyncSession, character_id: int, keep_count: int) -> None:
    """Backward-compatible alias → importance-aware eviction."""
    await _delete_lowest_value_memories(db, character_id, keep_count)


async def ensure_memory_limit(db: AsyncSession, character_id: int) -> None:
    count = await _count_memories_for_character(db, character_id)
    if count > settings.max_memories_per_character:
        await _delete_lowest_value_memories(db, character_id, settings.max_memories_per_character)


async def get_memories_by_character(
    db: AsyncSession, character_id: int, limit: int | None = None
) -> list[models.Memory]:
    stmt = (
        select(models.Memory)
        .where(models.Memory.character_id == character_id)
        .order_by(models.Memory.created_at.desc(), models.Memory.id.desc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    result = await db.execute(stmt)
    memories = list(result.scalars().all())
    memories.reverse()
    if memories:
        await _touch_memory_access(db, [m.id for m in memories])
    return memories


WitnessQuality = Literal["direct", "hearsay"]


async def filter_memories_by_witness(
    db: AsyncSession,
    memories: list[models.Memory],
    viewer_character_id: int,
) -> Tuple[list[models.Memory], dict[int, WitnessQuality]]:
    """Filter memories so the character only keeps those they actually witnessed.

    Checks each memory's *source_message_ids* against the MessagePresence table.
    A memory is kept if at least one of its source messages has presence
    ``present`` or ``told`` for *viewer_character_id*.

    Returns ``(filtered_memories, quality_map)`` where *quality_map* maps
    memory id → ``"direct"`` (at least one source with ``present``) or
    ``"hearsay"`` (all sources are ``told`` only).
    """
    if not memories:
        return [], {}

    # Collect all unique source message IDs across all memories
    import json
    all_source_ids: set[int] = set()
    mem_to_sources: dict[int, list[int]] = {}
    for mem in memories:
        try:
            ids = json.loads(mem.source_message_ids or "[]")
        except (json.JSONDecodeError, TypeError):
            ids = []
        mem_to_sources[mem.id] = ids
        all_source_ids.update(ids)

    if not all_source_ids:
        # No source IDs means legacy memory — keep it (assume direct)
        return memories, {mem.id: "direct" for mem in memories}

    # Batch-query presence for all source messages
    stmt = select(models.MessagePresence).where(
        models.MessagePresence.character_id == viewer_character_id,
        models.MessagePresence.message_id.in_(all_source_ids),
    )
    result = await db.execute(stmt)
    presence_rows = list(result.scalars().all())
    msg_presence: dict[int, str] = {row.message_id: row.presence for row in presence_rows}

    OBSERVABLE = witness_model.MEMORY_OBSERVABLE_PRESENCES  # {"present", "told"}

    filtered: list[models.Memory] = []
    quality_map: dict[int, str] = {}
    for mem in memories:
        source_ids = mem_to_sources.get(mem.id, [])
        if not source_ids:
            filtered.append(mem)
            quality_map[mem.id] = "direct"
            continue
        source_presences = [msg_presence.get(sid) for sid in source_ids]
        if any(p == "present" for p in source_presences):
            filtered.append(mem)
            quality_map[mem.id] = "direct"
        elif any(p in OBSERVABLE for p in source_presences):
            filtered.append(mem)
            quality_map[mem.id] = "hearsay"
        # If all absent, memory is excluded

    return filtered, quality_map


def _apply_witness_boost(
    selected: list[models.Memory],
    quality_map: dict[int, WitnessQuality],
    boost: float = 1.5,
) -> list[models.Memory]:
    """Re-rank so directly witnessed facts appear before hearsay at same score.

    Sorts stable so that within equal positions, direct facts come first.
    This is a gentle nudge, not a hard cutoff.
    """
    if not selected or not quality_map:
        return selected
    return sorted(
        selected,
        key=lambda m: (
            0 if quality_map.get(m.id) == "direct" else 1,
        ),
    )


# ----------------- Hybrid Retrieval v2 (Plans/update20.md §14, Sprint 6) ----
async def build_rerank_signals(
    db: AsyncSession,
    chat_id: int,
    character_ids: list[int],
    character_names: dict[int, str],
) -> dict[int, memory_service.RerankSignals]:
    """Сигналы текущего контекста для rerank (§14): направленные отношения
    персонажа (имена targets) и активные ``story_threads``.

    Собирается только при ``hybrid_rerank_enabled`` (read-path canary);
    пустые сигналы (нет отношений/потоков) — валидный результат: rerank просто
    работает без relationship/story-слагаемых. write-path ``story_threads`` —
    Sprint 8, поэтому на текущий момент поток обычно пуст (story-ось работает
    по ``memory_type='story'`` с мягким базовым бустом).
    """
    if not character_ids or not settings.hybrid_rerank_enabled:
        return {}

    rel_stmt = select(
        models.CharacterRelationship.source_character_id,
        models.CharacterRelationship.target_character_id,
    ).where(
        models.CharacterRelationship.chat_id == chat_id,
        models.CharacterRelationship.source_character_id.in_(character_ids),
    )
    rel_result = await db.execute(rel_stmt)
    rel_targets: dict[int, list[int]] = {cid: [] for cid in character_ids}
    for source_id, target_id in rel_result.all():
        rel_targets.setdefault(int(source_id), []).append(int(target_id))

    thread_stmt = select(models.StoryThread.name).where(
        models.StoryThread.chat_id == chat_id,
        models.StoryThread.status == "active",
    )
    thread_result = await db.execute(thread_stmt)
    active_threads = tuple(
        name for (name,) in thread_result.all() if name and str(name).strip()
    )

    signals: dict[int, memory_service.RerankSignals] = {}
    for cid in character_ids:
        names = tuple(
            n
            for t in rel_targets.get(cid, [])
            if (n := character_names.get(int(t)))
        )
        signals[cid] = memory_service.RerankSignals(
            relationship_target_names=names,
            active_threads=active_threads,
        )
    return signals


def _apply_rerank(
    selected: list[models.Memory],
    *,
    query_text: str,
    query_embedding: list[float] | None,
    signals: memory_service.RerankSignals | None,
) -> list[models.Memory]:
    """Применить rerank после RRF/BM25, до witness-boost (Sprint 6, §14).

    No-op (возвращает список без изменений) при выключенном
    ``hybrid_rerank_enabled`` или отсутствии сигналов — RRF-путь не меняется.
    """
    if not settings.hybrid_rerank_enabled or not signals:
        return selected
    context = memory_service.RerankContext(
        query_text=query_text,
        query_embedding=query_embedding,
        relationship_target_names=signals.relationship_target_names,
        active_threads=signals.active_threads,
    )
    return memory_service.rerank_memories(selected, context)


async def decay_memory_importance(db: AsyncSession) -> None:
    """Periodic decay of importance for memories not accessed recently.

    Reduces ``importance`` by ``settings.memory_importance_decay_factor`` for
    every full ``settings.memory_importance_decay_days`` since last access.
    """
    import math
    from datetime import datetime, timedelta

    decay_days = settings.memory_importance_decay_days
    decay_factor = settings.memory_importance_decay_factor
    if decay_days <= 0 or decay_factor <= 0:
        return

    cutoff = datetime.utcnow() - timedelta(days=decay_days)
    stmt = (
        select(models.Memory)
        .where(models.Memory.last_accessed_at < cutoff)
        .where(models.Memory.importance > 0.1)
    )
    result = await db.execute(stmt)
    stale = list(result.scalars().all())
    if not stale:
        return

    for mem in stale:
        days_unused = (datetime.utcnow() - (mem.last_accessed_at or mem.created_at)).days
        periods = max(1, days_unused // decay_days)
        decayed = mem.importance * (decay_factor ** periods)
        mem.importance = max(0.05, round(decayed, 2))

    await db.commit()
    logger.info("Decayed importance for %d stale memories", len(stale))


async def _touch_memory_access(db: AsyncSession, memory_ids: list[int]) -> None:
    """Update last_accessed_at for retrieved memories."""
    if not memory_ids:
        return
    from datetime import datetime
    await db.execute(
        models.Memory.__table__.update()
        .where(models.Memory.id.in_(memory_ids))
        .values(last_accessed_at=datetime.utcnow())
    )
    await db.commit()


async def get_memories_for_characters(
    db: AsyncSession, character_ids: list[int], limit: int | None = None
) -> dict[int, list[models.Memory]]:
    """Load memories for multiple characters in one query."""
    if not character_ids:
        return {}
    stmt = (
        select(models.Memory)
        .where(models.Memory.character_id.in_(character_ids))
        .order_by(
            models.Memory.character_id,
            models.Memory.created_at.desc(),
            models.Memory.id.desc(),
        )
    )
    result = await db.execute(stmt)
    all_memories = list(result.scalars().all())
    grouped: dict[int, list[models.Memory]] = {cid: [] for cid in character_ids}
    for mem in all_memories:
        bucket = grouped[mem.character_id]
        if limit is None or len(bucket) < limit:
            bucket.append(mem)
    for cid in grouped:
        grouped[cid].reverse()
    return grouped


async def _build_scoring_context(
    context_text: str,
    character_summaries: dict[int, str] | None,
    cid: int,
) -> str:
    """Augment the BM25 scoring context with the character's summary when available."""
    if not character_summaries:
        return context_text
    summary = character_summaries.get(cid)
    if not summary:
        return context_text
    return f"{context_text}\n\n{summary}"


async def get_relevant_memories_for_characters(
    db: AsyncSession,
    character_ids: list[int],
    context_text: str,
    top_k: int | None = None,
    *,
    witness_filter: bool = True,
    character_summaries: dict[int, str] | None = None,
    rerank_signals: dict[int, memory_service.RerankSignals] | None = None,
) -> dict[int, list[models.Memory]]:
    """Load and rank memories by BM25 relevance to current context (P1).

    When *witness_filter* is True (default), memories whose *source_message_ids*
    reference messages the character did not witness (present/told) are filtered
    out before ranking.

    When *character_summaries* is provided, each character's summary is appended
    to the scoring context so BM25 biases toward memories relevant to the
    character's current state.

    Sprint 6 (§14): when *rerank_signals* is provided and ``hybrid_rerank_enabled``
    is on, the BM25-selected top candidates are reranked (semantic-ось отпадает —
    embeddings не используются на этом пути, веса нормируются) before the
    witness boost. Without the flag the behaviour is unchanged.
    """
    if not character_ids:
        return {}
    if not settings.enable_relevant_memory_selection:
        return await get_memories_for_characters(
            db, character_ids, top_k or settings.memory_relevance_top_k
        )

    candidate_limit = (top_k or settings.memory_relevance_top_k) * 4
    # Decay importance periodically (approx every 20th call)
    if random.random() < 0.05:
        await decay_memory_importance(db)
    relevant: dict[int, list[models.Memory]] = {}
    for cid in character_ids:
        candidates = await get_memories_by_character(db, cid, candidate_limit)
        quality_map: dict[int, WitnessQuality] = {}
        if witness_filter and settings.enable_witness_memory_filter:
            candidates, quality_map = await filter_memories_by_witness(db, candidates, cid)
        scoring_context = await _build_scoring_context(context_text, character_summaries, cid)
        selected = memory_service.select_relevant_memories(
            candidates, scoring_context, top_k or settings.memory_relevance_top_k
        )
        # Sprint 6 (§14): rerank после BM25, до witness-boost (fallback без
        # embeddings — semantic-слагаемое отбрасывается, веса нормируются).
        selected = _apply_rerank(
            selected,
            query_text=scoring_context,
            query_embedding=None,
            signals=rerank_signals.get(cid) if rerank_signals else None,
        )
        if quality_map:
            selected = _apply_witness_boost(selected, quality_map)
        # Touch last_accessed_at for selected memories
        if selected:
            now = datetime.utcnow()
            for mem in selected:
                mem.last_accessed_at = now
            await db.commit()
        relevant[cid] = selected
    return relevant


async def get_hybrid_memories_for_characters(
    db: AsyncSession,
    character_ids: list[int],
    context_text: str,
    top_k: int | None = None,
    bm25_weight: float | None = None,
    vector_weight: float | None = None,
    *,
    witness_filter: bool = True,
    character_summaries: dict[int, str] | None = None,
    rerank_signals: dict[int, memory_service.RerankSignals] | None = None,
) -> dict[int, list[models.Memory]]:
    """
    Hybrid retrieval: BM25 (lexical) + Vector (semantic) with RRF fusion (P3).

    When *witness_filter* is True (default), memories whose *source_message_ids*
    reference messages the character did not witness are filtered out first.

    When *character_summaries* is provided, each character's summary is appended
    to the BM25 scoring context.
    
    Sprint 6 (§14): when *rerank_signals* is provided and ``hybrid_rerank_enabled``
    is on, the RRF top-K are reranked (lexical/semantic/emotional/story/
    relationship/recency/salience) BEFORE the witness boost. Without the flag the
    RRF path is unchanged.
    
    Returns top_k memories per character ranked by reciprocal rank fusion.
    """
    if not character_ids:
        return {}
    
    if not settings.embedding_enabled:
        logger.info("Embeddings disabled, falling back to BM25-only")
        return await get_relevant_memories_for_characters(
            db, character_ids, context_text, top_k,
            witness_filter=witness_filter,
            character_summaries=character_summaries,
            rerank_signals=rerank_signals,
        )
    
    bm25_w = bm25_weight if bm25_weight is not None else settings.hybrid_bm25_weight
    vector_w = vector_weight if vector_weight is not None else settings.hybrid_vector_weight
    rrf_k = settings.hybrid_rrf_k
    
    top_k = top_k or settings.memory_relevance_top_k
    candidate_limit = top_k * 8  # Get more candidates for better fusion
    
    emb_service = embedding_service.get_embedding_service()
    query_embedding = await emb_service.embed_single(context_text)
    
    if not query_embedding:
        logger.warning("Failed to generate query embedding, falling back to BM25")
        return await get_relevant_memories_for_characters(
            db, character_ids, context_text, top_k,
            witness_filter=witness_filter,
            character_summaries=character_summaries,
            rerank_signals=rerank_signals,
        )
    
    # Decay importance periodically (approx every 20th call)
    if random.random() < 0.05:
        await decay_memory_importance(db)
    relevant: dict[int, list[models.Memory]] = {}
    
    for cid in character_ids:
        candidates = await get_memories_by_character(db, cid, candidate_limit)
        quality_map: dict[int, WitnessQuality] = {}
        if witness_filter and settings.enable_witness_memory_filter:
            candidates, quality_map = await filter_memories_by_witness(db, candidates, cid)
        
        if not candidates:
            relevant[cid] = []
            continue
        
        scoring_context = await _build_scoring_context(context_text, character_summaries, cid)
        
        # BM25 ranking
        bm25_results = memory_service.select_relevant_memories(
            candidates, scoring_context, candidate_limit
        )
        bm25_rank = {mem.id: rank for rank, mem in enumerate(bm25_results)}
        
        # Vector ranking
        vector_scores = []
        for mem in candidates:
            if mem.embedding:
                mem_emb = emb_service.unpack_embedding(mem.embedding)
                if mem_emb:
                    sim = emb_service.cosine_similarity(query_embedding, mem_emb)
                    vector_scores.append((sim, mem))
        
        vector_scores.sort(key=lambda x: x[0], reverse=True)
        vector_rank = {mem.id: rank for rank, (_, mem) in enumerate(vector_scores)}
        
        # RRF fusion
        all_mem_ids = set(bm25_rank.keys()) | set(vector_rank.keys())
        rrf_scores = {}
        
        for mem_id in all_mem_ids:
            bm25_r = bm25_rank.get(mem_id, len(bm25_results))
            vec_r = vector_rank.get(mem_id, len(vector_scores))
            rrf = bm25_w / (rrf_k + bm25_r + 1) + vector_w / (rrf_k + vec_r + 1)
            rrf_scores[mem_id] = rrf
        
        # Sort by RRF score
        sorted_mem_ids = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)
        top_mem_ids = sorted_mem_ids[:top_k]
        
        # Get memory objects
        mem_map = {mem.id: mem for mem in candidates}
        selected = [mem_map[mid] for mid in top_mem_ids if mid in mem_map]
        
        # Sprint 6 (§14): rerank после RRF, до witness-boost.
        selected = _apply_rerank(
            selected,
            query_text=scoring_context,
            query_embedding=query_embedding,
            signals=rerank_signals.get(cid) if rerank_signals else None,
        )
        
        # Apply witness boost — direct facts rank before hearsay
        if quality_map:
            selected = _apply_witness_boost(selected, quality_map)
        
        # Touch last_accessed_at
        if selected:
            now = datetime.utcnow()
            for mem in selected:
                mem.last_accessed_at = now
            await db.commit()
        
        relevant[cid] = selected
    
    return relevant


async def delete_memory(db: AsyncSession, memory_id: int) -> bool:
    db_memory = await db.get(models.Memory, memory_id)
    if db_memory is None:
        return False
    await db.delete(db_memory)
    await db.commit()
    return True


async def update_memory(
    db: AsyncSession, memory_id: int, memory_update: schemas.MemoryUpdate
) -> models.Memory | None:
    """Update memory content/importance/category (+ Sprint 2 поля типа/эмоции)."""
    db_memory = await db.get(models.Memory, memory_id)
    if db_memory is None:
        return None
    update_data = memory_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_memory, field, value)
    if db_memory.memory_type is None:
        db_memory.memory_type = "semantic"
    await db.commit()
    await db.refresh(db_memory)
    return db_memory


# --------------------- Memory Anchors (Sprint 2, §7) ---------------------
def anchor_activation_score(anchor: models.MemoryAnchor, now=None) -> float:
    """Детерминированный score активации якоря: importance × recency (§7).

    recency = 1 / (1 + возраст_в_днях) — свежие якоря получают больший вес;
    score в диапазоне (0..1], монотонен по importance и свежести.
    """
    if now is None:
        now = datetime.utcnow()
    timestamp = anchor.timestamp or now
    age_days = max(0.0, (now - timestamp).total_seconds() / 86400.0)
    recency = 1.0 / (1.0 + age_days)
    return float(anchor.importance or 0.5) * recency


def select_top_anchors(
    anchors: list[models.MemoryAnchor],
    max_k: int,
    now=None,
) -> list[models.MemoryAnchor]:
    """Top-K якорей по ``importance × recency`` (активация в контекст, §7/§12.3).

    Дедупликация по ``event_id``: один канонический источник порождает не более
    одного якоря в top-K (уникальность в контексте). ``max_k`` — cap из
    ``RELATIONSHIP_ANCHOR_MAX`` (по умолчанию 3).
    """
    if not anchors or max_k <= 0:
        return []
    scored = [(anchor_activation_score(a, now), a) for a in anchors]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    seen_events: set[int] = set()
    selected: list[models.MemoryAnchor] = []
    for score, anchor in scored:
        event_id = anchor.event_id
        if event_id is not None and event_id in seen_events:
            continue
        if event_id is not None:
            seen_events.add(event_id)
        selected.append(anchor)
        if len(selected) >= max_k:
            break
    return selected


async def create_memory_anchor(
    db: AsyncSession,
    *,
    relationship_id: int,
    event_id: int | None = None,
    emotion: str = "",
    valence: float = 0.0,
    intensity: float = 0.0,
    importance: float = 0.5,
    timestamp=None,
) -> models.MemoryAnchor:
    """Записать эмоциональный якорь направленного отношения (§7).

    Якорь пишется движком из значимых RelationshipEvent (расширение
    ``_maybe_create_memory_from_event``); Sensors якоря НЕ предлагает.
    """
    anchor = models.MemoryAnchor(
        relationship_id=relationship_id,
        event_id=event_id,
        emotion=emotion or "",
        valence=max(-1.0, min(1.0, float(valence or 0.0))),
        intensity=max(0.0, min(1.0, float(intensity or 0.0))),
        importance=max(0.0, min(1.0, float(importance or 0.5))),
        timestamp=timestamp or datetime.utcnow(),
    )
    db.add(anchor)
    await db.commit()
    await db.refresh(anchor)
    return anchor


async def get_anchors_for_relationship(
    db: AsyncSession, relationship_id: int, limit: int | None = None
) -> list[models.MemoryAnchor]:
    """Все якоря отношения source→target (по убыванию свежести)."""
    stmt = (
        select(models.MemoryAnchor)
        .where(models.MemoryAnchor.relationship_id == relationship_id)
        .order_by(models.MemoryAnchor.timestamp.desc(), models.MemoryAnchor.id.desc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_anchors_for_relationships(
    db: AsyncSession, relationship_ids: list[int], limit: int | None = None
) -> dict[int, list[models.MemoryAnchor]]:
    """Загрузить якоря для нескольких отношений одним запросом (для контекста)."""
    if not relationship_ids:
        return {}
    stmt = (
        select(models.MemoryAnchor)
        .where(models.MemoryAnchor.relationship_id.in_(relationship_ids))
        .order_by(models.MemoryAnchor.timestamp.desc(), models.MemoryAnchor.id.desc())
    )
    result = await db.execute(stmt)
    anchors = list(result.scalars().all())
    grouped: dict[int, list[models.MemoryAnchor]] = {rid: [] for rid in relationship_ids}
    for anchor in anchors:
        bucket = grouped.get(anchor.relationship_id)
        if bucket is None:
            continue
        if limit is None or len(bucket) < limit:
            bucket.append(anchor)
    return grouped


# ------------------------ Character Summary ----------------------
async def get_character_summary(
    db: AsyncSession, character_id: int
) -> models.CharacterSummary | None:
    stmt = select(models.CharacterSummary).where(
        models.CharacterSummary.character_id == character_id
    )
    result = await db.execute(stmt)
    return result.scalars().first()


async def get_summaries_for_characters(
    db: AsyncSession, character_ids: list[int]
) -> dict[int, models.CharacterSummary]:
    if not character_ids:
        return {}
    stmt = select(models.CharacterSummary).where(
        models.CharacterSummary.character_id.in_(character_ids)
    )
    result = await db.execute(stmt)
    summaries = list(result.scalars().all())
    return {summary.character_id: summary for summary in summaries}


async def upsert_character_summary(
    db: AsyncSession,
    chat_id: int,
    character_id: int,
    content: str,
    through_message_id: int,
) -> models.CharacterSummary:
    existing = await get_character_summary(db, character_id)
    now = datetime.utcnow()
    if existing is None:
        db_summary = models.CharacterSummary(
            chat_id=chat_id,
            character_id=character_id,
            content=content,
            through_message_id=through_message_id,
            updated_at=now,
        )
        db.add(db_summary)
    else:
        existing.content = content
        existing.through_message_id = through_message_id
        existing.updated_at = now
        db_summary = existing
    await db.commit()
    await db.refresh(db_summary)
    return db_summary


async def reset_character_summaries_for_chat(db: AsyncSession, chat_id: int) -> None:
    await db.execute(
        delete(models.CharacterSummary).where(
            models.CharacterSummary.chat_id == chat_id
        )
    )
    await db.commit()


# ------------------------ Message Presence -----------------------
async def upsert_message_presence_batch(
    db: AsyncSession, records: list[schemas.MessagePresenceCreate]
) -> None:
    if not records:
        return
    for record in records:
        stmt = select(models.MessagePresence).where(
            models.MessagePresence.message_id == record.message_id,
            models.MessagePresence.character_id == record.character_id,
        )
        result = await db.execute(stmt)
        existing = result.scalars().first()
        if existing is None:
            db.add(models.MessagePresence(**record.model_dump()))
        else:
            existing.presence = record.presence
            # Sprint 4 (§11): attention обновляется только если явно передан
            # (None при выключенном флаге → существующее значение сохраняется).
            if record.attention is not None:
                existing.attention = record.attention
    await db.commit()


async def get_presence_map(
    db: AsyncSession, message_ids: list[int], character_id: int
) -> dict[int, str]:
    if not message_ids:
        return {}
    stmt = select(models.MessagePresence).where(
        models.MessagePresence.character_id == character_id,
        models.MessagePresence.message_id.in_(message_ids),
    )
    result = await db.execute(stmt)
    rows = list(result.scalars().all())
    return {row.message_id: row.presence for row in rows}


async def get_attention_map(
    db: AsyncSession, message_ids: list[int], character_id: int
) -> dict[int, float]:
    """Attention score (Sprint 4, §11) для пары (персонаж, сообщения).

    Пусто при выключенном ``attention_enabled`` — attention не считался
    (NULL в БД) → фильтры ведут себя как legacy. Возвращает
    ``{message_id: attention}`` только для строк с не-NULL score.
    """
    if not settings.attention_enabled or not message_ids:
        return {}
    stmt = select(
        models.MessagePresence.message_id, models.MessagePresence.attention
    ).where(
        models.MessagePresence.character_id == character_id,
        models.MessagePresence.message_id.in_(message_ids),
        models.MessagePresence.attention.is_not(None),
    )
    result = await db.execute(stmt)
    return {mid: attn for mid, attn in result.all()}


async def get_presence_for_message(
    db: AsyncSession, message_id: int, character_id: int
) -> str:
    """Presence одного события для персонажа (belief pipeline §9).

    Нет строки → "absent" (не воспринял — belief не пишется, изоляция R2).
    """
    if message_id is None:
        return "absent"
    stmt = select(models.MessagePresence).where(
        models.MessagePresence.character_id == character_id,
        models.MessagePresence.message_id == message_id,
    )
    result = await db.execute(stmt)
    row = result.scalars().first()
    return row.presence if row is not None else "absent"


async def get_attention_for_message(
    db: AsyncSession, message_id: int, character_id: int
) -> float | None:
    """Attention score одного события для персонажа (belief pipeline §9).

    None при выключенном ``attention_enabled`` / нет строки — gating выключен.
    """
    if not settings.attention_enabled or message_id is None:
        return None
    stmt = select(models.MessagePresence).where(
        models.MessagePresence.character_id == character_id,
        models.MessagePresence.message_id == message_id,
    )
    result = await db.execute(stmt)
    row = result.scalars().first()
    return row.attention if row is not None else None


async def _attention_context_for_chat(
    db: AsyncSession, chat_id: int, character_ids: list[int]
) -> dict[int, dict[str, set[int]]]:
    """Per-character внимание-контекст (§11) одним заходом (2 запроса).

    Для каждого персонажа:
    - ``rel_targets`` — targets его направленных отношений (w_relationship);
    - ``anchor_authors`` — targets отношений с эмоциональным якорем
      (w_emotional: событие с таким автором активирует якорь).
    Пусто при выключенном ``attention_enabled`` — score не считается.
    """
    if not settings.attention_enabled or not character_ids:
        return {}
    rel_stmt = select(models.CharacterRelationship).where(
        models.CharacterRelationship.chat_id == chat_id,
        models.CharacterRelationship.source_character_id.in_(character_ids),
    )
    rels = list((await db.execute(rel_stmt)).scalars().all())
    rel_ids = [r.id for r in rels]

    anchored_rel_ids: set[int] = set()
    if rel_ids:
        anchor_stmt = select(models.MemoryAnchor.relationship_id).where(
            models.MemoryAnchor.relationship_id.in_(rel_ids)
        )
        anchored_rel_ids = set((await db.execute(anchor_stmt)).scalars().all())

    rel_targets: dict[int, set[int]] = {}
    anchor_authors: dict[int, set[int]] = {}
    for rel in rels:
        rel_targets.setdefault(rel.source_character_id, set()).add(
            rel.target_character_id
        )
        if rel.id in anchored_rel_ids:
            anchor_authors.setdefault(rel.source_character_id, set()).add(
                rel.target_character_id
            )
    return {
        cid: {
            "rel_targets": rel_targets.get(cid, set()),
            "anchor_authors": anchor_authors.get(cid, set()),
        }
        for cid in character_ids
    }


def _attention_score_for(
    *,
    message,
    character_id: int,
    presence: str,
    character_names: dict[int, str],
    rel_targets: set[int],
    anchor_authors: set[int],
    sensors_significance: float | None = None,
) -> float:
    """Детерминированный attention score пары (персонаж, событие) (§11).

    Sensors ``significance`` (если передан) применяется как подсказка в рамках
    caps — Sensors не решает доступность информации (presence) и не принимает
    решение о внимании.
    """
    from . import attention

    author_id = getattr(message, "character_id", None)
    anchor_active = False
    if author_id is not None:
        try:
            anchor_active = int(author_id) in anchor_authors
        except (TypeError, ValueError):
            pass
    score = attention.compute_attention_score(
        presence=presence,
        event=perception.event_from_message(message),
        observer={
            "character_id": character_id,
            "name": character_names.get(character_id, ""),
        },
        character_names=character_names,
        relationship_target_ids=rel_targets,
        anchor_active=anchor_active,
    )
    if sensors_significance is not None:
        score = attention.apply_sensors_significance(score, sensors_significance)
    return score


def _round_text_snippet(round_messages: list, max_len: int = 1500) -> str:
    """Короткий текст раунда для sensor-задачи (минимальный контекст §5.1.7)."""
    parts: list[str] = []
    for message in round_messages:
        role = getattr(message, "role", None)
        content = str(getattr(message, "content", "") or "")
        if not content:
            continue
        if role == "user":
            parts.append(f"Игрок: {content}")
        elif role == "system":
            parts.append(f"Система: {content}")
        else:
            name = getattr(getattr(message, "character", None), "name", None) or ""
            parts.append(f"{name}: {content}")
    snippet = "\n".join(parts)
    return snippet[:max_len]


def _build_perception_world_state(
    locations: list,
    thread_deliveries: set[int] | frozenset[int] | None = None,
) -> perception.PerceptionWorldState | None:
    """Build the pure world snapshot for the two-channel cutover (Фаза 4).

    ``thread_deliveries`` (Фаза 6) — id персонажей, которым событие доставлено
    через тред/удалённый канал; источник ``remote_status=delivered`` (§4).
    """
    return perception.PerceptionWorldState(
        adjacency=perception.build_permeability_index(locations or []),
        thread_deliveries=frozenset(thread_deliveries or ()),
    )


async def _chat_world_state_for_characters(
    db: AsyncSession, characters: list
) -> perception.PerceptionWorldState | None:
    """World snapshot from the chat of ``characters`` (None if flag off / no chat)."""
    if not settings.world_engine_perception_enabled:
        return None
    chat_id = None
    for character in characters:
        chat_id = getattr(character, "chat_id", None)
        if chat_id is not None:
            break
    if chat_id is None:
        return None
    locations = await get_chat_locations(db, chat_id)
    return _build_perception_world_state(locations)


async def _chat_world_state_for_message(
    db: AsyncSession, message, characters: list
) -> perception.PerceptionWorldState | None:
    """World snapshot scoped to one event (Фаза 6): включает доставки тредов.

    ``thread_deliveries`` события вычисляются из ``ThreadParticipantState``
    (см. ``thread_delivery_ids_for_message``), чтобы `perceive()` мог отдать
    ``remote_status=delivered`` адресату независимо от локации (Golden #6).
    """
    if not settings.world_engine_perception_enabled:
        return None
    chat_id = getattr(message, "chat_id", None)
    if chat_id is None:
        for character in characters:
            chat_id = getattr(character, "chat_id", None)
            if chat_id is not None:
                break
    if chat_id is None:
        return None
    locations = await get_chat_locations(db, chat_id)
    deliveries = await thread_delivery_ids_for_message(db, message)
    return _build_perception_world_state(locations, thread_deliveries=deliveries)


async def compute_and_save_presence_for_message(
    db: AsyncSession,
    message,
    characters: list,
    character_names: dict[int, str] | None = None,
) -> dict[int, str]:
    """Compute and persist presence for one event for all characters.

    Returns {character_id: presence} for the given message.

    Cutover (WPE 3.0 Фаза 4): при ``WORLD_ENGINE_PERCEPTION_ENABLED``
    presence пишется через двухканальный ``perceive()`` (Renderer
    ``witness_model.perceive_presence_for_character``), а не через legacy
    ``can_character_perceive_event``. Откат — выключить флаг.

    Фаза 6: world-state строится по событию (включая ``thread_deliveries``),
    а при ``WORLD_ENGINE_PARTIAL_PERCEPTION_ENABLED`` голосовая атрибуция
    ``voice_known`` берётся из отношений наблюдателя (WPE.md §4).
    """
    names = character_names or {c.id: c.name for c in characters}
    locations = {c.id: getattr(c, "location", "") or "" for c in characters}
    message_id = getattr(message, "id", None)
    if message_id is None:
        return {}

    world_state = await _chat_world_state_for_message(db, message, characters)
    known_voices = None
    if settings.world_engine_partial_perception_enabled:
        chat_id = getattr(message, "chat_id", None)
        if chat_id is not None:
            known_voices = await _known_voices_for_chat(db, chat_id)

    # Sprint 4 (§11): attention score считается детерминированно вместе с
    # presence (только для включённого флага). Sensors perception-proposal не
    # вызывается на синхронном пути — только в пост-раунд presence pass.
    attention_ctx: dict[int, dict[str, set[int]]] = {}
    if settings.attention_enabled:
        chat_id = getattr(message, "chat_id", None)
        if chat_id is None:
            for character in characters:
                chat_id = getattr(character, "chat_id", None)
                if chat_id is not None:
                    break
        if chat_id is not None:
            attention_ctx = await _attention_context_for_chat(
                db, chat_id, [c.id for c in characters]
            )

    records: list[schemas.MessagePresenceCreate] = []
    result: dict[int, str] = {}
    for character in characters:
        if world_state is not None:
            presence = witness_model.perceive_presence_for_character(
                message,
                character,
                world_state,
                voice_known=witness_model.voice_familiarity(
                    character.id,
                    getattr(message, "character_id", None),
                    known_voices,
                ),
            )
        else:
            presence = witness_model.compute_mvp_presence(
                message,
                character.id,
                names,
                viewer_location=locations.get(character.id, ""),
                character_locations=locations,
            )
        result[character.id] = presence
        attention = None
        if attention_ctx:
            ctx = attention_ctx.get(character.id, {})
            attention = _attention_score_for(
                message=message,
                character_id=character.id,
                presence=presence,
                character_names=names,
                rel_targets=ctx.get("rel_targets", set()),
                anchor_authors=ctx.get("anchor_authors", set()),
            )
        records.append(
            schemas.MessagePresenceCreate(
                message_id=message_id,
                character_id=character.id,
                presence=presence,
                attention=attention,
            )
        )
    await upsert_message_presence_batch(db, records)
    return result


async def compute_and_save_presence_for_round(
    db: AsyncSession,
    round_messages: list,
    character_ids: list[int],
    character_names: dict[int, str],
    *,
    characters: list | None = None,
    character_locations: dict[int, str] | None = None,
    client: Any = None,
) -> None:
    """Persist perception-based presence for all messages in a completed round.

    Cutover (WPE 3.0 Фаза 4): при ``WORLD_ENGINE_PERCEPTION_ENABLED``
    presence пишется через ``perceive()`` (см. Фаза 4 / Golden #14).

    Фаза 6: для событий удалённых каналов доставки тредов подставляются
    по-событийно, voice familiarity — из отношений при включённом
    ``WORLD_ENGINE_PARTIAL_PERCEPTION_ENABLED``.

    Sprint 4 (§11): вместе с presence детерминированно пишется attention score.
    Sensors perception-proposal (§5.1.3) вызывается только здесь (пост-раунд,
    один вызов на раунд при ``sensors_perception_enabled``): предложенная
    ``significance`` применяется как подсказка к attention в рамках
    ``SENSORS_PERCEPTION_SIGNIFICANCE_CAP``; доступность информации (presence)
    Sensors не определяет. Недоступен Sensors → детерминированный путь.
    """
    if characters is None:
        characters = []
        for cid in character_ids:
            char = await get_character(db, cid)
            if char is not None:
                characters.append(char)

    locations = character_locations or {
        c.id: getattr(c, "location", "") or "" for c in characters
    }
    if not locations and character_ids:
        locations = {cid: "" for cid in character_ids}

    world_state = await _chat_world_state_for_characters(db, characters)
    known_voices = None
    chat_id = None
    if settings.world_engine_partial_perception_enabled and characters:
        chat_id = getattr(characters[0], "chat_id", None)
        if chat_id is not None:
            known_voices = await _known_voices_for_chat(db, chat_id)
    if chat_id is None and characters:
        chat_id = getattr(characters[0], "chat_id", None)

    # Sprint 4 (§11): attention-контекст персонажей + Sensors perception-подсказка
    # (significance раунда, один вызов, только пост-раунд).
    attention_ctx: dict[int, dict[str, set[int]]] = {}
    sensors_significance: float | None = None
    if settings.attention_enabled:
        attention_ctx = await _attention_context_for_chat(db, chat_id, character_ids)
        if chat_id is not None and client is not None:
            try:
                from .sensors_service import sensors_service

                if sensors_service.is_enabled("perception"):
                    minimal_context = _round_text_snippet(round_messages)
                    if minimal_context:
                        sensors_result = await sensors_service.run(
                            client, task="perception", minimal_context=minimal_context
                        )
                        if sensors_result is not None:
                            sensors_significance = sensors_result.get("significance")
            except Exception:  # noqa: BLE001 — Sensors не должен ронять раунд
                logger.warning(
                    "[chat_id=%s] Sensors perception proposal failed; "
                    "deterministic attention only",
                    chat_id,
                )

    records: list[schemas.MessagePresenceCreate] = []
    for message in round_messages:
        message_id = getattr(message, "id", None)
        if message_id is None:
            continue
        deliveries = frozenset()
        if world_state is not None and settings.world_engine_threads_enabled:
            deliveries = await thread_delivery_ids_for_message(db, message)
        for character_id in character_ids:
            if world_state is not None:
                character = next(
                    (c for c in characters if c.id == character_id), None
                )
                if character is None:
                    continue
                message_world_state = (
                    perception.PerceptionWorldState(
                        adjacency=world_state.adjacency,
                        thread_deliveries=deliveries,
                    )
                    if deliveries
                    else world_state
                )
                presence = witness_model.perceive_presence_for_character(
                    message,
                    character,
                    message_world_state,
                    voice_known=witness_model.voice_familiarity(
                        character.id,
                        getattr(message, "character_id", None),
                        known_voices,
                    ),
                )
            else:
                presence = witness_model.compute_mvp_presence(
                    message,
                    character_id,
                    character_names,
                    viewer_location=locations.get(character_id, ""),
                    character_locations=locations,
                )
            attention = None
            if attention_ctx:
                ctx = attention_ctx.get(character_id, {})
                attention = _attention_score_for(
                    message=message,
                    character_id=character_id,
                    presence=presence,
                    character_names=character_names,
                    rel_targets=ctx.get("rel_targets", set()),
                    anchor_authors=ctx.get("anchor_authors", set()),
                    sensors_significance=sensors_significance,
                )
            records.append(
                schemas.MessagePresenceCreate(
                    message_id=message_id,
                    character_id=character_id,
                    presence=presence,
                    attention=attention,
                )
            )
    await upsert_message_presence_batch(db, records)


# ------------------------ Character Location --------------------------
async def update_character_location(
    db: AsyncSession, character_id: int, location: str
) -> models.Character | None:
    """Manually override a character's location.

    WPE 3.0 Фаза 8 (аудит legacy-полей §6 v2): строка ``location`` — только
    read-only legacy-bridge; источник — ``location_id``. Резолвим каноническую
    локацию и пишем оба поля одной транзакцией, чтобы не оставалось пути,
    обновляющего только строку (нерезолвленная/общая сцена → ``location_id``
    = None).
    """
    db_character = await get_character(db, character_id)
    if db_character is None:
        return None
    db_character.location = location
    db_character.location_id = None
    if location.strip():
        locations = await get_chat_locations(db, db_character.chat_id)
        resolved = resolve_location_name(locations, location)
        if resolved is not None:
            db_character.location_id = resolved.id
    await _sync_chat_player_location(db, db_character)
    await db.commit()
    await db.refresh(db_character)
    return db_character


async def get_character_locations_by_chat(
    db: AsyncSession, chat_id: int
) -> dict[int, str]:
    """Get current locations for all characters in a chat."""
    characters = await get_characters_by_chat(db, chat_id)
    return {c.id: c.location or "" for c in characters}


async def update_character_locations_batch(
    db: AsyncSession, chat_id: int, locations: dict[int, str]
) -> None:
    """Batch-update character locations from scene extraction.

    WPE 3.0 Фаза 8 (аудит legacy-полей): строка ``location`` — read-only
    legacy-bridge; резолвим и пишем ``location_id`` параллельно (источник —
    каноническая ``Location``). Нерезолвленная локация оставляет
    ``location_id`` без изменений (консервативно, обратная совместимость).
    """
    characters = await get_characters_by_chat(db, chat_id)
    char_map = {c.id: c for c in characters}
    loc_rows = await get_chat_locations(db, chat_id)
    changed = False
    for cid, loc in locations.items():
        cid_int = int(cid)
        if cid_int in char_map:
            char = char_map[cid_int]
            new_loc = loc.strip()
            if char.location != new_loc:
                char.location = new_loc
                resolved = resolve_location_name(loc_rows, new_loc)
                if resolved is not None:
                    char.location_id = resolved.id
                changed = True
    if changed:
        await db.commit()


# -------------------- WPE 3.0: Action Resolution (Фаза 5) --------------------
@dataclass
class ApplyActionsResult:
    """Результат применения действий `turn.actions` (WPE.md §5, Фаза 5).

    ``applied_moves`` / ``applied_messages`` — применённые действия с индексом
    в исходном ``turn.actions`` (для System Narrator: какие из них не отражены
    в тексте). ``rejected`` — отклонённые с причиной (невалидная локация /
    невалидные адресаты). Невалидное действие не портит валидные (#13).
    """

    applied_moves: list[dict] = dataclass_field(default_factory=list)
    applied_messages: list[dict] = dataclass_field(default_factory=list)
    rejected: list[dict] = dataclass_field(default_factory=list)


async def apply_character_actions(
    db: AsyncSession,
    chat_id: int,
    character: models.Character,
    turn: schemas.TurnOutput | None,
    *,
    round_id: str | None = None,
) -> ApplyActionsResult:
    """Применить структурированные действия персонажа атомарно (WPE.md §5).

    - ``move_to``: локация резолвится в каноническую ``Location`` (Фаза 1);
      успешный переезд обновляет ``character.location`` + ``location_id`` и
      создаёт immutable ``WorldEvent(move)`` с ``location_from``/``location_to``
      в ОДНОЙ транзакции (flush всех обновлений + один commit). Переезд в ту же
      локацию считается применённым без изменения состояния и без события.
    - ``send_message``: валидируются адресаты (участники чата); создаётся
      ``WorldEvent(speech)`` с ``target_character_ids`` и текущей локацией.
      Thread/remote_status формализуются в Фазе 6.
    - Порядок применения: ``move`` → зависящие от локации (``send_message``),
      внутри вида — исходный порядок (§5.5). ``location_id`` обновляется для
      успешных ``move_to`` (§5.6).
    - Невалидное действие отклоняется (нет ``WorldEvent``, нет изменения
      ``WorldState``, v2 §5.4) и не ломает валидные (#13).
    """
    result = ApplyActionsResult()
    if turn is None or not turn.actions:
        return result

    locations = await get_chat_locations(db, chat_id)
    characters = await get_characters_by_chat(db, chat_id)
    char_map = {c.id: c for c in characters}
    current_char = char_map.get(character.id, character)
    from_location = current_char.location or ""

    # ---- 1. Валидация предпосылок (§5.3) ----
    planned_moves: list[tuple[int, schemas.Action, models.Location | None]] = []
    planned_messages: list[tuple[int, schemas.Action, list[int]]] = []
    for index, action in enumerate(turn.actions):
        if action.type == "move_to":
            target = resolve_location_name(locations, action.location)
            if target is None:
                result.rejected.append(
                    {
                        "action_index": index,
                        "type": "move_to",
                        "reason": "unknown_location",
                        "location": action.location or "",
                    }
                )
            else:
                planned_moves.append((index, action, target))
        elif action.type == "send_message":
            raw_targets = [int(t) for t in action.target_character_ids]
            bad = [t for t in raw_targets if t not in char_map]
            if bad:
                result.rejected.append(
                    {
                        "action_index": index,
                        "type": "send_message",
                        "reason": "invalid_target",
                        "targets": bad,
                    }
                )
            else:
                planned_messages.append((index, action, raw_targets))
        else:
            result.rejected.append(
                {
                    "action_index": index,
                    "type": str(action.type),
                    "reason": "unsupported_action",
                }
            )

    # ---- 2. Применение: move → send_message, атомарно (§5.5) ----
    for index, action, target in planned_moves:
        to_canonical = target.name
        if _locations_same(from_location, to_canonical):
            result.applied_moves.append(
                {
                    "action_index": index,
                    "character_id": current_char.id,
                    "location_from": from_location,
                    "location_to": from_location,
                    "location_id": current_char.location_id,
                    "changed": False,
                }
            )
            continue
        current_char.location = to_canonical
        current_char.location_id = target.id
        db.add(
            models.WorldEvent(
                chat_id=chat_id,
                character_id=current_char.id,
                event_type="move",
                location=to_canonical,
                location_from=from_location,
                location_to=to_canonical,
                round_id=round_id,
                target_character_ids="[]",
            )
        )
        result.applied_moves.append(
            {
                "action_index": index,
                "character_id": current_char.id,
                "location_from": from_location,
                "location_to": to_canonical,
                "location_id": target.id,
                "changed": True,
            }
        )

    current_location = current_char.location or from_location
    for index, action, targets in planned_messages:
        db.add(
            models.WorldEvent(
                chat_id=chat_id,
                character_id=current_char.id,
                event_type="speech",
                location=current_location,
                round_id=round_id,
                target_character_ids=perception.serialize_target_ids(targets),
            )
        )
        if settings.world_engine_threads_enabled:
            await _ensure_thread_for_action(
                db, chat_id, action.channel, current_char.id, targets
            )
        result.applied_messages.append(
            {
                "action_index": index,
                "character_id": current_char.id,
                "target_character_ids": targets,
                "channel": action.channel,
            }
        )

    if result.applied_moves or result.applied_messages:
        await db.commit()
        await db.refresh(current_char)

    if result.applied_moves or result.applied_messages or result.rejected:
        logger.info(
            "[WPE-P5] actions chat_id=%d character=%s applied_moves=%d "
            "applied_messages=%d rejected=%d",
            chat_id,
            current_char.name,
            len(result.applied_moves),
            len(result.applied_messages),
            len(result.rejected),
        )
    return result


def _locations_same(a: str, b: str) -> bool:
    """Сравнение локаций по каноническому имени (legacy-bridge, как Фаза 1)."""
    return perception.locations_match(a, b)


# -------------------- WPE 3.0: Threads / мессенджер (Фаза 6) --------------------
async def _get_thread(
    db: AsyncSession, chat_id: int, channel: str
) -> models.Thread | None:
    """Существующий тред чата по каналу (без создания)."""
    stmt = (
        select(models.Thread)
        .where(models.Thread.chat_id == chat_id, models.Thread.channel == channel)
        .order_by(models.Thread.id)
        .limit(1)
    )
    return (await db.execute(stmt)).scalars().first()


async def get_or_create_thread(
    db: AsyncSession,
    chat_id: int,
    channel: str,
    *,
    name: str = "",
) -> models.Thread:
    """Тред/канал общения чата (мессенджер, звонок и т.д.) (Фаза 6)."""
    channel = (channel or "messenger").strip().lower()
    thread = await _get_thread(db, chat_id, channel)
    if thread is None:
        thread = models.Thread(chat_id=chat_id, channel=channel, name=name)
        db.add(thread)
        await db.flush()
    return thread


async def ensure_thread_participant(
    db: AsyncSession, thread: models.Thread, character_id: int
) -> models.ThreadParticipantState:
    """Гарантировать участие персонажа в треде (создаёт при отсутствии)."""
    stmt = select(models.ThreadParticipantState).where(
        models.ThreadParticipantState.thread_id == thread.id,
        models.ThreadParticipantState.character_id == character_id,
    )
    state = (await db.execute(stmt)).scalars().first()
    if state is None:
        state = models.ThreadParticipantState(
            thread_id=thread.id, character_id=character_id
        )
        db.add(state)
        await db.flush()
    return state


async def mark_thread_delivered(
    db: AsyncSession,
    thread: models.Thread,
    character_ids: list[int],
    message_id: int,
) -> None:
    """Отметить доставку сообщения адресатам треда (`remote_status=delivered`)."""
    for cid in character_ids:
        state = await ensure_thread_participant(db, thread, cid)
        if (
            state.last_delivered_message_id is None
            or message_id > state.last_delivered_message_id
        ):
            state.last_delivered_message_id = message_id


async def _ensure_thread_for_action(
    db: AsyncSession,
    chat_id: int,
    channel: str,
    author_id: int | None,
    targets: list[int],
) -> None:
    """Создать тред и участников по действию ``send_message`` (Фаза 6).

    Доставка (`mark_thread_delivered`) проставляется позже в ``create_message``
    для реального сообщения с ``message.id``.
    """
    if channel not in perception.REMOTE_CHANNELS:
        return
    thread = await get_or_create_thread(db, chat_id, channel)
    members = set(targets)
    if author_id is not None:
        members.add(author_id)
    for cid in members:
        await ensure_thread_participant(db, thread, cid)


async def ensure_message_thread_delivery(db: AsyncSession, message) -> None:
    """Создать/обновить ``Thread`` + ``ThreadParticipantState`` для события.

    Фаза 6 (WPE.md §4, Golden #6/#15): сообщение по удалённому каналу
    (magic/phone/radio/messenger) создаёт/обновляет тред; адресат получает
    ``remote_status=delivered`` независимо от локации. Вызывается из
    ``create_message`` в той же транзакции (атомарно).
    """
    if not settings.world_engine_threads_enabled:
        return
    channel = (getattr(message, "channel", None) or "direct").strip().lower()
    if channel not in perception.REMOTE_CHANNELS:
        return
    chat_id = getattr(message, "chat_id", None)
    message_id = getattr(message, "id", None)
    if chat_id is None or message_id is None:
        return
    thread = await get_or_create_thread(db, chat_id, channel)
    author_id = getattr(message, "character_id", None)
    targets = perception.parse_target_ids(
        getattr(message, "target_character_ids", None)
    )
    members = set(targets)
    if author_id is not None:
        members.add(author_id)
    for cid in members:
        await ensure_thread_participant(db, thread, cid)
    await mark_thread_delivered(db, thread, targets, message_id)


async def thread_delivery_ids_for_message(
    db: AsyncSession, message
) -> frozenset[int]:
    """Id персонажей, которым событие доставлено через тред (Фаза 6).

    Пустое множество, когда Threads выключены или сообщение не по удалённому
    каналу — тогда ``perceive()`` не отдаёт ``remote_status=delivered``.
    """
    if not settings.world_engine_threads_enabled:
        return frozenset()
    channel = (getattr(message, "channel", None) or "direct").strip().lower()
    if channel not in perception.REMOTE_CHANNELS:
        return frozenset()
    chat_id = getattr(message, "chat_id", None)
    message_id = getattr(message, "id", None)
    if chat_id is None or message_id is None:
        return frozenset()
    thread = await _get_thread(db, chat_id, channel)
    if thread is None:
        return frozenset()
    stmt = select(models.ThreadParticipantState).where(
        models.ThreadParticipantState.thread_id == thread.id,
        models.ThreadParticipantState.last_delivered_message_id.is_not(None),
        models.ThreadParticipantState.last_delivered_message_id >= message_id,
    )
    rows = (await db.execute(stmt)).scalars().all()
    return frozenset(state.character_id for state in rows)


async def _known_voices_for_chat(
    db: AsyncSession, chat_id: int
) -> dict[int, set[int]]:
    """``{observer_id: set(author_ids)}`` из отношений (источник voice familiarity).

    Голос автора считается знакомым наблюдателю, если у наблюдателя есть
    направленное ``CharacterRelationship`` к автору (WPE.md §4, Фаза 6).
    """
    stmt = select(models.CharacterRelationship).where(
        models.CharacterRelationship.chat_id == chat_id
    )
    rows = (await db.execute(stmt)).scalars().all()
    result: dict[int, set[int]] = {}
    for rel in rows:
        result.setdefault(rel.source_character_id, set()).add(
            rel.target_character_id
        )
    return result


# ----------------------------- Location -----------------------------
async def get_chat_locations(
    db: AsyncSession, chat_id: int
) -> list[models.Location]:
    """Get all locations for a chat (source of truth for CRUD/descriptions)."""
    stmt = (
        select(models.Location)
        .where(models.Location.chat_id == chat_id)
        .order_by(models.Location.name, models.Location.id)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_adjacency_index(
    db: AsyncSession, chat_id: int
) -> dict[str, set[str]]:
    """Build a normalized location -> {neighbors} index from ``locations.adjacent_to``.

    Used by the perception layer for AUDIBLE / MENTIONED levels (§6, Sprint 2).
    """
    locations = await get_chat_locations(db, chat_id)
    return perception.build_adjacency_index(locations)


async def get_location(db: AsyncSession, location_id: int) -> models.Location | None:
    return await db.get(models.Location, location_id)


# -------------------- WPE 3.0: резолвер строка -> location_id (Фаза 1) --------------------
# Резолвер написан в Фазе 0; с Фазы 1 подключён через backfill
# `characters.location_id` и каноническое сравнение в `perception.perceive()`.


def resolve_location_name(
    locations: list[models.Location], name: str | None
) -> models.Location | None:
    """Чистый резолвер: строковая локация → каноническая ``Location``.

    Регистронезависимый матч через ``perception.locations_match`` (тот же
    normalize, что и у сравнения строк в движке). "Общая сцена" (пустая
    строка / каноническое имя) → None: у общей сцены нет id.
    """
    needle = (name or "").strip()
    if not needle:
        return None
    if perception.is_shared_scene(perception.normalize_location(needle)):
        return None
    for loc in locations:
        if perception.locations_match(loc.name, needle):
            return loc
    return None


async def resolve_location_string(
    db: AsyncSession, chat_id: int, name: str | None
) -> models.Location | None:
    """Async-обёртка резолвера над списком локаций чата."""
    locations = await get_chat_locations(db, chat_id)
    return resolve_location_name(locations, name)


@dataclass
class LocationBackfillReport:
    """Результат backfill ``characters.location_id`` (WPE 3.0, Фаза 1).

    ``unresolved`` — персонажи, чья строковая локация не резолвится ни в
    одну локацию чата и не является общей сценой. Такие случаи НЕ
    проставляются молча: они требуют ручного разбора и попадают в отчёт.
    """

    total: int = 0
    resolved: int = 0
    shared_scene: int = 0
    unresolved: list[tuple[int, int, str, str]] = dataclass_field(
        default_factory=list
    )  # (chat_id, character_id, character_name, location)

    def lines(self) -> list[str]:
        """Человекочитаемые строки отчёта (для лога / скрипта)."""
        out = [
            f"total={self.total} resolved={self.resolved} "
            f"shared_scene={self.shared_scene} unresolved={len(self.unresolved)}"
        ]
        for chat_id, char_id, name, location in self.unresolved:
            out.append(f"  UNRESOLVED chat={chat_id} char={char_id} ({name!r}): {location!r}")
        return out


async def backfill_character_location_ids(
    db: AsyncSession, chat_id: int | None = None
) -> LocationBackfillReport:
    """Backfill ``characters.location_id`` из строковой ``characters.location``.

    WPE 3.0 (Plans/WPE.md §10, Фаза 1): для каждого персонажа (включая
    игрока) резолвит ``location`` через ``resolve_location_name``
    (регистронезависимо) и проставляет ``location_id``. Идемпотентно:
    повторный запуск обновляет только изменившиеся значения.

    Случаи, которые нельзя резолвить однозначно, не проставляются и
    фиксируются в отчёте на ручной разбор:
    - пустая строка / «Общая сцена» → id сбрасывается в None (у общей
      сцены нет id);
    - нерезолвленное имя (нет в ``locations`` чата) → остаётся None,
      заносится в ``report.unresolved``.

    Запуск — ``scripts/backfill_location_ids.py``.
    """
    stmt = select(models.Character)
    if chat_id is not None:
        stmt = stmt.where(models.Character.chat_id == chat_id)
    stmt = stmt.order_by(models.Character.chat_id, models.Character.id)
    characters = list((await db.execute(stmt)).scalars().all())

    locations_by_chat: dict[int, list[models.Location]] = {}
    report = LocationBackfillReport(total=len(characters))

    for character in characters:
        raw = (character.location or "").strip()
        if not raw or perception.is_shared_scene(
            perception.normalize_location(raw)
        ):
            if character.location_id is not None:
                character.location_id = None
            report.shared_scene += 1
            continue
        locs = locations_by_chat.get(character.chat_id)
        if locs is None:
            locs = await get_chat_locations(db, character.chat_id)
            locations_by_chat[character.chat_id] = locs
        loc = resolve_location_name(locs, raw)
        if loc is None:
            report.unresolved.append(
                (character.chat_id, character.id, character.name, raw)
            )
            continue
        if character.location_id != loc.id:
            character.location_id = loc.id
        report.resolved += 1

    await db.commit()
    return report


# -------------------- Sprint 0 backfills (Plans/update20.md) --------------------
@dataclass
class PlotBackfillReport:
    """Результат backfill ``chats.original_plot/story_prompt`` из ``general_prompt``.

    Copy, не move: ``general_prompt`` не меняется. Идемпотентно: заполняются
    только пустые поля (повторный запуск ничего не перезаписывает).
    """

    total: int = 0
    filled_original_plot: int = 0
    filled_story_prompt: int = 0
    story_enabled: int = 0  # всегда 0: флаг остаётся false до Sprint 8

    def lines(self) -> list[str]:
        return [
            f"total={self.total} filled_original_plot={self.filled_original_plot} "
            f"filled_story_prompt={self.filled_story_prompt} story_enabled={self.story_enabled}"
        ]


async def backfill_plot_fields(
    db: AsyncSession, chat_id: int | None = None
) -> PlotBackfillReport:
    """Backfill ``chats.original_plot`` / ``chats.story_prompt`` из ``general_prompt``.

    Sprint 0 (Plans/update20.md §16.1): начальные значения story-полей = копия
    ``general_prompt`` (copy, не move). ``story_enabled`` остаётся False — сюжет
    выключен до Sprint 8. Идемпотентно: заполняются только пустые значения.
    """
    stmt = select(models.Chat)
    if chat_id is not None:
        stmt = stmt.where(models.Chat.id == chat_id)
    stmt = stmt.order_by(models.Chat.id)
    chats = list((await db.execute(stmt)).scalars().all())

    report = PlotBackfillReport(total=len(chats))
    for chat in chats:
        source = chat.general_prompt or ""
        if not chat.original_plot:
            chat.original_plot = source
            report.filled_original_plot += 1
        if not chat.story_prompt:
            chat.story_prompt = source
            report.filled_story_prompt += 1
        if chat.story_enabled:
            # Защита от случайного включения: backfill не включает сюжет.
            chat.story_enabled = False
            report.story_enabled += 1

    await db.commit()
    return report


@dataclass
class EventLocationBackfillReport:
    """Результат backfill ``world_events.location_id`` из строковой ``location``.

    Аналог ``LocationBackfillReport``: нерезолвленные случаи НЕ проставляются
    и попадают в ``unresolved`` на ручной разбор.
    """

    total: int = 0
    resolved: int = 0
    shared_scene: int = 0
    unresolved: list[tuple[int, int, str, str]] = dataclass_field(
        default_factory=list
    )  # (chat_id, event_id, event_type, location)

    def lines(self) -> list[str]:
        out = [
            f"total={self.total} resolved={self.resolved} "
            f"shared_scene={self.shared_scene} unresolved={len(self.unresolved)}"
        ]
        for chat_id, event_id, event_type, location in self.unresolved:
            out.append(
                f"  UNRESOLVED chat={chat_id} event={event_id} "
                f"type={event_type!r}: {location!r}"
            )
        return out


async def backfill_event_location_ids(
    db: AsyncSession, chat_id: int | None = None
) -> EventLocationBackfillReport:
    """Backfill ``world_events.location_id`` из строковой ``world_events.location``.

    Sprint 0 (Plans/update20.md): каноническая локация события (аналог
    ``backfill_character_location_ids``). Пустая строка / «Общая сцена» → NULL;
    нерезолвленное имя → NULL + отчёт на ручной разбор. Идемпотентно.
    """
    stmt = select(models.WorldEvent)
    if chat_id is not None:
        stmt = stmt.where(models.WorldEvent.chat_id == chat_id)
    stmt = stmt.order_by(models.WorldEvent.chat_id, models.WorldEvent.id)
    events = list((await db.execute(stmt)).scalars().all())

    locations_by_chat: dict[int, list[models.Location]] = {}
    report = EventLocationBackfillReport(total=len(events))

    for event in events:
        raw = (event.location or "").strip()
        if not raw or perception.is_shared_scene(
            perception.normalize_location(raw)
        ):
            if event.location_id is not None:
                event.location_id = None
            report.shared_scene += 1
            continue
        locs = locations_by_chat.get(event.chat_id)
        if locs is None:
            locs = await get_chat_locations(db, event.chat_id)
            locations_by_chat[event.chat_id] = locs
        loc = resolve_location_name(locs, raw)
        if loc is None:
            report.unresolved.append(
                (event.chat_id, event.id, event.event_type, raw)
            )
            continue
        if event.location_id != loc.id:
            event.location_id = loc.id
        report.resolved += 1

    await db.commit()
    return report


async def _sync_chat_locations_cache(db: AsyncSession, chat_id: int) -> None:
    """Keep `chats.locations` (JSON array of names) in sync with the locations table.

    Таблица `locations` — источник истины; `chats.locations` остаётся кэшем
    названий для движка (§14).
    """
    chat = await get_chat(db, chat_id)
    if chat is None:
        return
    locs = await get_chat_locations(db, chat_id)
    chat.locations = json.dumps([l.name for l in locs], ensure_ascii=False)
    await db.commit()


def _location_name_conflict(
    existing: list[models.Location], new_name: str, exclude_id: int | None = None
) -> models.Location | None:
    """Case-insensitive duplicate check (совпадает с locations_match/normalize)."""
    for loc in existing:
        if exclude_id is not None and loc.id == exclude_id:
            continue
        if perception.locations_match(loc.name, new_name):
            return loc
    return None


async def create_location(
    db: AsyncSession, chat_id: int, location: schemas.LocationCreate
) -> models.Location:
    """Create a location; raises ValueError on duplicate name (→ 409)."""
    if await get_chat(db, chat_id) is None:
        raise ValueError("Чат не найден")
    name = (location.name or "").strip()
    if not name:
        raise ValueError("Название локации не может быть пустым")
    existing = await get_chat_locations(db, chat_id)
    conflict = _location_name_conflict(existing, name)
    if conflict is not None:
        raise ValueError(f"Локация «{conflict.name}» уже существует")
    db_location = models.Location(
        chat_id=chat_id,
        name=name,
        description=(location.description or ""),
        adjacent_to=perception.serialize_adjacency(
            getattr(location, "adjacent_to", None)
        ),
    )
    db.add(db_location)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ValueError(f"Локация «{name}» уже существует") from exc
    await db.refresh(db_location)
    await _sync_chat_locations_cache(db, chat_id)
    return db_location


async def update_location(
    db: AsyncSession, location_id: int, location_update: schemas.LocationUpdate
) -> models.Location | None:
    """Update a location; on rename syncs string references. ValueError → 409."""
    db_location = await get_location(db, location_id)
    if db_location is None:
        return None
    update_data = location_update.model_dump(exclude_unset=True)
    old_name = db_location.name
    new_name: str | None = None
    if update_data.get("name") is not None:
        new_name = (update_data["name"] or "").strip()
        if not new_name:
            raise ValueError("Название локации не может быть пустым")
        update_data["name"] = new_name
        if not perception.locations_match(old_name, new_name):
            existing = await get_chat_locations(db, db_location.chat_id)
            conflict = _location_name_conflict(existing, new_name, exclude_id=db_location.id)
            if conflict is not None:
                raise ValueError(f"Локация «{conflict.name}» уже существует")
    for field, value in update_data.items():
        if field == "adjacent_to":
            value = perception.serialize_adjacency(value)
        setattr(db_location, field, value)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ValueError(f"Локация «{new_name}» уже существует") from exc
    await db.refresh(db_location)

    if new_name is not None and not perception.locations_match(old_name, new_name):
        await _rename_location_references(db, db_location.chat_id, old_name, new_name)
    await _sync_chat_locations_cache(db, db_location.chat_id)
    return db_location


async def _rename_location_references(
    db: AsyncSession, chat_id: int, old_name: str, new_name: str
) -> None:
    """Синхронно обновить строковые ссылки при переименовании (§14)."""
    changed = False

    # characters.location (включая игрока)
    char_rows = await db.execute(
        select(models.Character.id, models.Character.location).where(
            models.Character.chat_id == chat_id
        )
    )
    char_updates: list[int] = []
    for char_id, loc in char_rows.all():
        if perception.locations_match(loc or "", old_name):
            char_updates.append(char_id)
    if char_updates:
        await db.execute(
            update(models.Character)
            .where(models.Character.id.in_(char_updates))
            .values(location=new_name)
        )
        changed = True

    # messages.location
    msg_rows = await db.execute(
        select(models.Message.id, models.Message.location).where(
            models.Message.chat_id == chat_id
        )
    )
    msg_updates: list[int] = []
    for msg_id, loc in msg_rows.all():
        if perception.locations_match(loc or "", old_name):
            msg_updates.append(msg_id)
    if msg_updates:
        await db.execute(
            update(models.Message)
            .where(models.Message.id.in_(msg_updates))
            .values(location=new_name)
        )
        changed = True

    # scene_states.character_locations (JSON dict: {id|name: location}) — только значения
    scene = await get_scene_state(db, chat_id)
    if scene is not None and scene.character_locations:
        raw = json.loads(scene.character_locations) if scene.character_locations else {}
        updated = {
            k: (new_name if perception.locations_match(str(v), old_name) else v)
            for k, v in raw.items()
        }
        if updated != raw:
            scene.character_locations = json.dumps(updated, ensure_ascii=False)
            changed = True

    # locations.adjacent_to (JSON-массив имён): заменить old_name на new_name
    # в соседях других локаций (Спринт 2, аудиосвязь локаций).
    loc_rows = await db.execute(
        select(models.Location.id, models.Location.adjacent_to).where(
            models.Location.chat_id == chat_id
        )
    )
    for loc_id, adjacent_json in loc_rows.all():
        neighbors = perception._parse_adjacency_list(adjacent_json)
        replaced = False
        for i, neighbor in enumerate(neighbors):
            if perception.locations_match(neighbor, old_name):
                neighbors[i] = new_name
                replaced = True
        if replaced:
            await db.execute(
                update(models.Location)
                .where(models.Location.id == loc_id)
                .values(adjacent_to=perception.serialize_adjacency(neighbors))
            )
            changed = True

    if changed:
        await db.commit()


async def get_characters_referencing_location(
    db: AsyncSession, location: models.Location
) -> list[models.Character]:
    """Characters whose location matches this location (case-insensitive)."""
    characters = await get_characters_by_chat(db, location.chat_id, include_player=True)
    return [
        c for c in characters
        if c.location and perception.locations_match(c.location, location.name)
    ]


async def delete_location(db: AsyncSession, location_id: int) -> models.Location | None:
    """Delete a location and sync the `chats.locations` cache."""
    db_location = await get_location(db, location_id)
    if db_location is None:
        return None
    chat_id = db_location.chat_id
    await db.delete(db_location)
    await db.commit()
    await _sync_chat_locations_cache(db, chat_id)
    return db_location


# ----------------------------- Scene State -----------------------------
async def get_scene_state(db: AsyncSession, chat_id: int) -> models.SceneState | None:
    """Get scene state for a chat."""
    return await db.get(models.SceneState, chat_id)


async def upsert_scene_state(
    db: AsyncSession, chat_id: int, scene_update: schemas.SceneStateUpdate
) -> models.SceneState:
    """Create or update scene state for a chat."""
    scene = await get_scene_state(db, chat_id)
    if scene is None:
        scene = models.SceneState(chat_id=chat_id)
        db.add(scene)

    import json

    update_data = scene_update.model_dump(exclude_unset=True)
    if "custom_state" in update_data and update_data["custom_state"] is not None:
        cs = update_data["custom_state"]
        if hasattr(cs, "model_dump_json"):
            update_data["custom_state"] = cs.model_dump_json()
        elif isinstance(cs, dict):
            update_data["custom_state"] = json.dumps(cs)

    if "character_locations" in update_data and update_data["character_locations"] is not None:
        cl = update_data["character_locations"]
        if isinstance(cl, dict):
            update_data["character_locations"] = json.dumps(cl)

    for field, value in update_data.items():
        setattr(scene, field, value)

    await db.commit()
    await db.refresh(scene)
    return scene


async def get_present_character_ids(db: AsyncSession, chat_id: int) -> list[int]:
    """Get character IDs with presence 'present' or 'told' for latest messages."""
    from sqlalchemy import select, desc

    # Get latest message IDs for this chat
    stmt = (
        select(models.Message.id)
        .where(models.Message.chat_id == chat_id)
        .order_by(desc(models.Message.timestamp), desc(models.Message.id))
        .limit(20)
    )
    result = await db.execute(stmt)
    message_ids = [row[0] for row in result.fetchall()]

    if not message_ids:
        return []

    # Get presence records for these messages
    stmt = select(models.MessagePresence).where(
        models.MessagePresence.message_id.in_(message_ids),
        models.MessagePresence.presence.in_(["present", "told"]),
    )
    result = await db.execute(stmt)
    presence_records = result.scalars().all()

    return list({pr.character_id for pr in presence_records})


async def get_scene_state_with_presence(
    db: AsyncSession, chat_id: int
) -> schemas.SceneStateRead:
    """Get scene state with computed present character IDs."""
    scene = await get_scene_state(db, chat_id)
    present_ids = await get_present_character_ids(db, chat_id)
    chat = await get_chat(db, chat_id)
    player_location = getattr(chat, "player_location", "") if chat else ""

    if scene is None:
        return schemas.SceneStateRead(
            chat_id=chat_id,
            updated_at=datetime.utcnow(),
            present_character_ids=present_ids,
            custom_state=schemas.SceneCustomState(),
            player_location=player_location,
        )

    import json

    custom_state_dict = json.loads(scene.custom_state) if scene.custom_state else {}
    custom_state = schemas.SceneCustomState(**custom_state_dict)

    character_locations_raw = json.loads(scene.character_locations) if scene.character_locations else {}
    # Ensure keys are strings (JSON serialization uses string keys)
    character_locations = {str(k): str(v) for k, v in character_locations_raw.items() if v}

    return schemas.SceneStateRead(
        chat_id=scene.chat_id,
        time_of_day=scene.time_of_day,
        character_locations=character_locations,
        custom_state=custom_state,
        updated_at=scene.updated_at,
        present_character_ids=present_ids,
        player_location=player_location,
    )


# ------------------------ Relationship round lookup (Sprint 4) ------------------------
def parse_round_id(round_id: str) -> Optional[Tuple[int, int]]:
    """Parse ``r{chat_id}-m{user_message_id}`` → ``(chat_id, user_message_id)``.

    Returns ``None`` for any malformed round id.
    """
    if not round_id or not round_id.startswith("r") or "-m" not in round_id:
        return None
    parts = round_id.split("-m")
    if len(parts) != 2:
        return None
    try:
        chat_id = int(parts[0][1:])
        user_message_id = int(parts[1])
    except (TypeError, ValueError):
        return None
    return chat_id, user_message_id


async def get_latest_round_id(
    db: AsyncSession, chat_id: int
) -> Optional[str]:
    """Most recent non-null ``round_id`` seen for any relationship in the chat.

    Used by the on-demand analyze endpoint when no explicit round is given.
    """
    stmt = (
        select(models.RelationshipEvent.round_id)
        .join(
            models.CharacterRelationship,
            models.CharacterRelationship.id == models.RelationshipEvent.relationship_id,
        )
        .where(
            models.CharacterRelationship.chat_id == chat_id,
            models.RelationshipEvent.round_id.isnot(None),
        )
        .order_by(models.RelationshipEvent.id.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalars().first()


async def get_round_messages_by_round_id(
    db: AsyncSession, round_id: str
) -> list[models.Message]:
    """Resolve ``r{chat_id}-m{user_msg_id}`` to the messages of that round.

    Returns the user message that started the round followed by every later
    message up to (but not including) the next ``role="user"`` message.
    Returns ``[]`` for a malformed or unknown round id.
    """
    parsed = parse_round_id(round_id)
    if parsed is None:
        return []
    chat_id, user_message_id = parsed

    user_msg = await db.get(models.Message, user_message_id)
    if user_msg is None or user_msg.chat_id != chat_id or user_msg.role != "user":
        return []

    stmt = (
        select(models.Message)
        .where(
            models.Message.chat_id == chat_id,
            models.Message.id >= user_message_id,
        )
        .options(selectinload(models.Message.character))
        .order_by(models.Message.timestamp, models.Message.id)
    )
    result = await db.execute(stmt)
    round_messages: list[models.Message] = []
    for message in result.scalars().all():
        if message.role == "user" and message.id != user_message_id:
            break
        round_messages.append(message)
    return round_messages


# ------------------------ Structured World Events (Sprint 1) ------------------------
def _clamp01(value: float | int | None) -> float | None:
    """Clamp 0..1; None passes through (движковые события салиенс не имеют)."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, f))


async def save_round_events(
    db: AsyncSession,
    chat_id: int,
    events: list[schemas.ExtractedEvent],
    *,
    round_id: str | None = None,
) -> schemas.EventExtractionReport:
    """Записать извлечённые раундные события + causal links (§15, Sprint 1).

    Event extraction (LLM) — отдельная от движковых ``speech``/``move`` запись:
    движковые события ``importance`` не заполняют (NULL), поэтому раунд с уже
    записанной extraction детектируется по ``importance IS NOT NULL`` — повторный
    прогон pipeline идемпотентен и не дублирует события/links.

    Event'ы с ``importance < settings.event_min_importance`` пропускаются
    (стоимостной лимит). Неизвестный персонаж / нерезолвнутая локация деградируют
    в NULL (FK nullable) — никогда не падают. ``causes`` — индексы в переданном
    списке ``events``; из них строятся ``EventLink(kind=causes)``.
    """
    report = schemas.EventExtractionReport(extraction_used=True)
    if not events:
        return report

    if round_id:
        stmt = (
            select(models.WorldEvent.id)
            .where(
                models.WorldEvent.chat_id == chat_id,
                models.WorldEvent.round_id == round_id,
                models.WorldEvent.importance.isnot(None),
            )
            .limit(1)
        )
        existing = (await db.execute(stmt)).scalars().first()
        if existing is not None:
            logger.info(
                "[Sprint1] round %s already has extracted events — skip", round_id
            )
            return report

    characters = await get_characters_by_chat(db, chat_id, include_player=True)
    name_to_id: dict[str, int] = {}
    for character in characters:
        key = (character.name or "").strip().casefold()
        if key:
            name_to_id.setdefault(key, character.id)

    locations = await get_chat_locations(db, chat_id)
    min_importance = float(settings.event_min_importance or 0.0)

    index_to_event: dict[int, models.WorldEvent] = {}
    skipped = 0
    for idx, ev in enumerate(events):
        try:
            imp = float(ev.importance or 0.0)
        except (TypeError, ValueError):
            imp = 0.0
        if imp < min_importance:
            skipped += 1
            continue
        source_id = name_to_id.get((ev.source_character or "").strip().casefold())
        target_ids: list[int] = []
        for target in ev.targets or []:
            tid = name_to_id.get((target or "").strip().casefold())
            if tid is not None:
                target_ids.append(tid)
        loc = resolve_location_name(locations, ev.location or "")
        action_data = ev.action.model_dump() if ev.action else {}
        event = models.WorldEvent(
            chat_id=chat_id,
            character_id=source_id,
            event_type=(ev.event_type or "event").strip() or "event",
            location=(ev.location or "").strip(),
            location_id=loc.id if loc else None,
            round_id=round_id,
            target_character_ids=json.dumps(target_ids, ensure_ascii=False),
            action=json.dumps(action_data, ensure_ascii=False),
            importance=imp,
            story_salience=_clamp01(ev.story_salience),
            emotional_salience=_clamp01(ev.emotional_salience),
        )
        db.add(event)
        index_to_event[idx] = event

    await db.flush()

    links = 0
    for idx, ev in enumerate(events):
        target_event = index_to_event.get(idx)
        if target_event is None:
            continue
        for cause_idx in ev.causes or []:
            if not isinstance(cause_idx, int):
                continue
            cause_event = index_to_event.get(cause_idx)
            if cause_event is None or cause_event.id == target_event.id:
                continue
            db.add(
                models.EventLink(
                    chat_id=chat_id,
                    event_id=target_event.id,
                    caused_by_event_id=cause_event.id,
                    kind="causes",
                )
            )
            links += 1

    await db.commit()
    report.written_events = len(index_to_event)
    report.written_links = links
    report.skipped_below_importance = skipped
    return report


# ------------------------ Character State (Sprint 3) ------------------------

def _clamp_json_number(value, low: float, high: float) -> float:
    """Clamp число для JSON-полей character_states (None проходит как None)."""
    if value is None:
        return None  # type: ignore[return-value]
    try:
        f = float(value)
    except (TypeError, ValueError):
        return low
    return max(low, min(high, f))


async def get_character_state(
    db: AsyncSession, character_id: int
) -> models.CharacterState | None:
    """Прочитать состояние персонажа (одна строка на персонажа)."""
    stmt = select(models.CharacterState).where(
        models.CharacterState.character_id == character_id
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_character_states_for_chat(
    db: AsyncSession, chat_id: int
) -> list[models.CharacterState]:
    """Прочитать состояния всех персонажей чата (для сводки/debug)."""
    stmt = (
        select(models.CharacterState)
        .where(models.CharacterState.chat_id == chat_id)
        .order_by(models.CharacterState.character_id)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_or_create_character_state(
    db: AsyncSession,
    chat_id: int,
    character_id: int,
    round_id: str | None = None,
) -> models.CharacterState:
    """Получить состояние персонажа или создать пустую строку (Sprint 3).

    Пустая строка: emotional_state '{}', mood '', stress NULL, physical_state
    '{}', attention NULL, active_goal '', personal_goals '[]'. Локация/отношения
    в state НЕ хранятся (берутся из существующих таблиц).
    """
    state = await get_character_state(db, character_id)
    if state is not None:
        return state
    state = models.CharacterState(
        chat_id=chat_id,
        character_id=character_id,
        emotional_state="{}",
        mood="",
        stress=None,
        physical_state="{}",
        attention=None,
        current_focus_id=None,
        active_goal="",
        personal_goals="[]",
        updated_round_id=round_id,
    )
    db.add(state)
    await db.commit()
    await db.refresh(state)
    return state


async def update_character_state(
    db: AsyncSession,
    character_id: int,
    *,
    emotional_state: dict | str | None = None,
    mood: str | None = None,
    stress: float | None = None,
    physical_state: dict | str | None = None,
    attention: str | None = None,
    current_focus_id: int | None = None,
    active_goal: str | None = None,
    personal_goals: list | str | None = None,
    updated_round_id: str | None = None,
) -> models.CharacterState | None:
    """Обновить состояние персонажа (частичное; None-поля НЕ сбрасываются,
    кроме явного attention/current_focus_id, передаваемых как есть)."""
    state = await get_character_state(db, character_id)
    if state is None:
        return None

    if emotional_state is not None:
        state.emotional_state = (
            json.dumps(emotional_state, ensure_ascii=False)
            if isinstance(emotional_state, dict)
            else str(emotional_state)
        )
    if mood is not None:
        state.mood = str(mood)
    if stress is not None:
        state.stress = _clamp_json_number(stress, 0.0, 1.0)
    if physical_state is not None:
        state.physical_state = (
            json.dumps(physical_state, ensure_ascii=False)
            if isinstance(physical_state, dict)
            else str(physical_state)
        )
    if attention is not None:
        state.attention = attention or None
    if current_focus_id is not None:
        state.current_focus_id = current_focus_id or None
    if active_goal is not None:
        state.active_goal = str(active_goal)
    if personal_goals is not None:
        state.personal_goals = (
            json.dumps(personal_goals, ensure_ascii=False)
            if isinstance(personal_goals, list)
            else str(personal_goals)
        )
    if updated_round_id is not None:
        state.updated_round_id = updated_round_id

    await db.commit()
    await db.refresh(state)
    return state


# ------------------------ Belief System (Sprint 5, Plans/update20.md §9) ----

async def get_beliefs_for_character(
    db: AsyncSession,
    character_id: int,
    *,
    top_k: int | None = None,
    min_confidence: float = 0.0,
) -> list[models.Belief]:
    """Beliefs персонажа (топ-K по confidence, §9).

    read-path: в контекст попадают ТОЛЬКО свои beliefs; пусто при выключенном
    ``beliefs_enabled`` (canary — никто не читает таблицу до включения флага).
    """
    if not settings.beliefs_enabled:
        return []
    stmt = select(models.Belief).where(
        models.Belief.character_id == character_id,
        models.Belief.confidence >= min_confidence,
    )
    if top_k is None:
        top_k = settings.beliefs_top_k
    stmt = stmt.order_by(models.Belief.confidence.desc(), models.Belief.id.desc())
    if top_k:
        stmt = stmt.limit(top_k)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_beliefs_for_chat(
    db: AsyncSession, chat_id: int
) -> list[models.Belief]:
    """Все beliefs чата (для debug/API; §29.1)."""
    stmt = (
        select(models.Belief)
        .where(models.Belief.chat_id == chat_id)
        .order_by(models.Belief.character_id, models.Belief.confidence.desc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def _find_belief(
    db: AsyncSession,
    character_id: int,
    subject: str,
    predicate: str,
    object: str,
) -> models.Belief | None:
    stmt = select(models.Belief).where(
        models.Belief.character_id == character_id,
        models.Belief.subject == subject,
        models.Belief.predicate == predicate,
        models.Belief.object == object,
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def upsert_belief(
    db: AsyncSession,
    chat_id: int,
    character_id: int,
    *,
    subject: str,
    predicate: str,
    object: str = "",
    source: str = "memory",
    confidence: float = 0.5,
    type: str = "belief",
    world_truth_ref: int | None = None,
) -> models.Belief:
    """Создать/обновить belief персонажа (Sprint 5).

    Ключ — (character_id, subject, predicate, object): повторное наблюдение
    повышает confidence (детерминированное слияние в belief_service), источник
    обновляется только на более сильный. Невалидные значения обрезаются
    (confidence → 0..1, source/type → известные значения).
    """
    if source not in ("direct_observation", "heard", "told_by", "inference", "rumor", "memory"):
        source = "memory"
    if type not in ("fact", "belief", "suspicion"):
        type = "belief"
    confidence = _clamp_json_number(confidence, 0.0, 1.0)
    subject = (subject or "").strip()
    predicate = (predicate or "").strip()
    if not subject or not predicate:
        raise ValueError("belief subject and predicate are required")

    belief = await _find_belief(db, character_id, subject, predicate, object)
    if belief is not None:
        from .belief_service import merge_confidence

        belief.confidence = merge_confidence(belief.confidence, confidence)
        belief.updated_at = datetime.utcnow()
        if world_truth_ref is not None:
            belief.world_truth_ref = world_truth_ref
        await db.commit()
        await db.refresh(belief)
        return belief
    belief = models.Belief(
        chat_id=chat_id,
        character_id=character_id,
        subject=subject,
        predicate=predicate,
        object=object,
        source=source,
        confidence=confidence,
        type=type,
        world_truth_ref=world_truth_ref,
    )
    db.add(belief)
    await db.commit()
    await db.refresh(belief)
    return belief


async def delete_belief(db: AsyncSession, belief_id: int) -> bool:
    """Удалить belief (для отката/debug)."""
    belief = await db.get(models.Belief, belief_id)
    if belief is None:
        return False
    await db.delete(belief)
    await db.commit()
    return True


# ------------------------ Round inputs for emotion engine (Sprint 3) ---------

async def get_relationship_events_for_round(
    db: AsyncSession, round_id: str | None
) -> list[dict]:
    """Relationship события раунда с source/target и дельтами (для emotion_engine).

    Join с ``character_relationships``: только направленные рёбра, которые реально
    изменились в этом раунде (kind='llm'). Пустой round_id → пустой список.
    """
    if not round_id:
        return []
    stmt = (
        select(models.RelationshipEvent, models.CharacterRelationship)
        .join(
            models.CharacterRelationship,
            models.CharacterRelationship.id == models.RelationshipEvent.relationship_id,
        )
        .where(
            models.RelationshipEvent.round_id == round_id,
            models.RelationshipEvent.kind == "llm",
        )
    )
    result = await db.execute(stmt)
    rows = []
    for event, rel in result.all():
        rows.append(
            {
                "source_character_id": rel.source_character_id,
                "target_character_id": rel.target_character_id,
                "delta_affection": event.delta_affection,
                "delta_trust": event.delta_trust,
                "delta_attraction": event.delta_attraction,
                "delta_resentment": event.delta_resentment,
                "delta_jealousy": event.delta_jealousy,
                "importance": event.importance,
            }
        )
    return rows


async def get_world_events_for_round(
    db: AsyncSession, round_id: str | None
) -> list[dict]:
    """World events раунда со структурной разметкой (эмоциональная салиенсность).

    Только extraction-события (emotional_salience/importance заполнены):
    движковые speech/move салиенс не имеют и эмоции не двигают.
    """
    if not round_id:
        return []
    stmt = (
        select(models.WorldEvent)
        .where(
            models.WorldEvent.round_id == round_id,
            models.WorldEvent.emotional_salience.isnot(None),
        )
        .order_by(models.WorldEvent.id)
    )
    result = await db.execute(stmt)
    events: list[dict] = []
    for event in result.scalars().all():
        try:
            target_ids = json.loads(event.target_character_ids or "[]")
        except (json.JSONDecodeError, TypeError):
            target_ids = []
        events.append(
            {
                "character_id": event.character_id,
                "event_type": event.event_type,
                "importance": event.importance,
                "emotional_salience": event.emotional_salience,
                "story_salience": event.story_salience,
                "target_character_ids": target_ids,
                "action": event.action,
            }
        )
    return events


async def get_round_world_events(
    db: AsyncSession, round_id: str | None
) -> list[dict]:
    """ВСЕ world events раунда (включая движковые speech/move, §9 pipeline).

    Для belief pipeline: каждое событие с ``message_id`` (речевое) привязывается
    к presence/attention через ``message_presence`` — так belief пишется ТОЛЬКО
    из событий, которые персонаж реально воспринял (изоляция R2). Возвращает
    ``{message_id, character_id, event_type, target_character_ids, action}``.
    """
    if not round_id:
        return []
    stmt = (
        select(models.WorldEvent)
        .where(models.WorldEvent.round_id == round_id)
        .order_by(models.WorldEvent.id)
    )
    result = await db.execute(stmt)
    events: list[dict] = []
    for event in result.scalars().all():
        try:
            target_ids = json.loads(event.target_character_ids or "[]")
        except (json.JSONDecodeError, TypeError):
            target_ids = []
        try:
            action = json.loads(event.action or "{}")
        except (json.JSONDecodeError, TypeError):
            action = {}
        if not isinstance(action, dict):
            action = {}
        events.append(
            {
                "id": event.id,
                "message_id": event.message_id,
                "character_id": event.character_id,
                "event_type": event.event_type,
                "target_character_ids": target_ids,
                "action": action,
            }
        )
    return events


# ------------------------ Dynamic Story State (Sprint 8) --------------------

def _parse_json_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []
    return []


async def get_story_state(
    db: AsyncSession, chat_id: int
) -> models.StoryState | None:
    """Прочитать Current Story State чата (одна строка на чат)."""
    stmt = select(models.StoryState).where(models.StoryState.chat_id == chat_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_or_create_story_state(
    db: AsyncSession, chat_id: int, round_id: str | None = None
) -> models.StoryState:
    """Получить story_state чата или создать пустую строку (Sprint 8).

    ``original_plot`` копируется из ``chats.original_plot`` как срез на момент
    версии (§16.2). Пустой ``current_story`` = '{}', ``story_phase`` = ''.
    """
    state = await get_story_state(db, chat_id)
    if state is not None:
        return state
    chat = await get_chat(db, chat_id)
    state = models.StoryState(
        chat_id=chat_id,
        original_plot=(chat.original_plot if chat is not None else "") or "",
        current_story="{}",
        story_phase="",
        updated_round_id=round_id,
    )
    db.add(state)
    await db.commit()
    await db.refresh(state)
    return state


async def update_story_state(
    db: AsyncSession,
    chat_id: int,
    *,
    original_plot: str | None = None,
    current_story: dict | str | None = None,
    story_phase: str | None = None,
    updated_round_id: str | None = None,
    version: int | None = None,
    last_consolidation_rounds: int | None = None,
) -> models.StoryState | None:
    """Обновить story_state чата (частичное; None-поля НЕ сбрасываются).

    ``version``/``last_consolidation_rounds`` — для Sprint 9: консолидация
    пишет новую версию (rollback = предыдущая версия остаётся при сбое).
    """
    state = await get_story_state(db, chat_id)
    if state is None:
        return None
    if original_plot is not None:
        state.original_plot = str(original_plot)
    if current_story is not None:
        state.current_story = (
            json.dumps(current_story, ensure_ascii=False)
            if isinstance(current_story, dict)
            else str(current_story)
        )
    if story_phase is not None:
        state.story_phase = str(story_phase)
    if updated_round_id is not None:
        state.updated_round_id = updated_round_id
    if version is not None:
        state.version = int(version)
    if last_consolidation_rounds is not None:
        state.last_consolidation_rounds = int(last_consolidation_rounds)
    await db.commit()
    await db.refresh(state)
    return state


async def get_story_events_for_chat(
    db: AsyncSession, chat_id: int, limit: int = 50
) -> list[models.StoryEvent]:
    """Story events чата (новые сначала)."""
    stmt = (
        select(models.StoryEvent)
        .where(models.StoryEvent.chat_id == chat_id)
        .order_by(models.StoryEvent.id.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def count_story_events(db: AsyncSession, chat_id: int) -> int:
    """Сколько story_events записано для чата (progress)."""
    stmt = (
        select(func.count())
        .select_from(models.StoryEvent)
        .where(models.StoryEvent.chat_id == chat_id)
    )
    result = await db.execute(stmt)
    return int(result.scalar_one() or 0)


async def count_distinct_rounds(db: AsyncSession, chat_id: int) -> int:
    """Число раундов чата (distinct round_id в world_events, Sprint 9 trigger).

    У каждого раунда, дошедшего до event-этапа, есть world_events — это
    детерминированная мера «сколько раундов прошло» для §17.1.
    """
    stmt = (
        select(func.count(func.distinct(models.WorldEvent.round_id)))
        .where(
            models.WorldEvent.chat_id == chat_id,
            models.WorldEvent.round_id.isnot(None),
        )
    )
    result = await db.execute(stmt)
    return int(result.scalar_one() or 0)


async def get_story_event_ids_for_chat(db: AsyncSession, chat_id: int) -> set[int]:
    """event_id уже записанных story_events чата (идемпотентность записи)."""
    stmt = select(models.StoryEvent.event_id).where(
        models.StoryEvent.chat_id == chat_id,
        models.StoryEvent.event_id.isnot(None),
    )
    result = await db.execute(stmt)
    return {int(eid) for eid in result.scalars().all() if eid is not None}


async def create_story_event(
    db: AsyncSession,
    *,
    event_id: int | None,
    chat_id: int,
    round_id: str | None,
    event: str,
    actors: list,
    location: str,
    cause: str,
    consequences: str,
    importance: float,
    story_thread_id: int | None = None,
) -> models.StoryEvent:
    """Проекция канонического world_event для сюжета (§16.3, Sprint 8)."""
    row = models.StoryEvent(
        event_id=event_id,
        chat_id=chat_id,
        round_id=round_id,
        event=(event or "").strip()[:4000],
        actors=json.dumps(actors, ensure_ascii=False),
        location=(location or "").strip()[:255],
        cause=(cause or "").strip()[:4000],
        consequences=(consequences or "").strip()[:4000],
        importance=int(round(float(importance))),
        story_thread_id=story_thread_id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def get_story_round_world_events(
    db: AsyncSession, chat_id: int, round_id: str | None
) -> list[dict]:
    """Extraction world_events раунда для сюжета (importance NOT NULL).

    Структурированные события (action/importance/story_salience) — кандидаты
    в ``story_events``. Движковые speech/move салиенс не имеют и сюжет не
    двигают (аналог ``get_world_events_for_round``, но с location/id).
    """
    if not round_id:
        return []
    stmt = (
        select(models.WorldEvent)
        .where(
            models.WorldEvent.chat_id == chat_id,
            models.WorldEvent.round_id == round_id,
            models.WorldEvent.importance.isnot(None),
        )
        .order_by(models.WorldEvent.id)
    )
    result = await db.execute(stmt)
    events: list[dict] = []
    for event in result.scalars().all():
        try:
            target_ids = json.loads(event.target_character_ids or "[]")
        except (json.JSONDecodeError, TypeError):
            target_ids = []
        try:
            action = json.loads(event.action or "{}")
        except (json.JSONDecodeError, TypeError):
            action = {}
        if not isinstance(action, dict):
            action = {}
        events.append(
            {
                "id": event.id,
                "character_id": event.character_id,
                "event_type": event.event_type or "",
                "location": event.location or "",
                "importance": event.importance,
                "story_salience": event.story_salience,
                "target_character_ids": target_ids,
                "action": action,
            }
        )
    return events


async def get_world_events_by_ids(
    db: AsyncSession, ids: list[int]
) -> dict[int, dict]:
    """Короткие тексты world_events по id (для cause/связей)."""
    if not ids:
        return {}
    stmt = select(models.WorldEvent).where(models.WorldEvent.id.in_(list(set(ids))))
    result = await db.execute(stmt)
    out: dict[int, dict] = {}
    for event in result.scalars().all():
        try:
            action = json.loads(event.action or "{}")
        except (json.JSONDecodeError, TypeError):
            action = {}
        if not isinstance(action, dict):
            action = {}
        out[event.id] = {"event_type": event.event_type or "", "action": action}
    return out


async def get_caused_by_ids_for_events(
    db: AsyncSession, chat_id: int, event_ids: list[int]
) -> dict[int, list[int]]:
    """event_id → список caused_by_event_id (события-причины, kind=causes)."""
    if not event_ids:
        return {}
    stmt = (
        select(models.EventLink.event_id, models.EventLink.caused_by_event_id)
        .where(
            models.EventLink.chat_id == chat_id,
            models.EventLink.event_id.in_(list(set(event_ids))),
            models.EventLink.kind == "causes",
        )
    )
    result = await db.execute(stmt)
    out: dict[int, list[int]] = {}
    for event_id, caused_by in result.all():
        out.setdefault(int(event_id), []).append(int(caused_by))
    return out


async def get_active_story_threads(
    db: AsyncSession, chat_id: int, top_k: int | None = None
) -> list[models.StoryThread]:
    """Активные story_threads чата (importance desc; top-K для контекста)."""
    stmt = (
        select(models.StoryThread)
        .where(
            models.StoryThread.chat_id == chat_id,
            models.StoryThread.status == "active",
        )
        .order_by(models.StoryThread.importance.desc(), models.StoryThread.id.desc())
    )
    if top_k:
        stmt = stmt.limit(top_k)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def find_story_thread_by_name(
    db: AsyncSession, chat_id: int, name: str
) -> models.StoryThread | None:
    """Найти thread по точному имени (casefold) — дедупликация линий."""
    needle = (name or "").strip().casefold()
    if not needle:
        return None
    stmt = (
        select(models.StoryThread)
        .where(models.StoryThread.chat_id == chat_id)
        .order_by(models.StoryThread.id.desc())
    )
    result = await db.execute(stmt)
    for thread in result.scalars().all():
        if (thread.name or "").strip().casefold() == needle:
            return thread
    return None


async def create_story_thread(
    db: AsyncSession,
    *,
    chat_id: int,
    name: str,
    actors: list,
    importance: float,
    status: str = "active",
    created_round_id: str | None = None,
) -> models.StoryThread:
    """Новая активная сюжетная линия (Sprint 8 write-path)."""
    row = models.StoryThread(
        chat_id=chat_id,
        name=(name or "").strip()[:500],
        actors=json.dumps(actors, ensure_ascii=False),
        importance=int(round(float(importance))),
        status=status,
        created_round_id=created_round_id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def update_story_thread(
    db: AsyncSession,
    thread_id: int,
    *,
    importance: float | None = None,
    actors: list | None = None,
    status: str | None = None,
) -> models.StoryThread | None:
    """Обновить thread: importance растёт (max), actors объединяются без дублей.

    ``status`` — для Sprint 9 (archived после завершения цели).
    """
    thread = await db.get(models.StoryThread, thread_id)
    if thread is None:
        return None
    if importance is not None:
        thread.importance = max(
            int(thread.importance or 0), int(round(float(importance)))
        )
    if actors is not None:
        merged = list(
            dict.fromkeys(
                [str(a) for a in _parse_json_list(thread.actors)]
                + [str(a) for a in actors]
            )
        )
        thread.actors = json.dumps(merged, ensure_ascii=False)
    if status is not None:
        thread.status = status
    await db.commit()
    await db.refresh(thread)
    return thread


async def set_story_event_thread(db: AsyncSession, event_id: int, thread_id: int) -> None:
    """Привязать story_event к story_thread (разметка проекции)."""
    await db.execute(
        update(models.StoryEvent)
        .where(models.StoryEvent.id == event_id)
        .values(story_thread_id=thread_id)
    )
    await db.commit()