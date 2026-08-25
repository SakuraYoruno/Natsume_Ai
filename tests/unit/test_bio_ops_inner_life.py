from __future__ import annotations

from host_brain.bio_ops.adoption import resolve_bio_ops_adoption_policy
from host_brain.bio_ops.capabilities import (
    INNER_LIFE_CAPABILITIES,
    audit_self_model,
    classify_memory_texture,
    compile_personal_mythology,
    compile_symbolic_memory,
    compute_life_pulse,
    list_capability_specs,
    plan_dream_rehearsal,
    resolve_internal_weather,
)
from host_brain.bio_ops.runner import BioOpsRunner


def _runner(tmp_path) -> BioOpsRunner:
    runner = BioOpsRunner()
    runner.artifact_store.root = tmp_path
    return runner


def test_inner_life_capability_set_is_registered() -> None:
    registered = {spec.capability for spec in list_capability_specs()}

    assert set(INNER_LIFE_CAPABILITIES) <= registered
    assert list(INNER_LIFE_CAPABILITIES) == [
        "dream_rehearsal",
        "symbolic_memory",
        "memory_texture",
        "internal_weather_system",
        "self_model",
        "life_pulse",
        "personal_mythology",
    ]


def test_dream_rehearsal_reports_next_day_diagnostic_path(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "dream-rehearsal",
            "capability": "dream_rehearsal",
            "mode": "audit",
            "owner_organ": "default_mode_agent",
            "inputs": {
                "high_frequency_topics": ["Codex immune auto-state"],
                "unfinished_codex_jobs": ["immune dispatch probe"],
                "unstable_components": ["tts"],
            },
        }
    )
    rehearsal = plan_dream_rehearsal({"high_frequency_topics": ["Codex immune auto-state"]})

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["scenario_count"] == 1
    assert "host_brain/agent_runtime/immune.py" in result["findings"][0]["likely_files"]
    assert rehearsal["write_policy"] == "report_only"
    assert resolve_bio_ops_adoption_policy("dream_rehearsal")["mode"] == "report_only"


def test_symbolic_memory_converts_repair_event_without_writing_memory(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "symbolic-memory",
            "capability": "symbolic_memory",
            "mode": "audit",
            "owner_organ": "hippocampus_agent",
            "inputs": {"memory_events": ["TTS voice subprocess fallback fixed"], "evidence_refs": ["evt:tts"]},
        }
    )
    symbolic = compile_symbolic_memory({"memory_events": ["TTS voice subprocess fallback fixed"]})

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["direct_write_allowed"] is False
    assert result["findings"][0]["symbols"][0]["symbol"] == "voice_recovery"
    assert symbolic["owner_required_for_memory_write"] == "hippocampus_agent"


def test_memory_texture_tags_technical_milestone(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "memory-texture",
            "capability": "memory_texture",
            "mode": "audit",
            "owner_organ": "hippocampus_agent",
            "inputs": {"memory_fragments": ["Codex BioOps tests passed; still has open loop"]},
        }
    )
    texture = classify_memory_texture({"memory_fragments": ["Codex tests passed milestone"]})

    assert result["status"] == "PROPOSED"
    assert "technical" in result["findings"][0]["texture"]
    assert "unfinished" in result["findings"][0]["texture"]
    assert "milestone" in texture["texture"]


def test_self_model_flags_engineering_assistant_drift(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "self-model",
            "capability": "self_model",
            "mode": "audit",
            "owner_organ": "prefrontal_agent",
            "inputs": {
                "current_identity": "engineering assistant",
                "current_capabilities": ["chat", "codex"],
                "current_limitations": ["vision_not_ready"],
            },
        }
    )
    self_model = audit_self_model({"current_identity": "engineering assistant", "current_capabilities": ["chat"]})

    assert result["status"] == "PROPOSED"
    assert "engineering_assistant_drift" in result["findings"][0]["current_risks"]
    assert self_model["direct_write_allowed"] is False


def test_life_pulse_recommends_pacing_under_overload(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "life-pulse",
            "capability": "life_pulse",
            "mode": "audit",
            "owner_organ": "default_mode_agent",
            "inputs": {
                "pulse_signals": {
                    "codex_jobs": 7,
                    "organ_events": 10,
                    "user_turns": 6,
                    "emotion_variance": 0.6,
                    "edge_state": "degraded",
                }
            },
        }
    )
    pulse = compute_life_pulse({"pulse_signals": {"codex_jobs": 7, "edge_state": "degraded"}})

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["pulse_state"] == "overloaded"
    assert result["findings"][0]["suggested_behavior"] == "reduce_proactive_behavior"
    assert pulse["write_policy"] == "trace_only"


def test_personal_mythology_stays_private_and_forbids_core_targets(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "personal-mythology",
            "capability": "personal_mythology",
            "mode": "audit",
            "owner_organ": "hippocampus_agent",
            "inputs": {"growth_events": ["Codex Bio-Ops closure completed"], "evidence_refs": ["evt:bio_ops"]},
        }
    )
    mythology = compile_personal_mythology({"growth_events": ["first stable self-heal immune loop"]})

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["user_visible"] is False
    assert "Canon" in result["findings"][0]["forbidden_targets"]
    assert mythology["myth_events"][0]["identity_delta"] == "candidate_only"


def test_internal_weather_reports_causes_and_behavior_policy() -> None:
    weather = resolve_internal_weather({"codex_queue_size": 4, "tts_status": "degraded"})

    assert weather["internal_weather"] == "static"
    assert "codex_queue_high" in weather["causes"]
    assert weather["behavior_policy"] == "reduce_multitasking_and_keep_replies_short"
