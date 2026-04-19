"""PersonaProactiveVoice：人格驱动的主动表达——Phase VIII（优化版）。

职责：
  1. 场景感知（深夜陪伴 / 节日问候 / 离线久归 / 情感高潮后 / 话题延续 / 早安问候）
  2. 根据人格特质（PersonalityTraits）生成个性化主动表达
  3. 频率控制（同类型触发间隔 ≥ 24h）
  4. 输出主动表达内容 + 触发类型 + 情感标签

场景触发条件：
  - LATE_NIGHT: 23:00-02:00 + 用户在线
  - LONG_ABSENT: 用户 > 3 天未互动
  - POST_INTENSE: 前一次对话 emotion_intensity > 0.8
  - TOPIC_FOLLOWUP: 存在待跟进话题（由外部提供 pending_followup_topics）
  - MORNING_GREET: 首次日间互动（06:00-10:00）
  - HOLIDAY_GREET: 检测到节日（由外部提供）

设计原则：
  - 纯逻辑层，不直接发送消息（由 proactive_talk skill 调用）
  - 人格特质影响表达风格和内容选择
  - 频率限制防止打扰
  - bond_stage 分层：陌生人和灵魂伴侣说的话完全不同
  - 离线时长感知：3天/1周/1个月说不同的话
  - 季节感知：冬天早安 vs 夏天早安
  - 节日专属文案：春节/圣诞/情人节各有专属内容
  - 话题延续：主动跟进之前未完结的话题，让用户感受到被记住
"""

from __future__ import annotations

import datetime
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

from host_brain.app.prompt_template_store import resolve_prompt_template_json_merged
from host_brain.app.prompt_builder_utils import (
    _normalize_template_lang,
    _resolve_template_payload_with_mirror,
)

from host_brain.persona.offline_continuity import (
    classify_absence_band_from_duration_ms,
    reconnect_style_for_absence_band,
    resolve_absence_band as resolve_offline_absence_band,
)

_logger = logging.getLogger("Natsume.proactive_voice")


def _resolve_render_target_lang(ctx: Mapping[str, Any] | None) -> str:
    if not isinstance(ctx, Mapping):
        return "zh"
    candidate = (
        ctx.get("render_target_lang")
        or ctx.get("reply_lang")
        or ctx.get("output_lang")
    )
    return _normalize_template_lang(str(candidate or "zh"))


def _load_template_json(
    template_id: str,
    *,
    fallback: Any,
    render_target_lang: str | None = None,
) -> Any:
    try:
        if render_target_lang:
            raw = _resolve_template_payload_with_mirror(
                template_id,
                render_target_lang=_normalize_template_lang(str(render_target_lang or "zh")),
            )
        else:
            raw = resolve_prompt_template_json_merged(template_id)
    except Exception:
        return fallback
    if raw is None:
        return fallback
    if isinstance(fallback, Mapping):
        return raw if isinstance(raw, Mapping) else fallback
    if isinstance(fallback, list):
        return raw if isinstance(raw, list) else fallback
    return raw


def _mapping_text(
    mapping: Mapping[str, Any],
    key: str,
    *,
    default: str = "",
    **kwargs: Any,
) -> str:
    text = str(mapping.get(key, default) or "")
    if not kwargs:
        return text
    try:
        return text.format_map({str(k): "" if v is None else str(v) for k, v in kwargs.items()})
    except Exception:
        return text


# ---------------------------------------------------------------------------
# 场景类型
# ---------------------------------------------------------------------------

SCENE_LATE_NIGHT = "LATE_NIGHT"
SCENE_LONG_ABSENT = "LONG_ABSENT"
SCENE_POST_INTENSE = "POST_INTENSE"
SCENE_TOPIC_FOLLOWUP = "TOPIC_FOLLOWUP"
SCENE_MORNING_GREET = "MORNING_GREET"
SCENE_HOLIDAY_GREET = "HOLIDAY_GREET"

# 频率限制：同类型触发间隔（毫秒）
DEFAULT_COOLDOWN_MS = 24 * 60 * 60 * 1000  # 24 小时

# 离线久归阈值（毫秒）：3 天
DEFAULT_LONG_ABSENT_THRESHOLD_MS = 3 * 24 * 60 * 60 * 1000

# 强 reconnect 条件下的提前重连阈值：24 小时
EARLY_RECONNECT_THRESHOLD_MS = 24 * 60 * 60 * 1000

# 情感高潮阈值
DEFAULT_INTENSE_THRESHOLD = 0.8

# 离线时长分级（毫秒）
_ABSENT_SHORT_MS = 24 * 60 * 60 * 1000        # 24小时
_ABSENT_MEDIUM_MS = 3 * 24 * 60 * 60 * 1000   # 72小时
_ABSENT_LONG_MS = 7 * 24 * 60 * 60 * 1000     # 1周

_MEMORY_WARM_TAGS = frozenset({"NOSTALGIC", "WARM", "GRATEFUL", "LOVING", "TOUCHED", "HAPPY"})
_MEMORY_FRAGILE_TAGS = frozenset({"SAD", "HURT", "LONELY", "ANXIOUS", "SCARED", "DEPRESSED", "MELANCHOLY"})


def _coerce_memory_persona_signal(raw: Any) -> dict[str, Any]:
    return dict(raw) if isinstance(raw, dict) else {}


def _score_motive_drive(motive_state: dict[str, Any] | None, key: str) -> float:
    motive = dict(motive_state or {})
    candidate = motive.get(key)
    if candidate in (None, ""):
        drives = motive.get("drives")
        if isinstance(drives, dict):
            candidate = drives.get(key)
    try:
        return float(candidate or 0.0)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProactiveExpression:
    """主动表达内容."""
    scene_type: str        # 场景类型
    content: str           # 表达内容
    emotion_tag: str       # 建议情感标签
    trait_influence: str   # 人格特质影响说明
    ts_ms: int
    motive_influence: str = ""  # 动机状态影响说明
    # ── 技术债务 10.2：等待感知字段（Phase XVI offline_state 注入）──────────
    wait_duration_ms: int = 0    # 距上次互动的等待时长（毫秒），0 表示未知
    loneliness_value: float = 0.0  # 孤独感 [0,1]，来自 LonelinessAccumulator
    anticipation_value: float = 0.0  # 期待值 [0,1]，来自 AnticipationTracker

    def as_dict(self) -> dict[str, Any]:
        return {
            "scene_type": self.scene_type,
            "content": self.content,
            "emotion_tag": self.emotion_tag,
            "trait_influence": self.trait_influence,
            "motive_influence": self.motive_influence,
            "ts_ms": self.ts_ms,
            "wait_duration_ms": self.wait_duration_ms,
            "loneliness_value": self.loneliness_value,
            "anticipation_value": self.anticipation_value,
        }


# ---------------------------------------------------------------------------
# 节日专属文案库
# ---------------------------------------------------------------------------

# 节日名称 → 专属文案（按风格）
_HOLIDAY_SPECIAL: dict[str, dict[str, list[str]]] = {}

# 默认节日文案（未匹配到专属节日时使用）
_HOLIDAY_DEFAULT: dict[str, list[str]] = {}


# ---------------------------------------------------------------------------
# 表达模板（按场景 × bond_stage × 风格）
# ---------------------------------------------------------------------------

