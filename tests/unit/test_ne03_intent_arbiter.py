from __future__ import annotations

from dataclasses import replace
import hashlib

import pytest

from host_brain.contracts.runtime_context import (
    ContextConstraintV1,
    IntentFrameV1,
    SceneDecisionV2,
)
from host_brain.runtime.intent_arbiter import IntentArbiter
from host_brain.runtime.intent_authority import (
    IntentAuthorityAssembler,
    IntentAuthorityAttestationV1,
    IntentAuthorityError,
    IntentPolicyIdentityV1,
)


NOW_MS = 10_000
POLICY = IntentPolicyIdentityV1(
    policy_ref="policy://intent-arbiter/v1",
    policy_digest="1" * 64,
)


def _constraint(
    code: str,
    disposition: str,
    *,
    origin: str,
) -> ContextConstraintV1:
    source_ref = (
        "grant://graph-a/node-a"
        if origin == "GRANT"
        else "goal://ws-a/research"
        if origin == "LONG_TERM_GOAL"
        else f"policy://intent/v1/{code.lower()}"
    )
    return ContextConstraintV1(
        code=code,
        disposition=disposition,
        origin=origin,
        source_ref=source_ref,
    )


def _scene(*constraints: ContextConstraintV1) -> SceneDecisionV2:
    return SceneDecisionV2(
        workspace_id="ws-a",
        subject_id="person:1",
        scene_id="scene.research",
        revision=4,
        world_snapshot_digest="a" * 64,
        context_digest="b" * 64,
        guardian_snapshot_digest="c" * 64,
        constraints=tuple(constraints),
    )


def _frame(
    scene: SceneDecisionV2,
    *,
    risk_constraints: tuple[ContextConstraintV1, ...] = (),
    grant_constraints: tuple[ContextConstraintV1, ...] = (),
    policy: IntentPolicyIdentityV1 = POLICY,
) -> IntentFrameV1:
    return IntentFrameV1(
        workspace_id=scene.workspace_id,
        subject_id=scene.subject_id,
        principal_id="principal-a",
        intent_origin="EXPLICIT_USER",
        goal_ref="user-goal://ws-a/research-notes",
        goal_digest=hashlib.sha256("整理今天的研究笔记".encode()).hexdigest(),
        task_graph_id="graph-a",
        graph_revision=2,
        task_node_id="node-a",
        context_pack_digest="f" * 64,
        scene_decision_digest=scene.decision_digest,
        scene_revision=scene.revision,
        requested_action_ref="action://graph-a/node-a",
        requested_action_digest="d" * 64,
        grant_ref="grant://graph-a/node-a",
        grant_digest="e" * 64,
        arbiter_policy_ref=policy.policy_ref,
        arbiter_policy_digest=policy.policy_digest,
        task_constraints=(),
        relationship_constraints=(),
        long_term_goal_refs=("goal://ws-a/research",),
        long_term_goal_constraints=(),
        risk_constraints=risk_constraints,
        grant_constraints=grant_constraints,
    )


def _attestation(
    frame: IntentFrameV1,
    scene: SceneDecisionV2,
    **overrides: object,
) -> IntentAuthorityAttestationV1:
    values: dict[str, object] = {
        "canonical_frame": frame,
        "canonical_scene": scene,
        "policy_identity": POLICY,
        "authority_ref": "intent-authority://ws-a/graph-a/node-a",
        "attested_at_ms": NOW_MS,
        "grant_status": "ACTIVE",
        "grant_issued_at_ms": NOW_MS - 1_000,
        "grant_expires_at_ms": NOW_MS + 1_000,
        "canonical_policy_constraints": (),
    }
    values.update(overrides)
    return IntentAuthorityAssembler.assemble(**values)  # type: ignore[arg-type]


class _AuthorityPort:
    def __init__(self, attestation: object) -> None:
        self.attestation = attestation
        self.queries: list[dict[str, object]] = []

    def resolve_intent_authority(self, **query: object) -> object:
        self.queries.append(dict(query))
        return self.attestation


def _arbiter(attestation: object) -> IntentArbiter:
    return IntentArbiter(
        authority_port=_AuthorityPort(attestation),
        policy_identity=POLICY,
        clock=lambda: NOW_MS,
    )


def test_intent_arbiter_only_tightens_and_preserves_exact_authority() -> None:
    scene_constraint = _constraint(
        "SOURCE_EVIDENCE_REQUIRED", "RESTRICT", origin="SCENE"
    )
    policy_constraint = _constraint(
        "NETWORK_PREVIEW_REQUIRED", "RESTRICT", origin="POLICY"
    )
    risk_constraint = _constraint("HIGH_RISK_REVIEW", "REVIEW", origin="RISK")
    scene = _scene(scene_constraint, policy_constraint)
    frame = _frame(scene, risk_constraints=(risk_constraint,))

    result = _arbiter(_attestation(frame, scene)).arbitrate(
        frame=frame,
        scene=scene,
    )

    assert result.decision == "REVIEW"
    assert result.goal_digest == frame.goal_digest
    assert result.requested_action_digest == frame.requested_action_digest
    assert result.grant_digest == frame.grant_digest
    assert result.arbiter_policy_ref == POLICY.policy_ref
    assert result.arbiter_policy_digest == POLICY.policy_digest
    assert {item.code for item in result.constraints} == {
        "SOURCE_EVIDENCE_REQUIRED",
        "NETWORK_PREVIEW_REQUIRED",
        "HIGH_RISK_REVIEW",
    }


def test_intent_arbiter_blocks_typed_block_constraint() -> None:
    scene = _scene()
    blocked = _constraint("EDGE_WRITE_BLOCKED", "BLOCK", origin="GRANT")
    frame = _frame(scene, grant_constraints=(blocked,))

    result = _arbiter(_attestation(frame, scene)).arbitrate(
        frame=frame,
        scene=scene,
    )

    assert result.decision == "BLOCK"


