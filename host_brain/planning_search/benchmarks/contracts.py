from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from host_brain.contracts.planning_search.contracts import SearchBudget


EVIDENCE_LEVEL = "SIMULATION_SHADOW"
SCENARIO_SCHEMA_VERSION = "planning_search.shadow_scenarios.v1"
RESULT_SCHEMA_VERSION = "planning_search.shadow_benchmark_result.v1"
LATENCY_DIMENSIONS = ("candidates", "expansions", "transitions", "rollouts")


@dataclass(frozen=True, slots=True)
class FrozenAction:
    action_id: str
    from_state: str
    to_state: str
    prior_score: float
    cognitive_score: float
    valid: bool
    explanation: str

    @classmethod
    def from_dict(cls, action_id: str, value: Mapping[str, Any]) -> "FrozenAction":
        action = cls(
            action_id=str(action_id),
            from_state=str(value["from_state"]),
            to_state=str(value["to_state"]),
            prior_score=float(value["prior_score"]),
            cognitive_score=float(value["cognitive_score"]),
            valid=bool(value["valid"]),
            explanation=str(value["explanation"]),
        )
        for name, score in (
            ("prior_score", action.prior_score),
            ("cognitive_score", action.cognitive_score),
        ):
            if not math.isfinite(score) or not 0.0 <= score <= 1.0:
                raise ValueError(f"{action_id}: {name} must be finite and in [0, 1]")
        return action


@dataclass(frozen=True, slots=True)
class FrozenScenario:
    scenario_id: str
    domain: str
    title: str
    trap_explanation: str
    requires_lookahead: bool
    initial_state: str
    states: Mapping[str, Mapping[str, Any]]
    actions: Mapping[str, FrozenAction]
    expansions: Mapping[str, tuple[str, ...]]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], *, expected_domain: str) -> "FrozenScenario":
        scenario_id = str(value["scenario_id"])
        domain = str(value["domain"])
        if domain != expected_domain:
            raise ValueError(f"{scenario_id}: domain {domain!r} != suite domain {expected_domain!r}")
        states = _json_copy(dict(value["states"]))
        actions = {
            str(action_id): FrozenAction.from_dict(str(action_id), action)
            for action_id, action in dict(value["actions"]).items()
        }
        expansions = {
            str(state_id): tuple(str(action_id) for action_id in action_ids)
            for state_id, action_ids in dict(value["expansions"]).items()
        }
        scenario = cls(
            scenario_id=scenario_id,
            domain=domain,
            title=str(value["title"]),
            trap_explanation=str(value["trap_explanation"]),
            requires_lookahead=bool(value["requires_lookahead"]),
            initial_state=str(value["initial_state"]),
            states=states,
            actions=actions,
            expansions=expansions,
        )
        scenario.validate()
        return scenario

    def validate(self) -> None:
        if self.initial_state not in self.states:
            raise ValueError(f"{self.scenario_id}: initial state is missing")
        if len(self.expansions.get(self.initial_state, ())) < 2:
            raise ValueError(f"{self.scenario_id}: local-optimum benchmark needs at least two root actions")
        for state_id, action_ids in self.expansions.items():
            if state_id not in self.states:
                raise ValueError(f"{self.scenario_id}: expansion references unknown state {state_id}")
            for action_id in action_ids:
                action = self.actions.get(action_id)
                if action is None:
                    raise ValueError(f"{self.scenario_id}: unknown action {action_id}")
                if action.from_state != state_id:
                    raise ValueError(f"{self.scenario_id}: action {action_id} has mismatched from_state")
                if action.to_state not in self.states:
                    raise ValueError(f"{self.scenario_id}: action {action_id} targets unknown state")
        referenced = [action_id for action_ids in self.expansions.values() for action_id in action_ids]
        if set(referenced) != set(self.actions) or len(referenced) != len(set(referenced)):
            raise ValueError(f"{self.scenario_id}: every action must be referenced exactly once")
        for state_id, state in self.states.items():
            reward = float(state.get("reward", 0.0))
            if not math.isfinite(reward):
                raise ValueError(f"{self.scenario_id}: state {state_id} reward must be finite")


@dataclass(frozen=True, slots=True)
class FrozenBenchmarkSuite:
    suite_id: str
    domain: str
    evidence_level: str
    production_benefit_claimed: bool
    claim_scope: str
    budget: SearchBudget
    latency_model_ms: Mapping[str, int]
    scenarios: tuple[FrozenScenario, ...]
    fixture_name: str
    fixture_digest: str

    @classmethod
    def load(cls, path: str | Path) -> "FrozenBenchmarkSuite":
        source = Path(path)
        raw = json.loads(source.read_text(encoding="utf-8"))
        if str(raw.get("schema_version")) != SCENARIO_SCHEMA_VERSION:
            raise ValueError(f"unsupported scenario schema: {raw.get('schema_version')}")
        if str(raw.get("evidence_level")) != EVIDENCE_LEVEL:
            raise ValueError("shadow benchmark fixture must use SIMULATION_SHADOW evidence")
        if bool(raw.get("production_benefit_claimed")):
            raise ValueError("simulation fixtures must not claim production benefit")
        domain = str(raw["domain"])
        scenarios = tuple(
            FrozenScenario.from_dict(item, expected_domain=domain)
            for item in raw["scenarios"]
        )
        if len(scenarios) < 4:
            raise ValueError("each shadow domain requires at least four scenarios")
        if sum(scenario.requires_lookahead for scenario in scenarios) < 4:
            raise ValueError("each shadow domain requires at least four declared lookahead traps")
        ids = [scenario.scenario_id for scenario in scenarios]
        if len(ids) != len(set(ids)):
            raise ValueError("scenario ids must be unique")
        raw_latency = raw.get("latency_model_ms")
        if not isinstance(raw_latency, Mapping) or set(raw_latency) != set(LATENCY_DIMENSIONS):
            raise ValueError(
                "latency_model_ms must explicitly define candidates, expansions, transitions and rollouts"
            )
        latency_model: dict[str, int] = {}
        for dimension in LATENCY_DIMENSIONS:
            value = raw_latency[dimension]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"latency_model_ms.{dimension} must be a non-negative integer")
            latency_model[dimension] = value
        return cls(
            suite_id=str(raw["suite_id"]),
            domain=domain,
            evidence_level=EVIDENCE_LEVEL,
            production_benefit_claimed=False,
            claim_scope=str(raw["claim_scope"]),
            budget=SearchBudget(**dict(raw["budget"])),
            latency_model_ms=latency_model,
            scenarios=scenarios,
            fixture_name=source.name,
            fixture_digest=_digest(raw),
        )


@dataclass(frozen=True, slots=True)
class ReplayOutcome:
    success: bool
    validation_passed: bool
    invalid_action_count: int
    selected_action_count: int
    final_state: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "validation_passed": self.validation_passed,
            "invalid_action_count": self.invalid_action_count,
            "selected_action_count": self.selected_action_count,
            "final_state": self.final_state,
        }


def canonical_json(value: Any, *, indent: int | None = None) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":") if indent is None else None,
        indent=indent,
    )


def payload_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _digest(value: Any) -> str:
    return payload_digest(_json_copy(value))


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False))
