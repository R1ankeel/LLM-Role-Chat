"""Endpoints for managing chats."""

import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from .. import crud
from .. import schemas
from ..context_state import ctx_state
from ..database import get_async_db

router = APIRouter(prefix="/chats", tags=["chats"])


@router.post("", response_model=schemas.ChatRead, status_code=status.HTTP_201_CREATED)
async def create_chat(chat: schemas.ChatCreate, db: AsyncSession = Depends(get_async_db)):
    """Create a new chat with player character."""
    db_chat = await crud.create_chat(db, chat)
    await crud.create_player_character(db, db_chat.id, name=chat.player_name or "Игрок")
    ctx_state.reset(db_chat.id)
    return db_chat


@router.get("", response_model=list[schemas.ChatRead])
async def list_chats(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_async_db)):
    """List all chats (newest first)."""
    return await crud.get_chats(db, skip=skip, limit=limit)


@router.get("/{chat_id}", response_model=schemas.ChatDetail)
async def get_chat_detail(chat_id: int, db: AsyncSession = Depends(get_async_db)):
    """Chat detail: card + characters + last 50 messages."""
    chat = await crud.get_chat(db, chat_id)
    if chat is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Чат не найден"
        )
    characters = await crud.get_characters_by_chat(db, chat_id, include_player=True)
    messages = await crud.get_messages_by_chat(db, chat_id, limit=50)
    return schemas.ChatDetail(
        **schemas.ChatRead.model_validate(chat).model_dump(),
        characters=[schemas.CharacterRead.model_validate(c) for c in characters],
        messages=[schemas.MessageRead.model_validate(m) for m in messages],
    )


@router.put("/{chat_id}", response_model=schemas.ChatRead)
async def update_chat(
    chat_id: int, chat_update: schemas.ChatUpdate, db: AsyncSession = Depends(get_async_db)
):
    """Update chat name / prompt / model / history length."""
    updated = await crud.update_chat(db, chat_id, chat_update)
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Чат не найден"
        )
    return updated


@router.delete("/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat(chat_id: int, db: AsyncSession = Depends(get_async_db)):
    """Delete chat with all characters, messages and memories."""
    if not await crud.delete_chat(db, chat_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Чат не найден"
        )
    ctx_state.remove(chat_id)


@router.delete("/{chat_id}/messages", status_code=status.HTTP_204_NO_CONTENT)
async def clear_messages(
    chat_id: int,
    scope: str = "messages",
    db: AsyncSession = Depends(get_async_db),
):
    """Clear chat history with configurable scope.

    scope:
      - messages: delete messages only
      - messages_memories: delete messages + memories
      - full: delete all interaction history (messages + memories + summaries
        + relationships + world events + threads + memory jobs); characters
        and locations are preserved
    """
    if scope == "messages":
        ok = await crud.clear_chat_messages(db, chat_id)
    elif scope == "messages_memories":
        ok = await crud.clear_chat_messages(db, chat_id)
        if ok:
            await crud.clear_chat_memories(db, chat_id)
    elif scope == "full":
        ok = await crud.clear_chat_messages(db, chat_id)
        if ok:
            await crud.clear_chat_memories(db, chat_id)
            await crud.reset_character_summaries_for_chat(db, chat_id)
            await crud.clear_chat_relationships(db, chat_id)
            await crud.clear_chat_world_events(db, chat_id)
            await crud.clear_chat_threads(db, chat_id)
            await crud.clear_chat_memory_jobs(db, chat_id)
            ctx_state.reset(chat_id)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Недопустимый scope: используйте messages, messages_memories или full",
        )

    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Чат не найден",
        )


# ------------------------- Scene State (P3) -------------------------
@router.get("/{chat_id}/scene", response_model=schemas.SceneStateRead)
async def get_scene_state(chat_id: int, db: AsyncSession = Depends(get_async_db)):
    """Get current scene state with per-character locations."""
    if await crud.get_chat(db, chat_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Чат не найден"
        )
    return await crud.get_scene_state_with_presence(db, chat_id)


