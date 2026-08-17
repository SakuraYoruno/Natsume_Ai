"""供 Digital Life 准入使用的保守、纯文本情感观察器。

模块有意不依赖 Host 应用组合。它只对当前用户文本给出低置信度观察，并非
Natsume self-affect 的 authority；后续融合仍可保留不确定性或完全忽略它。
"""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class EmotionBootstrapResult:
    """启发式观察结果；``applied`` 仅表示足够作为候选输入，不代表状态已变更。"""
    emotion_tag: str
    emotion_intensity: float
    confidence: float
    source: str
    reason_codes: tuple[str, ...] = ()
    emotion_distribution: tuple[tuple[str, float], ...] = ()

    @property
    def applied(self) -> bool:
        """以保守置信阈值判断是否存在非中性候选信号。"""
        return bool(
            self.emotion_tag
            and self.emotion_tag != "NEUTRAL"
            and self.confidence >= 0.45
        )


_BOOTSTRAP_EMOTION_MARKERS: dict[str, tuple[str, ...]] = {
    "SAD": (
        "难受", "难过", "低落", "崩溃", "委屈", "想哭", "哭了", "撑不住", "不想动",
        "没力气", "好累", "累死", "心累", "失落", "孤独", "没人懂",
        "不太顺", "不顺心", "诸事不顺", "提不起劲", "没精神", "不太想说",
        "不想说", "就这样吧", "今天先到这里",
    ),
    "ANGRY": (
        "生气", "烦死", "烦躁", "气死", "火大", "别烦", "滚", "闭嘴", "讨厌你",
        "你很烦", "不想理你", "离我远点",
    ),
    "FRUSTRATED": (
        "很烦", "真烦", "卡住", "搞不定", "烂", "垃圾", "离谱", "奇怪", "不通顺",
        "没眼看", "惨不忍睹", "怎么又", "为什么还",
    ),
    "ANXIOUS": (
        "焦虑", "慌", "紧张", "害怕", "怕", "担心", "不安", "完了", "怎么办",
        "来不及", "压力好大", "不踏实", "心里没底", "悬着",
    ),
    "HAPPY": (
        "开心", "高兴", "舒服了", "好多了", "不错", "可以了", "好耶", "太好了",
        "满意", "喜欢这个", "好消息", "终于出来", "终于拿到", "终于过了",
        "发布成功", "跑通了", "搞定了", "值得庆祝", "像做梦",
    ),
    "GRATEFUL": ("谢谢", "谢了", "多谢", "辛苦", "帮大忙", "感谢"),
    "LOVING": ("喜欢你", "想你", "抱抱", "贴贴", "爱你"),
    "CONFUSED": (
        "看不懂", "没懂", "不明白", "什么意思", "啥意思", "为什么", "怎么回事",
    ),
}
_BOOTSTRAP_INTENSIFIERS = (
    "很", "特别", "真的", "太", "超级", "非常", "有点", "一点",
)


def infer_bootstrap_emotion_from_text(text: str) -> EmotionBootstrapResult:
    """从一轮文本推断保守的用户情感观察。

    词标记只产生有限分布与原因码，不把原文输出或持久化。空文本、无标记和
    低置信情况统一退化为中性候选，避免单个词语被解释成用户确定的情绪事实。
    """

    normalized = _normalize_bootstrap_text(text)
    if not normalized:
        return EmotionBootstrapResult(
            emotion_tag="NEUTRAL",
            emotion_intensity=0.35,
            confidence=0.0,
            source="text_bootstrap_empty",
            reason_codes=("EMPTY_TEXT",),
            emotion_distribution=(("NEUTRAL", 0.35),),
        )

    scores: dict[str, float] = {}
    reasons: dict[str, list[str]] = {}
    for tag, markers in _BOOTSTRAP_EMOTION_MARKERS.items():
        for marker in markers:
            if marker and marker in normalized:
                scores[tag] = scores.get(tag, 0.0) + _marker_weight(
                    marker,
                    normalized,
                )
                reasons.setdefault(tag, []).append(f"marker:{marker}")

    if not scores:
        return EmotionBootstrapResult(
            emotion_tag="NEUTRAL",
            emotion_intensity=0.35,
            confidence=0.0,
            source="text_bootstrap_no_signal",
            reason_codes=("NO_MARKER",),
            emotion_distribution=(("NEUTRAL", 0.35),),
        )

    best_tag, best_score = max(scores.items(), key=lambda item: item[1])
    if "怎么办" in normalized and best_tag not in {"ANXIOUS", "CONFUSED"}:
        best_tag = "ANXIOUS"
        best_score = max(best_score, scores.get("ANXIOUS", 0.0) + 0.35)
        reasons.setdefault(best_tag, []).append("phrase:怎么办")

    confidence = min(0.92, 0.46 + best_score * 0.16)
    distribution = {
        tag: round(min(0.95, 0.55 + score * 0.22), 4)
        for tag, score in scores.items()
    }
    intensity = distribution[best_tag]
    if any(word in normalized for word in _BOOTSTRAP_INTENSIFIERS):
        confidence = min(0.95, confidence + 0.05)
        intensity = min(0.95, intensity + 0.08)

    if confidence < 0.45:
        return EmotionBootstrapResult(
            emotion_tag="NEUTRAL",
            emotion_intensity=0.35,
            confidence=round(confidence, 4),
            source="text_bootstrap_low_confidence",
            reason_codes=tuple(reasons.get(best_tag, ())) or ("LOW_CONFIDENCE",),
            emotion_distribution=(("NEUTRAL", 0.35),),
        )

    return EmotionBootstrapResult(
        emotion_tag=best_tag,
        emotion_intensity=round(intensity, 4),
        confidence=round(confidence, 4),
        source="text_bootstrap_fallback",
        reason_codes=tuple(reasons.get(best_tag, ()))[:4],
        emotion_distribution=tuple(
            sorted(distribution.items(), key=lambda item: (-item[1], item[0]))
        )[:4],
    )


def _normalize_bootstrap_text(text: str) -> str:
    """仅为匹配标记消除空白与大小写；不改变任何外部可见内容。"""
    return re.sub(r"\s+", "", str(text or "").strip().lower())


def _marker_weight(marker: str, text: str) -> float:
    """给更具体或带强化词的标记适度加权，权重仍受最终置信上限约束。"""
    weight = 1.0
    if len(marker) >= 3:
        weight += 0.15
    if any(prefix + marker in text for prefix in _BOOTSTRAP_INTENSIFIERS):
        weight += 0.35
    return weight


__all__ = ["EmotionBootstrapResult", "infer_bootstrap_emotion_from_text"]
