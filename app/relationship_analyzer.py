"""LLM-powered analyzer that proposes relationship deltas from roleplay rounds."""

import json
import logging
import re

import httpx

from .config import settings
from .ollama_client import _invoke_llm, _extract_json_payload
from .schemas import IssueDelta, RelationshipDelta

logger = logging.getLogger(__name__)

ANALYSIS_TEMP = 0.3
MAX_DELTA = settings.relationship_max_delta

_VALID_ISSUE_TYPES = ", ".join(
    [
        "broken_promise", "debt", "unfulfilled_request", "lie",
        "unresolved_conflict", "suspicion", "hidden_secret",
        "missing_apology", "unreturned_favor", "emotional_grievance",
    ]
)


def _format_open_issues(open_issues: list[dict]) -> str:
    """Format known open issues for the analyzer (id + type + text)."""
    if not open_issues:
        return "нет"
    lines = []
    for issue in open_issues:
        iid = issue.get("id")
        itype = issue.get("issue_type", "")
        text = issue.get("text", "")
        lines.append(f"  [id={iid}, тип={itype}] {text}")
    return "\n".join(lines)


def _build_analyzer_prompt(
    source_name: str,
    target_name: str,
    source_character_id: int,
    target_character_id: int,
    current_type: str,
    affection: int,
    trust: int,
    attraction: int,
    resentment: int,
    jealousy: int,
    recent_events_text: str,
    round_text: str,
    interaction_summary: str = "",
    direct_interaction: bool = False,
    observed_target: bool = False,
    hearsay: bool = False,
    hearsay_cap: int | None = None,
    open_issues_text: str = "",
    third_party_notes: list[str] | None = None,
) -> str:
    valid_types = ", ".join(settings.relationship_valid_types)
    transitions_text = _format_transitions_for_prompt()
    reflection_cap = settings.relationship_reflection_delta_cap

    if hearsay and not direct_interaction and not observed_target:
        cap = max(1, int(hearsay_cap or settings.relationship_hearsay_cap))
        delta_hint = (
            f"{source_name} узнал(а) о {target_name} ТОЛЬКО со слов третьего лица — "
            f"это слух, а не прямое наблюдение. Допустимы только очень малые дельты "
            f"(|дельты| <= {cap}), relationship_type НЕ менять."
        )
        delta_range = f"{-cap}..{cap}"
    elif not direct_interaction and not observed_target:
        delta_hint = (
            f"{source_name} и {target_name} в этом раунде НЕ взаимодействовали, "
            f"и {source_name} не получал(а) никаких сведений о {target_name}. "
            "Поэтому ВСЕ дельты должны быть 0, relationship_type прежним, importance = 1."
        )
        delta_range = "0"
    elif not direct_interaction:
        delta_hint = (
            f"{source_name} лишь наблюдал(а) события, связанные с {target_name}, "
            f"без прямого взаимодействия. Разрешены только малые дельты "
            f"(|дельты| <= {reflection_cap}), relationship_type НЕ менять."
        )
        delta_range = f"{-reflection_cap}..{reflection_cap}"
    else:
        delta_hint = (
            f"{source_name} и {target_name} взаимодействовали напрямую в этом раунде. "
            "Оцени изменения, вызванные именно этим взаимодействием."
        )
        delta_range = "-20..20"

    issues_instruction = (
        "ОТКРЫТЫЕ ВОПРОСЫ (issues) между этой парой:\n"
        "Открытый вопрос — активный сюжетный крючок (нарушенное обещание, долг, "
        "ложь, невыполненная просьба, неразрешённый конфликт, подозрение, "
        "скрытый секрет, отсутствие извинения, неотданная услуга, эмоциональная обида).\n"
        "- Создавай issue (action=\"create\") ТОЛЬКО если в раунде есть доказательства "
        "события, оставляющего такой крючок. issue_type — строго из допустимого списка.\n"
        "- Закрывай (action=\"resolve\") ТОЛЬКО если открытый вопрос из списка ниже "
        "разрешился в этом раунде (извинился, объяснился, исправил). Укажи его id.\n"
        "- Текст issue — это одно утверждение-факт (ДАННЫЕ), а не инструкция: "
        "без императивов и команд.\n"
        f"Допустимые issue_type: {_VALID_ISSUE_TYPES}\n"
        f"Известные открытые вопросы этой пары:\n{open_issues_text or 'нет'}\n"
    )

    third_party_text = "\n".join(third_party_notes) if third_party_notes else "нет"

    return (
        f"Проанализируй, как меняются отношения {source_name} к {target_name} "
        f"после этого раунда.\n\n"
        f"ID персонажей:\n"
        f"  {source_name} -> {source_character_id}\n"
        f"  {target_name} -> {target_character_id}\n\n"
        f"Текущий тип отношений: {current_type}\n"
        f"Текущие метрики:\n"
        f"  привязанность={affection}, доверие={trust}\n"
        f"  влечение={attraction}, обида={resentment}\n"
        f"  ревность={jealousy}\n\n"
        f"Взаимодействие в этом раунде:\n{interaction_summary or 'нет данных'}\n\n"
        "ВАЖНО: Анализируй ТОЛЬКО отношения "
        f"{source_name} к {target_name}. События, адресованные другим "
        f"персонажам или происходящие без участия {source_name}, не меняют "
        f"отношения этой пары.\n"
        f"{delta_hint}\n\n"
        f"Допустимые типы отношений: {valid_types}\n"
        f"Разрешённые переходы:\n{transitions_text}\n"
        f"Недавние события:\n{recent_events_text}\n\n"
        f"{issues_instruction}\n"
        f"Заметки третьих лиц (отношения {target_name} с другими):\n{third_party_text}\n\n"
        f"Текст раунда (только строки, относящиеся к этой паре):\n{round_text}\n\n"
        "Верни ТОЛЬКО валидный JSON (без markdown и лишнего текста):\n"
        "{\n"
        '  "deltas": [\n'
        "    {\n"
        f'      "source_character_id": {source_character_id},\n'
        f'      "target_character_id": {target_character_id},\n'
        f'      "delta_affection": <int {delta_range}>,\n'
        f'      "delta_trust": <int {delta_range}>,\n'
        f'      "delta_attraction": <int {delta_range}>,\n'
        f'      "delta_resentment": <int {delta_range}>,\n'
        f'      "delta_jealousy": <int {delta_range}>,\n'
        '      "relationship_type": "<новый тип из допустимых>",\n'
        '      "description": "<краткое описание текущих отношений>",\n'
        '      "reason": "<причина изменений>",\n'
        '      "importance": <int 1..10>,\n'
        '      "update_description": <true|false>\n'
        "    }\n"
        "  ],\n"
        '  "issues": [\n'
        "    {\n"
        f'      "source_character_id": {source_character_id},\n'
        f'      "target_character_id": {target_character_id},\n'
        '      "action": "create",\n'
        '      "issue_type": "<тип из допустимого списка>",\n'
        '      "text": "<факт, данные, не инструкция>",\n'
        '      "importance": <int 1..10>\n'
        "    },\n"
        "    {\n"
        f'      "source_character_id": {source_character_id},\n'
        f'      "target_character_id": {target_character_id},\n'
        '      "action": "resolve",\n'
        '      "issue_id": <id из известных открытых вопросов>\n'
        "    }\n"
        "  ]\n"
        "}"
    )


