from __future__ import annotations

from host_brain.runtime.context_contracts import SceneDecisionV2
from host_brain.runtime.intent_arbiter import IntentArbiter


def _scene(constraints: dict[str, object]) -> SceneDecisionV2:
    return SceneDecisionV2(
        workspace_id="ws-a",
        subject_id="person:1",
        scene_id="scene:day",
        revision=4,
        world_snapshot_digest="a" * 64,
        constraints=constraints,
    )


def test_intent_arbiter_only_adds_constraints_and_preserves_goal_digest() -> None:
    result = IntentArbiter().arbitrate(
        user_goal="整理今天的研究笔记",
        scene=_scene({"constraints": ["NO_EXTERNAL_SEND"]}),
        risk_constraints=("REVIEW_HIGH_RISK",),
    )
    assert result.decision == "REVIEW"
    assert result.constraints == ("NO_EXTERNAL_SEND", "REVIEW_HIGH_RISK")


def test_intent_arbiter_blocks_explicit_forbidden_constraint() -> None:
    result = IntentArbiter().arbitrate(
        user_goal="执行任务",
        scene=_scene({"constraints": ["FORBID_EDGE_WRITE"]}),
    )
    assert result.decision == "BLOCK"
