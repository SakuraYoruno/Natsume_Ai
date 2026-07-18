"""CoreSettings——最小必需配置集合（OPT-4）

覆盖：
- 环境标识（dev/test/prod）
- 数据目录（SQLite / Chroma / Neo4j 本地数据根）
- 日志级别
- Host HTTP 监听端点

这些是任何部署都必须先确定的"冷启动参数"。其他域（LLM/Tribe/Observability）
允许在启动后运行时协商，但 Core 不行。
"""

from __future__ import annotations

import base64
import binascii
from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .base import SettingsFieldReport, inspect_setting

LogLevel = Literal["DEBUG", "INFO", "WARN", "WARNING", "ERROR"]
RuntimeEnv = Literal["dev", "test", "prod"]

_RECONCILIATION_PUBLIC_KEY_PREFIX = "ed25519:"
_RECONCILIATION_PUBLIC_KEY_VALUE_PATTERN = re.compile(
    r"^[A-Za-z0-9_-]{43}$"
)
_RECONCILIATION_KEY_ID_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
)
_MAX_RECONCILIATION_PUBLIC_KEYS = 64


def _reject_duplicate_json_object_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    parsed: dict[str, object] = {}
    for key, value in pairs:
        if key in parsed:
            raise ValueError(f"duplicate key_id {key!r}")
        parsed[key] = value
    return parsed


