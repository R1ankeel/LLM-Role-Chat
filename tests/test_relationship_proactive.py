"""Tests for weighted deterministic proactive boost (Sprint 1 item 7, §7.4)."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import ollama_client
from app.config import settings
from app.context_state import ctx_state
from app.relationship_service import (
    compute_proactive_boost,
    get_or_create_relationship,
    list_open_issues,
    proactive_boost_from_issues,
    tick_open_issues,
    touch_issue,
)
from app.schemas import IssueDelta, RelationshipDelta
from app.models import RelationshipIssue


def _issue(importance: int = 5, rounds_since: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        issue_type="lie",
        text="Борис солгал Ане",
        importance=importance,
        rounds_since_last_mention=rounds_since,
    )


# ---------------------------------------------------------------------------
# Pure formula (§7.4)
# ---------------------------------------------------------------------------
class TestProactiveBoostFormula:
    def test_no_issues_zero_boost(self):
        assert proactive_boost_from_issues([]) == 0.0

    def test_fresh_high_importance_positive(self):
        boost = proactive_boost_from_issues([_issue(importance=10, rounds_since=0)])
        expected = settings.issue_proactive_coeff * (10 / 10.0) * 1.0
        assert boost == pytest.approx(expected)
        assert boost > 0.0

    def test_importance_weighting(self):
        high = proactive_boost_from_issues([_issue(importance=10, rounds_since=0)])
        low = proactive_boost_from_issues([_issue(importance=5, rounds_since=0)])
        assert high == pytest.approx(2 * low)

    def test_stale_issue_no_salience(self):
        decay = settings.issue_salience_decay_rounds
        stale = proactive_boost_from_issues([_issue(importance=10, rounds_since=decay)])
        assert stale == 0.0
        very_stale = proactive_boost_from_issues(
            [_issue(importance=10, rounds_since=decay * 10)]
        )
        assert very_stale == 0.0

    def test_salience_decays_linearly(self):
        decay = settings.issue_salience_decay_rounds
        rounds_since = decay // 2
        half = proactive_boost_from_issues(
            [_issue(importance=10, rounds_since=rounds_since)]
        )
        fresh = proactive_boost_from_issues([_issue(importance=10, rounds_since=0)])
        expected = settings.issue_proactive_coeff * (10 / 10.0) * (1 - rounds_since / decay)
        assert half == pytest.approx(expected)
        assert 0.0 < half < fresh

    def test_not_count_based(self):
        # Three weak hooks (importance 3) must not weigh more than one major
        # conflict (importance 10).
        three_minor = proactive_boost_from_issues(
            [_issue(importance=3, rounds_since=0)] * 3
        )
        one_major = proactive_boost_from_issues([_issue(importance=10, rounds_since=0)])
        assert three_minor < one_major

    def test_stale_issue_does_not_add_weight(self):
        # A naive `const * len(open_issues)` would add weight for a stale issue;
        # the weighted formula contributes ~0 (zero salience).
        decay = settings.issue_salience_decay_rounds
        fresh = proactive_boost_from_issues([_issue(importance=10, rounds_since=0)])
        fresh_plus_stale = proactive_boost_from_issues(
            [
                _issue(importance=10, rounds_since=0),
                _issue(importance=10, rounds_since=decay),
            ]
        )
        assert fresh_plus_stale == pytest.approx(fresh)

    def test_cap_respected(self):
        many_hot = proactive_boost_from_issues(
            [_issue(importance=10, rounds_since=0)] * 50
        )
        assert many_hot == pytest.approx(settings.issue_proactive_boost_cap)

    def test_deterministic(self):
        issues = [_issue(importance=8, rounds_since=2), _issue(importance=6, rounds_since=0)]
        assert proactive_boost_from_issues(issues) == proactive_boost_from_issues(issues)

    def test_never_negative(self):
        assert proactive_boost_from_issues([_issue(importance=-5, rounds_since=-3)]) >= 0.0


# ---------------------------------------------------------------------------
# Per-character aggregation (DB)
# ---------------------------------------------------------------------------
class TestComputeProactiveBoost:
    async def test_zero_when_no_issues(self, db_session: AsyncSession, chat, three_characters):
        a, _, _ = three_characters
        assert await compute_proactive_boost(db_session, chat.id, a.id) == 0.0

    async def test_own_edge_issue_counts(
        self, db_session: AsyncSession, chat, three_characters
    ):
        a, b, _ = three_characters
        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        issue = RelationshipIssue(
            relationship_id=rel.id,
            issue_type="lie",
            text="Борис солгал Ане",
            importance=8,
            state="open",
            rounds_since_last_mention=0,
        )
        db_session.add(issue)
        await db_session.commit()

        boost = await compute_proactive_boost(db_session, chat.id, a.id)
        expected = settings.issue_proactive_coeff * (8 / 10.0)
        assert boost == pytest.approx(expected)

    async def test_other_characters_edge_ignored(
        self, db_session: AsyncSession, chat, three_characters
    ):
        a, b, c = three_characters
        rel = await get_or_create_relationship(db_session, chat.id, b.id, c.id)
        issue = RelationshipIssue(
            relationship_id=rel.id,
            issue_type="lie",
            text="Борис солгал Кате",
            importance=9,
            state="open",
        )
        db_session.add(issue)
        await db_session.commit()
        # A has no outgoing issues -> zero; B's edge is B->C -> counts for B.
        assert await compute_proactive_boost(db_session, chat.id, a.id) == 0.0
        assert await compute_proactive_boost(db_session, chat.id, b.id) > 0.0

    async def test_resolved_issue_excluded(
        self, db_session: AsyncSession, chat, three_characters
    ):
        a, b, _ = three_characters
        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        from app.relationship_service import create_issue, resolve_issue

        issue = await create_issue(
            db_session, rel, issue_type="lie",
            text="Борис солгал Ане", importance=7,
        )
        assert await compute_proactive_boost(db_session, chat.id, a.id) > 0.0
        await resolve_issue(db_session, rel, issue.id)
        assert await compute_proactive_boost(db_session, chat.id, a.id) == 0.0


# ---------------------------------------------------------------------------
# Salience tick / touch (§7.4)
# ---------------------------------------------------------------------------
class TestSalienceTick:
    async def test_fresh_issue_counter_zero(
        self, db_session: AsyncSession, chat, three_characters
    ):
        a, b, _ = three_characters
        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        issue = await self._create(db_session, rel, "r1")
        assert issue.rounds_since_last_mention == 0

    async def test_tick_increments_unmentioned(
        self, db_session: AsyncSession, chat, three_characters
    ):
        a, b, _ = three_characters
        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        await self._create(db_session, rel, "r1")
        await tick_open_issues(db_session, chat.id, round_id="r2")
        issue = (await list_open_issues(db_session, rel))[0]
        assert issue.rounds_since_last_mention == 1

    async def test_tick_skips_issue_created_this_round(
        self, db_session: AsyncSession, chat, three_characters
    ):
        a, b, _ = three_characters
        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        await self._create(db_session, rel, "r1")
        await tick_open_issues(db_session, chat.id, round_id="r1")
        issue = (await list_open_issues(db_session, rel))[0]
        assert issue.rounds_since_last_mention == 0

    async def test_touch_resets_counter(
        self, db_session: AsyncSession, chat, three_characters
    ):
        a, b, _ = three_characters
        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        issue = await self._create(db_session, rel, "r1")
        await tick_open_issues(db_session, chat.id, round_id="r2")
        await touch_issue(db_session, issue, round_id="r3")
        assert issue.rounds_since_last_mention == 0
        assert issue.last_mention_round_id == "r3"

    async def test_mentioned_reset_others_incremented(
        self, db_session: AsyncSession, chat, three_characters
    ):
        a, b, c = three_characters
        rel_ab = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        rel_ac = await get_or_create_relationship(db_session, chat.id, a.id, c.id)
        mentioned = await self._create(db_session, rel_ab, "r1")
        other = await self._create(db_session, rel_ac, "r1")

        await tick_open_issues(
            db_session, chat.id, round_id="r2", mentioned_ids=[mentioned.id]
        )
        mentioned_now = (await list_open_issues(db_session, rel_ab))[0]
        other_now = (await list_open_issues(db_session, rel_ac))[0]
        assert mentioned_now.rounds_since_last_mention == 0
        assert mentioned_now.last_mention_round_id == "r2"
        assert other_now.rounds_since_last_mention == 1

    @staticmethod
    async def _create(db, rel, round_id: str) -> RelationshipIssue:
        from app.relationship_service import create_issue

        return await create_issue(
            db, rel, issue_type="lie",
            text=f"Борис солгал Ане {rel.target_character_id}",
            importance=7, round_id=round_id,
        )


# ---------------------------------------------------------------------------
# Proactive action probability in generation (ollama_client)
# ---------------------------------------------------------------------------
def _make_character(name: str = "Alice") -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        name=name,
        personality="Curious",
        traits="Brave",
        background="",
        speech_style="",
        example_messages="",
        boundaries="",
        relationships="",
        temperature=None,
    )


def _fake_chat_client(captured: dict) -> httpx.AsyncClient:
    async def fake_post(url, json=None, **kwargs):
        captured["url"] = url
        captured["payload"] = json
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {
            "message": {"role": "assistant", "content": "Proactive reply with enough length."}
        }
        return response

    client = httpx.AsyncClient(base_url="http://test")
    client.post = fake_post  # type: ignore[method-assign]
    return client


async def _generate_with_boost(proactive_boost: float, random_value: float) -> dict:
    captured: dict = {}
    client = _fake_chat_client(captured)
    ctx_state.remove(1)
    with (
        patch("app.ollama_client.settings.use_chat_api", True),
        patch("app.ollama_client.settings.enable_thinking", False),
        patch("app.ollama_client.settings.scene_advancement_enabled", True),
        patch("app.ollama_client.settings.proactive_action_chance", 0.0),
        patch("app.ollama_client.random.random", return_value=random_value),
    ):
        async for event in ollama_client.generate(
            client,
            chat_id=1,
            character=_make_character(),
            messages_history=[],
            general_prompt="Scene",
            memories=[],
            other_character_names=["Bob"],
            stagnation_rounds=0,
            proactive_boost=proactive_boost,
        ):
            if event["type"] == "response":
                break
    return captured


@pytest.mark.asyncio
async def test_boost_pushes_probability_over_threshold():
    captured = await _generate_with_boost(proactive_boost=0.6, random_value=0.5)
    messages = captured["payload"]["messages"]
    assert "<proactive>" in messages[1]["content"]


@pytest.mark.asyncio
async def test_zero_boost_keeps_old_behavior():
    captured = await _generate_with_boost(proactive_boost=0.0, random_value=0.5)
    messages = captured["payload"]["messages"]
    # chance patched to 0.0 and boost 0.0 -> threshold 0.0 < 0.5 -> no cue.
    assert "<proactive>" not in messages[1]["content"]


@pytest.mark.asyncio
async def test_boost_capped_at_one():
    captured = await _generate_with_boost(proactive_boost=10.0, random_value=0.99)
    messages = captured["payload"]["messages"]
    # threshold = min(0.0 + 10.0, 1.0) = 1.0 > 0.99 -> cue present.
    assert "<proactive>" in messages[1]["content"]
