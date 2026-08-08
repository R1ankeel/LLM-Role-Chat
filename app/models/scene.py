"""ORM-модели сцены и локаций: Location, SceneState (Sprint 2, §3)."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base


class Location(Base):
    """Локация чата как самостоятельная сущность (Локации 2.0).

    Источник истины для CRUD и описаний локаций. ``chats.locations``
    остаётся кэшем названий для движка и синхронизируется при CRUD-операциях.
    """

    __tablename__ = "locations"
    __table_args__ = (
        UniqueConstraint("chat_id", "name", name="uq_location_chat_name"),
        Index("ix_locations_chat_id", "chat_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # JSON list of names of adjacent locations (Sprint 2 — аудиовосприятие
    # соседних). WPE 3.0 (И13): элементы могут быть строками (имена) либо
    # объектами {"name", "visual_permeability", "audio_permeability"} —
    # проницаемость ребра по каналам. Ребро без явных значений по умолчанию
    # visual=none, audio=muffled (обратная совместимость).
    adjacent_to: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    chat: Mapped["Chat"] = relationship(back_populates="location_records")


class SceneState(Base):
    """Scene/world state tracking. Time/weather are global; locations are per-character."""

    __tablename__ = "scene_states"
    __table_args__ = (Index("ix_scene_states_chat_id", "chat_id"),)

    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), primary_key=True)
    time_of_day: Mapped[str] = mapped_column(default="")
    # JSON dict: {character_id: location_name} — cached per-character locations
    character_locations: Mapped[str] = mapped_column(default="{}")
    custom_state: Mapped[str] = mapped_column(default="{}")  # JSON: weather, mood, tension, ...
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    chat: Mapped["Chat"] = relationship(back_populates="scene_state")
