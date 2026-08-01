"""Chat engine: process user messages, generate character replies, extract memories."""

import asyncio
import json
import logging
import random
from collections.abc import AsyncIterator
from datetime import datetime
from types import SimpleNamespace

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from . import crud
from . import memory_service
from . import models
from . import ollama_client
from . import perception
from . import relationship_analyzer
from . import relationship_service
from . import schemas
from .config import settings
from .context_builder import ContextBuilder
from .database import AsyncSessionLocal
from .context_state import ctx_state
from .repetition_detector import analyze_response
from .role_isolation import get_other_character_names
from .witness_model import Presence

logger = logging.getLogger(__name__)


def _message_to_dict(msg) -> dict:
    d = schemas.MessageRead.model_validate(msg).model_dump()
    if isinstance(d.get("timestamp"), datetime):
        d["timestamp"] = d["timestamp"].isoformat()
    return d


def _parse_allowed_locations(locations_json: str) -> set[str]:
    """Parse JSON array of allowed locations into a set of normalized strings."""
    import json
    try:
        locs = json.loads(locations_json) if locations_json and locations_json != "[]" else []
        if isinstance(locs, list):
            return {loc.strip().casefold() for loc in locs if loc.strip()}
    except (json.JSONDecodeError, TypeError):
        pass
    return set()


def _is_location_allowed(location: str, allowed: set[str]) -> bool:
    """Check if a location is in the allowed set (case-insensitive)."""
    if not allowed:
        return True  # if no locations defined, everything is allowed
    return location.strip().casefold() in allowed

# Remote communication channel keywords (Russian)
_CHANNEL_PATTERNS: list[tuple[str, list[str]]] = [
    ("magic", ["маги", "заклинани", "телепати", "магическ", "ментальн", "телекин", "мистическ", "закляти"]),
    ("phone", ["звон", "телефон", "мобильн", "набира", "трубк", "вызов", "позвон"]),
    ("radio", ["раци", "передатчик", "приёмник", "эфир", "частота", "радиосвяз"]),
    ("messenger", ["сообщени", "мессенджер", "чат", "электрон", "письм", "написал", "смс", "телеграм", "whatsapp", "telegram"]),
]


_MOVEMENT_VERBS = [
    "иду", "пошёл", "пошла", "пошли", "идёт", "идут", "идём", "идите",
    "направляюсь", "направляется", "направляются", "направился", "направилась", "направились",
    "перемещаюсь", "перемещается", "перемещаются", "переместился", "переместилась", "переместились",
    "ушёл", "ушла", "ушли", "ушел", "ухожу", "уходит", "уходят", "уходим",
    "вышел", "вышла", "вышли", "вышел", "выхожу", "выходит", "выходят", "выходим",
    "зашёл", "зашла", "зашли", "зашёл", "захожу", "заходит", "заходят", "заходим",
    "вхожу", "входит", "входят", "входим",
    "веду", "ведёт", "ведут", "ведём", "ведёшь",
    "отойди", "отходят", "отхожу",
    "схожу", "сходит", "сходят",
    "шагаю", "шагает", "шагают",
    "бегу", "бежит", "бегут",
    "спускаюсь", "спускается", "спускаются", "спустился", "спустилась",
    "поднимаюсь", "поднимается", "поднимаются", "поднялся", "поднялась",
    "возвращаюсь", "возвращается", "возвращаются", "вернулся", "вернулась",
    "прохожу", "проходит", "проходят", "прошёл", "прошла",
    "перехожу", "переходит", "переходят", "перешёл", "перешла",
    "покидаю", "покидает", "покидают", "покинул", "покинула",
]


def _get_character_lines(round_text: str, character_name: str) -> str:
    """Extract only the lines belonging to a specific character from round text.

    The round text has lines in format: "CharacterName: content"
    Returns the concatenated content of all lines for this character (lowercased).
    """
    name_lower = character_name.lower()
    lines = []
    for line in round_text.split('\n'):
        stripped = line.strip()
        if stripped.lower().startswith(name_lower + ':'):
            # Remove the "Name: " prefix and add the rest
            content = stripped[len(name_lower) + 1:].strip()
            if content:
                lines.append(content.lower())
    return '\n'.join(lines)


def _loc_keys(name: str) -> list[str]:
    """Extract keywords from a location name with short prefixes for case-flexion matching.

    Handles Russian declension: e.g. "Квартира" → ["квартира", "кварт"]
    so that "из квартиры" still matches the key "кварт" even though the
    full word "квартира" does not appear in the genitive "квартиры".
    """
    keys: set[str] = set()
    for word in name.split():
        w = word.strip().lower()
        if len(w) <= 2:
            continue
        keys.add(w)
        if len(w) >= 4:
            keys.add(w[:4])
        if len(w) >= 5:
            keys.add(w[:5])
    return list(keys)


