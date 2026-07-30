from __future__ import annotations

import pytest

from host_brain.app.schemas import ChatRequest
from host_brain.digital_life.affect_fusion import AffectiveScope
from host_brain.digital_life.relationship_lifecycle import RelationshipScope
from host_brain.runtime.conversation_scope import (
    GatewayConversationScopeError,
    SecureGatewayConversationScopeV1,
)


def _scope(
    *,
    sender: str = "gateway-sender-a",
    conversation: str = "gateway-conversation-a",
) -> SecureGatewayConversationScopeV1:
    return SecureGatewayConversationScopeV1.derive(
        principal_id="service.edge.gateway",
        tenant_id="workspace-companion",
        platform="qq",
        from_id=sender,
        person_id="caller-selected-person-must-not-win",
        conversation_id=conversation,
    )


def test_signed_gateway_scope_keeps_sender_and_session_separate_from_raw_ids() -> None:
    first = _scope()
    same_sender_new_conversation = _scope(conversation="gateway-conversation-b")
    different_sender = _scope(sender="gateway-sender-b")

    assert first.workspace_id == "tenant:workspace-companion"
    assert first.relationship_subject_id == same_sender_new_conversation.relationship_subject_id
    assert first.session_id != same_sender_new_conversation.session_id
    assert first.relationship_subject_id != different_sender.relationship_subject_id
    assert "gateway-sender-a" not in first.relationship_subject_id
    assert "gateway-conversation-a" not in first.session_id
    assert "caller-selected-person-must-not-win" not in first.to_dict().values()

    first_request = ChatRequest(
        message="我今天有些低落。",
        workspace_id=first.workspace_id,
        person_id=first.relationship_subject_id,
        session_id=first.session_id,
    )
    second_request = ChatRequest(
        message="我今天有些低落。",
        workspace_id=different_sender.workspace_id,
        person_id=different_sender.relationship_subject_id,
        session_id=different_sender.session_id,
    )

    first_affect = AffectiveScope.from_request(first_request)
    second_affect = AffectiveScope.from_request(second_request)
    first_relationship = RelationshipScope.from_request(first_request)
    second_relationship = RelationshipScope.from_request(second_request)

    assert first_affect.workspace_id == first.workspace_id
    assert first_affect.relationship_subject_id == first.relationship_subject_id
    assert first_relationship.aggregate_id != second_relationship.aggregate_id
    assert first_affect.aggregate_id != second_affect.aggregate_id


def test_gateway_scope_refuses_to_treat_a_conversation_as_a_sender() -> None:
    with pytest.raises(
        GatewayConversationScopeError,
        match="TRANSPORT_SUBJECT_REQUIRED",
    ):
        SecureGatewayConversationScopeV1.derive(
            principal_id="service.edge.gateway",
            tenant_id="workspace-companion",
            platform="qq",
            conversation_id="group-conversation-is-not-a-person",
        )


def test_gateway_scope_reopen_rejects_cross_tenant_authority_copy() -> None:
    scope = _scope()
    reopened = SecureGatewayConversationScopeV1.from_command_authority(
        scope.as_command_authority()
    )

    assert reopened == scope
    with pytest.raises(
        GatewayConversationScopeError,
        match="INGRESS_AUTHORITY_DRIFT",
    ):
        scope.assert_bound_to_authority(
            {
                "principal_id": "service.edge.gateway",
                "tenant_id": "workspace-other",
            }
        )
