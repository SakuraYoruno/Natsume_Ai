"""Domain-neutral, shadow-only planning calibration contracts.

Calibration stores planning predictions and subsequently observed outcomes.  It
is deliberately not a promotion authority: a record can document evidence, but
cannot authorize a search result to replace an approved baseline action.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any, Mapping, Protocol, Sequence


CALIBRATION_PREDICTION_SCHEMA = "natsume.planning-calibration.prediction.v1"
CALIBRATION_OUTCOME_SCHEMA = "natsume.planning-calibration.outcome.v1"
CALIBRATION_MODE_SHADOW_ONLY = "SHADOW_ONLY"

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_PATH_CANDIDATES = 64
_MAX_VERIFIER_REFS = 64


class CalibrationContractError(ValueError):
    """A prediction or outcome cannot be represented as immutable evidence."""


@dataclass(frozen=True, slots=True)
class CalibrationPrediction:
    """Immutable evidence emitted by one domain's planning shadow run."""

    domain: str
    scope_key: str
    request_id: str
    state_schema: str
    state_digest: str
    baseline_candidate_id: str
    executed_candidate_id: str
    executed_path_candidate_ids: tuple[str, ...]
    shadow_recommended_candidate_id: str
    shadow_path_candidate_ids: tuple[str, ...]
    search_trace_digest: str
    search_budget: Mapping[str, Any]
    search_cost: Mapping[str, Any]
    adapter_revision: str
    predicted_reward: float
    mode: str = CALIBRATION_MODE_SHADOW_ONLY
    influence_enabled: bool = False
    prediction_id: str = ""

    def __post_init__(self) -> None:
        domain = _required_text(self.domain, "domain", 128)
        scope_key = _required_text(self.scope_key, "scope_key", 512)
        request_id = _required_text(self.request_id, "request_id", 512)
        state_schema = _required_text(self.state_schema, "state_schema", 256)
        state_digest = _digest_text(self.state_digest, "state_digest")
        baseline_candidate_id = _required_text(
            self.baseline_candidate_id, "baseline_candidate_id", 512
        )
        executed_candidate_id = _required_text(
            self.executed_candidate_id, "executed_candidate_id", 512
        )
        executed_path = _candidate_path(
            self.executed_path_candidate_ids, "executed_path_candidate_ids"
        )
        shadow_candidate = _required_text(
            self.shadow_recommended_candidate_id,
            "shadow_recommended_candidate_id",
            512,
        )
        shadow_path = _candidate_path(
            self.shadow_path_candidate_ids, "shadow_path_candidate_ids"
        )
        trace_digest = _digest_text(self.search_trace_digest, "search_trace_digest")
        budget = _json_mapping(self.search_budget, "search_budget")
        cost = _json_mapping(self.search_cost, "search_cost")
        adapter_revision = _required_text(self.adapter_revision, "adapter_revision", 256)
        reward = _unit_reward(self.predicted_reward, "predicted_reward")
        mode = str(self.mode or "").strip().upper()
        if mode != CALIBRATION_MODE_SHADOW_ONLY:
            raise CalibrationContractError("calibration mode must be SHADOW_ONLY")
        if self.influence_enabled:
            raise CalibrationContractError("calibration evidence cannot enable influence")

        object.__setattr__(self, "domain", domain)
        object.__setattr__(self, "scope_key", scope_key)
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "state_schema", state_schema)
        object.__setattr__(self, "state_digest", state_digest)
        object.__setattr__(self, "baseline_candidate_id", baseline_candidate_id)
        object.__setattr__(self, "executed_candidate_id", executed_candidate_id)
        object.__setattr__(self, "executed_path_candidate_ids", executed_path)
        object.__setattr__(self, "shadow_recommended_candidate_id", shadow_candidate)
        object.__setattr__(self, "shadow_path_candidate_ids", shadow_path)
        object.__setattr__(self, "search_trace_digest", trace_digest)
        object.__setattr__(self, "search_budget", budget)
        object.__setattr__(self, "search_cost", cost)
        object.__setattr__(self, "adapter_revision", adapter_revision)
        object.__setattr__(self, "predicted_reward", reward)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "influence_enabled", False)

        expected_id = "calpred_" + self.identity_digest[:32]
        supplied = str(self.prediction_id or "").strip()
        if supplied and supplied != expected_id:
            raise CalibrationContractError("prediction_id does not bind immutable identity")
        object.__setattr__(self, "prediction_id", expected_id)

    @property
    def identity_digest(self) -> str:
        return _digest(self.identity_dict())

    def identity_dict(self) -> dict[str, Any]:
        return {
            "schema": CALIBRATION_PREDICTION_SCHEMA,
            "domain": self.domain,
            "scope_key": self.scope_key,
            "request_id": self.request_id,
            "state_schema": self.state_schema,
            "state_digest": self.state_digest,
            "baseline_candidate_id": self.baseline_candidate_id,
            "executed_candidate_id": self.executed_candidate_id,
            "executed_path_candidate_ids": list(self.executed_path_candidate_ids),
            "shadow_recommended_candidate_id": self.shadow_recommended_candidate_id,
            "shadow_path_candidate_ids": list(self.shadow_path_candidate_ids),
            "search_trace_digest": self.search_trace_digest,
            "search_budget": _json_copy(self.search_budget, "search_budget"),
            "search_cost": _json_copy(self.search_cost, "search_cost"),
            "adapter_revision": self.adapter_revision,
            "predicted_reward": self.predicted_reward,
            "mode": self.mode,
            "influence_enabled": False,
        }

    def to_dict(self) -> dict[str, Any]:
        return {"prediction_id": self.prediction_id, **self.identity_dict()}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CalibrationPrediction":
        if not isinstance(value, Mapping):
            raise CalibrationContractError("prediction must be a mapping")
        if value.get("schema") != CALIBRATION_PREDICTION_SCHEMA:
            raise CalibrationContractError("unsupported calibration prediction schema")
        return cls(
            prediction_id=str(value.get("prediction_id") or ""),
            domain=str(value.get("domain") or ""),
            scope_key=str(value.get("scope_key") or ""),
            request_id=str(value.get("request_id") or ""),
            state_schema=str(value.get("state_schema") or ""),
            state_digest=str(value.get("state_digest") or ""),
            baseline_candidate_id=str(value.get("baseline_candidate_id") or ""),
            executed_candidate_id=str(value.get("executed_candidate_id") or ""),
            executed_path_candidate_ids=value.get("executed_path_candidate_ids") or (),
            shadow_recommended_candidate_id=str(
                value.get("shadow_recommended_candidate_id") or ""
            ),
            shadow_path_candidate_ids=value.get("shadow_path_candidate_ids") or (),
            search_trace_digest=str(value.get("search_trace_digest") or ""),
            search_budget=value.get("search_budget") or {},
            search_cost=value.get("search_cost") or {},
            adapter_revision=str(value.get("adapter_revision") or ""),
            predicted_reward=value.get("predicted_reward"),
            mode=str(value.get("mode") or ""),
            influence_enabled=bool(value.get("influence_enabled")),
        )


