from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.organ_ops.organ_contract_matrix import (  # noqa: E402
    CONTRACT_SCOPE_FLAGS,
    build_organ_contract_matrix,
    validate_organ_contract_matrix,
)
from host_brain.persona.surface_organ_influence import SURFACE_ORGAN_IDS  # noqa: E402
from tools.validate_organ_runtime_integration import run_validation as run_runtime_validation  # noqa: E402


DEFAULT_OUT = ROOT / "artifacts" / "organ_contracts"
RUNTIME_CHECKS_OF_INTEREST = (
    "fixture_matrix_passed",
    "fixture_each_organ_has_contract_and_surface_effect",
    "live_maps_core_organs_to_contract",
    "live_collects_runtime_state_for_all_organs",
    "live_reply_control_receives_compressed_influence",
    "live_packets_do_not_leak_raw_context",
)


def run_validation(
    *,
    runtime_mode: str = "fixture",
    iterations: int = 1,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    resolved_out = out_dir or (DEFAULT_OUT / time.strftime("%Y%m%d-%H%M%S"))
    resolved_out.mkdir(parents=True, exist_ok=True)
    normalized_runtime_mode = str(runtime_mode or "fixture").strip().lower()
    if normalized_runtime_mode not in {"none", "fixture", "live", "both"}:
        raise ValueError("runtime_mode must be none, fixture, live, or both")

    matrix_rows = [dict(row) for row in build_organ_contract_matrix()]
    checks = [
        *validate_organ_contract_matrix(matrix_rows),
        *_contract_checks(matrix_rows),
    ]
    runtime_result: dict[str, Any] = {}
    runtime_summary: dict[str, Any] = {}
    runtime_details: dict[str, Any] = {}
    if normalized_runtime_mode != "none":
        runtime_result = run_runtime_validation(
            mode=normalized_runtime_mode,
            iterations=max(1, int(iterations or 1)),
            out_dir=resolved_out / "runtime_integration",
        )
        runtime_summary = dict(runtime_result.get("summary") or {})
        runtime_details = dict(runtime_result.get("details") or {})
        checks.extend(_runtime_checks(runtime_summary=runtime_summary, runtime_details=runtime_details))

    failed = [check for check in checks if not bool(check.get("passed"))]
    summary = {
        "status": "PASS" if not failed else "FAIL",
        "evaluation_mode": "organ_contract_matrix_validation_no_text_quality_scoring",
        "runtime_mode": normalized_runtime_mode,
        "iterations": max(1, int(iterations or 1)) if normalized_runtime_mode in {"live", "both"} else 0,
        "organ_count": len(matrix_rows),
        "failed_count": len(failed),
        "checks": {str(check.get("name")): bool(check.get("passed")) for check in checks},
        "output_dir": str(resolved_out),
        "runtime_output_dir": str(runtime_summary.get("output_dir") or ""),
    }
    details = {
        "summary": summary,
        "checks": checks,
        "matrix_rows": matrix_rows,
        "runtime_summary": runtime_summary,
        "runtime_details": runtime_details,
    }
    _write_json(resolved_out / "summary.json", summary)
    _write_json(resolved_out / "details.json", details)
    _write_jsonl(resolved_out / "organ_contract_matrix.jsonl", matrix_rows)
    _write_json(resolved_out / "organ_contract_matrix.json", {"rows": matrix_rows})
    (resolved_out / "report.md").write_text(_markdown_report(summary, details), encoding="utf-8")
    return {"summary": summary, "details": details}


def _contract_checks(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    row_list = [dict(row) for row in rows]
    return [
        _check(
            "every_organ_is_contract_only_surface_input",
            all(
                "surface_text" in list(row.get("main_chain_effects") or [])
                and str(row.get("raw_prompt_visibility") or "") == "forbidden"
                for row in row_list
            ),
            {
                "failed": [
                    row.get("organ_id")
                    for row in row_list
                    if "surface_text" not in list(row.get("main_chain_effects") or [])
                    or str(row.get("raw_prompt_visibility") or "") != "forbidden"
                ]
            },
        ),
        _check(
            "write_scope_flags_are_explicit_for_each_organ",
            all(all(flag in row for flag in CONTRACT_SCOPE_FLAGS) for row in row_list),
            {
                "failed": [
                    row.get("organ_id")
                    for row in row_list
                    if not all(flag in row for flag in CONTRACT_SCOPE_FLAGS)
                ]
            },
        ),
        _check(
            "memory_emotion_relationship_write_scopes_are_not_empty",
            any(bool(row.get("may_write_memory")) for row in row_list)
            and any(bool(row.get("may_write_emotion")) for row in row_list)
            and any(bool(row.get("may_write_relationship")) for row in row_list),
            {
                "memory_organs": [row.get("organ_id") for row in row_list if bool(row.get("may_write_memory"))],
                "emotion_organs": [row.get("organ_id") for row in row_list if bool(row.get("may_write_emotion"))],
                "relationship_organs": [row.get("organ_id") for row in row_list if bool(row.get("may_write_relationship"))],
            },
        ),
        _check(
            "observability_surface_is_declared",
            all(str(row.get("module") or "") and str(row.get("file_path") or "") for row in row_list),
            {
                "failed": [
                    row.get("organ_id")
                    for row in row_list
                    if not str(row.get("module") or "") or not str(row.get("file_path") or "")
                ]
            },
        ),
    ]


def _runtime_checks(
    *,
    runtime_summary: Mapping[str, Any],
    runtime_details: Mapping[str, Any],
) -> list[dict[str, Any]]:
    runtime_checks = dict(runtime_summary.get("checks") or {})
    runtime_rows = _runtime_rows(runtime_details)
    runtime_ids = {str(row.get("organ_id") or "") for row in runtime_rows}
    expected_ids = set(SURFACE_ORGAN_IDS)
    return [
        _check(
            "runtime_integration_validation_passed",
            str(runtime_summary.get("status") or "") == "PASS",
            {
                "status": runtime_summary.get("status"),
                "failed_count": runtime_summary.get("failed_count"),
                "interesting_checks": {
                    name: runtime_checks.get(name)
                    for name in RUNTIME_CHECKS_OF_INTEREST
                    if name in runtime_checks
                },
            },
        ),
        _check(
            "runtime_rows_cover_contract_matrix",
            bool(runtime_rows) and runtime_ids == expected_ids,
            {
                "row_count": len(runtime_rows),
                "missing": sorted(expected_ids - runtime_ids),
                "extra": sorted(runtime_ids - expected_ids),
            },
        ),
        _check(
            "runtime_rows_use_compressed_contract_without_raw_leak",
            bool(runtime_rows)
            and all(
                bool(row.get("raw_context_not_leaked", row.get("raw_context_dropped", True)))
                for row in runtime_rows
            ),
            {
                "failed": [
                    row.get("organ_id")
                    for row in runtime_rows
                    if not bool(row.get("raw_context_not_leaked", row.get("raw_context_dropped", True)))
                ]
            },
        ),
    ]


def _runtime_rows(details: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("live_rows", "fixture_rows"):
        for raw in list(details.get(key) or []):
            if isinstance(raw, Mapping):
                rows.append(dict(raw))
    return rows


def _markdown_report(summary: Mapping[str, Any], details: Mapping[str, Any]) -> str:
    lines = [
        "# Organ Contract Matrix Validation",
        "",
        "This validates the 23 host organs against the compressed main-chain contract. It is not text quality scoring.",
        "The `scopes` column means allowed compressed update scope; direct raw writes still go through the owning runtime modules.",
        "",
        f"- status: `{summary.get('status')}`",
        f"- runtime_mode: `{summary.get('runtime_mode')}`",
        f"- organ_count: `{summary.get('organ_count')}`",
        f"- failed_count: `{summary.get('failed_count')}`",
        f"- output_dir: `{summary.get('output_dir')}`",
        f"- runtime_output_dir: `{summary.get('runtime_output_dir')}`",
        "",
        "## Checks",
        "",
    ]
    for check in list(details.get("checks") or []):
        marker = "PASS" if check.get("passed") else "FAIL"
        lines.append(f"- `{marker}` {check.get('name')}: `{json.dumps(check.get('details') or {}, ensure_ascii=False)}`")
    lines.extend(["", "## Contract Matrix", ""])
    for row in list(details.get("matrix_rows") or []):
        if not isinstance(row, Mapping):
            continue
        scopes = [
            name.removeprefix("may_")
            for name in CONTRACT_SCOPE_FLAGS
            if bool(row.get(name))
        ]
        lines.append(
            "- `{organ}` inputs=`{inputs}` outputs=`{outputs}` effects=`{effects}` scopes=`{scopes}` events_out=`{events}` state=`{state}`".format(
                organ=row.get("organ_id"),
                inputs=",".join(str(item) for item in list(row.get("input_keys") or [])),
                outputs=",".join(str(item) for item in list(row.get("compressed_output_fields") or [])),
                effects=",".join(str(item) for item in list(row.get("main_chain_effects") or [])),
                scopes=",".join(scopes),
                events=",".join(str(item) for item in list(row.get("event_outputs") or [])[:4]),
                state=",".join(str(item) for item in list(row.get("state_write_targets") or [])[:4]),
            )
        )
    return "\n".join(lines).rstrip() + "\n"


def _check(name: str, passed: bool, details: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": dict(details or {})}


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            file.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate the 23-organ contract matrix and runtime integration.")
    parser.add_argument("--runtime-mode", choices=("none", "fixture", "live", "both"), default="fixture")
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--out-dir", type=Path, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(list(argv) if argv is not None else None)
    result = run_validation(runtime_mode=args.runtime_mode, iterations=args.iterations, out_dir=args.out_dir)
    print(json.dumps(result["summary"], ensure_ascii=False, sort_keys=True))
    return 0 if result["summary"].get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
