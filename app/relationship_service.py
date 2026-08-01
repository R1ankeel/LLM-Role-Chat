"""Service layer for character relationship CRUD and delta application."""

import logging
import re
from datetime import datetime
from typing import Any, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .config import settings
from .models import (
    DEFAULT_AFFECTION,
    DEFAULT_ATTRACTION,
    DEFAULT_JEALOUSY,
    DEFAULT_RELATIONSHIP_TYPE,
    DEFAULT_RESENTMENT,
    DEFAULT_TRUST,
    CharacterRelationship,
    RelationshipEvent,
    RelationshipIssue,
)
from .relationship_interpreter import (
    format_interpretation,
    format_interpretation_from_other,
    interpret,
    weighted_behavior_drivers,
)
from .schemas import ISSUE_TYPES, IssueDelta, RelationshipDelta
from .prompt_builder import (
    build_behavior_drivers_block as _wrap_drivers_block,
    build_epistemic_mask_block as _wrap_epistemic_block,
    build_open_issues_block as _wrap_open_issues_block,
)

logger = logging.getLogger(__name__)

MAX_DELTA = settings.relationship_max_delta
VALID_TYPES = set(settings.relationship_valid_types)
TRANSITIONS: dict[str, set[str]] = {
    k: set(v) for k, v in settings.relationship_transition_rules.items()
}

# Prompt-injection markers that invalidate an issue text (§14). Issue text is
# LLM-produced data that later lands in another LLM's context, so obvious
# instruction markers are rejected, not silently kept.
_ISSUE_INSTRUCTION_MARKERS = (
    "игнорируй",
    "игнорировать",
    "ignore",
    "system:",
    "developer:",
    "забудь предыдущие",
)
_CTRL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
async def get_or_create_relationship(
    db: AsyncSession,
    chat_id: int,
    source_id: int,
    target_id: int,
) -> CharacterRelationship:
    stmt = select(CharacterRelationship).where(
        CharacterRelationship.source_character_id == source_id,
        CharacterRelationship.target_character_id == target_id,
    )
    result = await db.execute(stmt)
    rel = result.scalar_one_or_none()
    if rel is not None:
        return rel
    rel = CharacterRelationship(
        chat_id=chat_id,
        source_character_id=source_id,
        target_character_id=target_id,
        relationship_type=DEFAULT_RELATIONSHIP_TYPE,
        affection=DEFAULT_AFFECTION,
        trust=DEFAULT_TRUST,
        attraction=DEFAULT_ATTRACTION,
        resentment=DEFAULT_RESENTMENT,
        jealousy=DEFAULT_JEALOUSY,
    )
    db.add(rel)
    await db.commit()
    await db.refresh(rel)
    return rel


