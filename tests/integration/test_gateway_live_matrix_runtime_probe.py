from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

from edge_client.gateway.webhook_security import build_internal_signature


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


def test_live_probe_signature_matches_gateway_contract() -> None:
    body = '{"msg_id":"live-1","text":"你好"}'.encode("utf-8")
    values = {
        "shared_secret": "acceptance-secret-with-at-least-32-bytes",
        "platform": "wechat",
        "method": "POST",
        "path": "/v1/gateway/wechat/webhook",
        "timestamp": "1785872000",
        "nonce": "live-probe-nonce",
        "raw_body": body,
    }

    assert probe._build_internal_webhook_signature(**values) == build_internal_signature(**values)


def test_live_probe_rejects_short_webhook_secret() -> None:
    try:
        probe._build_internal_webhook_signature(
            shared_secret="too-short",
            platform="wechat",
            method="POST",
            path="/v1/gateway/wechat/webhook",
            timestamp="1785872000",
            nonce="live-probe-nonce",
            raw_body=b"{}",
        )
    except ValueError as exc:
        assert "at least 32 bytes" in str(exc)
    else:
        raise AssertionError("short webhook secret must be rejected")


def test_live_probe_fails_fast_on_rejected_gateway_webhook(
    monkeypatch,
    tmp_path,
) -> None:
    host_log = tmp_path / "host.log"
    host_log.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        probe,
        "_send_gateway_webhook",
        lambda **_kwargs: {
            "sent_ts_ms": 2_000,
            "http_status": 404,
            "body": {"detail": "No adapter registered for platform: wechat"},
            "transport_error": None,
        },
    )
    monkeypatch.setattr(
        probe,
        "_fetch_gateway_event_by_conversation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("rejected webhook must not be polled")
        ),
    )

    outcome = probe._run_case(
        gateway_base_url="http://127.0.0.1:19000",
        webhook_hmac_secret="x" * 32,
        db_path=tmp_path / "audit.sqlite3",
        host_log_path=host_log,
        timeout_s=180,
        case=probe.LiveCase(
            case_id="translate_delegate",
            prompt="翻译",
            expected_targets=("translate_agent",),
        ),
        run_tag="rejected",
    )

    assert outcome.verdict == "FAIL"
    assert "HTTP 404" in outcome.summary
    assert "No adapter registered" in outcome.summary


