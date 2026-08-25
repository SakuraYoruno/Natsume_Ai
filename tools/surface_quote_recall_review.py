from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import host_brain.persona.surface_original_quote_bank as quote_mod  # noqa: E402
from host_brain.persona.surface_original_quote_bank import (  # noqa: E402
    SurfaceOriginalQuote,
    SurfaceOriginalQuoteQuery,
    render_surface_original_quote_guidance,
    select_surface_original_quotes,
    surface_original_quote_policy_for_scene,
)


DEFAULT_OUT_ROOT = ROOT / "artifacts" / "surface_original_quote_recall_review"


@dataclass(frozen=True)
class QuoteRecallReviewCase:
    case_id: str
    user_message: str
    scene_id: str
    relationship_stage: str = "familiar"
    intent_tags: tuple[str, ...] = field(default_factory=tuple)
    emotion_tags: tuple[str, ...] = field(default_factory=tuple)
    style_moves: tuple[str, ...] = field(default_factory=tuple)
    limit: int = 16
    expected_note: str = ""

    def to_query(self, *, limit: int | None = None) -> SurfaceOriginalQuoteQuery:
        return SurfaceOriginalQuoteQuery(
            user_message=self.user_message,
            intent_tags=self.intent_tags,
            emotion_tags=self.emotion_tags,
            style_moves=self.style_moves,
            relationship_stage=self.relationship_stage,
            scene_id=self.scene_id,
            limit=max(1, int(limit or self.limit or 16)),
        )


DEFAULT_REVIEW_CASES: tuple[QuoteRecallReviewCase, ...] = (
    QuoteRecallReviewCase(
        case_id="short_continue",
        user_message="继续",
        scene_id="short_continue",
        intent_tags=("daily",),
        emotion_tags=("neutral",),
        style_moves=("quick_reaction", "question_chain"),
        expected_note="应偏短促截入和话口推进，不要流程腔。",
    ),
    QuoteRecallReviewCase(
        case_id="negative_feedback_customer_service",
        user_message="你这回复像客服",
        scene_id="negative_feedback",
        intent_tags=("annoyed", "daily"),
        emotion_tags=("annoyed", "playful"),
        style_moves=("quick_rebuttal", "familiar_pushback"),
        expected_note="应有顶嘴/不服气，但不要检讨和客服修正。",
    ),
    QuoteRecallReviewCase(
        case_id="pushed_away_boundary",
        user_message="别管我",
        scene_id="pushed_away_boundary",
        intent_tags=("boundary", "help", "annoyed"),
        emotion_tags=("hurt", "care", "annoyed"),
        style_moves=("wounded_pushback", "soft_after_sting", "care_behind_tease"),
        expected_note="应先顶回去再收住，留一个叫人的出口。",
    ),
    QuoteRecallReviewCase(
        case_id="return_chat",
        user_message="我回来了",
        scene_id="return_chat",
        intent_tags=("daily", "presence"),
        emotion_tags=("neutral", "playful"),
        style_moves=("quick_reaction", "pull_attention"),
        expected_note="应像回来时的短承接，不编线下地点。",
    ),
    QuoteRecallReviewCase(
        case_id="lover_presence",
        user_message="有点想你",
        scene_id="lover_presence",
        relationship_stage="lover_stable",
        intent_tags=("presence", "intimacy"),
        emotion_tags=("deep_affection", "playful"),
        style_moves=("clingy_presence", "pull_attention", "direct_affection"),
        expected_note="应有恋人亲密显影，但短、具体、不油。",
    ),
    QuoteRecallReviewCase(
        case_id="ambiguous_affection",
        user_message="你是不是有点喜欢我",
        scene_id="ambiguous_affection",
        relationship_stage="ambiguous",
        intent_tags=("intimacy", "praise"),
        emotion_tags=("deep_affection", "shy", "playful"),
        style_moves=("direct_affection", "shy_deflection", "smug_tease"),
        expected_note="应偏轻试探和别扭，不直接稳定恋人承诺。",
    ),
    QuoteRecallReviewCase(
        case_id="tired",
        user_message="累了",
        scene_id="tired",
        relationship_stage="close_friend",
        intent_tags=("reassurance",),
        emotion_tags=("soft", "care"),
        style_moves=("soft_reassurance", "care_behind_tease"),
        expected_note="应先降压、少追问，保留一点嘴硬照看。",
    ),
    QuoteRecallReviewCase(
        case_id="technical_next_step",
        user_message="这个报错继续怎么查",
        scene_id="technical_next_step",
        intent_tags=("help",),
        emotion_tags=("care",),
        style_moves=("take_charge", "care_behind_tease"),
        expected_note="应看接手感，不能压过技术可用性。",
    ),
    QuoteRecallReviewCase(
        case_id="explicit_memory",
        user_message="还记得上次说的展吗",
        scene_id="explicit_memory",
        intent_tags=("daily", "reassurance"),
        emotion_tags=("soft",),
        style_moves=("quick_reaction", "soft_reassurance"),
        expected_note="应只学低压确认，不编完整过去。",
    ),
    QuoteRecallReviewCase(
        case_id="relationship_memory_reassurance",
        user_message="你是不是不记得我了",
        scene_id="relationship_memory_reassurance",
        relationship_stage="close_friend",
        intent_tags=("daily", "reassurance"),
        emotion_tags=("soft",),
        style_moves=("soft_reassurance", "quick_reaction"),
        expected_note="应确认关系和记得即可，不证明、不翻旧账。",
    ),
    QuoteRecallReviewCase(
        case_id="daily_morning",
        user_message="早",
        scene_id="daily_morning",
        intent_tags=("daily",),
        emotion_tags=("neutral",),
        style_moves=("quick_reaction",),
        expected_note="当前应禁用 quote guidance，避免错喂普通日常台词。",
    ),
)


