"""Tests for round_id parsing and round message lookup (Sprint 4 item 4.2)."""

from app import crud
from app import schemas
from app.models import RelationshipEvent
from app.relationship_service import get_or_create_relationship


class TestRoundLookup:
    async def test_normal_round(self, db_session, chat, three_characters):
        user = await crud.create_message(
            db_session,
            schemas.MessageCreate(chat_id=chat.id, role="user", content="Привет"),
        )
        reply = await crud.create_message(
            db_session,
            schemas.MessageCreate(
                chat_id=chat.id,
                role="character",
                content="Здравствуй",
                character_id=three_characters[0].id,
            ),
        )
        messages = await crud.get_round_messages_by_round_id(
            db_session, f"r{chat.id}-m{user.id}"
        )
        assert [m.id for m in messages] == [user.id, reply.id]

    async def test_stops_before_next_user_message(self, db_session, chat, three_characters):
        user1 = await crud.create_message(
            db_session,
            schemas.MessageCreate(chat_id=chat.id, role="user", content="А"),
        )
        reply = await crud.create_message(
            db_session,
            schemas.MessageCreate(
                chat_id=chat.id,
                role="character",
                content="Б",
                character_id=three_characters[0].id,
            ),
        )
        user2 = await crud.create_message(
            db_session,
            schemas.MessageCreate(chat_id=chat.id, role="user", content="В"),
        )
        messages = await crud.get_round_messages_by_round_id(
            db_session, f"r{chat.id}-m{user1.id}"
        )
        assert [m.id for m in messages] == [user1.id, reply.id]
        assert user2.id not in [m.id for m in messages]

    async def test_malformed_round_id_returns_empty(self, db_session, chat):
        assert await crud.get_round_messages_by_round_id(db_session, "") == []
        assert await crud.get_round_messages_by_round_id(db_session, "bogus") == []
        assert await crud.get_round_messages_by_round_id(db_session, "r5-abc") == []

    async def test_unknown_user_message_returns_empty(self, db_session, chat):
        assert await crud.get_round_messages_by_round_id(
            db_session, f"r{chat.id}-m99999999"
        ) == []

    async def test_latest_round_id_without_events(self, db_session, chat):
        assert await crud.get_latest_round_id(db_session, chat.id) is None

    async def test_latest_round_id_from_events(self, db_session, chat, three_characters):
        a, b, _ = three_characters
        rel = await get_or_create_relationship(db_session, chat.id, a.id, b.id)
        for round_id in (f"r{chat.id}-m1", f"r{chat.id}-m2"):
            db_session.add(
                RelationshipEvent(
                    relationship_id=rel.id,
                    kind="llm",
                    description="x",
                    round_id=round_id,
                )
            )
        await db_session.commit()
        assert await crud.get_latest_round_id(db_session, chat.id) == f"r{chat.id}-m2"
