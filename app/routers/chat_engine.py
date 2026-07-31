"""Endpoints for chat: send message and browse history."""

import asyncio
import json

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from .. import chat_engine
from .. import crud
from .. import generation_tracker
from .. import models
from .. import schemas
from ..database import AsyncSessionLocal, get_async_db
from ..ratelimit import check_rate_limit, update_rate_limit

router = APIRouter(tags=["chat"])


def _sse_event(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _run_generation(
    queue: asyncio.Queue,
    client: httpx.AsyncClient,
    chat_id: int,
    content: str,
    *,
    visibility: str | None = None,
    target_character_ids: list[int] | None = None,
) -> None:
    """Run message generation in a detached task with its own DB session."""
    async with AsyncSessionLocal() as db:
        try:
            async for event in chat_engine.process_user_message_streaming(
                client,
                db,
                chat_id,
                content,
                visibility=visibility,
                target_character_ids=target_character_ids,
            ):
                await queue.put(event)
            await queue.put({"type": "done"})
        except ValueError as exc:
            await queue.put({"type": "error", "detail": str(exc)})
        except RuntimeError as exc:
            await queue.put({"type": "error", "detail": str(exc), "rate_limit": True})


@router.post("/chats/{chat_id}/message", status_code=status.HTTP_200_OK)
async def send_message(
    chat_id: int,
    message: schemas.UserMessage,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
):
    """Send a player message and stream character replies via SSE.

    Events:
      {"type": "message", "message": MessageRead}
      {"type": "token", "text": "...", "character_id": 1}
      {"type": "done"}
      {"type": "error", "detail": "..."}
    """
    if not message.content.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Сообщение не может быть пустым",
        )

    chat = await crud.get_chat(db, chat_id)
    if chat is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Чат не найден",
        )

    check_rate_limit(chat_id)

    if generation_tracker.is_gen_active(chat_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="В этом чате уже выполняется генерация",
        )

    client = request.app.state.ollama_client

    async def event_stream():
        queue: asyncio.Queue = asyncio.Queue()
        task = asyncio.create_task(
            _run_generation(
                queue,
                client,
                chat_id,
                message.content,
                visibility=message.visibility,
                target_character_ids=message.target_character_ids,
            )
        )
        await generation_tracker.start_generation(chat_id, task)

        try:
            while True:
                event = await queue.get()
                if event["type"] == "done":
                    update_rate_limit(chat_id)
                    yield _sse_event({"type": "done"})
                    break
                if event["type"] == "error":
                    if event.get("rate_limit"):
                        update_rate_limit(chat_id)
                    yield _sse_event({"type": "error", "detail": event["detail"]})
                    break
                yield _sse_event(event)
        except asyncio.CancelledError:
            update_rate_limit(chat_id)
            pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/chats/{chat_id}/stop-generation", status_code=status.HTTP_200_OK)
async def stop_generation_endpoint(chat_id: int):
    """Остановить активную генерацию в чате."""
    stopped = await generation_tracker.stop_generation(chat_id)
    if stopped:
        update_rate_limit(chat_id)
        return {"status": "cancelled"}
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Нет активной генерации",
    )


@router.get("/chats/{chat_id}/generation-status", status_code=status.HTTP_200_OK)
async def generation_status_endpoint(chat_id: int):
    """Проверить, выполняется ли генерация в чате."""
    return {"active": generation_tracker.is_gen_active(chat_id)}


@router.get(
    "/chats/{chat_id}/messages",
    response_model=list[schemas.MessageRead],
)
async def get_messages(
    chat_id: int,
    limit: int = Query(50, ge=1, le=500, description="Number of messages"),
    offset: int = Query(0, ge=0, description="Offset from the beginning"),
    db: AsyncSession = Depends(get_async_db),
):
    """Chat message history with pagination (chronological order)."""
    if await crud.get_chat(db, chat_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Чат не найден",
        )
    return await crud.get_messages_paginated(db, chat_id, limit=limit, offset=offset)


@router.delete(
    "/chats/{chat_id}/messages/{message_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_message_endpoint(
    chat_id: int,
    message_id: int,
    db: AsyncSession = Depends(get_async_db),
):
    """Delete a message from the chat (UI + DB).

    Deleting a player message also deletes every message after it in the chat
    (the round's replies). Deleting a character reply removes only that reply.
    """
    if await crud.get_chat(db, chat_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Чат не найден",
        )
    if generation_tracker.is_gen_active(chat_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="В этом чате уже выполняется генерация",
        )
    message = await db.get(models.Message, message_id)
    if message is None or message.chat_id != chat_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Сообщение не найдено",
        )
    cascade_after = message.role == "user"
    if not await crud.delete_message(db, message_id, cascade_after=cascade_after):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Сообщение не найдено",
        )


async def _run_regeneration(
    queue: asyncio.Queue,
    client: httpx.AsyncClient,
    chat_id: int,
    message_id: int,
) -> None:
    """Run message regeneration in a detached task with its own DB session."""
    async with AsyncSessionLocal() as db:
        try:
            async for event in chat_engine.regenerate_message_streaming(
                client,
                db,
                chat_id,
                message_id,
            ):
                await queue.put(event)
            await queue.put({"type": "done"})
        except ValueError as exc:
            await queue.put({"type": "error", "detail": str(exc)})
        except RuntimeError as exc:
            await queue.put({"type": "error", "detail": str(exc)})


@router.post(
    "/chats/{chat_id}/messages/{message_id}/regenerate",
    status_code=status.HTTP_200_OK,
)
async def regenerate_message_endpoint(
    chat_id: int,
    message_id: int,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
):
    """Regenerate a character reply and stream the new text via SSE.

    Events:
      {"type": "token", "text": "...", "character_id": 1}
      {"type": "message", "message": MessageRead}
      {"type": "done"}
      {"type": "error", "detail": "..."}
    """
    if await crud.get_chat(db, chat_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Чат не найден",
        )
    message = await db.get(models.Message, message_id)
    if message is None or message.chat_id != chat_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Сообщение не найдено",
        )
    if message.role != "character":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Перегенерировать можно только ответ персонажа",
        )

    check_rate_limit(chat_id)

    if generation_tracker.is_gen_active(chat_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="В этом чате уже выполняется генерация",
        )

    client = request.app.state.ollama_client

    async def event_stream():
        queue: asyncio.Queue = asyncio.Queue()
        task = asyncio.create_task(
            _run_regeneration(
                queue,
                client,
                chat_id,
                message_id,
            )
        )
        await generation_tracker.start_generation(chat_id, task)

        try:
            while True:
                event = await queue.get()
                if event["type"] == "done":
                    update_rate_limit(chat_id)
                    yield _sse_event({"type": "done"})
                    break
                if event["type"] == "error":
                    update_rate_limit(chat_id)
                    yield _sse_event({"type": "error", "detail": event["detail"]})
                    break
                yield _sse_event(event)
        except asyncio.CancelledError:
            update_rate_limit(chat_id)
            pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )