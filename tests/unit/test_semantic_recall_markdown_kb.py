from __future__ import annotations

from types import SimpleNamespace

import pytest

from host_brain.app.recall import semantic_recall
from host_brain.app.recall.semantic_recall import SemanticRecaller


@pytest.mark.asyncio
async def test_semantic_recaller_uses_markdown_kb_when_primary_empty(monkeypatch) -> None:
    def _fake_query_markdown_kbs(query: str, *, top_k: int = 5):
        assert query == "GraphRAG"
        return [
            SimpleNamespace(
                doc_id="agent_guide:00173",
                content="第 9 章 检索、知识库与 GraphRAG 工程",
                score=1.0,
                metadata={"kb_id": "agent_guide", "heading_path": "第 9 章"},
                source="markdown_kb",
            )
        ][:top_k]

    monkeypatch.setattr(semantic_recall, "query_markdown_kbs", _fake_query_markdown_kbs)

    recaller = SemanticRecaller(use_mock=True, markdown_kb_enabled=True)
    result = await recaller.recall("GraphRAG", top_k=3, threshold=0.7)

    assert result.filtered_count == 1
    assert result.hits[0].source == "markdown_kb"
    assert result.hits[0].metadata["kb_id"] == "agent_guide"


@pytest.mark.asyncio
async def test_semantic_recaller_skips_markdown_kb_for_metadata_filtered_recall(monkeypatch) -> None:
    called = False

    def _fake_query_markdown_kbs(query: str, *, top_k: int = 5):  # pragma: no cover - must not be called
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(semantic_recall, "query_markdown_kbs", _fake_query_markdown_kbs)

    recaller = SemanticRecaller(use_mock=True, markdown_kb_enabled=True)
    result = await recaller.recall(
        "GraphRAG",
        top_k=3,
        threshold=0.7,
        filter_metadata={"session_id": "session-1"},
    )

    assert result.filtered_count == 0
    assert called is False
