"""Общие хелперы раунда + non-streaming точка входа (decomposition.md §4.2).

Вынесено из ``app/chat_engine.py`` (Milestone 5B). Хелперы используются и
``streaming.py``, и ``regeneration.py``; ``process_user_message`` — удобная
non-streaming обёртка над ``process_user_message_streaming``.

Logger хранится в поддереве ``app.chat_engine`` (``app.chat_engine.pipeline.*`),
чтобы log-фильтры/тесты, ориентирующиеся на ``app.chat_engine``, продолжали
видеть записи (пропагация в pytest caplog).
"""

import json
import logging
import re
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from .. import crud
from .. import models
from .. import perception
from .. import schemas
from .. import wpe_shadow
from ..config import settings
from ..movement import detect_character_movement
from ..witness_model import resolve_presence

logger = logging.getLogger("app.chat_engine.pipeline.session")

async def _create_message_with_shadow(
    db: AsyncSession,
    message: schemas.MessageCreate,
    *,
    round_id: str | None = None,
) -> models.Message:
    """Create a message and run WPE shadow-perception (Sprint 1, §7.1).

    Shadow-триггер перенесён из ``crud.create_message`` в сервисный слой:
    направление зависимостей — сервис → crud. ``maybe_run_shadow_perception``
    сам держит флаг-гард и try/except, поэтому поведение 1:1 с прежним.
    """
    saved = await crud.create_message(db, message, round_id=round_id)
    await wpe_shadow.maybe_run_shadow_perception(db, saved)
    return saved


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
        "location_id": getattr(m, "location_id", None),
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
    adjacency_index: dict[str, set[str]] | None = None,
    viewer_location_id: Any = None,
) -> list[tuple[str, str]]:
    """Per-viewer filter of this round's prior replies (§10).

    Each prior reply is a real message event; availability is decided by the
    same perception mechanism as ordinary history — ``can_character_perceive_event``.
    A reply is fully available when it can be perceived (presence ``present`` or
    ``told``); ``audible``/``mentioned`` replies are surfaced as a sensory line
    (via ``format_line_for_presence``) without leaking full content; ``absent``
    replies are hidden.
    """
    from ..witness_model import format_line_for_presence

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
            adjacency_index=adjacency_index,
            viewer_location_id=viewer_location_id,
        )
        author_name = character_names.get(author_id, "")
        if presence in ("present", "told"):
            content = getattr(event, "content", "") or ""
            effective.append((author_name, content))
        elif presence in ("audible", "mentioned"):
            line = format_line_for_presence(
                event, presence, character_names, viewer_name=viewer_name
            )
            if line:
                effective.append((author_name, line))
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


def _parse_known_locations(
    locations_json: str,
    character_locations: dict[int, str],
    player_location: str,
) -> list[str]:
    """Known location names for deterministic movement detection.

    Combines the chat's declared locations (original casing), every character's
    current location, and the player's location. Used by
    ``detect_character_movement`` to resolve explicit destinations.
    """
    import json
    known: list[str] = []
    seen: set[str] = set()
    try:
        locs = json.loads(locations_json) if locations_json and locations_json != "[]" else []
        if isinstance(locs, list):
            for loc in locs:
                name = str(loc).strip() if loc else ""
                if name and name.casefold() not in seen:
                    known.append(name)
                    seen.add(name.casefold())
    except (json.JSONDecodeError, TypeError):
        pass
    for loc in list(character_locations.values()) + [player_location]:
        name = (loc or "").strip()
        if name and name.casefold() not in seen:
            known.append(name)
            seen.add(name.casefold())
    return known


def _is_location_allowed(location: str, allowed: set[str]) -> bool:
    """Check if a location is in the allowed set (case-insensitive)."""
    if not allowed:
        return True  # if no locations defined, everything is allowed
    return location.strip().casefold() in allowed


def _build_character_round_text(round_messages: list, character_id: int) -> str:
    """Join a single character's own lines from the current round.

    Speaker isolation for the scene-state movement gate: a character is only
    confirmed by THEIR OWN speech, never by the combined round history
    (Елизавета's «иду в общий зал» must not move Кирка). The user message and
    system remarks (no ``character``) are excluded.
    """
    return "\n".join(
        m.content
        for m in round_messages
        if m.character is not None
        and m.character.id == character_id
        and m.content
    )


