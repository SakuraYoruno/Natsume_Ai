from __future__ import annotations

import base64
from pathlib import Path
import sqlite3

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

import host_brain.contracts.task_runtime_feature_canary as canonical_canary
import host_brain.persistence.task_runtime_feature_canary_repository as canary_repository
import host_brain.task_runtime.feature_canary as legacy_canary
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.reconciliation_proof_verifier import (
    Ed25519ReconciliationProofVerifier,
)
from host_brain.persistence.task_runtime_feature_canary_repository import (
    SQLiteTaskRuntimeFeatureCanaryReceiptStore,
)
from host_brain.task_runtime.feature_canary import (
    TASK_RUNTIME_FEATURE_CANARY_EXPIRED,
    TASK_RUNTIME_FEATURE_CANARY_MISMATCH,
    TASK_RUNTIME_FEATURE_CANARY_UNATTESTED,
    TaskRuntimeFeatureCanaryError,
    TaskRuntimeFeatureCanaryReceipt,
    bounded_fanout_configuration_digest,
    require_bounded_fanout_canary,
    task_runtime_feature_canary_signing_message,
)


NOW_MS = 1_900_000_000_000
BUILD_SHA = "a" * 40
MIGRATION_VERSION = 120
KEY_ID = "task-runtime-canary-v1"


def test_feature_canary_contract_has_one_authority_neutral_type_identity() -> None:
    """Persistence and the legacy runtime path must share one contract object."""

    assert (
        legacy_canary.TaskRuntimeFeatureCanaryReceipt
        is canonical_canary.TaskRuntimeFeatureCanaryReceipt
    )
    assert (
        legacy_canary.TaskRuntimeFeatureCanaryError
        is canonical_canary.TaskRuntimeFeatureCanaryError
    )
    assert (
        canary_repository.TaskRuntimeFeatureCanaryReceipt
        is canonical_canary.TaskRuntimeFeatureCanaryReceipt
    )
    repository_source = (
        Path(canary_repository.__file__).read_text(encoding="utf-8-sig")
    )
    assert "host_brain.task_runtime.feature_canary" not in repository_source
    assert "host_brain.contracts.task_runtime_feature_canary" in repository_source


def _store(tmp_path: Path) -> SQLiteTaskRuntimeFeatureCanaryReceiptStore:
    db_path = tmp_path / "canonical.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="test-feature-canary",
        ts_ms=NOW_MS,
    )
    assert result.success is True
    return SQLiteTaskRuntimeFeatureCanaryReceiptStore(db_path)


def _verifier(
    private_key: Ed25519PrivateKey,
) -> Ed25519ReconciliationProofVerifier:
    public = private_key.public_key().public_bytes(
        encoding=Encoding.Raw,
        format=PublicFormat.Raw,
    )
    return Ed25519ReconciliationProofVerifier({KEY_ID: public})


def _receipt(
    private_key: Ed25519PrivateKey,
    *,
    configuration_digest: str | None = None,
    issued_at_ms: int = NOW_MS - 1,
    expires_at_ms: int = NOW_MS + 60_000,
    signature: str | None = None,
) -> TaskRuntimeFeatureCanaryReceipt:
    configuration = configuration_digest or bounded_fanout_configuration_digest(
        atomic_graph_success_enabled=True,
        fanout_event_buffer_size=32,
    )
    evidence_digest = "sha256:" + "e" * 64
    receipt_ref, message = task_runtime_feature_canary_signing_message(
        feature_key="bounded_fanout",
        build_commit_sha=BUILD_SHA,
        runtime_migration_version=MIGRATION_VERSION,
        configuration_digest=configuration,
        evidence_digest=evidence_digest,
        attestation_key_id=KEY_ID,
        issued_at_ms=issued_at_ms,
        expires_at_ms=expires_at_ms,
    )
    encoded_signature = signature or (
        "ed25519:"
        + base64.urlsafe_b64encode(private_key.sign(message)).decode("ascii")
    )
    return TaskRuntimeFeatureCanaryReceipt(
        receipt_ref=receipt_ref,
        feature_key="bounded_fanout",
        build_commit_sha=BUILD_SHA,
        runtime_migration_version=MIGRATION_VERSION,
        configuration_digest=configuration,
        evidence_digest=evidence_digest,
        attestation_key_id=KEY_ID,
        attestation_signature=encoded_signature,
        issued_at_ms=issued_at_ms,
        expires_at_ms=expires_at_ms,
    )


