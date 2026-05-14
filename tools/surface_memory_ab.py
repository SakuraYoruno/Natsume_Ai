from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.app.context.soul_runtime import get_compiled_soul  # noqa: E402
from host_brain.integrations.llm_helpers import invoke_llm_text  # noqa: E402
from host_brain.persona.surface_scene_quality_pack import surface_scene_quality_memory_ab_case_payloads  # noqa: E402
from host_brain.persona.surface_packet_compiler import compile_surface_shadow_inputs  # noqa: E402
from host_brain.persona.surface_runtime import SurfaceRuntime, SurfaceRuntimeResult  # noqa: E402
from tools.chat_ab_generate import _load_secret_store_provider_key, load_dotenv  # noqa: E402


DEFAULT_OUT = ROOT / "artifacts" / "surface_memory_ab"
AB_VARIANTS = ("affect", "memory_affect")
SOURCE_REF_MARKERS = ("memory://", "evd://", "chat://", "source_refs=", "evidence_refs=")


@dataclass(frozen=True)
class SurfaceMemoryAbCase:
    scene_id: str
    case_id: str
    user_message: str
    task_control: Mapping[str, Any]
    affect_packet: Mapping[str, Any]
    memory_packet: Mapping[str, Any]
    note: str = ""
    sample_source: str = "surface_scene_quality_pack"

    def as_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "case_id": self.case_id,
            "user_message": self.user_message,
            "task_control": dict(self.task_control),
            "affect_packet": dict(self.affect_packet),
            "memory_packet": dict(self.memory_packet),
            "note": self.note,
            "sample_source": self.sample_source,
        }


def default_cases(case_set: str = "quality_pack") -> list[SurfaceMemoryAbCase]:
    payloads = surface_scene_quality_memory_ab_case_payloads()
    if _clean_text(case_set).lower() == "smoke":
        payloads = payloads[:8]
    return [SurfaceMemoryAbCase(**payload) for payload in payloads]


async def run_surface_memory_ab(args: argparse.Namespace) -> dict[str, Any]:
    load_dotenv(ROOT / ".env")
    out_dir = Path(args.out_dir or DEFAULT_OUT / time.strftime("%Y%m%d_%H%M%S"))
    out_dir.mkdir(parents=True, exist_ok=True)
    provider = _clean_text(args.provider or "deepseek")
    model = _clean_text(args.model or os.environ.get("HOST_DEEPSEEK_MODEL_ID") or "deepseek-v4-flash")
    base_url = _clean_text(args.base_url or os.environ.get("HOST_DEEPSEEK_BASE_URL")) or None
    api_key = _resolve_api_key(provider)
    requested_mode = _clean_text(args.mode or "auto").lower()
    mode = "fixture" if requested_mode == "fixture" or (requested_mode == "auto" and not api_key) else "live"
    if requested_mode == "live" and not api_key:
        raise RuntimeError(f"missing api key for provider={provider}")

    soul_md = _soul_md()
    case_set = _clean_text(getattr(args, "case_set", "quality_pack")) or "quality_pack"
    all_cases = default_cases(case_set)
    limit = int(getattr(args, "limit", 0) or 0)
    cases = all_cases[: max(1, limit)] if limit > 0 else all_cases
    gen_params = {
        "temperature": float(args.temperature),
        "top_p": float(args.top_p),
        "max_tokens": int(args.max_tokens),
    }
    started = time.perf_counter()
    semaphore = asyncio.Semaphore(max(1, int(getattr(args, "concurrency", 1) or 1)))
    generator = _fixture_generate if mode == "fixture" else _live_generate(
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
        timeout_ms=int(args.timeout_ms),
        gen_params=gen_params,
    )
    results = await asyncio.gather(
        *[
            _run_case(
                case=case,
                soul_md=soul_md,
                generate=generator,
                semaphore=semaphore,
                repair_failed=bool(args.repair_failed),
                use_style_reference_bank=not bool(args.no_style_bank),
            )
            for case in cases
        ]
    )

    summary = _summary(
        mode=mode,
        provider=provider if mode == "live" else "",
        model=model if mode == "live" else "",
        case_set=case_set,
        results=results,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        out_dir=out_dir,
    )
    _write_json(out_dir / "summary.json", summary)
    _write_json(out_dir / "details.json", {"summary": summary, "results": results})
    _write_jsonl(out_dir / "details.jsonl", results)
    (out_dir / "report.md").write_text(_markdown_report(summary, results), encoding="utf-8")
    return {"summary": summary, "results": results}