def _detect_movement_in_text(
    round_text: str,
    character_name: str,
    new_location: str,
    old_location: str | None,
) -> bool:
    """Check if the round text contains explicit movement for a character.

    Returns True only when the character references leaving the OLD location
    or arriving at the NEW location. Movement verbs alone (e.g. "иду из спальни
    в ванную") are NOT sufficient — the text must connect them to the location
    names to distinguish intra-location movement from location changes.
    """
    if not old_location:
        return True  # first time setting location
    if old_location == new_location:
        return False

    char_text = _get_character_lines(round_text, character_name)
    # Fallback to full text if character-specific lines not found
    if not char_text:
        char_text = round_text.lower()

    text_lower = round_text.lower()
    old_keys = _loc_keys(old_location)
    new_keys = _loc_keys(new_location)

    # ── Departure from old location ────────────────────────────────────
    # Verb: leave / go out / abandon + old-location keyword in text
    _LEAVE_VERBS = (
        "вышел", "вышла", "вышли", "выхожу", "выходит", "выходят",
        "ушёл", "ушла", "ушли", "ухожу", "уходит", "уходят",
        "покида", "покинул", "покинула",
    )
    for verb in _LEAVE_VERBS:
        if verb in char_text and any(kw in char_text for kw in old_keys):
            return True

    # ── Arrival at new location ────────────────────────────────────────
    # Verb: enter / come / arrive + new-location keyword in text
    _ARRIVE_VERBS = (
        "захожу", "заходит", "заходят", "зашёл", "зашла", "зашли",
        "вхожу", "входит", "входят", "вошёл", "вошла", "вошли",
        "прихожу", "приходит", "приходят", "пришёл", "пришла", "пришли",
        "добираюсь", "добирается", "добрался", "добралась",
        "дохожу", "доходит", "дошёл", "дошла",
    )
    for verb in _ARRIVE_VERBS:
        if verb in char_text and any(kw in char_text for kw in new_keys):
            return True

    # ── Directional movement + location keyword ────────────────────────
    # "иду из дома", "вышел на улицу", "направляюсь к лесу"
    _DIR_VERBS = (
        "иду", "пошёл", "пошла", "пошли", "идёт", "идут", "шёл", "шла", "шли",
        "направляюсь", "направляется", "направился", "направилась",
        "вышел", "вышла", "вышли", "выхожу", "выходит", "выходят",
    )
    for verb in _DIR_VERBS:
        if verb in char_text:
            # Away from old: "из/с/от + old_keyword"
            if any(f"{prep} {kw}" in char_text for kw in old_keys for prep in ("из", "с", "от")):
                return True
            # Toward new: "в/на/к + new_keyword"
            if any(f"{prep} {kw}" in char_text for kw in new_keys for prep in ("в", "на", "к")):
                return True

    # ── Fallback: original strict check (verb + location in full text) ─
    for verb in _MOVEMENT_VERBS:
        if verb in text_lower and new_location.lower() in text_lower:
            return True

    return False


def _detect_communication_channel(
    text: str, speaker_name: str, character_names: dict[int, str]
) -> tuple[str, list[int]]:
    """Detect remote communication channel from character response text.

    Scans for channel keywords and identifies the target character by name.
    Returns (channel_type, list_of_target_character_ids).
    """
    if not text:
        return "direct", []

    text_lower = text.lower()
    name_list = list(character_names.values())
    name_lower_map = {name.lower(): cid for cid, name in character_names.items()}

    for channel, keywords in _CHANNEL_PATTERNS:
        if any(kw in text_lower for kw in keywords):
            # Found a remote channel — find which character is being contacted
            targets = []
            for cname_lower, cid in name_lower_map.items():
                cname_original = character_names[cid]
                if cname_original == speaker_name:
                    continue
                if cname_lower in text_lower:
                    targets.append(cid)
            return channel, targets

    return "direct", []
    return schemas.MessageRead.model_validate(message).model_dump(mode="json")


def _character_to_snapshot(character) -> dict:
    return schemas.CharacterRead.model_validate(character).model_dump(mode="python")


