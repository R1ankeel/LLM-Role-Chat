"""Witness-aware history filtering by character perception."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .config import settings
from .perception import (
    PerceptionWorldState,
    can_character_perceive_event,
    event_from_message,
    log_perception_decision,
    normalize_visibility,
    parse_target_ids,
    perceive,
)
from .prompt_builder import build_system_intervention_block
from .stimuli import build_audible_line, parse_stimuli, stimulus_targets
Presence = Literal["present", "mentioned", "audible", "absent", "told"]

# Presence values that count as real observation for memory / summary extraction.
MEMORY_OBSERVABLE_PRESENCES: frozenset[str] = frozenset({"present", "told"})

_TEMPLATES_PATH = Path(__file__).parent / "prompts" / "ru.json"
with _TEMPLATES_PATH.open(encoding="utf-8") as f:
    _TEMPLATES: dict[str, Any] = json.load(f)

_WITNESS_TEMPLATES = _TEMPLATES.get("witness", {})


def _get_attr(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _message_id(message: Any) -> int | None:
    message_id = _get_attr(message, "id")
    return int(message_id) if message_id is not None else None


def _character_name(
    message: Any,
    character_names: dict[int, str] | None = None,
) -> str:
    character_id = _get_attr(message, "character_id")
    if character_names and character_id:
        return character_names.get(character_id, "Персонаж")
    character = _get_attr(message, "character") if not isinstance(message, dict) else None
    if character is not None:
        return character.name
    return "Персонаж"


def _is_name_mentioned(content: str, name: str) -> bool:
    if not name or not content:
        return False
    pattern = rf"(?<!\w){re.escape(name)}(?!\w)"
    return bool(re.search(pattern, content, flags=re.IGNORECASE))


def _truncate_snippet(text: str, max_len: int = settings.witness_mentioned_snippet_len) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_len:
        return normalized
    return normalized[: max_len - 1].rstrip() + "…"


def compute_mvp_presence(
    message: Any,
    viewer_character_id: int,
    character_names: dict[int, str],
    *,
    same_round_ids: set[int] | None = None,
    viewer_location: str | None = None,
    viewer_location_id: Any = None,
    character_locations: dict[int, str] | None = None,
    adjacency_index: dict[str, set[str]] | None = None,
    world_state: PerceptionWorldState | None = None,
) -> Presence:
    """Compute witness presence for one message and one viewer.

    Location/visibility-aware. ``same_round_ids`` is ignored for forcing
    visibility (kept only for API compatibility). ``adjacency_index`` enables
    AUDIBLE / MENTIONED presence for events from adjacent locations.

    Cutover (WPE 3.0 Фаза 4): при ``settings.world_engine_perception_enabled``
    и переданном ``world_state`` решение принимает двухканальный
    ``perceive()``, а результат схлопывается в legacy-лестницу через
    ``perceive_to_presence`` (Renderer). Без ``world_state`` (legacy hot-path)
    решение остаётся на ``can_character_perceive_event`` — откат по флагу.
    """
    del same_round_ids  # no longer forces present — perception decides

    locations = character_locations or {}
    if viewer_location is None:
        viewer_location = locations.get(viewer_character_id, "")

    viewer_name = character_names.get(viewer_character_id, "")
    event = event_from_message(message)

    # Legacy messages without visibility metadata still work via defaults
    if not _get_attr(message, "visibility"):
        event["visibility"] = normalize_visibility(None)

    if settings.world_engine_perception_enabled and world_state is not None:
        result = perceive(
            world_state=world_state,
            event=event,
            observer={
                "character_id": viewer_character_id,
                "location": viewer_location or "",
                "location_id": None,
            },
        )
        return perceive_to_presence(result)

    presence, reason = can_character_perceive_event(
        viewer_character_id=viewer_character_id,
        viewer_location=viewer_location or "",
        event=event,
        viewer_name=viewer_name,
        adjacency_index=adjacency_index,
        viewer_location_id=viewer_location_id,
    )

    log_perception_decision(
        character_name=viewer_name or str(viewer_character_id),
        character_id=viewer_character_id,
        event_id=event.get("id"),
        visibility=event.get("visibility") or "local",
        event_location=event.get("location") or "",
        character_location=viewer_location or "",
        presence=presence,
        reason=reason,
    )
    return presence


def perceive_to_presence(result: Any, *, voice_known: bool = True) -> Presence:
    """Renderer (WPE.md §4): collapse a ``PerceptionResult`` to the legacy
    witness ``Presence`` ladder that drives the presence table and history.

    - visual full (стекло: действия видны, текст не слышен) → "present";
    - audio full + addressed/знакомый голос → "mentioned" (атрибуция крика);
    - audio full + незнакомый голос → "audible";
    - audio muffled → "audible" (шум, без семантики);
    - иначе → "absent".
    """
    visual = _get_attr(result, "visual_level", "none")
    audio = _get_attr(result, "audio_level", "none")
    addressed = bool(_get_attr(result, "addressed", False))
    if visual == "full":
        return "present"
    if audio == "full":
        if addressed or voice_known:
            return "mentioned"
        return "audible"
    if audio == "muffled":
        return "audible"
    return "absent"


def voice_familiarity(
    observer_id: int,
    author_id: int | None,
    known_voices: dict[int, set[int]] | None = None,
) -> bool:
    """Детерминированная атрибуция голоса (WPE.md §4, Фаза 6): известен ли
    автор наблюдателю.

    ``known_voices`` — ``{observer_id: set(author_ids)}``, построенный CRUD из
    ``CharacterRelationship`` (отношение наблюдателя к автору = голос знаком).
    ``None`` → True: константа Renderer'а Фазы 4 (голос считается знакомым) —
    откат при выключенном ``WORLD_ENGINE_PARTIAL_PERCEPTION_ENABLED``.
    """
    if author_id is None:
        return False
    if known_voices is None:
        return True
    return author_id in known_voices.get(observer_id, set())


def perceive_presence_for_character(
    message: Any,
    character: Any,
    world_state: PerceptionWorldState,
    *,
    voice_known: bool = True,
) -> Presence:
    """Two-channel presence for one ORM character (cutover path).

    Uses ``perceive()`` with the character's ``location``/``location_id``
    (Фаза 1 backfill) and collapses the result to the legacy ladder. No DB/LLM.
    """
    event = event_from_message(message)
    if not _get_attr(message, "visibility"):
        event["visibility"] = normalize_visibility(None)
    result = perceive(
        world_state=world_state,
        event=event,
        observer={
            "character_id": character.id,
            "location": getattr(character, "location", "") or "",
            "location_id": getattr(character, "location_id", None),
        },
    )
    return perceive_to_presence(result, voice_known=voice_known)


def render_perception_line(
    message: Any,
    result: Any,
    character_names: dict[int, str] | None = None,
    viewer_name: str | None = None,
    *,
    voice_known: bool = True,
) -> str | None:
    """Renderer (WPE.md §6): канало-зависимый текст строки из ``PerceptionResult``.

    Используется вместо ``format_line_for_presence``, когда есть двухканальный
    результат. ``None`` — полностью невоспринимаемое событие (шум, И11).

    - full/full → обычная строка «Автор: текст»;
    - full/none (стекло) → действия видны, слов не слышно;
    - none/muffled → audible-шум (semantics нет);
    - none/full (крик/голос из соседней локации) → атрибуция по голосу:
      знакомый — «голос <имя>», незнакомый — «чей-то голос».
    """
    visual = _get_attr(result, "visual_level", "none")
    audio = _get_attr(result, "audio_level", "none")
    content = _get_attr(message, "content") or ""
    role = _get_attr(message, "role")

    if visual == "none" and audio == "none":
        return None

    if visual == "full" and audio == "full":
        if role == "user":
            return f"Игрок: {content}"
        if role == "system":
            return f"Система: {content}"
        if role == "character":
            return f"{_character_name(message, character_names)}: {content}"
        return content

    if visual == "full":
        return "[Что-то происходит за стеклом: слов не слышно]"

    snippet = _truncate_snippet(content)
    if audio == "muffled":
        template = _WITNESS_TEMPLATES.get(
            "audible", "[Ты слышишь: {snippet}]"
        )
        return template.format(snippet=build_audible_line(message))

    if voice_known:
        author = _character_name(message, character_names)
        return f"[Ты слышишь голос {author}: {snippet}]"
    return f"[Чей-то голос: {snippet}]"


def build_character_recency_tail(
    messages: list,
    viewer_character_id: int,
    character_names: dict[int, str],
    *,
    player_id: int | None = None,
    attention_map: dict[int, float] | None = None,
) -> str:
    """Recency Tail (WPE.md §6, Ул.3, И15): P0-события одного персонажа.

    Собирает события, адресованные данному зрителю (addressed=true /
    remote_status=delivered), включая срочные вызовы из стимулов, и рендерит
    их блоком ``build_system_intervention_block``. Пересобирается отдельно для
    каждого персонажа: в хвост конкретного NPC попадают только его события.

    Sprint 4 (§11): при ``attention_enabled`` события с ``attention < LOW``
    («слышал фоном») в реакцию/recency tail НЕ идут (рендерится recent history
    при этом не меняется). ``None``/отсутствие в карте → legacy (HIGH bucket).
    """
    from .attention import attention_bucket

    lines: list[str] = []
    for message in messages:
        targets = parse_target_ids(_get_attr(message, "target_character_ids"))
        if viewer_character_id not in targets:
            continue
        author_id = _get_attr(message, "character_id")
        if author_id is not None and author_id == player_id:
            author = "Игрок"
        elif author_id is not None:
            author = character_names.get(author_id, "Персонаж")
        else:
            author = "Игрок"
        mid = _message_id(message)
        if attention_map and mid is not None and mid in attention_map:
            if attention_bucket(attention_map[mid]) == "low":
                continue
        lines.append(f"{author} обращается к тебе прямо сейчас. Отреагируй!")
    return build_system_intervention_block(lines)


def resolve_presence(
    message: Any,
    viewer_character_id: int,
    character_names: dict[int, str],
    presence_map: dict[int, Presence] | None,
    *,
    same_round_ids: set[int] | None = None,
    viewer_location: str | None = None,
    character_locations: dict[int, str] | None = None,
    adjacency_index: dict[str, set[str]] | None = None,
) -> Presence:
    """Use stored presence when available, otherwise compute from perception rules."""
    message_id = _message_id(message)
    if presence_map and message_id is not None and message_id in presence_map:
        return presence_map[message_id]
    return compute_mvp_presence(
        message,
        viewer_character_id,
        character_names,
        same_round_ids=same_round_ids,
        viewer_location=viewer_location,
        character_locations=character_locations,
        adjacency_index=adjacency_index,
    )


def format_line_for_presence(
    message: Any,
    presence: Presence,
    character_names: dict[int, str] | None = None,
    templates: dict[str, str] | None = None,
    viewer_name: str | None = None,
) -> str | None:
    """Format one history line according to witness presence."""
    tpl = templates or _WITNESS_TEMPLATES
    role = _get_attr(message, "role")
    content = _get_attr(message, "content") or ""

    if presence == "absent":
        return None

    if presence == "present":
        if role == "user":
            return f"Игрок: {content}"
        if role == "system":
            return f"Система: {content}"
        if role == "character":
            return f"{_character_name(message, character_names)}: {content}"
        return content

    snippet = _truncate_snippet(content)
    if presence == "mentioned":
        # Direct address (address/call stimulus aimed at the viewer) → a
        # first-person form; otherwise the generic mention placeholder.
        if viewer_name and stimulus_targets(
            parse_stimuli(_get_attr(message, "stimuli")), viewer_name
        ):
            author = _character_name(message, character_names)
            template = tpl.get("address", "{author} обращается к тебе: «{snippet}»")
            return template.format(author=author, snippet=snippet)
        template = tpl.get("mentioned", "[Тебя упомянули: {snippet}]")
        return template.format(snippet=snippet)

    if presence == "audible":
        template = tpl.get("audible", "[Ты слышишь: {snippet}]")
        return template.format(snippet=build_audible_line(message))

    if presence == "told":
        template = tpl.get("told", "[Тебе рассказали: {snippet}]")
        return template.format(snippet=snippet)

    return None


def filter_history_for_character(
    messages: list,
    viewer_character_id: int,
    character_names: dict[int, str],
    presence_map: dict[int, Presence] | None = None,
    *,
    same_round_ids: set[int] | None = None,
    max_len: int,
    viewer_location: str | None = None,
    character_locations: dict[int, str] | None = None,
    max_replies_per_character: int = 0,
    adjacency_index: dict[str, set[str]] | None = None,
) -> str:
    """Build witness-filtered dialogue text for one character (RP generation).

    When *max_replies_per_character* > 0, limits the number of consecutive
    replies shown per other character to that many, keeping only the most
    recent ones. The viewer's own replies are not capped. This prevents
    lexical contamination by reducing exposure to other characters' style.
    """
    recent = messages[-max_len:] if len(messages) > max_len else messages
    lines: list[str] = []
    char_count: dict[int, int] = {}
    viewer_name = character_names.get(viewer_character_id, "")
    for message in reversed(recent):
        presence = resolve_presence(
            message,
            viewer_character_id,
            character_names,
            presence_map,
            same_round_ids=same_round_ids,
            viewer_location=viewer_location,
            character_locations=character_locations,
            adjacency_index=adjacency_index,
        )
        line = format_line_for_presence(
            message, presence, character_names, viewer_name=viewer_name
        )
        if not line:
            continue

        cid = _get_attr(message, "character_id")
        is_self = (cid is not None and int(cid) == viewer_character_id)

        if not is_self and max_replies_per_character > 0 and cid is not None:
            cid_key = int(cid)
            if cid_key not in char_count:
                char_count[cid_key] = 0
            if char_count[cid_key] >= max_replies_per_character:
                continue
            char_count[cid_key] += 1

        lines.append(line)

    lines.reverse()
    return "\n".join(lines)


def filter_history_for_character_with_presence(
    messages: list,
    viewer_character_id: int,
    character_names: dict[int, str],
    presence_map: dict[int, Presence] | None = None,
    *,
    same_round_ids: set[int] | None = None,
    viewer_location: str | None = None,
    character_locations: dict[int, str] | None = None,
    max_replies_per_character: int = 0,
    max_len: int | None = None,
    adjacency_index: dict[str, set[str]] | None = None,
) -> list:
    """Return a list of message objects filtered by witness perception.

    Unlike filter_history_for_character which returns formatted text, this
    returns the original message objects that the character can perceive
    (present/told/mentioned). Use for vocabulary borrowing checks and
    repetition detection which operate on message objects.
    """
    if not settings.enable_witness_filter:
        return messages

    if max_len is None:
        max_len = len(messages) or 1
    recent = messages[-max_len:] if len(messages) > max_len else messages
    filtered: list = []
    char_count: dict[int, int] = {}

    for message in reversed(recent):
        presence = resolve_presence(
            message,
            viewer_character_id,
            character_names,
            presence_map,
            same_round_ids=same_round_ids,
            viewer_location=viewer_location,
            character_locations=character_locations,
            adjacency_index=adjacency_index,
        )

        # Skip absent messages
        if presence == "absent":
            continue

        cid = _get_attr(message, "character_id")
        is_self = (cid is not None and int(cid) == viewer_character_id)

        if not is_self and max_replies_per_character > 0 and cid is not None:
            cid_key = int(cid)
            if cid_key not in char_count:
                char_count[cid_key] = 0
            if char_count[cid_key] >= max_replies_per_character:
                continue
            char_count[cid_key] += 1

        filtered.append(message)

    filtered.reverse()
    return filtered


@dataclass
class ObservableEventLine:
    """One message included in a character's memory-observable context."""

    message_id: int | None
    presence: Presence
    line: str
    location: str
    content_preview: str
    reason: str = "observable"


