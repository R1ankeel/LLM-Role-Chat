"""Evaluation harness for running golden scenarios and computing metrics."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import yaml

from tests.eval.mock_llm import MOCK_LLM, reset_mock_llm, set_mock_scenario, advance_mock_turn
from tests.eval.metrics import (
    compute_isolation_violation_rate,
    compute_fact_recall_at_k,
    compute_style_similarity,
    compute_silence_rate,
    compute_repetition_rate,
    compute_witness_leakage,
    compute_consolidation_metrics,
    compute_scene_state_consistency,
    compute_all_metrics,
    MetricResult,
)

logger = logging.getLogger(__name__)


@dataclass
class ScenarioExpectation:
    """Expected output for a character at a specific turn."""
    character: str
    must_contain: list[str] = field(default_factory=list)
    must_not_contain: list[str] = field(default_factory=list)
    min_length: int = 1


@dataclass
class ScenarioTurn:
    """A single turn in a scenario (user input + expected character responses)."""
    user: str | None = None
    expect: list[ScenarioExpectation] = field(default_factory=list)


@dataclass
class Scenario:
    """Complete evaluation scenario loaded from YAML."""
    name: str
    chat: dict[str, Any]
    turns: list[ScenarioTurn]

    @classmethod
    def from_yaml(cls, path: Path) -> "Scenario":
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls(
            name=data["name"],
            chat=data["chat"],
            turns=[
                ScenarioTurn(
                    user=turn.get("user"),
                    expect=[
                        ScenarioExpectation(**exp) for exp in turn.get("expect", [])
                    ],
                )
                for turn in data.get("turns", [])
            ],
        )


@dataclass
class ScenarioResult:
    """Results from running a single scenario."""
    scenario_name: str
    passed: bool
    turn_results: list[dict[str, Any]]
    metrics: dict[str, MetricResult]
    errors: list[str] = field(default_factory=list)


class EvalHarness:
    """
    Runs evaluation scenarios against the chat engine using a mock LLM.
    
    Flow:
    1. Load scenario YAML
    2. Register mock responses from expectations
    3. Create chat + characters in test DB
    4. Run each turn through chat_engine.process_user_message_streaming
    5. Collect actual responses
    6. Validate against expectations
    7. Compute metrics
    """

    def __init__(
        self,
        db_session_factory: Any,
        client: Any,
        mock_mode: bool = True,
    ):
        self.db_session_factory = db_session_factory
        self.client = client
        self.mock_mode = mock_mode

    async def run_scenario(self, scenario: Scenario) -> ScenarioResult:
        """Execute a full scenario and return results."""
        reset_mock_llm()
        set_mock_scenario(scenario.name)

        # Register mock responses from expectations
        self._register_mock_responses(scenario)

        # Create chat and characters
        async with self.db_session_factory() as db:
            from app.crud import create_chat, create_character
            from app import schemas

            chat = await create_chat(
                db,
                schemas.ChatCreate(
                    name=f"eval_{scenario.name}",
                    general_prompt=scenario.chat.get("prompt", ""),
                    model_name=scenario.chat.get("model", "test-model"),
                ),
            )

            characters = []
            for i, char_data in enumerate(scenario.chat.get("characters", [])):
                char = await create_character(
                    db,
                    chat.id,
                    schemas.CharacterCreate(
                        name=char_data["name"],
                        personality=char_data.get("personality", ""),
                        traits=char_data.get("traits", ""),
                        background=char_data.get("background", ""),
                        speech_style=char_data.get("speech_style", ""),
                        order_index=i + 1,
                    ),
                )
                characters.append(char)

            # Import chat_engine here to avoid circular imports
            from app.chat_engine import process_user_message_streaming
            from unittest.mock import patch, AsyncMock

            # Comprehensively mock ollama_client for both generation and memory extraction
            async def mock_generate(*args, **kwargs):
                async for event in MOCK_LLM.generate(*args, **kwargs):
                    yield event

            # Mock the internal Ollama HTTP calls for memory extraction
            async def mock_invoke_llm(*args, **kwargs):
                return '[]'  # Empty memory extraction result

            # Mock extract_memories_for_character to return empty list
            async def mock_extract_memories(*args, **kwargs):
                return []

            # Mock summarize_for_character to return empty string
            async def mock_summarize(*args, **kwargs):
                return ""

            with patch("app.chat_engine.ollama_client.generate", mock_generate), \
                 patch("app.chat_engine.ollama_client._invoke_llm", mock_invoke_llm), \
                 patch("app.chat_engine.ollama_client.extract_memories_for_character", mock_extract_memories), \
                 patch("app.chat_engine.ollama_client.summarize_for_character", mock_summarize), \
                 patch("app.memory_service.ollama_client._invoke_llm", mock_invoke_llm), \
                 patch("app.memory_service.ollama_client.extract_memories_for_character", mock_extract_memories), \
                 patch("app.memory_service.ollama_client.summarize_for_character", mock_summarize):
                turn_results = []
                all_responses = []  # (character_name, response_text, turn_idx)

                for turn_idx, turn in enumerate(scenario.turns):
                    advance_mock_turn()

                    # Set mock LLM context on characters
                    for char in characters:
                        char._eval_scenario = scenario.name
                        char._eval_turn = turn_idx

                    if turn.user is None:
                        continue

                    # Process user message
                    events = []
                    async for event in process_user_message_streaming(
                        client=self.client,
                        db=db,
                        chat_id=chat.id,
                        user_text=turn.user,
                    ):
                        events.append(event)

                # Extract character responses from events
                turn_responses = {}
                for event in events:
                    if event.get("type") == "message" and event.get("message", {}).get("role") == "character":
                        msg = event["message"]
                        # Handle both dict and SQLAlchemy model
                        if hasattr(msg, "get"):
                            char_name = msg.get("character_name") or msg.get("character_id")
                            if char_name is None:
                                char_name = "Unknown"
                            # If we have character_id, look up the name
                            if isinstance(char_name, int) or (isinstance(char_name, str) and char_name.isdigit()):
                                char_id = int(char_name)
                                char_name = next((c.name for c in characters if c.id == char_id), "Unknown")
                        else:
                            # SQLAlchemy model
                            char_name = getattr(msg, "character_name", None) or getattr(msg, "name", None) or getattr(msg, "character_id", None)
                            if char_name is None:
                                char_name = "Unknown"
                            if isinstance(char_name, int) or (isinstance(char_name, str) and char_name.isdigit()):
                                char_id = int(char_name)
                                char_name = next((c.name for c in characters if c.id == char_id), "Unknown")
                        turn_responses[char_name] = msg.get("content", "") if hasattr(msg, "get") else getattr(msg, "content", "")
                        all_responses.append((char_name, turn_responses[char_name], turn_idx))

                # Validate expectations for this turn
                turn_result = {
                    "turn": turn_idx,
                    "user": turn.user,
                    "responses": turn_responses,
                    "expectations": [],
                    "passed": True,
                }

                for exp in turn.expect:
                    actual = turn_responses.get(exp.character, "")
                    passed = self._validate_expectation(actual, exp)
                    turn_result["expectations"].append({
                        "character": exp.character,
                        "expected_contains": exp.must_contain,
                        "expected_not_contains": exp.must_not_contain,
                        "actual": actual,
                        "passed": passed,
                    })
                    if not passed:
                        turn_result["passed"] = False

                turn_results.append(turn_result)

            # Compute metrics
            metrics = self._compute_metrics(scenario, all_responses, characters)

            # Overall pass/fail
            passed = all(t["passed"] for t in turn_results)

            return ScenarioResult(
                scenario_name=scenario.name,
                passed=passed,
                turn_results=turn_results,
                metrics=metrics,
            )

    def _register_mock_responses(self, scenario: Scenario) -> None:
        """Register canned responses for each expected character response."""
        for turn_idx, turn in enumerate(scenario.turns):
            for exp in turn.expect:
                # Build a response that satisfies the expectation
                response_parts = []
                if exp.must_contain:
                    response_parts.extend(exp.must_contain)
                if not response_parts:
                    response_parts = [f"*{exp.character} отвечает*"]
                response_text = " ".join(response_parts)

                MOCK_LLM.register_response(
                    scenario=scenario.name,
                    character=exp.character,
                    turn=turn_idx,
                    text=response_text,
                )

    def _validate_expectation(self, actual: str, exp: ScenarioExpectation) -> bool:
        """Check if actual response meets expectation."""
        if len(actual.strip()) < exp.min_length:
            return False
        for phrase in exp.must_contain:
            if phrase.lower() not in actual.lower():
                return False
        for phrase in exp.must_not_contain:
            if phrase.lower() in actual.lower():
                return False
        return True

    def _compute_metrics(
        self,
        scenario: Scenario,
        responses: list[tuple[str, str, int]],
        characters: list[Any],
    ) -> dict[str, MetricResult]:
        """Compute all evaluation metrics for the scenario."""
        char_names = [c.name for c in characters]
        char_map = {c.name: c for c in characters}

        # Use the comprehensive metrics function
        return compute_all_metrics(responses, scenario, char_map)

    async def run_all(self, scenario_dir: Path) -> list[ScenarioResult]:
        """Run all scenarios in a directory."""
        results = []
        for yaml_file in sorted(scenario_dir.glob("*.yaml")):
            scenario = Scenario.from_yaml(yaml_file)
            logger.info("Running scenario: %s", scenario.name)
            result = await self.run_scenario(scenario)
            results.append(result)
            logger.info("Scenario %s: %s", scenario.name, "PASS" if result.passed else "FAIL")
        return results


def print_results(results: list[ScenarioResult]) -> None:
    """Pretty-print evaluation results."""
    print("\n" + "=" * 60)
    print("EVALUATION RESULTS")
    print("=" * 60)

    for r in results:
        status = "✓ PASS" if r.passed else "✗ FAIL"
        print(f"\n{r.scenario_name}: {status}")

        for turn in r.turn_results:
            for exp in turn["expectations"]:
                exp_status = "✓" if exp["passed"] else "✗"
                print(f"  Turn {turn['turn']} [{exp['character']}]: {exp_status}")
                if not exp["passed"]:
                    print(f"    Expected contains: {exp['expected_contains']}")
                    print(f"    Expected not contains: {exp['expected_not_contains']}")
                    print(f"    Actual: {exp['actual'][:100]}...")

        print("\n  Metrics:")
        for name, metric in r.metrics.items():
            print(f"    {name}: {metric.value:.3f} ({metric.details})")

    print("\n" + "=" * 60)
    passed = sum(1 for r in results if r.passed)
    print(f"Total: {passed}/{len(results)} scenarios passed")
    print("=" * 60)