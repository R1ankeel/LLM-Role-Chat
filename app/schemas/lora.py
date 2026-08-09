"""Схемы LoRA-адаптеров (Sprint 3, decomposition-sprints.md §4)."""

import json
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

LoRAAdapterFormat = Literal["gguf", "safetensors", "auto"]


class LoRAAdapterCreate(BaseModel):
    """Создание регистрации LoRA-адаптера (глобальный registry, §2.6).

    ``path`` — абсолютный путь к файлу пользователя; валидируется при
    create/update (абсолютный, существует, читаемый, валидный GGUF; §2.7).
    ``format`` — gguf | safetensors | auto; safetensors отклоняется
    (``supports_safetensors=false``, §2.5), auto нормализуется в фактический
    формат. ``base_model_identity`` — identity базовой модели (§2.3), nullable.
    """

    name: str = Field(min_length=1)
    path: str = Field(min_length=1)
    format: LoRAAdapterFormat = "auto"
    base_model: str = ""
    base_model_identity: Optional[str] = None
    enabled: bool = True
    description: str = ""
    source: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class LoRAAdapterUpdate(BaseModel):
    """Частичное обновление регистрации адаптера.

    При изменении ``path``/``format`` выполняется повторная валидация (§2.7) и
    пересчёт sha256. Пустой/невалидный путь → 422 (LoRAValidationError).
    """

    name: Optional[str] = None
    path: Optional[str] = None
    format: Optional[LoRAAdapterFormat] = None
    base_model: Optional[str] = None
    base_model_identity: Optional[str] = None
    enabled: Optional[bool] = None
    description: Optional[str] = None
    source: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


class LoRAAdapterRead(BaseModel):
    """Регистрация адаптера, возвращаемая API/UI.

    ``metadata`` хранится в БД как JSON-строка и отдаётся объектом;
    ``sha256`` — содержимое файла (blob-диджест, §2.2).
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    path: str
    format: str
    base_model: str = ""
    base_model_identity: Optional[str] = None
    enabled: bool
    description: str = ""
    source: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    sha256: str = ""
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _coerce_from_orm(cls, data: Any) -> Any:
        if isinstance(data, dict):
            payload = dict(data)
        else:
            # ORM-объект LoRAAdapter: атрибут `metadata_json` (колонка в БД —
            # `metadata`, зарезервировано в Declarative API) → `metadata`.
            payload = {
                "id": getattr(data, "id", None),
                "name": getattr(data, "name", None),
                "path": getattr(data, "path", None),
                "format": getattr(data, "format", None),
                "base_model": getattr(data, "base_model", "") or "",
                "base_model_identity": getattr(data, "base_model_identity", None),
                "enabled": getattr(data, "enabled", None),
                "description": getattr(data, "description", "") or "",
                "source": getattr(data, "source", "") or "",
                "metadata": getattr(data, "metadata_json", "{}") or "{}",
                "sha256": getattr(data, "sha256", "") or "",
                "created_at": getattr(data, "created_at", None),
                "updated_at": getattr(data, "updated_at", None),
            }
        return payload

    @field_validator("metadata", mode="before")
    @classmethod
    def _parse_metadata(cls, value: object) -> Any:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return {}
        if value is None:
            return {}
        return value


class ChatLoRAConfig(BaseModel):
    """Конфигурация LoRA одного чата (§2.6).

    Ровно один адаптер, без weight/order_index (MVP §2.5).
    ``enabled=true`` + ``adapter_id=null`` — допустимое состояние (§2.4):
    «LoRA включена, но адаптер не выбран»; пустая runtime-модель не создаётся.
    """

    enabled: bool
    adapter_id: Optional[int] = None
