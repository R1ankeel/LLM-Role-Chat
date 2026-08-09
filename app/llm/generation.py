"""LLM-генерация: ретраи/валидация/повторы + публичный JSON-фасад (Sprint 5A).

Перенесено 1:1 из ``app/ollama_client.py`` (диапазоны §4.3:
``_invoke_llm`` 1106–1122, ``_log_repetition`` 1202–1220, ``_generate_once``
1223–1775, vocabulary borrowing 1778–1914, ``generate`` 1917–2332). Публичный
фасад ``invoke_json``/``extract_json_payload`` сохранён для потребителей
(relationship_analyzer, sensors_service).
"""

from __future__ import annotations

import asyncio
import random
import re
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ..config import settings
from ..context_budget_manager import context_budget_manager
from .. import schemas
from .. import action_resolution
from ..prompt_builder import (
    build_anti_mimicry_block,
    build_character_summary_block,
    build_consistency_feedback_block,
    build_intervention_block,
    build_isolated_block,
    build_memories_block,
    build_personality_block,
    build_personality_consistency_block,
    build_recent_dialogue_block,
    build_reinforcement_block,
    build_scene_advancement_block,
    build_scene_block,
    build_system_prompt,
    build_take_actions_instruction,
    build_vocabulary_block,
    build_world_block,
    merge_char_locations,
)
from ..repetition_detector import (
    RepetitionAnalysis,
    analyze_response,
    build_repetition_feedback,
    build_repetition_feedback_block,
)
from ..role_isolation import (
    build_generation_cue,
    build_generation_cue_for_chat,
    build_stop_sequences,
    find_foreign_speaker_marker,
    sanitize_and_validate_response,
)
from ..witness_model import Presence
from .prompting import (
    ChatMessage,
    _build_chat_payload,
    _build_generate_payload,
    _build_generation_messages,
    _character_temperature,
    _count_history_tokens,
    _count_prompt_tokens,
    _messages_to_prompt,
    _resolve_thinking,
    filter_history_for_character_messages,
    format_history_for_character,
)
from .transport import (
    DEFAULT_TEMPERATURE,
    _call_ollama,
    _call_ollama_chat,
    _stream_ollama_chat,
    _stream_ollama_generate,
    llm_request,
)
from .wpe import (
    _parse_tool_calls,
    _parse_turn_output_json,
    _record_shadow_turn,
)

import logging

logger = logging.getLogger(__name__)

__all__ = ["invoke_json", "extract_json_payload", "generate"]


def extract_json_payload(raw: str) -> object | None:
    """Публичная обёртка над извлечением первого JSON из ответа модели."""
    from .tasks import _extract_json_payload as _extract

    return _extract(raw)


async def invoke_json(
    client: Any,
    model_name: str,
    messages: list[dict[str, str]],
    *,
    temperature: float = DEFAULT_TEMPERATURE,
    format_schema: dict | None = None,
    timeout: float | None = None,
    enable_thinking: bool = False,
    num_ctx: int | None = None,
    num_predict: int | None = None,
) -> str | None:
    """Нестриминговый LLM-вызов с JSON-контрактом.

    - ``format_schema`` задан — JSON-mode (структурированный вывод) через
      ``/api/chat`` или ``/api/generate`` (сенсорный путь). Возвращает текст
      ответа модели (``content``/``response``) или ``None``, если контента нет.
    - иначе — обычный нестриминговый вызов (``_invoke_llm``), возвращает сырой
      текст (путь relationship_analyzer).

    Исключения (RuntimeError от ``_invoke_llm``, ``asyncio.TimeoutError``,
    httpx-ошибки) пробрасываются наружу — вызывающий решает, как обрабатывать.
    """
    if format_schema is not None:
        return await _invoke_json_mode(
            client=client,
            model_name=model_name,
            messages=messages,
            temperature=temperature,
            format_schema=format_schema,
            timeout=timeout,
            enable_thinking=enable_thinking,
            num_ctx=num_ctx,
            num_predict=num_predict,
        )
    return await _invoke_llm(
        client, model_name, messages, temperature=temperature
    )


async def _invoke_json_mode(
    *,
    client: Any,
    model_name: str,
    messages: list[dict[str, str]],
    temperature: float,
    format_schema: dict,
    timeout: float | None,
    enable_thinking: bool,
    num_ctx: int | None,
    num_predict: int | None,
) -> str | None:
    """JSON-mode вызов (перенесён 1:1 из ``sensors_service.SensorsService.invoke``)."""
    if settings.use_chat_api:
        payload = _build_chat_payload(
            model_name,
            messages,
            temperature,
            [],
            stream=False,
            enable_thinking=enable_thinking,
            num_ctx=num_ctx,
            num_predict=num_predict,
            format_schema=format_schema,
        )
        endpoint = "/api/chat"
        async with llm_request(model_name, endpoint):
            response = await asyncio.wait_for(
                client.post(endpoint, json=payload),
                timeout=timeout,
            )
        response.raise_for_status()
        return response.json().get("message", {}).get("content", "") or None

    prompt = "\n\n".join(m["content"] for m in messages if m.get("content"))
    payload = _build_generate_payload(
        model_name,
        prompt,
        temperature,
        [],
        stream=False,
        enable_thinking=enable_thinking,
        num_ctx=num_ctx,
        num_predict=num_predict,
        format_schema=format_schema,
    )
    endpoint = "/api/generate"
    async with llm_request(model_name, endpoint):
        response = await asyncio.wait_for(
            client.post(endpoint, json=payload),
            timeout=timeout,
        )
    response.raise_for_status()
    return response.json().get("response", "") or None


