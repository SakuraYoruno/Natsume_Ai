"""TaskGraph-driven, node-bound child Governor grants.

The service deliberately has no dependency on Executive planning types.  It
accepts only the canonical ``TaskGraph``/``WorkOrderV3`` pair and returns
opaque durable grant references; bearer approval tokens remain confined to
the Governor ledger.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import threading
import time
from typing import Any, Callable
from urllib.parse import unquote

from host_brain.contracts.agent_runtime.task_graph import TaskGraph
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.governor.action_arbiter import (
    GOVERNED_ACTION_BINDING_SCHEMA_VERSION,
    build_governed_action_binding,
)
from host_brain.governor.route_token import compile_route_decision
from host_brain.orchestration.governor_grant import (
    DurableGovernorGrantResolver,
    durable_governor_binding,
    governor_grant_ref,
)


NODE_GRANT_INPUT_INVALID = "NODE_GRANT_INPUT_INVALID"
NODE_GRANT_PARENT_INVALID = "NODE_GRANT_PARENT_INVALID"
NODE_GRANT_BINDING_MISSING = "NODE_GRANT_BINDING_MISSING"
NODE_GRANT_ROUTE_DRIFT = "NODE_GRANT_ROUTE_DRIFT"
NODE_GRANT_ROUTE_UNSUPPORTED = "NODE_GRANT_ROUTE_UNSUPPORTED"
NODE_GRANT_REPLAY_DRIFT = "NODE_GRANT_REPLAY_DRIFT"
NODE_GRANT_EXPIRED = "NODE_GRANT_EXPIRED"
NODE_GRANT_VERIFICATION_FAILED = "NODE_GRANT_VERIFICATION_FAILED"

_CHILD_STAGE = "S_TASK_GRAPH_NODE_GRANT"
_CHILD_REASON = "TASK_GRAPH_NODE_DERIVED_APPROVE"
_SUPPORTED_EXECUTABLE_ROUTES = frozenset({"skill", "delegate", "chat"})
_ISSUABLE_NODE_ROUTES = _SUPPORTED_EXECUTABLE_ROUTES


class NodeGrantDerivationError(PermissionError):
    """Fail-closed node grant derivation error with a stable reason code."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or NODE_GRANT_INPUT_INVALID)
        self.detail = str(detail or "node grant derivation failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


class NodeGrantVerificationError(PermissionError):
    """A durable child decision failed exact authority verification."""

    def __init__(self, detail: str, *, reason_code: str = NODE_GRANT_VERIFICATION_FAILED) -> None:
        self.reason_code = str(reason_code or NODE_GRANT_VERIFICATION_FAILED)
        self.detail = str(detail or "node grant verification failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


@dataclass(frozen=True, slots=True)
class NodeBoundGrant:
    """Secret-free public projection of one durable child decision."""

    node_id: str
    route: str
    target_id: str
    decision_id: str
    intent_id: str
    grant_ref: str
    expires_at_ms: int
    nonce: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "route": self.route,
            "target_id": self.target_id,
            "decision_id": self.decision_id,
            "intent_id": self.intent_id,
            "grant_ref": self.grant_ref,
            "expires_at_ms": self.expires_at_ms,
            "nonce": self.nonce,
        }


@dataclass(frozen=True, slots=True)
class NodeGrantBatch:
    batch_id: str
    parent_grant_ref: str
    task_graph_id: str
    graph_revision: int
    grants: tuple[NodeBoundGrant, ...]
    replayed: bool

    @property
    def grant_refs(self) -> dict[str, str]:
        return {grant.node_id: grant.grant_ref for grant in self.grants}

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "parent_grant_ref": self.parent_grant_ref,
            "task_graph_id": self.task_graph_id,
            "graph_revision": self.graph_revision,
            "grants": [grant.to_dict() for grant in self.grants],
            "replayed": self.replayed,
        }


@dataclass(frozen=True, slots=True)
class VerifiedNodeGrant:
    """Secret-free exact projection returned to a TaskRuntime authority gate.

    Attempt, lease/fencing and business-idempotency checks remain TaskRuntime
    responsibilities.  This projection proves only the durable node grant.
    """

    grant_ref: str
    decision_id: str
    intent_id: str
    request_id: str
    session_id: str
    principal_id: str
    graph_id: str
    root_work_order_id: str
    graph_revision: int
    node_id: str
    route: str
    target_id: str
    parent_grant_ref: str
    expires_at_ms: int
    nonce: str
    action_kind: str
    token_subject: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "grant_ref": self.grant_ref,
            "decision_id": self.decision_id,
            "intent_id": self.intent_id,
            "request_id": self.request_id,
            "session_id": self.session_id,
            "principal_id": self.principal_id,
            "graph_id": self.graph_id,
            "root_work_order_id": self.root_work_order_id,
            "graph_revision": self.graph_revision,
            "node_id": self.node_id,
            "route": self.route,
            "target_id": self.target_id,
            "parent_grant_ref": self.parent_grant_ref,
            "expires_at_ms": self.expires_at_ms,
            "nonce": self.nonce,
            "action_kind": self.action_kind,
            "token_subject": self.token_subject,
        }


