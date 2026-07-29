"""Safety invariants for the real DL-07 Monitor browser receipt runner."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "runtime" / "run_dl07_monitor_ui_live.py"
SPEC = importlib.util.spec_from_file_location("dl07_monitor_ui_live_runner", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_operator_binding_contains_only_the_digest_not_the_ephemeral_session() -> None:
    binding, binding_digest = MODULE._operator_binding("ephemeral-session-value")
    payload = json.loads(binding)

    assert "ephemeral-session-value" not in binding
    assert payload[0]["token_sha256"] == binding_digest.removeprefix("sha256:")
    assert payload[0]["scopes"]


def test_launcher_parser_selects_the_final_top_level_json_object() -> None:
    parsed = MODULE._parse_last_json_object(
        "bounded warning\n"
        '{"status":"started","nested":{"status":"ignored"}}\n'
    )

    assert parsed == {"status": "started", "nested": {"status": "ignored"}}


def test_monitor_live_receipt_uses_only_digests_and_exact_running_identity() -> None:
    unsigned = MODULE._unsigned_receipt(
        commit_sha="a" * 40,
        run_id="dl07.monitor:20260729",
        host_url="http://127.0.0.1:18099",
        obs_url="http://127.0.0.1:18100",
        monitor_url="http://127.0.0.1:18199",
        host_summary={
            "host_pid": 101,
            "obs_pid": 102,
            "monitor_pid": 103,
            "canonical_db_path": "D:/isolated/audit.sqlite3",
            "runtime_root": "D:/isolated/host",
            "external_services_status": "ready",
        },
        binding_digest="sha256:" + "b" * 64,
        playwright_exit_code=0,
        timed_out=False,
        stdout_digest="sha256:" + "c" * 64,
        stderr_digest="sha256:" + "d" * 64,
        duration_ms=42,
        security_projection_digest="sha256:" + "e" * 64,
        status="PASS",
        stack_state="RUNNING_FOR_RECEIPT_SEAL",
        stop_status="NOT_REQUIRED",
    )

    assert unsigned["privacy"] == {
        "contains_user_text": False,
        "contains_model_output": False,
        "contains_raw_audio_or_image": False,
        "contains_secrets": False,
    }
    assert all("ephemeral" not in str(value) for value in unsigned.values())
    assert unsigned["operator_binding_digest"] == "sha256:" + "b" * 64


def test_security_projection_preparation_requires_real_host_hunt_then_healthy_obs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_request(**kwargs: object) -> dict[str, object]:
        calls.append(dict(kwargs))
        if len(calls) == 1:
            return {"status": "completed"}
        return {
            "schema_version": "Natsume.security_immune.runtime.v1",
            "asset_count": 1,
            "incident_count": 0,
            "projection": {
                "kind": "security_immune",
                "available": True,
                "degraded_state": "HEALTHY",
            },
        }

    monkeypatch.setattr(MODULE, "_bounded_json_request", fake_request)
    digest = MODULE._prepare_security_projection(
        host_url="http://127.0.0.1:18099",
        monitor_url="http://127.0.0.1:18199",
        session_secret="only-in-memory",
        timeout_seconds=10,
    )

    assert digest.startswith("sha256:")
    assert calls[0]["url"] == "http://127.0.0.1:18099/internal/security/hunt/run-once"
    assert calls[0]["method"] == "POST"
    assert calls[1]["url"] == "http://127.0.0.1:18199/obs/security/runtime"
    assert calls[1]["session_secret"] == "only-in-memory"


@pytest.mark.parametrize(
    "value",
    [
        "http://192.0.2.1:18199",
        "https://127.0.0.1:18199",
        "http://127.0.0.1:18199?credential=forbidden",
    ],
)
def test_monitor_runner_rejects_non_loopback_or_non_http_endpoints(value: str) -> None:
    with pytest.raises(ValueError):
        MODULE._loopback_url(value)