def parse_task_reconciliation_public_keys(raw: str) -> dict[str, bytes]:
    """Parse one strict, rotation-capable Ed25519 public-key ring.

    The environment contract is a JSON object whose values use the canonical
    ``ed25519:<unpadded-base64url>`` representation.  Only raw 32-byte public
    keys are returned; this boundary intentionally has no private-key or
    signing-key format.
    """

    supplied = str(raw or "").strip()
    if not supplied:
        return {}
    try:
        parsed = json.loads(
            supplied,
            object_pairs_hook=_reject_duplicate_json_object_keys,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(
            "TASK_RECONCILIATION_PUBLIC_KEYS_INVALID: expected unique-key JSON object"
        ) from exc
    if not isinstance(parsed, dict):
        raise ValueError(
            "TASK_RECONCILIATION_PUBLIC_KEYS_INVALID: top-level value must be an object"
        )
    if len(parsed) > _MAX_RECONCILIATION_PUBLIC_KEYS:
        raise ValueError(
            "TASK_RECONCILIATION_PUBLIC_KEYS_INVALID: too many active public keys"
        )

    public_keys: dict[str, bytes] = {}
    seen_key_material: set[bytes] = set()
    for key_id, value in parsed.items():
        if not _RECONCILIATION_KEY_ID_PATTERN.fullmatch(key_id):
            raise ValueError(
                "TASK_RECONCILIATION_PUBLIC_KEYS_INVALID: invalid key_id"
            )
        if not isinstance(value, str) or not value.startswith(
            _RECONCILIATION_PUBLIC_KEY_PREFIX
        ):
            raise ValueError(
                "TASK_RECONCILIATION_PUBLIC_KEYS_INVALID: "
                "public key must use ed25519:<unpadded-base64url>"
            )
        encoded = value.removeprefix(_RECONCILIATION_PUBLIC_KEY_PREFIX)
        if not _RECONCILIATION_PUBLIC_KEY_VALUE_PATTERN.fullmatch(encoded):
            raise ValueError(
                "TASK_RECONCILIATION_PUBLIC_KEYS_INVALID: "
                "Ed25519 public key encoding is not canonical"
            )
        try:
            key_bytes = base64.b64decode(
                f"{encoded}=",
                altchars=b"-_",
                validate=True,
            )
        except (ValueError, binascii.Error) as exc:
            raise ValueError(
                "TASK_RECONCILIATION_PUBLIC_KEYS_INVALID: invalid base64url public key"
            ) from exc
        if len(key_bytes) != 32 or (
            base64.urlsafe_b64encode(key_bytes).decode("ascii").rstrip("=")
            != encoded
        ):
            raise ValueError(
                "TASK_RECONCILIATION_PUBLIC_KEYS_INVALID: "
                "Ed25519 public key must contain exactly 32 bytes"
            )
        if key_bytes in seen_key_material:
            raise ValueError(
                "TASK_RECONCILIATION_PUBLIC_KEYS_INVALID: "
                "duplicate Ed25519 public key material"
            )
        seen_key_material.add(key_bytes)
        public_keys[key_id] = key_bytes
    return public_keys


class CoreSettings(BaseSettings):
    """最小必需的运行时配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    env: RuntimeEnv = Field(default="dev", alias="Natsume_ENV")
    data_dir: Path = Field(default=Path("./data"), alias="NATSUME_DATA_DIR")
    log_level: LogLevel = Field(default="INFO", alias="HOST_APP_LOG_LEVEL")
    host_app_host: str = Field(default="127.0.0.1", alias="HOST_APP_HOST")
    host_app_port: int = Field(default=18080, ge=1, le=65535, alias="HOST_APP_PORT")
    uvicorn_access_log_enabled: bool = Field(
        default=False,
        alias="HOST_UVICORN_ACCESS_LOG_ENABLED",
    )
    startup_eager_warmup: bool = Field(
        default=False,
        alias="HOST_STARTUP_EAGER_WARMUP",
    )
    resource_manager_enabled: bool = Field(
        default=False,
        alias="HOST_RESOURCE_MANAGER_ENABLED",
    )
    resource_manager_run_on_startup: bool = Field(
        default=True,
        alias="HOST_RESOURCE_MANAGER_RUN_ON_STARTUP",
    )

    # OPT-4 第三批：运行时特性开关 / 调度参数（原散落在各 app 模块里）
    prefrontal_plan_enabled: bool = Field(
        default=False, alias="PREFRONTAL_PLAN_ENABLED"
    )
    skill_dag_parallel_window: int = Field(
        default=4, ge=1, le=16, alias="HOST_SKILL_DAG_PARALLEL_WINDOW"
    )
    task_runtime_canonical_owner: str = Field(
        default="", alias="HOST_TASK_RUNTIME_CANONICAL_OWNER"
    )
    task_runtime_bounded_fanout_enabled: bool = Field(
        default=False,
        alias="HOST_TASK_RUNTIME_BOUNDED_FANOUT_ENABLED",
    )
    task_runtime_atomic_graph_success_enabled: bool = Field(
        default=False,
        alias="HOST_TASK_RUNTIME_ATOMIC_GRAPH_SUCCESS_ENABLED",
    )
    task_runtime_canary_receipt: str = Field(
        default="",
        alias="HOST_TASK_RUNTIME_CANARY_RECEIPT",
    )
    agent_workspace_managed_root: Path = Field(
        default_factory=lambda: Path.home() / ".natsume" / "agent_worktrees",
        alias="HOST_AGENT_WORKSPACE_MANAGED_ROOT",
    )
    agent_workspace_lease_ms: int = Field(
        default=120_000,
        ge=1_000,
        le=86_400_000,
        alias="HOST_AGENT_WORKSPACE_LEASE_MS",
    )
    agent_workspace_heartbeat_ms: int = Field(
        default=30_000,
        ge=100,
        le=86_399_999,
        alias="HOST_AGENT_WORKSPACE_HEARTBEAT_MS",
    )
    agent_workspace_cleanup_poll_ms: int = Field(
        default=5_000,
        ge=100,
        le=86_400_000,
        alias="HOST_AGENT_WORKSPACE_CLEANUP_POLL_MS",
    )
    agent_workspace_hmac_secret: SecretStr = Field(
        default=SecretStr(""),
        alias="HOST_AGENT_WORKSPACE_HMAC_SECRET",
    )
    task_graph_cancellation_operator_enabled: bool = Field(
        default=False,
        alias="HOST_TASK_GRAPH_CANCELLATION_OPERATOR_ENABLED",
    )
    task_operation_reconciliation_operator_enabled: bool = Field(
        default=False,
        alias="HOST_TASK_OPERATION_RECONCILIATION_OPERATOR_ENABLED",
    )
    task_effect_reconciliation_operator_enabled: bool = Field(
        default=False,
        alias="HOST_TASK_EFFECT_RECONCILIATION_OPERATOR_ENABLED",
    )
    task_terminal_reconciliation_operator_enabled: bool = Field(
        default=False,
        alias="HOST_TASK_TERMINAL_RECONCILIATION_OPERATOR_ENABLED",
    )
    task_terminal_reconciliation_auto_enabled: bool = Field(
        default=False,
        alias="HOST_TASK_TERMINAL_RECONCILIATION_AUTO_ENABLED",
    )
    task_reconciliation_ed25519_public_keys_json: str = Field(
        default="",
        alias="HOST_TASK_RECONCILIATION_ED25519_PUBLIC_KEYS_JSON",
    )
    runtime_event_replay_enabled: bool = Field(
        default=False, alias="HOST_RUNTIME_EVENT_REPLAY_ENABLED"
    )
    nle_operator_views_enabled: bool = Field(
        default=False, alias="HOST_NLE_OPERATOR_VIEWS_ENABLED"
    )
    nle_life_views_enabled: bool = Field(
        default=False, alias="HOST_NLE_LIFE_VIEWS_ENABLED"
    )
    nle_governance_views_enabled: bool = Field(
        default=False, alias="HOST_NLE_GOVERNANCE_VIEWS_ENABLED"
    )
    sys_mode: str = Field(default="normal", alias="HOST_SYS_MODE")

    @model_validator(mode="after")
    def _validate_task_reconciliation_authority(self) -> CoreSettings:
        if self.agent_workspace_heartbeat_ms >= self.agent_workspace_lease_ms:
            raise ValueError("AGENT_WORKSPACE_HEARTBEAT_MUST_PRECEDE_LEASE_EXPIRY")
        workspace_secret = self.agent_workspace_hmac_secret.get_secret_value().encode(
            "utf-8"
        )
        if workspace_secret and not 32 <= len(workspace_secret) <= 4_096:
            raise ValueError("AGENT_WORKSPACE_HMAC_SECRET_INVALID")
        public_keys = parse_task_reconciliation_public_keys(
            self.task_reconciliation_ed25519_public_keys_json
        )
        if (
            self.task_graph_cancellation_operator_enabled
            or self.task_operation_reconciliation_operator_enabled
            or self.task_effect_reconciliation_operator_enabled
            or self.task_terminal_reconciliation_operator_enabled
        ) and not public_keys:
            raise ValueError(
                "TASK_RECONCILIATION_OPERATOR_REQUIRES_PUBLIC_KEYS"
            )
        if (
            self.task_graph_cancellation_operator_enabled
            and not self.task_operation_reconciliation_operator_enabled
        ):
            raise ValueError(
                "TASK_GRAPH_CANCELLATION_OPERATOR_REQUIRES_OPERATION_RECONCILIATION"
            )
        if (
            self.task_graph_cancellation_operator_enabled
            and not self.task_effect_reconciliation_operator_enabled
        ):
            raise ValueError(
                "TASK_GRAPH_CANCELLATION_OPERATOR_REQUIRES_EFFECT_RECONCILIATION"
            )
        if (
            self.task_runtime_bounded_fanout_enabled
            or self.task_runtime_atomic_graph_success_enabled
        ) and not self.task_runtime_canary_receipt.strip():
            raise ValueError("TASK_RUNTIME_GRAPH_ENABLEMENT_REQUIRES_CANARY_RECEIPT")
        return self

    def task_reconciliation_public_keys(self) -> dict[str, bytes]:
        """Return a fresh verification-only public-key ring."""

        return parse_task_reconciliation_public_keys(
            self.task_reconciliation_ed25519_public_keys_json
        )

    @classmethod
    def readiness_fields(cls) -> list[SettingsFieldReport]:
        """产出 readiness 报告字段（供 /health/env-readiness 使用）。"""
        s = get_core_settings()
        # domain 标识
        domain = "core"
        return [
            inspect_setting(
                domain=domain, field_name="env", env_key="Natsume_ENV",
                default_value="dev", current_value=s.env, is_required=False,
            ),
            inspect_setting(
                domain=domain, field_name="data_dir", env_key="NATSUME_DATA_DIR",
                default_value="./data", current_value=str(s.data_dir), is_required=False,
            ),
            inspect_setting(
                domain=domain, field_name="log_level", env_key="HOST_APP_LOG_LEVEL",
                default_value="INFO", current_value=s.log_level, is_required=False,
            ),
            inspect_setting(
                domain=domain, field_name="host_app_host", env_key="HOST_APP_HOST",
                default_value="127.0.0.1", current_value=s.host_app_host, is_required=False,
            ),
            inspect_setting(
                domain=domain, field_name="host_app_port", env_key="HOST_APP_PORT",
                default_value=18080, current_value=s.host_app_port, is_required=False,
            ),
            inspect_setting(
                domain=domain, field_name="prefrontal_plan_enabled",
                env_key="PREFRONTAL_PLAN_ENABLED",
                default_value=False, current_value=s.prefrontal_plan_enabled, is_required=False,
            ),
            inspect_setting(
                domain=domain, field_name="skill_dag_parallel_window",
                env_key="HOST_SKILL_DAG_PARALLEL_WINDOW",
                default_value=4, current_value=s.skill_dag_parallel_window, is_required=False,
            ),
            inspect_setting(
                domain=domain, field_name="task_runtime_canonical_owner",
                env_key="HOST_TASK_RUNTIME_CANONICAL_OWNER",
                default_value="",
                current_value=s.task_runtime_canonical_owner,
                is_required=False,
            ),
            inspect_setting(
                domain=domain,
                field_name="task_runtime_bounded_fanout_enabled",
                env_key="HOST_TASK_RUNTIME_BOUNDED_FANOUT_ENABLED",
                default_value=False,
                current_value=s.task_runtime_bounded_fanout_enabled,
                is_required=False,
            ),
            inspect_setting(
                domain=domain,
                field_name="task_runtime_atomic_graph_success_enabled",
                env_key="HOST_TASK_RUNTIME_ATOMIC_GRAPH_SUCCESS_ENABLED",
                default_value=False,
                current_value=s.task_runtime_atomic_graph_success_enabled,
                is_required=False,
            ),
            inspect_setting(
                domain=domain,
                field_name="task_runtime_canary_receipt",
                env_key="HOST_TASK_RUNTIME_CANARY_RECEIPT",
                default_value="",
                current_value=("configured" if s.task_runtime_canary_receipt else ""),
                is_required=(
                    s.task_runtime_bounded_fanout_enabled
                    or s.task_runtime_atomic_graph_success_enabled
                ),
            ),
            inspect_setting(
                domain=domain,
                field_name="agent_workspace_cleanup_poll_ms",
                env_key="HOST_AGENT_WORKSPACE_CLEANUP_POLL_MS",
                default_value=5_000,
                current_value=s.agent_workspace_cleanup_poll_ms,
                is_required=False,
            ),
            inspect_setting(
                domain=domain,
                field_name="agent_workspace_hmac_secret",
                env_key="HOST_AGENT_WORKSPACE_HMAC_SECRET",
                default_value="",
                current_value=(
                    "configured"
                    if s.agent_workspace_hmac_secret.get_secret_value()
                    else ""
                ),
                is_required=True,
            ),
            inspect_setting(
                domain=domain,
                field_name="agent_workspace_managed_root",
                env_key="HOST_AGENT_WORKSPACE_MANAGED_ROOT",
                default_value=str(Path.home() / ".natsume" / "agent_worktrees"),
                current_value=str(s.agent_workspace_managed_root),
                is_required=False,
            ),
            inspect_setting(
                domain=domain,
                field_name="agent_workspace_lease_ms",
                env_key="HOST_AGENT_WORKSPACE_LEASE_MS",
                default_value=120_000,
                current_value=s.agent_workspace_lease_ms,
                is_required=False,
            ),
            inspect_setting(
                domain=domain,
                field_name="agent_workspace_heartbeat_ms",
                env_key="HOST_AGENT_WORKSPACE_HEARTBEAT_MS",
                default_value=30_000,
                current_value=s.agent_workspace_heartbeat_ms,
                is_required=False,
            ),
            inspect_setting(
                domain=domain,
                field_name="task_graph_cancellation_operator_enabled",
                env_key="HOST_TASK_GRAPH_CANCELLATION_OPERATOR_ENABLED",
                default_value=False,
                current_value=s.task_graph_cancellation_operator_enabled,
                is_required=False,
            ),
            inspect_setting(
                domain=domain,
                field_name="task_operation_reconciliation_operator_enabled",
                env_key="HOST_TASK_OPERATION_RECONCILIATION_OPERATOR_ENABLED",
                default_value=False,
                current_value=s.task_operation_reconciliation_operator_enabled,
                is_required=False,
            ),
            inspect_setting(
                domain=domain,
                field_name="task_effect_reconciliation_operator_enabled",
                env_key="HOST_TASK_EFFECT_RECONCILIATION_OPERATOR_ENABLED",
                default_value=False,
                current_value=s.task_effect_reconciliation_operator_enabled,
                is_required=False,
            ),
            inspect_setting(
                domain=domain,
                field_name="task_terminal_reconciliation_operator_enabled",
                env_key="HOST_TASK_TERMINAL_RECONCILIATION_OPERATOR_ENABLED",
                default_value=False,
                current_value=s.task_terminal_reconciliation_operator_enabled,
                is_required=False,
            ),
            inspect_setting(
                domain=domain,
                field_name="task_terminal_reconciliation_auto_enabled",
                env_key="HOST_TASK_TERMINAL_RECONCILIATION_AUTO_ENABLED",
                default_value=False,
                current_value=s.task_terminal_reconciliation_auto_enabled,
                is_required=False,
            ),
            inspect_setting(
                domain=domain,
                field_name="task_reconciliation_ed25519_public_keys_json",
                env_key="HOST_TASK_RECONCILIATION_ED25519_PUBLIC_KEYS_JSON",
                default_value="",
                current_value=s.task_reconciliation_ed25519_public_keys_json,
                is_required=(
                    s.task_graph_cancellation_operator_enabled
                    or s.task_operation_reconciliation_operator_enabled
                    or s.task_effect_reconciliation_operator_enabled
                    or s.task_terminal_reconciliation_operator_enabled
                ),
            ),
            inspect_setting(
                domain=domain, field_name="runtime_event_replay_enabled",
                env_key="HOST_RUNTIME_EVENT_REPLAY_ENABLED",
                default_value=False,
                current_value=s.runtime_event_replay_enabled,
                is_required=False,
            ),
            inspect_setting(
                domain=domain, field_name="nle_operator_views_enabled",
                env_key="HOST_NLE_OPERATOR_VIEWS_ENABLED",
                default_value=False,
                current_value=s.nle_operator_views_enabled,
                is_required=False,
            ),
            inspect_setting(
                domain=domain, field_name="nle_life_views_enabled",
                env_key="HOST_NLE_LIFE_VIEWS_ENABLED",
                default_value=False,
                current_value=s.nle_life_views_enabled,
                is_required=False,
            ),
            inspect_setting(
                domain=domain, field_name="nle_governance_views_enabled",
                env_key="HOST_NLE_GOVERNANCE_VIEWS_ENABLED",
                default_value=False,
                current_value=s.nle_governance_views_enabled,
                is_required=False,
            ),
            inspect_setting(
                domain=domain, field_name="sys_mode",
                env_key="HOST_SYS_MODE",
                default_value="normal", current_value=s.sys_mode, is_required=False,
            ),
        ]


@lru_cache(maxsize=1)
def get_core_settings() -> CoreSettings:
    """惰性单例。测试可通过 `get_core_settings.cache_clear()` 重置。"""
    return CoreSettings()
