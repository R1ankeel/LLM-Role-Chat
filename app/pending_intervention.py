"""One-time user intervention ("Вмешательство") for the next generation round.

The instruction is held in memory only: it is never written to the message
history, never persisted and never enters the memory store. It applies to the
next generation and is consumed atomically after a fully successful round.

State machine: нет инструкции -> создана -> ожидает генерации ->
использована -> удалена.

Keyed by ``(chat_id, character_id)`` so a future per-character intervention
can coexist with the chat-wide one. ``character_id=None`` means chat-wide.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PendingIntervention:
    """A single-use directive scoped to (chat_id, character_id)."""

    chat_id: int
    instruction: str
    created_at: datetime
    character_id: int | None = None


_store: dict[tuple[int, int | None], PendingIntervention] = {}


def _key(chat_id: int, character_id: int | None) -> tuple[int, int | None]:
    return (chat_id, character_id)


def set_intervention(
    chat_id: int,
    instruction: str,
    character_id: int | None = None,
) -> PendingIntervention:
    """Store (or replace) a pending one-time intervention."""
    entry = PendingIntervention(
        chat_id=chat_id,
        instruction=instruction,
        created_at=datetime.now(timezone.utc),
        character_id=character_id,
    )
    _store[_key(chat_id, character_id)] = entry
    return entry


def get_intervention(
    chat_id: int,
    character_id: int | None = None,
) -> PendingIntervention | None:
    """Return the intervention for a character, falling back to the chat-wide one."""
    return _store.get(_key(chat_id, character_id)) or _store.get(_key(chat_id, None))


def consume_intervention(
    chat_id: int,
    character_id: int | None = None,
    expected: PendingIntervention | None = None,
) -> bool:
    """Consume (remove) a pending intervention after a successful round.

    ``expected`` guards against removing a *newer* intervention that was set
    between the snapshot and the consumption (asyncio allows interleaving at
    await points).
    """
    entry = _store.get(_key(chat_id, character_id))
    if entry is None:
        return False
    if expected is not None and entry is not expected:
        return False
    del _store[_key(chat_id, character_id)]
    return True


def remove_intervention(
    chat_id: int,
    character_id: int | None = None,
) -> bool:
    """Manually remove a pending intervention (user cancels it)."""
    return _store.pop(_key(chat_id, character_id), None) is not None


def list_interventions(chat_id: int) -> list[PendingIntervention]:
    """All pending interventions for a chat (chat-wide and per-character)."""
    return [entry for (cid, _char_id), entry in _store.items() if cid == chat_id]


def clear_all() -> None:
    """Reset the store (used by tests)."""
    _store.clear()


async def record_intervention_outcome(
    db,
    chat_id: int,
    characters: list,
    directive: str,
    character_id: int | None = None,
) -> list:
    """Persist a discreet memory fact so affected bots remember the directive.

    Called right before the intervention is consumed, after a fully successful
    round. The fact never mentions the word «вмешательство»: it reads like an
    ordinary story event so it does not break immersion.

    ``source_message_ids`` is left empty on purpose: ``crud.filter_memories_by_witness``
    keeps memories without source references as "direct", so the fact survives even
    for characters who did not perceive the round's messages (isolated bots) — the
    instruction was given to them regardless of perception.
    """
    from . import crud, schemas

    fact = f"Игрок попросил: {directive.strip()}"
    target_ids = [c.id for c in characters if getattr(c, "id", None) is not None]
    if character_id is not None:
        target_ids = [character_id]
    written: list = []
    for cid in target_ids:
        try:
            memory = await crud.create_memory(
                db,
                schemas.MemoryCreate(
                    chat_id=chat_id,
                    character_id=cid,
                    content=fact,
                    category="событие",
                    memory_type="episodic",
                    importance=0.9,
                    witnessed=True,
                ),
                source_message_ids=[],
            )
            if memory is not None:
                written.append(memory)
        except Exception:
            logger.warning(
                "[chat_id=%d] Failed to record intervention outcome for character %s",
                chat_id,
                cid,
            )
    return written
