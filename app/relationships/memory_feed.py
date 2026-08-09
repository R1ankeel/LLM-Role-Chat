"""Интеграция памяти для значимых событий отношений (Milestone 6B, §4.4).

Вынесено из ``app/relationship_service.py`` без изменения поведения (тела
функций перенесены 1:1). Создание Memory/якорей по ``RelationshipEvent``
и по разрешённым issues; используется ``deltas.py`` и ``issues.py``.
"""

import json
import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from .. import crud
from ..config import settings
from ..memory.create import create_memory as memory_create_memory
from ..models import (
    CharacterRelationship,
    Memory,
    RelationshipEvent,
    RelationshipIssue,
)
from ..relationship_interpreter import format_interpretation, interpret
from ..schemas import MemoryCreate

logger = logging.getLogger(__name__)


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
    from ..relationship_interpreter import interpret, format_interpretation

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

    # Память из события — через явный интерфейс `memory/` (Sprint 1, §7.1).
    memory = MemoryCreate(
        chat_id=chat_id,
        character_id=rel.source_character_id,
        content=content,
        importance=min(1.0, max(0.1, event.importance / 10.0)),
        category="отношения",
        # Sprint 2 (§7): социальный тип памяти (canary-флаг).
        memory_type="social" if settings.memory_types_enabled else None,
    )

    created = await memory_create_memory(db, memory, source_message_ids=source_msg_ids)

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

    # Память из события — через явный интерфейс `memory/` (Sprint 1, §7.1).
    memory = MemoryCreate(
        chat_id=chat_id,
        character_id=rel.source_character_id,
        content=content,
        importance=min(1.0, max(0.1, issue.importance / 10.0)),
        category="отношения",
        # Sprint 2 (§7): социальный тип памяти (canary-флаг).
        memory_type="social" if settings.memory_types_enabled else None,
    )

    created = await memory_create_memory(db, memory, source_message_ids=source_msg_ids)
    return created
