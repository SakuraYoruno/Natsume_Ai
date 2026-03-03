"""OBS metrics collector: aggregates governor stats, retrieval latency, and recent chains.

Supports dev/prod mode via OBS_MODE env var:
  - dev:  includes debug_trace (intent_draft, rules_hit, skill_params)
  - prod: debug_trace is None
"""

from __future__ import annotations

import os
import time
from enum import Enum
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.ids import now_ms


class ObsMode(str, Enum):
    DEV = "dev"
    PROD = "prod"


def get_obs_mode() -> ObsMode:
    """Read OBS_MODE env var; default to prod."""
    raw = os.environ.get("OBS_MODE", "prod").strip().lower()
    if raw == "dev":
        return ObsMode.DEV
    return ObsMode.PROD


class MetricsCollector:
    """Aggregates observability metrics from SQLiteLedger for /obs/metrics endpoint."""

    def __init__(self, *, ledger: SQLiteLedger) -> None:
        self.ledger = ledger

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

        governor_stats = self._query_governor_stats()
        retrieval_stats = self._query_retrieval_stats()
        snapshot_staleness = self._query_snapshot_staleness()
        recent_chains = self._query_recent_chains(recent_chains_n)

        payload: dict[str, Any] = {
            "request_id": request_id,
            "ts_ms": collected_ts,
            "obs_mode": mode.value,
            "governor_decisions": governor_stats,
            "retrieval_latency": retrieval_stats,
            "hard_snapshot_staleness_ms": snapshot_staleness,
            "recent_chains": recent_chains,
            "debug_trace": None,
        }

        if mode == ObsMode.DEV and request_id:
            payload["debug_trace"] = self._build_debug_trace(request_id)

        return payload

    # ------------------------------------------------------------------
    # Internal query helpers
    # ------------------------------------------------------------------

    def _query_governor_stats(self) -> dict[str, Any]:
        """Count governor decisions from governor_log."""
        try:
            rows = self.ledger.governor_log
        except Exception:
            rows = []

        total = len(rows)
        approved = sum(1 for r in rows if r.get("decision") == "APPROVE")
        rejected = sum(1 for r in rows if r.get("decision") == "REJECT")
        degraded = sum(1 for r in rows if r.get("decision") == "DEGRADED")

        # 伪执行拦截：reason_code 含 AIRLOCK 或 SAFETY 的 REJECT
        pseudo_exec_blocked = sum(
            1 for r in rows
            if r.get("decision") == "REJECT"
            and r.get("reason_code") in {"AIRLOCK_BLOCKED", "SAFETY_BLOCKED"}
        )

        return {
            "total": total,
            "approved": approved,
            "rejected": rejected,
            "degraded": degraded,
            "pseudo_exec_blocked": pseudo_exec_blocked,
        }

    def _query_retrieval_stats(self) -> dict[str, Any]:
        """Compute p50/p95 latency from retrieval_event_log."""
        try:
            rows = self.ledger.retrieval_event_log
        except Exception:
            rows = []

        latencies: list[int] = []
        for r in rows:
            payload = r.get("payload") or {}
            if isinstance(payload, dict):
                lat = payload.get("latency_ms")
                if isinstance(lat, (int, float)) and lat >= 0:
                    latencies.append(int(lat))

        if not latencies:
            return {"p50_ms": None, "p95_ms": None, "sample_count": 0}

        sorted_lats = sorted(latencies)
        n = len(sorted_lats)
        p50 = sorted_lats[int(n * 0.50)]
        p95 = sorted_lats[min(int(n * 0.95), n - 1)]
        return {"p50_ms": p50, "p95_ms": p95, "sample_count": n}

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
