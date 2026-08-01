"""API tests for Memory CRUD operations."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401
from app.database import Base, get_db
from app.main import app
from app import crud
from app import schemas


# Override the database dependency for testing
@pytest.fixture(scope="session")
def test_engine():
    """Create an in-memory SQLite engine for testing."""
    engine = create_engine(
        "sqlite:///:memory:?cache=shared",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture
def test_db_session(test_engine):
    """Create a database session for testing."""
    connection = test_engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection)()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def client(test_db_session):
    """Create a TestClient with overridden database dependency."""
    def override_get_db():
        try:
            yield test_db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def test_chat(client):
    """Create a test chat via API."""
    response = client.post("/api/chats", json={"name": "Test Chat", "general_prompt": "A test scene"})
    assert response.status_code == 201
    return response.json()


@pytest.fixture
def test_character(client, test_chat):
    """Create a test character via API."""
    response = client.post(
        f"/api/chats/{test_chat['id']}/characters",
        json={"name": "Test Character", "personality": "Test personality", "order_index": 0},
    )
    assert response.status_code == 201
    return response.json()


def test_create_memory(client, test_chat, test_character):
    """POST /characters/{id}/memories - create a new memory."""
    response = client.post(
        f"/api/characters/{test_character['id']}/memories",
        json={
            "chat_id": test_chat["id"],
            "character_id": test_character["id"],
            "content": "Test memory content",
            "importance": 0.8,
            "category": "событие",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["content"] == "Test memory content"
    assert data["importance"] == 0.8
    assert data["category"] == "событие"
    assert data["character_id"] == test_character["id"]
    assert data["chat_id"] == test_chat["id"]
    assert "id" in data
    assert "created_at" in data


def test_create_memory_duplicate_fails(client, test_chat, test_character):
    """Creating duplicate memory content returns 409."""
    # Create first memory
    client.post(
        f"/api/characters/{test_character['id']}/memories",
        json={
            "chat_id": test_chat["id"],
            "character_id": test_character["id"],
            "content": "Duplicate memory content",
            "importance": 0.5,
            "category": "событие",
        },
    )
    # Try to create duplicate
    response = client.post(
        f"/api/characters/{test_character['id']}/memories",
        json={
            "chat_id": test_chat["id"],
            "character_id": test_character["id"],
            "content": "Duplicate memory content",
            "importance": 0.5,
            "category": "событие",
        },
    )
    assert response.status_code == 409
    assert "Дубликат" in response.json()["detail"]


def test_update_memory(client, test_chat, test_character):
    """PUT /memories/{id} - update memory content/importance/category."""
    # Create memory first
    create_resp = client.post(
        f"/api/characters/{test_character['id']}/memories",
        json={
            "chat_id": test_chat["id"],
            "character_id": test_character["id"],
            "content": "Original content",
            "importance": 0.5,
            "category": "событие",
        },
    )
    memory_id = create_resp.json()["id"]

    # Update memory
    response = client.put(
        f"/api/memories/{memory_id}",
        json={"content": "Updated content", "importance": 0.9, "category": "отношения"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["content"] == "Updated content"
    assert data["importance"] == 0.9
    assert data["category"] == "отношения"
    assert data["id"] == memory_id


def test_delete_memory(client, test_chat, test_character):
    """DELETE /memories/{id} - delete a memory."""
    # Create memory first
    create_resp = client.post(
        f"/api/characters/{test_character['id']}/memories",
        json={
            "chat_id": test_chat["id"],
            "character_id": test_character["id"],
            "content": "To be deleted",
            "importance": 0.5,
            "category": "событие",
        },
    )
    memory_id = create_resp.json()["id"]

    # Delete memory
    response = client.delete(f"/api/memories/{memory_id}")
    assert response.status_code == 204

    # Verify deleted
    get_resp = client.get(f"/api/characters/{test_character['id']}/memories")
    assert memory_id not in [m["id"] for m in get_resp.json()]


def test_get_memories_by_character(client, test_chat, test_character):
    """GET /characters/{id}/memories - list memories for a character."""
    # Create a few memories
    client.post(
        f"/api/characters/{test_character['id']}/memories",
        json={"chat_id": test_chat["id"], "character_id": test_character["id"], "content": "Memory 1", "importance": 0.5, "category": "событие"},
    )
    client.post(
        f"/api/characters/{test_character['id']}/memories",
        json={"chat_id": test_chat["id"], "character_id": test_character["id"], "content": "Memory 2", "importance": 0.7, "category": "отношения"},
    )

    response = client.get(f"/api/characters/{test_character['id']}/memories")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert all(m["character_id"] == test_character["id"] for m in data)


def test_clear_chat_memories_scope(client, test_chat, test_character):
    """DELETE /chats/{id}/messages?scope=messages_memories clears messages and memories."""
    # Create a memory first
    client.post(
        f"/api/characters/{test_character['id']}/memories",
        json={"chat_id": test_chat["id"], "character_id": test_character["id"], "content": "Test memory", "importance": 0.5, "category": "событие"},
    )
    # Verify memory exists
    mem_resp = client.get(f"/api/characters/{test_character['id']}/memories")
    assert len(mem_resp.json()) == 1

    # Create a message
    client.post(
        f"/api/chats/{test_chat['id']}/message",
        json={"content": "Test message"},
    )

    # Clear with messages_memories scope
    response = client.delete(f"/api/chats/{test_chat['id']}/messages?scope=messages_memories")
    assert response.status_code == 204

    # Verify memory is gone
    mem_resp = client.get(f"/api/characters/{test_character['id']}/memories")
    assert len(mem_resp.json()) == 0


def test_clear_chat_full_scope(client, test_chat, test_character):
    """DELETE /chats/{id}/messages?scope=full clears messages, memories, and summaries."""
    # Create a memory
    client.post(
        f"/api/characters/{test_character['id']}/memories",
        json={"chat_id": test_chat["id"], "character_id": test_character["id"], "content": "Test memory", "importance": 0.5, "category": "событие"},
    )

    # Create messages to trigger summary
    for i in range(25):
        client.post(f"/api/chats/{test_chat['id']}/message", json={"content": f"Message {i}"})

    # Clear with full scope
    response = client.delete(f"/api/chats/{test_chat['id']}/messages?scope=full")
    assert response.status_code == 204

    # Verify memories are gone
    mem_resp = client.get(f"/api/characters/{test_character['id']}/memories")
    assert len(mem_resp.json()) == 0


def test_clear_chat_invalid_scope(client, test_chat):
    """DELETE /chats/{id}/messages with invalid scope returns 400."""
    response = client.delete(f"/api/chats/{test_chat['id']}/messages?scope=invalid")
    assert response.status_code == 400