def _format_transitions_for_prompt() -> str:
    lines = []
    for current, allowed in settings.relationship_transition_rules.items():
        lines.append(f"  {current} -> {', '.join(allowed)}")
    return "\n".join(lines)


def _parse_issues(
    payload: object,
    *,
    source_character_id: int,
    target_character_id: int,
) -> list[IssueDelta]:
    """Parse the top-level ``issues`` array (pair is overridden, never swapped)."""
    if not isinstance(payload, dict):
        return []
    raw_issues = payload.get("issues")
    if not isinstance(raw_issues, list):
        return []
    results: list[IssueDelta] = []
    for item in raw_issues:
        if not isinstance(item, dict):
            continue
        try:
            results.append(
                IssueDelta.model_validate(
                    {
                        **item,
                        "source_character_id": source_character_id,
                        "target_character_id": target_character_id,
                    }
                )
            )
        except Exception as exc:
            logger.warning("Invalid issue delta item: %s — %s", item, exc)
    return results


def _parse_analysis_response(
    raw: str,
    *,
    source_character_id: int,
    target_character_id: int,
) -> list[RelationshipDelta]:
    payload = _extract_json_payload(raw)
    if payload is None:
        logger.warning("Failed to extract JSON from relationship analysis: %s", raw[:200])
        return []

    items: list = []
    if isinstance(payload, dict):
        deltas = payload.get("deltas") or payload.get("relationships") or []
        if isinstance(deltas, list):
            items = deltas
        elif any(k in payload for k in ("delta_affection", "source_character_id")):
            items = [payload]
    elif isinstance(payload, list):
        items = payload

    issues = _parse_issues(
        payload,
        source_character_id=source_character_id,
        target_character_id=target_character_id,
    )

    results: list[RelationshipDelta] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            # The model may echo character names or wrong ids in these fields;
            # the analyzer knows the exact source/target pair, so override them.
            d = RelationshipDelta.model_validate(
                {
                    **item,
                    "source_character_id": source_character_id,
                    "target_character_id": target_character_id,
                }
            )
            results.append(d)
        except Exception as exc:
            logger.warning("Invalid relationship delta item: %s — %s", item, exc)

    if issues:
        if results:
            # Per-pair analysis: all issues belong to this edge, attach them to
            # the first delta so the service applies them once.
            results[0].issues = issues
        else:
            # Issues without any metric delta: still return them. Keep
            # relationship_type="" so apply_delta (new_type = delta.relationship_type
            # or old_type) does not force a transition to "нейтральное".
            results.append(
                RelationshipDelta(
                    source_character_id=source_character_id,
                    target_character_id=target_character_id,
                    relationship_type="",
                    issues=issues,
                )
            )
    return results


