"""app.pipeline — Milestone 5B (decomposition.md §4.2, decomposition-sprints.md §6).

Streaming-ядро ``app/chat_engine.py``, вынесенное в подпакет без изменения
поведения. Фасад ``app/chat_engine.py`` реэкспортирует публичный API и
сохраняет контракт патчей тестов (``app.chat_engine.{asyncio, ollama_client,
settings, AsyncSessionLocal, ...}``).

Публичный API (Gate 5B):
- ``streaming.process_user_message_streaming`` — основной SSE-пайплайн раунда;
- ``session.process_user_message`` — non-streaming точка входа;
- ``regeneration.regenerate_message_streaming`` — регенерация ответа персонажа;
- ``lora.resolve_generation_model`` / ``lora.lora_first_apply_warning`` —
  выбор модели для ОСНОВНОЙ генерации (Plans/LoRA.md, Sprint 3);
- ``story._chat_story_block`` / ``story._chat_plot_text`` — story-блок (Sprint 8);
- ``story._compute_epistemic_evidence`` / ``story._belief_evidenced_ids`` —
  эпистемический evidence/belief (docs/relations.md §10, Sprint 5 §9).

Milestone 6A (decomposition-sprints.md §6A): ``relations.py`` — анализ отношений
после раунда (``_analyze_and_update_relationships``, sensors-hook, per-pair
fallback, evidence/constrain, hearsay caps). Анализ отношений больше не живёт
в фасаде ``chat_engine`` и не входит в streaming-путь; streaming только
планирует его через ``post_round_pipeline``.

Зависимости: ``pipeline/*`` импортируют только публичные API модулей
(``crud``, ``schemas``, ``perception`` и т.д.).
"""

from . import lora
from . import regeneration
from . import relations
from . import session
from . import story
from . import streaming

__all__ = ["lora", "regeneration", "relations", "session", "story", "streaming"]
