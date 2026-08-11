"""Durable Life delivery outbox and terminal receipt repository mixin."""

from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any

from natsume_contracts.life import (
    LIFE_COMMAND_DURABLE_DATA_INVALID,
    LIFE_COMMAND_INVALID,
    LIFE_DELIVERY_CLAIM_FENCED,
    LifeCommandRepositoryError,
    LifeDeliveryAuthorization,
    LifeDeliveryReceipt,
    LifeDeliveryStatus,
    LifeOutboxMessage,
    canonical_json,
    digest_json,
)


class LifeCommandDeliveryMixin:
    """Own delivery claim, completion, retry and query operations."""
    def claim_deliveries(
        self,
        *,
        owner_token: str,
        now_ms: int,
        lease_ms: int,
        limit: int,
    ) -> list[LifeOutboxMessage]:
        owner = _required_text(owner_token, "owner_token")
        now_value = max(0, int(now_ms))
        expires = now_value + max(1, int(lease_ms))
        with self._operation(write=True) as conn:
            rows = conn.execute(
                """
                SELECT * FROM life_delivery_outbox
                WHERE NOT EXISTS (
                    SELECT 1 FROM life_delivery_receipt
                    WHERE life_delivery_receipt.outbox_id = life_delivery_outbox.outbox_id
                ) AND (
                    (
                        status IN ('PENDING', 'FAILED') AND available_at_ms <= ?
                    ) OR (
                        status = 'CLAIMED' AND COALESCE(claim_expires_at_ms, 0) <= ?
                    )
                )
                ORDER BY available_at_ms ASC, created_at_ms ASC, outbox_id ASC
                LIMIT ?
                """,
                (now_value, now_value, max(1, min(int(limit), 1_000))),
            ).fetchall()
            claimed: list[LifeOutboxMessage] = []
            for row in rows:
                fencing_token = int(row["fencing_token"] or 0) + 1
                attempt_count = int(row["attempt_count"] or 0) + 1
                cursor = conn.execute(
                    """
                    UPDATE life_delivery_outbox
                    SET status = 'CLAIMED', claim_owner = ?, fencing_token = ?,
                        claim_expires_at_ms = ?, attempt_count = ?, updated_at_ms = ?
                    WHERE outbox_id = ? AND fencing_token = ?
                    """,
                    (
                        owner,
                        fencing_token,
                        expires,
                        attempt_count,
                        now_value,
                        row["outbox_id"],
                        int(row["fencing_token"] or 0),
                    ),
                )
                if cursor.rowcount != 1:
                    continue
                claimed.append(
                    _outbox_from_row(
                        {
                            **dict(row),
                            "status": LifeDeliveryStatus.CLAIMED.value,
                            "attempt_count": attempt_count,
                            "fencing_token": fencing_token,
                        }
                    )
                )
            return claimed

    def complete_delivery(
        self,
        message: LifeOutboxMessage,
        *,
        owner_token: str,
        result: Mapping[str, Any],
        now_ms: int,
    ) -> LifeDeliveryReceipt:
        owner = _required_text(owner_token, "owner_token")
        completed_at = max(0, int(now_ms))
        with self._operation(write=True) as conn:
            existing = conn.execute(
                "SELECT * FROM life_delivery_receipt WHERE outbox_id = ?",
                (message.outbox_id,),
            ).fetchone()
            if existing is not None:
                receipt = _delivery_receipt_from_row(existing)
                _assert_delivery_receipt_matches(message, receipt)
                return receipt
            row = conn.execute(
                "SELECT * FROM life_delivery_outbox WHERE outbox_id = ?",
                (message.outbox_id,),
            ).fetchone()
            if (
                row is None
                or str(row["status"]) != LifeDeliveryStatus.CLAIMED.value
                or str(row["claim_owner"] or "") != owner
                or int(row["fencing_token"]) != message.fencing_token
                or int(row["claim_expires_at_ms"] or 0) <= completed_at
            ):
                raise LifeCommandRepositoryError(
                    LIFE_DELIVERY_CLAIM_FENCED,
                    "delivery claim is no longer owned by this dispatcher",
                )
            if (
                str(row["payload_digest"]) != message.payload_digest
                or str(row["authorization_digest"])
                != message.authorization_digest
                or str(row["delivery_key"]) != message.delivery_key
                or digest_json(dict(message.payload)) != message.payload_digest
                or digest_json(message.authorization.as_dict())
                != message.authorization_digest
            ):
                raise LifeCommandRepositoryError(
                    LIFE_COMMAND_DURABLE_DATA_INVALID,
                    "delivery claim payload or authorization digest drift",
                )
            receipt = LifeDeliveryReceipt(
                outbox_id=message.outbox_id,
                delivery_key=message.delivery_key,
                payload_digest=message.payload_digest,
                authorization_digest=message.authorization_digest,
                result=dict(result),
                completed_at_ms=completed_at,
            )
            cursor = conn.execute(
                """
                UPDATE life_delivery_outbox
                SET status = 'DELIVERED', result_json = ?, last_error = NULL,
                    available_at_ms = ?, claim_owner = NULL,
                    claim_expires_at_ms = NULL, updated_at_ms = ?,
                    delivered_at_ms = ?
                WHERE outbox_id = ? AND status = 'CLAIMED'
                  AND claim_owner = ? AND fencing_token = ?
                """,
                (
                    canonical_json(dict(receipt.result)),
                    completed_at,
                    completed_at,
                    completed_at,
                    message.outbox_id,
                    owner,
                    message.fencing_token,
                ),
            )
            if cursor.rowcount != 1:
                raise LifeCommandRepositoryError(
                    LIFE_DELIVERY_CLAIM_FENCED,
                    "delivery claim is no longer owned by this dispatcher",
                )
            conn.execute(
                """
                INSERT INTO life_delivery_receipt(
                    outbox_id, delivery_key, payload_digest,
                    authorization_digest, result_json, completed_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt.outbox_id,
                    receipt.delivery_key,
                    receipt.payload_digest,
                    receipt.authorization_digest,
                    canonical_json(dict(receipt.result)),
                    receipt.completed_at_ms,
                ),
            )
            return receipt

    def renew_delivery_claim(
        self,
        message: LifeOutboxMessage,
        *,
        owner_token: str,
        now_ms: int,
        lease_ms: int,
    ) -> None:
        owner = _required_text(owner_token, "owner_token")
        now_value = max(0, int(now_ms))
        expires = now_value + max(1, int(lease_ms))
        with self._operation(write=True) as conn:
            row = conn.execute(
                "SELECT * FROM life_delivery_outbox WHERE outbox_id = ?",
                (message.outbox_id,),
            ).fetchone()
            if row is None:
                raise LifeCommandRepositoryError(
                    LIFE_COMMAND_DURABLE_DATA_INVALID,
                    "delivery outbox row is missing",
                )
            durable = _outbox_from_row(row)
            if (
                durable.event_id != message.event_id
                or durable.command_id != message.command_id
                or durable.person_id != message.person_id
                or durable.destination != message.destination
                or durable.delivery_key != message.delivery_key
                or durable.payload_digest != message.payload_digest
                or durable.authorization_digest != message.authorization_digest
                or durable.authorization != message.authorization
                or digest_json(dict(message.payload)) != message.payload_digest
                or digest_json(message.authorization.as_dict())
                != message.authorization_digest
            ):
                raise LifeCommandRepositoryError(
                    LIFE_COMMAND_DURABLE_DATA_INVALID,
                    "delivery message differs from durable outbox identity",
                )
            cursor = conn.execute(
                """
                UPDATE life_delivery_outbox
                SET claim_expires_at_ms = ?, updated_at_ms = ?
                WHERE outbox_id = ? AND status = 'CLAIMED'
                  AND claim_owner = ? AND fencing_token = ?
                  AND COALESCE(claim_expires_at_ms, 0) > ?
                """,
                (
                    expires,
                    now_value,
                    message.outbox_id,
                    owner,
                    message.fencing_token,
                    now_value,
                ),
            )
            if cursor.rowcount != 1:
                raise LifeCommandRepositoryError(
                    LIFE_DELIVERY_CLAIM_FENCED,
                    "delivery claim expired or is no longer owned",
                )

    def fail_delivery(
        self,
        message: LifeOutboxMessage,
        *,
        owner_token: str,
        error: str,
        retry_at_ms: int,
        now_ms: int,
    ) -> None:
        self._finish_delivery(
            message,
            owner_token=owner_token,
            status=LifeDeliveryStatus.FAILED,
            result={},
            error=str(error or "delivery failed")[:2_000],
            available_at_ms=max(0, int(retry_at_ms)),
            now_ms=now_ms,
        )

    def retry_delivery(self, outbox_id: str, *, now_ms: int) -> bool:
        with self._operation(write=True) as conn:
            receipt = conn.execute(
                "SELECT 1 FROM life_delivery_receipt WHERE outbox_id = ?",
                (str(outbox_id),),
            ).fetchone()
            if receipt is not None:
                return False
            cursor = conn.execute(
                """
                UPDATE life_delivery_outbox
                SET status = 'PENDING', available_at_ms = ?, claim_owner = NULL,
                    claim_expires_at_ms = NULL, last_error = NULL, updated_at_ms = ?
                WHERE outbox_id = ? AND status = 'FAILED'
                """,
                (max(0, int(now_ms)), max(0, int(now_ms)), str(outbox_id)),
            )
            return cursor.rowcount == 1

    def get_delivery_receipt(
        self,
        outbox_id: str,
    ) -> LifeDeliveryReceipt | None:
        with self._operation(write=False) as conn:
            row = conn.execute(
                "SELECT * FROM life_delivery_receipt WHERE outbox_id = ?",
                (str(outbox_id),),
            ).fetchone()
            return None if row is None else _delivery_receipt_from_row(row)

    def list_delivery_receipts(self) -> list[LifeDeliveryReceipt]:
        with self._operation(write=False) as conn:
            rows = conn.execute(
                "SELECT * FROM life_delivery_receipt ORDER BY outbox_id ASC"
            ).fetchall()
            return [_delivery_receipt_from_row(row) for row in rows]

    def list_completed_companion_proactive_messages(
        self,
        *,
        limit: int = 100,
    ) -> list[LifeOutboxMessage]:
        """Read receipt-backed companion rows still missing their relation event.

        This is a recovery-only projection of the canonical outbox.  It does
        not claim, alter or resend messages.  A completed relation event is
        excluded at query time so recurring ticks do not replay historical
        messages; the relationship service still validates context and receipt
        identity before writing a missing episode.
        """

        with self._operation(write=False) as conn:
            rows = conn.execute(
                """
                SELECT outbox.*
                FROM life_delivery_outbox AS outbox
                INNER JOIN life_delivery_receipt AS receipt
                    ON receipt.outbox_id = outbox.outbox_id
                WHERE outbox.status = 'DELIVERED'
                  AND outbox.destination = 'proactive.gateway'
                  AND outbox.payload_json LIKE '%"_companion_relationship_context"%'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM life_event_journal AS relation_event
                      WHERE relation_event.event_type =
                            'relationship_proactive_delivered'
                        AND json_extract(
                            relation_event.event_payload_json,
                            '$.command_payload.episode.response_receipt_ref'
                        ) = ('life-delivery://' || outbox.outbox_id)
                  )
                ORDER BY receipt.completed_at_ms ASC, outbox.outbox_id ASC
                LIMIT ?
                """,
                (max(1, min(int(limit), 10_000)),),
            ).fetchall()
        return [_outbox_from_row(row) for row in rows]

    def list_outbox(self, *, command_id: str | None = None) -> list[dict[str, Any]]:
        with self._operation(write=False) as conn:
            if command_id:
                rows = conn.execute(
                    """
                    SELECT * FROM life_delivery_outbox
                    WHERE command_id = ? ORDER BY outbox_id ASC
                    """,
                    (str(command_id),),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM life_delivery_outbox ORDER BY outbox_id ASC"
                ).fetchall()
            return [dict(row) for row in rows]


def _json_object(value: Any, field_name: str) -> dict[str, Any]:
    try:
        decoded = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError) as exc:
        raise LifeCommandRepositoryError(
            LIFE_COMMAND_DURABLE_DATA_INVALID,
            f"{field_name} is not valid JSON",
        ) from exc
    if not isinstance(decoded, dict):
        raise LifeCommandRepositoryError(
            LIFE_COMMAND_DURABLE_DATA_INVALID,
            f"{field_name} must contain a JSON object",
        )
    return decoded


def _outbox_from_row(row: Mapping[str, Any]) -> LifeOutboxMessage:
    return LifeOutboxMessage(
        outbox_id=str(row["outbox_id"]),
        event_id=str(row["event_id"]),
        command_id=str(row["command_id"]),
        person_id=str(row["person_id"]),
        destination=str(row["destination"]),
        delivery_key=str(row["delivery_key"]),
        payload=_json_object(row["payload_json"], "payload_json"),
        payload_digest=str(row["payload_digest"]),
        authorization=LifeDeliveryAuthorization(
            request_id=str(row["authorization_request_id"]),
            governor_grant_ref=str(row["governor_grant_ref"]),
            authority_kind=str(row["authority_kind"]),
            authority_principal_id=str(row["authority_principal_id"]),
        ),
        authorization_digest=str(row["authorization_digest"]),
        status=LifeDeliveryStatus(str(row["status"])),
        attempt_count=int(row["attempt_count"]),
        fencing_token=int(row["fencing_token"]),
    )

def _delivery_receipt_from_row(row: Mapping[str, Any]) -> LifeDeliveryReceipt:
    return LifeDeliveryReceipt(
        outbox_id=str(row["outbox_id"]),
        delivery_key=str(row["delivery_key"]),
        payload_digest=str(row["payload_digest"]),
        authorization_digest=str(row["authorization_digest"]),
        result=_json_object(row["result_json"], "result_json"),
        completed_at_ms=int(row["completed_at_ms"]),
    )

def _assert_delivery_receipt_matches(
    message: LifeOutboxMessage,
    receipt: LifeDeliveryReceipt,
) -> None:
    if (
        receipt.delivery_key != message.delivery_key
        or receipt.payload_digest != message.payload_digest
        or receipt.authorization_digest != message.authorization_digest
    ):
        raise LifeCommandRepositoryError(
            LIFE_COMMAND_DURABLE_DATA_INVALID,
            "delivery receipt payload or authorization digest drift",
        )

def _required_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise LifeCommandRepositoryError(
            LIFE_COMMAND_INVALID,
            f"{field_name} is required",
        )
    return text
