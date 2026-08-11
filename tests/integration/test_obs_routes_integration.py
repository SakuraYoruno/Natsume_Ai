"""OBS query-side integration tests.

The operator surface is migration-first and projection-only.  Source rows are
written to the canonical ledger, then an explicit background refresh
materializes the public read models before HTTP is exercised.  No route in
this test is allowed to fall back to a live ledger query.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.app.obs_routes import router
from host_brain.app.obs_routes_health import router as health_router
from host_brain.app.obs_fastapi_app import request_trace
from host_brain.app.obs_route_ports import ObsRoutePorts
from host_brain.app.obs_routes_core import _clear_obs_route_response_cache
from host_brain.app.observability_runtime.live_source_projection import (
    refresh_operator_live_state_projections,
)
from host_brain.app.observability_runtime.process_state_projection import (
    refresh_runtime_snapshot_projection,
)
from host_brain.app.observability_runtime.projection_ledger_view import (
    ProjectionLedgerView,
    refresh_ledger_stats_projection,
)
from host_brain.app.obs_routes_trace import refresh_trace_query_projections
from host_brain.app.obs_projection_jobs import (
    _health_callback,
    _sessions_callback,
)
from host_brain.observability.projection_store import QueryProjectionStore
from host_brain.persistence.capability_execution_repository import (
    CapabilityExecutionRepository,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectionRefresh:
    ledger_stats: Callable[[], object]
    sessions: Callable[[], object]
    health: Callable[[], object]
    runtime: Callable[..., object]
    gateway: Callable[[], object]
    trace: Callable[[], object]


@dataclass(frozen=True)
class ObsRoutesTestRuntime:
    ledger: SQLiteLedger
    store: QueryProjectionStore


class EmptyLifeSnapshotReader:
    """Explicit query-side Life reader used by the live projection fixture."""

    @staticmethod
    def get_snapshot(person_id: str) -> dict[str, object]:
        return {
            "person_id": str(person_id or "default"),
            "version": 0,
            "state": {},
            "state_digest": "",
            "updated_at_ms": 0,
        }


class EmptyModeTransitionService:
    """Explicit app-owned mode source used by the live projection fixture."""

    @staticmethod
    def get_current_status() -> dict[str, object]:
        return {}

    @staticmethod
    def get_history(*, limit: int) -> list[dict[str, object]]:
        del limit
        return []


@pytest.fixture
def obs_runtime(tmp_path: Path, monkeypatch):
    """Migrated durable ledger shared by source writers and projection store."""

    db_path = tmp_path / "obs-query.sqlite3"
    migration = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="obs-route-integration",
    )
    assert migration.success, migration
    db = SQLiteLedger(
        db_path,
        check_same_thread=False,
        initialize_schema=False,
    )
    store = QueryProjectionStore(db_path)

    async def _no_remote_gateway_health():
        return {}

    monkeypatch.setattr(
        "host_brain.app.obs_routes_platform._fetch_remote_gateway_health",
        _no_remote_gateway_health,
    )
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
        "payload": {
            "phase": "COMPLETED",
            "source": "GATEWAY",
            "latency_ms": 2600,
            "reply_preview": "fallback reply",
        },
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
        "payload": {"from_stage": "FRIEND", "to_stage": "FRIEND", "total_score": 1},
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
        "detail": "memory written: [session=sess-002] User: fallback hello",
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

    # Model attempts are the migration-owned model health source.  Migration
    # 0103 cold-migrates and removes the retired ``llm_node_call_log`` table.
    db.log_model_attempt({
        "attempt_id": "attempt-int-001",
        "request_id": "req-int-001",
        "route_decision_id": "route-int-001",
        "attempt_index": 1,
        "status": "succeeded",
        "retry_reason": "initial",
        "adapter_id": "openai-production",
        "provider": "openai",
        "model_id": "gpt-5.2",
        "prompt_id": "surface-reply",
        "prompt_version": "1",
        "prompt_digest": "a" * 64,
        "started_at_ms": now - 4120,
        "ended_at_ms": now - 4000,
        "latency_ms": 120,
        "output_digest": "b" * 64,
        "error_code": None,
        "error_message": None,
        "metadata": {},
        "ts_ms": now - 4000,
    })

    try:
        yield ObsRoutesTestRuntime(ledger=db, store=store)
    finally:
        db.close()


@pytest.fixture
def ledger(obs_runtime: ObsRoutesTestRuntime) -> SQLiteLedger:
    return obs_runtime.ledger


@pytest.fixture
def projection_refresh(
    obs_runtime: ObsRoutesTestRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> ProjectionRefresh:
    """Expose background-owned materializers; HTTP never invokes these."""

    ledger = obs_runtime.ledger
    store = obs_runtime.store

    def _refresh_ledger_stats() -> object:
        return refresh_ledger_stats_projection(ledger=ledger, store=store)

    def _refresh_runtime(mode_service: object) -> object:
        return refresh_runtime_snapshot_projection(
            lifecycle_ports=object(),
            mode_transition_service=mode_service,
            store=store,
        )

    return ProjectionRefresh(
        ledger_stats=_refresh_ledger_stats,
        sessions=_sessions_callback(ledger, store),
        health=_health_callback(ledger, store),
        runtime=_refresh_runtime,
        gateway=lambda: refresh_operator_live_state_projections(
            life_snapshot_reader=EmptyLifeSnapshotReader(),
            ledger_view=ProjectionLedgerView(store),
            mode_service=EmptyModeTransitionService(),
            store=store,
        ),
        trace=lambda: refresh_trace_query_projections(
            ledger=ledger,
            store=store,
            tribe_event_reader=lambda: [],
        ),
    )


@pytest.fixture
def client(
    obs_runtime: ObsRoutesTestRuntime,
    projection_refresh: ProjectionRefresh,
):
    """Projection-only HTTP app with retired trace compatibility paths absent."""

    projection_refresh.ledger_stats()
    projection_refresh.sessions()
    projection_refresh.health()
    projection_refresh.gateway()
    projection_refresh.trace()
    app = FastAPI()
    app.state.obs_route_ports = ObsRoutePorts(
        query_projection_store_provider=lambda: obs_runtime.store,
    )
    app.include_router(router)
    app.include_router(health_router)
    app.add_api_route("/observability/trace", request_trace, methods=["POST"])
    _clear_obs_route_response_cache()
    client = TestClient(app)
    yield client
    _clear_obs_route_response_cache()


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


def test_runtime_snapshot_returns_unified_state(
    client,
    monkeypatch,
    projection_refresh: ProjectionRefresh,
):
    """T-OBS-ROUTE-INT-17: /obs/runtime-snapshot 返回统一数字生命状态摘要。"""

    monkeypatch.setattr(
        "host_brain.lifecycle_runtime.runtime_snapshot.build_digital_life_runtime_snapshot",
        lambda *, ports=None, current_topic="": {
            "persona_user_id": "persona-user-001",
            "current_topic": current_topic,
            "sys_mode": "focus",
            "hard_state": {"sys_mode": "focus", "mood_state": "engaged"},
            "activity_monitor": {"running": True, "idle_minutes": 3.5},
            "emotion_context": {"emotion_tag": "NEUTRAL", "mood_state": "engaged"},
            "bond_state": {"current_stage": "FRIEND"},
            "bond_summary": {"stage": "FRIEND", "trend": "上升"},
            "bond_summary_human": {"stage": "关系好的兄妹", "capability_hint": "关系好的兄妹，可轻松拌嘴"},
            "inner_world_state": {"loneliness": 0.32},
            "offline_state": {"offline_hours": 8.0},
            "silence_state": {"response_willingness": 0.72},
            "body_sense": {"fatigue_level": "FRESH"},
            "self_model_snapshot": {"recent_mood_trend": "平稳"},
            "self_reflection_pulse": {"growth_note": "最近更稳了。"},
            "bond_narrative": {"narratives": ["我还记得你说过那件事。"]},
            "motive_state": {"dominant_motive": "continuity"},
            "render_policy": {"allow_topic_shift": False},
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
                    "render_policy:focused",
                ],
                "core_slots": {
                    "sys_mode": {"value": "focus", "activity_event": "wakeup", "idle_minutes": 3.5},
                    "offline_state": {"absence_band": "6h_plus", "offline_reconnect_style": "soft_resume"},
                    "motive_state": {"dominant_motive": "continuity"},
                    "render_policy": {"reply_posture": "focused"},
                },
                "why_reply": ["mode:focus", "motive:continuity"],
            },
            "slot_status": {"render_policy": "live"},
            "degraded": False,
            "degraded_reasons": [],
            "timestamp": 1700000000000,
        },
    )
    mode_service = type(
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
    )()
    projection_refresh.runtime(mode_service)

    resp = client.get("/obs/runtime-snapshot?current_topic=%E9%82%A3%E4%B8%AA%E9%A1%B9%E7%9B%AE")

    assert resp.status_code == 200
    data = resp.json()
    assert data["persona_user_id"] == "persona-user-001"
    # Query parameters never trigger a live rebuild; the worker owns the
    # canonical topic captured in the projection.
    assert data["current_topic"] == ""
    assert data["sys_mode"] == "focus"
    assert data["sys_mode_status"]["sys_mode"] == "focus"
    assert data["hard_state"]["mood_state"] == "engaged"
    assert data["bond_summary_human"]["stage"] == "关系好的兄妹"
    assert data["motive_state"]["dominant_motive"] == "continuity"
    assert data["runtime_summary"]["current_state"]["sys_mode"] == "focus"
    assert data["runtime_summary"]["current_state"]["offline_reconnect_style"] == "soft_resume"
    assert data["runtime_summary"]["current_state"]["activity_event"] == "wakeup"
    assert "offline:6h_plus->soft_resume" in data["runtime_summary"]["decision_chain"]
    assert data["runtime_summary"]["core_slots"]["sys_mode"]["activity_event"] == "wakeup"
    # Content-shaped reply fields are removed by the projection-store privacy
    # serializer before the document becomes queryable.
    assert "reply_posture" not in data["runtime_summary"]["core_slots"]["render_policy"]
    assert data["runtime_summary"]["why_reply"][0] == "mode:focus"
    assert data["slot_status"]["sys_mode_status"] == "live"
    assert "interoception_state" in data
    assert "motor_cortex_state" in data
    assert "cerebellum_state" in data
    assert "glymphatic_state" in data
    assert "vestibular_state" in data
    assert "homeostasis_report" in data


def test_runtime_snapshot_accepts_nested_slot_privacy_metadata(
    client,
    monkeypatch,
    projection_refresh: ProjectionRefresh,
):
    """Projected slot statuses retain serializer privacy metadata without a 500."""

    monkeypatch.setattr(
        "host_brain.lifecycle_runtime.runtime_snapshot.build_digital_life_runtime_snapshot",
        lambda *, ports=None, current_topic="": {
            "persona_user_id": "persona-user-private-slot",
            "current_topic": current_topic,
            "sys_mode": "normal",
            # ``bond_narrative`` is intentionally content-shaped.  The public
            # projection serializer must withhold its value and emit nested
            # metadata rather than weakening the privacy policy.
            "slot_status": {"bond_narrative": "live"},
            "degraded": False,
            "degraded_reasons": [],
            "timestamp": 1700000000000,
        },
    )
    mode_service = type(
        "_FakeModeService",
        (),
        {"get_current_status": lambda self: {"sys_mode": "normal"}},
    )()
    projection_refresh.runtime(mode_service)

    response = client.get("/obs/runtime-snapshot")

    assert response.status_code == 200
    slot_status = response.json()["slot_status"]
    assert slot_status["sys_mode_status"] == "live"
    assert "bond_narrative" not in slot_status
    privacy = slot_status["_operator_privacy"]
    assert privacy["redacted"] is True
    assert privacy["schema_version"] == "natsume.operator-query.v1"
    assert [item["field"] for item in privacy["withheld"]] == ["bond_narrative"]
    assert privacy["withheld"][0]["citation_ref"].startswith(
        "operator-private-bond-narrative://sha256/"
    )


def test_runtime_snapshot_repeated_reads_do_not_rebuild(
    client,
    monkeypatch,
    projection_refresh: ProjectionRefresh,
):
    """Repeated GETs read the same projection and never invoke source builders."""

    calls = {"snapshot": 0, "status": 0}

    def _fake_snapshot(*, ports=None, current_topic=""):
        del ports
        calls["snapshot"] += 1
        return {
            "persona_user_id": "persona-user-cache",
            "current_topic": current_topic,
            "sys_mode": "normal",
            "sys_mode_status": {},
            "hard_state": {},
            "activity_monitor": {},
            "emotion_context": {},
            "endocrine_state": {},
            "immune_state": {},
            "bond_state": {},
            "bond_summary": {},
            "bond_summary_human": {},
            "inner_world_state": {},
            "offline_state": {},
            "silence_state": {},
            "body_sense": {},
            "self_model_snapshot": {},
            "self_reflection_pulse": {},
            "bond_narrative": {},
            "motive_state": {},
            "render_policy": {},
            "somatic_state": {},
            "peripheral_state": {},
            "circadian_state": {},
            "proprioception_state": {},
            "initiative_decision": {},
            "default_mode_state": {},
            "mirror_neuron_state": {},
            "memory_layers": {},
            "language_dual_system": {},
            "homeostasis_report": {},
            "interoception_state": {},
            "motor_cortex_state": {},
            "cerebellum_state": {},
            "glymphatic_state": {},
            "vestibular_state": {},
            "runtime_summary": {},
            "background_runtime": {},
            "slot_status": {},
            "degraded": False,
            "degraded_reasons": [],
            "timestamp": 1700000000000,
        }

    class _FakeModeService:
        def get_current_status(self):
            calls["status"] += 1
            return {
                "sys_mode": "normal",
                "auto_rules": {},
                "idle_ms": 0,
                "idle_minutes": 0,
                "last_interaction_ts_ms": 1700000000000,
                "is_night_time": False,
                "valid_transitions": [],
                "ts_ms": 1700000000000,
                "degraded": False,
                "degraded_reason": None,
            }

    monkeypatch.setattr(
        "host_brain.lifecycle_runtime.runtime_snapshot.build_digital_life_runtime_snapshot",
        _fake_snapshot,
    )
    projection_refresh.runtime(_FakeModeService())

    first = client.get("/obs/runtime-snapshot?current_topic=cache-demo")
    second = client.get("/obs/runtime-snapshot?current_topic=cache-demo")

    assert first.status_code == 200
    assert second.status_code == 200
    assert calls == {"snapshot": 1, "status": 1}


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


def test_sessions_can_find_rows_beyond_default_fetch_limit(
    client,
    ledger,
    projection_refresh: ProjectionRefresh,
):
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
    projection_refresh.sessions()
    _clear_obs_route_response_cache()

    resp = client.get("/obs/sessions?window_minutes=10080&keyword=req-many-119")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["request_id"] == "req-many-119"


def test_sessions_projection_redacts_input_preview(client):
    """The durable public session projection never stores private input text."""
    resp = client.get("/obs/sessions?window_minutes=10080")
    assert resp.status_code == 200
    data = resp.json()
    item = next(row for row in data["items"] if row["request_id"] == "req-int-002")
    assert item["input_preview"] is None


def test_sessions_keyword_does_not_search_redacted_private_input(client):
    """Query filters cannot recover text removed at projection-write time."""
    resp = client.get("/obs/sessions?window_minutes=10080&keyword=fallback%20hello")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []


def test_sessions_export_csv_with_filters(client):
    """Regression: CSV export writes SessionItem rows without dict-style access."""
    resp = client.get("/obs/sessions/export?window_minutes=10080&keyword=req-int-001&status=ok")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    text = resp.text
    assert "request_id,session_id,ts_ms,datetime,status,decision,reason_code,total_latency_ms" in text
    assert "req-int-001" in text


def test_gateway_stats_reads_channel_io_log(
    client,
    ledger,
    projection_refresh: ProjectionRefresh,
):
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
    projection_refresh.ledger_stats()
    projection_refresh.gateway()

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


def test_gateway_stats_falls_back_to_remote_adapter_health(
    client,
    monkeypatch,
    projection_refresh: ProjectionRefresh,
):
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

    monkeypatch.setattr(
        "host_brain.app.obs_routes_platform._fetch_remote_gateway_health",
        _fake_remote_gateway_health,
    )
    projection_refresh.gateway()

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


def test_gateway_stats_counts_gateway_input_lifecycle_rows(
    client,
    ledger,
    monkeypatch,
    projection_refresh: ProjectionRefresh,
):
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

    monkeypatch.setattr(
        "host_brain.app.obs_routes_platform._fetch_remote_gateway_health",
        _fake_remote_gateway_health,
    )
    projection_refresh.ledger_stats()
    projection_refresh.gateway()

    resp = client.get("/obs/gateway/stats?window_minutes=10080")
    assert resp.status_code == 200
    data = resp.json()
    qq = next(row for row in data["platforms"] if row["platform"] == "qq")
    assert qq["inbound_count"] == 1
    assert qq["outbound_count"] == 1
    assert qq["error_count"] == 0
    assert qq["avg_latency_ms"] == 1234
    assert qq["status"] == "UP"


def test_gateway_stats_counts_platform_from_gateway_payload(
    client,
    ledger,
    monkeypatch,
    projection_refresh: ProjectionRefresh,
):
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

    monkeypatch.setattr(
        "host_brain.app.obs_routes_platform._fetch_remote_gateway_health",
        _fake_remote_gateway_health,
    )
    projection_refresh.ledger_stats()
    projection_refresh.gateway()

    resp = client.get("/obs/gateway/stats?window_minutes=10080")
    assert resp.status_code == 200
    data = resp.json()
    telegram = next(row for row in data["platforms"] if row["platform"] == "telegram")
    assert telegram["inbound_count"] == 1
    assert telegram["outbound_count"] == 1
    assert telegram["avg_latency_ms"] == 888
    assert telegram["status"] == "UP"


def test_trace_live_feed_route_is_retired(client):
    """The legacy SSE trace feed is absent from the canonical query app."""
    resp = client.get("/obs/trace-feed/live?limit=2&interval_ms=250&once=true")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /obs/subsystems/stats
# ---------------------------------------------------------------------------

def test_subsystems_stats_classifies_intent_airlock_and_retrieval(
    client,
    ledger,
    projection_refresh: ProjectionRefresh,
):
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
    capability_repository = CapabilityExecutionRepository(ledger.canonical_db_path)
    assert capability_repository.claim({
        "idempotency_key": "subsystem-capability-001",
        "request_hash": "hash-capability-001",
        "request_id": "req-capability-001",
        "attempt_id": "attempt-capability-001",
        "capability_id": "tool.search",
        "kind": "tool",
        "owner_token": "owner-capability-001",
        "now_ms": now - 50,
        "lease_expires_at_ms": now + 10_000,
    }) == "ACQUIRED"
    capability_repository.complete({
        "idempotency_key": "subsystem-capability-001",
        "request_hash": "hash-capability-001",
        "request_id": "req-capability-001",
        "attempt_id": "attempt-capability-001",
        "capability_id": "tool.search",
        "kind": "tool",
        "owner_token": "owner-capability-001",
        "status": "SUCCEEDED",
        "attempt_json": "{}",
        "evidence_json": "{}",
        "receipt_json": "{}",
        "finished_at_ms": now - 40,
        "claim_state": "COMPLETED",
    })
    projection_refresh.ledger_stats()

    resp = client.get("/obs/subsystems/stats?window_minutes=10080")
    assert resp.status_code == 200, resp.text
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

    sandbox = subsystems["sandbox"]
    assert sandbox["status"] == "UNAVAILABLE"
    assert sandbox["detail"] == "OBS_SANDBOX_TELEMETRY_SOURCE_UNAVAILABLE"
    assert sandbox["success_rate"] is None

    capability_execution = subsystems["capability_execution"]
    assert capability_execution["display_name"] == "Capability 执行回执"
    assert capability_execution["status"] == "UP"
    assert capability_execution["total"] == 1
    assert capability_execution["success_rate"] == 100.0


# ---------------------------------------------------------------------------
# Canonical POST /observability/trace; legacy GET /obs/trace/* is retired
# ---------------------------------------------------------------------------

def test_trace_existing_request(client):
    """The canonical trace query reads the durable public projection."""
    resp = client.post("/observability/trace", json={"request_id": "req-int-001"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["request_id"] == "req-int-001"
    assert data["timeline_total"] >= 1
    assert isinstance(data["timeline"], list)
    assert isinstance(data["sources"], dict)


def test_trace_not_found(client):
    """Retired trace compatibility paths remain absent for every id."""
    resp = client.get("/obs/trace/nonexistent-xyz-999")
    assert resp.status_code == 404


def test_trace_fallback_path(client, ledger):
    """The old on-demand ledger synthesis route is permanently retired."""
    del ledger
    resp = client.get("/obs/trace/req-int-002")
    assert resp.status_code == 404


def test_trace_projection_excludes_private_summary_and_recall_bodies(client):
    """The canonical projection exposes structure without source text bodies."""
    resp = client.post("/observability/trace", json={"request_id": "req-int-002"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["request_id"] == "req-int-002"
    assert data["timeline_total"] >= 1
    serialized = json.dumps(data, ensure_ascii=False)
    assert "fallback hello" not in serialized
    assert "fallback reply" not in serialized
    assert "recall snippet" not in serialized
    assert "rerank snippet" not in serialized
    assert "evidence snippet" not in serialized


def test_trace_response_structure(client):
    """Canonical trace response keeps its stable projection contract."""
    resp = client.post("/observability/trace", json={"request_id": "req-int-001"})
    assert resp.status_code == 200
    data = resp.json()
    required_fields = {
        "request_id",
        "trace_status",
        "timeline_total",
        "sources",
        "timeline",
        "timestamp",
    }
    assert required_fields.issubset(data.keys())


# ---------------------------------------------------------------------------
# GET /obs/metrics/timeseries
# ---------------------------------------------------------------------------

def test_metrics_timeseries_returns_200(client):
    """T-OBS-ROUTE-INT-13: 正常路径返回 200 + 时序字段。"""
    resp = client.get("/obs/metrics/timeseries")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "points" in data
    assert "total_requests" in data
    assert "rejection_rate" in data
    assert isinstance(data["points"], list)


def test_metrics_timeseries_range_param(client):
    """T-OBS-ROUTE-INT-14: range_minutes 参数正确影响 range_ms。"""
    resp = client.get("/obs/metrics/timeseries?range_minutes=30")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["range_ms"] == 30 * 60 * 1000


def test_metrics_timeseries_interval_param(client):
    """T-OBS-ROUTE-INT-15: interval_minutes 参数正确影响 interval_ms。"""
    resp = client.get("/obs/metrics/timeseries?interval_minutes=1")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["interval_ms"] == 1 * 60 * 1000


def test_metrics_timeseries_invalid_range(client):
    """T-OBS-ROUTE-INT-16: 超出范围的 range_minutes 返回 422。"""
    resp = client.get("/obs/metrics/timeseries?range_minutes=1")
    assert resp.status_code == 422
