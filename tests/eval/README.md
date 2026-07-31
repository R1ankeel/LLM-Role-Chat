# Evaluation Harness Documentation

## Overview

The evaluation harness (`tests/eval/`) provides automated regression testing for the AI roleplay chat system. It runs predefined scenarios against the chat engine using a mock LLM (fast, deterministic) or real Ollama (slower, validates actual model behavior).

## Quick Start

### Mock Mode (Fast, No Ollama Required)

```bash
cd ai-roleplay-chat
python -m tests.eval.run_eval --mode mock
```

### Mock Mode with Verbose Output

```bash
cd ai-roleplay-chat
python -m tests.eval.run_eval --mode mock --verbose
```

### Real Model Mode (Requires Local Ollama)

```bash
cd ai-roleplay-chat
python -m tests.eval.run_eval --mode real
```

### Run Specific Scenario

```bash
cd ai-roleplay-chat
python -m tests.eval.run_eval --mode mock --scenarios tests/eval/scenarios/isolation_basic.yaml
```

### Generate JUnit XML for CI

```bash
cd ai-roleplay-chat
python -m tests.eval.run_eval --mode mock --junit-xml eval-results.xml
```

## Scenario Format

Scenarios are YAML files in `tests/eval/scenarios/`:

```yaml
name: "scenario_name"
chat:
  prompt: "Scene description"
  model: "test-model"
  characters:
    - name: "Character1"
      personality: "..."
      traits: "..."
      background: "..."
      speech_style: "..."
      location: "library"
    - name: "Character2"
      ...
turns:
  - user: "Player input"
    expect:
      - character: "Character1"
        must_contain: ["keyword1", "keyword2"]
        must_not_contain: ["forbidden1"]
      - character: "Character2"
        must_contain: ["keyword3"]
```

## Metrics Computed

The harness computes these metrics per scenario:

| Metric | Description | Pass Threshold |
|--------|-------------|----------------|
| `isolation_violation_rate` | Foreign speaker markers + semantic contamination | < 10% |
| `fact_recall_at_5` | Can character recall facts from 5 turns ago | ≥ 70% |
| `style_similarity` | Jaccard similarity vs example messages | ≥ 70% |
| `silence_rate` | Empty/placeholder responses | < 10% |
| `repetition_rate` | Self-repetition (n-gram overlap) | < 30% |
| `witness_leakage` | Characters knowing unwitnessed events | 0% |
| `consolidation_score` | Memory dedup/merge effectiveness | N/A |
| `scene_state_consistency` | Location/time/present character tracking | N/A |

## Adding New Scenarios

1. Create a new `.yaml` file in `tests/eval/scenarios/`
2. Follow the format above
3. Run in mock mode to verify: `python -m tests.eval.run_eval --mode mock --scenarios tests/eval/scenarios/your_scenario.yaml`

## Golden Tests

The `tests/golden/` directory contains snapshot tests for prompt builder and role isolation outputs:

```bash
# Run all golden tests
pytest tests/golden/ -v

# Update snapshots (when intentionally changing output format)
python -m tests.eval.run_eval --mode mock --update-snapshots
```

## CI Integration

The GitHub Actions workflow (`.github/workflows/eval.yml`) runs:
- **Mock evaluation** on every push/PR (fast, ~30 seconds)
- **Real model evaluation** nightly on self-hosted runner with Ollama (optional)

### Self-Hosted Runner Setup

For real model evaluation:
1. Set up a self-hosted GitHub Actions runner
2. Install Ollama and pull required models:
   ```bash
   ollama pull qwen3-coder:30b-a3b-q4_K_M
   ```
3. Add `self-hosted` label to the runner

## Architecture

```
tests/eval/
├── harness.py       # EvalHarness class - runs scenarios, computes metrics
├── metrics.py       # 8 metric implementations
├── mock_llm.py      # Deterministic MockLLM for fast testing
├── run_eval.py      # CLI entry point
└── scenarios/       # YAML scenario definitions
```

## Troubleshooting

### "No scenarios found"
Ensure scenario files are in `tests/eval/scenarios/` with `.yaml` extension.

### Mock LLM not returning expected responses
The mock LLM generates responses from `must_contain` expectations. Add explicit `must_contain` phrases to expectations for better mock responses.

### Real mode fails
Ensure Ollama is running (`ollama serve`) and the model is available (`ollama list`).

### Database errors
Each scenario runs with a fresh in-memory SQLite database. No persistence between runs.