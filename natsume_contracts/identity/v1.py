"""Natsume 入站身份 v1 契约：可审计元数据与瞬时凭据材料严格分离。

本模块只规范化、解析并匹配身份声明，不验证签名、不解码 token、不访问网络或持久层。
``IngressIdentityEnvelope`` 永远只序列化 ``CredentialProofMetadata``；原始签名或
token 仅存在于 ``CredentialProof`` 的运行时对象中，避免随审计、重放或日志扩散。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Sequence


class IdentitySchemaVersion(str, Enum):
    """Supported wire schema versions."""

    V1 = "natsume.identity.v1"


class PrincipalKind(str, Enum):
    """Kinds of principals accepted by the version-1 contract."""

    USER = "user"
    SERVICE = "service"
    DEVICE = "device"
    WORKLOAD = "workload"


class CredentialProofKind(str, Enum):
    """How a caller proves possession of a credential.

    Values identify the verification scheme only.  The proof itself is never
    placed in the serializable metadata object.
    """

    HMAC_SHA256 = "hmac-sha256"
    BEARER_OPAQUE = "bearer-opaque"
    JWT = "jwt"
    MTLS_CERTIFICATE = "mtls-certificate"
    API_KEY = "api-key"


class ScopeMatchMode(str, Enum):
    """Whether every or at least one requested scope must match."""

    ALL = "all"
    ANY = "any"


class IdentityReasonCode(str, Enum):
    """Stable fail-closed outcomes for identity contract consumers."""

    PAYLOAD_INVALID = "IDENTITY_PAYLOAD_INVALID"
    FIELD_MISSING = "IDENTITY_FIELD_MISSING"
    FIELD_INVALID = "IDENTITY_FIELD_INVALID"
    FIELD_UNEXPECTED = "IDENTITY_FIELD_UNEXPECTED"
    VERSION_UNSUPPORTED = "IDENTITY_VERSION_UNSUPPORTED"
    SECRET_MATERIAL_FORBIDDEN = "IDENTITY_SECRET_MATERIAL_FORBIDDEN"
    CREDENTIAL_PROOF_MISSING = "IDENTITY_CREDENTIAL_PROOF_MISSING"
    CREDENTIAL_PROOF_INVALID = "IDENTITY_CREDENTIAL_PROOF_INVALID"
    CREDENTIAL_PROOF_TOO_LARGE = "IDENTITY_CREDENTIAL_PROOF_TOO_LARGE"
    SCOPE_INVALID = "IDENTITY_SCOPE_INVALID"
    SCOPE_DENIED = "IDENTITY_SCOPE_DENIED"


class IdentityContractError(ValueError):
    """Fail-closed identity validation error with a stable machine code."""

    def __init__(
        self,
        reason_code: IdentityReasonCode,
        *,
        field_path: str,
        detail: str,
    ) -> None:
        self.reason_code = reason_code.value
        self.field_path = field_path
        self.detail = detail
        super().__init__(f"{self.reason_code}: {field_path}: {detail}")

    def audit_dict(self) -> dict[str, str]:
        """Return a secret-free error record suitable for audit events."""

        return {
            "reason_code": self.reason_code,
            "field_path": self.field_path,
            "detail": self.detail,
        }


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")
_ALGORITHM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
_SCOPE_SEGMENT_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_HMAC_SHA256_HEX_RE = re.compile(rb"^[0-9A-Fa-f]{64}$")
_CREDENTIAL_PROOF_MAX_BYTES = MappingProxyType(
    {
        CredentialProofKind.HMAC_SHA256: 64,
        CredentialProofKind.BEARER_OPAQUE: 4096,
        CredentialProofKind.JWT: 8192,
        CredentialProofKind.API_KEY: 1024,
        CredentialProofKind.MTLS_CERTIFICATE: 16384,
    }
)
_FORBIDDEN_SECRET_FIELD_NAMES = frozenset(
    {
        "authorization",
        "credential",
        "password",
        "raw_proof",
        "raw_secret",
        "raw_token",
        "secret",
        "shared_secret",
        "signature",
        "token",
    }
)


def _error(
    reason_code: IdentityReasonCode,
    field_path: str,
    detail: str,
) -> IdentityContractError:
    return IdentityContractError(reason_code, field_path=field_path, detail=detail)


def _require_mapping(value: object, field_path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise _error(IdentityReasonCode.FIELD_INVALID, field_path, "must be an object")
    if not all(isinstance(key, str) for key in value):
        raise _error(IdentityReasonCode.FIELD_INVALID, field_path, "keys must be strings")
    return value


def _normalise_field_name(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _check_fields(
    value: Mapping[str, object],
    *,
    field_path: str,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> None:
    present = frozenset(value)
    missing = sorted(required - present)
    if missing:
        raise _error(
            IdentityReasonCode.FIELD_MISSING,
            f"{field_path}.{missing[0]}",
            "required field is missing",
        )

    unexpected = sorted(present - required - optional)
    if unexpected:
        field = unexpected[0]
        reason = (
            IdentityReasonCode.SECRET_MATERIAL_FORBIDDEN
            if _normalise_field_name(field) in _FORBIDDEN_SECRET_FIELD_NAMES
            else IdentityReasonCode.FIELD_UNEXPECTED
        )
        detail = "secret or raw credential material is forbidden" if reason is IdentityReasonCode.SECRET_MATERIAL_FORBIDDEN else "field is not defined by schema v1"
        raise _error(reason, f"{field_path}.{field}", detail)


def _require_identifier(value: object, field_path: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise _error(
            IdentityReasonCode.FIELD_INVALID,
            field_path,
            "must be a non-empty identifier without whitespace or control characters",
        )
    return value


def _require_algorithm(value: object, field_path: str) -> str:
    if not isinstance(value, str) or not _ALGORITHM_RE.fullmatch(value):
        raise _error(
            IdentityReasonCode.FIELD_INVALID,
            field_path,
            "must be a non-empty algorithm identifier",
        )
    return value


def _require_epoch_ms(value: object, field_path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _error(
            IdentityReasonCode.FIELD_INVALID,
            field_path,
            "must be a non-negative integer epoch timestamp in milliseconds",
        )
    return value


def _validate_runtime_credential_proof(
    kind: CredentialProofKind,
    raw_proof: object,
) -> None:
    field_path = "credential.raw_proof"
    if not isinstance(raw_proof, (str, bytes)):
        raise _error(
            IdentityReasonCode.CREDENTIAL_PROOF_INVALID,
            field_path,
            "runtime credential proof must be str or bytes",
        )
    if not raw_proof:
        raise _error(
            IdentityReasonCode.CREDENTIAL_PROOF_MISSING,
            field_path,
            "runtime credential proof is required",
        )
    try:
        proof_bytes = raw_proof.encode("utf-8") if isinstance(raw_proof, str) else raw_proof
    except UnicodeEncodeError as exc:
        raise _error(
            IdentityReasonCode.CREDENTIAL_PROOF_INVALID,
            field_path,
            "runtime credential proof must be valid UTF-8",
        ) from exc

    max_bytes = _CREDENTIAL_PROOF_MAX_BYTES[kind]
    if len(proof_bytes) > max_bytes:
        raise _error(
            IdentityReasonCode.CREDENTIAL_PROOF_TOO_LARGE,
            field_path,
            f"runtime credential proof exceeds the {max_bytes}-byte limit for {kind.value}",
        )

    if kind is CredentialProofKind.HMAC_SHA256:
        if not _HMAC_SHA256_HEX_RE.fullmatch(proof_bytes):
            raise _error(
                IdentityReasonCode.CREDENTIAL_PROOF_INVALID,
                field_path,
                "hmac-sha256 proof must be exactly 64 ASCII hexadecimal characters",
            )
        return

    if any(byte < 0x20 or byte == 0x7F for byte in proof_bytes):
        raise _error(
            IdentityReasonCode.CREDENTIAL_PROOF_INVALID,
            field_path,
            f"{kind.value} proof must not contain control characters",
        )


def _parse_enum(enum_type: type[Enum], value: object, field_path: str) -> Any:
    if not isinstance(value, str):
        raise _error(IdentityReasonCode.FIELD_INVALID, field_path, "must be a string")
    try:
        return enum_type(value)
    except ValueError as exc:
        reason = (
            IdentityReasonCode.VERSION_UNSUPPORTED
            if enum_type is IdentitySchemaVersion
            else IdentityReasonCode.FIELD_INVALID
        )
        detail = "schema version is not supported" if reason is IdentityReasonCode.VERSION_UNSUPPORTED else "value is not supported"
        raise _error(reason, field_path, detail) from exc


def _validate_scope(scope: object, field_path: str) -> str:
    if not isinstance(scope, str) or not scope:
        raise _error(IdentityReasonCode.SCOPE_INVALID, field_path, "scope must be a non-empty string")
    if scope == "*":
        return scope
    parts = scope.split(":")
    for index, part in enumerate(parts):
        if part == "*" and index == len(parts) - 1 and index > 0:
            continue
        if not _SCOPE_SEGMENT_RE.fullmatch(part):
            raise _error(
                IdentityReasonCode.SCOPE_INVALID,
                field_path,
                "scope must use lower-case colon-separated segments; wildcard is final only",
            )
    return scope


def _parse_scopes(value: object, field_path: str, *, allow_empty: bool) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _error(IdentityReasonCode.SCOPE_INVALID, field_path, "scopes must be an array")
    scopes = tuple(_validate_scope(scope, f"{field_path}[{index}]") for index, scope in enumerate(value))
    if not allow_empty and not scopes:
        raise _error(IdentityReasonCode.SCOPE_INVALID, field_path, "at least one scope is required")
    return tuple(sorted(set(scopes)))


@dataclass(frozen=True, slots=True)
class Principal:
    """已认证主体及其获授 scope 声明；构造本身不证明该主体真的完成认证。"""

    principal_id: str
    kind: PrincipalKind
    tenant_id: str
    granted_scopes: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_identifier(self.principal_id, "principal.principal_id")
        if not isinstance(self.kind, PrincipalKind):
            raise _error(IdentityReasonCode.FIELD_INVALID, "principal.kind", "must be a PrincipalKind")
        _require_identifier(self.tenant_id, "principal.tenant_id")
        scopes = _parse_scopes(self.granted_scopes, "principal.granted_scopes", allow_empty=True)
        object.__setattr__(self, "granted_scopes", scopes)

    def to_dict(self) -> dict[str, object]:
        return {
            "granted_scopes": list(self.granted_scopes),
            "kind": self.kind.value,
            "principal_id": self.principal_id,
            "tenant_id": self.tenant_id,
        }

    @classmethod
    def from_mapping(cls, value: object) -> Principal:
        data = _require_mapping(value, "principal")
        _check_fields(
            data,
            field_path="principal",
            required=frozenset({"principal_id", "kind", "tenant_id", "granted_scopes"}),
        )
        return cls(
            principal_id=_require_identifier(data["principal_id"], "principal.principal_id"),
            kind=_parse_enum(PrincipalKind, data["kind"], "principal.kind"),
            tenant_id=_require_identifier(data["tenant_id"], "principal.tenant_id"),
            granted_scopes=_parse_scopes(data["granted_scopes"], "principal.granted_scopes", allow_empty=True),
        )


@dataclass(frozen=True, slots=True)
class CredentialProofMetadata:
    """可审计的凭据描述元数据，刻意不承载签名、token 或其他原始证明材料。"""

    kind: CredentialProofKind
    key_id: str
    algorithm: str
    proof_id: str
    issued_at_epoch_ms: int
    expires_at_epoch_ms: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, CredentialProofKind):
            raise _error(IdentityReasonCode.FIELD_INVALID, "credential_proof.kind", "must be a CredentialProofKind")
        _require_identifier(self.key_id, "credential_proof.key_id")
        _require_algorithm(self.algorithm, "credential_proof.algorithm")
        _require_identifier(self.proof_id, "credential_proof.proof_id")
        issued = _require_epoch_ms(self.issued_at_epoch_ms, "credential_proof.issued_at_epoch_ms")
        if self.expires_at_epoch_ms is not None:
            expires = _require_epoch_ms(self.expires_at_epoch_ms, "credential_proof.expires_at_epoch_ms")
            if expires <= issued:
                raise _error(
                    IdentityReasonCode.FIELD_INVALID,
                    "credential_proof.expires_at_epoch_ms",
                    "must be greater than issued_at_epoch_ms",
                )

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "algorithm": self.algorithm,
            "issued_at_epoch_ms": self.issued_at_epoch_ms,
            "key_id": self.key_id,
            "kind": self.kind.value,
            "proof_id": self.proof_id,
        }
        if self.expires_at_epoch_ms is not None:
            data["expires_at_epoch_ms"] = self.expires_at_epoch_ms
        return data

    @classmethod
    def from_mapping(cls, value: object) -> CredentialProofMetadata:
        data = _require_mapping(value, "credential_proof")
        _check_fields(
            data,
            field_path="credential_proof",
            required=frozenset({"kind", "key_id", "algorithm", "proof_id", "issued_at_epoch_ms"}),
            optional=frozenset({"expires_at_epoch_ms"}),
        )
        expires_raw = data.get("expires_at_epoch_ms")
        return cls(
            kind=_parse_enum(CredentialProofKind, data["kind"], "credential_proof.kind"),
            key_id=_require_identifier(data["key_id"], "credential_proof.key_id"),
            algorithm=_require_algorithm(data["algorithm"], "credential_proof.algorithm"),
            proof_id=_require_identifier(data["proof_id"], "credential_proof.proof_id"),
            issued_at_epoch_ms=_require_epoch_ms(data["issued_at_epoch_ms"], "credential_proof.issued_at_epoch_ms"),
            expires_at_epoch_ms=(
                None
                if expires_raw is None
                else _require_epoch_ms(expires_raw, "credential_proof.expires_at_epoch_ms")
            ),
        )


@dataclass(frozen=True, slots=True)
class CredentialProof:
    """仅在验证调用期间携带的瞬时凭据，不属于审计信封。

    ``raw_proof`` 可为 HTTP 签名、token、API key 或证书材料；它被排除出 repr、相等
    比较和所有序列化操作，防止诊断路径将秘密当作身份元数据保存。
    """

    metadata: CredentialProofMetadata
    raw_proof: str | bytes = field(repr=False, compare=False, hash=False)

    def __post_init__(self) -> None:
        if not isinstance(self.metadata, CredentialProofMetadata):
            raise _error(IdentityReasonCode.FIELD_INVALID, "credential.metadata", "must be CredentialProofMetadata")
        _validate_runtime_credential_proof(self.metadata.kind, self.raw_proof)

    def audit_dict(self) -> dict[str, object]:
        """Return a deliberately redacted audit view."""

        return {
            "metadata": self.metadata.to_dict(),
            "raw_proof": "[REDACTED]",
            "raw_proof_present": True,
        }


@dataclass(frozen=True, slots=True)
class ScopeMatchResult:
    """Deterministic result of matching required scopes against grants."""

    allowed: bool
    mode: ScopeMatchMode
    matched_scopes: tuple[str, ...]
    unmatched_scopes: tuple[str, ...]
    reason_code: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "matched_scopes": list(self.matched_scopes),
            "mode": self.mode.value,
            "reason_code": self.reason_code,
            "unmatched_scopes": list(self.unmatched_scopes),
        }


def _scope_is_granted(granted_scope: str, required_scope: str) -> bool:
    if granted_scope == "*" or granted_scope == required_scope:
        return True
    if granted_scope.endswith(":*"):
        prefix = granted_scope[:-2]
        return required_scope.startswith(f"{prefix}:")
    return False


def match_scopes(
    granted_scopes: Sequence[str],
    required_scopes: Sequence[str],
    *,
    mode: ScopeMatchMode = ScopeMatchMode.ALL,
) -> ScopeMatchResult:
    """确定性匹配精确 scope 与末段通配符，但不授予或提升任何权限。

    空或非法请求 scope 会失败关闭，``ALL``/``ANY`` 只定义现有 grants 的判定方式；
    调用者仍须将结果置于实际认证与授权流程中，不能把匹配函数当作认证器。
    """

    if not isinstance(mode, ScopeMatchMode):
        raise _error(IdentityReasonCode.FIELD_INVALID, "scope_match.mode", "must be a ScopeMatchMode")
    grants = _parse_scopes(granted_scopes, "scope_match.granted_scopes", allow_empty=True)
    required = _parse_scopes(required_scopes, "scope_match.required_scopes", allow_empty=False)
    matched = tuple(
        scope for scope in required if any(_scope_is_granted(grant, scope) for grant in grants)
    )
    unmatched = tuple(scope for scope in required if scope not in matched)
    allowed = not unmatched if mode is ScopeMatchMode.ALL else bool(matched)
    return ScopeMatchResult(
        allowed=allowed,
        mode=mode,
        matched_scopes=matched,
        unmatched_scopes=unmatched,
        reason_code=None if allowed else IdentityReasonCode.SCOPE_DENIED.value,
    )


@dataclass(frozen=True, slots=True)
class IngressIdentityEnvelope:
    """绑定单次入站请求的无密钥身份信息，可安全进入签名载荷和审计记录。"""

    schema_version: IdentitySchemaVersion
    principal: Principal
    credential_proof: CredentialProofMetadata
    audience: str
    request_id: str
    requested_scopes: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version is not IdentitySchemaVersion.V1:
            raise _error(
                IdentityReasonCode.VERSION_UNSUPPORTED,
                "schema_version",
                "schema version is not supported",
            )
        if not isinstance(self.principal, Principal):
            raise _error(IdentityReasonCode.FIELD_INVALID, "principal", "must be Principal")
        if not isinstance(self.credential_proof, CredentialProofMetadata):
            raise _error(
                IdentityReasonCode.FIELD_INVALID,
                "credential_proof",
                "must be CredentialProofMetadata",
            )
        _require_identifier(self.audience, "audience")
        _require_identifier(self.request_id, "request_id")
        scopes = _parse_scopes(self.requested_scopes, "requested_scopes", allow_empty=False)
        object.__setattr__(self, "requested_scopes", scopes)

    def evaluate_scopes(self, *, mode: ScopeMatchMode = ScopeMatchMode.ALL) -> ScopeMatchResult:
        """用声明的 grants 评估本请求所需 scope；结果不替代凭据验证。"""
        return match_scopes(self.principal.granted_scopes, self.requested_scopes, mode=mode)

    def to_dict(self) -> dict[str, object]:
        """Return the complete and secret-free schema-v1 wire object."""

        return {
            "audience": self.audience,
            "credential_proof": self.credential_proof.to_dict(),
            "principal": self.principal.to_dict(),
            "request_id": self.request_id,
            "requested_scopes": list(self.requested_scopes),
            "schema_version": self.schema_version.value,
        }

    def to_canonical_json(self) -> str:
        """Serialize deterministically for signing, hashing, and replay tests."""

        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @classmethod
    def from_mapping(cls, value: object) -> IngressIdentityEnvelope:
        data = _require_mapping(value, "identity_envelope")
        _check_fields(
            data,
            field_path="identity_envelope",
            required=frozenset(
                {
                    "schema_version",
                    "principal",
                    "credential_proof",
                    "audience",
                    "request_id",
                    "requested_scopes",
                }
            ),
        )
        return cls(
            schema_version=_parse_enum(IdentitySchemaVersion, data["schema_version"], "schema_version"),
            principal=Principal.from_mapping(data["principal"]),
            credential_proof=CredentialProofMetadata.from_mapping(data["credential_proof"]),
            audience=_require_identifier(data["audience"], "audience"),
            request_id=_require_identifier(data["request_id"], "request_id"),
            requested_scopes=_parse_scopes(data["requested_scopes"], "requested_scopes", allow_empty=False),
        )

    @classmethod
    def from_canonical_json(cls, value: str | bytes) -> IngressIdentityEnvelope:
        return parse_identity_envelope(value)


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> Mapping[str, object]:
    """拒绝重复键，防止不同 JSON 解析器对同一签名输入选择不同字段值。"""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _error(
                IdentityReasonCode.FIELD_UNEXPECTED,
                key,
                "duplicate JSON object key is forbidden",
            )
        result[key] = value
    return MappingProxyType(result)


def parse_identity_envelope(value: str | bytes) -> IngressIdentityEnvelope:
    """解析为经字段白名单验证的信封；任一歧义或格式错误均以稳定原因码失败关闭。"""

    if not isinstance(value, (str, bytes)) or not value:
        raise _error(IdentityReasonCode.PAYLOAD_INVALID, "identity_envelope", "JSON payload is required")
    try:
        payload = json.loads(value, object_pairs_hook=_reject_duplicate_keys)
    except IdentityContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _error(IdentityReasonCode.PAYLOAD_INVALID, "identity_envelope", "malformed JSON payload") from exc
    return IngressIdentityEnvelope.from_mapping(payload)


__all__ = [
    "CredentialProof",
    "CredentialProofKind",
    "CredentialProofMetadata",
    "IdentityContractError",
    "IdentityReasonCode",
    "IdentitySchemaVersion",
    "IngressIdentityEnvelope",
    "Principal",
    "PrincipalKind",
    "ScopeMatchMode",
    "ScopeMatchResult",
    "match_scopes",
    "parse_identity_envelope",
]
