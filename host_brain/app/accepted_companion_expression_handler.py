"""Accepted Host-outbox handler for one frozen companion expression intent.

The handler is intentionally narrower than an Edge client: it first proves
that the corresponding ``life.foreground_completed`` command is durable, then
adds one exact command to the app-local embodiment outbox.  It neither starts
nor submits to a remote Edge process.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import json
from typing import Any

from host_brain.app.acceptance_effect_handler_common import _canonical_id_ref
from host_brain.digital_life.companion_expression_acceptance import (
    COMPANION_EXPRESSION_ENQUEUE_EFFECT,
    CompanionExpressionEffectError,
    parse_companion_expression_effect_batch_payload,
)
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectOutcomeUnknown,
)


class AcceptedCompanionExpressionEnqueueHandler:
    """Persist one approved Voice or Avatar protocol command in Host outbox."""

    def __init__(
        self,
        *,
        runtime_provider: Callable[[], Any],
        life_receipt_reader_provider: Callable[[], Any],
    ) -> None:
        if not callable(runtime_provider):
            raise TypeError("runtime_provider must be callable")
        if not callable(life_receipt_reader_provider):
            raise TypeError("life_receipt_reader_provider must be callable")
        self._runtime_provider = runtime_provider
        self._life_receipt_reader_provider = life_receipt_reader_provider

    def __call__(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        value, runtime, preview = _accepted_companion_expression_request(
            payload,
            runtime_provider=self._runtime_provider,
            life_receipt_reader_provider=self._life_receipt_reader_provider,
        )
        commands = tuple(item["command"] for item in value)
        try:
            if preview.get("mode") == "ACCEPTANCE_LINEAGE":
                acknowledgements = runtime.enqueue_accepted_companion_batch(
                    commands,
                    acceptance_effect_payload=preview["payload"],
                )
            else:
                acknowledgements = runtime.enqueue_batch(
                    commands,
                    action_preview_service=preview["service"],
                    action_preview_event_id=preview["event_id"],
                    action_preview_boundary=preview["boundary"],
                )
        except Exception as exc:
            try:
                return self.reconcile(payload)
            except AcceptanceEffectOutcomeUnknown:
                # No remote effect occurred.  If the exact durable row is
                # absent, the dispatcher may retry the immutable enqueue.
                raise exc
        reopened = self.reconcile(payload)
        expected_acks = tuple(
            (
                str(getattr(ack, "outbox_id", "") or ""),
                str(getattr(ack, "command_id", "") or ""),
                str(getattr(ack, "idempotency_key", "") or ""),
            )
            for ack in acknowledgements
        )
        reopened_acks = tuple(
            (
                str(item.get("outbox_id") or ""),
                str(item.get("command_id") or ""),
                str(item.get("idempotency_key") or ""),
            )
            for item in list(reopened.get("commands") or [])
            if isinstance(item, Mapping)
        )
        if expected_acks != reopened_acks:
            raise AcceptanceEffectOutcomeUnknown(
                "companion expression batch acknowledgement differs from "
                "its exact Host outbox rows"
            )
        return reopened

    def reconcile(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        value, runtime, _preview = _accepted_companion_expression_request(
            payload,
            runtime_provider=self._runtime_provider,
            life_receipt_reader_provider=self._life_receipt_reader_provider,
        )
        reopen = getattr(runtime, "reopen_outbox_command", None)
        if not callable(reopen):
            raise AcceptanceEffectOutcomeUnknown(
                "companion expression runtime exposes no exact Host outbox reader"
            )
        commands: list[dict[str, Any]] = []
        for item in value:
            command = item["command"]
            row = reopen(command)
            if not isinstance(row, Mapping):
                raise AcceptanceEffectOutcomeUnknown(
                    "accepted companion expression has no exact durable Host outbox row"
                )
            durable = dict(row)
            outbox_id = str(durable.get("outbox_id") or "").strip()
            status = str(durable.get("status") or "").strip().upper()
            if (
                not outbox_id
                or str(durable.get("runtime_scope_id") or "").strip()
                != str(getattr(runtime, "runtime_scope_id", "") or "").strip()
                or str(durable.get("command_id") or "").strip()
                != command.command_id
                or str(durable.get("idempotency_key") or "").strip()
                != command.idempotency_key
                or status
                not in {
                    "PENDING",
                    "DISPATCHING",
                    "IN_DOUBT",
                    "APPLIED",
                    "REJECTED",
                }
            ):
                raise AcceptanceEffectOutcomeUnknown(
                    "accepted companion expression durable Host outbox row drifted"
                )
            receipt = durable.get("receipt")
            physical_receipt_ref = (
                str(receipt.get("receipt_ref") or "").strip()
                if isinstance(receipt, Mapping)
                else ""
            )
            intent = item["intent"]
            commands.append(
                {
                    "outbox_id": outbox_id,
                    "command_id": command.command_id,
                    "idempotency_key": command.idempotency_key,
                    "delivery_id": intent.delivery_id,
                    "synchronization_id": intent.synchronization_id,
                    "self_affect_revision": intent.self_affect_revision,
                    "host_outbox_status": status,
                    "physical_receipt_ref": physical_receipt_ref or None,
                }
            )
        material = {
            "schema_version": "Natsume.companion_expression_batch_receipt.v1",
            "outbox_ids": [item["outbox_id"] for item in commands],
            "command_ids": [item["command_id"] for item in commands],
        }
        receipt_digest = hashlib.sha256(
            json.dumps(
                material,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        statuses = {str(item["host_outbox_status"]) for item in commands}
        return {
            "receipt_ref": f"embodiment-outbox-batch://sha256/{receipt_digest}",
            "batch_size": len(commands),
            "commands": commands,
            # This is the Host local outbox state only.  It is never an Edge
            # render claim, even if a later dispatcher has returned a receipt.
            "host_outbox_status": next(iter(statuses)) if len(statuses) == 1 else "MIXED",
        }


def companion_expression_handler_receipt_ref(outbox_id: str) -> str:
    return _canonical_id_ref("embodiment-outbox", outbox_id)


def _accepted_companion_expression_request(
    payload: Mapping[str, Any],
    *,
    runtime_provider: Callable[[], Any],
    life_receipt_reader_provider: Callable[[], Any],
) -> tuple[tuple[dict[str, Any], ...], Any, dict[str, Any]]:
    from host_brain.governor.action_preview import ActionPreviewService  # noqa: PLC0415

    value = dict(payload or {})
    raw_preview = value.pop("_action_preview_terminal_proof", None)
    preview = dict(raw_preview) if isinstance(raw_preview, Mapping) else {}
    if raw_preview is not None and (
        set(preview) != {"service", "event_id", "boundary"}
        or not isinstance(preview.get("service"), ActionPreviewService)
        or not str(preview.get("event_id") or "").strip()
        or not isinstance(preview.get("boundary"), Mapping)
    ):
        raise ValueError("accepted companion expression Preview proof is invalid")
    try:
        parsed = parse_companion_expression_effect_batch_payload(value)
    except CompanionExpressionEffectError as exc:
        raise ValueError("accepted companion expression payload drifted") from exc
    runtime = runtime_provider()
    if (
        runtime is None
        or not callable(getattr(runtime, "reopen_outbox_command", None))
        or not str(getattr(runtime, "runtime_scope_id", "") or "").strip()
    ):
        raise ValueError("accepted companion expression runtime is unavailable")
    _require_life_foreground_receipt(
        parsed[0]["life_foreground"],
        life_receipt_reader_provider=life_receipt_reader_provider,
    )
    if raw_preview is None:
        if not callable(getattr(runtime, "enqueue_accepted_companion_batch", None)):
            raise ValueError("accepted companion expression runtime is unavailable")
        grant_refs = {
            str(item["command"].grant_ref or "").strip() for item in parsed
        }
        if (
            len(grant_refs) != 1
            or "" in grant_refs
            or any(
                not str(item["command"].authority_ref or "").startswith(
                    "authority://task-acceptance/sha256/"
                )
                for item in parsed
            )
        ):
            raise ValueError("accepted companion expression lineage drifted")
        return parsed, runtime, {
            "mode": "ACCEPTANCE_LINEAGE",
            "payload": value,
        }
    if not callable(getattr(runtime, "enqueue_batch", None)):
        raise ValueError("accepted companion expression runtime is unavailable")
    boundary = dict(preview["boundary"])
    authority_context = boundary.get("authority_context")
    action_scope = str(boundary.get("action_scope") or "").strip()
    if (
        str(boundary.get("action_kind") or "")
        != f"acceptance-effect.{COMPANION_EXPRESSION_ENQUEUE_EFFECT}"
        or int(boundary.get("risk_level") or 0) != 4
        or boundary.get("action_args") != value
        or not isinstance(authority_context, Mapping)
        or set(authority_context)
        != {"schema_version", "effect_id", "lease_owner_id", "fencing_token"}
        or authority_context.get("schema_version")
        != "Natsume.action_preview.acceptance_effect_authority_request.v1"
        or str(authority_context.get("effect_id") or "").strip() != action_scope
        or not action_scope
        or not str(authority_context.get("lease_owner_id") or "").strip()
        or isinstance(authority_context.get("fencing_token"), bool)
        or int(authority_context.get("fencing_token") or 0) < 1
        or any(
            str(item["command"].grant_ref or "")
            != str(boundary.get("node_grant_ref") or "").strip()
            for item in parsed
        )
    ):
        raise ValueError("accepted companion expression Preview boundary drifted")
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
        != f"acceptance-effect.{COMPANION_EXPRESSION_ENQUEUE_EFFECT}"
        or str(preview_payload.get("action_scope") or "").strip() != action_scope
        or int(preview_payload.get("risk_level") or 0) != 4
        or str(preview_payload.get("action_args_digest") or "").lower()
        != expected_args_digest
    ):
        raise ValueError("accepted companion expression Preview receipt drifted")
    return parsed, runtime, {
        "service": preview["service"],
        "event_id": str(preview["event_id"]),
        "boundary": boundary,
    }


def _require_life_foreground_receipt(
    binding: Mapping[str, Any],
    *,
    life_receipt_reader_provider: Callable[[], Any],
) -> None:
    try:
        reader = life_receipt_reader_provider()
        get_receipt = getattr(reader, "get_receipt", None)
        if not callable(get_receipt):
            raise RuntimeError("LIFE_COMMAND_RECEIPT_READER_UNAVAILABLE")
        receipt = get_receipt(str(binding["command_id"]))
        projected = (
            dict(receipt)
            if isinstance(receipt, Mapping)
            else dict(receipt.as_dict())
            if callable(getattr(receipt, "as_dict", None))
            else None
        )
        if (
            not isinstance(projected, Mapping)
            or str(projected.get("status") or "").strip().upper() != "APPLIED"
            or str(projected.get("command_id") or "").strip()
            != str(binding["command_id"])
            or str(projected.get("person_id") or "").strip()
            != str(binding["person_id"])
            or str(projected.get("command_hash") or "").strip()
            != str(binding["command_hash"])
            or not str(projected.get("event_id") or "").startswith("life_evt_")
        ):
            raise RuntimeError("LIFE_FOREGROUND_RECEIPT_NOT_APPLIED")
    except Exception as exc:
        # The outbox has not been touched yet, so a normal dispatcher retry is
        # safe.  Do not recast this as an unknown external effect.
        raise RuntimeError("LIFE_FOREGROUND_RECEIPT_REQUIRED") from exc


__all__ = [
    "AcceptedCompanionExpressionEnqueueHandler",
    "companion_expression_handler_receipt_ref",
]
