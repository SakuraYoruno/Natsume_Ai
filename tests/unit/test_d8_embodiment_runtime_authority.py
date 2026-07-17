from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.embodiment_outbox_repository import (
    EmbodimentOutboxError,
    EmbodimentOutboxRepository,
    EmbodimentRuntimeAuthority,
)


ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "host_brain" / "audit" / "migrations"


def _repository(
    tmp_path: Path,
    name: str,
    *,
    runtime_scope_id: str | None = None,
) -> tuple[EmbodimentOutboxRepository, Path]:
    db_path = tmp_path / f"{name}.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id=f"d8-runtime-authority-{name}",
    )
    assert migrated.success, migrated.as_dict()
    return EmbodimentOutboxRepository(db_path, runtime_scope_id=runtime_scope_id), db_path


def _command(name: str) -> dict[str, object]:
    return {
        "command_id": f"d8-authority-command-{name}",
        "idempotency_key": f"d8-authority-key-{name}",
        "kind": "mateengine",
        "action": "command",
        "payload": {"command": {"type": "status"}},
    }


def _authority(
    repository: EmbodimentOutboxRepository,
    *,
    worker_id: str,
) -> EmbodimentRuntimeAuthority:
    lease = repository.acquire_runtime_lease(worker_id=worker_id, lease_ms=60_000)
    assert lease is not None
    return EmbodimentRuntimeAuthority(
        worker_id=worker_id,
        lease_id=str(lease["lease_id"]),
        fencing_token=int(lease["fencing_token"]),
    )


def _receipt(command: dict[str, object], *, status: str = "IN_DOUBT") -> dict[str, object]:
    return {
        "command_id": str(command["command_id"]),
        "idempotency_key": str(command["idempotency_key"]),
        "accepted": status in {"APPLIED", "COMPLETED", "SUCCEEDED"},
        "status": status,
        "reason_code": "D8_TEST",
        "detail": "test receipt",
    }


def test_0104_upgrades_a_0103_database_without_backfilling_unscoped_rows(tmp_path) -> None:
    legacy_dir = tmp_path / "migrations-0103"
    legacy_dir.mkdir()
    for asset in MIGRATIONS.glob("*.sql"):
        if int(asset.name[:4]) <= 103:
            shutil.copy2(asset, legacy_dir / asset.name)

    db_path = tmp_path / "upgrade-from-0103.sqlite3"
    before = SQLiteMigrationRunner(
        db_path=db_path,
        migrations_dir=legacy_dir,
    ).apply_migrations(request_id="d8-before-0104")
    assert before.success, before.as_dict()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO host_embodiment_outbox "
            "(outbox_id, command_id, idempotency_key, command_digest, command_json, "
            "status, created_at_ms, updated_at_ms) VALUES (?, ?, ?, ?, ?, 'PENDING', 1, 1)",
            (
                "legacy-unscoped-row",
                "legacy-command",
                "legacy-key",
                "sha256:" + "0" * 64,
                json.dumps({"legacy": True}),
            ),
        )
        connection.commit()

    after = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="d8-after-0104",
    )
    assert after.success, after.as_dict()
    with sqlite3.connect(db_path) as connection:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(host_embodiment_outbox)").fetchall()
        }
        legacy_scope = connection.execute(
            "SELECT runtime_scope_id FROM host_embodiment_outbox WHERE outbox_id = ?",
            ("legacy-unscoped-row",),
        ).fetchone()[0]
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert "runtime_scope_id" in columns
    assert legacy_scope is None
    assert {
        "host_embodiment_runtime_lease",
        "host_embodiment_edge_session_lease",
    }.issubset(tables)


def test_scope_fences_new_rows_and_legacy_unscoped_rows_fail_closed(tmp_path) -> None:
    repository, db_path = _repository(tmp_path, "scope", runtime_scope_id="scope-a")
    other = EmbodimentOutboxRepository(db_path, runtime_scope_id="scope-b")
    authority = _authority(repository, worker_id="scope-a-worker")
    other_authority = _authority(other, worker_id="scope-b-worker")
    legacy_payload = json.dumps({"legacy": True}, sort_keys=True, separators=(",", ":"))
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO host_embodiment_outbox "
            "(outbox_id, command_id, idempotency_key, command_digest, command_json, "
            "status, created_at_ms, updated_at_ms) VALUES (?, ?, ?, ?, ?, 'PENDING', 1, 1)",
            (
                "legacy-unscoped-row",
                "legacy-command",
                "legacy-key",
                "sha256:" + hashlib.sha256(legacy_payload.encode("utf-8")).hexdigest(),
                legacy_payload,
            ),
        )
        connection.commit()

    assert repository.claim_next(worker_id="scope-a-worker", authority=authority) is None
    assert repository.get("legacy-unscoped-row") is None
    row = repository.enqueue(_command("scope"), authority=authority)
    assert row["runtime_scope_id"] == "scope-a"
    assert other.get(str(row["outbox_id"])) is None
    assert other.claim_next(worker_id="scope-b-worker", authority=other_authority) is None
    with pytest.raises(EmbodimentOutboxError, match="EMBODIMENT_OUTBOX_SCOPE_CONFLICT"):
        other.enqueue(_command("scope"), authority=other_authority)


