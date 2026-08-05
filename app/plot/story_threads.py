"""Story threads — архивация завершённых линий (Plans/update20.md §22-смежное, Sprint 10).

Write-path линий (создание/рост importance из story_events) — Sprint 8
(``story_state._sync_threads_from_events``), LLM-архивация по завершённым
целям — Sprint 9 (``story_consolidation``). Здесь — детерминированная
**архивация завершённых линий**: активный ``story_thread``, чьё имя
пересекается (token overlap) с ``completed_goals`` текущего story_state,
переводится в ``status=archived``.

Также — общие детерминированные помощники (token overlap), используемые
intent-слоем и npc_plans.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from .. import crud
from ..config import settings

logger = logging.getLogger(__name__)

# Стоп-слова для «значимых токенов» (не раздувают overlap общими словами).
_STOPWORDS: frozenset[str] = frozenset(
    {
        "и", "в", "во", "не", "что", "он", "на", "я", "с", "со", "как", "а",
        "то", "все", "она", "так", "его", "но", "да", "ты", "к", "у", "же",
        "вы", "за", "бы", "по", "только", "ее", "мне", "было", "вот", "от",
        "меня", "еще", "нет", "о", "из", "ему", "теперь", "когда", "даже",
        "ну", "вдруг", "ли", "если", "уже", "или", "ни", "быть", "был", "него",
        "до", "вас", "нибудь", "опять", "уж", "вам", "ведь", "там", "потом",
        "себя", "ничего", "ей", "может", "они", "тут", "где", "есть", "надо",
        "ней", "для", "мы", "тебя", "их", "чем", "была", "сам", "чтоб", "без",
        "будто", "чего", "раз", "тоже", "себе", "под", "будет", "ж", "тогда",
        "кто", "этот", "того", "потому", "этого", "какой", "совсем", "ним",
        "здесь", "этом", "один", "почти", "мой", "тем", "чтобы", "нее", "сейчас",
        "были", "куда", "зачем", "всех", "никогда", "можно", "при", "наконец",
        "два", "об", "другой", "хоть", "после", "над", "больше", "тот", "через",
        "эти", "нас", "про", "всего", "них", "какая", "много", "разве", "три",
        "эту", "моя", "впрочем", "хорошо", "свою", "этой", "перед", "иногда",
        "лучше", "чуть", "том", "нельзя", "такой", "им", "более", "всегда",
        "конечно", "всю", "между",
    }
)

_WORD_RE = re.compile(r"[а-яёa-z0-9-]{2,}", re.IGNORECASE)


def significant_tokens(text: str) -> set[str]:
    """Набор «значимых» токенов текста (без стоп-слов), lowercase."""
    if not text:
        return set()
    tokens = {m.group(0).lower() for m in _WORD_RE.finditer(text)}
    return tokens - _STOPWORDS


def token_overlap(a: str, b: str) -> float:
    """Доля значимых токенов A, присутствующих в B (0..1).

    ``overlap = |A_tokens ∩ B_tokens| / |A_tokens|`` (0 при пустом A) —
    детерминированная мера соответствия имени линии завершённой цели.
    """
    tokens_a = significant_tokens(a)
    if not tokens_a:
        return 0.0
    tokens_b = significant_tokens(b)
    if not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a)


def _parse_json(value: Any) -> Any:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


async def archive_completed_threads(db: Any, chat_id: int) -> dict:
    """Архивировать активные story_threads, совпавшие с ``completed_goals``.

    Детерминированно: читает ``story_state.current_story.completed_goals``
    (пишется пользователем/консолидацией Sprint 9) и переводит в
    ``status=archived`` те активные линии, чьё имя пересекается с целью
    (``token_overlap >= story_thread_archive_overlap``). No-op при выключенном
    ``story_enabled``; падение не роняет раунд.
    """
    if not settings.story_enabled:
        return {"ok": True, "stage": "story_threads", "skipped": "flag off"}
    try:
        state = await crud.get_story_state(db, chat_id)
        if state is None:
            return {"ok": True, "stage": "story_threads", "skipped": "no state"}
        current = _parse_json(getattr(state, "current_story", "{}"))
        completed = current.get("completed_goals") if isinstance(current, dict) else None
        if not completed:
            return {"ok": True, "stage": "story_threads", "archived": 0}
        threshold = float(settings.story_thread_archive_overlap or 0.5)
        threads = await crud.get_active_story_threads(db, chat_id)
        archived = 0
        for thread in threads:
            name = (getattr(thread, "name", "") or "").strip()
            if not name:
                continue
            if any(
                token_overlap(name, str(goal)) >= threshold for goal in completed
            ):
                await crud.update_story_thread(db, thread.id, status="archived")
                archived += 1
        return {"ok": True, "stage": "story_threads", "archived": archived}
    except Exception as exc:  # noqa: BLE001 — не роняет раунд
        logger.warning("Story threads archive stage failed: %s", exc)
        return {"ok": False, "stage": "story_threads", "error": str(exc)}
