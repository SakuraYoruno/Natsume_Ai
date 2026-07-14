"""Authenticated, privacy-preserving boundary for operator queries.

The observability process intentionally exposes useful public diagnostics, but
it must never turn the audit ledger into a second copy of private conversation
bodies.  This module binds a server-owned principal/scope decision to every
query and serializes JSON responses through a fail-closed field policy.

Bearer credentials are configured by digest, never by raw token::

    NATSUME_OPERATOR_QUERY_AUTH_BINDINGS='[
      {"token_sha256":"<64 hex>","principal_id":"ops.alice",
       "scopes":["obs:trace:read"]}
    ]'

Anonymous callers receive the public scope.  Authentication never relaxes the
default private-body policy: a future endpoint that genuinely needs private
material must use a different, explicitly reviewed serializer.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
import hashlib
import hmac
from http.cookies import SimpleCookie
import json
import os
import re
from typing import Any, Callable, Protocol

from fastapi import HTTPException, Request, status

from host_brain.core.trace_redaction import contains_sensitive_trace_content


OPERATOR_QUERY_SCHEMA_VERSION = "natsume.operator-query.v1"
OPERATOR_QUERY_AUTH_BINDINGS_ENV = "NATSUME_OPERATOR_QUERY_AUTH_BINDINGS"
OPERATOR_QUERY_PUBLIC_SCOPE = "obs:public:read"
OPERATOR_QUERY_SESSION_COOKIE = "natsume_operator_session"
OPERATOR_QUERY_MAX_JSON_BYTES = 8 * 1024 * 1024

# These fields are forbidden by the default serializer at every nesting depth,
# even for an authenticated principal with broad scopes.
DEFAULT_FORBIDDEN_PRIVATE_FIELDS = frozenset(
    {
        "chunk",
        "acceptance_criteria",
        "input_text",
        "objective",
        "output_text",
        "raw_text",
        "normalized_intent",
        "private_reasoning",
        "task_description",
    }
)
_SECRET_FIELD_NAMES = frozenset(
    {
        "authorization",
        "cookie",
        "cookies",
        "credential",
        "credential_proof",
        "api_key",
        "password",
        "passwd",
        "secret",
        "secret_value",
        "token",
        "access_token",
        "refresh_token",
        "session_token",
        "private_key",
        "private_body",
        "private_memory",
        "private_memory_body",
        "memory_private_body",
    }
)

# Content-shaped keys are denied unless they are explicit summaries, metadata,
# or references.  This allowlist prevents a newly added ``raw_prompt_text`` or
# ``message_body`` field from silently bypassing the five historical names.
PUBLIC_CONTENT_FIELD_ALLOWLIST = frozenset(
    {
        "body_sha256",
        "content_length",
        "content_ref",
        "content_refs",
        "content_type",
        "decision_summary",
        "error",
        "error_code",
        "evidence_ref",
        "evidence_refs",
        "failure_summary",
        "input_ref",
        "input_refs",
        "output_ref",
        "output_refs",
        "payload_hash",
        "public_summary",
        "reason",
        "reason_code",
        "request_ref",
        "source_ref",
        "source_refs",
        "summary",
    }
)
_CONTENT_KEY_PARTS = frozenset(
    {
        "body",
        "content",
        "description",
        "detail",
        "input",
        "intent",
        "message",
        "narrative",
        "output",
        "prompt",
        "preview",
        "reasoning",
        "reply",
        "response",
        "snippet",
        "text",
    }
)
_SECRET_KEY_PARTS = frozenset(
    {
        "authorization",
        "cookie",
        "credential",
        "nonce",
        "password",
        "proof",
        "secret",
        "signature",
        "token",
    }
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)(?:bearer\s+[A-Za-z0-9._~+/-]{8,}|"
    r"(?:approval|confirm|undo|review_resume|canon|credential|token)::[^\s,;]+)"
)
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,254}$")
_SCOPE_RE = re.compile(r"^[a-z0-9][a-z0-9:._/-]{0,127}$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


@dataclass(frozen=True, slots=True)
class OperatorQueryRoutePolicy:
    method: str
    path_pattern: re.Pattern[str]
    required_scope: str

    def matches(self, *, method: str, path: str) -> bool:
        return self.method == method.upper() and self.path_pattern.fullmatch(path) is not None


# Authorization policy is server-owned and evaluated before the route handler.
# Do not infer sensitivity from caller headers, query parameters, or response
# contents: doing so would make authorization dependent on untrusted input.
OPERATOR_QUERY_ROUTE_POLICIES = (
    OperatorQueryRoutePolicy(
        method="GET",
        path_pattern=re.compile(r"/obs/ws/health"),
        required_scope="obs:health:stream",
    ),
    OperatorQueryRoutePolicy(
        method="GET",
        path_pattern=re.compile(r"/obs/task-runtime/(?:executions(?:/[^/]+)?|review-locator)"),
        required_scope="obs:task-runtime:read",
    ),
    OperatorQueryRoutePolicy(
        method="GET",
        path_pattern=re.compile(r"/obs/sessions"),
        required_scope="obs:session:read",
    ),
    OperatorQueryRoutePolicy(
        method="POST",
        path_pattern=re.compile(r"/observability/trace"),
        required_scope="obs:trace:read",
    ),
    OperatorQueryRoutePolicy(
        method="GET",
        path_pattern=re.compile(r"/obs/trace/[^/]+"),
        required_scope="obs:trace:read",
    ),
    OperatorQueryRoutePolicy(
        method="GET",
        path_pattern=re.compile(r"/obs/trace/[^/]+/stream_events"),
        required_scope="obs:trace:read",
    ),
    OperatorQueryRoutePolicy(
        method="GET",
        path_pattern=re.compile(r"/obs/trace-feed(?:/.*)?"),
        required_scope="obs:trace:read",
    ),
    OperatorQueryRoutePolicy(
        method="GET",
        path_pattern=re.compile(r"/obs/tribe/events"),
        required_scope="obs:trace:read",
    ),
    OperatorQueryRoutePolicy(
        method="GET",
        path_pattern=re.compile(r"/obs/sessions/export"),
        required_scope="obs:session:export",
    ),
    OperatorQueryRoutePolicy(
        method="GET",
        path_pattern=re.compile(r"/obs/checkpoints(?:/[^/]+)?"),
        required_scope="obs:checkpoint:read",
    ),
    OperatorQueryRoutePolicy(
        method="GET",
        path_pattern=re.compile(r"/obs/memory/resident-context"),
        required_scope="obs:memory:read",
    ),
    OperatorQueryRoutePolicy(
        method="GET",
        path_pattern=re.compile(r"/v1/memory/context/debug"),
        required_scope="obs:memory:read",
    ),
    OperatorQueryRoutePolicy(
        method="GET",
        path_pattern=re.compile(r"/obs/immune-gate/replay/[^/]+"),
        required_scope="obs:immune:read",
    ),
    OperatorQueryRoutePolicy(
        method="GET",
        path_pattern=re.compile(
            r"/obs/codex-bio-ops/(?:owner-inbox(?:/.*)?|owner-queues|owner-state(?:/.*)?)"
        ),
        required_scope="obs:bio-ops:read",
    ),
    OperatorQueryRoutePolicy(
        method="GET",
        path_pattern=re.compile(r"/api/ui(?:/.*)?"),
        required_scope="obs:ui:read",
    ),
    OperatorQueryRoutePolicy(
        method="POST",
        path_pattern=re.compile(r"/api/ui(?:/.*)?"),
        required_scope="obs:ui:read",
    ),
    OperatorQueryRoutePolicy(
        method="POST",
        path_pattern=re.compile(r"/obs/capability-gaps/forge-proposals/preview"),
        required_scope="obs:capability-forge:read",
    ),
    OperatorQueryRoutePolicy(
        method="POST",
        path_pattern=re.compile(r"/obs/life-evolution/action-preview"),
        required_scope="obs:life-evolution:read",
    ),
    OperatorQueryRoutePolicy(
        method="GET",
        path_pattern=re.compile(r"/obs/agent-loop/history"),
        required_scope="obs:agent-history:read",
    ),
)

# Only deliberately bounded health/metrics/summary projections are anonymous.
# Every other OBS/UI query is operator-owned even if a future route author
# forgets to add a more specific scope above.
_PUBLIC_QUERY_ROUTES = (
    re.compile(r"/health"),
    re.compile(r"/obs/services/health"),
    re.compile(r"/obs/metrics(?:/.*)?"),
    re.compile(r"/obs/(?:summary|public-summary)"),
    re.compile(r"/obs/(?:insights/summary-snapshot|digital-life/overview-snapshot)"),
)
_OPERATOR_QUERY_NAMESPACES = (
    re.compile(r"/obs(?:/.*)?"),
    re.compile(r"/api/ui(?:/.*)?"),
    re.compile(r"/observability(?:/.*)?"),
)


class OperatorQueryAuthStatus(str, Enum):
    ANONYMOUS = "ANONYMOUS"
    AUTHENTICATED = "AUTHENTICATED"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class OperatorQueryAuthority:
    principal_id: str
    scopes: frozenset[str]
    auth_status: OperatorQueryAuthStatus
    auth_ref: str | None = None
    tenant_id: str | None = None
    key_id: str | None = None
    principal_kind: str | None = None

    @property
    def authenticated(self) -> bool:
        return self.auth_status is OperatorQueryAuthStatus.AUTHENTICATED

    def allows(self, required_scope: str) -> bool:
        scope = _validated_scope(required_scope)
        return scope in self.scopes


class OperatorQueryAuthenticator(Protocol):
    def authenticate(self, authorization: str | None) -> OperatorQueryAuthority:
        """Return a server-bound authority and never raise for bad credentials."""


@dataclass(frozen=True, slots=True)
class _DigestBinding:
    token_sha256: str
    principal_id: str
    scopes: frozenset[str]
    tenant_id: str
    key_id: str | None
    principal_kind: str


class DigestOperatorQueryAuthenticator:
    """Authenticate Bearer tokens against server-configured SHA-256 bindings."""

    def __init__(self, bindings: Sequence[_DigestBinding] = ()) -> None:
        normalized: dict[str, _DigestBinding] = {}
        for binding in bindings:
            if not isinstance(binding, _DigestBinding):
                raise TypeError("operator query binding must be _DigestBinding")
            if binding.token_sha256 in normalized:
                raise ValueError("duplicate operator query token digest")
            normalized[binding.token_sha256] = binding
        self._bindings = tuple(normalized.values())

    @classmethod
    def from_environment(cls) -> "DigestOperatorQueryAuthenticator":
        raw = str(os.environ.get(OPERATOR_QUERY_AUTH_BINDINGS_ENV) or "").strip()
        if not raw:
            return cls()
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("OPERATOR_QUERY_AUTH_BINDINGS_INVALID") from exc
        if not isinstance(decoded, list):
            raise RuntimeError("OPERATOR_QUERY_AUTH_BINDINGS_INVALID")
        bindings: list[_DigestBinding] = []
        for item in decoded:
            if not isinstance(item, Mapping):
                raise RuntimeError("OPERATOR_QUERY_AUTH_BINDINGS_INVALID")
            digest = str(item.get("token_sha256") or "").strip().lower()
            principal_id = str(item.get("principal_id") or "").strip()
            tenant_id = str(item.get("tenant_id") or "local").strip()
            key_id = str(item.get("key_id") or "").strip() or None
            principal_kind = str(item.get("principal_kind") or "service").strip().lower()
            raw_scopes = item.get("scopes")
            if not _SHA256_RE.fullmatch(digest):
                raise RuntimeError("OPERATOR_QUERY_AUTH_BINDINGS_INVALID")
            if not _IDENTIFIER_RE.fullmatch(principal_id):
                raise RuntimeError("OPERATOR_QUERY_AUTH_BINDINGS_INVALID")
            if not _IDENTIFIER_RE.fullmatch(tenant_id):
                raise RuntimeError("OPERATOR_QUERY_AUTH_BINDINGS_INVALID")
            if key_id is not None and not _IDENTIFIER_RE.fullmatch(key_id):
                raise RuntimeError("OPERATOR_QUERY_AUTH_BINDINGS_INVALID")
            if principal_kind not in {"user", "service"}:
                raise RuntimeError("OPERATOR_QUERY_AUTH_BINDINGS_INVALID")
            if not isinstance(raw_scopes, list) or not raw_scopes:
                raise RuntimeError("OPERATOR_QUERY_AUTH_BINDINGS_INVALID")
            try:
                scopes = frozenset(_validated_scope(value) for value in raw_scopes)
            except ValueError as exc:
                raise RuntimeError("OPERATOR_QUERY_AUTH_BINDINGS_INVALID") from exc
            bindings.append(
                _DigestBinding(
                    token_sha256=digest,
                    principal_id=principal_id,
                    scopes=scopes | {OPERATOR_QUERY_PUBLIC_SCOPE},
                    tenant_id=tenant_id,
                    key_id=key_id,
                    principal_kind=principal_kind,
                )
            )
        return cls(bindings)

    def authenticate(self, authorization: str | None) -> OperatorQueryAuthority:
        value = str(authorization or "").strip()
        if not value:
            return _anonymous_authority()
        scheme, separator, token = value.partition(" ")
        token = token.strip()
        if separator != " " or scheme.lower() != "bearer" or not token or len(token) > 4096:
            return _invalid_authority()
        presented_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        matched: _DigestBinding | None = None
        # Iterate over all bindings so matching work does not reveal position.
        for binding in self._bindings:
            if hmac.compare_digest(presented_digest, binding.token_sha256):
                matched = binding
        if matched is None:
            return _invalid_authority()
        return OperatorQueryAuthority(
            principal_id=matched.principal_id,
            scopes=matched.scopes,
            auth_status=OperatorQueryAuthStatus.AUTHENTICATED,
            auth_ref=f"operator-auth://sha256/{presented_digest}",
            tenant_id=matched.tenant_id,
            key_id=matched.key_id,
            principal_kind=matched.principal_kind,
        )


@dataclass(frozen=True, slots=True)
class OperatorQueryContext:
    authority: OperatorQueryAuthority
    schema_version: str = OPERATOR_QUERY_SCHEMA_VERSION

    def require_scope(self, required_scope: str) -> None:
        if self.authority.allows(required_scope):
            return
        if self.authority.auth_status is OperatorQueryAuthStatus.ANONYMOUS:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="OPERATOR_QUERY_AUTH_REQUIRED",
                headers={"WWW-Authenticate": "Bearer"},
            )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="OPERATOR_QUERY_SCOPE_DENIED",
        )


@dataclass(frozen=True, slots=True)
class OperatorQuerySerialization:
    payload: Any
    redacted_fields: int
    citation_refs: tuple[str, ...]


class OperatorQueryGateway:
    """Bind authentication and the default public query serializer."""

    def __init__(self, authenticator: OperatorQueryAuthenticator) -> None:
        self._authenticator = authenticator

    @classmethod
    def from_environment(cls) -> "OperatorQueryGateway":
        return cls(DigestOperatorQueryAuthenticator.from_environment())

    def open_context(
        self,
        *,
        authorization_values: Sequence[str],
        cookie_values: Sequence[str] = (),
    ) -> OperatorQueryContext:
        session_tokens = _operator_session_tokens(cookie_values)
        # A same-origin HttpOnly session is the production browser authority.
        # If present it takes precedence over bearer material so JavaScript
        # never needs to retain or forward a proof/secret.
        if session_tokens:
            authority = (
                self._authenticator.authenticate(f"Bearer {session_tokens[0]}")
                if len(session_tokens) == 1
                else _invalid_authority()
            )
            return OperatorQueryContext(authority=authority)
        # Duplicate Authorization headers are ambiguous and fail authentication.
        if len(authorization_values) > 1:
            authority = _invalid_authority()
        else:
            authority = self._authenticator.authenticate(
                authorization_values[0] if authorization_values else None
            )
        return OperatorQueryContext(authority=authority)

    def open_asgi_context(self, scope: Mapping[str, Any]) -> OperatorQueryContext:
        return self.open_context(
            authorization_values=_header_values(scope, b"authorization"),
            cookie_values=_header_values(scope, b"cookie"),
        )

    def serialize_public(self, payload: Any) -> OperatorQuerySerialization:
        citation_refs: list[str] = []
        redaction_count = [0]
        sanitized = _sanitize_value(
            payload,
            field_name=None,
            citation_refs=citation_refs,
            redaction_count=redaction_count,
        )
        return OperatorQuerySerialization(
            payload=sanitized,
            redacted_fields=redaction_count[0],
            citation_refs=tuple(dict.fromkeys(citation_refs)),
        )

    def authorize_route(self, context: OperatorQueryContext, *, method: str, path: str) -> None:
        required_scope = required_operator_query_scope(method=method, path=path)
        if required_scope is not None:
            context.require_scope(required_scope)


class OperatorQueryMiddleware:
    """Pure ASGI middleware that sanitizes every JSON query response."""

    def __init__(
        self,
        app: Any,
        *,
        gateway_provider: Callable[[], OperatorQueryGateway],
        max_json_bytes: int = OPERATOR_QUERY_MAX_JSON_BYTES,
    ) -> None:
        self._app = app
        self._gateway_provider = gateway_provider
        self._max_json_bytes = max(1024, int(max_json_bytes))

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        gateway = self._gateway_provider()
        context = gateway.open_asgi_context(scope)
        scope.setdefault("state", {})["operator_query_context"] = context
        method = str(scope.get("method") or "GET")
        path = str(scope.get("path") or "/")
        route_is_protected = required_operator_query_scope(method=method, path=path) is not None
        private_no_store = route_is_protected or context.authority.authenticated
        try:
            gateway.authorize_route(
                context,
                method=method,
                path=path,
            )
        except HTTPException as exc:
            await _send_authorization_denial(
                send,
                context=context,
                exc=exc,
                private_no_store=private_no_store,
            )
            return

        response_start: dict[str, Any] | None = None
        body_chunks: list[bytes] = []
        body_size = 0
        buffering_json = False

        async def guarded_send(message: dict[str, Any]) -> None:
            nonlocal response_start, body_size, buffering_json
            message_type = message.get("type")
            if message_type == "http.response.start":
                response_start = dict(message)
                headers = list(response_start.get("headers") or ())
                content_type = _response_header(headers, b"content-type").lower()
                buffering_json = b"application/json" in content_type or b"+json" in content_type
                if not buffering_json:
                    response_start["headers"] = _with_query_headers(
                        headers,
                        context,
                        None,
                        private_no_store=private_no_store,
                    )
                    await send(response_start)
                return
            if message_type != "http.response.body" or not buffering_json:
                await send(message)
                return

            chunk = bytes(message.get("body") or b"")
            body_chunks.append(chunk)
            body_size += len(chunk)
            if message.get("more_body", False):
                return
            assert response_start is not None
            if body_size > self._max_json_bytes:
                sanitized_body = json.dumps(
                    {
                        "status": "ERROR",
                        "detail": "OPERATOR_QUERY_RESPONSE_TOO_LARGE",
                        "schema_version": OPERATOR_QUERY_SCHEMA_VERSION,
                    },
                    separators=(",", ":"),
                ).encode("utf-8")
                redacted_fields = 1
                response_start["status"] = 500
            else:
                try:
                    decoded = json.loads(b"".join(body_chunks).decode("utf-8"))
                    serialization = gateway.serialize_public(decoded)
                    sanitized_body = json.dumps(
                        serialization.payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                    redacted_fields = serialization.redacted_fields
                except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
                    sanitized_body = json.dumps(
                        {
                            "status": "ERROR",
                            "detail": "OPERATOR_QUERY_JSON_INVALID",
                            "schema_version": OPERATOR_QUERY_SCHEMA_VERSION,
                        },
                        separators=(",", ":"),
                    ).encode("utf-8")
                    redacted_fields = 1
                    response_start["status"] = 500
            headers = list(response_start.get("headers") or ())
            headers = [
                (name, value)
                for name, value in headers
                if name.lower() not in {b"content-length", b"content-encoding"}
            ]
            headers.append((b"content-length", str(len(sanitized_body)).encode("ascii")))
            response_start["headers"] = _with_query_headers(
                headers,
                context,
                redacted_fields,
                private_no_store=private_no_store,
            )
            await send(response_start)
            await send({"type": "http.response.body", "body": sanitized_body})

        await self._app(scope, receive, guarded_send)


def get_operator_query_context(request: Request) -> OperatorQueryContext:
    context = getattr(request.state, "operator_query_context", None)
    if not isinstance(context, OperatorQueryContext):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OPERATOR_QUERY_GATEWAY_UNAVAILABLE",
        )
    return context


def require_operator_query_scope(request: Request, required_scope: str) -> OperatorQueryContext:
    context = get_operator_query_context(request)
    context.require_scope(required_scope)
    return context


def required_operator_query_scope(*, method: str, path: str) -> str | None:
    normalized_method = str(method or "GET").strip().upper()
    normalized_path = str(path or "/").strip() or "/"
    for policy in OPERATOR_QUERY_ROUTE_POLICIES:
        if policy.matches(method=normalized_method, path=normalized_path):
            return policy.required_scope
    if normalized_method == "GET" and any(
        pattern.fullmatch(normalized_path) for pattern in _PUBLIC_QUERY_ROUTES
    ):
        return None
    if any(pattern.fullmatch(normalized_path) for pattern in _OPERATOR_QUERY_NAMESPACES):
        return "obs:operator:read"
    return None


def _operator_session_tokens(cookie_values: Sequence[str]) -> tuple[str, ...]:
    tokens: list[str] = []
    for raw_cookie in cookie_values:
        try:
            parsed = SimpleCookie()
            parsed.load(str(raw_cookie or ""))
            morsel = parsed.get(OPERATOR_QUERY_SESSION_COOKIE)
            if morsel is not None:
                value = str(morsel.value or "").strip()
                if value:
                    tokens.append(value)
        except Exception:
            tokens.append("")
    return tuple(tokens)


def _sanitize_value(
    value: Any,
    *,
    field_name: str | None,
    citation_refs: list[str],
    redaction_count: list[int],
) -> Any:
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        withheld: list[dict[str, str]] = []
        for raw_key, item in value.items():
            key = str(raw_key)
            normalized_key = _normalize_key(key)
            if _field_is_private(normalized_key, item):
                redaction_count[0] += 1
                reference = _private_ref(normalized_key, item)
                citation_refs.append(reference)
                withheld.append(
                    {
                        "field": normalized_key,
                        "summary": _value_summary(item),
                        "citation_ref": reference,
                    }
                )
                continue
            output[key] = _sanitize_value(
                item,
                field_name=normalized_key,
                citation_refs=citation_refs,
                redaction_count=redaction_count,
            )
        if withheld:
            existing = output.get("_operator_privacy")
            privacy = dict(existing) if isinstance(existing, Mapping) else {}
            privacy.update(
                {
                    "schema_version": OPERATOR_QUERY_SCHEMA_VERSION,
                    "redacted": True,
                    "withheld": withheld,
                }
            )
            output["_operator_privacy"] = privacy
        return output
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [
            _sanitize_value(
                item,
                field_name=field_name,
                citation_refs=citation_refs,
                redaction_count=redaction_count,
            )
            for item in value
        ]
    if isinstance(value, (bytes, bytearray)):
        redaction_count[0] += 1
        reference = _private_ref(field_name or "binary", value)
        citation_refs.append(reference)
        return reference
    if isinstance(value, str) and (
        contains_sensitive_trace_content(value) or _SECRET_VALUE_RE.search(value)
    ):
        redaction_count[0] += 1
        reference = _private_ref(field_name or "value", value)
        citation_refs.append(reference)
        return reference
    return value


def _field_is_private(normalized_key: str, value: Any) -> bool:
    if normalized_key in DEFAULT_FORBIDDEN_PRIVATE_FIELDS:
        return True
    if normalized_key in _SECRET_FIELD_NAMES:
        return True
    if normalized_key.startswith("private_") or normalized_key.startswith("raw_"):
        return True
    parts = frozenset(part for part in normalized_key.split("_") if part)
    if parts & _SECRET_KEY_PARTS or {"api", "key"}.issubset(parts):
        return True
    if normalized_key == "preview" or normalized_key.endswith("_preview"):
        return True
    if normalized_key in PUBLIC_CONTENT_FIELD_ALLOWLIST:
        return False
    # Structured input/output containers are traversed; scalar bodies are not.
    if isinstance(value, (Mapping, list, tuple)):
        return False
    # Counts and flags under names such as ``sources.intent`` are operational
    # metadata, not content bodies.  Exact private/secret fields above remain
    # forbidden regardless of value type.
    if not isinstance(value, (str, bytes, bytearray)):
        return False
    return bool(parts & _CONTENT_KEY_PARTS)


def _private_ref(field_name: str, value: Any) -> str:
    digest = _stable_private_digest(value)
    kind = re.sub(r"[^a-z0-9-]+", "-", field_name.lower().replace("_", "-"))
    return f"operator-private-{kind or 'field'}://sha256/{digest}"


def _stable_private_digest(value: Any) -> str:
    def _fallback(item: Any) -> str:
        return f"<{type(item).__module__}.{type(item).__qualname__}>"

    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=_fallback,
        ).encode("utf-8", errors="replace")
    except Exception:
        encoded = f"<{type(value).__module__}.{type(value).__qualname__}>".encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _value_summary(value: Any) -> str:
    if value is None:
        return "withheld null field"
    if isinstance(value, str):
        return f"withheld text field ({len(value)} chars)"
    if isinstance(value, (bytes, bytearray)):
        return f"withheld binary field ({len(value)} bytes)"
    if isinstance(value, Mapping):
        return f"withheld object field ({len(value)} entries)"
    if isinstance(value, Sequence):
        return f"withheld sequence field ({len(value)} entries)"
    return f"withheld {type(value).__name__} field"


def _header_values(scope: Mapping[str, Any], header_name: bytes) -> tuple[str, ...]:
    values: list[str] = []
    for raw_name, raw_value in scope.get("headers") or ():
        if bytes(raw_name).lower() != header_name:
            continue
        try:
            values.append(bytes(raw_value).decode("latin-1"))
        except Exception:
            values.append("")
    return tuple(values)


def _response_header(headers: Sequence[tuple[bytes, bytes]], name: bytes) -> bytes:
    for raw_name, value in headers:
        if raw_name.lower() == name:
            return value
    return b""


def _with_query_headers(
    headers: Sequence[tuple[bytes, bytes]],
    context: OperatorQueryContext,
    redacted_fields: int | None,
    *,
    private_no_store: bool = False,
) -> list[tuple[bytes, bytes]]:
    excluded = {
        b"x-natsume-query-auth",
        b"x-natsume-query-privacy",
        b"x-natsume-query-redactions",
    }
    output = [(name, value) for name, value in headers if name.lower() not in excluded]
    output.extend(
        [
            (b"x-natsume-query-auth", context.authority.auth_status.value.encode("ascii")),
            (b"x-natsume-query-privacy", OPERATOR_QUERY_SCHEMA_VERSION.encode("ascii")),
        ]
    )
    if redacted_fields is not None:
        output.append((b"x-natsume-query-redactions", str(redacted_fields).encode("ascii")))
    if private_no_store:
        output = _with_private_no_store_headers(output)
    return output


def _with_private_no_store_headers(
    headers: Sequence[tuple[bytes, bytes]],
) -> list[tuple[bytes, bytes]]:
    """Prevent shared or browser caches from retaining operator-owned responses."""

    output: list[tuple[bytes, bytes]] = []
    vary_values: list[str] = []
    vary_names: set[str] = set()
    for name, value in headers:
        normalized_name = name.lower()
        if normalized_name == b"cache-control":
            continue
        if normalized_name == b"vary":
            for raw_token in value.decode("latin-1").split(","):
                token = raw_token.strip()
                normalized_token = token.lower()
                if token and normalized_token not in vary_names:
                    vary_values.append(token)
                    vary_names.add(normalized_token)
            continue
        output.append((name, value))

    for token in ("Cookie", "Authorization"):
        if token.lower() not in vary_names:
            vary_values.append(token)
            vary_names.add(token.lower())

    output.append((b"cache-control", b"private, no-store"))
    output.append((b"vary", ", ".join(vary_values).encode("latin-1")))
    return output


async def _send_authorization_denial(
    send: Any,
    *,
    context: OperatorQueryContext,
    exc: HTTPException,
    private_no_store: bool,
) -> None:
    body = json.dumps(
        {
            "detail": str(exc.detail),
            "schema_version": OPERATOR_QUERY_SCHEMA_VERSION,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    headers: list[tuple[bytes, bytes]] = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
    ]
    for name, value in (exc.headers or {}).items():
        headers.append((str(name).lower().encode("latin-1"), str(value).encode("latin-1")))
    headers = _with_query_headers(
        headers,
        context,
        0,
        private_no_store=private_no_store,
    )
    await send(
        {
            "type": "http.response.start",
            "status": int(exc.status_code),
            "headers": headers,
        }
    )
    await send({"type": "http.response.body", "body": body})


def _normalize_key(value: Any) -> str:
    raw = str(value or "").strip()
    # JSON producers commonly use camelCase/PascalCase while the privacy
    # policy is expressed in canonical snake_case.  Split both ordinary and
    # acronym boundaries before lower-casing so aliases such as accessToken,
    # privateReasoning, rawText and APIKey cannot bypass the deny rules.
    raw = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", raw)
    raw = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", raw)
    return re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")


def _validated_scope(value: Any) -> str:
    scope = str(value or "").strip().lower()
    if not _SCOPE_RE.fullmatch(scope):
        raise ValueError("operator query scope is invalid")
    return scope


def _anonymous_authority() -> OperatorQueryAuthority:
    return OperatorQueryAuthority(
        principal_id="operator.anonymous",
        scopes=frozenset({OPERATOR_QUERY_PUBLIC_SCOPE}),
        auth_status=OperatorQueryAuthStatus.ANONYMOUS,
    )


def _invalid_authority() -> OperatorQueryAuthority:
    return OperatorQueryAuthority(
        principal_id="operator.invalid",
        scopes=frozenset({OPERATOR_QUERY_PUBLIC_SCOPE}),
        auth_status=OperatorQueryAuthStatus.INVALID,
    )


__all__ = [
    "DEFAULT_FORBIDDEN_PRIVATE_FIELDS",
    "DigestOperatorQueryAuthenticator",
    "OPERATOR_QUERY_AUTH_BINDINGS_ENV",
    "OPERATOR_QUERY_MAX_JSON_BYTES",
    "OPERATOR_QUERY_PUBLIC_SCOPE",
    "OPERATOR_QUERY_SESSION_COOKIE",
    "OPERATOR_QUERY_ROUTE_POLICIES",
    "OPERATOR_QUERY_SCHEMA_VERSION",
    "OperatorQueryAuthStatus",
    "OperatorQueryAuthority",
    "OperatorQueryContext",
    "OperatorQueryGateway",
    "OperatorQueryMiddleware",
    "OperatorQueryRoutePolicy",
    "OperatorQuerySerialization",
    "PUBLIC_CONTENT_FIELD_ALLOWLIST",
    "get_operator_query_context",
    "require_operator_query_scope",
    "required_operator_query_scope",
]