@dataclass(frozen=True, slots=True)
class CalibrationOutcome:
    """Immutable observed outcome bound to one shadow prediction."""

    prediction_id: str
    executed_candidate_id: str
    actual_reward: float
    outcome_ref: str
    verifier_refs: tuple[str, ...]
    outcome_snapshot: Mapping[str, Any]

    def __post_init__(self) -> None:
        prediction_id = _required_text(self.prediction_id, "prediction_id", 256)
        if not prediction_id.startswith("calpred_"):
            raise CalibrationContractError("outcome prediction_id is not a calibration prediction")
        candidate_id = _required_text(
            self.executed_candidate_id, "executed_candidate_id", 512
        )
        reward = _unit_reward(self.actual_reward, "actual_reward")
        outcome_ref = _required_text(self.outcome_ref, "outcome_ref", 1_024)
        refs = tuple(
            sorted(
                set(
                    _text_sequence(
                        self.verifier_refs,
                        "verifier_refs",
                        item_limit=_MAX_VERIFIER_REFS,
                        text_limit=1_024,
                    )
                )
            )
        )
        snapshot = _json_mapping(self.outcome_snapshot, "outcome_snapshot")
        object.__setattr__(self, "prediction_id", prediction_id)
        object.__setattr__(self, "executed_candidate_id", candidate_id)
        object.__setattr__(self, "actual_reward", reward)
        object.__setattr__(self, "outcome_ref", outcome_ref)
        object.__setattr__(self, "verifier_refs", refs)
        object.__setattr__(self, "outcome_snapshot", snapshot)

    @property
    def outcome_digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CALIBRATION_OUTCOME_SCHEMA,
            "prediction_id": self.prediction_id,
            "executed_candidate_id": self.executed_candidate_id,
            "actual_reward": self.actual_reward,
            "outcome_ref": self.outcome_ref,
            "verifier_refs": list(self.verifier_refs),
            "outcome_snapshot": _json_copy(self.outcome_snapshot, "outcome_snapshot"),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CalibrationOutcome":
        if not isinstance(value, Mapping):
            raise CalibrationContractError("outcome must be a mapping")
        if value.get("schema") != CALIBRATION_OUTCOME_SCHEMA:
            raise CalibrationContractError("unsupported calibration outcome schema")
        return cls(
            prediction_id=str(value.get("prediction_id") or ""),
            executed_candidate_id=str(value.get("executed_candidate_id") or ""),
            actual_reward=value.get("actual_reward"),
            outcome_ref=str(value.get("outcome_ref") or ""),
            verifier_refs=value.get("verifier_refs") or (),
            outcome_snapshot=value.get("outcome_snapshot") or {},
        )