@dataclass(frozen=True, slots=True)
class _NodeSpec:
    node_id: str
    route: str
    target_id: str
    risk_level: int
    ordinal: int
    decision_id: str
    intent_id: str
    nonce: str
    seq: int
    action_kind: str
    token_subject: str


class NodeBoundGrantService:
    """Derive an all-or-nothing set of child decisions for a canonical graph.

    All graph/binding/route checks run before token issuance or ledger writes.
    Replays resolve the already-durable batch and never mint replacement
    tokens.  A partial or drifting durable batch is rejected.

    ``skill`` keeps its existing target-only token subject for executor
    compatibility. ``chat`` and ``delegate`` use a ``route:target`` subject and
    are consumed only through :class:`DurableNodeGrantVerifier`; they do not
    pretend to use the Skill executor token-consumption contract.
    """

    def __init__(
        self,
        *,
        ledger: Any,
        token_service: Any,
        clock: Callable[[], int] | None = None,
        child_ttl_ms: int = 60_000,
    ) -> None:
        if not callable(getattr(ledger, "trace_request", None)):
            raise TypeError("ledger must expose trace_request")
        if not callable(getattr(ledger, "log_governor", None)):
            raise TypeError("ledger must expose log_governor")
        if not callable(getattr(token_service, "issue_approval_token", None)):
            raise TypeError("token_service must expose issue_approval_token")
        ttl = int(child_ttl_ms)
        if ttl <= 0:
            raise ValueError("child_ttl_ms must be positive")
        self._ledger = ledger
        self._token_service = token_service
        self._clock = clock or (lambda: int(time.time() * 1000))
        self._child_ttl_ms = ttl
        self._lock = threading.RLock()

    def derive(
        self,
        *,
        parent_grant_ref: str,
        task_graph: TaskGraph,
        work_order: WorkOrderV3,
        request_id: str,
        session_id: str,
        principal_id: str,
    ) -> NodeGrantBatch:
        """Return stable child grant refs or fail before creating authority."""

        with self._lock:
            return self._derive_locked(
                parent_grant_ref=parent_grant_ref,
                task_graph=task_graph,
                work_order=work_order,
                request_id=request_id,
                session_id=session_id,
                principal_id=principal_id,
            )

    def _derive_locked(
        self,
        *,
        parent_grant_ref: str,
        task_graph: TaskGraph,
        work_order: WorkOrderV3,
        request_id: str,
        session_id: str,
        principal_id: str,
    ) -> NodeGrantBatch:
        request = _required_text(request_id, "request_id")
        session = _required_text(session_id, "session_id")
        principal = _required_text(principal_id, "principal_id")
        parent_ref = _required_text(parent_grant_ref, "parent_grant_ref")
        if not isinstance(task_graph, TaskGraph) or not isinstance(work_order, WorkOrderV3):
            raise NodeGrantDerivationError(
                NODE_GRANT_INPUT_INVALID,
                "canonical TaskGraph and WorkOrderV3 are required",
            )
        task_graph.validate()
        work_order.validate()
        work_order_before = work_order.to_dict()
        now_ms = int(self._clock())
        if now_ms <= 0:
            raise NodeGrantDerivationError(NODE_GRANT_INPUT_INVALID, "clock must return a positive millisecond value")

        parent = self._resolve_parent(
            parent_ref=parent_ref,
            request_id=request,
            session_id=session,
            principal_id=principal,
            now_ms=now_ms,
        )
        parent_binding = durable_governor_binding(parent)
        specs, batch_id = _compile_specs(
            task_graph=task_graph,
            work_order=work_order,
            parent_ref=parent_ref,
            request_id=request,
            session_id=session,
            principal_id=principal,
            parent_seq=_integer(parent_binding.get("seq"), NODE_GRANT_PARENT_INVALID, "parent seq"),
        )

        # Atomic fail-closed preflight: unsupported routes are detected for the
        # entire graph before a single token is minted or row is written.
        unsupported = sorted({spec.route for spec in specs} - _ISSUABLE_NODE_ROUTES)
        if unsupported:
            raise NodeGrantDerivationError(
                NODE_GRANT_ROUTE_UNSUPPORTED,
                "node grant issuance is unsupported for route(s): " + ",".join(unsupported),
            )

        replay = self._load_replay(
            specs=specs,
            batch_id=batch_id,
            parent_ref=parent_ref,
            task_graph=task_graph,
            request_id=request,
            session_id=session,
            principal_id=principal,
            parent_scope=str(parent_binding.get("scope") or "").strip().lower(),
            now_ms=now_ms,
        )
        if replay is not None:
            if work_order.to_dict() != work_order_before:
                raise AssertionError("NodeBoundGrantService mutated WorkOrderV3")
            return replay

        parent_expiry = _integer(
            parent_binding.get("expires_at_ms"),
            NODE_GRANT_PARENT_INVALID,
            "parent expiry",
        )
        child_expiry = min(parent_expiry, now_ms + self._child_ttl_ms)
        if child_expiry <= now_ms:
            raise NodeGrantDerivationError(NODE_GRANT_EXPIRED, "child grant expiry is not in the future")
        parent_scope = str(parent_binding.get("scope") or "").strip().lower()

        staged: list[tuple[dict[str, Any], NodeBoundGrant]] = []
        for spec in specs:
            token_ref = self._token_service.issue_approval_token(
                intent_id=spec.intent_id,
                skill_name=spec.token_subject,
                session_id=session,
                seq=spec.seq,
                now_ts_ms=now_ms,
            )
            child_intent = _child_intent(
                spec=spec,
                task_graph=task_graph,
                parent_ref=parent_ref,
                request_id=request,
                session_id=session,
                principal_id=principal,
                parent_scope=parent_scope,
                now_ms=now_ms,
                expires_at_ms=child_expiry,
            )
            binding = build_governed_action_binding(
                intent_envelope=child_intent,
                approval_token_ref=token_ref,
                risk_level=spec.risk_level,
                issued_at_ms=now_ms,
                expires_at_ms=child_expiry,
            )
            node_authority = _node_authority(
                spec=spec,
                task_graph=task_graph,
                parent_ref=parent_ref,
                batch_id=batch_id,
                request_id=request,
                expires_at_ms=child_expiry,
            )
            binding["node_authority"] = deepcopy(node_authority)
            committed = compile_route_decision(
                approved=True,
                reason_code=_CHILD_REASON,
                committed_at_ms=now_ms,
                proposer_node_id="node_bound_grant_service",
                tier="full",
            )
            decision = {
                "request_id": request,
                "intent_id": spec.intent_id,
                "decision": "APPROVE",
                "reason_code": _CHILD_REASON,
                "rule_trace": [{
                    "gate_id": "DURABLE_PARENT_TASK_GRAPH_BINDING",
                    "hit": False,
                    "reason_hint": "EXACT_CANONICAL_NODE",
                }],
                "tokens": {"approval_token_ref": token_ref},
                "approved_action_binding": binding,
                "snapshot_refs": {
                    "parent_governor_grant_ref": parent_ref,
                    "node_grant": node_authority,
                    "node_intent": child_intent,
                },
                "stage": _CHILD_STAGE,
                "ts_ms": now_ms,
                "committed_route_token_ref": committed.token.value,
                "committed_route_token": {
                    "token": committed.token.value,
                    "reason_code": committed.reason_code,
                    "committed_at_ms": committed.committed_at_ms,
                    "proposer_node_id": committed.proposer_node_id,
                },
            }
            grant_ref = governor_grant_ref(request, decision)
            if grant_ref == parent_ref:
                raise NodeGrantDerivationError(NODE_GRANT_REPLAY_DRIFT, "child grant reused parent authority")
            staged.append((
                decision,
                NodeBoundGrant(
                    node_id=spec.node_id,
                    route=spec.route,
                    target_id=spec.target_id,
                    decision_id=spec.decision_id,
                    intent_id=spec.intent_id,
                    grant_ref=grant_ref,
                    expires_at_ms=child_expiry,
                    nonce=spec.nonce,
                ),
            ))

        # All validation and decision construction completed before durable
        # writes.  SQLiteLedger owns the canonical row encoding.
        for decision, _grant in staged:
            self._ledger.log_governor(decision)

        if work_order.to_dict() != work_order_before:
            raise AssertionError("NodeBoundGrantService mutated WorkOrderV3")
        return NodeGrantBatch(
            batch_id=batch_id,
            parent_grant_ref=parent_ref,
            task_graph_id=task_graph.graph_id,
            graph_revision=task_graph.revision,
            grants=tuple(grant for _decision, grant in staged),
            replayed=False,
        )

    def _resolve_parent(
        self,
        *,
        parent_ref: str,
        request_id: str,
        session_id: str,
        principal_id: str,
        now_ms: int,
    ) -> dict[str, Any]:
        try:
            decision = DurableGovernorGrantResolver(self._ledger).require(
                request_id=request_id,
                expected_ref=parent_ref,
            )
        except Exception as exc:
            raise NodeGrantDerivationError(NODE_GRANT_PARENT_INVALID, str(exc)) from exc
        binding = durable_governor_binding(decision)
        try:
            expires_at_ms = int(binding.get("expires_at_ms") or 0)
            seq = int(binding.get("seq"))
        except (TypeError, ValueError) as exc:
            raise NodeGrantDerivationError(NODE_GRANT_PARENT_INVALID, "parent binding integers are invalid") from exc
        if (
            str(binding.get("schema_version") or "") != GOVERNED_ACTION_BINDING_SCHEMA_VERSION
            or str(binding.get("action_kind") or "").strip() == ""
            or str(binding.get("intent_id") or "").strip() == ""
            or str(binding.get("session_id") or "").strip() != session_id
            or str(binding.get("principal_id") or "").strip() != principal_id
            or str(binding.get("scope") or "").strip() == ""
            or seq < 0
            or expires_at_ms <= now_ms
        ):
            raise NodeGrantDerivationError(
                NODE_GRANT_PARENT_INVALID,
                "durable parent action/session/principal/scope/expiry binding mismatch",
            )
        return decision

    def _load_replay(
        self,
        *,
        specs: tuple[_NodeSpec, ...],
        batch_id: str,
        parent_ref: str,
        task_graph: TaskGraph,
        request_id: str,
        session_id: str,
        principal_id: str,
        parent_scope: str,
        now_ms: int,
    ) -> NodeGrantBatch | None:
        trace = self._ledger.trace_request(request_id)
        rows = list(trace.get("governor_log") or []) if isinstance(trace, Mapping) else []
        expected_intents = {spec.intent_id for spec in specs}
        candidates: list[dict[str, Any]] = []
        for raw in rows:
            if not isinstance(raw, Mapping):
                continue
            row = dict(raw)
            snapshots = _mapping(row.get("snapshot_refs"))
            authority = _mapping(snapshots.get("node_grant"))
            if (
                str(row.get("intent_id") or "") in expected_intents
                or str(authority.get("batch_id") or "") == batch_id
            ):
                candidates.append(row)
        if not candidates:
            return None
        if len(candidates) != len(specs):
            raise NodeGrantDerivationError(
                NODE_GRANT_REPLAY_DRIFT,
                "durable child batch is partial or duplicated",
            )
        by_intent = {str(row.get("intent_id") or ""): row for row in candidates}
        if set(by_intent) != expected_intents or len(by_intent) != len(candidates):
            raise NodeGrantDerivationError(NODE_GRANT_REPLAY_DRIFT, "durable child identity set drifted")

        grants: list[NodeBoundGrant] = []
        for spec in specs:
            row = by_intent[spec.intent_id]
            snapshots = _mapping(row.get("snapshot_refs"))
            authority = _mapping(snapshots.get("node_grant"))
            binding = durable_governor_binding(row)
            tokens = _mapping(row.get("tokens"))
            token_ref = str(tokens.get("approval_token_ref") or "").strip()
            try:
                expires_at_ms = int(binding.get("expires_at_ms") or 0)
            except (TypeError, ValueError) as exc:
                raise NodeGrantDerivationError(NODE_GRANT_REPLAY_DRIFT, "durable child expiry is invalid") from exc
            expected_authority = _node_authority(
                spec=spec,
                task_graph=task_graph,
                parent_ref=parent_ref,
                batch_id=batch_id,
                request_id=request_id,
                expires_at_ms=expires_at_ms,
            )
            token_digest = (
                "sha256:" + hashlib.sha256(token_ref.encode("utf-8")).hexdigest()
                if token_ref
                else ""
            )
            if (
                str(row.get("decision") or "").strip().upper() != "APPROVE"
                or str(row.get("stage") or "") != _CHILD_STAGE
                or str(row.get("reason_code") or "") != _CHILD_REASON
                or not token_ref
                or authority != expected_authority
                or _mapping(binding.get("node_authority")) != expected_authority
                or str(binding.get("schema_version") or "") != GOVERNED_ACTION_BINDING_SCHEMA_VERSION
                or str(binding.get("intent_id") or "") != spec.intent_id
                or str(binding.get("session_id") or "") != session_id
                or int(binding.get("seq") or -1) != spec.seq
                or str(binding.get("action_kind") or "") != spec.action_kind
                or str(binding.get("scope") or "").strip().lower() != parent_scope
                or str(binding.get("principal_id") or "") != principal_id
                or str(binding.get("approval_token_digest") or "") != token_digest
            ):
                raise NodeGrantDerivationError(NODE_GRANT_REPLAY_DRIFT, f"durable child drifted: {spec.node_id}")
            if expires_at_ms <= now_ms:
                raise NodeGrantDerivationError(NODE_GRANT_EXPIRED, f"durable child expired: {spec.node_id}")
            ref = governor_grant_ref(request_id, row)
            if ref == parent_ref:
                raise NodeGrantDerivationError(NODE_GRANT_REPLAY_DRIFT, "child reused parent grant")
            grants.append(NodeBoundGrant(
                node_id=spec.node_id,
                route=spec.route,
                target_id=spec.target_id,
                decision_id=spec.decision_id,
                intent_id=spec.intent_id,
                grant_ref=ref,
                expires_at_ms=expires_at_ms,
                nonce=spec.nonce,
            ))
        return NodeGrantBatch(
            batch_id=batch_id,
            parent_grant_ref=parent_ref,
            task_graph_id=task_graph.graph_id,
            graph_revision=task_graph.revision,
            grants=tuple(grants),
            replayed=True,
        )


