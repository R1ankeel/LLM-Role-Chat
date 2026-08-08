"""Настройки аватаров персонажей (Sprint 2, §4.9)."""

from pydantic import Field




class AvatarSettings():
    """Character avatars: директория, лимиты, допустимые форматы."""

    # Character avatars (docs/Profile.docx; upload service is Этап B)
    avatar_dir: str = Field(default="app/static/avatars", alias="AVATAR_DIR")
    avatar_max_size_mb: int = Field(default=5, alias="AVATAR_MAX_SIZE_MB")
    avatar_max_dimension: int = Field(default=512, alias="AVATAR_MAX_DIMENSION")
    # Допустимые форматы (проверка по magic-байтам). Константа в коде — как
    # event_visibilities/memory_categories, не читается из env.
    avatar_allowed_types: tuple[str, ...] = ("png", "jpeg", "webp")
