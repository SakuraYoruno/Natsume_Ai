from __future__ import annotations

import pytest

from host_brain.runtime.context_contracts import (
    ContextContractError,
    ContextPackV2,
    SceneDecisionV2,
    WorldFactV2,
    WorldSnapshotV2,
)


def _fact(source: str, value: str) -> WorldFactV2:
    return WorldFactV2(
        workspace_id="workspace-a",
        subject_id="user-a",
        key="user.mood",
        value=value,
        source_ref=f"observation://{source}",
        confidence=0.8,
        observed_at_ms=100,
        expires_at_ms=200,
    )


def test_ne03_snapshot_preserves_competing_sources_and_is_digest_bound() -> None:
    snapshot = WorldSnapshotV2(
        workspace_id="workspace-a",
        subject_id="user-a",
        revision=3,
        facts=(_fact("life", "calm"), _fact("surface", "busy")),
    )

    assert len(snapshot.competing_facts("user.mood")) == 2
    assert len(snapshot.snapshot_digest) == 64
    assert snapshot.to_dict()["revision"] == 3


def test_ne03_context_pack_rejects_cross_workspace_fact() -> None:
    foreign = WorldFactV2(
        workspace_id="workspace-b",
        subject_id="user-a",
        key="user.mood",
        value="secret",
        source_ref="observation://foreign",
        confidence=1.0,
        observed_at_ms=100,
        expires_at_ms=200,
    )
    with pytest.raises(ContextContractError, match="scope mismatch"):
        ContextPackV2(
            workspace_id="workspace-a",
            subject_id="user-a",
            task_graph_id="graph-a",
            graph_revision=1,
            world_snapshot_digest="a" * 64,
            facts=(foreign,),
        )


def test_ne03_scene_decision_digest_rejects_mutation() -> None:
    decision = SceneDecisionV2(
        workspace_id="workspace-a",
        subject_id="user-a",
        scene_id="scene.deep-work",
        revision=1,
        world_snapshot_digest="b" * 64,
        constraints={"interruptions": "low"},
    )
    with pytest.raises(ContextContractError, match="scene decision digest drift"):
        SceneDecisionV2(
            workspace_id="workspace-a",
            subject_id="user-a",
            scene_id="scene.deep-work",
            revision=1,
            world_snapshot_digest="b" * 64,
            constraints={"interruptions": "high"},
            decision_digest=decision.decision_digest,
        )
