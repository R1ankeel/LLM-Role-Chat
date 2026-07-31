"""Pydantic-схемы для CRUD-операций (Pydantic v2)."""

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import settings
from .perception import parse_target_ids, serialize_target_ids

# Допустимые роли сообщений
Role = Literal["user", "character", "system"]
PresenceType = Literal["present", "mentioned", "absent", "told"]
MemoryCategory = Literal["relationship", "event", "location", "item", "other"]
EventVisibility = Literal["private", "local", "targeted", "public", "global"]
CommunicationChannel = Literal["direct", "magic", "phone", "radio", "messenger"]


def _normalize_visibility(value: object) -> str:
    if value is None or value == "":
        return settings.default_event_visibility
    text = str(value).strip().lower()
    if text in settings.event_visibilities:
        return text
    return settings.default_event_visibility


# ------------------------------ Chat ------------------------------
class ChatBase(BaseModel):
    name: str
    general_prompt: str = ""
    model_name: str = settings.default_model
    max_history_length: int = settings.default_history_length
    thinking_mode: bool = settings.enable_thinking
    player_location: str = ""
    locations: str = "[]"


class ChatCreate(ChatBase):
    pass


class ChatUpdate(BaseModel):
    name: Optional[str] = None
    general_prompt: Optional[str] = None
    model_name: Optional[str] = None
    max_history_length: Optional[int] = None
    thinking_mode: Optional[bool] = None
    player_location: Optional[str] = None
    locations: Optional[str] = None


