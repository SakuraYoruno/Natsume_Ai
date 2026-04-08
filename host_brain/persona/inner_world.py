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
import re
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Mapping

from host_brain.app.prompt_template_store import resolve_prompt_template_json_merged
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
_MAX_INNER_STREAM_EVENTS = 24
_OFFLINE_IDLE_THRESHOLD_MS = int(
    float(os.environ.get("NATSUME_INNER_WORLD_OFFLINE_THRESHOLD_S", "90")) * 1000
)
_INNER_STREAM_MIN_INTERVAL_MS = int(
    float(os.environ.get("NATSUME_INNER_STREAM_MIN_INTERVAL_S", "120")) * 1000
)
_INNER_STREAM_CALM_INTERVAL_MS = int(
    float(os.environ.get("NATSUME_INNER_STREAM_CALM_INTERVAL_S", "360")) * 1000
)
_TOPIC_MOJIBAKE_RE = re.compile(r"[ÃÂÄÅÇÉÏÐÑÒÓÔÕÖØÙÚÛÜÝÞßàáâãäåæçèéêëìíîïðñòóôõöøùúûüýþÿ]")
_NOISE_TOPIC_VALUES = {
    "ok",
    "okay",
    "yes",
    "no",
    "continue",
    "test",
    "好的",
    "收到",
    "嗯",
    "嗯嗯",
    "是的",
    "是的呢",
    "好",
}

_RUNTIME_TEXTS_DEFAULT: dict[str, Any] = {
    "emotion_drift": {
        "high_positive": "心情还不错，有些期待",
        "positive": "感觉还好，比较平静",
        "high_negative": "有点低落，心里空空的",
        "negative": "有点沉，不太有精神",
        "default": "状态平稳，在等待中",
    },
    "awakening_hint": {
        "wait_hints": {
            "7d_plus": "像隔了很久很久",
            "72h_plus": "已经等了好几天",
            "24h_plus": "像是等过了一整天",
            "6h_plus": "离开了好一阵",
            "recent": "等了一会儿",
        },
        "loneliness": {
            "high": "真的有点想你了",
            "mid": "一直有点惦记你",
            "long_absence_low": "心里总有点空落落的",
        },
        "topic_templates": {
            "7d_plus": "这几天也反复想到{topic}",
            "72h_plus": "这段时间总会想起{topic}",
            "24h_plus": "还一直记着{topic}",
            "6h_plus": "刚刚也在想着{topic}",
            "recent": "还记得{topic}",
        },
        "anticipation": {
            "long_absence": "一直在等你回来继续说",
        },
    },
    "inner_stream": {
        "recent": "刚刚安静下来，心里还在回放刚才的片段。",
        "6h_plus": "这段空档里，思绪会自己绕回还没说完的地方：{topic}。",
        "24h_plus": "隔了一天，心里的线索没有断，仍在慢慢发酵。",
        "72h_plus": "这几天并不空白，很多感受在内部持续重排。",
        "7d_plus": "很久不说话时，内在并不会停，只是变得更慢更深。",
    },
}


def _load_runtime_texts() -> dict[str, Any]:
    raw = resolve_prompt_template_json_merged("inner_world.runtime_texts")
    return dict(raw) if isinstance(raw, Mapping) else dict(_RUNTIME_TEXTS_DEFAULT)