def test_intent_arbiter_includes_composition_policy_authority_constraints() -> None:
    scene = _scene()
    frame = _frame(scene)
    policy_block = ContextConstraintV1(
        code="POLICY_ACTION_BLOCKED",
        disposition="BLOCK",
        origin="POLICY",
        source_ref=POLICY.policy_ref,
    )

    result = _arbiter(
        _attestation(
            frame,
            scene,
            canonical_policy_constraints=(policy_block,),
        )
    ).arbitrate(frame=frame, scene=scene)

    assert result.decision == "BLOCK"
    assert result.constraints == (policy_block,)


def test_intent_arbiter_rejects_cross_source_constraint_identity_conflict() -> None:
    scene_policy = ContextConstraintV1(
        code="POLICY_ACTION_REVIEW",
        disposition="REVIEW",
        origin="POLICY",
        source_ref=POLICY.policy_ref,
    )
    current_policy = replace(scene_policy, disposition="BLOCK")
    scene = _scene(scene_policy)
    frame = _frame(scene)

    with pytest.raises(IntentAuthorityError, match="conflicting identity"):
        _arbiter(
            _attestation(
                frame,
                scene,
                canonical_policy_constraints=(current_policy,),
            )
        ).arbitrate(frame=frame, scene=scene)


def test_intent_arbiter_rejects_scene_identity_drift() -> None:
    scene = _scene()
    frame = _frame(scene)
    drifted = _scene(
        _constraint("LOW_INTERRUPTION", "RESTRICT", origin="SCENE")
    )

    with pytest.raises(IntentAuthorityError, match="binding drift"):
        _arbiter(_attestation(frame, scene)).arbitrate(
            frame=_frame(drifted),
            scene=drifted,
        )

    with pytest.raises(IntentAuthorityError, match="canonical Scene"):
        _arbiter(_attestation(frame, scene)).arbitrate(
            frame=frame,
            scene=drifted,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("workspace_id", "ws-b"),
        ("subject_id", "person:2"),
        ("principal_id", "principal-b"),
        ("task_graph_id", "graph-b"),
        ("graph_revision", 3),
        ("task_node_id", "node-b"),
        ("context_pack_digest", "2" * 64),
        ("scene_id", "scene.debugging"),
        ("scene_revision", 5),
        ("scene_decision_digest", "3" * 64),
        ("scene_context_digest", "4" * 64),
        ("requested_action_ref", "action://graph-a/node-b"),
        ("requested_action_digest", "5" * 64),
        ("grant_ref", "grant://graph-a/node-b"),
        ("grant_digest", "6" * 64),
        ("policy_ref", "policy://intent-arbiter/v2"),
        ("policy_digest", "7" * 64),
    ],
)
def test_intent_arbiter_rejects_every_drifted_authority_binding(
    field: str,
    value: object,
) -> None:
    scene = _scene()
    frame = _frame(scene)
    attestation = replace(
        _attestation(frame, scene),
        **{field: value, "attestation_digest": ""},
    )

    with pytest.raises(IntentAuthorityError, match="binding drift"):
        _arbiter(attestation).arbitrate(frame=frame, scene=scene)


def test_intent_arbiter_rejects_noncanonical_constraint_projection() -> None:
    risk = _constraint("HIGH_RISK_REVIEW", "REVIEW", origin="RISK")
    scene = _scene()
    frame = _frame(scene, risk_constraints=(risk,))
    attestation = replace(
        _attestation(frame, scene),
        risk_constraints=(),
        attestation_digest="",
    )

    with pytest.raises(IntentAuthorityError, match="risk_constraints"):
        _arbiter(attestation).arbitrate(frame=frame, scene=scene)


@pytest.mark.parametrize(
    "overrides",
    [
        {"grant_status": "EXPIRED", "grant_expires_at_ms": NOW_MS},
        {"grant_status": "REVOKED", "grant_revoked_at_ms": NOW_MS - 10},
        {"grant_status": "DENIED"},
        {"grant_expires_at_ms": NOW_MS},
        {"grant_principal_id": "principal-b"},
        {"attested_at_ms": NOW_MS + 1},
        {"attested_at_ms": NOW_MS - 5_001},
        {
            "attested_at_ms": NOW_MS - 500,
            "grant_issued_at_ms": NOW_MS - 100,
        },
    ],
)
def test_intent_arbiter_rejects_inactive_or_unbound_grant(
    overrides: dict[str, object],
) -> None:
    scene = _scene()
    frame = _frame(scene)

    with pytest.raises(IntentAuthorityError, match="Grant is inactive"):
        _arbiter(_attestation(frame, scene, **overrides)).arbitrate(
            frame=frame,
            scene=scene,
        )


def test_intent_arbiter_rejects_caller_reported_policy_identity() -> None:
    scene = _scene()
    caller_policy = IntentPolicyIdentityV1(
        policy_ref="policy://intent-arbiter/caller-selected",
        policy_digest="8" * 64,
    )
    frame = _frame(scene, policy=caller_policy)

    with pytest.raises(IntentAuthorityError, match="composition authority"):
        _arbiter(None).arbitrate(frame=frame, scene=scene)


def test_intent_arbiter_fails_closed_without_complete_typed_attestation() -> None:
    scene = _scene()
    frame = _frame(scene)

    with pytest.raises(IntentAuthorityError, match="no typed"):
        _arbiter(None).arbitrate(frame=frame, scene=scene)

    with pytest.raises(TypeError, match="resolve_intent_authority"):
        IntentArbiter(
            authority_port=object(),  # type: ignore[arg-type]
            policy_identity=POLICY,
        )
