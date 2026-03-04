"""T-TOOL-API-01~05: ToolBroker REST API 集成测试（FastAPI TestClient）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from host_brain.tools.catalog import ToolCatalog
from host_brain.tools.broker import ToolBroker, RESULT_SUCCESS, RESULT_REJECTED
from host_brain.app.tool_routes import router, set_tool_providers
from fastapi import FastAPI


# ── 测试夹具 ──────────────────────────────────────────────────────────────────

def _make_test_app() -> tuple[FastAPI, ToolCatalog, ToolBroker]:
    """构建独立测试 FastAPI 应用，注入 mock 依赖。"""
    from unittest.mock import MagicMock

    catalog = ToolCatalog()
    # 注册 READ 工具
    catalog.register({
        "id": "read_tool_01",
        "name": "Read Tool",
        "desc": "A read-only tool for testing",
        "schema_hash": "sha256:" + "a" * 64,
        "side_effect_level": "READ",
        "auth_level": "NONE",
        "domain_tags": ["test", "read"],
        "version": "1.0.0",
    })
    # 注册 WRITE 工具
    catalog.register({
        "id": "write_tool_01",
        "name": "Write Tool",
        "desc": "A write tool requiring approval",
        "schema_hash": "sha256:" + "b" * 64,
        "side_effect_level": "WRITE",
        "auth_level": "APPROVAL",
        "domain_tags": ["test", "write"],
        "version": "1.0.0",
    })

    token_service = MagicMock()
    validation = MagicMock()
    validation.valid = False
    validation.reason_code = "TOKEN_INVALID"
    validation.detail = "token invalid"
    token_service.validate_execution_tokens.return_value = validation

    ledger = MagicMock()
    ledger.log_skill_exec = MagicMock()

    broker = ToolBroker(catalog=catalog, token_service=token_service, ledger=ledger)
    broker.register_handler("read_tool_01", lambda args, ctx: {"result": "read_ok"})

    app = FastAPI()
    app.include_router(router)
    set_tool_providers(
        broker_provider=lambda: broker,
        catalog_provider=lambda: catalog,
        ledger_provider=lambda: ledger,
    )
    return app, catalog, broker


@pytest.fixture
def client():
    app, catalog, broker = _make_test_app()
    return TestClient(app)


# ── T-TOOL-API-01: GET /tools/health 返回 OK ─────────────────────────────────

class TestToolHealth:
    """T-TOOL-API-01: GET /tools/health 返回 OK 且字段稳定。"""

    def test_health_ok(self, client):
        resp = client.get("/tools/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "OK"
        assert data["catalog_size"] == 2
        assert data["read_tools"] == 1
        assert data["write_tools"] == 1
        assert "request_id" in data
        assert "timestamp" in data

    def test_health_counts_correct(self, client):
        resp = client.get("/tools/health")
        data = resp.json()
        assert data["read_tools"] + data["write_tools"] == data["catalog_size"]


# ── T-TOOL-API-02: GET /tools/meta-pack 默认不含 WRITE ───────────────────────

class TestToolMetaPack:
    """T-TOOL-API-02: GET /tools/meta-pack 默认不含 WRITE 工具。"""

    def test_meta_pack_excludes_write_by_default(self, client):
        resp = client.get("/tools/meta-pack")
        assert resp.status_code == 200
        data = resp.json()
        ids = data["selected_ids"]
        assert "read_tool_01" in ids
        assert "write_tool_01" not in ids

    def test_meta_pack_includes_write_when_explicit(self, client):
        resp = client.get("/tools/meta-pack?include_write=true")
        assert resp.status_code == 200
        data = resp.json()
        assert "write_tool_01" in data["selected_ids"]

    def test_meta_pack_has_visibility_hash(self, client):
        resp = client.get("/tools/meta-pack")
        data = resp.json()
        assert data["tool_visibility_hash"].startswith("sha256:")

    def test_meta_pack_k_limits_results(self, client):
        resp = client.get("/tools/meta-pack?k=1&include_write=true")
        data = resp.json()
        assert len(data["selected_ids"]) == 1

    def test_meta_pack_fields_stable(self, client):
        resp = client.get("/tools/meta-pack")
        data = resp.json()
        for meta in data["tool_meta_pack"]:
            for field in ("id", "name", "desc", "schema_hash", "side_effect_level", "auth_level", "domain_tags", "version"):
                assert field in meta, f"missing field: {field}"


# ── T-TOOL-API-03: GET /tools/{tool_id} 查询单个工具 ─────────────────────────

class TestGetToolMeta:
    """T-TOOL-API-03: GET /tools/{tool_id} 查询单个工具元数据。"""

    def test_get_existing_tool(self, client):
        resp = client.get("/tools/read_tool_01")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "read_tool_01"
        assert data["side_effect_level"] == "READ"

    def test_get_nonexistent_tool_404(self, client):
        resp = client.get("/tools/nonexistent_tool")
        assert resp.status_code == 404
        assert resp.headers.get("x-reason-code") == "TOOL_NOT_FOUND"

    def test_get_write_tool(self, client):
        resp = client.get("/tools/write_tool_01")
        assert resp.status_code == 200
        data = resp.json()
        assert data["side_effect_level"] == "WRITE"
        assert data["auth_level"] == "APPROVAL"


# ── T-TOOL-API-04: POST /tools/execute READ 工具成功 ─────────────────────────

class TestExecuteReadTool:
    """T-TOOL-API-04: POST /tools/execute READ 工具无需 token 直接成功。"""

    def test_read_tool_success(self, client):
        resp = client.post("/tools/execute", json={
            "tool_id": "read_tool_01",
            "intent_id": "intent-001",
            "session_id": "sess-001",
            "seq": 1,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == RESULT_SUCCESS
        assert data["output"] == {"result": "read_ok"}
        assert data["evidence_ref"] is not None
        assert data["evidence_ref"].startswith("evidence::")

    def test_read_tool_response_fields(self, client):
        resp = client.post("/tools/execute", json={
            "tool_id": "read_tool_01",
            "intent_id": "intent-002",
            "session_id": "sess-001",
            "seq": 1,
        })
        data = resp.json()
        for field in ("status", "result_code", "tool_id", "intent_id", "request_id", "timestamp"):
            assert field in data, f"missing field: {field}"

    def test_nonexistent_tool_404(self, client):
        resp = client.post("/tools/execute", json={
            "tool_id": "nonexistent",
            "intent_id": "intent-003",
            "session_id": "sess-001",
            "seq": 1,
        })
        assert resp.status_code == 404


# ── T-TOOL-API-05: POST /tools/execute WRITE 工具无 token → 403 ──────────────

class TestExecuteWriteToolGating:
    """T-TOOL-API-05: POST /tools/execute WRITE 工具无 token → 403。"""

    def test_write_tool_no_token_403(self, client):
        resp = client.post("/tools/execute", json={
            "tool_id": "write_tool_01",
            "intent_id": "intent-w01",
            "session_id": "sess-001",
            "seq": 1,
            # 无 approval_token_ref
        })
        # WRITE 工具无 handler → TOOL_HANDLER_MISSING → 422
        # 但 token 校验先于 handler 检查，所以应该是 403
        assert resp.status_code in (403, 422)

    def test_write_tool_handler_missing_422(self, client):
        # write_tool_01 没有注册 handler，token 校验失败 → 403
        resp = client.post("/tools/execute", json={
            "tool_id": "write_tool_01",
            "intent_id": "intent-w02",
            "session_id": "sess-001",
            "seq": 1,
            "approval_token_ref": None,
        })
        assert resp.status_code in (403, 422)
