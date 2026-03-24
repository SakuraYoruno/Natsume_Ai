"""Edge configuration contract for RPC capabilities and resource gate defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from host_brain.core.config_snapshot import (
    build_config_snapshot,
    collect_redacted_keys,
    redact_secret,
    resolve_override_sources,
    S_CONFIG_SNAPSHOT,
)
from host_brain.core.errors import (
    CONFIG_ENV_INVALID,
    CONFIG_REQUIRED_MISSING,
    CONFIG_VALUE_INVALID,
)
from host_brain.core.key_profiles import resolve_secret_from_profiles


ENV_DEV = "dev"
ENV_TEST = "test"
ENV_PROD = "prod"
ALLOWED_ENVS = {ENV_DEV, ENV_TEST, ENV_PROD}

S_EDGE_CONFIG = "S_EDGE_CONFIG"
EDGE_SNAPSHOT_COMPONENT = "edge"

EDGE_FIELD_TO_ENV_KEY = {
    "env": "NASTUME_ENV",
    "rpc_host": "EDGE_RPC_HOST",
    "rpc_port": "EDGE_RPC_PORT",
    "hmac_shared_key_set": "EDGE_HMAC_SHARED_KEY",
    "hmac_shared_key": "EDGE_HMAC_SHARED_KEY",
    "hmac_shared_key_ref": "EDGE_HMAC_SHARED_KEY_REF",
    "key_profile_status": "EDGE_HMAC_SHARED_KEY_REF",
    "allow_insecure_auth": "EDGE_ALLOW_INSECURE_AUTH",
    "rpc_auth_window_ms": "EDGE_RPC_AUTH_WINDOW_MS",
    "rpc_nonce_window_ms": "EDGE_RPC_NONCE_WINDOW_MS",
    "rpc_gateway_enabled": "EDGE_RPC_GATEWAY_ENABLED",
    "asr_enabled": "EDGE_ASR_ENABLED",
    "tts_enabled": "EDGE_TTS_ENABLED",
    "barge_in_enabled": "EDGE_BARGE_IN_ENABLED",
    "fallback_llm_enabled": "EDGE_FALLBACK_LLM_ENABLED",
    "resource_gate_enabled": "EDGE_RESOURCE_GATE_ENABLED",
    "max_vram_mb": "EDGE_MAX_VRAM_MB",
    "max_queue_length": "EDGE_MAX_QUEUE_LENGTH",
    # Unified Message Gateway
    "gateway_enabled": "EDGE_GATEWAY_ENABLED",
    "gateway_port": "EDGE_GATEWAY_PORT",
    "gateway_platforms": "EDGE_GATEWAY_PLATFORMS",
    "wx_self_wxid": "WECHAT_SELF_WXID",
    "wx_db_storage_dir": "WECHAT_DB_STORAGE_DIR",
    "wx_decrypt_output_dir": "WECHAT_DECRYPT_OUTPUT_DIR",
    "wx_key_cache_path": "WECHAT_KEY_CACHE_PATH",
    "wx_poll_interval_s": "WECHAT_POLL_INTERVAL_S",
    "wx_decrypt_refresh_interval_s": "WECHAT_DECRYPT_REFRESH_INTERVAL_S",
    "qq_onebot_api_base": "QQ_ONEBOT_API_BASE",
    "qq_self_id": "QQ_SELF_ID",
    "feishu_app_id": "FEISHU_APP_ID",
    "feishu_app_secret": "FEISHU_APP_SECRET",
    "feishu_encrypt_key": "FEISHU_ENCRYPT_KEY",
    "feishu_verification_token": "FEISHU_VERIFICATION_TOKEN",
    "wecom_corp_id": "WECOM_CORP_ID",
    "wecom_corp_secret": "WECOM_CORP_SECRET",
    "wecom_agent_id": "WECOM_AGENT_ID",
    "wecom_token": "WECOM_TOKEN",
    "wecom_encoding_aes_key": "WECOM_ENCODING_AES_KEY",
    # VLM 视觉定位
    "vlm_enabled": "EDGE_VLM_ENABLED",
    "vlm_model": "EDGE_VLM_MODEL",
    "vlm_base_url": "EDGE_VLM_BASE_URL",
    "vlm_timeout_s": "EDGE_VLM_TIMEOUT_S",
    "vlm_confidence_threshold": "EDGE_VLM_CONFIDENCE_THRESHOLD",
    "vlm_max_image_width": "EDGE_VLM_MAX_IMAGE_WIDTH",
}
EDGE_SECRET_ENV_KEYS = {"EDGE_HMAC_SHARED_KEY"}


@dataclass(frozen=True)
class ConfigIssue:
    reason_code: str
    detail: str
    field: str | None = None


@dataclass(frozen=True)
class EdgeConfig:
    env: str
    rpc_host: str
    rpc_port: int
    hmac_shared_key: str | None
    hmac_shared_key_ref: str | None
    key_profile_status: str | None
    allow_insecure_auth: bool
    rpc_auth_window_ms: int
    rpc_nonce_window_ms: int
    rpc_gateway_enabled: bool
    asr_enabled: bool
    tts_enabled: bool
    barge_in_enabled: bool
    fallback_llm_enabled: bool
    resource_gate_enabled: bool
    max_vram_mb: int
    max_queue_length: int
    # Unified Message Gateway
    gateway_enabled: bool = True
    gateway_port: int = 19000
    gateway_platforms: tuple[str, ...] = ("wechat", "qq")
    wx_self_wxid: str | None = None
    wx_db_storage_dir: str | None = None
    wx_decrypt_output_dir: str | None = None
    wx_key_cache_path: str | None = None
    wx_poll_interval_s: float = 5.0
    wx_decrypt_refresh_interval_s: int = 3600
    qq_onebot_api_base: str | None = None
    qq_self_id: str | None = None
    feishu_app_id: str | None = None
    feishu_app_secret: str | None = None
    feishu_encrypt_key: str | None = None
    feishu_verification_token: str | None = None
    wecom_corp_id: str | None = None
    wecom_corp_secret: str | None = None
    wecom_agent_id: str | None = None
    wecom_token: str | None = None
    wecom_encoding_aes_key: str | None = None
    # VLM 视觉定位
    vlm_enabled: bool = False
    vlm_model: str = "qwen2.5vl:7b"
    vlm_base_url: str = "http://localhost:11434"
    vlm_timeout_s: float = 30.0
    vlm_confidence_threshold: float = 0.3
    vlm_max_image_width: int = 1280

    def as_dict(self) -> dict[str, Any]:
        return {
            "env": self.env,
            "rpc_host": self.rpc_host,
            "rpc_port": self.rpc_port,
            "hmac_shared_key_set": bool(self.hmac_shared_key),
            "hmac_shared_key_ref": self.hmac_shared_key_ref,
            "key_profile_status": self.key_profile_status,
            "allow_insecure_auth": self.allow_insecure_auth,
            "rpc_auth_window_ms": self.rpc_auth_window_ms,
            "rpc_nonce_window_ms": self.rpc_nonce_window_ms,
            "rpc_gateway_enabled": self.rpc_gateway_enabled,
            "asr_enabled": self.asr_enabled,
            "tts_enabled": self.tts_enabled,
            "barge_in_enabled": self.barge_in_enabled,
            "fallback_llm_enabled": self.fallback_llm_enabled,
            "resource_gate_enabled": self.resource_gate_enabled,
            "max_vram_mb": self.max_vram_mb,
            "max_queue_length": self.max_queue_length,
            # Unified Message Gateway
            "gateway_enabled": self.gateway_enabled,
            "gateway_port": self.gateway_port,
            "gateway_platforms": list(self.gateway_platforms),
            "wx_self_wxid": self.wx_self_wxid,
            "wx_db_storage_dir": self.wx_db_storage_dir,
            "wx_decrypt_output_dir": self.wx_decrypt_output_dir,
            "wx_key_cache_path": self.wx_key_cache_path,
            "wx_poll_interval_s": self.wx_poll_interval_s,
            "wx_decrypt_refresh_interval_s": self.wx_decrypt_refresh_interval_s,
            "qq_onebot_api_base": self.qq_onebot_api_base,
            "qq_self_id": self.qq_self_id,
            "feishu_app_id": self.feishu_app_id,
            "feishu_app_secret": bool(self.feishu_app_secret),
            "feishu_encrypt_key": bool(self.feishu_encrypt_key),
            "feishu_verification_token": bool(self.feishu_verification_token),
            "wecom_corp_id": self.wecom_corp_id,
            "wecom_corp_secret": bool(self.wecom_corp_secret),
            "wecom_agent_id": self.wecom_agent_id,
            "wecom_token": bool(self.wecom_token),
            "wecom_encoding_aes_key": bool(self.wecom_encoding_aes_key),
            # VLM 视觉定位
            "vlm_enabled": self.vlm_enabled,
            "vlm_model": self.vlm_model,
            "vlm_base_url": self.vlm_base_url,
            "vlm_timeout_s": self.vlm_timeout_s,
            "vlm_confidence_threshold": self.vlm_confidence_threshold,
            "vlm_max_image_width": self.vlm_max_image_width,
        }


@dataclass(frozen=True)
class EdgeConfigLoadResult:
    success: bool
    stage: str
    reason_code: str | None
    config: EdgeConfig | None
    issues: tuple[ConfigIssue, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "stage": self.stage,
            "reason_code": self.reason_code,
            "config": self.config.as_dict() if self.config is not None else None,
            "issues": [
                {
                    "reason_code": issue.reason_code,
                    "detail": issue.detail,
                    "field": issue.field,
                }
                for issue in self.issues
            ],
        }


@dataclass(frozen=True)
class ConfigSnapshotExportResult:
    success: bool
    stage: str
    reason_code: str | None
    snapshot: dict[str, Any] | None
    issues: tuple[ConfigIssue, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "stage": self.stage,
            "reason_code": self.reason_code,
            "snapshot": dict(self.snapshot) if self.snapshot is not None else None,
            "issues": [
                {
                    "reason_code": issue.reason_code,
                    "detail": issue.detail,
                    "field": issue.field,
                }
                for issue in self.issues
            ],
        }


def load_edge_config(
    *,
    env: Mapping[str, Any] | None = None,
    local_overrides: Mapping[str, Any] | None = None,
    key_profiles: Sequence[Mapping[str, Any]] | None = None,
    secret_store: Mapping[str, Any] | None = None,
    node_id: str = "edge.default",
    current_epoch: int | None = None,
) -> EdgeConfigLoadResult:
    merged: dict[str, Any] = dict(env or os.environ)
    if local_overrides:
        merged.update(dict(local_overrides))
    env_lookup = _normalize_env_lookup(merged)

    issues: list[ConfigIssue] = []
    runtime_env = _as_text(_env_get(env_lookup, "Natsume_ENV"), ENV_DEV).lower()
    if runtime_env not in ALLOWED_ENVS:
        issues.append(
            ConfigIssue(
                reason_code=CONFIG_ENV_INVALID,
                detail=f"Natsume_ENV must be one of {sorted(ALLOWED_ENVS)}, got {runtime_env}",
                field="Natsume_ENV",
            )
        )
        return _failure(issues)

    rpc_host = _as_text(_env_get(env_lookup, "EDGE_RPC_HOST"), "127.0.0.1")
    if not rpc_host:
        issues.append(
            ConfigIssue(
                reason_code=CONFIG_VALUE_INVALID,
                detail="EDGE_RPC_HOST must be non-empty",
                field="EDGE_RPC_HOST",
            )
        )

    rpc_port = _parse_int(
        _env_get(env_lookup, "EDGE_RPC_PORT"),
        default=17800,
        field_name="EDGE_RPC_PORT",
        min_value=1,
        max_value=65535,
        issues=issues,
    )
    max_vram_mb = _parse_int(
        _env_get(env_lookup, "EDGE_MAX_VRAM_MB"),
        default=4096,
        field_name="EDGE_MAX_VRAM_MB",
        min_value=512,
        max_value=131072,
        issues=issues,
    )
    max_queue_length = _parse_int(
        _env_get(env_lookup, "EDGE_MAX_QUEUE_LENGTH"),
        default=8,
        field_name="EDGE_MAX_QUEUE_LENGTH",
        min_value=1,
        max_value=1024,
        issues=issues,
    )

    allow_insecure_auth = _parse_bool(
        _env_get(env_lookup, "EDGE_ALLOW_INSECURE_AUTH"),
        default=(runtime_env != ENV_PROD),
    )
    rpc_auth_window_ms = _parse_int(
        _env_get(env_lookup, "EDGE_RPC_AUTH_WINDOW_MS"),
        default=15_000,
        field_name="EDGE_RPC_AUTH_WINDOW_MS",
        min_value=1_000,
        max_value=300_000,
        issues=issues,
    )
    rpc_nonce_window_ms = _parse_int(
        _env_get(env_lookup, "EDGE_RPC_NONCE_WINDOW_MS"),
        default=120_000,
        field_name="EDGE_RPC_NONCE_WINDOW_MS",
        min_value=1_000,
        max_value=3_600_000,
        issues=issues,
    )
    hmac_shared_key = _as_optional_text(_env_get(env_lookup, "EDGE_HMAC_SHARED_KEY"))
    hmac_shared_key_ref = _as_optional_text(_env_get(env_lookup, "EDGE_HMAC_SHARED_KEY_REF"))
    key_profile_status: str | None = None
    if not hmac_shared_key and hmac_shared_key_ref:
        resolved = resolve_secret_from_profiles(
            provider="edge_hmac",
            node_id=str(node_id),
            scope="edge.rpc.auth",
            key_profiles=key_profiles,
            secret_store=secret_store,
            current_epoch=int(current_epoch if current_epoch is not None else 0),
        )
        if resolved.success:
            hmac_shared_key = resolved.secret_value
            hmac_shared_key_ref = resolved.secret_ref or hmac_shared_key_ref
            key_profile_status = "resolved"
        else:
            key_profile_status = "degraded"
            if runtime_env == ENV_PROD and not allow_insecure_auth:
                issues.append(
                    ConfigIssue(
                        reason_code=str(resolved.reason_code or CONFIG_REQUIRED_MISSING),
                        detail=str(resolved.detail),
                        field="EDGE_HMAC_SHARED_KEY_REF",
                    )
                )

    if runtime_env == ENV_PROD and not hmac_shared_key and not allow_insecure_auth:
        issues.append(
            ConfigIssue(
                reason_code=CONFIG_REQUIRED_MISSING,
                detail="EDGE_HMAC_SHARED_KEY required in prod when EDGE_ALLOW_INSECURE_AUTH=false",
                field="EDGE_HMAC_SHARED_KEY",
            )
        )

    rpc_gateway_enabled = _parse_bool(_env_get(env_lookup, "EDGE_RPC_GATEWAY_ENABLED"), default=True)
    asr_enabled = _parse_bool(_env_get(env_lookup, "EDGE_ASR_ENABLED"), default=True)
    tts_enabled = _parse_bool(_env_get(env_lookup, "EDGE_TTS_ENABLED"), default=True)
    barge_in_enabled = _parse_bool(_env_get(env_lookup, "EDGE_BARGE_IN_ENABLED"), default=True)
    fallback_llm_enabled = _parse_bool(_env_get(env_lookup, "EDGE_FALLBACK_LLM_ENABLED"), default=True)
    resource_gate_enabled = _parse_bool(_env_get(env_lookup, "EDGE_RESOURCE_GATE_ENABLED"), default=True)

    # Unified Message Gateway
    gateway_enabled = _parse_bool(_env_get(env_lookup, "EDGE_GATEWAY_ENABLED"), default=True)
    gateway_port = _parse_int(
        _env_get(env_lookup, "EDGE_GATEWAY_PORT"),
        default=19000,
        field_name="EDGE_GATEWAY_PORT",
        min_value=1,
        max_value=65535,
        issues=issues,
    )
    gateway_platforms_raw = _as_text(
        _env_get(env_lookup, "EDGE_GATEWAY_PLATFORMS"),
        "wechat,qq",
    )
    parsed_gateway_platforms: list[str] = []
    for piece in gateway_platforms_raw.split(","):
        platform_val = str(piece or "").strip().lower()
        if not platform_val:
            continue
        if platform_val in {"wechat", "qq", "feishu", "wecom"} and platform_val not in parsed_gateway_platforms:
            parsed_gateway_platforms.append(platform_val)
    if not parsed_gateway_platforms:
        parsed_gateway_platforms = ["wechat", "qq"]
    wx_self_wxid = _as_optional_text(_env_get(env_lookup, "WECHAT_SELF_WXID"))
    wx_db_storage_dir = _as_optional_text(_env_get(env_lookup, "WECHAT_DB_STORAGE_DIR"))
    wx_decrypt_output_dir = _as_optional_text(_env_get(env_lookup, "WECHAT_DECRYPT_OUTPUT_DIR"))
    wx_key_cache_path = _as_optional_text(_env_get(env_lookup, "WECHAT_KEY_CACHE_PATH"))
    wx_poll_interval_s_raw = _env_get(env_lookup, "WECHAT_POLL_INTERVAL_S")
    try:
        wx_poll_interval_s = float(wx_poll_interval_s_raw) if wx_poll_interval_s_raw else 5.0
    except (TypeError, ValueError):
        wx_poll_interval_s = 5.0
    wx_decrypt_refresh_interval_s = _parse_int(
        _env_get(env_lookup, "WECHAT_DECRYPT_REFRESH_INTERVAL_S"),
        default=3600,
        field_name="WECHAT_DECRYPT_REFRESH_INTERVAL_S",
        min_value=60,
        max_value=86400,
        issues=issues,
    )
    qq_onebot_api_base = _as_optional_text(_env_get(env_lookup, "QQ_ONEBOT_API_BASE"))
    qq_self_id = _as_optional_text(_env_get(env_lookup, "QQ_SELF_ID"))
    feishu_app_id = _as_optional_text(_env_get(env_lookup, "FEISHU_APP_ID"))
    feishu_app_secret = _as_optional_text(_env_get(env_lookup, "FEISHU_APP_SECRET"))
    feishu_encrypt_key = _as_optional_text(_env_get(env_lookup, "FEISHU_ENCRYPT_KEY"))
    feishu_verification_token = _as_optional_text(_env_get(env_lookup, "FEISHU_VERIFICATION_TOKEN"))
    wecom_corp_id = _as_optional_text(_env_get(env_lookup, "WECOM_CORP_ID"))
    wecom_corp_secret = _as_optional_text(_env_get(env_lookup, "WECOM_CORP_SECRET"))
    wecom_agent_id = _as_optional_text(_env_get(env_lookup, "WECOM_AGENT_ID"))
    wecom_token = _as_optional_text(_env_get(env_lookup, "WECOM_TOKEN"))
    wecom_encoding_aes_key = _as_optional_text(_env_get(env_lookup, "WECOM_ENCODING_AES_KEY"))

    # VLM 视觉定位
    vlm_enabled = _parse_bool(_env_get(env_lookup, "EDGE_VLM_ENABLED"), default=False)
    vlm_model = _as_text(_env_get(env_lookup, "EDGE_VLM_MODEL"), "qwen2.5vl:7b")
    vlm_base_url = _as_text(_env_get(env_lookup, "EDGE_VLM_BASE_URL"), "http://localhost:11434")
    vlm_timeout_s_raw = _env_get(env_lookup, "EDGE_VLM_TIMEOUT_S")
    try:
        vlm_timeout_s = float(vlm_timeout_s_raw) if vlm_timeout_s_raw else 30.0
        vlm_timeout_s = max(1.0, min(vlm_timeout_s, 300.0))
    except (TypeError, ValueError):
        vlm_timeout_s = 30.0
    vlm_confidence_threshold_raw = _env_get(env_lookup, "EDGE_VLM_CONFIDENCE_THRESHOLD")
    try:
        vlm_confidence_threshold = float(vlm_confidence_threshold_raw) if vlm_confidence_threshold_raw else 0.3
        vlm_confidence_threshold = max(0.0, min(vlm_confidence_threshold, 1.0))
    except (TypeError, ValueError):
        vlm_confidence_threshold = 0.3
    vlm_max_image_width = _parse_int(
        _env_get(env_lookup, "EDGE_VLM_MAX_IMAGE_WIDTH"),
        default=1280,
        field_name="EDGE_VLM_MAX_IMAGE_WIDTH",
        min_value=320,
        max_value=3840,
        issues=issues,
    )

    if issues:
        return _failure(issues)

    config = EdgeConfig(
        env=runtime_env,
        rpc_host=rpc_host,
        rpc_port=rpc_port,
        hmac_shared_key=hmac_shared_key,
        hmac_shared_key_ref=hmac_shared_key_ref,
        key_profile_status=key_profile_status,
        allow_insecure_auth=bool(allow_insecure_auth),
        rpc_auth_window_ms=int(rpc_auth_window_ms),
        rpc_nonce_window_ms=int(rpc_nonce_window_ms),
        rpc_gateway_enabled=bool(rpc_gateway_enabled),
        asr_enabled=bool(asr_enabled),
        tts_enabled=bool(tts_enabled),
        barge_in_enabled=bool(barge_in_enabled),
        fallback_llm_enabled=bool(fallback_llm_enabled),
        resource_gate_enabled=bool(resource_gate_enabled),
        max_vram_mb=max_vram_mb,
        max_queue_length=max_queue_length,
        # Unified Message Gateway
        gateway_enabled=bool(gateway_enabled),
        gateway_port=int(gateway_port),
        gateway_platforms=tuple(parsed_gateway_platforms),
        wx_self_wxid=wx_self_wxid,
        wx_db_storage_dir=wx_db_storage_dir,
        wx_decrypt_output_dir=wx_decrypt_output_dir,
        wx_key_cache_path=wx_key_cache_path,
        wx_poll_interval_s=float(wx_poll_interval_s),
        wx_decrypt_refresh_interval_s=int(wx_decrypt_refresh_interval_s),
        qq_onebot_api_base=qq_onebot_api_base,
        qq_self_id=qq_self_id,
        feishu_app_id=feishu_app_id,
        feishu_app_secret=feishu_app_secret,
        feishu_encrypt_key=feishu_encrypt_key,
        feishu_verification_token=feishu_verification_token,
        wecom_corp_id=wecom_corp_id,
        wecom_corp_secret=wecom_corp_secret,
        wecom_agent_id=wecom_agent_id,
        wecom_token=wecom_token,
        wecom_encoding_aes_key=wecom_encoding_aes_key,
        # VLM 视觉定位
        vlm_enabled=bool(vlm_enabled),
        vlm_model=vlm_model,
        vlm_base_url=vlm_base_url,
        vlm_timeout_s=float(vlm_timeout_s),
        vlm_confidence_threshold=float(vlm_confidence_threshold),
        vlm_max_image_width=int(vlm_max_image_width),
    )
    return EdgeConfigLoadResult(
        success=True,
        stage=S_EDGE_CONFIG,
        reason_code=None,
        config=config,
        issues=(),
    )


def export_edge_config_snapshot(
    *,
    env: Mapping[str, Any] | None = None,
    local_overrides: Mapping[str, Any] | None = None,
    key_profiles: Sequence[Mapping[str, Any]] | None = None,
    secret_store: Mapping[str, Any] | None = None,
    node_id: str = "edge.default",
    current_epoch: int | None = None,
) -> ConfigSnapshotExportResult:
    env_layer = dict(env or os.environ)
    overrides = dict(local_overrides or {})
    env_lookup = _normalize_env_lookup(env_layer)
    overrides_lookup = _normalize_env_lookup(overrides)

    load_result = load_edge_config(
        env=env_layer,
        local_overrides=overrides,
        key_profiles=key_profiles,
        secret_store=secret_store,
        node_id=node_id,
        current_epoch=current_epoch,
    )
    if not load_result.success or load_result.config is None:
        return ConfigSnapshotExportResult(
            success=False,
            stage=S_CONFIG_SNAPSHOT,
            reason_code=load_result.reason_code,
            snapshot=None,
            issues=load_result.issues,
        )

    config = load_result.config
    values = config.as_dict()
    values["hmac_shared_key"] = redact_secret(config.hmac_shared_key)

    redacted_keys = collect_redacted_keys(
        secret_env_keys=EDGE_SECRET_ENV_KEYS,
        env_layer=env_lookup,
        local_overrides=overrides_lookup,
    )
    if config.hmac_shared_key and "EDGE_HMAC_SHARED_KEY" not in redacted_keys:
        redacted_keys.append("EDGE_HMAC_SHARED_KEY")
        redacted_keys = sorted(redacted_keys)

    snapshot = build_config_snapshot(
        component=EDGE_SNAPSHOT_COMPONENT,
        env=config.env,
        values=values,
        override_sources=resolve_override_sources(
            field_to_env_key=EDGE_FIELD_TO_ENV_KEY,
            env_layer=env_lookup,
            local_overrides=overrides_lookup,
        ),
        redacted_keys=redacted_keys,
    )
    return ConfigSnapshotExportResult(
        success=True,
        stage=S_CONFIG_SNAPSHOT,
        reason_code=None,
        snapshot=snapshot,
        issues=(),
    )


def build_edge_runtime_statuses(config: EdgeConfig) -> dict[str, dict[str, Any]]:
    statuses: dict[str, dict[str, Any]] = {}

    if not config.rpc_gateway_enabled:
        gateway_down = {
            "available": False,
            "unavailable_kind": "dependency",
            "detail": "edge rpc gateway disabled by config",
        }
        statuses["edge.rpc.gateway"] = gateway_down
        statuses["edge.rpc.asr"] = dict(gateway_down)
        statuses["edge.rpc.tts"] = dict(gateway_down)
        statuses["edge.rpc.barge_in"] = dict(gateway_down)
        statuses["edge.rpc.fallback_llm"] = dict(gateway_down)
        statuses["edge.rpc.resource_gate"] = dict(gateway_down)
        return statuses

    statuses["edge.rpc.gateway"] = {"available": True, "detail": "edge rpc gateway enabled"}
    statuses["edge.rpc.asr"] = _service_status(
        enabled=config.asr_enabled,
        detail="edge asr enabled",
        disabled_detail="edge asr disabled by config",
    )
    statuses["edge.rpc.tts"] = _service_status(
        enabled=config.tts_enabled,
        detail="edge tts enabled",
        disabled_detail="edge tts disabled by config",
    )
    statuses["edge.rpc.barge_in"] = _service_status(
        enabled=(config.barge_in_enabled and config.tts_enabled),
        detail="edge barge-in enabled",
        disabled_detail="edge barge-in requires EDGE_BARGE_IN_ENABLED and EDGE_TTS_ENABLED",
    )
    if config.fallback_llm_enabled:
        statuses["edge.rpc.fallback_llm"] = {
            "available": True,
            "detail": "edge fallback llm enabled",
        }
    else:
        statuses["edge.rpc.fallback_llm"] = {
            "available": False,
            "unavailable_kind": "dependency",
            "detail": "edge fallback llm disabled by config (EDGE_FALLBACK_LLM_ENABLED=false)",
        }
    statuses["edge.rpc.resource_gate"] = _service_status(
        enabled=config.resource_gate_enabled,
        detail="edge resource gate enabled",
        disabled_detail="edge resource gate disabled by config",
    )
    statuses["edge.vlm"] = _service_status(
        enabled=config.vlm_enabled,
        detail=f"edge vlm enabled (model={config.vlm_model})",
        disabled_detail="edge vlm disabled by config (EDGE_VLM_ENABLED=false)",
    )
    return statuses


def _failure(issues: list[ConfigIssue]) -> EdgeConfigLoadResult:
    primary = issues[0] if issues else None
    return EdgeConfigLoadResult(
        success=False,
        stage=S_EDGE_CONFIG,
        reason_code=(primary.reason_code if primary else CONFIG_VALUE_INVALID),
        config=None,
        issues=tuple(issues),
    )


def _service_status(*, enabled: bool, detail: str, disabled_detail: str) -> dict[str, Any]:
    if enabled:
        return {"available": True, "detail": detail}
    return {
        "available": False,
        "unavailable_kind": "dependency",
        "detail": disabled_detail,
    }


def _as_text(value: Any, default: str) -> str:
    text = str(value or "").strip()
    return text if text else str(default)


def _as_optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if text else None


def _normalize_env_lookup(values: Mapping[str, Any]) -> dict[str, Any]:
    lookup: dict[str, Any] = {}
    for raw_key, raw_value in values.items():
        key = str(raw_key)
        lookup[key] = raw_value
        lookup[key.upper()] = raw_value
        lookup[key.lower()] = raw_value
    return lookup


def _env_get(lookup: Mapping[str, Any], key: str) -> Any:
    if key in lookup:
        return lookup.get(key)
    upper = key.upper()
    if upper in lookup:
        return lookup.get(upper)
    lower = key.lower()
    if lower in lookup:
        return lookup.get(lower)
    return None


def _parse_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return bool(default)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _parse_int(
    value: Any,
    *,
    default: int,
    field_name: str,
    min_value: int,
    max_value: int,
    issues: list[ConfigIssue],
) -> int:
    if value is None or str(value).strip() == "":
        return int(default)
    try:
        parsed = int(str(value).strip())
    except ValueError:
        issues.append(
            ConfigIssue(
                reason_code=CONFIG_VALUE_INVALID,
                detail=f"{field_name} must be integer",
                field=field_name,
            )
        )
        return int(default)
    if parsed < min_value or parsed > max_value:
        issues.append(
            ConfigIssue(
                reason_code=CONFIG_VALUE_INVALID,
                detail=f"{field_name} must be in range [{min_value}, {max_value}]",
                field=field_name,
            )
        )
        return int(default)
    return parsed