async def _invoke_llm(
    client: httpx.AsyncClient,
    model_name: str,
    messages: list[ChatMessage],
    *,
    temperature: float = DEFAULT_TEMPERATURE,
    stop: list[str] | None = None,
) -> str:
    """Route a non-streaming LLM call to Chat or Generate API."""
    if settings.use_chat_api:
        return await _call_ollama_chat(
            client, model_name, messages, temperature=temperature, stop=stop
        )
    prompt = _messages_to_prompt(messages)
    return await _call_ollama(
        client, model_name, prompt, temperature=temperature, stop=stop
    )


def _log_repetition(
    chat_id: int,
    character_name: str,
    analysis: RepetitionAnalysis,
    retry_number: int,
) -> None:
    logger.info(
        "[REPETITION] chat_id=%d character=%s score=%.2f progression=%.2f "
        "stagnation=%s actions=%s interaction=%s retry=%d reason=%s",
        chat_id,
        character_name,
        analysis.score,
        analysis.progression_score,
        analysis.stagnation,
        analysis.repeated_actions,
        analysis.interaction_pattern or "-",
        retry_number,
        analysis.reason,
    )


async def _generate_once(
    client: httpx.AsyncClient,
    *,
    chat_id: int,
    character,
    messages_history: list,
    general_prompt: str,
    memories: list,
    other_character_names: list[str],
    max_history_length: int,
    model_name: str,
    character_names: dict[int, str] | None,
    summary: str | None,
    viewer_character_id: int | None,
    presence_map: dict[int, Presence] | None,
    same_round_message_ids: set[int] | None,
    enable_thinking: bool | None,
    viewer_location: str | None,
    character_locations: dict[int, str] | None,
    stop: list[str],
    temperature: float,
    strict_isolation: bool,
    repetition_feedback: str,
    attempt_label: str,
    prior_replies: list[tuple[str, str]] | None = None,
    scene_state=None,
    present_character_names: list[str] | None = None,
    stagnation_rounds: int = 0,
    is_isolated: bool = False,
    locations: str = "[]",
    location_descriptions: dict[str, str] | None = None,
    relationships_block: str = "",
    behavior_drivers_block: str = "",
    open_issues_block: str = "",
    built_context: schemas.BuiltContext | None = None,
    proactive_boost: float = 0.0,
    epistemic_mask_block: str = "",
    directive: str | None = None,
    recency_tail_block: str = "",
    consistency_feedback: str = "",
    what_you_know_block: str = "",
    story_block: str = "",
    active_goal_block: str = "",
    active_plan_block: str = "",
    crisis_block: str = "",
    world_state_block: str = "",
) -> tuple[str, str, bool, int, list[str], schemas.TurnOutput | None]:
    """One LLM call + isolation sanitize.

    Returns (raw, sanitized, isolation_ok, thinking_len, tokens_list,
    shadow_turn_output) — последний элемент: структурированный `TurnOutput`
    из tool_calls/JSON-схемы (WPE Фаза 2), `None`, если инструменты выключены
    или ответ невалиден. Фаза 5 использует его для Action Resolution.
    """
    api_mode = "chat" if settings.use_chat_api else "generate"
    thinking = _resolve_thinking(enable_thinking)
    if settings.world_engine_recency_tail_enabled and not recency_tail_block:
        recency_tail_block = (
            built_context.recency_tail_text
            if built_context is not None
            else ""
        )
    if not settings.world_engine_recency_tail_enabled:
        recency_tail_block = ""
    # Context Builder v2 (Sprint 13, §23): при включённом флаге relationships
    # уходят из system-промпта в отдельный user-блок RELATIONSHIP; legacy
    # `<relationships>` в system остаётся только при off.
    v2 = bool(settings.context_v2_enabled)
    system_prompt = build_system_prompt(
        character, general_prompt, strict=strict_isolation,
        relationships_block="" if v2 else (relationships_block or ""),
        take_actions_instruction=(
            build_take_actions_instruction()
            if settings.world_engine_tools_enabled
            else ""
        ),
    )

    if built_context is not None:
        dialogue_text = built_context.dialogue_text
        history_text = ""
    else:
        history_text = format_history_for_character(
            messages_history,
            max_history_length,
            character.name,
            character_names,
            viewer_character_id=viewer_character_id or character.id,
            presence_map=presence_map,
            same_round_message_ids=same_round_message_ids,
            viewer_location=viewer_location
            if viewer_location is not None
            else getattr(character, "location", "") or "",
            character_locations=character_locations,
            max_replies_per_character=settings.max_replies_per_character,
        )
        dialogue_text = history_text
    summary_block = build_character_summary_block(
        (built_context.summary_text if built_context is not None else summary) or ""
    )
    # RELEVANT MEMORY (v2, §23): reranked memories в отдельном блоке; при off —
    # legacy `<character_memories>`.
    if v2 and built_context is not None:
        memories_block = built_context.relevant_memory_text
    else:
        memories_block = build_memories_block(
            built_context.memories if built_context is not None else memories
        )
    dialogue_block = build_recent_dialogue_block(dialogue_text)

    # Anti-mimicry block for sequential generation
    anti_mimicry_block = ""
    if settings.enable_anti_mimicry and prior_replies:
        anti_mimicry_block = build_anti_mimicry_block(character.name, prior_replies)

    # Personality reinforcement block — prevents role drift (Phase 3)
    personality_block = build_personality_block(character, scene_state)
    consistency_block = build_personality_consistency_block(character)

    reinforcement = ""
    if settings.enable_post_history_reinforcement:
        reinforcement = build_reinforcement_block(character.name)

    # Scene block with world tracking — per-character location (P3).
    # WORLD (v2, §23): заменяет legacy `<scene>`; legacy рендерится только off.
    if built_context is not None:
        scene_block = built_context.world_text if v2 else built_context.scene_text
    else:
        if v2:
            scene_block = build_world_block(
                general_prompt,
                scene_state,
                present_character_names,
                current_character_name=character.name,
                character_locations=merge_char_locations(
                    scene_state, character_locations, character_names
                ),
                locations=locations,
                location_descriptions=location_descriptions,
            )
        else:
            char_locs = merge_char_locations(scene_state, character_locations, character_names)
            scene_block = build_scene_block(
                general_prompt,
                scene_state,
                present_character_names,
                current_character_name=character.name,
                character_locations=char_locs,
                locations=locations,
                location_descriptions=location_descriptions,
            )

    # WHAT YOU PERCEIVE / RELATIONSHIP (v2, §23): user-блоки из built_context;
    # при off — пустые (legacy-пути не меняются).
    perceive_block = built_context.perceive_text if (v2 and built_context is not None) else ""
    relationship_user_block = (
        built_context.relationship_text
        if (v2 and built_context is not None)
        else ""
    )

    # YOUR STATE block (Sprint 3, §23) — runtime-состояние персонажа.
    # Рендер только когда context_builder получил state (флаг
    # character_state_enabled + включённый билдер); иначе блок пуст.
    your_state_block = built_context.state_text if built_context is not None else ""

    # WHAT YOU KNOW block (Sprint 5, §9) — beliefs персонажа. Рендер только при
    # beliefs_enabled; фолбэк на переданный параметр (non-context-путь).
    if not what_you_know_block and built_context is not None:
        what_you_know_block = built_context.what_you_know_text

    # STORY block (Sprint 8, §16) — сюжет чата. Рендер только при
    # story_enabled; фолбэк на переданный параметр (non-context-путь).
    if not story_block and built_context is not None:
        story_block = built_context.story_text

    # ACTIVE GOAL / ACTIVE PLAN (Sprint 10, §21/§22) — intent и план NPC.
    # Рендер только при включённых флагах (решает chat_engine); фолбэк на
    # переданные параметры (non-context-путь).
    if not active_goal_block and built_context is not None:
        active_goal_block = built_context.active_goal_text
    if not active_plan_block and built_context is not None:
        active_plan_block = built_context.active_plan_text

    # CRISIS block (Sprint 11, §19) — активные кризисные линии («давление в
    # контексте», data-only). Рендер только при crisis_engine_enabled; фолбэк
    # на переданный параметр (non-context-путь).
    if not crisis_block and built_context is not None:
        crisis_block = built_context.crisis_text

    # WORLD STATE block (Sprint 14) — глобальный блок (локации + расположение
    # всех персонажей, вкл. игрока). Передан из чат-пайплайна (собран там же,
    # где и WORLD); фолбэк на built_context для не-chat-путей.
    if not world_state_block and built_context is not None:
        world_state_block = built_context.world_state_text

    # Vocabulary fingerprinting block — prevents style contamination (Phase 5)
    vocabulary_block = build_vocabulary_block(character, prior_replies)

    # Scene advancement block — breaks stagnation loops (Phase 6)
    # Weighted proactive boost (Sprint 1 п.7, docs/relations.md §7.4) raises the
    # *probability* of a proactive action when the character has salient open
    # issues; it never guarantees one. Default 0.0 keeps the old behavior.
    scene_advancement_block = ""
    if settings.scene_advancement_enabled:
        proactive_chance = min(
            settings.proactive_action_chance + max(0.0, float(proactive_boost)),
            1.0,
        )
        proactive_action = (
            stagnation_rounds == 0
            and random.random() < proactive_chance
        )
        scene_advancement_block = build_scene_advancement_block(
            stagnation_rounds,
            max_stagnation_rounds=settings.stagnation_max_rounds,
            proactive_action=proactive_action,
        )

    isolated_block = build_isolated_block() if is_isolated else ""
    directive_block = build_intervention_block(directive) if directive else ""

    tokens_collected = []

    if settings.use_chat_api:
        generation_cue = build_generation_cue_for_chat(character.name)
        chat_messages = _build_generation_messages(
            system_prompt,
            summary_block,
            memories_block,
            dialogue_block,
            scene_block,
            reinforcement,
            generation_cue,
            repetition_feedback=repetition_feedback,
            consistency_feedback=consistency_feedback,
            anti_mimicry_block=anti_mimicry_block,
            vocabulary_block=vocabulary_block,
            personality_block=personality_block,
            consistency_block=consistency_block,
            scene_advancement_block=scene_advancement_block,
            isolated_block=isolated_block,
            behavior_drivers_block=behavior_drivers_block,
            open_issues_block=open_issues_block,
            epistemic_mask_block=epistemic_mask_block,
            directive_block=directive_block,
            recency_tail_block=recency_tail_block,
            your_state_block=your_state_block,
            what_you_know_block=what_you_know_block,
            story_block=story_block,
            active_goal_block=active_goal_block,
            active_plan_block=active_plan_block,
            crisis_block=crisis_block,
            perceive_block=perceive_block,
            relationship_block=relationship_user_block,
            world_state_block=world_state_block,
        )
        prompt_len = sum(len(msg["content"]) for msg in chat_messages)
        full_prompt = _messages_to_prompt(chat_messages)
    else:
        generation_cue = build_generation_cue(character.name)
        context_parts = [system_prompt]
        if world_state_block:
            context_parts.append(world_state_block)
        if summary_block:
            context_parts.append(summary_block)
        if memories_block:
            context_parts.append(memories_block)
        if dialogue_block:
            context_parts.append(dialogue_block)
        if scene_block:
            context_parts.append(scene_block)
        if perceive_block:
            context_parts.append(perceive_block)
        if your_state_block:
            context_parts.append(your_state_block)
        if what_you_know_block:
            context_parts.append(what_you_know_block)
        if story_block:
            context_parts.append(story_block)
        if active_goal_block:
            context_parts.append(active_goal_block)
        if active_plan_block:
            context_parts.append(active_plan_block)
        if crisis_block:
            context_parts.append(crisis_block)
        if relationship_user_block:
            context_parts.append(relationship_user_block)
        if anti_mimicry_block:
            context_parts.append(anti_mimicry_block)
        if vocabulary_block:
            context_parts.append(vocabulary_block)
        if scene_advancement_block:
            context_parts.append(scene_advancement_block)
        if isolated_block:
            context_parts.append(isolated_block)
        if personality_block:
            context_parts.append(personality_block)
        if consistency_block:
            context_parts.append(consistency_block)
        if reinforcement:
            context_parts.append(reinforcement)
        feedback_block = build_repetition_feedback_block(repetition_feedback)
        if feedback_block:
            context_parts.append(feedback_block)
        consistency_feedback_block = build_consistency_feedback_block(
            consistency_feedback
        )
        if consistency_feedback_block:
            context_parts.append(consistency_feedback_block)
        if behavior_drivers_block:
            context_parts.append(behavior_drivers_block)
        if open_issues_block:
            context_parts.append(open_issues_block)
        if epistemic_mask_block:
            context_parts.append(epistemic_mask_block)
        if directive_block:
            context_parts.append(directive_block)
        if recency_tail_block:
            context_parts.append(recency_tail_block)
        context_parts.append(generation_cue)
        full_prompt = "\n\n".join(context_parts)
        prompt_len = len(full_prompt)
        chat_messages = []

    prompt_tokens = _count_prompt_tokens(chat_messages, full_prompt)
    history_tokens = _count_history_tokens(dialogue_block, built_context)
    budget = context_budget_manager.calculate(
        chat_id=chat_id,
        prompt_tokens=prompt_tokens,
        history_tokens=history_tokens,
        thinking=thinking,
    )
    num_ctx = budget.final_ctx

    logger.info(
        "[chat_id=%d] Ollama request (api=%s, model=%s, character=%s, %s, "
        "prompt_len=%d, prompt_tokens=%d, history=%d msgs, memories=%d, has_summary=%s, stop=%d, "
        "thinking=%s, has_rep_feedback=%s, num_ctx=%d, context=%s)",
        chat_id,
        api_mode,
        model_name,
        character.name,
        attempt_label,
        prompt_len,
        prompt_tokens,
        len(messages_history),
        len(memories),
        bool(summary_block),
        len(stop),
        thinking,
        bool(repetition_feedback),
        num_ctx,
        "builder" if built_context is not None else "legacy",
    )

    generated = ""
    thinking_len = 0
    tools_enabled = settings.world_engine_tools_enabled
    shadow_turn_output: schemas.TurnOutput | None = None
    shadow_tool_mode = "text"

    if tools_enabled:
        # WPE 3.0 Фаза 2: tool-calling take_actions (shadow). Токены стримятся
        # как раньше, tool_calls в терминальном сообщении не рендерятся (§8).
        shadow_tool_mode = "tools" if settings.use_chat_api else "format"
        _t0 = time.perf_counter()
        if settings.use_chat_api:
            tool_calls: list[dict[str, Any]] = []
            token_buffer = ""
            async for event in _stream_ollama_chat(
                client,
                model_name,
                chat_messages,
                temperature=temperature,
                stop=stop,
                enable_thinking=thinking,
                num_ctx=num_ctx,
                tools=[schemas.build_take_actions_tool()],
                format_schema=schemas.build_take_actions_json_schema(),
            ):
                if event.get("content"):
                    token_buffer += event["content"]
                    if len(token_buffer) >= 10:
                        tokens_collected.append(token_buffer)
                        token_buffer = ""
                elif event["type"] == "complete":
                    if token_buffer:
                        tokens_collected.append(token_buffer)
                        token_buffer = ""
                    generated = event["text"]
                    thinking_len = event.get("thinking_len", 0)
                    shadow_tool_mode = event.get("tool_mode", "tools")
                    tool_calls = event.get("tool_calls") or []
            shadow_turn_output = _parse_tool_calls(tool_calls)
        else:
            token_buffer = ""
            async for event in _stream_ollama_generate(
                client,
                model_name,
                full_prompt,
                temperature=temperature,
                stop=stop,
                enable_thinking=thinking,
                num_ctx=num_ctx,
                format_schema=schemas.build_take_actions_json_schema(),
            ):
                if event.get("content"):
                    token_buffer += event["content"]
                    if len(token_buffer) >= 10:
                        tokens_collected.append(token_buffer)
                        token_buffer = ""
                elif event["type"] == "complete":
                    if token_buffer:
                        tokens_collected.append(token_buffer)
                        token_buffer = ""
                    generated = event["text"]
                    thinking_len = event.get("thinking_len", 0)
                    shadow_tool_mode = event.get("tool_mode", "format")
            shadow_turn_output = _parse_turn_output_json(generated)
        _record_shadow_turn(
            chat_id,
            character.name,
            shadow_tool_mode,
            shadow_turn_output,
            (time.perf_counter() - _t0) * 1000.0,
        )
    elif settings.use_chat_api:
        if thinking:
            token_buffer = ""
            async for event in _stream_ollama_chat(
                client,
                model_name,
                chat_messages,
                temperature=temperature,
                stop=stop,
                enable_thinking=True,
                num_ctx=num_ctx,
            ):
                if event.get("content"):
                    token_buffer += event["content"]
                    if len(token_buffer) >= 10:
                        tokens_collected.append(token_buffer)
                        token_buffer = ""
                elif event["type"] == "complete":
                    if token_buffer:
                        tokens_collected.append(token_buffer)
                        token_buffer = ""
                    generated = event["text"]
                    thinking_len = event.get("thinking_len", 0)
        else:
            generated = await _call_ollama_chat(
                client,
                model_name,
                chat_messages,
                temperature=temperature,
                stop=stop,
                num_ctx=num_ctx,
            )
    elif thinking:
        token_buffer = ""
        async for event in _stream_ollama_generate(
            client,
            model_name,
            full_prompt,
            temperature=temperature,
            stop=stop,
            enable_thinking=True,
            num_ctx=num_ctx,
        ):
            if event.get("content"):
                token_buffer += event["content"]
                if len(token_buffer) >= 10:
                    tokens_collected.append(token_buffer)
                    token_buffer = ""
            elif event["type"] == "complete":
                if token_buffer:
                    tokens_collected.append(token_buffer)
                    token_buffer = ""
                generated = event["text"]
                thinking_len = event.get("thinking_len", 0)
    else:
        generated = await _call_ollama(
            client, model_name, full_prompt, temperature=temperature, stop=stop,
            num_ctx=num_ctx,
        )

    validation_result = sanitize_and_validate_response(
        generated,
        character.name,
        other_character_names,
        settings.min_character_response_length,
    )
    sanitized = validation_result.cleaned_text
    is_valid = validation_result.is_valid
    hard_violation = validation_result.hard_violation
    soft_violation = validation_result.soft_violation
    had_foreign_marker = (
        find_foreign_speaker_marker(generated, other_character_names) is not None
    )

    logger.info(
        "[chat_id=%d] Response (api=%s, char=%s, %s, raw=%d, sanitized=%d, "
        "thinking=%d, foreign_marker=%s, isolation_valid=%s, soft_violation=%s, hard_violation=%s)",
        chat_id,
        api_mode,
        character.name,
        attempt_label,
        len(generated),
        len(sanitized),
        thinking_len,
        had_foreign_marker,
        is_valid,
        soft_violation,
        hard_violation,
    )

    if had_foreign_marker:
        logger.warning(
            "[chat_id=%d] Foreign speaker marker for %s (%s)",
            chat_id,
            character.name,
            attempt_label,
        )

    if soft_violation:
        logger.debug(
            "[chat_id=%d] Soft perspective violation for %s (%s)",
            chat_id,
            character.name,
            attempt_label,
        )

    if built_context is not None and settings.context_debug:
        d = built_context.diagnostics
        logger.debug(
            "[chat_id=%d] Built context (%s): total=%d budget=%d oldest=%s "
            "newest=%s summary_through=%s recent=%d retrieved=%d excluded=%d "
            "memories=%d/%d dropped=%d",
            chat_id,
            character.name,
            built_context.total_tokens,
            built_context.budget.total_tokens,
            d.oldest_included_message_id,
            d.newest_included_message_id,
            d.summary_through_message_id,
            len(d.recent_message_ids),
            len(d.retrieved_message_ids),
            len(d.excluded_message_ids),
            d.memories_selected,
            d.memories_candidates,
            len(built_context.dropped_items),
        )

    return (
        generated,
        sanitized,
        is_valid,
        thinking_len,
        tokens_collected,
        num_ctx,
        shadow_turn_output,
    )


