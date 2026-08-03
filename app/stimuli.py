"""World event stimuli — metadata attached to messages, not separate DB entities.

Stimuli describe what can be *heard* from an event (knock, call, shout,
loud_sound) and who is being *addressed* (address/call with target_character).
They are stored as a JSON array in ``messages.stimuli`` and consumed by the
perception layer to decide between VISIBLE / AUDIBLE / MENTIONED / ABSENT.

The extraction heuristics below are isolated in this module so they can be
replaced later by LLM-based extraction without touching the rest of the code.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

STIMULUS_TYPES = ("knock", "call", "shout", "address", "loud_sound")

# Stimulus types that are loud enough to be heard from an adjacent location.
AUDIBLE_STIMULUS_TYPES = frozenset({"knock", "call", "shout", "loud_sound"})


@dataclass
class Stimulus:
    type: str  # knock | call | shout | address | loud_sound
    target_character: str | None = None
    audibility: str = "medium"  # high | medium | low

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "target_character": self.target_character,
            "audibility": self.audibility,
        }


# ------------------------------ serialization ------------------------------

def _coerce_stimulus(data: Any) -> Stimulus | None:
    if isinstance(data, Stimulus):
        return data
    if isinstance(data, dict):
        stype = str(data.get("type") or "").strip().lower()
        if stype not in STIMULUS_TYPES:
            return None
        return Stimulus(
            type=stype,
            target_character=data.get("target_character"),
            audibility=str(data.get("audibility") or "medium"),
        )
    return None


def parse_stimuli(raw: Any) -> list[Stimulus]:
    """Parse stimuli from a JSON string, list of dicts/Stimulus, or None."""
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(raw, list):
        return []
    result: list[Stimulus] = []
    for item in raw:
        stimulus = _coerce_stimulus(item)
        if stimulus is not None:
            result.append(stimulus)
    return result


def serialize_stimuli(stimuli: list[Stimulus] | list[dict] | None) -> str:
    """Serialize stimuli to a JSON string for storage in ``messages.stimuli``."""
    if not stimuli:
        return "[]"
    items: list[dict[str, Any]] = []
    for s in stimuli:
        d = s.to_dict() if isinstance(s, Stimulus) else s
        if isinstance(d, dict):
            items.append({k: v for k, v in d.items() if v is not None})
    return json.dumps(items, ensure_ascii=False)


def has_stimulus(stimuli: list[Stimulus] | list[dict] | str | None, type_: str) -> bool:
    """Whether any stimulus of ``type_`` is present."""
    return any(s.type == type_ for s in parse_stimuli(stimuli))


def stimulus_targets(stimuli: list[Stimulus] | list[dict] | str | None, name: str) -> bool:
    """Whether an address/call stimulus is aimed at the given character name."""
    if not name:
        return False
    folded = name.casefold()
    return any(
        s.target_character and s.target_character.casefold() == folded
        for s in parse_stimuli(stimuli)
    )


# ------------------------------ extraction ------------------------------

_KNOCK_RE = re.compile(r"(?:стуч|стук|постучал|постуч|барабан)", re.IGNORECASE)
_CALL_RE = re.compile(r"(?:зову|зовёт|зовут|зовешь|позвал|позвала|позов|оклика|окликнул|окликнула)", re.IGNORECASE)
_SHOUT_RE = re.compile(r"(?:крич|орать|орет|орёт|воп|заорал|заорала)", re.IGNORECASE)
_LOUD_SOUND_RE = re.compile(
    r"(?:грохот|шум|громк|звон|треск|хлоп|дверь\s+захлопнулась|лязг|гул)", re.IGNORECASE
)
_WHISPER_RE = re.compile(r"(?:шепч|шёпот|тихо\s+говор)", re.IGNORECASE)


def _address_found(text: str, name: str) -> bool:
    """Vocative address: ``Name`` followed by comma/punctuation/end of line."""
    pattern = re.compile(
        rf"(?<!\w){re.escape(name)}(?=\s*[,!?….:\n])",
        re.IGNORECASE,
    )
    return bool(pattern.search(text))


def extract_stimuli(
    text: str,
    character_names: list[str],
    viewer_name: str | None = None,
) -> list[Stimulus]:
    """Heuristically extract stimuli from message text.

    ``character_names`` are used to resolve ``address`` targets. ``viewer_name``
    is reserved for future per-viewer resolution and is currently unused.
    """
    del viewer_name  # reserved for LLM-based per-viewer extraction
    if not text:
        return []
    text_lower = text.lower()

    stimuli: list[Stimulus] = []
    if _LOUD_SOUND_RE.search(text_lower):
        stimuli.append(Stimulus(type="loud_sound", audibility="high"))
    if _KNOCK_RE.search(text_lower):
        stimuli.append(Stimulus(type="knock", audibility="high"))
    if _SHOUT_RE.search(text_lower):
        stimuli.append(Stimulus(type="shout", audibility="high"))
    if _CALL_RE.search(text_lower):
        stimuli.append(Stimulus(type="call", audibility="high"))

    for name in character_names:
        if name and _address_found(text, name):
            audibility = "low" if _WHISPER_RE.search(text_lower) else "medium"
            stimuli.append(
                Stimulus(type="address", target_character=name, audibility=audibility)
            )
    return stimuli


# ------------------------------ audible rendering ------------------------------

_AUDIBLE_LINES = {
    "knock": "Ты слышишь стук в дверь из соседней локации.",
    "shout": "Ты слышишь крик из соседней локации.",
    "call": "Из соседней локации доносится зов.",
    "loud_sound": "Ты слышишь громкий звук из соседней локации.",
}

_QUOTE_RE = re.compile(r"[«\"“]([^»\"”]{1,200})[»\"”]")


def _extract_direct_speech(text: str) -> str:
    match = _QUOTE_RE.search(text or "")
    if match:
        return match.group(1).strip()
    return ""


def build_audible_line(event: Any) -> str:
    """Render what a character can *hear* from an adjacent location.

    Never leaks the full event content or visual details (ТЗ §7/§8): only a
    generic audible line based on the first matching stimulus. For events
    without stimuli, returns a generic sound line.
    """
    raw = getattr(event, "stimuli", None)
    if isinstance(event, dict):
        raw = event.get("stimuli")
    stimuli = parse_stimuli(raw)
    if not stimuli:
        return "Из соседней локации доносится звук."

    content = getattr(event, "content", "") if not isinstance(event, dict) else (event.get("content") or "")
    for stimulus in stimuli:
        if stimulus.type in _AUDIBLE_LINES:
            return _AUDIBLE_LINES[stimulus.type]
        if stimulus.type == "address":
            quote = _extract_direct_speech(content)
            if quote:
                return f"Из соседней локации доносится голос: «{quote}»"
            return "Из соседней локации доносится голос."
    return "Из соседней локации доносится звук."
