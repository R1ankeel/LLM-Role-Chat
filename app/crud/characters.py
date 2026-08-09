"""Персонажи (вкл. player, sync локаций игрока, actions) (Sprint 4)."""



from __future__ import annotations



import logging

from dataclasses import dataclass, field as dataclass_field

from sqlalchemy import select

from sqlalchemy.ext.asyncio import AsyncSession

from .. import models

from .. import schemas

from ..perception_utils import locations_match, serialize_target_ids

from ..config import settings

from .locations import get_chat_locations, get_location, resolve_location_name

from .threads import _ensure_thread_for_action



logger = logging.getLogger(__name__)

async def _sync_player_character_location(
    db: AsyncSession, db_chat: models.Chat, location: str
) -> None:
    """Sync the player character's ``location`` with ``chats.player_location``.

    The location of the player exists in two places — the chat (edited via the
    right panel "Локация игрока") and the player character's card (edited via
    the character card "Локация"). Both feed the LLM prompt (presence /
    isolation vs the scene block), so they must stay in sync. ``player_location``
    is the source of truth; the player character mirrors it.
    """
    player = await get_player_character(db, db_chat.id)
    if player is None:
        return
    player.location = location
    player.location_id = None
    if location.strip():
        locations = await get_chat_locations(db, db_chat.id)
        resolved = resolve_location_name(locations, location)
        if resolved is not None:
            player.location_id = resolved.id

async def _sync_chat_player_location(
    db: AsyncSession, db_character: models.Character
) -> None:
    """Sync ``chats.player_location`` with the player character's ``location``.

    Reverse direction of ``_sync_player_character_location``: when the player
    character's location is edited from the character card, the chat-level
    ``player_location`` (used for presence/isolation in prompts) follows it.
    """
    from .chats import get_chat  # против цикла модулей (Sprint 4)
    if not db_character.is_player:
        return
    db_chat = await get_chat(db, db_character.chat_id)
    if db_chat is None or db_chat.player_location == db_character.location:
        return
    db_chat.player_location = db_character.location

async def resolve_player_location(
    db: AsyncSession, chat_id: int
) -> models.Location | None:
    """Resolve the player's canonical location and reconcile representations.

    Canonical identity is the ``Location`` row (by ``location_id``, else by
    name). When the canonical location is determined and the legacy strings
    (``chats.player_location`` / player character ``location``) drift from it,
    they are healed to the canonical name — restoring consistency rather than
    picking a "winner" between two strings. Returns the canonical ``Location``
    or ``None`` when none is resolvable (shared scene / unknown name).
    """
    from .chats import get_chat  # против цикла модулей (Sprint 4)
    player = await get_player_character(db, chat_id)
    chat = await get_chat(db, chat_id)
    if player is None or chat is None:
        return None

    canonical: models.Location | None = None
    if player.location_id is not None:
        canonical = await get_location(db, player.location_id)
    if canonical is None:
        locations = await get_chat_locations(db, chat_id)
        canonical = resolve_location_name(locations, chat.player_location) or (
            resolve_location_name(locations, player.location)
        )
    if canonical is None:
        return None

    changed = False
    if (chat.player_location or "") != canonical.name:
        chat.player_location = canonical.name
        changed = True
    if (player.location or "") != canonical.name:
        player.location = canonical.name
        changed = True
    if player.location_id != canonical.id:
        player.location_id = canonical.id
        changed = True
    if changed:
        await db.commit()
    return canonical

# ---------------------------- Character ----------------------------
async def _order_index_taken(
    db: AsyncSession, chat_id: int, order_index: int, exclude_id: int | None = None
) -> bool:
    stmt = select(models.Character).where(
        models.Character.chat_id == chat_id,
        models.Character.order_index == order_index,
    )
    if exclude_id is not None:
        stmt = stmt.where(models.Character.id != exclude_id)
    result = await db.execute(stmt)
    return result.scalars().first() is not None

