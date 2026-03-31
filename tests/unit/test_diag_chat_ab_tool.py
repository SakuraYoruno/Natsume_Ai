from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.diag_chat_ab import SampleRun, _score_quality, _summarize_profile


def _run(
    *,
    profile: str,
    latency_ms: float,
    decision: str,
    llm_status: str,
    route_target: str,
    ok: bool,
    quality_score: float,
    error: str | None = None,
) -> SampleRun:
    return SampleRun(
        profile=profile,
        round_index=1,
        sample_index=1,
        message="hello",
        ok=ok,
        latency_ms=latency_ms,
        decision=decision,
        reason_code=None,
        quality_stage="S_COMPLETE",
        quality_score=quality_score,
        narrative_len=32,
        llm_status=llm_status,
        provider="openai",
        model_id="qwen3:14b-q4_k_m",
        route_target=route_target,
        route_reason_code="HOST_CHAT_HOST_14B_ACTIVE",
        error=error,
    )


def test_score_quality_returns_zero_on_error() -> None:
    score = _score_quality(
        decision="APPROVE",
        llm_status="OK",
        narrative_len=64,
        reason_code=None,
        error="boom",
    )
    assert score == 0.0


def test_score_quality_rewards_approve_and_ok() -> None:
    score = _score_quality(
        decision="APPROVE",
        llm_status="OK",
        narrative_len=64,
        reason_code=None,
        error=None,
    )
    assert score == 1.0


def test_summarize_profile_aggregates_rates_and_histograms() -> None:
    rows = [
        _run(
            profile="A",
            latency_ms=100.0,
            decision="APPROVE",
            llm_status="OK",
            route_target="host_14b",
            ok=True,
            quality_score=0.95,
        ),
        _run(
            profile="A",
            latency_ms=300.0,
            decision="REJECT",
            llm_status="DEGRADED",
            route_target="cloud",
            ok=False,
            quality_score=0.25,
            error="timeout",
        ),
    ]

    summary = _summarize_profile(rows)
    assert summary["sample_count"] == 2
    assert summary["ok_rate"] == 0.5
    assert summary["approve_rate"] == 0.5
    assert summary["errors"] == 1
    assert summary["route_target_hist"]["cloud"] == 1
    assert summary["route_target_hist"]["host_14b"] == 1
