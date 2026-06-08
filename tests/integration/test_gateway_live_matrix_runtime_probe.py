from __future__ import annotations

import importlib.util
from pathlib import Path
import sqlite3
import sys


def _load_module():
    module_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "runtime"
        / "test_gateway_live_matrix.py"
    )
    spec = importlib.util.spec_from_file_location(
        "gateway_live_matrix_runtime_probe_test_module",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load gateway live matrix module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = _load_module()


def test_evaluate_case_passes_when_target_and_output_match() -> None:
    case = probe.LiveCase(
        case_id="translate_delegate",
        prompt="把“今天辛苦了，早点休息”翻译成英文。",
        expected_targets=("translate_agent",),
        required_patterns=("rest",),
    )

    verdict, summary, targets = probe._evaluate_case(
        case,
        request_id="req_translate_ok",
        governor_rows=[{"decision": "APPROVE"}],
        skill_rows=[
            {
                "skill_name": "sub_agent:translate_agent",
                "result_status": "OK",
                "result_code": "SUB_AGENT_OK",
                "result_digest": "You've worked hard today. Get some rest.",
            }
        ],
        graph_row={
            "request_id": "req_translate_ok",
            "node_ids": ["sub_agent:translate_agent", "surface_reply"],
        },
        log_lines=["input_routes: dispatch completed request_id=req_translate_ok"],
    )

    assert verdict == "PASS"
    assert "可用执行证据" in summary
    assert targets == ["translate_agent"]


def test_evaluate_case_warns_when_planning_case_has_no_steps() -> None:
    case = probe.LiveCase(
        case_id="intent_planning",
        prompt="帮我整理一下我现在应该先做什么，再给我一个三步建议。",
        expected_targets=("intent_agent",),
        required_patterns=("第一步", "第二步", "第三步"),
    )

    verdict, summary, targets = probe._evaluate_case(
        case,
        request_id="req_plan_warn",
        governor_rows=[{"decision": "APPROVE"}],
        skill_rows=[
            {
                "skill_name": "sub_agent:intent_agent",
                "result_status": "OK",
                "result_code": "SUB_AGENT_OK",
                "result_digest": "为了给你一个有用的建议，我需要先了解你目前的情况。",
            }
        ],
        graph_row={
            "request_id": "req_plan_warn",
            "node_ids": ["sub_agent:intent_agent", "surface_reply"],
        },
        log_lines=["input_routes: dispatch completed request_id=req_plan_warn"],
    )

    assert verdict == "WARN"
    assert "关键判定模式" in summary
    assert targets == ["intent_agent"]


def test_evaluate_case_fails_when_voice_proof_is_required_but_missing() -> None:
    case = probe.LiveCase(
        case_id="translate_delegate",
        prompt="translate this",
        expected_targets=("translate_agent",),
        required_patterns=("rest",),
        require_local_voice=True,
    )

    verdict, summary, _targets = probe._evaluate_case(
        case,
        request_id="req_translate_voice_missing",
        governor_rows=[{"decision": "APPROVE"}],
        skill_rows=[
            {
                "skill_name": "sub_agent:translate_agent",
                "result_status": "OK",
                "result_code": "SUB_AGENT_OK",
                "result_digest": "Please get some rest.",
            }
        ],
        graph_row={"request_id": "req_translate_voice_missing", "node_ids": ["sub_agent:translate_agent"]},
        log_lines=["input_routes: dispatch completed request_id=req_translate_voice_missing"],
        input_completed={"payload": {"gateway_voice_success": False}},
    )

    assert verdict == "FAIL"
    assert "edge" in summary.lower() or "vts" in summary.lower()


def test_evaluate_case_fails_on_dispatch_error_and_extracts_request_id() -> None:
    case = probe.LiveCase(
        case_id="hub_skill_list",
        prompt="蓝蓝，列出技能",
        expected_targets=("hub_skill_list",),
    )
    log_chunk = "\n".join(
        [
            "2026-04-16 22:03:18,894 - Natsume.input_routes - INFO - input_routes: received edge input source=GATEWAY request_id=req_b0a0684136d04d34a20c2ed96fa8ef2f",
            "2026-04-16 22:03:22,475 - Natsume.input_routes - ERROR - input_routes: dispatch failed request_id=req_b0a0684136d04d34a20c2ed96fa8ef2f: 'skill not found: skill_manager'",
        ]
    )

    request_id = probe._extract_request_id(log_chunk)
    verdict, summary, targets = probe._evaluate_case(
        case,
        request_id=request_id,
        governor_rows=[],
        skill_rows=[],
        graph_row=None,
        log_lines=probe._extract_request_lines(log_chunk, request_id),
    )

    assert request_id == "req_b0a0684136d04d34a20c2ed96fa8ef2f"
    assert verdict == "FAIL"
    assert "dispatch failed" in summary
    assert targets == []


def test_extract_request_id_prefers_lines_bound_to_current_conversation() -> None:
    log_chunk = "\n".join(
        [
            "2026-04-17 02:01:01,000 - Natsume.input_routes - INFO - input_routes: received edge input source=GATEWAY request_id=req_a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0",
            "2026-04-17 02:01:02,000 - Natsume.tribe.ans_agent - INFO - ANSAgent 感知语音管线启动: request_id=req_b1b1b1b1b1b1b1b1b1b1b1b1b1b1b1b1 session_id=other_case_123 stage=S_VOICE_TTS_PLAYBACK",
            "2026-04-17 02:01:03,000 - Natsume.tribe.ans_agent - INFO - ANSAgent 感知语音管线启动: request_id=req_c2c2c2c2c2c2c2c2c2c2c2c2c2c2c2c2 session_id=case_456 stage=S_VOICE_TTS_PLAYBACK",
        ]
    )

    assert probe._extract_request_id(log_chunk, conversation_id="case_456") == "req_c2c2c2c2c2c2c2c2c2c2c2c2c2c2c2c2"


def test_extract_request_id_returns_none_when_conversation_id_has_no_bound_lines() -> None:
    log_chunk = "\n".join(
        [
            "2026-04-17 02:01:01,000 - Natsume.input_routes - INFO - input_routes: received edge input source=GATEWAY request_id=req_a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0",
            "2026-04-17 02:01:02,000 - Natsume.tribe.ans_agent - INFO - ANSAgent request_id=req_b1b1b1b1b1b1b1b1b1b1b1b1b1b1b1b1 session_id=other_case_123",
        ]
    )

    assert probe._extract_request_id(log_chunk, conversation_id="case_999") is None


def test_fetch_input_completed_event_falls_back_to_conversation_id(tmp_path) -> None:
    db_path = tmp_path / "audit.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE voice_event_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT,
            event_type TEXT,
            status TEXT,
            reason_code TEXT,
            detail TEXT,
            payload TEXT,
            ts_ms INTEGER
        )
        """
    )
    conn.execute(
        """
        INSERT INTO voice_event_log (request_id, event_type, status, reason_code, detail, payload, ts_ms)
        VALUES (?, 'INPUT_COMPLETED', 'OK', NULL, 'old', ?, ?)
        """,
        (
            "req_old",
            '{"source":"GATEWAY","conversation_id":"other_case"}',
            1000,
        ),
    )
    conn.execute(
        """
        INSERT INTO voice_event_log (request_id, event_type, status, reason_code, detail, payload, ts_ms)
        VALUES (?, 'INPUT_COMPLETED', 'OK', NULL, 'new', ?, ?)
        """,
        (
            "req_new",
            '{"source":"GATEWAY","conversation_id":"case_789","gateway_voice_success":true}',
            2000,
        ),
    )
    conn.commit()
    conn.close()

    event = probe._fetch_input_completed_event(
        db_path,
        "req_old",
        conversation_id="case_789",
        min_ts_ms=1500,
    )

    assert event is not None
    assert event["request_id"] == "req_new"
    assert event["payload"]["gateway_voice_success"] is True


def test_fetch_input_completed_event_returns_none_when_no_conversation_match(tmp_path) -> None:
    db_path = tmp_path / "audit.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE voice_event_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT,
            event_type TEXT,
            status TEXT,
            reason_code TEXT,
            detail TEXT,
            payload TEXT,
            ts_ms INTEGER
        )
        """
    )
    conn.execute(
        """
        INSERT INTO voice_event_log (request_id, event_type, status, reason_code, detail, payload, ts_ms)
        VALUES (?, 'INPUT_COMPLETED', 'OK', NULL, 'translate', ?, ?)
        """,
        (
            "req_translate",
            '{"source":"GATEWAY","conversation_id":"translate_case"}',
            2000,
        ),
    )
    conn.commit()
    conn.close()

    event = probe._fetch_input_completed_event(
        db_path,
        None,
        conversation_id="planning_case",
        min_ts_ms=1500,
    )

    assert event is None


