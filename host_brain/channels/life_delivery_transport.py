"""Authorized transport router for committed Digital Life outbox messages."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from natsume_contracts.life import LifeOutboxMessage
from host_brain.orchestration.governor_grant import DurableGovernorGrantResolver

if TYPE_CHECKING:
    from host_brain.channels.embodiment import HostEmbodimentRuntime


_DESTINATION_AUTHORITY_KINDS = {
    "edge.proactive": frozenset(
        {"scheduler_host_command", "governed_life_http"}
    ),
    "proactive.gateway": frozenset(
        {"scheduler_host_command", "governed_life_http"}
    ),
    "edge.gateway_reply": frozenset(
        {"scheduler_host_command", "governed_life_http"}
    ),
    "vts.emotion": frozenset({"surface_governor"}),
    "mateengine.state": frozenset({"surface_governor"}),
}


class LifeDeliveryTransport:
    """Dispatch one already-committed delivery with its stable idempotency key."""

    def __init__(
        self,
        *,
        ledger: Any,
        embodiment_runtime: "HostEmbodimentRuntime | None" = None,
        action_preview_service: Any | None = None,
    ) -> None:
        self._ledger = ledger
        self._grant_resolver = DurableGovernorGrantResolver(ledger)
        self._embodiment_runtime = embodiment_runtime
        self._action_preview_service = action_preview_service

    async def __call__(self, message: LifeOutboxMessage) -> Mapping[str, Any]:
        destination = str(message.destination or "").strip().lower()
        allowed_authorities = _DESTINATION_AUTHORITY_KINDS.get(destination)
        if allowed_authorities is None:
            raise ValueError(
                f"unsupported Life delivery destination: {destination}"
            )
        if message.authorization.authority_kind not in allowed_authorities:
            raise ValueError(
                "Life delivery authority kind is not allowed for destination: "
                f"{destination}"
            )
        payload = dict(message.payload)
        preview_context = payload.pop("_action_preview_context", None)
        self._grant_resolver.require_delivery_bound(
            request_id=message.authorization.request_id,
            expected_ref=message.authorization.governor_grant_ref,
            authority_kind=message.authorization.authority_kind,
            authority_principal_id=message.authorization.authority_principal_id,
            person_id=message.person_id,
            destination=destination,
            payload=payload,
        )
        preview_receipt = self._require_action_preview(
            message=message,
            destination=destination,
            payload=payload,
            preview_context=preview_context,
        )
        if destination == "edge.proactive":
            from host_brain.channels.edge_transport import submit_proactive_push  # noqa: PLC0415

            result = await submit_proactive_push(
                message=payload,
                request_id=message.delivery_key,
                action_preview_service=self._action_preview_service,
                action_preview_event_id=str(preview_receipt["event_id"]),
                action_preview_boundary=dict(
                    preview_receipt["_terminal_execution_boundary"]
                ),
            )
            return _with_preview(_validated_result(destination, result), preview_receipt)
        if destination in {"proactive.gateway", "edge.gateway_reply"}:
            from host_brain.channels.edge_transport import submit_gateway_reply  # noqa: PLC0415

            result = await submit_gateway_reply(
                platform=str(payload.get("platform") or ""),
                conversation_id=str(payload.get("conversation_id") or ""),
                content=str(payload.get("content") or ""),
                msg_type=str(payload.get("msg_type") or "text"),
                raw_extra=dict(payload.get("raw_extra") or {}),
                request_id=message.delivery_key,
                delivery_id=message.delivery_key,
                wait_result=True,
                action_preview_service=self._action_preview_service,
                action_preview_event_id=str(preview_receipt["event_id"]),
                action_preview_boundary=dict(
                    preview_receipt["_terminal_execution_boundary"]
                ),
            )
            return _with_preview(_validated_result(destination, result), preview_receipt)
        if destination == "vts.emotion":
            from host_brain.channels.embodiment import (  # noqa: PLC0415
                build_host_embodiment_command,
                queue_embodiment_command,
            )
            from natsume_contracts.edge.embodiment_v1 import (  # noqa: PLC0415
                EMBODIMENT_KIND_AVATAR,
            )

            runtime = self._require_embodiment_runtime(destination)

            emotion_context = dict(payload.get("emotion_context") or {})
            target = dict(payload.get("target") or {})
            node_grant_ref = self._preview_node_grant_ref(preview_context)
            command = build_host_embodiment_command(
                kind=EMBODIMENT_KIND_AVATAR,
                action="set_expression",
                payload={"emotion_tag": str(
                    emotion_context.get("emotion_tag")
                    or target.get("emotion_tag")
                    or "NEUTRAL"
                )},
                request_id=message.delivery_key,
                authority_ref=f"authority://task-node/{message.delivery_key}",
                grant_ref=node_grant_ref,
                deadline_ms=int(
                    dict(preview_receipt.get("receipt") or {}).get("expires_at_ms")
                    or 0
                ),
                idempotency_key=message.delivery_key,
            )
            terminal_preview = self._require_embodiment_action_preview(
                preview_context=preview_context,
                action_kind="embodiment.vts.set_expression",
                action_scope=message.delivery_key,
                action_args={
                    "delivery_key": message.delivery_key,
                    "command": command.as_dict(),
                },
                guardian_capability_id="embodiment_vts_expression",
            )
            result = queue_embodiment_command(
                command,
                runtime=runtime,
                action_preview_service=self._action_preview_service,
                action_preview_event_id=str(terminal_preview["event_id"]),
                action_preview_boundary=dict(
                    terminal_preview["_terminal_execution_boundary"]
                ),
            )
            return _with_preview(_validated_result(destination, result), preview_receipt)
        if destination == "mateengine.state":
            from host_brain.channels.embodiment import build_host_embodiment_command, queue_embodiment_command  # noqa: PLC0415
            from natsume_contracts.edge.embodiment_v1 import EMBODIMENT_KIND_MATEENGINE  # noqa: PLC0415

            runtime = self._require_embodiment_runtime(destination)

            target = dict(payload.get("target") or {})
            state = str(
                    payload.get("state")
                    or target.get("mateengine_state")
                    or "idle"
                )
            emotion_tag = str(
                    payload.get("emotion_tag")
                    or target.get("emotion_tag")
                    or "NEUTRAL"
                )
            command_payload = (
                {"command": {"type": "reset"}}
                if state.strip().lower() == "idle"
                else {"command": {"type": "expression", "preset": emotion_tag, "emotion_tag": emotion_tag}}
            )
            node_grant_ref = self._preview_node_grant_ref(preview_context)
            command = build_host_embodiment_command(
                kind=EMBODIMENT_KIND_MATEENGINE,
                action="command",
                payload=command_payload,
                request_id=message.delivery_key,
                authority_ref=f"authority://task-node/{message.delivery_key}",
                grant_ref=node_grant_ref,
                deadline_ms=int(
                    dict(preview_receipt.get("receipt") or {}).get("expires_at_ms")
                    or 0
                ),
                idempotency_key=message.delivery_key,
            )
            terminal_preview = self._require_embodiment_action_preview(
                preview_context=preview_context,
                action_kind="embodiment.mateengine.command",
                action_scope=message.delivery_key,
                action_args={
                    "delivery_key": message.delivery_key,
                    "command": command.as_dict(),
                },
                guardian_capability_id="embodiment_mateengine_command",
            )
            result = queue_embodiment_command(
                command,
                runtime=runtime,
                action_preview_service=self._action_preview_service,
                action_preview_event_id=str(terminal_preview["event_id"]),
                action_preview_boundary=dict(
                    terminal_preview["_terminal_execution_boundary"]
                ),
            )
            return _with_preview(_validated_result(destination, result), preview_receipt)

    def _require_action_preview(
        self,
        *,
        message: LifeOutboxMessage,
        destination: str,
        payload: Mapping[str, Any],
        preview_context: Any,
    ) -> dict[str, Any] | None:
        service = self._action_preview_service
        if service is None:
            raise ValueError(
                "ACTION_PREVIEW_INVALID: Life actuator has no app-local authority"
            )
        if not isinstance(preview_context, Mapping):
            raise ValueError(
                "ACTION_PREVIEW_INVALID: high-risk Life actuator lacks exact Task authority"
            )
        from host_brain.runtime.action_preview_authority import (  # noqa: PLC0415
            require_scheduler_derived_action,
        )

        return require_scheduler_derived_action(
            service=service,
            preview_context=preview_context,
            action_kind=f"life-delivery.{destination}",
            action_scope=message.delivery_key,
            risk_level=4 if destination in {"vts.emotion", "mateengine.state"} else 3,
            action_args={
                "delivery_key": message.delivery_key,
                "destination": destination,
                "payload": dict(payload),
            },
            guardian_capability_id=f"life_delivery_{destination.replace('.', '_')}",
        )

    def _require_embodiment_runtime(
        self,
        destination: str,
    ) -> "HostEmbodimentRuntime":
        runtime = self._embodiment_runtime
        if runtime is None:
            raise RuntimeError(
                f"Life delivery embodiment runtime unavailable for {destination}"
            )
        return runtime

    def _require_embodiment_action_preview(
        self,
        *,
        preview_context: Mapping[str, Any],
        action_kind: str,
        action_scope: str,
        action_args: Mapping[str, Any],
        guardian_capability_id: str,
    ) -> dict[str, Any]:
        from host_brain.runtime.action_preview_authority import (  # noqa: PLC0415
            require_scheduler_derived_action,
        )

        return require_scheduler_derived_action(
            service=self._action_preview_service,
            preview_context=preview_context,
            action_kind=action_kind,
            action_scope=action_scope,
            risk_level=4,
            action_args=action_args,
            guardian_capability_id=guardian_capability_id,
        )

    @staticmethod
    def _preview_node_grant_ref(preview_context: Mapping[str, Any]) -> str:
        boundary = preview_context.get("origin_boundary")
        if not isinstance(boundary, Mapping):
            raise ValueError("ACTION_PREVIEW_INVALID: Scheduler origin boundary is missing")
        grant_ref = str(boundary.get("node_grant_ref") or "").strip()
        if not grant_ref:
            raise ValueError("ACTION_PREVIEW_INVALID: Scheduler node grant is missing")
        return grant_ref


def _result_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    converter = getattr(value, "as_dict", None)
    if callable(converter):
        converted = converter()
        if isinstance(converted, Mapping):
            return dict(converted)
    attributes = getattr(value, "__dict__", None)
    if isinstance(attributes, Mapping):
        return dict(attributes)
    return {"value": str(value)}


def _validated_result(destination: str, value: Any) -> dict[str, Any]:
    result = _result_mapping(value)
    status = str(result.get("status") or "").strip().upper()
    if result.get("success") is False or result.get("ok") is False:
        raise RuntimeError(
            f"Life delivery failed for {destination}: "
            f"{result.get('reason_code') or result.get('error') or status}"
        )
    if status in {"FAILED", "ERROR", "REJECTED", "DEGRADED"}:
        raise RuntimeError(
            f"Life delivery failed for {destination}: "
            f"{result.get('reason_code') or result.get('error') or status}"
        )
    return result


def _with_preview(
    result: Mapping[str, Any],
    preview_receipt: Mapping[str, Any] | None,
) -> dict[str, Any]:
    output = dict(result)
    if isinstance(preview_receipt, Mapping):
        output["action_preview_receipt_ref"] = str(
            preview_receipt.get("receipt_ref") or ""
        )
        output["action_preview_event_id"] = str(
            preview_receipt.get("event_id") or ""
        )
    return output


__all__ = ["LifeDeliveryTransport"]
