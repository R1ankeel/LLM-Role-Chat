"""Sprint 3 — Character State (Plans/update20.md §8, §23).

Покрывает:
- `emotion_engine` — детерминированные правила (эмоции из relationship deltas,
  стресс из событий раунда, mood, decay, Sensors-caps);
- `character_state.update_states_from_round` — запись в character_states из
  relationship_events + world_events раунда; откат (флаг off → не пишет);
  нет дублирования location/relationships в state;
- рендер блока YOUR STATE;
- стадию `character_state` в post-round pipeline;
- откат без Sensors (fallback на детерминированный путь).
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text
from unittest.mock import AsyncMock, MagicMock

from app import crud
from app import schemas
from app.emotion_engine import (
    compute_state_update,
    decay_emotional_state,
    derive_mood,
    normalize_emotional_state,
    relationship_emotion_deltas,
    stress_delta,
    apply_sensors_proposal,
)
from app.character_state import (
    build_your_state_block,
    update_states_from_round,
    state_to_dict,
)


# ---------------------------------------------------------------------------
# emotion_engine: детерминированные правила (чистые функции)
# ---------------------------------------------------------------------------

class TestEmotionRules:
    def test_normalize_filters_unknown_and_clamps(self):
        out = normalize_emotional_state(
            {"warmth": 0.8, "not_an_emotion": 0.5, "fear": 5.0}
        )
        assert out == {"warmth": 0.8, "fear": 1.0}

    def test_affection_up_drives_warmth_hope(self):
        deltas = [{"delta_affection": 20}]
        out = relationship_emotion_deltas(deltas)
        # affection 20 → 1.0 * 0.25 warmth, 1.0 * 0.10 hope
        assert out["warmth"] == pytest.approx(0.25)
        assert out["hope"] == pytest.approx(0.10)

    def test_trust_down_drives_suspicion_hurt(self):
        deltas = [{"delta_trust": -20}]
        out = relationship_emotion_deltas(deltas)
        # trust 20 → 1.0: suspicion 0.30, hurt 0.10
        assert out["suspicion"] == pytest.approx(0.30)
        assert out["hurt"] == pytest.approx(0.10)

    def test_resentment_up_drives_resentment(self):
        deltas = [{"delta_resentment": 20}]
        out = relationship_emotion_deltas(deltas)
        assert out["resentment"] == pytest.approx(0.30)

    def test_round_cap_limits_single_emotion(self):
        # много дельт одного знака не могут поднять эмоцию выше капа за раунд
        deltas = [
            {"delta_affection": 20},
            {"delta_attraction": 20},
            {"delta_trust": 20},
        ]
        out = relationship_emotion_deltas(deltas, round_cap=0.4)
        assert out["warmth"] == pytest.approx(0.35)
        assert all(v <= 0.4 for v in out.values())

    def test_stress_from_emotional_events(self):
        events = [
            {"emotional_salience": 0.8, "importance": 5.0},
            {"emotional_salience": 0.3, "importance": 5.0},  # < 0.5 — не заряжен
        ]
        out = stress_delta(events, [], round_cap=0.2)
        # 0.8*0.10 + min(5/10,0.05) = 0.08+0.05 = 0.13
        assert out == pytest.approx(0.13)

    def test_stress_from_negative_deltas(self):
        deltas = [{"delta_trust": -20, "delta_affection": -20}]
        out = stress_delta([], deltas, round_cap=0.2)
        # trust: 1.0*0.06 + affection: 1.0*0.06 = 0.12
        assert out == pytest.approx(0.12)

    def test_decay_reduces_and_drops_below_threshold(self):
        out = decay_emotional_state({"warmth": 0.50, "hope": 0.05})
        assert out["warmth"] == pytest.approx(0.45)
        assert "hope" not in out

    def test_mood_from_dominant_emotion(self):
        assert derive_mood({"suspicion": 0.7}, 0.1) == "wary"
        assert derive_mood({"warmth": 0.7}, 0.1) == "warm"

    def test_mood_panicked_at_high_stress(self):
        assert derive_mood({}, 0.9) == "panicked"
        assert derive_mood({}, 0.5) == "tense"


class TestSensorsProposal:
    def test_sensors_shift_within_cap(self):
        state = {"suspicion": 0.2}
        proposal = {"emotion": "suspicion", "intensity": 0.9, "confidence": 1.0}
        out = apply_sensors_proposal(state, proposal, intensity_cap=0.3)
        # shift = (0.9-0.2)*1.0 = 0.7 → зажат до 0.3
        assert out["suspicion"] == pytest.approx(0.5)

    def test_sensors_invalid_emotion_noop(self):
        state = {"warmth": 0.2}
        assert apply_sensors_proposal(state, {"emotion": "xyz"}) == state

    def test_sensors_none_noop(self):
        state = {"warmth": 0.2}
        assert apply_sensors_proposal(state, None) == state


class TestComputeStateUpdate:
    def test_deterministic_update_from_deltas_and_events(self):
        update = compute_state_update(
            emotional_state={},
            stress=None,
            relationship_deltas=[{"delta_trust": -20}],
            round_events=[{"emotional_salience": 0.8, "importance": 5.0}],
        )
        # suspicion 0.30, hurt 0.10;
        # stress: baseline decay (0.1) + event (0.13) + trust↓ (0.06) = 0.29
        assert update["emotional_state"]["suspicion"] == pytest.approx(0.30)
        assert update["emotional_state"]["hurt"] == pytest.approx(0.10)
        assert update["stress"] == pytest.approx(0.29)
        # доминирующей эмоции (≥0.50) нет, стресс ≥0.25 → tense
        assert update["mood"] == "tense"

    def test_sensors_proposal_applied_in_caps(self):
        update = compute_state_update(
            emotional_state={"suspicion": 0.2},
            stress=0.1,
            sensors_proposal={
                "emotion": "suspicion", "intensity": 0.9, "confidence": 1.0
            },
        )
        # decay → 0.18, затем shift зажат до капа 0.3 → 0.48
        assert update["emotional_state"]["suspicion"] == pytest.approx(0.48)
        # suspicion 0.48 < порога доминирования (0.50), стресс низкий → neutral
        assert update["mood"] == "neutral"

    def test_decay_of_old_emotions(self):
        update = compute_state_update(emotional_state={"warmth": 0.8}, stress=0.1)
        assert update["emotional_state"]["warmth"] == pytest.approx(0.72)


# ---------------------------------------------------------------------------
# character_state: интеграция с БД (запись из events/deltas раунда)
# ---------------------------------------------------------------------------

@pytest.fixture
def enable_character_state(monkeypatch):
    monkeypatch.setattr(
        "app.character_state.settings.character_state_enabled", True
    )
    monkeypatch.setattr("app.post_round_pipeline.settings.character_state_enabled", True)


async def _create_world_event(db_session, chat, character, *, salience=0.8):
    from app import models

    event = models.WorldEvent(
        chat_id=chat.id,
        character_id=character.id,
        event_type="speech",
        location="",
        round_id="r1-m1",
        target_character_ids="[]",
        action=json.dumps({"actor": character.name, "action": "говорит"}),
        importance=6.0,
        story_salience=0.6,
        emotional_salience=salience,
    )
    db_session.add(event)
    await db_session.commit()
    return event


@pytest.mark.asyncio
async def test_update_states_from_round_writes_emotions(
    enable_character_state, db_session, chat, three_characters
):
    a, b, _ = three_characters
    # relationship delta A→B (trust падает) + событие с эмоциональной салиенсностью
    delta = schemas.RelationshipDelta(
        source_character_id=a.id,
        target_character_id=b.id,
        delta_trust=-20,
        importance=7,
    )
    from app.relationship_service import apply_delta

    await apply_delta(db_session, delta, chat.id, round_id="r1-m1")
    await _create_world_event(db_session, chat, a, salience=0.8)

    report = await update_states_from_round(
        db_session, chat.id, "r1-m1", [a, b]
    )

    assert report["states"] == 2
    assert report["updated"] == 2
    state = await crud.get_character_state(db_session, a.id)
    emotions = json.loads(state.emotional_state)
    # suspicion/hurt от падения trust
    assert emotions.get("suspicion", 0.0) == pytest.approx(0.30)
    assert emotions.get("hurt", 0.0) == pytest.approx(0.10)
    # стресс: baseline (0.1) + событие (0.13) + trust↓ (0.06) = 0.29
    assert state.stress is not None and state.stress == pytest.approx(0.29)
    assert state.updated_round_id == "r1-m1"


@pytest.mark.asyncio
async def test_state_has_no_location_or_relationships(
    enable_character_state, db_session, chat, three_characters
):
    """НЕ хранить локацию/отношения в state (§8): row не содержит этих полей."""
    a, _, _ = three_characters
    await crud.get_or_create_character_state(db_session, chat.id, a.id, round_id="r1")
    state = await crud.get_character_state(db_session, a.id)
    data = state_to_dict(state)
    assert "location" not in data
    assert "relationship" not in data
    assert "affection" not in data
    assert data["emotional_state"] == {}


@pytest.mark.asyncio
async def test_update_idempotent_no_duplicate_rows(
    enable_character_state, db_session, chat, three_characters
):
    a, _, _ = three_characters
    await _create_world_event(db_session, chat, a, salience=0.6)

    await update_states_from_round(db_session, chat.id, "r1-m1", [a])
    await update_states_from_round(db_session, chat.id, "r1-m1", [a])

    count = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM character_states WHERE character_id = :cid"),
            {"cid": a.id},
        )
    ).scalar()
    assert count == 1  # одна строка на персонажа (unique character_id)


@pytest.mark.asyncio
async def test_update_rolls_back_without_sensors(
    db_session, chat, three_characters
):
    """Флаг off по умолчанию: стадия не пишет (canary-откат)."""
    a, _, _ = three_characters
    await _create_world_event(db_session, chat, a, salience=0.8)
    from app.post_round_pipeline import _stage_character_state

    report = await _stage_character_state(
        MagicMock(), db_session, chat_id=chat.id,
        round_id="r1-m1", characters=[a],
    )
    assert report["ok"] is True
    assert report.get("skipped") == "flag off"
    assert await crud.get_character_state(db_session, a.id) is None


# ---------------------------------------------------------------------------
# Sensors: предложение применяется в рамках caps, ошибка → детерминированный путь
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sensors_proposal_failure_falls_back_to_deterministic(
    enable_character_state, monkeypatch, db_session, chat, three_characters
):
    monkeypatch.setattr(
        "app.sensors_service.sensors_service.is_enabled", lambda task: True
    )

    async def failing_run(*args, **kwargs):
        raise RuntimeError("Sensors недоступен")

    monkeypatch.setattr("app.sensors_service.sensors_service.run", failing_run)

    a, b, _ = three_characters
    delta = schemas.RelationshipDelta(
        source_character_id=a.id,
        target_character_id=b.id,
        delta_affection=20,
        importance=6,
    )
    from app.relationship_service import apply_delta

    await apply_delta(db_session, delta, chat.id, round_id="r1-m1")

    report = await update_states_from_round(
        db_session, chat.id, "r1-m1", [a], client=MagicMock()
    )
    assert report["sensors_used"] == 0
    state = await crud.get_character_state(db_session, a.id)
    emotions = json.loads(state.emotional_state)
    assert emotions.get("warmth", 0.0) == pytest.approx(0.25)
    assert report["updated"] == 1


# ---------------------------------------------------------------------------
# YOUR STATE блок
# ---------------------------------------------------------------------------

def test_build_your_state_block_renders_emotions_mood_stress():
    state = MagicMock(
        emotional_state=json.dumps({"suspicion": 0.5, "warmth": 0.02}),
        mood="wary",
        stress=0.25,
        physical_state=json.dumps({"повреждение": "лёгкое"}),
        attention="следит за Борисом",
        active_goal="выяснить правду",
    )
    block = build_your_state_block(state)
    assert "<your_state>" in block
    assert "Эмоции: suspicion" in block
    assert "warmth" not in block  # ниже порога рендера
    assert "Настроение: wary" in block
    assert "Стресс: 0.25" in block
    assert "Фокус: следит за Борисом" in block
    assert "Цель: выяснить правду" in block


def test_build_your_state_block_empty_state():
    assert build_your_state_block(None) == ""


def test_build_your_state_block_no_location_leak():
    state = MagicMock(
        emotional_state=json.dumps({"warmth": 0.5}),
        mood="warm",
        stress=0.1,
        physical_state=json.dumps({}),
        attention="",
        active_goal="",
    )
    block = build_your_state_block(state)
    assert "локация" not in block.lower()
    assert "отношен" not in block.lower()


# ---------------------------------------------------------------------------
# CharacterStateRead schema
# ---------------------------------------------------------------------------

def test_character_state_read_parses_json_fields():
    data = {
        "id": 1,
        "chat_id": 10,
        "character_id": 42,
        "emotional_state": '{"warmth": 0.6, "bogus": 5}',
        "mood": "warm",
        "stress": 0.3,
        "physical_state": '{"рука": "больная"}',
        "attention": None,
        "current_focus_id": None,
        "active_goal": "спасти друга",
        "personal_goals": "[]",
        "updated_round_id": "r1-m1",
    }
    parsed = schemas.CharacterStateRead.model_validate(data)
    assert parsed.emotional_state == {"warmth": 0.6}  # bogus отфильтрован
    assert parsed.mood == "warm"
    assert parsed.active_goal == "спасти друга"
    assert parsed.updated_round_id == "r1-m1"
    assert parsed.physical_state == {"рука": "больная"}
