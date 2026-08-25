"""情感上下文处理模块。

负责情感状态的解析、归一化和治理快照构建。

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
from datetime import datetime
from typing import Any, Callable, Mapping

from host_brain.model_runtime.prompt_contract import resolve_prompt_contract_json
from host_brain.model_runtime.prompt_builder_utils import (
    _format_template_text,
    _mapping_section,
)
from host_brain.digital_life.text_affect_observer import (
    infer_bootstrap_emotion_from_text,
)
from host_brain.core.metabolism import (
    build_governance_snapshot,
    build_hard_snapshot,
    normalize_hard_state,
    # 修复审计问题 #6：接入孤立的情感模块
    EmotionVector,
    evaluate_emotion_triggers,
)
from host_brain.core.emotion_timeline import EmotionTimeline, compute_emotion_echo
from host_brain.core.emotion_constants import get_vad
from host_brain.core.runtime_helpers import _coerce_optional_float

logger = logging.getLogger(__name__)

def _load_profile_hint_templates() -> dict[str, Any]:
    raw = resolve_prompt_contract_json("emotion_context.user_profile_hints")
    return dict(raw) if isinstance(raw, Mapping) else {}


def _load_inner_monologue_templates() -> dict[str, Any]:
    raw = resolve_prompt_contract_json("emotion_context.inner_monologue_texts")
    return dict(raw) if isinstance(raw, Mapping) else {}


def _apply_time_aware_decay(
    vector: EmotionVector,
    now_ms: int,
    *,
    last_update_ms: int,
) -> EmotionVector:
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
    elapsed_ms = max(0, int(now_ms) - int(last_update_ms))
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


def normalize_chat_emotion_context(
    raw: Mapping[str, Any] | None,
    *,
    runtime: "HostEmotionRuntime | None" = None,
) -> dict[str, Any]:
    """Normalize one emotion payload.

    Without ``runtime`` this is deliberately pure and is safe for replay,
    prompt rendering and read projections.  Only the Host admission resolver
    supplies its app-local runtime and therefore advances continuity state.
    """

    source = dict(raw or {})
    # ``affective_life`` is an internal, canonical hand-off created by
    # AffectFusionService. An external ChatRequest may never spoof it to set
    # Natsume's self-affect or bypass the Life journal.
    if bool(source.get("_affective_life_internal")) and isinstance(
        source.get("affective_life"),
        Mapping,
    ):
        return _normalize_affective_life_context(source)
    source.pop("_affective_life_internal", None)
    source.pop("affective_life", None)
    state = normalize_hard_state(
        {
            "mood_state": source.get("mood_state"),
            "ts_ms": source.get("emotion_ts_ms"),
        }
    )
    hard_snapshot = build_hard_snapshot(state)
    allowed_emotion_tags = list(hard_snapshot.allowed_emotion_tags)

    emotion_tag = _normalize_runtime_emotion_tag(source.get("emotion_tag")) or "NEUTRAL"
    inference_source = str(source.get("emotion_inference_source") or "").strip()
    user_emotion_inferred = inference_source.startswith("text_bootstrap")
    if allowed_emotion_tags and emotion_tag not in allowed_emotion_tags and not user_emotion_inferred:
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
    valence, arousal, dominance = get_vad(emotion_tag)
    input_vector = EmotionVector(
        valence=valence,
        arousal=arousal,
        dominance=dominance,
        ts_ms=emotion_ts_ms,
    )
    current_vector = input_vector
    prev_tag: str | None = None
    profile_summary: dict[str, Any] = {
        "status": "NO_DATA",
        "total_interactions": 0,
    }
    if runtime is not None:
        current_vector, prev_tag, profile_summary = runtime.ingest(
            input_vector=input_vector,
            emotion_tag=emotion_tag,
            emotion_intensity=emotion_intensity,
            mood_state=str(hard_snapshot.mood_state),
            emotion_ts_ms=emotion_ts_ms,
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

    return {
        "mood_state": str(hard_snapshot.mood_state),
        "emotion_tag": str(emotion_tag),
        "emotion_range_hint": str(emotion_range_hint),
        "emotion_intensity": emotion_intensity,
        "emotion_inference_source": inference_source or "explicit_or_default",
        "emotion_inference_confidence": _coerce_emotion_intensity(
            source.get("emotion_inference_confidence"),
            default=1.0 if str(source.get("emotion_inference_source") or "") == "explicit" else 0.0,
        ),
        "emotion_inference_degraded": bool(source.get("emotion_inference_degraded", False)),
        "emotion_bootstrap_reason_codes": [
            str(item)
            for item in list(source.get("emotion_bootstrap_reason_codes") or [])
            if str(item or "").strip()
        ][:6],
        "allowed_emotion_tags": allowed_emotion_tags,
        "emotion_ts_ms": emotion_ts_ms,
        # Phase 1+2：持久化 VAD 向量（含衰减 + 混合后的值）
        "vad_vector": current_vector.as_dict(),
        "emotion_echo": echo_result,
        "triggered_emotions": triggered_emotions,
        # Phase 1：内心独白（供 emotion_pulse 槽位使用）
        "inner_monologue": _vector_to_inner_monologue(current_vector),
        # 方向 F：用户情感画像摘要
        "user_emotion_profile": profile_summary,
        # 方向 C：上一次 emotion_tag（供 VTS 渐变过渡帧计算）
        "_prev_emotion_tag": prev_tag,
    }


def _normalize_affective_life_context(source: Mapping[str, Any]) -> dict[str, Any]:
    """Render the durable self-affect head without advancing legacy runtime.

    This is the DL-02 boundary that prevents an observed user emotion from
    being blended into ``HostEmotionRuntime``. The bridge fields retain their
    historical names for downstream Governor/prompt consumers, but every one
    is derived from the canonical *self* head supplied by the Life journal.
    """

    life = dict(source.get("affective_life") or {})
    self_affect = dict(life.get("self_affect") or {})
    mood = dict(self_affect.get("mood") or {})
    components = list(self_affect.get("components") or [])
    top = dict(components[0] or {}) if components and isinstance(components[0], Mapping) else {}
    state = normalize_hard_state({"mood_state": mood.get("label")})
    hard_snapshot = build_hard_snapshot(state)
    allowed_emotion_tags = list(hard_snapshot.allowed_emotion_tags)
    emotion_tag = _normalize_runtime_emotion_tag(source.get("emotion_tag")) or "CALM"
    if allowed_emotion_tags and emotion_tag not in allowed_emotion_tags:
        emotion_tag = allowed_emotion_tags[0]
    emotion_intensity = _coerce_emotion_intensity(
        source.get("emotion_intensity"),
        default=_coerce_emotion_intensity(top.get("intensity"), default=0.18),
    )
    try:
        updated_at_ms = int(mood.get("updated_at_ms") or self_affect.get("last_integrated_ms") or 0)
    except (TypeError, ValueError):
        updated_at_ms = 0
    vector = EmotionVector(
        valence=max(-1.0, min(1.0, float(mood.get("valence") or 0.18))),
        arousal=max(-1.0, min(1.0, (float(mood.get("arousal") or 0.24) * 2.0) - 1.0)),
        dominance=0.0,
        ts_ms=max(0, updated_at_ms),
    )
    return {
        "mood_state": str(mood.get("label") or "calm"),
        "emotion_tag": emotion_tag,
        "emotion_range_hint": "canonical_self_affect",
        "emotion_intensity": emotion_intensity,
        "emotion_inference_source": "affective_life_fusion",
        "emotion_inference_confidence": 1.0,
        "emotion_inference_degraded": str(life.get("path") or "") == "FAST_FALLBACK",
        "emotion_bootstrap_reason_codes": [
            str(item)
            for item in list(life.get("reason_codes") or [])
            if str(item or "").strip()
        ][:6],
        "allowed_emotion_tags": allowed_emotion_tags,
        "emotion_ts_ms": max(0, updated_at_ms),
        "vad_vector": vector.as_dict(),
        "emotion_echo": compute_emotion_echo(
            emotion_tag,
            current_vector=vector,
            echo_weight=0.0,
        ),
        "triggered_emotions": evaluate_emotion_triggers(
            {
                "emotion_tag": emotion_tag,
                "emotion_intensity": emotion_intensity,
                "mood_state": str(mood.get("label") or "calm"),
            }
        ),
        "inner_monologue": _vector_to_inner_monologue(vector),
        "user_emotion_profile": {
            "status": "AFFECTIVE_FUSION",
            "total_interactions": 0,
        },
        "_prev_emotion_tag": None,
        "_affective_life_internal": True,
        "affective_life": life,
    }


def _normalize_runtime_emotion_tag(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    from host_brain.core.emotion_constants import normalize_emotion_tag  # noqa: PLC0415
    return normalize_emotion_tag(raw)


def _extract_request_text(request_body: Any) -> str:
    for key in ("message", "text", "content", "input_text", "user_message"):
        value = getattr(request_body, key, None)
        if value not in (None, ""):
            return str(value or "").strip()
    return ""


def resolve_chat_emotion_context(
    *,
    request_body: Any,
    runtime: "HostEmotionRuntime | None" = None,
    affect_fusion_service: Any | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Resolve one Host input through canonical fusion or a legacy fallback.

    A production caller with ``AffectFusionService`` must not even resolve the
    compatibility ``HostEmotionRuntime``: doing so would keep a second,
    mutable emotion timeline alive on the canonical chat path.  The runtime is
    still required for explicitly legacy callers that have no Life authority.
    """

    if affect_fusion_service is not None:
        resolver = getattr(affect_fusion_service, "resolve_request", None)
        if not callable(resolver):
            raise RuntimeError("AFFECT_FUSION_SERVICE_NOT_INJECTED")
        fused = resolver(
            request_body=request_body,
            request_id=request_id,
        )
        as_context = getattr(fused, "as_emotion_context", None)
        if not callable(as_context):
            raise RuntimeError("AFFECT_FUSION_RESULT_INVALID")
        return normalize_chat_emotion_context(as_context())
    if not isinstance(runtime, HostEmotionRuntime):
        raise RuntimeError("HOST_EMOTION_RUNTIME_NOT_INJECTED")
    raw: dict[str, Any] = {}
    if isinstance(getattr(request_body, "emotion_context", None), Mapping):
        raw.update(dict(request_body.emotion_context))
    explicit_emotion_tag = _normalize_runtime_emotion_tag(raw.get("emotion_tag"))
    if getattr(request_body, "mood_state", None) is not None:
        raw["mood_state"] = request_body.mood_state
    if getattr(request_body, "emotion_tag", None) is not None:
        raw["emotion_tag"] = request_body.emotion_tag
        explicit_emotion_tag = _normalize_runtime_emotion_tag(request_body.emotion_tag)
    if getattr(request_body, "emotion_range_hint", None) is not None:
        raw["emotion_range_hint"] = request_body.emotion_range_hint
    if getattr(request_body, "emotion_intensity", None) is not None:
        raw["emotion_intensity"] = request_body.emotion_intensity

    user_text = _extract_request_text(request_body)
    current_tag = _normalize_runtime_emotion_tag(raw.get("emotion_tag"))
    current_confidence = _coerce_optional_float(
        raw.get("emotion_inference_confidence")
        or raw.get("inferred_confidence")
        or raw.get("confidence")
    )
    should_bootstrap = (
        explicit_emotion_tag is None
        or current_tag in (None, "NEUTRAL")
        or (current_tag == "NEUTRAL" and (current_confidence is None or current_confidence < 0.55))
    )
    if should_bootstrap:
        bootstrap = infer_bootstrap_emotion_from_text(user_text)
        if bootstrap.applied:
            raw["emotion_tag"] = bootstrap.emotion_tag
            raw.setdefault("emotion_intensity", bootstrap.emotion_intensity)
            raw["emotion_inference_source"] = bootstrap.source
            raw["emotion_inference_confidence"] = bootstrap.confidence
            raw["emotion_inference_degraded"] = False
            raw["emotion_bootstrap_reason_codes"] = list(bootstrap.reason_codes)
        elif current_tag in (None, "NEUTRAL"):
            raw.setdefault("emotion_tag", "NEUTRAL")
            raw.setdefault("emotion_intensity", 0.35)
            raw.setdefault("emotion_inference_source", "default_neutral")
            raw.setdefault("emotion_inference_confidence", 0.0)
            raw["emotion_inference_degraded"] = True
            raw["emotion_bootstrap_reason_codes"] = ["NO_BOOTSTRAP_SIGNAL"]
    elif current_tag:
        raw.setdefault("emotion_inference_source", "explicit")
        raw.setdefault("emotion_inference_confidence", 1.0)
        raw.setdefault("emotion_inference_degraded", False)
    if "mood_state" not in raw:
        raw["mood_state"] = str(os.environ.get("HOST_DEFAULT_MOOD_STATE") or "steady").strip().lower() or "steady"
    if "emotion_tag" not in raw:
        raw["emotion_tag"] = str(os.environ.get("HOST_DEFAULT_EMOTION_TAG") or "NEUTRAL").strip().upper() or "NEUTRAL"
    return normalize_chat_emotion_context(raw, runtime=runtime)


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
# Phase 3: 内心独白——将 VAD 向量转为第一人称感受描述
# ---------------------------------------------------------------------------

