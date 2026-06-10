from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import os
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.app.context.soul_runtime import get_compiled_soul  # noqa: E402
from host_brain.integrations.llm_helpers import invoke_llm_text  # noqa: E402
from host_brain.persona.isolated_surface_harness import (  # noqa: E402
    IsolatedSurfaceInput,
    build_surface_original_quote_query,
    merged_must_not_do,
    parse_surface_candidates,
    surface_prompt_facts,
)
from host_brain.persona.surface_original_quote_bank import (  # noqa: E402
    DEFAULT_SURFACE_ORIGINAL_QUOTE_COLLECTION,
    select_surface_original_quotes,
    surface_original_quote_policy_for_scene,
    to_prompt_style_text,
)
from host_brain.persona.surface_packet_compiler import compile_surface_shadow_inputs  # noqa: E402
from tools.live_env import load_dotenv, load_secret_store_provider_key  # noqa: E402
from tools.surface_memory_ab import AB_VARIANTS, SurfaceMemoryAbCase, default_cases  # noqa: E402


DEFAULT_OUT = ROOT / "artifacts" / "surface_original_quote_direct_trial"
DEFAULT_REVIEW_TEMPLATE = (
    ROOT
    / "artifacts"
    / "surface_scene_quality"
    / "original_quote_review"
    / "latest"
    / "surface_scene_original_quote_review_template.json"
)
SOURCE_REF_MARKERS = ("memory://", "evd://", "chat://", "source_refs=", "evidence_refs=")


@dataclass(frozen=True)
class SceneQuoteExamples:
    scene_id: str
    examples: tuple[str, ...]
    policy: str = "careful"
    policy_note: str = ""
    source: str = ""
    degraded: bool = False
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "examples": list(self.examples),
            "policy": self.policy,
            "policy_note": self.policy_note,
            "source": self.source,
            "degraded": self.degraded,
            "reason": self.reason,
        }


def load_scene_quote_examples(
    path: str | os.PathLike[str] | None = None,
    *,
    limit: int = 8,
) -> dict[str, SceneQuoteExamples]:
    template_path = Path(path or DEFAULT_REVIEW_TEMPLATE)
    if not template_path.exists():
        return {}
    payload = json.loads(template_path.read_text(encoding="utf-8-sig"))
    raw_scenes = payload.get("scenes") if isinstance(payload, Mapping) else None
    if not isinstance(raw_scenes, list):
        return {}
    examples_by_scene: dict[str, SceneQuoteExamples] = {}
    for raw_scene in raw_scenes:
        if not isinstance(raw_scene, Mapping):
            continue
        scene_id = _clean_text(raw_scene.get("scene_id"))
        if not scene_id:
            continue
        policy = surface_original_quote_policy_for_scene(scene_id)
        candidates = raw_scene.get("candidate_good_examples")
        effective_limit = min(max(0, int(limit or 0)), max(0, int(policy.max_quotes or 0)))
        examples = (
            _candidate_texts(candidates if isinstance(candidates, list) else (), limit=effective_limit)
            if policy.enabled
            else ()
        )
        examples_by_scene[scene_id] = SceneQuoteExamples(
            scene_id=scene_id,
            examples=examples,
            policy=policy.mode,
            policy_note=policy.note,
            source=str(template_path),
        )
    return examples_by_scene


