"""Локации: CRUD + backfill + adjacency (Sprint 4)."""



from __future__ import annotations



import json

from dataclasses import dataclass, field as dataclass_field

from sqlalchemy import select, update

from sqlalchemy.exc import IntegrityError

from sqlalchemy.ext.asyncio import AsyncSession

from .. import models

from .. import schemas

from ..perception_utils import _parse_adjacency_list, build_adjacency_index, is_shared_scene, locations_match, normalize_location, serialize_adjacency

# ----------------------------- Location -----------------------------
async def get_chat_locations(
    db: AsyncSession, chat_id: int
) -> list[models.Location]:
    """Get all locations for a chat (source of truth for CRUD/descriptions)."""
    stmt = (
        select(models.Location)
        .where(models.Location.chat_id == chat_id)
        .order_by(models.Location.name, models.Location.id)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())

async def get_adjacency_index(
    db: AsyncSession, chat_id: int
) -> dict[str, set[str]]:
    """Build a normalized location -> {neighbors} index from ``locations.adjacent_to``.

    Used by the perception layer for AUDIBLE / MENTIONED levels (§6, Sprint 2).
    """
    locations = await get_chat_locations(db, chat_id)
    return build_adjacency_index(locations)

async def get_location(db: AsyncSession, location_id: int) -> models.Location | None:
    return await db.get(models.Location, location_id)

def resolve_location_name(
    locations: list[models.Location], name: str | None
) -> models.Location | None:
    """Чистый резолвер: строковая локация → каноническая ``Location``.

    Регистронезависимый матч через ``locations_match`` (тот же
    normalize, что и у сравнения строк в движке). "Общая сцена" (пустая
    строка / каноническое имя) → None: у общей сцены нет id.
    """
    needle = (name or "").strip()
    if not needle:
        return None
    if is_shared_scene(normalize_location(needle)):
        return None
    for loc in locations:
        if locations_match(loc.name, needle):
            return loc
    return None

async def resolve_location_string(
    db: AsyncSession, chat_id: int, name: str | None
) -> models.Location | None:
    """Async-обёртка резолвера над списком локаций чата."""
    locations = await get_chat_locations(db, chat_id)
    return resolve_location_name(locations, name)

@dataclass
class LocationBackfillReport:
    """Результат backfill ``characters.location_id`` (WPE 3.0, Фаза 1).

    ``unresolved`` — персонажи, чья строковая локация не резолвится ни в
    одну локацию чата и не является общей сценой. Такие случаи НЕ
    проставляются молча: они требуют ручного разбора и попадают в отчёт.
    """

    total: int = 0
    resolved: int = 0
    shared_scene: int = 0
    unresolved: list[tuple[int, int, str, str]] = dataclass_field(
        default_factory=list
    )  # (chat_id, character_id, character_name, location)

    def lines(self) -> list[str]:
        """Человекочитаемые строки отчёта (для лога / скрипта)."""
        out = [
            f"total={self.total} resolved={self.resolved} "
            f"shared_scene={self.shared_scene} unresolved={len(self.unresolved)}"
        ]
        for chat_id, char_id, name, location in self.unresolved:
            out.append(f"  UNRESOLVED chat={chat_id} char={char_id} ({name!r}): {location!r}")
        return out

