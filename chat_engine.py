"""Chat engine: process user messages, generate character replies, extract memories."""

import logging

from sqlalchemy.orm import Session

import crud
import ollama_client
import schemas

logger = logging.getLogger(__name__)


def _format_round_as_text(messages: list) -> str:
    """Format a list of Message ORM objects as a readable dialogue."""
    lines = []
    for m in messages:
        if m.role == "user":
            lines.append(f"Игрок: {m.content}")
        elif m.role == "character":
            name = m.character.name if m.character else "Персонаж"
            lines.append(f"{name}: {m.content}")
        elif m.role == "system":
            lines.append(f"Система: {m.content}")
    return "\n".join(lines)


def extract_memories(db: Session, chat_id: int, round_messages: list) -> None:
    """Analyse the latest round and extract memories for each character.

    Called after all characters have replied.

    Args:
        db: SQLAlchemy session.
        chat_id: Chat to process.
        round_messages: ORM Message list from this round (player + all character replies).
    """
    chat = crud.get_chat(db, chat_id)
    if chat is None:
        return

    model_name = chat.model_name
    characters = crud.get_characters_by_chat(db, chat_id)

    if not characters or len(round_messages) < 2:
        return

    round_text = _format_round_as_text(round_messages)

    for character in characters:
        # Skip extraction if Ollama is down — early return handled in extract_memories_text
        facts = ollama_client.extract_memories_text(
            character_name=character.name,
            character_personality=character.personality,
            character_traits=character.traits,
            model_name=model_name,
            round_history_text=round_text,
        )

        if not facts:
            continue

        for fact in facts:
            crud.create_memory(
                db,
                schemas.MemoryCreate(
                    chat_id=chat_id,
                    character_id=character.id,
                    content=fact,
                ),
            )

        # Enforce memory limit (delete oldest if > MAX_MEMORIES_PER_CHARACTER)
        crud.ensure_memory_limit(db, character.id)

    logger.info(
        "[chat_id=%d] Memory extraction complete for %d characters",
        chat_id,
        len(characters),
    )


def process_user_message(db: Session, chat_id: int, user_text: str) -> list[dict]:
    """Accept a player message and run a generation round.

    Algorithm:
      1. Save player message.
      2. For each character (by order_index):
         a. Get character memories (last 10).
         b. Get pre-round message history.
         c. Build context = history + current round messages.
         d. Call Ollama, save reply.
         e. Append reply to round history.
      3. Run memory extraction for the round.
      4. Return all round messages as dicts.

    Raises:
        ValueError — if chat not found.
        RuntimeError — if Ollama is unavailable.
    """
    chat = crud.get_chat(db, chat_id)
    if chat is None:
        raise ValueError("Чат не найден")

    history_limit = getattr(chat, "max_history_length", 30)

    # 1. Save player message
    user_message = crud.create_message(
        db,
        schemas.MessageCreate(
            chat_id=chat_id,
            role="user",
            content=user_text,
        ),
    )

    round_messages: list = [user_message]

    # 2. Get characters sorted by order_index
    characters = crud.get_characters_by_chat(db, chat_id)

    if not characters:
        logger.warning(
            "[chat_id=%d] No characters in chat — returning only user message", chat_id
        )
        return [
            schemas.MessageRead.model_validate(m).model_dump()
            for m in round_messages
        ]

    # Get history BEFORE this round (last history_limit messages)
    pre_round_messages = crud.get_messages_by_chat(db, chat_id, limit=history_limit)

    for character in characters:
        # a. Character memories (last 10 most recent)
        memories = crud.get_memories_by_character(
            db, character.id, limit=ollama_client.RECENT_MEMORIES_FOR_PROMPT
        )

        # b. Context: pre-round + current round (so each next character sees previous replies)
        context_messages = list(pre_round_messages) + list(round_messages)
        if len(context_messages) > history_limit:
            context_messages = context_messages[-history_limit:]

        # c. Generate
        response_text = ollama_client.generate(
            chat_id=chat_id,
            character=character,
            messages_history=context_messages,
            general_prompt=chat.general_prompt,
            memories=memories,
            max_history_length=history_limit,
        )

        # d. Save character reply
        char_message = crud.create_message(
            db,
            schemas.MessageCreate(
                chat_id=chat_id,
                character_id=character.id,
                role="character",
                content=response_text,
            ),
        )
        round_messages.append(char_message)

    # 3. Memory extraction for this round
    try:
        extract_memories(db, chat_id, round_messages)
    except RuntimeError:
        # Memory extraction errors are non-fatal — log and continue
        logger.warning("[chat_id=%d] Memory extraction failed, skipping", chat_id)

    # 4. Return all round messages
    return [
        schemas.MessageRead.model_validate(m).model_dump()
        for m in round_messages
    ]