from __future__ import annotations

from dataclasses import replace

import pytest

from host_brain.contracts.runtime_context import (
    ContextConstraintV1,
    ContextContractError,
    ContextPackV2,
    IntentFrameV1,
    SceneDecisionV2,
    WorldFactV2,
    WorldObservationBatchV1,
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
    quiet = ContextConstraintV1(
        code="LOW_INTERRUPTION",
        disposition="RESTRICT",
        origin="SCENE",
        source_ref="policy://scene/v1/quiet",
    )
    decision = SceneDecisionV2(
        workspace_id="workspace-a",
        subject_id="user-a",
        scene_id="scene.deep-work",
        revision=1,
        world_snapshot_digest="b" * 64,
        context_digest="c" * 64,
        guardian_snapshot_digest="d" * 64,
        constraints=(quiet,),
    )
    noisy = ContextConstraintV1(
        code="HIGH_RISK_REVIEW",
        disposition="REVIEW",
        origin="SCENE",
        source_ref="policy://scene/v1/noisy",
    )
    with pytest.raises(ContextContractError, match="scene decision digest drift"):
        SceneDecisionV2(
            workspace_id="workspace-a",
            subject_id="user-a",
            scene_id="scene.deep-work",
            revision=1,
            world_snapshot_digest="b" * 64,
            context_digest="c" * 64,
            guardian_snapshot_digest="d" * 64,
            constraints=(noisy,),
            decision_digest=decision.decision_digest,
        )


def test_ne03_intent_frame_binds_exact_action_grant_and_all_constraint_sources() -> None:
    constraint = ContextConstraintV1(
        code="REVIEW_REQUIRED",
        disposition="REVIEW",
        origin="TASK",
        source_ref="policy://intent/v1/review",
    )
    frame = IntentFrameV1(
        workspace_id="workspace-a",
        subject_id="user-a",
        principal_id="principal-a",
        intent_origin="EXPLICIT_USER",
        goal_ref="user-goal://workspace-a/one",
        goal_digest="1" * 64,
        task_graph_id="graph-a",
        graph_revision=2,
        task_node_id="node-a",
        context_pack_digest="5" * 64,
        scene_decision_digest="2" * 64,
        scene_revision=3,
        requested_action_ref="action://graph-a/node-a",
        requested_action_digest="3" * 64,
        grant_ref="grant://graph-a/node-a",
        grant_digest="4" * 64,
        arbiter_policy_ref="policy://intent-arbiter/v1",
        arbiter_policy_digest="6" * 64,
        task_constraints=(constraint,),
        relationship_constraints=(),
        long_term_goal_refs=("goal://workspace-a/one",),
        long_term_goal_constraints=(),
        risk_constraints=(),
        grant_constraints=(),
    )

    assert len(frame.frame_digest) == 64
    with pytest.raises(ContextContractError, match="intent frame digest drift"):
        replace(frame, graph_revision=4)


def test_ne03_world_fact_rejects_control_characters_in_durable_reference() -> None:
    with pytest.raises(ContextContractError, match="durable reference"):
        WorldFactV2(
            workspace_id="workspace-a",
            subject_id="user-a",
            key="user.mood",
            value="calm",
            source_ref="observation://life\nforged",
            confidence=0.8,
            observed_at_ms=100,
            expires_at_ms=200,
        )


def test_ne03_observation_batch_enforces_sqlite_payload_limit() -> None:
    oversized = WorldFactV2(
        workspace_id="workspace-a",
        subject_id="user-a",
        key="oversized.value",
        value="x" * 1_048_576,
        source_ref="observation://oversized",
        confidence=1.0,
        observed_at_ms=100,
        expires_at_ms=200,
    )
    with pytest.raises(ContextContractError, match="payload limit"):
        WorldObservationBatchV1(
            workspace_id="workspace-a",
            subject_id="user-a",
            runtime_scope_id="runtime-scope-a",
            runtime_owner_id="owner-a",
            runtime_lease_id="lease-a",
            fencing_token=1,
            revision=1,
            previous_batch_digest="",
            source_event_ref="observation-event://oversized/1",
            source_event_digest="a" * 64,
            observed_at_ms=100,
            upserts=(oversized,),
        )