async def backfill_character_location_ids(
    db: AsyncSession, chat_id: int | None = None
) -> LocationBackfillReport:
    """Backfill ``characters.location_id`` из строковой ``characters.location``.

    WPE 3.0 (Plans/WPE.md §10, Фаза 1): для каждого персонажа (включая
    игрока) резолвит ``location`` через ``resolve_location_name``
    (регистронезависимо) и проставляет ``location_id``. Идемпотентно:
    повторный запуск обновляет только изменившиеся значения.

    Случаи, которые нельзя резолвить однозначно, не проставляются и
    фиксируются в отчёте на ручной разбор:
    - пустая строка / «Общая сцена» → id сбрасывается в None (у общей
      сцены нет id);
    - нерезолвленное имя (нет в ``locations`` чата) → остаётся None,
      заносится в ``report.unresolved``.

    Запуск — ``scripts/backfill_location_ids.py``.
    """
    stmt = select(models.Character)
    if chat_id is not None:
        stmt = stmt.where(models.Character.chat_id == chat_id)
    stmt = stmt.order_by(models.Character.chat_id, models.Character.id)
    characters = list((await db.execute(stmt)).scalars().all())

    locations_by_chat: dict[int, list[models.Location]] = {}
    report = LocationBackfillReport(total=len(characters))

    for character in characters:
        raw = (character.location or "").strip()
        if not raw or is_shared_scene(
            normalize_location(raw)
        ):
            if character.location_id is not None:
                character.location_id = None
            report.shared_scene += 1
            continue
        locs = locations_by_chat.get(character.chat_id)
        if locs is None:
            locs = await get_chat_locations(db, character.chat_id)
            locations_by_chat[character.chat_id] = locs
        loc = resolve_location_name(locs, raw)
        if loc is None:
            report.unresolved.append(
                (character.chat_id, character.id, character.name, raw)
            )
            continue
        if character.location_id != loc.id:
            character.location_id = loc.id
        report.resolved += 1

    await db.commit()
    return report

@dataclass
class PlotBackfillReport:
    """Результат backfill ``chats.original_plot/story_prompt`` из ``general_prompt``.

    Copy, не move: ``general_prompt`` не меняется. Идемпотентно: заполняются
    только пустые поля (повторный запуск ничего не перезаписывает).
    """

    total: int = 0
    filled_original_plot: int = 0
    filled_story_prompt: int = 0
    story_enabled: int = 0  # всегда 0: флаг остаётся false до Sprint 8

    def lines(self) -> list[str]:
        return [
            f"total={self.total} filled_original_plot={self.filled_original_plot} "
            f"filled_story_prompt={self.filled_story_prompt} story_enabled={self.story_enabled}"
        ]

async def backfill_plot_fields(
    db: AsyncSession, chat_id: int | None = None
) -> PlotBackfillReport:
    """Backfill ``chats.original_plot`` / ``chats.story_prompt`` из ``general_prompt``.

    Sprint 0 (Plans/update20.md §16.1): начальные значения story-полей = копия
    ``general_prompt`` (copy, не move). ``story_enabled`` остаётся False — сюжет
    выключен до Sprint 8. Идемпотентно: заполняются только пустые значения.
    """
    stmt = select(models.Chat)
    if chat_id is not None:
        stmt = stmt.where(models.Chat.id == chat_id)
    stmt = stmt.order_by(models.Chat.id)
    chats = list((await db.execute(stmt)).scalars().all())

    report = PlotBackfillReport(total=len(chats))
    for chat in chats:
        source = chat.general_prompt or ""
        if not chat.original_plot:
            chat.original_plot = source
            report.filled_original_plot += 1
        if not chat.story_prompt:
            chat.story_prompt = source
            report.filled_story_prompt += 1
        if chat.story_enabled:
            # Защита от случайного включения: backfill не включает сюжет.
            chat.story_enabled = False
            report.story_enabled += 1

    await db.commit()
    return report

@dataclass
class EventLocationBackfillReport:
    """Результат backfill ``world_events.location_id`` из строковой ``location``.

    Аналог ``LocationBackfillReport``: нерезолвленные случаи НЕ проставляются
    и попадают в ``unresolved`` на ручной разбор.
    """

    total: int = 0
    resolved: int = 0
    shared_scene: int = 0
    unresolved: list[tuple[int, int, str, str]] = dataclass_field(
        default_factory=list
    )  # (chat_id, event_id, event_type, location)

    def lines(self) -> list[str]:
        out = [
            f"total={self.total} resolved={self.resolved} "
            f"shared_scene={self.shared_scene} unresolved={len(self.unresolved)}"
        ]
        for chat_id, event_id, event_type, location in self.unresolved:
            out.append(
                f"  UNRESOLVED chat={chat_id} event={event_id} "
                f"type={event_type!r}: {location!r}"
            )
        return out

