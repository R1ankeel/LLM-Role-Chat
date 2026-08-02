"""Token-aware context builder.

Collects the most relevant per-character context (recent dialogue, retrieved
historical events, summary, memories, scene state) and fits it into a token
budget instead of mechanically trimming history to a fixed message count.

No LLM calls happen here: only DB reads, presence/witness filtering and BM25
retrieval are used. The ContextBuilder does not modify any existing system.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from . import crud
from . import memory_service
from . import schemas
from .config import settings
from .context_budget import build_budget
from .prompt_builder import (
    build_anti_mimicry_block,
    build_character_summary_block,
    build_isolated_block,
    build_memories_block,
    build_negative_prompting_block,
    build_personality_block,
    build_personality_consistency_block,
    build_recent_dialogue_block,
    build_reinforcement_block,
    build_scene_advancement_block,
    build_scene_block,
    build_system_prompt,
    build_vocabulary_block,
    merge_char_locations,
)
from .repetition_detector import build_repetition_feedback_block
from .role_isolation import (
    build_generation_cue,
    build_generation_cue_for_chat,
    build_isolated_generation_cue,
)
from .token_counter import get_token_counter
from .witness_model import Presence, format_line_for_presence, resolve_presence

logger = logging.getLogger(__name__)

_PRESENCE_QUERY_CHUNK = 500
_MAX_EXCLUDED_IDS = 500

# Presences that may surface in retrieved (older) history. Soft "mentioned"
# snippets are excluded so retrieval respects the knowledge/perception boundary.
_RETRIEVED_PRESENCES = frozenset({"present", "told"})

_MAX_QUERY_CHARS = 600


async def _load_presence_map(
    db: AsyncSession,
    message_ids: list[int],
    character_id: int,
) -> dict[int, str]:
    """Load presence rows in chunks (avoids SQLite variable limits)."""
    if not message_ids:
        return {}
    presence_map: dict[int, str] = {}
    for start in range(0, len(message_ids), _PRESENCE_QUERY_CHUNK):
        chunk = message_ids[start : start + _PRESENCE_QUERY_CHUNK]
        presence_map.update(await crud.get_presence_map(db, chunk, character_id))
    return presence_map


class ContextBuilder:
    """Assembles one character's context within a token budget."""

    def __init__(self, *, token_counter=None):
        self._token_counter = token_counter or get_token_counter()

    async def build(
        self,
        *,
        db: AsyncSession,
        chat_id: int,
        character,
        user_message: str,
        general_prompt: str,
        messages_window: list,
        round_messages: list,
        character_names: dict[int, str],
        character_locations: dict[int, str],
        character_appearances: dict[str, str] | None = None,
        summary: str | None = None,
        summary_through_message_id: int | None = None,
        memories: list | None = None,
        scene_state: Any = None,
        present_character_names: list[str] | None = None,
        relationships_block: str = "",
        locations: str = "[]",
        stagnation_rounds: int = 0,
        viewer_location: str | None = None,
        prior_replies: list[tuple[str, str]] | None = None,
        is_isolated: bool = False,
        max_tokens: int | None = None,
    ) -> schemas.BuiltContext:
        counter = self._token_counter
        budget = build_budget(max_tokens)
        dropped: list[schemas.DroppedItem] = []
        diagnostics = schemas.ContextDiagnostics()

        char_id = int(character.id)
        char_name = character.name
        viewer_location = viewer_location or getattr(character, "location", "") or ""
        prior_replies = prior_replies or []
        memories = list(memories or [])

        # ---- 1. candidate messages + witness lines --------------------
        candidates = list(messages_window) + list(round_messages)
        message_ids = [
            m.id for m in candidates if getattr(m, "id", None) is not None
        ]
        presence_map = await _load_presence_map(db, message_ids, char_id)

        filter_enabled = bool(settings.enable_witness_filter)
        lines: list[dict[str, Any]] = []
        for message in candidates:
            mid = getattr(message, "id", None)
            if filter_enabled:
                presence = resolve_presence(
                    message,
                    char_id,
                    character_names,
                    presence_map,
                    viewer_location=viewer_location,
                    character_locations=character_locations,
                )
            else:
                presence = "present"
            line = format_line_for_presence(message, presence, character_names)
            if not line:
                continue
            lines.append(
                {
                    "message_id": mid,
                    "line": line,
                    "presence": presence,
                    "char_id": getattr(message, "character_id", None),
                }
            )

        # ---- 2. summary frontier split --------------------------------
        frontier = int(summary_through_message_id or 0)
        recent_lines = [
            l
            for l in lines
            if l["message_id"] is None or int(l["message_id"]) > frontier
        ]
        older_lines = [
            l for l in lines if l["message_id"] is not None and int(l["message_id"]) <= frontier
        ]

        # ---- 3. recent dialogue (P1, newest-first, per-char reply cap) --
        recent_selected, recent_tokens = self._assemble_recent(
            recent_lines,
            budget.recent_history_max_tokens,
            counter,
            max_replies_per_character=settings.max_replies_per_character,
            viewer_id=char_id,
        )

        # ---- 4. retrieved historical events (P2/P3, BM25 + token-aware) -
        selected_recent_ids = {
            l["message_id"] for l in recent_selected if l["message_id"] is not None
        }
        retrieved_candidates: list[dict[str, Any]] = []
        for l in older_lines:
            if l["presence"] in _RETRIEVED_PRESENCES:
                retrieved_candidates.append(l)
        for l in recent_lines:
            if (
                l["message_id"] not in selected_recent_ids
                and l["presence"] in _RETRIEVED_PRESENCES
            ):
                retrieved_candidates.append(l)

        query_text = self._build_retrieval_query(
            user_message, recent_selected, scene_state, char_name
        )
        retrieved_selected = self._select_retrieved(
            retrieved_candidates, query_text, budget.retrieved_history_budget, counter
        )
        retrieved_tokens = sum(counter.count(l["line"]) for l in retrieved_selected)
        diagnostics.retrieved_message_ids = [
            int(l["message_id"])
            for l in retrieved_selected
            if l["message_id"] is not None
        ]

        # ---- 5. fixed blocks (system / scene / instructions) -----------
        system_block = build_system_prompt(
            character,
            general_prompt,
            strict=False,
            relationships_block=relationships_block or "",
        )
        scene_block = build_scene_block(
            general_prompt,
            scene_state,
            present_character_names,
            current_character_name=char_name,
            character_locations=merge_char_locations(
                scene_state, character_locations, character_names
            ),
            character_appearances=character_appearances,
            locations=locations,
        )
        instructions_text = self._build_instructions_text(
            character, scene_state, stagnation_rounds, prior_replies, is_isolated
        )
        system_tokens = counter.count(system_block)
        scene_tokens = counter.count(scene_block)
        instructions_tokens = counter.count(instructions_text)

        # ---- 6. summary (P2, budgeted) ---------------------------------
        summary_text = (summary or "").strip()
        summary_block = build_character_summary_block(summary_text)
        summary_tokens = counter.count(summary_block)
        if summary_text and summary_tokens > budget.summary_budget:
            summary_text = self._truncate_text(
                summary_text, budget.summary_budget, counter
            )
            summary_tokens = counter.count(build_character_summary_block(summary_text))
            dropped.append(
                schemas.DroppedItem(
                    component="summary",
                    reason="budget",
                    preview=summary_text[:60],
                )
            )

        # ---- 7. memories (P2, budgeted) --------------------------------
        mem_list = list(memories)
        mem_block = build_memories_block(mem_list)
        mem_tokens = counter.count(mem_block)
        while mem_list and mem_tokens > budget.memory_budget:
            mem = mem_list.pop()
            dropped.append(
                schemas.DroppedItem(
                    component="memories",
                    reason="budget",
                    item_id=getattr(mem, "id", None),
                    preview=(getattr(mem, "content", "") or "")[:60],
                )
            )
            mem_block = build_memories_block(mem_list)
            mem_tokens = counter.count(mem_block)

        # ---- 8. final enforcement pass (priority order) ----------------
        fixed_tokens = system_tokens + scene_tokens + instructions_tokens
        content_available = max(
            0, budget.total_tokens - budget.reserve_tokens - fixed_tokens
        )

        # Overflow diagnostic: if fixed portion itself exceeds budget, record it
        if fixed_tokens > budget.total_tokens - budget.reserve_tokens:
            dropped.append(
                schemas.DroppedItem(
                    component="scene",
                    reason="fixed_budget_exceeded",
                    preview=scene_block[:60],
                )
            )

        content_used = summary_tokens + mem_tokens + retrieved_tokens + recent_tokens
        overrun = content_used - content_available

        if overrun > 0:
            # a) retrieved history (P3)
            overrun = self._trim_retrieved(
                retrieved_selected, overrun, counter, dropped
            )
            retrieved_tokens = sum(counter.count(l["line"]) for l in retrieved_selected)
            # b) memories (P2)
            overrun = self._trim_memories(mem_list, overrun, counter, dropped)
            mem_tokens = counter.count(build_memories_block(mem_list))
            # c) summary (P2)
            if overrun > 0 and summary_text:
                summary_tokens = counter.count(build_character_summary_block(summary_text))
                shrink = budget.summary_budget
                summary_text = self._truncate_text(summary_text, shrink, counter)
                summary_tokens = counter.count(build_character_summary_block(summary_text))
                overrun -= summary_tokens
                dropped.append(
                    schemas.DroppedItem(component="summary", reason="total_budget")
                )
            # d) recent dialogue (P1, last resort — soft target may be missed)
            overrun = self._trim_recent(recent_selected, overrun, counter, dropped)
            recent_tokens = sum(counter.count(l["line"]) for l in recent_selected)

        # ---- 9. assemble dialogue + diagnostics ------------------------
        retrieved_text = self._join_lines(
            sorted(retrieved_selected, key=lambda l: (l["message_id"] or 0))
        )
        recent_text = self._join_lines(recent_selected)
        dialogue_text = self._join_nonempty(retrieved_text, recent_text)

        recent_tokens = counter.count(build_recent_dialogue_block(recent_text))
        retrieved_tokens = counter.count(build_recent_dialogue_block(retrieved_text))
        summary_tokens = counter.count(build_character_summary_block(summary_text))
        mem_tokens = counter.count(build_memories_block(mem_list))

        total_tokens = (
            system_tokens
            + scene_tokens
            + summary_tokens
            + mem_tokens
            + retrieved_tokens
            + recent_tokens
            + instructions_tokens
        )

        diagnostics.summary_through_message_id = frontier or None
        all_included = [
            int(l["message_id"])
            for l in recent_selected + retrieved_selected
            if l["message_id"] is not None
        ]
        if all_included:
            diagnostics.oldest_included_message_id = min(all_included)
            diagnostics.newest_included_message_id = max(all_included)
        diagnostics.recent_message_ids = [
            int(l["message_id"]) for l in recent_selected if l["message_id"] is not None
        ]
        excluded = [
            int(l["message_id"])
            for l in lines
            if l["message_id"] is not None
            and l["message_id"] not in set(all_included)
        ]
        diagnostics.excluded_message_ids = excluded[:_MAX_EXCLUDED_IDS]
        diagnostics.memories_candidates = len(memories)
        diagnostics.memories_selected = len(mem_list)
        diagnostics.retrieved_events_selected = len(retrieved_selected)
        diagnostics.total_tokens = total_tokens

        component_tokens = {
            "system": system_tokens,
            "scene": scene_tokens,
            "relationships": counter.count(
                build_relationships_block(relationships_block)
            ),
            "summary": summary_tokens,
            "memories": mem_tokens,
            "retrieved_history": retrieved_tokens,
            "recent_history": recent_tokens,
            "instructions": instructions_tokens,
            "reserve": budget.reserve_tokens,
        }

        built = schemas.BuiltContext(
            dialogue_text=dialogue_text,
            recent_text=recent_text,
            retrieved_text=retrieved_text,
            scene_text=scene_block,
            summary_text=summary_text or None,
            memories=mem_list,
            total_tokens=total_tokens,
            token_count_mode=counter.mode,
            component_tokens=component_tokens,
            budget=budget,
            dropped_items=dropped,
            diagnostics=diagnostics,
        )
        self._log_context(built)
        return built

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _assemble_recent(
        self,
        lines: list[dict[str, Any]],
        budget_tokens: int,
        counter,
        *,
        max_replies_per_character: int,
        viewer_id: int,
    ) -> tuple[list[dict[str, Any]], int]:
        selected: list[dict[str, Any]] = []
        total = 0
        char_count: dict[int, int] = {}
        first = True
        for line_info in reversed(lines):
            line = line_info["line"]
            cid = line_info.get("char_id")
            is_self = cid is not None and int(cid) == viewer_id
            if (
                not is_self
                and max_replies_per_character > 0
                and cid is not None
            ):
                key = int(cid)
                char_count[key] = char_count.get(key, 0)
                if char_count[key] >= max_replies_per_character:
                    continue
                char_count[key] += 1

            est = counter.count(line)
            # The newest message is always included (P0); afterwards strict.
            if first or total + est <= budget_tokens:
                selected.append(line_info)
                total += est
                first = False
            else:
                break
        selected.reverse()
        return selected, total

    def _build_retrieval_query(
        self,
        user_message: str,
        recent_selected: list[dict[str, Any]],
        scene_state: Any,
        char_name: str,
    ) -> str:
        parts = [(user_message or "").strip()]
        tail = [l["line"] for l in recent_selected[-3:] if l["line"]]
        if tail:
            parts.append(" ".join(tail)[:400])
        if scene_state is not None:
            if getattr(scene_state, "time_of_day", ""):
                parts.append(f"Время: {scene_state.time_of_day}")
            custom_state = getattr(scene_state, "custom_state", None)
            if isinstance(custom_state, str):
                try:
                    custom_state = json.loads(custom_state)
                except (json.JSONDecodeError, TypeError):
                    custom_state = None
            if isinstance(custom_state, dict):
                if custom_state.get("active_goal"):
                    parts.append(f"Цель: {custom_state['active_goal']}")
                goals = custom_state.get("active_goals")
                if isinstance(goals, dict) and char_name in goals:
                    parts.append(f"Цель: {goals[char_name]}")
                if custom_state.get("mood"):
                    parts.append(f"Атмосфера: {custom_state['mood']}")
                tension = custom_state.get("tension", 0) or 0
                if tension > 0:
                    parts.append(f"Напряжение: {tension:.1f}")
        query = " ".join(parts).strip()
        return query[:_MAX_QUERY_CHARS]

    def _select_retrieved(
        self,
        candidates: list[dict[str, Any]],
        query_text: str,
        budget_tokens: int,
        counter,
    ) -> list[dict[str, Any]]:
        if not candidates or budget_tokens <= 0:
            return []
        contents = [c["line"] for c in candidates]
        bm25 = memory_service.SimpleBM25(
            contents, k1=settings.bm25_k1, b=settings.bm25_b
        )
        scored: list[tuple[float, dict[str, Any]]] = []
        for i, cand in enumerate(candidates):
            score = bm25.score(query_text, i)
            if score >= settings.bm25_min_score_threshold:
                scored.append((score, cand))
        scored.sort(key=lambda x: x[0], reverse=True)
        scored = scored[: settings.context_retrieval_candidates]

        selected: list[dict[str, Any]] = []
        total = 0
        for _score, cand in scored:
            est = counter.count(cand["line"])
            if total + est <= budget_tokens:
                selected.append(cand)
                total += est
            elif not selected:
                # Retrieval exception: top relevant event may exceed the
                # per-component budget; still surface it.
                selected.append(cand)
                total += est
        return selected

    def _build_instructions_text(
        self,
        character,
        scene_state: Any,
        stagnation_rounds: int,
        prior_replies: list[tuple[str, str]],
        is_isolated: bool,
    ) -> str:
        parts: list[str] = []
        if settings.enable_anti_mimicry and prior_replies:
            parts.append(build_anti_mimicry_block(character.name, prior_replies))
        if settings.enable_vocabulary_control:
            parts.append(build_vocabulary_block(character, prior_replies))
        if settings.scene_advancement_enabled:
            parts.append(
                build_scene_advancement_block(
                    stagnation_rounds,
                    max_stagnation_rounds=settings.stagnation_max_rounds,
                    proactive_action=False,
                )
            )
        if is_isolated:
            parts.append(build_isolated_block())
        parts.append(build_personality_block(character, scene_state))
        parts.append(build_personality_consistency_block(character))
        if settings.enable_post_history_reinforcement:
            parts.append(build_reinforcement_block(character.name))
        feedback = build_repetition_feedback_block("")
        if feedback:
            parts.append(feedback)
        parts.append(build_negative_prompting_block())
        if settings.use_chat_api:
            cue = (
                build_isolated_generation_cue(character.name)
                if is_isolated
                else build_generation_cue_for_chat(character.name)
            )
            parts.append(cue)
        else:
            cue = (
                build_isolated_generation_cue(character.name)
                if is_isolated
                else build_generation_cue(character.name)
            )
            parts.append(cue)
        return "\n\n".join(part for part in parts if part and part.strip())

    @staticmethod
    def _truncate_text(text: str, budget_tokens: int, counter) -> str:
        """Truncate raw text so the wrapped block fits approximately."""
        text = (text or "").strip()
        if not text or budget_tokens <= 0:
            return ""
        tokens = counter.count(build_character_summary_block(text))
        if tokens <= budget_tokens:
            return text
        ratio = budget_tokens / max(1, tokens)
        max_chars = max(50, int(len(text) * ratio))
        cut = text[:max_chars]
        for sep in ("\n\n", ". ", "! ", "? "):
            idx = max(cut.rfind(sep), cut.rfind(". "))
            if idx > max(50, len(cut) // 2):
                cut = cut[: idx + (1 if sep in (". ", "! ", "? ") else 0)]
                break
        return cut.strip()

    @staticmethod
    def _trim_retrieved(
        selected: list[dict[str, Any]],
        overrun: int,
        counter,
        dropped: list[schemas.DroppedItem],
    ) -> int:
        while overrun > 0 and selected:
            cand = selected.pop()  # lowest score is last
            est = counter.count(cand["line"])
            overrun -= est
            dropped.append(
                schemas.DroppedItem(
                    component="retrieved_history",
                    reason="total_budget",
                    item_id=cand.get("message_id"),
                    preview=cand["line"][:60],
                )
            )
        return max(0, overrun)

    @staticmethod
    def _trim_memories(
        mem_list: list,
        overrun: int,
        counter,
        dropped: list[schemas.DroppedItem],
    ) -> int:
        while overrun > 0 and mem_list:
            mem = mem_list.pop()
            est = counter.count(getattr(mem, "content", "") or "")
            overrun -= est
            dropped.append(
                schemas.DroppedItem(
                    component="memories",
                    reason="total_budget",
                    item_id=getattr(mem, "id", None),
                    preview=(getattr(mem, "content", "") or "")[:60],
                )
            )
        return max(0, overrun)

    @staticmethod
    def _trim_recent(
        selected: list[dict[str, Any]],
        overrun: int,
        counter,
        dropped: list[schemas.DroppedItem],
    ) -> int:
        while overrun > 0 and len(selected) > 1:
            oldest = selected.pop(0)
            est = counter.count(oldest["line"])
            overrun -= est
            dropped.append(
                schemas.DroppedItem(
                    component="recent_history",
                    reason="total_budget",
                    item_id=oldest.get("message_id"),
                    preview=oldest["line"][:60],
                )
            )
        return max(0, overrun)

    @staticmethod
    def _join_lines(lines: list[dict[str, Any]]) -> str:
        return "\n".join(l["line"] for l in lines if l["line"])

    @staticmethod
    def _join_nonempty(*parts: str) -> str:
        return "\n\n".join(p for p in parts if p and p.strip())

    def _log_context(self, built: schemas.BuiltContext) -> None:
        t = built.component_tokens
        logger.info(
            "Context budget: %d | system=%d scene=%d relationships=%d "
            "summary=%d memories=%d retrieved_history=%d recent_history=%d "
            "instructions=%d reserve=%d | TOTAL=%d mode=%s dropped=%d",
            built.budget.total_tokens,
            t.get("system", 0),
            t.get("scene_state", 0),
            t.get("relationships", 0),
            t.get("summary", 0),
            t.get("memories", 0),
            t.get("retrieved_history", 0),
            t.get("recent_history", 0),
            t.get("instructions", 0),
            t.get("reserve", 0),
            built.total_tokens,
            built.token_count_mode,
            len(built.dropped_items),
        )
        if settings.context_debug:
            d = built.diagnostics
            logger.debug(
                "Context diagnostics: oldest=%s newest=%s summary_through=%s "
                "recent=%d retrieved=%d excluded=%d memories=%d/%d "
                "retrieved_selected=%d",
                d.oldest_included_message_id,
                d.newest_included_message_id,
                d.summary_through_message_id,
                len(d.recent_message_ids),
                len(d.retrieved_message_ids),
                len(d.excluded_message_ids),
                d.memories_selected,
                d.memories_candidates,
                d.retrieved_events_selected,
            )
            for item in built.dropped_items:
                logger.debug(
                    "Context dropped: component=%s reason=%s item_id=%s preview=%r",
                    item.component,
                    item.reason,
                    item.item_id,
                    item.preview[:60],
                )


def build_relationships_block(relationships_text: str) -> str:
    """Thin wrapper so context accounting matches prompt_builder output."""
    from .prompt_builder import build_relationships_block as _build

    return _build(relationships_text)
