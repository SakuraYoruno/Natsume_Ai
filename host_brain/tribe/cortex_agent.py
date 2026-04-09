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
