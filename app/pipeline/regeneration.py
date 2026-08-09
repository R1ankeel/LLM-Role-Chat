"""Регенерация ответа персонажа (decomposition.md §4.2, Milestone 5B).

``regenerate_message_streaming`` вынесено из ``app/chat_engine.py`` без
изменения поведения: WPE-фазы, присутствие, память, LoRA, story — всё на месте.
"""

import json
import logging
from collections.abc import AsyncIterator

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from .. import action_resolution
from .. import crud
from .. import models
from .. import ollama_client
from .. import pending_intervention
from .. import perception
from .. import relationship_service
from .. import schemas
from .. import witness_model
from ..config import settings
from ..context_builder import ContextBuilder
from ..lora_manager import LoRAManager
from ..movement import detect_character_movement
from ..post_round_pipeline import compute_and_save_presence_for_message
from ..prompt_builder import build_world_state_block
from ..role_isolation import get_other_character_names
from ..stimuli import extract_stimuli

from .lora import lora_first_apply_warning, resolve_generation_model
from .session import (
    _character_is_isolated,
    _create_message_with_shadow,
    _detect_communication_channel,
    _load_location_descriptions,
    _log_generation_diagnostics,
    _message_snapshot,
    _message_to_dict,
    _parse_known_locations,
)
from .story import _chat_plot_text, _chat_story_block, _compute_epistemic_evidence

logger = logging.getLogger("app.chat_engine.pipeline.regeneration")

