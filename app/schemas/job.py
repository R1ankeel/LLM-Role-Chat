"""Схема статусов фоновых джобов памяти (Sprint 3, decomposition-sprints.md §4)."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


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
