"""Client for Ollama API (local LLM) with memory extraction and retry logic."""

import json
import logging
import re
import time

import httpx

logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = "http://localhost:11434"
GENERATE_TIMEOUT = 120.0  # seconds
DEFAULT_TEMPERATURE = 0.8
MEMORY_EXTRACTION_TEMP = 0.3  # low temperature for deterministic fact extraction

MAX_MEMORIES_PER_CHARACTER = 20
RECENT_MEMORIES_FOR_PROMPT = 10

MAX_RETRIES = 3
RETRY_DELAY = 1.0  # seconds between retries


def _build_system_prompt(character, general_prompt: str) -> str:
    """Build the system prompt from character card and chat story."""
    parts = [f"Ты — {character.name}."]
    if character.personality:
        parts.append(character.personality)
    if character.traits:
        parts.append(character.traits)
    if general_prompt:
        parts.append(f"Сюжет: {general_prompt}")
    parts.append("Отвечай от первого лица, естественно, в рамках характера.")
    return " ".join(parts)


def _format_memories(memories) -> str:
    """Format memory block to inject into the prompt."""
    if not memories:
        return ""
    mem_lines = "\n".join(f"- {m.content}" for m in memories)
    return f"Важная информация, которую ты знаешь:\n{mem_lines}"


def _format_history(messages: list, max_len: int) -> str:
    """Convert message list to text history (last max_len messages)."""
    recent = messages[-max_len:] if len(messages) > max_len else messages
    lines = []
    for m in recent:
        if m.role == "user":
            lines.append(f"Игрок: {m.content}")
        elif m.role == "character":
            name = m.character.name if m.character else "Персонаж"
            lines.append(f"{name}: {m.content}")
        elif m.role == "system":
            lines.append(f"Система: {m.content}")
    return "\n".join(lines)


def _call_ollama(
    model_name: str, prompt: str, temperature: float = DEFAULT_TEMPERATURE
) -> str:
    """Low-level call to Ollama /api/generate with retry logic.

    Retries up to MAX_RETRIES times on transient errors (timeout, connection).
    Raises RuntimeError after all retries are exhausted.
    """
    payload = {
        "model": model_name,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature},
    }

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with httpx.Client(timeout=GENERATE_TIMEOUT) as client:
                response = client.post(f"{OLLAMA_BASE_URL}/api/generate", json=payload)
                response.raise_for_status()
                data = response.json()
                return data.get("response", "")
        except httpx.TimeoutException:
            last_error = RuntimeError(
                f"Ollama не отвечает (таймаут {GENERATE_TIMEOUT} сек)"
            )
            logger.warning("Ollama timeout (attempt %d/%d)", attempt, MAX_RETRIES)
        except httpx.HTTPStatusError as exc:
            # HTTP errors are not retryable
            raise RuntimeError(f"Ollama вернула ошибку: {exc.response.text}")
        except httpx.RequestError as exc:
            last_error = RuntimeError(
                f"Ollama недоступна. Убедитесь, что сервер запущен на {OLLAMA_BASE_URL}"
            )
            logger.warning(
                "Ollama connection error (attempt %d/%d): %s",
                attempt, MAX_RETRIES, exc,
            )

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY)

    raise last_error or RuntimeError("Ollama недоступна после всех попыток")


def generate(
    chat_id: int,
    character,
    messages_history: list,
    general_prompt: str,
    memories: list,
    max_history_length: int = 30,
) -> str:
    """Send a request to Ollama and return the response text.

    Returns:
        Generated text string.

    Raises:
        RuntimeError: If Ollama is unreachable or returns an error.
    """
    model_name = (
        character.chat.model_name
        if hasattr(character, "chat") and character.chat
        else "default"
    )

    system_prompt = _build_system_prompt(character, general_prompt)
    history_text = _format_history(messages_history, max_history_length)
    mem_text = _format_memories(memories)

    context_parts = [system_prompt]
    if mem_text:
        context_parts.append(mem_text)
    if history_text:
        context_parts.append(history_text)

    full_prompt = "\n\n".join(context_parts)

    logger.info(
        "[chat_id=%d] Ollama request (model=%s, prompt_len=%d, history=%d msgs, memories=%d items)",
        chat_id, model_name, len(full_prompt), len(messages_history), len(memories),
    )

    generated = _call_ollama(model_name, full_prompt)
    logger.info(
        "[chat_id=%d] Ollama response received (len=%d)", chat_id, len(generated)
    )
    return generated


def extract_memories_text(
    character_name: str,
    character_personality: str,
    character_traits: str,
    model_name: str,
    round_history_text: str,
) -> list[str]:
    """Ask Ollama to extract 1-3 important facts from a round for this character.

    Returns a list of fact strings. Returns empty list on parse errors.
    """
    prompt = (
        f"Проанализируй следующий диалог от лица персонажа {character_name} "
        f"({character_personality}). "
        f"Извлеки 1-3 важных факта, которые {character_name} должен запомнить "
        f"для будущих разговоров (предпочтения других, факты о мире, "
        f"договорённости, эмоции). "
        f"Если ничего важного — верни пустой список.\n"
        f"Формат: JSON-массив строк. "
        f'Пример: ["Игрок боится темноты", "Виктор предложил встретиться в таверне"]\n\n'
        f"Диалог:\n{round_history_text}"
    )

    try:
        raw = _call_ollama(model_name, prompt, temperature=MEMORY_EXTRACTION_TEMP)
    except RuntimeError:
        logger.warning("Memory extraction failed — Ollama unavailable")
        return []

    return _parse_json_array(raw)


def _parse_json_array(raw: str) -> list[str]:
    """Try to parse a JSON array from the raw string, handling markdown fences."""
    if not raw or not raw.strip():
        return []

    text = raw.strip()

    code_block_match = re.search(
        r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL
    )
    if code_block_match:
        text = code_block_match.group(1).strip()

    try:
        result = json.loads(text)
        if isinstance(result, list):
            return [str(item) for item in result if item]
        return []
    except json.JSONDecodeError:
        pass

    list_match = re.search(r"\[.*?\]", text, re.DOTALL)
    if list_match:
        try:
            result = json.loads(list_match.group(0))
            if isinstance(result, list):
                return [str(item) for item in result if item]
        except json.JSONDecodeError:
            pass

    lines = re.findall(r'"([^"]+)"', text)
    if lines:
        return lines

    return []