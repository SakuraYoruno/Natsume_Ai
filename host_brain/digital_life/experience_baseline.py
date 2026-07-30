"""DL-00 frozen experience-baseline contract.

This module deliberately validates a *measurement plan*, not a completed
companion-quality experiment.  A synthetic fixture can make future blind
evaluation reproducible, but it cannot prove that 七海 is emotionally natural
or that a real model/provider behaved correctly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
import json


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
    """The frozen experience baseline is incomplete or misleading."""


@dataclass(frozen=True)
class EmotionalCompanionBaseline:
    schema_version: str
    status: str
    family_count: int
    scenario_count: int
    coverage_tags: tuple[str, ...]
    minimum_model_trajectories: int
    minimum_distinct_human_reviewers: int

    @property
    def acceptance_status(self) -> str:
        """Fixtures alone are never an emotional-experience acceptance."""

        return MEASUREMENT_PENDING


def load_emotional_companion_baseline(path: Path) -> EmotionalCompanionBaseline:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise EmotionalBaselineManifestError("baseline payload must be an object")
    return validate_emotional_companion_baseline(payload)


def validate_emotional_companion_baseline(payload: Mapping[str, Any]) -> EmotionalCompanionBaseline:
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
    prohibited = _string_set(measurement.get("prohibited_final_evidence"), name="prohibited_final_evidence")
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

        context_turns = _string_list(family.get("context_turns"), name=f"{family_id}.context_turns")
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
    forbidden_keys = {"raw_audio", "raw_video", "raw_image", "raw_text", "audio_bytes", "image_bytes"}
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
