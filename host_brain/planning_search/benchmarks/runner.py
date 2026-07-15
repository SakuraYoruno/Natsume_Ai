from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..contracts import MCTSMode, SearchBudget, SearchCandidate, SearchRequest
from ..service import PlanningSearchService
from .adapters import adapter_for
from .contracts import (
    EVIDENCE_LEVEL,
    RESULT_SCHEMA_VERSION,
    FrozenBenchmarkSuite,
    FrozenScenario,
    ReplayOutcome,
    canonical_json,
    payload_digest,
)


GREEDY = "greedy_l1"
PUBLIC_NO_L3 = "l1_l2_no_l3"
PUBLIC_SEARCH = "l1_l2_conditional_l3"


def run_suite(suite: FrozenBenchmarkSuite) -> dict[str, Any]:
    greedy_records = tuple(_run_scenario(suite, scenario, GREEDY) for scenario in suite.scenarios)
    no_l3_records = tuple(_run_scenario(suite, scenario, PUBLIC_NO_L3) for scenario in suite.scenarios)
    public_records = tuple(_run_scenario(suite, scenario, PUBLIC_SEARCH) for scenario in suite.scenarios)
    greedy_metrics = _aggregate(greedy_records)
    no_l3_metrics = _aggregate(no_l3_records)
    public_metrics = _aggregate(public_records)
    comparison = _comparison(suite.scenarios, greedy_records, public_records, greedy_metrics, public_metrics)
    return {
        "suite_id": suite.suite_id,
        "domain": suite.domain,
        "evidence_level": suite.evidence_level,
        "production_benefit_claimed": False,
        "claim_scope": suite.claim_scope,
        "fixture": {
            "name": suite.fixture_name,
            "sha256": suite.fixture_digest,
            "scenario_count": len(suite.scenarios),
        },
        "configured_budget_shared_by_strategies": suite.budget.to_dict(),
        "logical_latency_model_ms_shared_by_strategies": dict(suite.latency_model_ms),
        "strategies": {
            GREEDY: {
                "description": "Iterative deterministic L1 prior selection to terminal/depth/budget; no L2 or L3.",
                "metrics": greedy_metrics,
                "scenarios": list(greedy_records),
            },
            PUBLIC_NO_L3: {
                "description": "Iterative common L1 plus frozen L2 ranking, with L3 disabled as an ablation.",
                "metrics": no_l3_metrics,
                "scenarios": list(no_l3_records),
            },
            PUBLIC_SEARCH: {
                "description": "Common PlanningSearchService with frozen L2 scores and conditional pure L3 MCTS.",
                "metrics": public_metrics,
                "scenarios": list(public_records),
            },
        },
        "comparison": comparison,
        "l3_ablation": {
            **_metric_delta(no_l3_metrics, public_metrics),
            "baseline": PUBLIC_NO_L3,
            "candidate": PUBLIC_SEARCH,
            "interpretation": "This synthetic delta isolates conditional L3 from the frozen L1+L2 arm.",
        },
    }


def build_artifact(suite_paths: Sequence[str | Path]) -> dict[str, Any]:
    suites = tuple(FrozenBenchmarkSuite.load(path) for path in suite_paths)
    domains = {suite.domain for suite in suites}
    if len(suites) != 2 or domains != {"code", "research"}:
        raise ValueError("artifact requires exactly the Code and Research shadow domains")
    suite_results = [run_suite(suite) for suite in sorted(suites, key=lambda item: item.domain)]
    greedy_records = _all_records(suite_results, GREEDY)
    no_l3_records = _all_records(suite_results, PUBLIC_NO_L3)
    public_records = _all_records(suite_results, PUBLIC_SEARCH)
    greedy_metrics = _aggregate(greedy_records)
    no_l3_metrics = _aggregate(no_l3_records)
    public_metrics = _aggregate(public_records)
    artifact: dict[str, Any] = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "artifact_id": "gr02a_planning_search_code_research_shadow_v1",
        "evidence_level": EVIDENCE_LEVEL,
        "deterministic": True,
        "production_benefit_claimed": False,
        "claim_scope": "Synthetic frozen Code and Research scenarios only; this is not production efficacy evidence.",
        "limitations": [
            "All transitions, rewards, cognitive scores, and validations are fixture-defined simulations.",
            "No live repository, external research source, model call, user workload, or production side effect is exercised.",
            "Positive deltas demonstrate algorithm behavior on declared local-optimum traps, not expected production uplift.",
        ],
        "strategy_contract": {
            "baseline": GREEDY,
            "ablation": PUBLIC_NO_L3,
            "candidate": PUBLIC_SEARCH,
            "budget_policy": "The configured SearchBudget is identical per scenario; actual consumed cost is reported.",
        },
        "metric_definitions": {
            "success_rate": "Successful terminal scenarios divided by scenario count.",
            "validation_pass_rate": "Fixture validation-pass terminals divided by scenario count.",
            "invalid_action_rate": "Oracle-invalid actions on the selected path divided by selected action count.",
                "cost": "Actual candidates, expansions, virtual transitions, and rollouts consumed under the shared limits.",
                "logical_latency_ms": "Deterministic sum of consumed counters multiplied by the frozen per-suite latency model; wall clock is excluded.",
            "trace_digest": "SHA-256 of deterministic trace data; wall-clock time and timestamps are excluded.",
        },
        "suites": suite_results,
        "overall": {
            GREEDY: greedy_metrics,
            PUBLIC_NO_L3: no_l3_metrics,
            PUBLIC_SEARCH: public_metrics,
            "baseline_to_candidate": _metric_delta(greedy_metrics, public_metrics),
            "l3_ablation": _metric_delta(no_l3_metrics, public_metrics),
        },
    }
    artifact["artifact_payload_sha256"] = payload_digest(artifact)
    return artifact