def test_live_probe_reads_canonical_task_graph_without_legacy_trace(tmp_path) -> None:
    db_path = tmp_path / "audit.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE host_command_journal (
            request_id TEXT,
            session_id TEXT,
            route_type TEXT,
            target_id TEXT,
            graph_id TEXT,
            graph_revision INTEGER,
            created_at_ms INTEGER
        );
        CREATE TABLE task_runtime_graph (
            graph_id TEXT,
            revision INTEGER,
            status TEXT,
            created_ts_ms INTEGER,
            updated_ts_ms INTEGER
        );
        CREATE TABLE task_runtime_node (
            graph_id TEXT,
            graph_revision INTEGER,
            node_id TEXT,
            status TEXT,
            created_ts_ms INTEGER,
            updated_ts_ms INTEGER
        );
        """
    )
    conn.execute(
        "INSERT INTO host_command_journal VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "req-canonical",
            "session-canonical",
            "delegate",
            "translate_agent",
            "graph-canonical",
            2,
            2_000,
        ),
    )
    conn.execute(
        "INSERT INTO task_runtime_graph VALUES (?, ?, ?, ?, ?)",
        ("graph-canonical", 2, "COMPLETED", 2_010, 2_090),
    )
    conn.executemany(
        "INSERT INTO task_runtime_node VALUES (?, ?, ?, ?, ?, ?)",
        (
            ("graph-canonical", 2, "delegate:translate_agent", "COMPLETED", 2_020, 2_070),
            ("graph-canonical", 2, "surface_reply", "COMPLETED", 2_030, 2_080),
        ),
    )
    conn.commit()
    conn.close()

    graph = probe._fetch_graph_by_request_id(
        db_path=db_path,
        request_id="req-canonical",
        min_ts_ms=1_500,
    )

    assert graph is not None
    assert graph["authority"] == "canonical_task_runtime"
    assert graph["target_id"] == "translate_agent"
    assert graph["node_ids"] == ["delegate:translate_agent", "surface_reply"]
    assert graph["node_count"] == 2
    assert graph["total_latency_ms"] == 80


def test_live_probe_missing_graph_returns_none_without_legacy_table(tmp_path) -> None:
    db_path = tmp_path / "audit.sqlite3"
    sqlite3.connect(str(db_path)).close()

    assert probe._fetch_graph_by_request_id(
        db_path=db_path,
        request_id="req-missing",
        min_ts_ms=1_500,
    ) is None


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


def test_evaluate_case_checks_patterns_in_accepted_chat_narrative() -> None:
    case = probe.LiveCase(
        case_id="intent_planning",
        prompt="给我三步建议",
        expected_targets=(),
        required_patterns=("第一步", "第二步", "第三步"),
    )

    verdict, _summary, targets = probe._evaluate_case(
        case,
        request_id="req_chat_steps",
        governor_rows=[{"decision": "APPROVE"}],
        skill_rows=[],
        graph_row={
            "request_id": "req_chat_steps",
            "node_ids": ["node-chat"],
            "accepted_narrative": "第一步列清单，第二步排序，第三步开始做。",
        },
        log_lines=["input_routes: dispatch completed request_id=req_chat_steps"],
    )

    assert verdict == "PASS"
    assert targets == []


def test_evaluate_case_rejects_cancelled_canonical_planning_graph() -> None:
    case = probe.LiveCase(
        case_id="multi_skill_plan",
        prompt="请给我一个前端可用性审查方案。",
        expected_targets=("planning_search",),
    )

    verdict, summary, targets = probe._evaluate_case(
        case,
        request_id="req:cancelled-plan",
        governor_rows=[{"decision": "APPROVE"}],
        skill_rows=[],
        graph_row={
            "request_id": "req:cancelled-plan",
            "route_type": "plan",
            "target_id": "planning_search",
            "status": "CANCELLED",
            "authority": "canonical_task_runtime",
            "node_count": 3,
            "node_ids": ["node-1", "node-2", "node-3"],
            "node_statuses": {
                "node-1": "CANCELLED",
                "node-2": "CANCELLED",
                "node-3": "CANCELLED",
            },
        },
        log_lines=[],
        input_terminal={
            "event_type": "INPUT_COMPLETED",
            "status": "OK",
            "payload": {"decision": "APPROVE"},
        },
    )

    assert verdict == "FAIL"
    assert "CANCELLED" in summary
    assert targets == ["planning_search"]


def test_evaluate_case_rejects_clarification_only_planning_synthesis() -> None:
    case = probe.LiveCase(
        case_id="multi_skill_plan",
        prompt="请给我一个前端可用性审查方案。",
        expected_targets=("planning_search",),
    )

    verdict, summary, _targets = probe._evaluate_case(
        case,
        request_id="req:plan-clarification",
        governor_rows=[{"decision": "APPROVE"}],
        skill_rows=[
            {"skill_name": "agent_frontend_acceptance", "result_status": "SUCCESS"},
            {"skill_name": "agent_frontend_review", "result_status": "SUCCESS"},
        ],
        graph_row={
            "request_id": "req:plan-clarification",
            "route_type": "plan",
            "target_id": "planning_search",
            "status": "COMPLETED",
            "authority": "canonical_task_runtime",
            "node_count": 3,
            "node_statuses": {"node-1": "ACCEPTED", "node-2": "ACCEPTED", "node-3": "ACCEPTED"},
            "accepted_narrative": "你要审查的是哪个前端项目？有具体的页面或组件范围吗？我先确认一下。",
        },
        log_lines=[],
        input_terminal={
            "event_type": "INPUT_COMPLETED",
            "status": "OK",
            "payload": {"decision": "APPROVE"},
        },
    )

    assert verdict == "FAIL"
    assert "范围澄清" in summary


def test_evaluate_case_accepts_substantive_planning_synthesis() -> None:
    case = probe.LiveCase(
        case_id="multi_skill_plan",
        prompt="请给我一个前端可用性审查方案。",
        expected_targets=("planning_search",),
    )
    narrative = (
        "先建立路由、页面与关键用户流程清单，再逐页检查导航入口和返回路径。"
        "每个数据页面都覆盖加载状态、空状态、错误状态和权限边界，并记录复现证据。"
        "随后在移动端断点检查响应式布局、触控热区和横竖屏表现；使用键盘、ARIA 和"
        "屏幕阅读器完成无障碍审查。性能部分用 Lighthouse 记录首屏与 LCP，最后按"
        "影响范围、严重程度和修复成本划分优先级，交付包含截图、复现步骤、责任人与"
        "验收标准的审查报告。建议先覆盖登录、搜索、表单提交三个主流程，再扩展到其余页面。"
    )

    verdict, summary, _targets = probe._evaluate_case(
        case,
        request_id="req:plan-synthesis",
        governor_rows=[{"decision": "APPROVE"}],
        skill_rows=[
            {"skill_name": "agent_frontend_acceptance", "result_status": "SUCCESS"},
            {"skill_name": "agent_frontend_review", "result_status": "SUCCESS"},
        ],
        graph_row={
            "request_id": "req:plan-synthesis",
            "route_type": "plan",
            "target_id": "planning_search",
            "status": "COMPLETED",
            "authority": "canonical_task_runtime",
            "node_count": 3,
            "node_statuses": {"node-1": "ACCEPTED", "node-2": "ACCEPTED", "node-3": "ACCEPTED"},
            "accepted_narrative": narrative,
        },
        log_lines=[],
        input_terminal={
            "event_type": "INPUT_COMPLETED",
            "status": "OK",
            "payload": {"decision": "APPROVE"},
        },
    )

    assert verdict == "PASS"
    assert "可用执行证据" in summary


def test_evaluate_case_accepts_natural_chinese_three_step_enumeration() -> None:
    default_case = probe.build_default_cases()[1]
    case = probe.LiveCase(
        case_id=default_case.case_id,
        prompt=default_case.prompt,
        expected_targets=default_case.expected_targets,
        required_regexes=default_case.required_regexes,
    )

    verdict, _summary, targets = probe._evaluate_case(
        case,
        request_id="req_chat_natural_steps",
        governor_rows=[{"decision": "APPROVE"}],
        skill_rows=[],
        graph_row={
            "request_id": "req_chat_natural_steps",
            "node_ids": ["node-chat"],
            "accepted_narrative": "三步：一，把事情写下；二，选最急的；三，先做五分钟。",
        },
        log_lines=["input_routes: dispatch completed request_id=req_chat_natural_steps"],
    )

    assert verdict == "PASS"
    assert targets == []


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
    assert "expressionplan" in summary.lower()


def test_evaluate_case_uses_canonical_physical_expression_receipts() -> None:
    case = probe.LiveCase(
        case_id="translate_delegate",
        prompt="translate this",
        expected_targets=("translate_agent",),
        required_patterns=("rest",),
        require_local_voice=True,
    )
    synchronization_id = "sync::canonical-live-probe"
    shared = {
        "effect_status": "COMMITTED",
        "effect_reason_code": None,
        "outbox_status": "APPLIED",
        "outbox_reason_code": None,
        "receipt_accepted": True,
        "receipt_status": "APPLIED",
        "synchronization_id": synchronization_id,
    }

    verdict, summary, targets = probe._evaluate_case(
        case,
        request_id="req_translate_voice_applied",
        governor_rows=[{"decision": "APPROVE"}],
        skill_rows=[
            {
                "skill_name": "sub_agent:translate_agent",
                "result_status": "OK",
                "result_code": "SUB_AGENT_OK",
                "result_digest": "Please get some rest.",
            }
        ],
        graph_row={
            "request_id": "req_translate_voice_applied",
            "node_ids": ["sub_agent:translate_agent"],
        },
        log_lines=[
            "input_routes: dispatch completed request_id=req_translate_voice_applied"
        ],
        embodiment_rows=[
            {**shared, "command_kind": "voice", "command_action": "speak"},
            {
                **shared,
                "command_kind": "avatar",
                "command_action": "set_expression",
            },
        ],
    )

    assert verdict == "PASS"
    assert "可用执行证据" in summary
    assert targets == ["translate_agent"]


def test_evaluate_case_requires_every_segment_receipt_to_apply() -> None:
    case = probe.LiveCase(
        case_id="chat_voice_segments",
        prompt="say this",
        expected_targets=(),
        require_local_voice=True,
    )
    shared = {
        "effect_status": "COMMITTED",
        "outbox_status": "APPLIED",
        "receipt_accepted": True,
        "receipt_status": "APPLIED",
        "synchronization_id": "sync::segments",
    }

    verdict, summary, _targets = probe._evaluate_case(
        case,
        request_id="req_voice_segments",
        governor_rows=[{"decision": "APPROVE"}],
        skill_rows=[],
        graph_row={"request_id": "req_voice_segments", "node_ids": ["node-chat"]},
        log_lines=[],
        embodiment_rows=[
            {**shared, "command_kind": "voice", "command_action": "speak"},
            {
                **shared,
                "command_kind": "voice",
                "command_action": "speak",
                "outbox_status": "REJECTED",
                "receipt_accepted": False,
                "receipt_reason_code": "TTS_SEGMENT_FAILED",
            },
            {
                **shared,
                "command_kind": "avatar",
                "command_action": "set_expression",
            },
        ],
    )

    assert verdict == "FAIL"
    assert "TTS_SEGMENT_FAILED" in summary


def test_evaluate_case_marks_missing_external_avatar_as_pending_warning() -> None:
    case = probe.LiveCase(
        case_id="chat_external_avatar_pending",
        prompt="say this",
        expected_targets=(),
        require_local_voice=True,
    )
    shared = {
        "effect_status": "COMMITTED",
        "synchronization_id": "sync::external-avatar-pending",
    }

    verdict, summary, _targets = probe._evaluate_case(
        case,
        request_id="req_external_avatar_pending",
        governor_rows=[{"decision": "APPROVE"}],
        skill_rows=[],
        graph_row={
            "request_id": "req_external_avatar_pending",
            "node_ids": ["node-chat"],
        },
        log_lines=[],
        embodiment_rows=[
            {
                **shared,
                "command_kind": "voice",
                "command_action": "speak",
                "outbox_status": "APPLIED",
                "receipt_accepted": True,
                "receipt_status": "APPLIED",
            },
            {
                **shared,
                "command_kind": "avatar",
                "command_action": "set_expression",
                "outbox_status": "REJECTED",
                "receipt_accepted": False,
                "receipt_status": "REJECTED",
                "receipt_reason_code": "VTS_EXPRESSION_UNAVAILABLE",
            },
        ],
    )

    assert verdict == "WARN"
    assert "Avatar 外部环境待补齐" in summary


def test_expression_poll_keeps_waiting_for_edge_accepted_async_tts() -> None:
    shared = {
        "effect_status": "COMMITTED",
        "outbox_status": "APPLIED",
    }

    rows = [
        {**shared, "command_kind": "voice", "command_action": "speak"},
        {
            **shared,
            "command_kind": "avatar",
            "command_action": "set_expression",
            "outbox_status": "IN_DOUBT",
            "receipt_accepted": True,
            "receipt_status": "ACCEPTED",
            "completed_at_ms": 1_000,
        },
    ]

    assert not probe._companion_expression_is_terminal(
        rows,
        observed_at_ms=15_999,
        in_doubt_grace_ms=15_000,
    )
    assert not probe._companion_expression_is_terminal(
        rows,
        observed_at_ms=16_000,
        in_doubt_grace_ms=15_000,
    )


def test_expression_poll_can_close_unacknowledged_in_doubt_after_grace() -> None:
    rows = [
        {
            "effect_status": "COMMITTED",
            "command_kind": "voice",
            "command_action": "speak",
            "outbox_status": "APPLIED",
        },
        {
            "effect_status": "COMMITTED",
            "command_kind": "avatar",
            "command_action": "set_expression",
            "outbox_status": "IN_DOUBT",
            "receipt_accepted": None,
            "completed_at_ms": 1_000,
        },
    ]

    assert probe._companion_expression_is_terminal(
        rows,
        observed_at_ms=16_000,
        in_doubt_grace_ms=15_000,
    )


def test_expression_poll_reopens_canonical_rows_after_sleep_crosses_deadline(
    monkeypatch,
    tmp_path,
) -> None:
    now = [100.0]
    snapshots = [
        [
            {
                "effect_status": "COMMITTED",
                "command_kind": "voice",
                "command_action": "speak",
                "outbox_status": "IN_DOUBT",
                "receipt_accepted": True,
                "receipt_status": "ACCEPTED",
            },
            {
                "effect_status": "COMMITTED",
                "command_kind": "avatar",
                "command_action": "set_expression",
                "outbox_status": "REJECTED",
            },
        ],
        [
            {
                "effect_status": "COMMITTED",
                "command_kind": "voice",
                "command_action": "speak",
                "outbox_status": "APPLIED",
            },
            {
                "effect_status": "COMMITTED",
                "command_kind": "avatar",
                "command_action": "set_expression",
                "outbox_status": "REJECTED",
            },
        ],
    ]
    reads: list[int] = []

    def fetch(**_kwargs):
        index = min(len(reads), len(snapshots) - 1)
        reads.append(index)
        return snapshots[index]

    monkeypatch.setattr(probe, "_fetch_companion_expression_rows", fetch)
    monkeypatch.setattr(probe.time, "time", lambda: now[0])
    monkeypatch.setattr(
        probe.time,
        "sleep",
        lambda seconds: now.__setitem__(0, now[0] + float(seconds) + 0.1),
    )

    rows = probe._poll_companion_expression_rows(
        db_path=tmp_path / "audit.sqlite3",
        graph_id="graph:test",
        graph_revision=1,
        deadline=101.0,
    )

    assert len(reads) == 2
    assert rows[0]["outbox_status"] == "APPLIED"


def test_fetch_companion_expression_rows_reopens_effect_outbox_and_receipt(
    tmp_path,
) -> None:
    db_path = tmp_path / "audit.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE task_runtime_acceptance_effect_intent (
            effect_id TEXT,
            graph_id TEXT,
            graph_revision INTEGER,
            effect_type TEXT,
            status TEXT,
            reason_code TEXT,
            payload_json TEXT,
            receipt_ref TEXT,
            created_ts_ms INTEGER,
            updated_ts_ms INTEGER
        );
        CREATE TABLE host_embodiment_outbox (
            command_id TEXT,
            idempotency_key TEXT,
            status TEXT,
            reason_code TEXT,
            receipt_json TEXT,
            attempt_count INTEGER,
            created_at_ms INTEGER,
            updated_at_ms INTEGER,
            completed_at_ms INTEGER
        );
        """
    )
    command = {
        "command_id": "emb-canonical",
        "idempotency_key": "companion-expression:canonical",
        "kind": "voice",
        "action": "speak",
    }
    payload = {
        "embodiment_command": command,
        "expression_delivery_intent": {
            "delivery_id": "delivery-canonical",
            "synchronization_id": "sync-canonical",
            "channel": "VOICE",
            "action": "SPEAK",
        },
    }
    receipt = {
        "accepted": True,
        "status": "APPLIED",
        "event": {
            "event_type": "TTS_PLAYBACK_FINISHED",
            "external_side_effects": True,
        },
    }
    conn.execute(
        "INSERT INTO task_runtime_acceptance_effect_intent VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "effect-canonical",
            "graph-canonical",
            1,
            "host.companion_expression_enqueue",
            "COMMITTED",
            None,
            json.dumps(payload),
            "embodiment-outbox://id/outbox-canonical",
            2_000,
            2_100,
        ),
    )
    conn.execute(
        "INSERT INTO host_embodiment_outbox VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            command["command_id"],
            command["idempotency_key"],
            "APPLIED",
            None,
            json.dumps(receipt),
            1,
            2_050,
            2_150,
            2_150,
        ),
    )
    conn.commit()
    conn.close()

    rows = probe._fetch_companion_expression_rows(
        db_path=db_path,
        graph_id="graph-canonical",
        graph_revision=1,
    )

    assert len(rows) == 1
    assert rows[0]["command_kind"] == "voice"
    assert rows[0]["outbox_status"] == "APPLIED"
    assert rows[0]["receipt_event_type"] == "TTS_PLAYBACK_FINISHED"
    assert rows[0]["receipt_external_side_effects"] is True


