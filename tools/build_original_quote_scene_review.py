from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.persona.surface_original_quote_bank import to_prompt_style_text  # noqa: E402
from host_brain.persona.surface_scene_quality_pack import surface_scene_quality_pack_payload  # noqa: E402


DEFAULT_QUOTE_BANK = ROOT / "data" / "style_bank" / "surface_original_quote_bank.jsonl"
DEFAULT_CANDIDATE_SOURCES = (
    ROOT / "artifacts" / "style_source" / "original_cn_sora_candidates.jsonl",
)
DEFAULT_OUT_ROOT = ROOT / "artifacts" / "surface_scene_quality" / "original_quote_review"

KANA_RE = re.compile(r"[\u3040-\u30ff]")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
PUNCTUATION_NORMALIZE_RE = re.compile(r"[\s，。！？!?、,.；;：:\"'“”‘’「」『』（）()\[\]{}~～…]+")
GLOBAL_POLLUTION_MARKERS = (
    "老师",
    "学校",
    "教室",
    "作业",
    "社团",
    "班长",
    "学生",
    "考试",
    "清洁公司",
    "传唤",
    "NINE BALL",
    "金拱门",
    "意大利面",
    "沐浴露",
    "洗澡",
    "春风",
    "石化",
    "爸妈",
    "爸爸",
    "妈妈",
    "父亲",
    "母亲",
    "同一个房间",
    "睡一个房间",
    "过夜",
    "小天",
    "放学",
    "购物",
    "使用者",
    "战斗",
    "伙伴",
    "感冒",
    "周六",
    "消失",
    "唯一的哥哥",
    "帮你洗",
    "洗个",
    "洗过澡",
    "材料费",
)
GLOBAL_UNSAFE_MARKERS = (
    "裸体",
    "鸡巴",
    "内裤",
    "做爱",
    "中出",
    "色情",
    "射出来",
    "满满的",
    "我的里面",
    "做那种事",
    "亲热",
    "要死",
)

