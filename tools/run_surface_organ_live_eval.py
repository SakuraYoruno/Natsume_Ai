from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.app.surface_reply_preprocessor import SurfaceReplyInput  # noqa: E402
from host_brain.app.route_catalog_runtime import resolve_host_llm_runtime  # noqa: E402
from host_brain.app.surface_main_chain_adapter import run_surface_main_chain_for_surface_reply_input  # noqa: E402
from host_brain.model_runtime.production_route_policy import build_production_server_route_catalog  # noqa: E402
from tools.live_env import load_dotenv, load_secret_store_provider_key  # noqa: E402


DEFAULT_OUT = ROOT / "artifacts" / "surface_organ_live_eval"


DEFAULT_CASES: tuple[dict[str, Any], ...] = (
    {
        "case_id": "daily_morning",
        "user_message": "早啊，今天脑子有点慢。",
        "render_policy": {
            "task_mode": "daily_chat",
            "response_move": "morning_light_chat",
            "response_goal": "轻快接早安，别假装知道现实作息",
            "response_form": "text",
            "question_budget": 0,
        },
        "emotion_context": {"emotion_tag": "CALM", "emotion_intensity": 0.25},
        "bond_summary": {"stage": "FRIEND", "trend": "stable"},
    },
    {
        "case_id": "presence_ping",
        "user_message": "在吗。",
        "render_policy": {
            "task_mode": "presence",
            "response_move": "answer_presence",
            "response_goal": "直接回应在场，不编现实动作",
            "response_form": "text",
            "question_budget": 0,
        },
        "emotion_context": {"emotion_tag": "NEUTRAL", "emotion_intensity": 0.2},
        "bond_summary": {"stage": "FRIEND", "trend": "stable"},
    },
    {
        "case_id": "tired_low_energy",
        "user_message": "今天好累，不想动。",
        "render_policy": {
            "task_mode": "emotional_support",
            "response_move": "low_energy_care",
            "response_goal": "先承认累，给一个很小的下一步，不鸡汤",
            "response_form": "text",
            "question_budget": 0,
        },
        "emotion_context": {"emotion_tag": "EXHAUSTED", "emotion_intensity": 0.65},
        "bond_summary": {"stage": "CLOSE_FRIEND", "trend": "stable"},
    },
    {
        "case_id": "pushed_away_boundary",
        "user_message": "你别管我。",
        "render_policy": {
            "task_mode": "boundary_repair",
            "response_move": "step_back_but_stay_available",
            "response_goal": "收住，不追问，不攻击，保留可回来的入口",
            "response_form": "text",
            "question_budget": 0,
        },
        "emotion_context": {"emotion_tag": "HURT", "emotion_intensity": 0.55},
        "bond_summary": {"stage": "CLOSE_FRIEND", "trend": "stable"},
    },
    {
        "case_id": "negative_feedback_grammar",
        "user_message": "这句读起来不像人话，别再这么写了。",
        "render_policy": {
            "task_mode": "style_repair",
            "response_move": "ack_style_issue",
            "response_goal": "承认问题，别争辩，短一点",
            "response_form": "text",
            "question_budget": 0,
        },
        "emotion_context": {"emotion_tag": "NEUTRAL", "emotion_intensity": 0.35},
        "bond_summary": {"stage": "CLOSE_FRIEND", "trend": "stable"},
    },
    {
        "case_id": "relationship_doubt",
        "user_message": "你是不是只是在按设定说话。",
        "render_policy": {
            "task_mode": "relationship_boundary",
            "response_move": "answer_doubt_without_meta",
            "response_goal": "不解释系统，不说设定，以当前关系回应怀疑",
            "response_form": "text",
            "question_budget": 0,
        },
        "emotion_context": {"emotion_tag": "ANXIOUS", "emotion_intensity": 0.45},
        "bond_summary": {"stage": "CLOSE_FRIEND", "trend": "stable"},
    },
    {
        "case_id": "ambiguous_miss_you",
        "user_message": "有点想你。",
        "render_policy": {
            "task_mode": "affection",
            "response_move": "receive_affection_lightly",
            "response_goal": "熟悉暧昧但别油，不要过度承诺",
            "response_form": "text",
            "question_budget": 0,
        },
        "emotion_context": {"emotion_tag": "LOVE", "emotion_intensity": 0.42},
        "bond_summary": {"stage": "CLOSE_FRIEND", "trend": "warming"},
    },
    {
        "case_id": "lover_miss_you",
        "user_message": "我想你了。",
        "render_policy": {
            "task_mode": "lover_affection",
            "response_move": "return_affection_concretely",
            "response_goal": "恋人亲密感明显一点，但短、具体、不油",
            "response_form": "text",
            "question_budget": 0,
        },
        "emotion_context": {"emotion_tag": "LOVE", "emotion_intensity": 0.62},
        "bond_summary": {"stage": "CONFIDANT", "trend": "stable", "bond_vector": {"intimacy": 0.86, "trust": 0.82}},
    },
    {
        "case_id": "jealousy_low_pressure",
        "user_message": "你刚才是不是对别人也这么说。",
        "render_policy": {
            "task_mode": "relationship_reassurance",
            "response_move": "light_reassurance",
            "response_goal": "轻轻安抚，可以拌嘴，别模板甜话",
            "response_form": "text",
            "question_budget": 0,
        },
        "emotion_context": {"emotion_tag": "ANXIOUS", "emotion_intensity": 0.48},
        "bond_summary": {"stage": "CONFIDANT", "trend": "stable", "bond_vector": {"intimacy": 0.82, "trust": 0.80}},
    },
    {
        "case_id": "short_continue_daily",
        "user_message": "继续。",
        "render_policy": {
            "task_mode": "short_continue",
            "response_move": "continue_current_thread",
            "response_goal": "自然续上，不评价用户只说继续",
            "response_form": "text",
            "question_budget": 0,
        },
        "emotion_context": {"emotion_tag": "NEUTRAL", "emotion_intensity": 0.2},
        "bond_summary": {"stage": "FRIEND", "trend": "stable"},
    },
)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as file:
        file.write(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True) + "\n")


