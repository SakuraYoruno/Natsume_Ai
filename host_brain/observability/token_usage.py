"""LLM token usage estimation and reporting helpers."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from host_brain.observability.token_usage_settings import TokenUsageSettings
from host_brain.persistence.token_usage_query_repository import (
    TokenUsageQueryRepository,
)


ESTIMATOR_VERSION = "llm-token-estimator-v1"
MS_HOUR = 60 * 60 * 1000

SCENE_TOKEN_ESTIMATE_DEFAULTS: dict[str, dict[str, int]] = {
    "CHAT": {"prompt": 2400, "completion": 450},
    "INNER_ACTIVITY": {"prompt": 4200, "completion": 160},
    "SELF_REFLECTION": {"prompt": 6200, "completion": 500},
    "MEMORY_CONSOLIDATE": {"prompt": 5200, "completion": 600},
    "HIPPOCAMPUS_RECALL": {"prompt": 3200, "completion": 400},
    "MULTIMODAL": {"prompt": 1800, "completion": 350},
    "DEFAULT": {"prompt": 3000, "completion": 400},
}


@dataclass(frozen=True)
class TokenUsageEstimate:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated: bool
    source: str


def estimate_text_tokens(text: Any) -> int:
    """Cheap deterministic text token estimator tuned for mixed zh/en prompts."""
    if text is None:
        return 0
    value = str(text)
    if not value:
        return 0
    non_ws = [ch for ch in value if not ch.isspace()]
    if not non_ws:
        return 0
    cjk_count = sum(1 for ch in non_ws if _is_cjk(ch))
    non_cjk_count = len(non_ws) - cjk_count
    # CJK text is often near 1 char/token; ASCII prose/JSON is closer to 4 chars/token.
    return max(1, int(math.ceil(cjk_count + (non_cjk_count / 4.0))))


def estimate_value_tokens(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return estimate_text_tokens(value)
    if isinstance(value, Mapping):
        content_type = str(value.get("type") or "").strip().lower()
        if content_type in {"image_url", "input_image"}:
            return 255
        return estimate_text_tokens(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return sum(estimate_value_tokens(item) for item in value)
    return estimate_text_tokens(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def estimate_messages_tokens(messages: Iterable[Mapping[str, Any]] | None) -> int:
    total = 0
    count = 0
    for message in list(messages or []):
        if not isinstance(message, Mapping):
            continue
        count += 1
        total += 4
        total += estimate_text_tokens(message.get("role") or "")
        total += estimate_value_tokens(message.get("content"))
        for key, value in message.items():
            if key in {"role", "content"}:
                continue
            total += estimate_text_tokens(key) + estimate_value_tokens(value)
    if count:
        total += 3
    return total


def normalize_provider_usage(usage: Mapping[str, Any] | None) -> TokenUsageEstimate | None:
    if not isinstance(usage, Mapping):
        return None
    prompt = _coerce_int(
        usage.get("prompt_tokens")
        or usage.get("input_tokens")
        or usage.get("promptTokenCount")
        or usage.get("inputTokenCount")
    )
    completion = _coerce_int(
        usage.get("completion_tokens")
        or usage.get("output_tokens")
        or usage.get("candidatesTokenCount")
        or usage.get("outputTokenCount")
    )
    total = _coerce_int(usage.get("total_tokens") or usage.get("totalTokenCount"))
    if total <= 0:
        total = prompt + completion
    if prompt <= 0 and completion <= 0 and total <= 0:
        return None
    return TokenUsageEstimate(
        prompt_tokens=max(0, prompt),
        completion_tokens=max(0, completion),
        total_tokens=max(0, total),
        estimated=False,
        source="provider_usage",
    )


def build_llm_token_usage_row(
    *,
    request_id: str,
    scene_type: str,
    cognitive_tier: str,
    provider: str,
    model_id: str,
    messages: Iterable[Mapping[str, Any]] | None,
    content: Any,
    attempts: Iterable[Any] | None,
    cached: bool,
    pool_exhausted: bool,
    ts_ms: int,
    provider_usage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    message_rows = [item for item in list(messages or []) if isinstance(item, Mapping)]
    usage = normalize_provider_usage(provider_usage)
    if usage is None:
        prompt_tokens = estimate_messages_tokens(message_rows)
        completion_tokens = estimate_value_tokens(content)
        usage = TokenUsageEstimate(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            estimated=True,
            source="estimate",
        )

    attempt_statuses = [_attempt_status(item) for item in list(attempts or [])]
    status_counts = Counter(status for status in attempt_statuses if status)
    upstream_attempt_count = sum(
        1
        for status in attempt_statuses
        if status in {"OK", "FAILED", "FAILED_AFTER_STREAM"}
    )
    billable_attempt_count = 0 if cached else upstream_attempt_count
    billable_prompt = 0 if cached else usage.prompt_tokens * max(1, billable_attempt_count)
    if pool_exhausted and billable_attempt_count <= 0:
        billable_prompt = 0
    billable_completion = 0 if cached or pool_exhausted else usage.completion_tokens
    billable_total = billable_prompt + billable_completion
    detail = {
        "estimator_version": ESTIMATOR_VERSION,
        "message_count": len(message_rows),
        "attempt_status_counts": dict(status_counts),
        "usage_source": usage.source,
    }
    return {
        "request_id": str(request_id or "").strip(),
        "scene_type": str(scene_type or "").strip().upper(),
        "cognitive_tier": str(cognitive_tier or "").strip().upper(),
        "provider": str(provider or "").strip().lower(),
        "model_id": str(model_id or "").strip(),
        "prompt_tokens": int(usage.prompt_tokens),
        "completion_tokens": int(usage.completion_tokens),
        "total_tokens": int(usage.total_tokens),
        "billable_prompt_tokens": int(billable_prompt),
        "billable_completion_tokens": int(billable_completion),
        "billable_total_tokens": int(billable_total),
        "estimated": 1 if usage.estimated else 0,
        "cached": 1 if cached else 0,
        "pool_exhausted": 1 if pool_exhausted else 0,
        "attempt_count": len(attempt_statuses),
        "billable_attempt_count": int(billable_attempt_count),
        "source": usage.source,
        "detail_json": detail,
        "ts_ms": int(ts_ms),
    }


def build_token_usage_report(
    *,
    ledger: Any | None = None,
    db_path: str | Path | None = None,
    start_ms: int | None = None,
    end_ms: int | None = None,
    window_hours: int = 24,
    top_n: int = 10,
) -> dict[str, Any]:
    end_value = int(end_ms if end_ms is not None else time.time() * 1000)
    start_value = int(start_ms if start_ms is not None else end_value - max(1, int(window_hours)) * MS_HOUR)
    limit = max(1, min(50, int(top_n)))
    resolved_db_path = str(db_path or getattr(ledger, "db_path", "") or _default_audit_db_path())
    window = TokenUsageQueryRepository.from_source(
        ledger=ledger,
        db_path=resolved_db_path,
    ).read_window(start_ms=start_value, end_ms=end_value, limit=limit)
    token_rows = window["token_rows"]
    token_hotspots = window["token_hotspots"]
    pool_daily = window["pool_daily"]
    pool_hotspots = window["pool_hotspots"]
    fallback_estimates = _estimate_from_pool_hotspots(pool_hotspots)
    return {
            "status": "OK",
            "window": {
                "start_ms": start_value,
                "end_ms": end_value,
                "window_hours": round((end_value - start_value) / MS_HOUR, 2),
            },
            "source": {
                "db_path": resolved_db_path,
                "has_llm_token_usage_log": window["has_tokens"],
                "has_llm_pool_call_log": window["has_pool"],
                "measurement_mode": "token_usage_log" if token_rows else "pool_call_log_fallback",
            },
            "token_usage_daily": token_rows,
            "token_usage_hotspots": token_hotspots,
            "pool_call_daily": pool_daily,
            "pool_call_hotspots": pool_hotspots,
            "fallback_billable_estimates": fallback_estimates,
            "diagnosis": _build_diagnosis(
                token_rows=token_rows,
                pool_daily=pool_daily,
                pool_hotspots=pool_hotspots,
                fallback_estimates=fallback_estimates,
            ),
        }


def _estimate_from_pool_hotspots(pool_hotspots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    estimates: list[dict[str, Any]] = []
    for row in pool_hotspots:
        scene = str(row.get("scene_type") or "DEFAULT").strip().upper() or "DEFAULT"
        defaults = SCENE_TOKEN_ESTIMATE_DEFAULTS.get(scene, SCENE_TOKEN_ESTIMATE_DEFAULTS["DEFAULT"])
        noncached = int(row.get("noncached_success_calls") or 0)
        prompt = int(defaults["prompt"])
        completion = int(defaults["completion"])
        mid = noncached * (prompt + completion)
        estimates.append(
            {
                "scene_type": scene,
                "provider": str(row.get("provider") or ""),
                "model_id": str(row.get("model_id") or ""),
                "noncached_success_calls": noncached,
                "assumed_prompt_tokens_per_call": prompt,
                "assumed_completion_tokens_per_call": completion,
                "estimated_billable_total_tokens": mid,
                "confidence": "low" if noncached else "none",
                "note": "fallback estimate because historical token rows were not recorded",
            }
        )
    return estimates


def _build_diagnosis(
    *,
    token_rows: list[dict[str, Any]],
    pool_daily: list[dict[str, Any]],
    pool_hotspots: list[dict[str, Any]],
    fallback_estimates: list[dict[str, Any]],
) -> dict[str, Any]:
    total_pool_calls = sum(int(row.get("calls") or 0) for row in pool_daily)
    total_cached = sum(int(row.get("cached_calls") or 0) for row in pool_daily)
    total_exhausted = sum(int(row.get("pool_exhausted_calls") or 0) for row in pool_daily)
    top_pool = pool_hotspots[0] if pool_hotspots else {}
    top_scene = str(top_pool.get("scene_type") or "").upper()
    estimated_total = sum(int(row.get("estimated_billable_total_tokens") or 0) for row in fallback_estimates)
    token_total = sum(int(row.get("billable_total_tokens") or 0) for row in token_rows)
    findings: list[str] = []
    if top_scene == "INNER_ACTIVITY":
        findings.append("INNER_ACTIVITY 是窗口内最大的 LLM 池热点，通常来自内心活动/内世界 tick 后台任务。")
    if total_pool_calls and total_cached / max(1, total_pool_calls) > 0.5:
        findings.append("日志里大量 cached=1，本地缓存命中会放大调用行数，但通常不是供应商计费 token。")
    if total_exhausted:
        findings.append("存在 pool_exhausted 行；纯 cooldown 耗尽通常不计费，但会说明模型池正处于退避/失败风暴。")
    if estimated_total >= 10_000_000 or token_total >= 10_000_000:
        findings.append("按非缓存成功调用与场景均值估算，千万级日 token 消耗是合理量级。")
    if not token_rows:
        findings.append("历史 llm_pool_call_log 没有保存真实 token 字段，本报表对旧数据只能做低置信估算。")
    return {
        "summary": findings,
        "top_scene": top_scene or None,
        "pool_calls": total_pool_calls,
        "cached_calls": total_cached,
        "pool_exhausted_calls": total_exhausted,
        "billable_total_tokens": token_total,
        "fallback_estimated_billable_total_tokens": estimated_total,
    }


def _default_audit_db_path() -> str:
    return str(TokenUsageSettings().audit_db_path)


def _attempt_status(item: Any) -> str:
    if isinstance(item, Mapping):
        return str(item.get("status") or "").strip().upper()
    return str(getattr(item, "status", "") or "").strip().upper()


def _coerce_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return (
        0x4E00 <= code <= 0x9FFF
        or 0x3400 <= code <= 0x4DBF
        or 0x20000 <= code <= 0x2A6DF
        or 0x2A700 <= code <= 0x2B73F
        or 0x2B740 <= code <= 0x2B81F
        or 0x2B820 <= code <= 0x2CEAF
        or 0xF900 <= code <= 0xFAFF
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build an LLM token usage report from the audit sqlite DB.")
    parser.add_argument("--db", default=_default_audit_db_path(), help="audit sqlite path")
    parser.add_argument("--hours", type=int, default=24, help="window size in hours")
    parser.add_argument("--top", type=int, default=10, help="top hotspot count")
    parser.add_argument("--start-ms", type=int, default=None)
    parser.add_argument("--end-ms", type=int, default=None)
    args = parser.parse_args(argv)
    report = build_token_usage_report(
        db_path=args.db,
        start_ms=args.start_ms,
        end_ms=args.end_ms,
        window_hours=args.hours,
        top_n=args.top,
    )
    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
