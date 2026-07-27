"""ORM-модели (SQLAlchemy 2.0 синтаксис: Mapped / mapped_column)."""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base

DEFAULT_MODEL = "qwen3-coder:30b-a3b-q4_K_M"
DEFAULT_HISTORY_LENGTH = 30


class Chat(Base):
    """Чат (сессия ролевой игры)."""

    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    general_prompt: Mapped[str] = mapped_column(Text, default="")
    model_name: Mapped[str] = mapped_column(String(255), default=DEFAULT_MODEL)
    max_history_length: Mapped[int] = mapped_column(
        Integer, default=DEFAULT_HISTORY_LENGTH
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


class Character(Base):
    """Персонаж, участвующий в чате."""

    __tablename__ = "characters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    personality: Mapped[str] = mapped_column(Text, default="")
    traits: Mapped[str] = mapped_column(Text, default="")
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    chat: Mapped["Chat"] = relationship(back_populates="characters")
    messages: Mapped[list["Message"]] = relationship(back_populates="character")
    # Память персонажа удаляется вместе с ним
    memories: Mapped[list["Memory"]] = relationship(
        back_populates="character", cascade="all, delete-orphan"
    )


class Message(Base):
    """Сообщение в чате (от пользователя, персонажа или системы)."""

    __tablename__ = "messages"

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
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    chat: Mapped["Chat"] = relationship(back_populates="messages")
    character: Mapped[Optional["Character"]] = relationship(back_populates="messages")


class Memory(Base):
    """Долгосрочная память персонажа в рамках чата."""

    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    chat: Mapped["Chat"] = relationship(back_populates="memories")
    character: Mapped["Character"] = relationship(back_populates="memories")