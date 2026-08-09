"""Память: извлечение и сохранение фактов после раунда (Sprint 6C).

Sprint 6C (§4.5 decomposition.md): перенос из ``memory_service.py``
`_extract_and_save_memories`. Направление: memory/ → crud, ollama_client,
task_queue (без обратных импортов).
"""

import httpx
import structlog

from .. import crud
from .. import ollama_client
from .. import schemas
from .. import task_queue
from ..config import settings
from ..database import AsyncSessionLocal
from .validation import MAX_EXISTING_FOR_DEDUP, validate_extracted_facts
from .witness import (
    _character_from_snapshot,
    _log_memory_perception,
    _sensors_proposal_to_facts,
    get_observable_context_for_character,
)

logger = structlog.get_logger(__name__)


async def _extract_and_save_memories(
    client: httpx.AsyncClient,
    chat_id: int,
    round_snapshots: list[dict],
    character_snapshots: list[dict],
    model_name: str,
) -> None:
    if len(round_snapshots) < 2:
        return

    character_names = {c["id"]: c["name"] for c in character_snapshots}
    character_locations = {
        c["id"]: (c.get("location") or "") for c in character_snapshots
    }
    same_round_ids = {
        snapshot["id"]
        for snapshot in round_snapshots
        if snapshot.get("id") is not None
    }
    message_ids = [
        snapshot["id"] for snapshot in round_snapshots if snapshot.get("id") is not None
    ]

    async with AsyncSessionLocal() as db:
        try:
            for character_snap in character_snapshots:
                character = _character_from_snapshot(character_snap)
                char_id = character_snap["id"]
                char_name = character_snap["name"]
                viewer_location = character_snap.get("location") or ""

                presence_map = await crud.get_presence_map(db, message_ids, char_id)
                attention_map = await crud.get_attention_map(db, message_ids, char_id)
                observable = get_observable_context_for_character(
                    round_snapshots,
                    char_id,
                    character_names,
                    presence_map,
                    same_round_ids=same_round_ids,
                    viewer_location=viewer_location,
                    character_locations=character_locations,
                    attention_map=attention_map,
                )
                _log_memory_perception(
                    chat_id=chat_id,
                    character_name=char_name,
                    character_id=char_id,
                    context=observable,
                )

                if not observable.has_observable_events:
                    continue

                round_text = observable.text
                # Sprint 2 (§5.1.3/§7): Sensors memory-candidates имеют
                # приоритет над прямым LLM-извлечением; Sensors память НЕ пишет —
                # только предлагает факты, движок валидирует и сохраняет.
                raw_facts = None
                sensors_used = False
                try:
                    from ..sensors_service import sensors_service

                    sensors_result = await sensors_service.run(
                        client, task="memory", minimal_context=round_text
                    )
                    sensors_facts = _sensors_proposal_to_facts(sensors_result or {})
                    if sensors_facts:
                        raw_facts = sensors_facts
                        sensors_used = True
                        logger.debug(
                            "[Memory] character=%s source=sensors candidates=%d",
                            char_name,
                            len(sensors_facts),
                        )
                except Exception:
                    logger.warning(
                        "[chat_id=%d] Sensors memory proposal failed for %s",
                        chat_id,
                        char_name,
                    )
                    sensors_facts = []

                if raw_facts is None:
                    try:
                        raw_facts = await ollama_client.extract_memories_for_character(
                            client, model_name, character, round_text
                        )
                    except Exception:
                        logger.warning(
                            "[chat_id=%d] Memory extraction failed for %s",
                            chat_id,
                            char_name,
                        )
                        continue

                if not raw_facts:
                    logger.debug(
                        "[Memory] character=%s memory_candidate=skipped reason=llm_empty",
                        char_name,
                    )
                    continue

                structured: list[schemas.ExtractedFact] = []
                for item in raw_facts:
                    if isinstance(item, schemas.ExtractedFact):
                        structured.append(item)
                    elif isinstance(item, str):
                        structured.append(schemas.ExtractedFact(fact=item))
                    elif isinstance(item, dict):
                        try:
                            structured.append(schemas.ExtractedFact.model_validate(item))
                        except Exception:
                            continue

                existing = await crud.get_memories_by_character(
                    db, char_id, limit=MAX_EXISTING_FOR_DEDUP
                )
                existing_contents = [m.content for m in existing]

                validated = validate_extracted_facts(
                    structured,
                    char_name,
                    existing_contents=existing_contents,
                    observable_context=round_text,
                )
                if not validated:
                    logger.debug(
                        "[chat_id=%d] No valid facts for %s after validation",
                        chat_id,
                        char_name,
                    )
                    continue

                # Only link memory to messages the character actually witnessed (present/told)
                observed_message_ids = [
                    line.message_id for line in observable.lines
                    if line.message_id is not None
                ]
                saved = 0
                for fact in validated:
                    # Sprint 2 (§7): тип памяти пишется только при включённом
                    # флаге memory_types_enabled (canary); иначе legacy-поведение.
                    memory_type = (
                        fact.memory_type if settings.memory_types_enabled else None
                    )
                    created = await crud.create_memory(
                        db,
                        schemas.MemoryCreate(
                            chat_id=chat_id,
                            character_id=char_id,
                            content=fact.fact,
                            importance=fact.importance,
                            category=fact.category,
                            memory_type=memory_type,
                        ),
                        source_message_ids=observed_message_ids,
                    )
                    if created is not None:
                        saved += 1
                        # Enqueue embedding generation job (non-blocking)
                        if settings.embedding_enabled:
                            await task_queue.memory_job_queue.enqueue(
                                job_type="embed_memory",
                                chat_id=chat_id,
                                payload={"memory_id": created.id, "content": fact.fact},
                            )
                        logger.debug(
                            "[Memory] character=%s memory_candidate=created fact=%r",
                            char_name,
                            fact.fact[:120],
                        )
                await crud.ensure_memory_limit(db, char_id)
                logger.info(
                    "[chat_id=%d] Saved %d/%d validated facts for %s",
                    chat_id,
                    saved,
                    len(validated),
                    char_name,
                )

            logger.info("[chat_id=%d] Per-character memory extraction complete", chat_id)
        except Exception:
            logger.exception("[chat_id=%d] Background memory save failed", chat_id)
