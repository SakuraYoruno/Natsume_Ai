from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from host_brain.tribe.base_agent import TribeAgentDescriptor
from host_brain.tribe.tribe_state import TribeStateRegistry, get_global_registry
from host_brain.tribe.cortex_context_collector import collect_tribe_context as _collect_tribe_context
from host_brain.tribe.cortex_route_decider import route_intent as _route_intent




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

        P3-1：委托给 cortex_context_collector.collect_tribe_context() 执行，
        CortexAgent 保留为协调器入口，保持向后兼容。
        """
        return _collect_tribe_context(
            state_registry=self.state_registry,
            request_body=request_body,
            session_id=session_id,
            user_id=user_id,
        )

    def route_intent(
        self,
        *,
        message: str,
        tribe_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Supervisor 意图路由：根据消息内容和族上下文决定路由策略。

        P3-1：委托给 cortex_route_decider.route_intent() 执行，
        CortexAgent 保留为协调器入口，保持向后兼容。
        """
        return _route_intent(
            message=message,
            tribe_context=tribe_context,
        )

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

        # T-369：对话结束时联动 PainRewardAgent 记录对话完成奖励
        # 成功完成一次对话是最直接的正向强化（"我帮助了对方"是核心奖励）
        try:
            from host_brain.tribe.pain_reward_agent import get_pain_reward_agent  # noqa: PLC0415
            _pr = get_pain_reward_agent()
            if hasattr(_pr, "record_reward"):
                _emotion_tag = str(emotion_tag or "NEUTRAL").strip().upper()
                # 正向情绪结束的对话奖励更高
                _pos_tags = {"JOY", "HAPPY", "EXCITED", "CONTENT", "GRATEFUL", "AMUSED"}
                _base_reward = 0.22 if _emotion_tag in _pos_tags else 0.12
                _reward = round(min(0.40, _base_reward * float(emotion_intensity or 0.5) * 2.0), 4)
                _pr.record_reward(
                    source=f"cortex_conversation_ended:{_emotion_tag.lower()}",
                    intensity=_reward,
                    metadata={
                        "user_id": str(user_id or ""),
                        "session_id": str(session_id or ""),
                        "emotion_tag": _emotion_tag,
                        "emotion_intensity": round(float(emotion_intensity or 0.5), 4),
                    },
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
            # G3: 订阅主动发声触发事件，将 expression.content 走 TTS 链路发出
            bus.subscribe("PROACTIVE_VOICE_TRIGGERED", self._on_proactive_voice_triggered)
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

        P3-4：同步将本次成功的意图类型写入 GovernorRulesCache.intent_allowlist_extra，
        使 Governor 裁决时能感知 CortexAgent 已确认可通过的意图，完成 TRIBE 族接入闭环。
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
            _log = logging.getLogger("Natsume.tribe.cortex_agent")
            _log.debug(
                "CortexAgent TASK_SUCCESS: session_id=%s intensity=%.2f", session_id, intensity,
            )

            # ── P3-4：同步意图白名单到 GovernorRulesCache ──
            # 将本次成功任务的 intent_type 写入动态白名单，
            # Governor 裁决时通过 get_effective_rules()["intent_allowlist_extra"] 读取
            intent_type = str(payload.get("intent_type") or payload.get("route_type") or "").strip()
            if intent_type:
                try:
                    from host_brain.integrations.governor_rules_cache import get_governor_rules_cache  # noqa: PLC0415
                    cache = get_governor_rules_cache()
                    # 追加到现有白名单（去重合并）
                    existing = list(cache.get_effective_rules().get("intent_allowlist_extra") or [])
                    if intent_type not in existing:
                        existing.append(intent_type)
                    # 只保留最近 20 个，避免无限增长
                    cache.update_cortex_intent_allowlist(existing[-20:])
                    _log.debug(
                        "CortexAgent 更新 GovernorRulesCache intent_allowlist_extra: +%s (total=%d)",
                        intent_type, len(existing),
                    )
                except Exception as cache_exc:
                    _log.debug("CortexAgent 更新 GovernorRulesCache 失败（降级）: %s", cache_exc)

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

    async def _on_proactive_voice_triggered(self, event: Any) -> None:
        """G3/D3: 处理 PROACTIVE_VOICE_TRIGGERED 事件，通过 MotorCortexAgent.dispatch_speak() 发出语音。

        D3 改造：原来用 plan_action+dispatch_action 间接分发，现在直接调用
        dispatch_speak()，统一走 Phase C 发声入口，确保：
        1. 发声结果写入 state_registry（last_speak_outcome）
        2. 发布 MOTOR_SPEAK_DISPATCHED 事件到 TribeEventBus
        3. 发声失败时联动 LimbicAgent 轻度挫败感
        """
        import logging as _logging
        _log = _logging.getLogger("Natsume.tribe.cortex_agent")
        try:
            payload = dict(getattr(event, "payload", None) or {})
            triggered = bool(payload.get("triggered", False))
            if not triggered:
                return

            expression = dict(payload.get("expression") or {})
            content = str(expression.get("content") or payload.get("content_preview") or "").strip()
            if not content:
                _log.debug("CortexAgent._on_proactive_voice_triggered: content 为空，跳过")
                return

            emotion_tag = str(
                expression.get("emotion_tag")
                or payload.get("emotion_tag")
                or "NEUTRAL"
            ).strip().upper() or "NEUTRAL"

            scene_type = str(payload.get("scene_type") or "").strip()
            session_id = str(payload.get("session_id") or "").strip() or "proactive_voice"

            # 从 Broca 获取 emotion_intensity（如果可用）
            emotion_intensity: float = 0.5
            try:
                from host_brain.app.chat_agent_postprocessor import broca_to_tts_params  # noqa: PLC0415
                from host_brain.tribe.language_system_agent import get_language_system_agent  # noqa: PLC0415
                _lang_agent = get_language_system_agent()
                _last_broca = dict(
                    (_lang_agent.state_registry.get_state("language_system_agent") or {})
                    .get("state", {})
                    .get("last_broca") or {}
                )
                if _last_broca:
                    _tts_params = broca_to_tts_params(
                        tone=str(_last_broca.get("tone") or "balanced"),
                        warmth_weight=float(_last_broca.get("warmth_weight") or 0.5),
                        emotion_tag=emotion_tag,
                    )
                    emotion_tag = _tts_params["emotion_tag"]
                    emotion_intensity = float(_tts_params["emotion_intensity"])
            except Exception:
                pass

            # D3: 直接调用 MotorCortexAgent.dispatch_speak()（Phase C 统一发声入口）
            from host_brain.tribe.motor_cortex_agent import get_motor_cortex_agent  # noqa: PLC0415
            from host_brain.core.ids import new_request_id  # noqa: PLC0415
            req_id = new_request_id()
            motor = get_motor_cortex_agent()
            speak_result = motor.dispatch_speak(
                text=content,
                emotion_tag=emotion_tag,
                emotion_intensity=emotion_intensity,
                utterance_id=f"utt_proactive_{req_id}",
                request_id=req_id,
                session_id=session_id,
                source="proactive_voice",
                require_gate=False,
            )

            _log.info(
                "CortexAgent._on_proactive_voice_triggered: 主动发声已发出 "
                "scene=%s emotion=%s content_len=%d ok=%s",
                scene_type, emotion_tag, len(content), speak_result.get("ok"),
            )
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.cortex_agent").warning(
                "CortexAgent._on_proactive_voice_triggered 异常: %s", exc
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

# ── 跨模块引用（供测试 monkeypatch 使用）────────────────────────────────────
from host_brain.tribe.pain_reward_agent import get_pain_reward_agent  # noqa: E402, F401
