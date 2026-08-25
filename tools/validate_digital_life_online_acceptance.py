"""Read-only online acceptance check for the digital-life main loop.

This validator is intentionally non-invasive: it only reads the running
wall-clock soak files and GET-only observability/host endpoints. It can be run
while a 24h soak is still in progress.
"""

from __future__ import annotations

import argparse
import json
import time
from host_brain.core.ids import now_ms as _now_ms
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


EXPECTED_ORGAN_COUNT = 23
DEFAULT_RUN_DIR = Path(
    "artifacts/codex_runs/real-24h-wall-clock-20260519/run"
)
REQUIRED_CORE_TICKS = {
    "body_sense_tick",
    "emotion_decay_tick",
    "inner_world_monologue_tick",
    "inner_world_numeric_tick",
    "silence_tick",
}


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _read_jsonl_tail(path: Path, limit: int = 12) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            rows.append({"_parse_error": line[:240]})
    return rows


def _http_get_json(url: str, timeout: float = 20.0) -> tuple[dict[str, Any] | None, str | None]:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return json.loads(body), None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        return None, f"HTTP {exc.code}: {detail}"
    except Exception as exc:  # noqa: BLE001 - CLI validator should report all probe failures.
        return None, str(exc)


def _http_get_text(url: str, timeout: float = 20.0) -> tuple[str | None, str | None]:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace"), None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        return None, f"HTTP {exc.code}: {detail}"
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def _get(data: Any, path: str, default: Any = None) -> Any:
    current = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def _boolish(value: Any) -> bool:
    return value is True or str(value).lower() in {"true", "1", "yes", "ok", "healthy"}


@dataclass
class Check:
    name: str
    status: str
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)


def _latest_checkpoint(rows: list[dict[str, Any]]) -> dict[str, Any]:
    for row in reversed(rows):
        if row.get("event_type") == "idle_tick":
            return row
    return rows[-1] if rows else {}


