"""Host configuration contract for env/default/local layered loading."""

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

PROVIDER_OPENAI = "openai"
PROVIDER_QWEN = "qwen"
PROVIDER_GEMINI = "gemini"
PROVIDER_DEEPSEEK = "deepseek"
PROVIDER_MOCK = "mock"
PROVIDER_DISABLED = "disabled"

ALLOWED_LLM_PROVIDERS = {
    PROVIDER_OPENAI,
    PROVIDER_QWEN,
    PROVIDER_GEMINI,
    PROVIDER_DEEPSEEK,
    PROVIDER_MOCK,
    PROVIDER_DISABLED,
}

S_HOST_CONFIG = "S_HOST_CONFIG"
HOST_SNAPSHOT_COMPONENT = "host"

HOST_FIELD_TO_ENV_KEY = {
    "env": "NATSUME_ENV",
    "llm_provider": "HOST_LLM_PROVIDER",
    "openai_api_key_set": "OPENAI_API_KEY",
    "openai_api_key": "OPENAI_API_KEY",
    "openai_api_key_ref": "OPENAI_API_KEY_REF",
    "qwen_api_key_set": "QWEN_API_KEY",
    "qwen_api_key": "QWEN_API_KEY",
    "qwen_api_key_ref": "QWEN_API_KEY_REF",
    "gemini_api_key_set": "GEMINI_API_KEY",
    "gemini_api_key": "GEMINI_API_KEY",
    "gemini_api_key_ref": "GEMINI_API_KEY_REF",
    "deepseek_api_key_set": "DEEPSEEK_API_KEY",
    "deepseek_api_key": "DEEPSEEK_API_KEY",
    "deepseek_api_key_ref": "DEEPSEEK_API_KEY_REF",
    "key_profile_status": "OPENAI_API_KEY_REF",
    "llm_timeout_ms": "HOST_LLM_TIMEOUT_MS",
    "llm_max_retries": "HOST_LLM_MAX_RETRIES",
    "cold_start_allowed": "HOST_COLD_START_ALLOWED",
    "enable_retrieval": "HOST_ENABLE_RETRIEVAL",
    "enable_vision": "HOST_ENABLE_VISION",
    "enable_edge_rpc": "HOST_ENABLE_EDGE_RPC",
}
HOST_SECRET_ENV_KEYS = {"OPENAI_API_KEY", "QWEN_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY"}


@dataclass(frozen=True)
class ConfigIssue:
    reason_code: str
    detail: str
    field: str | None = None


@dataclass(frozen=True)
class HostConfig:
    env: str
    llm_provider: str
    openai_api_key: str | None
    openai_api_key_ref: str | None
    qwen_api_key: str | None
    qwen_api_key_ref: str | None
    gemini_api_key: str | None
    gemini_api_key_ref: str | None
    deepseek_api_key: str | None
    deepseek_api_key_ref: str | None
    llm_api_key: str | None
    llm_api_key_ref: str | None
    key_profile_status: str | None
    llm_timeout_ms: int
    llm_max_retries: int
    cold_start_allowed: bool
    enable_retrieval: bool
    enable_vision: bool
    enable_edge_rpc: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "env": self.env,
            "llm_provider": self.llm_provider,
            "openai_api_key_set": bool(self.openai_api_key),
            "openai_api_key_ref": self.openai_api_key_ref,
            "qwen_api_key_set": bool(self.qwen_api_key),
            "qwen_api_key_ref": self.qwen_api_key_ref,
            "gemini_api_key_set": bool(self.gemini_api_key),
            "gemini_api_key_ref": self.gemini_api_key_ref,
            "deepseek_api_key_set": bool(self.deepseek_api_key),
            "deepseek_api_key_ref": self.deepseek_api_key_ref,
            "llm_api_key_set": bool(self.llm_api_key),
            "llm_api_key_ref": self.llm_api_key_ref,
            "key_profile_status": self.key_profile_status,
            "llm_timeout_ms": self.llm_timeout_ms,
            "llm_max_retries": self.llm_max_retries,
            "cold_start_allowed": self.cold_start_allowed,
            "enable_retrieval": self.enable_retrieval,
            "enable_vision": self.enable_vision,
            "enable_edge_rpc": self.enable_edge_rpc,
        }


