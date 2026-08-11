from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from edge_client.gateway.adapters.qq_adapter import QQAdapter
from edge_client.gateway.delivery_runtime import (
    GatewayDeliveryRuntime,
    GatewayTransportMode,
)
from edge_client.gateway.normalizer import MessageNormalizer
from edge_client.gateway.outbound import OutboundDispatcher
from edge_client.gateway.server import create_gateway_app
from edge_client.gateway.webhook_security import (
    INTERNAL_NONCE_HEADER,
    INTERNAL_SIGNATURE_HEADER,
    INTERNAL_TIMESTAMP_HEADER,
    InternalHmacWebhookVerifier,
    VerifierRegistry,
    build_internal_signature,
)
from host_brain.app import acceptance_effect_handlers
from host_brain.app.acceptance_effect_handlers import (
    build_production_acceptance_effect_handlers,
)
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.gateway_transport_repository import (
    SQLiteGatewayTransportStore,
)
from host_brain.runtime.acceptance_effect_execution import (
    AcceptanceEffectExecutionProof,
)
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectOutcomeUnknown,
)
from natsume_contracts.edge.transport_v1 import EdgeTransportResult


ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "host_brain" / "audit" / "migrations"
REPLY_PATH = "/v1/gateway/reply"
QUERY_PATH = "/v1/gateway/reply/query"
REPLY_SECRET = "ne01-gateway-query-secret-0123456789abcdef"
PRINCIPAL = "service.host.gateway-reply"


def _migrated_db(tmp_path: Path, *, request_id: str) -> Path:
    db_path = tmp_path / f"{request_id}.sqlite3"
    result = SQLiteMigrationRunner(
        db_path=db_path,
        migrations_dir=MIGRATIONS,
    ).apply_migrations(request_id=request_id, ts_ms=int(time.time() * 1000))
    assert result.success, result.as_dict()
    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute(
            "SELECT 1 FROM schema_migration_assets WHERE asset_key = ?",
            ("0059#gateway_transport",),
        ).fetchone() == (1,)
    return db_path


def _signed_headers(
    *,
    path: str,
    body: bytes,
    nonce: str,
    timestamp: str | None = None,
    secret: str = REPLY_SECRET,
) -> dict[str, str]:
    issued = str(timestamp if timestamp is not None else int(time.time()))
    return {
        "Content-Type": "application/json; charset=utf-8",
        INTERNAL_TIMESTAMP_HEADER: issued,
        INTERNAL_NONCE_HEADER: nonce,
        INTERNAL_SIGNATURE_HEADER: build_internal_signature(
            shared_secret=secret,
            platform="internal_reply",
            method="POST",
            path=path,
            timestamp=issued,
            nonce=nonce,
            raw_body=body,
        ),
    }


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


class _RecordingQQAdapter(QQAdapter):
    def __init__(self) -> None:
        super().__init__("http://127.0.0.1:9", self_id="ne01-query")
        self.sent: list[Any] = []
        self._lock = threading.Lock()

    def send_reply(self, reply: Any) -> bool:
        with self._lock:
            self.sent.append(reply)
        return True


def _runtime(
    db_path: Path,
    *,
    outbound: OutboundDispatcher,
    worker_id: str,
) -> tuple[SQLiteGatewayTransportStore, GatewayDeliveryRuntime]:
    store = SQLiteGatewayTransportStore(db_path)
    runtime = GatewayDeliveryRuntime(
        mode=GatewayTransportMode.DURABLE,
        store=store,
        reporter=None,
        outbound=outbound,
        worker_interval_s=0.01,
        lease_ms=2_000,
        worker_id=worker_id,
    )
    return store, runtime


def _app(
    runtime: GatewayDeliveryRuntime,
    *,
    outbound: OutboundDispatcher,
    principal_id: str = PRINCIPAL,
):
    return create_gateway_app(
        normalizer=MessageNormalizer(),
        adapters={"qq": object()},
        outbound=outbound,
        delivery_runtime=runtime,
        webhook_verifiers=VerifierRegistry(()),
        reply_verifiers=VerifierRegistry(
            (
                InternalHmacWebhookVerifier(
                    platform="internal_reply",
                    shared_secret=REPLY_SECRET,
                    principal_id=principal_id,
                    credential_ref="edge.gateway.reply.hmac.v1",
                ),
            )
        ),
    )


