from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from host_brain.tribe.base_agent import TribeAgentDescriptor
from host_brain.tribe.events import emit_tribe_event
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
    _bond_stage: str = field(default="stranger", init=False, repr=False)

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

    def resolve_bond_vector(
        self,
        *,
        person_id: str | None,
        bond_summary: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        snapshot = self.resolve_bond_snapshot(person_id=person_id, bond_summary=bond_summary)
        stage = str(snapshot.get("bond_stage") or "STRANGER").strip().upper()
        intimacy = max(0.0, min(1.0, float(snapshot.get("intimacy_score") or 0.0)))

        stage_baseline = {
            "STRANGER": {"trust": 0.25, "affinity": 0.20, "safety": 0.32, "novelty": 0.72, "dependency": 0.06},
            "ACQUAINTANCE": {"trust": 0.38, "affinity": 0.34, "safety": 0.45, "novelty": 0.62, "dependency": 0.14},
            "FRIEND": {"trust": 0.58, "affinity": 0.60, "safety": 0.64, "novelty": 0.48, "dependency": 0.32},
            "CLOSE_FRIEND": {"trust": 0.68, "affinity": 0.72, "safety": 0.74, "novelty": 0.42, "dependency": 0.44},
            "INTIMATE": {"trust": 0.82, "affinity": 0.86, "safety": 0.82, "novelty": 0.36, "dependency": 0.62},
            "SOULMATE": {"trust": 0.92, "affinity": 0.94, "safety": 0.90, "novelty": 0.28, "dependency": 0.76},
        }
        base = dict(stage_baseline.get(stage, stage_baseline["STRANGER"]))
        trend_hint = str((snapshot.get("bond_trend") or snapshot.get("bond_summary", {}).get("trend") or "")).strip().lower()
        if trend_hint in {"up", "progress"}:
            base["trust"] += 0.06
            base["affinity"] += 0.06
            base["safety"] += 0.04
        elif trend_hint in {"down", "regress", "regress_risk"}:
            base["trust"] -= 0.08
            base["safety"] -= 0.06
            base["novelty"] += 0.04

        # intimacy_score 作为连续校准项，防止只靠 stage 离散跳变
        base["trust"] += (intimacy - 0.5) * 0.30
        base["affinity"] += (intimacy - 0.5) * 0.28
        base["dependency"] += (intimacy - 0.5) * 0.20
        # P2-3: repair_confidence 动态闭环（受损负载 + 修复进度）
        repair_state = self.resolve_relationship_repair_state(person_id=person_id)
        rupture_load = float(repair_state.get("rupture_load") or 0.0)
        repair_progress = float(repair_state.get("repair_progress") or 0.0)
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

        vector = {
            "trust": round(max(0.0, min(1.0, base["trust"])), 4),
            "affinity": round(max(0.0, min(1.0, base["affinity"])), 4),
            "safety": round(max(0.0, min(1.0, base["safety"])), 4),
            "novelty": round(max(0.0, min(1.0, base["novelty"])), 4),
            "dependency": round(max(0.0, min(1.0, base["dependency"])), 4),
            "repair_confidence": round(max(0.0, min(1.0, base["repair_confidence"])), 4),
            "rupture_load": round(max(0.0, min(1.0, rupture_load)), 4),
            "repair_progress": round(max(0.0, min(1.0, repair_progress)), 4),
            "bond_stage": stage.lower(),
        }
        return vector

    def get_bond_context(
        self,
        *,
        session_id: str = "default",
        user_id: str = "",
    ) -> dict[str, Any]:
        resolved_user_id = str(user_id or "").strip() or None
        snapshot = self.resolve_bond_snapshot(person_id=resolved_user_id)
        vector = self.resolve_bond_vector(person_id=resolved_user_id, bond_summary=snapshot.get("bond_summary"))
        self._bond_stage = str(snapshot.get("bond_stage") or "STRANGER").strip().lower() or "stranger"
        self._touch_state(key="bond_stage", value=self._bond_stage)
        self._touch_state(key="bond_vector", value=vector)
        return {
            "session_id": str(session_id or "default").strip() or "default",
            "user_id": resolved_user_id,
            "bond_stage": self._bond_stage,
            "bond_trend": str(snapshot.get("bond_trend") or "").strip().lower(),
            "capability_hint": str(snapshot.get("capability_hint") or "").strip(),
            "intimacy_score": float(snapshot.get("intimacy_score") or 0.0),
            "bond_vector": vector,
            "bond_summary": dict(snapshot.get("bond_summary") or {}),
        }

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
                        person_id=str(user_id or "user_1") or "user_1",
                    )
            except Exception:
                pass
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.social_brain_agent").warning(
                "SocialBrainAgent._on_bond_progress_snapshot 异常: %s", exc
            )

    async def _on_bond_stage_changed(self, event: Any) -> None:
        try:
            payload = dict(getattr(event, "payload", None) or {})
            to_stage = str(payload.get("to_stage") or "STRANGER").strip().lower() or "stranger"
            self._bond_stage = to_stage
            self._touch_state(key="bond_stage", value=to_stage)
            self._refresh_bond_vector(user_id=payload.get("user_id"))
        except Exception:
            pass

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


    async def _on_task_success_bond(self, event: Any) -> None:
        """处理 TASK_SUCCESS 事件：任务成功对 bond_trend 产生正向影响。

        T-48：任务成功时，将 bond_trend 偏置为 PROGRESS，
        并将 last_task_success 写入 social_brain_agent 快照，
        供 aggregate_vital_signs() 和上下文装配主链参考。
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
            self._refresh_bond_vector(user_id=payload.get("user_id"))
            logging.getLogger("Natsume.tribe.social_brain_agent").debug(
                "SocialBrainAgent LONELINESS_SIGNAL: hint=%s loneliness=%.2f", hint, loneliness,
            )
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.social_brain_agent").warning(
                "SocialBrainAgent._on_loneliness_signal_bond 异常: %s", exc
            )

    async def _on_immune_block_bond(self, event: Any) -> None:
        """处理 IMMUNE_BLOCK 事件：拦截时将 bond_trend_hint 设为 regress_risk。

        T-78：ImmuneAgent 拦截有害内容时，说明用户可能存在越界行为，
        将 bond_trend_hint 设为 regress_risk，提示关系可能面临退步风险，
        供 CortexAgent.collect_tribe_context() 的 bond_trend_hint 字段使用。
        仅在 risk_level 为 medium/high/critical 时触发，低风险拦截不影响关系评估。
        """
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


    def _touch_state(self, *, key: str, value: Any) -> None:
        """辅助方法：合并更新 social_brain_agent 命名空间快照中的单个字段。"""
        try:
            prev = self.state_registry.get_state("social_brain_agent") or {}
            prev_state = dict(prev.get("state") or {})
            prev_state[key] = value
            self.state_registry.set_state("social_brain_agent", prev_state, writer="social_brain_agent")
        except Exception:
            pass

    def _refresh_bond_vector(self, *, user_id: Any = None) -> None:
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

    async def _on_bond_rupture_signal(self, event: Any) -> None:
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

    async def _on_bond_repair_action(self, event: Any) -> None:
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

    # ── T-140：canon_style_store 族化入口 ────────────────────────────────────

    def resolve_style_canon(
        self,
        *,
        canon_id: str = "natsume_ai_cn",
        style_root: str | None = None,
    ) -> dict[str, Any]:
        """T-140：获取风格 Canon 条目，将 StyleCanonStore 族化到 SocialBrainAgent。

        SocialBrainAgent 作为风格 Canon 的唯一访问入口，确保风格数据
        不在上下文装配等模块中直接调用 StyleCanonStore。

        Args:
            canon_id: Canon ID，默认 "natsume_ai_cn"。
            style_root: 风格文件根目录（可选，None 使用默认路径）。

        Returns:
            StyleCanonEntry.to_dict() 或空字典（降级）。
        """
        try:
            from host_brain.memory.canon_style_store import StyleCanonStore  # noqa: PLC0415

            kwargs: dict[str, Any] = {}
            if style_root is not None:
                kwargs["style_root"] = style_root
            store = StyleCanonStore(**kwargs)
            entry = store.get(str(canon_id or "natsume_ai_cn").strip() or "natsume_ai_cn")
            if entry is None:
                return {"status": "NOT_FOUND", "canon_id": str(canon_id or "")}
            result = entry.to_dict() if hasattr(entry, "to_dict") else {}
            result["status"] = "OK"
            self._touch_state(
                key="last_style_canon",
                value={
                    "canon_id": str(canon_id or ""),
                    "version": result.get("version"),
                    "hash": str(result.get("hash") or "")[:16],
                    "tone_only": result.get("tone_only"),
                },
            )
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.social_brain_agent").debug(
                "SocialBrainAgent.resolve_style_canon: canon_id=%s version=%s",
                canon_id, result.get("version"),
            )
            return result
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.social_brain_agent").warning(
                "SocialBrainAgent.resolve_style_canon 失败（降级）: %s", exc
            )
            return {"status": "ERROR", "canon_id": str(canon_id or ""), "error": str(exc)}

    def resolve_style_canon_content(
        self,
        *,
        canon_id: str = "natsume_ai_cn",
    ) -> str:
        """T-140：获取风格 Canon 的纯文本内容（供 prompt 注入使用）。

        Returns:
            Canon 内容字符串，或空字符串（降级）。
        """
        result = self.resolve_style_canon(canon_id=canon_id)
        return str(result.get("content") or "").strip()

    # ── T-141：style_distiller 族化入口 ──────────────────────────────────────

    def resolve_style_distillation(
        self,
        *,
        lang: str = "cn",
        cn_lines_path: str | None = None,
        ja_lines_path: str | None = None,
        top_n: int = 20,
    ) -> dict[str, Any]:
        """T-141：执行风格蒸馏，将 StyleDistiller 族化到 SocialBrainAgent。

        SocialBrainAgent 作为风格蒸馏的唯一调用入口，确保蒸馏逻辑
        不在上下文装配等模块中直接调用 StyleDistiller。

        Args:
            lang: 语言，"cn" 或 "ja"，默认 "cn"。
            cn_lines_path: 中文台词文件路径（可选，None 使用默认路径）。
            ja_lines_path: 日文台词文件路径（可选，None 使用默认路径）。
            top_n: 返回的特征词汇数量，默认 20。

        Returns:
            StyleProfile 字典或空字典（降级）。
        """
        try:
            from host_brain.retrieval.style_distiller import StyleDistiller  # noqa: PLC0415

            kwargs: dict[str, Any] = {}
            if cn_lines_path is not None:
                kwargs["cn_lines_path"] = cn_lines_path
            if ja_lines_path is not None:
                kwargs["ja_lines_path"] = ja_lines_path
            distiller = StyleDistiller(**kwargs)

            resolved_lang = str(lang or "cn").strip().lower()
            if resolved_lang == "ja":
                profile = distiller.distill_ja(top_n=int(top_n))
            else:
                profile = distiller.distill_cn(top_n=int(top_n))

            result = profile.to_dict() if hasattr(profile, "to_dict") else {}
            result["status"] = "OK"
            result["lang"] = resolved_lang
            self._touch_state(
                key="last_style_distillation",
                value={
                    "lang": resolved_lang,
                    "sentence_patterns_count": len(result.get("sentence_patterns") or []),
                    "vocab_count": len(result.get("characteristic_vocab") or []),
                },
            )
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.social_brain_agent").debug(
                "SocialBrainAgent.resolve_style_distillation: lang=%s patterns=%d",
                resolved_lang, len(result.get("sentence_patterns") or []),
            )
            return result
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.social_brain_agent").warning(
                "SocialBrainAgent.resolve_style_distillation 失败（降级）: %s", exc
            )
            return {"status": "ERROR", "lang": str(lang or "cn"), "error": str(exc)}

    async def _on_humor_landed_bond(self, event: Any) -> None:
        """处理 HUMOR_LANDED 事件：幽默成功时 bond_trend_hint 设为 progress。

        T-91：幽默成功是关系升温的正向信号，
        将 bond_trend_hint 设为 progress，供上下文装配主链参考。
        """
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
        user_id: str = "user_1",
        bond_stage: str | None = None,
        now_ts_ms: int | None = None,
        first_interaction_ts_ms: int | None = None,
    ) -> dict[str, Any]:
        """T-150：构建 Bond 叙事，将 BondNarrativeLayer 族化到 SocialBrainAgent。

        SocialBrainAgent 作为 Bond 叙事的唯一构建入口，封装
        `BondNarrativeLayer.build_narrative()` 调用。

        Args:
            user_id: 用户 ID，默认 "user_1"。
            bond_stage: Bond 阶段（如 "FRIEND"/"INTIMATE" 等），None 时从内部状态读取。
            now_ts_ms: 当前时间戳（毫秒），None 使用当前时间。
            first_interaction_ts_ms: 首次互动时间戳（毫秒），可选。

        Returns:
            BondNarrativeResult.to_prompt_slot() 字典，或空字典（降级）。
        """
        try:
            from host_brain.bond.bond_narrative import BondNarrativeLayer  # noqa: PLC0415
            from host_brain.app.dependencies import (  # noqa: PLC0415
                get_shared_memory_index,
                get_milestone_tracker,
            )

            resolved_stage = str(bond_stage or self._bond_stage or "STRANGER").strip().upper() or "STRANGER"
            memory_index = get_shared_memory_index()
            milestone_tracker = get_milestone_tracker()
            layer = BondNarrativeLayer(
                memory_index=memory_index,
                milestone_tracker=milestone_tracker,
            )
            result = layer.build_narrative(
                user_id=str(user_id or "user_1").strip() or "user_1",
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
                "user_id": str(user_id or "user_1").strip() or "user_1",
                "bond_stage": resolved_stage,
                "prompt_slot": slot_payload,
                "narrative_count": len(slot_payload.get("narrative_items") or []),
            }
            # 兼容旧调用方：保留扁平键访问
            payload.update(slot_payload)
            self._touch_state(
                key="last_bond_narrative",
                value={
                    "user_id": str(user_id or "user_1"),
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
                "user_id": str(user_id or "user_1"),
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
            from host_brain.app.dependencies import get_shared_memory_index  # noqa: PLC0415

            memory_index = get_shared_memory_index()
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
        user_id: str = "user_1",
        event_type: str | None = None,
        ts_ms: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """T-159：触发里程碑检测，将 MilestoneTracker 族化到 SocialBrainAgent。

        SocialBrainAgent 作为里程碑检测的唯一调用入口，封装
        `MilestoneTracker.on_event()` 调用。

        Args:
            user_id: 用户 ID，默认 "user_1"。
            event_type: Bond 事件类型（如 "INTERACTION_DEEP"/"STAGE_PROMOTED" 等），
                        None 时仅查询已达成里程碑，不触发新检测。
            ts_ms: 时间戳（毫秒），None 使用当前时间。
            metadata: 额外信息（如 from_stage/to_stage），可选。

        Returns:
            {"status": "OK", "new_milestones": list, "achieved_count": int}
        """
        try:
            from host_brain.app.dependencies import get_milestone_tracker  # noqa: PLC0415
            from host_brain.core.ids import now_ms as _now_ms  # noqa: PLC0415

            tracker = get_milestone_tracker()
            if tracker is None:
                return {"status": "NO_TRACKER", "new_milestones": [], "achieved_count": 0}

            resolved_ts = int(ts_ms or _now_ms())
            new_milestones: list[dict[str, Any]] = []

            if event_type is not None:
                raw_milestones = tracker.on_event(
                    user_id=str(user_id or "user_1"),
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
                achieved_count = len(tracker.get_achieved(str(user_id or "user_1")) or [])

            result = {
                "status": "OK",
                "user_id": str(user_id or "user_1"),
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
                        "user_id": str(user_id or "user_1"),
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
                                "user_id": str(user_id or "user_1"),
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
        snap = self.state_registry.get_state("social_brain_agent") or {}
        state = dict(snap.get("state") or {})
        bond_vector = dict(state.get("bond_vector") or {})
        repair_state = dict(state.get("repair_state") or {})
        last_milestone = dict(state.get("last_milestone_achieved") or {})
        return {
            "bond_stage": str(state.get("bond_stage") or "STRANGER").strip().upper(),
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

# ── 跨模块引用（供测试 monkeypatch 使用）────────────────────────────────────
from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: E402, F401
from host_brain.tribe.pain_reward_agent import get_pain_reward_agent  # noqa: E402, F401
from host_brain.tribe.default_mode_agent import get_default_mode_agent  # noqa: E402, F401
from host_brain.tribe.events import emit_tribe_event  # noqa: E402, F401
from host_brain.tribe.interoception_agent import get_interoception_agent  # noqa: E402, F401
from host_brain.app.dependencies import get_milestone_tracker  # noqa: E402, F401
