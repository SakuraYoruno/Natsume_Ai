from __future__ import annotations

from pathlib import Path

import pytest

from host_brain.persona.proactive_voice import (
    SCENE_MORNING_GREET,
    PersonaProactiveVoice,
)
from host_brain.runtime.context_contracts import ContextConstraintV1, SceneDecisionV2
from host_brain.runtime.scene_consumer_projection import (
    SCENE_IDENTITY_MISMATCH,
    SCENE_PROJECTION_BLOCK,
    SCENE_PROJECTION_INVALID,
    SceneConsumerProjectionError,
    SceneIdentityV2,
    project_scene_decision_v2,
)
from host_brain.surface_runtime.eligibility import _surface_scene_guardrails


def _decision(
    *constraints: ContextConstraintV1,
    scene_id: str = "scene.architecture-refactor",
) -> SceneDecisionV2:
    return SceneDecisionV2(
        workspace_id="workspace_ne03_cutover",
        subject_id="subject_ne03_cutover",
        scene_id=scene_id,
        revision=7,
        world_snapshot_digest="1" * 64,
        context_digest="2" * 64,
        guardian_snapshot_digest="3" * 64,
        constraints=tuple(constraints),
    )


def _constraint(
    code: str,
    disposition: str,
    *,
    origin: str = "SCENE",
) -> ContextConstraintV1:
    return ContextConstraintV1(
        code=code,
        disposition=disposition,
        origin=origin,
        source_ref=f"policy://scene/v1/{code.lower()}",
        reason=f"reason for {code}",
    )


def test_projection_verifies_exact_identity_and_only_emits_restrictions() -> None:
    decision = _decision(_constraint("EVIDENCE_BOUND_CHANGE", "RESTRICT"))
    identity = SceneIdentityV2.from_decision(decision)

    projection = project_scene_decision_v2(
        decision.to_dict(),
        expected_identity=identity.to_dict(),
    )
    legacy = projection.expression_projection(user_goal_ref="intent://ne03-cutover")

    assert projection.identity == identity
    assert projection.disposition == "RESTRICT"
    assert projection.expression_mode == "task_first"
    assert legacy["validation_status"] == "VERIFIED"
    assert legacy["decision_digest"] == decision.decision_digest
    assert legacy["scene_identity_v2"] == identity.to_dict()
    assert legacy["constraints"]["can_change_user_goal"] is False
    assert legacy["constraints"]["can_expand_scope"] is False
    assert legacy["constraints"]["can_reduce_risk"] is False
    assert legacy["user_goal_preserved"] is True


def test_projection_rejects_digest_tamper_and_exact_identity_drift() -> None:
    decision = _decision()
    identity = SceneIdentityV2.from_decision(decision).to_dict()
    tampered = decision.to_dict()
    tampered["scene_id"] = "scene.tampered"

    with pytest.raises(SceneConsumerProjectionError) as digest_error:
        project_scene_decision_v2(tampered, expected_identity=identity)
    assert digest_error.value.reason_code == SCENE_PROJECTION_INVALID

    wrong_identity = dict(identity)
    wrong_identity["revision"] = identity["revision"] + 1
    with pytest.raises(SceneConsumerProjectionError) as identity_error:
        project_scene_decision_v2(
            decision.to_dict(),
            expected_identity=wrong_identity,
        )
    assert identity_error.value.reason_code == SCENE_IDENTITY_MISMATCH


def test_surface_missing_scene_fails_closed_without_cancelling_explicit_goal() -> None:
    payload, guardrails = _surface_scene_guardrails(
        scene_decision_v2=None,
        scene_identity_v2=None,
        user_goal_ref="intent://explicit-user-task",
    )

    assert payload["validation_status"] == "REJECTED"
    assert payload["proactive_policy"] == SCENE_PROJECTION_BLOCK
    assert payload["user_goal_ref"] == "intent://explicit-user-task"
    assert payload["user_goal_preserved"] is True
    assert payload["permission_effect"] == "RESTRICT_ONLY"
    assert any("不能改写" in item or "绝不能改写" in item for item in guardrails)


def test_proactive_rejects_raw_scene_and_mismatched_frozen_identity() -> None:
    voice = PersonaProactiveVoice()
    raw_allowed, raw_reason = voice._scene_allows_scene(
        SCENE_MORNING_GREET,
        {"scene_awareness": {"scene_tags": ["general"]}},
    )
    decision = _decision(scene_id="scene.casual-chat")
    wrong_identity = SceneIdentityV2.from_decision(decision).to_dict()
    wrong_identity["workspace_id"] = "workspace_other"
    mismatch_allowed, mismatch_reason = voice._scene_allows_scene(
        SCENE_MORNING_GREET,
        {
            "scene_decision_v2": decision.to_dict(),
            "scene_identity_v2": wrong_identity,
        },
    )

    assert raw_allowed is False
    assert "SCENE_DECISION_V2_REQUIRED" in raw_reason
    assert mismatch_allowed is False
    assert SCENE_IDENTITY_MISMATCH in mismatch_reason


def test_production_scene_consumers_do_not_rebuild_legacy_scene() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    production_consumers = (
        repository_root / "host_brain/surface_runtime/eligibility.py",
        repository_root / "host_brain/persona/proactive_voice.py",
        repository_root / "host_brain/executive_runtime/skill_execution.py",
    )

    for path in production_consumers:
        assert "build_scene_decision" not in path.read_text(encoding="utf-8"), path
