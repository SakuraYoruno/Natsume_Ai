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
            "bond_vector": {},
            "endocrine_gate": {},
            "silence_willingness": 0.5,
            "voice_emotion": None,
            "hedonic_state": {},
            "immune_state": {},  # T-38：ImmuneAgent 运行时状态（计数器/风险等级）
            "loneliness_level": 0.0,  # T-76：当前孤独感强度（来自 PainRewardAgent）
            "somatic_state": {},
            "peripheral_state": {},
            "circadian_state": {},
            "proprioception_state": {},
            "initiative_decision": {},
            "default_mode_state": {},
            "mirror_neuron_state": {},
            "memory_layers": {},
            "language_dual_system": {},
            "homeostasis_report": {},
            "interoception_state": {},
            "motor_cortex_state": {},
            "cerebellum_state": {},
            "glymphatic_state": {},
            "vestibular_state": {},
            "tribe_context_source": "cortex_agent",
        }

        # ── 1. 统一生命体征聚合（TribeStateRegistry）────────────────────────
        try:
            from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

            vital_signs = get_limbic_agent().state_registry.aggregate_vital_signs()
            result["vital_signs"] = vital_signs
            result["homeostasis_report"] = dict(vital_signs.get("homeostasis_report") or {})
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
            result["bond_vector"] = dict(bond_ctx.get("bond_vector") or {})
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

        # ── 7. 孤独感强度（PainRewardAgent）T-76 ─────────────────────────────
        # 读取当前孤独感强度，供 chat_context_builder 在高孤独感时触发主动关怀策略。
        try:
            from host_brain.tribe.pain_reward_agent import get_pain_reward_agent  # noqa: PLC0415

            result["loneliness_level"] = get_pain_reward_agent().get_loneliness_level()
        except Exception:
            pass

        # ── 8. 躯体状态（SomaticAgent）───────────────────────────────────────
        try:
            from host_brain.tribe.somatic_agent import get_somatic_agent  # noqa: PLC0415

            result["somatic_state"] = get_somatic_agent().export_runtime_state()
        except Exception:
            pass

        # ── 9. 外周状态（PeripheralAgent）────────────────────────────────────
        try:
            from host_brain.tribe.peripheral_agent import get_peripheral_agent  # noqa: PLC0415

            result["peripheral_state"] = get_peripheral_agent().export_runtime_state()
        except Exception:
            pass

        # ── 10. bond_trend_hint（SocialBrainAgent 快照）T-54 ─────────────────
        # 读取 SocialBrainAgent 写入的 bond_trend_hint 字段，
        # 供 chat_context_builder 在构建 prompt 时参考任务成功带来的关系倾向。
        try:
            from host_brain.tribe.tribe_state import get_global_registry  # noqa: PLC0415

            _sba_snap = get_global_registry().get_state("social_brain_agent") or {}
            _sba_state = dict(_sba_snap.get("state") or {})
            _hint = str(_sba_state.get("bond_trend_hint") or "").strip().lower()
            if _hint:
                result["bond_trend_hint"] = _hint
                result["bond_trend_hint_ts_ms"] = int(_sba_state.get("bond_trend_hint_ts_ms") or 0)
        except Exception:
            pass

        # ── 11. Circadian / Proprioception / InitiativeGovernor──────────────
        try:
            from host_brain.tribe.circadian_agent import get_circadian_agent  # noqa: PLC0415
            from host_brain.tribe.proprioception_agent import get_proprioception_agent  # noqa: PLC0415
            from host_brain.tribe.initiative_governor_agent import get_initiative_governor_agent  # noqa: PLC0415

            result["circadian_state"] = get_circadian_agent().export_runtime_state()
            result["proprioception_state"] = get_proprioception_agent().export_runtime_state()
            result["initiative_decision"] = get_initiative_governor_agent().get_last_decision()
        except Exception:
            pass

        # ── 12. DefaultMode / MirrorNeuron（内在生活 + 共情共振）──────────────
        try:
            from host_brain.tribe.default_mode_agent import get_default_mode_agent  # noqa: PLC0415
            from host_brain.tribe.mirror_neuron_agent import get_mirror_neuron_agent  # noqa: PLC0415

            result["default_mode_state"] = get_default_mode_agent().export_runtime_state()
            result["mirror_neuron_state"] = get_mirror_neuron_agent().export_runtime_state()
        except Exception:
            pass

        # ── 13. Memory Layers（三分层记忆快照）────────────────────────────────
        try:
            from host_brain.tribe.hippocampus_agent import get_hippocampus_agent  # noqa: PLC0415

            result["memory_layers"] = get_hippocampus_agent().resolve_memory_layer_snapshot()
        except Exception:
            pass

        # ── 14. Language Dual System（Wernicke/Broca）──────────────────────
        try:
            from host_brain.tribe.language_system_agent import get_language_system_agent  # noqa: PLC0415

            language_agent = get_language_system_agent()
            latest_state_row = language_agent.state_registry.get_state("language_system_agent") or {}
            latest_state = dict(latest_state_row.get("state") or {})
            dual_snapshot = dict(latest_state.get("last_dual_system") or {})

            if not dual_snapshot:
                user_text = ""
                if isinstance(request_body, dict):
                    user_text = str(
                        request_body.get("user_message")
                        or request_body.get("message")
                        or request_body.get("text")
                        or ""
                    ).strip()
                elif request_body is not None:
                    user_text = str(
                        getattr(request_body, "user_message", None)
                        or getattr(request_body, "message", None)
                        or getattr(request_body, "text", None)
                        or ""
                    ).strip()
                if user_text:
                    vital_signs = dict(result.get("vital_signs") or {})
                    dual_snapshot = language_agent.resolve_dual_system(
                        user_text=user_text,
                        bond_stage=str(vital_signs.get("bond_stage") or "stranger"),
                        emotion_tag=str(vital_signs.get("emotion_tag") or "NEUTRAL"),
                        loneliness_level=float(vital_signs.get("loneliness_level") or 0.0),
                        high_loneliness_flag=bool(vital_signs.get("high_loneliness_flag", False)),
                        response_goal="chat_reply",
                    )
            result["language_dual_system"] = dual_snapshot
        except Exception:
            pass

        # ── 15. Homeostasis fallback（当 vital_signs 未携带报告时）───────────
        if not result.get("homeostasis_report"):
            try:
                result["homeostasis_report"] = self.state_registry.get_homeostasis_report()
            except Exception:
                pass

        # ── 16. 扩充族（Interoception/Motor/Cerebellum/Glymphatic/Vestibular）──
        try:
            from host_brain.tribe.interoception_agent import get_interoception_agent  # noqa: PLC0415
            from host_brain.tribe.motor_cortex_agent import get_motor_cortex_agent  # noqa: PLC0415
            from host_brain.tribe.cerebellum_agent import get_cerebellum_agent  # noqa: PLC0415
            from host_brain.tribe.glymphatic_agent import get_glymphatic_agent  # noqa: PLC0415
            from host_brain.tribe.vestibular_agent import get_vestibular_agent  # noqa: PLC0415

            result["interoception_state"] = get_interoception_agent().export_runtime_state()
            result["motor_cortex_state"] = get_motor_cortex_agent().export_runtime_state()
            result["cerebellum_state"] = get_cerebellum_agent().export_runtime_state()
            result["glymphatic_state"] = get_glymphatic_agent().export_runtime_state()
            result["vestibular_state"] = get_vestibular_agent().export_runtime_state()
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
        interoception_state = dict(ctx.get("interoception_state") or {})
        vestibular_state = dict(ctx.get("vestibular_state") or {})
        interoceptive_index = float(interoception_state.get("interoceptive_index") or vital_signs.get("interoceptive_index") or 0.0)
        vestibular_jitter = float(vestibular_state.get("jitter_index") or 0.0)

        # 系统层门禁：主动行为统一经过 motive/readiness/continuity 三问。
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
                    return {
                        "route": "throttle",
                        "reason": f"initiative_block:{decision.get('reason_code')}",
                        "gate_state": gate_state,
                        "confidence": 0.92,
                    }
                if decision.get("decision") == "DEFER":
                    return {
                        "route": "silent",
                        "reason": f"initiative_defer:{decision.get('reason_code')}",
                        "gate_state": gate_state,
                        "confidence": 0.78,
                    }
            except Exception:
                pass

        # 门控暂停：内分泌系统要求暂停响应
        if gate_state == "pause":
            return {
                "route": "throttle",
                "reason": "endocrine_gate_pause",
                "gate_state": gate_state,
                "confidence": 0.95,
            }

        if interoceptive_index >= 0.82:
            return {
                "route": "throttle",
                "reason": "interoceptive_overload",
                "gate_state": gate_state,
                "confidence": min(0.95, max(0.75, interoceptive_index)),
            }

        if vestibular_jitter >= 0.78:
            return {
                "route": "throttle",
                "reason": "vestibular_instability",
                "gate_state": gate_state,
                "confidence": min(0.9, max(0.7, vestibular_jitter)),
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
            # T-58：订阅免疫拦截事件，写入 cortex_agent 快照
            bus.subscribe("IMMUNE_BLOCK", self._on_immune_block)
            # T-65：订阅孤独感信号，写入 cortex_agent 快照
            bus.subscribe("LONELINESS_SIGNAL", self._on_loneliness_signal)
            # T-88：订阅任务成功事件，写入 cortex_agent 快照
            bus.subscribe("TASK_SUCCESS", self._on_task_success_cortex)
            # T-118：订阅躯干族平台连接/断线事件（来自 edge_client PeripheralAgent）
            bus.subscribe("PLATFORM_CONNECTED", self._on_platform_connected)
            bus.subscribe("PLATFORM_DISCONNECTED", self._on_platform_disconnected)
            bus.subscribe("CIRCADIAN_PHASE_UPDATED", self._on_circadian_phase_updated)
            bus.subscribe("PROPRIOCEPTION_UPDATED", self._on_proprioception_updated)
            bus.subscribe("INITIATIVE_DECISION_MADE", self._on_initiative_decision_made)
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

    async def _on_immune_block(self, event: Any) -> None:
        """处理 IMMUNE_BLOCK 事件：将免疫拦截记录写入 cortex_agent 快照。

        T-58：ImmuneAgent 拦截有害内容时发布此事件，
        CortexAgent 将拦截信息写入快照，供路由决策和诊断使用。
        """
        try:
            import logging  # noqa: PLC0415
            payload = dict(getattr(event, "payload", None) or {})
            ts_ms = int(getattr(event, "ts_ms", 0) or 0)
            self.state_registry.set_state(
                "cortex_agent",
                {
                    "last_immune_block_ts_ms": ts_ms,
                    "last_immune_block_reason": str(payload.get("reason") or "").strip(),
                    "last_immune_block_score": float(payload.get("score") or 0.0),
                    "last_immune_block_session_id": str(payload.get("session_id") or "").strip(),
                },
                writer="cortex_agent",
            )
            logging.getLogger("Natsume.tribe.cortex_agent").debug(
                "CortexAgent IMMUNE_BLOCK: reason=%s score=%.3f",
                payload.get("reason"), payload.get("score"),
            )
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.cortex_agent").warning(
                "CortexAgent._on_immune_block 异常: %s", exc
            )

    async def _on_loneliness_signal(self, event: Any) -> None:
        """处理 LONELINESS_SIGNAL 事件：将孤独感数据写入 cortex_agent 快照。

        T-65：记录最近一次孤独感信号的强度和时间戳，
        供路由决策和诊断使用（高孤独感时可触发主动关怀策略）。
        """
        try:
            import logging  # noqa: PLC0415
            payload = dict(getattr(event, "payload", None) or {})
            loneliness = max(0.0, min(1.0, float(payload.get("loneliness") or 0.0)))
            ts_ms = int(getattr(event, "ts_ms", 0) or 0)
            bond_stage = str(payload.get("bond_stage") or "").strip().lower()
            self.state_registry.set_state(
                "cortex_agent",
                {
                    "last_loneliness_level": round(loneliness, 4),
                    "last_loneliness_ts_ms": ts_ms,
                    "last_loneliness_bond_stage": bond_stage,
                    # 高孤独感（>0.7）标记，供路由决策参考
                    "high_loneliness_flag": loneliness > 0.7,
                },
                writer="cortex_agent",
            )
            logging.getLogger("Natsume.tribe.cortex_agent").debug(
                "CortexAgent LONELINESS_SIGNAL: loneliness=%.3f bond_stage=%s",
                loneliness, bond_stage,
            )
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.cortex_agent").warning(
                "CortexAgent._on_loneliness_signal 异常: %s", exc
            )

    async def _on_task_success_cortex(self, event: Any) -> None:
        """处理 TASK_SUCCESS 事件：将任务成功写入 cortex_agent 快照。

        T-88：任务成功时记录最近一次成功的时间戳和强度，
        供路由决策参考（任务成功后适当提升 chat 路由置信度）。
        """
        try:
            import logging  # noqa: PLC0415
            payload = dict(getattr(event, "payload", None) or {})
            session_id = str(payload.get("session_id") or "").strip()
            intensity = max(0.0, min(1.0, float(payload.get("intensity") or 0.35)))
            ts_ms = int(getattr(event, "ts_ms", 0) or 0)
            prev = self.state_registry.get_state("cortex_agent") or {}
            prev_state = dict(prev.get("state") or {})
            prev_state.update({
                "last_task_success_ts_ms": ts_ms,
                "last_task_success_intensity": round(intensity, 4),
                "last_task_success_session_id": session_id,
            })
            self.state_registry.set_state("cortex_agent", prev_state, writer="cortex_agent")
            logging.getLogger("Natsume.tribe.cortex_agent").debug(
                "CortexAgent TASK_SUCCESS: session_id=%s intensity=%.2f", session_id, intensity,
            )
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.cortex_agent").warning(
                "CortexAgent._on_task_success_cortex 异常: %s", exc
            )

    async def _on_circadian_phase_updated(self, event: Any) -> None:
        try:
            payload = dict(getattr(event, "payload", None) or {})
            prev = self.state_registry.get_state("cortex_agent") or {}
            prev_state = dict(prev.get("state") or {})
            prev_state.update(
                {
                    "circadian_phase": str(payload.get("to_phase") or "").strip().lower() or None,
                    "circadian_sleep_pressure": float(payload.get("sleep_pressure") or 0.0),
                    "circadian_last_update_ts_ms": int(payload.get("ts_ms") or 0),
                }
            )
            self.state_registry.set_state("cortex_agent", prev_state, writer="cortex_agent")
        except Exception:
            pass

    async def _on_proprioception_updated(self, event: Any) -> None:
        try:
            payload = dict(getattr(event, "payload", None) or {})
            prev = self.state_registry.get_state("cortex_agent") or {}
            prev_state = dict(prev.get("state") or {})
            prev_state.update(
                {
                    "cognitive_load_index": float(payload.get("cognitive_load_index") or 0.0),
                    "readiness_hint": float(payload.get("readiness_hint") or 0.0),
                    "continuity_hint": float(payload.get("continuity_hint") or 0.0),
                    "proprioception_last_update_ts_ms": int(payload.get("ts_ms") or 0),
                }
            )
            self.state_registry.set_state("cortex_agent", prev_state, writer="cortex_agent")
        except Exception:
            pass

    async def _on_initiative_decision_made(self, event: Any) -> None:
        try:
            payload = dict(getattr(event, "payload", None) or {})
            prev = self.state_registry.get_state("cortex_agent") or {}
            prev_state = dict(prev.get("state") or {})
            prev_state["last_initiative_decision"] = {
                "trace_id": str(payload.get("trace_id") or "").strip() or None,
                "action": str(payload.get("action") or "").strip() or None,
                "decision": str(payload.get("decision") or "").strip().upper() or None,
                "reason_code": str(payload.get("reason_code") or "").strip().upper() or None,
                "scores": dict(payload.get("scores") or {}),
                "ts_ms": int(getattr(event, "ts_ms", 0) or 0),
            }
            self.state_registry.set_state("cortex_agent", prev_state, writer="cortex_agent")
        except Exception:
            pass

    async def _on_platform_connected(self, event: Any) -> None:
        """处理 PLATFORM_CONNECTED 事件：记录平台连接状态。

        T-118：来自 edge_client PeripheralAgent，平台适配器成功连接时触发。
        将平台连接状态写入 cortex_agent 快照，供路由决策参考。
        """
        try:
            import logging  # noqa: PLC0415
            payload = dict(getattr(event, "payload", None) or {})
            platform = str(payload.get("platform") or "").strip()
            ts_ms = int(getattr(event, "ts_ms", 0) or 0)
            if not platform:
                return
            prev = self.state_registry.get_state("cortex_agent") or {}
            prev_state = dict(prev.get("state") or {})
            connected_platforms = list(prev_state.get("connected_platforms") or [])
            if platform not in connected_platforms:
                connected_platforms.append(platform)
            prev_state.update({
                "connected_platforms": connected_platforms,
                f"platform_{platform}_connected_at_ms": ts_ms,
            })
            self.state_registry.set_state("cortex_agent", prev_state, writer="cortex_agent")
            logging.getLogger("Natsume.tribe.cortex_agent").info(
                "CortexAgent PLATFORM_CONNECTED: platform=%s", platform,
            )
            # T-129：平台连接后向 ChannelAgent 推送开门指令（允许消息路由）
            try:
                from host_brain.tribe.edge_command_pusher import get_edge_command_pusher  # noqa: PLC0415
                _pusher = get_edge_command_pusher()
                if _pusher is not None and _pusher.enabled:
                    _pusher.push_gate_control(
                        gate_open=True,
                        reason="platform_connected",
                        platform=platform,
                        source_agent="cortex_agent",
                    )
            except Exception as _push_exc:
                logging.getLogger("Natsume.tribe.cortex_agent").debug(
                    "CortexAgent 推送开门指令失败（降级）: %s", _push_exc
                )
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.cortex_agent").warning(
                "CortexAgent._on_platform_connected 异常: %s", exc
            )

    async def _on_platform_disconnected(self, event: Any) -> None:
        """处理 PLATFORM_DISCONNECTED 事件：记录平台断线状态。

        T-118：来自 edge_client PeripheralAgent，平台适配器断线时触发。
        从 cortex_agent 快照中移除该平台，供路由决策参考。
        """
        try:
            import logging  # noqa: PLC0415
            payload = dict(getattr(event, "payload", None) or {})
            platform = str(payload.get("platform") or "").strip()
            ts_ms = int(getattr(event, "ts_ms", 0) or 0)
            if not platform:
                return
            prev = self.state_registry.get_state("cortex_agent") or {}
            prev_state = dict(prev.get("state") or {})
            connected_platforms = list(prev_state.get("connected_platforms") or [])
            if platform in connected_platforms:
                connected_platforms.remove(platform)
            prev_state.update({
                "connected_platforms": connected_platforms,
                f"platform_{platform}_disconnected_at_ms": ts_ms,
            })
            self.state_registry.set_state("cortex_agent", prev_state, writer="cortex_agent")
            logging.getLogger("Natsume.tribe.cortex_agent").info(
                "CortexAgent PLATFORM_DISCONNECTED: platform=%s", platform,
            )
            # T-129：平台断线后向 ChannelAgent 推送关门指令（暂停该平台消息路由）
            try:
                from host_brain.tribe.edge_command_pusher import get_edge_command_pusher  # noqa: PLC0415
                _pusher = get_edge_command_pusher()
                if _pusher is not None and _pusher.enabled:
                    _pusher.push_gate_control(
                        gate_open=False,
                        reason="platform_disconnected",
                        platform=platform,
                        source_agent="cortex_agent",
                    )
            except Exception as _push_exc:
                logging.getLogger("Natsume.tribe.cortex_agent").debug(
                    "CortexAgent 推送关门指令失败（降级）: %s", _push_exc
                )
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.cortex_agent").warning(
                "CortexAgent._on_platform_disconnected 异常: %s", exc
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
