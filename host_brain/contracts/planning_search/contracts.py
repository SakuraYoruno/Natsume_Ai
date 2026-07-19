from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence
from types import MappingProxyType


VIRTUAL_STATE_SNAPSHOT_SCHEMA = "natsume.planning-search.virtual-state.v1"


@dataclass(frozen=True, slots=True)
class PlanningGoal:
    """One immutable, domain-scoped planning objective.

    A goal carries only the statement of work and references to context that
    was frozen before search begins.  It deliberately has no executor,
    authority object, credential, or mutable runtime handle.
    """

    goal_id: str
    domain: str
    objective: str
    success_criteria: tuple[str, ...]
    frozen_context_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        goal_id = _required_text(self.goal_id, field_name="goal_id", max_length=240)
        domain = _required_text(self.domain, field_name="domain", max_length=128).lower()
        objective = _required_text(self.objective, field_name="objective", max_length=4_000)
        criteria = _normalized_text_sequence(
            self.success_criteria,
            field_name="success_criteria",
            allow_empty=False,
            item_limit=4_000,
        )
        refs = _normalized_text_sequence(
            self.frozen_context_refs,
            field_name="frozen_context_refs",
            allow_empty=True,
            item_limit=1_024,
        )
        if any("://" not in ref for ref in refs):
            raise ValueError("frozen_context_refs must contain governed refs")
        object.__setattr__(self, "goal_id", goal_id)
        object.__setattr__(self, "domain", domain)
        object.__setattr__(self, "objective", objective)
        object.__setattr__(self, "success_criteria", criteria)
        object.__setattr__(self, "frozen_context_refs", refs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "domain": self.domain,
            "objective": self.objective,
            "success_criteria": list(self.success_criteria),
            "frozen_context_refs": list(self.frozen_context_refs),
        }


@dataclass(frozen=True, slots=True)
class VirtualStateSnapshot:
    """Canonical, immutable state supplied to a pure planning search.

    The digest is derived locally from the canonical snapshot.  Callers cannot
    supply a digest that points to different facts, capabilities, or
    constraints.  A state transition must create a fresh snapshot rather than
    mutate this object in place.
    """

    domain: str
    capabilities: Sequence[str] = ()
    facts: Mapping[str, Any] = field(default_factory=dict)
    constraints: Mapping[str, Any] = field(default_factory=dict)
    source_refs: Sequence[str] = ()
    schema_version: str = VIRTUAL_STATE_SNAPSHOT_SCHEMA

    def __post_init__(self) -> None:
        schema = _required_text(
            self.schema_version,
            field_name="schema_version",
            max_length=256,
        )
        domain = _required_text(self.domain, field_name="domain", max_length=128).lower()
        capabilities = _normalized_text_sequence(
            self.capabilities,
            field_name="capabilities",
            allow_empty=True,
            item_limit=240,
        )
        if not isinstance(self.facts, Mapping) or not isinstance(self.constraints, Mapping):
            raise ValueError("facts and constraints must be objects")
        facts = _freeze_json(_json_copy(dict(self.facts)))
        constraints = _freeze_json(_json_copy(dict(self.constraints)))
        refs = _normalized_text_sequence(
            self.source_refs,
            field_name="source_refs",
            allow_empty=True,
            item_limit=1_024,
        )
        if any("://" not in ref for ref in refs):
            raise ValueError("source_refs must contain governed refs")
        object.__setattr__(self, "schema_version", schema)
        object.__setattr__(self, "domain", domain)
        object.__setattr__(self, "capabilities", capabilities)
        object.__setattr__(self, "facts", facts)
        object.__setattr__(self, "constraints", constraints)
        object.__setattr__(self, "source_refs", refs)

    @property
    def state_digest(self) -> str:
        encoded = json.dumps(
            self.identity_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def identity_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "domain": self.domain,
            "capabilities": list(self.capabilities),
            "facts": _thaw_json(self.facts),
            "constraints": _thaw_json(self.constraints),
            "source_refs": list(self.source_refs),
        }

    def to_dict(self) -> dict[str, Any]:
        return {"state_digest": self.state_digest, **self.identity_dict()}

    def replace(
        self,
        *,
        capabilities: Sequence[str] | None = None,
        facts: Mapping[str, Any] | None = None,
        constraints: Mapping[str, Any] | None = None,
        source_refs: Sequence[str] | None = None,
    ) -> "VirtualStateSnapshot":
        """Return a new frozen virtual state after a pure transition."""

        return VirtualStateSnapshot(
            schema_version=self.schema_version,
            domain=self.domain,
            capabilities=self.capabilities if capabilities is None else capabilities,
            facts=_thaw_json(self.facts) if facts is None else facts,
            constraints=_thaw_json(self.constraints) if constraints is None else constraints,
            source_refs=self.source_refs if source_refs is None else source_refs,
        )


class MCTSMode(str, Enum):
    """Controls whether L3 look-ahead is disabled, automatic, or mandatory."""

    NEVER = "never"
    AUTO = "auto"
    ALWAYS = "always"


