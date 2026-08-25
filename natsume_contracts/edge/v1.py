"""与 Edge 分发包共享的版本化、运行时无关契约。

本模块只依赖标准库，使 Edge 与桥接客户端无需安装 Host 运行时也能构造认证请求、
识别稳定原因码并声明注入端口。它是控制面向设备侧传递“可验证描述”的边界，而非
传递 Host 实现或秘密的通道。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import re
from typing import Any, Mapping, Protocol, Sequence
import uuid

from natsume_contracts.identity import CredentialProof, IngressIdentityEnvelope


EDGE_CONTRACT_SCHEMA_VERSION = "natsume.edge.contract.v1"
SECURE_INGRESS_SIGNATURE_SCHEMA_VERSION = "natsume.secure-ingress.signature.v1"
S_CONFIG_SNAPSHOT = "S_CONFIG_SNAPSHOT"
CONFIG_SNAPSHOT_VERSION = "v1"
CONFIG_SNAPSHOT_DIGEST_PREFIX = "cfg_snap::sha256:"
REDACTED_PLACEHOLDER = "<redacted>"

# Stable wire reason codes. Values deliberately equal their symbolic names.
_REASON_CODES = (
    "ASR_DEVICE_ERROR ASR_DEVICE_RECOVERED ASR_DEVICE_RECOVERY_EXHAUSTED "
    "ASR_GPU_OOM ASR_LOW_CONFIDENCE ASR_MODEL_UNAVAILABLE ASR_STREAM_INTERRUPTED "
    "ASR_VAD_NO_SPEECH AVATAR_FREE_ACTION_BLOCKED AVATAR_TAG_INVALID "
    "CONFIG_ENV_INVALID CONFIG_REQUIRED_MISSING CONFIG_VALUE_INVALID "
    "EDGE_RPC_AUTH_EXPIRED EDGE_RPC_AUTH_INVALID EDGE_RPC_SCHEMA_INVALID "
    "REPLAY_DETECTED RESOURCE_GATE_PREEMPTED RPC_EVENT_BATCH_PARTIAL_FAIL "
    "RPC_EVENT_NETWORK_UNAVAILABLE RPC_EVENT_SCHEMA_INVALID "
    "SERVICE_UNAVAILABLE_DEPENDENCY SERVICE_UNAVAILABLE_EDGE_RPC "
    "SERVICE_UNAVAILABLE_GENERIC TTS_GPU_OOM TTS_MODEL_UNAVAILABLE TTS_SYNTH_TIMEOUT "
    "VOICE_DECISION_FIELD_FORBIDDEN VOICE_DRAFT_ONLY_REQUIRED "
    "VOICE_EXECUTION_CLAIM_FORBIDDEN "
    "VOICE_GATE_PREEMPTED VOICE_GATE_PRIORITY_BLOCKED VOICE_GATE_RESOURCE_LIMITED "
    "VOICE_GATE_TASK_INVALID VOICE_INTERRUPTED VOICE_LOW_CONFIDENCE "
    "VOICE_OUT_OF_ORDER VOICE_PLAYBACK_FAILED VOICE_SCHEMA_INVALID "
    "VOICE_SYNTH_FAILED VOICE_THROTTLED VTS_API_ERROR VTS_CONNECTION_FAILED "
    "VTS_DURATION_INVALID VTS_EXPRESSION_INVALID VTS_EXPRESSION_TIMEOUT "
    "VTS_EXPRESSION_UNAVAILABLE "
    "VTS_INTENSITY_OUT_OF_RANGE VTS_RECONNECTED VTS_RECONNECT_EXHAUSTED "
    "VTS_TAG_UNKNOWN KEY_PROFILE_EXPIRED KEY_PROFILE_SCOPE_INVALID "
    "KEY_PROFILE_SECRET_REF_MISSING"
).split()
globals().update({name: name for name in _REASON_CODES})


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex}"


class EdgeVoiceEventLedger(Protocol):
    def log_voice_event(self, **kwargs: Any) -> Any: ...


class GatewayTransportStore(Protocol):
    def assert_schema_ready(self) -> None: ...
    def accept_inbox(self, **kwargs: Any) -> Any: ...
    def enqueue_outbox(self, **kwargs: Any) -> Any: ...
    def claim_inbox(self, **kwargs: Any) -> Any: ...
    def claim_outbox(self, **kwargs: Any) -> Any: ...


class GatewayTransportStoreFactory(Protocol):
    def __call__(self, db_path: str, *, verify_schema: bool = True) -> GatewayTransportStore: ...


class IngressSurface(str, Enum):
    HOST = "host"


@dataclass(frozen=True, slots=True)
class SecureIngressRequest:
    """进入 Host 输入面的原始传输请求，供认证层生成不可歧义的签名材料。

    ``raw_body`` 只在当前认证处理期间使用；持久化契约应仅保存其摘要或受控引用，
    以免把未经脱敏的传输内容扩散到后续运行链。
    """
    identity: IngressIdentityEnvelope | None
    credential: CredentialProof | None
    surface: str
    method: str
    normalized_path: str
    action: str
    required_scopes: tuple[str, ...]
    idempotency_key: str
    nonce: str
    raw_body: bytes
    received_at_epoch_ms: int
    transport_key: str
    correlation_id: str | None = None

    @property
    def body_sha256(self) -> str:
        return hashlib.sha256(self.raw_body).hexdigest()

    def canonical_auth_bytes(self) -> bytes:
        """确定性编码所有影响认证语义的字段，避免字段顺序造成验签分叉。"""
        if self.identity is None:
            raise ValueError("identity is required for canonical auth input")
        payload = {
            "action": self.action,
            "audience": self.identity.audience,
            "body_sha256": self.body_sha256,
            "correlation_id": self.correlation_id,
            "credential": self.identity.credential_proof.to_dict(),
            "idempotency_key": self.idempotency_key,
            "identity_schema": self.identity.schema_version.value,
            "method": self.method.upper(),
            "nonce": self.nonce,
            "path": self.normalized_path,
            "principal": self.identity.principal.to_dict(),
            "request_id": self.identity.request_id,
            "requested_scopes": list(self.identity.requested_scopes),
            "required_scopes": sorted(set(self.required_scopes)),
            "schema_version": SECURE_INGRESS_SIGNATURE_SCHEMA_VERSION,
            "surface": self.surface,
            "transport_key_digest": digest_identifier(self.transport_key),
        }
        return json.dumps(
            payload, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")


def digest_identifier(value: str | None) -> str | None:
    text = str(value or "").strip()
    return f"sha256:{hashlib.sha256(text.encode()).hexdigest()}" if text else None


def build_surface_request(
    *,
    surface: IngressSurface,
    identity: IngressIdentityEnvelope | None,
    credential: CredentialProof | None,
    method: str,
    normalized_path: str,
    idempotency_key: str,
    nonce: str,
    raw_body: bytes,
    received_at_epoch_ms: int,
    transport_key: str,
    correlation_id: str | None = None,
) -> SecureIngressRequest:
    """把 Host 入口收窄为固定 action、audience 与最小必需 scope。

    调用方不能借由此工厂伪造别的 surface 或扩大权限；实际 credential 的验证仍由
    secure-ingress 负责，本函数只固定签名所覆盖的语义。
    """
    if surface is not IngressSurface.HOST:
        raise ValueError("SECURE_INGRESS_SURFACE_INVALID")
    if identity is not None and identity.audience != "host":
        raise ValueError("SECURE_INGRESS_AUDIENCE_MISMATCH")
    return SecureIngressRequest(
        identity=identity,
        credential=credential,
        surface="host.input",
        method=method,
        normalized_path=normalized_path,
        action="host:input",
        required_scopes=("host:write",),
        idempotency_key=idempotency_key,
        nonce=nonce,
        raw_body=bytes(raw_body),
        received_at_epoch_ms=int(received_at_epoch_ms),
        transport_key=transport_key,
        correlation_id=correlation_id,
    )


def build_hmac_sha256_signature(request: SecureIngressRequest, secret: str | bytes) -> str:
    key = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    return _hmac_sha256(key, request.canonical_auth_bytes())


EMOTION_TAG_TO_VAD: dict[str, tuple[float, float, float]] = {
    "HAPPY": (0.60, 0.40, 0.30), "EXCITED": (0.80, 0.80, 0.50),
    "SAD": (-0.60, -0.20, -0.30), "ANGRY": (-0.70, 0.80, 0.60),
    "CALM": (0.20, -0.40, 0.10), "THINKING": (0.00, 0.20, 0.40),
    "NEUTRAL": (0.00, 0.00, 0.00), "SURPRISED": (0.30, 0.70, 0.00),
    "LOVING": (0.80, 0.30, 0.20), "EMBARRASSED": (-0.20, 0.50, -0.50),
    "CONFUSED": (-0.10, 0.30, -0.30), "BORED": (-0.30, -0.50, 0.00),
    "PROUD": (0.70, 0.50, 0.70), "GRATEFUL": (0.70, 0.20, -0.20),
    "ANXIOUS": (-0.40, 0.70, -0.40), "FRUSTRATED": (-0.50, 0.60, 0.30),
}
EMOTION_TAG_ALIASES = {
    "HAPPINESS": "HAPPY", "JOY": "HAPPY", "JOYFUL": "HAPPY",
    "EXCITEMENT": "EXCITED", "SADNESS": "SAD", "ANGER": "ANGRY",
    "LOVE": "LOVING", "ANXIETY": "ANXIOUS", "TIRED": "BORED",
    "ANNOYED": "FRUSTRATED", "SHY": "EMBARRASSED",
}


def normalize_emotion_tag(value: str | None) -> str:
    text = str(value or "NEUTRAL").strip().upper().replace("-", "_").replace(" ", "_")
    text = re.sub(r"[^A-Z0-9_]+", "_", text).strip("_") or "NEUTRAL"
    return EMOTION_TAG_ALIASES.get(text, text)


def emotion_tag_from_vad(valence: float, arousal: float, dominance: float) -> str:
    """Map a bounded VAD vector to the shared edge/host emotion tag."""

    if valence > 0.5 and arousal > 0.5:
        return "EXCITED"
    if valence > 0.3 and arousal > 0.0:
        return "HAPPY"
    if valence < -0.5 and arousal > 0.3:
        return "ANGRY"
    if valence < -0.3 and arousal < 0.0:
        return "SAD"
    if arousal > 0.4 and abs(valence) < 0.3:
        return "SURPRISED"
    if arousal < -0.3 and abs(valence) < 0.3:
        return "CALM"
    if dominance > 0.3 and abs(valence) < 0.3:
        return "THINKING"
    return "NEUTRAL"


@dataclass(slots=True)
class EmotionVector:
    valence: float = 0.0
    arousal: float = 0.0
    dominance: float = 0.0
    ts_ms: int = 0

    def __post_init__(self) -> None:
        self.valence = max(-1.0, min(1.0, float(self.valence)))
        self.arousal = max(-1.0, min(1.0, float(self.arousal)))
        self.dominance = max(-1.0, min(1.0, float(self.dominance)))

    def to_mood_state(self) -> str:
        if self.valence < -0.5 and self.arousal < -0.3:
            return "critical"
        if self.valence < -0.2 or self.arousal < -0.4:
            return "tired"
        if self.valence > 0.3 and self.arousal > 0.3:
            return "engaged"
        if abs(self.valence) < 0.2 and abs(self.arousal) < 0.2:
            return "calm"
        return "steady"

    def to_emotion_tag(self) -> str:
        return emotion_tag_from_vad(self.valence, self.arousal, self.dominance)


@dataclass(frozen=True, slots=True)
class KeyProfile:
    provider: str
    node_id: str
    scope: str
    secret_ref: str
    rotation_epoch: int
    status: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider, "node_id": self.node_id, "scope": self.scope,
            "secret_ref": self.secret_ref, "rotation_epoch": self.rotation_epoch,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class KeyProfileResolveResult:
    success: bool
    reason_code: str | None
    detail: str
    secret_value: str | None
    secret_ref: str | None
    profile: KeyProfile | None


def resolve_secret_from_profiles(
    *, provider: str, node_id: str, scope: str,
    key_profiles: Sequence[Mapping[str, Any]] | None,
    secret_store: Mapping[str, Any] | None, current_epoch: int,
) -> KeyProfileResolveResult:
    """在提供者、节点和作用域三重匹配后解析最新有效的秘密引用。

    找不到、跨作用域或过期都返回不含 ``secret_value`` 的失败结果，防止诊断路径
    意外泄露值；轮换 epoch 则让旧配置无法在恢复后继续获得授权。
    """
    profiles = [KeyProfile(
        provider=str(row.get("provider") or "").strip(),
        node_id=str(row.get("node_id") or "").strip(),
        scope=str(row.get("scope") or "").strip(),
        secret_ref=str(row.get("secret_ref") or "").strip(),
        rotation_epoch=_int(row.get("rotation_epoch")),
        status=str(row.get("status") or "active").strip().lower(),
    ) for row in key_profiles or ()]
    owner = [p for p in profiles if p.provider.lower() == str(provider).lower() and p.node_id.lower() == str(node_id).lower()]
    if not owner:
        return KeyProfileResolveResult(False, KEY_PROFILE_SECRET_REF_MISSING, "key profile not found", None, None, None)
    scoped = [p for p in owner if p.scope.lower() == str(scope).lower()]
    if not scoped:
        return KeyProfileResolveResult(False, KEY_PROFILE_SCOPE_INVALID, "key profile scope mismatch", None, None, None)
    profile = max(scoped, key=lambda item: item.rotation_epoch)
    if profile.status != "active" or profile.rotation_epoch < int(current_epoch):
        return KeyProfileResolveResult(False, KEY_PROFILE_EXPIRED, "key profile expired", None, profile.secret_ref, profile)
    secret = str(dict(secret_store or {}).get(profile.secret_ref) or "").strip()
    if not profile.secret_ref or not secret:
        return KeyProfileResolveResult(False, KEY_PROFILE_SECRET_REF_MISSING, "secret ref missing", None, profile.secret_ref or None, profile)
    return KeyProfileResolveResult(True, None, "secret resolved from key profile", secret, profile.secret_ref, profile)


def build_config_snapshot(
    *, component: str, env: str, values: Mapping[str, Any],
    override_sources: Mapping[str, str], redacted_keys: Sequence[str],
    version: str = CONFIG_SNAPSHOT_VERSION,
) -> dict[str, Any]:
    """生成可审计配置快照，只记录脱敏后的值与来源并附加内容摘要。"""
    payload: dict[str, Any] = {
        "component": str(component), "env": str(env), "version": str(version),
        "values": _sorted_mapping(values),
        "override_sources": {str(k): str(override_sources[k]) for k in sorted(override_sources)},
        "redacted_keys": sorted(str(key) for key in redacted_keys),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["digest"] = CONFIG_SNAPSHOT_DIGEST_PREFIX + hashlib.sha256(encoded.encode()).hexdigest()
    payload["stage"] = S_CONFIG_SNAPSHOT
    return payload


def resolve_override_sources(
    *, field_to_env_key: Mapping[str, str], env_layer: Mapping[str, Any],
    local_overrides: Mapping[str, Any],
) -> dict[str, str]:
    return {
        field: ("local_overrides" if key in local_overrides else "env" if key in env_layer else "default")
        for field, key in sorted(field_to_env_key.items())
    }


def collect_redacted_keys(
    *, secret_env_keys: Sequence[str] | set[str], env_layer: Mapping[str, Any],
    local_overrides: Mapping[str, Any],
) -> list[str]:
    return sorted(
        key for key in secret_env_keys
        if str((local_overrides if key in local_overrides else env_layer).get(key) or "").strip()
    )


def redact_secret(raw_value: Any) -> str | None:
    return REDACTED_PLACEHOLDER if str(raw_value or "").strip() else None


def _int(value: Any) -> int:
    try: return int(str(value).strip())
    except (TypeError, ValueError): return 0


def _sorted_mapping(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): payload[key] for key in sorted(payload, key=lambda item: str(item))}


def _hmac_sha256(key: bytes, payload: bytes) -> str:
    block_size = 64
    normalized = hashlib.sha256(key).digest() if len(key) > block_size else key
    normalized = normalized.ljust(block_size, b"\0")
    outer = bytes(byte ^ 0x5C for byte in normalized)
    inner = bytes(byte ^ 0x36 for byte in normalized)
    return hashlib.sha256(outer + hashlib.sha256(inner + payload).digest()).hexdigest()


__all__ = [
    *_REASON_CODES,
    "EDGE_CONTRACT_SCHEMA_VERSION", "S_CONFIG_SNAPSHOT", "EdgeVoiceEventLedger",
    "EmotionVector", "EMOTION_TAG_TO_VAD", "GatewayTransportStore",
    "GatewayTransportStoreFactory", "IngressSurface", "SecureIngressRequest",
    "build_config_snapshot", "build_hmac_sha256_signature", "build_surface_request",
    "collect_redacted_keys", "emotion_tag_from_vad", "new_request_id", "normalize_emotion_tag", "now_ms",
    "redact_secret", "resolve_override_sources", "resolve_secret_from_profiles",
]