def test_runtime_lease_is_exclusive_and_old_fences_cannot_renew_or_release(tmp_path) -> None:
    first, db_path = _repository(tmp_path, "runtime-lease", runtime_scope_id="shared")
    second = EmbodimentOutboxRepository(db_path, runtime_scope_id="shared")

    acquired = first.acquire_runtime_lease(
        worker_id="app-a", lease_ms=10_000, now_ms=1_000
    )
    assert acquired is not None
    assert acquired["fencing_token"] == 1
    assert second.acquire_runtime_lease(
        worker_id="app-b", lease_ms=10_000, now_ms=1_001
    ) is None
    assert first.acquire_runtime_lease(
        worker_id="app-a", lease_ms=10_000, now_ms=1_001
    ) is None
    assert first.renew_runtime_lease(
        worker_id="app-a",
        lease_id=str(acquired["lease_id"]),
        fencing_token=1,
        lease_ms=10_000,
        now_ms=2_000,
    ) is not None
    assert not first.release_runtime_lease(
        worker_id="app-a",
        lease_id=str(acquired["lease_id"]),
        fencing_token=2,
        now_ms=2_001,
    )
    assert first.release_runtime_lease(
        worker_id="app-a",
        lease_id=str(acquired["lease_id"]),
        fencing_token=1,
        now_ms=2_001,
    )
    replacement = second.acquire_runtime_lease(
        worker_id="app-b", lease_ms=10_000, now_ms=2_002
    )
    assert replacement is not None
    assert replacement["fencing_token"] == 2
    assert first.renew_runtime_lease(
        worker_id="app-a",
        lease_id=str(acquired["lease_id"]),
        fencing_token=1,
        now_ms=2_003,
    ) is None


def test_edge_session_lease_is_runtime_fenced_and_reloadable_after_crash(tmp_path) -> None:
    repository, db_path = _repository(tmp_path, "edge-lease", runtime_scope_id="shared")
    runtime = repository.acquire_runtime_lease(
        worker_id="app-a", lease_ms=10_000, now_ms=1_000
    )
    assert runtime is not None
    persisted = repository.persist_edge_session_lease(
        session_id="edge-session",
        owner_id="edge-owner",
        lease_id="edge-lease-7",
        fencing_token=7,
        issued_at_ms=1_100,
        expires_at_ms=9_000,
        expected_runtime_lease_id=str(runtime["lease_id"]),
        expected_runtime_fencing_token=int(runtime["fencing_token"]),
        now_ms=1_200,
    )
    assert persisted is not None
    restarted = EmbodimentOutboxRepository(db_path, runtime_scope_id="shared")
    loaded = restarted.load_edge_session_lease("edge-session", "edge-owner")
    assert loaded is not None
    assert loaded["lease_id"] == "edge-lease-7"
    assert loaded["fencing_token"] == 7
    assert restarted.persist_edge_session_lease(
        session_id="edge-session",
        owner_id="edge-owner",
        lease_id="edge-lease-8",
        fencing_token=8,
        issued_at_ms=1_200,
        expires_at_ms=9_500,
        expected_runtime_lease_id=str(runtime["lease_id"]),
        expected_runtime_fencing_token=2,
        now_ms=1_300,
    ) is None
    assert not restarted.release_edge_session_lease(
        session_id="edge-session",
        owner_id="edge-owner",
        lease_id="edge-lease-7",
        fencing_token=6,
        expected_runtime_lease_id=str(runtime["lease_id"]),
        expected_runtime_fencing_token=1,
        now_ms=1_300,
    )
    assert restarted.release_edge_session_lease(
        session_id="edge-session",
        owner_id="edge-owner",
        lease_id="edge-lease-7",
        fencing_token=7,
        expected_runtime_lease_id=str(runtime["lease_id"]),
        expected_runtime_fencing_token=1,
        now_ms=1_300,
    )
    assert restarted.load_edge_session_lease("edge-session", "edge-owner") is None