def _vector_to_inner_monologue(v: EmotionVector) -> str:
    """将 VAD 向量转为细腻的第一人称内心独白（Phase 3）。

    根据 valence/arousal/dominance 的不同区间组合，生成差异化的感受描述，
    替代原来机械的 4 种 style_hint 模板。
    """
    val, aro, dom = v.valence, v.arousal, v.dominance
    templates = _load_inner_monologue_templates()
    case_templates = _mapping_section(templates, "cases")

    # 高正面 + 高唤醒：兴奋/雀跃
    if val > 0.6 and aro > 0.6:
        return str(case_templates.get("high_positive_high_arousal") or "现在心里特别开心，有种想要分享一切的冲动")
    # 高正面 + 低唤醒：温暖/安心
    if val > 0.5 and aro < 0.3:
        return str(case_templates.get("high_positive_low_arousal") or "心里暖暖的，感觉很安心，这种平静的幸福感真好")
    # 中正面 + 中唤醒：小雀跃
    if val > 0.3 and aro > 0.3:
        return str(case_templates.get("mid_positive_mid_arousal") or "心情不错，有些小雀跃，想和对方多聊聊")
    # 中正面 + 低唤醒：恬淡满足
    if val > 0.2 and aro <= 0.0:
        return str(case_templates.get("mid_positive_low_arousal") or "很舒服的状态，不需要太多言语，安静待着也很好")
    # 高负面 + 高唤醒：难受但克制
    if val < -0.5 and aro > 0.5:
        return str(case_templates.get("high_negative_high_arousal") or "心里有些难受，但还在努力消化这种感觉，不想让对方担心")
    # 高负面 + 低唤醒：低落沉默
    if val < -0.5 and aro < 0.0:
        return str(case_templates.get("high_negative_low_arousal") or "有点低落，不太想说话，只想安静待一会儿")
    # 中负面：有点沉
    if val < -0.3:
        return str(case_templates.get("mid_negative") or "心情有点沉，不过还好，也许聊聊会好一些")
    # 高唤醒 + 中性效价：紧张/期待
    if aro > 0.5 and abs(val) < 0.3:
        return str(case_templates.get("high_arousal_neutral_valence") or "有些紧张，心跳比平时快一些，不知道在期待什么")
    # 低唤醒 + 中性效价：平静如水
    if aro < -0.3 and abs(val) < 0.3:
        return str(case_templates.get("low_arousal_neutral_valence") or "很平静，像水面一样，思绪慢慢地流淌")
    # 高支配感：自信/掌控
    if dom > 0.4:
        return str(case_templates.get("high_dominance") or "状态不错，心里很有底，知道自己在做什么")
    # 低支配感：柔软/顺从
    if dom < -0.3:
        return str(case_templates.get("low_dominance") or "心态比较柔软，愿意倾听和配合对方")
    # 默认：平稳自然
    return str(templates.get("default") or "状态还不错，心里很平稳，可以自然地聊下去")


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
    runtime: "HostEmotionRuntime",
    episodic_store: Any = None,
    force: bool = False,
    window_ms: int = 86_400_000,
) -> dict[str, Any] | None:
    if not isinstance(runtime, HostEmotionRuntime):
        raise RuntimeError("HOST_EMOTION_RUNTIME_NOT_INJECTED")
    return runtime.flush_emotion_diary(
        episodic_store=episodic_store,
        force=force,
        window_ms=window_ms,
    )


