"""Схема runtime-состояния персонажа (Sprint 3, decomposition-sprints.md §4)."""

import json
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CharacterStateRead(BaseModel):
    """Runtime-состояние персонажа (Plans/update20.md §8, Sprint 3).

    Хранит ТОЛЬКО то, чего нет в других таблицах: эмоции/стресс/физическое
    состояние/внимание/цели. Локация и отношения в state НЕ дублируются.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    chat_id: int
    character_id: int
    emotional_state: dict[str, float] = Field(default_factory=dict)
    mood: str = ""
    stress: Optional[float] = None
    physical_state: dict = Field(default_factory=dict)
    attention: Optional[str] = None
    current_focus_id: Optional[int] = None
    active_goal: str = ""
    personal_goals: list = Field(default_factory=list)
    updated_round_id: Optional[str] = None

    @field_validator("emotional_state", mode="before")
    @classmethod
    def _parse_emotional_state(cls, value: object) -> dict[str, float]:
        from ..emotion_engine import normalize_emotional_state

        if isinstance(value, dict):
            return normalize_emotional_state(value)
        if isinstance(value, str):
            try:
                return normalize_emotional_state(json.loads(value))
            except (json.JSONDecodeError, TypeError, ValueError):
                return {}
        return {}

    @field_validator("physical_state", "personal_goals", mode="before")
    @classmethod
    def _parse_json(cls, value: object) -> Any:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError, ValueError):
                return value
        return value