@dataclass(frozen=True)
class HostConfigLoadResult:
    success: bool
    stage: str
    reason_code: str | None
    config: HostConfig | None
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


def load_host_config(
    *,
    env: Mapping[str, Any] | None = None,
    local_overrides: Mapping[str, Any] | None = None,
    key_profiles: Sequence[Mapping[str, Any]] | None = None,
    secret_store: Mapping[str, Any] | None = None,
    node_id: str = "host.default",
    current_epoch: int | None = None,
) -> HostConfigLoadResult:
    merged: dict[str, Any] = dict(env or os.environ)
    if local_overrides:
        merged.update(dict(local_overrides))

    issues: list[ConfigIssue] = []
    runtime_env = _as_text(merged.get("NATSUME_ENV"), ENV_DEV).lower()
    if runtime_env not in ALLOWED_ENVS:
        issues.append(
            ConfigIssue(
                reason_code=CONFIG_ENV_INVALID,
                detail=f"NATSUME_ENV must be one of {sorted(ALLOWED_ENVS)}, got {runtime_env}",
                field="NATSUME_ENV",
            )
        )
        return _failure(issues)

    llm_provider = _as_text(merged.get("HOST_LLM_PROVIDER"), "openai").lower()
    if llm_provider not in ALLOWED_LLM_PROVIDERS:
        issues.append(
            ConfigIssue(
                reason_code=CONFIG_VALUE_INVALID,
                detail=(
                    "HOST_LLM_PROVIDER must be one of "
                    f"{sorted(ALLOWED_LLM_PROVIDERS)}, got {llm_provider}"
                ),
                field="HOST_LLM_PROVIDER",
            )
        )

    llm_timeout_ms = _parse_int(
        merged.get("HOST_LLM_TIMEOUT_MS"),
        default=15_000,
        field_name="HOST_LLM_TIMEOUT_MS",
        min_value=100,
        max_value=120_000,
        issues=issues,
    )
    llm_max_retries = _parse_int(
        merged.get("HOST_LLM_MAX_RETRIES"),
        default=1,
        field_name="HOST_LLM_MAX_RETRIES",
        min_value=0,
        max_value=10,
        issues=issues,
    )
    cold_start_allowed = _parse_bool(
        merged.get("HOST_COLD_START_ALLOWED"),
        default=(runtime_env != ENV_PROD),
    )
    enable_retrieval = _parse_bool(
        merged.get("HOST_ENABLE_RETRIEVAL"),
        default=True,
    )
    enable_vision = _parse_bool(
        merged.get("HOST_ENABLE_VISION"),
        default=True,
    )
    enable_edge_rpc = _parse_bool(
        merged.get("HOST_ENABLE_EDGE_RPC"),
        default=True,
    )

    openai_api_key = _as_optional_text(merged.get("OPENAI_API_KEY"))
    openai_api_key_ref = _as_optional_text(merged.get("OPENAI_API_KEY_REF"))
    qwen_api_key = _as_optional_text(merged.get("QWEN_API_KEY"))
    qwen_api_key_ref = _as_optional_text(merged.get("QWEN_API_KEY_REF"))
    gemini_api_key = _as_optional_text(merged.get("GEMINI_API_KEY"))
    gemini_api_key_ref = _as_optional_text(merged.get("GEMINI_API_KEY_REF"))
    deepseek_api_key = _as_optional_text(merged.get("DEEPSEEK_API_KEY"))
    deepseek_api_key_ref = _as_optional_text(merged.get("DEEPSEEK_API_KEY_REF"))
    llm_api_key: str | None = None
    llm_api_key_ref: str | None = None
    key_profile_status: str | None = None

    provider_to_runtime_keys: dict[str, tuple[str | None, str | None, str, str]] = {
        PROVIDER_OPENAI: (openai_api_key, openai_api_key_ref, "OPENAI_API_KEY", "OPENAI_API_KEY_REF"),
        PROVIDER_QWEN: (qwen_api_key, qwen_api_key_ref, "QWEN_API_KEY", "QWEN_API_KEY_REF"),
        PROVIDER_GEMINI: (gemini_api_key, gemini_api_key_ref, "GEMINI_API_KEY", "GEMINI_API_KEY_REF"),
        PROVIDER_DEEPSEEK: (deepseek_api_key, deepseek_api_key_ref, "DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY_REF"),
    }
    if llm_provider in provider_to_runtime_keys:
        llm_api_key, llm_api_key_ref, llm_key_env_name, llm_ref_env_name = provider_to_runtime_keys[llm_provider]
    else:
        llm_api_key, llm_api_key_ref, llm_key_env_name, llm_ref_env_name = (None, None, "", "")

    if llm_provider in {PROVIDER_OPENAI, PROVIDER_QWEN, PROVIDER_GEMINI, PROVIDER_DEEPSEEK} and not llm_api_key and llm_api_key_ref:
        resolved = resolve_secret_from_profiles(
            provider=llm_provider,
            node_id=str(node_id),
            scope="host.llm",
            key_profiles=key_profiles,
            secret_store=secret_store,
            current_epoch=int(current_epoch if current_epoch is not None else 0),
        )
        if resolved.success:
            llm_api_key = resolved.secret_value
            llm_api_key_ref = resolved.secret_ref or llm_api_key_ref
            key_profile_status = "resolved"
        else:
            key_profile_status = "degraded"
            if not cold_start_allowed:
                issues.append(
                    ConfigIssue(
                        reason_code=str(resolved.reason_code or CONFIG_REQUIRED_MISSING),
                        detail=str(resolved.detail),
                        field=llm_ref_env_name,
                    )
                )

    if llm_provider == PROVIDER_OPENAI:
        openai_api_key = llm_api_key
        openai_api_key_ref = llm_api_key_ref
    elif llm_provider == PROVIDER_QWEN:
        qwen_api_key = llm_api_key
        qwen_api_key_ref = llm_api_key_ref
    elif llm_provider == PROVIDER_GEMINI:
        gemini_api_key = llm_api_key
        gemini_api_key_ref = llm_api_key_ref
    elif llm_provider == PROVIDER_DEEPSEEK:
        deepseek_api_key = llm_api_key
        deepseek_api_key_ref = llm_api_key_ref

    if llm_provider in {PROVIDER_OPENAI, PROVIDER_QWEN, PROVIDER_GEMINI, PROVIDER_DEEPSEEK} and not llm_api_key and not cold_start_allowed:
        missing_detail = f"{llm_key_env_name} is required when HOST_COLD_START_ALLOWED=false"
        issues.append(
            ConfigIssue(
                reason_code=CONFIG_REQUIRED_MISSING,
                detail=missing_detail,
                field=llm_key_env_name,
            )
        )

    if issues:
        return _failure(issues)

    config = HostConfig(
        env=runtime_env,
        llm_provider=llm_provider,
        openai_api_key=openai_api_key,
        openai_api_key_ref=openai_api_key_ref,
        qwen_api_key=qwen_api_key,
        qwen_api_key_ref=qwen_api_key_ref,
        gemini_api_key=gemini_api_key,
        gemini_api_key_ref=gemini_api_key_ref,
        deepseek_api_key=deepseek_api_key,
        deepseek_api_key_ref=deepseek_api_key_ref,
        llm_api_key=llm_api_key,
        llm_api_key_ref=llm_api_key_ref,
        key_profile_status=key_profile_status,
        llm_timeout_ms=llm_timeout_ms,
        llm_max_retries=llm_max_retries,
        cold_start_allowed=bool(cold_start_allowed),
        enable_retrieval=bool(enable_retrieval),
        enable_vision=bool(enable_vision),
        enable_edge_rpc=bool(enable_edge_rpc),
    )
    return HostConfigLoadResult(
        success=True,
        stage=S_HOST_CONFIG,
        reason_code=None,
        config=config,
        issues=(),
    )


