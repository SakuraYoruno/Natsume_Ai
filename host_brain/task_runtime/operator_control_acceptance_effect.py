"""Acceptance 后 Operator 控制效果的严格编译器。

控制 worker 只能回传已冻结 intent 的镜像提案；本模块重新核验命令白名单、
操作 ID、Secure Ingress 绑定与节点 Preview Context，再生成内容寻址 effect。
这样取消、Review 批准/拒绝/恢复等高权限控制不会因 worker 输出或过期请求
而扩大范围，真正的状态变化仍由 Acceptance Effect handler 执行。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import re
from typing import Any

from host_brain.task_runtime.operator_control_support import (
    OPERATOR_CONTROL_COMMAND_IDS,
    OPERATOR_CONTROL_EFFECT_TYPE,
    OPERATOR_CONTROL_TARGET_ID,
    operator_control_action_kind,
    operator_control_operation_id,
)
from host_brain.persistence.task_repository import (
    AcceptanceEffectCommitScope,
    AcceptanceEffectIntentSpec,
)
from host_brain.runtime.action_preview_authority import (
    ActionPreviewAuthorityError,
    context_pack_digest_from_work_order,
)
from host_brain.runtime.action_preview_resolver import (
    action_preview_scene_identity,
)
from natsume_contracts.task import HostCommand


_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_DURABLE_REF = re.compile(r"^[a-z][a-z0-9+.-]{1,31}://\S+$")


def compile_operator_control_acceptance_effect(
    *,
    command: HostCommand,
    work_order: Any,
    node_id: str,
    attempt_id: str,
    commit: Mapping[str, Any],
    pack: Mapping[str, Any],
) -> tuple[AcceptanceEffectIntentSpec, ...]:
    """编译单一 Operator 控制提案，并要求它逐字段等于冻结接入。

    控制参数从 intent 复制后排除展示字段；编译器额外绑定 principal、claim、
    audit 和 body 摘要，确保同一控制请求的重放不会换主体或换操作。缺少
    Runtime Context/节点 grant 摘要时失败闭锁，不能创建可执行 effect。
    """
    raw = commit.get("acceptance_effect_intents")
    if not isinstance(raw, Sequence) or isinstance(
        raw,
        (str, bytes, bytearray),
    ):
        raise _error("Operator control must propose one effect")
    if len(raw) != 1:
        raise _error("Operator control must propose exactly one effect")
    descriptor = _mapping(raw[0], "Operator control descriptor")
    if set(descriptor) != {"effect_type", "commit_scope", "payload"}:
        raise _error("Operator control descriptor fields drifted")
    if (
        descriptor.get("effect_type") != OPERATOR_CONTROL_EFFECT_TYPE
        or str(descriptor.get("commit_scope") or "").strip().upper() != "NODE"
    ):
        raise _error("Operator control proposed an unsupported effect")
    proposed = _mapping(descriptor.get("payload"), "Operator control payload")
    if set(proposed) != {
        "operator_command_id",
        "request_id",
        "operation_id",
        "control_args",
    }:
        raise _error("Operator control payload fields drifted")
    control_args = _mapping(
        proposed.get("control_args"),
        "Operator control args",
    )
    command_id = str(proposed.get("operator_command_id") or "").strip()
    if command_id not in OPERATOR_CONTROL_COMMAND_IDS:
        raise _error("Operator control command is unsupported")
    expected_operation = operator_control_operation_id(
        request_id=command.request_id,
        command_id=command_id,
    )
    intent = _mapping(pack.get("intent_draft"), "intent_draft")
    intent_args = _mapping(intent.get("args"), "intent_draft.args")
    expected_control_args = {
        key: value
        for key, value in intent_args.items()
        if key not in {"text", "route", "target_id"}
    }
    metadata = _mapping(
        getattr(work_order, "metadata", {}),
        "work_order.metadata",
    )
    if (
        command.target_id != OPERATOR_CONTROL_TARGET_ID
        or command.route_type != "delegate"
        or proposed.get("request_id") != command.request_id
        or proposed.get("operation_id") != expected_operation
        or control_args != expected_control_args
        or control_args.get("operator_command_id") != command_id
        or control_args.get("request_id") != command.request_id
        or str(intent.get("route") or "").strip().lower() != "delegate"
        or str(intent.get("target_id") or "").strip()
        != OPERATOR_CONTROL_TARGET_ID
        or str(intent.get("target") or "").strip().lower() != "task_runtime"
        or str(intent.get("action") or "").strip()
        != operator_control_action_kind(command_id)
        or str(metadata.get("operator_command_id") or "").strip()
        != command_id
    ):
        raise _error("Operator control differs from frozen admission")
    _validate_control_args(command_id, control_args)
    if (
        not str(command.ingress_claim_ref or "").strip()
        or not str(command.ingress_audit_ref or "").strip()
        or _DIGEST.fullmatch(str(command.input_sha256 or "").strip().lower())
        is None
    ):
        raise _error("Operator control lacks canonical Secure Ingress binding")
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
        "control_args": control_args,
        "_action_preview_context": preview_context,
    }
    return (
        _spec(
            effect_type=OPERATOR_CONTROL_EFFECT_TYPE,
            payload=payload,
            command=command,
            node_id=node_id,
            attempt_id=attempt_id,
        ),
    )


def _validate_control_args(
    command_id: str,
    args: Mapping[str, Any],
) -> None:
    """按控制命令类别校验最小恢复/取消/Review 身份，拒绝混用参数。"""
    if (
        not str(args.get("operator_actor_id") or "").strip()
        or str(args.get("operator_command_id") or "").strip() != command_id
    ):
        raise _error("Operator control actor or command identity is invalid")
    if command_id == "task_graph.cancel":
        if (
            not str(args.get("graph_id") or "").strip()
            or type(args.get("graph_revision")) is not int
            or int(args["graph_revision"]) < 1
            or re.fullmatch(
                r"[A-Z][A-Z0-9_]{1,127}",
                str(args.get("reason_code") or ""),
            )
            is None
        ):
            raise _error("Operator cancellation fields are invalid")
        return
    if not str(args.get("review_id") or "").strip():
        raise _error("Operator Review identity is invalid")
    if command_id == "task_review.reject":
        if args.get("actor_id") != args.get("operator_actor_id"):
            raise _error("Operator Review reject actor drifted")
        return
    if (
        not str(args.get("subject_ref") or "").strip()
        or type(args.get("subject_version")) is not int
        or int(args["subject_version"]) < 0
        or str(args.get("resume_phase") or "")
        not in {"PRE_EXECUTION", "POST_EXECUTION_ACCEPTANCE"}
        or _DURABLE_REF.fullmatch(str(args.get("checkpoint_id") or ""))
        is None
    ):
        raise _error("Operator Review binding is invalid")
    if command_id == "task_review.approve":
        if args.get("actor_id") != args.get("operator_actor_id"):
            raise _error("Operator Review approve actor drifted")
        return
    if (
        _DIGEST.fullmatch(str(args.get("resume_binding_sha256") or ""))
        is None
        or _DURABLE_REF.fullmatch(str(args.get("resume_result_ref") or ""))
        is None
    ):
        raise _error("Operator Review resume binding is invalid")


def _preview_context(
    *,
    command: HostCommand,
    work_order: Any,
    node_id: str,
    worker_commit: Mapping[str, Any],
) -> dict[str, Any]:
    """从冻结 Context 与 worker 审计证据重建节点级 ActionPreview 谱系。"""
    pack = getattr(work_order, "input_pack", None)
    if (
        not isinstance(pack, Mapping)
        or pack.get("runtime_context_schema")
        != "natsume.runtime-context.request.v1"
    ):
        raise _error("Operator control has no Runtime Context")
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
        raise _error("Operator control has no exact node/Scene Preview binding")
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


def _spec(
    *,
    effect_type: str,
    payload: Mapping[str, Any],
    command: HostCommand,
    node_id: str,
    attempt_id: str,
) -> AcceptanceEffectIntentSpec:
    """以规范 payload 和节点尝试生成稳定 effect/幂等身份。"""
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    payload_hex = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    binding = json.dumps(
        {
            "effect_type": effect_type,
            "graph_id": command.graph_id,
            "graph_revision": command.graph_revision,
            "node_id": node_id,
            "attempt_id": attempt_id,
            "payload_sha256": f"sha256:{payload_hex}",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    binding_hex = hashlib.sha256(binding.encode("utf-8")).hexdigest()
    return AcceptanceEffectIntentSpec(
        effect_id=f"acceptance-effect://sha256/{binding_hex}",
        graph_id=command.graph_id,
        graph_revision=command.graph_revision,
        node_id=node_id,
        attempt_id=attempt_id,
        commit_scope=AcceptanceEffectCommitScope.GRAPH,
        effect_type=effect_type,
        payload_ref=f"acceptance-payload://sha256/{payload_hex}",
        payload=json.loads(canonical),
        payload_sha256=f"sha256:{payload_hex}",
        idempotency_key=f"acceptance-idempotency://sha256/{binding_hex}",
    )


def _mapping(value: Any, field: str) -> dict[str, Any]:
    """复制并验证控制提案为有限规范 JSON，避免可变对象跨越接受边界。"""
    if not isinstance(value, Mapping):
        raise _error(f"{field} must be a mapping")
    try:
        return json.loads(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )
    except (TypeError, ValueError) as exc:
        raise _error(f"{field} must be canonical JSON") from exc


def _error(detail: str) -> RuntimeError:
    """延迟取得公共编译异常，保持模块依赖方向而不改变失败契约。"""
    # Lazy import avoids a module cycle: acceptance_effects owns the public
    # exception contract and delegates this one focused compiler at runtime.
    from host_brain.task_runtime.acceptance_effects import (  # noqa: PLC0415
        AcceptanceEffectCompileError,
    )

    return AcceptanceEffectCompileError(detail)


__all__ = ["compile_operator_control_acceptance_effect"]
