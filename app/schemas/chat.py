"""Схемы чатов и chat-эндпоинтов (Sprint 3, decomposition-sprints.md §4).

``UserMessage`` (тело ``POST /api/chats/{id}/message``) и intervention-схемы —
домен чата, поэтому живут в chat.py.
"""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..config import settings
from .character import CharacterRead
from .message import EventVisibility, MessageRead, _normalize_visibility, parse_target_ids


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
    # LoRA (§2.3): identity базовой модели для compatibility check. UI показывает
    # статус Compatible/Incompatible/Unknown; NULL → fallback на model_name
    # (результат Unknown). Добавлено в Sprint 5.
    base_model_identity: Optional[str] = None


class ChatDetail(ChatRead):
    """Детали чата: персонажи + последние 50 сообщений."""

    characters: list[CharacterRead] = []
    messages: list[MessageRead] = []


class ClearHistoryRequest(BaseModel):
    scope: Literal["messages", "messages_memories", "full"] = "messages"


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


class InterventionCreate(BaseModel):
    """Body for PUT /api/chats/{chat_id}/intervention.

    ``recipient_character_ids`` — детерминированный список NPC-получателей,
    фиксируется при создании и не пересчитывается при генерации. Пустой список
    означает, что инструкцию в этом раунде не слышит никто.
    """

    instruction: str = Field(min_length=1, max_length=2000)
    recipient_character_ids: list[int] = Field(default_factory=list)

    @field_validator("instruction", mode="before")
    @classmethod
    def _strip_instruction(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("recipient_character_ids", mode="before")
    @classmethod
    def _unique_recipients(cls, value: object) -> object:
        if value is None:
            return []
        if not isinstance(value, list):
            return value
        seen: set[int] = set()
        result: list[int] = []
        for item in value:
            try:
                cid = int(item)
            except (TypeError, ValueError):
                continue
            if cid not in seen:
                seen.add(cid)
                result.append(cid)
        return result


class InterventionRead(BaseModel):
    """Pending one-time intervention state."""

    chat_id: int
    character_id: Optional[int] = None
    instruction: str
    created_at: datetime
    recipient_character_ids: list[int] = Field(default_factory=list)