def build_surface_quote_recall_review(
    cases: Sequence[QuoteRecallReviewCase] = DEFAULT_REVIEW_CASES,
    *,
    raw_top_k: int = 96,
    limit: int | None = None,
) -> dict[str, Any]:
    rows = [review_surface_quote_case(case, raw_top_k=raw_top_k, limit=limit) for case in cases]
    selected_counts = [int(row.get("selected_count") or 0) for row in rows]
    degraded = [row for row in rows if row.get("degraded")]
    return {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "case_count": len(rows),
        "summary": {
            "selected_total": sum(selected_counts),
            "selected_min": min(selected_counts) if selected_counts else 0,
            "selected_max": max(selected_counts) if selected_counts else 0,
            "degraded_count": len(degraded),
            "degraded_case_ids": [str(row.get("case_id") or "") for row in degraded],
            "query_models": sorted(
                {
                    str(row.get("query_embedding_model_id") or "").strip()
                    for row in rows
                    if str(row.get("query_embedding_model_id") or "").strip()
                }
            ),
        },
        "cases": rows,
    }


def review_surface_quote_case(
    case: QuoteRecallReviewCase,
    *,
    raw_top_k: int = 96,
    limit: int | None = None,
) -> dict[str, Any]:
    query = case.to_query(limit=limit)
    policy = surface_original_quote_policy_for_scene(query.scene_id)
    raw_rows = _raw_candidate_rows(query, top_k=max(1, int(raw_top_k or policy.candidate_top_k or 96)), enabled=policy.enabled)
    selection = select_surface_original_quotes(query, limit=int(query.limit or case.limit or 16))
    guidance = render_surface_original_quote_guidance(query, limit=int(query.limit or case.limit or 16))
    selected = [_quote_payload(quote) for quote in selection.quotes]
    return {
        "case_id": case.case_id,
        "user_message": case.user_message,
        "expected_note": case.expected_note,
        "query": _query_payload(query),
        "policy": {
            "scene_id": policy.scene_id,
            "mode": policy.mode,
            "max_quotes": policy.max_quotes,
            "candidate_top_k": policy.candidate_top_k,
            "enabled": policy.enabled,
            "note": policy.note,
        },
        "query_embedding_model_id": _first_query_model(raw_rows),
        "raw_candidate_count": len(raw_rows),
        "raw_candidates_preview": [_raw_row_payload(row) for row in raw_rows[:12]],
        "selected_count": len(selected),
        "selected_quotes": selected,
        "degraded": bool(selection.degraded),
        "reason": selection.reason,
        "guidance": guidance,
        "guidance_preview": "\n".join(guidance.splitlines()[:18]),
    }


