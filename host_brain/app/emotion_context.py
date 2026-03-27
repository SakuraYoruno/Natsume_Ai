"""情感上下文处理模块。

从 fastapi_app.py 拆分，负责情感状态的解析、归一化和治理快照构建。

升级（Phase 1/2/5）：
- Phase 1: 持久化情绪状态 + 时间感知衰减，让情绪"活着"
- Phase 2: VAD 映射精细化（16 种标签全覆盖 + dominance 维度）
- Phase 5: 情感回声差异化（关系深度 + 情绪类型）
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Mapping

from host_brain.core.metabolism import (
    build_governance_snapshot,
    build_hard_snapshot,
    normalize_hard_state,
    # 修复审计问题 #6：接入孤立的情感模块
    EmotionVector,
    EmotionTimeline,
    compute_emotion_echo,
    evaluate_emotion_triggers,
    apply_visual_emotion_hint,  # noqa: F401 — 供外部模块按需调用
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 线程安全锁——保护所有全局可变状态（Fix: 全局单例在 FastAPI 异步环境中的竞态条件）
# ---------------------------------------------------------------------------
# normalize/update helpers can re-enter accessors that also touch global emotion state.
_emotion_lock = threading.RLock()

# 修复审计问题 #6：全局 EmotionTimeline 实例，记录情感状态变化历史
_global_emotion_timeline = EmotionTimeline(max_entries=100)

# Phase 1: 全局持久化情绪向量（进程级单例，支持跨请求情绪连续性）
_global_emotion_vector: EmotionVector | None = None
_global_emotion_vector_last_ts: int = 0  # 上次更新时间戳（毫秒）

# 方向 C：上一次的 emotion_tag（用于 VTS 过渡帧计算）
_global_prev_emotion_tag: str | None = None

# 方向 D：上次日记写入时间戳（ms），防止频繁写入
_last_diary_flush_ts: int = 0


def get_emotion_timeline() -> EmotionTimeline:
    """获取全局情感时间线实例（供外部模块访问）。"""
    return _global_emotion_timeline


def get_persistent_emotion_vector() -> EmotionVector:
    """获取全局持久化情绪向量（Phase 1）。

    首次调用时初始化为中性偏微正的默认值（模拟"刚醒来，状态还不错"）。
    线程安全：通过 _emotion_lock 保护初始化和读取。
    """
    global _global_emotion_vector, _global_emotion_vector_last_ts
    with _emotion_lock:
        if _global_emotion_vector is None:
            _global_emotion_vector = EmotionVector(
                valence=0.1, arousal=0.2, dominance=0.0,
                ts_ms=int(time.time() * 1000),
            )
            _global_emotion_vector_last_ts = _global_emotion_vector.ts_ms
        return _global_emotion_vector


def _apply_time_aware_decay(vector: EmotionVector, now_ms: int) -> EmotionVector:
    """Phase 1: 时间感知情绪衰减。

    根据距离上次更新的时间差，自动衰减情绪：
    - 1 分钟内：几乎不衰减
    - 5 分钟：衰减约 15%
    - 30 分钟：衰减约 50%
    - 2 小时+：衰减约 85%，接近中性

    可通过环境变量配置：
    - HOST_EMOTION_DECAY_GRACE_MS: 不衰减的宽限期（默认 30000 ms）
    - HOST_EMOTION_DECAY_MAX_RATE: 最大衰减率（默认 0.85）
    - HOST_EMOTION_DECAY_WINDOW_MIN: 达到最大衰减率的时间窗口分钟数（默认 120）
    """
    global _global_emotion_vector_last_ts
    elapsed_ms = max(0, now_ms - _global_emotion_vector_last_ts)
    grace_ms = int(os.environ.get("HOST_EMOTION_DECAY_GRACE_MS", "30000"))
    if elapsed_ms < grace_ms:
        return vector
    # 衰减率随时间非线性增长
    max_rate = float(os.environ.get("HOST_EMOTION_DECAY_MAX_RATE", "0.85"))
    window_min = float(os.environ.get("HOST_EMOTION_DECAY_WINDOW_MIN", "120"))
    elapsed_minutes = elapsed_ms / 60_000.0
    rate = min(max_rate, elapsed_minutes / window_min)
    decayed = vector.decay(rate=rate)
    logger.debug(
        "情绪时间衰减 elapsed=%.1f分钟 rate=%.3f valence=%.3f→%.3f arousal=%.3f→%.3f",
        elapsed_minutes, rate,
        vector.valence, decayed.valence,
        vector.arousal, decayed.arousal,
    )
    return decayed


def _update_persistent_emotion(new_vector: EmotionVector, now_ms: int) -> EmotionVector:
    """Phase 1: 更新全局持久化情绪向量。

    流程：先对旧情绪做时间衰减 → 再与新输入混合（weight=0.4）。
    这确保了：
    1. 长时间不聊天后情绪自然归于平静
    2. 新输入能显著影响当前情绪，但不会完全覆盖（保持连续性）

    线程安全：通过 _emotion_lock 保护读-改-写序列。
    """
    global _global_emotion_vector, _global_emotion_vector_last_ts
    with _emotion_lock:
        current = get_persistent_emotion_vector()
        # 先衰减
        decayed = _apply_time_aware_decay(current, now_ms)
        # 再混合新输入（weight=0.4，新输入占 40%）
        blended = decayed.blend(new_vector, weight=0.4)
        # 更新时间戳
        blended = EmotionVector(
            valence=blended.valence,
            arousal=blended.arousal,
            dominance=blended.dominance,
            ts_ms=now_ms,
        )
        _global_emotion_vector = blended
        _global_emotion_vector_last_ts = now_ms
    logger.debug(
        "持久化情绪更新 new_input=(%.3f,%.3f) decayed=(%.3f,%.3f) blended=(%.3f,%.3f)",
        new_vector.valence, new_vector.arousal,
        decayed.valence, decayed.arousal,
        blended.valence, blended.arousal,
    )
    return blended


def normalize_chat_emotion_context(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    source = dict(raw or {})
    state = normalize_hard_state(
        {
            "mood_state": source.get("mood_state"),
            "ts_ms": source.get("emotion_ts_ms"),
        }
    )
    hard_snapshot = build_hard_snapshot(state)
    allowed_emotion_tags = list(hard_snapshot.allowed_emotion_tags)

    emotion_tag = str(source.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
    if allowed_emotion_tags and emotion_tag not in allowed_emotion_tags:
        emotion_tag = allowed_emotion_tags[0]

    emotion_range_hint = str(source.get("emotion_range_hint") or "").strip() or hard_snapshot.emotion_range_hint
    emotion_intensity = _coerce_emotion_intensity(
        source.get("emotion_intensity"),
        default=_coerce_emotion_intensity(os.environ.get("HOST_DEFAULT_EMOTION_INTENSITY"), default=0.5),
    )
    try:
        emotion_ts_ms = int(source.get("emotion_ts_ms") or hard_snapshot.emotion_ts_ms)
    except (TypeError, ValueError):
        emotion_ts_ms = int(hard_snapshot.emotion_ts_ms)

    # Phase 2: 精细化 VAD 映射（16 种标签 + dominance 维度）
    valence, arousal, dominance = map_emotion_tag_to_vad(emotion_tag)
    input_vector = EmotionVector(
        valence=valence,
        arousal=arousal,
        dominance=dominance,
        ts_ms=emotion_ts_ms,
    )
    # Phase 1: 与持久化情绪混合（时间衰减 + blend），实现情绪连续性
    current_vector = _update_persistent_emotion(input_vector, emotion_ts_ms)

    # 记录到全局情感时间线
    _global_emotion_timeline.append(
        ts_ms=emotion_ts_ms,
        emotion_tag=emotion_tag,
        valence=current_vector.valence,
        arousal=current_vector.arousal,
        source="normalize_chat_emotion_context",
    )
    # 计算情感回声（共情响应提示）
    echo_result = compute_emotion_echo(
        emotion_tag,
        current_vector=current_vector,
        echo_weight=0.4,
    )
    # 评估主动情感触发器
    triggered_emotions = evaluate_emotion_triggers({
        "emotion_tag": emotion_tag,
        "emotion_intensity": emotion_intensity,
        "mood_state": str(hard_snapshot.mood_state),
    })

    # 方向 F：更新用户情感画像（每次对话时蒸馏）
    import datetime as _dt
    _hour_now = _dt.datetime.now().hour
    update_user_emotion_profile(
        emotion_tag=emotion_tag,
        valence=current_vector.valence,
        hour_of_day=_hour_now,
    )

    # 方向 C：跟踪上一次 emotion_tag（供 VTS 过渡帧计算）
    global _global_prev_emotion_tag
    with _emotion_lock:
        prev_tag = _global_prev_emotion_tag
        _global_prev_emotion_tag = emotion_tag

    return {
        "mood_state": str(hard_snapshot.mood_state),
        "emotion_tag": str(emotion_tag),
        "emotion_range_hint": str(emotion_range_hint),
        "emotion_intensity": emotion_intensity,
        "allowed_emotion_tags": allowed_emotion_tags,
        "emotion_ts_ms": emotion_ts_ms,
        # Phase 1+2：持久化 VAD 向量（含衰减 + 混合后的值）
        "vad_vector": current_vector.as_dict(),
        "emotion_echo": echo_result,
        "triggered_emotions": triggered_emotions,
        # Phase 1：内心独白（供 emotion_pulse 槽位使用）
        "inner_monologue": _vector_to_inner_monologue(current_vector),
        # 方向 F：用户情感画像摘要
        "user_emotion_profile": get_user_emotion_profile().to_summary(),
        # 方向 C：上一次 emotion_tag（供 VTS 渐变过渡帧计算）
        "_prev_emotion_tag": prev_tag,
    }


def resolve_chat_emotion_context(*, request_body: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    if isinstance(getattr(request_body, "emotion_context", None), Mapping):
        raw.update(dict(request_body.emotion_context))
    if getattr(request_body, "mood_state", None) is not None:
        raw["mood_state"] = request_body.mood_state
    if getattr(request_body, "emotion_tag", None) is not None:
        raw["emotion_tag"] = request_body.emotion_tag
    if getattr(request_body, "emotion_range_hint", None) is not None:
        raw["emotion_range_hint"] = request_body.emotion_range_hint
    if getattr(request_body, "emotion_intensity", None) is not None:
        raw["emotion_intensity"] = request_body.emotion_intensity
    if "mood_state" not in raw:
        raw["mood_state"] = str(os.environ.get("HOST_DEFAULT_MOOD_STATE") or "steady").strip().lower() or "steady"
    if "emotion_tag" not in raw:
        raw["emotion_tag"] = str(os.environ.get("HOST_DEFAULT_EMOTION_TAG") or "NEUTRAL").strip().upper() or "NEUTRAL"
    return normalize_chat_emotion_context(raw)


def build_chat_emotion_snapshot(
    *,
    emotion_context: Mapping[str, Any] | None,
    source: str = "chat_request",
    ts_ms: int | None = None,
) -> dict[str, Any]:
    normalized = normalize_chat_emotion_context(emotion_context)
    captured_ts_ms = _coerce_non_negative_int(ts_ms, default=int(time.time() * 1000))
    emotion_ts_ms = _coerce_non_negative_int(
        normalized.get("emotion_ts_ms"),
        default=captured_ts_ms,
    )
    return {
        "version": "chat_emotion_snapshot.v1",
        "source": str(source or "chat_request").strip() or "chat_request",
        "captured_ts_ms": captured_ts_ms,
        "emotion_ts_ms": emotion_ts_ms,
        "age_ms": max(0, captured_ts_ms - emotion_ts_ms),
        "emotion_context": {
            "mood_state": str(normalized.get("mood_state") or "steady"),
            "emotion_tag": str(normalized.get("emotion_tag") or "NEUTRAL"),
            "emotion_range_hint": str(normalized.get("emotion_range_hint") or ""),
            "emotion_intensity": _coerce_emotion_intensity(normalized.get("emotion_intensity"), default=0.5),
        },
        "allowed_emotion_tags": list(normalized.get("allowed_emotion_tags") or []),
    }


def build_chat_governance_snapshot(
    *,
    emotion_context: Mapping[str, Any],
    ts_ms: int,
) -> dict[str, Any]:
    normalized = normalize_chat_emotion_context(emotion_context)
    hard_state = normalize_hard_state(
        {
            "mood_state": normalized.get("mood_state"),
            "ts_ms": normalized.get("emotion_ts_ms", ts_ms),
        }
    )
    snapshot = build_governance_snapshot(
        hard_state,
        overrides={
            "mood_state": normalized.get("mood_state"),
            "emotion_tag": normalized.get("emotion_tag"),
            "emotion_intensity": normalized.get("emotion_intensity", 0.5),
            "emotion_ts_ms": normalized.get("emotion_ts_ms", ts_ms),
        },
    )
    snapshot["emotion_context"] = {
        "mood_state": normalized.get("mood_state"),
        "emotion_tag": normalized.get("emotion_tag"),
        "emotion_range_hint": normalized.get("emotion_range_hint"),
        "emotion_intensity": normalized.get("emotion_intensity", 0.5),
    }
    snapshot["emotion_snapshot"] = build_chat_emotion_snapshot(
        emotion_context=normalized,
        source="governance_snapshot",
        ts_ms=ts_ms,
    )
    return snapshot


# ---------------------------------------------------------------------------
# Phase 2: 精细化 VAD 映射——使用共享常量模块（唯一权威来源）
# ---------------------------------------------------------------------------

from host_brain.core.emotion_constants import (  # noqa: E402
    EMOTION_TAG_TO_VAD as _EMOTION_TAG_TO_VAD,
    get_vad as _get_vad,
)


def map_emotion_tag_to_vad(emotion_tag: str) -> tuple[float, float, float]:
    """将情感标签映射为 VAD 三维向量（Phase 2 精细化）。

    使用 emotion_constants.py 中的权威映射。

    Returns:
        (valence, arousal, dominance) 三元组
    """
    return _get_vad(emotion_tag)


# ---------------------------------------------------------------------------
# Phase 3: 内心独白——将 VAD 向量转为第一人称感受描述
# ---------------------------------------------------------------------------

def _vector_to_inner_monologue(v: EmotionVector) -> str:
    """将 VAD 向量转为细腻的第一人称内心独白（Phase 3）。

    根据 valence/arousal/dominance 的不同区间组合，生成差异化的感受描述，
    替代原来机械的 4 种 style_hint 模板。
    """
    val, aro, dom = v.valence, v.arousal, v.dominance

    # 高正面 + 高唤醒：兴奋/雀跃
    if val > 0.6 and aro > 0.6:
        return "现在心里特别开心，有种想要分享一切的冲动"
    # 高正面 + 低唤醒：温暖/安心
    if val > 0.5 and aro < 0.3:
        return "心里暖暖的，感觉很安心，这种平静的幸福感真好"
    # 中正面 + 中唤醒：小雀跃
    if val > 0.3 and aro > 0.3:
        return "心情不错，有些小雀跃，想和对方多聊聊"
    # 中正面 + 低唤醒：恬淡满足
    if val > 0.2 and aro <= 0.0:
        return "很舒服的状态，不需要太多言语，安静待着也很好"
    # 高负面 + 高唤醒：难受但克制
    if val < -0.5 and aro > 0.5:
        return "心里有些难受，但还在努力消化这种感觉，不想让对方担心"
    # 高负面 + 低唤醒：低落沉默
    if val < -0.5 and aro < 0.0:
        return "有点低落，不太想说话，只想安静待一会儿"
    # 中负面：有点沉
    if val < -0.3:
        return "心情有点沉，不过还好，也许聊聊会好一些"
    # 高唤醒 + 中性效价：紧张/期待
    if aro > 0.5 and abs(val) < 0.3:
        return "有些紧张，心跳比平时快一些，不知道在期待什么"
    # 低唤醒 + 中性效价：平静如水
    if aro < -0.3 and abs(val) < 0.3:
        return "很平静，像水面一样，思绪慢慢地流淌"
    # 高支配感：自信/掌控
    if dom > 0.4:
        return "状态不错，心里很有底，知道自己在做什么"
    # 低支配感：柔软/顺从
    if dom < -0.3:
        return "心态比较柔软，愿意倾听和配合对方"
    # 默认：平稳自然
    return "状态还不错，心里很平稳，可以自然地聊下去"


def _coerce_non_negative_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default)
    return max(0, parsed)


def _coerce_emotion_intensity(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(default)
    if parsed < 0.0:
        return 0.0
    if parsed > 1.0:
        return 1.0
    return float(parsed)


# ---------------------------------------------------------------------------
# 方向 D：情感日记——将 EmotionTimeline 每日摘要写入 EpisodicStore
# ---------------------------------------------------------------------------


def flush_emotion_diary(
    *,
    episodic_store: Any = None,
    force: bool = False,
    window_ms: int = 86_400_000,
) -> dict[str, Any] | None:
    """方向 D：将当天的情感时间线摘要写入 EpisodicStore 作为"情感日记"。

    条件（满足任一即写入）：
    - force=True（手动触发/调度器触发）
    - 距离上次写入 ≥ 12 小时 且 当前时间线有 ≥ 5 条记录

    线程安全：通过 _emotion_lock 保护 _last_diary_flush_ts 读写。

    Returns:
        写入结果字典，或 None（跳过）
    """
    global _last_diary_flush_ts
    now_ms_val = int(time.time() * 1000)

    # 获取时间线
    timeline = _global_emotion_timeline
    if timeline.entry_count < 3 and not force:
        return None

    with _emotion_lock:
        # 频率限制：12 小时内不重复写入
        elapsed_since_last = now_ms_val - _last_diary_flush_ts
        if not force and elapsed_since_last < 12 * 3600 * 1000:
            return None
        if not force and timeline.entry_count < 5:
            return None

    # 构建日记
    diary = timeline.build_daily_diary(window_ms=window_ms)
    diary_text = str(diary.get("diary_text") or "").strip()
    if not diary_text:
        return None

    # 写入 EpisodicStore（如果可用）
    result: dict[str, Any] = {"status": "NO_STORE", "diary": diary}
    if episodic_store is not None:
        try:
            from host_brain.memory.episodic import EpisodicStore  # noqa: PLC0415
            if isinstance(episodic_store, EpisodicStore):
                dominant = str(diary.get("dominant_emotion") or "NEUTRAL")
                avg_val = float(diary.get("avg_valence") or 0.0)
                op_result = episodic_store.write_memory(
                    text=f"[情感日记] {diary_text}",
                    evidence_refs=["emotion_diary_auto"],
                    decay_profile_id="SLOW",  # 日记用慢衰减
                    visibility="internal",
                    emotion_tag=dominant,
                    emotion_valence=max(-1.0, min(1.0, avg_val)),
                    emotion_arousal=0.3,
                )
                result = {
                    "status": "OK" if op_result.success else "FAILED",
                    "memory_id": op_result.memory_id,
                    "diary": diary,
                }
                logger.info(
                    "方向D: 情感日记写入 status=%s entries=%d dominant=%s avg_valence=%.3f",
                    result["status"], diary.get("total_entries", 0), dominant, avg_val,
                )
        except Exception as exc:
            result = {"status": "ERROR", "error": str(exc)[:100], "diary": diary}
            logger.warning("方向D: 情感日记写入异常: %s", exc)

    with _emotion_lock:
        _last_diary_flush_ts = now_ms_val
    return result


# ---------------------------------------------------------------------------
# 方向 F：用户情感画像——从 EmotionTimeline 蒸馏用户情感规律
# ---------------------------------------------------------------------------

class UserEmotionProfile:
    """方向 F：用户情感画像（进程级单例）。

    从每次对话的情感推理结果中逐步蒸馏：
    - 常见情感分布（哪种情感出现最多）
    - 平均效价趋势（整体偏积极还是消极）
    - 情感波动性（变化大还是稳定）
    - 高频情感时段（深夜聊天时是否更容易消极）
    """

    def __init__(self) -> None:
        # 情感标签计数（累计）
        self._tag_counts: dict[str, int] = {}
        # 累计效价值（用于计算平均）
        self._total_valence: float = 0.0
        self._total_entries: int = 0
        # 上次效价（用于计算波动性）
        self._last_valence: float = 0.0
        self._valence_changes: float = 0.0  # 效价变化绝对值累计
        # 时段分布（0~23 小时）
        self._hour_tag_counts: dict[int, dict[str, int]] = {}

    def update(
        self,
        *,
        emotion_tag: str,
        valence: float,
        hour_of_day: int | None = None,
    ) -> None:
        """更新用户情感画像（每次对话时调用）。"""
        tag = str(emotion_tag or "NEUTRAL").strip().upper()
        self._tag_counts[tag] = self._tag_counts.get(tag, 0) + 1
        self._total_valence += valence
        self._total_entries += 1
        # 波动性
        if self._total_entries > 1:
            self._valence_changes += abs(valence - self._last_valence)
        self._last_valence = valence
        # 时段分布
        if hour_of_day is not None:
            h = int(hour_of_day) % 24
            if h not in self._hour_tag_counts:
                self._hour_tag_counts[h] = {}
            self._hour_tag_counts[h][tag] = self._hour_tag_counts[h].get(tag, 0) + 1

    def to_summary(self) -> dict[str, Any]:
        """生成用户情感画像摘要（供 prompt 注入）。"""
        if self._total_entries == 0:
            return {"status": "NO_DATA", "total_interactions": 0}

        # Top 3 常见情感
        sorted_tags = sorted(self._tag_counts.items(), key=lambda x: x[1], reverse=True)
        top_emotions = [
            {"tag": tag, "count": count, "ratio": round(count / self._total_entries, 3)}
            for tag, count in sorted_tags[:3]
        ]

        # 平均效价
        avg_valence = self._total_valence / self._total_entries

        # 波动性（0.0 = 完全稳定，1.0 = 每次都剧烈变化）
        volatility = 0.0
        if self._total_entries > 1:
            volatility = min(1.0, self._valence_changes / (self._total_entries - 1))

        # 生成自然语言画像
        profile_hint = self._build_profile_hint(top_emotions, avg_valence, volatility)

        return {
            "status": "ACTIVE",
            "total_interactions": self._total_entries,
            "top_emotions": top_emotions,
            "avg_valence": round(avg_valence, 3),
            "volatility": round(volatility, 3),
            "profile_hint": profile_hint,
        }

    def _build_profile_hint(
        self,
        top_emotions: list[dict[str, Any]],
        avg_valence: float,
        volatility: float,
    ) -> str:
        """将用户情感画像转为自然语言提示。"""
        from host_brain.core.emotion_constants import EMOTION_TAG_ZH as _EMOTION_ZH  # noqa: PLC0415

        parts: list[str] = []

        # 整体基调
        if avg_valence > 0.3:
            parts.append("这位用户总体上是一个比较积极乐观的人")
        elif avg_valence < -0.3:
            parts.append("这位用户似乎最近心情不太好，需要更多温暖和关心")
        else:
            parts.append("这位用户的情绪比较平稳")

        # 常见情感
        if top_emotions:
            top_tag = top_emotions[0]["tag"]
            top_zh = _EMOTION_ZH.get(top_tag, top_tag)
            ratio = top_emotions[0].get("ratio", 0)
            if ratio > 0.5:
                parts.append(f"大部分时候处于{top_zh}的状态")
            elif ratio > 0.3:
                parts.append(f"经常处于{top_zh}的状态")

        # 波动性
        if volatility > 0.5:
            parts.append("情绪变化比较大，对情感话题可能比较敏感")
        elif volatility < 0.2:
            parts.append("情绪很稳定")

        return "，".join(parts) + "。" if parts else ""


# 全局用户情感画像单例
_user_emotion_profile: UserEmotionProfile | None = None


def get_user_emotion_profile() -> UserEmotionProfile:
    """获取全局用户情感画像单例（方向 F）。线程安全。"""
    global _user_emotion_profile
    with _emotion_lock:
        if _user_emotion_profile is None:
            _user_emotion_profile = UserEmotionProfile()
        return _user_emotion_profile


def update_user_emotion_profile(
    *,
    emotion_tag: str,
    valence: float,
    hour_of_day: int | None = None,
) -> None:
    """更新用户情感画像（方向 F，在每次对话时调用）。"""
    profile = get_user_emotion_profile()
    profile.update(
        emotion_tag=emotion_tag,
        valence=valence,
        hour_of_day=hour_of_day,
    )
