"""SensorsService — единый интерфейс аналитических задач (Plans/update20.md §5.1).

Отдельная **Sensors Model** (``SENSORS_MODEL`` из ``.env``) — маленькая LLM для
быстрых фоновых задач (perception/event/emotion/memory/relationship). Это
аналитический слой, а НЕ источник истины:

- возвращает структурированное **предложение** (JSON по схеме задачи);
- НЕ пишет в БД и НЕ меняет состояние;
- итоговое изменение всегда выполняет движок по своим правилам/gates/caps.

Интерфейс (§5.1.2): ``task → build_prompt → invoke → validate → return``.

Graceful degradation (§5.1.8): при пустом ``SENSORS_MODEL``, отключённом флаге,
timeout, ошибке запроса или некорректном JSON — ``run`` возвращает ``None`` и
основной игровой цикл не падает.

Sprint 0: заведена как фундамент, НЕ подключена ни к одному процессу.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from .config import settings
from .sensors.schemas import get_schema, validate_sensor_result

logger = logging.getLogger(__name__)

SENSOR_SYSTEM_PROMPT = (
    "Ты — SensorsModel, лёгкий аналитический слой игрового движка. "
    "Ты предлагаешь структурированные результаты для фоновых задач. "
    "Возвращай только валидный JSON по заданной схеме, без пояснений "
    "и без markdown-обёрток."
)

# Краткие инструкции для каждой задачи (минимальный контекст, без дублирования
# всего context window основной модели — §5.1.7).
_TASK_INSTRUCTIONS: dict[str, str] = {
    "perception": (
        "Определи, что персонаж ПОТЕНЦИАЛЬНО может заметить в событии: "
        "визуально (potential_visual), звуком (potential_audio), обращением "
        "(addressed), краткое замечание (notice) и значимость для него "
        "(significance 0..1). Это только предложение — окончательную доступность "
        "информации решает движок (perceive/presence)."
    ),
    "event": (
        "Классифицируй событие: тип (event_type), источник (source_character, "
        "может быть null), участники (targets), важность (importance 0..10), "
        "слышимость (audibility: none|muffled|full), видимость "
        "(visibility: none|partial|full), нужна ли дальнейшая обработка "
        "(requires_processing). Это предложение — запись и салиенс решает движок."
    ),
    "emotion": (
        "Предложи эмоцию персонажа после события: emotion, интенсивность "
        "(intensity 0..1), уверенность (confidence 0..1), сдвиг настроения "
        "(mood_delta -1..1, опционально). Это предложение — движок применит его "
        "только в рамках caps и правил."
    ),
    "memory": (
        "Предложи до 3 кандидатов фактов для памяти персонажа: список facts "
        "[{text, importance 0..1}]. Кандидаты пройдут валидацию движка "
        "(grounding, witness, лимиты) перед записью."
    ),
    "relationship": (
        "Оцени, как событие меняет метрики отношения source→target: дельты "
        "affection/trust/resentment/jealousy/attraction в диапазоне [-20,20]. "
        "Это предложение — движок применит evidence gating, caps и нормализацию."
    ),
}


# num_ctx / num_predict на каждую sensor-задачу (default из .env). Задачи, не
# перечисленные здесь (например ``event``), запрашиваются без этих параметров.
_SENSOR_RUNTIME_OPTIONS: dict[str, tuple[str, str]] = {
    "perception": ("sensors_perception_num_ctx", "sensors_perception_num_predict"),
    "emotion": ("sensors_emotion_num_ctx", "sensors_emotion_num_predict"),
    "memory": ("sensors_memory_num_ctx", "sensors_memory_num_predict"),
    "relationship": ("sensors_relationship_num_ctx", "sensors_relationship_num_predict"),
}


def _parse_sensor_json(content: str) -> Any:
    """Распарсить JSON из ответа Sensors (без markdown-обёрток)."""
    text = content.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fence:
        text = fence.group(1).strip()
    return json.loads(text)


class SensorsService:
    """Единый интерфейс sensor-запросов (§5.1.2)."""

    def __init__(self, model: str | None = None, enabled: bool | None = None):
        self.model = (model if model is not None else settings.sensors_model) or ""
        self.enabled = enabled if enabled is not None else settings.sensors_enabled

    # ------------------------------ включённость ------------------------------
    def is_enabled(self, task: str) -> bool:
        """Задача активна только при: ``SENSORS_MODEL`` задан + мастер-флаг
        ``sensors_enabled`` + per-task флаг ``sensors_<task>_enabled``."""
        if not self.model or not self.enabled:
            return False
        return bool(getattr(settings, f"sensors_{task}_enabled", False))

    # ------------------------------ prompt ------------------------------
    def build_prompt(
        self, task: str, minimal_context: str, current_state: str = ""
    ) -> list[dict[str, str]]:
        """Короткий специализированный prompt для sensor-задачи.

        ``minimal_context`` — только минимально необходимый контекст (реплика,
        событие, нужные персонажи, локация). ``current_state`` — текущее
        состояние (например метрики отношения), если нужно.
        """
        instruction = _TASK_INSTRUCTIONS.get(task, "Верни валидный JSON по схеме задачи.")
        parts = [instruction]
        if current_state:
            parts.append(f"Текущее состояние:\n{current_state}")
        parts.append(f"Контекст:\n{minimal_context}")
        parts.append(
            "Ответ — только валидный JSON, строго соответствующий описанной структуре."
        )
        return [
            {"role": "system", "content": SENSOR_SYSTEM_PROMPT},
            {"role": "user", "content": "\n\n".join(parts)},
        ]

    # ------------------------------ invoke ------------------------------
    @staticmethod
    def _task_runtime_options(task: str) -> dict[str, int] | None:
        """num_ctx/num_predict задачи из .env (default), None — нет кастома."""
        names = _SENSOR_RUNTIME_OPTIONS.get(task)
        if names is None:
            return None
        ctx = getattr(settings, names[0], None)
        predict = getattr(settings, names[1], None)
        if not ctx and not predict:
            return None
        return {"num_ctx": ctx or None, "num_predict": predict or None}

    async def invoke(
        self,
        client: Any,
        *,
        task: str,
        minimal_context: str,
        current_state: str = "",
        temperature: float = 0.3,
    ) -> str | None:
        """Вызов Sensors-модели через существующий Ollama-клиент (§5.1.1).

        Использует ``SENSORS_MODEL`` (не основную модель генерации). При
        недоступности/timeout/ошибке возвращает None — цикл не падает.
        """
        from . import ollama_client  # локальный импорт: избегаем циклической связи

        if not self.model:
            return None
        schema = get_schema(task)
        if schema is None:
            logger.warning("Sensors: неизвестная задача %r — пропуск", task)
            return None

        messages = self.build_prompt(task, minimal_context, current_state)
        runtime = self._task_runtime_options(task) or {}
        num_ctx = runtime.get("num_ctx")
        num_predict = runtime.get("num_predict")
        try:
            if settings.use_chat_api:
                payload = ollama_client._build_chat_payload(
                    self.model,
                    messages,
                    temperature,
                    [],
                    stream=False,
                    enable_thinking=False,
                    num_ctx=num_ctx,
                    num_predict=num_predict,
                    format_schema=schema,
                )
                async with ollama_client.llm_request(self.model, "/api/chat"):
                    response = await asyncio.wait_for(
                        client.post("/api/chat", json=payload),
                        timeout=settings.sensors_timeout,
                    )
                response.raise_for_status()
                data = response.json()
                return data.get("message", {}).get("content", "") or None
            prompt = "\n\n".join(m["content"] for m in messages if m.get("content"))
            payload = ollama_client._build_generate_payload(
                self.model,
                prompt,
                temperature,
                [],
                stream=False,
                enable_thinking=False,
                num_ctx=num_ctx,
                num_predict=num_predict,
                format_schema=schema,
            )
            async with ollama_client.llm_request(self.model, "/api/generate"):
                response = await asyncio.wait_for(
                    client.post("/api/generate", json=payload),
                    timeout=settings.sensors_timeout,
                )
            response.raise_for_status()
            data = response.json()
            return data.get("response", "") or None
        except asyncio.TimeoutError:
            logger.warning("Sensors: timeout задачи %r (model=%s)", task, self.model)
            return None
        except Exception as exc:  # noqa: BLE001 — любой сбой не должен ронять цикл
            logger.warning("Sensors: сбой задачи %r (model=%s): %s", task, self.model, exc)
            return None

    # ------------------------------ validate ------------------------------
    @staticmethod
    def validate(result: Any, schema: dict[str, Any] | None) -> dict[str, Any] | None:
        """JSON-schema валидация результата (§5.1.6). None при невалидности."""
        return validate_sensor_result(result, schema)

    # ------------------------------ полный pipeline ------------------------------
    async def run(
        self,
        client: Any,
        *,
        task: str,
        minimal_context: str,
        current_state: str = "",
        temperature: float = 0.3,
    ) -> dict[str, Any] | None:
        """task → prompt → invoke → validate → return (или None).

        Sensors не пишет в БД и не меняет состояние — только возвращает
        валидированное предложение для движка. При любой ошибке — None.
        """
        if not self.is_enabled(task):
            return None
        schema = get_schema(task)
        if schema is None:
            return None

        started = time.monotonic()
        content = await self.invoke(
            client,
            task=task,
            minimal_context=minimal_context,
            current_state=current_state,
            temperature=temperature,
        )
        if content is None:
            return None
        try:
            result = _parse_sensor_json(content)
        except (json.JSONDecodeError, TypeError, ValueError):
            logger.warning("Sensors: некорректный JSON задачи %r — fallback", task)
            return None

        validated = self.validate(result, schema)
        elapsed = time.monotonic() - started
        if validated is None:
            logger.info(
                "Sensors: задача %r (model=%s) — результат не прошёл валидацию, "
                "fallback (%.2fs)",
                task,
                self.model,
                elapsed,
            )
            return None
        logger.info(
            "Sensors: задача %r (model=%s) — OK (%.2fs)",
            task,
            self.model,
            elapsed,
        )
        return validated


# Единственный инстанс слоя (использует настройки из `.env`). Sprint 0:
# заведён, ни к одному процессу не подключён.
sensors_service = SensorsService()
