"""M10-B ASR 热词注入 — WorldCanonStore.extract_hotwords() 单元测试。

T-Canon-HW-01：正常提取 — active 文档的 title/tags 应出现在热词列表中，去重排序。
T-Canon-HW-02：max_words 截断 — 超出 max_words 的热词应被截断。
T-Canon-HW-03：空 SSOT 目录 — 无文档时返回空列表。
"""

from __future__ import annotations

import json
import pytest

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.canon_world_store import WorldCanonStore, compute_world_canon_hash


# ---------------------------------------------------------------------------
# 共享 fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def ledger(tmp_path):
    return SQLiteLedger(db_path=str(tmp_path / "test.sqlite3"))


@pytest.fixture()
def store(tmp_path, ledger):
    return WorldCanonStore(world_root=str(tmp_path / "world"), ledger=ledger)


def _make_doc(store: WorldCanonStore, ledger: SQLiteLedger, doc_id: str, title: str, tags: list[str]) -> None:
    """辅助函数：在临时测试根写入只读 fixture。"""
    payload = {
        "doc_id": doc_id,
        "version": 1,
        "status": "active",
        "title": title,
        "tags": tags,
        "body": f"{title} 的详细描述内容，用于测试热词提取功能。",
        "updated_by": "test-fixture",
        "ts_ms": 1000,
    }
    payload["hash"] = compute_world_canon_hash(payload)
    store._doc_path(doc_id).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# T-Canon-HW-01：正常提取
# ---------------------------------------------------------------------------

def test_extract_hotwords_normal(store, ledger):
    """active 文档的 title/tags 应出现在热词列表中，去重排序。"""
    _make_doc(store, ledger, "char-001", "夏目友人帐", ["妖怪", "友情"])
    _make_doc(store, ledger, "char-002", "斑", ["猫咪老师", "妖怪"])  # "妖怪" 重复

    hotwords = store.extract_hotwords()

    assert isinstance(hotwords, list)
    assert "夏目友人帐" in hotwords
    assert "斑" in hotwords
    assert "妖怪" in hotwords
    assert "友情" in hotwords
    assert "猫咪老师" in hotwords

    # 去重：重复的 "妖怪" 只出现一次
    assert hotwords.count("妖怪") == 1

    # 排序：列表应按字母序排列
    assert hotwords == sorted(hotwords)


# ---------------------------------------------------------------------------
# T-Canon-HW-02：max_words 截断
# ---------------------------------------------------------------------------

def test_extract_hotwords_max_words(store, ledger):
    """超出 max_words 的热词应被截断。"""
    # 创建 10 个文档，每个有 3 个 tag，共约 40 个热词
    for i in range(10):
        _make_doc(
            store, ledger,
            f"doc-{i:03d}",
            f"文档标题{i:03d}",
            [f"标签A{i:03d}", f"标签B{i:03d}", f"标签C{i:03d}"],
        )

    # max_words=5 时应只返回 5 个
    hotwords_5 = store.extract_hotwords(max_words=5)
    assert len(hotwords_5) == 5

    # max_words=100 时应返回全部（约 40 个）
    hotwords_all = store.extract_hotwords(max_words=100)
    assert len(hotwords_all) == 40  # 10 titles + 30 tags

    # max_words=5 的结果应是 max_words=100 结果的前 5 个（排序后截断）
    assert hotwords_5 == hotwords_all[:5]


# ---------------------------------------------------------------------------
# T-Canon-HW-03：空 SSOT 目录
# ---------------------------------------------------------------------------

def test_extract_hotwords_empty(store):
    """无文档时 extract_hotwords 应返回空列表。"""
    hotwords = store.extract_hotwords()
    assert hotwords == []
    assert isinstance(hotwords, list)
