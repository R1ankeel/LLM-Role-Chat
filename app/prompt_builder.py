"""Assembly of system prompts from character cards and templates."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .role_isolation import build_role_isolation_block

_TEMPLATES_PATH = Path(__file__).parent / "prompts" / "ru.json"
with _TEMPLATES_PATH.open(encoding="utf-8") as f:
    _TEMPLATES: dict[str, Any] = json.load(f)

_CHARACTER_SECTIONS = (
    "personality",
    "traits",
    "background",
    "speech_style",
    "boundaries",
)


def _character_field(character: Any, field: str) -> str:
    value = getattr(character, field, "") or ""
    return value.strip()


def _scene_char_locs(scene_state: Any) -> dict[str, str]:
    if scene_state is None:
        return {}
    raw = getattr(scene_state, "character_locations", {})
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items() if v}
    return {}


def merge_char_locations(
    scene_state: Any,
    character_locations: dict[int, str] | None = None,
    character_names: dict[int, str] | None = None,
) -> dict[str, str]:
    """Merge authoritative character locations with scene state.

    character_locations maps character_id -> location (from the characters table),
    character_names maps character_id -> name. Scene state locations are
    confirmed per-round and take precedence.
    """
    merged: dict[str, str] = {}
    if character_locations:
        for cid, loc in character_locations.items():
            if not loc:
                continue
            name = (character_names or {}).get(int(cid), str(cid))
            if name:
                merged[name] = loc
    merged.update(_scene_char_locs(scene_state))
    return {name: loc for name, loc in merged.items() if name and loc}


def build_character_card(character: Any) -> str:
    """Build XML character block with only non-empty sections."""
    identity = _TEMPLATES["character"]["identity"].format(name=character.name)
    inner_parts = [f"  <identity>{identity}</identity>"]

    for field in _CHARACTER_SECTIONS:
        content = _character_field(character, field)
        if content:
            tag = _TEMPLATES["character"]["section_tags"][field]
            inner_parts.append(f"  <{tag}>{content}</{tag}>")

    return "<character>\n" + "\n".join(inner_parts) + "\n</character>"


def build_examples_block(example_messages: str) -> str:
    """Build few-shot examples block from character.example_messages.

    Examples are split only by the configured separator (---).
    Multi-line examples are supported; newlines alone do not split.
    Without a separator, the whole text is treated as one example.
    """
    text = (example_messages or "").strip()
    if not text:
        return ""

    separator = _TEMPLATES["examples"]["separator"]
    examples = [part.strip() for part in text.split(separator) if part.strip()]

    if not examples:
        return ""

    header = _TEMPLATES["examples"]["header"]
    body_lines = [header, ""]
    for example in examples:
        body_lines.append(separator)
        body_lines.append(example)
        body_lines.append("")

    return "<examples>\n" + "\n".join(body_lines).rstrip() + "\n</examples>"


def build_rules_block() -> str:
    """Build rules block from template (now includes negative prompting)."""
    rules = _TEMPLATES["rules"]
    negative = _TEMPLATES.get("negative", [])
    lines = ["<rules>"] + [f"- {rule}" for rule in rules]
    if negative:
        lines.append("<negative>")
        lines.extend([f"- {item}" for item in negative])
        lines.append("</negative>")
    lines.append("</rules>")
    return "\n".join(lines)


def build_negative_prompting_block() -> str:
    """Dedicated negative prompting block (for isolation or fallback)."""
    negative = _TEMPLATES.get("negative", [])
    if not negative:
        return ""
    lines = ["<negative_prompting>"] + [f"- {item}" for item in negative] + ["</negative_prompting>"]
    return "\n".join(lines)


def build_reinforcement_block(name: str) -> str:
    """Shortened post-history reinforcement from template (per 3.3)."""
    template = _TEMPLATES.get("reinforcement", "")
    if not template:
        return f"\n---\nТы — ТОЛЬКО {name}. Отвечай только за него.\n---\n"
    return "\n---\n" + template.format(name=name) + "\n---\n"


def build_character_summary_block(summary: str) -> str:
    """Build level-3 session summary block for a character."""
    text = (summary or "").strip()
    if not text:
        return ""
    header = _TEMPLATES["memory"]["summary_header"]
    return f"<character_summary>\n{header}\n{text}\n</character_summary>"


def build_anti_mimicry_block(current_name: str, prior_replies: list[tuple[str, str]]) -> str:
    """Build anti-mimicry block for characters with order_index > 0.
    
    Args:
        current_name: Name of the current character generating a response.
        prior_replies: List of (character_name, reply_content) tuples from earlier characters in this round.
    
    Returns:
        Formatted anti-mimicry block, or empty string if no prior replies.
    """
    if not prior_replies:
        return ""
    lines = [
        f"В этом ходе уже ответили: {', '.join(name for name, _ in prior_replies)}.",
        "Их реплики выше — для контекста. НЕ повторяй их действия, интонацию или формулировки.",
        f"Отвечай ТОЛЬКО со своей уникальной перспективы как {current_name}.",
    ]
    return "\n---\n" + "\n".join(lines) + "\n---\n"


def build_memories_block(memories: list) -> str:
    """Build episodic memory facts block. Now uses BM25-relevance ranked memories (P1).
    Includes importance if available from selection or extraction."""
    if not memories:
        return ""
    header = _TEMPLATES["memory"]["facts_header"]
    mem_lines = []
    for m in memories:
        content = getattr(m, "content", m.get("content", str(m)) if isinstance(m, dict) else str(m))
        importance = getattr(m, "importance", None) or (m.get("importance") if isinstance(m, dict) else None)
        if importance and float(importance) > 0.6:
            mem_lines.append(f"- {content} (важность: {float(importance):.1f})")
        else:
            mem_lines.append(f"- {content}")
    mem_lines_str = "\n".join(mem_lines)
    return f"<character_memories>\n{header}\n{mem_lines_str}\n</character_memories>"


def build_recent_dialogue_block(history_text: str) -> str:
    """Wrap formatted recent dialogue in XML block."""
    text = (history_text or "").strip()
    if not text:
        return ""
    header = _TEMPLATES["memory"]["dialogue_header"]
    return f"<recent_dialogue>\n{header}\n{text}\n</recent_dialogue>"


def build_scene_block(
    general_prompt: str,
    scene_state: Any = None,
    present_character_names: list[str] | None = None,
    *,
    current_character_name: str | None = None,
    character_locations: dict[str, str] | None = None,
    locations: str = "[]",
) -> str:
    """Build scene block with per-character location tracking (P3).

    Args:
        general_prompt: The chat's general prompt / plot description.
        scene_state: SceneState object with time_of_day, custom_state, character_locations.
        present_character_names: Deprecated — kept for backward compat.
        current_character_name: Name of the character whose prompt is being built.
        character_locations: Map of character_name -> current_location.
        locations: JSON array of allowed locations for this chat.
    """
    parts = []
    text = (general_prompt or "").strip()
    if text:
        parts.append(_TEMPLATES["scene"].format(general_prompt=text))

    if scene_state:
        # Global time of day
        if getattr(scene_state, "time_of_day", ""):
            parts.append(f"Время: {scene_state.time_of_day}")

        # Per-character location
        cl_map = character_locations or {}
        if not cl_map and hasattr(scene_state, "character_locations"):
            raw = scene_state.character_locations
            if isinstance(raw, dict):
                cl_map = raw

        if current_character_name and current_character_name in cl_map:
            parts.append(f"Твоя локация: {cl_map[current_character_name]}")

        # Which characters are present in the same location
        if current_character_name and cl_map:
            my_loc = cl_map.get(current_character_name, "")
            same_loc = [
                name for name, loc in cl_map.items()
                if name != current_character_name and loc and loc == my_loc
            ]
            if same_loc:
                parts.append(f"Рядом с тобой: {', '.join(sorted(same_loc))}")
        elif present_character_names:
            # Fallback for legacy callers
            parts.append(f"Присутствуют: {', '.join(present_character_names)}")

        # Allowed locations list
        if locations and locations != "[]":
            try:
                loc_list = json.loads(locations)
                if isinstance(loc_list, list) and loc_list:
                    parts.append(f"Доступные локации: {', '.join(loc_list)}")
            except (json.JSONDecodeError, TypeError):
                pass

        # Global custom state (weather, mood, etc.)
        custom_state = getattr(scene_state, "custom_state", None)
        if custom_state:
            if isinstance(custom_state, str):
                try:
                    custom_state = json.loads(custom_state)
                except json.JSONDecodeError:
                    custom_state = {}
            if isinstance(custom_state, dict):
                if custom_state.get("weather"):
                    parts.append(f"Погода: {custom_state['weather']}")
                if custom_state.get("mood"):
                    parts.append(f"Атмосфера: {custom_state['mood']}")
                if custom_state.get("tension", 0) > 0.3:
                    label = "высокое" if custom_state['tension'] > 0.5 else "среднее"
                    parts.append(f"Напряжение: {label} ({custom_state['tension']:.1f})")
                if custom_state.get("tension", 0) > 0.7:
                    parts.append("Ты чувствуешь, что ситуация на пределе — каждое слово и жест имеют вес.")
                    parts.append("Покажи состояние персонажа через тело: дрожь, сбитое дыхание, учащённый пульс, холодный пот, напряжение в каждой мышце.")
                if custom_state.get("active_goal"):
                    parts.append(f"Активная цель сцены: {custom_state['active_goal']}")
                if current_character_name and custom_state.get("active_goals"):
                    goals = custom_state["active_goals"]
                    if isinstance(goals, dict) and current_character_name in goals:
                        parts.append(f"Твоя цель: {goals[current_character_name]}")
                if custom_state.get("important_objects"):
                    parts.append(f"Важные объекты: {', '.join(custom_state['important_objects'])}")
                if custom_state.get("active_events"):
                    parts.append(f"События: {', '.join(custom_state['active_events'])}")
                if custom_state.get("time_progression"):
                    parts.append(f"Прогрессия времени: {custom_state['time_progression']}")

    if not parts:
        return ""
    return f"<scene>\n{chr(10).join(parts)}\n</scene>"


def build_user_context_message(*blocks: str) -> str:
    """Join non-empty context blocks for Chat API user message."""
    parts = [block.strip() for block in blocks if block and block.strip()]
    return "\n\n".join(parts)


def build_system_prompt(
    character: Any,
    general_prompt: str,
    strict: bool = False,
    relationships_block: str = "",
) -> str:
    """Assemble full system prompt: card → examples → rules (with negative) → isolation (per 3.5 full localization)."""
    parts = [build_character_card(character)]

    examples = build_examples_block(_character_field(character, "example_messages"))
    if examples:
        parts.append(examples)

    if relationships_block:
        parts.append(build_relationships_block(relationships_block))

    parts.append(build_rules_block())
    parts.append(build_role_isolation_block(character.name, strict=strict))

    return "\n\n".join(parts)


def build_extraction_system(character_name: str) -> str:
    """Localized system prompt for memory extraction (P1 completion)."""
    template = _TEMPLATES["extraction"]["system"]
    return template.format(name=character_name)


def build_extraction_user(char_desc: str, history: str, name: str) -> str:
    """Localized user prompt for extraction."""
    template = _TEMPLATES["extraction"]["user_prefix"]
    return template.format(desc=char_desc, history=history, name=name)


def build_summary_system(character_name: str, max_paragraphs: int = 3) -> str:
    """Localized system prompt for summary (P1 completion)."""
    template = _TEMPLATES["summary"]["system"]
    return template.format(name=character_name, max_paragraphs=max_paragraphs)


def build_summary_user(
    char_desc: str, history: str, existing: str = "", name: str = ""
) -> str:
    """Localized user prompt for summary."""
    if existing:
        template = _TEMPLATES["summary"]["user_with_existing"]
        return template.format(
            existing=existing, desc=char_desc, history=history, name=name or ""
        )
    template = _TEMPLATES["summary"]["user_new"]
    return template.format(desc=char_desc, history=history, name=name or "")


def format_character_descriptor(character: Any) -> str:
    """Compact character description for memory extraction prompts."""
    parts = [character.name]
    for field in (
        "personality",
        "traits",
        "background",
        "speech_style",
        "boundaries",
        "relationships",
    ):
        content = _character_field(character, field)
        if content:
            parts.append(content)
    return " — ".join(parts)


def _extract_vocabulary_fingerprint(
    speech_style: str,
    example_messages: str,
    max_words: int = 12,
) -> list[str]:
    """Extract distinctive vocabulary from a character's speech style and examples."""
    text = f"{speech_style} {example_messages}"
    if not text.strip():
        return []
    words = re.findall(r'\b[а-яёa-z-]{3,}\b', text.lower())
    stopwords = {
        "что", "это", "так", "вот", "все", "или", "как", "его", "она", "они",
        "только", "если", "нет", "да", "уже", "еще", "там", "тут", "когда",
        "даже", "меня", "тебя", "него", "нее", "них", "ним", "ней", "нами",
        "вами", "нами", "мой", "твой", "свой", "наш", "ваш", "эти", "этот",
        "вдруг", "опять", "снова", "потом", "чтобы", "потому", "поэтому",
        "the", "and", "that", "this", "with", "from", "your", "have", "not",
        "but", "for", "all", "was", "are", "had", "has", "its", "how", "what",
        "when", "then", "where", "which", "their", "them", "they", "been",
    }
    filtered = [w for w in words if w not in stopwords and len(w) > 2]
    counter = Counter(filtered)
    return [word for word, _ in counter.most_common(max_words)]


