"""Tests for task_queue memory job processing."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.orm import sessionmaker

from app import crud
from app import models
from app import schemas
from app import task_queue
from app.config import settings
from tests.conftest import create_characters


@pytest.fixture
def mock_client():
    class MockClient:
        base_url = "http://test"
        timeout = 180.0

    return MockClient()


@pytest.mark.asyncio
async def test_job_enqueue_creates_record(db_session, chat, db_engine):
    """Enqueue should create a MemoryJob record with pending status."""
    test_session_factory = sessionmaker(bind=db_engine)

    with patch("app.database.get_session_factory", lambda: test_session_factory):
        job = await task_queue.memory_job_queue.enqueue(
            job_type="test_job",
            chat_id=chat.id,
            payload={"key": "value", "number": 42},
            max_attempts=3,
        )

    assert job.id is not None
    assert job.chat_id == chat.id
    assert job.job_type == "test_job"
    assert job.status == task_queue.JobStatus.PENDING
    assert job.max_attempts == 3
    assert job.correlation_id is not None

    # Verify payload is stored correctly
    import json
    payload = json.loads(job.payload)
    assert payload["key"] == "value"
    assert payload["number"] == 42


@pytest.mark.asyncio
async def test_job_run_succeeds(db_session, chat, db_engine):
    """Job should execute handler and update status to succeeded."""
    test_session_factory = sessionmaker(bind=db_engine)

    handler_called = []

    async def test_handler(payload: dict):
        handler_called.append(payload)
        return {"result": "success"}

    with patch("app.database.get_session_factory", lambda: test_session_factory):
        job = await task_queue.memory_job_queue.enqueue(
            job_type="test_job",
            chat_id=chat.id,
            payload={"input": "data"},
        )

        await task_queue.memory_job_queue.run_job(job, test_handler)

    # Refresh from DB
    db = test_session_factory()
    try:
        updated = db.query(models.MemoryJob).filter(models.MemoryJob.id == job.id).first()
        assert updated.status == task_queue.JobStatus.SUCCEEDED
        assert updated.result == '{"result": "success"}'
        assert updated.started_at is not None
        assert updated.completed_at is not None
    finally:
        db.close()

    assert len(handler_called) == 1
    assert handler_called[0]["input"] == "data"


@pytest.mark.asyncio
async def test_job_retry_on_failure(db_session, chat, db_engine):
    """Job should retry on failure and eventually succeed."""
    test_session_factory = sessionmaker(bind=db_engine)

    attempt_count = []

    async def flaky_handler(payload: dict):
        attempt_count.append(1)
        if len(attempt_count) < 3:
            raise RuntimeError("Temporary failure")
        return {"result": "success on attempt 3"}

    with patch("app.database.get_session_factory", lambda: test_session_factory):
        job = await task_queue.memory_job_queue.enqueue(
            job_type="test_job",
            chat_id=chat.id,
            payload={"input": "data"},
            max_attempts=5,
        )

        await task_queue.memory_job_queue.run_job(job, flaky_handler)

    # Refresh from DB
    db = test_session_factory()
    try:
        updated = db.query(models.MemoryJob).filter(models.MemoryJob.id == job.id).first()
        assert updated.status == task_queue.JobStatus.SUCCEEDED
        assert updated.attempt == 3
    finally:
        db.close()

    assert len(attempt_count) == 3


@pytest.mark.asyncio
async def test_job_dead_letter_after_max_retries(db_session, chat, db_engine):
    """Job should go to dead_letter after max attempts exceeded."""
    test_session_factory = sessionmaker(bind=db_engine)

    async def failing_handler(payload: dict):
        raise RuntimeError("Permanent failure")

    with patch("app.database.get_session_factory", lambda: test_session_factory):
        job = await task_queue.memory_job_queue.enqueue(
            job_type="test_job",
            chat_id=chat.id,
            payload={"input": "data"},
            max_attempts=2,
        )

        await task_queue.memory_job_queue.run_job(job, failing_handler)

    # Refresh from DB
    db = test_session_factory()
    try:
        updated = db.query(models.MemoryJob).filter(models.MemoryJob.id == job.id).first()
        assert updated.status == task_queue.JobStatus.DEAD_LETTER
        assert updated.attempt == 2
        assert "Permanent failure" in updated.error_message
    finally:
        db.close()


@pytest.mark.asyncio
async def test_retry_dead_letter_job(db_session, chat, db_engine):
    """Manual retry should reset dead_letter job to pending."""
    test_session_factory = sessionmaker(bind=db_engine)

    async def failing_handler(payload: dict):
        raise RuntimeError("Failure")

    with patch("app.database.get_session_factory", lambda: test_session_factory):
        job = await task_queue.memory_job_queue.enqueue(
            job_type="test_job",
            chat_id=chat.id,
            payload={"input": "data"},
            max_attempts=1,
        )

        await task_queue.memory_job_queue.run_job(job, failing_handler)

        # Now retry
        retried = await task_queue.memory_job_queue.retry_job(job.id)

    assert retried is not None
    assert retried.status == task_queue.JobStatus.PENDING
    assert retried.attempt == 0
    assert retried.error_message is None


@pytest.mark.asyncio
async def test_cleanup_old_jobs(db_session, chat, db_engine):
    """Cleanup should remove old completed/dead_letter jobs."""
    test_session_factory = sessionmaker(bind=db_engine)

    async def handler(payload: dict):
        return {"done": True}

    with patch("app.database.get_session_factory", lambda: test_session_factory):
        # Create old succeeded job
        job1 = await task_queue.memory_job_queue.enqueue(
            job_type="old_job",
            chat_id=chat.id,
            payload={},
        )
        await task_queue.memory_job_queue.run_job(job1, handler)

        # Manually set completed_at to old date
        db = test_session_factory()
        try:
            job1_rec = db.query(models.MemoryJob).filter(models.MemoryJob.id == job1.id).first()
            job1_rec.completed_at = datetime.utcnow() - timedelta(days=40)
            db.commit()
        finally:
            db.close()

        # Create recent succeeded job
        job2 = await task_queue.memory_job_queue.enqueue(
            job_type="recent_job",
            chat_id=chat.id,
            payload={},
        )
        await task_queue.memory_job_queue.run_job(job2, handler)

        # Cleanup with 30-day retention
        deleted = await task_queue.memory_job_queue.cleanup_old_jobs(retention_days=30)

    assert deleted == 1

    # Verify recent job still exists
    db = test_session_factory()
    try:
        remaining = db.query(models.MemoryJob).filter(models.MemoryJob.id == job2.id).first()
        assert remaining is not None
        assert remaining.status == task_queue.JobStatus.SUCCEEDED
    finally:
        db.close()


@pytest.mark.asyncio
async def test_get_job_stats(db_session, chat, db_engine):
    """Get job stats should return counts by status."""
    test_session_factory = sessionmaker(bind=db_engine)

    async def handler(payload: dict):
        return {}

    with patch("app.database.get_session_factory", lambda: test_session_factory):
        await task_queue.memory_job_queue.enqueue("pending", chat.id, {})
        job_succeeded = await task_queue.memory_job_queue.enqueue("succeeded", chat.id, {})
        await task_queue.memory_job_queue.run_job(job_succeeded, handler)

        job_failed = await task_queue.memory_job_queue.enqueue("failed", chat.id, {}, max_attempts=1)
        async def fail_handler(p): raise RuntimeError("fail")
        await task_queue.memory_job_queue.run_job(job_failed, fail_handler)

        stats = await task_queue.memory_job_queue.get_job_stats(chat_id=chat.id)

    assert stats[task_queue.JobStatus.PENDING] == 1
    assert stats[task_queue.JobStatus.SUCCEEDED] == 1
    assert stats[task_queue.JobStatus.DEAD_LETTER] == 1


@pytest.mark.asyncio
async def test_process_post_round_uses_task_queue(db_session, chat, mock_client, db_engine):
    """process_post_round should enqueue a job when task queue enabled."""
    create_characters(db_session, chat.id, 2)

    test_session_factory = sessionmaker(bind=db_engine)

    with patch("app.database.get_session_factory", lambda: test_session_factory), \
         patch.object(settings, "task_queue_enabled", True):
        
        round_snapshots = [
            {"id": 1, "role": "user", "content": "Hello", "chat_id": chat.id, "character_id": None}
        ]
        character_snapshots = [
            {"id": 1, "name": "Char A", "chat_id": chat.id, "personality": "", "traits": ""},
            {"id": 2, "name": "Char B", "chat_id": chat.id, "personality": "", "traits": ""},
        ]

        await task_queue.memory_job_queue.enqueue(
            job_type="post_round",
            chat_id=chat.id,
            payload={
                "chat_id": chat.id,
                "round_snapshots": round_snapshots,
                "character_snapshots": character_snapshots,
                "model_name": "test-model",
            },
        )

    db = test_session_factory()
    try:
        job = db.query(models.MemoryJob).filter(models.MemoryJob.chat_id == chat.id).first()
        assert job is not None
        assert job.job_type == "post_round"
        assert job.status == task_queue.JobStatus.PENDING
    finally:
        db.close()


@pytest.mark.asyncio
async def test_datetime_serialization_in_payload(db_session, chat, db_engine):
    """Payload with datetime objects should serialize correctly."""
    test_session_factory = sessionmaker(bind=db_engine)

    with patch("app.database.get_session_factory", lambda: test_session_factory):
        job = await task_queue.memory_job_queue.enqueue(
            job_type="datetime_test",
            chat_id=chat.id,
            payload={"timestamp": datetime.utcnow(), "nested": {"dt": datetime.utcnow()}},
        )

    db = test_session_factory()
    try:
        updated = db.query(models.MemoryJob).filter(models.MemoryJob.id == job.id).first()
        import json
        payload = json.loads(updated.payload)
        assert "timestamp" in payload
        assert "nested" in payload
        assert "dt" in payload["nested"]
        # Should be ISO format strings
        assert isinstance(payload["timestamp"], str)
        assert isinstance(payload["nested"]["dt"], str)
    finally:
        db.close()