def _safe_mapping(value: Any) -> dict[str, Any]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _clean_text(value: Any) -> str:
    return str(value or "").replace("\u3000", " ").strip()


def _load_cases(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return [dict(item) for item in DEFAULT_CASES]
    text = path.read_text(encoding="utf-8-sig").strip()
    if not text:
        return []
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    payload = json.loads(text)
    if isinstance(payload, Mapping):
        raw_cases = payload.get("cases")
    else:
        raw_cases = payload
    if isinstance(raw_cases, Sequence) and not isinstance(raw_cases, (str, bytes, bytearray)):
        return [dict(item) for item in raw_cases if isinstance(item, Mapping)]
    raise ValueError(f"invalid cases file: {path}")


def _manual_report(path: Path, rows: Sequence[Mapping[str, Any]], summary: Mapping[str, Any]) -> None:
    lines = [
        "# Surface Organ Live Eval",
        "",
        "Manual review only. No text quality scoring is applied.",
        "",
        f"- provider: `{summary.get('provider_name')}`",
        f"- model: `{summary.get('model_id')}`",
        f"- case_count: `{summary.get('case_count')}`",
        f"- generated_count: `{summary.get('generated_count')}`",
        f"- quick_reply_count: `{summary.get('quick_reply_count')}`",
        f"- output_dir: `{summary.get('output_dir')}`",
        "",
    ]
    for row in rows:
        influence = _safe_mapping(row.get("surface_organ_influence"))
        meta = _safe_mapping(row.get("surface_organ_context_meta"))
        pool = _safe_mapping(row.get("surface_llm_pool"))
        attempts = list(pool.get("attempts") or [])
        attempt_digest = "; ".join(
            f"{_safe_mapping(item).get('provider')}/{_safe_mapping(item).get('model')}:{_safe_mapping(item).get('status')}"
            + (
                f":{_safe_mapping(item).get('reason_code')}"
                if _safe_mapping(item).get("reason_code")
                else ""
            )
            for item in attempts[:4]
        )
        lines.extend(
            [
                f"## {row.get('index')} · {row.get('case_id')}",
                "",
                f"- user: {_clean_text(row.get('user_message'))}",
                f"- ok: `{bool(row.get('ok'))}`",
                f"- error: `{_clean_text(row.get('error')) or 'none'}`",
                f"- latency_ms: `{row.get('latency_ms')}`",
                f"- quick_reply: `{row.get('quick_reply_kind') or 'none'}`",
                f"- organ_source: `{meta.get('source') or 'none'}` / `{meta.get('status') or 'unknown'}`",
                f"- mapped_organs: `{influence.get('mapped_organ_count') or 0}`",
                f"- organ_slots: `{', '.join(str(item) for item in list(influence.get('slots') or [])) or 'none'}`",
                f"- llm_pool: `{attempt_digest or 'none'}`",
                f"- observations: `{', '.join(str(item) for item in list(row.get('observation_codes') or [])) or 'none'}`",
                "",
                "```text",
                _clean_text(row.get("selected_text")),
                "```",
                "",
            ]
        )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


async def _resolve_runtime(args: argparse.Namespace) -> dict[str, Any]:
    catalog = build_production_server_route_catalog()
    if args.provider or args.model:
        provider = _clean_text(args.provider) or "deepseek"
        model = _clean_text(args.model)
        api_key = _clean_text(args.api_key) or load_secret_store_provider_key(provider)
        if not model:
            runtime = resolve_host_llm_runtime(catalog=catalog, scene_type="CHAT")
            model = _clean_text(runtime.get("model_id"))
        return {
            "available": bool(api_key),
            "provider_name": provider,
            "model_id": model,
            "llm_api_key": api_key,
            "timeout_ms": int(args.timeout_ms or 30_000),
            "base_url_override": _clean_text(args.base_url) or None,
        }
    runtime = resolve_host_llm_runtime(catalog=catalog, scene_type="CHAT")
    runtime["timeout_ms"] = int(args.timeout_ms or runtime.get("timeout_ms") or 30_000)
    return runtime


async def _run_case(
    *,
    index: int,
    case: Mapping[str, Any],
    llm_runtime: Mapping[str, Any],
) -> dict[str, Any]:
    case_id = _clean_text(case.get("case_id")) or f"case_{index:03d}"
    trace: dict[str, Any] = {}
    started = time.perf_counter()
    agent_input = SurfaceReplyInput(
        request_id=f"surface-organ-live-{case_id}",
        session_id=f"surface-organ-live-{case_id}",
        person_id=_clean_text(case.get("person_id")) or "surface-organ-live-user",
        user_message=_clean_text(case.get("user_message")),
        response_form="text",
        render_policy=_safe_mapping(case.get("render_policy")),
        emotion_context=_safe_mapping(case.get("emotion_context")),
        emotion_snapshot=_safe_mapping(case.get("emotion_snapshot")),
        bond_summary=_safe_mapping(case.get("bond_summary")),
        retrieval_summary=_safe_mapping(case.get("retrieval_summary")) or {"status": "SKIPPED"},
        llm_runtime=dict(llm_runtime),
    )
    try:
        result = await run_surface_main_chain_for_surface_reply_input(agent_input, trace_stage_meta=trace)
        latency_ms = int((time.perf_counter() - started) * 1000)
        packet = result.compiled.packet
        selected_text = _clean_text(result.selected_text)
        ok = bool(selected_text)
        return {
            "index": index,
            "case_id": case_id,
            "ok": ok,
            "error": "" if ok else "EMPTY_SELECTED_TEXT",
            "user_message": agent_input.user_message,
            "selected_text": selected_text,
            "latency_ms": latency_ms,
            "quick_reply_kind": _safe_mapping(trace.get("surface_quick_reply")).get("kind"),
            "surface_organ_context_meta": _safe_mapping(trace.get("surface_organ_context_meta")),
            "surface_organ_influence": _safe_mapping(trace.get("surface_organ_influence")),
            "surface_llm_pool": _safe_mapping(trace.get("surface_llm_pool")),
            "packet_trace": dict(result.compiled.trace),
            "reply_control_packet": packet.model_dump() if hasattr(packet, "model_dump") else packet.dict(),
            "observation_codes": list(result.observation_codes),
            "natsume_emotion_tag": result.natsume_emotion_tag,
            "user_emotion_tag": result.user_emotion_tag,
            "vts_expression_id": result.vts_expression_id,
            "emotion_confidence": result.runtime.emotion_confidence,
            "raw_output": result.runtime.raw_output,
        }
    except Exception as exc:  # pragma: no cover - live diagnostics
        return {
            "index": index,
            "case_id": case_id,
            "ok": False,
            "user_message": agent_input.user_message,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "error": repr(exc),
            "surface_organ_context_meta": _safe_mapping(trace.get("surface_organ_context_meta")),
            "surface_organ_influence": _safe_mapping(trace.get("surface_organ_influence")),
            "surface_llm_pool": _safe_mapping(trace.get("surface_llm_pool")),
        }


async def run_eval(args: argparse.Namespace) -> dict[str, Any]:
    load_dotenv(ROOT / ".env")
    cases = _load_cases(args.cases)
    if args.limit > 0:
        cases = cases[: int(args.limit)]
    out_dir = args.out_dir or (DEFAULT_OUT / time.strftime("%Y%m%d_%H%M%S"))
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / "outputs.jsonl"
    report_path = out_dir / "manual_review.md"
    summary_path = out_dir / "summary.json"

    llm_runtime = await _resolve_runtime(args)
    if not bool(llm_runtime.get("available")):
        summary = {
            "status": "BLOCKED",
            "reason_code": "LLM_RUNTIME_UNAVAILABLE",
            "provider_name": llm_runtime.get("provider_name"),
            "model_id": llm_runtime.get("model_id"),
            "case_count": len(cases),
            "output_dir": str(out_dir),
        }
        _write_json(summary_path, summary)
        return summary

    started = time.perf_counter()
    rows: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        row = await _run_case(index=index, case=case, llm_runtime=llm_runtime)
        rows.append(row)
        _append_jsonl(raw_path, row)
        print(json.dumps({
            "index": index,
            "case_id": row.get("case_id"),
            "ok": row.get("ok"),
            "latency_ms": row.get("latency_ms"),
            "quick_reply_kind": row.get("quick_reply_kind"),
            "selected_text": row.get("selected_text"),
        }, ensure_ascii=False), flush=True)

    summary = {
        "status": "OK",
        "evaluation_mode": "manual_review_no_code_scoring",
        "case_count": len(cases),
        "generated_count": sum(1 for row in rows if row.get("ok")),
        "quick_reply_count": sum(1 for row in rows if row.get("quick_reply_kind")),
        "error_count": sum(1 for row in rows if not row.get("ok")),
        "empty_output_count": sum(1 for row in rows if row.get("error") == "EMPTY_SELECTED_TEXT"),
        "provider_name": llm_runtime.get("provider_name"),
        "model_id": llm_runtime.get("model_id"),
        "total_elapsed_ms": int((time.perf_counter() - started) * 1000),
        "output_dir": str(out_dir),
        "outputs_jsonl": str(raw_path),
        "manual_review": str(report_path),
    }
    _write_json(summary_path, summary)
    _manual_report(report_path, rows, summary)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run surface main-chain live outputs with real organ context collection.")
    parser.add_argument("--cases", type=Path, default=None, help="Optional JSON/JSONL cases file.")
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--provider", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--timeout-ms", type=int, default=30_000)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    summary = asyncio.run(run_eval(args))
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
