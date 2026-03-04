from __future__ import annotations

import json
from pathlib import Path

from host_brain.memory.canon_vector_projection import (
    collect_canon_projection_documents,
    sync_canon_vector_index,
)


class _FakeCollection:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    def upsert(
        self,
        *,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict],
        embeddings: list[list[float]],
    ) -> None:
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

    def get_or_create_collection(self, *, name: str):
        key = str(name)
        if key not in self.collections:
            self.collections[key] = _FakeCollection()
        return self.collections[key]


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_collect_projection_docs_reports_invalid_json(tmp_path: Path) -> None:
    style_root = tmp_path / "style"
    world_root = tmp_path / "world"
    style_root.mkdir(parents=True, exist_ok=True)
    world_root.mkdir(parents=True, exist_ok=True)
    (style_root / "broken.json").write_text("{", encoding="utf-8")

    docs, issues = collect_canon_projection_documents(
        style_root=style_root,
        world_root=world_root,
        include_style=True,
        include_world=False,
    )

    assert docs == []
    assert len(issues) == 1
    assert issues[0]["code"] == "CANON_JSON_INVALID"


def test_sync_canon_projection_into_vector_collection(tmp_path: Path) -> None:
    style_root = tmp_path / "style"
    world_root = tmp_path / "world"

    _write_json(
        style_root / "natsume_ai_cn.json",
        {
            "canon_id": "natsume_ai_cn",
            "version": 1,
            "hash": "style_hash_v1",
            "tone_only": True,
            "unsafe_as_fact": True,
            "content": "Keep concise and empathetic.",
            "tags": ["style_canon", "tone_only"],
        },
    )
    _write_json(
        world_root / "academy_rules.json",
        {
            "doc_id": "academy_rules",
            "version": 2,
            "hash": "world_hash_v2",
            "status": "active",
            "title": "Academy Rules",
            "body": "Use registered lab stations for practice sessions.",
            "tags": ["setting"],
        },
    )

    fake_client = _FakeChromaClient()
    env = {
        "CHROMA_ENABLED": "true",
        "CHROMA_ENDPOINT": "http://127.0.0.1:8000",
        "CHROMA_DEFAULT_COLLECTION": "episodic",
        "CHROMA_COLLECTION_PROFILES_JSON": json.dumps({"episodic": 16, "canon": 16}),
    }
    summary = sync_canon_vector_index(
        style_root=style_root,
        world_root=world_root,
        collection="canon",
        backend="http",
        include_style=True,
        include_world=True,
        env=env,
        chroma_client=fake_client,
    )

    assert summary["success"] is True
    assert summary["docs_synced"] == 2
    assert summary["style_docs_synced"] == 1
    assert summary["world_docs_synced"] == 1
    assert summary["vector_dim"] == 16
    assert summary["issues_count"] == 0
    assert summary["collection_count"] == 2

    collection = fake_client.get_or_create_collection(name="canon")
    assert "canon_style::natsume_ai_cn" in collection.rows
    assert "canon_world::academy_rules" in collection.rows

    style_row = collection.rows["canon_style::natsume_ai_cn"]
    assert style_row["embedding_dim"] == 16
    assert style_row["metadata"]["canon_kind"] == "style"
    assert style_row["metadata"]["version"] == 1
    assert style_row["metadata"]["tags"] == "style_canon,tone_only"
    assert style_row["metadata"]["tags_count"] == 2

    world_row = collection.rows["canon_world::academy_rules"]
    assert world_row["embedding_dim"] == 16
    assert world_row["metadata"]["canon_kind"] == "world"
    assert world_row["metadata"]["version"] == 2
    assert world_row["metadata"]["tags"] == "setting"
    assert world_row["metadata"]["tags_count"] == 1