async def run_surface_original_quote_direct_trial(args: argparse.Namespace) -> dict[str, Any]:
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

    all_cases = default_cases(_clean_text(args.case_set) or "quality_pack")
    limit = int(getattr(args, "limit", 0) or 0)
    cases = all_cases[: max(1, limit)] if limit > 0 else all_cases
    variants = _variants(args.variant)
    quote_source = _quote_source(args)
    examples_by_scene = (
        load_scene_quote_examples(args.review_template, limit=int(args.quote_limit)) if quote_source == "template" else {}
    )
    soul_md = _soul_md()
    gen_params = {
        "temperature": float(args.temperature),
        "top_p": float(args.top_p),
        "max_tokens": int(args.max_tokens),
    }
    generator = _fixture_generate if mode == "fixture" else _live_generate(
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
        timeout_ms=int(args.timeout_ms),
        gen_params=gen_params,
    )
    semaphore = asyncio.Semaphore(max(1, int(getattr(args, "concurrency", 1) or 1)))
    started = time.perf_counter()
    results = await asyncio.gather(
        *[
            _run_case(
                case=case,
                variants=variants,
                soul_md=soul_md,
                examples_by_scene=examples_by_scene,
                generate=generator,
                semaphore=semaphore,
                quote_source=quote_source,
                quote_limit=int(args.quote_limit),
            )
            for case in cases
        ]
    )
    summary = _summary(
        mode=mode,
        provider=provider if mode == "live" else "",
        model=model if mode == "live" else "",
        case_set=_clean_text(args.case_set) or "quality_pack",
        review_template=str(Path(args.review_template or DEFAULT_REVIEW_TEMPLATE)),
        quote_source=quote_source,
        quote_limit=int(args.quote_limit),
        variants=variants,
        results=results,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        out_dir=out_dir,
    )
    _write_json(out_dir / "summary.json", summary)
    _write_json(out_dir / "details.json", {"summary": summary, "results": results})
    _write_jsonl(out_dir / "details.jsonl", results)
    (out_dir / "report.md").write_text(_markdown_report(summary, results), encoding="utf-8")
    (out_dir / "selected_outputs.md").write_text(_selected_outputs(summary, results), encoding="utf-8")
    return {"summary": summary, "results": results}


async def _run_case(
    *,
    case: SurfaceMemoryAbCase,
    variants: Sequence[str],
    soul_md: str,
    examples_by_scene: Mapping[str, SceneQuoteExamples],
    generate,
    semaphore: asyncio.Semaphore,
    quote_source: str,
    quote_limit: int,
) -> dict[str, Any]:
    compiled_variants = compile_surface_shadow_inputs(
        soul_md=soul_md,
        user_message=case.user_message,
        case_id=case.case_id,
        task_control=case.task_control,
        memory_packet=case.memory_packet,
        affect_packet=case.affect_packet,
    )
    scene_examples = examples_by_scene.get(case.scene_id)
    if scene_examples is None:
        policy = surface_original_quote_policy_for_scene(case.scene_id)
        scene_examples = SceneQuoteExamples(
            scene_id=case.scene_id,
            examples=(),
            policy=policy.mode,
            policy_note=policy.note,
        )
    variant_results = await asyncio.gather(
        *[
            _run_variant(
                request=compiled_variants[variant].request,
                scene_id=case.scene_id,
                template_quote_examples=scene_examples,
                quote_source=quote_source,
                quote_limit=quote_limit,
                generate=generate,
                semaphore=semaphore,
            )
            for variant in variants
        ]
    )
    return {
        "case": case.as_dict(),
        "quote_examples": dict(variant_results[0].get("quote_examples") or scene_examples.as_dict()),
        "quote_examples_by_variant": {
            variant: dict(report.get("quote_examples") or {})
            for variant, report in zip(variants, variant_results, strict=True)
        },
        "variants": {
            variant: report
            for variant, report in zip(variants, variant_results, strict=True)
        },
    }


