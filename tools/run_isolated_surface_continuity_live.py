from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.integrations.llm_helpers import invoke_llm_text  # noqa: E402
from host_brain.persona.isolated_surface_continuity import (  # noqa: E402
    ContinuityTurnSurfaceInput,
    build_continuity_surface_inputs,
)
from host_brain.persona.surface_runtime import SurfaceRuntime  # noqa: E402
from tools.live_env import load_dotenv, load_secret_store_provider_key  # noqa: E402
from tools.isolated_surface_harness import _read_json_or_jsonl  # noqa: E402


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as file:
        file.write(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True) + "\n")


def _percentile(values: list[int], q: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return ordered[index]


def _flatten_turns(cases: list[dict[str, Any]], *, soul_md: str) -> list[ContinuityTurnSurfaceInput]:
    turns: list[ContinuityTurnSurfaceInput] = []
    for dialogue_index, case in enumerate(cases, start=1):
        case_id = str(case.get("case_id") or f"dialogue_{dialogue_index:03d}")
        turns.extend(build_continuity_surface_inputs({**case, "case_id": case_id}, soul_md=soul_md))
    return turns


def _dialogue_turn_counts(turns: list[ContinuityTurnSurfaceInput]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for turn in turns:
        counts[turn.case_id] = counts.get(turn.case_id, 0) + 1
    return counts


def _summarize(
    *,
    dialogues_count: int,
    turns_count: int,
    expected_dialogue_turns: Mapping[str, int],
    completed: int,
    results: list[dict[str, Any]],
    started: float,
    provider: str,
    model: str,
    concurrency: int,
    gen_params: Mapping[str, Any],
    out_dir: Path,
) -> dict[str, Any]:
    outputs = [dict(row.get("output") or {}) for row in results if isinstance(row.get("output"), Mapping)]
    latencies = [int(row.get("latency_ms") or 0) for row in results if int(row.get("latency_ms") or 0) > 0]
    generated_count = sum(1 for row in results if row.get("ok"))
    fact_counts = [int(row.get("fact_count") or 0) for row in results if int(row.get("fact_count") or 0) > 0]
    observation_counts: dict[str, int] = {}
    for row in outputs:
        for code in list(row.get("observation_codes") or []):
            observation_counts[code] = observation_counts.get(code, 0) + 1
    dialogue_turns: dict[str, int] = {}
    for row in results:
        case_id = str(row.get("case_id") or "")
        if not case_id:
            continue
        dialogue_turns[case_id] = dialogue_turns.get(case_id, 0) + 1
    dialogue_completed_count = sum(
        1
        for case_id, expected_count in expected_dialogue_turns.items()
        if dialogue_turns.get(case_id, 0) == expected_count and expected_count > 0
    )
    return {
        "dialogues": dialogues_count,
        "completed": completed,
        "turns": turns_count,
        "generated_count": generated_count,
        "errors": completed - generated_count,
        "output_count": len(outputs),
        "empty_output_count": sum(1 for row in outputs if not str(row.get("direct_text") or "").strip()),
        "dialogue_completed_count": dialogue_completed_count,
        "dialogue_completion_rate": round(dialogue_completed_count / dialogues_count, 4) if dialogues_count else None,
        "facts_budget_max": max(fact_counts) if fact_counts else 0,
        "facts_budget_violations": sum(1 for count in fact_counts if count > 6),
        "long_dialogues_30_plus": sum(1 for count in expected_dialogue_turns.values() if count >= 30),
        "max_turns_per_dialogue": max(expected_dialogue_turns.values()) if expected_dialogue_turns else 0,
        "min_turns_per_dialogue": min(expected_dialogue_turns.values()) if expected_dialogue_turns else 0,
        "observation_count": sum(observation_counts.values()),
        "observation_reason_counts": dict(sorted(observation_counts.items())),
        "latency_ms_p50": _percentile(latencies, 0.5),
        "latency_ms_p95": _percentile(latencies, 0.95),
        "total_elapsed_ms": int((time.perf_counter() - started) * 1000),
        "provider": provider,
        "model": model,
        "concurrency": concurrency,
        "gen_params": dict(gen_params),
        "output_dir": str(out_dir),
        "evaluation_mode": "manual_review_no_code_scoring",
    }


async def _run_turn(
    *,
    turn: ContinuityTurnSurfaceInput,
    semaphore: asyncio.Semaphore,
    provider: str,
    model: str,
    api_key: str,
    timeout_ms: int,
    gen_params: Mapping[str, Any],
    base_url_override: str | None,
) -> dict[str, Any]:
    async with semaphore:
        started = time.perf_counter()
        trace = turn.as_trace()
        try:
            request = turn.request
            fact_count = len(request.control.facts)

            async def generate(messages: list[Mapping[str, str]]) -> str:
                return await asyncio.to_thread(
                    invoke_llm_text,
                    provider_name=provider,
                    model_id=model,
                    api_key=api_key,
                    timeout_ms=timeout_ms,
                    messages=repair_messages,
                    gen_params=dict(gen_params),
                    base_url_override=base_url_override,
                )

            runtime = SurfaceRuntime(generate)
            result = await runtime.run(request)
            latency_ms = result.latency_ms or int((time.perf_counter() - started) * 1000)
            raw_row = {
                **trace,
                "ok": True,
                "provider": provider,
                "model": model,
                "latency_ms": latency_ms,
                "raw_output": str(result.raw_output or "").strip(),
                "candidate_texts": [candidate.text for candidate in result.candidates],
                "direct_text": result.selected_text,
                "selected_text": result.selected_text,
                "fact_count": fact_count,
                "observation_codes": list(result.observation_codes),
                "selected": result.selected.as_dict() if result.selected else None,
                "generation_trace": dict(result.generation_trace),
            }
            return {
                **trace,
                "raw": raw_row,
                "output": raw_row,
                "fact_count": fact_count,
                "ok": True,
                "latency_ms": latency_ms,
            }
        except Exception as exc:  # pragma: no cover - live diagnostics
            latency_ms = int((time.perf_counter() - started) * 1000)
            raw_row = {
                **trace,
                "ok": False,
                "provider": provider,
                "model": model,
                "latency_ms": latency_ms,
                "error": repr(exc),
            }
            return {
                **trace,
                "raw": raw_row,
                "output": None,
                "ok": False,
                "latency_ms": latency_ms,
            }


async def run_live(args: argparse.Namespace) -> dict[str, Any]:
    load_dotenv(ROOT / ".env")
    provider = str(args.provider or "deepseek").strip()
    model = str(args.model or os.environ.get("HOST_DEEPSEEK_MODEL_ID") or "deepseek-v4-flash").strip()
    api_key = str(os.environ.get("DEEPSEEK_API_KEY") or "").strip() or load_secret_store_provider_key(provider)
    if not api_key:
        raise RuntimeError(f"missing api key for provider={provider}")
    base_url_override = str(args.base_url or os.environ.get("HOST_DEEPSEEK_BASE_URL") or "").strip() or None
    gen_params = {"temperature": args.temperature, "top_p": args.top_p, "max_tokens": args.max_tokens}
    cases = _read_json_or_jsonl(args.cases)
    soul_md = args.soul.read_text(encoding="utf-8-sig")
    turns = _flatten_turns(cases, soul_md=soul_md)
    expected_dialogue_turns = _dialogue_turn_counts(turns)
    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / "live_raw_generations.jsonl"
    output_path = out_dir / "live_outputs.jsonl"
    progress_path = out_dir / "progress.json"
    summary_path = out_dir / "summary.json"
    raw_path.write_text("", encoding="utf-8")
    output_path.write_text("", encoding="utf-8")

    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    completed = 0
    semaphore = asyncio.Semaphore(max(1, int(args.concurrency)))
    tasks = [
        asyncio.create_task(
            _run_turn(
                turn=turn,
                semaphore=semaphore,
                provider=provider,
                model=model,
                api_key=api_key,
                timeout_ms=int(args.timeout_ms),
                gen_params=gen_params,
                base_url_override=base_url_override,
            )
        )
        for turn in turns
    ]
    _write_json(
        progress_path,
        _summarize(
            dialogues_count=len(cases),
            turns_count=len(turns),
            expected_dialogue_turns=expected_dialogue_turns,
            completed=completed,
            results=results,
            started=started,
            provider=provider,
            model=model,
            concurrency=int(args.concurrency),
            gen_params=gen_params,
            out_dir=out_dir,
        ),
    )
    for task in asyncio.as_completed(tasks):
        row = await task
        completed += 1
        results.append(row)
        _append_jsonl(raw_path, row["raw"])
        if isinstance(row.get("output"), Mapping):
            _append_jsonl(output_path, row["output"])
        summary = _summarize(
            dialogues_count=len(cases),
            turns_count=len(turns),
            expected_dialogue_turns=expected_dialogue_turns,
            completed=completed,
            results=results,
            started=started,
            provider=provider,
            model=model,
            concurrency=int(args.concurrency),
            gen_params=gen_params,
            out_dir=out_dir,
        )
        _write_json(progress_path, summary)
        if completed % int(args.progress_every) == 0 or completed == len(turns):
            print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)

    final_summary = _summarize(
        dialogues_count=len(cases),
        turns_count=len(turns),
        expected_dialogue_turns=expected_dialogue_turns,
        completed=completed,
        results=results,
        started=started,
        provider=provider,
        model=model,
        concurrency=int(args.concurrency),
        gen_params=gen_params,
        out_dir=out_dir,
    )
    _write_json(summary_path, final_summary)
    return final_summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run live compressed-continuity cases through the isolated surface generator.")
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--soul", default=ROOT / "Soul.md", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--provider", default="deepseek")
    parser.add_argument("--model", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--timeout-ms", type=int, default=60_000)
    parser.add_argument("--temperature", type=float, default=0.35)
    parser.add_argument("--top-p", type=float, default=0.8)
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--progress-every", type=int, default=3)
    args = parser.parse_args(argv)
    summary = asyncio.run(run_live(args))
    print("FINAL " + json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