@dataclass(frozen=True, slots=True)
class CalibrationRecord:
    prediction: CalibrationPrediction
    outcome: CalibrationOutcome | None
    created_at_ms: int
    reconciled_at_ms: int | None

    def __post_init__(self) -> None:
        created = int(self.created_at_ms)
        reconciled = (
            None if self.reconciled_at_ms is None else int(self.reconciled_at_ms)
        )
        if created < 0 or (reconciled is not None and reconciled < created):
            raise CalibrationContractError("calibration timestamps are invalid")
        if (self.outcome is None) != (reconciled is None):
            raise CalibrationContractError("outcome and reconciliation timestamp must agree")
        object.__setattr__(self, "created_at_ms", created)
        object.__setattr__(self, "reconciled_at_ms", reconciled)

    @property
    def reconciled(self) -> bool:
        return self.outcome is not None


@dataclass(frozen=True, slots=True)
class CalibrationMetrics:
    domain: str
    predictions: int
    reconciled: int
    pending: int
    mean_absolute_error: float | None

    def __post_init__(self) -> None:
        domain = _required_text(self.domain, "domain", 128)
        predictions = max(0, int(self.predictions))
        reconciled = max(0, int(self.reconciled))
        pending = max(0, int(self.pending))
        if predictions != reconciled + pending:
            raise CalibrationContractError("calibration metric counts do not reconcile")
        mae = self.mean_absolute_error
        if mae is not None:
            mae = _finite_float(mae, "mean_absolute_error")
        object.__setattr__(self, "domain", domain)
        object.__setattr__(self, "predictions", predictions)
        object.__setattr__(self, "reconciled", reconciled)
        object.__setattr__(self, "pending", pending)
        object.__setattr__(self, "mean_absolute_error", mae)


class CalibrationRepository(Protocol):
    """Durable port for immutable planning calibration evidence."""

    def record_prediction(self, prediction: CalibrationPrediction) -> CalibrationRecord: ...

    def reconcile_outcome(self, outcome: CalibrationOutcome) -> CalibrationRecord: ...

    def get(self, prediction_id: str) -> CalibrationRecord | None: ...

    def list_records(
        self,
        *,
        domain: str,
        scope_key: str | None = None,
        limit: int = 256,
    ) -> tuple[CalibrationRecord, ...]: ...

    def metrics(self, *, domain: str, scope_key: str | None = None) -> CalibrationMetrics: ...


def _required_text(value: Any, field_name: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit:
        raise CalibrationContractError(f"{field_name} must be non-empty and at most {limit} characters")
    return text


def _digest_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip().lower()
    if not _DIGEST_RE.fullmatch(text):
        raise CalibrationContractError(f"{field_name} must be a lowercase SHA-256 digest")
    return text


def _candidate_path(value: Sequence[Any], field_name: str) -> tuple[str, ...]:
    items = _text_sequence(
        value,
        field_name,
        item_limit=_MAX_PATH_CANDIDATES,
        text_limit=512,
    )
    if not items:
        raise CalibrationContractError(f"{field_name} must not be empty")
    return items


def _text_sequence(
    value: Any,
    field_name: str,
    *,
    item_limit: int,
    text_limit: int,
) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise CalibrationContractError(f"{field_name} must be a sequence, not text")
    if len(value) > item_limit:
        raise CalibrationContractError(f"{field_name} exceeds {item_limit} items")
    return tuple(
        _required_text(item, f"{field_name}[]", text_limit)
        for item in value
        if str(item).strip()
    )


def _finite_float(value: Any, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise CalibrationContractError(f"{field_name} must be finite") from exc
    if not math.isfinite(number):
        raise CalibrationContractError(f"{field_name} must be finite")
    return number


def _unit_reward(value: Any, field_name: str) -> float:
    reward = _finite_float(value, field_name)
    if not 0.0 <= reward <= 1.0:
        raise CalibrationContractError(f"{field_name} must be within [0, 1]")
    return reward


def _json_mapping(value: Mapping[str, Any], field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CalibrationContractError(f"{field_name} must be a mapping")
    copied = _json_copy(dict(value), field_name)
    if not isinstance(copied, dict):
        raise CalibrationContractError(f"{field_name} must be a JSON object")
    return copied


def _json_copy(value: Any, field_name: str) -> Any:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise CalibrationContractError(f"{field_name} must be JSON-safe") from exc
    if len(encoded.encode("utf-8")) > 1_048_576:
        raise CalibrationContractError(f"{field_name} exceeds the calibration evidence size limit")
    return json.loads(encoded)


def _digest(value: Mapping[str, Any]) -> str:
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


__all__ = [
    "CALIBRATION_MODE_SHADOW_ONLY",
    "CALIBRATION_OUTCOME_SCHEMA",
    "CALIBRATION_PREDICTION_SCHEMA",
    "CalibrationContractError",
    "CalibrationMetrics",
    "CalibrationOutcome",
    "CalibrationPrediction",
    "CalibrationRecord",
    "CalibrationRepository",
]
