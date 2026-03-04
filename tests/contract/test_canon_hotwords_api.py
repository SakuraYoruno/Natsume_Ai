"""M10-B ASR 热词注入 — GET /canon/hotwords API 契约测试。

T-Canon-HW-API-01：正常返回 — hotwords 列表非空，total 与列表长度一致。
T-Canon-HW-API-02：max_words 参数生效 — max_words=2 时返回不超过 2 个热词。
"""

from __future__ import annotations

import os
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("Natsume_AUDIT_DB", ":memory:")
os.environ.setdefault("Natsume_WORLD_CANON_ROOT", "")  # 由 fixture 覆盖


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """创建带有预置文档的 TestClient。"""
    monkeypatch.setenv("Natsume_AUDIT_DB", ":memory:")
    monkeypatch.setenv("Natsume_WORLD_CANON_ROOT", str(tmp_path / "world"))

    # 重置模块级单例，确保 fixture 隔离
    import host_brain.app.canon_routes as cr
    import host_brain.app.fastapi_app as fa
    cr._canon_store = None
    fa._ledger = None

    from host_brain.audit.sqlite_ledger import SQLiteLedger
    from host_brain.memory.canon_world_store import WorldCanonStore

    ledger = SQLiteLedger(db_path=":memory:")
    store = WorldCanonStore(world_root=str(tmp_path / "world"), ledger=ledger)

    # 预置两个 active 文档
    for doc_id, title, tags in [
        ("hw-doc-001", "夏目贵志", ["主角", "人类"]),
        ("hw-doc-002", "猫咪老师", ["妖怪", "斑"]),
    ]:
        store.submit_proposal(
            proposal_id=f"p-{doc_id}",
            target_doc_id=doc_id,
            proposed_title=title,
            proposed_tags=tags,
            proposed_body=f"{title} 的详细描述，用于 API 测试。",
            ts_ms=1000,
        )
        store.approve_proposal_with_content(
            f"p-{doc_id}",
            proposed_title=title,
            proposed_tags=tags,
            proposed_body=f"{title} 的详细描述，用于 API 测试。",
            ts_ms=2000,
        )

    # 注入 store 和 ledger 到 canon_routes
    cr._canon_store = store
    cr.set_ledger_provider(lambda: ledger)

    from host_brain.app.fastapi_app import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# T-Canon-HW-API-01：正常返回
# ---------------------------------------------------------------------------

def test_get_hotwords_normal(client):
    """GET /canon/hotwords 应返回 hotwords 列表，total 与列表长度一致。"""
    resp = client.get("/canon/hotwords")
    assert resp.status_code == 200

    data = resp.json()
    assert "hotwords" in data
    assert "total" in data
    assert "request_id" in data
    assert "timestamp" in data

    hotwords = data["hotwords"]
    assert isinstance(hotwords, list)
    assert len(hotwords) > 0
    assert data["total"] == len(hotwords)

    # 预置的热词应出现在列表中
    assert "夏目贵志" in hotwords
    assert "猫咪老师" in hotwords


# ---------------------------------------------------------------------------
# T-Canon-HW-API-02：max_words 参数生效
# ---------------------------------------------------------------------------

def test_get_hotwords_max_words(client):
    """GET /canon/hotwords?max_words=2 应返回不超过 2 个热词。"""
    resp = client.get("/canon/hotwords?max_words=2")
    assert resp.status_code == 200

    data = resp.json()
    hotwords = data["hotwords"]
    assert len(hotwords) <= 2
    assert data["total"] == len(hotwords)
