"""Authority-verified intent arbitration that can only tighten constraints."""

from __future__ import annotations

from collections.abc import Callable

from .context_contracts import (
    ContextConstraintV1,
    IntentArbitrationV1,
    IntentFrameV1,
    SceneDecisionV2,
)
from .intent_authority import (
    IntentAuthorityError,
    IntentAuthorityPort,
    IntentAuthorityVerifier,
    IntentPolicyIdentityV1,
)


class IntentArbiter:
    """Combine only canonical constraints without replacing the user's goal."""

    def __init__(
        self,
        *,
        authority_port: IntentAuthorityPort,
        policy_identity: IntentPolicyIdentityV1,
        clock: Callable[[], int] | None = None,
    ) -> None:
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable")
        self._authority_verifier = IntentAuthorityVerifier(
            authority_port=authority_port,
            policy_identity=policy_identity,
            clock=clock,
        )

    def arbitrate(
        self,
        *,
        frame: IntentFrameV1,
        scene: SceneDecisionV2,
    ) -> IntentArbitrationV1:
        authority = self._authority_verifier.verify(frame=frame, scene=scene)

        authority_sets = (
            authority.policy_constraints,
            authority.scene_constraints,
            authority.task_constraints,
            authority.relationship_constraints,
            authority.long_term_goal_constraints,
            authority.risk_constraints,
            authority.grant_constraints,
        )
        merged: dict[tuple[str, str, str], ContextConstraintV1] = {}
        for values in authority_sets:
            for constraint in values:
                identity = (
                    constraint.origin,
                    constraint.source_ref,
                    constraint.code,
                )
                existing = merged.get(identity)
                if (
                    existing is not None
                    and existing.constraint_digest != constraint.constraint_digest
                ):
                    raise IntentAuthorityError(
                        "canonical Intent constraints contain a conflicting identity"
                    )
                merged[identity] = constraint
        constraints = tuple(
            sorted(merged.values(), key=lambda item: item.constraint_digest)
        )
        dispositions = {item.disposition for item in constraints}
        if "BLOCK" in dispositions:
            decision = "BLOCK"
        elif "REVIEW" in dispositions:
            decision = "REVIEW"
        elif constraints:
            decision = "RESTRICT"
        else:
            decision = "NO_ADDITIONAL_RESTRICTION"
        result = IntentArbitrationV1(
            intent_frame_digest=authority.intent_frame_digest,
            workspace_id=authority.workspace_id,
            subject_id=authority.subject_id,
            principal_id=authority.principal_id,
            intent_origin=authority.intent_origin,
            goal_ref=authority.goal_ref,
            goal_digest=authority.goal_digest,
            task_graph_id=authority.task_graph_id,
            graph_revision=authority.graph_revision,
            task_node_id=authority.task_node_id,
            context_pack_digest=authority.context_pack_digest,
            scene_revision=authority.scene_revision,
            scene_decision_digest=authority.scene_decision_digest,
            requested_action_ref=authority.requested_action_ref,
            requested_action_digest=authority.requested_action_digest,
            grant_ref=authority.grant_ref,
            grant_digest=authority.grant_digest,
            arbiter_policy_ref=authority.policy_ref,
            arbiter_policy_digest=authority.policy_digest,
            constraints=constraints,
            decision=decision,
        )
        declared = {
            (item.origin, item.source_ref, item.code)
            for values in (
                scene.constraints,
                frame.task_constraints,
                frame.relationship_constraints,
                frame.long_term_goal_constraints,
                frame.risk_constraints,
                frame.grant_constraints,
            )
            for item in values
        }
        authority_only = tuple(
            item
            for item in constraints
            if (item.origin, item.source_ref, item.code) not in declared
        )
        if result.constraints != constraints:
            raise IntentAuthorityError(
                "Intent arbitration changed canonical authority constraints"
            )
        result.verify_against(
            frame=frame,
            scene=scene,
            authoritative_constraints=authority_only,
        )
        return result


__all__ = ["IntentArbiter"]
