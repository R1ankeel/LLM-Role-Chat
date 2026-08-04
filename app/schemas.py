"""Pydantic-схемы для CRUD-операций (Pydantic v2)."""

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import settings
from .perception import parse_target_ids, serialize_target_ids
from .stimuli import parse_stimuli, serialize_stimuli

# Допустимые роли сообщений
Role = Literal["user", "character", "system"]
PresenceType = Literal["present", "mentioned", "audible", "absent", "told"]
MemoryCategory = Literal["отношения", "событие", "локация", "предмет", "другое"]
# Sprint 2 (Plans/update20.md §7): типы памяти на единой таблице memories.
MemoryType = Literal["semantic", "episodic", "social", "story"]
EventVisibility = Literal["private", "local", "targeted", "public", "global"]
CommunicationChannel = Literal["direct", "magic", "phone", "radio", "messenger"]


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
    """Имя игрока-персонажа создаётся при создании чата (player character).

    ``player_name`` не является колонкой Chat и не возвращается в ``ChatRead``;
    используется только для именования автоматически создаваемого player-персонажа.
    """

    player_name: Optional[str] = None


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
def _validate_avatar_crop(value: object) -> str:
    """Валидация JSON-параметров кадрирования аватара.

    Допускается пустая строка (кадрирование не задано) либо JSON-объект
    вида {"scale": 1..8, "positionX": -1..1, "positionY": -1..1}.
    """
    import json

    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value)
    text = str(value).strip()
    if text == "":
        return ""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("avatar_crop должен быть валидным JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("avatar_crop должен быть JSON-объектом")
    for key in ("scale", "positionX", "positionY"):
        if key not in data:
            raise ValueError(f"avatar_crop: отсутствует ключ '{key}'")
    scale = data.get("scale")
    if not isinstance(scale, (int, float)) or not (1.0 <= float(scale) <= 8.0):
        raise ValueError("avatar_crop.scale должен быть числом от 1 до 8")
    for key in ("positionX", "positionY"):
        pos = data.get(key)
        if not isinstance(pos, (int, float)) or not (-1.0 <= float(pos) <= 1.0):
            raise ValueError(f"avatar_crop.{key} должен быть числом от -1 до 1")
    return text


class CharacterBase(BaseModel):
    name: str
    personality: str = ""
    traits: str = ""
    speech_style: str = ""
    example_messages: str = ""
    boundaries: str = ""
    background: str = ""
    relationships: str = ""
    appearance: str = ""
    avatar_url: str = ""
    avatar_crop: str = ""
    location: str = ""
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    order_index: int = 0
    is_player: bool = False


class InitialRelationship(BaseModel):
    target_id: int
    relationship_type: str = "нейтральное"
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
    appearance: Optional[str] = None
    avatar_url: Optional[str] = None
    avatar_crop: Optional[str] = None
    location: Optional[str] = None
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    order_index: Optional[int] = None
    is_player: Optional[bool] = None

    @field_validator("avatar_crop", mode="before")
    @classmethod
    def _avatar_crop(cls, value: object) -> Optional[str]:
        if value is None:
            return None
        return _validate_avatar_crop(value)


class CharacterRead(CharacterBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    chat_id: int
    created_at: datetime


# ----------------------------- Location -----------------------------
def _strip_location_name(value: object) -> object:
    if isinstance(value, str):
        return value.strip()
    return value


class LocationBase(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    adjacent_to: list[str] = Field(default_factory=list)

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, value: object) -> object:
        return _strip_location_name(value)

    @field_validator("description", mode="before")
    @classmethod
    def _desc(cls, value: object) -> object:
        if value is None:
            return ""
        return str(value).strip()

    @field_validator("adjacent_to", mode="before")
    @classmethod
    def _adjacent_to(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            import json

            try:
                parsed = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return []
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
            return []
        if isinstance(value, list):
            return [str(x).strip() for x in value if str(x).strip()]
        return []


class LocationCreate(LocationBase):
    """Локация создаётся внутри чата (chat_id берётся из пути)."""


class LocationUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1)
    description: Optional[str] = None
    adjacent_to: Optional[list[str]] = None

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, value: object) -> object:
        if value is None:
            return None
        return _strip_location_name(value)

    @field_validator("description", mode="before")
    @classmethod
    def _desc(cls, value: object) -> object:
        if value is None:
            return None
        return str(value).strip()

    @field_validator("adjacent_to", mode="before")
    @classmethod
    def _adjacent_to(cls, value: object) -> Optional[list[str]]:
        if value is None:
            return None
        if isinstance(value, str):
            import json

            try:
                parsed = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return None
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
            return None
        if isinstance(value, list):
            return [str(x).strip() for x in value if str(x).strip()]
        return None


class LocationRead(LocationBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    chat_id: int
    created_at: datetime
    updated_at: datetime


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
    stimuli: list[dict] = Field(default_factory=list)

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
        data["stimuli"] = serialize_stimuli(data.get("stimuli") or [])
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
    stimuli: list[dict] = Field(default_factory=list)
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
                "stimuli": getattr(data, "stimuli", "[]"),
                "timestamp": getattr(data, "timestamp", None),
            }
        payload["target_character_ids"] = parse_target_ids(
            payload.get("target_character_ids")
        )
        payload["visibility"] = _normalize_visibility(payload.get("visibility"))
        payload["location"] = payload.get("location") or ""
        payload["channel"] = payload.get("channel") or "direct"
        payload["stimuli"] = [s.to_dict() for s in parse_stimuli(payload.get("stimuli"))]
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


# ----------------------- Structured World Events (Sprint 1) -----------------------
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


# ------------------------- One-time intervention -------------------------
class InterventionCreate(BaseModel):
    """Body for PUT /api/chats/{chat_id}/intervention."""

    instruction: str = Field(min_length=1, max_length=2000)

    @field_validator("instruction", mode="before")
    @classmethod
    def _strip_instruction(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class InterventionRead(BaseModel):
    """Pending one-time intervention state."""

    chat_id: int
    character_id: Optional[int] = None
    instruction: str
    created_at: datetime


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
# Whitelist of issue types (docs/relations.md §7.1) — never a free-form string.
IssueType = Literal[
    "broken_promise",
    "debt",
    "unfulfilled_request",
    "lie",
    "unresolved_conflict",
    "suspicion",
    "hidden_secret",
    "missing_apology",
    "unreturned_favor",
    "emotional_grievance",
]

ISSUE_TYPES = frozenset(IssueType.__args__)


class IssueDelta(BaseModel):
    """Issue create/resolve proposal. Pair attribution is mandatory (§7.2).

    ``source_character_id`` + ``target_character_id`` resolve to a single
    relationship edge; the service never guesses the pair from the text.
    """

    source_character_id: int
    target_character_id: int
    action: Literal["create", "resolve"] = "create"
    issue_type: Optional[IssueType] = None
    text: str = ""
    importance: int = Field(default=5, ge=1, le=10)
    issue_id: Optional[int] = None
    reason: str = ""
    # Source attribution (Sprint 3 item 18): message IDs that originated this issue.
    # Validated against round context; falls back to all messages in the round.
    source_message_ids: list[int] = Field(default_factory=list)


class RelationshipDelta(BaseModel):
    """Структурированный дельта-выход от LLM для изменения отношений."""
    source_character_id: int
    target_character_id: int
    delta_affection: int = 0
    delta_trust: int = 0
    delta_attraction: int = 0
    delta_resentment: int = 0
    delta_jealousy: int = 0
    relationship_type: str = "нейтральное"
    description: str = ""
    reason: str = ""
    importance: int = Field(default=5, ge=1, le=10)
    update_description: bool = False
    issues: list[IssueDelta] = Field(default_factory=list)
    source_message_ids: list[int] = Field(default_factory=list)

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
    relationship_type: str = "нейтральное"
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

    @field_validator("relationship_type", mode="before")
    @classmethod
    def _validate_relationship_type(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        from .config import settings
        if value not in settings.relationship_valid_types:
            raise ValueError(
                f"Invalid relationship_type: '{value}'. "
                f"Must be one of: {', '.join(settings.relationship_valid_types)}"
            )
        return value


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
    event_id: Optional[int] = None
    timestamp: datetime


class RelationshipIssueRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    relationship_id: int
    issue_type: IssueType
    text: str
    importance: int
    state: Literal["open", "resolved"] = "open"
    created_round_id: Optional[str] = None
    resolved_round_id: Optional[str] = None
    created_at: datetime
    resolved_at: Optional[datetime] = None
    last_mention_round_id: Optional[str] = None
    rounds_since_last_mention: int = 0


class RelationshipIssueResolve(BaseModel):
    """Manual resolve of an open issue (docs/relations.md §7.2)."""
    reason: str = ""


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
    scene_text: str = ""
    summary_text: Optional[str] = None
    memories: list = Field(default_factory=list)
    recency_tail_text: str = ""
    total_tokens: int = 0
    token_count_mode: str = "estimated"
    component_tokens: dict[str, int] = Field(default_factory=dict)
    budget: ContextBudget
    dropped_items: list[DroppedItem] = Field(default_factory=list)
    diagnostics: ContextDiagnostics = Field(default_factory=ContextDiagnostics)


# ------------------- World & Perception Engine 3.0 (Plans/WPE.md) -------------------
# Контракт данных Фазы 0: PerceptionResult (И13), Action[] (И14),
# tool/JSON-Schema схема take_actions (§8). Написаны, НЕ подключены.

VisualLevel = Literal["full", "partial", "none"]
AudioLevel = Literal["full", "muffled", "none"]
RemoteStatus = Literal["none", "delivered"]
ActionType = Literal["move_to", "send_message"]


class PerceptionResult(BaseModel):
    """Двухканальный результат восприятия события наблюдателем (И13).

    Эфемерный объект (И8): ни текста, ни атрибуции говорящего. Каналы
    независимы: visual=full/audio=none (стекло), visual=none/audio=full
    (крик/звонок), audio=muffled (стена) — разные комбинации, не уровни
    одной шкалы. Возвращается чистой функцией `perception.perceive`.
    """

    visual_level: VisualLevel = "none"
    audio_level: AudioLevel = "none"
    addressed: bool = False
    remote_status: RemoteStatus = "none"


class Action(BaseModel):
    """Структурированное действие персонажа за ход (контракт данных, И14).

    `type` расширяем в данных, не в коде. Передача — только через native
    tools / structured outputs (§8); regex-парсинг JSON из сырого текста
    запрещён (И14). Отдельное поле `reply_target_character_ids` (Address
    Resolution, §3) живёт в `TurnOutput`.
    """

    type: ActionType
    location: Optional[str] = None  # move_to
    message: Optional[str] = None  # send_message
    channel: CommunicationChannel = "direct"
    target_character_ids: list[int] = Field(default_factory=list)

    @field_validator("target_character_ids", mode="before")
    @classmethod
    def _targets(cls, value: object) -> list[int]:
        return parse_target_ids(value)


class TurnOutput(BaseModel):
    """Структурированный выход хода персонажа (текст + действия) (Ул.4).

    Форма терминального tool-сообщения `take_actions`: адресация реплики
    отдельно от действий. Текст реплики приходит как content сообщения.
    """

    reply_target_character_ids: list[int] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)

    @field_validator("reply_target_character_ids", mode="before")
    @classmethod
    def _targets(cls, value: object) -> list[int]:
        return parse_target_ids(value)


def build_take_actions_tool() -> dict[str, Any]:
    """OpenAI-совместимая tool-схема `take_actions` (WPE.md §8, Ул.4)."""
    return {
        "type": "function",
        "function": {
            "name": "take_actions",
            "description": (
                "Действия персонажа в этом ходу (перемещение, отправка сообщения)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reply_target_character_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Кому адресована реплика (id персонажей).",
                    },
                    "actions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "enum": ["move_to", "send_message"],
                                },
                                "location": {"type": "string"},
                                "message": {"type": "string"},
                                "channel": {
                                    "type": "string",
                                    "enum": [
                                        "direct",
                                        "magic",
                                        "phone",
                                        "radio",
                                        "messenger",
                                    ],
                                },
                                "target_character_ids": {
                                    "type": "array",
                                    "items": {"type": "integer"},
                                },
                            },
                            "required": ["type"],
                        },
                    },
                },
                "required": ["reply_target_character_ids", "actions"],
            },
        },
    }


def build_take_actions_json_schema() -> dict[str, Any]:
    """JSON-Schema вариант той же схемы (Ollama `format` / OpenAI response_format)."""
    parameters = build_take_actions_tool()["function"]["parameters"]
    return {
        "type": "object",
        "properties": parameters["properties"],
        "required": parameters["required"],
        "additionalProperties": False,
    }