# ---------------------------------------------------------------------------
# 方向 F：用户情感画像——从 EmotionTimeline 蒸馏用户情感规律
# ---------------------------------------------------------------------------

class UserEmotionProfile:
    """方向 F：由一个 Host emotion runtime 独占的用户情感画像。

    从每次对话的情感推理结果中逐步蒸馏：
    - 常见情感分布（哪种情感出现最多）
    - 平均效价趋势（整体偏积极还是消极）
    - 情感波动性（变化大还是稳定）
    - 高频情感时段（深夜聊天时是否更容易消极）
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
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
        with self._lock:
            tag = str(emotion_tag or "NEUTRAL").strip().upper()
            self._tag_counts[tag] = self._tag_counts.get(tag, 0) + 1
            self._total_valence += valence
            self._total_entries += 1
            if self._total_entries > 1:
                self._valence_changes += abs(valence - self._last_valence)
            self._last_valence = valence
            if hour_of_day is not None:
                h = int(hour_of_day) % 24
                if h not in self._hour_tag_counts:
                    self._hour_tag_counts[h] = {}
                self._hour_tag_counts[h][tag] = (
                    self._hour_tag_counts[h].get(tag, 0) + 1
                )

    def to_summary(self) -> dict[str, Any]:
        """生成用户情感画像摘要（供 prompt 注入）。"""
        with self._lock:
            total_entries = self._total_entries
            if total_entries == 0:
                return {"status": "NO_DATA", "total_interactions": 0}
            sorted_tags = sorted(
                self._tag_counts.items(),
                key=lambda x: x[1],
                reverse=True,
            )
            top_emotions = [
                {
                    "tag": tag,
                    "count": count,
                    "ratio": round(count / total_entries, 3),
                }
                for tag, count in sorted_tags[:3]
            ]
            avg_valence = self._total_valence / total_entries
            volatility = 0.0
            if total_entries > 1:
                volatility = min(
                    1.0,
                    self._valence_changes / (total_entries - 1),
                )

        # 生成自然语言画像
        profile_hint = self._build_profile_hint(top_emotions, avg_valence, volatility)

        return {
            "status": "ACTIVE",
            "total_interactions": total_entries,
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

        templates = _load_profile_hint_templates()
        tone_templates = _mapping_section(templates, "tone")
        emotion_templates = _mapping_section(templates, "emotion_frequency")
        volatility_templates = _mapping_section(templates, "volatility")
        output_templates = _mapping_section(templates, "output")
        parts: list[str] = []

        # 整体基调
        if avg_valence > 0.3:
            parts.append(
                str(
                    tone_templates.get(
                        "positive",
                        "这位用户总体上是一个比较积极乐观的人",
                    )
                )
            )
        elif avg_valence < -0.3:
            parts.append(
                str(
                    tone_templates.get(
                        "negative",
                        "这位用户似乎最近心情不太好，需要更多温暖和关心",
                    )
                )
            )
        else:
            parts.append(
                str(
                    tone_templates.get(
                        "neutral",
                        "这位用户的情绪比较平稳",
                    )
                )
            )

        # 常见情感
        if top_emotions:
            top_tag = top_emotions[0]["tag"]
            top_zh = _EMOTION_ZH.get(top_tag, top_tag)
            ratio = top_emotions[0].get("ratio", 0)
            if ratio > 0.5:
                parts.append(
                    _format_template_text(
                        emotion_templates.get(
                            "dominant",
                            "大部分时候处于{emotion}的状态",
                        ),
                        emotion=top_zh,
                    )
                )
            elif ratio > 0.3:
                parts.append(
                    _format_template_text(
                        emotion_templates.get(
                            "frequent",
                            "经常处于{emotion}的状态",
                        ),
                        emotion=top_zh,
                    )
                )

        # 波动性
        if volatility > 0.5:
            parts.append(
                str(
                    volatility_templates.get(
                        "high",
                        "情绪变化比较大，对情感话题可能比较敏感",
                    )
                )
            )
        elif volatility < 0.2:
            parts.append(
                str(
                    volatility_templates.get(
                        "low",
                        "情绪很稳定",
                    )
                )
            )

        if not parts:
            return ""
        joiner = str(output_templates.get("joiner", "，"))
        suffix = str(output_templates.get("suffix", "。"))
        return joiner.join(parts) + suffix


class HostEmotionRuntime:
    """App-local owner for mutable affect continuity and profile state."""

    def __init__(
        self,
        *,
        timeline_provider: Callable[[], Any] | None = None,
        clock_ms: Callable[[], int] | None = None,
        hour_provider: Callable[[], int] | None = None,
    ) -> None:
        owned_timeline = EmotionTimeline(max_entries=200)
        self._timeline_provider = timeline_provider or (lambda: owned_timeline)
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._hour_provider = hour_provider or (lambda: datetime.now().hour)
        self._lock = threading.RLock()
        self._emotion_vector: EmotionVector | None = None
        self._emotion_vector_last_ts = 0
        self._previous_emotion_tag: str | None = None
        self._last_diary_flush_ts = 0
        self._profile = UserEmotionProfile()

    def require_timeline(self) -> Any:
        timeline = self._timeline_provider()
        if not callable(getattr(timeline, "append", None)):
            raise RuntimeError("HOST_EMOTION_TIMELINE_NOT_INJECTED")
        return timeline

    def user_emotion_profile(self) -> UserEmotionProfile:
        return self._profile

    def persistent_emotion_vector(self) -> EmotionVector:
        with self._lock:
            if self._emotion_vector is None:
                timestamp = int(self._clock_ms())
                self._emotion_vector = EmotionVector(
                    valence=0.1,
                    arousal=0.2,
                    dominance=0.0,
                    ts_ms=timestamp,
                )
                self._emotion_vector_last_ts = timestamp
            return self._emotion_vector

    def ingest(
        self,
        *,
        input_vector: EmotionVector,
        emotion_tag: str,
        emotion_intensity: float,
        mood_state: str,
        emotion_ts_ms: int,
    ) -> tuple[EmotionVector, str | None, dict[str, Any]]:
        del emotion_intensity, mood_state
        with self._lock:
            current = self.persistent_emotion_vector()
            decayed = _apply_time_aware_decay(
                current,
                emotion_ts_ms,
                last_update_ms=self._emotion_vector_last_ts,
            )
            blended_raw = decayed.blend(input_vector, weight=0.4)
            blended = EmotionVector(
                valence=blended_raw.valence,
                arousal=blended_raw.arousal,
                dominance=blended_raw.dominance,
                ts_ms=int(emotion_ts_ms),
            )
            self._emotion_vector = blended
            self._emotion_vector_last_ts = int(emotion_ts_ms)
            previous = self._previous_emotion_tag
            self._previous_emotion_tag = str(emotion_tag)
            self.require_timeline().append(
                ts_ms=int(emotion_ts_ms),
                emotion_tag=str(emotion_tag),
                valence=blended.valence,
                arousal=blended.arousal,
                source="host_emotion_runtime",
            )
            self._profile.update(
                emotion_tag=str(emotion_tag),
                valence=blended.valence,
                hour_of_day=int(self._hour_provider()),
            )
            summary = self._profile.to_summary()
        logger.debug(
            "Host 情绪更新 input=(%.3f,%.3f) decayed=(%.3f,%.3f) "
            "blended=(%.3f,%.3f)",
            input_vector.valence,
            input_vector.arousal,
            decayed.valence,
            decayed.arousal,
            blended.valence,
            blended.arousal,
        )
        return blended, previous, summary

    def flush_emotion_diary(
        self,
        *,
        episodic_store: Any = None,
        force: bool = False,
        window_ms: int = 86_400_000,
    ) -> dict[str, Any] | None:
        now_ms_val = int(self._clock_ms())
        timeline = self.require_timeline()
        if timeline.entry_count < 3 and not force:
            return None
        with self._lock:
            elapsed_since_last = now_ms_val - self._last_diary_flush_ts
            if not force and elapsed_since_last < 12 * 3600 * 1000:
                return None
            if not force and timeline.entry_count < 5:
                return None

        diary = timeline.build_daily_diary(window_ms=window_ms)
        diary_text = str(diary.get("diary_text") or "").strip()
        if not diary_text:
            return None
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
                        decay_profile_id="SLOW",
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
            except Exception as exc:
                result = {
                    "status": "ERROR",
                    "error": str(exc)[:100],
                    "diary": diary,
                }
                logger.warning("方向D: 情感日记写入异常: %s", exc)
        with self._lock:
            self._last_diary_flush_ts = now_ms_val
        return result
