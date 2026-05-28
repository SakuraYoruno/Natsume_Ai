from __future__ import annotations

from pathlib import Path

from host_brain.audit.sqlite_ledger import SQLiteLedger
from scripts.runtime.retire_legacy_semantic_memory import retire_legacy_semantic_memory


def test_retire_legacy_semantic_memory_soft_deletes_non_life_consolidated_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "audit.sqlite3"
    ledger = SQLiteLedger(db_path=db_path)
    try:
        ledger.log_semantic_memory(
            {
                "semantic_id": "sem-legacy",
                "summary": "当前回合摘要：用户提到「metrics test」；回复「哈？」。",
                "source_memory_ids": ["mem-legacy"],
                "source_hash": "legacy-hash",
                "distill_strategy": "merge_top_k",
                "confidence": 0.5,
                "tags": ["old_distill"],
                "ts_ms": 1_800_000_000_000,
                "operator_node_id": "distillation_engine",
            }
        )
        ledger.log_semantic_memory(
            {
                "semantic_id": "sem-life",
                "summary": "用户偏好：推荐按卡片分段。",
                "source_memory_ids": ["life_episode::ep-1"],
                "source_hash": "life-hash",
                "distill_strategy": "life_episode_rule_consolidation",
                "confidence": 0.8,
                "tags": ["life_consolidated", "source:life_episode"],
                "ts_ms": 1_800_000_000_001,
                "operator_node_id": "life_memory_consolidator",
            }
        )
    finally:
        ledger.close()

    dry_run = retire_legacy_semantic_memory(db_path=db_path, apply=False)
    assert dry_run["planned_count"] == 1
    assert dry_run["active_after"] == 2
    assert dry_run["samples"][0]["semantic_id"] == "sem-legacy"

    applied = retire_legacy_semantic_memory(db_path=db_path, apply=True)
    assert applied["planned_count"] == 1
    assert applied["retired_count"] == 1
    assert applied["active_after"] == 1
    assert applied["backup_path"]

    verify = SQLiteLedger(db_path=db_path)
    try:
        active, total = verify.list_semantic_memories()
        assert total == 1
        assert active[0]["semantic_id"] == "sem-life"
        assert any(row["op_kind"] == "semantic_memory_retire" and row["memory_id"] == "sem-legacy" for row in verify.memory_ops_log)
    finally:
        verify.close()
