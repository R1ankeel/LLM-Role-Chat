"""Tests for embedding service and hybrid retrieval (P3 Vector Search)."""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app import crud
from app import embedding_service
from app import memory_service
from app import schemas
from app.config import settings
from tests.conftest import create_characters


class TestEmbeddingService:
    """Tests for EmbeddingService."""

    @pytest.fixture
    def mock_client(self):
        """Mock httpx.AsyncClient."""
        client = AsyncMock()
        return client

    @pytest.mark.asyncio
    async def test_pack_unpack_embedding(self):
        """Test embedding packing and unpacking round-trip."""
        emb_svc = embedding_service.EmbeddingService()
        
        # Test with standard dimension
        original = [0.1] * settings.embedding_dim
        packed = emb_svc.pack_embedding(original)
        unpacked = emb_svc.unpack_embedding(packed)
        
        assert len(unpacked) == settings.embedding_dim
        assert all(abs(a - b) < 1e-6 for a, b in zip(original, unpacked))

    @pytest.mark.asyncio
    async def test_cosine_similarity(self):
        """Test cosine similarity calculation."""
        emb_svc = embedding_service.EmbeddingService()
        
        # Identical vectors
        a = [1.0, 0.0, 0.0]
        b = [1.0, 0.0, 0.0]
        assert abs(emb_svc.cosine_similarity(a, b) - 1.0) < 1e-6
        
        # Orthogonal vectors
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert abs(emb_svc.cosine_similarity(a, b) - 0.0) < 1e-6
        
        # Opposite vectors
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert abs(emb_svc.cosine_similarity(a, b) - (-1.0)) < 1e-6
        
        # Different lengths
        a = [1.0, 0.0]
        b = [1.0]
        assert emb_svc.cosine_similarity(a, b) == 0.0

    @pytest.mark.asyncio
    async def test_embed_single(self, mock_client):
        """Test single text embedding."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"embeddings": [[0.1] * settings.embedding_dim]}
        mock_response.raise_for_status.return_value = None
        mock_client.post.return_value = mock_response
        
        emb_svc = embedding_service.EmbeddingService()
        emb_svc._client = mock_client
        
        result = await emb_svc.embed_single("test text")
        
        assert result is not None
        assert len(result) == settings.embedding_dim

    @pytest.mark.asyncio
    async def test_embed_batch(self, mock_client):
        """Test batch embedding."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "embeddings": [[0.1] * settings.embedding_dim, [0.2] * settings.embedding_dim]
        }
        mock_response.raise_for_status.return_value = None
        mock_client.post.return_value = mock_response
        
        emb_svc = embedding_service.EmbeddingService()
        emb_svc._client = mock_client
        
        results = await emb_svc.embed_batch(["text 1", "text 2"])
        
        assert len(results) == 2
        assert all(len(r) == settings.embedding_dim for r in results if r)

    @pytest.mark.asyncio
    async def test_embed_batch_empty(self):
        """Test embedding empty list."""
        emb_svc = embedding_service.EmbeddingService()
        results = await emb_svc.embed_batch([])
        assert results == []


