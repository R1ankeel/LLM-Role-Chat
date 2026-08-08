"""Чистые хелперы локаций/адресатов (без DB и LLM).

Выделены из ``perception.py`` в Sprint 1 (§7.1): ``crud`` не должен импортировать
``perception`` (WPE-слой «обособленная группа»), но использует чистые функции
сравнения локаций и сериализации адресатов. ``perception.py`` реэкспортирует
эти символы, чтобы публичный API модуля не изменился.

Правило: только чистые функции (флаги ``config`` допускаются) — никакого DB/ORM.
"""

from __future__ import annotations

import json
from typing import Any

from .config import settings

# Communication channels that bridge location isolation
REMOTE_CHANNELS = frozenset({"magic", "phone", "radio", "messenger"})


def normalize_location(location: str | None) -> str:
    """Normalize location labels for comparison (legacy-bridge, WPE 3.0 Фаза 1)."""
    text = (location or "").strip()
    if settings.normalize_locations:
        return text.casefold()
    return text


def locations_match(a: str | None, b: str | None) -> bool:
    return normalize_location(a) == normalize_location(b)


def _adjacency_name(item: Any) -> str:
    """Extract an adjacent location name from a string or a permeability object."""
    if isinstance(item, dict):
        return str(item.get("name") or "").strip()
    return str(item).strip()


def _parse_adjacency_list(raw: Any) -> list[str]:
    """Parse a JSON list of adjacent location names (from ``locations.adjacent_to``).

    Tolerates the WPE 3.0 object form ``{"name": ..., "visual_permeability": ...,
    "audio_permeability": ...}`` — only the name is used (legacy 1D index).
    """
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
        if isinstance(data, list):
            return [n for n in (_adjacency_name(x) for x in data) if n]
        return []
    if isinstance(raw, list):
        return [n for n in (_adjacency_name(x) for x in raw) if n]
    return []


def serialize_adjacency(names: list[str] | None) -> str:
    """Serialize an adjacency list to a JSON string for ``locations.adjacent_to``."""
    if not names:
        return "[]"
    cleaned = [str(x).strip() for x in names if str(x).strip()]
    return json.dumps(cleaned, ensure_ascii=False)


def _get_attr(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


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


# Каноническая "Общая сцена": пустая строка и явное имя — эквиваленты.
SHARED_SCENE_NAME = "общая сцена"


def is_shared_scene(location_norm: str) -> bool:
    """Whether a (normalized) location label is the canonical shared scene."""
    return location_norm == "" or location_norm == SHARED_SCENE_NAME
