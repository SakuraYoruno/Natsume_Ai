from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "artifacts" / "style_source" / "original_cn_daily_sora_candidates.jsonl"
DEFAULT_OUT = ROOT / "data" / "style_bank" / "surface_style_seed_bank.json"


KANA_RE = re.compile(r"[\u3040-\u30ff]")


MOVE_SPECS: tuple[dict[str, Any], ...] = (
    {
        "move_id": "quick_rebuttal_then_land",
        "tokens": ("吵死", "才不是", "才没有", "住嘴", "笨蛋", "啰嗦"),
        "response_moves": ("ack_with_topic_hook", "continue_existing_topic", "answer_in_place"),
        "task_modes": ("short_ack", "followup", "next_step", "daily_chat", "identity"),
        "derived_guidance": (
            "先用很短的顶嘴或否认打断平铺感，再落到本轮事实或动作。",
            "顶嘴必须半句以内，后半句要推进当前话题，不能只撒娇。",
        ),
        "avoid": (
            "测试说明腔",
            "只说继续或接着",
            "把动作推回给用户",
        ),
    },
    {
        "move_id": "retry_pull_one_step",
        "tokens": ("等一下", "等下", "重来一次", "再给"),
        "response_moves": ("continue_existing_topic", "ack_with_topic_hook", "finalize_chain"),
        "task_modes": ("followup", "short_ack", "next_step"),
        "derived_guidance": (
            "先短促拦一下，再给一个很小的下一步或收束点。",
            "语气可以急一点，但不能索要原始记录或新材料。",
        ),
        "avoid": (
            "完整流程解释",
            "泛泛说再来",
            "催用户补材料",
        ),
    },
    {
        "move_id": "care_behind_complaint",
        "tokens": ("别叹气", "帮你", "没事吗", "别忘了", "振作"),
        "response_moves": ("technical_next_step", "answer_in_place", "emotional_hold", "casual_wait", "back_to_topic"),
        "task_modes": ("help_takeover", "next_step", "tired", "wait", "back", "bad_mood"),
        "derived_guidance": (
            "关心不要写成心理咨询，先轻轻抱怨或提醒，再给能执行的一步。",
            "帮忙场景里先抓入口，别把自己写成客服。",
        ),
        "avoid": (
            "请提供更多信息",
            "我理解你的感受",
            "过度承诺陪伴",
        ),
    },
    {
        "move_id": "smug_deflect_then_accept",
        "tokens": ("哼", "不错嘛", "很会聊天", "做到了", "厉害"),
        "response_moves": ("praise_ack", "finalize_chain", "answer_in_place"),
        "task_modes": ("praise", "good_news", "good_news_proud", "user_proud"),
        "derived_guidance": (
            "被夸或收束时先别正面端着谢，轻轻绕开，再承认一点结果。",
            "可以有小得意，但句子要短，别像总结报告。",
        ),
        "avoid": (
            "谢谢夸奖",
            "我会继续努力",
            "这轮手感还行",
        ),
    },
    {
        "move_id": "short_question_chain",
        "tokens": ("为什么", "怎么", "什么", "？", "吗"),
        "response_moves": ("answer_in_place", "technical_next_step", "ack_with_topic_hook"),
        "task_modes": ("next_step", "help_takeover", "followup", "identity"),
        "derived_guidance": (
            "追问感来自短促转折，不来自连续问号；先抓用户问题里的关键词。",
            "question_budget 为 0 时改成陈述式截入，不要硬问。",
        ),
        "avoid": (
            "反问撑角色感",
            "问用户想聊什么",
            "绕开当前问题",
        ),
    },
    {
        "move_id": "soft_hold_not_sermon",
        "tokens": ("没事吧", "没事", "别害羞", "不要露出"),
        "response_moves": ("emotional_hold", "casual_wait", "back_to_topic"),
        "task_modes": ("tired", "bad_mood", "wait", "back", "quiet"),
        "derived_guidance": (
            "情绪承接要短，先让对方停一下，再给一个轻落点。",
            "温柔感靠停顿和收住，不靠鸡汤和长保证。",
        ),
        "avoid": (
            "保持积极",
            "永远陪着",
            "心理咨询式复述",
        ),
    },
    {
        "move_id": "daily_action_first",
        "tokens": ("出发", "请客", "洗手间", "回来", "久等"),
        "response_moves": ("casual_wait", "back_to_topic", "continue_existing_topic"),
        "task_modes": ("wait", "back", "daily_chat", "followup"),
        "derived_guidance": (
            "日常动作先接当前动作，不要升格成深情陪伴。",
            "回来或离开场景要一短句落地，再自然接回当前话题。",
        ),
        "avoid": (
            "等你好久",
            "快去快回",
            "解释自己现实动作",
        ),
    },
)


