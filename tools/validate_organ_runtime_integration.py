from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.app.surface_context_packet import build_surface_context_packet  # noqa: E402
from host_brain.persona.organ_influence_contract import (  # noqa: E402
    CONTRACT_FIELDS,
    organ_main_chain_integration_table,
)
from host_brain.persona.surface_packet_compiler import compile_reply_control_packet  # noqa: E402
from tools.validate_organ_influence_matrix import run_validation as run_matrix_validation  # noqa: E402


DEFAULT_OUT = ROOT / "artifacts" / "organ_runtime_integration"
CORE_LIVE_ORGANS = (
    "cortex",
    "thalamus",
    "limbic",
    "hippocampus",
    "social_brain",
    "default_mode",
    "language_system",
    "immune",
    "prefrontal",
)
RAW_CONTEXT_MARKERS = (
    "raw_organ_outputs",
    "organ_outputs",
    "messages",
    "natural_language_advice",
    "module_advice",
    "agent_advice",
    "tool_candidates",
    "uncompressed_rag_documents",
)


def run_validation(
    *,
    mode: str = "fixture",
    iterations: int = 1,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    resolved_out = out_dir or (DEFAULT_OUT / time.strftime("%Y%m%d-%H%M%S"))
    resolved_out.mkdir(parents=True, exist_ok=True)
    normalized_mode = str(mode or "fixture").strip().lower()
    if normalized_mode not in {"fixture", "live", "both"}:
        raise ValueError("mode must be fixture, live, or both")

    fixture_summary: dict[str, Any] = {}
    fixture_rows: list[dict[str, Any]] = []
    if normalized_mode in {"fixture", "both"}:
        fixture_result = run_matrix_validation(out_dir=resolved_out / "fixture_matrix")
        fixture_summary = dict(fixture_result.get("summary") or {})
        fixture_rows = [dict(row) for row in list(dict(fixture_result.get("details") or {}).get("rows") or [])]

    live_iterations: list[dict[str, Any]] = []
    live_rows: list[dict[str, Any]] = []
    if normalized_mode in {"live", "both"}:
        live_iterations = [_live_iteration(index) for index in range(max(1, int(iterations or 1)))]
        for item in live_iterations:
            live_rows.extend([dict(row) for row in list(item.get("rows") or [])])

    checks = _checks(
        mode=normalized_mode,
        fixture_summary=fixture_summary,
        fixture_rows=fixture_rows,
        live_iterations=live_iterations,
        live_rows=live_rows,
    )
    summary = {
        "status": "PASS" if all(bool(item.get("passed")) for item in checks) else "FAIL",
        "evaluation_mode": "runtime_integration_contract_validation_no_text_quality_scoring",
        "mode": normalized_mode,
        "iterations": max(1, int(iterations or 1)) if normalized_mode in {"live", "both"} else 0,
        "organ_count": len(organ_main_chain_integration_table()),
        "failed_count": sum(1 for item in checks if not bool(item.get("passed"))),
        "checks": {str(item.get("name")): bool(item.get("passed")) for item in checks},
        "output_dir": str(resolved_out),
    }
    details = {
        "summary": summary,
        "checks": checks,
        "fixture_summary": fixture_summary,
        "fixture_rows": fixture_rows,
        "live_iterations": live_iterations,
        "live_rows": live_rows,
    }
    _write_json(resolved_out / "summary.json", summary)
    _write_json(resolved_out / "details.json", details)
    _write_jsonl(resolved_out / "runtime_rows.jsonl", live_rows or fixture_rows)
    (resolved_out / "report.md").write_text(_markdown_report(summary, details), encoding="utf-8")
    return {"summary": summary, "details": details}


def _live_iteration(index: int) -> dict[str, Any]:
    started = time.perf_counter()
    request_id = f"organ-runtime-integration-{index + 1:03d}"
    session_id = "organ-runtime-integration-session"
    person_id = "organ-runtime-integration-user"
    try:
        from host_brain.tribe.cortex_agent import get_cortex_agent  # noqa: PLC0415

        raw_context = get_cortex_agent().collect_tribe_context(
            request_body={
                "request_id": request_id,
                "person_id": person_id,
                "session_id": session_id,
                "user_message": "验证器官族状态是否进入当前主链",
            },
            session_id=session_id,
            user_id=person_id,
        )
        raw_context = dict(raw_context or {}) if isinstance(raw_context, Mapping) else {}
        surface_context = build_surface_context_packet(
            user_message="验证器官族状态是否进入当前主链",
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
            organ_context=raw_context,
            surface_task_control={
                "task_mode": "daily_chat",
                "response_move": "reply_current_turn",
                "question_budget": 0,
            },
        )
        reply_control = compile_reply_control_packet(
            user_message="验证器官族状态是否进入当前主链",
            task_control=surface_context.get("reply_plan"),
            memory_packet=surface_context.get("surface_memory_recall_packet"),
            affect_packet=surface_context.get("surface_affect_packet"),
            influence_packet=surface_context.get("surface_influence_packet"),
        )
        return {
            "index": index + 1,
            "ok": True,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "raw_context_key_count": len(raw_context),
            "surface_organ_context_meta": dict(surface_context.get("surface_organ_context_meta") or {}),
            "mapped_organs": list(
                dict(dict(surface_context.get("surface_influence_packet") or {}).get("organ_trace") or {}).get("mapped_organs") or []
            ),
            "rows": _organ_rows(
                raw_context=raw_context,
                surface_context=surface_context,
                reply_control_packet=reply_control.packet.model_dump(),
            ),
            "error": "",
        }
    except Exception as exc:  # pragma: no cover - diagnostic path
        return {
            "index": index + 1,
            "ok": False,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "raw_context_key_count": 0,
            "mapped_organs": [],
            "rows": [],
            "error": repr(exc),
        }


def _organ_rows(
    *,
    raw_context: Mapping[str, Any],
    surface_context: Mapping[str, Any],
    reply_control_packet: Mapping[str, Any],
) -> list[dict[str, Any]]:
    integration_rows = [dict(row) for row in organ_main_chain_integration_table()]
    organ_packet = dict(surface_context.get("organ_influence_packet") or {})
    surface_packet = dict(surface_context.get("surface_influence_packet") or {})
    trace = dict(surface_packet.get("organ_trace") or {})
    mapped = set(str(item) for item in list(trace.get("mapped_organs") or []))
    changed_contract_fields = _changed_contract_fields(organ_packet)
    rendered_surface = json.dumps(
        {
            "organ_influence_packet": organ_packet,
            "surface_influence_packet": surface_packet,
            "reply_control_packet": dict(reply_control_packet or {}),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    rows: list[dict[str, Any]] = []
    for row in integration_rows:
        organ_id = str(row.get("organ_id") or "")
        context_keys = [str(item) for item in list(row.get("context_keys") or [])]
        contract_fields = [str(item) for item in list(row.get("contract_fields") or [])]
        present_context_keys = [key for key in context_keys if _has_value(raw_context.get(key))]
        available_contract_fields = sorted(set(changed_contract_fields) & set(contract_fields))
        rows.append(
            {
                "organ_id": organ_id,
                "main_chain_effects": list(row.get("main_chain_effects") or []),
                "context_keys": context_keys,
                "present_context_keys": present_context_keys,
                "raw_state_collected": bool(present_context_keys),
                "mapped_to_contract": organ_id in mapped,
                "contract_fields": contract_fields,
                "available_contract_fields": available_contract_fields,
                "contract_field_available": bool(available_contract_fields),
                "surface_packet_built": bool(surface_packet),
                "reply_control_influence_applied": _reply_control_influence_applied(reply_control_packet),
                "raw_context_not_leaked": not any(marker in rendered_surface for marker in RAW_CONTEXT_MARKERS),
            }
        )
    return rows


def _checks(
    *,
    mode: str,
    fixture_summary: Mapping[str, Any],
    fixture_rows: Sequence[Mapping[str, Any]],
    live_iterations: Sequence[Mapping[str, Any]],
    live_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    if mode in {"fixture", "both"}:
        _add_check(
            checks,
            "fixture_matrix_passed",
            str(fixture_summary.get("status") or "") == "PASS",
            dict(fixture_summary),
        )
        _add_check(
            checks,
            "fixture_each_organ_has_contract_and_surface_effect",
            bool(fixture_rows)
            and all(bool(row.get("contract_effect_hits_allowed_fields")) for row in fixture_rows)
            and all(bool(row.get("effect_hits_allowed_contract")) for row in fixture_rows),
            {
                "failed": [
                    row.get("organ_id")
                    for row in fixture_rows
                    if not (
                        bool(row.get("contract_effect_hits_allowed_fields"))
                        and bool(row.get("effect_hits_allowed_contract"))
                    )
                ]
            },
        )
    if mode in {"live", "both"}:
        ok_iterations = [dict(item) for item in live_iterations if bool(item.get("ok"))]
        failed_iterations = [dict(item) for item in live_iterations if not bool(item.get("ok"))]
        live_row_list = [dict(row) for row in live_rows]
        mapped_counts = [len(list(item.get("mapped_organs") or [])) for item in ok_iterations]
        latencies = [int(item.get("latency_ms") or 0) for item in ok_iterations]
        core_rows = [row for row in live_row_list if row.get("organ_id") in CORE_LIVE_ORGANS]
        _add_check(
            checks,
            "live_surface_context_builds_without_errors",
            bool(ok_iterations) and not failed_iterations,
            {"ok_count": len(ok_iterations), "failed": failed_iterations[:3]},
        )
        _add_check(
            checks,
            "live_maps_core_organs_to_contract",
            bool(core_rows) and all(bool(row.get("mapped_to_contract")) for row in core_rows),
            {
                "failed": [
                    row.get("organ_id")
                    for row in core_rows
                    if not bool(row.get("mapped_to_contract"))
                ],
                "mapped_count_range": [
                    min(mapped_counts) if mapped_counts else 0,
                    max(mapped_counts) if mapped_counts else 0,
                ],
            },
        )
        _add_check(
            checks,
            "live_collects_runtime_state_for_all_organs",
            bool(live_row_list)
            and sum(1 for row in live_row_list if bool(row.get("raw_state_collected"))) == 23 * len(ok_iterations),
            {
                "collected_count": sum(1 for row in live_row_list if bool(row.get("raw_state_collected"))),
                "expected": 23 * len(ok_iterations),
                "missing": [
                    row.get("organ_id")
                    for row in live_row_list
                    if not bool(row.get("raw_state_collected"))
                ][:20],
            },
        )
        _add_check(
            checks,
            "live_contract_fields_are_available",
            bool(live_row_list)
            and sum(1 for row in live_row_list if bool(row.get("contract_field_available"))) >= 21 * len(ok_iterations),
            {
                "available_count": sum(1 for row in live_row_list if bool(row.get("contract_field_available"))),
                "expected_floor": 21 * len(ok_iterations),
                "missing": [
                    row.get("organ_id")
                    for row in live_row_list
                    if not bool(row.get("contract_field_available"))
                ][:20],
            },
        )
        _add_check(
            checks,
            "live_reply_control_receives_compressed_influence",
            bool(live_row_list) and all(bool(row.get("reply_control_influence_applied")) for row in live_row_list),
            {"failed_count": sum(1 for row in live_row_list if not bool(row.get("reply_control_influence_applied")))},
        )
        _add_check(
            checks,
            "live_packets_do_not_leak_raw_context",
            bool(live_row_list) and all(bool(row.get("raw_context_not_leaked")) for row in live_row_list),
            {"failed": [row.get("organ_id") for row in live_row_list if not bool(row.get("raw_context_not_leaked"))][:20]},
        )
        _add_check(
            checks,
            "live_runtime_integration_latency_is_bounded",
            bool(latencies) and max(latencies) < 5_000,
            {
                "min_ms": min(latencies) if latencies else 0,
                "max_ms": max(latencies) if latencies else 0,
                "avg_ms": round(statistics.mean(latencies), 2) if latencies else 0,
            },
        )
    return checks


def _changed_contract_fields(packet: Mapping[str, Any]) -> list[str]:
    return [
        field_name
        for field_name in CONTRACT_FIELDS
        if packet.get(field_name) not in (None, "", [], {}, ())
    ]


def _reply_control_influence_applied(packet: Mapping[str, Any]) -> bool:
    return bool(
        packet.get("emotional_tone")
        or packet.get("relationship_distance")
        or list(packet.get("facts") or [])
        or list(packet.get("style_anchor") or [])
        or list(packet.get("must_not_do") or [])
    )


def _has_value(value: Any) -> bool:
    if value in (None, "", [], {}, ()):
        return False
    if isinstance(value, Mapping):
        return any(_has_value(child) for child in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_has_value(item) for item in value)
    return True


def _add_check(checks: list[dict[str, Any]], name: str, passed: bool, details: Mapping[str, Any] | None = None) -> None:
    checks.append({"name": name, "passed": bool(passed), "details": dict(details or {})})


def _markdown_report(summary: Mapping[str, Any], details: Mapping[str, Any]) -> str:
    lines = [
        "# Organ Runtime Integration Validation",
        "",
        "This validates runtime organ collection through the compressed contract path. It is not text quality scoring.",
        "",
        f"- status: `{summary.get('status')}`",
        f"- mode: `{summary.get('mode')}`",
        f"- organ_count: `{summary.get('organ_count')}`",
        f"- failed_count: `{summary.get('failed_count')}`",
        f"- output_dir: `{summary.get('output_dir')}`",
        "",
        "## Checks",
        "",
    ]
    for check in list(details.get("checks") or []):
        marker = "PASS" if check.get("passed") else "FAIL"
        lines.append(f"- `{marker}` {check.get('name')}: `{json.dumps(check.get('details') or {}, ensure_ascii=False)}`")
    live_rows = [dict(row) for row in list(details.get("live_rows") or []) if isinstance(row, Mapping)]
    if live_rows:
        lines.extend(["", "## Live Organ Rows", ""])
        for row in live_rows:
            lines.append(
                "- `{organ}` collected=`{collected}` mapped=`{mapped}` fields=`{fields}` keys=`{keys}` effects=`{effects}`".format(
                    organ=row.get("organ_id"),
                    collected=bool(row.get("raw_state_collected")),
                    mapped=bool(row.get("mapped_to_contract")),
                    fields=",".join(str(item) for item in list(row.get("available_contract_fields") or [])),
                    keys=",".join(str(item) for item in list(row.get("present_context_keys") or [])),
                    effects=",".join(str(item) for item in list(row.get("main_chain_effects") or [])),
                )
            )
    return "\n".join(lines).rstrip() + "\n"


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            file.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate runtime organ state integration into the main-chain contract.")
    parser.add_argument("--mode", choices=("fixture", "live", "both"), default="fixture")
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--out-dir", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    result = run_validation(mode=args.mode, iterations=args.iterations, out_dir=args.out_dir)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if str(result["summary"].get("status")) == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
