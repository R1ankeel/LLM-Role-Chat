"""Token counting abstraction for the token-aware context builder.

Two modes:
- ``estimated`` (default): fast character-based approximation (no dependencies).
- ``exact``: optional ``tiktoken``-based counting when a tokenizer is configured
  via ``TOKENIZER_ENCODING``.

The counter is cached module-wide (loaded once per process) and the effective
mode is always reported truthfully via ``TokenCounter.mode``.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Protocol

from .config import settings

logger = logging.getLogger(__name__)

# Rough overhead in tokens per chat message (role framing, separators).
MESSAGE_OVERHEAD_TOKENS = 4


class TokenCounter(Protocol):
    mode: str

    def count(self, text: str) -> int:
        """Count tokens in a single text blob."""
        ...

    def count_messages(self, messages: list[dict[str, str]]) -> int:
        """Count tokens across chat messages including per-message overhead."""
        ...


class EstimatedTokenCounter:
    """Fast approximation: ~4 chars/token plus small overhead per line/space."""

    mode = "estimated"

    def count(self, text: str) -> int:
        if not text:
            return 0
        length = len(text)
        # CJK characters are denser (~1 token per character on average).
        cjk = len(re.findall(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", text))
        base = math.ceil((length - cjk) / 4) + cjk
        # Overhead for whitespace/newlines and markup.
        base += text.count("\n") // 2 + text.count(" ") // 4
        return max(1, base)

    def count_messages(self, messages: list[dict[str, str]]) -> int:
        if not messages:
            return 0
        return sum(
            self.count(str(msg.get("content") or "")) + MESSAGE_OVERHEAD_TOKENS
            for msg in messages
        )


class ExactTokenCounter:
    """tiktoken-based counting; transparently falls back to estimated mode."""

    def __init__(self, encoding_name: str):
        self._encoding_name = encoding_name
        self._encoding = None
        self._fallback = EstimatedTokenCounter()
        try:
            import tiktoken

            self._encoding = tiktoken.get_encoding(encoding_name)
        except Exception as exc:  # pragma: no cover - depends on environment
            logger.warning(
                "Exact tokenizer unavailable (encoding=%r): %s; "
                "falling back to estimated mode",
                encoding_name,
                exc,
            )

    @property
    def mode(self) -> str:
        return "exact" if self._encoding is not None else "estimated"

    def count(self, text: str) -> int:
        if self._encoding is None:
            return self._fallback.count(text)
        try:
            return len(self._encoding.encode(text))
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("tiktoken encoding failed (%s); using estimate", exc)
            return self._fallback.count(text)

    def count_messages(self, messages: list[dict[str, str]]) -> int:
        if not messages:
            return 0
        if self._encoding is None:
            return self._fallback.count_messages(messages)
        total = 0
        try:
            for msg in messages:
                total += len(self._encoding.encode(str(msg.get("content") or "")))
                total += MESSAGE_OVERHEAD_TOKENS
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("tiktoken messages encoding failed (%s); using estimate", exc)
            return self._fallback.count_messages(messages)
        return total


_counter: TokenCounter | None = None


def get_token_counter() -> TokenCounter:
    """Return the process-wide cached token counter."""
    global _counter
    if _counter is None:
        if settings.token_count_mode == "exact" and settings.tokenizer_encoding:
            _counter = ExactTokenCounter(settings.tokenizer_encoding)
        else:
            _counter = EstimatedTokenCounter()
        logger.info("Token counter initialized: mode=%s", _counter.mode)
    return _counter