def default_suite_paths() -> tuple[Path, Path]:
    root = Path(__file__).resolve().parents[3]
    fixture_root = root / "tests" / "fixtures" / "planning_search_shadow"
    return fixture_root / "code_v1.json", fixture_root / "research_v1.json"


def _run_scenario(
    suite: FrozenBenchmarkSuite,
    scenario: FrozenScenario,
    strategy: str,
) -> dict[str, Any]:
    adapter = adapter_for(scenario)
    if strategy in {GREEDY, PUBLIC_NO_L3}:
        return _run_iterative_scenario(suite, scenario, strategy)
    if strategy == PUBLIC_SEARCH:
        service = PlanningSearchService(
            expander=adapter,
            cognitive_ranker=adapter,
            transition=adapter,
            reward_model=adapter,
            mcts_condition=adapter,
            clock_ms=lambda: 0.0,
        )
        mode = MCTSMode.AUTO
    else:
        raise ValueError(f"unknown strategy: {strategy}")
    result = service.search(
        SearchRequest(
            request_id=f"{suite.suite_id}:{scenario.scenario_id}:{strategy}",
            initial_state=adapter.initial_virtual_state,
            budget=suite.budget,
            context={
                "suite_id": suite.suite_id,
                "scenario_id": scenario.scenario_id,
                "domain": scenario.domain,
                "strategy": strategy,
                "evidence_level": EVIDENCE_LEVEL,
            },
            mcts_mode=mode,
        )
    )
    outcome = adapter.replay(result.selected_path)
    selected_path = [candidate.candidate_id for candidate in result.selected_path]
    cost = result.counters.to_dict()
    return {
        "scenario_id": scenario.scenario_id,
        "title": scenario.title,
        "local_optimum_trap": scenario.trap_explanation,
        "strategy": strategy,
        "status": result.status.value,
        "stop_reason": result.stop_reason,
        "selected_path": selected_path,
        "selected_path_explanations": _path_explanations(scenario, result.selected_path),
        "outcome": outcome.to_dict(),
        "invalid_action_rate": _rate(outcome.invalid_action_count, outcome.selected_action_count),
        "mcts_used": result.mcts_used,
        "cost": cost,
        "logical_latency_ms": _logical_latency_ms(cost, suite.latency_model_ms),
        "trace_digest": result.trace_digest,
        "side_effects_executed": result.side_effects_executed,
    }


