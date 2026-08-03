"""Character perception: who may know about a world event.

World events are messages. Each character gets only events they can perceive.
This is the primary isolation mechanism (context filtering), not prompt text.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from .config import settings

logger = logging.getLogger(__name__)

Presence = Literal["present", "mentioned", "absent", "told"]
Visibility = Literal["private", "local", "targeted", "public", "global"]

VALID_VISIBILITIES = frozenset(
    {"private", "local", "targeted", "public", "global"}
)

# Communication channels that bridge location isolation
REMOTE_CHANNELS = frozenset({"magic", "phone", "radio", "messenger"})


def normalize_location(location: str | None) -> str:
    """Normalize location labels for comparison."""
    text = (location or "").strip()
    if settings.normalize_locations:
        return text.casefold()
    return text


def locations_match(a: str | None, b: str | None) -> bool:
    return normalize_location(a) == normalize_location(b)


def compute_is_isolated(
    char_loc: str | None,
    other_char_locs: list[str | None],
    player_loc: str | None,
) -> bool:
    """Whether the character has no one (player or other NPC) nearby.

    A character is isolated only when neither the player nor any other NPC
    shares their location. Locations are compared via `locations_match`.
    An empty location (`""`) means a shared scene and never isolates.
    """
    if not (char_loc or "").strip():
        return False
    if locations_match(char_loc, player_loc):
        return False
    for other_loc in other_char_locs:
        if locations_match(char_loc, other_loc):
            return False
    return True


def parse_target_ids(raw: Any) -> list[int]:
    """Parse target character ids from list, JSON string, or empty."""
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        result: list[int] = []
        for item in raw:
            try:
                result.append(int(item))
            except (TypeError, ValueError):
                continue
        return result
    if isinstance(raw, int):
        return [raw]
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            parts = [p.strip() for p in text.split(",") if p.strip()]
            result = []
            for part in parts:
                try:
                    result.append(int(part))
                except ValueError:
                    continue
            return result
        return parse_target_ids(data)
    return []


def serialize_target_ids(ids: list[int] | None) -> str:
    if not ids:
        return "[]"
    cleaned: list[int] = []
    for item in ids:
        try:
            cleaned.append(int(item))
        except (TypeError, ValueError):
            continue
    return json.dumps(cleaned, ensure_ascii=False)


def normalize_visibility(value: str | None) -> str:
    text = (value or settings.default_event_visibility).strip().lower()
    if text not in VALID_VISIBILITIES:
        return settings.default_event_visibility
    return text


def _get_attr(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def event_from_message(message: Any) -> dict[str, Any]:
    """Extract perception fields from a Message ORM/dict/namespace."""
    return {
        "id": _get_attr(message, "id"),
        "role": _get_attr(message, "role") or "",
        "character_id": _get_attr(message, "character_id"),
        "content": _get_attr(message, "content") or "",
        "location": _get_attr(message, "location") or "",
        "visibility": normalize_visibility(_get_attr(message, "visibility")),
        "channel": (_get_attr(message, "channel") or "direct").strip().lower(),
        "target_character_ids": parse_target_ids(
            _get_attr(message, "target_character_ids")
            if _get_attr(message, "target_character_ids") is not None
            else _get_attr(message, "target_ids")
        ),
    }


def can_character_perceive_event(
    *,
    viewer_character_id: int,
    viewer_location: str | None,
    event: dict[str, Any] | Any,
    viewer_name: str = "",
) -> tuple[Presence, str]:
    """Decide if a character may receive an event in their LLM context.

    Returns (presence, reason_code).
    Supports remote channels (magic/phone/radio/messenger) that bridge locations.
    """
    if not isinstance(event, dict):
        event = event_from_message(event)

    role = (event.get("role") or "").strip().lower()
    visibility = normalize_visibility(event.get("visibility"))
    event_location = event.get("location") or ""
    author_id = event.get("character_id")
    targets = parse_target_ids(event.get("target_character_ids"))
    content = event.get("content") or ""
    channel = (event.get("channel") or "direct").strip().lower()

    # Own speech is always known to the speaker
    if role == "character" and author_id is not None:
        try:
            if int(author_id) == int(viewer_character_id):
                return "present", "OWN_MESSAGE"
        except (TypeError, ValueError):
            pass

    if visibility == "global":
        return "present", "GLOBAL"

    if visibility == "public":
        return "present", "PUBLIC"

    if visibility == "private":
        if viewer_character_id in targets:
            return "present", "PRIVATE_TARGET"
        return "absent", "PRIVATE_NOT_TARGET"

    if visibility == "targeted":
        if viewer_character_id in targets:
            return "present", "TARGETED"
        return "absent", "TARGETED_NOT_TARGET"

    # Remote channels bridge location isolation
    if channel in REMOTE_CHANNELS:
        if viewer_character_id in targets:
            return "present", f"REMOTE_CHANNEL_{channel.upper()}"
        # If not targeted but still a remote channel, others may hear only if mentioned
        if viewer_name and _name_mentioned(content, viewer_name):
            return "mentioned", f"REMOTE_MENTIONED_{channel.upper()}"
        return "absent", f"REMOTE_NOT_TARGET_{channel.upper()}"

    # LOCAL (default): same location as the event
    if visibility == "local":
        if locations_match(viewer_location, event_location):
            return "present", "SAME_LOCATION"
        # Soft signal: name mention while absent (optional awareness, not full content)
        if viewer_name and _name_mentioned(content, viewer_name):
            return "mentioned", "MENTIONED_REMOTE"
        return "absent", "DIFFERENT_LOCATION"

    # Unknown visibility → safe default local
    if locations_match(viewer_location, event_location):
        return "present", "SAME_LOCATION_FALLBACK"
    return "absent", "UNKNOWN_VISIBILITY_HIDDEN"


def _name_mentioned(content: str, name: str) -> bool:
    if not name or not content:
        return False
    import re

    pattern = rf"(?<!\w){re.escape(name)}(?!\w)"
    return bool(re.search(pattern, content, flags=re.IGNORECASE))


def log_perception_decision(
    *,
    character_name: str,
    character_id: int,
    event_id: Any,
    visibility: str,
    event_location: str,
    character_location: str,
    presence: Presence,
    reason: str,
) -> None:
    """Debug-level explanation of filter decisions."""
    result = "VISIBLE" if presence != "absent" else "HIDDEN"
    logger.debug(
        "[Context] Character: %s (id=%s) | Event #%s | visibility=%s | "
        "event_location=%r | character_location=%r | result=%s | reason=%s | presence=%s",
        character_name,
        character_id,
        event_id,
        visibility.upper(),
        event_location,
        character_location,
        result,
        reason,
        presence,
    )


def build_world_locations(
    *,
    characters: list[Any],
    player_location: str = "",
) -> dict[str, str]:
    """Map character_id -> location; player stored under key 'player'."""
    world: dict[str, str] = {"player": player_location or ""}
    for character in characters:
        cid = _get_attr(character, "id")
        if cid is None:
            continue
        world[str(cid)] = _get_attr(character, "location") or ""
    return world
