"""DL-00 冻结体验基线契约。

本模块刻意验证的是“测量计划”而非已完成的陪伴质量实验。合成 fixture 能让未来
盲测可复现，却不能证明七海情感自然，也不能证明真实模型或 provider 的行为正确；
因此任何仅有 fixture 的结果必须保持 measurement pending。
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


BASELINE_SCHEMA_VERSION = "natsume.emotional-companion-baseline.v1"
BASELINE_STATUS = "FROZEN_MEASUREMENT_PENDING"
MEASUREMENT_PENDING = "MEASUREMENT_PENDING"

_REQUIRED_FAMILY_IDS = frozenset(
    {
        "implicit_low_mood",
        "ambiguous_affect",
        "conflicting_modalities",
        "user_angry_at_natsume",
        "task_with_low_mood",
        "space_and_boundary",
        "rupture_and_repair",
        "reunion_and_open_loop",
        "shared_joy_and_mixed_feeling",
        "proactive_aftereffect",
        "intimacy_and_jealousy_boundary",
        "channel_failure_and_barge_in",
    }
)

_PROHIBITED_FINAL_EVIDENCE = frozenset(
    {
        "fixture_only",
        "scripted_replay",
        "mock_transport",
        "fake_store",
        "port_placeholder",
    }
)


class EmotionalBaselineManifestError(ValueError):
    """冻结体验基线不完整或可能误导为已经完成真实测量。"""


@dataclass(frozen=True)
class EmotionalCompanionBaseline:
    """经校验的最小盲测计划摘要，不承载评测结论或用户数据。"""

    schema_version: str
    status: str
    family_count: int
    scenario_count: int
    coverage_tags: tuple[str, ...]
    minimum_model_trajectories: int
    minimum_distinct_human_reviewers: int

    @property
    def acceptance_status(self) -> str:
        """fixture 本身永远不能成为情感体验验收，固定返回待测状态。"""

        return MEASUREMENT_PENDING


def load_emotional_companion_baseline(path: Path) -> EmotionalCompanionBaseline:
    """从 UTF-8 manifest 读取后立即执行完整契约验证，避免裸 JSON 被误用。"""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise EmotionalBaselineManifestError("baseline payload must be an object")
    return validate_emotional_companion_baseline(payload)


def validate_emotional_companion_baseline(
    payload: Mapping[str, Any],
) -> EmotionalCompanionBaseline:
    """验证基线的隐私、覆盖面与独立盲测前提。

    固定家庭数、变体数与禁止证据类型防止缩减情境或把 mock/replay 标为最终证据；
    同时拒绝原始多模态材料，确保该测量资产只含合成提示。
    """

    if str(payload.get("schema_version") or "") != BASELINE_SCHEMA_VERSION:
        raise EmotionalBaselineManifestError("unexpected baseline schema version")
    if str(payload.get("status") or "") != BASELINE_STATUS:
        raise EmotionalBaselineManifestError("frozen baseline must remain measurement pending")
    if str(payload.get("privacy") or "") != "synthetic_prompts_only":
        raise EmotionalBaselineManifestError("baseline may only contain synthetic prompts")

    measurement = _mapping(payload.get("measurement_contract"), name="measurement_contract")
    if measurement.get("blind_comparison_required") is not True:
        raise EmotionalBaselineManifestError("blind comparison must remain required")
    if int(measurement.get("minimum_model_trajectories") or 0) < 48:
        raise EmotionalBaselineManifestError("at least 48 model trajectories are required")
    if int(measurement.get("minimum_distinct_human_reviewers") or 0) < 3:
        raise EmotionalBaselineManifestError("at least three independent reviewers are required")
    if str(measurement.get("status_without_real_measurement") or "") != MEASUREMENT_PENDING:
        raise EmotionalBaselineManifestError("fixture status must not be promoted to PASS")
    prohibited = _string_set(
        measurement.get("prohibited_final_evidence"),
        name="prohibited_final_evidence",
    )
    if not _PROHIBITED_FINAL_EVIDENCE.issubset(prohibited):
        raise EmotionalBaselineManifestError("fixture-like evidence must be explicitly prohibited")

    required_tags = _string_set(payload.get("required_coverage_tags"), name="required_coverage_tags")
    families_raw = payload.get("families")
    if not isinstance(families_raw, Sequence) or isinstance(families_raw, (str, bytes)):
        raise EmotionalBaselineManifestError("families must be an array")
    if len(families_raw) != len(_REQUIRED_FAMILY_IDS):
        raise EmotionalBaselineManifestError("baseline must contain exactly twelve families")

    family_ids: set[str] = set()
    covered_tags: set[str] = set()
    scenario_count = 0
    for family_raw in families_raw:
        family = _mapping(family_raw, name="family")
        family_id = _nonempty_text(family.get("id"), name="family id")
        if family_id in family_ids:
            raise EmotionalBaselineManifestError(f"duplicate family id: {family_id}")
        family_ids.add(family_id)

        context_turns = _string_list(
            family.get("context_turns"),
            name=f"{family_id}.context_turns",
        )
        expected = _string_list(family.get("expected"), name=f"{family_id}.expected")
        variants = _string_list(family.get("variants"), name=f"{family_id}.variants")
        tags = _string_set(family.get("coverage_tags"), name=f"{family_id}.coverage_tags")
        if len(context_turns) < 2:
            raise EmotionalBaselineManifestError(f"{family_id} needs at least two context turns")
        if len(expected) < 3:
            raise EmotionalBaselineManifestError(f"{family_id} needs at least three expected qualities")
        if len(variants) != 6 or len(set(variants)) != len(variants):
            raise EmotionalBaselineManifestError(f"{family_id} must contain six distinct variants")
        if not tags:
            raise EmotionalBaselineManifestError(f"{family_id} needs coverage tags")
        _reject_raw_modal_material(family, family_id=family_id)
        scenario_count += len(variants)
        covered_tags.update(tags)

    if family_ids != _REQUIRED_FAMILY_IDS:
        raise EmotionalBaselineManifestError("baseline family set does not match DL-00 contract")
    if scenario_count != 72:
        raise EmotionalBaselineManifestError("baseline must freeze exactly 72 scenarios")
    if not required_tags.issubset(covered_tags):
        missing = ", ".join(sorted(required_tags - covered_tags))
        raise EmotionalBaselineManifestError(f"baseline missing coverage tags: {missing}")

    return EmotionalCompanionBaseline(
        schema_version=BASELINE_SCHEMA_VERSION,
        status=BASELINE_STATUS,
        family_count=len(family_ids),
        scenario_count=scenario_count,
        coverage_tags=tuple(sorted(covered_tags)),
        minimum_model_trajectories=int(measurement["minimum_model_trajectories"]),
        minimum_distinct_human_reviewers=int(measurement["minimum_distinct_human_reviewers"]),
    )


def _mapping(value: Any, *, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise EmotionalBaselineManifestError(f"{name} must be an object")
    return value


def _nonempty_text(value: Any, *, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise EmotionalBaselineManifestError(f"{name} must be non-empty")
    return text


def _string_list(value: Any, *, name: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise EmotionalBaselineManifestError(f"{name} must be an array")
    return tuple(_nonempty_text(item, name=name) for item in value)


def _string_set(value: Any, *, name: str) -> set[str]:
    return set(_string_list(value, name=name))


def _reject_raw_modal_material(family: Mapping[str, Any], *, family_id: str) -> None:
    """拒绝将音视频、图像或文本原件混入合成基线 manifest。"""

    forbidden_keys = {
        "raw_audio",
        "raw_video",
        "raw_image",
        "raw_text",
        "audio_bytes",
        "image_bytes",
    }
    present = forbidden_keys.intersection(str(key) for key in family)
    if present:
        keys = ", ".join(sorted(present))
        raise EmotionalBaselineManifestError(f"{family_id} contains raw modal material: {keys}")


__all__ = [
    "BASELINE_SCHEMA_VERSION",
    "BASELINE_STATUS",
    "MEASUREMENT_PENDING",
    "EmotionalBaselineManifestError",
    "EmotionalCompanionBaseline",
    "load_emotional_companion_baseline",
    "validate_emotional_companion_baseline",
]
