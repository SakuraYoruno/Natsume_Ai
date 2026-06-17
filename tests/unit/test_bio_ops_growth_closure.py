from __future__ import annotations

from host_brain.bio_ops.adoption import resolve_bio_ops_adoption_policy
from host_brain.bio_ops.capabilities import (
    GROWTH_CLOSURE_CAPABILITIES,
    evaluate_growth_stage,
    classify_idea_fermentation,
    list_capability_specs,
    manage_open_loops,
    measure_existential_load,
    shed_cognitive_load,
)
from host_brain.bio_ops.runner import BioOpsRunner


def _runner(tmp_path) -> BioOpsRunner:
    runner = BioOpsRunner()
    runner.artifact_store.root = tmp_path
    return runner


def test_growth_closure_capability_set_is_registered() -> None:
    registered = {spec.capability for spec in list_capability_specs()}

    assert set(GROWTH_CLOSURE_CAPABILITIES) <= registered
    assert list(GROWTH_CLOSURE_CAPABILITIES) == [
        "idea_fermentation_pool",
        "growth_stage_system",
        "existential_load",
        "open_loop_manager",
        "cognitive_load_shedding",
    ]


def test_idea_fermentation_pool_keeps_ideas_in_triage(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "idea-fermentation",
            "capability": "idea_fermentation_pool",
            "mode": "audit",
            "owner_organ": "default_mode_agent",
            "inputs": {"ideas": [{"idea": "InnerParliament", "blockers": ["owner mapping"]}]},
        }
    )
    ideas = classify_idea_fermentation({"ideas": [{"idea": "ReflexArcCompiler", "state": "ready"}]})

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["ideas"][0]["state"] == "needs_design"
    assert ideas["ideas"][0]["state"] == "ready_for_codex"


def test_growth_stage_system_keeps_risky_autonomy_locked(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "growth-stage",
            "capability": "growth_stage_system",
            "mode": "audit",
            "owner_organ": "prefrontal_agent",
            "inputs": {"current_stage": "Stage 3", "unlocked": ["organ_ct_scan"]},
        }
    )
    growth = evaluate_growth_stage({"current_stage": "Stage 3", "unlocked": ["organ_ct_scan"]})

    assert result["status"] == "PROPOSED"
    assert "autonomous_self_evolve" in result["findings"][0]["locked"]
    assert "bio_ops_direct_state_write" in result["findings"][0]["locked"]
    assert growth["next_milestone"] == "ImmuneMemoryBuilder"
    assert resolve_bio_ops_adoption_policy("growth_stage_system")["mode"] == "report_only"


def test_existential_load_reduces_new_initiatives(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "existential-load",
            "capability": "existential_load",
            "mode": "audit",
            "owner_organ": "prefrontal_agent",
            "inputs": {
                "open_loops": [f"loop-{index}" for index in range(8)],
                "failed_self_heal_jobs": ["probe-timeout"],
                "persona_drift_risk": 0.7,
                "overdue_promises": ["finish closure"],
            },
        }
    )
    load = measure_existential_load({"open_loops": [1, 2, 3, 4, 5], "persona_drift_risk": 0.7})

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["policy"] == "reduce_new_initiatives_until_closure"
    assert "many_open_loops" in result["findings"][0]["sources"]
    assert load["score"] > 0


def test_open_loop_manager_triages_without_disturbing_user(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "open-loop",
            "capability": "open_loop_manager",
            "mode": "audit",
            "owner_organ": "prefrontal_agent",
            "inputs": {
                "open_loops": [
                    {"id": "live2d_custom_model_stage_b", "status": "pending", "importance": 0.82},
                    {"id": "old_chat_context", "status": "pending", "importance": 0.2},
                ]
            },
        }
    )
    loops = manage_open_loops({"open_loops": [{"id": "x", "importance": 0.8}]})

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["task_candidate_count"] == 1
    assert result["findings"][0]["user_disturbance_allowed"] is False
    assert loops["open_loops"][0]["review_action"] == "turn_into_task"


def test_cognitive_load_shedding_pauses_low_priority_background_work(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "load-shedding",
            "capability": "cognitive_load_shedding",
            "mode": "audit",
            "owner_organ": "prefrontal_agent",
            "inputs": {"active_tasks": ["agent_worker", "dream_compile", "weekly_evolution_scan"], "codex_queue_size": 3},
        }
    )
    shedding = shed_cognitive_load({"active_tasks": ["agent_worker"], "codex_queue_size": 3})

    assert result["status"] == "PROPOSED"
    assert "dream_compile" in result["findings"][0]["paused"]
    assert "immune_monitor" in result["findings"][0]["kept"]
    assert shedding["reason"] == "agent_worker_active"