def _run_iterative_scenario(
    suite: FrozenBenchmarkSuite,
    scenario: FrozenScenario,
    strategy: str,
) -> dict[str, Any]:
    adapter = adapter_for(scenario)
    state: Mapping[str, Any] = adapter.initial_virtual_state
    selected_path: list[SearchCandidate] = []
    step_trace_digests: list[str] = []
    cost = {"candidates": 0, "expansions": 0, "transitions": 0, "rollouts": 0}
    stop_reason = "max_depth_reached"
    status = "empty"
    for depth in range(suite.budget.max_depth):
        node_id = str(state.get("node_id") or "")
        if not scenario.expansions.get(node_id, ()):
            stop_reason = "terminal_state"
            break
        remaining = _remaining_budget(suite.budget, cost, depth)
        if remaining.max_candidates <= 0 or remaining.max_expansions <= 0:
            stop_reason = "budget_exhausted"
            break
        service = PlanningSearchService(
            expander=adapter,
            cognitive_ranker=adapter if strategy == PUBLIC_NO_L3 else None,
            clock_ms=lambda: 0.0,
        )
        result = service.search(
            SearchRequest(
                request_id=f"{suite.suite_id}:{scenario.scenario_id}:{strategy}:step{depth + 1}",
                initial_state=state,
                budget=remaining,
                context={
                    "suite_id": suite.suite_id,
                    "scenario_id": scenario.scenario_id,
                    "domain": scenario.domain,
                    "strategy": strategy,
                    "step": depth + 1,
                    "evidence_level": EVIDENCE_LEVEL,
                },
                mcts_mode=MCTSMode.NEVER,
            )
        )
        step_trace_digests.append(result.trace_digest)
        for name, value in result.counters.to_dict().items():
            cost[name] += int(value)
        if result.selected_candidate is None:
            stop_reason = result.stop_reason
            break
        if cost["transitions"] >= suite.budget.max_transitions:
            stop_reason = "transition_budget_exhausted"
            break
        selected = result.selected_candidate
        next_path = tuple([*selected_path, selected])
        state = adapter.transition(state=state, candidate=selected, path=next_path, context={})
        selected_path.append(selected)
        cost["transitions"] += 1
        status = result.status.value
    else:
        stop_reason = "max_depth_reached"
    final_node = str(state.get("node_id") or "")
    if not scenario.expansions.get(final_node, ()):
        stop_reason = "terminal_state"
    outcome = adapter.replay(selected_path)
    trace_digest = payload_digest(
        {
            "scenario_id": scenario.scenario_id,
            "strategy": strategy,
            "step_trace_digests": step_trace_digests,
            "selected_path": [candidate.candidate_id for candidate in selected_path],
            "stop_reason": stop_reason,
            "cost": cost,
        }
    )
    return {
        "scenario_id": scenario.scenario_id,
        "title": scenario.title,
        "local_optimum_trap": scenario.trap_explanation,
        "strategy": strategy,
        "status": status,
        "stop_reason": stop_reason,
        "selected_path": [candidate.candidate_id for candidate in selected_path],
        "selected_path_explanations": _path_explanations(scenario, selected_path),
        "outcome": outcome.to_dict(),
        "invalid_action_rate": _rate(outcome.invalid_action_count, outcome.selected_action_count),
        "mcts_used": False,
        "cost": cost,
        "logical_latency_ms": _logical_latency_ms(cost, suite.latency_model_ms),
        "trace_digest": trace_digest,
        "step_trace_digests": step_trace_digests,
        "side_effects_executed": False,
    }


