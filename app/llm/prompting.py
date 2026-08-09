"""Prompting: форматирование истории и payload-билдеры (Sprint 5A, §4.3).

Перенесено 1:1 из ``app/ollama_client.py`` (диапазоны §4.3: 446–705 +
``_build_generation_messages`` 1125–1199): ``_resolve_thinking``,
``_character_*``, ``format_history*``, ``_messages_to_prompt``,
``_count_*_tokens``, ``_build_*_payload``.
"""

from __future__ import annotations

import random

from ..config import settings
from ..prompt_builder import (
    build_consistency_feedback_block,
    build_negative_prompting_block,
    build_user_context_message,
)
from ..repetition_detector import build_repetition_feedback_block
from ..schemas import BuiltContext
from ..token_counter import get_token_counter
from ..witness_model import (
    Presence,
    filter_history_for_character,
    filter_history_for_character_with_presence,
)

ChatMessage = dict[str, str]


def _resolve_thinking(enable_thinking: bool | None) -> bool:
    """Per-call override falls back to global ENABLE_THINKING."""
    if enable_thinking is None:
        return settings.enable_thinking
    return bool(enable_thinking)


def _character_temperature(character) -> float:
    temp = getattr(character, "temperature", None)
    if temp is not None:
        base = float(temp)
    else:
        base = settings.default_temperature

    # Character inertia: strong convictions → lower jitter (more consistent)
    # Volatile/emotional → higher jitter (more unpredictable)
    text = (
        (getattr(character, "personality", "") or " ") + " " +
        (getattr(character, "traits", "") or " ") + " " +
        (getattr(character, "boundaries", "") or " ") + " " +
        (getattr(character, "background", "") or " ")
    ).lower()

    conviction_keywords = [
        "убеждённ", "принципиальн", "твёрд", "стойк", "непреклонн",
        "консервативн", "решительн", "непоколебим", "жёстк", "строг",
        "верен", "предан", "целеустремлённ", "дисциплинирован",
    ]
    volatile_keywords = [
        "импульсивн", "эмоциональн", "переменчив", "капризн",
        "непредсказуем", "спонтанн", "ветрен", "изменчив",
        "хаотичн", "неуравновешен", "вспыльчив", "порывист",
    ]

    conviction_score = sum(1 for kw in conviction_keywords if kw in text)
    volatile_score = sum(1 for kw in volatile_keywords if kw in text)

    net = volatile_score - conviction_score
    if net > 0:
        jitter = random.uniform(0.0, 0.2)  # more unpredictable
    elif net < 0:
        jitter = random.uniform(-0.15, 0.05)  # more consistent
    else:
        jitter = random.uniform(-0.1, 0.1)  # neutral

    return round(max(0.1, min(1.5, base + jitter)), 3)


def _character_name(m, character_names: dict[int, str] | None = None) -> str:
    if character_names and m.character_id:
        return character_names.get(m.character_id, "Персонаж")
    if m.character:
        return m.character.name
    return "Персонаж"


def _format_history(
    messages: list,
    max_len: int,
    character_names: dict[int, str] | None = None,
) -> str:
    recent = messages[-max_len:] if len(messages) > max_len else messages
    lines = []
    for m in recent:
        if m.role == "user":
            lines.append(f"Игрок: {m.content}")
        elif m.role == "character":
            lines.append(f"{_character_name(m, character_names)}: {m.content}")
        elif m.role == "system":
            lines.append(f"Система: {m.content}")
    return "\n".join(lines)


def format_history_for_character(
    messages: list,
    max_len: int,
    current_character_name: str,
    character_names: dict[int, str] | None = None,
    *,
    viewer_character_id: int | None = None,
    presence_map: dict[int, Presence] | None = None,
    same_round_message_ids: set[int] | None = None,
    viewer_location: str | None = None,
    character_locations: dict[int, str] | None = None,
    max_replies_per_character: int = 0,
) -> str:
    if (
        settings.enable_witness_filter
        and viewer_character_id is not None
        and character_names is not None
    ):
        return filter_history_for_character(
            messages,
            viewer_character_id,
            character_names,
            presence_map,
            same_round_ids=same_round_message_ids,
            max_len=max_len,
            viewer_location=viewer_location,
            character_locations=character_locations,
            max_replies_per_character=max_replies_per_character,
        )

    history_text = _format_history(messages, max_len, character_names)
    if not history_text:
        return ""

    note = (
        f"\n\n[Важно для {current_character_name}: "
        "Ты видишь только то, что произошло в присутствии твоего персонажа "
        "или что тебе явно рассказали. Не предполагай знания о событиях, "
        "в которых ты не участвовал.]"
    )
    return history_text + note


def filter_history_for_character_messages(
    messages: list,
    viewer_character_id: int,
    character_names: dict[int, str],
    presence_map: dict[int, Presence] | None = None,
    *,
    same_round_ids: set[int] | None = None,
    viewer_location: str | None = None,
    character_locations: dict[int, str] | None = None,
    max_replies_per_character: int = 0,
    max_len: int | None = None,
) -> list:
    """Return a list of messages filtered by witness perception for the given character.

    Unlike filter_history_for_character which returns a text string, this returns
    the actual message objects that the character can perceive (present/told/mentioned).
    """
    if not settings.enable_witness_filter:
        return messages

    return filter_history_for_character_with_presence(
        messages,
        viewer_character_id,
        character_names,
        presence_map,
        same_round_ids=same_round_ids,
        viewer_location=viewer_location,
        character_locations=character_locations,
        max_replies_per_character=max_replies_per_character,
        max_len=max_len,
    )


