"""Валидация пути LoRA-адаптера при create/update (Plans/LoRA.md §2.7).

Проверки (Sprint 1, задача 7):
- путь абсолютный;
- путь существует и является файлом;
- файл доступен для чтения;
- формат корректен для ``format`` (только gguf/auto; safetensors отклоняется —
  ``supports_safetensors=false``, §2.5);
- файл — валидный GGUF (магические байты ``GGUF`` + чтение заголовка);
- при регистрации вычисляется ``sha256`` содержимого файла (blob-диджест,
  §2.2) — хранится в ``lora_adapters.sha256`` для blob-флоу и runtime key.

Ошибки валидации → ``LoRAValidationError`` (роутер отдаёт 422).
``LoRAInUseError`` — адаптер используется чатами (роутер отдаёт 409).
"""

from __future__ import annotations

import hashlib
import os
import struct
from dataclasses import dataclass

# Магические байты GGUF (первые 4 байта файла).
GGUF_MAGIC = b"GGUF"
# Заголовок GGUF, читаемый при валидации: magic(u32) + version(u32) +
# tensor_count(u64) + metadata_kv_count(u64) = 24 байта.
_GGUF_HEADER_SIZE = 24

# Допустимые значения поля `format` (задача 1); safetensors НЕ поддерживается.
SUPPORTED_FORMATS = frozenset({"gguf", "safetensors", "auto"})
UNSUPPORTED_FORMATS = frozenset({"safetensors"})


class LoRAValidationError(Exception):
    """Невалидный путь/формат/ссылка LoRA-адаптера (роутер → 422)."""


class LoRAInUseError(Exception):
    """Адаптер используется хотя бы одним чатом (роутер → 409).

    ``chats`` — список пар ``(chat_id, chat_name)`` для тела 409-ответа.
    """

    def __init__(self, message: str = "", chats: list[tuple[int, str]] | None = None):
        super().__init__(message)
        self.chats = chats or []


@dataclass(frozen=True)
class AdapterFileInfo:
    """Результат валидации файла адаптера при регистрации."""

    sha256: str
    # Фактически определённый формат (в MVP всегда "gguf").
    detected_format: str


def read_gguf_header(path: str) -> tuple[int, int]:
    """Читает заголовок GGUF: (version, tensor_count).

    Невалидный магический префикс/версия/обрезка → ``LoRAValidationError``.
    Чтение также служит проверкой доступности файла для чтения.
    """
    try:
        with open(path, "rb") as f:
            header = f.read(_GGUF_HEADER_SIZE)
    except OSError as exc:
        raise LoRAValidationError(f"Файл не может быть прочитан: {path} ({exc})") from exc

    if len(header) < 4 or header[:4] != GGUF_MAGIC:
        hint = ""
        if path.lower().endswith(".safetensors"):
            hint = (
                " Файл выглядит как safetensors-адаптер — формат safetensors "
                "не поддерживается (supports_safetensors=false, §2.5), "
                "используйте GGUF."
            )
        raise LoRAValidationError(
            "Файл не является валидным GGUF-адаптером (ожидаются магические "
            f"байты 'GGUF').{hint}"
        )
    if len(header) < _GGUF_HEADER_SIZE:
        raise LoRAValidationError(
            "Файл слишком мал для валидного GGUF-заголовка (обрезка файла)."
        )
    version = struct.unpack("<I", header[4:8])[0]
    if version < 1:
        raise LoRAValidationError(f"Неподдерживаемая версия GGUF: {version}")
    tensor_count = struct.unpack("<Q", header[8:16])[0]
    return version, tensor_count


def compute_sha256(path: str) -> str:
    """sha256 содержимого файла (по чанкам, файлы могут быть гигабайтными)."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_adapter_path(
    path: str, format: str = "auto", with_sha256: bool = True
) -> AdapterFileInfo:
    """Валидирует путь к LoRA-адаптеру по §2.7.

    Возвращает ``AdapterFileInfo`` (sha256 + определённый формат) либо бросает
    ``LoRAValidationError``. ``format`` normalise: ``gguf``/``auto`` — валидные
    GGUF; ``safetensors`` отклоняется.

    ``with_sha256=False`` — пропускает чтение всего файла для хеширования
    (runtime-проверка пути при промахе кэша, Sprint 2): валидность GGUF и
    доступность всё равно проверяются, но ``sha256`` возвращается пустым
    (хранимый в БД blob-диджест остаётся авторитетным, §2.2).
    """
    fmt = (format or "auto").strip().lower()
    if fmt not in SUPPORTED_FORMATS:
        raise LoRAValidationError(
            f"Неизвестный формат LoRA: {format!r} "
            f"(допустимо: {', '.join(sorted(SUPPORTED_FORMATS))})"
        )
    if fmt in UNSUPPORTED_FORMATS:
        raise LoRAValidationError(
            "Формат safetensors не поддерживается (supports_safetensors=false, "
            "§2.5). Поддерживаются только GGUF-адаптеры."
        )

    if not os.path.isabs(path):
        raise LoRAValidationError(
            "Путь к LoRA-адаптеру должен быть абсолютным "
            f"(получено: {path!r})"
        )
    if not os.path.exists(path):
        raise LoRAValidationError(f"Файл LoRA-адаптера не найден: {path}")
    if not os.path.isfile(path):
        raise LoRAValidationError(
            f"Путь указывает не на файл (ожидается файл .gguf): {path}"
        )
    if not os.access(path, os.R_OK):
        raise LoRAValidationError(f"Нет прав на чтение файла LoRA-адаптера: {path}")

    # Чтение заголовка = проверка валидности GGUF + доступности для чтения.
    read_gguf_header(path)
    if with_sha256:
        sha256 = compute_sha256(path)
    else:
        sha256 = ""
    return AdapterFileInfo(sha256=sha256, detected_format="gguf")
