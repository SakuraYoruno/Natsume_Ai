"""WebUI 数据聚合函数。

从 app/main.py 拆分，负责 WebUI 时间线/拓扑/请求搜索等聚合逻辑。
"""
from __future__ import annotations

import re
from typing import Any

def build_webui_timeline_aggregation(
    *,
    ledger: SQLiteLedger,
    start_time: int,
    end_time: int,
    interval: str = "1h",
    filters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """按时间区间聚合请求数/成功率/平均耗时，用于时间线图表。"""

    _filters = dict(filters or {})
    interval_ms = _parse_interval_ms(interval)

    # TASK-0078: 使用 fetch_by_time_range 替代全量加载，按时间窗口查询
    all_rows = [
        dict(row)
        for row in ledger.fetch_by_time_range(
            "intent_log",
            start_ms=start_time,
            end_ms=end_time,
            limit=10_000,
        )
    ]

    # 按状态/类型过滤
    if "status" in _filters:
        allowed = set(_filters["status"])
        all_rows = [r for r in all_rows if str(r.get("status") or r.get("decision") or "").upper() in allowed]
    if "request_type" in _filters:
        allowed = set(_filters["request_type"])
        all_rows = [r for r in all_rows if str(r.get("type") or "unknown") in allowed]

    # 构建区间桶
    intervals: list[dict[str, Any]] = []
    bucket_start = start_time
    while bucket_start < end_time:
        bucket_end = min(bucket_start + interval_ms, end_time)
        bucket_rows = [r for r in all_rows if bucket_start <= int(r.get("ts_ms") or 0) < bucket_end]
        success_count = sum(1 for r in bucket_rows if str(r.get("decision") or "").upper() in {"APPROVE", ""})
        total = len(bucket_rows)
        durations = [int(r.get("duration_ms") or 0) for r in bucket_rows if int(r.get("duration_ms") or 0) > 0]
        avg_dur = (sum(durations) / len(durations)) if durations else 0.0

        intervals.append({
            "start_time": bucket_start,
            "end_time": bucket_end,
            "total": total,
            "success": success_count,
            "failure": total - success_count,
            "success_rate": round(success_count / total, 4) if total else 0.0,
            "avg_duration": round(avg_dur, 2),
        })
        bucket_start = bucket_end

    total_all = len(all_rows)
    success_all = sum(iv["success"] for iv in intervals)
    dur_all = [int(r.get("duration_ms") or 0) for r in all_rows if int(r.get("duration_ms") or 0) > 0]

    return {
        "request_id": new_request_id(),
        "start_time": start_time,
        "end_time": end_time,
        "interval": interval,
        "intervals": intervals,
        "total_requests": total_all,
        "success_rate": round(success_all / total_all, 4) if total_all else 0.0,
        "avg_duration": round((sum(dur_all) / len(dur_all)) if dur_all else 0.0, 2),
        "timestamp": int(time.time() * 1000),
    }


def build_webui_status_distribution(
    *,
    ledger: SQLiteLedger,
    start_time: int,
    end_time: int,
    filters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """统计时间范围内各状态（APPROVE/REJECT/DEGRADED等）的分布。"""

    _filters = dict(filters or {})
    # TASK-0078: 使用 fetch_by_time_range 替代全量加载
    all_rows = [
        dict(row)
        for row in ledger.fetch_by_time_range(
            "intent_log",
            start_ms=start_time,
            end_ms=end_time,
            limit=10_000,
        )
    ]

    if "request_type" in _filters:
        allowed = set(_filters["request_type"])
        all_rows = [r for r in all_rows if str(r.get("type") or "unknown") in allowed]

    status_counts: dict[str, int] = {}
    for row in all_rows:
        st = str(row.get("decision") or row.get("status") or "UNKNOWN").upper()
        status_counts[st] = status_counts.get(st, 0) + 1

    total = len(all_rows)
    distribution: dict[str, float] = {}
    for st, cnt in status_counts.items():
        distribution[st] = round(cnt / total * 100, 2) if total else 0.0

    return {
        "request_id": new_request_id(),
        "start_time": start_time,
        "end_time": end_time,
        "status_counts": dict(sorted(status_counts.items())),
        "total_requests": total,
        "distribution_percentage": dict(sorted(distribution.items())),
        "timestamp": int(time.time() * 1000),
    }


def build_webui_request_detail(
    *,
    ledger: SQLiteLedger,
    request_id: str,
) -> dict[str, Any] | None:
    """获取单个请求的详情（含追踪数据），用于面板的请求详情视图。"""

    req_id = str(request_id or "").strip()
    if not req_id:
        return None

    try:
        trace = ledger.trace_request(req_id)
    except Exception:
        return None

    if not trace:
        return None

    intent_rows = trace.get("intent_log") or []
    governor_rows = trace.get("governor_log") or []

    # 若无任何 intent 或 governor 记录，视为请求不存在
    if not intent_rows and not governor_rows:
        return None

    ts_ms = int(intent_rows[0].get("ts_ms", 0)) if intent_rows else 0
    decision = str(governor_rows[0].get("decision", "UNKNOWN")).upper() if governor_rows else "UNKNOWN"
    reason_code = str(governor_rows[0].get("reason_code", "")).strip() if governor_rows else None
    duration = int(intent_rows[0].get("duration_ms", 0)) if intent_rows else 0
    req_type = str(intent_rows[0].get("type", "unknown")) if intent_rows else "unknown"

    return {
        "request_id": req_id,
        "timestamp": ts_ms,
        "status": decision,
        "duration": float(duration),
        "request_type": req_type,
        "user_id": str(intent_rows[0].get("user_id", "")).strip() or None if intent_rows else None,
        "error_message": reason_code if decision == "REJECT" else None,
        "trace_data": trace,
        "query_timestamp": int(time.time() * 1000),
    }


def build_webui_link_topology(
    *,
    ledger: SQLiteLedger,
    start_time: int,
    end_time: int,
    min_connections: int = 1,
) -> dict[str, Any]:
    """构建链路拓扑图（节点=审计源，边=同一 request_id 串联），用于拓扑可视化。"""

    # 收集所有审计源的记录
    source_names = [
        "intent_log", "governor_log", "skill_exec_log",
        "channel_io_log", "avatar_action_log", "voice_event_log",
        "retrieval_event_log", "vision_event_log", "service_health_log",
    ]

    # 收集 request_id -> 经过的源集合
    request_sources: dict[str, set[str]] = {}
    for source in source_names:
        rows = getattr(ledger, source, [])
        for row in rows:
            ts = int(row.get("ts_ms") or 0)
            if start_time <= ts <= end_time:
                rid = str(row.get("request_id") or "")
                if rid:
                    request_sources.setdefault(rid, set()).add(source)

    # 构建节点
    all_sources_used: set[str] = set()
    for sources in request_sources.values():
        all_sources_used.update(sources)

    nodes = [
        {"id": s, "label": s.replace("_log", "").replace("_", " ").title(), "type": "audit_source"}
        for s in sorted(all_sources_used)
    ]

    # 构建边（同一请求经过的两个审计源之间的关联）
    edge_counts: dict[tuple[str, str], int] = {}
    for sources in request_sources.values():
        sorted_sources = sorted(sources)
        for i in range(len(sorted_sources)):
            for j in range(i + 1, len(sorted_sources)):
                key = (sorted_sources[i], sorted_sources[j])
                edge_counts[key] = edge_counts.get(key, 0) + 1

    edges = [
        {"source": k[0], "target": k[1], "weight": v, "label": f"{v} requests"}
        for k, v in sorted(edge_counts.items())
        if v >= min_connections
    ]

    # 构建集群（按前缀分组）
    clusters: list[dict[str, Any]] = []
    cluster_map: dict[str, list[str]] = {}
    for s in all_sources_used:
        prefix = s.split("_")[0] if "_" in s else s
        cluster_map.setdefault(prefix, []).append(s)
    for prefix, members in sorted(cluster_map.items()):
        clusters.append({"id": prefix, "label": prefix.title(), "members": sorted(members)})

    return {
        "request_id": new_request_id(),
        "start_time": start_time,
        "end_time": end_time,
        "nodes": nodes,
        "edges": edges,
        "clusters": clusters,
        "timestamp": int(time.time() * 1000),
    }


def search_webui_requests(
    *,
    ledger: SQLiteLedger,
    query: str,
    start_time: int,
    end_time: int,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """按关键词搜索请求记录（request_id/reason_code/type 模糊匹配）。"""

    keyword = str(query or "").strip().lower()
    # TASK-0078: 使用 fetch_by_time_range 替代全量加载
    all_rows = [
        dict(row)
        for row in ledger.fetch_by_time_range(
            "intent_log",
            start_ms=start_time,
            end_ms=end_time,
            limit=10_000,
        )
    ]

    if keyword:
        matched = []
        for row in all_rows:
            searchable = " ".join(str(v) for v in row.values()).lower()
            if keyword in searchable:
                matched.append(row)
    else:
        matched = all_rows

    total = len(matched)
    offset = (page - 1) * page_size
    page_rows = matched[offset: offset + page_size]

    results = []
    for row in page_rows:
        results.append({
            "request_id": str(row.get("request_id") or ""),
            "timestamp": int(row.get("ts_ms") or 0),
            "status": str(row.get("decision") or row.get("status") or "UNKNOWN").upper(),
            "type": str(row.get("type") or "unknown"),
            "reason_code": str(row.get("reason_code") or "").strip() or None,
        })

    return {
        "results": results,
        "total": total,
    }


def _parse_interval_ms(interval: str) -> int:
    """将间隔字符串（1m/5m/15m/1h/1d）解析为毫秒。"""
    interval = str(interval).strip().lower()
    multipliers = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}
    for suffix, mult in multipliers.items():
        if interval.endswith(suffix):
            try:
                return int(interval[:-1]) * mult
            except ValueError:
                pass
    return 3_600_000  # 默认 1h


if __name__ == "__main__":
    report = build_startup_report(runtime_statuses={}, dependency_probes={})
    monitor_profile = build_agent_monitoring_profile()
    console_profile = build_runtime_console_profile(
        spawn_console=(str(os.environ.get("Natsume_RUNTIME_MONITOR_CONSOLE") or "").strip().lower() in {"1", "true", "yes", "on"})
    )
    emit_runtime_bootstrap_logs(
        startup_report=report,
        monitoring_profile=monitor_profile,
        console_profile=console_profile,
    )
    _main_logger = logging.getLogger(LOGGER_NAME_RUNTIME_MONITOR)
    _main_logger.info(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