class DurableNodeGrantVerifier:
    """Verify one child decision without exposing its bearer token.

    Chat/delegate consumers must combine this exact durable projection with
    their canonical TaskRuntime attempt, active lease/fencing token and
    idempotency claim.  Those runtime checks are intentionally not fabricated
    here and remain outside this grant-only boundary.
    """

    def __init__(self, *, ledger: Any, clock: Callable[[], int] | None = None) -> None:
        if not callable(getattr(ledger, "trace_request", None)):
            raise TypeError("ledger must expose trace_request")
        self._ledger = ledger
        self._clock = clock or (lambda: int(time.time() * 1000))

    def verify(
        self,
        *,
        grant_ref: str,
        request_id: str,
        session_id: str,
        principal_id: str,
        graph_id: str,
        root_work_order_id: str,
        graph_revision: int,
        node_id: str,
        route: str,
        target_id: str,
        parent_grant_ref: str,
    ) -> VerifiedNodeGrant:
        expected = {
            "grant_ref": _verify_text(grant_ref, "grant_ref"),
            "request_id": _verify_text(request_id, "request_id"),
            "session_id": _verify_text(session_id, "session_id"),
            "principal_id": _verify_text(principal_id, "principal_id"),
            "graph_id": _verify_text(graph_id, "graph_id"),
            "root_work_order_id": _verify_text(root_work_order_id, "root_work_order_id"),
            "node_id": _verify_text(node_id, "node_id"),
            "route": _verify_text(route, "route").lower(),
            "target_id": _verify_text(target_id, "target_id"),
            "parent_grant_ref": _verify_text(parent_grant_ref, "parent_grant_ref"),
        }
        try:
            revision = int(graph_revision)
        except (TypeError, ValueError) as exc:
            raise NodeGrantVerificationError("graph_revision must be an integer") from exc
        if revision < 1 or expected["route"] not in _SUPPORTED_EXECUTABLE_ROUTES:
            raise NodeGrantVerificationError("graph revision or route is invalid")
        if expected["grant_ref"] == expected["parent_grant_ref"]:
            raise NodeGrantVerificationError("child grant must not reuse parent authority")
        now_ms = int(self._clock())

        resolver = DurableGovernorGrantResolver(self._ledger)
        try:
            decision = resolver.require(
                request_id=expected["request_id"],
                expected_ref=expected["grant_ref"],
            )
            parent = resolver.require(
                request_id=expected["request_id"],
                expected_ref=expected["parent_grant_ref"],
            )
        except Exception as exc:
            raise NodeGrantVerificationError("grant reference is not a durable approved decision") from exc

        parent_binding = durable_governor_binding(parent)
        try:
            parent_seq = int(parent_binding.get("seq"))
            parent_expiry = int(parent_binding.get("expires_at_ms") or 0)
        except (TypeError, ValueError) as exc:
            raise NodeGrantVerificationError("durable parent binding is malformed") from exc
        if (
            str(parent_binding.get("schema_version") or "") != GOVERNED_ACTION_BINDING_SCHEMA_VERSION
            or str(parent_binding.get("action_kind") or "").strip() == ""
            or str(parent_binding.get("session_id") or "") != expected["session_id"]
            or str(parent_binding.get("principal_id") or "") != expected["principal_id"]
            or parent_seq < 0
            or parent_expiry <= now_ms
        ):
            raise NodeGrantVerificationError(
                "durable parent binding mismatch or expiry",
                reason_code=(NODE_GRANT_EXPIRED if parent_expiry <= now_ms else NODE_GRANT_VERIFICATION_FAILED),
            )

        snapshots = _mapping(decision.get("snapshot_refs"))
        authority = _mapping(snapshots.get("node_grant"))
        bound_authority = _mapping(durable_governor_binding(decision).get("node_authority"))
        child_intent = _mapping(snapshots.get("node_intent"))
        tokens = _mapping(decision.get("tokens"))
        token_ref = str(tokens.get("approval_token_ref") or "").strip()
        binding = durable_governor_binding(decision)
        try:
            expiry = int(binding.get("expires_at_ms") or 0)
            ordinal = int(authority.get("ordinal") or 0)
        except (TypeError, ValueError) as exc:
            raise NodeGrantVerificationError("durable node binding integers are malformed") from exc
        if expiry <= now_ms:
            raise NodeGrantVerificationError(
                "durable node grant is expired",
                reason_code=NODE_GRANT_EXPIRED,
            )
        if expiry > parent_expiry or ordinal < 1:
            raise NodeGrantVerificationError("node expiry or ordinal exceeds parent authority")

        batch_id = _stable_ref("node-grant-batch", {
            "request_id": expected["request_id"],
            "session_id": expected["session_id"],
            "principal_id": expected["principal_id"],
            "parent_grant_ref": expected["parent_grant_ref"],
            "graph_id": expected["graph_id"],
            "graph_revision": revision,
            "root_work_order_id": expected["root_work_order_id"],
        })
        identity = {
            "batch_id": batch_id,
            "node_id": expected["node_id"],
            "route": expected["route"],
            "target_id": expected["target_id"],
            "ordinal": ordinal,
        }
        identity_digest = _digest(identity)
        decision_id = f"node-grant-decision://sha256/{identity_digest}"
        intent_id = f"node-grant-intent::{identity_digest}"
        nonce = f"nonce::node-grant::{identity_digest}"
        seq = parent_seq * 1_000_000 + ordinal
        action_kind = (
            expected["target_id"]
            if expected["route"] == "skill"
            else f"{expected['route']}:{expected['target_id']}"
        )
        token_subject = action_kind
        expected_authority = {
            "schema_version": "Natsume.task_graph.node_grant.binding.v1",
            "batch_id": batch_id,
            "decision_id": decision_id,
            "request_id": expected["request_id"],
            "graph_id": expected["graph_id"],
            "graph_revision": revision,
            "root_work_order_id": expected["root_work_order_id"],
            "node_id": expected["node_id"],
            "route": expected["route"],
            "target_id": expected["target_id"],
            "ordinal": ordinal,
            "seq": seq,
            "action_kind": action_kind,
            "token_subject": token_subject,
            "parent_governor_grant_ref": expected["parent_grant_ref"],
            "expires_at_ms": expiry,
            "nonce": nonce,
        }
        parent_scope = str(parent_binding.get("scope") or "").strip().lower()
        expected_args = {
            "request_id": expected["request_id"],
            "graph_id": expected["graph_id"],
            "graph_revision": revision,
            "root_work_order_id": expected["root_work_order_id"],
            "node_id": expected["node_id"],
            "route": expected["route"],
            "target_id": expected["target_id"],
            "action_kind": action_kind,
            "token_subject": token_subject,
            "parent_governor_grant_ref": expected["parent_grant_ref"],
            "expires_at_ms": expiry,
            "nonce": nonce,
        }
        expected_intent = {
            "schema_version": "Natsume.intent.v1",
            "id": intent_id,
            "ts_ms": int(decision.get("ts_ms") or 0),
            "session_id": expected["session_id"],
            "seq": seq,
            "person_id": expected["principal_id"],
            "principal_id": expected["principal_id"],
            "type": "query",
            "action": action_kind,
            "route": expected["route"],
            "target_id": expected["target_id"],
            "target": parent_scope,
            "args": expected_args,
            "nonce": nonce,
            "source": "node_bound_grant_service",
        }
        if (
            str(decision.get("request_id") or "") != expected["request_id"]
            or str(decision.get("decision") or "").strip().upper() != "APPROVE"
            or str(decision.get("stage") or "") != _CHILD_STAGE
            or str(decision.get("reason_code") or "") != _CHILD_REASON
            or str(snapshots.get("parent_governor_grant_ref") or "") != expected["parent_grant_ref"]
            or authority != expected_authority
            or bound_authority != expected_authority
            or child_intent != expected_intent
            or not token_ref
        ):
            raise NodeGrantVerificationError("durable decision/node authority/intent exact match failed")

        try:
            rebuilt_binding = build_governed_action_binding(
                intent_envelope=expected_intent,
                approval_token_ref=token_ref,
                risk_level=int(binding.get("risk_level") or 0),
                issued_at_ms=int(binding.get("issued_at_ms") or 0),
                expires_at_ms=expiry,
            )
        except (TypeError, ValueError) as exc:
            raise NodeGrantVerificationError("durable binding cannot be reconstructed") from exc
        rebuilt_binding["node_authority"] = deepcopy(expected_authority)
        if rebuilt_binding != binding:
            raise NodeGrantVerificationError("binding, nonce, intent or token digest exact match failed")

        return VerifiedNodeGrant(
            grant_ref=expected["grant_ref"],
            decision_id=decision_id,
            intent_id=intent_id,
            request_id=expected["request_id"],
            session_id=expected["session_id"],
            principal_id=expected["principal_id"],
            graph_id=expected["graph_id"],
            root_work_order_id=expected["root_work_order_id"],
            graph_revision=revision,
            node_id=expected["node_id"],
            route=expected["route"],
            target_id=expected["target_id"],
            parent_grant_ref=expected["parent_grant_ref"],
            expires_at_ms=expiry,
            nonce=nonce,
            action_kind=action_kind,
            token_subject=token_subject,
        )


