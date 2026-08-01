"""Detect semantic action loops and scene stagnation in multi-character RP."""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

from .config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Action taxonomy (extensible). Patterns are matched against lowercased text.
# ---------------------------------------------------------------------------

ACTION_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    (
        "move_closer",
        (
            r"приближ",
            r"подош[её]л",
            r"подошла",
            r"сокращ\w*\s+(расстоян|дистан)",
            r"сокращаю\s+расстоян",
            r"делаю\s+шаг\s+(ближ|впер[её]д)",
            r"шаг\w*\s+(ближ|впер[её]д)",
            r"наклоня\w*.{0,20}(ближ|к\s+(не|нем|ней|нему|мне))",
            r"наклон\w*\s+ближ",
            r"меньше\s+пространств",
            r"почти\s+не\s+оставляя\s+пространств",
            r"сокращая\s+расстоян",
            r"ещ[её]\s+немного\s+(ближе|ближе)",
            r"move\s+closer",
            r"step\s+(closer|forward)",
            r"lean\s+(in|closer)",
            r"close\s+the\s+(gap|distance)",
        ),
    ),
    (
        "step_back",
        (
            r"отступ",
            r"отодвиг",
            r"шаг\w*\s+назад",
            r"отход",
            r"отстраня",
            r"step\s+back",
            r"pull\s+away",
            r"backed\s+away",
        ),
    ),
    (
        "maintain_eye_contact",
        (
            r"не\s+отвод\w*\s*.{0,20}взгляд",
            r"не\s+отрыва\w*\s*.{0,20}взгляд",
            r"не\s+отрывая\s*.{0,20}взгляд",
            r"не\s+отрываю\s*.{0,20}взгляд",
            r"смотр\w*\s*.{0,25}(в\s+глаз|прямо\s+в\s+глаз)",
            r"гляд\w*\s*.{0,25}(в\s+глаз|прямо)",
            r"пристальн\w*\s*(смотр|взгляд|гляд)",
            r"встреч\w*\s*.{0,15}взгляд",
            r"взгляд\w*\s*в\s+глаз",
            r"глаза\s+в\s+глаза",
            r"прямо\s+в\s+глаз",
            r"eye\s+contact",
            r"stare\s+into",
            r"lock(ed|ing)?\s+(eyes|gaze)",
            r"hold(s|ing)?\s+(her|his|their|my)\s+gaze",
        ),
    ),
    (
        "look_at_other",
        (
            r"смотр\w*\s+на\s+(не[ег]о|не[её]|него|тебя|вас)",
            r"гляд\w*\s+на\s+",
            r"look(s|ing)?\s+at\s+(him|her|them|you)",
            r"gaze[sd]?\s+at",
        ),
    ),
    (
        "smile",
        (
            r"усмех",
            r"улыб",
            r"ухмыл",
            r"улыбну",
            r"\bsmile[sd]?\b",
            r"\bgrin(s|ned|ning)?\b",
            r"smirk",
        ),
    ),
    (
        "verbal_challenge",
        (
            r"следующ\w*\s+шаг",
            r"хват\w*\s+ли\s+у\s+тебя\s+смел",
            r"завышаешь\s+планку",
            r"это\s+вызов",
            r"обязан\s+принять",
            r"провоцир",
            r"challenge",
            r"dare\s+you",
            r"prove\s+it",
            r"try\s+me",
        ),
    ),
    (
        "touch",
        (
            r"каса\w*",
            r"дотрон",
            r"трога\w*",
            r"кладу\s+.{0,10}рук",
            r"беру\s+.{0,10}за\s+рук",
            r"обним",
            r"touch(es|ed|ing)?",
            r"caress",
            r"hold(s|ing)?\s+(her|his|their)\s+hand",
        ),
    ),
    (
        "kiss",
        (
            r"целу",
            r"поцел",
            r"целовать",
            r"\bkiss(es|ed|ing)?\b",
        ),
    ),
    (
        "sit_down",
        (
            r"сад\w*\s",
            r"присел",
            r"усад",
            r"sit[s]?\s+down",
            r"sat\s+down",
        ),
    ),
    (
        "stand_up",
        (
            r"вста[еюл]",
            r"подним\w*\s",
            r"stand[s]?\s+up",
            r"stood\s+up",
            r"get[s]?\s+up",
        ),
    ),
    (
        "leave",
        (
            r"уход",
            r"ушел",
            r"ушла",
            r"покида",
            r"направл\w*\s+к\s+выходу",
            r"\bleave[sd]?\b",
            r"walk(s|ed|ing)?\s+away",
            r"exit(s|ed|ing)?",
        ),
    ),
    (
        "ask_question",
        (
            r"\?",
            r"спрашив",
            r"поинтерес",
            r"\bask(s|ed|ing)?\b",
            r"what\s+(do|did|are|is|was)\b",
            r"why\s+",
            r"how\s+",
        ),
    ),
    (
        "change_topic",
        (
            r"кстати",
            r"смен\w*\s+тем",
            r"другой\s+вопрос",
            r"поговорим\s+о",
            r"by\s+the\s+way",
            r"changing\s+(the\s+)?subject",
            r"anyway[, ]",
        ),
    ),
    (
        "counter_provoke",
        (
            r"предпочитаю\s+переходить\s+от\s+слов",
            r"от\s+слов\s+к\s+делу",
            r"не\s+слишком\s+ли\s+ты",
            r"accepted\s+(the\s+)?challenge",
            r"two\s+can\s+play",
        ),
    ),
]

