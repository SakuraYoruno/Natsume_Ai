"""Generate post-release 24h review report.

Consumes stability and rollback-gate artifacts and outputs a structured review
in JSON + Markdown.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = ROOT / "docs" / "60_trace"


def _default_output_paths() -> tuple[Path, Path]:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return (
        DOCS_ROOT / f"POST_RELEASE_REVIEW_{stamp}.json",
        DOCS_ROOT / f"POST_RELEASE_REVIEW_{stamp}.md",
    )


def _resolve_output_path(raw: str, *, fallback: Path) -> Path:
    text = str(raw or "").strip()
    if not text:
        return fallback
    path = Path(text)
    if not path.is_absolute():
        path = ROOT / path
    return path


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must be a JSON object")
    return payload


def _parse_iso_datetime(value: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("released_at is required")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _build_review(
    *,
    release_id: str,
    released_at: datetime,
    owner: str,
    stability_report: dict[str, Any],
    rollback_gate_report: dict[str, Any],
) -> dict[str, Any]:
    now = datetime.now(tz=timezone.utc)
    age_hours = round((now - released_at).total_seconds() / 3600.0, 2)

    stability_summary = dict(stability_report.get("summary") or {})
    rollback_eval = dict(rollback_gate_report.get("evaluation") or {})
    failed_conditions = list(rollback_eval.get("failed_conditions") or [])

    alert_count = int(stability_summary.get("alert_count") or 0)
    rollback_required = bool(rollback_eval.get("rollback_required"))
    gate_decision = str(rollback_eval.get("decision") or "UNKNOWN")
    status = "COMPLETE" if age_hours >= 24.0 else "PENDING_24H_WINDOW"

    findings: list[str] = []
    if rollback_required:
        findings.append("Rollback gate requires rollback based on current thresholds.")
    if alert_count > 0:
        findings.append(f"Weekly stability report still has {alert_count} active alerts.")
    if not findings:
        findings.append("No blocking signal from stability/gate reports.")

    actions: list[str] = []
    if rollback_required:
        actions.append("Keep HOST_CHAT_FORCE_CLOUD=true and HOST_CHAT_GRAY_ENABLED=false until gate turns PASS.")
    else:
        actions.append("Maintain current rollout and monitor 24h error/latency trend.")
    if alert_count > 0:
        actions.append("Prioritize reducing timeout/degraded/failure rates before expanding gray percentage.")
    else:
        actions.append("Canary expansion can be considered with +10% increments per release window.")

    return {
        "version": "post_release_review.v1",
        "generated_at": now.isoformat(timespec="seconds"),
        "release_id": str(release_id),
        "released_at": released_at.isoformat(timespec="seconds"),
        "owner": str(owner),
        "review_status": status,
        "age_hours": age_hours,
        "inputs": {
            "stability_report_version": str(stability_report.get("version") or ""),
            "rollback_gate_version": str(rollback_gate_report.get("version") or ""),
        },
        "summary": {
            "gate_decision": gate_decision,
            "rollback_required": rollback_required,
            "alert_count": alert_count,
            "failed_condition_count": len(failed_conditions),
        },
        "failed_conditions": failed_conditions,
        "findings": findings,
        "actions": actions,
    }


def _render_markdown(review: dict[str, Any]) -> str:
    findings = [f"- {row}" for row in list(review.get("findings") or [])]
    actions = [f"- {row}" for row in list(review.get("actions") or [])]
    failed = list(review.get("failed_conditions") or [])
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

    lines = [
        "# Post Release 24h Review",
        "",
        f"- generated_at: {review.get('generated_at')}",
        f"- release_id: {review.get('release_id')}",
        f"- released_at: {review.get('released_at')}",
        f"- age_hours: {review.get('age_hours')}",
        f"- review_status: {review.get('review_status')}",
        "",
        "## Summary",
        "",
        f"- gate_decision: {dict(review.get('summary') or {}).get('gate_decision')}",
        f"- rollback_required: {dict(review.get('summary') or {}).get('rollback_required')}",
        f"- alert_count: {dict(review.get('summary') or {}).get('alert_count')}",
        "",
        "## Failed Conditions",
        "",
        *failed_lines,
        "",
        "## Findings",
        "",
        *(findings or ["- none"]),
        "",
        "## Actions",
        "",
        *(actions or ["- none"]),
    ]
    return "\n".join(lines) + "\n"


def _write_outputs(review: dict[str, Any], *, output_json: Path, output_md: Path) -> tuple[Path, Path]:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output_md.write_text(_render_markdown(review), encoding="utf-8")
    return output_json, output_md


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate post-release 24h review")
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--released-at", required=True, help="ISO datetime, e.g. 2026-03-09T00:00:00+08:00")
    parser.add_argument("--owner", default="host_brain")
    parser.add_argument("--stability-report", required=True)
    parser.add_argument("--rollback-gate-report", required=True)
    parser.add_argument("--output-json", default="")
    parser.add_argument("--output-md", default="")
    return parser


def run(argv: Sequence[str] | None = None) -> dict[str, Any]:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    stability_path = Path(str(args.stability_report)).resolve()
    rollback_path = Path(str(args.rollback_gate_report)).resolve()
    stability_report = _load_json(stability_path)
    rollback_gate_report = _load_json(rollback_path)
    released_at = _parse_iso_datetime(str(args.released_at))

    review = _build_review(
        release_id=str(args.release_id),
        released_at=released_at,
        owner=str(args.owner),
        stability_report=stability_report,
        rollback_gate_report=rollback_gate_report,
    )

    default_json, default_md = _default_output_paths()
    output_json = _resolve_output_path(str(args.output_json), fallback=default_json)
    output_md = _resolve_output_path(str(args.output_md), fallback=default_md)
    json_path, md_path = _write_outputs(review, output_json=output_json, output_md=output_md)

    print(
        f"post_release_review: release_id={review['release_id']} "
        f"status={review['review_status']} age_hours={review['age_hours']}"
    )
    print(f"json_report: {json_path}")
    print(f"markdown_report: {md_path}")
    return review


def main(argv: Sequence[str] | None = None) -> int:
    run(argv=argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
