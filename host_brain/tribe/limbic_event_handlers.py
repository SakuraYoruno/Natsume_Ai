"""Legacy Tribe event compatibility handlers for the Limbic projection."""

from __future__ import annotations

from typing import Any


def _canonical_limbic_persona_id() -> str:
    from host_brain.tribe.limbic_agent import _canonical_limbic_persona_id as resolve

    return resolve()


def _is_trusted_runtime_emotion_source(**kwargs: Any) -> bool:
    from host_brain.tribe.limbic_agent import _is_trusted_runtime_emotion_source as resolve

    return bool(resolve(**kwargs))


class LimbicEventHandlersMixin:
    def register_subscriptions(self) -> None:
        """向 TribeEventBus 注册事件订阅 handler。

        在 http_hooks.py lifespan 中调用，确保 Bus 的 dispatch_loop 启动后
        各族 Agent 的 handler 能被正确触发。

        T-30：新增订阅 BOND_PROGRESS / BOND_REGRESS，
        关系变化影响情绪效价（valence）和沉默意愿（silence_willingness）。
        """
        try:
            from host_brain.tribe.event_bus import get_tribe_event_bus  # noqa: PLC0415

            bus = get_tribe_event_bus()
            bus.subscribe("BODY_SENSE_UPDATED", self._on_body_sense_updated)
            bus.subscribe("INNER_WORLD_TICKED", self._on_inner_world_ticked)
            bus.subscribe("CONVERSATION_STARTED", self._on_conversation_started_event)
            bus.subscribe("CONVERSATION_ENDED", self._on_conversation_ended_event)
            # T-09：订阅 SomaticAgent 通过 EdgeTribeBridge 上报的情绪推断事件
            # edge_client 侧 SomaticAgent.publish_emotion_inference_updated() 发布
            # event_type="emotion_inference_updated"，经 /internal/tribe_event 转发后
            # Bus 以大写 key 分发，此处用大写匹配
            bus.subscribe("EMOTION_INFERENCE_UPDATED", self._on_emotion_inference_updated)
            # T-30：订阅关系进展/退步事件，影响情绪基线
            bus.subscribe("BOND_PROGRESS", self._on_bond_progress)
            bus.subscribe("BOND_REGRESS", self._on_bond_regress)
            # T-41：订阅任务成功/幽默成功事件，正向情绪偏置
            bus.subscribe("TASK_SUCCESS", self._on_task_success_emotion)
            bus.subscribe("HUMOR_LANDED", self._on_humor_landed_emotion)
            # T-51：订阅孤独感信号，触发情绪效价下降
            bus.subscribe("LONELINESS_SIGNAL", self._on_loneliness_signal_emotion)
            # T-79：订阅免疫拦截事件，拦截时触发轻微负面情绪偏置
            bus.subscribe("IMMUNE_BLOCK", self._on_immune_block_emotion)
            # T-149：订阅对话记录事件，每轮对话结束后驱动情绪代谢
            bus.subscribe("INTERACTION_RECORDED", self._on_interaction_recorded_metabolism)
            # T-208：订阅体内感受更新事件，高负荷时主动驱动情绪偏移
            bus.subscribe("INTEROCEPTION_UPDATED", self._on_interoception_updated)
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.limbic_agent").warning(
                "LimbicAgent 订阅注册失败（降级）: %s", exc
            )

    async def _on_interoception_updated(self, event: Any) -> None:
        """T-208：处理 INTEROCEPTION_UPDATED 事件，高负荷时主动驱动情绪偏移。

        - interoceptive_index >= 0.70：高负荷，情绪向低效价/低唤醒偏移
        - body_budget >= 0.75 且 interoceptive_index <= 0.30：体能充足，轻微正效价偏移
        """
        if self._legacy_self_affect_writes_retired():
            return
        try:
            payload = dict(getattr(event, "payload", None) or {})
            interoceptive_index = float(payload.get("interoceptive_index") or 0.0)
            body_budget = float(payload.get("body_budget") or 0.5)

            if interoceptive_index >= 0.70:
                valence_shift = -((interoceptive_index - 0.70) * 0.8)
                arousal_shift = -((interoceptive_index - 0.70) * 0.5)
                self.drive_emotion_metabolism(
                    valence=valence_shift,
                    arousal=arousal_shift,
                    dominance=0.0,
                    blend_weight=0.35,
                    event_kind="interoceptive_overload",
                    source="interoception_agent",
                )
            elif body_budget >= 0.75 and interoceptive_index <= 0.30:
                self.drive_emotion_metabolism(
                    valence=0.08,
                    arousal=0.05,
                    dominance=0.0,
                    blend_weight=0.20,
                    event_kind="body_budget_surplus",
                    source="interoception_agent",
                )
        except Exception as exc:
            import logging  # noqa: PLC0415

            logging.getLogger("Natsume.tribe.limbic_agent").debug(
                "LimbicAgent._on_interoception_updated 异常: %s", exc
            )

    async def _on_body_sense_updated(self, event: Any) -> None:
        """处理 BODY_SENSE_UPDATED 事件：更新沉默意愿惩罚 + 驱动情绪代谢。

        T-139：body_sense 更新时，除更新沉默意愿外，还通过 drive_emotion_metabolism()
        将疲劳/孤独感转化为情绪代谢信号，打通 body_sense → LimbicAgent 联动链路。
        """
        if self._legacy_self_affect_writes_retired():
            # A legacy scheduler/skill may still emit this compatibility event
            # during the rollback window.  It must not adjust silence or
            # recreate an affect transition after canonical Life has taken
            # ownership; the current turn reads the scoped Life projection.
            return
        try:
            payload = dict(event.payload or {})
            penalty = float(payload.get("willingness_penalty") or 0.0)
            if penalty > 0.0:
                self.apply_body_sense_penalty(penalty=penalty)

            # T-139：body_sense 驱动情绪代谢
            # 疲劳 → arousal 下降；孤独感 → valence 下降
            fatigue = float(payload.get("fatigue") or 0.0)
            loneliness = float(payload.get("loneliness") or 0.0)
            if fatigue > 0.3 or loneliness > 0.3:
                try:
                    # 从当前持久化情绪读取基线
                    base_valence = 0.0
                    base_arousal = 0.3
                    if self._emotion_persistence is not None:
                        restored = self._emotion_persistence.restore(user_id=_canonical_limbic_persona_id())
                        base_valence = float(getattr(restored, "valence", 0.0))
                        base_arousal = float(getattr(restored, "arousal", 0.3))
                    # 疲劳拉低唤醒度，孤独感拉低效价
                    target_arousal = base_arousal - min(0.15, fatigue * 0.20)
                    target_valence = base_valence - min(0.10, loneliness * 0.12)
                    self.drive_emotion_metabolism(
                        valence=target_valence,
                        arousal=target_arousal,
                        event_kind="emotion_calm" if fatigue > 0.5 else "interaction",
                        source="body_sense_updated",
                    )
                    # 同步写入 emotion_timeline
                    from host_brain.core.metabolism import EmotionVector  # noqa: PLC0415
                    _vec = EmotionVector(valence=target_valence, arousal=target_arousal)
                    self.write_emotion_timeline(
                        emotion_tag=_vec.to_emotion_tag(),
                        valence=target_valence,
                        arousal=target_arousal,
                        source="body_sense_updated",
                    )
                except Exception as _meta_exc:
                    import logging  # noqa: PLC0415
                    logging.getLogger("Natsume.tribe.limbic_agent").debug(
                        "LimbicAgent body_sense 代谢更新失败（降级）: %s", _meta_exc
                    )

            self._touch_state(
                key="last_bus_body_sense_updated",
                value={
                    "source": str(event.source_agent or ""),
                    "fatigue_level": str(payload.get("fatigue_level") or "normal"),
                    "loneliness_level": str(payload.get("loneliness_level") or "none"),
                    "willingness_penalty": penalty,
                    "fatigue": fatigue,
                    "loneliness": loneliness,
                },
            )
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.limbic_agent").warning(
                "LimbicAgent._on_body_sense_updated 异常: %s", exc
            )

    async def _on_inner_world_ticked(self, event: Any) -> None:
        """处理 INNER_WORLD_TICKED 事件：记录内心世界 tick 状态。"""
        try:
            payload = dict(event.payload or {})
            self._touch_state(
                key="last_bus_inner_world_ticked",
                value={
                    "source": str(event.source_agent or ""),
                    "user_id": str(payload.get("user_id") or ""),
                    "loneliness": payload.get("loneliness"),
                    "anticipation": payload.get("anticipation"),
                    "continuity_score": payload.get("continuity_score"),
                },
            )
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.limbic_agent").warning(
                "LimbicAgent._on_inner_world_ticked 异常: %s", exc
            )

    async def _on_conversation_started_event(self, event: Any) -> None:
        """处理 CONVERSATION_STARTED 事件：重置沉默意愿、捕获 body_sense 快照。"""
        try:
            payload = dict(event.payload or {})
            self.on_conversation_started(
                request_id=payload.get("request_id"),
                session_id=payload.get("session_id"),
                person_id=payload.get("user_id") or payload.get("person_id"),
                emotion_tag=payload.get("emotion_tag"),
                mood_state=payload.get("mood_state"),
            )
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.limbic_agent").warning(
                "LimbicAgent._on_conversation_started_event 异常: %s", exc
            )

    async def _on_conversation_ended_event(self, event: Any) -> None:
        """处理 CONVERSATION_ENDED 事件：触发 session_end 收口。"""
        try:
            payload = dict(event.payload or {})
            self.on_conversation_ended(
                request_id=payload.get("request_id"),
                session_id=payload.get("session_id"),
                person_id=payload.get("user_id") or payload.get("person_id"),
                duration_minutes=payload.get("duration_minutes"),
            )
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.limbic_agent").warning(
                "LimbicAgent._on_conversation_ended_event 异常: %s", exc
            )

    async def _on_emotion_inference_updated(self, event: Any) -> None:
        """处理 EMOTION_INFERENCE_UPDATED 事件：接收多源情绪提示。

        T-09：SomaticAgent（edge_client）在运行期处理完成后通过 EdgeTribeBridge
        发布 emotion_inference_updated 事件，经 /internal/tribe_event 端点转发到 Bus。
        ASR 转写文本只作为低可信提示；可信情绪必须来自显式上游、视觉或声学模型。
        """
        try:
            import logging  # noqa: PLC0415
            _log = logging.getLogger("Natsume.tribe.limbic_agent")
            if self._legacy_self_affect_writes_retired():
                # In a composed production Host, final ASR is admitted through
                # the standard Input -> AffectFusion -> Life command chain.
                # This legacy bus subscriber has neither a scope-bound
                # observation command nor a durable receipt, so it must not
                # recreate a process-local voice-emotion truth or append the
                # retired timeline.  Offline compatibility composition keeps
                # the historical behavior for isolated migration tests.
                _log.debug(
                    "LimbicAgent 忽略 EMOTION_INFERENCE_UPDATED："
                    "canonical Life authority 已接管"
                )
                return
            payload = dict(event.payload or {})
            emotion_tag = str(payload.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
            emotion_intensity = payload.get("emotion_intensity")
            mood_state = str(payload.get("mood_state") or "").strip().lower() or None
            emotion_source = str(payload.get("emotion_source") or "runtime_unknown").strip() or "runtime_unknown"
            emotion_modality = str(payload.get("emotion_modality") or "").strip().lower()
            emotion_confidence = payload.get("emotion_confidence")
            acoustic_available = bool(payload.get("acoustic_emotion_available", False))
            inference_degraded = bool(payload.get("emotion_inference_degraded", False))
            session_id = str(payload.get("session_id") or "").strip()
            _log.debug(
                "LimbicAgent 收到 EMOTION_INFERENCE_UPDATED: emotion_tag=%s intensity=%s source=%s modality=%s degraded=%s session=%s",
                emotion_tag, emotion_intensity, emotion_source, emotion_modality, inference_degraded, session_id,
            )
            trusted_runtime_emotion = _is_trusted_runtime_emotion_source(
                emotion_source=emotion_source,
                emotion_modality=emotion_modality,
                acoustic_emotion_available=acoustic_available,
                degraded=inference_degraded,
            )
            # 将运行期情绪提示写入 TribeStateRegistry，供上下文装配主链读取。
            self._touch_state(
                key="last_voice_emotion_inference",
                value={
                    "emotion_tag": emotion_tag,
                    "emotion_intensity": emotion_intensity,
                    "mood_state": mood_state,
                    "emotion_source": emotion_source,
                    "emotion_modality": emotion_modality or None,
                    "emotion_confidence": emotion_confidence,
                    "emotion_inference_degraded": inference_degraded,
                    "acoustic_emotion_available": acoustic_available,
                    "trusted_runtime_emotion": trusted_runtime_emotion,
                    "session_id": session_id,
                    "source_agent": str(event.source_agent or ""),
                    "ts_ms": int(event.ts_ms or 0),
                },
            )
            # HardState 是 Host app-local 的 frozen 运行快照，Limbic 不拥有其写权。
            # 可信情绪只进入 TribeStateRegistry 与 emotion timeline，后续上下文
            # 从这些 canonical 来源组装，不尝试对 HardState 属性就地赋值。
            if trusted_runtime_emotion:
                # T-138：只有可信传感器/显式情绪写入 emotion_timeline。
                # ASR 转写文本只能作为低可信提示，不能伪装成声学情绪进入长期情绪链路。
                try:
                    self.write_emotion_timeline(
                        emotion_tag=emotion_tag,
                        valence=0.0,  # 运行期上游暂无 VAD 数据，使用中性值
                        arousal=float(emotion_intensity) * 0.5 if isinstance(emotion_intensity, (int, float)) else 0.0,
                        source=emotion_source,
                        ts_ms=int(event.ts_ms or 0) or None,
                    )
                except Exception as _tl_exc:
                    _log.debug("LimbicAgent write_emotion_timeline 失败（降级）: %s", _tl_exc)

            # Limbic only records the trusted observation.  Any downstream
            # somatic effect must be admitted as a governed TaskGraph node and
            # dispatched after Acceptance with an exact Preview proof.
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.limbic_agent").warning(
                "LimbicAgent._on_emotion_inference_updated 异常: %s", exc
            )


    async def _on_bond_progress(self, event: Any) -> None:
        """处理 BOND_PROGRESS 事件：关系进展 → 情绪效价提升 + 沉默意愿降低。

        T-30：关系进展（bond_stage 升级或 bond_trend 上升）时，
        通过 EmotionPersistence 将正向效价偏置写入情绪持久化，
        并降低沉默意愿（更愿意主动交流）。

        效价提升：intensity * 0.12（上限 0.10）
        沉默意愿降低：intensity * 0.08（上限 0.06）
        """
        if self._legacy_self_affect_writes_retired():
            return
        try:
            import logging  # noqa: PLC0415
            _log = logging.getLogger("Natsume.tribe.limbic_agent")
            payload = dict(getattr(event, "payload", None) or {})
            intensity = max(0.0, min(1.0, float(payload.get("intensity") or 0.3)))
            reason = str(payload.get("reason") or "bond_progress")

            # 效价提升（正向情绪偏置）
            valence_boost = round(min(0.10, intensity * 0.12), 4)
            # 沉默意愿降低（更愿意交流）
            silence_relief = round(min(0.06, intensity * 0.08), 4)

            # 通过 EmotionPersistence 写入情绪持久化（叠加到当前 valence）
            if valence_boost > 0.01 and self._emotion_persistence is not None:
                try:
                    from host_brain.persona.emotion_persistence import EmotionPersistence  # noqa: PLC0415
                    _ep = self._emotion_persistence
                    _emotion_user_id = _canonical_limbic_persona_id()
                    _restored = _ep.restore(user_id=_emotion_user_id)
                    _cur_valence = float(getattr(_restored, "valence", 0.0))
                    _cur_arousal = float(getattr(_restored, "arousal", 0.3))
                    _cur_dominance = float(getattr(_restored, "dominance", 0.0))
                    _new_valence = max(-1.0, min(1.0, _cur_valence + valence_boost))
                    _ep.persist(
                        user_id=_emotion_user_id,
                        valence=_new_valence,
                        arousal=_cur_arousal,
                        dominance=_cur_dominance,
                    )
                except Exception:
                    pass

            # 降低沉默意愿（通过 silence_engine 的 update_emotion 间接影响）
            if silence_relief > 0.01:
                try:
                    _engine = self.application_ports.require("silence_engine")()
                    # 以正向情绪更新沉默引擎，降低沉默意愿
                    _engine.update_emotion(
                        emotion_tag="JOY",
                        emotion_valence=min(1.0, 0.3 + valence_boost * 2),
                        emotion_arousal=0.5,
                        mood_state="upbeat",
                    )
                except Exception:
                    pass

            self._touch_state(
                key="last_bond_progress_effect",
                value={
                    "reason": reason,
                    "intensity": intensity,
                    "valence_boost": valence_boost,
                    "silence_relief": silence_relief,
                    "ts_ms": int(getattr(event, "ts_ms", 0) or 0),
                },
            )
            _log.debug(
                "LimbicAgent BOND_PROGRESS: valence+%.3f silence_relief=%.3f reason=%s",
                valence_boost, silence_relief, reason,
            )
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.limbic_agent").warning(
                "LimbicAgent._on_bond_progress 异常: %s", exc
            )

    async def _on_bond_regress(self, event: Any) -> None:
        """处理 BOND_REGRESS 事件：关系退步 → 情绪效价降低 + 沉默意愿提升。

        T-30：关系退步（bond_stage 降级或 bond_trend 下降）时，
        通过 EmotionPersistence 将负向效价偏置写入情绪持久化，
        并提升沉默意愿（更倾向沉默回避）。

        效价降低：intensity * 0.10（上限 0.08）
        沉默意愿提升：通过 apply_body_sense_penalty 施加惩罚
        """
        if self._legacy_self_affect_writes_retired():
            return
        try:
            import logging  # noqa: PLC0415
            _log = logging.getLogger("Natsume.tribe.limbic_agent")
            payload = dict(getattr(event, "payload", None) or {})
            intensity = max(0.0, min(1.0, float(payload.get("intensity") or 0.4)))
            reason = str(payload.get("reason") or "bond_regress")

            # 效价降低（负向情绪偏置）
            valence_drop = round(min(0.08, intensity * 0.10), 4)
            # 沉默意愿惩罚（通过 body_sense_penalty 提升沉默意愿）
            silence_penalty = round(min(0.05, intensity * 0.06), 4)

            # 通过 EmotionPersistence 写入情绪持久化（叠加到当前 valence）
            if valence_drop > 0.01 and self._emotion_persistence is not None:
                try:
                    _ep = self._emotion_persistence
                    _emotion_user_id = _canonical_limbic_persona_id()
                    _restored = _ep.restore(user_id=_emotion_user_id)
                    _cur_valence = float(getattr(_restored, "valence", 0.0))
                    _cur_arousal = float(getattr(_restored, "arousal", 0.3))
                    _cur_dominance = float(getattr(_restored, "dominance", 0.0))
                    _new_valence = max(-1.0, min(1.0, _cur_valence - valence_drop))
                    _ep.persist(
                        user_id=_emotion_user_id,
                        valence=_new_valence,
                        arousal=_cur_arousal,
                        dominance=_cur_dominance,
                    )
                except Exception:
                    pass

            # 提升沉默意愿（施加惩罚）
            if silence_penalty > 0.01:
                try:
                    self.apply_body_sense_penalty(penalty=silence_penalty)
                except Exception:
                    pass

            self._touch_state(
                key="last_bond_regress_effect",
                value={
                    "reason": reason,
                    "intensity": intensity,
                    "valence_drop": valence_drop,
                    "silence_penalty": silence_penalty,
                    "ts_ms": int(getattr(event, "ts_ms", 0) or 0),
                },
            )
            _log.debug(
                "LimbicAgent BOND_REGRESS: valence-%.3f silence_penalty=%.3f reason=%s",
                valence_drop, silence_penalty, reason,
            )
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.limbic_agent").warning(
                "LimbicAgent._on_bond_regress 异常: %s", exc
            )

    async def _on_task_success_emotion(self, event: Any) -> None:
        """处理 TASK_SUCCESS 事件：任务成功 → 情绪效价小幅提升。

        T-41：任务完成时将情绪效价小幅提升（+0.04 ~ +0.06），
        模拟成就感带来的轻微正向情绪。
        """
        if self._legacy_self_affect_writes_retired():
            return
        try:
            import logging  # noqa: PLC0415
            payload = dict(getattr(event, "payload", None) or {})
            intensity = max(0.0, min(1.0, float(payload.get("intensity") or 0.35)))
            valence_boost = round(min(0.06, intensity * 0.08), 4)
            if valence_boost > 0.01 and self._emotion_persistence is not None:
                try:
                    _ep = self._emotion_persistence
                    _emotion_user_id = _canonical_limbic_persona_id()
                    _restored = _ep.restore(user_id=_emotion_user_id)
                    _cur_valence = float(getattr(_restored, "valence", 0.0))
                    _cur_arousal = float(getattr(_restored, "arousal", 0.3))
                    _cur_dominance = float(getattr(_restored, "dominance", 0.0))
                    _new_valence = max(-1.0, min(1.0, _cur_valence + valence_boost))
                    _ep.persist(
                        user_id=_emotion_user_id,
                        valence=_new_valence,
                        arousal=_cur_arousal,
                        dominance=_cur_dominance,
                    )
                except Exception:
                    pass
            self._touch_state(
                key="last_task_success_effect",
                value={"intensity": intensity, "valence_boost": valence_boost,
                       "ts_ms": int(getattr(event, "ts_ms", 0) or 0)},
            )
            logging.getLogger("Natsume.tribe.limbic_agent").debug(
                "LimbicAgent TASK_SUCCESS: valence+%.3f", valence_boost,
            )
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.limbic_agent").warning(
                "LimbicAgent._on_task_success_emotion 异常: %s", exc
            )

    async def _on_humor_landed_emotion(self, event: Any) -> None:
        """处理 HUMOR_LANDED 事件：幽默成功 → 情绪效价提升 + 唤醒度小幅提升。

        T-41：幽默成功时将情绪效价提升（+0.05 ~ +0.08）和唤醒度小幅提升（+0.03），
        模拟幽默带来的轻松感和活跃感。
        """
        if self._legacy_self_affect_writes_retired():
            return
        try:
            import logging  # noqa: PLC0415
            payload = dict(getattr(event, "payload", None) or {})
            intensity = max(0.0, min(1.0, float(payload.get("intensity") or 0.3)))
            valence_boost = round(min(0.08, intensity * 0.10), 4)
            arousal_boost = round(min(0.03, intensity * 0.04), 4)
            if valence_boost > 0.01 and self._emotion_persistence is not None:
                try:
                    _ep = self._emotion_persistence
                    _emotion_user_id = _canonical_limbic_persona_id()
                    _restored = _ep.restore(user_id=_emotion_user_id)
                    _cur_valence = float(getattr(_restored, "valence", 0.0))
                    _cur_arousal = float(getattr(_restored, "arousal", 0.3))
                    _cur_dominance = float(getattr(_restored, "dominance", 0.0))
                    _new_valence = max(-1.0, min(1.0, _cur_valence + valence_boost))
                    _new_arousal = max(0.0, min(1.0, _cur_arousal + arousal_boost))
                    _ep.persist(
                        user_id=_emotion_user_id,
                        valence=_new_valence,
                        arousal=_new_arousal,
                        dominance=_cur_dominance,
                    )
                except Exception:
                    pass
            self._touch_state(
                key="last_humor_landed_effect",
                value={"intensity": intensity, "valence_boost": valence_boost,
                       "arousal_boost": arousal_boost,
                       "ts_ms": int(getattr(event, "ts_ms", 0) or 0)},
            )
            logging.getLogger("Natsume.tribe.limbic_agent").debug(
                "LimbicAgent HUMOR_LANDED: valence+%.3f arousal+%.3f", valence_boost, arousal_boost,
            )
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.limbic_agent").warning(
                "LimbicAgent._on_humor_landed_emotion 异常: %s", exc
            )

    async def _on_loneliness_signal_emotion(self, event: Any) -> None:
        """处理 LONELINESS_SIGNAL 事件：孤独感触发情绪效价下降。

        T-51：孤独感 > 0.5 时将情绪效价小幅下降（-0.03 ~ -0.06），
        模拟孤独感带来的轻微负向情绪。孤独感极高（>0.8）时额外降低唤醒度。
        """
        if self._legacy_self_affect_writes_retired():
            return
        try:
            import logging  # noqa: PLC0415
            payload = dict(getattr(event, "payload", None) or {})
            loneliness = max(0.0, min(1.0, float(payload.get("loneliness") or 0.0)))
            if loneliness <= 0.5:
                return  # 低孤独感属于正常状态，不影响情绪
            valence_drop = round(min(0.06, (loneliness - 0.5) * 0.12), 4)  # 0.5~1.0 → 0.0~0.06
            arousal_drop = round(min(0.03, (loneliness - 0.8) * 0.10), 4) if loneliness > 0.8 else 0.0
            if valence_drop > 0.01 and self._emotion_persistence is not None:
                try:
                    _ep = self._emotion_persistence
                    _emotion_user_id = _canonical_limbic_persona_id()
                    _restored = _ep.restore(user_id=_emotion_user_id)
                    _cur_valence = float(getattr(_restored, "valence", 0.0))
                    _cur_arousal = float(getattr(_restored, "arousal", 0.3))
                    _cur_dominance = float(getattr(_restored, "dominance", 0.0))
                    _new_valence = max(-1.0, min(1.0, _cur_valence - valence_drop))
                    _new_arousal = max(0.0, min(1.0, _cur_arousal - arousal_drop))
                    _ep.persist(
                        user_id=_emotion_user_id,
                        valence=_new_valence,
                        arousal=_new_arousal,
                        dominance=_cur_dominance,
                    )
                except Exception:
                    pass
            self._touch_state(
                key="last_loneliness_effect",
                value={
                    "loneliness": loneliness,
                    "valence_drop": valence_drop,
                    "arousal_drop": arousal_drop,
                    "ts_ms": int(getattr(event, "ts_ms", 0) or 0),
                },
            )
            logging.getLogger("Natsume.tribe.limbic_agent").debug(
                "LimbicAgent LONELINESS_SIGNAL: valence-%.3f arousal-%.3f", valence_drop, arousal_drop,
            )
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.limbic_agent").warning(
                "LimbicAgent._on_loneliness_signal_emotion 异常: %s", exc
            )

    async def _on_immune_block_emotion(self, event: Any) -> None:
        """处理 IMMUNE_BLOCK 事件：拦截时触发轻微负面情绪偏置。

        T-79：ImmuneAgent 拦截有害内容时，将情绪效价小幅下降（-0.02 ~ -0.04），
        模拟面对越界行为时的轻微不适感。仅在 risk_level 为 medium/high/critical 时触发。
        """
        if self._legacy_self_affect_writes_retired():
            return
        try:
            import logging  # noqa: PLC0415
            payload = dict(getattr(event, "payload", None) or {})
            risk_level = str(payload.get("risk_level") or "").strip().lower()
            score = float(payload.get("score") or 0.0)
            # 仅中等及以上风险才影响情绪
            if risk_level not in ("medium", "high", "critical") and score < 0.55:
                return
            # 风险越高，情绪效价下降越多
            risk_weight = {"medium": 0.02, "high": 0.03, "critical": 0.04}.get(risk_level, 0.02)
            valence_drop = round(min(0.04, risk_weight + score * 0.01), 4)
            if valence_drop > 0.01 and self._emotion_persistence is not None:
                try:
                    _ep = self._emotion_persistence
                    _emotion_user_id = _canonical_limbic_persona_id()
                    _restored = _ep.restore(user_id=_emotion_user_id)
                    _cur_valence = float(getattr(_restored, "valence", 0.0))
                    _cur_arousal = float(getattr(_restored, "arousal", 0.3))
                    _cur_dominance = float(getattr(_restored, "dominance", 0.0))
                    _new_valence = max(-1.0, min(1.0, _cur_valence - valence_drop))
                    _ep.persist(
                        user_id=_emotion_user_id,
                        valence=_new_valence,
                        arousal=_cur_arousal,
                        dominance=_cur_dominance,
                    )
                except Exception:
                    pass
            self._touch_state(
                key="last_immune_block_emotion_effect",
                value={"risk_level": risk_level, "valence_drop": valence_drop,
                       "ts_ms": int(getattr(event, "ts_ms", 0) or 0)},
            )
            logging.getLogger("Natsume.tribe.limbic_agent").debug(
                "LimbicAgent IMMUNE_BLOCK: valence-%.3f risk_level=%s", valence_drop, risk_level,
            )
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.limbic_agent").warning(
                "LimbicAgent._on_immune_block_emotion 异常: %s", exc
            )

    # ── T-149：INTERACTION_RECORDED 情绪代谢 ─────────────────────────────────

    async def _on_interaction_recorded_metabolism(self, event: Any) -> None:
        """处理 INTERACTION_RECORDED 事件：每轮对话结束后驱动情绪代谢。

        T-149：每轮对话结束后，根据对话的情绪信号（emotion_tag/valence/arousal）
        驱动情绪代谢，并写入 emotion_timeline，确保情绪状态随对话自然演化。
        """
        if self._legacy_self_affect_writes_retired():
            return
        try:
            payload = dict(getattr(event, "payload", None) or {})
            emotion_tag = str(payload.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
            valence = float(payload.get("valence") or 0.0)
            arousal = float(payload.get("arousal") or 0.3)
            self.drive_emotion_metabolism_from_interaction(
                emotion_tag=emotion_tag,
                valence=valence,
                arousal=arousal,
                source="interaction_recorded",
            )
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.limbic_agent").warning(
                "LimbicAgent._on_interaction_recorded_metabolism 异常: %s", exc
            )

    def drive_emotion_metabolism_from_interaction(
        self,
        *,
        emotion_tag: str = "NEUTRAL",
        valence: float = 0.0,
        arousal: float = 0.3,
        source: str = "interaction_recorded",
    ) -> dict[str, Any]:
        """T-149：每轮对话结束后驱动情绪代谢，将对话情绪信号融入情绪状态。

        封装 drive_emotion_metabolism() + write_emotion_timeline() 的组合调用，
        LimbicAgent 作为对话情绪代谢的唯一驱动者。

        Args:
            emotion_tag: 对话情绪标签（如 "HAPPY"/"SAD"/"NEUTRAL" 等）。
            valence: 效价 [-1.0, 1.0]，默认 0.0。
            arousal: 唤醒度 [-1.0, 1.0]，默认 0.3。
            source: 数据来源，默认 "interaction_recorded"。

        Returns:
            {"metabolism": dict, "timeline": dict}
        """
        try:
            # 驱动情绪代谢（blend_weight=0.3 表示新信号占 30%，历史状态占 70%）
            metabolism_result = self.drive_emotion_metabolism(
                valence=float(valence),
                arousal=float(arousal),
                blend_weight=0.3,
                event_kind="interaction",
                source=str(source or "interaction_recorded"),
            )
            # 写入 emotion_timeline
            timeline_result = self.write_emotion_timeline(
                emotion_tag=str(emotion_tag or "NEUTRAL").strip().upper() or "NEUTRAL",
                valence=float(valence),
                arousal=float(arousal),
                source=str(source or "interaction_recorded"),
            )
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.limbic_agent").debug(
                "LimbicAgent.drive_emotion_metabolism_from_interaction: tag=%s valence=%.3f arousal=%.3f",
                emotion_tag, valence, arousal,
            )
            return {"metabolism": metabolism_result, "timeline": timeline_result}
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.limbic_agent").warning(
                "LimbicAgent.drive_emotion_metabolism_from_interaction 失败（降级）: %s", exc
            )
            return {"metabolism": {}, "timeline": {}, "error": str(exc)}

    # ── D4：情绪衰减连续动态系统 ─────────────────────────────────────────────

    def tick_emotion_decay(
        self,
        *,
        source: str = "scheduler",
        circadian_phase: str = "day",
        sleep_pressure: float = 0.2,
    ) -> dict[str, Any]:
        """D4: 情绪衰减连续 tick——每 30s 由 NatsumeScheduler 驱动一次。

        模拟真实情绪的连续动态特性：
        - 惯性（Inertia）：情绪不会瞬间归零，有时间常数
        - 峰值衰减（Peak Decay）：强烈情绪（|valence|>0.5）衰减更快
        - 昼夜调制（Circadian Modulation）：夜间衰减更慢（睡眠巩固情绪记忆）
        - 睡眠压力调制：高睡眠压力时情绪趋向中性（疲惫抹平情绪）

        Args:
            source: 调用来源标识
            circadian_phase: 当前昼夜相位（day/dusk/night/dawn）
            sleep_pressure: 当前睡眠压力 [0.0, 1.0]

        Returns:
            {"valence": float, "arousal": float, "emotion_tag": str, "decayed": bool}
        """
        if self._legacy_self_affect_writes_retired():
            return {
                **self._retired_legacy_affect_result(
                    source=source,
                    operation="emotion_decay",
                ),
                "decayed": False,
            }
        import logging as _logging
        _log = _logging.getLogger("Natsume.tribe.limbic_agent")
        try:
            from host_brain.core.metabolism import EmotionVector  # noqa: PLC0415
            from host_brain.core.ids import now_ms  # noqa: PLC0415

            # 读取当前持久化情绪状态
            if self._emotion_persistence is None:
                return {"decayed": False, "reason": "no_persistence"}
            emotion_user_id = _canonical_limbic_persona_id()
            restored = self._emotion_persistence.restore(user_id=emotion_user_id)
            cur_valence = float(getattr(restored, "valence", 0.0))
            cur_arousal = float(getattr(restored, "arousal", 0.3))
            cur_dominance = float(getattr(restored, "dominance", 0.0))

            # 如果情绪已经接近中性，跳过衰减（避免无意义的计算）
            if abs(cur_valence) < 0.05 and abs(cur_arousal - 0.3) < 0.05:
                return {"decayed": False, "reason": "already_neutral",
                        "valence": cur_valence, "arousal": cur_arousal}

            # ── 昼夜调制衰减速率 ──────────────────────────────────────────
            # 夜间衰减更慢（睡眠期间情绪记忆巩固），日间衰减正常
            phase = str(circadian_phase or "day").strip().lower()
            base_decay = {
                "day":   0.04,   # 日间：正常衰减
                "dusk":  0.03,   # 黄昏：略慢
                "night": 0.015,  # 夜间：最慢（睡眠巩固）
                "dawn":  0.025,  # 黎明：恢复中
            }.get(phase, 0.04)

            # ── 峰值衰减加速 ──────────────────────────────────────────────
            # 强烈情绪（|valence|>0.5）衰减更快，模拟情绪峰值后的自然回落
            peak_factor = 1.0
            if abs(cur_valence) > 0.5:
                peak_factor = 1.0 + (abs(cur_valence) - 0.5) * 0.8  # 最高 1.4x

            # ── 睡眠压力调制 ──────────────────────────────────────────────
            # 高睡眠压力时情绪趋向中性（疲惫抹平情绪波动）
            sp = max(0.0, min(1.0, float(sleep_pressure or 0.2)))
            sleep_factor = 1.0 + sp * 0.5  # 睡眠压力 1.0 时衰减速率 1.5x

            effective_decay = min(0.15, base_decay * peak_factor * sleep_factor)

            # ── 执行衰减 ──────────────────────────────────────────────────
            vec = EmotionVector(
                valence=cur_valence,
                arousal=cur_arousal,
                dominance=cur_dominance,
                ts_ms=now_ms(),
            )
            decayed_vec = vec.decay(rate=effective_decay)

            # 将衰减后的情绪写回持久化存储
            try:
                self._emotion_persistence.persist(
                    user_id=emotion_user_id,
                    valence=decayed_vec.valence,
                    arousal=decayed_vec.arousal,
                    dominance=decayed_vec.dominance,
                )
            except Exception:
                pass

            emotion_tag = decayed_vec.to_emotion_tag()
            mood_state = decayed_vec.to_mood_state()

            # 更新 state_registry
            self._touch_state(
                key="last_emotion_decay_tick",
                value={
                    "valence": round(decayed_vec.valence, 4),
                    "arousal": round(decayed_vec.arousal, 4),
                    "dominance": round(decayed_vec.dominance, 4),
                    "emotion_tag": emotion_tag,
                    "mood_state": mood_state,
                    "effective_decay_rate": round(effective_decay, 4),
                    "circadian_phase": phase,
                    "sleep_pressure": round(sp, 3),
                    "source": source,
                },
            )

            _log.debug(
                "LimbicAgent.tick_emotion_decay: v=%.3f→%.3f a=%.3f→%.3f "
                "decay=%.4f phase=%s tag=%s",
                cur_valence, decayed_vec.valence,
                cur_arousal, decayed_vec.arousal,
                effective_decay, phase, emotion_tag,
            )
            return {
                "decayed": True,
                "valence": round(decayed_vec.valence, 4),
                "arousal": round(decayed_vec.arousal, 4),
                "dominance": round(decayed_vec.dominance, 4),
                "emotion_tag": emotion_tag,
                "mood_state": mood_state,
                "effective_decay_rate": round(effective_decay, 4),
                "circadian_phase": phase,
            }
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.limbic_agent").warning(
                "LimbicAgent.tick_emotion_decay 失败（降级）: %s", exc
            )
            return {"decayed": False, "error": str(exc)}