def build_vocabulary_block(
    character: Any,
    other_replies: list[tuple[str, str]] | None = None,
    max_own: int = 10,
    max_foreign: int = 8,
) -> str:
    """Build a vocabulary guidance block to prevent style contamination.

    Injects the character's own distinctive words (from *speech_style* and
    *example_messages*) and flags words from other characters' recent replies
    that the current character should avoid.
    """
    own_words = _extract_vocabulary_fingerprint(
        _character_field(character, "speech_style"),
        _character_field(character, "example_messages"),
        max_words=max_own,
    )
    if not own_words:
        return ""

    parts = [f"<vocabulary>\nТвой стиль: {', '.join(own_words)}."]

    if other_replies:
        foreign_text = " ".join(reply for _, reply in other_replies if reply)
        foreign_words = _extract_vocabulary_fingerprint("", foreign_text, max_words=max_foreign)
        if foreign_words:
            parts.append(f"Избегай этих слов (они из чужих реплик): {', '.join(foreign_words)}.")

    parts.append("</vocabulary>")
    return "\n".join(parts)


def build_personality_block(character: Any, scene_state: Any = None) -> str:
    """Periodic personality reinforcement block to prevent role drift (Phase 3).

    Injects a brief reminder of who the character is and what their current goal is,
    placed near the end of the prompt where it won't be buried.
    """
    parts = [f"Ты — {character.name}."]

    # Gather key traits
    personality = getattr(character, "personality", "") or ""
    traits = getattr(character, "traits", "") or ""
    key_traits = []
    if personality:
        key_traits.append(personality.strip())
    if traits:
        key_traits.append(traits.strip())
    if key_traits:
        parts.append(f"Твой характер: {'; '.join(key_traits)}.")

    # Get current active goal from scene state
    goal = ""
    if scene_state:
        custom_state = getattr(scene_state, "custom_state", None)
        if custom_state:
            if isinstance(custom_state, str):
                try:
                    custom_state = json.loads(custom_state)
                except (json.JSONDecodeError, TypeError):
                    custom_state = {}
            if isinstance(custom_state, dict):
                goal = custom_state.get("active_goal", "") or ""
                # Prefer per-character active_goals
                char_name = getattr(character, "name", "")
                per_char_goals = custom_state.get("active_goals", {})
                if isinstance(per_char_goals, dict) and char_name in per_char_goals:
                    goal = per_char_goals[char_name]
    if goal:
        parts.append(f"Твоя цель сейчас: {goal}.")

    return "<personality>\n" + "\n".join(parts) + "\n</personality>"


