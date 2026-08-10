"""Основной streaming-пайплайн раунда (decomposition.md §4.2, Milestone 5B).

``process_user_message_streaming`` вынесено из ``app/chat_engine.py`` без
изменения поведения: SSE-события, присутствие, память, отношения, повторы,
ретраи, LoRA, story, WPE-фазы — всё на месте.

Внешний контракт: ``process_user_message_streaming`` использует единую
DB-транзакцию на раунд (batch commit) и отдаёт те же SSE-события, что и раньше.
"""

import json
import logging
from collections.abc import AsyncIterator

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from .. import action_resolution
from .. import crud
from .. import memory_service
from .. import ollama_client
from .. import pending_intervention
from .. import perception
from .. import round_engine
from .. import relationship_service
from .. import schemas
from .. import witness_model
from ..config import settings
from ..context_builder import ContextBuilder
from ..lora_manager import LoRAManager
from ..movement import detect_character_movement
from ..post_round_pipeline import compute_and_save_presence_for_message
from ..prompt_builder import build_world_state_block
from ..repetition_detector import analyze_response
from ..role_isolation import get_other_character_names
from ..stimuli import extract_stimuli

from .lora import lora_first_apply_warning, resolve_generation_model
from .session import (
    _character_is_isolated,
    _character_to_snapshot,
    _create_message_with_shadow,
    _detect_communication_channel,
    _directly_addressed_ids,
    _effective_prior_replies,
    _is_location_allowed,
    _load_location_descriptions,
    _log_generation_diagnostics,
    _message_snapshot,
    _message_to_dict,
    _parse_allowed_locations,
    _parse_known_locations,
    _scene_gate_confirms,
)
from .story import _chat_plot_text, _chat_story_block, _compute_epistemic_evidence

logger = logging.getLogger("app.chat_engine.pipeline.streaming")

