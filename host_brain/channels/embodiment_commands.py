"""Public Host embodiment command construction and Preview-gated submission."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Mapping

from host_brain.channels.embodiment import (
    _AUTHORITY_REF,
    EmbodimentOutboxAck,
    EmbodimentOutboxPort,
    HostEmbodimentRuntime,
    ReceiptObserver,
)
from natsume_contracts.edge.embodiment_v1 import (
    EMBODIMENT_KIND_AVATAR,
    EMBODIMENT_KIND_DESKTOP,
    EMBODIMENT_KIND_MATEENGINE,
    EMBODIMENT_KIND_VOICE,
    EmbodimentCommand,
    EmbodimentCommandPort,
    EmbodimentReceipt,
    build_embodiment_command,
)
from natsume_contracts.edge.v1 import new_request_id


def build_host_embodiment_command(
    *,
    kind: str,
    action: str,
    payload: Mapping[str, Any] | None = None,
    request_id: str | None = None,
    authority_ref: str = _AUTHORITY_REF,
    grant_ref: str | None = None,
    deadline_ms: int | None = None,
    idempotency_key: str | None = None,
) -> EmbodimentCommand:
    req_id = str(request_id or new_request_id()).strip()
    body = dict(payload or {})
    key = str(idempotency_key or "").strip() or _default_idempotency_key(
        request_id=req_id,
        kind=kind,
        action=action,
        payload=body,
    )
    # Preserve the Host module's injectable clock contract while keeping
    # command authoring in this focused boundary.
    from host_brain.channels import embodiment as embodiment_runtime  # noqa: PLC0415

    issued = embodiment_runtime.now_ms()
    return build_embodiment_command(
        kind=kind,
        action=action,
        payload=body,
        authority_ref=authority_ref,
        grant_ref=str(grant_ref or f"grant://host/device/{req_id}").strip(),
        deadline_ms=int(deadline_ms if deadline_ms is not None else issued + 30_000),
        idempotency_key=key,
        request_id=req_id,
        issued_at_ms=issued,
    )


async def submit_embodiment_command(
    command: EmbodimentCommand,
    *,
    runtime: HostEmbodimentRuntime | None = None,
    command_port: EmbodimentCommandPort | None = None,
    action_preview_service: Any | None = None,
    action_preview_event_id: str | None = None,
    action_preview_boundary: Mapping[str, Any] | None = None,
) -> EmbodimentReceipt:
    """Submit through an explicitly supplied app runtime or command port.

    ``command_port=`` supports narrow test/adaptor use.  Production paths must
    pass their app-owned ``runtime=`` so its receipt audit and local lease are
    retained.  There is intentionally no module default.
    """

    if runtime is not None:
        if command_port is not None:
            raise TypeError("runtime and command_port are mutually exclusive")
        return await runtime.submit(
            command,
            action_preview_service=action_preview_service,
            action_preview_event_id=action_preview_event_id,
            action_preview_boundary=action_preview_boundary,
        )
    if command_port is None:
        raise RuntimeError("EMBODIMENT_RUNTIME_REQUIRED")
    _require_terminal_embodiment_preview(
        command=command,
        action_preview_service=action_preview_service,
        action_preview_event_id=action_preview_event_id,
        action_preview_boundary=action_preview_boundary,
    )
    return await command_port.submit(command)


def _require_terminal_embodiment_preview(
    *,
    command: EmbodimentCommand,
    action_preview_service: Any,
    action_preview_event_id: str | None,
    action_preview_boundary: Mapping[str, Any] | None,
) -> None:
    """Reopen exact Preview authority before public enqueue/direct submit."""

    from host_brain.governor.action_preview import ActionPreviewService  # noqa: PLC0415

    if not isinstance(action_preview_service, ActionPreviewService):
        raise RuntimeError("ACTION_PREVIEW_TASK_AUTHORITY_REQUIRED")
    if not str(action_preview_event_id or "").strip() or not isinstance(
        action_preview_boundary,
        Mapping,
    ):
        raise RuntimeError("ACTION_PREVIEW_TASK_AUTHORITY_REQUIRED")
    boundary = dict(action_preview_boundary)
    args = boundary.get("action_args")
    expected_kind = {
        (EMBODIMENT_KIND_AVATAR, "set_expression"): "embodiment.vts.set_expression",
        (EMBODIMENT_KIND_MATEENGINE, "command"): "embodiment.mateengine.command",
    }.get((str(command.kind), str(command.action)))
    direct_preview = (
        expected_kind is not None
        and str(boundary.get("action_kind") or "") == expected_kind
        and str(boundary.get("action_scope") or "")
        == str(command.idempotency_key)
        and isinstance(args, Mapping)
        and dict(args)
        == {
            "delivery_key": str(command.idempotency_key),
            "command": command.as_dict(),
        }
    )
    authority_context = boundary.get("authority_context")
    acceptance_effect_preview = (
        str(boundary.get("action_kind") or "")
        == "acceptance-effect.edge.embodiment_enqueue"
        and isinstance(args, Mapping)
        and dict(args).get("embodiment_command") == command.as_dict()
        and isinstance(authority_context, Mapping)
        and str(authority_context.get("effect_id") or "").strip()
        == str(boundary.get("action_scope") or "").strip()
        and str(authority_context.get("schema_version") or "")
        == "Natsume.action_preview.acceptance_effect_authority_request.v1"
    )
    companion_expression_preview = False
    if (
        str(boundary.get("action_kind") or "")
        == "acceptance-effect.host.companion_expression_enqueue"
        and isinstance(args, Mapping)
        and isinstance(authority_context, Mapping)
        and str(authority_context.get("effect_id") or "").strip()
        == str(boundary.get("action_scope") or "").strip()
        and str(authority_context.get("schema_version") or "")
        == "Natsume.action_preview.acceptance_effect_authority_request.v1"
        and str(command.grant_ref or "")
        == str(boundary.get("node_grant_ref") or "").strip()
    ):
        # Keep the public enqueue boundary independent from the handler while
        # still proving that a companion-plan Preview owns this exact command.
        # The helper is pure; it cannot open an Edge transport or mutate Life.
        from host_brain.digital_life.companion_expression_acceptance import (  # noqa: PLC0415
            verify_companion_expression_command,
        )

        companion_expression_preview = verify_companion_expression_command(
            payload=dict(args),
            command=command,
        )
    if (
        not direct_preview
        and not acceptance_effect_preview
        and not companion_expression_preview
    ):
        raise RuntimeError("ACTION_PREVIEW_DRIFT")
    action_preview_service.require_execution(
        event_id=str(action_preview_event_id),
        **boundary,
    )


def _require_terminal_embodiment_batch_preview(
    *,
    commands: tuple[EmbodimentCommand, ...],
    action_preview_service: Any,
    action_preview_event_id: str | None,
    action_preview_boundary: Mapping[str, Any] | None,
) -> None:
    """Reopen one exact Preview for every command in one expression batch."""

    from host_brain.governor.action_preview import ActionPreviewService  # noqa: PLC0415
    from host_brain.digital_life.companion_expression_acceptance import (  # noqa: PLC0415
        verify_companion_expression_command,
    )

    if not isinstance(action_preview_service, ActionPreviewService):
        raise RuntimeError("ACTION_PREVIEW_TASK_AUTHORITY_REQUIRED")
    if not str(action_preview_event_id or "").strip() or not isinstance(
        action_preview_boundary, Mapping
    ):
        raise RuntimeError("ACTION_PREVIEW_TASK_AUTHORITY_REQUIRED")
    boundary = dict(action_preview_boundary)
    args = boundary.get("action_args")
    authority_context = boundary.get("authority_context")
    if (
        str(boundary.get("action_kind") or "")
        != "acceptance-effect.host.companion_expression_enqueue"
        or not isinstance(args, Mapping)
        or not isinstance(authority_context, Mapping)
        or str(authority_context.get("effect_id") or "").strip()
        != str(boundary.get("action_scope") or "").strip()
        or str(authority_context.get("schema_version") or "")
        != "Natsume.action_preview.acceptance_effect_authority_request.v1"
        or any(
            str(command.grant_ref or "")
            != str(boundary.get("node_grant_ref") or "").strip()
            or not verify_companion_expression_command(
                payload=dict(args),
                command=command,
            )
            for command in commands
        )
    ):
        raise RuntimeError("ACTION_PREVIEW_DRIFT")
    action_preview_service.require_execution(
        event_id=str(action_preview_event_id),
        **boundary,
    )


def queue_embodiment_command(
    command: EmbodimentCommand,
    *,
    runtime: HostEmbodimentRuntime | None = None,
    outbox: EmbodimentOutboxPort | None = None,
    action_preview_service: Any | None = None,
    action_preview_event_id: str | None = None,
    action_preview_boundary: Mapping[str, Any] | None = None,
) -> EmbodimentOutboxAck:
    """Durably enqueue through an explicitly owned app runtime or outbox."""

    _require_terminal_embodiment_preview(
        command=command,
        action_preview_service=action_preview_service,
        action_preview_event_id=action_preview_event_id,
        action_preview_boundary=action_preview_boundary,
    )

    if runtime is not None:
        if outbox is not None:
            raise TypeError("runtime and outbox are mutually exclusive")
        return runtime.enqueue(
            command,
            action_preview_service=action_preview_service,
            action_preview_event_id=action_preview_event_id,
            action_preview_boundary=action_preview_boundary,
        )
    if outbox is None:
        raise RuntimeError("EMBODIMENT_RUNTIME_REQUIRED")
    return outbox.enqueue(command)


def register_embodiment_receipt_observer(
    observer: ReceiptObserver,
    *,
    runtime: HostEmbodimentRuntime,
) -> Callable[[], None]:
    """Compatibility adapter that remains explicitly runtime-scoped."""

    return runtime.register_receipt_observer(observer)


async def submit_voice_command(
    *,
    action: str = "speak",
    payload: Mapping[str, Any],
    runtime: HostEmbodimentRuntime | None = None,
    command_port: EmbodimentCommandPort | None = None,
    action_preview_service: Any | None = None,
    action_preview_event_id: str | None = None,
    action_preview_boundary: Mapping[str, Any] | None = None,
    **meta: Any,
) -> EmbodimentReceipt:
    return await submit_embodiment_command(
        build_host_embodiment_command(kind=EMBODIMENT_KIND_VOICE, action=action, payload=payload, **meta),
        runtime=runtime,
        command_port=command_port,
        action_preview_service=action_preview_service,
        action_preview_event_id=action_preview_event_id,
        action_preview_boundary=action_preview_boundary,
    )


async def submit_avatar_command(
    *,
    action: str,
    payload: Mapping[str, Any],
    runtime: HostEmbodimentRuntime | None = None,
    command_port: EmbodimentCommandPort | None = None,
    action_preview_service: Any | None = None,
    action_preview_event_id: str | None = None,
    action_preview_boundary: Mapping[str, Any] | None = None,
    **meta: Any,
) -> EmbodimentReceipt:
    return await submit_embodiment_command(
        build_host_embodiment_command(kind=EMBODIMENT_KIND_AVATAR, action=action, payload=payload, **meta),
        runtime=runtime,
        command_port=command_port,
        action_preview_service=action_preview_service,
        action_preview_event_id=action_preview_event_id,
        action_preview_boundary=action_preview_boundary,
    )


async def submit_avatar_behavior(
    *,
    behavior_id: str,
    emotion_tag: str | None = None,
    body_action_id: str | None = None,
    emotion_intensity: float | None = None,
    duration_s: float | None = None,
    talk_state: str | None = None,
    source: str | None = None,
    runtime: HostEmbodimentRuntime | None = None,
    command_port: EmbodimentCommandPort | None = None,
    action_preview_service: Any | None = None,
    action_preview_event_id: str | None = None,
    action_preview_boundary: Mapping[str, Any] | None = None,
    **meta: Any,
) -> EmbodimentReceipt:
    """Submit one high-level avatar behavior to the Edge-owned actuator."""

    payload: dict[str, Any] = {"behavior_id": str(behavior_id or "").strip().lower()}
    for key, value, normalizer in (
        ("emotion_tag", emotion_tag, lambda item: str(item).strip().upper()),
        ("body_action_id", body_action_id, lambda item: str(item).strip().lower()),
        ("talk_state", talk_state, lambda item: str(item).strip().lower()),
        ("source", source, lambda item: str(item).strip()),
    ):
        if value is not None and str(value).strip():
            payload[key] = normalizer(value)
    if emotion_intensity is not None:
        payload["emotion_intensity"] = float(emotion_intensity)
    if duration_s is not None:
        payload["duration_s"] = float(duration_s)
    return await submit_avatar_command(
        action="behavior",
        payload=payload,
        runtime=runtime,
        command_port=command_port,
        action_preview_service=action_preview_service,
        action_preview_event_id=action_preview_event_id,
        action_preview_boundary=action_preview_boundary,
        **meta,
    )


def queue_mateengine_command(
    *,
    action: str,
    payload: Mapping[str, Any],
    runtime: HostEmbodimentRuntime | None = None,
    outbox: EmbodimentOutboxPort | None = None,
    action_preview_service: Any | None = None,
    action_preview_event_id: str | None = None,
    action_preview_boundary: Mapping[str, Any] | None = None,
    **meta: Any,
) -> EmbodimentOutboxAck:
    return queue_embodiment_command(
        build_host_embodiment_command(
            kind=EMBODIMENT_KIND_MATEENGINE,
            action=action,
            payload=payload,
            **meta,
        ),
        runtime=runtime,
        outbox=outbox,
        action_preview_service=action_preview_service,
        action_preview_event_id=action_preview_event_id,
        action_preview_boundary=action_preview_boundary,
    )


def queue_desktop_command(
    *,
    action: str,
    payload: Mapping[str, Any],
    runtime: HostEmbodimentRuntime | None = None,
    outbox: EmbodimentOutboxPort | None = None,
    action_preview_service: Any | None = None,
    action_preview_event_id: str | None = None,
    action_preview_boundary: Mapping[str, Any] | None = None,
    **meta: Any,
) -> EmbodimentOutboxAck:
    return queue_embodiment_command(
        build_host_embodiment_command(
            kind=EMBODIMENT_KIND_DESKTOP,
            action=action,
            payload=payload,
            **meta,
        ),
        runtime=runtime,
        outbox=outbox,
        action_preview_service=action_preview_service,
        action_preview_event_id=action_preview_event_id,
        action_preview_boundary=action_preview_boundary,
    )


async def submit_desktop_command(
    *,
    action: str,
    payload: Mapping[str, Any],
    runtime: HostEmbodimentRuntime | None = None,
    command_port: EmbodimentCommandPort | None = None,
    action_preview_service: Any | None = None,
    action_preview_event_id: str | None = None,
    action_preview_boundary: Mapping[str, Any] | None = None,
    **meta: Any,
) -> EmbodimentReceipt:
    return await submit_embodiment_command(
        build_host_embodiment_command(kind=EMBODIMENT_KIND_DESKTOP, action=action, payload=payload, **meta),
        runtime=runtime,
        command_port=command_port,
        action_preview_service=action_preview_service,
        action_preview_event_id=action_preview_event_id,
        action_preview_boundary=action_preview_boundary,
    )


def _default_idempotency_key(
    *, request_id: str, kind: str, action: str, payload: Mapping[str, Any]
) -> str:
    encoded = json.dumps(
        dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:24]
    return f"{request_id}:{str(kind).lower()}:{str(action).lower()}:{digest}"
