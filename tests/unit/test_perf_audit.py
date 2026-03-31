"""T-PERF-AUDIT-01~02：SQLiteLedger _table_has_column 缓存测试（TASK-0219 M25-C）。"""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from host_brain.audit.sqlite_ledger import SQLiteLedger


# ---------------------------------------------------------------------------
# 辅助工厂
# ---------------------------------------------------------------------------

def _make_ledger() -> SQLiteLedger:
    return SQLiteLedger(db_path=":memory:", check_same_thread=False)


# ---------------------------------------------------------------------------
# T-PERF-AUDIT-01：第二次调用 _table_has_column 不执行 PRAGMA table_info
# ---------------------------------------------------------------------------

class TestTableHasColumnCache:
    """T-PERF-AUDIT-01：第二次调用 _table_has_column 不执行 PRAGMA table_info。"""

    def test_second_call_uses_cache(self) -> None:
        ledger = _make_ledger()

        # 清空缓存，确保从干净状态开始
        ledger._invalidate_schema_cache()
        assert "intent_log" not in ledger._column_cache

        # 第一次调用：缓存未命中，应填充缓存
        result1 = ledger._table_has_column("intent_log", "request_id")
        assert result1 is True
        # 缓存已填充
        assert "intent_log" in ledger._column_cache
        cache_snapshot = frozenset(ledger._column_cache["intent_log"])

        # 第二次调用：缓存命中，结果一致
        result2 = ledger._table_has_column("intent_log", "request_id")
        assert result2 is True
        # 缓存内容未变化（说明没有重新查询 PRAGMA）
        assert frozenset(ledger._column_cache["intent_log"]) == cache_snapshot

    def test_cache_populated_after_first_call(self) -> None:
        ledger = _make_ledger()
        ledger._invalidate_schema_cache()

        # 调用前缓存为空
        assert "intent_log" not in ledger._column_cache

        ledger._table_has_column("intent_log", "ts_ms")

        # 调用后缓存已填充
        assert "intent_log" in ledger._column_cache
        assert "ts_ms" in ledger._column_cache["intent_log"]
        assert "request_id" in ledger._column_cache["intent_log"]


# ---------------------------------------------------------------------------
# T-PERF-AUDIT-02：迁移后缓存被清空
# ---------------------------------------------------------------------------

class TestInvalidateSchemaCacheAfterMigration:
    """T-PERF-AUDIT-02：迁移后缓存被清空（_invalidate_schema_cache）。"""

    def test_invalidate_specific_table(self) -> None:
        ledger = _make_ledger()

        # 先填充缓存
        ledger._table_has_column("intent_log", "request_id")
        ledger._table_has_column("governor_log", "decision")
        assert "intent_log" in ledger._column_cache
        assert "governor_log" in ledger._column_cache

        # 清空指定表的缓存
        ledger._invalidate_schema_cache("intent_log")
        assert "intent_log" not in ledger._column_cache
        # 其他表缓存不受影响
        assert "governor_log" in ledger._column_cache

    def test_invalidate_all_tables(self) -> None:
        ledger = _make_ledger()

        # 先填充多个表的缓存
        ledger._table_has_column("intent_log", "request_id")
        ledger._table_has_column("governor_log", "decision")
        ledger._table_has_column("skill_exec_log", "skill_name")
        assert len(ledger._column_cache) >= 3

        # 清空全部缓存
        ledger._invalidate_schema_cache()
        assert len(ledger._column_cache) == 0

    def test_after_invalidate_pragma_called_again(self) -> None:
        ledger = _make_ledger()

        # 第一次调用填充缓存
        ledger._table_has_column("intent_log", "request_id")
        assert "intent_log" in ledger._column_cache

        # 清空缓存
        ledger._invalidate_schema_cache("intent_log")
        assert "intent_log" not in ledger._column_cache

        # 缓存清空后再次调用，应重新填充缓存（验证缓存失效后重新查询）
        result = ledger._table_has_column("intent_log", "request_id")
        assert result is True
        # 缓存重新填充
        assert "intent_log" in ledger._column_cache
        assert "request_id" in ledger._column_cache["intent_log"]
