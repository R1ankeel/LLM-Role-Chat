"""Main entry point for running evaluation harness."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import pytest

from tests.eval.harness import EvalHarness, Scenario, ScenarioResult
from tests.eval.mock_llm import MOCK_LLM, reset_mock_llm


async def run_scenarios(
    scenario_dir: Path,
    db_session_factory,
    client,
    mock_mode: bool = True,
    verbose: bool = False,
) -> dict[str, any]:
    """Run all scenarios in a directory."""
    scenario_files = sorted(scenario_dir.glob("*.yaml"))
    if not scenario_files:
        print(f"No scenarios found in {scenario_dir}")
        return {"passed": 0, "failed": 0, "results": []}

    harness = EvalHarness(db_session_factory, client, mock_mode=mock_mode)
    results = []

    for scenario_file in scenario_files:
        print(f"\n{'='*60}")
        print(f"Running scenario: {scenario_file.stem}")
        print(f"{'='*60}")

        scenario = Scenario.from_yaml(scenario_file)
        reset_mock_llm()

        try:
            result = await harness.run_scenario(scenario)
            results.append(result)

            status = "PASSED" if result.passed else "FAILED"
            print(f"Result: {status}")

            if verbose or not result.passed:
                for turn in result.turn_results:
                    print(f"  Turn {turn['turn']}: {turn.get('user', '')[:50]}")
                    for exp in turn.get('expectations', []):
                        exp_status = "✓" if exp['passed'] else "✗"
                        print(f"    {exp_status} {exp['character']}: {exp['actual'][:80]}")

            # Print metrics
            for metric_name, metric_result in result.metrics.items():
                print(f"  {metric_name}: {metric_result.value:.3f} - {metric_result.details}")

        except Exception as e:
            print(f"ERROR running scenario: {e}")
            if verbose:
                import traceback
                traceback.print_exc()
            results.append(ScenarioResult(
                scenario_name=scenario_file.stem,
                passed=False,
                turn_results=[],
                metrics={},
                errors=[str(e)]
            ))

    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed

    print(f"\n{'='*60}")
    print(f"SUMMARY: {passed} passed, {failed} failed")
    print(f"{'='*60}")

    return {
        "passed": passed,
        "failed": failed,
        "results": results,
    }


def run_pytest_tests(
    test_paths: list[str],
    verbose: bool = False,
    junit_xml: str | None = None,
) -> int:
    """Run pytest on specified test paths."""
    args = ["-v"] if verbose else ["-q"]
    if junit_xml:
        args.extend(["--junit-xml", junit_xml])
    args.extend(test_paths)

    return pytest.main(args)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run evaluation harness")
    parser.add_argument(
        "--mode",
        choices=["mock", "real", "pytest"],
        default="mock",
        help="Evaluation mode (default: mock)",
    )
    parser.add_argument(
        "--scenarios",
        type=Path,
        default=Path("tests/eval/scenarios"),
        help="Path to scenario YAML files",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose output",
    )
    parser.add_argument(
        "--junit-xml",
        type=Path,
        help="Output JUnit XML for CI",
    )
    parser.add_argument(
        "--update-snapshots",
        action="store_true",
        help="Update golden test snapshots",
    )
    parser.add_argument(
        "pytest_args",
        nargs="*",
        help="Additional arguments passed to pytest",
    )

    args = parser.parse_args()

    if args.mode == "pytest":
        test_paths = args.pytest_args or ["tests"]
        return run_pytest_tests(test_paths, args.verbose, str(args.junit_xml) if args.junit_xml else None)

    # Import here to avoid circular imports
    from tests.conftest import db_engine, db_session
    import httpx

    async def run():
        # Create mock client
        client = httpx.AsyncClient(base_url="http://mock-ollama", timeout=30.0)

        # Get session factory
        from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
        from app.database import Base
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:?cache=shared",
            connect_args={"check_same_thread": False},
        )

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        def session_factory():
            return async_session()

        return await run_scenarios(
            args.scenarios,
            session_factory,
            client,
            mock_mode=(args.mode == "mock"),
            verbose=args.verbose,
        )

    result = asyncio.run(run())

    if args.junit_xml:
        import xml.etree.ElementTree as ET
        root = ET.Element("testsuite", name="eval_harness", tests=str(len(result["results"])))
        for res in result["results"]:
            tc = ET.SubElement(root, "testcase", name=res.get("scenario_name", "unknown"))
            if not res.get("passed", False):
                ET.SubElement(tc, "failure", message="Scenario failed")
        tree = ET.ElementTree(root)
        tree.write(args.junit_xml, encoding="utf-8", xml_declaration=True)

    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())