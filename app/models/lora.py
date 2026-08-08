"""LoRA адаптеры и связки «чат → адаптер» (Sprint 2, decomposition-sprints.md §3)."""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base


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
