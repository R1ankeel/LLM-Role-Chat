"""Evaluation metrics for regression prevention."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class MetricResult:
    """Result of a metric computation."""
    value: float
    details: str
    passed: bool = True

    def __post_init__(self):
        # For violation rates, lower is better
        if "rate" in self.details.lower() or "violation" in self.details.lower():
            self.passed = self.value < 0.1  # Less than 10% violation rate
        # For recall/similarity, higher is better
        elif "recall" in self.details.lower() or "similarity" in self.details.lower():
            self.passed = self.value >= 0.7


def compute_isolation_violation_rate(
    responses: list[tuple[str, str, int]],  # (character, response, turn)
    character_names: list[str],
) -> MetricResult:
    """
    Compute role isolation violation rate.
    
    Checks for:
    - Foreign speaker markers (CharacterName:)
    - Semantic contamination (speaking for others' internal states)
    """
    if not responses:
        return MetricResult(value=0.0, details="No responses")

    violations = 0
    total = len(responses)

    # Patterns for semantic contamination
    contamination_patterns = [
        r"\b(он|она|они)\s+(подумал|подумала|подумали|чувствовал|чувствовала|чувствовали|решил|решила|решили|хотел|хотела|хотели|знал|знала|знали)\b",
        r"\b(я\s+знаю|ты\s+думал)\s+(что|как)\b",
    ]

    for char_name, response, turn in responses:
        other_names = [n for n in character_names if n != char_name]

        # Check for foreign speaker markers
        for other in other_names:
            pattern = rf"(?m)^(?:\*\*)?{re.escape(other)}(?:\*\*)?:\s"
            if re.search(pattern, response):
                violations += 1
                break

        # Check for semantic contamination
        lowered = response.lower()
        for pattern in contamination_patterns:
            if re.search(pattern, lowered, re.IGNORECASE):
                violations += 1
                break

        # Check for other character names with internal state verbs
        for other in other_names:
            other_lower = other.lower()
            if other_lower in lowered:
                if re.search(
                    rf"{re.escape(other_lower)}.*\b(думал|думала|чувствовал|чувствовала|хотел|хотела|знал|знала|решил|решила|боится|любит|ненавидит|помнит)\b",
                    lowered
                ):
                    violations += 1
                    break

    rate = violations / total if total > 0 else 0.0
    return MetricResult(
        value=rate,
        details=f"{violations}/{total} responses violated isolation",
    )


def compute_fact_recall_at_k(
    responses: list[tuple[str, str, int]],
    scenario: Any,
    character_map: dict[str, Any],
    k: int = 5,
) -> MetricResult:
    """
    Compute fact recall@k - can characters recall facts from k turns ago?
    
    Expects scenario.expected_facts dict mapping character -> list of fact strings.
    """
    if not hasattr(scenario, "expected_facts") or not scenario.expected_facts:
        return MetricResult(value=1.0, details="No expected facts defined")

    total_facts = 0
    recalled = 0

    for char_name, response, turn in responses:
        if char_name not in scenario.expected_facts:
            continue

        for fact in scenario.expected_facts[char_name]:
            total_facts += 1
            # Simple containment check - in reality would use semantic similarity
            if fact.lower() in response.lower():
                recalled += 1

    rate = recalled / total_facts if total_facts > 0 else 1.0
    return MetricResult(
        value=rate,
        details=f"Recalled {recalled}/{total_facts} expected facts",
    )


def compute_style_similarity(
    responses: list[tuple[str, str, int]],
    scenario: Any,
    character_map: dict[str, Any],
) -> MetricResult:
    """
    Compute style similarity to character's example messages.
    
    Uses Jaccard similarity on word sets as proxy for style consistency.
    """
    if not hasattr(scenario, "style_examples") or not scenario.style_examples:
        return MetricResult(value=1.0, details="No style examples defined")

    similarities = []

    for char_name, response, turn in responses:
        if char_name not in scenario.style_examples:
            similarities.append(1.0)
            continue

        examples = scenario.style_examples[char_name]
        if not examples:
            similarities.append(1.0)
            continue

        resp_words = set(response.lower().split())
        best_sim = 0.0

        for example in examples:
            ex_words = set(example.lower().split())
            if not ex_words:
                continue
            intersection = len(resp_words & ex_words)
            union = len(resp_words | ex_words)
            if union > 0:
                sim = intersection / union
                best_sim = max(best_sim, sim)

        similarities.append(best_sim)

    avg_sim = sum(similarities) / len(similarities) if similarities else 1.0
    return MetricResult(
        value=avg_sim,
        details=f"Avg style similarity: {avg_sim:.3f}",
    )


def compute_silence_rate(
    responses: list[tuple[str, str, int]],
    character_names: list[str],
) -> MetricResult:
    """Compute rate of empty/placeholder responses."""
    if not responses:
        return MetricResult(value=0.0, details="No responses")

    silences = 0
    total = len(responses)

    silence_patterns = [
        r"^\s*\*.*молчит.*\*$",
        r"^\s*\*.*не в силах ответить.*\*$",
        r"^\s*$",
    ]

    for char_name, response, turn in responses:
        if any(re.search(p, response.strip(), re.IGNORECASE) for p in silence_patterns):
            silences += 1

    rate = silences / total
    return MetricResult(
        value=rate,
        details=f"{silences}/{total} responses were silence/placeholder",
    )


def compute_repetition_rate(
    responses: list[tuple[str, str, int]],
    window: int = 6,
) -> MetricResult:
    """Compute self-repetition rate using n-gram overlap."""
    if not responses:
        return MetricResult(value=0.0, details="No responses")

    # Group by character
    by_char: dict[str, list[str]] = {}
    for char_name, response, turn in responses:
        by_char.setdefault(char_name, []).append(response)

    total_repetitive = 0
    total_compared = 0

    for char_name, char_responses in by_char.items():
        for i in range(1, len(char_responses)):
            start = max(0, i - window)
            for j in range(start, i):
                total_compared += 1
                if _is_repetitive(char_responses[i], char_responses[j]):
                    total_repetitive += 1

    rate = total_repetitive / total_compared if total_compared > 0 else 0.0
    return MetricResult(
        value=rate,
        details=f"{total_repetitive}/{total_compared} pairwise comparisons repetitive",
    )


def _is_repetitive(text1: str, text2: str, threshold: float = 0.7) -> bool:
    """Check if two texts are semantically repetitive using word overlap."""
    words1 = set(text1.lower().split())
    words2 = set(text2.lower().split())

    if not words1 or not words2:
        return False

    intersection = len(words1 & words2)
    union = len(words1 | words2)
    jaccard = intersection / union

    return jaccard >= threshold


def compute_witness_leakage(
    responses: list[tuple[str, str, int]],
    scenario: Any,
    character_map: dict[str, Any],
) -> MetricResult:
    """
    Compute witness filter leakage - characters knowing things they shouldn't.
    
    Expects scenario.witness_metrics with leakage thresholds.
    """
    if not hasattr(scenario, "witness_metrics"):
        return MetricResult(value=0.0, details="No witness metrics defined")

    # This is a simplified check - real implementation would verify
    # that characters don't reference events they couldn't perceive
    return MetricResult(value=0.0, details="Witness filter check placeholder")


def compute_consolidation_metrics(
    responses: list[tuple[str, str, int]],
    scenario: Any,
    character_map: dict[str, Any],
) -> MetricResult:
    """
    Compute memory consolidation metrics.
    
    Expects scenario.consolidation_metrics with dedup thresholds.
    """
    if not hasattr(scenario, "consolidation_metrics"):
        return MetricResult(value=1.0, details="No consolidation metrics defined")

    # Check if responses mention "consolidated" or "merged" appropriately
    # This is a proxy - real check would query the DB for memory counts
    return MetricResult(value=1.0, details="Consolidation check placeholder")


def compute_scene_state_consistency(
    responses: list[tuple[str, str, int]],
    scenario: Any,
    character_map: dict[str, Any],
) -> MetricResult:
    """
    Compute scene state consistency - characters aware of correct location/time/present chars.
    
    Expects scenario.scene_state_expectations with expected state per turn.
    """
    if not hasattr(scenario, "scene_state_expectations"):
        return MetricResult(value=1.0, details="No scene state expectations defined")

    # Check that responses reference correct location/time
    consistent = 0
    total = 0

    # This is a placeholder - real implementation would check against
    # the expected scene state for each turn
    return MetricResult(value=1.0, details="Scene state check placeholder")


def compute_all_metrics(
    responses: list[tuple[str, str, int]],
    scenario: Any,
    character_map: dict[str, Any],
) -> dict[str, MetricResult]:
    """Compute all available metrics for a scenario."""
    character_names = list(character_map.keys())

    return {
        "isolation_violation_rate": compute_isolation_violation_rate(
            responses, character_names
        ),
        "fact_recall_at_5": compute_fact_recall_at_k(
            responses, scenario, character_map, k=5
        ),
        "style_similarity": compute_style_similarity(
            responses, scenario, character_map
        ),
        "silence_rate": compute_silence_rate(responses, character_names),
        "repetition_rate": compute_repetition_rate(responses),
        "witness_leakage": compute_witness_leakage(responses, scenario, character_map),
        "consolidation_score": compute_consolidation_metrics(responses, scenario, character_map),
        "scene_state_consistency": compute_scene_state_consistency(responses, scenario, character_map),
    }