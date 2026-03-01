"""Edge configuration contract for RPC capabilities and resource gate defaults."""

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

S_EDGE_CONFIG = "S_EDGE_CONFIG"


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
    allow_insecure_auth: bool
    rpc_gateway_enabled: bool
    asr_enabled: bool
    tts_enabled: bool
    barge_in_enabled: bool
    fallback_llm_enabled: bool
    resource_gate_enabled: bool
    max_vram_mb: int
    max_queue_length: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "env": self.env,
            "rpc_host": self.rpc_host,
            "rpc_port": self.rpc_port,
            "hmac_shared_key_set": bool(self.hmac_shared_key),
            "allow_insecure_auth": self.allow_insecure_auth,
            "rpc_gateway_enabled": self.rpc_gateway_enabled,
            "asr_enabled": self.asr_enabled,
            "tts_enabled": self.tts_enabled,
            "barge_in_enabled": self.barge_in_enabled,
            "fallback_llm_enabled": self.fallback_llm_enabled,
            "resource_gate_enabled": self.resource_gate_enabled,
            "max_vram_mb": self.max_vram_mb,
            "max_queue_length": self.max_queue_length,
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


def load_edge_config(
    *,
    env: Mapping[str, Any] | None = None,
    local_overrides: Mapping[str, Any] | None = None,
) -> EdgeConfigLoadResult:
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

    rpc_host = _as_text(merged.get("EDGE_RPC_HOST"), "127.0.0.1")
    if not rpc_host:
        issues.append(
            ConfigIssue(
                reason_code=CONFIG_VALUE_INVALID,
                detail="EDGE_RPC_HOST must be non-empty",
                field="EDGE_RPC_HOST",
            )
        )

    rpc_port = _parse_int(
        merged.get("EDGE_RPC_PORT"),
        default=17800,
        field_name="EDGE_RPC_PORT",
        min_value=1,
        max_value=65535,
        issues=issues,
    )
    max_vram_mb = _parse_int(
        merged.get("EDGE_MAX_VRAM_MB"),
        default=4096,
        field_name="EDGE_MAX_VRAM_MB",
        min_value=512,
        max_value=131072,
        issues=issues,
    )
    max_queue_length = _parse_int(
        merged.get("EDGE_MAX_QUEUE_LENGTH"),
        default=8,
        field_name="EDGE_MAX_QUEUE_LENGTH",
        min_value=1,
        max_value=1024,
        issues=issues,
    )

    allow_insecure_auth = _parse_bool(
        merged.get("EDGE_ALLOW_INSECURE_AUTH"),
        default=(runtime_env != ENV_PROD),
    )
    hmac_shared_key = _as_optional_text(merged.get("EDGE_HMAC_SHARED_KEY"))
    if runtime_env == ENV_PROD and not hmac_shared_key and not allow_insecure_auth:
        issues.append(
            ConfigIssue(
                reason_code=CONFIG_REQUIRED_MISSING,
                detail="EDGE_HMAC_SHARED_KEY required in prod when EDGE_ALLOW_INSECURE_AUTH=false",
                field="EDGE_HMAC_SHARED_KEY",
            )
        )

    rpc_gateway_enabled = _parse_bool(merged.get("EDGE_RPC_GATEWAY_ENABLED"), default=True)
    asr_enabled = _parse_bool(merged.get("EDGE_ASR_ENABLED"), default=True)
    tts_enabled = _parse_bool(merged.get("EDGE_TTS_ENABLED"), default=True)
    barge_in_enabled = _parse_bool(merged.get("EDGE_BARGE_IN_ENABLED"), default=True)
    fallback_llm_enabled = _parse_bool(merged.get("EDGE_FALLBACK_LLM_ENABLED"), default=False)
    resource_gate_enabled = _parse_bool(merged.get("EDGE_RESOURCE_GATE_ENABLED"), default=True)

    if issues:
        return _failure(issues)

    config = EdgeConfig(
        env=runtime_env,
        rpc_host=rpc_host,
        rpc_port=rpc_port,
        hmac_shared_key=hmac_shared_key,
        allow_insecure_auth=bool(allow_insecure_auth),
        rpc_gateway_enabled=bool(rpc_gateway_enabled),
        asr_enabled=bool(asr_enabled),
        tts_enabled=bool(tts_enabled),
        barge_in_enabled=bool(barge_in_enabled),
        fallback_llm_enabled=bool(fallback_llm_enabled),
        resource_gate_enabled=bool(resource_gate_enabled),
        max_vram_mb=max_vram_mb,
        max_queue_length=max_queue_length,
    )
    return EdgeConfigLoadResult(
        success=True,
        stage=S_EDGE_CONFIG,
        reason_code=None,
        config=config,
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
            "unavailable_kind": "cold_start",
            "detail": "edge fallback llm disabled by default",
        }
    statuses["edge.rpc.resource_gate"] = _service_status(
        enabled=config.resource_gate_enabled,
        detail="edge resource gate enabled",
        disabled_detail="edge resource gate disabled by config",
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

