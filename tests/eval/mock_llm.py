"""Mock LLM for deterministic evaluation testing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from collections import defaultdict


@dataclass
class MockResponse:
    """A canned response for a specific scenario/character/turn."""
    text: str
    thinking: str = ""
    tokens: list[str] = field(default_factory=list)

    def to_events(self) -> list[dict[str, Any]]:
        """Convert to streaming event format matching ollama_client.generate."""
        events = []
        for token in self.tokens or [self.text]:
            events.append({"type": "token", "text": token})
        events.append({
            "type": "response",
            "text": self.text,
            "thinking_len": len(self.thinking),
        })
        return events


class MockLLM:
    """Deterministic mock LLM for evaluation harness."""

    def __init__(self):
        # Key: (scenario_name, character_name, turn_index) -> MockResponse
        self._responses: dict[tuple[str, str, int], MockResponse] = {}
        # Call tracking for verification
        self._calls: list[dict[str, Any]] = []

    def register_response(
        self,
        scenario: str,
        character: str,
        turn: int,
        text: str,
        thinking: str = "",
        tokens: list[str] | None = None,
    ) -> None:
        """Register a canned response."""
        key = (scenario, character, turn)
        self._responses[key] = MockResponse(
            text=text,
            thinking=thinking,
            tokens=tokens or [text],
        )

    def register_response_from_expectation(
        self,
        scenario: str,
        character: str,
        turn: int,
        must_contain: list[str],
        must_not_contain: list[str],
    ) -> None:
        """Generate a response that satisfies the expectation."""
        text = " ".join(must_contain) if must_contain else f"*{character} отвечает*"
        self.register_response(scenario, character, turn, text)

    def get_response(
        self,
        scenario: str,
        character: str,
        turn: int,
    ) -> MockResponse | None:
        """Get registered response or generate default. Does not consume the response."""
        key = (scenario, character, turn)
        return self._responses.get(key)

    def clear(self) -> None:
        """Clear all registered responses and call history."""
        self._responses.clear()
        self._calls.clear()

    async def generate(
        self,
        client: Any,
        chat_id: int,
        character: Any,
        messages_history: list,
        general_prompt: str,
        memories: list,
        other_character_names: list[str],
        max_history_length: int,
        model_name: str,
        character_names: dict[int, str] | None,
        summary: str | None,
        viewer_character_id: int | None,
        presence_map: dict[int, str] | None,
        same_round_message_ids: set[int] | None,
        enable_thinking: bool,
        viewer_location: str | None,
        character_locations: dict[int, str] | None,
        prior_replies: list[tuple[str, str]] | None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Mock generate that yields streaming events."""
        scenario = getattr(character, "_eval_scenario", "unknown")
        turn = getattr(character, "_eval_turn", 0)
        char_name = character.name

        # Helper to get content from either dict or SQLAlchemy model
        def get_content(m):
            if hasattr(m, "get"):
                return m.get("content", "")
            return getattr(m, "content", "")

        self._calls.append({
            "scenario": scenario,
            "character": char_name,
            "turn": turn,
            "prompt_length": sum(len(get_content(m)) for m in messages_history),
            "memories_count": len(memories),
            "has_summary": bool(summary),
            "other_chars": other_character_names,
        })

        # Character generation calls have prior_replies parameter (list of tuples)
        # Memory extraction/summary calls have prior_replies=None
        # But to be safe, also check for "ТЕКУЩИЙ ПЕРСОНАЖ" in the prompt
        is_character_generation = prior_replies is not None

        response = self.get_response(scenario, char_name, turn)
        if response is not None:
            # Registered response found - use it
            pass
        elif prior_replies is not None:
            # Character generation call but no registered response - use default
            response = MockResponse(text=f"*{char_name} отвечает на реплику игрока*")
            print(f"[MOCK LLM] Default response for {char_name} in {scenario} turn {turn}")
        else:
            # Memory extraction/summary: return empty array
            response = MockResponse(text="[]")
            print(f"[MOCK LLM] Returning empty array for memory/summary call: {char_name} in {scenario} turn {turn}")

        print(f"[MOCK LLM] Returning response for {char_name} in {scenario} turn {turn}: {response.text[:50]}...")
        for event in response.to_events():
            yield event
            if event["type"] == "token":
                import asyncio
                await asyncio.sleep(0.001)  # Minimal delay for streaming feel

    def get_call_history(self) -> list[dict[str, Any]]:
        """Return recorded call history for verification."""
        return self._calls.copy()


# Global instance
MOCK_LLM = MockLLM()


# Convenience functions for backward compatibility
def reset_mock_llm() -> None:
    """Reset the global mock LLM."""
    MOCK_LLM.clear()


def set_mock_scenario(name: str) -> None:
    """Set the active scenario name."""
    # This is a no-op in the current implementation since we store by scenario name
    pass


def advance_mock_turn() -> None:
    """Advance the mock turn counter."""
    # This is a no-op in the current implementation since we track by turn index
    pass