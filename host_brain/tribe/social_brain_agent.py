from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from host_brain.tribe.application_ports import TribeApplicationPorts
from host_brain.tribe.base_agent import TribeAgentDescriptor
from host_brain.tribe.events import emit_tribe_event
from host_brain.tribe.tribe_state import TribeStateRegistry, get_global_registry


def _resolve_social_bond_user_id(value: Any = None, *, session_id: str = "default") -> str:
    try:
        from host_brain.bond.user_identity import resolve_bond_user_id  # noqa: PLC0415

        return resolve_bond_user_id(str(value or "").strip() or None, session_id=session_id)
    except Exception:
        return str(value or "").strip() or "default"


def derive_social_bond_vector(
    *,
    bond_snapshot: Mapping[str, Any],
    repair_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Pure SocialBrain vector projection from an explicit Bond snapshot.

    Supplying the snapshot prevents read-model callers from invoking the
    legacy ``get_state()`` path, which creates an INIT state for unknown users.
    ``CONFIDANT`` is the canonical BondStageEngine name for the historical
    ``INTIMATE`` SocialBrain baseline; both remain accepted compatibility
    inputs.
    """
    snapshot = dict(bond_snapshot or {})
    stage = str(snapshot.get("bond_stage") or "FRIEND").strip().upper()
    intimacy = max(0.0, min(1.0, float(snapshot.get("intimacy_score") or 0.0)))
    stage_baseline = {
        "FRIEND": {"trust": 0.58, "affinity": 0.60, "safety": 0.64, "novelty": 0.48, "dependency": 0.32},
        "CLOSE_FRIEND": {"trust": 0.68, "affinity": 0.72, "safety": 0.74, "novelty": 0.42, "dependency": 0.44},
        "CONFIDANT": {"trust": 0.82, "affinity": 0.86, "safety": 0.82, "novelty": 0.36, "dependency": 0.62},
        "INTIMATE": {"trust": 0.82, "affinity": 0.86, "safety": 0.82, "novelty": 0.36, "dependency": 0.62},
        "SOULMATE": {"trust": 0.92, "affinity": 0.94, "safety": 0.90, "novelty": 0.28, "dependency": 0.76},
    }
    base = dict(stage_baseline.get(stage, stage_baseline["FRIEND"]))
    summary = snapshot.get("bond_summary")
    summary_map = dict(summary) if isinstance(summary, Mapping) else {}
    trend_hint = str(snapshot.get("bond_trend") or summary_map.get("trend") or "").strip().lower()
    if trend_hint in {"up", "progress"}:
        base["trust"] += 0.06
        base["affinity"] += 0.06
        base["safety"] += 0.04
    elif trend_hint in {"down", "regress", "regress_risk"}:
        base["trust"] -= 0.08
        base["safety"] -= 0.06
        base["novelty"] += 0.04

    base["trust"] += (intimacy - 0.5) * 0.30
    base["affinity"] += (intimacy - 0.5) * 0.28
    base["dependency"] += (intimacy - 0.5) * 0.20
    repair = dict(repair_state or {})
    rupture_load = max(0.0, min(1.0, float(repair.get("rupture_load") or 0.0)))
    repair_progress = max(0.0, min(1.0, float(repair.get("repair_progress") or 0.0)))
    base["repair_confidence"] = max(
        0.0,
        min(
            1.0,
            0.55
            + base["safety"] * 0.25
            - max(0.0, 0.5 - base["trust"]) * 0.35
            - rupture_load * 0.30
            + repair_progress * 0.28,
        ),
    )
    return {
        "trust": round(max(0.0, min(1.0, base["trust"])), 4),
        "affinity": round(max(0.0, min(1.0, base["affinity"])), 4),
        "safety": round(max(0.0, min(1.0, base["safety"])), 4),
        "novelty": round(max(0.0, min(1.0, base["novelty"])), 4),
        "dependency": round(max(0.0, min(1.0, base["dependency"])), 4),
        "repair_confidence": round(base["repair_confidence"], 4),
        "rupture_load": round(rupture_load, 4),
        "repair_progress": round(repair_progress, 4),
        "bond_stage": stage.lower(),
    }


@dataclass(slots=True)
class SocialBrainAgent:
    application_ports: TribeApplicationPorts | None = field(default=None, repr=False)
    descriptor: TribeAgentDescriptor = field(
        default_factory=lambda: TribeAgentDescriptor(
            agent_id="social_brain_agent",
            family="social_brain",
            capabilities=("bond", "style_adapter", "expression"),
        )
    )
    state_registry: TribeStateRegistry = field(default_factory=get_global_registry)  # T-31：使用全局单例
    _bond_stage: str = field(default="friend", init=False, repr=False)

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    def _legacy_relationship_writes_retired(self) -> bool:
        """Whether this Host has moved relationship authority to Life.

        ``SocialBrainAgent`` remains available to isolated legacy fixtures for
        one rollback window, but a composed Host cannot let its global event
        handlers rebuild a second, process-local relationship truth.  The
        decision comes from the immutable app-local port bundle rather than a
        module-global feature flag.
        """

        ports = self.application_ports
        return bool(
            isinstance(ports, TribeApplicationPorts)
            and ports.uses_canonical_affective_life()
        )

    @staticmethod
    def _retired_legacy_relationship_result(*, operation: str) -> dict[str, Any]:
        """Make a rejected compatibility mutation observable and harmless."""

        return {
            "status": "RETIRED_CANONICAL_LIFE",
            "authority": "CANONICAL_LIFE_JOURNAL",
            "reason_code": "LEGACY_SOCIAL_BRAIN_RELATIONSHIP_WRITE_RETIRED",
            "operation": str(operation),
            "accepted": False,
        }

    def _read_canonical_relationship_state(
        self,
        *,
        person_id: str | None,
    ) -> dict[str, Any]:
        """Read one bounded relationship projection without a Bond fallback.

        A missing or unreadable Life head intentionally returns an unavailable
        projection.  It must never cause this compatibility agent to consult
        ``BondStageEngine`` or ``TribeStateRegistry`` in a canonical Host.
        """

        subject_id = _resolve_social_bond_user_id(person_id)
        unavailable = {
            "authority": "CANONICAL_LIFE_JOURNAL",
            "relationship_subject_id": subject_id or None,
            "available": False,
        }
        if not subject_id or self.application_ports is None:
            return unavailable
        try:
            from host_brain.digital_life.application_boundary import (  # noqa: PLC0415
                read_life_snapshot,
            )

            reader = self.application_ports.require("life_snapshot_reader")()
            if reader is None:
                return unavailable
            life_snapshot = read_life_snapshot(subject_id, reader=reader)
            state = dict(life_snapshot.get("state") or {})
            relationship = dict(state.get("relationship_v2") or {})
            if not relationship:
                return unavailable
            declared_self_id = str(relationship.get("self_id") or "").strip()
            declared_subject_id = str(
                relationship.get("relationship_subject_id") or ""
            ).strip()
            if declared_self_id != "natsume_ai" or declared_subject_id != subject_id:
                return unavailable
            return {
                **relationship,
                "authority": "CANONICAL_LIFE_JOURNAL",
                "available": True,
                "life_version": max(0, int(life_snapshot.get("version") or 0)),
                "life_state_digest": str(life_snapshot.get("state_digest") or ""),
            }
        except Exception:
            return unavailable

    def _canonical_bond_snapshot(self, *, person_id: str | None) -> dict[str, Any]:
        """Project canonical relationship state into the legacy read shape.

        The compatibility stage is permanently ``FRIEND``: trust, warmth or a
        successful exchange may never infer romance, exclusivity, dependency
        or outreach permission.  The actual relationship projection remains
        the canonical Life journal.
        """

        relationship = self._read_canonical_relationship_state(person_id=person_id)
        subject_id = str(relationship.get("relationship_subject_id") or "").strip()
        repair_required = bool(relationship.get("repair_required"))
        rupture_load = max(0.0, min(1.0, float(relationship.get("rupture_load") or 0.0)))
        safety = max(0.0, min(1.0, float(relationship.get("safety") or 0.0)))
        texture = "guarded" if repair_required or safety < 0.35 else "familiar"
        trend = "regress_risk" if repair_required or rupture_load >= 0.5 else "steady"
        summary = {
            "stage": "FRIEND",
            "trend": trend,
            "relationship_texture": texture,
            "repair_required": repair_required,
            "proactive_permission": str(
                relationship.get("proactive_permission") or "UNKNOWN"
            ).strip().upper() or "UNKNOWN",
            "authority": "CANONICAL_LIFE_JOURNAL",
        }
        return {
            "person_id": subject_id or None,
            "bond_state_obj": None,
            "bond_summary": summary,
            "bond_stage": "FRIEND",
            "bond_trend": trend,
            "bond_trend_intensity": "",
            "capability_hint": "",
            "intimacy_score": 0.0,
            "last_event_ts_ms": 0,
            "texture": texture,
            "rift": None,
            "relationship_state": relationship,
            "authority": "CANONICAL_LIFE_JOURNAL",
        }

    def _canonical_bond_vector(self, *, person_id: str | None) -> dict[str, Any]:
        """Return a non-escalating compatibility vector from canonical Life."""

        relationship = self._read_canonical_relationship_state(person_id=person_id)
        trust = max(0.0, min(1.0, float(relationship.get("trust") or 0.0)))
        safety = max(0.0, min(1.0, float(relationship.get("safety") or 0.0)))
        attunement = max(0.0, min(1.0, float(relationship.get("attunement") or 0.0)))
        rupture_load = max(0.0, min(1.0, float(relationship.get("rupture_load") or 0.0)))
        repair_required = bool(relationship.get("repair_required"))
        repair_confidence = 0.0 if repair_required else (trust + safety) / 2.0
        return {
            "trust": round(trust, 4),
            "affinity": round(attunement, 4),
            "safety": round(safety, 4),
            "novelty": 0.0,
            "dependency": 0.0,
            "repair_confidence": round(max(0.0, min(1.0, repair_confidence)), 4),
            "rupture_load": round(rupture_load, 4),
            "repair_progress": 0.0,
            "bond_stage": "friend",
            "authority": "CANONICAL_LIFE_JOURNAL",
        }

    def resolve_bond_snapshot(
        self,
        *,
        person_id: str | None,
        bond_summary: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved_person_id = _resolve_social_bond_user_id(person_id)
        if self._legacy_relationship_writes_retired():
            # Caller-supplied summaries are legacy, unscoped values.  Once
            # canonical Life is composed they may not override the exact
            # relationship projection for this subject.
            return self._canonical_bond_snapshot(person_id=resolved_person_id)
        from host_brain.bond.summarizer import normalize_bond_summary  # noqa: PLC0415

        resolved_summary = (
            normalize_bond_summary(dict(bond_summary))
            if isinstance(bond_summary, Mapping) and bond_summary
            else {}
        )
        state_obj: Any = None
        stage = "FRIEND"
        trend = str(resolved_summary.get("trend") or "").strip()
        trend_intensity = ""
        capability_hint = str(resolved_summary.get("capability_hint") or "").strip()
        intimacy_score = 0.0
        last_event_ts_ms = 0

        try:
            engine = self.application_ports.require("bond_stage_engine")()
            if engine is not None:
                if resolved_person_id and hasattr(engine, "get_state"):
                    state_obj = engine.get_state(resolved_person_id)
                elif hasattr(engine, "get_bond_state"):
                    try:
                        state_obj = engine.get_bond_state(session_id="default", user_id=resolved_person_id or None)
                    except TypeError:
                        state_obj = engine.get_bond_state(session_id="default")
        except Exception:
            state_obj = None

        if state_obj is not None and not resolved_summary:
            try:
                raw_summary = state_obj.to_summary() if hasattr(state_obj, "to_summary") else {}
                if isinstance(raw_summary, Mapping) and raw_summary:
                    resolved_summary = normalize_bond_summary(dict(raw_summary))
            except Exception:
                pass

        if state_obj is not None:
            stage = str(
                getattr(state_obj, "current_stage", None)
                or getattr(state_obj, "stage", None)
                or resolved_summary.get("stage")
                or "FRIEND"
            ).strip() or "FRIEND"
            trend = str(
                (
                    state_obj.trend() if callable(getattr(state_obj, "trend", None)) else getattr(state_obj, "trend", "")
                )
                or resolved_summary.get("trend")
                or trend
            ).strip()
            trend_intensity = str(
                state_obj.trend_intensity()
                if callable(getattr(state_obj, "trend_intensity", None))
                else ""
            ).strip()
            capability_hint = str(
                (
                    state_obj.capability_hint()
                    if callable(getattr(state_obj, "capability_hint", None))
                    else getattr(state_obj, "label", "")
                )
                or resolved_summary.get("capability_hint")
                or capability_hint
            ).strip()
            intimacy_score = float(getattr(state_obj, "intimacy_score", 0.0) or 0.0)
            last_event_ts_ms = int(getattr(state_obj, "last_event_ts_ms", 0) or 0)
        else:
            stage = str(resolved_summary.get("stage") or stage).strip() or "FRIEND"

        if not resolved_summary:
            resolved_summary = {
                "stage": stage,
                "trend": trend,
                "capability_hint": capability_hint,
            }

        return {
            "person_id": resolved_person_id or None,
            "bond_state_obj": state_obj,
            "bond_summary": resolved_summary,
            "bond_stage": stage,
            "bond_trend": trend,
            "bond_trend_intensity": trend_intensity,
            "capability_hint": capability_hint,
            "intimacy_score": intimacy_score,
            "last_event_ts_ms": last_event_ts_ms,
            "texture": getattr(state_obj, "texture", None) if state_obj is not None else None,
            "rift": getattr(state_obj, "rift", None) if state_obj is not None else None,
        }

    def resolve_bond_stage(self, *, person_id: str | None, default: str = "FRIEND") -> str:
        snapshot = self.resolve_bond_snapshot(person_id=person_id)
        stage = str(snapshot.get("bond_stage") or "").strip().upper()
        fallback = str(default or "FRIEND").strip().upper() or "FRIEND"
        if snapshot.get("bond_state_obj") is None and stage in {"", "UNKNOWN"}:
            return fallback
        return stage or fallback

    def resolve_scheduler_bond_summary(
        self,
        *,
        session_id: str = "default",
        user_id: str | None = None,
    ) -> dict[str, Any]:
        if self._legacy_relationship_writes_retired():
            return dict(
                self._canonical_bond_snapshot(person_id=user_id).get("bond_summary")
                or {}
            )
        summary: dict[str, Any] = {}
        try:
            engine = self.application_ports.require("bond_stage_engine")()
            if engine is None:
                return {}
            state = None
            if user_id and hasattr(engine, "get_state"):
                state = engine.get_state(str(user_id))
            elif hasattr(engine, "get_bond_state"):
                try:
                    state = engine.get_bond_state(session_id=session_id, user_id=user_id)
                except TypeError:
                    state = engine.get_bond_state(session_id=session_id)
            if state is None:
                return {}
            summary = {
                "stage": str(
                    getattr(state, "stage", None)
                    or getattr(state, "current_stage", None)
                    or ""
                ).strip(),
                "trend": str(
                    getattr(state, "trend", "")
                    or (
                        state.trend() if callable(getattr(state, "trend", None)) else ""
                    )
                ).strip(),
                "capability_hint": str(
                    getattr(state, "capability_hint", "")
                    or (
                        state.capability_hint()
                        if callable(getattr(state, "capability_hint", None))
                        else getattr(state, "label", "")
                    )
                ).strip(),
            }
        except Exception:
            return {}
        return {key: value for key, value in summary.items() if value}

    def resolve_stage_engine(self) -> Any:
        if self._legacy_relationship_writes_retired():
            # A stage engine can create or promote a legacy Bond state on
            # read.  Canonical callers receive the bounded Life projection
            # instead of a mutable engine handle.
            return None
        try:
            return self.application_ports.require("bond_stage_engine")()
        except Exception:
            return None

    def resolve_bond_vector(
        self,
        *,
        person_id: str | None,
        bond_summary: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._legacy_relationship_writes_retired():
            return self._canonical_bond_vector(person_id=person_id)
        snapshot = self.resolve_bond_snapshot(person_id=person_id, bond_summary=bond_summary)
        repair_state = self.resolve_relationship_repair_state(person_id=person_id)
        return self.resolve_bond_vector_from_snapshot(
            bond_snapshot=snapshot,
            repair_state=repair_state,
        )

    def resolve_bond_vector_from_snapshot(
        self,
        *,
        bond_snapshot: Mapping[str, Any],
        repair_state: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Project a vector from caller-owned read data; never initializes Bond."""
        return derive_social_bond_vector(
            bond_snapshot=bond_snapshot,
            repair_state=repair_state,
        )

    def resolve_bond_projection(
        self,
        *,
        person_id: str,
        stage_engine: Any = None,
        appraisal_records: Any = None,
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        """Build the canonical read-only Bond projection facade."""
        if self._legacy_relationship_writes_retired():
            relationship = self._read_canonical_relationship_state(person_id=person_id)
            snapshot = self._canonical_bond_snapshot(person_id=person_id)
            vector = self._canonical_bond_vector(person_id=person_id)
            subject_id = str(snapshot.get("person_id") or "").strip() or None
            available = bool(relationship.get("available"))
            repair_required = bool(relationship.get("repair_required"))
            rupture_load = max(
                0.0,
                min(1.0, float(relationship.get("rupture_load") or 0.0)),
            )
            texture = str(snapshot.get("texture") or "guarded").strip() or "guarded"
            return {
                "schema_version": "Natsume.bond.projection.v1",
                "projection_id": "canonical_life_relationship_projection"
                f"::{subject_id or 'unavailable'}"
                f"::{max(0, int(relationship.get('life_version') or 0))}",
                "person_id": subject_id,
                "projected_at_ms": max(0, int(now_ms or 0)),
                "status": "AVAILABLE" if available else "DEGRADED",
                "reason_codes": [] if available else ["CANONICAL_RELATIONSHIP_UNAVAILABLE"],
                "read_only": True,
                "write_api_available": False,
                "write_methods": [],
                "write_effect": "NONE",
                "state_available": available,
                "authority": {
                    "legal_writer": "CANONICAL_LIFE_JOURNAL",
                    "command_path": "RelationshipLifecycleService",
                    "single_writer_enforced": True,
                    "evidence_required": True,
                    "score_stage_authority": "NONE",
                    "permission_effect": "NONE",
                },
                "compatibility": {
                    "authority": "canonical_life_projection",
                    "authoritative": False,
                    "epistemic_type": "compatibility_projection",
                    "total_score": None,
                    "current_stage": "FRIEND",
                    "event_count": 0,
                    "last_event_ts_ms": 0,
                    "trend": str(snapshot.get("bond_trend") or "steady"),
                    "trend_intensity": "",
                    "capability_hint": "",
                    "summary": dict(snapshot.get("bond_summary") or {}),
                    "permission_effect": "NONE",
                },
                "relationship": {
                    "vector": vector,
                    "vector_source": "canonical Life compatibility projection",
                    "texture": {"label": texture},
                    "rift": {
                        "active": repair_required,
                        "rift_level": round(rupture_load, 4),
                        "repair_progress": 0.0,
                        "rift_cause": "",
                        "repair_hint": "",
                    },
                    "repair": {
                        "rift_repair_progress": 0.0,
                        "social_repair_progress": 0.0,
                        "rupture_load": round(rupture_load, 4),
                        "last_rupture_ts_ms": 0,
                        "last_repair_ts_ms": 0,
                        "repair_hint": "",
                        "sources": ["canonical_life_relationship"],
                    },
                    "boundary": {
                        "status": "NONE",
                        "explicit_user_boundary": False,
                        "source_event_ref": "",
                        "expression_constraint": "NONE",
                        "permission_effect": "NONE",
                        "requires_governed_command_for_bond_change": True,
                    },
                    "relationship_permission": {
                        "changed": False,
                        "effect": "NONE",
                        "reason": "read_only_projection_cannot_change_permission",
                    },
                },
                "evidence_refs": [],
            }
        from host_brain.bond.bond_projection import build_bond_projection  # noqa: PLC0415

        return build_bond_projection(
            user_id=person_id,
            stage_engine=stage_engine,
            social_brain=self,
            appraisal_records=appraisal_records,
            now_ms=now_ms,
        )

    def get_bond_context(
        self,
        *,
        session_id: str = "default",
        user_id: str = "",
    ) -> dict[str, Any]:
        resolved_user_id = str(user_id or "").strip() or None
        snapshot = self.resolve_bond_snapshot(person_id=resolved_user_id)
        vector = self.resolve_bond_vector(person_id=resolved_user_id, bond_summary=snapshot.get("bond_summary"))
        self._bond_stage = str(snapshot.get("bond_stage") or "FRIEND").strip().lower() or "friend"
        if not self._legacy_relationship_writes_retired():
            self._touch_state(key="bond_stage", value=self._bond_stage)
            self._touch_state(key="bond_vector", value=vector)
        return {
            "session_id": str(session_id or "default").strip() or "default",
            "user_id": (
                str(snapshot.get("person_id") or "").strip()
                if self._legacy_relationship_writes_retired()
                else resolved_user_id
            )
            or resolved_user_id,
            "bond_stage": self._bond_stage,
            "bond_trend": str(snapshot.get("bond_trend") or "").strip().lower(),
            "capability_hint": str(snapshot.get("capability_hint") or "").strip(),
            "intimacy_score": float(snapshot.get("intimacy_score") or 0.0),
            "bond_vector": vector,
            "bond_summary": dict(snapshot.get("bond_summary") or {}),
            "authority": str(snapshot.get("authority") or "LEGACY_SOCIAL_BRAIN"),
        }

    # ── TribeEventBus 订阅注册（Phase 4）────────────────────────────────────

    def register_subscriptions(self) -> None:
        """向 TribeEventBus 注册事件订阅 handler。

        在 http_hooks.py lifespan 中调用，确保 Bus 的 dispatch_loop 启动后
        SocialBrainAgent 的 handler 能被正确触发。

        T-31：新增订阅 BOND_PROGRESS / BOND_REGRESS，
        将关系变化写入 TribeStateRegistry 快照，供 aggregate_vital_signs() 读取。
        """
        if self._legacy_relationship_writes_retired():
            # Every historical subscription below writes a global, unscoped
            # SocialBrain snapshot or fans into another legacy affect writer.
            # Canonical relationship changes enter through accepted
            # RelationshipLifecycle commands instead.
            return
        try:
            from host_brain.tribe.event_bus import get_tribe_event_bus  # noqa: PLC0415

            bus = get_tribe_event_bus()
            bus.subscribe("INTERACTION_RECORDED", self._on_interaction_recorded)
            # T-31：订阅关系进展/退步事件，更新 bond_summary 快照
            bus.subscribe("BOND_PROGRESS", self._on_bond_progress_snapshot)
            bus.subscribe("BOND_REGRESS", self._on_bond_regress_snapshot)
            bus.subscribe("BOND_STAGE_CHANGED", self._on_bond_stage_changed)
            # T-48：订阅任务成功事件，影响 bond_trend 评估
            bus.subscribe("TASK_SUCCESS", self._on_task_success_bond)
            # T-60：订阅孤独感信号，孤独感影响 bond_trend 评估
            bus.subscribe("LONELINESS_SIGNAL", self._on_loneliness_signal_bond)
            # T-78：订阅免疫拦截事件，拦截时将 bond_trend_hint 设为 regress_risk
            bus.subscribe("IMMUNE_BLOCK", self._on_immune_block_bond)
            # T-91：订阅幽默成功事件，幽默成功时 bond_trend_hint 设为 progress
            bus.subscribe("HUMOR_LANDED", self._on_humor_landed_bond)
            # P2-3：关系受损-修复闭环
            bus.subscribe("BOND_RUPTURE_SIGNAL", self._on_bond_rupture_signal)
            bus.subscribe("BOND_REPAIR_ACTION", self._on_bond_repair_action)
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.social_brain_agent").warning(
                "SocialBrainAgent 订阅注册失败（降级）: %s", exc
            )

    async def _on_interaction_recorded(self, event: Any) -> dict[str, Any] | None:
        """处理 INTERACTION_RECORDED 事件：触发 Bond 动态更新。

        由 CortexAgent 在对话结束后发布，SocialBrainAgent 订阅后
        异步更新 Bond 状态（关系阶段、亲密度等）。

        T-25：根据 bond_trend 发布 BOND_PROGRESS / BOND_REGRESS 事件，
        供 PainRewardAgent 接收并更新享乐状态。
        """
        if self._legacy_relationship_writes_retired():
            return self._retired_legacy_relationship_result(
                operation="interaction_recorded"
            )
        try:
            import logging  # noqa: PLC0415
            _log = logging.getLogger("Natsume.tribe.social_brain_agent")
            payload = dict(event.payload or {})
            user_id = str(payload.get("user_id") or "").strip()
            session_id = str(payload.get("session_id") or "default").strip() or "default"
            emotion_tag = str(payload.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
            emotion_intensity = float(payload.get("emotion_intensity") or 0.5)
            _log.debug(
                "SocialBrainAgent 收到 INTERACTION_RECORDED: user_id=%s session_id=%s emotion=%s",
                user_id, session_id, emotion_tag,
            )
            # Bond 更新由 CortexAgent.post_process_bond_update 负责，
            # 此处仅记录事件，避免重复写入
            # 未来可在此触发 bond_vector 更新（familiarity/trust/attachment 等）

            # T-25：读取当前 bond 快照，根据 bond_trend 发布 BOND_PROGRESS / BOND_REGRESS
            try:

                snapshot = self.resolve_bond_snapshot(person_id=user_id or None)
                bond_trend = str(snapshot.get("bond_trend") or "").strip().upper()
                trend_intensity = float(snapshot.get("bond_trend_intensity") or 0.3)
                bond_stage = str(snapshot.get("bond_stage") or "FRIEND").strip().upper()

                if bond_trend == "PROGRESS":
                    emit_tribe_event(
                        event_type="BOND_PROGRESS",
                        source_agent="social_brain_agent",
                        payload={
                            "user_id": user_id,
                            "session_id": session_id,
                            "bond_stage": bond_stage,
                            "bond_trend": bond_trend,
                            "intensity": min(1.0, trend_intensity),
                        },
                    )
                    _log.debug(
                        "SocialBrainAgent 发布 BOND_PROGRESS: user_id=%s stage=%s intensity=%.2f",
                        user_id, bond_stage, trend_intensity,
                    )
                elif bond_trend == "REGRESS":
                    emit_tribe_event(
                        event_type="BOND_REGRESS",
                        source_agent="social_brain_agent",
                        payload={
                            "user_id": user_id,
                            "session_id": session_id,
                            "bond_stage": bond_stage,
                            "bond_trend": bond_trend,
                            "intensity": min(1.0, trend_intensity),
                        },
                    )
                    _log.debug(
                        "SocialBrainAgent 发布 BOND_REGRESS: user_id=%s stage=%s intensity=%.2f",
                        user_id, bond_stage, trend_intensity,
                    )
            except Exception as inner_exc:
                _log.debug("SocialBrainAgent bond trend 事件发布跳过: %s", inner_exc)
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.social_brain_agent").warning(
                "SocialBrainAgent._on_interaction_recorded 异常: %s", exc
            )


    async def _on_bond_progress_snapshot(self, event: Any) -> dict[str, Any] | None:
        """处理 BOND_PROGRESS 事件：将关系进展写入 TribeStateRegistry 快照。

        T-31：BOND_PROGRESS 触发时，从 payload 中读取最新 bond_stage/bond_trend，
        写入 social_brain_agent 命名空间，供 aggregate_vital_signs() 读取。

        同时记录最近一次关系进展的时间戳和强度，用于调试和上下文构建。
        """
        if self._legacy_relationship_writes_retired():
            return self._retired_legacy_relationship_result(
                operation="bond_progress_snapshot"
            )
        try:
            import logging  # noqa: PLC0415
            _log = logging.getLogger("Natsume.tribe.social_brain_agent")
            payload = dict(getattr(event, "payload", None) or {})
            user_id = str(payload.get("user_id") or "").strip()
            bond_stage = str(payload.get("bond_stage") or "FRIEND").strip().upper() or "FRIEND"
            bond_trend = str(payload.get("bond_trend") or "PROGRESS").strip().upper()
            intensity = max(0.0, min(1.0, float(payload.get("intensity") or 0.3)))
            ts_ms = int(getattr(event, "ts_ms", 0) or 0)

            # 读取当前快照，合并更新
            prev = self.state_registry.get_state("social_brain_agent") or {}
            prev_state = dict(prev.get("state") or {})
            prev_state.update({
                "bond_stage": bond_stage.lower(),
                "bond_trend": bond_trend.lower(),
                "last_bond_progress": {
                    "user_id": user_id,
                    "bond_stage": bond_stage,
                    "intensity": intensity,
                    "ts_ms": ts_ms,
                },
            })
            self.state_registry.set_state(
                "social_brain_agent",
                prev_state,
                writer="social_brain_agent",
            )
            self._bond_stage = bond_stage.lower()
            self._refresh_bond_vector(user_id=user_id)
            _log.debug(
                "SocialBrainAgent 快照更新 BOND_PROGRESS: stage=%s intensity=%.2f",
                bond_stage, intensity,
            )
            # T-220：Bond 进展联动 PainRewardAgent 记录 bond_progress 奖励
            try:
                get_pain_reward_agent().record_reward(
                    source="bond_progress",
                    intensity=round(intensity * 0.85, 4),
                    metadata={"user_id": user_id, "bond_stage": bond_stage, "ts_ms": ts_ms},
                )
            except Exception:
                pass
            # T-348：Bond 进展时联动 DefaultModeAgent.tick_idle_cycle()
            # 关系进展是自我连续性的重要来源（"我与他人的关系"是自我叙事的核心）
            try:
                _dma = get_default_mode_agent()
                if hasattr(_dma, "tick_idle_cycle"):
                    _dma.tick_idle_cycle(
                        source=f"social_bond_progress:{bond_stage.lower()}",
                        person_id=user_id or _resolve_social_bond_user_id(user_id),
                    )
            except Exception:
                pass
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.social_brain_agent").warning(
                "SocialBrainAgent._on_bond_progress_snapshot 异常: %s", exc
            )

    async def _on_bond_stage_changed(self, event: Any) -> dict[str, Any] | None:
        if self._legacy_relationship_writes_retired():
            return self._retired_legacy_relationship_result(
                operation="bond_stage_changed"
            )
        try:
            payload = dict(getattr(event, "payload", None) or {})
            to_stage = str(payload.get("to_stage") or "FRIEND").strip().lower() or "friend"
            self._bond_stage = to_stage
            self._touch_state(key="bond_stage", value=to_stage)
            self._refresh_bond_vector(user_id=payload.get("user_id"))
        except Exception:
            pass

    async def _on_bond_regress_snapshot(self, event: Any) -> dict[str, Any] | None:
        """处理 BOND_REGRESS 事件：将关系退步写入 TribeStateRegistry 快照。

        T-31：BOND_REGRESS 触发时，从 payload 中读取最新 bond_stage/bond_trend，
        写入 social_brain_agent 命名空间，供 aggregate_vital_signs() 读取。

        同时记录最近一次关系退步的时间戳和强度，用于调试和上下文构建。
        """
        if self._legacy_relationship_writes_retired():
            return self._retired_legacy_relationship_result(
                operation="bond_regress_snapshot"
            )
        try:
            import logging  # noqa: PLC0415
            _log = logging.getLogger("Natsume.tribe.social_brain_agent")
            payload = dict(getattr(event, "payload", None) or {})
            user_id = str(payload.get("user_id") or "").strip()
            bond_stage = str(payload.get("bond_stage") or "FRIEND").strip().upper() or "FRIEND"
            bond_trend = str(payload.get("bond_trend") or "REGRESS").strip().upper()
            intensity = max(0.0, min(1.0, float(payload.get("intensity") or 0.4)))
            ts_ms = int(getattr(event, "ts_ms", 0) or 0)

            # 读取当前快照，合并更新
            prev = self.state_registry.get_state("social_brain_agent") or {}
            prev_state = dict(prev.get("state") or {})
            prev_state.update({
                "bond_stage": bond_stage.lower(),
                "bond_trend": bond_trend.lower(),
                "last_bond_regress": {
                    "user_id": user_id,
                    "bond_stage": bond_stage,
                    "intensity": intensity,
                    "ts_ms": ts_ms,
                },
            })
            self.state_registry.set_state(
                "social_brain_agent",
                prev_state,
                writer="social_brain_agent",
            )
            self._bond_stage = bond_stage.lower()
            self._refresh_bond_vector(user_id=user_id)
            _log.debug(
                "SocialBrainAgent 快照更新 BOND_REGRESS: stage=%s intensity=%.2f",
                bond_stage, intensity,
            )
            # T-220：Bond 退步联动 PainRewardAgent 记录 bond_regress 痛觉
            try:
                get_pain_reward_agent().record_pain(
                    source="bond_regress",
                    intensity=round(intensity * 0.8, 4),
                    metadata={"user_id": user_id, "bond_stage": bond_stage, "ts_ms": ts_ms},
                )
            except Exception:
                pass
            # T-343：Bond 退步/社交冲突时联动 InteroceptionAgent 注入躯体压力
            # 社交关系退步是强烈的躯体压力源（社交痛苦与躯体痛苦共享神经回路）
            try:
                _intero = get_interoception_agent()
                if hasattr(_intero, "inject_external_stress"):
                    _social_stress = round(min(0.45, intensity * 0.50), 4)
                    _intero.inject_external_stress(
                        source=f"social_bond_regress:{bond_stage.lower()}",
                        intensity=_social_stress,
                        ts_ms=ts_ms,
                    )
            except Exception:
                pass
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.social_brain_agent").warning(
                "SocialBrainAgent._on_bond_regress_snapshot 异常: %s", exc
            )


    async def _on_task_success_bond(self, event: Any) -> dict[str, Any] | None:
        """处理 TASK_SUCCESS 事件：任务成功对 bond_trend 产生正向影响。

        T-48：任务成功时，将 bond_trend 偏置为 PROGRESS，
        并将 last_task_success 写入 social_brain_agent 快照，
        供 aggregate_vital_signs() 和上下文装配主链参考。
        任务成功是关系进展的正向信号，但不直接触发 BOND_PROGRESS
        （避免与 _on_interaction_recorded 中的 bond_trend 逻辑重复）。
        """
        if self._legacy_relationship_writes_retired():
            return self._retired_legacy_relationship_result(
                operation="task_success_bond"
            )
        try:
            import logging  # noqa: PLC0415
            _log = logging.getLogger("Natsume.tribe.social_brain_agent")
            payload = dict(getattr(event, "payload", None) or {})
            session_id = str(payload.get("session_id") or "default").strip() or "default"
            intensity = max(0.0, min(1.0, float(payload.get("intensity") or 0.35)))
            ts_ms = int(getattr(event, "ts_ms", 0) or 0)

            # 读取当前快照，记录最近一次任务成功
            prev = self.state_registry.get_state("social_brain_agent") or {}
            prev_state = dict(prev.get("state") or {})
            prev_state["last_task_success"] = {
                "session_id": session_id,
                "intensity": intensity,
                "ts_ms": ts_ms,
            }
            # 任务成功时将 bond_trend_hint 设为 PROGRESS（hint 字段，不覆盖真实 bond_trend）
            prev_state["bond_trend_hint"] = "progress"
            prev_state["bond_trend_hint_ts_ms"] = ts_ms
            self.state_registry.set_state(
                "social_brain_agent",
                prev_state,
                writer="social_brain_agent",
            )
            self._refresh_bond_vector(user_id=payload.get("user_id"))
            _log.debug(
                "SocialBrainAgent TASK_SUCCESS 快照更新: session_id=%s intensity=%.2f",
                session_id, intensity,
            )
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.social_brain_agent").warning(
                "SocialBrainAgent._on_task_success_bond 异常: %s", exc
            )

    async def _on_loneliness_signal_bond(self, event: Any) -> dict[str, Any] | None:
        """处理 LONELINESS_SIGNAL 事件：孤独感影响 bond_trend 评估。

        T-60：孤独感 > 0.6 时将 bond_trend_hint 设为 regress_risk，
        提示 SocialBrainAgent 当前关系存在退步风险，供路由决策参考。
        孤独感 > 0.8 时写入 high_loneliness 标记，触发更强的关系维护策略。
        """
        if self._legacy_relationship_writes_retired():
            return self._retired_legacy_relationship_result(
                operation="loneliness_signal_bond"
            )
        try:
            import logging  # noqa: PLC0415
            from host_brain.core.ids import now_ms  # noqa: PLC0415

            payload = dict(getattr(event, "payload", None) or {})
            loneliness = max(0.0, min(1.0, float(payload.get("loneliness") or 0.0)))
            if loneliness <= 0.6:
                return
            ts_ms = int(getattr(event, "ts_ms", 0) or now_ms())
            hint = "high_loneliness" if loneliness > 0.8 else "regress_risk"
            self.state_registry.set_state(
                "social_brain_agent",
                {
                    "bond_trend_hint": hint,
                    "bond_trend_hint_ts_ms": ts_ms,
                    "loneliness_level": round(loneliness, 4),
                },
                writer="social_brain_agent",
            )
            self._refresh_bond_vector(user_id=payload.get("user_id"))
            logging.getLogger("Natsume.tribe.social_brain_agent").debug(
                "SocialBrainAgent LONELINESS_SIGNAL: hint=%s loneliness=%.2f", hint, loneliness,
            )
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.social_brain_agent").warning(
                "SocialBrainAgent._on_loneliness_signal_bond 异常: %s", exc
            )

    async def _on_immune_block_bond(self, event: Any) -> dict[str, Any] | None:
        """处理 IMMUNE_BLOCK 事件：拦截时将 bond_trend_hint 设为 regress_risk。

        T-78：ImmuneAgent 拦截有害内容时，说明用户可能存在越界行为，
        将 bond_trend_hint 设为 regress_risk，提示关系可能面临退步风险，
        供 CortexAgent.collect_tribe_context() 的 bond_trend_hint 字段使用。
        仅在 risk_level 为 medium/high/critical 时触发，低风险拦截不影响关系评估。
        """
        if self._legacy_relationship_writes_retired():
            return self._retired_legacy_relationship_result(
                operation="immune_block_bond"
            )
        try:
            import logging  # noqa: PLC0415
            _log = logging.getLogger("Natsume.tribe.social_brain_agent")
            payload = dict(getattr(event, "payload", None) or {})
            risk_level = str(payload.get("risk_level") or "").strip().lower()
            score = float(payload.get("score") or 0.0)
            # 仅中等及以上风险才影响关系评估
            if risk_level not in ("medium", "high", "critical") and score < 0.55:
                return
            ts_ms = int(getattr(event, "ts_ms", 0) or 0)
            self._touch_state(
                key="bond_trend_hint",
                value="regress_risk",
            )
            self._touch_state(
                key="last_immune_block_risk_level",
                value=risk_level,
            )
            self._touch_state(
                key="last_immune_block_ts_ms",
                value=ts_ms,
            )
            self._refresh_bond_vector(user_id=payload.get("user_id"))
            _log.info(
                "SocialBrainAgent IMMUNE_BLOCK: bond_trend_hint=regress_risk risk_level=%s score=%.3f",
                risk_level, score,
            )
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.social_brain_agent").warning(
                "SocialBrainAgent._on_immune_block_bond 异常: %s", exc
            )


    def _touch_state(self, *, key: str, value: Any) -> dict[str, Any] | None:
        """辅助方法：合并更新 social_brain_agent 命名空间快照中的单个字段。"""
        if self._legacy_relationship_writes_retired():
            return self._retired_legacy_relationship_result(
                operation=f"touch_state:{str(key or '').strip() or 'unknown'}"
            )
        try:
            prev = self.state_registry.get_state("social_brain_agent") or {}
            prev_state = dict(prev.get("state") or {})
            prev_state[key] = value
            self.state_registry.set_state("social_brain_agent", prev_state, writer="social_brain_agent")
        except Exception:
            pass

    def _refresh_bond_vector(self, *, user_id: Any = None) -> dict[str, Any] | None:
        if self._legacy_relationship_writes_retired():
            return self._retired_legacy_relationship_result(
                operation="refresh_bond_vector"
            )
        try:
            resolved_user = str(user_id or "").strip() or None
            vector = self.resolve_bond_vector(person_id=resolved_user)
            self._touch_state(key="bond_vector", value=vector)
            emit_tribe_event(
                event_type="BOND_VECTOR_UPDATED",
                source_agent="social_brain_agent",
                payload={
                    "user_id": resolved_user,
                    "bond_stage": vector.get("bond_stage"),
                    "bond_vector": vector,
                },
            )
        except Exception:
            pass

    def resolve_relationship_repair_state(self, *, person_id: str | None = None) -> dict[str, Any]:
        if self._legacy_relationship_writes_retired():
            relationship = self._read_canonical_relationship_state(person_id=person_id)
            return {
                "person_id": str(
                    relationship.get("relationship_subject_id") or person_id or ""
                ).strip()
                or None,
                "rupture_load": max(
                    0.0,
                    min(1.0, float(relationship.get("rupture_load") or 0.0)),
                ),
                # The Life aggregate records whether repair remains required;
                # it does not invent a percentage from an old effort score.
                "repair_progress": 0.0,
                "last_rupture_ts_ms": 0,
                "last_repair_ts_ms": 0,
                "repair_required": bool(relationship.get("repair_required")),
                "authority": "CANONICAL_LIFE_JOURNAL",
            }
        snap = self.state_registry.get_state("social_brain_agent") or {}
        state = dict(snap.get("state") or {})
        repair_state = dict(state.get("relationship_repair") or {})
        return {
            "person_id": str(person_id or "").strip() or None,
            "rupture_load": max(0.0, min(1.0, float(repair_state.get("rupture_load") or 0.0))),
            "repair_progress": max(0.0, min(1.0, float(repair_state.get("repair_progress") or 0.0))),
            "last_rupture_ts_ms": int(repair_state.get("last_rupture_ts_ms") or 0),
            "last_repair_ts_ms": int(repair_state.get("last_repair_ts_ms") or 0),
        }

    async def _on_bond_rupture_signal(self, event: Any) -> dict[str, Any] | None:
        if self._legacy_relationship_writes_retired():
            return self._retired_legacy_relationship_result(
                operation="bond_rupture_signal"
            )
        try:
            payload = dict(getattr(event, "payload", None) or {})
            severity = max(0.0, min(1.0, float(payload.get("severity") or payload.get("intensity") or 0.4)))
            ts_ms = int(getattr(event, "ts_ms", 0) or 0)
            prev = self.state_registry.get_state("social_brain_agent") or {}
            state = dict(prev.get("state") or {})
            repair_state = dict(state.get("relationship_repair") or {})
            rupture_load = max(0.0, min(1.0, float(repair_state.get("rupture_load") or 0.0) + severity * 0.55))
            repair_progress = max(0.0, min(1.0, float(repair_state.get("repair_progress") or 0.0) - severity * 0.35))
            repair_state.update(
                {
                    "rupture_load": round(rupture_load, 4),
                    "repair_progress": round(repair_progress, 4),
                    "last_rupture_ts_ms": ts_ms,
                    "last_rupture_reason": str(payload.get("reason") or "").strip() or None,
                }
            )
            state["relationship_repair"] = repair_state
            state["bond_trend_hint"] = "regress_risk"
            state["bond_trend_hint_ts_ms"] = ts_ms
            self.state_registry.set_state("social_brain_agent", state, writer="social_brain_agent")
            self._refresh_bond_vector(user_id=payload.get("user_id"))
        except Exception:
            pass

    async def _on_bond_repair_action(self, event: Any) -> dict[str, Any] | None:
        if self._legacy_relationship_writes_retired():
            return self._retired_legacy_relationship_result(
                operation="bond_repair_action"
            )
        try:
            payload = dict(getattr(event, "payload", None) or {})
            effort = max(0.0, min(1.0, float(payload.get("effort") or payload.get("intensity") or 0.4)))
            ts_ms = int(getattr(event, "ts_ms", 0) or 0)
            prev = self.state_registry.get_state("social_brain_agent") or {}
            state = dict(prev.get("state") or {})
            repair_state = dict(state.get("relationship_repair") or {})
            rupture_load = max(0.0, min(1.0, float(repair_state.get("rupture_load") or 0.0) - effort * 0.45))
            repair_progress = max(0.0, min(1.0, float(repair_state.get("repair_progress") or 0.0) + effort * 0.62))
            repair_state.update(
                {
                    "rupture_load": round(rupture_load, 4),
                    "repair_progress": round(repair_progress, 4),
                    "last_repair_ts_ms": ts_ms,
                    "last_repair_action": str(payload.get("action") or "").strip() or None,
                }
            )
            state["relationship_repair"] = repair_state
            state["bond_trend_hint"] = "progress" if effort >= 0.4 else str(state.get("bond_trend_hint") or "")
            state["bond_trend_hint_ts_ms"] = ts_ms
            self.state_registry.set_state("social_brain_agent", state, writer="social_brain_agent")
            self._refresh_bond_vector(user_id=payload.get("user_id"))

            # T-280：关系修复时联动 LimbicAgent 触发温暖情绪偏移（修复带来正向情绪）
            if effort >= 0.30:
                try:
                    _limbic = get_limbic_agent()
                    if hasattr(_limbic, "drive_emotion_metabolism"):
                        _warmth = min(0.30, effort * 0.35)
                        _limbic.drive_emotion_metabolism(
                            valence=round(_warmth * 0.70, 4),
                            arousal=round(_warmth * 0.20, 4),
                            dominance=0.05,
                            blend_weight=0.18,
                            event_kind=f"bond_repair_action:{round(effort, 3)}",
                            source="social_brain_agent",
                        )
                except Exception:
                    pass
        except Exception:
            pass

    def resolve_style_distillation(
        self,
        *,
        lang: str = "cn",
        cn_lines_path: str | None = None,
        ja_lines_path: str | None = None,
        top_n: int = 20,
    ) -> dict[str, Any]:
        """Legacy StyleDistiller compatibility shim.

        Current surface style anchoring is sample-guided and uses the style
        reference bank directly. The old style distiller path is`r`n        retired so it cannot reintroduce template-like style instructions.
        """
        resolved_lang = str(lang or "cn").strip().lower() or "cn"
        return {
            "status": "SKIPPED",
            "lang": resolved_lang,
            "reason_code": "LEGACY_STYLE_DISTILLER_REMOVED",
        }

    async def _on_humor_landed_bond(self, event: Any) -> dict[str, Any] | None:
        """处理 HUMOR_LANDED 事件：幽默成功时 bond_trend_hint 设为 progress。

        T-91：幽默成功是关系升温的正向信号，
        将 bond_trend_hint 设为 progress，供上下文装配主链参考。
        """
        if self._legacy_relationship_writes_retired():
            return self._retired_legacy_relationship_result(
                operation="humor_landed_bond"
            )
        try:
            import logging  # noqa: PLC0415
            from host_brain.core.ids import now_ms  # noqa: PLC0415

            payload = dict(getattr(event, "payload", None) or {})
            intensity = max(0.0, min(1.0, float(payload.get("intensity") or 0.3)))
            ts_ms = int(getattr(event, "ts_ms", 0) or now_ms())
            prev = self.state_registry.get_state("social_brain_agent") or {}
            prev_state = dict(prev.get("state") or {})
            prev_state.update({
                "bond_trend_hint": "progress",
                "bond_trend_hint_ts_ms": ts_ms,
                "last_humor_landed": {
                    "intensity": round(intensity, 4),
                    "ts_ms": ts_ms,
                },
            })
            self.state_registry.set_state("social_brain_agent", prev_state, writer="social_brain_agent")
            self._refresh_bond_vector(user_id=payload.get("user_id"))
            logging.getLogger("Natsume.tribe.social_brain_agent").debug(
                "SocialBrainAgent HUMOR_LANDED: bond_trend_hint=progress intensity=%.2f", intensity,
            )
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.social_brain_agent").warning(
                "SocialBrainAgent._on_humor_landed_bond 异常: %s", exc
            )

    # ── T-150：bond_narrative 族化入口 ───────────────────────────────────────

    def resolve_bond_narrative(
        self,
        *,
        user_id: str = "",
        bond_stage: str | None = None,
        now_ts_ms: int | None = None,
        first_interaction_ts_ms: int | None = None,
    ) -> dict[str, Any]:
        """T-150：构建 Bond 叙事，将 BondNarrativeLayer 族化到 SocialBrainAgent。

        SocialBrainAgent 作为 Bond 叙事的唯一构建入口，封装
        `BondNarrativeLayer.build_narrative()` 调用。

        Args:
            user_id: 用户 ID；空值使用 canonical bond user id。
            bond_stage: Bond 阶段（如 "FRIEND"/"INTIMATE" 等），None 时从内部状态读取。
            now_ts_ms: 当前时间戳（毫秒），None 使用当前时间。
            first_interaction_ts_ms: 首次互动时间戳（毫秒），可选。

        Returns:
            BondNarrativeResult.to_prompt_slot() 字典，或空字典（降级）。
        """
        if self._legacy_relationship_writes_retired():
            # Narrative generation from a score/stage would reintroduce a
            # second relationship interpreter.  Surface receives the frozen
            # canonical AffectiveTurnPlan instead.
            return {
                **self._retired_legacy_relationship_result(
                    operation="bond_narrative"
                ),
                "user_id": _resolve_social_bond_user_id(user_id),
                "bond_stage": "FRIEND",
                "prompt_slot": {},
                "narrative_count": 0,
            }
        try:
            from host_brain.bond.bond_narrative import BondNarrativeLayer  # noqa: PLC0415
            resolved_stage = str(bond_stage or self._bond_stage or "FRIEND").strip().upper() or "FRIEND"
            resolved_user_id = _resolve_social_bond_user_id(user_id)
            memory_index = self.application_ports.require("shared_memory_index")()
            milestone_tracker = self.application_ports.require("milestone_tracker")()
            layer = BondNarrativeLayer(
                memory_index=memory_index,
                milestone_tracker=milestone_tracker,
                use_llm=True,
                model_attempt_service=self.application_ports.require(
                    "model_attempt_service"
                )(),
            )
            result = layer.build_narrative(
                user_id=resolved_user_id,
                bond_stage=resolved_stage,
                now_ts_ms=int(now_ts_ms) if now_ts_ms is not None else None,
                first_interaction_ts_ms=int(first_interaction_ts_ms) if first_interaction_ts_ms is not None else None,
            )
            slot = result.to_prompt_slot() if hasattr(result, "to_prompt_slot") else {}
            slot_payload = dict(slot) if isinstance(slot, dict) else {}
            slot_payload["status"] = "OK"
            slot_payload["bond_stage"] = resolved_stage
            payload = {
                "status": "OK",
                "user_id": resolved_user_id,
                "bond_stage": resolved_stage,
                "prompt_slot": slot_payload,
                "narrative_count": len(slot_payload.get("narrative_items") or []),
            }
            # 兼容旧调用方：保留扁平键访问
            payload.update(slot_payload)
            self._touch_state(
                key="last_bond_narrative",
                value={
                    "user_id": resolved_user_id,
                    "bond_stage": resolved_stage,
                    "narrative_count": len(slot_payload.get("narrative_items") or []),
                },
            )
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.social_brain_agent").debug(
                "SocialBrainAgent.resolve_bond_narrative: user=%s stage=%s items=%d",
                user_id, resolved_stage, len(slot_payload.get("narrative_items") or []),
            )
            return payload
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.social_brain_agent").warning(
                "SocialBrainAgent.resolve_bond_narrative 失败（降级）: %s", exc
            )
            return {
                "status": "ERROR",
                "user_id": _resolve_social_bond_user_id(user_id),
                "prompt_slot": {},
                "error": str(exc),
            }

    # ── T-151：shared_memory_index 族化入口 ──────────────────────────────────

    def resolve_shared_memory_index(
        self,
        *,
        max_entries: int = 5,
        min_ts_ms: int = 0,
    ) -> dict[str, Any]:
        """T-151：查询共同记忆索引，将 SharedMemoryIndex 族化到 SocialBrainAgent。

        SocialBrainAgent 作为共同记忆查询的唯一入口，封装
        `SharedMemoryIndex.query()` 调用。

        Args:
            max_entries: 最大返回条数，默认 5。
            min_ts_ms: 最早时间戳过滤（毫秒），默认 0（不过滤）。

        Returns:
            SharedMemoryResult.to_prompt_slot() 字典，或空字典（降级）。
        """
        try:
            memory_index = self.application_ports.require("shared_memory_index")()
            if memory_index is None:
                return {"status": "NO_INDEX", "entries": []}

            result = memory_index.query(
                max_entries=int(max_entries),
                min_ts_ms=int(min_ts_ms),
            )
            slot = result.to_prompt_slot() if hasattr(result, "to_prompt_slot") else {}
            slot["status"] = "OK"
            slot["entry_count"] = len(slot.get("entries") or [])
            self._touch_state(
                key="last_shared_memory_query",
                value={
                    "max_entries": int(max_entries),
                    "entry_count": slot["entry_count"],
                    "min_ts_ms": int(min_ts_ms),
                },
            )
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.social_brain_agent").debug(
                "SocialBrainAgent.resolve_shared_memory_index: entries=%d max=%d",
                slot["entry_count"], max_entries,
            )
            return slot
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.social_brain_agent").warning(
                "SocialBrainAgent.resolve_shared_memory_index 失败（降级）: %s", exc
            )
            return {"status": "ERROR", "entries": [], "error": str(exc)}


    # ── T-159：milestone_tracker 族化入口 ────────────────────────────────────

    def resolve_milestone_tracker(
        self,
        *,
        user_id: str = "",
        event_type: str | None = None,
        ts_ms: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """T-159：触发里程碑检测，将 MilestoneTracker 族化到 SocialBrainAgent。

        SocialBrainAgent 作为里程碑检测的唯一调用入口，封装
        `MilestoneTracker.on_event()` 调用。

        Args:
            user_id: 用户 ID；空值使用 canonical bond user id。
            event_type: Bond 事件类型（如 "INTERACTION_DEEP"/"STAGE_PROMOTED" 等），
                        None 时仅查询已达成里程碑，不触发新检测。
            ts_ms: 时间戳（毫秒），None 使用当前时间。
            metadata: 额外信息（如 from_stage/to_stage），可选。

        Returns:
            {"status": "OK", "new_milestones": list, "achieved_count": int}
        """
        if self._legacy_relationship_writes_retired():
            # A legacy stage/milestone may neither promote relationship state
            # nor reward the retired affect metabolism in a canonical Host.
            return {
                **self._retired_legacy_relationship_result(
                    operation="milestone_tracker"
                ),
                "user_id": _resolve_social_bond_user_id(user_id),
                "new_milestones": [],
                "new_milestone_count": 0,
                "achieved_count": 0,
                "event_type": str(event_type or ""),
            }
        try:
            from host_brain.core.ids import now_ms as _now_ms  # noqa: PLC0415

            tracker = self.application_ports.require("milestone_tracker")()
            if tracker is None:
                return {"status": "NO_TRACKER", "new_milestones": [], "achieved_count": 0}

            resolved_ts = int(ts_ms or _now_ms())
            resolved_user_id = _resolve_social_bond_user_id(user_id)
            new_milestones: list[dict[str, Any]] = []

            if event_type is not None:
                raw_milestones = tracker.on_event(
                    user_id=resolved_user_id,
                    event_type=str(event_type or "").strip().upper(),
                    ts_ms=resolved_ts,
                    metadata=dict(metadata or {}),
                )
                new_milestones = [
                    m.to_dict() if hasattr(m, "to_dict") else {"milestone_id": str(m)}
                    for m in (raw_milestones or [])
                ]

            # 查询已达成里程碑总数
            achieved_count = 0
            if hasattr(tracker, "get_achieved"):
                achieved_count = len(tracker.get_achieved(resolved_user_id) or [])

            result = {
                "status": "OK",
                "user_id": resolved_user_id,
                "new_milestones": new_milestones,
                "new_milestone_count": len(new_milestones),
                "achieved_count": achieved_count,
                "event_type": str(event_type or ""),
                "ts_ms": resolved_ts,
            }
            if new_milestones:
                self._touch_state(
                    key="last_milestone_achieved",
                    value={
                        "user_id": resolved_user_id,
                        "new_milestones": [m.get("milestone_id") for m in new_milestones],
                        "ts_ms": resolved_ts,
                    },
                )
                # T-318：里程碑达成时联动 PainRewardAgent 记录高强度奖励事件（里程碑是重要正向强化）
                try:
                    _pr = get_pain_reward_agent()
                    if hasattr(_pr, "record_reward_event"):
                        _milestone_reward = round(min(0.90, 0.60 + len(new_milestones) * 0.10), 4)
                        _pr.record_reward_event(
                            source=f"milestone_achieved:{len(new_milestones)}",
                            reward_level=_milestone_reward,
                            metadata={
                                "user_id": resolved_user_id,
                                "milestone_ids": [m.get("milestone_id") for m in new_milestones],
                                "achieved_count": achieved_count,
                                "ts_ms": resolved_ts,
                            },
                        )
                except Exception:
                    pass
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.social_brain_agent").debug(
                "SocialBrainAgent.resolve_milestone_tracker: user=%s event=%s new=%d",
                user_id, event_type, len(new_milestones),
            )
            return result
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.social_brain_agent").warning(
                "SocialBrainAgent.resolve_milestone_tracker 失败（降级）: %s", exc
            )
    def export_runtime_state(self) -> dict[str, Any]:
        """T-275：导出 SocialBrainAgent 当前运行时状态快照。

        读取 state_registry 中的 social_brain_agent 命名空间快照，
        不触发任何副作用（不 emit 事件，不写 state_registry）。

        Returns:
            dict: 包含 bond_stage/bond_vector/repair_needed 等关键字段。
        """
        if self._legacy_relationship_writes_retired():
            relationship = self._read_canonical_relationship_state(person_id=None)
            vector = self._canonical_bond_vector(person_id=None)
            return {
                "status": "CANONICAL_COMPATIBILITY_PROJECTION",
                "authority": "CANONICAL_LIFE_JOURNAL",
                "bond_stage": "FRIEND",
                "bond_trend": (
                    "regress_risk"
                    if bool(relationship.get("repair_required"))
                    else "steady"
                ),
                "bond_vector_intimacy": 0.0,
                "bond_vector_trust": round(float(vector.get("trust") or 0.0), 4),
                "bond_vector_dependency": 0.0,
                "repair_needed": bool(relationship.get("repair_required")),
                "repair_reason": "canonical_relationship_repair_required"
                if bool(relationship.get("repair_required"))
                else "",
                "last_milestone_ids": [],
                "milestone_achieved_count": 0,
                "last_updated_ts_ms": 0,
            }
        snap = self.state_registry.get_state("social_brain_agent") or {}
        state = dict(snap.get("state") or {})
        bond_vector = dict(state.get("bond_vector") or {})
        repair_state = dict(state.get("repair_state") or {})
        last_milestone = dict(state.get("last_milestone_achieved") or {})
        return {
            "bond_stage": str(state.get("bond_stage") or "FRIEND").strip().upper(),
            "bond_trend": str(state.get("bond_trend") or "stable").strip().lower(),
            "bond_vector_intimacy": round(float(bond_vector.get("intimacy") or 0.0), 4),
            "bond_vector_trust": round(float(bond_vector.get("trust") or 0.0), 4),
            "bond_vector_dependency": round(float(bond_vector.get("dependency") or 0.0), 4),
            "repair_needed": bool(repair_state.get("repair_needed", False)),
            "repair_reason": str(repair_state.get("repair_reason") or ""),
            "last_milestone_ids": list(last_milestone.get("new_milestones") or []),
            "milestone_achieved_count": int(state.get("milestone_achieved_count") or 0),
            "last_updated_ts_ms": int(state.get("ts_ms") or 0),
        }


def get_social_brain_agent() -> SocialBrainAgent:
    from host_brain.tribe.runtime_context import (  # noqa: PLC0415
        require_active_tribe_agent,
    )

    return require_active_tribe_agent(
        "social_brain_agent",
        expected_type=SocialBrainAgent,
    )


__all__ = ["SocialBrainAgent", "get_social_brain_agent"]

# ── 跨模块引用（供测试 monkeypatch 使用）────────────────────────────────────
from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: E402, F401
from host_brain.tribe.pain_reward_agent import get_pain_reward_agent  # noqa: E402, F401
from host_brain.tribe.default_mode_agent import get_default_mode_agent  # noqa: E402, F401
from host_brain.tribe.events import emit_tribe_event  # noqa: E402, F401
from host_brain.tribe.interoception_agent import get_interoception_agent  # noqa: E402, F401
