"""Pure normalization helpers for affect fusion.

The production service and canonical Life writer stay in ``affect_fusion``;
this module owns only bounded, deterministic value transforms.
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
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _text(value: Any, *, default: str = "", limit: int = 240) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return default
    return normalized[:limit]


def _clamp(value: Any, *, default: float = 0.0) -> float:
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        resolved = float(default)
    return round(max(0.0, min(1.0, resolved)), 6)


def _signed(value: Any, *, default: float = 0.0) -> float:
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        resolved = float(default)
    return round(max(-1.0, min(1.0, resolved)), 6)


def _opaque_ref(prefix: str, value: Any) -> str:
    return f"{prefix}://sha256/{_digest(value)}"


def _safe_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _safe_sequence(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _contains_raw_material(value: Any) -> bool:
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
    key = _text(value, limit=48).upper().replace(" ", "_")
    return _MODALITY_ALIASES.get(key)


def _distribution(value: Any, *, fallback_tag: str = "NEUTRAL") -> dict[str, float]:
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
    weighted: dict[str, float] = {}
    for observation in observations:
        for tag, probability in observation.emotion_distribution.items():
            weighted[tag] = weighted.get(tag, 0.0) + (
                _clamp(probability) * observation.confidence
            )
    if not weighted:
        return "NEUTRAL"
    return sorted(weighted.items(), key=lambda item: (-item[1], item[0]))[0][0]
