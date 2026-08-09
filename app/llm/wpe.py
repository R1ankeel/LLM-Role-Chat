"""WPE tool-calling: tool-mode chain, TurnOutput-парсинг (Sprint 5A, §4.3).

Перенесено 1:1 из ``app/ollama_client.py`` (диапазон §4.3: 114–288): режимы
тулов (tools/format), кэш возможностей модели, shadow-метрики Фазы 2,
``_parse_tool_calls``/``_parse_turn_output_json``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..config import settings
from .. import schemas
from .tasks import _extract_json_payload

logger = logging.getLogger(__name__)

# model_name -> "tools" | "format" | "text" (лучший поддерживаемый режим)
_MODEL_TOOL_MODE_CACHE: dict[str, str] = {}

WPE_TOOLS_STATS: dict[str, Any] = {
    "calls": 0,
    "by_mode": {},
    "schema_valid": 0,
    "with_move_to": 0,
    "with_send_message": 0,
    "with_addressing": 0,
    "latency_ms": [],
}


def _tool_mode_chain(model_name: str, preferred: str) -> list[str]:
    """Порядок попыток для модели: tools → format (§8, И14), с кэшем (§12).

    Фаза 8: deprecated text-only fallback удалён — при запросе
    структурированных действий (preferred tools/format) генерация больше не
    деградирует к тексту. ``preferred="text"`` остаётся только для обычного
    (нетools) пути генерации, где tools/format не запрашивались.
    """
    cached = _MODEL_TOOL_MODE_CACHE.get(model_name)
    if cached == "tools":
        return ["tools"]
    if cached == "format":
        return ["format"]
    return {
        "tools": ["tools", "format"],
        "format": ["format"],
        "text": ["text"],
    }.get(preferred, ["text"])


def _next_tool_mode(model_name: str, current: str, wants_format: bool) -> str:
    """Понизить режим tools→format и запомнить в кэш (после 400 от Ollama).

    Фаза 8: text-only fallback удалён (И14). Если структурированный режим
    недоступен и дальнейшего фоллбэка нет — выбрасывается RuntimeError:
    модель обязана поддерживать tools или format при включённом флаге tools.
    """
    if current == "tools":
        if not wants_format:
            raise RuntimeError(
                f"Модель {model_name} не поддерживает tools, а format не "
                "запрошен: структурированные действия обязательны (И14, Фаза 8)"
            )
        nxt = "format"
    elif current == "format":
        raise RuntimeError(
            f"Модель {model_name} не поддерживает format: структурированные "
            "действия обязательны (И14, Фаза 8)"
        )
    else:
        nxt = current
    _MODEL_TOOL_MODE_CACHE[model_name] = nxt
    return nxt


def _tools_unsupported_error(body: str) -> bool:
    lowered = body.lower()
    return "tool" in lowered and any(
        k in lowered for k in ("not support", "unsupported", "unknown field", "no tool")
    )


def _format_unsupported_error(body: str) -> bool:
    lowered = body.lower()
    if "format" in lowered and any(
        k in lowered for k in ("not support", "unsupported", "unknown field")
    ):
        return True
    return "failed to parse" in lowered or "unexpected json" in lowered


def wpe_tools_stats_snapshot() -> dict[str, Any]:
    """Снимок shadow-метрик WPE Фазы 2 для canary-измерений (§10, §12)."""
    lats = list(WPE_TOOLS_STATS["latency_ms"])
    return {
        "calls": WPE_TOOLS_STATS["calls"],
        "by_mode": dict(WPE_TOOLS_STATS["by_mode"]),
        "schema_valid": WPE_TOOLS_STATS["schema_valid"],
        "with_move_to": WPE_TOOLS_STATS["with_move_to"],
        "with_send_message": WPE_TOOLS_STATS["with_send_message"],
        "with_addressing": WPE_TOOLS_STATS["with_addressing"],
        "latency_ms": lats,
        "latency_avg_ms": sum(lats) / len(lats) if lats else 0.0,
        "latency_max_ms": max(lats) if lats else 0.0,
    }


def _record_shadow_turn(
    chat_id: int,
    character_name: str,
    mode: str,
    turn_output: schemas.TurnOutput | None,
    latency_ms: float,
) -> None:
    """Логирует и накапливает shadow-результат хода (Фаза 2). Действия не применяются."""
    stats = WPE_TOOLS_STATS
    stats["calls"] += 1
    stats["by_mode"][mode] = stats["by_mode"].get(mode, 0) + 1
    stats["latency_ms"].append(latency_ms)

    if turn_output is None:
        logger.warning(
            "[WPE-P2] shadow chat_id=%d character=%s mode=%s: схема-невалидно/нет "
            "tool_calls, действия не извлечены (латентность %.1f ms)",
            chat_id,
            character_name,
            mode,
            latency_ms,
        )
        return

    stats["schema_valid"] += 1
    actions = turn_output.actions
    targets = turn_output.reply_target_character_ids
    if any(a.type == "move_to" for a in actions):
        stats["with_move_to"] += 1
    if any(a.type == "send_message" for a in actions):
        stats["with_send_message"] += 1
    if targets:
        stats["with_addressing"] += 1

    logger.info(
        "[WPE-P2] shadow chat_id=%d character=%s mode=%s schema_valid=yes "
        "targets=%s actions=%s (латентность %.1f ms)",
        chat_id,
        character_name,
        mode,
        targets,
        [a.model_dump(exclude_none=True) for a in actions],
        latency_ms,
    )


def _parse_tool_calls(raw: list) -> schemas.TurnOutput | None:
    """Разобрать `message.tool_calls` (Ollama chat) в TurnOutput. И14: только нативно."""
    for call in raw or []:
        fn = call.get("function") if isinstance(call, dict) else None
        if not isinstance(fn, dict) or fn.get("name") != "take_actions":
            continue
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                continue
        if isinstance(args, dict):
            try:
                return schemas.TurnOutput.model_validate(args)
            except Exception as exc:
                logger.warning("[WPE-P2] невалидные take_actions аргументы: %s (%s)", args, exc)
                return None
    return None


def _parse_turn_output_json(text: str) -> schemas.TurnOutput | None:
    """Разобрать JSON-ответ формат-пути (Ollama `format` / response_format) в TurnOutput."""
    payload = _extract_json_payload(text)
    if not isinstance(payload, dict):
        return None
    try:
        return schemas.TurnOutput.model_validate(payload)
    except Exception as exc:
        logger.warning("[WPE-P2] невалидный take_actions JSON: %s (%s)", payload, exc)
        return None
