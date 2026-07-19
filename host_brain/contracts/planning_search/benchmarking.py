"""Public contracts for paired, isolated PlanningSearch shadow benchmarks.

The contracts deliberately represent evidence collection, not promotion.  A
benchmark implementation may run real verification inside an isolated
workspace, but it can never grant production influence to a selected search
path.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Protocol, Sequence

from .contracts import SearchBudget, SearchResult


DOMAIN_BENCHMARK_SCHEMA = "natsume.planning-search.domain-benchmark.v1"
DOMAIN_BENCHMARK_MODE = "SHADOW_ONLY"
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


class DomainBenchmarkContractError(ValueError):
    """Benchmark evidence is not safe to compare or persist."""


@dataclass(frozen=True, slots=True)
class DomainBenchmarkWorkload:
    """One preregistered workload with fixed baseline, verifier, and isolation."""

    workload_id: str
    domain: str
    workload_ref: str
    baseline_ref: str
    verifier_refs: Sequence[str]
    isolation: Mapping[str, Any]
    budget: SearchBudget
    cohort_key: str
    workload_digest: str = ""

    def __post_init__(self) -> None:
        workload_id = _required_text(self.workload_id, "workload_id", 240)
        domain = _required_text(self.domain, "domain", 128).lower()
        workload_ref = _governed_ref(self.workload_ref, "workload_ref")
        baseline_ref = _governed_ref(self.baseline_ref, "baseline_ref")
        refs = _refs(self.verifier_refs, "verifier_refs", allow_empty=False)
        if not isinstance(self.isolation, Mapping) or not self.isolation:
            raise DomainBenchmarkContractError("isolation must be a non-empty object")
        isolation = _freeze_json(_json_copy(dict(self.isolation)))
        cohort_key = _required_text(self.cohort_key, "cohort_key", 240)
        if not isinstance(self.budget, SearchBudget):
            raise DomainBenchmarkContractError("budget must be SearchBudget")
        identity = {
            "schema": DOMAIN_BENCHMARK_SCHEMA,
            "workload_id": workload_id,
            "domain": domain,
            "workload_ref": workload_ref,
            "baseline_ref": baseline_ref,
            "verifier_refs": list(refs),
            "isolation": _thaw_json(isolation),
            "budget": self.budget.to_dict(),
            "cohort_key": cohort_key,
        }
        digest = _digest(identity)
        supplied = str(self.workload_digest or "").strip().lower()
        if supplied and supplied != digest:
            raise DomainBenchmarkContractError("workload_digest does not bind workload identity")
        object.__setattr__(self, "workload_id", workload_id)
        object.__setattr__(self, "domain", domain)
        object.__setattr__(self, "workload_ref", workload_ref)
        object.__setattr__(self, "baseline_ref", baseline_ref)
        object.__setattr__(self, "verifier_refs", refs)
        object.__setattr__(self, "isolation", isolation)
        object.__setattr__(self, "cohort_key", cohort_key)
        object.__setattr__(self, "workload_digest", digest)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": DOMAIN_BENCHMARK_SCHEMA,
            "workload_id": self.workload_id,
            "domain": self.domain,
            "workload_ref": self.workload_ref,
            "baseline_ref": self.baseline_ref,
            "verifier_refs": list(self.verifier_refs),
            "isolation": _thaw_json(self.isolation),
            "budget": self.budget.to_dict(),
            "cohort_key": self.cohort_key,
            "workload_digest": self.workload_digest,
        }


@dataclass(frozen=True, slots=True)
class BenchmarkArm:
    """A baseline or selected-search arm, without execution authority."""

    arm_id: str
    selected_candidate_id: str
    selected_path_candidate_ids: Sequence[str]
    trace_digest: str
    counters: Mapping[str, int]
    virtual_state_schema: str = ""
    virtual_state_digest: str = ""

    def __post_init__(self) -> None:
        arm_id = _required_text(self.arm_id, "arm_id", 128)
        candidate = _required_text(self.selected_candidate_id, "selected_candidate_id", 512)
        path = _text_sequence(
            self.selected_path_candidate_ids,
            "selected_path_candidate_ids",
            allow_empty=False,
            item_limit=512,
        )
        if path[0] != candidate:
            raise DomainBenchmarkContractError(
                "selected_candidate_id must be the first selected path candidate"
            )
        trace_digest = _digest_text(self.trace_digest, "trace_digest")
        counters = _freeze_json(_counters(self.counters))
        schema = str(self.virtual_state_schema or "").strip()
        state_digest = str(self.virtual_state_digest or "").strip().lower()
        if bool(schema) != bool(state_digest):
            raise DomainBenchmarkContractError(
                "virtual state schema and digest must be supplied together"
            )
        if state_digest:
            _digest_text(state_digest, "virtual_state_digest")
        object.__setattr__(self, "arm_id", arm_id)
        object.__setattr__(self, "selected_candidate_id", candidate)
        object.__setattr__(self, "selected_path_candidate_ids", path)
        object.__setattr__(self, "trace_digest", trace_digest)
        object.__setattr__(self, "counters", counters)
        object.__setattr__(self, "virtual_state_schema", schema)
        object.__setattr__(self, "virtual_state_digest", state_digest)

    @classmethod
    def from_search_result(cls, arm_id: str, result: SearchResult) -> "BenchmarkArm":
        if result.selected_candidate is None or not result.selected_path:
            raise DomainBenchmarkContractError("benchmark arm requires a selected search result")
        return cls(
            arm_id=arm_id,
            selected_candidate_id=result.selected_candidate.candidate_id,
            selected_path_candidate_ids=tuple(
                candidate.candidate_id for candidate in result.selected_path
            ),
            trace_digest=result.trace_digest,
            counters=result.counters.to_dict(),
            virtual_state_schema=result.virtual_state_schema,
            virtual_state_digest=result.virtual_state_digest,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm_id": self.arm_id,
            "selected_candidate_id": self.selected_candidate_id,
            "selected_path_candidate_ids": list(self.selected_path_candidate_ids),
            "trace_digest": self.trace_digest,
            "counters": _thaw_json(self.counters),
            "virtual_state": (
                {
                    "schema": self.virtual_state_schema,
                    "digest": self.virtual_state_digest,
                }
                if self.virtual_state_digest
                else None
            ),
        }


@dataclass(frozen=True, slots=True)
class BenchmarkArmOutcome:
    """Verifier-bound observed outcome for exactly one isolated benchmark arm."""

    arm_id: str
    verifier_receipt_refs: Sequence[str]
    success: bool | None
    latency_ms: int | None
    cost: Mapping[str, int]
    workspace_diff_digest: str = ""
    executed: bool = False

    def __post_init__(self) -> None:
        arm_id = _required_text(self.arm_id, "arm_id", 128)
        refs = _refs(self.verifier_receipt_refs, "verifier_receipt_refs", allow_empty=True)
        if self.executed and not refs:
            raise DomainBenchmarkContractError(
                "executed benchmark arm requires verifier receipt refs"
            )
        if self.success is not None and not self.executed:
            raise DomainBenchmarkContractError("unexecuted benchmark arm cannot claim success")
        if self.success is not None and not isinstance(self.success, bool):
            raise DomainBenchmarkContractError("success must be boolean or null")
        if self.latency_ms is not None and (
            isinstance(self.latency_ms, bool)
            or not isinstance(self.latency_ms, int)
            or self.latency_ms < 0
        ):
            raise DomainBenchmarkContractError("latency_ms must be a non-negative integer")
        cost = _freeze_json(_counters(self.cost))
        digest = str(self.workspace_diff_digest or "").strip().lower()
        if digest:
            _digest_text(digest, "workspace_diff_digest")
        object.__setattr__(self, "arm_id", arm_id)
        object.__setattr__(self, "verifier_receipt_refs", refs)
        object.__setattr__(self, "latency_ms", None if self.latency_ms is None else int(self.latency_ms))
        object.__setattr__(self, "cost", cost)
        object.__setattr__(self, "workspace_diff_digest", digest)

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm_id": self.arm_id,
            "verifier_receipt_refs": list(self.verifier_receipt_refs),
            "success": self.success,
            "latency_ms": self.latency_ms,
            "cost": _thaw_json(self.cost),
            "workspace_diff_digest": self.workspace_diff_digest or None,
            "executed": self.executed,
        }


@dataclass(frozen=True, slots=True)
class DomainBenchmarkReport:
    """One paired shadow report; production influence is structurally impossible."""

    workload: DomainBenchmarkWorkload
    baseline: BenchmarkArm
    selected: BenchmarkArm
    baseline_outcome: BenchmarkArmOutcome | None = None
    selected_outcome: BenchmarkArmOutcome | None = None
    mode: str = DOMAIN_BENCHMARK_MODE
    production_influence_enabled: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.workload, DomainBenchmarkWorkload):
            raise DomainBenchmarkContractError("workload is invalid")
        if not isinstance(self.baseline, BenchmarkArm) or not isinstance(self.selected, BenchmarkArm):
            raise DomainBenchmarkContractError("benchmark arms are invalid")
        if self.baseline.arm_id == self.selected.arm_id:
            raise DomainBenchmarkContractError("baseline and selected arm ids must differ")
        if (
            self.baseline.virtual_state_schema != self.selected.virtual_state_schema
            or self.baseline.virtual_state_digest != self.selected.virtual_state_digest
        ):
            raise DomainBenchmarkContractError(
                "paired benchmark arms must bind the same virtual state provenance"
            )
        for outcome, arm in (
            (self.baseline_outcome, self.baseline),
            (self.selected_outcome, self.selected),
        ):
            if outcome is not None:
                if not isinstance(outcome, BenchmarkArmOutcome) or outcome.arm_id != arm.arm_id:
                    raise DomainBenchmarkContractError("benchmark outcome does not bind its arm")
        if str(self.mode or "").strip().upper() != DOMAIN_BENCHMARK_MODE:
            raise DomainBenchmarkContractError("domain benchmark mode must be SHADOW_ONLY")
        if self.production_influence_enabled:
            raise DomainBenchmarkContractError("domain benchmark cannot enable production influence")
        object.__setattr__(self, "mode", DOMAIN_BENCHMARK_MODE)
        object.__setattr__(self, "production_influence_enabled", False)

    @property
    def report_digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": DOMAIN_BENCHMARK_SCHEMA,
            "workload": self.workload.to_dict(),
            "baseline": self.baseline.to_dict(),
            "selected": self.selected.to_dict(),
            "baseline_outcome": (
                self.baseline_outcome.to_dict() if self.baseline_outcome else None
            ),
            "selected_outcome": (
                self.selected_outcome.to_dict() if self.selected_outcome else None
            ),
            "mode": DOMAIN_BENCHMARK_MODE,
            "production_influence_enabled": False,
        }


class DomainBenchmarkPort(Protocol):
    """Run a fixed paired workload in isolation and return shadow evidence."""

    def run_shadow(
        self,
        *,
        workload: DomainBenchmarkWorkload,
        baseline: BenchmarkArm,
        selected: BenchmarkArm,
    ) -> DomainBenchmarkReport: ...


def _required_text(value: Any, field_name: str, limit: int) -> str:
    if not isinstance(value, str):
        raise DomainBenchmarkContractError(f"{field_name} must be text")
    text = value.strip()
    if not text or len(text) > limit:
        raise DomainBenchmarkContractError(f"{field_name} is invalid")
    return text


def _governed_ref(value: Any, field_name: str) -> str:
    text = _required_text(value, field_name, 1_024)
    if "://" not in text:
        raise DomainBenchmarkContractError(f"{field_name} must be a governed ref")
    return text


def _refs(values: Sequence[str], field_name: str, *, allow_empty: bool) -> tuple[str, ...]:
    return _text_sequence(values, field_name, allow_empty=allow_empty, item_limit=1_024, refs=True)


def _text_sequence(
    values: Sequence[str],
    field_name: str,
    *,
    allow_empty: bool,
    item_limit: int,
    refs: bool = False,
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, Sequence):
        raise DomainBenchmarkContractError(f"{field_name} must be an explicit sequence")
    output: list[str] = []
    for index, value in enumerate(values):
        text = _required_text(value, f"{field_name}[{index}]", item_limit)
        if refs and "://" not in text:
            raise DomainBenchmarkContractError(f"{field_name}[{index}] must be a governed ref")
        if text not in output:
            output.append(text)
    if not output and not allow_empty:
        raise DomainBenchmarkContractError(f"{field_name} must not be empty")
    return tuple(output)


def _counters(value: Mapping[str, int]) -> dict[str, int]:
    if not isinstance(value, Mapping):
        raise DomainBenchmarkContractError("cost/counters must be an object")
    output: dict[str, int] = {}
    for key, raw in value.items():
        name = _required_text(str(key), "counter name", 128)
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
            raise DomainBenchmarkContractError(f"counter {name} must be a non-negative integer")
        output[name] = int(raw)
    return output


def _digest_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip().lower()
    if not _DIGEST_RE.fullmatch(text):
        raise DomainBenchmarkContractError(f"{field_name} must be a lowercase sha256 digest")
    return text


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    ).hexdigest()


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False))


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({str(key): _freeze_json(child) for key, child in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(child) for child in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(child) for child in value]
    return value


__all__ = [
    "DOMAIN_BENCHMARK_MODE",
    "DOMAIN_BENCHMARK_SCHEMA",
    "BenchmarkArm",
    "BenchmarkArmOutcome",
    "DomainBenchmarkContractError",
    "DomainBenchmarkPort",
    "DomainBenchmarkReport",
    "DomainBenchmarkWorkload",
]
