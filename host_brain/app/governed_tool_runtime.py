"""ToolCatalog 表面的受治理 HTTP 接入与冻结执行边界。

HTTP 端只验证 Secure Ingress、构造规范 intent 并交给 Host Runtime；真正的
handler 调用只能由冻结 Tool TaskNode 发起。执行前会重开实时节点/租约/grant
并对比入图时的绑定快照，结果再剔除敏感字段后投影为可持久化终态，避免
HTTP 重试、目录变更或客户端参数直接成为工具执行权限。

The public ``/tools/execute`` adapter is deliberately absent from this module.
It may only hand an authenticated request to :class:`GovernedToolHttpApplication`.
The frozen TaskNode executor owns an app-local Tool execution port. The raw
Broker cannot execute from a resolved binding; the port reopens the exact
graph/node/attempt/lease/grant authority immediately before handler dispatch.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
import re
import time
from typing import Any

from host_brain.governor.route_token import (
    RouteTokenDisposition,
    validate_governor_route_token,
)
from host_brain.orchestration.governor_grant import (
    DurableGovernorGrantResolver,
    durable_governor_binding,
)
from host_brain.skills.capability_scorecard import canonical_risk_from_tool_meta
from host_brain.task_runtime.governed_workers import FrozenNodeInput
from host_brain.tools.broker import RESULT_SUCCESS, ToolExecutionResult


GOVERNED_TOOL_INGRESS_REJECTED = "GOVERNED_TOOL_INGRESS_REJECTED"
GOVERNED_TOOL_AUTHORITY_INVALID = "GOVERNED_TOOL_AUTHORITY_INVALID"
GOVERNED_TOOL_GOVERNOR_REJECTED = "GOVERNED_TOOL_GOVERNOR_REJECTED"
GOVERNED_TOOL_REVIEW_REQUIRED = "GOVERNED_TOOL_REVIEW_REQUIRED"
GOVERNED_TOOL_REVIEW_REJECTED = "GOVERNED_TOOL_REVIEW_REJECTED"
GOVERNED_TOOL_REVIEW_EXPIRED = "GOVERNED_TOOL_REVIEW_EXPIRED"
GOVERNED_TOOL_RUNTIME_UNAVAILABLE = "GOVERNED_TOOL_RUNTIME_UNAVAILABLE"
GOVERNED_TOOL_RESULT_INVALID = "GOVERNED_TOOL_RESULT_INVALID"
GOVERNED_TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
GOVERNED_TOOL_RECONCILIATION_REQUIRED = "GOVERNED_TOOL_RECONCILIATION_REQUIRED"

_SENSITIVE_KEY = re.compile(
    r"(?:^|_)(?:api_?key|authorization|bearer|cookie|credential|password|"
    r"secret|token)(?:$|_)",
    re.IGNORECASE,
)


class GovernedToolExecutionError(RuntimeError):
    """HTTP 适配器投影的类型化、无密钥失败。

    ``detail`` 仅是稳定公共错误文案；内部异常、token 与绑定细节不穿过
    Tool API，防止拒绝路径变成授权或环境信息的旁路。
    """

    def __init__(self, status_code: int, reason_code: str, detail: str) -> None:
        self.status_code = int(status_code)
        self.reason_code = str(reason_code or GOVERNED_TOOL_RESULT_INVALID)
        self.detail = str(detail or "governed tool execution failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


@dataclass(frozen=True, slots=True)
class _ToolGovernorMeta:
    """供官方 Governor 消费的只读 Tool 元数据投影，不包含 handler。"""

    skill_name: str
    risk_level: int
    scope: str
    reversible: bool
    require_confirm: bool
    clearance: str


class ToolGovernanceRegistry:
    """仅暴露治理所需元数据的目录视图，绝不暴露 Tool handler/执行端口。

    WRITE 在缺少回滚证明时按不可逆处理，促使 Governor/AIRLOCK 使用保守
    路径；此处的风险结论来自目录，不信任 HTTP body 自报。
    """

    def __init__(self, catalog: Any) -> None:
        if not callable(getattr(catalog, "get", None)):
            raise TypeError("catalog must expose get")
        if not callable(getattr(catalog, "list_all", None)):
            raise TypeError("catalog must expose list_all")
        self._catalog = catalog

    def list_skill_names(self) -> list[str]:
        return [str(meta.id) for meta in self._catalog.list_all()]

    def get(self, capability_id: str) -> _ToolGovernorMeta | None:
        try:
            meta = self._catalog.get(str(capability_id or "").strip())
        except KeyError:
            return None
        risk = canonical_risk_from_tool_meta(meta).canonical_risk
        # ToolCatalog does not carry a rollback proof.  Treat WRITE as
        # irreversible for routing purposes; with production maturity enabled
        # it remains AIRLOCK until real Lab/Review evidence promotes it.
        reversible = str(meta.side_effect_level).upper() == "READ"
        return _ToolGovernorMeta(
            skill_name=str(meta.id),
            risk_level=int(risk),
            scope="host",
            reversible=reversible,
            require_confirm=str(meta.auth_level).upper() == "CONFIRM",
            clearance="HIGH" if risk >= 4 else "MEDIUM" if risk >= 3 else "LOW",
        )


class DurableToolTaskAuthorityVerifier:
    """在 handler 调用瞬间重开规范 Task 与节点 grant 授权。

    它同时检查 reservation/业务范围/幂等指纹的实时有效性，以及尝试、租约
    所有者和 fencing token。只要节点已被接管、取消或 grant 漂移，就返回
    false/抛错，避免过期 worker 继续运行工具。
    """

    def __init__(self, *, task_store: Any, node_grants: Any) -> None:
        if not callable(
            getattr(task_store, "assert_live_node_execution_authority", None)
        ):
            raise TypeError(
                "task_store.assert_live_node_execution_authority is required"
            )
        if not callable(getattr(node_grants, "verify", None)):
            raise TypeError("node_grants.verify is required")
        self._task_store = task_store
        self._node_grants = node_grants

    def verify(
        self,
        *,
        task_authority: Any,
        request_id: str | None,
        intent_id: str,
        session_id: str,
        tool_id: str,
    ) -> bool:
        """验证冻结节点与 intent/tool 的完整绑定；不接受部分身份匹配。"""
        frozen = task_authority
        if (
            not isinstance(frozen, FrozenNodeInput)
            or frozen.route != "tool"
            or frozen.request_id != str(request_id or "")
            or frozen.session_id != str(session_id or "")
            or frozen.target_id != str(tool_id or "")
            or not frozen.reservation_id
            or not frozen.business_scope
            or not frozen.operation_fingerprint
        ):
            return False
        self._task_store.assert_live_node_execution_authority(
            reservation_id=frozen.reservation_id,
            business_scope=frozen.business_scope,
            operation_fingerprint=frozen.operation_fingerprint,
            graph_id=frozen.graph_id,
            graph_revision=frozen.graph_revision,
            node_id=frozen.node_id,
            attempt_id=frozen.attempt_id,
            lease_id=frozen.lease_id,
            lease_owner_id=frozen.lease_owner_id,
            fencing_token=frozen.fencing_token,
        )
        verified = self._node_grants.verify(
            grant_ref=frozen.node_grant_ref,
            parent_grant_ref=frozen.parent_grant_ref,
            request_id=frozen.request_id,
            session_id=frozen.session_id,
            principal_id=frozen.principal_id,
            graph_id=frozen.graph_id,
            graph_revision=frozen.graph_revision,
            root_work_order_id=frozen.root_work_order_id,
            node_id=frozen.node_id,
            route=frozen.route,
            target_id=frozen.target_id,
            attempt_id=frozen.attempt_id,
            lease_id=frozen.lease_id,
            lease_owner_id=frozen.lease_owner_id,
            fencing_token=frozen.fencing_token,
        )
        return (
            str(getattr(verified, "intent_id", "") or "")
            == str(intent_id or "")
            and str(getattr(verified, "session_id", "") or "")
            == frozen.session_id
            and str(getattr(verified, "graph_id", "") or "")
            == frozen.graph_id
            and int(getattr(verified, "graph_revision", 0) or 0)
            == frozen.graph_revision
            and str(getattr(verified, "node_id", "") or "") == frozen.node_id
            and str(getattr(verified, "route", "") or "") == "tool"
            and str(getattr(verified, "target_id", "") or "")
            == frozen.target_id
        )


@dataclass(frozen=True, slots=True)
class ToolHostRequest:
    """Host 合约冻结器使用的无授权 Tool 请求外形。

    Tool HTTP 不拥有回复通道或边缘表达控制；其结果由 Task Acceptance 的
    耐久收据决定，而非 handler 直接回写客户端。
    """

    input_source: str = "TOOL"
    response_form: str = "json"
    reply_channel: None = None
    reply_target: None = None
    attachments: tuple[Any, ...] = ()
    multimodal_observations: Mapping[str, Any] | None = None
    attachment_summary: str = ""


class FrozenToolRouteExecutor:
    """使用 app-local Tool 端口执行外部 Tool HTTP 表面的冻结节点。

    入图绑定与现场目录绑定必须字节级规范相等；执行前先记 PENDING，以便
    崩溃后强制进入 reconciliation 而不是盲目重跑可能副作用的 handler。
    """

    def __init__(
        self,
        *,
        binding_resolver: Callable[[str], Mapping[str, Any]],
        execution_port: Any,
        catalog: Any,
        ledger: Any,
    ) -> None:
        if not callable(getattr(catalog, "get", None)):
            raise TypeError("catalog must expose get")
        if not callable(binding_resolver):
            raise TypeError("binding_resolver is required")
        if not callable(getattr(execution_port, "execute_bound", None)):
            raise TypeError("app-local execution_port.execute_bound is required")
        self._binding_resolver = binding_resolver
        self._execution_port = execution_port
        self._catalog = catalog
        self._ledger = ledger
        self._grants = DurableGovernorGrantResolver(ledger)

    async def execute_stream(
        self,
        frozen: FrozenNodeInput,
    ) -> AsyncIterator[Mapping[str, Any]]:
        """重验子 grant、绑定和重放安全性后，执行一次并投影唯一 COMMIT。

        外部 bearer 从不被冻结；Broker 只收到节点绑定的短期 child token。
        handler 原始输出通过 ``_public_json`` 清洗，且终态明确无直接投递
        授权，后续仍需由 Task Runtime 完成 Verification/Acceptance。
        """
        if not isinstance(frozen, FrozenNodeInput) or frozen.route != "tool":
            raise GovernedToolExecutionError(
                500,
                GOVERNED_TOOL_AUTHORITY_INVALID,
                "frozen tool TaskNode input is required",
            )
        decision = self._grants.require(
            request_id=frozen.request_id,
            expected_ref=frozen.node_grant_ref,
        )
        child = _require_child_tool_authority(decision, frozen=frozen)
        intent = child["intent"]
        token_ref = child["approval_token_ref"]
        pack = dict(frozen.input_pack or {})
        draft = pack.get("intent_draft")
        if not isinstance(draft, Mapping):
            raise GovernedToolExecutionError(
                500,
                GOVERNED_TOOL_AUTHORITY_INVALID,
                "frozen tool intent projection is missing",
            )
        if (
            str(draft.get("route") or "").strip().lower() != "tool"
            or str(draft.get("target_id") or "").strip() != frozen.target_id
        ):
            raise GovernedToolExecutionError(
                500,
                GOVERNED_TOOL_AUTHORITY_INVALID,
                "frozen tool route differs from the node grant",
            )
        frozen_binding = draft.get("tool_binding")
        if not isinstance(frozen_binding, Mapping):
            raise GovernedToolExecutionError(
                500,
                GOVERNED_TOOL_AUTHORITY_INVALID,
                "frozen tool execution binding is missing",
            )
        try:
            live_binding = self._binding_resolver(frozen.target_id)
        except (KeyError, ValueError) as exc:
            raise GovernedToolExecutionError(
                409,
                GOVERNED_TOOL_AUTHORITY_INVALID,
                "frozen tool handler is no longer available",
            ) from exc
        if _canonical_json(frozen_binding) != _canonical_json(live_binding):
            raise GovernedToolExecutionError(
                409,
                GOVERNED_TOOL_AUTHORITY_INVALID,
                "ToolCatalog or handler identity changed after admission",
            )
        if str(live_binding.get("execution_safety") or "").strip().upper() != (
            "SAFE_LOCAL_REPLAY"
        ):
            raise GovernedToolExecutionError(
                409,
                GOVERNED_TOOL_RECONCILIATION_REQUIRED,
                "Tool execution requires pending-operation reconciliation before handler dispatch",
            )

        trace = self._ledger.trace_request(frozen.request_id)
        prior_executions = (
            trace.get("skill_exec_log")
            if isinstance(trace, Mapping)
            else None
        )
        for prior in prior_executions if isinstance(prior_executions, list) else ():
            record = dict(prior) if isinstance(prior, Mapping) else {}
            if (
                str(record.get("intent_id") or "") == str(intent["id"])
                and str(record.get("skill_name") or "") == frozen.target_id
            ):
                raise GovernedToolExecutionError(
                    409,
                    GOVERNED_TOOL_RECONCILIATION_REQUIRED,
                    "A prior Tool handler dispatch requires reconciliation",
                )

        def _mark_dispatch_pending() -> None:
            self._ledger.log_skill_exec(
                {
                    "request_id": frozen.request_id,
                    "thread_id": None,
                    "turn_id": None,
                    "intent_id": str(intent["id"]),
                    "skill_name": frozen.target_id,
                    "approval_token_ref": token_ref,
                    "confirm_token_ref": None,
                    "result_status": "PENDING",
                    "result_code": "GOVERNED_TOOL_HANDLER_DISPATCH_PENDING",
                    "result_digest": "sha256:"
                    + hashlib.sha256(
                        _canonical_json(
                            {
                                "request_id": frozen.request_id,
                                "graph_id": frozen.graph_id,
                                "graph_revision": frozen.graph_revision,
                                "node_id": frozen.node_id,
                                "tool_id": frozen.target_id,
                                "args": dict(draft.get("args") or {}),
                                "context": dict(draft.get("context") or {}),
                                "tool_binding": dict(frozen_binding),
                            }
                        ).encode("utf-8")
                    ).hexdigest(),
                    "evidence_ref": None,
                    "manifest_ref": None,
                    "undo_token_ref": None,
                    "ts_ms": int(time.time() * 1000),
                }
            )

        try:
            result: ToolExecutionResult = self._execution_port.execute_bound(
                task_authority=frozen,
                expected_binding=dict(frozen_binding),
                request_id=frozen.request_id,
                intent_id=str(intent["id"]),
                session_id=str(intent["session_id"]),
                seq=int(intent["seq"]),
                tool_id=frozen.target_id,
                args=dict(draft.get("args") or {}),
                context=dict(draft.get("context") or {}),
                # External bearer material is never frozen.  The broker receives
                # only the Task Runtime's short-lived, node-bound child token.
                approval_token_ref=token_ref,
                # There is no canonical child-confirm authority yet.  CONFIRM
                # tools therefore fail closed in ToolBroker instead of trusting a
                # client-supplied reference.
                confirm_token_ref=None,
                thread_id=None,
                turn_id=None,
                ts_ms=int(time.time() * 1000),
                before_handler=_mark_dispatch_pending,
            )
        except (KeyError, ValueError, PermissionError) as exc:
            raise GovernedToolExecutionError(
                409,
                GOVERNED_TOOL_AUTHORITY_INVALID,
                "Tool execution binding changed before handler dispatch",
            ) from exc
        output = _public_json(result.output, path="tool.output")
        digest = str(result.detail.get("result_digest") or "").strip()
        public_intent_id = str(draft.get("client_intent_id") or "").strip()
        projection = {
            "status": str(result.status),
            "result_code": str(result.result_code),
            "tool_id": str(result.tool_id),
            "intent_id": public_intent_id,
            "evidence_ref": str(result.evidence_ref or "").strip() or None,
            "output": output,
            "result_digest": digest or None,
            "side_effect_level": str(frozen_binding.get("side_effect_level") or ""),
            "auth_level": str(frozen_binding.get("auth_level") or ""),
        }
        approved = result.status == RESULT_SUCCESS
        yield {
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE" if approved else "REJECT",
            "reason_code": None if approved else str(result.result_code),
            "narrative": digest or str(result.result_code) or "tool execution completed",
            "route_type": "tool",
            "target_id": frozen.target_id,
            "audit_refs": {
                "governed_tool_execution": projection,
                "delivery_authorized": False,
            },
        }


class GovernedToolHttpApplication:
    """认证、治理并提交单个 Tool 请求给 Host Runtime 的 HTTP 应用服务。

    该层处理 Secure Ingress 幂等 claim：重复请求只重放规范 Task，未形成
    HostCommand 的崩溃窗口要求恢复，绝不再次调用 Governor 或 Broker。新
    请求的 args/context/绑定先变为公开 JSON，再进入官方 Governor。
    """

    def __init__(
        self,
        *,
        catalog: Any,
        governed_runtime: Any,
        intent_engine: Any,
        ingress_adapter: Any,
        ingress_store: Any,
        tool_binding_resolver: Callable[[str], Mapping[str, Any]],
        governance_snapshot_builder: Callable[..., Mapping[str, Any]],
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        if not callable(getattr(governed_runtime, "execute_stream", None)):
            raise TypeError("governed_runtime.execute_stream is required")
        if not callable(getattr(governed_runtime, "replay_tool_stream", None)):
            raise TypeError("governed_runtime.replay_tool_stream is required")
        if not callable(getattr(intent_engine, "evaluate_intent", None)):
            raise TypeError("intent_engine.evaluate_intent is required")
        if not callable(getattr(ingress_adapter, "evaluate", None)):
            raise TypeError("ingress_adapter.evaluate is required")
        if not callable(getattr(ingress_store, "complete_claim", None)):
            raise TypeError("ingress_store.complete_claim is required")
        if not callable(tool_binding_resolver):
            raise TypeError("tool_binding_resolver is required")
        if not callable(governance_snapshot_builder):
            raise TypeError("governance_snapshot_builder is required")
        self._catalog = catalog
        self._registry = ToolGovernanceRegistry(catalog)
        self._runtime = governed_runtime
        self._intent_engine = intent_engine
        self._ingress = ingress_adapter
        self._ingress_store = ingress_store
        self._tool_binding_resolver = tool_binding_resolver
        self._governance_snapshot_builder = governance_snapshot_builder
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))

    async def execute(
        self,
        *,
        body: Any,
        request: Any,
        raw_body: bytes,
    ) -> dict[str, Any]:
        """执行或重放一个 Tool HTTP 请求，并把耐久终态转为公共响应。

        新请求顺序为 Ingress→目录/绑定→Governor route token→HostCommand；
        PAUSE 会留下可 Review 的图而非直接执行。重复请求根据 claim 的
        result_ref 验证 body 摘要，保证网络重试不会覆盖已有决定。
        """
        now = int(self._clock_ms())
        authority = self._ingress.evaluate(
            request=request,
            raw_body=raw_body,
            received_at_ms=now,
        )
        if not bool(getattr(authority, "admissible", False)):
            raise GovernedToolExecutionError(
                403,
                str(getattr(authority, "reason_code", "") or GOVERNED_TOOL_INGRESS_REJECTED),
                "Secure Ingress rejected the Tool command",
            )
        command_authority = _command_authority(authority, raw_body=raw_body)
        request_id = str(command_authority["request_id"])
        principal_id = str(command_authority["principal_id"])
        tool_id = str(getattr(body, "tool_id", "") or "").strip()
        client_intent_id = str(getattr(body, "intent_id", "") or "").strip()
        session_id = str(getattr(body, "session_id", "") or "").strip()
        if not tool_id or not client_intent_id or not session_id:
            self._raise_durable_outcome(
                command_authority=command_authority,
                status_code=422,
                reason_code=GOVERNED_TOOL_AUTHORITY_INVALID,
                detail="tool_id, intent_id and session_id are required",
            )

        if bool(getattr(authority, "duplicate", False)):
            existing_result_ref = str(
                getattr(authority, "existing_result_ref", "") or ""
            ).strip()
            durable_outcome = _parse_tool_outcome_ref(
                existing_result_ref,
                body_sha256=str(command_authority["body_sha256"]),
            )
            if durable_outcome is not None:
                raise GovernedToolExecutionError(
                    durable_outcome[0],
                    durable_outcome[1],
                    _durable_outcome_detail(durable_outcome[1]),
                )
            review_outcome = _parse_review_decision_ref(existing_result_ref)
            try:
                terminal = await _collect_terminal(
                    self._runtime.replay_tool_stream(
                        request_id=request_id,
                        target_id=tool_id,
                        session_id=session_id,
                        principal_id=principal_id,
                        command_authority=command_authority,
                    )
                )
            except GovernedToolExecutionError:
                raise
            except Exception as exc:
                # A COMMITTED claim can exist in the narrow crash/race window
                # before HostCommand admission.  Never run Governor/Broker a
                # second time and never seal a result while the first request
                # may still commit.
                raise GovernedToolExecutionError(
                    409,
                    "GOVERNED_TOOL_ADMISSION_RECOVERY_REQUIRED",
                    "Tool admission is in progress or requires recovery",
                ) from exc
            replay_result_ref = _terminal_result_ref(terminal)
            if review_outcome is not None:
                receipt = terminal.get("execution_receipt")
                receipt = dict(receipt) if isinstance(receipt, Mapping) else {}
                terminal_status = str(receipt.get("status") or "").strip().upper()
                terminal_reason = str(
                    receipt.get("reason_code") or terminal.get("reason_code") or ""
                ).strip().upper()
                # Terminal replay intentionally collapses a durably cancelled
                # graph to GOVERNED_REPLAY_CANCELLED.  The exact
                # review-decision:// binding has already been reopened and
                # validated by HostCommandRepository before this replay, so a
                # non-accepted CANCELLED receipt is the canonical replay shape.
                review_terminal = terminal_status in {"REVIEW_DENIED", "CANCELLED"}
                review_terminal = review_terminal and not bool(receipt.get("accepted"))
                if not review_terminal:
                    raise GovernedToolExecutionError(
                        409,
                        GOVERNED_TOOL_AUTHORITY_INVALID,
                        "durable Review decision differs from Task Runtime terminal state "
                        f"(status={terminal_status or 'UNKNOWN'}, "
                        f"reason={terminal_reason or 'UNKNOWN'})",
                    )
                review_reason = (
                    GOVERNED_TOOL_REVIEW_REJECTED
                    if review_outcome == "REJECTED"
                    else GOVERNED_TOOL_REVIEW_EXPIRED
                )
                raise GovernedToolExecutionError(
                    403,
                    review_reason,
                    _durable_outcome_detail(review_reason),
                )
            if not existing_result_ref and _terminal_claim_is_final(terminal):
                # Repairs the accepted-Task/NULL-claim crash window with the
                # exact canonical receipt.  Store CAS rejects a concurrent
                # different result instead of overwriting it.
                self._ingress_store.complete_claim(
                    claim_ref=str(command_authority["ingress_claim_ref"]),
                    result_ref=replay_result_ref,
                    at_ms=now,
                )
            return _http_projection(
                terminal,
                request_id=request_id,
                client_intent_id=client_intent_id,
                timestamp=now,
                expected_result_ref=existing_result_ref or replay_result_ref,
            )

        try:
            safe_args = _public_json(
                dict(getattr(body, "args", None) or {}),
                path="tool.args",
            )
            safe_context = _public_json(
                dict(getattr(body, "context", None) or {}),
                path="tool.context",
            )
        except GovernedToolExecutionError:
            self._raise_durable_outcome(
                command_authority=command_authority,
                status_code=422,
                reason_code=GOVERNED_TOOL_AUTHORITY_INVALID,
                detail="Tool args/context are not safe durable JSON",
            )
            raise AssertionError("unreachable")

        try:
            self._catalog.get(tool_id)
        except (KeyError, GovernedToolExecutionError) as exc:
            self._raise_durable_outcome(
                command_authority=command_authority,
                status_code=404,
                reason_code=GOVERNED_TOOL_NOT_FOUND,
                detail=f"tool not found: {tool_id}",
            )
            raise AssertionError("unreachable") from exc
        try:
            tool_binding = _public_json(
                self._tool_binding_resolver(tool_id),
                path="tool.binding",
            )
        except KeyError as exc:
            self._raise_durable_outcome(
                command_authority=command_authority,
                status_code=422,
                reason_code="TOOL_HANDLER_MISSING",
                detail="the Tool handler is not available",
            )
            raise AssertionError("unreachable") from exc
        if str(tool_binding.get("execution_safety") or "").strip().upper() != (
            "SAFE_LOCAL_REPLAY"
        ):
            self._raise_durable_outcome(
                command_authority=command_authority,
                status_code=409,
                reason_code=GOVERNED_TOOL_RECONCILIATION_REQUIRED,
                detail="Tool requires a pending-operation reconciliation adapter",
            )

        canonical_intent = _canonical_tool_intent(
            body=body,
            request_id=request_id,
            principal_id=principal_id,
            tool_id=tool_id,
            session_id=session_id,
            now_ms=now,
            safe_args=safe_args,
        )
        governance_snapshot = self._governance_snapshot_builder(
            emotion_context={
                "mood_state": "steady",
                "emotion_tag": "NEUTRAL",
                "emotion_intensity": 0.5,
            },
            ts_ms=now,
        )
        governor = self._intent_engine.evaluate_intent(
            raw_intent=canonical_intent,
            request_id=request_id,
            thread_id=str(getattr(body, "thread_id", "") or "").strip() or None,
            turn_id=str(getattr(body, "turn_id", "") or "").strip() or None,
            current_ms=now,
            proposer_node_id="host_tool_http_application",
            governance_snapshot=dict(governance_snapshot),
            registry_override=self._registry,
        )
        if str(governor.get("decision") or "").strip().upper() != "APPROVE":
            self._raise_durable_outcome(
                command_authority=command_authority,
                status_code=403,
                reason_code=str(
                    governor.get("reason_code") or GOVERNED_TOOL_GOVERNOR_REJECTED
                ),
                detail="Governor rejected the Tool command",
            )
        route = validate_governor_route_token(governor)
        durable_review_required = route.disposition is RouteTokenDisposition.PAUSE
        if route.disposition not in {
            RouteTokenDisposition.ALLOW,
            RouteTokenDisposition.PAUSE,
        }:
            self._raise_durable_outcome(
                command_authority=command_authority,
                status_code=403,
                reason_code=route.reason_code or GOVERNED_TOOL_GOVERNOR_REJECTED,
                detail="Governor route authority is not executable",
            )

        intent_draft = {
            "id": canonical_intent["id"],
            "route": "tool",
            "target_id": tool_id,
            "action": tool_id,
            "target": "host",
            "task": f"execute governed tool {tool_id}",
            "args": safe_args,
            "context": safe_context,
            "tool_binding": tool_binding,
            "durable_review_required": durable_review_required,
            "client_intent_id": client_intent_id,
        }
        terminal = await _collect_terminal(
            self._runtime.execute_stream(
                request_body=ToolHostRequest(multimodal_observations={}),
                request_id=request_id,
                session_id=session_id,
                ts_ms=now,
                intent_draft=intent_draft,
                governor_result=governor,
                person_id=principal_id,
                sanitized_message=f"execute governed tool {tool_id}",
                command_authority=command_authority,
            )
        )
        result_ref = _terminal_result_ref(terminal)
        if _terminal_claim_is_final(terminal):
            self._ingress_store.complete_claim(
                claim_ref=str(command_authority["ingress_claim_ref"]),
                result_ref=result_ref,
                at_ms=now,
            )
        return _http_projection(
            terminal,
            request_id=request_id,
            client_intent_id=client_intent_id,
            timestamp=now,
            expected_result_ref=result_ref,
        )

    def _raise_durable_outcome(
        self,
        *,
        command_authority: Mapping[str, Any],
        status_code: int,
        reason_code: str,
        detail: str,
    ) -> None:
        """把可确定拒绝 CAS 写入 ingress claim，再返回固定公共错误。

        这样同一请求的后续重试得到同一结果；调用方传来的 ``detail`` 不会
        泄露，因为耐久 outcome ref 只绑定 body 摘要、HTTP 状态与原因码。
        """
        canonical_reason = str(
            reason_code or GOVERNED_TOOL_RESULT_INVALID
        ).strip().upper()
        outcome_ref = _tool_outcome_ref(
            body_sha256=str(command_authority.get("body_sha256") or ""),
            status_code=status_code,
            reason_code=canonical_reason,
        )
        self._ingress_store.complete_claim(
            claim_ref=str(command_authority["ingress_claim_ref"]),
            result_ref=outcome_ref,
            at_ms=int(self._clock_ms()),
        )
        del detail  # public denial text is deterministic from durable authority
        raise GovernedToolExecutionError(
            status_code,
            canonical_reason,
            _durable_outcome_detail(canonical_reason),
        )


def _canonical_tool_intent(
    *,
    body: Any,
    request_id: str,
    principal_id: str,
    tool_id: str,
    session_id: str,
    now_ms: int,
    safe_args: Mapping[str, Any],
) -> dict[str, Any]:
    """由请求、客户端 intent 与工具标识生成稳定内部 intent 身份。"""
    identity = json.dumps(
        {
            "request_id": request_id,
            "client_intent_id": str(getattr(body, "intent_id", "") or ""),
            "tool_id": tool_id,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return {
        "schema_version": "Natsume.intent.v1",
        "id": f"tool-intent::{digest}",
        "ts_ms": int(now_ms),
        "session_id": session_id,
        "seq": int(getattr(body, "seq", 1)),
        "nonce": f"nonce::tool::{digest}",
        "type": "query",
        "action": tool_id,
        "target": "host",
        "route": "tool",
        "target_id": tool_id,
        "task": f"execute governed tool {tool_id}",
        "person_id": principal_id,
        "principal_id": principal_id,
        "args": dict(safe_args),
    }


def _command_authority(authority: Any, *, raw_body: bytes) -> dict[str, Any]:
    """提取并重新散列 Secure Ingress authority，拒绝与原始 body 脱绑定。"""
    body_digest = hashlib.sha256(bytes(raw_body)).hexdigest()
    values = {
        "request_id": str(getattr(authority, "request_id", "") or "").strip(),
        "principal_id": str(getattr(authority, "principal_id", "") or "").strip(),
        "ingress_claim_ref": str(getattr(authority, "claim_ref", "") or "").strip(),
        "ingress_audit_ref": str(getattr(authority, "audit_ref", "") or "").strip(),
        "body_sha256": str(getattr(authority, "body_sha256", "") or "").strip().removeprefix("sha256:"),
    }
    if not all(values.values()) or values["body_sha256"].lower() != body_digest:
        raise GovernedToolExecutionError(
            409,
            GOVERNED_TOOL_AUTHORITY_INVALID,
            "Secure Ingress authority is incomplete or does not bind this body",
        )
    values["body_sha256"] = body_digest
    return values


def _require_child_tool_authority(
    decision: Mapping[str, Any],
    *,
    frozen: FrozenNodeInput,
) -> dict[str, Any]:
    """从耐久子 grant 取得节点专属 intent/token，并逐字段绑定冻结节点。"""
    snapshots = decision.get("snapshot_refs")
    snapshots = dict(snapshots) if isinstance(snapshots, Mapping) else {}
    node = snapshots.get("node_grant")
    node = dict(node) if isinstance(node, Mapping) else {}
    intent = snapshots.get("node_intent")
    intent = dict(intent) if isinstance(intent, Mapping) else {}
    tokens = decision.get("tokens")
    tokens = dict(tokens) if isinstance(tokens, Mapping) else {}
    binding = durable_governor_binding(decision)
    expected = {
        "request_id": frozen.request_id,
        "graph_id": frozen.graph_id,
        "graph_revision": frozen.graph_revision,
        "root_work_order_id": frozen.root_work_order_id,
        "node_id": frozen.node_id,
        "route": "tool",
        "target_id": frozen.target_id,
        "action_kind": f"tool:{frozen.target_id}",
        "token_subject": frozen.target_id,
    }
    actual = {key: node.get(key) for key in expected}
    token_ref = str(tokens.get("approval_token_ref") or "").strip()
    if (
        actual != expected
        or str(intent.get("id") or "").strip() != str(decision.get("intent_id") or "").strip()
        or str(intent.get("session_id") or "").strip() != frozen.session_id
        or isinstance(intent.get("seq"), bool)
        or not isinstance(intent.get("seq"), int)
        or str(intent.get("action") or "").strip() != expected["action_kind"]
        or str(binding.get("action_kind") or "").strip() != expected["action_kind"]
        or str(binding.get("scope") or "").strip().lower() != "host"
        or str(binding.get("principal_id") or "").strip() != frozen.principal_id
        or str(binding.get("approval_token_digest") or "").strip()
        != "sha256:" + hashlib.sha256(token_ref.encode("utf-8")).hexdigest()
        or not token_ref
    ):
        raise GovernedToolExecutionError(
            500,
            GOVERNED_TOOL_AUTHORITY_INVALID,
            "durable child Tool grant is missing or drifted",
        )
    return {"intent": intent, "approval_token_ref": token_ref}


async def _collect_terminal(stream: Any) -> dict[str, Any]:
    """只接受 Host Runtime 的唯一终态；中间流永远不是 HTTP 执行结果。"""
    if not hasattr(stream, "__aiter__"):
        raise GovernedToolExecutionError(
            503,
            GOVERNED_TOOL_RUNTIME_UNAVAILABLE,
            "governed Host runtime did not return a stream",
        )
    terminal: dict[str, Any] | None = None
    count = 0
    async for raw in stream:
        event = dict(raw) if isinstance(raw, Mapping) else {}
        if bool(event.get("done")) or str(event.get("phase") or "").strip().upper() in {
            "COMMIT",
            "TERMINAL",
        }:
            terminal = event
            count += 1
    if terminal is None or count != 1:
        raise GovernedToolExecutionError(
            503,
            GOVERNED_TOOL_RESULT_INVALID,
            "governed Host runtime returned no unique terminal receipt",
        )
    return terminal


def _terminal_result_ref(terminal: Mapping[str, Any]) -> str:
    """取可重放的耐久收据引用；缺失引用的终态不能封存进 ingress claim。"""
    receipt = terminal.get("execution_receipt")
    receipt = dict(receipt) if isinstance(receipt, Mapping) else {}
    return (
        str(receipt.get("receipt_ref") or "").strip()
        or str(receipt.get("command_id") or terminal.get("command_id") or "").strip()
        or (_raise_result_invalid())
    )


def _terminal_claim_is_final(terminal: Mapping[str, Any]) -> bool:
    """判断终态是否足以封存 ingress claim，等待审批仍需允许后续继续。"""
    receipt = terminal.get("execution_receipt")
    receipt = dict(receipt) if isinstance(receipt, Mapping) else {}
    return str(receipt.get("status") or "").strip().upper() in {
        "ACCEPTED",
        "FAILED",
        "CANCELLED",
        "BUDGET_BLOCKED",
        "BUDGET_EXCEEDED",
        "VERIFICATION_FAILED",
        "REVIEW_DENIED",
    }


def _tool_outcome_ref(*, body_sha256: str, status_code: int, reason_code: str) -> str:
    """生成内容绑定的确定性拒绝引用，供重复 HTTP 请求安全重放。"""
    digest = str(body_sha256 or "").strip().lower().removeprefix("sha256:")
    reason = str(reason_code or GOVERNED_TOOL_RESULT_INVALID).strip().upper()
    if (
        not re.fullmatch(r"[0-9a-f]{64}", digest)
        or not 400 <= int(status_code) <= 599
        or not re.fullmatch(r"[A-Z][A-Z0-9_]{1,127}", reason)
    ):
        raise GovernedToolExecutionError(
            500,
            GOVERNED_TOOL_RESULT_INVALID,
            "Tool outcome authority is invalid",
        )
    return f"tool-http-outcome://sha256/{digest}/{int(status_code)}/{reason}"


def _durable_outcome_detail(reason_code: str) -> str:
    reason = str(reason_code or GOVERNED_TOOL_RESULT_INVALID).strip().upper()
    if reason == GOVERNED_TOOL_AUTHORITY_INVALID:
        return "Tool request authority is invalid"
    if reason == GOVERNED_TOOL_NOT_FOUND:
        return "Tool target is unavailable"
    if reason == "TOOL_HANDLER_MISSING":
        return "Tool handler is unavailable"
    if reason == GOVERNED_TOOL_RECONCILIATION_REQUIRED:
        return "Tool requires pending-operation reconciliation"
    if reason == GOVERNED_TOOL_REVIEW_REJECTED:
        return "Tool execution was rejected by durable Review"
    if reason == GOVERNED_TOOL_REVIEW_EXPIRED:
        return "Tool execution Review expired"
    if reason in {
        GOVERNED_TOOL_GOVERNOR_REJECTED,
        "ROUTE_TOKEN_REF_MISSING",
        "ROUTE_TOKEN_DETAIL_MISSING",
        "ROUTE_TOKEN_DETAIL_TOKEN_MISSING",
        "ROUTE_TOKEN_REF_DETAIL_MISMATCH",
        "ROUTE_TOKEN_NOT_EXECUTABLE",
    }:
        return "Governor rejected the Tool command"
    return "Governed Tool command was rejected"


def _parse_tool_outcome_ref(
    value: str,
    *,
    body_sha256: str,
) -> tuple[int, str] | None:
    """解析旧拒绝引用并确认其摘要仍绑定本次 body。"""
    ref = str(value or "").strip()
    if not ref.startswith("tool-http-outcome://"):
        return None
    match = re.fullmatch(
        r"tool-http-outcome://sha256/([0-9a-f]{64})/([45][0-9]{2})/([A-Z][A-Z0-9_]{1,127})",
        ref,
    )
    expected = str(body_sha256 or "").strip().lower().removeprefix("sha256:")
    if match is None or match.group(1) != expected:
        raise GovernedToolExecutionError(
            409,
            GOVERNED_TOOL_AUTHORITY_INVALID,
            "durable Tool HTTP outcome does not bind this request body",
        )
    return int(match.group(2)), match.group(3)


def _parse_review_decision_ref(value: str) -> str | None:
    """识别耐久 Review 拒绝/过期引用，供重放时恢复同一结果。"""
    ref = str(value or "").strip()
    if not ref.startswith("review-decision://"):
        return None
    match = re.fullmatch(
        r"review-decision://([^/?#]+)/((?:rejected)|(?:expired))/([1-9][0-9]*)",
        ref,
    )
    if match is None:
        raise GovernedToolExecutionError(
            409,
            GOVERNED_TOOL_AUTHORITY_INVALID,
            "durable Tool Review outcome is malformed",
        )
    return match.group(2).upper()


def _raise_result_invalid() -> str:
    raise GovernedToolExecutionError(
        503,
        GOVERNED_TOOL_RESULT_INVALID,
        "governed Tool terminal has no durable result reference",
    )


def _http_projection(
    terminal: Mapping[str, Any],
    *,
    request_id: str,
    client_intent_id: str,
    timestamp: int,
    expected_result_ref: str | None,
) -> dict[str, Any]:
    """把已 Acceptance 的 Tool 收据投影为 HTTP 响应并再次绑定 result_ref。"""
    receipt = terminal.get("execution_receipt")
    receipt = dict(receipt) if isinstance(receipt, Mapping) else {}
    if expected_result_ref:
        actual_ref = (
            str(receipt.get("receipt_ref") or "").strip()
            or str(receipt.get("command_id") or terminal.get("command_id") or "").strip()
        )
        if actual_ref != expected_result_ref:
            raise GovernedToolExecutionError(
                409,
                GOVERNED_TOOL_AUTHORITY_INVALID,
                "Secure Ingress result reference differs from the durable Task receipt",
            )
    execution = terminal.get("audit_refs")
    execution = dict(execution) if isinstance(execution, Mapping) else {}
    execution = execution.get("governed_tool_execution")
    execution = dict(execution) if isinstance(execution, Mapping) else {}
    accepted = bool(receipt.get("accepted")) and str(receipt.get("status") or "").upper() == "ACCEPTED"
    if not accepted or not execution:
        reason = str(
            receipt.get("reason_code")
            or terminal.get("reason_code")
            or GOVERNED_TOOL_RESULT_INVALID
        ).strip()
        status_code = 409 if str(receipt.get("status") or "").upper() == "WAITING_APPROVAL" else 422
        raise GovernedToolExecutionError(
            status_code,
            reason,
            "governed Tool command was not accepted",
        )
    return {
        "status": str(execution.get("status") or "SUCCESS"),
        "result_code": str(execution.get("result_code") or "TOOL_EXECUTED"),
        "tool_id": str(execution.get("tool_id") or ""),
        "intent_id": client_intent_id,
        "evidence_ref": execution.get("evidence_ref"),
        "output": _public_json(execution.get("output"), path="tool.output"),
        "reason_code": None,
        "detail": None,
        "request_id": request_id,
        "timestamp": int(timestamp),
    }


def _public_json(value: Any, *, path: str) -> Any:
    """递归验证公开 JSON，拒绝敏感键、非有限数字和不可序列化对象。"""
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, child in value.items():
            key = str(raw_key).strip()
            if not key or _SENSITIVE_KEY.search(key):
                raise GovernedToolExecutionError(
                    500,
                    GOVERNED_TOOL_RESULT_INVALID,
                    f"{path} contains a sensitive or empty key",
                )
            result[key] = _public_json(child, path=f"{path}.{key}")
        return result
    if isinstance(value, (list, tuple)):
        return [
            _public_json(child, path=f"{path}[{index}]")
            for index, child in enumerate(value)
        ]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise GovernedToolExecutionError(
                500,
                GOVERNED_TOOL_RESULT_INVALID,
                f"{path} contains a non-finite number",
            )
        return value
    raise GovernedToolExecutionError(
        500,
        GOVERNED_TOOL_RESULT_INVALID,
        f"{path} is not JSON-safe",
    )


def _canonical_json(value: Mapping[str, Any]) -> str:
    """用稳定 JSON 对比入图绑定与现场绑定，任何不可规范化值均失败。"""
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise GovernedToolExecutionError(
            500,
            GOVERNED_TOOL_RESULT_INVALID,
            "tool execution binding is not canonical JSON",
        ) from exc


__all__ = [
    "FrozenToolRouteExecutor",
    "GovernedToolExecutionError",
    "GovernedToolHttpApplication",
    "ToolGovernanceRegistry",
    "ToolHostRequest",
]
