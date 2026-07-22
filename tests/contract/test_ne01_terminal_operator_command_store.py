from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.terminal_operator_command_store import (
    TERMINAL_OPERATOR_COMMAND_CONFLICT,
    TerminalOperatorCommandStore,
    TerminalOperatorCommandStoreError,
)


def _command(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "operator_command_id": "task_terminal.reconcile",
        "case_id": "terminal-case-0119",
        "expected_case_snapshot_sha256": "sha256:" + "a" * 64,
        "expected_subject_snapshot_sha256": "sha256:" + "b" * 64,
        "action": "RELEASE_BUDGET",
        "target_graph_status": "CANCELLED",
        "target_budget_status": "RELEASED",
        "reconciler_actor_id": "operator.0119",
        "issued_ts_ms": 1_785_000_000_000,
        "attestation_ref": "attestation://0119/terminal-case",
        "attestation_key_id": "operator-key-0119",
        "attestation_signature": "ed25519:" + "A" * 88,
    }
    value.update(overrides)
    return value


def _store(tmp_path: Path) -> tuple[Path, TerminalOperatorCommandStore]:
    db_path = tmp_path / "terminal-operator-command.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="terminal-operator-command-store"
    )
    assert migrated.success is True, migrated.as_dict()
    return db_path, TerminalOperatorCommandStore(db_path)


def _stage(store: TerminalOperatorCommandStore, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "request_id": "request-terminal-0119",
        "principal_id": "operator.0119",
        "command": _command(),
        "ingress_claim_ref": "secure-ingress-claim://0119",
        "ingress_audit_ref": "secure-ingress-audit://0119",
        "ingress_body_sha256": "sha256:" + "c" * 64,
        "staged_at_ms": 1_785_000_000_001,
    }
    values.update(overrides)
    return store.stage(**values)  # type: ignore[arg-type]


def test_0119_stages_and_exactly_replays_one_signed_command(tmp_path: Path) -> None:
    _, store = _store(tmp_path)

    first = _stage(store)
    replay = _stage(store)
    reopened = store.read(str(first["bundle_ref"]))

    assert first["replayed"] is False
    assert replay["replayed"] is True
    assert replay["bundle_ref"] == first["bundle_ref"]
    assert reopened["command"] == _command()
    assert reopened["command_sha256"] == first["command_sha256"]


def test_0119_rejects_request_rebinding(tmp_path: Path) -> None:
    _, store = _store(tmp_path)
    _stage(store)

    with pytest.raises(TerminalOperatorCommandStoreError) as caught:
        _stage(store, command=_command(attestation_signature="ed25519:" + "B" * 88))

    assert caught.value.reason_code == TERMINAL_OPERATOR_COMMAND_CONFLICT


def test_0119_rows_are_immutable_even_without_repository(tmp_path: Path) -> None:
    db_path, store = _store(tmp_path)
    staged = _stage(store)

    with sqlite3.connect(db_path) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE task_terminal_operator_signed_command "
            "SET principal_id='operator.forged' WHERE bundle_ref=?",
            (staged["bundle_ref"],),
        )


def test_0119_reader_detects_canonical_payload_tamper(tmp_path: Path) -> None:
    db_path, store = _store(tmp_path)
    staged = _stage(store)
    forged = _command(reconciler_actor_id="operator.forged")
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TRIGGER task_terminal_operator_signed_command_no_update")
        conn.execute(
            "UPDATE task_terminal_operator_signed_command SET command_json=? "
            "WHERE bundle_ref=?",
            (
                json.dumps(
                    forged,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                staged["bundle_ref"],
            ),
        )
        conn.commit()

    with pytest.raises(TerminalOperatorCommandStoreError) as caught:
        store.read(str(staged["bundle_ref"]))

    assert caught.value.reason_code == TERMINAL_OPERATOR_COMMAND_CONFLICT
