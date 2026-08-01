"""Deterministic relationship interpreter (State -> Interpretation).

Relationship State (numbers in DB) -> semantic labels. Pure functions, no DB
access, no LLM. These labels feed the generation prompt instead of raw metrics
so the character model has a single source of truth (the drivers/interpretation),
never conflicting raw numbers.

See docs/relations.md §4-§5.
"""

from dataclasses import dataclass, field
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Thresholds (from docs/relations.md §4)
# ---------------------------------------------------------------------------
TRUST_LOW = 30
TRUST_HIGH = 70
AFFECTION_LOW = 40
AFFECTION_HIGH = 70
ATTRACTION_LOW = 40
ATTRACTION_HIDDEN = 70
RESENTMENT_HIGH = 50
JEALOUSY_MODERATE = 30
JEALOUSY_HIGH = 60

# Derived combinations (docs/relations.md §4)
BONDED_AFFECTION = 70
BONDED_TRUST = 40          # affection >= 70 and trust < 40
HIDDEN_ATTRACTION_TRUST = 40
HIDDEN_ATTRACTION_RESENTMENT = 50
DISTRUST_TRUST = 30
DISTRUST_RESENTMENT = 50

# Open Issues (docs/relations.md §7.3): an open issue is an active plot hook —
# a derived label so it outranks base tendencies but stays tone-neutral.
OPEN_ISSUE_DERIVED = "открытый вопрос"
OPEN_ISSUE_DRIVER_WEIGHT = 5


_VOWELS = frozenset("аеёиоуыэюя")


def decline_name(name: str, case: str) -> str:
    """Best-effort Russian declension of a single-word proper name.

    Args:
        name: character name in nominative (e.g. "Борис", "Аня", "Андрей").
        case: "dative" (кому? -> Борису/Ане) or "accusative" (кого? ->
            Бориса/Аню).

    Falls back to the unchanged name for uncertain forms: names ending in "-ь"
    (Игорь/Любовь are ambiguous), multi-word names, and indeclinable vowels.
    """
    if not name or not isinstance(name, str):
        return ""
    name = name.strip()
    if len(name) < 3 or " " in name:
        return name
    last = name[-1]
    stem = name[:-1]

    if case == "dative":
        if last == "а":
            return stem + "е"
        if last == "я":
            return stem + "и" if name.endswith("ия") else stem + "е"
        if last == "й":
            return stem + "ю"
        if last == "ь":
            return name
        if last in _VOWELS:
            return name
        return name + "у"
    if case == "accusative":
        if last == "а":
            return stem + "у"
        if last == "я":
            return stem + "ю"
        if last == "й":
            return stem + "я"
        if last == "ь":
            return name
        if last in _VOWELS:
            return name
        return name + "а"
    return name


def _dative(name: str) -> str:
    return decline_name(name, "dative")


def _accusative(name: str) -> str:
    return decline_name(name, "accusative")


@dataclass
class RelationshipInterpretation:
    """Semantic labels derived from one directed relationship state.

    Labels are deliberately tone-neutral: they describe *what* the character
    feels, not *how* he expresses it (that is the personality card's job, §15).
    """

    relationship_type: str = ""
    trust: str = "medium"          # low | medium | high
    attachment: str = "medium"     # low | medium | high
    hostility: str = "low"         # low | high
    attraction: str = "none"       # none | visible | hidden
    jealousy: str = "none"         # none | moderate | high
    derived: list[str] = field(default_factory=list)


def _band(value: int, low: int, high: int, low_label: str, mid_label: str, high_label: str) -> str:
    if value < low:
        return low_label
    if value >= high:
        return high_label
    return mid_label


