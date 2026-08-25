from __future__ import annotations

from collections.abc import Iterable

from host_brain.persona.drift_detector import extract_style_fingerprint
from host_brain.persona.galgame_text_contract import build_galgame_text_contract
from host_brain.persona.sample_guided_text_scorer import score_sample_guided_text
from host_brain.persona.text_quality_gate import get_text_quality_gate

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

    quality = get_text_quality_gate().validate(
        text=normalized_reply,
        render_profile="zh_text",
    )
    contract = build_galgame_text_contract(
        user_message=user_message,
        scene_id="",
    )
    surface_score = score_sample_guided_text(
        text=quality.normalized_text or normalized_reply,
        contract=contract,
        quality=quality,
        relationship_axes={"stage": bond_stage},
    )
    if quality.hard_fail or surface_score.hard_fail:
        quality_codes = tuple(
            dict.fromkeys(
                [
                    *list(quality.reason_codes or ()),
                    *list(surface_score.hard_gate_codes or ()),
                ]
            )
        )
        violations.append(f"surface_quality:{quality_codes}")

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
        max_chars=max_chars,
        required_markers=required_markers,
    )
    assert not violations, violations
