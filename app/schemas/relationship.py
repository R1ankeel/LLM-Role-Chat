"""Схемы отношений и issues (Sprint 3, decomposition-sprints.md §4)."""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
        from ..config import settings
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
