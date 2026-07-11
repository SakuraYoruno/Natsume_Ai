from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlsplit
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from edge_client.channels.input_reporter import EdgeInputReporter
from edge_client.gateway.adapters.qq_adapter import QQAdapter
from edge_client.gateway.delivery_runtime import (
    GatewayDeliveryRuntime,
    GatewayTransportMode,
)
from edge_client.gateway.normalizer import MessageNormalizer
from edge_client.gateway.outbound import OutboundDispatcher
from edge_client.gateway.router import GatewayRouter
from edge_client.gateway.server import create_gateway_app
from edge_client.gateway.webhook_security import (
    INTERNAL_NONCE_HEADER,
    INTERNAL_SIGNATURE_HEADER,
    INTERNAL_TIMESTAMP_HEADER,
    InternalHmacWebhookVerifier,
    VerifierRegistry,
    build_internal_signature,
)
from host_brain.app.input_routes import router as host_input_router
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.eventing.gateway_transport import SQLiteGatewayTransportStore
from host_brain.secure_ingress import IngressSurface, M48B_P0_SURFACE_POLICIES
from tests.fixtures.m48b_isolation import build_m48b_isolation_harness


ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "host_brain" / "audit" / "migrations"


def _migrated_db(tmp_path: Path, *, request_id: str) -> Path:
    db_path = tmp_path / f"{request_id}.sqlite3"
    result = SQLiteMigrationRunner(
        db_path=db_path,
        migrations_dir=MIGRATIONS,
    ).apply_migrations(request_id=request_id, ts_ms=int(time.time() * 1000))
    assert result.success, result.as_dict()
    assert result.summary["current_version"] >= 59
    with sqlite3.connect(str(db_path)) as conn:
        assets = dict(conn.execute(
            """
            SELECT asset_key, disposition
            FROM schema_migration_assets
            WHERE asset_key IN ('0058#secure_ingress', '0059#gateway_transport')
            """
        ).fetchall())
    assert assets == {
        "0058#secure_ingress": "APPLIED",
        "0059#gateway_transport": "APPLIED",
    }
    return db_path


def _signed_headers(
    *,
    secret: str,
    platform: str,
    path: str,
    body: bytes,
    nonce: str,
) -> dict[str, str]:
    timestamp = str(int(time.time()))
    return {
        "Content-Type": "application/json",
        INTERNAL_TIMESTAMP_HEADER: timestamp,
        INTERNAL_NONCE_HEADER: nonce,
        INTERNAL_SIGNATURE_HEADER: build_internal_signature(
            shared_secret=secret,
            platform=platform,
            method="POST",
            path=path,
            timestamp=timestamp,
            nonce=nonce,
            raw_body=body,
        ),
    }


def _wait_for(predicate, *, timeout_s: float = 5.0) -> None:  # noqa: ANN001
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for durable transport state")


