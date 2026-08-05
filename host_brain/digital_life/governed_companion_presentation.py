"""Build one affective presentation for an accepted governed reply.

The helper is intentionally pure: it reads the canonical affective context
that was frozen at admission and returns deferred Acceptance intents.  It does
not mutate Life, deliver voice/avatar effects, or infer Natsume's state from a
worker result.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from host_brain.digital_life.affective_expression_delivery import (
    build_buffered_expression_delivery,
)
from host_brain.digital_life.affective_turn_planner import (
    AffectiveTurnPlanError,
    build_affective_turn_guidance,
)
from host_brain.digital_life.companion_voice import japanese_companion_voice_enabled
from natsume_contracts.life import digest_json


_VISIBLE_INPUT_SOURCES = frozenset({"CHAT", "GATEWAY", "HOST_INPUT", "WEB"})


class GovernedCompanionPresentationError(ValueError):
    """A visible governed result cannot be bound to its frozen Life context."""


def build_governed_companion_expression_intents(
    *,
    request_id: str,
    session_id: str,
    workspace_id: str,
    relationship_subject_id: str,
    runtime_context_schema: str,
    input_source: str,
    suppress_edge_tts: bool,
    suppress_edge_vts: bool,
    emotion_context: Mapping[str, Any],
    narrative: str,
    decision: str,
    task_mode: str,
    created_at_ms: int,
    metadata_source: str,
) -> tuple[dict[str, Any], ...]:
    """Freeze deferred Voice/Avatar intents for one user-visible result.

    Returning an empty tuple is deliberate for non-visible, rejected, muted,
    or legacy requests.  A current typed Runtime Context is fail-closed because
    its workspace/relationship identity is the delivery authority.
    """

    text = str(narrative or "").strip()
    if str(decision or "").strip().upper() != "APPROVE" or not text:
        return ()
    if str(input_source or "").strip().upper() not in _VISIBLE_INPUT_SOURCES:
        return ()
    if bool(suppress_edge_tts) or bool(suppress_edge_vts):
        return ()
    if str(runtime_context_schema or "").strip() != "natsume.runtime-context.request.v1":
        return ()

    request_ref = str(request_id or "").strip()
    session_ref = str(session_id or "").strip()
    workspace_ref = str(workspace_id or "").strip()
    subject_ref = str(relationship_subject_id or "").strip()
    if not all((request_ref, session_ref, workspace_ref, subject_ref)):
        raise GovernedCompanionPresentationError(
            "governed companion presentation lacks canonical runtime scope"
        )
    try:
        guidance = build_affective_turn_guidance(
            emotion_context=dict(emotion_context or {}),
            request_id=request_ref,
            workspace_id=workspace_ref,
            relationship_subject_id=subject_ref,
            session_id=session_ref,
            task_mode=str(task_mode or "").strip(),
            created_at_ms=max(0, int(created_at_ms or 0)),
            previous_avatar_expression="NEUTRAL",
            previous_expression_source="FALLBACK_NEUTRAL",
        )
    except (AffectiveTurnPlanError, TypeError, ValueError) as exc:
        raise GovernedCompanionPresentationError(str(exc)) from exc
    if guidance is None:
        return ()

    completed_at_ms = max(0, int(created_at_ms or 0))
    plan = guidance.plan.as_dict()
    expression_suppressed_reason = ""
    if japanese_companion_voice_enabled():
        # Skill and Delegate results do not own a second model call and do not
        # carry Surface's same-call Japanese speech field. Speaking their
        # Chinese/English result through the selected Japanese voice model is
        # worse than a deliberate text-only degradation.
        sentence_intents = ()
        controls = ()
        receipts = ()
        expression_suppressed_reason = "JAPANESE_SPEECH_TEXT_UNAVAILABLE"
    else:
        try:
            sentence_intents, controls, receipts = build_buffered_expression_delivery(
                affective_turn_plan=plan,
                narrative=text,
                created_at_ms=completed_at_ms,
            )
        except (TypeError, ValueError) as exc:
            raise GovernedCompanionPresentationError(
                "accepted governed result could not be compiled into an ExpressionPlan"
            ) from exc
    return (
        {
            "effect_type": "life.foreground_completed",
            "commit_scope": "NODE",
            "payload": {
                "command_type": "foreground_completed",
                "person_id": subject_ref,
                "command_id": f"life::foreground_completed::{request_ref}",
                "correlation_id": request_ref,
                "created_at_ms": completed_at_ms,
                "metadata": {
                    "source": str(metadata_source or "governed_companion_presentation")[:120],
                },
                "payload": {
                    "assistant_reply": text,
                    "workspace_id": workspace_ref,
                    "affective_turn_plan": plan,
                    "affective_turn_plan_digest": digest_json(plan),
                    "expression_delivery_intents": [
                        item.as_dict() for item in sentence_intents
                    ],
                    "expression_delivery_controls": [
                        item.as_dict() for item in controls
                    ],
                    "expression_delivery_receipts": [
                        item.as_dict() for item in receipts
                    ],
                    **(
                        {
                            "expression_delivery_suppressed_reason": expression_suppressed_reason,
                        }
                        if expression_suppressed_reason
                        else {}
                    ),
                    "legacy_direct_embodiment_delivery_retired": True,
                },
            },
        },
    )


__all__ = [
    "GovernedCompanionPresentationError",
    "build_governed_companion_expression_intents",
]
