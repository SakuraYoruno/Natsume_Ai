"""Scheduler、Host 模式与长期目标状态控制的 Acceptance 后执行器。

Effect 已被接受后，本处理器才调用各状态域的事务所有者。写入抛错时不会
立即假定失败：先按同一 operation_id 从真实 owner 只读重开结果；若仍无法
归因则报告 outcome unknown，让 dispatcher 走对账而非重复执行状态变更。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import inspect
import json
from typing import Any

from host_brain.app.acceptance_effect_handler_common import _receipt
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectOutcomeUnknown,
)
from host_brain.task_runtime.operator_state_control_support import (
    LONG_TERM_GOAL_CONTROL_COMMAND_IDS,
    OPERATOR_STATE_CONTROL_EFFECT_TYPE,
    SCHEDULER_ADMIN_CONTROL_COMMAND_IDS,
    STATE_CONTROL_COMMAND_IDS,
    operator_state_control_ingress_requirements,
    operator_state_control_operation_id,
)


_SET_ENABLED_COMMAND_IDS = frozenset(
    {"scheduler.task.enable", "scheduler.task.disable"}
)


class AcceptedOperatorStateControlHandler:
    """提交一份不可变状态控制，并从真实状态 owner 对账其终态。

    Scheduler、模式策略和长期目标各自保持自己的版本/事务边界；handler
    只负责路由到对应 app-local service，绝不从 effect payload 直接写库。
    operation_id 是三条路径共同的幂等和恢复锚点。
    """

    def __init__(
        self,
        *,
        scheduler_command_service_provider: Callable[[], Any],
        mode_transition_service_provider: Callable[[], Any],
        long_term_goal_authority_provider: Callable[[], Any],
        secure_ingress_authority_resolver: Any,
    ) -> None:
        if not callable(scheduler_command_service_provider):
            raise TypeError(
                "scheduler_command_service_provider must be callable"
            )
        if not callable(mode_transition_service_provider):
            raise TypeError(
                "mode_transition_service_provider must be callable"
            )
        if not callable(long_term_goal_authority_provider):
            raise TypeError(
                "long_term_goal_authority_provider must be callable"
            )
        if not callable(
            getattr(
                secure_ingress_authority_resolver,
                "resolve_committed_authority_by_audit",
                None,
            )
        ):
            raise TypeError("Secure Ingress authority resolver is required")
        self._scheduler_provider = scheduler_command_service_provider
        self._mode_provider = mode_transition_service_provider
        self._long_term_goal_provider = long_term_goal_authority_provider
        self._authority_resolver = secure_ingress_authority_resolver

    async def __call__(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        """按命令域申请一次事务变更，随后始终从耐久 owner 读取最终结果。"""
        value = _accepted_operator_state_control_request(payload)
        try:
            if (
                value["operator_command_id"]
                in SCHEDULER_ADMIN_CONTROL_COMMAND_IDS
            ):
                await self._apply_scheduler(value)
            elif (
                value["operator_command_id"]
                in LONG_TERM_GOAL_CONTROL_COMMAND_IDS
            ):
                self._apply_long_term_goal(value)
            else:
                self._apply_mode_policy(value)
        except Exception as exc:
            try:
                return await self.reconcile(payload)
            except AcceptanceEffectOutcomeUnknown:
                # No canonical operation means the transactional writer did
                # not cross its durable boundary. Preserve the typed failure
                # so the dispatcher applies its bounded retry policy.
                raise exc
        return await self.reconcile(payload)

    async def reconcile(
        self,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """不调用写入器地重开精确 operation，供重试和回收 claim 安全使用。"""
        value = _accepted_operator_state_control_request(payload)
        return await resolve_operator_state_control_outcome(
            value,
            scheduler_command_service_provider=self._scheduler_provider,
            mode_transition_service_provider=self._mode_provider,
            long_term_goal_authority_provider=(
                self._long_term_goal_provider
            ),
            secure_ingress_authority_resolver=self._authority_resolver,
        )

    async def _apply_scheduler(self, value: Mapping[str, Any]) -> None:
        """调用 Scheduler 的 accepted-operation 事务入口，并携带重开 Ingress 授权。"""
        args = dict(value["state_args"])
        service = self._scheduler_service()
        command_id = str(value["operator_command_id"])
        if command_id == "scheduler.task.publish":
            method = "apply_accepted_publish_definition"
            kwargs = {
                "task_id": str(args["task_id"]),
                "definition": dict(args["definition"]),
                "expected_head_version": int(
                    args["expected_head_version"]
                ),
                "next_scheduled_at_ms": args["next_scheduled_at_ms"],
            }
        elif command_id == "scheduler.task.delete":
            method = "apply_accepted_delete_definition"
            kwargs = {
                "task_id": str(args["task_id"]),
                "expected_head_version": int(
                    args["expected_head_version"]
                ),
            }
        else:
            method = "apply_accepted_set_enabled"
            kwargs = {
                "task_id": str(args["task_id"]),
                "enabled": bool(args["enabled"]),
                "expected_head_version": int(
                    args["expected_head_version"]
                ),
            }
        apply = getattr(service, method, None)
        if not callable(apply):
            raise RuntimeError(
                "APP_LOCAL_SCHEDULER_STATE_CONTROL_UNAVAILABLE"
            )
        result = apply(
            authority=_reopen_authority(
                value,
                resolver=self._authority_resolver,
            ),
            **kwargs,
        )
        if inspect.isawaitable(result):
            await result

    def _apply_mode_policy(self, value: Mapping[str, Any]) -> None:
        """以 operation_id 和期望 policy 版本写入模式规则或事件，阻断陈旧覆盖。"""
        args = dict(value["state_args"])
        service = self._mode_service()
        command_id = str(value["operator_command_id"])
        if command_id == "sys_mode.auto_rules.update":
            update = getattr(service, "update_auto_rules", None)
            if not callable(update):
                raise RuntimeError(
                    "APP_LOCAL_HOST_MODE_RULE_CONTROL_UNAVAILABLE"
                )
            update(
                dict(args["rules"]),
                operation_id=str(value["operation_id"]),
                expected_policy_revision=int(
                    args["expected_policy_revision"]
                ),
                observed_at_ms=int(args["observed_at_ms"]),
            )
            return
        submit = getattr(service, "on_event", None)
        if not callable(submit):
            raise RuntimeError(
                "APP_LOCAL_HOST_MODE_EVENT_CONTROL_UNAVAILABLE"
            )
        submit(
            str(args["event_type"]),
            {},
            operation_id=str(value["operation_id"]),
            expected_policy_revision=int(args["expected_policy_revision"]),
            observed_at_ms=int(args["observed_at_ms"]),
        )

    def _apply_long_term_goal(self, value: Mapping[str, Any]) -> None:
        """提交长期目标转换；该路径要求同步事务完成以避免未知异步副作用。"""
        args = dict(value["state_args"])
        service = self._long_term_goal_service()
        apply = getattr(service, "apply_accepted_transition", None)
        if not callable(apply):
            raise RuntimeError(
                "APP_LOCAL_LONG_TERM_GOAL_CONTROL_UNAVAILABLE"
            )
        result = apply(
            authority=_reopen_authority(
                value,
                resolver=self._authority_resolver,
            ),
            command_id=str(value["operator_command_id"]),
            operation_id=str(value["operation_id"]),
            workspace_id=str(args["workspace_id"]),
            subject_id=str(args["subject_id"]),
            goal_id=str(args["goal_id"]),
            expected_version=int(args["expected_version"]),
            constraints=tuple(args.get("constraints") or ()),
            observed_at_ms=int(args["observed_at_ms"]),
        )
        if inspect.isawaitable(result):
            raise RuntimeError(
                "APP_LOCAL_LONG_TERM_GOAL_CONTROL_MUST_BE_TRANSACTIONAL"
            )

    def _scheduler_service(self) -> Any:
        service = self._scheduler_provider()
        if service is None:
            raise RuntimeError("APP_LOCAL_SCHEDULER_SERVICE_UNAVAILABLE")
        return service

    def _mode_service(self) -> Any:
        service = self._mode_provider()
        if service is None:
            raise RuntimeError("APP_LOCAL_HOST_MODE_SERVICE_UNAVAILABLE")
        return service

    def _long_term_goal_service(self) -> Any:
        service = self._long_term_goal_provider()
        if service is None:
            raise RuntimeError(
                "APP_LOCAL_LONG_TERM_GOAL_SERVICE_UNAVAILABLE"
            )
        return service


async def resolve_operator_state_control_outcome(
    value: Mapping[str, Any],
    *,
    scheduler_command_service_provider: Callable[[], Any],
    mode_transition_service_provider: Callable[[], Any],
    long_term_goal_authority_provider: Callable[[], Any],
    secure_ingress_authority_resolver: Any,
) -> dict[str, Any]:
    """只读解析一个精确规范结果，不调用任何写入器。

    根据 command 域到对应 owner 查询同一 operation，并重新核对公开身份。
    查询不到精确记录时抛 ``AcceptanceEffectOutcomeUnknown``，使恢复链保持
    对账状态，而不是把“尚不确定”压缩为失败或再次提交。
    """

    payload = dict(value or {})
    command_id = str(payload.get("operator_command_id") or "")
    args = dict(payload.get("state_args") or {})
    if command_id in LONG_TERM_GOAL_CONTROL_COMMAND_IDS:
        service = long_term_goal_authority_provider()
        resolver = getattr(service, "resolve_accepted_transition", None)
        if not callable(resolver):
            raise AcceptanceEffectOutcomeUnknown(
                "long-term-goal accepted-operation reader is unavailable"
            )
        operation = resolver(operation_id=str(payload["operation_id"]))
        if inspect.isawaitable(operation):
            operation = await operation
        if operation is None:
            raise AcceptanceEffectOutcomeUnknown(
                "long-term-goal state control has no exact durable operation"
            )
        identity, public = _long_term_goal_operation_identity(
            payload,
            operation,
        )
    elif command_id in SCHEDULER_ADMIN_CONTROL_COMMAND_IDS:
        service = scheduler_command_service_provider()
        if command_id == "scheduler.task.publish":
            method = "resolve_accepted_publish_definition"
            kwargs = {
                "task_id": str(args["task_id"]),
                "definition": dict(args["definition"]),
                "expected_head_version": int(
                    args["expected_head_version"]
                ),
                "next_scheduled_at_ms": args["next_scheduled_at_ms"],
            }
        elif command_id == "scheduler.task.delete":
            method = "resolve_accepted_delete_definition"
            kwargs = {
                "task_id": str(args["task_id"]),
                "expected_head_version": int(
                    args["expected_head_version"]
                ),
            }
        else:
            method = "resolve_accepted_set_enabled"
            kwargs = {
                "task_id": str(args["task_id"]),
                "enabled": bool(args["enabled"]),
                "expected_head_version": int(
                    args["expected_head_version"]
                ),
            }
        resolver = getattr(service, method, None)
        if not callable(resolver):
            raise AcceptanceEffectOutcomeUnknown(
                "Scheduler accepted-operation reader is unavailable"
            )
        operation = resolver(
            authority=_reopen_authority(
                payload,
                resolver=secure_ingress_authority_resolver,
            ),
            **kwargs,
        )
        if inspect.isawaitable(operation):
            operation = await operation
        if not isinstance(operation, Mapping):
            raise AcceptanceEffectOutcomeUnknown(
                "Scheduler state control has no exact durable operation"
            )
        identity, public = _scheduler_operation_identity(payload, operation)
    else:
        service = mode_transition_service_provider()
        reader = getattr(service, "get_mode_policy_operation", None)
        if not callable(reader):
            raise AcceptanceEffectOutcomeUnknown(
                "Host mode-policy operation reader is unavailable"
            )
        operation = reader(str(payload["operation_id"]))
        if inspect.isawaitable(operation):
            operation = await operation
        if operation is None:
            raise AcceptanceEffectOutcomeUnknown(
                "Host mode state control has no exact durable operation"
            )
        identity, public = _mode_operation_identity(payload, operation)
    return {
        "receipt_ref": operator_state_control_handler_receipt_ref(
            payload,
            outcome_identity=identity,
        ),
        "operator_command_id": command_id,
        "operation_id": payload["operation_id"],
        **public,
    }


def _long_term_goal_operation_identity(
    value: Mapping[str, Any],
    operation: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    args = dict(value["state_args"])
    raw = (
        dict(operation)
        if isinstance(operation, Mapping)
        else dict(operation.to_dict())
        if callable(getattr(operation, "to_dict", None))
        else {}
    )
    command_id = str(value["operator_command_id"])
    expected_status = (
        "ACTIVE" if command_id == "long_term_goal.activate" else "REVOKED"
    )
    constraints = tuple(
        dict(item)
        for item in raw.get("constraints") or ()
        if isinstance(item, Mapping)
    )
    expected_specs = tuple(
        sorted(
            (
                str(item.get("code") or "").strip().upper(),
                str(item.get("disposition") or "").strip().upper(),
                str(item.get("reason") or "").strip(),
            )
            for item in args.get("constraints") or ()
            if isinstance(item, Mapping)
        )
    )
    actual_specs = tuple(
        sorted(
            (
                str(item.get("code") or "").strip().upper(),
                str(item.get("disposition") or "").strip().upper(),
                str(item.get("reason") or "").strip(),
            )
            for item in constraints
        )
    )
    if (
        raw.get("operation_id") != value.get("operation_id")
        or raw.get("workspace_id") != args.get("workspace_id")
        or raw.get("subject_id") != args.get("subject_id")
        or raw.get("goal_id") != args.get("goal_id")
        or raw.get("status") != expected_status
        or type(raw.get("version")) is not int
        or int(raw["version"]) != int(args["expected_version"]) + 1
        or not str(raw.get("goal_ref") or "").startswith(
            "long-term-goal://"
        )
        or not str(raw.get("event_digest") or "").startswith("sha256:")
        or actual_specs != expected_specs
    ):
        raise AcceptanceEffectOutcomeUnknown(
            "long-term-goal durable operation drifted"
        )
    identity = {
        "workspace_id": str(raw["workspace_id"]),
        "subject_id": str(raw["subject_id"]),
        "goal_id": str(raw["goal_id"]),
        "goal_ref": str(raw["goal_ref"]),
        "version": int(raw["version"]),
        "status": str(raw["status"]),
        "event_digest": str(raw["event_digest"]),
    }
    return identity, {
        "workspace_id": identity["workspace_id"],
        "subject_id": identity["subject_id"],
        "goal_id": identity["goal_id"],
        "goal_ref": identity["goal_ref"],
        "goal_version": identity["version"],
        "goal_status": identity["status"],
        "goal_event_digest": identity["event_digest"],
    }


def operator_state_control_handler_receipt_ref(
    payload: Mapping[str, Any],
    *,
    outcome_identity: Mapping[str, Any],
) -> str:
    value = dict(payload or {})
    value.pop("_action_preview_context", None)
    value.pop("_action_preview_terminal_proof", None)
    return _receipt(
        "operator-state-control",
        {
            "operation_id": value.get("operation_id"),
            "operator_command_id": value.get("operator_command_id"),
            "request_id": value.get("request_id"),
            "outcome_identity": dict(outcome_identity or {}),
        },
    )


def _scheduler_operation_identity(
    value: Mapping[str, Any],
    operation: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    args = dict(value["state_args"])
    request = (
        dict(operation.get("request") or {})
        if isinstance(operation.get("request"), Mapping)
        else {}
    )
    result = (
        dict(operation.get("result") or {})
        if isinstance(operation.get("result"), Mapping)
        else {}
    )
    if (
        operation.get("request_id") != value.get("request_id")
        or operation.get("operation_kind") != _scheduler_operation_kind(
            str(value.get("operator_command_id") or "")
        )
        or operation.get("task_id") != args.get("task_id")
        or operation.get("principal_id") != value.get("principal_id")
        or operation.get("ingress_claim_ref")
        != value.get("ingress_claim_ref")
        or operation.get("ingress_audit_ref")
        != value.get("ingress_audit_ref")
        or str(operation.get("body_sha256") or "").removeprefix("sha256:")
        != str(value.get("input_sha256") or "").removeprefix("sha256:")
        or request != _scheduler_operation_request(value)
        or result.get("task_id") != args.get("task_id")
        or not str(operation.get("receipt_ref") or "").strip()
        or not str(operation.get("result_digest") or "").startswith(
            "sha256:"
        )
    ):
        raise AcceptanceEffectOutcomeUnknown(
            "Scheduler state-control durable operation drifted"
        )
    command_id = str(value.get("operator_command_id") or "")
    if command_id in _SET_ENABLED_COMMAND_IDS and (
        result.get("enabled") != args.get("enabled")
    ):
        raise AcceptanceEffectOutcomeUnknown(
            "Scheduler state-control durable result drifted"
        )
    if command_id == "scheduler.task.publish" and (
        dict(result.get("definition") or {})
        != dict(request.get("definition") or {})
        or bool(result.get("retired"))
    ):
        raise AcceptanceEffectOutcomeUnknown(
            "Scheduler publish durable result drifted"
        )
    if command_id == "scheduler.task.delete" and (
        result.get("deleted") is not True
        or result.get("retired") is not True
    ):
        raise AcceptanceEffectOutcomeUnknown(
            "Scheduler delete durable result drifted"
        )
    identity: dict[str, Any] = {
        "receipt_ref": str(operation["receipt_ref"]),
        "result_digest": str(operation["result_digest"]),
        "operation_kind": str(operation["operation_kind"]),
        "task_id": str(result["task_id"]),
        "head_version": int(result.get("head_version") or 0),
    }
    public: dict[str, Any] = {
        "scheduler_operation_receipt_ref": identity["receipt_ref"],
        "scheduler_result_digest": identity["result_digest"],
        "task_id": identity["task_id"],
        "head_version": identity["head_version"],
    }
    if command_id in _SET_ENABLED_COMMAND_IDS:
        identity["enabled"] = bool(result["enabled"])
        public["enabled"] = identity["enabled"]
    elif command_id == "scheduler.task.publish":
        identity["definition_revision"] = int(
            result.get("definition_revision") or 0
        )
        identity["definition"] = dict(result["definition"])
        public.update(
            {
                "definition_revision": identity["definition_revision"],
                "definition": identity["definition"],
                "enabled": bool(result.get("enabled")),
                "next_scheduled_at_ms": result.get(
                    "next_scheduled_at_ms"
                ),
            }
        )
    else:
        identity["deleted"] = True
        public.update({"deleted": True, "retired": True})
    return identity, public


def _scheduler_operation_kind(command_id: str) -> str:
    if command_id == "scheduler.task.publish":
        return "PUBLISH"
    if command_id == "scheduler.task.delete":
        return "DELETE"
    if command_id in _SET_ENABLED_COMMAND_IDS:
        return "SET_ENABLED"
    raise AcceptanceEffectOutcomeUnknown(
        "Scheduler state-control command is unsupported"
    )


def _scheduler_operation_request(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    args = dict(value["state_args"])
    command_id = str(value["operator_command_id"])
    common = {
        "task_id": args["task_id"],
        "expected_head_version": args["expected_head_version"],
    }
    if command_id == "scheduler.task.publish":
        return {
            "task_id": args["task_id"],
            "definition": {
                **dict(args["definition"]),
                "task_id": args["task_id"],
            },
            "expected_head_version": args["expected_head_version"],
            "next_scheduled_at_ms": args["next_scheduled_at_ms"],
        }
    if command_id in _SET_ENABLED_COMMAND_IDS:
        return {
            "task_id": args["task_id"],
            "enabled": args["enabled"],
            "expected_head_version": args["expected_head_version"],
        }
    return common


def _mode_operation_identity(
    value: Mapping[str, Any],
    operation: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    args = dict(value["state_args"])
    event = _object_mapping(operation)
    command_id = str(value["operator_command_id"])
    event_payload = (
        dict(event.get("event_payload") or {})
        if isinstance(event.get("event_payload"), Mapping)
        else {}
    )
    expected_kind = (
        "AUTO_RULES_UPDATED"
        if command_id == "sys_mode.auto_rules.update"
        else "EVENT_SUBMITTED"
    )
    if (
        event.get("operation_id") != value.get("operation_id")
        or event.get("event_kind") != expected_kind
        or type(event.get("revision")) is not int
        or int(event["revision"]) < 1
        or not str(event.get("event_digest") or "").startswith("sha256:")
        or int(event.get("observed_at_ms") or -1)
        != int(args["observed_at_ms"])
    ):
        raise AcceptanceEffectOutcomeUnknown(
            "Host mode-policy durable operation drifted"
        )
    identity = {
        "revision": int(event["revision"]),
        "event_digest": str(event["event_digest"]),
        "event_kind": str(event["event_kind"]),
    }
    if command_id == "sys_mode.auto_rules.update":
        if event_payload != {"rules": dict(args["rules"])}:
            raise AcceptanceEffectOutcomeUnknown(
                "Host mode rules durable operation drifted"
            )
        rules = dict(event.get("auto_rules") or {})
        identity["auto_rules"] = rules
        return identity, {
            "policy_revision": identity["revision"],
            "policy_event_digest": identity["event_digest"],
            "auto_rules": rules,
        }
    expected_input = {"event_type": args["event_type"], "payload": {}}
    if event_payload.get("input") != expected_input:
        raise AcceptanceEffectOutcomeUnknown(
            "Host mode event durable operation drifted"
        )
    outcome_raw = event_payload.get("outcome")
    outcome = dict(outcome_raw) if isinstance(outcome_raw, Mapping) else None
    identity["event_type"] = str(args["event_type"])
    identity["outcome"] = outcome
    return identity, {
        "policy_revision": identity["revision"],
        "policy_event_digest": identity["event_digest"],
        "event_type": identity["event_type"],
        "triggered": outcome is not None,
        "result": outcome,
    }


def _object_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    fields = (
        "runtime_scope_id",
        "revision",
        "operation_id",
        "event_kind",
        "input_digest",
        "event_payload",
        "auto_rules",
        "last_interaction_ts_ms",
        "previous_event_digest",
        "event_digest",
        "observed_at_ms",
        "created_at_ms",
    )
    return {field: getattr(value, field, None) for field in fields}


def _accepted_operator_state_control_request(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    from host_brain.governor.action_preview import (  # noqa: PLC0415
        ActionPreviewService,
    )

    value = dict(payload or {})
    raw_preview = value.pop("_action_preview_terminal_proof", None)
    preview = dict(raw_preview) if isinstance(raw_preview, Mapping) else {}
    required = {
        "operator_command_id",
        "request_id",
        "operation_id",
        "principal_id",
        "ingress_claim_ref",
        "ingress_audit_ref",
        "input_sha256",
        "state_args",
    }
    command_id = str(value.get("operator_command_id") or "").strip()
    state_args = (
        dict(value.get("state_args") or {})
        if isinstance(value.get("state_args"), Mapping)
        else {}
    )
    if (
        set(value) != required
        or command_id not in STATE_CONTROL_COMMAND_IDS
        or value.get("operation_id")
        != operator_state_control_operation_id(
            request_id=str(value.get("request_id") or ""),
            command_id=command_id,
        )
        or state_args.get("operator_command_id") != command_id
        or state_args.get("request_id") != value.get("request_id")
        or state_args.get("operator_actor_id") != value.get("principal_id")
        or set(preview) != {"service", "event_id", "boundary"}
        or not isinstance(preview.get("service"), ActionPreviewService)
        or not str(preview.get("event_id") or "").strip()
        or not isinstance(preview.get("boundary"), Mapping)
    ):
        raise ValueError("accepted Operator state-control payload drifted")
    boundary = dict(preview["boundary"])
    authority_context = boundary.get("authority_context")
    scope = str(boundary.get("action_scope") or "").strip()
    if (
        str(boundary.get("action_kind") or "")
        != f"acceptance-effect.{OPERATOR_STATE_CONTROL_EFFECT_TYPE}"
        or int(boundary.get("risk_level") or 0) != 4
        or boundary.get("action_args") != value
        or not isinstance(authority_context, Mapping)
        or set(authority_context)
        != {
            "schema_version",
            "effect_id",
            "lease_owner_id",
            "fencing_token",
        }
        or authority_context.get("schema_version")
        != "Natsume.action_preview.acceptance_effect_authority_request.v1"
        or str(authority_context.get("effect_id") or "").strip() != scope
        or not scope
        or not str(authority_context.get("lease_owner_id") or "").strip()
        or isinstance(authority_context.get("fencing_token"), bool)
        or int(authority_context.get("fencing_token") or 0) < 1
    ):
        raise ValueError("accepted Operator state-control Preview drifted")
    reopened = preview["service"].require_execution(
        event_id=str(preview["event_id"]),
        **boundary,
    )
    preview_payload = getattr(reopened, "payload", None)
    expected_digest = "sha256:" + hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    if (
        not isinstance(preview_payload, Mapping)
        or preview_payload.get("authority_kind") != "ACCEPTANCE_EFFECT"
        or preview_payload.get("action_kind")
        != f"acceptance-effect.{OPERATOR_STATE_CONTROL_EFFECT_TYPE}"
        or str(preview_payload.get("action_scope") or "") != scope
        or int(preview_payload.get("risk_level") or 0) != 4
        or str(preview_payload.get("action_args_digest") or "").lower()
        != expected_digest
    ):
        raise ValueError(
            "accepted Operator state-control Preview receipt drifted"
        )
    return {**value, "state_args": state_args}


def _reopen_authority(
    value: Mapping[str, Any],
    *,
    resolver: Any,
) -> dict[str, Any]:
    audit_ref = str(value.get("ingress_audit_ref") or "").strip()
    raw = resolver.resolve_committed_authority_by_audit(audit_ref=audit_ref)
    record = dict(raw) if isinstance(raw, Mapping) else {}
    command_id = str(value.get("operator_command_id") or "").strip()
    args = dict(value.get("state_args") or {})
    scheduler_http = (
        command_id in SCHEDULER_ADMIN_CONTROL_COMMAND_IDS
        and "scheduler_ingress_method" in args
    )
    if scheduler_http:
        action = "scheduler:admin"
        path = str(args.get("scheduler_ingress_path") or "").strip()
        method = str(
            args.get("scheduler_ingress_method") or ""
        ).strip().upper()
        scopes = ("scheduler:admin",)
        surface = "scheduler.admin"
    else:
        action, path, scopes = operator_state_control_ingress_requirements(
            command_id
        )
        method = "POST"
        surface = "obs.command"
    body_digest = str(record.get("body_sha256") or "").strip().lower()
    expected_digest = str(value.get("input_sha256") or "").lower().removeprefix(
        "sha256:"
    )
    if (
        str(record.get("audit_ref") or "").strip() != audit_ref
        or str(record.get("claim_ref") or "").strip()
        != str(value.get("ingress_claim_ref") or "").strip()
        or str(record.get("request_id") or "").strip()
        != str(value.get("request_id") or "").strip()
        or str(record.get("principal_id") or "").strip()
        != str(value.get("principal_id") or "").strip()
        or body_digest != expected_digest
        or str(record.get("action") or "").strip() != action
        or str(record.get("surface") or "").strip() != surface
        or str(record.get("method") or "").strip().upper() != method
        or str(record.get("normalized_path") or "").strip() != path
        or tuple(sorted(record.get("required_scopes") or ()))
        != tuple(sorted(scopes))
    ):
        raise ValueError(
            "Operator state-control Secure Ingress binding drifted"
        )
    return {
        "allowed": True,
        "duplicate": bool(record.get("claim_result_ref")),
        "audit_ref": audit_ref,
        "claim_ref": str(record["claim_ref"]),
        "request_id": str(record["request_id"]),
        "principal_id": str(record["principal_id"]),
        "tenant_id": str(record["tenant_id"]),
        "body_sha256": body_digest,
        "action": action,
        "required_scopes": tuple(scopes),
        "ingress_surface": surface,
        "method": method,
        "normalized_path": path,
        "operator_command_id": command_id,
    }


__all__ = [
    "AcceptedOperatorStateControlHandler",
    "operator_state_control_handler_receipt_ref",
    "resolve_operator_state_control_outcome",
]
