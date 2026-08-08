"""Настройки World & Perception Engine 3.0 (Sprint 2, §4.9)."""

from pydantic import Field




class WpeSettings():
    """World & Perception Engine 3.0: флаги фаз-канареек."""

    # ----- World & Perception Engine 3.0 (Plans/WPE.md) -----
    # Все флаги по умолчанию выключены (Фаза 0 — фундамент без изменения
    # поведения). Каждая фаза включает свой флаг отдельным canary'ем.
    world_engine_locations_enabled: bool = Field(
        default=False, alias="WORLD_ENGINE_LOCATIONS_ENABLED"
    )
    world_engine_tools_enabled: bool = Field(
        default=False, alias="WORLD_ENGINE_TOOLS_ENABLED"
    )
    world_engine_events_enabled: bool = Field(
        default=False, alias="WORLD_ENGINE_EVENTS_ENABLED"
    )
    world_engine_perception_enabled: bool = Field(
        default=False, alias="WORLD_ENGINE_PERCEPTION_ENABLED"
    )
    world_engine_recency_tail_enabled: bool = Field(
        default=False, alias="WORLD_ENGINE_RECENCY_TAIL_ENABLED"
    )
    world_engine_actions_enabled: bool = Field(
        default=False, alias="WORLD_ENGINE_ACTIONS_ENABLED"
    )
    wpe_action_consistency_max_retries: int = Field(
        default=1, alias="WORLD_ENGINE_CONSISTENCY_MAX_RETRIES"
    )
    world_engine_threads_enabled: bool = Field(
        default=False, alias="WORLD_ENGINE_THREADS_ENABLED"
    )
    world_engine_partial_perception_enabled: bool = Field(
        default=False, alias="WORLD_ENGINE_PARTIAL_PERCEPTION_ENABLED"
    )
    world_engine_event_bus_enabled: bool = Field(
        default=False, alias="WORLD_ENGINE_EVENT_BUS_ENABLED"
    )
