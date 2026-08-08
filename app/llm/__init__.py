"""Пакет LLM-интерфейсов (Sprint 1, §7.2 decomposition.md).

Заглушка-фасад: пока проксирует внутренности ``ollama_client``, чтобы
потребители (relationship_analyzer, sensors_service) не пересекали границу
модулей приватными символами. Полная резка ``llm/*`` — спринт 5A.
"""

from .generation import extract_json_payload, invoke_json

__all__ = ["invoke_json", "extract_json_payload"]
