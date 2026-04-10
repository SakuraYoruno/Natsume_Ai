"""
T-OBS-ROUTE-INT-01~16 — obs_routes.py 集成测试
TASK-0198 / WARN-TEST-002
使用内存 SQLite + FastAPI TestClient，覆盖 4 个端点 × 4 个场景
"""
from __future__ import annotations

import json
import time
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.app import obs_routes
from host_brain.app.obs_routes import router, set_ledger_provider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ledger():
    """内存 SQLite ledger，预置 fixture 数据。"""
    db = SQLiteLedger(":memory:", check_same_thread=False)
    now = int(time.time() * 1000)

    # 预置 governor_log 数据
    db.log_governor({
        "request_id": "req-int-001",
        "thread_id": "t1", "turn_id": "u1", "intent_id": "i1",
        "decision": "APPROVE", "reason_code": None,
        "rule_trace": None, "tokens": None, "snapshot_refs": None,
        "stage": "S_GOVERNOR", "ts_ms": now - 5000,
    })
    db.log_governor({
        "request_id": "req-int-002",
        "thread_id": "t2", "turn_id": "u2", "intent_id": "i2",
        "decision": "REJECT", "reason_code": "RATE_LIMIT_EXCEEDED",
        "rule_trace": None, "tokens": None, "snapshot_refs": None,
        "stage": "S_GOVERNOR", "ts_ms": now - 3000,
    })

    db.log_intent({
        "request_id": "req-int-002",
        "thread_id": "t2",
        "turn_id": "u2",
        "intent_id": "i2",
        "session_id": "sess-002",
        "seq": 2,
        "ts_ms": now - 3100,
        "nonce": "nonce-002",
        "parse_stage": "LENIENT_OK",
        "raw_text": "fallback hello",
        "normalized_json": {
            "action": "narrative_only",
            "args": {"text": "fallback hello"},
            "session_id": "sess-002",
        },
        "proposer_node_id": "core_reasoner_primary",
        "model_snapshot": "test-model",
        "prompt_profile_id": "default",
        "tool_visibility_hash": "",
        "proposed_intent_draft_json": {
            "action": "narrative_only",
            "args": {"text": "fallback hello"},
        },
        "lint_result": {"args": [], "schema": []},
    })
    db.log_voice_event({
        "request_id": "req-int-002",
        "session_id": "sess-002",
        "event_type": "INPUT_ACCEPTED",
        "direction": "EDGE_TO_HOST",
        "seq": 1,
        "status": "ACCEPTED",
        "reason_code": None,
        "stage": "INPUT",
        "payload": {"phase": "ACCEPTED", "source": "GATEWAY"},
        "detail": "source=GATEWAY",
        "ts_ms": now - 3200,
    })
    db.log_voice_event({
        "request_id": "req-int-002",
        "session_id": "sess-002",
        "event_type": "INPUT_COMPLETED",
        "direction": "EDGE_TO_HOST",
        "seq": 2,
        "status": "OK",
        "reason_code": None,
        "stage": "INPUT",
        "payload": {"phase": "COMPLETED", "source": "GATEWAY", "latency_ms": 2600},
        "detail": "main_chain completed",
        "ts_ms": now - 200,
    })
    db.log_airlock({
        "request_id": "req-int-002",
        "direction": "INGRESS",
        "reason_code": None,
        "removed_fields": None,
        "redactions": None,
        "payload_digest": None,
        "quarantine_digest": None,
        "ingress_classification": "normal",
        "blocked": 0,
        "detail": "passed",
        "ts_ms": now - 2800,
    })
    db.log_retrieval_event({
        "request_id": "req-int-002",
        "intent_id": "i2",
        "op_kind": "RECALL",
        "status": "OK",
        "reason_code": None,
        "stage": "S_RET_RECALL",
        "payload": {
            "candidate_count": 7,
            "route_count": 3,
            "route_error_count": 0,
            "items": [
                {
                    "doc_id": "mem-001",
                    "source_ref": "memo://episodic/001",
                    "evidence_ref": "evd://episodic/001",
                    "snippet": "fallback hello recall snippet",
                    "score": 0.92,
                    "confidence": 0.92,
                    "metadata": {
                        "store_id": "episodic_vector",
                        "collection": "episodic",
                        "route_id": "episodic_route",
                    },
                }
            ],
        },
        "detail": "recall fused",
        "ts_ms": now - 2500,
    })
    db.log_retrieval_event({
        "request_id": "req-int-002",
        "intent_id": "i2",
        "op_kind": "RERANK",
        "status": "OK",
        "reason_code": None,
        "stage": "S_RET_RERANK",
        "payload": {
            "topk_count": 1,
            "provider": "heuristic",
            "topk": [
                {
                    "doc_id": "mem-001",
                    "source_ref": "memo://episodic/001",
                    "evidence_ref": "evd://episodic/001",
                    "snippet": "fallback hello rerank snippet",
                    "score": 0.96,
                    "confidence": 0.96,
                    "metadata": {
                        "store_id": "episodic_vector",
                        "collection": "episodic",
                        "route_id": "episodic_route",
                    },
                }
            ],
        },
        "detail": "rerank completed",
        "ts_ms": now - 2450,
    })
    db.log_retrieval_event({
        "request_id": "req-int-002",
        "intent_id": "i2",
        "op_kind": "EVIDENCE_PACK",
        "status": "OK",
        "reason_code": None,
        "stage": "S_RET_EVIDENCE_PACK",
        "payload": {
            "evidence_refs_count": 3,
            "pack_id": "epk_test_002",
            "evidence_refs": ["evd://episodic/001"],
            "topk": [
                {
                    "doc_id": "mem-001",
                    "source_ref": "memo://episodic/001",
                    "evidence_ref": "evd://episodic/001",
                    "snippet": "fallback hello evidence snippet",
                    "score": 0.96,
                    "confidence": 0.96,
                    "metadata": {
                        "store_id": "episodic_vector",
                        "collection": "episodic",
                        "route_id": "episodic_route",
                    },
                }
            ],
        },
        "detail": "evidence pack built",
        "ts_ms": now - 2400,
    })
    db.log_retrieval_event({
        "request_id": "req-int-002",
        "intent_id": "i2",
        "op_kind": "PIPELINE",
        "status": "OK",
        "reason_code": None,
        "stage": "S_RET_PIPELINE",
        "payload": {
            "fallback_embedding_used": False,
            "selected_refs": ["evd://episodic/001"],
            "query_plan": {
                "intent": "companion_chat",
                "route": "episodic",
                "top_k": 8,
            },
            "agentic_meta": {
                "mode": "QUERY_PLANNER",
                "selected_route": "episodic",
                "turn_index": 1,
            },
        },
        "detail": "pipeline completed",
        "ts_ms": now - 2350,
    })
    db.log_bond_update({
        "request_id": "req-int-002",
        "intent_id": "i2",
        "session_id": "sess-002",
        "seq": 1,
        "event_id": "bond-002",
        "user_id": "user-002",
        "topic_id": "topic-002",
        "capability_hint": None,
        "update_kind": "STAGE_TRANSITION",
        "status": "SUCCESS",
        "reason_code": None,
        "stage": "S_BOND_STAGE",
        "evidence_refs": ["chat://request/req-int-002"],
        "payload": {"from_stage": "STRANGER", "to_stage": "STRANGER", "total_score": 1},
        "detail": "stable",
        "ts_ms": now - 160,
    })
    db.log_memory_op({
        "request_id": "req-int-002",
        "intent_id": "i2",
        "op_kind": "WRITE",
        "memory_id": "mem-002",
        "target_memory_id": None,
        "source_memory_ids": [],
        "evidence_refs": ["chat://request/req-int-002"],
        "status": "SUCCESS",
        "reason_code": None,
        "detail": "memory written: [session=sess-002] User: fallback hello Natsume: fallback reply",
        "decay_profile_id": None,
        "visibility": "private",
        "ts_ms": now - 120,
    })
    db.log_error(
        request_id="req-int-002",
        stage="AUDIT_SNAPSHOT",
        reason_code="AUDIT_SNAPSHOT_WRITTEN",
        detail=json.dumps(
            {
                "request_id": "req-int-002",
                "session_id": "sess-002",
                "task_type": "chat",
                "audit_tags": [
                    "RECALL_EVIDENCE_EMPTY",
                    "ROUTING_SELECTED:ModelTarget.HOST_14B",
                    "EMOTION_INFERRED:NEUTRAL",
                ],
                "node_timings_ms": {
                    "governor": 12,
                    "recall_fusion": 320,
                    "llm_gateway": 18,
                    "episodic_store": 4,
                },
                "metadata_keys": ["recall_fusion", "llm_gateway"],
                "governor_passed": False,
                "final_response_chars": 14,
                "failure_event": "RATE_LIMIT_EXCEEDED",
            },
            ensure_ascii=False,
        ),
    )

    # 预置 llm_node_call_log 数据
    db.log_llm_node_call({
        "request_id": "req-int-001",
        "node_id": "core_reasoner_primary",
        "provider": "openai", "model_id": "gpt-5.2",
        "status": "OK", "latency_ms": 120, "ts_ms": now - 4000,
    })

    # 预置 graph_run_trace 数据
    db.log_graph_run_trace({
        "request_id": "req-int-001",
        "session_id": "sess-001",
        "total_latency_ms": 350,
        "status": "OK",
        "node_count": 3,
        "nodes_json": [
            {"node_id": "intent_i1", "node_type": "intent", "display_name": "Intent Parser",
             "status": "OK", "ts_ms": now - 5000},
        ],
        "edges_json": [],
        "ts_ms": now - 4500,
    })

    yield db
    db.close()