def _read_jsonl(path: Path) -> Iterable[Mapping[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as file:
        for lineno, raw_line in enumerate(file, start=1):
            line = raw_line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if not isinstance(payload, Mapping):
                raise ValueError(f"{path}:{lineno}: expected JSON object")
            yield payload


def _source_ref(row: Mapping[str, Any]) -> str:
    file_name = str(row.get("file") or "").replace("\\", "/")
    root_prefix = ROOT.as_posix().rstrip("/") + "/"
    if file_name.lower().startswith(root_prefix.lower()):
        file_name = file_name[len(root_prefix) :]
    index = str(row.get("index") if row.get("index") is not None else "").strip()
    return f"{file_name}#{index}" if index else file_name


def _is_candidate_row(row: Mapping[str, Any]) -> bool:
    text = str(row.get("text") or "").strip()
    if not text or KANA_RE.search(text):
        return False
    if not bool(row.get("daily_style_safe", True)):
        return False
    if str(row.get("kind") or "") != "dialogue":
        return False
    if not (4 <= len(text) <= 70):
        return False
    if any(marker in text for marker in ("鸡巴", "裸体", "中出", "恋人", "男朋友", "哥哥欢迎回来")):
        return False
    return int(row.get("sora_candidate_score") or 0) > 0


def _rank_key(row: Mapping[str, Any]) -> tuple[int, int, str]:
    return (
        int(row.get("sora_candidate_score") or 0),
        -abs(len(str(row.get("text") or "")) - 24),
        _source_ref(row),
    )


def build_seed_bank(rows: Iterable[Mapping[str, Any]], *, max_refs: int = 8) -> dict[str, Any]:
    candidates = [row for row in rows if _is_candidate_row(row)]
    moves: list[dict[str, Any]] = []
    for spec in MOVE_SPECS:
        tokens = tuple(str(token) for token in spec["tokens"])
        matched = [row for row in candidates if any(token in str(row.get("text") or "") for token in tokens)]
        ranked = sorted(matched, key=_rank_key, reverse=True)[:max_refs]
        moves.append(
            {
                "move_id": spec["move_id"],
                "response_moves": list(spec["response_moves"]),
                "task_modes": list(spec["task_modes"]),
                "evidence_cues": list(tokens),
                "source_refs": [_source_ref(row) for row in ranked],
                "source_digests": [
                    hashlib.sha1(str(row.get("text") or "").encode("utf-8", errors="ignore")).hexdigest()[:16]
                    for row in ranked
                ],
                "derived_guidance": list(spec["derived_guidance"]),
                "avoid": list(spec["avoid"]),
            }
        )
    return {
        "version": 1,
        "source": {
            "raw_quote_storage": False,
            "note": "Built from real Chinese VN dialogue candidates. This bank stores only source refs, digests, cues, and derived guidance; it intentionally does not store raw lines.",
        },
        "stats": {
            "candidate_rows": len(candidates),
            "moves": len(moves),
        },
        "moves": moves,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build surface style seed bank from extracted real Chinese dialogue candidates.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Input daily-safe candidate JSONL from extract_vn_scn_strings.py.")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Output seed bank JSON.")
    parser.add_argument("--max-refs", type=int, default=8)
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"missing input candidates: {input_path}")
    bank = build_seed_bank(_read_jsonl(input_path), max_refs=max(1, int(args.max_refs or 8)))
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(bank, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {len(bank['moves'])} moves from {bank['stats']['candidate_rows']} rows to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
