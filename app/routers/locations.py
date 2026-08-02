"""Endpoints for managing locations (Локации 2.0).

Источник истины локаций — таблица ``locations``; ``chats.locations`` (JSON
массив названий) синхронизируется как кэш для движка при каждой CRUD-операции.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from .. import crud
from .. import schemas
from ..database import get_async_db

router = APIRouter(tags=["locations"])


@router.get("/chats/{chat_id}/locations", response_model=list[schemas.LocationRead])
async def list_locations(
    chat_id: int, db: AsyncSession = Depends(get_async_db)
):
    """List all locations for a chat (sorted by name)."""
    if await crud.get_chat(db, chat_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Чат не найден"
        )
    return await crud.get_chat_locations(db, chat_id)


@router.post(
    "/chats/{chat_id}/locations",
    response_model=schemas.LocationRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_location(
    chat_id: int,
    location: schemas.LocationCreate,
    db: AsyncSession = Depends(get_async_db),
):
    """Create a location (name unique per chat; duplicate → 409)."""
    if await crud.get_chat(db, chat_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Чат не найден"
        )
    try:
        return await crud.create_location(db, chat_id, location)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc


@router.put(
    "/chats/{chat_id}/locations/{location_id}",
    response_model=schemas.LocationRead,
)
async def update_location(
    chat_id: int,
    location_id: int,
    location_update: schemas.LocationUpdate,
    db: AsyncSession = Depends(get_async_db),
):
    """Update a location; on rename syncs characters/messages/scene references."""
    loc = await crud.get_location(db, location_id)
    if loc is None or loc.chat_id != chat_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Локация не найдена"
        )
    try:
        return await crud.update_location(db, location_id, location_update)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc


@router.delete(
    "/chats/{chat_id}/locations/{location_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_location(
    chat_id: int,
    location_id: int,
    db: AsyncSession = Depends(get_async_db),
):
    """Delete a location.

    Если на локацию ссылаются персонажи — 409 с информацией о них
    (локация не удаляется молча, битых ссылок не допускаем).
    """
    loc = await crud.get_location(db, location_id)
    if loc is None or loc.chat_id != chat_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Локация не найдена"
        )
    referencing = await crud.get_characters_referencing_location(db, loc)
    if referencing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Локация используется персонажами",
                "characters": [c.name for c in referencing],
            },
        )
    await crud.delete_location(db, location_id)
