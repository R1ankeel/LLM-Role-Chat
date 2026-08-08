"""Конфигурация приложения (Sprint 2, §4.9 decomposition.md).

Заменяет монолит ``app/config.py``: набор полей разбит на доменные миксины,
композиция — через ``SettingsBase`` + миксины. Синглтон ``settings`` и доступ
``settings.<attr>`` сохранены — потребители не меняются.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config.avatar import AvatarSettings
from app.config.context import ContextSettings
from app.config.core import SettingsBase
from app.config.memory import MemorySettings
from app.config.relationships import RelationshipSettings
from app.config.repetition import RepetitionSettings
from app.config.sensors import SensorsSettings
from app.config.story import StorySettings
from app.config.task_queue import TaskQueueSettings
from app.config.wpe import WpeSettings


class Settings(
    SettingsBase,
    MemorySettings,
    ContextSettings,
    RelationshipSettings,
    RepetitionSettings,
    WpeSettings,
    StorySettings,
    SensorsSettings,
    TaskQueueSettings,
    AvatarSettings,
):
    """Полный набор настроек приложения."""


settings = Settings()