async def regenerate_message_streaming(
    client: httpx.AsyncClient,
    db: AsyncSession,
    chat_id: int,
    message_id: int,
    *,
    lora_manager: LoRAManager | None = None,
) -> AsyncIterator[dict]:
    """Regenerate a single character reply with the context of its round.

    Only the *last* message of a character in the *last* round can be
    regenerated. The old reply stays in the DB until the new one is saved,
    so a failed generation never loses the previous text.

    Yields SSE-like dict events:
      {"type": "token", "text": "...", "character_id": N}
      {"type": "message", "message": MessageRead}
    Raises ValueError for invalid input, RuntimeError on generation failure.

    ``lora_manager`` (Plans/LoRA.md Sprint 3): если передан, LoRA применяется
    к основному вызову ``ollama_client.generate`` в этом пути перегенерации.
    """
    target = await db.get(models.Message, message_id)
    if target is None or target.chat_id != chat_id:
        raise ValueError("Сообщение не найдено")
    if target.role != "character":
        raise ValueError("Перегенерировать можно только ответ персонажа")

    chat = await crud.get_chat(db, chat_id)
    if chat is None:
        raise ValueError("Чат не найден")

    # Sprint 3 (LoRA): модель для ОСНОВНОЙ генерации выбирается ДО начала
    # генерации; ошибки LoRA поднимаются раньше любого yield, конфигурация
    # чата не изменяется (§7). При lora_enabled=false — chat.model_name.
    generation_model_name, lora_info = await resolve_generation_model(
        db, client, chat, lora_manager
    )
    _lora_warning = lora_first_apply_warning(chat.id, lora_info)
    if _lora_warning is not None:
        yield _lora_warning

    messages = await crud.get_messages_by_chat(db, chat_id)
    idx = next((i for i, m in enumerate(messages) if m.id == message_id), None)
    if idx is None:
        raise ValueError("Сообщение не найдено")

    # Only the character's last message may be regenerated
    for m in messages[idx + 1:]:
        if m.character_id == target.character_id:
            raise ValueError(
                "Можно перегенерировать только последнее сообщение персонажа"
            )
        if m.role == "user":
            raise ValueError(
                "Можно перегенерировать только сообщение из последнего раунда"
            )

    # The player message that started the round
    user_idx = None
    for i in range(idx - 1, -1, -1):
        if messages[i].role == "user":
            user_idx = i
            break
    if user_idx is None:
        raise ValueError("Сообщение не относится к раунду")

    user_message = messages[user_idx]
    pre_round_messages = messages[:user_idx]
    round_messages = messages[user_idx:idx]

    history_limit = getattr(chat, "max_history_length", settings.default_history_length)
    context_messages = list(pre_round_messages) + list(round_messages)
    if len(context_messages) > history_limit:
        context_messages = context_messages[-history_limit:]

    character = await crud.get_character(db, target.character_id)
    if character is None:
        raise ValueError("Персонаж не найден")

    all_characters = await crud.get_characters_by_chat(db, chat_id, include_player=True)
    characters = [c for c in all_characters if not c.is_player]
    character_names = {c.id: c.name for c in all_characters}
    char_by_id = {c.id: c for c in all_characters}
    character_locations = {
        c.id: getattr(c, "location", "") or "" for c in characters
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
    player_location = getattr(chat, "player_location", "") or ""
    chat_locations = getattr(chat, "locations", "") or "[]"
    location_descriptions = await _load_location_descriptions(db, chat_id)
    known_locations = _parse_known_locations(
        chat_locations, character_locations, player_location
    )

    # WORLD STATE block (Sprint 14): глобальный data-only блок с доступными
    # локациями (одна выборка locations) и расположением всех персонажей
    # (вкл. игрока) из живой карты раунда. Собирается прямо перед генерацией.
    world_state_block = ""
    if settings.world_state_enabled:
        try:
            _locations_orm = await crud.get_chat_locations(db, chat_id)
            world_state_block = build_world_state_block(
                [loc.name for loc in _locations_orm],
                character_locations,
                character_names,
            )
        except Exception as exc:  # noqa: BLE001 — блок не роняет перегенерацию
            logger.warning(
                "[chat_id=%d] Failed to build world state block: %s", chat_id, exc
            )
            world_state_block = ""

    scene_state = await crud.get_scene_state_with_presence(db, chat_id)
    stagnation_rounds = 0
    if scene_state and scene_state.custom_state:
        stagnation_rounds = scene_state.custom_state.stagnation_rounds or 0

    # Memory retrieval for this character (mirrors the main round path)
    context_text = user_message.content or ""
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

    summary_obj = await crud.get_character_summary(db, character.id)
    summary_text = summary_obj.content if summary_obj else None

    context_enabled = bool(settings.context_enabled)
    memory_top_k = (
        settings.context_retrieval_candidates
        if context_enabled
        else settings.memory_relevance_top_k
    )
    summary_texts: dict[int, str] = {character.id: summary_text} if summary_text else {}

    # Sprint 6 (§14): сигналы текущего контекста для rerank (отношения/threads).
    rerank_signals = {}
    if settings.hybrid_rerank_enabled:
        try:
            rerank_signals = await crud.build_rerank_signals(
                db, chat_id, [character.id], character_names
            )
        except Exception as exc:
            logger.warning(
                "[chat_id=%d] Failed to build rerank signals: %s", chat_id, exc
            )
            rerank_signals = {}

    if settings.embedding_enabled:
        memories = (
            await crud.get_hybrid_memories_for_characters(
                db,
                [character.id],
                context_text,
                memory_top_k,
                character_summaries=summary_texts,
                rerank_signals=rerank_signals,
            )
        ).get(character.id, [])
    else:
        memories = (
            await crud.get_relevant_memories_for_characters(
                db,
                [character.id],
                context_text,
                memory_top_k,
                character_summaries=summary_texts,
                rerank_signals=rerank_signals,
            )
        ).get(character.id, [])

    # Relationships block for this character
    relationships_block = ""
    try:
        relationships_block = await relationship_service.build_relationships_block(
            db, chat_id, character.id, character.name, character_names,
            max_events=settings.relationship_max_events_in_prompt,
        )
    except Exception as exc:
        logger.warning("[chat_id=%d] Failed to build relationships block: %s", chat_id, exc)

    # Behavior drivers block for this character (Sprint 1 п.3-4)
    drivers_block = ""
    try:
        drivers_block = await relationship_service.build_behavior_drivers_block(
            db, chat_id, character.id, character.name, character_names,
        )
    except Exception as exc:
        logger.warning("[chat_id=%d] Failed to build behavior drivers block: %s", chat_id, exc)

    # Open issues data block for this character (Sprint 1 п.5-6)
    open_issues_block = ""
    try:
        open_issues_block = await relationship_service.build_open_issues_block(
            db, chat_id, character.id, character.name, character_names,
        )
    except Exception as exc:
        logger.warning("[chat_id=%d] Failed to build open issues block: %s", chat_id, exc)

    # Weighted proactive boost for this character (Sprint 1 п.7, §7.4)
    proactive_boost = 0.0
    try:
        proactive_boost = await relationship_service.compute_proactive_boost(
            db, chat_id, character.id,
        )
    except Exception as exc:
        logger.warning("[chat_id=%d] Failed to compute proactive boost: %s", chat_id, exc)

    # MVP epistemic mask (Sprint 2 item 10, docs/relations.md §10)
    epistemic_mask_block = ""
    try:
        round_snapshots_now = [_message_snapshot(m) for m in round_messages]
        evidenced = await _compute_epistemic_evidence(
            round_snapshots_now,
            character,
            all_characters,
            character_names,
            character_locations,
            player_id=player_id,
            db=db,
            chat_id=chat_id,
        )
        epistemic_mask_block = await relationship_service.build_epistemic_mask_block(
            db, chat_id, character.id, character.name,
            character_names, evidenced_target_ids=evidenced,
        )
    except Exception as exc:
        logger.warning(
            "[chat_id=%d] Failed to build epistemic mask block: %s", chat_id, exc,
        )
        epistemic_mask_block = ""

    # Presence & prior replies visible to this character
    history_message_ids = [m.id for m in context_messages if m.id is not None]
    presence_map = await crud.get_presence_map(db, history_message_ids, character.id)
    attention_map = await crud.get_attention_map(db, history_message_ids, character.id)

    _log_generation_diagnostics(
        character_id=character.id,
        character_name=character.name,
        character_locations=character_locations,
        player_location=player_location,
        player_name=character_names.get(player_id, "") if player_id else "",
        characters=characters,
        character_names=character_names,
        messages=context_messages,
        presence_map=presence_map,
    )

    # Prior replies from this round visible to this character (§10): availability
    # is decided by the same perception mechanism as ordinary history.
    prior_replies: list[tuple[str, str]] = []
    for prior in round_messages:
        if prior.role != "character" or prior.id == message_id:
            continue
        prior_char = char_by_id.get(prior.character_id)
        if prior_char is None:
            continue
        presence, _ = perception.can_character_perceive_event(
            viewer_character_id=character.id,
            viewer_location=character_locations.get(character.id, "") or "",
            event=prior,
            viewer_name=character.name,
            viewer_location_id=getattr(character, "location_id", None),
        )
        if presence in ("present", "told"):
            prior_replies.append((prior_char.name, prior.content))

    # Token-aware context for this character (same assembly as the round)
    story_block = await _chat_story_block(db, chat)
    built_context = None
    if context_enabled:
        max_tokens = (
            getattr(chat, "max_context_tokens", None)
            or settings.max_context_tokens
        )
        built_context = await ContextBuilder().build(
            db=db,
            chat_id=chat_id,
            character=character,
            user_message=user_message.content,
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
            memories=memories,
            scene_state=scene_state,
            present_character_names=None,
            relationships_block=relationships_block,
            locations=chat_locations,
            location_descriptions=location_descriptions,
            stagnation_rounds=stagnation_rounds,
            viewer_location=character_locations.get(character.id, ""),
            prior_replies=prior_replies,
            is_isolated=_character_is_isolated(
                character_locations,
                character.id,
                characters,
                player_location,
            ),
            max_tokens=max_tokens,
            story_block=story_block,
            world_state_block=world_state_block,
        )

    other_names = get_other_character_names(characters, character.id)
    enable_thinking = bool(getattr(chat, "thinking_mode", settings.enable_thinking))

    # One-time user interventions apply to regeneration too, but are NOT
    # consumed here — they survive until a full round is generated. Only the
    # character's frozen recipient set decides whether the instruction applies.
    _round_interventions = await pending_intervention.list_interventions(db, chat_id)
    directive = pending_intervention.build_directive_for_character(
        _round_interventions, character.id
    )

    response_text = ""
    turn_output = None
    consistency_verdict = "no_actions"
    try:
        async for event in ollama_client.generate(
            client=client,
            chat_id=chat.id,
            character=character,
            messages_history=context_messages,
            general_prompt=_chat_plot_text(chat),
            memories=memories,
            other_character_names=other_names,
            max_history_length=history_limit,
            model_name=generation_model_name,
            character_names=character_names,
            summary=summary_text,
            viewer_character_id=character.id,
            presence_map=presence_map,
            same_round_message_ids=None,
            enable_thinking=enable_thinking,
            viewer_location=character_locations.get(character.id, ""),
            character_locations=character_locations,
            prior_replies=prior_replies,
            scene_state=scene_state,
            present_character_names=None,
            stagnation_rounds=stagnation_rounds,
            is_isolated=_character_is_isolated(
                character_locations,
                character.id,
                characters,
                player_location,
            ),
            locations=chat_locations,
            location_descriptions=location_descriptions,
            relationships_block=relationships_block,
            behavior_drivers_block=drivers_block,
            open_issues_block=open_issues_block,
            proactive_boost=proactive_boost,
            built_context=built_context,
            epistemic_mask_block=epistemic_mask_block,
            directive=directive,
            story_block=story_block,
            recency_tail_block=(
                built_context.recency_tail_text
                if built_context is not None
                else witness_model.build_character_recency_tail(
                    round_messages,
                    character.id,
                    character_names,
                    player_id=player_id,
                    attention_map=attention_map,
                )
            ),
            world_state_block=world_state_block,
        ):
            if event["type"] == "token":
                yield {
                    "type": "token",
                    "text": event["text"],
                    "character_id": character.id,
                }
            elif event["type"] == "response":
                response_text = event["text"]
                turn_output = event.get("turn")
                consistency_verdict = event.get("verdict", "no_actions")
    except RuntimeError as exc:
        logger.error(
            "[chat_id=%d] Regeneration failed for %s: %s",
            chat_id,
            character.name,
            exc,
        )
        raise RuntimeError(
            f"Не удалось перегенерировать ответ {character.name}: {exc}"
        ) from exc

    if not response_text:
        raise RuntimeError(f"Пустой ответ при перегенерации {character.name}")

    # WPE Фаза 5: Action Resolution в пути перегенерации (Ул.1, §5). При
    # выключенном флаге остаётся legacy regex-канал (safety-net, И4).
    actions_active = (
        settings.world_engine_actions_enabled
        and turn_output is not None
        and bool(turn_output.actions)
    )
    if actions_active:
        if consistency_verdict != "contradiction":
            applied = await crud.apply_character_actions(
                db,
                chat_id,
                character,
                turn_output,
                round_id=None,
            )
            for mv in applied.applied_moves:
                if mv.get("changed"):
                    character_locations[character.id] = mv["location_to"]
                    character.location = mv["location_to"]
            rejected = applied.rejected
        else:
            applied = crud.ApplyActionsResult()
            rejected = [
                {"action_index": index, "type": action.type}
                for index, action in enumerate(turn_output.actions)
            ]
        reflected = action_resolution.reflected_action_indices(
            turn_output, response_text, tuple(known_locations)
        )
        for remark in action_resolution.build_narrator_remarks(
            character.name,
            turn_output,
            consistency_verdict,
            applied.applied_moves,
            applied.applied_messages,
            reflected,
            rejected=rejected,
        ):
            await _create_message_with_shadow(
                db,
                schemas.MessageCreate(
                    chat_id=chat_id,
                    role="system",
                    content=remark,
                    visibility="global",
                ),
                round_id=None,
            )
    else:
        # Deterministic movement detection on the regenerated text (§9-§11, §12).
        # The world update is applied the same way as in the main round path.
        movement_target = detect_character_movement(
            response_text,
            character.name,
            known_locations,
            character_locations,
            character_names,
        )
        if movement_target and movement_target != character_locations.get(
            character.id, ""
        ):
            await crud.update_character_locations_batch(
                db, chat_id, {character.id: movement_target}
            )
            character_locations[character.id] = movement_target
            character.location = movement_target

    # Persist the new reply, then drop the old one
    char_location = character_locations.get(character.id, "") or ""
    _char_location_obj = await crud.resolve_location_string(db, chat_id, char_location)
    char_location_id = (
        _char_location_obj.id if _char_location_obj is not None
        else getattr(character, "location_id", None)
    )
    msg_channel, msg_targets = _detect_communication_channel(
        response_text, character.name, character_names
    )
    msg_visibility = settings.default_event_visibility
    if msg_channel != "direct" and msg_targets:
        msg_visibility = "targeted"
    new_message = await _create_message_with_shadow(
        db,
        schemas.MessageCreate(
            chat_id=chat_id,
            character_id=character.id,
            role="character",
            content=response_text,
            visibility=msg_visibility,
            location=char_location,
            location_id=char_location_id,
            target_character_ids=msg_targets,
            channel=msg_channel,
            stimuli=[
                s.to_dict()
                for s in extract_stimuli(response_text, list(character_names.values()))
            ],
        ),
    )
    await compute_and_save_presence_for_message(
        db, new_message, characters, character_names
    )
    await crud.delete_message(db, message_id)

    yield {"type": "message", "message": _message_to_dict(new_message)}