# bond_stage 分组：浅层 / 中层 / 深层
_SHALLOW_STAGES = frozenset({"STRANGER", "ACQUAINTANCE"})
_MID_STAGES = frozenset({"FRIEND"})
_DEEP_STAGES = frozenset({"CLOSE_FRIEND", "CONFIDANT", "SOULMATE"})


_LATE_NIGHT_TEMPLATES: dict[str, dict[str, list[str]]] = {}

# 离线久归模板（按离线时长 × bond_stage深度 × 风格）
_LONG_ABSENT_TEMPLATES: dict[str, dict[str, dict[str, list[str]]]] = {}



_LIGHT_RECONNECT_TEMPLATES: dict[str, dict[str, list[str]]] = {}

_REUNION_TEMPLATES: dict[str, dict[str, list[str]]] = {}

_DEFAULT_RUNTIME_TEXTS: dict[str, str] = {}

_DEEP_REUNION_TEMPLATES: dict[str, dict[str, list[str]]] = {}

# 情感高潮后模板（按 bond_stage深度 × 风格）
_POST_INTENSE_TEMPLATES: dict[str, dict[str, list[str]]] = {}

# ---------------------------------------------------------------------------
# 话题延续模板（按 bond_stage深度 × 风格）
# 模板中 {topic} 占位符会被替换为实际话题标签
# ---------------------------------------------------------------------------

_TOPIC_FOLLOWUP_TEMPLATES: dict[str, dict[str, list[str]]] = {}


# 早安问候模板（按季节 × bond_stage深度 × 风格）
_MORNING_GREET_TEMPLATES: dict[str, dict[str, dict[str, list[str]]]] = {}

_DEFAULT_SCENE_TEMPLATES: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _get_season(month: int) -> str:
    """根据月份返回季节。"""
    if month in (3, 4, 5):
        return "spring"
    elif month in (6, 7, 8):
        return "summer"
    elif month in (9, 10, 11):
        return "autumn"
    else:
        return "winter"


def _get_bond_depth(bond_stage: str) -> str:
    """将 bond_stage 映射为深度分组。"""
    stage = str(bond_stage or "").strip().upper()
    if stage in _SHALLOW_STAGES:
        return "shallow"
    elif stage in _MID_STAGES:
        return "mid"
    else:
        return "deep"


def _get_absent_level(elapsed_ms: int) -> str:
    """根据离线时长返回分级。"""
    if elapsed_ms >= _ABSENT_LONG_MS:
        return "long"
    if elapsed_ms >= _ABSENT_MEDIUM_MS:
        return "medium"
    if elapsed_ms >= _ABSENT_SHORT_MS:
        return "short"
    return "short"


def _resolve_absence_band_from_elapsed_ms(elapsed_ms: int) -> str:
    return classify_absence_band_from_duration_ms(elapsed_ms)


def _resolve_long_absent_elapsed_ms(ctx: dict[str, Any], now_ts: int) -> int:
    offline_duration_ms = int(ctx.get("_offline_duration_ms") or 0)
    if offline_duration_ms > 0:
        return offline_duration_ms
    last_ts = int(ctx.get("last_interaction_ts_ms") or 0)
    return now_ts - last_ts if last_ts > 0 else _ABSENT_SHORT_MS


