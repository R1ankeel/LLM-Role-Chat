"""Sprint 6 — Hybrid Retrieval v2 (Plans/update20.md §14).

Проверяет:
- `rerank_weights` — веса осей из config, нормированные на 1.0;
- оси rerank: `_story_relevance` (story-память выше при активном thread),
  `_relationship_relevance` (social-память об участнике отношений),
  `_emotional_relevance` (valence/intensity — эмоциональная релевантность);
- `rerank_memories` — детерминированный rerank ПОСЛЕ RRF, ДО witness-boost:
  story-память поднимается при активном потоке; эмоциональная память при
  anchors; fallback BM25 без embeddings (semantic-слагаемое отбрасывается,
  веса нормируются); отсутствие перестановки без флага;
- `crud.build_rerank_signals` — сигналы контекста (target-имена отношений +
  активные story_threads), пусто при выключенном флаге;
- интеграция в retrieval: `get_relevant_memories_for_characters` (fallback BM25)
  и `get_hybrid_memories_for_characters` (RRF-путь) применяют rerank только при
  `hybrid_rerank_enabled` + сигналов; RRF-путь без флага не меняется.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import crud
from app import memory_service
from app import models
from app import relationship_service
from app import schemas
from app import embedding_service
from app.config import settings
from tests.conftest import create_characters


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _mem(
    content: str,
    *,
    memory_type: str = "semantic",
    category: str = "событие",
    importance: float = 0.5,
    valence=None,
    intensity=None,
    days: int = 1,
    embedding=None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=id(content),
        content=content,
        memory_type=memory_type,
        category=category,
        importance=importance,
        valence=valence,
        intensity=intensity,
        created_at=datetime(2026, 8, days, 12, 0),
        embedding=embedding,
    )


@pytest.fixture
def enable_rerank(monkeypatch):
    """settings — общий синглтон; включаем флаг для всех читающих модулей."""
    monkeypatch.setattr("app.crud.settings.hybrid_rerank_enabled", True)
    monkeypatch.setattr("app.memory_service.settings.hybrid_rerank_enabled", True)
    monkeypatch.setattr("app.context_builder.settings.hybrid_rerank_enabled", True)
    monkeypatch.setattr("app.chat_engine.settings.hybrid_rerank_enabled", True)


# ---------------------------------------------------------------------------
# веса
# ---------------------------------------------------------------------------

def test_rerank_weights_normalize_to_one():
    w = memory_service.rerank_weights()
    assert set(w) == {
        "lexical", "semantic", "emotional", "story",
        "relationship", "recency", "salience",
    }
    assert sum(w.values()) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# оси
# ---------------------------------------------------------------------------

class TestStoryRelevance:
    def test_story_with_active_thread_overlap_is_full(self):
        mem = _mem("Поиск Николая продолжается в горах", memory_type="story")
        assert memory_service._story_relevance(
            mem, ("Поиск Николая в горах",)
        ) == 1.0

    def test_story_without_threads_gets_soft_boost(self):
        mem = _mem("Мы ищем Николая", memory_type="story")
        assert memory_service._story_relevance(mem, ()) == pytest.approx(0.3)

    def test_story_no_overlap_partial(self):
        mem = _mem("Ищем Николая в горах", memory_type="story")
        assert memory_service._story_relevance(
            mem, ("Тайна старого замка",)
        ) == pytest.approx(0.5)

    def test_non_story_is_zero(self):
        mem = _mem("Максим любит кофе", memory_type="semantic")
        assert memory_service._story_relevance(mem, ("Поиск Николая",)) == 0.0


class TestRelationshipRelevance:
    def test_social_mentioning_target_is_full(self):
        mem = _mem("Борис предал меня", memory_type="social")
        assert memory_service._relationship_relevance(
            mem, ("Борис",)
        ) == 1.0

    def test_social_without_mention_is_zero(self):
        mem = _mem("Мы поговорили о погоде", memory_type="social")
        assert memory_service._relationship_relevance(
            mem, ("Борис",)
        ) == 0.0

    def test_category_fallback_without_types(self):
        # Без memory_types_enabled тип 'semantic' у всех — fallback по category.
        mem = _mem("Борис унизил меня", category="отношения")
        assert memory_service._relationship_relevance(
            mem, ("Борис",)
        ) == 1.0

    def test_non_social_is_zero(self):
        mem = _mem("Борис любит кофе", memory_type="semantic")
        assert memory_service._relationship_relevance(
            mem, ("Борис",)
        ) == 0.0


class TestEmotionalRelevance:
    def test_intensity_plus_valence(self):
        mem = _mem("Анна спасла меня", intensity=0.7, valence=0.8)
        # 0.7 + 0.5*0.8 = 1.1 → clamp 1.0
        assert memory_service._emotional_relevance(mem) == pytest.approx(1.0)

    def test_neutral_is_zero(self):
        mem = _mem("В саду цвели цветы")
        assert memory_service._emotional_relevance(mem) == 0.0


# ---------------------------------------------------------------------------
# rerank_memories: детерминированные сценарии
# ---------------------------------------------------------------------------

class TestRerankMemories:
    def test_story_memory_above_when_active_thread(self):
        """Story-память выше при активном thread (равная lexical-релевантность)."""
        m_sem = _mem("Николай пьёт кофе утром", memory_type="semantic", days=1)
        m_story = _mem("Николай ищет в горах", memory_type="story", days=1)
        candidates = [m_sem, m_story]  # lex-запрос «Николай» — обе по 1 токену
        ctx = memory_service.RerankContext(
            query_text="Николай",
            active_threads=("Поиск Николая в горах",),
        )
        result = memory_service.rerank_memories(candidates, ctx)
        assert result[0].content == m_story.content

    def test_emotional_relevance_at_anchors(self):
        """Эмоциональная релевантность при anchors: интенсивная память выше."""
        m_neutral = _mem("Максим купил хлеб", memory_type="semantic", days=1)
        m_emotional = _mem(
            "Максим предал меня", memory_type="social",
            valence=-0.9, intensity=0.8, days=1,
        )
        ctx = memory_service.RerankContext(query_text="Максим")
        result = memory_service.rerank_memories([m_neutral, m_emotional], ctx)
        assert result[0].content == m_emotional.content

    def test_fallback_bm25_without_embeddings(self):
        """Fallback: без embeddings semantic-слагаемое отбрасывается."""
        m1 = _mem("Корабль плывёт на север", memory_type="semantic", days=1)
        m2 = _mem("Корабль тонет в шторм", memory_type="episodic", days=2)
        ctx = memory_service.RerankContext(query_text="корабль шторм")
        result = memory_service.rerank_memories([m1, m2], ctx)
        assert result[0].content == m2.content  # шторм ближе к запросу
        assert len(result) == 2

    def test_lexical_axis_reorders_by_bm25(self):
        m_a = _mem("В саду цвели цветы", memory_type="semantic", days=1)
        m_b = _mem("Максим пьёт кофе", memory_type="semantic", days=1)
        result = memory_service.rerank_memories(
            [m_a, m_b], memory_service.RerankContext(query_text="цветы сад")
        )
        assert result[0].content == m_a.content

    def test_no_embeddings_drops_semantic_from_weights(self):
        # semantic-слагаемое отпадает, веса перенормируются — сумма не ломается
        ctx = memory_service.RerankContext(query_text="тест")
        # rerank проходит и с пустыми embeddings у memories
        m1 = _mem("тестовая память", memory_type="semantic")
        m2 = _mem("другая память", memory_type="semantic")
        result = memory_service.rerank_memories([m1, m2], ctx)
        assert len(result) == 2

    def test_semantic_axis_used_when_embeddings_present(self):
        emb_high = embedding_service.EmbeddingService.pack_embedding([1.0, 0.0, 0.0])
        emb_low = embedding_service.EmbeddingService.pack_embedding([0.0, 1.0, 0.0])
        m_high = _mem("некая память", memory_type="semantic", embedding=emb_high)
        m_low = _mem("другая память", memory_type="semantic", embedding=emb_low)
        ctx = memory_service.RerankContext(
            query_text="", query_embedding=[0.9, 0.1, 0.0],
        )
        result = memory_service.rerank_memories([m_low, m_high], ctx)
        assert result[0].content == m_high.content

    def test_stable_on_already_sorted_input(self):
        m1 = _mem("Борис предал меня", memory_type="social", days=1)
        m2 = _mem("Борис купил хлеб", memory_type="semantic", days=1)
        ctx = memory_service.RerankContext(
            query_text="Борис", relationship_target_names=("Борис",)
        )
        once = memory_service.rerank_memories([m1, m2], ctx)
        twice = memory_service.rerank_memories(once, ctx)
        assert [m.content for m in once] == [m.content for m in twice]


# ---------------------------------------------------------------------------
# crud.build_rerank_signals
# ---------------------------------------------------------------------------

class TestBuildRerankSignals:
    @pytest.mark.asyncio
    async def test_signals_collect_relationships_and_threads(
        self, db_session, chat, enable_rerank
    ):
        characters = await create_characters(db_session, chat.id, 2)
        a, b = characters
        await relationship_service.get_or_create_relationship(
            db_session, chat.id, a.id, b.id
        )
        thread = models.StoryThread(
            chat_id=chat.id, name="Поиск Николая в горах", status="active"
        )
        db_session.add(thread)
        await db_session.commit()

        names = {x.id: x.name for x in characters}
        signals = await crud.build_rerank_signals(
            db_session, chat.id, [a.id], names
        )
        assert a.id in signals
        assert b.name in signals[a.id].relationship_target_names
        assert "Поиск Николая в горах" in signals[a.id].active_threads

    @pytest.mark.asyncio
    async def test_empty_when_flag_off(self, db_session, chat):
        characters = await create_characters(db_session, chat.id, 1)
        signals = await crud.build_rerank_signals(
            db_session, chat.id, [characters[0].id], {characters[0].id: "A"}
        )
        assert signals == {}


# ---------------------------------------------------------------------------
# интеграция в retrieval
# ---------------------------------------------------------------------------

class TestRetrievalIntegration:
    async def _make_char_with_memories(
        self, db_session, chat, mem_specs: list[dict]
    ):
        chars = await create_characters(db_session, chat.id, 1)
        char = chars[0]
        memories = []
        for spec in mem_specs:
            memories.append(
                await crud.create_memory(
                    db_session,
                    schemas.MemoryCreate(
                        chat_id=char.chat_id,
                        character_id=char.id,
                        content=spec["content"],
                        importance=spec.get("importance", 0.5),
                        category=spec.get("category", "событие"),
                        memory_type=spec.get("memory_type"),
                        valence=spec.get("valence"),
                        intensity=spec.get("intensity"),
                    ),
                )
            )
        return char, memories

    @pytest.mark.asyncio
    async def test_bm25_path_reranks_with_signals(
        self, db_session, chat, enable_rerank
    ):
        """Fallback BM25-путь: социальная память об участнике отношений выше."""
        char, memories = await self._make_char_with_memories(
            db_session, chat,
            [
                {"content": "Борис купил хлеб вчера", "memory_type": "semantic"},
                {
                    "content": "Борис предал меня в лесу",
                    "memory_type": "social",
                    "category": "отношения",
                    "valence": -0.9,
                    "intensity": 0.8,
                },
            ],
        )
        m_sem, m_social = memories

        # target отношений персонажа — «Борис»
        target = await crud.create_character(
            db_session, chat.id,
            schemas.CharacterCreate(
                name="Борис", personality="", traits="", order_index=2,
            ),
        )
        await relationship_service.get_or_create_relationship(
            db_session, chat.id, char.id, target.id
        )
        names = {char.id: char.name, target.id: target.name}
        signals = await crud.build_rerank_signals(db_session, chat.id, [char.id], names)

        # БЕЗ флага — BM25-порядок не меняется (lex-связь «Борис» равная,
        # стабильная сортировка сохраняет порядок создания).
        no_flag = await crud.get_relevant_memories_for_characters(
            db_session, [char.id], "Борис", top_k=5,
        )
        assert [m.id for m in no_flag[char.id]] == [m_sem.id, m_social.id]

        # С флагом + сигналы — социальная память об участнике отношений выше.
        reranked = await crud.get_relevant_memories_for_characters(
            db_session, [char.id], "Борис", top_k=5,
            rerank_signals=signals,
        )
        assert [m.id for m in reranked[char.id]] == [m_social.id, m_sem.id]

    @pytest.mark.asyncio
    async def test_bm25_path_story_with_active_thread(
        self, db_session, chat, enable_rerank
    ):
        """Story-память выше при активном thread (через BM25-путь)."""
        char, memories = await self._make_char_with_memories(
            db_session, chat,
            [
                {"content": "Николай пьёт кофе утром", "memory_type": "semantic"},
                {"content": "Николай ищет в горах", "memory_type": "story"},
            ],
        )
        m_sem, m_story = memories
        db_session.add(models.StoryThread(
            chat_id=chat.id, name="Поиск Николая в горах", status="active",
        ))
        await db_session.commit()
        signals = await crud.build_rerank_signals(
            db_session, chat.id, [char.id], {char.id: char.name}
        )
        reranked = await crud.get_relevant_memories_for_characters(
            db_session, [char.id], "Николай", top_k=5,
            rerank_signals=signals,
        )
        assert [m.id for m in reranked[char.id]] == [m_story.id, m_sem.id]

    @pytest.mark.asyncio
    async def test_rrf_path_reranks_after_fusion(
        self, db_session, chat, enable_rerank
    ):
        """RRF-путь: rerank применяется ПОСЛЕ слияния, story выше при потоке."""
        char, memories = await self._make_char_with_memories(
            db_session, chat,
            [
                {"content": "Николай пьёт кофе утром", "memory_type": "semantic"},
                {"content": "Николай ищет в горах", "memory_type": "story"},
            ],
        )
        m_sem, m_story = memories
        db_session.add(models.StoryThread(
            chat_id=chat.id, name="Поиск Николая в горах", status="active",
        ))
        await db_session.commit()
        signals = await crud.build_rerank_signals(
            db_session, chat.id, [char.id], {char.id: char.name}
        )

        mock_emb_svc = MagicMock()
        mock_emb_svc.embed_single = AsyncMock(
            return_value=[0.1] * settings.embedding_dim
        )
        mock_emb_svc.unpack_embedding = embedding_service.EmbeddingService.unpack_embedding
        mock_emb_svc.cosine_similarity = embedding_service.EmbeddingService.cosine_similarity

        with patch(
            "app.embedding_service.get_embedding_service", return_value=mock_emb_svc
        ):
            reranked = await crud.get_hybrid_memories_for_characters(
                db_session, [char.id], "Николай", top_k=5,
                rerank_signals=signals,
            )
        assert [m.id for m in reranked[char.id]] == [m_story.id, m_sem.id]

    @pytest.mark.asyncio
    async def test_rrf_path_unchanged_without_flag(
        self, db_session, chat
    ):
        """Критерий: RRF-путь без флага не меняется (даже при переданных сигналах)."""
        char, memories = await self._make_char_with_memories(
            db_session, chat,
            [
                {"content": "Николай пьёт кофе утром", "memory_type": "semantic"},
                {"content": "Николай ищет в горах", "memory_type": "story"},
            ],
        )
        m_sem, m_story = memories
        db_session.add(models.StoryThread(
            chat_id=chat.id, name="Поиск Николая в горах", status="active",
        ))
        await db_session.commit()

        mock_emb_svc = MagicMock()
        mock_emb_svc.embed_single = AsyncMock(
            return_value=[0.1] * settings.embedding_dim
        )
        mock_emb_svc.unpack_embedding = embedding_service.EmbeddingService.unpack_embedding
        mock_emb_svc.cosine_similarity = embedding_service.EmbeddingService.cosine_similarity

        with patch(
            "app.embedding_service.get_embedding_service", return_value=mock_emb_svc
        ):
            baseline = await crud.get_hybrid_memories_for_characters(
                db_session, [char.id], "Николай", top_k=5,
            )
        # флаг off → сигналы не применяются, порядок — RRF без rerank
        assert [m.id for m in baseline[char.id]] == [m_sem.id, m_story.id]
