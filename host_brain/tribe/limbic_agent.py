from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from host_brain.tribe.application_ports import TribeApplicationPorts
from host_brain.tribe.limbic_event_handlers import LimbicEventHandlersMixin
from host_brain.tribe.base_agent import TribeAgentDescriptor
from host_brain.tribe.events import emit_tribe_event
from host_brain.tribe.tribe_state import TribeStateRegistry, get_global_registry


_TRUSTED_RUNTIME_EMOTION_SOURCES = frozenset(
    {
        "edge_explicit",
        "face_capture",
        "somatic_voice_acoustic",
        "voice_acoustic",
        "acoustic_emotion_model",
    }
)
_TEXT_ONLY_EMOTION_SOURCES = frozenset(
    {
        "asr_text_bootstrap",
        "asr_text_unavailable",
        "text_heuristic",
        "text_bootstrap_fallback",
    }
)


def _resolve_limbic_persona_id(value: Any = None) -> str:
    try:
        from host_brain.persona.identity import resolve_persona_self_id  # noqa: PLC0415

        return resolve_persona_self_id(value)
    except Exception:
        return str(value or "").strip() or "natsume_ai"


def _canonical_limbic_persona_id() -> str:
    return _resolve_limbic_persona_id(None)


def _read_canonical_inner_world(person_id: str, *, reader: Any) -> dict[str, Any]:
    from host_brain.digital_life.application_boundary import (  # noqa: PLC0415
        project_inner_world_state,
        read_life_snapshot,
    )

    return project_inner_world_state(read_life_snapshot(person_id, reader=reader))


def _read_canonical_body_sense(person_id: str, *, reader: Any) -> dict[str, Any]:
    """Project body presence from one scoped Life snapshot without mutation."""

    from host_brain.digital_life.application_boundary import read_life_snapshot  # noqa: PLC0415
    from host_brain.persona.body_sense import (  # noqa: PLC0415
        project_body_sense_from_life_snapshot,
    )

    snapshot = read_life_snapshot(person_id, reader=reader)
    projection = project_body_sense_from_life_snapshot(snapshot).as_dict()
    projection.update(
        {
            "status": "OK",
            "authority": "CANONICAL_LIFE_JOURNAL",
            "projection": "READ_ONLY",
            "relationship_subject_id": str(person_id),
            "life_version": int(snapshot.get("version") or 0),
            "life_state_digest": str(snapshot.get("state_digest") or ""),
        }
    )
    return projection


def _read_canonical_silence_state(person_id: str, *, reader: Any) -> dict[str, Any]:
    """Project response posture from one scoped canonical regulation head.

    ``EmotionalSilenceEngine`` used to keep a process-wide willingness and
    topic-resistance state.  In a canonical Host those are consequences of
    the accepted RegulationDecision and body projection, not a second mutable
    organ.  The projection deliberately contains no caller-supplied topic or
    emotion data, so an untrusted compatibility request cannot steer
    Natsume's self-affect by asking for a silence refresh.
    """

    from host_brain.digital_life.application_boundary import read_life_snapshot  # noqa: PLC0415
    from host_brain.persona.body_sense import (  # noqa: PLC0415
        project_body_sense_from_life_snapshot,
    )

    snapshot = read_life_snapshot(person_id, reader=reader)
    state = dict(snapshot.get("state") or {})
    affective = dict(state.get("affective") or {})
    regulation = dict(affective.get("last_regulation") or {})
    regulation_mode = str(regulation.get("mode") or "NONE").strip().upper() or "NONE"
    body_sense = project_body_sense_from_life_snapshot(snapshot).as_dict()
    willingness_penalty = max(0.0, min(1.0, float(body_sense.get("willingness_penalty") or 0.0)))
    return {
        "status": "OK",
        "authority": "CANONICAL_LIFE_JOURNAL",
        "projection": "READ_ONLY",
        "relationship_subject_id": str(person_id),
        "life_version": int(snapshot.get("version") or 0),
        "life_state_digest": str(snapshot.get("state_digest") or ""),
        "response_willingness": round(max(0.0, 1.0 - willingness_penalty), 4),
        "willingness_level": (
            "SUBDUED"
            if regulation_mode in {"GIVE_SPACE", "PAUSE", "SILENCE"}
            else "NORMAL"
        ),
        "topic_resistance_active": regulation_mode in {"SET_BOUNDARY", "BOUNDARY"},
        "resistant_topic": "",
        "trigger_reason": f"canonical_regulation:{regulation_mode}",
    }


