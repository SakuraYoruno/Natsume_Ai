"""T-CHANNEL-GC-01~03 + T-VTS-STORE-01：WeChatChannelHealth GC + VTSMappingStore WARNING（TASK-0218 M25-B）。"""

from __future__ import annotations

import logging

import pytest

from host_brain.channels.wechat_health import WeChatChannelHealth
from host_brain.channels.vts_mapping_store import VTSMappingStore
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.core.ids import now_ms


# ---------------------------------------------------------------------------
# T-CHANNEL-GC-01：GC 后过期 thread_key 被清理
# ---------------------------------------------------------------------------

class TestGcInactiveThreadsCleaned:
    """T-CHANNEL-GC-01：GC 后过期 thread_key 被清理。"""

    def test_expired_thread_key_removed(self) -> None:
        health = WeChatChannelHealth(
            inactive_threshold_s=3600,  # 1 小时
        )
        base_ts = 1_700_000_000_000  # 固定基准时间戳

        # 模拟 thread_key 在 base_ts 时有活动
        health._outbound_counts["thread-old"] = [base_ts]
        health._thread_last_active_ms["thread-old"] = base_ts

        # GC 时间为 base_ts + 2 小时（超过 inactive_threshold_s）
        gc_ts = base_ts + 2 * 3600 * 1000
        result = health.gc_inactive_threads(ts_ms=gc_ts)

        assert result["cleaned_count"] == 1
        assert "thread-old" in result["cleaned_keys"]
        assert "thread-old" not in health._outbound_counts
        assert "thread-old" not in health._thread_last_active_ms


# ---------------------------------------------------------------------------
# T-CHANNEL-GC-02：活跃 thread_key 不被清理
# ---------------------------------------------------------------------------

class TestGcActiveThreadsPreserved:
    """T-CHANNEL-GC-02：活跃 thread_key 不被清理。"""

    def test_active_thread_key_preserved(self) -> None:
        health = WeChatChannelHealth(
            inactive_threshold_s=3600,  # 1 小时
        )
        base_ts = 1_700_000_000_000

        # 活跃 thread_key：最后活动在 30 分钟前（未超过阈值）
        active_ts = base_ts + 30 * 60 * 1000
        health._outbound_counts["thread-active"] = [active_ts]
        health._thread_last_active_ms["thread-active"] = active_ts

        # 过期 thread_key：最后活动在 2 小时前
        old_ts = base_ts
        health._outbound_counts["thread-old"] = [old_ts]
        health._thread_last_active_ms["thread-old"] = old_ts

        # GC 时间为 base_ts + 1.5 小时
        gc_ts = base_ts + int(1.5 * 3600 * 1000)
        result = health.gc_inactive_threads(ts_ms=gc_ts)

        assert result["cleaned_count"] == 1
        assert "thread-old" in result["cleaned_keys"]
        assert "thread-active" not in result["cleaned_keys"]
        # 活跃 thread_key 仍然存在
        assert "thread-active" in health._outbound_counts


# ---------------------------------------------------------------------------
# T-CHANNEL-GC-03：GC 写审计日志
# ---------------------------------------------------------------------------

class TestGcWritesAuditLog:
    """T-CHANNEL-GC-03：GC 写审计日志（通过 ledger.log_channel_io）。"""

    def test_gc_writes_channel_io_log(self) -> None:
        from host_brain.audit.sqlite_ledger import SQLiteLedger

        ledger = SQLiteLedger(db_path=":memory:", check_same_thread=False)
        health = WeChatChannelHealth(
            inactive_threshold_s=3600,
            ledger=ledger,
        )
        base_ts = 1_700_000_000_000

        # 添加过期 thread_key
        health._outbound_counts["thread-gc-test"] = [base_ts]
        health._thread_last_active_ms["thread-gc-test"] = base_ts

        gc_ts = base_ts + 2 * 3600 * 1000
        result = health.gc_inactive_threads(ts_ms=gc_ts)

        assert result["cleaned_count"] == 1

        # 验证审计日志已写入
        gc_logs = [
            row for row in ledger.channel_io_log
            if row.get("direction") == "GC"
        ]
        assert len(gc_logs) >= 1
        gc_log = gc_logs[0]
        assert gc_log["channel"] == "wechat"
        assert gc_log["status"] == "GC_COMPLETED"
        assert gc_log["stage"] == "S_CHANNEL_GC"


# ---------------------------------------------------------------------------
# T-VTS-STORE-01：db_path=":memory:" 时写 WARNING 日志
# ---------------------------------------------------------------------------

class TestVtsMappingStoreMemoryWarning:
    """T-VTS-STORE-01：db_path=':memory:' 时写 WARNING 日志（生产可观测）。"""

    def test_memory_db_emits_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="host_brain.channels.vts_mapping_store"):
            store = VTSMappingStore(db_path=None)  # None → ":memory:"
            store.close()

        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any(":memory:" in msg for msg in warning_messages), (
            f"期望包含 ':memory:' 的 WARNING 日志，实际日志：{warning_messages}"
        )

    def test_file_db_no_warning(self, caplog: pytest.LogCaptureFixture, tmp_path: pytest.TempPathFactory) -> None:
        db_file = str(tmp_path / "test_vts.db")
        migration = SQLiteMigrationRunner(db_path=db_file).apply_migrations(
            request_id="test-vts-file-warning",
            ts_ms=1_800_000_000_000,
        )
        assert migration.success is True, migration.as_dict()
        with caplog.at_level(logging.WARNING, logger="host_brain.channels.vts_mapping_store"):
            store = VTSMappingStore(db_path=db_file)
            store.close()

        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert not any(":memory:" in msg for msg in warning_messages), (
            f"文件路径不应触发 ':memory:' WARNING，实际日志：{warning_messages}"
        )
