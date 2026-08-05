"""Tests for the Belief System (Plans/update20.md §9, Sprint 5).

Pipeline world_event → perceive → attention → belief update
(app.belief_service). Covering:

- character does NOT learn what it did not perceive (absent presence → skip);
- told_by confidence depends on trust(believer → teller);
- suspicion when confidence is low / no world confirmation;
- read-path stays empty while ``beliefs_enabled`` is off (mask fallback).
"""

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app import belief_service
from app.config import settings


async def _seed_event(
    db: AsyncSession,
    chat_id: int,
    author_id: int,
    *,
    round_id: str = "r-test-1",
    action: dict | None = None,
) -> dict:
    """Create a Message + WorldEvent + return the event dict (like get_round_world_events)."""
    message = models.Message(
        chat_id=chat_id,
        character_id=author_id,
        role="character",
        content="Яркая реплика персонажа.",
    )
    db.add(message)
    await db.flush()

    world_event = models.WorldEvent(
        chat_id=chat_id,
        character_id=author_id,
        message_id=message.id,
        event_type="speech",
        round_id=round_id,
        target_character_ids="[]",
        action=json.dumps(action or {}),
    )
    db.add(world_event)
    await db.commit()
    await db.refresh(world_event)
    return {
        "id": world_event.id,
        "message_id": world_event.message_id,
        "character_id": world_event.character_id,
        "event_type": world_event.event_type,
        "target_character_ids": [],
        "action": action or {},
    }


async def _seed_presence(
    db: AsyncSession,
    message_id: int,
    character_id: int,
    *,
    presence: str = "present",
    attention: float | None = None,
) -> None:
    await crud.upsert_message_presence_batch(
        db,
        [
            schemas.MessagePresenceCreate(
                message_id=message_id,
                character_id=character_id,
                presence=presence,  # type: ignore[arg-type]
                attention=attention,
            )
        ],
    )


@pytest.fixture(autouse=True)
def _beliefs_enabled():
    """Enable the Belief System for the belief pipeline tests (canary off by default)."""
    old = settings.beliefs_enabled
    settings.beliefs_enabled = True
    yield
    settings.beliefs_enabled = old


class TestPerceptionGating:
    """Character does not learn what it did not perceive (isolation R2)."""

    async def test_absent_presence_writes_no_belief(
        self, db_session, chat, three_characters
    ):
        a, b, _c = three_characters
        event = await _seed_event(
            db_session,
            chat.id,
            a.id,
            action={"actor": a.name, "action": "улыбнулся", "object": b.name},
        )
        await _seed_presence(
            db_session, event["message_id"], b.id, presence="absent"
        )

        report = await belief_service.update_beliefs_from_round(
            db_session, chat.id, "r-test-1", three_characters
        )
        assert report["written"] == 0
        assert report["updated"] == 0
        assert report["skipped"] >= 1
        beliefs = await crud.get_beliefs_for_character(db_session, b.id)
        assert beliefs == []

    async def test_present_presence_writes_direct_observation(
        self, db_session, chat, three_characters
    ):
        a, b, _c = three_characters
        event = await _seed_event(
            db_session,
            chat.id,
            a.id,
            action={"actor": a.name, "action": "улыбнулся", "object": b.name},
        )
        await _seed_presence(db_session, event["message_id"], b.id, presence="present")

        report = await belief_service.update_beliefs_from_round(
            db_session, chat.id, "r-test-1", three_characters
        )
        assert report["written"] == 1
        beliefs = await crud.get_beliefs_for_character(db_session, b.id)
        assert len(beliefs) == 1
        b0 = beliefs[0]
        assert b0.subject == a.name
        assert b0.source == "direct_observation"
        assert b0.type == "fact"
        assert b0.confidence >= 0.75
        assert b0.world_truth_ref is not None

    async def test_low_attention_skips_belief(
        self, db_session, chat, three_characters
    ):
        old_attention = settings.attention_enabled
        old_low = settings.attention_low
        settings.attention_enabled = True
        settings.attention_low = 0.5
        try:
            a, b, _c = three_characters
            event = await _seed_event(
                db_session,
                chat.id,
                a.id,
                action={"actor": a.name, "action": "бормочет", "object": b.name},
            )
            await _seed_presence(
                db_session, event["message_id"], b.id, presence="audible", attention=0.1
            )

            report = await belief_service.update_beliefs_from_round(
                db_session, chat.id, "r-test-1", three_characters
            )
            assert report["written"] == 0
            assert report["skipped"] >= 1
            assert await crud.get_beliefs_for_character(db_session, b.id) == []
        finally:
            settings.attention_enabled = old_attention
            settings.attention_low = old_low