def test_receipt_identity_is_strict_and_repeated_in_doubt_digest_is_a_noop(tmp_path) -> None:
    repository, _db_path = _repository(tmp_path, "receipt", runtime_scope_id="scope-a")
    authority = _authority(repository, worker_id="receipt-worker")
    command = _command("receipt")
    row = repository.enqueue(command, authority=authority)
    claimed = repository.claim_next(worker_id="receipt-worker", authority=authority)
    assert claimed is not None
    bad = _receipt(command)
    bad["command_id"] = "wrong-command"
    with pytest.raises(EmbodimentOutboxError, match="RECEIPT_IDENTITY_MISMATCH"):
        repository.complete(
            outbox_id=str(row["outbox_id"]),
            claim_token=str(claimed["claim_token"]),
            receipt=bad,
            authority=authority,
        )
    assert repository.get(str(row["outbox_id"]))["status"] == "DISPATCHING"

    receipt = _receipt(command)
    repository.complete(
        outbox_id=str(row["outbox_id"]),
        claim_token=str(claimed["claim_token"]),
        receipt=receipt,
        authority=authority,
    )
    first = repository.get(str(row["outbox_id"]))
    assert first is not None and first["status"] == "IN_DOUBT"
    assert not repository.reconcile_in_doubt(
        outbox_id=str(row["outbox_id"]),
        command_id=str(command["command_id"]),
        idempotency_key=str(command["idempotency_key"]),
        expected_receipt_digest=str(first["receipt_digest"]),
        receipt=receipt,
        authority=authority,
    )
    second = repository.get(str(row["outbox_id"]))
    assert second is not None
    assert second["receipt_json"] == first["receipt_json"]
    assert second["completed_at_ms"] == first["completed_at_ms"]
    wrong = _receipt(command)
    wrong["idempotency_key"] = "wrong-key"
    assert not repository.reconcile_in_doubt(
        outbox_id=str(row["outbox_id"]),
        command_id=str(command["command_id"]),
        idempotency_key=str(command["idempotency_key"]),
        expected_receipt_digest=str(first["receipt_digest"]),
        receipt=wrong,
        authority=authority,
    )


def test_every_outbox_mutation_is_fenced_in_its_own_transaction(tmp_path) -> None:
    repository, _db_path = _repository(tmp_path, "outbox-fence", runtime_scope_id="shared")
    old_authority = _authority(repository, worker_id="old-host")
    command = _command("outbox-fence")
    row = repository.enqueue(command, authority=old_authority)
    assert repository.release_runtime_lease(
        worker_id=old_authority.worker_id,
        lease_id=old_authority.lease_id,
        fencing_token=old_authority.fencing_token,
    )
    replacement = _authority(repository, worker_id="replacement-host")

    with pytest.raises(EmbodimentOutboxError, match="EMBODIMENT_RUNTIME_AUTHORITY_LOST"):
        repository.enqueue(_command("stale-enqueue"), authority=old_authority)
    with pytest.raises(EmbodimentOutboxError, match="EMBODIMENT_RUNTIME_AUTHORITY_LOST"):
        repository.claim_next(worker_id="old-host", authority=old_authority)
    with pytest.raises(EmbodimentOutboxError, match="EMBODIMENT_RUNTIME_AUTHORITY_LOST"):
        repository.expire_dispatching_claims(authority=old_authority)

    claimed = repository.claim_next(
        worker_id="replacement-host",
        authority=replacement,
    )
    assert claimed is not None and claimed["outbox_id"] == row["outbox_id"]
    receipt = _receipt(command)
    with pytest.raises(EmbodimentOutboxError, match="EMBODIMENT_RUNTIME_AUTHORITY_LOST"):
        repository.complete(
            outbox_id=str(row["outbox_id"]),
            claim_token=str(claimed["claim_token"]),
            receipt=receipt,
            authority=old_authority,
        )

    repository.complete(
        outbox_id=str(row["outbox_id"]),
        claim_token=str(claimed["claim_token"]),
        receipt=receipt,
        authority=replacement,
    )
    settled = repository.get(str(row["outbox_id"]))
    assert settled is not None and settled["status"] == "IN_DOUBT"
    with pytest.raises(EmbodimentOutboxError, match="EMBODIMENT_RUNTIME_AUTHORITY_LOST"):
        repository.reconcile_in_doubt(
            outbox_id=str(row["outbox_id"]),
            command_id=str(command["command_id"]),
            idempotency_key=str(command["idempotency_key"]),
            expected_receipt_digest=str(settled["receipt_digest"]),
            receipt=receipt,
            authority=old_authority,
        )