SOFT_ACTIONS = frozenset({"smile", "look_at_other", "maintain_eye_contact"})

PROGRESSION_ACTIONS = frozenset(
    {
        "touch",
        "kiss",
        "leave",
        "sit_down",
        "stand_up",
        "step_back",
        "change_topic",
        "ask_question",
    }
)

DISTANCE_CLOSER = frozenset({"move_closer"})
DISTANCE_FARTHER = frozenset({"step_back", "leave"})
CONTACT_ACTIONS = frozenset({"touch", "kiss"})

_ACTION_SPAN_RE = re.compile(r"\*([^*]+)\*")
_TOKEN_RE = re.compile(r"[a-zA-Zа-яА-ЯёЁ0-9]+", re.UNICODE)

_COMPILED_PATTERNS: list[tuple[str, list[re.Pattern[str]]]] = [
    (action_id, [re.compile(p, re.IGNORECASE | re.UNICODE) for p in patterns])
    for action_id, patterns in ACTION_PATTERNS
]


@dataclass
class ActionTurn:
    """One character utterance reduced to action classes."""

    character_id: int | None
    text: str
    actions: list[str] = field(default_factory=list)

    @property
    def action_set(self) -> frozenset[str]:
        return frozenset(self.actions)


@dataclass
class RepetitionAnalysis:
    """Structured result of loop / stagnation detection."""

    is_repetitive: bool
    score: float
    repeated_actions: list[str] = field(default_factory=list)
    stagnation: bool = False
    progression_score: float = 1.0
    reason: str = ""
    interaction_pattern: str = ""
    character_level: bool = False
    interaction_level: bool = False
    cooldown_hits: list[str] = field(default_factory=list)

    def as_log_fields(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 3),
            "progression": round(self.progression_score, 3),
            "stagnation": self.stagnation,
            "actions": self.repeated_actions,
            "interaction_pattern": self.interaction_pattern,
            "character_level": self.character_level,
            "interaction_level": self.interaction_level,
        }


