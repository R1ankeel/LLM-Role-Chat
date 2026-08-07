"""Runtime-слой LoRA (Plans/LoRA.md, Sprint 2).

Создание/кэширование/проверка runtime-моделей Ollama через HTTP API (без shell).

Состав:
- ``RuntimeCapabilities`` / ``check_capabilities`` — флаги возможностей runtime
  (§2.5): ``supports_lora=true``, ``supports_safetensors=false`` (подтверждены
  Sprint 0), проверка доступности сервера.
- ``validate`` — проверка пути (§2.7) + compatibility check по
  ``base_model_identity`` (§2.3) → ``Compatible / Incompatible / Unknown``.
  ``Unknown`` → предупреждение/подтверждение, НЕ silent fallback и НЕ блокировка.
- ``runtime_key`` / ``runtime_name`` — детерминированный ключ конфигурации (§2.2)
  и имя runtime-модели ``{slug(base)}-lora-{hash8}``.
- ``LoRAManager.resolve`` — семантика ``lora_enabled`` (§2.4) → выбранная
  модель для генерации.
- ``LoRAManager.ensure_runtime_model`` — кэш ``key → exists``; при промахе —
  создание; блокировка повторного создания (lock). Максимум 1× ``POST
  /api/create`` на конфигурацию. НИКАКОГО ``cleanup()``/GC (удалено из MVP,
  §2.7): runtime-модели остаются в Ollama.

Ошибки несовместимости/невозможности создать runtime-модель → ``RuntimeError``
с текстом (не silent fallback, §7).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import weakref
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from . import crud, ollama_client
from .lora_validation import LoRAValidationError, validate_adapter_path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Возможности runtime (§2.5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuntimeCapabilities:
    """Флаги возможностей Ollama runtime, подтверждённые Sprint 0.

    MVP (§2.5): ровно один адаптер на runtime-модель, weight/scale отсутствует,
    только GGUF (``supports_safetensors=false``). Флаги мульти-LoRA и весов
    исключены как нерелевантные для MVP.
    """

    supports_lora: bool = True
    supports_safetensors: bool = False


async def check_capabilities(client) -> RuntimeCapabilities:
    """Проверка доступности Ollama + capability-флаги (см. ollama_client)."""
    return await ollama_client.check_capabilities(client)


# ---------------------------------------------------------------------------
# Совместимость по base_model_identity (§2.3)
# ---------------------------------------------------------------------------


class CompatibilityStatus(str, Enum):
    """Результат compatibility check по identity базовой модели (§2.3)."""

    COMPATIBLE = "Compatible"
    INCOMPATIBLE = "Incompatible"
    UNKNOWN = "Unknown"


@dataclass(frozen=True)
class CompatibilityResult:
    """Результат проверки совместимости адаптера с базовой моделью чата.

    ``*_identity_source``: ``explicit`` (задано явно — высокододоверенная
    идентичность), ``model_name`` (fallback на ``chat.model_name`` — низкая
    уверенность), ``auto`` (автоопределение по метаданным/имени файла), либо
    пустая строка (не определена).
    """

    status: CompatibilityStatus
    adapter_identity: str | None = None
    base_identity: str | None = None
    adapter_identity_source: str = ""
    base_identity_source: str = ""
    detail: str = ""


def _normalize_identity(value: str) -> str:
    """Нормализация identity для сравнения: регистр + разделители → пробел."""
    return re.sub(r"[:/_-]+", " ", value.strip().lower())


def _adapter_identity(adapter) -> tuple[str | None, str]:
    """Identity адаптера: явное поле → попытка автоопределения → None (§2.3).

    Возвращает ``(identity, source)``: ``explicit`` | ``auto`` | ``""``.
    """
    explicit = getattr(adapter, "base_model_identity", None)
    if explicit and str(explicit).strip():
        return str(explicit).strip(), "explicit"
    guess = _auto_detect_adapter_identity(adapter)
    if guess:
        return guess, "auto"
    return None, ""


def _auto_detect_adapter_identity(adapter) -> str | None:
    """Низкодоверенное автоопределение identity адаптера (§2.3, п.1).

    Порядок: метаданные регистрации (``base_model_identity``/``base_model``/
    ``model``) → поле ``base_model`` registry → имя файла (без расширения).
    """
    raw_meta = getattr(adapter, "metadata_json", None)
    if isinstance(raw_meta, str) and raw_meta.strip():
        try:
            meta = json.loads(raw_meta)
        except (json.JSONDecodeError, TypeError):
            meta = {}
    else:
        meta = {}
    if isinstance(meta, dict):
        for key in ("base_model_identity", "base_model", "model"):
            value = meta.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    base_model = getattr(adapter, "base_model", "") or ""
    if base_model.strip():
        return base_model.strip()
    path = getattr(adapter, "path", "") or ""
    stem = os.path.splitext(os.path.basename(path))[0]
    if stem:
        return stem
    return None


def _chat_base_identity(chat) -> tuple[str | None, str]:
    """Identity базовой модели чата (§2.3, п.2).

    Явное ``Chat.base_model_identity`` → ``explicit``; иначе низкодоверенная
    fallback на ``chat.model_name`` → ``model_name`` (в MVP поле обычно не
    задано, поэтому результат сравнения — ``Unknown``, а не блокирующее
    ``Incompatible``).
    """
    explicit = getattr(chat, "base_model_identity", None)
    if explicit and str(explicit).strip():
        return str(explicit).strip(), "explicit"
    model_name = getattr(chat, "model_name", "") or ""
    if model_name:
        return model_name, "model_name"
    return None, ""


def check_compatibility(adapter, chat=None) -> CompatibilityResult:
    """Compatibility check по ``base_model_identity`` (§2.3).

    - обе идентичности определены с высокой уверенностью и совпадают →
      ``Compatible``;
    - определены и не совпадают → ``Incompatible`` (блокировка);
    - хотя бы одна не определена / определена с низкой уверенностью →
      ``Unknown`` (НЕ блокируем автоматически, предупреждение; НЕ silent
      fallback).

    Важно: локальное имя ``chat.model_name`` (``goetia-26b``) НЕ сравнивается
    строковым ``==`` с HF-идентификатором адаптера (``Naphula/Goetia-...``) —
    в этом случае результат ``Unknown``, runtime-модель всё равно создаётся.
    """
    adapter_identity, adapter_source = _adapter_identity(adapter)
    base_identity, base_source = (
        _chat_base_identity(chat) if chat is not None else (None, "")
    )

    if not adapter_identity or not base_identity:
        return CompatibilityResult(
            status=CompatibilityStatus.UNKNOWN,
            adapter_identity=adapter_identity,
            base_identity=base_identity,
            adapter_identity_source=adapter_source,
            base_identity_source=base_source,
            detail="одна из идентичностей базовой модели не определена",
        )
    if adapter_source != "explicit" or base_source != "explicit":
        return CompatibilityResult(
            status=CompatibilityStatus.UNKNOWN,
            adapter_identity=adapter_identity,
            base_identity=base_identity,
            adapter_identity_source=adapter_source,
            base_identity_source=base_source,
            detail=(
                "идентичность базовой модели определена с низкой уверенностью "
                "(fallback на имя модели / автоопределение)"
            ),
        )
    if _normalize_identity(adapter_identity) == _normalize_identity(base_identity):
        return CompatibilityResult(
            status=CompatibilityStatus.COMPATIBLE,
            adapter_identity=adapter_identity,
            base_identity=base_identity,
            adapter_identity_source=adapter_source,
            base_identity_source=base_source,
            detail="идентичности базовых моделей совпадают",
        )
    return CompatibilityResult(
        status=CompatibilityStatus.INCOMPATIBLE,
        adapter_identity=adapter_identity,
        base_identity=base_identity,
        adapter_identity_source=adapter_source,
        base_identity_source=base_source,
        detail="идентичности базовых моделей не совпадают",
    )


@dataclass
class ValidationResult:
    """Результат ``validate``: путь (§2.7) + compatibility (§2.3)."""

    path_ok: bool
    path_error: str = ""
    compatibility: CompatibilityResult | None = None


def validate(adapter, chat=None) -> ValidationResult:
    """Проверка пути адаптера (§2.7) + compatibility check (§2.3).

    Ошибки пути возвращаются в ``ValidationResult`` (не бросаются) — это
    валидация, а не исключение. Бросает только на неожиданных проблемах.
    """
    try:
        validate_adapter_path(adapter.path, adapter.format, with_sha256=False)
    except LoRAValidationError as exc:
        return ValidationResult(path_ok=False, path_error=str(exc))
    return ValidationResult(
        path_ok=True,
        compatibility=check_compatibility(adapter, chat) if chat is not None else None,
    )


# ---------------------------------------------------------------------------
# Runtime key / name (§2.2)
# ---------------------------------------------------------------------------


def runtime_key(*, base_identity: str, adapter_id: int, file_sha256: str) -> str:
    """Детерминированный sha256 ключ конфигурации (§2.2).

    ``base_model_identity + adapter_id + sha256 содержимого файла``. Любое
    изменение (другой адаптер / другой файл адаптера) → новый ключ →
    новая runtime-модель. Weight/order в ключ НЕ входят (их нет в MVP, §2.5).
    """
    material = (
        f"{base_identity or ''}\x00{int(adapter_id)}\x00{file_sha256 or ''}"
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def runtime_name(base_model: str, key: str) -> str:
    """Имя runtime-модели: ``{slug(base)}-lora-{hash8}``."""
    return f"{_slugify(base_model)}-lora-{key[:8]}"


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or "model"


# ---------------------------------------------------------------------------
# LoRAManager: resolve + ensure_runtime_model
# ---------------------------------------------------------------------------


@dataclass
class ResolveResult:
    """Информация о результате ``LoRAManager.resolve``.

    ``model_name`` — модель для генерации; ``runtime_used`` — используется ли
    runtime-модель (LoRA реально активна); ``runtime_name``/``runtime_key`` —
    имя/ключ runtime-модели; ``compatibility`` — результат compatibility check;
    ``created`` — была ли runtime-модель создана в этом вызове.
    """

    model_name: str
    runtime_used: bool = False
    runtime_name: str | None = None
    runtime_key: str | None = None
    adapter_id: int | None = None
    compatibility: CompatibilityResult | None = None
    created: bool = False
    cache_hit: bool = field(default=False, repr=False)


class LoRAManager:
    """Runtime-слой LoRA: resolve по семантике ``lora_enabled`` (§2.4).

    Инстанс живёт в ``app.state.lora_manager`` (создаётся на lifespan);
    в тестах создаётся свежий ``LoRAManager()`` на тест (кэш per-instance).
    """

    def __init__(self) -> None:
        # in-memory кэш key → runtime_name (§2.2): на промахе сверяемся с
        # GET /api/tags (list_models), повторный create не выполняется.
        self._exists_cache: dict[str, str] = {}
        self._capabilities: RuntimeCapabilities | None = None
        # per-event-loop lock (pytest-asyncio создаёт loop на тест)
        self._locks: "weakref.WeakKeyDictionary[Any, asyncio.Lock]" = (
            weakref.WeakKeyDictionary()
        )

    def _create_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        lock = self._locks.get(loop)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[loop] = lock
        return lock

    async def check_capabilities(self, client) -> RuntimeCapabilities:
        """Capability-флаги с кэшированием на инстансе (1 запрос на процесс)."""
        if self._capabilities is not None:
            return self._capabilities
        self._capabilities = await ollama_client.check_capabilities(client)
        logger.debug("LoRA runtime capabilities: %s", self._capabilities)
        return self._capabilities

    async def resolve(
        self, db, client, chat
    ) -> tuple[str, ResolveResult]:
        """Выбрать модель для генерации по семантике ``lora_enabled`` (§2.4).

        - ``enabled=false`` → ``chat.model_name``;
        - ``enabled=true`` + адаптер не выбран → ``chat.model_name``,
          runtime-модель НЕ создаётся;
        - ``enabled=true`` + 1 адаптер → compatibility check → runtime-модель
          (compat check ``Incompatible`` → ``RuntimeError``; ``Unknown`` →
          предупреждение, не блокирует; несовместимый/недоступный runtime →
          ``RuntimeError``, не silent fallback).

        Возвращает ``(model_name, ResolveResult)``.
        """
        model_name = getattr(chat, "model_name", "") or ""
        result = ResolveResult(model_name=model_name)

        if not bool(getattr(chat, "lora_enabled", False)):
            logger.debug(
                "LoRA disabled chat_id=%s → base model %r",
                getattr(chat, "id", "?"),
                model_name,
            )
            return model_name, result

        link = await crud.get_chat_lora_adapter(db, chat.id)
        if link is None:
            # enabled=true + адаптер не выбран — допустимое состояние (§2.4):
            # пустая runtime-модель НЕ создаётся, генерация на chat.model_name.
            logger.warning(
                "LoRA enabled chat_id=%s, но адаптер не выбран → base model "
                "%r (runtime-модель НЕ создаётся, §2.4)",
                chat.id,
                model_name,
            )
            return model_name, result

        adapter = await crud.get_lora_adapter(db, link.adapter_id)
        if adapter is None:
            raise RuntimeError(
                "Конфигурация LoRA чата ссылается на несуществующий адаптер "
                f"id={link.adapter_id} (chat_id={chat.id})"
            )
        if not bool(getattr(adapter, "enabled", True)):
            logger.warning(
                "LoRA-адаптер '%s' отключён (enabled=false), но выбран чатом %s",
                adapter.name,
                chat.id,
            )

        compatibility = check_compatibility(adapter, chat)
        result.compatibility = compatibility
        if compatibility.status is CompatibilityStatus.INCOMPATIBLE:
            raise RuntimeError(
                f"LoRA-адаптер '{adapter.name}' несовместим с базовой моделью "
                f"чата: {compatibility.detail} "
                f"(identity адаптера={compatibility.adapter_identity!r}, "
                f"identity базовой={compatibility.base_identity!r})"
            )
        if compatibility.status is CompatibilityStatus.UNKNOWN:
            logger.warning(
                "LoRA compatibility Unknown (adapter=%s): %s — предупреждение, "
                "не блокировка и не silent fallback (§2.3)",
                adapter.name,
                compatibility.detail,
            )

        caps = await self.check_capabilities(client)
        if not caps.supports_lora:
            raise RuntimeError(
                "Ollama runtime не поддерживает LoRA (supports_lora=false); "
                "runtime-модель не создана"
            )

        base_identity, _ = _chat_base_identity(chat)
        key = runtime_key(
            base_identity=base_identity or "",
            adapter_id=adapter.id,
            file_sha256=adapter.sha256,
        )
        name = runtime_name(model_name, key)
        created = await self.ensure_runtime_model(
            client, adapter=adapter, key=key, name=name, base_model=model_name
        )
        result.runtime_used = True
        result.runtime_name = name
        result.runtime_key = key
        result.adapter_id = adapter.id
        result.created = created
        logger.info(
            "LoRA resolve chat_id=%s adapter=%s → runtime model %r (created=%s)",
            chat.id,
            adapter.name,
            name,
            created,
        )
        return name, result

    async def ensure_runtime_model(
        self,
        client,
        *,
        adapter,
        key: str,
        name: str,
        base_model: str,
    ) -> bool:
        """Гарантирует существование runtime-модели; максимум 1× create.

        Порядок: кэш ``key → exists`` → сверка ``list_models`` (GET /api/tags,
        покрывает повторный запуск/другой процесс) → под lock (повторный
        create исключён) → валидация пути (§2.7) → загрузка blob → create.

        Возвращает True, если runtime-модель создана этим вызовом; False — на
        кэш-хите или если модель уже есть в Ollama. НИКОГДА не вызывает
        ``ollama create`` на каждое сообщение (§7.4). БЕЗ ``cleanup()``/GC
        (§2.7): runtime-модели остаются в Ollama.
        """
        cached = self._exists_cache.get(key)
        if cached is not None:
            logger.info("LoRA runtime cache-hit key=%s name=%s", key, name)
            return False

        models = await ollama_client.list_models(client)
        if name in models:
            self._exists_cache[key] = name
            logger.info(
                "LoRA runtime-модель уже существует в Ollama (list_models): %s",
                name,
            )
            return False

        async with self._create_lock():
            if key in self._exists_cache:
                logger.info(
                    "LoRA runtime cache-hit после блокировки key=%s name=%s",
                    key,
                    name,
                )
                return False
            if name in models:
                # другой конкурент уже создал модель, пока мы ждали lock
                self._exists_cache[key] = name
                return False

            # Проверка пути (§2.7): файл не пропал и не стал недоступным с
            # момента регистрации. with_sha256=False — хранимый blob-диджест
            # остаётся авторитетным (§2.2).
            try:
                validate_adapter_path(
                    adapter.path, adapter.format, with_sha256=False
                )
            except LoRAValidationError as exc:
                raise RuntimeError(f"LoRA-адаптер '{adapter.name}': {exc}") from exc

            digest = f"sha256:{adapter.sha256}"
            filename = os.path.basename(adapter.path) or f"adapter-{adapter.id}.gguf"
            try:
                await ollama_client.upload_adapter_file(client, adapter.path, digest)
                await ollama_client.create_model(
                    client,
                    name=name,
                    from_model=base_model,
                    adapters={filename: digest},
                )
            except RuntimeError:
                logger.exception(
                    "LoRA runtime-модель не создана key=%s name=%s", key, name
                )
                raise
            self._exists_cache[key] = name
            return True
