"""Rate limiter for chat messages (simple per-chat-id throttling)."""

import time

_last_message_time: dict[int, float] = {}
RATE_LIMIT_SECONDS = 5


def check_rate_limit(chat_id: int) -> None:
    """Проверяет, можно ли отправить сообщение в чат. Бросает 429 если слишком часто."""
    from fastapi import HTTPException, status

    now = time.time()
    last = _last_message_time.get(chat_id, 0)
    if now - last < RATE_LIMIT_SECONDS:
        remaining = int(RATE_LIMIT_SECONDS - (now - last))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Слишком часто! Подождите {remaining} сек.",
        )


def update_rate_limit(chat_id: int) -> None:
    """Обновить время последнего сообщения для чата."""
    _last_message_time[chat_id] = time.time()