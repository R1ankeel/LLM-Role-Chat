"""Sprint 11 — Crisis Engine (Plans/update20.md §19).

Покрывает:
- pressure: `trajectory_score_from_events`, `beliefs_conflict_score`,
  `compute_crisis_pressure` (6 компонентов §19, нормировка весов);
- кандидат: `build_crisis_candidate` — только при pressure+неразрешённость+
  взаимодействие, type direct_conflict при противоположных интентах;
- evaluation: `validate_crisis_evaluation` (JSON-schema, benchmark gate §27);
- resolution: `run_crisis_engine` — пишет story_thread «Кризис» + story_event,
  НЕ пишет world_events (нет форсированных аргументов);
- мягкое применение: `compute_crisis_boost` (вовлечённые получают boost,
  остальные 0), `build_crisis_block` (data-only, флаг off → пусто);
- стадия pipeline: `_stage_crisis` (flag off → skipped).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import crud
from app import models
from app import post_round_pipeline
from app import relationship_service
from app import schemas
from app.config import settings
from app.plot import crisis_engine


# ---------------------------------------------------------------------------
# trajectory / beliefs / crisis pressure (pure)
# ---------------------------------------------------------------------------

class TestCrisisPressureComponents:
    def test_trajectory_negative_deltas(self):
        evs = [
            SimpleNamespace(delta_resentment=2, delta_jealousy=0, delta_trust=-2, delta_affection=0),
        ]
        assert crisis_engine.trajectory_score_from_events(evs) == pytest.approx(1.0)

    def test_trajectory_positive_deltas_zero(self):
        evs = [
            SimpleNamespace(delta_resentment=0, delta_jealousy=0, delta_trust=2, delta_affection=3),
        ]
        assert crisis_engine.trajectory_score_from_events(evs) == 0.0

    def test_trajectory_empty(self):
        assert crisis_engine.trajectory_score_from_events([]) == 0.0

    def test_beliefs_conflict_suspicion(self):
        bels = [SimpleNamespace(type="suspicion", confidence=0.8)]
        assert crisis_engine.beliefs_conflict_score(bels) == pytest.approx(1 / 3)

    def test_beliefs_conflict_low_confidence_zero(self):
        bels = [SimpleNamespace(type="suspicion", confidence=0.3)]
        assert crisis_engine.beliefs_conflict_score(bels) == 0.0

    def test_beliefs_conflict_empty(self):
        assert crisis_engine.beliefs_conflict_score([]) == 0.0

    def test_pressure_weighted_and_normalized(self, monkeypatch):
        monkeypatch.setattr(settings, "crisis_weight_base", 1.0)
        monkeypatch.setattr(settings, "crisis_weight_trajectory", 1.0)
        monkeypatch.setattr(settings, "crisis_weight_beliefs", 0.0)
        p = crisis_engine.compute_crisis_pressure(base_pressure=0.5, trajectory=0.0)
        assert p == pytest.approx(0.25)

    def test_pressure_trajectory_only(self):
        p = crisis_engine.compute_crisis_pressure(
            base_pressure=0.0, trajectory=0.7, beliefs_conflict=0.0,
            weights={"base": 0.0, "trajectory": 1.0, "beliefs": 0.0},
        )
        assert p == pytest.approx(0.7)

    def test_pressure_zero_when_all_components_zero(self):
        assert crisis_engine.compute_crisis_pressure() == 0.0


class TestOpposingIntents:
    def test_opposing_targets(self):
        a = [SimpleNamespace(target=2)]
        b = [SimpleNamespace(target=1)]
        assert crisis_engine.opposing_intents(1, 2, a, b) is True

    def test_not_opposing(self):
        a = [SimpleNamespace(target=2)]
        b = [SimpleNamespace(target=9)]
        assert crisis_engine.opposing_intents(1, 2, a, b) is False

    def test_empty_intents(self):
        assert crisis_engine.opposing_intents(1, 2, [], []) is False


# ---------------------------------------------------------------------------
# build_crisis_candidate (правила, детерминированные)
# ---------------------------------------------------------------------------

def _stale_issue(iid=1, text="долгий конфликт", rounds=3):
    return SimpleNamespace(id=iid, text=text, rounds_since_last_mention=rounds)


class TestBuildCrisisCandidate:
    @pytest.fixture(autouse=True)
    def _tune(self, monkeypatch):
        monkeypatch.setattr(settings, "crisis_pressure_threshold", 0.5)
        monkeypatch.setattr(settings, "crisis_min_issue_age_rounds", 2)

    def test_candidate_when_pressure_and_unresolved(self):
        cand = crisis_engine.build_crisis_candidate(
            pressure=0.8,
            open_issues=[_stale_issue()],
            interaction_rounds=2,
        )
        assert cand is not None
        assert cand["pressure"] == pytest.approx(0.8)
        assert cand["type"] == "discovery"

    def test_no_candidate_low_pressure(self):
        assert (
            crisis_engine.build_crisis_candidate(
                pressure=0.3, open_issues=[_stale_issue()], interaction_rounds=2
            )
            is None
        )

    def test_no_candidate_without_unresolved(self):
        fresh = SimpleNamespace(id=2, text="свежий", rounds_since_last_mention=0)
        assert (
            crisis_engine.build_crisis_candidate(
                pressure=0.8, open_issues=[fresh], interaction_rounds=2
            )
            is None
        )

    def test_no_candidate_without_interaction(self):
        assert (
            crisis_engine.build_crisis_candidate(
                pressure=0.8, open_issues=[_stale_issue()], interaction_rounds=0
            )
            is None
        )

    def test_candidate_type_direct_conflict_with_opposing(self):
        cand = crisis_engine.build_crisis_candidate(
            pressure=0.8,
            open_issues=[_stale_issue()],
            interaction_rounds=2,
            opposing=True,
        )
        assert cand["type"] == "direct_conflict"

    def test_candidate_characters_from_edges(self):
        cand = crisis_engine.build_crisis_candidate(
            pressure=0.8,
            open_issues=[_stale_issue(iid=7)],
            interaction_rounds=2,
            issue_edges={7: (1, 2)},
        )
        assert cand["characters"] == [1, 2]


# ---------------------------------------------------------------------------
# validate_crisis_evaluation (LLM, benchmark gate §27)
# ---------------------------------------------------------------------------

class TestValidateCrisisEvaluation:
    def test_valid_result(self):
        out = crisis_engine.validate_crisis_evaluation(
            {"candidate": True, "type": "direct_conflict", "confidence": 0.8}
        )
        assert out == {"candidate": True, "type": "direct_conflict", "confidence": 0.8}

    def test_unknown_type_falls_back(self):
        out = crisis_engine.validate_crisis_evaluation(
            {"candidate": True, "type": "banana", "confidence": 0.5}
        )
        assert out["type"] == "discovery"

    def test_invalid_top_level(self):
        assert crisis_engine.validate_crisis_evaluation("not json") is None
        assert crisis_engine.validate_crisis_evaluation([1, 2]) is None

    def test_confidence_clamped(self):
        out = crisis_engine.validate_crisis_evaluation(
            {"candidate": True, "type": "departure", "confidence": 5.0}
        )
        assert out["confidence"] == 1.0


# ---------------------------------------------------------------------------
# run_crisis_engine (async, DB)
# ---------------------------------------------------------------------------

class TestRunCrisisEngine:
    @pytest.mark.asyncio
    async def test_skip_when_flag_off(self, monkeypatch, db_session, chat):
        monkeypatch.setattr(settings, "crisis_engine_enabled", False)
        report = await crisis_engine.run_crisis_engine(
            db_session, chat_id=chat.id, round_id="r1", characters=[]
        )
        assert report["skipped"] == "flag off"

    @pytest.mark.asyncio
    async def test_no_candidate_without_stale_issue(self, monkeypatch, db_session, chat):
        monkeypatch.setattr(settings, "crisis_engine_enabled", True)
        monkeypatch.setattr(settings, "relationship_issues_enabled", True)
        a = await crud.create_character(
            db_session, chat.id,
            schemas.CharacterCreate(name="A", personality="", traits="", order_index=1),
        )
        b = await crud.create_character(
            db_session, chat.id,
            schemas.CharacterCreate(name="B", personality="", traits="", order_index=2),
        )
        report = await crisis_engine.run_crisis_engine(
            db_session,
            chat_id=chat.id,
            round_id="r1",
            characters=[a, b],
            character_names={a.id: a.name, b.id: b.name},
        )
        assert report["ok"] is True
        assert report["candidate"] is False
        threads = await crud.get_active_story_threads(db_session, chat.id)
        assert threads == []

    @pytest.mark.asyncio
    async def test_resolution_writes_thread_and_no_world_events(
        self, monkeypatch, db_session, chat
    ):
        monkeypatch.setattr(settings, "crisis_engine_enabled", True)
        monkeypatch.setattr(settings, "story_enabled", True)
        monkeypatch.setattr(settings, "relationship_issues_enabled", True)
        monkeypatch.setattr(settings, "npc_plans_enabled", False)
        monkeypatch.setattr(settings, "beliefs_enabled", False)
        # «затянутый конфликт»: отрицательная траектория → высокий pressure,
        # старый неразрешённый issue, пара взаимодействовала ≥ 1 раунд.
        monkeypatch.setattr(settings, "crisis_pressure_threshold", 0.4)
        monkeypatch.setattr(settings, "crisis_weight_base", 0.2)
        monkeypatch.setattr(settings, "crisis_weight_trajectory", 0.6)
        monkeypatch.setattr(settings, "crisis_weight_beliefs", 0.2)
        monkeypatch.setattr(settings, "crisis_min_issue_age_rounds", 2)
        monkeypatch.setattr(settings, "issue_salience_decay_rounds", 5)

        a = await crud.create_character(
            db_session, chat.id,
            schemas.CharacterCreate(name="A", personality="", traits="", order_index=1),
        )
        b = await crud.create_character(
            db_session, chat.id,
            schemas.CharacterCreate(name="B", personality="", traits="", order_index=2),
        )
        rel = await relationship_service.get_or_create_relationship(
            db_session, chat.id, a.id, b.id
        )
        issue = await relationship_service.create_issue(
            db_session, rel,
            issue_type="unresolved_conflict",
            text="A хочет отомстить B",
            importance=9,
            round_id="r1",
        )
        assert issue is not None
        issue.rounds_since_last_mention = 2
        db_session.add(
            models.RelationshipEvent(
                relationship_id=rel.id,
                kind="llm",
                description="размолвка",
                delta_resentment=2,
                delta_trust=-2,
                round_id="r1",
            )
        )
        await db_session.commit()

        report = await crisis_engine.run_crisis_engine(
            db_session,
            chat_id=chat.id,
            round_id="r1",
            characters=[a, b],
            character_names={a.id: a.name, b.id: b.name},
        )
        assert report["ok"] is True
        assert report["candidate"] is True
        assert report["type"] == "discovery"

        threads = await crud.get_active_story_threads(db_session, chat.id)
        assert len(threads) == 1
        assert threads[0].name.startswith("Кризис")

        events = await crud.get_story_events_for_chat(db_session, chat.id)
        assert len(events) == 1
        assert events[0].event.startswith("Кризис")

        world_events = await crud.get_story_round_world_events(
            db_session, chat.id, "r1"
        )
        assert world_events == []  # нет форсированных аргументов: world_events не пишутся


# ---------------------------------------------------------------------------
# мягкое применение: boost + context block
# ---------------------------------------------------------------------------

class TestCrisisBoost:
    @pytest.mark.asyncio
    async def test_boost_for_involved_character_only(self, monkeypatch, db_session, chat):
        monkeypatch.setattr(settings, "crisis_engine_enabled", True)
        monkeypatch.setattr(settings, "crisis_boost_cap", 0.3)
        a = await crud.create_character(
            db_session, chat.id,
            schemas.CharacterCreate(name="A", personality="", traits="", order_index=1),
        )
        b = await crud.create_character(
            db_session, chat.id,
            schemas.CharacterCreate(name="B", personality="", traits="", order_index=2),
        )
        c = await crud.create_character(
            db_session, chat.id,
            schemas.CharacterCreate(name="C", personality="", traits="", order_index=3),
        )
        await crud.create_story_thread(
            db_session,
            chat_id=chat.id,
            name="Кризис: конфликт",
            actors=["A", "B"],
            importance=7,
            created_round_id="r1",
        )
        assert await crisis_engine.compute_crisis_boost(
            db_session, chat.id, a
        ) == pytest.approx(0.3)
        assert await crisis_engine.compute_crisis_boost(
            db_session, chat.id, c
        ) == 0.0

    @pytest.mark.asyncio
    async def test_no_boost_when_flag_off(self, monkeypatch, db_session, chat):
        monkeypatch.setattr(settings, "crisis_engine_enabled", False)
        a = await crud.create_character(
            db_session, chat.id,
            schemas.CharacterCreate(name="A", personality="", traits="", order_index=1),
        )
        assert await crisis_engine.compute_crisis_boost(db_session, chat.id, a) == 0.0


class TestBuildCrisisBlock:
    @pytest.mark.asyncio
    async def test_block_renders_active_crisis(self, monkeypatch, db_session, chat):
        monkeypatch.setattr(settings, "crisis_engine_enabled", True)
        await crud.create_story_thread(
            db_session,
            chat_id=chat.id,
            name="Кризис: конфликт",
            actors=["A"],
            importance=7,
        )
        block = await crisis_engine.build_crisis_block(db_session, chat.id)
        assert "<crisis data>" in block
        assert "Кризис: конфликт" in block

    @pytest.mark.asyncio
    async def test_block_empty_when_flag_off(self, monkeypatch, db_session, chat):
        monkeypatch.setattr(settings, "crisis_engine_enabled", False)
        assert await crisis_engine.build_crisis_block(db_session, chat.id) == ""


class TestCrisisStage:
    @pytest.mark.asyncio
    async def test_stage_skipped_when_flag_off(self, monkeypatch, db_session, chat):
        monkeypatch.setattr(settings, "crisis_engine_enabled", False)
        report = await post_round_pipeline._stage_crisis(
            db_session, chat_id=chat.id, round_id="r1", characters=[]
        )
        assert report["skipped"] == "flag off"

    @pytest.mark.asyncio
    async def test_pipeline_includes_crisis_stage(self, monkeypatch, db_session, chat):
        monkeypatch.setattr(settings, "crisis_engine_enabled", False)
        report = await post_round_pipeline.run_post_round_pipeline(
            client=None,
            db=db_session,
            chat_id=chat.id,
            model_name="",
            round_messages=[],
            character_ids=[],
            character_names={},
            characters=[],
            character_locations={},
            round_id="r1",
            round_snapshots=[],
            character_snapshots=[],
            stages={"crisis"},
        )
        assert "crisis" in report
        assert report["crisis"]["skipped"] == "flag off"
