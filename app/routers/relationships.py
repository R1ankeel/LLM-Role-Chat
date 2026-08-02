"""API endpoints for character relationships."""

import json
import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .. import chat_engine
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
    prune_relationship_events,
    resolve_issue,
    update_relationship_fields,
    validate_relationship_type_update,
)
from ..schemas import (
    CharacterRelationshipRead,
    CharacterRelationshipUpdate,
    RelationshipDelta,
    RelationshipEventRead,
    RelationshipIssueRead,
    RelationshipIssueResolve,
)

logger = logging.getLogger(__name__)

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

    if update.relationship_type is not None:
        is_valid, error_msg = validate_relationship_type_update(
            rel.relationship_type, update.relationship_type
        )
        if not is_valid:
            raise HTTPException(status_code=400, detail=error_msg)

    updated = await update_relationship_fields(
        db, rel,
        relationship_type=update.relationship_type,
        affection=update.affection,
        trust=update.trust,
        attraction=update.attraction,
        resentment=update.resentment,
        jealousy=update.jealousy,
        description=update.description,
    )
    # Fold old events into an archive after a manual update (Sprint 4 item 3).
    try:
        await prune_relationship_events(db, rel.id)
    except Exception as exc:
        logger.warning("Pruning failed after manual update: %s", exc)
    await db.commit()
    await db.refresh(updated)
    return updated


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


# ---------------------------------------------------------------------------
# On-demand analysis + timeline (Sprint 4 items 4.2–4.3)
# ---------------------------------------------------------------------------
@router.post("/chats/{chat_id}/relationships/analyze")
async def analyze_relationships_on_demand(
    chat_id: int,
    request: Request,
    round_id: Optional[str] = None,
    db: AsyncSession = Depends(get_async_db),
):
    """Re-run relationship analysis for one round synchronously (Sprint 4 item 4.2).

    ``round_id`` defaults to the most recent round that produced relationship
    events. Pass ``?round_id=r{chat_id}-m{user_message_id}`` explicitly to
    re-analyze a specific round. Returns the batch summary:
    ``{round_id, analyzed_pairs, applied_deltas, created_issues, ...}``.
    """
    chat = await crud.get_chat(db, chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Чат не найден")

    if not round_id:
        round_id = await crud.get_latest_round_id(db, chat_id)
        if not round_id:
            raise HTTPException(
                status_code=400,
                detail="No round_id provided and no existing rounds",
            )

    round_messages = await crud.get_round_messages_by_round_id(db, round_id)
    if not round_messages:
        raise HTTPException(status_code=404, detail="Сообщения раунда не найдены")
    round_snapshots = [chat_engine._message_snapshot(m) for m in round_messages]

    characters = await crud.get_characters_by_chat(db, chat_id, include_player=False)
    character_snapshots = [
        {
            "id": c.id,
            "name": c.name,
            "location": getattr(c, "location", "") or "",
        }
        for c in characters
    ]

    client = request.app.state.ollama_client
    return await chat_engine._analyze_and_update_relationships(
        client, chat_id, chat.model_name,
        round_snapshots, character_snapshots,
        round_id=round_id,
    )


@router.get("/chats/{chat_id}/relationships/{source_id}/{target_id}/timeline")
async def get_relationship_timeline(
    chat_id: int,
    source_id: int,
    target_id: int,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_async_db),
):
    """Paginated relationship timeline: events + issues + source messages (Sprint 4 item 4.3).

    ``limit`` is clamped to [1, 500], ``offset`` to >= 0. Events and issues are
    each paginated independently; source messages referenced by the events page
    are joined from the ``messages`` table.
    """
    rel = await get_relationship(db, source_id, target_id)
    if rel is None:
        raise HTTPException(status_code=404, detail="Отношение не найдено")

    events_stmt = (
        select(models.RelationshipEvent)
        .where(models.RelationshipEvent.relationship_id == rel.id)
        .order_by(models.RelationshipEvent.timestamp, models.RelationshipEvent.id)
        .offset(offset)
        .limit(limit)
    )
    events = list((await db.execute(events_stmt)).scalars().all())

    issues_stmt = (
        select(models.RelationshipIssue)
        .where(models.RelationshipIssue.relationship_id == rel.id)
        .order_by(models.RelationshipIssue.created_at, models.RelationshipIssue.id)
        .offset(offset)
        .limit(limit)
    )
    issues = list((await db.execute(issues_stmt)).scalars().all())

    source_ids: set[int] = set()
    for ev in events:
        try:
            source_ids.update(int(i) for i in json.loads(ev.source_message_ids or "[]"))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue

    messages = []
    if source_ids:
        msgs_stmt = (
            select(models.Message)
            .where(models.Message.id.in_(source_ids))
            .options(selectinload(models.Message.character))
            .order_by(models.Message.timestamp, models.Message.id)
        )
        messages = list((await db.execute(msgs_stmt)).scalars().all())

    msg_map = {
        m.id: {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "timestamp": m.timestamp.isoformat() if m.timestamp else None,
        }
        for m in messages
    }

    def _serialize_event(ev) -> dict:
        try:
            ev_source_ids = [int(i) for i in json.loads(ev.source_message_ids or "[]")]
        except (json.JSONDecodeError, TypeError, ValueError):
            ev_source_ids = []
        return {
            "id": ev.id,
            "kind": ev.kind,
            "description": ev.description,
            "reason": ev.reason,
            "delta_affection": ev.delta_affection,
            "delta_trust": ev.delta_trust,
            "delta_attraction": ev.delta_attraction,
            "delta_resentment": ev.delta_resentment,
            "delta_jealousy": ev.delta_jealousy,
            "affection_after": ev.affection_after,
            "trust_after": ev.trust_after,
            "attraction_after": ev.attraction_after,
            "resentment_after": ev.resentment_after,
            "jealousy_after": ev.jealousy_after,
            "importance": ev.importance,
            "round_id": ev.round_id,
            "timestamp": ev.timestamp.isoformat() if ev.timestamp else None,
            "source_messages": [msg_map[i] for i in ev_source_ids if i in msg_map],
        }

    total_events = (
        (await db.execute(
            select(func.count()).select_from(models.RelationshipEvent).where(
                models.RelationshipEvent.relationship_id == rel.id
            )
        )).scalar()
        or 0
    )
    total_issues = (
        (await db.execute(
            select(func.count()).select_from(models.RelationshipIssue).where(
                models.RelationshipIssue.relationship_id == rel.id
            )
        )).scalar()
        or 0
    )

    return {
        "events": [_serialize_event(ev) for ev in events],
        "issues": [
            schemas.RelationshipIssueRead.model_validate(issue).model_dump(mode="json")
            for issue in issues
        ],
        "messages": list(msg_map.values()),
        "pagination": {
            "limit": limit,
            "offset": offset,
            "total_events": total_events,
            "total_issues": total_issues,
            "total": total_events + total_issues,
        },
    }
