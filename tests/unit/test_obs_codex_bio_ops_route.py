from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

import host_brain.app.obs_routes_codex_bio_ops as route_module


def _write_json(path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_codex_bio_ops_observability_compacts_latest_run(tmp_path, monkeypatch) -> None:
    run_dir = tmp_path / "20260425_102824"
    run_dir.mkdir(parents=True)
    contract_payload = {
        "status": "EXECUTED",
        "work_order_id": "wo_inspect",
        "summary": "Codex WorkOrder executed.",
        "result_text": "BioOps capabilities inspected.",
        "changed_files": [],
        "artifacts_dir": str(run_dir / "codex_artifacts" / "wo_inspect"),
        "thread_id": "thread-1",
        "verification": {"status": "NOT_RUN"},
        "risk_flags": [],
        "review_required": False,
        "next_actions": [],
        "rollback_manifest_ref": None,
        "metadata": {
            "codex_model": "gpt-5.4-mini",
            "prompt_profile": "bio_ops_inspect",
            "sandbox_mode": "read-only",
            "returncode": 0,
        },
    }
    blocked_payload = {
        **contract_payload,
        "status": "BLOCKED",
        "work_order_id": "wo_blocked",
        "summary": "Codex WorkOrder blocked by policy.",
        "result_text": "",
        "verification": {"status": "NOT_RUN"},
        "risk_flags": ["policy_block"],
        "review_required": True,
        "reason_code": "CODEX_WORKORDER_BLOCKED",
    }
    _write_json(
        run_dir / "summary.json",
        {
            "run_id": run_dir.name,
            "bio_ops_capability_count": 27,
            "bio_ops_status_counts": {"PROPOSED": 20, "CANDIDATE": 5, "AUDITED": 2},
            "codex_inspect_fixed_status": "EXECUTED",
            "codex_blocked_status": "BLOCKED",
            "hard_cut_scan_exit_code": 1,
            "pytest_exit_code": 0,
            "work_order_preview_ok": True,
            "immune_proposal_triggered": True,
        },
    )
    _write_json(
        run_dir / "bio_ops_daily_summary.json",
        {
            "status": "AUDITED",
            "capability_count": 27,
            "status_counts": {"PROPOSED": 20, "CANDIDATE": 5, "AUDITED": 2},
        },
    )
    _write_json(
        run_dir / "bio_ops_daily_full.json",
        {
            "results": [
                {
                    "status": "CANDIDATE",
                    "capability": "habit_compiler",
                    "summary": "Habit candidate.",
                    "metadata": {
                        "bio_ops_adoption": {
                            "status": "PENDING_REVIEW",
                            "candidate": {
                                "candidate_id": "candidate-1",
                                "capability": "habit_compiler",
                                "owner_organ": "social_brain_agent",
                                "output_kind": "habit_candidate",
                                "summary": "Habit candidate.",
                                "evidence_refs": ["chat:pattern"],
                                "next_actions": ["owner must review"],
                                "review_required": True,
                            },
                        }
                    },
                }
            ]
        },
    )
    _write_json(
        run_dir / "focused_effects.json",
        {
            "sensitive_write_blocked": {
                "status": "BLOCKED",
                "capability": "habit_compiler",
                "summary": "BioOps task blocked by policy.",
                "risk_flags": ["bio_ops_policy_block"],
                "review_required": True,
                "reason_code": "BIO_OPS_SENSITIVE_TARGET_BLOCKED",
                "detail": ".env",
                "metadata": {
                    "bio_ops_adoption": {
                        "status": "BLOCKED",
                        "policy": {"owner_organ": "social_brain_agent"},
                        "candidate": {"owner_organ": "social_brain_agent"},
                    }
                },
            }
        },
    )
    _write_json(
        run_dir / "runtime_traces.json",
        {
            "short_ack_chat": {
                "proposal_only": True,
                "reflex": {"matched": True, "reflex_type": "short_ack", "action": "chat_short_ack"},
                "attention_budget": {
                    "pattern": "short_ack_message_overprocessed",
                    "overprocessed": True,
                    "forbidden_actions": ["codex_worker"],
                },
                "internal_weather": {"internal_weather": "night_fog", "ttl_minutes": 30},
                "active_silence": {"silence_policy": "quiet_presence", "reason_code": "LATE_NIGHT_HIGH_LOAD_NO_ASK"},
                "response_breathing": {"reply_density": "normal", "reason_code": "DEFAULT_BREATHING"},
                "forbidden_actions": ["codex_worker"],
            },
            "complex_code_request": {
                "proposal_only": True,
                "reflex": {"matched": False, "reason_code": "NO_LOW_RISK_REFLEX"},
                "attention_budget": {"pattern": "within_budget", "overprocessed": False, "forbidden_actions": []},
                "internal_weather": {"internal_weather": "clear", "ttl_minutes": 30},
                "active_silence": {"silence_policy": "normal_presence", "reason_code": "NO_ACTIVE_SILENCE"},
                "response_breathing": {"reply_density": "compact_status"},
                "forbidden_actions": [],
            },
        },
    )
    _write_json(run_dir / "codex_inspect_fixed_result.json", contract_payload)
    _write_json(run_dir / "codex_blocked_write_result.json", blocked_payload)
    _write_json(run_dir / "hard_cut_scan.json", {"exit_code": 1})
    _write_json(run_dir / "pytest_result.json", {"exit_code": 0})

    monkeypatch.setattr(route_module, "OBSERVABLE_EFFECTS_ROOT", tmp_path)
    app = FastAPI()
    app.include_router(route_module.router)

    response = TestClient(app).get("/obs/codex-bio-ops")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["run_id"] == run_dir.name
    assert payload["codex_runtime"]["inspect"]["status"] == "EXECUTED"
    assert payload["codex_runtime"]["blocked_write"]["status"] == "BLOCKED"
    assert payload["codex_runtime"]["hard_cut_scan"]["passed"] is True
    assert payload["bio_ops"]["status_counts"]["CANDIDATE"] == 5
    assert payload["bio_ops"]["pending_adoptions"][0]["owner_organ"] == "social_brain_agent"
    assert payload["bio_ops"]["inner_mechanisms"]["capability_count"] == 6
    assert payload["bio_ops"]["inner_mechanisms"]["direct_write_allowed"] is False
    assert {item["capability"] for item in payload["bio_ops"]["inner_mechanisms"]["capabilities"]} == {
        "promise_ledger",
        "capability_self_awareness",
        "reflex_arc_compiler",
        "shadow_persona_test",
        "idea_fermentation_pool",
        "inner_outer_world_boundary",
    }
    assert payload["bio_ops"]["trust_foundation"]["capability_count"] == 6
    assert payload["bio_ops"]["trust_foundation"]["direct_write_allowed"] is False
    assert {item["capability"] for item in payload["bio_ops"]["trust_foundation"]["capabilities"]} == {
        "promise_ledger",
        "truthfulness_cortex",
        "capability_self_awareness",
        "consent_gradient",
        "competence_humility",
        "uncertainty_ritual",
    }
    assert payload["runtime_traces"][0]["id"] == "short_ack_chat"
    assert {item["id"] for item in payload["benefits"]} == {
        "codex_runtime_control",
        "bio_ops_owner_gate",
        "runtime_behavior_trace",
        "verification_surface",
    }
