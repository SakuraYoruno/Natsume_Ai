from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from host_brain.tribe.base_agent import TribeAgentDescriptor

logger = logging.getLogger("Natsume.tribe.ans_agent")
_GLYMPHATIC_LOG_COOLDOWN_MS = 30_000


def _default_state_registry() -> Any:
    try:
        from host_brain.tribe.tribe_state import get_global_registry  # noqa: PLC0415
        return get_global_registry()
    except Exception:
        return None


@dataclass(slots=True)
class ANSAgent:
    """自主神经系统族 Agent。

    职责：持续后台节律，驱动生命体征。
    - 加壳 NatsumeScheduler，不改造其内部逻辑
    - 提供 emit_from_thread()，供 threading.Timer 线程安全地向 asyncio 事件循环发布 Tribe 事件
    - 提供 emit_inner_world_ticked() / emit_body_sense_updated() 供 Skill 调用
    """

    descriptor: TribeAgentDescriptor = field(
        default_factory=lambda: TribeAgentDescriptor(
            agent_id="ans_agent",
            family="ans",
            capabilities=("scheduler", "mode_transition", "proactive_tick"),
        )
    )
    state_registry: Any = field(default=None)
    _loop: asyncio.AbstractEventLoop | None = field(default=None, init=False, repr=False)
    _bond_stage: str = field(default="friend", init=False, repr=False)  # T-37：记录当前关系阶段
    _recovery_mode: bool = field(default=False, init=False, repr=False)  # T-203：Glymphatic 恢复窗口标志
    _recovery_mode_ts_ms: int = field(default=0, init=False, repr=False)  # T-203：恢复窗口开始时间戳
    _last_glymphatic_log_signature: tuple[bool, int] | None = field(default=None, init=False, repr=False)
    _last_glymphatic_log_ts_ms: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.state_registry is None:
            self.state_registry = _default_state_registry()

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    # ── 事件循环注册 ──────────────────────────────────────────────────────────

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """注册主 asyncio 事件循环，供 Timer 线程跨线程发布事件使用。"""
        self._loop = loop
        logger.debug("ANSAgent: 主事件循环已注册")

    # ── 线程安全事件发布 ──────────────────────────────────────────────────────

    def emit_from_thread(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        """从 threading.Timer 线程安全地发布 Tribe 事件到 asyncio 事件循环。

        若事件循环未注册或已关闭，则静默降级（不抛出异常，避免影响 Scheduler 主流程）。
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            logger.debug("ANSAgent.emit_from_thread: 事件循环未就绪，跳过事件 %s", event_type)
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self._async_emit(event_type, payload or {}),
                loop,
            )
        except Exception as exc:
            logger.warning("ANSAgent.emit_from_thread 失败: event_type=%s exc=%s", event_type, exc)

    async def _async_emit(self, event_type: str, payload: dict[str, Any]) -> None:
        """异步发布事件到 TribeEventBus（在主 asyncio 线程中执行）。"""
        try:

            emit_tribe_event(
                event_type=event_type,
                source_agent="ans_agent",
                payload=payload,
            )
        except Exception as exc:
            logger.warning("ANSAgent._async_emit 失败: event_type=%s exc=%s", event_type, exc)

    # ── Skill 调用入口（供 inner_world_skill / body_sense_skill 调用）──────────

    def emit_inner_world_ticked(self, *, tick_result: dict[str, Any] | None = None) -> None:
        """inner_world_skill 执行完成后调用，发布 INNER_WORLD_TICKED 事件。

        可从任意线程调用（Timer 线程或 asyncio 线程均可）。

        T-43：自动将 _bond_stage 注入 payload，供 NatsumeScheduler 根据
        关系亲密度动态调整 tick 间隔（friend < close_friend < intimate）。

        T-148：在 tick 完成后，根据 tick_result 中的 proactive_voice_eligible 标志
        判断是否触发主动发声评估，打通 INNER_WORLD_TICKED → proactive_voice 联动链路。
        """
        payload = dict(tick_result or {})
        payload.setdefault("source", "inner_world_skill")
        # T-43：携带当前关系阶段，调度器可据此调整 tick 频率
        payload.setdefault("bond_stage", self._bond_stage)
        # T-203：恢复窗口期间，降低主动发声倾向（注入 proactive_multiplier）
        if self._recovery_mode:
            payload.setdefault("proactive_multiplier", 0.35)
            payload.setdefault("recovery_mode", True)
        self.emit_from_thread("INNER_WORLD_TICKED", payload)

        # T-148：INNER_WORLD_TICKED 后判断是否触发主动发声
        # 触发条件：tick_result 中 proactive_voice_eligible=True，
        # 或者 loneliness > 0.5 且关系阶段已存在
        try:
            _eligible = bool(payload.get("proactive_voice_eligible", False))
            _loneliness = float(payload.get("loneliness") or 0.0)
            _bond = str(self._bond_stage or "friend").lower()
            _voice_active = bool(getattr(self, "_voice_active", False))
            _should_trigger = (
                not _voice_active  # 语音管线空闲时才触发
                and (
                    _eligible
                    or _loneliness > 0.5
                )
            )
            if _should_trigger:

                decision = get_initiative_governor_agent().evaluate_initiative(
                    action="proactive_voice",
                    motive=_loneliness,
                    session_id=str(payload.get("session_id") or "default"),
                    user_id=str(payload.get("user_id") or "user_1"),
                    context={
                        "bond_stage": _bond,
                        "loneliness": _loneliness,
                        "trigger_source": "inner_world_ticked",
                        "pending_followup_count": int(payload.get("pending_followup_count") or 0),
                        "session_id": str(payload.get("session_id") or "default"),
                        "active_topic_id": str(payload.get("topic_id") or "").strip() or None,
                    },
                )
                if str(decision.get("decision") or "").upper() == "ALLOW":
                    self.trigger_proactive_voice(
                        user_id=str(payload.get("user_id") or "user_1"),
                        context={
                            "bond_stage": _bond,
                            "loneliness": _loneliness,
                            "trigger_source": "inner_world_ticked",
                            "initiative_trace_id": decision.get("trace_id"),
                        },
                    )
        except Exception as _pv_exc:
            logger.debug("ANSAgent emit_inner_world_ticked proactive_voice 触发失败（降级）: %s", _pv_exc)

    def emit_body_sense_updated(self, *, sense_result: dict[str, Any] | None = None) -> None:
        """body_sense_skill 执行完成后调用，发布 BODY_SENSE_UPDATED 事件。

        可从任意线程调用（Timer 线程或 asyncio 线程均可）。
        """
        payload = dict(sense_result or {})
        payload.setdefault("source", "body_sense_skill")
        self.emit_from_thread("BODY_SENSE_UPDATED", payload)

    def emit_loneliness_signal(
        self,
        *,
        loneliness: float,
        user_id: str = "user_1",
        source: str = "inner_world_skill",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """T-53：发布 LONELINESS_SIGNAL 事件，供各族 Agent 感知孤独感。

        inner_world_skill 在检测到孤独感超过阈值时调用此方法，
        触发 PainRewardAgent / LimbicAgent / PrefrontalAgent 等的联动响应。

        Args:
            loneliness: 孤独感强度，0.0 ~ 1.0。
            user_id: 用户 ID，默认 "user_1"。
            source: 信号来源，默认 "inner_world_skill"。
            metadata: 附加元数据（可选）。
        """
        clamped = max(0.0, min(1.0, float(loneliness)))
        payload: dict[str, Any] = {
            "loneliness": round(clamped, 4),
            "user_id": str(user_id or "user_1").strip() or "user_1",
            "source": str(source or "inner_world_skill").strip() or "inner_world_skill",
            "bond_stage": self._bond_stage,
        }
        if metadata:
            payload["metadata"] = dict(metadata)
        self.emit_from_thread("LONELINESS_SIGNAL", payload)
        logger.debug(
            "ANSAgent.emit_loneliness_signal: loneliness=%.3f user_id=%s bond_stage=%s",
            clamped, user_id, self._bond_stage,
        )

    def emit_task_success(
        self,
        *,
        task_type: str = "general",
        intensity: float = 0.35,
        user_id: str = "user_1",
        session_id: str = "default",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """T-63：发布 TASK_SUCCESS 事件，供 skill 完成任务时调用。

        触发 PainRewardAgent / LimbicAgent / SocialBrainAgent 等的联动响应：
        - PainRewardAgent：记录 task_success 奖励
        - LimbicAgent：情绪效价小幅提升
        - SocialBrainAgent：bond_trend_hint 设为 progress

        Args:
            task_type: 任务类型，如 "general"/"creative"/"helpful"/"humor" 等。
            intensity: 任务成功强度，0.0 ~ 1.0，默认 0.35。
            user_id: 用户 ID，默认 "user_1"。
            session_id: 会话 ID，默认 "default"。
            metadata: 附加元数据（可选）。
        """
        clamped = max(0.0, min(1.0, float(intensity)))
        payload: dict[str, Any] = {
            "task_type": str(task_type or "general").strip() or "general",
            "intensity": round(clamped, 4),
            "user_id": str(user_id or "user_1").strip() or "user_1",
            "session_id": str(session_id or "default").strip() or "default",
            "bond_stage": self._bond_stage,
        }
        if metadata:
            payload["metadata"] = dict(metadata)
        self.emit_from_thread("TASK_SUCCESS", payload)
        logger.debug(
            "ANSAgent.emit_task_success: task_type=%s intensity=%.3f bond_stage=%s",
            task_type, clamped, self._bond_stage,
        )

    def emit_humor_landed(
        self,
        *,
        humor_type: str = "general",
        intensity: float = 0.3,
        user_id: str = "user_1",
        session_id: str = "default",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """T-75：发布 HUMOR_LANDED 事件，供 skill 幽默成功时调用。

        触发 PainRewardAgent / LimbicAgent / EndocrineAgent 等的联动响应：
        - PainRewardAgent：记录 humor_landed 奖励
        - LimbicAgent：情绪效价 + 唤醒度小幅提升
        - EndocrineAgent：cortisol 轻微下降（通过 TASK_SUCCESS 路径）

        Args:
            humor_type: 幽默类型，如 "general"/"wordplay"/"self_deprecating"/"absurd" 等。
            intensity: 幽默成功强度，0.0 ~ 1.0，默认 0.3。
            user_id: 用户 ID，默认 "user_1"。
            session_id: 会话 ID，默认 "default"。
            metadata: 附加元数据（可选）。
        """
        clamped = max(0.0, min(1.0, float(intensity)))
        payload: dict[str, Any] = {
            "humor_type": str(humor_type or "general").strip() or "general",
            "intensity": round(clamped, 4),
            "user_id": str(user_id or "user_1").strip() or "user_1",
            "session_id": str(session_id or "default").strip() or "default",
            "bond_stage": self._bond_stage,
        }
        if metadata:
            payload["metadata"] = dict(metadata)
        self.emit_from_thread("HUMOR_LANDED", payload)
        logger.debug(
            "ANSAgent.emit_humor_landed: humor_type=%s intensity=%.3f bond_stage=%s",
            humor_type, clamped, self._bond_stage,
        )

    def emit_bond_progress(
        self,
        *,
        delta: float = 0.05,
        reason: str = "general",
        user_id: str = "user_1",
        session_id: str = "default",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """T-84：发布 BOND_PROGRESS 事件，供 skill 触发关系进展时调用。

        触发 SocialBrainAgent / LimbicAgent / CortexAgent / PainRewardAgent 等的联动响应：
        - SocialBrainAgent：bond_trend_hint 设为 progress，更新关系进展快照
        - LimbicAgent：情绪效价小幅提升
        - CortexAgent：更新 bond_progress 快照
        - PainRewardAgent：记录关系进展奖励

        Args:
            delta: 关系进展幅度，0.0 ~ 1.0，默认 0.05。
            reason: 进展原因，如 "task_success"/"humor_landed"/"deep_talk" 等。
            user_id: 用户 ID，默认 "user_1"。
            session_id: 会话 ID，默认 "default"。
            metadata: 附加元数据（可选）。
        """
        clamped = max(0.0, min(1.0, float(delta)))
        payload: dict[str, Any] = {
            "delta": round(clamped, 4),
            "reason": str(reason or "general").strip() or "general",
            "user_id": str(user_id or "user_1").strip() or "user_1",
            "session_id": str(session_id or "default").strip() or "default",
            "bond_stage": self._bond_stage,
        }
        if metadata:
            payload["metadata"] = dict(metadata)
        self.emit_from_thread("BOND_PROGRESS", payload)
        logger.debug(
            "ANSAgent.emit_bond_progress: delta=%.3f reason=%s bond_stage=%s",
            clamped, reason, self._bond_stage,
        )

    def emit_bond_regress(
        self,
        *,
        delta: float = 0.05,
        reason: str = "general",
        user_id: str = "user_1",
        session_id: str = "default",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """T-84：发布 BOND_REGRESS 事件，供 skill 触发关系退步时调用。

        触发 SocialBrainAgent / LimbicAgent / CortexAgent / PainRewardAgent 等的联动响应：
        - SocialBrainAgent：bond_trend_hint 设为 regress_risk
        - LimbicAgent：情绪效价轻微下降
        - CortexAgent：更新 bond_regress 快照

        Args:
            delta: 关系退步幅度，0.0 ~ 1.0，默认 0.05。
            reason: 退步原因，如 "conflict"/"long_silence"/"immune_block" 等。
            user_id: 用户 ID，默认 "user_1"。
            session_id: 会话 ID，默认 "default"。
            metadata: 附加元数据（可选）。
        """
        clamped = max(0.0, min(1.0, float(delta)))
        payload: dict[str, Any] = {
            "delta": round(clamped, 4),
            "reason": str(reason or "general").strip() or "general",
            "user_id": str(user_id or "user_1").strip() or "user_1",
            "session_id": str(session_id or "default").strip() or "default",
            "bond_stage": self._bond_stage,
        }
        if metadata:
            payload["metadata"] = dict(metadata)
        self.emit_from_thread("BOND_REGRESS", payload)
        logger.debug(
            "ANSAgent.emit_bond_regress: delta=%.3f reason=%s bond_stage=%s",
            clamped, reason, self._bond_stage,
        )

    # ── T-133：proactive_voice 族化入口 ──────────────────────────────────────

    def trigger_proactive_voice(
        self,
        *,
        user_id: str = "user_1",
        traits: Any = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """T-133：触发主动发声评估，将 PersonaProactiveVoice 族化到 ANSAgent。

        由 inner_world_skill / proactive_talk_skill 调用，ANSAgent 作为统一入口
        封装 PersonaProactiveVoice.evaluate_scene()，并在触发时发布
        PROACTIVE_VOICE_TRIGGERED 事件供 CortexAgent 感知。

        Args:
            user_id: 用户 ID，默认 "user_1"。
            traits: PersonalityTraits 实例（可选）。
            context: 场景上下文（可选），含 bond_stage / pending_followup_topics 等。

        Returns:
            {"triggered": bool, "expression": dict | None, "scene_type": str | None}
        """
        try:
            from host_brain.persona.proactive_voice import PersonaProactiveVoice  # noqa: PLC0415

            decision = get_initiative_governor_agent().evaluate_initiative(
                action="proactive_voice",
                motive=context.get("motive") if isinstance(context, dict) else None,
                user_id=str(user_id or "user_1").strip() or "user_1",
                session_id=str((context or {}).get("session_id") or "default"),
                context=dict(context or {}),
            )
            if str(decision.get("decision") or "").upper() != "ALLOW":
                return {
                    "triggered": False,
                    "expression": None,
                    "scene_type": None,
                    "reason": f"initiative_{str(decision.get('decision') or '').lower()}",
                    "reason_code": decision.get("reason_code"),
                    "trace_id": decision.get("trace_id"),
                }

            voice = PersonaProactiveVoice()
            ctx = dict(context or {})
            # 注入当前 bond_stage（从 ANSAgent 内部状态）
            ctx.setdefault("bond_stage", self._bond_stage.upper())
            expression = voice.evaluate_scene(
                user_id=str(user_id or "user_1").strip() or "user_1",
                traits=traits,
                context=ctx,
            )
            if expression is None:
                return {"triggered": False, "expression": None, "scene_type": None}

            expr_dict = expression.as_dict() if hasattr(expression, "as_dict") else {}
            scene_type = str(getattr(expression, "scene_type", "") or "")

            # 发布 PROACTIVE_VOICE_TRIGGERED 事件，供 CortexAgent 感知
            self.emit_from_thread(
                "PROACTIVE_VOICE_TRIGGERED",
                {
                    "user_id": str(user_id or "user_1"),
                    "scene_type": scene_type,
                    "emotion_tag": str(getattr(expression, "emotion_tag", "") or "NEUTRAL"),
                    "content_preview": str(getattr(expression, "content", "") or "")[:80],
                    "bond_stage": self._bond_stage,
                    "source": "ans_agent",
                },
            )
            logger.info(
                "ANSAgent.trigger_proactive_voice: scene=%s user_id=%s bond_stage=%s",
                scene_type, user_id, self._bond_stage,
            )
            return {"triggered": True, "expression": expr_dict, "scene_type": scene_type}
        except Exception as exc:
            logger.warning("ANSAgent.trigger_proactive_voice 失败（降级）: %s", exc)
            return {"triggered": False, "expression": None, "scene_type": None, "error": str(exc)}

    # ── T-134：meta_learning_loop 族化入口 ───────────────────────────────────

    def trigger_meta_learning_record(
        self,
        *,
        module_name: str,
        success: bool = True,
        skipped: bool = False,
        error: bool = False,
        latency_ms: float = 0.0,
        custom: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """T-134：记录模块调用指标到 MetaLearningLoop，将 meta_learning_loop 族化到 ANSAgent。

        由各 Skill / Agent 在模块调用后调用，ANSAgent 作为统一入口
        封装 MetaLearningLoop.record()，维护全局元学习循环单例。

        Args:
            module_name: 模块名称（如 "query_planner"/"reflection_chain" 等）。
            success: 是否成功，默认 True。
            skipped: 是否跳过，默认 False。
            error: 是否出错，默认 False。
            latency_ms: 调用延迟（毫秒），默认 0.0。
            custom: 自定义指标字典（可选）。

        Returns:
            {"status": "OK", "module_name": str}
        """
        try:
            from host_brain.core.meta_learning_loop import MetaLearningLoop  # noqa: PLC0415

            if not hasattr(self, "_meta_learning_loop") or self._meta_learning_loop is None:
                object.__setattr__(self, "_meta_learning_loop", MetaLearningLoop())
            loop: MetaLearningLoop = self._meta_learning_loop  # type: ignore[attr-defined]
            loop.record(
                str(module_name or "unknown").strip() or "unknown",
                success=bool(success),
                skipped=bool(skipped),
                error=bool(error),
                latency_ms=float(latency_ms),
                custom=dict(custom) if custom else None,
            )
            logger.debug(
                "ANSAgent.trigger_meta_learning_record: module=%s success=%s latency=%.1fms",
                module_name, success, latency_ms,
            )
            return {"status": "OK", "module_name": str(module_name or "")}
        except Exception as exc:
            logger.warning("ANSAgent.trigger_meta_learning_record 失败（降级）: %s", exc)
            return {"status": "ERROR", "module_name": str(module_name or ""), "error": str(exc)}

    def get_meta_learning_report(self, *, period_days: int = 7) -> dict[str, Any]:
        """T-134：生成元学习进化报告。

        Returns:
            EvolutionReport.as_dict() 或空字典（降级）。
        """
        try:
            if not hasattr(self, "_meta_learning_loop") or self._meta_learning_loop is None:
                return {"status": "NO_DATA", "modules_count": 0}
            loop = self._meta_learning_loop  # type: ignore[attr-defined]
            report = loop.generate_report(period_days=int(period_days))
            return report.as_dict() if hasattr(report, "as_dict") else {}
        except Exception as exc:
            logger.warning("ANSAgent.get_meta_learning_report 失败（降级）: %s", exc)
            return {"status": "ERROR", "error": str(exc)}

    # ── T-135：activity_monitor 族化入口 ─────────────────────────────────────

    def trigger_activity_monitor_event(
        self,
        *,
        event_type: str = "user_message_received",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """T-135：向 ActivityMonitor 发送活动事件，将 activity_monitor 族化到 ANSAgent。

        由消息路由层 / 语音管线在用户有交互时调用，ANSAgent 作为统一入口
        封装 ActivityMonitor.notify_user_interaction()，确保活动时间戳更新。

        Args:
            event_type: 事件类型，如 "user_message_received"/"voice_input_received"。
            payload: 事件载荷（可选）。

        Returns:
            {"status": "OK", "event_type": str} 或降级结果。
        """
        try:
            from host_brain.app.dependencies import get_activity_monitor  # noqa: PLC0415

            monitor = get_activity_monitor()
            if monitor is None:
                return {"status": "NO_MONITOR", "event_type": str(event_type or "")}

            # 通知用户有交互（更新活动时间戳）
            if hasattr(monitor, "notify_user_interaction"):
                monitor.notify_user_interaction()

            # 如果 monitor 支持 on_event，也触发事件驱动逻辑
            if hasattr(monitor, "_mode_service") and monitor._mode_service is not None:
                if hasattr(monitor._mode_service, "on_event"):
                    monitor._mode_service.on_event(
                        str(event_type or "user_message_received"),
                        dict(payload or {}),
                    )

            logger.debug(
                "ANSAgent.trigger_activity_monitor_event: event_type=%s", event_type,
            )
            return {"status": "OK", "event_type": str(event_type or "")}
        except Exception as exc:
            logger.warning("ANSAgent.trigger_activity_monitor_event 失败（降级）: %s", exc)
            return {"status": "ERROR", "event_type": str(event_type or ""), "error": str(exc)}

    def get_activity_monitor_status(self) -> dict[str, Any]:
        """T-135：获取 ActivityMonitor 当前状态。

        Returns:
            ActivityMonitor.get_status() 或空字典（降级）。
        """
        try:
            from host_brain.app.dependencies import get_activity_monitor  # noqa: PLC0415

            monitor = get_activity_monitor()
            if monitor is None:
                return {"status": "NO_MONITOR"}
            return monitor.get_status() if hasattr(monitor, "get_status") else {}
        except Exception as exc:
            logger.warning("ANSAgent.get_activity_monitor_status 失败（降级）: %s", exc)
            return {"status": "ERROR", "error": str(exc)}

    # ── T-136：mode_transition 族化入口 ──────────────────────────────────────

    def trigger_mode_transition(
        self,
        *,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """T-136：触发模式切换事件，将 mode_transition 族化到 ANSAgent。

        由 ActivityMonitor 检测到游戏进程 / 空闲超时等事件时调用，
        ANSAgent 作为统一入口封装 ModeTransitionService.on_event()。

        Args:
            event_type: 事件类型，如 "gaming_process_detected"/"idle_check" 等。
            payload: 事件载荷（可选）。

        Returns:
            ModeTransitionService.on_event() 结果或降级结果。
        """
        try:
            from host_brain.app.dependencies import get_mode_transition_service  # noqa: PLC0415

            service = get_mode_transition_service()
            if service is None:
                return {"status": "NO_SERVICE", "event_type": str(event_type or "")}

            result = service.on_event(
                str(event_type or "").strip(),
                dict(payload or {}),
            )
            if result is not None:
                # 模式切换成功，发布 MODE_TRANSITION 事件
                self.emit_from_thread(
                    "MODE_TRANSITION",
                    {
                        "from_mode": str(result.get("from_mode") or ""),
                        "to_mode": str(result.get("to_mode") or ""),
                        "source": str(result.get("source") or "ans_agent"),
                        "reason": str(result.get("reason") or ""),
                        "success": bool(result.get("success", True)),
                        "event_type": str(event_type or ""),
                    },
                )
                logger.info(
                    "ANSAgent.trigger_mode_transition: %s → %s (event=%s)",
                    result.get("from_mode"), result.get("to_mode"), event_type,
                )
            return result or {"status": "NO_TRANSITION", "event_type": str(event_type or "")}
        except Exception as exc:
            logger.warning("ANSAgent.trigger_mode_transition 失败（降级）: %s", exc)
            return {"status": "ERROR", "event_type": str(event_type or ""), "error": str(exc)}

    def get_mode_transition_status(self) -> dict[str, Any]:
        """T-136：获取 ModeTransitionService 当前状态。

        Returns:
            ModeTransitionService.get_current_status() 或空字典（降级）。
        """
        try:
            from host_brain.app.dependencies import get_mode_transition_service  # noqa: PLC0415

            service = get_mode_transition_service()
            if service is None:
                return {"status": "NO_SERVICE"}
            return service.get_current_status() if hasattr(service, "get_current_status") else {}
        except Exception as exc:
            logger.warning("ANSAgent.get_mode_transition_status 失败（降级）: %s", exc)
            return {"status": "ERROR", "error": str(exc)}

    def adjust_rhythm_for_high_interrupt(
        self,
        *,
        interrupt_count: int,
        source: str = "proprioception_agent",
    ) -> None:
        """T-247：高打断频率时调整 ANS 节律，降低主动发声倾向。

        由 ProprioceptionAgent 在 interrupt_count_5m >= 6 时调用，
        临时将 _recovery_mode 设为 True，使后续 emit_inner_world_ticked
        自动降低 proactive_multiplier，减少主动打扰。

        Args:
            interrupt_count: 5分钟内打断次数
            source: 调用来源标识
        """
        import math  # noqa: PLC0415
        # 打断次数越多，恢复模式持续时间越长（但最多标记一次，由下次 glymphatic 事件重置）
        self._recovery_mode = True
        logger.info(
            "ANSAgent.adjust_rhythm_for_high_interrupt: interrupt_count=%d source=%s → recovery_mode=True",
            interrupt_count, source,
        )
        # 发布 INNER_WORLD_TICKED 通知节律降低
        self.emit_from_thread(
            "INNER_WORLD_TICKED",
            {
                "source": "ans_agent_high_interrupt",
                "recovery_mode": True,
                "proactive_multiplier": max(0.20, 1.0 - math.log1p(interrupt_count - 5) * 0.25),
                "interrupt_count": interrupt_count,
                "trigger_source": str(source or "proprioception_agent"),
            },
        )
        # T-371：高打断频率（交感过度激活）时联动 InteroceptionAgent 注入内在压力
        # 频繁打断 = 交感神经持续激活 = 身体预算持续消耗（内在压力源）
        try:
            _intero = get_interoception_agent()
            if hasattr(_intero, "inject_external_stress"):
                _ans_stress = round(min(0.35, math.log1p(interrupt_count - 5) * 0.18), 4)
                _intero.inject_external_stress(
                    source=f"ans_high_interrupt:{interrupt_count}",
                    intensity=_ans_stress,
                )
        except Exception:
            pass

    def set_recovery_mode(self, enabled: bool, *, source: str = "external") -> None:
        """T-330：公开方法，供外部族安全地设置 recovery_mode 标志。

        避免外部族（DefaultModeAgent/CircadianAgent 等）直接访问私有属性 _recovery_mode。
        当 enabled=False 时（如日间相位恢复、高连续性恢复），退出恢复模式，
        恢复正常主动发声倾向。

        Args:
            enabled: True=进入恢复模式，False=退出恢复模式
            source: 调用来源标识（用于日志追踪）
        """
        prev = self._recovery_mode
        self._recovery_mode = bool(enabled)
        if prev != self._recovery_mode:
            logger.info(
                "ANSAgent.set_recovery_mode: %s → %s source=%s",
                prev, self._recovery_mode, source,
            )
            if not self._recovery_mode:
                # 退出恢复模式时发布通知，恢复正常节律
                self.emit_from_thread(
                    "INNER_WORLD_TICKED",
                    {
                        "source": "ans_agent_recovery_exit",
                        "recovery_mode": False,
                        "proactive_multiplier": 1.0,
                        "trigger_source": str(source or "external"),
                    },
                )
                # T-364：退出恢复模式时联动 LimbicAgent 触发轻度舒适情绪（恢复后的清爽感）
                try:
                    _limbic = get_limbic_agent()
                    if hasattr(_limbic, "drive_emotion_metabolism"):
                        _limbic.drive_emotion_metabolism(
                            valence=0.12,
                            arousal=0.05,
                            dominance=0.04,
                            blend_weight=0.10,
                            event_kind=f"ans_recovery_exit:{str(source or 'external')[:24]}",
                            source="ans_agent",
                        )
                except Exception:
                    pass
                # T-372：退出恢复模式时联动 InteroceptionAgent 降低 recovery_urgency
                # 副交感恢复激活 = 身体预算重新充盈，应主动释放内在压力
                try:
                    _intero = get_interoception_agent()
                    if hasattr(_intero, "reduce_recovery_urgency"):
                        _intero.reduce_recovery_urgency(
                            source=f"ans_parasympathetic_recovery:{str(source or 'external')[:24]}",
                            relief=0.30,
                        )
                except Exception:
                    pass

    def export_runtime_state(self) -> dict[str, Any]:
        """T-254/T-289：导出 ANSAgent 当前运行时状态快照，并写入 state_registry。

        供 CortexAgent.collect_tribe_context() 和上下文装配主链使用。
        T-289：同时将快照写入 state_registry["ans_agent"]，
        使 vital_signs.aggregate_vital_signs() 能读取 ans_recovery_mode/ans_proactive_multiplier。

        Returns:
            dict: 包含 bond_stage/recovery_mode/proactive_multiplier 等关键字段。
        """
        from host_brain.core.ids import now_ms as _now_ms  # noqa: PLC0415
        # 根据 bond_stage 推算 proactive_multiplier
        _stage_multiplier = {
            "friend": 0.85,
            "close_friend": 0.95,
            "intimate": 1.00,
        }
        proactive_multiplier = _stage_multiplier.get(str(self._bond_stage or "friend"), 0.85)
        if self._recovery_mode:
            proactive_multiplier = round(proactive_multiplier * 0.35, 4)
        snapshot = {
            "bond_stage": str(self._bond_stage or "friend"),
            "recovery_mode": bool(self._recovery_mode),
            "recovery_mode_ts_ms": int(self._recovery_mode_ts_ms or 0),
            "proactive_multiplier": round(proactive_multiplier, 4),
            "loop_registered": self._loop is not None,
            "ts_ms": _now_ms(),
        }
        # T-289：写入 state_registry，供 vital_signs 聚合读取
        try:
            if hasattr(self.state_registry, "write"):
                self.state_registry.write(
                    namespace="ans_agent",
                    state=snapshot,
                    writer="ans_agent",
                )
            elif hasattr(self.state_registry, "set_state"):
                self.state_registry.set_state("ans_agent", snapshot, writer="ans_agent")
        except Exception:
            pass
        return snapshot

    # ── T-37： TribeEventBus 订阅注册 ────────────────────────────────────────

    def register_subscriptions(self) -> None:
        """向 TribeEventBus 注册事件订阅 handler。

        T-37：订阅 BOND_STAGE_CHANGED，关系阶段跃迁时记录当前阶段，
        供后续 inner_world tick 节奏调整参考（亲密度越高， tick 越频繁）。
        T-119：订阅 VOICE_PIPELINE_STARTED/ENDED，语音管线状态变化时更新内部状态。
        T-120：订阅 CHANNEL_MESSAGE_RECEIVED，消息到达时更新活跃度感知。
        """
        try:
            from host_brain.app.internal_tribe_routes import get_tribe_event_bus  # noqa: PLC0415

            bus = get_tribe_event_bus()
            bus.subscribe("BOND_STAGE_CHANGED", self._on_bond_stage_changed)
            bus.subscribe("VOICE_PIPELINE_STARTED", self._on_voice_pipeline_started)   # T-119
            bus.subscribe("VOICE_PIPELINE_ENDED", self._on_voice_pipeline_ended)       # T-119
            bus.subscribe("CHANNEL_MESSAGE_RECEIVED", self._on_channel_message_received)  # T-120
            bus.subscribe("GLYMPHATIC_CYCLE_COMPLETED", self._on_glymphatic_cycle)  # T-203
            bus.subscribe("MOTOR_SPEAK_DISPATCHED", self._on_motor_speak_dispatched)  # D5
        except Exception as exc:
            logger.warning("ANSAgent 订阅注册失败（降级）: %s", exc)

    async def _on_glymphatic_cycle(self, event: Any) -> None:
        """T-203：处理 GLYMPHATIC_CYCLE_COMPLETED 事件，感知恢复窗口并调整节律。

        恢复窗口期间：将 _recovery_mode 设为 True，后续 emit_inner_world_ticked 会自动
        降低 proactive_multiplier 至 0.35，减少主动发声频率。
        恢复窗口关闭时：将 _recovery_mode 设为 False，恢复正常节律。
        """
        try:
            payload = dict(getattr(event, "payload", None) or {})
            executed = bool(payload.get("executed", False))
            ts_ms = int(getattr(event, "ts_ms", 0) or 0)
            consolidation_score = float(payload.get("consolidation_score") or 0.0)
            self._recovery_mode = executed
            self._recovery_mode_ts_ms = ts_ms
            signature = (executed, int(round(consolidation_score * 1000.0)))
            should_log = (
                self._last_glymphatic_log_signature != signature
                or self._last_glymphatic_log_ts_ms <= 0
                or (ts_ms - int(self._last_glymphatic_log_ts_ms)) >= _GLYMPHATIC_LOG_COOLDOWN_MS
            )
            if should_log:
                self._last_glymphatic_log_signature = signature
                self._last_glymphatic_log_ts_ms = ts_ms
                logger.info(
                    "ANSAgent._on_glymphatic_cycle: recovery_mode=%s consolidation_score=%.3f",
                    executed,
                    consolidation_score,
                )
        except Exception as exc:
            logger.debug("ANSAgent._on_glymphatic_cycle 异常: %s", exc)

    async def _on_motor_speak_dispatched(self, event: Any) -> None:
        """D5: 处理 MOTOR_SPEAK_DISPATCHED 事件，发声后更新自主神经节律状态。

        每次七海发出声音，自主神经系统应感知到"我刚刚说话了"，
        更新最近发声时间戳，调整主动发声节律（避免连续发声过密）。
        """
        try:
            payload = dict(getattr(event, "payload", None) or {})
            ok = bool(payload.get("ok", True))
            source = str(payload.get("source") or "unknown").strip()
            emotion_tag = str(payload.get("emotion_tag") or "NEUTRAL").strip().upper()
            ts_ms = int(getattr(event, "ts_ms", 0) or payload.get("ts_ms") or 0)

            if not ok:
                return  # 发声失败不更新节律

            # 更新最近发声时间戳（供 NatsumeScheduler 主动对话门控使用）
            self.update_last_message_ts()

            # 发声后短暂降低主动发声倾向（避免连续发声过密）
            # 主动发声（proactive_voice）后降低更多，对话回复（chat_commit）后降低较少
            _multiplier_reduction = 0.6 if source == "proactive_voice" else 0.85
            self.emit_from_thread(
                "INNER_WORLD_TICKED",
                {
                    "source": "ans_motor_speak",
                    "speak_dispatched": True,
                    "speak_source": source,
                    "speak_emotion_tag": emotion_tag,
                    "proactive_multiplier": _multiplier_reduction,
                    "ts_ms": ts_ms,
                },
            )
            logger.debug(
                "ANSAgent._on_motor_speak_dispatched: source=%s emotion=%s multiplier=%.2f",
                source, emotion_tag, _multiplier_reduction,
            )
        except Exception as exc:
            logger.debug("ANSAgent._on_motor_speak_dispatched 异常: %s", exc)

    async def _on_bond_stage_changed(self, event: Any) -> None:
        """处理 BOND_STAGE_CHANGED 事件：记录当前关系阶段。

        T-37：当 bond stage 发生跃迁时，更新 _bond_stage 字段。
        后续可在 emit_inner_world_ticked() 中将 bond_stage 带入 payload，
        供 NatsumeScheduler 根据关系亲密度动态调整 tick 间隔。
        """
        try:
            payload = dict(getattr(event, "payload", None) or {})
            to_stage = str(payload.get("to_stage") or "").strip().upper()
            if to_stage:
                self._bond_stage = to_stage.lower()
                logger.info(
                    "ANSAgent 关系阶段更新: bond_stage=%s", self._bond_stage,
                )
                # 将关系阶段变化广播到 INNER_WORLD_TICKED，供 LimbicAgent 感知
                self.emit_from_thread(
                    "INNER_WORLD_TICKED",
                    {
                        "source": "ans_agent",
                        "bond_stage_changed": True,
                        "bond_stage": self._bond_stage,
                        "from_stage": str(payload.get("from_stage") or "").strip().lower(),
                        "to_stage": self._bond_stage,
                    },
                )
        except Exception as exc:
            logger.warning("ANSAgent._on_bond_stage_changed 异常: %s", exc)

    async def _on_voice_pipeline_started(self, event: Any) -> None:
        """T-119：处理 VOICE_PIPELINE_STARTED 事件：记录语音管线启动状态。

        edge_client VoiceAgent 在语音管线启动时通过 EdgeTribeBridge 上报，
        ANSAgent 感知后可用于调整后台节律（如暂停主动对话检查）。
        """
        try:
            payload = dict(getattr(event, "payload", None) or {})
            request_id = str(payload.get("request_id") or "")
            session_id = str(payload.get("session_id") or "")
            stage = str(payload.get("stage") or "")
            logger.info(
                "ANSAgent 感知语音管线启动: request_id=%s session_id=%s stage=%s",
                request_id, session_id, stage,
            )
            # 写入 TribeStateRegistry，供后续调度门控读取
            try:
                registry = get_global_registry()
                registry.write(
                    namespace="ans_agent",
                    writer="ANSAgent._on_voice_pipeline_started",
                    state={
                        "voice_pipeline_active": True,
                        "voice_pipeline_request_id": request_id,
                        "voice_pipeline_session_id": session_id,
                        "voice_pipeline_stage": stage,
                    },
                )
            except Exception as _reg_exc:
                logger.debug("ANSAgent voice_pipeline_started 写入 registry 失败: %s", _reg_exc)
        except Exception as exc:
            logger.warning("ANSAgent._on_voice_pipeline_started 异常: %s", exc)

    async def _on_voice_pipeline_ended(self, event: Any) -> None:
        """T-119：处理 VOICE_PIPELINE_ENDED 事件：记录语音管线结束状态。

        语音管线结束后清除活跃标记，恢复正常后台节律。
        """
        try:
            payload = dict(getattr(event, "payload", None) or {})
            request_id = str(payload.get("request_id") or "")
            session_id = str(payload.get("session_id") or "")
            status = str(payload.get("status") or "")
            logger.info(
                "ANSAgent 感知语音管线结束: request_id=%s session_id=%s status=%s",
                request_id, session_id, status,
            )
            try:
                registry = get_global_registry()
                registry.write(
                    namespace="ans_agent",
                    writer="ANSAgent._on_voice_pipeline_ended",
                    state={
                        "voice_pipeline_active": False,
                        "voice_pipeline_request_id": request_id,
                        "voice_pipeline_session_id": session_id,
                        "voice_pipeline_last_status": status,
                    },
                )
            except Exception as _reg_exc:
                logger.debug("ANSAgent voice_pipeline_ended 写入 registry 失败: %s", _reg_exc)
        except Exception as exc:
            logger.warning("ANSAgent._on_voice_pipeline_ended 异常: %s", exc)

    async def _on_channel_message_received(self, event: Any) -> None:
        """T-120：处理 CHANNEL_MESSAGE_RECEIVED 事件：更新活跃度感知。

        edge_client ChannelAgent 在消息路由成功时上报，ANSAgent 感知后
        更新最近消息时间戳，供 NatsumeScheduler 主动对话门控（idle_threshold）使用。
        """
        try:
            payload = dict(getattr(event, "payload", None) or {})
            platform = str(payload.get("platform") or "")
            ts_ms = int(payload.get("ts_ms") or 0)
            routed_success = bool(payload.get("routed_success", True))
            if not routed_success:
                return  # 路由失败的消息不更新活跃度
            logger.debug(
                "ANSAgent 感知消息到达: platform=%s ts_ms=%d", platform, ts_ms,
            )
            try:
                from host_brain.core.ids import now_ms  # noqa: PLC0415
                registry = get_global_registry()
                registry.write(
                    namespace="ans_agent",
                    writer="ANSAgent._on_channel_message_received",
                    state={
                        "last_channel_message_ts_ms": ts_ms or now_ms(),
                        "last_channel_message_platform": platform,
                    },
                )
            except Exception as _reg_exc:
                logger.debug("ANSAgent channel_message_received 写入 registry 失败: %s", _reg_exc)
        except Exception as exc:
            logger.warning("ANSAgent._on_channel_message_received 异常: %s", exc)


# ── 单例管理 ──────────────────────────────────────────────────────────────────

_ANS_AGENT: ANSAgent | None = None


def get_ans_agent() -> ANSAgent:
    global _ANS_AGENT
    if _ANS_AGENT is None:
        _ANS_AGENT = ANSAgent()
    return _ANS_AGENT


def set_ans_agent(agent: ANSAgent | None) -> None:
    global _ANS_AGENT
    _ANS_AGENT = agent


__all__ = ["ANSAgent", "get_ans_agent", "set_ans_agent"]

# ── 跨模块引用（供测试 monkeypatch 使用）────────────────────────────────────
from host_brain.tribe.events import emit_tribe_event  # noqa: E402, F401
from host_brain.tribe.initiative_governor_agent import get_initiative_governor_agent  # noqa: E402, F401
from host_brain.tribe.interoception_agent import get_interoception_agent  # noqa: E402, F401
from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: E402, F401
from host_brain.tribe.tribe_state import get_global_registry  # noqa: E402, F401
