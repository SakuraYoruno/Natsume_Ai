from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.persona.surface.input_validation import build_isolated_surface_input  # noqa: E402
from host_brain.persona.surface.candidate_scoring import (  # noqa: E402
    parse_surface_candidates,
    score_isolated_surface_candidates,
)
from tools.live_env import load_dotenv, load_secret_store_provider_key  # noqa: E402
from tools.isolated_surface_harness import _case_request_payload, _read_json_or_jsonl  # noqa: E402
from tools.run_isolated_surface_live import _run_one  # noqa: E402
from tools.surface_tool_contract import (  # noqa: E402
    OFFLINE_FIXTURE_EVAL_LAYER,
    SURFACE_LOW_LEVEL_ENTRYPOINT_ALLOWED as _SURFACE_LOW_LEVEL_ENTRYPOINT_ALLOWED,
    surface_tool_contract_payload,
)


SURFACE_TOOL_LAYER = OFFLINE_FIXTURE_EVAL_LAYER
SURFACE_LOW_LEVEL_ENTRYPOINT_ALLOWED = _SURFACE_LOW_LEVEL_ENTRYPOINT_ALLOWED


async def _run_retry_attempt(
    *,
    attempt: int,
    **kwargs: Any,
) -> dict[str, Any]:
    result = await _run_one(**kwargs)
    result["retry_attempt"] = int(attempt)
    for key in ("raw", "score"):
        if isinstance(result.get(key), dict):
            result[key]["retry_attempt"] = int(attempt)
    return result


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            file.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def _append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as file:
        file.write(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        text = line.strip()
        if not text:
            continue
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError(f"{path}:{line_no} must be a JSON object")
        rows.append(payload)
    return rows


def _case_id(index: int, case: Mapping[str, Any]) -> str:
    return str(case.get("case_id") or f"case_{index:03d}")


def _selected_needs_retry(selected: Mapping[str, Any] | None, *, threshold: float) -> bool:
    if not selected:
        return True
    return bool(not selected.get("passed") or float(selected.get("score") or 0.0) < float(threshold))


def _retry_reason(
    selected: Mapping[str, Any] | None,
    *,
    threshold: float,
    challenge_threshold: float | None = None,
) -> str:
    if _selected_needs_retry(selected, threshold=threshold):
        return "fail_or_below_threshold"
    if challenge_threshold is None:
        return ""
    if not selected or not selected.get("passed"):
        return ""
    score = float(selected.get("score") or 0.0)
    if float(threshold) <= score < float(challenge_threshold):
        return "weak_pass_challenge"
    return ""


def _selected_text(selected: Mapping[str, Any] | None) -> str:
    if not selected:
        return ""
    return str(selected.get("text") or "").strip()


def _scene_from_case_id(case_id: str) -> str:
    parts = [part for part in str(case_id or "").lower().split("_") if part]
    if len(parts) >= 2 and parts[0] == "relstage":
        return parts[1]
    return parts[0] if parts else "unknown"


def _unique_nonempty_texts(texts: Iterable[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for text in texts:
        clean = str(text or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        unique.append(clean)
    return unique


def _request_with_retry_avoidance(
    case: Mapping[str, Any],
    *,
    soul: str,
    avoid_text: str = "",
    avoid_texts: Sequence[str] | None = None,
) -> Any:
    payload = _case_request_payload(case, soul)
    texts_to_avoid = _unique_nonempty_texts([avoid_text, *list(avoid_texts or [])])
    if texts_to_avoid:
        must_not_do = [*list(payload.get("must_not_do") or []), *texts_to_avoid]
        avoid_summary = "；".join(texts_to_avoid[:12])
        facts = [
            *list(payload.get("facts") or []),
            f"重试要求：上一轮输出和本批同场景输出重复；这次换一种顺中文的表达，不要重复这些句子：{avoid_summary}",
        ]
        payload["must_not_do"] = must_not_do
        payload["facts"] = facts
    return build_isolated_surface_input(payload)


def _selected_rank(selected: Mapping[str, Any] | None) -> tuple[int, int, int, float]:
    if not selected:
        return (0, 0, -999, 0.0)
    return (
        1 if selected.get("passed") else 0,
        0 if selected.get("hard_fail") else 1,
        -len(selected.get("control_violations") or ()),
        float(selected.get("score") or 0.0),
    )


def rescore_source_outputs(
    *,
    cases: Sequence[Mapping[str, Any]],
    raw_rows: Sequence[Mapping[str, Any]],
    soul_md: str,
) -> list[dict[str, Any]]:
    raw_by_case = {str(row.get("case_id") or ""): dict(row) for row in raw_rows if row.get("case_id")}
    rows: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        case_id = _case_id(index, case)
        raw = raw_by_case.get(case_id)
        if not raw:
            rows.append({"index": index, "case_id": case_id, "error": "missing source raw output", "selected": None})
            continue
        try:
            request = build_isolated_surface_input(_case_request_payload(case, soul_md))
            candidate_texts = parse_surface_candidates(raw.get("raw_output"))
            scores = score_isolated_surface_candidates(request, candidate_texts[:1])
            selected = scores[0] if scores else None
            rows.append(
                {
                    "index": index,
                    "case_id": case_id,
                    "source_raw_ok": bool(raw.get("ok", True)),
                    "parsed_candidates": candidate_texts,
                    "candidates": [score.as_dict() for score in scores],
                    "selected": selected.as_dict() if selected else None,
                }
            )
        except Exception as exc:
            rows.append({"index": index, "case_id": case_id, "error": repr(exc), "selected": None})
    return rows


def source_score_rows_from_existing(
    *,
    cases: Sequence[Mapping[str, Any]],
    existing_rows: Sequence[Mapping[str, Any]],
    soul_md: str,
) -> list[dict[str, Any]]:
    by_case = {str(row.get("case_id") or ""): dict(row) for row in existing_rows if row.get("case_id")}
    rows: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        case_id = _case_id(index, case)
        row = by_case.get(case_id)
        if not row:
            rows.append({"index": index, "case_id": case_id, "error": "missing source score row", "selected": None})
            continue
        selected = row.get("selected") if isinstance(row.get("selected"), Mapping) else None
        selected_text = _selected_text(selected if isinstance(selected, Mapping) else None)
        if selected_text:
            request = build_isolated_surface_input(_case_request_payload(case, soul_md))
            scores = score_isolated_surface_candidates(request, [selected_text])
            rescored = scores[0] if scores else None
            selected = rescored.as_dict() if rescored else selected
        rows.append(
            {
                "index": int(row.get("index") or index),
                "case_id": case_id,
                "source_score_kind": str(row.get("source_score_kind") or "existing_score"),
                "selected": dict(selected or {}) if selected else None,
            }
        )
    return rows


def load_source_score_rows(
    *,
    cases: Sequence[Mapping[str, Any]],
    source_run: Path,
    soul_md: str,
) -> tuple[list[dict[str, Any]], str]:
    live_raw_path = source_run / "live_raw_generations.jsonl"
    if live_raw_path.exists():
        return rescore_source_outputs(cases=cases, raw_rows=_read_jsonl(live_raw_path), soul_md=soul_md), "live_raw_generations"

    merged_path = source_run / "merged_scores.jsonl"
    if merged_path.exists():
        return source_score_rows_from_existing(cases=cases, existing_rows=_read_jsonl(merged_path), soul_md=soul_md), "merged_scores"

    rescored_path = source_run / "source_rescored_scores.jsonl"
    if rescored_path.exists():
        return source_score_rows_from_existing(cases=cases, existing_rows=_read_jsonl(rescored_path), soul_md=soul_md), "source_rescored_scores"

    raise FileNotFoundError(
        f"{source_run} must contain live_raw_generations.jsonl, merged_scores.jsonl, or source_rescored_scores.jsonl"
    )


def retry_case_indexes(
    *,
    cases: Sequence[Mapping[str, Any]],
    rescored_rows: Sequence[Mapping[str, Any]],
    threshold: float,
    challenge_threshold: float | None = None,
    retry_limit: int | None = None,
) -> list[int]:
    by_case = {str(row.get("case_id") or ""): row for row in rescored_rows}
    indexes: list[int] = []
    for index, case in enumerate(cases, start=1):
        row = by_case.get(_case_id(index, case))
        selected = row.get("selected") if isinstance(row, Mapping) else None
        reason = _retry_reason(
            selected if isinstance(selected, Mapping) else None,
            threshold=threshold,
            challenge_threshold=challenge_threshold,
        )
        if reason:
            indexes.append(index)
    if retry_limit is not None and retry_limit >= 0:
        indexes = indexes[:retry_limit]
    return indexes


def duplicate_surface_case_indexes(
    *,
    cases: Sequence[Mapping[str, Any]],
    rescored_rows: Sequence[Mapping[str, Any]],
) -> list[int]:
    by_case = {str(row.get("case_id") or ""): row for row in rescored_rows}
    indexes_by_text: dict[str, list[int]] = defaultdict(list)
    for index, case in enumerate(cases, start=1):
        row = by_case.get(_case_id(index, case))
        selected = row.get("selected") if isinstance(row, Mapping) else None
        text = _selected_text(selected if isinstance(selected, Mapping) else None)
        if text:
            indexes_by_text[text].append(index)
    return sorted(
        index
        for indexes in indexes_by_text.values()
        if len(indexes) > 1
        for index in indexes
    )


def duplicate_surface_avoid_text_sets(
    *,
    cases: Sequence[Mapping[str, Any]],
    rescored_rows: Sequence[Mapping[str, Any]],
) -> dict[int, list[str]]:
    by_case = {str(row.get("case_id") or ""): row for row in rescored_rows}
    duplicate_indexes = set(duplicate_surface_case_indexes(cases=cases, rescored_rows=rescored_rows))
    texts_by_scene: dict[str, list[str]] = defaultdict(list)
    own_text_by_index: dict[int, str] = {}
    for index, case in enumerate(cases, start=1):
        case_id = _case_id(index, case)
        row = by_case.get(case_id)
        selected = row.get("selected") if isinstance(row, Mapping) else None
        text = _selected_text(selected if isinstance(selected, Mapping) else None)
        if not text:
            continue
        scene = _scene_from_case_id(case_id)
        texts_by_scene[scene].append(text)
        own_text_by_index[index] = text

    avoid: dict[int, list[str]] = {}
    for index in sorted(duplicate_indexes):
        case_id = _case_id(index, cases[index - 1])
        scene = _scene_from_case_id(case_id)
        avoid[index] = _unique_nonempty_texts([own_text_by_index.get(index, ""), *texts_by_scene.get(scene, [])])
    return avoid


def retry_case_reasons(
    *,
    cases: Sequence[Mapping[str, Any]],
    rescored_rows: Sequence[Mapping[str, Any]],
    threshold: float,
    challenge_threshold: float | None = None,
    retry_limit: int | None = None,
) -> dict[int, str]:
    indexes = retry_case_indexes(
        cases=cases,
        rescored_rows=rescored_rows,
        threshold=threshold,
        challenge_threshold=challenge_threshold,
        retry_limit=retry_limit,
    )
    retry_index_set = set(indexes)
    by_case = {str(row.get("case_id") or ""): row for row in rescored_rows}
    reasons: dict[int, str] = {}
    for index, case in enumerate(cases, start=1):
        if index not in retry_index_set:
            continue
        row = by_case.get(_case_id(index, case))
        selected = row.get("selected") if isinstance(row, Mapping) else None
        reasons[index] = _retry_reason(
            selected if isinstance(selected, Mapping) else None,
            threshold=threshold,
            challenge_threshold=challenge_threshold,
        )
    return reasons


def merge_source_and_retry_scores(
    *,
    source_rows: Sequence[Mapping[str, Any]],
    retry_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    retry_by_case: dict[str, list[Mapping[str, Any]]] = {}
    for row in retry_rows:
        retry_by_case.setdefault(str(row.get("case_id") or ""), []).append(row)

    merged: list[dict[str, Any]] = []
    for source in source_rows:
        case_id = str(source.get("case_id") or "")
        source_selected = source.get("selected") if isinstance(source.get("selected"), Mapping) else None
        retry_options = retry_by_case.get(case_id, [])
        best_retry = max(
            retry_options,
            key=lambda row: _selected_rank(row.get("selected") if isinstance(row.get("selected"), Mapping) else None),
            default=None,
        )
        retry_selected = (
            best_retry.get("selected")
            if best_retry is not None and isinstance(best_retry.get("selected"), Mapping)
            else None
        )
        retry_reason = str(best_retry.get("retry_reason") or "") if best_retry is not None else ""
        use_retry = _selected_rank(retry_selected) > _selected_rank(source_selected)
        if retry_reason == "batch_duplicate_surface" and retry_selected and retry_selected.get("passed"):
            use_retry = True
        selected = retry_selected if use_retry else source_selected
        source_score = float((source_selected or {}).get("score") or 0.0)
        selected_score = float((selected or {}).get("score") or 0.0)
        merged.append(
            {
                "index": source.get("index"),
                "case_id": case_id,
                "selected_from": "retry" if use_retry else "source",
                "improved": bool(use_retry and selected_score > source_score),
                "source_selected": dict(source_selected or {}),
                "retry_selected": dict(retry_selected or {}),
                "retry_reason": retry_reason,
                "selected": dict(selected or {}) if selected else None,
            }
        )
    return merged


def summarize_merged(
    *,
    cases_count: int,
    source_rows: Sequence[Mapping[str, Any]],
    retry_indexes: Sequence[int],
    retry_results: Sequence[Mapping[str, Any]],
    merged_rows: Sequence[Mapping[str, Any]],
    started: float,
    threshold: float,
    out_dir: Path,
    challenge_threshold: float | None = None,
    retry_reasons: Mapping[int, str] | None = None,
) -> dict[str, Any]:
    selected = [row.get("selected") for row in merged_rows if isinstance(row.get("selected"), Mapping)]
    scores = [float(row.get("score") or 0.0) for row in selected]
    pass_count = sum(1 for row in selected if row.get("passed"))
    low_score_count = sum(1 for row in selected if _selected_needs_retry(row, threshold=threshold))
    weak_pass_remaining_count = 0
    if challenge_threshold is not None:
        weak_pass_remaining_count = sum(
            1
            for row in selected
            if row.get("passed") and float(threshold) <= float(row.get("score") or 0.0) < float(challenge_threshold)
        )
    retry_latencies = [int(row.get("latency_ms") or 0) for row in retry_results if int(row.get("latency_ms") or 0) > 0]
    retry_reason_values = list(dict(retry_reasons or {}).values())
    return {
        **surface_tool_contract_payload("retry_isolated_surface_failures"),
        "cases": int(cases_count),
        "source_scored_count": len(source_rows),
        "retry_case_count": len(retry_indexes),
        "retry_completed_count": len(retry_results),
        "retry_attempt_completed_count": len(retry_results),
        "retry_generated_count": sum(1 for row in retry_results if row.get("ok")),
        "retry_case_indexes": list(retry_indexes),
        "retry_fail_or_below_threshold_count": retry_reason_values.count("fail_or_below_threshold"),
        "retry_weak_pass_challenge_count": retry_reason_values.count("weak_pass_challenge"),
        "retry_batch_duplicate_surface_count": retry_reason_values.count("batch_duplicate_surface"),
        "selected_count": len(selected),
        "pass_count": pass_count,
        "pass_rate": round(pass_count / len(selected), 4) if selected else None,
        "avg_selected_score": round(statistics.fmean(scores), 3) if scores else None,
        "low_score_count": low_score_count,
        "weak_pass_remaining_count": weak_pass_remaining_count,
        "hard_fail_count": sum(1 for row in selected if row.get("hard_fail")),
        "retry_improved_count": sum(1 for row in merged_rows if row.get("improved")),
        "retry_selected_count": sum(1 for row in merged_rows if row.get("selected_from") == "retry"),
        "retry_latency_ms_total": sum(retry_latencies),
        "total_elapsed_ms": int((time.perf_counter() - started) * 1000),
        "threshold": float(threshold),
        "challenge_threshold": float(challenge_threshold) if challenge_threshold is not None else None,
        "output_dir": str(out_dir),
    }


async def run_retry(args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    cases = _read_json_or_jsonl(args.cases)
    soul = args.soul.read_text(encoding="utf-8-sig")
    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    source_rows, source_score_kind = load_source_score_rows(cases=cases, source_run=args.source_run, soul_md=soul)
    challenge_threshold = args.challenge_threshold
    quality_retry_indexes = retry_case_indexes(
        cases=cases,
        rescored_rows=source_rows,
        threshold=float(args.threshold),
        challenge_threshold=float(challenge_threshold) if challenge_threshold is not None else None,
    )
    retry_reasons = retry_case_reasons(
        cases=cases,
        rescored_rows=source_rows,
        threshold=float(args.threshold),
        challenge_threshold=float(challenge_threshold) if challenge_threshold is not None else None,
    )
    duplicate_indexes = (
        duplicate_surface_case_indexes(cases=cases, rescored_rows=source_rows)
        if bool(args.retry_duplicate_surfaces)
        else []
    )
    duplicate_avoid_texts = (
        duplicate_surface_avoid_text_sets(cases=cases, rescored_rows=source_rows)
        if bool(args.retry_duplicate_surfaces)
        else {}
    )
    retry_index_set = set(quality_retry_indexes)
    for index in duplicate_indexes:
        retry_index_set.add(index)
        retry_reasons.setdefault(index, "batch_duplicate_surface")
    retry_indexes = sorted(retry_index_set)
    if args.retry_limit is not None and args.retry_limit >= 0:
        retry_indexes = retry_indexes[: int(args.retry_limit)]
        retry_reasons = {index: reason for index, reason in retry_reasons.items() if index in set(retry_indexes)}
        duplicate_avoid_texts = {index: texts for index, texts in duplicate_avoid_texts.items() if index in set(retry_indexes)}
    retry_score_rows: list[dict[str, Any]] = []
    retry_results: list[dict[str, Any]] = []
    _write_jsonl(out_dir / "source_rescored_scores.jsonl", source_rows)

    if not args.dry_run and retry_indexes:
        load_dotenv(ROOT / ".env")
        provider = str(args.provider or "deepseek").strip()
        model = str(args.model or os.environ.get("HOST_DEEPSEEK_MODEL_ID") or "deepseek-v4-flash").strip()
        api_key = str(os.environ.get("DEEPSEEK_API_KEY") or "").strip() or load_secret_store_provider_key(provider)
        if not api_key:
            raise RuntimeError(f"missing api key for provider={provider}")
        base_url_override = str(args.base_url or os.environ.get("HOST_DEEPSEEK_BASE_URL") or "").strip() or None
        gen_params = {"temperature": args.temperature, "top_p": args.top_p, "max_tokens": args.max_tokens}
        raw_path = out_dir / "retry_raw_generations.jsonl"
        score_path = out_dir / "retry_scores.jsonl"
        raw_path.write_text("", encoding="utf-8")
        score_path.write_text("", encoding="utf-8")
        semaphore = asyncio.Semaphore(max(1, int(args.concurrency)))
        attempts_per_case = max(1, int(args.attempts_per_case))
        tasks = []
        for index in retry_indexes:
            case_payload = cases[index - 1]
            request = _request_with_retry_avoidance(
                case_payload,
                soul=soul,
                avoid_texts=duplicate_avoid_texts.get(index, []),
            )
            case_id = _case_id(index, case_payload)
            expected_terms = [str(term) for term in list(case_payload.get("memory_expected_terms") or [])]
            for attempt in range(1, attempts_per_case + 1):
                tasks.append(
                    asyncio.create_task(
                        _run_retry_attempt(
                            attempt=attempt,
                            index=index,
                            case_id=case_id,
                            variant="retry",
                            request=request,
                            memory_expected_terms=expected_terms,
                            semaphore=semaphore,
                            provider=provider,
                            model=model,
                            api_key=api_key,
                            timeout_ms=int(args.timeout_ms),
                            gen_params=gen_params,
                            base_url_override=base_url_override,
                        )
                    )
                )
        for task in asyncio.as_completed(tasks):
            result = await task
            result_index = int((result.get("score") or {}).get("index") or result.get("index") or 0)
            retry_reason = retry_reasons.get(result_index, "")
            if retry_reason:
                result["retry_reason"] = retry_reason
                if isinstance(result.get("raw"), dict):
                    result["raw"]["retry_reason"] = retry_reason
                if isinstance(result.get("score"), dict):
                    result["score"]["retry_reason"] = retry_reason
            retry_results.append(result)
            _append_jsonl(raw_path, result["raw"])
            _append_jsonl(score_path, result["score"])
            retry_score_rows.append(dict(result["score"]))
            if len(retry_results) % int(args.progress_every) == 0 or len(retry_results) == len(tasks):
                print(
                    json.dumps(
                        {
                            "retry_completed_count": len(retry_results),
                            "retry_case_count": len(retry_indexes),
                            "retry_attempt_count": len(tasks),
                            "elapsed_ms": int((time.perf_counter() - started) * 1000),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    flush=True,
                )

    merged_rows = merge_source_and_retry_scores(source_rows=source_rows, retry_rows=retry_score_rows)
    _write_jsonl(out_dir / "merged_scores.jsonl", merged_rows)
    summary = summarize_merged(
        cases_count=len(cases),
        source_rows=source_rows,
        retry_indexes=retry_indexes,
        retry_results=retry_results,
        merged_rows=merged_rows,
        started=started,
        threshold=float(args.threshold),
        out_dir=out_dir,
        challenge_threshold=float(challenge_threshold) if challenge_threshold is not None else None,
        retry_reasons=retry_reasons,
    )
    if args.dry_run:
        summary["dry_run"] = True
    summary["source_run"] = str(args.source_run)
    summary["source_score_kind"] = source_score_kind
    summary["attempts_per_case"] = max(1, int(args.attempts_per_case))
    summary["retry_attempt_count"] = len(retry_indexes) * max(1, int(args.attempts_per_case))
    summary["retry_case_ids"] = [_case_id(index, cases[index - 1]) for index in retry_indexes]
    summary["retry_case_reasons"] = {
        _case_id(index, cases[index - 1]): retry_reasons[index]
        for index in retry_indexes
        if index in retry_reasons
    }
    _write_json(out_dir / "summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Retry only failed or low-scoring isolated surface live cases.")
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument(
        "--source-run",
        required=True,
        type=Path,
        help="Previous run directory with live_raw_generations.jsonl, merged_scores.jsonl, or source_rescored_scores.jsonl.",
    )
    parser.add_argument("--soul", default=ROOT / "Soul.md", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--threshold", type=float, default=80.0)
    parser.add_argument("--challenge-threshold", type=float, default=None, help="Also retry passed cases below this score.")
    parser.add_argument("--retry-duplicate-surfaces", action="store_true", help="Also retry cases whose selected text is exactly repeated across the batch.")
    parser.add_argument("--attempts-per-case", type=int, default=1, help="Number of independent retry generations per selected case.")
    parser.add_argument("--retry-limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true", help="Only rescore source raw outputs and list retry cases.")
    parser.add_argument("--provider", default="deepseek")
    parser.add_argument("--model", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--timeout-ms", type=int, default=60_000)
    parser.add_argument("--temperature", type=float, default=0.35)
    parser.add_argument("--top-p", type=float, default=0.8)
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--progress-every", type=int, default=1)
    args = parser.parse_args(argv)
    summary = asyncio.run(run_retry(args))
    print("FINAL " + json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
