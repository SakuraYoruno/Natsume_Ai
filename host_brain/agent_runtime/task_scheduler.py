"""Deterministic, side-effect-free canonical scheduler shadow.

TASK-0469 deliberately separates decision replay from durable mutation.  The
shadow evaluator consumes a frozen TaskGraph snapshot and never calls a worker,
tool, provider, or executor.  A later cutover task may add an explicit apply
boundary after comparing these decisions with the legacy authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from typing import Any, Mapping, Sequence

from host_brain.agent_runtime.task_graph import TaskNodeStatus


SCHEDULER_SHADOW_SCHEMA_VERSION = "Natsume.agent.scheduler_shadow.v1"
SCHEDULER_SNAPSHOT_INVALID = "SCHEDULER_SNAPSHOT_INVALID"
SCHEDULER_DEPENDENCY_BLOCKED = "SCHEDULER_DEPENDENCY_BLOCKED"
SCHEDULER_ARTIFACT_MISSING = "SCHEDULER_ARTIFACT_MISSING"
SCHEDULER_RETRY_EXHAUSTED = "SCHEDULER_RETRY_EXHAUSTED"
SCHEDULER_RETRY_BACKOFF = "SCHEDULER_RETRY_BACKOFF"
SCHEDULER_RETRY_CLASSIFIED_TERMINAL = "SCHEDULER_RETRY_CLASSIFIED_TERMINAL"
SCHEDULER_CANCEL_REQUESTED = "SCHEDULER_CANCEL_REQUESTED"
SCHEDULER_RECOVERY_REQUIRED = "SCHEDULER_RECOVERY_REQUIRED"
SCHEDULER_BUDGET_BLOCKED = "SCHEDULER_BUDGET_BLOCKED"
SCHEDULER_READY = "SCHEDULER_READY"
SCHEDULER_OWNER_REQUIRED = "SCHEDULER_OWNER_REQUIRED"


class ShadowSchedulerError(ValueError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code)
        self.detail = str(detail)
        super().__init__(f"{self.reason_code}: {self.detail}")


class SchedulerAction(StrEnum):
    READY = "READY"
    BLOCKED = "BLOCKED"
    RETRY_WAIT = "RETRY_WAIT"
    RETRY_EXHAUSTED = "RETRY_EXHAUSTED"
    CANCEL = "CANCEL"
    RECOVER = "RECOVER"
    BUDGET_BLOCKED = "BUDGET_BLOCKED"
    HOLD = "HOLD"


@dataclass(frozen=True, slots=True)
class SchedulerDecision:
    graph_id: str
    graph_revision: int
    node_id: str
    node_status_version: int
    action: SchedulerAction
    reason_code: str
    attempt_no: int | None = None
    eligible_ts_ms: int | None = None
    dependency_artifact_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEDULER_SHADOW_SCHEMA_VERSION,
            "graph_id": self.graph_id,
            "graph_revision": self.graph_revision,
            "node_id": self.node_id,
            "node_status_version": self.node_status_version,
            "action": self.action.value,
            "reason_code": self.reason_code,
            "attempt_no": self.attempt_no,
            "eligible_ts_ms": self.eligible_ts_ms,
            "dependency_artifact_refs": list(self.dependency_artifact_refs),
        }


@dataclass(frozen=True, slots=True)
class SchedulerShadowReport:
    graph_id: str
    graph_revision: int
    observed_ts_ms: int
    decisions: tuple[SchedulerDecision, ...]
    legacy_digest: str | None = None
    unexplained_diff_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": SCHEDULER_SHADOW_SCHEMA_VERSION,
            "graph_id": self.graph_id,
            "graph_revision": self.graph_revision,
            "observed_ts_ms": self.observed_ts_ms,
            "decisions": [item.to_dict() for item in self.decisions],
            "legacy_digest": self.legacy_digest,
            "unexplained_diff_count": self.unexplained_diff_count,
            "external_side_effect_count": 0,
        }
        payload["decision_digest"] = _digest(payload["decisions"])
        return payload


class CanonicalShadowScheduler:
    """Pure scheduler decision engine; construction and evaluation are read-only."""

    _DEPENDENCY_COMPLETE = frozenset({"VERIFIED", "ACCEPTED"})
    _TERMINAL = frozenset({"ACCEPTED", "FAILED", "VERIFICATION_FAILED", "CANCELLED"})
    _ACTIVE_ATTEMPTS = frozenset({"CREATED", "LEASED", "RUNNING"})

    def evaluate(
        self,
        graph_record: Mapping[str, Any],
        *,
        attempts: Sequence[Mapping[str, Any]] = (),
        artifact_refs: Mapping[str, str] | None = None,
        budget_allowed: Mapping[str, bool] | None = None,
        now_ts_ms: int,
        legacy_decisions: Sequence[Mapping[str, Any]] | None = None,
    ) -> SchedulerShadowReport:
        if isinstance(now_ts_ms, bool) or not isinstance(now_ts_ms, int) or now_ts_ms < 0:
            raise ShadowSchedulerError(SCHEDULER_SNAPSHOT_INVALID, "now_ts_ms must be an exact non-negative integer")
        graph = graph_record.get("graph") if isinstance(graph_record.get("graph"), Mapping) else graph_record
        graph_id = _text(graph.get("graph_id"))
        revision = _exact_int(graph.get("revision"), "graph revision", minimum=1)
        raw_nodes = graph_record.get("nodes")
        if not isinstance(raw_nodes, Sequence) or isinstance(raw_nodes, (str, bytes)):
            raw_nodes = graph.get("nodes")
        if not graph_id or not isinstance(raw_nodes, Sequence) or isinstance(raw_nodes, (str, bytes)):
            raise ShadowSchedulerError(SCHEDULER_SNAPSHOT_INVALID, "graph identity and nodes are required")

        nodes = [_node_view(item) for item in raw_nodes]
        if len({item["node_id"] for item in nodes}) != len(nodes):
            raise ShadowSchedulerError(SCHEDULER_SNAPSHOT_INVALID, "node identities must be unique")
        by_id = {item["node_id"]: item for item in nodes}
        attempts_by_node: dict[str, list[Mapping[str, Any]]] = {}
        for attempt in attempts:
            if _text(attempt.get("graph_id")) != graph_id or _exact_int(attempt.get("graph_revision"), "attempt revision", minimum=1) != revision:
                raise ShadowSchedulerError(SCHEDULER_SNAPSHOT_INVALID, "attempt lineage differs from graph revision")
            node_id = _text(attempt.get("node_id"))
            if node_id not in by_id:
                raise ShadowSchedulerError(SCHEDULER_SNAPSHOT_INVALID, "attempt references an unknown node")
            attempts_by_node.setdefault(node_id, []).append(attempt)

        refs = {str(key): _text(value) for key, value in dict(artifact_refs or {}).items()}
        budgets = {str(key): value for key, value in dict(budget_allowed or {}).items()}
        if any(not isinstance(value, bool) for value in budgets.values()):
            raise ShadowSchedulerError(SCHEDULER_SNAPSHOT_INVALID, "budget decisions must be booleans")
        decisions = tuple(
            self._decide_node(
                graph_id,
                revision,
                node,
                by_id=by_id,
                attempts=attempts_by_node.get(node["node_id"], []),
                artifact_refs=refs,
                budget_allowed=budgets.get(node["node_id"], True),
                now_ts_ms=now_ts_ms,
            )
            for node in sorted(nodes, key=lambda item: item["node_id"])
        )
        legacy_digest, diff_count = _compare_legacy(decisions, legacy_decisions)
        return SchedulerShadowReport(
            graph_id=graph_id,
            graph_revision=revision,
            observed_ts_ms=now_ts_ms,
            decisions=decisions,
            legacy_digest=legacy_digest,
            unexplained_diff_count=diff_count,
        )

    def evaluate_durable(
        self,
        store: Any,
        graph_id: str,
        graph_revision: int,
        *,
        artifact_refs: Mapping[str, str] | None = None,
        budget_allowed: Mapping[str, bool] | None = None,
        now_ts_ms: int,
        legacy_decisions: Sequence[Mapping[str, Any]] | None = None,
    ) -> SchedulerShadowReport:
        """Evaluate one durable snapshot without recovery or state mutation."""
        reader = getattr(store, "read_scheduler_snapshot", None)
        if not callable(reader):
            raise ShadowSchedulerError(SCHEDULER_SNAPSHOT_INVALID, "store lacks read_scheduler_snapshot")
        snapshot = reader(graph_id, graph_revision)
        attempts = snapshot.get("attempts")
        active_leases = snapshot.get("active_leases")
        budgets = snapshot.get("budgets")
        if not isinstance(attempts, list) or not isinstance(active_leases, list) or not isinstance(budgets, list):
            raise ShadowSchedulerError(SCHEDULER_SNAPSHOT_INVALID, "durable scheduler snapshot is incomplete")
        attempts_for_decision = [dict(item) for item in attempts]
        active_attempt_ids = {_text(item.get("attempt_id")) for item in attempts_for_decision if _text(item.get("status")) in self._ACTIVE_ATTEMPTS}
        leases_by_attempt: dict[str, list[Mapping[str, Any]]] = {}
        for lease in active_leases:
            attempt_id = _text(lease.get("attempt_id"))
            leases_by_attempt.setdefault(attempt_id, []).append(lease)
        if not set(leases_by_attempt).issubset(active_attempt_ids):
            raise ShadowSchedulerError(SCHEDULER_SNAPSHOT_INVALID, "active lease lacks matching active attempt")
        if any(len(items) != 1 for items in leases_by_attempt.values()):
            raise ShadowSchedulerError(SCHEDULER_SNAPSHOT_INVALID, "attempt has multiple active leases")
        for attempt in attempts_for_decision:
            status = _text(attempt.get("status"))
            if status not in {"LEASED", "RUNNING"}:
                continue
            lease_items = leases_by_attempt.get(_text(attempt.get("attempt_id")), [])
            if not lease_items:
                attempt["status"] = "RECOVERABLE"
                continue
            expiry = _exact_int(lease_items[0].get("expires_ts_ms"), "lease expires_ts_ms", minimum=0)
            _exact_int(lease_items[0].get("fencing_token"), "lease fencing_token", minimum=1)
            if expiry <= now_ts_ms:
                attempt["status"] = "RECOVERABLE"
        durable_budget_allowed: dict[str, bool] = {}
        for budget in budgets:
            if _text(budget.get("graph_id")) != _text(graph_id) or _exact_int(budget.get("graph_revision"), "budget graph_revision", minimum=1) != graph_revision:
                raise ShadowSchedulerError(SCHEDULER_SNAPSHOT_INVALID, "budget lineage differs from graph revision")
            node_id = _text(budget.get("node_id"))
            if not node_id:
                continue
            status = _text(budget.get("status"))
            if status not in {"PENDING", "ACTIVE", "CONSUMED", "RELEASED", "EXCEEDED", "CANCELLED"}:
                raise ShadowSchedulerError(SCHEDULER_SNAPSHOT_INVALID, "unknown durable budget status")
            durable_budget_allowed[node_id] = durable_budget_allowed.get(node_id, True) and status == "ACTIVE"
        for node_id, allowed in dict(budget_allowed or {}).items():
            if not isinstance(allowed, bool):
                raise ShadowSchedulerError(SCHEDULER_SNAPSHOT_INVALID, "budget decisions must be booleans")
            durable_budget_allowed[str(node_id)] = durable_budget_allowed.get(str(node_id), True) and allowed
        return self.evaluate(
            snapshot.get("graph_record") or {},
            attempts=attempts_for_decision,
            artifact_refs=artifact_refs,
            budget_allowed=durable_budget_allowed,
            now_ts_ms=now_ts_ms,
            legacy_decisions=legacy_decisions,
        )

    def _decide_node(
        self,
        graph_id: str,
        revision: int,
        node: Mapping[str, Any],
        *,
        by_id: Mapping[str, Mapping[str, Any]],
        attempts: Sequence[Mapping[str, Any]],
        artifact_refs: Mapping[str, str],
        budget_allowed: bool,
        now_ts_ms: int,
    ) -> SchedulerDecision:
        node_id = node["node_id"]
        status = node["status"]
        base = dict(
            graph_id=graph_id,
            graph_revision=revision,
            node_id=node_id,
            node_status_version=node["status_version"],
        )
        allowed_attempt_statuses = {"CREATED", "LEASED", "RUNNING", "RECOVERABLE", "SUCCEEDED", "FAILED", "CANCEL_REQUESTED", "CANCELLED"}
        attempt_statuses = [_text(item.get("status")) for item in attempts]
        if any(value not in allowed_attempt_statuses for value in attempt_statuses):
            raise ShadowSchedulerError(SCHEDULER_SNAPSHOT_INVALID, f"unknown attempt status for {node_id}")
        attempt_numbers = sorted(_exact_int(item.get("attempt_no"), "attempt_no", minimum=1) for item in attempts)
        if len(attempt_numbers) != len(set(attempt_numbers)) or attempt_numbers != list(range(1, len(attempt_numbers) + 1)):
            raise ShadowSchedulerError(SCHEDULER_SNAPSHOT_INVALID, f"attempt sequence is not contiguous for {node_id}")
        if status in self._TERMINAL or status in {"EXECUTED", "VERIFYING", "WAITING_ACCEPTANCE_REVIEW"}:
            return SchedulerDecision(**base, action=SchedulerAction.HOLD, reason_code=f"SCHEDULER_HOLD_{status}")
        if status == TaskNodeStatus.CANCEL_REQUESTED.value:
            return SchedulerDecision(**base, action=SchedulerAction.CANCEL, reason_code=SCHEDULER_CANCEL_REQUESTED)
        active = [item for item in attempts if _text(item.get("status")) in self._ACTIVE_ATTEMPTS]
        recoverable = [item for item in attempts if _text(item.get("status")) == "RECOVERABLE"]
        succeeded = [item for item in attempts if _text(item.get("status")) == "SUCCEEDED"]
        if len(active) > 1 or len(recoverable) > 1 or (active and recoverable):
            raise ShadowSchedulerError(SCHEDULER_SNAPSHOT_INVALID, f"ambiguous live attempt ownership for {node_id}")
        if recoverable:
            attempt_no = _exact_int(recoverable[0].get("attempt_no"), "attempt_no", minimum=1)
            return SchedulerDecision(**base, action=SchedulerAction.RECOVER, reason_code=SCHEDULER_RECOVERY_REQUIRED, attempt_no=attempt_no)
        if succeeded:
            if len(succeeded) != 1:
                raise ShadowSchedulerError(SCHEDULER_SNAPSHOT_INVALID, f"multiple successful attempts for {node_id}")
            attempt_no = _exact_int(succeeded[0].get("attempt_no"), "attempt_no", minimum=1)
            return SchedulerDecision(**base, action=SchedulerAction.HOLD, reason_code="SCHEDULER_ATTEMPT_SUCCEEDED", attempt_no=attempt_no)
        if active:
            attempt_no = _exact_int(active[0].get("attempt_no"), "attempt_no", minimum=1)
            return SchedulerDecision(**base, action=SchedulerAction.HOLD, reason_code=f"SCHEDULER_ATTEMPT_{_text(active[0].get('status'))}", attempt_no=attempt_no)

        dependencies = tuple(node["dependencies"])
        missing = [dependency for dependency in dependencies if dependency not in by_id]
        if missing:
            raise ShadowSchedulerError(SCHEDULER_SNAPSHOT_INVALID, f"unknown dependencies for {node_id}: {','.join(missing)}")
        blocked = [dependency for dependency in dependencies if by_id[dependency]["status"] not in self._DEPENDENCY_COMPLETE]
        if blocked:
            return SchedulerDecision(**base, action=SchedulerAction.BLOCKED, reason_code=SCHEDULER_DEPENDENCY_BLOCKED)
        dependency_refs: list[str] = []
        for dependency in dependencies:
            ref = artifact_refs.get(dependency, "")
            if not ref:
                return SchedulerDecision(**base, action=SchedulerAction.BLOCKED, reason_code=SCHEDULER_ARTIFACT_MISSING)
            if ref not in node["input_refs"]:
                raise ShadowSchedulerError(SCHEDULER_SNAPSHOT_INVALID, f"dependency artifact ref is not planner-bound for {node_id}")
            dependency_refs.append(ref)
        if not budget_allowed:
            return SchedulerDecision(**base, action=SchedulerAction.BUDGET_BLOCKED, reason_code=SCHEDULER_BUDGET_BLOCKED, dependency_artifact_refs=tuple(dependency_refs))

        failed = sorted(
            (item for item in attempts if _text(item.get("status")) == "FAILED"),
            key=lambda item: _exact_int(item.get("attempt_no"), "attempt_no", minimum=1),
        )
        retry_limit, backoff_ms, retryable_codes = _retry_policy(node["retry_policy"])
        if failed:
            last = failed[-1]
            attempt_no = _exact_int(last.get("attempt_no"), "attempt_no", minimum=1)
            error_ref = _text(last.get("error_ref"))
            reason_code = error_ref.split(":", 1)[-1].split("/", 1)[0].strip().upper()
            if not error_ref or reason_code not in retryable_codes:
                return SchedulerDecision(**base, action=SchedulerAction.RETRY_EXHAUSTED, reason_code=SCHEDULER_RETRY_CLASSIFIED_TERMINAL, attempt_no=attempt_no)
            if attempt_no > retry_limit:
                return SchedulerDecision(**base, action=SchedulerAction.RETRY_EXHAUSTED, reason_code=SCHEDULER_RETRY_EXHAUSTED, attempt_no=attempt_no)
            updated_ts_ms = _exact_int(last.get("updated_ts_ms"), "attempt updated_ts_ms", minimum=0)
            eligible = updated_ts_ms + backoff_ms * (2 ** (attempt_no - 1))
            if now_ts_ms < eligible:
                return SchedulerDecision(**base, action=SchedulerAction.RETRY_WAIT, reason_code=SCHEDULER_RETRY_BACKOFF, attempt_no=attempt_no + 1, eligible_ts_ms=eligible, dependency_artifact_refs=tuple(dependency_refs))
            next_attempt = attempt_no + 1
        else:
            next_attempt = 1
        if status not in {TaskNodeStatus.CREATED.value, TaskNodeStatus.BLOCKED.value, TaskNodeStatus.READY.value, TaskNodeStatus.RETRY_PENDING.value}:
            return SchedulerDecision(**base, action=SchedulerAction.HOLD, reason_code=f"SCHEDULER_HOLD_{status}")
        return SchedulerDecision(**base, action=SchedulerAction.READY, reason_code=SCHEDULER_READY, attempt_no=next_attempt, dependency_artifact_refs=tuple(dependency_refs))


def _node_view(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ShadowSchedulerError(SCHEDULER_SNAPSHOT_INVALID, "nodes must be mappings")
    node = raw.get("node") if isinstance(raw.get("node"), Mapping) else raw
    node_id = _text(node.get("node_id"))
    status = _text(raw.get("status") or node.get("status"))
    version = _exact_int(raw.get("status_version", node.get("status_version")), "node status_version", minimum=0)
    dependencies = node.get("dependencies")
    input_refs = node.get("input_refs")
    retry_policy = node.get("retry_policy")
    if not node_id or status not in {item.value for item in TaskNodeStatus}:
        raise ShadowSchedulerError(SCHEDULER_SNAPSHOT_INVALID, "node identity/status is invalid")
    if not isinstance(dependencies, list) or not isinstance(input_refs, list) or not isinstance(retry_policy, Mapping):
        raise ShadowSchedulerError(SCHEDULER_SNAPSHOT_INVALID, "node dependencies/input_refs/retry_policy types are invalid")
    return {"node_id": node_id, "status": status, "status_version": version, "dependencies": tuple(_text(item) for item in dependencies), "input_refs": tuple(_text(item) for item in input_refs), "retry_policy": dict(retry_policy)}


def _retry_policy(policy: Mapping[str, Any]) -> tuple[int, int, frozenset[str]]:
    allowed = {"retry_limit", "retry_backoff_ms", "idempotency_key", "fallback_policy", "max_attempts", "retryable_reason_codes"}
    if not set(policy).issubset(allowed):
        raise ShadowSchedulerError(SCHEDULER_SNAPSHOT_INVALID, "retry_policy contains unknown fields")
    if "retry_limit" in policy and "max_attempts" in policy:
        raise ShadowSchedulerError(SCHEDULER_SNAPSHOT_INVALID, "retry_policy cannot mix retry_limit and max_attempts")
    if "max_attempts" in policy:
        max_attempts = _exact_int(policy.get("max_attempts"), "max_attempts", minimum=1)
        retry_limit = max_attempts - 1
    else:
        retry_limit = _exact_int(policy.get("retry_limit", 0), "retry_limit", minimum=0)
    raw_codes = policy.get("retryable_reason_codes", ["TIMEOUT", "RATE_LIMIT", "PROVIDER_UNAVAILABLE", "LEASE_LOST", "TRANSIENT"])
    if not isinstance(raw_codes, list) or any(not isinstance(item, str) or not item.strip() for item in raw_codes):
        raise ShadowSchedulerError(SCHEDULER_SNAPSHOT_INVALID, "retryable_reason_codes must be a non-empty string array")
    codes = tuple(item.strip().upper() for item in raw_codes)
    if len(codes) != len(set(codes)):
        raise ShadowSchedulerError(SCHEDULER_SNAPSHOT_INVALID, "retryable_reason_codes must be unique")
    return (retry_limit, _exact_int(policy.get("retry_backoff_ms", 0), "retry_backoff_ms", minimum=0), frozenset(codes))


def _exact_int(value: Any, field: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ShadowSchedulerError(SCHEDULER_SNAPSHOT_INVALID, f"{field} must be an exact integer >= {minimum}")
    return value


def _text(value: Any) -> str:
    return str(value or "").strip()


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _compare_legacy(decisions: Sequence[SchedulerDecision], legacy: Sequence[Mapping[str, Any]] | None) -> tuple[str | None, int]:
    if legacy is None:
        return None, 0
    normalized = sorted(({"node_id": _text(item.get("node_id")), "action": _text(item.get("action")), "reason_code": _text(item.get("reason_code"))} for item in legacy), key=lambda item: item["node_id"])
    if any(not item["node_id"] or not item["action"] for item in normalized):
        raise ShadowSchedulerError(SCHEDULER_SNAPSHOT_INVALID, "legacy decisions require node_id/action")
    canonical = {item.node_id: item.action.value for item in decisions}
    legacy_actions = {item["node_id"]: item["action"] for item in normalized}
    diff_count = sum(1 for node_id in set(canonical) | set(legacy_actions) if canonical.get(node_id) != legacy_actions.get(node_id))
    return _digest(normalized), diff_count


def evaluate_configured_shadow(
    scheduler: CanonicalShadowScheduler,
    store: Any,
    graph_id: str,
    graph_revision: int,
    *,
    runtime_mode: str,
    configured_owner: str,
    confirmed_owner: str,
    now_ts_ms: int,
    artifact_refs: Mapping[str, str] | None = None,
    budget_allowed: Mapping[str, bool] | None = None,
    legacy_decisions: Sequence[Mapping[str, Any]] | None = None,
) -> SchedulerShadowReport | None:
    """Feature-flagged shadow adapter; legacy mode is a strict no-op."""
    mode = _text(runtime_mode).lower()
    if mode == "legacy":
        return None
    if mode != "shadow":
        raise ShadowSchedulerError(SCHEDULER_SNAPSHOT_INVALID, "runtime mode must be legacy or shadow")
    owner = _text(configured_owner)
    if not owner or _text(confirmed_owner) != owner:
        raise ShadowSchedulerError(SCHEDULER_OWNER_REQUIRED, "shadow runtime owner confirmation does not match")
    return scheduler.evaluate_durable(
        store,
        graph_id,
        graph_revision,
        artifact_refs=artifact_refs,
        budget_allowed=budget_allowed,
        now_ts_ms=now_ts_ms,
        legacy_decisions=legacy_decisions,
    )


__all__ = ["CanonicalShadowScheduler", "SchedulerAction", "SchedulerDecision", "SchedulerShadowReport", "ShadowSchedulerError", "evaluate_configured_shadow"]
