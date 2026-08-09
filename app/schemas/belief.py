"""Схемы системы убеждений персонажей (Sprint 3, decomposition-sprints.md §4)."""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

BeliefSource = Literal[
    "direct_observation",
    "heard",
    "told_by",
    "inference",
    "rumor",
    "memory",
]
BeliefType = Literal["fact", "belief", "suspicion"]


class BeliefRead(BaseModel):
    """Знание/убеждение персонажа (Plans/update20.md §9, Sprint 5).

    Триплет «subject predicate object», источник, уверенность (0..1) и тип
    (fact = «знает», belief = «полагает», suspicion = «подозревает»).
    `world_truth_ref` — FK на каноническое world_events (NULL, если мир не
    подтвердил). Персонажу в контекст попадают ТОЛЬКО его beliefs.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    chat_id: int
    character_id: int
    subject: str
    predicate: str
    object: str
    source: BeliefSource
    confidence: float
    type: BeliefType
    world_truth_ref: Optional[int] = None
    created_at: datetime
    updated_at: datetime