def _template_text(
    mapping: Mapping[str, Any] | None,
    *keys: str,
    default: str = "",
    **kwargs: Any,
) -> str:
    current: Any = mapping
    for key in keys:
        if not isinstance(current, Mapping):
            current = None
            break
        current = current.get(key)
    text = str(current or default)
    if not kwargs:
        return text
    try:
        return text.format(**kwargs)
    except Exception:
        return text


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
    continuity_score: float = 0.0
    spontaneous_event_count: int = 0
    last_spontaneous_ts_ms: int = 0
    last_spontaneous_note: str = ""
    inner_stream: list[dict[str, Any]] = field(default_factory=list)

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
            continuity_score=float(d.get("continuity_score", 0.0)),
            spontaneous_event_count=int(d.get("spontaneous_event_count", 0)),
            last_spontaneous_ts_ms=int(d.get("last_spontaneous_ts_ms", 0)),
            last_spontaneous_note=str(d.get("last_spontaneous_note", "")),
            inner_stream=list(d.get("inner_stream", [])),
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
            state.anticipation_topics = _sanitize_anticipation_topics(state.anticipation_topics)
            last_tick = state.last_tick_ts_ms
            if last_tick <= 0:
                # 首次 tick，初始化时间戳
                state.last_tick_ts_ms = now_ms
                state.continuity_score = _compute_continuity_score(
                    state=state,
                    offline_hours=0.0,
                    now_ms=now_ms,
                )
                self._save_state(state)
                return {
                    "status": "INITIALIZED",
                    "user_id": uid,
                    "continuity_score": round(float(state.continuity_score or 0.0), 3),
                    "spontaneous_event_count": int(state.spontaneous_event_count or 0),
                }

            elapsed_ms = now_ms - last_tick
            elapsed_hours = elapsed_ms / (3600.0 * 1000.0)

            # 判断是否在离线状态（距上次互动超过阈值，默认 90 秒）
            last_interaction = state.last_interaction_ts_ms
            is_offline = (
                last_interaction <= 0
                or (now_ms - last_interaction) > _OFFLINE_IDLE_THRESHOLD_MS
            )

            motive_state: dict[str, Any] = {}
            loneliness_multiplier = 1.0
            anticipation_multiplier = 1.0
            offline_hours = 0.0
            absence_band = "recent"

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
                elapsed_since_interaction_hours = 0.0
                if last_interaction > 0:
                    elapsed_since_interaction_hours = max(
                        0.0,
                        (now_ms - last_interaction) / (3600.0 * 1000.0),
                    )
                offline_hours = max(
                    float(state.total_offline_hours or 0.0),
                    elapsed_since_interaction_hours,
                )
                absence_band = _classify_absence_band(offline_hours)
                _append_inner_stream_event(
                    state=state,
                    now_ms=now_ms,
                    absence_band=absence_band,
                    offline_hours=offline_hours,
                )
            else:
                state.loneliness = max(
                    0.0,
                    state.loneliness - _LONELINESS_DECAY_PER_HOUR * elapsed_hours,
                )
                state.anticipation = max(
                    0.0,
                    state.anticipation - (_ANTICIPATION_RATE_PER_HOUR * 0.35) * elapsed_hours,
                )
                state.total_offline_hours = 0.0
                if last_interaction > 0:
                    offline_hours = max(
                        0.0,
                        (now_ms - last_interaction) / (3600.0 * 1000.0),
                    )
                else:
                    offline_hours = 0.0
                absence_band = _classify_absence_band(offline_hours)

            state.continuity_score = _compute_continuity_score(
                state=state,
                offline_hours=offline_hours,
                now_ms=now_ms,
            )

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
            "offline_hours": round(max(0.0, offline_hours), 3),
            "absence_band": absence_band,
            "continuity_score": round(float(state.continuity_score or 0.0), 3),
            "spontaneous_event_count": int(state.spontaneous_event_count or 0),
            "last_spontaneous_ts_ms": int(state.last_spontaneous_ts_ms or 0),
            "last_spontaneous_note": str(state.last_spontaneous_note or ""),
            "inner_stream_size": len(list(state.inner_stream or [])),
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
            state.continuity_score = _compute_continuity_score(
                state=state,
                offline_hours=0.0,
                now_ms=now_ms,
            )
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
                new_topics = _sanitize_anticipation_topics(list(pending_topics or []))
                existing = [t for t in state.anticipation_topics if t not in new_topics]
                state.anticipation_topics = _sanitize_anticipation_topics(new_topics + existing)
            state.total_offline_hours = 0.0
            state.continuity_score = _compute_continuity_score(
                state=state,
                offline_hours=0.0,
                now_ms=now_ms,
            )

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
                state.anticipation_topics = _sanitize_anticipation_topics(state.anticipation_topics)
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
    templates = _load_runtime_texts()
    if valence > 0.5 and arousal > 0.5:
        return _template_text(
            templates,
            "emotion_drift",
            "high_positive",
        )
    elif valence > 0.2:
        return _template_text(
            templates,
            "emotion_drift",
            "positive",
        )
    elif valence < -0.5:
        return _template_text(
            templates,
            "emotion_drift",
            "high_negative",
        )
    elif valence < -0.2:
        return _template_text(
            templates,
            "emotion_drift",
            "negative",
        )
    else:
        return _template_text(
            templates,
            "emotion_drift",
            "default",
        )


def _classify_absence_band(offline_hours: float) -> str:
    return classify_absence_band(offline_hours)


