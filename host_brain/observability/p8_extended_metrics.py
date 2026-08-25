"""P8-1：扩展观测聚合模块。

只负责从 ledger 查询与聚合，不直接承担持久化。
持久化由 graph_runtime 在 run 完成后写入 p8_subgraph_failure_log / p8_graph_run_log。

提供四类聚合：
  1. subgraph_failure_metrics   — Intent 子图失败分布（SUBGRAPH_FAILURE_* 等）
  2. local_loop_failure_metrics — local loop 失败分布（LOCAL_LOOP_* 三码）
  3. intent_budget_breach_metrics — Intent 图 budget breach 聚合
  4. graph_latency_metrics       — 三图延迟分布（p50/p95，样本量 ≥ 10 才计算）

归类优先级（两者互斥，不重复计数）：
  error_code 属于 LOCAL_LOOP_* → 只计入 local_loop_failure_metrics
  其余 → 只计入 subgraph_failure_metrics
"""

from __future__ import annotations

import os
from typing import Any

from host_brain.integrations.graph_contract import is_budget_breach_failure_code

# P8 扩展观测开关（默认 false，开关关闭时 collect() 不追加 P8 字段）
_P8_EXTENDED_METRICS_ENV = "P8_EXTENDED_METRICS_ENABLED"

# 三图 execution_profile 枚举
_GRAPH_PROFILES = ("CHAT_FAST", "WORKFLOW_SUPERVISED", "INTENT_EXEC")

# p50/p95 计算最小样本量
_MIN_SAMPLE_FOR_PERCENTILE = 10


def is_p8_enabled() -> bool:
    """读取 P8_EXTENDED_METRICS_ENABLED 环境变量；默认 false。"""
    raw = str(os.environ.get(_P8_EXTENDED_METRICS_ENV, "")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _percentile(samples: list[int], pct: float) -> int | None:
    """计算分位数；样本量不足 _MIN_SAMPLE_FOR_PERCENTILE 时返回 None。"""
    if len(samples) < _MIN_SAMPLE_FOR_PERCENTILE:
        return None
    s = sorted(samples)
    idx = min(int(len(s) * pct), len(s) - 1)
    return s[idx]


class P8ExtendedMetricsCollector:
    """P8 扩展观测聚合器。

    从 SQLiteLedger 的 p8_subgraph_failure_log / p8_graph_run_log 查询聚合，
    不直接写入任何表。
    """

    def __init__(self, *, ledger: Any) -> None:
        self.ledger = ledger

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def collect_all(self) -> dict[str, Any]:
        """返回全部四类 P8 扩展观测指标。"""
        return {
            "subgraph_failure_metrics": self.collect_subgraph_failure_metrics(),
            "local_loop_failure_metrics": self.collect_local_loop_failure_metrics(),
            "intent_budget_breach_metrics": self.collect_intent_budget_breach_metrics(),
            "graph_latency_metrics": self.collect_graph_latency_metrics(),
        }

    def collect_subgraph_failure_metrics(self) -> dict[str, Any]:
        """3.1：subgraph_failure 分布可跨请求聚合。

        只统计 log_category='subgraph' 的行（LOCAL_LOOP_* 已在写入时归入 local_loop）。
        """
        rows = self._fetch_subgraph_failure_rows()
        subgraph_rows = [r for r in rows if r.get("log_category") == "subgraph"]

        subgraph_failure_by_code: dict[str, int] = {}
        for r in subgraph_rows:
            code = str(r.get("error_code") or "").strip()
            if code:
                subgraph_failure_by_code[code] = subgraph_failure_by_code.get(code, 0) + 1

        return {
            "subgraph_failure_count": len(subgraph_rows),
            "subgraph_failure_by_code": subgraph_failure_by_code,
        }

    def collect_local_loop_failure_metrics(self) -> dict[str, Any]:
        """3.2：local_loop failure 分布可跨请求聚合。

        只统计 log_category='local_loop' 的行，覆盖 P6 封板三码。
        """
        rows = self._fetch_subgraph_failure_rows()
        local_loop_rows = [r for r in rows if r.get("log_category") == "local_loop"]

        local_loop_failure_by_code: dict[str, int] = {}
        for r in local_loop_rows:
            code = str(r.get("error_code") or "").strip()
            if code:
                local_loop_failure_by_code[code] = local_loop_failure_by_code.get(code, 0) + 1

        return {
            "local_loop_failure_count": len(local_loop_rows),
            "local_loop_failure_by_code": local_loop_failure_by_code,
        }

    def collect_intent_budget_breach_metrics(self) -> dict[str, Any]:
        """3.3：Intent 图 budget breach 可跨请求聚合。

        从 p8_graph_run_log 中统计 budget_breach_code 非空的行。
        注意：不修改 P7 已有的 budget_breach_metrics（Supervisor 侧），独立上报。
        """
        rows = self._fetch_graph_run_rows()
        breach_rows = [
            r for r in rows
            if r.get("budget_breach_code")
            and is_budget_breach_failure_code(str(r.get("budget_breach_code")))
        ]

        intent_budget_breach_by_code: dict[str, int] = {}
        for r in breach_rows:
            code = str(r.get("budget_breach_code") or "").strip()
            if code:
                intent_budget_breach_by_code[code] = intent_budget_breach_by_code.get(code, 0) + 1

        return {
            "intent_budget_breach_count": len(breach_rows),
            "intent_budget_breach_by_code": intent_budget_breach_by_code,
        }

    def collect_graph_latency_metrics(self) -> dict[str, Any]:
        """3.4：三图延迟分布可统一聚合。

        测量口径（已写死）：
        - 统计 run_graph 总耗时（从调用入口到返回结果），单位毫秒
        - 按 execution_profile 分组，每个 profile 独立维护样本序列
        - p50/p95 计算要求样本量 ≥ 10；不足时置 null，sample_count 正常记录
        """
        rows = self._fetch_graph_run_rows()

        # 按 execution_profile 分组收集延迟样本
        latencies_by_profile: dict[str, list[int]] = {p: [] for p in _GRAPH_PROFILES}
        for r in rows:
            profile = str(r.get("execution_profile") or "").strip()
            lat = r.get("latency_ms")
            if profile in latencies_by_profile and isinstance(lat, (int, float)) and lat >= 0:
                latencies_by_profile[profile].append(int(lat))

        profiles: dict[str, Any] = {}
        for profile, lats in latencies_by_profile.items():
            profiles[profile] = {
                "p50_ms": _percentile(lats, 0.50),
                "p95_ms": _percentile(lats, 0.95),
                "sample_count": len(lats),
            }

        return {
            "profiles": profiles,
        }

    # ------------------------------------------------------------------
    # 内部查询辅助
    # ------------------------------------------------------------------

    def _fetch_subgraph_failure_rows(self) -> list[dict[str, Any]]:
        try:
            return list(getattr(self.ledger, "p8_subgraph_failure_log", None) or [])
        except Exception:
            return []

    def _fetch_graph_run_rows(self) -> list[dict[str, Any]]:
        try:
            return list(getattr(self.ledger, "p8_graph_run_log", None) or [])
        except Exception:
            return []
