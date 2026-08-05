"""Sprint 10 — Story threads archiving + plot pressure (Plans/update20.md §19, §22).

Покрывает:
- `story_threads.archive_completed_threads` — архивация активных линий, чьё
  имя пересекается с ``completed_goals`` (token overlap), canary (story_enabled);
- `story_threads.significant_tokens`/`token_overlap` — детерминированные helpers;
- `plot_pressure.compute_story_pressure` — взвешенная сумма, нормировка весов;
- `plot_pressure.issues_score_from_issues`/`goals_blocked_score`/
  `recent_intensity_score` — компоненты.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import crud
from app import models
from app.config import settings
from app.plot import plot_pressure
from app.plot import story_threads


# ---------------------------------------------------------------------------
# token overlap helpers
# ---------------------------------------------------------------------------

class TestTokenOverlap:
    def test_significant_tokens_drops_stopwords(self):
        toks = story_threads.significant_tokens("найти письмо в таверне")
        assert "письмо" in toks
        assert "найти" in toks
        assert "в" not in toks

    def test_overlap_fraction(self):
        # 1 из 3 значимых токенов совпадает (письмо)
        ov = story_threads.token_overlap("найти письмо в таверне", "потерянное письмо")
        assert ov == pytest.approx(1 / 3)

    def test_overlap_zero_on_empty(self):
        assert story_threads.token_overlap("", "что-то") == 0.0


# ---------------------------------------------------------------------------
# archive_completed_threads
# ---------------------------------------------------------------------------

class TestArchiveCompletedThreads:
    @pytest.mark.asyncio
    async def test_archives_matching_threads(self, monkeypatch, db_session, chat):
        monkeypatch.setattr(settings, "story_enabled", True)
        await crud.get_or_create_story_state(db_session, chat.id)
        await crud.update_story_state(
            db_session, chat.id,
            current_story={"completed_goals": ["найти письмо"]},
        )
        t1 = await crud.create_story_thread(
            db_session, chat_id=chat.id, name="найти письмо", actors=["A"], importance=7,
        )
        t2 = await crud.create_story_thread(
            db_session, chat_id=chat.id, name="победить дракона", actors=["B"], importance=6,
        )

        report = await story_threads.archive_completed_threads(db_session, chat.id)
        assert report["ok"] is True
        assert report["archived"] == 1

        t1 = await db_session.get(models.StoryThread, t1.id)
        t2 = await db_session.get(models.StoryThread, t2.id)
        assert t1.status == "archived"
        assert t2.status == "active"

    @pytest.mark.asyncio
    async def test_noop_without_completed_goals(self, monkeypatch, db_session, chat):
        monkeypatch.setattr(settings, "story_enabled", True)
        await crud.get_or_create_story_state(db_session, chat.id)
        await crud.update_story_state(
            db_session, chat.id, current_story={"summary": ["нет целей"]}
        )
        await crud.create_story_thread(
            db_session, chat_id=chat.id, name="найти письмо", actors=["A"], importance=7,
        )
        report = await story_threads.archive_completed_threads(db_session, chat.id)
        assert report["ok"] is True
        assert report["archived"] == 0

    @pytest.mark.asyncio
    async def test_skip_when_story_disabled(self, db_session, chat):
        report = await story_threads.archive_completed_threads(db_session, chat.id)
        assert report["skipped"] == "flag off"


# ---------------------------------------------------------------------------
# plot pressure
# ---------------------------------------------------------------------------

class TestPlotPressure:
    def test_pressure_is_weighted_and_normalized(self, monkeypatch):
        monkeypatch.setattr(settings, "plot_pressure_weight_issues", 1.0)
        monkeypatch.setattr(settings, "plot_pressure_weight_goals", 1.0)
        monkeypatch.setattr(settings, "plot_pressure_weight_stagnation", 0.0)
        monkeypatch.setattr(settings, "plot_pressure_weight_recent", 0.0)
        # issues 0.5, goals 0.0 → среднее по ненулевым весам
        p = plot_pressure.compute_story_pressure(issues_score=0.5, goals_blocked=0.0)
        assert p == pytest.approx(0.25)

    def test_zero_when_all_components_zero(self, monkeypatch):
        monkeypatch.setattr(settings, "plot_pressure_weight_issues", 1.0)
        monkeypatch.setattr(settings, "plot_pressure_weight_goals", 1.0)
        monkeypatch.setattr(settings, "plot_pressure_weight_stagnation", 1.0)
        monkeypatch.setattr(settings, "plot_pressure_weight_recent", 1.0)
        assert plot_pressure.compute_story_pressure() == 0.0

    def test_goals_blocked_needs_goal(self):
        assert plot_pressure.goals_blocked_score(has_goal=False, plan_blocked=True) == 0.0
        assert plot_pressure.goals_blocked_score(has_goal=True, plan_blocked=True) > 0.0

    def test_issues_score_uses_salience_decay(self, monkeypatch):
        monkeypatch.setattr(settings, "issue_salience_decay_rounds", 5)
        fresh = SimpleNamespace(importance=10, rounds_since_last_mention=0)
        stale = SimpleNamespace(importance=10, rounds_since_last_mention=5)
        s_fresh = plot_pressure.issues_score_from_issues([fresh])
        s_stale = plot_pressure.issues_score_from_issues([stale])
        assert s_fresh > s_stale
        assert s_stale == 0.0

    def test_recent_intensity_is_average(self):
        assert plot_pressure.recent_intensity_score([10.0, 10.0]) == pytest.approx(1.0)
        assert plot_pressure.recent_intensity_score([5.0, 5.0]) == pytest.approx(0.5)
