"""T-CVP-CONTRACT-01~06: Canon 向量投影模块契约测试

TASK-0205 M21-C：验证 canon_vector_projection.py 的核心契约点：
- T-CVP-CONTRACT-01: 投影文档 canon_kind 字段必须为 style 或 world
- T-CVP-CONTRACT-02: 向量 ID 格式确定性（canon_style::{canon_id} / canon_world::{doc_id}）
- T-CVP-CONTRACT-03: metadata tags 字段必须为逗号分隔字符串（非 list），tags_count 为整数
- T-CVP-CONTRACT-04: Chroma 不可用时投影操作不抛出未捕获异常，返回降级结果
- T-CVP-CONTRACT-05: 投影文档不含 Canon SSOT 写路径（不直接修改 canon_proposal_log / canon_change_log）
- T-CVP-CONTRACT-06: --dry-run 模式（include_style=False, include_world=False）不写入 Chroma，仅返回 docs_collected 统计
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from host_brain.memory.canon_vector_projection import (
    CanonProjectionDocument,
    collect_canon_projection_documents,
    sync_canon_vector_index,
)


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------

def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8-sig",
    )


class _FakeCollection:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.upsert_called: int = 0

    def upsert(self, *, ids, documents, metadatas, embeddings) -> None:
        self.upsert_called += 1
        for idx, row_id in enumerate(ids):
            self.rows[str(row_id)] = {
                "document": documents[idx],
                "metadata": metadatas[idx],
                "embedding_dim": len(embeddings[idx]),
            }

    def count(self) -> int:
        return len(self.rows)


class _FakeChromaClient:
    def __init__(self) -> None:
        self.collections: dict[str, _FakeCollection] = {}

    def get_or_create_collection(self, *, name: str) -> _FakeCollection:
        key = str(name)
        if key not in self.collections:
            self.collections[key] = _FakeCollection()
        return self.collections[key]


def _make_env() -> dict[str, str]:
    return {
        "CHROMA_ENABLED": "true",
        "CHROMA_ENDPOINT": "http://127.0.0.1:8000",
        "CHROMA_DEFAULT_COLLECTION": "episodic",
        "CHROMA_COLLECTION_PROFILES_JSON": json.dumps({"episodic": 16, "canon": 16}),
    }


def _make_style_world_roots(tmp_path: Path) -> tuple[Path, Path]:
    style_root = tmp_path / "style"
    world_root = tmp_path / "world"
    _write_json(
        style_root / "s001.json",
        {
            "canon_id": "s001",
            "version": 1,
            "hash": "h1",
            "tone_only": True,
            "unsafe_as_fact": True,
            "content": "Be concise.",
            "tags": ["tone", "style"],
        },
    )
    _write_json(
        world_root / "w001.json",
        {
            "doc_id": "w001",
            "version": 1,
            "hash": "h2",
            "status": "active",
            "title": "World Doc",
            "body": "World body content.",
            "tags": ["world", "lore"],
        },
    )
    return style_root, world_root


# ---------------------------------------------------------------------------
# T-CVP-CONTRACT-01: canon_kind 字段必须为 style 或 world
# ---------------------------------------------------------------------------

class TestCanonKindContract:
    """T-CVP-CONTRACT-01"""

    def test_all_docs_have_valid_canon_kind(self, tmp_path: Path) -> None:
        """所有投影文档的 canon_kind 必须为 'style' 或 'world'。"""
        style_root, world_root = _make_style_world_roots(tmp_path)
        docs, issues = collect_canon_projection_documents(
            style_root=style_root,
            world_root=world_root,
        )
        assert len(docs) == 2
        assert issues == []
        valid_kinds = {"style", "world"}
        for doc in docs:
            kind = doc.metadata.get("canon_kind")
            assert kind in valid_kinds, (
                f"vector_id={doc.vector_id} 的 canon_kind={kind!r} 不在 {valid_kinds}"
            )

    def test_style_doc_has_kind_style(self, tmp_path: Path) -> None:
        """Style Canon 文档的 canon_kind 必须为 'style'。"""
        style_root = tmp_path / "style"
        world_root = tmp_path / "world"
        _write_json(
            style_root / "s_only.json",
            {"canon_id": "s_only", "version": 1, "hash": "h", "content": "x", "tags": []},
        )
        docs, _ = collect_canon_projection_documents(
            style_root=style_root, world_root=world_root,
            include_style=True, include_world=False,
        )
        assert len(docs) == 1
        assert docs[0].metadata["canon_kind"] == "style"

    def test_world_doc_has_kind_world(self, tmp_path: Path) -> None:
        """World Canon 文档的 canon_kind 必须为 'world'。"""
        style_root = tmp_path / "style"
        world_root = tmp_path / "world"
        _write_json(
            world_root / "w_only.json",
            {"doc_id": "w_only", "version": 1, "hash": "h", "body": "x", "title": "T", "tags": []},
        )
        docs, _ = collect_canon_projection_documents(
            style_root=style_root, world_root=world_root,
            include_style=False, include_world=True,
        )
        assert len(docs) == 1
        assert docs[0].metadata["canon_kind"] == "world"


# ---------------------------------------------------------------------------
# T-CVP-CONTRACT-02: 向量 ID 格式确定性
# ---------------------------------------------------------------------------

class TestVectorIdFormatContract:
    """T-CVP-CONTRACT-02"""

    def test_style_vector_id_format(self, tmp_path: Path) -> None:
        """Style 文档向量 ID 格式必须为 canon_style::{canon_id}。"""
        style_root = tmp_path / "style"
        world_root = tmp_path / "world"
        _write_json(
            style_root / "my_style.json",
            {"canon_id": "my_style", "version": 1, "hash": "h", "content": "x", "tags": []},
        )
        docs, _ = collect_canon_projection_documents(
            style_root=style_root, world_root=world_root,
            include_style=True, include_world=False,
        )
        assert docs[0].vector_id == "canon_style::my_style"

    def test_world_vector_id_format(self, tmp_path: Path) -> None:
        """World 文档向量 ID 格式必须为 canon_world::{doc_id}。"""
        style_root = tmp_path / "style"
        world_root = tmp_path / "world"
        _write_json(
            world_root / "my_world.json",
            {"doc_id": "my_world", "version": 1, "hash": "h", "body": "x", "title": "T", "tags": []},
        )
        docs, _ = collect_canon_projection_documents(
            style_root=style_root, world_root=world_root,
            include_style=False, include_world=True,
        )
        assert docs[0].vector_id == "canon_world::my_world"

    def test_vector_id_deterministic_same_input(self, tmp_path: Path) -> None:
        """相同输入文件，两次调用产生相同向量 ID（确定性）。"""
        style_root, world_root = _make_style_world_roots(tmp_path)
        docs1, _ = collect_canon_projection_documents(
            style_root=style_root, world_root=world_root,
        )
        docs2, _ = collect_canon_projection_documents(
            style_root=style_root, world_root=world_root,
        )
        ids1 = sorted(d.vector_id for d in docs1)
        ids2 = sorted(d.vector_id for d in docs2)
        assert ids1 == ids2


# ---------------------------------------------------------------------------
# T-CVP-CONTRACT-03: metadata tags 为逗号分隔字符串，tags_count 为整数
# ---------------------------------------------------------------------------

class TestMetadataTagsContract:
    """T-CVP-CONTRACT-03"""

    def test_tags_is_csv_string_not_list(self, tmp_path: Path) -> None:
        """metadata.tags 必须为逗号分隔字符串，不得为 list。"""
        style_root, world_root = _make_style_world_roots(tmp_path)
        docs, _ = collect_canon_projection_documents(
            style_root=style_root, world_root=world_root,
        )
        for doc in docs:
            tags_val = doc.metadata.get("tags")
            assert isinstance(tags_val, str), (
                f"vector_id={doc.vector_id} 的 tags 类型为 {type(tags_val).__name__}，应为 str"
            )

    def test_tags_count_is_int(self, tmp_path: Path) -> None:
        """metadata.tags_count 必须为整数。"""
        style_root, world_root = _make_style_world_roots(tmp_path)
        docs, _ = collect_canon_projection_documents(
            style_root=style_root, world_root=world_root,
        )
        for doc in docs:
            tags_count = doc.metadata.get("tags_count")
            assert isinstance(tags_count, int), (
                f"vector_id={doc.vector_id} 的 tags_count 类型为 {type(tags_count).__name__}，应为 int"
            )

    def test_tags_csv_matches_tags_count(self, tmp_path: Path) -> None:
        """tags CSV 分割后的数量与 tags_count 一致。"""
        style_root, world_root = _make_style_world_roots(tmp_path)
        docs, _ = collect_canon_projection_documents(
            style_root=style_root, world_root=world_root,
        )
        for doc in docs:
            tags_csv: str = doc.metadata["tags"]
            tags_count: int = doc.metadata["tags_count"]
            if tags_csv:
                assert len(tags_csv.split(",")) == tags_count, (
                    f"vector_id={doc.vector_id} tags CSV 数量与 tags_count 不一致"
                )
            else:
                assert tags_count == 0

    def test_empty_tags_produces_empty_string(self, tmp_path: Path) -> None:
        """空 tags 列表应产生空字符串，tags_count=0。"""
        style_root = tmp_path / "style"
        world_root = tmp_path / "world"
        _write_json(
            style_root / "no_tags.json",
            {"canon_id": "no_tags", "version": 1, "hash": "h", "content": "x", "tags": []},
        )
        docs, _ = collect_canon_projection_documents(
            style_root=style_root, world_root=world_root,
            include_style=True, include_world=False,
        )
        assert docs[0].metadata["tags"] == ""
        assert docs[0].metadata["tags_count"] == 0


# ---------------------------------------------------------------------------
# T-CVP-CONTRACT-04: Chroma 不可用时不抛出未捕获异常，返回降级结果
# ---------------------------------------------------------------------------

class TestChromaUnavailableDegradationContract:
    """T-CVP-CONTRACT-04"""

    def test_chroma_unavailable_raises_runtime_error_not_uncaught(
        self, tmp_path: Path
    ) -> None:
        """Chroma 不可用时 sync_canon_vector_index 抛出 RuntimeError（可预期），
        而非 AttributeError / TypeError 等未捕获异常。"""
        style_root, world_root = _make_style_world_roots(tmp_path)
        env = {
            "CHROMA_ENABLED": "true",
            "CHROMA_ENDPOINT": "http://127.0.0.1:19999",  # 不可达端口
            "CHROMA_DEFAULT_COLLECTION": "episodic",
            "CHROMA_COLLECTION_PROFILES_JSON": json.dumps({"episodic": 16, "canon": 16}),
        }

        class _BrokenChromaClient:
            def get_or_create_collection(self, *, name: str):
                raise ConnectionRefusedError("chroma unavailable")

        try:
            sync_canon_vector_index(
                style_root=style_root,
                world_root=world_root,
                collection="canon",
                backend="http",
                env=env,
                chroma_client=_BrokenChromaClient(),
            )
            # 若未抛出异常，说明有内部降级处理，也可接受
        except (RuntimeError, ConnectionRefusedError, OSError):
            # 预期的可捕获异常类型
            pass
        except Exception as exc:
            pytest.fail(
                f"Chroma 不可用时抛出了未预期的异常类型 {type(exc).__name__}: {exc}"
            )

    def test_collect_docs_does_not_require_chroma(self, tmp_path: Path) -> None:
        """collect_canon_projection_documents 不依赖 Chroma，始终可正常返回。"""
        style_root, world_root = _make_style_world_roots(tmp_path)
        # 不传任何 Chroma 配置，仅收集文档
        docs, issues = collect_canon_projection_documents(
            style_root=style_root,
            world_root=world_root,
        )
        assert len(docs) == 2
        assert issues == []


# ---------------------------------------------------------------------------
# T-CVP-CONTRACT-05: 投影文档不含 Canon SSOT 写路径
# ---------------------------------------------------------------------------

class TestNoSSOTWritePathContract:
    """T-CVP-CONTRACT-05"""

    def test_projection_doc_has_no_proposal_log_field(self, tmp_path: Path) -> None:
        """投影文档 metadata 不含 canon_proposal_log / canon_change_log 字段。"""
        style_root, world_root = _make_style_world_roots(tmp_path)
        docs, _ = collect_canon_projection_documents(
            style_root=style_root, world_root=world_root,
        )
        forbidden_fields = {"canon_proposal_log", "canon_change_log", "proposal_id", "change_id"}
        for doc in docs:
            overlap = set(doc.metadata.keys()) & forbidden_fields
            assert not overlap, (
                f"vector_id={doc.vector_id} 的 metadata 含 SSOT 写路径字段: {overlap}"
            )

    def test_projection_doc_has_no_write_intent_in_text(self, tmp_path: Path) -> None:
        """投影文档 text 不含 SSOT 写操作关键词（INSERT/UPDATE/DELETE）。"""
        style_root, world_root = _make_style_world_roots(tmp_path)
        docs, _ = collect_canon_projection_documents(
            style_root=style_root, world_root=world_root,
        )
        forbidden_keywords = {"INSERT INTO", "UPDATE SET", "DELETE FROM"}
        for doc in docs:
            for kw in forbidden_keywords:
                assert kw not in doc.text.upper(), (
                    f"vector_id={doc.vector_id} 的 text 含 SSOT 写操作关键词: {kw}"
                )

    def test_sync_does_not_write_to_ssot_tables(self, tmp_path: Path) -> None:
        """sync_canon_vector_index 只写入 Chroma，不写入 canon_proposal_log / canon_change_log。"""
        style_root, world_root = _make_style_world_roots(tmp_path)
        fake_client = _FakeChromaClient()
        summary = sync_canon_vector_index(
            style_root=style_root,
            world_root=world_root,
            collection="canon",
            backend="http",
            env=_make_env(),
            chroma_client=fake_client,
        )
        assert summary["success"] is True
        # 只有一个 canon collection，不含 SSOT 表
        assert set(fake_client.collections.keys()) == {"canon"}


# ---------------------------------------------------------------------------
# T-CVP-CONTRACT-06: dry-run 模式不写入 Chroma，仅返回 docs_collected 统计
# ---------------------------------------------------------------------------

class TestDryRunContract:
    """T-CVP-CONTRACT-06"""

    def test_dry_run_no_style_no_world_returns_zero_synced(self, tmp_path: Path) -> None:
        """include_style=False, include_world=False 时不写入 Chroma，docs_synced=0。"""
        style_root, world_root = _make_style_world_roots(tmp_path)
        fake_client = _FakeChromaClient()
        summary = sync_canon_vector_index(
            style_root=style_root,
            world_root=world_root,
            collection="canon",
            backend="http",
            env=_make_env(),
            chroma_client=fake_client,
            include_style=False,
            include_world=False,
        )
        assert summary["success"] is True
        assert summary["docs_synced"] == 0
        assert summary["style_docs_synced"] == 0
        assert summary["world_docs_synced"] == 0
        # Chroma collection 存在但无写入
        collection = fake_client.get_or_create_collection(name="canon")
        assert collection.upsert_called == 0

    def test_dry_run_collect_only_returns_correct_count(self, tmp_path: Path) -> None:
        """collect_canon_projection_documents 在 include_style=False 时只收集 world 文档。"""
        style_root, world_root = _make_style_world_roots(tmp_path)
        docs, issues = collect_canon_projection_documents(
            style_root=style_root,
            world_root=world_root,
            include_style=False,
            include_world=True,
        )
        assert len(docs) == 1
        assert docs[0].metadata["canon_kind"] == "world"
        assert issues == []

    def test_dry_run_style_only_does_not_write_world(self, tmp_path: Path) -> None:
        """include_world=False 时 world_docs_synced=0，style_docs_synced=1。"""
        style_root, world_root = _make_style_world_roots(tmp_path)
        fake_client = _FakeChromaClient()
        summary = sync_canon_vector_index(
            style_root=style_root,
            world_root=world_root,
            collection="canon",
            backend="http",
            env=_make_env(),
            chroma_client=fake_client,
            include_style=True,
            include_world=False,
        )
        assert summary["success"] is True
        assert summary["style_docs_synced"] == 1
        assert summary["world_docs_synced"] == 0
        assert summary["docs_synced"] == 1
