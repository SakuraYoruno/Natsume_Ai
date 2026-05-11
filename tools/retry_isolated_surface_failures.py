from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.persona.isolated_surface_harness import (  # noqa: E402
    build_isolated_surface_input,
    parse_surface_candidates,
    score_isolated_surface_candidates,
    select_best_isolated_candidate,
)
from tools.chat_ab_generate import _load_secret_store_provider_key, load_dotenv  # noqa: E402
from tools.isolated_surface_harness import _case_request_payload, _read_json_or_jsonl  # noqa: E402
from tools.run_isolated_surface_live import _run_one  # noqa: E402


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


def _selected_rank(selected: Mapping[str, Any] | None) -> tuple[int, int, int, float, int]:
    if not selected:
        return (0, 0, -999, 0.0, -999)
    text = str(selected.get("text") or "")
    return (
        1 if selected.get("passed") else 0,
        0 if selected.get("hard_fail") else 1,
        -len(selected.get("control_violations") or ()),
        float(selected.get("score") or 0.0),
        -len(text),
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
            scores = score_isolated_surface_candidates(request, candidate_texts)
            selected = select_best_isolated_candidate(scores)
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


def retry_case_indexes(
    *,
    cases: Sequence[Mapping[str, Any]],
    rescored_rows: Sequence[Mapping[str, Any]],
    threshold: float,
    retry_limit: int | None = None,
) -> list[int]:
    by_case = {str(row.get("case_id") or ""): row for row in rescored_rows}
    indexes: list[int] = []
    for index, case in enumerate(cases, start=1):
        row = by_case.get(_case_id(index, case))
        selected = row.get("selected") if isinstance(row, Mapping) else None
        if _selected_needs_retry(selected if isinstance(selected, Mapping) else None, threshold=threshold):
            indexes.append(index)
    if retry_limit is not None and retry_limit >= 0:
        indexes = indexes[:retry_limit]
    return indexes


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
        use_retry = _selected_rank(retry_selected) > _selected_rank(source_selected)
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
) -> dict[str, Any]:
    selected = [row.get("selected") for row in merged_rows if isinstance(row.get("selected"), Mapping)]
    scores = [float(row.get("score") or 0.0) for row in selected]
    pass_count = sum(1 for row in selected if row.get("passed"))
    low_score_count = sum(1 for row in selected if _selected_needs_retry(row, threshold=threshold))
    retry_latencies = [int(row.get("latency_ms") or 0) for row in retry_results if int(row.get("latency_ms") or 0) > 0]
    return {
        "cases": int(cases_count),
        "source_scored_count": len(source_rows),
        "retry_case_count": len(retry_indexes),
        "retry_completed_count": len(retry_results),
        "retry_generated_count": sum(1 for row in retry_results if row.get("ok")),
        "retry_case_indexes": list(retry_indexes),
        "selected_count": len(selected),
        "pass_count": pass_count,
        "pass_rate": round(pass_count / len(selected), 4) if selected else None,
        "avg_selected_score": round(statistics.fmean(scores), 3) if scores else None,
        "low_score_count": low_score_count,
        "hard_fail_count": sum(1 for row in selected if row.get("hard_fail")),
        "retry_improved_count": sum(1 for row in merged_rows if row.get("improved")),
        "retry_selected_count": sum(1 for row in merged_rows if row.get("selected_from") == "retry"),
        "retry_latency_ms_total": sum(retry_latencies),
        "total_elapsed_ms": int((time.perf_counter() - started) * 1000),
        "threshold": float(threshold),
        "output_dir": str(out_dir),
    }


async def run_retry(args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    cases = _read_json_or_jsonl(args.cases)
    soul = args.soul.read_text(encoding="utf-8-sig")
    raw_rows = _read_jsonl(args.source_run / "live_raw_generations.jsonl")
    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    source_rows = rescore_source_outputs(cases=cases, raw_rows=raw_rows, soul_md=soul)
    retry_indexes = retry_case_indexes(
        cases=cases,
        rescored_rows=source_rows,
        threshold=float(args.threshold),
        retry_limit=args.retry_limit,
    )
    retry_score_rows: list[dict[str, Any]] = []
    retry_results: list[dict[str, Any]] = []
    _write_jsonl(out_dir / "source_rescored_scores.jsonl", source_rows)

    if not args.dry_run and retry_indexes:
        load_dotenv(ROOT / ".env")
        provider = str(args.provider or "deepseek").strip()
        model = str(args.model or os.environ.get("HOST_DEEPSEEK_MODEL_ID") or "deepseek-v4-flash").strip()
        api_key = str(os.environ.get("DEEPSEEK_API_KEY") or "").strip() or _load_secret_store_provider_key(provider)
        if not api_key:
            raise RuntimeError(f"missing api key for provider={provider}")
        base_url_override = str(args.base_url or os.environ.get("HOST_DEEPSEEK_BASE_URL") or "").strip() or None
        gen_params = {"temperature": args.temperature, "top_p": args.top_p, "max_tokens": args.max_tokens}
        raw_path = out_dir / "retry_raw_generations.jsonl"
        score_path = out_dir / "retry_scores.jsonl"
        raw_path.write_text("", encoding="utf-8")
        score_path.write_text("", encoding="utf-8")
        semaphore = asyncio.Semaphore(max(1, int(args.concurrency)))
        tasks = [
            asyncio.create_task(
                _run_one(
                    index=index,
                    case=cases[index - 1],
                    soul=soul,
                    semaphore=semaphore,
                    provider=provider,
                    model=model,
                    api_key=api_key,
                    timeout_ms=int(args.timeout_ms),
                    gen_params=gen_params,
                    base_url_override=base_url_override,
                )
            )
            for index in retry_indexes
        ]
        for task in asyncio.as_completed(tasks):
            result = await task
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
    )
    if args.dry_run:
        summary["dry_run"] = True
    summary["source_run"] = str(args.source_run)
    summary["retry_case_ids"] = [_case_id(index, cases[index - 1]) for index in retry_indexes]
    _write_json(out_dir / "summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Retry only failed or low-scoring isolated surface live cases.")
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--source-run", required=True, type=Path, help="Previous live run directory with live_raw_generations.jsonl.")
    parser.add_argument("--soul", default=ROOT / "Soul.md", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--threshold", type=float, default=80.0)
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
