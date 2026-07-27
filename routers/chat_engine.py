"""Endpoints for chat: send message and browse history."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

import chat_engine
import crud
import schemas
from database import get_db
from ratelimit import check_rate_limit, update_rate_limit

router = APIRouter(tags=["chat"])


@router.post(
    "/chats/{chat_id}/message",
    response_model=list[schemas.MessageRead],
    status_code=status.HTTP_200_OK,
)
def send_message(
    chat_id: int,
    message: schemas.UserMessage,
    db: Session = Depends(get_db),
):
    """Send a player message and generate replies from all characters.

    Returns the new messages array:
      [player_msg, char1_reply, char2_reply, ...]
    """
    if not message.content.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Сообщение не может быть пустым",
        )

    chat = crud.get_chat(db, chat_id)
    if chat is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Чат не найден",
        )

    # Rate limiting
    check_rate_limit(chat_id)

    new_messages = None
    try:
        new_messages = chat_engine.process_user_message(
            db, chat_id, message.content
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except RuntimeError as exc:
        # User message was saved, but Ollama failed -> still update rate limit
        update_rate_limit(chat_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    # Update rate limit on success
    update_rate_limit(chat_id)
    return new_messages


@router.get(
    "/chats/{chat_id}/messages",
    response_model=list[schemas.MessageRead],
)
def get_messages(
    chat_id: int,
    limit: int = Query(50, ge=1, le=500, description="Number of messages"),
    offset: int = Query(0, ge=0, description="Offset from the beginning"),
    db: Session = Depends(get_db),
):
    """Chat message history with pagination (chronological order)."""
    if crud.get_chat(db, chat_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Чат не найден",
        )
    return crud.get_messages_paginated(db, chat_id, limit=limit, offset=offset)
