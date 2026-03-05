"""Extract Natsume-Ai speaker lines from CN/JP corpora and upsert Style Canon.

Design goals:
- Read paired `.ast` files from translated_cn/raw_jp roots.
- Extract only target speaker utterances (default: 钃?钘?.
- Keep raw cleaned lines as artifacts for audit/review.
- Distill corpus statistics into tone-only style instructions.
- Upsert into StyleCanonStore (never write raw plot text as canon body).
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import sys
import time
from typing import Any

# Allow standalone execution from repository root.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.canon_vector_projection import (
    load_env_file,
    sync_canon_vector_index,
)
from host_brain.memory.canon_style_store import StyleCanonStore


BLOCK_START_RE = re.compile(r"(?m)^\s*block_(\d+)\s*=\s*\{")
JA_ASSIGN_RE = re.compile(r"ja\s*=\s*\{")
NAME_RE = re.compile(r'name\s*=\s*\{"([^"]+)"\}')
STRING_LITERAL_RE = re.compile(r'"((?:\\.|[^"\\])*)"')
CONTROL_TOKEN_RE = re.compile(r"^(?:rt\d+|[A-Za-z_][A-Za-z0-9_]*)$")
CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
PUNCT_RE = re.compile(r"[。！？?.!…]")
ENDING_PUNCT_RE = re.compile(r"[。！？?.!…\s]+$")

ENDING_PARTICLES_CN = ("呢", "啊", "吧", "呀", "哦", "嘛", "呐", "喔", "哇", "啦")
ENDING_PARTICLES_JP = ("ね", "よ", "な", "か", "わ", "ぞ", "さ", "ぜ", "よね", "かな")


@dataclass(frozen=True)
class DialogueEntry:
    speaker: str
    text: str


@dataclass(frozen=True)
class SpeakerLine:
    file_rel_path: str
    block_id: str
    line_index: int
    speaker_cn: str
    cn_text: str
    speaker_jp: str
    jp_text: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract Natsume speaker lines from corpus and upsert Style Canon."
    )
    parser.add_argument("--cn-dir", default="", help="CN corpus root (translated_cn).")
    parser.add_argument("--jp-dir", default="", help="JP corpus root (raw_jp).")
    parser.add_argument(
        "--data-root",
        default=r"D:\acg",
        help="Search root when cn/jp dir not explicitly provided.",
    )
    parser.add_argument("--file-glob", default="*.ast", help="Corpus file glob.")
    parser.add_argument(
        "--speaker-keywords",
        default="蓝,夏目蓝,夏目",
        help="Comma-separated target speaker name keywords.",
    )
    parser.add_argument(
        "--include-mixed-speakers",
        action="store_true",
        help="Include entries like '闈欐祦&钃?. Default is exact match only.",
    )
    parser.add_argument("--min-line-len", type=int, default=2, help="Minimum cleaned line length.")
    parser.add_argument("--max-files", type=int, default=0, help="Limit paired files (0=no limit).")
    parser.add_argument("--max-lines", type=int, default=0, help="Limit extracted lines (0=no limit).")
    parser.add_argument(
        "--output-dir",
        default="runtime/style_canon",
        help="Artifact output dir for cleaned lines/profile.",
    )
    parser.add_argument("--canon-id", default="natsume_ai_cn", help="Style canon id.")
    parser.add_argument(
        "--style-language",
        choices=("cn", "jp", "bilingual"),
        default="cn",
        help="Distill style statistics from cn/jp/bilingual lines.",
    )
    parser.add_argument(
        "--tags",
        default="natsume_ai,sakura_no_koku,style_canon,tone_only,derived_from_corpus",
        help="Comma-separated style canon tags.",
    )
    parser.add_argument("--proposal-id", default="", help="Optional fixed proposal id.")
    parser.add_argument(
        "--style-root",
        default="host_brain/canon/style",
        help="Style canon store root.",
    )
    parser.add_argument(
        "--audit-db",
        default="host_brain/data/audit.sqlite3",
        help="SQLite audit DB path for canon logs.",
    )
    parser.add_argument(
        "--upsert-style-canon",
        action="store_true",
        help="Write distilled style into StyleCanonStore.",
    )
    parser.add_argument(
        "--sync-canon-vector-index",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="After upsert, sync style/world canon projection into Chroma collection.",
    )
    parser.add_argument(
        "--sync-env-file",
        default=".env",
        help="Env profile file used by canon vector sync.",
    )
    parser.add_argument(
        "--sync-backend",
        choices=("http", "persistent"),
        default="http",
        help="Chroma backend mode for canon vector sync.",
    )
    parser.add_argument(
        "--sync-chroma-path",
        default="runtime/chroma/data",
        help="Used when --sync-backend=persistent.",
    )
    parser.add_argument(
        "--sync-chroma-endpoint",
        default="",
        help="Optional CHROMA_ENDPOINT override for canon vector sync.",
    )
    parser.add_argument(
        "--sync-collection",
        default="canon",
        help="Target Chroma collection name for canon projection.",
    )
    parser.add_argument(
        "--sync-collection-dim",
        type=int,
        default=0,
        help="Optional vector dim override for canon projection. 0 means auto-resolve.",
    )
    parser.add_argument(
        "--sync-world-root",
        default="host_brain/canon/world",
        help="World canon root used during projection sync.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Parse and distill without store writes.")
    return parser.parse_args()


def parse_keywords(raw: str) -> tuple[str, ...]:
    values = [item.strip() for item in str(raw).split(",")]
    return tuple(item for item in values if item)


def discover_corpus_roots(*, data_root: Path) -> tuple[Path, Path]:
    cn_dirs: list[Path] = []
    jp_dirs: list[Path] = []
    for item in data_root.rglob("*"):
        if not item.is_dir():
            continue
        lower = item.name.lower()
        if lower == "translated_cn":
            cn_dirs.append(item)
        elif lower == "raw_jp":
            jp_dirs.append(item)
    if len(cn_dirs) != 1 or len(jp_dirs) != 1:
        raise RuntimeError(
            f"expected exactly one translated_cn/raw_jp under {data_root}, "
            f"got translated_cn={len(cn_dirs)} raw_jp={len(jp_dirs)}"
        )
    return cn_dirs[0], jp_dirs[0]


def resolve_roots(args: argparse.Namespace) -> tuple[Path, Path]:
    if args.cn_dir and args.jp_dir:
        cn_root = Path(args.cn_dir).resolve()
        jp_root = Path(args.jp_dir).resolve()
    else:
        cn_root, jp_root = discover_corpus_roots(data_root=Path(args.data_root).resolve())
    if not cn_root.exists() or not cn_root.is_dir():
        raise FileNotFoundError(f"CN directory not found: {cn_root}")
    if not jp_root.exists() or not jp_root.is_dir():
        raise FileNotFoundError(f"JP directory not found: {jp_root}")
    return cn_root, jp_root


def collect_files(root: Path, file_glob: str) -> dict[str, Path]:
    mapping: dict[str, Path] = {}
    for path in root.rglob(file_glob):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix().lower()
        mapping[rel] = path
    return mapping


def read_text(path: Path) -> str:
    for enc in ("utf-8", "utf-8-sig", "cp932", "gbk"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def find_matching_brace(text: str, start_brace: int) -> int:
    if start_brace < 0 or start_brace >= len(text) or text[start_brace] != "{":
        return -1
    depth = 0
    in_string = False
    escaped = False
    for idx in range(start_brace, len(text)):
        ch = text[idx]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth += 1
            continue
        if ch == "}":
            depth -= 1
            if depth == 0:
                return idx
    return -1


def extract_blocks(text: str) -> dict[str, str]:
    blocks: dict[str, str] = {}
    cursor = 0
    while True:
        match = BLOCK_START_RE.search(text, cursor)
        if match is None:
            break
        block_id = f"block_{match.group(1)}"
        brace_start = text.find("{", match.start())
        brace_end = find_matching_brace(text, brace_start)
        if brace_start < 0 or brace_end < 0:
            break
        blocks[block_id] = text[brace_start : brace_end + 1]
        cursor = brace_end + 1
    return blocks


def extract_ja_sections(block_text: str) -> list[str]:
    sections: list[str] = []
    cursor = 0
    while True:
        match = JA_ASSIGN_RE.search(block_text, cursor)
        if match is None:
            break
        brace_start = block_text.find("{", match.end() - 1)
        brace_end = find_matching_brace(block_text, brace_start)
        if brace_start < 0 or brace_end < 0:
            break
        sections.append(block_text[brace_start : brace_end + 1])
        cursor = brace_end + 1
    return sections


def decode_string_literal(raw_value: str) -> str:
    try:
        return json.loads(f'"{raw_value}"')
    except Exception:
        return (
            raw_value.replace('\\"', '"')
            .replace("\\n", "\n")
            .replace("\\r", "\r")
            .replace("\\t", "\t")
        )


def normalize_speaker(name: str) -> str:
    return (
        str(name)
        .replace("\u3000", "")
        .replace(" ", "")
        .replace("＆", "&")
        .replace("，", ",")
        .strip()
    )


def normalize_line(text: str) -> str:
    value = str(text).strip()
    value = value.strip("「」『』“”\"")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def keep_dialogue_line(text: str, *, min_len: int) -> bool:
    value = normalize_line(text)
    if not value:
        return False
    if CONTROL_TOKEN_RE.fullmatch(value):
        return False
    if len(value) < max(1, int(min_len)):
        return False
    has_cjk = CJK_RE.search(value) is not None
    has_punct = PUNCT_RE.search(value) is not None
    if not has_cjk and not has_punct:
        return False
    if re.fullmatch(r"[。！？?.!…、，,]+", value):
        return False
    return True


def split_top_level_objects(container_text: str) -> list[str]:
    # container_text is expected to be a `{ ... }` block.
    if not container_text or container_text[0] != "{":
        return []
    items: list[str] = []
    idx = 1
    end = len(container_text) - 1
    while idx < end:
        ch = container_text[idx]
        if ch != "{":
            idx += 1
            continue
        obj_end = find_matching_brace(container_text, idx)
        if obj_end < 0:
            break
        items.append(container_text[idx : obj_end + 1])
        idx = obj_end + 1
    return items


def parse_dialogue_entries(block_text: str, *, min_len: int) -> list[DialogueEntry]:
    entries: list[DialogueEntry] = []
    for ja_section in extract_ja_sections(block_text):
        for raw_item in split_top_level_objects(ja_section):
            name_match = NAME_RE.search(raw_item)
            if name_match is None:
                continue
            speaker = normalize_speaker(name_match.group(1))
            if not speaker:
                continue
            fragments = [decode_string_literal(raw) for raw in STRING_LITERAL_RE.findall(raw_item)]
            parts: list[str] = []
            for frag in fragments:
                cleaned = normalize_line(frag)
                if not keep_dialogue_line(cleaned, min_len=min_len):
                    continue
                parts.append(cleaned)
            if not parts:
                continue
            text = normalize_line("".join(parts))
            if not keep_dialogue_line(text, min_len=min_len):
                continue
            entries.append(DialogueEntry(speaker=speaker, text=text))
    return entries


def matches_target_speaker(
    speaker: str,
    *,
    keywords: tuple[str, ...],
    include_mixed: bool,
) -> bool:
    normalized = normalize_speaker(speaker)
    if not normalized:
        return False
    if include_mixed:
        return any(keyword in normalized for keyword in keywords)
    return normalized in set(keywords)


def block_sort_key(block_id: str) -> tuple[int, str]:
    try:
        return int(block_id.split("_", 1)[1]), block_id
    except Exception:
        return 0, block_id


def extract_target_lines_for_file(
    *,
    rel_path: str,
    cn_text: str,
    jp_text: str,
    keywords: tuple[str, ...],
    include_mixed: bool,
    min_len: int,
) -> tuple[list[SpeakerLine], int]:
    cn_blocks = extract_blocks(cn_text)
    jp_blocks = extract_blocks(jp_text)
    shared_block_ids = sorted(set(cn_blocks.keys()) & set(jp_blocks.keys()), key=block_sort_key)

    collected: list[SpeakerLine] = []
    for block_id in shared_block_ids:
        cn_entries = parse_dialogue_entries(cn_blocks[block_id], min_len=min_len)
        jp_entries = parse_dialogue_entries(jp_blocks[block_id], min_len=min_len)

        cn_target = [
            item
            for item in cn_entries
            if matches_target_speaker(
                item.speaker,
                keywords=keywords,
                include_mixed=include_mixed,
            )
        ]
        jp_target = [
            item
            for item in jp_entries
            if matches_target_speaker(
                item.speaker,
                keywords=keywords,
                include_mixed=include_mixed,
            )
        ]
        if not jp_target and cn_target:
            # CN names are localized; when JP speaker names do not match keywords,
            # fallback to positional alignment within the block.
            jp_target = list(jp_entries)
        for idx, cn_item in enumerate(cn_target):
            jp_item = jp_target[idx] if idx < len(jp_target) else None
            collected.append(
                SpeakerLine(
                    file_rel_path=rel_path,
                    block_id=block_id,
                    line_index=idx,
                    speaker_cn=cn_item.speaker,
                    cn_text=cn_item.text,
                    speaker_jp=jp_item.speaker if jp_item is not None else "",
                    jp_text=jp_item.text if jp_item is not None else "",
                )
            )
    return collected, len(shared_block_ids)


def percentile(values: list[int], p: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    rank = int(round((len(ordered) - 1) * float(p)))
    rank = max(0, min(rank, len(ordered) - 1))
    return int(ordered[rank])


def _strip_end_punct(text: str) -> str:
    return ENDING_PUNCT_RE.sub("", str(text).strip())


def _extract_ending_particle(text: str, *, style_language: str) -> str:
    core = _strip_end_punct(text)
    if not core:
        return ""
    particles = ENDING_PARTICLES_JP if str(style_language).strip().lower() == "jp" else ENDING_PARTICLES_CN
    for token in sorted(particles, key=len, reverse=True):
        if core.endswith(token):
            return token
    return ""


def _ratio(part: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return float(part) / float(total)


def _top_keys(counter: Counter[str], k: int = 3) -> list[str]:
    return [name for name, _ in counter.most_common(k)]


def build_style_profile(lines: list[str], *, style_language: str) -> dict[str, Any]:
    total = len(lines)
    lengths = [len(re.sub(r"\s+", "", item)) for item in lines]

    particle_counter: Counter[str] = Counter()
    final_punct_counter: Counter[str] = Counter()
    for text in lines:
        particle = _extract_ending_particle(text, style_language=style_language)
        if particle:
            particle_counter[particle] += 1
        stripped = str(text).strip()
        if stripped:
            last_char = stripped[-1]
            if last_char in "。！？?!…":
                final_punct_counter[last_char] += 1

    question_count = sum(1 for item in lines if ("？" in item or "?" in item))
    exclaim_count = sum(1 for item in lines if ("！" in item or "!" in item))
    ellipsis_count = sum(1 for item in lines if ("…" in item or "..." in item))

    return {
        "style_language": str(style_language).strip().lower(),
        "line_count": total,
        "length": {
            "min": int(min(lengths)) if lengths else 0,
            "max": int(max(lengths)) if lengths else 0,
            "avg": float(round(sum(lengths) / total, 2)) if total else 0.0,
            "p25": percentile(lengths, 0.25),
            "p50": percentile(lengths, 0.50),
            "p75": percentile(lengths, 0.75),
            "p90": percentile(lengths, 0.90),
        },
        "question_ratio": round(_ratio(question_count, total), 4),
        "exclaim_ratio": round(_ratio(exclaim_count, total), 4),
        "ellipsis_ratio": round(_ratio(ellipsis_count, total), 4),
        "ending_particles_top": _top_keys(particle_counter, k=5),
        "final_punct_top": _top_keys(final_punct_counter, k=5),
    }


def render_style_instruction(profile: dict[str, Any], *, style_language: str) -> str:
    line_count = int(profile.get("line_count", 0))
    length = dict(profile.get("length", {}))
    p25 = int(length.get("p25", 0))
    p75 = int(length.get("p75", 0))
    p90 = int(length.get("p90", 0))
    avg = float(length.get("avg", 0.0))

    q_pct = int(round(float(profile.get("question_ratio", 0.0)) * 100))
    e_pct = int(round(float(profile.get("exclaim_ratio", 0.0)) * 100))
    ellipsis_pct = int(round(float(profile.get("ellipsis_ratio", 0.0)) * 100))

    normalized_language = str(style_language).strip().lower()
    if normalized_language == "jp":
        particles = list(profile.get("ending_particles_top", [])) or ["ね", "よ", "か"]
        puncts = list(profile.get("final_punct_top", [])) or ["。", "？", "！"]
        language_scope = "日语原文"
    elif normalized_language == "bilingual":
        particles = list(profile.get("ending_particles_top", [])) or ["ね", "よ", "吧"]
        puncts = list(profile.get("final_punct_top", [])) or ["。", "？", "！"]
        language_scope = "中日双语平行语料"
    else:
        particles = list(profile.get("ending_particles_top", [])) or ["呢", "啊", "吧"]
        puncts = list(profile.get("final_punct_top", [])) or ["。", "？", "！"]
        language_scope = "中文台词语料"

    particles_text = " / ".join(particles[:3])
    punct_text = " / ".join(puncts[:3])

    return (
        "你是夏目蓝（NatsumeAi）的风格模板（tone_only，不作为事实依据）。\n"
        f"语料来源：{language_scope}，已清洗台词 {line_count} 条。\n"
        "表达规则：\n"
        f"- 句长：常用区间约 {p25}-{p75} 字，超过 {p90} 优先拆句；均值约 {avg:.2f}。\n"
        "- 语气：克制、温和、先接住情绪再给建议。\n"
        f"- 疑问句比例约 {q_pct}%，用轻追问推进对话，避免连续压迫式提问。\n"
        f"- 句末标点偏好：{punct_text}；感叹句比例约 {e_pct}%。\n"
        f"- 停顿（省略号）比例约 {ellipsis_pct}%，可少量使用“……”制造呼吸感。\n"
        f"- 句尾语气偏好：{particles_text}，每句最多一个语气词。\n"
        "- 输出默认精简，不复述剧情细节。\n"
        "边界约束：\n"
        "- 不输出系统动作结果的断言语句。\n"
        "- 不逐字复现原作台词，只复用语气、节奏、句法倾向。\n"
    )


def write_artifacts(
    *,
    output_dir: Path,
    canon_id: str,
    records: list[SpeakerLine],
    profile: dict[str, Any],
    style_instruction: str,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    lines_path = output_dir / f"{canon_id}_speaker_lines.jsonl"
    profile_path = output_dir / f"{canon_id}_style_profile.json"

    with lines_path.open("w", encoding="utf-8") as fp:
        for row in records:
            fp.write(
                json.dumps(
                    {
                        "file_rel_path": row.file_rel_path,
                        "block_id": row.block_id,
                        "line_index": row.line_index,
                        "speaker_cn": row.speaker_cn,
                        "cn_text": row.cn_text,
                        "speaker_jp": row.speaker_jp,
                        "jp_text": row.jp_text,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    profile_payload = {
        "canon_id": canon_id,
        "profile": profile,
        "style_instruction": style_instruction,
    }
    profile_path.write_text(
        json.dumps(profile_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return lines_path, profile_path


def _style_line_for_mode(row: SpeakerLine, *, style_language: str) -> str:
    mode = str(style_language).strip().lower()
    cn_text = str(row.cn_text or "").strip()
    jp_text = str(row.jp_text or "").strip()
    if mode == "jp":
        return jp_text or cn_text
    if mode == "bilingual":
        if jp_text and cn_text:
            return f"{jp_text}\n{cn_text}"
        return jp_text or cn_text
    return cn_text or jp_text


def _dedupe_key_for_mode(row: SpeakerLine, *, style_language: str) -> str:
    return _style_line_for_mode(row, style_language=style_language).strip()


def main() -> int:
    args = parse_args()
    keywords = parse_keywords(args.speaker_keywords)
    if not keywords:
        print("[error] speaker_keywords cannot be empty", file=sys.stderr)
        return 2

    try:
        cn_root, jp_root = resolve_roots(args)
    except Exception as exc:
        print(f"[error] resolve roots failed: {exc}", file=sys.stderr)
        return 2

    cn_files = collect_files(cn_root, args.file_glob)
    jp_files = collect_files(jp_root, args.file_glob)
    shared = sorted(set(cn_files.keys()) & set(jp_files.keys()))
    if args.max_files > 0:
        shared = shared[: int(args.max_files)]
    if not shared:
        print("[error] no paired files found", file=sys.stderr)
        return 3

    all_lines: list[SpeakerLine] = []
    processed_files = 0
    processed_blocks = 0
    for rel in shared:
        processed_files += 1
        cn_text = read_text(cn_files[rel])
        jp_text = read_text(jp_files[rel])
        rows, block_count = extract_target_lines_for_file(
            rel_path=rel,
            cn_text=cn_text,
            jp_text=jp_text,
            keywords=keywords,
            include_mixed=bool(args.include_mixed_speakers),
            min_len=int(args.min_line_len),
        )
        processed_blocks += int(block_count)
        all_lines.extend(rows)
        if args.max_lines > 0 and len(all_lines) >= int(args.max_lines):
            all_lines = all_lines[: int(args.max_lines)]
            break

        if processed_files % 25 == 0:
            print(
                json.dumps(
                    {
                        "progress_files": processed_files,
                        "progress_lines": len(all_lines),
                    },
                    ensure_ascii=False,
                )
            )

    if not all_lines:
        print("[error] no target speaker lines extracted", file=sys.stderr)
        return 4

    style_language = str(args.style_language).strip().lower()
    # Deduplicate by the selected style-language view (cn/jp/bilingual).
    deduped: list[SpeakerLine] = []
    seen: set[str] = set()
    for row in all_lines:
        key = _dedupe_key_for_mode(row, style_language=style_language)
        if not key:
            continue
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)

    style_texts = [
        _style_line_for_mode(row, style_language=style_language)
        for row in deduped
    ]
    style_texts = [text for text in style_texts if text.strip()]
    if not style_texts:
        print("[error] no style texts after language filtering", file=sys.stderr)
        return 4

    profile = build_style_profile(style_texts, style_language=style_language)
    style_instruction = render_style_instruction(profile, style_language=style_language)

    output_dir = Path(args.output_dir).resolve()
    lines_path, profile_path = write_artifacts(
        output_dir=output_dir,
        canon_id=str(args.canon_id),
        records=deduped,
        profile=profile,
        style_instruction=style_instruction,
    )

    summary: dict[str, Any] = {
        "cn_root": str(cn_root),
        "jp_root": str(jp_root),
        "paired_files": len(shared),
        "processed_files": processed_files,
        "processed_blocks": processed_blocks,
        "extracted_lines_raw": len(all_lines),
        "extracted_lines_deduped": len(deduped),
        "style_language": style_language,
        "style_line_count": len(style_texts),
        "output_lines_path": str(lines_path),
        "output_profile_path": str(profile_path),
        "canon_id": str(args.canon_id),
        "dry_run": bool(args.dry_run),
    }
    print(json.dumps(summary, ensure_ascii=False))

    if args.upsert_style_canon and not args.dry_run:
        proposal_id = str(args.proposal_id).strip() or f"proposal_style_{args.canon_id}_{int(time.time() * 1000)}"
        tags = [item.strip() for item in str(args.tags).split(",") if item.strip()]

        ledger = SQLiteLedger(db_path=Path(args.audit_db).resolve())
        try:
            store = StyleCanonStore(style_root=Path(args.style_root).resolve(), ledger=ledger)
            entry = store.upsert(
                canon_id=str(args.canon_id),
                content=style_instruction,
                tags=tags,
                proposal_id=proposal_id,
            )
        finally:
            ledger.close()
        print(
            json.dumps(
                {
                    "upserted": True,
                    "canon_id": entry.canon_id,
                    "version": entry.version,
                    "hash": entry.hash,
                    "proposal_id": proposal_id,
                    "style_path": str(Path(args.style_root).resolve() / f"{entry.canon_id}.json"),
                },
                ensure_ascii=False,
            )
        )

        if args.sync_canon_vector_index:
            env_values = load_env_file(Path(args.sync_env_file).resolve()) if args.sync_env_file else {}
            sync_overrides: dict[str, Any] = {}
            if str(args.sync_chroma_endpoint).strip():
                sync_overrides["CHROMA_ENDPOINT"] = str(args.sync_chroma_endpoint).strip()
            try:
                sync_summary = sync_canon_vector_index(
                    style_root=Path(args.style_root).resolve(),
                    world_root=Path(args.sync_world_root).resolve(),
                    collection=str(args.sync_collection),
                    backend=str(args.sync_backend),
                    chroma_path=Path(args.sync_chroma_path).resolve(),
                    collection_dim=(
                        int(args.sync_collection_dim) if int(args.sync_collection_dim) > 0 else None
                    ),
                    include_style=True,
                    include_world=True,
                    env=env_values,
                    local_overrides=sync_overrides,
                )
            except Exception as exc:
                print(f"[error] canon vector sync failed: {exc}", file=sys.stderr)
                return 5
            print(
                json.dumps(
                    {
                        "canon_vector_sync": sync_summary,
                    },
                    ensure_ascii=False,
                )
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

