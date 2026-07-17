from __future__ import annotations

from dataclasses import replace
import sqlite3

from fastapi.testclient import TestClient

from edge_client.app.fastapi_app import create_edge_fastapi_app
from edge_client.config import EdgeConfig
from edge_client.gateway.embodiment_runtime import SQLiteEdgeEmbodimentStore
from edge_client.gateway.migration_runner import (
    MIGRATIONS_ROOT,
    apply_edge_gateway_migrations,
)
from edge_client.rpc.auth import build_edge_rpc_signature
from natsume_contracts.edge.embodiment_v1 import (
    EMBODIMENT_FENCE_STALE,
    EMBODIMENT_LEASE_INVALID,
    EMBODIMENT_LEASE_EXPIRED,
    EMBODIMENT_RECEIPT_NOT_FOUND,
    EMBODIMENT_RECEIPT_QUERY_INVALID,
    build_embodiment_command,
    build_embodiment_receipt_query,
)
from natsume_contracts.edge.transport_v1 import (
    bind_command_to_lease,
    parse_edge_session_lease,
)
from natsume_contracts.edge.v1 import EDGE_RPC_AUTH_INVALID


class _Clock:
    def __init__(self, value: int = 1_000) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value


_SHARED_KEY = "d8-edge-receipt-query-key"


def _config() -> EdgeConfig:
    return EdgeConfig(
        env="test",
        rpc_host="127.0.0.1",
        rpc_port=17800,
        hmac_shared_key=_SHARED_KEY,
        hmac_shared_key_ref=None,
        key_profile_status=None,
        allow_insecure_auth=False,
        rpc_auth_window_ms=60_000,
        rpc_nonce_window_ms=60_000,
        rpc_gateway_enabled=True,
        asr_enabled=False,
        tts_enabled=False,
        barge_in_enabled=False,
        fallback_llm_enabled=False,
        resource_gate_enabled=False,
        max_vram_mb=1024,
        max_queue_length=32,
    )


def _signed_envelope(*, clock: _Clock, nonce: str, payload: dict, request_id: str | None = None) -> dict:
    resolved_request_id = request_id or f"request-{nonce}"
    return {
        "request_id": resolved_request_id,
        "ts_ms": clock.value,
        "nonce": nonce,
        "signature": build_edge_rpc_signature(
            request_id=resolved_request_id,
            ts_ms=clock.value,
            nonce=nonce,
            payload=payload,
            shared_key=_SHARED_KEY,
        ),
        "payload": payload,
    }


