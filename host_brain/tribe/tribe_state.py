from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from host_brain.core.ids import now_ms

# T-29：持久化文件路径（可通过环境变量覆盖）
_DEFAULT_SNAPSHOT_PATH = Path(
    os.environ.get(
        "NATSUME_TRIBE_STATE_SNAPSHOT_PATH",
        "host_brain/data/tribe_state_snapshot.json",
    )
)
# T-29：节流：每 N 次 set_state 才写一次磁盘
_SNAPSHOT_THROTTLE = 10


class TribeStateRegistry:
    """最小 Tribe 状态注册表：按命名空间保存状态快照。

    T-29：支持跨会话持久化，重启后自动恢复所有 Agent 状态快照。
    """

    def __init__(
        self,
        *,
        state_path: Path | None = None,
        persist_throttle: int = _SNAPSHOT_THROTTLE,
    ) -> None:
        self._states: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        # T-29：持久化控制
        self._state_path: Path = state_path or _DEFAULT_SNAPSHOT_PATH
        self._persist_throttle: int = max(1, int(persist_throttle))
        self._persist_counter: int = 0

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
            self._persist_counter += 1
            should_persist = (self._persist_counter % self._persist_throttle == 0)
        # T-29：节流持久化（在锁外执行，避免阻塞）
        if should_persist:
            self.save_snapshot()
        return dict(snapshot)

    def get_state(self, namespace: str) -> dict[str, Any] | None:
        ns = str(namespace).strip() or "default"
        with self._lock:
            item = self._states.get(ns)
            return dict(item) if isinstance(item, dict) else None

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {ns: dict(data) for ns, data in self._states.items()}

    def get_all_namespaces(self) -> list[dict[str, Any]]:
        """T-39：返回所有命名空间的完整快照列表，供诊断端点使用。

        Returns:
            按 updated_at_ms 降序排列的命名空间快照列表，每项包含：
            - namespace (str): 命名空间名称
            - writer (str | None): 最后写入的 Agent
            - updated_at_ms (int): 最后更新时间戳
            - state (dict): 状态数据
        """
        with self._lock:
            items = list(self._states.values())
        return sorted(
            [dict(item) for item in items if isinstance(item, dict)],
            key=lambda x: int(x.get("updated_at_ms") or 0),
            reverse=True,
        )

    def get_state_by_family(self, family: str) -> list[dict[str, Any]]:
        """T-50：按 Agent 族名查询所有匹配命名空间的快照列表。

        命名空间命名约定为 "{family}_agent" 或 "{family}"，
        此方法同时匹配两种格式，方便按族批量查询。

        Args:
            family: Agent 族名，如 "limbic"、"endocrine"、"social_brain" 等。
                    大小写不敏感，自动转为小写匹配。

        Returns:
            按 updated_at_ms 降序排列的匹配命名空间快照列表。
            若无匹配则返回空列表。

        Examples:
            >>> registry.get_state_by_family("limbic")
            # 返回 namespace 为 "limbic_agent" 或 "limbic" 的快照
            >>> registry.get_state_by_family("social_brain")
            # 返回 namespace 为 "social_brain_agent" 或 "social_brain" 的快照
        """
        target = str(family or "").strip().lower()
        if not target:
            return []
        # 匹配规则：namespace == target 或 namespace == f"{target}_agent"
        # 或 namespace 以 f"{target}_" 开头（支持子族，如 "limbic_voice"）
        with self._lock:
            matched = [
                dict(item)
                for ns, item in self._states.items()
                if isinstance(item, dict) and (
                    ns == target
                    or ns == f"{target}_agent"
                    or ns.startswith(f"{target}_")
                )
            ]
        return sorted(
            matched,
            key=lambda x: int(x.get("updated_at_ms") or 0),
            reverse=True,
        )

    def clear_namespace(self, namespace: str) -> bool:
        """T-70：清除指定命名空间的状态快照，支持运维手动清零。

        Args:
            namespace: 要清除的命名空间名称，大小写敏感，与 set_state() 保持一致。

        Returns:
            True 表示成功清除（命名空间存在），False 表示命名空间不存在。
        """
        ns = str(namespace or "").strip() or "default"
        with self._lock:
            if ns not in self._states:
                return False
            del self._states[ns]
        import logging  # noqa: PLC0415
        logging.getLogger("Natsume.tribe.tribe_state").info(
            "TribeStateRegistry.clear_namespace: 已清除命名空间 %s", ns
        )
        return True

    # ── T-29：持久化方法 ─────────────────────────────────────────────────────

    def save_snapshot(self) -> bool:
        """将当前所有命名空间状态快照持久化到磁盘 JSON 文件。

        采用原子写入（.tmp → rename），避免写入中断导致文件损坏。

        Returns:
            True 表示保存成功，False 表示保存失败（降级，不抛异常）。
        """
        try:
            with self._lock:
                data = {ns: dict(snap) for ns, snap in self._states.items()}
            persist_data = {
                "version": 1,
                "saved_at_ms": now_ms(),
                "namespaces": data,
            }
            path = self._state_path
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_suffix(".tmp")
            tmp_path.write_text(
                json.dumps(persist_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp_path.replace(path)
            return True
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.tribe_state").debug(
                "TribeStateRegistry.save_snapshot 失败（降级）: %s", exc
            )
            return False

    def load_snapshot(self) -> bool:
        """从磁盘 JSON 文件加载状态快照，恢复跨会话 Agent 状态。

        Returns:
            True 表示加载成功，False 表示文件不存在或加载失败（降级，不抛异常）。
        """
        try:
            path = self._state_path
            if not path.exists():
                return False
            raw = path.read_text(encoding="utf-8")
            persist_data = json.loads(raw)
            namespaces = dict(persist_data.get("namespaces") or {})
            if not namespaces:
                return False
            with self._lock:
                for ns, snap in namespaces.items():
                    if isinstance(snap, dict):
                        self._states[str(ns)] = snap
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.tribe_state").info(
                "TribeStateRegistry 加载持久化快照: %d 个命名空间，saved_at_ms=%s",
                len(namespaces),
                persist_data.get("saved_at_ms"),
            )
            return True
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.tribe_state").debug(
                "TribeStateRegistry.load_snapshot 失败（降级）: %s", exc
            )
            return False

    # ── T-32：定时保存 ────────────────────────────────────────────────────────

    def start_periodic_save(self, interval_seconds: float = 300.0) -> None:
        """启动后台守护线程，每隔 interval_seconds 秒强制持久化一次快照。

        T-32：补充节流机制的盲区——当 set_state 调用频率较低时，
        节流计数可能长时间未达到阈值，导致最新状态未持久化。
        定时保存确保即使低频写入场景下，状态也能定期落盘。

        Args:
            interval_seconds: 保存间隔（秒），默认 300 秒（5 分钟）。
                              可通过 NATSUME_TRIBE_STATE_SAVE_INTERVAL 环境变量覆盖。
        """
        import logging  # noqa: PLC0415
        _log = logging.getLogger("Natsume.tribe.tribe_state")

        # 支持环境变量覆盖间隔
        env_interval = os.environ.get("NATSUME_TRIBE_STATE_SAVE_INTERVAL")
        if env_interval:
            try:
                interval_seconds = max(30.0, float(env_interval))
            except (TypeError, ValueError):
                pass

        interval_seconds = max(30.0, float(interval_seconds))

        with self._lock:
            # 如果已有定时线程在运行，先停止
            if getattr(self, "_periodic_stop_event", None) is not None:
                self._periodic_stop_event.set()  # type: ignore[attr-defined]

        stop_event = threading.Event()
        self._periodic_stop_event = stop_event  # type: ignore[attr-defined]

        def _periodic_worker() -> None:
            _log.info(
                "TribeStateRegistry 定时保存已启动，间隔=%.0f 秒",
                interval_seconds,
            )
            while not stop_event.wait(timeout=interval_seconds):
                ok = self.save_snapshot()
                _log.debug(
                    "TribeStateRegistry 定时保存: %s",
                    "成功" if ok else "失败（降级）",
                )
            _log.info("TribeStateRegistry 定时保存已停止")

        t = threading.Thread(
            target=_periodic_worker,
            name="TribeStateRegistry-PeriodicSave",
            daemon=True,
        )
        t.start()
        self._periodic_thread = t  # type: ignore[attr-defined]
        _log.debug("TribeStateRegistry 定时保存线程已启动: interval=%.0fs", interval_seconds)

    def stop_periodic_save(self) -> None:
        """停止后台定时保存线程。

        T-32：在 http_hooks.py lifespan shutdown 阶段调用，
        确保进程退出前完成最后一次持久化。
        """
        stop_event = getattr(self, "_periodic_stop_event", None)
        if stop_event is not None:
            stop_event.set()
        # 关闭前强制保存一次，确保最新状态落盘
        self.save_snapshot()

    def aggregate_vital_signs(self) -> dict[str, Any]:
        """T-10：从各族 Agent 写入的状态快照中聚合统一生命体征。

        聚合来源：
        - endocrine_agent 写入的 gate_state / stress_index / energy_reserve / homeostasis_ok
        - limbic_agent 写入的 emotion_tag / mood_state / silence_willingness
        - social_brain_agent 写入的 bond_stage
        - limbic_agent 写入的 last_voice_emotion_inference（语音情绪推断）
        - pain_reward_agent 写入的 hedonic_tone / pain_level / reward_level / net_valence（T-19）

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
            hedonic_tone (str): 享乐基调 "positive"|"neutral"|"negative"|"mixed"（T-19）
            pain_level (float): 当前痛觉强度 0.0 ~ 1.0（T-19）
            reward_level (float): 当前奖励强度 0.0 ~ 1.0（T-19）
            net_valence (float): 净享乐效价 -1.0 ~ 1.0（T-19）
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

        # ── T-19：享乐状态（PainRewardAgent）────────────────────────────────
        # 从 pain_reward_agent 命名空间读取 hedonic_state，
        # 将 hedonic_tone / pain_level / reward_level / net_valence 纳入聚合。
        hedonic_tone = "neutral"
        pain_level = 0.0
        reward_level = 0.0
        net_valence = 0.0
        pain_ns = states.get("pain_reward_agent")
        if pain_ns:
            _ps = dict(pain_ns.get("state") or {})
            hedonic_tone = str(_ps.get("hedonic_tone") or "neutral").strip().lower() or "neutral"
            pain_level = _safe_float(_ps.get("pain_level"), 0.0)
            reward_level = _safe_float(_ps.get("reward_level"), 0.0)
            net_valence = _safe_float(_ps.get("net_valence"), 0.0)

        # ── T-46：免疫状态（ImmuneAgent）────────────────────────────────────
        # 从 immune_agent 命名空间读取计数器快照，
        # 将 immune_block_count / immune_block_rate / immune_risk_level 纳入聚合。
        immune_block_count = 0
        immune_block_rate = 0.0
        immune_risk_level = "none"
        immune_ns = states.get("immune_agent")
        if immune_ns:
            _is = dict(immune_ns.get("state") or {})
            _counters = dict(_is.get("counters") or {})
            immune_block_count = int(_counters.get("block") or 0)
            immune_block_rate = _safe_float(_is.get("block_rate"), 0.0)
            immune_risk_level = str(_is.get("risk_level") or "none").strip().lower() or "none"

        # ── T-59：bond_trend_hint（SocialBrainAgent 快照）────────────────────
        # 从 social_brain_agent 命名空间读取 bond_trend_hint，
        # 供 chat_context_builder 在构建 prompt 时参考任务成功带来的关系倾向。
        bond_trend_hint = ""
        social_ns2 = states.get("social_brain_agent") or states.get("social_brain")
        if social_ns2:
            _ss2 = dict(social_ns2.get("state") or {})
            bond_trend_hint = str(_ss2.get("bond_trend_hint") or "").strip().lower()

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
            # T-19 享乐状态
            "hedonic_tone": hedonic_tone,
            "pain_level": round(pain_level, 4),
            "reward_level": round(reward_level, 4),
            "net_valence": round(net_valence, 4),
            # T-46 免疫状态
            "immune_block_count": immune_block_count,
            "immune_block_rate": round(immune_block_rate, 4),
            "immune_risk_level": immune_risk_level,
            # T-59 关系趋势提示
            "bond_trend_hint": bond_trend_hint,
            "ts_ms": now_ms(),
        }


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


# ── T-29：全局单例 TribeStateRegistry ────────────────────────────────────────
# 各族 Agent 共享同一个 registry 实例，确保状态快照跨 Agent 可见，
# 并且持久化时能一次性保存所有命名空间。
_GLOBAL_REGISTRY: TribeStateRegistry | None = None
_GLOBAL_REGISTRY_LOCK = threading.Lock()


def get_global_registry() -> TribeStateRegistry:
    """获取全局共享的 TribeStateRegistry 单例。

    T-29：所有族 Agent 应使用此函数获取 registry，
    确保状态快照跨 Agent 可见，并支持统一持久化。
    """
    global _GLOBAL_REGISTRY
    if _GLOBAL_REGISTRY is None:
        with _GLOBAL_REGISTRY_LOCK:
            if _GLOBAL_REGISTRY is None:
                _GLOBAL_REGISTRY = TribeStateRegistry()
    return _GLOBAL_REGISTRY


__all__ = ["TribeStateRegistry", "get_global_registry"]