class TestToldByTrust:
    async def _told_by_event(self, db_session, chat, a, b, trust: int | None):
        event = await _seed_event(
            db_session,
            chat.id,
            a.id,
            action={"actor": a.name, "action": "рассказал", "object": b.name},
        )
        await _seed_presence(db_session, event["message_id"], b.id, presence="told")
        if trust is not None:
            db_session.add(
                models.CharacterRelationship(
                    chat_id=chat.id,
                    source_character_id=b.id,
                    target_character_id=a.id,
                    relationship_type="friend",
                    trust=trust,
                )
            )
            await db_session.commit()
        return event

    async def test_high_trust_boosts_told_by(
        self, db_session, chat, three_characters
    ):
        a, b, _c = three_characters
        await self._told_by_event(db_session, chat, a, b, trust=90)
        await belief_service.update_beliefs_from_round(
            db_session, chat.id, "r-test-1", three_characters
        )
        b0 = (await crud.get_beliefs_for_character(db_session, b.id))[0]
        assert b0.source == "told_by"
        assert b0.type == "belief"
        assert b0.confidence == pytest.approx(
            belief_service.told_by_confidence(90), abs=1e-6
        )
        assert 0.7 < b0.confidence <= 0.8

    async def test_low_trust_drops_told_by(
        self, db_session, chat, three_characters
    ):
        a, b, _c = three_characters
        await self._told_by_event(db_session, chat, a, b, trust=10)
        await belief_service.update_beliefs_from_round(
            db_session, chat.id, "r-test-1", three_characters
        )
        b0 = (await crud.get_beliefs_for_character(db_session, b.id))[0]
        assert b0.source == "told_by"
        assert b0.type == "suspicion"
        assert b0.confidence == pytest.approx(0.26, abs=1e-6)

    async def test_missing_trust_edge_is_neutral(
        self, db_session, chat, three_characters
    ):
        a, b, _c = three_characters
        await self._told_by_event(db_session, chat, a, b, trust=None)
        await belief_service.update_beliefs_from_round(
            db_session, chat.id, "r-test-1", three_characters
        )
        b0 = (await crud.get_beliefs_for_character(db_session, b.id))[0]
        assert b0.confidence == pytest.approx(0.5, abs=1e-6)


class TestSuspicion:
    async def test_unconfirmed_rumor_is_suspicion(
        self, db_session, chat, three_characters
    ):
        a, b, _c = three_characters
        event = await _seed_event(
            db_session,
            chat.id,
            a.id,
            action={"actor": a.name, "action": "шепчутся", "object": b.name},
        )
        await _seed_presence(db_session, event["message_id"], b.id, presence="audible")

        await belief_service.update_beliefs_from_round(
            db_session, chat.id, "r-test-1", three_characters
        )
        b0 = (await crud.get_beliefs_for_character(db_session, b.id))[0]
        assert b0.source == "rumor"
        assert b0.type == "suspicion"
        assert b0.confidence == pytest.approx(0.3, abs=1e-6)
        assert b0.world_truth_ref is None


class TestMaskFallback:
    async def test_read_path_empty_when_disabled(self, db_session, chat, three_characters):
        old = settings.beliefs_enabled
        settings.beliefs_enabled = False
        try:
            a, b, _c = three_characters
            event = await _seed_event(
                db_session,
                chat.id,
                a.id,
                action={"actor": a.name, "action": "улыбнулся", "object": b.name},
            )
            await _seed_presence(db_session, event["message_id"], b.id, presence="present")

            await belief_service.update_beliefs_from_round(
                db_session, chat.id, "r-test-1", three_characters
            )
            # read-path (context builder / mask) must see nothing until flag on
            assert await crud.get_beliefs_for_character(db_session, b.id) == []
            # pure functions are deterministic regardless of the flag
            assert belief_service.source_for_presence("present") == "direct_observation"
            assert belief_service.belief_type(
                "direct_observation", 0.85, confirmed=True
            ) == "fact"
        finally:
            settings.beliefs_enabled = old