SCENE_SOURCE_PROFILES: dict[str, dict[str, Any]] = {
    "technical_next_step": {
        "intent_tags": ("help",),
        "emotion_tags": ("care",),
        "style_moves": ("take_charge", "care_behind_tease"),
        "keywords": ("帮", "调查", "资料", "怎么办", "重来", "机会", "别叹气", "先"),
        "avoid": ("老师", "学校", "教室", "作业", "传唤"),
        "transfer_hint": "优先看接手感：先顶一下，再给能做的一步。",
    },
    "short_continue": {
        "intent_tags": ("daily",),
        "style_moves": ("quick_reaction", "question_chain"),
        "keywords": ("等一下", "重来", "再给", "继续", "接下来", "那就", "行吧", "好吧"),
        "avoid": ("老师", "学校", "主链", "报错"),
        "require_keyword": True,
        "transfer_hint": "优先看短促截入和话口推进，不要扩写成流程句。",
    },
    "explicit_memory": {
        "intent_tags": ("daily",),
        "style_moves": ("quick_reaction", "soft_reassurance"),
        "keywords": ("记得", "忘", "记忆", "想起来", "知道", "当然"),
        "avoid": ("回忆杀", "完整记录", "过去全部"),
        "require_keyword": True,
        "transfer_hint": "只学低压确认，不学编造完整过去。",
    },
    "daily_morning": {
        "intent_tags": ("daily",),
        "style_moves": ("quick_reaction",),
        "keywords": ("早安", "早上", "早啊", "早呀", "早哦", "早。", "早！", "早～"),
        "avoid": ("学校", "老师", "考试"),
        "require_keyword": True,
        "transfer_hint": "只取早安接话节奏，不补作息和精神状态。",
    },
    "tired": {
        "intent_tags": ("reassurance",),
        "emotion_tags": ("soft", "care"),
        "style_moves": ("soft_reassurance", "care_behind_tease"),
        "keywords": ("累", "困", "辛苦", "叹气", "没事吧", "休息", "缓", "硬撑"),
        "avoid": ("病", "医院", "学校"),
        "require_keyword": True,
        "transfer_hint": "先降压，少问原因，保留一点嘴硬照看。",
    },
    "relationship_memory_reassurance": {
        "intent_tags": ("daily", "reassurance"),
        "emotion_tags": ("soft",),
        "style_moves": ("soft_reassurance", "quick_reaction"),
        "keywords": ("记得", "忘", "不记得", "知道", "别瞎想", "没事"),
        "avoid": ("完整", "记录", "证据"),
        "require_keyword": True,
        "transfer_hint": "确认关系和记得即可，不证明、不翻旧账。",
    },
    "negative_feedback": {
        "intent_tags": ("annoyed", "daily"),
        "emotion_tags": ("annoyed", "playful"),
        "style_moves": ("quick_rebuttal", "familiar_pushback", "quick_reaction"),
        "keywords": ("吵死", "不是啦", "你烦", "好烦", "烦啦", "烦死", "重来", "什么嘛", "没办法", "笨蛋", "恶趣味"),
        "avoid": ("客服", "优化", "流程"),
        "transfer_hint": "看顶嘴后的转身，别写成检讨或客服修正。",
    },
    "return_chat": {
        "intent_tags": ("daily", "presence"),
        "style_moves": ("quick_reaction", "pull_attention"),
        "keywords": ("回来", "欢迎回来", "我回来了"),
        "avoid": ("回去路上", "地点", "学校"),
        "require_keyword": True,
        "transfer_hint": "只学回来时的短承接，不编线下地点。",
    },
    "lover_presence": {
        "intent_tags": ("presence", "intimacy"),
        "emotion_tags": ("deep_affection", "playful"),
        "style_moves": ("clingy_presence", "pull_attention", "direct_affection"),
        "keywords": ("想你", "想见", "看见", "叫你", "哥哥", "人家", "在", "喜欢"),
        "avoid": ("地点", "学校", "老师"),
        "transfer_hint": "看亲密显影和撒娇，不要搬具体剧情。",
    },
    "lover_low_energy": {
        "intent_tags": ("reassurance", "intimacy", "help"),
        "emotion_tags": ("soft", "care", "deep_affection"),
        "style_moves": ("soft_reassurance", "direct_affection", "care_behind_tease"),
        "keywords": ("没事吧", "累", "照顾", "抱", "想你", "休息", "硬撑"),
        "avoid": ("医院", "病", "看病"),
        "require_keyword": True,
        "transfer_hint": "恋人感要短、具体、照看落地。",
    },
    "ambiguous_affection": {
        "intent_tags": ("intimacy", "praise"),
        "emotion_tags": ("deep_affection", "shy", "playful"),
        "style_moves": ("direct_affection", "shy_deflection", "smug_tease"),
        "keywords": ("喜欢", "想你", "可爱", "害羞", "抱抱", "哥哥", "人家"),
        "avoid": ("永远", "结婚", "孩子"),
        "transfer_hint": "暧昧只取轻试探和别扭，不直接写稳定恋人承诺。",
    },
    "close_friend_care": {
        "intent_tags": ("help", "reassurance"),
        "emotion_tags": ("care", "soft"),
        "style_moves": ("take_charge", "care_behind_tease", "soft_reassurance"),
        "keywords": ("帮", "照顾", "管你", "别叹气", "没事", "辛苦", "放心"),
        "avoid": ("恋人", "男朋友", "女朋友"),
        "transfer_hint": "朋友护短感优先，别升成恋人甜话。",
    },
    "low_intensity_sibling_distance": {
        "intent_tags": ("daily", "annoyed"),
        "emotion_tags": ("playful", "annoyed"),
        "style_moves": ("quick_reaction", "familiar_pushback", "smug_tease"),
        "keywords": ("哥哥", "老哥", "人家", "本小姐", "妹妹", "笨蛋", "嘛", "啦"),
        "avoid": ("恋人", "情侣", "亲热"),
        "transfer_hint": "保留兄妹熟人感：能顶嘴、能撒娇，但不恋人化。",
    },
    "pushed_away_boundary": {
        "intent_tags": ("boundary", "help", "annoyed"),
        "emotion_tags": ("hurt", "wounded", "care", "annoyed"),
        "style_moves": ("wounded_pushback", "soft_after_sting", "care_behind_tease", "quick_rebuttal"),
        "keywords": ("管你", "谁稀罕", "别管", "不管", "自己", "听见没", "随便你"),
        "avoid": ("永远陪", "心理", "咨询"),
        "require_keyword": True,
        "transfer_hint": "核心是先顶回去，再收住边界，最后留一个叫人的出口。",
    },
    "jealousy_low_pressure": {
        "intent_tags": ("intimacy", "annoyed", "daily"),
        "emotion_tags": ("playful", "shy", "annoyed"),
        "style_moves": ("smug_tease", "familiar_pushback", "direct_affection"),
        "keywords": ("吃醋", "嫉妒", "男朋友", "女朋友", "情侣", "关系", "得意"),
        "avoid": ("占有", "不准", "永远"),
        "require_keyword": True,
        "transfer_hint": "吃醋只能低压显影，别写成占有或审问。",
    },
}


