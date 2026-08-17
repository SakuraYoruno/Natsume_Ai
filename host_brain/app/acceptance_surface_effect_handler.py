"""Gateway 表面回复效果的提交处理器与只读恢复器。

一条回复先由 Acceptance 固化内容、目的地与 effect identity，随后本模块才把
它交给 Gateway durable outbox。网络调用跨越外部副作用边界后，任何非终态
异常都不能直接重试；处理器必须以经过认证的 outbox 查询精确归因，或明确
保留“结果未知”，从而避免重复向用户发送可见消息。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import inspect
import json
import re
from typing import Any

from host_brain.app.acceptance_effect_handler_common import (
    _canonical_id_ref,
    _is_exact_effect_identity,
    _opaque_failure,
)
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectOutcomeUnknown,
)
from host_brain.runtime.acceptance_effect_execution import (
    AcceptanceEffectExecutionProof,
)


class _AcceptedSurfaceNoEffectFailure(RuntimeError):
    """Gateway 经认证地证明未产生外部副作用，才允许普通重试。"""


class _AcceptedSurfaceReplyDeliveryHandler:
    """只提交一次回复；恢复阶段只能查询精确 Gateway outbox。

    query port 是 Host 与 Gateway 之间的认证读取 authority。没有它时，已越过
    transport 的失败永远不能推断为“未发送”；dispatcher 必须得到 outcome
    unknown，而非把副作用再次 POST 到表面渠道。
    """

    def __init__(self, *, query_port: Callable[..., Any] | None) -> None:
        if query_port is not None and not callable(query_port):
            raise TypeError("gateway_reply_query_port must be callable")
        self._query_port = query_port

    async def __call__(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        # Reject request/proof drift before entering the transport.  Once the
        # transport has been invoked, any non-terminal exception may have been
        # raised after Gateway intake and therefore cannot be retried safely.
        _accepted_surface_invocation(payload)
        try:
            return await _deliver_accepted_surface_reply(payload)
        except _AcceptedSurfaceNoEffectFailure:
            # The authenticated transport explicitly proved that it did not
            # enqueue or send anything.  Only this narrow result is safe for
            # the dispatcher's ordinary failure/retry policy.
            raise
        except Exception as exc:
            try:
                return await self.reconcile(payload)
            except _AcceptedSurfaceNoEffectFailure:
                raise
            except AcceptanceEffectOutcomeUnknown as reconciliation:
                raise AcceptanceEffectOutcomeUnknown(
                    "surface reply invocation crossed the transport boundary "
                    "without exact Gateway attribution after "
                    f"{_opaque_failure(exc)}; {reconciliation.detail}"
                ) from exc

    async def reconcile(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        value = _accepted_surface_request(payload)
        if self._query_port is None:
            raise AcceptanceEffectOutcomeUnknown(
                "surface reply delivery cannot be read-only reconciled: the Host "
                "has no authenticated Gateway outbox query port for delivery "
                f"identity {_surface_delivery_id(value)}"
            )
        value, execution_proof = _accepted_surface_invocation(payload)
        effect_id = _surface_effect_id(
            value=value,
            execution_proof=execution_proof,
        )
        delivery_id = _surface_delivery_id(value)
        payload_digest = _surface_gateway_payload_digest(
            value,
            effect_id=effect_id,
        )
        try:
            result = self._query_port(
                effect_id=effect_id,
                delivery_id=delivery_id,
                platform=value["reply_channel"],
                conversation_id=value["reply_target"],
                payload_digest=payload_digest,
            )
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            raise AcceptanceEffectOutcomeUnknown(
                "authenticated Gateway reply query is unavailable after "
                f"{_opaque_failure(exc)}"
            ) from exc
        return _validated_surface_query_result(
            value=value,
            effect_id=effect_id,
            result=result,
        )


async def _deliver_accepted_surface_reply(
    payload: Mapping[str, Any],
) -> Mapping[str, Any]:
    """向 Gateway 写入不可变文本 outbox，并校验其返回的同一身份回执。"""
    from host_brain.channels.edge_transport import (  # noqa: PLC0415
        submit_gateway_reply,
    )

    value, execution_proof = _accepted_surface_invocation(payload)
    channel = value["reply_channel"]
    target = value["reply_target"]
    request_id = value["request_id"]
    effect_id = _surface_effect_id(
        value=value,
        execution_proof=execution_proof,
    )
    delivery_id = _surface_delivery_id(value)
    # The Acceptance Effect binds one exact, visible text result.  Reply-form,
    # voice, avatar, media and arbitrary ``raw_extra`` transforms require
    # their own accepted effect/Preview receipts; allowing them here would
    # turn an immutable Acceptance decision into a second planning surface.
    result = await submit_gateway_reply(
        platform=channel,
        conversation_id=target,
        content=str(value.get("narrative") or ""),
        msg_type="text",
        request_id=request_id,
        effect_id=effect_id,
        # Acceptance commits the exact durable Gateway outbox intake.  The
        # platform send is an independently recoverable eventual-delivery
        # concern and must not add its retry/timeout window to an interactive
        # Host turn.
        wait_result=False,
        accepted_effect_proof=execution_proof,
    )
    projected = (
        dict(result.as_dict())
        if hasattr(result, "as_dict")
        else dict(result or {})
    )
    if projected.get("success") is not True:
        event = projected.get("event")
        evidence = _opaque_failure(
            RuntimeError(
                repr(
                    {
                        "status": projected.get("status"),
                        "reason_code": projected.get("reason_code"),
                        "detail": projected.get("detail"),
                        "event": event,
                    }
                )
            )
        )
        if (
            isinstance(event, Mapping)
            and event.get("external_side_effects") is False
        ):
            raise _AcceptedSurfaceNoEffectFailure(
                "Gateway explicitly attested external_side_effects=false for "
                f"{_surface_delivery_id(value)} after {evidence}"
            )
        # A 500, missing/malformed body, apparently queued response, string
        # "false", or any other non-success is not proof of no effect.  Host
        # has no authenticated outbox read port, so all remain unknown.
        raise AcceptanceEffectOutcomeUnknown(
            "Gateway reply has no trusted zero-effect attestation after the "
            f"transport boundary for {_surface_delivery_id(value)}; {evidence}"
        )
    event = projected.get("event")
    outbox_id = (
        str(event.get("outbox_id") or "").strip()
        if isinstance(event, Mapping)
        else ""
    )
    expected_delivery_id = delivery_id
    expected_payload_digest = _surface_gateway_payload_digest(
        value,
        effect_id=effect_id,
    )
    if (
        not outbox_id.startswith("gateway-outbox-")
        or not isinstance(event, Mapping)
        or str(event.get("delivery_id") or "") != expected_delivery_id
        or str(event.get("platform") or "") != channel
        or str(event.get("conversation_id") or "") != target
        or str(event.get("payload_digest") or "") != expected_payload_digest
    ):
        raise AcceptanceEffectOutcomeUnknown(
            "Gateway accepted the reply without returning an exact canonical "
            "outbox/delivery/destination/payload binding for "
            f"{expected_delivery_id}"
        )
    return {
        **projected,
        "outbox_id": outbox_id,
        "delivery_id": expected_delivery_id,
        "payload_digest": expected_payload_digest,
        "receipt_ref": _canonical_id_ref("gateway-outbox", outbox_id),
    }


def _accepted_surface_request(payload: Mapping[str, Any]) -> dict[str, Any]:
    """剥离运行时证明后验证已接受回复的目的地与 authority 未漂移。"""
    value = dict(payload or {})
    value.pop("_action_preview_terminal_proof", None)
    value.pop("_acceptance_effect_execution_proof", None)
    required = {
        "request_id",
        "session_id",
        "person_id",
        "reply_channel",
        "reply_target",
        "decision",
        "narrative",
        "authority",
    }
    if set(value) != required:
        raise ValueError("accepted surface delivery payload drifted")
    authority = value.get("authority")
    request_id = value.get("request_id")
    session_id = value.get("session_id")
    person_id = value.get("person_id")
    channel = value.get("reply_channel")
    target = value.get("reply_target")
    decision = value.get("decision")
    narrative = value.get("narrative")
    if (
        not _is_exact_effect_identity(request_id, limit=180)
        or not _is_exact_effect_identity(session_id, limit=180)
        or not _is_exact_effect_identity(person_id, limit=120)
        or not _is_exact_effect_identity(channel, limit=32)
        or channel != channel.lower()
        or not _is_exact_effect_identity(target, limit=240)
        or decision != "APPROVE"
        or not isinstance(narrative, str)
        or not isinstance(authority, Mapping)
        or authority.get("request_id") != request_id
    ):
        raise ValueError("accepted surface delivery identity is incomplete")
    return {
        **value,
        "request_id": request_id,
        "session_id": session_id,
        "person_id": person_id,
        "reply_channel": channel,
        "reply_target": target,
        "decision": "APPROVE",
        "narrative": narrative,
        "authority": dict(authority),
    }


def _accepted_surface_invocation(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], AcceptanceEffectExecutionProof]:
    """在调用 Gateway 前把 payload 与活跃 dispatcher claim 逐字绑定。

    proof 不是可由 transport 自行构造的元数据：它证明当前调用仍属于同一个
    已接受 effect，且 action arguments 未被 handler 或上游重试路径替换。
    """

    value = _accepted_surface_request(payload)
    proof = dict(payload).get("_acceptance_effect_execution_proof")
    if not isinstance(proof, AcceptanceEffectExecutionProof):
        raise ValueError("accepted delivery lacks typed Acceptance Effect proof")
    if (
        proof.effect_type != "surface.reply_delivery"
        or proof.action_args_dict() != value
    ):
        raise ValueError("accepted delivery differs from its Acceptance Effect proof")
    return value, proof


def _surface_delivery_id(value: Mapping[str, Any]) -> str:
    """从可见文本和目标生成 Gateway 幂等投递身份。"""
    material = json.dumps(
        {
            "platform": str(value.get("reply_channel") or "").strip().lower(),
            "conversation_id": str(value.get("reply_target") or "").strip(),
            "content": str(value.get("narrative") or ""),
            "msg_type": "text",
            "media_path": "",
            "raw_extra": {},
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "reply:" + hashlib.sha256(
        str(value.get("request_id") or "").encode("utf-8") + b"\n" + material
    ).hexdigest()


def _surface_gateway_payload_digest(
    value: Mapping[str, Any],
    *,
    effect_id: str | None = None,
) -> str:
    """摘要化 Gateway durable payload，供写入和只读查询交叉校验。"""

    delivery_id = _surface_delivery_id(value)
    durable_payload = {
        "request_id": str(value.get("request_id") or ""),
        "delivery_id": delivery_id,
        "platform": str(value.get("reply_channel") or ""),
        "conversation_id": str(value.get("reply_target") or ""),
        "msg_type": "text",
        "content": str(value.get("narrative") or ""),
        "media_path": None,
        "reply_to_msg_id": None,
        "raw_extra": {},
    }
    if effect_id is not None:
        durable_payload["effect_id"] = str(effect_id)
    encoded = json.dumps(
        durable_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _surface_effect_id(
    *,
    value: Mapping[str, Any],
    execution_proof: AcceptanceEffectExecutionProof,
) -> str:
    """从实时 dispatcher claim 取得不可变 effect id，拒绝 authority 漂移。"""

    effect_id = str(execution_proof.effect_id or "")
    if (
        not _is_exact_effect_identity(effect_id, limit=255)
        or execution_proof.effect_type != "surface.reply_delivery"
        or execution_proof.action_args_dict() != dict(value)
    ):
        raise AcceptanceEffectOutcomeUnknown(
            "surface reply authority/effect identity drifted"
        )
    return effect_id


def _validated_surface_query_result(
    *,
    value: Mapping[str, Any],
    effect_id: str,
    result: Any,
) -> dict[str, Any]:
    """仅接受身份、版本和投递状态均精确匹配的认证 outbox 快照。

    ``COMMITTED`` 表示持久 outbox 已接管最终投递；``ACKED``/重试等待等状态
    只描述后续 eventual delivery，不能反向驱动第二次提交。未找到、取消或
    schema 漂移都不是负回执，必须继续报告结果未知。
    """

    try:
        projected = (
            dict(result.as_dict())
            if callable(getattr(result, "as_dict", None))
            else dict(result or {})
        )
    except Exception as exc:
        raise AcceptanceEffectOutcomeUnknown(
            "authenticated Gateway reply query returned a malformed transport "
            f"projection after {_opaque_failure(exc)}"
        ) from exc
    event = projected.get("event")
    if projected.get("success") is not True or not isinstance(event, Mapping):
        raise AcceptanceEffectOutcomeUnknown(
            "authenticated Gateway reply query returned no trusted snapshot"
        )
    snapshot = dict(event)
    required = {
        "status",
        "schema_version",
        "effect_id",
        "delivery_id",
        "platform",
        "conversation_id",
        "payload_digest",
        "effect_status",
        "reason_code",
        "outbox_id",
        "delivery_state",
        "status_version",
        "fencing_token",
        "delivery_receipt_digest",
    }
    if set(snapshot) != required:
        raise AcceptanceEffectOutcomeUnknown(
            "authenticated Gateway reply query shape drifted"
        )
    delivery_id = _surface_delivery_id(value)
    payload_digest = _surface_gateway_payload_digest(
        value,
        effect_id=effect_id,
    )
    if (
        snapshot.get("schema_version") != "Natsume.gateway.reply-query.v1"
        or snapshot.get("effect_id") != effect_id
        or snapshot.get("delivery_id") != delivery_id
        or snapshot.get("platform") != value["reply_channel"]
        or snapshot.get("conversation_id") != value["reply_target"]
        or snapshot.get("payload_digest") != payload_digest
    ):
        raise AcceptanceEffectOutcomeUnknown(
            "authenticated Gateway reply query identity drifted"
        )
    effect_status = str(snapshot.get("effect_status") or "")
    if effect_status != "COMMITTED":
        # NOT_FOUND, mismatches, cancellations and any future state are not a
        # negative receipt.  Never turn them into a second POST.
        raise AcceptanceEffectOutcomeUnknown(
            "Gateway reply query did not prove an exact committed outbox"
        )
    outbox_id = snapshot.get("outbox_id")
    delivery_state = snapshot.get("delivery_state")
    status_version = snapshot.get("status_version")
    fencing_token = snapshot.get("fencing_token")
    receipt_digest = snapshot.get("delivery_receipt_digest")
    if (
        snapshot.get("status") != "committed"
        or not isinstance(outbox_id, str)
        or not outbox_id.startswith("gateway-outbox-")
        or delivery_state
        not in {"READY", "CLAIMED", "RETRY_WAIT", "ACKED", "DLQ"}
        or not isinstance(status_version, int)
        or isinstance(status_version, bool)
        or status_version < 0
        or not isinstance(fencing_token, int)
        or isinstance(fencing_token, bool)
        or fencing_token < 0
        or (
            receipt_digest is not None
            and (
                not isinstance(receipt_digest, str)
                or re.fullmatch(r"sha256:[0-9a-f]{64}", receipt_digest) is None
            )
        )
        or (delivery_state == "ACKED" and receipt_digest is None)
    ):
        raise AcceptanceEffectOutcomeUnknown(
            "Gateway reply query committed snapshot is invalid"
        )
    return {
        "success": True,
        "status": "committed",
        "effect_status": "COMMITTED",
        "outbox_id": outbox_id,
        "delivery_id": delivery_id,
        "platform": value["reply_channel"],
        "conversation_id": value["reply_target"],
        "payload_digest": payload_digest,
        "delivery_state": delivery_state,
        "status_version": status_version,
        "fencing_token": fencing_token,
        "delivery_receipt_digest": receipt_digest,
        "replayed": True,
        "receipt_ref": _canonical_id_ref("gateway-outbox", outbox_id),
    }


__all__ = [
    "_AcceptedSurfaceReplyDeliveryHandler",
    "_accepted_surface_request",
    "_deliver_accepted_surface_reply",
    "_surface_delivery_id",
    "_surface_gateway_payload_digest",
]
