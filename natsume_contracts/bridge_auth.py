"""Runtime-independent bridge authentication contract.

Host, Edge and bridge processes share this module so field order, digest
format and HTTP header spelling cannot drift between producers and consumers.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any, Mapping


BRIDGE_AUTH_VERSION = "natsume.bridge-auth.v1"
HTTP_AUTH_HEADERS = {
    "version": "X-Natsume-Bridge-Version",
    "key_id": "X-Natsume-Bridge-Key-Id",
    "timestamp_ms": "X-Natsume-Bridge-Timestamp",
    "nonce": "X-Natsume-Bridge-Nonce",
    "principal": "X-Natsume-Bridge-Principal",
    "scope": "X-Natsume-Bridge-Scope",
    "signature": "X-Natsume-Bridge-Signature",
}


def body_sha256(raw_body: bytes) -> str:
    if not isinstance(raw_body, bytes):
        raise TypeError("raw_body must be bytes")
    return hashlib.sha256(raw_body).hexdigest()


def _canonical_text(value: Any, *, lower: bool = False, upper: bool = False) -> str:
    text = str(value).strip()
    if "\n" in text or "\r" in text:
        raise ValueError("bridge authentication fields cannot contain newlines")
    if lower:
        return text.lower()
    if upper:
        return text.upper()
    return text


def canonical_hmac_payload(
    *,
    version: str,
    key_id: str,
    transport: str,
    method: str,
    path: str,
    timestamp_ms: int | str,
    nonce: str,
    principal: str,
    scope: str,
    body_digest: str,
) -> bytes:
    """Return the frozen ten-field bridge signature payload."""

    return "\n".join(
        (
            _canonical_text(version),
            _canonical_text(key_id),
            _canonical_text(transport, lower=True),
            _canonical_text(method, upper=True),
            _canonical_text(path),
            _canonical_text(timestamp_ms),
            _canonical_text(nonce),
            _canonical_text(principal),
            _canonical_text(scope),
            _canonical_text(body_digest, lower=True),
        )
    ).encode("utf-8")


def build_hmac_digest(secret: bytes, **canonical_fields: Any) -> str:
    key = bytes(secret)
    if not key:
        raise ValueError("bridge HMAC secret is required")
    return hmac.new(
        key,
        canonical_hmac_payload(**canonical_fields),
        hashlib.sha256,
    ).hexdigest()


def build_hmac_signature(secret: bytes, **canonical_fields: Any) -> str:
    return "sha256=" + build_hmac_digest(secret, **canonical_fields)


def build_http_auth_headers(
    *,
    secret: bytes,
    key_id: str,
    method: str,
    path: str,
    timestamp_ms: int,
    nonce: str,
    principal: str,
    scope: str,
    raw_body: bytes,
    header_names: Mapping[str, str] = HTTP_AUTH_HEADERS,
) -> dict[str, str]:
    signature = build_hmac_signature(
        secret,
        version=BRIDGE_AUTH_VERSION,
        key_id=key_id,
        transport="http",
        method=method,
        path=path,
        timestamp_ms=timestamp_ms,
        nonce=nonce,
        principal=principal,
        scope=scope,
        body_digest=body_sha256(raw_body),
    )
    return {
        str(header_names["version"]): BRIDGE_AUTH_VERSION,
        str(header_names["key_id"]): str(key_id).strip(),
        str(header_names["timestamp_ms"]): str(int(timestamp_ms)),
        str(header_names["nonce"]): str(nonce).strip(),
        str(header_names["principal"]): str(principal).strip(),
        str(header_names["scope"]): str(scope).strip(),
        str(header_names["signature"]): signature,
    }


__all__ = [
    "BRIDGE_AUTH_VERSION",
    "HTTP_AUTH_HEADERS",
    "body_sha256",
    "build_hmac_digest",
    "build_hmac_signature",
    "build_http_auth_headers",
    "canonical_hmac_payload",
]
