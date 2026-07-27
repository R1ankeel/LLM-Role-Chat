"""Endpoints for managing characters and their memories."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

import crud
import schemas
from database import get_db

router = APIRouter(tags=["characters"])


@router.post(
    "/chats/{chat_id}/characters",
    response_model=schemas.CharacterRead,
    status_code=status.HTTP_201_CREATED,
)
def add_character(
    chat_id: int, character: schemas.CharacterCreate, db: Session = Depends(get_db)
):
    """Add a character to a chat (order_index is unique per chat)."""
    if crud.get_chat(db, chat_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Чат не найден"
        )
    try:
        return crud.create_character(db, chat_id, character)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


@router.get("/chats/{chat_id}/characters", response_model=list[schemas.CharacterRead])
def list_characters(chat_id: int, db: Session = Depends(get_db)):
    """List characters for a chat, sorted by order_index."""
    if crud.get_chat(db, chat_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Чат не найден"
        )
    return crud.get_characters_by_chat(db, chat_id)


@router.put("/characters/{character_id}", response_model=schemas.CharacterRead)
def update_character(
    character_id: int,
    character_update: schemas.CharacterUpdate,
    db: Session = Depends(get_db),
):
    """Update a character's card (name, personality, traits, order_index)."""
    try:
        updated = crud.update_character(db, character_id, character_update)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Персонаж не найден"
        )
    return updated


@router.delete("/characters/{character_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_character(character_id: int, db: Session = Depends(get_db)):
    """Delete a character (messages remain, memories cascade)."""
    if not crud.delete_character(db, character_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Персонаж не найден"
        )


@router.get(
    "/characters/{character_id}/memories",
    response_model=list[schemas.MemoryRead],
)
def list_character_memories(character_id: int, db: Session = Depends(get_db)):
    """List all memories for a character (chronological order)."""
    character = crud.get_character(db, character_id)
    if character is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Персонаж не найден"
        )
    return crud.get_memories_by_character(db, character_id)


@router.delete("/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_memory(memory_id: int, db: Session = Depends(get_db)):
    """Delete a specific memory entry."""
    if not crud.delete_memory(db, memory_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Память не найдена"
        )