def _wait_for(predicate: Any, *, timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for durable Gateway state")


def _reply_body(*, effect_id: str) -> dict[str, Any]:
    return {
        "request_id": "request-ne01-query",
        "delivery_id": "reply:ne01-query-delivery",
        "effect_id": effect_id,
        "platform": "qq",
        "conversation_id": "qq-conversation-ne01-query",
        "msg_type": "text",
        "content": "真实 Gateway 查询恢复",
        "wait_result": True,
        "timeout_ms": 3_000,
    }


def _query_body(
    *,
    effect_id: str,
    delivery_id: str,
    payload_digest: str,
) -> dict[str, Any]:
    return {
        "schema_version": "Natsume.gateway.reply-query.v1",
        "effect_id": effect_id,
        "delivery_id": delivery_id,
        "platform": "qq",
        "conversation_id": "qq-conversation-ne01-query",
        "payload_digest": payload_digest,
    }


def _observable_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    """Every mutable outbox field must remain unchanged across a query."""

    return {
        key: row.get(key)
        for key in (
            "status",
            "status_version",
            "fencing_token",
            "attempt_count",
            "max_attempts",
            "claim_token",
            "claim_owner",
            "claim_expires_at_ms",
            "next_attempt_at_ms",
            "last_reason_code",
            "last_replay_operator_ref",
            "updated_at_ms",
            "ack_ref",
            "ack_digest",
            "acked_at_ms",
            "dlq_at_ms",
            "cancelled_at_ms",
            "replay_count",
        )
    }


def test_authenticated_query_is_exact_read_only_and_replay_safe(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path, request_id="ne01-gateway-query-exact")
    adapter = _RecordingQQAdapter()
    outbound = OutboundDispatcher()
    outbound.register(adapter)
    store, runtime = _runtime(db_path, outbound=outbound, worker_id="query-exact")
    runtime.start()
    effect_id = "acceptance-effect://sha256/" + "a" * 64
    reply = _reply_body(effect_id=effect_id)
    reply_bytes = _canonical(reply)
    try:
        with TestClient(_app(runtime, outbound=outbound)) as client:
            accepted = client.post(
                REPLY_PATH,
                content=reply_bytes,
                headers=_signed_headers(
                    path=REPLY_PATH,
                    body=reply_bytes,
                    nonce="ne01-query-reply",
                ),
            )
            assert accepted.status_code == 200, accepted.text
            accepted_payload = accepted.json()
            outbox_id = accepted_payload["outbox_id"]
            _wait_for(lambda: store.get_outbox(outbox_id=outbox_id)["status"] == "ACKED")
            assert len(adapter.sent) == 1
            before = _observable_snapshot(store.get_outbox(outbox_id=outbox_id))

            query = _query_body(
                effect_id=effect_id,
                delivery_id=reply["delivery_id"],
                payload_digest=accepted_payload["payload_digest"],
            )
            query_bytes = _canonical(query)
            headers = _signed_headers(
                path=QUERY_PATH,
                body=query_bytes,
                nonce="ne01-query-read-replayed",
            )
            first = client.post(QUERY_PATH, content=query_bytes, headers=headers)
            # Replaying a signed read nonce is harmless: no claim, attempt,
            # status, fence or timestamp is updated and no delivery is sent.
            replayed = client.post(QUERY_PATH, content=query_bytes, headers=headers)
            assert first.status_code == replayed.status_code == 200
            assert first.json() == replayed.json()
            snapshot = first.json()
            assert snapshot == {
                "status": "committed",
                "schema_version": "Natsume.gateway.reply-query.v1",
                "effect_id": effect_id,
                "delivery_id": reply["delivery_id"],
                "platform": "qq",
                "conversation_id": reply["conversation_id"],
                "payload_digest": accepted_payload["payload_digest"],
                "effect_status": "COMMITTED",
                "reason_code": None,
                "outbox_id": outbox_id,
                "delivery_state": "ACKED",
                "status_version": snapshot["status_version"],
                "fencing_token": snapshot["fencing_token"],
                "delivery_receipt_digest": snapshot["delivery_receipt_digest"],
            }
            assert snapshot["status_version"] >= 1
            assert snapshot["fencing_token"] >= 1
            assert snapshot["delivery_receipt_digest"].startswith("sha256:")
            assert _observable_snapshot(store.get_outbox(outbox_id=outbox_id)) == before
            assert len(adapter.sent) == 1

            # effect_id lives in the immutable durable payload.  A caller may
            # know the delivery id and payload digest, but cannot substitute a
            # different accepted effect identity and receive COMMITTED.
            forged_effect = {
                **query,
                "effect_id": "acceptance-effect://sha256/" + "f" * 64,
            }
            forged_effect_bytes = _canonical(forged_effect)
            durable_before_forgery = store.get_outbox(outbox_id=outbox_id)
            forged = client.post(
                QUERY_PATH,
                content=forged_effect_bytes,
                headers=_signed_headers(
                    path=QUERY_PATH,
                    body=forged_effect_bytes,
                    nonce="ne01-query-effect-id-drift",
                ),
            )
            assert forged.status_code == 200
            forged_payload = forged.json()
            assert forged_payload["effect_status"] == "UNKNOWN"
            assert forged_payload["reason_code"] == "GW_REPLY_QUERY_IDENTITY_MISMATCH"
            assert forged_payload["outbox_id"] is None
            assert store.get_outbox(outbox_id=outbox_id) == durable_before_forgery
            assert len(adapter.sent) == 1

            # A caller-signed identity drift never returns canonical store or
            # provider detail and cannot be interpreted as NO_SIDE_EFFECT.
            drifted = {**query, "payload_digest": "sha256:" + "0" * 64}
            drifted_bytes = _canonical(drifted)
            mismatch = client.post(
                QUERY_PATH,
                content=drifted_bytes,
                headers=_signed_headers(
                    path=QUERY_PATH,
                    body=drifted_bytes,
                    nonce="ne01-query-digest-drift",
                ),
            )
            assert mismatch.status_code == 200
            mismatch_payload = mismatch.json()
            assert mismatch_payload["effect_status"] == "UNKNOWN"
            assert mismatch_payload["reason_code"] == "GW_REPLY_QUERY_IDENTITY_MISMATCH"
            assert mismatch_payload["outbox_id"] is None
            assert "ack_ref" not in mismatch_payload
            assert "last_reason_code" not in mismatch_payload

            missing = {**query, "delivery_id": "reply:ne01-query-missing"}
            missing_bytes = _canonical(missing)
            not_found = client.post(
                QUERY_PATH,
                content=missing_bytes,
                headers=_signed_headers(
                    path=QUERY_PATH,
                    body=missing_bytes,
                    nonce="ne01-query-not-found",
                ),
            ).json()
            assert not_found["effect_status"] == "UNKNOWN"
            assert not_found["reason_code"] == "GW_REPLY_QUERY_NOT_FOUND"
            assert not_found["outbox_id"] is None

            wrong_signature = client.post(
                QUERY_PATH,
                content=query_bytes,
                headers={
                    **headers,
                    INTERNAL_SIGNATURE_HEADER: "0" * 64,
                },
            )
            assert wrong_signature.status_code == 401

            expired = client.post(
                QUERY_PATH,
                content=query_bytes,
                headers=_signed_headers(
                    path=QUERY_PATH,
                    body=query_bytes,
                    nonce="ne01-query-expired",
                    timestamp=str(int(time.time()) - 301),
                ),
            )
            assert expired.status_code == 401

            tampered_bytes = query_bytes.replace(
                b"qq-conversation-ne01-query",
                b"qq-conversation-ne01-tamper",
            )
            assert tampered_bytes != query_bytes
            body_tamper = client.post(
                QUERY_PATH,
                content=tampered_bytes,
                headers=headers,
            )
            assert body_tamper.status_code == 401
            assert _observable_snapshot(store.get_outbox(outbox_id=outbox_id)) == before
    finally:
        runtime.stop()

    # The same key under a different verified principal cannot resolve the
    # deterministic outbox identity and receives no canonical row fields.
    other_store, other_runtime = _runtime(
        db_path,
        outbound=outbound,
        worker_id="query-other-principal",
    )
    with TestClient(
        _app(
            other_runtime,
            outbound=outbound,
            principal_id="service.other.gateway-reply",
        )
    ) as client:
        query = _query_body(
            effect_id=effect_id,
            delivery_id=reply["delivery_id"],
            payload_digest=accepted_payload["payload_digest"],
        )
        query_bytes = _canonical(query)
        isolated = client.post(
            QUERY_PATH,
            content=query_bytes,
            headers=_signed_headers(
                path=QUERY_PATH,
                body=query_bytes,
                nonce="ne01-query-other-principal",
            ),
        ).json()
        assert isolated["effect_status"] == "UNKNOWN"
        assert isolated["reason_code"] == "GW_REPLY_QUERY_NOT_FOUND"
        assert isolated["outbox_id"] is None
    del other_store


@pytest.mark.asyncio
async def test_surface_response_loss_restart_reopens_without_second_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = _migrated_db(tmp_path, request_id="ne01-surface-query-restart")
    adapter = _RecordingQQAdapter()
    outbound = OutboundDispatcher()
    outbound.register(adapter)
    first_store, first_runtime = _runtime(
        db_path,
        outbound=outbound,
        worker_id="surface-query-first",
    )
    first_runtime.start()

    effect_id = "acceptance-effect://sha256/" + "b" * 64
    value = {
        "request_id": "request-ne01-surface-query-restart",
        "session_id": "session-ne01-surface-query-restart",
        "person_id": "person-ne01-surface-query-restart",
        "reply_channel": "qq",
        "reply_target": "qq-conversation-ne01-query",
        "decision": "APPROVE",
        "narrative": "真实 Gateway 查询恢复",
        "authority": {
            "request_id": "request-ne01-surface-query-restart",
            "governor_grant_ref": "governor-grant://ne01/surface-query",
            "authority_kind": "governed_task_acceptance",
            "authority_principal_id": "person-ne01-surface-query-restart",
        },
    }

    payload = {
        **value,
        "_acceptance_effect_execution_proof": AcceptanceEffectExecutionProof(
            effect_id=effect_id,
            effect_type="surface.reply_delivery",
            graph_id="graph-ne01-query",
            graph_revision=1,
            node_id="node-ne01-query",
            attempt_id="attempt-ne01-query",
            lease_owner_id="acceptance-owner-first",
            fencing_token=1,
            payload_sha256="sha256:" + "1" * 64,
            action_args=value,
        ),
    }
    delivery_id = acceptance_effect_handlers._surface_delivery_id(value)
    expected_digest = acceptance_effect_handlers._surface_gateway_payload_digest(
        value,
        effect_id=effect_id,
    )
    post_calls = 0
    outbox_id = ""

    with TestClient(_app(first_runtime, outbound=outbound)) as first_client:

        async def commit_then_lose(**kwargs: Any) -> EdgeTransportResult:
            nonlocal post_calls, outbox_id
            post_calls += 1
            assert kwargs.get("delivery_id") is None
            assert kwargs["effect_id"] == effect_id
            assert isinstance(
                kwargs.get("accepted_effect_proof"),
                AcceptanceEffectExecutionProof,
            )
            body = _reply_body(effect_id=effect_id)
            body.update(
                {
                    "request_id": value["request_id"],
                    "delivery_id": delivery_id,
                    "conversation_id": value["reply_target"],
                    "content": value["narrative"],
                    "timeout_ms": 12_000,
                }
            )
            encoded = _canonical(body)
            response = first_client.post(
                REPLY_PATH,
                content=encoded,
                headers=_signed_headers(
                    path=REPLY_PATH,
                    body=encoded,
                    nonce="ne01-surface-response-lost",
                ),
            )
            assert response.status_code == 200, response.text
            assert response.json()["payload_digest"] == expected_digest
            outbox_id = response.json()["outbox_id"]
            raise TimeoutError("simulated lost Host response")

        monkeypatch.setattr(
            "host_brain.channels.edge_transport.submit_gateway_reply",
            commit_then_lose,
        )
        first_handler = build_production_acceptance_effect_handlers()[
            "surface.reply_delivery"
        ]
        with pytest.raises(AcceptanceEffectOutcomeUnknown):
            await first_handler(payload)
        _wait_for(
            lambda: first_store.get_outbox(outbox_id=outbox_id)["status"]
            == "ACKED"
        )
        assert post_calls == 1
        assert len(adapter.sent) == 1
    first_runtime.stop()

    restarted_store, restarted_runtime = _runtime(
        db_path,
        outbound=outbound,
        worker_id="surface-query-restarted",
    )
    before = _observable_snapshot(
        restarted_store.get_outbox(outbox_id=outbox_id)
    )
    with TestClient(_app(restarted_runtime, outbound=outbound)) as restarted_client:

        async def authenticated_query(**kwargs: Any) -> EdgeTransportResult:
            encoded = _canonical(
                {
                    "schema_version": "Natsume.gateway.reply-query.v1",
                    **kwargs,
                }
            )
            response = restarted_client.post(
                QUERY_PATH,
                content=encoded,
                headers=_signed_headers(
                    path=QUERY_PATH,
                    body=encoded,
                    nonce="ne01-surface-query-after-restart",
                ),
            )
            raw = response.json()
            return EdgeTransportResult(
                success=response.status_code == 200,
                status=str(raw.get("status") or "unknown"),
                reason_code=raw.get("reason_code"),
                detail="authenticated query",
                event=raw,
                latency_ms=1,
            )

        restarted_handler = build_production_acceptance_effect_handlers(
            gateway_reply_query_port=authenticated_query,
        )["surface.reply_delivery"]
        reopened = await restarted_handler.reconcile(payload)

    assert reopened["effect_status"] == "COMMITTED"
    assert reopened["outbox_id"] == outbox_id
    assert reopened["delivery_id"] == delivery_id
    assert reopened["payload_digest"] == expected_digest
    assert reopened["replayed"] is True
    assert reopened["receipt_ref"].startswith("gateway-outbox://id/")
    assert post_calls == 1
    assert len(adapter.sent) == 1
    assert _observable_snapshot(
        restarted_store.get_outbox(outbox_id=outbox_id)
    ) == before
