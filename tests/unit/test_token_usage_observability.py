from __future__ import annotations

from pathlib import Path

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.observability.token_usage import (
    build_llm_token_usage_row,
    build_token_usage_report,
    estimate_messages_tokens,
    estimate_text_tokens,
)
from host_brain.skills.token_usage_registry import build_token_usage_skill_registry


def test_token_estimator_handles_mixed_cjk_and_ascii() -> None:
    assert estimate_text_tokens("你好") == 2
    assert estimate_text_tokens("hello world") >= 3
    assert estimate_messages_tokens([{"role": "user", "content": "你好 hello"}]) >= 10


def test_llm_token_usage_row_tracks_cache_and_retry_billable_estimate() -> None:
    row = build_llm_token_usage_row(
        request_id="req-token-row",
        scene_type="chat",
        cognitive_tier="b",
        provider="gemini",
        model_id="gemini-2.5-flash-lite",
        messages=[{"role": "user", "content": "你好 hello"}],
        content="好的",
        attempts=[
            {"status": "FAILED", "provider": "gemini"},
            {"status": "OK", "provider": "gemini"},
            {"status": "COOLDOWN", "provider": "deepseek"},
        ],
        cached=False,
        pool_exhausted=False,
        ts_ms=1800000000000,
    )

    assert row["attempt_count"] == 3
    assert row["billable_attempt_count"] == 2
    assert row["billable_prompt_tokens"] == row["prompt_tokens"] * 2
    assert row["billable_completion_tokens"] == row["completion_tokens"]

    cached = dict(row)
    cached.update(cached=1, billable_prompt_tokens=0, billable_completion_tokens=0, billable_total_tokens=0)
    assert cached["billable_total_tokens"] == 0

    actual = build_llm_token_usage_row(
        request_id="req-provider-usage",
        scene_type="chat",
        cognitive_tier="b",
        provider="openai",
        model_id="demo",
        messages=[{"role": "user", "content": "ignored by provider usage"}],
        content="ignored",
        attempts=[{"status": "OK"}],
        cached=False,
        pool_exhausted=False,
        ts_ms=1800000000001,
        provider_usage={"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
    )
    assert actual["estimated"] == 0
    assert actual["prompt_tokens"] == 11
    assert actual["billable_total_tokens"] == 14


def test_token_usage_report_aggregates_token_and_pool_rows() -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    ts_ms = 1800000000000
    ledger.log_llm_pool_call(
        {
            "request_id": "req-inner",
            "scene_type": "INNER_ACTIVITY",
            "cognitive_tier": "B",
            "attempts_json": "[]",
            "attempt_count": 1,
            "final_model": "gemini-2.5-flash-lite",
            "final_provider": "gemini",
            "pool_exhausted": 0,
            "cached": 0,
            "degraded_to_rule": 0,
            "total_latency_ms": 123,
            "ts_ms": ts_ms,
        }
    )
    ledger.log_llm_token_usage(
        {
            "request_id": "req-inner",
            "scene_type": "INNER_ACTIVITY",
            "cognitive_tier": "B",
            "provider": "gemini",
            "model_id": "gemini-2.5-flash-lite",
            "prompt_tokens": 4200,
            "completion_tokens": 160,
            "total_tokens": 4360,
            "billable_prompt_tokens": 4200,
            "billable_completion_tokens": 160,
            "billable_total_tokens": 4360,
            "estimated": 1,
            "cached": 0,
            "pool_exhausted": 0,
            "attempt_count": 1,
            "billable_attempt_count": 1,
            "source": "estimate",
            "detail_json": {"estimator_version": "test"},
            "ts_ms": ts_ms,
        }
    )

    report = build_token_usage_report(ledger=ledger, start_ms=ts_ms - 1, end_ms=ts_ms + 1)

    assert report["source"]["measurement_mode"] == "token_usage_log"
    assert report["token_usage_daily"][0]["billable_total_tokens"] == 4360
    assert report["pool_call_hotspots"][0]["scene_type"] == "INNER_ACTIVITY"
    assert report["diagnosis"]["top_scene"] == "INNER_ACTIVITY"


def test_token_usage_skill_provider_returns_report(tmp_path: Path) -> None:
    db_path = tmp_path / "audit.sqlite3"
    ledger = SQLiteLedger(str(db_path), check_same_thread=False)
    ledger.close()
    registry = build_token_usage_skill_registry()
    payload = registry.require("llm_token_usage_report").handler(
        {"window_hours": 1, "db_path": str(db_path)},
        None,
    )

    assert payload["status"] == "OK"
    assert payload["tool"] == "llm_token_usage"
    assert payload["action"] == "report"
    assert "report" in payload
