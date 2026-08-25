from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.governor.tokens import (
    TOKEN_TYPE_CANON_ROLLBACK,
    CanonApprovalTokenIssuer,
    canon_token_digest,
)
from host_brain.memory.canon_apply_service import (
    CANON_OPERATION_APPLY,
    CANON_OPERATION_ROLLBACK,
    CANON_SUBJECT_PENTEST_PROMOTION,
    CANON_SUBJECT_PROPOSAL,
    CanonApplyCommand,
    CanonApplyCrash,
    CanonApplyError,
    CanonApplyService,
    _validated_canon_snapshot,
    default_world_canon_root,
    resolve_world_canon_root,
)
from host_brain.memory.canon_world_store import WorldCanonStore, compute_world_canon_hash
from host_brain.observability.degradation_state import (
    DegradationStateMachine,
    set_global_degradation_state_machine,
)
from host_brain.pentest.knowledge_store import PentestKnowledgeEntry, PentestKnowledgeStore
from host_brain.persistence.canon_apply_repository import (
    CANON_APPLY_MIGRATION_SHA256,
    assert_canon_apply_schema_ready,
)
from tests.support.degradation_authority import GlobalDegradationTestPort


CANON_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "host_brain"
    / "audit"
    / "migrations"
    / "0053_canon_apply.sql"
)


@pytest.fixture(autouse=True)
def _isolated_degradation_machine(monkeypatch: pytest.MonkeyPatch):
    """Keep Canon contracts independent from an ignored runtime audit DB."""
    monkeypatch.setenv("HOST_DEGRADATION_STATE_ENABLED", "true")
    set_global_degradation_state_machine(DegradationStateMachine())
    try:
        yield
    finally:
        set_global_degradation_state_machine(None)


