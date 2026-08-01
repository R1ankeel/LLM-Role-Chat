"""Tests for Memory Consolidation (P3)."""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app import memory_service
from app import models
from app import schemas
from app.config import settings
from tests.conftest import create_characters


@pytest.fixture
def mock_client():
    return httpx.AsyncClient(base_url="http://test")


@pytest.mark.asyncio
async def test_jaccard_similarity():
    """Test Jaccard similarity function."""
    # Identical
    assert memory_service.jaccard_similarity("test", "test") == 1.0
    
    # Similar
    sim = memory_service.jaccard_similarity("Алиса любит котиков", "Алиса любит котиков очень сильно")
    assert 0.5 < sim < 1.0
    
    # Different
    sim = memory_service.jaccard_similarity("Алиса любит котиков", "Боб пьёт пиво")
    assert sim < 0.5
    
    # Empty
    assert memory_service.jaccard_similarity("", "test") == 0.0
    assert memory_service.jaccard_similarity("test", "") == 0.0


@pytest.mark.asyncio
async def test_cluster_memories_by_similarity():
    """Test memory clustering by Jaccard similarity."""
    
    class MockMemory:
        def __init__(self, id, content, importance):
            self.id = id
            self.content = content
            self.importance = importance
    
    # Test: Similar facts cluster together
    # "Алиса любит котиков" vs "Алиса любит котиков очень сильно" = 0.600
    # "Боб любит пиво" vs "Боб любит пиво в баре" = 0.600 (both >= 0.55)
    memories = [
        MockMemory(1, "Алиса любит котиков", 0.8),
        MockMemory(2, "Алиса любит котиков очень сильно", 0.7),
        MockMemory(3, "Боб любит пиво", 0.6),
        MockMemory(4, "Боб любит пиво в баре", 0.5),
    ]
    
    # Use threshold 0.55 - both pairs cluster (0.600 >= 0.55)
    clusters = await memory_service._cluster_memories_by_similarity(memories, 0.55)
    
    # Should form 2 clusters: cats cluster + beer cluster
    assert len(clusters) == 2
    cluster_sizes = sorted([len(c) for c in clusters])
    assert cluster_sizes == [2, 2]
    
    # The cats should cluster together
    cat_cluster = None
    for cluster in clusters:
        if any("котиков" in m.content for m in cluster):
            cat_cluster = cluster
            break
    assert cat_cluster is not None
    assert len(cat_cluster) == 2
    
    # Test: Dissimilar facts don't cluster
    memories = [
        MockMemory(1, "Алиса любит котиков", 0.8),
        MockMemory(2, "Боб пьёт пиво в баре", 0.7),
    ]
    clusters = await memory_service._cluster_memories_by_similarity(memories, 0.55)
    assert len(clusters) == 2
    
    # Test: Single memory
    memories = [MockMemory(1, "Тест", 0.5)]
    clusters = await memory_service._cluster_memories_by_similarity(memories, 0.55)
    assert len(clusters) == 1
    assert len(clusters[0]) == 1
    
    # Test: Empty list
    clusters = await memory_service._cluster_memories_by_similarity([], 0.55)
    assert clusters == []


@pytest.mark.asyncio
async def test_cluster_uses_importance_for_ordering():
    """Test that higher importance memories become cluster centers."""
    
    class MockMemory:
        def __init__(self, id, content, importance):
            self.id = id
            self.content = content
            self.importance = importance
    
    # Lower importance fact that's very similar to high importance fact
    # Should be clustered under the high importance one
    memories = [
        MockMemory(1, "Алиса любит котиков", 0.9),  # High importance - cluster center
        MockMemory(2, "Алиса любит котиков очень сильно", 0.5),  # Lower importance
    ]
    
    clusters = await memory_service._cluster_memories_by_similarity(memories, 0.55)
    assert len(clusters) == 1
    assert clusters[0][0].id == 1  # High importance first


@pytest.mark.asyncio
async def test_merge_memory_cluster_llm_fallback(mock_client):
    """Test LLM merge with fallback when LLM fails."""
    
    class MockMemory:
        def __init__(self, id, content, importance):
            self.id = id
            self.content = content
            self.importance = importance
    
    # Mock LLM failure - should fallback to longest fact
    with patch("app.memory_service.httpx.AsyncClient.post", side_effect=Exception("LLM error")):
        cluster = [
            MockMemory(1, "Short", 0.5),
            MockMemory(2, "This is a much longer fact about something important", 0.6),
        ]
        result = await memory_service._merge_memory_cluster_llm(
            mock_client, "test-model", cluster, "TestChar"
        )
        # Should fallback to longest
        assert result == "This is a much longer fact about something important"


@pytest.mark.asyncio
async def test_consolidate_character_memories_no_memories(db_session: AsyncSession):
    """Test consolidation with no memories returns zero counts."""
    from tests.conftest import create_characters
    
    chat = await crud.create_chat(db_session, schemas.ChatCreate(name="Test"))
    characters = await create_characters(db_session, chat.id, 1)
    char = characters[0]
    
    client = MagicMock()
    merged, deleted = await memory_service._consolidate_character_memories(
        db_session, client, "test-model", char.id, char.name,
        0.65, 2, 200
    )
    
    assert merged == 0
    assert deleted == 0


