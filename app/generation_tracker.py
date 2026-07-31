"""In-flight generation tracker.

Tracks one active generation task per chat so that concurrent messages to the
same chat are rejected and an active generation can be cancelled via the
``/stop-generation`` endpoint.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

_tasks: dict[int, asyncio.Task] = {}


def is_gen_active(chat_id: int) -> bool:
    """Return True if a generation task for this chat is still running."""
    task = _tasks.get(chat_id)
    if task is None:
        return False
    if task.done():
        _tasks.pop(chat_id, None)
        return False
    return True


def _on_task_done(chat_id: int, task: asyncio.Task) -> None:
    if _tasks.get(chat_id) is task:
        _tasks.pop(chat_id, None)
    if not task.cancelled() and task.exception() is not None:
        logger.error(
            "[chat_id=%d] Generation task failed: %s",
            chat_id,
            task.exception(),
        )


async def start_generation(chat_id: int, task: asyncio.Task) -> None:
    """Register a running generation task for the chat."""
    previous = _tasks.get(chat_id)
    if previous is not None and not previous.done():
        logger.warning(
            "[chat_id=%d] Overwriting an active generation task", chat_id
        )
    _tasks[chat_id] = task
    task.add_done_callback(
        lambda done: _on_task_done(chat_id, done)
    )


async def stop_generation(chat_id: int) -> bool:
    """Cancel the active generation task for the chat.

    Returns True if there was an active task that was stopped.
    """
    task = _tasks.pop(chat_id, None)
    if task is None or task.done():
        return False
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception as exc:  # pragma: no cover - defensive
        logger.error(
            "[chat_id=%d] Unexpected error while stopping generation: %s",
            chat_id,
            exc,
        )
    return True