def _core_tick_status(latest: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    task_results = _get(latest, "payload.task_results", {}) or {}
    present = set(task_results.keys())
    missing = sorted(REQUIRED_CORE_TICKS - present)
    bad = {
        task_id: data.get("status")
        for task_id, data in task_results.items()
        if str(data.get("status") or "").upper() != "OK"
    }
    return not missing and not bad, {
        "present": sorted(present),
        "missing": missing,
        "bad": bad,
    }


def _checkpoint_advancing(rows: list[dict[str, Any]]) -> tuple[bool, dict[str, Any]]:
    idle_rows = [row for row in rows if row.get("event_type") == "idle_tick"]
    tick_indexes = [int(row.get("tick_index", -1)) for row in idle_rows if row.get("tick_index") is not None]
    elapsed = [float(row.get("elapsed_seconds", -1)) for row in idle_rows if row.get("elapsed_seconds") is not None]
    increasing_ticks = len(tick_indexes) >= 2 and tick_indexes == sorted(set(tick_indexes))
    increasing_elapsed = len(elapsed) >= 2 and elapsed == sorted(elapsed)
    return increasing_ticks and increasing_elapsed, {
        "tick_indexes": tick_indexes,
        "elapsed_seconds": elapsed,
    }


def _write_report(report_path: Path, result: dict[str, Any]) -> None:
    summary = result["summary"]
    checks = result["checks"]
    lines = [
        "# Digital Life Online Acceptance",
        "",
        f"- verdict: {summary['verdict']}",
        f"- generated_at_ms: {summary['generated_at_ms']}",
        f"- run_status: {summary.get('run_status')}",
        f"- progress: {summary.get('progress_percent')}%",
        f"- idle_tick_count: {summary.get('idle_tick_count')}",
        f"- organ_coverage: {summary.get('registered_organ_count')}/{EXPECTED_ORGAN_COUNT} registered, "
        f"{summary.get('last_mapped_organ_count')}/{EXPECTED_ORGAN_COUNT} mapped",
        f"- sample_gaps: {', '.join(summary.get('sample_gaps') or []) or 'none'}",
        "",
        "## Checks",
        "",
    ]
    for check in checks:
        lines.append(f"- {check['status']} {check['name']}: {check['detail']}")
    lines.extend(
        [
            "",
            "## Evidence Files",
            "",
            f"- evidence_json: {result['paths']['evidence_json']}",
            f"- report_markdown: {result['paths']['report_markdown']}",
            "",
        ]
    )
    report_path.write_text("\n".join(lines), encoding="utf-8")


def run_acceptance(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.run_dir).resolve()
    summary_path = run_dir / "summary.json"
    checkpoints_path = run_dir / "checkpoints.jsonl"
    summary = _read_json(summary_path)
    checkpoints_tail = _read_jsonl_tail(checkpoints_path, limit=args.checkpoint_tail)
    latest = _latest_checkpoint(checkpoints_tail)
    latest_snapshot = latest.get("snapshot") or _get(latest, "payload.snapshot", {}) or {}
    latest_ts = int(latest.get("ts_ms") or summary.get("updated_at_ms") or 0)
    generated_at_ms = _now_ms()
    tick_interval_seconds = float(summary.get("tick_interval_seconds") or 300)
    latest_age_seconds = (generated_at_ms - latest_ts) / 1000 if latest_ts else None

    endpoints = {
        "wall_clock_soak": f"{args.obs_base}/obs/digital-life/wall-clock-soak",
        "runtime_snapshot": f"{args.obs_base}/obs/runtime-snapshot",
        "life_body": f"{args.obs_base}/obs/life-body?window_minutes={args.window_minutes}",
        "digital_life_overview": f"{args.obs_base}/obs/digital-life/overview-snapshot?window_minutes={args.window_minutes}",
        "humanoid_loop": f"{args.obs_base}/obs/input/humanoid-loop-stats?window_minutes={args.window_minutes}",
        "memory_stats": f"{args.obs_base}/obs/memory/stats?window_minutes={args.window_minutes}",
        "vts_stats": f"{args.obs_base}/obs/vts/stats?window_minutes={args.window_minutes}",
    }
    payloads: dict[str, Any] = {}
    endpoint_errors: dict[str, str] = {}
    for name, url in endpoints.items():
        payload, error = _http_get_json(url, timeout=args.timeout)
        if error:
            endpoint_errors[name] = error
        else:
            payloads[name] = payload

    host_text, host_error = _http_get_text(
        f"{args.host_base}/prompt-config/templates",
        timeout=args.timeout,
    )
    if host_error:
        endpoint_errors["host_prompt_templates"] = host_error

    frontend_evidence = {}
    if args.frontend_evidence:
        path = Path(args.frontend_evidence)
        if path.exists():
            frontend_evidence = _read_json(path)

    checks: list[Check] = []

    running = (
        str(summary.get("run_status") or summary.get("status")).upper() == "RUNNING"
        and summary.get("alive") is True
        and summary.get("is_advancing") is True
    )
    checks.append(Check(
        "soak_running",
        "PASS" if running else "FAIL",
        "wall-clock soak is running, alive, and advancing" if running else "soak is not in a running/advancing state",
        {
            "status": summary.get("status"),
            "run_status": summary.get("run_status"),
            "alive": summary.get("alive"),
            "is_advancing": summary.get("is_advancing"),
        },
    ))

    fresh_limit = tick_interval_seconds * 2 + 120
    fresh = latest_age_seconds is not None and latest_age_seconds <= fresh_limit
    checks.append(Check(
        "checkpoint_freshness",
        "PASS" if fresh else "FAIL",
        f"latest checkpoint age {latest_age_seconds:.1f}s <= {fresh_limit:.1f}s" if latest_age_seconds is not None else "latest checkpoint timestamp missing",
        {"latest_age_seconds": latest_age_seconds, "fresh_limit_seconds": fresh_limit},
    ))

    advancing, advancing_evidence = _checkpoint_advancing(checkpoints_tail)
    checks.append(Check(
        "checkpoint_advancing",
        "PASS" if advancing else "FAIL",
        "recent idle ticks are monotonically advancing" if advancing else "recent idle ticks are not advancing monotonically",
        advancing_evidence,
    ))

    core_tick_ok, core_tick_evidence = _core_tick_status(latest)
    checks.append(Check(
        "core_tick_tasks",
        "PASS" if core_tick_ok else "FAIL",
        "required background tick tasks are present and OK" if core_tick_ok else "one or more required background tick tasks failed or are missing",
        core_tick_evidence,
    ))

    organs_ok = (
        int(summary.get("registered_organ_count") or 0) >= EXPECTED_ORGAN_COUNT
        and int(summary.get("last_mapped_organ_count") or 0) >= EXPECTED_ORGAN_COUNT
        and int(latest_snapshot.get("registered_organ_count") or 0) >= EXPECTED_ORGAN_COUNT
        and int(latest_snapshot.get("last_mapped_organ_count") or 0) >= EXPECTED_ORGAN_COUNT
    )
    checks.append(Check(
        "organ_coverage",
        "PASS" if organs_ok else "FAIL",
        "23 organ families are registered and mapped in summary and latest checkpoint" if organs_ok else "organ registration/mapping is incomplete",
        {
            "summary_registered": summary.get("registered_organ_count"),
            "summary_mapped": summary.get("last_mapped_organ_count"),
            "checkpoint_registered": latest_snapshot.get("registered_organ_count"),
            "checkpoint_mapped": latest_snapshot.get("last_mapped_organ_count"),
        },
    ))

    scheduler_ok = (
        str(summary.get("scheduler_status") or "").upper() == "HEALTHY"
        and str(summary.get("tick_health") or "").lower() == "fresh"
    )
    checks.append(Check(
        "scheduler_health",
        "PASS" if scheduler_ok else "FAIL",
        "scheduler is HEALTHY and tick health is fresh" if scheduler_ok else "scheduler/tick health is degraded",
        {"scheduler_status": summary.get("scheduler_status"), "tick_health": summary.get("tick_health")},
    ))

    endpoints_ok = not endpoint_errors and len(payloads) == len(endpoints)
    checks.append(Check(
        "read_only_endpoints",
        "PASS" if endpoints_ok else "FAIL",
        "all read-only acceptance endpoints returned JSON" if endpoints_ok else "one or more read-only endpoints failed",
        {"errors": endpoint_errors, "ok": sorted(payloads.keys())},
    ))

    runtime = payloads.get("runtime_snapshot") or {}
    runtime_context_ok = all(
        isinstance(runtime.get(key), dict) and runtime.get(key)
        for key in ("emotion_context", "bond_state", "motive_state", "silence_state", "offline_state")
    )
    runtime_identity_ok = bool(runtime.get("persona_self_id") or runtime.get("self_id") or summary.get("persona_self_id"))
    checks.append(Check(
        "persona_context",
        "PASS" if runtime_context_ok and runtime_identity_ok else "FAIL",
        "identity, emotion, bond, motive, silence, and offline-state context are readable"
        if runtime_context_ok and runtime_identity_ok
        else "runtime persona context is incomplete",
        {
            "persona_self_id": runtime.get("persona_self_id") or runtime.get("self_id") or summary.get("persona_self_id"),
            "emotion_present": bool(runtime.get("emotion_context")),
            "bond_present": bool(runtime.get("bond_state")),
            "motive_present": bool(runtime.get("motive_state")),
            "silence_present": bool(runtime.get("silence_state")),
            "offline_present": bool(runtime.get("offline_state")),
        },
    ))

    life_body = payloads.get("life_body") or {}
    derived = life_body.get("derived") or {}
    life_body_ok = _boolish(derived.get("hasRuntimeSnapshot")) and bool(life_body.get("model"))
    checks.append(Check(
        "life_body_projection",
        "PASS" if life_body_ok else "FAIL",
        "life-body projection can derive the organism view from runtime and lineage data"
        if life_body_ok
        else "life-body projection is missing runtime/model data",
        {
            "hasRuntimeSnapshot": derived.get("hasRuntimeSnapshot"),
            "hasMemoryData": derived.get("hasMemoryData"),
            "hasMemorySamples": derived.get("hasMemorySamples"),
            "recentSessionCount": derived.get("recentSessionCount"),
            "memoryLineageCount": derived.get("memoryLineageCount"),
            "tickLineageCount": derived.get("tickLineageCount"),
        },
    ))

    frontend_ok = True
    if frontend_evidence:
        required_text = frontend_evidence.get("requiredText") or {}
        frontend_ok = (
            frontend_evidence.get("overflowX") is False
            and frontend_evidence.get("loadingPlaceholderVisible") is False
            and all(required_text.values())
        )
        checks.append(Check(
            "frontend_consistency",
            "PASS" if frontend_ok else "FAIL",
            "front-end digital-life page displays the online main-chain facts"
            if frontend_ok
            else "front-end page does not match required online facts",
            frontend_evidence,
        ))

    memory_data = payloads.get("memory_stats") or {}
    humanoid = payloads.get("humanoid_loop") or {}
    sample_gaps = []
    if not _boolish(derived.get("hasMemorySamples")):
        sample_gaps.append("memory_samples")
    if int(humanoid.get("total_gateway_requests") or 0) <= 0:
        sample_gaps.append("gateway_request_samples")

    if sample_gaps:
        checks.append(Check(
            "sample_gaps",
            "INFO",
            "interfaces are wired, but some long-loop proof points need real traffic samples",
            {"sample_gaps": sample_gaps, "memory_summary": memory_data, "humanoid_loop": humanoid},
        ))

    blocking_failures = [check for check in checks if check.status == "FAIL"]
    verdict = "ONLINE_PASS_WITH_SAMPLE_GAPS" if not blocking_failures and sample_gaps else (
        "ONLINE_PASS" if not blocking_failures else "ONLINE_FAIL"
    )

    result: dict[str, Any] = {
        "summary": {
            "verdict": verdict,
            "generated_at_ms": generated_at_ms,
            "run_dir": str(run_dir),
            "run_status": summary.get("run_status") or summary.get("status"),
            "progress_ratio": summary.get("progress_ratio"),
            "progress_percent": round(float(summary.get("progress_ratio") or 0) * 100, 2),
            "elapsed_seconds": summary.get("elapsed_seconds"),
            "idle_tick_count": summary.get("idle_tick_count"),
            "checkpoint_count": summary.get("checkpoint_count"),
            "registered_organ_count": summary.get("registered_organ_count"),
            "last_mapped_organ_count": summary.get("last_mapped_organ_count"),
            "scheduler_status": summary.get("scheduler_status"),
            "tick_health": summary.get("tick_health"),
            "latest_checkpoint_age_seconds": latest_age_seconds,
            "sample_gaps": sample_gaps,
        },
        "checks": [
            {
                "name": check.name,
                "status": check.status,
                "detail": check.detail,
                "evidence": check.evidence,
            }
            for check in checks
        ],
        "inputs": {
            "summary": summary,
            "latest_checkpoint": latest,
            "endpoint_payloads": payloads,
            "endpoint_errors": endpoint_errors,
            "host_prompt_templates_bytes": len(host_text or ""),
            "frontend_evidence": frontend_evidence,
        },
        "paths": {},
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", default=str(DEFAULT_RUN_DIR))
    parser.add_argument("--obs-base", default="http://127.0.0.1:18082")
    parser.add_argument("--host-base", default="http://127.0.0.1:18080")
    parser.add_argument("--window-minutes", type=int, default=360)
    parser.add_argument("--checkpoint-tail", type=int, default=12)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--frontend-evidence", default="")
    parser.add_argument("--out-dir", default="artifacts/digital_life_online_acceptance")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    result = run_acceptance(args)

    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    evidence_path = out_dir / f"online-acceptance-{stamp}.json"
    report_path = out_dir / f"online-acceptance-{stamp}.md"
    result["paths"] = {
        "evidence_json": str(evidence_path),
        "report_markdown": str(report_path),
    }
    evidence_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_report(report_path, result)

    print(json.dumps({
        "verdict": result["summary"]["verdict"],
        "evidence_json": str(evidence_path),
        "report_markdown": str(report_path),
        "failed_checks": [
            check["name"] for check in result["checks"] if check["status"] == "FAIL"
        ],
        "sample_gaps": result["summary"].get("sample_gaps", []),
    }, ensure_ascii=False, indent=2))
    return 0 if result["summary"]["verdict"] != "ONLINE_FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