_INFLECTION_SUFFIXES = (
    "аются", "ются", "ется", "аться", "иться", "еться",
    "ешься", "ишься", "его", "ому", "ему", "ыми", "ими", "ого",
    "ая", "яя", "ое", "ее", "ые", "ие", "ый", "ий", "ой",
    "ую", "юю", "ею", "ою", "ами", "ями", "ов", "ев", "ам", "ям",
    "ах", "ях", "ом", "ем", "им", "ым", "ей", "у", "ю", "е", "о",
    "ы", "и", "а", "я",
)


def _vocab_key(word: str) -> str:
    """Coarse morphological normalization for Russian words.

    Strips common inflectional endings so different forms of the same stem
    (e.g. ``рука``/``рукой``/``руки``) collapse to one key. Keeps stems >= 3
    chars so short function words stay intact.
    """
    w = re.sub(r"[^а-яёa-z]", "", word.lower().replace("ё", "е"))
    for suf in _INFLECTION_SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: len(w) - len(suf)]
    return w


# Base roots treated as non-distinctive shared vocabulary (common Russian +
# physical/intimacy words any two characters may legitimately both use).
_VOCAB_STOP_ROOTS = {
    "что", "это", "так", "вот", "все", "или", "как", "его", "она", "они",
    "только", "если", "нет", "да", "уже", "еще", "там", "тут", "когда",
    "даже", "меня", "тебя", "него", "них", "мой", "твой", "свой", "наш",
    "ваш", "этот", "быть", "будет", "стать", "сказать", "такое", "сейчас",
    "здесь", "тогда", "потом", "вдруг", "опять", "снова", "чтобы", "потому",
    "поэтому", "который", "очень", "просто", "совсем", "бы", "же", "ли",
    "не", "ни", "а", "но", "да", "кто", "чего", "чем", "кому", "себя",
    "будто", "словно", "пока", "ведь", "наконец", "кстати", "между", "через",
    "около", "почти", "мимо", "вокруг", "перед", "после", "среди", "сам",
    "весь", "ничего", "никогда", "всегда", "более", "менее", "нужно",
    "можно", "нельзя", "надо", "хотеть", "мочь", "должен", "знать",
    "понимать", "видеть", "слышать", "думать", "казаться", "конечно",
    "наверное", "вероятно", "возможно", "правда", "честно", "точно",
    "действительно", "пожалуй", "скорее", "почему", "отчего", "зачем",
    "какой", "намного", "чуть", "немного", "слишком", "гораздо",
    "рука", "губы", "тело", "плечо", "близко", "рядом", "вместе", "друг",
    "голова", "лицо", "глаза", "взгляд", "смотреть", "глядеть", "улыбка",
    "улыбнуться", "дыхание", "сердце", "кожа", "шея", "волосы", "пальцы",
    "ладонь", "спина", "живот", "грудь", "бедра", "ноги", "поцелуй",
    "поцеловать", "целовать", "обнять", "обнимать", "объятие", "касаться",
    "коснуться", "прикосновение", "шептать", "шепот", "дышать", "дрожать",
    "дрожь", "тепло", "жар", "страсть", "желание", "нежность", "нежный",
    "мягкий", "сильно", "крепкий", "медленный", "осторожно", "тихо",
    "громко", "чувствовать", "чувство", "волна", "напряжение", "спокойный",
    "долго", "день", "ночь", "утро", "вечер", "сегодня", "завтра",
    "язык", "слово", "голос", "смех", "вздох", "стон", "запах", "вкус",
}

