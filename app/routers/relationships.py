"""API endpoints for character relationships."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from .. import crud
from .. import schemas
from ..database import get_async_db
from ..relationship_service import (
    apply_delta,
    get_or_create_relationship,
    get_relationship,
    get_recent_events,
    list_received_relationships,
    list_relationships_for_character,
    update_relationship_fields,
)
from ..schemas import (
    CharacterRelationshipRead,
    CharacterRelationshipUpdate,
    RelationshipDelta,
    RelationshipEventRead,
)

router = APIRouter(tags=["relationships"])


@router.get(
    "/chats/{chat_id}/characters/{character_id}/relationships",
    response_model=list[CharacterRelationshipRead],
)
async def list_outgoing_relationships(
    chat_id: int,
    character_id: int,
    db: AsyncSession = Depends(get_async_db),
):
    """List relationships where this character is the source."""
    chat = await crud.get_chat(db, chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Чат не найден")
    return await list_relationships_for_character(db, character_id, chat_id=chat_id)


@router.get(
    "/chats/{chat_id}/characters/{character_id}/relationships/received",
    response_model=list[CharacterRelationshipRead],
)
async def list_incoming_relationships(
    chat_id: int,
    character_id: int,
    db: AsyncSession = Depends(get_async_db),
):
    """List relationships where this character is the target."""
    chat = await crud.get_chat(db, chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Чат не найден")
    return await list_received_relationships(db, character_id)


@router.get(
    "/chats/{chat_id}/relationships/{source_id}/{target_id}",
    response_model=CharacterRelationshipRead,
)
async def get_relationship_endpoint(
    chat_id: int,
    source_id: int,
    target_id: int,
    db: AsyncSession = Depends(get_async_db),
):
    rel = await get_relationship(db, source_id, target_id)
    if rel is None:
        raise HTTPException(status_code=404, detail="Отношение не найдено")
    return rel


@router.put(
    "/chats/{chat_id}/relationships/{source_id}/{target_id}",
    response_model=CharacterRelationshipRead,
)
async def update_relationship_endpoint(
    chat_id: int,
    source_id: int,
    target_id: int,
    update: CharacterRelationshipUpdate,
    db: AsyncSession = Depends(get_async_db),
):
    rel = await get_relationship(db, source_id, target_id)
    if rel is None:
        rel = await get_or_create_relationship(db, chat_id, source_id, target_id)
    return await update_relationship_fields(
        db, rel,
        relationship_type=update.relationship_type,
        affection=update.affection,
        trust=update.trust,
        attraction=update.attraction,
        resentment=update.resentment,
        jealousy=update.jealousy,
        description=update.description,
    )


@router.get(
    "/relationships/{relationship_id}/events",
    response_model=list[RelationshipEventRead],
)
async def list_relationship_events(
    relationship_id: int,
    db: AsyncSession = Depends(get_async_db),
):
    """List recent events for a relationship."""
    from sqlalchemy import select
    from ..models import CharacterRelationship, RelationshipEvent

    stmt = select(CharacterRelationship).where(CharacterRelationship.id == relationship_id)
    result = await db.execute(stmt)
    rel = result.scalar_one_or_none()
    if rel is None:
        raise HTTPException(status_code=404, detail="Отношение не найдено")
    return await get_recent_events(db, rel, limit=20)