async def process_user_message_streaming(
    client: httpx.AsyncClient,
    db: AsyncSession,
    chat_id: int,
    user_text: str,
    *,
    visibility: str | None = None,
    target_character_ids: list[int] | None = None,
) -> AsyncIterator[dict]:
    """Process user message with per-character generation and perception filtering.
    
    Uses a single database transaction for the entire round (batch commit).
    If any character generation fails, the entire round is rolled back.
    """
    chat = await crud.get_chat(db, chat_id)
    if chat is None:
        raise ValueError("Чат не найден")

    history_limit = getattr(chat, "max_history_length", settings.default_history_length)
    enable_thinking = bool(getattr(chat, "thinking_mode", settings.enable_thinking))
    player_location = getattr(chat, "player_location", "") or ""
    chat_locations = getattr(chat, "locations", "") or "[]"

    context_enabled = bool(settings.context_enabled)
    # Wide history window for retrieval when the Context Builder is active;
    # CONTEXT_HISTORY_LOAD_CAP is only a safety cap, not a semantic boundary.
    window_limit = (
        settings.context_history_load_cap if context_enabled else history_limit
    )
    pre_round_messages = await crud.get_messages_by_chat(db, chat_id, window_limit)

    event_visibility = visibility or settings.default_event_visibility
    event_targets = list(target_character_ids or [])

    user_message = await crud.create_message(
        db,
        schemas.MessageCreate(
            chat_id=chat_id,
            role="user",
            content=user_text,
            visibility=event_visibility,
            location=player_location,
            target_character_ids=event_targets,
        ),
    )

    round_messages: list = [user_message]
    yield {"type": "message", "message": _message_to_dict(user_message)}

    # Load NPCs for generation, all characters (incl. player) for relationships
    all_characters = await crud.get_characters_by_chat(db, chat_id, include_player=True)
    characters = [c for c in all_characters if not c.is_player]  # NPCs only for generation

    if not characters:
        logger.warning("[chat_id=%d] No characters in chat", chat_id)
        return

    character_names = {c.id: c.name for c in all_characters}  # includes player
    character_ids = [c.id for c in characters]  # NPCs only
    character_locations = {
        c.id: getattr(c, "location", "") or "" for c in characters
    }

    # Persist presence for the user event immediately (before any character reply)
    await crud.compute_and_save_presence_for_message(
        db,
        user_message,
        characters,
        character_names,
    )

    # Get scene state for this round (P3) — needed for the retrieval query too
    scene_state = await crud.get_scene_state_with_presence(db, chat_id)

    # Phase 6: Extract current stagnation round count from scene state
    stagnation_rounds = 0
    if scene_state and scene_state.custom_state:
        stagnation_rounds = scene_state.custom_state.stagnation_rounds or 0

    # Build context for relevant memory selection (P1: BM25 ranking for fact accuracy)
    # Compact query: user message + recent dialogue tail + scene state (TZ §18).
    context_text = user_text
    if pre_round_messages:
        recent_context = pre_round_messages[-3:]
        context_text += "\n" + "\n".join(
            f"{getattr(m, 'role', 'unknown')}: {getattr(m, 'content', '')[:120]}"
            for m in recent_context
        )
    if scene_state is not None:
        if getattr(scene_state, "time_of_day", ""):
            context_text += f"\nВремя: {scene_state.time_of_day}"
        custom_state = getattr(scene_state, "custom_state", None)
        if isinstance(custom_state, str):
            try:
                custom_state = json.loads(custom_state)
            except (json.JSONDecodeError, TypeError):
                custom_state = None
        if isinstance(custom_state, dict):
            if custom_state.get("active_goal"):
                context_text += f"\nЦель: {custom_state['active_goal']}"
            if custom_state.get("mood"):
                context_text += f"\nАтмосфера: {custom_state['mood']}"
            tension = custom_state.get("tension", 0) or 0
            if tension > 0:
                context_text += f"\nНапряжение: {tension:.1f}"
    context_text = context_text[:600]

    summaries_by_character = await crud.get_summaries_for_characters(
        db,
        character_ids,
    )
    # Convert summaries to {id: content} for BM21 scoring augmentation
    summary_texts: dict[int, str] = {
        cid: s.content for cid, s in summaries_by_character.items() if s and s.content
    }

    # With the Context Builder active, retrieve a larger candidate set and let
    # the builder's token-aware selection pick the final memories per character.
    memory_top_k = (
        settings.context_retrieval_candidates
        if context_enabled
        else settings.memory_relevance_top_k
    )

    # Hybrid retrieval: BM25 + Vector with RRF fusion (P3)
    if settings.embedding_enabled:
        memories_by_character = await crud.get_hybrid_memories_for_characters(
            db,
            character_ids,
            context_text,
            memory_top_k,
            character_summaries=summary_texts,
        )
    else:
        memories_by_character = await crud.get_relevant_memories_for_characters(
            db,
            character_ids,
            context_text,
            memory_top_k,
            character_summaries=summary_texts,
        )

    context_messages = list(pre_round_messages) + list(round_messages)
    if len(context_messages) > history_limit:
        context_messages = context_messages[-history_limit:]

    # Pre-compute dynamic relationships block for each NPC (includes player relationships)
    relationships_blocks: dict[int, str] = {}
    try:
        for c in characters:
            rel_block = await relationship_service.build_relationships_block(
                db, chat_id, c.id, c.name, character_names,
                max_events=settings.relationship_max_events_in_prompt,
            )
            relationships_blocks[c.id] = rel_block
    except Exception as exc:
        logger.warning("[chat_id=%d] Failed to build relationships block: %s", chat_id, exc)
        relationships_blocks = {c.id: "" for c in characters}

    # Top-K behavior drivers per NPC (deterministic tendencies, Sprint 1 п.3-4)
    drivers_blocks: dict[int, str] = {}
    try:
        for c in characters:
            drivers_blocks[c.id] = await relationship_service.build_behavior_drivers_block(
                db, chat_id, c.id, c.name, character_names,
            )
    except Exception as exc:
        logger.warning("[chat_id=%d] Failed to build behavior drivers block: %s", chat_id, exc)
        drivers_blocks = {c.id: "" for c in characters}

    # Open issues data block per NPC (Sprint 1 п.5-6, docs/relations.md §14)
    open_issues_blocks: dict[int, str] = {}
    try:
        for c in characters:
            open_issues_blocks[c.id] = await relationship_service.build_open_issues_block(
                db, chat_id, c.id, c.name, character_names,
            )
    except Exception as exc:
        logger.warning("[chat_id=%d] Failed to build open issues block: %s", chat_id, exc)
        open_issues_blocks = {c.id: "" for c in characters}

    # Weighted proactive boost per NPC (Sprint 1 п.7, docs/relations.md §7.4):
    # deterministic from open-issue importance & salience, raises the chance of
    # a proactive action during generation.
    proactive_boosts: dict[int, float] = {}
    try:
        for c in characters:
            proactive_boosts[c.id] = await relationship_service.compute_proactive_boost(
                db, chat_id, c.id,
            )
    except Exception as exc:
        logger.warning("[chat_id=%d] Failed to compute proactive boost: %s", chat_id, exc)
        proactive_boosts = {c.id: 0.0 for c in characters}

    # Track prior replies in this round for anti-mimicry (P2)
    prior_replies: list[tuple[str, str]] = []

    # Reusable token-aware context builder (per-character contexts A ≠ B)
    context_builder = ContextBuilder()

    for current_character in characters:
        other_names = get_other_character_names(characters, current_character.id)
        summary_obj = summaries_by_character.get(current_character.id)
        summary_text = summary_obj.content if summary_obj else None

        history_message_ids = [
            message.id for message in context_messages if message.id is not None
        ]
        # Fresh presence map including events already saved this round
        presence_map = await crud.get_presence_map(
            db,
            history_message_ids,
            current_character.id,
        )

        # Determine effective prior replies based on character's presence
        # Characters with presence "absent" or "mentioned" cannot perceive prior replies
        current_presence = presence_map.get(current_character.id, "present")
        if current_presence in ("present", "told"):
            effective_prior_replies = prior_replies
        else:
            effective_prior_replies = []

        # Assemble token-aware context within the budget (TZ §11–§21)
        built_context = None
        if context_enabled:
            max_tokens = (
                getattr(chat, "max_context_tokens", None)
                or settings.max_context_tokens
            )
            built_context = await context_builder.build(
                db=db,
                chat_id=chat_id,
                character=current_character,
                user_message=user_text,
                general_prompt=chat.general_prompt,
                messages_window=pre_round_messages,
                round_messages=round_messages,
                character_names=character_names,
                character_locations=character_locations,
                summary=summary_text,
                summary_through_message_id=(
                    getattr(summary_obj, "through_message_id", None)
                    if summary_obj is not None
                    else None
                ),
                memories=memories_by_character.get(current_character.id, []),
                scene_state=scene_state,
                present_character_names=None,
                relationships_block=relationships_blocks.get(
                    current_character.id, ""
                ),
                locations=chat_locations,
                stagnation_rounds=stagnation_rounds,
                viewer_location=character_locations.get(
                    current_character.id, ""
                ),
                prior_replies=effective_prior_replies,
                is_isolated=(
                    character_locations.get(current_character.id, "")
                    != player_location
                ),
                max_tokens=max_tokens,
            )

        response_text = ""
        try:
            async for event in ollama_client.generate(
                client=client,
                chat_id=chat.id,
                character=current_character,
                messages_history=context_messages,
                general_prompt=chat.general_prompt,
                memories=memories_by_character.get(current_character.id, []),
                other_character_names=other_names,
                max_history_length=history_limit,
                model_name=chat.model_name,
                character_names=character_names,
                summary=summary_text,
                viewer_character_id=current_character.id,
                presence_map=presence_map,
                same_round_message_ids=None,
                enable_thinking=enable_thinking,
                viewer_location=character_locations.get(current_character.id, ""),
                character_locations=character_locations,
                prior_replies=effective_prior_replies,
                scene_state=scene_state,
                present_character_names=None,
                stagnation_rounds=stagnation_rounds,
                is_isolated=(character_locations.get(current_character.id, "") != player_location),
                locations=chat_locations,
                relationships_block=relationships_blocks.get(current_character.id, ""),
                behavior_drivers_block=drivers_blocks.get(current_character.id, ""),
                open_issues_block=open_issues_blocks.get(current_character.id, ""),
                proactive_boost=proactive_boosts.get(current_character.id, 0.0),
                built_context=built_context,
            ):
                if event["type"] == "token":
                    # Forward token to SSE with character_id for frontend avatar
                    yield {"type": "token", "text": event["text"], "character_id": current_character.id}
                elif event["type"] == "response":
                    response_text = event["text"]
        except RuntimeError as exc:
            logger.error(
                "[chat_id=%d] Generation failed for %s: %s",
                chat_id,
                current_character.name,
                exc,
            )
            response_text = f"*[{current_character.name} молчит, не в силах ответить]*"

        char_location = getattr(current_character, "location", "") or ""
        # Detect remote communication channel from response text
        msg_channel, msg_targets = _detect_communication_channel(
            response_text, current_character.name, character_names
        )
        msg_visibility = settings.default_event_visibility
        if msg_channel != "direct" and msg_targets:
            msg_visibility = "targeted"
        char_message = await crud.create_message(
            db,
            schemas.MessageCreate(
                chat_id=chat_id,
                character_id=current_character.id,
                role="character",
                content=response_text,
                visibility=msg_visibility,
                location=char_location,
                target_character_ids=msg_targets,
                channel=msg_channel,
            ),
        )
        # Perception for this reply before the next character generates
        await crud.compute_and_save_presence_for_message(
            db,
            char_message,
            characters,
            character_names,
        )

        # Add this character's reply to prior_replies for subsequent characters
        # Only if they can be perceived (present or told)
        if response_text and current_presence in ("present", "told"):
            prior_replies.append((current_character.name, response_text))

        round_messages.append(char_message)
        context_messages.append(char_message)
        if len(context_messages) > history_limit:
            context_messages = context_messages[-history_limit:]
        yield {"type": "message", "message": _message_to_dict(char_message)}

    character_snapshots = [_character_to_snapshot(c) for c in characters]
    round_snapshots = [_message_to_dict(m) for m in round_messages]

    # Final pass ensures all round presence rows are consistent
    await crud.compute_and_save_presence_for_round(
        db,
        round_messages,
        character_ids,
        character_names,
        characters=characters,
        character_locations=character_locations,
    )

    # Extract and update scene state from round history (P3)
    scene_update = None
    try:
        round_history_text = "\n".join(
            f"{m.character.name if m.character else 'Игрок'}: {m.content}"
            for m in round_messages
        )
        scene_update = await ollama_client.extract_scene_state(
            client=client,
            model_name=chat.model_name,
            round_history_text=round_history_text,
            current_scene_state=scene_state,
            character_names=character_names,
            locations=chat_locations,
            num_ctx=ctx_state.get(chat_id),
        )
        if scene_update:
            # Save time-related data first (without character_locations — confirmed later)
            scene_update_no_locs = {
                k: v for k, v in scene_update.items() if k != "character_locations"
            }
            if scene_update_no_locs:
                await crud.upsert_scene_state(db, chat_id, schemas.SceneStateUpdate(**scene_update_no_locs))

            # Update per-character locations from scene extraction
            confirmed_locs: dict[str, str] = {}
            char_locs_by_name = scene_update.get("character_locations", {})
            if char_locs_by_name:
                name_to_id = {name: cid for cid, name in character_names.items()}
                loc_updates: dict[int, str] = {}
                allowed_locs = _parse_allowed_locations(chat_locations)
                old_locs = scene_state.character_locations if scene_state else {}
                for cname, loc in char_locs_by_name.items():
                    if cname in name_to_id and loc.strip():
                        new_loc = loc.strip()
                        cid = name_to_id[cname]
                        old_loc = old_locs.get(cname, character_locations.get(cid, ""))
                        if not _is_location_allowed(new_loc, allowed_locs):
                            logger.info(
                                "[chat_id=%d] Ignoring disallowed location '%s' for %s",
                                chat_id, new_loc, cname,
                            )
                        elif _detect_movement_in_text(round_history_text, cname, new_loc, old_loc):
                            loc_updates[cid] = new_loc
                            confirmed_locs[cname] = new_loc
                        else:
                            logger.info(
                                "[chat_id=%d] No movement detected for %s, keeping '%s'",
                                chat_id, cname, old_loc,
                            )
                if loc_updates:
                    await crud.update_character_locations_batch(db, chat_id, loc_updates)
                    for cid, loc in loc_updates.items():
                        character_locations[cid] = loc

            # Save only the confirmed character_locations to scene state
            if confirmed_locs:
                await crud.upsert_scene_state(
                    db, chat_id,
                    schemas.SceneStateUpdate(character_locations=confirmed_locs),
                )

            # Announce location changes as system messages (Part B1)
            old_locations = scene_state.character_locations if scene_state else {}
            name_to_id_rev = {cid: name for cid, name in character_names.items()}
            for cid, new_loc in character_locations.items():
                cname = name_to_id_rev.get(cid, "")
                if not cname:
                    continue
                old_loc = old_locations.get(cname, "")
                if old_loc and new_loc and old_loc != new_loc:
                    loc_msg_text = f"*{cname} переместился в {new_loc}*"
                    loc_message = await crud.create_message(
                        db,
                        schemas.MessageCreate(
                            chat_id=chat_id,
                            role="system",
                            content=loc_msg_text,
                            visibility="global",
                        ),
                    )
                    yield {"type": "message", "message": _message_to_dict(loc_message)}
                    round_messages.append(loc_message)
                    context_messages.append(loc_message)
                    if len(context_messages) > history_limit:
                        context_messages = context_messages[-history_limit:]
    except Exception as exc:
        logger.warning("[chat_id=%d] Scene state extraction failed: %s", chat_id, exc)

    # Relationship analysis hook (non-blocking, background)
    if settings.relationship_analyzer_enabled:
        round_snapshots = [
            {
                "id": getattr(m, "id", None),
                "role": getattr(m, "role", ""),
                "character_id": getattr(m, "character_id", None),
                "content": getattr(m, "content", "") or "",
                "location": getattr(m, "location", "") or "",
                "visibility": (
                    getattr(m, "visibility", None)
                    or settings.default_event_visibility
                ),
                "channel": getattr(m, "channel", None) or "direct",
                "target_character_ids": getattr(m, "target_character_ids", "[]"),
            }
            for m in round_messages
        ]
        character_snapshots = [
            {
                "id": c.id,
                "name": c.name,
                "location": getattr(c, "location", "") or "",
            }
            for c in characters
        ]
        asyncio.create_task(
            _analyze_and_update_relationships(
                client, chat_id, chat.model_name,
                round_snapshots, character_snapshots,
            )
        )

    # Phase 6: Track stagnation across rounds and force time advance
    try:
        # Use pre-round custom_state (LLM no longer returns custom_state)
        if scene_state and scene_state.custom_state:
            custom_state_raw = scene_state.custom_state.model_dump()
        else:
            custom_state_raw = {}
        stagnation_rounds = int(custom_state_raw.get("stagnation_rounds", 0))
        round_count = int(custom_state_raw.get("round_count", 0)) + 1

        # Detect stagnation from character responses this round
        char_responses = [m for m in round_messages if getattr(m, "role", "") == "character"]
        if char_responses:
            any_stagnant = False
            for m in char_responses:
                cid = getattr(m, "character_id", None)
                if cid is None:
                    continue
                analysis = analyze_response(
                    getattr(m, "content", ""),
                    character_id=cid,
                    messages=pre_round_messages,
                    character_names=character_names,
                )
                if analysis.stagnation:
                    any_stagnant = True
                    break
            if any_stagnant:
                stagnation_rounds += 1
            else:
                stagnation_rounds = 0
        else:
            stagnation_rounds = 0

        # Force time advance every N rounds (use LLM-updated time if available)
        updated_time = (scene_update or {}).get("time_of_day", "")
        if not updated_time and scene_state:
            updated_time = scene_state.time_of_day
        time_of_day = updated_time or ""
        if (
            settings.scene_advancement_enabled
            and round_count > 1
            and round_count % settings.time_advance_interval == 0
        ):
            time_options = ["Утро", "День", "Вечер", "Ночь"]
            if time_of_day and time_of_day in time_options:
                idx = time_options.index(time_of_day)
                time_of_day = time_options[(idx + 1) % len(time_options)]
            else:
                time_of_day = random.choice(time_options)

        # Build custom_state updates
        custom_state_raw["stagnation_rounds"] = stagnation_rounds
        custom_state_raw["round_count"] = round_count
        custom_state_update = schemas.SceneCustomState(**custom_state_raw)

        update_kwargs: dict = {"custom_state": custom_state_update}
        if time_of_day:
            update_kwargs["time_of_day"] = time_of_day

        await crud.upsert_scene_state(db, chat_id, schemas.SceneStateUpdate(**update_kwargs))
    except Exception as exc:
        logger.warning("[chat_id=%d] Stagnation tracking failed: %s", chat_id, exc)

    # Post-round memory job (outside transaction, background)
    asyncio.create_task(
        memory_service.process_post_round(
            client,
            chat_id,
            round_snapshots,
            character_snapshots,
            chat.model_name,
        )
    )


