"""Embedding service for vector search (P3)."""

import logging
import struct
from typing import Optional

import httpx

from .config import settings
from .ollama_client import llm_request

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Service for generating and managing embeddings via Ollama."""

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        self._model = settings.embedding_model
        self._dim = settings.embedding_dim
        self._base_url = settings.ollama_base_url
        self._timeout = settings.ollama_timeout

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
            )
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def embed_single(self, text: str) -> Optional[list[float]]:
        """Generate embedding for a single text."""
        result = await self.embed_batch([text])
        return result[0] if result else None

    async def embed_batch(self, texts: list[str]) -> list[Optional[list[float]]]:
        """Generate embeddings for multiple texts."""
        if not texts:
            return []

        results: list[Optional[list[float]]] = [None] * len(texts)
        batch_size = settings.embedding_batch_size

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            try:
                async with llm_request(self._model, "/api/embed"):
                    response = await self.client.post(
                        "/api/embed",
                        json={"model": self._model, "input": batch},
                    )
                response.raise_for_status()
                data = response.json()
                embeddings = data.get("embeddings", [])
                for j, emb in enumerate(embeddings):
                    if emb and len(emb) == self._dim:
                        results[i + j] = emb
                    else:
                        logger.warning(
                            "Invalid embedding at index %d: len=%s", i + j, len(emb) if emb else None
                        )
            except httpx.TimeoutException:
                logger.error("Embedding timeout for batch %d-%d", i, i + batch_size)
            except httpx.HTTPStatusError as exc:
                logger.error("Embedding HTTP error: %s", exc.response.text)
            except Exception as exc:
                logger.exception("Embedding error: %s", exc)

        await self.unload_model()
        return results

    async def unload_model(self) -> None:
        """Unload the embedding model from Ollama memory (keep_alive=0)."""
        try:
            async with llm_request(self._model, "/api/generate"):
                await self.client.post(
                    "/api/generate",
                    json={"model": self._model, "keep_alive": 0},
                )
        except Exception as exc:
            logger.warning("Failed to unload embedding model: %s", exc)

    @staticmethod
    def pack_embedding(embedding: list[float]) -> bytes:
        """Pack float32 array to bytes for SQLite BLOB storage."""
        return struct.pack(f"{len(embedding)}f", *embedding)

    @staticmethod
    def unpack_embedding(blob: bytes) -> list[float]:
        """Unpack bytes to float32 array."""
        count = len(blob) // 4
        return list(struct.unpack(f"{count}f", blob))

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        if len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(y * y for y in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)


_embedding_service: Optional[EmbeddingService] = None


def get_embedding_service() -> EmbeddingService:
    """Get singleton embedding service instance."""
    global _embedding_service
    if _embedding_service is None:
        _embedding_service = EmbeddingService()
    return _embedding_service


async def close_embedding_service():
    """Close the embedding service (for shutdown)."""
    global _embedding_service
    if _embedding_service:
        await _embedding_service.close()
        _embedding_service = None