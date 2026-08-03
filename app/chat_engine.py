"""Chat engine: process user messages, generate character replies, extract memories."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime
from types import SimpleNamespace

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import crud
from . import memory_service
from . import models
from . import ollama_client
from . import pending_intervention
from . import perception
from . import relationship_analyzer
from . import relationship_service
from . import schemas
from .relationship_interpreter import interpret as _interpret_rel, TRUST_LOW
from .config import settings
from .context_builder import ContextBuilder
from .database import AsyncSessionLocal
from .context_state import ctx_state
from .repetition_detector import analyze_response
from .role_isolation import get_other_character_names
from .witness_model import Presence, resolve_presence

logger = logging.getLogger(__name__)


def _message_to_dict(msg) -> dict:
    d = schemas.MessageRead.model_validate(msg).model_dump()
    if isinstance(d.get("timestamp"), datetime):
        d["timestamp"] = d["timestamp"].isoformat()
    return d


def _message_snapshot(m) -> dict:
    """Compact round-snapshot dict for one message (relationship analysis input)."""
    return {
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


async def _load_location_descriptions(
    db: AsyncSession, chat_id: int
) -> dict[str, str]:
    """Map location_name -> description (Локации 2.0, §18). Empty when none."""
    try:
        locations = await crud.get_chat_locations(db, chat_id)
    except Exception as exc:
        logger.warning(
            "[chat_id=%d] Failed to load location descriptions: %s", chat_id, exc
        )
        return {}
    return {loc.name: (loc.description or "") for loc in locations}


def _character_is_isolated(
    character_locations: dict[int, str],
    character_id: int,
    characters: list,
    player_location: str,
) -> bool:
    """Whether the character has no player or other NPC in their location.

    Uses `perception.compute_is_isolated`: an empty location is a shared scene
    and never isolates (Plans/locations2.md §6, Sprint 5).
    """
    return perception.compute_is_isolated(
        character_locations.get(character_id, ""),
        [character_locations[c.id] for c in characters if c.id != character_id],
        player_location,
    )


def _effective_prior_replies(
    prior_reply_events: list,
    viewer_character_id: int,
    viewer_location: str,
    viewer_name: str,
    character_names: dict[int, str],
) -> list[tuple[str, str]]:
    """Per-viewer filter of this round's prior replies (§10).

    Each prior reply is a real message event; availability is decided by the
    same perception mechanism as ordinary history — ``can_character_perceive_event``.
    A reply is available only when it can be fully perceived (presence ``present``
    or ``told``); ``absent``/``mentioned`` replies are hidden.
    """
    effective: list[tuple[str, str]] = []
    for event in prior_reply_events:
        author_id = getattr(event, "character_id", None)
        if author_id is None:
            continue
        try:
            author_id = int(author_id)
        except (TypeError, ValueError):
            continue
        presence, _ = perception.can_character_perceive_event(
            viewer_character_id=viewer_character_id,
            viewer_location=viewer_location,
            event=event,
            viewer_name=viewer_name,
        )
        if presence in ("present", "told"):
            content = getattr(event, "content", "") or ""
            effective.append((character_names.get(author_id, ""), content))
    return effective


def _log_generation_diagnostics(
    *,
    character_id: int,
    character_name: str,
    character_locations: dict[int, str],
    player_location: str,
    player_name: str,
    characters: list,
    character_names: dict[int, str],
    messages: list,
    presence_map: dict[int, str] | None = None,
) -> None:
    """DEBUG diagnostics per NPC generation (Plans/locations2.md §21).

    Logs who this NPC sees (same-location characters), who is hidden, and how
    many messages survived the perception filter. Enabled only via
    ``settings.generation_debug`` (GENERATION_DEBUG).
    """
    if not settings.generation_debug:
        return

    my_loc = character_locations.get(character_id, "") or ""
    visible_names: list[str] = []
    hidden_names: list[str] = []
    for c in characters:
        if c.id == character_id:
            continue
        loc = character_locations.get(c.id, "") or ""
        if loc and perception.locations_match(loc, my_loc):
            visible_names.append(c.name)
        else:
            hidden_names.append(c.name)

    # Player is handled separately: not part of the NPC loop.
    if my_loc and perception.locations_match(my_loc, player_location):
        if player_name:
            visible_names.append(player_name)

    visible_messages = 0
    filtered_messages = 0
    for message in messages:
        presence = resolve_presence(
            message,
            character_id,
            character_names,
            presence_map,
            viewer_location=my_loc,
            character_locations=character_locations,
        )
        if presence == "absent":
            filtered_messages += 1
        else:
            visible_messages += 1

    logger.debug(
        "[Generation] NPC=%s Location=%r PlayerLocation=%r\n"
        "Visible characters=%s\n"
        "Hidden characters=%s\n"
        "Visible messages=%d\n"
        "Filtered messages=%d",
        character_name,
        my_loc,
        player_location,
        sorted(visible_names),
        sorted(hidden_names),
        visible_messages,
        filtered_messages,
    )


def _compute_epistemic_evidence(
    round_snapshots: list[dict],
    viewer,
    all_characters: list,
    character_names: dict[int, str],
    character_locations: dict[int, str],
    player_id: int | None,
) -> set[int]:
    """Ids of characters whose behavior ``viewer`` perceived this round.

    MVP epistemic mask (docs/relations.md §10, Sprint 2 item 10): a character
    may only learn how another treats it when there was direct or observed
    evidence of that other's behavior in the current round (mode != "none").
    """
    evidenced: set[int] = set()
    for other in all_characters:
        if other.id == viewer.id:
            continue
        ctx = _build_pair_relationship_context(
            round_snapshots,
            viewer,
            other,
            character_names,
            character_locations,
            player_id=player_id,
        )
        if _evidence_mode(ctx) in ("direct", "observed"):
            evidenced.add(other.id)
    return evidenced


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
    location_descriptions = await _load_location_descriptions(db, chat_id)

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

    # Stable round anchor (docs/relations.md §6, Sprint 1 item 9): one round_id
    # per turn, fixed once to the user-message id. utcnow() is never used for it.
    round_id = f"r{chat_id}-m{user_message.id}"

    # One-time user intervention ("Вмешательство") — read once as a snapshot
    # and consumed only after a fully successful round.
    round_intervention = pending_intervention.get_intervention(chat_id)
    directive = round_intervention.instruction if round_intervention else None
    round_generation_ok = True

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
    player_id = next(
        (c.id for c in all_characters if getattr(c, "is_player", False)),
        None,
    )
    if player_id is not None:
        player_obj = next(
            (c for c in all_characters if c.id == player_id), None
        )
        if player_obj is not None:
            character_locations[player_id] = getattr(player_obj, "location", "") or ""

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

    # Track prior replies in this round for anti-mimicry (P2).
    # We keep the underlying Message events (not just name/text) so that
    # availability of each reply can be decided per viewer via perception (§10).
    prior_reply_events: list = []

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

        _log_generation_diagnostics(
            character_id=current_character.id,
            character_name=current_character.name,
            character_locations=character_locations,
            player_location=player_location,
            player_name=character_names.get(player_id, "") if player_id else "",
            characters=characters,
            character_names=character_names,
            messages=context_messages,
            presence_map=presence_map,
        )

        # Effective prior replies for this viewer: availability of each reply
        # is decided by the same perception mechanism as ordinary history (§10).
        effective_prior_replies = _effective_prior_replies(
            prior_reply_events,
            current_character.id,
            character_locations.get(current_character.id, "") or "",
            current_character.name,
            character_names,
        )

        # MVP epistemic mask (Sprint 2 item 10, docs/relations.md §10): a
        # character learns how another treats it only from this round's
        # direct/observed evidence, and only as an interpretation (no numbers).
        epistemic_mask_block = ""
        try:
            round_snapshots_now = [
                _message_snapshot(m) for m in round_messages
            ]
            evidenced = _compute_epistemic_evidence(
                round_snapshots_now,
                current_character,
                all_characters,
                character_names,
                character_locations,
                player_id=player_id,
            )
            epistemic_mask_block = await relationship_service.build_epistemic_mask_block(
                db, chat_id, current_character.id, current_character.name,
                character_names, evidenced_target_ids=evidenced,
            )
        except Exception as exc:
            logger.warning(
                "[chat_id=%d] Failed to build epistemic mask block: %s",
                chat_id, exc,
            )
            epistemic_mask_block = ""

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
                character_appearances={
                    c.name: c.appearance or "" for c in all_characters
                },
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
                location_descriptions=location_descriptions,
                stagnation_rounds=stagnation_rounds,
                viewer_location=character_locations.get(
                    current_character.id, ""
                ),
                prior_replies=effective_prior_replies,
                is_isolated=_character_is_isolated(
                    character_locations,
                    current_character.id,
                    characters,
                    player_location,
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
                is_isolated=_character_is_isolated(
                    character_locations,
                    current_character.id,
                    characters,
                    player_location,
                ),
                locations=chat_locations,
                location_descriptions=location_descriptions,
                relationships_block=relationships_blocks.get(current_character.id, ""),
                behavior_drivers_block=drivers_blocks.get(current_character.id, ""),
                open_issues_block=open_issues_blocks.get(current_character.id, ""),
                proactive_boost=proactive_boosts.get(current_character.id, 0.0),
                built_context=built_context,
                epistemic_mask_block=epistemic_mask_block,
                directive=directive,
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
            round_generation_ok = False

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

        # Track this reply's event for subsequent characters. Availability for
        # each subsequent viewer is decided later via perception (§10).
        if response_text:
            prior_reply_events.append(char_message)

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
            # Save location-related data first (without character_locations — confirmed later).
            # time_of_day is intentionally excluded: the engine never changes the time of day
            # automatically — it is set only by the user via PATCH /chats/{chat_id}/scene.
            scene_update_no_locs = {
                k: v
                for k, v in scene_update.items()
                if k not in ("character_locations", "time_of_day")
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
                round_id=round_id,
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

        # Track stagnation and round count only (no automatic time advance).
        # NOTE: time of day is intentionally NOT advanced automatically — the engine
        # only tracks stagnation/round_count. The user sets the time themselves via
        # PATCH /chats/{chat_id}/scene.
        custom_state_raw["stagnation_rounds"] = stagnation_rounds
        custom_state_raw["round_count"] = round_count
        custom_state_update = schemas.SceneCustomState(**custom_state_raw)

        await crud.upsert_scene_state(
            db, chat_id, schemas.SceneStateUpdate(custom_state=custom_state_update)
        )
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

    # Consume the one-time intervention after a fully successful round. If a
    # character fell back to the "молчит" placeholder, the round is considered
    # failed and the instruction is preserved for a retry (docs/intervention.md).
    if directive is not None and round_generation_ok:
        pending_intervention.consume_intervention(
            chat_id, expected=round_intervention
        )


async def _analyze_and_update_relationships(
    client: httpx.AsyncClient,
    chat_id: int,
    model_name: str,
    round_snapshots: list[dict],
    character_snapshots: list[dict],
    round_id: str | None = None,
) -> dict:
    """Analyze relationships for all character pairs and apply deltas (Sprint 4).

    Only NPCs are analyzed as relationship *sources*. The player is a valid
    *target* (bots -> player) but never a source (player -> bots is not tracked).

    The default path uses the batch analyzer: a single LLM call covers every
    pair with evidence (§8). Every proposed delta/issue is then passed through
    the deterministic evidence gate (§8.3) — a pair without evidence rejects
    everything. When the batch fails or is disabled, the per-pair analyzer is
    used (§8.4); the fallback never disables evidence-gating.

    ``round_id`` is the stable per-turn anchor from §6, computed once per user
    message in ``process_user_message_streaming``.

    Opens its own DB session instead of borrowing the caller's, so the
    connection is always returned to the pool when the task finishes.

    Single-transaction contract: all deltas/issues/decay/pruning are staged in
    the session and committed with ONE ``flush()`` + ``commit()`` at the end.
    Returns an observability summary dict (counts per action); background
    callers ignore it, the on-demand API endpoint returns it.
    """
    # Stable round anchor (docs/relations.md §6). Kept for direct calls.
    if round_id is None:
        round_id = f"round_{chat_id}_{datetime.utcnow().isoformat()}"

    summary: dict = {
        "round_id": round_id,
        "analyzed_pairs": 0,
        "applied_deltas": 0,
        "created_issues": 0,
        "resolved_issues": 0,
        "created_events": 0,
        "decay_events": 0,
        "pruned_events": 0,
    }
    affected_relationship_ids: set[int] = set()
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

            # Issues mentioned this round: those passed to the analyzer for an
            # analyzed pair, plus those selected into each source's
            # generation-context `<open_issue data>` block (§7.4 salience).
            mentioned_issue_ids: set[int] = set()

            # Per-pair analysis inputs, shared by the batch prompt and the
            # per-pair fallback.
            pairs: list[dict] = []
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

                    # Deterministic hearsay reliability (§12): resolve the
                    # effective delta cap from stored edges (trust in the
                    # teller, teller->target valence). Stored on pair_ctx so
                    # both the batch path and the per-pair fallback apply it.
                    if (
                        pair_ctx.get("hearsay")
                        and pair_ctx.get("hearsay_source") is not None
                    ):
                        try:
                            pair_ctx["hearsay_effective_cap"] = (
                                await _compute_hearsay_effective_cap(
                                    db,
                                    source_char.id,
                                    pair_ctx["hearsay_source"],
                                    target_char.id,
                                )
                            )
                        except Exception as exc:
                            logger.warning(
                                "[chat_id=%d] Failed to compute hearsay cap "
                                "for %d->%d: %s",
                                chat_id, source_char.id, target_char.id, exc,
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

                    # Trajectory (docs/relations.md §11): snapshot-based from LLM events
                    trajectory_events = await relationship_service.get_trajectory_events(
                        db, rel.id, window=settings.relationship_trajectory_window,
                    )
                    trajectory_text = relationship_service.build_trajectory_block(
                        trajectory_events,
                        source_char.name,
                        target_char.name,
                    )

                    # Triadic MVP (§13): build third-party notes for target's
                    # relationships with characters mentioned in this round.
                    third_party_notes: list[str] = []
                    third_party_ids = pair_ctx.get("third_party_ids", [])
                    for third_id in third_party_ids:
                        if third_id == source_char.id or third_id == target_char.id:
                            continue
                        third_rel = await relationship_service.get_relationship(
                            db, target_char.id, third_id,
                        )
                        if third_rel is None:
                            continue
                        third_name = character_names.get(third_id, f"ID:{third_id}")
                        target_name = character_names.get(target_char.id, f"ID:{target_char.id}")
                        # Format: [третье лицо] {target} ↔ {third}: {type}, {метрика}={значение}
                        # Show relationship_type + top non-zero metric
                        metrics = [
                            ("привязанность", third_rel.affection),
                            ("доверие", third_rel.trust),
                            ("влечение", third_rel.attraction),
                            ("обида", third_rel.resentment),
                            ("ревность", third_rel.jealousy),
                        ]
                        non_zero = [(name, val) for name, val in metrics if val > 0]
                        if non_zero:
                            # Pick highest metric
                            top_metric = max(non_zero, key=lambda x: x[1])
                            metric_str = f"{top_metric[0]}={top_metric[1]}"
                        else:
                            metric_str = "нейтральное"
                        note = f"[третье лицо] {target_name} ↔ {third_name}: {third_rel.relationship_type}, {metric_str}"
                        third_party_notes.append(note)

                    pairs.append(
                        {
                            "source_char": source_char,
                            "target_char": target_char,
                            "source_id": source_char.id,
                            "target_id": target_char.id,
                            "source_name": source_char.name,
                            "target_name": target_char.name,
                            "pair_ctx": pair_ctx,
                            "mode": _evidence_mode(pair_ctx),
                            "rel": rel,
                            "affection": rel.affection,
                            "trust": rel.trust,
                            "attraction": rel.attraction,
                            "resentment": rel.resentment,
                            "jealousy": rel.jealousy,
                            "current_type": rel.relationship_type,
                            "recent_events_text": events_text,
                            "open_issues": open_issues_payload,
                            "third_party_notes": third_party_notes,
                            "trajectory": trajectory_text,
                        }
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

            if settings.relationship_batch_enabled and pairs:
                pair_by_key = {
                    (p["source_id"], p["target_id"]): p for p in pairs
                }
                known_pairs = set(pair_by_key)
                scene_text = _build_batch_scene_summary(
                    round_snapshots,
                    character_names,
                    character_locations,
                    player_id=player_id,
                )
                prompt_pairs = [
                    {
                        "source_name": p["source_name"],
                        "target_name": p["target_name"],
                        "source_id": p["source_id"],
                        "target_id": p["target_id"],
                        "mode": p["mode"],
                        "current_type": p["current_type"],
                        "affection": p["affection"],
                        "trust": p["trust"],
                        "attraction": p["attraction"],
                        "resentment": p["resentment"],
                        "jealousy": p["jealousy"],
                        "interaction_summary": p["pair_ctx"]["interaction_summary"],
                        "recent_events_text": p["recent_events_text"],
                        "open_issues": p["open_issues"],
                        "excerpt": p["pair_ctx"]["excerpt"],
                        "hearsay_cap": p["pair_ctx"].get("hearsay_effective_cap"),
                        "hearsay_source_name": (
                            character_names.get(p["pair_ctx"]["hearsay_source"], "")
                            if p["pair_ctx"].get("hearsay_source") is not None
                            else ""
                        ),
                        "third_party_notes": p.get("third_party_notes", []),
                        "trajectory": p.get("trajectory", ""),
                    }
                    for p in pairs
                ]
                try:
                    deltas, orphan_issues = (
                        await relationship_analyzer.analyze_batch_relationships(
                            client=client,
                            model_name=model_name,
                            scene_text=scene_text,
                            pairs=prompt_pairs,
                            known_pairs=known_pairs,
                        )
                    )
                except Exception as exc:
                    logger.warning(
                        "[chat_id=%d] Batch relationship analysis failed: %s",
                        chat_id, exc,
                    )
                    deltas = None

                summary["analyzed_pairs"] = len(pairs)

                if deltas is None:
                    if settings.relationship_batch_fallback:
                        logger.info(
                            "[chat_id=%d] Falling back to per-pair analysis",
                            chat_id,
                        )
                        applied, affected = await _run_per_pair_analysis(
                            db, chat_id, client, model_name, pairs,
                            round_id=round_id,
                        )
                        summary["applied_deltas"] = applied
                        affected_relationship_ids.update(affected)
                    else:
                        logger.warning(
                            "[chat_id=%d] Batch failed and fallback disabled; "
                            "skipping relationship update", chat_id,
                        )
                else:
                    # Issues for edges with no metric delta (§8.1): apply them
                    # directly without touching the edge's metrics/type.
                    for issue in orphan_issues:
                        p = pair_by_key.get(
                            (issue.source_character_id, issue.target_character_id)
                        )
                        if p is None:
                            continue
                        affected_relationship_ids.add(p["rel"].id)
                        applied_issues = await relationship_service.apply_issue_deltas(
                            db, [issue], rel=p["rel"], round_id=round_id,
                        )
                        for applied_issue in applied_issues:
                            if applied_issue.state == "open":
                                summary["created_issues"] += 1
                            elif applied_issue.state == "resolved":
                                summary["resolved_issues"] += 1
                    # Evidence-gated metric deltas (§8.3).
                    for delta in deltas:
                        p = pair_by_key.get(
                            (delta.source_character_id, delta.target_character_id)
                        )
                        if p is None:
                            continue
                        gated = _constrain_pair_delta(delta, p["rel"], p["pair_ctx"])
                        if gated is None:
                            continue
                        affected_relationship_ids.add(p["rel"].id)
                        await relationship_service.apply_delta(
                            db, gated, chat_id, round_id=round_id,
                        )
                        summary["applied_deltas"] += 1
            else:
                applied, affected = await _run_per_pair_analysis(
                    db, chat_id, client, model_name, pairs,
                    round_id=round_id,
                )
                summary["applied_deltas"] = applied
                affected_relationship_ids.update(affected)

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

            # Apply deterministic decay (Sprint 3 item 16, docs/relations.md §18).
            # Runs after LLM deltas and issue tick, using current round_id.
            try:
                decay_events = await relationship_service.apply_decay(
                    db, chat_id, round_id=round_id,
                )
                summary["decay_events"] = len(decay_events)
                if decay_events:
                    logger.debug(
                        "[chat_id=%d] Created %d decay events",
                        chat_id, len(decay_events),
                    )
            except Exception as exc:
                logger.warning(
                    "[chat_id=%d] Decay application failed: %s", chat_id, exc
                )

            # Event pruning (Sprint 4 item 3): fold old events of every pair
            # that changed this round into a single archive entry.
            for rel_id in affected_relationship_ids:
                try:
                    archive = await relationship_service.prune_relationship_events(
                        db, rel_id,
                    )
                    if archive is not None:
                        summary["pruned_events"] += 1
                except Exception as exc:
                    logger.warning(
                        "[chat_id=%d] Pruning failed for rel %d: %s",
                        chat_id, rel_id, exc,
                    )

            # Count LLM events created this round (visible only after flush).
            try:
                await db.flush()
                count_stmt = (
                    select(func.count())
                    .select_from(models.RelationshipEvent)
                    .where(
                        models.RelationshipEvent.round_id == round_id,
                        models.RelationshipEvent.kind == "llm",
                    )
                )
                summary["created_events"] = (
                    (await db.execute(count_stmt)).scalar() or 0
                )
            except Exception as exc:
                logger.warning(
                    "[chat_id=%d] Failed to flush/count events: %s", chat_id, exc
                )

            # Single flush + commit for the whole round (Sprint 4 item 2).
            await db.commit()
            logger.info(
                "relationship_analysis_complete",
                extra={"chat_id": chat_id, **summary},
            )
            return summary
    except Exception:
        logger.exception("[chat_id=%d] Relationship analysis failed", chat_id)
        summary["error"] = "relationship analysis failed"
        return summary


async def _run_per_pair_analysis(
    db: AsyncSession,
    chat_id: int,
    client: httpx.AsyncClient,
    model_name: str,
    pairs: list[dict],
    *,
    round_id: str,
) -> tuple[int, set[int]]:
    """Per-pair relationship analysis (docs/relations.md §8.4 fallback path).

    Applies the same deterministic evidence gating (§8.3) as the batch path —
    the fallback never disables gating. Returns ``(applied_delta_count,
    affected_relationship_ids)`` for the caller's observability summary.
    """
    applied = 0
    affected: set[int] = set()
    for p in pairs:
        deltas = await relationship_analyzer.analyze_relationships(
            client=client,
            model_name=model_name,
            source_name=p["source_name"],
            target_name=p["target_name"],
            current_type=p["current_type"],
            affection=p["affection"],
            trust=p["trust"],
            attraction=p["attraction"],
            resentment=p["resentment"],
            jealousy=p["jealousy"],
            recent_events_text=p["recent_events_text"],
            round_text=p["pair_ctx"]["excerpt"],
            source_character_id=p["source_id"],
            target_character_id=p["target_id"],
            interaction_summary=p["pair_ctx"]["interaction_summary"],
            direct_interaction=p["pair_ctx"]["direct_interaction"],
            observed_target=p["pair_ctx"]["observed_target"],
            hearsay=p["pair_ctx"].get("hearsay", False),
            hearsay_cap=p["pair_ctx"].get("hearsay_effective_cap"),
            open_issues=p["open_issues"],
            third_party_notes=p.get("third_party_notes"),
        )
        for delta in deltas:
            gated = _constrain_pair_delta(delta, p["rel"], p["pair_ctx"])
            if gated is None:
                continue
            affected.add(p["rel"].id)
            await relationship_service.apply_delta(
                db, gated, chat_id, round_id=round_id,
            )
            applied += 1
    return applied, affected


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
    any_evidence, third_party_ids (for Triadic MVP).
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
    hearsay = False
    hearsay_source = None
    third_party_ids: set[int] = set()

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
            # Target speaks about others -> those are third parties relevant to target
            for tid in targets:
                if tid != source.id and tid != target.id:
                    third_party_ids.add(tid)
            # Also check content for name mentions
            for cid, cname in character_names.items():
                if cid != source.id and cid != target.id and _text_mentions_name(content, cname):
                    third_party_ids.add(cid)
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
            # Hearsay (§12): the author X directly addresses the source and
            # talks about the target — a second-hand report, not a direct
            # observation of the target's behavior.
            if source.id in targets and _text_mentions_name(content, target_name):
                hearsay = True
                hearsay_source = author_id
            else:
                observed_target = True
            # Third party is relevant to target (they're talking about target)
            third_party_ids.add(author_id)
            # Also check if they mention other characters
            for tid in targets:
                if tid != source.id and tid != target.id:
                    third_party_ids.add(tid)
            for cid, cname in character_names.items():
                if cid != source.id and cid != target.id and cid != author_id and _text_mentions_name(content, cname):
                    third_party_ids.add(cid)

        speaker = character_names.get(author_id, f"ID:{author_id}")
        addressee_names = [character_names.get(t, f"ID:{t}") for t in targets]
        addressee_text = ", ".join(addressee_names) if addressee_names else "(всем)"
        if hearsay:
            lines.append(
                f"[слух от {speaker}] {speaker} (id={author_id}) -> "
                f"{addressee_text}: {content}"
            )
        else:
            lines.append(f"{speaker} (id={author_id}) -> {addressee_text}: {content}")
        summary.append(f"{speaker} -> {addressee_text}")

    excerpt = "\n".join(lines[-max_lines:])
    return {
        "excerpt": excerpt,
        "interaction_summary": "\n".join(summary[:max_lines]) or "взаимодействия не было",
        "direct_interaction": direct_interaction,
        "observed_target": observed_target,
        "hearsay": hearsay,
        "hearsay_source": hearsay_source,
        "any_evidence": bool(excerpt.strip()),
        "third_party_ids": list(third_party_ids),
    }


def _evidence_mode(pair_ctx: dict) -> str:
    """Deterministic evidence mode for a pair: direct | observed | hearsay | none.

    Precedence: direct > observed > hearsay > none. ``hearsay`` means the
    source only heard a second-hand report about the target (§12). ``none``
    means the source had no perceivable evidence about the target this round;
    every LLM-proposed delta for such a pair is rejected (§8.3).
    """
    if pair_ctx.get("direct_interaction"):
        return "direct"
    if pair_ctx.get("observed_target"):
        return "observed"
    if pair_ctx.get("hearsay"):
        return "hearsay"
    return "none"


def _build_batch_scene_summary(
    round_snapshots: list[dict],
    character_names: dict[int, str],
    character_locations: dict[int, str],
    player_id: int | None = None,
    max_lines: int = 30,
) -> str:
    """Compressed social scene for the batch prompt (docs/relations.md §8.2.2).

    Who is where, then who said what to whom (global view), capped to
    ``max_lines`` lines.
    """
    lines: list[str] = []
    for cid, loc in character_locations.items():
        lines.append(f"{character_names.get(cid, f'ID:{cid}')}: {loc or '?'}")
    for snap in round_snapshots:
        role = (snap.get("role") or "").strip().lower()
        if role == "system":
            continue
        author_id = snap.get("character_id")
        if role == "user":
            author_id = player_id
        if author_id is None:
            continue
        speaker = character_names.get(author_id, f"ID:{author_id}")
        targets = perception.parse_target_ids(snap.get("target_character_ids"))
        addressee_names = [character_names.get(t, f"ID:{t}") for t in targets]
        addressee_text = ", ".join(addressee_names) if addressee_names else "(всем)"
        lines.append(f"{speaker} -> {addressee_text}: {snap.get('content') or ''}")
    return "\n".join(lines[-max_lines:])


def _constrain_pair_delta(
    delta: schemas.RelationshipDelta,
    rel: models.CharacterRelationship,
    pair_ctx: dict,
) -> schemas.RelationshipDelta | None:
    """Deterministic evidence gating + caps (docs/relations.md §8.3, §9).

    - mode ``none``: REJECT (returns ``None``) — no evidence means no right to
      change anything; the LLM never decides admissibility.
    - mode ``direct``: deltas are already clamped to ±MAX_DELTA by the schema;
      the type may change (the transition graph is validated in apply_delta).
    - mode ``observed``: deltas capped to relationship_reflection_delta_cap and
      the relationship type is frozen (unless configured otherwise).
    - mode ``hearsay``: deltas capped to the deterministic effective hearsay
      cap (§12), always weaker than direct/observed, and the type is frozen.
    """
    mode = _evidence_mode(pair_ctx)
    if mode == "none":
        logger.warning(
            "Evidence gating: rejecting delta for %d->%d (mode=none)",
            delta.source_character_id, delta.target_character_id,
        )
        return None
    if mode == "direct":
        return delta
    if mode == "hearsay":
        cap = pair_ctx.get("hearsay_effective_cap")
        if cap is None:
            cap = settings.relationship_hearsay_cap
        cap = max(1, int(cap))
    else:
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


# ---------------------------------------------------------------------------
# Hearsay reliability (docs/relations.md §12, Sprint 2 item 12)
# ---------------------------------------------------------------------------
def _hearsay_effective_cap(
    *,
    trust: int | None,
    hostility_high: bool,
    base_cap: int,
) -> int:
    """Deterministic hearsay cap: LLM cannot grade a rumor's reliability.

    ``trust`` is the source's trust in the teller (``None`` when no edge
    exists → treat as neutral). A hostile teller->target valence makes the
    report a gossip and lowers the cap further. Floor is 1 so the delta stays
    non-zero (weak but allowed).
    """
    cap = max(1, int(base_cap))
    if trust is not None and trust < TRUST_LOW:
        cap = max(1, int(cap / 2))
    if hostility_high:
        cap = max(1, int(cap * 0.7))
    return cap


async def _compute_hearsay_effective_cap(
    db: AsyncSession,
    source_id: int,
    teller_id: int,
    target_id: int,
) -> int:
    """Resolve the deterministic hearsay cap for pair source -> target (§12).

    - ``trust(source -> teller)``: the main reliability factor; low trust
      halves the cap.
    - ``valence(teller -> target)``: hostile (resentment/jealousy-derived)
      halves it further via the 0.7 gossip multiplier.
    Missing edges are treated as neutral (no penalty).
    """
    trust = None
    rel_teller = await relationship_service.get_relationship(db, source_id, teller_id)
    if rel_teller is not None:
        trust = int(getattr(rel_teller, "trust", 50) or 50)

    hostility_high = False
    rel_valence = await relationship_service.get_relationship(db, teller_id, target_id)
    if rel_valence is not None:
        hostility_high = _interpret_rel(rel_valence).hostility == "high"

    return _hearsay_effective_cap(
        trust=trust,
        hostility_high=hostility_high,
        base_cap=settings.relationship_hearsay_cap,
    )


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
    player_id = next(
        (c.id for c in all_characters if getattr(c, "is_player", False)),
        None,
    )
    if player_id is not None:
        player_obj = next(
            (c for c in all_characters if c.id == player_id), None
        )
        if player_obj is not None:
            character_locations[player_id] = getattr(player_obj, "location", "") or ""
    player_location = getattr(chat, "player_location", "") or ""
    chat_locations = getattr(chat, "locations", "") or "[]"
    location_descriptions = await _load_location_descriptions(db, chat_id)

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

    # MVP epistemic mask (Sprint 2 item 10, docs/relations.md §10)
    epistemic_mask_block = ""
    try:
        round_snapshots_now = [_message_snapshot(m) for m in round_messages]
        evidenced = _compute_epistemic_evidence(
            round_snapshots_now,
            character,
            all_characters,
            character_names,
            character_locations,
            player_id=player_id,
        )
        epistemic_mask_block = await relationship_service.build_epistemic_mask_block(
            db, chat_id, character.id, character.name,
            character_names, evidenced_target_ids=evidenced,
        )
    except Exception as exc:
        logger.warning(
            "[chat_id=%d] Failed to build epistemic mask block: %s", chat_id, exc,
        )
        epistemic_mask_block = ""

    # Presence & prior replies visible to this character
    history_message_ids = [m.id for m in context_messages if m.id is not None]
    presence_map = await crud.get_presence_map(db, history_message_ids, character.id)

    _log_generation_diagnostics(
        character_id=character.id,
        character_name=character.name,
        character_locations=character_locations,
        player_location=player_location,
        player_name=character_names.get(player_id, "") if player_id else "",
        characters=characters,
        character_names=character_names,
        messages=context_messages,
        presence_map=presence_map,
    )

    # Prior replies from this round visible to this character (§10): availability
    # is decided by the same perception mechanism as ordinary history.
    prior_replies: list[tuple[str, str]] = []
    for prior in round_messages:
        if prior.role != "character" or prior.id == message_id:
            continue
        prior_char = char_by_id.get(prior.character_id)
        if prior_char is None:
            continue
        presence, _ = perception.can_character_perceive_event(
            viewer_character_id=character.id,
            viewer_location=character_locations.get(character.id, "") or "",
            event=prior,
            viewer_name=character.name,
        )
        if presence in ("present", "told"):
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
            character_appearances={
                c.name: c.appearance or "" for c in all_characters
            },
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
            location_descriptions=location_descriptions,
            stagnation_rounds=stagnation_rounds,
            viewer_location=character_locations.get(character.id, ""),
            prior_replies=prior_replies,
            is_isolated=_character_is_isolated(
                character_locations,
                character.id,
                characters,
                player_location,
            ),
            max_tokens=max_tokens,
        )

    other_names = get_other_character_names(characters, character.id)
    enable_thinking = bool(getattr(chat, "thinking_mode", settings.enable_thinking))

    # One-time user intervention applies to regeneration too, but is NOT
    # consumed here — it survives until a full round is generated.
    pending = pending_intervention.get_intervention(chat_id, character.id)
    directive = pending.instruction if pending else None

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
            is_isolated=_character_is_isolated(
                character_locations,
                character.id,
                characters,
                player_location,
            ),
            locations=chat_locations,
            location_descriptions=location_descriptions,
            relationships_block=relationships_block,
            behavior_drivers_block=drivers_block,
            open_issues_block=open_issues_block,
            proactive_boost=proactive_boost,
            built_context=built_context,
            epistemic_mask_block=epistemic_mask_block,
            directive=directive,
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
