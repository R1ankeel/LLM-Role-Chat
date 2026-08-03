"""WPE 3.0 (Plans/WPE.md) Фаза 5 — Action Resolution + System Narrator (Ул.1, §5).

Чистая логика без БД:
- `classify_consistency` — Action↔Text Consistency Validator (три класса:
  `consistent` / `minor_ambiguity` (молчаливое действие) / `contradiction`);
- `reflected_action_indices` — какие из применённых действий отражены в тексте
  реплики (для System Narrator);
- `build_consistency_feedback` — промпт-фидбек для contradiction-ретрая;
- `build_narrator_remarks` + шаблоны ремарок — детерминированный System Narrator
  (И6): ремарка генерируется только движком из `WorldEvent`, никогда LLM.

Применение действий (БД, атомарно) живёт в `crud.apply_character_actions`.
"""

from __future__ import annotations

import logging
import re
from typing import Literal

from app import schemas

logger = logging.getLogger(__name__)

ConsistencyVerdict = Literal[
    "no_actions", "consistent", "minor_ambiguity", "contradiction"
]

# Детерминированные эвристики для Consistency Validator. Это анализ *текста
# реплики*, а не извлечение действий из текста (И4: источник истины — actions).
_MOVE_VERBS = (
    "иду",
    "пойду",
    "перебираюсь",
    "направляюсь",
    "захожу",
    "ухожу",
    "уйду",
    "перехожу",
    "отправляюсь",
    "идём",
    "пошли",
)
_MOVE_NEGATIVE = (
    "никуда не пойду",
    "не пойду",
    "никуда не иду",
    "не иду",
    "не уйду",
    "останусь",
    "остаюсь",
    "ни с места",
    "не буду уходить",
    "не собираюсь уходить",
    "не собираюсь идти",
)
_MESSAGE_VERBS = (
    "напишу",
    "пишу",
    "отвечу",
    "отвечаю",
    "позвоню",
    "звоню",
    "отправляю",
    "скину",
    "передам",
    "сброшу",
)
_MESSAGE_NEGATIVE = (
    "не буду писать",
    "не напишу",
    "не отвечу",
    "не стану писать",
    "не позвоню",
    "не звоню",
    "не отправляю",
)


def _normalize(text: str) -> str:
    return (text or "").strip().lower()


# Лёгкая русская морфология для сопоставления названий локаций: слово
# сводится к основе (без падежных окончаний), чтобы "Кухня" из действия
# соответствовало "на кухню" в тексте реплики. Эвристика только для анализа
# текста (И4: действия не извлекаются из текста).
_CASE_ENDINGS = (
    "ами", "ями", "ах", "ях", "ом", "ем", "ой", "ей", "ам", "ям",
    "а", "я", "у", "ю", "е", "ы", "и", "о", "ь",
)


def _stem(word: str) -> str:
    w = word
    for ending in _CASE_ENDINGS:
        if len(w) > len(ending) + 1 and w.endswith(ending):
            return w[: -len(ending)]
    return w


def _stem_in_text(stem: str, low_text: str) -> bool:
    if len(stem) <= 3:
        return stem in low_text
    return any(_stem(word) == stem for word in re.findall(r"[а-яё]+", low_text))


# Отрицание глагола с допуском коротких вставок: "не буду тебе писать",
# "не стану я писать" и т.п. (маркер отрицания + не более ~14 символов до
# глагола). Используется только для анализа текста реплики, не для извлечения
# действий (И4).
_MESSAGE_NEG_RE = re.compile(
    r"(?:не\s+буду|не\s+стану|не\s+хочу|не\s+собираюсь|не)"
    r"[^.!?]{0,14}"
    r"(?:писать|напишу|отвечу|позвоню|звоню|отправляю|скину|передам|сброшу)"
)


def _text_contradicts(turn: schemas.TurnOutput, text: str) -> bool:
    low = _normalize(text)
    if not low:
        return False
    for action in turn.actions:
        if action.type == "move_to":
            if any(p in low for p in _MOVE_NEGATIVE):
                return True
        elif action.type == "send_message":
            if any(p in low for p in _MESSAGE_NEGATIVE):
                return True
            if _MESSAGE_NEG_RE.search(low):
                return True
    return False


def _action_reflected(
    action: schemas.Action,
    low_text: str,
    location_names: tuple[str, ...],
    any_location_mentioned: bool,
) -> bool:
    if action.type == "move_to":
        target = _normalize(action.location)
        if target and _stem_in_text(_stem(target), low_text):
            return True
        for name in location_names:
            if name and _stem_in_text(_stem(name), low_text):
                return True
        if any_location_mentioned:
            # в тексте уже упомянута конкретная локация (своя или чужая) —
            # глагол движения не засчитываем без упоминания этой локации
            return False
        return any(v in low_text for v in _MOVE_VERBS)
    if action.type == "send_message":
        message = _normalize(action.message)
        if message and message in low_text:
            return True
        return any(v in low_text for v in _MESSAGE_VERBS)
    return False


def _location_mentioned(
    turn: schemas.TurnOutput, low_text: str, location_names: tuple[str, ...]
) -> bool:
    for action in turn.actions:
        if action.type == "move_to":
            target = _normalize(action.location)
            if target and _stem_in_text(_stem(target), low_text):
                return True
    return any(
        name and _stem_in_text(_stem(name), low_text) for name in location_names
    )