@pytest.mark.asyncio
async def test_consolidate_character_memories_insufficient_for_cluster(db_session: AsyncSession):
    """Test consolidation with memories below min cluster size."""
    from tests.conftest import create_characters
    
    chat = await crud.create_chat(db_session, schemas.ChatCreate(name="Test"))
    characters = await create_characters(db_session, chat.id, 1)
    char = characters[0]
    
    # Create only 1 memory (min_cluster_size = 2)
    await crud.create_memory(db_session, schemas.MemoryCreate(
        chat_id=chat.id,
        character_id=char.id,
        content="Only one fact",
        importance=0.5,
        category="событие",
    ))
    
    client = MagicMock()
    merged, deleted = await memory_service._consolidate_character_memories(
        db_session, client, "test-model", char.id, char.name,
        0.65, 2, 200
    )
    
    assert merged == 0
    assert deleted == 0


@pytest.mark.asyncio
async def test_consolidate_character_memories_merges_similar(db_session: AsyncSession, mock_client):
    """Test consolidation merges similar memories."""
    from tests.conftest import create_characters
    
    chat = await crud.create_chat(db_session, schemas.ChatCreate(name="Test"))
    characters = await create_characters(db_session, chat.id, 1)
    char = characters[0]
    
    # Create similar memories
    await crud.create_memory(db_session, schemas.MemoryCreate(
        chat_id=chat.id, character_id=char.id,
        content="Алиса любит котиков", importance=0.8, category="отношения",
    ))
    await crud.create_memory(db_session, schemas.MemoryCreate(
        chat_id=chat.id, character_id=char.id,
        content="Алиса любит котиков очень сильно", importance=0.7, category="отношения",
    ))
    await crud.create_memory(db_session, schemas.MemoryCreate(
        chat_id=chat.id, character_id=char.id,
        content="Боб пьёт пиво", importance=0.6, category="событие",
    ))
    await db_session.commit()
    
    # Mock LLM merge to return combined fact
    async def mock_post(*args, **kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"response": "Алиса любит котиков очень сильно"})
        return resp
    
    with patch.object(mock_client, "post", side_effect=mock_post):
        merged, deleted = await memory_service._consolidate_character_memories(
            db_session, mock_client, "test-model", char.id, char.name,
            0.55, 2, 200
        )
    
    # Should merge the 2 cat facts, keep beer fact separate
    assert merged == 1
    assert deleted == 1
    
    # Verify remaining memories
    memories = await crud.get_memories_by_character(db_session, char.id)
    assert len(memories) == 2


@pytest.mark.asyncio
async def test_consolidation_job_disabled(db_session: AsyncSession, mock_client):
    """Test consolidation job returns disabled status when turned off."""
    from app.config import settings
    
    original = settings.consolidation_enabled
    settings.consolidation_enabled = False
    
    try:
        result = await memory_service.consolidate_memories_job(
            db=db_session, client=mock_client, model_name="test"
        )
        assert result["status"] == "disabled"
    finally:
        settings.consolidation_enabled = original


@pytest.mark.asyncio
async def test_process_consolidation_job(mock_client):
    """Test the job handler for consolidation."""
    
    # Mock the consolidate_memories_job
    with patch("app.memory_service.consolidate_memories_job", new_callable=AsyncMock) as mock_consolidate:
        mock_consolidate.return_value = {"status": "completed", "chars_processed": 5, "merged": 3, "deleted": 2}
        
        payload = {"model_name": "test-model"}
        result = await memory_service._process_consolidation_job(payload)
        
        assert result["status"] == "completed"
        assert result["chars_processed"] == 5
        mock_consolidate.assert_called_once()


@pytest.mark.asyncio
async def test_enqueue_consolidation_job():
    """Test enqueueing a consolidation job."""
    
    with patch("app.memory_service.task_queue.memory_job_queue.enqueue", new_callable=AsyncMock) as mock_enqueue:
        mock_job = MagicMock()
        mock_job.id = 1
        mock_enqueue.return_value = mock_job
        
        job = await memory_service.enqueue_consolidation_job(chat_id=42)
        
        assert job.id == 1
        mock_enqueue.assert_called_once()
        call_args = mock_enqueue.call_args
        assert call_args.kwargs["job_type"] == "consolidation"
        assert call_args.kwargs["chat_id"] == 42


@pytest.mark.asyncio
async def test_consolidation_updates_last_accessed(db_session: AsyncSession, mock_client):
    """Test that consolidation updates last_accessed_at on merged memories."""
    from tests.conftest import create_characters
    
    chat = await crud.create_chat(db_session, schemas.ChatCreate(name="Test"))
    characters = await create_characters(db_session, chat.id, 1)
    char = characters[0]
    
    # Create memories using crud to avoid hash collision
    m1 = await crud.create_memory(db_session, schemas.MemoryCreate(
        chat_id=chat.id, character_id=char.id,
        content="Алиса любит котиков", importance=0.8, category="отношения",
    ))
    m2 = await crud.create_memory(db_session, schemas.MemoryCreate(
        chat_id=chat.id, character_id=char.id,
        content="Алиса любит котиков очень сильно", importance=0.7, category="отношения",
    ))
    
    # Update last_accessed_at to old date
    old_date = datetime.utcnow() - timedelta(days=30)
    m1.last_accessed_at = old_date
    m2.last_accessed_at = old_date
    await db_session.commit()
    await db_session.refresh(m1)
    await db_session.refresh(m2)
    
    # Mock LLM merge
    async def mock_post(*args, **kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"response": "Алиса очень любит котиков"})
        return resp
    
    with patch.object(mock_client, "post", side_effect=mock_post):
        merged, deleted = await memory_service._consolidate_character_memories(
            db_session, mock_client, "test-model", char.id, char.name,
            0.55, 2, 200
        )
    
    # Check that primary memory's last_accessed_at was updated
    await db_session.refresh(m1)
    assert m1.last_accessed_at > old_date
    assert "котиков" in m1.content.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])