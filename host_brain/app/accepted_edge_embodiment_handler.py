"""已接受 Edge 具身效果的入队处理器。

本模块只负责将一条已被 Preview/Acceptance 绑定的 ``EmbodimentCommand`` 写入
本地 Host durable outbox；真正的物理设备分发仍由 ``HostEmbodimentRuntime``
持有。因而该层既不重新规划动作，也不把入队成功误作设备已执行。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import json
from typing import Any

from host_brain.app.acceptance_effect_handler_common import _canonical_id_ref
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectOutcomeUnknown,
)


class AcceptedEdgeEmbodimentEnqueueHandler:
    """将一条已接受物理命令幂等写入本地 durable outbox。

命令与 Preview execution receipt 必须同时匹配 risk-4 authority boundary。
入队抛异常时先重开同一 command 行：若不存在才允许 dispatcher 正常重试，
因为这里尚未跨越外部物理副作用边界；若存在但身份不一致则保留结果未知。
"""

    def __init__(self, *, runtime_provider: Callable[[], Any]) -> None:
        if not callable(runtime_provider):
            raise TypeError("runtime_provider must be callable")
        self._runtime_provider = runtime_provider

    def __call__(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        value, command, runtime, preview = _accepted_edge_embodiment_request(
            payload,
            runtime_provider=self._runtime_provider,
        )
        try:
            ack = runtime.enqueue(
                command,
                action_preview_service=preview["service"],
                action_preview_event_id=preview["event_id"],
                action_preview_boundary=preview["boundary"],
            )
        except Exception as exc:
            try:
                return self.reconcile(payload)
            except AcceptanceEffectOutcomeUnknown:
                # Enqueue is exact-idempotent. If no row exists, a normal
                # dispatcher retry is safe; this was not an external effect.
                raise exc
        reopened = self.reconcile(payload)
        if (
            str(getattr(ack, "outbox_id", "") or "")
            != str(reopened.get("outbox_id") or "")
            or str(getattr(ack, "command_id", "") or "") != command.command_id
            or str(getattr(ack, "idempotency_key", "") or "")
            != command.idempotency_key
        ):
            raise AcceptanceEffectOutcomeUnknown(
                "Edge embodiment enqueue acknowledgement differs from its "
                "durable outbox row"
            )
        return {
            **reopened,
            "operator_command_id": value["operator_command_id"],
        }

    def reconcile(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        _value, command, runtime, _preview = _accepted_edge_embodiment_request(
            payload,
            runtime_provider=self._runtime_provider,
        )
        reopen = getattr(runtime, "reopen_outbox_command", None)
        if not callable(reopen):
            raise AcceptanceEffectOutcomeUnknown(
                "app-local embodiment runtime exposes no exact outbox reader"
            )
        row = reopen(command)
        if not isinstance(row, Mapping):
            raise AcceptanceEffectOutcomeUnknown(
                "accepted Edge embodiment command has no exact durable outbox row"
            )
        durable = dict(row)
        outbox_id = str(durable.get("outbox_id") or "").strip()
        status = str(durable.get("status") or "").strip().upper()
        if (
            not outbox_id
            or str(durable.get("runtime_scope_id") or "").strip()
            != str(getattr(runtime, "runtime_scope_id", "") or "").strip()
            or str(durable.get("command_id") or "").strip() != command.command_id
            or str(durable.get("idempotency_key") or "").strip()
            != command.idempotency_key
            or status
            not in {"PENDING", "DISPATCHING", "IN_DOUBT", "APPLIED", "REJECTED"}
        ):
            raise AcceptanceEffectOutcomeUnknown(
                "accepted Edge embodiment durable row identity drifted"
            )
        receipt = durable.get("receipt")
        receipt_ref = (
            str(receipt.get("receipt_ref") or "").strip()
            if isinstance(receipt, Mapping)
            else ""
        )
        return {
            "receipt_ref": edge_embodiment_handler_receipt_ref(outbox_id),
            "outbox_id": outbox_id,
            "command_id": command.command_id,
            "idempotency_key": command.idempotency_key,
            "status": status,
            "physical_receipt_ref": receipt_ref or None,
        }


def edge_embodiment_handler_receipt_ref(outbox_id: str) -> str:
    """把内部 outbox 主键转换为跨处理器可引用的 canonical receipt。"""
    return _canonical_id_ref("embodiment-outbox", outbox_id)


def _accepted_edge_embodiment_request(
    payload: Mapping[str, Any],
    *,
    runtime_provider: Callable[[], Any],
) -> tuple[dict[str, Any], Any, Any, dict[str, Any]]:
    """校验命令、运行时作用域与 Preview 授权仍是同一已接受动作。

