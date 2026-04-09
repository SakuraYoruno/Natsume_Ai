from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from host_brain.tribe.base_agent import TribeAgentDescriptor
from host_brain.tribe.tribe_state import TribeStateRegistry, get_global_registry


@dataclass(slots=True)
class CortexAgent:
    descriptor: TribeAgentDescriptor = field(
        default_factory=lambda: TribeAgentDescriptor(
            agent_id="cortex_agent",
            family="cortex",
            capabilities=("intent_route", "handoff", "reply"),
        )
    )
    state_registry: TribeStateRegistry = field(default_factory=get_global_registry, repr=False)  # T-33：使用全局单例

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

    # ── Supervisor：统一上下文收口 + 意图路由（T-11）────────────────────────

    def collect_tribe_context(
        self,
        *,
        request_body: Any | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Supervisor 收口：从各族 Agent 收集运行时上下文，供 chat_context_builder 使用。

        T-11：CortexAgent 作为 Supervisor，统一聚合：
        - LimbicAgent：情绪上下文、沉默意愿、body_sense 快照
        - HippocampusAgent：记忆召回摘要（仅元数据，不触发新召回）
        - SocialBrainAgent：Bond 阶段、关系状态
        - EndocrineAgent：内分泌门控状态（vital_signs）
        - TribeStateRegistry：aggregate_vital_signs() 统一生命体征
        - PainRewardAgent：完整享乐状态快照（T-20）

        返回字段：
            vital_signs (dict): 统一生命体征聚合（含 hedonic_tone/pain_level/reward_level/net_valence）
            emotion_context (dict): 情绪上下文快照
            bond_context (dict): Bond/关系上下文
            endocrine_gate (dict): 内分泌门控状态
            silence_willingness (float): 沉默意愿
            voice_emotion (dict | None): 最近语音情绪推断
            hedonic_state (dict): 完整享乐状态（pain_level/reward_level/net_valence/hedonic_tone/...）（T-20）
            tribe_context_source (str): 数据来源标记
        """
        result: dict[str, Any] = {
            "vital_signs": {},
            "emotion_context": {},
            "bond_context": {},
            "endocrine_gate": {},
            "silence_willingness": 0.5,
            "voice_emotion": None,
            "hedonic_state": {},
            "immune_state": {},  # T-38：ImmuneAgent 运行时状态（计数器/风险等级）
            "tribe_context_source": "cortex_agent",
        }

        # ── 1. 统一生命体征聚合（TribeStateRegistry）────────────────────────
        try:
            from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

            vital_signs = get_limbic_agent().state_registry.aggregate_vital_signs()
            result["vital_signs"] = vital_signs
            result["silence_willingness"] = float(vital_signs.get("silence_willingness") or 0.5)
            if vital_signs.get("voice_emotion_tag"):
                result["voice_emotion"] = {
                    "emotion_tag": vital_signs["voice_emotion_tag"],
                    "emotion_intensity": vital_signs.get("voice_emotion_intensity"),
                }
        except Exception:
            pass

        # ── 2. 情绪上下文（LimbicAgent）──────────────────────────────────────
        try:
            from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

            limbic = get_limbic_agent()
            if request_body is not None:
                emotion_ctx = limbic.get_emotion_context(request_body=request_body)
            else:
                # 无 request_body 时从 state_registry 读取最近快照
                last_ctx = limbic.state_registry.get_state("last_emotion_context")
                emotion_ctx = dict((last_ctx or {}).get("state") or {})
            result["emotion_context"] = emotion_ctx
        except Exception:
            pass

        # ── 3. Bond/关系上下文（SocialBrainAgent）────────────────────────────
        try:
            from host_brain.tribe.social_brain_agent import get_social_brain_agent  # noqa: PLC0415

            social = get_social_brain_agent()
            bond_ctx = social.get_bond_context(
                session_id=str(session_id or ""),
                user_id=str(user_id or ""),
            )
            result["bond_context"] = bond_ctx
        except Exception:
            pass

        # ── 4. 内分泌门控（EndocrineAgent）───────────────────────────────────
        try:
            from host_brain.tribe.endocrine_agent import get_endocrine_agent  # noqa: PLC0415

            endocrine = get_endocrine_agent()
            vitals = endocrine.resolve_vital_signs()
            result["endocrine_gate"] = vitals
        except Exception:
            pass

        # ── 5. 享乐状态（PainRewardAgent）T-20 ───────────────────────────────
        # 直接从 PainRewardAgent 读取完整 hedonic_state，
        # 包含 pain_level / reward_level / net_valence / hedonic_tone 等字段。
        # vital_signs 中已通过 aggregate_vital_signs() 包含摘要字段（T-19），
        # 此处提供完整快照供 chat_context_builder 精细化使用。
        try:
            from host_brain.tribe.pain_reward_agent import get_pain_reward_agent  # noqa: PLC0415

            pain_agent = get_pain_reward_agent()
            result["hedonic_state"] = pain_agent.get_hedonic_state()
        except Exception:
            pass

        # ── 6. 免疫状态（ImmuneAgent）T-38 ───────────────────────────────────
        # 读取 ImmuneAgent 运行时状态（计数器/风险等级），
        # 供 chat_context_builder 在构建 prompt 时参考安全状态。
        try:
            from host_brain.tribe.immune_agent import get_immune_agent  # noqa: PLC0415

            result["immune_state"] = get_immune_agent().export_runtime_state()
        except Exception:
            pass

        return result

    def route_intent(
        self,
        *,
        message: str,
        tribe_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Supervisor 意图路由：根据消息内容和族上下文决定路由策略。

        T-11：CortexAgent 作为 Supervisor，综合考虑：
        - 内分泌门控状态（gate_state）
        - 沉默意愿（silence_willingness）
        - Bond 阶段（bond_stage）
        - 消息长度和内容特征

        返回字段：
            route (str): "chat" | "skill" | "silent" | "throttle"
            reason (str): 路由原因说明
            gate_state (str): 当前门控状态
            confidence (float): 路由置信度 0.0 ~ 1.0
        """
        ctx = dict(tribe_context or {})
        vital_signs = dict(ctx.get("vital_signs") or {})
        gate_state = str(vital_signs.get("gate_state") or ctx.get("gate_state") or "open").strip().lower()
        silence_willingness = float(vital_signs.get("silence_willingness") or ctx.get("silence_willingness") or 0.5)
        bond_stage = str(vital_signs.get("bond_stage") or ctx.get("bond_stage") or "stranger").strip().lower()
        homeostasis_ok = bool(vital_signs.get("homeostasis_ok", True))

        # 门控暂停：内分泌系统要求暂停响应
        if gate_state == "pause":
            return {
                "route": "throttle",
                "reason": "endocrine_gate_pause",
                "gate_state": gate_state,
                "confidence": 0.95,
            }

        # 沉默意愿极高（>= 0.85）：主动沉默
        if silence_willingness >= 0.85:
            return {
                "route": "silent",
                "reason": "high_silence_willingness",
                "gate_state": gate_state,
                "confidence": round(silence_willingness, 4),
            }

        # 门控节流：降低响应频率
        if gate_state == "throttle" or not homeostasis_ok:
            return {
                "route": "throttle",
                "reason": "endocrine_gate_throttle" if gate_state == "throttle" else "homeostasis_disrupted",
                "gate_state": gate_state,
                "confidence": 0.75,
            }

        # 正常路由：chat
        return {
            "route": "chat",
            "reason": f"normal_flow (bond_stage={bond_stage})",
            "gate_state": gate_state,
            "confidence": 0.9,
        }

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

        T-33：新增订阅 BOND_PROGRESS / BOND_REGRESS，
        将关系变化写入 cortex_agent 命名空间快照，供路由决策参考。
        """
        try:
            from host_brain.app.internal_tribe_routes import get_tribe_event_bus  # noqa: PLC0415

            bus = get_tribe_event_bus()
            bus.subscribe("SILENCE_WILLINGNESS_CHANGED", self._on_silence_willingness_changed)
            bus.subscribe("EMOTION_STATE_CHANGED", self._on_emotion_state_changed)
            bus.subscribe("BOND_STAGE_CHANGED", self._on_bond_stage_changed)
            # T-33：订阅关系进展/退步事件，更新 cortex_agent 快照
            bus.subscribe("BOND_PROGRESS", self._on_bond_progress_cortex)
            bus.subscribe("BOND_REGRESS", self._on_bond_regress_cortex)
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
        """处理 BOND_STAGE_CHANGED 事件：记录 Bond 阶段变化。

        T-25：当 bond stage 发生跃迁时，发布 BOND_PROGRESS / BOND_REGRESS 事件，
        供 PainRewardAgent 接收并更新享乐状态（stage 跃迁强度高于普通 trend）。
        """
        try:
            import logging  # noqa: PLC0415
            from host_brain.tribe.events import emit_tribe_event  # noqa: PLC0415

            payload = dict(event.payload or {})
            _log = logging.getLogger("Natsume.tribe.cortex_agent")
            from_stage = str(payload.get("from_stage") or "").strip().upper()
            to_stage = str(payload.get("to_stage") or "").strip().upper()
            user_id = str(payload.get("user_id") or "").strip()
            _log.info(
                "CortexAgent 收到 BOND_STAGE_CHANGED: from=%s to=%s user_id=%s",
                from_stage, to_stage, user_id,
            )
            # Stage 跃迁顺序（越靠后越亲密）
            _STAGE_ORDER = [
                "STRANGER", "ACQUAINTANCE", "FRIEND",
                "CLOSE_FRIEND", "INTIMATE", "SOULMATE",
            ]
            from_idx = _STAGE_ORDER.index(from_stage) if from_stage in _STAGE_ORDER else -1
            to_idx = _STAGE_ORDER.index(to_stage) if to_stage in _STAGE_ORDER else -1
            if from_idx >= 0 and to_idx >= 0 and from_idx != to_idx:
                if to_idx > from_idx:
                    # Stage 升级 → BOND_PROGRESS（强度 0.7，高于普通 trend）
                    emit_tribe_event(
                        event_type="BOND_PROGRESS",
                        source_agent="cortex_agent",
                        payload={
                            "user_id": user_id,
                            "from_stage": from_stage,
                            "to_stage": to_stage,
                            "intensity": 0.7,
                            "reason": "stage_upgrade",
                        },
                    )
                    _log.info(
                        "CortexAgent 发布 BOND_PROGRESS (stage_upgrade): %s→%s user_id=%s",
                        from_stage, to_stage, user_id,
                    )
                else:
                    # Stage 降级 → BOND_REGRESS（强度 0.6）
                    emit_tribe_event(
                        event_type="BOND_REGRESS",
                        source_agent="cortex_agent",
                        payload={
                            "user_id": user_id,
                            "from_stage": from_stage,
                            "to_stage": to_stage,
                            "intensity": 0.6,
                            "reason": "stage_downgrade",
                        },
                    )
                    _log.info(
                        "CortexAgent 发布 BOND_REGRESS (stage_downgrade): %s→%s user_id=%s",
                        from_stage, to_stage, user_id,
                    )
        except Exception:
            pass


    async def _on_bond_progress_cortex(self, event: Any) -> None:
        """处理 BOND_PROGRESS 事件：将关系进展写入 cortex_agent 快照。

        T-33：更新 cortex_agent 命名空间的 bond_trend/bond_stage 字段，
        供 route_intent() 在路由决策时参考（关系越亲密，越倾向于 chat 路由）。
        """
        try:
            import logging  # noqa: PLC0415
            payload = dict(getattr(event, "payload", None) or {})
            bond_stage = str(payload.get("bond_stage") or "STRANGER").strip().upper() or "STRANGER"
            intensity = max(0.0, min(1.0, float(payload.get("intensity") or 0.3)))
            prev = self.state_registry.get_state("cortex_agent") or {}
            prev_state = dict(prev.get("state") or {})
            prev_state.update({
                "bond_stage": bond_stage.lower(),
                "bond_trend": "progress",
                "last_bond_progress_intensity": intensity,
            })
            self.state_registry.set_state("cortex_agent", prev_state, writer="cortex_agent")
            logging.getLogger("Natsume.tribe.cortex_agent").debug(
                "CortexAgent 快照更新 BOND_PROGRESS: stage=%s intensity=%.2f", bond_stage, intensity,
            )
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.cortex_agent").warning(
                "CortexAgent._on_bond_progress_cortex 异常: %s", exc
            )

    async def _on_bond_regress_cortex(self, event: Any) -> None:
        """处理 BOND_REGRESS 事件：将关系退步写入 cortex_agent 快照。

        T-33：更新 cortex_agent 命名空间的 bond_trend/bond_stage 字段，
        供 route_intent() 在路由决策时参考（关系退步时适当提高 throttle 阈值）。
        """
        try:
            import logging  # noqa: PLC0415
            payload = dict(getattr(event, "payload", None) or {})
            bond_stage = str(payload.get("bond_stage") or "STRANGER").strip().upper() or "STRANGER"
            intensity = max(0.0, min(1.0, float(payload.get("intensity") or 0.4)))
            prev = self.state_registry.get_state("cortex_agent") or {}
            prev_state = dict(prev.get("state") or {})
            prev_state.update({
                "bond_stage": bond_stage.lower(),
                "bond_trend": "regress",
                "last_bond_regress_intensity": intensity,
            })
            self.state_registry.set_state("cortex_agent", prev_state, writer="cortex_agent")
            logging.getLogger("Natsume.tribe.cortex_agent").debug(
                "CortexAgent 快照更新 BOND_REGRESS: stage=%s intensity=%.2f", bond_stage, intensity,
            )
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.cortex_agent").warning(
                "CortexAgent._on_bond_regress_cortex 异常: %s", exc
            )


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
