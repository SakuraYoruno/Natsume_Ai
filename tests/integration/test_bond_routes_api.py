"""T-BOND-API-01~08: Bond 阶段 REST API 集成测试（FastAPI TestClient）。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from host_brain.bond.bond_stage_engine import (
    STAGE_CLOSE_FRIEND,
    STAGE_FRIEND,
    BondStageEngine,
)
from host_brain.app.bond_routes import router, set_bond_engine_provider
from host_brain.bond.user_identity import get_canonical_bond_user_id


# ── 夹具 ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    engine = BondStageEngine()
    app = FastAPI()
    app.include_router(router)
    set_bond_engine_provider(lambda: engine)
    return TestClient(app)


# ── T-BOND-API-01: GET /bond/health ──────────────────────────────────────────

class TestBondHealth:
    """T-BOND-API-01: GET /bond/health 返回 OK。"""

    def test_health_ok(self, client):
        resp = client.get("/bond/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "OK"
        assert "stage_order" in data
        assert len(data["stage_order"]) == 4
        assert "request_id" in data

    def test_health_user_count_starts_zero(self, client):
        resp = client.get("/bond/health")
        data = resp.json()
        assert data["user_count"] == 0


# ── T-BOND-API-02: GET /bond/stage/{user_id} ─────────────────────────────────

class TestGetBondStage:
    """T-BOND-API-02: GET /bond/stage/{user_id} 返回关系好的兄妹基线。"""

    def test_new_user_is_friend(self, client):
        resp = client.get("/bond/stage/alice")
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == get_canonical_bond_user_id()
        assert data["current_stage"] == STAGE_FRIEND
        assert data["total_score"] == 0
        assert data["stage_index"] == 0

    def test_stage_response_has_all_fields(self, client):
        resp = client.get("/bond/stage/bob")
        data = resp.json()
        for field in ("user_id", "current_stage", "stage_index", "total_score",
                      "event_count", "trend", "capability_hint", "request_id", "timestamp"):
            assert field in data, f"missing field: {field}"


# ── T-BOND-API-03: GET /bond/summary/{user_id} ───────────────────────────────

class TestGetBondSummary:
    """T-BOND-API-03: GET /bond/summary 返回不含数值的 Summary（FR-BOND-003）。"""

    def test_summary_has_no_numeric_values(self, client):
        resp = client.get("/bond/summary/carol")
        assert resp.status_code == 200
        data = resp.json()
        for key in ("stage", "trend", "capability_hint"):
            assert isinstance(data[key], str), f"summary[{key}] should be string"

    def test_summary_stage_is_valid(self, client):
        resp = client.get("/bond/summary/dave")
        data = resp.json()
        # M28-H：to_summary() 返回中文阶段名
        _VALID_STAGES_CN = {"关系好的兄妹", "亲近兄妹", "亲密恋人", "灵魂伴侣"}
        assert data["stage"] in _VALID_STAGES_CN

    def test_summary_trend_is_valid(self, client):
        resp = client.get("/bond/summary/eve")
        data = resp.json()
        # M28-F：trend() 返回中文趋势描述
        _VALID_TRENDS_CN = {"上升", "稳定", "下降", "快速上升", "缓慢上升", "快速下降", "缓慢下降"}
        assert data["trend"] in _VALID_TRENDS_CN


# ── T-BOND-API-04: POST /bond/event 成功 ─────────────────────────────────────

class TestSubmitBondEvent:
    """T-BOND-API-04: POST /bond/event 成功提交事件并返回 STABLE。"""

    def test_submit_interaction_event(self, client):
        resp = client.post("/bond/event", json={
            "user_id": "user_001",
            "update_kind": "INTERACTION",
            "evidence_refs": ["evt::001"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == get_canonical_bond_user_id()
        assert data["transition_status"] in ("STABLE", "PROMOTED", "DEMOTED")
        assert data["score_delta"] > 0

    def test_submit_event_response_fields(self, client):
        resp = client.post("/bond/event", json={
            "user_id": "user_002",
            "update_kind": "DEEP_TALK",
            "evidence_refs": ["evt::002"],
        })
        data = resp.json()
        for field in ("user_id", "from_stage", "to_stage", "transition_status",
                      "score_delta", "total_score", "transition_id", "request_id", "timestamp"):
            assert field in data, f"missing field: {field}"

    def test_submit_event_updates_stage_state(self, client):
        # 提交足够多的 DEEP_TALK 事件触发晋级
        for i in range(14):
            client.post("/bond/event", json={
                "user_id": "user_003",
                "update_kind": "DEEP_TALK",
                "evidence_refs": [f"evt::{i}"],
            })
        resp = client.get("/bond/stage/user_003")
        data = resp.json()
        assert data["current_stage"] == STAGE_CLOSE_FRIEND
        assert data["total_score"] == 70


# ── T-BOND-API-05: POST /bond/event 无 evidence_refs → 422 ───────────────────

class TestBondEventValidation:
    """T-BOND-API-05: POST /bond/event 无 evidence_refs → 422（FR-BOND-002）。"""

    def test_empty_evidence_refs_422(self, client):
        resp = client.post("/bond/event", json={
            "user_id": "user_004",
            "update_kind": "INTERACTION",
            "evidence_refs": [],
        })
        assert resp.status_code == 422
        assert resp.headers.get("x-reason-code") == "BOND_EVIDENCE_REQUIRED"

    def test_whitespace_only_refs_422(self, client):
        resp = client.post("/bond/event", json={
            "user_id": "user_005",
            "update_kind": "INTERACTION",
            "evidence_refs": ["  ", ""],
        })
        assert resp.status_code == 422

    def test_valid_refs_succeed(self, client):
        resp = client.post("/bond/event", json={
            "user_id": "user_006",
            "update_kind": "INTERACTION",
            "evidence_refs": ["evt::valid"],
        })
        assert resp.status_code == 200


# ── T-BOND-API-06: GET /bond/users ───────────────────────────────────────────

class TestListBondUsers:
    """T-BOND-API-06: GET /bond/users 返回所有有 Bond 状态的用户。"""

    def test_list_users_empty_initially(self, client):
        resp = client.get("/bond/users")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0

    def test_list_users_after_events(self, client):
        client.post("/bond/event", json={
            "user_id": "alice",
            "update_kind": "INTERACTION",
            "evidence_refs": ["evt::001"],
        })
        client.post("/bond/event", json={
            "user_id": "bob",
            "update_kind": "INTERACTION",
            "evidence_refs": ["evt::002"],
        })
        resp = client.get("/bond/users")
        data = resp.json()
        assert data["total"] == 1
        assert data["users"] == [get_canonical_bond_user_id()]


# ── T-BOND-API-07: 晋级后 Summary 阶段更新 ───────────────────────────────────

class TestSummaryAfterPromotion:
    """T-BOND-API-07: 晋级后 Summary 阶段字段更新。"""

    def test_summary_stage_updates_after_promotion(self, client):
        # 触发晋级到 CLOSE_FRIEND
        for i in range(14):
            client.post("/bond/event", json={
                "user_id": "frank",
                "update_kind": "DEEP_TALK",
                "evidence_refs": [f"evt::{i}"],
            })
        resp = client.get("/bond/summary/frank")
        data = resp.json()
        assert data["stage"] == "亲近兄妹"

    def test_summary_still_no_numeric_after_promotion(self, client):
        for i in range(14):
            client.post("/bond/event", json={
                "user_id": "grace",
                "update_kind": "DEEP_TALK",
                "evidence_refs": [f"evt::{i}"],
            })
        resp = client.get("/bond/summary/grace")
        data = resp.json()
        for key in ("stage", "trend", "capability_hint"):
            assert not isinstance(data[key], (int, float))


# ── T-BOND-API-08: 自定义 request_id 透传 ────────────────────────────────────

class TestRequestIdPassthrough:
    """T-BOND-API-08: 自定义 request_id 透传到响应。"""

    def test_custom_request_id_in_response(self, client):
        resp = client.post("/bond/event", json={
            "user_id": "henry",
            "update_kind": "INTERACTION",
            "evidence_refs": ["evt::001"],
            "request_id": "custom-req-001",
        })
        data = resp.json()
        assert data["request_id"] == "custom-req-001"
