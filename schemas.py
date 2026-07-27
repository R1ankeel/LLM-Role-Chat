"""Pydantic-схемы для CRUD-операций (Pydantic v2)."""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

# Допустимые роли сообщений
Role = Literal["user", "character", "system"]

DEFAULT_MODEL = "qwen3-coder:30b-a3b-q4_K_M"
DEFAULT_HISTORY_LENGTH = 30


# ------------------------------ Chat ------------------------------
class ChatBase(BaseModel):
    name: str
    general_prompt: str = ""
    model_name: str = DEFAULT_MODEL
    max_history_length: int = DEFAULT_HISTORY_LENGTH


class ChatCreate(ChatBase):
    pass


class ChatUpdate(BaseModel):
    name: Optional[str] = None
    general_prompt: Optional[str] = None
    model_name: Optional[str] = None
    max_history_length: Optional[int] = None


class ChatRead(ChatBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime


# ---------------------------- Character ----------------------------
class CharacterBase(BaseModel):
    name: str
    personality: str = ""
    traits: str = ""
    order_index: int = 0


class CharacterCreate(CharacterBase):
    """chat_id берётся из пути эндпоинта POST /api/chats/{chat_id}/characters."""

    pass


class CharacterUpdate(BaseModel):
    name: Optional[str] = None
    personality: Optional[str] = None
    traits: Optional[str] = None
    order_index: Optional[int] = None


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


class MessageRead(MessageBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    chat_id: int
    character_id: Optional[int] = None
    timestamp: datetime


# ----------------------------- Memory ------------------------------
class MemoryBase(BaseModel):
    content: str


class MemoryCreate(MemoryBase):
    chat_id: int
    character_id: int


class MemoryUpdate(BaseModel):
    content: Optional[str] = None


class MemoryRead(MemoryBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    chat_id: int
    character_id: int
    created_at: datetime


# --------------------------- Composition ---------------------------
class ChatDetail(ChatRead):
    """Детали чата: персонажи + последние 50 сообщений."""

    characters: list[CharacterRead] = []
    messages: list[MessageRead] = []


# ------------------------- Chat Engine -----------------------------
class UserMessage(BaseModel):
    """Тело запроса POST /api/chats/{chat_id}/message."""

    content: str