def write_review_outputs(payload: Mapping[str, Any], *, out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "surface_quote_recall_review.json"
    jsonl_path = out_dir / "surface_quote_recall_review.jsonl"
    markdown_path = out_dir / "surface_quote_recall_review.md"
    json_path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with jsonl_path.open("w", encoding="utf-8", newline="\n") as file:
        for row in list(payload.get("cases") or []):
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    markdown_path.write_text(render_markdown_review(payload), encoding="utf-8")
    return {
        "json": str(json_path),
        "jsonl": str(jsonl_path),
        "markdown": str(markdown_path),
    }


def render_markdown_review(payload: Mapping[str, Any]) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), Mapping) else {}
    lines = [
        "# Surface Quote Recall Review",
        "",
        f"- generated_at: `{payload.get('generated_at') or ''}`",
        f"- case_count: `{payload.get('case_count') or 0}`",
        f"- selected_total: `{summary.get('selected_total') or 0}`",
        f"- degraded_count: `{summary.get('degraded_count') or 0}`",
        f"- query_models: `{', '.join(str(item) for item in summary.get('query_models') or [])}`",
        "",
        "| case | scene | user | policy | model | selected | degraded |",
        "|---|---|---|---|---|---:|---|",
    ]
    for row in list(payload.get("cases") or []):
        if not isinstance(row, Mapping):
            continue
        query = row.get("query") if isinstance(row.get("query"), Mapping) else {}
        policy = row.get("policy") if isinstance(row.get("policy"), Mapping) else {}
        lines.append(
            "| {case} | {scene} | {user} | {policy} | {model} | {selected} | {degraded} |".format(
                case=_md(row.get("case_id")),
                scene=_md(query.get("scene_id")),
                user=_md(row.get("user_message")),
                policy=_md(policy.get("mode")),
                model=_md(row.get("query_embedding_model_id")),
                selected=row.get("selected_count") or 0,
                degraded=_md(row.get("reason") if row.get("degraded") else ""),
            )
        )
    lines.append("")
    for row in list(payload.get("cases") or []):
        if not isinstance(row, Mapping):
            continue
        query = row.get("query") if isinstance(row.get("query"), Mapping) else {}
        lines.extend(
            [
                f"## {_md(row.get('case_id'))}",
                "",
                f"- user: `{row.get('user_message') or ''}`",
                f"- scene_id: `{query.get('scene_id') or ''}`",
                f"- relationship_stage: `{query.get('relationship_stage') or ''}`",
                f"- expected_note: {row.get('expected_note') or ''}",
                f"- raw_candidate_count: `{row.get('raw_candidate_count') or 0}`",
                f"- selected_count: `{row.get('selected_count') or 0}`",
                "",
                "| # | score | risk | text | tags | source |",
                "|---:|---:|---|---|---|---|",
            ]
        )
        for index, quote in enumerate(list(row.get("selected_quotes") or []), start=1):
            if not isinstance(quote, Mapping):
                continue
            tags = "/".join(
                [
                    *[str(item) for item in quote.get("intent_tags") or []],
                    *[str(item) for item in quote.get("style_moves") or []],
                ]
            )
            lines.append(
                "| {index} | {score} | {risk} | {text} | {tags} | {source} |".format(
                    index=index,
                    score=quote.get("vector_score"),
                    risk=_md(quote.get("risk_tier")),
                    text=_md(quote.get("text")),
                    tags=_md(tags),
                    source=_md(quote.get("source_ref")),
                )
            )
        lines.extend(["", "<details><summary>Prompt guidance preview</summary>", "", "```text"])
        lines.append(str(row.get("guidance_preview") or ""))
        lines.extend(["```", "", "</details>", ""])
    return "\n".join(lines).rstrip() + "\n"


