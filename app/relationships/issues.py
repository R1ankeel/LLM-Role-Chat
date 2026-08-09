"""Open Issues и проактивный буст (Milestone 6B, decomposition.md §4.4).

Вынесено из ``app/relationship_service.py`` без изменения поведения (тела
функций перенесены 1:1). Создание/разрешение issues, выборка для UI и
контекста, детерминированный счётчик salience (``touch_issue`` /
``tick_open_issues``).
"""

import json
import logging
import re
from datetime import datetime
from typing import Any, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..config import settings
from ..models import CharacterRelationship, Message, RelationshipIssue
from ..prompt_builder import build_open_issues_block as _wrap_open_issues_block
from ..schemas import ISSUE_TYPES, IssueDelta
from .crud import get_or_create_relationship, list_relationships_for_character
from .memory_feed import _maybe_create_memory_from_resolved_issue

logger = logging.getLogger(__name__)


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
        from ..models import Message

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
