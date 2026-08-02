"""Endpoints for managing characters and their memories."""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from .. import avatar_service
from .. import crud
from .. import schemas
from ..database import get_async_db

router = APIRouter(tags=["characters"])


@router.post(
    "/chats/{chat_id}/characters",
    response_model=schemas.CharacterRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_character(
    chat_id: int, character: schemas.CharacterCreate, db: AsyncSession = Depends(get_async_db)
):
    """Add a character to a chat (order_index is unique per chat)."""
    if await crud.get_chat(db, chat_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Чат не найден"
        )
    try:
        return await crud.create_character(db, chat_id, character)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


@router.get("/chats/{chat_id}/characters", response_model=list[schemas.CharacterRead])
async def list_characters(
    chat_id: int,
    include_player: bool = False,
    db: AsyncSession = Depends(get_async_db),
):
    """List characters for a chat, sorted by order_index.
    By default excludes the player character. Set include_player=true to include them."""
    if await crud.get_chat(db, chat_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Чат не найден"
        )
    return await crud.get_characters_by_chat(db, chat_id, include_player=include_player)


@router.put("/characters/{character_id}", response_model=schemas.CharacterRead)
async def update_character(
    character_id: int,
    character_update: schemas.CharacterUpdate,
    db: AsyncSession = Depends(get_async_db),
):
    """Update a character's card (name, personality, traits, order_index)."""
    char = await crud.get_character(db, character_id)
    if char is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Персонаж не найден"
        )
    if char.is_player and character_update.is_player is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Нельзя изменить статус игрока"
        )
    try:
        updated = await crud.update_character(db, character_id, character_update)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return updated


@router.delete("/characters/{character_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_character(character_id: int, db: AsyncSession = Depends(get_async_db)):
    """Delete a character (messages remain, memories cascade)."""
    try:
        if not await crud.delete_character(db, character_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Персонаж не найден"
            )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    avatar_service.remove_avatar(character_id)


@router.post(
    "/characters/{character_id}/avatar", response_model=schemas.CharacterRead
)
async def upload_character_avatar(
    character_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_async_db),
):
    """Upload/replace a character's avatar (PNG/JPEG/WebP, magic-byte checked).

    The file is validated (size, magic bytes), resized/re-encoded to WebP and
    saved to ``/static/avatars/{id}-{stamp}.webp``; ``avatar_url`` is updated.
    """
    if await crud.get_character(db, character_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Персонаж не найден"
        )
    try:
        avatar_url = await avatar_service.validate_and_save(file, character_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return await crud.update_character(
        db, character_id, schemas.CharacterUpdate(avatar_url=avatar_url)
    )


@router.delete(
    "/characters/{character_id}/avatar", response_model=schemas.CharacterRead
)
async def delete_character_avatar(
    character_id: int, db: AsyncSession = Depends(get_async_db)
):
    """Remove a character's avatar file and reset ``avatar_url``."""
    if await crud.get_character(db, character_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Персонаж не найден"
        )
    avatar_service.remove_avatar(character_id)
    return await crud.update_character(
        db, character_id, schemas.CharacterUpdate(avatar_url="")
    )


@router.put("/chats/{chat_id}/player", response_model=schemas.CharacterRead)
async def update_player_name(
    chat_id: int,
    update: schemas.CharacterUpdate,
    db: AsyncSession = Depends(get_async_db),
):
    """Update the player character's name."""
    player = await crud.get_player_character(db, chat_id)
    if player is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Игрок не найден"
        )
    if update.name is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Имя обязательно"
        )
    return await crud.update_character(
        db, player.id, schemas.CharacterUpdate(name=update.name)
    )


@router.get(
    "/characters/{character_id}/memories",
    response_model=list[schemas.MemoryRead],
)
async def list_character_memories(character_id: int, db: AsyncSession = Depends(get_async_db)):
    """List all memories for a character (chronological order)."""
    character = await crud.get_character(db, character_id)
    if character is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Персонаж не найден"
        )
    return await crud.get_memories_by_character(db, character_id)


@router.post(
    "/characters/{character_id}/memories",
    response_model=schemas.MemoryRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_memory(
    character_id: int, memory: schemas.MemoryCreate, db: AsyncSession = Depends(get_async_db)
):
    """Create a new memory for a character (auto-evicts lowest importance if limit reached)."""
    character = await crud.get_character(db, character_id)
    if character is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Персонаж не найден"
        )
    # Ensure chat_id matches character's chat
    if memory.chat_id != character.chat_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="chat_id не соответствует чату персонажа"
        )
    created = await crud.create_memory(db, memory)
    if created is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Дубликат памяти (такое содержимое уже существует)"
        )
    return created


@router.put("/memories/{memory_id}", response_model=schemas.MemoryRead)
async def update_memory(
    memory_id: int, memory_update: schemas.MemoryUpdate, db: AsyncSession = Depends(get_async_db)
):
    """Update memory content/importance/category."""
    updated = await crud.update_memory(db, memory_id, memory_update)
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Память не найдена"
        )
    return updated


@router.get(
    "/characters/{character_id}/summary",
    response_model=schemas.CharacterSummaryRead,
)
async def get_character_summary(character_id: int, db: AsyncSession = Depends(get_async_db)):
    """Return the current session summary for a character."""
    character = await crud.get_character(db, character_id)
    if character is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Персонаж не найден"
        )
    summary = await crud.get_character_summary(db, character_id)
    if summary is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Сводка не найдена"
        )
    return summary


@router.patch("/characters/{character_id}/location", response_model=schemas.CharacterRead)
async def update_character_location(
    character_id: int,
    location_update: schemas.CharacterLocationUpdate,
    db: AsyncSession = Depends(get_async_db),
):
    """Manually override a character's location (if LLM placed them wrong)."""
    updated = await crud.update_character_location(db, character_id, location_update.location)
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Персонаж не найден"
        )
    return updated


@router.delete("/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(memory_id: int, db: AsyncSession = Depends(get_async_db)):
    """Delete a specific memory entry."""
    if not await crud.delete_memory(db, memory_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Память не найдена"
        )