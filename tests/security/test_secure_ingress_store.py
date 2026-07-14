from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import sqlite3

import pytest

from host_brain.persistence.secure_ingress_repository import (
    SECURE_INGRESS_MIGRATION_SHA256,
    SQLiteSecureIngressStore,
    SecureIngressStoreError,
)
from host_brain.secure_ingress import (
    ClaimStatus,
    IngressAuditEvent,
)


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "host_brain/audit/migrations/0058_secure_ingress.sql"
NOW = 1_800_000_000_000


def _migrated_db(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "secure-ingress.sqlite3"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(MIGRATION.read_text(encoding="utf-8"))
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
                asset_key, migration_id, name, checksum, disposition, applied_ts_ms
            ) VALUES ('0058#secure_ingress', 58, 'secure_ingress', ?, 'APPLIED', ?)
            """,
            (SECURE_INGRESS_MIGRATION_SHA256, NOW),
        )
    return db_path


def _seed_authority(db_path: Path, *, status: str = "ACTIVE") -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO secure_ingress_principal(
                principal_id, principal_kind, tenant_id, status, version,
                granted_scopes_json, allowed_audiences_json, allowed_actions_json,
                valid_from_ms, valid_until_ms, updated_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "service.gateway.test",
                "service",
                "tenant.test",
                status,
                2,
                '["host:write"]',
                '["host.test"]',
                '["host:*"]',
                NOW - 1000,
                NOW + 100_000,
                NOW,
            ),
        )
        conn.execute(
            """
            INSERT INTO secure_ingress_credential(
                principal_id, key_id, credential_kind, algorithm, secret_ref,
                status, version, valid_from_ms, valid_until_ms, updated_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "service.gateway.test",
                "key.test.1",
                "hmac-sha256",
                "hmac-sha256",
                "secret-ref://test-only/key.test.1",
                "ACTIVE",
                3,
                NOW - 1000,
                NOW + 100_000,
                NOW,
            ),
        )


def _claim(store: SQLiteSecureIngressStore, **overrides: object):
    values: dict[str, object] = {
        "principal_id": "service.gateway.test",
        "surface": "host.input",
        "action": "host:input",
        "required_scopes": ("host:write",),
        "idempotency_key": "idem.test.1",
        "nonce": "nonce.test.1",
        "body_sha256": "a" * 64,
        "request_id": "request.test.1",
        "at_ms": NOW,
        "expires_at_ms": NOW + 60_000,
    }
    values.update(overrides)
    return store.try_claim(**values)


def _audit_event() -> IngressAuditEvent:
    return IngressAuditEvent(
        request_id="request.test.1",
        correlation_id="correlation.test.1",
        surface="host.input",
        action="host:input",
        method="POST",
        normalized_path="/v1/host/input",
        principal_id="service.gateway.test",
        tenant_id="tenant.test",
        key_id="key.test.1",
        credential_kind="hmac-sha256",
        credential_ref_digest="sha256:" + "b" * 64,
        requested_scopes=("host:write",),
        required_scopes=("host:write",),
        body_sha256="a" * 64,
        idempotency_digest="sha256:" + "c" * 64,
        nonce_digest="sha256:" + "d" * 64,
        transport_key_digest="sha256:" + "e" * 64,
        decision="ALLOW",
        reason_code="SECURE_INGRESS_ALLOWED",
        stage="COMPLETE",
        claim_ref="secure-ingress-claim://test",
        rate_snapshot={"backend": "sqlite", "current_count": 1},
        side_effects_executed=False,
        ts_ms=NOW,
    )


def test_store_requires_catalog_owned_schema_and_never_creates_db(tmp_path: Path) -> None:
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(SecureIngressStoreError, match="SCHEMA_UNAVAILABLE"):
        SQLiteSecureIngressStore(missing)
    assert not missing.exists()

    empty = tmp_path / "empty.sqlite3"
    empty.touch()
    with pytest.raises(SecureIngressStoreError, match="SCHEMA_UNAVAILABLE"):
        SQLiteSecureIngressStore(empty)


