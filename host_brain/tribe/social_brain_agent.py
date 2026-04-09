from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from host_brain.tribe.base_agent import TribeAgentDescriptor
from host_brain.tribe.tribe_state import TribeStateRegistry, get_global_registry


@dataclass(slots=True)
class SocialBrainAgent:
    descriptor: TribeAgentDescriptor = field(
        default_factory=lambda: TribeAgentDescriptor(
            agent_id="social_brain_agent",
            family="social_brain",
            capabilities=("bond", "style_adapter", "expression"),
        )
    )
    state_registry: TribeStateRegistry = field(default_factory=get_global_registry)  # T-31：使用全局单例

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

        T-31：新增订阅 BOND_PROGRESS / BOND_REGRESS，
        将关系变化写入 TribeStateRegistry 快照，供 aggregate_vital_signs() 读取。
        """
        try:
            from host_brain.app.internal_tribe_routes import get_tribe_event_bus  # noqa: PLC0415

            bus = get_tribe_event_bus()
            bus.subscribe("INTERACTION_RECORDED", self._on_interaction_recorded)
            # T-31：订阅关系进展/退步事件，更新 bond_summary 快照
            bus.subscribe("BOND_PROGRESS", self._on_bond_progress_snapshot)
            bus.subscribe("BOND_REGRESS", self._on_bond_regress_snapshot)
            # T-48：订阅任务成功事件，影响 bond_trend 评估
            bus.subscribe("TASK_SUCCESS", self._on_task_success_bond)
            # T-60：订阅孤独感信号，孤独感影响 bond_trend 评估
            bus.subscribe("LONELINESS_SIGNAL", self._on_loneliness_signal_bond)
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


    async def _on_bond_progress_snapshot(self, event: Any) -> None:
        """处理 BOND_PROGRESS 事件：将关系进展写入 TribeStateRegistry 快照。

        T-31：BOND_PROGRESS 触发时，从 payload 中读取最新 bond_stage/bond_trend，
        写入 social_brain_agent 命名空间，供 aggregate_vital_signs() 读取。

        同时记录最近一次关系进展的时间戳和强度，用于调试和上下文构建。
        """
        try:
            import logging  # noqa: PLC0415
            _log = logging.getLogger("Natsume.tribe.social_brain_agent")
            payload = dict(getattr(event, "payload", None) or {})
            user_id = str(payload.get("user_id") or "").strip()
            bond_stage = str(payload.get("bond_stage") or "STRANGER").strip().upper() or "STRANGER"
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
            _log.debug(
                "SocialBrainAgent 快照更新 BOND_PROGRESS: stage=%s intensity=%.2f",
                bond_stage, intensity,
            )
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.social_brain_agent").warning(
                "SocialBrainAgent._on_bond_progress_snapshot 异常: %s", exc
            )

    async def _on_bond_regress_snapshot(self, event: Any) -> None:
        """处理 BOND_REGRESS 事件：将关系退步写入 TribeStateRegistry 快照。

        T-31：BOND_REGRESS 触发时，从 payload 中读取最新 bond_stage/bond_trend，
        写入 social_brain_agent 命名空间，供 aggregate_vital_signs() 读取。

        同时记录最近一次关系退步的时间戳和强度，用于调试和上下文构建。
        """
        try:
            import logging  # noqa: PLC0415
            _log = logging.getLogger("Natsume.tribe.social_brain_agent")
            payload = dict(getattr(event, "payload", None) or {})
            user_id = str(payload.get("user_id") or "").strip()
            bond_stage = str(payload.get("bond_stage") or "STRANGER").strip().upper() or "STRANGER"
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
            _log.debug(
                "SocialBrainAgent 快照更新 BOND_REGRESS: stage=%s intensity=%.2f",
                bond_stage, intensity,
            )
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.social_brain_agent").warning(
                "SocialBrainAgent._on_bond_regress_snapshot 异常: %s", exc
            )


    async def _on_task_success_bond(self, event: Any) -> None:
        """处理 TASK_SUCCESS 事件：任务成功对 bond_trend 产生正向影响。

        T-48：任务成功时，将 bond_trend 偏置为 PROGRESS，
        并将 last_task_success 写入 social_brain_agent 快照，
        供 aggregate_vital_signs() 和 chat_context_builder 参考。
        任务成功是关系进展的正向信号，但不直接触发 BOND_PROGRESS
        （避免与 _on_interaction_recorded 中的 bond_trend 逻辑重复）。
        """
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
            _log.debug(
                "SocialBrainAgent TASK_SUCCESS 快照更新: session_id=%s intensity=%.2f",
                session_id, intensity,
            )
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.social_brain_agent").warning(
                "SocialBrainAgent._on_task_success_bond 异常: %s", exc
            )

    async def _on_loneliness_signal_bond(self, event: Any) -> None:
        """处理 LONELINESS_SIGNAL 事件：孤独感影响 bond_trend 评估。

        T-60：孤独感 > 0.6 时将 bond_trend_hint 设为 regress_risk，
        提示 SocialBrainAgent 当前关系存在退步风险，供路由决策参考。
        孤独感 > 0.8 时写入 high_loneliness 标记，触发更强的关系维护策略。
        """
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
            logging.getLogger("Natsume.tribe.social_brain_agent").debug(
                "SocialBrainAgent LONELINESS_SIGNAL: hint=%s loneliness=%.2f", hint, loneliness,
            )
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.social_brain_agent").warning(
                "SocialBrainAgent._on_loneliness_signal_bond 异常: %s", exc
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
