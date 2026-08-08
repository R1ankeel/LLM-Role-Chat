"""Deterministic character movement detection (Isolation FIS, Sprint 4, §9-§11).

``detect_character_movement`` reads a character's own reply text and decides
whether it describes a *completed* transition to an explicit location. It never
guesses a destination ("вышел из комнаты" without a target → ``None``) and never
fires on intention, future tense, negation or memory ("хочу пойти в кухню",
"не пошёл", "вспоминаю, как ходил в магазин").

``movement_signal`` is the boolean counterpart: does the text *evidence* an
actual (non-hypothetical) movement, with or without a resolvable destination?

WPE 3.0 Фаза 8 (И14): legacy-safety-net, НЕ источник истины. Источник
перемещений — `Action(move_to)` из tools/format (`apply_character_actions`);
этот regex-путь активен только как вход Consistency Validator и для
actions-off / text-only чатов.

Isolation rules (§10-§11):
- Per-sentence analysis: a "думаю..." clause cannot suppress the movement in
  the previous sentence, and a movement in one sentence is decided on its own.
- Suppression (intent / future / conditional / negation / memory) is scoped to
  the sentence that contains it.
- The location-name matcher uses short-prefix keys to tolerate Russian
  declension (e.g. "Кухня" matches "в кухню" via the key "кухн").
- A location is matched only by its LEADING word ("в лес" → "Лес у таверны",
  but "в таверну" never matches). Multi-word phrases resolve explicitly
  ("в комнату Кирка"). A non-unique leading stem (three "Комната …") is
  ambiguous → ``None``.
- Degenerate case: a chat with exactly one known location and clear movement
  evidence (with no self-reference) resolves to that location.
"""

from __future__ import annotations

import re

# Movement verbs — completed transitions (perfective) and in-progress
# imperfective present forms. Word-boundary matched (never fires inside
# "в виду"/"выводит").
_MOVEMENT_VERBS = (
    # completed arrivals
    "вошёл", "вошла", "вошли", "войду", "войдёт", "войдём",
    "зашёл", "зашла", "зашли", "зайдёт", "зайду",
    "пришёл", "пришла", "пришли", "приду", "придёт",
    "добрался", "добралась", "добрались", "добираюсь", "добирается",
    "дошёл", "дошла", "дошли", "дохожу", "доходит", "доходят",
    "направился", "направилась", "направились", "направляюсь", "направляется",
    "пошёл", "пошла", "пошли", "пошел",
    "вышел", "вышла", "вышли",
    "перешёл", "перешла", "прошёл", "прошла",
    "спустился", "спустилась", "поднялся", "поднялась",
    "вернулся", "вернулась", "возвращаюсь", "возвращается",
    "поднимаюсь", "поднимается", "спускаюсь", "спускается",
    # in-progress imperfective present forms
    "иду", "идёт", "идут", "идём", "идем", "идёте", "идете", "иди",
    "выхожу", "выходит", "выходят", "выходим",
    "захожу", "заходит", "заходят", "вхожу", "входит", "входят",
    "прихожу", "приходит", "приходят",
)

_VERB_PATTERN = re.compile(
    r"\b(?:%s)\b" % "|".join(sorted(_MOVEMENT_VERBS, key=len, reverse=True))
)

# Spatial prepositions: toward (в/на/к/до) and motion along/away (из/от/по/за).
# Used as the "spatial anchor" that separates real movement from, e.g.,
# "она выходит победителем из спора" (no anchor near the verb).
_SPATIAL_ANCHOR_PATTERN = re.compile(r"\b(?:в|на|к|до|из|от|по|за)\b")

# Intent / future / conditional markers (scoped to a sentence).
_INTENT_MARKERS = (
    "хочу", "хотел", "хотела", "хотелось",
    "собираюсь", "собирается", "собирался", "собиралась",
    "планирую", "планирует", "намерен", "намерена", "намереваюсь",
    "пойду", "пойдёт", "пойдём", "пойдете", "пойдешь",
    "пошёл бы", "пошла бы", "пошли бы", "сходил бы", "сходила бы",
    "мог бы", "могла бы", "могли бы",
)

# Memory / past references.
_MEMORY_MARKERS = (
    "вспомина", "помню", "помнишь", "помнит", "вспомнил", "вспомнила",
    "вчера", "когда-то", "раньше", "бывало",
)

_CONDITIONAL_PATTERN = re.compile(r"\bбы\b")

