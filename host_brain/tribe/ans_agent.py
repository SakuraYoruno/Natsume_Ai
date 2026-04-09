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
        """
        payload = dict(tick_result or {})
        payload.setdefault("source", "inner_world_skill")
        self.emit_from_thread("INNER_WORLD_TICKED", payload)

    def emit_body_sense_updated(self, *, sense_result: dict[str, Any] | None = None) -> None:
        """body_sense_skill 执行完成后调用，发布 BODY_SENSE_UPDATED 事件。

        可从任意线程调用（Timer 线程或 asyncio 线程均可）。
        """
        payload = dict(sense_result or {})
        payload.setdefault("source", "body_sense_skill")
        self.emit_from_thread("BODY_SENSE_UPDATED", payload)


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