async def get_relationship(
    db: AsyncSession,
    source_id: int,
    target_id: int,
) -> Optional[CharacterRelationship]:
    stmt = select(CharacterRelationship).where(
        CharacterRelationship.source_character_id == source_id,
        CharacterRelationship.target_character_id == target_id,
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def list_relationships_for_character(
    db: AsyncSession,
    character_id: int,
    chat_id: Optional[int] = None,
) -> list[CharacterRelationship]:
    stmt = select(CharacterRelationship).where(
        CharacterRelationship.source_character_id == character_id,
    )
    if chat_id is not None:
        stmt = stmt.where(CharacterRelationship.chat_id == chat_id)
    stmt = stmt.options(selectinload(CharacterRelationship.target_character))
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def list_received_relationships(
    db: AsyncSession,
    character_id: int,
) -> list[CharacterRelationship]:
    stmt = select(CharacterRelationship).where(
        CharacterRelationship.target_character_id == character_id,
    ).options(selectinload(CharacterRelationship.source_character))
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def update_relationship_fields(
    db: AsyncSession,
    rel: CharacterRelationship,
    *,
    relationship_type: Optional[str] = None,
    affection: Optional[int] = None,
    trust: Optional[int] = None,
    attraction: Optional[int] = None,
    resentment: Optional[int] = None,
    jealousy: Optional[int] = None,
    description: Optional[str] = None,
) -> CharacterRelationship:
    if relationship_type is not None:
        rel.relationship_type = relationship_type
    if affection is not None:
        rel.affection = max(0, min(100, affection))
    if trust is not None:
        rel.trust = max(0, min(100, trust))
    if attraction is not None:
        rel.attraction = max(0, min(100, attraction))
    if resentment is not None:
        rel.resentment = max(0, min(100, resentment))
    if jealousy is not None:
        rel.jealousy = max(0, min(100, jealousy))
    if description is not None:
        rel.description = description
    rel.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(rel)
    return rel


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def validate_transition(
    current_type: str,
    new_type: str,
) -> bool:
    if new_type == current_type:
        return True
    allowed = TRANSITIONS.get(current_type, set())
    return new_type in allowed


def clamp_metric(value: int) -> int:
    return max(0, min(100, value))


# ---------------------------------------------------------------------------
# Apply delta from LLM analysis
# ---------------------------------------------------------------------------
async def apply_delta(
    db: AsyncSession,
    delta: RelationshipDelta,
    chat_id: int,
    round_id: Optional[str] = None,
) -> CharacterRelationship:
    rel = await get_or_create_relationship(
        db, chat_id, delta.source_character_id, delta.target_character_id,
    )

    # Issues are applied regardless of the metric delta importance — each
    # issue carries its own importance gate (see create_issue).
    issue_results = await apply_issue_deltas(
        db, delta.issues, rel=rel, round_id=round_id,
    )

    if delta.importance < settings.relationship_min_importance:
        logger.debug(
            "Skipping relationship delta for %d->%d: importance %d < %d",
            delta.source_character_id, delta.target_character_id,
            delta.importance, settings.relationship_min_importance,
        )
        if issue_results:
            await db.commit()
        return rel

    old_type = rel.relationship_type
    new_type = delta.relationship_type or old_type

    if new_type != old_type and not validate_transition(old_type, new_type):
        logger.warning(
            "Invalid transition %s -> %s for rel %d->%d; keeping %s",
            old_type, new_type, delta.source_character_id,
            delta.target_character_id, old_type,
        )
        new_type = old_type

    old_values = (
        rel.affection, rel.trust, rel.attraction, rel.resentment, rel.jealousy,
        rel.relationship_type, rel.description,
    )

    # Apply clamped deltas
    rel.affection = clamp_metric(rel.affection + _clamp_delta(delta.delta_affection))
    rel.trust = clamp_metric(rel.trust + _clamp_delta(delta.delta_trust))
    rel.attraction = clamp_metric(rel.attraction + _clamp_delta(delta.delta_attraction))
    rel.resentment = clamp_metric(rel.resentment + _clamp_delta(delta.delta_resentment))
    rel.jealousy = clamp_metric(rel.jealousy + _clamp_delta(delta.delta_jealousy))
    rel.relationship_type = new_type

    if delta.update_description and delta.description:
        rel.description = delta.description

    new_values = (
        rel.affection, rel.trust, rel.attraction, rel.resentment, rel.jealousy,
        rel.relationship_type, rel.description,
    )

    if old_values == new_values:
        logger.debug(
            "No actual change for rel %d->%d; skipping event",
            delta.source_character_id, delta.target_character_id,
        )
        # Values are identical to what is already persisted, so there is
        # nothing to write. Do not rollback (it would expire the ORM object).
        if issue_results:
            await db.commit()
        return rel

    rel.updated_at = datetime.utcnow()
    await db.flush()

    # Create event log
    event = RelationshipEvent(
        relationship_id=rel.id,
        description=delta.description or "",
        reason=delta.reason or "",
        delta_affection=_clamp_delta(delta.delta_affection),
        delta_trust=_clamp_delta(delta.delta_trust),
        delta_attraction=_clamp_delta(delta.delta_attraction),
        delta_resentment=_clamp_delta(delta.delta_resentment),
        delta_jealousy=_clamp_delta(delta.delta_jealousy),
        importance=delta.importance,
        source_round_id=round_id,
    )
    db.add(event)
    await db.commit()
    await db.refresh(rel)
    return rel


def _clamp_delta(value: int) -> int:
    return max(-MAX_DELTA, min(MAX_DELTA, value))


# ---------------------------------------------------------------------------
# Formatting for prompt
# ---------------------------------------------------------------------------
async def get_recent_events(
    db: AsyncSession,
    rel: CharacterRelationship,
    limit: int = 5,
) -> list[RelationshipEvent]:
    stmt = (
        select(RelationshipEvent)
        .where(RelationshipEvent.relationship_id == rel.id)
        .order_by(RelationshipEvent.timestamp.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


def format_relationship_for_prompt(
    rel: CharacterRelationship,
    target_name: str,
    events: list[RelationshipEvent],
    open_issues: Iterable[Any] = (),
) -> str:
    """Format one relationship for the generation prompt.

    Uses the deterministic interpreter instead of raw metrics: the character
    model gets semantic labels, never numbers (docs/relations.md §4-§5).
    Open issues (Sprint 1 п.5) bias the interpretation toward an unresolved
    hook without leaking raw issue text into this block (that is the separate
    ``<open_issue data>`` block, §14).
    """
    interp = interpret(rel, open_issues=open_issues)
    lines = [f"{target_name}: {rel.relationship_type}"]
    text = format_interpretation(interp, target_name)
    if text:
        lines.append(f"  {text}")
    if rel.description:
        lines.append(f"  описание: {rel.description}")
    if events:
        for ev in reversed(events):
            if ev.description:
                lines.append(f"  - {ev.description}")
    return "\n".join(lines)


async def build_relationships_block(
    db: AsyncSession,
    chat_id: int,
    character_id: int,
    character_name: str,
    all_characters: dict[int, str],
    max_events: int = 5,
) -> str:
    rels = await list_relationships_for_character(db, character_id, chat_id=chat_id)
    if not rels:
        return ""

    blocks: list[str] = [f"Отношения {character_name} к другим персонажам:"]
    for rel in rels:
        target_name = all_characters.get(rel.target_character_id, f"ID:{rel.target_character_id}")
        events = await get_recent_events(db, rel, limit=max_events)
        open_issues = await list_open_issues(db, rel)
        blocks.append(format_relationship_for_prompt(rel, target_name, events, open_issues=open_issues))
    return "\n".join(blocks)


async def build_behavior_drivers_block(
    db: AsyncSession,
    chat_id: int,
    character_id: int,
    character_name: str,
    all_characters: dict[int, str],
    max_drivers: int | None = None,
) -> str:
    """Build the top-K behavior drivers block for one character (Sprint 1 п.3-4).

    Aggregates deterministic tendency drivers across all outgoing relationships
    of the character, keeps the most significant ``relationship_drivers_max``,
    and wraps them in ``<behavior_drivers>…</behavior_drivers>``.

    Args:
        db: database session.
        chat_id: chat scope.
        character_id: source character id.
        character_name: source character name (kept for signature symmetry
            with :func:`build_relationships_block`).
        all_characters: {character_id: name} for name resolution.
        max_drivers: cap on returned tendencies; defaults to
            ``settings.relationship_drivers_max``.
    """
    if max_drivers is None:
        max_drivers = settings.relationship_drivers_max
    rels = await list_relationships_for_character(db, character_id, chat_id=chat_id)
    if not rels:
        return ""

    candidates: list[tuple[int, str]] = []
    for rel in rels:
        target_name = all_characters.get(rel.target_character_id, f"ID:{rel.target_character_id}")
        open_issues = await list_open_issues(db, rel)
        interp = interpret(rel, open_issues=open_issues)
        candidates.extend(weighted_behavior_drivers(interp, target_name))

    candidates.sort(key=lambda item: (-item[0], item[1]))
    top = [text for _, text in candidates[:max(0, int(max_drivers))]]
    return _wrap_drivers_block(top)


async def build_epistemic_mask_block(
    db: AsyncSession,
    chat_id: int,
    character_id: int,
    character_name: str,
    all_characters: dict[int, str],
    evidenced_target_ids: Iterable[int] = (),
    max_edges: int | None = None,
) -> str:
    """Build the ``<epistemic_mask>`` block for one character (Sprint 2 item 10).

    A character only *knows* how another treats them when it had direct or
    observed evidence of that other's behavior this round (docs/relations.md
    §10). Incoming edges (source -> this character) with evidence are shown as
    an interpretation WITHOUT any numbers; edges without evidence are explicitly
    marked unknown. Foreign internal metrics are never leaked into the prompt.

    Args:
        db: database session.
        chat_id: chat scope.
        character_id: the viewing character (target of the incoming edges).
        character_name: the viewing character's name.
        all_characters: {character_id: name} for name resolution.
        evidenced_target_ids: ids of characters whose behavior this character
            perceived this round (mode direct/observed, computed in chat_engine).
        max_edges: cap on returned lines; defaults to
            ``settings.relationship_epistemic_max``.
    """
    if not settings.relationship_epistemic_mask_enabled:
        return ""
    if max_edges is None:
        max_edges = settings.relationship_epistemic_max
    evidenced = set(int(i) for i in evidenced_target_ids)

    received = await list_received_relationships(db, character_id)
    if not received:
        return ""

    known_lines: list[str] = []
    unknown_lines: list[str] = []
    for rel in received:
        source_name = all_characters.get(
            rel.source_character_id, f"ID:{rel.source_character_id}"
        )
        if source_name == character_name:
            continue
        if rel.source_character_id in evidenced:
            interp = interpret(rel)
            text = format_interpretation_from_other(interp, source_name)
            known_lines.append(f"Известное тебе отношение {source_name} к тебе: {text}")
        else:
            unknown_lines.append(f"Тебе неизвестно, как {source_name} относится к тебе.")

    lines = known_lines + unknown_lines
    if max_edges and len(lines) > max_edges:
        lines = lines[: max(0, int(max_edges))]
    return _wrap_epistemic_block(lines)


# ---------------------------------------------------------------------------
# Open Issues (docs/relations.md §7, Sprint 1 items 5-6)
# ---------------------------------------------------------------------------
def sanitize_issue_text(text: str, max_len: int | None = None) -> Optional[str]:
    """Clean/validate LLM-produced issue text (data, not instructions, §14).

    - normalizes whitespace;
    - strips control / non-printable characters;
    - rejects text carrying prompt-injection markers (denylist);
    - truncates to ``RELATIONSHIP_ISSUE_TEXT_MAX``.

    Returns the sanitized text or ``None`` when the text must be rejected.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    cleaned = " ".join(text.split())
    cleaned = _CTRL_CHARS_RE.sub("", cleaned)
    if not cleaned:
        return None
    lowered = cleaned.lower()
    for marker in _ISSUE_INSTRUCTION_MARKERS:
        if marker in lowered:
            logger.warning("Rejecting issue text with injection marker %r", marker)
            return None
    if max_len is None:
        max_len = settings.relationship_issue_text_max
    if max_len and len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip()
    return cleaned or None


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


def _jaccard(a: str, b: str) -> float:
    ta = _tokenize(a)
    tb = _tokenize(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


async def _is_near_dup(db: AsyncSession, rel: CharacterRelationship, issue_type: str, text: str) -> bool:
    stmt = select(RelationshipIssue).where(
        RelationshipIssue.relationship_id == rel.id,
        RelationshipIssue.state == "open",
        RelationshipIssue.issue_type == issue_type,
    )
    result = await db.execute(stmt)
    existing = list(result.scalars().all())
    threshold = settings.relationship_issue_near_dup_jaccard
    return any(_jaccard(text, ex.text) >= threshold for ex in existing)


async def create_issue(
    db: AsyncSession,
    rel: CharacterRelationship,
    *,
    issue_type: str,
    text: str,
    importance: int = 5,
    round_id: Optional[str] = None,
) -> Optional[RelationshipIssue]:
    """Create an open issue for a specific relationship edge (§7.2).

    Rejects: unknown issue_type (whitelist), unsanitizable text, importance
    below ``RELATIONSHIP_MIN_IMPORTANCE``, near-duplicate of an existing open
    issue on the same edge with the same type.
    """
    if issue_type not in ISSUE_TYPES:
        logger.warning(
            "Unknown issue_type %r for rel %d->%d; rejecting",
            issue_type, rel.source_character_id, rel.target_character_id,
        )
        return None
    cleaned = sanitize_issue_text(text)
    if cleaned is None:
        logger.warning(
            "Rejected invalid issue text for rel %d->%d",
            rel.source_character_id, rel.target_character_id,
        )
        return None
    if importance < settings.relationship_min_importance:
        logger.debug(
            "Skipping issue for rel %d->%d: importance %d < %d",
            rel.source_character_id, rel.target_character_id,
            importance, settings.relationship_min_importance,
        )
        return None
    if await _is_near_dup(db, rel, issue_type, cleaned):
        logger.debug(
            "Skipping near-duplicate issue (%s) for rel %d->%d",
            issue_type, rel.source_character_id, rel.target_character_id,
        )
        return None

    issue = RelationshipIssue(
        relationship_id=rel.id,
        issue_type=issue_type,
        text=cleaned,
        importance=importance,
        state="open",
        created_round_id=round_id,
        last_mention_round_id=round_id,
    )
    db.add(issue)
    await db.flush()
    return issue


async def resolve_issue(
    db: AsyncSession,
    rel: CharacterRelationship,
    issue_id: int,
    *,
    reason: str = "",
    round_id: Optional[str] = None,
) -> Optional[RelationshipIssue]:
    """Resolve an open issue, only if it belongs to this relationship edge (§7.2).

    An issue belonging to a different pair is rejected — the service never
    guesses or reassigns the relationship by text.
    """
    issue = await db.get(RelationshipIssue, issue_id)
    if issue is None:
        logger.warning(
            "Resolve failed: issue %d not found for rel %d->%d",
            issue_id, rel.source_character_id, rel.target_character_id,
        )
        return None
    if issue.relationship_id != rel.id:
        logger.warning(
            "Resolve rejected: issue %d belongs to rel %d, not %d->%d (%d)",
            issue_id, issue.relationship_id,
            rel.source_character_id, rel.target_character_id, rel.id,
        )
        return None
    if issue.state == "resolved":
        logger.debug("Issue %d already resolved; skipping", issue_id)
        return None
    issue.state = "resolved"
    issue.resolved_round_id = round_id
    issue.resolved_at = datetime.utcnow()
    await db.flush()
    return issue


async def apply_issue_deltas(
    db: AsyncSession,
    issues: Iterable[IssueDelta],
    *,
    rel: Optional[CharacterRelationship] = None,
    source_id: Optional[int] = None,
    target_id: Optional[int] = None,
    chat_id: Optional[int] = None,
    round_id: Optional[str] = None,
) -> list[RelationshipIssue]:
    """Apply create/resolve issue proposals for one relationship edge.

    Pair attribution is mandatory: each issue's source/target must match the
    resolved edge, otherwise the action is rejected (analyzers cannot swap the
    pair). Returns the list of successfully created/resolved issues.
    """
    applied: list[RelationshipIssue] = []
    for issue_delta in issues:
        if issue_delta.source_character_id != (source_id if source_id is not None else rel.source_character_id if rel else None) or \
           issue_delta.target_character_id != (target_id if target_id is not None else rel.target_character_id if rel else None):
            logger.warning(
                "Issue pair mismatch (%d->%d); rejecting",
                issue_delta.source_character_id, issue_delta.target_character_id,
            )
            continue
        if rel is None:
            if source_id is None or target_id is None or chat_id is None:
                logger.warning("Cannot resolve issue pair: missing relationship context")
                continue
            rel = await get_or_create_relationship(db, chat_id, source_id, target_id)
        if issue_delta.action == "create":
            issue = await create_issue(
                db, rel,
                issue_type=issue_delta.issue_type or "",
                text=issue_delta.text,
                importance=issue_delta.importance,
                round_id=round_id,
            )
            if issue is not None:
                applied.append(issue)
        elif issue_delta.action == "resolve":
            if issue_delta.issue_id is None:
                logger.warning("Resolve action without issue_id; rejecting")
                continue
            issue = await resolve_issue(
                db, rel, issue_delta.issue_id,
                reason=issue_delta.reason or "",
                round_id=round_id,
            )
            if issue is not None:
                applied.append(issue)
        else:
            logger.warning("Unknown issue action %r; rejecting", issue_delta.action)
    return applied


async def list_open_issues(
    db: AsyncSession,
    rel: CharacterRelationship,
    limit: int | None = None,
) -> list[RelationshipIssue]:
    """Open issues for one edge, most important/newest first."""
    stmt = (
        select(RelationshipIssue)
        .where(
            RelationshipIssue.relationship_id == rel.id,
            RelationshipIssue.state == "open",
        )
        .order_by(
            RelationshipIssue.importance.desc(),
            RelationshipIssue.created_at.desc(),
            RelationshipIssue.id.desc(),
        )
    )
    if limit is not None:
        stmt = stmt.limit(max(0, int(limit)))
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def list_top_open_issues_for_character(
    db: AsyncSession,
    chat_id: int,
    character_id: int,
    limit: int | None = None,
) -> list[RelationshipIssue]:
    """Top open issues across all outgoing edges of a character.

    Deterministic selection shared by :func:`build_open_issues_block` (the
    generation-context mentions) and the salience ticker (§7.4): most important
    first, then newest.
    """
    rels = await list_relationships_for_character(db, character_id, chat_id=chat_id)
    if not rels:
        return []

    all_open: list[RelationshipIssue] = []
    for rel in rels:
        all_open.extend(await list_open_issues(db, rel))

    all_open.sort(
        key=lambda issue: (issue.importance, issue.created_at, issue.id),
        reverse=True,
    )
    if limit is not None:
        all_open = all_open[: max(0, int(limit))]
    return all_open


async def build_open_issues_block(
    db: AsyncSession,
    chat_id: int,
    character_id: int,
    character_name: str,
    all_characters: dict[int, str],
    max_issues: int | None = None,
) -> str:
    """Build the ``<open_issue data>`` block for one character (Sprint 1 п.5-6).

    Aggregates open issues across all outgoing edges of the character, keeps
    the most important ``relationship_max_issues_in_prompt``, and wraps them
    in a data-only block (never an instruction; §14).
    """
    if not settings.relationship_issues_enabled:
        return ""
    if max_issues is None:
        max_issues = settings.relationship_max_issues_in_prompt
    selected = await list_top_open_issues_for_character(
        db, chat_id, character_id, limit=max_issues
    )
    return _wrap_open_issues_block(selected)


# ---------------------------------------------------------------------------
# Weighted deterministic proactive boost (docs/relations.md §7.4, Sprint 1 п.7)
# ---------------------------------------------------------------------------
def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def proactive_boost_from_issues(
    issues: Iterable[Any],
) -> float:
    """Deterministic weighted proactive boost from open issues (§7.4).

    Forbidden: ``const * len(open_issues)`` — three minor hooks must not weigh
    like one major conflict. Each issue contributes ``importance * salience``:

        salience_i = clamp01(1 - rounds_since_last_mention_i / DECAY_ROUNDS)
        w_i        = (importance_i / 10) * salience_i
        boost      = clamp(COEFF * sum(w_i), 0, BOOST_CAP)

    Deterministic: identical input always yields identical output.
    """
    decay_rounds = max(1, int(settings.issue_salience_decay_rounds))
    total = 0.0
    for issue in issues:
        importance = max(0, min(10, int(getattr(issue, "importance", 5) or 5)))
        rounds_since = max(0, int(getattr(issue, "rounds_since_last_mention", 0) or 0))
        salience = _clamp01(1.0 - rounds_since / decay_rounds)
        total += (importance / 10.0) * salience
    boost = settings.issue_proactive_coeff * total
    return _clamp(boost, 0.0, settings.issue_proactive_boost_cap)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


async def compute_proactive_boost(
    db: AsyncSession,
    chat_id: int,
    character_id: int,
) -> float:
    """Weighted proactive boost for one character (all its open issues, §7.4).

    Reads the deterministic ``rounds_since_last_mention`` counters as of the
    current round's start (before any tick), so the boost reflects how long
    each hook stayed unmentioned.
    """
    if not settings.relationship_issues_enabled:
        return 0.0
    rels = await list_relationships_for_character(db, character_id, chat_id=chat_id)
    issues: list[RelationshipIssue] = []
    for rel in rels:
        issues.extend(await list_open_issues(db, rel))
    return proactive_boost_from_issues(issues)


async def touch_issue(
    db: AsyncSession,
    issue: RelationshipIssue,
    round_id: Optional[str] = None,
) -> RelationshipIssue:
    """Reset an issue's salience counter (it was mentioned this round, §7.4)."""
    issue.rounds_since_last_mention = 0
    issue.last_mention_round_id = round_id
    await db.flush()
    return issue


async def tick_open_issues(
    db: AsyncSession,
    chat_id: int,
    round_id: Optional[str] = None,
    mentioned_ids: Iterable[int] = (),
) -> None:
    """Advance the deterministic salience counters once per round (§7.4).

    Every open issue in the chat that was NOT mentioned this round (absent
    from context/analysis) has its counter incremented; mentioned ones are
    reset. Issues created in this very round carry ``last_mention_round_id ==
    round_id`` and are left at 0 until the next round.
    """
    mentioned = set(int(i) for i in mentioned_ids)
    stmt = (
        select(RelationshipIssue)
        .join(CharacterRelationship, CharacterRelationship.id == RelationshipIssue.relationship_id)
        .where(
            RelationshipIssue.state == "open",
            CharacterRelationship.chat_id == chat_id,
        )
    )
    result = await db.execute(stmt)
    open_issues = list(result.scalars().all())
    for issue in open_issues:
        if issue.id in mentioned:
            await touch_issue(db, issue, round_id=round_id)
        elif issue.last_mention_round_id != round_id:
            issue.rounds_since_last_mention = (
                int(issue.rounds_since_last_mention or 0) + 1
            )
            await db.flush()