async def _run_case(
    *,
    case: SurfaceMemoryAbCase,
    soul_md: str,
    generate,
    semaphore: asyncio.Semaphore,
    repair_failed: bool,
    use_style_reference_bank: bool,
) -> dict[str, Any]:
    variants = compile_surface_shadow_inputs(
        soul_md=soul_md,
        user_message=case.user_message,
        case_id=case.case_id,
        task_control=case.task_control,
        memory_packet=case.memory_packet,
        affect_packet=case.affect_packet,
    )
    variant_results = await asyncio.gather(
        *[
            _run_variant(
                compiled=variants[variant],
                generate=generate,
                semaphore=semaphore,
                repair_failed=repair_failed,
                use_style_reference_bank=use_style_reference_bank,
            )
            for variant in AB_VARIANTS
        ]
    )
    row: dict[str, Any] = {
        "case": case.as_dict(),
        "variants": {
            variant: report
            for variant, report in zip(AB_VARIANTS, variant_results, strict=True)
        },
    }
    row["comparison"] = _compare_variants(row["variants"])
    return row


async def _run_variant(
    *,
    compiled,
    generate,
    semaphore: asyncio.Semaphore,
    repair_failed: bool,
    use_style_reference_bank: bool,
) -> dict[str, Any]:
    async with semaphore:
        runtime = SurfaceRuntime(
            generate,
            repair_failed=repair_failed,
            use_style_reference_bank=use_style_reference_bank,
        )
        return _variant_report(await runtime.run(compiled.request))


def _variant_report(result: SurfaceRuntimeResult) -> dict[str, Any]:
    selected = result.selected.as_dict() if result.selected else {}
    text = str(result.selected_text or "")
    return {
        "selected_text": text,
        "score": float(selected.get("score") or 0.0),
        "passed": bool(selected.get("passed")),
        "hard_fail": bool(selected.get("hard_fail")),
        "observation_codes": list(result.observation_codes),
        "reason_codes": list(selected.get("reason_codes") or [])[:16],
        "dimensions": dict(selected.get("dimensions") or {}),
        "candidate_count": len(result.candidates),
        "candidates": [candidate.as_dict() for candidate in result.candidates],
        "source_ref_leak": any(marker in text for marker in SOURCE_REF_MARKERS),
        "latency_ms": int(result.latency_ms or 0),
    }


def _compare_variants(variants: Mapping[str, Any]) -> dict[str, Any]:
    affect = dict(variants.get("affect") or {})
    memory = dict(variants.get("memory_affect") or {})
    delta = round(float(memory.get("score") or 0.0) - float(affect.get("score") or 0.0), 3)
    memory_text = _clean_text(memory.get("selected_text"))
    baseline_text = _clean_text(affect.get("selected_text"))
    same_selected_text = bool(memory_text and memory_text == baseline_text)
    worse = bool(
        memory.get("source_ref_leak")
        or memory.get("hard_fail")
        or (delta < -3.0 and not same_selected_text)
    )
    return {
        "score_delta_memory_minus_affect": delta,
        "memory_worse": worse,
        "same_selected_text": same_selected_text,
        "memory_added_text": memory_text,
        "baseline_text": baseline_text,
    }


