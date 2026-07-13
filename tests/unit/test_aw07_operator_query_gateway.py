from __future__ import annotations

import hashlib
import json

import pytest
from fastapi import HTTPException

from host_brain.observability.operator_query_gateway import (
    DEFAULT_FORBIDDEN_PRIVATE_FIELDS,
    DigestOperatorQueryAuthenticator,
    OPERATOR_QUERY_AUTH_BINDINGS_ENV,
    OperatorQueryAuthStatus,
    OperatorQueryGateway,
    required_operator_query_scope,
)


def _gateway(monkeypatch: pytest.MonkeyPatch, *, token: str = "operator-test-token") -> OperatorQueryGateway:
    monkeypatch.setenv(
        OPERATOR_QUERY_AUTH_BINDINGS_ENV,
        json.dumps(
            [
                {
                    "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                    "principal_id": "ops.test",
                    "scopes": ["obs:trace:read"],
                }
            ]
        ),
    )
    return OperatorQueryGateway.from_environment()


def test_operator_auth_binds_server_principal_and_scope_by_token_digest(monkeypatch) -> None:
    gateway = _gateway(monkeypatch)

    context = gateway.open_context(authorization_values=("Bearer operator-test-token",))

    assert context.authority.auth_status is OperatorQueryAuthStatus.AUTHENTICATED
    assert context.authority.principal_id == "ops.test"
    assert context.authority.allows("obs:trace:read")
    assert context.authority.allows("obs:public:read")
    assert "operator-test-token" not in str(context.authority)
    assert context.authority.auth_ref.startswith("operator-auth://sha256/")


def test_missing_duplicate_and_invalid_credentials_never_gain_private_scope(monkeypatch) -> None:
    gateway = _gateway(monkeypatch)

    anonymous = gateway.open_context(authorization_values=())
    invalid = gateway.open_context(authorization_values=("Bearer wrong",))
    duplicate = gateway.open_context(
        authorization_values=("Bearer operator-test-token", "Bearer operator-test-token")
    )

    assert anonymous.authority.auth_status is OperatorQueryAuthStatus.ANONYMOUS
    assert invalid.authority.auth_status is OperatorQueryAuthStatus.INVALID
    assert duplicate.authority.auth_status is OperatorQueryAuthStatus.INVALID
    for context in (anonymous, invalid, duplicate):
        assert context.authority.scopes == frozenset({"obs:public:read"})
        with pytest.raises(HTTPException) as error:
            context.require_scope("obs:trace:read")
        assert error.value.status_code in {401, 403}


def test_bindings_reject_raw_tokens_and_invalid_scope_configuration(monkeypatch) -> None:
    monkeypatch.setenv(
        OPERATOR_QUERY_AUTH_BINDINGS_ENV,
        json.dumps(
            [
                {
                    "token": "raw-token-must-not-be-configured",
                    "principal_id": "ops.test",
                    "scopes": ["obs:trace:read"],
                }
            ]
        ),
    )
    with pytest.raises(RuntimeError, match="OPERATOR_QUERY_AUTH_BINDINGS_INVALID"):
        DigestOperatorQueryAuthenticator.from_environment()


def test_default_serializer_removes_private_fields_at_every_depth_and_emits_refs() -> None:
    gateway = OperatorQueryGateway(DigestOperatorQueryAuthenticator())
    source = {
        "request_id": "req-1",
        "summary": {
            "input_text": "user private body",
            "output_text": "assistant private body",
            "normalized_intent": {"intent": "private"},
            "status": "DONE",
        },
        "timeline": [
            {
                "raw_text": "timeline private body",
                "private_reasoning": "chain of thought",
                "api_key": "secret-value",
                "reason_code": "OK",
            }
        ],
    }

    result = gateway.serialize_public(source)
    encoded = json.dumps(result.payload, ensure_ascii=False)

    assert result.payload["request_id"] == "req-1"
    assert result.payload["summary"]["status"] == "DONE"
    for field in DEFAULT_FORBIDDEN_PRIVATE_FIELDS | {"api_key"}:
        assert f'"{field}"' not in encoded or field in {
            item["field"]
            for node in (result.payload["summary"], result.payload["timeline"][0])
            for item in node["_operator_privacy"]["withheld"]
        }
    assert "user private body" not in encoded
    assert "assistant private body" not in encoded
    assert "timeline private body" not in encoded
    assert "chain of thought" not in encoded
    assert "secret-value" not in encoded
    assert result.redacted_fields == 6
    assert len(result.citation_refs) == 6
    assert all("sha256" in ref for ref in result.citation_refs)


def test_content_field_allowlist_fails_closed_for_new_body_names_and_secret_values() -> None:
    gateway = OperatorQueryGateway(DigestOperatorQueryAuthenticator())

    result = gateway.serialize_public(
        {
            "prompt_text": "new private prompt",
            "message_body": "new private message",
            "input_preview": "session private preview",
            "reply_preview": "session private reply",
            "description": "safe operational description",
            "detail": "Authorization: Bearer abcdefghijklmnop",
        }
    )
    encoded = json.dumps(result.payload)

    assert "new private prompt" not in encoded
    assert "new private message" not in encoded
    assert "session private preview" not in encoded
    assert "session private reply" not in encoded
    assert "safe operational description" not in encoded
    assert "Bearer" not in encoded
    assert result.redacted_fields == 6