# Verbs that can express an arrival AT a person ("зашёл к Ольге").
_ARRIVAL_TO_PERSON = (
    "зашёл", "зашла", "зашли", "вошёл", "вошла", "вошли",
    "пришёл", "пришла", "пришли", "вхожу", "входит", "входят",
    "иду", "идёт", "идут", "шёл", "шла", "шли",
)


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"[.!?…]+", text) if s.strip()]


def _verb_matches(text_lower: str) -> list[re.Match]:
    return list(_VERB_PATTERN.finditer(text_lower))


def _is_suppressed(sentence_lower: str) -> bool:
    if any(marker in sentence_lower for marker in _INTENT_MARKERS):
        return True
    if any(marker in sentence_lower for marker in _MEMORY_MARKERS):
        return True
    if _CONDITIONAL_PATTERN.search(sentence_lower):
        return True
    return False


def _has_negated_verb(sentence_lower: str) -> bool:
    """Whether any movement verb is negated ("не иду", "не пошёл")."""
    for match in _verb_matches(sentence_lower):
        words = re.findall(r"[а-яё]+", sentence_lower[: match.start()])
        if "не" in words[-3:]:
            return True
    return False


def movement_signal(text: str) -> bool:
    """True when ``text`` evidences an actual (non-hypothetical) movement.

    A movement needs a movement verb AND a spatial anchor right after it
    ("выхожу из", "идёт по", "иду в"), with no negation/intent/future/
    conditional/memory marker in the same sentence. "Она выходит победителем
    из спора" carries no anchor next to the verb → False.
    """
    if not text or not text.strip():
        return False
    text_lower = text.lower()
    for sentence in _split_sentences(text_lower):
        if _is_suppressed(sentence):
            continue
        for match in _verb_matches(sentence):
            if _has_negated_verb(sentence):
                continue
            window = sentence[match.end() : match.end() + 8]
            if _SPATIAL_ANCHOR_PATTERN.search(window):
                return True
    return False