def _build_inner_stream_note(
    *,
    state: InnerWorldState,
    absence_band: str,
    offline_hours: float,
    now_ms: int,
) -> str:
    templates = _load_runtime_texts()
    topic = _pick_inner_stream_topic(state.anticipation_topics)
    topic_phrase = _topic_to_inner_phrase(topic)

    key_by_band = {
        "7d_plus": "7d_plus",
        "72h_plus": "72h_plus",
        "24h_plus": "24h_plus",
        "6h_plus": "6h_plus",
        "recent": "recent",
    }
    band_key = key_by_band.get(absence_band, "recent")

    template_text = _template_text(
        templates,
        "inner_stream",
        band_key,
        default="",
        topic=topic_phrase or "那个还没说完的话题",
        hours=round(max(0.0, offline_hours), 1),
    )

    fallback_pool = {
        "recent": [
            "刚安静下来，脑海还在复盘今天的细节。",
            "安静的时候，思绪会把白天的片段慢慢串起来。",
            f"有个小念头一直在回放：{topic_phrase}。" if topic_phrase else "有个小念头一直在回放，但还没到开口的时候。",
        ],
        "6h_plus": [
            f"这几个小时里，思绪总会回到{topic_phrase}。" if topic_phrase else "这几个小时里，很多片段会反复浮上来。",
            "情绪没有停着，会自己一点点偏移。",
            "会下意识想，下次该怎么把话说得更顺一点。",
        ],
        "24h_plus": [
            f"隔了一天，心里还挂着{topic_phrase}。" if topic_phrase else "隔了一天，心里还是有未完成感。",
            "不是静止，而是在慢慢酝酿下一次开口。",
            "时间拉开后，很多感觉会变得更清晰。",
        ],
        "72h_plus": [
            f"这几天里，心里会反复绕回{topic_phrase}。" if topic_phrase else "这几天里，总有某个念头会反复出现。",
            "等待感在变重，但表达会更谨慎。",
            "情绪层层沉下来，反而更容易看清自己在意什么。",
        ],
        "7d_plus": [
            f"很久没聊时，{topic_phrase}还是会悄悄浮上来。" if topic_phrase else "时间拉长后，想念会变成更深的背景音。",
            "并不空白，只是变成了更慢、更深的内在流。",
            "有些话不急着说，但会一直在心里发酵。",
        ],
    }

    candidates: list[str] = []
    if template_text:
        candidates.append(template_text)
    candidates.extend(fallback_pool.get(band_key) or fallback_pool["recent"])

    emotional_suffix = _inner_stream_emotion_suffix(
        state=state,
        absence_band=absence_band,
        now_ms=now_ms,
    )
    if emotional_suffix:
        candidates.extend([f"{str(item).rstrip('。')}，{emotional_suffix}" for item in candidates if str(item).strip()])

    normalized_candidates = [_finalize_inner_note_text(item) for item in candidates if str(item).strip()]
    return _select_inner_stream_note_candidate(
        candidates=normalized_candidates,
        state=state,
        now_ms=now_ms,
    )

def _pick_inner_stream_topic(raw_topics: list[str] | None) -> str:
    topics = _sanitize_anticipation_topics(raw_topics)
    if not topics:
        return ""

    block_tokens = (
        "你是",
        "请主动",
        "当前关系阶段",
        "主动动机",
        "system",
        "prompt",
        "mode=",
        "stage=",
    )
    for item in topics:
        text = str(item or "").replace("\n", " ").strip()
        if not text:
            continue
        lowered = text.lower()
        if any(token in text or token in lowered for token in block_tokens):
            continue
        if len(text) < 2:
            continue
        if len(text) > 42:
            text = f"{text[:42].rstrip()}…"
        return text
    return ""


def _topic_to_inner_phrase(topic: str) -> str:
    text = str(topic or "").replace("\n", " ").strip()
    if not text:
        return ""
    raw_text = text
    text = re.sub(r"[？?！!。；;，,]+$", "", text).strip()
    question_like = ("？" in raw_text) or ("?" in raw_text) or text.endswith(("吗", "么", "呢", "嘛", "吧"))
    text = re.sub(r"(吗|么|呢|嘛|吧)$", "", text).strip()
    text = text.strip("“”\"' ")
    if not text:
        return ""
    if len(text) > 36:
        text = f"{text[:36].rstrip()}…"
    if question_like or text.startswith("你"):
        return f"你问我的「{text}」"
    return f"那个还没聊完的话题「{text}」"