def _open_session(
    client: TestClient,
    *,
    clock: _Clock,
    session_id: str,
    owner_id: str,
    nonce: str,
):
    response = client.post(
        "/v1/edge/embodiment/session",
        json=_signed_envelope(
            clock=clock,
            nonce=nonce,
            payload={
                "session_id": session_id,
                "owner_id": owner_id,
                "ttl_ms": 10_000,
            },
        ),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True, body
    return parse_edge_session_lease(body["event"]["lease"])


def test_hmac_session_rotation_returns_new_lease_and_fences_old_command(tmp_path) -> None:
    """The wire response is safe for a replacement Host to validate exactly."""

    clock = _Clock()
    db_path = tmp_path / "edge-session-rotation.sqlite3"
    apply_edge_gateway_migrations(db_path)
    store = SQLiteEdgeEmbodimentStore(db_path, clock=clock)
    app = create_edge_fastapi_app(
        config=_config(),
        runtime=object(),
        now_fn=clock,
        startup_hook=lambda _runtime: None,
        shutdown_hook=lambda _runtime: None,
        health_collector=lambda _runtime: {},
        embodiment_store=store,
    )

    with TestClient(app) as client:
        first = _open_session(
            client,
            clock=clock,
            session_id="rotation-session",
            owner_id="rotation-owner",
            nonce="rotation-open",
        )
        old_command = bind_command_to_lease(
            build_embodiment_command(
                kind="mateengine",
                action="command",
                payload={"command": {"type": "status"}},
                authority_ref="authority://host/device",
                grant_ref="grant://host/device/rotation",
                deadline_ms=20_000,
                idempotency_key="d8-edge-rotation-old-command",
                request_id="d8-edge-rotation-old-command",
                issued_at_ms=900,
            ),
            first,
        )

        # The same exact credential without the explicit replacement bit is a
        # normal renewal and its response remains the original lease.
        renewed_response = client.post(
            "/v1/edge/embodiment/session",
            json=_signed_envelope(
                clock=clock,
                nonce="rotation-renew",
                payload={
                    "session_id": first.session_id,
                    "owner_id": first.owner_id,
                    "ttl_ms": 10_000,
                    "reconnect_lease_id": first.lease_id,
                    "reconnect_fencing_token": first.fencing_token,
                },
            ),
        )
        assert renewed_response.status_code == 200
        renewed_body = renewed_response.json()
        assert renewed_body["success"] is True, renewed_body
        renewed = parse_edge_session_lease(renewed_body["event"]["lease"])
        assert renewed.lease_id == first.lease_id
        assert renewed.fencing_token == first.fencing_token

        rotated_response = client.post(
            "/v1/edge/embodiment/session",
            json=_signed_envelope(
                clock=clock,
                nonce="rotation-replace",
                payload={
                    "session_id": renewed.session_id,
                    "owner_id": renewed.owner_id,
                    "ttl_ms": 10_000,
                    "reconnect_lease_id": renewed.lease_id,
                    "reconnect_fencing_token": renewed.fencing_token,
                    "rotate_fencing_token": True,
                },
            ),
        )
        assert rotated_response.status_code == 200
        rotated_body = rotated_response.json()
        assert rotated_body["success"] is True, rotated_body
        replacement = parse_edge_session_lease(rotated_body["event"]["lease"])
        assert replacement.session_id == first.session_id
        assert replacement.owner_id == first.owner_id
        assert replacement.fencing_token == first.fencing_token + 1
        assert replacement.lease_id != first.lease_id

        # It is the same signed route a delayed old Host request uses.  The
        # new response fence must make it fail before any effect handler runs.
        stale_command = client.post(
            "/v1/edge/embodiment",
            json=_signed_envelope(
                clock=clock,
                nonce="rotation-old-command",
                request_id=old_command.request_id,
                payload=old_command.as_dict(),
            ),
        )
        assert stale_command.status_code == 200
        stale_body = stale_command.json()
        assert stale_body["success"] is False
        assert stale_body["reason_code"] == EMBODIMENT_FENCE_STALE

        # A no-credential or other-owner caller cannot request this rotation.
        no_credential = client.post(
            "/v1/edge/embodiment/session",
            json=_signed_envelope(
                clock=clock,
                nonce="rotation-no-credential",
                payload={
                    "session_id": replacement.session_id,
                    "owner_id": replacement.owner_id,
                    "ttl_ms": 10_000,
                    "rotate_fencing_token": True,
                },
            ),
        )
        assert no_credential.json()["success"] is False

        other_owner = client.post(
            "/v1/edge/embodiment/session",
            json=_signed_envelope(
                clock=clock,
                nonce="rotation-other-owner",
                payload={
                    "session_id": replacement.session_id,
                    "owner_id": "other-owner",
                    "ttl_ms": 10_000,
                    "reconnect_lease_id": replacement.lease_id,
                    "reconnect_fencing_token": replacement.fencing_token,
                    "rotate_fencing_token": True,
                },
            ),
        )
        assert other_owner.json()["success"] is False


def test_hmac_receipt_query_is_live_lease_fenced_and_never_replays(tmp_path, monkeypatch) -> None:
    clock = _Clock()
    db_path = tmp_path / "edge-embodiment.sqlite3"
    apply_edge_gateway_migrations(db_path)
    store = SQLiteEdgeEmbodimentStore(db_path, clock=clock)
    app = create_edge_fastapi_app(
        config=_config(),
        runtime=object(),
        now_fn=clock,
        startup_hook=lambda _runtime: None,
        shutdown_hook=lambda _runtime: None,
        health_collector=lambda _runtime: {},
        embodiment_store=store,
    )
    effect_calls: list[str] = []
    monkeypatch.setattr(
        "edge_client.actuators.mateengine_driver.execute_mateengine_effect",
        lambda **kwargs: effect_calls.append(str(kwargs["action"])) or {
            "success": True,
            "status": "COMPLETED",
            "detail": "effect applied",
            "event": {"physical_effect_verified": False},
        },
    )

    with TestClient(app) as client:
        lease = _open_session(
            client,
            clock=clock,
            session_id="session-a",
            owner_id="host-a",
            nonce="session-a-open",
        )
        command = bind_command_to_lease(
            build_embodiment_command(
                kind="mateengine",
                action="command",
                payload={"command": {"type": "status"}},
                authority_ref="authority://host/device",
                grant_ref="grant://host/device/query-test",
                deadline_ms=20_000,
                idempotency_key="d8-receipt-query-command",
                request_id="d8-receipt-command",
                issued_at_ms=900,
            ),
            lease,
        )
        command_response = client.post(
            "/v1/edge/embodiment",
            json=_signed_envelope(
                clock=clock,
                nonce="command-submit",
                request_id=command.request_id,
                payload=command.as_dict(),
            ),
        )
        assert command_response.status_code == 200
        assert command_response.json()["success"] is True
        assert effect_calls == ["command"]

        query = build_embodiment_receipt_query(
            command_id=command.command_id,
            idempotency_key=command.idempotency_key,
            session_id=lease.session_id,
            lease_id=lease.lease_id,
            owner_id=lease.owner_id,
            fencing_token=lease.fencing_token,
        )
        found = client.post(
            "/v1/edge/embodiment/receipt",
            json=_signed_envelope(
                clock=clock,
                nonce="receipt-found",
                payload=query.as_dict(),
            ),
        )
        assert found.status_code == 200
        found_body = found.json()
        assert found_body["success"] is True
        assert found_body["status"] == "ACCEPTED"
        assert found_body["reason_code"] is None
        assert found_body["event"]["receipt_query"]["status"] == "FOUND"
        assert found_body["event"]["receipt_query"]["receipt_sha256"].startswith("sha256:")
        assert found_body["event"]["receipt"]["command_id"] == command.command_id
        assert found_body["event"]["receipt"]["idempotency_key"] == command.idempotency_key
        assert effect_calls == ["command"]

        other_lease = _open_session(
            client,
            clock=clock,
            session_id="session-b",
            owner_id="host-b",
            nonce="session-b-open",
        )
        cross_session_query = build_embodiment_receipt_query(
            command_id=command.command_id,
            idempotency_key=command.idempotency_key,
            session_id=other_lease.session_id,
            lease_id=other_lease.lease_id,
            owner_id=other_lease.owner_id,
            fencing_token=other_lease.fencing_token,
        )
        cross_session = client.post(
            "/v1/edge/embodiment/receipt",
            json=_signed_envelope(
                clock=clock,
                nonce="receipt-cross-session",
                payload=cross_session_query.as_dict(),
            ),
        )
        cross_session_body = cross_session.json()
        assert cross_session_body["success"] is True
        assert cross_session_body["reason_code"] == EMBODIMENT_RECEIPT_NOT_FOUND
        assert cross_session_body["event"]["receipt_query"]["status"] == "NOT_FOUND"
        assert cross_session_body["event"]["receipt"] is None
        assert effect_calls == ["command"]

        mismatched_key_payload = query.as_dict()
        mismatched_key_payload["idempotency_key"] = "another-key"
        mismatched_key = client.post(
            "/v1/edge/embodiment/receipt",
            json=_signed_envelope(
                clock=clock,
                nonce="receipt-mismatched-key",
                payload=mismatched_key_payload,
            ),
        )
        assert mismatched_key.json()["success"] is False
        assert mismatched_key.json()["reason_code"] == EMBODIMENT_RECEIPT_QUERY_INVALID
        assert effect_calls == ["command"]

        bad_signature_payload = query.as_dict()
        bad_signature = _signed_envelope(
            clock=clock,
            nonce="receipt-bad-signature",
            payload=bad_signature_payload,
        )
        bad_signature["signature"] = "forged"
        unauthenticated = client.post(
            "/v1/edge/embodiment/receipt",
            json=bad_signature,
        )
        assert unauthenticated.json()["success"] is False
        assert unauthenticated.json()["reason_code"] == EDGE_RPC_AUTH_INVALID
        assert effect_calls == ["command"]

        clock.value = lease.expires_at_ms + 1
        expired_query = replace(query, fencing_token=lease.fencing_token)
        expired = client.post(
            "/v1/edge/embodiment/receipt",
            json=_signed_envelope(
                clock=clock,
                nonce="receipt-expired-lease",
                payload=expired_query.as_dict(),
            ),
        )
        assert expired.json()["success"] is False
        assert expired.json()["reason_code"] == EMBODIMENT_LEASE_EXPIRED
        assert effect_calls == ["command"]

        # A host restart receives a new lease/fence, but preserves the stable
        # owner identity.  That owner can reconcile the old durable command.
        same_owner_lease = _open_session(
            client,
            clock=clock,
            session_id=lease.session_id,
            owner_id=lease.owner_id,
            nonce="session-a-same-owner-new-fence",
        )
        assert same_owner_lease.fencing_token > lease.fencing_token
        same_owner_query = build_embodiment_receipt_query(
            command_id=command.command_id,
            idempotency_key=command.idempotency_key,
            session_id=same_owner_lease.session_id,
            lease_id=same_owner_lease.lease_id,
            owner_id=same_owner_lease.owner_id,
            fencing_token=same_owner_lease.fencing_token,
        )
        # Re-opening the durable store models an Edge restart: no in-memory
        # lease or receipt cache is needed to reconcile the stable owner.
        restarted_store = SQLiteEdgeEmbodimentStore(db_path, clock=clock)
        restarted_lookup = restarted_store.query_receipt_for_live_lease(
            session_id=same_owner_query.session_id,
            lease_id=same_owner_query.lease_id,
            owner_id=same_owner_query.owner_id,
            fencing_token=same_owner_query.fencing_token,
            command_id=same_owner_query.command_id,
            idempotency_key=same_owner_query.idempotency_key,
        )
        assert restarted_lookup is not None
        assert restarted_lookup.receipt.command_id == command.command_id
        same_owner = client.post(
            "/v1/edge/embodiment/receipt",
            json=_signed_envelope(
                clock=clock,
                nonce="receipt-same-owner-new-fence",
                payload=same_owner_query.as_dict(),
            ),
        )
        assert same_owner.json()["success"] is True
        assert same_owner.json()["event"]["receipt_query"]["status"] == "FOUND"

        stale_fence = client.post(
            "/v1/edge/embodiment/receipt",
            json=_signed_envelope(
                clock=clock,
                nonce="receipt-stale-fence",
                payload=query.as_dict(),
            ),
        )
        assert stale_fence.json()["success"] is False
        assert stale_fence.json()["reason_code"] == EMBODIMENT_FENCE_STALE

        invalid_lease_query = replace(same_owner_query, lease_id="not-a-live-lease")
        invalid_lease = client.post(
            "/v1/edge/embodiment/receipt",
            json=_signed_envelope(
                clock=clock,
                nonce="receipt-invalid-lease",
                payload=invalid_lease_query.as_dict(),
            ),
        )
        assert invalid_lease.json()["success"] is False
        assert invalid_lease.json()["reason_code"] == EMBODIMENT_LEASE_INVALID

        # Once that lease expires, another owner can legitimately acquire the
        # same session.  It remains unable to read the original owner's row.
        clock.value = same_owner_lease.expires_at_ms + 1
        different_owner_lease = _open_session(
            client,
            clock=clock,
            session_id=lease.session_id,
            owner_id="host-b",
            nonce="session-a-different-owner-new-fence",
        )
        different_owner_query = build_embodiment_receipt_query(
            command_id=command.command_id,
            idempotency_key=command.idempotency_key,
            session_id=different_owner_lease.session_id,
            lease_id=different_owner_lease.lease_id,
            owner_id=different_owner_lease.owner_id,
            fencing_token=different_owner_lease.fencing_token,
        )
        different_owner = client.post(
            "/v1/edge/embodiment/receipt",
            json=_signed_envelope(
                clock=clock,
                nonce="receipt-different-owner",
                payload=different_owner_query.as_dict(),
            ),
        )
        different_owner_body = different_owner.json()
        assert different_owner_body["success"] is True
        assert different_owner_body["reason_code"] == EMBODIMENT_RECEIPT_NOT_FOUND
        assert different_owner_body["event"]["receipt"] is None

        # The serialized owner is bound to the live lease, so presenting the
        # old owner against the new owner's otherwise valid lease is rejected.
        forged_owner_query = replace(different_owner_query, owner_id=lease.owner_id)
        forged_owner = client.post(
            "/v1/edge/embodiment/receipt",
            json=_signed_envelope(
                clock=clock,
                nonce="receipt-forged-owner",
                payload=forged_owner_query.as_dict(),
            ),
        )
        assert forged_owner.json()["success"] is False
        assert forged_owner.json()["reason_code"] == EMBODIMENT_LEASE_INVALID
        assert effect_calls == ["command"]


def test_receipt_authority_migration_does_not_guess_legacy_command_owner(tmp_path) -> None:
    """A v1 journal row remains unreadable until it has explicit authority."""

    db_path = tmp_path / "legacy-edge-embodiment.sqlite3"
    legacy_migration = MIGRATIONS_ROOT / "0001_embodiment_transport.sql"
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(legacy_migration.read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO edge_session_lease("
            "session_id, lease_id, owner_id, fencing_token, issued_at_ms, "
            "expires_at_ms, updated_at_ms"
            ") VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("legacy-session", "legacy-lease", "legacy-owner", 1, 1, 20_000, 1),
        )
        command = build_embodiment_command(
            kind="mateengine",
            action="command",
            payload={"command": {"type": "status"}},
            authority_ref="authority://legacy/device",
            grant_ref="grant://legacy/device",
            deadline_ms=20_000,
            idempotency_key="legacy-receipt-query-command",
            request_id="legacy-receipt-command",
            issued_at_ms=1,
            session_id="legacy-session",
            lease_id="legacy-lease",
            fencing_token=1,
        )
        conn.execute(
            "INSERT INTO edge_embodiment_command("
            "command_id, idempotency_key, command_digest, session_id, lease_id, "
            "fencing_token, status, receipt_json, receipt_sha256, created_at_ms, "
            "updated_at_ms, completed_at_ms"
            ") VALUES (?, ?, ?, ?, ?, ?, 'DISPATCHING', NULL, NULL, ?, ?, NULL)",
            (
                command.command_id,
                command.idempotency_key,
                "legacy-command-digest",
                command.session_id,
                command.lease_id,
                command.fencing_token,
                1,
                1,
            ),
        )

    apply_edge_gateway_migrations(db_path)
    clock = _Clock(1_000)
    store = SQLiteEdgeEmbodimentStore(db_path, clock=clock)
    lookup = store.query_receipt_for_live_lease(
        session_id="legacy-session",
        lease_id="legacy-lease",
        owner_id="legacy-owner",
        fencing_token=1,
        command_id=command.command_id,
        idempotency_key=command.idempotency_key,
    )
    assert lookup is None
