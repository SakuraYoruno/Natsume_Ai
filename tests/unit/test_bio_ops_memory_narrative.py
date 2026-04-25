from __future__ import annotations

from host_brain.bio_ops.adoption import resolve_bio_ops_adoption_policy
from host_brain.bio_ops.capabilities import (
    MEMORY_NARRATIVE_CAPABILITIES,
    build_inner_library,
    check_self_continuity,
    distill_memory_dream,
    identify_cognitive_landmarks,
    integrate_narrative_identity,
    list_capability_specs,
    segment_episode_memory,
)
from host_brain.bio_ops.runner import BioOpsRunner


def _runner(tmp_path) -> BioOpsRunner:
    runner = BioOpsRunner()
    runner.artifact_store.root = tmp_path
    return runner


def test_memory_narrative_capability_set_is_registered() -> None:
    registered = {spec.capability for spec in list_capability_specs()}

    assert set(MEMORY_NARRATIVE_CAPABILITIES) <= registered
    assert list(MEMORY_NARRATIVE_CAPABILITIES) == [
        "episode_memory",
        "inner_library",
        "cognitive_landmarks",
        "narrative_identity",
        "self_continuity_checksum",
        "memory_dream_distillation",
    ]


def test_episode_memory_segments_conversation_without_memory_write(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "episode-memory",
            "capability": "episode_memory",
            "mode": "audit",
            "owner_organ": "hippocampus_agent",
            "inputs": {
                "conversation_turns": ["继续做 Codex BioOps", "把 WebUI 观测接上"],
                "outcomes": ["added monitor panel"],
                "open_loops": ["push PR"],
            },
        }
    )
    episode = segment_episode_memory({"conversation_turns": ["Codex BioOps WorkOrder"]})

    assert result["status"] == "PROPOSED"
    assert "Codex Bio-Ops" in result["findings"][0]["main_topics"]
    assert result["findings"][0]["long_term_write_allowed"] is False
    assert episode["episode_id"].startswith("episode_codex_bio_ops")
    assert resolve_bio_ops_adoption_policy("episode_memory")["mode"] == "report_only"


def test_inner_library_builds_shelf_first_retrieval_report(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "inner-library",
            "capability": "inner_library",
            "mode": "audit",
            "owner_organ": "hippocampus_agent",
            "inputs": {
                "docs": ["CODEX_BIO_OPERATIONS_PLAN.md", "Live2D body notes"],
                "episodes": ["Codex BioOps closure"],
                "open_loops": ["Live2D custom model stage B"],
            },
        }
    )
    library = build_inner_library({"docs": ["CODEX_BIO_OPERATIONS_PLAN.md"]})

    assert result["status"] == "PROPOSED"
    assert {shelf["shelf_id"] for shelf in result["findings"][0]["shelves"]} >= {"codex_bio_ops", "live2d_body"}
    assert library["retrieval_policy"] == "shelf_first_then_documents"


def test_cognitive_landmarks_anchor_long_running_projects(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "cognitive-landmarks",
            "capability": "cognitive_landmarks",
            "mode": "audit",
            "owner_organ": "hippocampus_agent",
            "inputs": {
                "landmarks": ["Codex Bio-Ops", "Memory / Bond"],
                "related_docs": ["CODEX_DIGITAL_LIFE_INNER_MECHANISMS.md"],
            },
        }
    )
    landmarks = identify_cognitive_landmarks({"landmarks": ["Codex Bio-Ops"]})

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["landmarks"][0]["landmark"] == "Codex Bio-Ops"
    assert landmarks["direct_write_allowed"] is False


def test_narrative_identity_keeps_growth_delta_out_of_persona_core(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "narrative-identity",
            "capability": "narrative_identity",
            "mode": "audit",
            "owner_organ": "prefrontal_agent",
            "inputs": {
                "identity_delta": {
                    "new_capability": "codex_self_heal",
                    "meaning": "I can inspect my own runtime state",
                }
            },
        }
    )
    identity = integrate_narrative_identity({"new_capability": "codex_self_heal"})

    assert result["status"] == "PROPOSED"
    assert "do_not_confuse_capability_with_unbounded_action" in result["findings"][0]["risks"]
    assert identity["integration"].startswith("add to self_model")


def test_self_continuity_checksum_flags_formal_tone_regression(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "self-continuity",
            "capability": "self_continuity_checksum",
            "mode": "audit",
            "owner_organ": "prefrontal_agent",
            "inputs": {
                "before_snapshot": {"tone": "warm technical companion"},
                "after_snapshot": {"tone": "尊敬的用户，很抱歉给您带来不便"},
            },
        }
    )
    continuity = check_self_continuity({"after_snapshot": {"tone": "尊敬的用户"}})

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["tone_consistent"] is False
    assert continuity["recommended_action"] == "rollback or revise style block before adoption"


def test_memory_dream_distillation_stays_private(tmp_path) -> None:
    result = _runner(tmp_path).run(
        {
            "task_id": "memory-dream-distillation",
            "capability": "memory_dream_distillation",
            "mode": "audit",
            "owner_organ": "default_mode_agent",
            "inputs": {
                "source_events": ["Codex CI tests passed", "terminal work felt long"],
                "emotion": "focused_warmth",
            },
        }
    )
    dream = distill_memory_dream({"source_events": ["Codex terminal tests"]})

    assert result["status"] == "PROPOSED"
    assert result["findings"][0]["user_visible"] is False
    assert result["findings"][0]["dream_memory"]["symbol"] == "long corridor with glowing terminals"
    assert dream["direct_write_allowed"] is False
