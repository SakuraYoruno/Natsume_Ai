from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "runtime" / "test_voice_dual_chain_full_nodes_refactored.py"


def _load_probe_module():
    spec = importlib.util.spec_from_file_location("voice_dual_chain_probe_test", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _healthy_edge_health_body() -> dict[str, Any]:
    return {
        "event": {
            "components": {
                "tts": {"status": "UP"},
                "vts": {
                    "status": "UP",
                    "health": {"state": "CONNECTED"},
                },
            }
        }
    }


def _healthy_gateway_completion_payload() -> dict[str, Any]:
    return {
        "decision": "APPROVE",
        "route_type": "chat",
        "reply_form": "text",
        "voice_sent": True,
        "gateway_voice_success": True,
        "gateway_voice_status": "ACCEPTED",
        "gateway_voice_reason_code": "",
        "gateway_voice_vts_started": True,
        "gateway_voice_vts_expression_success": True,
        "gateway_voice_event": {
            "event_type": "TTS_PLAYBACK_FINISHED",
            "start_event": {"event_type": "TTS_PLAYBACK_STARTED"},
            "vts_expression_result": {"success": True},
            "vts_talk_result": {"success": True},
            "vts_mouth_sync": {"started": True, "state": "finished"},
        },
    }


def _healthy_audit_db_result() -> dict[str, Any]:
    completion_row = {
        "event_type": "INPUT_COMPLETED",
        "status": "OK",
        "reason_code": None,
        "stage": "input_dispatch.completed",
        "detail": "dispatch completed",
        "payload": _healthy_gateway_completion_payload(),
        "ts_ms": 1776435000002,
    }
    return {
        "available": True,
        "request_event_count": 3,
        "request_event_latest": [completion_row],
        "input_completed": completion_row,
        "tts_event_count": 2,
        "tts_event_latest": [
            {
                "event_type": "TTS_PLAYBACK_FINISHED",
                "status": "ACCEPTED",
                "reason_code": None,
                "payload": {"event_type": "TTS_PLAYBACK_FINISHED"},
                "ts_ms": 1776435000001,
            },
            {
                "event_type": "TTS_PLAYBACK_STARTED",
                "status": "ACCEPTED",
                "reason_code": None,
                "payload": {"event_type": "TTS_PLAYBACK_STARTED"},
                "ts_ms": 1776435000000,
            },
        ],
        "avatar_action_count": 1,
        "avatar_action_latest": [
            {
                "trigger": "HOST",
                "emotion_tag": "HAPPY",
                "expression_id": "exp_happy_01",
                "status": "APPLIED",
                "reason_code": None,
                "stage": "S_AVATAR_EXPRESSION_MAP",
                "detail": "expression resolved via fixed mapping",
                "ts_ms": 1776434994932,
            }
        ],
    }


def _run_main_with_fakes(
    *,
    monkeypatch,
    tmp_path: Path,
    gateway_input_body: dict[str, Any] | None = None,
    audit_db_result: dict[str, Any] | None = None,
    extra_args: list[str] | None = None,
) -> tuple[int, dict[str, Any]]:
    module = _load_probe_module()

    def _fake_http_json(method: str, url: str, **_kwargs) -> dict[str, Any]:
        if url.endswith("/v1/edge/health"):
            return {"ok": True, "status_code": 200, "body": _healthy_edge_health_body()}
        if url.endswith("/health"):
            return {"ok": True, "status_code": 200, "body": {"status": "UP"}}
        if url.endswith("/ready"):
            return {"ok": True, "status_code": 200, "body": {"ready": True}}
        if url.endswith("/v1/host/input"):
            return {
                "ok": True,
                "status_code": 200,
                "body": gateway_input_body
                or {
                    "success": True,
                    "status": "ACCEPTED",
                    "request_id": "gateway-req-001",
                },
            }
        raise AssertionError(f"Unexpected URL: {method} {url}")

    monkeypatch.setattr(module, "_load_env_file", lambda _path: {})
    monkeypatch.setattr(module, "_http_json", _fake_http_json)
    monkeypatch.setattr(
        module,
        "_await_gateway_runtime_evidence",
        lambda **_kwargs: audit_db_result or _healthy_audit_db_result(),
    )

    argv = [
        "voice_probe",
        "--host-url",
        "http://host.test",
        "--edge-url",
        "http://edge.test",
        "--report-dir",
        str(tmp_path),
        "--voice-evidence-wait-s",
        "0",
    ]
    if extra_args:
        argv.extend(extra_args)
    monkeypatch.setattr(sys, "argv", argv)

    exit_code = module.main()
    report_path = next(tmp_path.glob("voice_dual_chain_full_nodes_*.json"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    return exit_code, report


def test_runtime_probe_main_passes_when_gateway_voice_chain_is_healthy(monkeypatch, tmp_path: Path) -> None:
    exit_code, report = _run_main_with_fakes(monkeypatch=monkeypatch, tmp_path=tmp_path)

    assert exit_code == 0
    assert report["overall"]["passed"] is True
    assert report["overall"]["failed_nodes"] == []
    matrix = {row["node"]: row for row in report["matrix"]}
    assert matrix["gateway.input_accepted"]["passed"] is True
    assert matrix["gateway.dispatch_completed"]["passed"] is True
    assert matrix["tts.rpc"]["passed"] is True
    assert matrix["vts.rpc"]["passed"] is True


def test_runtime_probe_main_fails_when_tts_playback_evidence_is_missing(monkeypatch, tmp_path: Path) -> None:
    bad_audit = _healthy_audit_db_result()
    bad_audit["tts_event_latest"] = [
        {
            "event_type": "TTS_QUEUED",
            "status": "ACCEPTED",
            "reason_code": None,
            "payload": {"event_type": "TTS_QUEUED"},
            "ts_ms": 1776435000000,
        }
    ]
    bad_audit["tts_event_count"] = 1

    exit_code, report = _run_main_with_fakes(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        audit_db_result=bad_audit,
    )

    assert exit_code == 2
    assert report["overall"]["passed"] is False
    assert "tts.rpc" in report["overall"]["failed_nodes"]


def test_runtime_probe_main_allows_degraded_tts_when_flag_is_enabled(monkeypatch, tmp_path: Path) -> None:
    degraded_audit = _healthy_audit_db_result()
    degraded_audit["tts_event_latest"] = [
        {
            "event_type": "TTS_PLAYBACK_STARTED",
            "status": "DEGRADED",
            "reason_code": None,
            "payload": {"event_type": "TTS_PLAYBACK_STARTED"},
            "ts_ms": 1776435000000,
        }
    ]
    degraded_audit["tts_event_count"] = 1

    exit_code, report = _run_main_with_fakes(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        audit_db_result=degraded_audit,
        extra_args=["--allow-degraded-tts"],
    )

    assert exit_code == 0
    assert report["overall"]["passed"] is True
    matrix = {row["node"]: row for row in report["matrix"]}
    assert matrix["tts.rpc"]["passed"] is True


def test_runtime_probe_main_falls_back_to_input_completed_payload_when_logs_are_sparse(
    monkeypatch,
    tmp_path: Path,
) -> None:
    sparse_audit = _healthy_audit_db_result()
    sparse_audit["tts_event_latest"] = []
    sparse_audit["tts_event_count"] = 0
    sparse_audit["avatar_action_latest"] = []
    sparse_audit["avatar_action_count"] = 0

    exit_code, report = _run_main_with_fakes(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        audit_db_result=sparse_audit,
    )

    assert exit_code == 0
    assert report["overall"]["passed"] is True
    assert report["steps"]["runtime_voice_sources"] == {"tts": "input_completed", "vts": "input_completed"}
    matrix = {row["node"]: row for row in report["matrix"]}
    assert matrix["tts.rpc"]["passed"] is True
    assert matrix["vts.rpc"]["passed"] is True


def test_build_tts_summary_prefers_finished_event_when_present() -> None:
    module = _load_probe_module()
    summary = module._build_tts_summary_from_event_rows(
        [
            {
                "event_type": "TTS_PLAYBACK_STARTED",
                "status": "ACCEPTED",
                "reason_code": None,
                "payload": {"event_type": "TTS_PLAYBACK_STARTED"},
            },
            {
                "event_type": "TTS_PLAYBACK_FINISHED",
                "status": "ACCEPTED",
                "reason_code": None,
                "payload": {"event_type": "TTS_PLAYBACK_FINISHED"},
            },
        ]
    )

    assert summary["event"]["event_type"] == "TTS_PLAYBACK_FINISHED"
    assert summary["success"] is True


def test_await_gateway_runtime_evidence_waits_for_playback_after_queue(monkeypatch) -> None:
    module = _load_probe_module()

    queued_audit = _healthy_audit_db_result()
    queued_audit["tts_event_latest"] = [
        {
            "event_type": "TTS_QUEUED",
            "status": "ACCEPTED",
            "reason_code": None,
            "payload": {"event_type": "TTS_QUEUED", "queue_depth": 2},
            "ts_ms": 1776435000000,
        }
    ]
    queued_audit["tts_event_count"] = 1

    started_audit = _healthy_audit_db_result()
    calls = {"count": 0}

    def _fake_query_gateway_audit_db(**_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return queued_audit
        return started_audit

    tick = {"value": 0.0}

    def _fake_time() -> float:
        tick["value"] += 0.1
        return tick["value"]

    monkeypatch.setattr(module, "_query_gateway_audit_db", _fake_query_gateway_audit_db)
    monkeypatch.setattr(module.time, "time", _fake_time)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    result = module._await_gateway_runtime_evidence(
        db_path=Path("unused.sqlite3"),
        request_id="gateway-req-001",
        wait_seconds=2.0,
        allow_degraded_tts=False,
        allow_degraded_vts=False,
        require_mouth_sync=True,
        poll_interval_s=0.0,
    )

    assert calls["count"] >= 2
    assert result["tts_event_latest"][0]["event_type"] == "TTS_PLAYBACK_FINISHED"