def test_signed_canary_reopens_after_store_restart_and_enables_exact_runtime(
    tmp_path: Path,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    verifier = _verifier(private_key)
    store = _store(tmp_path)
    receipt = _receipt(private_key)

    appended, replayed = store.append_attested(
        receipt,
        signature_verifier=verifier,
    )
    replay, replayed_again = store.append_attested(
        receipt,
        signature_verifier=verifier,
    )

    assert appended == receipt
    assert replayed is False
    assert replay == receipt
    assert replayed_again is True

    reopened = SQLiteTaskRuntimeFeatureCanaryReceiptStore(
        tmp_path / "canonical.sqlite3"
    )
    assert (
        require_bounded_fanout_canary(
            requested=True,
            receipt_ref=receipt.receipt_ref,
            build_commit_sha=BUILD_SHA,
            runtime_migration_version=MIGRATION_VERSION,
            atomic_graph_success_enabled=True,
            fanout_event_buffer_size=32,
            receipt_store=reopened,
            signature_verifier=verifier,
            now_ms=NOW_MS,
        )
        is True
    )
    with sqlite3.connect(tmp_path / "canonical.sqlite3") as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE task_runtime_feature_canary_receipt "
                "SET evidence_digest=? WHERE receipt_ref=?",
                ("sha256:" + "f" * 64, receipt.receipt_ref),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "DELETE FROM task_runtime_feature_canary_receipt "
                "WHERE receipt_ref=?",
                (receipt.receipt_ref,),
            )


def test_canary_rejects_configuration_or_build_drift(tmp_path: Path) -> None:
    private_key = Ed25519PrivateKey.generate()
    verifier = _verifier(private_key)
    store = _store(tmp_path)
    receipt = _receipt(private_key)
    store.append_attested(receipt, signature_verifier=verifier)

    with pytest.raises(
        TaskRuntimeFeatureCanaryError,
        match=TASK_RUNTIME_FEATURE_CANARY_MISMATCH,
    ):
        require_bounded_fanout_canary(
            requested=True,
            receipt_ref=receipt.receipt_ref,
            build_commit_sha=BUILD_SHA,
            runtime_migration_version=MIGRATION_VERSION,
            atomic_graph_success_enabled=True,
            fanout_event_buffer_size=33,
            receipt_store=store,
            signature_verifier=verifier,
            now_ms=NOW_MS,
        )


def test_canary_rejects_expired_receipt(tmp_path: Path) -> None:
    private_key = Ed25519PrivateKey.generate()
    verifier = _verifier(private_key)
    store = _store(tmp_path)
    receipt = _receipt(
        private_key,
        issued_at_ms=NOW_MS - 100,
        expires_at_ms=NOW_MS - 1,
    )
    store.append_attested(receipt, signature_verifier=verifier)

    with pytest.raises(
        TaskRuntimeFeatureCanaryError,
        match=TASK_RUNTIME_FEATURE_CANARY_EXPIRED,
    ):
        require_bounded_fanout_canary(
            requested=True,
            receipt_ref=receipt.receipt_ref,
            build_commit_sha=BUILD_SHA,
            runtime_migration_version=MIGRATION_VERSION,
            atomic_graph_success_enabled=True,
            fanout_event_buffer_size=32,
            receipt_store=store,
            signature_verifier=verifier,
            now_ms=NOW_MS,
        )


def test_canary_rejects_an_untrusted_signature_before_persistence(tmp_path: Path) -> None:
    private_key = Ed25519PrivateKey.generate()
    verifier = _verifier(private_key)
    store = _store(tmp_path)
    receipt = _receipt(
        private_key,
        signature="ed25519:" + base64.urlsafe_b64encode(b"x" * 64).decode("ascii"),
    )

    with pytest.raises(
        TaskRuntimeFeatureCanaryError,
        match=TASK_RUNTIME_FEATURE_CANARY_UNATTESTED,
    ):
        store.append_attested(receipt, signature_verifier=verifier)
    assert store.read(receipt.receipt_ref) is None
