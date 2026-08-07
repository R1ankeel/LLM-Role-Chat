"""LoRA-адаптеры: глобальный registry и конфигурация чата (Plans/LoRA.md §2.6, Sprint 4).

Две логические группы endpoints, разделённые по §2.6:
- глобальный registry: ``GET/POST/PUT/DELETE /api/lora``;
- конфигурация чата: ``GET/PUT /api/chats/{chat_id}/lora``.

Коды ошибок по конвенции проекта (задача 3): 404 (не найдено), 409
(конфликт/использование — удаление регистрации, используемой чатами), 422
(валидация пути/формата/ссылок на адаптер).
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from .. import crud
from .. import schemas
from ..database import get_async_db
from ..lora_validation import LoRAInUseError, LoRAValidationError

router = APIRouter(tags=["lora"])


# ------------------------- Глобальный registry (§2.6) -------------------------


@router.get("/lora", response_model=list[schemas.LoRAAdapterRead])
async def list_adapters(
    skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_async_db)
):
    """Список зарегистрированных LoRA-адаптеров (registry)."""
    adapters = await crud.list_lora_adapters(db, skip=skip, limit=limit)
    return [schemas.LoRAAdapterRead.model_validate(a) for a in adapters]


@router.post(
    "/lora",
    response_model=schemas.LoRAAdapterRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_adapter(
    create: schemas.LoRAAdapterCreate, db: AsyncSession = Depends(get_async_db)
):
    """Регистрация LoRA-адаптера: валидация пути (§2.7) + identity.

    Невалидный путь/формат/файл → 422 (``LoRAValidationError``). Физический
    файл не копируется и не удаляется.
    """
    try:
        adapter = await crud.create_lora_adapter(db, create)
    except LoRAValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return schemas.LoRAAdapterRead.model_validate(adapter)


@router.put("/lora/{adapter_id}", response_model=schemas.LoRAAdapterRead)
async def update_adapter(
    adapter_id: int,
    update: schemas.LoRAAdapterUpdate,
    db: AsyncSession = Depends(get_async_db),
):
    """Изменение регистрации адаптера.

    При изменении ``path``/``format`` — повторная валидация пути (§2.7) и
    пересчёт sha256; невалидный путь → 422. Несуществующий адаптер → 404.
    """
    try:
        adapter = await crud.update_lora_adapter(db, adapter_id, update)
    except LoRAValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    if adapter is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="LoRA-адаптер не найден",
        )
    return schemas.LoRAAdapterRead.model_validate(adapter)


@router.delete("/lora/{adapter_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_adapter(adapter_id: int, db: AsyncSession = Depends(get_async_db)):
    """Удаление регистрации адаптера из registry.

    Удаляется ТОЛЬКО регистрация — физический файл пользователя НЕ трогается
    (§2.7). Если адаптер используется хотя бы одним чатом → 409 со списком
    чатов. Несуществующий адаптер → 404.
    """
    try:
        deleted = await crud.delete_lora_adapter(db, adapter_id)
    except LoRAInUseError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": str(exc),
                "chats": [
                    {"chat_id": chat_id, "name": name}
                    for chat_id, name in exc.chats
                ],
            },
        ) from exc
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="LoRA-адаптер не найден",
        )
    return None


# ------------------------- Конфигурация чата (§2.6) -------------------------


@router.get("/chats/{chat_id}/lora", response_model=schemas.ChatLoRAConfig)
async def get_chat_lora_config(
    chat_id: int, db: AsyncSession = Depends(get_async_db)
):
    """Конфигурация LoRA чата ``{enabled, adapter_id}``.

    Один GET — источник настроек фронта. ``enabled=true`` + ``adapter_id=null``
    — допустимое состояние (§2.4). Чат не найден → 404.
    """
    config = await crud.get_chat_lora_config(db, chat_id)
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Чат не найден"
        )
    return config


@router.put("/chats/{chat_id}/lora", response_model=schemas.ChatLoRAConfig)
async def put_chat_lora_config(
    chat_id: int,
    config: schemas.ChatLoRAConfig,
    db: AsyncSession = Depends(get_async_db),
):
    """Атомарная замена конфигурации LoRA чата.

    Валидация: чат должен существовать (404), ссылка на существующий адаптер
    (422), UNIQUE(chat_id) — не более одного адаптера на чат. ``enabled=true`` +
    ``adapter_id=null`` сохраняется как допустимое состояние (§2.4).
    """
    if await crud.get_chat(db, chat_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Чат не найден"
        )
    try:
        saved = await crud.put_chat_lora_config(db, chat_id, config)
    except LoRAValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return saved
