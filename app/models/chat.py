"""ORM-модель Chat (Sprint 2, decomposition-sprints.md §3)."""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..config import settings
from ..database import Base


class Chat(Base):
    """Чат (сессия ролевой игры)."""

    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    general_prompt: Mapped[str] = mapped_column(Text, default="")
    # Story separation (Plans/update20.md §16.1, Sprint 0): `original_plot` —
    # неизменяемый пользовательский замысел (LLM не может его менять);
    # `story_prompt` — текущее эволюционирующее story prompt; `story_enabled` —
    # флаг включения динамического сюжета. Начальные значения — миграция из
    # `general_prompt` (copy, не move); read-path пока не читает.
    original_plot: Mapped[str] = mapped_column(Text, default="", nullable=False)
    story_prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    story_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    model_name: Mapped[str] = mapped_column(String(255), default=settings.default_model)
    max_history_length: Mapped[int] = mapped_column(
        Integer, default=settings.default_history_length
    )
    thinking_mode: Mapped[bool] = mapped_column(
        Boolean, default=settings.enable_thinking, nullable=False
    )
    player_location: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    locations: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    # LoRA (Plans/LoRA.md Sprint 1): включение LoRA на чат. Семантика трёх
    # состояний (§2.4): false — исходный chat.model_name; true + адаптер не
    # выбран — допустимо (пустая runtime-модель не создаётся); true + 1 адаптер
    # (связка chat_lora_adapters) — runtime-модель. Флаг отделён от связки:
    # adapter может быть не выбран.
    lora_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    # LoRA (§2.3): identity базовой модели для compatibility check, nullable.
    # Если не задано — низкодоверенная fallback на `model_name`. В отличие от
    # `model_name` (имя для API Ollama), identity — это, например, HF-идентификатор
    # (`Naphula/Goetia-...`), который нельзя сравнивать строковым `==` с именем.
    base_model_identity: Mapped[Optional[str]] = mapped_column(
        String(512), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Каскадное удаление: вместе с чатом удаляются персонажи, сообщения и память
    characters: Mapped[list["Character"]] = relationship(
        back_populates="chat", cascade="all, delete-orphan"
    )
    messages: Mapped[list["Message"]] = relationship(
        back_populates="chat", cascade="all, delete-orphan"
    )
    memories: Mapped[list["Memory"]] = relationship(
        back_populates="chat", cascade="all, delete-orphan"
    )
    character_summaries: Mapped[list["CharacterSummary"]] = relationship(
        back_populates="chat", cascade="all, delete-orphan"
    )
    scene_state: Mapped[Optional["SceneState"]] = relationship(
        back_populates="chat", cascade="all, delete-orphan", uselist=False
    )
    # Локации 2.0: самостоятельная сущность (источник истины для CRUD и
    # описаний). `chats.locations` остаётся кэшем названий для движка.
    location_records: Mapped[list["Location"]] = relationship(
        back_populates="chat", cascade="all, delete-orphan"
    )
    # World & Perception Engine 3.0 (Фаза 0): журнал world-событий и треды.
    world_events: Mapped[list["WorldEvent"]] = relationship(
        back_populates="chat", cascade="all, delete-orphan"
    )
    threads: Mapped[list["Thread"]] = relationship(
        back_populates="chat", cascade="all, delete-orphan"
    )
    # LoRA (Plans/LoRA.md, MVP §2.5): РОВНО одна связка на чат (UNIQUE(chat_id)
    # в chat_lora_adapters). relationship 1:1; weight/order_index отсутствуют.
    lora_adapter: Mapped[Optional["ChatLoRAAdapter"]] = relationship(
        back_populates="chat", cascade="all, delete-orphan", uselist=False
    )