async def create_character(
    db: AsyncSession, chat_id: int, character: schemas.CharacterCreate
) -> models.Character:
    if await _order_index_taken(db, chat_id, character.order_index):
        raise ValueError(
            f"order_index={character.order_index} уже занят в этом чате"
        )
    char_data = character.model_dump()
    initial_rels = char_data.pop("initial_relationships", [])
    char_data.pop("is_player", None)  # prevent setting is_player from API
    # avatar грузится только через upload endpoint, а не при создании
    char_data.pop("avatar_url", None)
    char_data.pop("avatar_crop", None)
    db_character = models.Character(chat_id=chat_id, **char_data)
    db.add(db_character)
    await db.commit()
    await db.refresh(db_character)

    if initial_rels:
        for rel in initial_rels:
            rel_exists = await db.execute(
                select(models.CharacterRelationship).where(
                    models.CharacterRelationship.source_character_id == db_character.id,
                    models.CharacterRelationship.target_character_id == rel["target_id"],
                )
            )
            if rel_exists.scalar_one_or_none() is not None:
                continue
            db_rel = models.CharacterRelationship(
                chat_id=chat_id,
                source_character_id=db_character.id,
                target_character_id=rel["target_id"],
                relationship_type=rel.get("relationship_type", models.DEFAULT_RELATIONSHIP_TYPE),
                affection=rel.get("affection", models.DEFAULT_AFFECTION),
                trust=rel.get("trust", models.DEFAULT_TRUST),
                attraction=rel.get("attraction", models.DEFAULT_ATTRACTION),
                resentment=rel.get("resentment", models.DEFAULT_RESENTMENT),
                jealousy=rel.get("jealousy", models.DEFAULT_JEALOUSY),
                description=rel.get("description", ""),
                initial_description=rel.get("description", ""),
            )
            db.add(db_rel)
        await db.commit()
        await db.refresh(db_character)

    return db_character

async def get_character(db: AsyncSession, character_id: int) -> models.Character | None:
    return await db.get(models.Character, character_id)

async def get_characters_by_chat(
    db: AsyncSession, chat_id: int, include_player: bool = False
) -> list[models.Character]:
    stmt = (
        select(models.Character)
        .where(models.Character.chat_id == chat_id)
        .order_by(models.Character.order_index, models.Character.id)
    )
    if not include_player:
        stmt = stmt.where(models.Character.is_player == False)
    result = await db.execute(stmt)
    return list(result.scalars().all())