async def _run_variant(
    *,
    request: IsolatedSurfaceInput,
    scene_id: str,
    template_quote_examples: SceneQuoteExamples,
    quote_source: str,
    quote_limit: int,
    generate,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    started = time.perf_counter()
    scene_quote_examples = quote_examples_for_request(
        request,
        scene_id=scene_id,
        source=quote_source,
        template_examples=template_quote_examples,
        limit=quote_limit,
    )
    messages = build_original_quote_direct_messages(
        request,
        scene_id=scene_id,
        quote_examples=scene_quote_examples.examples,
    )
    async with semaphore:
        raw_output = await _maybe_await_text(generate(messages))
    candidate_texts = parse_surface_candidates(raw_output)
    text = _clean_text(candidate_texts[0] if candidate_texts else raw_output)
    copy_trace = _prompt_quote_copy_trace(text, scene_quote_examples.examples)
    return {
        "selected_text": text,
        "raw_output": raw_output,
        "candidate_count": len(candidate_texts),
        "candidate_texts": candidate_texts[:1],
        "quote_count": len(scene_quote_examples.examples),
        "quote_examples": scene_quote_examples.as_dict(),
        "prompt_quote_copy": copy_trace,
        "source_ref_leak": any(marker in text for marker in SOURCE_REF_MARKERS),
        "latency_ms": int((time.perf_counter() - started) * 1000),
    }


def build_original_quote_direct_messages(
    request: IsolatedSurfaceInput,
    *,
    scene_id: str,
    quote_examples: Sequence[str],
) -> list[dict[str, str]]:
    control_payload = _model_payload(request.control)
    control_payload["facts"] = surface_prompt_facts(request)
    control_payload["must_not_do"] = merged_must_not_do(request)
    no_question_line = (
        "本轮 question_budget=0：不要写真正问句或追问；不要用“下一步呢/说句话呀/是不是/吧？”推动用户，直接接住当前话。"
        if int(getattr(request.control, "question_budget", 0) or 0) <= 0
        else f"本轮最多 {int(request.control.question_budget)} 个问句；问句只能用于完成当前动作。"
    )
    system_prompt = "\n".join(
        (
            "你是隔离的最终表层文本生成器，只负责产出一条可发送的中文私聊回复。",
            "本轮是原台词直喂实验：唯一允许读取的输入是 Soul.md、当前用户输入、ReplyControlPacket、必要事实、must_not_do 和对应场景的真实中文原台词例句。",
            "不要读取或推断原始历史、长期记忆、器官族输出、工具候选、RAG 文档或模块自然语言建议。",
            "原台词例句可以直接作为句法和节奏样本；可以贴近它们的称呼、转折、停顿、句尾和亲近感。",
            "如果当前语境完全贴合，允许直接使用例句里的短句结构或轻微改写；但不要搬原作地点、剧情、线下动作或未提供事实。",
            "事实准确性、must_not_do、用户边界和任务 usefulness 高于角色味。",
            "你是中文私聊里的数字生命，不能编现实动作或现场状态；不要说我去倒水、坐下、过来、抱一下、在你旁边、看见你、等类似现实行动，除非必要事实明确给出。",
            "也不要安排用户身体动作或现实作息：坐下、闭眼、躺下、喝水、早点睡、明天再说都只能在必要事实或用户明确请求时使用。",
            "用户追问记不记得时：事实或记忆包里有内容就直接接；没有就说不乱猜、让对方给线索。不要承认走神、忘了、脑袋空白，也不要编刚才发生过的事。",
            "用户问“你是不是不记得我/你忘了我吗”是在要关系安定，不是在要事实校验；直接说没忘、收住不安，不要反复追问“指哪段/给提示”。",
            "不要猜时间、作息、忙闲、进度或现实状态；禁用“熬夜、没睡醒、准时、挺早、忙文本、加班、摸鱼、做完、偷偷、又、主动来找我、回得干脆、回来得晚、忙到半夜、被工作吃掉、难得”等未提供事实推断。",
            "早安、回来、在吗这类低信息场景，只接当前消息本身，不评价对方来得早晚、是否主动、是否老实、是否刚睡醒。",
            "低能量/难受场景不要编喝水、坐下、躺下、睡觉、明天再说这类现实安排；可以说先缓一下、别硬撑，但要短、克制、像聊天。",
            "被用户推开时先收住距离，允许嘴硬一句，但不要反压成“偏不管/谁管你答不答应”。",
            "不要输出 emoji、颜文字、表情符号或日语口癖。",
            no_question_line,
            "只输出最终回复文本；不要 JSON，不要候选列表，不要解释，不要字段名。",
            "中文回复不要用空格断句；需要停顿就用逗号、句号或省略号。",
            "",
            "## Soul.md",
            request.soul_md.strip(),
        )
    )
    user_prompt = "\n".join(
        (
            "## 当前用户输入",
            request.user_message,
            "",
            "## ReplyControlPacket",
            json.dumps(control_payload, ensure_ascii=False, indent=2, sort_keys=True),
            "",
            "## 必要事实",
            "\n".join(f"- {item}" for item in surface_prompt_facts(request)) or "- 无",
            "",
            _quote_guidance(scene_id, quote_examples),
            "",
            "## must_not_do",
            "\n".join(f"- {item}" for item in merged_must_not_do(request)) or "- 无",
            "",
            "## 输出要求",
            "只输出一条最终回复文本。不要 JSON，不要解释，不要说你参考了原台词。",
        )
    )
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]


