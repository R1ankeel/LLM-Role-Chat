"""Sprint 10 — NPC Intent (Plans/update20.md §21).

Покрывает:
- `compute_intent` — детерминированные правила: источник цели по приоритету
  (plan > active_goal > issue > thread), urgency/risk/approach, отсечение слабой
  цели (min_urgency), target из issue;
- `compute_intent_for_character` — write-path в ``intents`` при включённом
  ``npc_intent_enabled``; no-op при выключенном (canary);
- рендер блока ``ACTIVE GOAL`` (build_active_goal_block).

Intent — тенденция, не приказ: блок содержит данные, а не императив.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config import settings
from app.plot import intent as plot_intent
from app.prompt_builder import build_active_goal_block


def _state(**overrides) -> SimpleNamespace:
    base = dict(
        active_goal="", mood="спокоен", stress=0.3,
        personal_goals="[]", emotional_state="{}",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _plan(**overrides) -> SimpleNamespace:
    base = dict(
        goal="", status="active", priority=5, blocked_by="",
        next_step="",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _issue(**overrides) -> SimpleNamespace:
    base = dict(
        id=1, relationship_id=1, text="", importance=5,
        rounds_since_last_mention=0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# compute_intent: чистые правила
# ---------------------------------------------------------------------------

class TestIntentSourcePriority:
    def test_plan_wins_over_active_goal(self):
        intent = plot_intent.compute_intent(
            active_plan=_plan(goal="украсть артефакт"),
            character_state=_state(active_goal="старая цель"),
            open_issues=[_issue(text="обида на Бориса")],
        )
        assert intent is not None
        assert intent["goal"] == "украсть артефакт"
        assert intent["source"] == "plan"

    def test_active_goal_wins_over_issue(self):
        intent = plot_intent.compute_intent(
            character_state=_state(active_goal="найти письмо"),
            open_issues=[_issue(text="обида на Бориса")],
        )
        assert intent is not None
        assert intent["goal"] == "найти письмо"
        assert intent["source"] == "active_goal"

    def test_issue_when_no_state_goal(self):
        intent = plot_intent.compute_intent(
            open_issues=[_issue(text="обида на Бориса", importance=7)],
        )
        assert intent is not None
        assert intent["goal"] == "обида на Бориса"
        assert intent["source"] == "issue"

    def test_thread_when_no_other_source(self):
        thread = SimpleNamespace(
            name="поиск апостола", importance=8, actors="[\"Character A\"]"
        )
        intent = plot_intent.compute_intent(
            story_threads=[thread], character_name="Character A",
        )
        assert intent is not None
        assert "поиск апостола" in intent["goal"]
        assert intent["source"] == "thread"

    def test_none_without_any_goal(self):
        assert plot_intent.compute_intent() is None


class TestIntentIssueTarget:
    def test_target_resolved_from_issue(self):
        intent = plot_intent.compute_intent(
            open_issues=[_issue(id=7, text="обида на Бориса", importance=7)],
            issue_targets={7: 42},
            target_names={42: "Борис"},
        )
        assert intent is not None
        assert intent["target"] == 42
        assert intent["target_name"] == "Борис"


class TestIntentApproach:
    def test_direct_by_default(self):
        intent = plot_intent.compute_intent(
            character_state=_state(active_goal="цель", stress=0.1),
        )
        assert intent["approach"] == "direct"

    def test_delay_when_plan_blocked(self):
        intent = plot_intent.compute_intent(
            active_plan=_plan(goal="цель", status="blocked", blocked_by="стена"),
            character_state=_state(stress=0.1),
        )
        assert intent["approach"] == "delay"

    def test_avoid_when_risk_high(self):
        intent = plot_intent.compute_intent(
            character_state=_state(active_goal="цель", stress=0.9),
            risk_avoid=0.5,
        )
        # risk = 0.9*0.6 = 0.54 >= risk_avoid 0.5
        assert intent["approach"] == "avoid"

    def test_indirect_when_suspicion(self):
        belief = SimpleNamespace(
            type="suspicion", object="Борис", confidence=0.8
        )
        intent = plot_intent.compute_intent(
            character_state=_state(active_goal="цель", stress=0.1),
            open_issues=[_issue(id=1, text="обида на Бориса", importance=7)],
            issue_targets={1: 42},
            target_names={42: "Борис"},
            beliefs=[belief],
            risk_avoid=1.0,
            risk_delay=1.0,
        )
        assert intent["approach"] == "indirect"


class TestIntentMinUrgency:
    def test_weak_issue_below_urgency_threshold(self):
        # importance 2 → priority 0.2 < min_urgency 0.25
        intent = plot_intent.compute_intent(
            open_issues=[_issue(text="мелкая обида", importance=2)],
            min_urgency=0.25,
        )
        assert intent is None

    def test_strong_issue_above_threshold(self):
        intent = plot_intent.compute_intent(
            open_issues=[_issue(text="обида на Бориса", importance=7)],
            min_urgency=0.25,
        )
        assert intent is not None

    def test_plan_goal_never_filtered_by_min_urgency(self):
        intent = plot_intent.compute_intent(
            active_plan=_plan(goal="важная цель", priority=2),
            min_urgency=0.5,
        )
        assert intent is not None


class TestActiveGoalBlock:
    def test_block_renders_goal_and_approach(self):
        block = build_active_goal_block(
            {
                "goal": "украсть артефакт",
                "approach": "indirect",
                "target_name": "Борис",
                "urgency": 0.7,
                "risk": 0.2,
            }
        )
        assert "<active_goal data>" in block
        assert "украсть артефакт" in block
        assert "осторожно" in block
        assert "Борис" in block
        # данные, не приказ
        assert "а не приказ" in block

    def test_empty_for_none(self):
        assert build_active_goal_block(None) == ""


# ---------------------------------------------------------------------------
# compute_intent_for_character: write-path (intents) + canary
# ---------------------------------------------------------------------------

class TestIntentForCharacter:
    @pytest.mark.asyncio
    async def test_noop_when_flag_off(self, monkeypatch, db_session, chat, three_characters):
        a, _, _ = three_characters
        monkeypatch.setattr(settings, "npc_intent_enabled", False)
        intent = await plot_intent.compute_intent_for_character(
            db_session, chat.id, a, round_id="r1-m1"
        )
        assert intent is None
        rows = await crud_get_intents(db_session, chat.id, a.id)
        assert rows == []

    @pytest.mark.asyncio
    async def test_writes_intent_when_enabled(self, monkeypatch, db_session, chat, three_characters):
        a, _, _ = three_characters
        monkeypatch.setattr(settings, "npc_intent_enabled", True)
        state = await crud_get_or_create_state(db_session, chat.id, a.id)
        state.active_goal = "найти письмо"
        await db_session.commit()

        intent = await plot_intent.compute_intent_for_character(
            db_session, chat.id, a, round_id="r1-m1"
        )
        assert intent is not None
        assert intent["goal"] == "найти письмо"
        rows = await crud_get_intents(db_session, chat.id, a.id)
        assert len(rows) == 1
        assert rows[0].goal == "найти письмо"
        assert rows[0].approach == "direct"

    @pytest.mark.asyncio
    async def test_issue_intent_target_persisted(
        self, monkeypatch, db_session, chat, three_characters
    ):
        a, b, _ = three_characters
        monkeypatch.setattr(settings, "npc_intent_enabled", True)
        monkeypatch.setattr(settings, "relationship_issues_enabled", True)
        from app.relationship_service import create_issue, get_or_create_relationship

        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        await create_issue(
            db_session, rel,
            issue_type="broken_promise",
            text="Борис не выполнил обещание",
            importance=7,
            round_id="r1-m1",
        )

        intent = await plot_intent.compute_intent_for_character(
            db_session, chat.id, a, round_id="r1-m1",
            character_names={a.id: a.name, b.id: b.name},
        )
        assert intent is not None
        assert intent["source"] == "issue"
        assert intent["target"] == b.id
        assert intent["target_name"] == b.name


def crud_get_intents(db, chat_id, character_id):
    from app import crud

    return crud.get_intents_for_character(db, chat_id, character_id)


def crud_get_or_create_state(db, chat_id, character_id):
    from app import crud

    return crud.get_or_create_character_state(db, chat_id, character_id)