async def _analyze_and_update_relationships(
    client: httpx.AsyncClient,
    chat_id: int,
    model_name: str,
    round_snapshots: list[dict],
    character_snapshots: list[dict],
) -> None:
    """Background task: analyze relationships for all character pairs and apply deltas.

    Only NPCs are analyzed as relationship *sources*. The player is a valid
    *target* (bots -> player) but never a source (player -> bots is not tracked).

    For each pair only the relevant excerpt of the round is sent to the LLM
    (filtered by perception), so an event aimed at someone else cannot be
    misattributed to unrelated pairs. Pairs without any interaction evidence
    are skipped entirely. Witness/reflection-only evidence is capped and never
    changes the relationship type.

    Opens its own DB session instead of borrowing the caller's, so the
    connection is always returned to the pool when the task finishes.
    """
    try:
        async with AsyncSessionLocal() as db:
            player = await crud.get_player_character(db, chat_id)
            player_id = player.id if player else None

            # Rebuild lightweight character objects from precomputed snapshots
            all_chars = [
                SimpleNamespace(
                    id=snap["id"],
                    name=snap["name"],
                    location=snap.get("location") or "",
                    is_player=False,
                )
                for snap in character_snapshots
            ]
            if player:
                all_chars.append(player)

            character_names = {c.id: c.name for c in all_chars}
            character_locations = {
                c.id: getattr(c, "location", "") or "" for c in all_chars
            }

            # Only NPCs are sources; targets include the player
            sources = [c for c in all_chars if not getattr(c, "is_player", False)]

            # One round_id per round (docs/relations.md §6; the user-message
            # anchor is a separate Sprint 1 item, kept here for forward-compat).
            round_id = f"round_{chat_id}_{datetime.utcnow().isoformat()}"

            # Issues mentioned this round: those passed to the analyzer for an
            # analyzed pair, plus those selected into each source's
            # generation-context `<open_issue data>` block (§7.4 salience).
            mentioned_issue_ids: set[int] = set()

            for source_char in sources:
                for target_char in all_chars:
                    if source_char.id == target_char.id:
                        continue

                    pair_ctx = _build_pair_relationship_context(
                        round_snapshots,
                        source_char,
                        target_char,
                        character_names,
                        character_locations,
                        player_id=player_id,
                    )

                    if (
                        not pair_ctx["any_evidence"]
                        and settings.relationship_analyze_only_interacting_pairs
                    ):
                        continue

                    rel = await relationship_service.get_relationship(
                        db, source_char.id, target_char.id,
                    )

                    if rel is None:
                        rel = await relationship_service.get_or_create_relationship(
                            db, chat_id, source_char.id, target_char.id,
                        )

                    recent_events = await relationship_service.get_recent_events(
                        db, rel, limit=settings.relationship_max_events_in_prompt,
                    )
                    events_text = "\n".join(
                        f"  - {e.description}" for e in recent_events if e.description
                    )

                    open_issues = await relationship_service.list_open_issues(db, rel)
                    open_issues_payload = [
                        {
                            "id": issue.id,
                            "issue_type": issue.issue_type,
                            "text": issue.text,
                        }
                        for issue in open_issues
                    ]
                    mentioned_issue_ids.update(issue.id for issue in open_issues)

                    deltas = await relationship_analyzer.analyze_relationships(
                        client=client,
                        model_name=model_name,
                        source_name=source_char.name,
                        target_name=target_char.name,
                        current_type=rel.relationship_type,
                        affection=rel.affection,
                        trust=rel.trust,
                        attraction=rel.attraction,
                        resentment=rel.resentment,
                        jealousy=rel.jealousy,
                        recent_events_text=events_text,
                        round_text=pair_ctx["excerpt"],
                        source_character_id=source_char.id,
                        target_character_id=target_char.id,
                        interaction_summary=pair_ctx["interaction_summary"],
                        direct_interaction=pair_ctx["direct_interaction"],
                        observed_target=pair_ctx["observed_target"],
                        open_issues=open_issues_payload,
                    )

                    for delta in deltas:
                        delta = _constrain_pair_delta(delta, rel, pair_ctx)
                        await relationship_service.apply_delta(
                            db, delta, chat_id, round_id=round_id,
                        )

            # Issues selected into per-source generation contexts count as
            # mentioned even when the pair itself had no analysis evidence.
            if settings.relationship_issues_enabled:
                for source_char in sources:
                    for issue in await relationship_service.list_top_open_issues_for_character(
                        db, chat_id, source_char.id,
                        limit=settings.relationship_max_issues_in_prompt,
                    ):
                        mentioned_issue_ids.add(issue.id)

            # Deterministic salience tick: advance counters for unmentioned
            # open issues, reset mentioned ones (§7.4, Sprint 1 п.7).
            try:
                await relationship_service.tick_open_issues(
                    db, chat_id, round_id=round_id, mentioned_ids=mentioned_issue_ids,
                )
            except Exception as exc:
                logger.warning(
                    "[chat_id=%d] Issue salience tick failed: %s", chat_id, exc
                )

            logger.info("[chat_id=%d] Relationship analysis complete", chat_id)
    except Exception:
        logger.exception("[chat_id=%d] Relationship analysis failed", chat_id)