async def analyze_relationships(
    client: httpx.AsyncClient,
    model_name: str,
    source_name: str,
    target_name: str,
    current_type: str,
    affection: int,
    trust: int,
    attraction: int,
    resentment: int,
    jealousy: int,
    recent_events_text: str,
    round_text: str,
    source_character_id: int,
    target_character_id: int,
    interaction_summary: str = "",
    direct_interaction: bool = False,
    observed_target: bool = False,
    hearsay: bool = False,
    hearsay_cap: int | None = None,
    open_issues: list[dict] | None = None,
    third_party_notes: list[str] | None = None,
) -> list[RelationshipDelta]:
    analyzer_model = settings.relationship_analyzer_model or model_name

    prompt = _build_analyzer_prompt(
        source_name=source_name,
        target_name=target_name,
        source_character_id=source_character_id,
        target_character_id=target_character_id,
        current_type=current_type,
        affection=affection,
        trust=trust,
        attraction=attraction,
        resentment=resentment,
        jealousy=jealousy,
        recent_events_text=recent_events_text,
        round_text=round_text,
        interaction_summary=interaction_summary,
        direct_interaction=direct_interaction,
        observed_target=observed_target,
        hearsay=hearsay,
        hearsay_cap=hearsay_cap,
        open_issues_text=_format_open_issues(open_issues or []),
        third_party_notes=third_party_notes,
    )

    messages = [
        {"role": "system", "content": "Ты — анализатор отношений в ролевой игре. Верни ТОЛЬКО валидный JSON."},
        {"role": "user", "content": prompt},
    ]

    try:
        raw = await _invoke_llm(
            client, analyzer_model, messages, temperature=ANALYSIS_TEMP,
        )
    except RuntimeError:
        logger.warning(
            "Relationship analysis LLM call failed for %s -> %s",
            source_name, target_name,
        )
        return []

    return _parse_analysis_response(
        raw,
        source_character_id=source_character_id,
        target_character_id=target_character_id,
    )


