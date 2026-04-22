"""OPT-3: SoftFailure 统一降级 + 观测指标 契约测试。

验收要点（docs/35_quality/OPTIMIZATION_PLAN_20260420.md §7）：
  1. soft_fail 可导入且行为符合契约（捕获 expected_exc、返回 fallback、致命不吞）
  2. 进程内 Counter 正确累加 stage × exc_type
  3. MetricsCollector.collect() 暴露 soft_failure_metrics 段落
  4. build_runtime_mode_flags 的业务段降级纳入观测
"""

from __future__ import annotations

import logging

import pytest

from host_brain.core.soft_fail import (
    reset_soft_failures,
    snapshot_soft_failures,
    soft_fail,
)


@pytest.fixture(autouse=True)
def _reset_counter() -> None:
    """每个测试用例前重置 SoftFailure 计数器，避免跨测试污染。"""
    reset_soft_failures()
    yield
    reset_soft_failures()


# ---------------------------------------------------------------------------
# 契约 1：soft_fail 基础行为
# ---------------------------------------------------------------------------


class TestSoftFailContract:
    def test_success_path_writes_box(self) -> None:
        with soft_fail("test.success", fallback="FALLBACK") as box:
            box[0] = "OK"
        assert box[0] == "OK"
        assert snapshot_soft_failures() == {}

    def test_failure_path_returns_fallback_and_counts(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING, logger="host_brain.soft_fail")
        with soft_fail("test.failure", fallback={"items": []}) as box:
            raise ValueError("boom")
        assert box[0] == {"items": []}
        snap = snapshot_soft_failures()
        assert snap == {"test.failure": {"ValueError": 1}}
        # 日志必须带 stage 与 exc_type
        assert any("stage=test.failure" in rec.message for rec in caplog.records)
        assert any("exc_type=ValueError" in rec.message for rec in caplog.records)

    def test_counter_accumulates_across_calls(self) -> None:
        for _ in range(3):
            with soft_fail("test.loop", fallback=None):
                raise KeyError("k")
        with soft_fail("test.loop", fallback=None):
            raise ValueError("v")
        snap = snapshot_soft_failures()
        assert snap == {"test.loop": {"KeyError": 3, "ValueError": 1}}

    def test_expected_exc_filter_lets_unexpected_propagate(self) -> None:
        with pytest.raises(RuntimeError):
            with soft_fail(
                "test.filter",
                fallback=None,
                expected_exc=(KeyError,),
            ):
                raise RuntimeError("not caught")
        # 未捕获的异常不应计数
        assert snapshot_soft_failures() == {}

    @pytest.mark.parametrize("exc_cls", [MemoryError, KeyboardInterrupt, SystemExit, GeneratorExit])
    def test_fatal_exceptions_never_swallowed(self, exc_cls: type[BaseException]) -> None:
        with pytest.raises(exc_cls):
            with soft_fail("test.fatal", fallback=None):
                raise exc_cls()
        assert snapshot_soft_failures() == {}


# ---------------------------------------------------------------------------
# 契约 2：MetricsCollector 暴露 soft_failure_metrics
# ---------------------------------------------------------------------------


class TestMetricsCollectorSoftFailureExposure:
    def test_collect_exposes_soft_failure_metrics(self) -> None:
        from host_brain.observability.metrics import MetricsCollector

        # 注入 3 条典型 SoftFailure
        with soft_fail("memory_recall", fallback={}):
            raise KeyError("user_id")
        with soft_fail("memory_recall", fallback={}):
            raise KeyError("user_id")
        with soft_fail("emotion_build", fallback=None):
            raise ValueError("bad shape")

        # 用最小化的 fake ledger，避免依赖真实 sqlite
        class _FakeLedger:
            governor_log: list = []
            retrieval_event_log: list = []
            supervisor_decision_log: list = []
            governor_decision_log: list = []

            def __getattr__(self, item: str):
                return []

        collector = MetricsCollector(ledger=_FakeLedger())  # type: ignore[arg-type]
        payload = collector.collect()

        assert "soft_failure_metrics" in payload
        sfm = payload["soft_failure_metrics"]
        assert sfm["total"] == 3
        assert sfm["by_stage"] == {"memory_recall": 2, "emotion_build": 1}
        assert sfm["by_stage_exc_type"] == {
            "memory_recall": {"KeyError": 2},
            "emotion_build": {"ValueError": 1},
        }
        # Top 5：memory_recall 排在第一
        assert sfm["top_stages"][0] == {"stage": "memory_recall", "count": 2}

    def test_collect_with_no_soft_failures_returns_empty_structure(self) -> None:
        from host_brain.observability.metrics import MetricsCollector

        class _FakeLedger:
            def __getattr__(self, item: str):
                return []

        collector = MetricsCollector(ledger=_FakeLedger())  # type: ignore[arg-type]
        payload = collector.collect()
        sfm = payload["soft_failure_metrics"]
        assert sfm == {
            "total": 0,
            "by_stage": {},
            "by_stage_exc_type": {},
            "top_stages": [],
        }


# ---------------------------------------------------------------------------
# 契约 3：chat_generation_payload 业务段改造生效
# ---------------------------------------------------------------------------


class TestChatContextBuilderSoftFailIntegration:
    def test_runtime_mode_flags_soft_fails_on_activity_monitor_crash(self) -> None:
        """当 activity_monitor.get_status 抛异常时，runtime_flags 必须降级为
        仅含 sys_mode，且 SoftFailure 计数器记录 stage=chat.runtime_mode.activity_monitor。
        """
        from host_brain.app import dependencies as _deps
        from host_brain.app.chat_generation_payload import build_runtime_mode_flags

        class _BrokenMonitor:
            def get_status(self):  # noqa: D401, ANN001
                raise RuntimeError("monitor down")

        original = getattr(_deps, "_activity_monitor_ref", None)
        try:
            _deps._activity_monitor_ref = _BrokenMonitor()
            flags = build_runtime_mode_flags({})
        finally:
            _deps._activity_monitor_ref = original

        assert flags["sys_mode"] == "normal"
        assert "activity_monitor" not in flags  # 降级为无该字段
        snap = snapshot_soft_failures()
        assert "chat.runtime_mode.activity_monitor" in snap
        assert snap["chat.runtime_mode.activity_monitor"].get("RuntimeError") == 1

    def test_runtime_mode_flags_happy_path_no_soft_failure(self) -> None:
        """正常路径不应产生 SoftFailure。"""
        from host_brain.app import dependencies as _deps
        from host_brain.app.chat_generation_payload import build_runtime_mode_flags

        class _HealthyMonitor:
            def get_status(self):  # noqa: D401, ANN001
                return {"cpu": 0.1, "mem": 0.2}

        original = getattr(_deps, "_activity_monitor_ref", None)
        try:
            _deps._activity_monitor_ref = _HealthyMonitor()
            flags = build_runtime_mode_flags({})
        finally:
            _deps._activity_monitor_ref = original

        assert flags.get("activity_monitor") == {"cpu": 0.1, "mem": 0.2}
        assert snapshot_soft_failures() == {}
