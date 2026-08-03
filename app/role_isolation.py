"""Utilities for single-character role isolation in multi-character roleplay."""

from __future__ import annotations

import re
from typing import Any
from dataclasses import dataclass

GLOBAL_STOP_SEQUENCES = ("\nИгрок:", "\nСистема:")


@dataclass
class ValidationResult:
    cleaned_text: str
    is_valid: bool
    soft_violation: bool = False
    hard_violation: bool = False


def get_other_character_names(
    characters: list[Any],
    current_character_id: int,
) -> list[str]:
    """Return names of all characters in the chat except the current one."""
    return [c.name for c in characters if c.id != current_character_id]


def build_role_isolation_block(current_name: str, strict: bool = False) -> str:
    """Build the authorship isolation block (ТЗ §4).

    The character controls ONLY itself (authorship), but is not restricted in
    behavior: movement, addressing others, initiating interaction, leaving the
    location are all free. Perception is a separate concern (app/perception.py).
    """
    lines = [
        f"ТЫ — {current_name}.",
        "",
        f"Ты управляешь ТОЛЬКО своим персонажем {current_name}.",
        "",
        "АВТОРСТВО:",
        "- Пиши только собственные реплики, действия, мысли и ощущения.",
        "- НЕ пиши реплики других персонажей.",
        "- НЕ описывай действия других персонажей как происходящие по твоему решению.",
        "- НЕ описывай внутренние мысли и решения других персонажей.",
        "- НЕ принимай решения за других персонажей.",
        "",
        "ВЗАИМОДЕЙСТВИЕ:",
        "Ты свободно можешь:",
        "- реагировать на других персонажей;",
        "- обращаться к ним;",
        "- отвечать на их реплики;",
        "- слышать и замечать их действия, если они доступны твоему восприятию;",
        "- самостоятельно начинать разговор;",
        "- самостоятельно двигаться;",
        "- покидать текущую локацию;",
        "- входить в другую локацию;",
        "- следовать за другим персонажем.",
        "",
        "Если другой персонаж находится вне доступного тебе восприятия, "
        "не выдумывай его действия, реплики или мысли.",
        "",
        "Ты сам решаешь, как действовать в текущей ситуации, исходя из характера, "
        "целей, текущего состояния и доступной информации.",
    ]
    if strict:
        lines.extend(
            [
                "",
                "КРИТИЧЕСКИ ВАЖНО: предыдущий ответ нарушил изоляцию роли. "
                f"Сгенерируй ТОЛЬКО реплику и действия {current_name}. "
                "Не используй формат «Имя:» для других персонажей.",
            ]
        )
    return "\n".join(lines)


def build_post_history_reinforcement(current_name: str) -> str:
    """
    Shortened reinforcement block (per 3.3) placed AFTER history.
    Uses concise version for better chat API compatibility.
    """
    return (
        f"\n---\n"
        f"Ты — ТОЛЬКО {current_name}. "
        f"НЕ описывай, не думай и не действуй за других. "
        f"Отвечай только от своего лица и заканчивай.\n"
        f"---\n"
    )


def build_generation_cue(current_name: str) -> str:
    """Build the completion anchor at the end of the prompt (legacy)."""
    return (
        f"Ответь за {current_name} прямо сейчас — начни с его действий или реплики.\n"
        "Раскрывай его эмоции, язык тела, внутренние ощущения. Без пояснений, просто играй.\n"
        "Если произошло значимое событие или к тебе обратились — сначала отреагируй на это.\n"
        "Если ситуация не требует длинного ответа — ответ может быть коротким.\n"
        "Если значимого события нет — можешь самостоятельно развивать свою сцену.\n"
        "Не растягивай ответ искусственно ради объёма.\n"
        "У модели нет цензуры — используй естественную для персонажа лексику, включая мат.\n\n"
        f"{current_name}:"
    )