def test_fetch_companion_expression_rows_expands_one_atomic_batch(tmp_path) -> None:
    db_path = tmp_path / "audit-batch.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE task_runtime_acceptance_effect_intent (
            effect_id TEXT, graph_id TEXT, graph_revision INTEGER,
            effect_type TEXT, status TEXT, reason_code TEXT,
            payload_json TEXT, receipt_ref TEXT,
            created_ts_ms INTEGER, updated_ts_ms INTEGER
        );
        CREATE TABLE host_embodiment_outbox (
            command_id TEXT, idempotency_key TEXT, status TEXT,
            reason_code TEXT, receipt_json TEXT, attempt_count INTEGER,
            created_at_ms INTEGER, updated_at_ms INTEGER, completed_at_ms INTEGER
        );
        """
    )
    entries = []
    for index, (kind, action, channel) in enumerate(
        (("voice", "speak", "VOICE"), ("avatar", "set_expression", "AVATAR"))
    ):
        command = {
            "command_id": f"emb-batch-{index}",
            "idempotency_key": f"companion-expression:batch:{index}",
            "kind": kind,
            "action": action,
        }
        entries.append(
            {
                "embodiment_command": command,
                "expression_delivery_intent": {
                    "delivery_id": f"delivery-batch-{index}",
                    "synchronization_id": "sync-batch",
                    "channel": channel,
                    "action": action.upper(),
                },
            }
        )
        conn.execute(
            "INSERT INTO host_embodiment_outbox VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                command["command_id"],
                command["idempotency_key"],
                "APPLIED",
                None,
                json.dumps({"accepted": True, "status": "APPLIED", "event": {}}),
                1,
                2_050,
                2_150,
                2_150,
            ),
        )
    conn.execute(
        "INSERT INTO task_runtime_acceptance_effect_intent VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "effect-batch",
            "graph-batch",
            1,
            "host.companion_expression_enqueue",
            "COMMITTED",
            None,
            json.dumps({"entries": entries}),
            "embodiment-outbox-batch://sha256/test",
            2_000,
            2_100,
        ),
    )
    conn.commit()
    conn.close()

    rows = probe._fetch_companion_expression_rows(
        db_path=db_path,
        graph_id="graph-batch",
        graph_revision=1,
    )

    assert [(row["command_kind"], row["command_action"]) for row in rows] == [
        ("voice", "speak"),
        ("avatar", "set_expression"),
    ]
    assert {row["synchronization_id"] for row in rows} == {"sync-batch"}


def test_run_case_refreshes_stale_graph_after_fast_input_terminal(
    monkeypatch,
    tmp_path,
) -> None:
    host_log = tmp_path / "host.log"
    host_log.write_text("", encoding="utf-8")
    graph_reads = 0

    monkeypatch.setattr(
        probe,
        "_send_gateway_webhook",
        lambda **_kwargs: {
            "sent_ts_ms": 2_000,
            "http_status": 200,
            "body": {"status": "accepted"},
            "transport_error": None,
        },
    )
    monkeypatch.setattr(
        probe,
        "_fetch_gateway_event_by_conversation",
        lambda *_args, **_kwargs: {"request_id": "req:late-graph"},
    )

    def fetch_graph(**_kwargs):
        nonlocal graph_reads
        graph_reads += 1
        return {
            "request_id": "req:late-graph",
            "target_id": "translate_agent",
            "node_ids": ["delegate:translate_agent", "surface_reply"],
            "status": "ACTIVE" if graph_reads == 1 else "COMPLETED",
        }

    monkeypatch.setattr(probe, "_fetch_graph_by_request_id", fetch_graph)
    monkeypatch.setattr(
        probe,
        "_read_log_chunk",
        lambda *_args, **_kwargs: (
            "input_routes: dispatch completed request_id=req:late-graph"
        ),
    )
    monkeypatch.setattr(
        probe,
        "_extract_request_id",
        lambda *_args, **_kwargs: "req:late-graph",
    )
    monkeypatch.setattr(
        probe,
        "_fetch_input_terminal_event",
        lambda *_args, **_kwargs: {
            "request_id": "req:late-graph",
            "event_type": "INPUT_COMPLETED",
            "status": "OK",
            "payload": {"decision": "APPROVE"},
        },
    )
    monkeypatch.setattr(
        probe,
        "_fetch_related_rows",
        lambda *_args, **_kwargs: (
            [{"decision": "APPROVE"}],
            [
                {
                    "skill_name": "sub_agent:translate_agent",
                    "result_status": "OK",
                    "result_code": "SUB_AGENT_OK",
                    "result_digest": "Please rest.",
                }
            ],
        ),
    )
    monkeypatch.setattr(
        probe,
        "_extract_request_lines",
        lambda *_args, **_kwargs: [],
    )

    outcome = probe._run_case(
        gateway_base_url="http://127.0.0.1:19000",
        webhook_hmac_secret="x" * 32,
        db_path=tmp_path / "audit.sqlite3",
        host_log_path=host_log,
        timeout_s=2,
        case=probe.LiveCase(
            case_id="translate_delegate",
            prompt="翻译",
            expected_targets=("translate_agent",),
            required_patterns=("rest",),
        ),
        run_tag="late_graph",
    )

    assert graph_reads == 2
    assert outcome.graph is not None
    assert outcome.graph["status"] == "COMPLETED"
    assert outcome.verdict == "PASS"


def test_evaluate_case_fails_on_dispatch_error_and_extracts_request_id() -> None:
    case = probe.LiveCase(
        case_id="system_info",
        prompt="蓝蓝，查看系统信息",
        expected_targets=("system_info",),
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


def test_extract_request_id_accepts_canonical_secure_ingress_separator() -> None:
    log_chunk = (
        "input_routes: received source=GATEWAY "
        "request_id=req:08eaf1a01e7e4aa5a379f7c17886c805 "
        "conversation_id=case-canonical"
    )

    assert probe._extract_request_id(
        log_chunk,
        conversation_id="case-canonical",
    ) == "req:08eaf1a01e7e4aa5a379f7c17886c805"


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


def test_fetch_input_terminal_event_returns_bound_failure_without_waiting_for_completion(
    tmp_path,
) -> None:
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
        VALUES (?, 'INPUT_FAILED', 'FAILED', 'INPUT_GRAPH_ERROR', ?, ?, ?)
        """,
        (
            "req:failed",
            "RuntimeError: delegate target unavailable",
            '{"source":"GATEWAY","conversation_id":"translate_failed"}',
            2000,
        ),
    )
    conn.commit()
    conn.close()

    event = probe._fetch_input_terminal_event(
        db_path,
        None,
        conversation_id="translate_failed",
        min_ts_ms=1500,
    )

    assert event is not None
    assert event["request_id"] == "req:failed"
    assert event["event_type"] == "INPUT_FAILED"
    assert probe._fetch_input_completed_event(
        db_path,
        None,
        conversation_id="translate_failed",
        min_ts_ms=1500,
    ) is None


