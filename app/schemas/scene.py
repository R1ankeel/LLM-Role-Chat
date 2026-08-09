"""Схемы сцены и структурированных мировых событий (Sprint 3, decomposition-sprints.md §4).

Event-схемы (``ExtractedEvent``/``EventExtraction*``/``EventAction``) — домен
сцены/мира, отдельного ``events.py`` в плане §4.6 нет.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class SceneCustomState(BaseModel):
    """Structured custom state for scene tracking (weather is global)."""
    model_config = ConfigDict(extra="allow")

    weather: str = ""
    mood: str = ""
    tension: float = Field(default=0.0, ge=0.0, le=1.0)
    plot_flags: list[str] = Field(default_factory=list)
    active_goal: str = ""
    important_objects: list[str] = Field(default_factory=list)
    active_events: list[str] = Field(default_factory=list)
    time_progression: str = ""
    stagnation_rounds: int = 0
    round_count: int = 0
    active_goals: dict[str, str] = Field(default_factory=dict)


class SceneStateBase(BaseModel):
    time_of_day: str = ""
    character_locations: dict[str, str] = Field(default_factory=dict)
    custom_state: SceneCustomState = Field(default_factory=SceneCustomState)


class SceneStateRead(SceneStateBase):
    model_config = ConfigDict(from_attributes=True)

    chat_id: int
    updated_at: datetime
    present_character_ids: list[int] = Field(default_factory=list)
    player_location: str = ""


class SceneStateUpdate(BaseModel):
    time_of_day: Optional[str] = None
    character_locations: Optional[dict[str, str]] = None
    custom_state: Optional[SceneCustomState] = None


class EventAction(BaseModel):
    """Структурированное действие события (§15). actor — имя персонажа."""

    actor: str = ""
    action: str = ""
    target: str = ""
    object: str = ""


class ExtractedEvent(BaseModel):
    """Одно извлечённое раундной event extraction событие.

    event_type повторяет вокабуляр `world_events` (speech|move|system_narrator),
    но допускает и свободные типы (fight, gift, promise, ...) — записываются
    как есть, read-path их ещё не фильтрует. importance 0..10,
    story_salience / emotional_salience 0..1.
    """

    event_type: str = "event"
    description: str = ""
    source_character: str = ""
    targets: list[str] = Field(default_factory=list)
    location: str = ""
    action: EventAction = Field(default_factory=EventAction)
    importance: float = 5.0
    story_salience: float = 0.5
    emotional_salience: float = 0.5
    # Индексы в массиве `events` ответа LLM, на которые ссылается это событие
    # как на причину (caused_by). Отсюда строятся event_links.
    causes: list[int] = Field(default_factory=list)


class EventExtractionResult(BaseModel):
    """Результат event extraction для одного раунда (до записи в БД)."""

    events: list[ExtractedEvent] = Field(default_factory=list)
    sensors_used: bool = False


class EventExtractionReport(BaseModel):
    """Отчёт о записанном в `world_events` / `event_links` событийном слое."""

    written_events: int = 0
    written_links: int = 0
    skipped_below_importance: int = 0
    extraction_used: bool = False
    sensors_used: bool = False
    error: str = ""
