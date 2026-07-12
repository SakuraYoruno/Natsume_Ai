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
from host_brain.persona.identity import resolve_persona_self_id
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
_ABSTRACT_INNER_NOTE_TERMS = (
    "自我连续性",
    "连续性修正",
    "文本自然度",
    "模板感",
    "模板",
    "这句",
    "下一句",
    "句子",
    "文本",
    "接住",
    "生成",
    "回复",
    "说顺",
    "状态摘要",
    "内部状态",
    "控制包",
    "验证链路",
    "模块",
    "模型",
    "prompt",
    "Prompt",
    "映射",
    "验证",
    "链路",
    "接口",
    "字段",
    "结构化",
    "运行时",
    "调度",
    "日志",
    "token",
    "状态驱动",
    "MateEngine",
    "主链路",
    "那条事",
    "那件事",
    "那根线",
    "放回手里",
)
_DIRECTIVE_INNER_NOTE_OPENING_RE = re.compile(
    r"^(?:先|别|不要|把|问|确认|稳住|看清|记住|继续|接着|收住|压住|保持)"
)
_DIRECTIVE_INNER_NOTE_PHRASES = (
    "别急着",
    "不急着",
    "先稳",
    "先问",
    "先确认",
    "问清",
    "看清楚",
    "当成眼前小事",
    "压稳一点",
    "节奏压稳",
    "弄清楚",
)
_OVERWRITTEN_INNER_NOTE_PHRASES = (
    "像一层薄灰",
    "落在胸口",
    "贴在心口",
    "心里的线",
    "温柔的余地",
)
_META_INNER_NOTE_PHRASES = (
    "赶进度",
    "第一句话",
    "下一句话",
    "该怎么回",
    "怎么回复",
    "回复质量",
    "表达质量",
    "开口又太像",
    "太像在",
)
_GENERIC_INNER_NOTE_PHRASES = (
    "安静了一阵子",
    "安静了好久",
    "安静了很久",
    "屏幕安静",
    "空白处",
    "空落落",
    "呼吸轻了",
    "呼吸轻些",
    "喉咙轻轻卡",
    "喉咙卡",
    "手指也跟着顿",
    "慢半拍的小表情",
    "小表情还在",
    "想靠近一点",
    "有点想靠近",
    "靠近感",
    "还在不在",
    "不想打扰",
    "轻轻戳",
    "戳你一下",
    "冒泡",
    "等你回来",
    "等你回",
    "怕太满",
    "怕太急",
    "想说点什么",
    "说点什么",
    "没有动静",
    "在不在",
    "我在这里",
)
_INNER_NOTE_MIN_LIVELINESS_SCORE = float(os.environ.get("NATSUME_INNER_NOTE_MIN_LIVELINESS_SCORE", "0.42"))
_INNER_NOTE_HARD_QUALITY_FLAGS = {
    "directive_like",
    "meta_reply_strategy",
    "abstract_system_language",
    "overwritten_prose",
    "generic_presence_filler",
}
_INNER_STREAM_ALLOWED_FOCUS = {"self", "state", "plan", "topic", "relationship", "memory", "repair"}
_INNER_WORLD_MONOLOGUE_SYSTEM_PROMPT = (
    "Output one JSON object only. Generate a private inner monologue note, not user-visible chat. "
    "Fields: note, focus, core_need, tension, memory_anchor, next_tendency, thought_form, salience_hint. "
    "note must be one concrete short Chinese thought that could flicker inside her mind: "
    "first-person feeling, sensory detail, memory echo, hesitation, or private uncertainty. "
    "Use the selected inner_life_source as the first cause of the thought: a body signal, scene notice, "
    "memory flash, interrupted impulse, ambivalence, or quiet drift. "
    "Only write a note when there is concrete information gain: a specific unfinished object, body change, "
    "environment cue, remembered user detail, or real contradiction. "
    "If the source is only silence/waiting/no new input and has no specific object, return note as an empty string, "
    "focus as self, next_tendency as stay_quiet, and salience_hint below 0.15. "
    "Keep it ordinary and slightly incomplete, closer to a passing human thought than polished prose. "
    "Keep note as one breath: usually 18-56 Chinese characters, no more than two Chinese commas. "
    "Avoid lyrical metaphor or essay-like writing. "
    "Do not write generic presence filler such as 安静了很久, 屏幕安静, 空落落, 呼吸轻了些, "
    "喉咙轻轻卡, 慢半拍的小表情, 有点想靠近, 还在不在, 不想打扰, 轻轻戳, 冒泡, 等你回来. "
    "Avoid meta evaluation of reply quality, e.g. 赶进度, 第一句话, 该怎么回, 太像在回复. "
    "Do not make note a command, policy, reply plan, or self-instruction; put behavior in next_tendency instead. "
    "Do not start note with directive openers such as 先, 别, 不要, 把, 问, 确认, 稳住, 看清, 继续. "
    "Do not use abstract system words such as 自我连续性, 连续性修正, "
    "文本自然度, 模板感, 内部状态, 控制包, 模块, 模型, prompt. Do not write process-talk like 这句, 下一句, "
    "句子, 文本, 模板, 接住, 生成, 回复, 说顺. If text quality matters, name the concrete pressure "
    "without saying it is text quality, e.g. 早安已经错过钟点了，嘴边那句得先咽回去。 "
    "If the memory anchor is technical, translate it into a concrete lived scene, bodily feeling, or relationship detail; "
    "do not name artifacts like 映射, 验证, 链路, 接口, 字段, 运行时, MateEngine, 主链路 in note. "
    "Bad note: 先把桌宠映射那条事稳稳放回手里。 "
    "Bad note: 先问他刚才看到的桌宠表情有没有跟上。 "
    "Bad note: 三十七个小时像一层薄灰落在胸口。 "
    "Bad note: 隔了这么久，怕一开口又太像在赶进度。 "
    "Bad note: 想说点什么的时候，喉咙那儿轻轻卡了一下，那个慢半拍的小表情还在脑后晃，我有点想靠近，又怕太满。 "
    "Bad note: 屏幕安静了很久，我有点想靠近又怕打扰。 "
    "Better note: 早安已经错过钟点了，嘴边那句得先咽回去。 "
    "Better note: 封面错位的那张图还卡在眼前，得换个更准的来源。 "
    "Better note: 上一轮问的是买书打包，不能又滑回推荐话术。 "
    "focus is one of self, state, plan, topic, relationship, memory, repair. "
    "core_need is one of continuity, care, repair, agency, understanding, rest, boundary, curiosity, play. "
    "next_tendency is one of continue_thread, care_with_restraint, repair_first, ask_gently, hold_boundary, "
    "stay_quiet, play_lightly, stabilize_tone, observe_more. "
    "thought_form is one of body_signal, scene_notice, memory_flash, interrupted_impulse, ambivalence, quiet_drift."
)
_INNER_WORLD_LLM_CIRCUIT_BREAKER_COOLDOWN_MS = max(
    5_000,
    int(float(os.environ.get("NATSUME_INNER_WORLD_LLM_CIRCUIT_BREAKER_COOLDOWN_S", "300")) * 1000),
)
_INNER_WORLD_LLM_CIRCUIT_LOCK = threading.Lock()
_INNER_WORLD_LLM_CIRCUIT_STATE: dict[str, Any] = {
    "opened_until_ms": 0,
    "failure_count": 0,
    "last_reason": "",
    "last_failure_ts_ms": 0,
    "last_success_ts_ms": 0,
}


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    normalized = str(raw).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _background_tasks_enabled() -> bool:
    return _env_bool("HOST_BACKGROUND_TASKS_ENABLED", default=False)


