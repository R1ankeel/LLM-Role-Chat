"""Story Consolidation (Plans/update20.md §17, Sprint 9).

LLM-обновление Current Story State с валидацией:

- **trigger (§17.1)**: пост-раунд, если с последней консолидации прошло >=
  ``story_consolidation_interval_rounds`` раундов ИЛИ критическое событие
  (importance >= ``story_consolidation_critical_importance``) затронуло окно;
- **вход → LLM → выход (§17.2)**: Original Plot + Current Story State +
  Recent Story Events → Updated Current Story State (JSON по схеме,
  ``format`` при вызове Ollama);
- **валидация и защита (§17.3)**:
  - *Original Plot diff*: consolidation НЕ пишет ``original_plot`` (нет
    write-path); смена фазы применяется ТОЛЬКО если новая фаза зарегистрирована
    в Original Plot (иначе остаётся предложением для пользователя);
  - *Hallucination guard*: новые/архивированные линии, прогресс и цели —
    только при подтверждении в окне ``story_events`` (grounding);
  - *confidence*: поле ниже ``story_consolidation_min_confidence`` не применяется;
  - *Rollback*: при невалидном JSON/нарушении правил — предыдущая версия
    ``story_states`` остаётся, ``version`` не растёт, ошибка логируется.

Под **benchmark gate (§27)**: перед включением обязателен прогон
``benchmark_structured`` на story-update; при schema-validity < 90% или
grounding < порога — только кандидаты-флаги без применения (флаг
``story_consolidation_enabled`` остаётся выключенным).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Awaitable, Callable

from .. import crud
from ..config import settings

logger = logging.getLogger(__name__)

# ----------------------------- JSON-schema контракт -----------------------------

CONSOLIDATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [],
    "properties": {
        "completed_goals": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "confidence"],
                "properties": {
                    "name": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "additionalProperties": False,
            },
        },
        "progress": {
            "type": "object",
            "required": ["overall", "confidence"],
            "properties": {
                "overall": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "additionalProperties": False,
        },
        "new_threads": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "confidence"],
                "properties": {
                    "name": {"type": "string"},
                    "actors": {"type": "array", "items": {"type": "string"}},
                    "importance": {"type": "integer", "minimum": 1, "maximum": 10},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "additionalProperties": False,
            },
        },
        "updated_threads": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "confidence"],
                "properties": {
                    "name": {"type": "string"},
                    "progress": {
                        "type": ["number", "null"],
                        "minimum": 0,
                        "maximum": 1,
                    },
                    "importance": {
                        "type": ["integer", "null"],
                        "minimum": 1,
                        "maximum": 10,
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "additionalProperties": False,
            },
        },
        "archived_threads": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "confidence"],
                "properties": {
                    "name": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "additionalProperties": False,
            },
        },
        "character_state_changes": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["character", "confidence"],
                "properties": {
                    "character": {"type": "string"},
                    "role": {"type": ["string", "null"]},
                    "notes": {"type": ["string", "null"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "additionalProperties": False,
            },
        },
        "phase_change": {
            "type": ["object", "string", "null"],
            "properties": {
                "phase": {"type": ["string", "null"]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "additionalProperties": False,
        },
        "summary": {
            "type": ["object", "string", "null"],
            "properties": {
                "text": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
}

# ----------------------------- парсинг -----------------------------


def _parse_consolidation_json(content: str) -> Any:
    """Распарсить JSON из ответа LLM (без markdown-обёрток)."""
    text = (content or "").strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fence:
        text = fence.group(1).strip()
    return json.loads(text)


# ----------------------------- валидатор контракта -----------------------------


def _clean_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _as_float(value: Any, lo: float = 0.0, hi: float = 1.0, default: Any = None) -> Any:
    if isinstance(value, bool):
        return default
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, num))


def _as_int(value: Any, lo: int, hi: int, default: Any = None) -> Any:
    if isinstance(value, bool):
        return default
    try:
        num = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, num))


def _name_conf(item: Any) -> dict | None:
    if not isinstance(item, dict):
        return None
    name = _clean_str(item.get("name"))
    conf = _as_float(item.get("confidence"))
    if not name or conf is None:
        return None
    return {"name": name, "confidence": conf}


def _new_thread_item(item: Any) -> dict | None:
    base = _name_conf(item)
    if base is None:
        return None
    actors = item.get("actors")
    if not isinstance(actors, list):
        actors = []
    actors = [str(a).strip() for a in actors if str(a).strip()]
    importance = _as_int(item.get("importance"), 1, 10, default=5)
    return {**base, "actors": actors, "importance": importance}


def _updated_thread_item(item: Any) -> dict | None:
    base = _name_conf(item)
    if base is None:
        return None
    progress = _as_float(item.get("progress")) if item.get("progress") is not None else None
    importance = (
        _as_int(item.get("importance"), 1, 10)
        if item.get("importance") is not None
        else None
    )
    return {**base, "progress": progress, "importance": importance}


def _char_change_item(item: Any) -> dict | None:
    if not isinstance(item, dict):
        return None
    character = _clean_str(item.get("character"))
    conf = _as_float(item.get("confidence"))
    if not character or conf is None:
        return None
    role = item.get("role")
    notes = item.get("notes")
    return {
        "character": character,
        "confidence": conf,
        "role": _clean_str(role) if role is not None else None,
        "notes": _clean_str(notes) if notes is not None else None,
    }


def _norm_phase_change(value: Any) -> dict | None:
    if isinstance(value, str):
        phase = value.strip()
        return {"phase": phase or None, "confidence": 1.0}
    if isinstance(value, dict):
        conf = _as_float(value.get("confidence"))
        if conf is None:
            return None
        phase = value.get("phase")
        return {
            "phase": _clean_str(phase) if phase is not None else None,
            "confidence": conf,
        }
    return None


def _norm_summary(value: Any) -> dict | None:
    if isinstance(value, str):
        text = value.strip()
        return {"text": text, "confidence": 1.0} if text else None
    if isinstance(value, dict):
        conf = _as_float(value.get("confidence"))
        if conf is None:
            return None
        text = _clean_str(value.get("text"))
        return {"text": text, "confidence": conf} if text else None
    return None


def validate_consolidation_result(result: Any) -> dict | None:
    """Нормализовать/проверить результат LLM по контракту (§17.2).

    Невалидные элементы списков отбрасываются поштучно; невалидная верхняя
    структура → None (rollback: предыдущая версия остаётся).
    """
    if not isinstance(result, dict):
        return None
    out: dict[str, Any] = {}

    goals = result.get("completed_goals")
    if isinstance(goals, list):
        items = [g for g in (_name_conf(x) for x in goals) if g is not None]
        if items:
            out["completed_goals"] = items

    progress = result.get("progress")
    if isinstance(progress, dict):
        conf = _as_float(progress.get("confidence"))
        overall = _as_float(progress.get("overall"))
        if conf is not None and overall is not None:
            out["progress"] = {"overall": overall, "confidence": conf}

    new_threads = result.get("new_threads")
    if isinstance(new_threads, list):
        items = [t for t in (_new_thread_item(x) for x in new_threads) if t is not None]
        if items:
            out["new_threads"] = items

    updated_threads = result.get("updated_threads")
    if isinstance(updated_threads, list):
        items = [
            t for t in (_updated_thread_item(x) for x in updated_threads) if t is not None
        ]
        if items:
            out["updated_threads"] = items

    archived_threads = result.get("archived_threads")
    if isinstance(archived_threads, list):
        items = [
            t for t in (_name_conf(x) for x in archived_threads) if t is not None
        ]
        if items:
            out["archived_threads"] = items

    csc = result.get("character_state_changes")
    if isinstance(csc, list):
        items = [c for c in (_char_change_item(x) for x in csc) if c is not None]
        if items:
            out["character_state_changes"] = items

    if "phase_change" in result:
        pc = _norm_phase_change(result.get("phase_change"))
        if pc is not None:
            out["phase_change"] = pc

    if "summary" in result:
        sm = _norm_summary(result.get("summary"))
        if sm is not None:
            out["summary"] = sm

    return out


# ----------------------------- grounding / защита -----------------------------

_STOPWORDS = {
    "и", "в", "во", "на", "с", "со", "для", "о", "об", "из", "за", "по", "при",
    "к", "ко", "у", "не", "что", "это", "как", "так", "же", "ли", "бы", "то", "от",
    "до", "под", "над", "перед", "между", "через", "после", "когда", "если",
    "чтобы", "но", "а", "или", "ни", "я", "ты", "он", "она", "они", "мы", "вы",
    "моя", "мое", "мой", "их", "его", "её", "нам", "вас", "все", "вся", "весь",
    "этот", "эта", "этот", "того", "тому", "там", "тут", "где", "кто", "чего",
    "очень", "только", "уже", "ещё", "вот", "теперь", "потом", "потому",
    "поэтому", "себя", "свой", "своя", "свое", "который", "которая", "которые",
    "также", "чем", "тем", "есть", "был", "была", "было", "были", "быть",
    "будет", "будут", "сказал", "сказала", "сказать", "говорит", "говорил",
    "решил", "решила", "стал", "стала", "стало", "мог", "могла", "могли",
    "хотел", "хотела", "знает", "знал", "знала", "видит", "видел", "видела",
}

_TOKEN_RE = re.compile(r"[а-яёa-z0-9_]{4,}")


def _significant_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for match in _TOKEN_RE.findall((text or "").casefold()):
        if match not in _STOPWORDS:
            tokens.add(match)
    return tokens


def _thread_grounded(name: str, actors: list | None, events: list[dict]) -> bool:
    """Hallucination guard (§17.3): линия подтверждена в окне story_events.

    По актёрам (точное имя в тексте события) или по значимым токенам имени.
    """
    joined = " ".join(str(ev.get("event") or "") for ev in events).casefold()
    for actor in actors or []:
        actor_str = str(actor).strip()
        if actor_str and actor_str.casefold() in joined:
            return True
    tokens = _significant_tokens(name)
    if not tokens:
        return False
    return any(token in joined for token in tokens)


def _character_known(
    name: str, existing_characters: dict, events: list[dict]
) -> bool:
    """Персонаж известен (в current_story.characters или в окне событий)."""
    clean = name.strip()
    if not clean:
        return False
    if clean in existing_characters:
        return True
    joined = " ".join(str(ev.get("event") or "") for ev in events).casefold()
    return clean.casefold() in joined


def _resolve_phase(
    proposed: str | None, current: str, original_plot: str
) -> str | None:
    """Смена фазы — только если фаза зарегистрирована в original_plot (§16.4).

    Новая/незарегистрированная фаза остаётся предложением (решает пользователь).
    """
    if not proposed or not proposed.strip():
        return None
    phase = proposed.strip()
    if phase.casefold() == (current or "").strip().casefold():
        return phase
    if original_plot and phase.casefold() in str(original_plot).casefold():
        return phase
    logger.info(
        "Story consolidation: фаза %r не зарегистрирована в original_plot — "
        "оставлена как предложение пользователю",
        phase,
    )
    return None


# ----------------------------- LLM-вызов -----------------------------


async def _invoke_consolidation(
    client: Any, model_name: str, messages: list[dict[str, str]]
) -> str | None:
    """Вызов LLM через существующий Ollama-клиент (§17.2, низкая T).

    Использует ``format=CONSOLIDATION_SCHEMA`` для надёжного JSON.
    """
    from .. import ollama_client  # локальный импорт: избегаем циклической связи

    timeout = float(settings.story_consolidation_timeout or 60.0)
    schema = CONSOLIDATION_SCHEMA
    temperature = 0.2
    if settings.use_chat_api:
        payload = ollama_client._build_chat_payload(
            model_name,
            messages,
            temperature,
            [],
            stream=False,
            enable_thinking=False,
            format_schema=schema,
        )
        response = await asyncio.wait_for(
            client.post("/api/chat", json=payload), timeout=timeout
        )
        response.raise_for_status()
        data = response.json()
        return (data.get("message", {}) or {}).get("content", "") or None
    prompt = "\n\n".join((m.get("content") or "") for m in messages)
    payload = ollama_client._build_generate_payload(
        model_name,
        prompt,
        temperature,
        [],
        stream=False,
        enable_thinking=False,
        format_schema=schema,
    )
    response = await asyncio.wait_for(
        client.post("/api/generate", json=payload), timeout=timeout
    )
    response.raise_for_status()
    data = response.json()
    return data.get("response", "") or None


# ----------------------------- применение -----------------------------


def _goal_exists(goals: list, name: str) -> bool:
    return any(
        isinstance(g, str) and g.strip().casefold() == name.strip().casefold()
        for g in goals
    )


async def _apply_consolidation(
    db: Any,
    chat_id: int,
    round_id: str | None,
    state: Any,
    validated: dict,
    rounds: int,
    window: list[dict],
) -> dict:
    """Применить валидированный результат к story_state (§17.3).

    Все изменения проходят grounding и confidence-порог; ``original_plot``
    не пишется; при отсутствии применённых изменений ``version`` не растёт,
    но ``last_consolidation_rounds`` фиксируется (консолидация состоялась).
    """
    min_conf = float(settings.story_consolidation_min_confidence or 0.5)
    new_version = int(getattr(state, "version", 1) or 1) + 1
    current_phase = (getattr(state, "story_phase", "") or "").strip()
    original_plot = getattr(state, "original_plot", "") or ""

    raw_current = getattr(state, "current_story", "{}")
    if isinstance(raw_current, str):
        try:
            parsed = json.loads(raw_current)
        except (json.JSONDecodeError, TypeError):
            parsed = {}
    elif isinstance(raw_current, dict):
        parsed = raw_current
    else:
        parsed = {}
    current = dict(parsed) if isinstance(parsed, dict) else {}

    active_threads = await crud.get_active_story_threads(db, chat_id)
    active_by_name = {
        (t.name or "").strip().casefold(): t
        for t in active_threads
        if (t.name or "").strip()
    }
    completed_goals = list(current.get("completed_goals") or [])
    active_names = [
        n for n in (current.get("active_threads") or []) if isinstance(n, str)
    ]
    thread_progress = dict(current.get("thread_progress") or {})
    characters = dict(current.get("characters") or {})

    counts = {
        "new_threads": 0,
        "updated_threads": 0,
        "archived_threads": 0,
        "completed_goals": 0,
        "characters": 0,
    }
    phase_changed = False
    summary_applied = False
    progress_applied = False

    for goal in validated.get("completed_goals") or []:
        if goal["confidence"] < min_conf:
            continue
        name = goal["name"]
        thread = active_by_name.get(name.casefold())
        if thread is not None:
            await crud.update_story_thread(db, thread.id, status="archived")
            active_by_name.pop(name.casefold(), None)
            active_names = [
                n for n in active_names if n.strip().casefold() != name.casefold()
            ]
        elif not _thread_grounded(name, None, window):
            continue
        if not _goal_exists(completed_goals, name):
            completed_goals.append(name)
        counts["completed_goals"] += 1

    for item in validated.get("new_threads") or []:
        if item["confidence"] < min_conf:
            continue
        name = item["name"]
        if name.casefold() in active_by_name:
            continue
        if not _thread_grounded(name, item.get("actors"), window):
            continue
        existing = await crud.find_story_thread_by_name(db, chat_id, name)
        if existing is not None:
            if existing.status != "active":
                await crud.update_story_thread(db, existing.id, status="active")
            continue
        await crud.create_story_thread(
            db,
            chat_id=chat_id,
            name=name,
            actors=item.get("actors") or [],
            importance=float(item.get("importance") or 5),
            status="active",
        )
        counts["new_threads"] += 1

    for item in validated.get("updated_threads") or []:
        if item["confidence"] < min_conf:
            continue
        name = item["name"]
        thread = await crud.find_story_thread_by_name(db, chat_id, name)
        if thread is None or not _thread_grounded(name, None, window):
            continue
        if item.get("importance") is not None:
            await crud.update_story_thread(db, thread.id, importance=item["importance"])
        if item.get("progress") is not None:
            thread_progress[name] = max(0.0, min(1.0, float(item["progress"])))
        counts["updated_threads"] += 1

    for item in validated.get("archived_threads") or []:
        if item["confidence"] < min_conf:
            continue
        name = item["name"]
        thread = active_by_name.get(name.casefold())
        if thread is None or not _thread_grounded(name, None, window):
            continue
        await crud.update_story_thread(db, thread.id, status="archived")
        active_by_name.pop(name.casefold(), None)
        active_names = [
            n for n in active_names if n.strip().casefold() != name.casefold()
        ]
        counts["archived_threads"] += 1

    for item in validated.get("character_state_changes") or []:
        if item["confidence"] < min_conf:
            continue
        cname = item["character"]
        if not _character_known(cname, characters, window):
            continue
        entry = dict(characters.get(cname) or {})
        if item.get("role") is not None:
            entry["role"] = item["role"]
        if item.get("notes") is not None:
            entry["notes"] = item["notes"]
        if entry:
            characters[cname] = entry
            counts["characters"] += 1

    new_phase: str | None = None
    pc = validated.get("phase_change")
    if pc is not None and pc["confidence"] >= min_conf:
        resolved = _resolve_phase(pc.get("phase"), current_phase, original_plot)
        if resolved is not None and resolved.casefold() != current_phase.casefold():
            new_phase = resolved
            phase_changed = True

    summary = validated.get("summary")
    if (
        summary is not None
        and summary["confidence"] >= min_conf
        and summary.get("text")
    ):
        current["narrative_summary"] = summary["text"][:4000]
        summary_applied = True

    progress = validated.get("progress")
    if progress is not None and progress["confidence"] >= min_conf:
        current.setdefault("progress", {})["overall"] = max(
            0.0, min(1.0, float(progress["overall"]))
        )
        progress_applied = True

    current["active_threads"] = active_names
    if completed_goals:
        current["completed_goals"] = completed_goals
    if thread_progress:
        current["thread_progress"] = thread_progress
    if characters:
        current["characters"] = characters

    applied_any = (
        sum(counts.values()) > 0 or phase_changed or summary_applied or progress_applied
    )
    final_version = new_version if applied_any else int(getattr(state, "version", 1) or 1)

    await crud.update_story_state(
        db,
        chat_id,
        current_story=current,
        story_phase=new_phase if new_phase is not None else None,
        updated_round_id=round_id,
        version=final_version,
        last_consolidation_rounds=rounds,
    )
    return {
        "applied": counts,
        "phase_changed": phase_changed,
        "summary_applied": summary_applied,
        "progress_applied": progress_applied,
        "version": final_version,
    }


# ----------------------------- входные данные -----------------------------


async def _recent_story_events(db: Any, chat_id: int) -> list[dict]:
    """Окно последних story_events для grounding (§17.3)."""
    limit = max(
        1, int(settings.story_consolidation_max_recent_events or 30)
    )
    from .story_state import story_event_to_dict

    rows = await crud.get_story_events_for_chat(db, chat_id, limit=limit)
    return [story_event_to_dict(ev) for ev in rows]


def _format_events(events: list[dict]) -> str:
    lines = []
    for ev in events:
        actors = ", ".join(str(a) for a in (ev.get("actors") or [])) or "(нет)"
        lines.append(
            f"- [{ev.get('round_id') or '?'} | важность {ev.get('importance', 0)}] "
            f"{ev.get('event') or ''} (актёры: {actors})"
        )
    return "\n".join(lines)


# ----------------------------- точка входа -----------------------------


async def maybe_consolidate_story(
    db: Any,
    client: Any,
    *,
    chat_id: int,
    round_id: str | None,
    model_name: str | None = None,
    invoke: Callable[[list[dict]], Awaitable[str | None]] | None = None,
) -> dict:
    """Пост-раундный hook Story Consolidation (§17, Sprint 9).

    Canary: работает только при ``story_consolidation_enabled`` + story включён
    (глобальный + перчатовый). Trigger — интервал в раундах ИЛИ критическое
    событие. ``invoke`` — тестовая инъекция LLM-вызова (messages → str).
    Любой сбой не роняет раунд и не меняет предыдущую версию state (rollback).
    """
    if not settings.story_consolidation_enabled:
        return {"ok": True, "stage": "story_consolidation", "skipped": "flag off"}
    try:
        chat = await crud.get_chat(db, chat_id)
        if chat is None:
            return {
                "ok": True,
                "stage": "story_consolidation",
                "skipped": "no chat",
            }
        if not settings.story_enabled or not getattr(chat, "story_enabled", False):
            return {
                "ok": True,
                "stage": "story_consolidation",
                "skipped": "story disabled",
            }
        state = await crud.get_story_state(db, chat_id)
        if state is None:
            return {
                "ok": True,
                "stage": "story_consolidation",
                "skipped": "no story state",
            }

        rounds = await crud.count_distinct_rounds(db, chat_id)
        last = int(getattr(state, "last_consolidation_rounds", 0) or 0)
        window = await _recent_story_events(db, chat_id)

        critical_threshold = float(
            settings.story_consolidation_critical_importance or 8.0
        )
        critical = any(
            float(ev.get("importance") or 0.0) >= critical_threshold for ev in window
        )
        interval = int(settings.story_consolidation_interval_rounds or 15)
        if not critical and (rounds - last) < interval:
            return {
                "ok": True,
                "stage": "story_consolidation",
                "skipped": "interval not reached",
                "rounds": rounds,
                "last_consolidation_rounds": last,
            }
        trigger = "critical" if critical else "interval"

        from ..prompt_builder import (
            build_story_consolidation_system,
            build_story_consolidation_user,
        )

        current_raw = getattr(state, "current_story", "{}")
        if isinstance(current_raw, str):
            try:
                current_display = json.loads(current_raw)
            except (json.JSONDecodeError, TypeError):
                current_display = {}
        elif isinstance(current_raw, dict):
            current_display = current_raw
        else:
            current_display = {}
        messages = [
            {
                "role": "system",
                "content": build_story_consolidation_system(),
            },
            {
                "role": "user",
                "content": build_story_consolidation_user(
                    getattr(state, "original_plot", "") or "",
                    json.dumps(current_display, ensure_ascii=False, indent=2),
                    _format_events(window),
                ),
            },
        ]

        consolidation_model = (
            settings.story_consolidation_model
            or model_name
            or getattr(chat, "model_name", "")
        ).strip()

        raw: str | None = None
        if invoke is not None:
            try:
                raw = await invoke(messages)
            except Exception as exc:  # noqa: BLE001 — не роняет раунд
                logger.warning(
                    "Story consolidation invoke failed for chat %s: %s", chat_id, exc
                )
                return {
                    "ok": False,
                    "stage": "story_consolidation",
                    "error": f"invoke failed: {exc}",
                    "rolled_back": True,
                }
        elif client is None or not consolidation_model:
            return {
                "ok": True,
                "stage": "story_consolidation",
                "skipped": "no client/model",
                "trigger": trigger,
            }
        else:
            try:
                raw = await _invoke_consolidation(client, consolidation_model, messages)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Story consolidation LLM call failed for chat %s: %s", chat_id, exc
                )
                return {
                    "ok": False,
                    "stage": "story_consolidation",
                    "error": str(exc),
                    "rolled_back": True,
                }

        if not raw or not str(raw).strip():
            return {
                "ok": False,
                "stage": "story_consolidation",
                "error": "empty LLM response",
                "rolled_back": True,
            }
        try:
            result = _parse_consolidation_json(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            logger.warning(
                "Story consolidation: невалидный JSON для chat %s — rollback",
                chat_id,
            )
            return {
                "ok": False,
                "stage": "story_consolidation",
                "error": "invalid JSON",
                "rolled_back": True,
            }
        validated = validate_consolidation_result(result)
        if validated is None:
            logger.warning(
                "Story consolidation: результат не прошёл валидацию для chat %s — "
                "rollback",
                chat_id,
            )
            return {
                "ok": False,
                "stage": "story_consolidation",
                "error": "schema invalid",
                "rolled_back": True,
            }

        applied = await _apply_consolidation(
            db, chat_id, round_id, state, validated, rounds, window
        )
        logger.info(
            "[chat_id=%d] Story consolidation (trigger=%s) applied=%s version=%d",
            chat_id,
            trigger,
            applied["applied"],
            applied["version"],
        )
        return {
            "ok": True,
            "stage": "story_consolidation",
            "trigger": trigger,
            "rounds": rounds,
            "applied": applied["applied"],
            "phase_changed": applied["phase_changed"],
            "summary_applied": applied["summary_applied"],
            "progress_applied": applied["progress_applied"],
            "version": applied["version"],
        }
    except Exception as exc:  # noqa: BLE001 — любой сбой не роняет раунд
        logger.warning("Story consolidation failed for chat %s: %s", chat_id, exc)
        return {
            "ok": False,
            "stage": "story_consolidation",
            "error": str(exc),
            "rolled_back": True,
        }
