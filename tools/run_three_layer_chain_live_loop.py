from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.integrations.llm_helpers import invoke_llm_text  # noqa: E402
from host_brain.persona.isolated_surface_continuity import (  # noqa: E402
    ThreeLayerContinuityContext,
    build_surface_input_from_three_layer_context,
)
from host_brain.persona.isolated_surface_harness import IsolatedSurfaceInput  # noqa: E402
from host_brain.persona.surface_runtime import SurfaceRuntime  # noqa: E402
from host_brain.persona.surface_affect_control import (  # noqa: E402
    affect_control_packet_from_mapping,
    build_surface_affect_control_packet,
)
from tools.build_isolated_surface_continuity_cases import build_continuity_long_cases  # noqa: E402
from tools.live_env import load_dotenv, load_secret_store_provider_key  # noqa: E402
from tools.isolated_surface_harness import _read_json_or_jsonl  # noqa: E402


DEFAULT_OUT = ROOT / "artifacts" / "isolated_surface_harness" / "three_layer_chain_36_closed_loop_20260512"


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as file:
        file.write(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True) + "\n")


def _model_dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return dict(value.model_dump())  # type: ignore[attr-defined]
    if hasattr(value, "dict"):
        return dict(value.dict())  # pragma: no cover - pydantic v1
    return dict(value)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _clean_list(values: Sequence[Any] | None) -> tuple[str, ...]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in list(values or []):
        text = _clean_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return tuple(cleaned)


def _first_mapping(payload: Mapping[str, Any], keys: Sequence[str]) -> Mapping[str, Any] | None:
    for key in keys:
        raw = payload.get(key)
        if isinstance(raw, Mapping):
            return raw
    return None


def _first_sequence(payload: Mapping[str, Any], keys: Sequence[str]) -> tuple[str, ...]:
    for key in keys:
        raw = payload.get(key)
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
            return _clean_list(raw)
    return ()


def _turn_affect_packet(turn: Mapping[str, Any], *, task_mode: str):
    explicit = _first_mapping(turn, ("affect_control_packet", "surface_affect_packet", "emotion_control_packet"))
    if explicit is not None:
        return affect_control_packet_from_mapping(explicit)
    raw_affect_state = _first_mapping(turn, ("affect_state", "emotion_state", "emotion_context"))
    if raw_affect_state is None:
        return None
    return build_surface_affect_control_packet(
        emotion_state=raw_affect_state,
        bond_summary=_first_mapping(turn, ("bond_summary", "relationship_summary", "relationship_state")),
        relationship_clues=_first_sequence(turn, ("relationship_clues", "affect_relationship_clues")),
        task_mode=task_mode,
    )


