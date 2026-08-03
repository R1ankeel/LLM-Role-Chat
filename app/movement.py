"""Deterministic character movement detection (Isolation FIS, Sprint 4, §9-§11).

``detect_character_movement`` reads a character's own reply text and decides
whether it describes a *completed* transition to an explicit location. It never
guesses a destination ("вышел из комнаты" without a target → ``None``) and never
fires on intention, future tense, negation or memory ("хочу пойти в кухню",
"не пошёл", "вспоминаю, как ходил в магазин").

The location-name matcher uses short-prefix keys to tolerate Russian
declension (e.g. "Кухня" matches "в кухню" via the key "кухн"), mirroring the
approach previously inlined in ``chat_engine._detect_movement_in_text``.
"""

from __future__ import annotations

# Arrival verbs: completed (or in-progress arrival) transition into a place.
_ARRIVAL_VERBS = (
    "вошёл", "вошла", "вошли", "войду", "войдёт", "войдём",
    "зашёл", "зашла", "зашли", "захожу", "заходит", "заходят", "зайдёт", "зайду",
    "вхожу", "входит", "входят", "войдёт",
    "пришёл", "пришла", "пришли", "прихожу", "приходит", "приходят", "приду", "придёт",
    "добрался", "добралась", "добрались", "добираюсь", "добирается",
    "дошёл", "дошла", "дошли", "дохожу", "доходит",
    "направился", "направилась", "направились", "направляюсь", "направляется",
    "пошёл", "пошла", "пошли", "пошел",
    "вышел", "вышла", "вышли", "выехал",
    "перешёл", "перешла", "прошёл", "прошла",
    "спустился", "спустилась", "поднялся", "поднялась",
    "вернулся", "вернулась", "возвращаюсь", "возвращается",
)

# Prepositions toward a destination: "в/на/к <location>".
_ARRIVAL_PREPOSITIONS = (" в ", "в", " на ", "на", " к ", "к")

# Intention / future / conditional / negation / memory markers.
# A message containing any of these is treated conservatively as NOT a
# completed transition (ТЗ §9: не додумываем намерение/будущее/отрицание).
_SUPPRESS_MARKERS = (
    "хочу", "хотел", "хотела", "хотелось", "хотелось бы",
    "собираюсь", "собирается", "собирался", "собиралась",
    "планирую", "планирует", "намерен", "намерена", "намереваюсь",
    "думаю", "хотел бы", "хотела бы",
    "пойду", "пойдёт", "пойдём", "пойдете", "пойдешь", "пойдёт",
    "пошёл бы", "пошла бы", "пошли бы", "сходил бы", "сходила бы",
    "мог бы", "могла бы", "могли бы",
    "вспомина", "помню", "помнишь", "помнит", "вспомнил", "вспомнила",
    "вчера", "когда-то", "раньше", "бывало",
)

# Negated arrival verb substrings (e.g. "не пошёл", "не вошёл").
_NEGATED_PREFIXES = (
    " не пошёл", "не пошёл", " не пошла", "не пошла", " не пошли", "не пошли",
    " не вошёл", "не вошёл", " не вошла", "не вошла", " не вошли", "не вошли",
    " не зашёл", "не зашёл", " не зашла", "не зашла", " не зашли", "не зашли",
    " не пришёл", "не пришёл", " не пришла", "не пришла", " не пришли", "не пришли",
    " не вышёл", "не вышёл", " не вышла", "не вышла", " не вышли", "не вышли",
    " не пойду", "не пойду", " не вхожу", "не вхожу", " не захожу", "не захожу",
)


def _loc_keys(name: str) -> list[str]:
    """Location keywords with short prefixes for Russian declension matching.

    "Квартира" → ["квартира", "кварт"] so that "из квартиры" still matches the
    key "кварт" even though the full word does not appear in the genitive form.
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


def _has_arrival_verb(text_lower: str) -> bool:
    return any(verb in text_lower for verb in _ARRIVAL_VERBS)


def _is_suppressed(text_lower: str) -> bool:
    """True when the text expresses intent/future/conditional/memory/negation."""
    if any(marker in text_lower for marker in _SUPPRESS_MARKERS):
        return True
    if " бы " in text_lower:
        return True
    if any(marker in text_lower for marker in _NEGATED_PREFIXES):
        return True
    return False


def _referenced_toward(text_lower: str, location: str) -> bool:
    """Whether the location is referenced with a 'toward' preposition.

    Only ``в/на/к`` (toward) count — "из комнаты" (away from) never triggers
    an arrival, matching test §18 item 12.
    """
    keys = _loc_keys(location)
    for kw in keys:
        for prep in _ARRIVAL_PREPOSITIONS:
            if f"{prep}{kw}" in text_lower:
                return True
    return False


def detect_character_movement(
    text: str,
    character_name: str,
    known_locations: list[str],
    character_locations: dict[int, str],
    character_names: dict[int, str],
) -> str | None:
    """Detect a completed movement for ``character_name`` in ``text``.

    Returns the destination location name (from ``known_locations``, or a
    target character's current location for "зашёл к Ольге"-style moves), or
    ``None`` when no completed, explicit transition is described.

    Rules (§9-§11):
    - Only arrival verbs + an explicit destination from ``known_locations``.
    - Intention/future/negation/memory → ``None`` (conservative).
    - "Вышел из комнаты" without a target → ``None``.
    - "Зашла к Ольге" → Ольга's location when it is known and non-empty.
    - Empty destination location "" (shared scene) → ``None``.
    """
    if not text or not text.strip():
        return None
    text_lower = text.lower()

    if _is_suppressed(text_lower):
        return None
    if not _has_arrival_verb(text_lower):
        return None

    # 1. Explicit location from known_locations.
    for loc in known_locations:
        loc_clean = loc.strip()
        if not loc_clean:
            continue
        if _referenced_toward(text_lower, loc_clean):
            return loc_clean

    # 2. "Зашёл/вошёл/пришёл к <character>" → that character's location.
    return _arrival_to_character(
        text_lower, character_name, character_names, character_locations
    )


def _arrival_to_character(
    text_lower: str,
    character_name: str,
    character_names: dict[int, str],
    character_locations: dict[int, str],
) -> str | None:
    """Resolve an arrival targeted at another character by name.

    Only certain verbs count as arrival to a person ("зашёл к", "вошёл к",
    "пришёл к"); "подошёл к", "пошёл к" are NOT arrivals (§11).
    """
    arrival_to_person = (
        "зашёл", "зашла", "зашли", "вошёл", "вошла", "вошли",
        "пришёл", "пришла", "пришли", "вхожу", "входит", "входят",
    )
    if not any(v in text_lower for v in arrival_to_person):
        return None

    for cid, cname in character_names.items():
        if cname == character_name or not cname:
            continue
        cname_lower = cname.lower()
        if f" к {cname_lower}" in text_lower or f"к{cname_lower}" in text_lower:
            target_loc = (character_locations.get(cid, "") or "").strip()
            if target_loc:
                return target_loc
    return None