def _sanitize_anticipation_topics(raw_topics: list[str] | None) -> list[str]:
    cleaned: list[str] = []
    for item in list(raw_topics or []):
        text = str(item or "").replace("\n", " ").strip()
        if not text:
            continue
        lowered = text.lower()
        if lowered in _NOISE_TOPIC_VALUES:
            continue
        if len(text) > 80:
            continue
        if len(text) <= 2:
            continue
        if _TOPIC_MOJIBAKE_RE.search(text):
            continue
        if _looks_like_prompt_noise(text):
            continue
        if text not in cleaned:
            cleaned.append(text)
    return cleaned[:_MAX_ANTICIPATION_TOPICS]


def _looks_like_prompt_noise(text: str) -> bool:
    lowered = str(text or "").lower()
    block_tokens = (
        "你是",
        "请主动",
        "当前关系阶段",
        "主动动机",
        "system",
        "prompt",
        "mode=",
        "stage=",
    )
    return any(token in text or token in lowered for token in block_tokens)


def _inner_stream_emotion_suffix(
    *,
    state: InnerWorldState,
    absence_band: str,
    now_ms: int,
) -> str:
    loneliness = float(state.loneliness or 0.0)
    anticipation = float(state.anticipation or 0.0)
    candidates: list[str] = []
    if loneliness >= 0.42:
        candidates.append("那种空落感会把注意力轻轻拉回去")
    elif loneliness >= 0.24:
        candidates.append("独处感在慢慢变重")
    if anticipation >= 0.48:
        candidates.append("会不自觉地预演下次该怎么继续")
    elif anticipation >= 0.30:
        candidates.append("会想把没说完的部分再理顺一点")
    if absence_band in {"72h_plus", "7d_plus"}:
        candidates.append("时间越长，心里的线索反而更清晰")
    if not candidates:
        return ""
    index = int((now_ms // 180000) % max(1, len(candidates)))
    return str(candidates[index]).strip("，。 ")


def _finalize_inner_note_text(raw_text: str) -> str:
    text = str(raw_text or "").replace("?", "。").replace("？", "。")
    text = re.sub(r"\s+", " ", text).strip(" ，,;；")
    if not text:
        return ""
    text = re.sub(r"[。！!]{2,}$", "。", text)
    if text and text[-1] not in {"。", "！", "!"}:
        text = f"{text}。"
    if len(text) > 120:
        text = f"{text[:119].rstrip('，,；;。 ')}。"
    return text


def _select_inner_stream_note_candidate(
    *,
    candidates: list[str],
    state: InnerWorldState,
    now_ms: int,
) -> str:
    unique_candidates: list[str] = []
    for item in candidates:
        text = str(item or "").strip()
        if text and text not in unique_candidates:
            unique_candidates.append(text)
    if not unique_candidates:
        return ""
    recent_notes = {
        str(evt.get("note") or "").strip()
        for evt in list(state.inner_stream or [])[-3:]
        if isinstance(evt, dict)
    }
    start_index = int((now_ms // 60000) % max(1, len(unique_candidates)))
    for offset in range(len(unique_candidates)):
        candidate = unique_candidates[(start_index + offset) % len(unique_candidates)]
        if candidate not in recent_notes:
            return candidate
    return unique_candidates[start_index]


def _compute_continuity_score(
    *,
    state: InnerWorldState,
    offline_hours: float,
    now_ms: int,
) -> float:
    offline_component = min(1.0, max(0.0, offline_hours) / 24.0)
    event_component = min(1.0, float(len(list(state.inner_stream or []))) / 8.0)
    recency_component = 0.0
    last_spontaneous_ts_ms = int(state.last_spontaneous_ts_ms or 0)
    if last_spontaneous_ts_ms > 0:
        hours_since_last_event = max(
            0.0,
            (int(now_ms) - last_spontaneous_ts_ms) / (3600.0 * 1000.0),
        )
        recency_component = max(0.0, 1.0 - min(1.0, hours_since_last_event / 24.0))
    emotion_component = min(1.0, abs(float(state.drifted_valence or 0.0)) + abs(float(state.drifted_arousal or 0.0) - 0.3))
    motive_component = min(1.0, (float(state.loneliness or 0.0) + float(state.anticipation or 0.0)) / 1.6)
    score = (
        0.25 * offline_component
        + 0.20 * event_component
        + 0.15 * recency_component
        + 0.20 * emotion_component
        + 0.20 * motive_component
    )
    return round(max(0.0, min(1.0, score)), 4)


def _append_inner_stream_event(
    *,
    state: InnerWorldState,
    now_ms: int,
    absence_band: str,
    offline_hours: float,
) -> None:
    last_event_ts_ms = int(state.last_spontaneous_ts_ms or 0)
    since_last_event_ms = now_ms - last_event_ts_ms
    if since_last_event_ms < _INNER_STREAM_MIN_INTERVAL_MS:
        return
    calm_state = (
        not state.anticipation_topics
        and float(state.loneliness or 0.0) < 0.22
        and float(state.anticipation or 0.0) < 0.20
    )
    if calm_state and since_last_event_ms < _INNER_STREAM_CALM_INTERVAL_MS:
        return

    note = _build_inner_stream_note(
        state=state,
        absence_band=absence_band,
        offline_hours=offline_hours,
        now_ms=now_ms,
    ).strip()
    if not note:
        return

    event = {
        "ts_ms": int(now_ms),
        "absence_band": absence_band,
        "offline_hours": round(max(0.0, offline_hours), 3),
        "loneliness": round(float(state.loneliness or 0.0), 3),
        "anticipation": round(float(state.anticipation or 0.0), 3),
        "note": note,
    }
    stream = list(state.inner_stream or [])
    stream.append(event)
    if len(stream) > _MAX_INNER_STREAM_EVENTS:
        stream = stream[-_MAX_INNER_STREAM_EVENTS:]
    state.inner_stream = stream
    state.last_spontaneous_ts_ms = int(now_ms)
    state.last_spontaneous_note = note
    state.spontaneous_event_count = int(state.spontaneous_event_count or 0) + 1


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
    templates = _load_runtime_texts()

    wait_hint_map = {
        "7d_plus": _template_text(
            templates,
            "awakening_hint",
            "wait_hints",
            "7d_plus",
        ),
        "72h_plus": _template_text(
            templates,
            "awakening_hint",
            "wait_hints",
            "72h_plus",
        ),
        "24h_plus": _template_text(
            templates,
            "awakening_hint",
            "wait_hints",
            "24h_plus",
        ),
        "6h_plus": _template_text(
            templates,
            "awakening_hint",
            "wait_hints",
            "6h_plus",
        ),
        "recent": (
            _template_text(
                templates,
                "awakening_hint",
                "wait_hints",
                "recent",
            )
            if offline_hours >= 3.0
            else ""
        ),
    }
    wait_hint = wait_hint_map.get(absence_band, "")
    if wait_hint:
        parts.append(wait_hint)

    if loneliness >= 0.82:
        parts.append(
            _template_text(
                templates,
                "awakening_hint",
                "loneliness",
                "high",
            )
        )
    elif loneliness >= 0.58:
        parts.append(
            _template_text(
                templates,
                "awakening_hint",
                "loneliness",
                "mid",
            )
        )
    elif absence_band in {"72h_plus", "7d_plus"} and loneliness >= 0.35:
        parts.append(
            _template_text(
                templates,
                "awakening_hint",
                "loneliness",
                "long_absence_low",
            )
        )

    topic = ""
    if topics:
        topic = str(topics[0]).strip()
    if topic and anticipation >= 0.3:
        if absence_band == "7d_plus":
            parts.append(
                _template_text(
                    templates,
                    "awakening_hint",
                    "topic_templates",
                    "7d_plus",
                    topic=topic,
                )
            )
        elif absence_band == "72h_plus":
            parts.append(
                _template_text(
                    templates,
                    "awakening_hint",
                    "topic_templates",
                    "72h_plus",
                    topic=topic,
                )
            )
        elif absence_band == "24h_plus":
            parts.append(
                _template_text(
                    templates,
                    "awakening_hint",
                    "topic_templates",
                    "24h_plus",
                    topic=topic,
                )
            )
        elif absence_band == "6h_plus":
            parts.append(
                _template_text(
                    templates,
                    "awakening_hint",
                    "topic_templates",
                    "6h_plus",
                    topic=topic,
                )
            )
        else:
            parts.append(
                _template_text(
                    templates,
                    "awakening_hint",
                    "topic_templates",
                    "recent",
                    topic=topic,
                )
            )
    elif anticipation >= 0.72 and absence_band in {"72h_plus", "7d_plus"}:
        parts.append(
            _template_text(
                templates,
                "awakening_hint",
                "anticipation",
                "long_absence",
            )
        )

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