_VOCAB_STOP_KEYS = {_vocab_key(w) for w in _VOCAB_STOP_ROOTS}


def _content_words(text: str) -> set[str]:
    """Set of normalized content-word keys, with global stopwords removed."""
    if not text:
        return set()
    words = {
        _vocab_key(w)
        for w in re.findall(r"\b[а-яёa-z-]{4,}\b", text.lower())
    }
    return words - _VOCAB_STOP_KEYS


def _check_vocabulary_borrowing(
    text: str,
    character: Any,
    other_character_names: list[str],
    messages_history: list,
    character_names: dict[int, str] | None = None,
) -> str:
    """Check if the character's response borrows vocabulary from other characters.

    The character's own vocabulary is derived from ``speech_style`` +
    ``example_messages`` + their own recent replies (ground-truth usage), so
    common/shared vocabulary in intimate scenes is NOT treated as borrowing.

    Returns a description of the character's own speech style if borrowing
    is detected, or empty string if clean.
    """
    if not text or not other_character_names:
        return ""

    own_style = (getattr(character, "speech_style", "") or "").strip()
    example_messages = getattr(character, "example_messages", "") or ""

    own_history: list[str] = []
    foreign_replies: list[str] = []
    for msg in messages_history:
        if getattr(msg, "role", None) != "character":
            continue
        cid = getattr(msg, "character_id", None)
        if cid is None:
            continue
        name = (character_names or {}).get(int(cid), "")
        if not name or name not in other_character_names:
            continue
        content = getattr(msg, "content", "") or ""
        if name == character.name:
            own_history.append(content)
        else:
            foreign_replies.append(content)

    if not foreign_replies:
        return ""

    own_words = _content_words(f"{own_style} {example_messages}") | _content_words(
        " ".join(own_history)
    )
    foreign_words = _content_words(" ".join(foreign_replies))
    if not own_words or not foreign_words:
        return ""

    response_words = _content_words(text)
    if not response_words:
        return ""

    # Distinctive words: used by other characters, NOT used by this character
    # (in their style, examples, or own history), NOT global stopwords.
    borrowed = (response_words & foreign_words) - own_words

    # Require at least 3 borrowed distinctive words.
    if len(borrowed) < 3:
        return ""

    # Require them to make up a meaningful share of the response vocabulary.
    if len(borrowed) / len(response_words) < 0.2:
        return ""

    if own_style:
        return own_style
    return "свой характерный стиль"


