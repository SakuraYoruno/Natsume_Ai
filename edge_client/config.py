"""Edge configuration contract for RPC capabilities and resource gate defaults."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from natsume_contracts.edge.v1 import (
    build_config_snapshot,
    collect_redacted_keys,
    redact_secret,
    resolve_override_sources,
    S_CONFIG_SNAPSHOT,
    CONFIG_ENV_INVALID,
    CONFIG_REQUIRED_MISSING,
    CONFIG_VALUE_INVALID,
    resolve_secret_from_profiles,
)


ENV_DEV = "dev"
ENV_TEST = "test"
ENV_PROD = "prod"
ALLOWED_ENVS = {ENV_DEV, ENV_TEST, ENV_PROD}

S_EDGE_CONFIG = "S_EDGE_CONFIG"
EDGE_SNAPSHOT_COMPONENT = "edge"
DEFAULT_KEY_PROFILES = Path("configs/secrets/key_profiles.runtime.json")
DEFAULT_KEY_PROFILES_FALLBACK = Path("configs/secrets/key_profiles.example.json")
DEFAULT_SECRET_STORE = Path("configs/secrets/secret_store.runtime.local.json")
DEFAULT_SECRET_STORE_FALLBACK = Path("configs/secrets/secret_store.runtime.example.json")
DEFAULT_EDGE_NODE_ID = "natsu-node-06"
DEFAULT_EDGE_NODE_ID_SENTINEL = "edge.default"

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
    "avatar_backend": "EDGE_AVATAR_BACKEND",
    "vrm_bridge_host": "EDGE_VRM_BRIDGE_HOST",
    "vrm_bridge_port": "EDGE_VRM_BRIDGE_PORT",
    "vrm_bridge_timeout_s": "EDGE_VRM_BRIDGE_TIMEOUT_S",
    "vrm_model_path": "EDGE_VRM_MODEL_PATH",
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
    # Telegram Userbot
    "telegram_api_id": "TELEGRAM_API_ID",
    "telegram_api_hash": "TELEGRAM_API_HASH",
    "telegram_phone": "TELEGRAM_PHONE",
    "telegram_session_name": "TELEGRAM_SESSION_NAME",
    "telegram_self_id": "TELEGRAM_SELF_ID",
    "telegram_typing_speed": "TELEGRAM_TYPING_SPEED",
    "telegram_min_delay_s": "TELEGRAM_MIN_DELAY_S",
    "telegram_max_delay_s": "TELEGRAM_MAX_DELAY_S",
    "telegram_proxy_type": "TELEGRAM_PROXY_TYPE",
    "telegram_proxy_addr": "TELEGRAM_PROXY_ADDR",
    "telegram_proxy_port": "TELEGRAM_PROXY_PORT",
    # LINE Messaging API
    "line_channel_access_token": "LINE_CHANNEL_ACCESS_TOKEN",
    "line_channel_secret": "LINE_CHANNEL_SECRET",
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
    avatar_backend: str = "vts"
    vrm_bridge_host: str = "127.0.0.1"
    vrm_bridge_port: int = 18770
    vrm_bridge_timeout_s: float = 1.5
    vrm_model_path: str | None = None
    # Unified Message Gateway
    gateway_enabled: bool = True
    gateway_port: int = 19000
    gateway_platforms: tuple[str, ...] = ("wechat", "qq", "feishu", "wecom", "telegram", "line")
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
    # Telegram Userbot
    telegram_api_id: int | None = None
    telegram_api_hash: str | None = None
    telegram_phone: str | None = None
    telegram_session_name: str = "nanami"
    telegram_self_id: str | None = None
    telegram_typing_speed: float = 15.0
    telegram_min_delay_s: float = 1.0
    telegram_max_delay_s: float = 3.0
    telegram_proxy_type: str | None = None
    telegram_proxy_addr: str | None = None
    telegram_proxy_port: int | None = None
    # LINE Messaging API
    line_channel_access_token: str | None = None
    line_channel_secret: str | None = None

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
            "avatar_backend": self.avatar_backend,
            "vrm_bridge_host": self.vrm_bridge_host,
            "vrm_bridge_port": self.vrm_bridge_port,
            "vrm_bridge_timeout_s": self.vrm_bridge_timeout_s,
            "vrm_model_path": self.vrm_model_path,
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
            # Telegram Userbot
            "telegram_api_id": self.telegram_api_id,
            "telegram_api_hash": bool(self.telegram_api_hash),
            "telegram_phone": self.telegram_phone,
            "telegram_session_name": self.telegram_session_name,
            "telegram_self_id": self.telegram_self_id,
            "telegram_typing_speed": self.telegram_typing_speed,
            "telegram_min_delay_s": self.telegram_min_delay_s,
            "telegram_max_delay_s": self.telegram_max_delay_s,
            "telegram_proxy_type": self.telegram_proxy_type,
            "telegram_proxy_addr": self.telegram_proxy_addr,
            "telegram_proxy_port": self.telegram_proxy_port,
            # LINE Messaging API
            "line_channel_access_token": bool(self.line_channel_access_token),
            "line_channel_secret": bool(self.line_channel_secret),
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
    # 局部覆盖仅用于本次装载，且后写入以便开发/部署注入能明确压过进程环境；
    # 后续仍会将键名归一化，因此此处不把大小写差异当成另一套配置来源。
    merged: dict[str, Any] = dict(env or os.environ)
    if local_overrides:
        merged.update(dict(local_overrides))
    env_lookup = _normalize_env_lookup(merged)
    # 密钥档案只提供“如何按节点和 epoch 查找”的上下文。真正的密钥值不会在
    # 此边界展开到日志或快照中，而是在下面按引用解析后仅留给认证运行时使用。
    resolved_key_profiles, resolved_secret_store, resolved_epoch, resolved_node_id = _resolve_runtime_key_materials(
        env=merged,
        key_profiles=key_profiles,
        secret_store=secret_store,
        node_id=node_id,
        current_epoch=current_epoch,
    )

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
        # 直接提供的值优先，避免一次热配置把显式注入的认证材料意外替换为档案结果；
        # 引用解析失败会保留 degraded 状态，供非生产诊断而非伪装成可认证状态。
        resolved = resolve_secret_from_profiles(
            provider="edge_hmac",
            node_id=str(resolved_node_id),
            scope="edge.rpc.auth",
            key_profiles=resolved_key_profiles,
            secret_store=resolved_secret_store,
            current_epoch=int(resolved_epoch),
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

    # “有引用”不等于“当前 epoch 可取得有效密钥”。生产环境在未明确放宽认证时
    # 必须在装载阶段失败，避免 RPC 网关带着空认证材料启动。
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
    fallback_llm_enabled = _parse_bool(_env_get(env_lookup, "EDGE_FALLBACK_LLM_ENABLED"), default=False)
    resource_gate_enabled = _parse_bool(_env_get(env_lookup, "EDGE_RESOURCE_GATE_ENABLED"), default=True)
    avatar_backend = _as_text(_env_get(env_lookup, "EDGE_AVATAR_BACKEND"), "vts").lower()
    if avatar_backend not in {"vts", "vrm"}:
        issues.append(
            ConfigIssue(
                reason_code=CONFIG_VALUE_INVALID,
                detail="EDGE_AVATAR_BACKEND must be one of ['vts', 'vrm']",
                field="EDGE_AVATAR_BACKEND",
            )
        )
    vrm_bridge_host = _as_text(_env_get(env_lookup, "EDGE_VRM_BRIDGE_HOST"), "127.0.0.1")
    vrm_bridge_port = _parse_int(
        _env_get(env_lookup, "EDGE_VRM_BRIDGE_PORT"),
        default=18770,
        field_name="EDGE_VRM_BRIDGE_PORT",
        min_value=1,
        max_value=65535,
        issues=issues,
    )
    vrm_bridge_timeout_s_raw = _env_get(env_lookup, "EDGE_VRM_BRIDGE_TIMEOUT_S")
    try:
        vrm_bridge_timeout_s = float(vrm_bridge_timeout_s_raw) if vrm_bridge_timeout_s_raw else 1.5
    except (TypeError, ValueError):
        vrm_bridge_timeout_s = 1.5
    if vrm_bridge_timeout_s <= 0:
        issues.append(
            ConfigIssue(
                reason_code=CONFIG_VALUE_INVALID,
                detail="EDGE_VRM_BRIDGE_TIMEOUT_S must be positive",
                field="EDGE_VRM_BRIDGE_TIMEOUT_S",
            )
        )
        vrm_bridge_timeout_s = 1.5
    vrm_model_path = _as_optional_text(_env_get(env_lookup, "EDGE_VRM_MODEL_PATH"))

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
        "wechat,qq,feishu,wecom,telegram,line",
    )
    parsed_gateway_platforms: list[str] = []
    for piece in gateway_platforms_raw.split(","):
        platform_val = str(piece or "").strip().lower()
        if not platform_val:
            continue
        if platform_val in {"wechat", "qq", "feishu", "wecom", "telegram", "line"} and platform_val not in parsed_gateway_platforms:
            parsed_gateway_platforms.append(platform_val)
    if not parsed_gateway_platforms:
        parsed_gateway_platforms = ["wechat", "qq", "feishu", "wecom", "telegram", "line"]
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

    # Telegram Userbot
    telegram_api_id_raw = _env_get(env_lookup, "TELEGRAM_API_ID")
    try:
        telegram_api_id: int | None = int(str(telegram_api_id_raw).strip()) if telegram_api_id_raw else None
    except (TypeError, ValueError):
        telegram_api_id = None
    telegram_api_hash = _as_optional_text(_env_get(env_lookup, "TELEGRAM_API_HASH"))
    telegram_phone = _as_optional_text(_env_get(env_lookup, "TELEGRAM_PHONE"))
    telegram_session_name = _as_text(_env_get(env_lookup, "TELEGRAM_SESSION_NAME"), "nanami")
    telegram_self_id = _as_optional_text(_env_get(env_lookup, "TELEGRAM_SELF_ID"))
    telegram_typing_speed_raw = _env_get(env_lookup, "TELEGRAM_TYPING_SPEED")
    try:
        telegram_typing_speed = float(telegram_typing_speed_raw) if telegram_typing_speed_raw else 15.0
    except (TypeError, ValueError):
        telegram_typing_speed = 15.0
    telegram_min_delay_s_raw = _env_get(env_lookup, "TELEGRAM_MIN_DELAY_S")
    try:
        telegram_min_delay_s = float(telegram_min_delay_s_raw) if telegram_min_delay_s_raw else 1.0
    except (TypeError, ValueError):
        telegram_min_delay_s = 1.0
    telegram_max_delay_s_raw = _env_get(env_lookup, "TELEGRAM_MAX_DELAY_S")
    try:
        telegram_max_delay_s = float(telegram_max_delay_s_raw) if telegram_max_delay_s_raw else 3.0
    except (TypeError, ValueError):
        telegram_max_delay_s = 3.0
    telegram_proxy_type = _as_optional_text(_env_get(env_lookup, "TELEGRAM_PROXY_TYPE"))
    telegram_proxy_addr = _as_optional_text(_env_get(env_lookup, "TELEGRAM_PROXY_ADDR"))
    telegram_proxy_port_raw = _env_get(env_lookup, "TELEGRAM_PROXY_PORT")
    try:
        telegram_proxy_port: int | None = int(str(telegram_proxy_port_raw).strip()) if telegram_proxy_port_raw else None
    except (TypeError, ValueError):
        telegram_proxy_port = None

    # LINE Messaging API
    line_channel_access_token = _as_optional_text(_env_get(env_lookup, "LINE_CHANNEL_ACCESS_TOKEN"))
    line_channel_secret = _as_optional_text(_env_get(env_lookup, "LINE_CHANNEL_SECRET"))

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
        avatar_backend=str(avatar_backend),
        vrm_bridge_host=vrm_bridge_host,
        vrm_bridge_port=int(vrm_bridge_port),
        vrm_bridge_timeout_s=float(vrm_bridge_timeout_s),
        vrm_model_path=vrm_model_path,
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
        # Telegram Userbot
        telegram_api_id=telegram_api_id,
        telegram_api_hash=telegram_api_hash,
        telegram_phone=telegram_phone,
        telegram_session_name=telegram_session_name,
        telegram_self_id=telegram_self_id,
        telegram_typing_speed=float(telegram_typing_speed),
        telegram_min_delay_s=float(telegram_min_delay_s),
        telegram_max_delay_s=float(telegram_max_delay_s),
        telegram_proxy_type=telegram_proxy_type,
        telegram_proxy_addr=telegram_proxy_addr,
        telegram_proxy_port=telegram_proxy_port,
        # LINE Messaging API
        line_channel_access_token=line_channel_access_token,
        line_channel_secret=line_channel_secret,
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
    # 快照面向状态查询而非配置回放：先将值投影为可观察字段，再单独标记秘密来源，
    # 使消费者能判断配置由何处覆盖，却无法借快照恢复认证材料。
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
    statuses["edge.rpc.fallback_llm"] = _service_status(
        enabled=config.fallback_llm_enabled,
        detail="edge fallback llm enabled",
        disabled_detail="edge fallback llm disabled by config",
    )
    statuses["edge.rpc.resource_gate"] = _service_status(
        enabled=config.resource_gate_enabled,
        detail="edge resource gate enabled",
        disabled_detail="edge resource gate disabled by config",
    )
    statuses["edge.rpc.avatar"] = {
        "available": True,
        "detail": f"edge avatar backend: {config.avatar_backend}",
    }
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


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _resolve_path(path: Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return _repo_root() / candidate


def _pick_existing_path(primary: Path, fallback: Path) -> Path:
    primary_abs = _resolve_path(primary)
    fallback_abs = _resolve_path(fallback)
    if primary_abs.exists() and primary_abs.is_file():
        return primary_abs
    return fallback_abs


def _load_json_file(path: Path) -> dict[str, Any]:
    resolved = _resolve_path(path)
    if not resolved.exists() or not resolved.is_file():
        return {}
    for encoding in ("utf-8", "utf-8-sig"):
        try:
            payload = json.loads(resolved.read_text(encoding=encoding))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _coerce_non_negative_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return int(default)
    return max(0, parsed)


def _is_default_edge_node_id(value: str | None) -> bool:
    text = str(value or "").strip().lower()
    return not text or text == DEFAULT_EDGE_NODE_ID_SENTINEL


def _resolve_edge_node_id(*, env: Mapping[str, Any], node_id: str | None, key_profiles_payload: Mapping[str, Any]) -> str:
    explicit = str(node_id or "").strip()
    if explicit and not _is_default_edge_node_id(explicit):
        return explicit
    env_lookup = _normalize_env_lookup(env)
    from_env = str(
        _env_get(env_lookup, "EDGE_NODE_ID")
        or _env_get(env_lookup, "Natsume_EDGE_NODE_ID")
        or _env_get(env_lookup, "EDGE_RPC_NODE_ID")
        or "",
    ).strip()
    if from_env:
        return from_env
    from_profiles = str(key_profiles_payload.get("edge_node_id") or "").strip()
    if from_profiles:
        return from_profiles
    return DEFAULT_EDGE_NODE_ID


def _resolve_runtime_key_materials(
    *,
    env: Mapping[str, Any],
    key_profiles: Sequence[Mapping[str, Any]] | None,
    secret_store: Mapping[str, Any] | None,
    node_id: str | None,
    current_epoch: int | None,
) -> tuple[list[Mapping[str, Any]], dict[str, Any], int, str]:
    resolved_profiles: list[Mapping[str, Any]] = list(key_profiles or [])
    resolved_store: dict[str, Any] = dict(secret_store or {})
    resolved_epoch = _coerce_non_negative_int(current_epoch, default=0)
    key_profiles_payload: dict[str, Any] = {}

    # 调用方缺少任一运行时定位信息时才读取仓库文件；非空的显式 profiles/store
    # 会优先保留，保证测试和受管部署不会被本地样例文件悄然影响。
    if not resolved_profiles or resolved_epoch <= 0 or _is_default_edge_node_id(node_id):
        key_profiles_payload = _load_json_file(_pick_existing_path(DEFAULT_KEY_PROFILES, DEFAULT_KEY_PROFILES_FALLBACK))
        if not resolved_profiles:
            raw_profiles = key_profiles_payload.get("profiles")
            if isinstance(raw_profiles, list):
                resolved_profiles = [row for row in raw_profiles if isinstance(row, Mapping)]
        if resolved_epoch <= 0:
            resolved_epoch = _coerce_non_negative_int(key_profiles_payload.get("current_epoch"), default=0)

    resolved_node_id = _resolve_edge_node_id(
        env=env,
        node_id=node_id,
        key_profiles_payload=key_profiles_payload,
    )
    if not resolved_store:
        resolved_store = _load_json_file(_pick_existing_path(DEFAULT_SECRET_STORE, DEFAULT_SECRET_STORE_FALLBACK))
    return resolved_profiles, resolved_store, resolved_epoch, resolved_node_id


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