async def backfill_event_location_ids(
    db: AsyncSession, chat_id: int | None = None
) -> EventLocationBackfillReport:
    """Backfill ``world_events.location_id`` из строковой ``world_events.location``.

    Sprint 0 (Plans/update20.md): каноническая локация события (аналог
    ``backfill_character_location_ids``). Пустая строка / «Общая сцена» → NULL;
    нерезолвленное имя → NULL + отчёт на ручной разбор. Идемпотентно.
    """
    stmt = select(models.WorldEvent)
    if chat_id is not None:
        stmt = stmt.where(models.WorldEvent.chat_id == chat_id)
    stmt = stmt.order_by(models.WorldEvent.chat_id, models.WorldEvent.id)
    events = list((await db.execute(stmt)).scalars().all())

    locations_by_chat: dict[int, list[models.Location]] = {}
    report = EventLocationBackfillReport(total=len(events))

    for event in events:
        raw = (event.location or "").strip()
        if not raw or is_shared_scene(
            normalize_location(raw)
        ):
            if event.location_id is not None:
                event.location_id = None
            report.shared_scene += 1
            continue
        locs = locations_by_chat.get(event.chat_id)
        if locs is None:
            locs = await get_chat_locations(db, event.chat_id)
            locations_by_chat[event.chat_id] = locs
        loc = resolve_location_name(locs, raw)
        if loc is None:
            report.unresolved.append(
                (event.chat_id, event.id, event.event_type, raw)
            )
            continue
        if event.location_id != loc.id:
            event.location_id = loc.id
        report.resolved += 1

    await db.commit()
    return report

async def _sync_chat_locations_cache(db: AsyncSession, chat_id: int) -> None:
    """Keep `chats.locations` (JSON array of names) in sync with the locations table.

    Таблица `locations` — источник истины; `chats.locations` остаётся кэшем
    названий для движка (§14).
    """
    from .chats import get_chat  # против цикла модулей (Sprint 4)
    chat = await get_chat(db, chat_id)
    if chat is None:
        return
    locs = await get_chat_locations(db, chat_id)
    chat.locations = json.dumps([l.name for l in locs], ensure_ascii=False)
    await db.commit()

def _location_name_conflict(
    existing: list[models.Location], new_name: str, exclude_id: int | None = None
) -> models.Location | None:
    """Case-insensitive duplicate check (совпадает с locations_match/normalize)."""
    for loc in existing:
        if exclude_id is not None and loc.id == exclude_id:
            continue
        if locations_match(loc.name, new_name):
            return loc
    return None

async def create_location(
    db: AsyncSession, chat_id: int, location: schemas.LocationCreate
) -> models.Location:
    """Create a location; raises ValueError on duplicate name (→ 409)."""
    from .chats import get_chat  # против цикла модулей (Sprint 4)
    if await get_chat(db, chat_id) is None:
        raise ValueError("Чат не найден")
    name = (location.name or "").strip()
    if not name:
        raise ValueError("Название локации не может быть пустым")
    existing = await get_chat_locations(db, chat_id)
    conflict = _location_name_conflict(existing, name)
    if conflict is not None:
        raise ValueError(f"Локация «{conflict.name}» уже существует")
    db_location = models.Location(
        chat_id=chat_id,
        name=name,
        description=(location.description or ""),
        adjacent_to=serialize_adjacency(
            getattr(location, "adjacent_to", None)
        ),
    )
    db.add(db_location)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ValueError(f"Локация «{name}» уже существует") from exc
    await db.refresh(db_location)
    await _sync_chat_locations_cache(db, chat_id)
    return db_location

