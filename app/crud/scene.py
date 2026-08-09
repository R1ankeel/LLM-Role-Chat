"""Scene state + присутствие (Sprint 4)."""



from __future__ import annotations



import json

from datetime import datetime

from sqlalchemy import select

from sqlalchemy.ext.asyncio import AsyncSession

from .. import models

from .. import schemas

# ----------------------------- Scene State -----------------------------
async def get_scene_state(db: AsyncSession, chat_id: int) -> models.SceneState | None:
    """Get scene state for a chat."""
    return await db.get(models.SceneState, chat_id)

async def upsert_scene_state(
    db: AsyncSession, chat_id: int, scene_update: schemas.SceneStateUpdate
) -> models.SceneState:
    """Create or update scene state for a chat."""
    scene = await get_scene_state(db, chat_id)
    if scene is None:
        scene = models.SceneState(chat_id=chat_id)
        db.add(scene)

    import json

    update_data = scene_update.model_dump(exclude_unset=True)
    if "custom_state" in update_data and update_data["custom_state"] is not None:
        cs = update_data["custom_state"]
        if hasattr(cs, "model_dump_json"):
            update_data["custom_state"] = cs.model_dump_json()
        elif isinstance(cs, dict):
            update_data["custom_state"] = json.dumps(cs)

    if "character_locations" in update_data and update_data["character_locations"] is not None:
        cl = update_data["character_locations"]
        if isinstance(cl, dict):
            update_data["character_locations"] = json.dumps(cl)

    for field, value in update_data.items():
        setattr(scene, field, value)

    await db.commit()
    await db.refresh(scene)
    return scene

async def get_present_character_ids(db: AsyncSession, chat_id: int) -> list[int]:
    """Get character IDs with presence 'present' or 'told' for latest messages."""
    from sqlalchemy import select, desc

    # Get latest message IDs for this chat
    stmt = (
        select(models.Message.id)
        .where(models.Message.chat_id == chat_id)
        .order_by(desc(models.Message.timestamp), desc(models.Message.id))
        .limit(20)
    )
    result = await db.execute(stmt)
    message_ids = [row[0] for row in result.fetchall()]

    if not message_ids:
        return []

    # Get presence records for these messages
    stmt = select(models.MessagePresence).where(
        models.MessagePresence.message_id.in_(message_ids),
        models.MessagePresence.presence.in_(["present", "told"]),
    )
    result = await db.execute(stmt)
    presence_records = result.scalars().all()

    return list({pr.character_id for pr in presence_records})

async def get_scene_state_with_presence(
    db: AsyncSession, chat_id: int
) -> schemas.SceneStateRead:
    """Get scene state with computed present character IDs."""
    from .chats import get_chat  # против цикла модулей (Sprint 4)
    scene = await get_scene_state(db, chat_id)
    present_ids = await get_present_character_ids(db, chat_id)
    chat = await get_chat(db, chat_id)
    player_location = getattr(chat, "player_location", "") if chat else ""

    if scene is None:
        return schemas.SceneStateRead(
            chat_id=chat_id,
            updated_at=datetime.utcnow(),
            present_character_ids=present_ids,
            custom_state=schemas.SceneCustomState(),
            player_location=player_location,
        )

    import json

    custom_state_dict = json.loads(scene.custom_state) if scene.custom_state else {}
    custom_state = schemas.SceneCustomState(**custom_state_dict)

    character_locations_raw = json.loads(scene.character_locations) if scene.character_locations else {}
    # Ensure keys are strings (JSON serialization uses string keys)
    character_locations = {str(k): str(v) for k, v in character_locations_raw.items() if v}

    return schemas.SceneStateRead(
        chat_id=scene.chat_id,
        time_of_day=scene.time_of_day,
        character_locations=character_locations,
        custom_state=custom_state,
        updated_at=scene.updated_at,
        present_character_ids=present_ids,
        player_location=player_location,
    )