@dataclass(frozen=True)
class SourceQuote:
    text: str
    source_ref: str
    source_kind: str
    source_score: int = 0
    confidence: float = 0.0
    character_confidence: float = 0.0
    intent_tags: tuple[str, ...] = field(default_factory=tuple)
    emotion_tags: tuple[str, ...] = field(default_factory=tuple)
    style_moves: tuple[str, ...] = field(default_factory=tuple)
    rhythm_tags: tuple[str, ...] = field(default_factory=tuple)
    lexical_cues: tuple[str, ...] = field(default_factory=tuple)
    relationship_stages: tuple[str, ...] = field(default_factory=tuple)


def _clean_text(value: Any) -> str:
    text = str(value or "").replace("\u3000", " ").strip()
    text = re.sub(r"\s+", "", text)
    while len(text) >= 2 and text[0] in "「『“\"" and text[-1] in "」』”\"":
        text = text[1:-1].strip()
    return text


def _clean_list(values: Iterable[Any]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item for item in (_clean_text(value) for value in values) if item))


def _norm_key(text: str) -> str:
    return PUNCTUATION_NORMALIZE_RE.sub("", str(text or "")).strip().lower()


def _has_cjk(text: str) -> bool:
    return bool(CJK_RE.search(text))


def _source_ref(row: Mapping[str, Any]) -> str:
    raw = str(row.get("source_ref") or "").strip()
    if raw:
        return raw
    file_name = str(row.get("file") or "").replace("\\", "/")
    index = str(row.get("index") if row.get("index") is not None else "").strip()
    return f"{file_name}#{index}" if index else file_name


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


def _quote_from_bank_row(row: Mapping[str, Any], *, source_kind: str) -> SourceQuote | None:
    text = to_prompt_style_text(_clean_text(row.get("prompt_text") or row.get("text")))
    if not _usable_quote_text(text):
        return None
    return SourceQuote(
        text=text,
        source_ref=_source_ref(row),
        source_kind=source_kind,
        source_score=int(row.get("source_score") or row.get("sora_candidate_score") or 0),
        confidence=float(row.get("confidence") or 0.0),
        character_confidence=float(row.get("character_confidence") or 0.0),
        intent_tags=_clean_list(row.get("intent_tags") or ()),
        emotion_tags=_clean_list(row.get("emotion_tags") or ()),
        style_moves=_clean_list(row.get("style_moves") or ()),
        rhythm_tags=_clean_list(row.get("rhythm_tags") or ()),
        lexical_cues=_clean_list(row.get("lexical_cues") or ()),
        relationship_stages=_clean_list(row.get("relationship_stages") or ()),
    )


