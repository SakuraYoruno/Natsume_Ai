"""将受治理 Operator 提案编译为规范的 Acceptance Effect。

Operator worker 只能提出一个无授权的结构化描述；编译器把它与冻结 intent、
WorkOrder 元数据和 Runtime Context 逐字段对齐，随后重新生成 effect 身份。
系统模式、边缘命令、终态/操作/Effect 修复均走各自严格分支，不能由通用
delegate 文本或任意 payload 触发。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import math
import re
from typing import Any

from host_brain.persistence.task_repository import AcceptanceEffectIntentSpec
from host_brain.task_runtime.acceptance_effect_support import (
    AcceptanceEffectCompileError,
    _action_preview_context,
    _canonical_json,
    _json_mapping,
    _reject_worker_authority,
    _spec,
)
from host_brain.task_runtime.operator_control_acceptance_effect import (
    compile_operator_control_acceptance_effect,
)
from host_brain.task_runtime.operator_state_control_acceptance_effect import (
    compile_operator_state_control_acceptance_effect,
    is_operator_state_control_command,
)
from host_brain.task_runtime.operation_reconciliation_acceptance_effect import (
    validate_operation_reconciliation_proposal,
)
from natsume_contracts.edge.embodiment_v1 import build_embodiment_command
from natsume_contracts.task import HostCommand


_OPERATOR_SYS_MODE_TARGET = "operator_sys_mode_switch"
_OPERATOR_SYS_MODE_EFFECT = "host.sys_mode_transition"
_OPERATOR_EDGE_TARGET = "operator_edge_command_push"
_OPERATOR_EDGE_EFFECT = "edge.embodiment_enqueue"
_OPERATOR_TERMINAL_TARGET = "operator_task_terminal_reconcile"
_OPERATOR_TERMINAL_EFFECT = "task.terminal_reconciliation"
_OPERATOR_OPERATION_RECONCILIATION_TARGET = "operator_task_operation_reconcile"
_OPERATOR_OPERATION_RECONCILIATION_EFFECT = "task.operation_reconciliation"
_OPERATOR_EFFECT_RECONCILIATION_TARGET = "operator_task_effect_reconcile"
_OPERATOR_EFFECT_RECONCILIATION_EFFECT = "task.acceptance_effect_reconciliation"
_OPERATOR_CONTROL_TARGET = "operator_task_control"
_SYS_MODES = frozenset({"normal", "sleep", "gaming", "focus", "maintenance", "locked"})
_SYS_MODE_SOURCES = frozenset(
    {"manual", "auto_gaming", "auto_idle", "auto_wakeup", "auto_schedule", "skill"}
)


def _compile_operator_effects(
    *,
    command: HostCommand,
    work_order: Any,
    node_id: str,
    attempt_id: str,
    commit: Mapping[str, Any],
    pack: Mapping[str, Any],
) -> tuple[AcceptanceEffectIntentSpec, ...]:
    """按冻结 Operator target 选择唯一专属编译路径，拒绝未声明的路线。"""
    if command.route_type != "delegate":
        raise AcceptanceEffectCompileError("unsupported governed Operator route")
    if command.target_id == _OPERATOR_CONTROL_TARGET:
        return compile_operator_control_acceptance_effect(
            command=command,
            work_order=work_order,
            node_id=node_id,
            attempt_id=attempt_id,
            commit=commit,
            pack=pack,
        )
    if is_operator_state_control_command(command):
        return compile_operator_state_control_acceptance_effect(
            command=command,
            work_order=work_order,
            node_id=node_id,
            attempt_id=attempt_id,
            commit=commit,
            pack=pack,
        )
    if command.target_id == _OPERATOR_TERMINAL_TARGET:
        return _compile_operator_terminal_effect(
            command=command,
            work_order=work_order,
            node_id=node_id,
            attempt_id=attempt_id,
            commit=commit,
            pack=pack,
        )
    if command.target_id == _OPERATOR_OPERATION_RECONCILIATION_TARGET:
        return _compile_operator_operation_reconciliation_effect(
            command=command,
            work_order=work_order,
            node_id=node_id,
            attempt_id=attempt_id,
            commit=commit,
            pack=pack,
        )
    if command.target_id == _OPERATOR_EFFECT_RECONCILIATION_TARGET:
        return _compile_operator_effect_reconciliation_effect(
            command=command,
            work_order=work_order,
            node_id=node_id,
            attempt_id=attempt_id,
            commit=commit,
            pack=pack,
        )
    if command.target_id == _OPERATOR_EDGE_TARGET:
        return _compile_operator_edge_effect(
            command=command,
            work_order=work_order,
            node_id=node_id,
            attempt_id=attempt_id,
            commit=commit,
            pack=pack,
        )
    if command.target_id != _OPERATOR_SYS_MODE_TARGET:
        raise AcceptanceEffectCompileError("unsupported governed Operator route")
    raw = commit.get("acceptance_effect_intents")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise AcceptanceEffectCompileError("Operator worker must propose one effect")
    if len(raw) != 1:
        raise AcceptanceEffectCompileError("Operator worker must propose exactly one effect")
    descriptor = _json_mapping(raw[0], field_name="Operator worker effect descriptor")
    if set(descriptor) != {"effect_type", "commit_scope", "payload"}:
        raise AcceptanceEffectCompileError(
            "Operator descriptor may contain only effect_type, commit_scope and payload"
        )
    if (
        descriptor.get("effect_type") != _OPERATOR_SYS_MODE_EFFECT
        or str(descriptor.get("commit_scope") or "").strip().upper() != "NODE"
    ):
        raise AcceptanceEffectCompileError("Operator worker proposed an unsupported effect")
    payload = _json_mapping(
        descriptor.get("payload"),
        field_name="Operator worker effect payload",
    )
    _reject_worker_authority(payload)
    required_keys = {
        "operator_command_id",
        "request_id",
        "target_mode",
        "source",
        "reason",
        "event_id",
        "runtime_scope_id",
    }
    if set(payload) != required_keys:
        raise AcceptanceEffectCompileError("Operator effect payload fields drifted")
    target_mode = str(payload.get("target_mode") or "").strip().lower()
    source = str(payload.get("source") or "").strip().lower()
    reason = str(payload.get("reason") or "").strip()
    runtime_scope_id = str(payload.get("runtime_scope_id") or "").strip()
    expected_event_id = (
        "obs-sys-mode:"
        + hashlib.sha256(command.request_id.encode("utf-8")).hexdigest()
    )
    if (
        payload.get("operator_command_id") != "sys_mode.switch"
        or payload.get("request_id") != command.request_id
        or target_mode not in _SYS_MODES
        or source not in _SYS_MODE_SOURCES
        or not reason
        or payload.get("event_id") != expected_event_id
        or not runtime_scope_id
        or len(runtime_scope_id.encode("utf-8")) > 512
        or any(
            ord(character) < 32 or ord(character) == 127
            for character in runtime_scope_id
        )
    ):
        raise AcceptanceEffectCompileError("Operator effect identity or value is invalid")
    intent = _json_mapping(pack.get("intent_draft"), field_name="intent_draft")
    args = _json_mapping(intent.get("args"), field_name="intent_draft.args")
    if (
        str(intent.get("action") or "").strip()
        != "operator_sys_mode_transition"
        or str(intent.get("target") or "").strip().lower() != "system"
        or str(intent.get("route") or "").strip().lower() != "delegate"
        or str(intent.get("target_id") or "").strip() != _OPERATOR_SYS_MODE_TARGET
        or str(args.get("operator_command_id") or "").strip() != "sys_mode.switch"
        or str(args.get("target_mode") or "").strip().lower() != target_mode
        or str(args.get("source") or "").strip().lower() != source
        or str(args.get("reason") or "").strip() != reason
    ):
        raise AcceptanceEffectCompileError("Operator effect differs from frozen intent")
    preview_context = _action_preview_context(
        command=command,
        work_order=work_order,
        node_id=node_id,
        worker_commit=commit,
    )
    if preview_context is None:
        raise AcceptanceEffectCompileError(
            "governed Operator effect has no Runtime Context preview binding"
        )
    return (
        _spec(
            effect_type=_OPERATOR_SYS_MODE_EFFECT,
            payload={**payload, "_action_preview_context": preview_context},
            command=command,
            node_id=node_id,
            attempt_id=attempt_id,
        ),
    )


def _compile_operator_edge_effect(
    *,
    command: HostCommand,
    work_order: Any,
    node_id: str,
    attempt_id: str,
    commit: Mapping[str, Any],
    pack: Mapping[str, Any],
) -> tuple[AcceptanceEffectIntentSpec, ...]:
    """把受限情绪表达提案编译为节点绑定的 Edge embodiment 命令。

    设备 payload 只允许规范 emotion/intensity，且必须等于冻结 intent 与
    WorkOrder 元数据。最终 command 的 authority、grant、期限与幂等键均
    在这里重建，worker 无法传入任意边缘指令或长期 token。
    """
    raw = commit.get("acceptance_effect_intents")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise AcceptanceEffectCompileError("Edge Operator must propose one effect")
    if len(raw) != 1:
        raise AcceptanceEffectCompileError(
            "Edge Operator must propose exactly one effect"
        )
    descriptor = _json_mapping(
        raw[0],
        field_name="Edge Operator effect descriptor",
    )
    if set(descriptor) != {"effect_type", "commit_scope", "payload"}:
        raise AcceptanceEffectCompileError(
            "Edge Operator descriptor fields drifted"
        )
    if (
        descriptor.get("effect_type") != _OPERATOR_EDGE_EFFECT
        or str(descriptor.get("commit_scope") or "").strip().upper() != "NODE"
    ):
        raise AcceptanceEffectCompileError(
            "Edge Operator proposed an unsupported effect"
        )
    proposed = _json_mapping(
        descriptor.get("payload"),
        field_name="Edge Operator effect payload",
    )
    _reject_worker_authority(proposed)
    required = {
        "operator_command_id",
        "request_id",
        "command_type",
        "target_agent",
        "payload",
        "runtime_scope_id",
    }
    if set(proposed) != required:
        raise AcceptanceEffectCompileError(
            "Edge Operator effect payload fields drifted"
        )
    device_payload = _json_mapping(
        proposed.get("payload"),
        field_name="Edge Operator device payload",
    )
    if set(device_payload) - {"emotion_tag", "intensity"}:
        raise AcceptanceEffectCompileError(
            "Edge Operator device payload contains unsupported fields"
        )
    emotion_tag = str(device_payload.get("emotion_tag") or "").strip()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", emotion_tag):
        raise AcceptanceEffectCompileError(
            "Edge Operator emotion_tag is not canonical"
        )
    canonical_device_payload: dict[str, Any] = {"emotion_tag": emotion_tag}
    if "intensity" in device_payload:
        intensity = device_payload.get("intensity")
        if (
            isinstance(intensity, bool)
            or not isinstance(intensity, (int, float))
            or not math.isfinite(float(intensity))
            or not 0.0 <= float(intensity) <= 1.0
        ):
            raise AcceptanceEffectCompileError(
                "Edge Operator intensity is invalid"
            )
        canonical_device_payload["intensity"] = float(intensity)
    if device_payload != canonical_device_payload:
        raise AcceptanceEffectCompileError(
            "Edge Operator device payload is not canonical"
        )

    intent = _json_mapping(pack.get("intent_draft"), field_name="intent_draft")
    args = _json_mapping(intent.get("args"), field_name="intent_draft.args")
    metadata = _json_mapping(
        getattr(work_order, "metadata", {}),
        field_name="work_order.metadata",
    )
    runtime_scope_id = str(proposed.get("runtime_scope_id") or "").strip()
    if (
        proposed.get("operator_command_id") != "edge.command.push"
        or proposed.get("request_id") != command.request_id
        or proposed.get("command_type") != "emotion_state_command"
        or proposed.get("target_agent") != "edge.somatic"
        or not runtime_scope_id
        or len(runtime_scope_id.encode("utf-8")) > 512
        or any(
            ord(character) < 32 or ord(character) == 127
            for character in runtime_scope_id
        )
        or str(intent.get("action") or "").strip()
        != "operator_edge_command_push"
        or str(intent.get("target") or "").strip().lower() != "edge"
        or str(intent.get("route") or "").strip().lower() != "delegate"
        or str(intent.get("target_id") or "").strip()
        != _OPERATOR_EDGE_TARGET
        or str(args.get("operator_command_id") or "").strip()
        != "edge.command.push"
        or str(args.get("command_type") or "").strip()
        != "emotion_state_command"
        or str(args.get("target_agent") or "").strip() != "edge.somatic"
        or str(args.get("embodiment_runtime_scope_id") or "").strip()
        != runtime_scope_id
        or _json_mapping(
            args.get("payload"),
            field_name="intent_draft.args.payload",
        )
        != canonical_device_payload
        or str(metadata.get("operator_command_id") or "").strip()
        != "edge.command.push"
    ):
        raise AcceptanceEffectCompileError(
            "Edge Operator effect differs from frozen intent"
        )

    preview_context = _action_preview_context(
        command=command,
        work_order=work_order,
        node_id=node_id,
        worker_commit=commit,
    )
    if preview_context is None:
        raise AcceptanceEffectCompileError(
            "governed Edge effect has no Runtime Context preview binding"
        )
    identity_digest = hashlib.sha256(
        _canonical_json(
            {
                "graph_id": command.graph_id,
                "graph_revision": command.graph_revision,
                "node_id": node_id,
                "attempt_id": attempt_id,
                "request_id": command.request_id,
                "command_type": "emotion_state_command",
                "target_agent": "edge.somatic",
                "payload": canonical_device_payload,
            }
        ).encode("utf-8")
    ).hexdigest()
    issued_at_ms = int(command.created_at_ms)
    embodiment_command = build_embodiment_command(
        kind="avatar",
        action="set_expression",
        payload=canonical_device_payload,
        authority_ref=f"authority://task-acceptance/sha256/{identity_digest}",
        grant_ref=str(preview_context["node_grant_ref"]),
        deadline_ms=issued_at_ms + 7 * 24 * 60 * 60 * 1000,
        idempotency_key=f"task-edge-effect:{identity_digest}",
        request_id=command.request_id,
        issued_at_ms=issued_at_ms,
    )
    payload = {
        "operator_command_id": "edge.command.push",
        "request_id": command.request_id,
        "command_type": "emotion_state_command",
        "target_agent": "edge.somatic",
        "runtime_scope_id": runtime_scope_id,
        "embodiment_command": embodiment_command.as_dict(),
        "_action_preview_context": preview_context,
    }
    return (
        _spec(
            effect_type=_OPERATOR_EDGE_EFFECT,
            payload=payload,
            command=command,
            node_id=node_id,
            attempt_id=attempt_id,
        ),
    )


def _compile_operator_terminal_effect(
    *,
    command: HostCommand,
    work_order: Any,
    node_id: str,
    attempt_id: str,
    commit: Mapping[str, Any],
    pack: Mapping[str, Any],
) -> tuple[AcceptanceEffectIntentSpec, ...]:
    """验证签名终态修复引用，并封装为可 Preview 重开的单一修复效果。"""
    raw = commit.get("acceptance_effect_intents")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise AcceptanceEffectCompileError("terminal Operator must propose one effect")
    if len(raw) != 1:
        raise AcceptanceEffectCompileError(
            "terminal Operator must propose exactly one effect"
        )
    descriptor = _json_mapping(
        raw[0], field_name="terminal Operator effect descriptor"
    )
    if set(descriptor) != {"effect_type", "commit_scope", "payload"}:
        raise AcceptanceEffectCompileError(
            "terminal Operator descriptor fields drifted"
        )
    if (
        descriptor.get("effect_type") != _OPERATOR_TERMINAL_EFFECT
        or str(descriptor.get("commit_scope") or "").strip().upper() != "NODE"
    ):
        raise AcceptanceEffectCompileError(
            "terminal Operator proposed an unsupported effect"
        )
    payload = _json_mapping(
        descriptor.get("payload"),
        field_name="terminal Operator effect payload",
    )
    _reject_worker_authority(payload)
    required_keys = {
        "operator_command_id",
        "request_id",
        "case_id",
        "signed_command_ref",
        "signed_command_sha256",
    }
    if set(payload) != required_keys:
        raise AcceptanceEffectCompileError(
            "terminal Operator effect payload fields drifted"
        )
    intent = _json_mapping(pack.get("intent_draft"), field_name="intent_draft")
    args = _json_mapping(intent.get("args"), field_name="intent_draft.args")
    expected = {
        "operator_command_id": "task_terminal.reconcile",
        "case_id": str(args.get("case_id") or "").strip(),
        "signed_command_ref": str(args.get("signed_command_ref") or "").strip(),
        "signed_command_sha256": str(
            args.get("signed_command_sha256") or ""
        ).strip(),
    }
    if (
        payload.get("request_id") != command.request_id
        or {key: payload.get(key) for key in expected} != expected
        or not expected["case_id"]
        or not re.fullmatch(
            r"terminal-command://sha256/[0-9a-f]{64}",
            expected["signed_command_ref"],
        )
        or not re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            expected["signed_command_sha256"],
        )
        or str(intent.get("action") or "").strip()
        != "operator_task_terminal_reconciliation"
        or str(intent.get("target") or "").strip().lower() != "system"
        or str(intent.get("route") or "").strip().lower() != "delegate"
        or str(intent.get("target_id") or "").strip()
        != _OPERATOR_TERMINAL_TARGET
    ):
        raise AcceptanceEffectCompileError(
            "terminal Operator effect differs from frozen intent"
        )
    preview_context = _action_preview_context(
        command=command,
        work_order=work_order,
        node_id=node_id,
        worker_commit=commit,
    )
    if preview_context is None:
        raise AcceptanceEffectCompileError(
            "terminal Operator effect has no Runtime Context preview binding"
        )
    return (
        _spec(
            effect_type=_OPERATOR_TERMINAL_EFFECT,
            payload={**payload, "_action_preview_context": preview_context},
            command=command,
            node_id=node_id,
            attempt_id=attempt_id,
        ),
    )


def _compile_operator_operation_reconciliation_effect(
    *,
    command: HostCommand,
    work_order: Any,
    node_id: str,
    attempt_id: str,
    commit: Mapping[str, Any],
    pack: Mapping[str, Any],
) -> tuple[AcceptanceEffectIntentSpec, ...]:
    """验证操作对账提案完全来源于冻结 intent/元数据，再附加节点 Preview 谱系。"""
    intent = _json_mapping(pack.get("intent_draft"), field_name="intent_draft")
    args = _json_mapping(intent.get("args"), field_name="intent_draft.args")
    try:
        payload = validate_operation_reconciliation_proposal(
            raw_effects=commit.get("acceptance_effect_intents"),
            frozen_intent=intent,
            frozen_args=args,
            work_order_metadata=_json_mapping(
                getattr(work_order, "metadata", {}),
                field_name="work_order.metadata",
            ),
            request_id=command.request_id,
        )
    except ValueError as exc:
        raise AcceptanceEffectCompileError(str(exc)) from exc
    _reject_worker_authority(payload)
    preview_context = _action_preview_context(
        command=command,
        work_order=work_order,
        node_id=node_id,
        worker_commit=commit,
    )
    if preview_context is None:
        raise AcceptanceEffectCompileError(
            "Operation reconciliation has no Runtime Context preview binding"
        )
    return (
        _spec(
            effect_type=_OPERATOR_OPERATION_RECONCILIATION_EFFECT,
            payload={**payload, "_action_preview_context": preview_context},
            command=command,
            node_id=node_id,
            attempt_id=attempt_id,
        ),
    )


def _compile_operator_effect_reconciliation_effect(
    *,
    command: HostCommand,
    work_order: Any,
    node_id: str,
    attempt_id: str,
    commit: Mapping[str, Any],
    pack: Mapping[str, Any],
) -> tuple[AcceptanceEffectIntentSpec, ...]:
    """编译通用未知 Effect 的 Operator 对账请求。

    evidence/case/effect/purpose/outcome 必须与冻结参数逐项相同，且 purpose
    只允许 ``GENERAL_UNKNOWN_EFFECT``；签名挑战与最终证明仍在后续对账服务
    完成，本层仅确保这条高权限路径没有被 worker 改写。
    """
    raw = commit.get("acceptance_effect_intents")
    if not isinstance(raw, Sequence) or isinstance(
        raw,
        (str, bytes, bytearray),
    ):
        raise AcceptanceEffectCompileError(
            "Acceptance Effect reconciliation Operator must propose one effect"
        )
    if len(raw) != 1:
        raise AcceptanceEffectCompileError(
            "Acceptance Effect reconciliation Operator must propose exactly "
            "one effect"
        )
    descriptor = _json_mapping(
        raw[0],
        field_name="Acceptance Effect reconciliation descriptor",
    )
    if set(descriptor) != {"effect_type", "commit_scope", "payload"}:
        raise AcceptanceEffectCompileError(
            "Acceptance Effect reconciliation descriptor fields drifted"
        )
    if (
        descriptor.get("effect_type")
        != _OPERATOR_EFFECT_RECONCILIATION_EFFECT
        or str(descriptor.get("commit_scope") or "").strip().upper()
        != "NODE"
    ):
        raise AcceptanceEffectCompileError(
            "Acceptance Effect reconciliation proposed an unsupported effect"
        )
    payload = _json_mapping(
        descriptor.get("payload"),
        field_name="Acceptance Effect reconciliation payload",
    )
    _reject_worker_authority(payload)
    intent = _json_mapping(pack.get("intent_draft"), field_name="intent_draft")
    args = _json_mapping(intent.get("args"), field_name="intent_draft.args")
    request_fields = {
        "evidence_ref",
        "evidence_digest",
        "effect_id",
        "case_id",
        "purpose",
        "outcome",
        "reconciler_actor_id",
    }
    required_keys = {
        "operator_command_id",
        "request_id",
        *request_fields,
    }
    expected = {
        "operator_command_id": "task_effect.reconcile",
        "request_id": command.request_id,
        **{field: args.get(field) for field in request_fields},
    }
    evidence_ref = str(expected.get("evidence_ref") or "")
    metadata = _json_mapping(
        getattr(work_order, "metadata", {}),
        field_name="work_order.metadata",
    )
    if (
        set(payload) != required_keys
        or payload != expected
        or not re.fullmatch(
            r"effect-reconcile-evidence://sha256/[0-9a-f]{64}",
            evidence_ref,
        )
        or str(expected.get("evidence_digest") or "")
        != "sha256:" + evidence_ref.rsplit("/", 1)[-1]
        or not re.fullmatch(
            r"[a-z][a-z0-9+.-]{1,31}://\S+",
            str(expected.get("effect_id") or ""),
        )
        or not re.fullmatch(
            r"[a-z][a-z0-9+.-]{1,31}://\S+",
            str(expected.get("case_id") or ""),
        )
        or str(expected.get("purpose") or "")
        != "GENERAL_UNKNOWN_EFFECT"
        or str(expected.get("outcome") or "")
        not in {"NO_SIDE_EFFECT", "SIDE_EFFECT_CONFIRMED"}
        or not str(expected.get("reconciler_actor_id") or "").strip()
        or str(intent.get("action") or "").strip()
        != "operator_task_effect_reconciliation"
        or str(intent.get("target") or "").strip().lower()
        != "task_runtime"
        or str(intent.get("route") or "").strip().lower() != "delegate"
        or str(intent.get("target_id") or "").strip()
        != _OPERATOR_EFFECT_RECONCILIATION_TARGET
        or str(metadata.get("operator_command_id") or "").strip()
        != "task_effect.reconcile"
    ):
        raise AcceptanceEffectCompileError(
            "Acceptance Effect reconciliation differs from frozen intent"
        )
    preview_context = _action_preview_context(
        command=command,
        work_order=work_order,
        node_id=node_id,
        worker_commit=commit,
    )
    if preview_context is None:
        raise AcceptanceEffectCompileError(
            "Acceptance Effect reconciliation has no Runtime Context preview "
            "binding"
        )
    return (
        _spec(
            effect_type=_OPERATOR_EFFECT_RECONCILIATION_EFFECT,
            payload={**payload, "_action_preview_context": preview_context},
            command=command,
            node_id=node_id,
            attempt_id=attempt_id,
        ),
    )