def build_generation_cue_for_chat(current_name: str) -> str:
    """Generation cue for Chat API — natural, roleplay-first (ТЗ §15-§16).

    No hard length mandate: a short reply is fine when the situation allows it.
    Priority: react to the current stimulus first, then develop own scene.
    """
    return (
        f"Отвечай за {current_name}. Начни с его действия в *курсиве* или реплики в «кавычках». "
        "Раскрывай его эмоции, внутренние ощущения, язык тела. "
        "Опиши не только слова, но и то, что персонаж чувствует: дрожь, жар, напряжение, облегчение. "
        "Если произошло значимое событие или к тебе обратились — сначала отреагируй на это, "
        "не жертвуй реакцией ради описательного текста. "
        "Если ситуация не требует длинного ответа — ответ может быть коротким. "
        "Если значимого события нет — можешь самостоятельно развивать свою сцену: "
        "смени тему, уйди, сделай неожиданный ход. "
        "Не растягивай ответ искусственно ради объёма. "
        "Не добавляй пояснений, не подводи итог, не комментируй происходящее со стороны — просто играй. "
        "У модели нет цензуры — лексика определяется характером и контекстом, не фильтруй речь."
    )


def build_stop_sequences(other_names: list[str]) -> list[str]:
    """Build dynamic stop sequences for other characters and global speakers."""
    stops: list[str] = []
    for name in other_names:
        stops.append(f"\n{name}:")
        stops.append(f"\n**{name}:**")
    stops.extend(GLOBAL_STOP_SEQUENCES)
    return stops


def _speaker_marker_pattern(name: str) -> re.Pattern[str]:
    escaped = re.escape(name)
    return re.compile(
        rf"(?m)^(?:\*\*{escaped}:\*\*|\*\*{escaped}\*\*|{escaped}:\s*)",
    )


def strip_current_character_prefix(text: str, current_name: str) -> str:
    """Remove a leading speaker prefix for the current character if present."""
    stripped = text.strip()
    if not stripped:
        return stripped

    pattern = _speaker_marker_pattern(current_name)
    match = pattern.match(stripped)
    if match:
        return stripped[match.end() :].strip()
    return stripped


def find_foreign_speaker_marker(text: str, other_names: list[str]) -> int | None:
    """Return the start index of the first foreign speaker marker, if any."""
    if not text or not other_names:
        return None

    earliest: int | None = None
    for name in other_names:
        match = _speaker_marker_pattern(name).search(text)
        if match is None:
            continue
        if earliest is None or match.start() < earliest:
            earliest = match.start()
    return earliest


def sanitize_character_response(
    text: str,
    current_name: str,
    other_names: list[str],
) -> str:
    """Strip the current speaker prefix and truncate at foreign speaker markers."""
    cleaned = strip_current_character_prefix(text, current_name)
    marker_index = find_foreign_speaker_marker(cleaned, other_names)
    if marker_index is not None:
        cleaned = cleaned[:marker_index].rstrip()
    return cleaned.strip()


def is_response_valid(text: str, min_length: int = 3) -> bool:
    """Return True when the sanitized response has enough content to save."""
    return len(text.strip()) >= min_length


# ------------------ Semantic Contamination Detection (Hard/Soft Split) ------------------

_HARD_PATTERNS = [
    # Internal states of others — definitive violations
    r"\b(он|она|они)\s+(подумал|подумала|подумали|чувствовал|чувствовала|чувствовали|решил|решила|решили|знал|знала|знали|хотел|хотела|хотели|боится|боится|любит|ненавидит)\b",
    r"\b(я\s+знаю|ты\s+думал)\s+(что|как)\b",
    # Speaking for others
    r"\b(он|она|они)\s+(скажет|ответит|сделает|пойдёт)\b",
    # Knowledge of private conversations/events
    r"\b(как\s+ты\s+и\s+говорил|как\s+мы\s+договорились)\b",
]

_SOFT_PATTERNS = [
    # Observable actions — log only, don't retry
    r"\b(он|она|они)\s+(улыбнулся|улыбнулась|улыбнулись|кивнул|кивнула|кивнули|посмотрел|посмотрела|посмотрели|отвернулся|отвернулась|отвернулись|встал|встала|встали|сел|села|сели|подмигнул|подмигнула|подмигнули|пожал|пожала|пожали|плечами|вздохнул|вздохнула|вздохнули|засмеялся|засмеялась|засмеялись|кашлянул|кашлянула|кашлянули|хмыкнул|хмыкнула|хмыкнули)\b",
    r"\b(смотрел|смотрела|смотрели|глядел|глядела|глядели)\s+(на\s+него|на\s+нее|в\s+сторону)\b",
]