def _quote_from_candidate_row(row: Mapping[str, Any], *, source_kind: str) -> SourceQuote | None:
    if str(row.get("kind") or "") != "dialogue":
        return None
    if not bool(row.get("daily_style_safe", True)):
        return None
    text = to_prompt_style_text(_clean_text(row.get("text")))
    if not _usable_quote_text(text):
        return None
    return SourceQuote(
        text=text,
        source_ref=_source_ref(row),
        source_kind=source_kind,
        source_score=int(row.get("sora_candidate_score") or 0),
    )


def _usable_quote_text(text: str) -> bool:
    if not text or not _has_cjk(text):
        return False
    if KANA_RE.search(text):
        return False
    length = len(text)
    if length < 3 or length > 62:
        return False
    if any(marker in text for marker in GLOBAL_UNSAFE_MARKERS):
        return False
    if any(marker in text for marker in GLOBAL_POLLUTION_MARKERS):
        return False
    return True


def load_source_quotes(
    *,
    quote_bank: Path = DEFAULT_QUOTE_BANK,
    candidate_sources: Sequence[Path] = DEFAULT_CANDIDATE_SOURCES,
) -> list[SourceQuote]:
    quotes: list[SourceQuote] = []
    if quote_bank.exists():
        for row in _read_jsonl(quote_bank):
            quote = _quote_from_bank_row(row, source_kind="surface_original_quote_bank")
            if quote:
                quotes.append(quote)
    for path in candidate_sources:
        if not path.exists():
            continue
        for row in _read_jsonl(path):
            quote = _quote_from_candidate_row(row, source_kind=path.name)
            if quote:
                quotes.append(quote)
    return _dedupe_quotes(quotes)


def _dedupe_quotes(quotes: Sequence[SourceQuote]) -> list[SourceQuote]:
    best: dict[str, SourceQuote] = {}
    for quote in quotes:
        key = _norm_key(quote.text)
        existing = best.get(key)
        if existing is None or _source_rank(quote) > _source_rank(existing):
            best[key] = quote
    return list(best.values())


def _source_rank(quote: SourceQuote) -> tuple[float, int, int]:
    bank_bonus = 1.0 if quote.source_kind == "surface_original_quote_bank" else 0.0
    return (bank_bonus + float(quote.confidence or 0.0), int(quote.source_score or 0), -abs(len(quote.text) - 18))


def _matches(values: Sequence[str], expected: Sequence[str]) -> tuple[str, ...]:
    expected_set = set(expected)
    return tuple(item for item in values if item in expected_set)


def _keyword_hits(text: str, keywords: Sequence[str]) -> tuple[str, ...]:
    return tuple(keyword for keyword in keywords if keyword and keyword in text)


def _scene_candidate_score(quote: SourceQuote, profile: Mapping[str, Any]) -> tuple[float, tuple[str, ...], tuple[str, ...]]:
    intent_hits = _matches(quote.intent_tags, tuple(profile.get("intent_tags") or ()))
    emotion_hits = _matches(quote.emotion_tags, tuple(profile.get("emotion_tags") or ()))
    move_hits = _matches(quote.style_moves, tuple(profile.get("style_moves") or ()))
    keyword_hits = _keyword_hits(quote.text, tuple(profile.get("keywords") or ()))
    avoid_hits = _keyword_hits(quote.text, tuple(profile.get("avoid") or ()))
    matched_tags = (*intent_hits, *emotion_hits, *move_hits)
    non_generic_tag_hits = tuple(
        tag
        for tag in matched_tags
        if tag not in {"daily", "neutral", "quick_reaction"}
    )
    if bool(profile.get("require_keyword")) and not keyword_hits:
        return -999.0, tuple(dict.fromkeys(matched_tags)), keyword_hits
    if not keyword_hits and not non_generic_tag_hits:
        return -999.0, tuple(dict.fromkeys(matched_tags)), keyword_hits
    score = 0.0
    score += float(quote.confidence or 0.0) * 24.0
    score += float(quote.character_confidence or 0.0) * 8.0
    score += min(int(quote.source_score or 0), 7) * 3.5
    score += len(intent_hits) * 8.0
    score += len(emotion_hits) * 6.5
    score += len(move_hits) * 6.0
    score += len(keyword_hits) * 5.0
    score += 4.0 if any(marker in quote.text for marker in ("哼", "哈？", "什么嘛", "人家", "老哥", "哥哥")) else 0.0
    score += 2.5 if any(punct in quote.text for punct in ("！", "？", "……", "～")) else 0.0
    score -= len(avoid_hits) * 7.0
    score -= max(0, len(quote.text) - 38) * 0.35
    return score, tuple(dict.fromkeys(matched_tags)), keyword_hits


