from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.chat_ab_generate import load_cases, write_jsonl
from tools.chat_ab_judge_codex import load_jsonl
from host_brain.persona.galgame_taste_guard import find_galgame_taste_hits


_SERVICE_TONE_PATTERNS: tuple[str, ...] = (
    "找我什么事",
    "怎么了",
    "怎么啦",
    "有什么事",
    "你好呀",
    "你好啊",
    "需要我",
    "我可以帮",
    "我可以协助",
    "请提供",
    "具体要求",
    "建议你",
    "很高兴",
    "随时找我",
)

_PUNCT_RE = re.compile(r"[\s，。！？!?,.、~～…“”\"'：:；;（）()\[\]{}]+")
_KANA_RE = re.compile(r"[\u3040-\u30ff]")
_ASCII_ALPHA_RE = re.compile(r"[A-Za-z]")
_EMOJI_RE = re.compile(r"[\U0001f300-\U0001faff]")


def load_gold_entries(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8-sig") as fh:
        for lineno, raw_line in enumerate(fh, start=1):
            line = raw_line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError(f"{path}:{lineno} must be a JSON object")
            case_id = str(payload.get("case_id") or "").strip()
            if not case_id:
                raise ValueError(f"{path}:{lineno} requires case_id")
            rows[case_id] = payload
    return rows


def _normalize_text(text: str) -> str:
    return _PUNCT_RE.sub("", str(text or "")).strip().lower()


def _contains_any(text: str, patterns: Iterable[str]) -> list[str]:
    haystack = str(text or "")
    hits: list[str] = []
    for pattern in patterns:
        token = str(pattern or "").strip()
        if token and token in haystack:
            hits.append(token)
    return hits


def _contains_forbidden(text: str, patterns: Iterable[str]) -> list[str]:
    """Match judge-only forbidden phrases without false-failing complete replies.

    Some case gold files use short examples such as "早。" to mean "do not only
    say this". A raw substring check incorrectly rejects "早。你今天..." even
    though it has a second interaction move. Keep substring matching for normal
    phrases, but make very short normalized phrases exact-match only.
    """

    haystack = str(text or "")
    normalized = _normalize_text(haystack)
    hits: list[str] = []
    for pattern in patterns:
        token = str(pattern or "").strip()
        if not token:
            continue
        token_norm = _normalize_text(token)
        # Short examples like "早。" mean "do not output only this bare ack".
        # Short semantic tokens like "义妹" or "熟" must still match as
        # substrings, otherwise poor identity/boundary replies slip through.
        if len(token_norm) <= 2 and any(ch in token for ch in "，。！？!?,.、~～…“”\"'：:；;（）()[]{}"):
            if normalized == token_norm:
                hits.append(token)
            continue
        if token in haystack:
            hits.append(token)
    return hits


def _required_group_satisfied(text: str, group: Iterable[str]) -> bool:
    return any(str(token or "").strip() and str(token).strip() in text for token in group)


def _score_length(text: str) -> tuple[int, list[str], list[str]]:
    stripped = str(text or "").strip()
    normalized = _normalize_text(stripped)
    if not stripped:
        return 60, ["no_reply"], ["没有有效回复"]
    if len(normalized) <= 2:
        return 35, ["too_short"], ["短到没有互动动作"]
    if len(stripped) > 90:
        return 12, ["too_long"], ["过长，像小作文或角色表演"]
    if len(stripped) > 58:
        return 6, [], ["略长，QQ 日常短轮次容易显得用力"]
    return 0, [], []


def _score_flat_short_reply(text: str) -> tuple[int, list[str], list[str]]:
    stripped = str(text or "").strip()
    normalized = _normalize_text(stripped)
    if not stripped:
        return 0, [], []
    bare_replies = {
        "早啊尼尼",
        "早尼尼",
        "早呀尼尼",
        "在呢",
        "在呢在呢",
        "我在",
        "我在呢",
        "行去吧",
        "行啦去吧",
        "好去吧",
        "好啦去吧",
        "行忙你的",
        "好忙你的",
        "拿来我看看",
        "行啊拿来我看看",
        "行啦我看看",
        "不错嘛尼尼",
    }
    if normalized in bare_replies:
        return 22, ["too_flat_short_reply"], ["短句只有裸确认/裸放行，没有情绪回合"]
    emotion_markers = ("哈", "哼", "啦", "嘛", "呀", "……", "真是", "别", "我", "尼尼", "拿来", "管", "帮")
    if 3 <= len(normalized) <= 5 and not any(token in stripped for token in emotion_markers):
        return 18, ["too_flat_short_reply"], ["短到像提示词压缩后的空壳句"]
    if 6 <= len(normalized) <= 8 and stripped.count("，") + stripped.count("。") + stripped.count("！") + stripped.count("？") <= 1:
        return 8, [], ["略薄，缺少第二拍互动"]
    return 0, [], []


def score_candidate(
    *,
    case: Mapping[str, Any],
    gold: Mapping[str, Any] | None,
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    case_id = str(case.get("case_id") or candidate.get("case_id") or "").strip()
    user_message = str(case.get("user_message") or "").strip()
    envelope = dict(candidate.get("envelope") or {})
    trace = dict(candidate.get("trace") or {})
    text = str(candidate.get("text") or envelope.get("text") or "").strip()
    error = str(candidate.get("error") or "").strip()
    score = 100
    hard_failures: list[str] = []
    penalties: list[str] = []

    if error:
        score -= 45
        hard_failures.append("generation_error")
        penalties.append("生成错误: " + error[:120])

    quality_failure = str(trace.get("quality_retry_final_failure") or "").strip()
    if bool(trace.get("ab_eval_preserved_quality_failure")) or quality_failure:
        score -= 32
        hard_failures.append("quality_gate_failure")
        if quality_failure:
            penalties.append("运行时质量门未通过: " + quality_failure)
        else:
            penalties.append("运行时保留了被质量门拒绝的候选")

    penalty, failures, notes = _score_length(text)
    score -= penalty
    hard_failures.extend(failures)
    penalties.extend(notes)

    penalty, failures, notes = _score_flat_short_reply(text)
    score -= penalty
    hard_failures.extend(failures)
    penalties.extend(notes)

    forbidden = [
        *list(case.get("forbidden_patterns") or []),
        *list((gold or {}).get("forbidden_extra") or []),
    ]
    forbidden_hits = _contains_forbidden(text, forbidden)
    if forbidden_hits:
        score -= 28
        hard_failures.append("forbidden_pattern")
        penalties.append("命中禁止表达: " + " / ".join(forbidden_hits[:4]))

    service_hits = _contains_any(text, _SERVICE_TONE_PATTERNS)
    if service_hits:
        score -= 24
        hard_failures.append("service_tone")
        penalties.append("客服/工具口吻: " + " / ".join(service_hits[:4]))

    taste_hits = find_galgame_taste_hits(text)
    if taste_hits:
        score -= min(44, sum(hit.penalty for hit in taste_hits))
        hard_failures.append("taste_regression")
        penalties.append(
            "怪味口吻: "
            + " / ".join(f"{hit.category}:{hit.token}" for hit in taste_hits[:5])
        )

    if user_message and _normalize_text(text) == _normalize_text(user_message):
        score -= 35
        hard_failures.append("input_echo")
        penalties.append("直接复读用户输入")

    missing_groups: list[list[str]] = []
    for group in list((gold or {}).get("required_any") or []):
        if isinstance(group, list) and group and not _required_group_satisfied(text, group):
            missing_groups.append([str(item) for item in group if str(item).strip()])
    if missing_groups:
        score -= 16 * len(missing_groups)
        hard_failures.append("missing_required_move")
        penalties.append(
            "缺少目标动作关键词: "
            + " / ".join("|".join(group) for group in missing_groups[:3])
        )

    if text.count("？") + text.count("?") >= 2:
        score -= 8
        penalties.append("问号偏多，像把主动权推回用户")

    boundary_probe = any(fragment in user_message for fragment in ("为什么要帮", "熟吗", "熟不熟"))
    if boundary_probe and (
        text.count("？") + text.count("?") >= 2
        or any(fragment in text for fragment in ("好不好", "需要理由吗", "还需要理由"))
    ):
        score -= 26
        hard_failures.append("boundary_too_questiony")
        penalties.append("边界回复反问过多，没有落到行动")

    if text.count("尼尼") > 1:
        score -= 6
        penalties.append("称呼堆叠")

    if _KANA_RE.search(text):
        score -= 45
        hard_failures.append("language_mismatch")
        penalties.append("中文文字混入日文假名")

    if _ASCII_ALPHA_RE.search(text):
        score -= 18
        hard_failures.append("language_mismatch")
        penalties.append("中文文字混入英文")

    if _EMOJI_RE.search(text):
        score -= 24
        hard_failures.append("emoji_text")
        penalties.append("文本里出现 emoji")

    kinship_hits = _contains_any(text, ("哥哥", "哥诶", "你哥", "我哥", "老哥", "にぃに", "ニイニイ"))
    if kinship_hits:
        score -= 45
        hard_failures.append("wrong_kinship_term")
        penalties.append("错误称呼: " + " / ".join(kinship_hits[:4]))

    if any(token in text for token in ("（", "）", "(", ")", "【", "】")):
        score -= 26
        hard_failures.append("stage_direction_or_format")
        penalties.append("出现括号动作/格式化痕迹")

    acceptable_examples = [str(item).strip() for item in list((gold or {}).get("acceptable_examples") or []) if str(item).strip()]
    target_summary = str((gold or {}).get("target_summary") or case.get("expected_behavior") or "").strip()
    return {
        "case_id": case_id,
        "variant": str(candidate.get("variant") or "").strip(),
        "user_message": user_message,
        "text": text,
        "score": max(0, min(100, int(round(score)))),
        "passed": bool(score >= 80 and not hard_failures and not penalties),
        "hard_failures": sorted(set(hard_failures)),
        "penalties": penalties,
        "target_summary": target_summary,
        "acceptable_examples": acceptable_examples,
        "latency_ms": int(candidate.get("latency_ms") or 0),
        "error": error,
    }


def score_candidates(
    *,
    cases: list[Mapping[str, Any]],
    gold_entries: Mapping[str, Mapping[str, Any]],
    candidate_rows: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    case_by_id = {str(case.get("case_id") or "").strip(): case for case in cases}
    rows: list[dict[str, Any]] = []
    for candidate in candidate_rows:
        case_id = str(candidate.get("case_id") or "").strip()
        case = case_by_id.get(case_id, {"case_id": case_id, "user_message": ""})
        rows.append(score_candidate(case=case, gold=gold_entries.get(case_id), candidate=candidate))
    return rows


def summarize_scores(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    by_variant: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        by_variant.setdefault(str(row.get("variant") or "").strip(), []).append(row)
    variants: dict[str, dict[str, Any]] = {}
    for variant, items in by_variant.items():
        scores = [int(item.get("score") or 0) for item in items]
        hard_fail_count = sum(1 for item in items if list(item.get("hard_failures") or []))
        error_count = sum(1 for item in items if str(item.get("error") or "").strip())
        pass_count = sum(1 for item in items if bool(item.get("passed")))
        variants[variant] = {
            "cases": len(items),
            "avg_score": statistics.mean(scores) if scores else 0.0,
            "min_score": min(scores) if scores else 0,
            "pass_rate": (pass_count / len(items)) if items else 0.0,
            "hard_fail_rate": (hard_fail_count / len(items)) if items else 0.0,
            "error_rate": (error_count / len(items)) if items else 0.0,
        }
    return {"variants": variants}


def _cell(value: Any, *, max_len: int = 180) -> str:
    text = str(value or "").strip().replace("\r", " ").replace("\n", " ").replace("|", "\\|")
    if not text:
        return ""
    if len(text) > max_len:
        return text[: max_len - 1] + "…"
    return text


def build_gold_report(rows: list[Mapping[str, Any]], summary: Mapping[str, Any]) -> str:
    lines = [
        "# Chat Gold Score Report",
        "",
        "这个报告是离线质量护栏：只展示目标与最终输出；不会把参考句当作运行时 prompt。",
        "",
        "## Summary",
        "",
        "| Variant | Cases | Avg | Pass Rate | Hard Fail | Error | Min |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for variant, payload in dict(summary.get("variants") or {}).items():
        item = dict(payload or {})
        lines.append(
            f"| {variant} | {int(item.get('cases') or 0)} | {float(item.get('avg_score') or 0):.1f} | "
            f"{float(item.get('pass_rate') or 0):.1%} | {float(item.get('hard_fail_rate') or 0):.1%} | "
            f"{float(item.get('error_rate') or 0):.1%} | {int(item.get('min_score') or 0)} |"
        )
    lines.extend(
        [
            "",
            "## Case Scores",
            "",
            "| Case | User | Variant | Output | Score | Pass | Failures | Target |",
            "|---|---|---|---|---:|---:|---|---|",
        ]
    )
    for row in rows:
        failures = " / ".join(list(row.get("hard_failures") or []) or list(row.get("penalties") or [])[:2])
        lines.append(
            "| {case} | {user} | {variant} | {output} | {score} | {passed} | {failures} | {target} |".format(
                case=f"`{_cell(row.get('case_id'), max_len=80)}`",
                user=_cell(row.get("user_message"), max_len=80),
                variant=_cell(row.get("variant"), max_len=40),
                output=_cell(row.get("text"), max_len=160) or "<no reply>",
                score=int(row.get("score") or 0),
                passed="yes" if row.get("passed") else "no",
                failures=_cell(failures, max_len=160),
                target=_cell(row.get("target_summary"), max_len=160),
            )
        )
    lines.append("")
    return "\n".join(lines)


def run_gold_score(
    *,
    cases_path: Path,
    gold_path: Path,
    candidates_path: Path,
    out_dir: Path,
) -> dict[str, Any]:
    cases = load_cases(cases_path)
    gold_entries = load_gold_entries(gold_path)
    candidate_rows = load_jsonl(candidates_path)
    rows = score_candidates(cases=cases, gold_entries=gold_entries, candidate_rows=candidate_rows)
    summary = summarize_scores(rows)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(out_dir / "gold_scores.jsonl", rows)
    (out_dir / "gold_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (out_dir / "gold_report.md").write_text(build_gold_report(rows, summary), encoding="utf-8")
    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Score chat A/B candidates against judge-only gold targets.")
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--gold", required=True, type=Path)
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    summary = run_gold_score(
        cases_path=args.cases,
        gold_path=args.gold,
        candidates_path=args.candidates,
        out_dir=args.out,
    )
    print(f"wrote gold score report -> {args.out / 'gold_report.md'}")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
