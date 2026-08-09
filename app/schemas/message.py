"""Схемы сообщений (Sprint 3, decomposition-sprints.md §4).

Shared literals сообщений (``Role``/``PresenceType``/``EventVisibility``/
``CommunicationChannel``) живут здесь — на них ссылаются chat.py, memory.py,
perception.py. Служебный ``_normalize_visibility`` — общий для Message* и
UserMessage (chat.py).
"""

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..config import settings
from ..perception import parse_target_ids, serialize_target_ids
from ..stimuli import parse_stimuli, serialize_stimuli

# Допустимые роли сообщений
Role = Literal["user", "character", "system"]
PresenceType = Literal["present", "mentioned", "audible", "absent", "told"]
EventVisibility = Literal["private", "local", "targeted", "public", "global"]
CommunicationChannel = Literal["direct", "magic", "phone", "radio", "messenger"]


def _normalize_visibility(value: object) -> str:
    if value is None or value == "":
        return settings.default_event_visibility
    text = str(value).strip().lower()
    if text in settings.event_visibilities:
        return text
    return settings.default_event_visibility


class MessageBase(BaseModel):
    role: Role
    content: str


class MessageCreate(MessageBase):
    chat_id: int
    character_id: Optional[int] = None
    visibility: EventVisibility = settings.default_event_visibility  # type: ignore[assignment]
    location: str = ""
    location_id: Optional[int] = None
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
    location_id: Optional[int] = None
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
                "location_id": getattr(data, "location_id", None),
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
    # Sprint 4 (Plans/update20.md §11): attention score (0..1), nullable.
    # Отсутствует/None → attention не пишется (флаг off, legacy-поведение).
    attention: Optional[float] = Field(default=None, ge=0.0, le=1.0)