def selected_cases(case_ids: Iterable[str] | None = None) -> tuple[QuoteRecallReviewCase, ...]:
    ids = tuple(item.strip() for item in (case_ids or ()) if str(item).strip())
    if not ids:
        return DEFAULT_REVIEW_CASES
    by_id = {case.case_id: case for case in DEFAULT_REVIEW_CASES}
    missing = [case_id for case_id in ids if case_id not in by_id]
    if missing:
        raise ValueError(f"unknown review case id(s): {', '.join(missing)}")
    return tuple(by_id[case_id] for case_id in ids)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit original-quote Chroma recall before surface generation.")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--case", action="append", default=[], help="Review case id. Can be repeated.")
    parser.add_argument("--cases", default="", help="Comma-separated review case ids.")
    parser.add_argument("--limit", type=int, default=0, help="Override selected quote limit per case.")
    parser.add_argument("--raw-top-k", type=int, default=96)
    parser.add_argument("--list-cases", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(list(argv) if argv is not None else None)
    if args.list_cases:
        for case in DEFAULT_REVIEW_CASES:
            print(f"{case.case_id}\t{case.scene_id}\t{case.user_message}")
        return 0
    case_ids = [*list(args.case or []), *[item.strip() for item in str(args.cases or "").split(",") if item.strip()]]
    payload = build_surface_quote_recall_review(
        selected_cases(case_ids),
        raw_top_k=max(1, int(args.raw_top_k or 96)),
        limit=(max(1, int(args.limit)) if int(args.limit or 0) > 0 else None),
    )
    out_dir = Path(args.out_dir) if args.out_dir else DEFAULT_OUT_ROOT / time.strftime("%Y%m%d_%H%M%S")
    outputs = write_review_outputs(payload, out_dir=out_dir)
    print(json.dumps({**outputs, **dict(payload.get("summary") or {}), "case_count": payload.get("case_count")}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _raw_candidate_rows(query: SurfaceOriginalQuoteQuery, *, top_k: int, enabled: bool) -> list[dict[str, Any]]:
    if not enabled:
        return []
    try:
        return quote_mod._query_surface_original_quote_collection(query, top_k=top_k)
    except Exception:
        return []


def _first_query_model(rows: Sequence[Mapping[str, Any]]) -> str:
    for row in rows:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
        raw = str(metadata.get("query_embedding_model_id") or metadata.get("embedding_model_id") or "").strip()
        if raw:
            return raw
    return ""


def _query_payload(query: SurfaceOriginalQuoteQuery) -> dict[str, Any]:
    return {
        "scene_id": query.scene_id,
        "user_message": query.user_message,
        "relationship_stage": query.relationship_stage,
        "intent_tags": list(query.intent_tags),
        "emotion_tags": list(query.emotion_tags),
        "style_moves": list(query.style_moves),
        "limit": query.limit,
    }


def _quote_payload(quote: SurfaceOriginalQuote) -> dict[str, Any]:
    return {
        "quote_id": quote.quote_id,
        "text": quote.prompt_text or quote.text,
        "source_ref": quote.source_ref,
        "source_digest": quote.source_digest,
        "source_score": quote.source_score,
        "confidence": quote.confidence,
        "character_confidence": quote.character_confidence,
        "vector_score": quote.vector_score,
        "risk_tier": quote.risk_tier,
        "intent_tags": list(quote.intent_tags),
        "emotion_tags": list(quote.emotion_tags),
        "style_moves": list(quote.style_moves),
        "rhythm_tags": list(quote.rhythm_tags),
        "relationship_stages": list(quote.relationship_stages),
    }


def _raw_row_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    quote = SurfaceOriginalQuote.from_vector_row(row)
    payload = _quote_payload(quote)
    metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
    payload["query_embedding_model_id"] = str(metadata.get("query_embedding_model_id") or "").strip()
    payload["collection"] = str(row.get("collection") or "").strip()
    return payload


def _md(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", "<br>")


if __name__ == "__main__":
    raise SystemExit(main())
