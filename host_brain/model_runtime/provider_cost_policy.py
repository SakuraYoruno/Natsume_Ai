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

    resolved = dict(usage or {}) if isinstance(usage, Mapping) else {}
    if not tool_calls_enabled:
        resolved["tool_call_count"] = 0

    normalized_provider = str(provider or "").strip().lower()
    normalized_model = str(model_id or "").strip().lower()
    rates = _DEEPSEEK_RATES_MICROS_PER_MILLION.get(normalized_model)
    if normalized_provider != "deepseek" or rates is None:
        return resolved, None
    if not _is_official_deepseek_url(base_url):
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
            return resolved, None
        values[key] = int(value)
    if (
        values["prompt_cache_hit_tokens"]
        + values["prompt_cache_miss_tokens"]
        != values["prompt_tokens"]
    ):
        return resolved, None

    numerator = sum(values[key] * rates[key] for key in rates)
    resolved["model_cost_micros"] = (numerator + _ONE_MILLION - 1) // _ONE_MILLION
    return resolved, provider_cost_policy_digest()


def _is_official_deepseek_url(base_url: str | None) -> bool:
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