class TestHybridRetrieval:
    """Tests for hybrid BM25 + Vector retrieval with RRF fusion."""

    @pytest_asyncio.fixture
    async def setup_memories(self, db_session, three_characters):
        """Create test memories with embeddings."""
        char = three_characters[0]
        emb_svc = embedding_service.EmbeddingService()
        
        # Create memories with known content and embeddings
        memories = []
        test_contents = [
            "Алиса любит читать книги в библиотеке",
            "Боб играет на гитаре каждый вечер",
            "Чарли путешествует по миру и фотографирует закаты",
            "Алиса и Боб друзья с детства",
            "Боб учит Чарли играть на гитаре",
        ]
        
        for i, content in enumerate(test_contents):
            emb = [0.1 * (i + 1)] * settings.embedding_dim  # Distinct embeddings
            packed = emb_svc.pack_embedding(emb)
            
            mem = await crud.create_memory(
                db_session,
                schemas.MemoryCreate(
                    chat_id=char.chat_id,
                    character_id=char.id,
                    content=content,
                    importance=0.5 + i * 0.1,
                    category="event",
                ),
            )
            # Manually set embedding
            mem.embedding = packed
            await db_session.commit()
            await db_session.refresh(mem)
            memories.append(mem)
        
        return char, memories

    @pytest.mark.asyncio
    async def test_hybrid_retrieval_bm25_only(self, db_session, three_characters, setup_memories):
        """Test hybrid retrieval falls back to BM25 when embeddings disabled."""
        char, memories = setup_memories
        
        with patch.object(settings, 'embedding_enabled', False):
            results = await crud.get_hybrid_memories_for_characters(
                db_session,
                [char.id],
                "Алиса читает книги",
                top_k=3,
            )
        
        assert char.id in results
        assert len(results[char.id]) <= 3
        # Should find "Алиса любит читать книги в библиотеке" via BM25

    @pytest.mark.asyncio
    async def test_hybrid_retrieval_with_vectors(self, db_session, three_characters, setup_memories):
        """Test hybrid retrieval with vector similarity."""
        char, memories = setup_memories
        
        # Mock embedding service to return query embedding
        mock_emb_svc = MagicMock()
        mock_emb_svc.embed_single = AsyncMock(return_value=[0.1] * settings.embedding_dim)
        mock_emb_svc.unpack_embedding = embedding_service.EmbeddingService.unpack_embedding
        mock_emb_svc.cosine_similarity = embedding_service.EmbeddingService.cosine_similarity
        
        with patch('app.embedding_service.get_embedding_service', return_value=mock_emb_svc):
            with patch.object(settings, 'embedding_enabled', True):
                results = await crud.get_hybrid_memories_for_characters(
                    db_session,
                    [char.id],
                    "Алиса читает книги",
                    top_k=3,
                )
        
        assert char.id in results
        # Should return memories ranked by RRF fusion

    @pytest.mark.asyncio
    async def test_rrf_fusion(self, db_session, three_characters, setup_memories):
        """Test RRF fusion combines BM25 and vector ranks."""
        char, memories = setup_memories
        
        # Query embedding similar to first memory
        query_emb = [0.1] * settings.embedding_dim
        
        mock_emb_svc = MagicMock()
        mock_emb_svc.embed_single = AsyncMock(return_value=query_emb)
        mock_emb_svc.unpack_embedding = embedding_service.EmbeddingService.unpack_embedding
        mock_emb_svc.cosine_similarity = embedding_service.EmbeddingService.cosine_similarity
        
        with patch('app.embedding_service.get_embedding_service', return_value=mock_emb_svc):
            with patch.object(settings, 'embedding_enabled', True):
                with patch.object(settings, 'hybrid_bm25_weight', 1.0):
                    with patch.object(settings, 'hybrid_vector_weight', 1.0):
                        with patch.object(settings, 'hybrid_rrf_k', 60):
                            results = await crud.get_hybrid_memories_for_characters(
                                db_session,
                                [char.id],
                                "Алиса библиотека книги",
                                top_k=5,
                            )
        
        assert char.id in results
        selected = results[char.id]
        assert len(selected) > 0
        
        # First memory should rank high in both BM25 (Алиса, книги) and vector (embedding 0.1)
        top_content = selected[0].content
        assert "Алиса" in top_content


class TestEmbeddingJobs:
    """Tests for embedding background jobs."""

    @pytest_asyncio.fixture
    async def setup_memory(self, db_session, three_characters):
        """Create a memory without embedding."""
        char = three_characters[0]
        mem = await crud.create_memory(
            db_session,
            schemas.MemoryCreate(
                chat_id=char.chat_id,
                character_id=char.id,
                content="Test memory for embedding",
                importance=0.7,
                category="event",
            ),
        )
        return char, mem

    @pytest.mark.asyncio
    async def test_embed_memory_job(self, db_session, setup_memory):
        """Test embedding memory job handler."""
        char, mem = setup_memory
        
        # Mock embedding service
        mock_emb_svc = MagicMock()
        mock_emb_svc.embed_single = AsyncMock(return_value=[0.5] * settings.embedding_dim)
        mock_emb_svc.pack_embedding = embedding_service.EmbeddingService.pack_embedding
        
        with patch('app.embedding_service.get_embedding_service', return_value=mock_emb_svc):
            with patch.object(settings, 'embedding_enabled', True):
                result = await memory_service._process_embed_memory_job({
                    "memory_id": mem.id,
                    "content": mem.content,
                })
        
        assert result["status"] == "completed"
        assert result["memory_id"] == mem.id
        
        # Verify embedding was stored
        await db_session.refresh(mem)
        assert mem.embedding is not None

    @pytest.mark.asyncio
    async def test_embed_memory_job_disabled(self, setup_memory):
        """Test embedding job when disabled."""
        char, mem = setup_memory
        
        with patch.object(settings, 'embedding_enabled', False):
            result = await memory_service._process_embed_memory_job({
                "memory_id": mem.id,
                "content": mem.content,
            })
        
        assert result["status"] == "disabled"


# Import crud and schemas for test setup
from app import crud
from app import schemas
from app import memory_service
