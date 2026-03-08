"""调度器心跳健康检测 Mixin。

从 scheduler.py 拆分，包含 register_agent_health_check 和心跳相关方法。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from host_brain.core.ids import now_ms
from host_brain.scheduler.health_check_prompt import (
    DEFAULT_HEARTBEAT_PROMPT,
    HEARTBEAT_EVENT_TYPE_ALERT,
    HEARTBEAT_EVENT_TYPE_OK,
    LLM_UNAVAILABLE_RESPONSE,
    MAX_RESPONSE_CHARS,
    parse_heartbeat_response,
    truncate_response,
)
from host_brain.scheduler.task_registry import SchedulerTaskConfig, TaskPriority, TriggerMode

try:
    from edge_client.rpc.health import build_edge_transport_probe_statuses as _build_edge_probes
    _EDGE_PROBE_AVAILABLE = True
except ImportError:
    _build_edge_probes = None  # type: ignore[assignment]
    _EDGE_PROBE_AVAILABLE = False

EDGE_PROBE_EVENT_TYPE_ALERT = "EDGE_PROBE_ALERT"

logger = logging.getLogger(__name__)


class SchedulerHealthMixin:
    """心跳健康检测 Mixin，需混入 NatsumeScheduler 使用 self 属性。"""

    def register_agent_health_check(
        self,
        *,
        interval_seconds: int = 1800,
        heartbeat_prompt: str | None = None,
        max_response_chars: int = MAX_RESPONSE_CHARS,
        llm_caller: Any | None = None,
    ) -> None:
        """注册 AgentHealthCheck 定时任务（默认 30 分钟间隔）。"""
        self._heartbeat_prompt = str(heartbeat_prompt or DEFAULT_HEARTBEAT_PROMPT)  # type: ignore[attr-defined]
        self._heartbeat_max_chars = int(max_response_chars)  # type: ignore[attr-defined]
        self._heartbeat_llm_caller = llm_caller  # type: ignore[attr-defined]

        task = SchedulerTaskConfig(
            task_id="agent_health_check",
            trigger_mode=TriggerMode.INTERVAL,
            trigger_config={"seconds": interval_seconds},
            intent_template={"skill_name": "agent_health_check"},
            priority=TaskPriority.MEDIUM,
            enabled=True,
            max_consecutive_failures=5,
        )
        if self._registry.get("agent_health_check") is None:  # type: ignore[attr-defined]
            self._registry.register(task)  # type: ignore[attr-defined]
        if self._running:  # type: ignore[attr-defined]
            self._schedule_task(task)  # type: ignore[attr-defined]
        logger.info("AgentHealthCheck task registered (interval=%ds)", interval_seconds)

    def _run_agent_health_check(self) -> dict[str, Any]:
        """执行一次心跳检测：发送 Prompt → 解析回复 → 静默过滤或推送告警。"""
        prompt = getattr(self, "_heartbeat_prompt", DEFAULT_HEARTBEAT_PROMPT)
        max_chars = getattr(self, "_heartbeat_max_chars", MAX_RESPONSE_CHARS)
        llm_caller = getattr(self, "_heartbeat_llm_caller", None)
        ts = now_ms()

        raw_response: str
        if llm_caller is not None:
            try:
                raw_response = str(llm_caller(prompt))
            except Exception as exc:
                logger.warning("AgentHealthCheck LLM call failed: %s", exc)
                raw_response = LLM_UNAVAILABLE_RESPONSE
        else:
            raw_response = LLM_UNAVAILABLE_RESPONSE

        state = parse_heartbeat_response(raw_response)
        truncated = truncate_response(raw_response, max_chars)

        if state == "OK":
            logger.debug("AgentHealthCheck: HEARTBEAT_OK — silent discard")
            self._check_edge_transport_probes(ts_ms=ts)  # type: ignore[attr-defined]
            return {"status": HEARTBEAT_EVENT_TYPE_OK, "ts_ms": ts}

        logger.warning("AgentHealthCheck: HEARTBEAT_ALERT — raw_response=%r", truncated)
        self._log_heartbeat_alert(raw_response=truncated, ts_ms=ts)  # type: ignore[attr-defined]
        self._check_edge_transport_probes(ts_ms=ts)  # type: ignore[attr-defined]
        return {"status": HEARTBEAT_EVENT_TYPE_ALERT, "raw_response": truncated, "ts_ms": ts}

    def _log_heartbeat_alert(self, *, raw_response: str, ts_ms: int) -> None:
        """将 HEARTBEAT_ALERT 写入审计 Ledger。"""
        if self._ledger is None:  # type: ignore[attr-defined]
            return
        try:
            self._ledger.log_scheduler_event({  # type: ignore[attr-defined]
                "event_id": f"hb_alert_{uuid.uuid4().hex}",
                "task_id": "agent_health_check",
                "trigger_ts": ts_ms,
                "intent_id": None,
                "status": HEARTBEAT_EVENT_TYPE_ALERT,
                "reason_code": HEARTBEAT_EVENT_TYPE_ALERT,
                "mode_at_trigger": self._get_sys_mode(),  # type: ignore[attr-defined]
                "error_detail": raw_response,
                "ts_ms": ts_ms,
            })
        except Exception as exc:
            logger.error("Failed to log heartbeat alert: %s", exc)

    def _check_edge_transport_probes(self, *, ts_ms: int) -> dict[str, Any]:
        """TASK-0211 M23-B：执行 edge 传输层探针检查。"""
        if not _EDGE_PROBE_AVAILABLE or _build_edge_probes is None:
            logger.debug("_check_edge_transport_probes: edge_client not available, skipping")
            return {"edge_available": None, "down_services": [], "ts_ms": ts_ms}

        try:
            probe_statuses = _build_edge_probes(available=True)
            down_services = [
                svc_id
                for svc_id, probe in probe_statuses.items()
                if not probe.get("available", True)
            ]
            edge_available = len(down_services) == 0

            if not edge_available:
                logger.warning(
                    "AgentHealthCheck: EDGE_PROBE_ALERT — down_services=%r", down_services
                )
                self._log_edge_probe_alert(down_services=down_services, ts_ms=ts_ms)  # type: ignore[attr-defined]

            return {
                "edge_available": edge_available,
                "down_services": down_services,
                "ts_ms": ts_ms,
            }
        except Exception as exc:
            logger.warning("Edge transport probe check failed (degraded): %s", exc)
            return {"edge_available": None, "down_services": [], "ts_ms": ts_ms, "error": str(exc)}

    def _log_edge_probe_alert(self, *, down_services: list[str], ts_ms: int) -> None:
        """将 EDGE_PROBE_ALERT 写入审计 Ledger。"""
        if self._ledger is None:  # type: ignore[attr-defined]
            return
        try:
            self._ledger.log_scheduler_event({  # type: ignore[attr-defined]
                "event_id": f"edge_probe_alert_{uuid.uuid4().hex}",
                "task_id": "agent_health_check",
                "trigger_ts": ts_ms,
                "intent_id": None,
                "status": EDGE_PROBE_EVENT_TYPE_ALERT,
                "reason_code": EDGE_PROBE_EVENT_TYPE_ALERT,
                "mode_at_trigger": self._get_sys_mode(),  # type: ignore[attr-defined]
                "error_detail": f"edge transport down: {', '.join(down_services)}",
                "ts_ms": ts_ms,
            })
        except Exception as exc:
            logger.error("Failed to log edge probe alert: %s", exc)