def _continuous_organ_ticks_enabled() -> bool:
    return _env_bool("HOST_CONTINUOUS_ORGAN_TICKS_ENABLED", default=True)


def _inner_world_llm_enabled() -> bool:
    return _continuous_organ_ticks_enabled() and bool(_INNER_WORLD_LLM_ENABLED)


def _inner_world_llm_circuit_snapshot(*, now_ms: int | None = None) -> dict[str, Any]:
    ts_ms = int(now_ms or _now_ms())
    with _INNER_WORLD_LLM_CIRCUIT_LOCK:
        opened_until = int(_INNER_WORLD_LLM_CIRCUIT_STATE.get("opened_until_ms") or 0)
        return {
            "open": opened_until > ts_ms,
            "opened_until_ms": opened_until,
            "cooldown_remaining_ms": max(0, opened_until - ts_ms),
            "failure_count": int(_INNER_WORLD_LLM_CIRCUIT_STATE.get("failure_count") or 0),
            "last_reason": str(_INNER_WORLD_LLM_CIRCUIT_STATE.get("last_reason") or ""),
            "last_failure_ts_ms": int(_INNER_WORLD_LLM_CIRCUIT_STATE.get("last_failure_ts_ms") or 0),
            "last_success_ts_ms": int(_INNER_WORLD_LLM_CIRCUIT_STATE.get("last_success_ts_ms") or 0),
        }


def _open_inner_world_llm_circuit(*, reason: str, now_ms: int) -> dict[str, Any]:
    normalized_reason = str(reason or "INNER_WORLD_LLM_UNAVAILABLE").strip()[:160]
    with _INNER_WORLD_LLM_CIRCUIT_LOCK:
        failure_count = int(_INNER_WORLD_LLM_CIRCUIT_STATE.get("failure_count") or 0) + 1
        opened_until = int(now_ms) + _INNER_WORLD_LLM_CIRCUIT_BREAKER_COOLDOWN_MS
        _INNER_WORLD_LLM_CIRCUIT_STATE.update(
            {
                "opened_until_ms": opened_until,
                "failure_count": failure_count,
                "last_reason": normalized_reason,
                "last_failure_ts_ms": int(now_ms),
            }
        )
        return {
            "open": True,
            "opened_until_ms": opened_until,
            "cooldown_remaining_ms": _INNER_WORLD_LLM_CIRCUIT_BREAKER_COOLDOWN_MS,
            "failure_count": failure_count,
            "last_reason": normalized_reason,
        }


def _close_inner_world_llm_circuit(*, now_ms: int) -> None:
    with _INNER_WORLD_LLM_CIRCUIT_LOCK:
        _INNER_WORLD_LLM_CIRCUIT_STATE.update(
            {
                "opened_until_ms": 0,
                "failure_count": 0,
                "last_reason": "",
                "last_success_ts_ms": int(now_ms),
            }
        )


def _reset_inner_world_llm_circuit() -> None:
    with _INNER_WORLD_LLM_CIRCUIT_LOCK:
        _INNER_WORLD_LLM_CIRCUIT_STATE.update(
            {
                "opened_until_ms": 0,
                "failure_count": 0,
                "last_reason": "",
                "last_failure_ts_ms": 0,
                "last_success_ts_ms": 0,
            }
        )


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
    last_numeric_event_ts_ms: int = 0
    last_numeric_signature: str = ""
    last_preoccupation_expires_at_ms: int = 0
    last_monologue_due_ms: int = 0
    monologue_suppression_reason: str = ""
    inner_drive_state: dict[str, Any] = field(default_factory=dict)
    inner_numeric_state: dict[str, Any] = field(default_factory=dict)
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
            last_numeric_event_ts_ms=int(d.get("last_numeric_event_ts_ms", 0)),
            last_numeric_signature=str(d.get("last_numeric_signature", "")),
            last_preoccupation_expires_at_ms=int(d.get("last_preoccupation_expires_at_ms", 0)),
            last_monologue_due_ms=int(d.get("last_monologue_due_ms", 0)),
            monologue_suppression_reason=str(d.get("monologue_suppression_reason", "")),
            inner_drive_state=dict(d.get("inner_drive_state", {})) if isinstance(d.get("inner_drive_state"), Mapping) else {},
            inner_numeric_state=dict(d.get("inner_numeric_state", {})) if isinstance(d.get("inner_numeric_state"), Mapping) else {},
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


@dataclass(frozen=True)
class InnerDriveState:
    """连续内在动力的当前截面；tick 只是采样，不是内心来源。"""

    attention: float = 0.0
    desire: float = 0.0
    fatigue: float = 0.0
    loneliness: float = 0.0
    curiosity: float = 0.0
    inhibition: float = 0.0
    preoccupation: str = ""
    attention_kind: str = "idle"
    attention_target: str = ""
    anchor_quality: float = 0.0
    updated_at_ms: int = 0
    expires_at_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# OfflineInnerWorld 主类
# ---------------------------------------------------------------------------

