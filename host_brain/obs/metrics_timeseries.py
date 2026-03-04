"""
MetricsTimeseriesService — M18-D
多维度时序数据聚合：请求量/拒绝率/延迟分位数/reason_code 分布。
FR-OBS-011 / TASK-0191
"""
from __future__ import annotations

import logging
import time
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

logger = logging.getLogger("Nastume.obs.metrics")

# 支持的 metric 类型
VALID_METRICS = frozenset([
    "request_count",
    "reject_rate",
    "latency_p50",
    "latency_p95",
    "reason_code_dist",
])

# 支持的 interval
VALID_INTERVALS = {"1m": 60, "5m": 300, "1h": 3600}

# 数据点上限
MAX_DATA_POINTS = 1000


class MetricsTimeseriesService:
    """
    从 SQLite Ledger 聚合多维度时序数据。
    支持：
    - 多 metric 批量查询
    - interval 聚合（1m/5m/1h）
    - 数据点上限 1000（超出自动降采样）
    - 无数据区间返回 null 值点（保证时序连续性）
    """

    def __init__(self, ledger):
        self._ledger = ledger

    def query(
        self,
        metrics: Optional[List[str]] = None,
        interval: str = "5m",
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        time_range: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        查询时序数据。
        :param metrics: 指标列表，默认全部
        :param interval: 聚合间隔 '1m'|'5m'|'1h'
        :param start_time: 起始时间戳（ms）
        :param end_time: 结束时间戳（ms）
        :param time_range: '1h'|'6h'|'24h'|'7d'（与 start/end_time 互斥）
        :return: {metrics: {metric_name: {data_points: [...]}}, summary, timestamp}
        """
        now_ms = int(time.time() * 1000)

        # 时间范围
        if start_time is None and time_range:
            _range_map = {"1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800}
            seconds = _range_map.get(time_range, 3600)
            start_time = now_ms - seconds * 1000
        if start_time is None:
            start_time = now_ms - 3600 * 1000
        if end_time is None:
            end_time = now_ms

        # interval
        interval_sec = VALID_INTERVALS.get(interval, 300)
        interval_ms  = interval_sec * 1000

        # 目标 metrics
        if not metrics:
            metrics = list(VALID_METRICS)
        metrics = [m for m in metrics if m in VALID_METRICS]

        # 构建时间桶
        buckets = self._build_buckets(start_time, end_time, interval_ms)

        # 拉取原始数据
        gov_rows   = self._safe_fetch("governor_log",      start_time, end_time)
        trace_rows = self._safe_fetch("graph_run_trace",   start_time, end_time)

        # 填充桶
        self._fill_buckets(buckets, gov_rows, trace_rows, interval_ms, start_time)

        # 降采样
        if len(buckets) > MAX_DATA_POINTS:
            buckets = self._downsample(buckets, MAX_DATA_POINTS)

        # 构建各 metric 的 data_points
        result_metrics: Dict[str, Any] = {}
        for m in metrics:
            result_metrics[m] = {"data_points": self._extract_metric(buckets, m)}

        # 汇总
        total_req = sum(b["requests"] for b in buckets)
        total_rej = sum(b["rejections"] for b in buckets)
        rej_rate  = round(total_rej / total_req * 100, 2) if total_req > 0 else 0.0

        # reason_code 分布（全局）
        rc_counter: Counter = Counter()
        for b in buckets:
            rc_counter.update(b.get("reason_codes", {}))
        reason_codes = [{"code": k, "count": v} for k, v in rc_counter.most_common(20)]

        # 延迟分布（全局）
        latency_data = self._compute_latency_dist(trace_rows)

        # 前端兼容格式（timeseries 字段）
        timestamps = [_fmt_ts(b["ts_ms"]) for b in buckets]
        timeseries = {
            "timestamps":     timestamps,
            "total":          [b["requests"]   for b in buckets],
            "chat":           [b.get("chat", 0) for b in buckets],
            "rejection_rate": [
                round(b["rejections"] / b["requests"] * 100, 1) if b["requests"] > 0 else 0.0
                for b in buckets
            ],
        }

        return {
            "metrics":      result_metrics,
            "timeseries":   timeseries,
            "reason_codes": reason_codes,
            "latency":      latency_data,
            "summary": {
                "total_requests":  total_req,
                "total_rejections": total_rej,
                "rejection_rate":  rej_rate,
                "interval":        interval,
                "data_points":     len(buckets),
            },
            "timestamp": now_ms,
        }

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------
    def _safe_fetch(self, table: str, start_ms: int, end_ms: int) -> List[Dict]:
        try:
            return self._ledger.fetch_by_time_range(table, start_ms, end_ms)
        except Exception as exc:
            logger.warning("MetricsTS: 读取 %s 失败 — %s", table, exc)
            return []

    @staticmethod
    def _build_buckets(start_ms: int, end_ms: int, interval_ms: int) -> List[Dict[str, Any]]:
        buckets = []
        ts = start_ms
        while ts < end_ms:
            buckets.append({
                "ts_ms":      ts,
                "requests":   0,
                "rejections": 0,
                "chat":       0,
                "latencies":  [],
                "reason_codes": Counter(),
            })
            ts += interval_ms
        if not buckets:
            buckets.append({
                "ts_ms": start_ms, "requests": 0, "rejections": 0,
                "chat": 0, "latencies": [], "reason_codes": Counter(),
            })
        return buckets

    @staticmethod
    def _bucket_idx(ts: int, start_ms: int, interval_ms: int, num: int) -> int:
        idx = int((ts - start_ms) / interval_ms)
        return max(0, min(idx, num - 1))

    def _fill_buckets(
        self,
        buckets: List[Dict],
        gov_rows: List[Dict],
        trace_rows: List[Dict],
        interval_ms: int,
        start_ms: int,
    ) -> None:
        n = len(buckets)
        for r in gov_rows:
            ts = r.get("ts_ms", 0)
            idx = self._bucket_idx(ts, start_ms, interval_ms, n)
            buckets[idx]["requests"] += 1
            if r.get("decision") != "APPROVE":
                buckets[idx]["rejections"] += 1
                rc = r.get("reason_code")
                if rc:
                    buckets[idx]["reason_codes"][rc] += 1
            if r.get("channel") == "voice":
                buckets[idx]["chat"] += 1

        for r in trace_rows:
            ts  = r.get("ts_ms", 0)
            lat = r.get("total_latency_ms")
            if lat is not None:
                idx = self._bucket_idx(ts, start_ms, interval_ms, n)
                buckets[idx]["latencies"].append(lat)

    @staticmethod
    def _downsample(buckets: List[Dict], max_points: int) -> List[Dict]:
        """等间隔降采样，合并相邻桶，确保结果 ≤ max_points。"""
        import math
        step = max(1, math.ceil(len(buckets) / max_points))
        result = []
        for i in range(0, len(buckets), step):
            group = buckets[i: i + step]
            merged: Dict[str, Any] = {
                "ts_ms":      group[0]["ts_ms"],
                "requests":   sum(b["requests"]   for b in group),
                "rejections": sum(b["rejections"] for b in group),
                "chat":       sum(b.get("chat", 0) for b in group),
                "latencies":  [lat for b in group for lat in b["latencies"]],
                "reason_codes": Counter(),
            }
            for b in group:
                merged["reason_codes"].update(b.get("reason_codes", {}))
            result.append(merged)
        return result

    @staticmethod
    def _extract_metric(buckets: List[Dict], metric: str) -> List[Optional[float]]:
        points = []
        for b in buckets:
            if metric == "request_count":
                points.append({"ts": b["ts_ms"], "value": b["requests"]})
            elif metric == "reject_rate":
                rate = round(b["rejections"] / b["requests"] * 100, 2) if b["requests"] > 0 else None
                points.append({"ts": b["ts_ms"], "value": rate})
            elif metric in ("latency_p50", "latency_p95"):
                lats = sorted(b["latencies"])
                if lats:
                    pct = 0.5 if metric == "latency_p50" else 0.95
                    idx = max(0, int(len(lats) * pct) - 1)
                    points.append({"ts": b["ts_ms"], "value": lats[idx]})
                else:
                    points.append({"ts": b["ts_ms"], "value": None})
            elif metric == "reason_code_dist":
                points.append({"ts": b["ts_ms"], "value": dict(b.get("reason_codes", {}))})
        return points

    @staticmethod
    def _compute_latency_dist(trace_rows: List[Dict]) -> List[Dict[str, Any]]:
        """按 node_type 聚合延迟分位数（用于前端柱状图）。"""
        by_type: Dict[str, List[int]] = defaultdict(list)
        for r in trace_rows:
            lat = r.get("total_latency_ms")
            if lat is not None:
                by_type["Overall"].append(lat)

        result = []
        for svc, lats in by_type.items():
            lats_sorted = sorted(lats)
            n = len(lats_sorted)
            p50 = lats_sorted[max(0, int(n * 0.5) - 1)] if n else 0
            p95 = lats_sorted[max(0, int(n * 0.95) - 1)] if n else 0
            result.append({"service": svc, "p50": p50, "p95": p95})
        return result


def _fmt_ts(ts_ms: int) -> str:
    import datetime
    dt = datetime.datetime.fromtimestamp(ts_ms / 1000)
    return dt.strftime("%H:%M")