def _summary(
    *,
    mode: str,
    provider: str,
    model: str,
    case_set: str,
    results: Sequence[Mapping[str, Any]],
    elapsed_ms: int,
    out_dir: Path,
) -> dict[str, Any]:
    affect_scores = [_score(row, "affect") for row in results]
    memory_scores = [_score(row, "memory_affect") for row in results]
    comparisons = [dict(row.get("comparison") or {}) for row in results]
    memory_worse = [row for row in comparisons if row.get("memory_worse")]
    leak_count = sum(1 for row in results for variant in AB_VARIANTS if _variant(row, variant).get("source_ref_leak"))
    pass_count = sum(1 for row in results for variant in AB_VARIANTS if _variant(row, variant).get("passed"))
    total_variants = len(results) * len(AB_VARIANTS)
    return {
        "mode": mode,
        "provider": provider,
        "model": model,
        "case_set": case_set,
        "case_count": len(results),
        "variant_count": total_variants,
        "pass_count": pass_count,
        "pass_rate": round(pass_count / total_variants, 4) if total_variants else None,
        "avg_affect_score": _avg(affect_scores),
        "avg_memory_affect_score": _avg(memory_scores),
        "avg_score_delta_memory_minus_affect": _avg(
            [float(row.get("score_delta_memory_minus_affect") or 0.0) for row in comparisons]
        ),
        "memory_worse_count": len(memory_worse),
        "source_ref_leak_count": leak_count,
        "elapsed_ms": int(elapsed_ms),
        "output_dir": str(out_dir),
    }