def build_personality_consistency_block(character: Any) -> str:
    """Build a consistency check block that prevents personality contradiction.

    If the character has strong convictions (boundaries), add a reminder
    to stay in character and explain any deviation.
    """
    boundaries = getattr(character, "boundaries", "") or ""
    personality = getattr(character, "personality", "") or ""
    constraints = []
    if boundaries:
        constraints.append(boundaries.strip())
    if personality:
        constraints.append(personality.strip())
    if not constraints:
        return ""

    return (
        "<consistency>\n"
        f"Помни: твой персонаж — {character.name}. "
        f"{' '.join(constraints)}. "
        "Если текущее действие противоречит характеру — объясни, что изменилось, или выбери другое действие.\n"
        "</consistency>"
    )


def build_scene_state_system() -> str:
    """Localized system prompt for scene state extraction (P3)."""
    template = _TEMPLATES["scene_state"]["system"]
    return template


def build_scene_advancement_block(
    stagnation_rounds: int = 0,
    *,
    max_stagnation_rounds: int = 3,
    proactive_action: bool = False,
) -> str:
    """Build scene advancement block to break loops (Phase 6).

    Injects twist, proactive action cue, or location change suggestion
    when stagnation is detected across consecutive rounds.

    Args:
        stagnation_rounds: How many consecutive rounds with stagnation.
        max_stagnation_rounds: Threshold to trigger twist injection.
        proactive_action: Whether to add proactive initiative cue.

    Returns:
        Formatted XML block or empty string.
    """
    templates = _TEMPLATES.get("scene_advancement", {})
    parts = []
    if stagnation_rounds >= max_stagnation_rounds:
        twist = templates.get("twist", "")
        if twist:
            parts.append(f"<scene_twist>\n{twist}\n</scene_twist>")
    if stagnation_rounds >= max_stagnation_rounds + 1:
        loc = templates.get("location_change", "")
        if loc:
            parts.append(f"<location_change>\n{loc}\n</location_change>")
    if proactive_action:
        cue = templates.get("proactive", "")
        if cue:
            parts.append(f"<proactive>\n{cue}\n</proactive>")
    if not parts:
        return ""
    return "\n\n".join(parts)


def build_isolated_block() -> str:
    """Build the isolation block for characters at different locations from the player."""
    return _TEMPLATES.get("isolated", "")


def build_scene_state_user(current_state: dict, history: str, character_names: str, locations: str = "[]") -> str:
    """Localized user prompt for scene state extraction."""
    template = _TEMPLATES["scene_state"]["user_prefix"]
    current_state_str = json.dumps(current_state, ensure_ascii=False, indent=2)
    # Parse allowed locations from JSON array to comma-separated string
    try:
        loc_list = json.loads(locations) if locations and locations != "[]" else []
        loc_str = ", ".join(loc_list) if isinstance(loc_list, list) and loc_list else "(не указаны)"
    except (json.JSONDecodeError, TypeError):
        loc_str = "(не указаны)"
    return template.format(current_state=current_state_str, history=history, character_names=character_names, locations=loc_str)


def build_relationships_block(relationships_text: str) -> str:
    """Build the dynamic relationships block for the system prompt."""
    if not relationships_text:
        return ""
    return f"<relationships>\n{relationships_text}\n</relationships>"
