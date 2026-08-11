from __future__ import annotations

import sqlite3
from pathlib import Path

import host_brain.retrieval.markdown_kb_store as markdown_store
from host_brain.retrieval.markdown_kb_store import query_markdown_kbs


def test_markdown_kb_fallback_is_read_through_persistence_adapter(tmp_path) -> None:
    db_path = tmp_path / "kb.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE kb_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO kb_meta VALUES ('kb_id', '\"aw03-kb\"')")
        conn.execute(
            """
            CREATE TABLE chunks(
                chunk_id TEXT PRIMARY KEY, doc_id TEXT, chunk_index INTEGER,
                title TEXT, heading_path TEXT, line_start INTEGER, line_end INTEGER,
                text TEXT, source_relpath TEXT, asset_refs_json TEXT,
                token_estimate INTEGER
            )
            """
        )
        conn.execute(
            "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("chunk-1", "doc-1", 0, "Natsume", "Root / Natsume", 1, 2,
             "durable memory spine", "doc.md", "[]", 3),
        )

    hits = query_markdown_kbs("durable", db_paths=[db_path], top_k=3)

    assert len(hits) == 1
    assert hits[0].doc_id == "chunk-1"
    assert hits[0].metadata["kb_id"] == "aw03-kb"
    source = Path(markdown_store.__file__).read_text(encoding="utf-8")
    assert "import sqlite3" not in source
    assert "sqlite3.connect" not in source
    assert "os.environ" not in source
