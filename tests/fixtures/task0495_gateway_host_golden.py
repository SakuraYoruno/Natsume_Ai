"""Isolated Gateway -> Host golden scenario for TASK-0495.

The scenario uses the real raw-body verifier, 0059 durable inbox, Edge signed
reporter and Host SecureIngress route.  HTTP is kept in-process through
``TestClient`` and every domain dispatch is recording-only.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient

from edge_client.channels import input_reporter as input_reporter_module
from edge_client.channels.input_reporter import EdgeInputReporter
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
from host_brain.composition import process_services as dependencies
from host_brain.app.input_runtime_composition import build_input_runtime_application_ports
from host_brain.input_runtime import ingress as input_ingress
from host_brain.input_runtime.settings import get_input_settings
from host_brain.input_runtime.worker_dispatch import create_input_router
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.gateway_transport_repository import SQLiteGatewayTransportStore
from host_brain.secure_ingress import (
    IngressReasonCode,
    IngressSurface,
    M48B_P0_SURFACE_POLICIES,
)
from tests.fixtures.m48b_isolation import M48BIsolationHarness
from tests.fixtures.m48b_p0_validation import resolve_m48b_owned_state_root


ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "host_brain" / "audit" / "migrations"


@dataclass(frozen=True, slots=True)
class _InProcessResponse:
    status: int
    content: bytes

    def __enter__(self) -> "_InProcessResponse":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        return None

    def read(self) -> bytes:
        return self.content


def _seed_host_principal(
    db_path: Path,
    *,
    principal_id: str,
    tenant_id: str,
    key_id: str,
    now_ms: int,
) -> None:
    policy = M48B_P0_SURFACE_POLICIES[IngressSurface.HOST]
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
                f"test-secret-ref://{key_id}",
                now_ms - 60_000,
                now_ms + 3_600_000,
                now_ms,
            ),
        )


def _webhook_headers(
    *,
    secret: str,
    path: str,
    raw_body: bytes,
    nonce: str,
    now_ms: int,
) -> dict[str, str]:
    timestamp = str(now_ms // 1_000)
    return {
        "Content-Type": "application/json",
        INTERNAL_TIMESTAMP_HEADER: timestamp,
        INTERNAL_NONCE_HEADER: nonce,
        INTERNAL_SIGNATURE_HEADER: build_internal_signature(
            shared_secret=secret,
            platform="qq",
            method="POST",
            path=path,
            timestamp=timestamp,
            nonce=nonce,
            raw_body=raw_body,
        ),
    }


def run_gateway_host_golden(
    *,
    state_root: Path,
    harness: M48BIsolationHarness,
    now_ms: int,
    monkeypatch: Any,
) -> dict[str, object]:
    """Run one repeatable in-process Gateway-to-Host delivery and replay."""

    if harness.domain != "gateway":
        raise ValueError("TASK0495_GATEWAY_HOST_DOMAIN_INVALID")
    root = resolve_m48b_owned_state_root(
        harness=harness,
        state_root=state_root,
    )
    db_path = root / "central.sqlite3"
    migration = SQLiteMigrationRunner(
        db_path=db_path,
        migrations_dir=MIGRATIONS,
    ).apply_migrations(
        request_id="task0495-gateway-host-migrations",
        ts_ms=now_ms - 1,
    )
    assert migration.success, migration.as_dict()
    assert migration.summary["current_version"] >= 61

    principal_id = harness.principal
    tenant_id = "tenant.task0495.gateway-host"
    key_id = "task0495.gateway-host.hmac.v1"
    secret = harness.test_secret
    _seed_host_principal(
        db_path,
        principal_id=principal_id,
        tenant_id=tenant_id,
        key_id=key_id,
        now_ms=now_ms,
    )

    monkeypatch.setenv("HOST_SECURE_INGRESS_MODE", "ENFORCE")
    monkeypatch.setenv("NATSUME_AUDIT_DB", str(db_path))
    monkeypatch.setenv("Natsume_AUDIT_DB", str(db_path))
    monkeypatch.setenv("HOST_EDGE_GATEWAY_HMAC_KEY_ID", key_id)
    monkeypatch.setenv("HOST_EDGE_GATEWAY_HMAC_SECRET", secret)
    monkeypatch.setenv(
        "HOST_SECURE_INGRESS_HMAC_SECRETS_JSON",
        json.dumps({key_id: secret}, separators=(",", ":")),
    )
    get_input_settings.cache_clear()
    monkeypatch.setattr(
        input_ingress,
        "_evaluate_immune_gate_for_input",
        lambda **_kwargs: {
            "decision": "ALLOW",
            "risk_level": "none",
            "reason_codes": [],
            "score": 0.0,
        },
    )

    effect_count_before = len(harness.sink.effects)

    async def _record_dispatch(*_args: object, **kwargs: object) -> None:
        assert kwargs.get("source") == "GATEWAY"
        assert kwargs.get("text") == "TASK-0495 isolated golden"
        payload = kwargs.get("payload")
        assert isinstance(payload, dict)
        assert payload.get("platform") == "qq"
        assert str(payload.get("msg_id")) == "495001"
        secure_ingress = payload.get("secure_ingress")
        assert isinstance(secure_ingress, dict)
        assert secure_ingress.get("principal_id") == principal_id
        harness.sink.emit(
            "host.graph.dispatch",
            {
                "principal": str(secure_ingress["principal_id"]),
                "source": str(kwargs["source"]),
                "platform": str(payload["platform"]),
            },
        )

    monkeypatch.setattr(input_ingress, "_dispatch_to_graph", _record_dispatch)
    monkeypatch.setattr(input_ingress, "_get_ledger_safe", lambda **_kwargs: None)
    monkeypatch.setattr(
        input_ingress,
        "_record_feedback_for_latest_persona_reply",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(dependencies, "notify_user_interaction", lambda: None)
    monkeypatch.setattr(input_reporter_module.time, "time", lambda: now_ms / 1_000)

    reporter = EdgeInputReporter(
        host_url="http://host.task0495.invalid",
        hmac_key=secret,
        timeout_s=1.0,
        secure_principal_id=principal_id,
        secure_tenant_id=tenant_id,
        secure_key_id=key_id,
    )
    store = SQLiteGatewayTransportStore(db_path, clock=lambda: now_ms)
    outbound = OutboundDispatcher()
    adapter = QQAdapter("http://127.0.0.1:9", self_id="9000")
    outbound.register(adapter)
    runtime = GatewayDeliveryRuntime(
        mode=GatewayTransportMode.DURABLE,
        store=store,
        reporter=reporter,
        outbound=outbound,
        worker_id="task0495-gateway-host",
    )
    verifier = VerifierRegistry(
        (
            InternalHmacWebhookVerifier(
                platform="qq",
                shared_secret=secret,
            ),
        ),
        now_ms_fn=lambda: now_ms,
    )
    gateway_app = create_gateway_app(
        normalizer=MessageNormalizer(),
        adapters={"qq": adapter},
        outbound=outbound,
        delivery_runtime=runtime,
        webhook_verifiers=verifier,
        reply_verifiers=VerifierRegistry(()),
    )
    host_app = FastAPI()
    host_app.include_router(
        create_input_router(
            application_ports=build_input_runtime_application_ports(),
        )
    )

    raw_event = json.dumps(
        {
            "post_type": "message",
            "message_type": "private",
            "message_id": 495001,
            "user_id": 495100,
            "self_id": 9000,
            "time": now_ms // 1_000,
            "raw_message": "TASK-0495 isolated golden",
            "message": [
                {
                    "type": "text",
                    "data": {"text": "TASK-0495 isolated golden"},
                }
            ],
            "sender": {"nickname": "task0495-test"},
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    webhook_path = "/v1/gateway/qq/webhook"

    with TestClient(host_app, raise_server_exceptions=False) as host_client:
        host_statuses: list[str] = []
        host_receipt_refs: list[str] = []

        def _in_process_urlopen(request: Any, timeout: float) -> _InProcessResponse:
            del timeout
            response = host_client.post(
                urlsplit(request.full_url).path,
                content=request.data,
                headers=dict(request.header_items()),
            )
            assert response.status_code == 200, response.text
            response_body = response.json()
            host_statuses.append(str(response_body["status"]))
            host_receipt_refs.append(str(response_body["receipt_ref"]))
            return _InProcessResponse(response.status_code, response.content)

        monkeypatch.setattr(
            input_reporter_module.urllib.request,
            "urlopen",
            _in_process_urlopen,
        )
        with TestClient(gateway_app, raise_server_exceptions=False) as gateway_client:
            accepted = gateway_client.post(
                webhook_path,
                content=raw_event,
                headers=_webhook_headers(
                    secret=secret,
                    path=webhook_path,
                    raw_body=raw_event,
                    nonce="task0495.gateway.nonce.1",
                    now_ms=now_ms,
                ),
            )
            assert accepted.status_code == 200, accepted.text
            assert accepted.json()["status"] == "accepted"
            inbox_id = str(accepted.json()["inbox_id"])

            claimed = store.claim_inbox(
                worker_id="task0495-gateway-host:inbox",
                lease_ms=30_000,
            )
            assert claimed is not None
            runtime._deliver_inbox(claimed)  # noqa: SLF001 - deterministic worker seam
            delivered = store.get_inbox(inbox_id=inbox_id)
            assert delivered["status"] == "DELIVERED"
            assert int(delivered["attempt_count"]) == 1

            duplicate = gateway_client.post(
                webhook_path,
                content=raw_event,
                headers=_webhook_headers(
                    secret=secret,
                    path=webhook_path,
                    raw_body=raw_event,
                    nonce="task0495.gateway.nonce.2",
                    now_ms=now_ms,
                ),
            )
            assert duplicate.status_code == 200, duplicate.text
            assert duplicate.json()["status"] == "duplicate"

            tampered = gateway_client.post(
                webhook_path,
                content=raw_event.replace(b"isolated golden", b"tampered payload"),
                headers=_webhook_headers(
                    secret=secret,
                    path=webhook_path,
                    raw_body=raw_event,
                    nonce="task0495.gateway.nonce.3",
                    now_ms=now_ms,
                ),
            )
            assert tampered.status_code == 401, tampered.text

        replay = reporter.deliver_gateway_payload(
            dict(claimed["normalized_payload"]),
            # 0059 persists only the idempotency digest.  The worker therefore
            # uses the durable inbox identity as the Host idempotency key.
            idempotency_key=str(claimed["inbox_id"]),
        )
        assert replay.success is True

    effect_count = len(harness.sink.effects) - effect_count_before
    assert effect_count == 1, {
        "effect_count": effect_count,
        "host_statuses": host_statuses,
        "effects": list(harness.sink.effects[effect_count_before:]),
    }
    assert host_statuses == ["ACCEPTED", "IGNORED"]
    assert len(host_receipt_refs) == 2
    assert host_receipt_refs[0].startswith("host-input-result://")
    assert host_receipt_refs[0].endswith("/ACCEPTED")
    assert host_receipt_refs[1] == host_receipt_refs[0]
    with sqlite3.connect(str(db_path)) as conn:
        host_claim_rows = conn.execute(
            """
            SELECT claim_ref, claim_status, result_ref
            FROM secure_ingress_idempotency_claim
            WHERE principal_id = ?
            """,
            (principal_id,),
        ).fetchall()
        host_claims = len(host_claim_rows)
        gateway_claims = int(conn.execute(
            "SELECT COUNT(*) FROM gateway_transport_inbox",
        ).fetchone()[0])
        audit_rows = conn.execute(
            """
            SELECT claim_ref, decision, reason_code, stage,
                   side_effects_executed
            FROM secure_ingress_audit
            WHERE principal_id = ?
            """,
            (principal_id,),
        ).fetchall()
        audit_count = len(audit_rows)

    assert host_claims == 1
    assert gateway_claims == 1
    assert audit_count == 2
    claim_ref, claim_status, result_ref = host_claim_rows[0]
    assert claim_status == "COMMITTED"
    assert result_ref == host_receipt_refs[0]
    assert sorted(tuple(row) for row in audit_rows) == sorted(
        (
            (
                claim_ref,
                "ALLOW",
                IngressReasonCode.ALLOWED.value,
                "COMPLETE",
                0,
            ),
            (
                claim_ref,
                "DENY",
                IngressReasonCode.IDEMPOTENCY_DUPLICATE.value,
                "CLAIM",
                0,
            ),
        )
    )

    projection: dict[str, object] = {
        "status": "COMPLETED",
        "reason_code": "GATEWAY_HOST_GOLDEN_COMPLETED",
        "effect_count": effect_count,
        "durable_claim_count": host_claims + gateway_claims,
        "audit_count": audit_count,
        "uncertain": False,
    }
    assert secret not in json.dumps(projection, sort_keys=True)
    get_input_settings.cache_clear()
    return projection


__all__ = ["run_gateway_host_golden"]