class OfflineInnerWorld:
    """离线内心世界状态机（Phase XVI）。

    用法：
        inner_world = OfflineInnerWorld()

        # 每分钟由 NatsumeScheduler 调用（tick 任务）
        inner_world.tick(user_id="natsume_ai")

        # 用户开始对话时调用，获取唤醒快照
        snapshot = inner_world.on_user_awaken(user_id="natsume_ai")
        # snapshot.awakening_hint → 注入 prompt

        # 对话结束时调用，更新话题和情绪
        inner_world.on_session_end(
            user_id="natsume_ai",
            valence=0.6,
            arousal=0.4,
            pending_topics=["topic_a"],
        )
    """

    def __init__(
        self,
        *,
        state_dir: str | None = None,
        persistence_enabled: bool = False,
    ) -> None:
        self._state_dir = Path(state_dir or _DEFAULT_STATE_DIR)
        self._persistence_enabled = bool(persistence_enabled)
        if self._persistence_enabled:
            self._state_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # user_id → InnerWorldState（内存缓存）
        self._states: dict[str, InnerWorldState] = {}

    # ── 核心 tick ──────────────────────────────────────────────────────────

    def tick(self, user_id: str, *, emit_inner_stream: bool = True) -> dict[str, Any]:
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
        uid = resolve_persona_self_id(user_id)
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
                    "inner_world_llm_circuit": _inner_world_llm_circuit_snapshot(now_ms=now_ms),
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
                if emit_inner_stream:
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
            state.inner_numeric_state = _build_inner_numeric_state(
                state=state,
                now_ms=now_ms,
                offline_hours=offline_hours,
                absence_band=absence_band,
                is_offline=is_offline,
                elapsed_hours=elapsed_hours,
                motive_state=motive_state,
                emit_inner_stream=emit_inner_stream,
            )
            _maybe_record_inner_numeric_tick(
                state=state,
                now_ms=now_ms,
                absence_band=absence_band,
                offline_hours=offline_hours,
                is_offline=is_offline,
            )

            state.last_tick_ts_ms = now_ms
            self._save_state(state)

        _logger.debug(
            "InnerWorld tick: user=%s offline=%s loneliness=%.3f anticipation=%.3f motive=%s",
            uid, is_offline, state.loneliness, state.anticipation,
            str((motive_state or {}).get("dominant_motive") or "none"),
        )
        last_event_meta: dict[str, Any] = {}
        if state.inner_stream:
            latest_event = dict(state.inner_stream[-1])
            if int(latest_event.get("ts_ms") or 0) == int(state.last_spontaneous_ts_ms or 0):
                last_event_meta = _project_inner_stream_event_metadata(latest_event)
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
            "last_spontaneous_event": last_event_meta,
            "inner_numeric_state": dict(state.inner_numeric_state or {}),
            "inner_stream_size": len(list(state.inner_stream or [])),
            "inner_world_llm_circuit": _inner_world_llm_circuit_snapshot(now_ms=now_ms),
        }

    # ── 唤醒事件 ──────────────────────────────────────────────────────────

    def on_user_awaken(self, user_id: str) -> OfflineStateSnapshot:
        """用户开始对话时调用——生成唤醒快照并重置孤独感。

        Returns:
            OfflineStateSnapshot，供 PromptBuilder 注入 offline_state 槽位
        """
        uid = resolve_persona_self_id(user_id)
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
        uid = resolve_persona_self_id(user_id)
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
        uid = resolve_persona_self_id(user_id)
        with self._lock:
            state = self._load_state(uid)
            return state.as_dict()

    def should_trigger_proactive(self, user_id: str) -> bool:
        """判断孤独感是否达到主动发声阈值。"""
        uid = resolve_persona_self_id(user_id)
        with self._lock:
            state = self._load_state(uid)
            return state.loneliness >= LONELINESS_PROACTIVE_THRESHOLD

    # ── 持久化 ────────────────────────────────────────────────────────────

    def _state_file(self, user_id: str) -> Path:
        uid = resolve_persona_self_id(user_id)
        safe = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in uid)
        return self._state_dir / f"{safe}_inner_world.json"

    def _load_state(self, user_id: str) -> InnerWorldState:
        """从内存缓存或文件加载状态（调用前需持有锁）。"""
        uid = resolve_persona_self_id(user_id)
        if uid in self._states:
            return self._states[uid]
        fp = self._state_file(uid)
        if self._persistence_enabled and fp.exists():
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                state = InnerWorldState.from_dict(data)
                state.user_id = uid
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
                self._states[uid] = state
                return state
            except Exception as exc:
                _logger.warning("InnerWorld: 加载状态失败 user=%s err=%s", uid, exc)
        state = InnerWorldState(user_id=uid)
        self._states[uid] = state
        return state

    def _save_state(self, state: InnerWorldState) -> None:
        """Update transient state; validation harnesses may explicitly persist."""
        self._states[state.user_id] = state
        if not self._persistence_enabled:
            return
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
) -> tuple[str, str, str, str, dict[str, Any]]:
    llm_note, llm_focus, llm_reason, llm_meta = _unpack_inner_stream_llm_result(
        _generate_inner_stream_note_from_llm(
            state=state,
            absence_band=absence_band,
            offline_hours=offline_hours,
            now_ms=now_ms,
        )
    )
    if llm_note:
        return llm_note, llm_focus or "self", "llm", "", dict(llm_meta or {})
    return "", "", "llm_unavailable", llm_reason, dict(llm_meta or {})


