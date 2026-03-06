"""request_id 单键回放报告（M43-D）。

基于 build_request_observability_report() 的结果，生成结构化的单键回放报告，
包含时间线摘要、关键节点状态、异常链路标注。

用途：
  - 生产演练：快速定位某个 request_id 的完整执行链路
  - 回归测试：对比两次执行的关键事件是否一致
  - 审计追踪：生成人类可读的回放报告
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from host_brain.core.ids import now_ms


@dataclass(frozen=True)
class ReplayReport:
    """request_id 单键回放报告（不可变）。

    Attributes:
        request_id: 被回放的请求 ID
        ts_ms: 报告生成时间戳（毫秒）
        trace_status: 追踪状态（来自 observability_report["trace_status"]）
        timeline_total: 时间线事件总数
        key_events: 关键事件列表（intent_log + governor_log 的关键节点）
        anomalies: 异常事件列表（REJECT 决策 + error_log 中的异常）
        source_summary: 各审计源的事件数量摘要
        report_hash: 报告内容的 SHA256 哈希（不含本字段）
    """

    request_id: str
    ts_ms: int
    trace_status: str | None
    timeline_total: int
    key_events: list[dict[str, Any]]
    anomalies: list[dict[str, Any]]
    source_summary: dict[str, int]
    report_hash: str


def build_replay_report(
    observability_report: dict[str, Any],
    *,
    ts_ms: int | None = None,
) -> ReplayReport:
    """从可观测性报告构建回放报告。

    Args:
        observability_report: build_request_observability_report() 的返回值
        ts_ms: 报告生成时间戳（可选，默认当前时间）

    Returns:
        ReplayReport（不可变）
    """
    now_value = int(ts_ms if ts_ms is not None else now_ms())
    request_id = str(observability_report.get("request_id") or "").strip()
    trace_status = observability_report.get("trace_status")
    timeline_total = int(observability_report.get("timeline_total") or 0)
    timeline = list(observability_report.get("timeline") or [])
    source_summary = dict(observability_report.get("sources") or {})

    # 提取关键事件（intent_log + governor_log 的关键节点）
    key_events = _extract_key_events(timeline)

    # 提取异常事件（REJECT 决策 + error_log 中的异常）
    anomalies = _extract_anomalies(timeline)

    # 构建报告内容（不含 report_hash）
    content: dict[str, Any] = {
        "request_id": request_id,
        "ts_ms": now_value,
        "trace_status": trace_status,
        "timeline_total": timeline_total,
        "key_events": key_events,
        "anomalies": anomalies,
        "source_summary": source_summary,
    }

    report_hash = _sha256_dict(content)

    return ReplayReport(
        request_id=request_id,
        ts_ms=now_value,
        trace_status=str(trace_status) if trace_status is not None else None,
        timeline_total=timeline_total,
        key_events=key_events,
        anomalies=anomalies,
        source_summary=source_summary,
        report_hash=report_hash,
    )


def format_replay_report(report: ReplayReport) -> str:
    """格式化为人类可读的文本报告。

    Args:
        report: ReplayReport 实例

    Returns:
        格式化的文本报告字符串
    """
    lines: list[str] = [
        "=" * 60,
        f"回放报告 — request_id: {report.request_id}",
        "=" * 60,
        f"生成时间: {report.ts_ms} ms",
        f"追踪状态: {report.trace_status or 'UNKNOWN'}",
        f"时间线事件总数: {report.timeline_total}",
        "",
        "--- 审计源摘要 ---",
    ]

    for source, count in sorted(report.source_summary.items()):
        lines.append(f"  {source}: {count} 条")

    if report.key_events:
        lines.append("")
        lines.append("--- 关键事件 ---")
        for event in report.key_events:
            ts = event.get("ts_ms", 0)
            source = event.get("source", "")
            status = event.get("status", "")
            detail = event.get("detail", "")
            lines.append(f"  [{ts}] [{source}] {status} — {detail}")

    if report.anomalies:
        lines.append("")
        lines.append("--- 异常事件 ---")
        for anomaly in report.anomalies:
            ts = anomaly.get("ts_ms", 0)
            source = anomaly.get("source", "")
            status = anomaly.get("status", "")
            reason = anomaly.get("reason_code", "")
            lines.append(f"  [{ts}] [{source}] {status} — {reason}")
    else:
        lines.append("")
        lines.append("--- 无异常事件 ---")

    lines.append("")
    lines.append(f"报告哈希: {report.report_hash[:16]}...")
    lines.append("=" * 60)

    return "\n".join(lines)


def _extract_key_events(timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从时间线中提取关键事件（intent_log + governor_log）。"""
    key_sources = {"intent_log", "governor_log", "skill_exec_log"}
    events: list[dict[str, Any]] = []
    for row in timeline:
        source = str(row.get("source") or "")
        if source not in key_sources:
            continue
        events.append({
            "event_id": row.get("event_id"),
            "source": source,
            "status": row.get("status"),
            "reason_code": row.get("reason_code"),
            "intent_id": row.get("intent_id"),
            "ts_ms": int(row.get("ts_ms") or 0),
            "detail": row.get("detail"),
        })
    return events


def _extract_anomalies(timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从时间线中提取异常事件（REJECT 决策 + error_log）。"""
    anomalies: list[dict[str, Any]] = []
    for row in timeline:
        source = str(row.get("source") or "")
        status = str(row.get("status") or "").upper()
        # error_log 中的所有事件都是异常
        if source == "error_log":
            anomalies.append({
                "event_id": row.get("event_id"),
                "source": source,
                "status": status,
                "reason_code": row.get("reason_code"),
                "intent_id": row.get("intent_id"),
                "ts_ms": int(row.get("ts_ms") or 0),
                "detail": row.get("detail"),
            })
            continue
        # governor_log 中的 REJECT 决策
        if source == "governor_log" and status == "REJECT":
            anomalies.append({
                "event_id": row.get("event_id"),
                "source": source,
                "status": status,
                "reason_code": row.get("reason_code"),
                "intent_id": row.get("intent_id"),
                "ts_ms": int(row.get("ts_ms") or 0),
                "detail": row.get("detail"),
            })
    return anomalies


def _sha256_dict(data: dict[str, Any]) -> str:
    """计算字典内容的 SHA256 哈希（JSON 序列化，key 排序）。"""
    serialized = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
