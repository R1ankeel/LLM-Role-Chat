"""ORM-модели (SQLAlchemy 2.0 синтаксис: Mapped / mapped_column)."""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .config import settings
from .database import Base

# Default values for relationship metrics
DEFAULT_AFFECTION = 50
DEFAULT_TRUST = 50
DEFAULT_ATTRACTION = 0
DEFAULT_RESENTMENT = 0
DEFAULT_JEALOUSY = 0
DEFAULT_RELATIONSHIP_TYPE = "нейтральное"


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


class WorldEvent(Base):
    """Неизменяемый (append-only) журнал world-событий (WPE 3.0, Фаза 0).

    Заведён, но до Фазы 3 не пишется. Иммутабельность после вставки (И9)
    обеспечивается на уровне кода: нет update-эндпоинтов, значения не
    меняются постфактум. ``message_id`` связывает речевое событие с
    ``messages``; для `move` фиксируются ``location_from``/``location_to``.
    """

    __tablename__ = "world_events"
    __table_args__ = (
        Index("ix_world_events_chat_ts", "chat_id", "created_at", "id"),
        Index("ix_world_events_character_id", "character_id"),
        Index("ix_world_events_round_id", "round_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    # NULL для system/global событий без автора
    character_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("characters.id", ondelete="SET NULL"), nullable=True
    )
    # Привязка к речевому сообщению (NULL для чистых действий/системных)
    message_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    # speech | move | system_narrator | system
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # legacy-bridge: строковая локация события (WPE 3.0 → location_id в Фазе 3).
    # Sprint 0 (Plans/update20.md): каноническая локация как FK на
    # `locations.id`. Backfill из строковой `location` — отдельным скриптом
    # (`scripts/backfill_event_location_ids.py`, аналог `characters.location_id`).
    location: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    location_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("locations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    location_from: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    location_to: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    round_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    target_character_ids: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    # Sprint 1 (Plans/update20.md §15): structured event metadata. `action` —
    # JSON {"actor", "action", "target", "object"} (или {}); importance 0..10,
    # story_salience / emotional_salience 0..1. Заполняются раундной event
    # extraction (`event_service.extract_round_events`) под флагом
    # `EVENT_EXTRACTION_ENABLED`; read-path пока не читает.
    action: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    importance: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    story_salience: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    emotional_salience: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    chat: Mapped["Chat"] = relationship(back_populates="world_events")
    character: Mapped[Optional["Character"]] = relationship()


class Thread(Base):
    """Тред/канал общения (мессенджер, звонок и т.д.) (WPE 3.0, Фаза 0).

    Заведён, до Фазы 6 не пишется. ``remote_status=delivered`` адресата
    определяется через ``ThreadParticipantState`` независимо от локации.
    """

    __tablename__ = "threads"
    __table_args__ = (Index("ix_threads_chat_id", "chat_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    channel: Mapped[str] = mapped_column(
        String(20), default="messenger", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    chat: Mapped["Chat"] = relationship(back_populates="threads")
    participants: Mapped[list["ThreadParticipantState"]] = relationship(
        back_populates="thread", cascade="all, delete-orphan"
    )


class ThreadParticipantState(Base):
    """Состояние участника треда (доставка/прочтение) (WPE 3.0, Фаза 0)."""

    __tablename__ = "thread_participant_states"
    __table_args__ = (
        UniqueConstraint("thread_id", "character_id", name="uq_thread_participant"),
        Index("ix_thread_participant_character", "character_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("threads.id", ondelete="CASCADE"), nullable=False
    )
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    last_delivered_message_id: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    last_read_message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    thread: Mapped["Thread"] = relationship(back_populates="participants")
    character: Mapped["Character"] = relationship()


class Character(Base):
    """Персонаж, участвующий в чате."""

    __tablename__ = "characters"
    __table_args__ = (Index("ix_characters_chat_order", "chat_id", "order_index"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    personality: Mapped[str] = mapped_column(Text, default="")
    traits: Mapped[str] = mapped_column(Text, default="")
    speech_style: Mapped[str] = mapped_column(Text, default="")
    example_messages: Mapped[str] = mapped_column(Text, default="")
    boundaries: Mapped[str] = mapped_column(Text, default="")
    background: Mapped[str] = mapped_column(Text, default="")
    relationships: Mapped[str] = mapped_column(Text, default="")
    appearance: Mapped[str] = mapped_column(Text, default="")
    avatar_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    # JSON: {"scale": number, "positionX": number, "positionY": number} —
    # параметры кадрирования аватара (docs/avatar_ui_crop_spec.md §4).
    # Пустая строка = кадрирование не задано (стандартное object-fit: cover).
    avatar_crop: Mapped[str] = mapped_column(Text, default="", nullable=False)
    location: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    # WPE 3.0 (Фаза 0): каноническая локация как FK на `locations.id`.
    # Фаза 8 (аудит legacy-полей §6 v2): `location` (строка) — read-only
    # legacy-bridge, все write-path (`update_character_location`,
    # `update_character_locations_batch`) пишут также `location_id`.
    location_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("locations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    temperature: Mapped[Optional[float]] = mapped_column(Float, nullable=True, default=None)
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    is_player: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    chat: Mapped["Chat"] = relationship(back_populates="characters")
    messages: Mapped[list["Message"]] = relationship(back_populates="character")
    # Память персонажа удаляется вместе с ним
    memories: Mapped[list["Memory"]] = relationship(
        back_populates="character", cascade="all, delete-orphan"
    )
    summary: Mapped[Optional["CharacterSummary"]] = relationship(
        back_populates="character", cascade="all, delete-orphan", uselist=False
    )


class Message(Base):
    """Сообщение в чате = world event (user / character / system)."""

    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_chat_ts", "chat_id", "timestamp", "id"),
        Index("ix_messages_character_id", "character_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    # nullable: сообщения пользователя/системы не привязаны к персонажу;
    # при удалении персонажа его сообщения остаются в истории (SET NULL)
    character_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("characters.id", ondelete="SET NULL"), nullable=True
    )
    role: Mapped[str] = mapped_column(String(50), nullable=False)  # user/character/system
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Event / perception metadata.
    # WPE 3.0 Фаза 8 (аудит legacy-полей §6 v2): `visibility` — read-only
    # legacy-bridge, задаётся только при создании сообщения (no update-path).
    visibility: Mapped[str] = mapped_column(
        String(20), default=settings.default_event_visibility, nullable=False
    )
    location: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    # WPE 3.0: canonical identity of the event's location (nullable — legacy
    # rows keep NULL and use the string fallback in perception). `location`
    # stays the string snapshot of the same canonical Location's name.
    location_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("locations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # JSON list of character ids for private/targeted events
    target_character_ids: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    # Communication channel: direct | magic | phone | radio | messenger
    channel: Mapped[str] = mapped_column(String(20), default="direct", nullable=False)
    # JSON list of world-event stimuli (knock/call/shout/address/loud_sound)
    stimuli: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    chat: Mapped["Chat"] = relationship(back_populates="messages")
    character: Mapped[Optional["Character"]] = relationship(back_populates="messages")
    presence_records: Mapped[list["MessagePresence"]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )


class MessagePresence(Base):
    """Per-character witness presence for a message.

    Sprint 4 (Plans/update20.md §11): колонка ``attention`` (REAL NULL) — score
    внимания персонажа к событию (0..1), пишется движком детерминированно вместе
    с presence. Фильтрует то, что идёт в память (attention < ATTENTION_LOW → не
    в память) и recency tail; presence-лестницу не меняет. NULL — attention не
    считался (флаг off) → legacy-поведение.
    """

    __tablename__ = "message_presence"
    __table_args__ = (
        Index("ix_presence_character_message", "character_id", "message_id"),
        UniqueConstraint("message_id", "character_id", name="uq_presence_message_character"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    presence: Mapped[str] = mapped_column(String(20), nullable=False)
    attention: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    message: Mapped["Message"] = relationship(back_populates="presence_records")
    character: Mapped["Character"] = relationship()


class Memory(Base):
    """Долгосрочная память персонажа в рамках чата.

    Sprint 2 (Plans/update20.md §7): единая таблица памяти дополнена осями типа
    и эмоциональной окраски. ``memory_type`` — semantic | episodic | social |
    story (для существующих строк при миграции — 'semantic'); ``event_id`` —
    проекция на каноническое ``world_events`` (источник события, §15.0);
    ``valence`` [-1..1] / ``intensity`` [0..1] — эмоциональная окраска.
    Дедупликация остаётся по ``(character_id, content_hash)`` — type в hash
    не входит (см. «Риски» Sprint 2). read-path не читает новые поля, пока
    ``MEMORY_TYPES_ENABLED=false``.
    """

    __tablename__ = "memories"
    __table_args__ = (
        Index("ix_memories_char_created", "character_id", "created_at", "id"),
        Index("ix_memories_char_imp_created", "character_id", "importance", "created_at"),
        Index("ix_memories_char_last_accessed", "character_id", "last_accessed_at"),
        Index("ix_memories_char_type", "character_id", "memory_type"),
        Index("ix_memories_event", "event_id"),
        UniqueConstraint("character_id", "content_hash", name="uq_memory_char_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    importance: Mapped[float] = mapped_column(Float, default=0.5, nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    last_accessed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    source_message_ids: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    embedding: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    # Sprint 2 (Plans/update20.md §7): semantic | episodic | social | story.
    # Default 'semantic' — миграция существующих строк без дата-потерь.
    memory_type: Mapped[str] = mapped_column(
        String(20), default="semantic", nullable=False
    )
    # Проекция на каноническое world_events (§15.0); NULL для фактов без события.
    event_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("world_events.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Эмоциональная окраска: valence [-1..1], intensity [0..1].
    valence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    intensity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    chat: Mapped["Chat"] = relationship(back_populates="memories")
    character: Mapped["Character"] = relationship(back_populates="memories")


class CharacterSummary(Base):
    """Per-character session summary (level-3 memory)."""

    __tablename__ = "character_summaries"
    __table_args__ = (
        Index("ix_summaries_chat_character", "chat_id", "character_id"),
        UniqueConstraint("character_id", name="uq_summary_character"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    through_message_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    chat: Mapped["Chat"] = relationship(back_populates="character_summaries")
    character: Mapped["Character"] = relationship(back_populates="summary")


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


class MemoryJob(Base):
    """Persistent job tracking for memory processing (observability)."""

    __tablename__ = "memory_jobs"
    __table_args__ = (Index("ix_memory_jobs_chat_status", "chat_id", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(Integer, index=True)
    job_type: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    payload: Mapped[str] = mapped_column(Text)
    result: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(64), index=True)


class RelationshipIssue(Base):
    """Открытый сюжетный крючок между парой (docs/relations.md §7).

    Пара однозначна через ``relationship_id`` (source+target). Text — это
    ДАННЫЕ сцены, а не инструкция для LLM (§14): ограничен по длине и
    очищается от маркеров prompt injection при создании.
    """

    __tablename__ = "relationship_issues"
    __table_args__ = (
        Index("ix_rel_issues_rel_state", "relationship_id", "state"),
        Index("ix_rel_issues_state", "state"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    relationship_id: Mapped[int] = mapped_column(
        ForeignKey("character_relationships.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    issue_type: Mapped[str] = mapped_column(String(50), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    state: Mapped[str] = mapped_column(String(20), default="open", nullable=False)
    created_round_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    resolved_round_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_mention_round_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # Deterministic salience counter (docs/relations.md §7.4): grows each round
    # the issue is absent from context/analysis, reset to 0 on mention.
    rounds_since_last_mention: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    # Source attribution (Sprint 3 item 18): JSON array of message IDs that
    # originated/led to this issue (validated against round context).
    source_message_ids: Mapped[str] = mapped_column(Text, default="[]", nullable=False)

    relationship: Mapped["CharacterRelationship"] = relationship(
        back_populates="issues"
    )


class CharacterRelationship(Base):
    """Динамическое отношение персонажа к другому персонажу (направленное).

    source_character_id -> target_character_id
    """

    __tablename__ = "character_relationships"
    __table_args__ = (
        Index("ix_rel_source_target", "source_character_id", "target_character_id"),
        Index("ix_rel_chat_source", "chat_id", "source_character_id"),
        UniqueConstraint(
            "source_character_id", "target_character_id",
            name="uq_relationship_pair"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    source_character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    target_character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    relationship_type: Mapped[str] = mapped_column(
        String(50), default=DEFAULT_RELATIONSHIP_TYPE, nullable=False
    )
    affection: Mapped[int] = mapped_column(Integer, default=DEFAULT_AFFECTION, nullable=False)
    trust: Mapped[int] = mapped_column(Integer, default=DEFAULT_TRUST, nullable=False)
    attraction: Mapped[int] = mapped_column(Integer, default=DEFAULT_ATTRACTION, nullable=False)
    resentment: Mapped[int] = mapped_column(Integer, default=DEFAULT_RESENTMENT, nullable=False)
    jealousy: Mapped[int] = mapped_column(Integer, default=DEFAULT_JEALOUSY, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    initial_description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    chat: Mapped["Chat"] = relationship()
    source_character: Mapped["Character"] = relationship(foreign_keys=[source_character_id])
    target_character: Mapped["Character"] = relationship(foreign_keys=[target_character_id])
    issues: Mapped[list["RelationshipIssue"]] = relationship(
        back_populates="relationship", cascade="all, delete-orphan"
    )


class RelationshipEvent(Base):
    """Журнал значимых изменений отношений."""

    __tablename__ = "relationship_events"
    __table_args__ = (
        Index("ix_rel_events_rel_id", "relationship_id"),
        Index("ix_rel_events_ts", "relationship_id", "timestamp"),
        Index("ix_rel_events_kind", "kind"),
        Index("ix_rel_events_round", "round_id"),
        Index("ix_rel_events_event_id", "event_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    relationship_id: Mapped[int] = mapped_column(
        ForeignKey("character_relationships.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(
        String(20), default="llm", nullable=False
    )  # "llm" | "decay" | "manual" | "archive"
    description: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    delta_affection: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    delta_trust: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    delta_attraction: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    delta_resentment: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    delta_jealousy: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Snapshot of state AFTER this event
    affection_after: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    trust_after: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attraction_after: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    resentment_after: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    jealousy_after: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    importance: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    source_message_ids: Mapped[str] = mapped_column(Text, default="[]", nullable=False)  # JSON array
    round_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    source_round_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    # Sprint 1 (Plans/update20.md §15.2): проекция на каноническое событие.
    # Заполняется только если событие порождено раундной event extraction.
    event_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("world_events.id", ondelete="SET NULL"), nullable=True, index=True
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    relationship: Mapped["CharacterRelationship"] = relationship()


# ------------------------- State-driven tables (Sprint 0) -------------------------
# Пустые таблицы заведены в Sprint 0 (Plans/update20.md) как фундамент под
# будущие спринты. read-path их НЕ читает и write-path НЕ пишет до соответствующих
# спринтов: character_states (3), beliefs (5), story_* (8-11), event_links (1),
# memory_anchors (2/7), intents/npc_plans (10), consolidation_state (12).
# Откат тривиален: новые таблицы изолированы от существующего поведения.


class CharacterState(Base):
    """Runtime-состояние персонажа (Plans/update20.md §8, Sprint 3).

    Хранит ТОЛЬКО то, чего нет в других таблицах: эмоции, стресс, физическое
    состояние, внимание, цели. Локация — из `characters.location_id`,
    отношения — из `character_relationships`, окружение — из `scene_states`.
    """

    __tablename__ = "character_states"
    __table_args__ = (
        UniqueConstraint("character_id", name="uq_character_state_character"),
        Index("ix_character_states_chat_id", "chat_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # JSON map emotion→intensity, e.g. {"suspicion":0.7,"relief":0.2}
    emotional_state: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    # neutral/tense/hopeful/... (из interpreter)
    mood: Mapped[str] = mapped_column(String(50), default="", nullable=False)
    # 0..1
    stress: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # JSON: energy, wounds, conditions (free-form, пишется LLM)
    physical_state: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    # current_focus (строка) — «следит за Борисом»; NULL = нет фокуса
    attention: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # на кого смотрит (FK characters, nullable)
    current_focus_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("characters.id", ondelete="SET NULL"), nullable=True
    )
    active_goal: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    # JSON list персональных целей
    personal_goals: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    updated_round_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    character: Mapped["Character"] = relationship(foreign_keys=[character_id])


class Belief(Base):
    """Знание/убеждение персонажа (Plans/update20.md §9, Sprint 5).

    Персонаж НЕ автоматически знает World Truth — в контекст попадают только
    его beliefs. `world_truth_ref` — FK на каноническое `world_events` (NULL,
    если подтверждения миром не было).
    """

    __tablename__ = "beliefs"
    __table_args__ = (
        Index("ix_beliefs_character", "character_id"),
        Index("ix_beliefs_chat_character", "chat_id", "character_id"),
        Index("ix_beliefs_subject", "subject"),
        Index("ix_beliefs_world_truth_ref", "world_truth_ref"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    predicate: Mapped[str] = mapped_column(String(255), nullable=False)
    object: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    # direct_observation | heard | told_by | inference | rumor | memory
    source: Mapped[str] = mapped_column(String(30), default="memory", nullable=False)
    # 0..1
    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    # fact | belief | suspicion — различие «знает» vs «полагает»
    type: Mapped[str] = mapped_column(String(20), default="belief", nullable=False)
    world_truth_ref: Mapped[Optional[int]] = mapped_column(
        ForeignKey("world_events.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    character: Mapped["Character"] = relationship()


class StoryState(Base):
    """Current Story State (Plans/update20.md §16.2, Sprint 8/9).

    `original_plot` (immutable) дублирует `chats.original_plot` как срез на
    момент версии; `current_story` — структурированный JSON
    (summary/active_threads/completed_goals/progress/phase/characters).
    """

    __tablename__ = "story_states"
    __table_args__ = (Index("ix_story_states_chat_id", "chat_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    original_plot: Mapped[str] = mapped_column(Text, default="", nullable=False)
    current_story: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    story_phase: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    updated_round_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # Versioning для rollback (Sprint 9): при консолидации версия растёт,
    # невалидный результат не применяется — предыдущая версия остаётся.
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # Число раундов (distinct round_id в world_events) на момент последней
    # консолидации — для trigger §17.1 (интервал в раундах).
    last_consolidation_rounds: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class StoryThread(Base):
    """Активная сюжетная линия (Plans/update20.md §16, Sprint 10).

    `actors` — JSON-список имён/ids участников; `importance` — салиентность
    для контекста; `status` — active | archived.
    """

    __tablename__ = "story_threads"
    __table_args__ = (Index("ix_story_threads_chat_status", "chat_id", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    actors: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    importance: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    created_round_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class StoryEvent(Base):
    """Проекция канонического `world_events` для сюжета (§15.0, §16.3).

    НЕ дублирует поля `world_events` — ссылается через `event_id`. Поля-
    надстройки (story_thread_id, cause/consequences) — проекционная разметка.
    """

    __tablename__ = "story_events"
    __table_args__ = (
        Index("ix_story_events_chat_id", "chat_id"),
        Index("ix_story_events_event_id", "event_id"),
        Index("ix_story_events_thread", "story_thread_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    event_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("world_events.id", ondelete="CASCADE"), nullable=True
    )
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    round_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    event: Mapped[str] = mapped_column(Text, default="", nullable=False)
    actors: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    location: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    cause: Mapped[str] = mapped_column(Text, default="", nullable=False)
    consequences: Mapped[str] = mapped_column(Text, default="", nullable=False)
    importance: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    story_thread_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("story_threads.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class EventLink(Base):
    """Причинно-следственное ребро событий (§15.1, Sprint 1).

    `event_id` → `caused_by_event_id`; `kind`: causes | consequence | goal_step
    | resolution.
    """

    __tablename__ = "event_links"
    __table_args__ = (
        Index("ix_event_links_chat_id", "chat_id"),
        Index("ix_event_links_event_id", "event_id"),
        Index("ix_event_links_caused_by", "caused_by_event_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    event_id: Mapped[int] = mapped_column(
        ForeignKey("world_events.id", ondelete="CASCADE"), nullable=False
    )
    caused_by_event_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("world_events.id", ondelete="CASCADE"), nullable=True
    )
    kind: Mapped[str] = mapped_column(
        String(20), default="causes", nullable=False
    )


class MemoryAnchor(Base):
    """Эмоциональный якорь направленного отношения (§7, Sprint 2/7).

    `relationship_id` — FK на `character_relationships` (source→target);
    `event_id` — FK на каноническое `world_events` (источник события).
    """

    __tablename__ = "memory_anchors"
    __table_args__ = (
        Index("ix_anchors_rel", "relationship_id"),
        Index("ix_anchors_event", "event_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    relationship_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("character_relationships.id", ondelete="CASCADE"), nullable=True
    )
    event_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("world_events.id", ondelete="CASCADE"), nullable=True
    )
    emotion: Mapped[str] = mapped_column(String(50), default="", nullable=False)
    # valence [-1..1], intensity [0..1]
    valence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    intensity: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    importance: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Intent(Base):
    """Intent NPC на ход (§21, Sprint 10).

    `target` — FK characters (nullable); `approach`: direct | indirect | avoid |
    delay; urgency/risk — 0..1.
    """

    __tablename__ = "intents"
    __table_args__ = (
        Index("ix_intents_chat_character", "chat_id", "character_id"),
        Index("ix_intents_round", "created_round_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    goal: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    target: Mapped[Optional[int]] = mapped_column(
        ForeignKey("characters.id", ondelete="SET NULL"), nullable=True
    )
    approach: Mapped[str] = mapped_column(
        String(20), default="direct", nullable=False
    )
    urgency: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    emotion: Mapped[str] = mapped_column(String(50), default="", nullable=False)
    risk: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    created_round_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class NpcPlan(Base):
    """Долгоживущий маленький план NPC (§22, Sprint 10).

    «Я хочу сделать X, но сейчас мне мешает Y». НЕ GOAP/planner. Один активный
    план на персонажа (обычно). status: active | blocked | done | abandoned.
    """

    __tablename__ = "npc_plans"
    __table_args__ = (
        Index("ix_npc_plans_chat_character", "chat_id", "character_id"),
        Index("ix_npc_plans_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    goal: Mapped[str] = mapped_column(String(500), nullable=False)
    next_step: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    blocked_by: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="active", nullable=False
    )
    created_round_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class ConsolidationState(Base):
    """Счётчики adaptive consolidation (§20, Sprint 12).

    Заменит 24h-таймер: score-пороги soft/hard/critical. Заведена в Sprint 0
    (по §E), write-path — Sprint 12.
    """

    __tablename__ = "consolidation_state"
    __table_args__ = (Index("ix_consolidation_state_chat", "chat_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    last_soft_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_hard_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # JSON counters: {"messages": N, "events": N, "facts": N, "rel_events": N,
    #                  "story_events": N, "anchors": N}
    counters: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


# ------------------- LoRA adapters (Plans/LoRA.md, Sprint 1) -------------------
# Глобальный registry + связка «чат → адаптер». MVP (§2.5): ровно один адаптер
# на чат (UNIQUE(chat_id)), поля weight/order_index НЕ создаются.
# read-path (chat_engine) новые таблицы пока не читает — Sprint 2/3.


class LoRAAdapter(Base):
    """Зарегистрированный LoRA-адаптер (глобальный registry, §2.6).

    Хранит МЕТАДАННЫЕ регистрации, а не файл: ``path`` — абсолютный путь к
    файлу пользователя (физический файл никогда не удаляется, §2.7).
    ``format`` — gguf | safetensors | auto; при регистрации auto нормализуется
    в фактически определённый формат (в MVP — всегда "gguf",
    ``supports_safetensors=false``, §2.5). ``sha256`` — содержимое файла
    (blob-диджест, §2.2): используется для blob-флоу и runtime key.
    """

    __tablename__ = "lora_adapters"
    __table_args__ = (
        Index("ix_lora_adapters_enabled", "enabled"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    # gguf | safetensors | auto (после create/update — конкретный формат "gguf")
    format: Mapped[str] = mapped_column(String(20), default="auto", nullable=False)
    # наименование базовой модели для справки (не identity, см. §2.3)
    base_model: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    # identity базовой модели (§2.3), nullable: используется для compatibility
    # check, НЕ строковое сравнение имён с chat.model_name
    base_model_identity: Mapped[Optional[str]] = mapped_column(
        String(512), nullable=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    # JSON-объект произвольных метаданных регистрации. Атрибут называется
    # `metadata_json` (колонка в БД — `metadata`), т.к. имя `metadata`
    # зарезервировано в Declarative API (Base.metadata).
    metadata_json: Mapped[str] = mapped_column(
        "metadata", Text, default="{}", nullable=False
    )
    # sha256 содержимого файла адаптера (blob-диджест, §2.2)
    sha256: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    chat_links: Mapped[list["ChatLoRAAdapter"]] = relationship(
        back_populates="adapter", cascade="all, delete-orphan"
    )


class ChatLoRAAdapter(Base):
    """Связка «чат → LoRA-адаптер» (конфигурация чата, §2.6).

    UNIQUE(chat_id) гарантирует НЕ более одного адаптера на чат (MVP §2.5).
    Поля ``weight``/``order_index`` НЕ создаются. Строка живёт ровно пока
    адаптер выбран в чате; `Chat.lora_enabled` управляется отдельно.
    """

    __tablename__ = "chat_lora_adapters"
    __table_args__ = (
        UniqueConstraint("chat_id", name="uq_chat_lora_chat"),
        Index("ix_chat_lora_adapter_id", "adapter_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    adapter_id: Mapped[int] = mapped_column(
        ForeignKey("lora_adapters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    chat: Mapped["Chat"] = relationship(back_populates="lora_adapter")
    adapter: Mapped["LoRAAdapter"] = relationship(back_populates="chat_links")