@router.patch("/{chat_id}/scene", response_model=schemas.SceneStateRead)
async def update_scene_state(
    chat_id: int, update: schemas.SceneStateUpdate, db: AsyncSession = Depends(get_async_db)
):
    """Manually update scene state (time, character locations, custom state)."""
    if await crud.get_chat(db, chat_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Чат не найден"
        )
    scene = await crud.upsert_scene_state(db, chat_id, update)
    present_ids = await crud.get_present_character_ids(db, chat_id)
    import json
    custom_state_dict = json.loads(scene.custom_state) if scene.custom_state else {}
    custom_state = schemas.SceneCustomState(**custom_state_dict)
    character_locations_raw = json.loads(scene.character_locations) if scene.character_locations else {}
    character_locations = {str(k): str(v) for k, v in character_locations_raw.items() if v}
    return schemas.SceneStateRead(
        chat_id=scene.chat_id,
        time_of_day=scene.time_of_day,
        character_locations=character_locations,
        custom_state=custom_state,
        updated_at=scene.updated_at,
        present_character_ids=present_ids,
    )


# ------------------------- Story State (Sprint 8, §16) -------------------------
def _story_response(chat, story_state, active_threads, recent_events):
    """Собрать StoryStateResponse из ORM-объектов."""
    return schemas.StoryStateResponse(
        id=story_state.id,
        chat_id=story_state.chat_id,
        original_plot=story_state.original_plot or "",
        current_story=_parse_story_json(story_state.current_story),
        story_phase=story_state.story_phase or "",
        updated_round_id=story_state.updated_round_id,
        version=story_state.version,
        updated_at=story_state.updated_at,
        active_threads=[
            schemas.StoryThreadRead.model_validate(t) for t in active_threads
        ],
        recent_events=[
            schemas.StoryEventRead.model_validate(e) for e in recent_events
        ],
        story_enabled=bool(getattr(chat, "story_enabled", False)),
        story_prompt=chat.story_prompt or "",
    )


def _parse_story_json(raw: str) -> dict:
    import json as _json

    if not raw:
        return {}
    try:
        parsed = _json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (_json.JSONDecodeError, TypeError):
        return {}


@router.get("/{chat_id}/story", response_model=schemas.StoryStateResponse)
async def get_story_state(chat_id: int, db: AsyncSession = Depends(get_async_db)):
    """Current Story State + активные потоки + последние события сюжета."""
    chat = await crud.get_chat(db, chat_id)
    if chat is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Чат не найден"
        )
    state = await crud.get_or_create_story_state(db, chat_id)
    threads = await crud.get_active_story_threads(db, chat_id)
    events = await crud.get_story_events_for_chat(db, chat_id, limit=30)
    return _story_response(chat, state, threads, events)


@router.patch("/{chat_id}/story", response_model=schemas.StoryStateResponse)
async def update_story_state(
    chat_id: int,
    update: schemas.StoryStateUpdate,
    db: AsyncSession = Depends(get_async_db),
):
    """Обновить story state — ТОЛЬКО пользователь (original_plot user-only).

    LLM не имеет write-path к ``original_plot``/``story_prompt``; здесь их
    правит игрок/пользователь. ``story_enabled`` включает сюжет для чата
    (при включении ``story_prompt`` пустой — инициализируется из
    ``general_prompt``). ``current_story`` — частичный JSON (merge).
    """
    chat = await crud.get_chat(db, chat_id)
    if chat is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Чат не найден"
        )
    state = await crud.get_or_create_story_state(db, chat_id)

    update_data = update.model_dump(exclude_unset=True)
    chat_changed = False

    if "story_enabled" in update_data:
        chat.story_enabled = bool(update_data["story_enabled"])
        if chat.story_enabled and not (chat.story_prompt or "").strip():
            chat.story_prompt = chat.general_prompt or chat.original_plot or ""
        chat_changed = True
    if "story_prompt" in update_data:
        chat.story_prompt = update_data["story_prompt"]
        chat_changed = True
    if chat_changed:
        await db.commit()
        await db.refresh(chat)

    if "original_plot" in update_data:
        plot = update_data["original_plot"]
        chat.original_plot = plot or ""
        state.original_plot = plot or ""
        await db.commit()
        await db.refresh(chat)

    if "current_story" in update_data and update_data["current_story"] is not None:
        merged = _parse_story_json(state.current_story)
        merged.update(update_data["current_story"])
        state.current_story = json.dumps(merged, ensure_ascii=False)

    if "story_phase" in update_data:
        state.story_phase = update_data["story_phase"] or ""

    if "current_story" in update_data or "story_phase" in update_data:
        await db.commit()
        await db.refresh(state)

    threads = await crud.get_active_story_threads(db, chat_id)
    events = await crud.get_story_events_for_chat(db, chat_id, limit=30)
    return _story_response(chat, state, threads, events)