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
    "anticipation_topics": ["topic_a", "topic_b"],
    "emotion_drift_summary": "state_summary",
    "awakening_hint": "awakening_hint_text",
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
_INNER_WORLD_LLM_ENABLED = str(
    os.environ.get("NATSUME_INNER_WORLD_LLM_ENABLED", "true")
).strip().lower() not in {"0", "false", "no", "off"}
_INNER_WORLD_LLM_TIMEOUT_MS = max(
    800,
    int(float(os.environ.get("NATSUME_INNER_WORLD_LLM_TIMEOUT_S", "15")) * 1000),
)
_INNER_WORLD_LLM_CACHE_TTL_MS = max(
    5_000,
    int(float(os.environ.get("NATSUME_INNER_WORLD_LLM_CACHE_TTL_S", "120")) * 1000),
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
_INNER_WORLD_LLM_CACHE_LOCK = threading.Lock()
_INNER_WORLD_LLM_CACHE: dict[str, Any] = {
    "resolved_at_ms": 0,
    "runtime": None,
}

def _load_runtime_texts() -> dict[str, Any]:
    raw = resolve_prompt_template_json_merged("inner_world.runtime_texts")
    return dict(raw) if isinstance(raw, Mapping) else {}


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


def _template_text_list(values: Any, **kwargs: Any) -> list[str]:
    rendered: list[str] = []
    for item in list(values or []):
        text = str(item or "")
        if not text:
            continue
        if kwargs:
            try:
                text = text.format(**kwargs)
            except Exception:
                pass
        rendered.append(text)
    return rendered


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
    last_spontaneous_source: str = ""
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
            last_spontaneous_source=str(d.get("last_spontaneous_source", "")),
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
            pending_topics=["topic_a"],
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
            state.inner_stream = _sanitize_inner_stream_events(state.inner_stream)
            if state.inner_stream:
                latest_event = dict(state.inner_stream[-1])
                state.last_spontaneous_note = str(latest_event.get("note") or "").strip()
                state.last_spontaneous_ts_ms = int(latest_event.get("ts_ms") or state.last_spontaneous_ts_ms or 0)
                state.last_spontaneous_source = str(latest_event.get("source") or state.last_spontaneous_source or "fallback")
                if int(state.spontaneous_event_count or 0) < len(state.inner_stream):
                    state.spontaneous_event_count = len(state.inner_stream)
            else:
                state.last_spontaneous_note = ""
                state.last_spontaneous_ts_ms = 0
                state.last_spontaneous_source = ""
                state.spontaneous_event_count = 0
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
                    "last_spontaneous_source": str(state.last_spontaneous_source or ""),
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
            "last_spontaneous_source": str(state.last_spontaneous_source or ""),
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
                state.inner_stream = _sanitize_inner_stream_events(state.inner_stream)
                if state.inner_stream:
                    latest_event = dict(state.inner_stream[-1])
                    state.last_spontaneous_note = str(latest_event.get("note") or "").strip()
                    state.last_spontaneous_ts_ms = int(latest_event.get("ts_ms") or state.last_spontaneous_ts_ms or 0)
                    state.last_spontaneous_source = str(latest_event.get("source") or state.last_spontaneous_source or "fallback")
                    if int(state.spontaneous_event_count or 0) < len(state.inner_stream):
                        state.spontaneous_event_count = len(state.inner_stream)
                else:
                    state.last_spontaneous_note = ""
                    state.last_spontaneous_ts_ms = 0
                    state.last_spontaneous_source = ""
                    state.spontaneous_event_count = 0
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
    note, _focus = _build_inner_stream_note_with_focus(
        state=state,
        absence_band=absence_band,
        offline_hours=offline_hours,
        now_ms=now_ms,
    )
    return note


def _build_inner_stream_note_with_focus(
    *,
    state: InnerWorldState,
    absence_band: str,
    offline_hours: float,
    now_ms: int,
) -> tuple[str, str]:
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
    selected_focus = _pick_inner_stream_focus(
        state=state,
        topic_phrase=topic_phrase,
        absence_band=absence_band,
        now_ms=now_ms,
    )
    pools = _build_inner_stream_focus_pools(
        band_key=band_key,
        topic_phrase=topic_phrase,
    )
    ordered_focus = [selected_focus] + [
        focus
        for focus in ("self", "state", "plan", "topic")
        if focus != selected_focus
    ]
    candidate_pairs: list[tuple[str, str]] = []
    for focus in ordered_focus:
        focus_pool = list(pools.get(focus) or [])
        if focus == "topic" and template_text:
            focus_pool = [template_text, *focus_pool]
        candidate_pairs.extend(
            (focus, str(text or "").strip())
            for text in focus_pool
            if str(text or "").strip()
        )
    emotional_suffix = _inner_stream_emotion_suffix(
        state=state,
        absence_band=absence_band,
        now_ms=now_ms,
    )
    if emotional_suffix:
        candidate_pairs.extend([
            (focus, f"{str(item).rstrip('。')}，{emotional_suffix}")
            for focus, item in candidate_pairs
            if str(item).strip()
        ])

    normalized_pairs = [
        (focus, _finalize_inner_note_text(text))
        for focus, text in candidate_pairs
        if str(text).strip()
    ]
    normalized_pairs = [(focus, text) for focus, text in normalized_pairs if text]
    return _select_inner_stream_note_candidate(
        candidates=normalized_pairs,
        state=state,
        now_ms=now_ms,
    )


def _build_inner_stream_event_content(
    *,
    state: InnerWorldState,
    absence_band: str,
    offline_hours: float,
    now_ms: int,
) -> tuple[str, str, str, str]:
    llm_note, llm_focus, llm_reason = _generate_inner_stream_note_from_llm(
        state=state,
        absence_band=absence_band,
        offline_hours=offline_hours,
        now_ms=now_ms,
    )
    if llm_note:
        return llm_note, llm_focus or "self", "llm", ""
    fallback_note, fallback_focus = _build_inner_stream_note_with_focus(
        state=state,
        absence_band=absence_band,
        offline_hours=offline_hours,
        now_ms=now_ms,
    )
    return fallback_note, fallback_focus or "self", "fallback", llm_reason


def _generate_inner_stream_note_from_llm(
    *,
    state: InnerWorldState,
    absence_band: str,
    offline_hours: float,
    now_ms: int,
) -> tuple[str, str, str]:
    try:
        from host_brain.tribe.default_mode_agent import resolve_recent_foreground_interaction_state  # noqa: PLC0415

        recent_foreground = resolve_recent_foreground_interaction_state(current_ts_ms=now_ms)
        if bool(recent_foreground.get("active")):
            return "", "", "INNER_WORLD_FOREGROUND_CHAT_COOLDOWN"
    except Exception:
        pass

    try:
        from host_brain.persona.inner_activity_agent import generate_inner_activity_note  # noqa: PLC0415

        topic = _pick_inner_stream_topic(state.anticipation_topics)
        topic_phrase = _topic_to_inner_phrase(topic)
        note, focus, reason, _meta = generate_inner_activity_note(
            persona_user_id=str(state.user_id or "user_1"),
            absence_band=absence_band,
            offline_hours=offline_hours,
            inner_state=state.as_dict(),
            now_ms=now_ms,
            topic_phrase=topic_phrase,
        )
        return str(note or ""), str(focus or ""), str(reason or "")
    except Exception as agent_exc:
        return "", "", f"INNER_ACTIVITY_AGENT_ERROR:{str(agent_exc)[:96]}"

    if not _INNER_WORLD_LLM_ENABLED:
        return "", "", "INNER_WORLD_LLM_DISABLED"

    runtime = _resolve_inner_world_llm_runtime(now_ms=now_ms)
    if not isinstance(runtime, Mapping):
        return "", "", "INNER_WORLD_LLM_RUNTIME_UNAVAILABLE"
    if not bool(runtime.get("available")):
        return "", "", str(runtime.get("route_reason_code") or "INNER_WORLD_LLM_UNAVAILABLE")

    provider_name = str(runtime.get("provider_name") or "").strip()
    model_id = str(runtime.get("model_id") or "").strip()
    api_key = str(runtime.get("llm_api_key") or "").strip()
    base_url_override = str(runtime.get("base_url_override") or "").strip() or None
    timeout_ms = max(800, min(int(runtime.get("timeout_ms") or _INNER_WORLD_LLM_TIMEOUT_MS), _INNER_WORLD_LLM_TIMEOUT_MS))
    if not provider_name or not model_id:
        return "", "", "INNER_WORLD_LLM_RUNTIME_INVALID"
    if not api_key:
        return "", "", "INNER_WORLD_LLM_KEY_MISSING"

    topic = _pick_inner_stream_topic(state.anticipation_topics)
    topic_phrase = _topic_to_inner_phrase(topic)
    recent_events = [
        {
            "focus": str(evt.get("focus") or "").strip(),
            "note": str(evt.get("note") or "").strip(),
        }
        for evt in list(state.inner_stream or [])[-4:]
        if isinstance(evt, Mapping)
    ]
    recent_events = [
        item for item in recent_events
        if item.get("note")
    ]

    runtime_texts = _load_runtime_texts()
    llm_templates = dict(runtime_texts.get("inner_stream_llm_prompt") or {})
    system_prompt = str(llm_templates.get("system_prompt") or "").strip()
    prompt_payload = {
        "absence_band": absence_band,
        "offline_hours": round(max(0.0, float(offline_hours)), 2),
        "loneliness": round(float(state.loneliness or 0.0), 3),
        "anticipation": round(float(state.anticipation or 0.0), 3),
        "drifted_valence": round(float(state.drifted_valence or 0.0), 3),
        "drifted_arousal": round(float(state.drifted_arousal or 0.0), 3),
        "topic_phrase": topic_phrase or "",
        "recent_events": recent_events,
        "requirements": {
            "max_chars": 46,
            "must_be_inner_monologue": True,
            "must_not_repeat_recent_events": True,
            "focus_should_rotate": True,
            "persona_style": str(llm_templates.get("persona_style") or ""),
        },
    }
    prompt_text_template = str(llm_templates.get("user_prompt_template") or "{payload_json}")
    prompt_text = prompt_text_template.format(payload_json=json.dumps(prompt_payload, ensure_ascii=False))

    try:
        from host_brain.integrations.llm_helpers import stream_llm_chunks  # noqa: PLC0415

        chunks: list[str] = []
        for chunk in stream_llm_chunks(
            provider_name=provider_name,
            model_id=model_id,
            api_key=api_key,
            timeout_ms=timeout_ms,
            prompt_text=prompt_text,
            system_prompt=system_prompt,
            gen_params=dict(runtime.get("agent_gen_params") or {"temperature": 0.72, "top_p": 0.9}),
            base_url_override=base_url_override,
        ):
            if chunk:
                chunks.append(str(chunk))
            if len("".join(chunks)) > 1024:
                break
        raw_text = "".join(chunks).strip()
        if not raw_text:
            return "", "", "INNER_WORLD_LLM_EMPTY"
        parsed = _extract_llm_json_payload(raw_text)
        note = _finalize_inner_note_text(str(parsed.get("note") or ""))
        focus = str(parsed.get("focus") or "").strip().lower()
        if focus not in {"self", "state", "plan", "topic"}:
            focus = _infer_inner_stream_focus_from_note(note)
        if not note:
            return "", "", "INNER_WORLD_LLM_NOTE_EMPTY"
        return note, focus, ""
    except Exception as llm_exc:
        return "", "", f"INNER_WORLD_LLM_ERROR:{str(llm_exc)[:96]}"


def _extract_llm_json_payload(raw_text: str) -> dict[str, Any]:
    text = str(raw_text or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        if isinstance(parsed, Mapping):
            return dict(parsed)
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            if isinstance(parsed, Mapping):
                return dict(parsed)
        except Exception:
            return {}
    return {}


def _resolve_inner_world_llm_runtime(*, now_ms: int) -> dict[str, Any] | None:
    cache_now = int(now_ms)
    with _INNER_WORLD_LLM_CACHE_LOCK:
        resolved_at = int(_INNER_WORLD_LLM_CACHE.get("resolved_at_ms") or 0)
        cached_runtime = _INNER_WORLD_LLM_CACHE.get("runtime")
        if cached_runtime and (cache_now - resolved_at) <= _INNER_WORLD_LLM_CACHE_TTL_MS:
            return dict(cached_runtime) if isinstance(cached_runtime, Mapping) else None

    runtime: dict[str, Any] | None = None
    try:
        from host_brain.app.graph_config_builder import (  # noqa: PLC0415
            resolve_chat_agent_llm_runtime,
            resolve_host_llm_runtime,
        )

        base_runtime = resolve_host_llm_runtime(
            rollout_key="inner_world_tick",
            scene_type="CHAT",
        )
        runtime = resolve_chat_agent_llm_runtime(base_runtime=base_runtime)
        model_override = str(
            os.environ.get("HOST_INNER_WORLD_LLM_MODEL_ID")
            or os.environ.get("HOST_DISPATCH_AGENT_MODEL_ID")
            or "gemini-2.5-flash"
        ).strip()
        runtime["model_id"] = model_override
        runtime["chat_model_id"] = model_override
        runtime["agent_gen_params"] = {
            "temperature": 0.65,
            "top_p": 0.9,
        }
    except Exception as exc:
        _logger.debug("InnerWorld LLM runtime resolve failed: %s", exc)
        runtime = None

    with _INNER_WORLD_LLM_CACHE_LOCK:
        _INNER_WORLD_LLM_CACHE["resolved_at_ms"] = cache_now
        _INNER_WORLD_LLM_CACHE["runtime"] = dict(runtime) if isinstance(runtime, Mapping) else None
    return dict(runtime) if isinstance(runtime, Mapping) else None

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
        text = _try_repair_mojibake_text(str(item or "").replace("\n", " ").strip())
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
    runtime_texts = _load_runtime_texts()
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
        return _template_text(
            runtime_texts,
            "topic_phrase",
            "question_like_template",
            topic=text,
        )
    return _template_text(
        runtime_texts,
        "topic_phrase",
        "default_template",
        topic=text,
    )


def _pick_inner_stream_focus(
    *,
    state: InnerWorldState,
    topic_phrase: str,
    absence_band: str,
    now_ms: int,
) -> str:
    recent_focuses = [
        str(evt.get("focus") or "").strip().lower()
        for evt in list(state.inner_stream or [])[-3:]
        if isinstance(evt, Mapping)
    ]
    streak_focus = recent_focuses[-1] if recent_focuses else ""
    streak_len = 0
    for item in reversed(recent_focuses):
        if item == streak_focus and item:
            streak_len += 1
        else:
            break

    weights: list[tuple[str, float]] = [
        ("self", 1.4),
        ("state", 1.2),
        ("plan", 1.1 + float(state.anticipation or 0.0) * 0.4),
        ("topic", 1.2 if topic_phrase else 0.0),
    ]
    if absence_band in {"72h_plus", "7d_plus"}:
        weights = [(name, weight + (0.25 if name in {"self", "state"} else 0.0)) for name, weight in weights]
    if float(state.loneliness or 0.0) > 0.45:
        weights = [(name, weight + (0.3 if name == "topic" else 0.0)) for name, weight in weights]

    if streak_focus:
        adjusted: list[tuple[str, float]] = []
        for name, weight in weights:
            if name == streak_focus and streak_len >= 2:
                adjusted.append((name, weight * 0.1))
            elif name == streak_focus:
                adjusted.append((name, weight * 0.55))
            else:
                adjusted.append((name, weight))
        weights = adjusted

    pool = [(name, max(0.0, float(weight))) for name, weight in weights if float(weight) > 0.0]
    if not pool:
        return "self"
    total_weight = sum(weight for _name, weight in pool)
    pivot = (int(now_ms // 60000) % 1000) / 1000.0 * total_weight
    cursor = 0.0
    for name, weight in pool:
        cursor += weight
        if pivot <= cursor:
            return name
    return pool[-1][0]


def _build_inner_stream_focus_pools(*, band_key: str, topic_phrase: str) -> dict[str, list[str]]:
    runtime_texts = _load_runtime_texts()
    template_pools = dict(runtime_texts.get("inner_stream_focus_pools") or {})

    def _resolve_topic_pool() -> list[str]:
        topic_templates = dict(template_pools.get("topic") or {})
        band_templates = topic_templates.get(band_key) or topic_templates.get("recent") or {}
        if isinstance(band_templates, Mapping):
            branch = "with_topic" if topic_phrase else "without_topic"
            return _template_text_list(band_templates.get(branch), topic=topic_phrase)
        return _template_text_list(band_templates, topic=topic_phrase)

    def _resolve_named_pool(name: str) -> list[str]:
        named_templates = dict(template_pools.get(name) or {})
        band_templates = named_templates.get(band_key) or named_templates.get("recent") or []
        return _template_text_list(band_templates, topic=topic_phrase)

    return {
        "topic": _resolve_topic_pool(),
        "self": _resolve_named_pool("self"),
        "state": _resolve_named_pool("state"),
        "plan": _resolve_named_pool("plan"),
    }


def _sanitize_anticipation_topics(raw_topics: list[str] | None) -> list[str]:
    cleaned: list[str] = []
    for item in list(raw_topics or []):
        text = _try_repair_mojibake_text(str(item or "").replace("\n", " ").strip())
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
        if _is_low_value_probe_topic(text):
            continue
        if text not in cleaned:
            cleaned.append(text)
    return cleaned[:_MAX_ANTICIPATION_TOPICS]


def _try_repair_mojibake_text(raw_text: str) -> str:
    text = str(raw_text or "").strip()
    if not text:
        return ""
    latin1_count = sum(1 for ch in text if 0x00C0 <= ord(ch) <= 0x00FF)
    cjk_count = sum(1 for ch in text if 0x4E00 <= ord(ch) <= 0x9FFF)
    if latin1_count < 2:
        return text
    for codec in ("latin1", "cp1252"):
        try:
            repaired = text.encode(codec, errors="strict").decode("utf-8", errors="strict")
        except Exception:
            continue
        repaired_cjk = sum(1 for ch in repaired if 0x4E00 <= ord(ch) <= 0x9FFF)
        repaired_latin1 = sum(1 for ch in repaired if 0x00C0 <= ord(ch) <= 0x00FF)
        if repaired_cjk > 0 and (repaired_cjk >= cjk_count or repaired_latin1 < latin1_count):
            return repaired.strip()
    return text


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


def _is_low_value_probe_topic(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    lowered = value.lower()
    if len(value) <= 24 and re.search(r"[吗\?？]\s*$", value):
        probe_tokens = ("特长", "优点", "缺点", "能力", "会什么", "擅长", "喜欢我")
        if any(token in value for token in probe_tokens):
            return True
        if lowered.startswith("你") and any(token in value for token in ("有", "会", "能")):
            return True
    return False


def _contains_low_value_probe_fragment(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    probe_tokens = ("你有什么特长", "你有什麽特长", "你有什么优点", "你会什么")
    return any(token in value for token in probe_tokens)


def _rewrite_inner_stream_note(raw_note: str) -> str:
    runtime_texts = _load_runtime_texts()
    text = _try_repair_mojibake_text(str(raw_note or "").strip())
    if not text:
        return ""
    note_rewrite_templates = dict(runtime_texts.get("note_rewrite") or {})
    special_replacements = list(note_rewrite_templates.get("special_replacements") or [])
    for item in special_replacements:
        if not isinstance(item, Mapping):
            continue
        source = str(item.get("from") or "")
        target = str(item.get("to") or "")
        if source and target and source in text:
            text = text.replace(source, target)
    text = re.sub(
        r"你([^\s。！？?!]{1,24})吗",
        lambda match: _template_text(
            note_rewrite_templates,
            "you_question_template",
            fragment=match.group(1),
        ),
        text,
    )
    return text


def _sanitize_inner_stream_events(raw_stream: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    recent_note_keys: list[str] = []
    for item in list(raw_stream or []):
        if not isinstance(item, Mapping):
            continue
        event = dict(item)
        note = _rewrite_inner_stream_note(str(event.get("note") or ""))
        note = _finalize_inner_note_text(note)
        if not note:
            continue
        if _contains_low_value_probe_fragment(note):
            continue
        if _TOPIC_MOJIBAKE_RE.search(note):
            continue
        if _looks_like_prompt_noise(note):
            continue
        event["note"] = note
        focus = str(event.get("focus") or "").strip().lower()
        if not focus:
            focus = _infer_inner_stream_focus_from_note(note)
        event["focus"] = focus
        source = str(event.get("source") or "").strip().lower()
        if source not in {"llm", "fallback"}:
            source = "fallback"
        event["source"] = source
        note_key = _normalize_inner_note_key(note)
        if cleaned and str(cleaned[-1].get("focus") or "").strip().lower() == focus:
            continue
        if cleaned and str(cleaned[-1].get("note") or "").strip() == note:
            continue
        if note_key and note_key in recent_note_keys:
            continue
        cleaned.append(event)
        if note_key:
            recent_note_keys.append(note_key)
            if len(recent_note_keys) > 8:
                recent_note_keys = recent_note_keys[-8:]
    return cleaned[-_MAX_INNER_STREAM_EVENTS:]


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
    candidates: list[tuple[str, str]],
    state: InnerWorldState,
    now_ms: int,
) -> tuple[str, str]:
    unique_candidates: list[tuple[str, str]] = []
    seen_texts: set[str] = set()
    for focus, item in candidates:
        text = str(item or "").strip()
        if not text or text in seen_texts:
            continue
        seen_texts.add(text)
        unique_candidates.append((str(focus or "self").strip().lower() or "self", text))
    if not unique_candidates:
        return "", "self"
    recent_notes = {
        str(evt.get("note") or "").strip()
        for evt in list(state.inner_stream or [])[-8:]
        if isinstance(evt, dict)
    }
    recent_note_keys = {_normalize_inner_note_key(item) for item in recent_notes if item}
    recent_focuses = [
        str(evt.get("focus") or "").strip().lower()
        for evt in list(state.inner_stream or [])[-3:]
        if isinstance(evt, dict)
    ]
    last_focus = recent_focuses[-1] if recent_focuses else ""
    start_index = int((now_ms // 60000) % max(1, len(unique_candidates)))
    for offset in range(len(unique_candidates)):
        focus, candidate = unique_candidates[(start_index + offset) % len(unique_candidates)]
        if last_focus and focus == last_focus:
            continue
        candidate_key = _normalize_inner_note_key(candidate)
        if candidate not in recent_notes and candidate_key not in recent_note_keys:
            return candidate, focus
    if len(recent_notes) >= 4:
        return "", "self"
    for offset in range(len(unique_candidates)):
        focus, candidate = unique_candidates[(start_index + offset) % len(unique_candidates)]
        if last_focus and focus == last_focus:
            continue
        return candidate, focus
    return unique_candidates[start_index][1], unique_candidates[start_index][0]


def _infer_inner_stream_focus_from_note(note: str) -> str:
    text = str(note or "").strip()
    if not text:
        return "self"
    if any(token in text for token in ("你问我的", "话题", "重连")):
        return "topic"
    if any(token in text for token in ("下次", "开口", "顺序", "优先级", "收口", "草稿")):
        return "plan"
    if any(token in text for token in ("情绪", "独处", "节奏", "平衡", "慢变量", "归位")):
        return "state"
    return "self"


def _normalize_inner_note_key(note: str) -> str:
    text = str(note or "").strip().lower()
    if not text:
        return ""
    text = re.sub(r"[，。！？!?,;；:\-\s]+", "", text)
    return text[:48]


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

    note, focus, source, fallback_reason = _build_inner_stream_event_content(
        state=state,
        absence_band=absence_band,
        offline_hours=offline_hours,
        now_ms=now_ms,
    )
    note = str(note or "").strip()
    if not note:
        return

    recent_stream = list(state.inner_stream or [])
    recent_fallback_failures = [
        evt
        for evt in recent_stream[-4:]
        if isinstance(evt, Mapping)
        and str(evt.get("source") or "").strip().lower() == "fallback"
        and str(evt.get("fallback_reason") or "").strip()
    ]
    if (
        str(source or "").strip().lower() == "fallback"
        and str(fallback_reason or "").strip()
        and len(recent_fallback_failures) >= 3
        and since_last_event_ms < (_INNER_STREAM_CALM_INTERVAL_MS * 2)
    ):
        return

    event = {
        "ts_ms": int(now_ms),
        "focus": str(focus or "self"),
        "source": str(source or "fallback"),
        "absence_band": absence_band,
        "offline_hours": round(max(0.0, offline_hours), 3),
        "loneliness": round(float(state.loneliness or 0.0), 3),
        "anticipation": round(float(state.anticipation or 0.0), 3),
        "note": note,
    }
    if fallback_reason:
        event["fallback_reason"] = str(fallback_reason)
    stream = list(state.inner_stream or [])
    stream.append(event)
    if len(stream) > _MAX_INNER_STREAM_EVENTS:
        stream = stream[-_MAX_INNER_STREAM_EVENTS:]
    state.inner_stream = stream
    state.last_spontaneous_ts_ms = int(now_ms)
    state.last_spontaneous_note = note
    state.last_spontaneous_source = str(source or "fallback")
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
