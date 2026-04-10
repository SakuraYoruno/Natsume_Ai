from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from host_brain.core.ids import now_ms
from host_brain.tribe.base_agent import TribeAgentDescriptor
from host_brain.tribe.events import emit_tribe_event
from host_brain.tribe.tribe_state import TribeStateRegistry, get_global_registry


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


@dataclass(slots=True)
class InitiativeGovernorAgent:
    descriptor: TribeAgentDescriptor = field(
        default_factory=lambda: TribeAgentDescriptor(
            agent_id="initiative_governor_agent",
            family="initiative_governor",
            execution_side="host",
            owner_domain="brain",
            capabilities=("motive_readiness_continuity", "initiative_gate", "decision_audit"),
        )
    )
    state_registry: TribeStateRegistry = field(default_factory=get_global_registry, repr=False)
    _recovery_mode: bool = field(default=False, init=False, repr=False)  # T-204：Glymphatic 恢复窗口标志

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    def register_subscriptions(self) -> None:
        try:
            from host_brain.app.internal_tribe_routes import get_tribe_event_bus  # noqa: PLC0415

            bus = get_tribe_event_bus()
            bus.subscribe("CIRCADIAN_PHASE_UPDATED", self._on_state_signal)
            bus.subscribe("PROPRIOCEPTION_UPDATED", self._on_state_signal)
            bus.subscribe("BOND_VECTOR_UPDATED", self._on_state_signal)
            bus.subscribe("GLYMPHATIC_CYCLE_COMPLETED", self._on_glymphatic_cycle)  # T-204
        except Exception:
            pass

    async def _on_glymphatic_cycle(self, event: Any) -> None:
        """T-204：处理 GLYMPHATIC_CYCLE_COMPLETED 事件，恢复窗口期间提高 DEFER 阈値。"""
        payload = dict(getattr(event, "payload", None) or {})
        executed = bool(payload.get("executed", False))
        self._recovery_mode = executed
        # 将恢复模式写入 state_registry，供外部读取
        self.state_registry.write(
            namespace="initiative_governor_agent",
            state={"recovery_mode": executed},
            writer="initiative_governor_agent",
            merge=True,
            reason="glymphatic_recovery_window",
        )

    async def _on_state_signal(self, event: Any) -> None:
        ts_ms = int(getattr(event, "ts_ms", 0) or now_ms())
        prev = self.state_registry.get_state("initiative_governor_agent") or {}
        state = dict(prev.get("state") or {})
        state["last_signal_type"] = str(getattr(event, "event_type", "") or "").strip().upper() or "UNKNOWN"
        state["last_signal_ts_ms"] = ts_ms
        self.state_registry.set_state("initiative_governor_agent", state, writer="initiative_governor_agent")

    def _resolve_motive(
        self,
        *,
        explicit_motive: float | None,
        context: Mapping[str, Any],
        vitals: Mapping[str, Any],
        bond_vector: Mapping[str, Any],
    ) -> float:
        if isinstance(explicit_motive, (int, float)):
            return _clamp(float(explicit_motive))
        loneliness = float(vitals.get("loneliness_level") or 0.0)
        followup_count = min(3.0, float(context.get("pending_followup_count") or 0.0))
        repair_confidence = float(bond_vector.get("repair_confidence") or 0.5)
        motive = 0.20 + loneliness * 0.45 + followup_count * 0.1 + max(0.0, 0.45 - repair_confidence) * 0.35
        return _clamp(motive)

    def _resolve_readiness(
        self,
        *,
        vitals: Mapping[str, Any],
        circadian_state: Mapping[str, Any],
        proprio_state: Mapping[str, Any],
    ) -> float:
        energy = _clamp(float(vitals.get("energy_reserve") or 0.6))
        stress = _clamp(float(vitals.get("stress_index") or 0.0))
        load = _clamp(float(proprio_state.get("cognitive_load_index") or 0.2))
        phase_multiplier = _clamp(float(circadian_state.get("proactive_multiplier") or 1.0), low=0.2, high=1.2)
        readiness = energy * (1.0 - stress * 0.72) * (1.0 - load * 0.80) * phase_multiplier
        return _clamp(readiness)

    def _resolve_continuity(
        self,
        *,
        context: Mapping[str, Any],
        proprio_state: Mapping[str, Any],
        bond_vector: Mapping[str, Any],
    ) -> float:
        continuity = float(proprio_state.get("continuity_hint") or 0.2)
        if context.get("active_topic_id"):
            continuity += 0.20
        if context.get("session_id") and str(context.get("session_id")) == str(proprio_state.get("active_session_id")):
            continuity += 0.15
        continuity += max(0.0, float(bond_vector.get("trust") or 0.0) - 0.5) * 0.25
        return _clamp(continuity)

    def evaluate_initiative(
        self,
        *,
        action: str,
        motive: float | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        from host_brain.tribe.circadian_agent import get_circadian_agent  # noqa: PLC0415
        from host_brain.tribe.proprioception_agent import get_proprioception_agent  # noqa: PLC0415
        from host_brain.tribe.social_brain_agent import get_social_brain_agent  # noqa: PLC0415

        resolved_context = dict(context or {})
        resolved_ts = now_ms()
        vitals = self.state_registry.aggregate_vital_signs()
        circadian_state = get_circadian_agent().export_runtime_state()
        proprio_state = get_proprioception_agent().export_runtime_state()
        bond_ctx = get_social_brain_agent().get_bond_context(
            session_id=str(session_id or ""),
            user_id=str(user_id or ""),
        )
        bond_vector = dict(bond_ctx.get("bond_vector") or {})

        motive_score = self._resolve_motive(
            explicit_motive=motive,
            context=resolved_context,
            vitals=vitals,
            bond_vector=bond_vector,
        )
        readiness_score = self._resolve_readiness(
            vitals=vitals,
            circadian_state=circadian_state,
            proprio_state=proprio_state,
        )
        continuity_score = self._resolve_continuity(
            context=resolved_context,
            proprio_state=proprio_state,
            bond_vector=bond_vector,
        )

        decision = "ALLOW"
        reason_code = "PASS_ALL_GATES"
        gate_state = str(vitals.get("gate_state") or "open").strip().lower()
        immune_risk = str(vitals.get("immune_risk_level") or "none").strip().lower()

        # T-209：读取 body_budget，体能耗尽时降低主动意愿
        body_budget = 0.5
        try:
            from host_brain.tribe.interoception_agent import get_interoception_agent  # noqa: PLC0415
            intero_state = get_interoception_agent().export_runtime_state()
            body_budget = _clamp(float(intero_state.get("body_budget") or 0.5))
        except Exception:
            pass
        if body_budget <= 0.25:
            motive_score = _clamp(motive_score * 0.6)

        # T-231：读取 sleep_pressure，夜间高睡眠压力时降低主动意愿
        sleep_pressure = _clamp(float(circadian_state.get("sleep_pressure") or 0.2))
        if sleep_pressure >= 0.65:
            # 睡眠压力高时，主动意愿打折（最大折扣 40%）
            sleep_discount = 1.0 - (sleep_pressure - 0.65) * 1.14  # 0.65→1.0, 0.90→0.71
            motive_score = _clamp(motive_score * max(0.60, sleep_discount))

        # T-204：恢复窗口期间，提高 readiness 阈値（更容易 DEFER）
        readiness_threshold = 0.45
        if self._recovery_mode:
            readiness_threshold = 0.60  # 恢复期提高阈値 +0.15

        if gate_state == "pause":
            decision, reason_code = "BLOCK", "GATE_PAUSE"
        elif immune_risk in {"high", "critical"}:
            decision, reason_code = "BLOCK", "IMMUNE_RISK_HIGH"
        elif motive_score < 0.35:
            decision, reason_code = "DEFER", "LOW_MOTIVE"
        elif readiness_score < 0.30:
            decision, reason_code = "BLOCK", "LOW_READINESS"
        elif readiness_score < readiness_threshold:
            decision, reason_code = "DEFER", "READINESS_THROTTLE" + ("_RECOVERY" if self._recovery_mode else "")
        elif continuity_score < 0.35:
            decision, reason_code = "DEFER", "LOW_CONTINUITY"

        trace_id = f"initiative_{resolved_ts}"
        result = {
            "action": str(action or "unknown").strip() or "unknown",
            "decision": decision,
            "reason_code": reason_code,
            "trace_id": trace_id,
            "scores": {
                "motive": round(motive_score, 4),
                "readiness": round(readiness_score, 4),
                "continuity": round(continuity_score, 4),
            },
            "thresholds": {"motive_min": 0.35, "readiness_min": readiness_threshold, "continuity_min": 0.35},
            "gate_state": gate_state,
            "immune_risk_level": immune_risk,
            "session_id": str(session_id or "").strip() or None,
            "user_id": str(user_id or "").strip() or None,
            "ts_ms": resolved_ts,
        }
        self.state_registry.set_state(
            "initiative_governor_agent",
            {
                "last_decision": result,
                "last_decision_trace_id": trace_id,
                "last_decision_ts_ms": resolved_ts,
            },
            writer="initiative_governor_agent",
        )
        emit_tribe_event(
            event_type="INITIATIVE_DECISION_MADE",
            source_agent="initiative_governor_agent",
            payload={
                "trace_id": trace_id,
                "action": result["action"],
                "decision": decision,
                "reason_code": reason_code,
                "scores": result["scores"],
                "session_id": result["session_id"],
                "user_id": result["user_id"],
            },
        )

        # T-270：BLOCK/DEFER 时联动 VestibularAgent 记录主动推迟事件（增加 initiative_defer_count）
        if decision in {"BLOCK", "DEFER"}:
            try:
                from host_brain.tribe.vestibular_agent import get_vestibular_agent  # noqa: PLC0415
                _vest = get_vestibular_agent()
                if hasattr(_vest, "state_registry"):
                    _vest_snap = _vest.state_registry.get_state("vestibular_agent") or {}
                    _vest_state = dict(_vest_snap.get("state") or {})
                    _vest_state["initiative_defer_count_10m"] = int(
                        _vest_state.get("initiative_defer_count_10m") or 0
                    ) + 1
                    _vest.state_registry.set_state(
                        "vestibular_agent", _vest_state, writer="initiative_governor_agent"
                    )
            except Exception:
                pass

        return result

    def get_last_decision(self) -> dict[str, Any]:
        snap = self.state_registry.get_state("initiative_governor_agent") or {}
        state = dict(snap.get("state") or {})
        return dict(state.get("last_decision") or {})

    def export_runtime_state(self) -> dict[str, Any]:
        """T-272：导出 InitiativeGovernorAgent 当前运行时状态快照。

        供 CortexAgent.collect_tribe_context() 和 chat_context_builder 使用，
        不触发任何副作用（不 emit 事件，不写 state_registry）。

        Returns:
            dict: 包含 last_decision/block_count/defer_count/approve_count 等关键字段。
        """
        snap = self.state_registry.get_state("initiative_governor_agent") or {}
        state = dict(snap.get("state") or {})
        last = dict(state.get("last_decision") or {})
        scores = dict(last.get("scores") or {})
        return {
            "last_decision": str(last.get("decision") or "APPROVE").strip().upper(),
            "last_reason_code": str(last.get("reason_code") or ""),
            "last_action": str(last.get("action") or ""),
            "last_motive_score": round(float(scores.get("motive") or 0.0), 4),
            "last_readiness_score": round(float(scores.get("readiness") or 0.0), 4),
            "last_continuity_score": round(float(scores.get("continuity") or 0.0), 4),
            "block_count": int(state.get("block_count") or 0),
            "defer_count": int(state.get("defer_count") or 0),
            "approve_count": int(state.get("approve_count") or 0),
            "recovery_mode": bool(self._recovery_mode),
            "last_ts_ms": int(last.get("ts_ms") or 0),
        }


_INITIATIVE_GOVERNOR_AGENT: InitiativeGovernorAgent | None = None


def get_initiative_governor_agent() -> InitiativeGovernorAgent:
    global _INITIATIVE_GOVERNOR_AGENT
    if _INITIATIVE_GOVERNOR_AGENT is None:
        _INITIATIVE_GOVERNOR_AGENT = InitiativeGovernorAgent()
    return _INITIATIVE_GOVERNOR_AGENT


def set_initiative_governor_agent(agent: InitiativeGovernorAgent | None) -> None:
    global _INITIATIVE_GOVERNOR_AGENT
    _INITIATIVE_GOVERNOR_AGENT = agent


__all__ = [
    "InitiativeGovernorAgent",
    "get_initiative_governor_agent",
    "set_initiative_governor_agent",
]
