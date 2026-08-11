"""Tribe event handlers and evolution scheduling for PrefrontalAgent."""

from __future__ import annotations

import logging
import time
from typing import Any

from host_brain.tribe.events import emit_tribe_event
from host_brain.tribe.prefrontal_runtime_policy import (
    evolution_event_cooldown_s as _evolution_event_cooldown_s,
    evolution_startup_grace_s as _evolution_startup_grace_s,
    evolution_user_cooldown_s as _evolution_user_cooldown_s,
    loneliness_event_emit_cooldown_s as _loneliness_event_emit_cooldown_s,
    loneliness_milestone_cooldown_s as _loneliness_milestone_cooldown_s,
    loneliness_signal_log_cooldown_s as _loneliness_signal_log_cooldown_s,
    resolve_prefrontal_persona_id as _resolve_prefrontal_persona_id,
)


_logger = logging.getLogger("Natsume.prefrontal_agent")


class PrefrontalEventHandlersMixin:
    def register_subscriptions(self) -> None:
        """向 TribeEventBus 注册事件订阅 handler。

        在 http_hooks.py lifespan 中调用，确保 Bus 的 dispatch_loop 启动后
        PrefrontalAgent 的 handler 能被正确触发。

        T-34：新增订阅 BOND_STAGE_CHANGED，
        关系阶段跃迁时触发 apply_stage_promotion_growth，将关系进展转化为人格成长动力。
        """
        try:
            from host_brain.tribe.event_bus import get_tribe_event_bus  # noqa: PLC0415

            bus = get_tribe_event_bus()
            bus.subscribe("INTERACTION_RECORDED", self._on_interaction_recorded)
            # The remaining EventBus routes below are legacy, unscoped
            # growth inputs.  In canonical composition neither an inner
            # world signal, an immune event nor a Bond stage can stand in for
            # durable relationship aftereffects.
            if not self._uses_canonical_affective_life():
                bus.subscribe("EVOLUTION_CYCLE_TRIGGERED", self._on_evolution_cycle_triggered)
                bus.subscribe("BOND_STAGE_CHANGED", self._on_bond_stage_changed_growth)
                bus.subscribe("LONELINESS_SIGNAL", self._on_loneliness_signal)
                bus.subscribe("IMMUNE_BLOCK", self._on_immune_block_growth)
        except Exception as exc:
            self._record_evolution_failure(
                user_id="natsume_ai",
                operation="REGISTER_EVOLUTION_SUBSCRIPTIONS",
                exc=exc,
            )
            _logger.warning("PrefrontalAgent 订阅注册失败（降级）: %s", exc)

    def _reserve_evolution_event(self, *, user_id: str, reason: str) -> str | None:
        reason_key = str(reason or "event_triggered").strip() or "event_triggered"
        user_key = _resolve_prefrontal_persona_id(user_id)
        event_key = f"{user_key}:{reason_key}"
        now = time.monotonic()
        startup_grace_s = _evolution_startup_grace_s()
        if (
            not reason_key.startswith("immune_block")
            and startup_grace_s > 0
            and now - float(self._created_monotonic or 0.0) < startup_grace_s
        ):
            _logger.info(
                "PrefrontalAgent evolution_cycle 启动保护窗内，跳过: user_id=%s reason=%s left_s=%d",
                user_key, reason_key, int(startup_grace_s - (now - float(self._created_monotonic or 0.0))),
            )
            return None
        if user_key in self._evolution_cycle_inflight_users:
            _logger.info(
                "PrefrontalAgent evolution_cycle 已在执行，合并跳过事件: user_id=%s reason=%s",
                user_key, reason_key,
            )
            return None
        if event_key in self._evolution_event_inflight_keys:
            _logger.info(
                "PrefrontalAgent evolution_cycle 已在执行，跳过重复事件: user_id=%s reason=%s",
                user_key, reason_key,
            )
            return None
        user_cooldown_s = _evolution_user_cooldown_s()
        last_user_ts = float(self._last_evolution_cycle_ts_by_user.get(user_key) or 0.0)
        if (
            not reason_key.startswith("immune_block")
            and last_user_ts > 0
            and now - last_user_ts < user_cooldown_s
        ):
            _logger.info(
                "PrefrontalAgent evolution_cycle 用户级冷却中，跳过: user_id=%s reason=%s left_s=%d",
                user_key, reason_key, int(user_cooldown_s - (now - last_user_ts)),
            )
            return None
        cooldown_s = _evolution_event_cooldown_s(reason_key)
        last_ts = float(self._last_evolution_event_ts_by_key.get(event_key) or 0.0)
        if last_ts > 0 and now - last_ts < cooldown_s:
            _logger.info(
                "PrefrontalAgent evolution_cycle 事件冷却中，跳过: user_id=%s reason=%s left_s=%d",
                user_key, reason_key, int(cooldown_s - (now - last_ts)),
            )
            return None
        if self._resource_gate_defers_evolution(reason=reason_key):
            return None
        self._last_evolution_event_ts_by_key[event_key] = now
        self._last_evolution_cycle_ts_by_user[user_key] = now
        self._evolution_event_inflight_keys.add(event_key)
        self._evolution_cycle_inflight_users.add(user_key)
        return event_key

    def _release_evolution_event(self, event_key: str | None) -> None:
        if event_key:
            self._evolution_event_inflight_keys.discard(event_key)
            user_key = str(event_key).split(":", 1)[0].strip()
            if user_key:
                self._evolution_cycle_inflight_users.discard(user_key)

    def _resource_gate_defers_evolution(self, *, reason: str) -> bool:
        try:
            manager = self.application_ports.require("resource_manager")(
                create_if_missing=False,
            )
        except Exception as exc:
            _logger.debug("PrefrontalAgent resource gate unavailable: %s", exc)
            return False
        if manager is None or not hasattr(manager, "assess_background_task"):
            return False
        priority = "HIGH" if str(reason or "").startswith("immune_block") else "LOW"
        try:
            decision = dict(
                manager.assess_background_task(
                    task_id="evolution_cycle_event",
                    priority=priority,
                    task_type="memory_heavy",
                    uses_llm=True,
                    uses_gpu=False,
                    reason=f"prefrontal_event:{reason}",
                )
                or {}
            )
        except Exception as exc:
            _logger.debug("PrefrontalAgent resource gate check failed: %s", exc)
            return False
        if bool(decision.get("allowed", True)):
            return False
        _logger.info(
            "PrefrontalAgent evolution_cycle 被资源管家延后: reason=%s pressure=%s retry_after_s=%s",
            reason,
            decision.get("pressure_status"),
            decision.get("retry_after_s"),
        )
        return True

    def _loneliness_evolution_emit_allowed(self, *, user_id: str) -> bool:
        user_key = _resolve_prefrontal_persona_id(user_id)
        cooldown_s = _loneliness_event_emit_cooldown_s()
        now = time.monotonic()
        last_ts = float(self._last_loneliness_event_emit_ts_by_user.get(user_key) or 0.0)
        if last_ts > 0 and now - last_ts < cooldown_s:
            _logger.debug(
                "PrefrontalAgent loneliness_signal 演化事件冷却中，跳过发布: user_id=%s left_s=%d",
                user_key, int(cooldown_s - (now - last_ts)),
            )
            return False
        self._last_loneliness_event_emit_ts_by_user[user_key] = now
        return True

    def _loneliness_signal_log_allowed(self, *, user_id: str) -> bool:
        user_key = _resolve_prefrontal_persona_id(user_id)
        cooldown_s = _loneliness_signal_log_cooldown_s()
        now = time.monotonic()
        last_ts = float(self._last_loneliness_signal_log_ts_by_user.get(user_key) or 0.0)
        if last_ts > 0 and now - last_ts < cooldown_s:
            return False
        self._last_loneliness_signal_log_ts_by_user[user_key] = now
        return True

    def _loneliness_milestone_allowed(self, *, user_id: str) -> bool:
        user_key = _resolve_prefrontal_persona_id(user_id)
        cooldown_s = _loneliness_milestone_cooldown_s()
        now = time.monotonic()
        last_ts = float(self._last_loneliness_milestone_ts_by_user.get(user_key) or 0.0)
        if last_ts > 0 and now - last_ts < cooldown_s:
            return False
        self._last_loneliness_milestone_ts_by_user[user_key] = now
        return True

    async def _on_evolution_cycle_triggered(self, event: Any) -> None:
        """处理 EVOLUTION_CYCLE_TRIGGERED 事件：触发人格演化周期。

        由 ANSAgent（inner_world_skill）或 LimbicAgent 发布，
        PrefrontalAgent 订阅后异步执行 evolution_cycle。
        """
        if self._uses_canonical_affective_life():
            # A stale subscription or an explicit compatibility caller must
            # not revive unscoped, in-memory Persona evolution in production.
            _logger.info(
                "PrefrontalAgent 忽略 retired 无 scope 演化事件: canonical Life authority",
            )
            return
        user_id = "natsume_ai"
        try:
            payload = dict(event.payload or {})
            user_id = _resolve_prefrontal_persona_id(payload.get("user_id"))
            reason = str(payload.get("reason") or "event_triggered").strip()
            _logger.info(
                "PrefrontalAgent 收到 EVOLUTION_CYCLE_TRIGGERED: user_id=%s reason=%s",
                user_id, reason,
            )
            event_key = self._reserve_evolution_event(user_id=user_id, reason=reason)
            if event_key is None:
                return
            # 异步执行 evolution_cycle（避免阻塞 dispatch_loop）
            import asyncio  # noqa: PLC0415
            asyncio.create_task(
                self._run_evolution_cycle_async(user_id=user_id, reason=reason, event_key=event_key),
                name=f"evolution_cycle_{user_id}",
            )
        except Exception as exc:
            self._record_evolution_failure(
                user_id=user_id,
                operation="HANDLE_EVOLUTION_CYCLE_EVENT",
                exc=exc,
            )
            _logger.warning("PrefrontalAgent._on_evolution_cycle_triggered 异常: %s", exc)

    async def _run_evolution_cycle_async(self, *, user_id: str, reason: str, event_key: str | None = None) -> None:
        """在独立 task 中异步执行 evolution_cycle，避免阻塞事件分发循环。"""
        try:
            import asyncio  # noqa: PLC0415
            result = await asyncio.to_thread(
                self.run_evolution_cycle,
                system_intent={
                    "skill_name": "evolution_cycle",
                    "args": {"user_id": user_id},
                    "proposer_node_id": "tribe_event_bus",
                },
            )
            _logger.info(
                "PrefrontalAgent evolution_cycle 完成: user_id=%s reason=%s status=%s",
                user_id, reason, result.get("status"),
            )
        except Exception as exc:
            self._record_evolution_failure(
                user_id=user_id,
                operation="RUN_EVOLUTION_CYCLE_ASYNC",
                exc=exc,
            )
            _logger.warning(
                "PrefrontalAgent._run_evolution_cycle_async 异常: user_id=%s exc=%s",
                user_id, exc,
            )
        finally:
            self._release_evolution_event(event_key)

    async def _on_interaction_recorded(self, event: Any) -> None:
        """处理 INTERACTION_RECORDED 事件：触发人格漂移检测。

        T-26：通过 assistant_reply 内容检测 humor/task 成功信号，
        发布 TASK_SUCCESS / HUMOR_LANDED 事件，供 PainRewardAgent 接收。
        """
        user_id = "natsume_ai"
        try:
            payload = dict(event.payload or {})
            uses_canonical_affective_life = self._uses_canonical_affective_life()
            user_id = _resolve_prefrontal_persona_id(payload.get("user_id"))
            user_message = str(payload.get("user_message") or "").strip()
            assistant_reply = str(payload.get("assistant_reply") or "").strip()
            session_id = str(payload.get("session_id") or "default").strip() or "default"
            if user_message and assistant_reply:
                drift_result = self.run_growth_feedback_cycle(
                    session_id=session_id,
                    user_message=user_message,
                    assistant_reply=assistant_reply,
                )

                # T-219：漂移检测结果联动 LimbicAgent 情绪调整
                drift_alert = str(drift_result.get("drift_alert_level") or "").strip().upper()
                if drift_alert in {"MODERATE", "SEVERE"}:
                    # FIX-P1：将漂移状态同步到 GovernorRulesCache（L3 → L2 单向通道）
                    # PrefrontalAgent 检测到漂移时，收紧 Governor 的 scope_policy_gate
                    try:
                        from host_brain.integrations.governor_rules_cache import get_governor_rules_cache  # noqa: PLC0415
                        _scope_override = f"drift_restricted:{drift_alert.lower()}" if drift_alert == "SEVERE" else None
                        get_governor_rules_cache().update_prefrontal_state(
                            drift_detected=True,
                            scope_override=_scope_override,
                        )
                        _logger.info(
                            "PrefrontalAgent 漂移状态已同步到 GovernorRulesCache: alert_level=%s scope_override=%s",
                            drift_alert, _scope_override,
                        )
                    except Exception as _gov_exc:
                        self._record_evolution_failure(
                            user_id=user_id,
                            operation="SYNC_DRIFT_GOVERNOR",
                            exc=_gov_exc,
                        )
                        _logger.debug("PrefrontalAgent GovernorRulesCache 更新跳过: %s", _gov_exc)

                    # canonical drift remains a safety constraint for the
                    # Governor, but it must not directly mutate retired
                    # Limbic/Endocrine/Pain/Interoception state machines.
                    if not uses_canonical_affective_life:
                        try:
                            limbic = get_limbic_agent()
                            if hasattr(limbic, "drive_emotion_metabolism"):
                                # 漂移时情绪向不安/焦虑偏移
                                severity = 0.15 if drift_alert == "MODERATE" else 0.28
                                limbic.drive_emotion_metabolism(
                                    valence=-severity,
                                    arousal=severity * 0.6,
                                    dominance=-0.05,
                                    blend_weight=0.25,
                                    event_kind=f"persona_drift:{drift_alert.lower()}",
                                    source="prefrontal_agent",
                                )
                        except Exception as exc:
                            self._record_evolution_failure(
                                user_id=user_id,
                                operation="SYNC_DRIFT_LIMBIC",
                                exc=exc,
                            )
                        # T-279：高漂移时联动 EndocrineAgent 记录压力事件（人格漂移是内在压力源）
                        try:
                            _endo = get_endocrine_agent()
                            if hasattr(_endo, "record_stress_event"):
                                _drift_intensity = 0.20 if drift_alert == "MODERATE" else 0.35
                                _endo.record_stress_event(
                                    source=f"persona_drift:{drift_alert.lower()}",
                                    intensity=_drift_intensity,
                                )
                        except Exception as exc:
                            self._record_evolution_failure(
                                user_id=user_id,
                                operation="SYNC_DRIFT_ENDOCRINE",
                                exc=exc,
                            )
                        # T-336：高漂移时联动 PainRewardAgent 记录深层痛苦（价值漂移是自我认同的痛苦）
                        try:
                            _pr = get_pain_reward_agent()
                            if hasattr(_pr, "record_pain"):
                                _pain_intensity = 0.22 if drift_alert == "MODERATE" else 0.38
                                _pr.record_pain(
                                    source=f"persona_drift:{drift_alert.lower()}",
                                    intensity=_pain_intensity,
                                    metadata={
                                        "drift_alert": drift_alert,
                                        "session_id": session_id,
                                        "drift_dims": list(drift_result.get("drift_dims") or []),
                                    },
                                )
                        except Exception as exc:
                            self._record_evolution_failure(
                                user_id=user_id,
                                operation="SYNC_DRIFT_PAIN_REWARD",
                                exc=exc,
                            )
                        # T-374：严重漂移（SEVERE）时联动 InteroceptionAgent 注入深层内在压力
                        # 人格/价值观漂移是最深层的内在压力源（自我认同危机消耗大量认知资源）
                        if drift_alert == "SEVERE":
                            try:
                                _intero = get_interoception_agent()
                                if hasattr(_intero, "inject_external_stress"):
                                    _drift_intero = round(min(0.40, len(
                                        list(drift_result.get("drift_dims") or [])
                                    ) * 0.10 + 0.20), 4)
                                    _intero.inject_external_stress(
                                        source=f"prefrontal_severe_drift:{session_id[:16]}",
                                        intensity=_drift_intero,
                                    )
                            except Exception as exc:
                                self._record_evolution_failure(
                                    user_id=user_id,
                                    operation="SYNC_DRIFT_INTEROCEPTION",
                                    exc=exc,
                                )
                else:
                    # FIX-P1：漂移恢复正常时，重置 GovernorRulesCache 的漂移状态
                    # 避免历史漂移状态永久收紧 Governor 策略
                    if drift_alert in {"NONE", "LOW", ""}:
                        try:
                            from host_brain.integrations.governor_rules_cache import get_governor_rules_cache  # noqa: PLC0415
                            get_governor_rules_cache().update_prefrontal_state(
                                drift_detected=False,
                                scope_override=None,
                            )
                        except Exception as exc:
                            self._record_evolution_failure(
                                user_id=user_id,
                                operation="RESET_DRIFT_GOVERNOR",
                                exc=exc,
                            )

            # T-26：检测 humor/task 成功信号并发布对应事件
            if assistant_reply and not uses_canonical_affective_life:
                try:

                    _reply_lower = assistant_reply.lower()
                    # 幽默成功信号：回复中包含笑声/幽默标记词
                    _HUMOR_SIGNALS = ["哈哈", "哈哈哈", "lol", "haha", "😄", "😂", "🤣", "笑死", "好笑", "哈哈哈哈"]
                    _humor_hit = any(sig in _reply_lower or sig in assistant_reply for sig in _HUMOR_SIGNALS)
                    if _humor_hit:
                        emit_tribe_event(
                            event_type="HUMOR_LANDED",
                            source_agent="prefrontal_agent",
                            payload={
                                "session_id": session_id,
                                "intensity": 0.3,
                                "reason": "humor_signal_in_reply",
                                "reply_preview": assistant_reply[:64],
                            },
                        )
                        _logger.debug(
                            "PrefrontalAgent 发布 HUMOR_LANDED: session_id=%s", session_id,
                        )

                    # 任务成功信号：用户消息包含任务请求词 + 回复包含完成标记词
                    _TASK_REQUEST_SIGNALS = ["帮我", "帮忙", "请", "能不能", "可以", "写", "生成", "翻译", "总结", "分析"]
                    _TASK_DONE_SIGNALS = ["好的", "完成", "已经", "这是", "如下", "以下", "done", "here", "sure"]
                    _task_request = any(sig in user_message for sig in _TASK_REQUEST_SIGNALS)
                    _task_done = any(sig in _reply_lower or sig in assistant_reply for sig in _TASK_DONE_SIGNALS)
                    if _task_request and _task_done and not _humor_hit:
                        emit_tribe_event(
                            event_type="TASK_SUCCESS",
                            source_agent="prefrontal_agent",
                            payload={
                                "session_id": session_id,
                                "intensity": 0.35,
                                "reason": "task_completion_signal",
                                "user_message_preview": user_message[:64],
                            },
                        )
                        _logger.debug(
                            "PrefrontalAgent 发布 TASK_SUCCESS: session_id=%s", session_id,
                        )
                except Exception as inner_exc:
                    self._record_evolution_failure(
                        user_id=user_id,
                        operation="PUBLISH_INTERACTION_FEEDBACK_SIGNAL",
                        exc=inner_exc,
                    )
                    _logger.debug("PrefrontalAgent humor/task 事件发布跳过: %s", inner_exc)
        except Exception as exc:
            self._record_evolution_failure(
                user_id=user_id,
                operation="HANDLE_INTERACTION_FEEDBACK",
                exc=exc,
            )
            _logger.warning("PrefrontalAgent._on_interaction_recorded 异常: %s", exc)


    async def _on_bond_stage_changed_growth(self, event: Any) -> None:
        """处理 BOND_STAGE_CHANGED 事件：关系阶段跃迁触发人格成长。

        T-34：当 bond stage 发生升级跃迁时，调用 apply_stage_promotion_growth()，
        将关系进展转化为人格成长动力（如开放度提升、信任感增强等）。
        降级跃迁不触发成长（但不会回滚已有成长）。
        """
        if self._uses_canonical_affective_life():
            _logger.info(
                "PrefrontalAgent 忽略 retired Bond 阶段成长事件: canonical Life authority",
            )
            return

        user_id = "natsume_ai"
        try:
            payload = dict(getattr(event, "payload", None) or {})
            from_stage = str(payload.get("from_stage") or "").strip().upper()
            to_stage = str(payload.get("to_stage") or "").strip().upper()
            user_id = _resolve_prefrontal_persona_id(payload.get("user_id"))
            ts_ms = int(getattr(event, "ts_ms", 0) or 0)

            _STAGE_ORDER = [
                "FRIEND",
                "CLOSE_FRIEND", "INTIMATE", "SOULMATE",
            ]
            from_idx = _STAGE_ORDER.index(from_stage) if from_stage in _STAGE_ORDER else -1
            to_idx = _STAGE_ORDER.index(to_stage) if to_stage in _STAGE_ORDER else -1

            # 只处理升级跃迁
            if from_idx >= 0 and to_idx > from_idx:
                import asyncio  # noqa: PLC0415
                result = await asyncio.to_thread(
                    self.apply_stage_promotion_growth,
                    user_id=user_id,
                    from_stage=from_stage,
                    to_stage=to_stage,
                    ts_ms=ts_ms,
                )
                _logger.info(
                    "PrefrontalAgent 关系阶段跃迁人格成长: %s→%s user_id=%s applied=%s",
                    from_stage, to_stage, user_id, result.get("applied"),
                )
        except Exception as exc:
            self._record_evolution_failure(
                user_id=user_id,
                operation="HANDLE_BOND_STAGE_FEEDBACK",
                exc=exc,
            )
            _logger.warning("PrefrontalAgent._on_bond_stage_changed_growth 异常: %s", exc)


    async def _on_loneliness_signal(self, event: Any) -> None:
        """处理 LONELINESS_SIGNAL 事件：孤独感影响决策策略。

        T-47：当孤独感较高时（>0.6），降低主动发起话题的阈值，
        触发 EVOLUTION_CYCLE_TRIGGERED 以加速人格成长（孤独感是成长催化剂）。
        孤独感极高时（>0.8）还会触发 milestone 成长节点。
        """
        if self._uses_canonical_affective_life():
            _logger.info(
                "PrefrontalAgent 忽略 retired 孤独成长事件: canonical Life authority",
            )
            return
        user_id = "natsume_ai"
        try:
            payload = dict(getattr(event, "payload", None) or {})
            loneliness = max(0.0, min(1.0, float(payload.get("loneliness") or 0.0)))
            user_id = _resolve_prefrontal_persona_id(payload.get("user_id"))
            ts_ms = int(getattr(event, "ts_ms", 0) or 0)

            if loneliness > 0.6:
                if self._loneliness_signal_log_allowed(user_id=user_id):
                    _logger.info(
                        "PrefrontalAgent 孤独感触发决策调整: loneliness=%.2f user_id=%s",
                        loneliness, user_id,
                    )
                # 孤独感 > 0.6：触发演化周期（孤独感是人格成长的催化剂），但不能让后台成长链路风暴化。
                if self._loneliness_evolution_emit_allowed(user_id=user_id):
                    try:
                        emit_tribe_event(
                            event_type="EVOLUTION_CYCLE_TRIGGERED",
                            source_agent="prefrontal_agent",
                            payload={
                                "user_id": user_id,
                                "reason": "loneliness_signal",
                                "loneliness": loneliness,
                                "ts_ms": ts_ms,
                            },
                        )
                    except Exception as exc:
                        self._record_evolution_failure(
                            user_id=user_id,
                            operation="PUBLISH_LONELINESS_EVOLUTION_SIGNAL",
                            exc=exc,
                        )

            if loneliness > 0.8 and self._loneliness_milestone_allowed(user_id=user_id):
                # 孤独感极高：触发 milestone 成长节点（深度孤独体验）
                import asyncio  # noqa: PLC0415
                asyncio.create_task(
                    self._run_milestone_growth_async(
                        user_id=user_id,
                        milestone_key="deep_loneliness_experience",
                        ts_ms=ts_ms,
                    ),
                    name=f"loneliness_milestone_{user_id}",
                )
        except Exception as exc:
            self._record_evolution_failure(
                user_id=user_id,
                operation="HANDLE_LONELINESS_FEEDBACK",
                exc=exc,
            )
            _logger.warning("PrefrontalAgent._on_loneliness_signal 异常: %s", exc)

    async def _run_milestone_growth_async(
        self,
        *,
        user_id: str,
        milestone_key: str,
        ts_ms: int,
    ) -> None:
        try:
            import asyncio  # noqa: PLC0415

            await asyncio.to_thread(
                self.apply_milestone_growth,
                user_id=user_id,
                milestone_key=milestone_key,
                ts_ms=ts_ms,
            )
        except Exception as exc:
            self._record_evolution_failure(
                user_id=user_id,
                operation="RUN_MILESTONE_FEEDBACK_ASYNC",
                exc=exc,
            )
            _logger.warning(
                "PrefrontalAgent 里程碑反馈异步处理失败: user_id=%s milestone=%s exc=%s",
                user_id,
                milestone_key,
                exc,
            )

    async def _on_immune_block_growth(self, event: Any) -> None:
        """处理 IMMUNE_BLOCK 事件：拦截时触发安全审查成长周期。

        T-71：ImmuneAgent 拦截有害内容时发布此事件，
        PrefrontalAgent 订阅后触发 apply_safety_review_growth()，
        将安全拦截转化为人格成长动力（提升边界感/自我保护意识）。
        仅在 block_rate > 0.1 或 risk_level 为 high/critical 时触发，
        避免低频拦截过度影响人格演化。
        """
        if self._uses_canonical_affective_life():
            _logger.info(
                "PrefrontalAgent 忽略 retired 免疫成长事件: canonical Life authority",
            )
            return
        user_id = "natsume_ai"
        try:
            payload = dict(getattr(event, "payload", None) or {})
            risk_level = str(payload.get("risk_level") or "").strip().lower()
            score = float(payload.get("score") or 0.0)
            session_id = str(payload.get("session_id") or "default").strip() or "default"
            # 仅高风险拦截才触发成长周期
            if risk_level not in ("high", "critical") and score < 0.7:
                return
            user_id = _resolve_prefrontal_persona_id(payload.get("user_id"))
            _logger.info(
                "PrefrontalAgent IMMUNE_BLOCK 触发安全审查成长: risk_level=%s score=%.3f session_id=%s",
                risk_level, score, session_id,
            )
            reason = f"immune_block_risk_{risk_level}"
            event_key = self._reserve_evolution_event(user_id=user_id, reason=reason)
            if event_key is None:
                return
            import asyncio  # noqa: PLC0415
            asyncio.create_task(
                self._run_evolution_cycle_async(
                    user_id=user_id,
                    reason=reason,
                    event_key=event_key,
                ),
                name=f"immune_block_growth_{session_id}",
            )
        except Exception as exc:
            self._record_evolution_failure(
                user_id=user_id,
                operation="HANDLE_IMMUNE_BLOCK_FEEDBACK",
                exc=exc,
            )
            _logger.warning("PrefrontalAgent._on_immune_block_growth 异常: %s", exc)