@dataclass
class ObservableContext:
    """Character-specific context for memory extraction / summarization."""

    text: str
    lines: list[ObservableEventLine] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_observable_events(self) -> bool:
        return bool(self.text.strip()) and bool(self.lines)


def filter_history_for_memory_extraction(
    messages: list,
    viewer_character_id: int,
    character_names: dict[int, str],
    presence_map: dict[int, Presence] | None = None,
    *,
    same_round_ids: set[int] | None = None,
    max_len: int | None = None,
    viewer_location: str | None = None,
    character_locations: dict[int, str] | None = None,
    adjacency_index: dict[str, set[str]] | None = None,
    attention_map: dict[int, float] | None = None,
) -> ObservableContext:
    """Build memory-safe observable dialogue for one character.

    Reuses the same presence/perception rules as RP generation, but only
    includes ``present`` and ``told`` events. Soft ``mentioned``/``audible``
    snippets are excluded so remote name-drops cannot become hard memories.

    Sprint 4 (§11): при ``attention_enabled`` события с ``attention < LOW``
    («слышал фоном») в память НЕ идут, даже если ``present`` — воспринято, но
    не вошло в сознание. ``None``/отсутствие в карте → legacy (HIGH bucket).
    """
    from .attention import attention_bucket

    if max_len is None:
        max_len = len(messages) or 1
    recent = messages[-max_len:] if len(messages) > max_len else messages

    included: list[ObservableEventLine] = []
    skipped: list[dict[str, Any]] = []
    viewer_name = character_names.get(viewer_character_id, "")

    for message in recent:
        presence = resolve_presence(
            message,
            viewer_character_id,
            character_names,
            presence_map,
            same_round_ids=same_round_ids,
            viewer_location=viewer_location,
            character_locations=character_locations,
            adjacency_index=adjacency_index,
        )
        mid = _message_id(message)
        location = str(_get_attr(message, "location") or "")
        content = str(_get_attr(message, "content") or "")
        preview = _truncate_snippet(content, max_len=80)

        if presence not in MEMORY_OBSERVABLE_PRESENCES:
            skipped.append(
                {
                    "message_id": mid,
                    "presence": presence,
                    "location": location,
                    "preview": preview,
                    "reason": (
                        "not_visible"
                        if presence == "absent"
                        else "soft_mention_only"
                    ),
                }
            )
            continue

        # Sprint 4 (§11): attention < LOW → в память НЕ идёт (фон), при этом
        # рендер recent history не меняется (presence-лестница нетронута).
        if attention_map and mid is not None and mid in attention_map:
            if attention_bucket(attention_map[mid]) == "low":
                skipped.append(
                    {
                        "message_id": mid,
                        "presence": presence,
                        "location": location,
                        "preview": preview,
                        "reason": "low_attention_background",
                    }
                )
                continue

        # For memory: use full present content; for told keep the told template.
        line = format_line_for_presence(
            message, presence, character_names, viewer_name=viewer_name
        )
        if not line:
            skipped.append(
                {
                    "message_id": mid,
                    "presence": presence,
                    "location": location,
                    "preview": preview,
                    "reason": "empty_line",
                }
            )
            continue

        included.append(
            ObservableEventLine(
                message_id=mid,
                presence=presence,  # type: ignore[arg-type]
                line=line,
                location=location,
                content_preview=preview,
                reason="observable",
            )
        )

    text = "\n".join(item.line for item in included)
    return ObservableContext(text=text, lines=included, skipped=skipped)
