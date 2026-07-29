from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "runtime" / "provision_workspace_secret_ref.py"


def _invoke(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_workspace_secret_provision_is_idempotent_and_never_emits_value(
    tmp_path: Path,
) -> None:
    store = tmp_path / "secret_store.runtime.local.json"
    secret_ref = "sec://workspace/dl07-acceptance"

    created = _invoke("--secret-store", str(store), "--secret-ref", secret_ref)
    assert created.returncode == 0, created.stderr
    created_payload = json.loads(created.stdout)
    assert created_payload["status"] == "CREATED"
    assert created_payload["secret_ref"] == secret_ref
    assert created_payload["secret_digest"].startswith("sha256:")

    stored_payload = json.loads(store.read_text(encoding="utf-8"))
    stored_value = stored_payload[secret_ref]
    assert len(stored_value.encode("utf-8")) >= 32
    assert stored_value not in created.stdout

    reopened = _invoke("--secret-store", str(store), "--secret-ref", secret_ref)
    assert reopened.returncode == 0, reopened.stderr
    reopened_payload = json.loads(reopened.stdout)
    assert reopened_payload["status"] == "EXISTS"
    assert reopened_payload["secret_digest"] == created_payload["secret_digest"]
    assert stored_value not in reopened.stdout


def test_workspace_secret_provision_rejects_non_workspace_reference(tmp_path: Path) -> None:
    completed = _invoke(
        "--secret-store",
        str(tmp_path / "secret_store.runtime.local.json"),
        "--secret-ref",
        "sec://edge/not-a-workspace-secret",
    )

    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    assert payload == {
        "status": "INVALID",
        "reason": "secret_ref must use sec://workspace/<name>",
    }
