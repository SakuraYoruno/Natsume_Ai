"""Acceptance 后 Operator/Scheduler 状态控制的严格编译器。

状态控制影响调度器或 Host 的持久状态平面，因而不能复用普通对话效果。
本模块仅接受白名单命令，并把 worker 提案、冻结 intent、Secure Ingress、
WorkOrder 元数据和节点 Preview 谱系锁为同一 operation；内容寻址身份使
相同接入的重放幂等，而版本/参数变化会显式成为不同操作。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any

from host_brain.persistence.task_repository import (
    AcceptanceEffectIntentSpec,
)
from host_brain.runtime.action_preview_authority import (
    ActionPreviewAuthorityError,
    context_pack_digest_from_work_order,
)
from host_brain.runtime.action_preview_resolver import (
    action_preview_scene_identity,
)
from host_brain.task_runtime.acceptance_effect_support import (
    AcceptanceEffectCompileError as _error,
    _json_mapping as _mapping,
    build_acceptance_effect_intent_spec as _spec,
)
from host_brain.task_runtime.operator_state_control_support import (
    OPERATOR_STATE_CONTROL_EFFECT_TYPE,
    OPERATOR_STATE_CONTROL_TARGET_ID,
    STATE_CONTROL_COMMAND_IDS,
    operator_state_control_action_kind,
    operator_state_control_operation_id,
    operator_state_control_target,
    validate_operator_state_control_args,
)
from natsume_contracts.task import HostCommand, HostCommandSurface


_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def is_operator_state_control_command(command: HostCommand) -> bool:
    """识别允许进入状态平面编译器的专属 target 与受信入口表面。"""
    return (
        command.target_id == OPERATOR_STATE_CONTROL_TARGET_ID
        and command.surface
        in {HostCommandSurface.OPERATOR, HostCommandSurface.SCHEDULER}
    )


def compile_operator_state_control_acceptance_effect(
    *,
    command: HostCommand,
    work_order: Any,
    node_id: str,
    attempt_id: str,
    commit: Mapping[str, Any],
    pack: Mapping[str, Any],
) -> tuple[AcceptanceEffectIntentSpec, ...]:
    """编译一个与冻结 admission 完全相等的状态控制 effect。

    ``state_args`` 不接受 worker 新增或删减字段；命令类型决定合法 target/
    action 与参数规则。写入前绑定请求主体、Ingress claim/audit/body 摘要和
    Runtime Context，确保延迟领取或重放都不能切换到另一状态对象。
    """
    raw = commit.get("acceptance_effect_intents")
    if not isinstance(raw, Sequence) or isinstance(
        raw,
        (str, bytes, bytearray),
    ):
        raise _error("Operator state control must propose one effect")
    if len(raw) != 1:
        raise _error("Operator state control must propose exactly one effect")
    descriptor = _mapping(
        raw[0],
        field_name="Operator state-control descriptor",
        required=True,
    )
    if set(descriptor) != {"effect_type", "commit_scope", "payload"}:
        raise _error("Operator state-control descriptor fields drifted")
    if (
        descriptor.get("effect_type") != OPERATOR_STATE_CONTROL_EFFECT_TYPE
        or str(descriptor.get("commit_scope") or "").strip().upper() != "NODE"
    ):
        raise _error("Operator state control proposed an unsupported effect")
    proposed = _mapping(
        descriptor.get("payload"),
        field_name="Operator state-control payload",
        required=True,
    )
    if set(proposed) != {
        "operator_command_id",
        "request_id",
        "operation_id",
        "state_args",
    }:
        raise _error("Operator state-control payload fields drifted")
    state_args = _mapping(
        proposed.get("state_args"),
        field_name="Operator state-control args",
        required=True,
    )
    command_id = str(proposed.get("operator_command_id") or "").strip()
    if command_id not in STATE_CONTROL_COMMAND_IDS:
        raise _error("state-control command is unsupported")
    expected_operation = operator_state_control_operation_id(
        request_id=command.request_id,
        command_id=command_id,
    )
    intent = _mapping(
        pack.get("intent_draft"),
        field_name="intent_draft",
        required=True,
    )
    intent_args = _mapping(
        intent.get("args"),
        field_name="intent_draft.args",
        required=True,
    )
    expected_state_args = {
        key: value
        for key, value in intent_args.items()
        if key not in {"text", "route", "target_id"}
    }
    metadata = _mapping(
        getattr(work_order, "metadata", {}),
        field_name="work_order.metadata",
        required=True,
    )
    if (
        command.target_id != OPERATOR_STATE_CONTROL_TARGET_ID
        or command.route_type != "delegate"
        or proposed.get("request_id") != command.request_id
        or proposed.get("operation_id") != expected_operation
        or state_args != expected_state_args
        or state_args.get("operator_command_id") != command_id
        or state_args.get("request_id") != command.request_id
        or str(intent.get("route") or "").strip().lower() != "delegate"
        or str(intent.get("target_id") or "").strip()
        != OPERATOR_STATE_CONTROL_TARGET_ID
        or str(intent.get("target") or "").strip().lower()
        != operator_state_control_target(command_id)
        or str(intent.get("action") or "").strip()
        != operator_state_control_action_kind(command_id)
        or str(metadata.get("operator_command_id") or "").strip()
        != command_id
    ):
        raise _error("Operator state control differs from frozen admission")
    validate_operator_state_control_args(command_id, intent_args)
    if (
        not str(command.ingress_claim_ref or "").strip()
        or not str(command.ingress_audit_ref or "").strip()
        or _DIGEST.fullmatch(str(command.input_sha256 or "").strip().lower())
        is None
    ):
        raise _error(
            "Operator state control lacks canonical Secure Ingress binding"
        )
    preview_context = _preview_context(
        command=command,
        work_order=work_order,
        node_id=node_id,
        worker_commit=commit,
    )
    payload = {
        "operator_command_id": command_id,
        "request_id": command.request_id,
        "operation_id": expected_operation,
        "principal_id": command.principal_id,
        "ingress_claim_ref": str(command.ingress_claim_ref),
        "ingress_audit_ref": str(command.ingress_audit_ref),
        "input_sha256": str(command.input_sha256).lower(),
        "state_args": state_args,
        "_action_preview_context": preview_context,
    }
    return (
        _spec(
            effect_type=OPERATOR_STATE_CONTROL_EFFECT_TYPE,
            payload=payload,
            command=command,
            node_id=node_id,
            attempt_id=attempt_id,
        ),
    )


def _preview_context(
    *,
    command: HostCommand,
    work_order: Any,
    node_id: str,
    worker_commit: Mapping[str, Any],
) -> dict[str, Any]:
    """基于冻结场景和 node grant 审计证据构建状态变更的实时 Preview 谱系。"""
    pack = getattr(work_order, "input_pack", None)
    if (
        not isinstance(pack, Mapping)
        or pack.get("runtime_context_schema")
        != "natsume.runtime-context.request.v1"
    ):
        raise _error("Operator state control has no Runtime Context")
    audit_raw = worker_commit.get("audit_refs")
    audit = dict(audit_raw) if isinstance(audit_raw, Mapping) else {}
    grant_ref = str(audit.get("node_grant_ref") or "").strip()
    grant_digest = str(audit.get("node_grant_digest") or "").strip().lower()
    scene = pack.get("scene_identity_v2")
    if (
        not grant_ref
        or _DIGEST.fullmatch(grant_digest) is None
        or not isinstance(scene, Mapping)
    ):
        raise _error(
            "Operator state control has no exact node/Scene Preview binding"
        )
    try:
        context_digest = context_pack_digest_from_work_order(work_order)
        scene_identity = action_preview_scene_identity(dict(scene))
    except ActionPreviewAuthorityError as exc:
        raise _error(exc.detail) from exc
    return {
        "schema_version": "Natsume.acceptance_effect.action_preview_context.v1",
        "context_pack_digest": context_digest,
        "scene_identity": scene_identity,
        "task_graph_id": command.graph_id,
        "graph_revision": command.graph_revision,
        "task_node_id": node_id,
        "parent_governor_grant_ref": command.governor_grant_ref,
        "node_grant_ref": grant_ref,
        "node_grant_digest": grant_digest,
    }


__all__ = ["compile_operator_state_control_acceptance_effect"]
