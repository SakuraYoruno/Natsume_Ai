"""Unified diagnostic entrypoint for retrieval/stream/emotion/graph/stability/dashboard.

This tool consolidates scattered diagnostic scripts behind one command:
    python tools/diag.py <retrieval|stream|emotion|graph|stability|dashboard> [options]

For every run, a unified JSON + Markdown report is written under docs/60_trace
unless overridden by --output-json/--output-md.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = ROOT / "docs" / "60_trace"

DOMAIN_RETRIEVAL = "retrieval"
DOMAIN_STREAM = "stream"
DOMAIN_EMOTION = "emotion"
DOMAIN_GRAPH = "graph"
DOMAIN_STABILITY = "stability"
DOMAIN_DASHBOARD = "dashboard"


@dataclass(frozen=True)
class CommandPlan:
    domain: str
    description: str
    command: list[str]


@dataclass(frozen=True)
class CommandExecution:
    status: str
    success: bool
    exit_code: int | None
    duration_ms: float
    stdout: str
    stderr: str
    error: str | None = None


def _resolve_output_path(raw: str, *, fallback: Path) -> Path:
    text = str(raw or "").strip()
    if not text:
        return fallback
    output = Path(text)
    if not output.is_absolute():
        output = ROOT / output
    return output


def _default_output_paths(domain: str) -> tuple[Path, Path]:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = DOCS_ROOT
    return (
        base / f"DIAG_UNIFIED_{domain.upper()}_{stamp}.json",
        base / f"DIAG_UNIFIED_{domain.upper()}_{stamp}.md",
    )


def _tail_text(text: str, *, max_lines: int = 120) -> str:
    rows = list(str(text or "").splitlines())
    if len(rows) <= max_lines:
        return "\n".join(rows)
    return "\n".join(rows[-max_lines:])


def extract_artifact_paths(stdout: str) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    for raw in str(stdout or "").splitlines():
        line = raw.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        normalized_key = str(key).strip().lower().replace(" ", "_")
        normalized_value = str(value).strip()
        if normalized_key in {"json_report", "markdown_report"} and normalized_value:
            artifacts[normalized_key] = normalized_value
    return artifacts


def _build_command_plan(args: argparse.Namespace) -> CommandPlan:
    python_bin = str(getattr(args, "python", "") or sys.executable)

    if args.domain == DOMAIN_RETRIEVAL:
        return CommandPlan(
            domain=DOMAIN_RETRIEVAL,
            description="Run retrieval pipeline smoke diagnostic.",
            command=[python_bin, str(ROOT / "tools" / "_diag_retrieval.py")],
        )

    if args.domain == DOMAIN_GRAPH:
        return CommandPlan(
            domain=DOMAIN_GRAPH,
            description="Run graph runtime smoke diagnostic.",
            command=[python_bin, str(ROOT / "tools" / "_diag_graph.py")],
        )

    if args.domain == DOMAIN_STREAM:
        command = [
            python_bin,
            str(ROOT / "tools" / "diag_stream_baseline.py"),
            "--base-url",
            str(args.base_url),
            "--path",
            str(args.path),
            "--runs",
            str(int(args.runs)),
            "--timeout-s",
            str(float(args.http_timeout_s)),
            "--session-prefix",
            str(args.session_prefix),
        ]
        if args.messages:
            command.extend(["--messages", *[str(item) for item in args.messages]])
        return CommandPlan(
            domain=DOMAIN_STREAM,
            description="Run stream SSE baseline profiler diagnostic.",
            command=command,
        )

    if args.domain == DOMAIN_EMOTION:
        command = [
            python_bin,
            str(ROOT / "tools" / "diag_emotion_closed_loop.py"),
            "--threshold",
            str(float(args.threshold)),
        ]
        case_file = str(args.case_file or "").strip()
        if case_file:
            command.extend(["--case-file", case_file])
        return CommandPlan(
            domain=DOMAIN_EMOTION,
            description="Run emotion closed-loop corpus diagnostic.",
            command=command,
        )

    if args.domain == DOMAIN_STABILITY:
        command = [
            python_bin,
            str(ROOT / "tools" / "diag_stability_weekly.py"),
            "--ledger-db",
            str(args.ledger_db),
            "--days",
            str(int(args.days)),
            "--threshold-timeout-rate-pct",
            str(float(args.threshold_timeout_rate_pct)),
            "--threshold-degraded-rate-pct",
            str(float(args.threshold_degraded_rate_pct)),
            "--threshold-failure-rate-pct",
            str(float(args.threshold_failure_rate_pct)),
        ]
        return CommandPlan(
            domain=DOMAIN_STABILITY,
            description="Run weekly stability summary diagnostic.",
            command=command,
        )

    if args.domain == DOMAIN_DASHBOARD:
        command = [
            python_bin,
            str(ROOT / "tools" / "diag_longterm_dashboard.py"),
            "--ledger-db",
            str(args.ledger_db),
            "--days",
            str(int(args.days)),
            "--threshold-governor-approve-rate-min-pct",
            str(float(args.threshold_governor_approve_rate_min_pct)),
            "--threshold-retrieval-failure-rate-max-pct",
            str(float(args.threshold_retrieval_failure_rate_max_pct)),
            "--threshold-graph-p95-latency-max-ms",
            str(float(args.threshold_graph_p95_latency_max_ms)),
            "--threshold-cloud-call-ratio-max-pct",
            str(float(args.threshold_cloud_call_ratio_max_pct)),
            "--threshold-required-up-rate-min-pct",
            str(float(args.threshold_required_up_rate_min_pct)),
        ]
        return CommandPlan(
            domain=DOMAIN_DASHBOARD,
            description="Run long-term KPI dashboard diagnostic.",
            command=command,
        )

    raise ValueError(f"unsupported domain: {args.domain}")


def _execute_command(
    plan: CommandPlan,
    *,
    dry_run: bool,
    exec_timeout_s: float,
) -> CommandExecution:
    started = time.perf_counter()
    if dry_run:
        return CommandExecution(
            status="DRY_RUN",
            success=True,
            exit_code=0,
            duration_ms=round((time.perf_counter() - started) * 1000.0, 3),
            stdout="",
            stderr="",
        )

    try:
        completed = subprocess.run(
            plan.command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=float(exec_timeout_s),
            check=False,
        )
        duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
        success = bool(completed.returncode == 0)
        return CommandExecution(
            status="OK" if success else "FAILED",
            success=success,
            exit_code=int(completed.returncode),
            duration_ms=duration_ms,
            stdout=str(completed.stdout or ""),
            stderr=str(completed.stderr or ""),
            error=None,
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
        return CommandExecution(
            status="TIMEOUT",
            success=False,
            exit_code=None,
            duration_ms=duration_ms,
            stdout=str(exc.stdout or ""),
            stderr=str(exc.stderr or ""),
            error=f"timeout after {float(exec_timeout_s)}s",
        )


def _build_report(
    *,
    plan: CommandPlan,
    execution: CommandExecution,
    dry_run: bool,
) -> dict[str, Any]:
    stdout_tail = _tail_text(execution.stdout)
    stderr_tail = _tail_text(execution.stderr)
    artifacts = extract_artifact_paths(execution.stdout)
    return {
        "version": "diag_unified.v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "domain": plan.domain,
        "description": plan.description,
        "dry_run": bool(dry_run),
        "status": execution.status,
        "success": bool(execution.success),
        "exit_code": execution.exit_code,
        "duration_ms": float(execution.duration_ms),
        "command": list(plan.command),
        "artifacts": artifacts,
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
        "error": execution.error,
    }


def _write_reports(report: dict[str, Any], *, output_json: Path, output_md: Path) -> tuple[Path, Path]:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)

    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    artifacts = dict(report.get("artifacts") or {})
    artifact_lines = ["- none"]
    if artifacts:
        artifact_lines = [f"- {key}: {value}" for key, value in sorted(artifacts.items())]

    md_lines = [
        "# Unified Diagnostic Report",
        "",
        f"- generated_at: {report.get('generated_at')}",
        f"- domain: {report.get('domain')}",
        f"- status: {report.get('status')}",
        f"- success: {report.get('success')}",
        f"- exit_code: {report.get('exit_code')}",
        f"- duration_ms: {report.get('duration_ms')}",
        f"- dry_run: {report.get('dry_run')}",
        "",
        "## Command",
        "",
        "```text",
        " ".join(str(item) for item in list(report.get("command") or [])),
        "```",
        "",
        "## Artifacts",
        "",
        *artifact_lines,
        "",
        "## Stdout Tail",
        "",
        "```text",
        str(report.get("stdout_tail") or ""),
        "```",
        "",
        "## Stderr Tail",
        "",
        "```text",
        str(report.get("stderr_tail") or ""),
        "```",
    ]

    output_md.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    return output_json, output_md


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--python", default=sys.executable, help="Python executable for delegated diagnostics")
    parser.add_argument("--exec-timeout-s", type=float, default=180.0, help="Timeout for delegated command")
    parser.add_argument("--dry-run", action="store_true", help="Render command/report without execution")
    parser.add_argument("--output-json", default="", help="Unified JSON report path")
    parser.add_argument("--output-md", default="", help="Unified Markdown report path")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified diagnostic entrypoint")
    subparsers = parser.add_subparsers(dest="domain", required=True)

    retrieval = subparsers.add_parser(DOMAIN_RETRIEVAL, help="Run retrieval diagnostic")
    _add_common_options(retrieval)

    graph = subparsers.add_parser(DOMAIN_GRAPH, help="Run graph diagnostic")
    _add_common_options(graph)

    stream = subparsers.add_parser(DOMAIN_STREAM, help="Run stream diagnostic")
    _add_common_options(stream)
    stream.add_argument("--base-url", default="http://127.0.0.1:8000")
    stream.add_argument("--path", default="/v1/chat/stream")
    stream.add_argument("--runs", type=int, default=20)
    stream.add_argument("--http-timeout-s", type=float, default=30.0)
    stream.add_argument("--session-prefix", default="diag-stream")
    stream.add_argument("--messages", nargs="*", default=None)

    emotion = subparsers.add_parser(DOMAIN_EMOTION, help="Run emotion diagnostic")
    _add_common_options(emotion)
    emotion.add_argument("--threshold", type=float, default=0.8)
    emotion.add_argument("--case-file", default="")

    stability = subparsers.add_parser(DOMAIN_STABILITY, help="Run weekly stability report diagnostic")
    _add_common_options(stability)
    stability.add_argument("--ledger-db", default="host_brain/data/audit.sqlite3")
    stability.add_argument("--days", type=int, default=7)
    stability.add_argument("--threshold-timeout-rate-pct", type=float, default=10.0)
    stability.add_argument("--threshold-degraded-rate-pct", type=float, default=20.0)
    stability.add_argument("--threshold-failure-rate-pct", type=float, default=15.0)

    dashboard = subparsers.add_parser(DOMAIN_DASHBOARD, help="Run long-term KPI dashboard diagnostic")
    _add_common_options(dashboard)
    dashboard.add_argument("--ledger-db", default="host_brain/data/audit.sqlite3")
    dashboard.add_argument("--days", type=int, default=30)
    dashboard.add_argument("--threshold-governor-approve-rate-min-pct", type=float, default=80.0)
    dashboard.add_argument("--threshold-retrieval-failure-rate-max-pct", type=float, default=20.0)
    dashboard.add_argument("--threshold-graph-p95-latency-max-ms", type=float, default=4000.0)
    dashboard.add_argument("--threshold-cloud-call-ratio-max-pct", type=float, default=70.0)
    dashboard.add_argument("--threshold-required-up-rate-min-pct", type=float, default=99.0)
    return parser


def run(argv: Sequence[str] | None = None) -> dict[str, Any]:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    plan = _build_command_plan(args)
    execution = _execute_command(
        plan,
        dry_run=bool(args.dry_run),
        exec_timeout_s=float(args.exec_timeout_s),
    )

    report = _build_report(plan=plan, execution=execution, dry_run=bool(args.dry_run))
    default_json, default_md = _default_output_paths(plan.domain)
    output_json = _resolve_output_path(str(args.output_json), fallback=default_json)
    output_md = _resolve_output_path(str(args.output_md), fallback=default_md)
    json_path, md_path = _write_reports(report, output_json=output_json, output_md=output_md)

    print(
        f"diag_unified: domain={plan.domain} status={report['status']} "
        f"success={report['success']} duration_ms={report['duration_ms']}"
    )
    print(f"json_report: {json_path}")
    print(f"markdown_report: {md_path}")
    return report


def main(argv: Sequence[str] | None = None) -> int:
    report = run(argv=argv)
    return 0 if bool(report.get("success")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