def _scene_gate_confirms(
    round_messages: list,
    character_id: int,
    character_name: str,
    proposed_location: str,
    known_locations: list[str],
    character_locations: dict[int, str],
    character_names: dict[int, str],
) -> bool:
    """Scene-state gate: confirm an LLM-proposed location for one character.

    The LLM scene extraction proposes ``character_locations``, but a move is
    only applied when the character's OWN round speech corroborates it — the
    combined history is never used (speaker isolation). ``None`` from the
    detector means "no corroboration" (and is not a crash).
    """
    if not proposed_location or not proposed_location.strip():
        return False
    char_text = _build_character_round_text(round_messages, character_id)
    detected = detect_character_movement(
        char_text,
        character_name,
        known_locations,
        character_locations,
        character_names,
    )
    if detected is None:
        return False
    return detected.strip().casefold() == proposed_location.strip().casefold()


# Remote communication channel keywords (Russian)
_CHANNEL_PATTERNS: list[tuple[str, list[str]]] = [
    ("magic", ["маги", "заклинани", "телепати", "магическ", "ментальн", "телекин", "мистическ", "закляти"]),
    ("phone", ["звон", "телефон", "мобильн", "набира", "трубк", "вызов", "позвон"]),
    ("radio", ["раци", "передатчик", "приёмник", "эфир", "частота", "радиосвяз"]),
    ("messenger", ["сообщени", "мессенджер", "чат", "электрон", "письм", "написал", "смс", "телеграм", "whatsapp", "telegram"]),
]

# Punctuation that marks a name as a direct address (vocative): ",, ! ? ; : «»
_VOCATIVE_PUNCT = ",!?;:«»\"'—…"
# Russian character names include endings (Кирк/Кирка/Кирку), so match a name
# only at a word boundary and only when adjacent to vocative punctuation.
_VOCATIVE_BOUNDARY = re.compile(r"(?<![A-Za-zА-Яа-яЁё0-9])")


def _directly_addressed_ids(
    text: str, speaker_name: str, character_names: dict[int, str]
) -> list[int]:
    """Which characters are directly addressed by name (vocative) in the text.

    A mere mention ("Голос Кирка затих", "если Анастасия снова теряла
    контроль") is NOT an address: characters in narration are not remote
    targets. Only a name adjacent to vocative punctuation counts —
    "Кирк, ты слышишь?", "Антон!", «Елизавета, ...». This is the isolation
    hardening that stops narration keywords from bridging locations.
    """
    if not text:
        return []
    text_lower = text.lower()
    targets: list[int] = []
    for cid, name in character_names.items():
        if name == speaker_name:
            continue
        n = name.lower()
        for m in re.finditer(re.escape(n), text_lower):
            if not _VOCATIVE_BOUNDARY.match(text_lower, m.start()):
                continue
            before = m.start() - 1
            after = m.end()
            if before >= 0 and text_lower[before] in _VOCATIVE_PUNCT:
                targets.append(cid)
                break
            if after < len(text_lower) and text_lower[after] in _VOCATIVE_PUNCT:
                targets.append(cid)
                break
    return targets


def _detect_communication_channel(
    text: str, speaker_name: str, character_names: dict[int, str]
) -> tuple[str, list[int]]:
    """Detect remote communication channel from character response text.

    WPE 3.0 Фаза 8: legacy-safety-net, НЕ источник истины (И14). Источник
    канала/адресатов — `send_message` action из tools/format; этот regex-путь
    срабатывает только когда действия не извлечены (actions off / text-only).
    Scans for channel keywords and identifies the target character by name.
    Returns (channel_type, list_of_target_character_ids).
    """
    if not text:
        return "direct", []

    text_lower = text.lower()

    for channel, keywords in _CHANNEL_PATTERNS:
        if any(kw in text_lower for kw in keywords):
            # Found a remote channel — find which character is being contacted.
            # A keyword alone is not enough: words like "магией" (fantasy world),
            # "звонки" (bells) or "сообща" (together) appear in ordinary in-person
            # speech. Only treat the reply as remote communication when at least
            # one other character is DIRECTLY addressed by name (vocative);
            # otherwise the dialogue is in-person and stays "direct" so location
            # isolation holds.
            targets = _directly_addressed_ids(text, speaker_name, character_names)
            if targets:
                return channel, targets

    return "direct", []
    return schemas.MessageRead.model_validate(message).model_dump(mode="json")


def _character_to_snapshot(character) -> dict:
    return schemas.CharacterRead.model_validate(character).model_dump(mode="python")


async def process_user_message(
    client: httpx.AsyncClient,
    db: AsyncSession,
    chat_id: int,
    user_text: str,
    *,
    visibility: str | None = None,
    target_character_ids: list[int] | None = None,
) -> list[dict]:
    from .streaming import process_user_message_streaming

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
