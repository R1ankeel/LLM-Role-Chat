"""Read-only debug observability contour (Plans/update20.md §29.1).

GET-only endpoints mapping onto existing tables via `crud.py` — no new DB.
Served only when ``settings.debug_enabled`` is on (safety: plan §29.1).
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from .. import crud
from ..config import settings
from ..database import get_async_db

router = APIRouter(tags=["debug"])

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _require_debug_enabled() -> None:
    if not settings.debug_enabled:
        raise HTTPException(status_code=404, detail="Не найдено")


async def _get_chat_or_404(db: AsyncSession, chat_id: int):
    chat = await crud.get_chat(db, chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="Чат не найден")
    return chat


def _serialize_state(state) -> dict:
    data = {
        "character_id": state.character_id,
        "emotional_state": json.loads(state.emotional_state or "{}"),
        "mood": state.mood,
        "stress": state.stress,
        "physical_state": json.loads(state.physical_state or "{}"),
        "attention": state.attention,
        "current_focus_id": state.current_focus_id,
        "active_goal": state.active_goal,
        "personal_goals": json.loads(state.personal_goals or "[]"),
        "updated_round_id": state.updated_round_id,
        "updated_at": state.updated_at.isoformat() if state.updated_at else None,
    }
    return data


def _serialize_belief(b) -> dict:
    return {
        "id": b.id,
        "character_id": b.character_id,
        "subject": b.subject,
        "predicate": b.predicate,
        "object": b.object,
        "source": b.source,
        "confidence": b.confidence,
        "type": b.type,
        "world_truth_ref": b.world_truth_ref,
        "updated_at": b.updated_at.isoformat() if b.updated_at else None,
    }


def _serialize_thread(t) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "actors": json.loads(t.actors or "[]"),
        "importance": t.importance,
        "status": t.status,
        "created_round_id": t.created_round_id,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


def _serialize_intent(i) -> dict:
    return {
        "id": i.id,
        "character_id": i.character_id,
        "goal": i.goal,
        "target": i.target,
        "approach": i.approach,
        "urgency": i.urgency,
        "emotion": i.emotion,
        "risk": i.risk,
        "created_round_id": i.created_round_id,
        "created_at": i.created_at.isoformat() if i.created_at else None,
    }


def _serialize_event(e) -> dict:
    return {
        "id": e.id,
        "character_id": e.character_id,
        "message_id": e.message_id,
        "event_type": e.event_type,
        "location": e.location,
        "location_id": e.location_id,
        "location_from": e.location_from,
        "location_to": e.location_to,
        "round_id": e.round_id,
        "target_character_ids": json.loads(e.target_character_ids or "[]"),
        "action": json.loads(e.action or "{}"),
        "importance": e.importance,
        "story_salience": e.story_salience,
        "emotional_salience": e.emotional_salience,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


def _serialize_anchor(a) -> dict:
    return {
        "id": a.id,
        "relationship_id": a.relationship_id,
        "event_id": a.event_id,
        "emotion": a.emotion,
        "valence": a.valence,
        "intensity": a.intensity,
        "importance": a.importance,
        "timestamp": a.timestamp.isoformat() if a.timestamp else None,
    }


@router.get("/debug/{chat_id}")
async def debug_page(chat_id: int):
    """Minimal debug UI page (§29.1, Sprint 13)."""
    if not settings.debug_enabled:
        raise HTTPException(status_code=404, detail="Не найдено")
    return FileResponse(STATIC_DIR / "debug.html")


@router.get("/chats/{chat_id}/debug/state")
async def debug_state(chat_id: int, db: AsyncSession = Depends(get_async_db)):
    """Summary: story_states, character_states, beliefs, intents, threads."""
    _require_debug_enabled()
    await _get_chat_or_404(db, chat_id)

    story_state = await crud.get_story_state(db, chat_id)
    character_states = await crud.get_character_states_for_chat(db, chat_id)
    beliefs = await crud.get_beliefs_for_chat(db, chat_id)
    intents = []
    characters = await crud.get_characters_by_chat(db, chat_id, include_player=True)
    for char in characters:
        intents.extend(
            await crud.get_intents_for_character(db, chat_id, char.id, limit=5)
        )
    threads = await crud.get_active_story_threads(db, chat_id)

    return {
        "chat_id": chat_id,
        "story_state": (
            {
                "story_phase": story_state.story_phase,
                "version": story_state.version,
                "last_consolidation_rounds": story_state.last_consolidation_rounds,
                "current_story": json.loads(story_state.current_story or "{}"),
                "updated_round_id": story_state.updated_round_id,
            }
            if story_state
            else None
        ),
        "character_states": [
            _serialize_state(s) for s in character_states
        ],
        "beliefs": [_serialize_belief(b) for b in beliefs],
        "intents": [_serialize_intent(i) for i in intents],
        "active_story_threads": [_serialize_thread(t) for t in threads],
    }


@router.get("/chats/{chat_id}/debug/beliefs")
async def debug_beliefs(
    chat_id: int,
    character_id: int | None = None,
    db: AsyncSession = Depends(get_async_db),
):
    """Beliefs of a character (type, confidence, world_truth_ref)."""
    _require_debug_enabled()
    await _get_chat_or_404(db, chat_id)
    if character_id is not None:
        beliefs = await crud.get_beliefs_for_character(db, character_id, top_k=100)
    else:
        beliefs = await crud.get_beliefs_for_chat(db, chat_id)
    return {"chat_id": chat_id, "beliefs": [_serialize_belief(b) for b in beliefs]}


@router.get("/chats/{chat_id}/debug/threads")
async def debug_threads(
    chat_id: int,
    status: str | None = Query(default=None, pattern="^(active|archived)$"),
    db: AsyncSession = Depends(get_async_db),
):
    """Active/archived story_threads."""
    _require_debug_enabled()
    await _get_chat_or_404(db, chat_id)
    if status:
        threads = await crud.get_story_threads_by_status(db, chat_id, status)
    else:
        threads = await crud.get_story_threads_for_chat(db, chat_id)
    return {"chat_id": chat_id, "story_threads": [_serialize_thread(t) for t in threads]}


@router.get("/chats/{chat_id}/debug/events")
async def debug_events(
    chat_id: int,
    limit: int = Query(default=50, ge=1, le=500),
    db: AsyncSession = Depends(get_async_db),
):
    """Event graph: world_events + event_links (causality)."""
    _require_debug_enabled()
    await _get_chat_or_404(db, chat_id)
    events = await crud.get_world_events_for_chat(db, chat_id, limit=limit)
    event_ids = [e.id for e in events]
    links = await crud.get_event_links_for_events(db, chat_id, event_ids)
    return {
        "chat_id": chat_id,
        "world_events": [_serialize_event(e) for e in events],
        "event_links": [
            {"event_id": event_id, "caused_by_event_id": caused_by}
            for event_id, caused_by in links
        ],
    }


@router.get("/chats/{chat_id}/debug/anchors")
async def debug_anchors(
    chat_id: int,
    relationship_id: int | None = None,
    db: AsyncSession = Depends(get_async_db),
):
    """memory_anchors of a relationship (or all in chat)."""
    _require_debug_enabled()
    await _get_chat_or_404(db, chat_id)
    if relationship_id is not None:
        grouped = await crud.get_anchors_for_relationships(db, [relationship_id], limit=100)
        anchors = grouped.get(relationship_id, [])
    else:
        rels = await _list_relationship_ids(db, chat_id)
        grouped = await crud.get_anchors_for_relationships(db, rels, limit=100)
        anchors = [
            a for rid in rels for a in grouped.get(rid, [])
        ]
    return {
        "chat_id": chat_id,
        "memory_anchors": [_serialize_anchor(a) for a in anchors],
    }


async def _list_relationship_ids(db: AsyncSession, chat_id: int) -> list[int]:
    from ..relationship_service import list_relationships_for_chat

    rels = await list_relationships_for_chat(db, chat_id)
    return [r.id for r in rels]


@router.get("/chats/{chat_id}/debug/pipeline")
async def debug_pipeline(
    chat_id: int, db: AsyncSession = Depends(get_async_db)
):
    """Last post-round pipeline report (in-memory; empty until a round runs)."""
    _require_debug_enabled()
    await _get_chat_or_404(db, chat_id)
    report = pipeline_reports.get(chat_id)
    if report is None:
        return {"chat_id": chat_id, "last_report": None}
    return {"chat_id": chat_id, "last_report": report}


# In-memory store of the last pipeline report per chat (Sprint 13, §29.1).
# Written by `chat_engine` after `run_post_round_pipeline`; never persisted.
pipeline_reports: dict[int, dict] = {}


def remember_pipeline_report(chat_id: int, report: dict) -> None:
    """Keep the last post-round pipeline report for the debug endpoint."""
    if settings.debug_enabled and report:
        pipeline_reports[chat_id] = report