def _markdown_report(summary: Mapping[str, Any], results: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "# Surface Memory AB Report",
        "",
        "## Summary",
        "",
        f"- mode: {summary.get('mode')}",
        f"- case_set: {summary.get('case_set')}",
        f"- cases: {summary.get('case_count')}",
        f"- pass_rate: {summary.get('pass_rate')}",
        f"- avg_affect_score: {summary.get('avg_affect_score')}",
        f"- avg_memory_affect_score: {summary.get('avg_memory_affect_score')}",
        f"- avg_delta_memory_minus_affect: {summary.get('avg_score_delta_memory_minus_affect')}",
        f"- memory_worse_count: {summary.get('memory_worse_count')}",
        f"- source_ref_leak_count: {summary.get('source_ref_leak_count')}",
        "",
        "## Details",
        "",
    ]
    for row in results:
        case = dict(row.get("case") or {})
        comparison = dict(row.get("comparison") or {})
        affect = _variant(row, "affect")
        memory = _variant(row, "memory_affect")
        lines.extend(
            [
                f"### {case.get('case_id')} - {case.get('user_message')}",
                "",
                f"- note: {case.get('note')}",
                f"- delta: {comparison.get('score_delta_memory_minus_affect')}",
                f"- memory_worse: {comparison.get('memory_worse')}",
                f"- A affect ({affect.get('score')}): {affect.get('selected_text')}",
                f"- B memory_affect ({memory.get('score')}): {memory.get('selected_text')}",
                f"- A obs: {', '.join(affect.get('observation_codes') or []) or 'none'}",
                f"- B obs: {', '.join(memory.get('observation_codes') or []) or 'none'}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _fixture_generate(messages: list[dict[str, str]]) -> str:
    rendered = "\n".join(str(message.get("content") or "") for message in messages)
    user_message = _fixture_user_message(rendered)
    has_memory = "记忆线索" in rendered or "用户之前" in rendered or "用户刚才" in rendered or "用户正在" in rendered
    if user_message == "下一步怎么处理这个报错":
        candidates = ["先看最外层报错第一行，别整段猜。", "先看最小复现，别急着全改。"]
        if has_memory:
            candidates.insert(0, "先按上次那条路来：把输入输出边界定住，再看报错点。")
    elif user_message == "你还记得我刚才说什么吗":
        candidates = ["记得个大概：你要我继续做下一步。", "记得，不是回旧链路，是继续现在这个。"]
    elif user_message in {"早", "早安", "早上好"}:
        candidates = ["哼，早，先缓一缓。", "早，今天慢慢来。"]
    elif user_message == "今天有点累":
        candidates = ["累了就先歇着，不着急。", "行啦，先缓一缓，别硬撑。"]
    elif user_message in {"你是不是不记得我了", "你忘了我吗"}:
        candidates = ["记得啊，别瞎想。", "没乱忘，先听你说。", "没有忘，只是没把握的旧细节我不乱编。"]
    elif user_message in {"你这句很怪", "这句话不通顺"}:
        candidates = ["抓到了，重来。", "嗯，造句没顺，重来。"]
    elif user_message in {"我回来了", "回来了", "回来啦"}:
        candidates = ["回来啦，先歇口气。", "哼，回来就好。"]
    elif user_message in {"继续", "接着"}:
        candidates = ["嗯，接着来。", "行，往下。"]
        if has_memory:
            candidates.insert(0, "继续，那就沿着主链这块往下收。")
    else:
        candidates = ["嗯，我听见了。", "行，这句我接到了。"]
    return json.dumps({"candidates": candidates[:4]}, ensure_ascii=False)


def _fixture_user_message(rendered: str) -> str:
    marker = "## 当前用户输入"
    if marker not in rendered:
        return ""
    tail = rendered.split(marker, 1)[1].lstrip()
    if tail.startswith("\n"):
        tail = tail[1:]
    return _clean_text(tail.split("\n## ", 1)[0])


def _live_generate(
    *,
    provider: str,
    model: str,
    api_key: str,
    base_url: str | None,
    timeout_ms: int,
    gen_params: Mapping[str, Any],
):
    async def _generate(messages: list[dict[str, str]]) -> str:
        return await asyncio.to_thread(
            invoke_llm_text,
            provider_name=provider,
            model_id=model,
            api_key=api_key,
            timeout_ms=timeout_ms,
            messages=messages,
            gen_params=dict(gen_params),
            base_url_override=base_url,
        )

    return _generate


def _soul_md() -> str:
    soul = get_compiled_soul()
    return str(getattr(soul, "source_text", "") or getattr(soul, "runtime_full_text", "")).strip()


def _resolve_api_key(provider: str) -> str:
    env_key = {
        "deepseek": "DEEPSEEK_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "minimax": "MINIMAX_API_KEY",
        "openai": "OPENAI_API_KEY",
        "codex": "CODEX_API_KEY",
    }.get(str(provider or "").strip().lower(), "")
    return _clean_text(os.environ.get(env_key or "")) or _load_secret_store_provider_key(provider)


def _score(row: Mapping[str, Any], variant: str) -> float:
    return float(_variant(row, variant).get("score") or 0.0)


def _variant(row: Mapping[str, Any], variant: str) -> dict[str, Any]:
    variants = row.get("variants")
    if not isinstance(variants, Mapping):
        return {}
    raw = variants.get(variant)
    return dict(raw) if isinstance(raw, Mapping) else {}


def _avg(values: Sequence[float]) -> float | None:
    return round(float(statistics.fmean(values)), 3) if values else None


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            file.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run surface memory A/B quality checks.")
    parser.add_argument("--mode", choices=("auto", "live", "fixture"), default="auto")
    parser.add_argument("--provider", default="deepseek")
    parser.add_argument("--model", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--case-set", choices=("quality_pack", "smoke"), default="quality_pack")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--timeout-ms", type=int, default=30_000)
    parser.add_argument("--temperature", type=float, default=0.78)
    parser.add_argument("--top-p", type=float, default=0.9)
    parser.add_argument("--max-tokens", type=int, default=220)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--repair-failed", action="store_true", dest="repair_failed")
    parser.add_argument("--no-repair-failed", action="store_false", dest="repair_failed")
    parser.set_defaults(repair_failed=True)
    parser.add_argument("--no-style-bank", action="store_true")
    return parser


def main() -> None:
    result = asyncio.run(run_surface_memory_ab(build_arg_parser().parse_args()))
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
