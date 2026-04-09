from __future__ import annotations

import threading
from typing import Any

from host_brain.core.ids import now_ms


class TribeStateRegistry:
    """最小 Tribe 状态注册表：按命名空间保存状态快照。"""

    def __init__(self) -> None:
        self._states: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def set_state(self, namespace: str, state: dict[str, Any], *, writer: str | None = None) -> dict[str, Any]:
        ns = str(namespace).strip() or "default"
        with self._lock:
            snapshot = {
                "namespace": ns,
                "state": dict(state or {}),
                "writer": (str(writer).strip() or None) if writer is not None else None,
                "updated_at_ms": now_ms(),
            }
            self._states[ns] = snapshot
            return dict(snapshot)

    def get_state(self, namespace: str) -> dict[str, Any] | None:
        ns = str(namespace).strip() or "default"
        with self._lock:
            item = self._states.get(ns)
            return dict(item) if isinstance(item, dict) else None

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {ns: dict(data) for ns, data in self._states.items()}

    def aggregate_vital_signs(self) -> dict[str, Any]:
        """T-10：从各族 Agent 写入的状态快照中聚合统一生命体征。

        聚合来源：
        - endocrine_agent 写入的 gate_state / stress_index / energy_reserve / homeostasis_ok
        - limbic_agent 写入的 emotion_tag / mood_state / silence_willingness
        - social_brain_agent 写入的 bond_stage
        - limbic_agent 写入的 last_voice_emotion_inference（语音情绪推断）

        返回字段：
            gate_state (str): "open" | "throttle" | "pause"
            stress_index (float): 0.0 ~ 1.0，来自 cortisol_level
            energy_reserve (float): 0.0 ~ 1.0，来自 endocrine
            homeostasis_ok (bool): 内稳态是否正常
            emotion_tag (str): 当前情绪标签
            mood_state (str): 当前心情状态
            silence_willingness (float): 沉默意愿 0.0 ~ 1.0
            bond_stage (str): 当前 Bond 阶段
            voice_emotion_tag (str | None): 最近语音情绪推断结果
            voice_emotion_intensity (float | None): 最近语音情绪强度
            ts_ms (int): 聚合时间戳
        """
        with self._lock:
            states = {ns: dict(data) for ns, data in self._states.items()}

        # ── 内分泌 / 门控 ────────────────────────────────────────────────────
        gate_state = "open"
        stress_index = 0.0
        energy_reserve = 0.6
        homeostasis_ok = True
        endocrine_ns = states.get("endocrine_agent") or states.get("endocrine")
        if endocrine_ns:
            _es = dict(endocrine_ns.get("state") or {})
            gate_state = str(_es.get("gate_state") or "open").strip().lower() or "open"
            stress_index = _safe_float(_es.get("stress_index") or _es.get("cortisol_level"), 0.0)
            energy_reserve = _safe_float(_es.get("energy_reserve"), 0.6)
            homeostasis_ok = bool(_es.get("homeostasis_ok", True))

        # ── 情绪 / 心情 ──────────────────────────────────────────────────────
        emotion_tag = "NEUTRAL"
        mood_state = "steady"
        silence_willingness = 0.5
        limbic_ns = states.get("limbic_agent") or states.get("limbic")
        if limbic_ns:
            _ls = dict(limbic_ns.get("state") or {})
            emotion_tag = str(_ls.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
            mood_state = str(_ls.get("mood_state") or "steady").strip().lower() or "steady"
            silence_willingness = _safe_float(_ls.get("silence_willingness"), 0.5)

        # ── 语音情绪推断（T-09 写入）────────────────────────────────────────
        voice_emotion_tag: str | None = None
        voice_emotion_intensity: float | None = None
        voice_ns = states.get("last_voice_emotion_inference")
        if voice_ns:
            _vs = dict(voice_ns.get("state") or {})
            _vt = str(_vs.get("emotion_tag") or "").strip().upper()
            if _vt:
                voice_emotion_tag = _vt
            _vi = _vs.get("emotion_intensity")
            if _vi is not None:
                try:
                    voice_emotion_intensity = float(_vi)
                except (TypeError, ValueError):
                    pass

        # ── Bond 阶段 ────────────────────────────────────────────────────────
        bond_stage = "stranger"
        social_ns = states.get("social_brain_agent") or states.get("social_brain")
        if social_ns:
            _ss = dict(social_ns.get("state") or {})
            bond_stage = str(_ss.get("bond_stage") or "stranger").strip().lower() or "stranger"

        return {
            "gate_state": gate_state,
            "stress_index": round(stress_index, 4),
            "energy_reserve": round(energy_reserve, 4),
            "homeostasis_ok": homeostasis_ok,
            "emotion_tag": emotion_tag,
            "mood_state": mood_state,
            "silence_willingness": round(silence_willingness, 4),
            "bond_stage": bond_stage,
            "voice_emotion_tag": voice_emotion_tag,
            "voice_emotion_intensity": (
                round(voice_emotion_intensity, 4) if voice_emotion_intensity is not None else None
            ),
            "ts_ms": now_ms(),
        }


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


__all__ = ["TribeStateRegistry"]
