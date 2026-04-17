from unittest.mock import MagicMock

from host_brain.channels.wechat_health import (
    WECHAT_STATUS_DEGRADED,
    WECHAT_STATUS_UP,
    WeChatChannelHealth,
)


def test_wechat_channel_repeated_degrade_and_recovery_cycles_remain_stable() -> None:
    ledger = MagicMock()
    ledger.log_channel_io = MagicMock()
    health = WeChatChannelHealth(
        failure_threshold=2,
        outbound_rate_per_minute=100,
        ledger=ledger,
    )
    base_ts = 1_800_700_000_000

    for index in range(10):
        ts = base_ts + index * 100
        thread_key = f"wechat::user_{index:02d}::bot"

        health.record_send_failure(ts_ms=ts)
        health.record_send_failure(ts_ms=ts + 1)
        degraded = health.get_status()
        blocked = health.check_outbound_allowed(thread_key=thread_key, ts_ms=ts + 2)

        assert degraded.status == WECHAT_STATUS_DEGRADED
        assert degraded.outbound_blocked is True
        assert blocked.allowed is False

        health.record_recv(ts_ms=ts + 3)
        health.record_send_success(ts_ms=ts + 4)
        recovered = health.get_status()
        allowed = health.check_outbound_allowed(thread_key=thread_key, ts_ms=ts + 5)

        assert recovered.status == WECHAT_STATUS_UP
        assert recovered.error_count == 0
        assert recovered.outbound_blocked is False
        assert allowed.allowed is True

    assert ledger.log_channel_io.call_count == 10
    for call in ledger.log_channel_io.call_args_list:
        row = call.args[0]
        assert row["direction"] == "RECOVERY"
        assert row["status"] == "RECOVERED"
        assert row["stage"] == "S_CHANNEL_RECOVERY"


def test_wechat_channel_gc_cleans_stale_threads_after_repeated_activity() -> None:
    ledger = MagicMock()
    ledger.log_channel_io = MagicMock()
    health = WeChatChannelHealth(
        outbound_rate_per_minute=100,
        ledger=ledger,
        inactive_threshold_s=60,
    )
    base_ts = 1_800_700_100_000

    for index in range(20):
        assert health.check_outbound_allowed(
            thread_key=f"wechat::stale_{index:02d}::bot",
            ts_ms=base_ts,
        ).allowed is True

    recent_key = "wechat::recent::bot"
    assert health.check_outbound_allowed(thread_key=recent_key, ts_ms=base_ts + 59_000).allowed is True

    gc_result = health.gc_inactive_threads(ts_ms=base_ts + 61_000)
    recent_allowed = health.check_outbound_allowed(thread_key=recent_key, ts_ms=base_ts + 61_001)

    assert gc_result["cleaned_count"] == 20
    assert recent_key not in gc_result["cleaned_keys"]
    assert recent_allowed.allowed is True

    gc_logs = [call.args[0] for call in ledger.log_channel_io.call_args_list if call.args and call.args[0]["direction"] == "GC"]
    assert len(gc_logs) == 1
    assert gc_logs[0]["status"] == "GC_COMPLETED"
    assert "cleaned 20 inactive thread_keys" in gc_logs[0]["detail"]
