"""OfflineInnerWorld：离线内心世界状态机——Phase XVI。

职责：
  1. InnerStateClock：持续运行的内在时钟，每分钟 tick 一次（由 NatsumeScheduler 驱动）
  2. OfflineEmotionDrift：无用户输入时的情绪自然漂移（复用 EmotionDecayModel）
  3. LonelinessAccumulator：孤独感积累器（无互动时长 → 孤独值 [0, 1]）
  4. AnticipationTracker：期待值追踪（上次对话话题 → 期待再聊）
  5. OfflineStateSnapshot：每次对话开始时生成"这段时间发生了什么"的快照

设计原则：
  - 纯状态机，不直接发送消息（由 proactive_talk skill 调用）
  - 持久化到 JSON 文件（进程重启后可恢复）
  - 线程安全：所有读写通过 _lock 保护
  - 与 EmotionPersistence 协同：漂移结果写回 EmotionPersistence

唤醒快照示例：
  {
    "offline_hours": 8.5,
    "loneliness_level": 0.6,
    "anticipation_topics": ["你说的那个项目", "上次聊到的旅行"],
    "emotion_drift_summary": "有点低落，但还好",
    "awakening_hint": "等了挺久的，有点想你",
    "should_express_waiting": True,
  }
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from host_brain.persona.offline_continuity import classify_absence_band

_logger = logging.getLogger("Natsume.inner_world")

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 默认持久化目录
_DEFAULT_STATE_DIR = os.environ.get(
    "NATSUME_INNER_WORLD_DIR",
    os.path.join(os.path.expanduser("~"), ".natsume", "inner_world"),
)

# 孤独感积累速率：每小时增加多少孤独值（最大 1.0）
# 约 12 小时达到 0.6（中等孤独），24 小时接近 1.0
_LONELINESS_RATE_PER_HOUR = 0.05

# 孤独感衰减速率：用户回来后每小时衰减多少
_LONELINESS_DECAY_PER_HOUR = 0.3

# 期待值积累速率：每小时增加多少（最大 1.0）
_ANTICIPATION_RATE_PER_HOUR = 0.03

# 情绪漂移：离线时向基线回归的速率（与 EmotionPersistence 一致）
_DRIFT_DECAY_RATE = float(os.environ.get("HOST_EMOTION_PERSISTENCE_DECAY_RATE", "0.1"))

# 孤独感触发主动发声的阈值
LONELINESS_PROACTIVE_THRESHOLD = float(
    os.environ.get("NATSUME_LONELINESS_PROACTIVE_THRESHOLD", "0.7")
)

# 表达等待感的最低离线时长（小时）
_MIN_HOURS_FOR_WAITING_EXPRESSION = 3.0

# 期待话题最大保留数量
_MAX_ANTICIPATION_TOPICS = 5


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class InnerWorldState:
    """离线内心世界的持久化状态。"""
    user_id: str

    # 时间感
    last_interaction_ts_ms: int = 0       # 上次用户互动时间戳
    last_tick_ts_ms: int = 0              # 上次 tick 时间戳

    # 孤独感 [0, 1]
    loneliness: float = 0.0

    # 期待值 [0, 1]
    anticipation: float = 0.0

    # 期待话题列表（上次对话中提到的未完结话题）
    anticipation_topics: list[str] = field(default_factory=list)

    # 情绪漂移：当前离线情绪效价（向基线漂移中）
    drifted_valence: float = 0.0
    drifted_arousal: float = 0.3
    mood_baseline: float = 0.0

    # 累计离线时长（小时，用于快照描述）
    total_offline_hours: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "InnerWorldState":
        return cls(
            user_id=str(d.get("user_id", "")),
            last_interaction_ts_ms=int(d.get("last_interaction_ts_ms", 0)),
            last_tick_ts_ms=int(d.get("last_tick_ts_ms", 0)),
            loneliness=float(d.get("loneliness", 0.0)),
            anticipation=float(d.get("anticipation", 0.0)),
            anticipation_topics=list(d.get("anticipation_topics", [])),
            drifted_valence=float(d.get("drifted_valence", 0.0)),
            drifted_arousal=float(d.get("drifted_arousal", 0.3)),
            mood_baseline=float(d.get("mood_baseline", 0.0)),
            total_offline_hours=float(d.get("total_offline_hours", 0.0)),
        )


@dataclass(frozen=True)
class OfflineStateSnapshot:
    """唤醒时注入的离线状态快照。"""
    offline_hours: float                  # 本次离线时长（小时）
    loneliness_level: float               # 孤独感 [0, 1]
    anticipation_level: float             # 期待值 [0, 1]
    anticipation_topics: list[str]        # 期待话题
    emotion_drift_summary: str            # 情绪漂移自然语言描述
    awakening_hint: str                   # 唤醒时的内心独白提示
    should_express_waiting: bool          # 是否应该表达等待感

    absence_band: str = "recent"

    def as_dict(self) -> dict[str, Any]:
        return {
            "offline_hours": round(self.offline_hours, 1),
            "absence_band": self.absence_band,
            "loneliness_level": round(self.loneliness_level, 3),
            "anticipation_level": round(self.anticipation_level, 3),
            "anticipation_topics": list(self.anticipation_topics),
            "emotion_drift_summary": self.emotion_drift_summary,
            "awakening_hint": self.awakening_hint,
            "should_express_waiting": self.should_express_waiting,
        }


# ---------------------------------------------------------------------------
# OfflineInnerWorld 主类
# ---------------------------------------------------------------------------

class OfflineInnerWorld:
    """离线内心世界状态机（Phase XVI）。

    用法：
        inner_world = OfflineInnerWorld()

        # 每分钟由 NatsumeScheduler 调用（tick 任务）
        inner_world.tick(user_id="user_1")

        # 用户开始对话时调用，获取唤醒快照
        snapshot = inner_world.on_user_awaken(user_id="user_1")
        # snapshot.awakening_hint → 注入 prompt

        # 对话结束时调用，更新话题和情绪
        inner_world.on_session_end(
            user_id="user_1",
            valence=0.6,
            arousal=0.4,
            pending_topics=["你说的那个项目"],
        )
    """

    def __init__(self, *, state_dir: str | None = None) -> None:
        self._state_dir = Path(state_dir or _DEFAULT_STATE_DIR)
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # user_id → InnerWorldState（内存缓存）
        self._states: dict[str, InnerWorldState] = {}

    # ── 核心 tick ──────────────────────────────────────────────────────────

    def tick(self, user_id: str) -> dict[str, Any]:
        """内在时钟 tick——由 NatsumeScheduler 每分钟调用。

        执行：
          1. 计算距上次 tick 的时间差
          2. 更新孤独感（积累）
          3. 更新期待值（积累）
          4. 更新情绪漂移（向基线回归）
          5. 持久化状态

        Returns:
            tick 结果摘要（用于调度器审计）
        """
        uid = str(user_id).strip()
        now_ms = _now_ms()

        with self._lock:
            state = self._load_state(uid)
            last_tick = state.last_tick_ts_ms
            if last_tick <= 0:
                # 首次 tick，初始化时间戳
                state.last_tick_ts_ms = now_ms
                self._save_state(state)
                return {"status": "INITIALIZED", "user_id": uid}

            elapsed_ms = now_ms - last_tick
            elapsed_hours = elapsed_ms / (3600.0 * 1000.0)

            # 判断是否在离线状态（距上次互动 > 5 分钟视为离线）
            last_interaction = state.last_interaction_ts_ms
            is_offline = (
                last_interaction <= 0
                or (now_ms - last_interaction) > 5 * 60 * 1000
            )

            motive_state: dict[str, Any] = {}
            loneliness_multiplier = 1.0
            anticipation_multiplier = 1.0

            if is_offline:
                try:
                    from host_brain.persona.motive_runtime import compute_runtime_motive_state  # noqa: PLC0415

                    motive_state = compute_runtime_motive_state(
                        persona_user_id=uid,
                        pending_topics=list(state.anticipation_topics),
                        inner_state=state.as_dict(),
                        now_ms=now_ms,
                    )
                    reach_out_drive = float(motive_state.get("reach_out_drive") or 0.0)
                    continuity_drive = float(motive_state.get("continuity_drive") or 0.0)
                    withdraw_drive = float(motive_state.get("withdraw_drive") or 0.0)
                    self_protect_drive = float(motive_state.get("self_protect_drive") or 0.0)
                    loneliness_multiplier = max(
                        0.75,
                        min(1.35, 1.0 + reach_out_drive * 0.30 - self_protect_drive * 0.15),
                    )
                    anticipation_multiplier = max(
                        0.60,
                        min(
                            1.50,
                            1.0
                            + continuity_drive * 0.45
                            + reach_out_drive * 0.20
                            - withdraw_drive * 0.20
                            - self_protect_drive * 0.20,
                        ),
                    )
                except Exception as motive_exc:
                    _logger.debug("InnerWorld motive tick skipped: user=%s err=%s", uid, motive_exc)

                # 孤独感积累
                state.loneliness = min(
                    1.0,
                    state.loneliness + _LONELINESS_RATE_PER_HOUR * elapsed_hours * loneliness_multiplier,
                )
                # 期待值积累
                state.anticipation = min(
                    1.0,
                    state.anticipation + _ANTICIPATION_RATE_PER_HOUR * elapsed_hours * anticipation_multiplier,
                )
                # 情绪漂移：向基线回归
                decay_factor = math.exp(-_DRIFT_DECAY_RATE * elapsed_hours)
                state.drifted_valence = (
                    state.drifted_valence * decay_factor
                    + state.mood_baseline * (1.0 - decay_factor)
                )
                state.drifted_arousal = (
                    state.drifted_arousal * decay_factor
                    + 0.3 * (1.0 - decay_factor)  # 基线唤醒度 0.3
                )
                # 累计离线时长
                state.total_offline_hours += elapsed_hours

            state.last_tick_ts_ms = now_ms
            self._save_state(state)

        _logger.debug(
            "InnerWorld tick: user=%s offline=%s loneliness=%.3f anticipation=%.3f motive=%s",
            uid, is_offline, state.loneliness, state.anticipation,
            str((motive_state or {}).get("dominant_motive") or "none"),
        )
        return {
            "status": "OK",
            "user_id": uid,
            "is_offline": is_offline,
            "loneliness": round(state.loneliness, 3),
            "anticipation": round(state.anticipation, 3),
            "drifted_valence": round(state.drifted_valence, 3),
            "motive_state": motive_state,
            "loneliness_rate_multiplier": round(loneliness_multiplier, 3),
            "anticipation_rate_multiplier": round(anticipation_multiplier, 3),
        }

    # ── 唤醒事件 ──────────────────────────────────────────────────────────

    def on_user_awaken(self, user_id: str) -> OfflineStateSnapshot:
        """用户开始对话时调用——生成唤醒快照并重置孤独感。

        Returns:
            OfflineStateSnapshot，供 PromptBuilder 注入 offline_state 槽位
        """
        uid = str(user_id).strip()
        now_ms = _now_ms()

        with self._lock:
            state = self._load_state(uid)
            last_interaction = state.last_interaction_ts_ms

            # 计算本次离线时长
            if last_interaction > 0:
                offline_ms = now_ms - last_interaction
                offline_hours = offline_ms / (3600.0 * 1000.0)
            else:
                offline_hours = 0.0

            absence_band = _classify_absence_band(offline_hours)
            snapshot = OfflineStateSnapshot(
                offline_hours=offline_hours,
                loneliness_level=state.loneliness,
                anticipation_level=state.anticipation,
                anticipation_topics=list(state.anticipation_topics[:3]),
                emotion_drift_summary=_describe_emotion_drift(
                    state.drifted_valence, state.drifted_arousal
                ),
                awakening_hint=_build_awakening_hint(
                    offline_hours=offline_hours,
                    absence_band=absence_band,
                    loneliness=state.loneliness,
                    anticipation=state.anticipation,
                    topics=state.anticipation_topics,
                ),
                should_express_waiting=offline_hours >= _MIN_HOURS_FOR_WAITING_EXPRESSION,
                absence_band=absence_band,
            )

            # 唤醒后重置孤独感（用户回来了）
            state.loneliness = max(0.0, state.loneliness * 0.2)  # 保留 20%，不完全清零
            state.last_interaction_ts_ms = now_ms
            state.total_offline_hours = 0.0
            self._save_state(state)

        _logger.info(
            "InnerWorld awaken: user=%s offline_hours=%.1f loneliness=%.3f hint=%s",
            uid, offline_hours, snapshot.loneliness_level,
            snapshot.awakening_hint[:30] if snapshot.awakening_hint else "",
        )
        return snapshot

    # ── 会话结束事件 ──────────────────────────────────────────────────────

    def on_session_end(
        self,
        user_id: str,
        *,
        valence: float = 0.0,
        arousal: float = 0.3,
        pending_topics: list[str] | None = None,
        mood_baseline: float | None = None,
    ) -> None:
        """对话结束时调用——更新情绪基准和期待话题。

        Args:
            user_id: 用户 ID
            valence: 本次会话结束时的情绪效价
            arousal: 本次会话结束时的唤醒度
            pending_topics: 本次对话中未完结的话题标签列表
            mood_baseline: 长期心情基线（可选，来自 EmotionTimeline）
        """
        uid = str(user_id).strip()
        now_ms = _now_ms()

        with self._lock:
            state = self._load_state(uid)
            state.drifted_valence = float(valence)
            state.drifted_arousal = float(arousal)
            if mood_baseline is not None:
                state.mood_baseline = float(mood_baseline)
            state.last_interaction_ts_ms = now_ms
            state.anticipation = max(0.0, state.anticipation * 0.5)  # 对话后期待值减半

            # 更新期待话题（新话题追加到前面）
            if pending_topics:
                new_topics = [t for t in pending_topics if t and t.strip()]
                existing = [t for t in state.anticipation_topics if t not in new_topics]
                state.anticipation_topics = (new_topics + existing)[:_MAX_ANTICIPATION_TOPICS]

            self._save_state(state)

        _logger.debug(
            "InnerWorld session_end: user=%s valence=%.3f topics=%s",
            uid, valence, state.anticipation_topics,
        )

    # ── 状态查询 ──────────────────────────────────────────────────────────

    def get_state(self, user_id: str) -> dict[str, Any]:
        """获取当前内心状态（用于监控/调试）。"""
        uid = str(user_id).strip()
        with self._lock:
            state = self._load_state(uid)
            return state.as_dict()

    def should_trigger_proactive(self, user_id: str) -> bool:
        """判断孤独感是否达到主动发声阈值。"""
        uid = str(user_id).strip()
        with self._lock:
            state = self._load_state(uid)
            return state.loneliness >= LONELINESS_PROACTIVE_THRESHOLD

    # ── 持久化 ────────────────────────────────────────────────────────────

    def _state_file(self, user_id: str) -> Path:
        return self._state_dir / f"{user_id}_inner_world.json"

    def _load_state(self, user_id: str) -> InnerWorldState:
        """从内存缓存或文件加载状态（调用前需持有锁）。"""
        if user_id in self._states:
            return self._states[user_id]
        fp = self._state_file(user_id)
        if fp.exists():
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                state = InnerWorldState.from_dict(data)
                self._states[user_id] = state
                return state
            except Exception as exc:
                _logger.warning("InnerWorld: 加载状态失败 user=%s err=%s", user_id, exc)
        state = InnerWorldState(user_id=user_id)
        self._states[user_id] = state
        return state

    def _save_state(self, state: InnerWorldState) -> None:
        """持久化状态到文件（调用前需持有锁）。"""
        self._states[state.user_id] = state
        fp = self._state_file(state.user_id)
        try:
            fp.write_text(
                json.dumps(state.as_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            _logger.warning("InnerWorld: 保存状态失败 user=%s err=%s", state.user_id, exc)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _describe_emotion_drift(valence: float, arousal: float) -> str:
    """将漂移后的情绪向量转为自然语言描述。"""
    if valence > 0.5 and arousal > 0.5:
        return "心情还不错，有些期待"
    elif valence > 0.2:
        return "感觉还好，比较平静"
    elif valence < -0.5:
        return "有点低落，心里空空的"
    elif valence < -0.2:
        return "有点沉，不太有精神"
    else:
        return "状态平稳，在等待中"


def _classify_absence_band(offline_hours: float) -> str:
    return classify_absence_band(offline_hours)


def _build_awakening_hint(
    *,
    offline_hours: float,
    absence_band: str,
    loneliness: float,
    anticipation: float,
    topics: list[str],
) -> str:
    """根据离线状态生成唤醒时的内心独白提示。"""
    if offline_hours < 1.0:
        return ""

    parts: list[str] = []

    wait_hint_map = {
        "7d_plus": "像隔了很久很久",
        "72h_plus": "已经等了好几天",
        "24h_plus": "像是等过了一整天",
        "6h_plus": "离开了好一阵",
        "recent": "等了一会儿" if offline_hours >= 3.0 else "",
    }
    wait_hint = wait_hint_map.get(absence_band, "")
    if wait_hint:
        parts.append(wait_hint)

    if loneliness >= 0.82:
        parts.append("真的有点想你了")
    elif loneliness >= 0.58:
        parts.append("一直有点惦记你")
    elif absence_band in {"72h_plus", "7d_plus"} and loneliness >= 0.35:
        parts.append("心里总有点空落落的")

    topic = ""
    if topics:
        topic = str(topics[0]).strip()
    if topic and anticipation >= 0.3:
        if absence_band == "7d_plus":
            parts.append(f"这几天也反复想到{topic}")
        elif absence_band == "72h_plus":
            parts.append(f"这段时间总会想起{topic}")
        elif absence_band == "24h_plus":
            parts.append(f"还一直记着{topic}")
        elif absence_band == "6h_plus":
            parts.append(f"刚刚也在想着{topic}")
        else:
            parts.append(f"还记得{topic}")
    elif anticipation >= 0.72 and absence_band in {"72h_plus", "7d_plus"}:
        parts.append("一直在等你回来继续说")

    if not parts:
        return ""

    return "，".join(dict.fromkeys(part for part in parts if part))


def _now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# 全局单例（供 Skill handler 和 Scheduler 使用）
# ---------------------------------------------------------------------------

_global_inner_world: OfflineInnerWorld | None = None
_global_lock = threading.Lock()


def get_inner_world() -> OfflineInnerWorld:
    """获取全局 OfflineInnerWorld 单例。"""
    global _global_inner_world
    if _global_inner_world is None:
        with _global_lock:
            if _global_inner_world is None:
                _global_inner_world = OfflineInnerWorld()
    return _global_inner_world