def _text_mentions_name(content: str, name: str) -> bool:
    if not name or not content:
        return False
    import re
    pattern = rf"(?<!\w){re.escape(name)}(?!\w)"
    return bool(re.search(pattern, content, flags=re.IGNORECASE))


def _build_pair_relationship_context(
    round_snapshots: list[dict],
    source,
    target,
    character_names: dict[int, str],
    character_locations: dict[int, str],
    player_id: int | None = None,
    max_lines: int | None = None,
) -> dict:
    """Build a pair-specific excerpt of the round for relation source -> target.

    Only lines the *source* could perceive and that concern the *target* are
    kept, so events aimed at other characters are not misattributed. Each line
    is annotated with the speaker and addressees.

    Returns: excerpt, interaction_summary, direct_interaction, observed_target,
    any_evidence.
    """
    if max_lines is None:
        max_lines = settings.relationship_max_pair_context_lines

    source_name = character_names.get(source.id, f"ID:{source.id}")
    target_name = character_names.get(target.id, f"ID:{target.id}")
    source_location = character_locations.get(source.id, "") or ""

    co_located_ids = [
        cid for cid, loc in character_locations.items()
        if (loc or "") == source_location and cid != source.id
    ]
    co_present = target.id in co_located_ids
    only_two_present = co_present and len(co_located_ids) == 1

    lines: list[str] = []
    summary: list[str] = []
    direct_interaction = False
    observed_target = False

    for snap in round_snapshots:
        role = (snap.get("role") or "").strip().lower()
        if role == "system":
            continue

        author_id = snap.get("character_id")
        if role == "user":
            author_id = player_id
        if author_id is None:
            continue

        content = snap.get("content") or ""
        targets = perception.parse_target_ids(snap.get("target_character_ids"))

        if author_id == source.id:
            # Source's own speech. Direct when explicitly addressed or when the
            # target is present (they are actually talking to/with them).
            # A name mention without co-presence is only reflection (weak).
            explicit_address = target.id in targets
            mentions_target = _text_mentions_name(content, target_name)
            if explicit_address or (co_present and mentions_target) or only_two_present:
                direct_interaction = True
            elif mentions_target:
                observed_target = True
            else:
                continue
        elif author_id == target.id:
            # Target's speech: direct when addressed to the source or said
            # face-to-face; otherwise observed through the source's perception.
            explicit_address = source.id in targets
            mentions_source = _text_mentions_name(content, source_name)
            presence = perception.can_character_perceive_event(
                viewer_character_id=source.id,
                viewer_location=source_location,
                event=snap,
                viewer_name=source_name,
            )[0]
            if explicit_address or (co_present and mentions_source) or only_two_present:
                direct_interaction = True
            elif presence != "absent":
                observed_target = True
            else:
                continue
        else:
            # Third party speaks; relevant only if perceived and about the target
            presence = perception.can_character_perceive_event(
                viewer_character_id=source.id,
                viewer_location=source_location,
                event=snap,
                viewer_name=source_name,
            )[0]
            if presence == "absent":
                continue
            involves_target = (target.id in targets) or _text_mentions_name(content, target_name)
            if not involves_target:
                continue
            observed_target = True

        speaker = character_names.get(author_id, f"ID:{author_id}")
        addressee_names = [character_names.get(t, f"ID:{t}") for t in targets]
        addressee_text = ", ".join(addressee_names) if addressee_names else "(всем)"
        lines.append(f"{speaker} (id={author_id}) -> {addressee_text}: {content}")
        summary.append(f"{speaker} -> {addressee_text}")

    excerpt = "\n".join(lines[-max_lines:])
    return {
        "excerpt": excerpt,
        "interaction_summary": "\n".join(summary[:max_lines]) or "взаимодействия не было",
        "direct_interaction": direct_interaction,
        "observed_target": observed_target,
        "any_evidence": bool(excerpt.strip()),
    }