def _install_canon_schema(
    db_path: Path,
    *,
    schema_sql: str | None = None,
    recorded_checksum: str | None = None,
) -> None:
    canonical_sql = CANON_MIGRATION.read_text(encoding="utf-8-sig")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                migration_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                checksum TEXT NOT NULL,
                applied_ts_ms INTEGER NOT NULL
            )
            """
        )
        conn.executescript(schema_sql if schema_sql is not None else canonical_sql)
        conn.execute(
            """
            INSERT INTO schema_migrations(migration_id, name, checksum, applied_ts_ms)
            VALUES (53, 'canon_apply', ?, 1000000)
            """,
            (
                recorded_checksum
                if recorded_checksum is not None
                else hashlib.sha256(canonical_sql.encode("utf-8")).hexdigest(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


class Clock:
    def __init__(self, value: int = 1_000_000) -> None:
        self._value = value
        self._lock = threading.Lock()

    def __call__(self) -> int:
        with self._lock:
            return self._value

    def advance(self, delta: int) -> int:
        with self._lock:
            self._value += delta
            return self._value


class Harness:
    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import host_brain.memory.canon_apply_service as module

        monkeypatch.setattr(module, "trigger_canon_vector_auto_sync", lambda **_kwargs: None)
        self.root = root
        self.db_path = (root / "audit.sqlite3").resolve()
        self.world_root = (root / "world").resolve()
        self.clock = Clock()
        self.private_key = Ed25519PrivateKey.generate()
        self.key_id = "master-test-key"
        self.ledger = SQLiteLedger(self.db_path, check_same_thread=False)
        _install_canon_schema(self.db_path)
        self.store = WorldCanonStore(world_root=self.world_root, ledger=self.ledger)

    def service(self, **kwargs) -> CanonApplyService:
        return CanonApplyService(
            world_root=self.world_root,
            ledger=self.ledger,
            clock=self.clock,
            recovery_grace_ms=100,
            recovery_lease_ms=100,
            trusted_approval_keys={self.key_id: self.private_key.public_key()},
            degradation_capability_port=(
                GlobalDegradationTestPort()
            ),
            **kwargs,
        )

    def issuer(
        self,
        *,
        private_key: Ed25519PrivateKey | None = None,
        key_id: str | None = None,
        clock: Clock | None = None,
    ) -> CanonApprovalTokenIssuer:
        return CanonApprovalTokenIssuer(
            self.db_path,
            authority_key_id=key_id or self.key_id,
            private_key=private_key or self.private_key,
            reviewer_id="owner-master",
            clock=clock or self.clock,
        )

    def proposal(self, proposal_id: str, target: str, *, body: str = "reviewed fact") -> dict:
        return self.store.submit_proposal(
            proposal_id=proposal_id,
            target_doc_id=target,
            proposed_title=f"Title {proposal_id}",
            proposed_tags=["reviewed"],
            proposed_body=body,
            proposer_node_id="test.proposer",
            ts_ms=self.clock(),
        )

    @staticmethod
    def binding(row: dict, **overrides) -> dict:
        values = {
            "operation": CANON_OPERATION_APPLY,
            "subject_type": CANON_SUBJECT_PROPOSAL,
            "subject_id": row["proposal_id"],
            "subject_version": int(row["proposed_version"]),
            "subject_hash": row["proposed_hash"],
            "target_doc_id": row["target_id"],
            "base_version": int(row["base_version"]),
            "actor_id": "owner",
            "session_id": "session-1",
            "trace_id": "trace-1",
            "evidence_refs": ("audit://proposal/review-1",),
        }
        values.update(overrides)
        return values

    def issue_command(
        self,
        row: dict,
        *,
        apply_id: str,
        issuer: CanonApprovalTokenIssuer | None = None,
        token_type: str | None = None,
        ttl_ms: int | None = None,
        **overrides,
    ) -> tuple[CanonApplyCommand, str, dict]:
        binding = self.binding(row, **overrides)
        token = (issuer or self.issuer()).issue_approved_token(
            **binding,
            token_type=token_type,
            ttl_ms=ttl_ms,
        )
        return (
            CanonApplyCommand(
                apply_id=apply_id,
                approval_token_ref=token,
                **binding,
            ),
            token,
            binding,
        )


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    return Harness(tmp_path, monkeypatch)


def _token_row(harness: Harness, token: str) -> sqlite3.Row:
    conn = sqlite3.connect(harness.db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM canon_apply_token WHERE token_digest = ?",
            (canon_token_digest(token),),
        ).fetchone()
        assert row is not None
        return row
    finally:
        conn.close()


def _apply_two_proposal_versions(
    harness: Harness,
    suffix: str,
) -> tuple[dict, str, dict, str]:
    target_doc_id = f"doc-chain-{suffix}"
    first = harness.proposal(
        f"proposal-chain-v1-{suffix}",
        target_doc_id,
        body=f"trusted v1 {suffix}",
    )
    first_apply_id = f"chain-v1-{suffix}"
    first_command, _token, _ = harness.issue_command(first, apply_id=first_apply_id)
    harness.service().apply(first_command)

    second = harness.proposal(
        f"proposal-chain-v2-{suffix}",
        target_doc_id,
        body=f"trusted v2 {suffix}",
    )
    second_apply_id = f"chain-v2-{suffix}"
    second_command, _token, _ = harness.issue_command(second, apply_id=second_apply_id)
    harness.service().apply(second_command)
    return first, first_apply_id, second, second_apply_id


def _issue_rollback_for_proposal(
    harness: Harness,
    proposal: dict,
    *,
    apply_id: str,
) -> tuple[CanonApplyCommand, str]:
    document = harness.store.get(proposal["target_id"])
    assert document is not None
    binding = {
        "operation": CANON_OPERATION_ROLLBACK,
        "subject_type": CANON_SUBJECT_PROPOSAL,
        "subject_id": proposal["proposal_id"],
        "subject_version": document.version,
        "subject_hash": document.hash,
        "target_doc_id": document.doc_id,
        "base_version": document.version,
        "actor_id": "owner",
        "session_id": f"session-{apply_id}",
        "trace_id": f"trace-{apply_id}",
        "evidence_refs": (f"audit://rollback/{apply_id}",),
    }
    token = harness.issuer().issue_approved_token(**binding)
    return (
        CanonApplyCommand(
            apply_id=apply_id,
            approval_token_ref=token,
            **binding,
        ),
        token,
    )


def _tamper_applied_before_snapshot(
    harness: Harness,
    *,
    source_apply_id: str,
    forged_body: str,
) -> dict:
    conn = sqlite3.connect(harness.db_path)
    try:
        raw = conn.execute(
            "SELECT before_payload_json FROM canon_apply_journal WHERE apply_id = ?",
            (source_apply_id,),
        ).fetchone()[0]
        payload = json.loads(raw)
        payload["body"] = forged_body
        identity = {key: value for key, value in payload.items() if key != "hash"}
        payload["hash"] = compute_world_canon_hash(identity)
        conn.execute(
            """
            UPDATE canon_apply_journal
            SET before_payload_json = ?, before_hash = ?
            WHERE apply_id = ?
            """,
            (
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                payload["hash"],
                source_apply_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return payload


def _tamper_sqlite_real(
    harness: Harness,
    *,
    table: str,
    key_column: str,
    key_value: str,
    column: str,
    value: float = 1.5,
) -> None:
    conn = sqlite3.connect(harness.db_path)
    try:
        conn.execute("PRAGMA ignore_check_constraints = ON")
        conn.execute(
            f"UPDATE {table} SET {column} = ? WHERE {key_column} = ?",
            (value, key_value),
        )
        stored_type = conn.execute(
            f"SELECT typeof({column}) FROM {table} WHERE {key_column} = ?",
            (key_value,),
        ).fetchone()[0]
        conn.commit()
    finally:
        conn.close()
    assert stored_type == "real"


def test_t_nle_03_apply_is_signed_durable_and_stores_only_bearer_digest(harness: Harness) -> None:
    row = harness.proposal("proposal-basic", "doc-basic")
    command, token, _binding = harness.issue_command(row, apply_id="apply-basic")

    result = harness.service().apply(command)

    assert result.state == "APPLIED"
    assert harness.store.get("doc-basic").body == "reviewed fact"
    token_row = _token_row(harness, token)
    assert token_row["state"] == "BURNED"
    assert token_row["token_digest"] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in json.dumps(dict(token_row), ensure_ascii=False)
    journal = harness.service().get_journal("apply-basic")
    assert journal is not None
    assert journal["token_digest"] == token_row["token_digest"]
    assert token not in json.dumps(journal, ensure_ascii=False)


def test_t_nle_03_raw_mutators_and_forged_permit_always_fail_closed(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NATSUME_ALLOW_CANON_DIRECT_WRITE", "1")
    with pytest.raises(PermissionError, match="CANON_DIRECT_WRITE_FORBIDDEN"):
        harness.store.upsert_document(
            doc_id="raw",
            title="raw",
            tags=[],
            body="raw",
            proposal_id="raw",
        )
    with pytest.raises(PermissionError, match="CANON_DIRECT_WRITE_FORBIDDEN"):
        harness.store.upsert(
            doc_id="raw", title="raw", body="raw", proposal_id="raw"
        )
    with pytest.raises(PermissionError, match="CANON_DIRECT_WRITE_FORBIDDEN"):
        harness.store.approve_proposal_with_content(
            "raw", proposed_title="x", proposed_tags=[], proposed_body="x"
        )
    with pytest.raises(PermissionError, match="CANON_DIRECT_WRITE_FORBIDDEN"):
        harness.store.rollback_proposal("raw")
    with pytest.raises(PermissionError, match="CANON_APPLY_PERMIT_INVALID"):
        harness.store._replace_authorized_payload(
            {}, expected_version=0, expected_hash=None, apply_id="x", permit=object()
        )
    with pytest.raises(PermissionError, match="CANON_APPLY_PERMIT_INVALID"):
        harness.store._delete_authorized_document(
            "raw", expected_version=0, expected_hash=None, apply_id="x", permit=object()
        )


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    (
        ("version", 1.0),
        ("version", "1"),
        ("version", True),
        ("ts_ms", 1.0),
        ("ts_ms", "1"),
        ("ts_ms", True),
        ("doc_id", 7),
        ("status", 7),
        ("title", []),
        ("body", {}),
        ("updated_by", False),
        ("tags", [7]),
        ("hash", 7),
    ),
)
def test_t_nle_03_journal_snapshot_requires_exact_json_field_types(
    field: str,
    invalid_value: object,
) -> None:
    payload: dict[str, object] = {
        "doc_id": "snapshot-types",
        "version": 1,
        "status": "active",
        "title": "Snapshot types",
        "tags": ["reviewed"],
        "body": "reviewed fact",
        "updated_by": "proposal-snapshot-types",
        "ts_ms": 1,
    }
    payload[field] = invalid_value
    identity = {key: value for key, value in payload.items() if key != "hash"}
    computed_hash = compute_world_canon_hash(identity)
    if field != "hash":
        payload["hash"] = computed_hash
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    with pytest.raises(CanonApplyError) as error:
        _validated_canon_snapshot(
            raw,
            target_doc_id="snapshot-types",
            expected_version=1,
            expected_hash=computed_hash,
        )
    assert error.value.reason_code == "CANON_APPLY_JOURNAL_INTEGRITY_FAILED"
    assert error.value.detail == "snapshot field invalid"


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    (
        ("subject_version", 1.0),
        ("subject_version", True),
        ("base_version", 0.0),
        ("base_version", False),
    ),
)
def test_t_nle_03_command_rejects_non_exact_integer_inputs(
    field: str,
    invalid_value: object,
) -> None:
    values = {
        "apply_id": "command-exact-int",
        "operation": CANON_OPERATION_APPLY,
        "subject_type": CANON_SUBJECT_PROPOSAL,
        "subject_id": "proposal-command-exact-int",
        "subject_version": 1,
        "subject_hash": "a" * 64,
        "target_doc_id": "command-exact-int",
        "base_version": 0,
        "approval_token_ref": "canon::command-exact-int",
        "actor_id": "owner",
        "session_id": "command-exact-int",
        "trace_id": "command-exact-int",
        "evidence_refs": ("audit://command/exact-int",),
    }
    values[field] = invalid_value
    with pytest.raises(ValueError, match="CANON_APPLY_INTEGER_INVALID"):
        CanonApplyCommand(**values)


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    (
        ("subject_version", 1.0),
        ("subject_version", True),
        ("base_version", 0.0),
        ("base_version", False),
        ("ttl_ms", 1.0),
        ("ttl_ms", True),
    ),
)
def test_t_nle_03_offline_issuer_rejects_non_exact_integer_inputs(
    harness: Harness,
    field: str,
    invalid_value: object,
) -> None:
    row = harness.proposal(
        f"proposal-issuer-exact-{field}-{type(invalid_value).__name__}",
        f"doc-issuer-exact-{field}-{type(invalid_value).__name__}",
    )
    binding = harness.binding(row)
    ttl_ms = None
    if field == "ttl_ms":
        ttl_ms = invalid_value
    else:
        binding[field] = invalid_value
    with pytest.raises(ValueError, match="CANON_APPLY_INTEGER_INVALID"):
        harness.issuer().issue_approved_token(
            **binding,
            ttl_ms=ttl_ms,
        )


def test_t_nle_03_missing_forged_wrong_type_and_exact_expiry_reject(harness: Harness) -> None:
    row = harness.proposal("proposal-token", "doc-token")
    _command, _token, binding = harness.issue_command(row, apply_id="unused")
    with pytest.raises(ValueError, match="CANON_APPLY_TOKEN_MISSING"):
        CanonApplyCommand(apply_id="missing", approval_token_ref="", **binding)

    forged = CanonApplyCommand(
        apply_id="forged", approval_token_ref="canon::forged", **binding
    )
    with pytest.raises(CanonApplyError) as forged_error:
        harness.service().apply(forged)
    assert forged_error.value.reason_code == "CANON_APPLY_TOKEN_INVALID"

    wrong_command, _wrong, _ = harness.issue_command(
        row,
        apply_id="wrong-type",
        token_type=TOKEN_TYPE_CANON_ROLLBACK,
    )
    with pytest.raises(CanonApplyError) as wrong_error:
        harness.service().apply(wrong_command)
    assert wrong_error.value.reason_code == "CANON_APPLY_TOKEN_BINDING_MISMATCH"

    expiry_command, expiry_token, _ = harness.issue_command(
        row, apply_id="expiry", ttl_ms=10
    )
    harness.clock.advance(10)
    with pytest.raises(CanonApplyError) as expiry_error:
        harness.service().apply(expiry_command)
    assert expiry_error.value.reason_code == "CANON_APPLY_TOKEN_EXPIRED"
    assert _token_row(harness, expiry_token)["state"] == "AVAILABLE"


@pytest.mark.parametrize(
    "column",
    (
        "subject_version",
        "base_version",
        "issued_ts_ms",
        "expiry_ts_ms",
    ),
)
def test_t_nle_03_real_token_integer_tamper_fails_before_burn(
    harness: Harness,
    column: str,
) -> None:
    row = harness.proposal(f"proposal-token-real-{column}", f"doc-token-real-{column}")
    command, token, _ = harness.issue_command(
        row,
        apply_id=f"token-real-{column}",
    )
    token_row = _token_row(harness, token)
    _tamper_sqlite_real(
        harness,
        table="canon_apply_token",
        key_column="token_digest",
        key_value=token_row["token_digest"],
        column=column,
    )

    with pytest.raises(CanonApplyError) as error:
        harness.service().apply(command)

    assert error.value.reason_code == "CANON_APPLY_APPROVAL_ATTESTATION_BINDING_MISMATCH"
    assert _token_row(harness, token)["state"] == "AVAILABLE"
    assert harness.ledger.get_proposal(row["proposal_id"])["status"] == "pending"
    assert harness.store.get(command.target_doc_id) is None
    assert harness.service().get_journal(command.apply_id) is None


def test_t_nle_03_binding_mismatch_and_secret_or_oversize_refs_reject(harness: Harness) -> None:
    row = harness.proposal("proposal-bind", "doc-bind")
    command, token, _ = harness.issue_command(row, apply_id="binding")
    service = harness.service()
    for changed in (
        replace(command, session_id="other-session"),
        replace(command, actor_id="other-actor"),
        replace(command, subject_version=command.subject_version + 1),
        replace(command, base_version=command.base_version + 1),
        replace(command, evidence_refs=("audit://other",)),
    ):
        with pytest.raises(CanonApplyError) as error:
            service.apply(changed)
        assert error.value.reason_code == "CANON_APPLY_TOKEN_BINDING_MISMATCH"
    assert _token_row(harness, token)["state"] == "AVAILABLE"

    for bad_refs in (
        ("plain text is not a ref",),
        ("audit://x?access_token=supersecret",),
        ("audit://x\ncontrol",),
        tuple(f"audit://e/{index}" for index in range(33)),
        ("audit://" + "x" * 600,),
    ):
        with pytest.raises(ValueError):
            CanonApplyCommand(
                apply_id="bad-ref",
                approval_token_ref="canon::placeholder",
                **Harness.binding(row, evidence_refs=bad_refs),
            )
    with pytest.raises(ValueError):
        CanonApplyCommand(
            apply_id="bad-control",
            approval_token_ref="canon::placeholder",
            **Harness.binding(row, trace_id="trace\x00secret"),
        )


def test_t_nle_03_immutable_proposal_payload_and_base_drift_reject_without_burn(
    harness: Harness,
) -> None:
    row = harness.proposal("proposal-drift", "doc-drift")
    command, token, _ = harness.issue_command(row, apply_id="payload-drift")
    harness.ledger.update_proposal_content(
        row["proposal_id"],
        proposed_title="replacement title",
        proposed_tags=["replacement"],
        proposed_body="replacement body",
    )
    with pytest.raises(CanonApplyError) as payload_error:
        harness.service().apply(command)
    assert payload_error.value.reason_code == "CANON_APPLY_PROPOSAL_HASH_DRIFT"
    assert _token_row(harness, token)["state"] == "AVAILABLE"
    assert harness.ledger.get_proposal(row["proposal_id"])["status"] == "pending"

    first = harness.proposal("proposal-first", "doc-base")
    first_command, _first_token, _ = harness.issue_command(first, apply_id="base-first")
    stale = harness.proposal("proposal-stale", "doc-base")
    stale_command, stale_token, _ = harness.issue_command(stale, apply_id="base-stale")
    harness.service().apply(first_command)
    with pytest.raises(CanonApplyError) as base_error:
        harness.service().apply(stale_command)
    assert base_error.value.reason_code == "CANON_APPLY_BASE_VERSION_DRIFT"
    assert _token_row(harness, stale_token)["state"] == "AVAILABLE"


def test_t_nle_03_multi_instance_replay_has_exactly_one_winner(harness: Harness) -> None:
    row = harness.proposal("proposal-race", "doc-race")
    command, token, binding = harness.issue_command(row, apply_id="race")
    services = [harness.service(), harness.service()]

    def run(service: CanonApplyService):
        try:
            return service.apply(command)
        except CanonApplyError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(run, services))
    assert sum(not isinstance(item, CanonApplyError) for item in outcomes) == 1
    assert sum(isinstance(item, CanonApplyError) for item in outcomes) == 1
    assert _token_row(harness, token)["state"] == "BURNED"

    new_token = harness.issuer().issue_approved_token(**binding)
    with pytest.raises(CanonApplyError) as replay_error:
        harness.service().apply(
            CanonApplyCommand(apply_id="race", approval_token_ref=new_token, **binding)
        )
    assert replay_error.value.reason_code == "CANON_APPLY_REPLAY"


def test_t_nle_03_active_target_serializes_subjects_without_burning_loser(
    harness: Harness,
) -> None:
    first = harness.proposal("proposal-target-a", "doc-shared")
    second = harness.proposal("proposal-target-b", "doc-shared")
    first_command, _first_token, _ = harness.issue_command(first, apply_id="target-a")
    second_command, second_token, second_binding = harness.issue_command(
        second, apply_id="target-b"
    )
    prepared = threading.Event()
    release = threading.Event()

    def pause(stage: str, _apply_id: str) -> None:
        if stage == "after_prepare":
            prepared.set()
            assert release.wait(timeout=10)

    first_service = harness.service(fault_injector=pause)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(first_service.apply, first_command)
        assert prepared.wait(timeout=10)

        with pytest.raises(CanonApplyError) as busy_error:
            harness.service().apply(second_command)
        assert busy_error.value.reason_code == "CANON_APPLY_TARGET_BUSY"
        assert _token_row(harness, second_token)["state"] == "AVAILABLE"
        assert harness.ledger.get_proposal(second["proposal_id"])["status"] == "pending"

        forged = CanonApplyCommand(
            apply_id="target-a",
            approval_token_ref="canon::forged",
            **second_binding,
        )
        with pytest.raises(CanonApplyError) as hidden_error:
            harness.service().apply(forged)
        assert hidden_error.value.reason_code == "CANON_APPLY_TOKEN_INVALID"
        assert "target-a" not in str(hidden_error.value)

        release.set()
        assert future.result(timeout=10).state == "APPLIED"


def test_t_nle_03_live_apply_lease_is_not_stolen_then_crash_recovers(harness: Harness) -> None:
    row = harness.proposal("proposal-lease", "doc-lease")
    command, _token, _ = harness.issue_command(row, apply_id="lease")
    prepared = threading.Event()
    release = threading.Event()

    def pause(stage: str, _apply_id: str) -> None:
        if stage == "after_prepare":
            prepared.set()
            assert release.wait(timeout=10)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(harness.service(fault_injector=pause).apply, command)
        assert prepared.wait(timeout=10)
        assert harness.service().recover_pending() == []
        release.set()
        assert future.result(timeout=10).state == "APPLIED"

    crashed = harness.proposal("proposal-crash", "doc-crash")
    crash_command, _crash_token, _ = harness.issue_command(crashed, apply_id="crash")

    def crash(stage: str, _apply_id: str) -> None:
        if stage == "after_file_replace":
            raise CanonApplyCrash()

    with pytest.raises(CanonApplyCrash):
        harness.service(fault_injector=crash).apply(crash_command)
    assert harness.service().recover_pending() == []
    harness.clock.advance(101)
    recovered = harness.service().recover_pending()
    assert len(recovered) == 1 and recovered[0].recovered


@pytest.mark.parametrize(
    ("table", "column"),
    (
        ("canon_apply_journal", "subject_version"),
        ("canon_apply_journal", "base_version"),
        ("canon_apply_journal", "before_version"),
        ("canon_apply_journal", "after_version"),
        ("canon_apply_journal", "fence_version"),
        ("canon_apply_journal", "file_replaced"),
        ("canon_apply_journal", "recovery_not_before_ts_ms"),
        ("canon_apply_journal", "lease_expiry_ts_ms"),
        ("canon_apply_journal", "prepared_ts_ms"),
        ("canon_apply_journal", "updated_ts_ms"),
        ("canon_apply_journal", "completed_ts_ms"),
        ("canon_apply_token", "burned_ts_ms"),
    ),
)
def test_t_nle_03_real_prepared_integer_tamper_is_quarantined_on_recovery(
    harness: Harness,
    table: str,
    column: str,
) -> None:
    row = harness.proposal(
        f"proposal-prepared-real-{column}",
        f"doc-prepared-real-{column}",
    )
    command, token, _ = harness.issue_command(
        row,
        apply_id=f"prepared-real-{column}",
    )

    def crash(stage: str, _apply_id: str) -> None:
        if stage == "after_prepare":
            raise CanonApplyCrash()

    with pytest.raises(CanonApplyCrash):
        harness.service(fault_injector=crash).apply(command)

    if table == "canon_apply_journal":
        key_column = "apply_id"
        key_value = command.apply_id
    else:
        key_column = "token_digest"
        key_value = _token_row(harness, token)["token_digest"]
    _tamper_sqlite_real(
        harness,
        table=table,
        key_column=key_column,
        key_value=key_value,
        column=column,
    )

    harness.clock.advance(101)
    assert harness.service().recover_pending() == []
    journal = harness.service().get_journal(command.apply_id)
    assert journal["state"] == "RECOVERY_REQUIRED"
    assert journal["reason_code"] == "CANON_APPLY_JOURNAL_INTEGRITY_FAILED"
    assert _token_row(harness, token)["state"] == "BURNED"
    assert harness.ledger.get_proposal(row["proposal_id"])["status"] == "APPLY_PREPARED"
    assert harness.store.get(command.target_doc_id) is None


@pytest.mark.parametrize(
    "corruption",
    (
        "extra_key",
        "missing_hash",
        "missing_status",
        "missing_tags",
        "version_bool",
        "version_float",
        "version_string",
        "ts_ms_bool",
        "ts_ms_float",
        "ts_ms_string",
        "non_string_tag",
        "embedded_doc_id",
    ),
)
def test_t_nle_03_after_replace_recovery_rejects_non_exact_disk_payload(
    harness: Harness,
    corruption: str,
) -> None:
    row = harness.proposal(
        f"proposal-disk-{corruption}",
        f"doc-disk-{corruption.replace('_', '-')}",
    )
    command, _token, _ = harness.issue_command(row, apply_id=f"disk-{corruption}")

    def crash(stage: str, _apply_id: str) -> None:
        if stage == "after_file_replace":
            raise CanonApplyCrash()

    with pytest.raises(CanonApplyCrash):
        harness.service(fault_injector=crash).apply(command)

    path = harness.world_root / f"{command.target_doc_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if corruption == "extra_key":
        payload["unknown"] = "must-not-be-ignored"
    elif corruption == "missing_hash":
        del payload["hash"]
    elif corruption == "missing_status":
        del payload["status"]
    elif corruption == "missing_tags":
        del payload["tags"]
    elif corruption == "version_bool":
        payload["version"] = True
    elif corruption == "version_float":
        payload["version"] = float(payload["version"])
    elif corruption == "version_string":
        payload["version"] = str(payload["version"])
    elif corruption == "ts_ms_bool":
        payload["ts_ms"] = True
    elif corruption == "ts_ms_float":
        payload["ts_ms"] = float(payload["ts_ms"])
    elif corruption == "ts_ms_string":
        payload["ts_ms"] = str(payload["ts_ms"])
    elif corruption == "non_string_tag":
        payload["tags"] = [1]
    elif corruption == "embedded_doc_id":
        payload["doc_id"] = "different-embedded-doc"
        identity = {key: value for key, value in payload.items() if key != "hash"}
        payload["hash"] = compute_world_canon_hash(identity)
    else:  # pragma: no cover - parameter table is closed above
        raise AssertionError(corruption)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    corrupted_bytes = path.read_bytes()

    harness.clock.advance(101)
    assert harness.service().recover_pending() == []
    journal = harness.service().get_journal(command.apply_id)
    assert journal["state"] == "RECOVERY_REQUIRED"
    assert journal["reason_code"] == "CANON_APPLY_RECOVERY_DRIFT"
    assert journal["detail"] == "CANON_APPLY_RECOVERY_DRIFT: CORRUPT"
    assert harness.ledger.get_proposal(row["proposal_id"])["status"] == "APPLY_PREPARED"
    assert path.read_bytes() == corrupted_bytes

    conn = sqlite3.connect(harness.db_path)
    try:
        change_count = conn.execute(
            "SELECT COUNT(*) FROM canon_change_log WHERE change_id = ?",
            (f"canon_apply::{command.apply_id}",),
        ).fetchone()[0]
    finally:
        conn.close()
    assert change_count == 0


def test_t_nle_03_before_disposition_rejects_extra_disk_fields(harness: Harness) -> None:
    original = harness.proposal("proposal-before-original", "doc-before-exact")
    original_command, _token, _ = harness.issue_command(
        original, apply_id="before-original"
    )
    harness.service().apply(original_command)

    update = harness.proposal("proposal-before-update", "doc-before-exact")
    update_command, _token, _ = harness.issue_command(update, apply_id="before-update")

    def crash(stage: str, _apply_id: str) -> None:
        if stage == "after_prepare":
            raise CanonApplyCrash()

    with pytest.raises(CanonApplyCrash):
        harness.service(fault_injector=crash).apply(update_command)

    path = harness.world_root / "doc-before-exact.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["unknown"] = "must-not-be-overwritten"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    corrupted_bytes = path.read_bytes()

    harness.clock.advance(101)
    assert harness.service().recover_pending() == []
    journal = harness.service().get_journal(update_command.apply_id)
    assert journal["state"] == "RECOVERY_REQUIRED"
    assert journal["reason_code"] == "CANON_APPLY_RECOVERY_DRIFT"
    assert journal["detail"] == "CANON_APPLY_RECOVERY_DRIFT: CORRUPT"
    assert path.read_bytes() == corrupted_bytes


@pytest.mark.parametrize("operation", ("apply", "rollback"))
@pytest.mark.parametrize("corruption", ("extra_key", "empty_hash", "embedded_doc_id"))
def test_t_nle_03_prepare_freezes_non_exact_existing_canon_before_token_burn(
    harness: Harness,
    operation: str,
    corruption: str,
) -> None:
    target_doc_id = f"doc-freeze-{operation}-{corruption.replace('_', '-')}"
    original = harness.proposal(
        f"proposal-freeze-original-{operation}-{corruption}",
        target_doc_id,
    )
    original_command, _token, _ = harness.issue_command(
        original,
        apply_id=f"freeze-original-{operation}-{corruption}",
    )
    harness.service().apply(original_command)
    document = harness.store.get(target_doc_id)
    assert document is not None

    if operation == "apply":
        subject = harness.proposal(
            f"proposal-freeze-update-{corruption}",
            target_doc_id,
            body="reviewed update",
        )
        command, token, _ = harness.issue_command(
            subject,
            apply_id=f"freeze-update-{corruption}",
        )
        expected_subject_status = "pending"
    else:
        binding = {
            "operation": CANON_OPERATION_ROLLBACK,
            "subject_type": CANON_SUBJECT_PROPOSAL,
            "subject_id": original["proposal_id"],
            "subject_version": document.version,
            "subject_hash": document.hash,
            "target_doc_id": document.doc_id,
            "base_version": document.version,
            "actor_id": "owner",
            "session_id": f"freeze-rollback-{corruption}",
            "trace_id": f"freeze-rollback-{corruption}",
            "evidence_refs": ("audit://rollback/freeze-review",),
        }
        token = harness.issuer().issue_approved_token(**binding)
        command = CanonApplyCommand(
            apply_id=f"freeze-rollback-{corruption}",
            approval_token_ref=token,
            **binding,
        )
        subject = original
        expected_subject_status = "APPLIED"

    path = harness.world_root / f"{target_doc_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if corruption == "extra_key":
        payload["personality_traits_baseline"] = {"warmth": 0.85}
    elif corruption == "empty_hash":
        payload["hash"] = ""
    elif corruption == "embedded_doc_id":
        payload["doc_id"] = f"other-{operation}-doc"
        identity = {key: value for key, value in payload.items() if key != "hash"}
        payload["hash"] = compute_world_canon_hash(identity)
    else:  # pragma: no cover - parameter table is closed above
        raise AssertionError(corruption)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    frozen_bytes = path.read_bytes()
    if corruption == "extra_key":
        assert harness.store.get(target_doc_id).hash == document.hash

    with pytest.raises(CanonApplyError) as error:
        harness.service().apply(command)
    assert error.value.reason_code == "CANON_APPLY_EXISTING_PAYLOAD_INVALID"
    assert error.value.detail == "strict Canon snapshot required"
    assert _token_row(harness, token)["state"] == "AVAILABLE"
    assert harness.ledger.get_proposal(subject["proposal_id"])["status"] == expected_subject_status
    assert harness.service().get_journal(command.apply_id) is None
    assert path.read_bytes() == frozen_bytes

    conn = sqlite3.connect(harness.db_path)
    try:
        change_count = conn.execute(
            "SELECT COUNT(*) FROM canon_change_log WHERE change_id = ?",
            (f"canon_apply::{command.apply_id}",),
        ).fetchone()[0]
    finally:
        conn.close()
    assert change_count == 0


def test_t_nle_03_before_file_crash_and_competing_recovery_have_one_winner(
    harness: Harness,
) -> None:
    row = harness.proposal("proposal-before-crash", "doc-before-crash")
    command, _token, _ = harness.issue_command(row, apply_id="before-crash")

    def crash(stage: str, _apply_id: str) -> None:
        if stage == "after_prepare":
            raise CanonApplyCrash()

    crashed_service = harness.service(fault_injector=crash)
    with pytest.raises(CanonApplyCrash):
        crashed_service.apply(command)
    assert harness.store.get("doc-before-crash") is None
    assert crashed_service.get_journal("before-crash")["state"] == "PREPARED"

    harness.clock.advance(101)
    recoverers = [harness.service(), harness.service()]
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda service: service.recover_pending(), recoverers))
    flattened = [result for batch in outcomes for result in batch]
    assert len(flattened) == 1
    assert flattened[0].state == "APPLIED" and flattened[0].recovered
    assert harness.store.get("doc-before-crash").version == 1

    with pytest.raises(CanonApplyError) as stale:
        crashed_service._finalize("before-crash", 1, harness.clock())
    assert stale.value.reason_code == "CANON_APPLY_STALE_FENCE"
    conn = sqlite3.connect(harness.db_path)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM canon_change_log WHERE change_id = ?",
            ("canon_apply::before-crash",),
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 1


def test_t_nle_03_journal_payload_tamper_never_writes_or_finalizes(
    harness: Harness,
) -> None:
    row = harness.proposal("proposal-journal-tamper", "doc-journal-tamper")
    command, _token, _ = harness.issue_command(row, apply_id="journal-tamper")

    def crash(stage: str, _apply_id: str) -> None:
        if stage == "after_prepare":
            raise CanonApplyCrash()

    with pytest.raises(CanonApplyCrash):
        harness.service(fault_injector=crash).apply(command)
    conn = sqlite3.connect(harness.db_path)
    try:
        raw = conn.execute(
            "SELECT target_payload_json FROM canon_apply_journal WHERE apply_id = ?",
            (command.apply_id,),
        ).fetchone()[0]
        payload = json.loads(raw)
        payload["body"] = "unapproved journal replacement"
        identity = {key: value for key, value in payload.items() if key != "hash"}
        payload["hash"] = compute_world_canon_hash(identity)
        conn.execute(
            "UPDATE canon_apply_journal SET target_payload_json = ? WHERE apply_id = ?",
            (
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                command.apply_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    harness.clock.advance(101)
    assert harness.service().recover_pending() == []
    assert harness.store.get(command.target_doc_id) is None
    journal = harness.service().get_journal(command.apply_id)
    assert journal["state"] == "RECOVERY_REQUIRED"
    assert journal["reason_code"] == "CANON_APPLY_JOURNAL_INTEGRITY_FAILED"
    assert harness.ledger.get_proposal(row["proposal_id"])["status"] == "APPLY_PREPARED"


def test_t_nle_03_proposal_domain_drift_before_prepare_keeps_token_available(
    harness: Harness,
) -> None:
    row = harness.proposal("proposal-domain-before", "doc-domain-before")
    command, token, _ = harness.issue_command(row, apply_id="domain-before")
    conn = sqlite3.connect(harness.db_path)
    try:
        conn.execute(
            "UPDATE canon_proposal_log SET canon_kind = 'style' WHERE proposal_id = ?",
            (row["proposal_id"],),
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(CanonApplyError) as error:
        harness.service().apply(command)
    assert error.value.reason_code == "CANON_APPLY_PROPOSAL_DOMAIN_MISMATCH"
    assert _token_row(harness, token)["state"] == "AVAILABLE"
    assert harness.ledger.get_proposal(row["proposal_id"])["status"] == "pending"
    assert harness.store.get(command.target_doc_id) is None
    assert harness.service().get_journal(command.apply_id) is None


@pytest.mark.parametrize("column", ("base_version", "proposed_version", "ts_ms"))
def test_t_nle_03_real_proposal_integer_tamper_fails_before_burn(
    harness: Harness,
    column: str,
) -> None:
    row = harness.proposal(
        f"proposal-real-{column}",
        f"doc-proposal-real-{column}",
    )
    command, token, _ = harness.issue_command(
        row,
        apply_id=f"proposal-real-{column}",
    )
    _tamper_sqlite_real(
        harness,
        table="canon_proposal_log",
        key_column="proposal_id",
        key_value=row["proposal_id"],
        column=column,
    )

    with pytest.raises(CanonApplyError) as error:
        harness.service().apply(command)

    assert error.value.reason_code == "CANON_APPLY_SUBJECT_INTEGER_INVALID"
    assert _token_row(harness, token)["state"] == "AVAILABLE"
    assert harness.ledger.get_proposal(row["proposal_id"])["status"] == "pending"
    assert harness.store.get(command.target_doc_id) is None
    assert harness.service().get_journal(command.apply_id) is None


def test_t_nle_03_prepared_proposal_domain_drift_is_quarantined(
    harness: Harness,
) -> None:
    row = harness.proposal("proposal-domain-recovery", "doc-domain-recovery")
    command, token, _ = harness.issue_command(row, apply_id="domain-recovery")

    def crash(stage: str, _apply_id: str) -> None:
        if stage == "after_prepare":
            raise CanonApplyCrash()

    with pytest.raises(CanonApplyCrash):
        harness.service(fault_injector=crash).apply(command)

    conn = sqlite3.connect(harness.db_path)
    try:
        conn.execute(
            "UPDATE canon_proposal_log SET canon_kind = 'style' WHERE proposal_id = ?",
            (row["proposal_id"],),
        )
        conn.commit()
    finally:
        conn.close()

    harness.clock.advance(101)
    assert harness.service().recover_pending() == []
    assert harness.store.get(command.target_doc_id) is None
    assert _token_row(harness, token)["state"] == "BURNED"
    assert harness.ledger.get_proposal(row["proposal_id"])["status"] == "APPLY_PREPARED"
    journal = harness.service().get_journal(command.apply_id)
    assert journal["state"] == "RECOVERY_REQUIRED"
    assert journal["reason_code"] == "CANON_APPLY_JOURNAL_INTEGRITY_FAILED"
    assert journal["detail"] == (
        "CANON_APPLY_JOURNAL_INTEGRITY_FAILED: prepared proposal domain drift"
    )

    conn = sqlite3.connect(harness.db_path)
    try:
        change_count = conn.execute(
            "SELECT COUNT(*) FROM canon_change_log WHERE change_id = ?",
            (f"canon_apply::{command.apply_id}",),
        ).fetchone()[0]
    finally:
        conn.close()
    assert change_count == 0


def test_t_nle_03_write_and_finalize_revalidate_journal_integrity(
    harness: Harness,
) -> None:
    write_row = harness.proposal("proposal-write-recheck", "doc-write-recheck")
    write_command, _token, _ = harness.issue_command(
        write_row, apply_id="write-recheck"
    )

    def tamper_after_prepare(stage: str, apply_id: str) -> None:
        if stage != "after_prepare":
            return
        conn = sqlite3.connect(harness.db_path)
        try:
            conn.execute(
                "UPDATE canon_apply_journal SET command_hash = ? WHERE apply_id = ?",
                ("f" * 64, apply_id),
            )
            conn.commit()
        finally:
            conn.close()

    with pytest.raises(CanonApplyError) as write_error:
        harness.service(fault_injector=tamper_after_prepare).apply(write_command)
    assert write_error.value.reason_code == "CANON_APPLY_JOURNAL_INTEGRITY_FAILED"
    assert harness.store.get(write_command.target_doc_id) is None
    assert harness.service().get_journal(write_command.apply_id)["state"] == "FAILED"

    finalize_row = harness.proposal("proposal-finalize-recheck", "doc-finalize-recheck")
    finalize_command, _token, _ = harness.issue_command(
        finalize_row, apply_id="finalize-recheck"
    )

    def tamper_after_replace(stage: str, apply_id: str) -> None:
        if stage != "after_file_replace":
            return
        conn = sqlite3.connect(harness.db_path)
        try:
            conn.execute(
                "UPDATE canon_apply_journal SET command_hash = ? WHERE apply_id = ?",
                ("e" * 64, apply_id),
            )
            conn.commit()
        finally:
            conn.close()

    with pytest.raises(CanonApplyError) as finalize_error:
        harness.service(fault_injector=tamper_after_replace).apply(finalize_command)
    assert finalize_error.value.reason_code == "CANON_APPLY_JOURNAL_INTEGRITY_FAILED"
    assert harness.store.get(finalize_command.target_doc_id).body == "reviewed fact"
    assert harness.service().get_journal(finalize_command.apply_id)["state"] == "RECOVERY_REQUIRED"


def test_t_nle_03_v2_rollback_restores_trusted_v1_apply_chain(harness: Harness) -> None:
    first, _first_apply_id, second, _second_apply_id = _apply_two_proposal_versions(
        harness,
        "valid-rollback",
    )
    command, _token = _issue_rollback_for_proposal(
        harness,
        second,
        apply_id="chain-rollback-valid",
    )

    result = harness.service().apply(command)

    assert result.state == "APPLIED"
    assert result.after_version == 1
    restored = harness.store.get(second["target_id"])
    assert restored is not None
    assert restored.version == 1
    assert restored.hash == first["proposed_hash"]
    assert restored.body == "trusted v1 valid-rollback"
    assert harness.ledger.get_proposal(second["proposal_id"])["status"] == "ROLLED_BACK"


@pytest.mark.parametrize(
    "column",
    (
        "subject_version",
        "base_version",
        "before_version",
        "after_version",
        "fence_version",
        "file_replaced",
        "recovery_not_before_ts_ms",
        "lease_expiry_ts_ms",
        "prepared_ts_ms",
        "updated_ts_ms",
        "completed_ts_ms",
    ),
)
def test_t_nle_03_real_applied_journal_integer_tamper_blocks_rollback_pre_burn(
    harness: Harness,
    column: str,
) -> None:
    _first, _first_apply_id, second, second_apply_id = _apply_two_proposal_versions(
        harness,
        f"applied-real-{column}",
    )
    command, token = _issue_rollback_for_proposal(
        harness,
        second,
        apply_id=f"rollback-applied-real-{column}",
    )
    path = harness.world_root / f"{second['target_id']}.json"
    v2_bytes = path.read_bytes()
    _tamper_sqlite_real(
        harness,
        table="canon_apply_journal",
        key_column="apply_id",
        key_value=second_apply_id,
        column=column,
    )

    with pytest.raises(CanonApplyError) as error:
        harness.service().apply(command)

    assert error.value.reason_code == "CANON_APPLY_JOURNAL_INTEGRITY_FAILED"
    assert _token_row(harness, token)["state"] == "AVAILABLE"
    assert harness.ledger.get_proposal(second["proposal_id"])["status"] == "APPLIED"
    assert harness.service().get_journal(command.apply_id) is None
    assert path.read_bytes() == v2_bytes


@pytest.mark.parametrize("column", ("base_version", "proposed_version", "ts_ms"))
def test_t_nle_03_real_historical_proposal_integer_tamper_blocks_rollback(
    harness: Harness,
    column: str,
) -> None:
    _first, _first_apply_id, second, _second_apply_id = _apply_two_proposal_versions(
        harness,
        f"historical-proposal-real-{column}",
    )
    command, token = _issue_rollback_for_proposal(
        harness,
        second,
        apply_id=f"rollback-historical-proposal-real-{column}",
    )
    path = harness.world_root / f"{second['target_id']}.json"
    v2_bytes = path.read_bytes()
    _tamper_sqlite_real(
        harness,
        table="canon_proposal_log",
        key_column="proposal_id",
        key_value=second["proposal_id"],
        column=column,
    )

    with pytest.raises(CanonApplyError) as error:
        harness.service().apply(command)

    assert error.value.reason_code == "CANON_APPLY_JOURNAL_INTEGRITY_FAILED"
    assert _token_row(harness, token)["state"] == "AVAILABLE"
    assert harness.ledger.get_proposal(second["proposal_id"])["status"] == "APPLIED"
    assert harness.service().get_journal(command.apply_id) is None
    assert path.read_bytes() == v2_bytes


def test_t_nle_03_rollback_rejects_tampered_source_before_snapshot_pre_burn(
    harness: Harness,
) -> None:
    _first, _first_apply_id, second, second_apply_id = _apply_two_proposal_versions(
        harness,
        "tamper-before",
    )
    command, token = _issue_rollback_for_proposal(
        harness,
        second,
        apply_id="chain-rollback-tamper-before",
    )
    path = harness.world_root / f"{second['target_id']}.json"
    original_bytes = path.read_bytes()
    _tamper_applied_before_snapshot(
        harness,
        source_apply_id=second_apply_id,
        forged_body="forged v1 before rollback prepare",
    )

    with pytest.raises(CanonApplyError) as error:
        harness.service().apply(command)

    assert error.value.reason_code == "CANON_APPLY_JOURNAL_INTEGRITY_FAILED"
    assert error.value.detail == "historical apply predecessor untrusted"
    assert _token_row(harness, token)["state"] == "AVAILABLE"
    assert harness.ledger.get_proposal(second["proposal_id"])["status"] == "APPLIED"
    assert harness.service().get_journal(command.apply_id) is None
    assert path.read_bytes() == original_bytes


def test_t_nle_03_rollback_recovery_quarantines_late_source_before_tamper(
    harness: Harness,
) -> None:
    _first, _first_apply_id, second, second_apply_id = _apply_two_proposal_versions(
        harness,
        "tamper-recovery",
    )
    command, token = _issue_rollback_for_proposal(
        harness,
        second,
        apply_id="chain-rollback-tamper-recovery",
    )

    def crash(stage: str, _apply_id: str) -> None:
        if stage == "after_prepare":
            raise CanonApplyCrash()

    with pytest.raises(CanonApplyCrash):
        harness.service(fault_injector=crash).apply(command)
    path = harness.world_root / f"{second['target_id']}.json"
    original_bytes = path.read_bytes()
    _tamper_applied_before_snapshot(
        harness,
        source_apply_id=second_apply_id,
        forged_body="forged v1 after rollback prepare",
    )

    harness.clock.advance(101)
    assert harness.service().recover_pending() == []
    assert path.read_bytes() == original_bytes
    assert _token_row(harness, token)["state"] == "BURNED"
    assert harness.ledger.get_proposal(second["proposal_id"])["status"] == (
        "ROLLBACK_PREPARED"
    )
    journal = harness.service().get_journal(command.apply_id)
    assert journal["state"] == "RECOVERY_REQUIRED"
    assert journal["reason_code"] == "CANON_APPLY_JOURNAL_INTEGRITY_FAILED"
    assert journal["detail"] == (
        "CANON_APPLY_JOURNAL_INTEGRITY_FAILED: "
        "historical apply predecessor untrusted"
    )


def test_t_nle_03_rollback_rejects_legacy_before_without_trusted_predecessor(
    harness: Harness,
) -> None:
    target_doc_id = "doc-chain-legacy-before"
    legacy_payload: dict[str, object] = {
        "doc_id": target_doc_id,
        "version": 1,
        "status": "active",
        "title": "Legacy v1",
        "tags": ["legacy"],
        "body": "legacy v1 without governed apply lineage",
        "updated_by": "legacy-import",
        "ts_ms": harness.clock(),
    }
    legacy_payload["hash"] = compute_world_canon_hash(legacy_payload)
    path = harness.world_root / f"{target_doc_id}.json"
    path.write_text(
        json.dumps(legacy_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    second = harness.proposal(
        "proposal-chain-legacy-v2",
        target_doc_id,
        body="trusted v2 over legacy v1",
    )
    second_command, _token, _ = harness.issue_command(
        second,
        apply_id="chain-legacy-v2",
    )
    harness.service().apply(second_command)
    command, token = _issue_rollback_for_proposal(
        harness,
        second,
        apply_id="chain-legacy-rollback",
    )
    v2_bytes = path.read_bytes()

    with pytest.raises(CanonApplyError) as error:
        harness.service().apply(command)

    assert error.value.reason_code == "CANON_APPLY_JOURNAL_INTEGRITY_FAILED"
    assert error.value.detail == "historical apply predecessor untrusted"
    assert _token_row(harness, token)["state"] == "AVAILABLE"
    assert harness.ledger.get_proposal(second["proposal_id"])["status"] == "APPLIED"
    assert harness.service().get_journal(command.apply_id) is None
    assert path.read_bytes() == v2_bytes


def test_t_nle_03_crash_after_delete_recovers_without_tombstone(harness: Harness) -> None:
    row = harness.proposal("proposal-delete", "doc-delete")
    apply_command, _token, _ = harness.issue_command(row, apply_id="delete-apply")
    harness.service().apply(apply_command)
    document = harness.store.get("doc-delete")
    rollback_binding = {
        "operation": CANON_OPERATION_ROLLBACK,
        "subject_type": CANON_SUBJECT_PROPOSAL,
        "subject_id": row["proposal_id"],
        "subject_version": document.version,
        "subject_hash": document.hash,
        "target_doc_id": document.doc_id,
        "base_version": document.version,
        "actor_id": "owner",
        "session_id": "session-rollback",
        "trace_id": "trace-rollback",
        "evidence_refs": ("audit://rollback/review",),
    }
    token = harness.issuer().issue_approved_token(**rollback_binding)
    rollback = CanonApplyCommand(
        apply_id="delete-rollback", approval_token_ref=token, **rollback_binding
    )

    def crash(stage: str, _apply_id: str) -> None:
        if stage == "after_file_replace":
            raise CanonApplyCrash()

    with pytest.raises(CanonApplyCrash):
        harness.service(fault_injector=crash).apply(rollback)
    assert harness.store.get("doc-delete") is None
    assert list(harness.world_root.glob("*.deleted")) == []
    harness.clock.advance(101)
    recovered = harness.service().recover_pending()
    assert len(recovered) == 1 and recovered[0].after_version == 0
    assert harness.ledger.get_proposal(row["proposal_id"])["status"] == "ROLLED_BACK"


def test_t_nle_03_delete_rollback_journal_tamper_keeps_approved_document(
    harness: Harness,
) -> None:
    row = harness.proposal("proposal-delete-tamper", "doc-delete-tamper")
    apply_command, _token, _ = harness.issue_command(row, apply_id="delete-tamper-apply")
    harness.service().apply(apply_command)
    document = harness.store.get(row["target_id"])
    binding = {
        "operation": CANON_OPERATION_ROLLBACK,
        "subject_type": CANON_SUBJECT_PROPOSAL,
        "subject_id": row["proposal_id"],
        "subject_version": document.version,
        "subject_hash": document.hash,
        "target_doc_id": document.doc_id,
        "base_version": document.version,
        "actor_id": "owner",
        "session_id": "delete-tamper-session",
        "trace_id": "delete-tamper-trace",
        "evidence_refs": ("audit://rollback/tamper-review",),
    }
    token = harness.issuer().issue_approved_token(**binding)
    command = CanonApplyCommand(
        apply_id="delete-tamper-rollback",
        approval_token_ref=token,
        **binding,
    )

    def crash(stage: str, _apply_id: str) -> None:
        if stage == "after_prepare":
            raise CanonApplyCrash()

    with pytest.raises(CanonApplyCrash):
        harness.service(fault_injector=crash).apply(command)
    malicious: dict[str, object] = {
        "doc_id": document.doc_id,
        "version": 1,
        "status": "active",
        "title": "unapproved rollback target",
        "tags": [],
        "body": "unapproved rollback body",
        "updated_by": row["proposal_id"],
        "ts_ms": harness.clock(),
    }
    malicious["hash"] = compute_world_canon_hash(malicious)
    conn = sqlite3.connect(harness.db_path)
    try:
        conn.execute(
            "UPDATE canon_apply_journal SET target_payload_json = ? WHERE apply_id = ?",
            (
                json.dumps(malicious, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                command.apply_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    harness.clock.advance(101)
    assert harness.service().recover_pending() == []
    assert harness.store.get(document.doc_id).hash == document.hash
    journal = harness.service().get_journal(command.apply_id)
    assert journal["state"] == "RECOVERY_REQUIRED"
    assert journal["reason_code"] == "CANON_APPLY_JOURNAL_INTEGRITY_FAILED"


def _promotion(harness: Harness, candidate_id_suffix: str = ""):
    with PentestKnowledgeStore(harness.db_path) as store:
        entry = PentestKnowledgeEntry.create(
            entry_id=f"pk-promotion{candidate_id_suffix}",
            knowledge_type="technique",
            title="Reviewed technique",
            summary="Reviewed summary",
            payload={"method": "safe lab technique"},
            source_ref="lab://knowledge/source",
            evidence_refs=["audit://pentest/evidence"],
            tags=["lab"],
            created_ts_ms=harness.clock(),
        )
        store.upsert_entry(entry)
        return store.create_promotion_candidate(entry, reason="owner reviewed")


@pytest.mark.parametrize(
    ("table", "column"),
    (
        ("pentest_knowledge_promotion_candidate", "created_ts_ms"),
        ("pentest_knowledge_entry", "created_ts_ms"),
        ("pentest_knowledge_entry", "updated_ts_ms"),
    ),
)
def test_t_nle_03_real_promotion_lineage_integer_tamper_fails_before_burn(
    harness: Harness,
    table: str,
    column: str,
) -> None:
    suffix = f"-real-{table.split('_')[-1]}-{column}"
    candidate = _promotion(harness, suffix)
    service = harness.service()
    binding = {
        "operation": CANON_OPERATION_APPLY,
        "subject_type": CANON_SUBJECT_PENTEST_PROMOTION,
        "subject_id": candidate.candidate_id,
        "subject_version": 1,
        "subject_hash": service.get_promotion_subject_hash(candidate.candidate_id),
        "target_doc_id": f"promotion-real-{table.split('_')[-1]}-{column}",
        "base_version": 0,
        "actor_id": "owner",
        "session_id": f"promotion-real-{column}",
        "trace_id": f"promotion-real-{column}",
        "evidence_refs": ("audit://promotion/real-integer",),
    }
    token = harness.issuer().issue_approved_token(**binding)
    command = CanonApplyCommand(
        apply_id=f"promotion-real-{table.split('_')[-1]}-{column}",
        approval_token_ref=token,
        **binding,
    )
    if table == "pentest_knowledge_promotion_candidate":
        key_column = "candidate_id"
        key_value = candidate.candidate_id
        expected_reason = "CANON_APPLY_PROMOTION_PAYLOAD_INVALID"
    else:
        key_column = "entry_id"
        key_value = candidate.entry_id
        expected_reason = "CANON_APPLY_PROMOTION_SOURCE_DRIFT"
    _tamper_sqlite_real(
        harness,
        table=table,
        key_column=key_column,
        key_value=key_value,
        column=column,
    )

    with pytest.raises(CanonApplyError) as error:
        service.apply(command)

    assert error.value.reason_code == expected_reason
    assert _token_row(harness, token)["state"] == "AVAILABLE"
    conn = sqlite3.connect(harness.db_path)
    try:
        status = conn.execute(
            "SELECT status FROM pentest_knowledge_promotion_candidate WHERE candidate_id = ?",
            (candidate.candidate_id,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert status == "PENDING_REVIEW"
    assert harness.store.get(command.target_doc_id) is None
    assert service.get_journal(command.apply_id) is None


def _apply_promotion_v1(
    harness: Harness,
    *,
    suffix: str,
    target_doc_id: str,
):
    candidate = _promotion(harness, suffix)
    service = harness.service()
    binding = {
        "operation": CANON_OPERATION_APPLY,
        "subject_type": CANON_SUBJECT_PENTEST_PROMOTION,
        "subject_id": candidate.candidate_id,
        "subject_version": 1,
        "subject_hash": service.get_promotion_subject_hash(candidate.candidate_id),
        "target_doc_id": target_doc_id,
        "base_version": 0,
        "actor_id": "owner",
        "session_id": f"promotion-chain-{suffix}",
        "trace_id": f"promotion-chain-{suffix}",
        "evidence_refs": ("audit://promotion/chain-review",),
    }
    token = harness.issuer().issue_approved_token(**binding)
    service.apply(
        CanonApplyCommand(
            apply_id=f"promotion-chain-v1-{suffix}",
            approval_token_ref=token,
            **binding,
        )
    )
    return candidate


def test_t_nle_03_rollback_accepts_verified_promotion_as_v1_predecessor(
    harness: Harness,
) -> None:
    target_doc_id = "promotion-chain-valid"
    candidate = _apply_promotion_v1(
        harness,
        suffix="-chain-valid",
        target_doc_id=target_doc_id,
    )
    second = harness.proposal(
        "proposal-after-promotion-valid",
        target_doc_id,
        body="trusted proposal v2 after promotion",
    )
    second_command, _token, _ = harness.issue_command(
        second,
        apply_id="proposal-after-promotion-valid",
    )
    harness.service().apply(second_command)
    rollback, _token = _issue_rollback_for_proposal(
        harness,
        second,
        apply_id="rollback-to-promotion-valid",
    )

    result = harness.service().apply(rollback)

    assert result.state == "APPLIED"
    assert result.after_version == 1
    restored = harness.store.get(target_doc_id)
    assert restored is not None
    assert restored.version == 1
    assert restored.updated_by == candidate.candidate_id
    assert candidate.entry_id in restored.body


@pytest.mark.parametrize(
    ("table", "column"),
    (
        ("pentest_knowledge_promotion_candidate", "created_ts_ms"),
        ("pentest_knowledge_entry", "created_ts_ms"),
        ("pentest_knowledge_entry", "updated_ts_ms"),
    ),
)
def test_t_nle_03_real_historical_promotion_integer_tamper_blocks_rollback(
    harness: Harness,
    table: str,
    column: str,
) -> None:
    suffix = f"historical-real-{table.split('_')[-1]}-{column}"
    target_doc_id = f"promotion-chain-{suffix}"
    candidate = _apply_promotion_v1(
        harness,
        suffix=f"-{suffix}",
        target_doc_id=target_doc_id,
    )
    second = harness.proposal(
        f"proposal-after-{suffix}",
        target_doc_id,
        body=f"trusted proposal v2 {suffix}",
    )
    second_command, _token, _ = harness.issue_command(
        second,
        apply_id=f"proposal-after-{suffix}",
    )
    harness.service().apply(second_command)
    rollback, token = _issue_rollback_for_proposal(
        harness,
        second,
        apply_id=f"rollback-{suffix}",
    )
    path = harness.world_root / f"{target_doc_id}.json"
    v2_bytes = path.read_bytes()
    if table == "pentest_knowledge_promotion_candidate":
        key_column = "candidate_id"
        key_value = candidate.candidate_id
    else:
        key_column = "entry_id"
        key_value = candidate.entry_id
    _tamper_sqlite_real(
        harness,
        table=table,
        key_column=key_column,
        key_value=key_value,
        column=column,
    )

    with pytest.raises(CanonApplyError) as error:
        harness.service().apply(rollback)

    assert error.value.reason_code == "CANON_APPLY_JOURNAL_INTEGRITY_FAILED"
    assert _token_row(harness, token)["state"] == "AVAILABLE"
    assert harness.ledger.get_proposal(second["proposal_id"])["status"] == "APPLIED"
    assert harness.service().get_journal(rollback.apply_id) is None
    assert path.read_bytes() == v2_bytes


def test_t_nle_03_rollback_rejects_unverified_promotion_predecessor(
    harness: Harness,
) -> None:
    target_doc_id = "promotion-chain-untrusted"
    candidate = _apply_promotion_v1(
        harness,
        suffix="-chain-untrusted",
        target_doc_id=target_doc_id,
    )
    second = harness.proposal(
        "proposal-after-promotion-untrusted",
        target_doc_id,
        body="trusted proposal v2 after promotion",
    )
    second_command, _token, _ = harness.issue_command(
        second,
        apply_id="proposal-after-promotion-untrusted",
    )
    harness.service().apply(second_command)
    rollback, token = _issue_rollback_for_proposal(
        harness,
        second,
        apply_id="rollback-to-promotion-untrusted",
    )
    path = harness.world_root / f"{target_doc_id}.json"
    v2_bytes = path.read_bytes()

    conn = sqlite3.connect(harness.db_path)
    try:
        conn.execute(
            "UPDATE pentest_knowledge_entry SET source_hash = ? WHERE entry_id = ?",
            ("f" * 64, candidate.entry_id),
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(CanonApplyError) as error:
        harness.service().apply(rollback)

    assert error.value.reason_code == "CANON_APPLY_JOURNAL_INTEGRITY_FAILED"
    assert error.value.detail == "historical apply predecessor untrusted"
    assert _token_row(harness, token)["state"] == "AVAILABLE"
    assert harness.ledger.get_proposal(second["proposal_id"])["status"] == "APPLIED"
    assert harness.service().get_journal(rollback.apply_id) is None
    assert path.read_bytes() == v2_bytes


def test_t_nle_03_promotion_full_payload_hash_and_status_cas(harness: Harness) -> None:
    candidate = _promotion(harness, "-ok")
    service = harness.service()
    subject_hash = service.get_promotion_subject_hash(candidate.candidate_id)
    binding = {
        "operation": CANON_OPERATION_APPLY,
        "subject_type": CANON_SUBJECT_PENTEST_PROMOTION,
        "subject_id": candidate.candidate_id,
        "subject_version": 1,
        "subject_hash": subject_hash,
        "target_doc_id": "promoted-doc",
        "base_version": 0,
        "actor_id": "owner",
        "session_id": "promotion-session",
        "trace_id": "promotion-trace",
        "evidence_refs": ("audit://promotion/review",),
    }
    token = harness.issuer().issue_approved_token(**binding)
    result = service.apply(
        CanonApplyCommand(apply_id="promotion-ok", approval_token_ref=token, **binding)
    )
    assert result.state == "APPLIED"
    conn = sqlite3.connect(harness.db_path)
    try:
        status = conn.execute(
            "SELECT status FROM pentest_knowledge_promotion_candidate WHERE candidate_id = ?",
            (candidate.candidate_id,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert status == "APPLIED"

    tampered = _promotion(harness, "-tamper")
    tamper_hash = service.get_promotion_subject_hash(tampered.candidate_id)
    tamper_binding = {**binding, "subject_id": tampered.candidate_id, "subject_hash": tamper_hash, "target_doc_id": "tampered-doc"}
    tamper_token = harness.issuer().issue_approved_token(**tamper_binding)
    conn = sqlite3.connect(harness.db_path)
    try:
        raw = conn.execute(
            "SELECT payload_json FROM pentest_knowledge_promotion_candidate WHERE candidate_id = ?",
            (tampered.candidate_id,),
        ).fetchone()[0]
        payload = json.loads(raw)
        payload["title"] = "replacement after approval"
        conn.execute(
            "UPDATE pentest_knowledge_promotion_candidate SET payload_json = ? WHERE candidate_id = ?",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")), tampered.candidate_id),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(CanonApplyError) as tamper_error:
        service.apply(
            CanonApplyCommand(
                apply_id="promotion-tamper",
                approval_token_ref=tamper_token,
                **tamper_binding,
            )
        )
    assert tamper_error.value.reason_code == "CANON_APPLY_SUBJECT_BINDING_MISMATCH"
    assert _token_row(harness, tamper_token)["state"] == "AVAILABLE"


def test_t_nle_03_promotion_journal_payload_tamper_is_quarantined(
    harness: Harness,
) -> None:
    candidate = _promotion(harness, "-journal-tamper")
    service = harness.service()
    binding = {
        "operation": CANON_OPERATION_APPLY,
        "subject_type": CANON_SUBJECT_PENTEST_PROMOTION,
        "subject_id": candidate.candidate_id,
        "subject_version": 1,
        "subject_hash": service.get_promotion_subject_hash(candidate.candidate_id),
        "target_doc_id": "promotion-journal-tamper",
        "base_version": 0,
        "actor_id": "owner",
        "session_id": "promotion-journal-session",
        "trace_id": "promotion-journal-trace",
        "evidence_refs": ("audit://promotion/journal-review",),
    }
    token = harness.issuer().issue_approved_token(**binding)
    command = CanonApplyCommand(
        apply_id="promotion-journal-tamper",
        approval_token_ref=token,
        **binding,
    )

    def crash(stage: str, _apply_id: str) -> None:
        if stage == "after_prepare":
            raise CanonApplyCrash()

    with pytest.raises(CanonApplyCrash):
        service_with_crash = harness.service(fault_injector=crash)
        service_with_crash.apply(command)
    conn = sqlite3.connect(harness.db_path)
    try:
        raw = conn.execute(
            "SELECT target_payload_json FROM canon_apply_journal WHERE apply_id = ?",
            (command.apply_id,),
        ).fetchone()[0]
        payload = json.loads(raw)
        payload["body"] = "unapproved promoted body"
        identity = {key: value for key, value in payload.items() if key != "hash"}
        payload["hash"] = compute_world_canon_hash(identity)
        conn.execute(
            "UPDATE canon_apply_journal SET target_payload_json = ? WHERE apply_id = ?",
            (
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                command.apply_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    harness.clock.advance(101)
    assert harness.service().recover_pending() == []
    assert harness.store.get(command.target_doc_id) is None
    journal = harness.service().get_journal(command.apply_id)
    assert journal["state"] == "RECOVERY_REQUIRED"
    assert journal["reason_code"] == "CANON_APPLY_JOURNAL_INTEGRITY_FAILED"


@pytest.mark.parametrize(
    ("lineage_tamper", "expected_detail"),
    (
        (
            "source_hash",
            "CANON_APPLY_JOURNAL_INTEGRITY_FAILED: promotion source lineage drift",
        ),
        (
            "delete_entry",
            "CANON_APPLY_JOURNAL_INTEGRITY_FAILED: promotion source entry missing",
        ),
    ),
)
def test_t_nle_03_promotion_recovery_revalidates_source_entry_lineage(
    harness: Harness,
    lineage_tamper: str,
    expected_detail: str,
) -> None:
    candidate = _promotion(harness, f"-lineage-{lineage_tamper}")
    service = harness.service()
    binding = {
        "operation": CANON_OPERATION_APPLY,
        "subject_type": CANON_SUBJECT_PENTEST_PROMOTION,
        "subject_id": candidate.candidate_id,
        "subject_version": 1,
        "subject_hash": service.get_promotion_subject_hash(candidate.candidate_id),
        "target_doc_id": f"promotion-lineage-{lineage_tamper.replace('_', '-')}",
        "base_version": 0,
        "actor_id": "owner",
        "session_id": f"promotion-lineage-{lineage_tamper}",
        "trace_id": f"promotion-lineage-{lineage_tamper}",
        "evidence_refs": ("audit://promotion/lineage-review",),
    }
    token = harness.issuer().issue_approved_token(**binding)
    command = CanonApplyCommand(
        apply_id=f"promotion-lineage-{lineage_tamper}",
        approval_token_ref=token,
        **binding,
    )

    def crash(stage: str, _apply_id: str) -> None:
        if stage == "after_prepare":
            raise CanonApplyCrash()

    with pytest.raises(CanonApplyCrash):
        harness.service(fault_injector=crash).apply(command)

    conn = sqlite3.connect(harness.db_path)
    try:
        if lineage_tamper == "source_hash":
            conn.execute(
                "UPDATE pentest_knowledge_entry SET source_hash = ? WHERE entry_id = ?",
                ("f" * 64, candidate.entry_id),
            )
        else:
            conn.execute(
                "DELETE FROM pentest_knowledge_entry WHERE entry_id = ?",
                (candidate.entry_id,),
            )
        conn.commit()
    finally:
        conn.close()

    harness.clock.advance(101)
    assert harness.service().recover_pending() == []
    assert harness.store.get(command.target_doc_id) is None
    journal = harness.service().get_journal(command.apply_id)
    assert journal["state"] == "RECOVERY_REQUIRED"
    assert journal["reason_code"] == "CANON_APPLY_JOURNAL_INTEGRITY_FAILED"
    assert journal["detail"] == expected_detail

    conn = sqlite3.connect(harness.db_path)
    try:
        candidate_status = conn.execute(
            "SELECT status FROM pentest_knowledge_promotion_candidate WHERE candidate_id = ?",
            (candidate.candidate_id,),
        ).fetchone()[0]
        change_count = conn.execute(
            "SELECT COUNT(*) FROM canon_change_log WHERE change_id = ?",
            (f"canon_apply::{command.apply_id}",),
        ).fetchone()[0]
    finally:
        conn.close()
    assert candidate_status == "APPROVED"
    assert change_count == 0


def test_t_nle_03_cross_db_promotion_is_explicitly_rejected(
    harness: Harness, tmp_path: Path
) -> None:
    other_db = (tmp_path / "other-promotion.sqlite3").resolve()
    service = CanonApplyService(
        world_root=harness.world_root,
        ledger=harness.ledger,
        promotion_db_path=other_db,
        clock=harness.clock,
        trusted_approval_keys={harness.key_id: harness.private_key.public_key()},
        degradation_capability_port=GlobalDegradationTestPort(),
    )
    binding = {
        "operation": CANON_OPERATION_APPLY,
        "subject_type": CANON_SUBJECT_PENTEST_PROMOTION,
        "subject_id": "candidate-other-db",
        "subject_version": 1,
        "subject_hash": "a" * 64,
        "target_doc_id": "cross-db-doc",
        "base_version": 0,
        "actor_id": "owner",
        "session_id": "session",
        "trace_id": "trace",
        "evidence_refs": ("audit://cross-db/review",),
    }
    token = harness.issuer().issue_approved_token(**binding)
    with pytest.raises(CanonApplyError) as error:
        service.apply(
            CanonApplyCommand(apply_id="cross-db", approval_token_ref=token, **binding)
        )
    assert error.value.reason_code == "CANON_APPLY_CROSS_DB_PROMOTION_UNSUPPORTED"


def test_t_nle_03_attestation_untrusted_invalid_signature_future_and_token_swap(
    harness: Harness,
) -> None:
    row = harness.proposal("proposal-attest", "doc-attest")
    rogue_key = Ed25519PrivateKey.generate()
    rogue_issuer = harness.issuer(private_key=rogue_key, key_id="rogue-key")
    rogue_command, _rogue_token, _ = harness.issue_command(
        row, apply_id="rogue", issuer=rogue_issuer
    )
    with pytest.raises(CanonApplyError) as rogue_error:
        harness.service().apply(rogue_command)
    assert rogue_error.value.reason_code == "CANON_APPLY_APPROVAL_AUTHORITY_UNTRUSTED"

    invalid_command, invalid_token, _ = harness.issue_command(row, apply_id="invalid-signature")
    conn = sqlite3.connect(harness.db_path)
    try:
        conn.execute(
            "UPDATE canon_apply_token SET approval_signature = ? WHERE token_digest = ?",
            ("!" * 88, canon_token_digest(invalid_token)),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(CanonApplyError) as signature_error:
        harness.service().apply(invalid_command)
    assert signature_error.value.reason_code == "CANON_APPLY_APPROVAL_SIGNATURE_INVALID"

    short_command, short_token, _ = harness.issue_command(row, apply_id="short-signature")
    conn = sqlite3.connect(harness.db_path)
    try:
        conn.execute(
            "UPDATE canon_apply_token SET approval_signature = ? WHERE token_digest = ?",
            (base64.b64encode(b"x" * 60).decode("ascii"), canon_token_digest(short_token)),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(CanonApplyError) as short_error:
        harness.service().apply(short_command)
    assert short_error.value.reason_code == "CANON_APPLY_APPROVAL_SIGNATURE_INVALID"

    decision_command, decision_token, _ = harness.issue_command(row, apply_id="bad-decision")
    conn = sqlite3.connect(harness.db_path)
    conn.row_factory = sqlite3.Row
    try:
        signed = conn.execute(
            "SELECT approval_attestation_json FROM canon_apply_token WHERE token_digest = ?",
            (canon_token_digest(decision_token),),
        ).fetchone()
        attestation = json.loads(signed["approval_attestation_json"])
        attestation["decision"] = "REJECT"
        canonical = json.dumps(
            attestation, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        conn.execute(
            """
            UPDATE canon_apply_token
            SET approval_attestation_json = ?, approval_attestation_digest = ?,
                approval_signature = ?
            WHERE token_digest = ?
            """,
            (
                canonical,
                hashlib.sha256(canonical.encode()).hexdigest(),
                base64.b64encode(harness.private_key.sign(canonical.encode())).decode("ascii"),
                canon_token_digest(decision_token),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(CanonApplyError) as decision_error:
        harness.service().apply(decision_command)
    assert decision_error.value.reason_code == "CANON_APPLY_APPROVAL_ATTESTATION_BINDING_MISMATCH"

    future_clock = Clock(harness.clock() + 1000)
    future_issuer = harness.issuer(clock=future_clock)
    future_command, _future_token, _ = harness.issue_command(
        row, apply_id="future", issuer=future_issuer
    )
    with pytest.raises(CanonApplyError) as future_error:
        harness.service().apply(future_command)
    assert future_error.value.reason_code == "CANON_APPLY_APPROVAL_ATTESTATION_FROM_FUTURE"

    swap_command, swap_token, binding = harness.issue_command(row, apply_id="swap")
    replacement_bearer = "canon::" + "f" * 64
    conn = sqlite3.connect(harness.db_path)
    try:
        conn.execute(
            "UPDATE canon_apply_token SET token_digest = ? WHERE token_digest = ?",
            (canon_token_digest(replacement_bearer), canon_token_digest(swap_token)),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(CanonApplyError) as swap_error:
        harness.service().apply(
            CanonApplyCommand(
                apply_id="swap",
                approval_token_ref=replacement_bearer,
                **binding,
            )
        )
    assert swap_error.value.reason_code == "CANON_APPLY_APPROVAL_ATTESTATION_BINDING_MISMATCH"


def test_t_nle_03_invalid_signature_precedes_state_expiry_and_binding_oracles(
    harness: Harness,
) -> None:
    row = harness.proposal("proposal-signature-oracle", "doc-signature-oracle")
    command, token, _ = harness.issue_command(row, apply_id="signature-oracle")
    conn = sqlite3.connect(harness.db_path)
    try:
        conn.execute(
            """
            UPDATE canon_apply_token
            SET approval_signature = ?, state = 'BURNED',
                burned_apply_id = 'hidden-apply', burned_ts_ms = ?
            WHERE token_digest = ?
            """,
            ("!" * 88, harness.clock(), canon_token_digest(token)),
        )
        conn.commit()
    finally:
        conn.close()
    harness.clock.advance(100_000)
    for probe in (
        command,
        replace(command, session_id="wrong-session"),
        replace(command, subject_version=command.subject_version + 1),
    ):
        with pytest.raises(CanonApplyError) as error:
            harness.service().apply(probe)
        assert error.value.reason_code == "CANON_APPLY_APPROVAL_SIGNATURE_INVALID"


def test_t_nle_03_nonce_is_unique_and_runtime_has_no_issuer(harness: Harness) -> None:
    row = harness.proposal("proposal-nonce", "doc-nonce")
    _first, token_one, binding = harness.issue_command(row, apply_id="nonce-one")
    token_two = harness.issuer().issue_approved_token(**binding)
    nonce = _token_row(harness, token_one)["approval_nonce"]
    conn = sqlite3.connect(harness.db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE canon_apply_token SET approval_nonce = ? WHERE token_digest = ?",
                (nonce, canon_token_digest(token_two)),
            )
    finally:
        conn.close()
    service = harness.service()
    assert not hasattr(service, "token_service")
    assert not hasattr(service, "private_key")


def test_t_nle_03_canon_and_db_paths_do_not_follow_later_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected_root = default_world_canon_root()
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    assert default_world_canon_root() == expected_root
    assert resolve_world_canon_root() == expected_root
    with pytest.raises(ValueError, match="WORLD_ROOT_MUST_BE_ABSOLUTE"):
        resolve_world_canon_root("host_brain/canon/world")

    original = tmp_path / "original"
    original.mkdir()
    monkeypatch.chdir(original)
    ledger = SQLiteLedger("data/audit.sqlite3", check_same_thread=False)
    frozen = Path(ledger.canonical_db_path)
    _install_canon_schema(frozen)
    monkeypatch.chdir(other)
    key = Ed25519PrivateKey.generate()
    service = CanonApplyService(
        world_root=(tmp_path / "world").resolve(),
        ledger=ledger,
        trusted_approval_keys={"key": key.public_key()},
        degradation_capability_port=GlobalDegradationTestPort(),
    )
    assert Path(service.db_path) == frozen
    with pytest.raises(ValueError, match="ABSOLUTE_PATH_REQUIRED"):
        CanonApprovalTokenIssuer(
            "relative.sqlite3",
            authority_key_id="key",
            private_key=key,
            reviewer_id="owner",
        )
    with pytest.raises(ValueError, match="DURABLE_DB_REQUIRED"):
        CanonApprovalTokenIssuer(
            "file:/tmp/a.sqlite3",
            authority_key_id="key",
            private_key=key,
            reviewer_id="owner",
        )
    with pytest.raises(ValueError, match="DURABLE_DB_REQUIRED"):
        CanonApprovalTokenIssuer(
            ":memory:",
            authority_key_id="key",
            private_key=key,
            reviewer_id="owner",
        )
    monkeypatch.setenv(
        "NATSUME_CANON_APPROVAL_PUBLIC_KEYS_JSON", '{"bad-key":"!!!!"}'
    )
    with pytest.raises(ValueError, match="CANON_APPLY_APPROVAL_KEYRING_INVALID"):
        CanonApplyService(
            world_root=(tmp_path / "another-world").resolve(),
            ledger=ledger,
        )


def test_t_nle_03_schema_readiness_is_read_only_central_and_exact(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()

    empty = (tmp_path / "empty.sqlite3").resolve()
    with pytest.raises(RuntimeError, match="central_migration_missing"):
        CanonApprovalTokenIssuer(
            empty,
            authority_key_id="key",
            private_key=key,
            reviewer_id="owner",
        )
    conn = sqlite3.connect(empty)
    try:
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE 'canon_apply_%'"
        ).fetchall() == []
    finally:
        conn.close()

    valid = (tmp_path / "valid.sqlite3").resolve()
    _install_canon_schema(valid)
    issuer = CanonApprovalTokenIssuer(
        valid,
        authority_key_id="key",
        private_key=key,
        reviewer_id="owner",
    )
    assert issuer.migration_asset_path().name == "0053_canon_apply.sql"

    canonical_sql = CANON_MIGRATION.read_text(encoding="utf-8-sig")
    half = (tmp_path / "half.sqlite3").resolve()
    conn = sqlite3.connect(half)
    try:
        conn.execute(
            "CREATE TABLE schema_migrations(migration_id INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL, applied_ts_ms INTEGER NOT NULL)"
        )
        conn.execute("CREATE TABLE canon_apply_token(token_digest TEXT PRIMARY KEY)")
        conn.execute(
            "INSERT INTO schema_migrations VALUES (53, 'canon_apply', ?, 1)",
            (hashlib.sha256(canonical_sql.encode("utf-8")).hexdigest(),),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(RuntimeError, match="schema_fingerprint"):
        CanonApprovalTokenIssuer(
            half,
            authority_key_id="key",
            private_key=key,
            reviewer_id="owner",
        )

    drift_paths: list[Path] = []
    extra_column = (tmp_path / "extra-column.sqlite3").resolve()
    _install_canon_schema(extra_column)
    conn = sqlite3.connect(extra_column)
    conn.execute("ALTER TABLE canon_apply_token ADD COLUMN raw_bearer_shadow TEXT")
    conn.commit()
    conn.close()
    drift_paths.append(extra_column)

    rogue_trigger = (tmp_path / "rogue-trigger.sqlite3").resolve()
    _install_canon_schema(rogue_trigger)
    conn = sqlite3.connect(rogue_trigger)
    conn.execute(
        """
        CREATE TRIGGER rogue_canon_journal_insert
        AFTER INSERT ON canon_apply_journal
        BEGIN
            UPDATE canon_apply_token SET state = state;
        END
        """
    )
    conn.commit()
    conn.close()
    drift_paths.append(rogue_trigger)

    source_trigger = (tmp_path / "source-trigger.sqlite3").resolve()
    _install_canon_schema(source_trigger)
    conn = sqlite3.connect(source_trigger)
    conn.execute("CREATE TABLE attacker_source(value TEXT)")
    conn.execute(
        """
        CREATE TRIGGER source_to_canon
        AFTER INSERT ON attacker_source
        BEGIN
            DELETE FROM canon_apply_journal WHERE apply_id = NEW.value;
        END
        """
    )
    conn.commit()
    conn.close()
    drift_paths.append(source_trigger)

    wrong_index = (tmp_path / "wrong-index.sqlite3").resolve()
    _install_canon_schema(wrong_index)
    conn = sqlite3.connect(wrong_index)
    conn.execute("DROP INDEX uq_canon_apply_active_target")
    conn.execute(
        "CREATE INDEX uq_canon_apply_active_target ON canon_apply_journal(target_doc_id)"
    )
    conn.commit()
    conn.close()
    drift_paths.append(wrong_index)

    no_command_unique = (tmp_path / "no-command-unique.sqlite3").resolve()
    _install_canon_schema(
        no_command_unique,
        schema_sql=canonical_sql.replace(
            "command_hash TEXT NOT NULL UNIQUE CHECK(length(command_hash) = 64)",
            "command_hash TEXT NOT NULL CHECK(length(command_hash) = 64)",
        ),
    )
    drift_paths.append(no_command_unique)

    for path in drift_paths:
        with pytest.raises(RuntimeError, match="CANON_APPLY_SCHEMA_NOT_READY"):
            CanonApprovalTokenIssuer(
                path,
                authority_key_id="key",
                private_key=key,
                reviewer_id="owner",
            )

    temp_trigger = (tmp_path / "temp-trigger.sqlite3").resolve()
    _install_canon_schema(temp_trigger)
    conn = sqlite3.connect(temp_trigger)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("CREATE TEMP TABLE temp_attacker(value TEXT)")
        conn.execute(
            """
            CREATE TEMP TRIGGER temp_to_canon
            AFTER INSERT ON temp_attacker
            BEGIN
                DELETE FROM canon_apply_journal WHERE apply_id = NEW.value;
            END
            """
        )
        with pytest.raises(RuntimeError, match="unexpected_schema_object"):
            assert_canon_apply_schema_ready(conn)
    finally:
        conn.close()

    wrong_record = (tmp_path / "wrong-record.sqlite3").resolve()
    _install_canon_schema(wrong_record, recorded_checksum="0" * 64)
    with pytest.raises(RuntimeError, match="central_migration_mismatch"):
        CanonApprovalTokenIssuer(
            wrong_record,
            authority_key_id="key",
            private_key=key,
            reviewer_id="owner",
        )


def test_t_nle_03_schema_readiness_performs_zero_target_writes(tmp_path: Path) -> None:
    db_path = (tmp_path / "readonly-schema.sqlite3").resolve()
    _install_canon_schema(db_path)
    denied_actions = {
        value
        for name in (
            "SQLITE_CREATE_INDEX",
            "SQLITE_CREATE_TABLE",
            "SQLITE_CREATE_TEMP_INDEX",
            "SQLITE_CREATE_TEMP_TABLE",
            "SQLITE_CREATE_TEMP_TRIGGER",
            "SQLITE_CREATE_TEMP_VIEW",
            "SQLITE_CREATE_TRIGGER",
            "SQLITE_CREATE_VIEW",
            "SQLITE_DELETE",
            "SQLITE_DROP_INDEX",
            "SQLITE_DROP_TABLE",
            "SQLITE_DROP_TEMP_INDEX",
            "SQLITE_DROP_TEMP_TABLE",
            "SQLITE_DROP_TEMP_TRIGGER",
            "SQLITE_DROP_TEMP_VIEW",
            "SQLITE_DROP_TRIGGER",
            "SQLITE_DROP_VIEW",
            "SQLITE_INSERT",
            "SQLITE_UPDATE",
            "SQLITE_ATTACH",
            "SQLITE_DETACH",
            "SQLITE_ALTER_TABLE",
            "SQLITE_REINDEX",
            "SQLITE_ANALYZE",
            "SQLITE_CREATE_VTABLE",
            "SQLITE_DROP_VTABLE",
        )
        if (value := getattr(sqlite3, name, None)) is not None
    }

    def deny_writes(action, _arg1, _arg2, _database, _trigger):
        return sqlite3.SQLITE_DENY if action in denied_actions else sqlite3.SQLITE_OK

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        before = (
            conn.total_changes,
            conn.execute("PRAGMA schema_version").fetchone()[0],
            conn.execute("PRAGMA page_count").fetchone()[0],
            conn.execute("PRAGMA freelist_count").fetchone()[0],
        )
        conn.set_authorizer(deny_writes)
        assert_canon_apply_schema_ready(conn)
        after = (
            conn.total_changes,
            conn.execute("PRAGMA schema_version").fetchone()[0],
            conn.execute("PRAGMA page_count").fetchone()[0],
            conn.execute("PRAGMA freelist_count").fetchone()[0],
        )
        assert after == before
    finally:
        conn.close()

    empty = sqlite3.connect((tmp_path / "readonly-empty.sqlite3").resolve())
    try:
        empty.execute("PRAGMA foreign_keys = ON")
        before = (empty.total_changes, empty.execute("PRAGMA schema_version").fetchone()[0])
        empty.set_authorizer(deny_writes)
        with pytest.raises(RuntimeError, match="central_migration_missing"):
            assert_canon_apply_schema_ready(empty)
        assert (empty.total_changes, empty.execute("PRAGMA schema_version").fetchone()[0]) == before
    finally:
        empty.close()


def test_t_nle_03_schema_asset_checksum_is_frozen(tmp_path: Path) -> None:
    assert CanonApprovalTokenIssuer.migration_asset_checksum() == CANON_APPLY_MIGRATION_SHA256
    truncated = tmp_path / "0053_canon_apply.sql"
    truncated.write_text(
        "-- canon_apply_token canon_apply_journal\n",
        encoding="utf-8",
    )
    conn = sqlite3.connect((tmp_path / "asset-check.sqlite3").resolve())
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(RuntimeError, match="asset_checksum"):
            assert_canon_apply_schema_ready(conn, migration_path=truncated)
    finally:
        conn.close()


def test_t_nle_03_migration_number_and_route_payload_boundary() -> None:
    root = Path(__file__).resolve().parents[2]
    assert (root / "host_brain/audit/migrations/0053_canon_apply.sql").is_file()
    assert not (root / "host_brain/audit/migrations/0052_canon_apply.sql").exists()
    from host_brain.app.canon_routes import CanonProposalApproveRequest

    raw_bearer = "canon::raw-bearer-must-never-render"
    request = CanonProposalApproveRequest(
        apply_id="route",
        subject_version=1,
        subject_hash="a" * 64,
        target_doc_id="doc",
        base_version=0,
        approval_token_ref=raw_bearer,
        actor_id="owner",
        session_id="session",
        trace_id="trace",
        evidence_refs=["audit://route/review"],
    )
    assert raw_bearer not in repr(request)
    assert raw_bearer not in str(request.model_dump())

    with pytest.raises(Exception) as validation_error:
        CanonProposalApproveRequest(
            apply_id="route",
            subject_version=1,
            subject_hash="a" * 64,
            target_doc_id="doc",
            base_version=0,
            approval_token_ref=raw_bearer,
            actor_id="owner",
            session_id="session",
            trace_id="trace",
            evidence_refs=["audit://route/review"],
            proposed_title="replacement forbidden",
            proposed_body="replacement forbidden",
        )
    assert raw_bearer not in str(validation_error.value)