def _aggregate(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    values = tuple(records)
    count = len(values)
    successes = sum(bool(item["outcome"]["success"]) for item in values)
    validation_passes = sum(bool(item["outcome"]["validation_passed"]) for item in values)
    invalid = sum(int(item["outcome"]["invalid_action_count"]) for item in values)
    actions = sum(int(item["outcome"]["selected_action_count"]) for item in values)
    cost_fields = ("candidates", "expansions", "transitions", "rollouts")
    totals = {name: sum(int(item["cost"][name]) for item in values) for name in cost_fields}
    latency_total = sum(int(item["logical_latency_ms"]) for item in values)
    trace_digests = {
        str(item["scenario_id"]): str(item["trace_digest"])
        for item in sorted(values, key=lambda value: str(value["scenario_id"]))
    }
    return {
        "scenario_count": count,
        "success_count": successes,
        "success_rate": _rate(successes, count),
        "validation_pass_count": validation_passes,
        "validation_pass_rate": _rate(validation_passes, count),
        "invalid_action_count": invalid,
        "selected_action_count": actions,
        "invalid_action_rate": _rate(invalid, actions),
        "cost_total": totals,
        "cost_average_per_scenario": {name: _rate(total, count) for name, total in totals.items()},
        "logical_latency_ms_total": latency_total,
        "logical_latency_ms_average_per_scenario": _rate(latency_total, count),
        "trace_digests": trace_digests,
        "aggregate_trace_digest": payload_digest(trace_digests),
        "mcts_scenario_count": sum(bool(item["mcts_used"]) for item in values),
        "side_effects_executed": any(bool(item["side_effects_executed"]) for item in values),
    }


def _comparison(
    scenarios: Sequence[FrozenScenario],
    greedy_records: Sequence[dict[str, Any]],
    public_records: Sequence[dict[str, Any]],
    greedy_metrics: dict[str, Any],
    public_metrics: dict[str, Any],
) -> dict[str, Any]:
    greedy_by_id = {item["scenario_id"]: item for item in greedy_records}
    public_by_id = {item["scenario_id"]: item for item in public_records}
    explanations: list[dict[str, Any]] = []
    for scenario in scenarios:
        greedy = greedy_by_id[scenario.scenario_id]
        public = public_by_id[scenario.scenario_id]
        explanations.append(
            {
                "scenario_id": scenario.scenario_id,
                "trap": scenario.trap_explanation,
                "greedy_path": greedy["selected_path"],
                "public_path": public["selected_path"],
                "success_improved": (
                    not bool(greedy["outcome"]["success"])
                    and bool(public["outcome"]["success"])
                ),
                "validation_improved": (
                    not bool(greedy["outcome"]["validation_passed"])
                    and bool(public["outcome"]["validation_passed"])
                ),
                "explanation": "L3 values the validated terminal path above the higher-prior one-step decoy.",
            }
        )
    delta = _metric_delta(greedy_metrics, public_metrics)
    positive = (
        delta["success_rate_gain"] > 0
        and delta["validation_pass_rate_gain"] > 0
        and delta["invalid_action_rate_reduction"] >= 0
    )
    return {
        **delta,
        "explainable_positive_gain": positive,
        "scenario_explanations": explanations,
        "interpretation": (
            "Positive gain is limited to the frozen simulation traps above and is not a production benefit claim."
        ),
    }


def _metric_delta(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "success_rate_gain": _rounded(candidate["success_rate"] - baseline["success_rate"]),
        "validation_pass_rate_gain": _rounded(
            candidate["validation_pass_rate"] - baseline["validation_pass_rate"]
        ),
        "invalid_action_rate_reduction": _rounded(
            baseline["invalid_action_rate"] - candidate["invalid_action_rate"]
        ),
        "cost_delta_total": {
            key: int(candidate["cost_total"][key]) - int(baseline["cost_total"][key])
            for key in ("candidates", "expansions", "transitions", "rollouts")
        },
        "logical_latency_ms_delta_total": (
            int(candidate["logical_latency_ms_total"])
            - int(baseline["logical_latency_ms_total"])
        ),
    }


def _all_records(suites: Sequence[dict[str, Any]], strategy: str) -> tuple[dict[str, Any], ...]:
    return tuple(
        record
        for suite in suites
        for record in suite["strategies"][strategy]["scenarios"]
    )


def _path_explanations(
    scenario: FrozenScenario,
    path: Sequence[SearchCandidate],
) -> list[str]:
    return [scenario.actions[candidate.candidate_id].explanation for candidate in path]


def _remaining_budget(budget: SearchBudget, used: Mapping[str, int], depth: int) -> SearchBudget:
    return SearchBudget(
        max_candidates=max(0, budget.max_candidates - int(used["candidates"])),
        max_expansions=max(0, budget.max_expansions - int(used["expansions"])),
        max_transitions=max(0, budget.max_transitions - int(used["transitions"])),
        max_rollouts=max(0, budget.max_rollouts - int(used["rollouts"])),
        max_depth=max(0, budget.max_depth - depth),
        branching_factor=budget.branching_factor,
        deadline_ms=budget.deadline_ms,
        exploration=budget.exploration,
        mcts_score_gap=budget.mcts_score_gap,
    )


def _rate(numerator: int, denominator: int) -> float:
    return _rounded(float(numerator) / float(denominator)) if denominator else 0.0


def _logical_latency_ms(
    cost: Mapping[str, int],
    latency_model_ms: Mapping[str, int],
) -> int:
    return sum(int(cost[name]) * int(latency_model_ms[name]) for name in latency_model_ms)


def _rounded(value: float) -> float:
    return round(float(value), 6)


def main(argv: Sequence[str] | None = None) -> int:
    defaults = default_suite_paths()
    parser = argparse.ArgumentParser(description="Run deterministic PlanningSearch shadow benchmarks")
    parser.add_argument("--code-fixture", type=Path, default=defaults[0])
    parser.add_argument("--research-fixture", type=Path, default=defaults[1])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    artifact = build_artifact((args.code_fixture, args.research_fixture))
    rendered = canonical_json(artifact, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
