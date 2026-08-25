"""受治理 Operator runtime：worker 只提议，应用局部 dispatcher 才能提交。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import hmac
from typing import Any

from host_brain.agent_runtime.application_ports import AgentRuntimeApplicationPorts
from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.agent_runtime.accepted_result import InternalAcceptedResultHandler
from host_brain.agent_runtime.accepted_workspace_artifact import (
    AcceptedAgentWorkspaceArtifactHandler,
)
from host_brain.app.acceptance_effect_handlers import (
    build_production_acceptance_effect_handlers,
)
from host_brain.app.acceptance_effect_recovery_observer import (
    AcceptanceEffectRecoveryGuardianObserver,
)
from host_brain.app.governed_worker_evidence import (
    DurableWorkerCommitAcceptance as _WorkerCommitAcceptance,
    DurableWorkerCommitVerifier as _WorkerCommitVerifier,
)
from host_brain.evolution.task_feedback import (
    decorate_task_acceptance,
)
from host_brain.contracts.governed_operator import (
    GOVERNED_OPERATOR_GOVERNOR_REJECTED,
    GOVERNED_OPERATOR_RUNTIME_INVALID,
    GovernedOperatorRuntimeError,
    _require_operator_authority,
)
from host_brain.app.governed_operator_execution import (
    FrozenSysModeSwitchExecutor,
    SYS_MODE_ACTION_KIND as _ACTION_KIND,
    SYS_MODE_EFFECT_TYPE as _EFFECT_TYPE,
    SYS_MODE_INGRESS_ACTION as _INGRESS_ACTION,
    SYS_MODE_INGRESS_PATH as _INGRESS_PATH,
    SYS_MODE_INGRESS_REQUIRED_SCOPES as _INGRESS_REQUIRED_SCOPES,
    SYS_MODE_TARGET_ID as _TARGET_ID,
    require_operator_runtime_scope_id as _runtime_scope_id,
)
from host_brain.task_runtime.operator_control_support import (
    OPERATOR_CONTROL_EFFECT_TYPE,
    OPERATOR_CONTROL_TARGET_ID,
)
from host_brain.task_runtime.operator_state_control_support import (
    OPERATOR_STATE_CONTROL_EFFECT_TYPE,
    OPERATOR_STATE_CONTROL_TARGET_ID,
)
from host_brain.app.governed_operator_control_runtime import (
    GovernedOperatorControlMixin,
)
from host_brain.app.governed_operator_state_control_runtime import (
    GovernedOperatorStateControlMixin,
    current_mode as _current_mode,
    require_durable_sys_mode_outcome as _require_durable_sys_mode_outcome,
)
from host_brain.app.governed_operator_edge_support import (
    EDGE_ACTION_KIND as _EDGE_ACTION_KIND,
    EDGE_EFFECT_TYPE as _EDGE_EFFECT_TYPE,
    EDGE_INGRESS_ACTION as _EDGE_INGRESS_ACTION,
    EDGE_INGRESS_PATH as _EDGE_INGRESS_PATH,
    EDGE_INGRESS_REQUIRED_SCOPES as _EDGE_INGRESS_REQUIRED_SCOPES,
    EDGE_TARGET_ID as _EDGE_TARGET_ID,
    build_edge_governor_intent as _build_edge_governor_intent,
    embodiment_runtime_scope_id as _embodiment_runtime_scope_id,
    normalize_edge_command_args as _normalize_edge_command_args,
    pending_operator_reviews as _pending_operator_reviews,
    require_durable_edge_outcome as _require_durable_edge_outcome,
)
from host_brain.app.governed_terminal_reconciliation_runtime import (
    GovernedTerminalReconciliationMixin,
    TERMINAL_EFFECT_TYPE,
    TERMINAL_TARGET_ID,
    require_durable_terminal_outcome,
)
from host_brain.app.governed_operation_reconciliation_runtime import (
    GovernedOperationReconciliationMixin,
    OPERATION_RECONCILIATION_TARGET_ID,
    operation_reconciliation_request_from_args,
    require_durable_operation_reconciliation_outcome,
)
from host_brain.app.accepted_operation_reconciliation_handler import (
    OPERATION_RECONCILIATION_EFFECT_TYPE,
)
from host_brain.app.accepted_effect_reconciliation_handler import (
    EFFECT_RECONCILIATION_EFFECT_TYPE,
)
from host_brain.app.governed_effect_reconciliation_runtime import (
    EFFECT_RECONCILIATION_TARGET_ID,
    GovernedEffectReconciliationMixin,
    effect_reconciliation_request_from_args,
    require_durable_effect_reconciliation_outcome,
)
from host_brain.app.governed_task_adapter import GovernedHostRuntime
from host_brain.app.user_preference_store import UserPreferenceStore
from host_brain.channels.edge_transport import query_gateway_reply_delivery
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.core.ids import now_ms
from host_brain.core.mode_transition import TransitionSource
from host_brain.orchestration.governed_command import GovernedCommandApplication
from host_brain.orchestration.governor_grant import DurableGovernorGrantResolver
from host_brain.orchestration.node_grants import (
    DurableNodeGrantVerifier,
    NodeBoundGrantService,
)
from host_brain.persistence.task_repository import OutboxMessage
from host_brain.persistence.context_memory_repository import MemoryEventRepository
from host_brain.persistence.context_repository import ContextRepository
from host_brain.runtime.context_application import RuntimeContextApplicationService
from host_brain.runtime.intent_admission_contracts import (
    INTENT_ADMISSION_REVIEW_REQUIRED,
)
from host_brain.runtime.intent_admission import DurableIntentAdmissionVerifier
from host_brain.task_runtime.coordinator import GovernedTaskCoordinator
from host_brain.task_runtime.errors import GovernedRuntimeError
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectDispatcher,
    AcceptanceEffectRuntime,
)
from host_brain.task_runtime.governed_workers import GovernedFrozenNodeWorker
from host_brain.task_runtime.review_runtime import TaskNodeReviewRuntime
from host_brain.task_runtime.review_command_service import (
    TaskReviewCommandService,
)
from host_brain.task_runtime.worker_registry import GovernedWorkerRegistry
from natsume_contracts.task import HostCommand, HostCommandSurface


@dataclass(frozen=True, slots=True)
class GovernedOperatorComposition:
    """Operator runtime 及其共享 acceptance/review port 的组合结果，尚未代表已激活。"""
    runtime: "GovernedOperatorRuntime"
    acceptance_effect_dispatcher: AcceptanceEffectDispatcher
    acceptance_effect_runtime: AcceptanceEffectRuntime
    review_command_service: TaskReviewCommandService


class GovernedOperatorRuntime(
    GovernedOperatorStateControlMixin,
    GovernedOperatorControlMixin,
    GovernedEffectReconciliationMixin,
    GovernedOperationReconciliationMixin,
    GovernedTerminalReconciliationMixin,
):
    """统一评估、准入并执行受签名约束的 Operator 垂直切片。

    混入涵盖系统模式、Edge 命令、控制平面与多种 reconciliation；所有路径共用 committed
    Secure Ingress authority 与 GovernedHostRuntime，防止不同运维端点形成绕过 Review 或
    acceptance effect 的平行执行通道。
    """

    def __init__(
        self,
        *,
        runtime: GovernedHostRuntime,
        command_repository: Any,
        intent_engine: Any,
        governor_grants: DurableGovernorGrantResolver,
        review_store: Any,
        task_store: Any,
        terminal_operator_command_store: Any,
        mode_transition_service_provider: Any,
        secure_ingress_authority_resolver: Any,
        runtime_scope_id: str,
        embodiment_runtime_provider: Any | None = None,
        scheduler_command_service_provider: Any | None = None,
        long_term_goal_authority_provider: Any | None = None,
    ) -> None:
        if not callable(
            getattr(
                secure_ingress_authority_resolver,
                "resolve_committed_authority_by_audit",
                None,
            )
        ):
            raise TypeError(
                "Operator runtime requires committed Secure Ingress authority resolver"
            )
        self._runtime = runtime
        self._commands = command_repository
        self._intent_engine = intent_engine
        self._governor_grants = governor_grants
        self._review_store = review_store
        self._task_store = task_store
        self._terminal_operator_command_store = terminal_operator_command_store
        self._mode_transition_service_provider = mode_transition_service_provider
        if (
            scheduler_command_service_provider is not None
            and not callable(scheduler_command_service_provider)
        ):
            raise TypeError(
                "scheduler_command_service_provider must be callable"
            )
        self._scheduler_command_service_provider = (
            scheduler_command_service_provider or (lambda: None)
        )
        if (
            long_term_goal_authority_provider is not None
            and not callable(long_term_goal_authority_provider)
        ):
            raise TypeError(
                "long_term_goal_authority_provider must be callable"
            )
        self._long_term_goal_authority_provider = (
            long_term_goal_authority_provider or (lambda: None)
        )
        if embodiment_runtime_provider is not None and not callable(
            embodiment_runtime_provider
        ):
            raise TypeError("embodiment_runtime_provider must be callable")
        self._embodiment_runtime_provider = embodiment_runtime_provider
        self._runtime_scope_id = _runtime_scope_id(runtime_scope_id)
        self._secure_ingress_authority_resolver = (
            secure_ingress_authority_resolver
        )

    def _verify_operator_authority(
        self,
        authority: Mapping[str, Any],
        **requirements: Any,
    ) -> dict[str, Any]:
        """用已提交 Ingress 审计记录重验 authority，拒绝仅靠请求体声称的权限。"""
        return _verify_durable_operator_authority(
            authority,
            resolver=self._secure_ingress_authority_resolver,
            **requirements,
        )

    async def activate(self) -> None:
        """激活共享 Host spine；Operator 不拥有第二套 Task Runtime 生命周期。"""
        await self._runtime.activate()

    async def close_admission(self) -> None:
        """先关闭新的 Operator admission，保留已准入工作供 drain/恢复处理。"""
        await self._runtime.close_admission()

    async def drain(self) -> None:
        """委托 Host spine 排空已准入执行；不在该层强制取消未知副作用。"""
        await self._runtime.drain()

    async def aclose(self) -> None:
        """关闭共享 runtime 视图；实际资源释放仍由上层应用生命周期编排。"""
        await self._runtime.aclose()

    async def continue_review_admission(
        self,
        record: Mapping[str, Any],
        claim: Mapping[str, Any],
    ) -> dict[str, Any]:
        """续接 Review 批准后的 Operator admission，并按命令类型重验耐久副作用结果。

        只有 receipt 明确接受才尝试重开 effect outcome；每类命令使用专属 verifier，
        防止将模式切换、Edge 物理投递或修复 proof 彼此错误归属。
        """
        result = await self._runtime.continue_review_admission(record, claim)
        bound = dict(result or {})
        receipt_raw = bound.get("execution_receipt")
        receipt = dict(receipt_raw) if isinstance(receipt_raw, Mapping) else {}
        if (
            receipt.get("accepted") is not True
            or str(receipt.get("status") or "").strip().upper() != "ACCEPTED"
        ):
            bound["historical_side_effects_executed"] = False
            return bound
        durable = dict(record or {})
        command = durable.get("command")
        work_order = durable.get("work_order")
        if not isinstance(command, HostCommand) or not isinstance(
            work_order, WorkOrderV3
        ):
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "Operator Review continuation lacks durable command contracts",
            )
        intent_raw = dict(work_order.input_pack or {}).get("intent_draft")
        intent = dict(intent_raw) if isinstance(intent_raw, Mapping) else {}
        args_raw = intent.get("args")
        args = dict(args_raw) if isinstance(args_raw, Mapping) else {}
        operator_command_id = str(
            dict(work_order.metadata or {}).get("operator_command_id") or ""
        ).strip()
        if operator_command_id == "task_terminal.reconcile":
            outcome = require_durable_terminal_outcome(
                task_store=self._task_store,
                command_store=self._terminal_operator_command_store,
                execution_receipt=receipt,
                args=args,
            )
            bound["acceptance_effect_receipt"] = outcome
            bound["historical_side_effects_executed"] = True
            return bound
        if operator_command_id == "task_operation.reconcile":
            outcome = require_durable_operation_reconciliation_outcome(
                task_store=self._task_store,
                execution_receipt=receipt,
                request=operation_reconciliation_request_from_args(args),
            )
            bound["acceptance_effect_receipt"] = outcome
            bound["historical_side_effects_executed"] = True
            return bound
        if operator_command_id == "task_effect.reconcile":
            outcome = require_durable_effect_reconciliation_outcome(
                task_store=self._task_store,
                execution_receipt=receipt,
                request=effect_reconciliation_request_from_args(args),
            )
            bound["acceptance_effect_receipt"] = outcome
            bound["historical_side_effects_executed"] = True
            return bound
        if operator_command_id == "edge.command.push":
            normalized = _normalize_edge_command_args(args)
            if (
                str(intent.get("action") or "").strip()
                != _EDGE_ACTION_KIND
                or str(intent.get("target") or "").strip().lower() != "edge"
                or str(intent.get("target_id") or "").strip()
                != _EDGE_TARGET_ID
            ):
                raise GovernedOperatorRuntimeError(
                    GOVERNED_OPERATOR_RUNTIME_INVALID,
                    "Edge Operator Review continuation intent binding drifted",
                )
            outcome = _require_durable_edge_outcome(
                task_store=self._task_store,
                embodiment_runtime_provider=self._embodiment_runtime_provider,
                execution_receipt=receipt,
                command=command,
                normalized_args=normalized,
            )
            bound["acceptance_effect_receipt"] = outcome
            bound["historical_side_effects_executed"] = bool(
                outcome["physical_effect_verified"]
            )
            return bound
        control_result = self._bind_operator_control_review_outcome(
            command_id=operator_command_id,
            execution_receipt=receipt,
            args=args,
            result=bound,
        )
        if control_result is not None:
            return control_result
        state_control_result = (
            await self._bind_operator_state_control_review_outcome(
                command_id=operator_command_id,
                execution_receipt=receipt,
                args=args,
                result=bound,
            )
        )
        if state_control_result is not None:
            return state_control_result
        target_mode = str(args.get("target_mode") or "").strip().lower()
        reason = str(args.get("reason") or "").strip()
        source = str(args.get("source") or "").strip().lower()
        if (
            str(intent.get("action") or "").strip() != _ACTION_KIND
            or str(intent.get("target_id") or "").strip() != _TARGET_ID
            or str(args.get("operator_command_id") or "").strip()
            != "sys_mode.switch"
            or source != TransitionSource.MANUAL.value
            or not target_mode
            or not reason
        ):
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "Operator Review continuation intent binding drifted",
            )
        outcome = _require_durable_sys_mode_outcome(
            task_store=self._task_store,
            mode_transition_service_provider=self._mode_transition_service_provider,
            execution_receipt=receipt,
            request_id=command.request_id,
            target_mode=target_mode,
            source=source,
            reason=reason,
            runtime_scope_id=self._runtime_scope_id,
        )
        bound["acceptance_effect_receipt"] = outcome
        bound["historical_side_effects_executed"] = bool(
            outcome["hard_state_event"] is not None
        )
        return bound

    async def continue_startup_admission(
        self,
        record: Mapping[str, Any],
    ) -> dict[str, Any]:
        """将精确 Operator survivor recovery 委托给 Host spine，保留原耐久 admission。"""

        return await self._runtime.continue_startup_admission(record)

    async def request_graph_cancellation(
        self,
        record: Mapping[str, Any],
        *,
        authority: Mapping[str, Any],
        source: str,
        reason_code: str,
    ) -> dict[str, Any]:
        """请求图取消，但 authority、来源与原因码仍由共享 runtime 逐层验证。"""
        return await self._runtime.request_graph_cancellation(
            record,
            authority=authority,
            source=source,
            reason_code=reason_code,
        )

    async def execute_sys_mode_switch(
        self,
        payload: Mapping[str, Any],
        request_id: str,
    ) -> dict[str, Any]:
        """执行签名 sys_mode.switch：只接受 Ingress 认证的 MANUAL 来源。

        外部请求不能假冒自动策略、Scheduler 或 Skill；新命令经 Governor/Review/runtime，
        成功后还必须由 HardState 耐久 outcome 证明，不能把流终态当作模式已切换。
        """
        value = dict(payload or {})
        supplied_authority = _require_operator_authority(value)
        request_ref = str(request_id or "").strip()
        if not request_ref:
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "Operator request_id is required",
            )
        existing = self._commands.get_by_request(
            surface=HostCommandSurface.OPERATOR.value,
            request_id=request_ref,
        )
        authority = _verify_durable_operator_authority(
            supplied_authority,
            resolver=self._secure_ingress_authority_resolver,
            request_id=request_ref,
            allow_settled_replay=isinstance(existing, Mapping),
            operator_command_id="sys_mode.switch",
            ingress_action=_INGRESS_ACTION,
            ingress_path=_INGRESS_PATH,
            required_scopes=_INGRESS_REQUIRED_SCOPES,
        )
        target_mode = str(value.get("target_mode") or "").strip().lower()
        reason = str(value.get("reason") or "").strip() or "操作员请求切换系统模式"
        # ``obs.command`` is the authenticated source authority.  Caller
        # input cannot impersonate an automatic policy, Scheduler, or Skill
        # in the canonical HardState journal.
        source = TransitionSource.MANUAL.value
        principal_id = str(authority["principal_id"])
        if (
            not target_mode
            or str(value.get("principal_id") or "").strip() != principal_id
            or authority.get("operator_command_id") != "sys_mode.switch"
        ):
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "Operator sys_mode payload differs from Secure Ingress authority",
            )
        message = f"切换系统模式为 {target_mode}；原因：{reason}"
        args = {
            "text": message,
            "route": "delegate",
            "target_id": _TARGET_ID,
            "operator_command_id": "sys_mode.switch",
            "target_mode": target_mode,
            "source": source,
            "reason": reason,
        }
        intent_draft = {
            "route": "delegate",
            "target_id": _TARGET_ID,
            "domain": "organ_ops",
            "task": message,
            "action": _ACTION_KIND,
            "target": "system",
            "args": args,
        }
        received_at_ms = now_ms()
        if isinstance(existing, Mapping):
            command = existing.get("command")
            work_order = existing.get("work_order")
            if (
                not isinstance(command, HostCommand)
                or not isinstance(work_order, WorkOrderV3)
                or str(
                    dict(work_order.metadata or {}).get(
                        "operator_command_id"
                    )
                    or ""
                ).strip()
                != "sys_mode.switch"
            ):
                raise GovernedOperatorRuntimeError(
                    GOVERNED_OPERATOR_RUNTIME_INVALID,
                    "durable Operator replay lacks exact command metadata",
                )
            decision = self._governor_grants.require(
                request_id=request_ref,
                expected_ref=command.governor_grant_ref,
            )
        else:
            decision = dict(
                self._intent_engine.evaluate_intent(
                    raw_intent=_build_governor_intent(
                        request_id=request_ref,
                        principal_id=principal_id,
                        ts_ms=received_at_ms,
                        args=args,
                    ),
                    request_id=request_ref,
                    current_ms=received_at_ms,
                    proposer_node_id="obs_secure_operator_ingress",
                    model_snapshot="server-owned-operator-command",
                    prompt_profile_id="operator-sys-mode-v1",
                )
                or {}
            )
        if str(decision.get("decision") or "").strip().upper() != "APPROVE":
            raise GovernedOperatorRuntimeError(
                str(
                    decision.get("reason_code")
                    or GOVERNED_OPERATOR_GOVERNOR_REJECTED
                ),
                str(decision.get("detail") or "Governor rejected Operator command"),
            )
        observed_mode = _current_mode(self._mode_transition_service_provider)
        terminal: dict[str, Any] | None = None
        try:
            async for raw in self._runtime.execute_stream(
                request_body={"input_source": "OPERATOR"},
                request_id=request_ref,
                session_id=f"operator-{request_ref}",
                ts_ms=received_at_ms,
                intent_draft=intent_draft,
                governor_result=decision,
                person_id=principal_id,
                sanitized_message=message,
                command_authority=authority,
            ):
                event = dict(raw or {})
                if bool(event.get("done")):
                    terminal = event
        except GovernedRuntimeError as exc:
            if exc.reason_code != INTENT_ADMISSION_REVIEW_REQUIRED:
                raise
            return self._pending_review_result(
                request_id=request_ref,
                target_mode=target_mode,
                current_mode=observed_mode,
            )
        if terminal is None:
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "governed Operator runtime emitted no terminal result",
            )
        receipt = terminal.get("execution_receipt")
        receipt_map = dict(receipt) if isinstance(receipt, Mapping) else {}
        effect_summary = dict(
            dict(terminal.get("audit_refs") or {}).get("acceptance_effects") or {}
        )
        accepted = (
            receipt_map.get("accepted") is True
            and str(receipt_map.get("status") or "").strip().upper() == "ACCEPTED"
        )
        durable_outcome = _require_durable_sys_mode_outcome(
            task_store=self._task_store,
            mode_transition_service_provider=(
                self._mode_transition_service_provider
            ),
            execution_receipt=receipt_map,
            request_id=request_ref,
            target_mode=target_mode,
            source=source,
            reason=reason,
            runtime_scope_id=self._runtime_scope_id,
        )
        current_mode = _current_mode(self._mode_transition_service_provider)
        success = accepted and durable_outcome["committed"] is True
        replayed = receipt_map.get("replayed") is True
        return {
            "success": success,
            "side_effects_executed": bool(
                success
                and durable_outcome["hard_state_event"] is not None
                and not replayed
            ),
            "historical_side_effects_executed": bool(
                success and durable_outcome["hard_state_event"] is not None
            ),
            "exact_replay": replayed,
            "target_mode": target_mode,
            "current_mode": current_mode,
            "execution_receipt": receipt_map,
            "acceptance_effects": effect_summary,
            "acceptance_effect_receipt": durable_outcome,
            "graph_id": receipt_map.get("graph_id"),
            "graph_revision": receipt_map.get("graph_revision"),
            "terminal_status": terminal.get("status"),
        }

    async def execute_edge_command_push(
        self,
        payload: Mapping[str, Any],
        request_id: str,
    ) -> dict[str, Any]:
        """执行签名 Edge 命令推送；缺少当前应用 embodiment runtime 时失败关闭。"""
        if self._embodiment_runtime_provider is None:
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "app-local embodiment runtime is unavailable",
            )
        value = dict(payload or {})
        supplied_authority = _require_operator_authority(value)
        request_ref = str(request_id or "").strip()
        if not request_ref:
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "Edge Operator request_id is required",
            )
        existing = self._commands.get_by_request(
            surface=HostCommandSurface.OPERATOR.value,
            request_id=request_ref,
        )
        authority = _verify_durable_operator_authority(
            supplied_authority,
            resolver=self._secure_ingress_authority_resolver,
            request_id=request_ref,
            allow_settled_replay=isinstance(existing, Mapping),
            operator_command_id="edge.command.push",
            ingress_action=_EDGE_INGRESS_ACTION,
            ingress_path=_EDGE_INGRESS_PATH,
            required_scopes=_EDGE_INGRESS_REQUIRED_SCOPES,
        )
        normalized = _normalize_edge_command_args(value)
        principal_id = str(authority["principal_id"])
        if str(value.get("principal_id") or "").strip() != principal_id:
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "Edge Operator payload differs from Secure Ingress authority",
            )
        message = (
            "向边缘表情执行器提交受治理指令："
            f"{normalized['payload']['emotion_tag']}"
        )
        args = {
            "text": message,
            "route": "delegate",
            "target_id": _EDGE_TARGET_ID,
            "embodiment_runtime_scope_id": _embodiment_runtime_scope_id(
                self._embodiment_runtime_provider
            ),
            **normalized,
        }
        intent_draft = {
            "route": "delegate",
            "target_id": _EDGE_TARGET_ID,
            "domain": "edge_device",
            "task": message,
            "action": _EDGE_ACTION_KIND,
            "target": "edge",
            "args": args,
        }
        received_at_ms = now_ms()
        if isinstance(existing, Mapping):
            command = existing.get("command")
            work_order = existing.get("work_order")
            if (
                not isinstance(command, HostCommand)
                or not isinstance(work_order, WorkOrderV3)
                or str(
                    dict(work_order.metadata or {}).get("operator_command_id")
                    or ""
                ).strip()
                != "edge.command.push"
            ):
                raise GovernedOperatorRuntimeError(
                    GOVERNED_OPERATOR_RUNTIME_INVALID,
                    "durable Edge Operator replay lacks exact command metadata",
                )
            decision = self._governor_grants.require(
                request_id=request_ref,
                expected_ref=command.governor_grant_ref,
            )
        else:
            decision = dict(
                self._intent_engine.evaluate_intent(
                    raw_intent=_build_edge_governor_intent(
                        request_id=request_ref,
                        principal_id=principal_id,
                        ts_ms=received_at_ms,
                        args=args,
                    ),
                    request_id=request_ref,
                    current_ms=received_at_ms,
                    proposer_node_id="obs_secure_operator_ingress",
                    model_snapshot="server-owned-edge-command",
                    prompt_profile_id="operator-edge-command-v1",
                )
                or {}
            )
        if str(decision.get("decision") or "").strip().upper() != "APPROVE":
            raise GovernedOperatorRuntimeError(
                str(
                    decision.get("reason_code")
                    or GOVERNED_OPERATOR_GOVERNOR_REJECTED
                ),
                str(decision.get("detail") or "Governor rejected Edge command"),
            )

        terminal: dict[str, Any] | None = None
        try:
            async for raw in self._runtime.execute_stream(
                request_body={"input_source": "OPERATOR"},
                request_id=request_ref,
                session_id=f"operator-{request_ref}",
                ts_ms=received_at_ms,
                intent_draft=intent_draft,
                governor_result=decision,
                person_id=principal_id,
                sanitized_message=message,
                command_authority=authority,
            ):
                event = dict(raw or {})
                if bool(event.get("done")):
                    terminal = event
        except GovernedRuntimeError as exc:
            if exc.reason_code != INTENT_ADMISSION_REVIEW_REQUIRED:
                raise
            return self._pending_edge_review_result(
                request_id=request_ref,
                normalized_args=normalized,
            )
        if terminal is None:
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "governed Edge Operator runtime emitted no terminal result",
            )
        receipt_raw = terminal.get("execution_receipt")
        receipt = dict(receipt_raw) if isinstance(receipt_raw, Mapping) else {}
        accepted = (
            receipt.get("accepted") is True
            and str(receipt.get("status") or "").strip().upper() == "ACCEPTED"
        )
        durable = self._commands.get_by_request(
            surface=HostCommandSurface.OPERATOR.value,
            request_id=request_ref,
        )
        command = durable.get("command") if isinstance(durable, Mapping) else None
        if not isinstance(command, HostCommand):
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "Edge Operator command cannot be reopened",
            )
        outcome = _require_durable_edge_outcome(
            task_store=self._task_store,
            embodiment_runtime_provider=self._embodiment_runtime_provider,
            execution_receipt=receipt,
            command=command,
            normalized_args=normalized,
        )
        replayed = receipt.get("replayed") is True
        success = bool(
            accepted
            and outcome["committed"]
            and outcome["durable_admitted"]
            and outcome["delivery_status"] != "REJECTED"
        )
        effect_summary = dict(
            dict(terminal.get("audit_refs") or {}).get("acceptance_effects")
            or {}
        )
        return {
            "success": success,
            "side_effects_executed": bool(
                success
                and outcome["physical_effect_verified"]
                and not replayed
            ),
            "historical_side_effects_executed": bool(
                outcome["physical_effect_verified"]
            ),
            "exact_replay": replayed,
            "command_type": normalized["command_type"],
            "target_agent": normalized["target_agent"],
            "delivery_status": outcome["delivery_status"],
            "execution_receipt": receipt,
            "acceptance_effects": effect_summary,
            "acceptance_effect_receipt": outcome,
            "graph_id": receipt.get("graph_id"),
            "graph_revision": receipt.get("graph_revision"),
            "terminal_status": terminal.get("status"),
        }

    def _pending_edge_review_result(
        self,
        *,
        request_id: str,
        normalized_args: Mapping[str, Any],
    ) -> dict[str, Any]:
        durable = self._commands.get_by_request(
            surface=HostCommandSurface.OPERATOR.value,
            request_id=request_id,
        )
        command = durable.get("command") if isinstance(durable, Mapping) else None
        if not isinstance(command, HostCommand):
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "durable Edge Operator Review admission is unavailable",
            )
        reviews = _pending_operator_reviews(
            self._review_store,
            graph_id=command.graph_id,
            graph_revision=command.graph_revision,
        )
        return {
            "success": True,
            "accepted": True,
            "side_effects_executed": False,
            "status": "WAITING_REVIEW",
            "reason_code": INTENT_ADMISSION_REVIEW_REQUIRED,
            "command_type": normalized_args["command_type"],
            "target_agent": normalized_args["target_agent"],
            "graph_id": command.graph_id,
            "graph_revision": command.graph_revision,
            "reviews": reviews,
        }

    def _pending_review_result(
        self,
        *,
        request_id: str,
        target_mode: str,
        current_mode: str,
    ) -> dict[str, Any]:
        durable = self._commands.get_by_request(
            surface=HostCommandSurface.OPERATOR.value,
            request_id=request_id,
        )
        command = durable.get("command") if isinstance(durable, Mapping) else None
        if not isinstance(command, HostCommand):
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "durable Operator Review admission is unavailable",
            )
        rows = self._review_store.list_reviews(
            task_graph_id=command.graph_id,
            graph_revision=command.graph_revision,
        )
        reviews = []
        for raw in tuple(rows or ()):
            record = dict(raw or {}) if isinstance(raw, Mapping) else {}
            review = record.get("review")
            review = dict(review) if isinstance(review, Mapping) else {}
            review_id = str(review.get("review_id") or "").strip()
            status = str(review.get("status") or "").strip().upper()
            if review_id and status in {"PENDING", "APPROVED"}:
                reviews.append(
                    {
                        "review_id": review_id,
                        "status": status,
                        "subject_ref": review.get("subject_ref"),
                        "subject_version": review.get("subject_version"),
                        "resume_phase": review.get("resume_phase"),
                        "checkpoint_id": review.get("checkpoint_id"),
                    }
                )
        if len(reviews) != 1:
            raise GovernedOperatorRuntimeError(
                GOVERNED_OPERATOR_RUNTIME_INVALID,
                "Operator Review admission must expose one exact pending ReviewItem",
            )
        return {
            # The authenticated command and immutable graph were accepted;
            # the requested mode effect has not executed.
            "success": True,
            "accepted": True,
            "side_effects_executed": False,
            "status": "WAITING_REVIEW",
            "reason_code": INTENT_ADMISSION_REVIEW_REQUIRED,
            "target_mode": target_mode,
            "current_mode": current_mode,
            "graph_id": command.graph_id,
            "graph_revision": command.graph_revision,
            "reviews": reviews,
        }


def build_governed_operator_runtime(
    *,
    task_runtime_container: Any,
    ledger: Any,
    token_service: Any,
    intent_engine: Any,
    runtime_context_application: RuntimeContextApplicationService,
    action_preview_service: Any,
    acceptance_effect_dispatcher: AcceptanceEffectDispatcher,
    mode_transition_service_provider: Any,
    cancellation_authority_verifier: Any | None,
    secure_ingress_authority_resolver: Any,
    owner_id: str,
    embodiment_runtime_provider: Any | None = None,
    scheduler_command_service_provider: Any | None = None,
    long_term_goal_authority_provider: Any | None = None,
) -> GovernedOperatorRuntime:
    runtime_scope_id = _runtime_scope_from_context(runtime_context_application)
    embodiment_runtime_scope_id = _embodiment_runtime_scope_id(
        embodiment_runtime_provider
    )
    projection_store = getattr(task_runtime_container, "worker_projection_store", None)
    review_store = getattr(task_runtime_container, "review_store", None)
    continuation_store = getattr(
        task_runtime_container,
        "review_continuation_store",
        None,
    )
    if projection_store is None or review_store is None or continuation_store is None:
        raise TypeError("Operator runtime requires Task Runtime projection and Review stores")
    node_grants = NodeBoundGrantService(
        ledger=ledger,
        token_service=token_service,
        review_store=continuation_store,
    )
    node_grant_verifier = DurableNodeGrantVerifier(
        ledger=ledger,
        review_store=continuation_store,
    )
    worker = GovernedFrozenNodeWorker(
        route_kind="delegate",
        executor=FrozenSysModeSwitchExecutor(
            runtime_scope_id=runtime_scope_id,
            embodiment_runtime_scope_id=embodiment_runtime_scope_id,
        ),
        node_grants=node_grant_verifier,
        projections=projection_store,
        action_preview_service=action_preview_service,
        allowed_acceptance_effect_types=frozenset(
            {
                _EFFECT_TYPE,
                _EDGE_EFFECT_TYPE,
                TERMINAL_EFFECT_TYPE,
                OPERATION_RECONCILIATION_EFFECT_TYPE,
                EFFECT_RECONCILIATION_EFFECT_TYPE,
                OPERATOR_CONTROL_EFFECT_TYPE,
                OPERATOR_STATE_CONTROL_EFFECT_TYPE,
            }
        ),
    )
    coordinator = GovernedTaskCoordinator(
        task_store=task_runtime_container.task_store,
        internal_agent_checkpoint_uow=(
            task_runtime_container.internal_agent_checkpoint_uow
        ),
        outbox_publisher=task_runtime_container.outbox_publisher,
        outbox_factory=OutboxMessage,
        worker_resolver=GovernedWorkerRegistry(delegate=worker),
        verifier=_WorkerCommitVerifier(projection_reader=projection_store),
        acceptance=decorate_task_acceptance(
            _WorkerCommitAcceptance(
                projection_reader=projection_store
            ),
            task_runtime_container=task_runtime_container,
        ),
        projection_reader=projection_store,
        review_runtime=TaskNodeReviewRuntime.for_task_store(
            review_store,
            task_runtime_container.task_store,
        ),
        node_grant_verifier=node_grant_verifier,
        acceptance_effects_enabled=True,
        atomic_graph_success_enabled=True,
        execution_registry=getattr(
            task_runtime_container,
            "graph_execution_registry",
            None,
        ),
        owner_id=owner_id,
    )
    application = GovernedCommandApplication(
        command_repository=task_runtime_container.command_repository,
        coordinator=coordinator,
        node_grant_service=node_grants,
        cancellation_authority_verifier=cancellation_authority_verifier,
        intent_admission_verifier=DurableIntentAdmissionVerifier(
            authority_port=runtime_context_application.composition.intent_authority_port,
            intent_arbiter=runtime_context_application.composition.intent_arbiter,
            event_store=runtime_context_application.composition.event_store,
            review_store=review_store,
        ),
    )
    if not isinstance(acceptance_effect_dispatcher, AcceptanceEffectDispatcher):
        raise TypeError(
            "Operator runtime requires the app-local Acceptance Effect dispatcher"
        )
    runtime = GovernedHostRuntime(
        application=application,
        runtime_router=CanonicalRuntimeRouter(),
        command_repository=task_runtime_container.command_repository,
        task_store=task_runtime_container.task_store,
        supported_routes=frozenset({"delegate"}),
        planning_enabled=False,
        acceptance_effect_dispatcher=acceptance_effect_dispatcher,
        delegate_target_validator=_require_operator_target,
        runtime_context_application=runtime_context_application,
    )
    return GovernedOperatorRuntime(
        runtime=runtime,
        command_repository=task_runtime_container.command_repository,
        intent_engine=intent_engine,
        governor_grants=DurableGovernorGrantResolver(ledger),
        review_store=review_store,
        task_store=task_runtime_container.task_store,
        terminal_operator_command_store=(
            task_runtime_container.terminal_operator_command_store
        ),
        mode_transition_service_provider=mode_transition_service_provider,
        scheduler_command_service_provider=(
            scheduler_command_service_provider
        ),
        long_term_goal_authority_provider=(
            long_term_goal_authority_provider
            or (
                lambda: runtime_context_application.composition
                .long_term_goal_authority
            )
        ),
        secure_ingress_authority_resolver=(
            secure_ingress_authority_resolver
        ),
        runtime_scope_id=runtime_scope_id,
        embodiment_runtime_provider=embodiment_runtime_provider,
    )


def build_governed_operator_composition(
    *,
    task_runtime_container: Any,
    ledger: Any,
    token_service: Any,
    intent_engine: Any,
    runtime_context_application: RuntimeContextApplicationService,
    action_preview_service: Any,
    mode_transition_service_provider: Any,
    cancellation_authority_verifier: Any | None,
    secure_ingress_authority_resolver: Any,
    runtime_owner_id: str,
    scheduler_operation_handler: Any | None = None,
    life_command_service_provider: Any | None = None,
    life_receipt_reader_provider: Any | None = None,
    life_projection_dispatcher: Any | None = None,
    relationship_lifecycle_service_provider: Any | None = None,
    gateway_reply_query_port: Any | None = None,
    internal_agent_artifact_store: Any | None = None,
    internal_agent_workspace_runtime: Any | None = None,
    agent_runtime_ports: AgentRuntimeApplicationPorts | None = None,
    embodiment_runtime_provider: Any | None = None,
    scheduler_command_service_provider: Any | None = None,
    episodic_store: Any | None = None,
    knowledge_graph_neo4j_client: Any | None = None,
) -> GovernedOperatorComposition:
    """Build one dispatcher/recovery owner shared by Host and Operator."""

    review_command_service = TaskReviewCommandService(
        review_store=task_runtime_container.review_store,
    )
    knowledge_graph_projection = None
    if knowledge_graph_neo4j_client is not None:
        from host_brain.persistence.context_preference_repository import PreferenceRepository  # noqa: PLC0415
        from host_brain.retrieval.knowledge_graph import KnowledgeGraphProjection  # noqa: PLC0415

        knowledge_graph_projection = KnowledgeGraphProjection(
            preference_repository=PreferenceRepository.from_ledger(ledger),
            neo4j_client=knowledge_graph_neo4j_client,
        )
    dispatcher = AcceptanceEffectDispatcher(
        task_store=task_runtime_container.task_store,
        handlers=build_production_acceptance_effect_handlers(
            mode_transition_service_provider=mode_transition_service_provider,
            scheduler_command_service_provider=(
                scheduler_command_service_provider
            ),
            long_term_goal_authority_provider=(
                lambda: runtime_context_application.composition
                .long_term_goal_authority
            ),
            terminal_reconciliation_service=(
                task_runtime_container.terminal_reconciliation_service
            ),
            terminal_reconciliation_store=task_runtime_container.task_store,
            terminal_operator_command_store=(
                task_runtime_container.terminal_operator_command_store
            ),
            operation_reconciliation_service=(
                task_runtime_container.operation_reconciliation_service
            ),
            effect_reconciliation_service=(
                task_runtime_container.effect_reconciliation_service
            ),
            effect_reconciliation_evidence_store=(
                task_runtime_container.task_store
            ),
            operation_reconciliation_evidence_store=(
                task_runtime_container.task_store
            ),
            operation_reacquisition_continuation_provider=lambda: getattr(
                task_runtime_container,
                "bound_runtime",
                None,
            ),
            scheduler_operation_handler=scheduler_operation_handler,
            internal_result_commit_handler=InternalAcceptedResultHandler(
                command_repository=task_runtime_container.command_repository,
                projection_store=task_runtime_container.worker_projection_store,
                result_repository=task_runtime_container.agent_result_repository,
                artifact_store=internal_agent_artifact_store,
                workspace_runtime=internal_agent_workspace_runtime,
                task_store=task_runtime_container.task_store,
            ),
            agent_workspace_artifact_handler=(
                AcceptedAgentWorkspaceArtifactHandler(
                    command_repository=(
                        task_runtime_container.command_repository
                    ),
                    projection_store=(
                        task_runtime_container.worker_projection_store
                    ),
                    artifact_store=internal_agent_artifact_store,
                    workspace_runtime=internal_agent_workspace_runtime,
                    task_store=task_runtime_container.task_store,
                )
                if internal_agent_artifact_store is not None
                and internal_agent_workspace_runtime is not None
                else None
            ),
            conversation_turn_reader=ContextRepository.from_ledger(ledger),
            conversation_event_reader=MemoryEventRepository.from_ledger(ledger),
            preference_store=UserPreferenceStore(ledger=ledger),
            knowledge_graph_projection=knowledge_graph_projection,
            life_command_service_provider=life_command_service_provider,
            life_receipt_reader_provider=life_receipt_reader_provider,
            life_projection_dispatcher=life_projection_dispatcher,
            relationship_lifecycle_service_provider=(
                relationship_lifecycle_service_provider
            ),
            gateway_reply_query_port=(
                gateway_reply_query_port or query_gateway_reply_delivery
            ),
            agent_runtime_ports=agent_runtime_ports,
            embodiment_runtime_provider=embodiment_runtime_provider,
            graph_cancellation_service_provider=lambda: getattr(
                task_runtime_container,
                "bound_runtime",
                None,
            ),
            secure_ingress_authority_resolver=(
                secure_ingress_authority_resolver
            ),
            task_store=task_runtime_container.task_store,
            review_command_service=review_command_service,
            review_store=task_runtime_container.review_store,
            episodic_store_provider=(
                (lambda: episodic_store) if episodic_store is not None else None
            ),
        ),
        owner_id=f"{runtime_owner_id}::acceptance-effects",
        action_preview_service=action_preview_service,
    )
    effect_runtime = AcceptanceEffectRuntime(
        dispatcher=dispatcher,
        recovery_observer=AcceptanceEffectRecoveryGuardianObserver(
            guardian=runtime_context_application.composition.guardian,
        ),
    )
    runtime = build_governed_operator_runtime(
        task_runtime_container=task_runtime_container,
        ledger=ledger,
        token_service=token_service,
        intent_engine=intent_engine,
        runtime_context_application=runtime_context_application,
        action_preview_service=action_preview_service,
        acceptance_effect_dispatcher=dispatcher,
        mode_transition_service_provider=mode_transition_service_provider,
        scheduler_command_service_provider=(
            scheduler_command_service_provider
        ),
        long_term_goal_authority_provider=(
            lambda: runtime_context_application.composition
            .long_term_goal_authority
        ),
        cancellation_authority_verifier=cancellation_authority_verifier,
        secure_ingress_authority_resolver=secure_ingress_authority_resolver,
        owner_id=f"{runtime_owner_id}::operator",
        embodiment_runtime_provider=embodiment_runtime_provider,
    )
    return GovernedOperatorComposition(
        runtime=runtime,
        acceptance_effect_dispatcher=dispatcher,
        acceptance_effect_runtime=effect_runtime,
        review_command_service=review_command_service,
    )


def _require_operator_target(target_id: str) -> str:
    target = str(target_id or "").strip()
    if target not in {
        _TARGET_ID,
        _EDGE_TARGET_ID,
        TERMINAL_TARGET_ID,
        OPERATION_RECONCILIATION_TARGET_ID,
        EFFECT_RECONCILIATION_TARGET_ID,
        OPERATOR_CONTROL_TARGET_ID,
        OPERATOR_STATE_CONTROL_TARGET_ID,
    }:
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "unsupported governed Operator target",
        )
    return target


def _verify_durable_operator_authority(
    authority: Mapping[str, Any],
    *,
    resolver: Any,
    request_id: str,
    allow_settled_replay: bool,
    operator_command_id: str,
    ingress_action: str,
    ingress_path: str,
    required_scopes: tuple[str, ...],
) -> dict[str, Any]:
    supplied = dict(authority or {})
    audit_ref = _authority_text(supplied, "audit_ref")
    durable_raw = resolver.resolve_committed_authority_by_audit(
        audit_ref=audit_ref,
    )
    if not isinstance(durable_raw, Mapping):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Operator Secure Ingress claim/audit is not durably committed",
        )
    durable = dict(durable_raw)
    expected_scopes = tuple(sorted(required_scopes))
    supplied_scopes = _authority_scopes(supplied.get("required_scopes"))
    durable_scopes = _authority_scopes(durable.get("required_scopes"))
    requested_scopes = _authority_scopes(durable.get("requested_scopes"))
    supplied_body = _authority_sha256(supplied.get("body_sha256"))
    durable_body = _authority_sha256(durable.get("body_sha256"))
    claim_result_ref = str(durable.get("claim_result_ref") or "").strip()
    valid = all(
        (
            supplied.get("allowed") is True,
            supplied.get("duplicate") is False,
            _authority_text(supplied, "request_id") == request_id,
            _authority_text(durable, "request_id") == request_id,
            _authority_text(supplied, "claim_ref")
            == _authority_text(durable, "claim_ref"),
            _authority_text(supplied, "audit_ref")
            == _authority_text(durable, "audit_ref"),
            _authority_text(supplied, "principal_id")
            == _authority_text(durable, "principal_id"),
            _authority_text(supplied, "tenant_id")
            == _authority_text(durable, "tenant_id"),
            _authority_text(supplied, "action") == ingress_action,
            _authority_text(durable, "action") == ingress_action,
            _authority_text(supplied, "ingress_surface") == "obs.command",
            _authority_text(durable, "surface") == "obs.command",
            _authority_text(supplied, "method").upper() == "POST",
            _authority_text(durable, "method").upper() == "POST",
            _authority_text(supplied, "normalized_path") == ingress_path,
            _authority_text(durable, "normalized_path") == ingress_path,
            _authority_text(supplied, "operator_command_id")
            == operator_command_id,
            supplied_scopes == expected_scopes,
            durable_scopes == expected_scopes,
            set(expected_scopes).issubset(requested_scopes),
            _authority_text(durable, "decision").upper() == "ALLOW",
            _authority_text(durable, "reason_code")
            == "SECURE_INGRESS_ALLOWED",
            _authority_text(durable, "stage").upper() == "COMPLETE",
            durable.get("side_effects_executed") is False,
            _authority_text(durable, "claim_status").upper() == "COMMITTED",
            not claim_result_ref or bool(allow_settled_replay),
        )
    )
    if not valid or not hmac.compare_digest(supplied_body, durable_body):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Operator Secure Ingress authority binding drifted",
        )
    return {
        "allowed": True,
        "duplicate": False,
        "request_id": request_id,
        "principal_id": _authority_text(durable, "principal_id"),
        "tenant_id": _authority_text(durable, "tenant_id"),
        "claim_ref": _authority_text(durable, "claim_ref"),
        "audit_ref": _authority_text(durable, "audit_ref"),
        "body_sha256": durable_body,
        "action": ingress_action,
        "required_scopes": expected_scopes,
        "ingress_surface": "obs.command",
        "method": "POST",
        "normalized_path": ingress_path,
        "operator_command_id": operator_command_id,
        "ingress_claim_ref": _authority_text(durable, "claim_ref"),
        "ingress_audit_ref": _authority_text(durable, "audit_ref"),
    }


def _authority_text(value: Mapping[str, Any], field: str) -> str:
    text = str(value.get(field) or "").strip()
    if not text or any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            f"Operator authority field {field} is invalid",
        )
    return text


def _authority_scopes(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Operator authority scopes are invalid",
        )
    return tuple(sorted(set(str(item).strip() for item in value)))


def _authority_sha256(value: Any) -> str:
    digest = str(value or "").strip().lower().removeprefix("sha256:")
    if len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        raise GovernedOperatorRuntimeError(
            GOVERNED_OPERATOR_RUNTIME_INVALID,
            "Operator authority body digest is invalid",
        )
    return digest


def _build_governor_intent(
    *,
    request_id: str,
    principal_id: str,
    ts_ms: int,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    digest = hashlib.sha256(request_id.encode("utf-8")).hexdigest()
    return {
        "schema_version": "Natsume.intent.v1",
        "id": f"intent-operator-sys-mode-{digest[:40]}",
        "ts_ms": int(ts_ms),
        "session_id": f"operator-{request_id}",
        "seq": int(ts_ms),
        "type": "control",
        "action": _ACTION_KIND,
        "target": "system",
        "args": dict(args),
        "nonce": hashlib.sha256(
            f"operator-sys-mode\n{request_id}".encode("utf-8")
        ).hexdigest(),
        "person_id": principal_id,
        "principal_id": principal_id,
        "route": "delegate",
        "target_id": _TARGET_ID,
    }


def _runtime_scope_from_context(
    application: RuntimeContextApplicationService,
) -> str:
    composition = getattr(application, "composition", None)
    guardian = getattr(composition, "guardian", None)
    snapshot = getattr(guardian, "snapshot", None)
    if not callable(snapshot):
        raise TypeError("Operator runtime requires RuntimeGuardian authority")
    authority = snapshot()
    value = (
        authority.get("runtime_scope_id")
        if isinstance(authority, Mapping)
        else getattr(authority, "runtime_scope_id", None)
    )
    return _runtime_scope_id(value)


__all__ = [
    "GOVERNED_OPERATOR_GOVERNOR_REJECTED",
    "GOVERNED_OPERATOR_RUNTIME_INVALID",
    "FrozenSysModeSwitchExecutor",
    "GovernedOperatorComposition",
    "GovernedOperatorRuntime",
    "GovernedOperatorRuntimeError",
    "build_governed_operator_composition",
    "build_governed_operator_runtime",
]
