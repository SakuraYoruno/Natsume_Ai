"""Host configuration contract for env/default/local layered loading."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
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
PROVIDER_CODEX = "codex"
PROVIDER_GEMINI = "gemini"
PROVIDER_DEEPSEEK = "deepseek"
PROVIDER_MINIMAX = "minimax"
PROVIDER_MOCK = "mock"
PROVIDER_DISABLED = "disabled"

ALLOWED_LLM_PROVIDERS = {
    PROVIDER_OPENAI,
    PROVIDER_CODEX,
    PROVIDER_GEMINI,
    PROVIDER_DEEPSEEK,
    PROVIDER_MINIMAX,
    PROVIDER_MOCK,
    PROVIDER_DISABLED,
}

S_HOST_CONFIG = "S_HOST_CONFIG"
HOST_SNAPSHOT_COMPONENT = "host"
DEFAULT_KEY_PROFILES = Path("configs/secrets/key_profiles.runtime.json")
DEFAULT_KEY_PROFILES_FALLBACK = Path("configs/secrets/key_profiles.example.json")
DEFAULT_SECRET_STORE = Path("configs/secrets/secret_store.runtime.local.json")
DEFAULT_SECRET_STORE_FALLBACK = Path("configs/secrets/secret_store.runtime.example.json")
DEFAULT_HOST_NODE_ID = "natsu-node-01"
DEFAULT_HOST_NODE_ID_SENTINEL = "host.default"

HOST_FIELD_TO_ENV_KEY = {
    "env": "Natsume_ENV",
    "llm_provider": "HOST_LLM_PROVIDER",
    "openai_api_key_set": "OPENAI_API_KEY",
    "openai_api_key": "OPENAI_API_KEY",
    "openai_api_key_ref": "OPENAI_API_KEY_REF",
    "codex_api_key_set": "CODEX_API_KEY",
    "codex_api_key": "CODEX_API_KEY",
    "codex_api_key_ref": "CODEX_API_KEY_REF",
    "gemini_api_key_set": "GEMINI_API_KEY",
    "gemini_api_key": "GEMINI_API_KEY",
    "gemini_api_key_ref": "GEMINI_API_KEY_REF",
    "deepseek_api_key_set": "DEEPSEEK_API_KEY",
    "deepseek_api_key": "DEEPSEEK_API_KEY",
    "deepseek_api_key_ref": "DEEPSEEK_API_KEY_REF",
    "minimax_api_key_set": "MINIMAX_API_KEY",
    "minimax_api_key": "MINIMAX_API_KEY",
    "minimax_api_key_ref": "MINIMAX_API_KEY_REF",
    "key_profile_status": "OPENAI_API_KEY_REF",
    "llm_timeout_ms": "HOST_LLM_TIMEOUT_MS",
    "llm_max_retries": "HOST_LLM_MAX_RETRIES",
    "cold_start_allowed": "HOST_COLD_START_ALLOWED",
    "enable_retrieval": "HOST_ENABLE_RETRIEVAL",
    "enable_vision": "HOST_ENABLE_VISION",
    "enable_edge_rpc": "HOST_ENABLE_EDGE_RPC",
    "rate_limit_backend": "HOST_RATE_LIMIT_BACKEND",
    "rate_limit_window_ms": "HOST_RATE_LIMIT_WINDOW_MS",
    "rate_limit_max_requests": "HOST_RATE_LIMIT_MAX_REQUESTS",
}
HOST_SECRET_ENV_KEYS = {"OPENAI_API_KEY", "CODEX_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY", "MINIMAX_API_KEY"}


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
    codex_api_key: str | None
    codex_api_key_ref: str | None
    gemini_api_key: str | None
    gemini_api_key_ref: str | None
    deepseek_api_key: str | None
    deepseek_api_key_ref: str | None
    minimax_api_key: str | None
    minimax_api_key_ref: str | None
    llm_api_key: str | None
    llm_api_key_ref: str | None
    key_profile_status: str | None
    llm_timeout_ms: int
    llm_max_retries: int
    cold_start_allowed: bool
    enable_retrieval: bool
    enable_vision: bool
    enable_edge_rpc: bool
    rate_limit_backend: str
    rate_limit_window_ms: int
    rate_limit_max_requests: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "env": self.env,
            "llm_provider": self.llm_provider,
            "openai_api_key_set": bool(self.openai_api_key),
            "openai_api_key_ref": self.openai_api_key_ref,
            "codex_api_key_set": bool(self.codex_api_key),
            "codex_api_key_ref": self.codex_api_key_ref,
            "gemini_api_key_set": bool(self.gemini_api_key),
            "gemini_api_key_ref": self.gemini_api_key_ref,
            "deepseek_api_key_set": bool(self.deepseek_api_key),
            "deepseek_api_key_ref": self.deepseek_api_key_ref,
            "minimax_api_key_set": bool(self.minimax_api_key),
            "minimax_api_key_ref": self.minimax_api_key_ref,
            "llm_api_key_set": bool(self.llm_api_key),
            "llm_api_key_ref": self.llm_api_key_ref,
            "key_profile_status": self.key_profile_status,
            "llm_timeout_ms": self.llm_timeout_ms,
            "llm_max_retries": self.llm_max_retries,
            "cold_start_allowed": self.cold_start_allowed,
            "enable_retrieval": self.enable_retrieval,
            "enable_vision": self.enable_vision,
            "enable_edge_rpc": self.enable_edge_rpc,
            "rate_limit_backend": self.rate_limit_backend,
            "rate_limit_window_ms": self.rate_limit_window_ms,
            "rate_limit_max_requests": self.rate_limit_max_requests,
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
    resolved_key_profiles, resolved_secret_store, resolved_epoch, resolved_node_id = _resolve_runtime_key_materials(
        env=merged,
        key_profiles=key_profiles,
        secret_store=secret_store,
        node_id=node_id,
        current_epoch=current_epoch,
    )

    issues: list[ConfigIssue] = []
    runtime_env = _as_text(merged.get("Natsume_ENV"), ENV_DEV).lower()
    if runtime_env not in ALLOWED_ENVS:
        issues.append(
            ConfigIssue(
                reason_code=CONFIG_ENV_INVALID,
                detail=f"Natsume_ENV must be one of {sorted(ALLOWED_ENVS)}, got {runtime_env}",
                field="Natsume_ENV",
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
    rate_limit_backend = _as_text(
        merged.get("HOST_RATE_LIMIT_BACKEND"), "memory",
    ).lower()
    if rate_limit_backend not in {"memory", "sqlite", "redis"}:
        # 无效值静默降级到 memory（不阻断配置加载）
        rate_limit_backend = "memory"
    rate_limit_window_ms = _parse_int(
        merged.get("HOST_RATE_LIMIT_WINDOW_MS"),
        default=60_000,
        field_name="HOST_RATE_LIMIT_WINDOW_MS",
        min_value=1_000,
        max_value=600_000,
        issues=issues,
    )
    rate_limit_max_requests = _parse_int(
        merged.get("HOST_RATE_LIMIT_MAX_REQUESTS"),
        default=30,
        field_name="HOST_RATE_LIMIT_MAX_REQUESTS",
        min_value=1,
        max_value=10_000,
        issues=issues,
    )

    openai_api_key = _as_optional_text(merged.get("OPENAI_API_KEY"))
    openai_api_key_ref = _as_optional_text(merged.get("OPENAI_API_KEY_REF"))
    codex_api_key = _as_optional_text(merged.get("CODEX_API_KEY"))
    codex_api_key_ref = _as_optional_text(merged.get("CODEX_API_KEY_REF"))
    gemini_api_key = _as_optional_text(merged.get("GEMINI_API_KEY"))
    gemini_api_key_ref = _as_optional_text(merged.get("GEMINI_API_KEY_REF"))
    deepseek_api_key = _as_optional_text(merged.get("DEEPSEEK_API_KEY"))
    deepseek_api_key_ref = _as_optional_text(merged.get("DEEPSEEK_API_KEY_REF"))
    minimax_api_key = _as_optional_text(merged.get("MINIMAX_API_KEY"))
    minimax_api_key_ref = _as_optional_text(merged.get("MINIMAX_API_KEY_REF"))
    llm_api_key: str | None = None
    llm_api_key_ref: str | None = None
    key_profile_status: str | None = None

    provider_to_runtime_keys: dict[str, tuple[str | None, str | None, str, str]] = {
        PROVIDER_OPENAI: (openai_api_key, openai_api_key_ref, "OPENAI_API_KEY", "OPENAI_API_KEY_REF"),
        PROVIDER_CODEX: (codex_api_key, codex_api_key_ref, "CODEX_API_KEY", "CODEX_API_KEY_REF"),
        PROVIDER_GEMINI: (gemini_api_key, gemini_api_key_ref, "GEMINI_API_KEY", "GEMINI_API_KEY_REF"),
        PROVIDER_DEEPSEEK: (deepseek_api_key, deepseek_api_key_ref, "DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY_REF"),
        PROVIDER_MINIMAX: (minimax_api_key, minimax_api_key_ref, "MINIMAX_API_KEY", "MINIMAX_API_KEY_REF"),
    }
    if llm_provider in provider_to_runtime_keys:
        llm_api_key, llm_api_key_ref, llm_key_env_name, llm_ref_env_name = provider_to_runtime_keys[llm_provider]
    else:
        llm_api_key, llm_api_key_ref, llm_key_env_name, llm_ref_env_name = (None, None, "", "")

    if llm_provider in {PROVIDER_OPENAI, PROVIDER_CODEX, PROVIDER_GEMINI, PROVIDER_DEEPSEEK, PROVIDER_MINIMAX} and not llm_api_key and llm_api_key_ref:
        resolved = resolve_secret_from_profiles(
            provider=llm_provider,
            node_id=resolved_node_id,
            scope="host.llm",
            key_profiles=resolved_key_profiles,
            secret_store=resolved_secret_store,
            current_epoch=resolved_epoch,
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
    elif llm_provider == PROVIDER_CODEX:
        codex_api_key = llm_api_key
        codex_api_key_ref = llm_api_key_ref
    elif llm_provider == PROVIDER_GEMINI:
        gemini_api_key = llm_api_key
        gemini_api_key_ref = llm_api_key_ref
    elif llm_provider == PROVIDER_DEEPSEEK:
        deepseek_api_key = llm_api_key
        deepseek_api_key_ref = llm_api_key_ref
    elif llm_provider == PROVIDER_MINIMAX:
        minimax_api_key = llm_api_key
        minimax_api_key_ref = llm_api_key_ref

    if llm_provider in {PROVIDER_OPENAI, PROVIDER_CODEX, PROVIDER_GEMINI, PROVIDER_DEEPSEEK, PROVIDER_MINIMAX} and not llm_api_key and not cold_start_allowed:
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

    if env is None:
        _inject_resolved_llm_key(
            provider=llm_provider,
            api_key=llm_api_key,
        )

    config = HostConfig(
        env=runtime_env,
        llm_provider=llm_provider,
        openai_api_key=openai_api_key,
        openai_api_key_ref=openai_api_key_ref,
        codex_api_key=codex_api_key,
        codex_api_key_ref=codex_api_key_ref,
        gemini_api_key=gemini_api_key,
        gemini_api_key_ref=gemini_api_key_ref,
        deepseek_api_key=deepseek_api_key,
        deepseek_api_key_ref=deepseek_api_key_ref,
        minimax_api_key=minimax_api_key,
        minimax_api_key_ref=minimax_api_key_ref,
        llm_api_key=llm_api_key,
        llm_api_key_ref=llm_api_key_ref,
        key_profile_status=key_profile_status,
        llm_timeout_ms=llm_timeout_ms,
        llm_max_retries=llm_max_retries,
        cold_start_allowed=bool(cold_start_allowed),
        enable_retrieval=bool(enable_retrieval),
        enable_vision=bool(enable_vision),
        enable_edge_rpc=bool(enable_edge_rpc),
        rate_limit_backend=rate_limit_backend,
        rate_limit_window_ms=int(rate_limit_window_ms),
        rate_limit_max_requests=int(rate_limit_max_requests),
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
    values["codex_api_key"] = redact_secret(config.codex_api_key)
    values["gemini_api_key"] = redact_secret(config.gemini_api_key)
    values["deepseek_api_key"] = redact_secret(config.deepseek_api_key)
    values["minimax_api_key"] = redact_secret(config.minimax_api_key)
    values["llm_api_key"] = redact_secret(config.llm_api_key)

    redacted_keys = collect_redacted_keys(
        secret_env_keys=HOST_SECRET_ENV_KEYS,
        env_layer=env_layer,
        local_overrides=overrides,
    )
    if config.openai_api_key and "OPENAI_API_KEY" not in redacted_keys:
        redacted_keys.append("OPENAI_API_KEY")
    if config.codex_api_key and "CODEX_API_KEY" not in redacted_keys:
        redacted_keys.append("CODEX_API_KEY")
    if config.gemini_api_key and "GEMINI_API_KEY" not in redacted_keys:
        redacted_keys.append("GEMINI_API_KEY")
    if config.deepseek_api_key and "DEEPSEEK_API_KEY" not in redacted_keys:
        redacted_keys.append("DEEPSEEK_API_KEY")
    if config.minimax_api_key and "MINIMAX_API_KEY" not in redacted_keys:
        redacted_keys.append("MINIMAX_API_KEY")
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

    # --- host.audit.sqlite_ledger ---
    # 通过 Natsume_AUDIT_DB 环境变量判断账本路径是否可访问
    import os as _os
    audit_db_path = str(_os.environ.get("Natsume_AUDIT_DB") or "host_brain/data/audit.sqlite3").strip()
    if audit_db_path == ":memory:":
        statuses["host.audit.sqlite_ledger"] = {
            "available": True,
            "detail": "sqlite ledger using in-memory db (test mode)",
        }
    else:
        parent_dir = _os.path.dirname(_os.path.abspath(audit_db_path))
        if _os.path.exists(audit_db_path) or _os.path.isdir(parent_dir):
            statuses["host.audit.sqlite_ledger"] = {
                "available": True,
                "detail": f"sqlite ledger path accessible: {audit_db_path}",
            }
        else:
            statuses["host.audit.sqlite_ledger"] = {
                "available": False,
                "unavailable_kind": "dependency",
                "detail": f"sqlite ledger parent dir not found: {parent_dir}",
            }

    # --- host.governor.decision ---
    # Governor 依赖 LLM provider 配置，provider=disabled 时裁决能力降级
    if config.llm_provider == PROVIDER_DISABLED:
        statuses["host.governor.decision"] = {
            "available": False,
            "unavailable_kind": "dependency",
            "detail": "governor degraded: HOST_LLM_PROVIDER disabled",
        }
    else:
        statuses["host.governor.decision"] = {
            "available": True,
            "detail": "governor decision engine ready",
        }

    # --- host.skills.loader ---
    # Skills loader 依赖 env 配置正常加载，env 合法即可用
    statuses["host.skills.loader"] = {
        "available": True,
        "detail": f"skills loader ready (env={config.env})",
    }

    # --- host.integrations.llm_gateway ---
    provider_to_env_key = {
        PROVIDER_OPENAI: "OPENAI_API_KEY",
        PROVIDER_CODEX: "CODEX_API_KEY",
        PROVIDER_GEMINI: "GEMINI_API_KEY",
        PROVIDER_DEEPSEEK: "DEEPSEEK_API_KEY",
        PROVIDER_MINIMAX: "MINIMAX_API_KEY",
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

    # --- host.retrieval.recall_fusion ---
    if not config.enable_retrieval:
        retrieval_down = {
            "available": False,
            "unavailable_kind": "dependency",
            "detail": "retrieval disabled by config",
        }
        statuses["host.retrieval.recall_fusion"] = dict(retrieval_down)
    else:
        statuses["host.retrieval.recall_fusion"] = {
            "available": True,
            "detail": "surface recall fusion enabled",
        }

    # --- host.vision.screen_ocr / host.vision.camera_recognizer ---
    if not config.enable_vision:
        vision_down = {
            "available": False,
            "unavailable_kind": "dependency",
            "detail": "vision disabled by config",
        }
        statuses["host.vision.screen_ocr"] = dict(vision_down)
        statuses["host.vision.camera_recognizer"] = dict(vision_down)
    else:
        statuses["host.vision.screen_ocr"] = {
            "available": True,
            "detail": "vision screen_ocr enabled",
        }
        statuses["host.vision.camera_recognizer"] = {
            "available": True,
            "detail": "vision camera_recognizer enabled",
        }

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

    if not resolved_profiles or resolved_epoch <= 0 or _is_default_node_id(node_id):
        key_profiles_payload = _load_json_file(
            _pick_existing_path(DEFAULT_KEY_PROFILES, DEFAULT_KEY_PROFILES_FALLBACK),
        )
        if not resolved_profiles:
            raw_profiles = key_profiles_payload.get("profiles")
            if isinstance(raw_profiles, list):
                resolved_profiles = [row for row in raw_profiles if isinstance(row, Mapping)]
        if resolved_epoch <= 0:
            resolved_epoch = _coerce_non_negative_int(
                key_profiles_payload.get("current_epoch"),
                default=0,
            )

    resolved_node_id = _resolve_runtime_node_id(
        env=env,
        node_id=node_id,
        key_profiles_payload=key_profiles_payload,
    )
    if not resolved_store:
        resolved_store = _load_json_file(
            _pick_existing_path(DEFAULT_SECRET_STORE, DEFAULT_SECRET_STORE_FALLBACK),
        )
    return resolved_profiles, resolved_store, resolved_epoch, resolved_node_id


def _resolve_runtime_node_id(
    *,
    env: Mapping[str, Any],
    node_id: str | None,
    key_profiles_payload: Mapping[str, Any],
) -> str:
    explicit = str(node_id or "").strip()
    if explicit and not _is_default_node_id(explicit):
        return explicit
    from_env = str(
        env.get("HOST_RETR_NODE_ID")
        or env.get("Natsume_HOST_NODE_ID")
        or env.get("HOST_NODE_ID")
        or "",
    ).strip()
    if from_env:
        return from_env
    from_profiles = str(key_profiles_payload.get("host_node_id") or "").strip()
    if from_profiles:
        return from_profiles
    return DEFAULT_HOST_NODE_ID


def _inject_resolved_llm_key(*, provider: str, api_key: str | None) -> None:
    key_value = str(api_key or "").strip()
    if not key_value:
        return
    provider_to_env_key = {
        PROVIDER_OPENAI: "OPENAI_API_KEY",
        PROVIDER_CODEX: "CODEX_API_KEY",
        PROVIDER_GEMINI: "GEMINI_API_KEY",
        PROVIDER_DEEPSEEK: "DEEPSEEK_API_KEY",
        PROVIDER_MINIMAX: "MINIMAX_API_KEY",
    }
    env_key = provider_to_env_key.get(str(provider or "").strip().lower())
    if not env_key:
        return
    if str(os.environ.get(env_key) or "").strip():
        return
    os.environ[env_key] = key_value


def _is_default_node_id(value: str | None) -> bool:
    text = str(value or "").strip().lower()
    return not text or text == DEFAULT_HOST_NODE_ID_SENTINEL


def _coerce_non_negative_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return int(default)
    return max(0, parsed)


def _pick_existing_path(primary: Path, fallback: Path) -> Path:
    if primary.exists() and primary.is_file():
        return primary
    return fallback


def _load_json_file(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    for encoding in ("utf-8", "utf-8-sig"):
        try:
            payload = json.loads(path.read_text(encoding=encoding))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return payload
    return {}