def test_evaluate_case_reports_input_terminal_failure_reason() -> None:
    case = probe.LiveCase(
        case_id="translate_delegate",
        prompt="translate this",
        expected_targets=("translate_agent",),
    )

    verdict, summary, targets = probe._evaluate_case(
        case,
        request_id="req:failed",
        governor_rows=[{"decision": "APPROVE"}],
        skill_rows=[],
        graph_row=None,
        log_lines=[],
        input_terminal={
            "event_type": "INPUT_FAILED",
            "status": "FAILED",
            "reason_code": "INPUT_GRAPH_ERROR",
            "detail": "RuntimeError: delegate target unavailable",
        },
    )

    assert verdict == "FAIL"
    assert "INPUT_GRAPH_ERROR" in summary
    assert "delegate target unavailable" in summary
    assert targets == []


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


def test_fetch_gateway_event_by_conversation_returns_bound_failure_by_default(
    tmp_path,
) -> None:
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
        VALUES (?, 'INPUT_FAILED', 'FAILED', 'INPUT_GRAPH_ERROR', 'failed', ?, ?)
        """,
        (
            "req_failed",
            '{"source":"GATEWAY","conversation_id":"case_failed"}',
            2000,
        ),
    )
    conn.commit()
    conn.close()

    event = probe._fetch_gateway_event_by_conversation(
        db_path,
        conversation_id="case_failed",
        min_ts_ms=1500,
    )

    assert event is not None
    assert event["request_id"] == "req_failed"
    assert event["event_type"] == "INPUT_FAILED"


def test_fetch_gateway_event_by_conversation_binds_current_message_within_grace_window(
    tmp_path,
) -> None:
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
    conn.executemany(
        """
        INSERT INTO voice_event_log
        (request_id, event_type, status, reason_code, detail, payload, ts_ms)
        VALUES (?, ?, 'OK', NULL, 'event', ?, ?)
        """,
        (
            (
                "req_old",
                "INPUT_COMPLETED",
                json.dumps(
                    {
                        "source": "GATEWAY",
                        "conversation_id": "case_multiturn",
                        "msg_id": "turn-old",
                    }
                ),
                1_950,
            ),
            (
                "req_current",
                "INPUT_ACCEPTED",
                json.dumps(
                    {
                        "source": "GATEWAY",
                        "conversation_id": "case_multiturn",
                        "msg_id": "turn-current",
                    }
                ),
                2_500,
            ),
        ),
    )
    conn.commit()
    conn.close()

    event = probe._fetch_gateway_event_by_conversation(
        db_path,
        conversation_id="case_multiturn",
        min_ts_ms=2_000,
        msg_id="turn-current",
    )

    assert event is not None
    assert event["request_id"] == "req_current"
    assert event["payload"]["msg_id"] == "turn-current"


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
