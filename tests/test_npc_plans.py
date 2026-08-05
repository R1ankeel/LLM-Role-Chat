"""Sprint 10 — NPC Plans (Plans/update20.md §22).

Покрывает:
- `get_or_create_active_plan` — один активный план на персонажа; второй НЕ
  создаётся; canary (``npc_plans_enabled=false`` → None);
- `update_plan_from_round` — детерминированное продвижение по событиям раунда:
  next_step, done при важности >= порога, снятие блокировки;
- `build_active_plan_block` — компактный data-only блок ACTIVE PLAN;
- НЕ GOAP: план — «цель + следующий шаг + препятствие», без инструкций.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config import settings
from app import npc_plans


def _plan(**overrides) -> SimpleNamespace:
    base = dict(
        id=1, chat_id=1, character_id=1, goal="украсть артефакт",
        next_step="", blocked_by="", priority=5, status="active",
        created_round_id=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _event(**overrides) -> dict:
    base = dict(
        importance=5.0,
        action={"actor": "Character A", "action": "находит", "object": "письмо"},
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# get_or_create_active_plan
# ---------------------------------------------------------------------------

class TestGetOrCreateActivePlan:
    @pytest.mark.asyncio
    async def test_creates_first_plan(self, monkeypatch, db_session, chat, three_characters):
        a, _, _ = three_characters
        monkeypatch.setattr(settings, "npc_plans_enabled", True)
        plan = await npc_plans.get_or_create_active_plan(
            db_session, chat.id, a.id, "украсть артефакт", round_id="r1-m1"
        )
        assert plan is not None
        assert plan.goal == "украсть артефакт"
        assert plan.status == "active"

    @pytest.mark.asyncio
    async def test_second_plan_not_created(self, monkeypatch, db_session, chat, three_characters):
        a, _, _ = three_characters
        monkeypatch.setattr(settings, "npc_plans_enabled", True)
        first = await npc_plans.get_or_create_active_plan(
            db_session, chat.id, a.id, "украсть артефакт", round_id="r1-m1"
        )
        second = await npc_plans.get_or_create_active_plan(
            db_session, chat.id, a.id, "другая цель", round_id="r2-m2"
        )
        assert second.id == first.id
        assert second.goal == "украсть артефакт"
        plans = await crud_get_plans(db_session, chat.id, a.id)
        assert len(plans) == 1

    @pytest.mark.asyncio
    async def test_noop_when_flag_off(self, db_session, chat, three_characters):
        a, _, _ = three_characters
        plan = await npc_plans.get_or_create_active_plan(
            db_session, chat.id, a.id, "украсть артефакт"
        )
        assert plan is None
        assert await crud_get_plans(db_session, chat.id, a.id) == []

    @pytest.mark.asyncio
    async def test_returns_existing_blocked_plan(self, monkeypatch, db_session, chat, three_characters):
        a, _, _ = three_characters
        monkeypatch.setattr(settings, "npc_plans_enabled", True)
        first = await npc_plans.get_or_create_active_plan(
            db_session, chat.id, a.id, "украсть артефакт", round_id="r1-m1"
        )
        await crud_update_plan(db_session, first.id, blocked_by="стена", status="blocked")
        again = await npc_plans.get_or_create_active_plan(
            db_session, chat.id, a.id, "новая цель", round_id="r2-m2"
        )
        assert again.id == first.id
        assert again.blocked_by == "стена"


# ---------------------------------------------------------------------------
# update_plan_from_round
# ---------------------------------------------------------------------------

class TestUpdatePlanFromRound:
    @pytest.mark.asyncio
    async def test_next_step_from_overlapping_event(self, monkeypatch, db_session, chat, three_characters):
        a, _, _ = three_characters
        monkeypatch.setattr(settings, "npc_plans_enabled", True)
        plan = await npc_plans.get_or_create_active_plan(
            db_session, chat.id, a.id, "украсть артефакт", round_id="r1-m1"
        )
        report = await npc_plans.update_plan_from_round(
            db_session, plan,
            [
                _event(
                    importance=5.0,
                    action={"actor": a.name, "action": "крадет", "object": "артефакт"},
                )
            ],
            round_id="r2-m2",
            resolve_importance=8.0,
        )
        assert report["next_step_changed"] is True
        await db_session.refresh(plan)
        assert "крадет" in plan.next_step

    @pytest.mark.asyncio
    async def test_done_when_importance_high(self, monkeypatch, db_session, chat, three_characters):
        a, _, _ = three_characters
        monkeypatch.setattr(settings, "npc_plans_enabled", True)
        plan = await npc_plans.get_or_create_active_plan(
            db_session, chat.id, a.id, "украсть артефакт", round_id="r1-m1"
        )
        report = await npc_plans.update_plan_from_round(
            db_session, plan,
            [
                _event(
                    importance=9.0,
                    action={"actor": a.name, "action": "крадет", "object": "артефакт"},
                )
            ],
            round_id="r2-m2",
            resolve_importance=8.0,
        )
        assert report["status"] == "done"
        await db_session.refresh(plan)
        assert plan.status == "done"

    @pytest.mark.asyncio
    async def test_unblocks_on_blocking_event(self, monkeypatch, db_session, chat, three_characters):
        a, _, _ = three_characters
        monkeypatch.setattr(settings, "npc_plans_enabled", True)
        plan = await npc_plans.get_or_create_active_plan(
            db_session, chat.id, a.id, "украсть артефакт", round_id="r1-m1"
        )
        await crud_update_plan(db_session, plan.id, blocked_by="охрана у входа")
        await db_session.refresh(plan)

        report = await npc_plans.update_plan_from_round(
            db_session, plan,
            [
                _event(
                    importance=8.0,
                    action={"actor": a.name, "action": "убирает", "object": "охрана"},
                )
            ],
            round_id="r2-m2",
            resolve_importance=7.0,
        )
        assert report["unblocked"] is True
        await db_session.refresh(plan)
        assert plan.blocked_by == ""

    @pytest.mark.asyncio
    async def test_no_change_without_overlap(self, monkeypatch, db_session, chat, three_characters):
        a, _, _ = three_characters
        monkeypatch.setattr(settings, "npc_plans_enabled", True)
        plan = await npc_plans.get_or_create_active_plan(
            db_session, chat.id, a.id, "украсть артефакт", round_id="r1-m1"
        )
        report = await npc_plans.update_plan_from_round(
            db_session, plan,
            [
                _event(
                    importance=5.0,
                    action={"actor": a.name, "action": "пьет", "object": "чай"},
                )
            ],
            round_id="r2-m2",
            resolve_importance=8.0,
        )
        assert report["status"] == ""
        assert report["next_step_changed"] is False
        await db_session.refresh(plan)
        assert plan.next_step == ""
        assert plan.status == "active"


# ---------------------------------------------------------------------------
# build_active_plan_block
# ---------------------------------------------------------------------------

class TestActivePlanBlock:
    def test_renders_goal_next_step_blocker(self):
        block = npc_plans.build_active_plan_block(
            _plan(next_step="проникнуть ночью", blocked_by="охрана")
        )
        assert "<active_plan data>" in block
        assert "украсть артефакт" in block
        assert "проникнуть ночью" in block
        assert "охрана" in block
        assert "а не приказ" in block

    def test_empty_for_none(self):
        assert npc_plans.build_active_plan_block(None) == ""


def crud_get_plans(db, chat_id, character_id):
    from app import crud

    return crud.get_npc_plans_for_character(db, chat_id, character_id)


def crud_update_plan(db, plan_id, **kwargs):
    from app import crud

    return crud.update_npc_plan(db, plan_id, **kwargs)
