"""Per-chat dynamic ``num_ctx`` tracking for fast response startup.

The KV window (``num_ctx``) starts at ``MIN_CTX`` and only ever grows, and
only when the actual assembled prompt outgrows it (``prompt_tokens + buffer
> current_ctx``). This keeps early responses fast (small cache) while long
conversations still get the context they need, capped by ``MAX_CTX``.
"""

from __future__ import annotations

import threading

from .config import settings


class ContextState:
    """In-memory per-chat registry of the current context window size."""

    def __init__(self) -> None:
        self._ctx: dict[int, int] = {}
        self._lock = threading.Lock()

    def get(self, chat_id: int) -> int:
        with self._lock:
            return self._ctx.get(chat_id, settings.min_ctx_tokens)

    def reset(self, chat_id: int) -> int:
        """Reset a chat to the starting window (MIN_CTX)."""
        with self._lock:
            self._ctx[chat_id] = settings.min_ctx_tokens
            return settings.min_ctx_tokens

    def remove(self, chat_id: int) -> None:
        """Forget a chat entirely (e.g. on deletion)."""
        with self._lock:
            self._ctx.pop(chat_id, None)

    def apply_prompt(self, chat_id: int, prompt_tokens: int) -> int:
        """Return the num_ctx to use for this request, growing it if needed.

        The window changes only when the prompt strictly exceeds the current
        window (``prompt_tokens > current_ctx``); it then becomes
        ``prompt_tokens + CTX_BUFFER_TOKENS`` and never shrinks. Growth is
        clamped to ``MAX_CTX``.
        """
        buffer = max(0, settings.ctx_buffer_tokens)
        needed = min(int(prompt_tokens) + buffer, settings.max_ctx_tokens)
        with self._lock:
            current = self._ctx.get(chat_id, settings.min_ctx_tokens)
            if int(prompt_tokens) > current:
                current = needed
                self._ctx[chat_id] = current
            return current


ctx_state = ContextState()
