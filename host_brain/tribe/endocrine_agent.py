from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Mapping

from host_brain.core.endocrine import derive_endocrine_state
from host_brain.tribe.base_agent import TribeAgentDescriptor


@dataclass(slots=True)
class EndocrineAgent:
    descriptor: TribeAgentDescriptor = field(
        default_factory=lambda: TribeAgentDescriptor(
            agent_id="endocrine_agent",
            family="endocrine",
            capabilities=("hormone_baseline", "gate_state", "homeostasis"),
        )
    )
    _last_state: dict[str, Any] = field(default_factory=dict, init=False, repr=False)
    # T-28：bond 事件产生的激素增量缓冲（在下次 resolve_endocrine_state 时消费）
    _bond_cortisol_delta: float = field(default=0.0, init=False, repr=False)
    _bond_oxytocin_delta: float = field(default=0.0, init=False, repr=False)
    _bond_lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    def resolve_endocrine_state(
        self,
        *,
        hard_state: Mapping[str, Any] | Any,
        emotion_tag: str | None = None,
        emotion_intensity: float | None = None,
    ) -> dict[str, Any]:
        state = derive_endocrine_state(
            hard_state=hard_state,
            emotion_tag=emotion_tag,
            emotion_intensity=emotion_intensity,
        )
        payload = dict(state.as_dict())

        # ── T-17：接入 PainRewardAgent 压力信号 ──────────────────────────
        # 将痛觉/奖励系统的净压力变化量叠加到 cortisol_level，
        # 使痛觉事件（被拒绝/被攻击/话题受挫等）能够影响内分泌状态。
        # 叠加量上限 ±0.15，避免单次事件过度影响皮质醇。
        try:
            from host_brain.tribe.pain_reward_agent import get_pain_reward_agent  # noqa: PLC0415

            _pain_agent = get_pain_reward_agent()
            _stress_signal = _pain_agent.get_stress_signal()
            _net_stress_delta = float(_stress_signal.get("net_stress_delta") or 0.0)
            if abs(_net_stress_delta) > 0.001:
                _clamped_delta = max(-0.15, min(0.15, _net_stress_delta))
                _cur_cortisol = _safe_float(payload.get("cortisol_level"), 0.0)
                _new_cortisol = max(0.0, min(1.0, _cur_cortisol + _clamped_delta))
                payload["cortisol_level"] = round(_new_cortisol, 4)
                payload["_pain_stress_delta"] = round(_clamped_delta, 4)
        except Exception:
            pass

        # ── T-28：消费 bond 事件产生的激素增量缓冲 ───────────────────────
        # BOND_PROGRESS → 催产素增加（oxytocin_boost）+ 皮质醇轻微降低
        # BOND_REGRESS  → 皮质醇增加（cortisol_boost）+ 催产素轻微降低
        try:
            with self._bond_lock:
                _cortisol_delta = self._bond_cortisol_delta
                _oxytocin_delta = self._bond_oxytocin_delta
                # 消费后清零
                self._bond_cortisol_delta = 0.0
                self._bond_oxytocin_delta = 0.0

            if abs(_cortisol_delta) > 0.001:
                _cur_cortisol = _safe_float(payload.get("cortisol_level"), 0.0)
                _new_cortisol = max(0.0, min(1.0, _cur_cortisol + _cortisol_delta))
                payload["cortisol_level"] = round(_new_cortisol, 4)
                payload["_bond_cortisol_delta"] = round(_cortisol_delta, 4)

            if abs(_oxytocin_delta) > 0.001:
                # oxytocin_level 可能不在 payload 中，先读取再叠加
                _cur_oxytocin = _safe_float(payload.get("oxytocin_level"), 0.5)
                _new_oxytocin = max(0.0, min(1.0, _cur_oxytocin + _oxytocin_delta))
                payload["oxytocin_level"] = round(_new_oxytocin, 4)
                payload["_bond_oxytocin_delta"] = round(_oxytocin_delta, 4)
        except Exception:
            pass

        self._last_state = dict(payload)
        return payload

    def resolve_vital_signs(self, *, endocrine_state: Mapping[str, Any] | None = None) -> dict[str, Any]:
        state = dict(endocrine_state or self._last_state or {})
        cortisol = _safe_float(state.get("cortisol_level"), 0.0)
        energy = _safe_float(state.get("energy_reserve"), 0.0)
        gate_state = str(state.get("gate_state") or "open").strip().lower() or "open"
        return {
            "gate_state": gate_state,
            "stress_index": round(cortisol, 4),
            "energy_reserve": round(energy, 4),
            "homeostasis_ok": bool(cortisol < 0.62 and energy > 0.22 and gate_state == "open"),
        }

    def export_runtime_state(self) -> dict[str, Any]:
        last_state = dict(self._last_state or {})
        return {
            "state": last_state,
            "vitals": self.resolve_vital_signs(endocrine_state=last_state),
        }

    # ── T-28：TribeEventBus 订阅注册 ─────────────────────────────────────────

    def register_subscriptions(self) -> None:
        """向 TribeEventBus 注册事件订阅 handler。

        T-28：订阅 BOND_PROGRESS / BOND_REGRESS 事件，
        关系变化影响催产素（oxytocin）和皮质醇（cortisol）。

        订阅事件：
          - BOND_PROGRESS：关系进展 → 催产素增加 + 皮质醇轻微降低
          - BOND_REGRESS：关系退步 → 皮质醇增加 + 催产素轻微降低
        """
        try:
            from host_brain.app.internal_tribe_routes import get_tribe_event_bus  # noqa: PLC0415

            bus = get_tribe_event_bus()
            bus.subscribe("BOND_PROGRESS", self._on_bond_progress)
            bus.subscribe("BOND_REGRESS", self._on_bond_regress)
            # T-57：订阅孤独感信号，孤独感触发 cortisol 轻微上升
            bus.subscribe("LONELINESS_SIGNAL", self._on_loneliness_signal)
            # T-69：订阅任务成功事件，任务成功触发 cortisol 轻微下降
            bus.subscribe("TASK_SUCCESS", self._on_task_success_endocrine)
            # T-86：订阅免疫拦截事件，拦截时 cortisol 轻微上升
            bus.subscribe("IMMUNE_BLOCK", self._on_immune_block_endocrine)
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.endocrine_agent").warning(
                "EndocrineAgent 订阅注册失败（降级）: %s", exc
            )

    async def _on_bond_progress(self, event: Any) -> None:
        """处理 BOND_PROGRESS 事件：关系进展 → 催产素增加 + 皮质醇轻微降低。

        催产素增量：intensity * 0.15（上限 0.12）
        皮质醇减量：intensity * 0.05（上限 0.05）
        """
        try:
            import logging  # noqa: PLC0415
            payload = dict(getattr(event, "payload", None) or {})
            intensity = max(0.0, min(1.0, float(payload.get("intensity") or 0.3)))
            reason = str(payload.get("reason") or "bond_progress")
            # 催产素增加（正值）
            oxytocin_boost = round(min(0.12, intensity * 0.15), 4)
            # 皮质醇降低（负值）
            cortisol_relief = round(-min(0.05, intensity * 0.05), 4)
            with self._bond_lock:
                self._bond_oxytocin_delta += oxytocin_boost
                self._bond_cortisol_delta += cortisol_relief
            logging.getLogger("Natsume.tribe.endocrine_agent").debug(
                "EndocrineAgent BOND_PROGRESS: oxytocin+%.3f cortisol%.3f reason=%s",
                oxytocin_boost, cortisol_relief, reason,
            )
        except Exception:
            pass

    async def _on_bond_regress(self, event: Any) -> None:
        """处理 BOND_REGRESS 事件：关系退步 → 皮质醇增加 + 催产素轻微降低。

        皮质醇增量：intensity * 0.10（上限 0.08）
        催产素减量：intensity * 0.08（上限 0.06）
        """
        try:
            import logging  # noqa: PLC0415
            payload = dict(getattr(event, "payload", None) or {})
            intensity = max(0.0, min(1.0, float(payload.get("intensity") or 0.4)))
            reason = str(payload.get("reason") or "bond_regress")
            # 皮质醇增加（正值）
            cortisol_boost = round(min(0.08, intensity * 0.10), 4)
            # 催产素降低（负值）
            oxytocin_drop = round(-min(0.06, intensity * 0.08), 4)
            with self._bond_lock:
                self._bond_cortisol_delta += cortisol_boost
                self._bond_oxytocin_delta += oxytocin_drop
            logging.getLogger("Natsume.tribe.endocrine_agent").debug(
                "EndocrineAgent BOND_REGRESS: cortisol+%.3f oxytocin%.3f reason=%s",
                cortisol_boost, oxytocin_drop, reason,
            )
        except Exception:
            pass


    async def _on_loneliness_signal(self, event: Any) -> None:
        """处理 LONELINESS_SIGNAL 事件：孤独感触发 cortisol 轻微上升。

        T-57：孤独感 > 0.5 时将 cortisol 增量写入缓冲，
        在下次 resolve_endocrine_state() 时消费，模拟孤独感带来的轻微压力反应。
        cortisol 增量上限 0.06，避免单次事件过度影响内分泌状态。
        """
        try:
            import logging  # noqa: PLC0415
            payload = dict(getattr(event, "payload", None) or {})
            loneliness = max(0.0, min(1.0, float(payload.get("loneliness") or 0.0)))
            if loneliness <= 0.5:
                return
            cortisol_boost = round(min(0.06, (loneliness - 0.5) * 0.12), 4)
            with self._bond_lock:
                self._bond_cortisol_delta += cortisol_boost
            logging.getLogger("Natsume.tribe.endocrine_agent").debug(
                "EndocrineAgent LONELINESS_SIGNAL: cortisol+%.3f loneliness=%.2f",
                cortisol_boost, loneliness,
            )
        except Exception:
            pass

    async def _on_task_success_endocrine(self, event: Any) -> None:
        """处理 TASK_SUCCESS 事件：任务成功触发 cortisol 轻微下降。

        T-69：任务成功时将 cortisol 减量写入缓冲，
        在下次 resolve_endocrine_state() 时消费，模拟成就感带来的压力缓解。
        cortisol 减量上限 0.05，避免单次事件过度影响内分泌状态。
        """
        try:
            import logging  # noqa: PLC0415
            payload = dict(getattr(event, "payload", None) or {})
            intensity = max(0.0, min(1.0, float(payload.get("intensity") or 0.35)))
            cortisol_relief = round(-min(0.05, intensity * 0.07), 4)  # 负值表示减少
            with self._bond_lock:
                self._bond_cortisol_delta += cortisol_relief
            logging.getLogger("Natsume.tribe.endocrine_agent").debug(
                "EndocrineAgent TASK_SUCCESS: cortisol%.3f intensity=%.2f",
                cortisol_relief, intensity,
            )
        except Exception:
            pass

    async def _on_immune_block_endocrine(self, event: Any) -> None:
        """处理 IMMUNE_BLOCK 事件：免疫拦截时 cortisol 轻微上升。

        T-86：被拦截意味着输出受到抑制，模拟轻微的挫败感/压力反应。
        cortisol 增量上限 0.04，避免单次拦截过度影响内分泌状态。
        仅在 risk_level 为 medium/high/critical 时触发。
        """
        try:
            import logging  # noqa: PLC0415
            payload = dict(getattr(event, "payload", None) or {})
            risk_level = str(payload.get("risk_level") or "low").strip().lower()
            # low 级别不触发压力反应
            if risk_level == "low":
                return
            # medium → +0.02，high → +0.03，critical → +0.04
            risk_boost_map = {"medium": 0.02, "high": 0.03, "critical": 0.04}
            cortisol_boost = risk_boost_map.get(risk_level, 0.02)
            with self._bond_lock:
                self._bond_cortisol_delta += cortisol_boost
            logging.getLogger("Natsume.tribe.endocrine_agent").debug(
                "EndocrineAgent IMMUNE_BLOCK: cortisol+%.3f risk_level=%s",
                cortisol_boost, risk_level,
            )
        except Exception:
            pass


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


_ENDOCRINE_AGENT: EndocrineAgent | None = None


def get_endocrine_agent() -> EndocrineAgent:
    global _ENDOCRINE_AGENT
    if _ENDOCRINE_AGENT is None:
        _ENDOCRINE_AGENT = EndocrineAgent()
    return _ENDOCRINE_AGENT


def set_endocrine_agent(agent: EndocrineAgent | None) -> None:
    global _ENDOCRINE_AGENT
    _ENDOCRINE_AGENT = agent


__all__ = [
    "EndocrineAgent",
    "get_endocrine_agent",
    "set_endocrine_agent",
]