async def get_player_character(
    db: AsyncSession, chat_id: int
) -> models.Character | None:
    stmt = select(models.Character).where(
        models.Character.chat_id == chat_id,
        models.Character.is_player == True,
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()

async def create_player_character(
    db: AsyncSession, chat_id: int, name: str = "Игрок"
) -> models.Character:
    from .chats import get_chat  # против цикла модулей (Sprint 4)
    existing = await get_player_character(db, chat_id)
    if existing:
        return existing
    db_chat = await get_chat(db, chat_id)
    player_location = ""
    if db_chat is not None:
        player_location = getattr(db_chat, "player_location", "") or ""
    player = models.Character(
        chat_id=chat_id,
        name=name,
        is_player=True,
        order_index=9999,
        location=player_location,
    )
    db.add(player)
    await db.commit()
    await db.refresh(player)

    # Create NPC->Player relationships for all NPCs.
    # Player->NPC relationships are intentionally not tracked.
    npcs = await get_characters_by_chat(db, chat_id)
    for npc in npcs:
        rel_npc_player = models.CharacterRelationship(
            chat_id=chat_id,
            source_character_id=npc.id,
            target_character_id=player.id,
        )
        db.add(rel_npc_player)
    await db.commit()
    await db.refresh(player)
    return player

async def update_character(
    db: AsyncSession, character_id: int, character_update: schemas.CharacterUpdate
) -> models.Character | None:
    db_character = await get_character(db, character_id)
    if db_character is None:
        return None
    update_data = character_update.model_dump(exclude_unset=True)
    new_index = update_data.get("order_index")
    if new_index is not None and new_index != db_character.order_index:
        if await _order_index_taken(
            db, db_character.chat_id, new_index, exclude_id=db_character.id
        ):
            raise ValueError(f"order_index={new_index} уже занят в этом чате")
    for field, value in update_data.items():
        setattr(db_character, field, value)
    if "location" in update_data:
        await _sync_chat_player_location(db, db_character)
    await db.commit()
    await db.refresh(db_character)
    return db_character

async def delete_character(db: AsyncSession, character_id: int) -> bool:
    db_character = await get_character(db, character_id)
    if db_character is None:
        return False
    if db_character.is_player:
        raise ValueError("Нельзя удалить игрока")
    await db.delete(db_character)
    await db.commit()
    return True

# ------------------------ Character Location --------------------------
async def update_character_location(
    db: AsyncSession, character_id: int, location: str
) -> models.Character | None:
    """Manually override a character's location.

    WPE 3.0 Фаза 8 (аудит legacy-полей §6 v2): строка ``location`` — только
    read-only legacy-bridge; источник — ``location_id``. Резолвим каноническую
    локацию и пишем оба поля одной транзакцией, чтобы не оставалось пути,
    обновляющего только строку (нерезолвленная/общая сцена → ``location_id``
    = None).
    """
    db_character = await get_character(db, character_id)
    if db_character is None:
        return None
    db_character.location = location
    db_character.location_id = None
    if location.strip():
        locations = await get_chat_locations(db, db_character.chat_id)
        resolved = resolve_location_name(locations, location)
        if resolved is not None:
            db_character.location_id = resolved.id
    await _sync_chat_player_location(db, db_character)
    await db.commit()
    await db.refresh(db_character)
    return db_character

async def get_character_locations_by_chat(
    db: AsyncSession, chat_id: int
) -> dict[int, str]:
    """Get current locations for all characters in a chat."""
    characters = await get_characters_by_chat(db, chat_id)
    return {c.id: c.location or "" for c in characters}

async def update_character_locations_batch(
    db: AsyncSession, chat_id: int, locations: dict[int, str]
) -> None:
    """Batch-update character locations from scene extraction.

    WPE 3.0 Фаза 8 (аудит legacy-полей): строка ``location`` — read-only
    legacy-bridge; резолвим и пишем ``location_id`` параллельно (источник —
    каноническая ``Location``). Нерезолвленная локация оставляет
    ``location_id`` без изменений (консервативно, обратная совместимость).
    """
    characters = await get_characters_by_chat(db, chat_id)
    char_map = {c.id: c for c in characters}
    loc_rows = await get_chat_locations(db, chat_id)
    changed = False
    for cid, loc in locations.items():
        cid_int = int(cid)
        if cid_int in char_map:
            char = char_map[cid_int]
            new_loc = loc.strip()
            if char.location != new_loc:
                char.location = new_loc
                resolved = resolve_location_name(loc_rows, new_loc)
                if resolved is not None:
                    char.location_id = resolved.id
                changed = True
    if changed:
        await db.commit()

@dataclass
class ApplyActionsResult:
    """Результат применения действий `turn.actions` (WPE.md §5, Фаза 5).

    ``applied_moves`` / ``applied_messages`` — применённые действия с индексом
    в исходном ``turn.actions`` (для System Narrator: какие из них не отражены
    в тексте). ``rejected`` — отклонённые с причиной (невалидная локация /
    невалидные адресаты). Невалидное действие не портит валидные (#13).
    """

    applied_moves: list[dict] = dataclass_field(default_factory=list)
    applied_messages: list[dict] = dataclass_field(default_factory=list)
    rejected: list[dict] = dataclass_field(default_factory=list)

async def apply_character_actions(
    db: AsyncSession,
    chat_id: int,
    character: models.Character,
    turn: schemas.TurnOutput | None,
    *,
    round_id: str | None = None,
) -> ApplyActionsResult:
    """Применить структурированные действия персонажа атомарно (WPE.md §5).

    - ``move_to``: локация резолвится в каноническую ``Location`` (Фаза 1);
      успешный переезд обновляет ``character.location`` + ``location_id`` и
      создаёт immutable ``WorldEvent(move)`` с ``location_from``/``location_to``
      в ОДНОЙ транзакции (flush всех обновлений + один commit). Переезд в ту же
      локацию считается применённым без изменения состояния и без события.
    - ``send_message``: валидируются адресаты (участники чата); создаётся
      ``WorldEvent(speech)`` с ``target_character_ids`` и текущей локацией.
      Thread/remote_status формализуются в Фазе 6.
    - Порядок применения: ``move`` → зависящие от локации (``send_message``),
      внутри вида — исходный порядок (§5.5). ``location_id`` обновляется для
      успешных ``move_to`` (§5.6).
    - Невалидное действие отклоняется (нет ``WorldEvent``, нет изменения
      ``WorldState``, v2 §5.4) и не ломает валидные (#13).
    """
    result = ApplyActionsResult()
    if turn is None or not turn.actions:
        return result

    locations = await get_chat_locations(db, chat_id)
    characters = await get_characters_by_chat(db, chat_id)
    char_map = {c.id: c for c in characters}
    current_char = char_map.get(character.id, character)
    from_location = current_char.location or ""

    # ---- 1. Валидация предпосылок (§5.3) ----
    planned_moves: list[tuple[int, schemas.Action, models.Location | None]] = []
    planned_messages: list[tuple[int, schemas.Action, list[int]]] = []
    for index, action in enumerate(turn.actions):
        if action.type == "move_to":
            target = resolve_location_name(locations, action.location)
            if target is None:
                result.rejected.append(
                    {
                        "action_index": index,
                        "type": "move_to",
                        "reason": "unknown_location",
                        "location": action.location or "",
                    }
                )
            else:
                planned_moves.append((index, action, target))
        elif action.type == "send_message":
            raw_targets = [int(t) for t in action.target_character_ids]
            bad = [t for t in raw_targets if t not in char_map]
            if bad:
                result.rejected.append(
                    {
                        "action_index": index,
                        "type": "send_message",
                        "reason": "invalid_target",
                        "targets": bad,
                    }
                )
            else:
                planned_messages.append((index, action, raw_targets))
        else:
            result.rejected.append(
                {
                    "action_index": index,
                    "type": str(action.type),
                    "reason": "unsupported_action",
                }
            )

    # ---- 2. Применение: move → send_message, атомарно (§5.5) ----
    for index, action, target in planned_moves:
        to_canonical = target.name
        if _locations_same(from_location, to_canonical):
            result.applied_moves.append(
                {
                    "action_index": index,
                    "character_id": current_char.id,
                    "location_from": from_location,
                    "location_to": from_location,
                    "location_id": current_char.location_id,
                    "changed": False,
                }
            )
            continue
        current_char.location = to_canonical
        current_char.location_id = target.id
        db.add(
            models.WorldEvent(
                chat_id=chat_id,
                character_id=current_char.id,
                event_type="move",
                location=to_canonical,
                location_from=from_location,
                location_to=to_canonical,
                round_id=round_id,
                target_character_ids="[]",
            )
        )
        result.applied_moves.append(
            {
                "action_index": index,
                "character_id": current_char.id,
                "location_from": from_location,
                "location_to": to_canonical,
                "location_id": target.id,
                "changed": True,
            }
        )

    current_location = current_char.location or from_location
    for index, action, targets in planned_messages:
        db.add(
            models.WorldEvent(
                chat_id=chat_id,
                character_id=current_char.id,
                event_type="speech",
                location=current_location,
                round_id=round_id,
                target_character_ids=serialize_target_ids(targets),
            )
        )
        if settings.world_engine_threads_enabled:
            await _ensure_thread_for_action(
                db, chat_id, action.channel, current_char.id, targets
            )
        result.applied_messages.append(
            {
                "action_index": index,
                "character_id": current_char.id,
                "target_character_ids": targets,
                "channel": action.channel,
            }
        )

    if result.applied_moves or result.applied_messages:
        await db.commit()
        await db.refresh(current_char)

    if result.applied_moves or result.applied_messages or result.rejected:
        logger.info(
            "[WPE-P5] actions chat_id=%d character=%s applied_moves=%d "
            "applied_messages=%d rejected=%d",
            chat_id,
            current_char.name,
            len(result.applied_moves),
            len(result.applied_messages),
            len(result.rejected),
        )
    return result

def _locations_same(a: str, b: str) -> bool:
    """Сравнение локаций по каноническому имени (legacy-bridge, как Фаза 1)."""
    return locations_match(a, b)