async def update_location(
    db: AsyncSession, location_id: int, location_update: schemas.LocationUpdate
) -> models.Location | None:
    """Update a location; on rename syncs string references. ValueError → 409."""
    db_location = await get_location(db, location_id)
    if db_location is None:
        return None
    update_data = location_update.model_dump(exclude_unset=True)
    old_name = db_location.name
    new_name: str | None = None
    if update_data.get("name") is not None:
        new_name = (update_data["name"] or "").strip()
        if not new_name:
            raise ValueError("Название локации не может быть пустым")
        update_data["name"] = new_name
        if not locations_match(old_name, new_name):
            existing = await get_chat_locations(db, db_location.chat_id)
            conflict = _location_name_conflict(existing, new_name, exclude_id=db_location.id)
            if conflict is not None:
                raise ValueError(f"Локация «{conflict.name}» уже существует")
    for field, value in update_data.items():
        if field == "adjacent_to":
            value = serialize_adjacency(value)
        setattr(db_location, field, value)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ValueError(f"Локация «{new_name}» уже существует") from exc
    await db.refresh(db_location)

    if new_name is not None and not locations_match(old_name, new_name):
        await _rename_location_references(db, db_location.chat_id, old_name, new_name)
    await _sync_chat_locations_cache(db, db_location.chat_id)
    return db_location

async def _rename_location_references(
    db: AsyncSession, chat_id: int, old_name: str, new_name: str
) -> None:
    """Синхронно обновить строковые ссылки при переименовании (§14)."""
    from .scene import get_scene_state  # против цикла модулей (Sprint 4)
    changed = False

    # characters.location (включая игрока)
    char_rows = await db.execute(
        select(models.Character.id, models.Character.location).where(
            models.Character.chat_id == chat_id
        )
    )
    char_updates: list[int] = []
    for char_id, loc in char_rows.all():
        if locations_match(loc or "", old_name):
            char_updates.append(char_id)
    if char_updates:
        await db.execute(
            update(models.Character)
            .where(models.Character.id.in_(char_updates))
            .values(location=new_name)
        )
        changed = True

    # messages.location
    msg_rows = await db.execute(
        select(models.Message.id, models.Message.location).where(
            models.Message.chat_id == chat_id
        )
    )
    msg_updates: list[int] = []
    for msg_id, loc in msg_rows.all():
        if locations_match(loc or "", old_name):
            msg_updates.append(msg_id)
    if msg_updates:
        await db.execute(
            update(models.Message)
            .where(models.Message.id.in_(msg_updates))
            .values(location=new_name)
        )
        changed = True

    # scene_states.character_locations (JSON dict: {id|name: location}) — только значения
    scene = await get_scene_state(db, chat_id)
    if scene is not None and scene.character_locations:
        raw = json.loads(scene.character_locations) if scene.character_locations else {}
        updated = {
            k: (new_name if locations_match(str(v), old_name) else v)
            for k, v in raw.items()
        }
        if updated != raw:
            scene.character_locations = json.dumps(updated, ensure_ascii=False)
            changed = True

    # locations.adjacent_to (JSON-массив имён): заменить old_name на new_name
    # в соседях других локаций (Спринт 2, аудиосвязь локаций).
    loc_rows = await db.execute(
        select(models.Location.id, models.Location.adjacent_to).where(
            models.Location.chat_id == chat_id
        )
    )
    for loc_id, adjacent_json in loc_rows.all():
        neighbors = _parse_adjacency_list(adjacent_json)
        replaced = False
        for i, neighbor in enumerate(neighbors):
            if locations_match(neighbor, old_name):
                neighbors[i] = new_name
                replaced = True
        if replaced:
            await db.execute(
                update(models.Location)
                .where(models.Location.id == loc_id)
                .values(adjacent_to=serialize_adjacency(neighbors))
            )
            changed = True

    if changed:
        await db.commit()

async def get_characters_referencing_location(
    db: AsyncSession, location: models.Location
) -> list[models.Character]:
    """Characters whose location matches this location (case-insensitive)."""
    from .characters import get_characters_by_chat  # против цикла модулей (Sprint 4)

    characters = await get_characters_by_chat(db, location.chat_id, include_player=True)
    return [
        c for c in characters
        if c.location and locations_match(c.location, location.name)
    ]

async def delete_location(db: AsyncSession, location_id: int) -> models.Location | None:
    """Delete a location and sync the `chats.locations` cache."""
    db_location = await get_location(db, location_id)
    if db_location is None:
        return None
    chat_id = db_location.chat_id
    await db.delete(db_location)
    await db.commit()
    await _sync_chat_locations_cache(db, chat_id)
    return db_location
