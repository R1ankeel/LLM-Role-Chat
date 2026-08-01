"""API endpoints for character relationships."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import crud
from .. import models
from .. import schemas
from ..database import get_async_db
from ..relationship_service import (
    apply_delta,
    get_or_create_relationship,
    get_relationship,
    get_recent_events,
    list_received_relationships,
    list_relationships_for_character,
    resolve_issue,
    update_relationship_fields,
)
from ..schemas import (
    CharacterRelationshipRead,
    CharacterRelationshipUpdate,
    RelationshipDelta,
    RelationshipEventRead,
    RelationshipIssueRead,
    RelationshipIssueResolve,
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
    stmt = select(models.CharacterRelationship).where(models.CharacterRelationship.id == relationship_id)
    result = await db.execute(stmt)
    rel = result.scalar_one_or_none()
    if rel is None:
        raise HTTPException(status_code=404, detail="Отношение не найдено")
    return await get_recent_events(db, rel, limit=20)


@router.get(
    "/chats/{chat_id}/relationships/{source_id}/{target_id}/issues",
    response_model=list[RelationshipIssueRead],
)
async def list_relationship_issues(
    chat_id: int,
    source_id: int,
    target_id: int,
    state: Literal["open", "resolved", "all"] = "open",
    db: AsyncSession = Depends(get_async_db),
):
    """List issues for a relationship pair (docs/relations.md §7)."""
    rel = await get_relationship(db, source_id, target_id)
    if rel is None:
        raise HTTPException(status_code=404, detail="Отношение не найдено")
    stmt = select(models.RelationshipIssue).where(
        models.RelationshipIssue.relationship_id == rel.id,
    )
    if state != "all":
        stmt = stmt.where(models.RelationshipIssue.state == state)
    stmt = stmt.order_by(
        models.RelationshipIssue.importance.desc(),
        models.RelationshipIssue.created_at.desc(),
        models.RelationshipIssue.id.desc(),
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.post(
    "/chats/{chat_id}/relationships/{source_id}/{target_id}/issues/{issue_id}/resolve",
    response_model=RelationshipIssueRead,
)
async def resolve_relationship_issue(
    chat_id: int,
    source_id: int,
    target_id: int,
    issue_id: int,
    payload: RelationshipIssueResolve,
    db: AsyncSession = Depends(get_async_db),
):
    """Resolve an open issue (only if it belongs to this pair, §7.2)."""
    rel = await get_relationship(db, source_id, target_id)
    if rel is None:
        raise HTTPException(status_code=404, detail="Отношение не найдено")
    issue = await resolve_issue(db, rel, issue_id, reason=payload.reason)
    if issue is None:
        raise HTTPException(
            status_code=404,
            detail="Открытый issue не найден или не принадлежит этой паре",
        )
    await db.commit()
    return issue
