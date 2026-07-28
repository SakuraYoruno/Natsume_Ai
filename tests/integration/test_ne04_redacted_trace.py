from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.evolution_lab.production_composition import (
    build_trusted_evolution_lab_composition,
)
from host_brain.evolution_lab.redacted_trace import (
    REDACTION_RULES_VERSION,
    TrustedCompositeDatasetReader,
    TrustedTraceRedactionError,
    TrustedTraceRedactionService,
)
from host_brain.persistence.evolution_lab_redacted_dataset_repository import (
    SQLiteTrustedRedactedDatasetReader,
)
from host_brain.persistence.evolution_lab_trusted_repository import (
    SQLiteTrustedEvolutionLabLedger,
)


_PINNED_IMAGE = "python@sha256:" + "a" * 64
_PRIVATE_MARKERS = (
    "private prompt for Alice",
    "alice@example.com",
    "sk-super-secret-provider-token",
    "+86 138 0013 8000",
)


def _prepare(tmp_path: Path, *, clock=lambda: 100):
    db_path = tmp_path / "trusted-redacted-trace.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="ne04-redacted-trace",
        ts_ms=1,
    )
    assert migrated.success is True
    assert migrated.summary["latest_version"] >= 140
    ledger = SQLiteTrustedEvolutionLabLedger(db_path, clock=clock)
    reader = SQLiteTrustedRedactedDatasetReader(db_path)
    service = TrustedTraceRedactionService(
        ledger=ledger,
        reader=reader,
        clock=clock,
    )
    return db_path, ledger, reader, service


def _real_trace() -> tuple[dict[str, object], ...]:
    return (
        {
            "event_type": "task.completed",
            "status": "PASS",
            "duration_ms": 120,
            "success": True,
            "route": "skill",
            "tool_name": "read_file",
            "capability_id": "planning.search",
            "reason_code": "VERIFIED",
            "tags": [
                "accepted",
                "alice@example.com",
                "sk-super-secret-provider-token",
            ],
            "prompt": "private prompt for Alice",
            "content": "call +86 138 0013 8000",
            "provider_token": "sk-super-secret-provider-token",
        },
        {
            "event_type": "task.failed",
            "status": "FAIL",
            "risk_level": "HIGH",
            "attempt_no": 2,
            "latency_ms": 13.5,
            "user_annoyed": False,
            "route": "sk-super-secret-provider-token",
            "tool_name": "f" * 64,
            "email": "alice@example.com",
        },
    )


def _database_bytes(db_path: Path) -> bytes:
    return b"".join(
        path.read_bytes()
        for path in sorted(db_path.parent.glob(f"{db_path.name}*"))
        if path.is_file()
    )


def test_ne04_real_trace_intake_persists_only_bounded_redacted_projection(
    tmp_path: Path,
) -> None:
    db_path, _ledger, reader, service = _prepare(tmp_path)

    accepted = service.redact_and_persist(
        workspace_id="workspace-ne04-redaction",
        records=_real_trace(),
    )

    assert accepted.receipt.rules_version == REDACTION_RULES_VERSION
    assert accepted.receipt.record_count == 2
    assert accepted.receipt.removed_field_count >= 8
    assert accepted.receipt.output_dataset_digest == (
        accepted.dataset.dataset_digest
    )
    assert accepted.manifest.redaction_receipt_digest == (
        accepted.receipt.receipt_digest
    )
    assert accepted.manifest.source_kind == "redacted_trace"
    assert reader.read(manifest=accepted.manifest) == accepted.dataset
    assert set(accepted.dataset.records[0]) == {
        "capability_id",
        "duration_ms",
        "event_type",
        "reason_code",
        "record_ref",
        "route",
        "status",
        "success",
        "tags",
        "tool_name",
    }
    assert set(accepted.dataset.records[1]) == {
        "attempt_no",
        "event_type",
        "latency_ms",
        "record_ref",
        "risk_level",
        "status",
        "user_annoyed",
    }
    serialized = json.dumps(
        accepted.dataset.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
    )
    durable_bytes = _database_bytes(db_path)
    for marker in _PRIVATE_MARKERS:
        assert marker not in serialized
        assert marker.encode("utf-8") not in durable_bytes


def test_ne04_redacted_trace_exact_replay_reopens_after_restart(
    tmp_path: Path,
) -> None:
    db_path, _ledger, _reader, service = _prepare(
        tmp_path,
        clock=lambda: 100,
    )
    first = service.redact_and_persist(
        workspace_id="workspace-ne04-restart",
        records=_real_trace(),
    )

    restarted_reader = SQLiteTrustedRedactedDatasetReader(db_path)
    restarted_service = TrustedTraceRedactionService(
        ledger=SQLiteTrustedEvolutionLabLedger(
            db_path,
            clock=lambda: 999,
        ),
        reader=restarted_reader,
        clock=lambda: 999,
    )
    replay = restarted_service.redact_and_persist(
        workspace_id="workspace-ne04-restart",
        records=_real_trace(),
    )

    assert replay == first
    assert restarted_reader.read_by_dataset_id(
        first.dataset.dataset_id
    ) == first
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) "
            "FROM evolution_lab_trusted_redaction_receipt"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) "
            "FROM evolution_lab_trusted_redacted_dataset"
        ).fetchone()[0] == 1


