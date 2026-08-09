"""Схемы сюжетного состояния (Sprint 3, decomposition-sprints.md §4)."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class StoryThreadRead(BaseModel):
    """Активная сюжетная линия (story_threads)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    chat_id: int
    name: str = ""
    actors: list = Field(default_factory=list)
    importance: int = 0
    status: str = "active"
    created_round_id: Optional[str] = None
    updated_at: Optional[datetime] = None


class StoryEventRead(BaseModel):
    """Проекция канонического world_event для сюжета (story_events)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: Optional[int] = None
    chat_id: int
    round_id: Optional[str] = None
    event: str = ""
    actors: list = Field(default_factory=list)
    location: str = ""
    cause: str = ""
    consequences: str = ""
    importance: int = 0
    story_thread_id: Optional[int] = None
    created_at: Optional[datetime] = None


class StoryStateRead(BaseModel):
    """Current Story State чата (story_states, §16.2)."""

    id: int
    chat_id: int
    original_plot: str = ""
    current_story: dict = Field(default_factory=dict)
    story_phase: str = ""
    updated_round_id: Optional[str] = None
    version: int = 1
    updated_at: Optional[datetime] = None


class StoryStateUpdate(BaseModel):
    """PATCH story state (только пользователь; original_plot — user-only).

    ``current_story`` — частичный JSON (key-value, мержится с существующим).
    ``story_enabled``/``story_prompt`` — поля чата (Sprint 0), управляются
    пользователем через этот endpoint; LLM их НЕ трогает.
    """

    original_plot: Optional[str] = None
    current_story: Optional[dict] = None
    story_phase: Optional[str] = None
    story_enabled: Optional[bool] = None
    story_prompt: Optional[str] = None


class StoryStateResponse(StoryStateRead):
    """GET story state: state + активные потоки + последние события."""

    active_threads: list[StoryThreadRead] = Field(default_factory=list)
    recent_events: list[StoryEventRead] = Field(default_factory=list)
    story_enabled: bool = False
    story_prompt: str = ""