def test_principal_resolution_is_status_version_and_time_bound(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    _seed_authority(db_path)
    store = SQLiteSecureIngressStore(db_path)
    try:
        authority = store.resolve(
            principal_id="service.gateway.test",
            key_id="key.test.1",
            at_ms=NOW,
        )
        assert authority is not None
        assert authority.principal_id == "service.gateway.test"
        assert authority.version == 2
        assert authority.granted_scopes == ("host:write",)
        assert store.resolve(
            principal_id="service.gateway.test",
            key_id="key.test.1",
            at_ms=NOW + 100_000,
        ) is None
        assert store.resolve(
            principal_id="unknown",
            key_id="key.test.1",
            at_ms=NOW,
        ) is None
    finally:
        store.close()


def test_rate_limit_is_durable_and_partitioned_by_principal_action_scope(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    first = SQLiteSecureIngressStore(db_path, rate_limit=2, rate_window_ms=1000)
    assert first.consume(
        principal_id="p1", action="host:input", required_scopes=("host:write",), at_ms=NOW
    ).allowed
    assert first.consume(
        principal_id="p1", action="host:input", required_scopes=("host:write",), at_ms=NOW + 1
    ).allowed
    first.close()

    restarted = SQLiteSecureIngressStore(db_path, rate_limit=2, rate_window_ms=1000)
    try:
        limited = restarted.consume(
            principal_id="p1", action="host:input", required_scopes=("host:write",), at_ms=NOW + 2
        )
        isolated = restarted.consume(
            principal_id="p2", action="host:input", required_scopes=("host:write",), at_ms=NOW + 2
        )
        expired = restarted.consume(
            principal_id="p1", action="host:input", required_scopes=("host:write",), at_ms=NOW + 1001
        )
        assert limited.allowed is False and limited.current_count == 3
        assert isolated.allowed is True and isolated.current_count == 1
        assert expired.allowed is True and expired.current_count == 2
    finally:
        restarted.close()


def test_attempt_rate_is_durable_and_uses_hashed_transport_identity(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    store = SQLiteSecureIngressStore(
        db_path,
        attempt_rate_limit=2,
        attempt_window_ms=1000,
    )
    try:
        first = store.consume_attempt(
            transport_key="loopback:attempt-test",
            claimed_principal_id="claimed.principal",
            key_id="claimed.key",
            at_ms=NOW,
        )
        second = store.consume_attempt(
            transport_key="loopback:attempt-test",
            claimed_principal_id="claimed.principal",
            key_id="claimed.key",
            at_ms=NOW + 1,
        )
        limited = store.consume_attempt(
            transport_key="loopback:attempt-test",
            claimed_principal_id="claimed.principal",
            key_id="claimed.key",
            at_ms=NOW + 2,
        )
        assert first.allowed and second.allowed and not limited.allowed
        assert limited.lane == "ATTEMPT"
        with sqlite3.connect(str(db_path)) as conn:
            rendered = repr(conn.execute(
                "SELECT principal_id, rate_key_digest FROM secure_ingress_rate_event"
            ).fetchall())
        assert "loopback:attempt-test" not in rendered
        assert "claimed.principal" not in rendered
        assert "claimed.key" not in rendered
    finally:
        store.close()


def test_claim_is_restart_safe_for_duplicate_conflict_and_replay(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    first_store = SQLiteSecureIngressStore(db_path)
    first = _claim(first_store)
    first_audit_ref = first_store.commit_with_audit(
        claim_ref=first.claim_ref or "",
        event=replace(_audit_event(), claim_ref=first.claim_ref),
    )
    first_store.complete_claim(claim_ref=first.claim_ref or "", result_ref="result://test/1")
    first_store.close()

    restarted = SQLiteSecureIngressStore(db_path)
    try:
        duplicate = _claim(restarted, nonce="nonce.test.2", request_id="request.test.2")
        conflict = _claim(
            restarted,
            nonce="nonce.test.3",
            body_sha256="e" * 64,
            request_id="request.test.3",
        )
        replay = _claim(
            restarted,
            idempotency_key="idem.test.2",
            nonce="nonce.test.1",
            request_id="request.test.4",
        )
        assert first.status is ClaimStatus.NEW
        assert duplicate.status is ClaimStatus.DUPLICATE_SAME
        assert duplicate.existing_result_ref == "result://test/1"
        assert duplicate.canonical_request_id == "request.test.1"
        assert duplicate.canonical_body_sha256 == "a" * 64
        assert duplicate.canonical_audit_ref == first_audit_ref
        assert conflict.status is ClaimStatus.IDEMPOTENCY_CONFLICT
        assert replay.status is ClaimStatus.REPLAY
    finally:
        restarted.close()


def test_committed_claim_remains_canonical_after_authentication_ttl(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    store = SQLiteSecureIngressStore(db_path)
    try:
        first = _claim(store, expires_at_ms=NOW + 10)
        audit_ref = store.commit_with_audit(
            claim_ref=first.claim_ref or "",
            event=replace(_audit_event(), claim_ref=first.claim_ref),
        )

        duplicate = _claim(
            store,
            nonce="nonce.test.after-expiry",
            request_id="request.test.after-expiry",
            at_ms=NOW + 11,
            expires_at_ms=NOW + 60_000,
        )

        assert duplicate.status is ClaimStatus.DUPLICATE_SAME
        assert duplicate.claim_ref == first.claim_ref
        assert duplicate.canonical_request_id == "request.test.1"
        assert duplicate.canonical_audit_ref == audit_ref
    finally:
        store.close()


def test_concurrent_claim_has_exactly_one_winner(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)

    def attempt(_index: int) -> ClaimStatus:
        store = SQLiteSecureIngressStore(db_path)
        try:
            return _claim(store).status
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=12) as pool:
        statuses = list(pool.map(attempt, range(24)))

    assert statuses.count(ClaimStatus.NEW) == 1
    assert statuses.count(ClaimStatus.DUPLICATE_SAME) == 23


def test_claim_and_allow_audit_commit_atomically_or_can_be_reclaimed(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    store = SQLiteSecureIngressStore(db_path)
    try:
        first = _claim(store)
        with pytest.raises(sqlite3.IntegrityError):
            store.commit_with_audit(
                claim_ref=first.claim_ref or "",
                event=replace(
                    _audit_event(),
                    claim_ref=first.claim_ref,
                    surface=None,  # type: ignore[arg-type]
                ),
            )
        with sqlite3.connect(str(db_path)) as conn:
            status = conn.execute(
                "SELECT claim_status FROM secure_ingress_idempotency_claim WHERE claim_ref = ?",
                (first.claim_ref,),
            ).fetchone()
            assert status == ("PROVISIONAL",)
            assert conn.execute("SELECT COUNT(*) FROM secure_ingress_audit").fetchone() == (0,)

        store.abort_claim(claim_ref=first.claim_ref or "")
        retried = _claim(store)
        assert retried.status is ClaimStatus.NEW
        assert retried.claim_ref != first.claim_ref
    finally:
        store.close()


def test_unaborted_provisional_claim_expires_on_short_lease(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    store = SQLiteSecureIngressStore(db_path, provisional_ttl_ms=10)
    try:
        first = _claim(store)
        retried = _claim(
            store,
            at_ms=NOW + 11,
            expires_at_ms=NOW + 60_000,
        )
        assert first.status is ClaimStatus.NEW
        assert retried.status is ClaimStatus.NEW
        assert retried.claim_ref != first.claim_ref
    finally:
        store.close()


def test_audit_persists_only_sanitized_event_fields(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    store = SQLiteSecureIngressStore(db_path)
    try:
        audit_ref = store.append(_audit_event())
        rows = store.read_audit(request_id="request.test.1")
        assert len(rows) == 1
        assert rows[0]["audit_ref"] == audit_ref
        assert rows[0]["side_effects_executed"] == 0
        assert json.loads(rows[0]["requested_scopes_json"]) == ["host:write"]
        assert json.loads(rows[0]["rate_snapshot_json"])["backend"] == "sqlite"
        rendered = repr(rows[0])
        assert "secret-ref://" not in rendered
        assert "raw_proof" not in rendered
        assert "message" not in rendered
    finally:
        store.close()


def test_primary_audit_sink_rejects_secret_marker_in_any_external_field(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    store = SQLiteSecureIngressStore(db_path)
    marker = "access_token=must-never-persist"
    unsafe = replace(
        _audit_event(),
        request_id=marker,
        correlation_id=marker,
        surface=marker,
        action=marker,
        normalized_path=f"/{marker}",
        principal_id=marker,
        tenant_id=marker,
        key_id=marker,
    )
    try:
        with pytest.raises(ValueError, match="SECRET_MATERIAL_FORBIDDEN"):
            store.append(unsafe)
        with sqlite3.connect(str(db_path)) as conn:
            assert conn.execute("SELECT COUNT(*) FROM secure_ingress_audit").fetchone() == (0,)
            assert marker not in repr(conn.execute(
                "SELECT * FROM secure_ingress_audit"
            ).fetchall())
    finally:
        store.close()


def test_runtime_store_source_contains_no_schema_ddl() -> None:
    source = (ROOT / "host_brain/persistence/secure_ingress_repository.py").read_text(encoding="utf-8").upper()
    assert "CREATE TABLE" not in source
    assert "CREATE INDEX" not in source


def test_store_rejects_migration_checksum_and_constraint_drift(tmp_path: Path) -> None:
    checksum_db = _migrated_db(tmp_path / "checksum")
    with sqlite3.connect(str(checksum_db)) as conn:
        conn.execute(
            "UPDATE schema_migration_assets SET checksum = ? WHERE asset_key = '0058#secure_ingress'",
            ("0" * 64,),
        )
    with pytest.raises(SecureIngressStoreError, match="MIGRATION_DRIFT"):
        SQLiteSecureIngressStore(checksum_db)

    constraint_db = _migrated_db(tmp_path / "constraint")
    with sqlite3.connect(str(constraint_db)) as conn:
        conn.executescript(
            """
            ALTER TABLE secure_ingress_audit RENAME TO secure_ingress_audit_old;
            CREATE TABLE secure_ingress_audit AS SELECT * FROM secure_ingress_audit_old WHERE 0;
            DROP TABLE secure_ingress_audit_old;
            """
        )
    with pytest.raises(SecureIngressStoreError, match="SCHEMA_DRIFT"):
        SQLiteSecureIngressStore(constraint_db)
