"""Cortex route decider: 根据族上下文决定意图路由策略。

从 cortex_agent.py 拆分，负责综合内分泌门控、沉默意愿、Bond 阶段等
状态信息，决定路由策略（chat/skill/silent/throttle）。
"""

from __future__ import annotations

from typing import Any


def route_intent(
    *,
    message: str,
    tribe_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Supervisor 意图路由：根据消息内容和族上下文决定路由策略。"""
    ctx = dict(tribe_context or {})
    vital_signs = dict(ctx.get("vital_signs") or {})
    gate_state = str(vital_signs.get("gate_state") or ctx.get("gate_state") or "open").strip().lower()
    silence_willingness = float(vital_signs.get("silence_willingness") or ctx.get("silence_willingness") or 0.5)
    bond_stage = str(vital_signs.get("bond_stage") or ctx.get("bond_stage") or "stranger").strip().lower()
    homeostasis_ok = bool(vital_signs.get("homeostasis_ok", True))
    interoception_state = dict(ctx.get("interoception_state") or {})
    vestibular_state = dict(ctx.get("vestibular_state") or {})
    interoceptive_index = float(interoception_state.get("interoceptive_index") or vital_signs.get("interoceptive_index") or 0.0)
    vestibular_jitter = float(vestibular_state.get("jitter_index") or 0.0)

    if bool(ctx.get("initiative_request", False)):
        try:
            from host_brain.tribe.initiative_governor_agent import get_initiative_governor_agent  # noqa: PLC0415
            decision = get_initiative_governor_agent().evaluate_initiative(
                action=str(ctx.get("initiative_action") or "proactive_talk"),
                motive=ctx.get("motive"),
                session_id=str(ctx.get("session_id") or ""),
                user_id=str(ctx.get("user_id") or ""),
                context=ctx,
            )
            if decision.get("decision") == "BLOCK":
                return {"route": "throttle", "reason": f"initiative_block:{decision.get('reason_code')}", "gate_state": gate_state, "confidence": 0.92}
            if decision.get("decision") == "DEFER":
                return {"route": "silent", "reason": f"initiative_defer:{decision.get('reason_code')}", "gate_state": gate_state, "confidence": 0.78}
        except Exception:
            pass

    if gate_state == "pause":
        return {"route": "throttle", "reason": "endocrine_gate_pause", "gate_state": gate_state, "confidence": 0.95}

    if interoceptive_index >= 0.82:
        return {"route": "throttle", "reason": "interoceptive_overload", "gate_state": gate_state, "confidence": min(0.95, max(0.75, interoceptive_index))}

    if vestibular_jitter >= 0.78:
        return {"route": "throttle", "reason": "vestibular_instability", "gate_state": gate_state, "confidence": min(0.9, max(0.7, vestibular_jitter)), "vestibular_bias": "throttle", "vestibular_jitter": round(vestibular_jitter, 4)}

    route_bias = str(vestibular_state.get("route_bias") or "normal").strip().lower()
    damping_factor = float(vestibular_state.get("damping_factor") or 1.0)

    if route_bias == "safe_only":
        return {"route": "chat", "reason": "vestibular_safe_only", "gate_state": gate_state, "confidence": 0.85, "vestibular_bias": "safe_only", "vestibular_jitter": round(vestibular_jitter, 4), "damping_factor": round(damping_factor, 4)}

    if silence_willingness >= 0.85:
        return {"route": "silent", "reason": "high_silence_willingness", "gate_state": gate_state, "confidence": round(silence_willingness, 4), "vestibular_bias": route_bias, "vestibular_jitter": round(vestibular_jitter, 4), "damping_factor": round(damping_factor, 4)}

    if gate_state == "throttle" or not homeostasis_ok:
        return {"route": "throttle", "reason": "endocrine_gate_throttle" if gate_state == "throttle" else "homeostasis_disrupted", "gate_state": gate_state, "confidence": 0.75, "vestibular_bias": route_bias, "vestibular_jitter": round(vestibular_jitter, 4), "damping_factor": round(damping_factor, 4)}

    return {"route": "chat", "reason": f"normal_flow (bond_stage={bond_stage})", "gate_state": gate_state, "confidence": 0.9, "vestibular_bias": route_bias, "vestibular_jitter": round(vestibular_jitter, 4), "damping_factor": round(damping_factor, 4)}


__all__ = ["route_intent"]
