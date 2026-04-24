from __future__ import annotations

from host_brain.bio_ops.capabilities import list_capability_specs
from host_brain.bio_ops.runner import BioOpsRunner


def test_bio_ops_daily_cycle_generates_all_capability_summaries(tmp_path) -> None:
    runner = BioOpsRunner()
    runner.artifact_store.root = tmp_path

    result = runner.run_daily_cycle(
        inputs={
            "evidence_refs": ["integration-daily"],
            "message": "我好累",
            "planned_path": ["retrieval", "chat_llm"],
            "current_hour": 1,
            "user_stress": "high",
        }
    )

    capabilities = {item["capability"] for item in result["results"]}
    expected = {spec.capability for spec in list_capability_specs()}

    assert result["status"] == "AUDITED"
    assert capabilities == expected
    assert result["capability_count"] == len(expected)