def _select_long_absent_behavior(
    *,
    ctx: dict[str, Any],
    bond_stage: str,
    elapsed_ms: int,
) -> dict[str, Any]:
    raw_motive = dict(ctx.get("motive_state") or {}) if isinstance(ctx.get("motive_state"), dict) else {}

    def _score(key: str) -> float:
        try:
            return float(raw_motive.get(key) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    absence_band = str(ctx.get("_absence_band") or "").strip() or _resolve_absence_band_from_elapsed_ms(elapsed_ms)
    depth = _get_bond_depth(bond_stage)
    reach_out = _score("reach_out_drive")
    care = _score("care_drive")
    continuity = _score("continuity_drive")
    withdraw = _score("withdraw_drive")
    self_protect = _score("self_protect_drive")
    reconnect_drive = max(reach_out, care, continuity)
    retreat_drive = max(withdraw, self_protect)
    baseline_style = reconnect_style_for_absence_band(absence_band)

    reserved = depth == "shallow" or self_protect >= 0.58 or retreat_drive >= reconnect_drive + 0.02
    deep_reunion_ready = (
        baseline_style == "deep_reunion"
        and not reserved
        and (depth == "deep" or continuity >= 0.80 or reconnect_drive >= 0.72)
    )
    if deep_reunion_ready:
        return {
            "behavior": "deep_reunion",
            "template_depth": depth,
            "influence": "reconnect_behavior=deep_reunion",
        }

    reunion_ready = (
        baseline_style in {"reunion", "deep_reunion"}
        and not reserved
        and (depth == "deep" or continuity >= 0.72 or reconnect_drive >= 0.66)
    )
    if reunion_ready:
        return {
            "behavior": "reunion",
            "template_depth": depth,
            "influence": "reconnect_behavior=reunion",
        }

    influence = "reconnect_behavior=light_reconnect"
    if reserved:
        influence = f"{influence} | reconnect_tone=reserved"
    return {
        "behavior": "light_reconnect",
        "template_depth": "shallow" if reserved else depth,
        "influence": influence,
    }


def _pick_variant(variants: list[str], seed: int) -> str:
    """从变体列表中按 seed 哈希选取，避免重复。"""
    if not variants:
        return ""
    idx = seed % len(variants)
    return variants[idx]


def _day_seed(ts_ms: int) -> int:
    """基于日期生成种子，同一天选同一条变体。"""
    dt = datetime.datetime.fromtimestamp(ts_ms / 1000)
    day_str = dt.strftime("%Y%m%d")
    return int(hashlib.md5(day_str.encode()).hexdigest()[:8], 16)


# ---------------------------------------------------------------------------
# PersonaProactiveVoice
# ---------------------------------------------------------------------------

class PersonaProactiveVoice:
    """人格驱动的主动表达（优化版）。

    用法：
        voice = PersonaProactiveVoice()
        result = voice.evaluate_scene(
            user_id="user1",
            traits=personality_traits,
            context={...},
        )
    """

    def __init__(
        self,
        *,
        cooldown_ms: int = DEFAULT_COOLDOWN_MS,
        long_absent_threshold_ms: int = DEFAULT_LONG_ABSENT_THRESHOLD_MS,
        intense_threshold: float = DEFAULT_INTENSE_THRESHOLD,
    ) -> None:
        self._cooldown_ms = int(cooldown_ms)
        self._long_absent_threshold_ms = int(long_absent_threshold_ms)
        self._intense_threshold = float(intense_threshold)
        # user_id → {scene_type → last_trigger_ts_ms}
        self._cooldowns: dict[str, dict[str, int]] = {}

    def evaluate_scene(
        self,
        *,
        user_id: str,
        traits: Any = None,
        context: dict[str, Any] | None = None,
    ) -> ProactiveExpression | None:
        """评估当前场景并生成主动表达（如果触发条件满足）。

        Args:
            user_id: 用户 ID
            traits: PersonalityTraits（可选）
            context: 场景上下文
                - current_ts_ms: 当前时间戳
                - last_interaction_ts_ms: 上次互动时间戳
                - last_emotion_intensity: 上次对话情感强度
                - is_holiday: 是否节日
                - holiday_name: 节日名称
                - user_online: 用户是否在线
                - bond_stage: 当前关系阶段
                - pending_followup_topics: 待跟进话题列表（可选）
                    每项为 dict，含 topic_label(str) 和 topic_keywords(list[str])
                - offline_state: OfflineStateSnapshot dict（Phase XVI，可选）
                    含 offline_duration_ms / loneliness_value / anticipation_value
                - memory_persona_signal: 记忆人格化摘要（可选）
                    含 suppressed_memory_count / dominant_memory_emotion / recall_bias 等

        Returns:
            ProactiveExpression 或 None（不触发）
        """
        uid = str(user_id).strip()
        ctx = dict(context or {})
        now_ts = int(ctx.get("current_ts_ms") or _now_ms())
        motive_state = dict(ctx.get("motive_state") or {}) if isinstance(ctx.get("motive_state"), dict) else {}
        memory_persona_signal = _coerce_memory_persona_signal(ctx.get("memory_persona_signal"))

        # ── 技术债务 10.2：读取 Phase XVI offline_state 快照 ─────────────────
        offline_state: dict[str, Any] = {}
        raw_offline = ctx.get("offline_state")
        if isinstance(raw_offline, dict):
            offline_state = raw_offline
        # 从 offline_state 提取等待感知数据，回退到 context 字段
        _offline_duration_ms = int(
            offline_state.get("offline_duration_ms")
            or offline_state.get("duration_ms")
            or 0
        )
        _loneliness_value = float(
            offline_state.get("loneliness_value")
            or offline_state.get("loneliness_level")
            or offline_state.get("loneliness")
            or 0.0
        )
        _anticipation_value = float(
            offline_state.get("anticipation_value")
            or offline_state.get("anticipation_level")
            or offline_state.get("anticipation")
            or 0.0
        )
        _absence_band = resolve_offline_absence_band(
            offline_state,
            offline_duration_ms=_offline_duration_ms,
        )
        if _offline_duration_ms > 0 and not ctx.get("_offline_duration_ms"):
            ctx["_offline_duration_ms"] = _offline_duration_ms
        ctx["_loneliness_value"] = _loneliness_value
        ctx["_anticipation_value"] = _anticipation_value
        ctx["_absence_band"] = _absence_band

        # 按优先级检测场景
        scenes_to_check = [
            (SCENE_HOLIDAY_GREET, self._check_holiday),
            (SCENE_LATE_NIGHT, self._check_late_night),
            (SCENE_LONG_ABSENT, self._check_long_absent),
            (SCENE_POST_INTENSE, self._check_post_intense),
            (SCENE_TOPIC_FOLLOWUP, self._check_topic_followup),
            (SCENE_MORNING_GREET, self._check_morning),
        ]

        for scene_type, checker in scenes_to_check:
            if not self._check_cooldown(uid, scene_type, now_ts):
                continue
            if checker(ctx, now_ts):
                mode_allowed, mode_influence = self._mode_allows_scene(scene_type, ctx)
                if not mode_allowed:
                    _logger.debug(
                        "主动表达被 runtime mode 拦截: user=%s scene=%s reason=%s",
                        uid,
                        scene_type,
                        mode_influence,
                    )
                    continue
                scene_allowed, scene_influence = self._scene_allows_scene(scene_type, ctx)
                if not scene_allowed:
                    _logger.debug(
                        "主动表达被 scene awareness 拦截: user=%s scene=%s reason=%s",
                        uid,
                        scene_type,
                        scene_influence,
                    )
                    continue
                activity_allowed, activity_influence = self._activity_allows_scene(scene_type, ctx)
                if not activity_allowed:
                    _logger.debug(
                        "主动表达被 activity_monitor 拦截: user=%s scene=%s reason=%s",
                        uid,
                        scene_type,
                        activity_influence,
                    )
                    continue
                motive_allowed, motive_influence = self._motive_allows_scene(scene_type, motive_state)
                if not motive_allowed:
                    _logger.debug(
                        "主动表达被 motive_state 拦截: user=%s scene=%s reason=%s",
                        uid,
                        scene_type,
                        motive_influence,
                    )
                    continue
                memory_allowed, memory_influence = self._memory_persona_allows_scene(
                    scene_type,
                    memory_persona_signal,
                    motive_state,
                )
                if not memory_allowed:
                    _logger.debug(
                        "主动表达被 memory_persona_signal 拦截: user=%s scene=%s reason=%s",
                        uid,
                        scene_type,
                        memory_influence,
                    )
                    continue
                style = self._select_style(traits)
                style = self._apply_mode_to_style(style, ctx)
                bond_stage = str(ctx.get("bond_stage") or "STRANGER").strip().upper()
                long_absent_influence = ""
                if scene_type == SCENE_LONG_ABSENT:
                    elapsed_ms = _resolve_long_absent_elapsed_ms(ctx, now_ts)
                    long_absent_meta = _select_long_absent_behavior(
                        ctx=ctx,
                        bond_stage=bond_stage,
                        elapsed_ms=elapsed_ms,
                    )
                    ctx["_long_absent_behavior"] = long_absent_meta.get("behavior")
                    ctx["_long_absent_template_depth"] = long_absent_meta.get("template_depth")
                    ctx["_long_absent_elapsed_ms"] = elapsed_ms
                    long_absent_influence = str(long_absent_meta.get("influence") or "")
                content = self._get_template(scene_type, style, ctx, now_ts, bond_stage)
                content, memory_style_influence = self._apply_memory_persona_to_content(
                    content,
                    scene_type=scene_type,
                    ctx=ctx,
                    motive_state=motive_state,
                    memory_persona_signal=memory_persona_signal,
                )
                content, activity_style_influence = self._apply_activity_to_content(
                    content,
                    scene_type=scene_type,
                    ctx=ctx,
                )
                emotion_tag = self._scene_emotion(
                    scene_type,
                    loneliness_value=_loneliness_value,
                    anticipation_value=_anticipation_value,
                )
                trait_influence = self._describe_trait_influence(
                    traits,
                    style,
                    render_target_lang=_resolve_render_target_lang(ctx),
                )

                # 记录触发时间
                self._record_trigger(uid, scene_type, now_ts)

                # 计算实际等待时长（优先 offline_state，回退 last_interaction_ts_ms）
                _wait_ms = _offline_duration_ms
                if _wait_ms <= 0:
                    _last_ts = int(ctx.get("last_interaction_ts_ms") or 0)
                    _wait_ms = (now_ts - _last_ts) if _last_ts > 0 else 0

                absence_influence = ""
                if scene_type == SCENE_LONG_ABSENT and _absence_band:
                    absence_influence = f"absence_band={_absence_band}"

                influence_summary = " | ".join(
                    part
                    for part in (
                        absence_influence,
                        long_absent_influence,
                        mode_influence,
                        scene_influence,
                        activity_influence,
                        motive_influence,
                        memory_influence,
                        memory_style_influence,
                        activity_style_influence,
                    )
                    if part
                )
                expr = ProactiveExpression(
                    scene_type=scene_type,
                    content=content,
                    emotion_tag=emotion_tag,
                    trait_influence=trait_influence,
                    motive_influence=influence_summary,
                    ts_ms=now_ts,
                    wait_duration_ms=_wait_ms,
                    loneliness_value=_loneliness_value,
                    anticipation_value=_anticipation_value,
                )
                _logger.info(
                    "主动表达触发: user=%s scene=%s style=%s bond=%s wait=%.1fh loneliness=%.2f motive=%s",
                    uid, scene_type, style, bond_stage,
                    _wait_ms / 3_600_000, _loneliness_value,
                    influence_summary or "-",
                )
                return expr

        return None

    def _motive_allows_scene(
        self,
        scene_type: str,
        motive_state: dict[str, Any] | None,
    ) -> tuple[bool, str]:
        raw = dict(motive_state or {})
        if not raw:
            return True, ""

        reach_out = _score_motive_drive(raw, "reach_out_drive")
        care = _score_motive_drive(raw, "care_drive")
        continuity = _score_motive_drive(raw, "continuity_drive")
        withdraw = _score_motive_drive(raw, "withdraw_drive")
        self_protect = _score_motive_drive(raw, "self_protect_drive")
        dominant_motive = str(raw.get("dominant_motive") or "").strip()

        active_drive = max(reach_out, care, continuity)
        retreat_drive = max(withdraw, self_protect)

        if retreat_drive >= 0.72 and retreat_drive >= active_drive + 0.1:
            return False, "withdraw/self_protect suppresses proactive reach-out"

        if scene_type == SCENE_TOPIC_FOLLOWUP:
            followup_drive = max(continuity, reach_out)
            if followup_drive < 0.45:
                return False, "continuity/reach_out too low for topic follow-up"
            return True, "continuity/reach_out promotes topic follow-up"

        if scene_type == SCENE_LONG_ABSENT:
            reconnect_drive = max(reach_out, care, continuity)
            if reconnect_drive < 0.5:
                return False, "reconnect drive too low for long-absent outreach"
            return True, "reconnect drive promotes long-absent outreach"

        if scene_type == SCENE_POST_INTENSE:
            soothe_drive = max(care, continuity)
            if soothe_drive < 0.5 and retreat_drive > active_drive:
                return False, "care/continuity too low after intense turn"
            if soothe_drive >= 0.55:
                return True, "care/continuity promotes post-intense follow-up"

        if scene_type in {SCENE_LATE_NIGHT, SCENE_MORNING_GREET, SCENE_HOLIDAY_GREET}:
            greet_drive = max(reach_out, care)
            if greet_drive < 0.45 and retreat_drive > active_drive:
                return False, "reach_out/care weaker than retreat drive"
            if greet_drive >= 0.6:
                return True, "reach_out/care promotes greeting"

        if dominant_motive:
            return True, f"dominant_motive={dominant_motive}"
        return True, ""

    def _memory_persona_allows_scene(
        self,
        scene_type: str,
        memory_persona_signal: dict[str, Any] | None,
        motive_state: dict[str, Any] | None,
    ) -> tuple[bool, str]:
        memory = _coerce_memory_persona_signal(memory_persona_signal)
        if not memory:
            return True, ""

        suppressed_count = int(memory.get("suppressed_memory_count") or 0)
        avg_distortion = float(memory.get("avg_distortion_level") or 0.0)
        dominant_emotion = str(memory.get("dominant_memory_emotion") or "").strip().upper()
        recall_bias = str(memory.get("recall_bias") or "").strip().lower()
        shared_history_resonance_count = int(memory.get("shared_history_resonance_count") or 0)
        protective_recoil_count = int(memory.get("protective_recoil_count") or 0)

        reach_out = _score_motive_drive(motive_state, "reach_out_drive")
        care = _score_motive_drive(motive_state, "care_drive")
        continuity = _score_motive_drive(motive_state, "continuity_drive")
        withdraw = _score_motive_drive(motive_state, "withdraw_drive")
        self_protect = _score_motive_drive(motive_state, "self_protect_drive")
        supportive_drive = max(reach_out, care, continuity)
        retreat_drive = max(withdraw, self_protect)

        if (
            protective_recoil_count > 0
            and scene_type in {SCENE_TOPIC_FOLLOWUP, SCENE_POST_INTENSE, SCENE_LONG_ABSENT}
            and retreat_drive >= 0.28
        ):
            return False, f"memory:recoil={protective_recoil_count} discourages proactive reopening"

        if recall_bias == "uncertain" and scene_type in {SCENE_TOPIC_FOLLOWUP, SCENE_POST_INTENSE} and retreat_drive >= 0.45:
            return False, "memory:uncertain prefers not to proactively reopen details"

        if scene_type == SCENE_TOPIC_FOLLOWUP:
            if suppressed_count > 0 and retreat_drive >= 0.35:
                return False, "memory:suppressed discourages topic follow-up"
            if avg_distortion >= 0.5:
                return False, "memory:distorted too uncertain for proactive follow-up"
            if dominant_emotion in _MEMORY_FRAGILE_TAGS and supportive_drive < 0.72:
                return False, f"memory:fragile={dominant_emotion} prefers gentler timing"
            if (
                shared_history_resonance_count > 0
                and protective_recoil_count <= 0
                and suppressed_count <= 0
                and avg_distortion < 0.35
                and max(care, continuity) >= 0.45
            ):
                return True, f"memory:resonance={shared_history_resonance_count} supports continuity"
            if (
                dominant_emotion in _MEMORY_WARM_TAGS
                and suppressed_count <= 0
                and avg_distortion < 0.35
                and max(care, continuity) >= 0.45
            ):
                return True, f"memory:warm={dominant_emotion} supports continuity"

        if scene_type == SCENE_POST_INTENSE:
            if (suppressed_count > 0 or dominant_emotion in _MEMORY_FRAGILE_TAGS) and max(care, continuity) < 0.75:
                return False, "memory:fragile suggests waiting after intense turn"

        if scene_type == SCENE_LONG_ABSENT:
            if suppressed_count > 0 and retreat_drive >= 0.55:
                return False, "memory:suppressed keeps reconnect from probing old topics"
            if (
                shared_history_resonance_count > 0
                and protective_recoil_count <= 0
                and suppressed_count <= 0
                and avg_distortion < 0.35
                and supportive_drive >= 0.5
            ):
                return True, f"memory:resonance={shared_history_resonance_count} supports reconnect"
            if (
                dominant_emotion in _MEMORY_WARM_TAGS
                and suppressed_count <= 0
                and avg_distortion < 0.35
                and supportive_drive >= 0.5
            ):
                return True, f"memory:warm={dominant_emotion} supports reconnect"

        return True, ""

    def _apply_memory_persona_to_content(
        self,
        content: str,
        *,
        scene_type: str,
        ctx: dict[str, Any],
        motive_state: dict[str, Any] | None,
        memory_persona_signal: dict[str, Any] | None,
    ) -> tuple[str, str]:
        resolved_content = str(content or "").strip()
        memory = _coerce_memory_persona_signal(memory_persona_signal)
        render_target_lang = _resolve_render_target_lang(ctx)
        runtime_texts = _load_template_json(
            "proactive_voice.runtime_texts",
            fallback=_DEFAULT_RUNTIME_TEXTS,
            render_target_lang=render_target_lang,
        )
        if not resolved_content or not memory:
            return resolved_content, ""

        suppressed_count = int(memory.get("suppressed_memory_count") or 0)
        avg_distortion = float(memory.get("avg_distortion_level") or 0.0)
        dominant_emotion = str(memory.get("dominant_memory_emotion") or "").strip().upper()
        recall_bias = str(memory.get("recall_bias") or "").strip().lower()
        shared_history_resonance_count = int(memory.get("shared_history_resonance_count") or 0)
        protective_recoil_count = int(memory.get("protective_recoil_count") or 0)

        reach_out = _score_motive_drive(motive_state, "reach_out_drive")
        care = _score_motive_drive(motive_state, "care_drive")
        continuity = _score_motive_drive(motive_state, "continuity_drive")
        withdraw = _score_motive_drive(motive_state, "withdraw_drive")
        self_protect = _score_motive_drive(motive_state, "self_protect_drive")
        supportive_drive = max(reach_out, care, continuity)
        retreat_drive = max(withdraw, self_protect)

        guarded_memory = (
            suppressed_count > 0
            or dominant_emotion in _MEMORY_FRAGILE_TAGS
            or recall_bias in {"guarded", "uncertain"}
            or avg_distortion >= 0.35
            or protective_recoil_count > 0
        )
        if guarded_memory:
            if scene_type == SCENE_TOPIC_FOLLOWUP:
                topic_label = self._extract_topic_label(ctx)
                return (
                    _mapping_text(
                        runtime_texts,
                        "guarded_soften_followup",
                        topic_label=topic_label,
                    ),
                    "memory:guarded_soften_followup",
                )
            if scene_type == SCENE_POST_INTENSE:
                return (
                    _mapping_text(
                        runtime_texts,
                        "guarded_soften_post_intense",
                    ),
                    "memory:guarded_soften_post_intense",
                )
            if scene_type == SCENE_LONG_ABSENT:
                return (
                    _mapping_text(
                        runtime_texts,
                        "guarded_soften_reconnect",
                    ),
                    "memory:guarded_soften_reconnect",
                )
            if scene_type == SCENE_LATE_NIGHT:
                return (
                    _mapping_text(
                        runtime_texts,
                        "guarded_soften_late_night",
                    ),
                    "memory:guarded_soften_late_night",
                )

        if (
            (dominant_emotion in _MEMORY_WARM_TAGS or shared_history_resonance_count > 0)
            and suppressed_count <= 0
            and protective_recoil_count <= 0
            and avg_distortion < 0.35
            and supportive_drive >= retreat_drive
        ):
            if scene_type == SCENE_TOPIC_FOLLOWUP:
                topic_label = self._extract_topic_label(ctx)
                if shared_history_resonance_count > 0:
                    return (
                        _mapping_text(
                            runtime_texts,
                            "resonance_followup",
                            topic_label=topic_label,
                        ),
                        f"memory:resonance_followup={dominant_emotion or 'NEUTRAL'}",
                    )
                return (
                    _mapping_text(
                        runtime_texts,
                        "warm_followup",
                        topic_label=topic_label,
                    ),
                    f"memory:warm_followup={dominant_emotion}",
                )
            if scene_type == SCENE_LONG_ABSENT:
                if shared_history_resonance_count > 0:
                    return (
                        _mapping_text(
                            runtime_texts,
                            "resonance_reconnect",
                        ),
                        f"memory:resonance_reconnect={dominant_emotion or 'NEUTRAL'}",
                    )
                return (
                    _mapping_text(
                        runtime_texts,
                        "warm_reconnect",
                    ),
                    f"memory:warm_reconnect={dominant_emotion}",
                )
            if scene_type == SCENE_POST_INTENSE:
                if shared_history_resonance_count > 0:
                    return (
                        _mapping_text(
                            runtime_texts,
                            "resonance_aftercare",
                        ),
                        f"memory:resonance_aftercare={dominant_emotion or 'NEUTRAL'}",
                    )
                return (
                    _mapping_text(
                        runtime_texts,
                        "warm_aftercare",
                    ),
                    f"memory:warm_aftercare={dominant_emotion}",
                )

        return resolved_content, ""

    @staticmethod
    def _resolve_runtime_mode(ctx: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        sys_mode = str(ctx.get("sys_mode") or "").strip().lower()
        activity_monitor_raw = ctx.get("activity_monitor")
        activity_monitor = dict(activity_monitor_raw) if isinstance(activity_monitor_raw, dict) else {}
        if sys_mode in {"normal", "focus", "gaming", "sleep", "maintenance", "locked"}:
            return sys_mode, activity_monitor
        if bool(activity_monitor.get("is_gaming")):
            return "gaming", activity_monitor
        return "normal", activity_monitor

    def _mode_allows_scene(self, scene_type: str, ctx: dict[str, Any]) -> tuple[bool, str]:
        sys_mode, activity_monitor = self._resolve_runtime_mode(ctx)
        if sys_mode == "sleep":
            return False, "sys_mode=sleep suppresses proactive expression"
        if sys_mode == "focus" and scene_type not in {SCENE_LONG_ABSENT, SCENE_POST_INTENSE}:
            return False, "sys_mode=focus keeps proactive expression restrained"
        if sys_mode == "gaming" and scene_type in {SCENE_LATE_NIGHT, SCENE_MORNING_GREET, SCENE_HOLIDAY_GREET}:
            return False, "sys_mode=gaming prefers companion follow-up over generic greeting"
        if bool(activity_monitor.get("is_gaming")) and scene_type == SCENE_LATE_NIGHT:
            return False, "gaming activity suppresses generic late-night prompt"
        return True, "" if sys_mode == "normal" else f"mode={sys_mode}"

    def _activity_allows_scene(self, scene_type: str, ctx: dict[str, Any]) -> tuple[bool, str]:
        _sys_mode, activity_monitor = self._resolve_runtime_mode(ctx)
        try:
            idle_minutes = float(activity_monitor.get("idle_minutes") or 0.0)
        except (TypeError, ValueError):
            idle_minutes = 0.0
        recent_event_type = str(activity_monitor.get("recent_event_type") or "").strip().lower()
        try:
            recent_event_age_s = float(activity_monitor.get("recent_event_age_s"))
        except (TypeError, ValueError):
            recent_event_age_s = float("inf")
        is_gaming = bool(activity_monitor.get("is_gaming"))
        pending_topics = ctx.get("pending_followup_topics")
        has_pending_topics = isinstance(pending_topics, list) and any(
            isinstance(item, dict) and str(item.get("topic_label") or "").strip() for item in pending_topics
        )

        if recent_event_type == "wakeup" and recent_event_age_s <= 600.0:
            if scene_type != SCENE_MORNING_GREET:
                return False, "activity:wakeup prefers gentle morning re-entry"
            return True, "activity:wakeup"

        if idle_minutes >= 20.0 and scene_type in {SCENE_LATE_NIGHT, SCENE_MORNING_GREET, SCENE_HOLIDAY_GREET}:
            return False, f"activity:idle={idle_minutes:.1f} suppresses generic ping"

        if (is_gaming or recent_event_type == "gaming_detected") and scene_type == SCENE_LONG_ABSENT and has_pending_topics:
            return False, "activity:gaming prefers topic follow-up over reconnect"

        if recent_event_type == "gaming_detected" and recent_event_age_s <= 900.0 and scene_type == SCENE_TOPIC_FOLLOWUP:
            return True, "activity:gaming_detected"

        if recent_event_type == "gaming_exited" and recent_event_age_s <= 600.0 and scene_type == SCENE_LONG_ABSENT:
            return True, "activity:gaming_exited"

        return True, ""

    @staticmethod
    def _coerce_scene_awareness(ctx: dict[str, Any]) -> dict[str, Any]:
        raw = ctx.get("scene_awareness")
        if not isinstance(raw, dict):
            return {"scene_tags": [], "current_activity": None, "companion_suitable": None}

        values: list[str] = []
        raw_tags = raw.get("scene_tags") or []
        if isinstance(raw_tags, str):
            raw_tags = [part.strip() for part in raw_tags.replace(";", ",").replace("|", ",").split(",")]
        if isinstance(raw_tags, (list, tuple, set)):
            for item in raw_tags:
                normalized = str(item or "").strip().lower().replace("-", "_").replace(" ", "_")
                normalized = normalized.strip("_")
                if normalized and normalized not in values:
                    values.append(normalized)

        current_activity = str(raw.get("current_activity") or "").strip().lower().replace("-", "_").replace(" ", "_")
        if current_activity and current_activity not in values:
            values.append(current_activity)

        alias_map = {
            "focus": "focus_work",
            "working": "focus_work",
            "coding": "focus_work",
            "editing": "focus_work",
            "study": "focus_work",
            "video": "fullscreen_video",
            "fullscreen": "fullscreen_video",
            "movie": "fullscreen_video",
            "sleep": "sleeping_screen",
            "lock_screen": "sleeping_screen",
            "screensaver": "sleeping_screen",
            "conference": "meeting",
            "video_call": "meeting",
            "game": "gaming",
            "combat": "battle",
        }

        scene_tags: list[str] = []
        for tag in values:
            resolved = alias_map.get(tag, tag)
            if resolved not in scene_tags:
                scene_tags.append(resolved)
            if resolved in {"battle", "lobby", "gameplay"} and "gaming" not in scene_tags:
                scene_tags.insert(0, "gaming")

        return {
            "scene_tags": scene_tags,
            "current_activity": str(raw.get("current_activity") or "").strip() or None,
            "companion_suitable": raw.get("companion_suitable") if isinstance(raw.get("companion_suitable"), bool) else None,
        }

    def _scene_allows_scene(self, scene_type: str, ctx: dict[str, Any]) -> tuple[bool, str]:
        scene = self._coerce_scene_awareness(ctx)
        scene_tags = set(scene.get("scene_tags") or [])
        if scene.get("companion_suitable") is False:
            return False, "scene companion_suitable=false"
        if scene_tags & {"focus_work", "fullscreen_video", "sleeping_screen", "meeting"}:
            return False, f"scene={','.join(sorted(scene_tags))} suppresses proactive"
        if scene_tags & {"gaming", "gameplay", "battle", "lobby"}:
            if scene_type in {SCENE_LATE_NIGHT, SCENE_MORNING_GREET, SCENE_HOLIDAY_GREET}:
                return False, "scene=gaming prefers companion follow-up over generic greeting"
            return True, "scene=gaming"
        return True, ""

    def _apply_mode_to_style(self, style: str, ctx: dict[str, Any]) -> str:
        sys_mode, activity_monitor = self._resolve_runtime_mode(ctx)
        recent_event_type = str(activity_monitor.get("recent_event_type") or "").strip().lower()
        if sys_mode == "gaming" and style == "default":
            return "playful"
        if (sys_mode in {"focus", "sleep"} or recent_event_type == "wakeup") and style == "playful":
            return "warm"
        if recent_event_type == "wakeup" and style == "default":
            return "warm"
        return style

    def _apply_activity_to_content(
        self,
        content: str,
        *,
        scene_type: str,
        ctx: dict[str, Any],
    ) -> tuple[str, str]:
        resolved_content = str(content or "").strip()
        render_target_lang = _resolve_render_target_lang(ctx)
        runtime_texts = _load_template_json(
            "proactive_voice.runtime_texts",
            fallback=_DEFAULT_RUNTIME_TEXTS,
            render_target_lang=render_target_lang,
        )
        if not resolved_content:
            return resolved_content, ""

        _sys_mode, activity_monitor = self._resolve_runtime_mode(ctx)
        recent_event_type = str(activity_monitor.get("recent_event_type") or "").strip().lower()
        try:
            recent_event_age_s = float(activity_monitor.get("recent_event_age_s"))
        except (TypeError, ValueError):
            recent_event_age_s = float("inf")
        is_gaming = bool(activity_monitor.get("is_gaming"))

        if recent_event_type == "wakeup" and recent_event_age_s <= 600.0 and scene_type == SCENE_MORNING_GREET:
            return (
                _mapping_text(
                    runtime_texts,
                    "wakeup_gentle_morning",
                ),
                "activity:wakeup_gentle_morning",
            )

        if (is_gaming or recent_event_type == "gaming_detected") and scene_type == SCENE_TOPIC_FOLLOWUP:
            topic_label = self._extract_topic_label(ctx)
            return (
                _mapping_text(
                    runtime_texts,
                    "gaming_companion_followup",
                    topic_label=topic_label,
                ),
                "activity:gaming_companion_followup",
            )

        if recent_event_type == "gaming_exited" and recent_event_age_s <= 600.0 and scene_type == SCENE_LONG_ABSENT:
            return (
                _mapping_text(
                    runtime_texts,
                    "gaming_exit_soft_reconnect",
                ),
                "activity:gaming_exit_soft_reconnect",
            )

        return resolved_content, ""

    # ── 场景检测 ──────────────────────────────────────────────────────────

    def _check_late_night(self, ctx: dict[str, Any], now_ts: int) -> bool:
        """深夜陪伴：23:00-02:00 + 用户在线。"""
        if not ctx.get("user_online", False):
            return False
        try:
            dt = datetime.datetime.fromtimestamp(now_ts / 1000)
            return dt.hour >= 23 or dt.hour < 2
        except Exception:
            return False

    def _check_long_absent(self, ctx: dict[str, Any], now_ts: int) -> bool:
        """离线久归：用户 > 3 天未互动。

        技术债务 10.2 修复：优先使用 offline_state.offline_duration_ms（Phase XVI
        InnerStateClock 精确计算），回退到 last_interaction_ts_ms 差值。
        """
        # 优先使用 offline_state 注入的精确离线时长
        offline_duration_ms = int(ctx.get("_offline_duration_ms") or 0)
        if offline_duration_ms > 0:
            elapsed = offline_duration_ms
        else:
            # 回退：使用 last_interaction_ts_ms 差值
            last_ts = int(ctx.get("last_interaction_ts_ms") or 0)
            if last_ts <= 0:
                return False
            elapsed = now_ts - last_ts

        if elapsed >= self._long_absent_threshold_ms:
            return True

        if elapsed < EARLY_RECONNECT_THRESHOLD_MS:
            return False

        loneliness = float(ctx.get("_loneliness_value") or 0.0)
        anticipation = float(ctx.get("_anticipation_value") or 0.0)
        bond_stage = str(ctx.get("bond_stage") or "STRANGER").strip().upper()
        motive_state = dict(ctx.get("motive_state") or {})

        def _score(key: str) -> float:
            try:
                return float(motive_state.get(key) or 0.0)
            except (TypeError, ValueError):
                return 0.0

        reconnect_drive = max(
            _score("reach_out_drive"),
            _score("care_drive"),
            _score("continuity_drive"),
        )
        deep_bond = bond_stage in {"FRIEND", "CLOSE", "INTIMATE", "SOULMATE", "CLOSE_FRIEND", "CONFIDANT"}
        if reconnect_drive >= 0.75 and max(loneliness, anticipation) >= 0.45:
            return True
        if deep_bond and reconnect_drive >= 0.62 and max(loneliness, anticipation) >= 0.35:
            return True
        return False

    def _check_post_intense(self, ctx: dict[str, Any], now_ts: int) -> bool:
        """情感高潮后：前一次对话 emotion_intensity > 阈值。"""
        intensity = float(ctx.get("last_emotion_intensity") or 0.0)
        return intensity > self._intense_threshold

    def _check_morning(self, ctx: dict[str, Any], now_ts: int) -> bool:
        """早安问候：06:00-10:00 首次日间互动。"""
        try:
            dt = datetime.datetime.fromtimestamp(now_ts / 1000)
            return 6 <= dt.hour < 10
        except Exception:
            return False

    def _check_topic_followup(self, ctx: dict[str, Any], now_ts: int) -> bool:
        """话题延续：存在待跟进话题。

        context 需提供 pending_followup_topics 列表，每项包含：
          - topic_label: str  话题标签（如"面试"、"搬家"）
          - topic_keywords: list[str]  话题关键词（可选）
        """
        topics = ctx.get("pending_followup_topics")
        if not topics or not isinstance(topics, list):
            return False
        # 至少有一个有效话题（含非空 label）
        return any(
            isinstance(t, dict) and str(t.get("topic_label", "")).strip()
            for t in topics
        )

    def _check_holiday(self, ctx: dict[str, Any], now_ts: int) -> bool:
        """节日问候：外部提供节日标志。"""
        return bool(ctx.get("is_holiday", False))

    # ── 风格选择 ──────────────────────────────────────────────────────────

    def _select_style(self, traits: Any) -> str:
        """根据人格特质选择表达风格。"""
        if traits is None:
            return "default"

        warmth = float(getattr(traits, "warmth", 0.5))
        playfulness = float(getattr(traits, "playfulness", 0.5))
        sensitivity = float(getattr(traits, "sensitivity", 0.5))

        # 最高维度决定风格
        scores = {
            "warm": warmth + sensitivity * 0.3,
            "playful": playfulness + float(getattr(traits, "humor", 0.4)) * 0.3,
            "caring": warmth * 0.5 + sensitivity * 0.5,
        }
        best_style = max(scores, key=scores.get)  # type: ignore
        return best_style

    def _get_template(
        self,
        scene_type: str,
        style: str,
        ctx: dict[str, Any],
        now_ts: int,
        bond_stage: str,
    ) -> str:
        """获取表达模板（支持 bond_stage 分层、离线时长感知、季节感知、节日专属）。"""
        seed = _day_seed(now_ts)
        depth = _get_bond_depth(bond_stage)
        render_target_lang = _resolve_render_target_lang(ctx)
        runtime_texts = _load_template_json(
            "proactive_voice.runtime_texts",
            fallback=_DEFAULT_RUNTIME_TEXTS,
            render_target_lang=render_target_lang,
        )
        scene_templates = _load_template_json(
            "proactive_voice.scene_templates",
            fallback=_DEFAULT_SCENE_TEMPLATES,
            render_target_lang=render_target_lang,
        )
        late_night_templates = scene_templates.get("late_night") if isinstance(scene_templates, Mapping) else None
        long_absent_templates = scene_templates.get("long_absent") if isinstance(scene_templates, Mapping) else None
        light_reconnect_templates = scene_templates.get("light_reconnect") if isinstance(scene_templates, Mapping) else None
        reunion_templates = scene_templates.get("reunion") if isinstance(scene_templates, Mapping) else None
        deep_reunion_templates = scene_templates.get("deep_reunion") if isinstance(scene_templates, Mapping) else None
        post_intense_templates = scene_templates.get("post_intense") if isinstance(scene_templates, Mapping) else None
        topic_followup_templates = scene_templates.get("topic_followup") if isinstance(scene_templates, Mapping) else None

        if scene_type == SCENE_HOLIDAY_GREET:
            return self._get_holiday_template(ctx, style, seed)

        elif scene_type == SCENE_LATE_NIGHT:
            late_night_source = late_night_templates if isinstance(late_night_templates, Mapping) else _LATE_NIGHT_TEMPLATES
            depth_templates = dict(late_night_source.get(depth) or late_night_source.get("mid") or {})
            variants = depth_templates.get(style) or depth_templates.get("default", [])
            if variants:
                return _pick_variant(variants, seed)
            return _mapping_text(runtime_texts, "generic_default_reply")

        elif scene_type == SCENE_LONG_ABSENT:
            elapsed = int(ctx.get("_long_absent_elapsed_ms") or 0) or _resolve_long_absent_elapsed_ms(ctx, now_ts)
            level = _get_absent_level(elapsed)
            behavior = str(ctx.get("_long_absent_behavior") or "").strip() or "light_reconnect"
            template_depth = str(ctx.get("_long_absent_template_depth") or depth).strip() or depth
            if behavior == "deep_reunion":
                behavior_templates = (
                    deep_reunion_templates if isinstance(deep_reunion_templates, Mapping) else _DEEP_REUNION_TEMPLATES
                )
            elif behavior == "reunion":
                behavior_templates = reunion_templates if isinstance(reunion_templates, Mapping) else _REUNION_TEMPLATES
            else:
                behavior_templates = (
                    light_reconnect_templates if isinstance(light_reconnect_templates, Mapping) else _LIGHT_RECONNECT_TEMPLATES
                )

            depth_templates = behavior_templates.get(template_depth, behavior_templates.get(depth, behavior_templates.get("mid", {})))
            variants = depth_templates.get(style) or depth_templates.get("default", [])
            if variants:
                return _pick_variant(variants, seed)

            long_absent_source = long_absent_templates if isinstance(long_absent_templates, Mapping) else _LONG_ABSENT_TEMPLATES
            level_templates = dict(long_absent_source.get(level) or long_absent_source.get("short") or {})
            depth_templates = dict(level_templates.get(depth) or level_templates.get("mid") or {})
            loneliness = float(ctx.get("_loneliness_value") or 0.0)
            variants = depth_templates.get(style) or depth_templates.get("default", [])
            if loneliness >= 0.6 and len(variants) > 1:
                half = max(1, len(variants) // 2)
                variants = variants[half:]
            if variants:
                return _pick_variant(variants, seed)
            return _mapping_text(runtime_texts, "generic_default_reply")

        elif scene_type == SCENE_POST_INTENSE:
            post_intense_source = post_intense_templates if isinstance(post_intense_templates, Mapping) else _POST_INTENSE_TEMPLATES
            depth_templates = dict(post_intense_source.get(depth) or post_intense_source.get("mid") or {})
            variants = depth_templates.get(style) or depth_templates.get("default", [])
            if variants:
                return _pick_variant(variants, seed)
            return _mapping_text(runtime_texts, "generic_default_reply")

        elif scene_type == SCENE_TOPIC_FOLLOWUP:
            topic_followup_source = (
                topic_followup_templates if isinstance(topic_followup_templates, Mapping) else _TOPIC_FOLLOWUP_TEMPLATES
            )
            depth_templates = dict(topic_followup_source.get(depth) or topic_followup_source.get("mid") or {})
            variants = depth_templates.get(style) or depth_templates.get("default", [])
            if not variants:
                return _mapping_text(runtime_texts, "generic_default_reply")
            raw = _pick_variant(variants, seed)
            # 从 context 中取第一个有效话题标签填充模板
            topic_label = self._extract_topic_label(ctx)
            return raw.replace("{topic}", topic_label)

        elif scene_type == SCENE_MORNING_GREET:
            morning_data = _load_template_json(
                "proactive_voice.morning_greet_templates",
                fallback=_MORNING_GREET_TEMPLATES,
                render_target_lang=render_target_lang,
            )
            dt = datetime.datetime.fromtimestamp(now_ts / 1000)
            season = _get_season(dt.month)
            season_source = morning_data if isinstance(morning_data, Mapping) else _MORNING_GREET_TEMPLATES
            season_templates = dict(season_source.get(season) or season_source.get("spring") or {})
            depth_templates = dict(season_templates.get(depth) or season_templates.get("mid") or {})
            variants = depth_templates.get(style) or depth_templates.get("default", [])
            if variants:
                return _pick_variant(variants, seed)
            return _mapping_text(runtime_texts, "generic_default_reply")

        return _mapping_text(
            runtime_texts,
            "generic_default_reply",
        )

    def _get_holiday_template(self, ctx: dict[str, Any], style: str, seed: int) -> str:
        """获取节日专属文案。"""
        render_target_lang = _resolve_render_target_lang(ctx)
        holiday_data = _load_template_json(
            "proactive_voice.holiday_templates",
            fallback={"special": _HOLIDAY_SPECIAL, "default": _HOLIDAY_DEFAULT},
            render_target_lang=render_target_lang,
        )
        special_mapping = holiday_data.get("special") if isinstance(holiday_data, Mapping) else None
        default_mapping = holiday_data.get("default") if isinstance(holiday_data, Mapping) else None
        special_templates = special_mapping if isinstance(special_mapping, Mapping) else _HOLIDAY_SPECIAL
        default_templates = default_mapping if isinstance(default_mapping, Mapping) else _HOLIDAY_DEFAULT
        runtime_texts = _load_template_json(
            "proactive_voice.runtime_texts",
            fallback=_DEFAULT_RUNTIME_TEXTS,
            render_target_lang=render_target_lang,
        )
        holiday_name = str(ctx.get("holiday_name") or "").strip()

        # 尝试匹配专属节日
        for key in special_templates:
            if key in holiday_name or holiday_name in key:
                style_variants = special_templates[key].get(style) or special_templates[key].get("warm", [])
                if style_variants:
                    return _pick_variant(style_variants, seed)

        # 默认节日文案
        default_variants = list(default_templates.get(style) or default_templates.get("warm") or [])
        content = _pick_variant(default_variants, seed) if default_variants else _mapping_text(
            runtime_texts,
            "holiday_generic_default",
        )

        # 替换节日名称
        if holiday_name:
            holiday_day_token = _mapping_text(runtime_texts, "holiday_generic_day_token")
            holiday_greet_token = _mapping_text(runtime_texts, "holiday_generic_greet_token")
            holiday_day_text = _mapping_text(
                runtime_texts,
                "holiday_name_day_template",
                holiday_name=holiday_name,
            )
            holiday_greet_text = _mapping_text(
                runtime_texts,
                "holiday_name_greet_template",
                holiday_name=holiday_name,
            )
            if holiday_day_token and holiday_day_text:
                content = content.replace(holiday_day_token, holiday_day_text)
            if holiday_greet_token and holiday_greet_text:
                content = content.replace(holiday_greet_token, holiday_greet_text)

        return content

    @staticmethod
    def _extract_topic_label(ctx: dict[str, Any]) -> str:
        """从 context 的 pending_followup_topics 中提取第一个有效话题标签。"""
        render_target_lang = _resolve_render_target_lang(ctx)
        runtime_texts = _load_template_json(
            "proactive_voice.runtime_texts",
            fallback=_DEFAULT_RUNTIME_TEXTS,
            render_target_lang=render_target_lang,
        )
        topics = ctx.get("pending_followup_topics") or []
        for t in topics:
            if isinstance(t, dict):
                label = str(t.get("topic_label", "")).strip()
                if label:
                    return label
        return _mapping_text(
            runtime_texts,
            "default_topic_label",
        )

    def _scene_emotion(
        self,
        scene_type: str,
        *,
        loneliness_value: float = 0.0,
        anticipation_value: float = 0.0,
    ) -> str:
        """场景对应的情感标签。

        技术债务 10.2：根据 loneliness_value / anticipation_value 动态调整情感标签强度。
        - LONG_ABSENT + 高孤独感（>0.7）→ LONGING（更强烈的思念）
        - LONG_ABSENT + 高期待值（>0.6）→ ANTICIPATING（期待重逢）
        - LATE_NIGHT + 高孤独感（>0.5）→ MELANCHOLY（深夜孤独）
        """
        base_map: dict[str, str] = {
            SCENE_LATE_NIGHT: "CARING",
            SCENE_LONG_ABSENT: "LOVING",
            SCENE_POST_INTENSE: "THINKING",
            SCENE_TOPIC_FOLLOWUP: "THINKING",
            SCENE_MORNING_GREET: "HAPPY",
            SCENE_HOLIDAY_GREET: "HAPPY",
        }
        base = base_map.get(scene_type, "NEUTRAL")

        # 根据等待感知数据动态升级情感标签
        if scene_type == SCENE_LONG_ABSENT:
            if loneliness_value >= 0.7:
                return "LONGING"       # 强烈思念
            if anticipation_value >= 0.6:
                return "ANTICIPATING"  # 期待重逢
        elif scene_type == SCENE_LATE_NIGHT:
            if loneliness_value >= 0.5:
                return "MELANCHOLY"    # 深夜孤独

        return base

    def _describe_trait_influence(self, traits: Any, style: str, *, render_target_lang: str = "zh") -> str:
        """描述人格特质对表达风格的影响。"""
        runtime_texts = _load_template_json(
            "proactive_voice.runtime_texts",
            fallback=_DEFAULT_RUNTIME_TEXTS,
            render_target_lang=render_target_lang,
        )
        if traits is None:
            return _mapping_text(
                runtime_texts,
                "trait_default",
            )
        desc_map = {
            "warm": _mapping_text(
                runtime_texts,
                "trait_warm",
            ),
            "playful": _mapping_text(
                runtime_texts,
                "trait_playful",
            ),
            "caring": _mapping_text(
                runtime_texts,
                "trait_caring",
            ),
            "default": _mapping_text(
                runtime_texts,
                "trait_balanced",
            ),
        }
        return desc_map.get(
            style,
            _mapping_text(
                runtime_texts,
                "trait_fallback",
            ),
        )

    # ── 频率控制 ──────────────────────────────────────────────────────────

    def _check_cooldown(self, user_id: str, scene_type: str, now_ts: int) -> bool:
        """检查是否在冷却期内。"""
        user_cooldowns = self._cooldowns.get(user_id, {})
        last_trigger = user_cooldowns.get(scene_type, 0)
        if last_trigger > 0 and (now_ts - last_trigger) < self._cooldown_ms:
            return False
        return True

    def _record_trigger(self, user_id: str, scene_type: str, ts_ms: int) -> None:
        """记录触发时间。"""
        if user_id not in self._cooldowns:
            self._cooldowns[user_id] = {}
        self._cooldowns[user_id][scene_type] = ts_ms

    # ── 查询 ──────────────────────────────────────────────────────────────

    def get_cooldown_status(self, user_id: str) -> dict[str, int]:
        """获取用户的冷却状态。"""
        return dict(self._cooldowns.get(str(user_id).strip(), {}))


def _now_ms() -> int:
    return int(time.time() * 1000)
