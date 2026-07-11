"""Fail-closed webhook verification primitives for the message gateway.

This module deliberately operates on the exact request bytes.  Platform
adapters must only receive ``payload_bytes`` from a successfully verified
envelope; parsing or normalising a request before verification can change the
bytes covered by a platform signature.

The verification result contains hashes and safe claims for audit/replay
handling.  Raw payloads, presented signatures, and secret material are never
included in exception messages or dataclass representations.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import struct
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence


class WebhookSecurityReason(str, Enum):
    """Stable, transport-independent webhook rejection reason codes."""

    VERIFIER_UNAVAILABLE = "GW_VERIFIER_UNAVAILABLE"
    SECRET_MISSING = "GW_SECRET_MISSING"
    HEADER_MISSING = "GW_HEADER_MISSING"
    QUERY_MISSING = "GW_QUERY_MISSING"
    HEADER_AMBIGUOUS = "GW_HEADER_AMBIGUOUS"
    QUERY_AMBIGUOUS = "GW_QUERY_AMBIGUOUS"
    SIGNATURE_INVALID = "GW_SIGNATURE_INVALID"
    TIMESTAMP_INVALID = "GW_TIMESTAMP_INVALID"
    TIMESTAMP_STALE = "GW_TIMESTAMP_STALE"
    TIMESTAMP_FUTURE = "GW_TIMESTAMP_FUTURE"
    BODY_INVALID = "GW_BODY_INVALID"
    PLATFORM_MISMATCH = "GW_PLATFORM_MISMATCH"
    ENVELOPE_INVALID = "GW_ENVELOPE_INVALID"
    CONFIGURATION_INVALID = "GW_CONFIGURATION_INVALID"
    VERIFICATION_FAILED = "GW_VERIFICATION_FAILED"
    REPLAYED = "GW_REPLAYED"


GW_VERIFIER_UNAVAILABLE = WebhookSecurityReason.VERIFIER_UNAVAILABLE.value
GW_SECRET_MISSING = WebhookSecurityReason.SECRET_MISSING.value
GW_HEADER_MISSING = WebhookSecurityReason.HEADER_MISSING.value
GW_QUERY_MISSING = WebhookSecurityReason.QUERY_MISSING.value
GW_HEADER_AMBIGUOUS = WebhookSecurityReason.HEADER_AMBIGUOUS.value
GW_QUERY_AMBIGUOUS = WebhookSecurityReason.QUERY_AMBIGUOUS.value
GW_SIGNATURE_INVALID = WebhookSecurityReason.SIGNATURE_INVALID.value
GW_TIMESTAMP_INVALID = WebhookSecurityReason.TIMESTAMP_INVALID.value
GW_TIMESTAMP_STALE = WebhookSecurityReason.TIMESTAMP_STALE.value
GW_TIMESTAMP_FUTURE = WebhookSecurityReason.TIMESTAMP_FUTURE.value
GW_BODY_INVALID = WebhookSecurityReason.BODY_INVALID.value
GW_PLATFORM_MISMATCH = WebhookSecurityReason.PLATFORM_MISMATCH.value
GW_ENVELOPE_INVALID = WebhookSecurityReason.ENVELOPE_INVALID.value
GW_CONFIGURATION_INVALID = WebhookSecurityReason.CONFIGURATION_INVALID.value
GW_VERIFICATION_FAILED = WebhookSecurityReason.VERIFICATION_FAILED.value
GW_REPLAYED = WebhookSecurityReason.REPLAYED.value


LINE_SIGNATURE_HEADER = "x-line-signature"
FEISHU_TIMESTAMP_HEADER = "x-lark-request-timestamp"
FEISHU_NONCE_HEADER = "x-lark-request-nonce"
FEISHU_SIGNATURE_HEADER = "x-lark-signature"
INTERNAL_TIMESTAMP_HEADER = "x-natsume-webhook-timestamp"
INTERNAL_NONCE_HEADER = "x-natsume-webhook-nonce"
INTERNAL_SIGNATURE_HEADER = "x-natsume-webhook-signature"

_SAFE_LABEL_RE = re.compile(r"^[a-zA-Z0-9_.:/-]{1,128}$")
_INTERNAL_PLATFORMS = frozenset({"qq", "wechat", "telegram", "internal_reply"})
_MAX_WECOM_ENVELOPE_BYTES = 1024 * 1024

ItemInput = Mapping[str, Any] | Iterable[tuple[Any, Any]]
SecretValue = str | bytes | None


class WebhookVerificationError(PermissionError):
    """A fail-closed rejection whose message is safe for logs and clients."""

    def __init__(
        self,
        reason_code: str | WebhookSecurityReason,
        *,
        platform: str,
        detail: str,
    ) -> None:
        code = reason_code.value if isinstance(reason_code, WebhookSecurityReason) else str(reason_code)
        safe_platform = _safe_label(platform, fallback="unknown")
        safe_detail = str(detail).replace("\r", " ").replace("\n", " ")[:240]
        self.reason_code = code
        self.platform = safe_platform
        self.detail = safe_detail
        super().__init__(f"{code}: platform={safe_platform}; {safe_detail}")

    def as_dict(self) -> dict[str, str]:
        return {
            "reason_code": self.reason_code,
            "platform": self.platform,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class RawWebhookEnvelope:
    """Exact inbound webhook request, before JSON/XML parsing.

    ``headers`` and ``query`` accept either mappings or ordered key/value
    pairs.  Ordered pairs are preferred at an HTTP boundary because they
    preserve duplicate fields so the verifier can reject ambiguity.
    """

    platform: str
    method: str
    path: str
    raw_body: bytes = field(repr=False)
    headers: ItemInput = field(default_factory=tuple, repr=False)
    query: ItemInput = field(default_factory=tuple, repr=False)
    received_at_ms: int = 0
    request_id: str = ""
    peer: str | None = None

    def __post_init__(self) -> None:
        platform = str(self.platform or "").strip().lower()
        method = str(self.method or "").strip().upper()
        path = str(self.path or "").strip()
        if not platform or not method or not path:
            raise WebhookVerificationError(
                GW_ENVELOPE_INVALID,
                platform=platform,
                detail="platform, method, and path are required",
            )
        if not isinstance(self.raw_body, (bytes, bytearray, memoryview)):
            raise WebhookVerificationError(
                GW_ENVELOPE_INVALID,
                platform=platform,
                detail="raw_body must be bytes",
            )
        object.__setattr__(self, "platform", platform)
        object.__setattr__(self, "method", method)
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "raw_body", bytes(self.raw_body))
        object.__setattr__(self, "headers", _freeze_items(self.headers))
        object.__setattr__(self, "query", _freeze_items(self.query))
        object.__setattr__(self, "received_at_ms", int(self.received_at_ms or 0))
        object.__setattr__(self, "request_id", str(self.request_id or "").strip())
        object.__setattr__(self, "peer", str(self.peer).strip() if self.peer is not None else None)


@dataclass(frozen=True, slots=True)
class VerifiedWebhookEnvelope:
    """Authenticated webhook data safe to pass to a platform parser."""

    platform: str
    profile_id: str
    principal_id: str
    credential_ref: str | None
    request_id: str
    verified_at_ms: int
    body_sha256: str
    replay_key: str
    claims: Mapping[str, Any]
    payload_bytes: bytes = field(repr=False)

    def __post_init__(self) -> None:
        # Prevent callers from mutating audit claims after verification.
        object.__setattr__(self, "claims", MappingProxyType(dict(self.claims)))
        object.__setattr__(self, "payload_bytes", bytes(self.payload_bytes))


class WebhookVerifier(Protocol):
    """Structural protocol implemented by all registry verifier profiles."""

    platform: str
    profile_id: str

    def verify(
        self,
        envelope: RawWebhookEnvelope,
        *,
        now_ms: int | None = None,
    ) -> VerifiedWebhookEnvelope: ...


class VerifierRegistry:
    """Explicit platform-to-verifier registry with no insecure fallback."""

    def __init__(
        self,
        verifiers: Iterable[WebhookVerifier] = (),
        *,
        now_ms_fn: Callable[[], int] | None = None,
    ) -> None:
        self._verifiers: dict[str, WebhookVerifier] = {}
        self._now_ms_fn = now_ms_fn or _system_now_ms
        for verifier in verifiers:
            self.register(verifier)

    @property
    def platforms(self) -> tuple[str, ...]:
        return tuple(sorted(self._verifiers))

    def register(self, verifier: WebhookVerifier, *, replace: bool = False) -> None:
        platform = str(getattr(verifier, "platform", "") or "").strip().lower()
        if not platform:
            raise ValueError("verifier platform is required")
        if platform in self._verifiers and not replace:
            raise ValueError(f"verifier already registered for platform={_safe_label(platform)}")
        self._verifiers[platform] = verifier

    def verify(self, envelope: RawWebhookEnvelope) -> VerifiedWebhookEnvelope:
        _validate_envelope(envelope)
        verifier = self._verifiers.get(envelope.platform)
        if verifier is None:
            raise WebhookVerificationError(
                GW_VERIFIER_UNAVAILABLE,
                platform=envelope.platform,
                detail="no verifier profile is registered",
            )
        try:
            return verifier.verify(envelope, now_ms=int(self._now_ms_fn()))
        except WebhookVerificationError:
            raise
        except Exception:
            # Custom verifier failures must remain fail-closed and must not
            # leak exception values that could contain request or key data.
            raise WebhookVerificationError(
                GW_VERIFICATION_FAILED,
                platform=envelope.platform,
                detail="verifier failed safely",
            ) from None


@dataclass(frozen=True, slots=True)
class LineWebhookVerifier:
    """LINE ``X-Line-Signature`` raw-body HMAC-SHA256 verifier."""

    channel_secret: SecretValue = field(default=None, repr=False)
    principal_id: str = "line:webhook"
    credential_ref: str | None = "line-channel-secret"
    platform: str = field(default="line", init=False)
    profile_id: str = field(default="line-hmac-sha256-v1", init=False)

    def verify(
        self,
        envelope: RawWebhookEnvelope,
        *,
        now_ms: int | None = None,
    ) -> VerifiedWebhookEnvelope:
        verified_at_ms = _resolve_now_ms(now_ms)
        _preflight(envelope, expected_platform=self.platform)
        secret = _require_secret(self.channel_secret, platform=self.platform)
        presented = _required_header(envelope, LINE_SIGNATURE_HEADER)
        expected = base64.b64encode(
            hmac.new(secret, envelope.raw_body, hashlib.sha256).digest()
        ).decode("ascii")
        if not hmac.compare_digest(expected, presented):
            _reject_signature(self.platform)
        body_hash = _body_hash(envelope.raw_body)
        return _verified(
            envelope,
            profile_id=self.profile_id,
            principal_id=self.principal_id,
            credential_ref=self.credential_ref,
            verified_at_ms=verified_at_ms,
            body_hash=body_hash,
            replay_material=(body_hash,),
            claims={"signature_scheme": "hmac-sha256-base64"},
        )


@dataclass(frozen=True, slots=True)
class FeishuWebhookVerifier:
    """Feishu/Lark signature verifier with authenticated decryption by default.

    ``verify_only=True`` is an explicit diagnostic/compatibility mode for
    testing the raw signature algorithm; runtime construction must retain the
    fail-closed authenticated-decrypt default.
    """

    encrypt_key: SecretValue = field(default=None, repr=False)
    verification_token: SecretValue = field(default=None, repr=False)
    verify_only: bool = False
    principal_id: str = "feishu:webhook"
    credential_ref: str | None = "feishu-encrypt-key"
    max_age_s: int = 300
    max_future_skew_s: int = 30
    platform: str = field(default="feishu", init=False)
    profile_id: str = field(default="feishu-sha256-v1", init=False)

    def verify(
        self,
        envelope: RawWebhookEnvelope,
        *,
        now_ms: int | None = None,
    ) -> VerifiedWebhookEnvelope:
        verified_at_ms = _resolve_now_ms(now_ms)
        _preflight(envelope, expected_platform=self.platform)
        secret = _require_secret(self.encrypt_key, platform=self.platform)
        timestamp = _required_header(envelope, FEISHU_TIMESTAMP_HEADER)
        nonce = _required_header(envelope, FEISHU_NONCE_HEADER)
        presented = _required_header(envelope, FEISHU_SIGNATURE_HEADER)
        timestamp_s = _validate_timestamp(
            timestamp,
            platform=self.platform,
            now_ms=verified_at_ms,
            max_age_s=self.max_age_s,
            max_future_skew_s=self.max_future_skew_s,
        )
        digest = hashlib.sha256()
        digest.update(timestamp.encode("utf-8"))
        digest.update(nonce.encode("utf-8"))
        digest.update(secret)
        digest.update(envelope.raw_body)
        if not hmac.compare_digest(digest.hexdigest(), presented.lower()):
            _reject_signature(self.platform)
        body_hash = _body_hash(envelope.raw_body)
        payload_bytes = envelope.raw_body
        token_verified = False
        if self.verify_only:
            if self.verification_token is not None:
                raise WebhookVerificationError(
                    GW_CONFIGURATION_INVALID,
                    platform=self.platform,
                    detail="verification token cannot be skipped in verify-only mode",
                )
        else:
            encrypted = _extract_feishu_encrypt(
                envelope.raw_body,
                platform=self.platform,
            )
            payload_bytes, payload = _decrypt_feishu_payload(
                encrypted,
                encrypt_key=secret,
                platform=self.platform,
            )
            if self.verification_token is not None:
                expected_token = _require_secret(
                    self.verification_token,
                    platform=self.platform,
                )
                presented_token = _extract_feishu_verification_token(
                    payload,
                    platform=self.platform,
                )
                if not hmac.compare_digest(expected_token, presented_token):
                    _reject_signature(self.platform)
                token_verified = True
        return _verified(
            envelope,
            profile_id=self.profile_id,
            principal_id=self.principal_id,
            credential_ref=self.credential_ref,
            verified_at_ms=verified_at_ms,
            body_hash=body_hash,
            replay_material=(timestamp, nonce, body_hash),
            claims={
                "signature_scheme": "sha256-timestamp-nonce-encrypt-key-body",
                "timestamp_s": timestamp_s,
                "nonce": nonce,
                "nonce_sha256": _text_hash(nonce),
                "authenticated_decrypt": not self.verify_only,
                "verification_token_verified": token_verified,
            },
            payload_bytes=payload_bytes,
        )


@dataclass(frozen=True, slots=True)
class WeComWebhookVerifier:
    """WeCom SHA-1 verifier with AES/CorpID validation by default.

    ``verify_only=True`` is an explicit diagnostic/compatibility mode.  The
    default requires the token, EncodingAESKey and CorpID and rejects partial
    runtime profiles.
    """

    token: SecretValue = field(default=None, repr=False)
    encoding_aes_key: SecretValue = field(default=None, repr=False)
    corp_id: SecretValue = field(default=None, repr=False)
    verify_only: bool = False
    principal_id: str = "wecom:webhook"
    credential_ref: str | None = "wecom-callback-token"
    max_age_s: int = 300
    max_future_skew_s: int = 30
    platform: str = field(default="wecom", init=False)
    profile_id: str = field(default="wecom-sha1-v1", init=False)

    def verify(
        self,
        envelope: RawWebhookEnvelope,
        *,
        now_ms: int | None = None,
    ) -> VerifiedWebhookEnvelope:
        verified_at_ms = _resolve_now_ms(now_ms)
        _preflight(envelope, expected_platform=self.platform)
        token = _require_secret(self.token, platform=self.platform)
        signature = _required_query(envelope, "msg_signature")
        timestamp = _required_query(envelope, "timestamp")
        nonce = _required_query(envelope, "nonce")
        timestamp_s = _validate_timestamp(
            timestamp,
            platform=self.platform,
            now_ms=verified_at_ms,
            max_age_s=self.max_age_s,
            max_future_skew_s=self.max_future_skew_s,
        )
        is_url_verification = envelope.method == "GET"
        encrypted = (
            _required_query(envelope, "echostr")
            if is_url_verification
            else _extract_wecom_encrypt(envelope.raw_body, platform=self.platform)
        )
        pieces = sorted((token, timestamp.encode("utf-8"), nonce.encode("utf-8"), encrypted.encode("utf-8")))
        expected = hashlib.sha1(b"".join(pieces)).hexdigest()  # noqa: S324 - platform protocol
        if not hmac.compare_digest(expected, signature.lower()):
            _reject_signature(self.platform)
        body_hash = _body_hash(envelope.raw_body)
        encrypted_hash = _text_hash(encrypted)
        payload_bytes = envelope.raw_body
        receive_id_verified = False
        if not self.verify_only:
            aes_key = _decode_wecom_encoding_key(
                self.encoding_aes_key,
                platform=self.platform,
            )
            expected_receive_id = _require_secret(
                self.corp_id,
                platform=self.platform,
            )
            message_bytes, receive_id = _decrypt_wecom_payload(
                encrypted,
                aes_key=aes_key,
                platform=self.platform,
            )
            if not hmac.compare_digest(expected_receive_id, receive_id):
                _reject_signature(self.platform)
            receive_id_verified = True
            if is_url_verification:
                try:
                    message_bytes.decode("utf-8", errors="strict")
                except UnicodeDecodeError:
                    _reject_body(self.platform, "decrypted echo is not UTF-8")
                payload_bytes = message_bytes
            else:
                payload_bytes = _wecom_xml_to_canonical_json(
                    message_bytes,
                    platform=self.platform,
                )
        return _verified(
            envelope,
            profile_id=self.profile_id,
            principal_id=self.principal_id,
            credential_ref=self.credential_ref,
            verified_at_ms=verified_at_ms,
            body_hash=body_hash,
            replay_material=(timestamp, nonce, encrypted_hash),
            claims={
                "signature_scheme": "sha1-sorted-token-timestamp-nonce-encrypt",
                "timestamp_s": timestamp_s,
                "nonce": nonce,
                "nonce_sha256": _text_hash(nonce),
                "encrypted_body_sha256": encrypted_hash,
                "authenticated_decrypt": not self.verify_only,
                "receive_id_verified": receive_id_verified,
                "url_verification": is_url_verification,
            },
            payload_bytes=payload_bytes,
        )


@dataclass(frozen=True, slots=True)
class InternalHmacWebhookVerifier:
    """Natsume internal raw-body HMAC profile for QQ/WeChat/Telegram."""

    platform: str
    shared_secret: SecretValue = field(default=None, repr=False)
    principal_id: str | None = None
    credential_ref: str | None = "internal-webhook-shared-key"
    max_age_s: int = 300
    max_future_skew_s: int = 30
    profile_id: str = field(default="internal-hmac-sha256-v1", init=False)

    def __post_init__(self) -> None:
        platform = str(self.platform or "").strip().lower()
        if platform not in _INTERNAL_PLATFORMS:
            raise ValueError("unsupported internal webhook platform")
        object.__setattr__(self, "platform", platform)
        if self.principal_id is None:
            object.__setattr__(self, "principal_id", f"{platform}:internal-webhook")

    def verify(
        self,
        envelope: RawWebhookEnvelope,
        *,
        now_ms: int | None = None,
    ) -> VerifiedWebhookEnvelope:
        verified_at_ms = _resolve_now_ms(now_ms)
        _preflight(envelope, expected_platform=self.platform)
        secret = _require_secret(self.shared_secret, platform=self.platform)
        timestamp = _required_header(envelope, INTERNAL_TIMESTAMP_HEADER)
        nonce = _required_header(envelope, INTERNAL_NONCE_HEADER)
        presented = _required_header(envelope, INTERNAL_SIGNATURE_HEADER)
        timestamp_s = _validate_timestamp(
            timestamp,
            platform=self.platform,
            now_ms=verified_at_ms,
            max_age_s=self.max_age_s,
            max_future_skew_s=self.max_future_skew_s,
        )
        expected = build_internal_signature(
            shared_secret=secret,
            platform=self.platform,
            method=envelope.method,
            path=envelope.path,
            timestamp=timestamp,
            nonce=nonce,
            raw_body=envelope.raw_body,
        )
        if not hmac.compare_digest(expected, presented.lower()):
            _reject_signature(self.platform)
        body_hash = _body_hash(envelope.raw_body)
        return _verified(
            envelope,
            profile_id=self.profile_id,
            principal_id=str(self.principal_id),
            credential_ref=self.credential_ref,
            verified_at_ms=verified_at_ms,
            body_hash=body_hash,
            replay_material=(timestamp, nonce, body_hash),
            claims={
                "signature_scheme": "natsume-internal-hmac-sha256-v1",
                "timestamp_s": timestamp_s,
                "nonce": nonce,
                "nonce_sha256": _text_hash(nonce),
            },
        )


def build_internal_signature(
    *,
    shared_secret: str | bytes,
    platform: str,
    method: str,
    path: str,
    timestamp: str | int,
    nonce: str,
    raw_body: bytes,
) -> str:
    """Build the documented Natsume internal webhook signature."""

    secret = _signing_secret(shared_secret)
    canonical = (
        "natsume-webhook-v1\n"
        f"{str(platform).strip().lower()}\n"
        f"{str(method).strip().upper()}\n"
        f"{str(path).strip()}\n"
        f"{timestamp}\n"
        f"{nonce}\n"
    ).encode("utf-8") + bytes(raw_body)
    return hmac.new(secret, canonical, hashlib.sha256).hexdigest()


def _verified(
    envelope: RawWebhookEnvelope,
    *,
    profile_id: str,
    principal_id: str,
    credential_ref: str | None,
    verified_at_ms: int,
    body_hash: str,
    replay_material: Sequence[str],
    claims: Mapping[str, Any],
    payload_bytes: bytes | None = None,
) -> VerifiedWebhookEnvelope:
    replay_digest = hashlib.sha256()
    for value in (envelope.platform, profile_id, *replay_material):
        encoded = str(value).encode("utf-8")
        replay_digest.update(len(encoded).to_bytes(4, "big"))
        replay_digest.update(encoded)
    return VerifiedWebhookEnvelope(
        platform=envelope.platform,
        profile_id=profile_id,
        principal_id=str(principal_id),
        credential_ref=str(credential_ref) if credential_ref is not None else None,
        request_id=envelope.request_id,
        verified_at_ms=verified_at_ms,
        body_sha256=body_hash,
        replay_key=f"gw:{envelope.platform}:{replay_digest.hexdigest()}",
        claims=claims,
        payload_bytes=envelope.raw_body if payload_bytes is None else payload_bytes,
    )


def _preflight(envelope: RawWebhookEnvelope, *, expected_platform: str) -> None:
    _validate_envelope(envelope)
    if envelope.platform != expected_platform:
        raise WebhookVerificationError(
            GW_PLATFORM_MISMATCH,
            platform=envelope.platform,
            detail="envelope platform does not match verifier profile",
        )


def _validate_envelope(envelope: RawWebhookEnvelope) -> None:
    if not isinstance(envelope, RawWebhookEnvelope):
        raise WebhookVerificationError(
            GW_ENVELOPE_INVALID,
            platform="unknown",
            detail="RawWebhookEnvelope is required",
        )
    _assert_unambiguous(envelope.headers, platform=envelope.platform, kind="header")
    _assert_unambiguous(envelope.query, platform=envelope.platform, kind="query")


def _required_header(envelope: RawWebhookEnvelope, name: str) -> str:
    return _required_item(
        envelope.headers,
        name=name,
        platform=envelope.platform,
        kind="header",
    )


def _required_query(envelope: RawWebhookEnvelope, name: str) -> str:
    return _required_item(
        envelope.query,
        name=name,
        platform=envelope.platform,
        kind="query",
    )


def _required_item(
    items: ItemInput,
    *,
    name: str,
    platform: str,
    kind: str,
) -> str:
    target = name.casefold()
    matches = [value for key, value in items if key.strip().casefold() == target]
    if len(matches) > 1:
        reason = GW_HEADER_AMBIGUOUS if kind == "header" else GW_QUERY_AMBIGUOUS
        raise WebhookVerificationError(
            reason,
            platform=platform,
            detail=f"duplicate security {kind} rejected",
        )
    if not matches or not matches[0].strip():
        reason = GW_HEADER_MISSING if kind == "header" else GW_QUERY_MISSING
        raise WebhookVerificationError(
            reason,
            platform=platform,
            detail=f"required security {kind} is missing",
        )
    return matches[0].strip()


def _assert_unambiguous(items: ItemInput, *, platform: str, kind: str) -> None:
    seen: set[str] = set()
    for key, _value in items:
        normalized = key.strip().casefold()
        if not normalized:
            raise WebhookVerificationError(
                GW_ENVELOPE_INVALID,
                platform=platform,
                detail=f"empty {kind} name rejected",
            )
        if normalized in seen:
            reason = GW_HEADER_AMBIGUOUS if kind == "header" else GW_QUERY_AMBIGUOUS
            raise WebhookVerificationError(
                reason,
                platform=platform,
                detail=f"duplicate {kind} rejected",
            )
        seen.add(normalized)


def _validate_timestamp(
    raw_timestamp: str,
    *,
    platform: str,
    now_ms: int,
    max_age_s: int,
    max_future_skew_s: int,
) -> int:
    if max_age_s < 0 or max_future_skew_s < 0:
        raise WebhookVerificationError(
            GW_CONFIGURATION_INVALID,
            platform=platform,
            detail="timestamp window configuration is invalid",
        )
    if not raw_timestamp.isascii() or not raw_timestamp.isdigit():
        raise WebhookVerificationError(
            GW_TIMESTAMP_INVALID,
            platform=platform,
            detail="timestamp must be unsigned epoch seconds",
        )
    try:
        timestamp_s = int(raw_timestamp)
    except (TypeError, ValueError, OverflowError):
        raise WebhookVerificationError(
            GW_TIMESTAMP_INVALID,
            platform=platform,
            detail="timestamp is invalid",
        ) from None
    timestamp_ms = timestamp_s * 1000
    if timestamp_ms < now_ms - int(max_age_s) * 1000:
        raise WebhookVerificationError(
            GW_TIMESTAMP_STALE,
            platform=platform,
            detail="timestamp is outside the accepted age window",
        )
    if timestamp_ms > now_ms + int(max_future_skew_s) * 1000:
        raise WebhookVerificationError(
            GW_TIMESTAMP_FUTURE,
            platform=platform,
            detail="timestamp is beyond the accepted future skew",
        )
    return timestamp_s


def _extract_feishu_encrypt(raw_body: bytes, *, platform: str) -> str:
    if not raw_body or len(raw_body) > _MAX_WECOM_ENVELOPE_BYTES:
        _reject_body(platform, "encrypted callback body is missing or too large")
    try:
        payload = json.loads(
            raw_body.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_json_object,
        )
    except Exception:
        _reject_body(platform, "encrypted callback body could not be parsed safely")
    if not isinstance(payload, dict) or len(payload) != 1:
        _reject_body(platform, "encrypted callback must contain only one encrypt field")
    key, value = next(iter(payload.items()))
    if str(key).casefold() != "encrypt" or not isinstance(value, str) or not value.strip():
        _reject_body(platform, "encrypted callback must contain only one encrypt field")
    return value.strip()


def _decrypt_feishu_payload(
    encrypted: str,
    *,
    encrypt_key: bytes,
    platform: str,
) -> tuple[bytes, dict[str, Any]]:
    packed = _strict_base64_decode(encrypted, platform=platform)
    if len(packed) < 32 or (len(packed) - 16) % 16 != 0:
        _reject_body(platform, "encrypted callback length is invalid")
    key = hashlib.sha256(encrypt_key).digest()
    decrypted = _aes_cbc_decrypt(
        key=key,
        iv=packed[:16],
        ciphertext=packed[16:],
        platform=platform,
    )
    plaintext = _strip_pkcs7(decrypted, block_size=16, platform=platform)
    try:
        payload = json.loads(
            plaintext.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_json_object,
        )
    except Exception:
        _reject_body(platform, "decrypted callback is not strict UTF-8 JSON")
    if not isinstance(payload, dict):
        _reject_body(platform, "decrypted callback must be a JSON object")
    return plaintext, payload


def _extract_feishu_verification_token(
    payload: Mapping[str, Any],
    *,
    platform: str,
) -> bytes:
    candidates: list[Any] = []
    for key, value in payload.items():
        if str(key).casefold() == "token":
            candidates.append(value)
        if str(key).casefold() == "header" and isinstance(value, Mapping):
            candidates.extend(
                nested
                for nested_key, nested in value.items()
                if str(nested_key).casefold() == "token"
            )
    if len(candidates) != 1 or not isinstance(candidates[0], str) or not candidates[0]:
        _reject_signature(platform)
    return candidates[0].encode("utf-8")


def _decode_wecom_encoding_key(value: SecretValue, *, platform: str) -> bytes:
    encoded = _require_secret(value, platform=platform)
    try:
        text = encoded.decode("ascii", errors="strict")
        if len(text) != 43 or "=" in text:
            raise ValueError
        key = base64.b64decode(text + "=", validate=True)
    except Exception:
        raise WebhookVerificationError(
            GW_CONFIGURATION_INVALID,
            platform=platform,
            detail="EncodingAESKey configuration is invalid",
        ) from None
    if len(key) != 32:
        raise WebhookVerificationError(
            GW_CONFIGURATION_INVALID,
            platform=platform,
            detail="EncodingAESKey configuration is invalid",
        )
    return key


def _decrypt_wecom_payload(
    encrypted: str,
    *,
    aes_key: bytes,
    platform: str,
) -> tuple[bytes, bytes]:
    ciphertext = _strict_base64_decode(encrypted, platform=platform)
    if not ciphertext or len(ciphertext) % 16 != 0:
        _reject_body(platform, "encrypted callback length is invalid")
    decrypted = _aes_cbc_decrypt(
        key=aes_key,
        iv=aes_key[:16],
        ciphertext=ciphertext,
        platform=platform,
    )
    plaintext = _strip_pkcs7(decrypted, block_size=32, platform=platform)
    if len(plaintext) < 21:
        _reject_body(platform, "decrypted callback envelope is invalid")
    message_length = struct.unpack("!I", plaintext[16:20])[0]
    message_end = 20 + message_length
    if message_length < 1 or message_end >= len(plaintext):
        _reject_body(platform, "decrypted callback envelope is invalid")
    message = plaintext[20:message_end]
    receive_id = plaintext[message_end:]
    if not receive_id:
        _reject_body(platform, "decrypted callback envelope is invalid")
    return message, receive_id


def _wecom_xml_to_canonical_json(message: bytes, *, platform: str) -> bytes:
    if not message or len(message) > _MAX_WECOM_ENVELOPE_BYTES:
        _reject_body(platform, "decrypted XML message is missing or too large")
    upper = message.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        _reject_body(platform, "unsafe decrypted XML rejected")
    try:
        text = message.decode("utf-8", errors="strict")
        root = ET.fromstring(text)
    except Exception:
        _reject_body(platform, "decrypted callback is not safe UTF-8 XML")
    if _xml_local_name(root.tag).casefold() != "xml" or root.attrib:
        _reject_body(platform, "decrypted callback XML root is invalid")
    if root.text and root.text.strip():
        _reject_body(platform, "decrypted callback XML contains mixed content")
    payload: dict[str, str] = {}
    seen: set[str] = set()
    for child in list(root):
        name = _xml_local_name(child.tag)
        normalized = name.casefold()
        if not name or normalized in seen or child.attrib or list(child):
            _reject_body(platform, "decrypted callback XML fields are ambiguous")
        if child.tail and child.tail.strip():
            _reject_body(platform, "decrypted callback XML contains mixed content")
        seen.add(normalized)
        payload[name] = child.text or ""
    if not payload:
        _reject_body(platform, "decrypted callback XML has no direct fields")
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _strict_base64_decode(value: str, *, platform: str) -> bytes:
    try:
        encoded = value.encode("ascii", errors="strict")
        return base64.b64decode(encoded, validate=True)
    except Exception:
        _reject_body(platform, "encrypted callback is not strict base64")


def _aes_cbc_decrypt(
    *,
    key: bytes,
    iv: bytes,
    ciphertext: bytes,
    platform: str,
) -> bytes:
    try:
        from Crypto.Cipher import AES  # noqa: PLC0415
    except Exception:
        raise WebhookVerificationError(
            GW_CONFIGURATION_INVALID,
            platform=platform,
            detail="AES backend is unavailable",
        ) from None
    try:
        return AES.new(key, AES.MODE_CBC, iv=iv).decrypt(ciphertext)
    except Exception:
        _reject_body(platform, "encrypted callback could not be decrypted")


def _strip_pkcs7(data: bytes, *, block_size: int, platform: str) -> bytes:
    if not data:
        _reject_body(platform, "encrypted callback padding is invalid")
    padding_length = data[-1]
    if padding_length < 1 or padding_length > block_size or padding_length > len(data):
        _reject_body(platform, "encrypted callback padding is invalid")
    expected = bytes((padding_length,)) * padding_length
    if not hmac.compare_digest(data[-padding_length:], expected):
        _reject_body(platform, "encrypted callback padding is invalid")
    return data[:-padding_length]


def _reject_body(platform: str, detail: str) -> None:
    raise WebhookVerificationError(
        GW_BODY_INVALID,
        platform=platform,
        detail=detail,
    )


def _extract_wecom_encrypt(raw_body: bytes, *, platform: str) -> str:
    if not raw_body or len(raw_body) > _MAX_WECOM_ENVELOPE_BYTES:
        raise WebhookVerificationError(
            GW_BODY_INVALID,
            platform=platform,
            detail="encrypted callback body is missing or too large",
        )
    stripped = raw_body.lstrip()
    try:
        if stripped.startswith(b"{"):
            payload = json.loads(raw_body.decode("utf-8"), object_pairs_hook=_strict_json_object)
            if not isinstance(payload, dict):
                raise ValueError
            matches = [value for key, value in payload.items() if str(key).casefold() == "encrypt"]
        else:
            upper = raw_body.upper()
            if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
                raise ValueError
            root = ET.fromstring(raw_body.decode("utf-8", errors="strict"))
            if _xml_local_name(root.tag).casefold() != "xml":
                raise ValueError
            matches = [
                node.text
                for node in list(root)
                if _xml_local_name(node.tag).casefold() == "encrypt"
            ]
    except WebhookVerificationError:
        raise
    except Exception:
        raise WebhookVerificationError(
            GW_BODY_INVALID,
            platform=platform,
            detail="encrypted callback body could not be parsed safely",
        ) from None
    if len(matches) != 1 or not isinstance(matches[0], str) or not matches[0].strip():
        raise WebhookVerificationError(
            GW_BODY_INVALID,
            platform=platform,
            detail="encrypted callback must contain exactly one Encrypt field",
        )
    return matches[0].strip()


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    seen: set[str] = set()
    for key, value in pairs:
        normalized = str(key).casefold()
        if normalized in seen:
            raise ValueError("duplicate json field")
        seen.add(normalized)
        result[str(key)] = value
    return result


def _xml_local_name(tag: Any) -> str:
    text = str(tag)
    return text.rsplit("}", 1)[-1]


def _require_secret(secret: SecretValue, *, platform: str) -> bytes:
    if isinstance(secret, str):
        material = secret.encode("utf-8")
    elif isinstance(secret, bytes):
        material = secret
    else:
        material = b""
    if not material:
        raise WebhookVerificationError(
            GW_SECRET_MISSING,
            platform=platform,
            detail="required verifier secret is not configured",
        )
    return material


def _signing_secret(secret: str | bytes) -> bytes:
    if isinstance(secret, str):
        material = secret.encode("utf-8")
    else:
        material = bytes(secret)
    if not material:
        raise ValueError("shared_secret is required")
    return material


def _reject_signature(platform: str) -> None:
    raise WebhookVerificationError(
        GW_SIGNATURE_INVALID,
        platform=platform,
        detail="signature verification failed",
    )


def _body_hash(raw_body: bytes) -> str:
    return hashlib.sha256(raw_body).hexdigest()


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _resolve_now_ms(value: int | None) -> int:
    return int(value if value is not None else _system_now_ms())


def _system_now_ms() -> int:
    return int(time.time() * 1000)


def _safe_label(value: Any, *, fallback: str = "invalid") -> str:
    text = str(value or "").strip()
    return text if _SAFE_LABEL_RE.fullmatch(text) else fallback


def _freeze_items(source: ItemInput) -> tuple[tuple[str, str], ...]:
    if isinstance(source, Mapping):
        expanded: list[tuple[Any, Any]] = []
        for key, value in source.items():
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                expanded.extend((key, item) for item in value)
            else:
                expanded.append((key, value))
        pairs: Iterable[tuple[Any, Any]] = expanded
    else:
        pairs = source
    frozen: list[tuple[str, str]] = []
    try:
        for key, value in pairs:
            frozen.append((_item_text(key), _item_text(value)))
    except (TypeError, ValueError):
        raise WebhookVerificationError(
            GW_ENVELOPE_INVALID,
            platform="unknown",
            detail="headers and query must be key/value pairs",
        ) from None
    return tuple(frozen)


def _item_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("latin-1")
    return str(value if value is not None else "")


__all__ = [
    "FEISHU_NONCE_HEADER",
    "FEISHU_SIGNATURE_HEADER",
    "FEISHU_TIMESTAMP_HEADER",
    "GW_BODY_INVALID",
    "GW_CONFIGURATION_INVALID",
    "GW_ENVELOPE_INVALID",
    "GW_HEADER_AMBIGUOUS",
    "GW_HEADER_MISSING",
    "GW_PLATFORM_MISMATCH",
    "GW_QUERY_AMBIGUOUS",
    "GW_QUERY_MISSING",
    "GW_REPLAYED",
    "GW_SECRET_MISSING",
    "GW_SIGNATURE_INVALID",
    "GW_TIMESTAMP_FUTURE",
    "GW_TIMESTAMP_INVALID",
    "GW_TIMESTAMP_STALE",
    "GW_VERIFICATION_FAILED",
    "GW_VERIFIER_UNAVAILABLE",
    "INTERNAL_NONCE_HEADER",
    "INTERNAL_SIGNATURE_HEADER",
    "INTERNAL_TIMESTAMP_HEADER",
    "LINE_SIGNATURE_HEADER",
    "FeishuWebhookVerifier",
    "InternalHmacWebhookVerifier",
    "LineWebhookVerifier",
    "RawWebhookEnvelope",
    "VerifiedWebhookEnvelope",
    "VerifierRegistry",
    "WeComWebhookVerifier",
    "WebhookSecurityReason",
    "WebhookVerificationError",
    "WebhookVerifier",
    "build_internal_signature",
]
