from __future__ import annotations

from collections.abc import Callable
import hashlib
from pathlib import Path
import sqlite3
from typing import Any

import pytest

from host_brain.persistence.secure_ingress_repository import (
    SECURE_INGRESS_MIGRATION_SHA256,
    SQLiteSecureIngressStore,
)
from host_brain.secure_ingress import ClaimStatus, IngressAuditEvent
from host_brain.secure_ingress.graph_cancellation_authority import (
    SecureIngressGraphCancellationAuthorityVerifier,
    TASK_GRAPH_CANCEL_ACTION,
    TASK_GRAPH_CANCEL_PATH,
    TASK_GRAPH_CANCEL_REQUIRED_SCOPES,
    canonical_task_graph_cancel_body,
)


ROOT = Path(__file__).resolve().parents[2]
MIGRATION_0058 = ROOT / "host_brain/audit/migrations/0058_secure_ingress.sql"
NOW = 1_800_000_000_000
GRAPH_ID = "graph.ne01.operator-cancel"
GRAPH_REVISION = 7
REASON_CODE = "OPERATOR_REQUEST"
REQUEST_ID = "request.ne01.operator-cancel"
PRINCIPAL_ID = "service.obs.operator.test"
TENANT_ID = "tenant.obs.test"


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _migrated_0058_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "secure-ingress-0058.sqlite3"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(MIGRATION_0058.read_text(encoding="utf-8"))
        conn.executescript(
            """
            CREATE TABLE schema_migration_assets (
                asset_key TEXT PRIMARY KEY,
                migration_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                checksum TEXT NOT NULL,
                disposition TEXT NOT NULL,
                applied_ts_ms INTEGER NOT NULL,
                UNIQUE(migration_id, name)
            );
            """
        )
        conn.execute(
            """
            INSERT INTO schema_migration_assets(
                asset_key, migration_id, name, checksum, disposition,
                applied_ts_ms
            ) VALUES (
                '0058#secure_ingress', 58, 'secure_ingress', ?, 'APPLIED', ?
            )
            """,
            (SECURE_INGRESS_MIGRATION_SHA256, NOW),
        )
    return db_path


