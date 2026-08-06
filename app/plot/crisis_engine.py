"""Crisis Engine — мягкое обнаружение кризисов (Plans/update20.md §19, Sprint 11).

Принцип §19: не заставлять сюжет развиваться искусственно, а обнаруживать
естественные критические точки. Запрещён паттерн ``if trust<30: force_argument``:
кризис — ВЕРОЯТНОСТЬ (давление в контексте + мягкий boost proactive), не команда.

Pipeline (детерминированный, без LLM по умолчанию):

    STORY PRESSURE = w_base × plot_pressure(issues/goals/stagnation/recent)
                   + w_trajectory × (resentment/jealousy растут, trust/affection падают)
                   + w_beliefs × (конфликт убеждений)

    CRISIS CANDIDATE (правила) = pressure ≥ порога И неразрешённый старый
        конфликт (open issue без упоминания ≥ N раундов) И пара взаимодействует

    CRISIS EVALUATION (LLM, JSON-schema, мягко) = ТОЛЬКО при
        ``crisis_evaluation_enabled`` (benchmark gate §27); иначе — type из
        правил, без LLM.

    CRISIS RESOLUTION (детерминированная) = мягко: story_event + story_thread
        «Кризис: ...»; кандидат повышает attention/pressure и шанс
        proactive-action (boost) у вовлечённых персонажей; никаких
        форсированных аргументов.

Write-path: ``story_events`` + ``story_threads`` (используются, новых таблиц
нет); флаг ``crisis_engine_enabled`` (canary). Падение не роняет раунд.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterable
from typing import Any

from .. import crud
from ..config import settings
from . import plot_pressure

logger = logging.getLogger(__name__)

# Типы кризиса (§19 CRISIS EVALUATION).
CRISIS_TYPES = frozenset(
    {
        "direct_conflict",
        "admission",
        "question",
        "discovery",
        "third_party",
        "world_event",
        "secret_hiding",
        "departure",
        "goal_change",
    }
)

# JSON-schema для LLM-оценки кризиса (формат Ollama, benchmark gate §27).
CRISIS_EVALUATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "candidate": {"type": "boolean"},
        "type": {
            "type": "string",
            "enum": sorted(CRISIS_TYPES),
        },
        "confidence": {"type": "number"},
    },
    "required": ["candidate", "type", "confidence"],
}


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _parse_actors(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(a) for a in raw]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return [str(a) for a in parsed] if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []
    return []


# ---------------------------------------------------------------------------
# STORY PRESSURE (§19)
# ---------------------------------------------------------------------------


def _event_delta(event: Any, key: str) -> float:
    """Дельта из relationship event (dict из crud или ORM-объект)."""
    if isinstance(event, dict):
        value = event.get(key)
    else:
        value = getattr(event, key, None)
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def trajectory_score_from_events(events: Iterable[Any]) -> float:
    """0..1: отрицательная траектория отношений (§19 w_trajectory).

    Из relationship events раунда (``delta_*``): рост resentment/jealousy и
    падение trust/affection дают вклад. Усреднение по событиям с отрицательным
    балансом (нейтральные/позитивные не тянут score вниз). Детерминированно.
    """
    total = 0.0
    count = 0
    for event in events or []:
        resent = _event_delta(event, "delta_resentment")
        jealousy = _event_delta(event, "delta_jealousy")
        trust = _event_delta(event, "delta_trust")
        affection = _event_delta(event, "delta_affection")
        negativity = (resent + jealousy) - (trust + affection)
        if negativity <= 0:
            continue
        total += min(1.0, negativity)
        count += 1
    if count == 0:
        return 0.0
    return _clamp01(total / count)


def beliefs_conflict_score(beliefs: Iterable[Any]) -> float:
    """0..1: конфликт убеждений (§19 w_beliefs).

    Доля suspicion/конфликтных убеждений с высокой уверенностью среди всех
    убеждений чата. Простая детерминированная мера: 3+ таких убеждения = 1.
    """
    bels = list(beliefs or [])
    if not bels:
        return 0.0
    conflicts = 0
    for belief in bels:
        btype = (getattr(belief, "type", "") or "").strip()
        confidence = float(getattr(belief, "confidence", 0.5) or 0.5)
        if btype == "suspicion" and confidence >= 0.6:
            conflicts += 1
        elif btype in ("conflict", "contradiction") and confidence >= 0.5:
            conflicts += 1
    return _clamp01(conflicts / 3.0)


def compute_crisis_pressure(
    *,
    base_pressure: float = 0.0,
    trajectory: float = 0.0,
    beliefs_conflict: float = 0.0,
    weights: dict[str, float] | None = None,
) -> float:
    """Совокупный crisis pressure 0..1 (§19, 6 компонентов).

    ``base_pressure`` — ``plot_pressure.compute_story_pressure`` (issues/goals/
    stagnation/recent); ``trajectory`` — отрицательная траектория отношений;
    ``beliefs_conflict`` — конфликт убеждений. Взвешенная сумма, веса
    нормируются; пустой вклад пропускается (не «съедает» долю).
    """
    weights = weights or {
        "base": settings.crisis_weight_base,
        "trajectory": settings.crisis_weight_trajectory,
        "beliefs": settings.crisis_weight_beliefs,
    }
    components = {
        "base": _clamp01(base_pressure),
        "trajectory": _clamp01(trajectory),
        "beliefs": _clamp01(beliefs_conflict),
    }
    total = 0.0
    wsum = 0.0
    for name, weight in weights.items():
        if weight <= 0:
            continue
        total += float(weight) * components.get(name, 0.0)
        wsum += float(weight)
    if wsum <= 0:
        return 0.0
    return _clamp01(total / wsum)


# ---------------------------------------------------------------------------
# CRISIS CANDIDATE (§19, правила, детерминированные)
# ---------------------------------------------------------------------------


def opposing_intents(
    char_id_a: int,
    char_id_b: int,
    intents_a: list | None = None,
    intents_b: list | None = None,
) -> bool:
    """Противоположные интенты пары (§19): A нацелен на B и B нацелен на A."""
    targets_a = {
        int(getattr(i, "target", 0))
        for i in (intents_a or [])
        if getattr(i, "target", None) is not None
    }
    targets_b = {
        int(getattr(i, "target", 0))
        for i in (intents_b or [])
        if getattr(i, "target", None) is not None
    }
    return char_id_b in targets_a and char_id_a in targets_b


def build_crisis_candidate(
    *,
    pressure: float,
    open_issues: list,
    interaction_rounds: int = 0,
    opposing: bool = False,
    issue_edges: dict | None = None,
    threshold: float | None = None,
    min_issue_age_rounds: int | None = None,
) -> dict | None:
    """Детерминированные правила кандидата (§19).

    Кандидат ТОЛЬКО если: ``pressure >= threshold`` И есть неразрешённый
    старый конфликт (open issue с ``rounds_since_last_mention >= порога``) И
    пара взаимодействует (``interaction_rounds >= 1``). Возвращает dict
    {pressure, type, characters, issue_text, trigger} или None. Кризис —
    вероятность, не команда: кандидат лишь повышает давление/boost.
    """
    threshold = (
        settings.crisis_pressure_threshold
        if threshold is None
        else float(threshold)
    )
    min_issue_age_rounds = (
        settings.crisis_min_issue_age_rounds
        if min_issue_age_rounds is None
        else int(min_issue_age_rounds)
    )
    issues = list(open_issues or [])
    stale = [
        issue
        for issue in issues
        if int(getattr(issue, "rounds_since_last_mention", 0) or 0)
        >= min_issue_age_rounds
    ]
    if pressure < threshold or not stale or int(interaction_rounds) < 1:
        return None

    top = stale[0]
    top_id = getattr(top, "id", None)
    edge = (issue_edges or {}).get(top_id)
    characters = [int(c) for c in (edge or []) if c is not None]
    characters = list(dict.fromkeys(characters))

    issue_text = (getattr(top, "text", "") or "").strip()[:300]
    return {
        "pressure": round(pressure, 3),
        "type": "direct_conflict" if opposing else "discovery",
        "confidence": 0.0,
        "characters": characters,
        "issue_text": issue_text,
        "trigger": {
            "threshold": threshold,
            "issue_rounds_since_last_mention": int(
                getattr(top, "rounds_since_last_mention", 0) or 0
            ),
            "interaction_rounds": int(interaction_rounds),
            "opposing_intents": bool(opposing),
        },
    }


# ---------------------------------------------------------------------------
# CRISIS EVALUATION (LLM, benchmark gate §27)
# ---------------------------------------------------------------------------


def validate_crisis_evaluation(result: Any) -> dict | None:
    """Нормализация/валидация LLM-оценки кризиса (§27, JSON-schema).

    Возвращает {candidate, type, confidence} или None при невалидной
    верхней структуре. Невалидные значения поштучно подставляются дефолтами.
    """
    if isinstance(result, dict):
        raw = result
    elif isinstance(result, str):
        try:
            raw = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return None
    else:
        return None
    if not isinstance(raw, dict):
        return None
    candidate = bool(raw.get("candidate"))
    ctype = str(raw.get("type") or "")
    if ctype not in CRISIS_TYPES:
        ctype = "discovery"
    try:
        confidence = float(raw.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = _clamp01(confidence)
    return {
        "candidate": candidate,
        "type": ctype,
        "confidence": confidence,
    }


async def _evaluate_crisis_llm(
    client: Any,
    db: Any,
    chat_id: int,
    model_name: str,
    candidate: dict,
    round_id: str | None,
) -> dict:
    """LLM-оценка кризиса (JSON-schema, мягко). Только под ``crisis_evaluation_enabled``.

    По образцу ``story_consolidation._invoke_consolidation``: ``format=schema``,
    низкая температура. Невалидный ответ деградирует в детерминированный
    кандидат (candidate=True, type из правил, confidence=0.0).
    """
    from .. import ollama_client  # локальный импорт: избегаем циклической связи

    messages = [
        {
            "role": "system",
            "content": (
                "Ты — детектор кризисов в истории. По данным сюжета оцени, "
                "является ли текущая ситуация естественной кризисной точкой. "
                "Верни строго JSON {candidate, type, confidence}. "
                "Тип из: " + ", ".join(sorted(CRISIS_TYPES)) + ". "
                "Кризис — вероятность, не команда: не форсируй события."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "chat_id": chat_id,
                    "round_id": round_id,
                    "pressure": candidate.get("pressure", 0.0),
                    "candidate_type_by_rules": candidate.get("type", "discovery"),
                    "issue_text": candidate.get("issue_text", ""),
                    "trigger": candidate.get("trigger", {}),
                },
                ensure_ascii=False,
            ),
        },
    ]
    timeout = float(settings.ollama_timeout or 180.0)
    temperature = 0.2
    schema = CRISIS_EVALUATION_SCHEMA
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
        async with ollama_client.llm_request(model_name, "/api/chat"):
            response = await asyncio.wait_for(
                client.post("/api/chat", json=payload), timeout=timeout
            )
        response.raise_for_status()
        data = response.json()
        content = (data.get("message", {}) or {}).get("content", "") or None
    else:
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
        async with ollama_client.llm_request(model_name, "/api/generate"):
            response = await asyncio.wait_for(
                client.post("/api/generate", json=payload), timeout=timeout
            )
        response.raise_for_status()
        data = response.json()
        content = data.get("response", "") or None

    evaluated = validate_crisis_evaluation(content)
    if evaluated is None:
        logger.warning(
            "[chat_id=%d] Crisis LLM evaluation returned invalid result; "
            "using deterministic candidate",
            chat_id,
        )
        return candidate
    if not evaluated["candidate"]:
        return None
    evaluated["pressure"] = candidate.get("pressure", 0.0)
    evaluated["characters"] = candidate.get("characters", [])
    evaluated["issue_text"] = candidate.get("issue_text", "")
    evaluated["trigger"] = candidate.get("trigger", {})
    return evaluated


# ---------------------------------------------------------------------------
# CRISIS RESOLUTION (детерминированная, мягко)
# ---------------------------------------------------------------------------


async def _apply_crisis_softly(
    db: Any,
    chat_id: int,
    round_id: str | None,
    candidate: dict,
    character_names: dict,
) -> dict | None:
    """Мягкое применение (§19 resolution): story_event + story_thread «Кризис».

    Кандидат НЕ применяется напрямую: пишется сюжетная линия кризиса, которая
    поднимает давление/boost вовлечённых персонажей. Никаких форсированных
    аргументов. Идемпотентно в рамках раунда (повторный story_event кризиса
    за тот же раунд не дублируется).
    """
    prefix = (settings.crisis_thread_prefix or "Кризис").strip()
    issue_text = (candidate.get("issue_text") or "").strip()
    name = f"{prefix}: {issue_text}"[:100].strip() or prefix

    actors = [
        character_names.get(int(cid), str(cid))
        for cid in (candidate.get("characters") or [])
    ]
    actors = list(dict.fromkeys(a for a in actors if a))

    thread = await crud.find_story_thread_by_name(db, chat_id, name)
    if thread is None:
        thread = await crud.create_story_thread(
            db,
            chat_id=chat_id,
            name=name,
            actors=actors,
            importance=float(settings.crisis_event_importance),
            created_round_id=round_id,
        )
    else:
        await crud.update_story_thread(
            db,
            thread.id,
            importance=float(settings.crisis_event_importance),
            actors=actors,
        )

    recent = await crud.get_story_events_for_chat(db, chat_id, limit=50)
    already = any(
        (ev.round_id == round_id) and (ev.event or "").strip().startswith(prefix)
        for ev in recent
    )
    if not already:
        await crud.create_story_event(
            db,
            event_id=None,
            chat_id=chat_id,
            round_id=round_id,
            event=name,
            actors=actors,
            location="",
            cause=issue_text,
            consequences="",
            importance=float(settings.crisis_event_importance),
            story_thread_id=thread.id,
        )
    return {
        "thread_id": thread.id,
        "name": name,
        "actors": actors,
        "boost": float(settings.crisis_boost_cap),
    }


# ---------------------------------------------------------------------------
# Soft boost + context block (read-path)
# ---------------------------------------------------------------------------


async def compute_crisis_boost(db: Any, chat_id: int, character: Any) -> float:
    """Мягкий boost (0..cap) для proactive-action вовлечённого персонажа (§19).

    Активный кризис-поток, в котором участвует персонаж (actors), повышает
    шанс proactive action — вероятность, не команда. No-op при выключенном
    флаге. Падение не роняет генерацию (возвращает 0.0).
    """
    if not settings.crisis_engine_enabled:
        return 0.0
    try:
        prefix = (settings.crisis_thread_prefix or "Кризис").strip().casefold()
        boost = 0.0
        for thread in await crud.get_active_story_threads(db, chat_id):
            name = (getattr(thread, "name", "") or "").strip().casefold()
            if not name.startswith(prefix):
                continue
            if (getattr(character, "name", "") or "") in _parse_actors(
                getattr(thread, "actors", "[]")
            ):
                boost = float(settings.crisis_boost_cap)
                break
        return _clamp01(boost)
    except Exception as exc:  # noqa: BLE001 — boost не роняет генерацию
        logger.warning(
            "[chat_id=%d] Failed to compute crisis boost for %s: %s",
            chat_id, getattr(character, "name", "?"), exc,
        )
        return 0.0


async def build_crisis_block(db: Any, chat_id: int) -> str:
    """CRISIS block (Sprint 11, §19): активный кризис — «давление в контексте».

    Data-only (активные кризис-потоки), НЕ инструкция: персонаж видит кризис
    как факт сюжета, но решает сам. Пусто при выключенном флаге или отсутствии
    активного кризис-потока. Падение не роняет контекст.
    """
    try:
        if not settings.crisis_engine_enabled:
            return ""
        prefix = (settings.crisis_thread_prefix or "Кризис").strip().casefold()
        crisis_threads = [
            thread
            for thread in await crud.get_active_story_threads(db, chat_id)
            if (getattr(thread, "name", "") or "").strip().casefold().startswith(prefix)
        ]
        if not crisis_threads:
            return ""
        lines = [
            f"- {(getattr(t, 'name', '') or '').strip()}"
            for t in crisis_threads
        ]
        return "\n".join(["<crisis data>"] + lines + ["</crisis data>"])
    except Exception as exc:  # noqa: BLE001 — блок не роняет контекст
        logger.warning(
            "Failed to build crisis block for chat %s: %s", chat_id, exc
        )
        return ""


# ---------------------------------------------------------------------------
# Оркестратор (post-round стадия)
# ---------------------------------------------------------------------------


async def _compute_base_pressure(
    db: Any,
    chat_id: int,
    round_id: str | None,
    characters: list | None,
) -> float:
    """Базовая story pressure (§19): issues + goals + stagnation + recent."""
    all_issues: list = []
    if settings.relationship_issues_enabled:
        from ..relationship_service import list_top_open_issues_for_character

        for character in characters or []:
            try:
                all_issues.extend(
                    await list_top_open_issues_for_character(
                        db, chat_id, character.id, limit=5
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "Failed to load open issues for crisis: %s", exc
                )

    has_goal = False
    blocked_plans = 0
    if settings.npc_plans_enabled:
        for character in characters or []:
            try:
                plan = await crud.get_active_npc_plan(db, chat_id, character.id)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Failed to load plan for crisis: %s", exc)
                plan = None
            if plan is not None:
                has_goal = True
                if (getattr(plan, "blocked_by", "") or "").strip():
                    blocked_plans += 1

    recent_intensity = 0.0
    if round_id:
        try:
            round_events = await crud.get_story_round_world_events(db, chat_id, round_id)
            recent_intensity = plot_pressure.recent_intensity_score(
                [float(ev.get("importance") or 0.0) for ev in round_events]
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to load round events for crisis: %s", exc)

    return plot_pressure.compute_story_pressure(
        issues_score=plot_pressure.issues_score_from_issues(all_issues),
        goals_blocked=plot_pressure.goals_blocked_score(
            has_goal=has_goal,
            plan_blocked=blocked_plans > 0,
        ),
        stagnation_rounds=0,
        recent_intensity=recent_intensity,
    )


async def _load_open_issues(
    db: Any, chat_id: int, characters: list | None
) -> tuple[list, dict]:
    """Open issues всех NPC + карта issue.id → (source, target) для кандидата."""
    from ..relationship_service import list_top_open_issues_for_character

    issues: list = []
    edges: dict = {}
    for character in characters or []:
        for issue in await list_top_open_issues_for_character(
            db, chat_id, character.id, limit=5
        ):
            issues.append(issue)
            target = None
            rel_id = getattr(issue, "relationship_id", None)
            if rel_id is not None:
                try:
                    target = await crud.get_relationship_target_id(db, rel_id)
                except Exception as exc:  # noqa: BLE001 — target опционален
                    logger.debug("Failed to resolve issue target: %s", exc)
            edges[getattr(issue, "id", None)] = (character.id, target)
    return issues, edges


async def _load_beliefs(
    db: Any, chat_id: int, characters: list | None
) -> list:
    """Beliefs всех NPC (для beliefs_conflict_score, §19)."""
    if not settings.beliefs_enabled:
        return []
    beliefs: list = []
    for character in characters or []:
        try:
            beliefs.extend(
                await crud.get_beliefs_for_character(
                    db, character.id, top_k=20
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to load beliefs for crisis: %s", exc)
    return beliefs


async def _opposing_intents(
    db: Any, chat_id: int, edges: dict
) -> bool:
    """Есть ли у пары (из issue-рёбер) противоположные интенты (§19)."""
    if not settings.npc_intent_enabled:
        return False
    char_ids = {
        int(c) for pair in edges.values() for c in pair if c is not None
    }
    intents_by_char: dict[int, list] = {}
    for char_id in char_ids:
        try:
            intents_by_char[char_id] = await crud.get_intents_for_character(
                db, chat_id, char_id, limit=3
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to load intents for crisis: %s", exc)
            intents_by_char[char_id] = []
    seen: set[tuple[int, int]] = set()
    for source, target in edges.values():
        if source is None or target is None:
            continue
        pair = (min(int(source), int(target)), max(int(source), int(target)))
        if pair in seen:
            continue
        seen.add(pair)
        if opposing_intents(
            pair[0],
            pair[1],
            intents_by_char.get(pair[0], []),
            intents_by_char.get(pair[1], []),
        ):
            return True
    return False


async def _interaction_rounds(db: Any, chat_id: int, edges: dict) -> int:
    """Max число раундов взаимодействия среди пар из issue-рёбер (§19)."""
    best = 0
    seen: set[tuple[int, int]] = set()
    for source, target in edges.values():
        if source is None or target is None:
            continue
        pair = (int(source), int(target))
        if pair in seen:
            continue
        seen.add(pair)
        try:
            count = await crud.count_pair_interaction_rounds(
                db, chat_id, pair[0], pair[1]
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to count interaction rounds: %s", exc)
            count = 0
        best = max(best, count)
    return best


async def run_crisis_engine(
    db: Any,
    *,
    chat_id: int,
    round_id: str | None,
    characters: list | None = None,
    character_names: dict | None = None,
    client: Any = None,
    model_name: str | None = None,
) -> dict:
    """Post-round crisis stage (Sprint 11, §19).

    Детерминированный pipeline: pressure → candidate → evaluation (LLM только
    под ``crisis_evaluation_enabled``, benchmark gate §27) → resolution (мягко).
    No-op при выключенном ``crisis_engine_enabled``; падение не роняет раунд.
    """
    if not settings.crisis_engine_enabled:
        return {"ok": True, "stage": "crisis", "skipped": "flag off"}
    if not round_id:
        return {"ok": True, "stage": "crisis", "skipped": "no round"}
    try:
        # 1. STORY PRESSURE (§19, 6 компонентов)
        base_pressure = await _compute_base_pressure(
            db, chat_id, round_id, characters
        )
        round_rel_events = await crud.get_relationship_events_for_round(
            db, round_id
        )
        trajectory = trajectory_score_from_events(round_rel_events)
        beliefs_score = beliefs_conflict_score(
            await _load_beliefs(db, chat_id, characters)
        )
        pressure = compute_crisis_pressure(
            base_pressure=base_pressure,
            trajectory=trajectory,
            beliefs_conflict=beliefs_score,
        )

        # 2. CRISIS CANDIDATE (правила, детерминированные)
        open_issues, edges = await _load_open_issues(db, chat_id, characters)
        interaction_rounds = await _interaction_rounds(db, chat_id, edges)
        opposing = await _opposing_intents(db, chat_id, edges)
        candidate = build_crisis_candidate(
            pressure=pressure,
            open_issues=open_issues,
            interaction_rounds=interaction_rounds,
            opposing=opposing,
            issue_edges=edges,
        )
        if candidate is None:
            return {
                "ok": True,
                "stage": "crisis",
                "pressure": round(pressure, 3),
                "candidate": False,
            }

        # 3. CRISIS EVALUATION (LLM только под benchmark gate §27)
        if settings.crisis_evaluation_enabled and client is not None and model_name:
            try:
                evaluated = await _evaluate_crisis_llm(
                    client, db, chat_id, model_name, candidate, round_id
                )
                if evaluated is None:
                    return {
                        "ok": True,
                        "stage": "crisis",
                        "pressure": round(pressure, 3),
                        "candidate": False,
                        "evaluation": "llm_rejected",
                    }
                candidate = evaluated
            except Exception as exc:  # noqa: BLE001 — LLM не должен ронять раунд
                logger.warning("Crisis LLM evaluation failed: %s", exc)
                candidate["confidence"] = 0.0

        # 4. CRISIS RESOLUTION (детерминированная, мягко)
        applied = await _apply_crisis_softly(
            db,
            chat_id,
            round_id,
            candidate,
            character_names or {},
        )
        return {
            "ok": True,
            "stage": "crisis",
            "pressure": round(pressure, 3),
            "candidate": True,
            "type": candidate.get("type"),
            "confidence": candidate.get("confidence", 0.0),
            "thread": applied,
        }
    except Exception as exc:  # noqa: BLE001 — стадия не должна ронять раунд
        logger.warning("Crisis engine stage failed: %s", exc)
        return {"ok": False, "stage": "crisis", "error": str(exc)}
