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
from host_brain.persona.surface_runtime import SurfaceRuntime  # noqa: E402
from tools.live_env import load_dotenv, load_secret_store_provider_key  # noqa: E402
from tools.isolated_surface_harness import (  # noqa: E402
    _read_json_or_jsonl,
    _request_variants,
)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True) + "\n")


def _variant_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for row in rows:
        variant = str(row.get("variant") or "unknown")
        item = summary.setdefault(
            variant,
            {
                "output_count": 0,
                "empty_output_count": 0,
                "hard_gate_observation_count": 0,
            },
        )
        item["output_count"] += 1
        if not str(row.get("direct_text") or "").strip():
            item["empty_output_count"] += 1
        if row.get("observation_codes"):
            item["hard_gate_observation_count"] += 1
    return summary


def _percentile(values: list[int], q: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return ordered[idx]


def _summarize(
    *,
    cases_count: int,
    completed: int,
    results: list[dict[str, Any]],
    started: float,
    provider: str,
    model: str,
    concurrency: int,
    gen_params: Mapping[str, Any],
    out_dir: Path,
    with_memory: bool = False,
    with_affect: bool = False,
    case_expectations: Mapping[str, list[str]] | None = None,
) -> dict[str, Any]:
    outputs = [dict(row.get("output") or {}) for row in results if isinstance(row.get("output"), Mapping)]
    latencies = [int(row.get("latency_ms") or 0) for row in results if int(row.get("latency_ms") or 0) > 0]
    generated_count = sum(1 for row in results if row.get("ok"))
    empty_output_count = sum(1 for row in outputs if not str(row.get("direct_text") or "").strip())
    hard_gate_observation_count = sum(1 for row in outputs if row.get("observation_codes"))
    summary = {
        "cases": cases_count,
        "completed": completed,
        "generated_count": generated_count,
        "errors": completed - generated_count,
        "output_count": len(outputs),
        "empty_output_count": empty_output_count,
        "hard_gate_observation_count": hard_gate_observation_count,
        "latency_ms_p50": _percentile(latencies, 0.5),
        "latency_ms_p95": _percentile(latencies, 0.95),
        "total_elapsed_ms": int((time.perf_counter() - started) * 1000),
        "provider": provider,
        "model": model,
        "concurrency": concurrency,
        "gen_params": dict(gen_params),
        "output_dir": str(out_dir),
        "variant_summary": _variant_counts(outputs),
        "with_affect": bool(with_affect),
        "with_memory": bool(with_memory),
        "evaluation_mode": "manual_review_no_code_scoring",
    }
    return summary


def _write_manual_review_report(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    lines = [
        "# Manual Surface Output Review",
        "",
        "No code scoring is applied in this report. Review text quality by reading the outputs below.",
        "",
    ]
    for row in sorted((dict(item) for item in rows), key=lambda item: int(item.get("index") or 0)):
        observations = ", ".join(str(item) for item in list(row.get("observation_codes") or [])) or "none"
        lines.extend(
            [
                f"## {row.get('index')} · {row.get('case_id')} · {row.get('variant')}",
                "",
                f"- latency_ms: `{row.get('latency_ms')}`",
                f"- hard_gate_observations: `{observations}`",
                "",
                "```text",
                str(row.get("direct_text") or ""),
                "```",
                "",
            ]
        )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


async def _run_one(
    *,
    index: int,
    case_id: str,
    variant: str,
    request: Any,
    memory_expected_terms: list[str],
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
        try:
            async def generate(messages: list[Mapping[str, str]]) -> str:
                return await asyncio.to_thread(
                    invoke_llm_text,
                    provider_name=provider,
                    model_id=model,
                    api_key=api_key,
                    timeout_ms=timeout_ms,
                    messages=messages,
                    gen_params=dict(gen_params),
                    base_url_override=base_url_override,
                )

            runtime = SurfaceRuntime(generate)
            result = await runtime.run(request)
            latency_ms = result.latency_ms or int((time.perf_counter() - started) * 1000)
            raw_row = {
                "index": index,
                "case_id": case_id,
                "variant": variant,
                "ok": True,
                "provider": provider,
                "model": model,
                "latency_ms": latency_ms,
                "raw_output": str(result.raw_output or "").strip(),
                "candidate_texts": [candidate.text for candidate in result.candidates],
                "direct_text": result.selected_text,
                "selected_text": result.selected_text,
                "memory_expected_terms": memory_expected_terms,
                "selected": result.selected.as_dict() if result.selected else None,
                "observation_codes": list(result.observation_codes),
                "generation_trace": dict(result.generation_trace),
                "natsume_emotion_tag": result.natsume_emotion_tag,
                "user_emotion_tag": result.user_emotion_tag,
                "vts_expression_id": result.vts_expression_id,
                "emotion_confidence": result.emotion_confidence,
            }
            return {
                "raw": raw_row,
                "output": raw_row,
                "ok": True,
                "latency_ms": latency_ms,
            }
        except Exception as exc:  # pragma: no cover - live diagnostics
            latency_ms = int((time.perf_counter() - started) * 1000)
            raw_row = {
                "index": index,
                "case_id": case_id,
                "variant": variant,
                "ok": False,
                "provider": provider,
                "model": model,
                "latency_ms": latency_ms,
                "error": repr(exc),
            }
            return {
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
    soul = args.soul.read_text(encoding="utf-8-sig")
    expanded_cases: list[dict[str, Any]] = []
    case_expectations: dict[str, list[str]] = {}
    for source_index, case in enumerate(cases, start=1):
        case_id = str(case.get("case_id") or f"case_{source_index:03d}")
        expected_terms = [str(term) for term in list(case.get("memory_expected_terms") or [])]
        case_expectations[case_id] = expected_terms
        for variant, request in _request_variants(
            case,
            soul_md=soul,
            with_memory=bool(args.with_memory),
            with_affect=bool(args.with_affect),
        ):
            expanded_cases.append(
                {
                    "case_id": case_id,
                    "variant": variant,
                    "request": request,
                    "memory_expected_terms": expected_terms,
                }
            )
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
            _run_one(
                index=index,
                case_id=str(item["case_id"]),
                variant=str(item["variant"]),
                request=item["request"],
                memory_expected_terms=list(item["memory_expected_terms"]),
                semaphore=semaphore,
                provider=provider,
                model=model,
                api_key=api_key,
                timeout_ms=int(args.timeout_ms),
                gen_params=gen_params,
                base_url_override=base_url_override,
            )
        )
        for index, item in enumerate(expanded_cases, start=1)
    ]
    _write_json(
        progress_path,
        _summarize(
            cases_count=len(expanded_cases),
            completed=completed,
            results=results,
            started=started,
            provider=provider,
            model=model,
            concurrency=int(args.concurrency),
            gen_params=gen_params,
            out_dir=out_dir,
            with_affect=bool(args.with_affect),
            with_memory=bool(args.with_memory),
            case_expectations=case_expectations,
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
            cases_count=len(expanded_cases),
            completed=completed,
            results=results,
            started=started,
            provider=provider,
            model=model,
            concurrency=int(args.concurrency),
            gen_params=gen_params,
            out_dir=out_dir,
            with_affect=bool(args.with_affect),
            with_memory=bool(args.with_memory),
            case_expectations=case_expectations,
        )
        _write_json(progress_path, summary)
        if completed % int(args.progress_every) == 0 or completed == len(expanded_cases):
            print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)

    final_summary = _summarize(
        cases_count=len(expanded_cases),
        completed=completed,
        results=results,
        started=started,
        provider=provider,
        model=model,
        concurrency=int(args.concurrency),
        gen_params=gen_params,
        out_dir=out_dir,
        with_affect=bool(args.with_affect),
        with_memory=bool(args.with_memory),
        case_expectations=case_expectations,
    )
    _write_json(summary_path, final_summary)
    _write_manual_review_report(
        out_dir / "manual_review_outputs.md",
        [dict(row.get("output") or {}) for row in results if isinstance(row.get("output"), Mapping)],
    )
    return final_summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run live isolated surface generation cases through an LLM provider.")
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
    parser.add_argument("--progress-every", type=int, default=5)
    parser.add_argument("--with-memory", action="store_true", help="Run baseline and with_memory prompt variants from compressed memory_recall_packet fields.")
    parser.add_argument("--with-affect", action="store_true", help="Run variants from compressed affect_control_packet fields or upstream affect_state fields.")
    args = parser.parse_args(argv)
    summary = asyncio.run(run_live(args))
    print("FINAL " + json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