def interpret(
    rel: Any,
    *,
    open_issues: Iterable[Any] = (),
) -> RelationshipInterpretation:
    """Deterministically label one relationship from its numeric state.

    Args:
        rel: object exposing affection/trust/attraction/resentment/jealousy
            and relationship_type (ORM CharacterRelationship or a plain stub).
        open_issues: iterable of open issues for this edge (Sprint 1 item 5).
            Each item only needs to be truthy — the interpreter treats open
            issues as a signal that an unresolved hook exists, without reading
            their text (which is untrusted LLM data, see §14).

    Returns:
        RelationshipInterpretation with semantic labels and derived combos.
    """
    affection = int(getattr(rel, "affection", 0))
    trust = int(getattr(rel, "trust", 0))
    attraction = int(getattr(rel, "attraction", 0))
    resentment = int(getattr(rel, "resentment", 0))
    jealousy = int(getattr(rel, "jealousy", 0))

    open_issues = list(open_issues or ())

    trust_label = _band(trust, TRUST_LOW, TRUST_HIGH, "low", "medium", "high")
    attachment = _band(affection, AFFECTION_LOW, AFFECTION_HIGH, "low", "medium", "high")

    hostility = "high" if (resentment >= RESENTMENT_HIGH or jealousy >= JEALOUSY_HIGH) else "low"

    if attraction >= ATTRACTION_HIDDEN and (resentment >= HIDDEN_ATTRACTION_RESENTMENT or trust < HIDDEN_ATTRACTION_TRUST):
        attraction_label = "hidden"
    elif attraction >= ATTRACTION_LOW:
        attraction_label = "visible"
    else:
        attraction_label = "none"

    if jealousy >= JEALOUSY_HIGH:
        jealousy_label = "high"
    elif jealousy >= JEALOUSY_MODERATE:
        jealousy_label = "moderate"
    else:
        jealousy_label = "none"

    derived: list[str] = []
    if affection >= BONDED_AFFECTION and trust < BONDED_TRUST:
        derived.append("болезненная привязанность")
    if attraction >= ATTRACTION_HIDDEN and (resentment >= HIDDEN_ATTRACTION_RESENTMENT or trust < HIDDEN_ATTRACTION_TRUST):
        derived.append("скрытое влечение")
    if trust < DISTRUST_TRUST and resentment >= DISTRUST_RESENTMENT:
        derived.append("недоверие + обида")
    if open_issues:
        derived.append(OPEN_ISSUE_DERIVED)

    return RelationshipInterpretation(
        relationship_type=getattr(rel, "relationship_type", "") or "",
        trust=trust_label,
        attachment=attachment,
        hostility=hostility,
        attraction=attraction_label,
        jealousy=jealousy_label,
        derived=derived,
    )


def format_interpretation(
    interp: RelationshipInterpretation,
    target_name: str,
) -> str:
    """Natural-language tendency phrases for one relationship (no numbers).

    Neutral values (medium / none / low) emit nothing so a fresh neutral
    relationship stays a one-line "Имя: нейтральное" without false signals.
    """
    parts: list[str] = []

    dat = _dative(target_name)
    acc = _accusative(target_name)

    if interp.trust == "high":
        parts.append(f"Ты доверяешь {dat}.")
    elif interp.trust == "low":
        parts.append(f"Ты не доверяешь {dat}: проверяешь его слова, не всё говоришь.")

    if interp.attachment == "high":
        parts.append(f"Ты эмоционально привязан к {dat} и ищешь близости.")
    elif interp.attachment == "low":
        parts.append(f"Ты держишь {acc} на дистанции.")

    if "болезненная привязанность" in interp.derived:
        parts.append(f"Твоя привязанность к {dat} болезненна: ты держишься за него, но не доверяешь.")

    if interp.hostility == "high":
        parts.append(f"Ты помнишь обиду на {acc} и склонен возвращаться к причине конфликта.")

    if "недоверие + обида" in interp.derived:
        parts.append(f"Недоверие и обида к {dat} глубоки.")

    if OPEN_ISSUE_DERIVED in interp.derived:
        parts.append(f"Между тобой и {dat} остался нерешённый вопрос, и ты к нему мысленно возвращаешься.")

    if interp.jealousy == "high":
        parts.append(f"Тебя задевает, когда {target_name} проводит время с другими.")
    elif interp.jealousy == "moderate":
        parts.append(f"Ты иногда испытываешь ревность к {dat}.")

    if interp.attraction == "hidden":
        parts.append(f"Тебя тянет к {dat}, но ты стараешься этого не показывать.")
    elif interp.attraction == "visible":
        parts.append(f"Тебя тянет к {dat}; это может отражаться в манерах.")

    return " ".join(parts)