async def process_user_message_streaming(
    client: httpx.AsyncClient,
    db: AsyncSession,
    chat_id: int,
    user_text: str,
    *,
    visibility: str | None = None,
    target_character_ids: list[int] | None = None,
    lora_manager: LoRAManager | None = None,
) -> AsyncIterator[dict]:
    """Process user message with per-character generation and perception filtering.
    
    Uses a single database transaction for the entire round (batch commit).
    If any character generation fails, the entire round is rolled back.

    ``lora_manager`` (Plans/LoRA.md Sprint 3): если передан, LoRA применяется
    ТОЛЬКО к основным вызовам ``ollama_client.generate`` в этом раунде;
    служебные вызовы (scene state, post_round_pipeline, память и т.д.)
    получают ``chat.model_name`` как раньше.
    """
    chat = await crud.get_chat(db, chat_id)
    if chat is None:
        raise ValueError("Чат не найден")

    # Sprint 3 (LoRA): модель для ОСНОВНОЙ генерации выбирается здесь — ДО
    # начала генерации. Ошибки LoRA (Incompatible, пропавший файл, битая
    # конфигурация, недоступный runtime) поднимаются раньше любого yield;
    # конфигурация чата не изменяется (§7). При lora_enabled=false возвращает
    # chat.model_name — поведение идентично текущему.
    generation_model_name, lora_info = await resolve_generation_model(
        db, client, chat, lora_manager
    )
    _lora_warning = lora_first_apply_warning(chat.id, lora_info)
    if _lora_warning is not None:
        yield _lora_warning

    history_limit = getattr(chat, "max_history_length", settings.default_history_length)
    enable_thinking = bool(getattr(chat, "thinking_mode", settings.enable_thinking))
    # WPE 3.0: canonical player location (Location id as identity). Resolving
    # here also heals any legacy string drift (chats.player_location vs player
    # character location) toward the canonical Location — defence-in-depth, no
    # second source of truth.
    canonical_location = await crud.resolve_player_location(db, chat_id)
    player_location = (
        canonical_location.name if canonical_location is not None
        else (getattr(chat, "player_location", "") or "")
    )
    player_location_id = canonical_location.id if canonical_location is not None else None
    chat_locations = getattr(chat, "locations", "") or "[]"
    location_descriptions = await _load_location_descriptions(db, chat_id)

    context_enabled = bool(settings.context_enabled)
    # Wide history window for retrieval when the Context Builder is active;
    # CONTEXT_HISTORY_LOAD_CAP is only a safety cap, not a semantic boundary.
    window_limit = (
        settings.context_history_load_cap if context_enabled else history_limit
    )
    pre_round_messages = await crud.get_messages_by_chat(db, chat_id, window_limit)

    event_visibility = visibility or settings.default_event_visibility
    event_targets = list(target_character_ids or [])

    # All characters (incl. player) are needed for stimuli extraction and
    # relationships; loaded before the user message is persisted (Sprint 3).
    all_characters = await crud.get_characters_by_chat(db, chat_id, include_player=True)
    character_names = {c.id: c.name for c in all_characters}  # includes player

    user_message = await _create_message_with_shadow(
        db,
        schemas.MessageCreate(
            chat_id=chat_id,
            role="user",
            content=user_text,
            visibility=event_visibility,
            location=player_location,
            location_id=player_location_id,
            target_character_ids=event_targets,
            stimuli=[
                s.to_dict()
                for s in extract_stimuli(user_text, list(character_names.values()))
            ],
        ),
    )

    # Stable round anchor (docs/relations.md §6, Sprint 1 item 9): one round_id
    # per turn, fixed once to the user-message id. utcnow() is never used for it.
    round_id = f"r{chat_id}-m{user_message.id}"

    # One-time user interventions ("Вмешательство") — read once as a snapshot
    # and consumed only after a fully successful round. Each intervention has a
    # frozen recipient set; the per-NPC directive text is computed below, before
    # any prompt is formed (docs/intervention.md).
    round_interventions = await pending_intervention.list_interventions(db, chat_id)
    round_generation_ok = True

    round_messages: list = [user_message]
    yield {"type": "message", "message": _message_to_dict(user_message)}

    # Load NPCs for generation, all characters (incl. player) for relationships
    characters = [c for c in all_characters if not c.is_player]  # NPCs only for generation

    if not characters:
        logger.warning("[chat_id=%d] No characters in chat", chat_id)
        return

    # Manual NPC toggle: только активные NPC участвуют в автоматической
    # генерации (sequential generation). `characters` остаётся ПОЛНЫМ списком
    # NPC — выключенный NPC по-прежнему существует в мире: его локация видна
    # в World State, он участвует в perception/presence, сохраняет память и
    # отношения, может быть целью взаимодействия (но не генерирует сам).
    active_characters = [c for c in characters if getattr(c, "is_active", True)]

    character_ids = [c.id for c in characters]  # NPCs only
    # Per-NPC directive text for this round: only the frozen recipients of each
    # pending intervention hear its instruction (filtering by character_id on
    # the backend, before the prompt is formed).
    directives_by_character = pending_intervention.build_directives_map(
        round_interventions, character_ids
    )
    character_locations = {
        c.id: getattr(c, "location", "") or "" for c in characters
    }
    character_location_ids = {
        c.id: getattr(c, "location_id", None) for c in characters
    }
    player_id = next(
        (c.id for c in all_characters if getattr(c, "is_player", False)),
        None,
    )
    if player_id is not None:
        player_obj = next(
            (c for c in all_characters if c.id == player_id), None
        )
        if player_obj is not None:
            character_locations[player_id] = getattr(player_obj, "location", "") or ""
            character_location_ids[player_id] = getattr(
                player_obj, "location_id", None
            )

    # Canonical name -> Location id lookup for the round (WPE 3.0). Used to
    # stamp messages with the canonical `location_id` even after in-round
    # movement (the in-memory character object's location_id may be stale).
    loc_id_by_name: dict[str, int] = {}
    _chat_locations_orm: list = []
    try:
        _chat_locations_orm = await crud.get_chat_locations(db, chat_id)
        loc_id_by_name = {
            perception.normalize_location(loc.name): loc.id
            for loc in _chat_locations_orm
        }
    except Exception as exc:  # noqa: BLE001 — локации не роняют раунд
        logger.warning("[chat_id=%d] Failed to load location ids: %s", chat_id, exc)

    # WORLD STATE block (Sprint 14): глобальный data-only блок с доступными
    # локациями (одна выборка locations на раунд) и расположением всех
    # персонажей (вкл. игрока) из живой in-memory карты раунда. Один блок на
    # раунд-шаг — общий для всех NPC; собирается прямо перед генерацией.
    world_state_block = ""
    if settings.world_state_enabled:
        try:
            world_state_block = build_world_state_block(
                [loc.name for loc in _chat_locations_orm],
                character_locations,
                character_names,
            )
        except Exception as exc:  # noqa: BLE001 — блок не роняет раунд
            logger.warning(
                "[chat_id=%d] Failed to build world state block: %s", chat_id, exc
            )
            world_state_block = ""

    def _resolve_loc_id(loc_name: str | None, fallback: int | None = None) -> int | None:
        if loc_name:
            found = loc_id_by_name.get(perception.normalize_location(loc_name))
            if found is not None:
                return found
        return fallback

    # Known location names for deterministic movement detection (Sprint 4, §9-§11).
    known_locations = _parse_known_locations(
        chat_locations, character_locations, player_location
    )
    # Adjacency index for audibility of events from neighboring locations (§6-§8).
    adjacency_index = await crud.get_adjacency_index(db, chat_id)
    # Locations confirmed by the deterministic detector during this round.
    # The post-round LLM scene extraction must not overwrite them (§12).
    detector_confirmed_locs: dict[str, str] = {}
    # cid -> location already announced as a system message this round.
    announced_movements: dict[int, str] = {}

    # Persist presence for the user event immediately (before any character reply)
    await compute_and_save_presence_for_message(
        db,
        user_message,
        characters,
        character_names,
    )

    if not active_characters:
        logger.warning("[chat_id=%d] No active NPCs in chat", chat_id)
        return

    # Get scene state for this round (P3) — needed for the retrieval query too
    scene_state = await crud.get_scene_state_with_presence(db, chat_id)

    # Phase 6: Extract current stagnation round count from scene state
    stagnation_rounds = 0
    if scene_state and scene_state.custom_state:
        stagnation_rounds = scene_state.custom_state.stagnation_rounds or 0

    # Build context for relevant memory selection (P1: BM25 ranking for fact accuracy)
    # Compact query: user message + recent dialogue tail + scene state (TZ §18).
    context_text = user_text
    if pre_round_messages:
        recent_context = pre_round_messages[-3:]
        context_text += "\n" + "\n".join(
            f"{getattr(m, 'role', 'unknown')}: {getattr(m, 'content', '')[:120]}"
            for m in recent_context
        )
    if scene_state is not None:
        if getattr(scene_state, "time_of_day", ""):
            context_text += f"\nВремя: {scene_state.time_of_day}"
        custom_state = getattr(scene_state, "custom_state", None)
        if isinstance(custom_state, str):
            try:
                custom_state = json.loads(custom_state)
            except (json.JSONDecodeError, TypeError):
                custom_state = None
        if isinstance(custom_state, dict):
            if custom_state.get("active_goal"):
                context_text += f"\nЦель: {custom_state['active_goal']}"
            if custom_state.get("mood"):
                context_text += f"\nАтмосфера: {custom_state['mood']}"
            tension = custom_state.get("tension", 0) or 0
            if tension > 0:
                context_text += f"\nНапряжение: {tension:.1f}"
    context_text = context_text[:600]

    summaries_by_character = await crud.get_summaries_for_characters(
        db,
        character_ids,
    )
    # Convert summaries to {id: content} for BM21 scoring augmentation
    summary_texts: dict[int, str] = {
        cid: s.content for cid, s in summaries_by_character.items() if s and s.content
    }

    # With the Context Builder active, retrieve a larger candidate set and let
    # the builder's token-aware selection pick the final memories per character.
    memory_top_k = (
        settings.context_retrieval_candidates
        if context_enabled
        else settings.memory_relevance_top_k
    )

    # Sprint 6 (§14): сигналы текущего контекста для rerank (отношения/threads).
    rerank_signals = {}
    if settings.hybrid_rerank_enabled:
        try:
            rerank_signals = await crud.build_rerank_signals(
                db, chat_id, character_ids, character_names
            )
        except Exception as exc:
            logger.warning(
                "[chat_id=%d] Failed to build rerank signals: %s", chat_id, exc
            )
            rerank_signals = {}

    # Hybrid retrieval: BM25 + Vector with RRF fusion (P3)
    if settings.embedding_enabled:
        memories_by_character = await crud.get_hybrid_memories_for_characters(
            db,
            character_ids,
            context_text,
            memory_top_k,
            character_summaries=summary_texts,
            rerank_signals=rerank_signals,
        )
    else:
        memories_by_character = await crud.get_relevant_memories_for_characters(
            db,
            character_ids,
            context_text,
            memory_top_k,
            character_summaries=summary_texts,
            rerank_signals=rerank_signals,
        )

    context_messages = list(pre_round_messages) + list(round_messages)
    if len(context_messages) > history_limit:
        context_messages = context_messages[-history_limit:]

    # Pre-compute dynamic relationships block for each NPC (includes player relationships)
    relationships_blocks: dict[int, str] = {}
    try:
        for c in characters:
            rel_block = await relationship_service.build_relationships_block(
                db, chat_id, c.id, c.name, character_names,
                max_events=settings.relationship_max_events_in_prompt,
            )
            relationships_blocks[c.id] = rel_block
    except Exception as exc:
        logger.warning("[chat_id=%d] Failed to build relationships block: %s", chat_id, exc)
        relationships_blocks = {c.id: "" for c in characters}

    # Top-K behavior drivers per NPC (deterministic tendencies, Sprint 1 п.3-4)
    drivers_blocks: dict[int, str] = {}
    try:
        for c in characters:
            drivers_blocks[c.id] = await relationship_service.build_behavior_drivers_block(
                db, chat_id, c.id, c.name, character_names,
            )
    except Exception as exc:
        logger.warning("[chat_id=%d] Failed to build behavior drivers block: %s", chat_id, exc)
        drivers_blocks = {c.id: "" for c in characters}

    # Open issues data block per NPC (Sprint 1 п.5-6, docs/relations.md §14)
    open_issues_blocks: dict[int, str] = {}
    try:
        for c in characters:
            open_issues_blocks[c.id] = await relationship_service.build_open_issues_block(
                db, chat_id, c.id, c.name, character_names,
            )
    except Exception as exc:
        logger.warning("[chat_id=%d] Failed to build open issues block: %s", chat_id, exc)
        open_issues_blocks = {c.id: "" for c in characters}

    # Weighted proactive boost per NPC (Sprint 1 п.7, docs/relations.md §7.4):
    # deterministic from open-issue importance & salience, raises the chance of
    # a proactive action during generation.
    proactive_boosts: dict[int, float] = {}
    try:
        for c in characters:
            proactive_boosts[c.id] = await relationship_service.compute_proactive_boost(
                db, chat_id, c.id,
            )
    except Exception as exc:
        logger.warning("[chat_id=%d] Failed to compute proactive boost: %s", chat_id, exc)
        proactive_boosts = {c.id: 0.0 for c in characters}

    # Crisis boost (Sprint 11, §19): активный кризис-поток мягко повышает шанс
    # proactive action вовлечённых персонажей (кризис = вероятность, не команда).
    crisis_boosts: dict[int, float] = {}
    if settings.crisis_engine_enabled:
        try:
            from ..plot import crisis_engine as crisis_engine_mod

            for c in characters:
                crisis_boosts[c.id] = await crisis_engine_mod.compute_crisis_boost(
                    db, chat_id, c,
                )
        except Exception as exc:
            logger.warning(
                "[chat_id=%d] Failed to compute crisis boost: %s", chat_id, exc
            )
            crisis_boosts = {c.id: 0.0 for c in characters}

    # Track prior replies in this round for anti-mimicry (P2).
    # We keep the underlying Message events (not just name/text) so that
    # availability of each reply can be decided per viewer via perception (§10).
    prior_reply_events: list = []

    # Reusable token-aware context builder (per-character contexts A ≠ B)
    context_builder = ContextBuilder()

    # WPE Фаза 7 (Ул.5, §7, И17): Event Bus / Interrupts. Пер-NPC шаг раунда
    # вынесен в `_round_step` (async-генератор); оркестрация цикла — очередь
    # приоритетов и буждение — делегируется round_engine (единственная
    # оркестрирующая функция, правило §9). Флаг off — run_round_fixed (откат
    # без изменения поведения: исходный фиксированный порядок). В очередь
    # попадают только активные NPC — выключенные не генерируют даже при
    # буждении/адресации.
    npc_id_set = {c.id for c in active_characters}

    async def _round_step(current_character, bus):
        nonlocal context_messages, round_generation_ok
        other_names = get_other_character_names(characters, current_character.id)
        summary_obj = summaries_by_character.get(current_character.id)
        summary_text = summary_obj.content if summary_obj else None

        history_message_ids = [
            message.id for message in context_messages if message.id is not None
        ]
        # Fresh presence map including events already saved this round
        presence_map = await crud.get_presence_map(
            db,
            history_message_ids,
            current_character.id,
        )
        attention_map = await crud.get_attention_map(
            db,
            history_message_ids,
            current_character.id,
        )

        _log_generation_diagnostics(
            character_id=current_character.id,
            character_name=current_character.name,
            character_locations=character_locations,
            player_location=player_location,
            player_name=character_names.get(player_id, "") if player_id else "",
            characters=characters,
            character_names=character_names,
            messages=context_messages,
            presence_map=presence_map,
        )

        # Effective prior replies for this viewer: availability of each reply
        # is decided by the same perception mechanism as ordinary history (§10).
        effective_prior_replies = _effective_prior_replies(
            prior_reply_events,
            current_character.id,
            character_locations.get(current_character.id, "") or "",
            current_character.name,
            character_names,
            adjacency_index=adjacency_index,
            viewer_location_id=getattr(current_character, "location_id", None),
        )

        # MVP epistemic mask (Sprint 2 item 10, docs/relations.md §10): a
        # character learns how another treats it only from this round's
        # direct/observed evidence, and only as an interpretation (no numbers).
        epistemic_mask_block = ""
        try:
            round_snapshots_now = [
                _message_snapshot(m) for m in round_messages
            ]
            evidenced = await _compute_epistemic_evidence(
                round_snapshots_now,
                current_character,
                all_characters,
                character_names,
                character_locations,
                player_id=player_id,
                db=db,
                chat_id=chat_id,
            )
            epistemic_mask_block = await relationship_service.build_epistemic_mask_block(
                db, chat_id, current_character.id, current_character.name,
                character_names, evidenced_target_ids=evidenced,
            )
        except Exception as exc:
            logger.warning(
                "[chat_id=%d] Failed to build epistemic mask block: %s",
                chat_id, exc,
            )
            epistemic_mask_block = ""

        # STORY block (Sprint 8, §16): сюжет чата (общий для всех персонажей).
        story_block = await _chat_story_block(db, chat)

        # CRISIS block (Sprint 11, §19): активные кризисные линии — «давление
        # в контексте» (общий для всех персонажей, data-only). Пусто при
        # выключенном crisis_engine_enabled; падение не роняет раунд.
        crisis_block = ""
        if settings.crisis_engine_enabled:
            try:
                from ..plot import crisis_engine as crisis_engine_mod

                crisis_block = await crisis_engine_mod.build_crisis_block(
                    db, chat.id
                )
            except Exception as exc:  # noqa: BLE001 — блок не роняет раунд
                logger.warning(
                    "[chat_id=%d] Failed to build crisis block: %s",
                    chat_id, exc,
                )

        # NPC Intent + Plans (Sprint 10, §21/§22): детерминированный intent
        # формируется ПЕРЕД генерацией (правила, без LLM) + долгоживущий план
        # NPC. Intent — тенденция, не команда (риск Sprint 10). Падение любой
        # части не роняет раунд (блоки остаются пустыми).
        character_state = None
        if settings.character_state_enabled:
            try:
                character_state = await crud.get_character_state(
                    db, current_character.id
                )
            except Exception as exc:  # noqa: BLE001 — state не роняет раунд
                logger.warning(
                    "[chat_id=%d] Failed to load character state for %s: %s",
                    chat_id, current_character.name, exc,
                )
        active_goal_block = ""
        active_plan_block = ""
        intent_data = None
        if settings.npc_intent_enabled:
            try:
                from ..plot import intent as plot_intent
                from ..prompt_builder import (
                    build_active_goal_block,
                )

                intent_data = await plot_intent.compute_intent_for_character(
                    db,
                    chat_id,
                    current_character,
                    round_id=round_id,
                    character_state=character_state,
                    character_names=character_names,
                )
                if intent_data:
                    active_goal_block = build_active_goal_block(intent_data)
            except Exception as exc:  # noqa: BLE001 — intent не роняет раунд
                logger.warning(
                    "[chat_id=%d] Failed to compute intent for %s: %s",
                    chat_id, current_character.name, exc,
                )
        if settings.npc_plans_enabled:
            try:
                from .. import npc_plans as npc_plans_mod

                goal_for_plan = (intent_data or {}).get("goal") or (
                    (character_state.active_goal if character_state is not None else "")
                    or ""
                )
                if goal_for_plan:
                    plan = await npc_plans_mod.get_or_create_active_plan(
                        db,
                        chat_id,
                        current_character.id,
                        goal_for_plan,
                        next_step="",
                        priority=5,
                        round_id=round_id,
                    )
                    if plan is not None:
                        active_plan_block = npc_plans_mod.build_active_plan_block(plan)
            except Exception as exc:  # noqa: BLE001 — план не роняет раунд
                logger.warning(
                    "[chat_id=%d] Failed to build active plan for %s: %s",
                    chat_id, current_character.name, exc,
                )

        # Assemble token-aware context within the budget (TZ §11–§21)
        built_context = None
        if context_enabled:
            max_tokens = (
                getattr(chat, "max_context_tokens", None)
                or settings.max_context_tokens
            )
            built_context = await context_builder.build(
                db=db,
                chat_id=chat_id,
                character=current_character,
                user_message=user_text,
                general_prompt=_chat_plot_text(chat),
                messages_window=pre_round_messages,
                round_messages=round_messages,
                character_names=character_names,
                character_locations=character_locations,
                character_appearances={
                    c.name: c.appearance or "" for c in all_characters
                },
                summary=summary_text,
                summary_through_message_id=(
                    getattr(summary_obj, "through_message_id", None)
                    if summary_obj is not None
                    else None
                ),
                memories=memories_by_character.get(current_character.id, []),
                scene_state=scene_state,
                present_character_names=None,
                relationships_block=relationships_blocks.get(
                    current_character.id, ""
                ),
                locations=chat_locations,
                location_descriptions=location_descriptions,
                stagnation_rounds=stagnation_rounds,
                viewer_location=character_locations.get(
                    current_character.id, ""
                ),
                prior_replies=effective_prior_replies,
                is_isolated=_character_is_isolated(
                    character_locations,
                    current_character.id,
                    characters,
                    player_location,
                ),
                max_tokens=max_tokens,
                character_state=character_state,
                story_block=story_block,
                active_goal_block=active_goal_block,
                active_plan_block=active_plan_block,
                crisis_block=crisis_block,
                world_state_block=world_state_block,
            )

        response_text = ""
        turn_output = None
        consistency_verdict = "no_actions"
        try:
            async for event in ollama_client.generate(
                client=client,
                chat_id=chat.id,
                character=current_character,
                messages_history=context_messages,
                general_prompt=_chat_plot_text(chat),
                memories=memories_by_character.get(current_character.id, []),
                other_character_names=other_names,
                max_history_length=history_limit,
                model_name=generation_model_name,
                character_names=character_names,
                summary=summary_text,
                viewer_character_id=current_character.id,
                presence_map=presence_map,
                same_round_message_ids=None,
                enable_thinking=enable_thinking,
                viewer_location=character_locations.get(current_character.id, ""),
                character_locations=character_locations,
                prior_replies=effective_prior_replies,
                scene_state=scene_state,
                present_character_names=None,
                stagnation_rounds=stagnation_rounds,
                is_isolated=_character_is_isolated(
                    character_locations,
                    current_character.id,
                    characters,
                    player_location,
                ),
                locations=chat_locations,
                location_descriptions=location_descriptions,
                relationships_block=relationships_blocks.get(current_character.id, ""),
                behavior_drivers_block=drivers_blocks.get(current_character.id, ""),
                open_issues_block=open_issues_blocks.get(current_character.id, ""),
                proactive_boost=(
                    proactive_boosts.get(current_character.id, 0.0)
                    + crisis_boosts.get(current_character.id, 0.0)
                ),
                built_context=built_context,
                epistemic_mask_block=epistemic_mask_block,
                directive=directives_by_character.get(current_character.id),
                story_block=story_block,
                active_goal_block=active_goal_block,
                active_plan_block=active_plan_block,
                crisis_block=crisis_block,
                world_state_block=world_state_block,
                recency_tail_block=(
                    built_context.recency_tail_text
                    if built_context is not None
                    else witness_model.build_character_recency_tail(
                        round_messages,
                        current_character.id,
                        character_names,
                        player_id=player_id,
                        attention_map=attention_map,
                    )
                ),
            ):
                if event["type"] == "token":
                    # Forward token to SSE with character_id for frontend avatar
                    yield {"type": "token", "text": event["text"], "character_id": current_character.id}
                elif event["type"] == "response":
                    response_text = event["text"]
                    turn_output = event.get("turn")
                    consistency_verdict = event.get("verdict", "no_actions")
        except RuntimeError as exc:
            logger.error(
                "[chat_id=%d] Generation failed for %s: %s",
                chat_id,
                current_character.name,
                exc,
            )
            response_text = f"*[{current_character.name} молчит, не в силах ответить]*"
            round_generation_ok = False

        # WPE Фаза 5: Action Resolution (Ул.1, §5). Действия извлекаются только
        # из tool_calls/JSON-схемы (И4); regex-канал движения понижен до
        # safety-net — при включённом флаге источник истины — actions.
        actions_active = (
            settings.world_engine_actions_enabled
            and turn_output is not None
            and bool(turn_output.actions)
        )
        if actions_active:
            if consistency_verdict == "contradiction":
                # contradiction: ретрай (≤1) уже был внутри generate(); действия
                # отклоняются, текст остаётся, инцидент логируется, ремарка
                # Narrator фиксирует решение движка (§5.2).
                logger.warning(
                    "[WPE-P5] chat_id=%d contradiction unresolved for %s, "
                    "actions rejected",
                    chat_id,
                    current_character.name,
                )
                applied = crud.ApplyActionsResult()
                rejected = [
                    {"action_index": index, "type": action.type}
                    for index, action in enumerate(turn_output.actions)
                ]
            else:
                applied = await crud.apply_character_actions(
                    db,
                    chat_id,
                    current_character,
                    turn_output,
                    round_id=round_id,
                )
                for mv in applied.applied_moves:
                    if mv.get("changed"):
                        character_locations[current_character.id] = mv["location_to"]
                        current_character.location = mv["location_to"]
                        announced_movements[current_character.id] = mv["location_to"]
                        detector_confirmed_locs[current_character.name] = mv[
                            "location_to"
                        ]
                if applied.rejected:
                    logger.info(
                        "[WPE-P5] chat_id=%d rejected actions for %s: %s",
                        chat_id,
                        current_character.name,
                        applied.rejected,
                    )
                rejected = applied.rejected

            # System Narrator (И16, §5.10): для каждого применённого действия,
            # НЕ отражённого в тексте реплики, движок сам вставляет служебную
            # ремарку role=system по детерминированному шаблону из WorldEvent.
            # Текст реплики при этом не редактируется.
            reflected = action_resolution.reflected_action_indices(
                turn_output,
                response_text,
                tuple(known_locations),
            )
            remarks = action_resolution.build_narrator_remarks(
                current_character.name,
                turn_output,
                consistency_verdict,
                applied.applied_moves,
                applied.applied_messages,
                reflected,
                rejected=rejected,
            )
            for remark in remarks:
                remark_message = await _create_message_with_shadow(
                    db,
                    schemas.MessageCreate(
                        chat_id=chat_id,
                        role="system",
                        content=remark,
                        visibility="global",
                    ),
                    round_id=round_id,
                )
                yield {"type": "message", "message": _message_to_dict(remark_message)}
                round_messages.append(remark_message)
                context_messages.append(remark_message)
                if len(context_messages) > history_limit:
                    context_messages = context_messages[-history_limit:]
        else:
            # WPE Фаза 8: regex-детектор — только legacy-safety-net (И14),
            # НЕ источник истины (источник — `turn.actions` из tools/format).
            # Активен только когда Action Resolution не применил действия.
            if settings.world_engine_actions_enabled:
                logger.warning(
                    "[WPE-P8] chat_id=%d regex movement safety-net for %s: "
                    "структурированные действия не извлечены (text-only путь "
                    "deprecated, Фаза 8)",
                    chat_id,
                    current_character.name,
                )
            else:
                logger.debug(
                    "[WPE-P8] regex movement safety-net (actions off) for %s",
                    current_character.name,
                )
            movement_target = detect_character_movement(
                response_text,
                current_character.name,
                known_locations,
                character_locations,
                character_names,
            )
            if movement_target and movement_target != character_locations.get(
                current_character.id, ""
            ):
                await crud.update_character_locations_batch(
                    db, chat_id, {current_character.id: movement_target}
                )
                character_locations[current_character.id] = movement_target
                current_character.location = movement_target
                detector_confirmed_locs[current_character.name] = movement_target

                loc_msg_text = f"*{current_character.name} переместился в {movement_target}*"
                loc_message = await _create_message_with_shadow(
                    db,
                    schemas.MessageCreate(
                        chat_id=chat_id,
                        role="system",
                        content=loc_msg_text,
                        visibility="global",
                    ),
                    round_id=round_id,
                )
                yield {"type": "message", "message": _message_to_dict(loc_message)}
                round_messages.append(loc_message)
                context_messages.append(loc_message)
                if len(context_messages) > history_limit:
                    context_messages = context_messages[-history_limit:]
                announced_movements[current_character.id] = movement_target

        char_location = character_locations.get(current_character.id, "") or ""
        # Remote channel: источник истины — `send_message` action (И14); regex
        # `_detect_communication_channel` — только legacy-safety-net (Фаза 8),
        # не источник истины, активен лишь когда actions не применились.
        if actions_active and applied.applied_messages:
            msg_channel = (
                applied.applied_messages[0].get("channel") or "direct"
            ).strip().lower()
            msg_targets = list(
                applied.applied_messages[0].get("target_character_ids") or []
            )
            # Isolation hardening: a remote `send_message` channel/targets from
            # the model are trusted ONLY if the text actually DIRECTLY addresses
            # one of the targets by name (vocative). Models routinely attach
            # magic/phone/messenger to ordinary in-person dialogue or narration;
            # storing that verbatim lets the remote-channel perception bridge
            # bypass location isolation (chat 9 leak). If nobody is addressed,
            # the speech is in-person → direct.
            if msg_channel != "direct":
                addressed = _directly_addressed_ids(
                    response_text, current_character.name, character_names
                )
                if not any(t in msg_targets for t in addressed):
                    msg_channel = "direct"
                    msg_targets = []
        else:
            if settings.world_engine_actions_enabled:
                logger.warning(
                    "[WPE-P8] chat_id=%d regex channel safety-net for %s "
                    "(text-only путь deprecated, Фаза 8)",
                    chat_id,
                    current_character.name,
                )
            msg_channel, msg_targets = _detect_communication_channel(
                response_text, current_character.name, character_names
            )
        msg_visibility = settings.default_event_visibility
        if msg_channel != "direct" and msg_targets:
            msg_visibility = "targeted"
        char_message = await _create_message_with_shadow(
            db,
            schemas.MessageCreate(
                chat_id=chat_id,
                character_id=current_character.id,
                role="character",
                content=response_text,
                visibility=msg_visibility,
                location=char_location,
                location_id=_resolve_loc_id(
                    char_location, character_location_ids.get(current_character.id)
                ),
                target_character_ids=msg_targets,
                channel=msg_channel,
                stimuli=[
                    s.to_dict()
                    for s in extract_stimuli(
                        response_text, list(character_names.values())
                    )
                ],
            ),
            round_id=round_id,
        )
        # Perception for this reply before the next character generates
        await compute_and_save_presence_for_message(
            db,
            char_message,
            characters,
            character_names,
        )

        # Track this reply's event for subsequent characters. Availability for
        # each subsequent viewer is decided later via perception (§10).
        if response_text:
            prior_reply_events.append(char_message)

        # WPE Фаза 7 (Event Bus, Ул.5, И17): реплика адресована конкретному
        # NPC (target_character_ids) → будим его для внеочередной генерации.
        # Повторные буждения и буждения уже ответивших игнорируются EventBus.
        if bus is not None:
            for _target_id in msg_targets:
                if _target_id in npc_id_set:
                    bus.wake(_target_id)

        round_messages.append(char_message)
        context_messages.append(char_message)
        if len(context_messages) > history_limit:
            context_messages = context_messages[-history_limit:]
        yield {"type": "message", "message": _message_to_dict(char_message)}

    # WPE Фаза 7 (Ул.5, §7): цикл раунда делегируется round_engine — единственной
    # оркестрирующей функции (правило §9). Шаг `_round_step` сам будит
    # NPC-адресатов своей реплики (NPC↔NPC, И17); игрок→NPC будятся первым
    # ходом через seed_target_ids. Флаг off — исходный фиксированный порядок.
    if settings.world_engine_event_bus_enabled:
        _round_iterator = round_engine.run_round(
            active_characters, _round_step, seed_target_ids=event_targets
        )
    else:
        _round_iterator = round_engine.run_round_fixed(active_characters, _round_step)
    async for _round_event in _round_iterator:
        yield _round_event

    character_snapshots = [_character_to_snapshot(c) for c in characters]
    round_snapshots = [_message_to_dict(m) for m in round_messages]

    # Extract and update scene state from round history (P3)
    scene_update = None
    try:
        round_history_text = "\n".join(
            f"{m.character.name if m.character else 'Игрок'}: {m.content}"
            for m in round_messages
        )
        scene_update = await ollama_client.extract_scene_state(
            client=client,
            model_name=chat.model_name,
            round_history_text=round_history_text,
            current_scene_state=scene_state,
            character_names=character_names,
            locations=chat_locations,
        )
        if scene_update:
            # Save location-related data first (without character_locations — confirmed later).
            # time_of_day is intentionally excluded: the engine never changes the time of day
            # automatically — it is set only by the user via PATCH /chats/{chat_id}/scene.
            scene_update_no_locs = {
                k: v
                for k, v in scene_update.items()
                if k not in ("character_locations", "time_of_day")
            }
            if scene_update_no_locs:
                await crud.upsert_scene_state(db, chat_id, schemas.SceneStateUpdate(**scene_update_no_locs))

            # Update per-character locations from scene extraction
            confirmed_locs: dict[str, str] = {}
            char_locs_by_name = scene_update.get("character_locations", {})
            if char_locs_by_name:
                name_to_id = {name: cid for cid, name in character_names.items()}
                loc_updates: dict[int, str] = {}
                allowed_locs = _parse_allowed_locations(chat_locations)
                old_locs = scene_state.character_locations if scene_state else {}
                for cname, loc in char_locs_by_name.items():
                    if cname in detector_confirmed_locs:
                        # Deterministically confirmed movement this round has
                        # priority; the LLM extraction must not overwrite it (§12).
                        continue
                    if cname in name_to_id and loc.strip():
                        new_loc = loc.strip()
                        cid = name_to_id[cname]
                        old_loc = old_locs.get(cname, character_locations.get(cid, ""))
                        if not _is_location_allowed(new_loc, allowed_locs):
                            logger.info(
                                "[chat_id=%d] Ignoring disallowed location '%s' for %s",
                                chat_id, new_loc, cname,
                            )
                        elif (
                            _scene_gate_confirms(
                                round_messages,
                                cid,
                                cname,
                                new_loc,
                                known_locations,
                                character_locations,
                                character_names,
                            )
                        ):
                            loc_updates[cid] = new_loc
                            confirmed_locs[cname] = new_loc
                        else:
                            logger.info(
                                "[chat_id=%d] No movement detected for %s, keeping '%s'",
                                chat_id, cname, old_loc,
                            )
                if loc_updates:
                    await crud.update_character_locations_batch(db, chat_id, loc_updates)
                    for cid, loc in loc_updates.items():
                        character_locations[cid] = loc

            # Save only the confirmed character_locations to scene state
            if confirmed_locs:
                await crud.upsert_scene_state(
                    db, chat_id,
                    schemas.SceneStateUpdate(character_locations=confirmed_locs),
                )

            # Announce location changes as system messages (Part B1).
            # Moves already announced by the deterministic detector during the
            # round are skipped here to avoid duplicates (§12).
            old_locations = scene_state.character_locations if scene_state else {}
            name_to_id_rev = {cid: name for cid, name in character_names.items()}
            for cid, new_loc in character_locations.items():
                if cid in announced_movements:
                    continue
                cname = name_to_id_rev.get(cid, "")
                if not cname:
                    continue
                old_loc = old_locations.get(cname, "")
                if old_loc and new_loc and old_loc != new_loc:
                    loc_msg_text = f"*{cname} переместился в {new_loc}*"
                    loc_message = await _create_message_with_shadow(
                        db,
                        schemas.MessageCreate(
                            chat_id=chat_id,
                            role="system",
                            content=loc_msg_text,
                            visibility="global",
                        ),
                        round_id=round_id,
                    )
                    yield {"type": "message", "message": _message_to_dict(loc_message)}
                    round_messages.append(loc_message)
                    context_messages.append(loc_message)
                    if len(context_messages) > history_limit:
                        context_messages = context_messages[-history_limit:]
    except Exception as exc:
        logger.warning("[chat_id=%d] Scene state extraction failed: %s", chat_id, exc)

    # Relationship analysis hook (non-blocking, background)
    if settings.relationship_analyzer_enabled:
        round_snapshots = [
            {
                "id": getattr(m, "id", None),
                "role": getattr(m, "role", ""),
                "character_id": getattr(m, "character_id", None),
                "content": getattr(m, "content", "") or "",
                "location": getattr(m, "location", "") or "",
                "visibility": (
                    getattr(m, "visibility", None)
                    or settings.default_event_visibility
                ),
                "channel": getattr(m, "channel", None) or "direct",
                "target_character_ids": getattr(m, "target_character_ids", "[]"),
            }
            for m in round_messages
        ]
        character_snapshots = [
            {
                "id": c.id,
                "name": c.name,
                "location": getattr(c, "location", "") or "",
            }
            for c in characters
        ]
        # Scheduling of the relationship task moved into post_round_pipeline
        # (Sprint 1, Plans/update20.md §15) — it uses these rich snapshots.

    # Phase 6: Track stagnation across rounds and force time advance
    try:
        # Use pre-round custom_state (LLM no longer returns custom_state)
        if scene_state and scene_state.custom_state:
            custom_state_raw = scene_state.custom_state.model_dump()
        else:
            custom_state_raw = {}
        stagnation_rounds = int(custom_state_raw.get("stagnation_rounds", 0))
        round_count = int(custom_state_raw.get("round_count", 0)) + 1

        # Detect stagnation from character responses this round
        char_responses = [m for m in round_messages if getattr(m, "role", "") == "character"]
        if char_responses:
            any_stagnant = False
            for m in char_responses:
                cid = getattr(m, "character_id", None)
                if cid is None:
                    continue
                analysis = analyze_response(
                    getattr(m, "content", ""),
                    character_id=cid,
                    messages=pre_round_messages,
                    character_names=character_names,
                )
                if analysis.stagnation:
                    any_stagnant = True
                    break
            if any_stagnant:
                stagnation_rounds += 1
            else:
                stagnation_rounds = 0
        else:
            stagnation_rounds = 0

        # Track stagnation and round count only (no automatic time advance).
        # NOTE: time of day is intentionally NOT advanced automatically — the engine
        # only tracks stagnation/round_count. The user sets the time themselves via
        # PATCH /chats/{chat_id}/scene.
        custom_state_raw["stagnation_rounds"] = stagnation_rounds
        custom_state_raw["round_count"] = round_count
        custom_state_update = schemas.SceneCustomState(**custom_state_raw)

        await crud.upsert_scene_state(
            db, chat_id, schemas.SceneStateUpdate(custom_state=custom_state_update)
        )
    except Exception as exc:
        logger.warning("[chat_id=%d] Stagnation tracking failed: %s", chat_id, exc)

    # Post-round pipeline (Sprint 1, Plans/update20.md §15): presence → event
    # extraction → memory → relationships → story. Каждая стадия изолирована
    # try/except — падение одной не ломает раунд (graceful degradation).
    try:
        from .relations import _analyze_and_update_relationships
        from ..post_round_pipeline import run_post_round_pipeline

        _pipeline_report = await run_post_round_pipeline(
            client=client,
            db=db,
            chat_id=chat_id,
            model_name=chat.model_name,
            round_messages=round_messages,
            character_ids=character_ids,
            character_names=character_names,
            characters=characters,
            character_locations=character_locations,
            round_id=round_id,
            round_snapshots=round_snapshots,
            character_snapshots=character_snapshots,
            memory_processor=memory_service.process_post_round,
            relationship_analyzer=_analyze_and_update_relationships,
        )
        try:
            from ..routers.debug import remember_pipeline_report

            remember_pipeline_report(chat_id, _pipeline_report)
        except Exception:
            pass
    except Exception as exc:
        logger.warning(
            "[chat_id=%d] Post-round pipeline failed: %s", chat_id, exc
        )

    # Consume the one-time interventions after a fully successful round. If a
    # character fell back to the "молчит" placeholder, the round is considered
    # failed and the instructions are preserved for a retry (docs/intervention.md).
    # Each intervention is consumed by id from the round snapshot, so an
    # intervention set while the round was generating survives.
    if round_interventions and round_generation_ok:
        for _ri in round_interventions:
            try:
                await pending_intervention.record_intervention_outcome(
                    db,
                    chat_id,
                    _ri.instruction,
                    _ri.recipient_ids,
                )
            except Exception:
                logger.warning(
                    "[chat_id=%d] Failed to persist intervention outcome", chat_id
                )
            await pending_intervention.consume_intervention(db, _ri.id)