def export_host_config_snapshot(
    *,
    env: Mapping[str, Any] | None = None,
    local_overrides: Mapping[str, Any] | None = None,
    key_profiles: Sequence[Mapping[str, Any]] | None = None,
    secret_store: Mapping[str, Any] | None = None,
    node_id: str = "host.default",
    current_epoch: int | None = None,
) -> ConfigSnapshotExportResult:
    env_layer = dict(env or os.environ)
    overrides = dict(local_overrides or {})

    load_result = load_host_config(
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
    values["openai_api_key"] = redact_secret(config.openai_api_key)
    values["qwen_api_key"] = redact_secret(config.qwen_api_key)
    values["gemini_api_key"] = redact_secret(config.gemini_api_key)
    values["deepseek_api_key"] = redact_secret(config.deepseek_api_key)
    values["llm_api_key"] = redact_secret(config.llm_api_key)

    redacted_keys = collect_redacted_keys(
        secret_env_keys=HOST_SECRET_ENV_KEYS,
        env_layer=env_layer,
        local_overrides=overrides,
    )
    if config.openai_api_key and "OPENAI_API_KEY" not in redacted_keys:
        redacted_keys.append("OPENAI_API_KEY")
    if config.qwen_api_key and "QWEN_API_KEY" not in redacted_keys:
        redacted_keys.append("QWEN_API_KEY")
    if config.gemini_api_key and "GEMINI_API_KEY" not in redacted_keys:
        redacted_keys.append("GEMINI_API_KEY")
    if config.deepseek_api_key and "DEEPSEEK_API_KEY" not in redacted_keys:
        redacted_keys.append("DEEPSEEK_API_KEY")
    redacted_keys = sorted(redacted_keys)

    snapshot = build_config_snapshot(
        component=HOST_SNAPSHOT_COMPONENT,
        env=config.env,
        values=values,
        override_sources=resolve_override_sources(
            field_to_env_key=HOST_FIELD_TO_ENV_KEY,
            env_layer=env_layer,
            local_overrides=overrides,
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


def build_host_runtime_statuses(config: HostConfig) -> dict[str, dict[str, Any]]:
    statuses: dict[str, dict[str, Any]] = {}

    provider_to_env_key = {
        PROVIDER_OPENAI: "OPENAI_API_KEY",
        PROVIDER_QWEN: "QWEN_API_KEY",
        PROVIDER_GEMINI: "GEMINI_API_KEY",
        PROVIDER_DEEPSEEK: "DEEPSEEK_API_KEY",
    }
    if config.llm_provider in provider_to_env_key and not config.llm_api_key:
        missing_key_name = provider_to_env_key[config.llm_provider]
        statuses["host.integrations.llm_gateway"] = {
            "available": False,
            "unavailable_kind": "cold_start",
            "detail": f"{missing_key_name} missing",
        }
    elif config.llm_provider == PROVIDER_DISABLED:
        statuses["host.integrations.llm_gateway"] = {
            "available": False,
            "unavailable_kind": "dependency",
            "detail": "HOST_LLM_PROVIDER disabled",
        }
    else:
        statuses["host.integrations.llm_gateway"] = {
            "available": True,
            "detail": "llm gateway enabled",
        }

    if not config.enable_retrieval:
        retrieval_down = {
            "available": False,
            "unavailable_kind": "dependency",
            "detail": "retrieval disabled by config",
        }
        statuses["host.retrieval.embedding"] = dict(retrieval_down)
        statuses["host.retrieval.reranker"] = dict(retrieval_down)

    if not config.enable_vision:
        vision_down = {
            "available": False,
            "unavailable_kind": "dependency",
            "detail": "vision disabled by config",
        }
        statuses["host.vision.screen_ocr"] = dict(vision_down)
        statuses["host.vision.camera_recognizer"] = dict(vision_down)

    return statuses


def _failure(issues: list[ConfigIssue]) -> HostConfigLoadResult:
    primary = issues[0] if issues else None
    return HostConfigLoadResult(
        success=False,
        stage=S_HOST_CONFIG,
        reason_code=(primary.reason_code if primary else CONFIG_VALUE_INVALID),
        config=None,
        issues=tuple(issues),
    )


def _as_text(value: Any, default: str) -> str:
    text = str(value or "").strip()
    return text if text else str(default)


def _as_optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if text else None


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
