from __future__ import annotations

from collections.abc import Iterable

from host_brain.persona.drift_detector import extract_style_fingerprint
from host_brain.reasoning.reflection_chain import ReflectionChain

DEFAULT_FORBIDDEN_MARKERS = (
    "AI",
    "人工智能",
    "ChatGPT",
    "LLM",
    "系统",
    "接口",
    "API",
    "JSON",
    "Agent",
    "ReAct",
    "客服",
    "工单",
    "降级",
)


def collect_natsume_style_violations(
    *,
    user_message: str,
    reply: str,
    emotion_tag: str,
    bond_stage: str,
    forbidden_markers: Iterable[str] = DEFAULT_FORBIDDEN_MARKERS,
    min_warmth: float = 0.50,
    min_sensitivity: float = 0.42,
    min_playfulness: float = 0.50,
    require_reflection_pass: bool = True,
    require_no_revision: bool = True,
    max_chars: int | None = None,
    required_markers: Iterable[str] = (),
) -> list[str]:
    violations: list[str] = []
    normalized_reply = str(reply or "").strip()

    if not normalized_reply:
        return ["empty_reply"]

    if max_chars is not None and len(normalized_reply) > int(max_chars):
        violations.append(f"too_long:{len(normalized_reply)}>{int(max_chars)}")

    missing_markers = [
        marker for marker in required_markers if marker not in normalized_reply
    ]
    if missing_markers:
        violations.append(f"missing_markers:{missing_markers}")

    forbidden_hits = [
        marker for marker in forbidden_markers if marker and marker in normalized_reply
    ]
    if forbidden_hits:
        violations.append(f"forbidden:{forbidden_hits}")

    reflection = ReflectionChain().reflect(
        user_message=user_message,
        ai_response=normalized_reply,
        emotion_tag=emotion_tag,
        bond_stage=bond_stage,
    )
    if require_reflection_pass and not reflection.passed:
        violations.append(f"reflection_failed:{reflection.as_dict()}")
    if require_no_revision and reflection.should_revise:
        violations.append(f"reflection_should_revise:{reflection.as_dict()}")

    fingerprint = extract_style_fingerprint(
        normalized_reply,
        user_input_text=user_message,
    ).as_dict()
    if float(fingerprint["warmth"]) < float(min_warmth):
        violations.append(f"warmth:{fingerprint['warmth']:.3f}")
    if float(fingerprint["sensitivity"]) < float(min_sensitivity):
        violations.append(f"sensitivity:{fingerprint['sensitivity']:.3f}")
    if float(fingerprint["playfulness"]) < float(min_playfulness):
        violations.append(f"playfulness:{fingerprint['playfulness']:.3f}")
    return violations


def assert_natsume_style(
    *,
    user_message: str,
    reply: str,
    emotion_tag: str,
    bond_stage: str,
    forbidden_markers: Iterable[str] = DEFAULT_FORBIDDEN_MARKERS,
    min_warmth: float = 0.50,
    min_sensitivity: float = 0.42,
    min_playfulness: float = 0.50,
    require_reflection_pass: bool = True,
    require_no_revision: bool = True,
    max_chars: int | None = None,
    required_markers: Iterable[str] = (),
) -> None:
    violations = collect_natsume_style_violations(
        user_message=user_message,
        reply=reply,
        emotion_tag=emotion_tag,
        bond_stage=bond_stage,
        forbidden_markers=forbidden_markers,
        min_warmth=min_warmth,
        min_sensitivity=min_sensitivity,
        min_playfulness=min_playfulness,
        require_reflection_pass=require_reflection_pass,
        require_no_revision=require_no_revision,
        max_chars=max_chars,
        required_markers=required_markers,
    )
    assert not violations, violations