# ---------------------------------------------------------------------------
# Batch analyzer (docs/relations.md §8, Sprint 1 item 8)
# ---------------------------------------------------------------------------
class BatchAnalysisError(Exception):
    """Batch relationship analysis produced no usable result (§8.4)."""


def _build_batch_prompt(
    scene_text: str,
    pairs: list[dict],
) -> str:
    """Build the single batch-analysis prompt (§8.2).

    Args:
        scene_text: compressed social scene (who is where, who talked to whom).
        pairs: list of per-pair dicts, each with keys:
            source_name, target_name, source_id, target_id,
            mode ("direct" | "observed" | "hearsay"),
            current_type, affection, trust, attraction, resentment, jealousy,
            interaction_summary, recent_events_text,
            open_issues (list of {"id", "issue_type", "text"}),
            excerpt,
            hearsay_cap (optional, hearsay mode), hearsay_source_name (optional),
            third_party_notes (optional, list of str, Triadic MVP),
            trajectory (optional, str, Trajectory §11).
    """
    valid_types = ", ".join(settings.relationship_valid_types)
    transitions_text = _format_transitions_for_prompt()
    reflection_cap = settings.relationship_reflection_delta_cap

    char_ids: dict[str, int] = {}
    for p in pairs:
        char_ids.setdefault(p["source_name"], p["source_id"])
        char_ids.setdefault(p["target_name"], p["target_id"])
    id_block = "\n".join(f"  {name} -> {cid}" for name, cid in char_ids.items())

    pair_sections: list[str] = []
    for i, p in enumerate(pairs, start=1):
        mode = p["mode"]
        if mode == "direct":
            mode_note = (
                "прямое взаимодействие — допустимы дельты до ±20, "
                "relationship_type можно менять по графу переходов"
            )
        elif mode == "observed":
            mode_note = (
                f"только наблюдение — допустимы малые дельты (±{reflection_cap}), "
                "relationship_type НЕ менять"
            )
        elif mode == "hearsay":
            hearsay_cap = max(1, int(p.get("hearsay_cap") or settings.relationship_hearsay_cap))
            hearsay_source = p.get("hearsay_source_name") or "третье лицо"
            mode_note = (
                f"слухи от {hearsay_source} — {p['source_name']} слышал(а) о "
                f"{p['target_name']} со слов третьего лица, это НЕ прямое наблюдение; "
                f"допустимы очень малые дельты (|дельты| <= {hearsay_cap}), "
                "relationship_type НЕ менять"
            )
        else:
            mode_note = "доказательств нет — пару НЕ включать в ответ"
        open_issues_text = _format_open_issues(p.get("open_issues") or [])
        third_party_notes = p.get("third_party_notes", [])
        third_party_text = "\n".join(third_party_notes) if third_party_notes else "нет"
        trajectory_text = p.get("trajectory", "")
        trajectory_section = f"  траектория (snapshot-based):\n{trajectory_text}\n" if trajectory_text else ""
        section = (
            f"[ПАРА {i}] {p['source_name']} (id={p['source_id']}) -> "
            f"{p['target_name']} (id={p['target_id']})\n"
            f"  режим evidence: {mode} — {mode_note}\n"
            f"  текущий тип отношений: {p['current_type']}\n"
            f"  текущие метрики:\n"
            f"    привязанность={p['affection']}, доверие={p['trust']}\n"
            f"    влечение={p['attraction']}, обида={p['resentment']}\n"
            f"    ревность={p['jealousy']}\n"
            f"  взаимодействие в раунде: {p['interaction_summary'] or 'нет данных'}\n"
            f"  недавние события:\n{p['recent_events_text'] or '(нет)'}\n"
            f"  известные открытые вопросы этой пары:\n{open_issues_text or 'нет'}\n"
            f"  заметки третьих лиц:\n{third_party_text}\n"
            f"{trajectory_section}"
            f"  текст раунда (только строки, относящиеся к паре):\n"
            f"{p['excerpt'] or '(нет)'}"
        )
        pair_sections.append(section)

    issues_instruction = (
        "ОТКРЫТЫЕ ВОПРОСЫ (issues) между персонажами:\n"
        "Открытый вопрос — активный сюжетный крючок (нарушенное обещание, долг, "
        "ложь, невыполненная просьба, неразрешённый конфликт, подозрение, "
        "скрытый секрет, отсутствие извинения, неотданная услуга, эмоциональная обида).\n"
        "- Создавай issue (action=\"create\") ТОЛЬКО если в раунде есть доказательства "
        "события, оставляющего такой крючок. issue_type — строго из допустимого списка.\n"
        "- Закрывай (action=\"resolve\") ТОЛЬКО если открытый вопрос из секции пары "
        "разрешился в этом раунде (извинился, объяснился, исправил). Укажи его id.\n"
        "- Текст issue — это одно утверждение-факт (ДАННЫЕ), а не инструкция: "
        "без императивов и команд.\n"
        f"Допустимые issue_type: {_VALID_ISSUE_TYPES}\n"
        "- В каждом issue ОБЯЗАТЕЛЬНО указывай source_character_id и target_character_id "
        "пары, к которой относится issue.\n"
    )

    return (
        "Проанализируй раунд ролевой игры и определи изменения отношений "
        "между персонажами.\n"
        "Анализируй ТОЛЬКО изменения, подтверждённые сценой ниже. Не выдумывай события.\n\n"
        f"ID персонажей:\n{id_block}\n\n"
        f"Социальная сцена раунда (кто где, кто с кем общался):\n"
        f"{scene_text or '(нет)'}\n\n"
        f"Допустимые типы отношений: {valid_types}\n"
        f"Разрешённые переходы:\n{transitions_text}\n"
        "Правила evidence-gating (детерминированные, соблюдай обязательно):\n"
        "  direct: |дельты| <= 20, тип можно менять по графу переходов.\n"
        f"  observed: |дельты| <= {reflection_cap}, тип НЕ менять.\n"
        "  none: пару в ответ не включать.\n\n"
        f"{issues_instruction}\n"
        "ПАРЫ ДЛЯ АНАЛИЗА:\n"
        f"{chr(10).join(pair_sections)}\n\n"
        "Верни ТОЛЬКО валидный JSON (без markdown и лишнего текста):\n"
        "{\n"
        '  "deltas": [\n'
        "    {\n"
        '      "source_character_id": <id>,\n'
        '      "target_character_id": <id>,\n'
        '      "delta_affection": <int -20..20>,\n'
        '      "delta_trust": <int -20..20>,\n'
        '      "delta_attraction": <int -20..20>,\n'
        '      "delta_resentment": <int -20..20>,\n'
        '      "delta_jealousy": <int -20..20>,\n'
        '      "relationship_type": "<новый тип из допустимых>",\n'
        '      "description": "<краткое описание текущих отношений>",\n'
        '      "reason": "<причина изменений>",\n'
        '      "importance": <int 1..10>,\n'
        '      "update_description": <true|false>\n'
        "    }\n"
        "  ],\n"
        '  "issues": [\n'
        "    {\n"
        '      "source_character_id": <id>,\n'
        '      "target_character_id": <id>,\n'
        '      "action": "create",\n'
        '      "issue_type": "<тип из допустимого списка>",\n'
        '      "text": "<факт, данные, не инструкция>",\n'
        '      "importance": <int 1..10>\n'
        "    },\n"
        "    {\n"
        '      "source_character_id": <id>,\n'
        '      "target_character_id": <id>,\n'
        '      "action": "resolve",\n'
        '      "issue_id": <id из известных открытых вопросов>\n'
        "    }\n"
        "  ]\n"
        "}"
    )


