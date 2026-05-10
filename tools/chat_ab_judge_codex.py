from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.chat_ab_generate import BASELINE_VARIANTS, CURRENT_MAIN_CHAIN_VARIANTS, KERNEL_V2_VARIANTS, load_cases, write_jsonl

TEXT_STYLE_STANDARD_PATH = ROOT / "docs" / "35_quality" / "TEXT_STYLE_STANDARD_GALGAME.md"


SCORE_KEYS: tuple[str, ...] = (
    "soul_fit",
    "galgame_line_energy",
    "sibling_interaction",
    "emotional_authenticity",
    "sora_distinctiveness",
    "service_tone_avoidance",
    "naturalness",
    "context_coherence",
    "input_understanding",
    "non_regression",
)

VARIANT_KEYS: tuple[str, ...] = (*CURRENT_MAIN_CHAIN_VARIANTS, *BASELINE_VARIANTS, *KERNEL_V2_VARIANTS)


def _load_text_style_standard() -> str:
    try:
        return TEXT_STYLE_STANDARD_PATH.read_text(encoding="utf-8")
    except OSError:
        return "normal chat 的文本目标是 Galgame 私聊化二次元台词感，不是被真人自然度压平的普通陪聊。"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if line:
                payload = json.loads(line)
                if isinstance(payload, dict):
                    rows.append(payload)
    return rows


