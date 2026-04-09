from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from host_brain.tribe.base_agent import TribeAgentDescriptor
from host_brain.tribe.events import emit_tribe_event
from host_brain.tribe.tribe_state import TribeStateRegistry, get_global_registry


@dataclass(slots=True)
class LimbicAgent:
    descriptor: TribeAgentDescriptor = field(
        default_factory=lambda: TribeAgentDescriptor(
            agent_id="limbic_agent",
            family="limbic",
            capabilities=("emotion", "motive", "body_sense", "offline_continuity"),
        )
    )
    state_registry: TribeStateRegistry = field(default_factory=get_global_registry)  # T-29：使用全局单例
    _emotion_persistence: Any | None = field(default=None, init=False, repr=False)

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    def get_emotion_context(self, *, request_body: Any) -> dict[str, Any]:
        from host_brain.app.emotion_context import resolve_chat_emotion_context  # noqa: PLC0415

        context = dict(resolve_chat_emotion_context(request_body=request_body) or {})
        self._touch_state(
            key="last_emotion_context",
            value={
                "emotion_tag": str(context.get("emotion_tag") or "NEUTRAL"),
                "mood_state": str(context.get("mood_state") or "steady"),
            },
        )
        return context

    def build_emotion_snapshot(
        self,
        *,
        emotion_context: dict[str, Any],
        source: str,
        ts_ms: int,
    ) -> dict[str, Any]:
        from host_brain.app.emotion_context import build_chat_emotion_snapshot  # noqa: PLC0415

        snapshot = dict(
            build_chat_emotion_snapshot(
                emotion_context=emotion_context,
                source=source,
                ts_ms=ts_ms,
            )
            or {}
        )
        self._touch_state(
            key="last_emotion_snapshot",
            value={
                "source": source,
                "emotion_tag": str(
                    (
                        dict(snapshot.get("emotion_context") or {}).get("emotion_tag")
                        or emotion_context.get("emotion_tag")
                        or "NEUTRAL"
                    )
                ),
                "ts_ms": int(ts_ms),
            },
        )
        return snapshot

    def resolve_emotion_timeline_snapshot(
        self,
        *,
        summary_window_ms: int | None = None,
        diary_window_ms: int | None = None,
    ) -> dict[str, Any]:
        from host_brain.app.dependencies import get_emotion_timeline  # noqa: PLC0415

        timeline = get_emotion_timeline()
        if timeline is None:
            return {
                "summary": {},
                "diary": {},
                "dominant_emotion": "NEUTRAL",
            }
        summary_raw = (
            timeline.rebuild_summary(window_ms=summary_window_ms)
            if (hasattr(timeline, "rebuild_summary") and summary_window_ms is not None)
            else timeline.rebuild_summary() if hasattr(timeline, "rebuild_summary") else {}
        )
        diary_raw = (
            timeline.build_daily_diary(window_ms=diary_window_ms)
            if (hasattr(timeline, "build_daily_diary") and diary_window_ms is not None)
            else timeline.build_daily_diary() if hasattr(timeline, "build_daily_diary") else {}
        )
        summary = dict(summary_raw) if isinstance(summary_raw, dict) else {}
        diary = dict(diary_raw) if isinstance(diary_raw, dict) else {}
        dominant_emotion = str(
            summary.get("dominant_emotion")
            or diary.get("dominant_emotion")
            or "NEUTRAL"
        ).strip().upper() or "NEUTRAL"
        snapshot = {
            "summary": summary,
            "diary": diary,
            "dominant_emotion": dominant_emotion,
        }
        self._touch_state(
            key="last_emotion_timeline",
            value={
                "dominant_emotion": dominant_emotion,
                "summary_size": int(len(summary)),
                "diary_size": int(len(diary)),
            },
        )
        return snapshot

    def resolve_persistent_emotion_state(
        self,
        *,
        person_id: str | None,
        valence: float,
        arousal: float,
        dominance: float,
    ) -> dict[str, Any]:
        from host_brain.persona.emotion_persistence import EmotionPersistence  # noqa: PLC0415

        if self._emotion_persistence is None:
            self._emotion_persistence = EmotionPersistence()

        user_id = str(person_id or "user_1").strip() or "user_1"
        cur_valence = float(valence)
        cur_arousal = float(arousal)
        cur_dominance = float(dominance)
        restored = self._emotion_persistence.restore(user_id=user_id)

        from_baseline = bool(getattr(restored, "from_baseline", True))
        reunion_type = str(getattr(restored, "reunion_type", "") or "").strip()
        if not from_baseline:
            has_current_signal = abs(cur_valence) > 0.05 or abs(cur_arousal - 0.3) > 0.05
            weight_current = 0.7 if has_current_signal else 0.0
            weight_history = 1.0 - weight_current
            cur_valence = cur_valence * weight_current + float(getattr(restored, "valence", 0.0)) * weight_history
            cur_arousal = cur_arousal * weight_current + float(getattr(restored, "arousal", 0.3)) * weight_history
            cur_dominance = cur_dominance * weight_current + float(getattr(restored, "dominance", 0.0)) * weight_history

        # ── T-18：接入 PainRewardAgent 情绪调制信号 ──────────────────────
        # 将痛觉/奖励系统的效价偏置（valence_bias）和唤醒度提升（arousal_boost）
        # 叠加到当前情绪状态，使奖励/痛觉事件能够影响情绪持久化。
        # 叠加量上限 ±0.20（valence）/ +0.15（arousal），避免过度影响。
        _pain_valence_bias: float = 0.0
        _pain_arousal_boost: float = 0.0
        _pain_suggested_tag: str | None = None
        try:
            from host_brain.tribe.pain_reward_agent import get_pain_reward_agent  # noqa: PLC0415

            _pain_agent = get_pain_reward_agent()
            _modulation = _pain_agent.get_emotion_modulation()
            _raw_valence_bias = float(_modulation.get("valence_bias") or 0.0)
            _raw_arousal_boost = float(_modulation.get("arousal_boost") or 0.0)
            _pain_suggested_tag = _modulation.get("suggested_emotion_tag") or None
            if abs(_raw_valence_bias) > 0.02:
                _pain_valence_bias = max(-0.20, min(0.20, _raw_valence_bias * 0.35))
                cur_valence = max(-1.0, min(1.0, cur_valence + _pain_valence_bias))
            if _raw_arousal_boost > 0.05:
                _pain_arousal_boost = min(0.15, _raw_arousal_boost * 0.25)
                cur_arousal = max(0.0, min(1.0, cur_arousal + _pain_arousal_boost))
        except Exception:
            pass

        self._emotion_persistence.persist(
            user_id=user_id,
            valence=cur_valence,
            arousal=cur_arousal,
            dominance=cur_dominance,
        )
        payload = {
            "user_id": user_id,
            "valence": cur_valence,
            "arousal": cur_arousal,
            "dominance": cur_dominance,
            "from_baseline": from_baseline,
            "reunion_type": reunion_type,
            # T-18 痛觉/奖励调制信息（调试用，不影响主流程）
            **(
                {
                    "pain_valence_bias": round(_pain_valence_bias, 4),
                    "pain_arousal_boost": round(_pain_arousal_boost, 4),
                    **({"pain_suggested_emotion_tag": _pain_suggested_tag} if _pain_suggested_tag else {}),
                }
                if abs(_pain_valence_bias) > 0.001 or _pain_arousal_boost > 0.001
                else {}
            ),
        }
        self._touch_state(
            key="last_emotion_persistence",
            value={
                "user_id": user_id,
                "valence": round(cur_valence, 4),
                "arousal": round(cur_arousal, 4),
                "dominance": round(cur_dominance, 4),
                "from_baseline": from_baseline,
                "reunion_type": reunion_type,
            },
        )
        return payload

    def resolve_emotion_reactivity_profile(
        self,
        *,
        personality_traits: Any,
        user_emotion_tag: str,
        bond_stage: str,
        echo_weight: float,
    ) -> dict[str, Any]:
        from host_brain.persona.emotion_reactivity import EmotionReactivityProfile  # noqa: PLC0415

        modifiers = EmotionReactivityProfile.compute_modifiers(
            personality_traits,
            str(user_emotion_tag or ""),
            str(bond_stage or ""),
        )
        effective_echo_weight = EmotionReactivityProfile.apply_to_echo_weight(
            float(echo_weight),
            modifiers,
        )
        payload = {
            "effective_echo_weight": float(effective_echo_weight),
            "persona_modifiers": dict(modifiers.as_dict() if hasattr(modifiers, "as_dict") else {}),
        }
        self._touch_state(
            key="last_emotion_reactivity_profile",
            value={
                "bond_stage": str(bond_stage or ""),
                "user_emotion_tag": str(user_emotion_tag or "").upper(),
                "effective_echo_weight": round(float(effective_echo_weight), 4),
            },
        )
        return payload

    def resolve_silence_state(self, *, current_topic: str = "") -> dict[str, Any]:
        from host_brain.persona.emotional_silence import get_silence_engine  # noqa: PLC0415

        state = get_silence_engine().get_silence_state(current_topic=str(current_topic or ""))
        payload = dict(state.as_dict() if hasattr(state, "as_dict") else {})
        self._touch_state(
            key="last_silence_state",
            value={
                "willingness_level": str(payload.get("willingness_level") or "NORMAL"),
                "response_willingness": float(payload.get("response_willingness") or 0.0),
            },
        )
        return payload

    def apply_body_sense_penalty(self, *, penalty: float) -> None:
        from host_brain.persona.emotional_silence import get_silence_engine  # noqa: PLC0415

        get_silence_engine().apply_body_sense_penalty(float(penalty))

    def resolve_body_sense_state(self) -> dict[str, Any]:
        from host_brain.persona.body_sense import get_body_sense_model  # noqa: PLC0415

        state = get_body_sense_model().get_state()
        payload = dict(state.as_dict() if hasattr(state, "as_dict") else {})
        self._touch_state(
            key="last_body_sense_state",
            value={
                "fatigue_level": str(payload.get("fatigue_level") or "normal"),
                "drowsiness_level": str(payload.get("drowsiness_level") or "normal"),
                "loneliness_level": str(payload.get("loneliness_level") or "none"),
            },
        )
        return payload

    def tick_body_sense(self, *, current_hour: int | None = None) -> dict[str, Any]:
        from host_brain.persona.body_sense import get_body_sense_model  # noqa: PLC0415

        model = get_body_sense_model()
        result = dict(model.tick(current_hour=current_hour) or {})
        result.setdefault("status", "OK")
        state = model.get_state()
        payload = dict(state.as_dict() if hasattr(state, "as_dict") else {})
        self._touch_state(
            key="last_body_sense_state",
            value={
                "fatigue_level": str(payload.get("fatigue_level") or "normal"),
                "drowsiness_level": str(payload.get("drowsiness_level") or "normal"),
                "loneliness_level": str(payload.get("loneliness_level") or "none"),
            },
        )
        try:
            self.handle_signal(
                event_type="BODY_SENSE_UPDATED",
                source_agent="ans_agent",
                target_agent="limbic_agent",
                payload={
                    "status": str(result.get("status") or "OK"),
                    "fatigue": payload.get("fatigue"),
                    "drowsiness": payload.get("drowsiness"),
                    "loneliness": payload.get("loneliness"),
                    "willingness_penalty": payload.get("willingness_penalty"),
                },
            )
        except Exception:
            pass
        result["body_sense"] = payload
        return result

    def update_body_sense_loneliness(self, *, loneliness_value: float) -> dict[str, Any]:
        from host_brain.persona.body_sense import get_body_sense_model  # noqa: PLC0415

        model = get_body_sense_model()
        model.update_loneliness(loneliness_value=float(loneliness_value))
        state = model.get_state()
        payload = dict(state.as_dict() if hasattr(state, "as_dict") else {})
        self._touch_state(
            key="last_body_sense_state",
            value={
                "fatigue_level": str(payload.get("fatigue_level") or "normal"),
                "drowsiness_level": str(payload.get("drowsiness_level") or "normal"),
                "loneliness_level": str(payload.get("loneliness_level") or "none"),
            },
        )
        try:
            self.handle_signal(
                event_type="BODY_SENSE_UPDATED",
                source_agent="ans_agent",
                target_agent="limbic_agent",
                payload={
                    "loneliness": payload.get("loneliness"),
                    "loneliness_level": payload.get("loneliness_level"),
                },
            )
        except Exception:
            pass
        return {
            "status": "OK",
            "body_sense": payload,
        }

    def capture_conversation_body_sense(
        self,
        *,
        request_id: str | None,
        session_id: str | None,
        person_id: str | None,
        emotion_tag: str | None,
        mood_state: str | None,
        offline_loneliness: float | int | None = None,
    ) -> dict[str, Any]:
        from host_brain.persona.body_sense import get_body_sense_model  # noqa: PLC0415

        model = get_body_sense_model()
        try:
            loneliness = float(offline_loneliness or 0.0)
        except (TypeError, ValueError):
            loneliness = 0.0
        if loneliness > 0.0:
            model.update_loneliness(loneliness_value=loneliness)
        model.on_conversation_start()
        try:
            self.on_conversation_started(
                request_id=request_id,
                session_id=session_id,
                person_id=person_id,
                emotion_tag=emotion_tag,
                mood_state=mood_state,
            )
        except Exception:
            pass

        state = model.get_state()
        payload = dict(state.as_dict() if hasattr(state, "as_dict") else {})
        is_active = bool(payload.get("is_active"))
        if is_active:
            try:
                self.handle_signal(
                    event_type="BODY_SENSE_UPDATED",
                    source_agent="limbic_agent",
                    target_agent="limbic_agent",
                    payload={
                        "request_id": str(request_id or "").strip() or None,
                        "session_id": str(session_id or "").strip() or None,
                        "person_id": str(person_id or "").strip() or None,
                        "fatigue": payload.get("fatigue"),
                        "drowsiness": payload.get("drowsiness"),
                        "loneliness": payload.get("loneliness"),
                        "willingness_penalty": payload.get("willingness_penalty"),
                    },
                )
            except Exception:
                pass
        self._touch_state(
            key="last_body_sense_state",
            value={
                "fatigue_level": str(payload.get("fatigue_level") or "normal"),
                "drowsiness_level": str(payload.get("drowsiness_level") or "normal"),
                "loneliness_level": str(payload.get("loneliness_level") or "none"),
            },
        )
        return {
            "body_sense": payload,
            "is_active": is_active,
            "willingness_penalty": float(payload.get("willingness_penalty") or 0.0),
        }

    def resolve_inner_world_state(self, *, person_id: str | None) -> dict[str, Any]:
        from host_brain.persona.inner_world import get_inner_world  # noqa: PLC0415

        user_id = str(person_id or "user_1").strip() or "user_1"
        payload = dict(get_inner_world().get_state(user_id) or {})
        self._touch_state(
            key="last_inner_world_state",
            value={
                "user_id": user_id,
                "offline_hours": float(payload.get("total_offline_hours") or 0.0),
                "anticipation_topics": list(payload.get("anticipation_topics") or [])[:6],
            },
        )
        return payload

    def resolve_offline_state(
        self,
        *,
        person_id: str | None,
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        from host_brain.persona.motive_runtime import build_offline_state_from_inner_state  # noqa: PLC0415

        inner_world_state = self.resolve_inner_world_state(person_id=person_id)
        payload = dict(build_offline_state_from_inner_state(inner_world_state, now_ms=now_ms) or {})
        self._touch_state(
            key="last_offline_state",
            value={
                "offline_hours": float(payload.get("offline_hours") or 0.0),
                "absence_band": str(payload.get("absence_band") or "recent"),
            },
        )
        return payload

    def resolve_offline_reconnect_profile(
        self,
        *,
        offline_state: Mapping[str, Any] | None,
    ) -> dict[str, str]:
        from host_brain.persona.offline_continuity import (  # noqa: PLC0415
            reconnect_style_for_absence_band,
            resolve_absence_band,
        )

        state = dict(offline_state or {})
        absence_band = str(resolve_absence_band(state) or "recent").strip() or "recent"
        reconnect_style = str(reconnect_style_for_absence_band(absence_band) or "").strip()
        payload = {
            "absence_band": absence_band,
            "reconnect_style": reconnect_style,
        }
        self._touch_state(
            key="last_offline_reconnect_profile",
            value=payload,
        )
        return payload

    def tick_inner_world(self, *, person_id: str | None) -> dict[str, Any]:
        from host_brain.persona.inner_world import get_inner_world  # noqa: PLC0415

        user_id = str(person_id or "user_1").strip() or "user_1"
        payload = dict(get_inner_world().tick(user_id=user_id) or {})
        payload.setdefault("status", "OK")
        payload.setdefault("user_id", user_id)
        self._touch_state(
            key="last_inner_world_tick",
            value={
                "user_id": user_id,
                "status": str(payload.get("status") or "OK"),
                "continuity_score": float(payload.get("continuity_score") or 0.0),
                "anticipation": float(payload.get("anticipation") or 0.0),
            },
        )
        return payload

    def on_user_awaken(self, *, person_id: str | None) -> dict[str, Any]:
        from host_brain.persona.inner_world import get_inner_world  # noqa: PLC0415

        user_id = str(person_id or "user_1").strip() or "user_1"
        snapshot = get_inner_world().on_user_awaken(user_id=user_id)
        payload = dict(snapshot.as_dict() if hasattr(snapshot, "as_dict") else {})
        self._touch_state(
            key="last_awaken_snapshot",
            value={
                "user_id": user_id,
                "offline_hours": float(payload.get("offline_hours") or 0.0),
                "absence_band": str(payload.get("absence_band") or "recent"),
                "has_hint": bool(str(payload.get("awakening_hint") or "").strip()),
            },
        )
        return payload

    def tick_silence(
        self,
        *,
        user_id: str,
        current_topic: str,
        bond_summary: Mapping[str, Any] | None = None,
        emotion_context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        from host_brain.persona.emotional_silence import get_silence_engine  # noqa: PLC0415

        engine = get_silence_engine()
        result = dict(
            engine.tick(
                user_id=str(user_id or "user_1"),
                current_topic=str(current_topic or ""),
                bond_summary=dict(bond_summary or {}) or None,
                emotion_context=dict(emotion_context or {}) or None,
            )
            or {}
        )
        result.setdefault("status", "OK")
        result["silence_state"] = self.resolve_silence_state(current_topic=current_topic)
        return result

    def update_silence_emotion(
        self,
        *,
        emotion_tag: str,
        mood_state: str,
        emotion_valence: float,
        emotion_arousal: float,
        current_topic: str = "",
    ) -> dict[str, Any]:
        from host_brain.persona.emotional_silence import get_silence_engine  # noqa: PLC0415

        engine = get_silence_engine()
        willingness = engine.update_emotion(
            emotion_tag=str(emotion_tag or "NEUTRAL").strip().upper() or "NEUTRAL",
            emotion_valence=float(emotion_valence),
            emotion_arousal=float(emotion_arousal),
            mood_state=str(mood_state or "steady").strip().lower() or "steady",
        )
        return {
            "status": "OK",
            "willingness": willingness,
            "silence_state": self.resolve_silence_state(current_topic=current_topic),
        }

    def add_silence_topic_resistance(
        self,
        *,
        topic_label: str,
        strength: float = 0.7,
    ) -> dict[str, Any]:
        from host_brain.persona.emotional_silence import get_silence_engine  # noqa: PLC0415

        resolved_topic = str(topic_label or "").strip()
        if not resolved_topic:
            return {
                "status": "REJECTED",
                "reason_code": "SILENCE_TOPIC_REQUIRED",
            }
        bounded_strength = max(0.0, min(1.0, float(strength)))
        engine = get_silence_engine()
        engine.add_topic_resistance(resolved_topic, strength=bounded_strength)
        return {
            "status": "OK",
            "topic_label": resolved_topic,
            "strength": bounded_strength,
            "silence_state": self.resolve_silence_state(current_topic=resolved_topic),
        }

    def on_session_end(
        self,
        *,
        person_id: str | None,
        valence: float,
        arousal: float,
        pending_topics: list[str] | None = None,
        mood_baseline: float | None = None,
    ) -> dict[str, Any]:
        from host_brain.persona.inner_world import get_inner_world  # noqa: PLC0415

        user_id = str(person_id or "user_1").strip() or "user_1"
        get_inner_world().on_session_end(
            user_id,
            valence=float(valence),
            arousal=float(arousal),
            pending_topics=list(pending_topics or []),
            mood_baseline=float(mood_baseline) if mood_baseline is not None else None,
        )
        payload = self.resolve_inner_world_state(person_id=user_id)
        try:
            self.handle_signal(
                event_type="SESSION_ENDED",
                source_agent="cortex_agent",
                target_agent="limbic_agent",
                payload={
                    "person_id": user_id,
                    "valence": float(valence),
                    "arousal": float(arousal),
                    "pending_topics_count": len(list(pending_topics or [])),
                },
            )
        except Exception:
            pass
        return payload

    def compute_runtime_motive_state(
        self,
        *,
        persona_user_id: str,
        current_topic: str = "",
        bond_summary: Mapping[str, Any] | None = None,
        emotion_context: Mapping[str, Any] | None = None,
        pending_topics: list[str] | None = None,
        silence_state: Mapping[str, Any] | None = None,
        body_sense: Mapping[str, Any] | None = None,
        inner_state: Mapping[str, Any] | None = None,
        runtime_flags: Mapping[str, Any] | None = None,
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        from host_brain.persona.motive_runtime import compute_runtime_motive_state  # noqa: PLC0415

        payload = dict(
            compute_runtime_motive_state(
                persona_user_id=persona_user_id,
                current_topic=current_topic,
                bond_summary=dict(bond_summary) if isinstance(bond_summary, Mapping) else None,
                emotion_context=dict(emotion_context) if isinstance(emotion_context, Mapping) else None,
                pending_topics=list(pending_topics or []),
                silence_state=dict(silence_state) if isinstance(silence_state, Mapping) else None,
                body_sense=dict(body_sense) if isinstance(body_sense, Mapping) else None,
                inner_state=dict(inner_state) if isinstance(inner_state, Mapping) else None,
                runtime_flags=dict(runtime_flags) if isinstance(runtime_flags, Mapping) else None,
                now_ms=now_ms,
            )
            or {}
        )
        self._touch_state(
            key="last_runtime_motive_state",
            value={
                "dominant_motive": str(payload.get("dominant_motive") or "steady"),
                "reach_out_drive": float(payload.get("reach_out_drive") or 0.0),
                "continuity_drive": float(payload.get("continuity_drive") or 0.0),
            },
        )
        return payload

    def compute_motive_state(
        self,
        *,
        silence_state: Mapping[str, Any] | None = None,
        body_sense: Mapping[str, Any] | None = None,
        offline_state: Mapping[str, Any] | None = None,
        bond_summary: Mapping[str, Any] | None = None,
        self_reflection_pulse: Mapping[str, Any] | None = None,
        emotion_context: Mapping[str, Any] | None = None,
        pending_followup_topics: list[str] | None = None,
        shared_memory_count: int = 0,
        user_message: str = "",
        runtime_flags: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        from host_brain.persona.motive_engine import MotiveInput, compute_motive_state  # noqa: PLC0415

        payload = dict(
            compute_motive_state(
                MotiveInput(
                    silence_state=dict(silence_state) if isinstance(silence_state, Mapping) else None,
                    body_sense=dict(body_sense) if isinstance(body_sense, Mapping) else None,
                    offline_state=dict(offline_state) if isinstance(offline_state, Mapping) else None,
                    bond_summary=dict(bond_summary) if isinstance(bond_summary, Mapping) else None,
                    self_reflection_pulse=(
                        dict(self_reflection_pulse)
                        if isinstance(self_reflection_pulse, Mapping)
                        else None
                    ),
                    emotion_context=dict(emotion_context) if isinstance(emotion_context, Mapping) else None,
                    pending_followup_topics=list(pending_followup_topics or []),
                    shared_memory_count=int(shared_memory_count),
                    user_message=str(user_message or ""),
                    runtime_flags=dict(runtime_flags) if isinstance(runtime_flags, Mapping) else None,
                )
            ).as_dict()
            or {}
        )
        self._touch_state(
            key="last_motive_state",
            value={
                "dominant_motive": str(payload.get("dominant_motive") or "steady"),
                "reach_out_drive": float(payload.get("reach_out_drive") or 0.0),
                "withdraw_drive": float(payload.get("withdraw_drive") or 0.0),
            },
        )
        return payload

    def decide_response_policy(
        self,
        *,
        silence_state: Mapping[str, Any] | None = None,
        body_sense: Mapping[str, Any] | None = None,
        offline_state: Mapping[str, Any] | None = None,
        bond_summary: Mapping[str, Any] | None = None,
        self_reflection_pulse: Mapping[str, Any] | None = None,
        emotion_context: Mapping[str, Any] | None = None,
        motive_state: Mapping[str, Any] | None = None,
        memory_persona_signal: Mapping[str, Any] | None = None,
        user_message: str = "",
        runtime_flags: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        from host_brain.persona.response_policy import ResponsePolicyInput, decide_response_policy  # noqa: PLC0415

        payload = dict(
            decide_response_policy(
                ResponsePolicyInput(
                    silence_state=dict(silence_state) if isinstance(silence_state, Mapping) else None,
                    body_sense=dict(body_sense) if isinstance(body_sense, Mapping) else None,
                    offline_state=dict(offline_state) if isinstance(offline_state, Mapping) else None,
                    bond_summary=dict(bond_summary) if isinstance(bond_summary, Mapping) else None,
                    self_reflection_pulse=(
                        dict(self_reflection_pulse)
                        if isinstance(self_reflection_pulse, Mapping)
                        else None
                    ),
                    emotion_context=dict(emotion_context) if isinstance(emotion_context, Mapping) else None,
                    motive_state=dict(motive_state) if isinstance(motive_state, Mapping) else None,
                    memory_persona_signal=(
                        dict(memory_persona_signal)
                        if isinstance(memory_persona_signal, Mapping)
                        else None
                    ),
                    user_message=str(user_message or ""),
                    runtime_flags=dict(runtime_flags) if isinstance(runtime_flags, Mapping) else None,
                )
            ).as_dict()
            or {}
        )
        self._touch_state(
            key="last_response_policy",
            value={
                "should_be_withdrawn": bool(payload.get("should_be_withdrawn")),
                "allow_proactive_after_reply": bool(payload.get("allow_proactive_after_reply", True)),
            },
        )
        return payload

    def handle_signal(
        self,
        *,
        event_type: str,
        payload: dict[str, Any] | None = None,
        source_agent: str | None = None,
        target_agent: str | None = None,
    ) -> dict[str, Any]:
        normalized_event = str(event_type or "").strip().upper() or "UNKNOWN"
        normalized_payload = dict(payload or {})
        writer = str(source_agent or "limbic_agent").strip() or "limbic_agent"

        prev = self.state_registry.get_state("limbic") or {}
        prev_state = dict(prev.get("state") or {})
        signals = list(prev_state.get("recent_signals") or [])
        signals.append(
            {
                "event_type": normalized_event,
                "source_agent": str(source_agent or "").strip() or None,
                "target_agent": str(target_agent or "").strip() or None,
                "payload": normalized_payload,
            }
        )
        if len(signals) > 24:
            signals = signals[-24:]
        merged = dict(prev_state)
        merged["recent_signals"] = signals
        merged["last_signal_event_type"] = normalized_event
        merged["signal_count"] = int(prev_state.get("signal_count") or 0) + 1
        self.state_registry.set_state("limbic", merged, writer=writer)

        event_row = emit_tribe_event(
            event_type=normalized_event,
            source_agent=writer,
            target_agent=(str(target_agent).strip() or None) if target_agent is not None else "limbic_agent",
            payload=normalized_payload,
        )
        return {
            "event_type": normalized_event,
            "accepted": event_row is not None,
            "state_signal_count": merged["signal_count"],
        }

    def on_conversation_started(
        self,
        *,
        request_id: str | None,
        session_id: str | None,
        person_id: str | None,
        emotion_tag: str | None,
        mood_state: str | None,
    ) -> dict[str, Any]:
        return self.handle_signal(
            event_type="CONVERSATION_STARTED",
            source_agent="cortex_agent",
            target_agent="limbic_agent",
            payload={
                "request_id": str(request_id or "").strip() or None,
                "session_id": str(session_id or "").strip() or None,
                "person_id": str(person_id or "").strip() or None,
                "emotion_tag": str(emotion_tag or "NEUTRAL").strip().upper(),
                "mood_state": str(mood_state or "steady").strip().lower(),
            },
        )

    def on_conversation_ended(
        self,
        *,
        request_id: str | None = None,
        session_id: str | None = None,
        person_id: str | None = None,
        duration_minutes: float | int | None = None,
    ) -> dict[str, Any]:
        from host_brain.persona.body_sense import get_body_sense_model  # noqa: PLC0415

        model = get_body_sense_model()
        try:
            resolved_duration = float(duration_minutes) if duration_minutes is not None else None
        except (TypeError, ValueError):
            resolved_duration = None
        model.on_conversation_end(duration_minutes=resolved_duration)
        state = model.get_state()
        payload = dict(state.as_dict() if hasattr(state, "as_dict") else {})
        self._touch_state(
            key="last_body_sense_state",
            value={
                "fatigue_level": str(payload.get("fatigue_level") or "normal"),
                "drowsiness_level": str(payload.get("drowsiness_level") or "normal"),
                "loneliness_level": str(payload.get("loneliness_level") or "none"),
            },
        )
        try:
            self.handle_signal(
                event_type="CONVERSATION_ENDED",
                source_agent="cortex_agent",
                target_agent="limbic_agent",
                payload={
                    "request_id": str(request_id or "").strip() or None,
                    "session_id": str(session_id or "").strip() or None,
                    "person_id": str(person_id or "").strip() or None,
                    "duration_minutes": resolved_duration,
                    "willingness_penalty": payload.get("willingness_penalty"),
                },
            )
        except Exception:
            pass
        return {
            "status": "OK",
            "body_sense": payload,
        }

    def _touch_state(self, *, key: str, value: Any) -> None:
        prev = self.state_registry.get_state("limbic") or {}
        prev_state = dict(prev.get("state") or {})
        merged = dict(prev_state)
        merged[str(key)] = value
        self.state_registry.set_state("limbic", merged, writer="limbic_agent")

    # ── TribeEventBus 订阅注册（Phase 1）────────────────────────────────────

    def register_subscriptions(self) -> None:
        """向 TribeEventBus 注册事件订阅 handler。

        在 http_hooks.py lifespan 中调用，确保 Bus 的 dispatch_loop 启动后
        各族 Agent 的 handler 能被正确触发。

        T-30：新增订阅 BOND_PROGRESS / BOND_REGRESS，
        关系变化影响情绪效价（valence）和沉默意愿（silence_willingness）。
        """
        try:
            from host_brain.app.internal_tribe_routes import get_tribe_event_bus  # noqa: PLC0415

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
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.limbic_agent").warning(
                "LimbicAgent 订阅注册失败（降级）: %s", exc
            )

    async def _on_body_sense_updated(self, event: Any) -> None:
        """处理 BODY_SENSE_UPDATED 事件：更新沉默意愿惩罚。"""
        try:
            payload = dict(event.payload or {})
            penalty = float(payload.get("willingness_penalty") or 0.0)
            if penalty > 0.0:
                self.apply_body_sense_penalty(penalty=penalty)
            self._touch_state(
                key="last_bus_body_sense_updated",
                value={
                    "source": str(event.source_agent or ""),
                    "fatigue_level": str(payload.get("fatigue_level") or "normal"),
                    "loneliness_level": str(payload.get("loneliness_level") or "none"),
                    "willingness_penalty": penalty,
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
        """处理 EMOTION_INFERENCE_UPDATED 事件：接收 SomaticAgent 的语音情绪推断结果。

        T-09：SomaticAgent（edge_client）在语音处理完成后通过 EdgeTribeBridge 发布
        emotion_inference_updated 事件，经 /internal/tribe_event 端点转发到 Bus，
        LimbicAgent 订阅后更新情绪状态快照，供后续对话上下文使用。
        """
        try:
            import logging  # noqa: PLC0415
            _log = logging.getLogger("Natsume.tribe.limbic_agent")
            payload = dict(event.payload or {})
            emotion_tag = str(payload.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
            emotion_intensity = payload.get("emotion_intensity")
            mood_state = str(payload.get("mood_state") or "").strip().lower() or None
            emotion_source = str(payload.get("emotion_source") or "somatic_voice").strip() or "somatic_voice"
            session_id = str(payload.get("session_id") or "").strip()
            _log.debug(
                "LimbicAgent 收到 EMOTION_INFERENCE_UPDATED: emotion_tag=%s intensity=%s source=%s session=%s",
                emotion_tag, emotion_intensity, emotion_source, session_id,
            )
            # 将语音情绪推断结果写入 TribeStateRegistry，供 chat_context_builder 读取
            self._touch_state(
                key="last_voice_emotion_inference",
                value={
                    "emotion_tag": emotion_tag,
                    "emotion_intensity": emotion_intensity,
                    "mood_state": mood_state,
                    "emotion_source": emotion_source,
                    "session_id": session_id,
                    "source_agent": str(event.source_agent or ""),
                    "ts_ms": int(event.ts_ms or 0),
                },
            )
            # 若情绪强度较高（>= 0.6），同步更新情绪上下文（影响当前对话情绪感知）
            if isinstance(emotion_intensity, (int, float)) and float(emotion_intensity) >= 0.6:
                try:
                    from host_brain.app.dependencies import get_hard_state  # noqa: PLC0415
                    hard_state = get_hard_state()
                    if hasattr(hard_state, "emotion_tag"):
                        hard_state.emotion_tag = emotion_tag
                    if hasattr(hard_state, "emotion_intensity"):
                        hard_state.emotion_intensity = float(emotion_intensity)
                    if mood_state and hasattr(hard_state, "mood_state"):
                        hard_state.mood_state = mood_state
                    _log.info(
                        "LimbicAgent 语音情绪推断已同步到 hard_state: emotion_tag=%s intensity=%s",
                        emotion_tag, emotion_intensity,
                    )
                except Exception as _hs_exc:
                    _log.debug("LimbicAgent 同步 hard_state 失败（降级）: %s", _hs_exc)
            # T-125：通过 EdgeCommandPusher 将情绪状态推送回 edge_client，驱动 vts_driver 表情
            # 实现 LimbicAgent → SomaticAgent 的下行反馈闭环（身心联动）
            try:
                from host_brain.tribe.edge_command_pusher import get_edge_command_pusher  # noqa: PLC0415
                _pusher = get_edge_command_pusher()
                if _pusher is not None and _pusher.enabled:
                    _push_result = _pusher.push_emotion_state(
                        emotion_tag=emotion_tag,
                        intensity=(
                            float(emotion_intensity)
                            if isinstance(emotion_intensity, (int, float))
                            else None
                        ),
                        mood_state=mood_state,
                        session_id=session_id or None,
                        source_agent="limbic_agent",
                    )
                    _log.debug(
                        "LimbicAgent 情绪指令已推送到 edge_client: emotion_tag=%s ok=%s",
                        emotion_tag, _push_result.get("ok"),
                    )
            except Exception as _push_exc:
                _log.debug("LimbicAgent 推送情绪指令失败（降级）: %s", _push_exc)
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
                    _restored = _ep.restore(user_id="user_1")
                    _cur_valence = float(getattr(_restored, "valence", 0.0))
                    _cur_arousal = float(getattr(_restored, "arousal", 0.3))
                    _cur_dominance = float(getattr(_restored, "dominance", 0.0))
                    _new_valence = max(-1.0, min(1.0, _cur_valence + valence_boost))
                    _ep.persist(
                        user_id="user_1",
                        valence=_new_valence,
                        arousal=_cur_arousal,
                        dominance=_cur_dominance,
                    )
                except Exception:
                    pass

            # 降低沉默意愿（通过 silence_engine 的 update_emotion 间接影响）
            if silence_relief > 0.01:
                try:
                    from host_brain.persona.emotional_silence import get_silence_engine  # noqa: PLC0415
                    _engine = get_silence_engine()
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
                    _restored = _ep.restore(user_id="user_1")
                    _cur_valence = float(getattr(_restored, "valence", 0.0))
                    _cur_arousal = float(getattr(_restored, "arousal", 0.3))
                    _cur_dominance = float(getattr(_restored, "dominance", 0.0))
                    _new_valence = max(-1.0, min(1.0, _cur_valence - valence_drop))
                    _ep.persist(
                        user_id="user_1",
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
        try:
            import logging  # noqa: PLC0415
            payload = dict(getattr(event, "payload", None) or {})
            intensity = max(0.0, min(1.0, float(payload.get("intensity") or 0.35)))
            valence_boost = round(min(0.06, intensity * 0.08), 4)
            if valence_boost > 0.01 and self._emotion_persistence is not None:
                try:
                    _ep = self._emotion_persistence
                    _restored = _ep.restore(user_id="user_1")
                    _cur_valence = float(getattr(_restored, "valence", 0.0))
                    _cur_arousal = float(getattr(_restored, "arousal", 0.3))
                    _cur_dominance = float(getattr(_restored, "dominance", 0.0))
                    _new_valence = max(-1.0, min(1.0, _cur_valence + valence_boost))
                    _ep.persist(
                        user_id="user_1",
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
        try:
            import logging  # noqa: PLC0415
            payload = dict(getattr(event, "payload", None) or {})
            intensity = max(0.0, min(1.0, float(payload.get("intensity") or 0.3)))
            valence_boost = round(min(0.08, intensity * 0.10), 4)
            arousal_boost = round(min(0.03, intensity * 0.04), 4)
            if valence_boost > 0.01 and self._emotion_persistence is not None:
                try:
                    _ep = self._emotion_persistence
                    _restored = _ep.restore(user_id="user_1")
                    _cur_valence = float(getattr(_restored, "valence", 0.0))
                    _cur_arousal = float(getattr(_restored, "arousal", 0.3))
                    _cur_dominance = float(getattr(_restored, "dominance", 0.0))
                    _new_valence = max(-1.0, min(1.0, _cur_valence + valence_boost))
                    _new_arousal = max(0.0, min(1.0, _cur_arousal + arousal_boost))
                    _ep.persist(
                        user_id="user_1",
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
                    _restored = _ep.restore(user_id="user_1")
                    _cur_valence = float(getattr(_restored, "valence", 0.0))
                    _cur_arousal = float(getattr(_restored, "arousal", 0.3))
                    _cur_dominance = float(getattr(_restored, "dominance", 0.0))
                    _new_valence = max(-1.0, min(1.0, _cur_valence - valence_drop))
                    _new_arousal = max(0.0, min(1.0, _cur_arousal - arousal_drop))
                    _ep.persist(
                        user_id="user_1",
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
                    _restored = _ep.restore(user_id="user_1")
                    _cur_valence = float(getattr(_restored, "valence", 0.0))
                    _cur_arousal = float(getattr(_restored, "arousal", 0.3))
                    _cur_dominance = float(getattr(_restored, "dominance", 0.0))
                    _new_valence = max(-1.0, min(1.0, _cur_valence - valence_drop))
                    _ep.persist(
                        user_id="user_1",
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


_LIMBIC_AGENT: LimbicAgent | None = None


def get_limbic_agent() -> LimbicAgent:
    global _LIMBIC_AGENT
    if _LIMBIC_AGENT is None:
        _LIMBIC_AGENT = LimbicAgent()
    return _LIMBIC_AGENT


def set_limbic_agent(agent: LimbicAgent | None) -> None:
    global _LIMBIC_AGENT
    _LIMBIC_AGENT = agent


__all__ = ["LimbicAgent", "get_limbic_agent", "set_limbic_agent"]