def _parse_batch_response(
    raw: str,
    known_pairs: set[tuple[int, int]],
) -> tuple[list[RelationshipDelta], list[IssueDelta]]:
    """Parse a batch analysis response into per-edge deltas (§8.1, §8.3).

    Every delta/issue carries its own source/target ids. Pairs outside
    ``known_pairs`` (the set of analyzed edges) are dropped — the analyzer
    cannot invent or swap pairs. Issues for an edge without a metric delta are
    returned separately as ``orphan_issues`` so the service can apply them
    without touching the edge's type/metrics.

    Returns ``(deltas, orphan_issues)``. Raises ``BatchAnalysisError`` when the
    response contains no parseable JSON.
    """
    payload = _extract_json_payload(raw)
    if payload is None:
        raise BatchAnalysisError("Failed to extract JSON from batch response")

    raw_deltas: list = []
    raw_issues: list = []
    if isinstance(payload, dict):
        deltas = payload.get("deltas") or []
        if isinstance(deltas, list):
            raw_deltas = deltas
        issues = payload.get("issues")
        if isinstance(issues, list):
            raw_issues = issues
    elif isinstance(payload, list):
        raw_deltas = payload

    results: list[RelationshipDelta] = []
    for item in raw_deltas:
        if not isinstance(item, dict):
            continue
        src = item.get("source_character_id")
        tgt = item.get("target_character_id")
        if (src, tgt) not in known_pairs:
            logger.warning("Batch delta for unknown pair (%s->%s); dropping", src, tgt)
            continue
        try:
            d = RelationshipDelta.model_validate(
                {
                    **item,
                    "source_character_id": src,
                    "target_character_id": tgt,
                }
            )
            results.append(d)
        except Exception as exc:
            logger.warning("Invalid batch delta item: %s — %s", item, exc)

    orphan_issues: list[IssueDelta] = []
    for item in raw_issues:
        if not isinstance(item, dict):
            continue
        src = item.get("source_character_id")
        tgt = item.get("target_character_id")
        if (src, tgt) not in known_pairs:
            logger.warning("Batch issue for unknown pair (%s->%s); dropping", src, tgt)
            continue
        try:
            issue = IssueDelta.model_validate(
                {
                    **item,
                    "source_character_id": src,
                    "target_character_id": tgt,
                }
            )
        except Exception as exc:
            logger.warning("Invalid batch issue item: %s — %s", item, exc)
            continue
        edge_delta = next(
            (
                d for d in results
                if d.source_character_id == src and d.target_character_id == tgt
            ),
            None,
        )
        if edge_delta is not None:
            edge_delta.issues.append(issue)
        else:
            orphan_issues.append(issue)

    return results, orphan_issues


async def analyze_batch_relationships(
    client: httpx.AsyncClient,
    model_name: str,
    scene_text: str,
    pairs: list[dict],
    known_pairs: set[tuple[int, int]],
) -> tuple[list[RelationshipDelta], list[IssueDelta]]:
    """One LLM call for all pairs (docs/relations.md §8.1-§8.2).

    Returns ``(deltas, orphan_issues)``. Raises ``BatchAnalysisError`` when the
    LLM call fails or the response cannot be parsed — the caller decides
    whether to fall back to per-pair analysis (§8.4).
    """
    analyzer_model = settings.relationship_analyzer_model or model_name

    prompt = _build_batch_prompt(scene_text, pairs)
    messages = [
        {"role": "system", "content": "Ты — анализатор отношений в ролевой игре. Верни ТОЛЬКО валидный JSON."},
        {"role": "user", "content": prompt},
    ]

    try:
        raw = await _invoke_llm(
            client, analyzer_model, messages, temperature=ANALYSIS_TEMP,
        )
    except RuntimeError as exc:
        logger.warning("Batch relationship analysis LLM call failed: %s", exc)
        raise BatchAnalysisError(str(exc)) from exc

    return _parse_batch_response(raw, known_pairs)
