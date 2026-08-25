"""Exact local-projection barrier for accepted conversation Life commands."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import inspect
import json
from typing import Any

from natsume_contracts.life import (
    LifeIntentKind,
    digest_json,
    normalize_intent_rows,
)


_SUPPORTED_TARGET_STATUS = {
    "surface.post_process": "APPLIED",
    "life.experience": "PROJECTED_FROM_LIFE_JOURNAL",
    "experience": "PROJECTED_FROM_LIFE_JOURNAL",
}


class LifeProjectionBarrierError(RuntimeError):
    """The exact local projection set cannot yet be durably proven."""


async def dispatch_and_require_life_projection_barrier(
    *,
    reader: Any,
    dispatcher: Any,
    command_receipt: Mapping[str, Any],
    command_payload: Mapping[str, Any],
    correlation_id: str,
) -> dict[str, Any]:
    """Dispatch the command's exact local intents, then reopen every receipt."""

    expected = _expected_projection_intents(
        command_receipt=command_receipt,
        command_payload=command_payload,
    )
    dispatch = getattr(dispatcher, "dispatch_intent_async", None)
    if not callable(dispatch):
        raise LifeProjectionBarrierError(
            "Life projection dispatcher is unavailable"
        )
    get_receipt = getattr(reader, "get_projection_receipt", None)
    if not callable(get_receipt):
        raise LifeProjectionBarrierError(
            "Life projection receipt reader is unavailable"
        )
    for intent in expected:
        if get_receipt(intent["intent_id"]) is not None:
            continue
        result = dispatch(intent["intent_id"])
        if inspect.isawaitable(result):
            await result
    return require_life_projection_barrier(
        reader=reader,
        command_receipt=command_receipt,
        command_payload=command_payload,
        correlation_id=correlation_id,
    )


def require_life_projection_barrier(
    *,
    reader: Any,
    command_receipt: Mapping[str, Any],
    command_payload: Mapping[str, Any],
    correlation_id: str,
) -> dict[str, Any]:
    """Read and validate the full immutable projection set without mutation."""

    expected = _expected_projection_intents(
        command_receipt=command_receipt,
        command_payload=command_payload,
    )
    list_intents = getattr(reader, "list_projection_intents", None)
    get_receipt = getattr(reader, "get_projection_receipt", None)
    if not callable(list_intents) or not callable(get_receipt):
        raise LifeProjectionBarrierError(
            "Life projection barrier requires app-local read ports"
        )
    actual_rows = list_intents(str(command_receipt.get("command_id") or ""))
    actual = {
        str(row.get("intent_id") or ""): row
        for row in (_mapping(item, "Life projection intent") for item in actual_rows)
    }
    expected_by_id = {row["intent_id"]: row for row in expected}
    if actual != expected_by_id:
        raise LifeProjectionBarrierError(
            "Life projection intent set differs from accepted command"
        )

    receipts: list[dict[str, Any]] = []
    for intent in expected:
        raw = get_receipt(intent["intent_id"])
        receipt = _mapping(raw, "Life projection receipt")
        result = _mapping(receipt.get("result"), "Life projection result")
        expected_status = _SUPPORTED_TARGET_STATUS.get(intent["target"])
        completed_at_ms = receipt.get("completed_at_ms")
        if (
            expected_status is None
            or receipt.get("intent_id") != intent["intent_id"]
            or receipt.get("payload_digest") != intent["payload_digest"]
            or isinstance(completed_at_ms, bool)
            or not isinstance(completed_at_ms, int)
            or completed_at_ms < intent["created_at_ms"]
            or str(result.get("status") or "").upper() != expected_status
            or result.get("target") != intent["target"]
            or result.get("intent_id") != intent["intent_id"]
            or (
                intent["target"] == "surface.post_process"
                and result.get("request_id") != correlation_id
            )
        ):
            raise LifeProjectionBarrierError(
                "Life projection receipt differs from accepted command"
            )
        receipts.append(
            {
                "intent_id": intent["intent_id"],
                "payload_digest": intent["payload_digest"],
                "result": result,
                "completed_at_ms": completed_at_ms,
            }
        )

    material = {
        "schema_version": "Natsume.life_projection_barrier.v1",
        "command_id": str(command_receipt.get("command_id") or ""),
        "command_hash": str(command_receipt.get("command_hash") or ""),
        "event_id": str(command_receipt.get("event_id") or ""),
        "projection_receipts": receipts,
    }
    digest = hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()
    return {
        **dict(command_receipt),
        "status": "APPLIED",
        "command_receipt_ref": (
            "life-event://id/" + str(command_receipt.get("event_id") or "")
        ),
        "projection_receipts": receipts,
        "receipt_ref": f"life-projection-barrier://sha256/{digest}",
    }