def _quote_guidance(scene_id: str, quote_examples: Sequence[str]) -> str:
    lines = [
        "## 原台词直喂样例",
        f"scene_id: {scene_id}",
        f"quote_policy: {surface_original_quote_policy_for_scene(scene_id).mode}",
        "这些是当前场景对应的真实中文台词候选，不混入旧人工样本；优先学它们的发话方式。",
    ]
    if not quote_examples:
        policy = surface_original_quote_policy_for_scene(scene_id)
        if not policy.enabled:
            lines.append(f"- 本场景原台词策略为 {policy.mode}：{policy.note}")
        else:
            lines.append("- 本场景没有可用原台词候选；只能按 Soul.md 和控制包直出。")
        return "\n".join(lines)
    lines.extend(f"- 「{item}」" for item in quote_examples)
    return "\n".join(lines)


def _candidate_texts(candidates: Iterable[Any], *, limit: int) -> tuple[str, ...]:
    values: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if isinstance(candidate, Mapping):
            text = to_prompt_style_text(candidate.get("text") or candidate.get("prompt_text") or "")
        else:
            text = to_prompt_style_text(str(candidate or ""))
        key = _copy_norm(text)
        if not text or key in seen:
            continue
        seen.add(key)
        values.append(text)
        if len(values) >= max(0, int(limit or 0)):
            break
    return tuple(values)


def quote_examples_for_request(
    request: IsolatedSurfaceInput,
    *,
    scene_id: str,
    source: str,
    template_examples: SceneQuoteExamples,
    limit: int,
) -> SceneQuoteExamples:
    if _clean_text(source).lower() == "template":
        return template_examples
    policy = surface_original_quote_policy_for_scene(scene_id)
    query = build_surface_original_quote_query(request, limit=max(1, int(limit or 1)))
    query = replace(
        query,
        scene_id=scene_id,
        limit=max(1, int(limit or query.limit or 1)),
    )
    selection = select_surface_original_quotes(query, limit=max(1, int(limit or query.limit or 1)))
    examples = tuple(
        text
        for text in (_clean_text(to_prompt_style_text(quote.prompt_text or quote.text)) for quote in selection.quotes)
        if text
    )
    return SceneQuoteExamples(
        scene_id=scene_id,
        examples=examples,
        policy=policy.mode,
        policy_note=policy.note,
        source=DEFAULT_SURFACE_ORIGINAL_QUOTE_COLLECTION,
        degraded=bool(selection.degraded),
        reason=selection.reason,
    )


def _prompt_quote_copy_trace(text: str, quote_examples: Sequence[str]) -> dict[str, Any]:
    normalized = _copy_norm(text)
    best_ratio = 0.0
    best_quote = ""
    exact = False
    for quote in quote_examples:
        quote_norm = _copy_norm(quote)
        if not normalized or not quote_norm:
            continue
        ratio = difflib.SequenceMatcher(None, normalized, quote_norm).ratio()
        contained = min(len(normalized), len(quote_norm)) >= 8 and (
            normalized in quote_norm or quote_norm in normalized
        )
        if ratio > best_ratio:
            best_ratio = ratio
            best_quote = quote
        if normalized == quote_norm or contained:
            exact = True
    return {
        "exact_or_contained": bool(exact),
        "near_ratio": round(float(best_ratio), 3),
        "near_copy": bool(best_ratio >= 0.82),
        "best_quote": best_quote,
    }