class SearchStatus(str, Enum):
    SELECTED = "selected"
    EMPTY = "empty"
    DEADLINE_REACHED = "deadline_reached"
    BUDGET_EXHAUSTED = "budget_exhausted"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SearchCandidate:
    """A data-only planning candidate.

    ``payload`` is deliberately JSON-only: search candidates cannot carry an
    executor, callback, open handle, or domain object. The selected payload is
    a plan descriptor; executing it is the caller's separate responsibility.
    """

    candidate_id: str
    prior_score: float = 0.0
    payload: Mapping[str, Any] = field(default_factory=dict)
    labels: tuple[str, ...] = ()
    simulation_only: bool = True

    def __post_init__(self) -> None:
        candidate_id = str(self.candidate_id).strip()
        if not candidate_id:
            raise ValueError("candidate_id must not be empty")
        if not math.isfinite(float(self.prior_score)):
            raise ValueError("prior_score must be finite")
        normalized_payload = _json_copy(dict(self.payload))
        normalized_labels = tuple(sorted({str(item).strip() for item in self.labels if str(item).strip()}))
        object.__setattr__(self, "candidate_id", candidate_id)
        object.__setattr__(self, "prior_score", float(self.prior_score))
        object.__setattr__(self, "payload", normalized_payload)
        object.__setattr__(self, "labels", normalized_labels)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "prior_score": _rounded(self.prior_score),
            "payload": _json_copy(dict(self.payload)),
            "labels": list(self.labels),
            "simulation_only": self.simulation_only,
        }


@dataclass(frozen=True, slots=True)
class CandidateScore:
    candidate_id: str
    score: float
    reason_code: str = "cognitive_rank"

    def __post_init__(self) -> None:
        candidate_id = str(self.candidate_id).strip()
        if not candidate_id:
            raise ValueError("candidate_id must not be empty")
        if not math.isfinite(float(self.score)):
            raise ValueError("score must be finite")
        object.__setattr__(self, "candidate_id", candidate_id)
        object.__setattr__(self, "score", float(self.score))
        object.__setattr__(self, "reason_code", str(self.reason_code or "cognitive_rank"))


@dataclass(frozen=True, slots=True)
class SearchBudget:
    """One budget shared by L1 expansion, L2 ranking, and L3 MCTS.

    ``deadline_ms`` is an absolute value in the clock domain injected into the
    service (monotonic milliseconds by default), which also makes fake-clock
    deadline tests deterministic.
    """

    max_candidates: int = 64
    max_expansions: int = 32
    max_transitions: int = 64
    max_rollouts: int = 16
    max_depth: int = 3
    branching_factor: int = 5
    deadline_ms: float | None = None
    exploration: float = 1.41421356237
    mcts_score_gap: float = 0.15

    def __post_init__(self) -> None:
        integer_fields = (
            "max_candidates",
            "max_expansions",
            "max_transitions",
            "max_rollouts",
            "max_depth",
            "branching_factor",
        )
        for name in integer_fields:
            value = int(getattr(self, name))
            if value < 0:
                raise ValueError(f"{name} must be >= 0")
            object.__setattr__(self, name, value)
        if self.branching_factor == 0 and self.max_candidates > 0:
            raise ValueError("branching_factor must be > 0 when candidates are allowed")
        if self.deadline_ms is not None and not math.isfinite(float(self.deadline_ms)):
            raise ValueError("deadline_ms must be finite")
        if not math.isfinite(float(self.exploration)) or self.exploration < 0:
            raise ValueError("exploration must be finite and >= 0")
        if not math.isfinite(float(self.mcts_score_gap)) or self.mcts_score_gap < 0:
            raise ValueError("mcts_score_gap must be finite and >= 0")
        if self.deadline_ms is not None:
            object.__setattr__(self, "deadline_ms", float(self.deadline_ms))
        object.__setattr__(self, "exploration", float(self.exploration))
        object.__setattr__(self, "mcts_score_gap", float(self.mcts_score_gap))

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_candidates": self.max_candidates,
            "max_expansions": self.max_expansions,
            "max_transitions": self.max_transitions,
            "max_rollouts": self.max_rollouts,
            "max_depth": self.max_depth,
            "branching_factor": self.branching_factor,
            "deadline_ms": _rounded(self.deadline_ms) if self.deadline_ms is not None else None,
            "exploration": _rounded(self.exploration),
            "mcts_score_gap": _rounded(self.mcts_score_gap),
        }