def _dedupe_known(known_locations: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for loc in known_locations or []:
        clean = (loc or "").strip()
        key = clean.casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(clean)
    return result


def _word_stems(word: str) -> list[str]:
    """Prefix stems of a word to tolerate Russian declension.

    "Кухня" → ["кухня", "кухн"] so "в кухню" matches via "кухн".
    """
    w = word.lower()
    if len(w) <= 2:
        return [w] if w else []
    stems = {w}
    if len(w) >= 4:
        stems.add(w[:4])
    if len(w) >= 5:
        stems.add(w[:5])
    return sorted(stems, key=len, reverse=True)


def _stem_matches(stems: set[str], word: str) -> bool:
    """True when ``word`` (declensed form) matches any stem as a prefix pair."""
    for stem in stems:
        if word.startswith(stem) or stem.startswith(word):
            return True
    return False


def _person_word_stems(
    location_names: list[str], character_names: dict[int, str]
) -> set[str]:
    """Stems of proper nouns: character names + capitalized interior words.

    In "Комната Кирка" the person name "Кирка" is capitalized and is not the
    first word; common nouns ("таверны" in "Лес у таверны") are excluded.
    """
    stems: set[str] = set()
    for cname in character_names.values():
        if cname:
            stems.update(_word_stems(cname))
    for loc in location_names:
        words = loc.split()
        for word in words[1:]:
            if word and word[0].isupper():
                stems.update(_word_stems(word))
    return stems


def _has_unresolved_toward_target(
    text_lower: str,
    known_locations: list[str],
    character_names: dict[int, str],
) -> bool:
    """True when the text moves toward a place that does NOT resolve.

    Gates the single-location fallback: «Я вошёл в таверну» must not fall back
    to «Лес у таверны», while «Я выхожу из комнаты» (no toward-target) and
    «Я иду к Кирку» (person name) may.
    """
    leading_stems: set[str] = set()
    for loc in known_locations:
        words = _loc_words(loc)
        if words:
            leading_stems.update(words[0])
    person_stems = _person_word_stems(known_locations, character_names)
    toward_re = re.compile(r"\b(?:в|на|к|до)\s+(?!сторон)([а-яё]{3,20})")
    for match in toward_re.finditer(text_lower):
        target = match.group(1)
        if _stem_matches(leading_stems, target):
            continue
        if _stem_matches(person_stems, target):
            continue
        return True
    return False


def _loc_words(loc_name: str) -> list[list[str]]:
    """Stem-sets per content word of a location name (skips short words)."""
    result: list[list[str]] = []
    for word in loc_name.split():
        if len(word) <= 2:
            continue
        result.append(_word_stems(word))
    return result


_TOWARD_PREP = r"\b(?:в|на|к|до)\s+(?!сторон)"
_PREP_TO_STEM = r"(?:[а-яё]{1,14}\s+){0,1}?"


def _match_toward_stem(sentence_lower: str, stems: list[str]) -> bool:
    for stem in stems:
        if re.search(_TOWARD_PREP + _PREP_TO_STEM + re.escape(stem), sentence_lower):
            return True
    return False


def _match_full_phrase(sentence_lower: str, word_stem_lists: list[list[str]]) -> bool:
    parts = [
        r"(?:%s)" % "|".join(re.escape(s) for s in stems)
        for stems in word_stem_lists
    ]
    inner = parts[0] + r"[а-яё]{0,8}"
    for part in parts[1:]:
        inner += r"(?:\s+[а-яё]{1,16}){0,2}?\s*" + part + r"[а-яё]{0,8}"
    return re.search(_TOWARD_PREP + _PREP_TO_STEM + inner, sentence_lower) is not None


def _resolve_destination(sentence_lower: str, known_locations: list[str]) -> str | None:
    """Resolve an explicit toward-phrase to a known location.

    Order: full multi-word phrase first ("в комнату Кирка"), then a unique
    leading word ("в лес" → "Лес у таверны"). A non-unique leading word is
    ambiguous → ``None``. Secondary words never match alone ("в таверну").
    """
    # 1. Full multi-word phrase.
    for loc in known_locations:
        words = _loc_words(loc)
        if len(words) >= 2 and _match_full_phrase(sentence_lower, words):
            return loc

    # 2. Unique leading word.
    for loc in known_locations:
        words = _loc_words(loc)
        if not words:
            continue
        leading = set(words[0])
        ambiguous = False
        for other in known_locations:
            other_words = _loc_words(other)
            if other != loc and other_words and (leading & set(other_words[0])):
                ambiguous = True
                break
        if ambiguous:
            continue
        if _match_toward_stem(sentence_lower, sorted(leading, key=len, reverse=True)):
            return loc
    return None


def _arrival_to_character(
    sentence_lower: str,
    character_name: str,
    character_names: dict[int, str],
    character_locations: dict[int, str],
) -> str | None:
    """Resolve an arrival targeted at another character by name.

    Only certain verbs count as arrival to a person ("зашёл к", "вошёл к",
    "пришёл к", "иду к"); "подошёл к" is NOT an arrival (§11). The speaker
    themself is never the target.
    """
    if not any(v in sentence_lower for v in _ARRIVAL_TO_PERSON):
        return None
    for cid, cname in character_names.items():
        if cname == character_name or not cname:
            continue
        cname_lower = cname.lower()
        if f" к {cname_lower}" in sentence_lower or f"к{cname_lower}" in sentence_lower:
            target_loc = (character_locations.get(cid, "") or "").strip()
            if target_loc:
                return target_loc
    return None


def _detect_in_sentence(
    sentence_lower: str,
    character_name: str,
    known_locations: list[str],
    character_locations: dict[int, str],
    character_names: dict[int, str],
) -> str | None:
    if _is_suppressed(sentence_lower):
        return None
    if not _verb_matches(sentence_lower):
        return None
    if _has_negated_verb(sentence_lower):
        return None
    target = _arrival_to_character(
        sentence_lower, character_name, character_names, character_locations
    )
    if target is not None:
        return target
    return _resolve_destination(sentence_lower, known_locations)


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
    - Only movement verbs + an explicit destination from ``known_locations``.
    - Intention/future/negation/memory → ``None`` (conservative).
    - "Вышел из комнаты" without a target → ``None``.
    - "Зашла к Ольге" → Ольга's location when it is known and non-empty.
    - Ambiguous destination (non-unique leading word) → ``None``.
    - Degenerate chats with exactly one known location resolve to it when the
      text evidences movement (no self-reference).
    """
    if not text or not text.strip():
        return None
    unique_known = _dedupe_known(known_locations)
    text_lower = text.lower()

    for sentence in _split_sentences(text_lower):
        result = _detect_in_sentence(
            sentence,
            character_name,
            unique_known,
            character_locations,
            character_names,
        )
        if result is not None:
            return result

    # Degenerate case: exactly one known location, clear movement evidence,
    # the character is not moving toward themself ("Я иду к Кирку" when the
    # speaker is Кирк → None), and no toward-target is left unresolved
    # ("Я вошёл в таверну" must not become "Лес у таверны").
    if (
        len(unique_known) == 1
        and character_name
        and character_name.lower() not in text_lower
        and movement_signal(text)
        and not _has_unresolved_toward_target(text_lower, unique_known, character_names)
    ):
        return unique_known[0]
    return None
