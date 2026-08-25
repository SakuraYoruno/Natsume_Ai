"""跨端事件时间线：Host/Edge 统一 request_id/session_id 关联视图。

提供：
- EdgeEventReport: Edge 端事件上报结构（event_type/ts_ms/request_id/session_id/payload）
- CrossTimelineAggregator: 合并 Host 审计事件 + Edge 上报事件，按 ts_ms 排序
- 统一时间线输出结构（timeline[]，含 source=host/edge 标记）
- 时间漂移容忍策略（Edge/Host 时钟不完全同步时的排序口径）
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    TIMELINE_CLOCK_DRIFT_DETECTED,
    TIMELINE_EDGE_EVENTS_MISSING,
    TIMELINE_SESSION_NOT_FOUND,
)

# ── 来源标识 ──
SOURCE_HOST = "host"
SOURCE_EDGE = "edge"

# ── Edge 事件类型（枚举常量） ──
EDGE_EVENT_ASR_CHUNK = "ASR_CHUNK"
EDGE_EVENT_SPEAK = "SPEAK"
EDGE_EVENT_BARGE_IN = "BARGE_IN"
EDGE_EVENT_RESOURCE_GATE = "VOICE_RESOURCE_GATE"
EDGE_EVENT_TTS_SYNTH = "TTS_SYNTH"
EDGE_EVENT_VTS_EXPRESSION = "VTS_EXPRESSION"
EDGE_EVENT_VTS_TALK_GATE = "VTS_TALK_GATE"
# M28-K（OPT-33）：情感事件类型
EDGE_EVENT_EMOTION_PEAK = "EMOTION_PEAK"
EDGE_EVENT_EMOTION_SHIFT = "EMOTION_SHIFT"

VALID_EDGE_EVENT_TYPES = frozenset({
    EDGE_EVENT_ASR_CHUNK,
    EDGE_EVENT_SPEAK,
    EDGE_EVENT_BARGE_IN,
    EDGE_EVENT_RESOURCE_GATE,
    EDGE_EVENT_TTS_SYNTH,
    EDGE_EVENT_VTS_EXPRESSION,
    EDGE_EVENT_VTS_TALK_GATE,
    # M28-K（OPT-33）：情感事件
    EDGE_EVENT_EMOTION_PEAK,
    EDGE_EVENT_EMOTION_SHIFT,
})

# ── 时间漂移容忍阈值（毫秒） ──
DEFAULT_CLOCK_DRIFT_TOLERANCE_MS = 5_000  # 5秒


@dataclass(frozen=True)
class EdgeEventReport:
    """Edge 端事件上报结构（标准化）。"""

    event_type: str
    ts_ms: int
    request_id: str
    session_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    direction: str = "EDGE_TO_HOST"
    seq: int | None = None
    status: str | None = None
    reason_code: str | None = None
    stage: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "ts_ms": self.ts_ms,
            "request_id": self.request_id,
            "session_id": self.session_id,
            "payload": dict(self.payload),
            "direction": self.direction,
            "seq": self.seq,
            "status": self.status,
            "reason_code": self.reason_code,
            "stage": self.stage,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class UnifiedTimelineEntry:
    """统一时间线中的单条事件。"""

    event_id: str
    source: str  # "host" | "edge"
    source_table: str  # 如 "intent_log", "voice_event_log", "edge_report" 等
    event_type: str | None
    stage: str | None
    status: str | None
    reason_code: str | None
    ts_ms: int
    detail: str | None
    request_id: str
    session_id: str | None = None
    clock_drift_flag: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "source": self.source,
            "source_table": self.source_table,
            "event_type": self.event_type,
            "stage": self.stage,
            "status": self.status,
            "reason_code": self.reason_code,
            "ts_ms": self.ts_ms,
            "detail": self.detail,
            "request_id": self.request_id,
            "session_id": self.session_id,
            "clock_drift_flag": self.clock_drift_flag,
        }


@dataclass
class CrossTimelineResult:
    """跨端时间线聚合结果。"""

    request_id: str
    session_id: str | None
    timeline: list[dict[str, Any]]
    host_event_count: int
    edge_event_count: int
    edge_missing: bool
    clock_drift_detected: bool
    clock_drift_max_ms: int
    warnings: list[str]
    status: str  # "COMPLETE" | "PARTIAL" | "HOST_ONLY"

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "timeline": list(self.timeline),
            "host_event_count": self.host_event_count,
            "edge_event_count": self.edge_event_count,
            "edge_missing": self.edge_missing,
            "clock_drift_detected": self.clock_drift_detected,
            "clock_drift_max_ms": self.clock_drift_max_ms,
            "warnings": list(self.warnings),
            "status": self.status,
        }


def parse_edge_event(raw: dict[str, Any]) -> EdgeEventReport | None:
    """解析 Edge 端上报的原始事件为标准化结构。无效事件返回 None。"""
    event_type = str(raw.get("event_type") or "").strip()
    ts_ms = raw.get("ts_ms")
    request_id = str(raw.get("request_id") or "").strip()
    session_id = str(raw.get("session_id") or "").strip()

    if not event_type or ts_ms is None or not request_id:
        return None

    payload = raw.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            payload = {"raw": payload}
    elif not isinstance(payload, dict):
        payload = {}

    return EdgeEventReport(
        event_type=event_type,
        ts_ms=int(ts_ms),
        request_id=request_id,
        session_id=session_id,
        payload=dict(payload),
        direction=str(raw.get("direction") or "EDGE_TO_HOST"),
        seq=raw.get("seq") if isinstance(raw.get("seq"), int) else None,
        status=str(raw.get("status") or "").strip() or None,
        reason_code=str(raw.get("reason_code") or "").strip() or None,
        stage=str(raw.get("stage") or "").strip() or None,
        detail=str(raw.get("detail") or "").strip() or None,
    )


class CrossTimelineAggregator:
    """Host/Edge 统一时间线聚合器。

    合并逻辑：
    1. 从 Host 审计 ledger 获取 request_id 关联的所有审计事件
    2. 接收 Edge 端上报事件列表
    3. 按 ts_ms 排序合并，标记 source=host/edge
    4. 检测时间漂移并在输出中标注
    """

    # Host 审计表到 source_table 的映射
    HOST_LOG_TABLES = (
        "intent_log",
        "governor_log",
        "skill_exec_log",
        "model_control_log",
        "channel_io_log",
        "avatar_action_log",
        "voice_event_log",
        "retrieval_event_log",
        "vision_event_log",
        "service_health_log",
        "error_log",
        "config_snapshot_log",
    )

    def __init__(
        self,
        *,
        ledger: SQLiteLedger,
        clock_drift_tolerance_ms: int = DEFAULT_CLOCK_DRIFT_TOLERANCE_MS,
    ) -> None:
        self.ledger = ledger
        self.clock_drift_tolerance_ms = int(clock_drift_tolerance_ms)

    @staticmethod
    def detect_emotion_events(
        events: list[dict[str, Any]],
        *,
        intensity_threshold: float = 0.8,
    ) -> list[dict[str, Any]]:
        """从事件列表中检测情感峰值和情感转变事件（M28-K OPT-33）。

        Args:
            events: 时间线事件列表（每个事件含 payload 字段）
            intensity_threshold: 情感强度阈值，超过此值生成 EMOTION_PEAK 事件

        Returns:
            新生成的情感事件列表（EMOTION_PEAK / EMOTION_SHIFT）
        """
        emotion_events: list[dict[str, Any]] = []
        prev_emotion_tag: str | None = None

        for event in events:
            payload = event.get("payload") or {}
            emotion_tag = payload.get("emotion_tag") or event.get("emotion_tag")
            emotion_intensity = float(payload.get("emotion_intensity") or
                                      event.get("emotion_intensity") or 0.0)
            ts_ms = int(event.get("ts_ms") or 0)

            # 检测情感峰值（intensity > threshold）
            if emotion_intensity > intensity_threshold and emotion_tag:
                emotion_events.append({
                    "event_type": EDGE_EVENT_EMOTION_PEAK,
                    "ts_ms": ts_ms,
                    "source": SOURCE_HOST,
                    "payload": {
                        "emotion_tag": emotion_tag,
                        "emotion_intensity": emotion_intensity,
                        "trigger_event_type": event.get("event_type"),
                    },
                })

            # 检测情感转变（emotion_tag 发生变化）
            if emotion_tag and prev_emotion_tag and emotion_tag != prev_emotion_tag:
                emotion_events.append({
                    "event_type": EDGE_EVENT_EMOTION_SHIFT,
                    "ts_ms": ts_ms,
                    "source": SOURCE_HOST,
                    "payload": {
                        "from_emotion": prev_emotion_tag,
                        "to_emotion": emotion_tag,
                        "trigger_event_type": event.get("event_type"),
                    },
                })

            if emotion_tag:
                prev_emotion_tag = emotion_tag

        return emotion_events

    def aggregate(
        self,
        *,
        request_id: str | None = None,
        session_id: str | None = None,
        edge_events: list[dict[str, Any]] | None = None,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> CrossTimelineResult:
        """按 request_id 或 session_id 聚合 Host+Edge 事件。

        参数：
            request_id: 按 request_id 关联（优先）
            session_id: 按 session_id 关联（当 request_id 为空时使用）
            edge_events: Edge 端上报的原始事件列表

        返回：
            CrossTimelineResult 包含合并时间线和元数据
        """
        req_id = str(request_id or "").strip()
        sess_id = str(session_id or "").strip()

        if not req_id and not sess_id:
            return CrossTimelineResult(
                request_id="",
                session_id=None,
                timeline=[],
                host_event_count=0,
                edge_event_count=0,
                edge_missing=True,
                clock_drift_detected=False,
                clock_drift_max_ms=0,
                warnings=[TIMELINE_SESSION_NOT_FOUND],
                status="HOST_ONLY",
            )

        # Step 1: 收集 Host 审计事件（支持时间窗口过滤）
        host_entries = self._collect_host_events(req_id, sess_id, start_ms=start_ms, end_ms=end_ms)

        # Step 2: 解析 Edge 上报事件
        edge_entries = self._collect_edge_events(edge_events or [], req_id, sess_id)

        # Step 3: 合并排序
        all_entries = host_entries + edge_entries
        all_entries.sort(key=lambda e: (e.ts_ms, e.source, e.event_id))

        # Step 4: 检测时间漂移
        drift_detected, drift_max_ms, drift_warnings = self._detect_clock_drift(host_entries, edge_entries)

        # Step 5: 构建输出
        edge_missing = len(edge_entries) == 0
        warnings: list[str] = list(drift_warnings)
        if edge_missing and (edge_events is None or len(edge_events) == 0):
            warnings.append(TIMELINE_EDGE_EVENTS_MISSING)

        if edge_missing:
            status = "HOST_ONLY"
        elif drift_detected:
            status = "PARTIAL"
        else:
            status = "COMPLETE"

        timeline_dicts = [e.to_dict() for e in all_entries]

        return CrossTimelineResult(
            request_id=req_id or (all_entries[0].request_id if all_entries else ""),
            session_id=sess_id or None,
            timeline=timeline_dicts,
            host_event_count=len(host_entries),
            edge_event_count=len(edge_entries),
            edge_missing=edge_missing,
            clock_drift_detected=drift_detected,
            clock_drift_max_ms=drift_max_ms,
            warnings=warnings,
            status=status,
        )

    def _collect_host_events(
        self,
        request_id: str,
        session_id: str,
        *,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> list[UnifiedTimelineEntry]:
        """从 Host 审计 ledger 收集事件。"""
        entries: list[UnifiedTimelineEntry] = []

        # 从 trace_request 获取核心三表
        if request_id:
            trace = self.ledger.trace_request(request_id)
            for table_name in ("intent_log", "governor_log", "skill_exec_log", "model_control_log"):
                rows = trace.get(table_name) or []
                entries.extend(self._host_rows_to_entries(rows, table_name, request_id))

        # 从各审计表按 request_id 或 session_id 补充
        # TASK-0078: 使用 fetch_by_time_range 替代全量加载，解决性能隐患
        supplementary_tables = (
            "channel_io_log",
            "avatar_action_log",
            "voice_event_log",
            "retrieval_event_log",
            "vision_event_log",
            "service_health_log",
            "error_log",
            "config_snapshot_log",
        )
        for table_name in supplementary_tables:
            # 使用时间窗口查询代替全量内存加载
            log_list = self.ledger.fetch_by_time_range(
                table_name,
                start_ms=start_ms,
                end_ms=end_ms,
                limit=10_000,
            )
            if request_id:
                rows = [
                    dict(row) for row in log_list
                    if str(row.get("request_id") or "") == request_id
                ]
            elif session_id:
                rows = [
                    dict(row) for row in log_list
                    if str(row.get("session_id") or "") == session_id
                ]
            else:
                rows = []
            entries.extend(self._host_rows_to_entries(rows, table_name, request_id))

        # 去重（按 source_table + ts_ms + detail 去重）
        seen: set[str] = set()
        unique: list[UnifiedTimelineEntry] = []
        for e in entries:
            key = f"{e.source_table}:{e.ts_ms}:{e.detail}:{e.status}"
            if key not in seen:
                seen.add(key)
                unique.append(e)

        return unique

    def _host_rows_to_entries(
        self,
        rows: list[dict[str, Any]],
        table_name: str,
        request_id: str,
    ) -> list[UnifiedTimelineEntry]:
        """将 Host 审计行转换为统一时间线条目。"""
        entries: list[UnifiedTimelineEntry] = []
        for idx, row in enumerate(rows, start=1):
            status = self._pick_status(row)
            detail = self._pick_detail(row)
            entries.append(
                UnifiedTimelineEntry(
                    event_id=f"host:{table_name}:{idx}",
                    source=SOURCE_HOST,
                    source_table=table_name,
                    event_type=str(row.get("event_type") or "").strip() or None,
                    stage=str(row.get("stage") or "").strip() or None,
                    status=status,
                    reason_code=str(row.get("reason_code") or "").strip() or None,
                    ts_ms=int(row.get("ts_ms") or 0),
                    detail=detail,
                    request_id=str(row.get("request_id") or request_id),
                    session_id=str(row.get("session_id") or "").strip() or None,
                )
            )
        return entries

    def _collect_edge_events(
        self,
        raw_events: list[dict[str, Any]],
        request_id: str,
        session_id: str,
    ) -> list[UnifiedTimelineEntry]:
        """解析并过滤 Edge 上报事件。"""
        entries: list[UnifiedTimelineEntry] = []
        for idx, raw in enumerate(raw_events, start=1):
            parsed = parse_edge_event(raw)
            if parsed is None:
                continue

            # 按 request_id 或 session_id 过滤
            if request_id and parsed.request_id != request_id:
                continue
            if not request_id and session_id and parsed.session_id != session_id:
                continue

            entries.append(
                UnifiedTimelineEntry(
                    event_id=f"edge:report:{idx}",
                    source=SOURCE_EDGE,
                    source_table="edge_report",
                    event_type=parsed.event_type,
                    stage=parsed.stage,
                    status=parsed.status,
                    reason_code=parsed.reason_code,
                    ts_ms=parsed.ts_ms,
                    detail=parsed.detail,
                    request_id=parsed.request_id,
                    session_id=parsed.session_id or None,
                )
            )
        return entries

    def _detect_clock_drift(
        self,
        host_entries: list[UnifiedTimelineEntry],
        edge_entries: list[UnifiedTimelineEntry],
    ) -> tuple[bool, int, list[str]]:
        """检测 Host 与 Edge 时间漂移。

        策略：比较相同 request_id 下 Host 和 Edge 的时间戳范围。
        如果 Edge 最早事件早于 Host 最早事件超过容忍阈值，
        或 Edge 最晚事件晚于 Host 最晚事件超过容忍阈值，则标记漂移。
        """
        if not host_entries or not edge_entries:
            return False, 0, []

        host_min_ts = min(e.ts_ms for e in host_entries)
        host_max_ts = max(e.ts_ms for e in host_entries)
        edge_min_ts = min(e.ts_ms for e in edge_entries)
        edge_max_ts = max(e.ts_ms for e in edge_entries)

        # 计算时间差异
        drift_candidates = [
            abs(edge_min_ts - host_min_ts),
            abs(edge_max_ts - host_max_ts),
        ]
        max_drift = max(drift_candidates)

        warnings: list[str] = []
        detected = max_drift > self.clock_drift_tolerance_ms

        if detected:
            warnings.append(
                f"{TIMELINE_CLOCK_DRIFT_DETECTED}: max_drift={max_drift}ms "
                f"exceeds tolerance={self.clock_drift_tolerance_ms}ms"
            )

        return detected, int(max_drift), warnings

    @staticmethod
    def _pick_status(row: dict[str, Any]) -> str | None:
        for key in ("status", "decision", "result_status"):
            value = str(row.get(key) or "").strip()
            if value:
                return value
        return None

    @staticmethod
    def _pick_detail(row: dict[str, Any]) -> str | None:
        for key in ("detail", "result_code", "event_type", "op_kind"):
            value = str(row.get(key) or "").strip()
            if value:
                return value
        return None


def build_cross_timeline(
    *,
    ledger: SQLiteLedger,
    request_id: str | None = None,
    session_id: str | None = None,
    edge_events: list[dict[str, Any]] | None = None,
    clock_drift_tolerance_ms: int = DEFAULT_CLOCK_DRIFT_TOLERANCE_MS,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> dict[str, Any]:
    """便捷入口：构建跨端统一时间线并返回可序列化的 dict。

    TASK-0078: 新增 start_ms/end_ms 时间窗口参数，避免全量内存加载。
    """
    aggregator = CrossTimelineAggregator(
        ledger=ledger,
        clock_drift_tolerance_ms=clock_drift_tolerance_ms,
    )
    result = aggregator.aggregate(
        request_id=request_id,
        session_id=session_id,
        edge_events=edge_events,
        start_ms=start_ms,
        end_ms=end_ms,
    )
    return result.to_dict()