def build_original_quote_scene_review(
    quotes: Sequence[SourceQuote],
    *,
    scene_ids: Sequence[str] | None = None,
    max_per_scene: int = 12,
    min_score: float = 18.0,
) -> dict[str, Any]:
    pack_meta = _scene_pack_meta()
    selected_scene_ids = tuple(scene_ids or SCENE_SOURCE_PROFILES.keys())
    scenes: list[dict[str, Any]] = []
    for scene_id in selected_scene_ids:
        profile = SCENE_SOURCE_PROFILES.get(scene_id)
        if not profile:
            continue
        candidates: list[dict[str, Any]] = []
        for quote in quotes:
            score, matched_tags, matched_keywords = _scene_candidate_score(quote, profile)
            if score < min_score:
                continue
            candidates.append(
                {
                    "text": quote.text,
                    "scene_score": round(score, 3),
                    "source_ref": quote.source_ref,
                    "source_kind": quote.source_kind,
                    "source_score": int(quote.source_score or 0),
                    "confidence": round(float(quote.confidence or 0.0), 4),
                    "matched_tags": list(matched_tags),
                    "matched_keywords": list(matched_keywords),
                    "intent_tags": list(quote.intent_tags),
                    "emotion_tags": list(quote.emotion_tags),
                    "style_moves": list(quote.style_moves),
                    "relationship_stages": list(quote.relationship_stages),
                    "transfer_hint": str(profile.get("transfer_hint") or ""),
                }
            )
        ranked = sorted(
            _dedupe_candidate_dicts(candidates),
            key=lambda item: (
                float(item.get("scene_score") or 0.0),
                int(item.get("source_score") or 0),
                -abs(len(str(item.get("text") or "")) - 18),
                str(item.get("text") or ""),
            ),
            reverse=True,
        )[: max(1, int(max_per_scene or 12))]
        meta = pack_meta.get(scene_id, {})
        scenes.append(
            {
                "scene_id": scene_id,
                "title": meta.get("title") or scene_id,
                "user_markers": list(meta.get("user_markers") or ()),
                "required_move": meta.get("required_move") or "",
                "scoring_focus": list(meta.get("scoring_focus") or ()),
                "good_examples": [],
                "bad_examples": [],
                "should_not_surface": [],
                "candidate_good_examples": ranked,
                "candidate_bad_examples": [],
                "source_note": "候选来自原台词抽取；只把人工认可并改到当前角色口吻的文本移动到 good_examples/bad_examples/should_not_surface。",
            }
        )
    return {
        "schema_version": 1,
        "sample_kind": "surface_scene_quality_review_template",
        "source_kind": "original_quote_scene_harvest",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "instructions": [
            "逐场景审 candidate_good_examples。",
            "不要整批接受；只把认可的句子或你改写后的句子复制到 good_examples。",
            "明显会带偏当前角色或语境的句子复制到 bad_examples 或 should_not_surface。",
            "candidate_* 字段不会被 merge 自动晋升。",
        ],
        "source_summary": {
            "quote_count": len(quotes),
            "scene_count": len(scenes),
            "max_per_scene": int(max_per_scene),
            "min_score": float(min_score),
        },
        "scenes": scenes,
    }