def _compile_specs(
    *,
    task_graph: TaskGraph,
    work_order: WorkOrderV3,
    parent_ref: str,
    request_id: str,
    session_id: str,
    principal_id: str,
    parent_seq: int,
) -> tuple[tuple[_NodeSpec, ...], str]:
    if not task_graph.nodes:
        raise NodeGrantDerivationError(NODE_GRANT_INPUT_INVALID, "TaskGraph has no executable nodes")
    if (
        work_order.task_graph_id != task_graph.graph_id
        or work_order.graph_revision != task_graph.revision
        or work_order.root_work_order_id != task_graph.root_work_order_id
        or work_order.work_order_id != task_graph.root_work_order_id
        or work_order.approval_ref != parent_ref
        or work_order.task_node_id not in {node.node_id for node in task_graph.nodes}
    ):
        raise NodeGrantDerivationError(
            NODE_GRANT_INPUT_INVALID,
            "WorkOrderV3 graph/root/revision/node/parent authority lineage mismatch",
        )
    lineage = _mapping(work_order.metadata.get("planning_lineage"))
    if (
        str(lineage.get("graph_id") or "") != task_graph.graph_id
        or str(lineage.get("root_work_order_id") or "") != task_graph.root_work_order_id
        or _integer(lineage.get("revision"), NODE_GRANT_INPUT_INVALID, "lineage revision") != task_graph.revision
        or str(lineage.get("approval_ref") or "") != parent_ref
    ):
        raise NodeGrantDerivationError(NODE_GRANT_INPUT_INVALID, "planning lineage drifted from canonical graph")
    raw_bindings = lineage.get("node_bindings")
    if not isinstance(raw_bindings, list):
        raise NodeGrantDerivationError(NODE_GRANT_BINDING_MISSING, "planning node_bindings are required")
    bindings: dict[str, dict[str, Any]] = {}
    for raw in raw_bindings:
        if not isinstance(raw, Mapping):
            raise NodeGrantDerivationError(NODE_GRANT_BINDING_MISSING, "node binding must be an object")
        binding = dict(raw)
        node_id = str(binding.get("node_id") or "").strip()
        if not node_id or node_id in bindings:
            raise NodeGrantDerivationError(NODE_GRANT_BINDING_MISSING, "node bindings must have unique node_id")
        bindings[node_id] = binding
    graph_node_ids = {node.node_id for node in task_graph.nodes}
    if set(bindings) != graph_node_ids:
        raise NodeGrantDerivationError(
            NODE_GRANT_BINDING_MISSING,
            "node bindings must cover the executable graph exactly",
        )

    batch_id = _stable_ref("node-grant-batch", {
        "request_id": request_id,
        "session_id": session_id,
        "principal_id": principal_id,
        "parent_grant_ref": parent_ref,
        "graph_id": task_graph.graph_id,
        "graph_revision": task_graph.revision,
        "root_work_order_id": task_graph.root_work_order_id,
    })
    specs: list[_NodeSpec] = []
    for ordinal, node in enumerate(task_graph.nodes, start=1):
        route, separator, encoded_target = str(node.route or "").partition(":")
        route = route.strip().lower()
        target_id = unquote(encoded_target).strip() if separator else ""
        binding = bindings[node.node_id]
        bound_route = str(binding.get("worker_route") or "").strip().lower()
        bound_target = str(binding.get("target_id") or "").strip()
        if route not in _SUPPORTED_EXECUTABLE_ROUTES:
            raise NodeGrantDerivationError(NODE_GRANT_ROUTE_UNSUPPORTED, f"unsupported executable route: {route}")
        if not target_id or bound_route != route or bound_target != target_id:
            raise NodeGrantDerivationError(
                NODE_GRANT_ROUTE_DRIFT,
                f"TaskGraph route and WorkOrder binding differ for node: {node.node_id}",
            )
        identity = {
            "batch_id": batch_id,
            "node_id": node.node_id,
            "route": route,
            "target_id": target_id,
            "ordinal": ordinal,
        }
        digest = _digest(identity)
        # Skill executor validation is already deployed with target-only
        # subjects. Chat/delegate have no such compatibility constraint, so
        # both action and token subject bind route+target explicitly.
        action_kind = target_id if route == "skill" else f"{route}:{target_id}"
        token_subject = action_kind
        specs.append(_NodeSpec(
            node_id=node.node_id,
            route=route,
            target_id=target_id,
            risk_level=int(node.risk_level),
            ordinal=ordinal,
            decision_id=f"node-grant-decision://sha256/{digest}",
            intent_id=f"node-grant-intent::{digest}",
            nonce=f"nonce::node-grant::{digest}",
            seq=parent_seq * 1_000_000 + ordinal,
            action_kind=action_kind,
            token_subject=token_subject,
        ))
    return tuple(specs), batch_id


