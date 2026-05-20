from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


DEFAULT_EXPECTED_ORGAN_COUNT = 23
DEFAULT_EXPECTED_HOURS = 24.0


def review_wall_clock_soak(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(str(getattr(args, "run_dir", "") or "")).resolve()
    out_dir = Path(str(getattr(args, "out_dir", "") or "")).resolve() if str(getattr(args, "out_dir", "") or "").strip() else run_dir.parent / "review"
    write_outputs = bool(getattr(args, "write_outputs", True))
    if write_outputs:
        out_dir.mkdir(parents=True, exist_ok=True)

    summary_path = run_dir / "summary.json"
    checkpoints_path = run_dir / "checkpoints.jsonl"
    stderr_path = _resolve_log_path(run_dir, str(getattr(args, "stderr", "") or ""), "stderr.log")
    stdout_path = _resolve_log_path(run_dir, str(getattr(args, "stdout", "") or ""), "stdout.log")

    now_ms = int(time.time() * 1000)
    summary = _read_json(summary_path)
    checkpoints = _read_jsonl(checkpoints_path)
    stderr_text = _tail_text(stderr_path, max_chars=int(getattr(args, "log_tail_chars", 20_000) or 20_000))
    stdout_text = _tail_text(stdout_path, max_chars=int(getattr(args, "log_tail_chars", 20_000) or 20_000))
    pid = int(getattr(args, "pid", 0) or 0)
    process_running = _is_process_running(pid) if pid > 0 else None
    expected_seconds = _expected_seconds(args, summary)
    expected_organ_count = int(getattr(args, "expected_organ_count", DEFAULT_EXPECTED_ORGAN_COUNT) or DEFAULT_EXPECTED_ORGAN_COUNT)
    stale_seconds = _stale_seconds(args, summary)
    require_complete = bool(getattr(args, "require_complete", False))

    metrics = _metrics(
        summary=summary,
        checkpoints=checkpoints,
        now_ms=now_ms,
        expected_seconds=expected_seconds,
        stale_seconds=stale_seconds,
        process_running=process_running,
    )
    checks = _checks(
        run_dir=run_dir,
        summary_path=summary_path,
        checkpoints_path=checkpoints_path,
        stderr_text=stderr_text,
        summary=summary,
        checkpoints=checkpoints,
        metrics=metrics,
        expected_seconds=expected_seconds,
        expected_organ_count=expected_organ_count,
        require_complete=require_complete,
        process_running=process_running,
    )
    verdict = _verdict(checks=checks, run_status=str(summary.get("run_status") or ""), require_complete=require_complete)
    review_summary = {
        "verdict": verdict,
        "evaluation_mode": "real_wall_clock_digital_life_soak_review",
        "run_dir": str(run_dir),
        "out_dir": str(out_dir),
        "run_id": str(summary.get("run_id") or ""),
        "run_status": str(summary.get("run_status") or ""),
        "soak_status": str(summary.get("status") or ""),
        "process_pid": pid or None,
        "process_running": process_running,
        "expected_seconds": expected_seconds,
        "elapsed_seconds": metrics["elapsed_seconds"],
        "progress_ratio": metrics["progress_ratio"],
        "idle_tick_count": metrics["idle_tick_count"],
        "checkpoint_count": metrics["checkpoint_count"],
        "idle_checkpoint_count": metrics["idle_checkpoint_count"],
        "latest_checkpoint_age_seconds": metrics["latest_checkpoint_age_seconds"],
        "stale_seconds": stale_seconds,
        "scheduler_status": str(summary.get("scheduler_status") or ""),
        "tick_health": str(summary.get("tick_health") or ""),
        "registered_organ_count": int(summary.get("registered_organ_count") or 0),
        "last_mapped_organ_count": int(summary.get("last_mapped_organ_count") or 0),
        "checks": checks,
        "failed_checks": [key for key, value in checks.items() if not bool(value)],
    }
    details = {
        "summary": review_summary,
        "source_summary": summary,
        "metrics": metrics,
        "paths": {
            "summary": str(summary_path),
            "checkpoints": str(checkpoints_path),
            "stdout": str(stdout_path) if stdout_path.exists() else "",
            "stderr": str(stderr_path) if stderr_path.exists() else "",
        },
        "stderr_tail": stderr_text,
        "stdout_tail": stdout_text,
        "latest_checkpoints": checkpoints[-10:],
    }
    if write_outputs:
        _write_json(out_dir / "review_summary.json", review_summary)
        _write_json(out_dir / "review_details.json", details)
        (out_dir / "review_report.md").write_text(_markdown_report(review_summary, details), encoding="utf-8")
    return {"summary": review_summary, "details": details}


def _metrics(
    *,
    summary: Mapping[str, Any],
    checkpoints: Sequence[Mapping[str, Any]],
    now_ms: int,
    expected_seconds: float,
    stale_seconds: float,
    process_running: bool | None,
) -> dict[str, Any]:
    updated_at_ms = int(summary.get("updated_at_ms") or 0)
    started_at_ms = int(summary.get("started_at_ms") or 0)
    elapsed_seconds = float(summary.get("elapsed_seconds") or 0.0)
    if elapsed_seconds <= 0 and started_at_ms > 0:
        elapsed_seconds = max(0.0, (now_ms - started_at_ms) / 1000.0)
    progress_ratio = float(summary.get("progress_ratio") or 0.0)
    if progress_ratio <= 0 and expected_seconds > 0:
        progress_ratio = min(1.0, elapsed_seconds / expected_seconds)
    latest_checkpoint_ts_ms = max((int(item.get("ts_ms") or 0) for item in checkpoints), default=0)
    idle_checkpoint_count = sum(1 for item in checkpoints if str(item.get("event_type") or "") == "idle_tick")
    checkpoint_count = len(list(checkpoints))
    latest_age = max(0.0, (now_ms - latest_checkpoint_ts_ms) / 1000.0) if latest_checkpoint_ts_ms else None
    updated_age = max(0.0, (now_ms - updated_at_ms) / 1000.0) if updated_at_ms else None
    return {
        "started_at_ms": started_at_ms,
        "updated_at_ms": updated_at_ms,
        "updated_age_seconds": updated_age,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "progress_ratio": round(min(1.0, max(0.0, progress_ratio)), 6),
        "checkpoint_count": checkpoint_count,
        "idle_checkpoint_count": idle_checkpoint_count,
        "latest_checkpoint_ts_ms": latest_checkpoint_ts_ms,
        "latest_checkpoint_age_seconds": round(latest_age, 3) if latest_age is not None else None,
        "idle_tick_count": int(summary.get("idle_tick_count") or 0),
        "foreground_turn_count": int(summary.get("foreground_turn_count") or 0),
        "stale_seconds": stale_seconds,
        "process_running": process_running,
    }


def _checks(
    *,
    run_dir: Path,
    summary_path: Path,
    checkpoints_path: Path,
    stderr_text: str,
    summary: Mapping[str, Any],
    checkpoints: Sequence[Mapping[str, Any]],
    metrics: Mapping[str, Any],
    expected_seconds: float,
    expected_organ_count: int,
    require_complete: bool,
    process_running: bool | None,
) -> dict[str, bool]:
    run_status = str(summary.get("run_status") or "")
    idle_tick_count = int(summary.get("idle_tick_count") or 0)
    checkpoint_count = int(metrics.get("checkpoint_count") or 0)
    idle_checkpoint_count = int(metrics.get("idle_checkpoint_count") or 0)
    latest_age = metrics.get("latest_checkpoint_age_seconds")
    stale_seconds = float(metrics.get("stale_seconds") or 0.0)
    progress_ratio = float(metrics.get("progress_ratio") or 0.0)
    raw_expected_tick_minimum = int(expected_seconds // max(1.0, float(summary.get("tick_interval_seconds") or 300.0)))
    # Wall-clock scheduling is intentionally tolerant: host sleep, process wake-up
    # jitter, and short start/stop windows can make a healthy 24h run land below
    # the exact expected_seconds / tick_interval count. Staleness and final PASS
    # checks still guard against a stuck runtime.
    expected_tick_minimum = max(1, int(raw_expected_tick_minimum * 0.95))
    running_ok = run_status == "RUNNING" and not require_complete
    complete_ok = run_status == "COMPLETED" and str(summary.get("status") or "") == "PASS"
    stderr_lower = stderr_text.lower()
    return {
        "run_dir_exists": run_dir.exists() and run_dir.is_dir(),
        "summary_file_present": summary_path.exists(),
        "checkpoints_file_present": checkpoints_path.exists(),
        "evaluation_mode_is_wall_clock": str(summary.get("evaluation_mode") or "") == "real_wall_clock_digital_life_soak",
        "run_status_allowed": complete_ok or running_ok,
        "no_reported_error": not bool(dict(summary.get("error") or {})),
        "stderr_has_no_traceback": "traceback" not in stderr_lower and "error:" not in stderr_lower,
        "summary_checkpoint_count_matches_file": int(summary.get("checkpoint_count") or 0) == checkpoint_count,
        "idle_tick_count_matches_checkpoints": idle_tick_count == idle_checkpoint_count,
        "foreground_turn_recorded": int(summary.get("foreground_turn_count") or 0) >= 1,
        "registered_organs_present": int(summary.get("registered_organ_count") or 0) >= expected_organ_count,
        "mapped_organs_present": int(summary.get("last_mapped_organ_count") or 0) >= expected_organ_count,
        "scheduler_healthy": str(summary.get("scheduler_status") or "") == "HEALTHY",
        "tick_health_fresh": str(summary.get("tick_health") or "") == "fresh",
        "runtime_advancing": bool(summary.get("is_advancing")),
        "running_checkpoint_not_stale": complete_ok
        or (latest_age is not None and float(latest_age) <= stale_seconds),
        "running_process_alive_when_pid_provided": process_running is not False or complete_ok,
        "completed_reaches_expected_duration": (not require_complete and run_status == "RUNNING")
        or (complete_ok and float(metrics.get("elapsed_seconds") or 0.0) >= expected_seconds * 0.995),
        "completed_reaches_expected_ticks": (not require_complete and run_status == "RUNNING")
        or (complete_ok and idle_tick_count >= expected_tick_minimum),
        "progress_ratio_in_range": 0.0 <= progress_ratio <= 1.0,
    }


def _verdict(*, checks: Mapping[str, bool], run_status: str, require_complete: bool) -> str:
    failed = [key for key, value in checks.items() if not bool(value)]
    if failed:
        return "FAIL"
    if str(run_status or "") == "RUNNING" and not require_complete:
        return "RUNNING"
    return "PASS"


def _expected_seconds(args: argparse.Namespace, summary: Mapping[str, Any]) -> float:
    raw = getattr(args, "expected_seconds", None)
    if raw is not None:
        return max(0.0, float(raw or 0.0))
    summary_target = float(summary.get("target_seconds") or 0.0)
    if summary_target > 0:
        return summary_target
    return max(0.0, float(getattr(args, "expected_hours", DEFAULT_EXPECTED_HOURS) or DEFAULT_EXPECTED_HOURS) * 3600.0)


def _stale_seconds(args: argparse.Namespace, summary: Mapping[str, Any]) -> float:
    raw = getattr(args, "stale_seconds", None)
    if raw is not None:
        return max(1.0, float(raw or 1.0))
    interval = float(summary.get("tick_interval_seconds") or 300.0)
    return max(60.0, interval * 2.0 + 120.0)


def _resolve_log_path(run_dir: Path, raw: str, filename: str) -> Path:
    if raw.strip():
        return Path(raw).resolve()
    parent_path = run_dir.parent / filename
    if parent_path.exists():
        return parent_path
    return run_dir / filename


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return dict(payload or {}) if isinstance(payload, Mapping) else {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            rows.append({"event_type": "MALFORMED_JSONL", "raw": text[:300]})
            continue
        rows.append(dict(payload or {}) if isinstance(payload, Mapping) else {"value": payload})
    return rows


def _tail_text(path: Path, *, max_chars: int) -> str:
    if not path.exists() or not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    limit = max(0, int(max_chars or 0))
    return text[-limit:] if limit and len(text) > limit else text


def _is_process_running(pid: int) -> bool:
    if int(pid or 0) <= 0:
        return False
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {int(pid)}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                check=False,
            )
            rows = list(csv.reader(result.stdout.splitlines()))
            return any(row and str(row[1] if len(row) > 1 else "").strip() == str(int(pid)) for row in rows)
        except Exception:
            return False
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def _markdown_report(summary: Mapping[str, Any], details: Mapping[str, Any]) -> str:
    checks = dict(summary.get("checks") or {})
    lines = [
        "# Wall Clock Soak Review",
        "",
        f"- verdict: `{summary.get('verdict')}`",
        f"- run_status: `{summary.get('run_status')}`",
        f"- progress: `{summary.get('progress_ratio')}`",
        f"- elapsed_seconds: `{summary.get('elapsed_seconds')}` / `{summary.get('expected_seconds')}`",
        f"- idle_tick_count: `{summary.get('idle_tick_count')}`",
        f"- checkpoint_count: `{summary.get('checkpoint_count')}`",
        f"- scheduler_status: `{summary.get('scheduler_status')}`",
        f"- tick_health: `{summary.get('tick_health')}`",
        f"- process_running: `{summary.get('process_running')}`",
        "",
        "## Checks",
        "",
    ]
    for key, value in checks.items():
        lines.append(f"- [{'x' if value else ' '}] `{key}`")
    failed = list(summary.get("failed_checks") or [])
    if failed:
        lines.extend(["", "## Failed", ""])
        for key in failed:
            lines.append(f"- `{key}`")
    stderr_tail = str(details.get("stderr_tail") or "").strip()
    if stderr_tail:
        lines.extend(["", "## Stderr Tail", "", "```text", stderr_tail[-2000:], "```"])
    return "\n".join(lines).rstrip() + "\n"


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review a real wall-clock digital-life soak run.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--pid", type=int, default=0)
    parser.add_argument("--stdout", default="")
    parser.add_argument("--stderr", default="")
    parser.add_argument("--expected-hours", type=float, default=DEFAULT_EXPECTED_HOURS)
    parser.add_argument("--expected-seconds", type=float, default=None)
    parser.add_argument("--expected-organ-count", type=int, default=DEFAULT_EXPECTED_ORGAN_COUNT)
    parser.add_argument("--stale-seconds", type=float, default=None)
    parser.add_argument("--log-tail-chars", type=int, default=20_000)
    parser.add_argument("--require-complete", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(list(argv) if argv is not None else None)
    result = review_wall_clock_soak(args)
    print(json.dumps(result["summary"], ensure_ascii=False, sort_keys=True))
    return 0 if result["summary"].get("verdict") in {"RUNNING", "PASS"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
