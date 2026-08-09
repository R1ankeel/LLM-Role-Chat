"""Схемы WPE-восприятия (Sprint 3, decomposition-sprints.md §4).

Контракт данных Фазы 0: PerceptionResult (И13), Action[] (И14),
tool/JSON-Schema схема take_actions (§8 WPE.md).
"""

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from ..perception import parse_target_ids
from .message import CommunicationChannel

VisualLevel = Literal["full", "partial", "none"]
AudioLevel = Literal["full", "muffled", "none"]
RemoteStatus = Literal["none", "delivered"]
ActionType = Literal["move_to", "send_message"]


class PerceptionResult(BaseModel):
    """Двухканальный результат восприятия события наблюдателем (И13).

    Эфемерный объект (И8): ни текста, ни атрибуции говорящего. Каналы
    независимы: visual=full/audio=none (стекло), visual=none/audio=full
    (крик/звонок), audio=muffled (стена) — разные комбинации, не уровни
    одной шкалы. Возвращается чистой функцией `perception.perceive`.
    """

    visual_level: VisualLevel = "none"
    audio_level: AudioLevel = "none"
    addressed: bool = False
    remote_status: RemoteStatus = "none"


class Action(BaseModel):
    """Структурированное действие персонажа за ход (контракт данных, И14).

    `type` расширяем в данных, не в коде. Передача — только через native
    tools / structured outputs (§8); regex-парсинг JSON из сырого текста
    запрещён (И14). Отдельное поле `reply_target_character_ids` (Address
    Resolution, §3) живёт в `TurnOutput`.
    """

    type: ActionType
    location: Optional[str] = None  # move_to
    message: Optional[str] = None  # send_message
    channel: CommunicationChannel = "direct"
    target_character_ids: list[int] = Field(default_factory=list)

    @field_validator("target_character_ids", mode="before")
    @classmethod
    def _targets(cls, value: object) -> list[int]:
        return parse_target_ids(value)


class TurnOutput(BaseModel):
    """Структурированный выход хода персонажа (текст + действия) (Ул.4).

    Форма терминального tool-сообщения `take_actions`: адресация реплики
    отдельно от действий. Текст реплики приходит как content сообщения.
    """

    reply_target_character_ids: list[int] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)

    @field_validator("reply_target_character_ids", mode="before")
    @classmethod
    def _targets(cls, value: object) -> list[int]:
        return parse_target_ids(value)


def build_take_actions_tool() -> dict[str, Any]:
    """OpenAI-совместимая tool-схема `take_actions` (WPE.md §8, Ул.4)."""
    return {
        "type": "function",
        "function": {
            "name": "take_actions",
            "description": (
                "Действия персонажа в этом ходу (перемещение, отправка сообщения)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reply_target_character_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Кому адресована реплика (id персонажей).",
                    },
                    "actions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "enum": ["move_to", "send_message"],
                                },
                                "location": {"type": "string"},
                                "message": {"type": "string"},
                                "channel": {
                                    "type": "string",
                                    "enum": [
                                        "direct",
                                        "magic",
                                        "phone",
                                        "radio",
                                        "messenger",
                                    ],
                                },
                                "target_character_ids": {
                                    "type": "array",
                                    "items": {"type": "integer"},
                                },
                            },
                            "required": ["type"],
                        },
                    },
                },
                "required": ["reply_target_character_ids", "actions"],
            },
        },
    }


def build_take_actions_json_schema() -> dict[str, Any]:
    """JSON-Schema вариант той же схемы (Ollama `format` / OpenAI response_format)."""
    parameters = build_take_actions_tool()["function"]["parameters"]
    return {
        "type": "object",
        "properties": parameters["properties"],
        "required": parameters["required"],
        "additionalProperties": False,
    }
