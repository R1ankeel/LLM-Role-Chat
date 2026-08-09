"""Схемы памяти (Sprint 3, decomposition-sprints.md §4).

Помимо классов здесь живут нормализаторы категорий/типов памяти
(``normalize_category``/``normalize_memory_type``) — публичные функции,
которые использовались через ``app.schemas``.
"""

import json
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..config import settings

MemoryCategory = Literal["отношения", "событие", "локация", "предмет", "другое"]
# Sprint 2 (Plans/update20.md §7): типы памяти на единой таблице memories.
MemoryType = Literal["semantic", "episodic", "social", "story"]


_CATEGORY_ALIASES = {
    "rel": "отношения",
    "relations": "отношения",
    "person": "отношения",
    "people": "отношения",
    "place": "локация",
    "loc": "локация",
    "object": "предмет",
    "thing": "предмет",
    "action": "событие",
    "plot": "событие",
    "relationship": "отношения",
    "event": "событие",
    "location": "локация",
    "item": "предмет",
    "other": "другое",
}


def normalize_category(value: object) -> Optional[str]:
    """Normalize a memory category token (English or Russian) to the Russian form."""
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip().lower()
    if text in settings.memory_categories:
        return text
    return _CATEGORY_ALIASES.get(text, "другое")


_MEMORY_TYPES = frozenset({"semantic", "episodic", "social", "story"})


def normalize_memory_type(value: object) -> Optional[str]:
    """Normalize a memory type token; None для пустого/неизвестного значения.

    Пустое значение означает «не задано» — движок применит детерминированный
    fallback-классификатор (§7). Неизвестное значение также → None (не валидно).
    """
    if value is None:
        return None
    text = str(value).strip().lower()
    if text == "" or text == "none":
        return None
    if text in _MEMORY_TYPES:
        return text
    return None


class MemoryBase(BaseModel):
    content: str
    importance: Optional[float] = 0.5
    category: Optional[str] = None
    # Sprint 2 (Plans/update20.md §7): тип памяти (semantic/episodic/social/story),
    # эмоциональная окраска (valence [-1..1], intensity [0..1]) и проекция на
    # каноническое `world_events`. Пустое memory_type → движок применит
    # fallback-классификатор; в БД уходит валидный тип (по умолчанию 'semantic').
    memory_type: Optional[str] = None
    valence: Optional[float] = Field(default=None, ge=-1.0, le=1.0)
    intensity: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    event_id: Optional[int] = None

    @field_validator("category", mode="before")
    @classmethod
    def _normalize_memory_category(cls, value: object) -> Optional[str]:
        return normalize_category(value)

    @field_validator("memory_type", mode="before")
    @classmethod
    def _normalize_memory_type(cls, value: object) -> Optional[str]:
        return normalize_memory_type(value)


class MemoryCreate(MemoryBase):
    chat_id: int
    character_id: int


class MemoryUpdate(BaseModel):
    content: Optional[str] = None
    importance: Optional[float] = None
    category: Optional[str] = None
    memory_type: Optional[str] = None
    valence: Optional[float] = Field(default=None, ge=-1.0, le=1.0)
    intensity: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    event_id: Optional[int] = None


class MemoryRead(MemoryBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    chat_id: int
    character_id: int
    created_at: datetime
    last_accessed_at: Optional[datetime] = None
    source_message_ids: list[int] = Field(default_factory=list)

    @field_validator("source_message_ids", mode="before")
    @classmethod
    def _parse_source_ids(cls, value: object) -> list[int]:
        if isinstance(value, str):
            import json
            try:
                return json.loads(value)
            except Exception:
                return []
        if isinstance(value, list):
            return value
        return []


class ExtractedFact(BaseModel):
    """Structured fact from LLM memory extraction (P1).

    Sprint 2 (Plans/update20.md §7): ``memory_type`` — semantic | episodic |
    social | story. LLM может вернуть тип; если он пуст/не валиден — движок
    применит детерминированный fallback-классификатор по категории/тексту
    (``memory_service.classify_memory_type``). ``valence``/``intensity`` —
    эмоциональная окраска (опционально).
    """

    fact: str
    category: MemoryCategory = "событие"
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    witnessed: bool = True
    memory_type: Optional[str] = None
    valence: Optional[float] = Field(default=None, ge=-1.0, le=1.0)
    intensity: Optional[float] = Field(default=None, ge=0.0, le=1.0)

    @field_validator("fact", mode="before")
    @classmethod
    def _strip_fact(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @field_validator("category", mode="before")
    @classmethod
    def _normalize_category(cls, value: object) -> str:
        normalized = normalize_category(value)
        return normalized or "событие"

    @field_validator("memory_type", mode="before")
    @classmethod
    def _normalize_fact_memory_type(cls, value: object) -> Optional[str]:
        return normalize_memory_type(value)

    @field_validator("importance", mode="before")
    @classmethod
    def _normalize_importance(cls, value: object) -> float:
        if value is None or value == "":
            return 0.5
        try:
            num = float(value)
        except (TypeError, ValueError):
            return 0.5
        # Accept 1–5 scale from some models
        if num > 1.0 and num <= 5.0:
            num = num / 5.0
        if num < 0.0:
            return 0.0
        if num > 1.0:
            return 1.0
        return num

    @field_validator("witnessed", mode="before")
    @classmethod
    def _normalize_witnessed(cls, value: object) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return True
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        if text in {"false", "0", "no", "нет", "n"}:
            return False
        if text in {"true", "1", "yes", "да", "y"}:
            return True
        return True