def _commit_authority(
    db_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    body = canonical_task_graph_cancel_body(
        graph_id=GRAPH_ID,
        graph_revision=GRAPH_REVISION,
        reason_code=REASON_CODE,
    )
    body_sha256 = hashlib.sha256(body).hexdigest()
    store = SQLiteSecureIngressStore(db_path, rate_limit=100)
    try:
        claim = store.try_claim(
            principal_id=PRINCIPAL_ID,
            surface="obs.command",
            action=TASK_GRAPH_CANCEL_ACTION,
            required_scopes=TASK_GRAPH_CANCEL_REQUIRED_SCOPES,
            idempotency_key="idem.ne01.operator-cancel",
            nonce="nonce.ne01.operator-cancel",
            body_sha256=body_sha256,
            request_id=REQUEST_ID,
            at_ms=NOW,
            expires_at_ms=NOW + 60_000,
        )
        assert claim.status is ClaimStatus.NEW
        assert claim.claim_ref is not None
        audit_ref = store.commit_with_audit(
            claim_ref=claim.claim_ref,
            event=IngressAuditEvent(
                request_id=REQUEST_ID,
                correlation_id="correlation.ne01.operator-cancel",
                surface="obs.command",
                action=TASK_GRAPH_CANCEL_ACTION,
                method="POST",
                normalized_path=TASK_GRAPH_CANCEL_PATH,
                principal_id=PRINCIPAL_ID,
                tenant_id=TENANT_ID,
                key_id="key.obs.operator.test",
                credential_kind="hmac-sha256",
                credential_ref_digest=_digest("credential.ne01.operator-cancel"),
                requested_scopes=TASK_GRAPH_CANCEL_REQUIRED_SCOPES,
                required_scopes=TASK_GRAPH_CANCEL_REQUIRED_SCOPES,
                body_sha256=body_sha256,
                idempotency_digest=_digest("idem.ne01.operator-cancel"),
                nonce_digest=_digest("nonce.ne01.operator-cancel"),
                transport_key_digest=_digest("transport.ne01.operator-cancel"),
                decision="ALLOW",
                reason_code="SECURE_INGRESS_ALLOWED",
                stage="COMPLETE",
                claim_ref=claim.claim_ref,
                rate_snapshot={"backend": "sqlite", "current_count": 1},
                side_effects_executed=False,
                ts_ms=NOW,
            ),
        )
        durable = store.resolve_committed_authority_by_audit(audit_ref=audit_ref)
        assert durable is not None
    finally:
        store.close()

    authority = {
        "allowed": True,
        "duplicate": False,
        "audit_ref": audit_ref,
        "claim_ref": claim.claim_ref,
        "request_id": REQUEST_ID,
        "principal_id": PRINCIPAL_ID,
        "tenant_id": TENANT_ID,
        "body_sha256": body_sha256,
        "action": TASK_GRAPH_CANCEL_ACTION,
        "required_scopes": TASK_GRAPH_CANCEL_REQUIRED_SCOPES,
    }
    return authority, durable


def _verify(db_path: Path, authority: dict[str, Any]) -> dict[str, Any]:
    store = SQLiteSecureIngressStore(db_path, rate_limit=100)
    try:
        verifier = SecureIngressGraphCancellationAuthorityVerifier(store)
        return dict(
            verifier.verify_graph_cancellation(
                authority=authority,
                source="OPERATOR",
                reason_code=REASON_CODE,
                graph_id=GRAPH_ID,
                graph_revision=GRAPH_REVISION,
            )
        )
    finally:
        store.close()


def test_canonical_task_graph_cancel_body_is_exact() -> None:
    assert canonical_task_graph_cancel_body(
        graph_id=f"  {GRAPH_ID}  ",
        graph_revision=GRAPH_REVISION,
        reason_code="operator_request",
    ) == (
        b'{"command_id":"task_graph.cancel","payload":{"graph_id":'
        b'"graph.ne01.operator-cancel","graph_revision":7,"reason_code":'
        b'"OPERATOR_REQUEST"},"schema_version":"natsume.obs-command.v1"}'
    )


@pytest.mark.parametrize(
    ("graph_id", "graph_revision", "reason_code"),
    (
        ("", GRAPH_REVISION, REASON_CODE),
        (GRAPH_ID, 0, REASON_CODE),
        (GRAPH_ID, True, REASON_CODE),
        (GRAPH_ID, GRAPH_REVISION, "not canonical punctuation!"),
    ),
)
def test_canonical_task_graph_cancel_body_rejects_invalid_input(
    graph_id: str,
    graph_revision: Any,
    reason_code: str,
) -> None:
    with pytest.raises(ValueError, match="TASK_GRAPH_CANCELLATION_COMMAND_INVALID"):
        canonical_task_graph_cancel_body(
            graph_id=graph_id,
            graph_revision=graph_revision,
            reason_code=reason_code,
        )


def test_sqlite_committed_claim_and_audit_resolve_and_verify_exactly(
    tmp_path: Path,
) -> None:
    db_path = _migrated_0058_db(tmp_path)
    authority, durable = _commit_authority(db_path)

    assert durable == {
        "audit_ref": authority["audit_ref"],
        "claim_ref": authority["claim_ref"],
        "request_id": REQUEST_ID,
        "surface": "obs.command",
        "action": TASK_GRAPH_CANCEL_ACTION,
        "method": "POST",
        "normalized_path": TASK_GRAPH_CANCEL_PATH,
        "principal_id": PRINCIPAL_ID,
        "tenant_id": TENANT_ID,
        "requested_scopes": tuple(sorted(TASK_GRAPH_CANCEL_REQUIRED_SCOPES)),
        "required_scopes": tuple(sorted(TASK_GRAPH_CANCEL_REQUIRED_SCOPES)),
        "body_sha256": authority["body_sha256"],
        "decision": "ALLOW",
        "reason_code": "SECURE_INGRESS_ALLOWED",
        "stage": "COMPLETE",
        "side_effects_executed": False,
        "audit_ts_ms": NOW,
        "claim_status": "COMMITTED",
        "claim_result_ref": None,
        "claim_expires_at_ms": NOW + 60_000,
    }
    assert _verify(db_path, authority) == {
        "request_id": REQUEST_ID,
        "request_identity": authority["audit_ref"],
        "principal_id": PRINCIPAL_ID,
        "source": "OPERATOR",
        "reason_code": REASON_CODE,
        "graph_id": GRAPH_ID,
        "graph_revision": GRAPH_REVISION,
    }


def test_settled_secure_ingress_claim_cannot_be_reused_as_bearer(
    tmp_path: Path,
) -> None:
    db_path = _migrated_0058_db(tmp_path)
    authority, _durable = _commit_authority(db_path)
    store = SQLiteSecureIngressStore(db_path, rate_limit=100)
    try:
        store.complete_claim(
            claim_ref=str(authority["claim_ref"]),
            result_ref="obs-command://result/task-graph/cancel/settled",
            at_ms=NOW + 1,
        )
    finally:
        store.close()

    with pytest.raises(
        ValueError,
        match="TASK_GRAPH_CANCELLATION_AUTHORITY_ALREADY_SETTLED",
    ):
        _verify(db_path, authority)


AuthorityMutation = Callable[[dict[str, Any]], None]


def _replace(field: str, value: Any) -> AuthorityMutation:
    def mutate(authority: dict[str, Any]) -> None:
        authority[field] = value

    return mutate


@pytest.mark.parametrize(
    ("drift", "mutate"),
    (
        ("claim", _replace("claim_ref", "secure-ingress-claim://drift")),
        ("audit", _replace("audit_ref", "secure-ingress-audit://drift")),
        ("request", _replace("request_id", "request.ne01.drift")),
        ("principal", _replace("principal_id", "service.obs.attacker")),
        ("body", _replace("body_sha256", "f" * 64)),
        ("action", _replace("action", "obs:command:other")),
        ("scope", _replace("required_scopes", ("obs:write",))),
    ),
)
def test_server_authority_drift_is_rejected(
    tmp_path: Path,
    drift: str,
    mutate: AuthorityMutation,
) -> None:
    del drift
    db_path = _migrated_0058_db(tmp_path)
    authority, _durable = _commit_authority(db_path)
    mutate(authority)

    with pytest.raises(
        ValueError,
        match=(
            "TASK_GRAPH_CANCELLATION_AUTHORITY_(?:NOT_DURABLE|BINDING_DRIFT)"
        ),
    ):
        _verify(db_path, authority)


@pytest.mark.parametrize(
    ("drift", "statement", "parameters"),
    (
        (
            "claim",
            "UPDATE secure_ingress_audit SET claim_ref = ? WHERE audit_ref = ?",
            ("secure-ingress-claim://drift",),
        ),
        (
            "request",
            "UPDATE secure_ingress_audit SET request_id = ? WHERE audit_ref = ?",
            ("request.ne01.drift",),
        ),
        (
            "principal",
            "UPDATE secure_ingress_audit SET principal_id = ? WHERE audit_ref = ?",
            ("service.obs.attacker",),
        ),
        (
            "body",
            "UPDATE secure_ingress_audit SET body_sha256 = ? WHERE audit_ref = ?",
            ("e" * 64,),
        ),
        (
            "action",
            "UPDATE secure_ingress_audit SET action = ? WHERE audit_ref = ?",
            ("obs:command:other",),
        ),
        (
            "scope",
            "UPDATE secure_ingress_audit SET required_scopes_json = ? WHERE audit_ref = ?",
            ('["obs:write"]',),
        ),
        (
            "path",
            "UPDATE secure_ingress_audit SET normalized_path = ? WHERE audit_ref = ?",
            ("/v1/host/obs-commands/other",),
        ),
        (
            "status",
            "UPDATE secure_ingress_idempotency_claim SET claim_status = ? WHERE claim_ref = ?",
            ("ABORTED",),
        ),
    ),
)
def test_durable_claim_or_audit_drift_is_rejected(
    tmp_path: Path,
    drift: str,
    statement: str,
    parameters: tuple[str, ...],
) -> None:
    del drift
    db_path = _migrated_0058_db(tmp_path)
    authority, _durable = _commit_authority(db_path)
    lookup_ref = (
        authority["claim_ref"]
        if "idempotency_claim" in statement
        else authority["audit_ref"]
    )
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(statement, (*parameters, lookup_ref))

    with pytest.raises(
        ValueError,
        match=(
            "TASK_GRAPH_CANCELLATION_AUTHORITY_(?:NOT_DURABLE|BINDING_DRIFT)"
        ),
    ):
        _verify(db_path, authority)