def require_life_projection_admission(
    *,
    reader: Any,
    command_receipt: Mapping[str, Any],
    command_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Prove command+intent atomicity without blocking on derived projections.

    The canonical Life command, event, state head and immutable projection
    intents are committed in one SQLite transaction.  Ordinary conversation
    Acceptance therefore only needs to reopen that exact intent set.  The
    app-local one-second projection pump owns delivery/retry and durable
    receipts; a visible reply must not wait for Memory or Surface projections.
    """

    expected = _expected_projection_intents(
        command_receipt=command_receipt,
        command_payload=command_payload,
    )
    list_intents = getattr(reader, "list_projection_intents", None)
    if not callable(list_intents):
        raise LifeProjectionBarrierError(
            "Life projection admission requires an app-local intent reader"
        )
    actual_rows = list_intents(str(command_receipt.get("command_id") or ""))
    actual = {
        str(row.get("intent_id") or ""): row
        for row in (_mapping(item, "Life projection intent") for item in actual_rows)
    }
    expected_by_id = {row["intent_id"]: row for row in expected}
    if actual != expected_by_id:
        raise LifeProjectionBarrierError(
            "Life projection intent set differs from accepted command"
        )
    material = {
        "schema_version": "Natsume.life_projection_admission.v1",
        "command_id": str(command_receipt.get("command_id") or ""),
        "command_hash": str(command_receipt.get("command_hash") or ""),
        "event_id": str(command_receipt.get("event_id") or ""),
        "projection_intents": expected,
    }
    digest = hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()
    return {
        **dict(command_receipt),
        "status": "APPLIED",
        "command_receipt_ref": (
            "life-event://id/" + str(command_receipt.get("event_id") or "")
        ),
        "projection_delivery_status": "QUEUED",
        "projection_intent_count": len(expected),
        "receipt_ref": f"life-projection-admission://sha256/{digest}",
    }


def _expected_projection_intents(
    *,
    command_receipt: Mapping[str, Any],
    command_payload: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    command_id = str(command_receipt.get("command_id") or "").strip()
    person_id = str(command_receipt.get("person_id") or "").strip()
    event_id = str(command_receipt.get("event_id") or "").strip()
    created_at_ms = command_receipt.get("created_at_ms")
    if (
        not command_id
        or not person_id
        or not event_id
        or isinstance(created_at_ms, bool)
        or not isinstance(created_at_ms, int)
        or created_at_ms < 0
    ):
        raise LifeProjectionBarrierError(
            "Life command receipt cannot bind projection intents"
        )
    output: list[dict[str, Any]] = []
    groups = (
        ("organ_intents", LifeIntentKind.ORGAN),
        ("projection_intents", LifeIntentKind.PROJECTION),
    )
    for payload_key, kind in groups:
        for index, row in enumerate(
            normalize_intent_rows(command_payload.get(payload_key))
        ):
            target = str(
                row.get("target")
                or row.get("organ_id")
                or row.get("projection_id")
                or ""
            ).strip()
            payload = row.get("payload") or {}
            if (
                target not in _SUPPORTED_TARGET_STATUS
                or not isinstance(payload, Mapping)
            ):
                raise LifeProjectionBarrierError(
                    "Life command contains an unsupported local projection"
                )
            projected_payload = dict(payload)
            intent_seed = {
                "event_id": event_id,
                "kind": kind.value,
                "index": index,
                "target": target,
                "payload": projected_payload,
            }
            output.append(
                {
                    "intent_id": (
                        f"life_int_{digest_json(intent_seed)[:32]}"
                    ),
                    "event_id": event_id,
                    "command_id": command_id,
                    "person_id": person_id,
                    "intent_kind": kind.value,
                    "target": target,
                    "payload": projected_payload,
                    "payload_digest": digest_json(projected_payload),
                    "created_at_ms": created_at_ms,
                }
            )
    if not output:
        raise LifeProjectionBarrierError(
            "Accepted conversation Life command has no local projection"
        )
    return tuple(output)


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if hasattr(value, "as_dict") and callable(value.as_dict):
        value = value.as_dict()
    if not isinstance(value, Mapping):
        raise LifeProjectionBarrierError(f"{field_name} is unavailable")
    return json.loads(_canonical_json(value))


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise LifeProjectionBarrierError(
            "Life projection material is not canonical JSON"
        ) from exc


__all__ = [
    "LifeProjectionBarrierError",
    "dispatch_and_require_life_projection_barrier",
    "require_life_projection_admission",
    "require_life_projection_barrier",
]
