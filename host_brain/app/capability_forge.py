"""Capability Forge service for promoting gap hotspots into governed proposals."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
from typing import Any, Mapping

from host_brain.app.capability_gap_telemetry import (
    CapabilityGapTelemetry,
    _normalize_tags,
)
from host_brain.core.ids import now_ms as _now_ms
from host_brain.core.soft_fail import soft_fail  # OPT-3 Batch D：业务路径统一降级


_FORGE_STATE_STAGE = "CAPABILITY_FORGE_STATE"
_FORGE_EVENT_CREATE = "CAPABILITY_FORGE_EVENT_CREATED"

_DEFAULT_EXECUTOR_BY_FAMILY = {
    "code": "agent_worker",
    "translation": "translate_agent",
    "perception": "vision_agent",
    "vision": "vision_agent",
    "system": "repair_agent",
    "reasoning": "intent_agent",
    "social": "surface_reply",
}


def _build_proposal_id(candidate_key: str, ts_ms: int) -> str:
    digest = hashlib.sha1(f"{candidate_key}|{ts_ms}".encode("utf-8")).hexdigest()[:14]
    return f"forge_prop_{digest}"


def _build_candidate_key(*, reason_code: str, suggested_family: str, missing_capability_tags: list[str]) -> str:
    return "|".join(
        [
            str(reason_code or "UNKNOWN").strip().upper() or "UNKNOWN",
            str(suggested_family or "unknown").strip().lower() or "unknown",
            ",".join(sorted(_normalize_tags(missing_capability_tags))),
        ]
    )


class CapabilityForgeService:
    """Proposal-only Forge registry; it owns no runtime or activation route."""

    def __init__(
        self,
        *,
        telemetry: CapabilityGapTelemetry,
        ledger: Any | None = None,
    ) -> None:
        self._telemetry = telemetry
        self._ledger = ledger
        self._lock = threading.RLock()
        self._loaded_from_ledger = False
        self._proposals: dict[str, dict[str, Any]] = {}

    def _audit(self, *, request_id: str, stage: str, reason_code: str, detail: str) -> None:
        if self._ledger is None:
            return
        with soft_fail("capability_forge.audit_log_error", fallback=None):
            self._ledger.log_error(
                request_id=request_id,
                stage=stage,
                reason_code=reason_code,
                detail=detail[:1500],
            )

    def _persist_state_event(self, *, proposal: Mapping[str, Any], event: str, actor: str, reason: str | None = None) -> None:
        if self._ledger is None:
            return
        payload = dict(proposal)
        proposal_id = str(payload.get("proposal_id") or "").strip()
        if not proposal_id:
            return
        now = _now_ms()
        promotion_stage = str(dict(payload.get("promotion") or {}).get("stage") or "PROPOSED").strip().upper()
        status = str(payload.get("status") or "UNKNOWN").strip().upper() or "UNKNOWN"
        try:
            self._ledger.log_capability_forge_proposal(
                {
                    "proposal_id": proposal_id,
                    "event_type": str(event or "").strip().upper(),
                    "status": status,
                    "stage": promotion_stage,
                    "session_id": str(payload.get("session_id") or "").strip(),
                    "candidate_key": str(payload.get("candidate_key") or "").strip(),
                    "reason_code": str(reason or "").strip().upper() or None,
                    "actor": str(actor or "").strip(),
                    "payload": payload,
                    "ts_ms": now,
                }
            )
        except Exception:
            # backward compatibility fallback
            try:
                legacy = {
                    "proposal_id": proposal_id,
                    "event": str(event or "").strip().upper(),
                    "actor": str(actor or "").strip(),
                    "reason": str(reason or "").strip(),
                    "proposal_snapshot": payload,
                    "ts_ms": now,
                }
                self._ledger.log_error(
                    request_id=proposal_id,
                    stage=_FORGE_STATE_STAGE,
                    reason_code=str(event or "").strip().upper(),
                    detail=json.dumps(legacy, ensure_ascii=False),
                )
            except Exception:
                return

    def _ensure_loaded_from_ledger(self) -> None:
        if self._loaded_from_ledger:
            return
        with self._lock:
            if self._loaded_from_ledger:
                return
            if self._ledger is None:
                self._loaded_from_ledger = True
                return
            with soft_fail(
                "capability_forge.ledger_list_proposals", fallback=[]
            ) as _rows_box:
                _rows_box[0] = list(self._ledger.list_capability_forge_proposals(limit=200000, offset=0))
            rows = _rows_box[0]
            restored: dict[str, dict[str, Any]] = {}
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                payload = row.get("payload")
                if not isinstance(payload, Mapping):
                    continue
                snapshot = payload
                proposal_id = str(snapshot.get("proposal_id") or "").strip()
                if not proposal_id:
                    continue
                restored[proposal_id] = dict(snapshot)
            # legacy fallback: recover from prior error_log encoding
            if not restored:
                with soft_fail(
                    "capability_forge.ledger_fetch_legacy", fallback=[]
                ) as _legacy_box:
                    _legacy_box[0] = list(self._ledger.fetch_by_time_range("error_log", limit=200000, offset=0))
                legacy_rows = _legacy_box[0]
                for row in legacy_rows:
                    if not isinstance(row, Mapping):
                        continue
                    if str(row.get("stage") or "").strip().upper() != _FORGE_STATE_STAGE:
                        continue
                    raw_detail = str(row.get("detail") or "").strip()
                    if not raw_detail:
                        continue
                    try:
                        payload = json.loads(raw_detail)
                    except Exception:
                        continue
                    if not isinstance(payload, Mapping):
                        continue
                    snapshot = payload.get("proposal_snapshot")
                    if not isinstance(snapshot, Mapping):
                        continue
                    proposal_id = str(snapshot.get("proposal_id") or "").strip()
                    if not proposal_id:
                        continue
                    restored[proposal_id] = dict(snapshot)
            for proposal_id, snapshot in list(restored.items()):
                if not isinstance(snapshot, Mapping):
                    continue
                normalized = dict(snapshot)
                runtime_stats = dict(normalized.get("runtime_stats") or {})
                runtime_stats.setdefault("total", 0)
                runtime_stats.setdefault("success", 0)
                runtime_stats.setdefault("failed", 0)
                runtime_stats.setdefault("consecutive_failures", 0)
                runtime_stats.setdefault("window_total", 0)
                runtime_stats.setdefault("window_failed", 0)
                runtime_stats.setdefault("window_failure_rate", 0.0)
                runtime_stats.setdefault("window_success_rate", 0.0)
                runtime_stats.setdefault("window_p95_latency_ms", 0)
                runtime_stats.setdefault("latency_ema_ms", 0.0)
                runtime_stats.setdefault("stability_score", 0.0)
                runtime_stats.setdefault("health_score", 0.0)
                runtime_stats.setdefault("recent_results", [])
                runtime_stats.setdefault("adaptive_tune_count", 0)
                runtime_stats.setdefault("last_tune_action", "NONE")
                runtime_stats.setdefault("last_tune_reason", None)
                runtime_stats.setdefault("last_tune_ts_ms", None)
                runtime_stats.setdefault("auto_rollback_triggered", False)
                normalized["runtime_stats"] = runtime_stats
                restored[proposal_id] = normalized
            self._proposals = restored
            self._loaded_from_ledger = True

    def _resolve_candidate(self, candidate_key: str | None) -> dict[str, Any] | None:
        key = str(candidate_key or "").strip()
        if key:
            return self._telemetry.get_candidate_detail(key)
        snapshot = self._telemetry.snapshot(
            limit_recent=5,
            limit_hotspots=1,
            limit_candidates=1,
            min_candidate_detected=2,
            min_candidate_sessions=1,
            min_candidate_failure_rate=0.0,
            min_candidate_failed=0,
        )
        forge_candidates = list(snapshot.get("forge_candidates") or [])
        if forge_candidates:
            top = forge_candidates[0]
            detail = self._telemetry.get_candidate_detail(str(top.get("candidate_key") or ""))
            if detail is not None:
                return detail
        hotspots = list(snapshot.get("hotspots") or [])
        if hotspots:
            top = hotspots[0]
            detail = self._telemetry.get_candidate_detail(str(top.get("candidate_key") or ""))
            if detail is not None:
                return detail
        return None

    def resolve_candidate(self, candidate_key: str | None = None) -> dict[str, Any] | None:
        return copy.deepcopy(self._resolve_candidate(candidate_key))

    def _find_latest_candidate_proposal_locked(self, candidate_key: str) -> dict[str, Any] | None:
        key = str(candidate_key or "").strip()
        if not key:
            return None
        latest: dict[str, Any] | None = None
        latest_ts = -1
        for proposal in self._proposals.values():
            if str(proposal.get("candidate_key") or "").strip() != key:
                continue
            ts = int(proposal.get("updated_ts_ms") or proposal.get("created_ts_ms") or 0)
            if ts >= latest_ts:
                latest_ts = ts
                latest = dict(proposal)
        return latest

    def _get_auto_plan_thresholds(self) -> dict[str, Any]:
        # OPT-4 第二批：原先散落的 10 个 HOST_CAPABILITY_FORGE_AUTO_* 环境变量
        # 已统一迁移至 host_brain.app.settings.capability_forge.CapabilityForgeSettings；
        # 本处保持对外契约不变（返回 dict，键名保持与历史调用点一致）。
        from host_brain.app.settings.capability_forge import (  # noqa: PLC0415
            get_capability_forge_settings,
        )

        s = get_capability_forge_settings()
        return {
            "enabled": bool(s.auto_proposal_enabled),
            "min_detected_count": int(s.auto_min_detected),
            "min_unique_sessions": int(s.auto_min_sessions),
            "min_failure_rate": float(s.auto_min_failure_rate),
            "min_failed_count": int(s.auto_min_failed),
            "dedupe_cooldown_ms": int(s.auto_dedupe_cooldown_ms),
            "canary_traffic_percent": int(s.auto_canary_traffic_percent),
        }

    def maybe_auto_plan_for_gap(
        self,
        *,
        request_id: str,
        session_id: str,
        reason_code: str,
        suggested_family: str,
        missing_capability_tags: list[str] | tuple[str, ...] | None = None,
        proposer: str = "executive_runtime",
    ) -> dict[str, Any]:
        self._ensure_loaded_from_ledger()
        normalized_request_id = str(request_id or "").strip()
        thresholds = self._get_auto_plan_thresholds()
        if not bool(thresholds.get("enabled")):
            return {
                "triggered": False,
                "reason": "AUTO_PROPOSAL_DISABLED",
                "request_id": normalized_request_id,
            }

        normalized_reason = str(reason_code or "UNKNOWN").strip().upper() or "UNKNOWN"
        normalized_family = str(suggested_family or "unknown").strip().lower() or "unknown"
        normalized_tags = _normalize_tags(missing_capability_tags)
        candidate_key = _build_candidate_key(
            reason_code=normalized_reason,
            suggested_family=normalized_family,
            missing_capability_tags=normalized_tags,
        )
        candidate = self._telemetry.get_candidate_detail(candidate_key)
        if candidate is None:
            return {
                "triggered": False,
                "reason": "CANDIDATE_NOT_FOUND",
                "request_id": normalized_request_id,
                "candidate_key": candidate_key,
            }

        detected_count = int(candidate.get("detected_count") or 0)
        executed_count = int(candidate.get("executed_count") or 0)
        failed_count = int(candidate.get("failed_count") or 0)
        failure_rate = float(candidate.get("failure_rate") or 0.0)
        unique_sessions = int(candidate.get("impact_sessions") or 0)
        min_detected_count = int(thresholds.get("min_detected_count") or 3)
        min_unique_sessions = int(thresholds.get("min_unique_sessions") or 2)
        min_failure_rate = float(thresholds.get("min_failure_rate") or 0.35)
        min_failed_count = int(thresholds.get("min_failed_count") or 2)

        meets_threshold = (
            detected_count >= min_detected_count
            and unique_sessions >= min_unique_sessions
            and (
                failed_count >= min_failed_count
                or failure_rate >= min_failure_rate
                or (executed_count == 0 and detected_count >= max(4, min_detected_count * 2))
            )
        )
        if not meets_threshold:
            return {
                "triggered": False,
                "reason": "THRESHOLD_NOT_MET",
                "request_id": normalized_request_id,
                "candidate_key": candidate_key,
                "metrics": {
                    "detected_count": detected_count,
                    "executed_count": executed_count,
                    "failed_count": failed_count,
                    "failure_rate": round(failure_rate, 4),
                    "impact_sessions": unique_sessions,
                },
                "thresholds": {
                    "min_detected_count": min_detected_count,
                    "min_unique_sessions": min_unique_sessions,
                    "min_failure_rate": round(min_failure_rate, 4),
                    "min_failed_count": min_failed_count,
                },
            }

        now = _now_ms()
        dedupe_cooldown_ms = int(thresholds.get("dedupe_cooldown_ms") or 1800000)
        with self._lock:
            latest = self._find_latest_candidate_proposal_locked(candidate_key)
        if latest:
            status = str(latest.get("status") or "").strip().upper()
            updated_ts = int(latest.get("updated_ts_ms") or latest.get("created_ts_ms") or 0)
            within_cooldown = updated_ts > 0 and (now - updated_ts) < dedupe_cooldown_ms
            if status in {"PENDING_APPROVAL", "APPROVED"}:
                return {
                    "triggered": False,
                    "reason": "EXISTING_ACTIVE_PROPOSAL",
                    "request_id": normalized_request_id,
                    "candidate_key": candidate_key,
                    "proposal_id": str(latest.get("proposal_id") or ""),
                    "status": status,
                    "within_cooldown": within_cooldown,
                }
            if status == "ROLLED_BACK" and within_cooldown:
                return {
                    "triggered": False,
                    "reason": "RECENT_ROLLBACK_COOLDOWN",
                    "request_id": normalized_request_id,
                    "candidate_key": candidate_key,
                    "proposal_id": str(latest.get("proposal_id") or ""),
                    "status": status,
                    "within_cooldown": True,
                }

        created = self.create_proposal(
            session_id=session_id,
            proposer=proposer,
            candidate_key=candidate_key,
            canary_traffic_percent=int(thresholds.get("canary_traffic_percent") or 5),
        )
        proposal_id = str(created.get("proposal_id") or "")
        result: dict[str, Any] = {
            "triggered": True,
            "reason": "PROPOSAL_CREATED",
            "request_id": normalized_request_id,
            "candidate_key": candidate_key,
            "proposal_id": proposal_id,
            "status": str(created.get("status") or "PENDING_APPROVAL"),
            "auto_approved": False,
        }

        return result

    def _build_artifact(
        self,
        *,
        proposal_id: str,
        session_id: str,
        proposer: str,
        candidate: Mapping[str, Any],
        canary_traffic_percent: int,
        ts_ms: int,
    ) -> dict[str, Any]:
        family = str(candidate.get("suggested_family") or "unknown").strip().lower() or "unknown"
        tags = [
            str(tag).strip().lower()
            for tag in list(candidate.get("missing_capability_tags") or [])
            if str(tag or "").strip()
        ]
        candidate_key = str(candidate.get("candidate_key") or "")
        top_targets = [
            dict(item)
            for item in list(candidate.get("top_executor_targets") or [])
            if isinstance(item, Mapping)
        ]
        executor_target = ""
        if top_targets:
            executor_target = str(top_targets[0].get("key") or "").strip()
        if not executor_target:
            executor_target = _DEFAULT_EXECUTOR_BY_FAMILY.get(family, "intent_agent")
        return {
            "proposal_id": proposal_id,
            "candidate_key": candidate_key,
            "status": "PENDING_APPROVAL",
            "created_ts_ms": ts_ms,
            "updated_ts_ms": ts_ms,
            "session_id": session_id,
            "proposer": proposer,
            "candidate_metrics": {
                "reason_code": str(candidate.get("reason_code") or "UNKNOWN"),
                "suggested_family": family,
                "missing_capability_tags": tags,
                "detected_count": int(candidate.get("detected_count") or 0),
                "executed_count": int(candidate.get("executed_count") or 0),
                "success_count": int(candidate.get("success_count") or 0),
                "failed_count": int(candidate.get("failed_count") or 0),
                "failure_rate": float(candidate.get("failure_rate") or 0.0),
                "impact_sessions": int(candidate.get("impact_sessions") or 0),
                "impact_requests": int(candidate.get("impact_requests") or 0),
                "top_executor_targets": top_targets,
            },
            "artifact": {
                "skill_family": family,
                "capability_tags": tags,
                "executor_target": executor_target,
                "proposal_summary": (
                    f"Forge skill capability for family={family} from candidate={candidate_key} "
                    f"with reason={str(candidate.get('reason_code') or 'UNKNOWN')}"
                ),
                "implementation_blueprint": [
                    "define skill contract and I/O schema",
                    "implement executor adapter with guarded side effects",
                    "wire skill registry metadata (family/tags/risk/fallback)",
                ],
                "test_blueprint": [
                    "unit tests for deterministic routing and executor output",
                    "integration tests for gateway->intent->executive->skill chain",
                    "rollback validation tests in canary/full stage",
                ],
            },
            "governor_gate": {
                "required": True,
                "authority": "VERSION_ACTIVATION_SERVICE",
                "review_required": True,
                "production_influence_enabled": False,
            },
            "release_plan": {
                "shadow": {"enabled": True, "state": "PENDING"},
                "canary": {
                    "enabled": True,
                    "state": "PENDING",
                    "traffic_percent": max(1, min(100, int(canary_traffic_percent))),
                },
                "full": {"enabled": True, "state": "PENDING"},
                "rollback": {
                    "enabled": True,
                    "auto_trigger_failure_rate": 0.35,
                    "observe_window_minutes": 30,
                    "observe_min_samples": 5,
                    "auto_trigger_consecutive_failures": 3,
                },
                "adaptive": {
                    "enabled": True,
                    "min_samples": 8,
                    "cooldown_samples": 4,
                    "target_failure_rate": 0.25,
                    "target_health_score": 75.0,
                    "latency_guard_ms": 1500,
                    "weight_success_rate": 0.55,
                    "weight_latency": 0.25,
                    "weight_stability": 0.20,
                    "increase_step_percent": 5,
                    "decrease_step_percent": 10,
                    "max_canary_percent": 50,
                    "min_canary_percent": 1,
                    "last_tuned_total": 0,
                    "last_tuned_ts_ms": None,
                    "last_action": "INIT",
                    "last_reason": None,
                },
            },
            "promotion": {
                "stage": "PROPOSED",
                "history": [
                    {
                        "ts_ms": ts_ms,
                        "event": "PROPOSED",
                        "actor": proposer,
                    }
                ],
            },
            "runtime_stats": {
                "total": 0,
                "success": 0,
                "failed": 0,
                "consecutive_failures": 0,
                "failure_rate": 0.0,
                "window_total": 0,
                "window_failed": 0,
                "window_failure_rate": 0.0,
                "window_success_rate": 0.0,
                "window_p95_latency_ms": 0,
                "latency_ema_ms": 0.0,
                "stability_score": 0.0,
                "health_score": 0.0,
                "recent_results": [],
                "adaptive_tune_count": 0,
                "last_tune_action": "NONE",
                "last_tune_reason": None,
                "last_tune_ts_ms": None,
                "last_error_code": None,
                "last_latency_ms": 0,
                "last_result_ts_ms": None,
                "auto_rollback_triggered": False,
            },
        }

    def create_proposal(
        self,
        *,
        session_id: str,
        proposer: str,
        candidate_key: str | None = None,
        canary_traffic_percent: int = 5,
    ) -> dict[str, Any]:
        self._ensure_loaded_from_ledger()
        candidate = self._resolve_candidate(candidate_key)
        if candidate is None:
            raise ValueError("CAPABILITY_FORGE_CANDIDATE_NOT_FOUND")
        now = _now_ms()
        proposal_id = _build_proposal_id(str(candidate.get("candidate_key") or ""), now)
        normalized_session = str(session_id or "").strip() or "forge-session"
        normalized_proposer = str(proposer or "").strip() or "capability_forge"
        artifact = self._build_artifact(
            proposal_id=proposal_id,
            session_id=normalized_session,
            proposer=normalized_proposer,
            candidate=candidate,
            canary_traffic_percent=canary_traffic_percent,
            ts_ms=now,
        )
        with self._lock:
            self._proposals[proposal_id] = artifact
        self._persist_state_event(proposal=artifact, event=_FORGE_EVENT_CREATE, actor=normalized_proposer)
        return copy.deepcopy(artifact)

    def list_proposals(self, *, status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        self._ensure_loaded_from_ledger()
        status_filter = str(status or "").strip().upper()
        with self._lock:
            items = list(self._proposals.values())
        if status_filter:
            items = [item for item in items if str(item.get("status") or "").upper() == status_filter]
        items.sort(key=lambda row: int(row.get("created_ts_ms") or 0), reverse=True)
        return [copy.deepcopy(item) for item in items[: max(1, int(limit))]]

    def get_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        self._ensure_loaded_from_ledger()
        key = str(proposal_id or "").strip()
        if not key:
            return None
        with self._lock:
            found = self._proposals.get(key)
        return copy.deepcopy(found) if isinstance(found, Mapping) else None

    def runtime_match_stats(self) -> dict[str, Any]:
        return {
            "timestamp": _now_ms(),
            "counters": {
                "total": 0,
                "applied": 0,
                "full_hit": 0,
                "canary_hit": 0,
                "canary_miss": 0,
                "no_active_proposal": 0,
                "invalid_input": 0,
                "no_match": 0,
                "retired": 1,
            },
            "apply_rate": 0.0,
            "authority": "PROPOSAL_ONLY",
        }

    def runtime_tuning_stats(self, *, limit: int = 20) -> dict[str, Any]:
        self._ensure_loaded_from_ledger()
        with self._lock:
            proposals = list(self._proposals.values())
        proposals.sort(key=lambda row: int(row.get("updated_ts_ms") or row.get("created_ts_ms") or 0), reverse=True)

        items: list[dict[str, Any]] = []
        summary = {
            "total_proposals": 0,
            "approved_proposals": 0,
            "canary_active": 0,
            "adaptive_enabled": 0,
            "auto_rollback_triggered": 0,
            "last_action_scale_up": 0,
            "last_action_scale_down": 0,
            "last_action_hold": 0,
            "health_lt_40": 0,
            "health_lt_60": 0,
        }
        for proposal in proposals:
            summary["total_proposals"] = int(summary["total_proposals"]) + 1
            status = str(proposal.get("status") or "").strip().upper()
            if status == "APPROVED":
                summary["approved_proposals"] = int(summary["approved_proposals"]) + 1

            promotion = dict(proposal.get("promotion") or {})
            stage = str(promotion.get("stage") or "PROPOSED").strip().upper()
            if status == "APPROVED" and stage == "CANARY":
                summary["canary_active"] = int(summary["canary_active"]) + 1

            release_plan = dict(proposal.get("release_plan") or {})
            canary_cfg = dict(release_plan.get("canary") or {})
            rollback_cfg = dict(release_plan.get("rollback") or {})
            adaptive_cfg = dict(release_plan.get("adaptive") or {})
            adaptive_enabled = bool(adaptive_cfg.get("enabled", True))
            if adaptive_enabled:
                summary["adaptive_enabled"] = int(summary["adaptive_enabled"]) + 1

            runtime_stats = dict(proposal.get("runtime_stats") or {})
            if bool(runtime_stats.get("auto_rollback_triggered")):
                summary["auto_rollback_triggered"] = int(summary["auto_rollback_triggered"]) + 1
            health_score = max(0.0, min(100.0, float(runtime_stats.get("health_score") or 0.0)))
            if health_score < 40.0:
                summary["health_lt_40"] = int(summary["health_lt_40"]) + 1
            if health_score < 60.0:
                summary["health_lt_60"] = int(summary["health_lt_60"]) + 1

            last_action = str(
                runtime_stats.get("last_tune_action")
                or adaptive_cfg.get("last_action")
                or "NONE"
            ).strip().upper()
            if last_action == "SCALE_UP":
                summary["last_action_scale_up"] = int(summary["last_action_scale_up"]) + 1
            elif last_action == "SCALE_DOWN":
                summary["last_action_scale_down"] = int(summary["last_action_scale_down"]) + 1
            else:
                summary["last_action_hold"] = int(summary["last_action_hold"]) + 1

            items.append(
                {
                    "proposal_id": str(proposal.get("proposal_id") or ""),
                    "status": status,
                    "stage": stage,
                    "adaptive_enabled": adaptive_enabled,
                    "canary_traffic_percent": int(canary_cfg.get("traffic_percent") or 0),
                    "rollback_failure_threshold": float(rollback_cfg.get("auto_trigger_failure_rate") or 0.0),
                    "window_total": int(runtime_stats.get("window_total") or 0),
                    "window_failure_rate": float(runtime_stats.get("window_failure_rate") or 0.0),
                    "window_p95_latency_ms": int(runtime_stats.get("window_p95_latency_ms") or 0),
                    "latency_ema_ms": float(runtime_stats.get("latency_ema_ms") or 0.0),
                    "stability_score": float(runtime_stats.get("stability_score") or 0.0),
                    "health_score": float(runtime_stats.get("health_score") or 0.0),
                    "total": int(runtime_stats.get("total") or 0),
                    "failed": int(runtime_stats.get("failed") or 0),
                    "adaptive_tune_count": int(runtime_stats.get("adaptive_tune_count") or 0),
                    "last_tune_action": last_action,
                    "last_tune_reason": runtime_stats.get("last_tune_reason"),
                    "last_tune_ts_ms": runtime_stats.get("last_tune_ts_ms"),
                    "updated_ts_ms": int(proposal.get("updated_ts_ms") or 0),
                }
            )
        return {
            "timestamp": _now_ms(),
            "summary": summary,
            "items": items[: max(1, int(limit))],
        }

    def runtime_quality_stats(self, *, limit: int = 20) -> dict[str, Any]:
        self._ensure_loaded_from_ledger()
        with self._lock:
            proposals = list(self._proposals.values())
        proposals.sort(key=lambda row: int(row.get("updated_ts_ms") or row.get("created_ts_ms") or 0), reverse=True)

        tracked = 0
        unhealthy = 0
        critical = 0
        health_sum = 0.0
        failure_sum = 0.0
        latency_sum = 0.0
        latency_count = 0
        items: list[dict[str, Any]] = []
        for proposal in proposals:
            runtime_stats = dict(proposal.get("runtime_stats") or {})
            total = int(runtime_stats.get("total") or 0)
            if total <= 0:
                continue
            tracked += 1
            health_score = max(0.0, min(100.0, float(runtime_stats.get("health_score") or 0.0)))
            window_failure_rate = max(0.0, min(1.0, float(runtime_stats.get("window_failure_rate") or 0.0)))
            window_p95_latency_ms = max(0, int(runtime_stats.get("window_p95_latency_ms") or 0))
            if health_score < 60.0:
                unhealthy += 1
            if health_score < 40.0:
                critical += 1
            health_sum += health_score
            failure_sum += window_failure_rate
            if window_p95_latency_ms > 0:
                latency_sum += float(window_p95_latency_ms)
                latency_count += 1

            promotion = dict(proposal.get("promotion") or {})
            items.append(
                {
                    "proposal_id": str(proposal.get("proposal_id") or ""),
                    "status": str(proposal.get("status") or "").strip().upper(),
                    "stage": str(promotion.get("stage") or "PROPOSED").strip().upper(),
                    "health_score": round(health_score, 2),
                    "window_failure_rate": round(window_failure_rate, 4),
                    "window_p95_latency_ms": window_p95_latency_ms,
                    "stability_score": float(runtime_stats.get("stability_score") or 0.0),
                    "latency_ema_ms": float(runtime_stats.get("latency_ema_ms") or 0.0),
                    "adaptive_tune_count": int(runtime_stats.get("adaptive_tune_count") or 0),
                    "last_tune_action": str(runtime_stats.get("last_tune_action") or "NONE"),
                    "updated_ts_ms": int(proposal.get("updated_ts_ms") or 0),
                }
            )

        avg_health = round((health_sum / tracked), 2) if tracked > 0 else 0.0
        avg_failure_rate = round((failure_sum / tracked), 4) if tracked > 0 else 0.0
        avg_p95_latency = round((latency_sum / latency_count), 2) if latency_count > 0 else 0.0
        return {
            "timestamp": _now_ms(),
            "summary": {
                "tracked_proposals": tracked,
                "unhealthy_proposals": unhealthy,
                "critical_proposals": critical,
                "avg_health_score": avg_health,
                "avg_window_failure_rate": avg_failure_rate,
                "avg_window_p95_latency_ms": avg_p95_latency,
            },
            "items": items[: max(1, int(limit))],
        }


__all__ = ["CapabilityForgeService"]
