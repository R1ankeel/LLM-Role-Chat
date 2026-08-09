"""ORM-модели (Sprint 2, decomposition-sprints.md §3).

Пакет разбит на доменные модули: chat, character, message, memory,
relationship, presence, scene, world, story, state, intent, lora.
Все классы реэкспортируются отсюда — публичный API пакета не меняется.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..config import settings
from ..database import Base

from .character import Character, CharacterSummary
from .chat import Chat
from .intent import Intent, NpcPlan
from .intervention import Intervention, InterventionRecipient
from .lora import ChatLoRAAdapter, LoRAAdapter
from .memory import Memory, MemoryAnchor, MemoryJob
from .message import Message
from .presence import MessagePresence
from .relationship import (
    DEFAULT_AFFECTION,
    DEFAULT_ATTRACTION,
    DEFAULT_JEALOUSY,
    DEFAULT_RELATIONSHIP_TYPE,
    DEFAULT_RESENTMENT,
    DEFAULT_TRUST,
    CharacterRelationship,
    RelationshipEvent,
    RelationshipIssue,
)
from .scene import Location, SceneState
from .state import Belief, CharacterState, ConsolidationState
from .story import EventLink, StoryEvent, StoryState, StoryThread
from .world import Thread, ThreadParticipantState, WorldEvent

__all__ = [
    "Base",
    "Belief",
    "Boolean",
    "Character",
    "CharacterRelationship",
    "CharacterState",
    "CharacterSummary",
    "Chat",
    "ChatLoRAAdapter",
    "ConsolidationState",
    "DEFAULT_AFFECTION",
    "DEFAULT_ATTRACTION",
    "DEFAULT_JEALOUSY",
    "DEFAULT_RELATIONSHIP_TYPE",
    "DEFAULT_RESENTMENT",
    "DEFAULT_TRUST",
    "DateTime",
    "EventLink",
    "Float",
    "ForeignKey",
    "Index",
    "Integer",
    "Intent",
    "Intervention",
    "InterventionRecipient",
    "LargeBinary",
    "LoRAAdapter",
    "Location",
    "Mapped",
    "Memory",
    "MemoryAnchor",
    "MemoryJob",
    "Message",
    "MessagePresence",
    "NpcPlan",
    "Optional",
    "RelationshipEvent",
    "RelationshipIssue",
    "SceneState",
    "StoryEvent",
    "StoryState",
    "StoryThread",
    "String",
    "Text",
    "Thread",
    "ThreadParticipantState",
    "UniqueConstraint",
    "WorldEvent",
    "datetime",
    "mapped_column",
    "relationship",
    "settings",
]
