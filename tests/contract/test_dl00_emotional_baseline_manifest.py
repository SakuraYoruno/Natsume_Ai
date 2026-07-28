from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "tests" / "fixtures" / "emotional_companion" / "dl00_multiturn_baseline_v1.json"


def test_dl00_baseline_manifest_freezes_seventy_two_synthetic_multiturn_cases() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert payload["schema_version"] == "natsume.emotional-companion-baseline.v1"
    assert payload["status"] == "FROZEN_MEASUREMENT_PENDING"
    assert payload["privacy"] == "synthetic_prompts_only"
    families = payload["families"]
    assert len(families) == 12
    assert len({family["id"] for family in families}) == len(families)
    assert sum(len(family["variants"]) for family in families) == 72
    assert all(len(family["context_turns"]) >= 2 for family in families)
    assert all(len(family["expected"]) >= 3 for family in families)