def _constrain_pair_delta(
    delta: schemas.RelationshipDelta,
    rel: models.CharacterRelationship,
    pair_ctx: dict,
) -> schemas.RelationshipDelta:
    """Limit deltas for pairs without direct interaction.

    Witness/reflection-only evidence gets a smaller cap and cannot change the
    relationship type (unless configured otherwise).
    """
    if pair_ctx["direct_interaction"]:
        return delta
    cap = settings.relationship_reflection_delta_cap
    updates: dict = {
        "delta_affection": max(-cap, min(cap, delta.delta_affection)),
        "delta_trust": max(-cap, min(cap, delta.delta_trust)),
        "delta_attraction": max(-cap, min(cap, delta.delta_attraction)),
        "delta_resentment": max(-cap, min(cap, delta.delta_resentment)),
        "delta_jealousy": max(-cap, min(cap, delta.delta_jealousy)),
    }
    if (
        settings.relationship_type_change_requires_interaction
        and delta.relationship_type != rel.relationship_type
    ):
        updates["relationship_type"] = rel.relationship_type
    return delta.model_copy(update=updates)


async def process_user_message(
    client: httpx.AsyncClient,
    db: AsyncSession,
    chat_id: int,
    user_text: str,
    *,
    visibility: str | None = None,
    target_character_ids: list[int] | None = None,
) -> list[dict]:
    messages = []
    async for event in process_user_message_streaming(
        client,
        db,
        chat_id,
        user_text,
        visibility=visibility,
        target_character_ids=target_character_ids,
    ):
        if event.get("type") == "message":
            messages.append(event["message"])
    return messages


