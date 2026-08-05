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
from . import perception
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
    build_relevant_memory_block,
    build_scene_advancement_block,
    build_scene_block,
    build_story_block,
    build_system_prompt,
    build_vocabulary_block,
    build_world_block,
    build_your_state_block,
    build_perceive_block,
    build_relationship_block,
    merge_char_locations,
)
from .repetition_detector import build_repetition_feedback_block
from .role_isolation import (
    build_generation_cue,
    build_generation_cue_for_chat,
)
from .token_counter import get_token_counter
from .witness_model import (
    Presence,
    build_character_recency_tail,
    format_line_for_presence,
    perceive_to_presence,
    render_perception_line,
    resolve_presence,
    voice_familiarity,
)
from .perception import parse_target_ids

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


async def _load_attention_map(
    db: AsyncSession,
    message_ids: list[int],
    character_id: int,
) -> dict[int, float]:
    """Attention scores (Sprint 4, §11) in chunks; empty when flag is off.

    Используется только для фильтрации recency tail — recent history рендер
    (presence-лестница) не трогается.
    """
    if not settings.attention_enabled or not message_ids:
        return {}
    attention_map: dict[int, float] = {}
    for start in range(0, len(message_ids), _PRESENCE_QUERY_CHUNK):
        chunk = message_ids[start : start + _PRESENCE_QUERY_CHUNK]
        attention_map.update(await crud.get_attention_map(db, chunk, character_id))
    return attention_map


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
        location_descriptions: dict[str, str] | None = None,
        stagnation_rounds: int = 0,
        viewer_location: str | None = None,
        prior_replies: list[tuple[str, str]] | None = None,
        is_isolated: bool = False,
        max_tokens: int | None = None,
        character_state: Any = None,
        what_you_know_block: str = "",
        story_block: str = "",
        active_goal_block: str = "",
        active_plan_block: str = "",
        crisis_block: str = "",
        rerank_signals: dict[int, memory_service.RerankSignals] | None = None,
    ) -> schemas.BuiltContext:
        counter = self._token_counter
        budget = build_budget(max_tokens)
        dropped: list[schemas.DroppedItem] = []
        diagnostics = schemas.ContextDiagnostics()

        # Context Builder v2 (Sprint 13, §23): новые блоки WORLD / WHAT YOU
        # PERCEIVE / RELATIONSHIP / RELEVANT MEMORY; legacy-блоки (scene →
        # relationships в system) остаются только при off.
        v2 = bool(settings.context_v2_enabled)
        world_block = ""
        perceive_block = ""
        relationship_block = ""
        relevant_memory_block = ""

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
        attention_map = await _load_attention_map(db, message_ids, char_id)

        filter_enabled = bool(settings.enable_witness_filter)
        # Renderer (WPE.md §4/§6, Фаза 6): канало-зависимый текст при включённом
        # двухканальном восприятии + частичном восприятии. Иначе — legacy-лестница
        # (идентичное поведение Фаз 1–5, откат по флагам).
        channel_render = bool(
            settings.world_engine_perception_enabled
            and settings.world_engine_partial_perception_enabled
        )
        world_state = (
            await crud._chat_world_state_for_characters(db, [character])
            if channel_render
            else None
        )
        known_voices = (
            await crud._known_voices_for_chat(db, chat_id)
            if (channel_render and world_state is not None)
            else None
        )
        lines: list[dict[str, Any]] = []
        for message in candidates:
            mid = getattr(message, "id", None)
            presence: Presence = "absent"
            line: str | None = None
            if channel_render and world_state is not None:
                ws = world_state
                if settings.world_engine_threads_enabled:
                    deliveries = await crud.thread_delivery_ids_for_message(
                        db, message
                    )
                    if deliveries:
                        ws = perception.PerceptionWorldState(
                            adjacency=world_state.adjacency,
                            thread_deliveries=deliveries,
                        )
                event = perception.event_from_message(message)
                result = perception.perceive(
                    world_state=ws,
                    event=event,
                    observer={
                        "character_id": char_id,
                        "location": viewer_location,
                        "location_id": getattr(character, "location_id", None),
                    },
                )
                known = voice_familiarity(
                    char_id, event.get("character_id"), known_voices
                )
                presence = perceive_to_presence(result, voice_known=known)
                line = render_perception_line(
                    message,
                    result,
                    character_names,
                    viewer_name=char_name,
                    voice_known=known,
                )
            if line is None:
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
                    "addressed": char_id
                    in parse_target_ids(
                        getattr(message, "target_character_ids", None)
                    ),
                }
            )

        # Recency Tail (WPE.md §6, Ул.3, И15): P0-события этого персонажа в
        # этом раунде. Добавляется после финальной сборки и исключается из
        # усечения бюджетом (резерв `context_reserve_tokens`).
        recency_tail_text = ""
        if settings.world_engine_recency_tail_enabled:
            recency_tail_text = build_character_recency_tail(
                round_messages,
                char_id,
                character_names,
                player_id=None,
                attention_map=attention_map,
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
            relationships_block="" if v2 else (relationships_block or ""),
        )
        if v2:
            # WORLD (P0): сцена — время/погода/локация/co-present. Заменяет
            # legacy `<scene>` (нет дублирования). RELATIONSHIP (P1) — в
            # отдельном user-блоке, а не в system.
            world_block = build_world_block(
                general_prompt,
                scene_state,
                present_character_names,
                current_character_name=char_name,
                character_locations=merge_char_locations(
                    scene_state, character_locations, character_names
                ),
                character_appearances=character_appearances,
                locations=locations,
                location_descriptions=location_descriptions,
            )
            scene_block = ""
        else:
            world_block = ""
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
                location_descriptions=location_descriptions,
            )
        # YOUR STATE (Sprint 3, Plans/update20.md §23): runtime-состояние
        # персонажа. Заполняется пост-раунд emotion_engine'ом; рендер только
        # при character_state_enabled. Часть фиксированных блоков — не усекается.
        state_block = ""
        if settings.character_state_enabled and character_state is not None:
            state_block = build_your_state_block(character_state)
        # WHAT YOU KNOW (Sprint 5, Plans/update20.md §9): beliefs персонажа.
        # Рендер только при beliefs_enabled (read-path НЕ читает beliefs до
        # включения флага — canary). Часть фиксированных блоков.
        if not what_you_know_block and settings.beliefs_enabled:
            what_you_know_block = await self._build_what_you_know_block(
                db, char_id
            )
        # STORY (Sprint 8, Plans/update20.md §16): сюжетный блок (фаза +
        # активные потоки top-K + прогресс). Передаётся из chat_engine (общий
        # для всех персонажей чата); иначе — сборка здесь при story_enabled.
        if not story_block and settings.story_enabled:
            story_block = await self._build_story_block(db, chat_id)
        # ACTIVE GOAL (Sprint 10, §21): intent NPC формируется в chat_engine
        # (перед генерацией) и передаётся сюда; no-op без intent.
        # ACTIVE PLAN (Sprint 10, §22): план NPC передаётся из chat_engine.
        # CRISIS (Sprint 11, §19): активные кризисные линии — «давление в
        # контексте» (data-only, мягкий сигнал). Передаётся из chat_engine;
        # иначе — сборка здесь при crisis_engine_enabled.
        if not crisis_block and settings.crisis_engine_enabled:
            crisis_block = await self._build_crisis_block(db, chat_id)

        # WHAT YOU PERCEIVE (P0, v2): perception-строки текущего раунда.
        # Только строки, которые персонаж реально воспринял (presence ≠ absent);
        # берутся из уже построенной presence-лестницы `lines`.
        if v2:
            round_ids = {
                int(m.id)
                for m in round_messages
                if getattr(m, "id", None) is not None
            }
            perceive_lines = [
                l["line"]
                for l in lines
                if l["message_id"] in round_ids and l["presence"] != "absent"
            ]
            perceive_block = build_perceive_block(perceive_lines)
            # RELATIONSHIP (P1, v2): интерпретации + anchors из переданного
            # relationships_block (top-K рёбер уже применён вызывающим).
            relationship_block = build_relationship_block(relationships_block or "")

        instructions_text = self._build_instructions_text(
            character,
            scene_state,
            stagnation_rounds,
            prior_replies,
            is_isolated,
            recency_tail=recency_tail_text,
        )
        system_tokens = counter.count(system_block)
        scene_tokens = counter.count(scene_block)
        world_tokens = counter.count(world_block)
        perceive_tokens = counter.count(perceive_block)
        relationship_tokens = counter.count(relationship_block)
        state_tokens = counter.count(state_block)
        what_you_know_tokens = counter.count(what_you_know_block)
        story_tokens = counter.count(story_block)
        active_goal_tokens = counter.count(active_goal_block)
        active_plan_tokens = counter.count(active_plan_block)
        crisis_tokens = counter.count(crisis_block)
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
        # Sprint 6 (§14): финальный порядок блока memories по сигналам контекста
        # (отношения/threads). Канонический rerank (включая semantic-ось)
        # выполняется выше — в `crud.get_hybrid_memories_for_characters`
        # (после RRF, до witness-boost); здесь — детерминированный re-order для
        # прямых вызовов build() без retrieval-пути. На уже-отсортированном
        # списке (путь chat_engine) не применяется: chat_engine не передаёт
        # сигналы в build(), чтобы не затирать порядок с semantic-осью.
        if (
            settings.hybrid_rerank_enabled
            and rerank_signals
            and rerank_signals.get(char_id) is not None
        ):
            builder_signals = rerank_signals.get(char_id)
            mem_list = memory_service.rerank_memories(
                mem_list,
                memory_service.RerankContext(
                    query_text="",
                    query_embedding=None,
                    relationship_target_names=builder_signals.relationship_target_names,
                    active_threads=builder_signals.active_threads,
                ),
            )
        mem_block = (
            build_relevant_memory_block(mem_list) if v2 else build_memories_block(mem_list)
        )
        mem_tokens = counter.count(mem_block)
        while mem_list and mem_tokens > budget.memory_budget:
            mem = mem_list.pop()
            dropped.append(
                schemas.DroppedItem(
                    component="relevant_memory" if v2 else "memories",
                    reason="budget",
                    item_id=getattr(mem, "id", None),
                    preview=(getattr(mem, "content", "") or "")[:60],
                )
            )
            mem_block = (
                build_relevant_memory_block(mem_list)
                if v2
                else build_memories_block(mem_list)
            )
            mem_tokens = counter.count(mem_block)
        if v2:
            relevant_memory_block = mem_block

        # ---- 8. final enforcement pass (priority order) ----------------
        fixed_tokens = (
            system_tokens
            + (world_tokens if v2 else scene_tokens)
            + perceive_tokens
            + relationship_tokens
            + state_tokens
            + what_you_know_tokens
            + story_tokens
            + active_goal_tokens
            + active_plan_tokens
            + crisis_tokens
            + instructions_tokens
        )
        content_available = max(
            0, budget.total_tokens - budget.reserve_tokens - fixed_tokens
        )

        # Overflow diagnostic: if fixed portion itself exceeds budget, record it
        if fixed_tokens > budget.total_tokens - budget.reserve_tokens:
            dropped.append(
                schemas.DroppedItem(
                    component="world" if v2 else "scene",
                    reason="fixed_budget_exceeded",
                    preview=(world_block if v2 else scene_block)[:60],
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
            mem_block = (
                build_relevant_memory_block(mem_list)
                if v2
                else build_memories_block(mem_list)
            )
            mem_tokens = counter.count(mem_block)
            if v2:
                relevant_memory_block = mem_block
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
            + (world_tokens if v2 else scene_tokens)
            + perceive_tokens
            + relationship_tokens
            + state_tokens
            + what_you_know_tokens
            + story_tokens
            + active_goal_tokens
            + active_plan_tokens
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
            "world": world_tokens,
            "perceive": perceive_tokens,
            "relationship": relationship_tokens,
            "character_state": state_tokens,
            "what_you_know": what_you_know_tokens,
            "story": story_tokens,
            "active_goal": active_goal_tokens,
            "active_plan": active_plan_tokens,
            "crisis": crisis_tokens,
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
            recency_tail_text=recency_tail_text,
            state_text=state_block,
            what_you_know_text=what_you_know_block,
            story_text=story_block,
            active_goal_text=active_goal_block,
            active_plan_text=active_plan_block,
            crisis_text=crisis_block,
            world_text=world_block,
            perceive_text=perceive_block,
            relationship_text=relationship_block,
            relevant_memory_text=relevant_memory_block,
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
            addressed = bool(line_info.get("addressed"))
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
            # Explicit addressing (WPE.md §2/§6) is also P0: addressed events
            # always survive the soft budget.
            if first or total + est <= budget_tokens or addressed:
                selected.append(line_info)
                total += est
                first = False
            else:
                continue
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
        recency_tail: str = "",
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
        # Recency Tail (И15): P0-события этого персонажа — самый конец
        # user-сообщения, непосредственно перед generation cue. Часть
        # фиксированных инструкций: не усекается бюджетом.
        if recency_tail:
            parts.append(recency_tail)
        if settings.use_chat_api:
            parts.append(build_generation_cue_for_chat(character.name))
        else:
            parts.append(build_generation_cue(character.name))
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
            # P0 (explicitly addressed) lines are never dropped: they are
            # exempt from budget truncation (WPE.md §2/§6).
            drop_idx = next(
                (
                    i
                    for i, l in enumerate(selected)
                    if not l.get("addressed")
                ),
                None,
            )
            if drop_idx is None:
                break
            oldest = selected.pop(drop_idx)
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

    async def _build_what_you_know_block(
        self, db: AsyncSession, character_id: int
    ) -> str:
        """WHAT YOU KNOW block (Sprint 5, §9): top-K beliefs персонажа.

        Рендер только при ``beliefs_enabled`` (canary); порог confidence —
        ``beliefs_render_confidence``. Пусто при отсутствии beliefs/флага.
        """
        try:
            from . import crud
            from .prompt_builder import build_what_you_know_block as _render

            beliefs = await crud.get_beliefs_for_character(
                db,
                character_id,
                top_k=settings.beliefs_top_k,
                min_confidence=settings.beliefs_render_confidence,
            )
            return _render(beliefs)
        except Exception as exc:  # noqa: BLE001 — блок не роняет контекст
            logger.warning(
                "Failed to build what_you_know block for character %s: %s",
                character_id, exc,
            )
            return ""

    async def _build_story_block(self, db: AsyncSession, chat_id: int) -> str:
        """STORY block (Sprint 8, §16): фаза + активные потоки top-K + прогресс.

        Общий для всех персонажей чата. Рендер только при ``story_enabled``
        (canary) И ``chats.story_enabled``; активные потоки усекаются до
        ``story_threads_max``.
        """
        try:
            from . import crud
            from .plot import story_state as plot_story

            chat = await crud.get_chat(db, chat_id)
            if chat is None or not getattr(chat, "story_enabled", False):
                return ""
            return await plot_story.build_story_block(db, chat_id)
        except Exception as exc:  # noqa: BLE001 — блок не роняет контекст
            logger.warning(
                "Failed to build story block for chat %s: %s", chat_id, exc,
            )
            return ""

    async def _build_crisis_block(self, db: AsyncSession, chat_id: int) -> str:
        """CRISIS block (Sprint 11, §19): активные кризисные линии.

        «Давление в контексте» — data-only мягкий сигнал, не инструкция.
        Рендер только при ``crisis_engine_enabled`` (canary).
        """
        try:
            from . import crud
            from .plot import crisis_engine as plot_crisis

            return await plot_crisis.build_crisis_block(db, chat_id)
        except Exception as exc:  # noqa: BLE001 — блок не роняет контекст
            logger.warning(
                "Failed to build crisis block for chat %s: %s", chat_id, exc,
            )
            return ""

    def _log_context(self, built: schemas.BuiltContext) -> None:
        t = built.component_tokens
        logger.info(
            "Context budget: %d | system=%d scene=%d world=%d perceive=%d "
            "relationship=%d character_state=%d relationships=%d summary=%d "
            "memories=%d retrieved_history=%d recent_history=%d instructions=%d "
            "story=%d reserve=%d | TOTAL=%d mode=%s dropped=%d v2=%s",
            built.budget.total_tokens,
            t.get("system", 0),
            t.get("scene", 0),
            t.get("world", 0),
            t.get("perceive", 0),
            t.get("relationship", 0),
            t.get("character_state", 0),
            t.get("relationships", 0),
            t.get("summary", 0),
            t.get("memories", 0),
            t.get("retrieved_history", 0),
            t.get("recent_history", 0),
            t.get("instructions", 0),
            t.get("story", 0),
            t.get("reserve", 0),
            built.total_tokens,
            built.token_count_mode,
            len(built.dropped_items),
            bool(settings.context_v2_enabled),
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