def _node_authority(
    *,
    spec: _NodeSpec,
    task_graph: TaskGraph,
    parent_ref: str,
    batch_id: str,
    request_id: str,
    expires_at_ms: int,
) -> dict[str, Any]:
    return {
        "schema_version": "Natsume.task_graph.node_grant.binding.v1",
        "batch_id": batch_id,
        "decision_id": spec.decision_id,
        "request_id": request_id,
        "graph_id": task_graph.graph_id,
        "graph_revision": task_graph.revision,
        "root_work_order_id": task_graph.root_work_order_id,
        "node_id": spec.node_id,
        "route": spec.route,
        "target_id": spec.target_id,
        "ordinal": spec.ordinal,
        "seq": spec.seq,
        "action_kind": spec.action_kind,
        "token_subject": spec.token_subject,
        "parent_governor_grant_ref": parent_ref,
        "expires_at_ms": int(expires_at_ms),
        "nonce": spec.nonce,
    }


def _child_intent(
    *,
    spec: _NodeSpec,
    task_graph: TaskGraph,
    parent_ref: str,
    request_id: str,
    session_id: str,
    principal_id: str,
    parent_scope: str,
    now_ms: int,
    expires_at_ms: int,
) -> dict[str, Any]:
    return {
        "schema_version": "Natsume.intent.v1",
        "id": spec.intent_id,
        "ts_ms": int(now_ms),
        "session_id": session_id,
        "seq": spec.seq,
        "person_id": principal_id,
        "principal_id": principal_id,
        "type": "query",
        "action": spec.action_kind,
        "route": spec.route,
        "target_id": spec.target_id,
        "target": parent_scope,
        "args": {
            "request_id": request_id,
            "graph_id": task_graph.graph_id,
            "graph_revision": task_graph.revision,
            "root_work_order_id": task_graph.root_work_order_id,
            "node_id": spec.node_id,
            "route": spec.route,
            "target_id": spec.target_id,
            "action_kind": spec.action_kind,
            "token_subject": spec.token_subject,
            "parent_governor_grant_ref": parent_ref,
            "expires_at_ms": int(expires_at_ms),
            "nonce": spec.nonce,
        },
        "nonce": spec.nonce,
        "source": "node_bound_grant_service",
    }


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(decoded) if isinstance(decoded, Mapping) else {}
    return {}


def _required_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise NodeGrantDerivationError(NODE_GRANT_INPUT_INVALID, f"{field_name} is required")
    return text


def _verify_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise NodeGrantVerificationError(f"{field_name} is required")
    return text


def _integer(value: Any, reason_code: str, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise NodeGrantDerivationError(reason_code, f"{field_name} must be an integer") from exc


def _digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _stable_ref(kind: str, value: Mapping[str, Any]) -> str:
    return f"{kind}://sha256/{_digest(value)}"


__all__ = [
    "NODE_GRANT_BINDING_MISSING",
    "NODE_GRANT_EXPIRED",
    "NODE_GRANT_INPUT_INVALID",
    "NODE_GRANT_PARENT_INVALID",
    "NODE_GRANT_REPLAY_DRIFT",
    "NODE_GRANT_ROUTE_DRIFT",
    "NODE_GRANT_ROUTE_UNSUPPORTED",
    "NODE_GRANT_VERIFICATION_FAILED",
    "DurableNodeGrantVerifier",
    "NodeBoundGrant",
    "NodeBoundGrantService",
    "NodeGrantBatch",
    "NodeGrantDerivationError",
    "NodeGrantVerificationError",
    "VerifiedNodeGrant",
]
