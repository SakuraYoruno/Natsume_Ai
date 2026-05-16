from __future__ import annotations

import sqlite3
from pathlib import Path

from tools.rebuild_long_term_vector_db import load_long_term_memory_seeds, rebuild_vector_db


def _init_ledger(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE semantic_memory (
            semantic_id TEXT,
            summary TEXT,
            source_memory_ids TEXT,
            tags TEXT,
            confidence REAL,
            ts_ms INTEGER,
            deleted_at INTEGER
        );
        CREATE TABLE memory_ops_log (
            id INTEGER PRIMARY KEY,
            request_id TEXT,
            intent_id TEXT,
            memory_id TEXT,
            evidence_refs TEXT,
            status TEXT,
            op_kind TEXT,
            detail TEXT,
            decay_profile_id TEXT,
            visibility TEXT,
            ts_ms INTEGER
        );
        """
    )
    conn.execute(
        "INSERT INTO semantic_memory VALUES (?,?,?,?,?,?,NULL)",
        ("sem-1", "用户偏好低能量时少追问。", '["mem-1"]', '["preference"]', 0.92, 100),
    )
    conn.execute(
        "INSERT INTO memory_ops_log VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (1, None, None, "mem-test", '["ev-test"]', "SUCCESS", "WRITE", "memory written: 测试记忆", "default", "internal", 101),
    )
    conn.execute(
        "INSERT INTO memory_ops_log VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            2,
            "req-1",
            "intent-1",
            "mem-real",
            '["chat://session/s1"]',
            "SUCCESS",
            "WRITE",
            "memory written: 用户喜欢技术场景先给可执行步骤。",
            "SLOW",
            "internal",
            102,
        ),
    )
    conn.commit()
    conn.close()


def test_load_long_term_memory_seeds_skips_requestless_test_rows(tmp_path: Path) -> None:
    ledger = tmp_path / "audit.sqlite3"
    _init_ledger(ledger)

    seeds, stats = load_long_term_memory_seeds(ledger)

    assert [seed.memory_id for seed in seeds] == ["semantic::sem-1", "audit::mem-real"]
    assert stats == {"semantic_memory": 1, "memory_ops_log": 1, "skipped_test_or_requestless": 1}


def test_rebuild_vector_db_dry_run_reports_seed_count(tmp_path: Path) -> None:
    ledger = tmp_path / "audit.sqlite3"
    _init_ledger(ledger)

    summary = rebuild_vector_db(
        chroma_dir=tmp_path / "chroma_db",
        ledger_path=ledger,
        collection="episodic",
        dim=1024,
        dry_run=True,
    )

    assert summary["dry_run"] is True
    assert summary["collection"] == "episodic"
    assert summary["seed_count"] == 2
