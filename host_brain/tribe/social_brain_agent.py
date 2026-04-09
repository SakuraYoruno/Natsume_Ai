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

    # ── TribeEventBus 订阅注册（Phase 4）────────────────────────────────────

    def register_subscriptions(self) -> None:
        """向 TribeEventBus 注册事件订阅 handler。

        在 http_hooks.py lifespan 中调用，确保 Bus 的 dispatch_loop 启动后
        SocialBrainAgent 的 handler 能被正确触发。
        """
        try:
            from host_brain.app.internal_tribe_routes import get_tribe_event_bus  # noqa: PLC0415

            bus = get_tribe_event_bus()
            bus.subscribe("INTERACTION_RECORDED", self._on_interaction_recorded)
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.social_brain_agent").warning(
                "SocialBrainAgent 订阅注册失败（降级）: %s", exc
            )

    async def _on_interaction_recorded(self, event: Any) -> None:
        """处理 INTERACTION_RECORDED 事件：触发 Bond 动态更新。

        由 CortexAgent 在对话结束后发布，SocialBrainAgent 订阅后
        异步更新 Bond 状态（关系阶段、亲密度等）。

        T-25：根据 bond_trend 发布 BOND_PROGRESS / BOND_REGRESS 事件，
        供 PainRewardAgent 接收并更新享乐状态。
        """
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
                from host_brain.tribe.events import emit_tribe_event  # noqa: PLC0415

                snapshot = self.resolve_bond_snapshot(person_id=user_id or None)
                bond_trend = str(snapshot.get("bond_trend") or "").strip().upper()
                trend_intensity = float(snapshot.get("bond_trend_intensity") or 0.3)
                bond_stage = str(snapshot.get("bond_stage") or "STRANGER").strip().upper()

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
