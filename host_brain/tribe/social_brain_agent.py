from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from host_brain.tribe.base_agent import TribeAgentDescriptor


@dataclass(slots=True)
class SocialBrainAgent:
    descriptor: TribeAgentDescriptor = field(
        default_factory=lambda: TribeAgentDescriptor(
            agent_id="social_brain_agent",
            family="social_brain",
            capabilities=("bond", "style_adapter", "expression"),
        )
    )

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    def resolve_bond_snapshot(
        self,
        *,
        person_id: str | None,
        bond_summary: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        from host_brain.app.dependencies import get_bond_stage_engine  # noqa: PLC0415
        from host_brain.bond.summarizer import normalize_bond_summary  # noqa: PLC0415

        resolved_person_id = str(person_id or "").strip()
        resolved_summary = (
            normalize_bond_summary(dict(bond_summary))
            if isinstance(bond_summary, Mapping) and bond_summary
            else {}
        )
        state_obj: Any = None
        stage = "STRANGER"
        trend = str(resolved_summary.get("trend") or "").strip()
        trend_intensity = ""
        capability_hint = str(resolved_summary.get("capability_hint") or "").strip()
        intimacy_score = 0.0
        last_event_ts_ms = 0

        try:
            engine = get_bond_stage_engine()
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
                or "STRANGER"
            ).strip() or "STRANGER"
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
            stage = str(resolved_summary.get("stage") or stage).strip() or "STRANGER"

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

    def resolve_bond_stage(self, *, person_id: str | None, default: str = "STRANGER") -> str:
        snapshot = self.resolve_bond_snapshot(person_id=person_id)
        stage = str(snapshot.get("bond_stage") or "").strip().upper()
        fallback = str(default or "STRANGER").strip().upper() or "STRANGER"
        if snapshot.get("bond_state_obj") is None and stage in {"", "STRANGER", "UNKNOWN"}:
            return fallback
        return stage or fallback

    def resolve_scheduler_bond_summary(
        self,
        *,
        session_id: str = "default",
        user_id: str | None = None,
    ) -> dict[str, Any]:
        from host_brain.app.dependencies import get_bond_stage_engine  # noqa: PLC0415

        summary: dict[str, Any] = {}
        try:
            engine = get_bond_stage_engine()
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
        from host_brain.app.dependencies import get_bond_stage_engine  # noqa: PLC0415

        try:
            return get_bond_stage_engine()
        except Exception:
            return None


_SOCIAL_BRAIN_AGENT: SocialBrainAgent | None = None


def get_social_brain_agent() -> SocialBrainAgent:
    global _SOCIAL_BRAIN_AGENT
    if _SOCIAL_BRAIN_AGENT is None:
        _SOCIAL_BRAIN_AGENT = SocialBrainAgent()
    return _SOCIAL_BRAIN_AGENT


def set_social_brain_agent(agent: SocialBrainAgent | None) -> None:
    global _SOCIAL_BRAIN_AGENT
    _SOCIAL_BRAIN_AGENT = agent


__all__ = ["SocialBrainAgent", "get_social_brain_agent", "set_social_brain_agent"]
