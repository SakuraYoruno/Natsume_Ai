from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from host_brain.digital_life.experience_baseline import (
    BASELINE_STATUS,
    MEASUREMENT_PENDING,
    EmotionalBaselineManifestError,
    validate_emotional_companion_baseline,
)


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "tests" / "fixtures" / "emotional_companion" / "dl00_multiturn_baseline_v1.json"


def test_dl00_baseline_manifest_freezes_seventy_two_synthetic_multiturn_cases() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    baseline = validate_emotional_companion_baseline(payload)

    assert payload["schema_version"] == "natsume.emotional-companion-baseline.v1"
    assert payload["status"] == BASELINE_STATUS
    assert payload["privacy"] == "synthetic_prompts_only"
    families = payload["families"]
    assert len(families) == 12
    assert len({family["id"] for family in families}) == len(families)
    assert sum(len(family["variants"]) for family in families) == 72
    assert all(len(family["context_turns"]) >= 2 for family in families)
    assert all(len(family["expected"]) >= 3 for family in families)
    assert baseline.family_count == 12
    assert baseline.scenario_count == 72
    assert baseline.acceptance_status == MEASUREMENT_PENDING


def test_dl00_fixture_cannot_be_promoted_to_experience_pass_without_real_measurement() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    promoted = deepcopy(payload)
    promoted["status"] = "PASS"

    with pytest.raises(EmotionalBaselineManifestError, match="measurement pending"):
        validate_emotional_companion_baseline(promoted)


def test_dl00_fixture_rejects_missing_required_experience_coverage() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    incomplete = deepcopy(payload)
    incomplete["families"][-1]["coverage_tags"] = ["barge_in"]

    with pytest.raises(EmotionalBaselineManifestError, match="missing coverage tags"):
        validate_emotional_companion_baseline(incomplete)
