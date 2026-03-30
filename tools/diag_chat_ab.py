"""In-process chat A/B benchmark for Host 8B rollout.

Runs `handle_chat` under two environment profiles, collects latency/quality
signals, and writes JSON + Markdown reports.

Default comparison:
- profile A: `host8b` (non-gaming, Host 8B preferred)
- profile B: `edge3b` (gaming mode, Edge 3B route preferred)

Usage example:
    python tools/diag_chat_ab.py --repeat 2 --disable-retrieval
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.app.chat_handler import handle_chat
from host_brain.app.schemas import ChatRequest


DEFAULT_MESSAGES: list[str] = [
    "Please greet me in one short sentence.",
    "Summarize today's priorities in 2 bullet points.",
    "How should I recover after a long coding session?",
    "Give me a calm response for a stressful moment.",
    "Help me rephrase this in clearer Japanese style.",
    "What is a good next step after finishing a feature?",
    "Explain one simple habit to improve consistency.",
    "Give me a short supportive response before gaming.",
    "How do I avoid over-committing in conversations?",
    "Provide one concise reflection question for tonight.",
]

PROFILE_PRESETS: dict[str, dict[str, str]] = {
    "host8b": {
        "HOST_SYS_MODE": "normal",
        "HOST_CHAT_HOST_8B_ENABLED": "true",
        "HOST_CHAT_FORCE_CLOUD": "false",
        "HOST_CHAT_HOST_8B_HEALTHCHECK_ENABLED": "false",
    },
    "cloud": {
        "HOST_SYS_MODE": "normal",
        "HOST_CHAT_FORCE_CLOUD": "true",
    },
}


@dataclass
class SampleRun:
    profile: str
    round_index: int
    sample_index: int
    message: str
    ok: bool
    latency_ms: float
    decision: str
    reason_code: str | None
    quality_stage: str
    quality_score: float
    narrative_len: int
    llm_status: str
    provider: str
    model_id: str
    route_target: str
    route_reason_code: str
    error: str | None = None


class _NoopMetrics:
    def inc_chat(self) -> None:
        return None

    def inc_error(self) -> None:
        return None

    def inc_rejection(self, _reason_code: str) -> None:
        return None


class _FakeRequest:
    def __init__(self, request_id: str) -> None:
        self.state = type("State", (), {"request_id": request_id})()


def _load_dotenv(path: Path) -> None:
    if not path.exists() or not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


def _parse_kv_overrides(rows: list[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for row in rows:
        text = str(row or "").strip()
        if not text:
            continue
        if "=" not in text:
            raise ValueError(f"invalid override '{text}', expected KEY=VALUE")
        key, _, value = text.partition("=")
        key = key.strip()
        if not key:
            raise ValueError(f"invalid override '{text}', empty key")
        parsed[key] = value.strip()
    return parsed


def _load_messages(sample_file: str | None) -> list[str]:
    if not sample_file:
        return list(DEFAULT_MESSAGES)
    path = Path(sample_file)
    raw_text = ""
    for encoding in ("utf-8", "utf-8-sig"):
        try:
            raw_text = path.read_text(encoding=encoding)
            break
        except UnicodeDecodeError:
            continue
    if not raw_text:
        raise ValueError(f"sample file '{sample_file}' could not be decoded as UTF-8")
    payload = json.loads(raw_text.lstrip("\ufeff"))
    messages: list[str] = []
    if isinstance(payload, list):
        for row in payload:
            if isinstance(row, str) and row.strip():
                messages.append(row.strip())
            elif isinstance(row, dict):
                text = str(row.get("message") or "").strip()
                if text:
                    messages.append(text)
    if not messages:
        raise ValueError(f"sample file '{sample_file}' has no usable messages")
    return messages


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (len(ordered) - 1) * (float(p) / 100.0)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    if low == high:
        return float(ordered[low])
    frac = rank - low
    return float(ordered[low] * (1.0 - frac) + ordered[high] * frac)


def _build_hist(values: list[str]) -> dict[str, int]:
    hist: dict[str, int] = {}
    for value in values:
        key = str(value or "").strip() or "UNKNOWN"
        hist[key] = hist.get(key, 0) + 1
    return dict(sorted(hist.items(), key=lambda item: (-item[1], item[0])))


def _score_quality(
    *,
    decision: str,
    llm_status: str,
    narrative_len: int,
    reason_code: str | None,
    error: str | None,
) -> float:
    if error:
        return 0.0
    score = 0.0
    if str(decision).upper() == "APPROVE":
        score += 0.4
    llm = str(llm_status).upper()
    if llm == "OK":
        score += 0.3
    elif llm == "DEGRADED":
        score += 0.15
    if int(narrative_len) >= 24:
        score += 0.2
    elif int(narrative_len) > 0:
        score += 0.1
    if not str(reason_code or "").strip():
        score += 0.1
    return round(min(score, 1.0), 3)


@contextmanager
def _temporary_env(overrides: dict[str, str]):
    before: dict[str, str | None] = {}
    for key, value in overrides.items():
        before[key] = os.environ.get(key)
        os.environ[key] = str(value)
    try:
        yield
    finally:
        for key, old_value in before.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value


async def _run_one(
    *,
    profile_name: str,
    round_index: int,
    sample_index: int,
    message: str,
    env_overrides: dict[str, str],
    session_prefix: str,
) -> SampleRun:
    request_id = f"diag-ab-{profile_name}-{round_index}-{sample_index}-{int(time.time() * 1000)}"
    session_id = f"{session_prefix}-{profile_name}-{round_index}-{sample_index}"
    request = _FakeRequest(request_id=request_id)
    request_body = ChatRequest(message=message, session_id=session_id)
    started = time.perf_counter()
    with _temporary_env(env_overrides):
        try:
            response = await handle_chat(request_body, request, _NoopMetrics())
            latency_ms = (time.perf_counter() - started) * 1000.0
            audit_refs = dict(response.audit_refs or {})
            llm = dict(audit_refs.get("llm") or {})
            narrative = str(response.narrative or "")
            quality_payload = dict(response.quality or {})
            reason_code = str(response.reason_code or "").strip() or None
            run = SampleRun(
                profile=profile_name,
                round_index=round_index,
                sample_index=sample_index,
                message=message,
                ok=(str(response.decision or "").upper() == "APPROVE" and bool(narrative.strip())),
                latency_ms=round(latency_ms, 3),
                decision=str(response.decision or ""),
                reason_code=reason_code,
                quality_stage=str(quality_payload.get("stage") or ""),
                quality_score=0.0,
                narrative_len=len(narrative.strip()),
                llm_status=str(llm.get("status") or ""),
                provider=str(llm.get("provider") or ""),
                model_id=str(llm.get("model_id") or ""),
                route_target=str(llm.get("route_target") or ""),
                route_reason_code=str(llm.get("route_reason_code") or ""),
                error=None,
            )
            run.quality_score = _score_quality(
                decision=run.decision,
                llm_status=run.llm_status,
                narrative_len=run.narrative_len,
                reason_code=run.reason_code,
                error=None,
            )
            return run
        except Exception as exc:  # pragma: no cover - runtime guard
            latency_ms = (time.perf_counter() - started) * 1000.0
            return SampleRun(
                profile=profile_name,
                round_index=round_index,
                sample_index=sample_index,
                message=message,
                ok=False,
                latency_ms=round(latency_ms, 3),
                decision="REJECT",
                reason_code=None,
                quality_stage="S_ERROR",
                quality_score=0.0,
                narrative_len=0,
                llm_status="",
                provider="",
                model_id="",
                route_target="",
                route_reason_code="",
                error=str(exc),
            )


def _summarize_profile(rows: list[SampleRun]) -> dict[str, Any]:
    if not rows:
        return {
            "sample_count": 0,
            "ok_rate": 0.0,
            "approve_rate": 0.0,
            "quality_mean": 0.0,
            "latency_mean_ms": 0.0,
            "latency_p95_ms": 0.0,
            "route_target_hist": {},
            "provider_hist": {},
            "llm_status_hist": {},
            "errors": 0,
        }
    total = len(rows)
    ok_count = len([row for row in rows if row.ok])
    approve_count = len([row for row in rows if row.decision.upper() == "APPROVE"])
    error_count = len([row for row in rows if row.error])
    latency_values = [float(row.latency_ms) for row in rows]
    quality_values = [float(row.quality_score) for row in rows]
    return {
        "sample_count": total,
        "ok_rate": round(ok_count / total, 4),
        "approve_rate": round(approve_count / total, 4),
        "quality_mean": round(statistics.fmean(quality_values), 4),
        "latency_mean_ms": round(statistics.fmean(latency_values), 3),
        "latency_p95_ms": round(float(_percentile(latency_values, 95) or 0.0), 3),
        "route_target_hist": _build_hist([row.route_target for row in rows]),
        "provider_hist": _build_hist([row.provider for row in rows]),
        "llm_status_hist": _build_hist([row.llm_status for row in rows]),
        "errors": error_count,
    }


def _summary_table(report: dict[str, Any]) -> str:
    profiles = report.get("profile_summary") or {}
    lines = [
        "| profile | samples | ok_rate | approve_rate | quality_mean | latency_mean_ms | latency_p95_ms |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name in sorted(profiles):
        row = profiles[name]
        lines.append(
            f"| {name} | {row.get('sample_count', 0)} | {row.get('ok_rate', 0)} | "
            f"{row.get('approve_rate', 0)} | {row.get('quality_mean', 0)} | "
            f"{row.get('latency_mean_ms', 0)} | {row.get('latency_p95_ms', 0)} |"
        )
    return "\n".join(lines)


def _write_markdown_report(*, report: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Chat A/B Diagnostic Report",
        "",
        f"- generated_at: {report.get('generated_at')}",
        f"- profiles: {', '.join(sorted((report.get('profiles') or {}).keys()))}",
        f"- repeat: {report.get('repeat')}",
        f"- samples_per_round: {report.get('sample_count_per_round')}",
        "",
        "## Summary",
        "",
        _summary_table(report),
        "",
    ]
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _default_output_paths() -> tuple[Path, Path]:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = ROOT / "docs" / "60_trace"
    return (
        base / f"HOST_CHAT_AB_REPORT_{stamp}.json",
        base / f"HOST_CHAT_AB_REPORT_{stamp}.md",
    )


async def _run_benchmark(
    *,
    profile_envs: dict[str, dict[str, str]],
    messages: list[str],
    repeat: int,
    session_prefix: str,
) -> list[SampleRun]:
    runs: list[SampleRun] = []
    for round_index in range(1, repeat + 1):
        for sample_index, message in enumerate(messages, start=1):
            for profile_name, env_overrides in profile_envs.items():
                row = await _run_one(
                    profile_name=profile_name,
                    round_index=round_index,
                    sample_index=sample_index,
                    message=message,
                    env_overrides=env_overrides,
                    session_prefix=session_prefix,
                )
                runs.append(row)
                print(
                    f"[{profile_name}] round={round_index:02d} sample={sample_index:02d} "
                    f"ok={row.ok} latency_ms={row.latency_ms:.1f} "
                    f"llm={row.provider}/{row.model_id} route={row.route_target or '-'} "
                    f"reason={row.reason_code or row.error or '-'}"
                )
    return runs


def _resolve_profile_env(
    *,
    preset_name: str,
    cli_overrides: list[str],
    disable_retrieval: bool,
) -> dict[str, str]:
    preset = dict(PROFILE_PRESETS.get(preset_name, {}))
    preset.update(_parse_kv_overrides(cli_overrides))
    if disable_retrieval:
        preset["HOST_ENABLE_RETRIEVAL"] = "false"
    return preset


def main() -> int:
    parser = argparse.ArgumentParser(description="Run in-process chat A/B benchmark across two profiles.")
    parser.add_argument("--profile-a", choices=sorted(PROFILE_PRESETS.keys()), default="host8b")
    parser.add_argument("--profile-b", choices=sorted(PROFILE_PRESETS.keys()), default="edge3b")
    parser.add_argument("--profile-a-name", default="A")
    parser.add_argument("--profile-b-name", default="B")
    parser.add_argument("--profile-a-env", action="append", default=[], help="Extra env override KEY=VALUE")
    parser.add_argument("--profile-b-env", action="append", default=[], help="Extra env override KEY=VALUE")
    parser.add_argument("--sample-file", default=None, help="JSON file with message list")
    parser.add_argument("--repeat", type=int, default=1, help="Rounds per message set")
    parser.add_argument("--session-prefix", default="diag-chat-ab")
    parser.add_argument("--disable-retrieval", action="store_true", help="Force HOST_ENABLE_RETRIEVAL=false")
    parser.add_argument("--load-dotenv", action="store_true", help="Load root .env before benchmark")
    parser.add_argument("--output-json", default="", help="Output JSON report path")
    parser.add_argument("--output-md", default="", help="Output Markdown report path")
    args = parser.parse_args()

    if args.repeat <= 0:
        raise SystemExit("--repeat must be > 0")

    if args.load_dotenv:
        _load_dotenv(ROOT / ".env")

    messages = _load_messages(args.sample_file)
    profile_envs = {
        str(args.profile_a_name): _resolve_profile_env(
            preset_name=str(args.profile_a),
            cli_overrides=list(args.profile_a_env),
            disable_retrieval=bool(args.disable_retrieval),
        ),
        str(args.profile_b_name): _resolve_profile_env(
            preset_name=str(args.profile_b),
            cli_overrides=list(args.profile_b_env),
            disable_retrieval=bool(args.disable_retrieval),
        ),
    }

    runs = asyncio.run(
        _run_benchmark(
            profile_envs=profile_envs,
            messages=messages,
            repeat=int(args.repeat),
            session_prefix=f"{str(args.session_prefix)}-{int(time.time() * 1000)}",
        )
    )

    profile_summary: dict[str, Any] = {}
    for profile_name in profile_envs:
        profile_rows = [row for row in runs if row.profile == profile_name]
        profile_summary[profile_name] = _summarize_profile(profile_rows)

    output_json_default, output_md_default = _default_output_paths()
    output_json = Path(str(args.output_json).strip()) if str(args.output_json).strip() else output_json_default
    output_md = Path(str(args.output_md).strip()) if str(args.output_md).strip() else output_md_default
    output_json.parent.mkdir(parents=True, exist_ok=True)

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "profiles": profile_envs,
        "repeat": int(args.repeat),
        "sample_count_per_round": len(messages),
        "profile_summary": profile_summary,
        "runs": [asdict(row) for row in runs],
    }
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_markdown_report(report=report, output_path=output_md)

    print("\n== A/B Summary ==")
    print(_summary_table(report))
    print(f"\njson_report: {output_json}")
    print(f"markdown_report: {output_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