class _RecordingReporter:
    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, object], str]] = []
        self._lock = threading.Lock()

    def deliver_gateway_payload(self, payload, *, idempotency_key: str):  # noqa: ANN001
        with self._lock:
            self.calls.append((dict(payload), idempotency_key))
        receipt_body_sha256 = hashlib.sha256(json.dumps(
            dict(payload),
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")).hexdigest()
        return SimpleNamespace(
            success=True,
            reason_code=None,
            detail="host accepted",
            receipt_ref=(
                "host-input-result://integration/ACCEPTED/"
                f"{receipt_body_sha256[:16]}"
            ),
            receipt_body_sha256=receipt_body_sha256,
        )

    def report_gateway_payload(self, payload):  # noqa: ANN001
        return self.deliver_gateway_payload(payload, idempotency_key="compat")


class _RecordingQQAdapter(QQAdapter):
    def __init__(self) -> None:
        super().__init__("http://127.0.0.1:9", self_id="9000")
        self.sent = []
        self._sent_lock = threading.Lock()

    def send_reply(self, reply):  # noqa: ANN001
        with self._sent_lock:
            self.sent.append(reply)
        return True


def _gateway_app(
    *,
    runtime: GatewayDeliveryRuntime,
    reporter: _RecordingReporter,
    adapter: _RecordingQQAdapter,
    outbound: OutboundDispatcher,
    webhook_secret: str,
    reply_secret: str,
):
    return create_gateway_app(
        router=GatewayRouter(reporter),
        normalizer=MessageNormalizer(),
        adapters={"qq": adapter},
        outbound=outbound,
        delivery_runtime=runtime,
        webhook_verifiers=VerifierRegistry((
            InternalHmacWebhookVerifier(
                platform="qq",
                shared_secret=webhook_secret,
            ),
        )),
        reply_verifiers=VerifierRegistry((
            InternalHmacWebhookVerifier(
                platform="internal_reply",
                shared_secret=reply_secret,
            ),
        )),
    )


def test_task0488_gateway_raw_verify_durable_restart_and_reply_wave(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path, request_id="task0488-gateway")
    bridge_harness = build_m48b_isolation_harness(
        domain="bridge",
        state_root=tmp_path / "bridge-isolation",
    )
    gateway_harness = build_m48b_isolation_harness(
        domain="gateway",
        state_root=tmp_path / "gateway-isolation",
    )
    webhook_secret = bridge_harness.test_secret
    reply_secret = gateway_harness.test_secret
    reporter = _RecordingReporter()
    adapter = _RecordingQQAdapter()
    outbound = OutboundDispatcher()
    outbound.register(adapter)

    raw_event = json.dumps(
        {
            "post_type": "message",
            "message_type": "private",
            "message_id": 480001,
            "user_id": 480100,
            "self_id": 9000,
            "time": int(time.time()),
            "raw_message": "TASK-0488 durable ingress",
            "message": [{"type": "text", "data": {"text": "TASK-0488 durable ingress"}}],
            "sender": {"nickname": "integration-user"},
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    webhook_path = "/v1/gateway/qq/webhook"

    first_store = SQLiteGatewayTransportStore(db_path)
    first_runtime = GatewayDeliveryRuntime(
        mode=GatewayTransportMode.DURABLE,
        store=first_store,
        reporter=reporter,
        outbound=outbound,
        worker_interval_s=0.01,
        lease_ms=2_000,
        worker_id="task0488-first",
    )
    first_runtime.start()
    try:
        with TestClient(_gateway_app(
            runtime=first_runtime,
            reporter=reporter,
            adapter=adapter,
            outbound=outbound,
            webhook_secret=webhook_secret,
            reply_secret=reply_secret,
        )) as client:
            accepted = client.post(
                webhook_path,
                content=raw_event,
                headers=_signed_headers(
                    secret=webhook_secret,
                    platform="qq",
                    path=webhook_path,
                    body=raw_event,
                    nonce="task0488-webhook-first",
                ),
            )
        assert accepted.status_code == 200, accepted.text
        accepted_payload = accepted.json()
        assert accepted_payload["status"] == "accepted"
        inbox_id = accepted_payload["inbox_id"]
        _wait_for(
            lambda: first_store.get_inbox(inbox_id=inbox_id)["status"] == "DELIVERED"
        )
        assert len(reporter.calls) == 1
        assert reporter.calls[0][0]["source"] == "GATEWAY"
    finally:
        first_runtime.stop()

    # A process restart and a newly signed platform retry must reuse the same
    # durable business identity without delivering to Host a second time.
    restarted_store = SQLiteGatewayTransportStore(db_path)
    restarted_runtime = GatewayDeliveryRuntime(
        mode=GatewayTransportMode.DURABLE,
        store=restarted_store,
        reporter=reporter,
        outbound=outbound,
        worker_interval_s=0.01,
        lease_ms=2_000,
        worker_id="task0488-restarted",
    )
    restarted_runtime.start()
    try:
        with TestClient(_gateway_app(
            runtime=restarted_runtime,
            reporter=reporter,
            adapter=adapter,
            outbound=outbound,
            webhook_secret=webhook_secret,
            reply_secret=reply_secret,
        )) as client:
            duplicate = client.post(
                webhook_path,
                content=raw_event,
                headers=_signed_headers(
                    secret=webhook_secret,
                    platform="qq",
                    path=webhook_path,
                    body=raw_event,
                    nonce="task0488-webhook-after-restart",
                ),
            )
            assert duplicate.status_code == 200, duplicate.text
            assert duplicate.json()["status"] == "duplicate"
            duplicate_inbox = restarted_store.get_inbox(inbox_id=inbox_id)
            assert duplicate_inbox["status"] == "DELIVERED"
            assert duplicate_inbox["attempt_count"] == 1
            assert len(reporter.calls) == 1

            reply_path = "/v1/gateway/reply"
            reply_body = json.dumps(
                {
                    "request_id": "task0488-business-reply-1",
                    "delivery_id": "task0488-business-reply-1-primary",
                    "platform": "qq",
                    "conversation_id": "qq_dm_480100",
                    "msg_type": "text",
                    "content": "durable reply",
                    "wait_result": True,
                    "timeout_ms": 3_000,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            first_reply = client.post(
                reply_path,
                content=reply_body,
                headers=_signed_headers(
                    secret=reply_secret,
                    platform="internal_reply",
                    path=reply_path,
                    body=reply_body,
                    nonce="task0488-reply-first",
                ),
            )
            assert first_reply.status_code == 200, first_reply.text
            assert first_reply.json()["status"] == "sent"
            outbox_id = first_reply.json()["outbox_id"]
            first_outbox = restarted_store.get_outbox(outbox_id=outbox_id)
            assert first_outbox["status"] == "ACKED"
            assert first_outbox["attempt_count"] == 1
            assert len(adapter.sent) == 1

            duplicate_reply = client.post(
                reply_path,
                content=reply_body,
                headers=_signed_headers(
                    secret=reply_secret,
                    platform="internal_reply",
                    path=reply_path,
                    body=reply_body,
                    nonce="task0488-reply-duplicate",
                ),
            )
            assert duplicate_reply.status_code == 200, duplicate_reply.text
            assert duplicate_reply.json()["duplicate"] is True
            duplicate_outbox = restarted_store.get_outbox(outbox_id=outbox_id)
            assert duplicate_outbox["status"] == "ACKED"
            assert duplicate_outbox["attempt_count"] == 1
            assert len(adapter.sent) == 1

            changed_target = json.loads(reply_body)
            changed_target["platform"] = "wechat"
            conflict_body = json.dumps(
                changed_target,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            conflict = client.post(
                reply_path,
                content=conflict_body,
                headers=_signed_headers(
                    secret=reply_secret,
                    platform="internal_reply",
                    path=reply_path,
                    body=conflict_body,
                    nonce="task0488-reply-target-conflict",
                ),
            )
            assert conflict.status_code == 409, conflict.text
        assert len(adapter.sent) == 1
    finally:
        restarted_runtime.stop()


def _seed_host_gateway_principal(
    db_path: Path,
    *,
    principal_id: str,
    tenant_id: str,
    key_id: str,
    secret_ref: str,
) -> None:
    policy = M48B_P0_SURFACE_POLICIES[IngressSurface.HOST]
    now_ms = int(time.time() * 1000)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO secure_ingress_principal(
                principal_id, principal_kind, tenant_id, status, version,
                granted_scopes_json, allowed_audiences_json,
                allowed_actions_json, valid_from_ms, valid_until_ms,
                updated_at_ms
            ) VALUES (?, 'service', ?, 'ACTIVE', 1, ?, ?, ?, ?, ?, ?)
            """,
            (
                principal_id,
                tenant_id,
                json.dumps(list(policy.required_scopes)),
                json.dumps([policy.audience]),
                json.dumps([policy.action]),
                now_ms - 60_000,
                now_ms + 3_600_000,
                now_ms,
            ),
        )
        conn.execute(
            """
            INSERT INTO secure_ingress_credential(
                principal_id, key_id, credential_kind, algorithm,
                secret_ref, status, version, valid_from_ms,
                valid_until_ms, updated_at_ms
            ) VALUES (?, ?, 'hmac-sha256', 'hmac-sha256', ?, 'ACTIVE', 1, ?, ?, ?)
            """,
            (
                principal_id,
                key_id,
                secret_ref,
                now_ms - 60_000,
                now_ms + 3_600_000,
                now_ms,
            ),
        )


@dataclass
class _URLResponse:
    status: int
    content: bytes

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:  # noqa: ANN001
        return None

    def read(self) -> bytes:
        return self.content


def test_task0488_edge_reporter_to_host_secure_ingress_lost_response_wave(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = _migrated_db(tmp_path, request_id="task0488-host")
    harness = build_m48b_isolation_harness(
        domain="gateway",
        state_root=tmp_path / "host-isolation",
    )
    principal_id = harness.principal
    tenant_id = "tenant.task0488"
    key_id = "edge.gateway.task0488.hmac.v1"
    secret = harness.test_secret
    _seed_host_gateway_principal(
        db_path,
        principal_id=principal_id,
        tenant_id=tenant_id,
        key_id=key_id,
        secret_ref=f"test-secret-ref://{key_id}",
    )

    monkeypatch.setenv("HOST_SECURE_INGRESS_MODE", "ENFORCE")
    monkeypatch.setenv("NATSUME_AUDIT_DB", str(db_path))
    monkeypatch.setenv("Natsume_AUDIT_DB", str(db_path))
    monkeypatch.setenv("HOST_EDGE_GATEWAY_HMAC_KEY_ID", key_id)
    monkeypatch.setenv("HOST_EDGE_GATEWAY_HMAC_SECRET", secret)
    monkeypatch.setattr(
        "host_brain.app.input_routes._evaluate_immune_gate_for_input",
        lambda **_kwargs: {
            "decision": "ALLOW",
            "risk_level": "none",
            "reason_codes": [],
            "score": 0.0,
        },
    )
    dispatch = AsyncMock(return_value=None)
    monkeypatch.setattr("host_brain.app.input_routes._dispatch_to_graph", dispatch)
    monkeypatch.setattr("host_brain.app.input_routes._get_ledger_safe", lambda: None)
    monkeypatch.setattr(
        "host_brain.app.input_routes._record_feedback_for_latest_persona_reply",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "host_brain.runtime.life_continuity.advance_inner_life_to_now",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.notify_user_interaction",
        lambda: None,
    )

    reporter = EdgeInputReporter(
        host_url="http://host.task0488",
        hmac_key=secret,
        timeout_s=1.0,
        cache_file=None,
        secure_principal_id=principal_id,
        secure_tenant_id=tenant_id,
        secure_key_id=key_id,
    )
    payload = {
        "source": "GATEWAY",
        "platform": "qq",
        "msg_id": "task0488-host-msg-1",
        "from_id": "480100",
        "to_id": "9000",
        "conversation_id": "qq_dm_480100",
        "msg_type": "text",
        "content": "Host secure ingress integration",
        "is_group": False,
        "ts_ms": int(time.time() * 1000),
    }

    test_host_app = FastAPI()
    test_host_app.include_router(host_input_router)
    with TestClient(test_host_app, raise_server_exceptions=False) as client:
        lose_first_response = [True]
        host_statuses: list[str] = []

        def _urlopen(request, timeout):  # noqa: ANN001, ARG001
            response = client.post(
                urlsplit(request.full_url).path,
                content=request.data,
                headers=dict(request.header_items()),
            )
            assert response.status_code == 200, response.text
            response_payload = response.json()
            assert response_payload["success"] is True
            host_statuses.append(str(response_payload["status"]))
            if lose_first_response[0]:
                assert response_payload["status"] == "ACCEPTED"
                lose_first_response[0] = False
                raise TimeoutError("simulated response loss after Host commit")
            assert response_payload["status"] == "IGNORED"
            return _URLResponse(response.status_code, response.content)

        monkeypatch.setattr(
            "edge_client.channels.input_reporter.urllib.request.urlopen",
            _urlopen,
        )
        first = reporter.deliver_gateway_payload(
            payload,
            idempotency_key="gateway-inbox-task0488-host-1",
        )
        duplicate = reporter.deliver_gateway_payload(
            payload,
            idempotency_key="gateway-inbox-task0488-host-1",
        )

        assert first.success is False
        assert first.reason_code == "INPUT_REPORT_DELIVERY_UNCERTAIN"
        assert duplicate.success is True
        assert host_statuses == ["ACCEPTED", "IGNORED"]
        assert dispatch.await_count == 1

        raw_body = reporter._serialize_payload(payload)
        tampered_headers = reporter._build_secure_ingress_headers(
            body=raw_body,
            idempotency_key="edge-host:task0488-tampered",
        )
        tampered_headers["X-Natsume-Proof"] = "0" * 64
        rejected = client.post(
            "/v1/host/input",
            content=raw_body,
            headers={"Content-Type": "application/json", **tampered_headers},
        )
        assert rejected.status_code == 401, rejected.text
        assert dispatch.await_count == 1