def _generate_inner_stream_note_from_llm(
    *,
    state: InnerWorldState,
    absence_band: str,
    offline_hours: float,
    now_ms: int,
) -> tuple[str, str, str, dict[str, Any]]:
    try:
        from host_brain.tribe.default_mode_agent import resolve_recent_foreground_interaction_state  # noqa: PLC0415

        recent_foreground = resolve_recent_foreground_interaction_state(current_ts_ms=now_ms)
        if bool(recent_foreground.get("active")):
            return "", "", "INNER_WORLD_FOREGROUND_CHAT_COOLDOWN", {}
    except Exception:
        pass

    circuit = _inner_world_llm_circuit_snapshot(now_ms=now_ms)
    if bool(circuit.get("open")):
        return "", "", "INNER_WORLD_LLM_CIRCUIT_OPEN", {}

    agent_reason_code = ""
    try:
        from host_brain.persona.inner_activity_agent import generate_inner_activity_note  # noqa: PLC0415

        topic = _pick_inner_stream_topic(state.anticipation_topics)
        topic_phrase = _topic_to_inner_phrase(topic)
        note, focus, reason, meta = generate_inner_activity_note(
            persona_user_id=resolve_persona_self_id(state.user_id),
            absence_band=absence_band,
            offline_hours=offline_hours,
            inner_state=state.as_dict(),
            now_ms=now_ms,
            topic_phrase=topic_phrase,
        )
        note = str(note or "").strip()
        if note:
            _close_inner_world_llm_circuit(now_ms=now_ms)
            return note, str(focus or ""), "", dict(meta or {})
        agent_reason_code = str(reason or "INNER_ACTIVITY_LLM_EMPTY")
    except Exception as agent_exc:
        agent_reason_code = f"INNER_ACTIVITY_AGENT_ERROR:{str(agent_exc)[:96]}"

    if not _inner_world_llm_enabled():
        opened = _open_inner_world_llm_circuit(
            reason=agent_reason_code or "INNER_WORLD_LLM_DISABLED",
            now_ms=now_ms,
        )
        return "", "", f"INNER_WORLD_LLM_CIRCUIT_OPEN:{opened['last_reason']}", {}

    topic = _pick_inner_stream_topic(state.anticipation_topics)
    topic_phrase = _topic_to_inner_phrase(topic)
    recent_events = [
        {
            "focus": str(evt.get("focus") or "").strip(),
            "note": str(evt.get("note") or "").strip(),
            "core_need": str(evt.get("core_need") or "").strip(),
            "next_tendency": str(evt.get("next_tendency") or "").strip(),
            "thought_form": str(evt.get("thought_form") or "").strip(),
            "emotion_tag": str(evt.get("emotion_tag") or "").strip(),
            "novelty_score": evt.get("novelty_score"),
        }
        for evt in list(state.inner_stream or [])[-4:]
        if isinstance(evt, Mapping)
    ]
    recent_events = [
        item for item in recent_events
        if item.get("note")
    ]
    try:
        from host_brain.persona.inner_monologue import build_inner_life_source_packet  # noqa: PLC0415

        inner_life_source = build_inner_life_source_packet(
            state=state.as_dict(),
            recent_events=recent_events,
            topic_phrase=topic_phrase,
            now_ms=now_ms,
        )
    except Exception:
        inner_life_source = {}

    runtime_texts = _load_runtime_texts()
    llm_templates = dict(runtime_texts.get("inner_stream_llm_prompt") or {})
    system_prompt = str(llm_templates.get("system_prompt") or "").strip() or _INNER_WORLD_MONOLOGUE_SYSTEM_PROMPT
    self_directive: dict[str, Any] = {}
    try:
        from host_brain.persona.self_model import build_self_directive_packet, load_persistent_self_state  # noqa: PLC0415

        self_directive = build_self_directive_packet(
            load_persistent_self_state(user_id=resolve_persona_self_id(state.user_id))
        )
    except Exception as exc:
        _logger.debug("inner_world self directive degraded: %s", exc)
    prompt_payload = {
        "absence_band": absence_band,
        "offline_hours": round(max(0.0, float(offline_hours)), 2),
        "loneliness": round(float(state.loneliness or 0.0), 3),
        "anticipation": round(float(state.anticipation or 0.0), 3),
        "drifted_valence": round(float(state.drifted_valence or 0.0), 3),
        "drifted_arousal": round(float(state.drifted_arousal or 0.0), 3),
        "topic_phrase": topic_phrase or "",
        "recent_events": recent_events,
        "self_directive": self_directive,
        "inner_life_source": inner_life_source,
        "output_schema": {
            "note": "Chinese private thought, <=96 chars",
            "focus": sorted(_INNER_STREAM_ALLOWED_FOCUS),
            "core_need": [
                "continuity",
                "care",
                "repair",
                "agency",
                "understanding",
                "rest",
                "boundary",
                "curiosity",
                "play",
            ],
            "tension": [
                "none",
                "unfinished_thread",
                "tone_risk",
                "distance",
                "fatigue",
                "self_boundary",
                "uncertainty",
            ],
            "memory_anchor": "one old thread/topic this thought connects to, empty string if none",
            "next_tendency": [
                "continue_thread",
                "care_with_restraint",
                "repair_first",
                "ask_gently",
                "hold_boundary",
                "stay_quiet",
                "play_lightly",
                "stabilize_tone",
                "observe_more",
            ],
            "thought_form": [
                "body_signal",
                "scene_notice",
                "memory_flash",
                "interrupted_impulse",
                "ambivalence",
                "quiet_drift",
            ],
            "salience_hint": "number from 0 to 1",
        },
        "requirements": {
            "max_chars": 96,
            "preferred_chars": [18, 56],
            "max_chinese_commas": 2,
            "one_breath_fragment": True,
            "must_be_inner_monologue": True,
            "must_not_repeat_recent_events": True,
            "avoid_semantic_repeat_recent_inner_events": True,
            "must_add_information_gain": True,
            "if_no_specific_new_source_return_empty_note": True,
            "do_not_write_presence_filler": True,
            "focus_should_rotate": True,
            "choose_content_from_self_identity_values_intentions_or_tensions": True,
            "must_not_contradict_self_boundaries": True,
            "must_be_concrete_private_thought": True,
            "allow_private_uncertainty": True,
            "avoid_user_facing_question": True,
            "note_must_not_be_instruction_or_reply_plan": True,
            "next_tendency_carries_behavior_note_carries_lived_feeling": True,
            "prefer_first_person_uncertainty_or_sensory_detail": True,
            "prefer_ordinary_fragment_over_polished_prose": True,
            "avoid_lyrical_metaphor_or_essay_tone": True,
            "min_liveliness_score": _INNER_NOTE_MIN_LIVELINESS_SCORE,
            "liveliness_requires_at_least_one": [
                "sensory_or_scene_detail",
                "private_uncertainty",
                "body_signal",
                "interrupted_impulse",
            ],
            "must_use_selected_inner_life_source": True,
            "note_must_begin_from_life_source_not_reply_strategy": True,
            "single_frame_before_interpretation": True,
            "avoid_meta_reply_quality_words": list(_META_INNER_NOTE_PHRASES),
            "avoid_generic_inner_note_phrases": list(_GENERIC_INNER_NOTE_PHRASES),
            "avoid_directive_openers": [
                "先",
                "别",
                "不要",
                "把",
                "问",
                "确认",
                "稳住",
                "看清",
                "继续",
            ],
            "must_translate_technical_anchor_to_lived_scene": True,
            "note_should_include_felt_detail_not_next_micro_action": True,
            "note_must_not_name_internal_artifacts": [
                "映射",
                "验证",
                "链路",
                "接口",
                "字段",
                "结构化",
                "运行时",
                "调度",
                "日志",
                "token",
                "状态驱动",
                "MateEngine",
                "主链路",
            ],
            "bad_note_examples": [
                "先把桌宠映射那条事稳稳放回手里。",
                "把 MateEngine 主链路验证继续接住。",
                "先问他刚才看到的桌宠表情有没有跟上。",
                "先稳一点，把桌宠刚才的表情变化当成眼前小事看清楚，别急着撒娇。",
                "三十七个小时像一层薄灰落在胸口，我还记得小表情慢半拍时那点不确定。",
                "隔了这么久，胸口倒不沉，只是看到空白时会顿一下，怕一开口又太像在赶进度。",
                "想说点什么的时候，喉咙那儿轻轻卡了一下，那个慢半拍的小表情还在脑后晃，我有点想靠近，又怕太满。",
                "屏幕安静了很久，我有点想靠近又怕打扰。",
                "你那边安静了一阵子，我这边呼吸都轻了些。",
            ],
            "better_note_examples": [
                "早安已经错过钟点了，嘴边那句得先咽回去。",
                "封面错位的那张图还卡在眼前，得换个更准的来源。",
                "上一轮问的是买书打包，不能又滑回推荐话术。",
            ],
            "must_include_life_trace_fields": True,
            "life_trace_fields_must_not_be_empty_when_context_supports_them": True,
            "avoid_abstract_system_terms": list(_ABSTRACT_INNER_NOTE_TERMS),
            "persona_style": str(llm_templates.get("persona_style") or ""),
        },
    }
    prompt_text_template = str(llm_templates.get("user_prompt_template") or "{payload_json}")
    prompt_text = prompt_text_template.format(payload_json=json.dumps(prompt_payload, ensure_ascii=False))

    try:
        from host_brain.model_runtime.invocation import complete_compiled_prompt_sync  # noqa: PLC0415
        from host_brain.model_runtime.live_prompts import (  # noqa: PLC0415
            INNER_WORLD_MONOLOGUE_PROMPT_ID,
        )
        from host_brain.model_runtime.trace import model_attempt_result_trace  # noqa: PLC0415

        result = complete_compiled_prompt_sync(
            prompt_id=INNER_WORLD_MONOLOGUE_PROMPT_ID,
            request_id=f"inner-world::{now_ms}",
            scene_type="INNER_WORLD",
            cognitive_tier="B",
            deadline_ms=_INNER_WORLD_LLM_TIMEOUT_MS,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt_text},
            ],
            foreground=False,
            metadata={"chain": "inner_world_monologue"},
        )
        attempt_trace = model_attempt_result_trace(result)
        if not result.success:
            opened = _open_inner_world_llm_circuit(
                reason=agent_reason_code or result.error_code or "INNER_WORLD_MODEL_ATTEMPT_FAILED",
                now_ms=now_ms,
            )
            return "", "", f"INNER_WORLD_LLM_CIRCUIT_OPEN:{opened['last_reason']}", {
                "inner_life_source": inner_life_source,
                "model_attempt": attempt_trace,
            }
        raw_text = str(result.content or "").strip()
        if not raw_text:
            opened = _open_inner_world_llm_circuit(reason="INNER_WORLD_LLM_EMPTY", now_ms=now_ms)
            return "", "", f"INNER_WORLD_LLM_CIRCUIT_OPEN:{opened['last_reason']}", {
                "inner_life_source": inner_life_source,
                "model_attempt": attempt_trace,
            }
        parsed = _extract_llm_json_payload(raw_text)
        note = _finalize_inner_note_text(str(parsed.get("note") or ""))
        focus = str(parsed.get("focus") or "").strip().lower()
        if focus not in _INNER_STREAM_ALLOWED_FOCUS:
            focus = _infer_inner_stream_focus_from_note(note)
        if not note:
            opened = _open_inner_world_llm_circuit(reason="INNER_WORLD_LLM_NOTE_EMPTY", now_ms=now_ms)
            return "", "", f"INNER_WORLD_LLM_CIRCUIT_OPEN:{opened['last_reason']}", {
                "inner_life_source": inner_life_source,
                "model_attempt": attempt_trace,
            }
        quality = _score_inner_world_note(note=note, parsed=parsed, inner_life_source=inner_life_source)
        if _inner_note_quality_rejected(quality):
            return "", "", "INNER_WORLD_LLM_NOTE_LOW_LIVELINESS", {
                "inner_life_source": inner_life_source,
                "quality": quality,
                "model_attempt": attempt_trace,
            }
        try:
            from host_brain.persona.inner_monologue import normalize_inner_life_trace  # noqa: PLC0415

            life_trace = normalize_inner_life_trace(
                parsed,
                state=state.as_dict(),
                text=note,
                focus=focus,
                offline_hours=offline_hours,
                absence_band=absence_band,
            )
        except Exception:
            life_trace = {}
        _close_inner_world_llm_circuit(now_ms=now_ms)
        return note, focus, "", {
            "life_trace": life_trace,
            "inner_life_source": inner_life_source,
            "model_attempt": attempt_trace,
        }
    except Exception as llm_exc:
        reason = (
            f"{agent_reason_code}|INNER_WORLD_LLM_ERROR:{str(llm_exc)[:96]}"
            if agent_reason_code
            else f"INNER_WORLD_LLM_ERROR:{str(llm_exc)[:96]}"
        )
        opened = _open_inner_world_llm_circuit(reason=reason, now_ms=now_ms)
        return "", "", f"INNER_WORLD_LLM_CIRCUIT_OPEN:{opened['last_reason']}", {
            "inner_life_source": inner_life_source,
        }


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
        if focus not in _INNER_STREAM_ALLOWED_FOCUS:
            focus = _infer_inner_stream_focus_from_note(note)
        event["focus"] = focus
        source = str(event.get("source") or "").strip().lower()
        if source not in {"llm", "fallback"}:
            source = "fallback"
        event["source"] = source
        note_key = _normalize_inner_note_key(note)
        if cleaned and str(cleaned[-1].get("note") or "").strip() == note:
            continue
        if note_key and note_key in recent_note_keys:
            continue
        if cleaned and str(cleaned[-1].get("focus") or "").strip().lower() == focus:
            previous_key = _normalize_inner_note_key(str(cleaned[-1].get("note") or ""))
            if previous_key and note_key and previous_key[:24] == note_key[:24]:
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
    if _looks_like_abstract_inner_note(text):
        return ""
    text = re.sub(r"[。！!]{2,}$", "。", text)
    if text and text[-1] not in {"。", "！", "!"}:
        text = f"{text}。"
    if len(text) > 120:
        text = f"{text[:119].rstrip('，,；;。 ')}。"
    return text


