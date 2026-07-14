from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from host_brain.planning_search.benchmarks.adapters import adapter_for
from host_brain.planning_search.benchmarks.contracts import (
    EVIDENCE_LEVEL,
    FrozenBenchmarkSuite,
    payload_digest,
)
from host_brain.planning_search.benchmarks.runner import (
    GREEDY,
    PUBLIC_NO_L3,
    PUBLIC_SEARCH,
    build_artifact,
)


ROOT = Path(__file__).parents[2]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "planning_search_shadow"
SUITE_PATHS = (FIXTURE_ROOT / "code_v1.json", FIXTURE_ROOT / "research_v1.json")
ARTIFACT_PATH = (
    ROOT
    / "docs"
    / "45_testing"
    / "test_reports"
    / "gr02a_planning_search_shadow_benchmark_v1.json"
)


def test_frozen_code_and_research_suites_are_pure_and_hide_oracle_fields() -> None:
    for path, domain in zip(SUITE_PATHS, ("code", "research"), strict=True):
        raw = json.loads(path.read_text(encoding="utf-8"))
        suite = FrozenBenchmarkSuite.load(path)

        assert suite.domain == domain
        assert suite.evidence_level == EVIDENCE_LEVEL
        assert suite.production_benefit_claimed is False
        assert set(suite.latency_model_ms) == {
            "candidates",
            "expansions",
            "transitions",
            "rollouts",
        }
        assert len(suite.scenarios) == 5
        assert sum(scenario.requires_lookahead for scenario in suite.scenarios) == 4
        assert json.loads(json.dumps(raw, allow_nan=False)) == raw

        for scenario in suite.scenarios:
            adapter = adapter_for(scenario)
            initial = adapter.initial_virtual_state
            snapshot = deepcopy(initial)
            candidates = adapter.expand(
                state=initial,
                path=(),
                depth=0,
                limit=suite.budget.max_candidates,
                context={},
            )
            assert initial == snapshot
            assert len(candidates) >= 2
            next_state = adapter.transition(
                state=initial,
                candidate=candidates[0],
                path=(candidates[0],),
                context={},
            )
            assert initial == snapshot
            assert next_state is not initial
            for candidate in candidates:
                assert not {
                    "valid",
                    "valid_in_fixture",
                    "success",
                    "validation_passed",
                    "reward",
                    "from_state",
                    "to_state",
                } & set(candidate.payload)


def test_iterative_greedy_and_l2_ablation_use_same_limits_before_l3_gain() -> None:
    artifact = build_artifact(SUITE_PATHS)

    for suite in artifact["suites"]:
        budget = suite["configured_budget_shared_by_strategies"]
        assert suite["fixture"]["scenario_count"] == 5
        assert suite["comparison"]["explainable_positive_gain"] is True
        assert suite["comparison"]["success_rate_gain"] == 0.8
        assert suite["l3_ablation"]["success_rate_gain"] == 0.8

        records = {
            strategy: {
                item["scenario_id"]: item
                for item in suite["strategies"][strategy]["scenarios"]
            }
            for strategy in (GREEDY, PUBLIC_NO_L3, PUBLIC_SEARCH)
        }
        for scenario_id, public in records[PUBLIC_SEARCH].items():
            greedy = records[GREEDY][scenario_id]
            no_l3 = records[PUBLIC_NO_L3][scenario_id]
            is_control = scenario_id.endswith("_control")

            for record in (greedy, no_l3, public):
                assert record["side_effects_executed"] is False
                assert len(record["selected_path"]) <= budget["max_depth"]
                assert record["cost"]["candidates"] <= budget["max_candidates"]
                assert record["cost"]["expansions"] <= budget["max_expansions"]
                assert record["cost"]["transitions"] <= budget["max_transitions"]
                assert record["cost"]["rollouts"] <= budget["max_rollouts"]
                expected_latency = sum(
                    int(record["cost"][name])
                    * int(suite["logical_latency_model_ms_shared_by_strategies"][name])
                    for name in record["cost"]
                )
                assert record["logical_latency_ms"] == expected_latency

            if is_control:
                assert greedy["outcome"]["success"] is True
                assert no_l3["outcome"]["success"] is True
                assert public["outcome"]["success"] is True
                assert public["mcts_used"] is False
            else:
                # Greedy is iterative and stops because its selected local optimum
                # is terminal, not because the benchmark artificially truncates it.
                assert greedy["stop_reason"] == "terminal_state"
                assert greedy["outcome"]["success"] is False
                assert no_l3["outcome"]["success"] is False
                assert public["outcome"]["success"] is True
                assert public["outcome"]["validation_passed"] is True
                assert public["mcts_used"] is True
                assert len(public["selected_path"]) >= 2
                assert public["selected_path"] != greedy["selected_path"]


def test_reported_metrics_costs_and_deterministic_traces_are_complete() -> None:
    first = build_artifact(SUITE_PATHS)
    second = build_artifact(SUITE_PATHS)

    assert first == second
    assert first["evidence_level"] == EVIDENCE_LEVEL
    assert first["production_benefit_claimed"] is False
    assert first["deterministic"] is True
    assert "production efficacy evidence" in first["claim_scope"]
    assert "generated_at" not in first

    expected_digest = first["artifact_payload_sha256"]
    without_digest = deepcopy(first)
    without_digest.pop("artifact_payload_sha256")
    assert payload_digest(without_digest) == expected_digest

    overall = first["overall"]
    assert overall[GREEDY]["success_rate"] == 0.2
    assert overall[PUBLIC_NO_L3]["success_rate"] == 0.2
    assert overall[PUBLIC_SEARCH]["success_rate"] == 1.0
    assert overall[PUBLIC_SEARCH]["validation_pass_rate"] == 1.0
    assert overall["baseline_to_candidate"]["invalid_action_rate_reduction"] == 0.4
    assert overall[PUBLIC_SEARCH]["cost_total"]["rollouts"] > 0
    assert overall[PUBLIC_SEARCH]["cost_total"]["transitions"] > overall[GREEDY]["cost_total"]["transitions"]
    assert overall[PUBLIC_SEARCH]["logical_latency_ms_total"] > 0
    assert overall["baseline_to_candidate"]["logical_latency_ms_delta_total"] > 0

    for strategy in (GREEDY, PUBLIC_NO_L3, PUBLIC_SEARCH):
        metrics = overall[strategy]
        assert set(metrics["cost_total"]) == {
            "candidates",
            "expansions",
            "transitions",
            "rollouts",
        }
        assert re.fullmatch(r"[0-9a-f]{64}", metrics["aggregate_trace_digest"])
        assert all(re.fullmatch(r"[0-9a-f]{64}", digest) for digest in metrics["trace_digests"].values())


def test_checked_in_machine_artifact_matches_the_deterministic_runner() -> None:
    checked_in = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    generated = build_artifact(SUITE_PATHS)

    assert checked_in == generated
    assert checked_in["evidence_level"] == "SIMULATION_SHADOW"
    assert checked_in["production_benefit_claimed"] is False
    assert all(suite["production_benefit_claimed"] is False for suite in checked_in["suites"])


def test_benchmark_package_is_independent_of_runtime_domains() -> None:
    package = ROOT / "host_brain" / "planning_search" / "benchmarks"
    source = "\n".join(path.read_text(encoding="utf-8") for path in sorted(package.glob("*.py")))

    assert "host_brain.pentest" not in source
    assert "host_brain.task_runtime" not in source
    assert "host_brain.executive_runtime" not in source
    assert "host_brain.cortex" not in source