def test_ne04_redacted_dataset_direct_mutation_is_rejected(
    tmp_path: Path,
) -> None:
    db_path, _ledger, _reader, service = _prepare(tmp_path)
    service.redact_and_persist(
        workspace_id="workspace-ne04-immutable",
        records=_real_trace(),
    )

    for statement in (
        (
            "UPDATE evolution_lab_trusted_redacted_dataset "
            "SET record_count = record_count"
        ),
        "DELETE FROM evolution_lab_trusted_redacted_dataset",
    ):
        with sqlite3.connect(db_path) as conn:
            with pytest.raises(sqlite3.DatabaseError):
                conn.execute(statement)
    with sqlite3.connect(db_path) as conn:
        conn.create_function(
            "natsume_evolution_lab_write_authorized",
            0,
            lambda: 0,
        )
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(
                "INSERT INTO evolution_lab_trusted_redacted_dataset "
                "DEFAULT VALUES"
            )
    with sqlite3.connect(
        f"{db_path.as_uri()}?mode=ro",
        uri=True,
    ) as conn:
        conn.execute("PRAGMA query_only = ON")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(
                "UPDATE evolution_lab_trusted_redacted_dataset "
                "SET record_count = record_count"
            )


def test_ne04_redacted_dataset_and_receipt_commit_atomically(
    tmp_path: Path,
) -> None:
    db_path, _ledger, _reader, service = _prepare(tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TRIGGER force_redacted_dataset_failure
            BEFORE INSERT ON evolution_lab_trusted_redacted_dataset
            BEGIN
                SELECT RAISE(ABORT, 'forced redacted dataset failure');
            END
            """
        )

    with pytest.raises(sqlite3.DatabaseError):
        service.redact_and_persist(
            workspace_id="workspace-ne04-atomic",
            records=_real_trace(),
        )

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) "
            "FROM evolution_lab_trusted_redaction_receipt"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) "
            "FROM evolution_lab_trusted_redacted_dataset"
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    ("column", "replacement"),
    (
        ("source_digest", "e" * 64),
        ("output_dataset_digest", "d" * 64),
    ),
)
def test_ne04_redacted_reader_rejects_receipt_index_drift(
    tmp_path: Path,
    column: str,
    replacement: str,
) -> None:
    db_path, _ledger, _reader, service = _prepare(tmp_path)
    accepted = service.redact_and_persist(
        workspace_id="workspace-ne04-tamper",
        records=_real_trace(),
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "DROP TRIGGER evolution_lab_trusted_redaction_no_update"
        )
        conn.execute(
            f"UPDATE evolution_lab_trusted_redaction_receipt "
            f"SET {column} = ? WHERE redaction_id = ?",
            (replacement, accepted.receipt.redaction_id),
        )

    tampered_reader = SQLiteTrustedRedactedDatasetReader(db_path)
    with pytest.raises(
        TrustedTraceRedactionError,
        match="durable binding drift",
    ):
        tampered_reader.read_by_dataset_id(accepted.dataset.dataset_id)


def test_ne04_redacted_reader_rejects_rules_digest_drift(
    tmp_path: Path,
) -> None:
    db_path, _ledger, _reader, service = _prepare(tmp_path)
    accepted = service.redact_and_persist(
        workspace_id="workspace-ne04-rules-tamper",
        records=_real_trace(),
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "DROP TRIGGER evolution_lab_trusted_redaction_no_update"
        )
        row = conn.execute(
            "SELECT receipt_json "
            "FROM evolution_lab_trusted_redaction_receipt "
            "WHERE redaction_id = ?",
            (accepted.receipt.redaction_id,),
        ).fetchone()
        payload = json.loads(str(row[0]))
        payload["rules_digest"] = "c" * 64
        conn.execute(
            "UPDATE evolution_lab_trusted_redaction_receipt "
            "SET receipt_json = ? WHERE redaction_id = ?",
            (
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                accepted.receipt.redaction_id,
            ),
        )

    tampered_reader = SQLiteTrustedRedactedDatasetReader(db_path)
    with pytest.raises(
        TrustedTraceRedactionError,
        match="durable binding drift",
    ):
        tampered_reader.read_by_redaction_id(
            accepted.receipt.redaction_id
        )


def test_ne04_production_composition_reads_synthetic_and_redacted_data(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "trusted-composition.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="ne04-redacted-composition",
        ts_ms=1,
    )
    assert migrated.success is True
    composition = build_trusted_evolution_lab_composition(
        db_path=db_path,
        runtime_root=tmp_path / "runtime",
        image_reference=_PINNED_IMAGE,
    )

    accepted = composition.trace_redaction.redact_and_persist(
        workspace_id="workspace-ne04-composition",
        records=_real_trace(),
    )

    assert isinstance(
        composition.dataset_reader,
        TrustedCompositeDatasetReader,
    )
    assert composition.dataset_reader.read(
        manifest=accepted.manifest
    ) == accepted.dataset
