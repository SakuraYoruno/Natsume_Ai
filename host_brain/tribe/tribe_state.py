from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Mapping

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
_SNAPSHOT_INLINE_MIN_INTERVAL_S = 30.0


def _float_env(name: str, *, default: float, low: float, high: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


class TribeStateRegistry:
    """Process-local Tribe state registry keyed by namespace.

    JSON persistence is an explicit compatibility/diagnostic mode. Production
    uses the default transient registry so it cannot become a parallel durable
    Life-state authority.
    """

    def __init__(
        self,
        *,
        state_path: Path | None = None,
        persist_throttle: int = _SNAPSHOT_THROTTLE,
        persist_min_interval_s: float | None = None,
        persistence_enabled: bool = False,
    ) -> None:
        self._states: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        # T-29：持久化控制
        self._persistence_enabled = bool(persistence_enabled)
        self._state_path: Path = state_path or _DEFAULT_SNAPSHOT_PATH
        self._persist_throttle: int = max(1, int(persist_throttle))
        self._persist_counter: int = 0
        self._persist_min_interval_s: float = (
            max(0.0, float(persist_min_interval_s))
            if persist_min_interval_s is not None
            else _float_env(
                "NATSUME_TRIBE_STATE_INLINE_SAVE_MIN_INTERVAL_S",
                default=_SNAPSHOT_INLINE_MIN_INTERVAL_S,
                low=0.0,
                high=3600.0,
            )
        )
        self._state_revision: int = 0
        self._last_saved_revision: int = 0
        self._last_save_monotonic: float = 0.0

    def set_state(
        self,
        namespace: str,
        state: dict[str, Any],
        *,
        writer: str | None = None,
        schema_version: str = "tribe.state.v2",
        reason: str | None = None,
        input_refs: list[str] | None = None,
        decision_trace_id: str | None = None,
    ) -> dict[str, Any]:
        ns = str(namespace).strip() or "default"
        audit = {
            "reason": str(reason or "").strip() or None,
            "input_refs": [str(item).strip() for item in list(input_refs or []) if str(item).strip()],
            "decision_trace_id": str(decision_trace_id or "").strip() or None,
        }
        with self._lock:
            snapshot = {
                "namespace": ns,
                "state": dict(state or {}),
                "writer": (str(writer).strip() or None) if writer is not None else None,
                "schema_version": str(schema_version or "tribe.state.v2").strip() or "tribe.state.v2",
                "audit": audit,
                "updated_at_ms": now_ms(),
            }
            self._states[ns] = snapshot
            self._persist_counter += 1
            self._state_revision += 1
            current_revision = self._state_revision
            elapsed_s = time.monotonic() - self._last_save_monotonic
            should_persist = (
                self._persistence_enabled
                and self._persist_counter % self._persist_throttle == 0
                and current_revision > self._last_saved_revision
                and elapsed_s >= self._persist_min_interval_s
            )
        # T-29：节流持久化（在锁外执行，避免阻塞）
        if should_persist:
            self.save_snapshot()
        return dict(snapshot)

    def write(
        self,
        *,
        namespace: str,
        state: dict[str, Any],
        writer: str | None = None,
        merge: bool = True,
        schema_version: str = "tribe.state.v2",
        reason: str | None = None,
        input_refs: list[str] | None = None,
        decision_trace_id: str | None = None,
    ) -> dict[str, Any]:
        """兼容写入口：支持 merge 更新并携带审计信息。"""
        ns = str(namespace).strip() or "default"
        next_state = dict(state or {})
        if merge:
            prev = self.get_state(ns) or {}
            prev_state = dict(prev.get("state") or {})
            prev_state.update(next_state)
            next_state = prev_state
        return self.set_state(
            ns,
            next_state,
            writer=writer,
            schema_version=schema_version,
            reason=reason,
            input_refs=input_refs,
            decision_trace_id=decision_trace_id,
        )

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

    def get_state_summary(self) -> list[dict[str, Any]]:
        """T-87：返回所有命名空间的轻量摘要，供诊断端点快速概览。

        每个命名空间只返回 namespace / writer / updated_at_ms / state_keys，
        不包含完整 state 数据，避免响应体过大。

        Returns:
            list[dict]: 按 updated_at_ms 降序排列的摘要列表，每项包含：
            - namespace (str): 命名空间名称
            - writer (str | None): 最后写入的 Agent
            - updated_at_ms (int): 最后更新时间戳
            - state_keys (list[str]): state 字典的 key 列表（不含值）
        """
        with self._lock:
            items = list(self._states.values())
        summaries = []
        for item in items:
            if not isinstance(item, dict):
                continue
            state = item.get("state") or {}
            summaries.append({
                "namespace": item.get("namespace", ""),
                "writer": item.get("writer"),
                "updated_at_ms": item.get("updated_at_ms", 0),
                "state_keys": sorted(state.keys()) if isinstance(state, dict) else [],
            })
        return sorted(summaries, key=lambda x: int(x.get("updated_at_ms") or 0), reverse=True)

    def list_namespaces(self) -> list[str]:
        """T-77：返回所有已注册命名空间的名称列表，按字母顺序排列。

        供诊断端点和调试工具快速枚举当前活跃的 Agent 命名空间，
        不包含状态数据，仅返回名称列表。

        Returns:
            list[str]: 命名空间名称列表，按字母顺序排列。
        """
        with self._lock:
            return sorted(self._states.keys())

    # ── T-29：持久化方法 ─────────────────────────────────────────────────────

    def save_snapshot(self) -> bool:
        """将当前所有命名空间状态快照持久化到磁盘 JSON 文件。

        采用原子写入（.tmp → rename），避免写入中断导致文件损坏。

        Returns:
            True 表示保存成功，False 表示保存失败（降级，不抛异常）。
        """
        if not self._persistence_enabled:
            return False
        try:
            with self._lock:
                data = {ns: dict(snap) for ns, snap in self._states.items()}
                saved_revision = self._state_revision
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
            with self._lock:
                if self._last_saved_revision < saved_revision:
                    self._last_saved_revision = saved_revision
                self._last_save_monotonic = time.monotonic()
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
        if not self._persistence_enabled:
            return False
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

    def start_periodic_save(
        self,
        interval_seconds: float = 300.0,
        *,
        enabled: bool = False,
    ) -> None:
        """Explicitly opt into the retired standalone snapshot-save thread.

        Production startup never enables this compatibility helper. Durable
        periodic production is owned by SchedulerClock; explicitly enabled
        compatibility instances retain throttled JSON persistence.

        Args:
            interval_seconds: Explicit compatibility interval in seconds.
            enabled: Must be true for an isolated compatibility caller to
                     create the thread. The default is deliberately inert.
        """
        import logging  # noqa: PLC0415
        _log = logging.getLogger("Natsume.tribe.tribe_state")

        if not enabled or not self._persistence_enabled:
            _log.debug(
                "TribeStateRegistry standalone periodic save is disabled; "
                "SchedulerClock owns periodic production"
            )
            return

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
        """Stop an explicitly enabled compatibility save thread.

        Production shutdown does not call this method because production never
        starts the thread.
        """
        stop_event = getattr(self, "_periodic_stop_event", None)
        if stop_event is not None:
            stop_event.set()
        # 关闭前强制保存一次，确保最新状态落盘
        self.save_snapshot()

    def export_snapshot(self, export_path: "Path | str | None" = None) -> str:
        """T-111：将当前所有命名空间状态快照导出为 JSON 文件。

        与 save_snapshot() 的区别：
        - save_snapshot() 用于内部节流持久化，路径固定（_state_path）
        - export_snapshot() 用于运维/诊断导出，支持自定义路径，
          并在返回值中包含导出文件的绝对路径，方便调用方确认写入位置。

        Args:
            export_path: 导出目标路径（文件路径）。
                         若为 None，则在 _state_path 同目录下生成带时间戳的文件，
                         格式为 tribe_state_export_{ts_ms}.json。

        Returns:
            str: 导出文件的绝对路径字符串。

        Raises:
            OSError: 目录创建或文件写入失败时抛出（不降级，让调用方感知）。
        """
        ts_ms = now_ms()
        if export_path is None:
            _base_dir = self._state_path.parent
            _filename = f"tribe_state_export_{ts_ms}.json"
            resolved_path = _base_dir / _filename
        else:
            resolved_path = Path(export_path)

        with self._lock:
            data = {ns: dict(snap) for ns, snap in self._states.items()}

        export_data = {
            "version": 1,
            "export_type": "full_snapshot",
            "exported_at_ms": ts_ms,
            "namespace_count": len(data),
            "namespaces": data,
        }
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = resolved_path.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(export_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(resolved_path)
        import logging  # noqa: PLC0415
        logging.getLogger("Natsume.tribe.tribe_state").info(
            "TribeStateRegistry.export_snapshot: 已导出 %d 个命名空间到 %s",
            len(data),
            resolved_path,
        )
        return str(resolved_path.resolve())

    def aggregate_vital_signs(self) -> dict[str, Any]:
        """T-10：从各族 Agent 写入的状态快照中聚合统一生命体征。

        聚合来源：
        - endocrine_agent 写入的 gate_state / stress_index / energy_reserve / homeostasis_ok
        - limbic_agent 写入的 emotion_tag / mood_state / silence_willingness
        - social_brain_agent 写入的 bond_stage
        - limbic_agent 写入的 last_voice_emotion_inference（运行期情绪提示）
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
            voice_emotion_tag (str | None): 最近可信语音/传感器情绪结果
            voice_emotion_intensity (float | None): 最近可信语音/传感器情绪强度
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
        limbic_state: dict[str, Any] = {}
        limbic_ns = states.get("limbic_agent") or states.get("limbic")
        if limbic_ns:
            limbic_state = dict(limbic_ns.get("state") or {})
            emotion_tag = str(limbic_state.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
            mood_state = str(limbic_state.get("mood_state") or "steady").strip().lower() or "steady"
            silence_willingness = _safe_float(limbic_state.get("silence_willingness"), 0.5)

        # ── 运行期情绪提示（T-09 写入）──────────────────────────────────────
        voice_emotion_tag: str | None = None
        voice_emotion_intensity: float | None = None
        voice_emotion_source: str | None = None
        voice_emotion_modality: str | None = None
        voice_emotion_trusted = False
        voice_ns = states.get("last_voice_emotion_inference")
        voice_state = dict(voice_ns.get("state") or {}) if voice_ns else {}
        if not voice_state and limbic_ns:
            voice_state = dict(limbic_state.get("last_voice_emotion_inference") or {})
        if voice_state:
            _vs = dict(voice_state)
            voice_emotion_source = str(_vs.get("emotion_source") or "").strip() or None
            voice_emotion_modality = str(_vs.get("emotion_modality") or "").strip() or None
            voice_emotion_trusted = _runtime_emotion_state_is_trusted(_vs)
            _vt = str(_vs.get("emotion_tag") or "").strip().upper()
            if voice_emotion_trusted and _vt:
                voice_emotion_tag = _vt
            _vi = _vs.get("emotion_intensity")
            if voice_emotion_trusted and _vi is not None:
                try:
                    voice_emotion_intensity = float(_vi)
                except (TypeError, ValueError):
                    pass

        # ── Bond 阶段 ────────────────────────────────────────────────────────
        bond_stage = "friend"
        bond_vector: dict[str, Any] = {}
        social_ns = states.get("social_brain_agent") or states.get("social_brain")
        if social_ns:
            _ss = dict(social_ns.get("state") or {})
            bond_stage = str(_ss.get("bond_stage") or "friend").strip().lower() or "friend"
            raw_bond_vector = _ss.get("bond_vector")
            if isinstance(raw_bond_vector, dict):
                bond_vector = {
                    "trust": _safe_float(raw_bond_vector.get("trust"), 0.5),
                    "affinity": _safe_float(raw_bond_vector.get("affinity"), 0.5),
                    "safety": _safe_float(raw_bond_vector.get("safety"), 0.5),
                    "novelty": _safe_float(raw_bond_vector.get("novelty"), 0.4),
                    "dependency": _safe_float(raw_bond_vector.get("dependency"), 0.3),
                    "repair_confidence": _safe_float(raw_bond_vector.get("repair_confidence"), 0.5),
                }

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
        # 供上下文装配主链在构建 prompt 时参考任务成功带来的关系倾向。
        bond_trend_hint = ""
        social_ns2 = states.get("social_brain_agent") or states.get("social_brain")
        if social_ns2:
            _ss2 = dict(social_ns2.get("state") or {})
            bond_trend_hint = str(_ss2.get("bond_trend_hint") or "").strip().lower()

        # ── T-80：loneliness_level（PainRewardAgent 快照）────────────────────
        # 从 pain_reward_agent 命名空间读取最近一次孤独感强度，
        # 供上下文装配主链在高孤独感时触发主动关怀策略。
        loneliness_level = 0.0
        pain_ns = states.get("pain_reward_agent") or states.get("pain_reward")
        if pain_ns:
            _ps = dict(pain_ns.get("state") or {})
            loneliness_level = _safe_float(_ps.get("last_loneliness_level"), 0.0)

        # ── T-90：high_loneliness_flag ────────────────────────────────────────
        # loneliness_level > 0.7 时标记为高孤独感，
        # 供上下文装配主链快速判断是否需要触发主动关怀策略。
        high_loneliness_flag = loneliness_level > 0.7

        # ── Circadian / Proprioception（系统层）──────────────────────────────
        circadian_phase = "day"
        circadian_proactive_multiplier = 1.0
        sleep_pressure = 0.2
        circadian_ns = states.get("circadian_agent") or states.get("circadian")
        if circadian_ns:
            _cs = dict(circadian_ns.get("state") or {})
            circadian_phase = str(_cs.get("phase") or "day").strip().lower() or "day"
            circadian_proactive_multiplier = _safe_float(_cs.get("proactive_multiplier"), 1.0)
            sleep_pressure = _safe_float(_cs.get("sleep_pressure"), 0.2)

        cognitive_load_index = 0.2
        readiness_hint = 0.8
        continuity_hint = 0.2
        proprio_ns = states.get("proprioception_agent") or states.get("proprioception")
        if proprio_ns:
            _psp = dict(proprio_ns.get("state") or {})
            cognitive_load_index = _safe_float(_psp.get("cognitive_load_index"), 0.2)
            readiness_hint = _safe_float(_psp.get("readiness_hint"), 0.8)
            continuity_hint = _safe_float(_psp.get("continuity_hint"), 0.2)

        interoceptive_index = 0.0
        intero_ns = states.get("interoception_agent") or states.get("interoception")
        if intero_ns:
            _ins = dict(intero_ns.get("state") or {})
            interoceptive_index = _safe_float(_ins.get("interoceptive_index"), 0.0)

        # ── T-200：Motor 执行健康度（MotorCortexAgent 快照）──────────────────
        # 从 motor_cortex_agent 命名空间读取最近一次 dispatch 结果，
        # 将 motor_execution_ok / motor_last_latency_ms 纳入聚合。
        motor_execution_ok = True
        motor_last_latency_ms = 0
        motor_ns = states.get("motor_cortex_agent")
        if motor_ns:
            _ms = dict(motor_ns.get("state") or {})
            _last_dispatch = dict(_ms.get("last_dispatch_outcome") or {})
            # 优先读取 edge_ok（T-199 回流结果），其次读取 ok（Host 侧推送结果）
            if "edge_ok" in _last_dispatch:
                motor_execution_ok = bool(_last_dispatch.get("edge_ok", True))
                motor_last_latency_ms = int(_last_dispatch.get("edge_latency_ms") or 0)
            else:
                motor_execution_ok = bool(_last_dispatch.get("ok", True))
                motor_last_latency_ms = int(_last_dispatch.get("latency_ms") or 0)

        # ── T-207：Cerebellum 纠偏频率（CerebellumAgent 快照）───────────────
        # 从 cerebellum_agent 命名空间读取纠偏计数和最近策略，
        # 将 cerebellum_correction_count / cerebellum_last_strategy 纳入聚合。
        cerebellum_correction_count = 0
        cerebellum_last_strategy = "NO_ADJUST"
        cerebellum_ns = states.get("cerebellum_agent")
        if cerebellum_ns:
            _cbs = dict(cerebellum_ns.get("state") or {})
            cerebellum_correction_count = int(_cbs.get("correction_count") or 0)
            _last_correction = dict(_cbs.get("last_correction") or {})
            cerebellum_last_strategy = str(
                _last_correction.get("strategy") or _cbs.get("last_strategy") or "NO_ADJUST"
            ).strip().upper() or "NO_ADJUST"

        # ── T-221：PainReward 享乐状态（PainRewardAgent 快照）──────────────────
        pain_level = 0.0
        reward_level = 0.0
        net_valence = 0.0
        pain_ns = states.get("pain_reward_agent")
        if pain_ns:
            _ps = dict(pain_ns.get("state") or {})
            pain_level = _safe_float(_ps.get("pain_level"), 0.0)
            reward_level = _safe_float(_ps.get("reward_level"), 0.0)
            net_valence = _safe_float(_ps.get("net_valence"), 0.0)

        # ── T-222：MirrorNeuron 共情强度（MirrorNeuronAgent 快照）─────────────
        mirror_resonance_strength = 0.0
        mirror_ns = states.get("mirror_neuron_agent")
        if mirror_ns:
            _mn = dict(mirror_ns.get("state") or {})
            _last_resonance = dict(_mn.get("last_resonance") or {})
            mirror_resonance_strength = _safe_float(
                _last_resonance.get("resonance_strength") or _mn.get("last_resonance_strength"), 0.0
            )

        # ── T-232：SomaticAgent 躯体感知（somatic_comfort_needed / fatigue_level）──
        somatic_comfort_needed = False
        somatic_fatigue_level = 0.0
        somatic_ns = states.get("somatic_agent")
        if somatic_ns:
            _som = dict(somatic_ns.get("state") or {})
            somatic_comfort_needed = bool(_som.get("somatic_comfort_needed", False))
            somatic_fatigue_level = _safe_float(_som.get("fatigue_level"), 0.0)

        # ── T-233：LanguageSystemAgent 双系统快照（urgency_score / response_mode）──
        language_urgency_score = 0.0
        language_response_mode = "normal"
        language_ns = states.get("language_system_agent")
        if language_ns:
            _lang = dict(language_ns.get("state") or {})
            _last_wernicke = dict(_lang.get("last_wernicke") or {})
            _last_broca = dict(_lang.get("last_broca") or {})
            language_urgency_score = _safe_float(_last_wernicke.get("urgency_score"), 0.0)
            language_response_mode = str(_last_broca.get("response_mode") or "normal").strip().lower() or "normal"

        # ── T-234：InteroceptionAgent 完整快照（body_budget / recovery_urgency）──
        body_budget = 0.5
        recovery_urgency = 0.0
        intero_ns2 = states.get("interoception_agent")
        if intero_ns2:
            _ins2 = dict(intero_ns2.get("state") or {})
            body_budget = _safe_float(_ins2.get("body_budget"), 0.5)
            recovery_urgency = _safe_float(_ins2.get("recovery_urgency"), 0.0)

        # ── T-242：ImmuneAgent 风险快照（immune_risk_level / block_rate）──────
        immune_risk_level = "none"
        immune_block_rate = 0.0
        immune_ns = states.get("immune_agent")
        if immune_ns:
            _imm = dict(immune_ns.get("state") or {})
            immune_risk_level = str(_imm.get("risk_level") or "none").strip().lower() or "none"
            immune_block_rate = _safe_float(_imm.get("block_rate"), 0.0)

        # ── T-243：VestibularAgent 路由偏置快照（damping_factor / route_bias）──
        vestibular_damping_factor = 0.0
        vestibular_route_bias = "normal"
        vestibular_ns = states.get("vestibular_agent")
        if vestibular_ns:
            _vest = dict(vestibular_ns.get("state") or {})
            vestibular_damping_factor = _safe_float(_vest.get("damping_factor"), 0.0)
            vestibular_route_bias = str(_vest.get("route_bias") or "normal").strip().lower() or "normal"

        # ── T-257：CircadianAgent 日节律快照（phase / proactive_multiplier / recovery_bias）──
        circadian_phase = "day"
        circadian_proactive_multiplier = 1.0
        circadian_recovery_bias = 0.3
        circadian_sleep_pressure = 0.2
        circadian_ns = states.get("circadian_agent")
        if circadian_ns:
            _circ = dict(circadian_ns.get("state") or {})
            circadian_phase = str(_circ.get("phase") or "day").strip().lower() or "day"
            circadian_proactive_multiplier = _safe_float(_circ.get("proactive_multiplier"), 1.0)
            circadian_recovery_bias = _safe_float(_circ.get("recovery_bias"), 0.3)
            circadian_sleep_pressure = _safe_float(_circ.get("sleep_pressure"), 0.2)

        # ── T-272：InitiativeGovernorAgent 门控快照（block_rate / defer_count）──
        initiative_block_count = 0
        initiative_defer_count = 0
        initiative_approve_count = 0
        initiative_ns = states.get("initiative_governor_agent")
        if initiative_ns:
            _ig = dict(initiative_ns.get("state") or {})
            initiative_block_count = int(_ig.get("block_count") or 0)
            initiative_defer_count = int(_ig.get("defer_count") or 0)
            initiative_approve_count = int(_ig.get("approve_count") or 0)
        _ig_total = max(1, initiative_block_count + initiative_defer_count + initiative_approve_count)
        initiative_block_rate = round(initiative_block_count / _ig_total, 4)

        # ── T-278：PrefrontalAgent 漂移快照（drift_alert_level）──────────────
        drift_alert_level = "NONE"
        prefrontal_ns = states.get("prefrontal_agent")
        if prefrontal_ns:
            _pf = dict(prefrontal_ns.get("state") or {})
            _last_drift = dict(_pf.get("last_drift_result") or {})
            drift_alert_level = str(_last_drift.get("alert_level") or "NONE").strip().upper() or "NONE"

        # ── T-278：SocialBrainAgent 关系修复快照（bond_repair_needed）─────────
        bond_repair_needed = False
        social_brain_ns = states.get("social_brain_agent")
        if social_brain_ns:
            _sb = dict(social_brain_ns.get("state") or {})
            _repair = dict(_sb.get("repair_state") or {})
            bond_repair_needed = bool(_repair.get("repair_needed", False))

        # ── T-283：ANSAgent 节律快照（recovery_mode / proactive_multiplier）──
        ans_recovery_mode = False
        ans_proactive_multiplier = 0.70
        ans_ns = states.get("ans_agent")
        if ans_ns:
            _ans = dict(ans_ns.get("state") or {})
            ans_recovery_mode = bool(_ans.get("recovery_mode", False))
            ans_proactive_multiplier = _safe_float(_ans.get("proactive_multiplier"), 0.70)

        result = {
            "gate_state": gate_state,
            "stress_index": round(stress_index, 4),
            "energy_reserve": round(energy_reserve, 4),
            "homeostasis_ok": homeostasis_ok,
            "emotion_tag": emotion_tag,
            "mood_state": mood_state,
            "silence_willingness": round(silence_willingness, 4),
            "bond_stage": bond_stage,
            "bond_vector": bond_vector,
            "voice_emotion_tag": voice_emotion_tag,
            "voice_emotion_intensity": (
                round(voice_emotion_intensity, 4) if voice_emotion_intensity is not None else None
            ),
            "voice_emotion_source": voice_emotion_source,
            "voice_emotion_modality": voice_emotion_modality,
            "voice_emotion_trusted": voice_emotion_trusted,
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
            # T-80 孤独感强度
            "loneliness_level": round(loneliness_level, 4),
            # T-90 高孤独感标记
            "high_loneliness_flag": high_loneliness_flag,
            # 系统层节律与本体感知
            "circadian_phase": circadian_phase,
            "circadian_proactive_multiplier": round(circadian_proactive_multiplier, 4),
            "sleep_pressure": round(sleep_pressure, 4),
            "cognitive_load_index": round(cognitive_load_index, 4),
            "readiness_hint": round(readiness_hint, 4),
            "continuity_hint": round(continuity_hint, 4),
            "interoceptive_index": round(interoceptive_index, 4),
            # T-200 Motor 执行健康度
            "motor_execution_ok": motor_execution_ok,
            "motor_last_latency_ms": motor_last_latency_ms,
            # T-207 Cerebellum 纠偏频率
            "cerebellum_correction_count": cerebellum_correction_count,
            "cerebellum_last_strategy": cerebellum_last_strategy,
            # T-267 Cerebellum 纠偏率（correction_count / max(1, total_interactions)）
            "cerebellum_correction_rate": round(
                min(1.0, cerebellum_correction_count / max(1, int(
                    (states.get("cortex_agent") or {}).get("state", {}).get("interaction_count") or
                    cerebellum_correction_count or 1
                ))), 4
            ),
            # T-221 PainReward 享乐状态
            "pain_level": round(pain_level, 4),
            "reward_level": round(reward_level, 4),
            "net_valence": round(net_valence, 4),
            # T-222 MirrorNeuron 共情强度
            "mirror_resonance_strength": round(mirror_resonance_strength, 4),
            # T-232 SomaticAgent 躯体感知
            "somatic_comfort_needed": somatic_comfort_needed,
            "somatic_fatigue_level": round(somatic_fatigue_level, 4),
            # T-233 LanguageSystemAgent 双系统快照
            "language_urgency_score": round(language_urgency_score, 4),
            "language_response_mode": language_response_mode,
            # T-234 InteroceptionAgent 完整快照
            "body_budget": round(body_budget, 4),
            "recovery_urgency": round(recovery_urgency, 4),
            # T-242 ImmuneAgent 风险快照
            "immune_risk_level": immune_risk_level,
            "immune_block_rate": round(immune_block_rate, 4),
            # T-243 VestibularAgent 路由偏置快照
            "vestibular_damping_factor": round(vestibular_damping_factor, 4),
            "vestibular_route_bias": vestibular_route_bias,
            # T-257 CircadianAgent 日节律快照
            "circadian_phase": circadian_phase,
            "circadian_proactive_multiplier": round(circadian_proactive_multiplier, 4),
            "circadian_recovery_bias": round(circadian_recovery_bias, 4),
            "circadian_sleep_pressure": round(circadian_sleep_pressure, 4),
            # T-272 InitiativeGovernorAgent 门控快照
            "initiative_block_rate": initiative_block_rate,
            "initiative_defer_count": initiative_defer_count,
            "initiative_approve_count": initiative_approve_count,
            # T-278 PrefrontalAgent 漂移快照
            "drift_alert_level": drift_alert_level,
            # T-278 SocialBrainAgent 关系修复快照
            "bond_repair_needed": bond_repair_needed,
            # T-283 ANSAgent 节律快照
            "ans_recovery_mode": ans_recovery_mode,
            "ans_proactive_multiplier": round(ans_proactive_multiplier, 4),
            "ts_ms": now_ms(),
        }
        self._record_homeostasis_sample(vital_signs=result)
        result["homeostasis_report"] = self.get_homeostasis_report(now_ts_ms=int(result["ts_ms"]))
        return result

    def _record_homeostasis_sample(self, *, vital_signs: Mapping[str, Any]) -> None:
        ts_ms = int(vital_signs.get("ts_ms") or now_ms())
        minute_sample = {
            "ts_ms": ts_ms,
            "stress_index": _safe_float(vital_signs.get("stress_index"), 0.0),
            "energy_reserve": _safe_float(vital_signs.get("energy_reserve"), 0.6),
            "cognitive_load_index": _safe_float(vital_signs.get("cognitive_load_index"), 0.2),
            "loneliness_level": _safe_float(vital_signs.get("loneliness_level"), 0.0),
            "homeostasis_ok": bool(vital_signs.get("homeostasis_ok", True)),
        }
        hour_key = ts_ms // (3600 * 1000)
        day_key = ts_ms // (24 * 3600 * 1000)
        with self._lock:
            row = dict(self._states.get("homeostasis_history") or {})
            state = dict(row.get("state") or {})
            minute_samples = [dict(item) for item in list(state.get("minute_samples") or []) if isinstance(item, dict)]
            minute_samples.append(minute_sample)
            minute_samples = minute_samples[-180:]  # 最近 3 小时（分钟级）

            hourly_samples = [dict(item) for item in list(state.get("hourly_samples") or []) if isinstance(item, dict)]
            _upsert_bucket_sample(
                samples=hourly_samples,
                key_name="hour_key",
                key_value=hour_key,
                sample=minute_sample,
                max_items=168,  # 最近 7 天（小时级）
            )

            daily_samples = [dict(item) for item in list(state.get("daily_samples") or []) if isinstance(item, dict)]
            _upsert_bucket_sample(
                samples=daily_samples,
                key_name="day_key",
                key_value=day_key,
                sample=minute_sample,
                max_items=56,  # 最近 8 周（日级）
            )

            history_state = {
                "minute_samples": minute_samples,
                "hourly_samples": hourly_samples,
                "daily_samples": daily_samples,
                "last_sample_ts_ms": ts_ms,
            }
            self._states["homeostasis_history"] = {
                "namespace": "homeostasis_history",
                "state": history_state,
                "writer": "tribe_state_registry",
                "schema_version": "tribe.state.v2",
                "audit": {"reason": "aggregate_vital_signs", "input_refs": [], "decision_trace_id": None},
                "updated_at_ms": ts_ms,
            }

    def get_homeostasis_report(self, *, now_ts_ms: int | None = None) -> dict[str, Any]:
        ts_ms = int(now_ts_ms or now_ms())
        with self._lock:
            row = dict(self._states.get("homeostasis_history") or {})
            state = dict(row.get("state") or {})
            minute_samples = [dict(item) for item in list(state.get("minute_samples") or []) if isinstance(item, dict)]
            hourly_samples = [dict(item) for item in list(state.get("hourly_samples") or []) if isinstance(item, dict)]
            daily_samples = [dict(item) for item in list(state.get("daily_samples") or []) if isinstance(item, dict)]

        minute_recent = [item for item in minute_samples if ts_ms - int(item.get("ts_ms") or 0) <= 30 * 60 * 1000]
        hourly_recent = [item for item in hourly_samples if ts_ms - int(item.get("ts_ms") or 0) <= 24 * 3600 * 1000]
        daily_recent = [item for item in daily_samples if ts_ms - int(item.get("ts_ms") or 0) <= 14 * 24 * 3600 * 1000]

        minute_stress = _avg_field(minute_recent, "stress_index")
        minute_load = _avg_field(minute_recent, "cognitive_load_index")
        hourly_stress = _avg_field(hourly_recent, "stress_index")
        hourly_energy = _avg_field(hourly_recent, "energy_reserve")
        daily_ok_ratio = _avg_bool_field(daily_recent, "homeostasis_ok")
        daily_loneliness = _avg_field(daily_recent, "loneliness_level")

        chronic_overload = hourly_stress >= 0.62 and minute_load >= 0.55
        chronic_low_energy = hourly_energy <= 0.42 and minute_stress >= 0.45
        social_withdraw_risk = daily_loneliness >= 0.58 and daily_ok_ratio < 0.85
        status = "stable"
        if chronic_overload or chronic_low_energy:
            status = "warning"
        if (chronic_overload and chronic_low_energy) or daily_ok_ratio < 0.55:
            status = "critical"
        return {
            "status": status,
            "minute_window": {
                "window_minutes": 30,
                "samples": len(minute_recent),
                "avg_stress_index": round(minute_stress, 4),
                "avg_cognitive_load_index": round(minute_load, 4),
            },
            "hour_window": {
                "window_hours": 24,
                "samples": len(hourly_recent),
                "avg_stress_index": round(hourly_stress, 4),
                "avg_energy_reserve": round(hourly_energy, 4),
            },
            "day_window": {
                "window_days": 14,
                "samples": len(daily_recent),
                "homeostasis_ok_ratio": round(daily_ok_ratio, 4),
                "avg_loneliness_level": round(daily_loneliness, 4),
            },
            "alerts": {
                "chronic_overload": chronic_overload,
                "chronic_low_energy": chronic_low_energy,
                "social_withdraw_risk": social_withdraw_risk,
            },
            "ts_ms": ts_ms,
        }

    def export_homeostasis_history(self) -> dict[str, Any]:
        with self._lock:
            row = dict(self._states.get("homeostasis_history") or {})
            state = dict(row.get("state") or {})
        return {
            "minute_samples": [dict(item) for item in list(state.get("minute_samples") or []) if isinstance(item, dict)],
            "hourly_samples": [dict(item) for item in list(state.get("hourly_samples") or []) if isinstance(item, dict)],
            "daily_samples": [dict(item) for item in list(state.get("daily_samples") or []) if isinstance(item, dict)],
            "last_sample_ts_ms": int(state.get("last_sample_ts_ms") or 0),
        }


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


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


def _runtime_emotion_state_is_trusted(state: Mapping[str, Any]) -> bool:
    if bool(state.get("emotion_inference_degraded", False)):
        return False
    if "trusted_runtime_emotion" in state:
        return bool(state.get("trusted_runtime_emotion"))
    source = str(state.get("emotion_source") or "").strip().lower()
    modality = str(state.get("emotion_modality") or "").strip().lower()
    if source in _TEXT_ONLY_EMOTION_SOURCES or modality in {"asr_text", "text"}:
        return False
    if bool(state.get("acoustic_emotion_available", False)):
        return True
    return source in _TRUSTED_RUNTIME_EMOTION_SOURCES or modality in {"face_capture", "explicit"}


def _avg_field(rows: list[dict[str, Any]], key: str) -> float:
    if not rows:
        return 0.0
    total = 0.0
    count = 0
    for row in rows:
        try:
            total += float(row.get(key) or 0.0)
            count += 1
        except (TypeError, ValueError):
            continue
    return (total / count) if count > 0 else 0.0


def _avg_bool_field(rows: list[dict[str, Any]], key: str) -> float:
    if not rows:
        return 1.0
    total = 0.0
    for row in rows:
        total += 1.0 if bool(row.get(key, False)) else 0.0
    return total / len(rows)


def _upsert_bucket_sample(
    *,
    samples: list[dict[str, Any]],
    key_name: str,
    key_value: int,
    sample: Mapping[str, Any],
    max_items: int,
) -> None:
    for idx in range(len(samples) - 1, -1, -1):
        if int(samples[idx].get(key_name) or -1) != int(key_value):
            continue
        prev_count = max(1, int(samples[idx].get("count") or 1))
        merged_count = prev_count + 1
        samples[idx] = {
            key_name: int(key_value),
            "ts_ms": int(sample.get("ts_ms") or 0),
            "stress_index": round((float(samples[idx].get("stress_index") or 0.0) * prev_count + float(sample.get("stress_index") or 0.0)) / merged_count, 6),
            "energy_reserve": round((float(samples[idx].get("energy_reserve") or 0.0) * prev_count + float(sample.get("energy_reserve") or 0.0)) / merged_count, 6),
            "cognitive_load_index": round((float(samples[idx].get("cognitive_load_index") or 0.0) * prev_count + float(sample.get("cognitive_load_index") or 0.0)) / merged_count, 6),
            "loneliness_level": round((float(samples[idx].get("loneliness_level") or 0.0) * prev_count + float(sample.get("loneliness_level") or 0.0)) / merged_count, 6),
            "homeostasis_ok": bool(sample.get("homeostasis_ok", True)) and bool(samples[idx].get("homeostasis_ok", True)),
            "count": merged_count,
        }
        return
    appended = dict(sample)
    appended[key_name] = int(key_value)
    appended["count"] = 1
    samples.append(appended)
    if len(samples) > max_items:
        del samples[:-max_items]


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
