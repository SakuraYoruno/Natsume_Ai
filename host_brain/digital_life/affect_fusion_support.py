"""AffectFusion 的纯规范化工具。

生产服务与 canonical Life writer 位于 ``affect_fusion``；本模块只负责有界、
确定性的值转换、摘要引用与隐私过滤，不拥有情感状态或写入 authority。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
from typing import Any

from host_brain.core.emotion_constants import get_vad, normalize_emotion_tag
from natsume_contracts.life import UserAffectObservationV1


_RAW_MATERIAL_KEYS = frozenset(
    {
        "assistant_reply",
        "audio",
        "audio_base64",
        "image",
        "image_base64",
        "model_chain",
        "raw_audio",
        "raw_image",
        "raw_text",
        "transcript",
        "user_message",
        "video",
    }
)
_MODALITY_ALIASES = {
    "TEXT": "TEXT",
    "SELF_REPORT": "SELF_REPORT",
    "SELF-REPORT": "SELF_REPORT",
    "ACOUSTIC": "ACOUSTIC",
    "VOICE": "ACOUSTIC",
    "AUDIO": "ACOUSTIC",
    "VISUAL": "VISUAL",
    "FACE": "VISUAL",
    "CAMERA": "VISUAL",
}


def _digest(value: Any) -> str:
    """以排序 JSON 派生稳定摘要，供证据/幂等引用使用且不保存原始值。"""
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _text(value: Any, *, default: str = "", limit: int = 240) -> str:
    """规范化并截断文本字段，缺失时使用固定默认值。"""
    normalized = str(value or "").strip()
    if not normalized:
        return default
    return normalized[:limit]


def _clamp(value: Any, *, default: float = 0.0) -> float:
    """将概率/置信度约束为 [0, 1]，非数值退回默认值。"""
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        resolved = float(default)
    return round(max(0.0, min(1.0, resolved)), 6)


def _signed(value: Any, *, default: float = 0.0) -> float:
    """将 valence 等有符号量约束为 [-1, 1]。"""
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        resolved = float(default)
    return round(max(-1.0, min(1.0, resolved)), 6)


def _opaque_ref(prefix: str, value: Any) -> str:
    """由值摘要生成不透明引用，让下游可关联证据而不可读取原始材料。"""
    return f"{prefix}://sha256/{_digest(value)}"


def _safe_mapping(value: Any) -> dict[str, Any]:
    """只接受 mapping 并复制，非对象按空输入处理。"""
    return dict(value) if isinstance(value, Mapping) else {}


def _safe_sequence(value: Any) -> list[Mapping[str, Any]]:
    """仅保留结构化对象序列；字符串与字节流绝不被当作可融合观察。"""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _contains_raw_material(value: Any) -> bool:
    """递归识别原文、媒体和模型链字段，供入口在持久化前剔除。"""
    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            if str(raw_key or "").strip().lower() in _RAW_MATERIAL_KEYS:
                return True
            if _contains_raw_material(nested):
                return True
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_raw_material(item) for item in value)
    return False


def _normalise_modality(value: Any) -> str | None:
    """将受支持别名映射为固定模态枚举，未知来源不进入融合。"""
    key = _text(value, limit=48).upper().replace(" ", "_")
    return _MODALITY_ALIASES.get(key)


def _distribution(value: Any, *, fallback_tag: str = "NEUTRAL") -> dict[str, float]:
    """规范化情绪分布并提供中性回退，键排序保证同语义输入稳定。"""
    source = _safe_mapping(value)
    result: dict[str, float] = {}
    for raw_tag in sorted(source, key=lambda item: str(item)):
        tag = normalize_emotion_tag(str(raw_tag))
        if not tag:
            continue
        result[tag] = _clamp(source[raw_tag])
    if result:
        return result
    return {normalize_emotion_tag(fallback_tag) or "NEUTRAL": 1.0}


def _distribution_vad(distribution: Mapping[str, float]) -> tuple[float, float]:
    """从受限分布计算加权 VAD 摘要，不作额外情绪推断。"""
    total = sum(_clamp(weight) for weight in distribution.values())
    if total <= 0:
        return 0.0, 0.5
    valence = 0.0
    arousal = 0.0
    for tag, weight in distribution.items():
        tag_valence, tag_arousal, _ = get_vad(str(tag))
        normalized = _clamp(weight) / total
        valence += tag_valence * normalized
        arousal += ((tag_arousal + 1.0) / 2.0) * normalized
    return _signed(valence), _clamp(arousal, default=0.5)


def _top_tag(observations: Sequence[UserAffectObservationV1]) -> str:
    """按观察置信度加权选择代表标签；仅作为融合摘要，非用户自述真值。"""
    weighted: dict[str, float] = {}
    for observation in observations:
        for tag, probability in observation.emotion_distribution.items():
            weighted[tag] = weighted.get(tag, 0.0) + (
                _clamp(probability) * observation.confidence
            )
    if not weighted:
        return "NEUTRAL"
    return sorted(weighted.items(), key=lambda item: (-item[1], item[0]))[0][0]
