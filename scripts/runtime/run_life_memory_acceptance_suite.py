#!/usr/bin/env python
"""Run the LifeMemory acceptance suite across recall, entrypoints, retention, and production ledger health."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.audit.sqlite_ledger import SQLiteLedger  # noqa: E402
from host_brain.memory.life_memory import LifeMemoryConsolidator, LifeMemoryStore, MemorySleepReplayTask  # noqa: E402
from scripts.runtime.audit_life_memory_coverage import build_coverage_report  # noqa: E402
from scripts.runtime.run_life_memory_entrypoint_scenario_batch import (  # noqa: E402
    REQUIRED_ENTRYPOINTS,
    run_entrypoint_scenario_batch,
)
from scripts.runtime.run_life_memory_recall_scenario_batch import run_scenario_batch  # noqa: E402


DEFAULT_DB_PATH = "host_brain/data/audit.sqlite3"
DEFAULT_OUT_DIR = "runtime/host/logs"
_PROTECTED_COMPACTION_RE = re.compile(
    r"失败|失败原因|异常|报错|连不上|被占用|偏好|用户要求|不要|别再|承诺|待办|纠正|不对|错了|"
    r"模型路由|记忆系统|能力|策略|刚才今日推荐|今日推荐的结果|没拿到完成结果"
)


def run_acceptance_suite(
    *,
    db_path: Path,
    include_synthetic: bool = False,
    include_seeded_e2e: bool = False,
    base_url: str = "",
    timeout_sec: float = 240.0,
    trace_timeout_sec: float = 40.0,
    sample_limit: int = 20,
) -> dict[str, Any]:
    started = int(time.time() * 1000)
    checks: list[dict[str, Any]] = []

    recall = run_scenario_batch()
    _add_check(
        checks,
        check_id="deterministic_recall_scenarios",
        passed=bool(recall.get("success")),
        summary={
            "passed_count": recall.get("passed_count"),
            "failed_count": recall.get("failed_count"),
            "case_count": recall.get("case_count"),
        },
        details=recall,
    )

    entrypoints = run_entrypoint_scenario_batch()
    _add_check(
        checks,
        check_id="deterministic_entrypoint_coverage",
        passed=bool(entrypoints.get("success")),
        summary={
            "required_entrypoints": entrypoints.get("required_entrypoints"),
            "summary": entrypoints.get("summary"),
        },
        details=entrypoints,
    )

    production = _run_production_ledger_checks(
        db_path=db_path,
        include_synthetic=include_synthetic,
        sample_limit=sample_limit,
    )
    _add_check(
        checks,
        check_id="production_ledger_coverage",
        passed=bool(production.get("passed")),
        summary=production.get("summary") or {},
        details=production,
    )

    sleep_replay = _run_sleep_replay_dry_run(db_path=db_path)
    _add_check(
        checks,
        check_id="sleep_replay_dry_run",
        passed=bool(sleep_replay.get("passed")),
        summary={
            "proposal_count": sleep_replay.get("proposal_count"),
            "replay_candidate_count": sleep_replay.get("replay_candidate_count"),
            "compaction_group_count": sleep_replay.get("compaction_group_count"),
            "protected_compaction_violation_count": len(sleep_replay.get("protected_compaction_violations") or []),
        },
        details=sleep_replay,
    )

    seeded_e2e: dict[str, Any] | None = None
    if include_seeded_e2e:
        seeded_e2e = _run_seeded_e2e_subprocess(
            base_url=base_url,
            db_path=db_path,
            timeout_sec=timeout_sec,
            trace_timeout_sec=trace_timeout_sec,
        )
        _add_check(
            checks,
            check_id="seeded_lightnovel_memory_e2e",
            passed=bool(seeded_e2e.get("passed")),
            summary={
                "returncode": seeded_e2e.get("returncode"),
                "summary": seeded_e2e.get("summary"),
            },
            details=seeded_e2e,
        )

    report = {
        "schema": "Natsume.life_memory_acceptance_suite.v1",
        "started_ts_ms": started,
        "finished_ts_ms": int(time.time() * 1000),
        "db_path": str(db_path),
        "include_synthetic": bool(include_synthetic),
        "include_seeded_e2e": bool(include_seeded_e2e),
        "success": all(bool(item.get("passed")) for item in checks),
        "check_count": len(checks),
        "passed_count": sum(1 for item in checks if item.get("passed")),
        "failed_count": sum(1 for item in checks if not item.get("passed")),
        "checks": checks,
    }
    if seeded_e2e is not None:
        report["seeded_e2e"] = seeded_e2e
    return report


def _run_production_ledger_checks(*, db_path: Path, include_synthetic: bool, sample_limit: int) -> dict[str, Any]:
    if not db_path.exists():
        return {
            "passed": False,
            "reason_code": "DB_NOT_FOUND",
            "summary": {"db_path": str(db_path)},
        }
    ledger = SQLiteLedger(db_path=db_path)
    try:
        coverage = build_coverage_report(
            ledger._conn,
            include_synthetic=include_synthetic,
            required_entrypoints=REQUIRED_ENTRYPOINTS,
            sample_limit=max(1, int(sample_limit)),
        )
    finally:
        ledger.close()
    summary = dict(coverage.get("summary") or {})
    counts = {
        str(item.get("entrypoint")): int(item.get("count") or 0)
        for item in coverage.get("entrypoint_counts") or []
        if isinstance(item, Mapping)
    }
    missing = [entrypoint for entrypoint in REQUIRED_ENTRYPOINTS if counts.get(entrypoint, 0) <= 0]
    passed = (
        int(summary.get("critical_issue_count") or 0) == 0
        and int(summary.get("collection_critical_issue_count") or 0) == 0
        and int(summary.get("semantic_critical_issue_count") or 0) == 0
        and int(summary.get("entrypoint_critical_issue_count") or 0) == 0
        and not missing
    )
    return {
        "passed": passed,
        "missing_entrypoints": missing,
        "summary": summary,
        "entrypoint_counts": coverage.get("entrypoint_counts") or [],
        "issue_counts": coverage.get("issue_counts") or {},
        "collection_issue_counts": coverage.get("collection_issue_counts") or {},
        "semantic_issue_counts": coverage.get("semantic_issue_counts") or {},
        "entrypoint_issue_counts": coverage.get("entrypoint_issue_counts") or {},
        "issues": coverage.get("issues") or [],
        "collection_issues": coverage.get("collection_issues") or [],
        "semantic_issues": coverage.get("semantic_issues") or [],
        "entrypoint_issues": coverage.get("entrypoint_issues") or [],
    }


def _run_sleep_replay_dry_run(*, db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        return {"passed": False, "reason_code": "DB_NOT_FOUND", "db_path": str(db_path)}
    ledger = SQLiteLedger(db_path=db_path)
    try:
        store = LifeMemoryStore(ledger=ledger)
        consolidator = LifeMemoryConsolidator(store=store)
        sleep_task = MemorySleepReplayTask(store=store)
        current = int(time.time() * 1000)
        proposals = consolidator.build_semantic_proposals(limit=80, max_records=12)
        replay_candidates = sleep_task.select_replay_batch(limit=24)
        compaction_groups = sleep_task.select_compaction_groups(now_ts_ms_value=current, group_limit=8)
        compact_groups = [_compact_compaction_group(group) for group in compaction_groups]
    finally:
        ledger.close()
    violations = [
        {
            "group_index": index,
            "matched": _PROTECTED_COMPACTION_RE.findall(str(group.get("joined_summary") or ""))[:8],
            "episode_ids": group.get("episode_ids") or [],
        }
        for index, group in enumerate(compact_groups, start=1)
        if _PROTECTED_COMPACTION_RE.search(str(group.get("joined_summary") or ""))
    ]
    return {
        "passed": not violations,
        "proposal_count": len(proposals),
        "replay_candidate_count": len(replay_candidates),
        "compaction_group_count": len(compact_groups),
        "protected_compaction_violations": violations,
        "compaction_groups": compact_groups[:8],
    }


def _run_seeded_e2e_subprocess(
    *,
    base_url: str,
    db_path: Path,
    timeout_sec: float,
    trace_timeout_sec: float,
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(ROOT / "scripts" / "runtime" / "test_lightnovel_memory_flow_batch.py"),
        "--scenario",
        "seeded",
        "--db-path",
        str(db_path),
        "--timeout-sec",
        str(float(timeout_sec)),
        "--trace-timeout-sec",
        str(float(trace_timeout_sec)),
    ]
    if base_url:
        command.extend(["--base-url", str(base_url)])
    try:
        completed = subprocess.run(
            command,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=max(float(timeout_sec) + float(trace_timeout_sec) + 60.0, 120.0),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "passed": False,
            "reason_code": "SEEDED_E2E_TIMEOUT",
            "command": command,
            "stdout": str(exc.stdout or ""),
            "stderr": str(exc.stderr or ""),
        }
    summary = _extract_summary_from_stdout(completed.stdout)
    return {
        "passed": completed.returncode == 0 and bool(summary.get("passed", completed.returncode == 0)),
        "returncode": completed.returncode,
        "command": command,
        "summary": summary,
        "stdout_tail": "\n".join(str(completed.stdout or "").splitlines()[-20:]),
        "stderr_tail": "\n".join(str(completed.stderr or "").splitlines()[-20:]),
    }


def _extract_summary_from_stdout(stdout: str) -> dict[str, Any]:
    for line in str(stdout or "").splitlines():
        if not line.startswith("SUMMARY "):
            continue
        try:
            value = json.loads(line.removeprefix("SUMMARY ").strip())
        except json.JSONDecodeError:
            return {}
        return dict(value) if isinstance(value, Mapping) else {}
    return {}


def _compact_compaction_group(group: list[Mapping[str, Any]]) -> dict[str, Any]:
    summaries = [str(item.get("summary") or "") for item in group]
    return {
        "source_count": len(group),
        "episode_ids": [item.get("episode_id") for item in group],
        "episode_kind": group[0].get("episode_kind") if group else "",
        "session_id": group[0].get("session_id") if group else "",
        "joined_summary": "\n".join(summaries),
        "items": [
            {
                "episode_id": item.get("episode_id"),
                "episode_kind": item.get("episode_kind"),
                "action": item.get("action"),
                "retention_state": item.get("retention_state"),
                "memory_strength": item.get("memory_strength"),
                "summary": item.get("summary"),
            }
            for item in group[:8]
        ],
    }


def _add_check(
    checks: list[dict[str, Any]],
    *,
    check_id: str,
    passed: bool,
    summary: Mapping[str, Any],
    details: Mapping[str, Any],
) -> None:
    checks.append(
        {
            "check_id": check_id,
            "passed": bool(passed),
            "summary": dict(summary),
            "details": dict(details),
        }
    )


def _resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the LifeMemory acceptance suite.")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--include-synthetic", action="store_true", help="Include synthetic probe episodes in production coverage checks.")
    parser.add_argument("--include-seeded-e2e", action="store_true", help="Also run the seeded HTTP lightnovel memory E2E probe.")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--timeout-sec", type=float, default=240.0)
    parser.add_argument("--trace-timeout-sec", type=float, default=40.0)
    parser.add_argument("--sample-limit", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--out", default="")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    db_path = _resolve_path(str(args.db_path or DEFAULT_DB_PATH))
    report = run_acceptance_suite(
        db_path=db_path,
        include_synthetic=bool(args.include_synthetic),
        include_seeded_e2e=bool(args.include_seeded_e2e),
        base_url=str(args.base_url or ""),
        timeout_sec=float(args.timeout_sec),
        trace_timeout_sec=float(args.trace_timeout_sec),
        sample_limit=max(1, int(args.sample_limit)),
    )
    out_path = _resolve_path(str(args.out)) if str(args.out or "").strip() else ROOT / DEFAULT_OUT_DIR / f"life_memory_acceptance_suite_{int(time.time())}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    compact = {
        "status": "OK" if report["success"] else "FAILED",
        "success": report["success"],
        "passed_count": report["passed_count"],
        "failed_count": report["failed_count"],
        "out": str(out_path),
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(compact, ensure_ascii=False))
    return 0 if report["success"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
