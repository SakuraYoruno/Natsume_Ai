from __future__ import annotations

from types import SimpleNamespace

from host_brain.app.context.interaction_facts_plane import (
    assemble_interaction_facts_plane,
)
from host_brain.app.context.prompt_context_plane import (
    assemble_prompt_context_plane,
)
from host_brain.app.surface_context_packet import build_surface_context_packet
from host_brain.persona.context.relationship_state import (
    canonical_relationship_context_claimed,
    extract_canonical_relationship_projection,
    resolve_relationship_state,
)


def _canonical_context(
    *,
    workspace_id: str = "workspace-dl06",
    person_id: str = "person-dl06",
    session_id: str = "session-dl06",
    repair_required: bool = False,
    rupture_load: float = 0.0,
) -> dict[str, object]:
    return {
        "_affective_life_internal": True,
        "emotion_tag": "CALM",
        "emotion_intensity": 0.2,
        "affective_life": {
            "schema_version": "affective_turn_context.v1",
            "self_id": "natsume_ai",
            "workspace_id": workspace_id,
            "relationship_subject_id": person_id,
            "session_id": session_id,
            "self_affect": {"revision": 9, "components": []},
            "relationship_life": {
                "schema_version": "relationship_turn_context.v1",
                "workspace_id": workspace_id,
                "relationship_subject_id": person_id,
                "state": {
                    "schema_version": "relationship_state.v2",
                    "self_id": "natsume_ai",
                    "workspace_id": workspace_id,
                    "relationship_subject_id": person_id,
                    "revision": 17,
                    "trust": 0.95,
                    "safety": 0.96,
                    "attunement": 0.94,
                    "rupture_load": rupture_load,
                    "repair_required": repair_required,
                    "proactive_permission": "ALLOWED",
                    # These must never be converted into a legacy close stage.
                    "romantic_consent": "GRANTED",
                    "exclusive_consent": "GRANTED",
                    "dependency_consent": "GRANTED",
                    "active_episodes": [],
                    "last_aftereffect": None,
                },
            },
        },
    }


def _runtime_payload(*, emotion_context: dict[str, object]) -> dict[str, object]:
    return {
        "request": {
            "request_id": "request-dl06",
            "person_id": "person-dl06",
            "workspace_id": "workspace-dl06",
            "session_id": "session-dl06",
            "user_message": "今天有点累。",
        },
        "render": {"target_lang": "zh"},
        "emotion_context": emotion_context,
        "prompt_context": {
            "payload": {
                # A retired source may claim a close stage. The canonical
                # relationship must win without turning this into romance.
                "bond_summary": {"stage": "SOULMATE", "score": 0.99},
            },
        },
        "bond_summary": {"stage": "SOULMATE", "score": 0.99},
        "social_brain_state": {"bond_stage": "SOULMATE"},
        "retrieval": {"status": "SKIPPED", "evidence_items": []},
    }


def test_canonical_relationship_projection_cannot_become_romance_from_legacy_bond() -> None:
    context = _canonical_context()
    projection = extract_canonical_relationship_projection(
        context,
        expected_relationship_subject_id="person-dl06",
        expected_workspace_id="workspace-dl06",
        expected_session_id="session-dl06",
    )

    relationship = resolve_relationship_state(
        bond_summary={"stage": "SOULMATE", "score": 1.0},
        social_brain_state={"bond_stage": "SOULMATE"},
        canonical_relationship=projection,
        canonical_context_claimed=canonical_relationship_context_claimed(context),
    )

    assert relationship.source_authority == "CANONICAL_RELATIONSHIP_LIFE"
    assert relationship.bond_stage == "FRIEND"
    assert relationship.relationship_phase == "stepsibling"
    assert relationship.relationship_substage not in {
        "ambiguous_affection",
        "confessed_lovers",
        "stable_lovers",
        "soulbound",
    }
    assert relationship.romantic_tension == 0.10
    assert relationship.attachment_depth == 0.32
    assert relationship.trust == 0.95