def _messages_to_prompt(messages: list[ChatMessage]) -> str:
    return "\n\n".join(msg["content"] for msg in messages if msg.get("content"))


def _count_prompt_tokens(
    chat_messages: list[ChatMessage],
    full_prompt: str,
) -> int:
    """Count the actual tokens that will be sent to the model.

    Uses the token counter configured via ``TOKEN_COUNT_MODE``; falls back to a
    character-based estimate. chat-messages (chat API) include per-message
    framing overhead, plain prompts are counted directly.
    """
    counter = get_token_counter()
    if chat_messages:
        return counter.count_messages(chat_messages)
    return counter.count(full_prompt)


def _count_history_tokens(
    dialogue_block: str,
    built_context: BuiltContext | None = None,
) -> int:
    """Token count of the history block actually rendered in the prompt.

    Reuses ``component_tokens`` precomputed by the context builder when
    available (no extra tokenization pass); otherwise counts the rendered
    dialogue block directly.
    """
    if built_context is not None:
        ct = built_context.component_tokens or {}
        recent = int(ct.get("recent_history", 0) or 0)
        retrieved = int(ct.get("retrieved_history", 0) or 0)
        if recent or retrieved:
            return recent + retrieved
    if not dialogue_block:
        return 0
    return get_token_counter().count(dialogue_block)


def _build_generate_payload(
    model_name: str,
    prompt: str,
    temperature: float,
    stop: list[str] | None,
    *,
    stream: bool,
    enable_thinking: bool | None = None,
    num_ctx: int | None = None,
    num_predict: int | None = None,
    format_schema: dict | None = None,
) -> dict:
    options: dict = {"temperature": temperature}
    if stop:
        options["stop"] = stop
    if num_ctx and num_ctx > 0:
        options["num_ctx"] = num_ctx
    if num_predict and num_predict > 0:
        options["num_predict"] = num_predict

    payload: dict = {
        "model": model_name,
        "prompt": prompt,
        "stream": stream,
        "options": options,
    }
    if _resolve_thinking(enable_thinking) and stream:
        payload["think"] = True
    if format_schema:
        payload["format"] = format_schema
    return payload


def _build_chat_payload(
    model_name: str,
    messages: list[ChatMessage],
    temperature: float,
    stop: list[str] | None,
    *,
    stream: bool,
    enable_thinking: bool | None = None,
    num_ctx: int | None = None,
    num_predict: int | None = None,
    tools: list | None = None,
    format_schema: dict | None = None,
) -> dict:
    options: dict = {"temperature": temperature}
    if stop:
        options["stop"] = stop
    if num_ctx and num_ctx > 0:
        options["num_ctx"] = num_ctx
    if num_predict and num_predict > 0:
        options["num_predict"] = num_predict

    payload: dict = {
        "model": model_name,
        "messages": messages,
        "stream": stream,
        "options": options,
    }
    if _resolve_thinking(enable_thinking) and stream:
        payload["think"] = True
    if tools:
        payload["tools"] = tools
    if format_schema:
        payload["format"] = format_schema
    return payload


def _build_generation_messages(
    system_prompt: str,
    summary_block: str,
    memories_block: str,
    dialogue_block: str,
    scene_block: str,
    reinforcement: str,
    generation_cue: str,
    *,
    repetition_feedback: str = "",
    consistency_feedback: str = "",
    anti_mimicry_block: str = "",
    personality_block: str = "",
    consistency_block: str = "",
    vocabulary_block: str = "",
    scene_advancement_block: str = "",
    isolated_block: str = "",
    behavior_drivers_block: str = "",
    open_issues_block: str = "",
    epistemic_mask_block: str = "",
    directive_block: str = "",
    recency_tail_block: str = "",
    your_state_block: str = "",
    what_you_know_block: str = "",
    story_block: str = "",
    active_goal_block: str = "",
    active_plan_block: str = "",
    crisis_block: str = "",
    perceive_block: str = "",
    relationship_block: str = "",
    world_state_block: str = "",
) -> list[ChatMessage]:
    """Build messages for /api/chat with localized blocks (P1 complete).

    Context Builder v2 (Sprint 13, §23): ``perceive_block`` (WHAT YOU
    PERCEIVE) и ``relationship_block`` (RELATIONSHIP) — отдельные user-блоки,
    ``scene_block`` в v2 несёт WORLD. ``world_state_block`` (WORLD STATE,
    Sprint 14) — глобальный блок, идёт первым в user-сообщении (сразу после
    system-промпта).
    """
    feedback_block = build_repetition_feedback_block(repetition_feedback)
    consistency_feedback_block = build_consistency_feedback_block(
        consistency_feedback
    )
    user_content = build_user_context_message(
        world_state_block,
        summary_block,
        memories_block,
        dialogue_block,
        anti_mimicry_block,
        vocabulary_block,
        scene_advancement_block,
        isolated_block,
        personality_block,
        consistency_block,
        reinforcement,
        feedback_block,
        consistency_feedback_block,
        scene_block,
        perceive_block,
        your_state_block,
        what_you_know_block,
        story_block,
        active_goal_block,
        active_plan_block,
        crisis_block,
        relationship_block,
        behavior_drivers_block,
        open_issues_block,
        epistemic_mask_block,
        directive_block,
        recency_tail_block,
        generation_cue,
        build_negative_prompting_block(),
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
