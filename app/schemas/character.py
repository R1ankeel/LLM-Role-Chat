"""Схемы персонажей и локаций (Sprint 3, decomposition-sprints.md §4).

Локации (Location*) — домен персонажей/сцены, поэтому живут в character.py
(в плане §4.6 отдельного ``locations.py`` в ``schemas/`` нет).
"""

import json
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    # Ручной переключатель участия в автоматической генерации (default=True).
    # is_active=false не влияет на существование персонажа в мире/локации.
    is_active: bool = True


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
    is_active: Optional[bool] = None

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


class CharacterLocationUpdate(BaseModel):
    """Manual override for a character's location."""
    location: str


class CharacterSummaryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    chat_id: int
    character_id: int
    content: str
    through_message_id: int
    updated_at: datetime