def _summary(
    *,
    mode: str,
    provider: str,
    model: str,
    case_set: str,
    review_template: str,
    quote_source: str,
    quote_limit: int,
    variants: Sequence[str],
    results: Sequence[Mapping[str, Any]],
    elapsed_ms: int,
    out_dir: Path,
) -> dict[str, Any]:
    variant_reports = [
        dict(row.get("variants", {}).get(variant) or {})
        for row in results
        for variant in variants
    ]
    quote_counts = [int(dict(row.get("quote_examples") or {}).get("examples") is not None and len(dict(row.get("quote_examples") or {}).get("examples") or [])) for row in results]
    return {
        "mode": mode,
        "provider": provider,
        "model": model,
        "case_set": case_set,
        "review_template": review_template,
        "quote_source": quote_source,
        "quote_limit": int(quote_limit),
        "variants": list(variants),
        "case_count": len(results),
        "variant_count": len(variant_reports),
        "output_count": sum(1 for report in variant_reports if str(report.get("selected_text") or "").strip()),
        "empty_output_count": sum(1 for report in variant_reports if not str(report.get("selected_text") or "").strip()),
        "source_ref_leak_count": sum(1 for report in variant_reports if report.get("source_ref_leak")),
        "prompt_quote_exact_or_contained_count": sum(
            1 for report in variant_reports if dict(report.get("prompt_quote_copy") or {}).get("exact_or_contained")
        ),
        "prompt_quote_near_copy_count": sum(
            1 for report in variant_reports if dict(report.get("prompt_quote_copy") or {}).get("near_copy")
        ),
        "cases_with_zero_quotes": sum(1 for count in quote_counts if count == 0),
        "elapsed_ms": int(elapsed_ms),
        "output_dir": str(out_dir),
        "evaluation_mode": "manual_review_no_code_scoring",
    }


