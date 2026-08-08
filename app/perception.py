"""Character perception: who may know about a world event.

World events are messages. Each character gets only events they can perceive.
This is the primary isolation mechanism (context filtering), not prompt text.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from typing import Any, Literal

from .config import settings
# Sprint 1 (§7.1): чистые хелперы локаций/адресатов вынесены в perception_utils
# (crud не импортирует perception, но использует эти функции). Реэкспорт
# сохраняет прежний публичный API модуля (from .perception import ...).
from .perception_utils import (
    REMOTE_CHANNELS,
    SHARED_SCENE_NAME,
    _adjacency_name,
    _get_attr,
    _parse_adjacency_list,
    build_adjacency_index,
    is_shared_scene,
    locations_match,
    normalize_location,
    parse_target_ids,
    serialize_adjacency,
    serialize_target_ids,
)
from .stimuli import (
    AUDIBLE_STIMULUS_TYPES,
    INVISIBILITY_STIMULUS_TYPE,
    Stimulus,
    parse_stimuli,
)

logger = logging.getLogger(__name__)

Presence = Literal["present", "mentioned", "audible", "absent", "told"]
PerceptionLevel = Literal["visible", "audible", "mentioned", "absent"]
Visibility = Literal["private", "local", "targeted", "public", "global"]

_LEVEL_TO_PRESENCE: dict[PerceptionLevel, Presence] = {
    "visible": "present",
    "audible": "audible",
    "mentioned": "mentioned",
    "absent": "absent",
}

VALID_VISIBILITIES = frozenset(
    {"private", "local", "targeted", "public", "global"}
)

# ---------------------------------------------------------------------------
# LEGACY-BRIDGE (WPE 3.0, Фаза 1) — строковое сравнение локаций.
# Существующий движок (witness_model / chat_engine / context_builder) и
# `can_character_perceive_event` сравнивают локации по нормализованным
# строкам (`normalize_location` / `locations_match`). Новый read-path
# `perceive()` (ниже, WPE 3.0) сравнивает канонические `location_id`
# (Фаза 1), строковый путь остаётся как legacy-bridge до cutover'а Фазы 4
# и как fallback для legacy-чатов (откат: `WORLD_ENGINE_LOCATIONS_ENABLED`).
# ---------------------------------------------------------------------------


def same_location_identity(
    *,
    viewer_location: str | None,
    event_location: str | None,
    viewer_location_id: Any = None,
    event_location_id: Any = None,
) -> bool:
    """Canonical co-location (WPE 3.0): location_id identity when both sides
    have it, else legacy string comparison (``locations_match``).

    Two distinct canonical locations that share the same label (e.g. renamed
    "Кухня" vs stale "Кухня" row) are NOT co-located — the id is the source of
    truth; the string is only a legacy-bridge fallback for rows without ids.
    """
    if viewer_location_id is not None and event_location_id is not None:
        try:
            return int(viewer_location_id) == int(event_location_id)
        except (TypeError, ValueError):
            pass
    return locations_match(viewer_location, event_location)


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


def _toponym_prefix_fallback(a: str, b: str) -> bool:
    """Optional heuristic: shared leading toponym token.

    e.g. «Квартира Ольги» / «Квартира Бориса» → adjacent. Only used when
    ``settings.adjacency_fallback_enabled`` is True (default False).
    """
    a_tokens = [t for t in re.split(r"\s+", a.strip()) if t]
    b_tokens = [t for t in re.split(r"\s+", b.strip()) if t]
    if not a_tokens or not b_tokens:
        return False
    return a_tokens[0] == b_tokens[0]


def are_locations_adjacent(
    a: str | None,
    b: str | None,
    adjacency_index: Mapping[str, set[str]] | None = None,
) -> bool:
    """Whether two locations are adjacent.

    ``adjacency_index`` is a normalized map built by ``build_adjacency_index``
    from explicit ``locations.adjacent_to`` links. Without explicit links the
    locations are NOT adjacent (conservative, no regressions for existing chats).
    The heuristic prefix fallback is only used when explicitly enabled.
    """
    a_norm = normalize_location(a)
    b_norm = normalize_location(b)
    if not a_norm or not b_norm or a_norm == b_norm:
        return False
    if adjacency_index:
        return b_norm in adjacency_index.get(a_norm, set())
    if settings.adjacency_fallback_enabled:
        return _toponym_prefix_fallback(a_norm, b_norm)
    return False


def build_adjacency_index(locations: list[Any]) -> dict[str, set[str]]:
    """Build a normalized location -> set(neighbors) index from ORM/dict locations.

    Reads ``adjacent_to`` (JSON string or list of names) on each location.
    Symmetric: a link A→B also makes B→A.
    """
    index: dict[str, set[str]] = {}
    for loc in locations:
        name = normalize_location(_get_attr(loc, "name"))
        if not name:
            continue
        neighbors = _parse_adjacency_list(_get_attr(loc, "adjacent_to"))
        for neighbor in neighbors:
            neighbor_norm = normalize_location(neighbor)
            if not neighbor_norm or neighbor_norm == name:
                continue
            index.setdefault(name, set()).add(neighbor_norm)
            index.setdefault(neighbor_norm, set()).add(name)
    return index


def get_perception_level(
    *,
    viewer_location: str | None,
    event_location: str | None,
    event_text: str = "",
    viewer_name: str = "",
    adjacency_index: Mapping[str, set[str]] | None = None,
    stimuli: list[Stimulus] | list[dict] | str | None = None,
    targets: list[int] | None = None,
    viewer_character_id: int | None = None,
    channel: str = "direct",
    event_location_id: Any = None,
    viewer_location_id: Any = None,
) -> tuple[PerceptionLevel, str]:
    """Decide how well a character perceives a spatially-scoped (LOCAL) event.

    Rules (ТЗ §6-§8, §14; Plans/isolation-fix.md §1.4):
    - Same location → ``visible`` regardless of stimuli. Co-location is decided
      by canonical ``location_id`` (WPE 3.0) when both sides have it; the
      string comparison is only a fallback for legacy rows without ids.
    - ``address``/``call`` aimed at the viewer from an adjacent location →
      ``mentioned`` (only physically reachable addressing).
    - Loud stimulus (knock/shout/loud_sound/call) from an adjacent location →
      ``audible``.
    - Far unrelated location → ``absent`` even when addressed by name.
    """
    ids_known = event_location_id is not None and viewer_location_id is not None
    same_id = False
    if ids_known:
        try:
            same_id = int(event_location_id) == int(viewer_location_id)
        except (TypeError, ValueError):
            ids_known = False
    if same_id:
        return "visible", "SAME_LOCATION"
    if not ids_known and locations_match(viewer_location, event_location):
        return "visible", "SAME_LOCATION"

    stimulus_list = parse_stimuli(stimuli)
    adjacent = are_locations_adjacent(viewer_location, event_location, adjacency_index)

    for stimulus in stimulus_list:
        if stimulus.type in ("address", "call"):
            if _stimulus_targets_viewer(stimulus, viewer_name):
                if adjacent:
                    return "mentioned", "MENTIONED_ADDRESS"
                if channel in REMOTE_CHANNELS and viewer_character_id in (targets or []):
                    return "mentioned", "MENTIONED_REMOTE_ADDRESS"
                return "absent", "UNREACHABLE_ADDRESS"

    if adjacent:
        for stimulus in stimulus_list:
            if stimulus.type in AUDIBLE_STIMULUS_TYPES:
                return "audible", f"ADJACENT_{stimulus.type.upper()}"
        return "absent", "ADJACENT_QUIET"

    return "absent", "DIFFERENT_LOCATION"


def _stimulus_targets_viewer(stimulus: Stimulus, viewer_name: str) -> bool:
    if not viewer_name or not stimulus.target_character:
        return False
    return normalize_location(stimulus.target_character) == normalize_location(viewer_name)


def normalize_visibility(value: str | None) -> str:
    text = (value or settings.default_event_visibility).strip().lower()
    if text not in VALID_VISIBILITIES:
        return settings.default_event_visibility
    return text


def event_from_message(message: Any) -> dict[str, Any]:
    """Extract perception fields from a Message ORM/dict/namespace."""
    return {
        "id": _get_attr(message, "id"),
        "role": _get_attr(message, "role") or "",
        "character_id": _get_attr(message, "character_id"),
        "content": _get_attr(message, "content") or "",
        "location": _get_attr(message, "location") or "",
        "location_id": _get_attr(message, "location_id"),
        "visibility": normalize_visibility(_get_attr(message, "visibility")),
        "channel": (_get_attr(message, "channel") or "direct").strip().lower(),
        "target_character_ids": parse_target_ids(
            _get_attr(message, "target_character_ids")
            if _get_attr(message, "target_character_ids") is not None
            else _get_attr(message, "target_ids")
        ),
        "stimuli": parse_stimuli(_get_attr(message, "stimuli")),
    }


def can_character_perceive_event(
    *,
    viewer_character_id: int,
    viewer_location: str | None,
    event: dict[str, Any] | Any,
    viewer_name: str = "",
    adjacency_index: Mapping[str, set[str]] | None = None,
    viewer_location_id: Any = None,
) -> tuple[Presence, str]:
    """Decide if a character may receive an event in their LLM context.

    Returns (presence, reason_code).
    Supports remote channels (magic/phone/radio/messenger) that bridge locations.
    ``adjacency_index`` enables AUDIBLE / MENTIONED levels for adjacent locations.
    ``viewer_location_id`` (WPE 3.0 canonical identity, optional) is preferred
    over string comparison when both the viewer and the event have ids.
    """
    if not isinstance(event, dict):
        event = event_from_message(event)

    role = (event.get("role") or "").strip().lower()
    visibility = normalize_visibility(event.get("visibility"))
    event_location = event.get("location") or ""
    event_location_id = event.get("location_id")
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

    # Remote channels bridge location isolation, but only for viewers that are
    # genuinely NOT co-located with the author. A viewer in the same location
    # hears the speech in person, so the channel label attached by the model or
    # the keyword detector must not hide it nor upgrade it to a remote delivery —
    # fall through to the local spatial path below (isolation hardening).
    if channel in REMOTE_CHANNELS and not same_location_identity(
        viewer_location=viewer_location,
        event_location=event_location,
        viewer_location_id=viewer_location_id,
        event_location_id=event_location_id,
    ):
        if viewer_character_id in targets:
            return "present", f"REMOTE_CHANNEL_{channel.upper()}"
        # If not targeted but still a remote channel, others may hear only if mentioned
        if viewer_name and _name_mentioned(content, viewer_name):
            return "mentioned", f"REMOTE_MENTIONED_{channel.upper()}"
        return "absent", f"REMOTE_NOT_TARGET_{channel.upper()}"

    # LOCAL (default): spatial perception via perception levels
    level, reason = get_perception_level(
        viewer_location=viewer_location,
        event_location=event_location,
        event_text=content,
        viewer_name=viewer_name,
        adjacency_index=adjacency_index,
        stimuli=event.get("stimuli"),
        targets=targets,
        viewer_character_id=viewer_character_id,
        channel=channel,
        event_location_id=event_location_id,
        viewer_location_id=viewer_location_id,
    )
    return _LEVEL_TO_PRESENCE[level], reason


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


# ---------------------------------------------------------------------------
# WPE 3.0 — двухканальное восприятие (Plans/WPE.md §2/§4, И13)
# Фаза 0: чистая функция `perceive` + проницаемость рёбер по каналам.
# Фаза 1: `perceive` сравнивает канонические `location_id` (включается
# флагом `WORLD_ENGINE_LOCATIONS_ENABLED`); без флага/без id — строковое
# сравнение (legacy-bridge, откат).
# Легаси-1D шкала (visible/audible/mentioned/absent) остаётся над этим блоком
# до Фазы 4, где `can_character_perceive_event` удаляется (§9).
# ---------------------------------------------------------------------------

from dataclasses import dataclass, field as dataclass_field

VisualPermeability = Literal["full", "partial", "none"]
AudioPermeability = Literal["full", "muffled", "none"]

# Обратная совместимость (WPE.md §2, §13.7): ребро без явных значений —
# стена между комнатами: visual=none, audio=muffled.
DEFAULT_EDGE_VISUAL = "none"
DEFAULT_EDGE_AUDIO = "muffled"

# Громкие стимулы повышают audio_level: muffled -> full.
LOUD_STIMULUS_TYPES = frozenset(AUDIBLE_STIMULUS_TYPES)

_VALID_VISUAL = frozenset({"full", "partial", "none"})
_VALID_AUDIO = frozenset({"full", "muffled", "none"})


@dataclass(frozen=True)
class EdgePermeability:
    """Проницаемость ребра локаций по каналам (И13)."""

    visual: VisualPermeability = DEFAULT_EDGE_VISUAL
    audio: AudioPermeability = DEFAULT_EDGE_AUDIO


@dataclass(frozen=True)
class PerceptionWorldState:
    """Pure snapshot of the world at event time (no DB, no LLM).

    ``adjacency`` — нормализованный индекс ``loc_norm -> {neighbor_norm:
    EdgePermeability}`` (см. ``build_permeability_index``).
    ``thread_deliveries`` — id персонажей, которым событие доставлено через
    тред/канал (источник ``remote_status``, WPE.md §4).
    """

    adjacency: Mapping[str, Mapping[str, EdgePermeability]] = dataclass_field(
        default_factory=dict
    )
    thread_deliveries: frozenset[int] = dataclass_field(default_factory=frozenset)


def _validate_permeability(
    value: Any, default: str, allowed: frozenset[str]
) -> str:
    text = str(value or "").strip().lower()
    if text not in allowed:
        return default
    return text


def _parse_adjacency_items(raw: Any) -> list[Any]:
    """Parse ``locations.adjacent_to`` into a list of items (str or dict)."""
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
        if isinstance(data, list):
            return data
        return []
    if isinstance(raw, list):
        return raw
    return []


def _parse_edge_item(item: Any) -> tuple[str, EdgePermeability] | None:
    """Parse one adjacency item (string name or permeability object)."""
    if isinstance(item, dict):
        name = str(item.get("name") or "").strip()
        if not name:
            return None
        edge = EdgePermeability(
            visual=_validate_permeability(
                item.get("visual_permeability"), DEFAULT_EDGE_VISUAL, _VALID_VISUAL
            ),
            audio=_validate_permeability(
                item.get("audio_permeability"), DEFAULT_EDGE_AUDIO, _VALID_AUDIO
            ),
        )
        return name, edge
    name = str(item).strip()
    if not name:
        return None
    return name, EdgePermeability()


def parse_adjacency_edges(raw: Any) -> dict[str, EdgePermeability]:
    """Parse ``locations.adjacent_to`` into normalized-name -> EdgePermeability."""
    result: dict[str, EdgePermeability] = {}
    for item in _parse_adjacency_items(raw):
        parsed = _parse_edge_item(item)
        if parsed is None:
            continue
        name, edge = parsed
        result[normalize_location(name)] = edge
    return result


def serialize_adjacency_edges(
    edges: list[str] | list[dict[str, Any]] | None,
) -> str:
    """Serialize an adjacency list (names and/or permeability objects) to JSON.

    Чистые имена сериализуются как строки (обратная совместимость); объекты —
    в формате ``{"name", "visual_permeability", "audio_permeability"}``.
    Не подключено к read-path (Фаза 0) — используется тестами и будущим CRUD.
    """
    if not edges:
        return "[]"
    items: list[Any] = []
    for entry in edges:
        if isinstance(entry, dict):
            name = str(entry.get("name") or "").strip()
            if not name:
                continue
            item: dict[str, Any] = {"name": name}
            visual = _validate_permeability(
                entry.get("visual_permeability"), "", _VALID_VISUAL
            )
            audio = _validate_permeability(
                entry.get("audio_permeability"), "", _VALID_AUDIO
            )
            if visual:
                item["visual_permeability"] = visual
            if audio:
                item["audio_permeability"] = audio
            items.append(item)
        else:
            name = str(entry).strip()
            if name:
                items.append(name)
    return json.dumps(items, ensure_ascii=False)


def build_permeability_index(
    locations: list[Any],
) -> dict[str, dict[str, EdgePermeability]]:
    """Normalized location -> {neighbor_norm: EdgePermeability}, symmetric.

    Читает ``adjacent_to`` (JSON-строка или список строк/объектов) на каждом
    объекте локации (ORM или dict). Ребро без явных значений проницаемости —
    ``visual=none, audio=muffled`` (обратная совместимость).
    """
    index: dict[str, dict[str, EdgePermeability]] = {}
    for loc in locations:
        name = normalize_location(_get_attr(loc, "name"))
        if not name:
            continue
        for neighbor, edge in parse_adjacency_edges(
            _get_attr(loc, "adjacent_to")
        ).items():
            if not neighbor or neighbor == name:
                continue
            index.setdefault(name, {})[neighbor] = edge
            index.setdefault(neighbor, {})[name] = edge
    return index


def same_canonical_location(
    *,
    event_location: str,
    observer_location: str,
    event_location_id: Any = None,
    observer_location_id: Any = None,
) -> bool:
    """Каноническая проверка «одна локация» (WPE 3.0, Фаза 1).

    При включённом `WORLD_ENGINE_LOCATIONS_ENABLED` и наличии `location_id`
    с обеих сторон идентичность решается **по id**: синонимичные строки
    («Кухня» / «кухня» / устаревший алиас) → одна локация; разные id →
    разные локации даже при совпадении строк (id — источник истины).
    Если id с какой-либо стороны отсутствует (legacy-чат до backfill) —
    legacy-bridge: сравнение нормализованных строк (`normalize_location`),
    как в Фазе 0. Откат Фазы 1 — выключить флаг, вернётся только строковое
    сравнение.
    """
    if settings.world_engine_locations_enabled:
        if event_location_id is not None and observer_location_id is not None:
            try:
                return int(event_location_id) == int(observer_location_id)
            except (TypeError, ValueError):
                pass
    return bool(
        event_location and observer_location and event_location == observer_location
    )


def perceive(
    *,
    world_state: PerceptionWorldState,
    event: Mapping[str, Any],
    observer: Mapping[str, Any],
) -> Any:
    """Двухканальное восприятие события наблюдателем (WPE.md §4, И13).

    Чистая функция: без БД и LLM. Возвращает ``schemas.PerceptionResult``.

    - ``event``: ``location`` (str), ``location_id`` (int, опц., Фаза 1),
      ``stimuli`` (list | str | None),
      ``target_character_ids`` (list[int]), ``character_id`` (автор, опц.).
    - ``observer``: ``location`` (str), ``location_id`` (int, опц., Фаза 1),
      ``character_id`` (int).
    - ``world_state``: ``PerceptionWorldState`` (граф проницаемости +
      доставки тредов).

    Правила:
    - собственная речь автора / общая сцена / одна локация → full/full;
      «одна локация» решается канонически: по `location_id` (Фаза 1) при
      включённом `WORLD_ENGINE_LOCATIONS_ENABLED`, иначе — по строкам
      (legacy-bridge);
    - удалённый канал (magic/phone/radio/messenger, Фаза 6): адресат из
      `world_state.thread_deliveries` получает `remote_status=delivered` и
      content (full/full) независимо от локации (Golden #6/#15);
    - невидимость (Фаза 6, Golden #19): при `WORLD_ENGINE_PARTIAL_PERCEPTION_ENABLED`
      событие в одной локации со стимулом `invisible` → `visual=none`, `audio=full`;
    - соседство → проницаемость ребра по каналам; громкий стимул повышает
      ``muffled`` до ``full``;
    - дальняя локация → none/none (И11 — никогда не додумывается);
    - ``addressed`` — только из ``target_character_ids`` (И7);
    - ``remote_status`` — из ``world_state.thread_deliveries``.
    """
    from .schemas import PerceptionResult  # локальный импорт против цикла

    event_location = normalize_location(event.get("location"))
    observer_location = normalize_location(observer.get("location"))
    observer_id = observer.get("character_id")
    targets = parse_target_ids(event.get("target_character_ids"))
    stimuli = parse_stimuli(event.get("stimuli"))
    author_id = event.get("character_id")
    channel = (event.get("channel") or "direct").strip().lower()

    # Собственная речь всегда полностью известна автору
    if author_id is not None and observer_id is not None:
        try:
            if int(author_id) == int(observer_id):
                return PerceptionResult(
                    visual_level="full", audio_level="full"
                )
        except (TypeError, ValueError):
            pass

    # Удалённый канал (Фаза 6): доставка через Thread/ThreadParticipantState —
    # адресат получает событие независимо от локации (WPE.md §4, Golden #6/#15).
    if (
        channel in REMOTE_CHANNELS
        and observer_id in world_state.thread_deliveries
    ):
        return PerceptionResult(
            visual_level="full",
            audio_level="full",
            addressed=observer_id in targets,
            remote_status="delivered",
        )

    invisible = (
        settings.world_engine_partial_perception_enabled
        and any(s.type == INVISIBILITY_STIMULUS_TYPE for s in stimuli)
    )

    if is_shared_scene(event_location) or is_shared_scene(observer_location):
        if invisible:
            visual, audio = "none", "full"
        else:
            visual, audio = "full", "full"
    elif same_canonical_location(
        event_location=event_location,
        observer_location=observer_location,
        event_location_id=event.get("location_id"),
        observer_location_id=observer.get("location_id"),
    ):
        if invisible:
            visual, audio = "none", "full"
        else:
            visual, audio = "full", "full"
    else:
        edge = None
        if observer_location:
            edge = (world_state.adjacency or {}).get(
                observer_location, {}
            ).get(event_location)
        if edge is None:
            visual, audio = "none", "none"
        else:
            visual = edge.visual
            audio = edge.audio
            if (
                audio == "muffled"
                and any(s.type in LOUD_STIMULUS_TYPES for s in stimuli)
            ):
                audio = "full"

    addressed = observer_id in targets
    remote_status = (
        "delivered" if observer_id in world_state.thread_deliveries else "none"
    )
    return PerceptionResult(
        visual_level=visual,  # type: ignore[arg-type]
        audio_level=audio,  # type: ignore[arg-type]
        addressed=addressed,
        remote_status=remote_status,
    )