``runtime_scope_id`` 防止旧进程或另一设备运行时接管该命令；解析后的命令还
限制为无 lease/fencing 的 avatar expression，避免此效果类型被借用来携带
更广泛的执行权限。最后 ``require_execution`` 复核 Preview 回执的参数摘要，
使入队时的 risk-4 许可不能被已接受 payload 漂移绕过。
    """
    from host_brain.governor.action_preview import ActionPreviewService  # noqa: PLC0415
    from natsume_contracts.edge.embodiment_v1 import (  # noqa: PLC0415
        parse_embodiment_command,
    )

    value = dict(payload or {})
    raw_preview = value.pop("_action_preview_terminal_proof", None)
    preview = dict(raw_preview) if isinstance(raw_preview, Mapping) else {}
    required = {
        "operator_command_id",
        "request_id",
        "command_type",
        "target_agent",
        "runtime_scope_id",
        "embodiment_command",
    }
    if (
        set(value) != required
        or value.get("operator_command_id") != "edge.command.push"
        or value.get("command_type") != "emotion_state_command"
        or value.get("target_agent") != "edge.somatic"
        or set(preview) != {"service", "event_id", "boundary"}
        or not isinstance(preview.get("service"), ActionPreviewService)
        or not str(preview.get("event_id") or "").strip()
        or not isinstance(preview.get("boundary"), Mapping)
    ):
        raise ValueError("accepted Edge embodiment effect payload drifted")
    try:
        command = parse_embodiment_command(value["embodiment_command"])
    except (TypeError, ValueError) as exc:
        raise ValueError("accepted Edge embodiment command is invalid") from exc
    runtime = runtime_provider()
    runtime_scope_id = str(value.get("runtime_scope_id") or "").strip()
    if (
        runtime is None
        or not callable(getattr(runtime, "enqueue", None))
        or not callable(getattr(runtime, "reopen_outbox_command", None))
        or not runtime_scope_id
        or str(getattr(runtime, "runtime_scope_id", "") or "").strip()
        != runtime_scope_id
        or command.kind != "avatar"
        or command.action != "set_expression"
        or command.request_id != str(value.get("request_id") or "").strip()
        or command.session_id
        or command.lease_id
        or command.fencing_token != 0
        or not command.authority_ref.startswith(
            "authority://task-acceptance/sha256/"
        )
        or not command.grant_ref
        or set(command.payload) - {"emotion_tag", "intensity"}
        or not str(command.payload.get("emotion_tag") or "").strip()
    ):
        raise ValueError("accepted Edge embodiment runtime binding drifted")

    boundary = dict(preview["boundary"])
    authority_context = boundary.get("authority_context")
    action_scope = str(boundary.get("action_scope") or "").strip()
    if (
        str(boundary.get("action_kind") or "")
        != "acceptance-effect.edge.embodiment_enqueue"
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
        or str(authority_context.get("effect_id") or "").strip() != action_scope
        or not action_scope
        or not str(authority_context.get("lease_owner_id") or "").strip()
        or isinstance(authority_context.get("fencing_token"), bool)
        or int(authority_context.get("fencing_token") or 0) < 1
    ):
        raise ValueError("accepted Edge embodiment Preview boundary drifted")
    reopened = preview["service"].require_execution(
        event_id=str(preview["event_id"]),
        **boundary,
    )
    preview_payload = getattr(reopened, "payload", None)
    expected_args_digest = "sha256:" + hashlib.sha256(
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
        != "acceptance-effect.edge.embodiment_enqueue"
        or str(preview_payload.get("action_scope") or "").strip()
        != action_scope
        or int(preview_payload.get("risk_level") or 0) != 4
        or str(preview_payload.get("action_args_digest") or "").lower()
        != expected_args_digest
    ):
        raise ValueError("accepted Edge embodiment Preview receipt drifted")
    return value, command, runtime, {
        "service": preview["service"],
        "event_id": str(preview["event_id"]),
        "boundary": boundary,
    }


__all__ = [
    "AcceptedEdgeEmbodimentEnqueueHandler",
    "edge_embodiment_handler_receipt_ref",
]
