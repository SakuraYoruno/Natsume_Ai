"""TaskGraph child grants; only canonical inputs cross the Governor boundary."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import threading
import time
from typing import Any, Callable

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
from host_brain.orchestration.node_grant_codec import (
    digest as _digest,
    mapping as _mapping,
    stable_ref as _stable_ref,
)
from host_brain.orchestration.node_grant_errors import (
    NODE_GRANT_BINDING_MISSING,
    NODE_GRANT_EXPIRED,
    NODE_GRANT_INPUT_INVALID,
    NODE_GRANT_PARENT_INVALID,
    NODE_GRANT_REPLAY_DRIFT,
    NODE_GRANT_ROUTE_DRIFT,
    NODE_GRANT_ROUTE_UNSUPPORTED,
    NODE_GRANT_VERIFICATION_FAILED,
    NodeGrantDerivationError,
    NodeGrantVerificationError,
)


from host_brain.orchestration.node_grant_compiler import (
    _ISSUABLE_NODE_ROUTES,
    _MAX_EXECUTION_GRANT_TTL_MS,
    _NodeSpec,
    _SUPPORTED_EXECUTABLE_ROUTES,
    _child_intent,
    _compile_specs,
    _execution_grant_ttl_ms,
    _integer,
    _node_authority,
    _required_text,
    _verify_text,
)

_CHILD_STAGE = "S_TASK_GRAPH_NODE_GRANT"
_CHILD_REASON = "TASK_GRAPH_NODE_DERIVED_APPROVE"
_REVIEW_RENEWAL_REASON = "TASK_GRAPH_NODE_REVIEW_CONTINUATION_APPROVE"
_REVIEW_RENEWAL_SCHEMA = "Natsume.task.review_continuation_authority.v1"


# 暴露给运行时的仅是无秘密的子决策投影；令牌材料不通过该对象或日志泄漏。
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
    review_continuation_authority: dict[str, Any] | None = None

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
            "review_continuation_authority": (
                None
                if self.review_continuation_authority is None
                else dict(self.review_continuation_authority)
            ),
        }

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
        review_store: Any | None = None,
        clock: Callable[[], int] | None = None,
        child_ttl_ms: int = 60_000,
    ) -> None:
        if not callable(getattr(ledger, "trace_request", None)):
            raise TypeError("ledger must expose trace_request")
        if not callable(getattr(ledger, "log_governor_batch", None)):
            raise TypeError("ledger must expose atomic log_governor_batch")
        if not callable(getattr(token_service, "issue_approval_token", None)):
            raise TypeError("token_service must expose issue_approval_token")
        ttl = int(child_ttl_ms)
        if ttl <= 0:
            raise ValueError("child_ttl_ms must be positive")
        self._ledger = ledger
        self._token_service = token_service
        self._review_store = review_store
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

        # 整批派生在同一锁内进行，先验证所有绑定再发放，避免只为部分节点留下可执行权威。
        with self._lock:
            return self._derive_locked(
                parent_grant_ref=parent_grant_ref,
                task_graph=task_graph,
                work_order=work_order,
                request_id=request_id,
                session_id=session_id,
                principal_id=principal_id,
            )

    def validate_parent_binding(
        self,
        *,
        parent_grant_ref: str,
        request_id: str,
        session_id: str,
        principal_id: str,
    ) -> Mapping[str, Any]:
        """Fail closed before command admission when parent identity drifts.

        This is intentionally read-only: child grants are still issued only
        after durable command admission, but an impossible request/session/
        principal/expiry tuple must never leave an admitted graph behind.
        ``derive`` repeats the same check to preserve the live authority fence
        across the admission-to-issuance race.
        """

        with self._lock:
            request = _required_text(request_id, "request_id")
            session = _required_text(session_id, "session_id")
            principal = _required_text(principal_id, "principal_id")
            parent_ref = _required_text(parent_grant_ref, "parent_grant_ref")
            now_ms = int(self._clock())
            if now_ms <= 0:
                raise NodeGrantDerivationError(
                    NODE_GRANT_INPUT_INVALID,
                    "clock must return a positive millisecond value",
                )
            return self._resolve_parent(
                parent_ref=parent_ref,
                request_id=request,
                session_id=session,
                principal_id=principal,
                now_ms=now_ms,
            )

    def derive_review_continuation(
        self,
        *,
        parent_grant_ref: str,
        task_graph: TaskGraph,
        work_order: WorkOrderV3,
        request_id: str,
        session_id: str,
        principal_id: str,
        continuation_claim: Mapping[str, Any],
    ) -> NodeGrantBatch:
        """Mint one short-lived batch bound to a live Review worker claim.

        The immutable HostCommand remains the parent business authority.  The
        consumed Review and the leased 0096 claim only renew its execution
        credential after a human pause; neither can change graph, route,
        target, principal, or WorkOrder lineage.
        """

        with self._lock:
            return self._derive_review_continuation_locked(
                parent_grant_ref=parent_grant_ref,
                task_graph=task_graph,
                work_order=work_order,
                request_id=request_id,
                session_id=session_id,
                principal_id=principal_id,
                continuation_claim=continuation_claim,
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
            # Derivation may be reopening an already-durable batch solely to
            # project an exact replay. An expired parent can never mint a new
            # batch; that check runs after the replay lookup below.
            allow_expired=True,
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
        if parent_expiry <= now_ms:
            raise NodeGrantDerivationError(
                NODE_GRANT_EXPIRED,
                "expired parent grant cannot derive a new node-grant batch",
            )
        execution_ttl_ms = _execution_grant_ttl_ms(
            work_order=work_order,
            minimum_ttl_ms=self._child_ttl_ms,
        )
        child_expiry = now_ms + execution_ttl_ms
        parent_scope = str(parent_binding.get("scope") or "").strip().lower()

        staged: list[tuple[dict[str, Any], NodeBoundGrant]] = []
        for spec in specs:
            token_ref = self._token_service.issue_approval_token(
                intent_id=spec.intent_id,
                skill_name=spec.token_subject,
                session_id=session,
                seq=spec.seq,
                now_ts_ms=now_ms,
                ttl_ms=execution_ttl_ms,
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
        inserted = self._persist_grant_batch(staged)
        if not inserted:
            concurrent_replay = self._load_replay(
                specs=specs,
                batch_id=batch_id,
                parent_ref=parent_ref,
                task_graph=task_graph,
                request_id=request,
                session_id=session,
                principal_id=principal,
                parent_scope=parent_scope,
                now_ms=now_ms,
            )
            if concurrent_replay is None:
                raise NodeGrantDerivationError(
                    NODE_GRANT_REPLAY_DRIFT,
                    "concurrent node-grant batch winner is not durably readable",
                )
            return concurrent_replay

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

    def _derive_review_continuation_locked(
        self,
        *,
        parent_grant_ref: str,
        task_graph: TaskGraph,
        work_order: WorkOrderV3,
        request_id: str,
        session_id: str,
        principal_id: str,
        continuation_claim: Mapping[str, Any],
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
            raise NodeGrantDerivationError(
                NODE_GRANT_INPUT_INVALID,
                "clock must return a positive millisecond value",
            )
        store = self._review_store
        validate_authority = getattr(
            store,
            "validate_review_continuation_authority",
            None,
        )
        if not callable(validate_authority):
            raise NodeGrantDerivationError(
                NODE_GRANT_PARENT_INVALID,
                "Review continuation grant renewal has no canonical authority validator",
            )
        try:
            # The background worker intentionally strips the raw claim token
            # before crossing into a surface runtime.  Renewal therefore
            # revalidates the digest/owner/fence projection, never a secret
            # token that the runtime must not receive.
            authority_raw = validate_authority(dict(continuation_claim or {}))
        except Exception as exc:
            raise NodeGrantDerivationError(
                NODE_GRANT_PARENT_INVALID,
                "Review continuation authority is not canonical or currently owned",
            ) from exc
        authority = dict(authority_raw) if isinstance(authority_raw, Mapping) else {}
        if (
            str(authority.get("schema_version") or "") != _REVIEW_RENEWAL_SCHEMA
            or str(authority.get("review_status") or "").strip().upper() != "CONSUMED"
            or str(authority.get("graph_id") or "").strip() != task_graph.graph_id
            or int(authority.get("graph_revision") or 0) != task_graph.revision
            or str(authority.get("node_id") or "").strip()
            not in {node.node_id for node in task_graph.nodes}
        ):
            raise NodeGrantDerivationError(
                NODE_GRANT_PARENT_INVALID,
                "Review continuation authority differs from the admitted Graph",
            )

        parent = self._resolve_parent(
            parent_ref=parent_ref,
            request_id=request,
            session_id=session,
            principal_id=principal,
            now_ms=now_ms,
            allow_expired=True,
        )
        parent_binding = durable_governor_binding(parent)
        specs, batch_id = _compile_specs(
            task_graph=task_graph,
            work_order=work_order,
            parent_ref=parent_ref,
            request_id=request,
            session_id=session,
            principal_id=principal,
            parent_seq=_integer(
                parent_binding.get("seq"),
                NODE_GRANT_PARENT_INVALID,
                "parent seq",
            ),
            review_continuation_authority=authority,
        )
        unsupported = sorted({spec.route for spec in specs} - _ISSUABLE_NODE_ROUTES)
        if unsupported:
            raise NodeGrantDerivationError(
                NODE_GRANT_ROUTE_UNSUPPORTED,
                "node grant issuance is unsupported for route(s): " + ",".join(unsupported),
            )
        parent_scope = str(parent_binding.get("scope") or "").strip().lower()
        replay = self._load_replay(
            specs=specs,
            batch_id=batch_id,
            parent_ref=parent_ref,
            task_graph=task_graph,
            request_id=request,
            session_id=session,
            principal_id=principal,
            parent_scope=parent_scope,
            now_ms=now_ms,
            review_continuation_authority=authority,
            expected_reason=_REVIEW_RENEWAL_REASON,
        )
        if replay is not None:
            if work_order.to_dict() != work_order_before:
                raise AssertionError("NodeBoundGrantService mutated WorkOrderV3")
            return replay

        # The live claim is revalidated by every worker-side verifier.  A
        # credential may outlive one heartbeat interval, but it is unusable as
        # soon as claim ownership expires or moves to another fencing token.
        budget_time_ms = (
            int(work_order.budget_policy.limits.time_ms)
            if work_order.budget_policy is not None
            else 0
        )
        if budget_time_ms > _MAX_EXECUTION_GRANT_TTL_MS:
            raise NodeGrantDerivationError(
                NODE_GRANT_INPUT_INVALID,
                "Review continuation execution budget exceeds the 24-hour grant ceiling",
            )
        execution_ttl_ms = min(
            _MAX_EXECUTION_GRANT_TTL_MS,
            max(
                self._child_ttl_ms,
                budget_time_ms + self._child_ttl_ms,
            ),
        )
        child_expiry = now_ms + execution_ttl_ms
        staged: list[tuple[dict[str, Any], NodeBoundGrant]] = []
        for spec in specs:
            token_ref = self._token_service.issue_approval_token(
                intent_id=spec.intent_id,
                skill_name=spec.token_subject,
                session_id=session,
                seq=spec.seq,
                now_ts_ms=now_ms,
                ttl_ms=execution_ttl_ms,
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
                review_continuation_authority=authority,
            )
            node_authority = _node_authority(
                spec=spec,
                task_graph=task_graph,
                parent_ref=parent_ref,
                batch_id=batch_id,
                request_id=request,
                expires_at_ms=child_expiry,
                review_continuation_authority=authority,
            )
            binding = build_governed_action_binding(
                intent_envelope=child_intent,
                approval_token_ref=token_ref,
                risk_level=spec.risk_level,
                issued_at_ms=now_ms,
                expires_at_ms=child_expiry,
            )
            binding["node_authority"] = deepcopy(node_authority)
            committed = compile_route_decision(
                approved=True,
                reason_code=_REVIEW_RENEWAL_REASON,
                committed_at_ms=now_ms,
                proposer_node_id="node_bound_grant_service",
                tier="full",
            )
            decision = {
                "request_id": request,
                "intent_id": spec.intent_id,
                "decision": "APPROVE",
                "reason_code": _REVIEW_RENEWAL_REASON,
                "rule_trace": [{
                    "gate_id": "DURABLE_REVIEW_CONTINUATION_BINDING",
                    "hit": False,
                    "reason_hint": "EXACT_LIVE_REVIEW_CLAIM",
                }],
                "tokens": {"approval_token_ref": token_ref},
                "approved_action_binding": binding,
                "snapshot_refs": {
                    "parent_governor_grant_ref": parent_ref,
                    "node_grant": node_authority,
                    "node_intent": child_intent,
                    "review_continuation_authority": deepcopy(authority),
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
                raise NodeGrantDerivationError(
                    NODE_GRANT_REPLAY_DRIFT,
                    "Review-renewed child grant reused parent authority",
                )
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
        inserted = self._persist_grant_batch(staged)
        if not inserted:
            concurrent_replay = self._load_replay(
                specs=specs,
                batch_id=batch_id,
                parent_ref=parent_ref,
                task_graph=task_graph,
                request_id=request,
                session_id=session,
                principal_id=principal,
                parent_scope=parent_scope,
                now_ms=now_ms,
                review_continuation_authority=authority,
                expected_reason=_REVIEW_RENEWAL_REASON,
            )
            if concurrent_replay is None:
                raise NodeGrantDerivationError(
                    NODE_GRANT_REPLAY_DRIFT,
                    "concurrent Review grant batch winner is not durably readable",
                )
            return concurrent_replay
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

    def _persist_grant_batch(
        self,
        staged: list[tuple[dict[str, Any], NodeBoundGrant]],
    ) -> bool:
        append_batch = getattr(self._ledger, "log_governor_batch", None)
        if not callable(append_batch):
            raise NodeGrantDerivationError(
                NODE_GRANT_PARENT_INVALID,
                "Governor ledger lacks atomic node-grant batch append",
            )
        result = append_batch([decision for decision, _grant in staged])
        if result not in {None, True, False}:
            raise NodeGrantDerivationError(
                NODE_GRANT_REPLAY_DRIFT,
                "Governor batch append returned an invalid result",
            )
        # ``None`` is retained only for legacy/in-memory test ledgers.  The
        # production SQLite ledger returns False when another process won the
        # deterministic batch race.
        return result is not False

    def _resolve_parent(
        self,
        *,
        parent_ref: str,
        request_id: str,
        session_id: str,
        principal_id: str,
        now_ms: int,
        allow_expired: bool = False,
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
        ):
            raise NodeGrantDerivationError(
                NODE_GRANT_PARENT_INVALID,
                "durable parent action/session/principal/scope binding mismatch",
            )
        if expires_at_ms <= now_ms and not allow_expired:
            raise NodeGrantDerivationError(
                NODE_GRANT_EXPIRED,
                "durable parent grant expired",
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
        review_continuation_authority: Mapping[str, Any] | None = None,
        expected_reason: str = _CHILD_REASON,
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
                review_continuation_authority=review_continuation_authority,
            )
            token_digest = (
                "sha256:" + hashlib.sha256(token_ref.encode("utf-8")).hexdigest()
                if token_ref
                else ""
            )
            if (
                str(row.get("decision") or "").strip().upper() != "APPROVE"
                or str(row.get("stage") or "") != _CHILD_STAGE
                or str(row.get("reason_code") or "") != expected_reason
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
                or (
                    dict(_mapping(snapshots.get("review_continuation_authority")))
                    != dict(review_continuation_authority or {})
                )
            ):
                raise NodeGrantDerivationError(NODE_GRANT_REPLAY_DRIFT, f"durable child drifted: {spec.node_id}")
            # Reopen the immutable child identity even after expiry so a
            # terminal Task receipt can be replayed exactly.  This is not
            # execution authority: DurableNodeGrantVerifier still rejects an
            # expired child before claim/worker mutation.
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

    def __init__(
        self,
        *,
        ledger: Any,
        review_store: Any | None = None,
        clock: Callable[[], int] | None = None,
    ) -> None:
        if not callable(getattr(ledger, "trace_request", None)):
            raise TypeError("ledger must expose trace_request")
        self._ledger = ledger
        self._review_store = review_store
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
        attempt_id: str | None = None,
        lease_id: str | None = None,
        lease_owner_id: str | None = None,
        fencing_token: int | None = None,
        _claim_phase: bool = False,
    ) -> VerifiedNodeGrant:
        # 验证从请求、主体、图修订到节点目标逐项闭合；child Grant 绝不能冒充或复用 parent Grant。
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

        snapshots = _mapping(decision.get("snapshot_refs"))
        review_authority = _mapping(
            snapshots.get("review_continuation_authority")
        )
        review_renewal = bool(review_authority)
        if review_renewal:
            # 人工恢复续发的授权还要由 ReviewStore 证明消费状态和当前租约，防止旧审查单反复解锁执行。
            validate_authority = getattr(
                self._review_store,
                (
                    "validate_review_continuation_authority"
                    if _claim_phase
                    else "validate_review_continuation_execution_authority"
                ),
                None,
            )
            if not callable(validate_authority):
                raise NodeGrantVerificationError(
                    "Review-renewed node grant has no execution-authority validator"
                )
            try:
                if _claim_phase:
                    canonical_authority = validate_authority(review_authority)
                else:
                    canonical_authority = validate_authority(
                        review_authority,
                        attempt_id=_verify_text(attempt_id, "attempt_id"),
                        lease_id=_verify_text(lease_id, "lease_id"),
                        lease_owner_id=_verify_text(
                            lease_owner_id,
                            "lease_owner_id",
                        ),
                        fencing_token=fencing_token,
                        node_id=expected["node_id"],
                    )
            except Exception as exc:
                raise NodeGrantVerificationError(
                    "Review continuation claim is no longer live or exact"
                ) from exc
            if (
                not isinstance(canonical_authority, Mapping)
                or dict(canonical_authority) != review_authority
                or str(review_authority.get("schema_version") or "")
                != _REVIEW_RENEWAL_SCHEMA
                or str(review_authority.get("review_status") or "").strip().upper()
                != "CONSUMED"
            ):
                raise NodeGrantVerificationError(
                    "Review continuation authority drifted from canonical state"
                )

        parent_binding = durable_governor_binding(parent)
        try:
            parent_seq = int(parent_binding.get("seq"))
            parent_expiry = int(parent_binding.get("expires_at_ms") or 0)
            parent_issued_at_ms = int(parent_binding.get("issued_at_ms") or 0)
        except (TypeError, ValueError) as exc:
            raise NodeGrantVerificationError("durable parent binding is malformed") from exc
        if (
            str(parent_binding.get("schema_version") or "") != GOVERNED_ACTION_BINDING_SCHEMA_VERSION
            or str(parent_binding.get("action_kind") or "").strip() == ""
            or str(parent_binding.get("session_id") or "") != expected["session_id"]
            or str(parent_binding.get("principal_id") or "") != expected["principal_id"]
            or parent_seq < 0
            or parent_issued_at_ms <= 0
            or parent_expiry <= parent_issued_at_ms
        ):
            raise NodeGrantVerificationError(
                "durable parent binding mismatch",
                reason_code=NODE_GRANT_VERIFICATION_FAILED,
            )

        authority = _mapping(snapshots.get("node_grant"))
        bound_authority = _mapping(durable_governor_binding(decision).get("node_authority"))
        child_intent = _mapping(snapshots.get("node_intent"))
        tokens = _mapping(decision.get("tokens"))
        token_ref = str(tokens.get("approval_token_ref") or "").strip()
        binding = durable_governor_binding(decision)
        try:
            expiry = int(binding.get("expires_at_ms") or 0)
            issued_at_ms = int(binding.get("issued_at_ms") or 0)
            ordinal = int(authority.get("ordinal") or 0)
        except (TypeError, ValueError) as exc:
            raise NodeGrantVerificationError("durable node binding integers are malformed") from exc
        # Grant 过期在 READY claim 之前即拒绝；持久图可能仍在，但它不自动延长可执行权限。
        if expiry <= now_ms:
            raise NodeGrantVerificationError(
                "durable node grant is expired",
                reason_code=NODE_GRANT_EXPIRED,
            )
        # 账本记录、重建的 Intent、绑定摘要和 token 引用必须完全一致，任一漂移都按拒绝处理。
        if (
            issued_at_ms <= 0
            or expiry <= issued_at_ms
            or expiry - issued_at_ms > _MAX_EXECUTION_GRANT_TTL_MS
            or ordinal < 1
            or int(decision.get("ts_ms") or 0) != issued_at_ms
            or (
                not review_renewal
                and not (parent_issued_at_ms <= issued_at_ms < parent_expiry)
            )
        ):
            raise NodeGrantVerificationError(
                "node grant issuance window, expiry or ordinal is invalid"
            )

        batch_identity: dict[str, Any] = {
            "request_id": expected["request_id"],
            "session_id": expected["session_id"],
            "principal_id": expected["principal_id"],
            "parent_grant_ref": expected["parent_grant_ref"],
            "graph_id": expected["graph_id"],
            "graph_revision": revision,
            "root_work_order_id": expected["root_work_order_id"],
        }
        if review_renewal:
            batch_identity["review_continuation_authority"] = review_authority
        batch_id = _stable_ref("node-grant-batch", batch_identity)
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
        token_subject = (
            expected["target_id"]
            if expected["route"] in {"skill", "tool"}
            else action_kind
        )
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
        if review_renewal:
            expected_authority["review_continuation_authority"] = deepcopy(
                review_authority
            )
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
        if review_renewal:
            expected_args["review_continuation_authority"] = deepcopy(
                review_authority
            )
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
            "source": (
                "node_bound_grant_service.review_continuation"
                if review_renewal
                else "node_bound_grant_service"
            ),
        }
        if (
            str(decision.get("request_id") or "") != expected["request_id"]
            or str(decision.get("decision") or "").strip().upper() != "APPROVE"
            or str(decision.get("stage") or "") != _CHILD_STAGE
            or str(decision.get("reason_code") or "")
            != (_REVIEW_RENEWAL_REASON if review_renewal else _CHILD_REASON)
            or str(snapshots.get("parent_governor_grant_ref") or "") != expected["parent_grant_ref"]
            or authority != expected_authority
            or bound_authority != expected_authority
            or child_intent != expected_intent
            or not token_ref
            or (
                _mapping(snapshots.get("review_continuation_authority"))
                != review_authority
            )
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
            review_continuation_authority=(
                dict(review_authority) if review_renewal else None
            ),
        )

    def verify_for_claim(self, **kwargs: Any) -> VerifiedNodeGrant:
        """Verify immutable grant authority before the READY claim transaction."""

        # claim 阶段使用更严格的 Review 校验，确保授权仍与即将领取的执行世代匹配。
        return self.verify(**kwargs, _claim_phase=True)
