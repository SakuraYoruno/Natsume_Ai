from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

import host_brain.app.tool_runtime_composition as tool_composition_module
from host_brain.app.tool_runtime_composition import (
    GovernedHostToolRuntimeComposition,
    GovernedHostToolRuntimeDependencies,
)
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.reconciliation_proof_verifier import (
    Ed25519ReconciliationProofVerifier,
)
from host_brain.persistence.task_runtime_feature_canary_repository import (
    SQLiteTaskRuntimeFeatureCanaryReceiptStore,
)
from host_brain.task_runtime.feature_canary import (
    TaskRuntimeFeatureCanaryReceipt,
    bounded_fanout_configuration_digest,
    task_runtime_feature_canary_signing_message,
)


BUILD_SHA = "a" * 40
NOW_MS = 1_900_000_000_000
KEY_ID = "task-runtime-canary-v1"


def _signed_store(
    tmp_path: Path,
) -> tuple[
    SQLiteTaskRuntimeFeatureCanaryReceiptStore,
    Ed25519ReconciliationProofVerifier,
    TaskRuntimeFeatureCanaryReceipt,
]:
    db_path = tmp_path / "canonical.sqlite3"
    migration = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="test-tool-runtime-feature-canary",
        ts_ms=NOW_MS,
    )
    assert migration.success is True
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=Encoding.Raw,
        format=PublicFormat.Raw,
    )
    verifier = Ed25519ReconciliationProofVerifier({KEY_ID: public_key})
    configuration_digest = bounded_fanout_configuration_digest(
        atomic_graph_success_enabled=True,
        fanout_event_buffer_size=32,
    )
    receipt_ref, message = task_runtime_feature_canary_signing_message(
        feature_key="bounded_fanout",
        build_commit_sha=BUILD_SHA,
        runtime_migration_version=120,
        configuration_digest=configuration_digest,
        evidence_digest="sha256:" + "e" * 64,
        attestation_key_id=KEY_ID,
        issued_at_ms=0,
        expires_at_ms=NOW_MS + 60_000,
    )
    receipt = TaskRuntimeFeatureCanaryReceipt(
        receipt_ref=receipt_ref,
        feature_key="bounded_fanout",
        build_commit_sha=BUILD_SHA,
        runtime_migration_version=120,
        configuration_digest=configuration_digest,
        evidence_digest="sha256:" + "e" * 64,
        attestation_key_id=KEY_ID,
        attestation_signature=(
            "ed25519:"
            + base64.urlsafe_b64encode(private_key.sign(message)).decode("ascii")
        ),
        issued_at_ms=0,
        expires_at_ms=NOW_MS + 60_000,
    )
    store = SQLiteTaskRuntimeFeatureCanaryReceiptStore(db_path)
    store.append_attested(receipt, signature_verifier=verifier)
    return store, verifier, receipt


def test_host_tool_composition_passes_only_a_verified_fanout_canary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store, verifier, receipt = _signed_store(tmp_path)
    captured: dict[str, object] = {}
    runtime = object()
    tool_composition = GovernedHostToolRuntimeComposition(
        GovernedHostToolRuntimeDependencies(
            skill_registry=lambda: object(),
            token_service=lambda: object(),
            capability_broker=lambda: object(),
            resource_manager=lambda **_kwargs: None,
            ledger=lambda: object(),
            tool_catalog=lambda: object(),
            intent_engine=lambda: object(),
            model_attempt_service=lambda: object(),
        )
    )
    tool_composition._boundary = SimpleNamespace(
        bind_execution_port=lambda **_kwargs: object(),
        broker=SimpleNamespace(
            catalog=object(),
            resolve_execution_binding=lambda _target: object(),
        )
    )
    monkeypatch.setattr(
        tool_composition_module,
        "build_governed_host_runtime",
        lambda **kwargs: captured.update(kwargs) or runtime,
    )
    monkeypatch.setattr(
        tool_composition_module,
        "GovernedToolHttpApplication",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        tool_composition_module,
        "SecureIngressHttpAuthorityAdapter",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        tool_composition_module,
        "DurableIntentAdmissionVerifier",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        tool_composition_module,
        "ApplicationModelAttemptServicePort",
        lambda value: value,
    )
    monkeypatch.setattr(tool_composition_module, "now_ms", lambda: NOW_MS)

    activated = tool_composition.activate(
        task_runtime=SimpleNamespace(
            migration_result=SimpleNamespace(summary={"latest_version": 120}),
            feature_canary_store=store,
            feature_canary_signature_verifier=verifier,
            review_store=object(),
        ),
        ledger=object(),
        command_ingress=SimpleNamespace(evaluator=object(), store=object()),
        owner_id="host-owner",
        runtime_settings=SimpleNamespace(
            task_runtime_bounded_fanout_enabled=True,
            task_runtime_canary_receipt=receipt.receipt_ref,
            task_runtime_build_commit_sha=BUILD_SHA,
            task_runtime_atomic_graph_success_enabled=True,
            task_runtime_fanout_event_buffer_size=32,
        ),
        runtime_context_application=SimpleNamespace(
            composition=SimpleNamespace(
                intent_authority_port=object(),
                intent_arbiter=object(),
                event_store=object(),
                guardian=object(),
            )
        ),
        cancellation_authority_verifier=object(),
        action_preview_service=object(),
        acceptance_effect_dispatcher=object(),
        episodic_store=None,
        semantic_recaller=None,
    )

    assert activated is runtime
    assert captured["bounded_fanout_enabled"] is True
    assert captured["fanout_event_buffer_size"] == 32