def _is_trusted_runtime_emotion_source(
    *,
    emotion_source: str,
    emotion_modality: str,
    acoustic_emotion_available: bool,
    degraded: bool,
) -> bool:
    if degraded:
        return False
    source = str(emotion_source or "").strip().lower()
    modality = str(emotion_modality or "").strip().lower()
    if source in _TEXT_ONLY_EMOTION_SOURCES or modality in {"asr_text", "text"}:
        return False
    if acoustic_emotion_available:
        return True
    return source in _TRUSTED_RUNTIME_EMOTION_SOURCES or modality in {"face_capture", "explicit"}


@dataclass(slots=True)
class LimbicAgent(LimbicEventHandlersMixin):
    application_ports: TribeApplicationPorts | None = field(default=None, repr=False)
    descriptor: TribeAgentDescriptor = field(
        default_factory=lambda: TribeAgentDescriptor(
            agent_id="limbic_agent",
            family="limbic",
            capabilities=("emotion", "motive", "body_sense", "offline_continuity"),
        )
    )
    state_registry: TribeStateRegistry = field(default_factory=get_global_registry)  # T-29：使用全局单例
    _emotion_persistence: Any | None = field(default=None, init=False, repr=False)

    def _legacy_self_affect_writes_retired(self) -> bool:
        """Return whether this Host has canonical Life self-affect authority.

        The check is deliberately derived from the immutable app-local port
        bundle.  A legacy/offline test may still exercise its isolated
        compatibility model, but a production Host must never let an event
        bus callback rebuild EmotionPersistence or append the old timeline.
        """

        ports = self.application_ports
        return bool(
            isinstance(ports, TribeApplicationPorts)
            and ports.uses_canonical_affective_life()
        )

    def uses_canonical_affective_life(self) -> bool:
        """Expose the read-only composition boundary to compatibility adapters.

        Inner-world adapters use this before they emit legacy EventBus signals
        or persist a private monologue.  The method deliberately does not
        expose a mutable state object or create an additional Life authority.
        """

        return self._legacy_self_affect_writes_retired()

    @staticmethod
    def _retired_legacy_affect_result(
        *,
        source: str,
        operation: str,
    ) -> dict[str, Any]:
        return {
            "status": "RETIRED_CANONICAL_LIFE",
            "authority": "CANONICAL_LIFE_JOURNAL",
            "reason_code": "LEGACY_SELF_AFFECT_WRITE_RETIRED",
            "operation": str(operation),
            "source": str(source or "limbic_agent"),
        }

    @staticmethod
    def _retired_body_sense_result(
        *,
        operation: str,
        person_id: str | None = None,
    ) -> dict[str, Any]:
        """Describe a retired mutable BodySense operation without hiding it.

        The old global BodySense model remains available only to isolated
        compatibility fixtures.  Returning an explicit receipt prevents a
        scheduler or legacy skill from treating a no-op as a durable update.
        """

        result = {
            "status": "RETIRED_CANONICAL_LIFE",
            "authority": "CANONICAL_LIFE_JOURNAL",
            "reason_code": "LEGACY_BODY_SENSE_MUTATION_RETIRED",
            "operation": str(operation),
        }
        if str(person_id or "").strip():
            result["relationship_subject_id"] = str(person_id).strip()
        return result

    @staticmethod
    def _retired_silence_result(
        *,
        operation: str,
        person_id: str | None = None,
    ) -> dict[str, Any]:
        """Describe a retired mutable silence operation without faking calm.

        A retired compatibility call must not look like a successful
        ``NEUTRAL``/normalization update.  Callers receive an explicit result
        and can consume the read-only canonical projection through
        ``resolve_silence_state`` instead.
        """

        result = {
            "status": "RETIRED_CANONICAL_LIFE",
            "authority": "CANONICAL_LIFE_JOURNAL",
            "reason_code": "LEGACY_SILENCE_MUTATION_RETIRED",
            "operation": str(operation),
            "silence_state": {},
        }
        if str(person_id or "").strip():
            result["relationship_subject_id"] = str(person_id).strip()
        return result

    @staticmethod
    def _retired_legacy_signal_result(
        *,
        event_type: str,
        operation: str,
    ) -> dict[str, Any]:
        """Refuse the retired global Limbic signal bus in a canonical Host.

        The old signal path wrote user-derived VAD, Bond deltas and body
        hints to ``TribeStateRegistry`` before fanning them back through the
        event bus.  It is neither durable nor scoped enough to represent
        Natsume's affect.  A canonical Host therefore acknowledges the
        compatibility call without retaining or re-publishing its payload.
        """

        return {
            "status": "RETIRED_CANONICAL_LIFE",
            "authority": "CANONICAL_LIFE_JOURNAL",
            "reason_code": "LEGACY_LIMBIC_SIGNAL_RETIRED",
            "operation": str(operation),
            "event_type": str(event_type or "UNKNOWN").strip().upper() or "UNKNOWN",
            "accepted": False,
            "state_signal_count": 0,
        }

    def _canonical_body_sense_projection(
        self,
        *,
        person_id: str | None,
    ) -> dict[str, Any]:
        """Read one relationship-scoped, canonical body projection.

        A missing subject must never fall back to the former process-global
        model: that would make one user's embodied context visible to another
        request.  The caller can deliberately degrade instead.
        """

        subject_id = str(person_id or "").strip()
        if not subject_id:
            return {
                "status": "UNAVAILABLE",
                "authority": "CANONICAL_LIFE_JOURNAL",
                "reason_code": "CANONICAL_BODY_SENSE_SUBJECT_REQUIRED",
                "body_sense": {},
            }
        if self.application_ports is None:
            raise RuntimeError("TRIBE_APPLICATION_PORTS_NOT_CONFIGURED")
        return _read_canonical_body_sense(
            subject_id,
            reader=self.application_ports.require("life_snapshot_reader")(),
        )

    def _canonical_silence_projection(
        self,
        *,
        person_id: str | None,
    ) -> dict[str, Any]:
        """Read one relationship-scoped response-posture projection.

        Canonical silence has no global fallback: a missing relationship
        subject is an unavailable read rather than permission to consult the
        former process-wide ``EmotionalSilenceEngine``.
        """

        subject_id = str(person_id or "").strip()
        if not subject_id:
            return {
                "status": "UNAVAILABLE",
                "authority": "CANONICAL_LIFE_JOURNAL",
                "reason_code": "CANONICAL_SILENCE_SUBJECT_REQUIRED",
                "silence_state": {},
            }
        if self.application_ports is None:
            raise RuntimeError("TRIBE_APPLICATION_PORTS_NOT_CONFIGURED")
        return _read_canonical_silence_state(
            subject_id,
            reader=self.application_ports.require("life_snapshot_reader")(),
        )

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    def get_emotion_context(self, *, request_body: Any) -> dict[str, Any]:
        context = dict(
            self.application_ports.require("resolve_chat_emotion_context")(
                request_body=request_body,
            )
            or {}
        )
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
        snapshot = dict(
            self.application_ports.require("build_chat_emotion_snapshot")(
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
        if self._legacy_self_affect_writes_retired():
            # Do not resolve or cache a historical mixed-affect projection in
            # production.  Canonical request fusion owns the scoped
            # self-affect hand-off; NEUTRAL is intentionally not emitted as a
            # fake substitute fact.
            del summary_window_ms, diary_window_ms
            return {
                **self._retired_legacy_affect_result(
                    source="resolve_emotion_timeline_snapshot",
                    operation="emotion_timeline_read",
                ),
                "reason_code": "LEGACY_EMOTION_TIMELINE_READ_RETIRED",
                "summary": {},
                "diary": {},
                "dominant_emotion": "",
            }
        timeline = self.application_ports.require("emotion_timeline")()
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
        if self._legacy_self_affect_writes_retired():
            # No fallback read/merge/persist is permitted.  Canonical request
            # fusion supplies self-affect to production prompts instead.
            return {
                **self._retired_legacy_affect_result(
                    source="resolve_persistent_emotion_state",
                    operation="emotion_persistence",
                ),
                "user_id": _resolve_limbic_persona_id(person_id),
                "valence": float(valence),
                "arousal": float(arousal),
                "dominance": float(dominance),
                "from_baseline": True,
                "reunion_type": "CANONICAL_LIFE_REQUIRED",
            }
        from host_brain.persona.emotion_persistence import EmotionPersistence  # noqa: PLC0415

        if self._emotion_persistence is None:
            self._emotion_persistence = EmotionPersistence()

        user_id = _resolve_limbic_persona_id(person_id)
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
        render_target_lang: str = "zh",
    ) -> dict[str, Any]:
        from host_brain.persona.emotion_reactivity import (  # noqa: PLC0415
            EmotionReactivityProfile,
            render_reactivity_hint,
        )

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
        model_attempt_trace: dict[str, Any] = {}
        try:
            reactivity_hint = render_reactivity_hint(
                user_emotion_tag=str(user_emotion_tag or ""),
                bond_stage=str(bond_stage or ""),
                modifiers=modifiers,
                render_target_lang=render_target_lang,
                model_attempt_sink=model_attempt_trace,
            )
        except Exception:
            reactivity_hint = ""
        if reactivity_hint:
            payload["reactivity_hint"] = reactivity_hint
        if model_attempt_trace:
            payload["reactivity_model_attempt"] = model_attempt_trace
        self._touch_state(
            key="last_emotion_reactivity_profile",
            value={
                "bond_stage": str(bond_stage or ""),
                "user_emotion_tag": str(user_emotion_tag or "").upper(),
                "effective_echo_weight": round(float(effective_echo_weight), 4),
            },
        )
        return payload

    def resolve_silence_state(
        self,
        *,
        current_topic: str = "",
        person_id: str | None = None,
    ) -> dict[str, Any]:
        if self._legacy_self_affect_writes_retired():
            # ``current_topic`` belongs to the old mutable engine and cannot
            # select or alter a canonical regulation decision.
            del current_topic
            return self._canonical_silence_projection(person_id=person_id)
        engine = self.application_ports.require("silence_engine")()
        state = engine.get_silence_state(current_topic=str(current_topic or ""))
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
        if self._legacy_self_affect_writes_retired():
            return
        self.application_ports.require("silence_engine")().apply_body_sense_penalty(float(penalty))

    def resolve_body_sense_state(self, *, person_id: str | None = None) -> dict[str, Any]:
        if self._legacy_self_affect_writes_retired():
            return self._canonical_body_sense_projection(person_id=person_id)
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
        if self._legacy_self_affect_writes_retired():
            return self._retired_body_sense_result(operation="body_sense_tick")
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

    def update_body_sense_loneliness(
        self,
        *,
        loneliness_value: float,
        person_id: str | None = None,
    ) -> dict[str, Any]:
        if self._legacy_self_affect_writes_retired():
            result = self._retired_body_sense_result(
                operation="body_sense_loneliness_update",
                person_id=person_id,
            )
            if str(person_id or "").strip():
                projection = self._canonical_body_sense_projection(person_id=person_id)
                result["body_sense"] = projection
            return result
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
        if self._legacy_self_affect_writes_retired():
            projection = self._canonical_body_sense_projection(person_id=person_id)
            result = self._retired_body_sense_result(
                operation="conversation_body_sense_start",
                person_id=person_id,
            )
            result.update(
                {
                    "body_sense": projection,
                    "is_active": bool(projection.get("is_active")),
                    "willingness_penalty": float(
                        projection.get("willingness_penalty") or 0.0
                    ),
                }
            )
            return result
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
        user_id = _resolve_limbic_persona_id(person_id)
        payload = _read_canonical_inner_world(
            user_id,
            reader=self.application_ports.require("life_snapshot_reader")(),
        )
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

    def tick_inner_world(self, *, person_id: str | None, emit_inner_stream: bool = True) -> dict[str, Any]:
        user_id = _resolve_limbic_persona_id(person_id)
        # SchedulerClock is the sole Life activation authority.  Tribe ticks
        # consume its durable snapshot instead of advancing a private JSON loop.
        payload = _read_canonical_inner_world(
            user_id,
            reader=self.application_ports.require("life_snapshot_reader")(),
        )
        payload.setdefault("status", "OK")
        payload.setdefault("user_id", user_id)
        payload["activation_authority"] = "scheduler_clock"
        payload["emit_inner_stream"] = bool(emit_inner_stream)
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
        from host_brain.persona.motive_runtime import build_offline_state_from_inner_state  # noqa: PLC0415

        user_id = _resolve_limbic_persona_id(person_id)
        payload = dict(
            build_offline_state_from_inner_state(
                _read_canonical_inner_world(
                    user_id,
                    reader=self.application_ports.require("life_snapshot_reader")(),
                )
            )
            or {}
        )
        payload["activation_authority"] = "canonical_life_snapshot"
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
        if self._legacy_self_affect_writes_retired():
            return self._retired_silence_result(
                operation="silence_tick",
                person_id=user_id,
            )
        engine = self.application_ports.require("silence_engine")()
        result = dict(
            engine.tick(
                user_id=_resolve_limbic_persona_id(user_id),
                current_topic=str(current_topic or ""),
                bond_summary=dict(bond_summary or {}) or None,
                emotion_context=dict(emotion_context or {}) or None,
                life_snapshot_reader=self.application_ports.require(
                    "life_snapshot_reader"
                )(),
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
        if self._legacy_self_affect_writes_retired():
            return self._retired_silence_result(operation="silence_emotion_update")
        engine = self.application_ports.require("silence_engine")()
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
        if self._legacy_self_affect_writes_retired():
            return self._retired_silence_result(operation="silence_topic_resistance")
        resolved_topic = str(topic_label or "").strip()
        if not resolved_topic:
            return {
                "status": "REJECTED",
                "reason_code": "SILENCE_TOPIC_REQUIRED",
            }
        bounded_strength = max(0.0, min(1.0, float(strength)))
        engine = self.application_ports.require("silence_engine")()
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
        user_id = _resolve_limbic_persona_id(person_id)
        # The foreground command already committed this turn's affect and
        # continuity.  Session-end hooks are observers and must not append a
        # second Life command for the same foreground occurrence.
        payload = self.resolve_inner_world_state(person_id=user_id)
        if self._legacy_self_affect_writes_retired():
            # In a canonical Host the historical hook receives VAD derived
            # from the *user* turn.  Do not retain, signal or reinterpret it
            # as Natsume's state: the accepted AffectiveTurnPlan and Life
            # journal already own the causal record.
            del valence, arousal, pending_topics, mood_baseline
            return {
                **payload,
                "authority": "CANONICAL_LIFE_JOURNAL",
                "projection": "READ_ONLY",
                "session_end_observer": "CANONICAL_LIFE_SNAPSHOT",
            }
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

    def handle_signal(
        self,
        *,
        event_type: str,
        payload: dict[str, Any] | None = None,
        source_agent: str | None = None,
        target_agent: str | None = None,
    ) -> dict[str, Any]:
        normalized_event = str(event_type or "").strip().upper() or "UNKNOWN"
        if self._legacy_self_affect_writes_retired():
            # Do not even emit the event in canonical composition.  Several
            # historical subscribers turn these unscoped compatibility
            # signals into private metabolism/body/Bond mutations; emitting a
            # no-op marker would still let a delayed subscriber recreate a
            # second authority.
            return self._retired_legacy_signal_result(
                event_type=normalized_event,
                operation="tribe_signal",
            )
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
        if self._legacy_self_affect_writes_retired():
            projection = self._canonical_body_sense_projection(person_id=person_id)
            result = self._retired_body_sense_result(
                operation="conversation_body_sense_end",
                person_id=person_id,
            )
            result["body_sense"] = projection
            return result
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

    # ── T-137：情绪代谢族化入口 ──────────────────────────────────────────────

    def drive_emotion_metabolism(
        self,
        *,
        valence: float,
        arousal: float,
        dominance: float = 0.0,
        decay_rate: float | None = None,
        blend_weight: float | None = None,
        event_kind: str = "interaction",
        source: str = "limbic_agent",
    ) -> dict[str, Any]:
        """Compatibility-only emotion metabolism for offline legacy callers.

        Production Host composition selects the canonical Digital Life journal
        and this method then returns a retired marker without mutating the
        former metabolism/persistence authority. The implementation remains
        available only for isolated legacy/offline fixtures during the single
        rollback window.

        Args:
            valence: 当前效价 [-1.0, 1.0]。
            arousal: 当前唤醒度 [-1.0, 1.0]。
            dominance: 当前支配感 [-1.0, 1.0]，默认 0.0。
            decay_rate: 衰减速率 [0.0, 1.0]，None 使用默认值（0.05）。
            blend_weight: 混合权重 [0.0, 1.0]，None 跳过混合步骤。
            event_kind: 事件类型（"interaction"/"emotion_spike"/"emotion_calm"/"bond_deepen"）。
            source: 调用来源，用于日志追踪。

        Returns:
            {"valence": float, "arousal": float, "dominance": float,
             "emotion_tag": str, "mood_state": str, "decayed": bool}
        """
        if self._legacy_self_affect_writes_retired():
            return {
                **self._retired_legacy_affect_result(
                    source=source,
                    operation="emotion_metabolism",
                ),
                "decayed": False,
                "event_kind": str(event_kind or "interaction"),
            }
        try:
            from host_brain.core.metabolism import EmotionVector  # noqa: PLC0415
            from host_brain.core.ids import now_ms  # noqa: PLC0415

            vec = EmotionVector(
                valence=float(valence),
                arousal=float(arousal),
                dominance=float(dominance),
                ts_ms=now_ms(),
            )

            # 执行衰减
            if decay_rate is not None:
                vec = vec.decay(rate=float(decay_rate))
            else:
                vec = vec.decay()

            # 可选混合（将新情绪信号混入当前状态）
            if blend_weight is not None:
                # 以当前持久化情绪作为基础，混入新信号
                try:
                    if self._emotion_persistence is not None:
                        restored = self._emotion_persistence.restore(user_id=_canonical_limbic_persona_id())
                        base_vec = EmotionVector(
                            valence=float(getattr(restored, "valence", 0.0)),
                            arousal=float(getattr(restored, "arousal", 0.3)),
                            dominance=float(getattr(restored, "dominance", 0.0)),
                            ts_ms=now_ms(),
                        )
                        vec = base_vec.blend(vec, weight=float(blend_weight))
                except Exception:
                    pass

            emotion_tag = vec.to_emotion_tag()
            mood_state = vec.to_mood_state()

            result = {
                "valence": round(vec.valence, 4),
                "arousal": round(vec.arousal, 4),
                "dominance": round(vec.dominance, 4),
                "emotion_tag": emotion_tag,
                "mood_state": mood_state,
                "decayed": True,
                "event_kind": str(event_kind or "interaction"),
                "source": str(source or "limbic_agent"),
            }
            self._touch_state(
                key="last_emotion_metabolism",
                value={
                    "valence": result["valence"],
                    "arousal": result["arousal"],
                    "emotion_tag": emotion_tag,
                    "mood_state": mood_state,
                    "event_kind": str(event_kind or "interaction"),
                },
            )
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.limbic_agent").debug(
                "LimbicAgent.drive_emotion_metabolism: valence=%.3f arousal=%.3f tag=%s mood=%s",
                vec.valence, vec.arousal, emotion_tag, mood_state,
            )
            return result
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.limbic_agent").warning(
                "LimbicAgent.drive_emotion_metabolism 失败（降级）: %s", exc
            )
            return {
                "valence": float(valence),
                "arousal": float(arousal),
                "dominance": float(dominance),
                "emotion_tag": "NEUTRAL",
                "mood_state": "steady",
                "decayed": False,
                "error": str(exc),
            }

    # ── T-138：emotion_timeline 写入族化入口 ─────────────────────────────────

    def write_emotion_timeline(
        self,
        *,
        emotion_tag: str,
        valence: float = 0.0,
        arousal: float = 0.0,
        source: str = "limbic_agent",
        ts_ms: int | None = None,
    ) -> dict[str, Any]:
        """Compatibility-only legacy EmotionTimeline append.

        Canonical Host composition never appends this projection. It receives
        an explicit retired result instead, so a delayed event-bus callback
        cannot recreate a second self-affect journal.

        Args:
            emotion_tag: 情绪标签（如 "HAPPY"/"SAD"/"NEUTRAL" 等）。
            valence: 效价 [-1.0, 1.0]，默认 0.0。
            arousal: 唤醒度 [-1.0, 1.0]，默认 0.0。
            source: 数据来源（"user_input"/"visual"/"proactive"/"bond_event"/"somatic_voice" 等）。
            ts_ms: 时间戳（毫秒），None 使用当前时间。

        Returns:
            {"status": "OK", "emotion_tag": str, "ts_ms": int}
        """
        if self._legacy_self_affect_writes_retired():
            return {
                **self._retired_legacy_affect_result(
                    source=source,
                    operation="emotion_timeline_append",
                ),
                "emotion_tag": str(emotion_tag or "NEUTRAL").upper(),
            }
        try:
            from host_brain.core.ids import now_ms  # noqa: PLC0415

            timeline = self.application_ports.require("emotion_timeline")()
            if timeline is None:
                return {"status": "NO_TIMELINE", "emotion_tag": str(emotion_tag or "")}

            resolved_ts = int(ts_ms or now_ms())
            timeline.append(
                ts_ms=resolved_ts,
                emotion_tag=str(emotion_tag or "NEUTRAL").strip().upper() or "NEUTRAL",
                valence=float(valence),
                arousal=float(arousal),
                source=str(source or "limbic_agent").strip() or "limbic_agent",
            )
            self._touch_state(
                key="last_emotion_timeline_write",
                value={
                    "emotion_tag": str(emotion_tag or "NEUTRAL").upper(),
                    "valence": round(float(valence), 4),
                    "arousal": round(float(arousal), 4),
                    "source": str(source or "limbic_agent"),
                    "ts_ms": resolved_ts,
                },
            )
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.limbic_agent").debug(
                "LimbicAgent.write_emotion_timeline: tag=%s valence=%.3f source=%s",
                emotion_tag, valence, source,
            )
            return {"status": "OK", "emotion_tag": str(emotion_tag or ""), "ts_ms": resolved_ts}
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.limbic_agent").warning(
                "LimbicAgent.write_emotion_timeline 失败（降级）: %s", exc
            )
            return {"status": "ERROR", "emotion_tag": str(emotion_tag or ""), "error": str(exc)}

    def _touch_state(self, *, key: str, value: Any) -> None:
        if self._legacy_self_affect_writes_retired():
            # ``TribeStateRegistry`` is a process-local diagnostic cache.
            # In canonical composition it must not retain a second copy of
            # self-affect, relationship posture or user-derived VAD.
            return
        prev = self.state_registry.get_state("limbic") or {}
        prev_state = dict(prev.get("state") or {})
        merged = dict(prev_state)
        merged[str(key)] = value
        self.state_registry.set_state("limbic", merged, writer="limbic_agent")

    # ── TribeEventBus 订阅注册（Phase 1）────────────────────────────────────

def get_limbic_agent() -> LimbicAgent:
    from host_brain.tribe.runtime_context import (  # noqa: PLC0415
        require_active_tribe_agent,
    )

    return require_active_tribe_agent(
        "limbic_agent",
        expected_type=LimbicAgent,
    )


__all__ = ["LimbicAgent", "get_limbic_agent"]
