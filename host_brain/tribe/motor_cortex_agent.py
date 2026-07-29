from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Mapping

from host_brain.core.ids import now_ms
from host_brain.tribe.application_ports import TribeApplicationPorts
from host_brain.tribe.base_agent import TribeAgentDescriptor
from host_brain.tribe.events import emit_tribe_event
from host_brain.tribe.tribe_state import TribeStateRegistry, get_global_registry


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


@dataclass(slots=True)
class MotorCortexAgent:
    application_ports: TribeApplicationPorts | None = field(default=None, repr=False)
    descriptor: TribeAgentDescriptor = field(
        default_factory=lambda: TribeAgentDescriptor(
            agent_id="motor_cortex_agent",
            family="motor_cortex",
            execution_side="host",
            owner_domain="brain",
            capabilities=("action_plan", "command_dispatch", "execution_trace"),
        )
    )
    state_registry: TribeStateRegistry = field(default_factory=get_global_registry, repr=False)

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    def _legacy_execution_authority_retired(self) -> bool:
        """Whether the canonical Host has retired this EventBus executor.

        ``MotorCortexAgent`` predates immutable TaskGraphs.  Its plan cache,
        EventBus retry and organ fan-out could turn a transport failure into a
        second execution authority *and* an unscoped self-affect mutation.
        The production Host now owns action attempts, reconciliation and
        embodiment delivery through governed TaskGraph/Life receipts.  Keep
        this class available only for isolated legacy compatibility fixtures.
        """

        return (
            self.application_ports is not None
            and self.application_ports.uses_canonical_affective_life()
        )

    def _retired_execution_result(
        self,
        *,
        plan_id: str = "",
        ok: bool | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "reason_code": "LEGACY_MOTOR_CORTEX_EXECUTION_RETIRED",
            "authority": "GOVERNED_TASKGRAPH_AND_LIFE_DELIVERY",
            "plan_id": str(plan_id or "").strip(),
        }
        if ok is not None:
            result["ok"] = bool(ok)
        return result

    def register_subscriptions(self) -> None:
        if self._legacy_execution_authority_retired():
            # Edge feedback and retry are reconciled against the immutable
            # TaskGraph/Effect ledger, never against a process-local plan.
            return
        try:
            from host_brain.tribe.event_bus import get_tribe_event_bus  # noqa: PLC0415

            bus = get_tribe_event_bus()
            bus.subscribe("INITIATIVE_DECISION_MADE", self._on_initiative_decision)
            bus.subscribe("PLATFORM_CONNECTED", self._on_platform_signal)
            bus.subscribe("PLATFORM_DISCONNECTED", self._on_platform_signal)
            # T-199：订阅 Edge 侧执行反馈事件
            bus.subscribe("MOTOR_EXECUTION_FEEDBACK", self._on_execution_feedback)
            # T-206：订阅 Cerebellum 重试请求事件
            bus.subscribe("MOTOR_RETRY_REQUESTED", self._on_retry_requested)
        except Exception:
            pass

    def receive_execution_feedback(
        self,
        *,
        plan_id: str,
        ok: bool,
        latency_ms: int,
        reason: str | None = None,
        source: str = "edge_feedback",
    ) -> dict[str, Any]:
        """T-199：接收 Edge 侧执行反馈，更新 last_dispatch_outcome。

        由 MOTOR_EXECUTION_FEEDBACK 事件触发，将 Edge 侧实际执行结果回写到
        state_registry，并在失败时发布 MOTOR_EXECUTION_FAILED 事件以供
        CerebellumAgent 订阅并触发纠偏。

        Args:
            plan_id: Host 侧计划 ID，用于匹配 last_plan。
            ok: Edge 侧实际执行是否成功。
            latency_ms: Edge 侧执行耗时（毫秒）。
            reason: 失败原因（可选）。
            source: 反馈来源标识。

        Returns:
            {"ok": bool, "plan_id": str, "matched": bool}
        """
        resolved_plan_id = str(plan_id or "").strip()
        if self._legacy_execution_authority_retired():
            return self._retired_execution_result(
                plan_id=resolved_plan_id,
                ok=bool(ok),
            )
        resolved_ts = now_ms()

        # 从 state_registry 读取当前 last_dispatch_outcome
        snap = self.state_registry.get_state("motor_cortex_agent") or {}
        state = dict(snap.get("state") or {})
        last_dispatch = dict(state.get("last_dispatch_outcome") or {})
        last_plan = dict(state.get("last_plan") or {})

        # 按 plan_id 匹配，将 Edge 实际执行结果回写到 last_dispatch_outcome
        matched = (last_dispatch.get("plan_id") == resolved_plan_id or
                   last_plan.get("plan_id") == resolved_plan_id)
        updated_outcome = dict(last_dispatch)
        updated_outcome.update({
            "plan_id": resolved_plan_id,
            "ok": bool(ok),
            "edge_ok": bool(ok),
            "edge_latency_ms": max(0, int(latency_ms or 0)),
            "edge_reason": str(reason or "").strip() or None,
            "edge_feedback_source": str(source or "edge_feedback"),
            "edge_feedback_ts_ms": resolved_ts,
        })
        self.state_registry.write(
            namespace="motor_cortex_agent",
            state={"last_dispatch_outcome": updated_outcome},
            writer="motor_cortex_agent",
            merge=True,
            reason="motor_execution_feedback",
            decision_trace_id=resolved_plan_id,
        )

        # T-199：发布 MOTOR_EXECUTION_FEEDBACK 事件（供 CerebellumAgent 订阅）
        emit_tribe_event(
            event_type="MOTOR_EXECUTION_FEEDBACK",
            source_agent="motor_cortex_agent",
            payload={
                "plan_id": resolved_plan_id,
                "ok": bool(ok),
                "latency_ms": max(0, int(latency_ms or 0)),
                "reason": str(reason or "").strip() or None,
                "source": str(source or "edge_feedback"),
                "ts_ms": resolved_ts,
            },
        )

        # T-199：失败时额外发布 MOTOR_EXECUTION_FAILED
        if not ok:
            emit_tribe_event(
                event_type="MOTOR_EXECUTION_FAILED",
                source_agent="motor_cortex_agent",
                payload={
                    "plan_id": resolved_plan_id,
                    "reason": str(reason or "").strip() or "EDGE_EXECUTION_FAILED",
                    "latency_ms": max(0, int(latency_ms or 0)),
                    "source": str(source or "edge_feedback"),
                    "ts_ms": resolved_ts,
                },
            )
            # T-287：执行失败时联动 LimbicAgent 触发轻度挫败感情绪偏移
            try:
                _limbic = get_limbic_agent()
                if hasattr(_limbic, "drive_emotion_metabolism"):
                    _limbic.drive_emotion_metabolism(
                        valence=-0.12,
                        arousal=0.08,
                        dominance=-0.06,
                        blend_weight=0.15,
                        event_kind=f"motor_execution_failed:{str(reason or 'unknown')[:32]}",
                        source="motor_cortex_agent",
                    )
            except Exception:
                pass
            # T-303：执行失败时联动 EndocrineAgent 记录执行压力事件
            try:
                _endo = get_endocrine_agent()
                if hasattr(_endo, "record_stress_event"):
                    _endo.record_stress_event(
                        source=f"motor_execution_failed:{str(reason or 'unknown')[:32]}",
                        intensity=0.18,
                    )
            except Exception:
                pass
            # T-338：执行失败时联动 PainRewardAgent 记录行动痛苦（执行失败是行动受阻的痛苦）
            try:
                _pr = get_pain_reward_agent()
                if hasattr(_pr, "record_pain"):
                    _pr.record_pain(
                        source=f"motor_execution_failed:{str(reason or 'unknown')[:32]}",
                        intensity=0.22,
                        metadata={
                            "plan_id": resolved_plan_id,
                            "reason": str(reason or ""),
                            "latency_ms": max(0, int(latency_ms or 0)),
                        },
                    )
            except Exception:
                pass

        return {"ok": bool(ok), "plan_id": resolved_plan_id, "matched": matched}

    def plan_action(
        self,
        *,
        action: str,
        payload: Mapping[str, Any] | None = None,
        target_agent: str = "edge.channel",
        command_type: str = "motor_action_command",
        session_id: str | None = None,
        user_id: str | None = None,
        require_gate: bool = False,
        motive: float | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._legacy_execution_authority_retired():
            return {
                "status": "RETIRED",
                **self._retired_execution_result(),
            }
        resolved_action = str(action or "").strip() or "unknown_action"
        resolved_ts = now_ms()
        resolved_payload = dict(payload or {})
        resolved_context = dict(context or {})
        gate_result: dict[str, Any] | None = None
        if require_gate:
            try:

                gate_result = get_initiative_governor_agent().evaluate_initiative(
                    action=resolved_action,
                    motive=motive,
                    session_id=str(session_id or ""),
                    user_id=str(user_id or ""),
                    context=resolved_context,
                )
                if str(gate_result.get("decision") or "").upper() in {"BLOCK", "DEFER"}:
                    return {
                        "status": "BLOCKED",
                        "reason_code": gate_result.get("reason_code"),
                        "gate": gate_result,
                    }
            except Exception:
                gate_result = None

        plan_id = f"motor_plan_{resolved_ts}"
        priority = _clamp(float(resolved_context.get("priority") or 0.5))
        plan = {
            "plan_id": plan_id,
            "action": resolved_action,
            "command_type": str(command_type or "motor_action_command").strip().lower() or "motor_action_command",
            "target_agent": str(target_agent or "edge.channel").strip() or "edge.channel",
            "payload": resolved_payload,
            "session_id": str(session_id or "").strip() or None,
            "user_id": str(user_id or "").strip() or None,
            "priority": round(priority, 4),
            "steps": [
                {
                    "step_id": "dispatch_edge_command",
                    "target": str(target_agent or "edge.channel").strip() or "edge.channel",
                    "expect_ack": True,
                }
            ],
            "ts_ms": resolved_ts,
        }
        self.state_registry.write(
            namespace="motor_cortex_agent",
            state={
                "last_plan": plan,
                "last_plan_ts_ms": resolved_ts,
                "last_gate_result": gate_result or {},
            },
            writer="motor_cortex_agent",
            merge=True,
            reason="motor_action_plan",
            decision_trace_id=plan_id,
        )
        emit_tribe_event(
            event_type="MOTOR_ACTION_PLANNED",
            source_agent="motor_cortex_agent",
            payload={
                "plan_id": plan_id,
                "action": resolved_action,
                "target_agent": plan["target_agent"],
                "command_type": plan["command_type"],
                "priority": plan["priority"],
                "ts_ms": resolved_ts,
            },
        )
        return {"status": "PLANNED", "plan": plan, "gate": gate_result or {}}

    def dispatch_action(
        self,
        *,
        plan: Mapping[str, Any],
        executor: Any | None = None,
    ) -> dict[str, Any]:
        plan_dict = dict(plan or {})
        if self._legacy_execution_authority_retired():
            return self._retired_execution_result(
                plan_id=str(plan_dict.get("plan_id") or ""),
                ok=False,
            )
        if not plan_dict:
            return {"ok": False, "reason_code": "EMPTY_PLAN"}
        target_agent = str(plan_dict.get("target_agent") or "edge.channel").strip() or "edge.channel"
        command_type = str(plan_dict.get("command_type") or "motor_action_command").strip() or "motor_action_command"
        payload = dict(plan_dict.get("payload") or {})
        plan_id = str(plan_dict.get("plan_id") or f"motor_plan_{now_ms()}").strip()
        started = now_ms()
        result: dict[str, Any]
        if callable(executor):
            try:
                result = dict(executor(plan_dict) or {})
            except Exception as exc:
                result = {"ok": False, "reason": "EXECUTOR_ERROR", "detail": str(exc)}
        else:
            # Motor plans are proposals, not execution authority.  Edge effects
            # must be dispatched by the governed TaskGraph/Acceptance boundary
            # through an explicitly injected executor carrying node-bound
            # Preview proof.
            result = {
                "ok": False,
                "reason": (
                    "GOVERNED_EDGE_COMMAND_REQUIRED"
                    if target_agent.startswith("edge.")
                    else "NO_EXECUTOR"
                ),
            }
        latency_ms = max(0, now_ms() - started)
        outcome = {
            "plan_id": plan_id,
            "command_type": command_type,
            "target_agent": target_agent,
            "ok": bool(result.get("ok", False)),
            "accepted": bool(result.get("accepted", result.get("ok", False))),
            "dispatched": bool(result.get("dispatched", result.get("ok", False))),
            "reason": result.get("reason") or result.get("reason_code"),
            "latency_ms": latency_ms,
            "detail": result.get("detail"),
            "ts_ms": now_ms(),
        }
        self.state_registry.write(
            namespace="motor_cortex_agent",
            state={"last_dispatch_outcome": outcome, "last_dispatch_ts_ms": outcome["ts_ms"]},
            writer="motor_cortex_agent",
            merge=True,
            reason="motor_action_dispatch",
            decision_trace_id=plan_id,
        )
        emit_tribe_event(
            event_type="MOTOR_ACTION_DISPATCHED",
            source_agent="motor_cortex_agent",
            payload=outcome,
        )
        return outcome

    def dispatch_speak(
        self,
        *,
        text: str,
        emotion_tag: str = "NEUTRAL",
        emotion_intensity: float = 0.5,
        utterance_id: str | None = None,
        request_id: str | None = None,
        session_id: str | None = None,
        source: str = "motor_cortex",
        require_gate: bool = False,
        motive: float | None = None,
    ) -> dict[str, Any]:
        """Retire the mutable Tribe speech path in favour of Life delivery.

        A frozen ``ExpressionPlanV1`` must create the voice intent and durable
        receipt before an Edge adapter sees a command.  This old convenience
        method had neither a Life revision nor a delivery receipt and could
        turn an event-bus payload into speech, so it is intentionally an
        effect-free compatibility rejection.
        """
        return {
            "ok": False,
            "utterance_id": str(utterance_id or "").strip(),
            "reason_code": "LEGACY_TRIBE_DIRECT_SPEAK_RETIRED",
            "authority": "CANONICAL_LIFE_DELIVERY_PROTOCOL",
        }

    def export_runtime_state(self) -> dict[str, Any]:
        if self._legacy_execution_authority_retired():
            return {
                "last_plan_id": "",
                "last_action": "",
                "last_target_agent": "",
                "last_dispatch_ok": False,
                "last_dispatch_reason": "LEGACY_MOTOR_CORTEX_EXECUTION_RETIRED",
                "last_dispatch_latency_ms": 0,
                "last_updated_ts_ms": 0,
                "authority": "GOVERNED_TASKGRAPH_AND_LIFE_DELIVERY",
            }
        snap = self.state_registry.get_state("motor_cortex_agent") or {}
        state = dict(snap.get("state") or {})
        last_plan = dict(state.get("last_plan") or {})
        last_dispatch = dict(state.get("last_dispatch_outcome") or {})
        return {
            "last_plan_id": str(last_plan.get("plan_id") or ""),
            "last_action": str(last_plan.get("action") or ""),
            "last_target_agent": str(last_plan.get("target_agent") or ""),
            "last_dispatch_ok": bool(last_dispatch.get("ok", False)),
            "last_dispatch_reason": last_dispatch.get("reason"),
            "last_dispatch_latency_ms": int(last_dispatch.get("latency_ms") or 0),
            "last_updated_ts_ms": int(last_dispatch.get("ts_ms") or state.get("last_plan_ts_ms") or 0),
        }

    async def _on_execution_feedback(self, event: Any) -> None:
        """T-199：处理 MOTOR_EXECUTION_FEEDBACK 事件（来自 Edge 侧或 Cerebellum）。"""
        if self._legacy_execution_authority_retired():
            return
        payload = dict(getattr(event, "payload", None) or {})
        plan_id = str(payload.get("plan_id") or "").strip()
        ok = bool(payload.get("ok", True))
        latency_ms = int(payload.get("latency_ms") or 0)
        reason = str(payload.get("reason") or "").strip() or None
        source = str(payload.get("source") or "event_bus").strip()
        if plan_id:
            self.receive_execution_feedback(
                plan_id=plan_id,
                ok=ok,
                latency_ms=latency_ms,
                reason=reason,
                source=source,
            )

    async def _on_retry_requested(self, event: Any) -> None:
        """T-206：处理 MOTOR_RETRY_REQUESTED 事件，自动重新执行 dispatch_action。"""
        if self._legacy_execution_authority_retired():
            return
        payload = dict(getattr(event, "payload", None) or {})
        plan_id = str(payload.get("plan_id") or "").strip()
        if not plan_id:
            return
        # 从 state_registry 恢复 last_plan
        snap = self.state_registry.get_state("motor_cortex_agent") or {}
        state = dict(snap.get("state") or {})
        last_plan = dict(state.get("last_plan") or {})
        if last_plan.get("plan_id") == plan_id:
            import logging  # noqa: PLC0415
            _log = logging.getLogger("Natsume.tribe.motor_cortex_agent")
            _log.info(
                "MotorCortexAgent._on_retry_requested: plan_id=%s 自动重试 dispatch_action",
                plan_id,
            )
            self.dispatch_action(plan=last_plan)

    async def _on_initiative_decision(self, event: Any) -> None:
        payload = dict(getattr(event, "payload", None) or {})
        await asyncio.to_thread(
            self.state_registry.write,
            namespace="motor_cortex_agent",
            state={
                "last_initiative_decision": {
                    "trace_id": str(payload.get("trace_id") or "").strip() or None,
                    "decision": str(payload.get("decision") or "").strip().upper() or None,
                    "reason_code": str(payload.get("reason_code") or "").strip().upper() or None,
                    "ts_ms": int(getattr(event, "ts_ms", 0) or now_ms()),
                }
            },
            writer="motor_cortex_agent",
            merge=True,
            reason="initiative_decision_signal",
        )

    async def _on_platform_signal(self, event: Any) -> None:
        payload = dict(getattr(event, "payload", None) or {})
        await asyncio.to_thread(
            self.state_registry.write,
            namespace="motor_cortex_agent",
            state={
                "last_platform_signal": {
                    "event_type": str(getattr(event, "event_type", "") or "").strip().upper(),
                    "platform": str(payload.get("platform") or "").strip().lower() or None,
                    "ts_ms": int(getattr(event, "ts_ms", 0) or now_ms()),
                }
            },
            writer="motor_cortex_agent",
            merge=True,
            reason="platform_signal",
        )

    # ── 发声风格微调（A 级升级）──────────────────────────────────────────────

    def plan_speak_style(
        self,
        *,
        emotion_tag: str = "NEUTRAL",
        valence: float = 0.0,
        arousal: float = 0.3,
        tone_hint: str = "balanced",
    ) -> dict[str, Any]:
        """dispatch_speak 前调用 LLM 生成发声风格微调建议，注入 TTS prosody_hint。

        触发条件：arousal > 0.6 或 valence < -0.3（情绪显著）。
        调用：Flash 小模型（<100ms，延迟敏感）。
        开关：MOTOR_SPEAK_STYLE_ENABLED（默认 false）。

        Returns:
            {
                "speed": float,         # 语速 0.8~1.2
                "pause_hint": str,      # 停顿建议（短/中/长）
                "filler_hint": str,     # 语气词建议
                "source": str,          # "motor_llm"|"disabled"|"error"
            }
        """
        if self._legacy_execution_authority_retired():
            return {
                "speed": 1.0,
                "pause_hint": "中",
                "filler_hint": "",
                "source": "retired",
                "reason_code": "LEGACY_MOTOR_CORTEX_EXECUTION_RETIRED",
            }
        _significant = float(arousal or 0.3) > 0.6 or float(valence or 0.0) < -0.3
        if not _significant:
            return {"speed": 1.0, "pause_hint": "中", "filler_hint": "嗯", "source": "skipped"}

        import logging as _logging  # noqa: PLC0415
        _log = _logging.getLogger("Natsume.tribe.motor_cortex_agent")
        try:
            from host_brain.model_runtime.prompt_contract import resolve_prompt_contract_json  # noqa: PLC0415
            from host_brain.tribe.model_invocation import complete_tribe_json  # noqa: PLC0415

            _tpl = resolve_prompt_contract_json("motor_cortex_agent.speak_style") or {}
            _system_prompt = str(_tpl.get("system_prompt") or "")
            _user_tpl = str(_tpl.get("user_prompt_template") or "")
            if not _system_prompt or not _user_tpl:
                raise ValueError("motor_cortex_agent.speak_style 模板未配置")

            _user_prompt = _user_tpl.format(
                emotion_tag=str(emotion_tag or "NEUTRAL").upper(),
                valence=round(float(valence), 3),
                arousal=round(float(arousal), 3),
                tone_hint=str(tone_hint or "balanced"),
            )
            _parsed = complete_tribe_json(
                attempt_service=self.application_ports.require("model_attempt_service")(),
                semantic_id="motor_speak_style",
                system_prompt=_system_prompt,
                user_prompt=_user_prompt,
            )
            _speed = max(0.8, min(1.2, float(_parsed.get("speed") or 1.0)))
            _result = {
                "speed": round(_speed, 2),
                "pause_hint": str(_parsed.get("pause_hint") or "中").strip()[:4],
                "filler_hint": str(_parsed.get("filler_hint") or "嗯").strip()[:8],
                "source": "motor_llm",
            }
            _log.debug("MotorCortexAgent.plan_speak_style: %s", _result)
            return _result
        except Exception as exc:
            _log.debug("MotorCortexAgent.plan_speak_style 失败（降级）: %s", exc)
            return {"speed": 1.0, "pause_hint": "中", "filler_hint": "嗯", "source": "error"}


def get_motor_cortex_agent() -> MotorCortexAgent:
    from host_brain.tribe.runtime_context import (  # noqa: PLC0415
        require_active_tribe_agent,
    )

    return require_active_tribe_agent(
        "motor_cortex_agent",
        expected_type=MotorCortexAgent,
    )


__all__ = ["MotorCortexAgent", "get_motor_cortex_agent"]

# ── 跨模块引用（供测试 monkeypatch 使用）────────────────────────────────────
from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: E402, F401
from host_brain.tribe.endocrine_agent import get_endocrine_agent  # noqa: E402, F401
from host_brain.tribe.initiative_governor_agent import get_initiative_governor_agent  # noqa: E402, F401
from host_brain.tribe.pain_reward_agent import get_pain_reward_agent  # noqa: E402, F401