def format_interpretation_from_other(
    interp: RelationshipInterpretation,
    source_name: str,
) -> str:
    """Tendency phrases about how *another* character feels toward the listener.

    Used by the epistemic mask (docs/relations.md §10, Sprint 2 item 10): the
    listener (second person "тебе") learns the interpretation of the incoming
    edge source_name -> listener, WITHOUT any numbers. Phrases are deliberately
    gender-neutral and describe *what* the other feels, never commands.

    ``source_name`` is only used for readability here (kept for call symmetry);
    the phrases themselves address "ты/тебе/тебя".
    """
    parts: list[str] = []

    if interp.trust == "low":
        parts.append("не доверяет тебе и проверяет твои слова")
    elif interp.trust == "high":
        parts.append("доверяет тебе")

    if "болезненная привязанность" in interp.derived:
        parts.append("привязанность к тебе болезненна: держится за тебя, но не доверяет")
    elif interp.attachment == "high":
        parts.append("привязан к тебе и ищет близости")
    elif interp.attachment == "low":
        parts.append("держится от тебя на расстоянии")

    if interp.hostility == "high":
        parts.append("помнит обиду на тебя и часто возвращается к ней")

    if "недоверие + обида" in interp.derived:
        parts.append("недоверие и обида к тебе глубоки")

    if OPEN_ISSUE_DERIVED in interp.derived:
        parts.append("помнит о нерешённом вопросе между вами")

    if interp.jealousy == "high":
        parts.append("задевает, когда ты проводишь время с другими")
    elif interp.jealousy == "moderate":
        parts.append("иногда ревнует тебя")

    if interp.attraction == "hidden":
        parts.append("тянет к тебе, но это скрывается")
    elif interp.attraction == "visible":
        parts.append("тянет к тебе; это заметно по манерам")

    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Behavior Drivers (docs/relations.md §4-§5, Sprint 1 item 3)
# ---------------------------------------------------------------------------
# Drivers are *tendencies*, not commands: they describe what the character
# feels, never "should/must". Derived combinations outrank their base labels so
# the top-K selection keeps the most informative tendencies.
_DRIVER_WEIGHTS = {
    "болезненная привязанность": 6,
    "недоверие + обида": 6,
    "скрытое влечение": 5,
    OPEN_ISSUE_DERIVED: OPEN_ISSUE_DRIVER_WEIGHT,
}


def weighted_behavior_drivers(
    interp: RelationshipInterpretation,
    target_name: str,
) -> list[tuple[int, str]]:
    """Ranked (weight, tendency) pairs for one relationship (deterministic).

    Weights are stable across relationships so an aggregator can compare
    drivers from different pairs on a common scale.
    """
    dat = _dative(target_name)
    acc = _accusative(target_name)

    candidates: list[tuple[int, str]] = []

    derived_phrases = {
        "болезненная привязанность": f"Ты держишься за {acc}, но твоя привязанность к {dat} болезненна.",
        "недоверие + обида": f"Недоверие и обида к {dat} глубоки.",
        "скрытое влечение": f"Тебя тянет к {dat}, но ты скрываешь это.",
        OPEN_ISSUE_DERIVED: f"Ты помнишь о нерешённом вопросе с {dat} и возвращаешься к нему, когда появляется повод.",
    }
    for key, phrase in derived_phrases.items():
        if key in interp.derived:
            candidates.append((_DRIVER_WEIGHTS[key], phrase))

    if interp.trust == "low":
        candidates.append((4, f"Ты не доверяешь {dat}: проверяешь его слова, не всё говоришь."))
    if interp.hostility == "high":
        candidates.append((4, f"Ты помнишь обиду на {acc} и склонен возвращаться к причине конфликта."))
    if interp.jealousy == "high":
        candidates.append((4, f"Тебя задевает, когда {target_name} проводит время с другими."))
    if interp.attachment == "high":
        candidates.append((3, f"Ты эмоционально привязан к {dat} и ищешь близости."))
    if interp.attachment == "low":
        candidates.append((3, f"Ты держишь {acc} на дистанции."))
    if interp.attraction == "visible":
        candidates.append((2, f"Тебя тянет к {dat}; это может отражаться в манерах."))
    if interp.jealousy == "moderate":
        candidates.append((2, f"Ты иногда испытываешь ревность к {dat}."))
    if interp.trust == "high":
        candidates.append((1, f"Ты доверяешь {dat}."))

    candidates.sort(key=lambda item: (-item[0], item[1]))
    return candidates


def build_behavior_drivers(
    interp: RelationshipInterpretation,
    target_name: str,
    *,
    max_drivers: int | None = None,
) -> list[str]:
    """Ranked behavior tendencies for one relationship (deterministic, no DB).

    Wraps :func:`weighted_behavior_drivers` and returns the tendency texts.
    A fresh neutral relationship emits nothing.

    Args:
        interp: semantic labels from :func:`interpret`.
        target_name: name of the target character (nominative).
        max_drivers: cap on the returned tendencies; None keeps all sorted.
    """
    texts = [text for _, text in weighted_behavior_drivers(interp, target_name)]
    if max_drivers is not None:
        texts = texts[:max(0, int(max_drivers))]
    return texts