def _markdown_report(summary: Mapping[str, Any], results: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "# Surface Original Quote Direct Trial",
        "",
        "## Summary",
        "",
    ]
    for key, value in summary.items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Details", ""])
    for row in results:
        case = dict(row.get("case") or {})
        case_quote_payload = dict(row.get("quote_examples") or {})
        quotes = list(case_quote_payload.get("examples") or [])
        lines.extend(
            [
                f"### {case.get('case_id')} - {case.get('user_message')}",
                "",
                f"- scene_id: {case.get('scene_id')}",
                f"- quote_source: {case_quote_payload.get('source') or summary.get('quote_source')}",
                f"- quote_policy: {case_quote_payload.get('policy')}",
                f"- quote_reason: {case_quote_payload.get('reason') or 'none'}",
                f"- quote_count: {len(quotes)}",
                "- quote_examples:",
                *[f"  - {item}" for item in quotes[:8]],
            ]
        )
        for variant, report in dict(row.get("variants") or {}).items():
            copy_trace = dict(report.get("prompt_quote_copy") or {})
            variant_quotes = dict(report.get("quote_examples") or {})
            lines.extend(
                [
                    f"- {variant}: {report.get('selected_text')}",
                    f"  - quote_count: {report.get('quote_count')} / source={variant_quotes.get('source') or summary.get('quote_source')} / reason={variant_quotes.get('reason') or 'none'}",
                    f"  - prompt_quote_near_ratio: {copy_trace.get('near_ratio')} / near_copy={copy_trace.get('near_copy')}",
                    f"  - source_ref_leak: {report.get('source_ref_leak')}",
                ]
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _selected_outputs(summary: Mapping[str, Any], results: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "# Selected Outputs",
        "",
        "```json",
        json.dumps(dict(summary), ensure_ascii=False, indent=2, sort_keys=True),
        "```",
        "",
    ]
    for row in results:
        case = dict(row.get("case") or {})
        lines.extend([f"## {case.get('case_id')} | {case.get('user_message')}", ""])
        for variant, report in dict(row.get("variants") or {}).items():
            quote_payload = dict(report.get("quote_examples") or {})
            quotes = list(quote_payload.get("examples") or [])
            lines.extend(
                [
                    f"### {variant}",
                    "",
                    f"原台词样例 source={quote_payload.get('source') or summary.get('quote_source')} policy={quote_payload.get('policy')} reason={quote_payload.get('reason') or 'none'}：",
                    *([f"- {item}" for item in quotes[:8]] if quotes else ["- 无"]),
                    "",
                    str(report.get("selected_text") or "").strip(),
                    "",
                    f"copy={dict(report.get('prompt_quote_copy') or {}).get('near_ratio')} source_ref_leak={report.get('source_ref_leak')}",
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def _fixture_generate(messages: list[dict[str, str]]) -> str:
    rendered = "\n".join(message.get("content") or "" for message in messages)
    if "别管我" in rendered:
        return "哈？谁稀罕管你啊。\n……行，那你自己待会儿。\n难受了就叫我，听见没。"
    if "继续" in rendered:
        return "等一下啦，接着来。"
    if "想你" in rendered:
        return "哼……想我就直说嘛。"
    return "什么嘛，先这样说。"


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


async def _maybe_await_text(value: str | Any) -> str:
    if hasattr(value, "__await__"):
        value = await value
    return str(value or "").strip()


def _variants(raw: str) -> tuple[str, ...]:
    value = _clean_text(raw or "all").lower()
    if value == "all":
        return tuple(AB_VARIANTS)
    if value not in AB_VARIANTS:
        raise ValueError(f"unknown variant={raw}; expected all/{'/'.join(AB_VARIANTS)}")
    return (value,)


def _model_payload(model: Any) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return dict(model.model_dump())
    if hasattr(model, "dict"):
        return dict(model.dict())
    return dict(model)


def _soul_md() -> str:
    soul = get_compiled_soul()
    return str(getattr(soul, "source_text", "") or getattr(soul, "runtime_full_text", "")).strip()


def _resolve_api_key(provider: str) -> str:
    env_key = {
        "deepseek": "DEEPSEEK_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "minimax": "MINIMAX_API_KEY",
        "openai": "OPENAI_API_KEY",    }.get(str(provider or "").strip().lower(), "")
    return _clean_text(os.environ.get(env_key or "")) or load_secret_store_provider_key(provider)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            file.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def _copy_norm(text: str) -> str:
    value = str(text or "").strip().lower()
    return "".join(ch for ch in value if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _quote_source(args: argparse.Namespace) -> str:
    source = _clean_text(getattr(args, "quote_source", "")).lower()
    if not source:
        source = "template"
    if source not in {"collection", "template"}:
        raise ValueError("quote_source must be collection or template")
    return source


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a clean direct trial with only scene-matched original quote examples.")
    parser.add_argument("--mode", choices=("auto", "live", "fixture"), default="auto")
    parser.add_argument("--provider", default="deepseek")
    parser.add_argument("--model", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--case-set", choices=("quality_pack", "smoke"), default="quality_pack")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--variant", choices=("all", *AB_VARIANTS), default="all")
    parser.add_argument("--quote-source", choices=("collection", "template"), default="collection")
    parser.add_argument("--review-template", default=str(DEFAULT_REVIEW_TEMPLATE))
    parser.add_argument("--quote-limit", type=int, default=8)
    parser.add_argument("--timeout-ms", type=int, default=30_000)
    parser.add_argument("--temperature", type=float, default=0.35)
    parser.add_argument("--top-p", type=float, default=0.8)
    parser.add_argument("--max-tokens", type=int, default=220)
    parser.add_argument("--concurrency", type=int, default=4)
    return parser


def main() -> None:
    result = asyncio.run(run_surface_original_quote_direct_trial(build_arg_parser().parse_args()))
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