def test_fetch_gateway_event_by_conversation_returns_bound_route_decided(tmp_path) -> None:
    db_path = tmp_path / "audit.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE voice_event_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT,
            event_type TEXT,
            status TEXT,
            reason_code TEXT,
            detail TEXT,
            payload TEXT,
            ts_ms INTEGER
        )
        """
    )
    conn.execute(
        """
        INSERT INTO voice_event_log (request_id, event_type, status, reason_code, detail, payload, ts_ms)
        VALUES (?, 'INPUT_ROUTE_DECIDED', 'OK', NULL, 'route', ?, ?)
        """,
        (
            "req_other",
            '{"source":"GATEWAY","conversation_id":"other_case","route_target":"memory_store"}',
            1000,
        ),
    )
    conn.execute(
        """
        INSERT INTO voice_event_log (request_id, event_type, status, reason_code, detail, payload, ts_ms)
        VALUES (?, 'INPUT_ROUTE_DECIDED', 'OK', NULL, 'route', ?, ?)
        """,
        (
            "req_bound",
            '{"source":"GATEWAY","conversation_id":"case_123","route_target":"intent_agent"}',
            2000,
        ),
    )
    conn.commit()
    conn.close()

    event = probe._fetch_gateway_event_by_conversation(
        db_path,
        conversation_id="case_123",
        min_ts_ms=1500,
    )

    assert event is not None
    assert event["request_id"] == "req_bound"


def test_edge_voice_pipeline_idle_returns_true_when_queue_and_playback_are_clear() -> None:
    body = {
        "event": {
            "components": {
                "resource_gate": {"snapshot": {"active_tasks": [], "metrics": {"queue_depth": 0}}},
                "tts_playback_device": {"health": {"playing": False}},
                "voice_pipeline": {"health": {"mouth_sync_thread_alive": False}},
            }
        }
    }

    assert probe._edge_voice_pipeline_idle(body) is True


def test_edge_voice_pipeline_idle_returns_false_when_playback_is_active() -> None:
    body = {
        "event": {
            "components": {
                "resource_gate": {"snapshot": {"active_tasks": ["PLAYBACK"], "metrics": {"queue_depth": 1}}},
                "tts_playback_device": {"health": {"playing": True}},
                "voice_pipeline": {"health": {"mouth_sync_thread_alive": True}},
            }
        }
    }

    assert probe._edge_voice_pipeline_idle(body) is False
