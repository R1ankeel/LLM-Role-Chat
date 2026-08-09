"""Фасад LLM-интерфейсов (Sprint 5A, decomposition.md §7.2).

Вся логика перенесена 1:1 в ``app/llm/*``; здесь — тонкая обёртка для обратной
совместимости: ``from . import ollama_client`` и ``from .ollama_client import X``
продолжают работать без изменений.

Оригинальный (до разрезания) файл: ``Plans/artifacts/pre-split/ollama_client.py``.
"""

from .llm import *  # noqa: F401,F403

# Публичный JSON-фасад, используемый relationship_analyzer / sensors_service.
from .llm import invoke_json, extract_json_payload  # noqa: F401
