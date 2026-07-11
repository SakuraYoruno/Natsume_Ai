from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from host_brain.release.m48_seal import (
    M48_ROLLBACK_MATRIX_SCHEMA_VERSION,
    M48_SEAL_ARTIFACT_MISSING,
    M48_SEAL_EVIDENCE_FAILED,
    M48_SEAL_EVIDENCE_SCHEMA_VERSION,
    M48_SEAL_EVIDENCE_SKIPPED,
    M48_SEAL_EVIDENCE_UNCLASSIFIED,
    M48_SEAL_HASH_MISMATCH,
    M48_SEAL_IDENTITY_PREVIOUS_REF_MISMATCH,
    M48_SEAL_MANIFEST_SCHEMA_VERSION,
    M48_SEAL_ROLLBACK_COMMAND_MISSING,
    M48SealManifest,
    M48SealStatus,
    evaluate_m48_release_seal,
)


def _write_json(path: Path, value: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _bundle(
    root: Path,
    *,
    evidence_status: str = "PASS",
    classification: str = "CONTRACT_TEST",
    rollback_status: str = "PASS",
    rollback_command: list[str] | None = None,
    rollback_previous_ref: str = "release://natsume/m47",
) -> dict[str, object]:
    evidence_path = root / "runtime/evidence/t-nle-24.json"
    rollback_path = root / "runtime/evidence/rollback.json"
    evidence_digest = _write_json(
        evidence_path,
        {
            "schema_version": M48_SEAL_EVIDENCE_SCHEMA_VERSION,
            "release_id": "release://natsume/m48-a",
            "records": [
                {
                    "evidence_id": "full_chain",
                    "classification": classification,
                    "status": evidence_status,
                    "command": ["python", "-m", "pytest", "tests/contract"],
                    "evidence_refs": ["report://m48/t-nle-24"],
                }
            ],
        },
    )
    rollback_digest = _write_json(
        rollback_path,
        {
            "schema_version": M48_ROLLBACK_MATRIX_SCHEMA_VERSION,
            "release_id": "release://natsume/m48-a",
            "previous_release_ref": rollback_previous_ref,
            "entries": [
                {
                    "rollback_id": "runtime_mode",
                    "scope": "RUNTIME_MODE",
                    "command": ["python", "tools/rollback.py"] if rollback_command is None else rollback_command,
                    "rehearsal_status": rollback_status,
                    "evidence_ref": "report://m48/rollback/runtime-mode",
                }
            ],
        },
    )
    return {
        "schema_version": M48_SEAL_MANIFEST_SCHEMA_VERSION,
        "release_id": "release://natsume/m48-a",
        "milestone": "M48-A",
        "git_commit_hash": "a" * 40,
        "identity_snapshot_ref": "release://natsume/m48-a/identity",
        "identity_previous_release_ref": "release://natsume/m47",
        "required_evidence_ids": ["full_chain"],
        "required_rollback_ids": ["runtime_mode"],
        "artifacts": [
            {
                "artifact_id": "t-nle-24",
                "kind": "EVIDENCE",
                "path": "runtime/evidence/t-nle-24.json",
                "sha256": evidence_digest,
            },
            {
                "artifact_id": "rollback",
                "kind": "ROLLBACK_MATRIX",
                "path": "runtime/evidence/rollback.json",
                "sha256": rollback_digest,
            },
        ],
    }


def _reason_codes(result: object) -> set[str]:
    return {item.reason_code for item in result.issues}  # type: ignore[attr-defined]


def test_t_nle_24_complete_bundle_is_only_ready_for_master_seal(tmp_path: Path) -> None:
    manifest = _bundle(tmp_path)

    result = evaluate_m48_release_seal(root=tmp_path, manifest=manifest)

    assert result.status is M48SealStatus.READY_FOR_MASTER_SEAL
    assert result.master_approval_required is True
    assert result.issues == ()
    assert result.verified_evidence_ids == ("full_chain",)
    assert result.verified_rollback_ids == ("runtime_mode",)
    payload = result.to_dict()
    assert payload["status"] == "READY_FOR_MASTER_SEAL"
    assert "SEALED" not in json.dumps(payload, sort_keys=True)


def test_t_nle_24_missing_artifact_blocks_seal(tmp_path: Path) -> None:
    manifest = _bundle(tmp_path)
    Path(tmp_path / "runtime/evidence/t-nle-24.json").unlink()

    result = evaluate_m48_release_seal(root=tmp_path, manifest=manifest)

    assert result.status is M48SealStatus.BLOCKED
    assert M48_SEAL_ARTIFACT_MISSING in _reason_codes(result)


def test_t_nle_24_hash_mismatch_blocks_before_evidence_is_trusted(tmp_path: Path) -> None:
    manifest = _bundle(tmp_path)
    Path(tmp_path / "runtime/evidence/t-nle-24.json").write_text("{}", encoding="utf-8")

    result = evaluate_m48_release_seal(root=tmp_path, manifest=manifest)

    assert result.status is M48SealStatus.BLOCKED
    assert M48_SEAL_HASH_MISMATCH in _reason_codes(result)
    assert result.verified_evidence_ids == ()


@pytest.mark.parametrize(
    ("evidence_status", "expected_status", "reason"),
    [
        ("FAILED", M48SealStatus.FAILED, M48_SEAL_EVIDENCE_FAILED),
        ("SKIPPED", M48SealStatus.BLOCKED, M48_SEAL_EVIDENCE_SKIPPED),
    ],
)
def test_t_nle_24_failed_or_skipped_evidence_fails_closed(
    tmp_path: Path,
    evidence_status: str,
    expected_status: M48SealStatus,
    reason: str,
) -> None:
    result = evaluate_m48_release_seal(
        root=tmp_path,
        manifest=_bundle(tmp_path, evidence_status=evidence_status),
    )

    assert result.status is expected_status
    assert reason in _reason_codes(result)


def test_t_nle_24_unclassified_evidence_is_never_counted(tmp_path: Path) -> None:
    result = evaluate_m48_release_seal(
        root=tmp_path,
        manifest=_bundle(tmp_path, classification="UNCLASSIFIED"),
    )

    assert result.status is M48SealStatus.BLOCKED
    assert M48_SEAL_EVIDENCE_UNCLASSIFIED in _reason_codes(result)
    assert result.verified_evidence_ids == ()


def test_t_nle_24_identity_previous_ref_is_mandatory(tmp_path: Path) -> None:
    manifest = _bundle(tmp_path)
    manifest["identity_previous_release_ref"] = ""

    result = evaluate_m48_release_seal(root=tmp_path, manifest=manifest)

    assert result.status is M48SealStatus.BLOCKED
    assert result.release_id is None


def test_t_nle_24_rollback_must_match_previous_identity_release(tmp_path: Path) -> None:
    result = evaluate_m48_release_seal(
        root=tmp_path,
        manifest=_bundle(tmp_path, rollback_previous_ref="release://natsume/other"),
    )

    assert result.status is M48SealStatus.BLOCKED
    assert M48_SEAL_IDENTITY_PREVIOUS_REF_MISMATCH in _reason_codes(result)


def test_t_nle_24_rollback_command_is_required(tmp_path: Path) -> None:
    result = evaluate_m48_release_seal(
        root=tmp_path,
        manifest=_bundle(tmp_path, rollback_command=[]),
    )

    assert result.status is M48SealStatus.BLOCKED
    assert M48_SEAL_ROLLBACK_COMMAND_MISSING in _reason_codes(result)


def test_t_nle_24_manifest_is_strict_and_cannot_request_sealed(tmp_path: Path) -> None:
    manifest = _bundle(tmp_path)
    manifest["status"] = "SEALED"

    result = evaluate_m48_release_seal(root=tmp_path, manifest=manifest)

    assert result.status is M48SealStatus.BLOCKED
    assert M48SealManifest.from_mapping(_bundle(tmp_path)).to_dict()["milestone"] == "M48-A"