def classify_consistency(
    turn: schemas.TurnOutput | None,
    text: str,
    location_names: tuple[str, ...] = (),
) -> ConsistencyVerdict:
    """Consistency Validator (WPE.md §5.2, переопределён Ул.1/И6).

    - `no_actions` — действий нет (нечего проверять);
    - `contradiction` — текст активно отрицает действие (ретрай ≤1, затем
      отклонение + ремарка);
    - `consistent` — действие обыграно в тексте (ремарка не нужна);
    - `minor_ambiguity` — молчаливое действие: применяется без ретрая, System
      Narrator вставляет ремарку (И16).
    """
    if turn is None or not turn.actions:
        return "no_actions"
    if _text_contradicts(turn, text):
        return "contradiction"
    low = _normalize(text)
    any_location_mentioned = _location_mentioned(turn, low, location_names)
    for action in turn.actions:
        if not _action_reflected(
            action, low, location_names, any_location_mentioned
        ):
            return "minor_ambiguity"
    return "consistent"


def reflected_action_indices(
    turn: schemas.TurnOutput | None,
    text: str,
    location_names: tuple[str, ...] = (),
) -> set[int]:
    """Индексы действий (в порядке `turn.actions`), отражённых в тексте реплики."""
    if turn is None:
        return set()
    low = _normalize(text)
    any_location_mentioned = _location_mentioned(turn, low, location_names)
    return {
        index
        for index, action in enumerate(turn.actions)
        if _action_reflected(action, low, location_names, any_location_mentioned)
    }


def describe_actions(turn: schemas.TurnOutput | None) -> str:
    """Человекочитаемое описание действий (для логов и фидбека)."""
    if turn is None or not turn.actions:
        return "нет действий"
    parts = []
    for action in turn.actions:
        if action.type == "move_to":
            parts.append(f"move_to -> '{action.location or ''}'")
        elif action.type == "send_message":
            parts.append(
                f"send_message -> targets={list(action.target_character_ids)} "
                f"channel={action.channel}"
            )
        else:
            parts.append(str(action.type))
    return "; ".join(parts)


def build_consistency_feedback(
    turn: schemas.TurnOutput | None,
    text: str,
    character_name: str,
) -> str:
    """Промпт-фидбек для contradiction-ретрая (в рамках существующего бюджета).

    Просит модель привести текст в соответствие с заявленными действиями либо
    убрать действия. Уложен в один блок, как repetition feedback.
    """
    snippet = (text or "").strip()
    if len(snippet) > 200:
        snippet = snippet[:200] + "…"
    return (
        "ОБНАРУЖЕНО ПРОТИВОРЕЧИЕ МЕЖДУ ДЕЙСТВИЯМИ И ТЕКСТОМ РЕПЛИКИ.\n\n"
        f"{character_name} заявил(а) действия: {describe_actions(turn)}.\n"
        f"Но текст реплики говорит: «{snippet}», что прямо противоречит действию.\n"
        "Перепиши реплику так, чтобы она соответствовала действиям, ЛИБО убери "
        "противоречащие действия. Никогда не описывай от первого лица действия "
        "других персонажей."
    )


def narrator_remark_for_move(
    character_name: str, from_location: str, to_location: str
) -> str:
    """Детерминированная ремарка перемещения (пример §5.10/И16)."""
    from_loc = _normalize(from_location)
    if from_loc:
        return (
            f"*[Система: {character_name} покидает '{from_location}' и переходит "
            f"в '{to_location}']*"
        )
    return f"*[Система: {character_name} перемещается в '{to_location}']*"


def narrator_remark_for_send(character_name: str) -> str:
    """Ремарка отправленного сообщения (Phase 6 формализует threads)."""
    return f"*[Система: {character_name} отправляет сообщение]*"


def narrator_remark_for_rejection(
    character_name: str, action_type: str
) -> str:
    """Ремарка отклонённого действия (contradiction, §5.2)."""
    label = "перемещение" if action_type == "move_to" else "действие"
    return f"*[Система: {character_name} не совершает заявленное {label}]*"


def build_narrator_remarks(
    character_name: str,
    turn: schemas.TurnOutput | None,
    verdict: ConsistencyVerdict,
    applied_moves: list[dict],
    applied_messages: list[dict],
    reflected: set[int],
    *,
    rejected: list[dict] | None = None,
) -> list[str]:
    """Список ремарок System Narrator (И16, §5.10).

    - `contradiction` → ремарка об отклонении (действия не применяются);
    - иначе для каждого применённого действия, НЕ отражённого в тексте реплики
      (`reflected`), движок сам вставляет ремарку из `WorldEvent`. Текст реплики
      при этом не редактируется.
    """
    remarks: list[str] = []
    if verdict == "contradiction":
        for item in rejected or []:
            remarks.append(
                narrator_remark_for_rejection(
                    character_name, item.get("type", "action")
                )
            )
        return remarks
    for move in applied_moves:
        if move.get("action_index") not in reflected:
            remarks.append(
                narrator_remark_for_move(
                    character_name,
                    move.get("location_from", ""),
                    move.get("location_to", ""),
                )
            )
    for message in applied_messages:
        if message.get("action_index") not in reflected:
            remarks.append(narrator_remark_for_send(character_name))
    return remarks
