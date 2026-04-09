from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from host_brain.tribe.base_agent import TribeAgentDescriptor


@dataclass(slots=True)
class CortexAgent:
    descriptor: TribeAgentDescriptor = field(
        default_factory=lambda: TribeAgentDescriptor(
            agent_id="cortex_agent",
            family="cortex",
            capabilities=("intent_route", "handoff", "reply"),
        )
    )

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    def shutdown_bond_update_queue(
        self,
        *,
        logger: Any | None = None,
        timeout_s: float = 3.0,
    ) -> dict[str, Any]:
        from host_brain.app.dependencies import get_bond_update_queue  # noqa: PLC0415

        try:
            queue = get_bond_update_queue()
            drained = bool(queue.drain(timeout_s=float(timeout_s)))
            queue.shutdown()
            if logger is not None and hasattr(logger, "info"):
                logger.info("BondUpdateQueue graceful shutdown completed (drained=%s)", drained)
            return {"status": "OK", "drained": drained}
        except Exception as exc:
            if logger is not None and hasattr(logger, "warning"):
                logger.warning("BondUpdateQueue shutdown failed: %s", exc)
            return {"status": "DEGRADED", "reason_code": "BOND_QUEUE_SHUTDOWN_FAILED", "detail": str(exc)}

    async def post_process_bond_update(
        self,
        *,
        request_id: str,
        session_id: str,
        user_message: str,
        emotion_context: dict[str, Any] | None,
        ts_ms: int,
    ) -> dict[str, Any]:
        from host_brain.app.dependencies import get_bond_update_queue, get_ledger  # noqa: PLC0415
        from host_brain.bond.bond_dynamics import BondDynamicsWriter  # noqa: PLC0415
        from host_brain.bond.user_identity import resolve_bond_user_id  # noqa: PLC0415
        from host_brain.tribe.social_brain_agent import get_social_brain_agent  # noqa: PLC0415

        payload_emotion = dict(emotion_context or {})
        emotion_tag = str(payload_emotion.get("emotion_tag") or "NEUTRAL").strip().upper()
        emotion_intensity = float(payload_emotion.get("emotion_intensity") or 0.5)
        update_kind = BondDynamicsWriter.classify_interaction(
            text=user_message,
            emotion_intensity=emotion_intensity,
        )
        bond_payload = {
            "request_id": request_id,
            "intent_id": request_id,
            "session_id": session_id,
            "seq": 1,
            "user_id": resolve_bond_user_id(session_id=session_id),
            "topic_id": str(user_message or "")[:64],
            "capability_hint": "bond_memory",
            "update_kind": update_kind,
            "evidence_refs": [request_id],
            "emotion_tag": emotion_tag,
            "emotion_intensity": emotion_intensity,
            "ts_ms": int(ts_ms),
        }

        queue = get_bond_update_queue()
        queue_result = dict(queue.enqueue(bond_payload) or {})
        if str(queue_result.get("status") or "") == "QUEUED":
            return {"status": "QUEUED", "update_kind": update_kind}

        stage_engine = get_social_brain_agent().resolve_stage_engine()
        writer = BondDynamicsWriter(
            ledger=get_ledger(),
            stage_engine=stage_engine,
        )
        await asyncio.to_thread(writer.apply_update, **bond_payload)
        return {"status": "WRITTEN", "update_kind": update_kind}

    # ── Phase 5：对话生命周期事件发布 ────────────────────────────────────────

    def emit_conversation_started(
        self,
        *,
        user_id: str,
        session_id: str,
        request_id: str | None = None,
    ) -> None:
        """对话开始时调用，向 TribeEventBus 发布 CONVERSATION_STARTED 事件。

        LimbicAgent 订阅此事件以重置沉默意愿、捕获 body_sense 快照。
        可从同步上下文调用（内部使用 emit_tribe_event 同步写入 bus）。
        """
        try:
            from host_brain.tribe.events import emit_tribe_event  # noqa: PLC0415

            emit_tribe_event(
                event_type="CONVERSATION_STARTED",
                source_agent="cortex_agent",
                target_agent="limbic_agent",
                payload={
                    "user_id": str(user_id or ""),
                    "session_id": str(session_id or ""),
                    "request_id": str(request_id or ""),
                },
            )
        except Exception:
            pass

    def emit_conversation_ended(
        self,
        *,
        user_id: str,
        session_id: str,
        request_id: str | None = None,
        emotion_tag: str = "NEUTRAL",
        emotion_intensity: float = 0.5,
        summary: dict[str, Any] | None = None,
    ) -> None:
        """对话结束时调用，向 TribeEventBus 发布 CONVERSATION_ENDED 和 INTERACTION_RECORDED 事件。

        - CONVERSATION_ENDED → LimbicAgent（触发 session_end 收口）
        - INTERACTION_RECORDED → PrefrontalAgent / SocialBrainAgent（触发人格/关系更新）
        """
        try:
            from host_brain.tribe.events import emit_tribe_event  # noqa: PLC0415

            base_payload = {
                "user_id": str(user_id or ""),
                "session_id": str(session_id or ""),
                "request_id": str(request_id or ""),
                "emotion_tag": str(emotion_tag or "NEUTRAL").strip().upper(),
                "emotion_intensity": float(emotion_intensity),
            }
            emit_tribe_event(
                event_type="CONVERSATION_ENDED",
                source_agent="cortex_agent",
                target_agent="limbic_agent",
                payload=base_payload,
            )
            interaction_payload = dict(base_payload)
            interaction_payload.update(dict(summary or {}))
            emit_tribe_event(
                event_type="INTERACTION_RECORDED",
                source_agent="cortex_agent",
                payload=interaction_payload,
            )
        except Exception:
            pass


    # ── TribeEventBus 订阅注册（Phase 5）────────────────────────────────────

    def register_subscriptions(self) -> None:
        """向 TribeEventBus 注册事件订阅 handler。

        在 http_hooks.py lifespan 中调用，确保 Bus 的 dispatch_loop 启动后
        CortexAgent 的 handler 能被正确触发。
        """
        try:
            from host_brain.app.internal_tribe_routes import get_tribe_event_bus  # noqa: PLC0415

            bus = get_tribe_event_bus()
            bus.subscribe("SILENCE_WILLINGNESS_CHANGED", self._on_silence_willingness_changed)
            bus.subscribe("EMOTION_STATE_CHANGED", self._on_emotion_state_changed)
            bus.subscribe("BOND_STAGE_CHANGED", self._on_bond_stage_changed)
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.cortex_agent").warning(
                "CortexAgent 订阅注册失败（降级）: %s", exc
            )

    async def _on_silence_willingness_changed(self, event: Any) -> None:
        """处理 SILENCE_WILLINGNESS_CHANGED 事件：记录沉默意愿变化。"""
        try:
            import logging  # noqa: PLC0415
            payload = dict(event.payload or {})
            logging.getLogger("Natsume.tribe.cortex_agent").debug(
                "CortexAgent 收到 SILENCE_WILLINGNESS_CHANGED: willingness=%s",
                payload.get("willingness_level"),
            )
        except Exception:
            pass

    async def _on_emotion_state_changed(self, event: Any) -> None:
        """处理 EMOTION_STATE_CHANGED 事件：记录情绪状态变化。"""
        try:
            import logging  # noqa: PLC0415
            payload = dict(event.payload or {})
            logging.getLogger("Natsume.tribe.cortex_agent").debug(
                "CortexAgent 收到 EMOTION_STATE_CHANGED: emotion_tag=%s mood_state=%s",
                payload.get("emotion_tag"),
                payload.get("mood_state"),
            )
        except Exception:
            pass

    async def _on_bond_stage_changed(self, event: Any) -> None:
        """处理 BOND_STAGE_CHANGED 事件：记录 Bond 阶段变化。"""
        try:
            import logging  # noqa: PLC0415
            payload = dict(event.payload or {})
            logging.getLogger("Natsume.tribe.cortex_agent").info(
                "CortexAgent 收到 BOND_STAGE_CHANGED: from=%s to=%s user_id=%s",
                payload.get("from_stage"),
                payload.get("to_stage"),
                payload.get("user_id"),
            )
        except Exception:
            pass


_CORTEX_AGENT: CortexAgent | None = None


def get_cortex_agent() -> CortexAgent:
    global _CORTEX_AGENT
    if _CORTEX_AGENT is None:
        _CORTEX_AGENT = CortexAgent()
    return _CORTEX_AGENT


def set_cortex_agent(agent: CortexAgent | None) -> None:
    global _CORTEX_AGENT
    _CORTEX_AGENT = agent


__all__ = ["CortexAgent", "get_cortex_agent", "set_cortex_agent"]
