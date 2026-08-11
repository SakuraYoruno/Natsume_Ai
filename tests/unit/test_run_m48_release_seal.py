from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from host_brain.release.m48_seal import M48_SEAL_RESULT_SCHEMA_VERSION
from tools import run_m48_release_seal as runner


def _result_payload(status: str) -> dict[str, Any]:
    return {
        "schema_version": M48_SEAL_RESULT_SCHEMA_VERSION,
        "release_id": "release://m48/test",
        "milestone": "M48-A",
        "status": status,
        "master_approval_required": True,
        "issues": [],
        "checked_artifacts": ["evidence"],
        "verified_evidence_ids": ["stable_suite"],
        "verified_rollback_ids": ["runtime_mode"],
    }


class _StubResult:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload)


def test_ready_result_is_written_atomically_and_deterministically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    output = root / "reports" / "seal-result.json"
    monkeypatch.setattr(
        runner,
        "evaluate_m48_release_seal",
        lambda **_kwargs: _StubResult(_result_payload("READY_FOR_MASTER_SEAL")),
    )
    replacements: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def _recording_replace(source: str | Path, target: str | Path) -> None:
        replacements.append((Path(source), Path(target)))
        real_replace(source, target)

    monkeypatch.setattr(runner.os, "replace", _recording_replace)

    first_exit, first_payload = runner.run_release_seal(
        root=root,
        manifest="manifest.json",
        output="reports/seal-result.json",
        pretty=True,
    )
    first_bytes = output.read_bytes()
    second_exit, second_payload = runner.run_release_seal(
        root=root,
        manifest="manifest.json",
        output="reports/seal-result.json",
        pretty=True,
    )

    assert first_exit == second_exit == runner.EXIT_READY
    assert first_payload == second_payload
    assert output.read_bytes() == first_bytes
    assert output.read_text(encoding="utf-8").endswith("\n")
    assert [target for _, target in replacements] == [output, output]
    assert all(source.parent == output.parent and source != output for source, _ in replacements)
    assert list(output.parent.glob(f".{output.name}.*.tmp")) == []


@pytest.mark.parametrize("status", ["FAILED", "BLOCKED"])
def test_non_ready_result_returns_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    monkeypatch.setattr(
        runner,
        "evaluate_m48_release_seal",
        lambda **_kwargs: _StubResult(_result_payload(status)),
    )

    exit_code, payload = runner.run_release_seal(
        root=tmp_path,
        manifest="manifest.json",
        output="seal-result.json",
    )

    assert exit_code == runner.EXIT_NOT_READY
    assert payload["status"] == status
    assert json.loads((tmp_path / "seal-result.json").read_text(encoding="utf-8")) == payload


def test_missing_and_invalid_manifest_write_fail_closed_json(tmp_path: Path) -> None:
    missing_exit, missing_payload = runner.run_release_seal(
        root=tmp_path,
        manifest="missing.json",
        output="missing-result.json",
    )
    invalid_manifest = tmp_path / "invalid.json"
    invalid_manifest.write_text("{not-json", encoding="utf-8")
    invalid_exit, invalid_payload = runner.run_release_seal(
        root=tmp_path,
        manifest="invalid.json",
        output="invalid-result.json",
    )

    assert missing_exit == invalid_exit == runner.EXIT_NOT_READY
    assert missing_payload["status"] == invalid_payload["status"] == "BLOCKED"
    assert missing_payload["master_approval_required"] is True
    assert invalid_payload["master_approval_required"] is True
    assert "SEALED" not in (tmp_path / "missing-result.json").read_text(encoding="utf-8")
    assert "SEALED" not in (tmp_path / "invalid-result.json").read_text(encoding="utf-8")


@pytest.mark.parametrize("mode", ["exception", "final_status"])
def test_exception_or_final_status_attempt_is_downgraded_to_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    if mode == "exception":
        def _evaluate(**_kwargs: Any) -> _StubResult:
            raise RuntimeError("SEALED must not leak from an exception")
    else:
        def _evaluate(**_kwargs: Any) -> _StubResult:
            return _StubResult(_result_payload("SEALED"))

    monkeypatch.setattr(runner, "evaluate_m48_release_seal", _evaluate)

    exit_code, payload = runner.run_release_seal(
        root=tmp_path,
        manifest="manifest.json",
        output="seal-result.json",
    )
    rendered = (tmp_path / "seal-result.json").read_text(encoding="utf-8")

    assert exit_code == runner.EXIT_NOT_READY
    assert payload["status"] == "BLOCKED"
    assert payload["issues"][0]["reason_code"] == runner.RUNNER_EXCEPTION_REASON
    assert "SEALED" not in rendered
