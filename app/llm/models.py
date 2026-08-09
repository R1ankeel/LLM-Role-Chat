"""Управление runtime-моделями Ollama: tags/blob/create/delete/version (Sprint 5A).

Перенесено 1:1 из ``app/ollama_client.py`` (диапазон §5.2: 2889–3032).
"""

from __future__ import annotations

import logging

import httpx

from ..config import settings

logger = logging.getLogger(__name__)


async def list_models(client: httpx.AsyncClient) -> list[str]:
    """Список имён моделей Ollama (GET /api/tags), отсортированный уникальный.

    Используется для сверки существования runtime-моделей (§2.2): повторный
    запуск/перезапуск процесса не должен пересоздавать уже созданную модель.
    """
    resp = await client.get("/api/tags")
    if resp.status_code != 200:
        raise RuntimeError(
            f"Не удалось получить список моделей Ollama (GET /api/tags → "
            f"{resp.status_code}): {resp.text}"
        )
    data = resp.json()
    names = {m.get("name") for m in data.get("models", []) if m.get("name")}
    return sorted(names)


async def upload_adapter_file(
    client: httpx.AsyncClient, path: str, digest: str
) -> bool:
    """Загрузить GGUF-адаптер в blob-хранилище Ollama (§2.7, Q4).

    ``digest`` — ``sha256:<hex>`` (или просто hex, будет нормализован). Флоу:
    HEAD /api/blobs/:digest → 200 (уже загружен, ничего не делаем) / 404
    (POST байтами файла). Возвращает True, если файл реально загружен, False —
    если blob уже существовал.
    """
    if digest and not digest.startswith("sha256:"):
        digest = f"sha256:{digest}"
    url = f"/api/blobs/{digest}"

    head = await client.head(url)
    if head.status_code == 200:
        logger.info("LoRA blob уже существует в Ollama: %s", digest)
        return False
    if head.status_code != 404:
        raise RuntimeError(
            f"Ollama: не удалось проверить blob {digest} "
            f"(HEAD → {head.status_code}): {head.text}"
        )

    try:
        with open(path, "rb") as f:
            content = f.read()
    except OSError as exc:
        raise RuntimeError(
            f"Не удалось прочитать файл LoRA-адаптера для загрузки: {path} ({exc})"
        ) from exc

    resp = await client.post(url, content=content)
    if resp.status_code != 200:
        raise RuntimeError(
            f"Не удалось загрузить LoRA-адаптер в Ollama (blob {digest}, "
            f"POST → {resp.status_code}): {resp.text}"
        )
    logger.info("LoRA blob загружен в Ollama: %s (%s)", digest, path)
    return True


async def create_model(
    client: httpx.AsyncClient,
    name: str,
    from_model: str,
    adapters: dict[str, str],
) -> None:
    """Создать runtime-модель (POST /api/create) со структурным телом.

    ``from_model`` — имя базовой модели Ollama; ``adapters`` —
    ``{имя_файла: "sha256:<digest>"}``, РОВНО один адаптер (§2.5).
    Modelfile-строка НЕ передаётся (в 0.32.6 HTTP API её не читает, 400).
    """
    if not adapters:
        raise RuntimeError(
            "create_model требует хотя бы один адаптер; пустой adapters недопустим"
        )
    if len(adapters) > 1:
        raise RuntimeError(
            "Ollama поддерживает ровно один LoRA-адаптер на runtime-модель "
            "(§2.5); получено адаптеров: %d" % len(adapters)
        )
    payload = {
        "model": name,
        "from": from_model,
        "adapters": dict(adapters),
        "stream": False,
    }
    resp = await client.post("/api/create", json=payload)
    if resp.status_code != 200:
        raise RuntimeError(
            f"Не удалось создать runtime-модель '{name}' (POST /api/create → "
            f"{resp.status_code}): {resp.text}"
        )
    logger.info(
        "Runtime-модель создана: %s (from=%s, adapters=%s)",
        name,
        from_model,
        list(adapters),
    )


async def delete_model(client: httpx.AsyncClient, name: str) -> None:
    """Удалить модель Ollama (DELETE /api/delete) — ТОЛЬКО явный вызов.

    Автоудаления runtime-моделей в MVP НЕТ (§2.7): cleanup()/GC исключены,
    runtime-модели остаются в Ollama. В httpx 0.28.1 ``Client.delete`` не
    принимает body — используем ``client.request("DELETE", url, json=...)``.
    404 трактуется как успех (модели и так нет).
    """
    resp = await client.request("DELETE", "/api/delete", json={"model": name})
    if resp.status_code not in (200, 404):
        raise RuntimeError(
            f"Не удалось удалить модель '{name}' (DELETE /api/delete → "
            f"{resp.status_code}): {resp.text}"
        )
    logger.info("Модель удалена (явный вызов): %s", name)


async def check_capabilities(
    client: httpx.AsyncClient,
) -> "RuntimeCapabilities":
    """Проверка доступности Ollama + capability-флаги runtime (§2.5).

    Ollama не имеет endpoint-а возможностей; значения подтверждены Sprint 0:
    ``supports_lora=true``, ``supports_safetensors=false`` (только GGUF).
    Здесь выполняется проверка версии/доступности сервера (GET /api/version);
    недоступность → RuntimeError (невозможно создать runtime-модель).
    """
    from ..lora_manager import RuntimeCapabilities  # local: избегаем цикла

    try:
        resp = await client.get("/api/version")
    except httpx.RequestError as exc:
        raise RuntimeError(
            "Ollama недоступна. Убедитесь, что сервер запущен на "
            f"{settings.ollama_base_url}"
        ) from exc
    if resp.status_code != 200:
        raise RuntimeError(
            f"Ollama вернула ошибку при проверке версии (GET /api/version → "
            f"{resp.status_code}): {resp.text}"
        )
    version = (resp.json() or {}).get("version", "?")
    logger.debug("Ollama version: %s", version)
    return RuntimeCapabilities(supports_lora=True, supports_safetensors=False)