def test_projection_serializer_rejects_token_aliases_and_bare_secret_refs() -> None:
    gateway = OperatorQueryGateway(DigestOperatorQueryAuthenticator())

    result = gateway.serialize_public(
        {
            "detail": "approval::super-secret",
            "preview": "private user sentence",
            "payload": {
                "resume_token_ref": "review_resume::abc123",
                "confirm_token_ref": "confirm::xyz987",
            },
        }
    )
    encoded = json.dumps(result.payload)

    assert "super-secret" not in encoded
    assert "private user sentence" not in encoded
    assert "abc123" not in encoded
    assert "xyz987" not in encoded
    assert result.redacted_fields == 4


def test_projection_serializer_normalizes_camel_case_private_aliases() -> None:
    gateway = OperatorQueryGateway(DigestOperatorQueryAuthenticator())

    result = gateway.serialize_public(
        {
            "accessToken": "opaque-access-secret",
            "privateReasoning": "hidden chain of thought",
            "rawText": "private raw message",
            "messageBody": "private message body",
            "apiKey": "opaque-api-secret",
            "statusCode": 200,
        }
    )
    encoded = json.dumps(result.payload)

    assert "opaque-access-secret" not in encoded
    assert "hidden chain of thought" not in encoded
    assert "private raw message" not in encoded
    assert "private message body" not in encoded
    assert "opaque-api-secret" not in encoded
    assert result.payload["statusCode"] == 200
    assert result.redacted_fields == 5


@pytest.mark.parametrize(
    ("method", "path", "scope"),
    [
        ("POST", "/observability/trace", "obs:trace:read"),
        ("GET", "/obs/trace/req-1", "obs:trace:read"),
        ("GET", "/obs/trace/req-1/stream_events", "obs:trace:read"),
        ("GET", "/obs/trace-feed/snapshot", "obs:trace:read"),
        ("GET", "/obs/trace-feed/live", "obs:trace:read"),
        ("GET", "/obs/tribe/events", "obs:trace:read"),
        ("GET", "/obs/sessions/export", "obs:session:export"),
        ("GET", "/obs/sessions", "obs:session:read"),
        ("GET", "/obs/summary", None),
    ],
)
def test_sensitive_route_scope_policy_is_server_owned(method, path, scope) -> None:
    assert required_operator_query_scope(method=method, path=path) == scope


def test_authenticated_low_scope_principal_is_denied_sensitive_trace(monkeypatch) -> None:
    token = "session-export-only-token"
    monkeypatch.setenv(
        OPERATOR_QUERY_AUTH_BINDINGS_ENV,
        json.dumps(
            [
                {
                    "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                    "principal_id": "ops.export-only",
                    "scopes": ["obs:session:export"],
                }
            ]
        ),
    )
    gateway = OperatorQueryGateway.from_environment()
    context = gateway.open_context(authorization_values=(f"Bearer {token}",))

    with pytest.raises(HTTPException) as error:
        gateway.authorize_route(context, method="GET", path="/obs/trace/req-private")

    assert error.value.status_code == 403
    assert context.authority.allows("obs:session:export")
    assert not context.authority.allows("obs:trace:read")


@pytest.mark.parametrize(
    ("path", "scope"),
    [
        ("/obs/task-runtime/executions", "obs:task-runtime:read"),
        ("/obs/task-runtime/review-locator", "obs:task-runtime:read"),
        ("/obs/task-runtime/executions/graph-1", "obs:task-runtime:read"),
        ("/obs/sessions", "obs:session:read"),
        ("/obs/sessions/export", "obs:session:export"),
        ("/obs/checkpoints", "obs:checkpoint:read"),
        ("/obs/checkpoints/checkpoint-1", "obs:checkpoint:read"),
        ("/obs/memory/resident-context", "obs:memory:read"),
        ("/obs/immune-gate/replay/request-1", "obs:immune:read"),
        ("/obs/codex-bio-ops/owner-inbox", "obs:bio-ops:read"),
        ("/obs/codex-bio-ops/owner-state", "obs:bio-ops:read"),
        ("/api/ui/timeline", "obs:ui:read"),
        ("/api/ui/request/request-1", "obs:ui:read"),
        ("/api/ui/search-requests", "obs:ui:read"),
        ("/api/ui/link-topology", "obs:ui:read"),
        ("/obs/agent-loop/history", "obs:agent-history:read"),
        ("/obs/ws/health", "obs:health:stream"),
    ],
)
def test_sensitive_operator_route_families_are_default_private(path, scope) -> None:
    assert required_operator_query_scope(method="GET", path=path) == scope


def test_same_origin_operator_session_cookie_authenticates_without_js_header(monkeypatch) -> None:
    gateway = _gateway(monkeypatch)

    context = gateway.open_asgi_context(
        {
            "headers": [
                (b"cookie", b"theme=dark; natsume_operator_session=operator-test-token")
            ]
        }
    )

    assert context.authority.auth_status is OperatorQueryAuthStatus.AUTHENTICATED
    assert context.authority.principal_id == "ops.test"
    assert context.authority.auth_ref.startswith("operator-auth://sha256/")


@pytest.mark.parametrize(
    ("path", "scope"),
    [
        (
            "/obs/capability-gaps/forge-proposals/preview",
            "obs:capability-forge:read",
        ),
        ("/obs/life-evolution/action-preview", "obs:life-evolution:read"),
        ("/obs/future-query-preview", "obs:operator:read"),
    ],
)
def test_query_only_post_routes_are_never_anonymous(path, scope) -> None:
    assert required_operator_query_scope(method="POST", path=path) == scope
