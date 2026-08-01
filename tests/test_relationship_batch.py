"""Tests for the batch relationship analyzer (Sprint 1 item 8, docs/relations.md §8)."""

import pytest

from app.config import settings
from app.relationship_analyzer import (
    BatchAnalysisError,
    _build_batch_prompt,
    _parse_batch_response,
    analyze_batch_relationships,
)


def _pair(mode: str = "direct", source_id: int = 1, target_id: int = 2, **extra) -> dict:
    pair = {
        "source_name": "A",
        "target_name": "B",
        "source_id": source_id,
        "target_id": target_id,
        "mode": mode,
        "current_type": "нейтральное",
        "affection": 50,
        "trust": 50,
        "attraction": 0,
        "resentment": 0,
        "jealousy": 0,
        "interaction_summary": "A -> B: A и B говорили",
        "recent_events_text": "",
        "open_issues": [],
        "excerpt": "A (id=1) -> B: привет",
    }
    pair.update(extra)
    return pair


class TestBuildBatchPrompt:
    def test_contains_scene_and_ids(self):
        prompt = _build_batch_prompt(
            "A: hall\nB: hall\nA -> B: привет",
            [_pair()],
        )
        assert "Социальная сцена раунда" in prompt
        assert "A: hall" in prompt
        assert "ID персонажей" in prompt
        assert "A -> 1" in prompt
        assert "B -> 2" in prompt

    def test_direct_mode_hint(self):
        prompt = _build_batch_prompt("сцена", [_pair(mode="direct")])
        assert "прямое взаимодействие" in prompt
        assert "relationship_type можно менять" in prompt

    def test_observed_mode_hint(self):
        prompt = _build_batch_prompt(
            "сцена",
            [_pair(mode="observed")],
        )
        assert "только наблюдение" in prompt
        assert f"±{settings.relationship_reflection_delta_cap}" in prompt
        assert "relationship_type НЕ менять" in prompt

    def test_hearsay_mode_hint(self):
        prompt = _build_batch_prompt(
            "сцена",
            [_pair(mode="hearsay", hearsay_cap=3, hearsay_source_name="C")],
        )
        assert "слухи от C" in prompt
        assert "|дельты| <= 3" in prompt
        assert "relationship_type НЕ менять" in prompt

    def test_hearsay_mode_hint_defaults(self):
        prompt = _build_batch_prompt("сцена", [_pair(mode="hearsay")])
        assert "третье лицо" in prompt
        assert f"<= {settings.relationship_hearsay_cap}" in prompt

    def test_issues_instruction_and_json_schema(self):
        prompt = _build_batch_prompt("сцена", [_pair()])
        assert "ОТКРЫТЫЕ ВОПРОСЫ (issues)" in prompt
        assert "source_character_id" in prompt
        assert '"deltas"' in prompt


class TestParseBatchResponse:
    def test_groups_deltas_per_edge(self):
        raw = (
            '{"deltas": ['
            '  {"source_character_id": 1, "target_character_id": 2, '
            '   "delta_trust": -5, "importance": 6},'
            '  {"source_character_id": 3, "target_character_id": 4, '
            '   "delta_resentment": 4, "importance": 5}'
            ']}'
        )
        deltas, orphan = _parse_batch_response(
            raw, {(1, 2), (3, 4)}
        )
        assert len(deltas) == 2
        assert orphan == []
        by_pair = {(d.source_character_id, d.target_character_id): d for d in deltas}
        assert by_pair[(1, 2)].delta_trust == -5
        assert by_pair[(3, 4)].delta_resentment == 4

    def test_unknown_pair_dropped(self):
        raw = (
            '{"deltas": ['
            '  {"source_character_id": 99, "target_character_id": 98, '
            '   "delta_trust": -5, "importance": 6}'
            ']}'
        )
        deltas, _ = _parse_batch_response(raw, {(1, 2)})
        assert deltas == []

    def test_issues_attached_to_matching_delta(self):
        raw = (
            '{"deltas": ['
            '  {"source_character_id": 1, "target_character_id": 2, '
            '   "delta_trust": -5, "importance": 6}'
            '],'
            '"issues": ['
            '  {"source_character_id": 1, "target_character_id": 2, '
            '   "action": "create", "issue_type": "lie", '
            '   "text": "Борис солгал", "importance": 7}'
            ']}'
        )
        deltas, orphan = _parse_batch_response(raw, {(1, 2)})
        assert len(deltas) == 1
        assert orphan == []
        assert deltas[0].issues
        assert deltas[0].issues[0].source_character_id == 1
        assert deltas[0].issues[0].target_character_id == 2
        assert deltas[0].issues[0].issue_type == "lie"

    def test_orphan_issues_returned_separately(self):
        raw = (
            '{"issues": ['
            '  {"source_character_id": 1, "target_character_id": 2, '
            '   "action": "create", "issue_type": "debt", '
            '   "text": "Борис должен", "importance": 6}'
            ']}'
        )
        deltas, orphan = _parse_batch_response(raw, {(1, 2)})
        assert deltas == []
        assert len(orphan) == 1
        assert orphan[0].source_character_id == 1
        assert orphan[0].target_character_id == 2
        assert orphan[0].issue_type == "debt"

    def test_issue_pair_overridden_not_trusted(self):
        raw = (
            '{"issues": ['
            '  {"source_character_id": 99, "target_character_id": 99, '
            '   "action": "create", "issue_type": "lie", '
            '   "text": "X", "importance": 6}'
            ']}'
        )
        deltas, orphan = _parse_batch_response(raw, {(1, 2)})
        assert deltas == []
        assert orphan == []

    def test_invalid_json_raises(self):
        with pytest.raises(BatchAnalysisError):
            _parse_batch_response("не json вообще", {(1, 2)})


class TestAnalyzeBatchRelationships:
    async def test_calls_llm_once_and_parses(self, monkeypatch):
        captured = {}

        async def fake_invoke(client, model_name, messages, *, temperature=0.3, stop=None):
            captured["model"] = model_name
            captured["messages"] = messages
            return (
                '{"deltas": [{"source_character_id": 1, "target_character_id": 2, '
                '"delta_trust": -5, "importance": 6}], "issues": []}'
            )

        monkeypatch.setattr(
            "app.relationship_analyzer._invoke_llm", fake_invoke
        )
        deltas, orphan = await analyze_batch_relationships(
            client=object(),
            model_name="model-x",
            scene_text="сцена",
            pairs=[_pair()],
            known_pairs={(1, 2)},
        )
        assert captured["model"] == "model-x"
        assert len(captured["messages"]) == 2
        assert captured["messages"][0]["role"] == "system"
        assert "ПАРЫ ДЛЯ АНАЛИЗА" in captured["messages"][1]["content"]
        assert len(deltas) == 1
        assert orphan == []

    async def test_llm_failure_raises_batch_error(self, monkeypatch):
        async def fake_invoke(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(
            "app.relationship_analyzer._invoke_llm", fake_invoke
        )
        with pytest.raises(BatchAnalysisError):
            await analyze_batch_relationships(
                client=object(),
                model_name="model-x",
                scene_text="сцена",
                pairs=[_pair()],
                known_pairs={(1, 2)},
            )
