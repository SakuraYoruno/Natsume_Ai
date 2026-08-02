"""Soft-failure context manager + process-local counter.

历史设计目标（OPT-3, docs/_history/quality/OPTIMIZATION_PLAN_20260420.md §7）：
  统一"非致命降级"的业务路径处理方式，消除全仓散落的
  `except Exception: pass / return {}` 静默掩盖行为。

使用方式：

    from host_brain.core.soft_fail import soft_fail

    with soft_fail("memory_recall", fallback={"items": []}) as box:
        box[0] = do_memory_recall(...)
    memory = box[0]

行为：
  * 捕获 ``expected_exc``（默认 ``Exception``）后返回 ``fallback``；
  * ``logger.warning`` 输出带 ``stage`` 标签的结构化日志（含 exc_info）；
  * 进程内 Counter ``context_soft_failure_total{stage, exc_type}`` +1，
    供 ``observability.metrics.MetricsCollector`` 采样汇总到
    ``/obs/metrics``；
  * ``MemoryError``/``SystemExit``/``KeyboardInterrupt``/``GeneratorExit`` 永不吞；
  * **不抛异常**，保证业务链路可继续推进。

设计取舍：
  * 采用 list[T] 的单元素盒子而非 yield 返回值，是为了支持
    `with` 语句退出前捕获异常后仍能让调用方读到 fallback。
  * Counter 采用进程内 dict（thread-safe via GIL + lock），不依赖
    Prometheus，符合当前可观测栈。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, TypeVar

T = TypeVar("T")

_LOGGER_NAME = "host_brain.soft_fail"

# ---------------------------------------------------------------------------
# 进程内 SoftFailure 计数器（线程安全，GIL + 显式锁）
# ---------------------------------------------------------------------------

_COUNTER_LOCK = threading.Lock()
_counter: dict[tuple[str, str], int] = {}


def _emit_metric(stage: str, exc_type: str) -> None:
    """进程内累加 SoftFailure 计数；由 observability.metrics 定期读取。"""
    key = (stage, exc_type)
    with _COUNTER_LOCK:
        _counter[key] = _counter.get(key, 0) + 1


def snapshot_soft_failures() -> dict[str, dict[str, int]]:
    """导出当前计数快照，结构为 ``{stage: {exc_type: count}}``。

    观察侧（``MetricsCollector.collect``）调用此函数读取快照；调用过程
    不会清零计数，便于 /obs/metrics 展示累计值。
    """
    snapshot: dict[str, dict[str, int]] = {}
    with _COUNTER_LOCK:
        for (stage, exc_type), count in _counter.items():
            snapshot.setdefault(stage, {})[exc_type] = count
    return snapshot


def reset_soft_failures() -> None:
    """仅供测试使用，重置计数器。"""
    with _COUNTER_LOCK:
        _counter.clear()


# ---------------------------------------------------------------------------
# soft_fail 上下文管理器
# ---------------------------------------------------------------------------

# 致命异常永不吞。
_FATAL_EXC: tuple[type[BaseException], ...] = (
    MemoryError,
    SystemExit,
    KeyboardInterrupt,
    GeneratorExit,
)


@contextmanager
def soft_fail(
    stage: str,
    *,
    fallback: T,
    expected_exc: tuple[type[BaseException], ...] = (Exception,),
    logger_name: str = _LOGGER_NAME,
) -> Iterator[list[T]]:
    """统一的"非致命降级"上下文管理器。

    Parameters
    ----------
    stage
        业务阶段标签，必须是稳定的短标识（如 ``memory_recall``/``emotion_build``）。
        作为 metric label，请勿使用含 session_id 等高基数的字段。
    fallback
        失败时返回的降级值；必须是调用方可以继续使用的"空"结构。
    expected_exc
        被视为"可软降级"的异常类型，默认 ``Exception``。若需更保守，
        可传 ``(KeyError, ValueError)`` 等具体类型。
    logger_name
        日志 logger 名，默认 ``host_brain.soft_fail``；方便做按阶段过滤。

    Yields
    ------
    list[T]
        单元素 list（"盒子"）。块内把结果写入 ``box[0]``；块退出后调用方
        从 ``box[0]`` 取结果。若块内抛出 ``expected_exc``，``box[0]`` 保持
        为 ``fallback``。
    """
    logger = logging.getLogger(logger_name)
    box: list[T] = [fallback]
    try:
        yield box
    except _FATAL_EXC:
        # 致命异常必须继续传播，不走降级逻辑。
        raise
    except expected_exc as exc:  # noqa: BLE001 — 业务路径的软降级
        box[0] = fallback
        exc_type = type(exc).__name__
        logger.warning(
            "soft_fail stage=%s exc_type=%s exc=%r",
            stage,
            exc_type,
            exc,
            exc_info=True,
        )
        _emit_metric(stage=stage, exc_type=exc_type)