async def regenerate_message_streaming(
    client: httpx.AsyncClient,
    db: AsyncSession,
    chat_id: int,
    message_id: int,
) -> AsyncIterator[dict]:
    """Regenerate a single character reply with the context of its round.

    Only the *last* message of a character in the *last* round can be
    regenerated. The old reply stays in the DB until the new one is saved,
    so a failed generation never loses the previous text.

    Yields SSE-like dict events:
      {"type": "token", "text": "...", "character_id": N}
      {"type": "message", "message": MessageRead}
    Raises ValueError for invalid input, RuntimeError on generation failure.
    """
    target = await db.get(models.Message, message_id)
    if target is None or target.chat_id != chat_id:
        raise ValueError("Сообщение не найдено")
    if target.role != "character":
        raise ValueError("Перегенерировать можно только ответ персонажа")

    chat = await crud.get_chat(db, chat_id)
    if chat is None:
        raise ValueError("Чат не найден")

    messages = await crud.get_messages_by_chat(db, chat_id)
    idx = next((i for i, m in enumerate(messages) if m.id == message_id), None)
    if idx is None:
        raise ValueError("Сообщение не найдено")

    # Only the character's last message may be regenerated
    for m in messages[idx + 1:]:
        if m.character_id == target.character_id:
            raise ValueError(
                "Можно перегенерировать только последнее сообщение персонажа"
            )
        if m.role == "user":
            raise ValueError(
                "Можно перегенерировать только сообщение из последнего раунда"
            )

    # The player message that started the round
    user_idx = None
    for i in range(idx - 1, -1, -1):
        if messages[i].role == "user":
            user_idx = i
            break
    if user_idx is None:
        raise ValueError("Сообщение не относится к раунду")

    user_message = messages[user_idx]
    pre_round_messages = messages[:user_idx]
    round_messages = messages[user_idx:idx]

    history_limit = getattr(chat, "max_history_length", settings.default_history_length)
    context_messages = list(pre_round_messages) + list(round_messages)
    if len(context_messages) > history_limit:
        context_messages = context_messages[-history_limit:]

    character = await crud.get_character(db, target.character_id)
    if character is None:
        raise ValueError("Персонаж не найден")

    all_characters = await crud.get_characters_by_chat(db, chat_id, include_player=True)
    characters = [c for c in all_characters if not c.is_player]
    character_names = {c.id: c.name for c in all_characters}
    char_by_id = {c.id: c for c in all_characters}
    character_locations = {
        c.id: getattr(c, "location", "") or "" for c in characters
    }
    player_location = getattr(chat, "player_location", "") or ""
    chat_locations = getattr(chat, "locations", "") or "[]"

    scene_state = await crud.get_scene_state_with_presence(db, chat_id)
    stagnation_rounds = 0
    if scene_state and scene_state.custom_state:
        stagnation_rounds = scene_state.custom_state.stagnation_rounds or 0

    # Memory retrieval for this character (mirrors the main round path)
    context_text = user_message.content or ""
    if pre_round_messages:
        recent_context = pre_round_messages[-3:]
        context_text += "\n" + "\n".join(
            f"{getattr(m, 'role', 'unknown')}: {getattr(m, 'content', '')[:120]}"
            for m in recent_context
        )
    if scene_state is not None:
        if getattr(scene_state, "time_of_day", ""):
            context_text += f"\nВремя: {scene_state.time_of_day}"
        custom_state = getattr(scene_state, "custom_state", None)
        if isinstance(custom_state, str):
            try:
                custom_state = json.loads(custom_state)
            except (json.JSONDecodeError, TypeError):
                custom_state = None
        if isinstance(custom_state, dict):
            if custom_state.get("active_goal"):
                context_text += f"\nЦель: {custom_state['active_goal']}"
            if custom_state.get("mood"):
                context_text += f"\nАтмосфера: {custom_state['mood']}"
            tension = custom_state.get("tension", 0) or 0
            if tension > 0:
                context_text += f"\nНапряжение: {tension:.1f}"
    context_text = context_text[:600]

    summary_obj = await crud.get_character_summary(db, character.id)
    summary_text = summary_obj.content if summary_obj else None

    context_enabled = bool(settings.context_enabled)
    memory_top_k = (
        settings.context_retrieval_candidates
        if context_enabled
        else settings.memory_relevance_top_k
    )
    summary_texts: dict[int, str] = {character.id: summary_text} if summary_text else {}
    if settings.embedding_enabled:
        memories = (
            await crud.get_hybrid_memories_for_characters(
                db,
                [character.id],
                context_text,
                memory_top_k,
                character_summaries=summary_texts,
            )
        ).get(character.id, [])
    else:
        memories = (
            await crud.get_relevant_memories_for_characters(
                db,
                [character.id],
                context_text,
                memory_top_k,
                character_summaries=summary_texts,
            )
        ).get(character.id, [])

    # Relationships block for this character
    relationships_block = ""
    try:
        relationships_block = await relationship_service.build_relationships_block(
            db, chat_id, character.id, character.name, character_names,
            max_events=settings.relationship_max_events_in_prompt,
        )
    except Exception as exc:
        logger.warning("[chat_id=%d] Failed to build relationships block: %s", chat_id, exc)

    # Behavior drivers block for this character (Sprint 1 п.3-4)
    drivers_block = ""
    try:
        drivers_block = await relationship_service.build_behavior_drivers_block(
            db, chat_id, character.id, character.name, character_names,
        )
    except Exception as exc:
        logger.warning("[chat_id=%d] Failed to build behavior drivers block: %s", chat_id, exc)

    # Open issues data block for this character (Sprint 1 п.5-6)
    open_issues_block = ""
    try:
        open_issues_block = await relationship_service.build_open_issues_block(
            db, chat_id, character.id, character.name, character_names,
        )
    except Exception as exc:
        logger.warning("[chat_id=%d] Failed to build open issues block: %s", chat_id, exc)

    # Weighted proactive boost for this character (Sprint 1 п.7, §7.4)
    proactive_boost = 0.0
    try:
        proactive_boost = await relationship_service.compute_proactive_boost(
            db, chat_id, character.id,
        )
    except Exception as exc:
        logger.warning("[chat_id=%d] Failed to compute proactive boost: %s", chat_id, exc)

    # Presence & prior replies visible to this character
    history_message_ids = [m.id for m in context_messages if m.id is not None]
    presence_map = await crud.get_presence_map(db, history_message_ids, character.id)
    current_presence = presence_map.get(character.id, "present")

    prior_replies: list[tuple[str, str]] = []
    if current_presence in ("present", "told"):
        for prior in round_messages:
            if prior.role != "character" or prior.id == message_id:
                continue
            prior_char = char_by_id.get(prior.character_id)
            if prior_char is None:
                continue
            prior_presence = await crud.get_presence_map(
                db, [user_message.id], prior.character_id
            )
            if prior_presence.get(user_message.id, "present") in ("present", "told"):
                prior_replies.append((prior_char.name, prior.content))

    # Token-aware context for this character (same assembly as the round)
    built_context = None
    if context_enabled:
        max_tokens = (
            getattr(chat, "max_context_tokens", None)
            or settings.max_context_tokens
        )
        built_context = await ContextBuilder().build(
            db=db,
            chat_id=chat_id,
            character=character,
            user_message=user_message.content,
            general_prompt=chat.general_prompt,
            messages_window=pre_round_messages,
            round_messages=round_messages,
            character_names=character_names,
            character_locations=character_locations,
            summary=summary_text,
            summary_through_message_id=(
                getattr(summary_obj, "through_message_id", None)
                if summary_obj is not None
                else None
            ),
            memories=memories,
            scene_state=scene_state,
            present_character_names=None,
            relationships_block=relationships_block,
            locations=chat_locations,
            stagnation_rounds=stagnation_rounds,
            viewer_location=character_locations.get(character.id, ""),
            prior_replies=prior_replies,
            is_isolated=(
                character_locations.get(character.id, "") != player_location
            ),
            max_tokens=max_tokens,
        )

    other_names = get_other_character_names(characters, character.id)
    enable_thinking = bool(getattr(chat, "thinking_mode", settings.enable_thinking))

    response_text = ""
    try:
        async for event in ollama_client.generate(
            client=client,
            chat_id=chat.id,
            character=character,
            messages_history=context_messages,
            general_prompt=chat.general_prompt,
            memories=memories,
            other_character_names=other_names,
            max_history_length=history_limit,
            model_name=chat.model_name,
            character_names=character_names,
            summary=summary_text,
            viewer_character_id=character.id,
            presence_map=presence_map,
            same_round_message_ids=None,
            enable_thinking=enable_thinking,
            viewer_location=character_locations.get(character.id, ""),
            character_locations=character_locations,
            prior_replies=prior_replies,
            scene_state=scene_state,
            present_character_names=None,
            stagnation_rounds=stagnation_rounds,
            is_isolated=(character_locations.get(character.id, "") != player_location),
            locations=chat_locations,
            relationships_block=relationships_block,
            behavior_drivers_block=drivers_block,
            open_issues_block=open_issues_block,
            proactive_boost=proactive_boost,
            built_context=built_context,
        ):
            if event["type"] == "token":
                yield {
                    "type": "token",
                    "text": event["text"],
                    "character_id": character.id,
                }
            elif event["type"] == "response":
                response_text = event["text"]
    except RuntimeError as exc:
        logger.error(
            "[chat_id=%d] Regeneration failed for %s: %s",
            chat_id,
            character.name,
            exc,
        )
        raise RuntimeError(
            f"Не удалось перегенерировать ответ {character.name}: {exc}"
        ) from exc

    if not response_text:
        raise RuntimeError(f"Пустой ответ при перегенерации {character.name}")

    # Persist the new reply, then drop the old one
    char_location = getattr(character, "location", "") or ""
    msg_channel, msg_targets = _detect_communication_channel(
        response_text, character.name, character_names
    )
    msg_visibility = settings.default_event_visibility
    if msg_channel != "direct" and msg_targets:
        msg_visibility = "targeted"
    new_message = await crud.create_message(
        db,
        schemas.MessageCreate(
            chat_id=chat_id,
            character_id=character.id,
            role="character",
            content=response_text,
            visibility=msg_visibility,
            location=char_location,
            target_character_ids=msg_targets,
            channel=msg_channel,
        ),
    )
    await crud.compute_and_save_presence_for_message(
        db, new_message, characters, character_names
    )
    await crud.delete_message(db, message_id)

    yield {"type": "message", "message": _message_to_dict(new_message)}