class ChatRead(ChatBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime


# ---------------------------- Character ----------------------------
class CharacterBase(BaseModel):
    name: str
    personality: str = ""
    traits: str = ""
    speech_style: str = ""
    example_messages: str = ""
    boundaries: str = ""
    background: str = ""
    relationships: str = ""
    location: str = ""
    temperature: Optional[float] = None
    order_index: int = 0
    is_player: bool = False


class InitialRelationship(BaseModel):
    target_id: int
    relationship_type: str = "neutral"
    affection: int = 50
    trust: int = 50
    attraction: int = 0
    resentment: int = 0
    jealousy: int = 0
    description: str = ""


class CharacterCreate(CharacterBase):
    """chat_id берётся из пути эндпоинта POST /api/chats/{chat_id}/characters."""

    initial_relationships: list[InitialRelationship] = []


class CharacterUpdate(BaseModel):
    name: Optional[str] = None
    personality: Optional[str] = None
    traits: Optional[str] = None
    speech_style: Optional[str] = None
    example_messages: Optional[str] = None
    boundaries: Optional[str] = None
    background: Optional[str] = None
    relationships: Optional[str] = None
    location: Optional[str] = None
    temperature: Optional[float] = None
    order_index: Optional[int] = None
    is_player: Optional[bool] = None


class CharacterRead(CharacterBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    chat_id: int
    created_at: datetime


# ----------------------------- Message -----------------------------
class MessageBase(BaseModel):
    role: Role
    content: str


class MessageCreate(MessageBase):
    chat_id: int
    character_id: Optional[int] = None
    visibility: EventVisibility = settings.default_event_visibility  # type: ignore[assignment]
    location: str = ""
    target_character_ids: list[int] = Field(default_factory=list)
    channel: CommunicationChannel = "direct"

    @field_validator("visibility", mode="before")
    @classmethod
    def _vis(cls, value: object) -> str:
        return _normalize_visibility(value)

    @field_validator("target_character_ids", mode="before")
    @classmethod
    def _targets(cls, value: object) -> list[int]:
        return parse_target_ids(value)

    def orm_kwargs(self) -> dict[str, Any]:
        """Dump fields ready for SQLAlchemy Message constructor."""
        data = self.model_dump()
        data["target_character_ids"] = serialize_target_ids(
            data.get("target_character_ids") or []
        )
        data["visibility"] = _normalize_visibility(data.get("visibility"))
        data["location"] = data.get("location") or ""
        data["channel"] = data.get("channel") or "direct"
        return data


class MessageRead(MessageBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    chat_id: int
    character_id: Optional[int] = None
    visibility: str = settings.default_event_visibility
    location: str = ""
    target_character_ids: list[int] = Field(default_factory=list)
    channel: str = "direct"
    timestamp: datetime

    @model_validator(mode="before")
    @classmethod
    def _coerce_from_orm(cls, data: Any) -> Any:
        if isinstance(data, dict):
            payload = dict(data)
        else:
            payload = {
                "id": getattr(data, "id", None),
                "chat_id": getattr(data, "chat_id", None),
                "character_id": getattr(data, "character_id", None),
                "role": getattr(data, "role", None),
                "content": getattr(data, "content", None),
                "visibility": getattr(data, "visibility", settings.default_event_visibility),
                "location": getattr(data, "location", "") or "",
                "target_character_ids": getattr(data, "target_character_ids", "[]"),
                "channel": getattr(data, "channel", "direct") or "direct",
                "timestamp": getattr(data, "timestamp", None),
            }
        payload["target_character_ids"] = parse_target_ids(
            payload.get("target_character_ids")
        )
        payload["visibility"] = _normalize_visibility(payload.get("visibility"))
        payload["location"] = payload.get("location") or ""
        payload["channel"] = payload.get("channel") or "direct"
        return payload


class MessagePresenceCreate(BaseModel):
    message_id: int
    character_id: int
    presence: PresenceType


# ----------------------------- Memory ------------------------------
class MemoryBase(BaseModel):
    content: str
    importance: Optional[float] = 0.5
    category: Optional[str] = None


class MemoryCreate(MemoryBase):
    chat_id: int
    character_id: int


class MemoryUpdate(BaseModel):
    content: Optional[str] = None
    importance: Optional[float] = None
    category: Optional[str] = None


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
    """Structured fact from LLM memory extraction (P1)."""

    fact: str
    category: MemoryCategory = "event"
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    witnessed: bool = True

    @field_validator("fact", mode="before")
    @classmethod
    def _strip_fact(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @field_validator("category", mode="before")
    @classmethod
    def _normalize_category(cls, value: object) -> str:
        if value is None or value == "":
            return "event"
        text = str(value).strip().lower()
        if text in settings.memory_categories:
            return text
        # common aliases
        aliases = {
            "rel": "relationship",
            "relations": "relationship",
            "person": "relationship",
            "people": "relationship",
            "place": "location",
            "loc": "location",
            "object": "item",
            "thing": "item",
            "action": "event",
            "plot": "event",
        }
        return aliases.get(text, "other")

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


# ------------------------ Character Summary ------------------------
class CharacterSummaryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    chat_id: int
    character_id: int
    content: str
    through_message_id: int
    updated_at: datetime


# --------------------------- Composition ---------------------------
class ChatDetail(ChatRead):
    """Детали чата: персонажи + последние 50 сообщений."""

    characters: list[CharacterRead] = []
    messages: list[MessageRead] = []


class ClearHistoryRequest(BaseModel):
    scope: Literal["messages", "messages_memories", "full"] = "messages"


# ------------------------- Scene State (P3) --------------------------
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


class CharacterLocationUpdate(BaseModel):
    """Manual override for a character's location."""
    location: str


# ------------------------- Chat Engine -----------------------------
class UserMessage(BaseModel):
    """Тело запроса POST /api/chats/{chat_id}/message."""

    content: str
    visibility: Optional[EventVisibility] = None
    target_character_ids: list[int] = Field(default_factory=list)

    @field_validator("visibility", mode="before")
    @classmethod
    def _vis(cls, value: object) -> object:
        if value is None or value == "":
            return None
        return _normalize_visibility(value)

    @field_validator("target_character_ids", mode="before")
    @classmethod
    def _targets(cls, value: object) -> list[int]:
        return parse_target_ids(value)


# ------------------------- Memory Jobs (P3) -------------------------
class MemoryJobRead(BaseModel):
    """Job status for memory processing observability."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    chat_id: int
    job_type: str
    status: str
    payload: str
    result: Optional[str] = None
    attempt: int
    max_attempts: int
    error_message: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    correlation_id: str


# ----------------------- Character Relationships -----------------------
class RelationshipDelta(BaseModel):
    """Структурированный дельта-выход от LLM для изменения отношений."""
    source_character_id: int
    target_character_id: int
    delta_affection: int = 0
    delta_trust: int = 0
    delta_attraction: int = 0
    delta_resentment: int = 0
    delta_jealousy: int = 0
    relationship_type: str = "neutral"
    description: str = ""
    reason: str = ""
    importance: int = Field(default=5, ge=1, le=10)
    update_description: bool = False

    @field_validator("delta_affection", "delta_trust", "delta_attraction",
                     "delta_resentment", "delta_jealousy", mode="after")
    @classmethod
    def _clamp_delta(cls, value: int) -> int:
        # Max |delta| per single event = 20
        return max(-20, min(20, value))


class CharacterRelationshipRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    chat_id: int
    source_character_id: int
    target_character_id: int
    relationship_type: str = "neutral"
    affection: int = 50
    trust: int = 50
    attraction: int = 0
    resentment: int = 0
    jealousy: int = 0
    description: str = ""
    initial_description: str = ""
    updated_at: datetime


class CharacterRelationshipUpdate(BaseModel):
    relationship_type: Optional[str] = None
    affection: Optional[int] = None
    trust: Optional[int] = None
    attraction: Optional[int] = None
    resentment: Optional[int] = None
    jealousy: Optional[int] = None
    description: Optional[str] = None


class RelationshipEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    relationship_id: int
    description: str
    reason: str = ""
    delta_affection: int = 0
    delta_trust: int = 0
    delta_attraction: int = 0
    delta_resentment: int = 0
    delta_jealousy: int = 0
    importance: int = 5
    source_round_id: Optional[str] = None
    timestamp: datetime


# ----------------------- Context Builder (token-aware) -----------------------
class ContextBudget(BaseModel):
    """Token allocation for one character context (soft per-component limits)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    total_tokens: int
    system_budget: int
    state_budget: int
    summary_budget: int
    memory_budget: int
    retrieved_history_budget: int
    recent_history_min_tokens: int
    recent_history_max_tokens: int
    reserve_tokens: int


class DroppedItem(BaseModel):
    """A component/candidate dropped to stay within the token budget."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    component: str
    reason: str
    item_id: Optional[int] = None
    preview: str = ""


class ContextDiagnostics(BaseModel):
    """Aggregated ids and counts for observability (no message texts)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    oldest_included_message_id: Optional[int] = None
    newest_included_message_id: Optional[int] = None
    summary_through_message_id: Optional[int] = None
    retrieved_message_ids: list[int] = Field(default_factory=list)
    recent_message_ids: list[int] = Field(default_factory=list)
    excluded_message_ids: list[int] = Field(default_factory=list)
    memories_candidates: int = 0
    memories_selected: int = 0
    retrieved_events_selected: int = 0
    total_tokens: int = 0


class BuiltContext(BaseModel):
    """Result of ContextBuilder.build — the assembled per-character context."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    dialogue_text: str = ""
    recent_text: str = ""
    retrieved_text: str = ""
    summary_text: Optional[str] = None
    memories: list = Field(default_factory=list)
    total_tokens: int = 0
    token_count_mode: str = "estimated"
    component_tokens: dict[str, int] = Field(default_factory=dict)
    budget: ContextBudget
    dropped_items: list[DroppedItem] = Field(default_factory=list)
    diagnostics: ContextDiagnostics = Field(default_factory=ContextDiagnostics)
