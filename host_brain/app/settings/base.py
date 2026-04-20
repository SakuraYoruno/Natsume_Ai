"""分层 Settings 的共用基础（OPT-4）

提供：
1. SettingsReadinessReport：启动自检报告的 DTO
2. verify_readiness()：聚合各域 Settings 的字段来源（env / default / missing）
   并返回报告，供 `/health/env-readiness` 展示。

设计要点：
- 不依赖具体域（core/llm/tribe/observability），通过回调解耦。
- 每个域 Settings 必须提供 `required_fields()` 类方法声明"必填字段"。
- 未设置的必填字段走 fail-fast——由调用方（通常是 fastapi_app 启动钩子）决定是抛错还是仅告警。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

FieldSource = Literal["env", "default", "missing"]


@dataclass(frozen=True)
class SettingsFieldReport:
    """单个 Settings 字段的来源与取值摘要。"""

    domain: str                    # core / llm / tribe / observability
    field_name: str                # 字段在 Settings 中的属性名
    env_key: str                   # 对应的环境变量名（含 prefix）
    source: FieldSource            # env 表示显式设置，default 表示使用默认，missing 表示必填缺失
    value_preview: str             # 值的预览（敏感字段自动脱敏为 "<redacted>"）
    is_required: bool = False      # 是否必填


@dataclass
class SettingsReadinessReport:
    """跨 domain 的 Settings readiness 聚合报告。"""

    fields: list[SettingsFieldReport] = field(default_factory=list)

    @property
    def missing_required(self) -> list[SettingsFieldReport]:
        return [f for f in self.fields if f.source == "missing"]

    @property
    def env_set_count(self) -> int:
        return sum(1 for f in self.fields if f.source == "env")

    @property
    def default_count(self) -> int:
        return sum(1 for f in self.fields if f.source == "default")

    def is_ready(self) -> bool:
        return not self.missing_required

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.is_ready(),
            "counts": {
                "total": len(self.fields),
                "env": self.env_set_count,
                "default": self.default_count,
                "missing": len(self.missing_required),
            },
            "missing_required": [
                {"domain": f.domain, "field": f.field_name, "env_key": f.env_key}
                for f in self.missing_required
            ],
            "fields": [
                {
                    "domain": f.domain,
                    "field": f.field_name,
                    "env_key": f.env_key,
                    "source": f.source,
                    "value_preview": f.value_preview,
                    "required": f.is_required,
                }
                for f in self.fields
            ],
        }


_REDACT_HINTS = ("key", "secret", "token", "password", "credential", "hmac", "api_key")


def _redact(env_key: str, raw: str) -> str:
    k = env_key.lower()
    if any(hint in k for hint in _REDACT_HINTS):
        return "<redacted>" if raw else "<empty>"
    if len(raw) > 80:
        return raw[:77] + "..."
    return raw


def inspect_setting(
    *,
    domain: str,
    field_name: str,
    env_key: str,
    default_value: Any,
    current_value: Any,
    is_required: bool = False,
) -> SettingsFieldReport:
    """判定一个 Settings 字段的来源。

    `env_key` 是 pydantic-settings 映射的环境变量名（含 prefix）。
    判定逻辑：
    - 环境变量已设置 → source=env
    - 必填但当前值为 None/空串 → source=missing
    - 否则 → source=default
    """
    raw_env = os.environ.get(env_key)
    if raw_env not in (None, ""):
        return SettingsFieldReport(
            domain=domain,
            field_name=field_name,
            env_key=env_key,
            source="env",
            value_preview=_redact(env_key, raw_env),
            is_required=is_required,
        )
    if is_required and current_value in (None, ""):
        return SettingsFieldReport(
            domain=domain,
            field_name=field_name,
            env_key=env_key,
            source="missing",
            value_preview="<missing>",
            is_required=True,
        )
    preview = "" if current_value is None else str(current_value)
    return SettingsFieldReport(
        domain=domain,
        field_name=field_name,
        env_key=env_key,
        source="default",
        value_preview=_redact(env_key, preview),
        is_required=is_required,
    )


ReportProducer = Callable[[], list[SettingsFieldReport]]


def verify_readiness(producers: list[ReportProducer]) -> SettingsReadinessReport:
    """合并多个域的 field report，返回统一报告。"""
    report = SettingsReadinessReport()
    for produce in producers:
        try:
            report.fields.extend(produce())
        except Exception:  # pragma: no cover - defensive
            # 个别 domain 失败不应阻塞整体自检
            continue
    return report
