"""OBS metrics collector: aggregates governor stats, retrieval latency, and recent chains.

Supports dev/prod mode via OBS_MODE env var:
  - dev:  includes debug_trace (intent_draft, rules_hit, skill_params)
  - prod: debug_trace is None
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import PERF_BASELINE_STALE
from host_brain.core.ids import now_ms
from host_brain.integrations.graph_contract import is_budget_breach_failure_code
from host_brain.observability.p8_extended_metrics import (
    P8ExtendedMetricsCollector,
    is_p8_enabled,
)


class ObsMode(str, Enum):
    DEV = "dev"
    PROD = "prod"


def get_obs_mode() -> ObsMode:
    """Read OBS_MODE env var; default to prod."""
    raw = os.environ.get("OBS_MODE", "prod").strip().lower()
    if raw == "dev":
        return ObsMode.DEV
    return ObsMode.PROD


def _parse_threshold_pct(env_name: str, default: float) -> float:
    raw = str(os.environ.get(env_name, "")).strip()
    if not raw:
        return float(default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return float(default)
    if value < 0:
        return 0.0
    if value > 100:
        return 100.0
    return float(value)


def _rate_pct(count: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((float(count) / float(total)) * 100.0, 2)


def _contains_timeout_marker(*, status: Any, reason_code: Any, detail: Any) -> bool:
    status_text = str(status or "").strip().upper()
    reason_text = str(reason_code or "").strip().upper()
    detail_text = str(detail or "").strip().upper()
    return (
        ("TIMEOUT" in status_text)
        or ("TIMEOUT" in reason_text)
        or ("TIMEOUT" in detail_text)
    )


def _is_failure_status(status: Any) -> bool:
    normalized = str(status or "").strip().upper()
    return normalized in {"FAILED", "ERROR", "REJECTED", "TIMEOUT"}


class MetricsCollector:
    """Aggregates observability metrics from SQLiteLedger for /obs/metrics endpoint."""

    def __init__(self, *, ledger: SQLiteLedger) -> None:
        self.ledger = ledger
        self._runtime_chat_requests = 0
        self._runtime_rejections = 0
        self._runtime_errors = 0
        self._runtime_reason_code_counts: dict[str, int] = {}

    def inc_chat(self) -> None:
        self._runtime_chat_requests += 1

    def inc_rejection(self, reason_code: str) -> None:
        self._runtime_rejections += 1
        normalized = str(reason_code or "UNKNOWN").strip() or "UNKNOWN"
        self._runtime_reason_code_counts[normalized] = self._runtime_reason_code_counts.get(normalized, 0) + 1

    def inc_error(self) -> None:
        self._runtime_errors += 1

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def collect(
        self,
        *,
        request_id: str | None = None,
        obs_mode: ObsMode | None = None,
        recent_chains_n: int = 5,
        ts_ms: int | None = None,
    ) -> dict[str, Any]:
        """Collect and return the full /obs/metrics payload."""
        mode = obs_mode if obs_mode is not None else get_obs_mode()
        collected_ts = int(ts_ms if ts_ms is not None else now_ms())
        alert_thresholds = self._resolve_alert_thresholds()

        governor_stats = self._query_governor_stats(alert_thresholds=alert_thresholds)
        retrieval_stats = self._query_retrieval_stats(alert_thresholds=alert_thresholds)
        snapshot_staleness = self._query_snapshot_staleness()
        recent_chains = self._query_recent_chains(recent_chains_n)
        latency_baseline = self.collect_latency_baseline(ts_ms=collected_ts)
        # M29-Q（OPT-?）：情感维度指标
        emotion_metrics = self._query_emotion_metrics()
        # M-MA4：多 agent 专项指标
        agent_metrics = self._query_agent_metrics()
        # P5-4：Supervisor 决策观测（rule/llm/llm_fallback_rule、failure_code、selected_agent）
        supervisor_metrics = self._query_supervisor_metrics()
        # P7-3：预算突破观测（从 supervisor_decision_log 等聚合 budget breach 类失败码）
        budget_breach_metrics = self._query_budget_breach_metrics()

        # P8-1：扩展观测聚合（开关控制，默认关闭）
        p8_metrics: dict[str, Any] | None = None
        if is_p8_enabled():
            p8_collector = P8ExtendedMetricsCollector(ledger=self.ledger)
            p8_metrics = p8_collector.collect_all()

        payload: dict[str, Any] = {
            "request_id": request_id,
            "ts_ms": collected_ts,
            "obs_mode": mode.value,
            "governor_decisions": governor_stats,
            "retrieval_latency": retrieval_stats,
            "hard_snapshot_staleness_ms": snapshot_staleness,
            "recent_chains": recent_chains,
            "latency_baseline": latency_baseline,
            # M29-Q：情感维度指标
            "emotion_metrics": emotion_metrics,
            # M-MA4：多 agent 专项指标
            "agent_metrics": agent_metrics,
            # P5-4：Supervisor 决策观测
            "supervisor_metrics": supervisor_metrics,
            # P7-3：预算突破观测（超时/超步数等）
            "budget_breach_metrics": budget_breach_metrics,
            "debug_trace": None,
        }

        # P8-1：开关开启时追加扩展观测字段（不修改已有字段）
        if p8_metrics is not None:
            payload["subgraph_failure_metrics"] = p8_metrics["subgraph_failure_metrics"]
            payload["local_loop_failure_metrics"] = p8_metrics["local_loop_failure_metrics"]
            payload["intent_budget_breach_metrics"] = p8_metrics["intent_budget_breach_metrics"]
            payload["graph_latency_metrics"] = p8_metrics["graph_latency_metrics"]

        if mode == ObsMode.DEV and request_id:
            payload["debug_trace"] = self._build_debug_trace(request_id)

        return payload

    # ------------------------------------------------------------------
    # Internal query helpers
    # ------------------------------------------------------------------

    def _resolve_alert_thresholds(self) -> dict[str, float]:
        return {
            "timeout_rate_pct": _parse_threshold_pct("OBS_ALERT_TIMEOUT_RATE_PCT", 10.0),
            "degraded_rate_pct": _parse_threshold_pct("OBS_ALERT_DEGRADED_RATE_PCT", 20.0),
            "failure_rate_pct": _parse_threshold_pct("OBS_ALERT_FAILURE_RATE_PCT", 15.0),
        }

    def _query_governor_stats(self, *, alert_thresholds: dict[str, float]) -> dict[str, Any]:
        """Count governor decisions from governor_log."""
        try:
            rows = self.ledger.governor_log
        except Exception:
            rows = []

        total = len(rows)
        approved = sum(1 for r in rows if r.get("decision") == "APPROVE")
        rejected = sum(1 for r in rows if r.get("decision") == "REJECT")
        degraded = sum(1 for r in rows if r.get("decision") == "DEGRADED")
        failure = rejected + degraded
        degraded_rate_pct = _rate_pct(degraded, total)
        failure_rate_pct = _rate_pct(failure, total)

        # 伪执行拦截：reason_code 含 AIRLOCK 或 SAFETY 的 REJECT
        pseudo_exec_blocked = sum(
            1 for r in rows
            if r.get("decision") == "REJECT"
            and r.get("reason_code") in {"AIRLOCK_BLOCKED", "SAFETY_BLOCKED"}
        )
        active_alerts: list[dict[str, Any]] = []
        if total > 0 and degraded_rate_pct >= float(alert_thresholds["degraded_rate_pct"]):
            active_alerts.append(
                {
                    "code": "GOVERNOR_DEGRADED_RATE_HIGH",
                    "severity": "WARN",
                    "observed_rate_pct": degraded_rate_pct,
                    "threshold_rate_pct": float(alert_thresholds["degraded_rate_pct"]),
                }
            )
        if total > 0 and failure_rate_pct >= float(alert_thresholds["failure_rate_pct"]):
            active_alerts.append(
                {
                    "code": "GOVERNOR_FAILURE_RATE_HIGH",
                    "severity": "WARN",
                    "observed_rate_pct": failure_rate_pct,
                    "threshold_rate_pct": float(alert_thresholds["failure_rate_pct"]),
                }
            )

        return {
            "total": total,
            "approved": approved,
            "rejected": rejected,
            "degraded": degraded,
            "pseudo_exec_blocked": pseudo_exec_blocked,
            "degraded_rate_pct": degraded_rate_pct,
            "failure_rate_pct": failure_rate_pct,
            "alerting": {
                "window_size": total,
                "thresholds": {
                    "degraded_rate_pct": float(alert_thresholds["degraded_rate_pct"]),
                    "failure_rate_pct": float(alert_thresholds["failure_rate_pct"]),
                },
                "active_alerts": active_alerts,
            },
        }

    def _query_retrieval_stats(self, *, alert_thresholds: dict[str, float]) -> dict[str, Any]:
        """Compute p50/p95 latency from retrieval_event_log."""
        try:
            rows = self.ledger.retrieval_event_log
        except Exception:
            rows = []

        latencies: list[int] = []
        timeout_count = 0
        degraded_count = 0
        failure_count = 0
        for r in rows:
            status_value = r.get("status")
            reason_code_value = r.get("reason_code")
            detail_value = r.get("detail")
            payload = r.get("payload") or {}
            row_degraded = False
            if isinstance(payload, dict):
                lat = payload.get("latency_ms")
                if isinstance(lat, (int, float)) and lat >= 0:
                    latencies.append(int(lat))
                if bool(payload.get("degraded")):
                    row_degraded = True
            if str(status_value or "").strip().upper() == "DEGRADED":
                row_degraded = True
            if row_degraded:
                degraded_count += 1
            if _contains_timeout_marker(
                status=status_value,
                reason_code=reason_code_value,
                detail=detail_value,
            ):
                timeout_count += 1
            if _is_failure_status(status_value):
                failure_count += 1

        total_events = len(rows)
        timeout_rate_pct = _rate_pct(timeout_count, total_events)
        degraded_rate_pct = _rate_pct(degraded_count, total_events)
        failure_rate_pct = _rate_pct(failure_count, total_events)
        active_alerts: list[dict[str, Any]] = []
        if total_events > 0 and timeout_rate_pct >= float(alert_thresholds["timeout_rate_pct"]):
            active_alerts.append(
                {
                    "code": "RETRIEVAL_TIMEOUT_RATE_HIGH",
                    "severity": "WARN",
                    "observed_rate_pct": timeout_rate_pct,
                    "threshold_rate_pct": float(alert_thresholds["timeout_rate_pct"]),
                }
            )
        if total_events > 0 and degraded_rate_pct >= float(alert_thresholds["degraded_rate_pct"]):
            active_alerts.append(
                {
                    "code": "RETRIEVAL_DEGRADED_RATE_HIGH",
                    "severity": "WARN",
                    "observed_rate_pct": degraded_rate_pct,
                    "threshold_rate_pct": float(alert_thresholds["degraded_rate_pct"]),
                }
            )
        if total_events > 0 and failure_rate_pct >= float(alert_thresholds["failure_rate_pct"]):
            active_alerts.append(
                {
                    "code": "RETRIEVAL_FAILURE_RATE_HIGH",
                    "severity": "WARN",
                    "observed_rate_pct": failure_rate_pct,
                    "threshold_rate_pct": float(alert_thresholds["failure_rate_pct"]),
                }
            )

        p50: int | None = None
        p95: int | None = None
        if latencies:
            sorted_lats = sorted(latencies)
            n = len(sorted_lats)
            p50 = sorted_lats[int(n * 0.50)]
            p95 = sorted_lats[min(int(n * 0.95), n - 1)]

        return {
            "p50_ms": p50,
            "p95_ms": p95,
            "sample_count": len(latencies),
            "event_count": total_events,
            "timeout_count": timeout_count,
            "degraded_count": degraded_count,
            "failure_count": failure_count,
            "timeout_rate_pct": timeout_rate_pct,
            "degraded_rate_pct": degraded_rate_pct,
            "failure_rate_pct": failure_rate_pct,
            "alerting": {
                "window_size": total_events,
                "thresholds": {
                    "timeout_rate_pct": float(alert_thresholds["timeout_rate_pct"]),
                    "degraded_rate_pct": float(alert_thresholds["degraded_rate_pct"]),
                    "failure_rate_pct": float(alert_thresholds["failure_rate_pct"]),
                },
                "active_alerts": active_alerts,
            },
        }

    def _query_snapshot_staleness(self) -> int | None:
        """Return staleness of the most recent config snapshot in ms."""
        try:
            rows = self.ledger.config_snapshot_log
        except Exception:
            rows = []

        if not rows:
            return None

        latest_ts = max(int(r.get("ts_ms") or 0) for r in rows)
        if latest_ts == 0:
            return None
        return int(now_ms()) - latest_ts

    def _query_recent_chains(self, n: int) -> list[dict[str, Any]]:
        """Return the n most recent request chains (intent→governor→skill)."""
        try:
            intent_rows = self.ledger.intent_log
        except Exception:
            intent_rows = []

        # 按 ts_ms 降序取最近 n 条
        sorted_rows = sorted(intent_rows, key=lambda r: int(r.get("ts_ms") or 0), reverse=True)
        chains: list[dict[str, Any]] = []
        for row in sorted_rows[:n]:
            rid = row.get("request_id") or ""
            chain: dict[str, Any] = {
                "request_id": rid,
                "intent_id": row.get("intent_id"),
                "ts_ms": row.get("ts_ms"),
                "decision": None,
                "reason_code": None,
                "skill_name": None,
            }
            # 查找对应 governor 决策
            try:
                gov_rows = [
                    g for g in self.ledger.governor_log
                    if g.get("request_id") == rid
                ]
                if gov_rows:
                    latest_gov = sorted(gov_rows, key=lambda g: int(g.get("ts_ms") or 0))[-1]
                    chain["decision"] = latest_gov.get("decision")
                    chain["reason_code"] = latest_gov.get("reason_code")
            except Exception:
                pass
            # 查找对应 skill 执行
            try:
                skill_rows = [
                    s for s in self.ledger.skill_exec_log
                    if s.get("request_id") == rid
                ]
                if skill_rows:
                    chain["skill_name"] = skill_rows[-1].get("skill_name")
            except Exception:
                pass
            chains.append(chain)
        return chains

    def _build_debug_trace(self, request_id: str) -> dict[str, Any]:
        """Build debug_trace for dev mode: intent_draft, rules_hit, skill_params."""
        intent_draft: dict[str, Any] | None = None
        rules_hit: list[str] = []
        skill_params: dict[str, Any] | None = None

        try:
            intent_rows = [
                r for r in self.ledger.intent_log
                if r.get("request_id") == request_id
            ]
            if intent_rows:
                latest = sorted(intent_rows, key=lambda r: int(r.get("ts_ms") or 0))[-1]
                draft_raw = latest.get("proposed_intent_draft_json")
                if isinstance(draft_raw, dict):
                    intent_draft = draft_raw
                elif isinstance(draft_raw, str):
                    import json
                    try:
                        intent_draft = json.loads(draft_raw)
                    except Exception:
                        intent_draft = {"raw": draft_raw}
        except Exception:
            pass

        try:
            gov_rows = [
                r for r in self.ledger.governor_log
                if r.get("request_id") == request_id
            ]
            if gov_rows:
                latest_gov = sorted(gov_rows, key=lambda r: int(r.get("ts_ms") or 0))[-1]
                rule_trace = latest_gov.get("rule_trace") or []
                if isinstance(rule_trace, list):
                    rules_hit = [
                        g["gate_id"] for g in rule_trace
                        if isinstance(g, dict) and g.get("hit")
                    ]
        except Exception:
            pass

        try:
            skill_rows = [
                r for r in self.ledger.skill_exec_log
                if r.get("request_id") == request_id
            ]
            if skill_rows:
                latest_skill = sorted(skill_rows, key=lambda r: int(r.get("ts_ms") or 0))[-1]
                skill_params = {
                    "skill_name": latest_skill.get("skill_name"),
                    "result_status": latest_skill.get("result_status"),
                    "result_code": latest_skill.get("result_code"),
                }
        except Exception:
            pass

        return {
            "intent_draft": intent_draft,
            "rules_hit": rules_hit,
            "skill_params": skill_params,
        }

    # ------------------------------------------------------------------
    # M8 TASK-0109: 性能基线采集
    # ------------------------------------------------------------------

    def collect_latency_baseline(
        self,
        *,
        ts_ms: int | None = None,
        stale_threshold_ms: int = 24 * 60 * 60 * 1000,  # 24h
    ) -> dict[str, Any]:
        """Compute p50/p95 latency baseline from retrieval/governor/skill_exec logs.

        Returns a latency_baseline dict with:
          retrieval_p50_ms, retrieval_p95_ms,
          governor_p50_ms, governor_p95_ms,
          skill_exec_p50_ms, skill_exec_p95_ms,
          sampled_at, sample_count, stale (bool)
        """
        now_value = int(ts_ms if ts_ms is not None else now_ms())

        # ── 检索链路时延（来自 retrieval_event_log.payload.latency_ms）──
        retrieval_lats: list[int] = []
        try:
            for r in self.ledger.retrieval_event_log:
                payload = r.get("payload") or {}
                if isinstance(payload, dict):
                    lat = payload.get("latency_ms")
                    if isinstance(lat, (int, float)) and lat >= 0:
                        retrieval_lats.append(int(lat))
        except Exception:
            pass

        # ── Governor 裁决时延（来自 governor_log.latency_ms，若无则跳过）──
        governor_lats: list[int] = []
        try:
            for r in self.ledger.governor_log:
                lat = r.get("latency_ms")
                if isinstance(lat, (int, float)) and lat >= 0:
                    governor_lats.append(int(lat))
        except Exception:
            pass

        # ── Skill 执行时延（来自 skill_exec_log.latency_ms，若无则跳过）──
        skill_lats: list[int] = []
        try:
            for r in self.ledger.skill_exec_log:
                lat = r.get("latency_ms")
                if isinstance(lat, (int, float)) and lat >= 0:
                    skill_lats.append(int(lat))
        except Exception:
            pass

        def _percentile(lats: list[int], pct: float) -> int | None:
            if not lats:
                return None
            s = sorted(lats)
            idx = min(int(len(s) * pct), len(s) - 1)
            return s[idx]

        sample_count = len(retrieval_lats) + len(governor_lats) + len(skill_lats)

        # 判断是否过期：若 sample_count=0 或最新采样时间超过阈值，标记 stale
        stale = sample_count == 0

        # 尝试从 retrieval_event_log 获取最新采样时间
        latest_sample_ts: int | None = None
        try:
            all_ts = [
                int(r.get("ts_ms") or 0)
                for r in self.ledger.retrieval_event_log
                if r.get("ts_ms")
            ]
            if all_ts:
                latest_sample_ts = max(all_ts)
                if (now_value - latest_sample_ts) > stale_threshold_ms:
                    stale = True
        except Exception:
            pass

        sampled_at = (
            datetime.fromtimestamp(latest_sample_ts / 1000, tz=timezone.utc).isoformat()
            if latest_sample_ts
            else datetime.fromtimestamp(now_value / 1000, tz=timezone.utc).isoformat()
        )

        return {
            "retrieval_p50_ms": _percentile(retrieval_lats, 0.50),
            "retrieval_p95_ms": _percentile(retrieval_lats, 0.95),
            "governor_p50_ms": _percentile(governor_lats, 0.50),
            "governor_p95_ms": _percentile(governor_lats, 0.95),
            "skill_exec_p50_ms": _percentile(skill_lats, 0.50),
            "skill_exec_p95_ms": _percentile(skill_lats, 0.95),
            "sampled_at": sampled_at,
            "sample_count": sample_count,
            "stale": stale,
            "stale_reason_code": PERF_BASELINE_STALE if stale else None,
        }

    def _query_agent_metrics(self) -> dict[str, Any]:
        """M-MA4：收集多 agent 专项指标。

        从 presence_events（写入 stream_events 表）中统计：
        - agent_hop_count：总 handoff 次数
        - avg_hops_per_request：每请求平均 hop 数
        - handoff_trace_complete_rate_pct：handoff 追踪完整率（有 AGENT_HANDOFF 事件的请求占比）
        - agent_dispatch_count：各 agent 被选派次数
        - subgraph_failure_rate_pct：子图失败率（来自 trace 中 subgraph 节点失败比例）
        - multi_agent_enabled：当前灰度开关状态
        """
        import os

        def _parse_bool_env(key: str, default: bool = False) -> bool:
            raw = str(os.environ.get(key, "")).strip().lower()
            if raw in {"1", "true", "yes", "on"}:
                return True
            if raw in {"0", "false", "no", "off"}:
                return False
            return default

        # 能力开关状态（P2 收敛：SUPERVISOR_MODE=rule|llm，不再用 SUPERVISOR_AUTO_ENABLED）
        switches = {
            "SUPERVISOR_MODE": str(os.environ.get("SUPERVISOR_MODE") or "rule").strip().lower(),
            "INTENT_SUBGRAPH_ENABLED": _parse_bool_env("INTENT_SUBGRAPH_ENABLED"),
            "READONLY_PARALLEL_ENABLED": _parse_bool_env("READONLY_PARALLEL_ENABLED"),
        }

        # 从 EvidencePlane.stream_events 按时间范围读取 agent 事件（优先）；否则回退 ledger.stream_events
        agent_selected_events: list[dict[str, Any]] = []
        agent_handoff_events: list[dict[str, Any]] = []
        now_ts = now_ms()
        since_ts = now_ts - 60 * 60 * 1000  # 近 60 分钟
        try:
            try:
                from host_brain.integrations.graph_evidence import get_evidence_plane
                ep = get_evidence_plane()
                stream_rows = ep.query_stream_events_by_time(
                    since_ms=since_ts,
                    end_ms=now_ts,
                    event_types=["AGENT_SELECTED", "AGENT_HANDOFF"],
                )
            except Exception:
                stream_rows = getattr(self.ledger, "stream_events", []) or []
            for row in stream_rows:
                event_type = str(row.get("event_type") or "").strip().upper()
                if event_type == "AGENT_SELECTED":
                    agent_selected_events.append(row)
                elif event_type == "AGENT_HANDOFF":
                    agent_handoff_events.append(row)
        except Exception:
            pass

        # 各 agent 被选派次数（EvidencePlane 无 agent_id 时用 node_id 表示选派节点）
        agent_dispatch_count: dict[str, int] = {}
        for ev in agent_selected_events:
            agent_id = str(ev.get("agent_id") or ev.get("node_id") or "").strip()
            if agent_id:
                agent_dispatch_count[agent_id] = agent_dispatch_count.get(agent_id, 0) + 1

        # 每请求 hop 数统计
        hop_by_request: dict[str, int] = {}
        for ev in agent_handoff_events:
            rid = str(ev.get("request_id") or "").strip()
            if rid:
                hop_by_request[rid] = hop_by_request.get(rid, 0) + 1

        total_hops = sum(hop_by_request.values())
        total_requests_with_hops = len(hop_by_request)
        avg_hops = round(total_hops / total_requests_with_hops, 2) if total_requests_with_hops > 0 else 0.0

        # handoff 追踪完整率：有 AGENT_SELECTED 的请求中，有 AGENT_HANDOFF 的比例
        requests_with_selected = {str(ev.get("request_id") or "") for ev in agent_selected_events if ev.get("request_id")}
        requests_with_handoff = {str(ev.get("request_id") or "") for ev in agent_handoff_events if ev.get("request_id")}
        handoff_trace_complete_rate_pct = _rate_pct(
            len(requests_with_selected & requests_with_handoff),
            len(requests_with_selected),
        ) if requests_with_selected else 100.0  # 无 agent 请求视为 100%

        # 告警：avg_hops > 2.5（AUTO 场景阈值）
        active_alerts: list[dict[str, Any]] = []
        avg_hops_threshold = float(os.environ.get("OBS_ALERT_AGENT_AVG_HOPS", "2.5"))
        if total_requests_with_hops > 0 and avg_hops > avg_hops_threshold:
            active_alerts.append({
                "code": "AGENT_AVG_HOPS_HIGH",
                "severity": "WARN",
                "observed_avg_hops": avg_hops,
                "threshold_avg_hops": avg_hops_threshold,
            })
        # 告警：handoff 追踪完整率 < 100%
        if requests_with_selected and handoff_trace_complete_rate_pct < 100.0:
            active_alerts.append({
                "code": "AGENT_HANDOFF_TRACE_INCOMPLETE",
                "severity": "WARN",
                "observed_rate_pct": handoff_trace_complete_rate_pct,
                "threshold_rate_pct": 100.0,
            })

        return {
            "switches": switches,
            "agent_hop_count": total_hops,
            "avg_hops_per_request": avg_hops,
            "handoff_trace_complete_rate_pct": handoff_trace_complete_rate_pct,
            "agent_dispatch_count": agent_dispatch_count,
            "alerting": {
                "avg_hops_threshold": avg_hops_threshold,
                "active_alerts": active_alerts,
            },
        }

    def _query_supervisor_metrics(self) -> dict[str, Any]:
        """P5-4：从 supervisor_decision_log 聚合 Supervisor 决策观测。

        统计：rule/llm/llm_fallback_rule 次数；failure_code 分布；selected_agent 分布；llm 模式 fallback 率。
        """
        rows: list[dict[str, Any]] = []
        try:
            rows = getattr(self.ledger, "supervisor_decision_log", None) or []
        except Exception:
            pass

        decision_source_count: dict[str, int] = {}
        failure_code_count: dict[str, int] = {}
        selected_agent_count: dict[str, int] = {}
        llm_mode_total = 0
        llm_mode_fallback = 0

        for r in rows:
            src = str(r.get("supervisor_decision_source") or "rule").strip()
            decision_source_count[src] = decision_source_count.get(src, 0) + 1
            agent = str(r.get("supervisor_selected_agent") or "").strip()
            if agent:
                selected_agent_count[agent] = selected_agent_count.get(agent, 0) + 1
            fc = r.get("supervisor_failure_code")
            if fc:
                failure_code_count[str(fc)] = failure_code_count.get(str(fc), 0) + 1
            mode = str(r.get("supervisor_mode") or "rule").strip()
            if mode == "llm":
                llm_mode_total += 1
                if src == "llm_fallback_rule":
                    llm_mode_fallback += 1

        llm_fallback_rate_pct = _rate_pct(llm_mode_fallback, llm_mode_total) if llm_mode_total > 0 else 0.0

        return {
            "decision_source_count": decision_source_count,
            "failure_code_count": failure_code_count,
            "selected_agent_count": selected_agent_count,
            "llm_mode_total": llm_mode_total,
            "llm_mode_fallback_count": llm_mode_fallback,
            "llm_fallback_rate_pct": llm_fallback_rate_pct,
            "total_decisions": len(rows),
        }

    def _query_budget_breach_metrics(self) -> dict[str, Any]:
        """P7-3：从 supervisor_decision_log 聚合预算突破类失败（超时/超步数等）。

        统计：budget_breach_count（Supervisor 决策中 failure_code 属于 BUDGET_BREACH 的数量）、
        budget_breach_by_code 分布。子图/local loop 的 budget breach 当前由 run 输出 subgraph_failure 承载，
        若需纳入聚合可后续扩展 ledger 或 run 级采样。
        """
        rows: list[dict[str, Any]] = []
        try:
            rows = getattr(self.ledger, "supervisor_decision_log", None) or []
        except Exception:
            pass

        budget_breach_by_code: dict[str, int] = {}
        budget_breach_count = 0
        for r in rows:
            fc = r.get("supervisor_failure_code")
            if fc and is_budget_breach_failure_code(str(fc)):
                budget_breach_count += 1
                code = str(fc).strip()
                budget_breach_by_code[code] = budget_breach_by_code.get(code, 0) + 1

        total_supervisor = len(rows)
        budget_breach_rate_pct = _rate_pct(budget_breach_count, total_supervisor) if total_supervisor > 0 else 0.0

        return {
            "budget_breach_count": budget_breach_count,
            "budget_breach_by_code": budget_breach_by_code,
            "budget_breach_rate_pct": budget_breach_rate_pct,
            "total_supervisor_decisions": total_supervisor,
        }

    def _query_emotion_metrics(self) -> dict[str, Any]:
        """M29-Q（OPT-?）：收集情感维度指标。

        从 ledger 中统计：
        - emotion_tag 分布（各情感标签出现次数）
        - 平均情感强度
        - 情感门控拒绝次数（Governor R8）
        - 最近情感状态
        """
        emotion_tag_counts: dict[str, int] = {}
        intensity_sum = 0.0
        intensity_count = 0
        emotion_gate_blocked = 0
        latest_emotion_tag: str | None = None
        latest_mood_state: str | None = None

        try:
            # 从 governor_decision_log 中统计情感相关数据
            for record in getattr(self.ledger, "governor_decision_log", []):
                payload = record.get("payload") or {}
                emotion_tag = payload.get("emotion_tag")
                if emotion_tag:
                    emotion_tag_counts[emotion_tag] = emotion_tag_counts.get(emotion_tag, 0) + 1
                    latest_emotion_tag = emotion_tag
                intensity = payload.get("emotion_intensity")
                if intensity is not None:
                    try:
                        intensity_sum += float(intensity)
                        intensity_count += 1
                    except (TypeError, ValueError):
                        pass
                # 统计 R8 情感门控拒绝次数
                reason_code = record.get("reason_code") or ""
                if "EMOTION_GATE" in str(reason_code).upper() or "R8" in str(reason_code).upper():
                    emotion_gate_blocked += 1
                mood_state = payload.get("mood_state")
                if mood_state:
                    latest_mood_state = mood_state
        except Exception:
            pass

        avg_intensity = round(intensity_sum / intensity_count, 4) if intensity_count > 0 else None

        return {
            "emotion_tag_distribution": emotion_tag_counts,
            "avg_emotion_intensity": avg_intensity,
            "emotion_gate_blocked_count": emotion_gate_blocked,
            "latest_emotion_tag": latest_emotion_tag,
            "latest_mood_state": latest_mood_state,
        }
