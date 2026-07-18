from __future__ import annotations

from host_brain.runtime.world_model import _dedupe_facts


def _fact(source_ref: str, value: str, confidence: float) -> dict[str, object]:
    return {
        "key": "life.affect.mood",
        "value": value,
        "source": source_ref.split(":", 1)[0],
        "source_ref": source_ref,
        "confidence": confidence,
        "conflict_state": "NONE",
        "metadata": {},
    }


def test_world_projection_retains_competing_sources_instead_of_confidence_overwrite() -> None:
    facts = _dedupe_facts(
        (
            _fact("life://state/5", "calm", 0.9),
            _fact("journal://entry/9", "tired", 0.4),
            _fact("life://state/5", "calm-old", 0.2),
        )
    )
    assert {(item["source_ref"], item["value"]) for item in facts} == {
        ("life://state/5", "calm"),
        ("journal://entry/9", "tired"),
    }
    assert all(item["conflict_state"] == "CONFLICT" for item in facts)
    assert facts[0]["metadata"]["competing_source_refs"] == [
        "journal://entry/9",
        "life://state/5",
    ]


def test_world_projection_preserves_matching_sources_as_corroboration() -> None:
    facts = _dedupe_facts(
        (
            _fact("life://state/5", "calm", 0.9),
            _fact("journal://entry/9", "calm", 0.4),
        )
    )

    assert {(item["source_ref"], item["value"]) for item in facts} == {
        ("life://state/5", "calm"),
        ("journal://entry/9", "calm"),
    }
    assert all(item["conflict_state"] == "NONE" for item in facts)
    assert all(
        item["metadata"]["corroborating_source_refs"]
        == ["journal://entry/9", "life://state/5"]
        for item in facts
    )
    assert all("competing_source_refs" not in item["metadata"] for item in facts)