def _dedupe_candidate_dicts(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for raw in candidates:
        item = dict(raw)
        key = _norm_key(str(item.get("text") or ""))
        if not key:
            continue
        existing = best.get(key)
        if existing is None or float(item.get("scene_score") or 0.0) > float(existing.get("scene_score") or 0.0):
            best[key] = item
    return list(best.values())


def _scene_pack_meta() -> dict[str, dict[str, Any]]:
    payload = surface_scene_quality_pack_payload()
    out: dict[str, dict[str, Any]] = {}
    for scene in list(payload.get("scenes") or ()):
        if not isinstance(scene, Mapping):
            continue
        scene_id = str(scene.get("scene_id") or "").strip()
        if scene_id:
            out[scene_id] = dict(scene)
    return out


def render_markdown_review(payload: Mapping[str, Any]) -> str:
    lines = [
        "# 原台词场景样本人工评审",
        "",
        "说明：这里只是候选池，不会自动进入主链。认可的句子请复制或改写到对应 scene 的 `good_examples`。",
        "",
        f"- source_kind: `{payload.get('source_kind')}`",
        f"- quote_count: `{(payload.get('source_summary') or {}).get('quote_count')}`",
        f"- scene_count: `{(payload.get('source_summary') or {}).get('scene_count')}`",
        "",
    ]
    for scene in list(payload.get("scenes") or ()):
        if not isinstance(scene, Mapping):
            continue
        lines.extend(
            [
                f"## {scene.get('scene_id')} - {scene.get('title')}",
                "",
                f"- required_move: {scene.get('required_move') or ''}",
                f"- user_markers: {', '.join(str(item) for item in scene.get('user_markers') or [])}",
                "",
                "| # | score | text | matched | source |",
                "|---:|---:|---|---|---|",
            ]
        )
        for index, candidate in enumerate(list(scene.get("candidate_good_examples") or ()), start=1):
            if not isinstance(candidate, Mapping):
                continue
            matched = "/".join(
                [
                    *[str(item) for item in candidate.get("matched_tags") or []],
                    *[str(item) for item in candidate.get("matched_keywords") or []],
                ]
            )
            lines.append(
                "| {index} | {score} | {text} | {matched} | {source} |".format(
                    index=index,
                    score=candidate.get("scene_score"),
                    text=_markdown_cell(candidate.get("text")),
                    matched=_markdown_cell(matched),
                    source=_markdown_cell(candidate.get("source_ref")),
                )
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _markdown_cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", "<br>")


def write_review_outputs(payload: Mapping[str, Any], *, out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    template_path = out_dir / "surface_scene_original_quote_review_template.json"
    markdown_path = out_dir / "surface_scene_original_quote_review.md"
    template_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown_review(payload), encoding="utf-8")
    return {"template": str(template_path), "markdown": str(markdown_path)}


def _scene_ids_arg(raw: str) -> tuple[str, ...] | None:
    items = tuple(item.strip() for item in str(raw or "").split(",") if item.strip())
    return items or None


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build manual review pack for scene samples mined from original Chinese quotes.")
    parser.add_argument("--quote-bank", default=str(DEFAULT_QUOTE_BANK))
    parser.add_argument("--candidate-source", action="append", default=[])
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--max-per-scene", type=int, default=12)
    parser.add_argument("--min-score", type=float, default=18.0)
    parser.add_argument("--scenes", default="", help="Comma-separated scene ids. Defaults to all built-in scene profiles.")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    candidate_sources = [Path(item) for item in args.candidate_source] or list(DEFAULT_CANDIDATE_SOURCES)
    quotes = load_source_quotes(quote_bank=Path(args.quote_bank), candidate_sources=candidate_sources)
    payload = build_original_quote_scene_review(
        quotes,
        scene_ids=_scene_ids_arg(args.scenes),
        max_per_scene=int(args.max_per_scene),
        min_score=float(args.min_score),
    )
    out_dir = Path(args.out_dir) if args.out_dir else DEFAULT_OUT_ROOT / time.strftime("%Y%m%d_%H%M%S")
    outputs = write_review_outputs(payload, out_dir=out_dir)
    result = {
        **outputs,
        "quote_count": payload["source_summary"]["quote_count"],
        "scene_count": payload["source_summary"]["scene_count"],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