@dataclass(frozen=True, slots=True)
class SearchRequest:
    request_id: str
    initial_state: Any = field(repr=False, compare=False)
    budget: SearchBudget = field(default_factory=SearchBudget)
    context: Mapping[str, Any] = field(default_factory=dict)
    mcts_mode: MCTSMode = MCTSMode.AUTO
    state_snapshot: VirtualStateSnapshot | None = None

    def __post_init__(self) -> None:
        request_id = str(self.request_id).strip()
        if not request_id:
            raise ValueError("request_id must not be empty")
        mode = self.mcts_mode if isinstance(self.mcts_mode, MCTSMode) else MCTSMode(str(self.mcts_mode))
        snapshot = self.state_snapshot
        if snapshot is not None and not isinstance(snapshot, VirtualStateSnapshot):
            raise ValueError("state_snapshot must be a VirtualStateSnapshot")
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "context", _json_copy(dict(self.context)))
        object.__setattr__(self, "mcts_mode", mode)


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    candidate: SearchCandidate
    score: float
    source: str
    visits: int = 0
    mean_reward: float = 0.0
    path: tuple[SearchCandidate, ...] = ()

    def __post_init__(self) -> None:
        if not math.isfinite(float(self.score)):
            raise ValueError("score must be finite")
        if not math.isfinite(float(self.mean_reward)):
            raise ValueError("mean_reward must be finite")
        object.__setattr__(self, "score", float(self.score))
        object.__setattr__(self, "mean_reward", float(self.mean_reward))
        object.__setattr__(self, "visits", max(0, int(self.visits)))
        object.__setattr__(self, "source", str(self.source))
        object.__setattr__(self, "path", tuple(self.path or (self.candidate,)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate.to_dict(),
            "score": _rounded(self.score),
            "source": self.source,
            "visits": self.visits,
            "mean_reward": _rounded(self.mean_reward),
            "path": [item.to_dict() for item in self.path],
        }


@dataclass(frozen=True, slots=True)
class SearchTraceEvent:
    sequence: int
    layer: str
    event: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", _json_copy(dict(self.details)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "layer": self.layer,
            "event": self.event,
            "details": _json_copy(dict(self.details)),
        }


@dataclass(frozen=True, slots=True)
class SearchCounters:
    candidates: int = 0
    expansions: int = 0
    transitions: int = 0
    rollouts: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "candidates": self.candidates,
            "expansions": self.expansions,
            "transitions": self.transitions,
            "rollouts": self.rollouts,
        }


@dataclass(frozen=True, slots=True)
class SearchResult:
    request_id: str
    status: SearchStatus
    stop_reason: str
    selected_candidate: SearchCandidate | None
    selected_path: tuple[SearchCandidate, ...]
    ranked: tuple[RankedCandidate, ...]
    trace: tuple[SearchTraceEvent, ...]
    counters: SearchCounters
    mcts_used: bool = False
    side_effects_executed: bool = False
    virtual_state_schema: str = ""
    virtual_state_digest: str = ""
    state_snapshot: VirtualStateSnapshot | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.side_effects_executed:
            raise ValueError("PlanningSearch results can never report executed side effects")
        snapshot = self.state_snapshot
        if snapshot is not None and not isinstance(snapshot, VirtualStateSnapshot):
            raise ValueError("state_snapshot must be a VirtualStateSnapshot")
        schema = str(self.virtual_state_schema or "").strip()
        digest = str(self.virtual_state_digest or "").strip().lower()
        if snapshot is not None:
            if schema and schema != snapshot.schema_version:
                raise ValueError("virtual_state_schema differs from state_snapshot")
            if digest and digest != snapshot.state_digest:
                raise ValueError("virtual_state_digest differs from state_snapshot")
            schema = snapshot.schema_version
            digest = snapshot.state_digest
        if bool(schema) != bool(digest):
            raise ValueError(
                "virtual_state_schema and virtual_state_digest must be supplied together"
            )
        if digest and not _SHA256_RE.fullmatch(digest):
            raise ValueError("virtual_state_digest must be a lowercase sha256 digest")
        object.__setattr__(self, "virtual_state_schema", schema)
        object.__setattr__(self, "virtual_state_digest", digest)

    @property
    def trace_digest(self) -> str:
        body = json.dumps(
            [event.to_dict() for event in self.trace],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(body).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        value = {
            "request_id": self.request_id,
            "status": self.status.value,
            "stop_reason": self.stop_reason,
            "selected_candidate": self.selected_candidate.to_dict() if self.selected_candidate else None,
            "selected_path": [item.to_dict() for item in self.selected_path],
            "ranked": [item.to_dict() for item in self.ranked],
            "trace": [item.to_dict() for item in self.trace],
            "trace_digest": self.trace_digest,
            "counters": self.counters.to_dict(),
            "mcts_used": self.mcts_used,
            "side_effects_executed": False,
        }
        if self.virtual_state_digest:
            value["virtual_state"] = {
                "schema": self.virtual_state_schema,
                "digest": self.virtual_state_digest,
            }
        return value


def _json_copy(value: Any) -> Any:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("value must be JSON-safe") from exc
    return json.loads(encoded)


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _required_text(value: Any, *, field_name: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    text = value.strip()
    if not text or len(text) > max_length:
        raise ValueError(f"{field_name} must be non-empty and at most {max_length} characters")
    return text


def _normalized_text_sequence(
    value: Sequence[str],
    *,
    field_name: str,
    allow_empty: bool,
    item_limit: int,
) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ValueError(f"{field_name} must be an explicit sequence")
    values: list[str] = []
    for index, item in enumerate(value):
        text = _required_text(item, field_name=f"{field_name}[{index}]", max_length=item_limit)
        if text not in values:
            values.append(text)
    if not values and not allow_empty:
        raise ValueError(f"{field_name} must not be empty")
    return tuple(values)


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


def _rounded(value: float | None) -> float:
    return round(float(value or 0.0), 10)
