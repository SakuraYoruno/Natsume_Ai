"""Host configuration contract for env/default/local layered loading."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

from host_brain.core.errors import (
    CONFIG_ENV_INVALID,
    CONFIG_REQUIRED_MISSING,
    CONFIG_VALUE_INVALID,
)


ENV_DEV = "dev"
ENV_TEST = "test"
ENV_PROD = "prod"
ALLOWED_ENVS = {ENV_DEV, ENV_TEST, ENV_PROD}

ALLOWED_LLM_PROVIDERS = {"openai", "mock", "disabled"}

S_HOST_CONFIG = "S_HOST_CONFIG"


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


def load_host_config(
    *,
    env: Mapping[str, Any] | None = None,
    local_overrides: Mapping[str, Any] | None = None,
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
    if llm_provider == "openai" and not openai_api_key and not cold_start_allowed:
        issues.append(
            ConfigIssue(
                reason_code=CONFIG_REQUIRED_MISSING,
                detail="OPENAI_API_KEY is required when HOST_COLD_START_ALLOWED=false",
                field="OPENAI_API_KEY",
            )
        )

    if issues:
        return _failure(issues)

    config = HostConfig(
        env=runtime_env,
        llm_provider=llm_provider,
        openai_api_key=openai_api_key,
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


def build_host_runtime_statuses(config: HostConfig) -> dict[str, dict[str, Any]]:
    statuses: dict[str, dict[str, Any]] = {}

    if config.llm_provider == "openai" and not config.openai_api_key:
        statuses["host.integrations.llm_gateway"] = {
            "available": False,
            "unavailable_kind": "cold_start",
            "detail": "OPENAI_API_KEY missing",
        }
    elif config.llm_provider == "disabled":
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