def _percentile(values: list[int], q: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return ordered[index]


def _control_payload(turn: Mapping[str, Any]) -> Mapping[str, Any]:
    control = turn.get("reply_control_packet") or turn.get("control") or {}
    return control if isinstance(control, Mapping) else {}


def _turns_for_case(case: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    turns = case.get("turns")
    if isinstance(turns, Sequence) and not isinstance(turns, (str, bytes, bytearray)):
        return [turn for turn in turns if isinstance(turn, Mapping)]
    return []


def _load_dialogue_cases(cases_path: Path | None) -> list[dict[str, Any]]:
    if cases_path is None:
        return build_continuity_long_cases()
    return _read_json_or_jsonl(cases_path)


def _hash_text(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()[:16]


def recent_window_trace(raw_recent: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    trace: list[dict[str, Any]] = []
    for index, item in enumerate(raw_recent, start=1):
        text = _clean_text(item.get("content") or item.get("text"))
        if not text:
            continue
        trace.append(
            {
                "index": index,
                "role": _clean_text(item.get("role") or item.get("speaker")),
                "chars": len(text),
                "sha256_16": _hash_text(text),
            }
        )
    return trace


def raw_recent_leak_hashes(messages: Sequence[Mapping[str, str]], raw_recent: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    rendered = "\n".join(str(message.get("content") or "") for message in messages)
    leaks: list[str] = []
    for item in raw_recent:
        text = _clean_text(item.get("content") or item.get("text"))
        if text and len(text) >= 6 and text in rendered:
            leaks.append(_hash_text(text))
    return tuple(dict.fromkeys(leaks))


def _turn_specific_loop_facts(turn: Mapping[str, Any]) -> tuple[str, ...]:
    user_message = _clean_text(turn.get("user_message"))
    facts: list[str] = []
    if "多少轮" in user_message:
        facts.append("必要事实：回答要落到先跑三十六轮，用来观察漂移。")
    if "最后一轮" in user_message:
        facts.append("必要事实：最后一轮要收束到稳定性，不索取关注。")
    if "观察行" in user_message:
        facts.append("必要事实：观察行要按问题簇处理，不按单句补规则。")
    return tuple(facts)


def build_loop_request(
    *,
    case_id: str,
    turn: Mapping[str, Any],
    raw_recent: Sequence[Mapping[str, Any]],
    soul_md: str,
    max_recent_turns: int = 8,
    max_facts: int = 6,
) -> IsolatedSurfaceInput:
    control = _control_payload(turn)
    task_mode = _clean_text(control.get("task_mode") or control.get("response_move")) or "daily_chat"
    context = ThreeLayerContinuityContext(
        current_user_message=_clean_text(turn.get("user_message")),
        recent_turns=tuple(raw_recent[-max(0, int(max_recent_turns or 0)) :]),
        mid_term_summary="尼尼和新海天在做三层闭环连续对话测试，上一轮真实输出只进入压缩器。",
        long_term_facts=(
            "最终生成层不能读取原始最近轮次。",
            "上一轮 live 回复只能压缩成控制事实。",
        ),
        task_mode_hint=task_mode,
        response_move_hint=_clean_text(control.get("response_move") or task_mode),
        emotional_tone_hint=_clean_text(control.get("emotional_tone") or "light"),
        relationship_distance=_clean_text(control.get("relationship_distance") or "familiar"),
        information_budget=_clean_text(control.get("information_budget") or "short"),
        question_budget=int(control.get("question_budget") or 0),
        must_include=_clean_list(control.get("must_include") if isinstance(control.get("must_include"), Sequence) else ()),
        must_not_do=_clean_list(control.get("must_not_do") if isinstance(control.get("must_not_do"), Sequence) else ()),
        necessary_facts=(
            *_turn_specific_loop_facts(turn),
            "必要事实：这是 actual-output closed loop，本轮控制包由真实上一轮输出压缩得到。",
        ),
        style_anchor=("三层闭环",),
    )
    affect_packet = _turn_affect_packet(turn, task_mode=task_mode)
    return build_surface_input_from_three_layer_context(
        context,
        soul_md=soul_md,
        case_id=case_id,
        affect_packet=affect_packet,
        max_facts=max_facts,
        max_recent_turns=max_recent_turns,
    )


def _dialogue_turn_counts(cases: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for index, case in enumerate(cases, start=1):
        case_id = _clean_text(case.get("case_id")) or f"dialogue_{index:03d}"
        counts[case_id] = len(_turns_for_case(case))
    return counts


def _summarize(
    *,
    cases: Sequence[Mapping[str, Any]],
    completed: int,
    results: list[dict[str, Any]],
    started: float,
    provider: str,
    model: str,
    gen_params: Mapping[str, Any],
    out_dir: Path,
    raw_recent_leak_count: int,
) -> dict[str, Any]:
    expected_dialogue_turns = _dialogue_turn_counts(cases)
    turns_count = sum(expected_dialogue_turns.values())
    outputs = [dict(row.get("output") or {}) for row in results if isinstance(row.get("output"), Mapping)]
    latencies = [int(row.get("latency_ms") or 0) for row in results if int(row.get("latency_ms") or 0) > 0]
    generated_count = sum(1 for row in results if row.get("ok"))
    dialogue_turns: dict[str, int] = {}
    for row in results:
        case_id = _clean_text(row.get("case_id"))
        if not case_id:
            continue
        dialogue_turns[case_id] = dialogue_turns.get(case_id, 0) + 1
    dialogue_completed_count = sum(
        1
        for case_id, expected_count in expected_dialogue_turns.items()
        if expected_count > 0
        and dialogue_turns.get(case_id, 0) == expected_count
    )
    return {
        "closed_loop": True,
        "recent_window_hash_only": True,
        "dialogues": len(cases),
        "turns": turns_count,
        "completed": completed,
        "generated_count": generated_count,
        "errors": completed - generated_count,
        "output_count": len(outputs),
        "empty_output_count": sum(1 for row in outputs if not _clean_text(row.get("direct_text"))),
        "dialogue_completed_count": dialogue_completed_count,
        "dialogue_completion_rate": round(dialogue_completed_count / len(cases), 4) if cases else None,
        "hard_gate_observation_count": sum(1 for row in outputs if row.get("observation_codes")),
        "raw_recent_leak_count": raw_recent_leak_count,
        "actual_feedback_turns": generated_count,
        "latency_ms_p50": _percentile(latencies, 0.5),
        "latency_ms_p95": _percentile(latencies, 0.95),
        "total_elapsed_ms": int((time.perf_counter() - started) * 1000),
        "provider": provider,
        "model": model,
        "concurrency": 1,
        "gen_params": dict(gen_params),
        "output_dir": str(out_dir),
        "evaluation_mode": "manual_review_no_code_scoring",
    }


def run_live_loop(args: argparse.Namespace) -> dict[str, Any]:
    load_dotenv(ROOT / ".env")
    provider = _clean_text(args.provider or "deepseek")
    model = _clean_text(args.model or os.environ.get("HOST_DEEPSEEK_MODEL_ID") or "deepseek-v4-flash")
    api_key = _clean_text(os.environ.get("DEEPSEEK_API_KEY")) or load_secret_store_provider_key(provider)
    if not api_key:
        raise RuntimeError(f"missing api key for provider={provider}")
    base_url_override = _clean_text(args.base_url or os.environ.get("HOST_DEEPSEEK_BASE_URL")) or None
    gen_params = {"temperature": args.temperature, "top_p": args.top_p, "max_tokens": args.max_tokens}
    cases = _load_dialogue_cases(args.cases)
    soul_md = args.soul.read_text(encoding="utf-8-sig")
    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    raw_path = out_dir / "live_raw_generations.jsonl"
    output_path = out_dir / "live_outputs.jsonl"
    prompt_path = out_dir / "prompt_traces.jsonl"
    loop_path = out_dir / "loop_trace.jsonl"
    progress_path = out_dir / "progress.json"
    summary_path = out_dir / "summary.json"
    for path in (raw_path, output_path, prompt_path, loop_path):
        path.write_text("", encoding="utf-8")

    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    completed = 0
    raw_recent_leak_count = 0
    _write_json(
        progress_path,
        _summarize(
            cases=cases,
            completed=completed,
            results=results,
            started=started,
            provider=provider,
            model=model,
            gen_params=gen_params,
            out_dir=out_dir,
            raw_recent_leak_count=raw_recent_leak_count,
        ),
    )

    for dialogue_index, case in enumerate(cases, start=1):
        case_id = _clean_text(case.get("case_id")) or f"dialogue_{dialogue_index:03d}"
        raw_recent: list[dict[str, str]] = []
        for turn_index, turn in enumerate(_turns_for_case(case), start=1):
            turn_id = _clean_text(turn.get("turn_id")) or f"t{turn_index:02d}"
            surface_case_id = f"{case_id}/{turn_id}"
            turn_started = time.perf_counter()
            recent_for_turn = list(raw_recent[-max(0, int(args.max_recent_turns or 0)) :])
            try:
                request = build_loop_request(
                    case_id=surface_case_id,
                    turn=turn,
                    raw_recent=recent_for_turn,
                    soul_md=soul_md,
                    max_recent_turns=int(args.max_recent_turns),
                    max_facts=int(args.max_facts),
                )
                def generate(messages: Sequence[Mapping[str, str]]) -> str:
                    return invoke_llm_text(
                        provider_name=provider,
                        model_id=model,
                        api_key=api_key,
                        timeout_ms=int(args.timeout_ms),
                        messages=messages,
                        gen_params=dict(gen_params),
                        base_url_override=base_url_override,
                    )

                runtime = SurfaceRuntime(generate)
                result = asyncio.run(runtime.run(request))
                messages = list(result.messages)
                leak_hashes = raw_recent_leak_hashes(messages, recent_for_turn)
                raw_recent_leak_count += len(leak_hashes)
                _append_jsonl(
                    prompt_path,
                    {
                        "case_id": case_id,
                        "turn_id": turn_id,
                        "turn_index": turn_index,
                        "surface_case_id": request.case_id,
                        "messages": messages,
                        "recent_window": recent_window_trace(recent_for_turn),
                        "raw_recent_leak_hashes": list(leak_hashes),
                    },
                )
                latency_ms = result.latency_ms or int((time.perf_counter() - turn_started) * 1000)
                selected = result.selected
                selected_payload = selected.as_dict() if selected else None
                selected_text = result.selected_text
                raw_row = {
                    "case_id": case_id,
                    "turn_id": turn_id,
                    "turn_index": turn_index,
                    "surface_case_id": request.case_id,
                    "ok": True,
                    "provider": provider,
                    "model": model,
                    "latency_ms": latency_ms,
                    "user_message": request.user_message,
                    "raw_output": _clean_text(result.raw_output),
                    "candidate_texts": [candidate.text for candidate in result.candidates],
                    "direct_text": selected_text,
                    "selected_text": selected_text,
                    "selected": selected_payload,
                    "observation_codes": list(result.observation_codes),
                    "generation_trace": dict(result.generation_trace),
                }
                result_row = {
                    "case_id": case_id,
                    "turn_id": turn_id,
                    "turn_index": turn_index,
                    "ok": True,
                    "output": raw_row,
                    "latency_ms": latency_ms,
                }
                _append_jsonl(raw_path, raw_row)
                _append_jsonl(output_path, raw_row)
                _append_jsonl(
                    loop_path,
                    {
                        "case_id": case_id,
                        "turn_id": turn_id,
                        "turn_index": turn_index,
                        "user_message": request.user_message,
                        "reply_control_packet": _model_dump(request.control),
                        "recent_window": recent_window_trace(recent_for_turn),
                        "raw_recent_leak_hashes": list(leak_hashes),
                        "selected": selected_payload,
                        "observation_codes": list(result.observation_codes),
                    },
                )
                raw_recent.append({"role": "user", "content": request.user_message})
                if selected_text:
                    raw_recent.append({"role": "assistant", "content": selected_text})
            except Exception as exc:  # pragma: no cover - live diagnostics
                latency_ms = int((time.perf_counter() - turn_started) * 1000)
                result_row = {
                    "case_id": case_id,
                    "turn_id": turn_id,
                    "turn_index": turn_index,
                    "ok": False,
                    "output": None,
                    "latency_ms": latency_ms,
                }
                _append_jsonl(
                    raw_path,
                    {
                        "case_id": case_id,
                        "turn_id": turn_id,
                        "turn_index": turn_index,
                        "ok": False,
                        "provider": provider,
                        "model": model,
                        "latency_ms": latency_ms,
                        "error": repr(exc),
                    },
                )
            completed += 1
            results.append(result_row)
            summary = _summarize(
                cases=cases,
                completed=completed,
                results=results,
                started=started,
                provider=provider,
                model=model,
                gen_params=gen_params,
                out_dir=out_dir,
                raw_recent_leak_count=raw_recent_leak_count,
            )
            _write_json(progress_path, summary)
            if completed % int(args.progress_every) == 0 or completed == sum(_dialogue_turn_counts(cases).values()):
                print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)

    final_summary = _summarize(
        cases=cases,
        completed=completed,
        results=results,
        started=started,
        provider=provider,
        model=model,
        gen_params=gen_params,
        out_dir=out_dir,
        raw_recent_leak_count=raw_recent_leak_count,
    )
    _write_json(summary_path, final_summary)
    return final_summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a serial actual-output three-layer closed-loop surface eval.")
    parser.add_argument("--cases", default=None, type=Path, help="Optional continuity cases file; defaults to built-in 36-turn long case.")
    parser.add_argument("--soul", default=ROOT / "Soul.md", type=Path)
    parser.add_argument("--out", default=DEFAULT_OUT, type=Path)
    parser.add_argument("--provider", default="deepseek")
    parser.add_argument("--model", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--timeout-ms", type=int, default=60_000)
    parser.add_argument("--temperature", type=float, default=0.35)
    parser.add_argument("--top-p", type=float, default=0.8)
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--max-recent-turns", type=int, default=8)
    parser.add_argument("--max-facts", type=int, default=6)
    parser.add_argument("--progress-every", type=int, default=3)
    args = parser.parse_args(argv)
    summary = run_live_loop(args)
    print("FINAL " + json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
