from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from host_brain.secure_ingress import FileEmergencyAuditSink, IngressAuditEvent


def _event() -> IngressAuditEvent:
    return IngressAuditEvent(
        request_id="request.emergency.test",
        correlation_id="correlation.emergency.test",
        surface="host.input",
        action="host:input",
        method="POST",
        normalized_path="/v1/host/input",
        principal_id="service.emergency.test",
        tenant_id="tenant.test",
        key_id="key.emergency.test",
        credential_kind="hmac-sha256",
        credential_ref_digest="sha256:" + "a" * 64,
        requested_scopes=("host:write",),
        required_scopes=("host:write",),
        body_sha256="b" * 64,
        idempotency_digest="sha256:" + "c" * 64,
        nonce_digest="sha256:" + "d" * 64,
        transport_key_digest="sha256:" + "e" * 64,
        decision="DENY",
        reason_code="SECURE_INGRESS_CREDENTIAL_INVALID",
        stage="CREDENTIAL",
        claim_ref=None,
        rate_snapshot={"lane": "ATTEMPT", "current_count": 1},
        side_effects_executed=False,
        ts_ms=1,
    )


def test_emergency_audit_is_append_only_durable_and_secret_guarded(
    tmp_path: Path,
) -> None:
    path = tmp_path / "emergency" / "secure-ingress.jsonl"
    sink = FileEmergencyAuditSink(path)

    audit_ref = sink.append(_event())
    payload = json.loads(path.read_text(encoding="utf-8").strip())

    assert payload["audit_ref"] == audit_ref
    assert payload["audit_status"] == "EMERGENCY_DURABLE"
    assert payload["event"]["side_effects_executed"] is False

    marker = "access_token=must-never-persist"
    with pytest.raises(ValueError, match="SECRET_MATERIAL_FORBIDDEN"):
        sink.append(replace(_event(), correlation_id=marker))
    assert marker not in path.read_text(encoding="utf-8")
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1