def normalize_text(text: str) -> str:
    """Lowercase, strip markup noise, collapse whitespace."""
    if not text:
        return ""
    t = text.lower().replace("ё", "е")
    t = re.sub(r"[*«»\"'`]+", " ", t)
    t = re.sub(r"[^\w\s\?\!\.\,а-яА-Яa-zA-Z0-9]+", " ", t, flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(normalize_text(text))


def token_jaccard(a: str, b: str) -> float:
    ta, tb = set(tokenize(a)), set(tokenize(b))
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


def _match_actions_in_text(text: str) -> list[str]:
    """Return ordered unique action ids found in text."""
    if not text:
        return []
    lowered = text.lower().replace("ё", "е")
    found: list[str] = []
    seen: set[str] = set()
    for action_id, patterns in _COMPILED_PATTERNS:
        for pat in patterns:
            if pat.search(lowered):
                if action_id not in seen:
                    seen.add(action_id)
                    found.append(action_id)
                break
    return found


def extract_actions(text: str) -> list[str]:
    """Extract semantic action classes from a character reply.

    Prefers *italic* action spans; also scans the full text so dialogue-only
    challenges are still detected.
    """
    if not text or not text.strip():
        return []

    actions: list[str] = []
    seen: set[str] = set()

    spans = _ACTION_SPAN_RE.findall(text)
    scan_parts = list(spans) + [text]

    for part in scan_parts:
        for action_id in _match_actions_in_text(part):
            if action_id not in seen:
                seen.add(action_id)
                actions.append(action_id)
    return actions


def _message_character_id(message: Any) -> int | None:
    cid = getattr(message, "character_id", None)
    if cid is not None:
        return int(cid)
    if isinstance(message, dict):
        raw = message.get("character_id")
        return int(raw) if raw is not None else None
    return None


def _message_role(message: Any) -> str:
    role = getattr(message, "role", None)
    if role is None and isinstance(message, dict):
        role = message.get("role")
    return str(role or "")


def _message_content(message: Any) -> str:
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    return str(content or "")


def _character_turns_from_history(
    messages: Iterable[Any],
    *,
    window_size: int,
) -> list[ActionTurn]:
    """Build action turns from recent character messages (oldest → newest)."""
    char_msgs = [m for m in messages if _message_role(m) == "character"]
    if window_size > 0:
        char_msgs = char_msgs[-window_size:]
    turns: list[ActionTurn] = []
    for m in char_msgs:
        text = _message_content(m)
        turns.append(
            ActionTurn(
                character_id=_message_character_id(m),
                text=text,
                actions=extract_actions(text),
            )
        )
    return turns


def _core_actions(actions: Iterable[str]) -> frozenset[str]:
    """Actions that matter for loop detection."""
    s = frozenset(actions)
    core = s - SOFT_ACTIONS
    return core if core else s


def _jaccard_sets(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 0.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _textual_repetition_score(
    candidate: str,
    same_character_texts: list[str],
) -> float:
    if not candidate or not same_character_texts:
        return 0.0
    scores = [token_jaccard(candidate, prev) for prev in same_character_texts]
    return max(scores) if scores else 0.0


def _action_overlap_score(
    candidate_actions: list[str],
    prior_same_char: list[ActionTurn],
) -> tuple[float, list[str]]:
    if not candidate_actions or not prior_same_char:
        return 0.0, []
    cand = _core_actions(candidate_actions)
    if not cand:
        return 0.0, []

    best = 0.0
    repeated: Counter[str] = Counter()
    for turn in prior_same_char:
        prior = _core_actions(turn.actions)
        if not prior:
            continue
        score = _jaccard_sets(cand, prior)
        if score > best:
            best = score
        for a in cand & prior:
            repeated[a] += 1

    window_counts: Counter[str] = Counter()
    for turn in prior_same_char:
        window_counts.update(_core_actions(turn.actions))
    sticky = [a for a, c in window_counts.items() if c >= 2 and a in cand]
    for a in sticky:
        repeated[a] += 1

    ordered = [a for a, _ in repeated.most_common()]
    if len(cand) >= settings.repetition_min_bundle_size and best >= 0.5:
        best = min(1.0, best + 0.15)
    if sticky and len(sticky) >= settings.repetition_min_bundle_size:
        best = min(1.0, max(best, 0.75))
    return best, ordered


def _cooldown_hits(
    candidate_actions: list[str],
    prior_same_char: list[ActionTurn],
    *,
    cooldown_turns: int,
) -> list[str]:
    if cooldown_turns <= 0 or not candidate_actions or not prior_same_char:
        return []
    recent = prior_same_char[-cooldown_turns:]
    recent_all: list[str] = []
    for t in recent:
        recent_all.extend(t.actions)
    if PROGRESSION_ACTIONS & set(recent_all):
        return []

    recent_core: set[str] = set()
    for t in recent:
        recent_core |= set(_core_actions(t.actions))

    hits = []
    for a in candidate_actions:
        if a in SOFT_ACTIONS:
            continue
        if a in recent_core:
            hits.append(a)
    return hits


def _interaction_loop_score(
    turns_with_candidate: list[ActionTurn],
) -> tuple[float, str, list[str]]:
    """Detect multi-character oscillating / stagnant interaction patterns."""
    if len(turns_with_candidate) < 4:
        return 0.0, "", []

    cores = [_core_actions(t.actions) for t in turns_with_candidate]

    by_char: dict[int | None, list[frozenset[str]]] = {}
    for t, core in zip(turns_with_candidate, cores):
        by_char.setdefault(t.character_id, []).append(core)

    self_sims: list[float] = []
    sticky_actions: Counter[str] = Counter()
    for _cid, seq in by_char.items():
        if len(seq) < 2:
            continue
        for i in range(1, len(seq)):
            sim = _jaccard_sets(seq[i - 1], seq[i])
            self_sims.append(sim)
            for a in seq[i - 1] & seq[i]:
                sticky_actions[a] += 1

    all_core_counts: Counter[str] = Counter()
    for core in cores:
        all_core_counts.update(core)
    global_sticky = [a for a, c in all_core_counts.items() if c >= 3]

    score = 0.0
    if self_sims:
        avg_self = sum(self_sims) / len(self_sims)
        if avg_self >= 0.5:
            score = max(score, avg_self)
        if avg_self >= 0.7 and len(self_sims) >= 2:
            score = max(score, min(1.0, avg_self + 0.1))

    if global_sticky and len(global_sticky) >= settings.repetition_min_bundle_size:
        score = max(score, 0.8)
    elif global_sticky:
        score = max(score, 0.55)

    if len(by_char) >= 2 and self_sims and sum(self_sims) / len(self_sims) >= 0.55:
        char_unions = [set().union(*seq) if seq else set() for seq in by_char.values()]
        if len(char_unions) >= 2:
            shared_all = set.intersection(*char_unions) if char_unions else set()
            shared_all = {
                a
                for a in shared_all
                if a not in SOFT_ACTIONS or a == "maintain_eye_contact"
            }
            if len(shared_all) >= settings.repetition_min_bundle_size:
                score = max(score, 0.85)
                sticky_actions.update(shared_all)

    pattern_parts = [a for a, _ in sticky_actions.most_common(6)]
    if not pattern_parts:
        pattern_parts = global_sticky[:6]
    pattern = " + ".join(pattern_parts) if pattern_parts else ""
    if len(by_char) >= 2 and pattern:
        pattern = f"interaction loop: {pattern}"

    return min(1.0, score), pattern, pattern_parts


def _progression_and_stagnation(
    turns_with_candidate: list[ActionTurn],
) -> tuple[float, bool, list[str]]:
    """Estimate scene progression (high=good) and stagnation flag."""
    if len(turns_with_candidate) < 2:
        return 1.0, False, []

    cores = [_core_actions(t.actions) for t in turns_with_candidate]
    all_actions: list[str] = []
    for t in turns_with_candidate:
        all_actions.extend(t.actions)

    flat_core = [a for core in cores for a in core]
    if not flat_core:
        return 0.7, False, []

    unique = len(set(flat_core))
    total = len(flat_core)
    novelty = unique / total if total else 1.0

    closer_n = sum(1 for a in all_actions if a in DISTANCE_CLOSER)
    farther_n = sum(1 for a in all_actions if a in DISTANCE_FARTHER)
    contact_n = sum(1 for a in all_actions if a in CONTACT_ACTIONS)
    progress_n = sum(1 for a in all_actions if a in PROGRESSION_ACTIONS)

    distance_penalty = 0.0
    if closer_n >= 3 and farther_n == 0 and contact_n == 0:
        distance_penalty = min(0.7, 0.2 * closer_n)
    if farther_n >= 3 and closer_n == 0 and contact_n == 0:
        distance_penalty = min(0.7, 0.2 * farther_n)

    progression = novelty
    if progress_n:
        progression = min(1.0, progression + 0.25 * min(progress_n, 3))
    progression = max(0.0, progression - distance_penalty)

    counts = Counter(flat_core)
    sticky = [a for a, c in counts.items() if c >= 3]
    multi_sticky = [a for a, c in counts.items() if c >= 2 and a not in SOFT_ACTIONS]

    stagnation = False
    if progression <= (1.0 - settings.stagnation_threshold) and len(turns_with_candidate) >= 4:
        stagnation = True
    if closer_n >= 4 and contact_n == 0 and farther_n == 0:
        stagnation = True
        progression = min(progression, 0.15)
    if (
        len(multi_sticky) >= settings.repetition_min_bundle_size
        and progress_n == 0
        and len(turns_with_candidate) >= 4
    ):
        stagnation = True
        progression = min(progression, 0.25)
    if sticky and progress_n == 0:
        stagnation = True
        progression = min(progression, 0.2)

    return progression, stagnation, sticky or multi_sticky


def _extract_ngrams(text: str, n: int = 2) -> set[tuple[str, ...]]:
    """Extract character-level or word-level n-grams from text.

    Returns word-level n-grams for lexical repetition detection.
    """
    if not text:
        return set()
    words = re.findall(r'\b\w+\b', text.lower())
    if len(words) < n:
        return set()
    return set(tuple(words[i:i + n]) for i in range(len(words) - n + 1))


def _lexical_ngram_score(
    candidate_text: str,
    same_character_texts: list[str],
) -> float:
    """Score lexical n-gram overlap between candidate and same-character history.

    Checks bigram and trigram overlap. Returns 0.0–1.0 where >=0.5 means
    the character is reusing the same phrasal patterns.
    """
    if not candidate_text or not same_character_texts:
        return 0.0
    cand_bigrams = _extract_ngrams(candidate_text, 2)
    cand_trigrams = _extract_ngrams(candidate_text, 3)

    if not cand_bigrams and not cand_trigrams:
        return 0.0

    max_bigram_overlap = 0.0
    max_trigram_overlap = 0.0
    for prev in same_character_texts:
        prev_bigrams = _extract_ngrams(prev, 2)
        prev_trigrams = _extract_ngrams(prev, 3)
        if cand_bigrams and prev_bigrams:
            bigram_j = len(cand_bigrams & prev_bigrams) / len(cand_bigrams | prev_bigrams)
            max_bigram_overlap = max(max_bigram_overlap, bigram_j)
        if cand_trigrams and prev_trigrams:
            trigram_j = len(cand_trigrams & prev_trigrams) / len(cand_trigrams | prev_trigrams)
            max_trigram_overlap = max(max_trigram_overlap, trigram_j)

    # Weight: bigrams matter more (0.7), trigrams add extra penalty (0.3)
    combined = max_bigram_overlap * 0.7 + max_trigram_overlap * 0.3
    return min(1.0, combined)


def analyze_response(
    candidate_text: str,
    *,
    character_id: int,
    messages: list | None = None,
    character_names: dict[int, str] | None = None,
    window_size: int | None = None,
    threshold: float | None = None,
    cooldown_turns: int | None = None,
) -> RepetitionAnalysis:
    """Analyze whether candidate_text continues a behavioral / scene loop.

    ``messages`` should be recent chat history (character + user). The candidate
    is not yet in history.
    """
    _ = character_names
    window = settings.repetition_window_size if window_size is None else window_size
    thr = settings.repetition_threshold if threshold is None else threshold
    cool = settings.action_cooldown_turns if cooldown_turns is None else cooldown_turns
    history = list(messages or [])

    candidate_actions = extract_actions(candidate_text)
    prior_turns = _character_turns_from_history(history, window_size=window)
    same_char = [t for t in prior_turns if t.character_id == character_id]
    same_char_texts = [t.text for t in same_char]

    text_score = _textual_repetition_score(candidate_text, same_char_texts)
    action_score, repeated_from_char = _action_overlap_score(
        candidate_actions, same_char
    )
    cool_hits = _cooldown_hits(candidate_actions, same_char, cooldown_turns=cool)

    candidate_turn = ActionTurn(
        character_id=character_id,
        text=candidate_text,
        actions=candidate_actions,
    )
    window_with_cand = (prior_turns + [candidate_turn])[-max(window, 1) :]
    interaction_score, interaction_pattern, sticky_actions = _interaction_loop_score(
        window_with_cand
    )
    progression, stagnation, stagnant_actions = _progression_and_stagnation(
        window_with_cand
    )

    # Lexical n-gram repetition (Phase 5)
    ngram_score = _lexical_ngram_score(candidate_text, same_char_texts)

    repeated = list(
        dict.fromkeys(
            repeated_from_char + sticky_actions + stagnant_actions + cool_hits
        )
    )
    repeated.sort(key=lambda a: (a in SOFT_ACTIONS, a))

    soft_only = bool(candidate_actions) and not (
        set(candidate_actions) - SOFT_ACTIONS
    )

    score = 0.0
    score = max(score, text_score * 0.95)
    score = max(score, action_score * 0.9)
    score = max(score, interaction_score * 0.95)
    score = max(score, ngram_score * 0.4)
    if cool_hits and not soft_only:
        score = max(score, 0.7)
        if len(cool_hits) >= 2:
            score = max(score, 0.85)
    if stagnation:
        score = max(score, 0.75)
        if progression < 0.2:
            score = max(score, 0.88)

    if soft_only and text_score < settings.repetition_text_jaccard and interaction_score < 0.7:
        score *= 0.35

    if not prior_turns:
        score = min(score, text_score)

    character_level = (
        text_score >= settings.repetition_text_jaccard
        or action_score >= thr
        or bool(cool_hits and not soft_only)
    )
    interaction_level = interaction_score >= thr or (
        stagnation and interaction_score >= 0.5
    )

    is_repetitive = score >= thr and (
        character_level or interaction_level or stagnation
    )

    if is_repetitive and soft_only and not stagnation and text_score < 0.9:
        is_repetitive = False
        score = min(score, thr - 0.01)

    reason_parts: list[str] = []
    if text_score >= settings.repetition_text_jaccard:
        reason_parts.append("почти дословный повтор собственных недавних реплик")
    if action_score >= 0.5:
        reason_parts.append("повторяющийся набор смысловых действий")
    if cool_hits:
        reason_parts.append(f"попадание в кулдаун действий: {', '.join(cool_hits)}")
    if interaction_level:
        reason_parts.append("цикл многостороннего паттерна взаимодействия")
    if stagnation:
        reason_parts.append("состояние сцены не развивается")
    if not reason_parts and is_repetitive:
        reason_parts.append("повтор поведенческого паттерна без изменения состояния")

    reason = (
        "Сцена продолжает повторять один и тот же паттерн взаимодействия без развития состояния"
        if stagnation and interaction_level
        else "; ".join(reason_parts)
        if reason_parts
        else "ok"
    )

    return RepetitionAnalysis(
        is_repetitive=is_repetitive,
        score=round(min(1.0, score), 3),
        repeated_actions=repeated,
        stagnation=stagnation,
        progression_score=round(progression, 3),
        reason=reason,
        interaction_pattern=interaction_pattern,
        character_level=character_level and is_repetitive,
        interaction_level=interaction_level and is_repetitive,
        cooldown_hits=cool_hits,
    )


def build_repetition_feedback(analysis: RepetitionAnalysis) -> str:
    """Build targeted retry instruction from detector result (not for chat history)."""
    actions = analysis.repeated_actions or [
        "одно и то же физическое или разговорное действие"
    ]
    action_lines = "\n".join(f"- {a}" for a in actions[:8])
    pattern = (
        analysis.interaction_pattern
        or analysis.reason
        or "повторяющийся поведенческий паттерн"
    )

    return (
        "ОБНАРУЖЕН ЦИКЛ СЦЕНЫ.\n\n"
        "Твои недавние ответы повторяют один и тот же поведенческий паттерн.\n\n"
        f"Паттерн: {pattern}\n\n"
        "Уже повторялось:\n"
        f"{action_lines}\n\n"
        "НЕ повторяй эти действия в следующем ответе.\n"
        "Сцена должна развиваться.\n\n"
        "Выбери действительно другое действие, реакцию, решение, тему или последствие.\n"
        "Не описывай очередную вариацию тех же действий через синонимы.\n"
        "Не просто перефразируй то же поведение.\n"
        "Не затягивай один и тот же паттерн взаимодействия.\n\n"
        "Продолжай естественно в роли персонажа."
    )


def build_repetition_feedback_block(feedback: str) -> str:
    """Wrap feedback for injection into the generation user context."""
    text = (feedback or "").strip()
    if not text:
        return ""
    return f"<scene_loop_control>\n{text}\n</scene_loop_control>"
