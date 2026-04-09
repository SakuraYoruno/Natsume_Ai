from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from host_brain.tribe.base_agent import TribeAgentDescriptor

logger = logging.getLogger("Natsume.tribe.ans_agent")


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
    _loop: asyncio.AbstractEventLoop | None = field(default=None, init=False, repr=False)
    _bond_stage: str = field(default="stranger", init=False, repr=False)  # T-37：记录当前关系阶段

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
            from host_brain.tribe.events import emit_tribe_event  # noqa: PLC0415

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
        关系亲密度动态调整 tick 间隔（stranger < acquaintance < friend < intimate）。
        """
        payload = dict(tick_result or {})
        payload.setdefault("source", "inner_world_skill")
        # T-43：携带当前关系阶段，调度器可据此调整 tick 频率
        payload.setdefault("bond_stage", self._bond_stage)
        self.emit_from_thread("INNER_WORLD_TICKED", payload)

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

    # ── T-37： TribeEventBus 订阅注册 ────────────────────────────────────────

    def register_subscriptions(self) -> None:
        """向 TribeEventBus 注册事件订阅 handler。

        T-37：订阅 BOND_STAGE_CHANGED，关系阶段跃迁时记录当前阶段，
        供后续 inner_world tick 节奏调整参考（亲密度越高， tick 越频繁）。
        """
        try:
            from host_brain.app.internal_tribe_routes import get_tribe_event_bus  # noqa: PLC0415

            bus = get_tribe_event_bus()
            bus.subscribe("BOND_STAGE_CHANGED", self._on_bond_stage_changed)
        except Exception as exc:
            logger.warning("ANSAgent 订阅注册失败（降级）: %s", exc)

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