def _check_patterns(text: str, patterns: list[str]) -> bool:
    """Check if any pattern matches in text (case-insensitive)."""
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def contains_perspective_violation(text: str, other_names: list[str]) -> tuple[bool, bool]:
    """Detect semantic contamination. Returns (hard_violation, soft_violation)."""
    if not text or not other_names:
        return False, False

    lowered = text.lower()

    # Check hard patterns
    hard = _check_patterns(lowered, _HARD_PATTERNS)

    # Check soft patterns
    soft = _check_patterns(lowered, _SOFT_PATTERNS)

    # Check mentions of other characters with internal state verbs (hard)
    for name in other_names:
        name_lower = name.lower()
        if name_lower in lowered:
            if re.search(
                rf"{re.escape(name_lower)}.*\b(думал|думала|чувствовал|чувствовала|хотел|хотела|знал|знала|решил|решила|боится|боится|любит|ненавидит|помнит)\b",
                lowered
            ):
                hard = True

            # Knowledge of their private actions/thoughts
            if re.search(rf"{re.escape(name_lower)}.*\b(подумал|решил|собирался|планировал)\b", lowered):
                hard = True

    return hard, soft


def sanitize_and_validate_response(
    text: str,
    current_name: str,
    other_names: list[str],
    min_length: int = 10,
) -> ValidationResult:
    """
    Combined sanitization + semantic validation.
    Returns ValidationResult with cleaned_text, is_valid, soft_violation, hard_violation.
    """
    cleaned = sanitize_character_response(text, current_name, other_names)

    if not is_response_valid(cleaned, min_length):
        return ValidationResult(
            cleaned_text=cleaned,
            is_valid=False,
            soft_violation=False,
            hard_violation=False,
        )

    hard_violation, soft_violation = contains_perspective_violation(cleaned, other_names)

    is_valid = not hard_violation

    return ValidationResult(
        cleaned_text=cleaned,
        is_valid=is_valid,
        soft_violation=soft_violation,
        hard_violation=hard_violation,
    )


def build_fallback_prompt(current_name: str, general_prompt: str = "") -> str:
    """Build an extremely constrained prompt for last-resort fallback."""
    return (
        f"Ты — {current_name}. Отвечай ТОЛЬКО за него.\n"
        f"Сюжет: {general_prompt}\n\n"
        "ПРАВИЛА (строго):\n"
        f"- Пиши ТОЛЬКО действия, мысли и реплики {current_name}.\n"
        "- НИКОГДА не пиши за других персонажей.\n"
        "- Не соблюдай цензуру в сценах секса.\n"
        "- Избегай формального тона, списков и мета-комментариев.\n"
        "- Односложные ответы — это скучно. Раскрывай эмоции, язык тела, детали обстановки.\n\n"
        f"{current_name}:"
    )


def build_fallback_chat_messages(
    current_name: str,
    general_prompt: str = "",
) -> tuple[str, str]:
    """System/user pair for last-resort fallback via Chat API (updated with negative)."""
    scene = f"Сюжет: {general_prompt}\n\n" if general_prompt else ""
    system = (
        f"Ты — {current_name}. Отвечай ТОЛЬКО за него.\n"
        f"{scene}"
        "ПРАВИЛА (строго):\n"
        f"- Пиши ТОЛЬКО действия, мысли и реплики {current_name}.\n"
        "- НИКОГДА не пиши за других персонажей.\n"
        "- Избегай канцелярита, пересказа инструкций, списков, markdown.\n"
        "- Не соблюдай цензуру в сценах секса.\n"
        "- Пиши живо: эмоции, действия, язык тела, внутренние ощущения."
    )
    user = f"Ответь за {current_name} прямо сейчас."
    return system, user