async def generate(
    client: httpx.AsyncClient,
    chat_id: int,
    character,
    messages_history: list,
    general_prompt: str,
    memories: list,
    other_character_names: list[str],
    max_history_length: int = 30,
    model_name: str = "default",
    character_names: dict[int, str] | None = None,
    summary: str | None = None,
    viewer_character_id: int | None = None,
    presence_map: dict[int, Presence] | None = None,
    same_round_message_ids: set[int] | None = None,
    enable_thinking: bool | None = None,
    viewer_location: str | None = None,
    character_locations: dict[int, str] | None = None,
    prior_replies: list[tuple[str, str]] | None = None,
    scene_state: schemas.SceneStateRead | None = None,
    present_character_names: list[str] | None = None,
    stagnation_rounds: int = 0,
    is_isolated: bool = False,
    locations: str = "[]",
    location_descriptions: dict[str, str] | None = None,
    relationships_block: str = "",
    behavior_drivers_block: str = "",
    open_issues_block: str = "",
    built_context: schemas.BuiltContext | None = None,
    proactive_boost: float = 0.0,
    epistemic_mask_block: str = "",
    directive: str | None = None,
    recency_tail_block: str = "",
    what_you_know_block: str = "",
    story_block: str = "",
    active_goal_block: str = "",
    active_plan_block: str = "",
    crisis_block: str = "",
    world_state_block: str = "",
) -> AsyncIterator[dict]:
    """Send a request to Ollama and yield the sanitized response.

    Pipeline:
      generate → sanitize/role-isolation → repetition check → accept or targeted retry.

    Isolation retries and repetition retries are tracked separately.
    Streaming still completes fully before validation; only the final text is yielded.
    """
    stop = build_stop_sequences(other_character_names)
    temperature = _character_temperature(character)

    # Create witness-filtered history for vocabulary borrowing and repetition checks
    filtered_history = messages_history
    if (
        settings.enable_witness_filter
        and viewer_character_id is not None
        and character_names is not None
    ):
        filtered_history = filter_history_for_character_messages(
            messages_history,
            viewer_character_id,
            character_names,
            presence_map,
            same_round_ids=same_round_message_ids,
            viewer_location=viewer_location,
            character_locations=character_locations,
            max_replies_per_character=settings.max_replies_per_character,
            max_len=max_history_length,
        )

    isolation_attempt = 0
    repetition_attempt = 0
    borrowing_attempt = 0
    repetition_feedback = ""
    strict_isolation = False

    # WPE Фаза 5: Action<->Text Consistency Validator (Ул.1, §5). Стоик
    # contradiction-ретраев ≤1 (в рамках общего бюджета вызовов); молчаливое
    # действие (minor_ambiguity) НЕ вызывает retry (И16).
    consistency_attempt = 0
    consistency_feedback = ""

    # Isolation-valid candidates ranked for best-of on exhaustion
    candidates: list[tuple[str, RepetitionAnalysis | None]] = []

    # Bound total LLM calls: each retry type has its own budget + slack for the
    # fallback attempt. Counters are incremented ONLY for their own failure type,
    # so repetition/borrowing retries never consume the isolation budget.
    max_total_calls = (
        settings.max_role_isolation_retries
        + settings.max_repetition_retries
        + settings.max_borrowing_retries
        + settings.wpe_action_consistency_max_retries
        + 2
    )

    for call_idx in range(1, max_total_calls + 1):
        label = (
            f"call={call_idx} isolation={isolation_attempt}/"
            f"{settings.max_role_isolation_retries} rep={repetition_attempt}/"
            f"{settings.max_repetition_retries} borrow={borrowing_attempt}/"
            f"{settings.max_borrowing_retries}"
        )

        (
            _raw,
            sanitized,
            isolation_ok,
            _thinking_len,
            token_chunks,
            used_num_ctx,
            turn_output,
        ) = await _generate_once(
            client,
            chat_id=chat_id,
            character=character,
            messages_history=messages_history,
            general_prompt=general_prompt,
            memories=memories,
            other_character_names=other_character_names,
            max_history_length=max_history_length,
            model_name=model_name,
            character_names=character_names,
            summary=summary,
            viewer_character_id=viewer_character_id,
            presence_map=presence_map,
            same_round_message_ids=same_round_message_ids,
            enable_thinking=enable_thinking,
            viewer_location=viewer_location,
            character_locations=character_locations,
            stop=stop,
            temperature=temperature,
            strict_isolation=strict_isolation,
            repetition_feedback=repetition_feedback,
            attempt_label=label,
            prior_replies=prior_replies,
            scene_state=scene_state,
            present_character_names=present_character_names,
            stagnation_rounds=stagnation_rounds,
            is_isolated=is_isolated,
            locations=locations,
            location_descriptions=location_descriptions,
            relationships_block=relationships_block,
            behavior_drivers_block=behavior_drivers_block,
            open_issues_block=open_issues_block,
            built_context=built_context,
            proactive_boost=proactive_boost,
            epistemic_mask_block=epistemic_mask_block,
            directive=directive,
            recency_tail_block=recency_tail_block,
            consistency_feedback=consistency_feedback,
            what_you_know_block=what_you_know_block,
            story_block=story_block,
            active_goal_block=active_goal_block,
            active_plan_block=active_plan_block,
            crisis_block=crisis_block,
            world_state_block=world_state_block,
        )

        if not isolation_ok:
            isolation_attempt += 1
            if isolation_attempt < settings.max_role_isolation_retries:
                strict_isolation = True
                logger.warning(
                    "[chat_id=%d] Isolation failure for %s — retrying (%d/%d)",
                    chat_id,
                    character.name,
                    isolation_attempt,
                    settings.max_role_isolation_retries,
                )
                continue
            # Isolation budget exhausted → fallback path below
            break

        # --- isolation OK: vocabulary borrowing validation ---
        borrowing_issue = ""
        if settings.enable_vocabulary_control:
            borrowing_issue = _check_vocabulary_borrowing(
                sanitized, character, other_character_names, filtered_history,
                character_names,
            )
        if borrowing_issue:
            if borrowing_attempt < settings.max_borrowing_retries:
                borrowing_attempt += 1
                repetition_feedback = (
                    "ОБНАРУЖЕНО ЗАИМСТВОВАНИЕ СТИЛЯ.\n\n"
                    f"Твой ответ содержит слова и выражения, не характерные для {character.name}. "
                    f"{character.name} говорит так: {borrowing_issue}\n\n"
                    "Перепиши ответ строго в стиле своего персонажа. "
                    "Не используй лексикон других персонажей."
                )
                logger.warning(
                    "[chat_id=%d] Borrowing detected for %s — retry (%d/%d)",
                    chat_id, character.name, borrowing_attempt,
                    settings.max_borrowing_retries,
                )
                continue

        # --- isolation OK: repetition validation ---
        analysis: RepetitionAnalysis | None = None
        if settings.repetition_detection_enabled:
            analysis = analyze_response(
                sanitized,
                character_id=int(character.id),
                messages=filtered_history,
                character_names=character_names,
            )
            if analysis.is_repetitive:
                _log_repetition(
                    chat_id, character.name, analysis, repetition_attempt + 1
                )
                candidates.append((sanitized, analysis))
                if repetition_attempt < settings.max_repetition_retries:
                    repetition_attempt += 1
                    repetition_feedback = build_repetition_feedback(analysis)
                    # Keep isolation non-strict for pure repetition retries
                    # unless we already needed strict mode.
                    logger.warning(
                        "[chat_id=%d] Repetition failure for %s — targeted retry "
                        "(%d/%d) score=%.2f",
                        chat_id,
                        character.name,
                        repetition_attempt,
                        settings.max_repetition_retries,
                        analysis.score,
                    )
                    continue
                # Retries exhausted: pick best candidate below
                break

        # --- WPE Фаза 5: Action<->Text Consistency Validator (Ул.1, §5) ---
        # Действия извлекаются только из tool_calls/JSON-схемы (И4), не из
        # текста. contradiction -> ретрай ≤1 с фидбеком; minor_ambiguity
        # (молчаливое действие) НЕ ретраится (И16).
        if turn_output is not None and turn_output.actions:
            verdict = action_resolution.classify_consistency(turn_output, sanitized)
            if (
                verdict == "contradiction"
                and consistency_attempt < settings.wpe_action_consistency_max_retries
            ):
                consistency_attempt += 1
                consistency_feedback = action_resolution.build_consistency_feedback(
                    turn_output, sanitized, character.name
                )
                logger.warning(
                    "[chat_id=%d] Action/Text contradiction for %s — retry "
                    "(%d/%d) actions=%s",
                    chat_id,
                    character.name,
                    consistency_attempt,
                    settings.wpe_action_consistency_max_retries,
                    action_resolution.describe_actions(turn_output),
                )
                continue
        else:
            verdict = "no_actions"

        # Clean accept - yield token events first, then response
        if token_chunks:
            for chunk in token_chunks:
                yield {"type": "token", "text": chunk, "character_id": character.id}
                await asyncio.sleep(0.01)  # small delay for streaming feel
        yield {
            "type": "response",
            "text": sanitized,
            "turn": turn_output,
            "verdict": verdict,
        }
        return

    # Best isolation-valid candidate after repetition exhaustion
    if candidates:
        def _rank(item: tuple[str, RepetitionAnalysis | None]) -> tuple:
            text, ana = item
            if ana is None:
                return (0.0, -1.0, -len(text))
            bonus = settings.scene_twist_retry_bonus if stagnation_rounds >= settings.stagnation_max_rounds else 0.0
            return (ana.score, -(ana.progression_score + bonus), -len(text))

        best_text, best_ana = min(candidates, key=_rank)
        logger.warning(
            "[chat_id=%d] Accepting best candidate for %s after repetition limit "
            "(score=%s progression=%s)",
            chat_id,
            character.name,
            getattr(best_ana, "score", None),
            getattr(best_ana, "progression_score", None),
        )
        yield {
            "type": "response",
            "text": best_text,
            "turn": None,
            "verdict": "no_actions",
        }
        return

    if settings.fallback_on_isolation_failure:
        logger.warning(
            "[chat_id=%d] All isolation retries failed for %s — attempting "
            "full-context fallback with relaxed isolation",
            chat_id,
            character.name,
        )
        try:
            (
                _raw,
                sanitized,
                _fallback_isolation_ok,
                _thinking_len,
                fallback_token_chunks,
                _used_num_ctx,
                fallback_turn_output,
            ) = await _generate_once(
                client,
                chat_id=chat_id,
                character=character,
                messages_history=messages_history,
                general_prompt=general_prompt,
                memories=memories,
                other_character_names=other_character_names,
                max_history_length=max_history_length,
                model_name=model_name,
                character_names=character_names,
                summary=summary,
                viewer_character_id=viewer_character_id,
                presence_map=presence_map,
                same_round_message_ids=same_round_message_ids,
                enable_thinking=enable_thinking,
                viewer_location=viewer_location,
                character_locations=character_locations,
                stop=stop,
                temperature=0.6,
                strict_isolation=False,
                repetition_feedback=repetition_feedback,
                attempt_label=f"call={call_idx} fallback (relaxed)",
                prior_replies=prior_replies,
                scene_state=scene_state,
                present_character_names=present_character_names,
                stagnation_rounds=stagnation_rounds,
                is_isolated=is_isolated,
                locations=locations,
                location_descriptions=location_descriptions,
                relationships_block=relationships_block,
                behavior_drivers_block=behavior_drivers_block,
                open_issues_block=open_issues_block,
                built_context=built_context,
                proactive_boost=proactive_boost,
                epistemic_mask_block=epistemic_mask_block,
                directive=directive,
                recency_tail_block=recency_tail_block,
                consistency_feedback=consistency_feedback,
            )
            if sanitized:
                fallback_verdict = "no_actions"
                if (
                    fallback_turn_output is not None
                    and fallback_turn_output.actions
                ):
                    fallback_verdict = action_resolution.classify_consistency(
                        fallback_turn_output, sanitized
                    )
                # Last-resort output is accepted regardless, but log if the
                # repetition/borrowing guards still trip on it.
                if settings.repetition_detection_enabled:
                    fb_analysis = analyze_response(
                        sanitized,
                        character_id=int(character.id),
                        messages=filtered_history,
                        character_names=character_names,
                    )
                    if fb_analysis.is_repetitive:
                        logger.warning(
                            "[chat_id=%d] Fallback output for %s still flagged "
                            "repetitive (score=%.2f) — accepting",
                            chat_id, character.name, fb_analysis.score,
                        )
                if settings.enable_vocabulary_control:
                    fb_borrow = _check_vocabulary_borrowing(
                        sanitized, character, other_character_names,
                        filtered_history, character_names,
                    )
                    if fb_borrow:
                        logger.warning(
                            "[chat_id=%d] Fallback output for %s still flagged "
                            "as borrowing — accepting",
                            chat_id, character.name,
                        )
                logger.info(
                    "[chat_id=%d] Full-context fallback succeeded for %s",
                    chat_id,
                    character.name,
                )
                if fallback_token_chunks:
                    for chunk in fallback_token_chunks:
                        yield {
                            "type": "token",
                            "text": chunk,
                            "character_id": character.id,
                        }
                        await asyncio.sleep(0.01)
                yield {
                    "type": "response",
                    "text": sanitized,
                    "turn": fallback_turn_output,
                    "verdict": fallback_verdict,
                }
                return
        except Exception as exc:
            logger.warning(
                "[chat_id=%d] Fallback also failed for %s: %s",
                chat_id,
                character.name,
                exc,
            )

    raise RuntimeError(
        f"Не удалось получить изолированный ответ для персонажа {character.name}"
    )
