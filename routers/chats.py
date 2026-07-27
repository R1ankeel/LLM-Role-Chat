"""Endpoints for managing chats."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

import crud
import schemas
from database import get_db

router = APIRouter(prefix="/chats", tags=["chats"])


@router.post("", response_model=schemas.ChatRead, status_code=status.HTTP_201_CREATED)
def create_chat(chat: schemas.ChatCreate, db: Session = Depends(get_db)):
    """Create a new chat."""
    return crud.create_chat(db, chat)


@router.get("", response_model=list[schemas.ChatRead])
def list_chats(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """List all chats (newest first)."""
    return crud.get_chats(db, skip=skip, limit=limit)


@router.get("/{chat_id}", response_model=schemas.ChatDetail)
def get_chat_detail(chat_id: int, db: Session = Depends(get_db)):
    """Chat detail: card + characters + last 50 messages."""
    chat = crud.get_chat(db, chat_id)
    if chat is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Чат не найден"
        )
    characters = crud.get_characters_by_chat(db, chat_id)
    messages = crud.get_messages_by_chat(db, chat_id, limit=50)
    return schemas.ChatDetail(
        **schemas.ChatRead.model_validate(chat).model_dump(),
        characters=[schemas.CharacterRead.model_validate(c) for c in characters],
        messages=[schemas.MessageRead.model_validate(m) for m in messages],
    )


@router.put("/{chat_id}", response_model=schemas.ChatRead)
def update_chat(
    chat_id: int, chat_update: schemas.ChatUpdate, db: Session = Depends(get_db)
):
    """Update chat name / prompt / model / history length."""
    updated = crud.update_chat(db, chat_id, chat_update)
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Чат не найден"
        )
    return updated


@router.delete("/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_chat(chat_id: int, db: Session = Depends(get_db)):
    """Delete chat with all characters, messages and memories."""
    if not crud.delete_chat(db, chat_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Чат не найден"
        )


@router.delete("/{chat_id}/messages", status_code=status.HTTP_204_NO_CONTENT)
def clear_messages(chat_id: int, db: Session = Depends(get_db)):
    """Delete all messages in a chat (keeps chat and characters)."""
    if not crud.clear_chat_messages(db, chat_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Чат не найден"
        )