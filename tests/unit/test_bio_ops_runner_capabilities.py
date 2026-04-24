from __future__ import annotations

import pytest

from host_brain.bio_ops.capabilities import (
    audit_truthfulness_claims,
    classify_attention_budget,
    classify_reflex_stimulus,
    evaluate_active_silence,
    list_capability_specs,
    resolve_internal_weather,
)
from host_brain.bio_ops.runner import BioOpsRunner


@pytest.mark.parametrize("capability", [spec.capability for spec in list_capability_specs()])
def test_bio_ops_each_capability_produces_report_with_evidence(tmp_path, capability: str) -> None:
    runner = BioOpsRunner(artifact_store=None)
    runner.artifact_store.root = tmp_path

    result = runner.run(
        {
            "task_id": f"task-{capability}",
            "capability": capability,
            "mode": "audit",
            "owner_organ": "prefrontal_agent",
            "inputs": {
                "evidence_refs": ["evt-1"],
                "message": "好的",
                "planned_path": ["retrieval", "chat_llm"],
                "current_hour": 2,
                "user_stress": "high",
            },
        }
    )

    assert result["status"] in {"PROPOSED", "AUDITED", "CANDIDATE"}
    assert result["findings"]
    assert result["proposals"]
    assert result["artifacts"]


@pytest.mark.parametrize("capability", [spec.capability for spec in list_capability_specs()])
def test_bio_ops_each_capability_degrades_without_evidence(tmp_path, capability: str) -> None:
    runner = BioOpsRunner()
    runner.artifact_store.root = tmp_path

    result = runner.run(
        {
            "task_id": f"missing-{capability}",
            "capability": capability,
            "mode": "audit",
            "owner_organ": "prefrontal_agent",
            "inputs": {},
        }
    )

    assert result["status"] == "DEGRADED"
    assert result["reason_code"] == "BIO_OPS_MISSING_EVIDENCE"


def test_bio_ops_runner_blocks_sensitive_and_ownerless_candidate(tmp_path) -> None:
    runner = BioOpsRunner()
    runner.artifact_store.root = tmp_path

    sensitive = runner.run(
        {
            "task_id": "sensitive",
            "capability": "habit_compiler",
            "mode": "audit",
            "inputs": {"target_paths": ["host_brain/canon/core.yaml"], "evidence_refs": ["evt-1"]},
        }
    )
    ownerless = runner.run(
        {
            "task_id": "ownerless",
            "capability": "habit_compiler",
            "mode": "candidate",
            "inputs": {"evidence_refs": ["evt-1"], "write_candidate": True},
        }
    )

    assert sensitive["status"] == "BLOCKED"
    assert sensitive["reason_code"] == "BIO_OPS_SENSITIVE_TARGET_BLOCKED"
    assert ownerless["status"] == "BLOCKED"
    assert ownerless["reason_code"] == "BIO_OPS_OWNER_REQUIRED"


def test_reflex_attention_and_truthfulness_helpers() -> None:
    reflex = classify_reflex_stimulus("好的")
    attention = classify_attention_budget("好的", planned_path=["retrieval", "chat_llm", "codex_worker"])
    truth = audit_truthfulness_claims("我已经帮你保存了", evidence_refs=[])

    assert reflex["matched"] is True
    assert reflex["reflex_type"] == "short_ack"
    assert attention["overprocessed"] is True
    assert "codex_worker" in attention["forbidden_actions"]
    assert truth["issue"] == "unsupported_completion_claim"


def test_weather_and_active_silence_helpers() -> None:
    weather = resolve_internal_weather({"codex_queue_size": 4, "tts_status": "degraded"})
    silence = evaluate_active_silence({"current_hour": 2, "user_stress": "high", "explicit_help_request": False})

    assert weather["internal_weather"] == "static"
    assert silence["silence_policy"] == "quiet_presence"


def test_bio_ops_daily_cycle_covers_all_capabilities(tmp_path) -> None:
    runner = BioOpsRunner()
    runner.artifact_store.root = tmp_path

    result = runner.run_daily_cycle(inputs={"evidence_refs": ["daily"], "message": "好的"})

    assert result["status"] == "AUDITED"
    assert result["capability_count"] == len(list_capability_specs())
    assert all(item["status"] in {"PROPOSED", "AUDITED", "CANDIDATE"} for item in result["results"])
