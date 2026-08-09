"""Одноразовое «Вмешательство» игрока (адресное) для следующего раунда.

Инструкция **не** пишется в историю сообщений и не входит в memory store.
Она живёт в таблицах ``interventions`` / ``intervention_recipients``: получатели
фиксируются в момент создания (PUT) и не пересчитываются при генерации.
Применяется к следующей генерации и потребляется атомарно после полностью
успешного раунда.

State machine: нет инструкции -> создана -> ожидает генерации ->
использована -> удалена.

Роли функций:
- ``set_intervention`` — сохранить/заменить вмешательство с получателями;
- ``list_interventions`` — все pending-вмешательства чата (снапшот раунда);
- ``build_directive_for_character`` / ``build_directives_map`` — per-NPC
  фильтрация текста вмешательства по ``character_id`` ДО формирования prompt
  (без имён и без LLM);
- ``consume_intervention`` — удалить вмешательство по id (identity-safe:
  новая запись, созданная во время генерации, имеет другой id и не удаляется);
- ``record_intervention_outcome`` — незаметный факт памяти только для
  получателей.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PendingIntervention:
    """A single-use directive with a frozen recipient set."""

    id: int
    chat_id: int
    instruction: str
    created_at: datetime
    character_id: int | None = None
    recipient_ids: frozenset[int] = frozenset()


def _from_row(row) -> PendingIntervention:
    from . import crud

    return PendingIntervention(
        id=row.id,
        chat_id=row.chat_id,
        instruction=row.instruction,
        created_at=row.created_at,
        character_id=row.character_id,
        recipient_ids=frozenset(crud._recipient_ids(row)),
    )


async def set_intervention(
    db,
    chat_id: int,
    instruction: str,
    character_id: int | None = None,
    recipient_ids: list[int] | None = None,
) -> PendingIntervention:
    """Store (or replace) a pending one-time intervention with its recipients."""
    from . import crud

    row = await crud.create_intervention(
        db,
        chat_id,
        instruction,
        character_id=character_id,
        recipient_ids=recipient_ids,
    )
    return _from_row(row)


async def list_interventions(db, chat_id: int) -> list[PendingIntervention]:
    """All pending interventions for a chat (chat-wide and per-character)."""
    from . import crud

    rows = await crud.list_interventions(db, chat_id)
    return [_from_row(row) for row in rows]


async def get_chat_wide_intervention(
    db, chat_id: int
) -> PendingIntervention | None:
    """The chat-wide pending intervention, if any."""
    from . import crud

    row = await crud.get_chat_wide_intervention(db, chat_id)
    return _from_row(row) if row is not None else None


async def consume_intervention(db, intervention_id: int) -> bool:
    """Consume (delete) a pending intervention after a successful round.

    Deleting by id is inherently identity-safe: an intervention set while the
    round was generating has a different id and survives.
    """
    from . import crud

    return await crud.delete_intervention(db, intervention_id)


async def remove_chat_wide_intervention(db, chat_id: int) -> bool:
    """Manually remove the chat-wide pending intervention (user cancels it)."""
    from . import crud

    return await crud.delete_chat_wide_intervention(db, chat_id)


async def clear_all(db) -> None:
    """Delete all pending interventions (used by tests)."""
    from . import crud

    await crud.clear_interventions(db)


def build_directive_for_character(
    interventions: list[PendingIntervention], character_id: int
) -> str | None:
    """Joined instruction text for one NPC, or None when it is not a recipient.

    The character hears every intervention whose frozen recipient set contains
    its ``character_id``; instructions are joined in creation order.
    """
    parts = [
        inv.instruction
        for inv in interventions
        if character_id in inv.recipient_ids
    ]
    return "\n\n".join(parts) if parts else None


def build_directives_map(
    interventions: list[PendingIntervention], character_ids: list[int]
) -> dict[int, str | None]:
    """Per-NPC directive map for a round: ``{character_id: text | None}``."""
    return {
        cid: build_directive_for_character(interventions, cid)
        for cid in character_ids
    }


def compute_default_recipients(
    characters: list,
    player_location: str,
    player_location_id=None,
) -> list[int]:
    """NPCs that perceive an intervention occurring at the player's location.

    Deterministic, no LLM: the recipients are computed once at creation time
    from the NPCs co-located with the player right now. A later-arriving NPC
    is NOT a recipient, because the set is frozen at creation.

    Co-location reuses the same canonical identity rules as the round's
    perception (`perception.same_location_identity`): ``location_id`` when both
    sides have one, otherwise normalized string comparison (empty string is the
    shared scene and only matches another shared scene — backwards compatible).
    """
    from .perception import same_location_identity

    recipients: list[int] = []
    for character in characters:
        cid = getattr(character, "id", None)
        if cid is None or getattr(character, "is_player", False):
            continue
        if same_location_identity(
            viewer_location=getattr(character, "location", "") or "",
            event_location=player_location,
            viewer_location_id=getattr(character, "location_id", None),
            event_location_id=player_location_id,
        ):
            recipients.append(cid)
    return recipients


async def record_intervention_outcome(
    db,
    chat_id: int,
    directive: str,
    recipient_ids: list[int] | frozenset[int],
) -> list:
    """Persist a discreet memory fact so affected bots remember the directive.

    Called right before the intervention is consumed, after a fully successful
    round. Only the frozen recipients get the fact. The fact never mentions the
    word «вмешательство»: it reads like an ordinary story event so it does not
    break immersion.

    ``source_message_ids`` is left empty on purpose: ``crud.filter_memories_by_witness``
    keeps memories without source references as "direct", so the fact survives even
    for characters who did not perceive the round's messages (isolated bots) — the
    instruction was given to them regardless of perception.
    """
    from . import crud, schemas

    fact = f"Игрок попросил: {directive.strip()}"
    written: list = []
    for cid in recipient_ids:
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
