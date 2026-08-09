"""Память: периодическая пересборка саммари персонажа (Sprint 6C).

Sprint 6C (§4.5 decomposition.md): перенос из ``memory_service.py``
`_maybe_update_summaries`. Направление: memory/ → crud, ollama_client
(без обратных импортов).
"""

import httpx
import structlog

from .. import crud
from .. import ollama_client
from ..config import settings
from ..database import AsyncSessionLocal
from .witness import _character_from_snapshot, get_observable_context_for_character

logger = structlog.get_logger(__name__)


async def _maybe_update_summaries(
    client: httpx.AsyncClient,
    chat_id: int,
    character_snapshots: list[dict],
    model_name: str,
) -> None:
    async with AsyncSessionLocal() as db:
        try:
            character_ids = [c["id"] for c in character_snapshots]
            character_names = {c["id"]: c["name"] for c in character_snapshots}
            character_locations = {
                c["id"]: (c.get("location") or "") for c in character_snapshots
            }
            summaries = await crud.get_summaries_for_characters(db, character_ids)

            for character_snap in character_snapshots:
                character_id = character_snap["id"]
                existing = summaries.get(character_id)
                through_message_id = existing.through_message_id if existing else 0

                pending_count = await crud.count_messages_after(
                    db, chat_id, through_message_id
                )
                if pending_count < settings.summary_interval_messages:
                    continue

                new_messages = await crud.get_messages_since(
                    db, chat_id, through_message_id
                )
                if not new_messages:
                    continue

                presence_map = await crud.get_presence_map(
                    db,
                    [message.id for message in new_messages],
                    character_id,
                )
                attention_map = await crud.get_attention_map(
                    db,
                    [message.id for message in new_messages],
                    character_id,
                )
                # Same stricter filter as memory: present/told only (no mentioned snippets)
                observable = get_observable_context_for_character(
                    new_messages,
                    character_id,
                    character_names,
                    presence_map,
                    viewer_location=character_snap.get("location") or "",
                    character_locations=character_locations,
                    attention_map=attention_map,
                )
                dialogue_text = observable.text
                if not dialogue_text.strip():
                    continue
                character = _character_from_snapshot(character_snap)
                existing_content = existing.content if existing else ""

                try:
                    updated_summary = await ollama_client.summarize_for_character(
                        client,
                        model_name,
                        character,
                        dialogue_text,
                        existing_summary=existing_content,
                    )
                except Exception:
                    logger.warning(
                        "[chat_id=%d] Summary update failed for %s",
                        chat_id,
                        character_snap["name"],
                    )
                    continue

                if not updated_summary.strip():
                    continue

                # Advance the summary frontier only to the last message the
                # character actually perceived. Using the global max would mark
                # unperceived messages (e.g. a distant scene) as "covered", so the
                # summary would be written as if the character lost part of history.
                perceived_ids = [
                    line.message_id
                    for line in observable.lines
                    if line.message_id is not None
                ]
                if not perceived_ids:
                    continue
                through_message_id = max(perceived_ids)
                await crud.upsert_character_summary(
                    db,
                    chat_id,
                    character_id,
                    updated_summary.strip(),
                    through_message_id,
                )
                logger.info(
                    "[chat_id=%d] Summary updated for %s (through_message_id=%d)",
                    chat_id,
                    character_snap["name"],
                    through_message_id,
                )
        except Exception:
            logger.exception("[chat_id=%d] Background summary update failed", chat_id)
