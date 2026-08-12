"""Versioned provider-cost derivation from provider-owned token usage.

Providers generally return token counts but not the currency amount charged
for each response.  Benefit experiments still need a comparable cost unit, so
this module applies a small, explicit price snapshot only when the transport,
model and all required cache-token dimensions are known exactly.  Unsupported
or incomplete usage remains unpriced instead of being silently treated as
free.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit


DEEPSEEK_V4_USD_POLICY_ID = "deepseek-v4-usd-2026-07-24"
"""Official V4 USD prices retrieved from DeepSeek API docs on 2026-08-05."""

_ONE_MILLION = 1_000_000
_DEEPSEEK_OFFICIAL_HOSTS = frozenset({"api.deepseek.com"})
_DEEPSEEK_RATES_MICROS_PER_MILLION: dict[str, dict[str, int]] = {
    # USD per one million tokens converted to integer micro-USD per one
    # million tokens.  The final request cost is rounded up by one micro-USD
    # so a budget or benefit report never understates the charge.
    "deepseek-v4-flash": {
        "prompt_cache_hit_tokens": 2_800,
        "prompt_cache_miss_tokens": 140_000,
        "completion_tokens": 280_000,
    },
    "deepseek-v4-pro": {
        "prompt_cache_hit_tokens": 3_625,
        "prompt_cache_miss_tokens": 435_000,
        "completion_tokens": 870_000,
    },
}


def provider_cost_policy_digest() -> str:
    """Return the immutable identity of the currently supported price table."""

    # 摘要固定价格表、单位与来源版本；把 policy_id 单独写入不足以发现费率字段被改动，
    # 结算/实验记录可凭该 identity 复现当时采用的估价依据。
    encoded = json.dumps(
        {
            "schema": "natsume.model-cost-policy.v1",
            "policy_id": DEEPSEEK_V4_USD_POLICY_ID,
            "currency_unit": "MICRO_USD",
            "rates_micros_per_million_tokens": _DEEPSEEK_RATES_MICROS_PER_MILLION,
            "source_ref": "https://api-docs.deepseek.com/quick_start/pricing",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def enrich_provider_usage(
    *,
    provider: str,
    model_id: str,
    base_url: str | None,
    usage: Mapping[str, Any] | None,
    tool_calls_enabled: bool,
) -> tuple[dict[str, Any], str | None]:
    """Add exact tool count and a supported official-provider cost.

    ``tool_call_count=0`` is authoritative only when the transport request did
    not expose tools.  Cost is added only for DeepSeek's official endpoint and
    only when hit, miss, prompt and completion counts are mutually consistent.
    The returned policy digest is suitable for transport audit metadata.
    """

    # 费用仅是基于供应商 token 计量的受支持估算，不替代账单。复制 usage 后补充字段，
    # 不修改调用方对象，避免一次路由的估价泄漏到其他 provider 尝试。
    resolved = dict(usage or {}) if isinstance(usage, Mapping) else {}
    if not tool_calls_enabled:
        # 只有传输层明确未暴露 tools 时，零调用数才是事实；若启用了工具却没有计数，
        # 保留未知状态而不是将其伪造成零。
        resolved["tool_call_count"] = 0

    normalized_provider = str(provider or "").strip().lower()
    normalized_model = str(model_id or "").strip().lower()
    rates = _DEEPSEEK_RATES_MICROS_PER_MILLION.get(normalized_model)
    if normalized_provider != "deepseek" or rates is None:
        # 未登记 provider/model 不估价，防止用相似模型名或陈旧表格低估成本。
        return resolved, None
    if not _is_official_deepseek_url(base_url):
        # 代理、兼容端点或自建网关即使声称 DeepSeek 也没有官方定价保证，必须保留
        # 未定价而非套用官方费率。
        return resolved, None

    required = (
        "prompt_tokens",
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
        "completion_tokens",
    )
    values: dict[str, int] = {}
    for key in required:
        value = resolved.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            # 缺失、布尔或负 token 计量都无法形成可审计费用；fail-open 为“无价格”，
            # 而不是生成零成本并扩大预算余量。
            return resolved, None
        values[key] = int(value)
    if (
        values["prompt_cache_hit_tokens"]
        + values["prompt_cache_miss_tokens"]
        != values["prompt_tokens"]
    ):
        # 命中与未命中缓存必须恰好覆盖 prompt token；不一致意味着供应商使用 schema
        # 或采集链变化，停止估价让新格式先经过显式审核。
        return resolved, None

    # 采用整数微美元和向上取整，保证任务预算及收益报表不会因分数 token 费用被截断
    # 而系统性低报；该步骤只在所有计量维度完整时执行。
    numerator = sum(values[key] * rates[key] for key in rates)
    resolved["model_cost_micros"] = (numerator + _ONE_MILLION - 1) // _ONE_MILLION
    return resolved, provider_cost_policy_digest()


def _is_official_deepseek_url(base_url: str | None) -> bool:
    # 仅接受 HTTPS 官方 hostname，忽略路径但拒绝子域/用户名混淆等字符串前缀匹配，
    # 使价格策略不会被任意 base_url 注入触发。
    raw = str(base_url or "https://api.deepseek.com/v1").strip()
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return False
    return (
        str(parsed.scheme or "").lower() == "https"
        and str(parsed.hostname or "").strip().rstrip(".").lower()
        in _DEEPSEEK_OFFICIAL_HOSTS
    )


__all__ = [
    "DEEPSEEK_V4_USD_POLICY_ID",
    "enrich_provider_usage",
    "provider_cost_policy_digest",
]
