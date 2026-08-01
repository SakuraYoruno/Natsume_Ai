from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import sqlite3

import pytest

from host_brain.persistence.gateway_transport_repository import (
    GATEWAY_TRANSPORT_MIGRATION_SHA256,
    GatewayTransportStoreError,
    InboxAcceptanceStatus,
    SQLiteGatewayTransportStore,
    GATEWAY_TRANSPORT_MAX_INBOX_BODY_BYTES,
    GATEWAY_TRANSPORT_MAX_OUTBOX_BODY_BYTES,
)


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "host_brain/audit/migrations/0059_gateway_transport.sql"
SECURE_INGRESS_MIGRATION = (
    ROOT / "host_brain/audit/migrations/0058_secure_ingress.sql"
)
RECONCILIATION_MIGRATION = (
    ROOT / "host_brain/audit/migrations/0151_gateway_inbox_reconciliation.sql"
)


class FrozenClock:
    def __init__(self, value: int = 1_900_000_000_000) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value

    def advance(self, delta_ms: int) -> None:
        self.value += int(delta_ms)


def _migrated_db(tmp_path: Path, *, migration_text: str | None = None) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "gateway-transport.sqlite3"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(
            migration_text
            if migration_text is not None
            else MIGRATION.read_text(encoding="utf-8")
        )
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
            ) VALUES ('0059#gateway_transport', 59, 'gateway_transport', ?, 'APPLIED', ?)
            """,
            (GATEWAY_TRANSPORT_MIGRATION_SHA256, 1_900_000_000_000),
        )
    return db_path


def _accept(
    store: SQLiteGatewayTransportStore,
    *,
    suffix: str = "1",
    raw_body: bytes = b'{"message":"hello"}',
    ordering_key: str = "qq:conversation-1",
    max_attempts: int = 3,
    safe_headers: dict[str, str] | None = None,
    tenant_id: str = "tenant.test",
    principal_id: str = "service.gateway.test",
):
    return store.accept_inbox(
        inbox_id=f"inbox-{suffix}",
        surface="host-input",
        platform="qq",
        principal_id=principal_id,
        tenant_id=tenant_id,
        secure_claim_ref=f"secure-ingress-claim://{suffix}",
        verification_audit_ref=f"secure-ingress-audit://{suffix}",
        platform_event_id=f"qq-event-{suffix}",
        idempotency_key=f"idem-{suffix}",
        raw_body=raw_body,
        safe_headers=(
            safe_headers
            if safe_headers is not None
            else {"content-type": "application/json", "x-edge-source": "test"}
        ),
        normalized_payload={"message": "hello", "suffix": suffix},
        ordering_key=ordering_key,
        source_sequence=int(suffix) if suffix.isdigit() else None,
        max_attempts=max_attempts,
    )


def _enqueue(store: SQLiteGatewayTransportStore, **kwargs: object):
    kwargs.setdefault("tenant_id", "tenant.test")
    kwargs.setdefault("principal_id", "service.gateway.test")
    return store.enqueue_outbox(**kwargs)


def _add_reconciliation_schema(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SECURE_INGRESS_MIGRATION.read_text(encoding="utf-8"))
        conn.executescript(RECONCILIATION_MIGRATION.read_text(encoding="utf-8"))


def _record_no_effect_denial(
    db_path: Path,
    *,
    inbox_id: str,
    audit_ref: str,
    reason_code: str = "SECURE_INGRESS_CREDENTIAL_INVALID",
    body_sha256: str | None = None,
    tenant_id: str = "tenant.test",
) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        inbox = conn.execute(
            "SELECT * FROM gateway_transport_inbox WHERE inbox_id = ?",
            (inbox_id,),
        ).fetchone()
        attempt = conn.execute(
            """
            SELECT * FROM gateway_transport_attempt
            WHERE direction = 'INBOX' AND message_id = ?
            ORDER BY replay_no DESC, attempt_no DESC LIMIT 1
            """,
            (inbox_id,),
        ).fetchone()
        assert inbox is not None and attempt is not None
        host_key = "edge-host:" + hashlib.sha256(inbox_id.encode()).hexdigest()
        conn.execute(
            """
            INSERT INTO secure_ingress_audit(
                audit_ref, request_id, correlation_id, surface, action, method,
                normalized_path, principal_id, tenant_id, key_id,
                credential_kind, credential_ref_digest, requested_scopes_json,
                required_scopes_json, body_sha256, idempotency_digest,
                nonce_digest, transport_key_digest, decision, reason_code,
                stage, claim_ref, rate_snapshot_json, side_effects_executed, ts_ms
            ) VALUES (?, ?, ?, 'host.input', 'host:input', 'POST',
                '/v1/host/input', 'service.edge.gateway', ?,
                'edge.gateway.hmac.v1', 'HMAC_SHA256', NULL,
                '["host:write"]', '["host:write"]', ?, ?, NULL, NULL,
                'DENY', ?, 'CREDENTIAL', NULL, NULL, 0, ?)
            """,
            (
                audit_ref,
                f"request:{audit_ref}",
                f"request:{audit_ref}",
                tenant_id,
                body_sha256
                or str(inbox["normalized_payload_digest"])[7:],
                "sha256:" + hashlib.sha256(host_key.encode()).hexdigest(),
                reason_code,
                int(attempt["completed_at_ms"]),
            ),
        )


def test_schema_is_catalog_checksum_bound_and_runtime_has_no_ddl(tmp_path: Path) -> None:
    migration_text = MIGRATION.read_text(encoding="utf-8")
    assert hashlib.sha256(migration_text.encode("utf-8")).hexdigest() == (
        GATEWAY_TRANSPORT_MIGRATION_SHA256
    )
    source = (
        ROOT / "host_brain/persistence/gateway_transport_repository.py"
    ).read_text(encoding="utf-8").upper()
    assert "CREATE TABLE" not in source
    assert "CREATE INDEX" not in source
    assert "ALTER TABLE" not in source

    db_path = _migrated_db(tmp_path / "good")
    SQLiteGatewayTransportStore(db_path).assert_schema_ready()

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE schema_migration_assets SET checksum = ? WHERE asset_key = '0059#gateway_transport'",
            ("0" * 64,),
        )
    with pytest.raises(GatewayTransportStoreError, match="MIGRATION_DRIFT"):
        SQLiteGatewayTransportStore(db_path)

    missing_catalog = tmp_path / "missing.sqlite3"
    with sqlite3.connect(str(missing_catalog)):
        pass
    with pytest.raises(GatewayTransportStoreError, match="MIGRATION_DRIFT"):
        SQLiteGatewayTransportStore(missing_catalog)

    index_drift = _migrated_db(tmp_path / "index-drift")
    with sqlite3.connect(str(index_drift)) as conn:
        conn.execute("DROP INDEX uq_gateway_transport_outbox_active_order")
        conn.execute(
            """
            CREATE INDEX uq_gateway_transport_outbox_active_order
            ON gateway_transport_outbox(ordering_key)
            WHERE status = 'CLAIMED'
            """
        )
    with pytest.raises(GatewayTransportStoreError, match="SCHEMA_DRIFT"):
        SQLiteGatewayTransportStore(index_drift)

    weakened_text = migration_text.replace(
        "AND substr(idempotency_digest, 8) NOT GLOB '*[^0-9a-f]*'",
        "AND substr(idempotency_digest, 8) GLOB '*'",
        1,
    )
    assert weakened_text != migration_text
    constraint_drift = _migrated_db(
        tmp_path / "constraint-drift",
        migration_text=weakened_text,
    )
    with pytest.raises(GatewayTransportStoreError, match="SCHEMA_DRIFT"):
        SQLiteGatewayTransportStore(constraint_drift)


def test_accept_inbox_is_digest_idempotent_and_conflicts_fail_closed(tmp_path: Path) -> None:
    clock = FrozenClock()
    store = SQLiteGatewayTransportStore(_migrated_db(tmp_path), clock=clock)

    first = _accept(store)
    duplicate = _accept(store)
    duplicate_with_new_transport_metadata = _accept(
        store,
        safe_headers={"content-type": "application/json", "x-edge-source": "retry"},
    )
    conflict = _accept(store, raw_body=b'{"message":"different"}')

    assert first.status is InboxAcceptanceStatus.NEW
    assert duplicate.status is InboxAcceptanceStatus.DUPLICATE_SAME
    assert duplicate.inbox_id == first.inbox_id
    assert duplicate.ack_ref == first.ack_ref
    assert duplicate_with_new_transport_metadata.status is InboxAcceptanceStatus.DUPLICATE_SAME
    assert conflict.status is InboxAcceptanceStatus.CONFLICT
    assert conflict.reason_code == "GATEWAY_TRANSPORT_IDEMPOTENCY_CONFLICT"

    record = store.get_inbox(inbox_id="inbox-1")
    assert record["status"] == record["state"] == "READY"
    assert record["raw_body_sha256"].startswith("sha256:")
    assert record["raw_body_size_bytes"] == len(b'{"message":"hello"}')
    assert record["safe_headers_sha256"].startswith("sha256:")
    assert "raw_body" not in record

    with pytest.raises(
        GatewayTransportStoreError,
        match="SECRET_HEADER_FORBIDDEN",
    ) as forbidden:
        store.accept_inbox(
            inbox_id="inbox-secret",
            surface="host-input",
            platform="qq",
            principal_id="service.gateway.test",
            tenant_id="tenant.test",
            idempotency_key="idem-secret",
            raw_body=b"secret-body-that-must-not-appear",
            safe_headers={"authorization": "secret-token-that-must-not-appear"},
        )
    rendered = str(forbidden.value)
    assert "secret-body" not in rendered
    assert "secret-token" not in rendered

    with pytest.raises(
        GatewayTransportStoreError,
        match="SECRET_HEADER_FORBIDDEN",
    ) as api_key:
        store.accept_inbox(
            inbox_id="inbox-api-key",
            surface="host-input",
            platform="qq",
            principal_id="service.gateway.test",
            tenant_id="tenant.test",
            idempotency_key="idem-api-key",
            raw_body=b"safe-body",
            safe_headers={"x-api-key": "api-key-that-must-not-appear"},
        )
    assert "api-key-that-must-not-appear" not in str(api_key.value)


def test_inbox_idempotency_and_platform_event_are_tenant_scoped(tmp_path: Path) -> None:
    store = SQLiteGatewayTransportStore(_migrated_db(tmp_path))
    common = {
        "surface": "host-input",
        "platform": "qq",
        "principal_id": "service.gateway.shared",
        "idempotency_key": "shared-platform-id",
        "raw_body": b'{"message":"same"}',
        "platform_event_id": "event-reused-by-installations",
        "ordering_key": "shared-order",
    }

    first = store.accept_inbox(inbox_id="tenant-a-inbox", tenant_id="tenant-a", **common)
    second = store.accept_inbox(inbox_id="tenant-b-inbox", tenant_id="tenant-b", **common)

    assert first.status is InboxAcceptanceStatus.NEW
    assert second.status is InboxAcceptanceStatus.NEW
    assert store.get_inbox(inbox_id="tenant-a-inbox")["tenant_id"] == "tenant-a"
    assert store.get_inbox(inbox_id="tenant-b-inbox")["tenant_id"] == "tenant-b"


def test_inbox_raw_body_is_hashed_then_discarded_without_plaintext_persistence(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    store = SQLiteGatewayTransportStore(db_path)
    sensitive_body = b"line-reply-token::do-not-store::private-user-message"

    accepted = store.accept_inbox(
        inbox_id="inbox-redacted",
        surface="host-input",
        platform="line",
        principal_id="service.gateway.line",
        tenant_id="tenant.line",
        idempotency_key="line-redacted",
        raw_body=sensitive_body,
        normalized_payload={"event_ref": "event://redacted"},
    )

    assert accepted.status is InboxAcceptanceStatus.NEW
    record = store.get_inbox(inbox_id="inbox-redacted")
    assert record["raw_body_size_bytes"] == len(sensitive_body)
    assert record["raw_body_sha256"] == "sha256:" + hashlib.sha256(
        sensitive_body
    ).hexdigest()
    assert "raw_body" not in record
    with sqlite3.connect(str(db_path)) as conn:
        columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(gateway_transport_inbox)")
        }
        assert "raw_body" not in columns
        persisted = conn.execute(
            """
            SELECT normalized_payload_json, safe_headers_json
            FROM gateway_transport_inbox
            WHERE inbox_id = ?
            """,
            ("inbox-redacted",),
        ).fetchone()
    assert persisted is not None
    assert sensitive_body not in str(persisted).encode("utf-8")
    assert sensitive_body not in db_path.read_bytes()


def test_same_ordering_key_is_isolated_by_tenant_and_authority(tmp_path: Path) -> None:
    store = SQLiteGatewayTransportStore(_migrated_db(tmp_path))
    _accept(
        store,
        suffix="tenant-a",
        tenant_id="tenant-a",
        principal_id="service.gateway.shared",
        ordering_key="shared-order",
    )
    _accept(
        store,
        suffix="tenant-b",
        tenant_id="tenant-b",
        principal_id="service.gateway.shared",
        ordering_key="shared-order",
    )

    first = store.claim_inbox(worker_id="worker-a")
    second = store.claim_inbox(worker_id="worker-b")

    assert first is not None and first["tenant_id"] == "tenant-a"
    assert second is not None and second["tenant_id"] == "tenant-b"
    assert first["ordering_key"] == second["ordering_key"] == "shared-order"


def test_outbox_idempotency_and_ordering_are_authority_scoped(tmp_path: Path) -> None:
    store = SQLiteGatewayTransportStore(_migrated_db(tmp_path))
    common = {
        "destination": "qq:shared",
        "principal_id": "service.gateway.shared",
        "idempotency_key": "shared-idempotency",
        "payload": b"same-payload",
        "ordering_key": "shared-order",
    }

    first = _enqueue(
        store,
        outbox_id="outbox-tenant-a",
        tenant_id="tenant-a",
        **common,
    )
    second = _enqueue(
        store,
        outbox_id="outbox-tenant-b",
        tenant_id="tenant-b",
        **common,
    )

    assert first["acceptance_status"] == second["acceptance_status"] == "NEW"
    assert first["tenant_id"] == "tenant-a"
    assert second["tenant_id"] == "tenant-b"
    first_claim = store.claim_outbox(worker_id="sender-a")
    second_claim = store.claim_outbox(worker_id="sender-b")
    assert first_claim is not None and first_claim["tenant_id"] == "tenant-a"
    assert second_claim is not None and second_claim["tenant_id"] == "tenant-b"


def test_transport_counts_are_one_payload_free_drain_snapshot(tmp_path: Path) -> None:
    store = SQLiteGatewayTransportStore(_migrated_db(tmp_path))
    assert store.transport_counts() == {
        "inbox_pending": 0,
        "outbox_pending": 0,
        "inbox_claimed": 0,
        "outbox_claimed": 0,
        "inbox_dlq": 0,
        "outbox_dlq": 0,
        "claimed": 0,
        "reconciliation_required": 0,
    }

    _accept(
        store,
        suffix="inbox-dlq",
        ordering_key="inbox-dlq-order",
        max_attempts=1,
    )
    inbox_dlq_claim = store.claim_inbox(worker_id="inbox-dlq-worker")
    assert inbox_dlq_claim is not None
    store.retry_inbox(
        inbox_id=inbox_dlq_claim["inbox_id"],
        claim_token=inbox_dlq_claim["claim_token"],
        fencing_token=inbox_dlq_claim["fencing_token"],
        reason_code="HOST_UNAVAILABLE",
    )
    _accept(store, suffix="inbox-claimed", ordering_key="inbox-claimed-order")
    inbox_claimed = store.claim_inbox(worker_id="inbox-claimed-worker")
    assert inbox_claimed is not None
    _accept(store, suffix="inbox-ready", ordering_key="inbox-ready-order")

    _enqueue(
        store,
        outbox_id="outbox-dlq",
        destination="qq:dlq",
        idempotency_key="outbox-dlq",
        payload=b"outbox-dlq-payload",
        max_attempts=1,
    )
    outbox_dlq_claim = store.claim_outbox(worker_id="outbox-dlq-worker")
    assert outbox_dlq_claim is not None
    store.retry_outbox(
        outbox_id=outbox_dlq_claim["outbox_id"],
        claim_token=outbox_dlq_claim["claim_token"],
        fencing_token=outbox_dlq_claim["fencing_token"],
        reason_code="PLATFORM_UNAVAILABLE",
    )
    _enqueue(
        store,
        outbox_id="outbox-claimed",
        destination="qq:claimed",
        idempotency_key="outbox-claimed",
        payload=b"outbox-claimed-payload",
    )
    outbox_claimed = store.claim_outbox(worker_id="outbox-claimed-worker")
    assert outbox_claimed is not None
    _enqueue(
        store,
        outbox_id="outbox-ready",
        destination="qq:ready",
        idempotency_key="outbox-ready",
        payload=b"outbox-ready-payload",
    )

    assert store.transport_counts() == {
        "inbox_pending": 2,
        "outbox_pending": 2,
        "inbox_claimed": 1,
        "outbox_claimed": 1,
        "inbox_dlq": 1,
        "outbox_dlq": 1,
        "claimed": 2,
        "reconciliation_required": 2,
    }


def test_claim_complete_and_outbox_enqueue_are_atomic(tmp_path: Path) -> None:
    clock = FrozenClock()
    store = SQLiteGatewayTransportStore(_migrated_db(tmp_path), clock=clock)
    _accept(store)

    claim = store.claim_inbox(worker_id="worker-a", lease_ms=500)
    assert claim is not None
    assert claim["status"] == "CLAIMED"
    assert "raw_body" not in claim
    assert claim["raw_body_size_bytes"] == len(b'{"message":"hello"}')
    assert claim["normalized_payload"]["message"] == "hello"
    assert claim["attempt_count"] == 1
    dispatch = store.mark_inbox_dispatch_started(
        inbox_id="inbox-1",
        claim_token=claim["claim_token"],
        fencing_token=claim["fencing_token"],
    )
    assert dispatch["dispatch_started_at_ms"] == clock.value

    completed = store.complete_inbox(
        inbox_id="inbox-1",
        claim_token=claim["claim_token"],
        fencing_token=claim["fencing_token"],
        receipt_ref="gateway-delivery-receipt://inbox-1",
        outbox_messages=(
            {
                "outbox_id": "outbox-1",
                "destination": "qq:conversation-1",
                "idempotency_key": "reply-inbox-1",
                "payload": {"reply": "world"},
                "ordering_key": "qq:conversation-1",
                "max_attempts": 2,
            },
        ),
    )
    assert completed["status"] == "DELIVERED"
    assert completed["outbox"][0]["acceptance_status"] == "NEW"
    persisted_outbox = store.get_outbox(outbox_id="outbox-1")
    assert persisted_outbox["status"] == "READY"
    assert persisted_outbox["tenant_id"] == "tenant.test"
    assert persisted_outbox["principal_id"] == "service.gateway.test"

    outbox_claim = store.claim_outbox(worker_id="sender-a", lease_ms=500)
    assert outbox_claim is not None
    assert outbox_claim["payload"] == b'{"reply":"world"}'
    store.mark_outbox_dispatch_started(
        outbox_id="outbox-1",
        claim_token=outbox_claim["claim_token"],
        fencing_token=outbox_claim["fencing_token"],
    )
    acked = store.ack_outbox(
        outbox_id="outbox-1",
        claim_token=outbox_claim["claim_token"],
        fencing_token=outbox_claim["fencing_token"],
        ack_ref="platform-ack://qq/1",
    )
    assert acked["status"] == "ACKED"
    assert (
        store.ack_outbox(
            outbox_id="outbox-1",
            claim_token=outbox_claim["claim_token"],
            fencing_token=outbox_claim["fencing_token"],
            ack_ref="platform-ack://qq/1",
        )["status"]
        == "ACKED"
    )

    _accept(store, suffix="2", ordering_key="qq:conversation-2")
    second = store.claim_inbox(worker_id="worker-b")
    assert second is not None and second["inbox_id"] == "inbox-2"
    with pytest.raises(GatewayTransportStoreError, match="PAYLOAD_INVALID"):
        store.complete_inbox(
            inbox_id="inbox-2",
            claim_token=second["claim_token"],
            fencing_token=second["fencing_token"],
            receipt_ref="gateway-delivery-receipt://inbox-2",
            outbox_messages=(
                {
                    "outbox_id": "outbox-invalid",
                    "destination": "qq:conversation-2",
                    "idempotency_key": "reply-inbox-2",
                    "payload": None,
                },
            ),
        )
    assert store.get_inbox(inbox_id="inbox-2")["status"] == "CLAIMED"
    with pytest.raises(GatewayTransportStoreError, match="MESSAGE_NOT_FOUND"):
        store.get_outbox(outbox_id="outbox-invalid")


def test_complete_inbox_derives_authority_and_rejects_conflicting_override(
    tmp_path: Path,
) -> None:
    store = SQLiteGatewayTransportStore(_migrated_db(tmp_path))
    _accept(store)
    claim = store.claim_inbox(worker_id="worker-authority")
    assert claim is not None

    with pytest.raises(GatewayTransportStoreError, match="AUTHORITY_CONFLICT"):
        store.complete_inbox(
            inbox_id="inbox-1",
            claim_token=claim["claim_token"],
            fencing_token=claim["fencing_token"],
            receipt_ref="gateway-delivery-receipt://authority-conflict",
            outbox_messages=(
                {
                    "outbox_id": "outbox-authority-conflict",
                    "tenant_id": "tenant.other",
                    "destination": "qq:authority",
                    "idempotency_key": "authority-conflict",
                    "payload": b"reply",
                },
            ),
        )

    assert store.get_inbox(inbox_id="inbox-1")["status"] == "CLAIMED"
    with pytest.raises(GatewayTransportStoreError, match="MESSAGE_NOT_FOUND"):
        store.get_outbox(outbox_id="outbox-authority-conflict")


@pytest.mark.parametrize(
    "overrides",
    (
        {"destination": "qq:other"},
        {"tenant_id": "tenant.other"},
        {"principal_id": "service.gateway.other"},
        {"idempotency_key": "immutable-other"},
        {"payload": b"other-payload"},
        {"origin_inbox_id": "missing-origin"},
        {"ordering_key": "other-order"},
        {"source_sequence": 2},
        {"max_attempts": 4},
    ),
)
def test_outbox_id_duplicate_requires_all_immutable_fields(
    tmp_path: Path,
    overrides: dict[str, object],
) -> None:
    store = SQLiteGatewayTransportStore(_migrated_db(tmp_path))
    original: dict[str, object] = {
        "outbox_id": "outbox-immutable",
        "destination": "qq:immutable",
        "idempotency_key": "immutable-idem",
        "payload": b"immutable-payload",
        "origin_inbox_id": None,
        "ordering_key": "immutable-order",
        "source_sequence": 1,
        "max_attempts": 3,
    }
    assert _enqueue(store, **original)["acceptance_status"] == "NEW"
    assert _enqueue(store, **original)["acceptance_status"] == "DUPLICATE_SAME"

    changed = {**original, **overrides}
    with pytest.raises(GatewayTransportStoreError, match="IDEMPOTENCY_CONFLICT"):
        _enqueue(store, **changed)
    assert store.get_outbox(outbox_id="outbox-immutable")["destination"] == "qq:immutable"


def test_retry_backoff_dlq_and_in_place_replay(tmp_path: Path) -> None:
    clock = FrozenClock()
    store = SQLiteGatewayTransportStore(_migrated_db(tmp_path), clock=clock)
    _enqueue(store,
        outbox_id="outbox-retry",
        destination="qq:retry",
        idempotency_key="outbox-retry",
        payload=b"frozen-payload",
        max_attempts=2,
    )
    _enqueue(store,
        outbox_id="outbox-after-retry",
        destination="qq:after-retry",
        idempotency_key="outbox-after-retry",
        payload=b"later-payload",
        ordering_key="qq:retry",
        source_sequence=2,
    )

    first = store.claim_outbox(worker_id="sender", lease_ms=100)
    assert first is not None
    retry = store.retry_outbox(
        outbox_id="outbox-retry",
        claim_token=first["claim_token"],
        fencing_token=first["fencing_token"],
        reason_code="PLATFORM_TIMEOUT",
        base_delay_ms=10,
        max_delay_ms=40,
    )
    assert retry["status"] == "RETRY_WAIT"
    assert retry["next_attempt_at_ms"] == clock.value + 10
    assert store.claim_outbox(worker_id="sender") is None

    clock.advance(10)
    second = store.claim_outbox(worker_id="sender", lease_ms=100)
    assert second is not None and second["attempt_count"] == 2
    dlq = store.retry_outbox(
        outbox_id="outbox-retry",
        claim_token=second["claim_token"],
        fencing_token=second["fencing_token"],
        reason_code="PLATFORM_TIMEOUT",
        base_delay_ms=10,
        max_delay_ms=40,
    )
    assert dlq["status"] == "DLQ"
    assert store.list_dlq(direction="OUTBOX")[0]["outbox_id"] == "outbox-retry"
    assert store.claim_outbox(worker_id="must-not-pass-dlq") is None

    replayed = store.replay_outbox(
        outbox_id="outbox-retry",
        operator_ref="operator://m48b/replay-1",
    )
    assert replayed["status"] == "READY"
    assert replayed["attempt_count"] == 0
    assert replayed["replay_count"] == 1
    assert replayed["last_replay_operator_ref"] == "operator://m48b/replay-1"
    replay_claim = store.claim_outbox(worker_id="sender", lease_ms=100)
    assert replay_claim is not None
    assert replay_claim["outbox_id"] == "outbox-retry"
    assert replay_claim["fencing_token"] > second["fencing_token"]
    store.mark_outbox_dispatch_started(
        outbox_id="outbox-retry",
        claim_token=replay_claim["claim_token"],
        fencing_token=replay_claim["fencing_token"],
    )
    store.ack_outbox(
        outbox_id="outbox-retry",
        claim_token=replay_claim["claim_token"],
        fencing_token=replay_claim["fencing_token"],
        ack_ref="platform-ack://qq/replayed",
    )
    next_claim = store.claim_outbox(worker_id="sender-next", lease_ms=100)
    assert next_claim is not None
    assert next_claim["outbox_id"] == "outbox-after-retry"


@pytest.mark.parametrize("direction", ("INBOX", "OUTBOX"))
def test_retry_after_dispatch_started_is_immediately_quarantined(
    tmp_path: Path,
    direction: str,
) -> None:
    clock = FrozenClock()
    store = SQLiteGatewayTransportStore(_migrated_db(tmp_path), clock=clock)
    if direction == "INBOX":
        _accept(store, max_attempts=3)
        claim = store.claim_inbox(worker_id="worker", lease_ms=100)
        assert claim is not None
        store.mark_inbox_dispatch_started(
            inbox_id="inbox-1",
            claim_token=claim["claim_token"],
            fencing_token=claim["fencing_token"],
        )
        result = store.retry_inbox(
            inbox_id="inbox-1",
            claim_token=claim["claim_token"],
            fencing_token=claim["fencing_token"],
            reason_code="HOST_TIMEOUT",
        )
        record_id = result["inbox_id"]
    else:
        _enqueue(store,
            outbox_id="outbox-uncertain",
            destination="qq:uncertain",
            idempotency_key="outbox-uncertain",
            payload=b"frozen-payload",
            max_attempts=3,
        )
        claim = store.claim_outbox(worker_id="sender", lease_ms=100)
        assert claim is not None
        store.mark_outbox_dispatch_started(
            outbox_id="outbox-uncertain",
            claim_token=claim["claim_token"],
            fencing_token=claim["fencing_token"],
        )
        result = store.retry_outbox(
            outbox_id="outbox-uncertain",
            claim_token=claim["claim_token"],
            fencing_token=claim["fencing_token"],
            reason_code="PLATFORM_TIMEOUT",
        )
        record_id = result["outbox_id"]

    assert result["status"] == "DLQ"
    assert result["last_reason_code"] == "GATEWAY_TRANSPORT_DELIVERY_UNCERTAIN"
    assert result["next_attempt_at_ms"] == clock.value
    assert store.list_dlq(direction=direction)[0][
        "inbox_id" if direction == "INBOX" else "outbox_id"
    ] == record_id
    with pytest.raises(
        GatewayTransportStoreError,
        match="REPLAY_REQUIRES_RECONCILIATION",
    ):
        if direction == "INBOX":
            store.replay_inbox(
                inbox_id=record_id,
                operator_ref="operator://must-reconcile",
            )
        else:
            store.replay_outbox(
                outbox_id=record_id,
                operator_ref="operator://must-reconcile",
            )


def test_uncertain_inbox_reopens_only_after_canonical_no_effect_denial(
    tmp_path: Path,
) -> None:
    clock = FrozenClock()
    db_path = _migrated_db(tmp_path)
    _add_reconciliation_schema(db_path)
    store = SQLiteGatewayTransportStore(db_path, clock=clock)
    _accept(store, max_attempts=3)
    claim = store.claim_inbox(worker_id="worker", lease_ms=100)
    assert claim is not None
    store.mark_inbox_dispatch_started(
        inbox_id="inbox-1",
        claim_token=claim["claim_token"],
        fencing_token=claim["fencing_token"],
    )
    uncertain = store.retry_inbox(
        inbox_id="inbox-1",
        claim_token=claim["claim_token"],
        fencing_token=claim["fencing_token"],
        reason_code="HOST_REJECTED",
    )
    assert uncertain["status"] == "DLQ"

    audit_ref = "secure-ingress-audit://credential-denied"
    _record_no_effect_denial(
        db_path,
        inbox_id="inbox-1",
        audit_ref=audit_ref,
    )
    reopened = store.reconcile_inbox_pre_effect_denial(
        inbox_id="inbox-1",
        secure_ingress_audit_ref=audit_ref,
        operator_ref="operator://dl07/local-acceptance",
    )

    assert reopened["status"] == "READY"
    assert reopened["attempt_count"] == 0
    assert reopened["replay_count"] == 1
    assert reopened["reconciliation_outcome"] == "NO_SIDE_EFFECT_SAFE_RETRY"
    assert reopened["reconciliation_ref"].startswith(
        "gateway-inbox-reconciliation://"
    )
    replay_claim = store.claim_inbox(worker_id="worker-after-reconcile")
    assert replay_claim is not None
    assert replay_claim["inbox_id"] == "inbox-1"
    store.mark_inbox_dispatch_started(
        inbox_id="inbox-1",
        claim_token=replay_claim["claim_token"],
        fencing_token=replay_claim["fencing_token"],
    )
    delivered = store.complete_inbox(
        inbox_id="inbox-1",
        claim_token=replay_claim["claim_token"],
        fencing_token=replay_claim["fencing_token"],
        receipt_ref="host-input-result://accepted",
    )
    assert delivered["status"] == "DELIVERED"

    exact_reopen = store.reconcile_inbox_pre_effect_denial(
        inbox_id="inbox-1",
        secure_ingress_audit_ref=audit_ref,
        operator_ref="operator://dl07/local-acceptance",
    )
    assert exact_reopen["status"] == "DELIVERED"
    assert exact_reopen["reconciliation_ref"] == reopened["reconciliation_ref"]
    assert exact_reopen["replay_count"] == 1

    with sqlite3.connect(str(db_path)) as conn:
        evidence_count = conn.execute(
            "SELECT COUNT(*) FROM gateway_transport_inbox_reconciliation"
        ).fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError, match="IMMUTABLE"):
            conn.execute(
                "UPDATE gateway_transport_inbox_reconciliation "
                "SET operator_ref = 'operator://tampered'"
            )
    assert evidence_count == 1


@pytest.mark.parametrize(
    ("override", "expected_reason"),
    (
        ({"body_sha256": "0" * 64}, "EVIDENCE_INVALID"),
        ({"tenant_id": "tenant.other"}, "EVIDENCE_INVALID"),
        (
            {"reason_code": "SECURE_INGRESS_IDEMPOTENCY_DUPLICATE"},
            "EVIDENCE_INVALID",
        ),
    ),
)
def test_uncertain_inbox_reconciliation_rejects_unbound_or_unsafe_evidence(
    tmp_path: Path,
    override: dict[str, str],
    expected_reason: str,
) -> None:
    clock = FrozenClock()
    db_path = _migrated_db(tmp_path)
    _add_reconciliation_schema(db_path)
    store = SQLiteGatewayTransportStore(db_path, clock=clock)
    _accept(store, max_attempts=3)
    claim = store.claim_inbox(worker_id="worker", lease_ms=100)
    assert claim is not None
    store.mark_inbox_dispatch_started(
        inbox_id="inbox-1",
        claim_token=claim["claim_token"],
        fencing_token=claim["fencing_token"],
    )
    store.retry_inbox(
        inbox_id="inbox-1",
        claim_token=claim["claim_token"],
        fencing_token=claim["fencing_token"],
        reason_code="HOST_REJECTED",
    )
    audit_ref = "secure-ingress-audit://unsafe"
    _record_no_effect_denial(
        db_path,
        inbox_id="inbox-1",
        audit_ref=audit_ref,
        **override,
    )

    with pytest.raises(GatewayTransportStoreError, match=expected_reason):
        store.reconcile_inbox_pre_effect_denial(
            inbox_id="inbox-1",
            secure_ingress_audit_ref=audit_ref,
            operator_ref="operator://dl07/local-acceptance",
        )
    assert store.get_inbox(inbox_id="inbox-1")["status"] == "DLQ"


def test_uncertain_inbox_reconciliation_requires_migration_0151(
    tmp_path: Path,
) -> None:
    clock = FrozenClock()
    store = SQLiteGatewayTransportStore(_migrated_db(tmp_path), clock=clock)
    _accept(store, max_attempts=3)
    claim = store.claim_inbox(worker_id="worker", lease_ms=100)
    assert claim is not None
    store.mark_inbox_dispatch_started(
        inbox_id="inbox-1",
        claim_token=claim["claim_token"],
        fencing_token=claim["fencing_token"],
    )
    store.retry_inbox(
        inbox_id="inbox-1",
        claim_token=claim["claim_token"],
        fencing_token=claim["fencing_token"],
        reason_code="HOST_REJECTED",
    )

    with pytest.raises(
        GatewayTransportStoreError,
        match="RECONCILIATION_SCHEMA_UNAVAILABLE",
    ):
        store.reconcile_inbox_pre_effect_denial(
            inbox_id="inbox-1",
            secure_ingress_audit_ref="secure-ingress-audit://missing-schema",
            operator_ref="operator://dl07/local-acceptance",
        )


def test_restart_recovers_expired_lease_and_rejects_stale_fence(tmp_path: Path) -> None:
    clock = FrozenClock()
    db_path = _migrated_db(tmp_path)
    first_store = SQLiteGatewayTransportStore(db_path, clock=clock)
    _accept(first_store, max_attempts=3)
    old_claim = first_store.claim_inbox(worker_id="worker-old", lease_ms=10)
    assert old_claim is not None

    clock.advance(11)
    restarted = SQLiteGatewayTransportStore(db_path, clock=clock)
    recovered = restarted.claim_inbox(worker_id="worker-new", lease_ms=100)
    assert recovered is not None
    assert recovered["inbox_id"] == "inbox-1"
    assert recovered["attempt_count"] == 2
    assert recovered["fencing_token"] > old_claim["fencing_token"]

    with pytest.raises(GatewayTransportStoreError, match="STALE_FENCE"):
        restarted.complete_inbox(
            inbox_id="inbox-1",
            claim_token=old_claim["claim_token"],
            fencing_token=old_claim["fencing_token"],
            receipt_ref="gateway-delivery-receipt://stale",
        )
    assert (
        restarted.complete_inbox(
            inbox_id="inbox-1",
            claim_token=recovered["claim_token"],
            fencing_token=recovered["fencing_token"],
            receipt_ref="gateway-delivery-receipt://fresh",
        )["status"]
        == "DELIVERED"
    )


def test_expired_started_dispatch_is_quarantined_as_delivery_uncertain(
    tmp_path: Path,
) -> None:
    clock = FrozenClock()
    db_path = _migrated_db(tmp_path)
    store = SQLiteGatewayTransportStore(db_path, clock=clock)
    _accept(store, max_attempts=3)
    claim = store.claim_inbox(worker_id="worker-dispatching", lease_ms=10)
    assert claim is not None

    first_mark = store.mark_inbox_dispatch_started(
        inbox_id="inbox-1",
        claim_token=claim["claim_token"],
        fencing_token=claim["fencing_token"],
    )
    repeated_mark = store.mark_inbox_dispatch_started(
        inbox_id="inbox-1",
        claim_token=claim["claim_token"],
        fencing_token=claim["fencing_token"],
    )
    assert repeated_mark["dispatch_started_at_ms"] == first_mark["dispatch_started_at_ms"]

    clock.advance(11)
    restarted = SQLiteGatewayTransportStore(db_path, clock=clock)
    assert restarted.claim_inbox(worker_id="must-not-redeliver") is None
    quarantined = restarted.get_inbox(inbox_id="inbox-1")
    assert quarantined["status"] == "DLQ"
    assert quarantined["last_reason_code"] == "GATEWAY_TRANSPORT_DELIVERY_UNCERTAIN"

    with sqlite3.connect(str(db_path)) as conn:
        attempt = conn.execute(
            """
            SELECT outcome, dispatch_started_at_ms, completed_at_ms
            FROM gateway_transport_attempt
            WHERE claim_token = ?
            """,
            (claim["claim_token"],),
        ).fetchone()
    assert attempt == ("DLQ", first_mark["dispatch_started_at_ms"], clock.value)

    with pytest.raises(GatewayTransportStoreError, match="STALE_FENCE"):
        restarted.complete_inbox(
            inbox_id="inbox-1",
            claim_token=claim["claim_token"],
            fencing_token=claim["fencing_token"],
            receipt_ref="gateway-delivery-receipt://late",
        )


def test_claim_is_single_winner_and_ordering_key_is_serial(tmp_path: Path) -> None:
    clock = FrozenClock()
    store = SQLiteGatewayTransportStore(_migrated_db(tmp_path), clock=clock)
    _accept(store, suffix="1", ordering_key="same-order")
    _accept(store, suffix="2", ordering_key="same-order")

    def claim(index: int):
        return store.claim_inbox(worker_id=f"worker-{index}", lease_ms=1000)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(claim, range(8)))
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert winners[0]["inbox_id"] == "inbox-1"
    assert store.claim_inbox(worker_id="blocked-by-order") is None

    store.complete_inbox(
        inbox_id="inbox-1",
        claim_token=winners[0]["claim_token"],
        fencing_token=winners[0]["fencing_token"],
        receipt_ref="gateway-delivery-receipt://ordered-1",
    )
    second = store.claim_inbox(worker_id="worker-next")
    assert second is not None and second["inbox_id"] == "inbox-2"


def test_claim_orders_the_available_set_by_source_sequence(tmp_path: Path) -> None:
    store = SQLiteGatewayTransportStore(_migrated_db(tmp_path))
    _accept(store, suffix="2", ordering_key="same-order")
    _accept(store, suffix="1", ordering_key="same-order")

    first = store.claim_inbox(worker_id="sequence-worker")

    assert first is not None
    assert first["inbox_id"] == "inbox-1"
    assert first["source_sequence"] == 1


def test_payload_tamper_is_moved_to_dlq_before_delivery(tmp_path: Path) -> None:
    clock = FrozenClock()
    db_path = _migrated_db(tmp_path)
    store = SQLiteGatewayTransportStore(db_path, clock=clock)
    _enqueue(store,
        outbox_id="outbox-tampered",
        destination="qq:tampered",
        idempotency_key="outbox-tampered",
        payload=b"original-body-that-must-not-leak",
    )
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE gateway_transport_outbox SET payload_body = ? WHERE outbox_id = ?",
            (b"tampered-body-that-must-not-leak", "outbox-tampered"),
        )

    with pytest.raises(GatewayTransportStoreError, match="PAYLOAD_DIGEST_MISMATCH") as exc:
        store.claim_outbox(worker_id="sender")
    rendered = str(exc.value)
    assert "original-body" not in rendered
    assert "tampered-body" not in rendered
    assert store.list_dlq(direction="OUTBOX")[0]["outbox_id"] == "outbox-tampered"


def test_safe_headers_are_allowlisted_size_bounded_and_digest_bound(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    store = SQLiteGatewayTransportStore(db_path)
    oversized_headers = {
        "content-length": "1" * 900,
        "content-type": "a" * 900,
        "user-agent": "b" * 900,
        "x-correlation-id": "c" * 900,
        "x-request-id": "d" * 900,
    }
    with pytest.raises(GatewayTransportStoreError, match="SAFE_HEADERS_TOO_LARGE"):
        store.accept_inbox(
            inbox_id="headers-too-large",
            surface="host-input",
            platform="qq",
            principal_id="service.gateway.test",
            tenant_id="tenant.test",
            idempotency_key="headers-too-large",
            raw_body=b"body",
            safe_headers=oversized_headers,
        )

    _accept(store)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            UPDATE gateway_transport_inbox
            SET safe_headers_json = ?
            WHERE inbox_id = 'inbox-1'
            """,
            ('{"content-type":"text/plain"}',),
        )

    with pytest.raises(GatewayTransportStoreError, match="PAYLOAD_DIGEST_MISMATCH"):
        store.claim_inbox(worker_id="must-not-receive-tampered-headers")
    dlq = store.list_dlq(direction="INBOX")
    assert dlq[0]["inbox_id"] == "inbox-1"
    assert dlq[0]["last_reason_code"] == "GATEWAY_TRANSPORT_PAYLOAD_DIGEST_MISMATCH"


def test_inbox_and_outbox_payload_hard_limits(tmp_path: Path) -> None:
    store = SQLiteGatewayTransportStore(_migrated_db(tmp_path))

    with pytest.raises(GatewayTransportStoreError, match="PAYLOAD_TOO_LARGE"):
        _accept(store, raw_body=b"x" * (GATEWAY_TRANSPORT_MAX_INBOX_BODY_BYTES + 1))
    with pytest.raises(GatewayTransportStoreError, match="PAYLOAD_TOO_LARGE"):
        store.accept_inbox(
            inbox_id="normalized-too-large",
            surface="host-input",
            platform="qq",
            principal_id="service.gateway.test",
            tenant_id="tenant.test",
            idempotency_key="normalized-too-large",
            raw_body=b"body",
            normalized_payload={"body": "x" * GATEWAY_TRANSPORT_MAX_INBOX_BODY_BYTES},
        )
    with pytest.raises(GatewayTransportStoreError, match="PAYLOAD_TOO_LARGE"):
        _enqueue(store,
            outbox_id="outbox-too-large",
            destination="qq:too-large",
            idempotency_key="outbox-too-large",
            payload=b"x" * (GATEWAY_TRANSPORT_MAX_OUTBOX_BODY_BYTES + 1),
        )
