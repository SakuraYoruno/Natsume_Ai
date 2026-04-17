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


def _healthy_commit_event() -> dict[str, Any]:
    return {
        "phase": "COMMIT",
        "done": True,
        "decision": "APPROVE",
        "request_id": "chat-req-001",
        "audit_refs": {
            "tts": {
                "success": True,
                "status": "OK",
                "reason_code": "",
                "event": {
                    "event_type": "TTS_PLAYBACK_FINISHED",
                    "start_event": {"event_type": "TTS_PLAYBACK_STARTED"},
                },
            },
            "vts": {
                "success": True,
                "status": "APPLIED",
                "reason_code": "",
                "event": {
                    "vts_expression_result": {"success": True},
                    "vts_talk_result": {"success": True},
                    "vts_mouth_sync": {"started": True, "state": "finished"},
                },
            },
        },
    }


def _run_main_with_fakes(
    *,
    monkeypatch,
    tmp_path: Path,
    edge_events_body: dict[str, Any] | None = None,
    commit_event: dict[str, Any] | None = None,
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
        if url.endswith("/v1/edge/events"):
            return {
                "ok": True,
                "status_code": 200,
                "body": edge_events_body
                or {"success": True, "total": 2, "accepted": 2, "rejected": 0},
            }
        raise AssertionError(f"Unexpected URL: {method} {url}")

    def _fake_stream_chat_sse(**_kwargs) -> dict[str, Any]:
        event = commit_event or _healthy_commit_event()
        return {
            "ok": True,
            "status_code": 200,
            "latency_ms": 120,
            "events": [{"json": {"phase": "GRAPH_START"}}, {"json": event}],
            "event_count": 2,
            "line_count": 4,
            "commit_event": event,
        }

    monkeypatch.setattr(module, "_load_env_file", lambda _path: {})
    monkeypatch.setattr(module, "_http_json", _fake_http_json)
    monkeypatch.setattr(module, "_stream_chat_sse", _fake_stream_chat_sse)
    monkeypatch.setattr(
        module,
        "_query_audit_db",
        lambda **_kwargs: audit_db_result
        or {"available": True, "voice_event_count": 2, "avatar_action_count": 1},
    )

    argv = [
        "voice_probe",
        "--host-url",
        "http://host.test",
        "--edge-url",
        "http://edge.test",
        "--report-dir",
        str(tmp_path),
        "--allow-synthetic-asr",
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


def test_runtime_probe_main_passes_when_all_nodes_are_healthy(monkeypatch, tmp_path: Path) -> None:
    exit_code, report = _run_main_with_fakes(monkeypatch=monkeypatch, tmp_path=tmp_path)

    assert exit_code == 0
    assert report["overall"]["passed"] is True
    assert report["overall"]["failed_nodes"] == []
    matrix = {row["node"]: row for row in report["matrix"]}
    assert matrix["tts.rpc"]["passed"] is True
    assert matrix["vts.rpc"]["passed"] is True
    assert matrix["asr.synthetic_ingest"]["passed"] is True


def test_runtime_probe_main_fails_when_tts_playback_evidence_is_missing(monkeypatch, tmp_path: Path) -> None:
    bad_commit = _healthy_commit_event()
    bad_commit["audit_refs"]["tts"] = {
        "success": True,
        "status": "OK",
        "reason_code": "",
        "event": {"event_type": "TTS_SYNTH_READY", "start_event": {"event_type": "TTS_QUEUED"}},
    }

    exit_code, report = _run_main_with_fakes(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        commit_event=bad_commit,
    )

    assert exit_code == 2
    assert report["overall"]["passed"] is False
    assert "tts.rpc" in report["overall"]["failed_nodes"]


def test_runtime_probe_main_allows_degraded_tts_when_flag_is_enabled(monkeypatch, tmp_path: Path) -> None:
    degraded_commit = _healthy_commit_event()
    degraded_commit["audit_refs"]["tts"] = {
        "success": False,
        "status": "DEGRADED",
        "reason_code": "",
        "event": {
            "event_type": "TTS_PLAYBACK_FINISHED",
            "start_event": {"event_type": "TTS_PLAYBACK_STARTED"},
        },
    }

    exit_code, report = _run_main_with_fakes(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        commit_event=degraded_commit,
        extra_args=["--allow-degraded-tts"],
    )

    assert exit_code == 0
    assert report["overall"]["passed"] is True
    matrix = {row["node"]: row for row in report["matrix"]}
    assert matrix["tts.rpc"]["passed"] is True


def test_runtime_probe_main_falls_back_to_audit_db_when_commit_audit_refs_are_missing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    commit_without_voice = {
        "phase": "COMMIT",
        "done": True,
        "decision": "APPROVE",
        "request_id": "chat-req-001",
    }
    audit_db_result = {
        "available": True,
        "voice_event_count": 2,
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
        "tts_event_latest": [
            {
                "event_type": "TTS_PLAYBACK_FINISHED",
                "status": "ACCEPTED",
                "reason_code": None,
                "stage": "S_VOICE_TTS_PLAYBACK",
                "detail": "tts playback finished",
                "payload": {
                    "event_type": "TTS_PLAYBACK_FINISHED",
                    "request_id": "chat-req-001",
                },
                "ts_ms": 1776435000001,
            },
            {
                "event_type": "TTS_PLAYBACK_STARTED",
                "status": "ACCEPTED",
                "reason_code": None,
                "stage": "S_VOICE_TTS_PLAYBACK",
                "detail": "tts playback started",
                "payload": {
                    "event_type": "TTS_PLAYBACK_STARTED",
                    "request_id": "chat-req-001",
                },
                "ts_ms": 1776435000000,
            },
        ],
    }

    exit_code, report = _run_main_with_fakes(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        commit_event=commit_without_voice,
        audit_db_result=audit_db_result,
    )

    assert exit_code == 0
    assert report["overall"]["passed"] is True
    assert report["steps"]["runtime_voice_sources"] == {"tts": "audit_db", "vts": "audit_db"}
    matrix = {row["node"]: row for row in report["matrix"]}
    assert matrix["tts.rpc"]["passed"] is True
    assert matrix["vts.rpc"]["passed"] is True
