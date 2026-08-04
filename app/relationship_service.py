"""Service layer for character relationship CRUD and delta application."""

import json
import logging
import re
from datetime import datetime
from typing import Any, Iterable, Optional

from sqlalchemy import delete, select
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
    Memory,
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


def _log_relationship_event(event: RelationshipEvent) -> None:
    """Emit one structured JSON log line per created event (Stage 4, Sprint 4).

    The ``extra`` payload is rendered top-level by the root ``JSONFormatter``
    configured in ``main.py`` — usable for analytics/debugging without parsing
    free-text messages.
    """
    try:
        source_ids = json.loads(event.source_message_ids or "[]")
    except (json.JSONDecodeError, TypeError):
        source_ids = []
    logger.info(
        "relationship_event",
        extra={
            "relationship_id": event.relationship_id,
            "event_kind": event.kind,
            "delta_affection": event.delta_affection,
            "delta_trust": event.delta_trust,
            "delta_attraction": event.delta_attraction,
            "delta_resentment": event.delta_resentment,
            "delta_jealousy": event.delta_jealousy,
            "affection_after": event.affection_after,
            "trust_after": event.trust_after,
            "attraction_after": event.attraction_after,
            "resentment_after": event.resentment_after,
            "jealousy_after": event.jealousy_after,
            "importance": event.importance,
            "round_id": event.round_id,
            "source_message_ids": source_ids,
        },
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
async def get_or_create_relationship(
    db: AsyncSession,
    chat_id: int,
    source_id: int,
    target_id: int,
) -> CharacterRelationship:
    """Get or create the directed edge source -> target (docs/relations.md §10).

    Reciprocity invariant: the edge is directional and NO automatic mirroring
    happens. Only ``source -> target`` is created/returned here; the reverse
    edge ``target -> source`` is a separate row that this function neither
    creates nor touches. A self-loop (source == target) is rejected.
    """
    if source_id == target_id:
        raise ValueError(
            f"Cannot create relationship for self-loop ({source_id} -> {target_id})"
        )
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


async def list_relationships_for_chat(
    db: AsyncSession,
    chat_id: int,
) -> list[CharacterRelationship]:
    """All tracked edges of a chat (NPC -> NPC / NPC -> player), with endpoints.

    Used by the relationship graph UI (Sprint 4 п.24). Player -> NPC edges are
    never tracked in the DB, so they cannot appear here.
    """
    stmt = (
        select(CharacterRelationship)
        .where(CharacterRelationship.chat_id == chat_id)
        .options(
            selectinload(CharacterRelationship.source_character),
            selectinload(CharacterRelationship.target_character),
        )
    )
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

    # Create manual event with snapshot (docs/relations.md §17)
    event = RelationshipEvent(
        relationship_id=rel.id,
        kind="manual",
        description="Ручное обновление через API",
        reason="",
        delta_affection=0,
        delta_trust=0,
        delta_attraction=0,
        delta_resentment=0,
        delta_jealousy=0,
        affection_after=rel.affection,
        trust_after=rel.trust,
        attraction_after=rel.attraction,
        resentment_after=rel.resentment,
        jealousy_after=rel.jealousy,
        importance=1,
        source_message_ids="[]",
        round_id=None,
        source_round_id=None,
    )
    db.add(event)
    await db.flush()
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


def validate_relationship_type_update(
    current_type: str,
    new_type: str,
    strict: bool = True,
) -> tuple[bool, str]:
    """Validate a relationship type update.
    
    Returns (is_valid, error_message). If valid, error_message is empty.
    Checks:
    1. new_type is in valid types whitelist
    2. transition from current_type to new_type is allowed

    ``strict`` controls the transition gate:
    - ``strict=True`` (default) — only realistic progressions are allowed
      (used by the automatic LLM analysis path: ``apply_delta``);
    - ``strict=False`` — any valid type is allowed, including family types
      (``семья``/``родитель``/``брат_сестра``), used by the manual editing
      endpoint. The whitelist check always applies.
    """
    if new_type not in VALID_TYPES:
        return False, (
            f"Invalid relationship_type: '{new_type}'. "
            f"Must be one of: {', '.join(sorted(VALID_TYPES))}"
        )
    if strict and not validate_transition(current_type, new_type):
        allowed = TRANSITIONS.get(current_type, set())
        return False, (
            f"Invalid transition from '{current_type}' to '{new_type}'. "
            f"Allowed transitions: {', '.join(sorted(allowed)) if allowed else 'none'}"
        )
    return True, ""


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
    """Apply a delta to the single directed edge source -> target.

    Reciprocity invariant: only ``delta.source_character_id ->
    delta.target_character_id`` is modified. The reverse edge is never touched,
    mirroring is forbidden (docs/relations.md §10, §22) — unrequited feelings
    (A->B affection 90, B->A affection 20) are valid and preserved.
    """
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
        # No commit here: the batch caller owns the single flush+commit.
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
        # No commit here: the batch caller owns the single flush+commit.
        return rel

    rel.updated_at = datetime.utcnow()

    # Create event log with kind + snapshot after (docs/relations.md §11, §17).
    # No flush/refresh: the batch caller applies one flush+commit per round and
    # ``rel`` already holds the new values in-memory. ``event`` gets its DB id
    # at that final flush.
    event = RelationshipEvent(
        relationship_id=rel.id,
        kind="llm",
        description=delta.description or "",
        reason=delta.reason or "",
        delta_affection=_clamp_delta(delta.delta_affection),
        delta_trust=_clamp_delta(delta.delta_trust),
        delta_attraction=_clamp_delta(delta.delta_attraction),
        delta_resentment=_clamp_delta(delta.delta_resentment),
        delta_jealousy=_clamp_delta(delta.delta_jealousy),
        affection_after=rel.affection,
        trust_after=rel.trust,
        attraction_after=rel.attraction,
        resentment_after=rel.resentment,
        jealousy_after=rel.jealousy,
        importance=delta.importance,
        source_message_ids=json.dumps(delta.source_message_ids or []),
        round_id=round_id,
        source_round_id=round_id,
    )
    db.add(event)
    _log_relationship_event(event)

    # Create memory for significant relationship events (Sprint 3 item 19)
    try:
        await _maybe_create_memory_from_event(db, rel, event, chat_id)
    except Exception as exc:
        logger.warning(
            "Failed to create memory from relationship event: %s", exc
        )

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


async def _validate_source_message_ids(
    proposed_ids: list[int],
    round_id: Optional[str],
    chat_id: int,
    db: AsyncSession,
) -> list[int]:
    """Validate proposed source_message_ids against messages in the round.

    If proposed_ids are empty or invalid, fall back to all user/character
    messages in this round. Returns validated list of message IDs.
    """
    if not round_id:
        return proposed_ids

    # Parse round_id: r{chat_id}-m{user_message_id}
    # Get all messages in this round (user message + subsequent character replies)
    try:
        # round_id format: r{chat_id}-m{user_message_id}
        if not round_id.startswith("r") or "-m" not in round_id:
            return proposed_ids
        parts = round_id.split("-m")
        if len(parts) != 2:
            return proposed_ids
        round_chat_id = int(parts[0][1:])  # skip 'r'
        user_message_id = int(parts[1])

        if round_chat_id != chat_id:
            return proposed_ids

        # Get all messages in this chat from the user message onwards in this round
        # We'll consider messages with timestamp >= user message timestamp
        # For simplicity, get recent messages in this chat
        from sqlalchemy import select
        from .models import Message

        # Get user message timestamp
        user_msg = await db.get(Message, user_message_id)
        if user_msg is None:
            return proposed_ids

        # Get all messages in this chat after the user message (same round)
        stmt = select(Message.id).where(
            Message.chat_id == chat_id,
            Message.timestamp >= user_msg.timestamp,
        ).order_by(Message.timestamp)
        result = await db.execute(stmt)
        valid_ids = [row[0] for row in result.all()]

        if not proposed_ids:
            return valid_ids

        # Return intersection of proposed and valid
        valid_set = set(valid_ids)
        return [mid for mid in proposed_ids if mid in valid_set]

    except Exception:
        # On any error, return proposed (will be empty list if none)
        return proposed_ids


async def create_issue(
    db: AsyncSession,
    rel: CharacterRelationship,
    *,
    issue_type: str,
    text: str,
    importance: int = 5,
    round_id: Optional[str] = None,
    source_message_ids: list[int] | None = None,
    chat_id: Optional[int] = None,
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

    # Validate and store source_message_ids (Sprint 3 item 18)
    validated_ids = []
    if source_message_ids and chat_id and round_id:
        validated_ids = await _validate_source_message_ids(source_message_ids, round_id, chat_id, db)

    issue = RelationshipIssue(
        relationship_id=rel.id,
        issue_type=issue_type,
        text=cleaned,
        importance=importance,
        state="open",
        created_round_id=round_id,
        last_mention_round_id=round_id,
        source_message_ids=json.dumps(validated_ids),
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
    source_message_ids: list[int] | None = None,
    chat_id: Optional[int] = None,
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

    # Update source_message_ids for resolution (Sprint 3 item 18)
    if source_message_ids and chat_id and round_id:
        validated_ids = await _validate_source_message_ids(source_message_ids, round_id, chat_id, db)
        if validated_ids:
            issue.source_message_ids = json.dumps(validated_ids)

    issue.state = "resolved"
    issue.resolved_round_id = round_id
    issue.resolved_at = datetime.utcnow()
    await db.flush()

    # Create memory for resolved issue (Sprint 3 item 19)
    if chat_id:
        try:
            await _maybe_create_memory_from_resolved_issue(db, rel, issue, chat_id)
        except Exception as exc:
            logger.warning(
                "Failed to create memory from resolved issue: %s", exc
            )

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
                source_message_ids=issue_delta.source_message_ids,
                chat_id=chat_id,
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
                source_message_ids=issue_delta.source_message_ids,
                chat_id=chat_id,
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


async def list_issues_for_chat(
    db: AsyncSession,
    chat_id: int,
    state: str = "open",
) -> list[RelationshipIssue]:
    """All issues of a chat, filtered by ``state`` (open/resolved/all).

    Used by the open-issues UI (Sprint 4 п.26). Ordered by importance, then
    newest first — same deterministic ordering as :func:`list_open_issues`.
    """
    stmt = (
        select(RelationshipIssue)
        .join(CharacterRelationship, CharacterRelationship.id == RelationshipIssue.relationship_id)
        .where(CharacterRelationship.chat_id == chat_id)
    )
    if state != "all":
        stmt = stmt.where(RelationshipIssue.state == state)
    stmt = stmt.order_by(
        RelationshipIssue.importance.desc(),
        RelationshipIssue.created_at.desc(),
        RelationshipIssue.id.desc(),
    ).options(
        selectinload(RelationshipIssue.relationship).selectinload(
            CharacterRelationship.source_character
        ),
        selectinload(RelationshipIssue.relationship).selectinload(
            CharacterRelationship.target_character
        ),
    )
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


# ---------------------------------------------------------------------------
# Decay (Sprint 3 item 16, docs/relations.md §18)
# ---------------------------------------------------------------------------
async def apply_decay(
    db: AsyncSession,
    chat_id: int,
    round_id: str,
) -> list[RelationshipEvent]:
    """Apply per-round decay to jealousy and resentment for all relationships in chat.

    - jealousy: -RELATIONSHIP_DECAY_JEALOUSY_PER_ROUND per round (default 3)
    - resentment: -RELATIONSHIP_DECAY_RESENTMENT_PER_ROUND per round (default 1)
    - affection/trust/attraction: no decay

    Creates RelationshipEvent(kind="decay") ONLY when value crosses a multiple of 10:
    20→19 (event), 10→9 (event), 0→0 (no event if already 0).

    Returns list of created decay events.
    """
    from sqlalchemy import select

    jealousy_decay = settings.relationship_decay_jealousy_per_round
    resentment_decay = settings.relationship_decay_resentment_per_round

    stmt = select(CharacterRelationship).where(CharacterRelationship.chat_id == chat_id)
    result = await db.execute(stmt)
    relationships = list(result.scalars().all())

    created_events: list[RelationshipEvent] = []

    for rel in relationships:
        old_jealousy = rel.jealousy
        old_resentment = rel.resentment

        new_jealousy = max(0, old_jealousy - jealousy_decay)
        new_resentment = max(0, old_resentment - resentment_decay)

        # Check if either crossed a multiple-of-10 boundary
        jealousy_crossed = (old_jealousy // 10) != (new_jealousy // 10) and old_jealousy > 0
        resentment_crossed = (old_resentment // 10) != (new_resentment // 10) and old_resentment > 0

        if not jealousy_crossed and not resentment_crossed:
            continue

        # Apply changes
        rel.jealousy = new_jealousy
        rel.resentment = new_resentment
        rel.updated_at = datetime.utcnow()

        # Create decay event with snapshot
        event = RelationshipEvent(
            relationship_id=rel.id,
            kind="decay",
            description="Естественное затухание эмоций",
            reason="",
            delta_affection=0,
            delta_trust=0,
            delta_attraction=0,
            delta_resentment=new_resentment - old_resentment,
            delta_jealousy=new_jealousy - old_jealousy,
            affection_after=rel.affection,
            trust_after=rel.trust,
            attraction_after=rel.attraction,
            resentment_after=rel.resentment,
            jealousy_after=rel.jealousy,
            importance=1,
            source_message_ids="[]",
            round_id=round_id,
            source_round_id=round_id,
        )
        db.add(event)
        created_events.append(event)

    return created_events


# ---------------------------------------------------------------------------
# Event pruning / archiving (Sprint 4 item 3, docs/relations.md §20)
# ---------------------------------------------------------------------------
async def prune_relationship_events(
    db: AsyncSession,
    relationship_id: int,
    max_events: int | None = None,
) -> Optional[RelationshipEvent]:
    """Fold old events of one relationship into a single archive entry.

    Keeps the newest ``max_events`` (``RELATIONSHIP_EVENTS_MAX_PER_PAIR``,
    default 100) raw events and replaces every older event with ONE aggregate
    ``kind="archive"`` row. The archive row:

    - carries ``delta_* = 0`` so it never changes the live relationship state;
    - snapshots the *current* ``*_after`` values of the edge;
    - aggregates counts per original kind (llm / decay / manual);
    - stores the folded period (``from_ts``–``to_ts``) in the description;
    - ``importance = 0`` so it never shows in trajectory/prompt blocks.

    Called from the batch commit in ``chat_engine`` and after manual field
    updates in the API — always inside the caller's transaction.
    """
    if max_events is None:
        max_events = settings.relationship_events_max_per_pair
    max_events = max(1, int(max_events))

    stmt = (
        select(RelationshipEvent)
        .where(RelationshipEvent.relationship_id == relationship_id)
        .order_by(RelationshipEvent.timestamp, RelationshipEvent.id)
    )
    result = await db.execute(stmt)
    events = list(result.scalars().all())

    if len(events) <= max_events:
        return None

    archive_prefix = events[: len(events) - max_events]
    archive_ids = [e.id for e in archive_prefix]

    llm_count = sum(1 for e in archive_prefix if e.kind == "llm")
    decay_count = sum(1 for e in archive_prefix if e.kind == "decay")
    manual_count = sum(1 for e in archive_prefix if e.kind == "manual")

    from_ts = archive_prefix[0].timestamp
    to_ts = archive_prefix[-1].timestamp

    rel = await db.get(CharacterRelationship, relationship_id)
    if rel is None:
        return None

    description = (
        f"Архив {len(archive_prefix)} событий "
        f"({from_ts:%Y-%m-%d %H:%M}–{to_ts:%Y-%m-%d %H:%M}): "
        f"llm={llm_count}, decay={decay_count}, manual={manual_count}"
    )

    if archive_ids:
        await db.execute(
            delete(RelationshipEvent).where(RelationshipEvent.id.in_(archive_ids))
        )

    archive_event = RelationshipEvent(
        relationship_id=relationship_id,
        kind="archive",
        description=description,
        reason="",
        delta_affection=0,
        delta_trust=0,
        delta_attraction=0,
        delta_resentment=0,
        delta_jealousy=0,
        affection_after=rel.affection,
        trust_after=rel.trust,
        attraction_after=rel.attraction,
        resentment_after=rel.resentment,
        jealousy_after=rel.jealousy,
        importance=0,
        source_message_ids="[]",
        round_id=archive_prefix[-1].round_id,
        source_round_id=archive_prefix[-1].round_id,
    )
    db.add(archive_event)
    return archive_event


# ---------------------------------------------------------------------------
# Memory Integration (Sprint 3 item 19, docs/relations.md §19)
# ---------------------------------------------------------------------------
async def _maybe_create_memory_from_event(
    db: AsyncSession,
    rel: CharacterRelationship,
    event: RelationshipEvent,
    chat_id: int,
) -> Optional["Memory"]:
    """Create a Memory for significant relationship events (Sprint 3 item 19).

    Criteria (configurable):
    - event.kind == "llm" (not decay/manual)
    - ANY metric |delta| >= RELATIONSHIP_MEMORY_DELTA_THRESHOLD (default 10)
    - OR relationship_type changed (detected via event.reason mentioning type change)

    Memory content: natural language summary using interpreter (no raw numbers).
    Category: "отношения"
    source_message_ids: from event.source_message_ids
    importance: derived from event.importance (scaled 0.1..1.0)
    """
    if not settings.relationship_memory_enabled:
        return None

    # Only for LLM events, not decay/manual
    if event.kind != "llm":
        return None

    # Check significance: any delta >= threshold OR type change
    max_delta = max(
        abs(event.delta_affection),
        abs(event.delta_trust),
        abs(event.delta_attraction),
        abs(event.delta_resentment),
        abs(event.delta_jealousy),
    )
    threshold = settings.relationship_memory_delta_threshold

    type_changed = "тип" in (event.reason or "").lower() and (
        "изменил" in (event.reason or "").lower()
        or "стал" in (event.reason or "").lower()
        or "стало" in (event.reason or "").lower()
    )

    if max_delta < threshold and not type_changed:
        return None

    # Generate memory content using interpreter (no raw numbers)
    from .relationship_interpreter import interpret, format_interpretation

    interp = interpret(rel)
    target_name = rel.target_character.name if rel.target_character else f"ID:{rel.target_character_id}"
    source_name = rel.source_character.name if rel.source_character else f"ID:{rel.source_character_id}"

    # Build a descriptive summary
    changes = []
    if event.delta_affection != 0:
        direction = "улучшилась" if event.delta_affection > 0 else "ухудшилась"
        changes.append(f"привязанность {direction}")
    if event.delta_trust != 0:
        direction = "выросло" if event.delta_trust > 0 else "упало"
        changes.append(f"доверие {direction}")
    if event.delta_attraction != 0:
        direction = "усилилось" if event.delta_attraction > 0 else "ослабло"
        changes.append(f"влечение {direction}")
    if event.delta_resentment != 0:
        direction = "выросла" if event.delta_resentment > 0 else "уменьшилась"
        changes.append(f"обида {direction}")
    if event.delta_jealousy != 0:
        direction = "выросла" if event.delta_jealousy > 0 else "уменьшилась"
        changes.append(f"ревность {direction}")

    if type_changed:
        changes.append(f"тип отношений стал «{event.relationship_type or rel.relationship_type}»")

    if not changes:
        return None

    interp_text = format_interpretation(interp, target_name)
    interp_part = f" ({interp_text})" if interp_text else ""

    content = (
        f"Отношения {source_name} к {target_name}: {', '.join(changes)}."
        f"{interp_part} Причина: {event.reason or event.description or 'неизвестно'}"
    )

    # Parse source_message_ids from event
    import json
    try:
        source_msg_ids = json.loads(event.source_message_ids or "[]")
    except Exception:
        source_msg_ids = []

    # Create memory via crud
    from . import crud
    from .schemas import MemoryCreate

    memory = MemoryCreate(
        chat_id=chat_id,
        character_id=rel.source_character_id,
        content=content,
        importance=min(1.0, max(0.1, event.importance / 10.0)),
        category="отношения",
        # Sprint 2 (§7): социальный тип памяти (canary-флаг).
        memory_type="social" if settings.memory_types_enabled else None,
    )

    created = await crud.create_memory(db, memory, source_message_ids=source_msg_ids)

    # Sprint 2 (§7/§13): эмоциональный якорь для значимого события отношения.
    # Якорь пишется движком (не Sensors); гейтится ANCHORS_ENABLED.
    if created is not None and settings.anchors_enabled:
        try:
            await crud.create_memory_anchor(
                db,
                relationship_id=rel.id,
                event_id=event.event_id,
                emotion=_anchor_emotion_from_deltas(event),
                valence=_anchor_valence_from_deltas(event),
                intensity=min(1.0, abs(max_delta) / 100.0),
                importance=min(1.0, event.importance / 10.0),
            )
        except Exception:
            logger.exception(
                "[rel_id=%d] Anchor write failed for event %d",
                rel.id,
                event.id,
            )
    return created


def _anchor_emotion_from_deltas(event) -> str:
    """Краткая эмоция якоря по знаку ведущего сдвига метрик (§7)."""
    if event.delta_affection != 0:
        return "тепло" if event.delta_affection > 0 else "холод"
    if event.delta_trust != 0:
        return "доверие" if event.delta_trust > 0 else "недоверие"
    if event.delta_attraction != 0:
        return "влечение" if event.delta_attraction > 0 else "отчуждение"
    if event.delta_resentment != 0:
        return "обида" if event.delta_resentment > 0 else "примирение"
    if event.delta_jealousy != 0:
        return "ревность" if event.delta_jealousy > 0 else "спокойствие"
    return "нейтрально"


def _anchor_valence_from_deltas(event) -> float:
    """Валентность −1..+1 из знаков сдвигов (§7): положительные сдвиги > 0."""
    signs = 0.0
    counts = 0
    for delta in (
        event.delta_affection,
        event.delta_trust,
        event.delta_attraction,
        event.delta_resentment,
        event.delta_jealousy,
    ):
        if delta:
            signs += 1.0 if delta > 0 else -1.0
            counts += 1
    if counts == 0:
        return 0.0
    return round(signs / counts, 3)


async def _maybe_create_memory_from_resolved_issue(
    db: AsyncSession,
    rel: CharacterRelationship,
    issue: "RelationshipIssue",
    chat_id: int,
) -> Optional["Memory"]:
    """Create a Memory when an issue is resolved (Sprint 3 item 19)."""
    if not settings.relationship_memory_enabled:
        return None

    target_name = rel.target_character.name if rel.target_character else f"ID:{rel.target_character_id}"
    source_name = rel.source_character.name if rel.source_character else f"ID:{rel.source_character_id}"

    content = (
        f"Разрешён открытый вопрос в отношениях {source_name} к {target_name}: "
        f"{issue.issue_type} — {issue.text}. "
        f"Причина: {issue.resolved_at and 'неизвестно' or ''}"
    )

    # Parse source_message_ids from issue
    import json
    try:
        source_msg_ids = json.loads(issue.source_message_ids or "[]")
    except Exception:
        source_msg_ids = []

    from . import crud
    from .schemas import MemoryCreate

    memory = MemoryCreate(
        chat_id=chat_id,
        character_id=rel.source_character_id,
        content=content,
        importance=min(1.0, max(0.1, issue.importance / 10.0)),
        category="отношения",
        # Sprint 2 (§7): социальный тип памяти (canary-флаг).
        memory_type="social" if settings.memory_types_enabled else None,
    )

    created = await crud.create_memory(db, memory, source_message_ids=source_msg_ids)
    return created


# ---------------------------------------------------------------------------
# Trajectory (docs/relations.md §11, §17)
# ---------------------------------------------------------------------------
async def get_trajectory_events(
    db: AsyncSession,
    relationship_id: int,
    window: int = 4,
) -> list[RelationshipEvent]:
    """Get LLM events for trajectory (kind='llm' only, reverse chronological)."""
    stmt = (
        select(RelationshipEvent)
        .where(
            RelationshipEvent.relationship_id == relationship_id,
            RelationshipEvent.kind == "llm",
        )
        .order_by(RelationshipEvent.id.desc())
        .limit(window)
    )
    result = await db.execute(stmt)
    return list(reversed(result.scalars().all()))


def build_trajectory_block(
    events: list[RelationshipEvent],
    source_name: str,
    target_name: str,
) -> str:
    """Format trajectory as a compact table for batch prompt (§11).

    Columns: affection, trust, attraction, resentment, jealousy
    Shows *_after values from each LLM event, oldest first.
    """
    if not events:
        return ""

    lines = [f"Последние {len(events)} раунда ({source_name} → {target_name}):"]
    metrics = [
        ("привязанность", "affection_after"),
        ("доверие", "trust_after"),
        ("влечение", "attraction_after"),
        ("обида", "resentment_after"),
        ("ревность", "jealousy_after"),
    ]
    for metric_name, attr in metrics:
        values = [str(getattr(e, attr, 0)) for e in events]
        lines.append(f"  {metric_name:<12}: {' → '.join(values)}")
    return "\n".join(lines)