def test_canonical_relationship_repair_is_visible_without_promoting_intimacy() -> None:
    context = _canonical_context(repair_required=True, rupture_load=0.18)
    projection = extract_canonical_relationship_projection(
        context,
        expected_relationship_subject_id="person-dl06",
        expected_workspace_id="workspace-dl06",
        expected_session_id="session-dl06",
    )
    relationship = resolve_relationship_state(
        bond_summary={"stage": "SOULMATE"},
        canonical_relationship=projection,
        canonical_context_claimed=True,
    )

    assert relationship.source_authority == "CANONICAL_RELATIONSHIP_LIFE"
    assert relationship.bond_stage == "FRIEND"
    assert relationship.relationship_substage == "repairing_rift"
    assert relationship.conflict_rift == 0.65


def test_mismatched_canonical_scope_fails_closed_without_legacy_bond_fallback() -> None:
    context = _canonical_context(person_id="other-person")
    projection = extract_canonical_relationship_projection(
        context,
        expected_relationship_subject_id="person-dl06",
        expected_workspace_id="workspace-dl06",
        expected_session_id="session-dl06",
    )
    relationship = resolve_relationship_state(
        bond_summary={"stage": "SOULMATE", "score": 1.0},
        canonical_relationship=projection,
        canonical_context_claimed=canonical_relationship_context_claimed(context),
    )

    assert projection is None
    assert relationship.source_authority == "CANONICAL_RELATIONSHIP_REJECTED"
    assert relationship.bond_stage == "FRIEND"
    assert relationship.relationship_phase == "stepsibling"


def test_incomplete_or_malformed_canonical_claim_fails_closed() -> None:
    context = _canonical_context()
    malformed_state = context["affective_life"]["relationship_life"]["state"]
    assert isinstance(malformed_state, dict)
    malformed_state["repair_required"] = "false"

    projection = extract_canonical_relationship_projection(
        context,
        expected_relationship_subject_id="person-dl06",
        expected_workspace_id="workspace-dl06",
        expected_session_id="session-dl06",
    )
    missing_scope_projection = extract_canonical_relationship_projection(
        _canonical_context(),
        expected_relationship_subject_id="person-dl06",
        expected_workspace_id="",
        expected_session_id="session-dl06",
    )
    relationship = resolve_relationship_state(
        bond_summary={"stage": "SOULMATE", "score": 1.0},
        canonical_relationship=projection,
        canonical_context_claimed=canonical_relationship_context_claimed(context),
    )

    assert projection is None
    assert missing_scope_projection is None
    assert relationship.source_authority == "CANONICAL_RELATIONSHIP_REJECTED"
    assert relationship.bond_stage == "FRIEND"


def test_prompt_and_interaction_planes_prefer_canonical_relationship_state() -> None:
    runtime_payload = _runtime_payload(emotion_context=_canonical_context())

    prompt_plane = assemble_prompt_context_plane(
        node_id="dl06-canonical-relationship",
        runtime_payload=runtime_payload,
    )
    facts_plane = assemble_interaction_facts_plane(
        runtime_payload=runtime_payload,
    )

    assert prompt_plane.relationship.state.source_authority == "CANONICAL_RELATIONSHIP_LIFE"
    assert prompt_plane.relationship.state.bond_stage == "FRIEND"
    assert facts_plane.relationship_state["source_authority"] == "CANONICAL_RELATIONSHIP_LIFE"
    assert facts_plane.relationship_stage == "FRIEND"


def test_surface_packet_keeps_canonical_relationship_at_familiar_distance() -> None:
    context = _canonical_context()
    packet = build_surface_context_packet(
        life_snapshot_reader=SimpleNamespace(
            get_snapshot=lambda person_id: {
                "person_id": person_id,
                "version": 0,
                "state": {"person_id": person_id, "version": 0},
            }
        ),
        user_message="今天有点累。",
        request_id="request-dl06",
        person_id="person-dl06",
        workspace_id="workspace-dl06",
        session_id="session-dl06",
        emotion_context=context,
        bond_summary={"stage": "SOULMATE", "score": 1.0},
    )

    assert packet["relationship_state"]["source_authority"] == "CANONICAL_RELATIONSHIP_LIFE"
    assert packet["relationship_state"]["bond_stage"] == "FRIEND"
    assert packet["surface_affect_packet"]["relationship_distance"] == "familiar"