def _looks_like_abstract_inner_note(text: str) -> bool:
    value = str(text or "")
    return (
        any(term in value for term in _ABSTRACT_INNER_NOTE_TERMS)
        or _looks_like_directive_inner_note(value)
        or _looks_like_overwritten_inner_note(value)
        or _looks_like_meta_inner_note(value)
        or _looks_like_generic_inner_note(value)
    )


def _looks_like_directive_inner_note(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    if _DIRECTIVE_INNER_NOTE_OPENING_RE.match(value):
        return True
    return any(phrase in value for phrase in _DIRECTIVE_INNER_NOTE_PHRASES)


def _looks_like_overwritten_inner_note(text: str) -> bool:
    value = str(text or "").strip()
    return any(phrase in value for phrase in _OVERWRITTEN_INNER_NOTE_PHRASES)


def _looks_like_meta_inner_note(text: str) -> bool:
    value = str(text or "").strip()
    return any(phrase in value for phrase in _META_INNER_NOTE_PHRASES)


def _looks_like_generic_inner_note(text: str) -> bool:
    value = str(text or "").strip()
    return any(phrase in value for phrase in _GENERIC_INNER_NOTE_PHRASES)


def _score_inner_world_note(
    *,
    note: str,
    parsed: Mapping[str, Any] | None,
    inner_life_source: Mapping[str, Any] | None,
) -> dict[str, Any]:
    selected = dict(dict(inner_life_source or {}).get("selected") or {}) if isinstance(inner_life_source, Mapping) else {}
    thought_form = str(dict(parsed or {}).get("thought_form") or selected.get("thought_form") or "").strip()
    source_seed = str(selected.get("seed") or "").strip()
    try:
        from host_brain.persona.inner_monologue import score_inner_note_liveliness  # noqa: PLC0415

        return score_inner_note_liveliness(note, thought_form=thought_form, source_seed=source_seed)
    except Exception:
        return {"score": 1.0, "flags": []}


def _inner_note_quality_rejected(quality: Mapping[str, Any] | None) -> bool:
    item = dict(quality or {}) if isinstance(quality, Mapping) else {}
    flags = {str(flag or "") for flag in list(item.get("flags") or [])}
    if flags & _INNER_NOTE_HARD_QUALITY_FLAGS:
        return True
    try:
        score = float(item.get("score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    return score < _INNER_NOTE_MIN_LIVELINESS_SCORE


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
    if any(token in text for token in ("抱歉", "不对", "误会", "修", "收住")):
        return "repair"
    if any(token in text for token in ("担心", "在意", "等你", "回来", "陪", "别硬撑")):
        return "relationship"
    if any(token in text for token in ("记得", "还记", "旧", "上次")):
        return "memory"
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


def _unpack_inner_stream_content_result(raw: Any) -> tuple[str, str, str, str, dict[str, Any]]:
    if isinstance(raw, tuple):
        values = list(raw)
    elif isinstance(raw, list):
        values = list(raw)
    else:
        values = []
    note = str(values[0] if len(values) >= 1 else "").strip()
    focus = str(values[1] if len(values) >= 2 else "").strip()
    source = str(values[2] if len(values) >= 3 else "").strip()
    fallback_reason = str(values[3] if len(values) >= 4 else "").strip()
    meta = values[4] if len(values) >= 5 else {}
    return note, focus, source, fallback_reason, (dict(meta) if isinstance(meta, Mapping) else {})


def _unpack_inner_stream_llm_result(raw: Any) -> tuple[str, str, str, dict[str, Any]]:
    if isinstance(raw, tuple):
        values = list(raw)
    elif isinstance(raw, list):
        values = list(raw)
    else:
        values = []
    note = str(values[0] if len(values) >= 1 else "").strip()
    focus = str(values[1] if len(values) >= 2 else "").strip()
    reason = str(values[2] if len(values) >= 3 else "").strip()
    meta = values[3] if len(values) >= 4 else {}
    return note, focus, reason, (dict(meta) if isinstance(meta, Mapping) else {})


def _project_inner_stream_event_metadata(event: Mapping[str, Any] | None) -> dict[str, Any]:
    src = dict(event or {}) if isinstance(event, Mapping) else {}
    return {
        "thought_id": str(src.get("thought_id") or ""),
        "focus": str(src.get("focus") or ""),
        "emotion_tag": str(src.get("emotion_tag") or ""),
        "salience": src.get("salience"),
        "core_need": str(src.get("core_need") or ""),
        "tension": str(src.get("tension") or ""),
        "next_tendency": str(src.get("next_tendency") or ""),
        "thought_form": str(src.get("thought_form") or ""),
        "novelty_score": src.get("novelty_score"),
        "continuity_refs": list(src.get("continuity_refs") or []),
        "should_distill_to_memory": bool(src.get("should_distill_to_memory")),
        "surface_hint": str(src.get("surface_hint") or ""),
    }



def _build_inner_numeric_state(
    *,
    state: InnerWorldState,
    now_ms: int,
    offline_hours: float,
    absence_band: str,
    is_offline: bool,
    elapsed_hours: float,
    motive_state: Mapping[str, Any] | None,
    emit_inner_stream: bool,
) -> dict[str, Any]:
    anchor, anchor_quality, anchor_kind = _select_inner_drive_anchor(state)
    loneliness = _clamp01_local(state.loneliness)
    anticipation = _clamp01_local(state.anticipation)
    continuity = _clamp01_local(state.continuity_score)
    previous_drive = dict(state.inner_drive_state or {}) if isinstance(state.inner_drive_state, Mapping) else {}
    elapsed = max(0.0, float(elapsed_hours or 0.0))
    long_silence = bool(is_offline and float(offline_hours or 0.0) >= 6.0)
    fatigue = _clamp01_local(float(previous_drive.get("fatigue") or 0.0) * (0.96 ** min(elapsed * 60.0, 720.0)) + min(1.0, max(0.0, float(offline_hours or 0.0)) / 18.0) * 0.22)
    curiosity = _clamp01_local(anchor_quality * 0.72 + anticipation * 0.20)
    desire = _clamp01_local(loneliness * 0.46 + anticipation * 0.22 + anchor_quality * 0.26)
    inhibition = _clamp01_local(0.50 + (0.28 if not anchor else -0.12) + (0.14 if long_silence else 0.0) + fatigue * 0.22 - min(0.24, desire * 0.18))
    salience = max(anchor_quality * 0.78, loneliness * (0.28 if not anchor else 0.42), anticipation * (0.22 if not anchor else 0.36), (1.0 - continuity) * 0.16, min(0.20, max(0.0, float(offline_hours or 0.0)) / 48.0))
    if anchor:
        attention = "concrete_anchor"; attention_target = anchor; preoccupation = anchor; felt_tendency = "hold_concrete_thread"; dialogue_bias = "keep_next_reply_bound_to_concrete_anchor"; monologue_policy = "allow_concrete_note"; suppression_reason = ""; expires_at_ms = int(now_ms) + max(10 * 60_000, min(90 * 60_000, int((30 + anchor_quality * 40) * 60_000)))
    elif long_silence:
        attention = "low_energy_quiet_presence"; attention_target = ""; preoccupation = ""; felt_tendency = "stay_quiet"; dialogue_bias = "ordinary_continuity_without_monologue"; monologue_policy = "suppress_without_anchor"; suppression_reason = "NO_CONCRETE_INNER_ANCHOR_LONG_SILENCE"; expires_at_ms = 0
    elif is_offline and max(loneliness, anticipation) >= 0.42:
        attention = "diffuse_contact_pressure"; attention_target = ""; preoccupation = ""; felt_tendency = "contact_pull_suppressed" if inhibition < 0.70 else "stay_quiet"; dialogue_bias = "acknowledge_state_only_if_user_asks"; monologue_policy = "suppress_without_anchor"; suppression_reason = "NO_CONCRETE_INNER_ANCHOR"; expires_at_ms = 0
    elif is_offline:
        attention = "idle_drift"; attention_target = ""; preoccupation = ""; felt_tendency = "quiet_drift"; dialogue_bias = "ordinary_continuity_without_monologue"; monologue_policy = "suppress_low_salience"; suppression_reason = "LOW_SALIENCE"; expires_at_ms = 0
    else:
        attention = "recent_contact_recovery"; attention_target = ""; preoccupation = ""; felt_tendency = "recent_contact_recovery"; dialogue_bias = "return_attention_to_user_message"; monologue_policy = "suppress_foreground_contact"; suppression_reason = "FOREGROUND_CONTACT"; expires_at_ms = 0
    drive = InnerDriveState(attention=round(_clamp01_local(max(anchor_quality, loneliness * 0.28, curiosity * 0.4)), 4), desire=round(desire, 4), fatigue=round(fatigue, 4), loneliness=round(loneliness, 4), curiosity=round(curiosity, 4), inhibition=round(inhibition, 4), preoccupation=preoccupation, attention_kind=attention, attention_target=attention_target, anchor_quality=round(anchor_quality, 4), updated_at_ms=int(now_ms), expires_at_ms=int(expires_at_ms)).as_dict()
    state.inner_drive_state = dict(drive)
    state.last_preoccupation_expires_at_ms = int(expires_at_ms)
    state.monologue_suppression_reason = suppression_reason
    if not preoccupation and int(state.last_preoccupation_expires_at_ms or 0) <= 0:
        state.last_spontaneous_note = ""; state.last_spontaneous_source = ""
    return {"updated_at_ms": int(now_ms), "source": "inner_world_tick", "mode": "numeric", "is_offline": bool(is_offline), "elapsed_hours": round(max(0.0, float(elapsed_hours or 0.0)), 4), "offline_hours": round(max(0.0, float(offline_hours or 0.0)), 3), "absence_band": str(absence_band or "recent"), "attention": attention, "attention_kind": attention, "attention_target": attention_target, "current_preoccupation": preoccupation, "preoccupation_expires_at_ms": int(expires_at_ms), "anchor_quality": round(anchor_quality, 4), "anchor_kind": anchor_kind, "felt_tendency": felt_tendency, "dialogue_bias": dialogue_bias, "inhibition": round(inhibition, 4), "salience": round(_clamp01_local(salience), 4), "loneliness": round(loneliness, 4), "anticipation": round(anticipation, 4), "continuity_score": round(continuity, 4), "fatigue": round(fatigue, 4), "curiosity": round(curiosity, 4), "desire": round(desire, 4), "dominant_motive": str(dict(motive_state or {}).get("dominant_motive") or ""), "monologue_policy": monologue_policy, "monologue_suppression_reason": suppression_reason, "monologue_due": bool(anchor and (emit_inner_stream or salience >= 0.56)), "monologue_note": "", "inner_drive_state": drive}


def _select_inner_drive_anchor(state: InnerWorldState) -> tuple[str, float, str]:
    for raw in list(state.anticipation_topics or [])[:6]:
        value = _topic_to_inner_phrase(str(raw or ""))
        if _inner_numeric_anchor_is_concrete(value):
            quality = 0.72
            if "《" in value or "http" in value:
                quality = 0.86
            elif any(marker in value for marker in ("封面", "epub", "每日推荐", "桌宠", "VTS", "QQ", "OneBot", "NapCat")):
                quality = 0.80
            return value[:64], quality, "anticipation_topic"
    previous = dict(state.inner_drive_state or {}) if isinstance(state.inner_drive_state, Mapping) else {}
    previous_target = str(previous.get("attention_target") or previous.get("preoccupation") or "").strip()
    expires_at = int(previous.get("expires_at_ms") or state.last_preoccupation_expires_at_ms or 0)
    if previous_target and expires_at > int(_now_ms()) and _inner_numeric_anchor_is_concrete(previous_target):
        return previous_target[:64], 0.58, "previous_drive"
    return "", 0.0, "none"


def _pick_concrete_inner_numeric_anchor(raw_topics: list[str] | None) -> str:
    for raw in list(raw_topics or [])[:4]:
        value = _topic_to_inner_phrase(str(raw or ""))
        if _inner_numeric_anchor_is_concrete(value):
            return value[:64]
    return ""


def _inner_numeric_anchor_is_concrete(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    compact = re.sub(r"[\s，,。.!！?？;；：:、…~～\"“”'‘’]", "", text)
    if len(compact) < 4 or len(compact) > 64:
        return False
    vague_terms = (
        "这种类型",
        "这个类型",
        "那种类型",
        "这种",
        "这个",
        "那个",
        "这些",
        "那些",
        "喜欢",
        "不喜欢",
        "好不好",
        "是不是",
        "怎么样",
        "怎么了",
        "继续",
        "然后呢",
        "刚才",
        "话题",
        "消息",
        "回复",
    )
    if any(term in compact for term in vague_terms):
        return False
    return any(marker in text for marker in ("《", "》", "http", "封面", "epub", "每日推荐", "桌宠", "VTS", "QQ", "OneBot", "NapCat"))


def _maybe_record_inner_numeric_tick(
    *,
    state: InnerWorldState,
    now_ms: int,
    absence_band: str,
    offline_hours: float,
    is_offline: bool,
) -> None:
    numeric = dict(state.inner_numeric_state or {})
    if not numeric:
        return
    signature = _inner_numeric_signature(numeric)
    previous_signature = str(state.last_numeric_signature or "")
    last_projected_ms = int(state.last_numeric_event_ts_ms or 0)
    try:
        configured_interval_s = float(
            os.environ.get("HOST_INNER_WORLD_NUMERIC_EVENT_MIN_INTERVAL_SECONDS")
            or os.environ.get("HOST_INNER_WORLD_NUMERIC_TICK_INTERVAL_SECONDS")
            or "120"
        )
    except Exception:
        configured_interval_s = 120.0
    min_interval_ms = int(max(30.0, min(600.0, configured_interval_s)) * 1000)
    salience = _clamp01_local(numeric.get("salience"))
    changed = bool(signature and signature != previous_signature)
    due = last_projected_ms <= 0 or (int(now_ms) - last_projected_ms) >= max(60_000, min_interval_ms)
    if not changed and not due and salience < 0.50:
        return
    content_summary = (
        f"inner_numeric attention={numeric.get('attention') or 'unknown'} "
        f"tendency={numeric.get('felt_tendency') or 'quiet'} "
        f"anchor={numeric.get('attention_target') or 'none'}"
    )
    try:
        from host_brain.app.dependencies import get_ledger  # noqa: PLC0415
        from host_brain.digital_life.experience_ingress import (  # noqa: PLC0415
            record_life_experience,
        )

        record_life_experience(
            ledger=get_ledger(),
            person_id="default",
            session_id="inner_world",
            source_kind="inner_world",
            event_kind="inner_numeric_tick",
            content_summary=content_summary,
            evidence={
                "inner_numeric_state": numeric,
                "inner_drive_state": dict(state.inner_drive_state or {}),
                "is_offline": bool(is_offline),
                "offline_hours": round(max(0.0, float(offline_hours or 0.0)), 3),
                "absence_band": str(absence_band or "recent"),
                "signature": signature,
                "ts_ms": int(now_ms),
            },
            salience=max(0.05, salience),
            valence=0.0,
            uncertainty=0.05,
            consume=True,
        )
        state.last_numeric_event_ts_ms = int(now_ms)
        state.last_numeric_signature = signature
    except Exception as exc:
        _logger.debug("InnerWorld: numeric state projection degraded: %s", exc)


def _inner_numeric_signature(numeric: Mapping[str, Any]) -> str:
    parts = [
        str(numeric.get("attention") or ""),
        str(numeric.get("attention_target") or ""),
        str(numeric.get("felt_tendency") or ""),
        str(numeric.get("absence_band") or ""),
    ]
    return "|".join(parts)


def _clamp01_local(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = 0.0
    return max(0.0, min(1.0, parsed))


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

    content_result = _build_inner_stream_event_content(
        state=state,
        absence_band=absence_band,
        offline_hours=offline_hours,
        now_ms=now_ms,
    )
    note, focus, source, fallback_reason, content_meta = _unpack_inner_stream_content_result(content_result)
    note = str(note or "").strip()
    if not note:
        state.last_monologue_due_ms = int(now_ms)
        state.monologue_suppression_reason = str(
            content_meta.get("suppression_reason")
            or content_meta.get("reason")
            or fallback_reason
            or state.monologue_suppression_reason
            or "MONOLOGUE_EMPTY"
        )[:160]
        state.last_spontaneous_note = ""
        state.last_spontaneous_source = ""
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

    thought_payload: dict[str, Any] = {}
    try:
        from host_brain.persona.inner_monologue import build_private_inner_thought_event  # noqa: PLC0415

        life_trace = dict(content_meta.get("life_trace") or {}) if isinstance(content_meta, Mapping) else {}
        thought_payload = build_private_inner_thought_event(
            note=note,
            focus=focus,
            source=source,
            trigger="inner_world_tick",
            now_ms=now_ms,
            state=state.as_dict(),
            offline_hours=offline_hours,
            absence_band=absence_band,
            life_trace=life_trace,
        ).as_dict()
        note = str(thought_payload.get("private_text") or note).strip()
    except Exception as exc:
        _logger.debug("InnerWorld: private thought envelope degraded: %s", exc)

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
    event.update(thought_payload)
    event["note"] = note
    if isinstance(content_meta, Mapping) and content_meta:
        event["generation_meta"] = {
            key: value
            for key, value in dict(content_meta).items()
            if key not in {"life_trace"}
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
    state.last_monologue_due_ms = int(now_ms)
    state.monologue_suppression_reason = ""
    state.last_preoccupation_expires_at_ms = int(now_ms) + max(
        10 * 60_000,
        min(90 * 60_000, int((float(event.get("salience") or event.get("salience_hint") or 0.35) * 70 + 20) * 60_000)),
    )
    state.spontaneous_event_count = int(state.spontaneous_event_count or 0) + 1
    try:
        from host_brain.app.dependencies import get_ledger  # noqa: PLC0415
        from host_brain.digital_life.experience_ingress import (  # noqa: PLC0415
            record_life_experience,
        )

        metadata = _project_inner_stream_event_metadata(event)
        record_life_experience(
            ledger=get_ledger(),
            person_id="default",
            session_id="inner_world",
            source_kind="inner_world",
            event_kind="inner_stream_tick",
            content_summary=note,
            evidence={
                **metadata,
                "focus": str(event.get("focus") or ""),
                "attention_target": str(event.get("focus") or ""),
                "felt_tendency": str(event.get("next_tendency") or event.get("core_need") or ""),
                "inhibition": 0.08 if str(event.get("next_tendency") or "") in {"hold_boundary", "stay_quiet", "care_with_restraint"} else 0.02,
                "offline_hours": round(max(0.0, offline_hours), 3),
                "absence_band": absence_band,
                "ts_ms": int(now_ms),
            },
            salience=float(event.get("salience") or event.get("salience_hint") or 0.24),
            valence=float(event.get("valence") or 0.0) if isinstance(event.get("valence"), (int, float)) else 0.0,
            uncertainty=0.08,
            consume=True,
        )
    except Exception as exc:
        _logger.debug("InnerWorld: life experience projection degraded: %s", exc)


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