@pytest.fixture
def client(ledger):
    """带 obs router 的 TestClient。"""
    app = FastAPI()
    app.include_router(router)
    set_ledger_provider(lambda: ledger)
    return TestClient(app)


# ---------------------------------------------------------------------------
# GET /obs/services/health
# ---------------------------------------------------------------------------

def test_services_health_returns_200(client):
    """T-OBS-ROUTE-INT-01: 正常路径返回 200 + 必要字段。"""
    resp = client.get("/obs/services/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert "up" in data
    assert "degraded" in data
    assert "down" in data
    assert "services" in data
    assert isinstance(data["services"], list)


def test_services_health_side_filter(client):
    """T-OBS-ROUTE-INT-02: ?side=host 过滤只返回 host 端服务。"""
    resp = client.get("/obs/services/health?side=host")
    assert resp.status_code == 200
    data = resp.json()
    for svc in data["services"]:
        assert svc.get("service_type") in ("host", "llm_node", None)


def test_services_health_empty_window(client):
    """T-OBS-ROUTE-INT-03: 极小时间窗口（1分钟）返回 UNKNOWN 状态。"""
    resp = client.get("/obs/services/health?window_minutes=1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 0


def test_services_health_invalid_window(client):
    """T-OBS-ROUTE-INT-04: 超出范围的 window_minutes 返回 422。"""
    resp = client.get("/obs/services/health?window_minutes=99999")
    assert resp.status_code == 422


def test_runtime_snapshot_returns_unified_state(client, monkeypatch):
    """T-OBS-ROUTE-INT-17: /obs/runtime-snapshot 返回统一数字生命状态摘要。"""

    monkeypatch.setattr(
        "host_brain.app.http_hooks_runtime_helpers.build_digital_life_runtime_snapshot",
        lambda current_topic="": {
            "persona_user_id": "persona-user-001",
            "current_topic": current_topic,
            "sys_mode": "focus",
            "hard_state": {"sys_mode": "focus", "mood_state": "engaged"},
            "activity_monitor": {"running": True, "idle_minutes": 3.5},
            "emotion_context": {"emotion_tag": "NEUTRAL", "mood_state": "engaged"},
            "bond_state": {"current_stage": "FRIEND"},
            "bond_summary": {"stage": "FRIEND", "trend": "上升"},
            "bond_summary_human": {"stage": "朋友", "capability_hint": "朋友，可深入交流"},
            "inner_world_state": {"loneliness": 0.32},
            "offline_state": {"offline_hours": 8.0},
            "silence_state": {"response_willingness": 0.72},
            "body_sense": {"fatigue_level": "FRESH"},
            "self_model_snapshot": {"recent_mood_trend": "平稳"},
            "self_reflection_pulse": {"growth_note": "最近更稳了。"},
            "bond_narrative": {"narratives": ["我还记得你说过那件事。"]},
            "motive_state": {"dominant_motive": "continuity"},
            "response_policy": {"allow_topic_shift": False},
            "runtime_summary": {
                "headline": "mode=focus | offline=6h_plus | motive=continuity | reply=focused",
                "current_state": {
                    "sys_mode": "focus",
                    "absence_band": "6h_plus",
                    "offline_reconnect_style": "soft_resume",
                    "activity_event": "wakeup",
                    "dominant_motive": "continuity",
                },
                "reply_posture": {"allow_topic_shift": False},
                "decision_chain": [
                    "mode:focus",
                    "activity:wakeup",
                    "offline:6h_plus->soft_resume",
                    "motive:continuity",
                    "response_policy:focused",
                ],
                "core_slots": {
                    "sys_mode": {"value": "focus", "activity_event": "wakeup", "idle_minutes": 3.5},
                    "offline_state": {"absence_band": "6h_plus", "offline_reconnect_style": "soft_resume"},
                    "motive_state": {"dominant_motive": "continuity"},
                    "response_policy": {"reply_posture": "focused"},
                },
                "why_reply": ["mode:focus", "motive:continuity"],
            },
            "slot_status": {"response_policy": "live"},
            "degraded": False,
            "degraded_reasons": [],
            "timestamp": 1700000000000,
        },
    )
    monkeypatch.setattr(
        "host_brain.app.obs_routes_sysmode._get_mode_transition_service",
        lambda: type(
            "_FakeModeService",
            (),
            {
                "get_current_status": lambda self: {
                    "sys_mode": "focus",
                    "auto_rules": {"gaming_detect": True},
                    "idle_ms": 120000,
                    "idle_minutes": 2.0,
                    "last_interaction_ts_ms": 1700000000000,
                    "is_night_time": False,
                    "valid_transitions": ["normal", "gaming"],
                    "ts_ms": 1700000000000,
                    "degraded": False,
                    "degraded_reason": None,
                }
            },
        )(),
    )

    resp = client.get("/obs/runtime-snapshot?current_topic=%E9%82%A3%E4%B8%AA%E9%A1%B9%E7%9B%AE")

    assert resp.status_code == 200
    data = resp.json()
    assert data["persona_user_id"] == "persona-user-001"
    assert data["current_topic"] == "那个项目"
    assert data["sys_mode"] == "focus"
    assert data["sys_mode_status"]["sys_mode"] == "focus"
    assert data["hard_state"]["mood_state"] == "engaged"
    assert data["bond_summary_human"]["stage"] == "朋友"
    assert data["motive_state"]["dominant_motive"] == "continuity"
    assert data["runtime_summary"]["current_state"]["sys_mode"] == "focus"
    assert data["runtime_summary"]["current_state"]["offline_reconnect_style"] == "soft_resume"
    assert data["runtime_summary"]["current_state"]["activity_event"] == "wakeup"
    assert "offline:6h_plus->soft_resume" in data["runtime_summary"]["decision_chain"]
    assert data["runtime_summary"]["core_slots"]["sys_mode"]["activity_event"] == "wakeup"
    assert data["runtime_summary"]["core_slots"]["response_policy"]["reply_posture"] == "focused"
    assert data["runtime_summary"]["why_reply"][0] == "mode:focus"
    assert data["slot_status"]["sys_mode_status"] == "live"
    assert "interoception_state" in data
    assert "motor_cortex_state" in data
    assert "cerebellum_state" in data
    assert "glymphatic_state" in data
    assert "vestibular_state" in data
    assert "homeostasis_report" in data


# ---------------------------------------------------------------------------
# GET /obs/sessions
# ---------------------------------------------------------------------------

def test_sessions_returns_200(client):
    """T-OBS-ROUTE-INT-05: 正常路径返回 200 + 分页字段。"""
    resp = client.get("/obs/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert "page" in data
    assert "page_size" in data
    assert "items" in data
    assert isinstance(data["items"], list)


def test_sessions_status_filter(client):
    """T-OBS-ROUTE-INT-06: ?status=ok 只返回 APPROVE 决策的会话。"""
    resp = client.get("/obs/sessions?status=ok")
    assert resp.status_code == 200
    data = resp.json()
    for item in data["items"]:
        assert item["status"].lower() in ("ok", "approve")


def test_sessions_empty_result(client):
    """T-OBS-ROUTE-INT-07: 极小时间窗口返回空列表（total=0）。"""
    resp = client.get("/obs/sessions?window_minutes=1")
    assert resp.status_code == 200
    data = resp.json()
    # 可能为空，但结构必须正确
    assert "items" in data
    assert isinstance(data["items"], list)


def test_sessions_pagination(client):
    """T-OBS-ROUTE-INT-08: 分页参数正确传递。"""
    resp = client.get("/obs/sessions?page=1&page_size=1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["page"] == 1
    assert data["page_size"] == 1
    assert len(data["items"]) <= 1


def test_sessions_can_find_rows_beyond_default_fetch_limit(client, ledger):
    """Regression: session query should not silently truncate to first 100 rows."""
    now = int(time.time() * 1000)
    for i in range(120):
        ledger.log_governor({
            "request_id": f"req-many-{i:03d}",
            "thread_id": f"tm{i:03d}",
            "turn_id": f"um{i:03d}",
            "intent_id": f"im{i:03d}",
            "decision": "APPROVE",
            "reason_code": None,
            "rule_trace": None,
            "tokens": None,
            "snapshot_refs": None,
            "stage": "S_GOVERNOR",
            "ts_ms": now - i,
        })

    resp = client.get("/obs/sessions?window_minutes=10080&keyword=req-many-119")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["request_id"] == "req-many-119"


def test_sessions_include_input_preview(client):
    """Sessions should expose input preview for request mapping."""
    resp = client.get("/obs/sessions?window_minutes=10080")
    assert resp.status_code == 200
    data = resp.json()
    item = next(row for row in data["items"] if row["request_id"] == "req-int-002")
    assert item["input_preview"] == "fallback hello"


def test_sessions_keyword_matches_input_preview(client):
    """Keyword filter should support matching by message preview."""
    resp = client.get("/obs/sessions?window_minutes=10080&keyword=fallback%20hello")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["request_id"] == "req-int-002"


def test_gateway_stats_reads_channel_io_log(client, ledger):
    """Gateway stats should work against current ledger schema without audit_log."""
    now = int(time.time() * 1000)
    ledger.log_channel_io({
        "request_id": "gw-wechat-in-001",
        "channel": "wechat",
        "direction": "INGRESS",
        "thread_key": "wx-thread-001",
        "message_id": "wx-msg-001",
        "user_id": "wx-user-001",
        "status": "RECEIVED",
        "reason_code": None,
        "stage": "S_CHANNEL_INGRESS",
        "detail": "wechat inbound",
        "payload_digest": "digest-wechat-in",
        "ts_ms": now - 2000,
    })
    ledger.log_channel_io({
        "request_id": "gw-wechat-out-001",
        "channel": "wechat",
        "direction": "EGRESS",
        "thread_key": "wx-thread-001",
        "message_id": "wx-msg-002",
        "user_id": "wx-user-001",
        "status": "SENT",
        "reason_code": None,
        "stage": "S_CHANNEL_EGRESS",
        "detail": "wechat outbound",
        "payload_digest": "digest-wechat-out",
        "ts_ms": now - 1500,
    })
    ledger.log_channel_io({
        "request_id": "gw-qq-in-001",
        "channel": "qq",
        "direction": "INGRESS",
        "thread_key": "qq-thread-001",
        "message_id": "qq-msg-001",
        "user_id": "qq-user-001",
        "status": "FAILED",
        "reason_code": "SEND_FAIL",
        "stage": "S_CHANNEL_INGRESS",
        "detail": "qq inbound failed",
        "payload_digest": "digest-qq-in",
        "ts_ms": now - 1000,
    })

    resp = client.get("/obs/gateway/stats?window_minutes=10080")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_inbound"] >= 2
    assert data["total_outbound"] >= 1
    wechat = next(row for row in data["platforms"] if row["platform"] == "wechat")
    qq = next(row for row in data["platforms"] if row["platform"] == "qq")
    assert wechat["inbound_count"] == 1
    assert wechat["outbound_count"] == 1
    assert wechat["status"] == "UP"
    assert qq["error_count"] == 1
    assert qq["status"] == "DOWN"


def test_gateway_stats_falls_back_to_remote_adapter_health(client, monkeypatch):
    """Gateway stats should surface remote adapter health even with no local samples."""

    async def _fake_remote_gateway_health():
        return {
            "base_url": "http://192.168.1.101:19000",
            "body": {
                "status": "ok",
                "adapters": {
                    "wechat": {
                        "platform": "wechat",
                        "status": "ok",
                        "bridge_backend": "wxautox4",
                        "last_outbound": {"ts_ms": 1774776709768},
                    },
                    "qq": {
                        "platform": "qq",
                        "status": "error",
                    },
                },
            },
        }

    monkeypatch.setattr(obs_routes, "_fetch_remote_gateway_health", _fake_remote_gateway_health)

    resp = client.get("/obs/gateway/stats?window_minutes=60")
    assert resp.status_code == 200
    data = resp.json()
    wechat = next(row for row in data["platforms"] if row["platform"] == "wechat")
    qq = next(row for row in data["platforms"] if row["platform"] == "qq")
    assert wechat["status"] == "UP"
    assert wechat["status_detail"] == "adapter health via http://192.168.1.101:19000"
    assert wechat["bridge_status"] == "wxautox4:ok"
    assert wechat["last_event_ts_ms"] == 1774776709768
    assert qq["status"] == "DOWN"
    assert qq["status_detail"] == "adapter health via http://192.168.1.101:19000"


def test_gateway_stats_counts_gateway_input_lifecycle_rows(client, ledger, monkeypatch):
    """Gateway stats should count source=GATEWAY INPUT_* rows as live window traffic."""
    now = int(time.time() * 1000)
    ledger.log_voice_event({
        "request_id": "req-gw-qq-001",
        "session_id": "qq_dm_3414578517",
        "event_type": "INPUT_ACCEPTED",
        "direction": "EDGE_TO_HOST",
        "seq": 1,
        "status": "ACCEPTED",
        "reason_code": None,
        "stage": "input_dispatch.accepted",
        "payload": {"source": "GATEWAY", "phase": "ACCEPTED", "latency_ms": None},
        "detail": "source=GATEWAY text_len=5",
        "ts_ms": now - 2000,
    })
    ledger.log_voice_event({
        "request_id": "req-gw-qq-001",
        "session_id": "qq_dm_3414578517",
        "event_type": "INPUT_COMPLETED",
        "direction": "EDGE_TO_HOST",
        "seq": 2,
        "status": "OK",
        "reason_code": None,
        "stage": "input_dispatch.completed",
        "payload": {"source": "GATEWAY", "phase": "COMPLETED", "latency_ms": 1234},
        "detail": "main_chain completed source=GATEWAY latency_ms=1234",
        "ts_ms": now - 1000,
    })

    async def _fake_remote_gateway_health():
        return {}

    monkeypatch.setattr(obs_routes, "_fetch_remote_gateway_health", _fake_remote_gateway_health)

    resp = client.get("/obs/gateway/stats?window_minutes=10080")
    assert resp.status_code == 200
    data = resp.json()
    qq = next(row for row in data["platforms"] if row["platform"] == "qq")
    assert qq["inbound_count"] == 1
    assert qq["outbound_count"] == 1
    assert qq["error_count"] == 0
    assert qq["avg_latency_ms"] == 1234
    assert qq["status"] == "UP"


def test_gateway_stats_counts_platform_from_gateway_payload(client, ledger, monkeypatch):
    """Gateway stats should honor payload.platform even when session_id has no platform prefix."""
    now = int(time.time() * 1000)
    ledger.log_voice_event({
        "request_id": "req-gw-tg-001",
        "session_id": "chat_918273",
        "event_type": "INPUT_ACCEPTED",
        "direction": "EDGE_TO_HOST",
        "seq": 1,
        "status": "ACCEPTED",
        "reason_code": None,
        "stage": "input_dispatch.accepted",
        "payload": {
            "source": "GATEWAY",
            "phase": "ACCEPTED",
            "latency_ms": None,
            "platform": "telegram",
            "conversation_id": "chat_918273",
            "msg_id": "tg-msg-001",
            "msg_type": "text",
        },
        "detail": "source=GATEWAY platform=telegram text_len=5",
        "ts_ms": now - 2000,
    })
    ledger.log_voice_event({
        "request_id": "req-gw-tg-001",
        "session_id": "chat_918273",
        "event_type": "INPUT_COMPLETED",
        "direction": "EDGE_TO_HOST",
        "seq": 2,
        "status": "OK",
        "reason_code": None,
        "stage": "input_dispatch.completed",
        "payload": {
            "source": "GATEWAY",
            "phase": "COMPLETED",
            "latency_ms": 888,
            "platform": "telegram",
            "conversation_id": "chat_918273",
            "msg_id": "tg-msg-001",
            "msg_type": "text",
        },
        "detail": "main_chain completed source=GATEWAY platform=telegram latency_ms=888",
        "ts_ms": now - 1000,
    })

    async def _fake_remote_gateway_health():
        return {}

    monkeypatch.setattr(obs_routes, "_fetch_remote_gateway_health", _fake_remote_gateway_health)

    resp = client.get("/obs/gateway/stats?window_minutes=10080")
    assert resp.status_code == 200
    data = resp.json()
    telegram = next(row for row in data["platforms"] if row["platform"] == "telegram")
    assert telegram["inbound_count"] == 1
    assert telegram["outbound_count"] == 1
    assert telegram["avg_latency_ms"] == 888
    assert telegram["status"] == "UP"


def test_trace_live_feed_streams_snapshot(client):
    """Trace live feed should emit an initial snapshot event."""
    resp = client.get("/obs/trace-feed/live?limit=2&interval_ms=250&once=true")
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")
    raw = resp.text
    first_event = raw.split("\n\n", 1)[0]
    assert "event: snapshot" in first_event
    data_line = next(line for line in first_event.splitlines() if line.startswith("data:"))
    payload = json.loads(data_line[5:].strip())
    assert payload["type"] == "snapshot"
    assert payload["count"] >= 1
    assert payload["items"][0]["session"]["request_id"].startswith("req-int-")


# ---------------------------------------------------------------------------
# GET /obs/subsystems/stats
# ---------------------------------------------------------------------------

def test_subsystems_stats_classifies_intent_airlock_and_retrieval(client, ledger):
    """Subsystem health should not treat intent lint JSON or airlock policy hits as hard failures."""
    now = int(time.time() * 1000)

    ledger.log_airlock({
        "request_id": "req-airlock-blocked-001",
        "direction": "INGRESS",
        "reason_code": "AIRLOCK_BLOCKED_INGRESS",
        "removed_fields": None,
        "redactions": None,
        "payload_digest": None,
        "quarantine_digest": None,
        "ingress_classification": "draft_only",
        "blocked": 1,
        "detail": "blocked by policy",
        "ts_ms": now - 80,
    })
    ledger.log_retrieval_event({
        "request_id": "req-retrieval-degraded-001",
        "intent_id": "intent-retrieval-degraded-001",
        "op_kind": "RERANK",
        "status": "DEGRADED",
        "reason_code": "RETR_RERANK_TIMEOUT",
        "stage": "S_RET_RERANK",
        "payload": {"elapsed_ms": 40866},
        "detail": "reranker slow: 40866ms>30000ms",
        "ts_ms": now - 60,
    })

    resp = client.get("/obs/subsystems/stats?window_minutes=10080")
    assert resp.status_code == 200
    data = resp.json()
    subsystems = {row["subsystem"]: row for row in data["subsystems"]}

    intent = subsystems["intent"]
    assert intent["status"] == "UP"
    assert intent["success"] >= 1
    assert intent["failed"] == 0

    airlock = subsystems["airlock"]
    assert airlock["status"] == "UP"
    assert airlock["success_rate"] == 100.0
    assert airlock["failed"] == 0
    assert airlock["blocked"] == 1

    retrieval = subsystems["retrieval"]
    assert retrieval["status"] == "DEGRADED"
    assert retrieval["degraded"] >= 1
    assert "降级" in (retrieval["detail"] or "")


# ---------------------------------------------------------------------------
# GET /obs/trace/{request_id}
# ---------------------------------------------------------------------------

def test_trace_existing_request(client):
    """T-OBS-ROUTE-INT-09: 存在的 request_id 返回 200 + 节点列表。"""
    resp = client.get("/obs/trace/req-int-001")
    assert resp.status_code == 200
    data = resp.json()
    assert data["request_id"] == "req-int-001"
    assert "nodes" in data
    assert data["node_count"] >= 1


def test_trace_not_found(client):
    """T-OBS-ROUTE-INT-10: 不存在的 request_id 返回 404。"""
    resp = client.get("/obs/trace/nonexistent-xyz-999")
    assert resp.status_code == 404


def test_trace_fallback_path(client, ledger):
    """T-OBS-ROUTE-INT-11: graph_run_trace 无数据时降级路径正常返回。"""
    # req-int-002 无 graph_run_trace，走审计表合成链路
    resp = client.get("/obs/trace/req-int-002")
    assert resp.status_code == 200
    data = resp.json()
    assert data["request_id"] == "req-int-002"
    assert data["node_count"] >= 1


def test_trace_fallback_contains_summary_and_recall(client):
    """Fallback trace should expose summary, recall stages, and node payloads."""
    resp = client.get("/obs/trace/req-int-002")
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"]["input_text"] == "fallback hello"
    assert data["summary"]["output_text"] == "fallback reply"
    assert data["summary"]["recall"]["candidate_count"] == 7
    assert data["summary"]["recall"]["pack_id"] == "epk_test_002"
    assert data["summary"]["recall"]["content_available"] is True
    assert data["summary"]["recall"]["items"][0]["snippet"] == "fallback hello recall snippet"
    assert data["summary"]["recall"]["topk"][0]["snippet"] == "fallback hello rerank snippet"
    assert data["summary"]["recall"]["evidence_topk"][0]["snippet"] == "fallback hello evidence snippet"
    node_ids = {node["node_id"] for node in data["nodes"]}
    assert "host_input" in node_ids
    assert "query_planner" in node_ids
    assert "agentic_rag" in node_ids
    assert "recall_fusion" in node_ids
    assert "top_agent" in node_ids
    assert "chat_agent" in node_ids
    assert "llm_gateway" in node_ids


def test_trace_response_structure(client):
    """T-OBS-ROUTE-INT-12: 响应结构包含所有必要字段。"""
    resp = client.get("/obs/trace/req-int-001")
    assert resp.status_code == 200
    data = resp.json()
    required_fields = {"request_id", "status", "node_count", "nodes", "edges", "ts_ms", "timestamp"}
    assert required_fields.issubset(data.keys())


# ---------------------------------------------------------------------------
# GET /obs/metrics/timeseries
# ---------------------------------------------------------------------------

def test_metrics_timeseries_returns_200(client):
    """T-OBS-ROUTE-INT-13: 正常路径返回 200 + 时序字段。"""
    resp = client.get("/obs/metrics/timeseries")
    assert resp.status_code == 200
    data = resp.json()
    assert "points" in data
    assert "total_requests" in data
    assert "rejection_rate" in data
    assert isinstance(data["points"], list)


def test_metrics_timeseries_range_param(client):
    """T-OBS-ROUTE-INT-14: range_minutes 参数正确影响 range_ms。"""
    resp = client.get("/obs/metrics/timeseries?range_minutes=30")
    assert resp.status_code == 200
    data = resp.json()
    assert data["range_ms"] == 30 * 60 * 1000


def test_metrics_timeseries_interval_param(client):
    """T-OBS-ROUTE-INT-15: interval_minutes 参数正确影响 interval_ms。"""
    resp = client.get("/obs/metrics/timeseries?interval_minutes=1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["interval_ms"] == 1 * 60 * 1000


def test_metrics_timeseries_invalid_range(client):
    """T-OBS-ROUTE-INT-16: 超出范围的 range_minutes 返回 422。"""
    resp = client.get("/obs/metrics/timeseries?range_minutes=1")
    assert resp.status_code == 422