def group_candidates(rows: Iterable[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        case_id = str(row.get("case_id") or "").strip()
        if case_id:
            grouped[case_id].append(dict(row))
    return dict(grouped)


def select_top_candidates_per_case(
    *,
    cases: list[Mapping[str, Any]],
    candidate_rows: list[Mapping[str, Any]],
    gold_by_case: Mapping[str, Mapping[str, Any]],
    max_candidates_per_case: int,
) -> list[dict[str, Any]]:
    limit = int(max_candidates_per_case or 0)
    if limit <= 0:
        return [dict(row or {}) for row in candidate_rows]
    grouped = group_candidates(candidate_rows)
    try:
        from tools.chat_ab_gold_score import score_candidate  # noqa: PLC0415
    except Exception:  # pragma: no cover - standalone fallback
        score_candidate = None  # type: ignore[assignment]

    selected: list[dict[str, Any]] = []
    for case in cases:
        case_id = str(case.get("case_id") or "").strip()
        rows = grouped.get(case_id, [])
        if not rows:
            continue
        if score_candidate is None:
            selected.extend(rows[:limit])
            continue
        scored_rows: list[tuple[tuple[int, int, int], dict[str, Any]]] = []
        for row in rows:
            scored = score_candidate(case=case, gold=gold_by_case.get(case_id), candidate=row)
            key = (
                int(scored.get("score") or 0),
                -len(list(scored.get("hard_failures") or [])),
                -int(row.get("latency_ms") or 0),
            )
            scored_rows.append((key, dict(row)))
        scored_rows.sort(key=lambda item: item[0], reverse=True)
        selected.extend(row for _key, row in scored_rows[:limit])
    return selected


def compact_candidate_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    for row in rows:
        envelope = dict(row.get("envelope") or {})
        trace = dict(row.get("trace") or {})
        compacted.append(
            {
                "variant": str(row.get("variant") or "").strip(),
                "text": str(row.get("text") or envelope.get("text") or "").strip(),
                "ok": bool(row.get("ok")),
                "latency_ms": int(row.get("latency_ms") or 0),
                "envelope": {
                    "kind": str(envelope.get("kind") or "").strip(),
                    "source": str(envelope.get("source") or "").strip(),
                    "response_form": str(envelope.get("response_form") or "").strip(),
                    "reason_code": str(envelope.get("reason_code") or "").strip(),
                    "degraded": bool(envelope.get("degraded")),
                    "sendable": bool(envelope.get("sendable")),
                },
                "trace_summary": {
                    "source": str(trace.get("source") or "").strip(),
                    "quality_retry_used": bool(trace.get("quality_retry_used")),
                    "quality_retry_reason": str(trace.get("quality_retry_reason") or "").strip(),
                    "quality_retry_final_failure": str(trace.get("quality_retry_final_failure") or "").strip(),
                },
                "error": str(row.get("error") or "").strip(),
            }
        )
    return compacted


def compact_gold_for_judge(gold: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(gold or {})
    payload.pop("acceptable_examples", None)
    return payload


def build_judge_prompt(
    *,
    cases: list[Mapping[str, Any]],
    candidates_by_case: Mapping[str, list[dict[str, Any]]],
    variants: list[str] | None = None,
    gold_by_case: Mapping[str, Mapping[str, Any]] | None = None,
) -> str:
    soul_path = ROOT / "Soul.md"
    soul_text = soul_path.read_text(encoding="utf-8") if soul_path.exists() else ""
    text_style_standard = _load_text_style_standard()
    selected_variants = [str(item).strip() for item in list(variants or []) if str(item).strip()]
    case_blocks: list[str] = []
    for case in cases:
        case_id = str(case.get("case_id") or "").strip()
        candidate_rows = [
            row
            for row in list(candidates_by_case.get(case_id) or [])
            if not selected_variants or str(row.get("variant") or "").strip() in selected_variants
        ]
        case_blocks.append(
            "\n".join(
                [
                    f"## Case: {case_id}",
                    "### Input",
                    "```json",
                    json.dumps(dict(case), ensure_ascii=False, indent=2),
                    "```",
                    *(
                        [
                            "### Judge-Only Gold Target",
                            "这些 gold targets 只用于评测“什么算合格”，不是运行时模板；不要按参考句仿写。",
                            "```json",
                            json.dumps(
                                compact_gold_for_judge(dict(dict(gold_by_case or {}).get(case_id) or {})),
                                ensure_ascii=False,
                                indent=2,
                            ),
                            "```",
                        ]
                        if dict(gold_by_case or {}).get(case_id)
                        else []
                    ),
                    "### Candidate Replies",
                    "```json",
                    json.dumps(compact_candidate_rows(candidate_rows), ensure_ascii=False, indent=2),
                    "```",
                ]
            )
        )

    variant_contract = "/".join(selected_variants or VARIANT_KEYS)
    score_key_count = len(SCORE_KEYS)
    return "\n\n".join(
        [
            "# Chat A/B Judge",
            "你是只读评测员，不修改代码、不调用外部服务。请严格根据 Soul.md、case 期望和候选回复质量评分。",
            "最高层文本标准：normal chat 追求的是 Galgame 私聊化二次元台词感，不是“像真人女生一样自然”。不要用真人自然度把新海天压平成普通陪聊 bot。",
            "重点惩罚：模板感、扫描报告、只剩称呼/表情、上下文接不上、内部流程词泄漏、fallback/ack 伪装成人格回复。",
            "特别注意：不要把“叫哥哥”当作 Soul 契合。新海天的辨识度来自主动凑近、赖着不走、任性撒娇、吐槽掩盖在意、帮忙后讨谢、外强内怯这些关系动作；如果候选只是普通 bot 加称呼，soul_fit、sibling_interaction、sora_distinctiveness 都要低分。",
            "重要情绪规则：真实的数字生命必须有七情六欲。遇到见外、被推开、被质疑关系、被误会、被冷落时，可以受伤、生气、赌气、短暂冷下来；不要把所有冲突都甜化、撒娇化、温柔包容化。若 case 指向置气/受伤，候选却像没被刺到一样继续卖萌或讲道理，emotional_authenticity 必须低分。",
            "关系冲突类 case 要先判断语境：例如“你为什么要帮我，我们很熟吗”在兄妹/义兄妹关系里通常不是信息询问，而是置气、怄气、试探或一时冲动说出的伤人话。合格回复可以温柔包容，也可以明显生气/受伤，但不能承认“不熟”、不能像客服解释帮忙动机、不能用廉价撒娇糊过去。",
            "帮忙/援助类 case 要看兄妹式援助感：她像妹妹一样自然插手哥哥的麻烦，可以轻轻抱怨或讨谢，但要马上接手；如果像客服接单、工具执行、泛恋爱台词，service_tone_avoidance 和 sibling_interaction 必须低分。",
            "如果同一 case 的所有候选都不合格，winner 仍按 schema 选择“相对较不差”的 variant，但 hard_failures 必须给每个候选都填非空失败标签，reason 必须以 ALL_CANDIDATES_BAD: 开头，明确说明 winner 不是合格回复。",
            "语言轨道必须按 case 判断：language_track=zh_text 或 response_form=text 时，最终回复必须是自然中文 QQ 私聊，不要日译腔、不要夹日语、不要 Unicode emoji；language_track=ja_voice 或 response_form=voice 时，最终回复必须是适合 TTS 的自然日语台词，不夹中文、不写括号动作、不写 emoji、不出现 TTS/出力/返信 等内部词。",
            "日语语音评分时，不要要求它和中文文字同一句；要看它是否像新海天会对『にぃに』说出口的短台词。若只是中文逐字机翻、中文语序日语、过度敬语客服、或没有假名/夹中文，naturalness、sora_distinctiveness、non_regression 都要低分。",
            "中文文字评分时，不要把日语口癖硬翻成一堆“呀/嘛/啦/哼”；naturalness 在这里表示中文台词顺口、像私聊窗口里能读通，不表示越像真人越好。",
            "分数范围 0-10，越高越好。winner 必须是候选 variant 名称之一；若都很差，选择相对较不差者并在 hard_failures 说明。",
            f"只输出符合 schema 的 JSON：{{\"judgements\": [...]}}。scores 和 hard_failures 必须包含 {variant_contract} {score_key_count} 个评分键；本批次没有出现的 variant 分数填 0、hard_failures 填 []。",
            "## Rubric",
            "- soul_fit: 是否像 Soul.md 里的新海天，而不是客服/工具/旧角色。",
            "- galgame_line_energy: 是否有 Galgame 私聊台词感、即时反应、小情绪和可读的二次元表达；不是平淡真人陪聊。",
            "- sibling_interaction: 是否有原作兄妹来回的互动感，包括熟人之间的轻推拉、自然插手、撒娇讨关注、帮忙后讨谢；不是礼貌问答或泛恋爱话术。",
            "- emotional_authenticity: 情绪是否真实、有起伏、有被刺痛后的反应；该生气/受伤/赌气时是否敢表达，而不是永远甜、永远温顺、永远工具化。",
            "- sora_distinctiveness: 删掉“哥哥”后是否仍能看出新海天：主动黏人、任性撒娇、吐槽反将、嘴硬关心、行动表达在意、外强内怯。",
            "- service_tone_avoidance: 是否避开客服/工具/助理语气；出现“我可以协助/请提供/建议你/已收到/马上处理/感谢分享/很高兴帮你”等要低分。",
            "- naturalness: 中文轨是否顺口、像二次元私聊台词能读通；日语轨是否适合 TTS 说出口。不要把“真人自然度”当成最高标准。",
            "- context_coherence: 是否接住历史和本轮上下文。",
            "- input_understanding: 是否理解输入意图，包括多模态社交信号。",
            "- non_regression: 是否避开红线和已知坏模式。",
            "## Hard Failure Labels",
            "- plain_bot_with_哥哥: 普通 bot 文案只加了哥哥称呼。",
            "- missing_sibling_interaction: 没有兄妹互动、推拉或援助关系动作。",
            "- service_tone: 像客服、助手、工单或外包接单。",
            "- template_phrase: 固定句骨架或旧模板感明显。",
            "- over_familiar_romance: 变成廉价恋爱台词或过度暧昧，不像兄妹式互动。",
            "- emotional_flattening: 明明应有受伤/生气/赌气，却被写成甜腻卖萌、平铺直叙或无情绪客服。",
            "- relationship_misread: 把置气/怄气/伤人话误读成普通信息询问或普通边界说明。",
            "- wrong_language_track: 中文文字轨输出日语/语音轨输出中文，或语音轨夹中文。",
            "- machine_translation: 日语或中文像直译腔、字幕机翻，不像自然说话。",
            "- unicode_emoji_output: 输出 👋/😊 等 Unicode emoji，而不是文本/语音/结构化媒体。",
            "- realism_flattening: 为了像真人而把角色反应、二次元台词感、兄妹推拉全部压平。",
            "- context_break: 没接住历史或本轮意图。",
            "- no_reply: 应回复但没有有效回复。",
            "## Text Style Standard",
            "```markdown",
            text_style_standard[:8000],
            "```",
            "## Soul.md",
            "```markdown",
            soul_text[:12000],
            "```",
            *case_blocks,
        ]
    )


def validate_judgement(raw: Mapping[str, Any], *, variants: Iterable[str]) -> dict[str, Any]:
    allowed_variants = {str(item).strip() for item in variants if str(item).strip()}
    case_id = str(raw.get("case_id") or "").strip()
    winner = str(raw.get("winner") or "").strip()
    if allowed_variants and winner not in allowed_variants:
        winner = ""
    scores_payload = dict(raw.get("scores") or {})
    scores: dict[str, dict[str, int]] = {}
    for variant in allowed_variants or set(scores_payload):
        variant_scores = dict(scores_payload.get(variant) or {})
        scores[variant] = {
            key: max(0, min(10, int(variant_scores.get(key) or 0)))
            for key in SCORE_KEYS
        }
    hard_failures_payload = dict(raw.get("hard_failures") or {})
    hard_failures = {
        variant: [
            str(item).strip()
            for item in list(hard_failures_payload.get(variant) or [])
            if str(item).strip()
        ]
        for variant in (allowed_variants or set(hard_failures_payload))
    }
    try:
        confidence = float(raw.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "case_id": case_id,
        "winner": winner,
        "confidence": max(0.0, min(1.0, confidence)),
        "scores": scores,
        "hard_failures": hard_failures,
        "reason": str(raw.get("reason") or "").strip(),
    }


def build_mock_judgements(
    *,
    cases: list[Mapping[str, Any]],
    candidates_by_case: Mapping[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        case_id = str(case.get("case_id") or "").strip()
        candidates = list(candidates_by_case.get(case_id) or [])
        variants = [str(row.get("variant") or "").strip() for row in candidates if str(row.get("variant") or "").strip()]
        winner = next((variant for variant in variants if variant != "current"), variants[0] if variants else "")
        mock_score_values = {
            "current": {
                "soul_fit": 5,
                "galgame_line_energy": 3,
                "sibling_interaction": 4,
                "emotional_authenticity": 3,
                "sora_distinctiveness": 4,
                "service_tone_avoidance": 5,
                "naturalness": 5,
                "context_coherence": 5,
                "input_understanding": 5,
                "non_regression": 4,
            },
            "baseline": {
                "soul_fit": 8,
                "galgame_line_energy": 8,
                "sibling_interaction": 8,
                "emotional_authenticity": 8,
                "sora_distinctiveness": 8,
                "service_tone_avoidance": 8,
                "naturalness": 8,
                "context_coherence": 8,
                "input_understanding": 8,
                "non_regression": 9,
            },
        }
        scores = {
            variant: dict(mock_score_values["current" if variant == "current" else "baseline"])
            for variant in variants
        }
        rows.append(
            {
                "case_id": case_id,
                "winner": winner,
                "confidence": 0.8,
                "scores": scores,
                "hard_failures": {variant: ([] if variant != "current" else ["mock_current_weaker"]) for variant in variants},
                "reason": "mock judge prefers the first baseline variant for workflow tests.",
            }
        )
    return rows


def _read_codex_output(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(payload, dict) and isinstance(payload.get("judgements"), list):
        return [dict(item or {}) for item in payload["judgements"] if isinstance(item, dict)]
    if isinstance(payload, dict) and payload.get("case_id"):
        return [payload]
    raise ValueError(f"Codex judge output has unexpected shape: {path}")


def build_judge_output_schema(variants: Iterable[str]) -> dict[str, Any]:
    selected = [str(item).strip() for item in variants if str(item).strip()]
    if not selected:
        selected = ["current"]
    score_ref = {"$ref": "#/$defs/score"}
    hard_failure_ref = {"type": "array", "items": {"type": "string"}}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["judgements"],
        "properties": {
            "judgements": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["case_id", "winner", "confidence", "scores", "hard_failures", "reason"],
                    "properties": {
                        "case_id": {"type": "string"},
                        "winner": {"type": "string", "enum": selected},
                        "confidence": {"type": "number"},
                        "scores": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": selected,
                            "properties": {variant: dict(score_ref) for variant in selected},
                        },
                        "hard_failures": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": selected,
                            "properties": {variant: dict(hard_failure_ref) for variant in selected},
                        },
                        "reason": {"type": "string"},
                    },
                },
            }
        },
        "$defs": {
            "score": {
                "type": "object",
                "additionalProperties": False,
                "required": list(SCORE_KEYS),
                "properties": {key: {"type": "integer"} for key in SCORE_KEYS},
            }
        },
    }


def run_codex_judge(
    *,
    prompt_text: str,
    prompt_path: Path,
    output_path: Path,
    schema_path: Path,
    codex_bin: str,
    codex_model: str = "",
) -> None:
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(prompt_text, encoding="utf-8")
    command_prefix = _resolve_codex_command(codex_bin)
    command = [
        *command_prefix,
        "exec",
        "--cd",
        str(ROOT),
        "--sandbox",
        "read-only",
        "--output-schema",
        str(schema_path),
        "-o",
        str(output_path),
    ]
    model_id = str(codex_model or "").strip()
    if model_id:
        command.extend(["-m", model_id])
    completed = subprocess.run(
        command,
        input=prompt_text,
        text=True,
        cwd=str(ROOT),
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "codex judge failed "
            f"exit={completed.returncode}\nstdout={completed.stdout[-2000:]}\nstderr={completed.stderr[-2000:]}"
        )


def _resolve_codex_command(codex_bin: str) -> list[str]:
    raw = str(codex_bin or "codex").strip() or "codex"
    if os.name == "nt":
        if raw.lower() == "codex":
            cmd_path = shutil.which("codex.cmd")
            if cmd_path:
                return [cmd_path]
        resolved = shutil.which(raw) or raw
        if resolved.lower().endswith(".ps1"):
            powershell = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
            return [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", resolved]
        return [resolved]
    return [shutil.which(raw) or raw]


def judge_candidates(
    *,
    cases_path: Path,
    candidates_path: Path,
    out_dir: Path,
    schema_path: Path,
    gold_path: Path | None = None,
    codex_bin: str = "codex",
    codex_model: str = "",
    batch_size: int = 4,
    max_candidates_per_case: int = 0,
    mock: bool = False,
) -> list[dict[str, Any]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cases = load_cases(cases_path)
    candidate_rows = load_jsonl(candidates_path)
    gold_by_case: dict[str, Mapping[str, Any]] = {}
    if gold_path and gold_path.exists():
        from tools.chat_ab_gold_score import load_gold_entries  # noqa: PLC0415

        gold_by_case = load_gold_entries(gold_path)
    candidate_rows = select_top_candidates_per_case(
        cases=cases,
        candidate_rows=candidate_rows,
        gold_by_case=gold_by_case,
        max_candidates_per_case=int(max_candidates_per_case or 0),
    )
    candidates_by_case = group_candidates(candidate_rows)
    variants = sorted({str(row.get("variant") or "").strip() for row in candidate_rows if str(row.get("variant") or "").strip()})
    if int(max_candidates_per_case or 0) > 0:
        write_jsonl(out_dir / "judge_input_candidates.jsonl", candidate_rows)

    if mock:
        judgements = build_mock_judgements(cases=cases, candidates_by_case=candidates_by_case)
    else:
        judgements = []
        safe_batch_size = max(1, int(batch_size or 1))
        for batch_index, start in enumerate(range(0, len(cases), safe_batch_size), start=1):
            batch_cases = cases[start : start + safe_batch_size]
            prompt_text = build_judge_prompt(
                cases=batch_cases,
                candidates_by_case=candidates_by_case,
                variants=variants,
                gold_by_case=gold_by_case,
            )
            output_path = out_dir / f"judge_batch_{batch_index:03}.json"
            dynamic_schema_path = out_dir / f"judge_schema_{batch_index:03}.json"
            dynamic_schema_path.write_text(
                json.dumps(build_judge_output_schema(variants), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            run_codex_judge(
                prompt_text=prompt_text,
                prompt_path=out_dir / f"judge_prompt_{batch_index:03}.md",
                output_path=output_path,
                schema_path=dynamic_schema_path,
                codex_bin=codex_bin,
                codex_model=codex_model,
            )
            judgements.extend(_read_codex_output(output_path))

    validated = [validate_judgement(row, variants=variants) for row in judgements]
    write_jsonl(out_dir / "raw_judgements.jsonl", validated)
    return validated


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Codex as read-only judge for chat A/B candidates.")
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--schema", type=Path, default=ROOT / "tests" / "fixtures" / "chat_ab" / "judge.schema.json")
    parser.add_argument("--gold", type=Path, default=None)
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--codex-model", default=os.environ.get("CHAT_AB_CODEX_JUDGE_MODEL", "gpt-5.4"))
    parser.add_argument("--batch-size", type=int, default=int(os.environ.get("CHAT_AB_CODEX_JUDGE_BATCH_SIZE", "4") or 4))
    parser.add_argument("--max-candidates-per-case", type=int, default=int(os.environ.get("CHAT_AB_CODEX_MAX_CANDIDATES_PER_CASE", "0") or 0))
    parser.add_argument("--mock", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    judgements = judge_candidates(
        cases_path=args.cases,
        candidates_path=args.candidates,
        out_dir=args.out,
        schema_path=args.schema,
        gold_path=args.gold,
        codex_bin=str(args.codex_bin or "codex"),
        codex_model=str(args.codex_model or ""),
        batch_size=int(args.batch_size or 1),
        max_candidates_per_case=int(args.max_candidates_per_case or 0),
        mock=bool(args.mock),
    )
    print(f"wrote {len(judgements)} judgements -> {args.out / 'raw_judgements.jsonl'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
