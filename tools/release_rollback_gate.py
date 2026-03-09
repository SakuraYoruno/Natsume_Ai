"""Release rollback gate based on weekly stability report.

Reads a stability report JSON artifact and determines whether rollout can
continue or should trigger rollback, then emits JSON + Markdown outputs with
explicit rollback commands.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = ROOT / "docs" / "60_trace"


def _default_output_paths() -> tuple[Path, Path]:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return (
        DOCS_ROOT / f"RELEASE_ROLLBACK_GATE_{stamp}.json",
        DOCS_ROOT / f"RELEASE_ROLLBACK_GATE_{stamp}.md",
    )


def _resolve_output_path(raw: str, *, fallback: Path) -> Path:
    value = str(raw or "").strip()
    if not value:
        return fallback
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    return path


def _coerce_float(raw: Any, fallback: float, *, min_value: float = 0.0, max_value: float = 100.0) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = float(fallback)
    if value < min_value:
        return float(min_value)
    if value > max_value:
        return float(max_value)
    return float(value)


def _rollback_commands() -> list[dict[str, str]]:
    return [
        {
            "label": "Force cloud route",
            "command": "$env:HOST_CHAT_FORCE_CLOUD='true'; $env:HOST_CHAT_HOST_8B_ENABLED='false'",
        },
        {
            "label": "Disable gaming edge chat",
            "command": "$env:HOST_GAMING_EDGE_CHAT_ENABLED='false'",
        },
        {
            "label": "Disable gray rollout",
            "command": "$env:HOST_CHAT_GRAY_ENABLED='false'; $env:HOST_CHAT_GRAY_PERCENT='100'",
        },
    ]


def _load_json(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("stability report must be a JSON object")
    return payload


def _evaluate_gate(
    *,
    stability_report: dict[str, Any],
    max_alert_count: int,
    max_timeout_rate_pct: float,
    max_degraded_rate_pct: float,
    max_failure_rate_pct: float,
) -> dict[str, Any]:
    summary = dict(stability_report.get("summary") or {})
    governor = dict(stability_report.get("governor") or {})
    retrieval = dict(stability_report.get("retrieval") or {})

    current_alert_count = int(summary.get("alert_count") or 0)
    retrieval_timeout_rate = _coerce_float(retrieval.get("timeout_rate_pct"), 0.0)
    retrieval_degraded_rate = _coerce_float(retrieval.get("degraded_rate_pct"), 0.0)
    retrieval_failure_rate = _coerce_float(retrieval.get("failure_rate_pct"), 0.0)
    governor_degraded_rate = _coerce_float(governor.get("degraded_rate_pct"), 0.0)
    governor_failure_rate = _coerce_float(governor.get("failure_rate_pct"), 0.0)

    failed_conditions: list[dict[str, Any]] = []

    def _check(metric: str, observed: float, threshold: float, mode: str = "<=") -> None:
        ok = observed <= threshold if mode == "<=" else observed >= threshold
        if not ok:
            failed_conditions.append(
                {
                    "metric": metric,
                    "observed": observed,
                    "threshold": threshold,
                    "operator": mode,
                }
            )

    _check("summary.alert_count", float(current_alert_count), float(max_alert_count))
    _check("retrieval.timeout_rate_pct", retrieval_timeout_rate, float(max_timeout_rate_pct))
    _check("retrieval.degraded_rate_pct", retrieval_degraded_rate, float(max_degraded_rate_pct))
    _check("retrieval.failure_rate_pct", retrieval_failure_rate, float(max_failure_rate_pct))
    _check("governor.degraded_rate_pct", governor_degraded_rate, float(max_degraded_rate_pct))
    _check("governor.failure_rate_pct", governor_failure_rate, float(max_failure_rate_pct))

    rollback_required = len(failed_conditions) > 0
    return {
        "decision": ("ROLLBACK_REQUIRED" if rollback_required else "PASS"),
        "rollback_required": rollback_required,
        "failed_conditions": failed_conditions,
        "thresholds": {
            "max_alert_count": int(max_alert_count),
            "max_timeout_rate_pct": float(max_timeout_rate_pct),
            "max_degraded_rate_pct": float(max_degraded_rate_pct),
            "max_failure_rate_pct": float(max_failure_rate_pct),
        },
        "observed": {
            "alert_count": current_alert_count,
            "retrieval_timeout_rate_pct": retrieval_timeout_rate,
            "retrieval_degraded_rate_pct": retrieval_degraded_rate,
            "retrieval_failure_rate_pct": retrieval_failure_rate,
            "governor_degraded_rate_pct": governor_degraded_rate,
            "governor_failure_rate_pct": governor_failure_rate,
        },
        "rollback_commands": _rollback_commands(),
    }


def _render_markdown(report: dict[str, Any]) -> str:
    evaluation = dict(report.get("evaluation") or {})
    commands = list(evaluation.get("rollback_commands") or [])
    failed = list(evaluation.get("failed_conditions") or [])

    failed_lines = ["- none"]
    if failed:
        failed_lines = [
            (
                f"- `{str(row.get('metric') or '')}`: observed={row.get('observed')}, "
                f"threshold={row.get('threshold')} ({row.get('operator')})"
            )
            for row in failed
            if isinstance(row, dict)
        ] or ["- none"]

    cmd_lines = ["- none"]
    if commands:
        cmd_lines = [
            f"- {str(row.get('label') or 'command')}: `{str(row.get('command') or '').strip()}`"
            for row in commands
            if isinstance(row, dict)
        ] or ["- none"]

    lines = [
        "# Release Rollback Gate",
        "",
        f"- generated_at: {report.get('generated_at')}",
        f"- stability_report: {report.get('stability_report_path')}",
        f"- decision: {evaluation.get('decision')}",
        f"- rollback_required: {evaluation.get('rollback_required')}",
        "",
        "## Failed Conditions",
        "",
        *failed_lines,
        "",
        "## Rollback Commands",
        "",
        *cmd_lines,
    ]
    return "\n".join(lines) + "\n"


def _write_outputs(report: dict[str, Any], *, output_json: Path, output_md: Path) -> tuple[Path, Path]:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output_md.write_text(_render_markdown(report), encoding="utf-8")
    return output_json, output_md


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Release rollback gate from stability report")
    parser.add_argument("--stability-report", required=True, help="Path to weekly stability report JSON")
    parser.add_argument("--max-alert-count", type=int, default=0)
    parser.add_argument("--max-timeout-rate-pct", type=float, default=10.0)
    parser.add_argument("--max-degraded-rate-pct", type=float, default=20.0)
    parser.add_argument("--max-failure-rate-pct", type=float, default=15.0)
    parser.add_argument("--output-json", default="")
    parser.add_argument("--output-md", default="")
    return parser


def run(argv: Sequence[str] | None = None) -> dict[str, Any]:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    stability_path = Path(str(args.stability_report)).resolve()
    stability_report = _load_json(stability_path)
    evaluation = _evaluate_gate(
        stability_report=stability_report,
        max_alert_count=max(0, int(args.max_alert_count)),
        max_timeout_rate_pct=_coerce_float(args.max_timeout_rate_pct, 10.0),
        max_degraded_rate_pct=_coerce_float(args.max_degraded_rate_pct, 20.0),
        max_failure_rate_pct=_coerce_float(args.max_failure_rate_pct, 15.0),
    )

    report = {
        "version": "release_rollback_gate.v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "stability_report_path": str(stability_path),
        "evaluation": evaluation,
    }

    default_json, default_md = _default_output_paths()
    output_json = _resolve_output_path(str(args.output_json), fallback=default_json)
    output_md = _resolve_output_path(str(args.output_md), fallback=default_md)
    json_path, md_path = _write_outputs(report, output_json=output_json, output_md=output_md)

    print(
        f"rollback_gate: decision={evaluation['decision']} "
        f"rollback_required={evaluation['rollback_required']}"
    )
    print(f"json_report: {json_path}")
    print(f"markdown_report: {md_path}")
    return report


def main(argv: Sequence[str] | None = None) -> int:
    report = run(argv=argv)
    rollback_required = bool(dict(report.get("evaluation") or {}).get("rollback_required"))
    return 2 if rollback_required else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
