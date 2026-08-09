"""LoRA: выбор модели для ОСНОВНОЙ генерации (Plans/LoRA.md, Sprint 3).

Вынесено из ``app/chat_engine.py`` (Milestone 5B, decomposition.md §4.2).

LoRA применяется ТОЛЬКО к основному ответу персонажа. Служебные LLM-вызовы
(scene state, post_round_pipeline, память, отношения, сенсоры, consolidation,
crisis) вызываются без этого хелпера и получают ``chat.model_name`` как раньше.
"""

import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ..lora_manager import CompatibilityStatus, LoRAManager, ResolveResult

logger = logging.getLogger("app.chat_engine.pipeline.lora")

_LORA_MANAGER_DEFAULT: LoRAManager | None = None

# In-process набор чатов, для которых уже отправлено предупреждение о первом
# применении LoRA со статусом Unknown (§2.3, Sprint 3 задача 5).
_lora_unknown_warned_chats: set[int] = set()


def _default_lora_manager() -> LoRAManager:
    """Лениво созданный LoRAManager по умолчанию.

    В продакшене менеджер живёт в ``app.state.lora_manager`` и передаётся
    из роутера; этот дефолт нужен для прямых вызовов (tests/legacy-хелперы).
    """
    global _LORA_MANAGER_DEFAULT
    if _LORA_MANAGER_DEFAULT is None:
        _LORA_MANAGER_DEFAULT = LoRAManager()
    return _LORA_MANAGER_DEFAULT


async def resolve_generation_model(
    db: AsyncSession,
    client: httpx.AsyncClient,
    chat,
    lora_manager: LoRAManager | None = None,
) -> tuple[str, ResolveResult]:
    """Выбрать модель для ОСНОВНОЙ генерации ответа персонажа (Sprint 3).

    Делегирует ``LoRAManager.resolve`` (семантика ``lora_enabled``, §2.4):
    - ``lora_enabled=false`` или без выбранного адаптера → ``chat.model_name``
      — поведение идентично текущему (критерий готовности Sprint 3);
    - ``lora_enabled=true`` + адаптер → runtime-модель LoRA.

    Ошибки LoRA (Incompatible, пропавший файл адаптера, битая конфигурация,
    недоступный runtime) поднимаются ДО начала генерации (``RuntimeError``);
    конфигурация чата при этом не изменяется (§7). НЕ silent fallback.
    """
    manager = lora_manager if lora_manager is not None else _default_lora_manager()
    return await manager.resolve(db, client, chat)


def lora_first_apply_warning(chat_id: int, info: ResolveResult) -> dict | None:
    """Предупреждение клиенту при ПЕРВОМ применении LoRA со статусом Unknown.

    Возвращает SSE-событие ``{"type": "lora_warning", ...}`` один раз на чат
    (в рамках процесса). Это НЕ silent fallback: runtime-модель уже выбрана и
    применяется, предупреждение информирует/запрашивает подтверждение (§2.3).
    Для Compatible события нет; Incompatible до этого места не доходит
    (``RuntimeError`` из ``resolve``).
    """
    if not getattr(info, "runtime_used", False):
        return None
    compat = getattr(info, "compatibility", None)
    if compat is None or compat.status is not CompatibilityStatus.UNKNOWN:
        return None
    if chat_id in _lora_unknown_warned_chats:
        return None
    _lora_unknown_warned_chats.add(chat_id)
    logger.warning(
        "LoRA first-apply compatibility Unknown (chat_id=%s): %s — предупреждение "
        "клиенту с подтверждением, НЕ silent fallback (§2.3)",
        chat_id,
        compat.detail,
    )
    return {
        "type": "lora_warning",
        "kind": "compatibility_unknown",
        "detail": (
            "LoRA применяется, но совместимость адаптера с базовой моделью "
            f"не подтверждена: {compat.detail}. Убедитесь, что адаптер "
            "предназначен для модели "
            f"{compat.base_identity or 'базовой модели чата'}."
        ),
    }
