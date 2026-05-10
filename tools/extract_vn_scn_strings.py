from __future__ import annotations

import argparse
import json
import re
import struct
import zlib
from contextlib import nullcontext
from pathlib import Path


def _is_text_char(ch: str) -> bool:
    if len(ch) != 1:
        return all(_is_text_char(part) for part in ch)
    cp = ord(ch)
    if ch in " \t、。，．！？!?…ー～〜・：:；;（）()「」『』《》〈〉[]【】—-／/♪♡❤☆★":
        return True
    if "0" <= ch <= "9" or "A" <= ch <= "Z" or "a" <= ch <= "z":
        return True
    return (
        0x3040 <= cp <= 0x30FF
        or 0x3400 <= cp <= 0x9FFF
        or 0xFF00 <= cp <= 0xFFEF
    )


def _looks_useful(text: str) -> bool:
    text = text.strip()
    if len(text) < 2:
        return False
    japanese_or_cjk = sum(
        1
        for ch in text
        if 0x3040 <= ord(ch) <= 0x30FF or 0x3400 <= ord(ch) <= 0x9FFF
    )
    if japanese_or_cjk < 2:
        return False
    noisy = sum(1 for ch in text if ch in "□�")
    return noisy == 0


CONTROL_RE = re.compile(r"%(?:D\$vl\d+|p-?\d*|f[^;]*);")
LINK_RE = re.compile(r"%l([^;]+);#[0-9a-fA-F]+;([^%]+)%l;#;")


def decode_scn_container(path: Path) -> bytes:
    data = path.read_bytes()
    if len(data) < 4:
        return data
    if data.startswith(b"mdf\x00") and len(data) >= 8:
        expected_size = struct.unpack_from("<I", data, 4)[0]
        try:
            decoded = zlib.decompress(data[8:])
        except zlib.error:
            return data
        if expected_size and len(decoded) != expected_size:
            # Some localized packs still decompress correctly even when their
            # size header is unreliable, so keep the payload instead of failing.
            return decoded
        return decoded
    if data.startswith(b"PSB\x00"):
        return data
    key = data[0] ^ ord("P")
    decoded = bytes(b ^ key for b in data)
    if decoded.startswith(b"PSB\x00"):
        return decoded
    return data


def _decode_utf8_parts(region: bytes) -> list[str]:
    texts: list[str] = []
    for part in region.split(b"\x00"):
        if not part:
            continue
        try:
            text = part.decode("utf-8")
        except UnicodeDecodeError:
            text = part.decode("utf-8", errors="replace")
        if "�" not in text:
            texts.append(text)
    return texts


def _string_score(texts: list[str]) -> int:
    score = 0
    for text in texts:
        if _looks_useful(text):
            score += 2
        if any("\u3040" <= ch <= "\u30ff" or "\u3400" <= ch <= "\u9fff" for ch in text):
            score += 1
        if text.startswith("「") or text.startswith("『"):
            score += 2
    return score


def extract_psb_string_table(data: bytes) -> tuple[list[str], dict[str, object]]:
    if not data.startswith(b"PSB\x00") or len(data) < 32:
        return [], {"format": "not_psb"}

    fields = struct.unpack_from("<10I", data, 4)
    string_start = fields[4]
    string_end = fields[5]
    if string_start >= string_end or string_end > len(data):
        return [], {"format": "psb", "error": "invalid_string_region", "fields": fields}

    raw_region = data[string_start:string_end]
    # 9-nine's PSB string table uses a per-file single-byte XOR. The first byte
    # normally encodes the leading NUL, so it is a reliable candidate key.
    candidate_keys = [0, raw_region[0], 0x08, 0x91, 0x96, 0x65]
    best_key = 0
    best_score = -1
    best_texts: list[str] = []
    key_scores: dict[int, int] = {}
    for key in dict.fromkeys(candidate_keys):
        region = raw_region if key == 0 else bytes(byte ^ key for byte in raw_region)
        texts = _decode_utf8_parts(region)
        score = _string_score(texts)
        key_scores[key] = score
        if score > best_score:
            best_key = key
            best_score = score
            best_texts = texts
    return best_texts, {
        "format": "psb",
        "fields": fields,
        "string_start": string_start,
        "string_end": string_end,
        "string_xor_key": best_key,
        "key_scores": key_scores,
    }


def clean_text(text: str) -> str:
    text = text.replace("\\n", " ")
    text = text.replace("\u3000", " ")
    text = LINK_RE.sub(lambda match: match.group(2), text)
    text = CONTROL_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def classify_text(text: str) -> str:
    if not _looks_useful(text):
        return "other"
    stripped = text.strip()
    if stripped.startswith(("「", "『")) and stripped.endswith(("」", "』")):
        return "dialogue"
    if stripped in {
        "天",
        "翔",
        "都",
        "沙月",
        "与一",
        "索菲缇雅",
        "希亚",
        "春风",
        "ソフィーティア",
        "希亜",
        "春風",
    }:
        return "name"
    if len(stripped) <= 12 and not any(ch in stripped for ch in "。、！？!?…"):
        return "label"
    return "narration"


def sora_candidate_score(text: str, kind: str) -> int:
    if kind != "dialogue":
        return 0
    score = 0
    strong_markers = [
        "にぃに",
        "にいやん",
        "あたし",
        "みゃーこ",
        "兄貴面",
        "小さくない",
        "おごり",
        "奢り",
        "ごちそうさま",
        "哥哥",
        "尼尼",
        "欧尼",
        "喵都",
        "请客",
        "请我",
        "不走",
        "我来啦",
        "人家",
    ]
    soft_markers = [
        "うっさい",
        "うっせ",
        "もっかい",
        "待ってって",
        "なんでっ",
        "やったらぁ",
        "怖い",
        "フフン",
        "うっそだよ",
        "気を使えない",
        "無視しないで",
        "吵死",
        "烦死",
        "才不是",
        "才没有",
        "笨蛋",
        "慢死",
        "过分",
        "小气",
        "哼",
        "嘛",
        "啦",
        "呀",
        "哇",
    ]
    for marker in strong_markers:
        if marker in text:
            score += 3
    for marker in soft_markers:
        if marker in text:
            score += 1
    if "天" in text and ("あたし" in text or "にぃに" in text):
        score += 1
    if "天" in text and ("哥哥" in text or "尼尼" in text or "欧尼" in text):
        score += 1
    return score


def is_daily_style_safe(text: str) -> bool:
    explicit_markers = [
        "チンコ",
        "ケツ",
        "処女",
        "性欲",
        "快楽",
        "気持ちいい",
        "気持ちよく",
        "興奮",
        "疼",
        "虜",
        "恋人",
        "付き合",
        "縁切",
        "イこ",
        "イク",
        "出して",
        "中に",
        "中で",
        "お兄ちゃんの中",
        "一緒に寝",
        "寝てる間",
        "してるところ",
        "ピクピク",
        "ふぁ",
        "はぁ",
        "ぁ……っ",
        "裸",
        "抱けば",
        "抱き",
        "抱く",
        "襲え",
        "襲おう",
        "襲っちゃう",
        "ヤリ",
        "もっこり",
        "エッチ",
        "おっぱい",
        "乳",
        "喘",
        "鸡巴",
        "屁股",
        "处女",
        "性欲",
        "快感",
        "舒服",
        "兴奋",
        "内射",
        "中出",
        "一起睡",
        "裸体",
        "裸",
        "抱我",
        "袭击",
        "色情",
        "色色",
        "乳",
        "胸",
        "喘",
    ]
    return not any(marker in text for marker in explicit_markers)


def extract_psb_strings(data: bytes) -> list[dict[str, object]]:
    strings, meta = extract_psb_string_table(data)
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for index, raw_text in enumerate(strings):
        text = clean_text(raw_text)
        if text in seen:
            continue
        seen.add(text)
        kind = classify_text(text)
        if kind == "other":
            continue
        rows.append(
            {
                "index": index,
                "text": text,
                "kind": kind,
                "sora_candidate_score": sora_candidate_score(text, kind),
                "daily_style_safe": is_daily_style_safe(text),
                "source_meta": meta,
            }
        )
    return rows


def extract_cp932_strings(data: bytes, min_chars: int = 3) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    i = 0
    n = len(data)
    while i < n:
        start = i
        chars: list[str] = []
        while i < n:
            b = data[i]
            if b in (0x00, 0x0A, 0x0D):
                break
            if b < 0x20:
                break
            consumed = 1
            try:
                if b <= 0x7E or 0xA1 <= b <= 0xDF:
                    ch = bytes([b]).decode("cp932")
                elif i + 1 < n:
                    ch = data[i : i + 2].decode("cp932")
                    consumed = 2
                else:
                    break
            except UnicodeDecodeError:
                break
            if not _is_text_char(ch):
                break
            chars.append(ch)
            i += consumed
        text = "".join(chars).strip()
        if len(text) >= min_chars and _looks_useful(text):
            results.append({"offset": start, "text": text})
        i = max(i + 1, start + 1)
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", action="append", required=True, help="Directory containing .scn files")
    parser.add_argument("--out", required=True, help="Output jsonl path")
    parser.add_argument("--sora-out", help="Output jsonl path for likely Niimi Sora dialogue")
    parser.add_argument("--daily-sora-out", help="Output jsonl path for daily-safe likely Niimi Sora dialogue")
    parser.add_argument("--summary", help="Output markdown summary path")
    parser.add_argument("--contains", action="append", default=[], help="Optional substring filter")
    parser.add_argument("--legacy-cp932-scan", action="store_true", help="Use noisy fallback CP932 scan")
    args = parser.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sora_path = Path(args.sora_out) if args.sora_out else None
    if sora_path:
        sora_path.parent.mkdir(parents=True, exist_ok=True)
    daily_sora_path = Path(args.daily_sora_out) if args.daily_sora_out else None
    if daily_sora_path:
        daily_sora_path.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    sora_count = 0
    source_stats: dict[str, dict[str, int]] = {}
    sora_rows: list[dict[str, object]] = []
    daily_sora_rows: list[dict[str, object]] = []
    daily_excluded_files = {"b18_2b.txt.scn", "b19_1.txt.scn"}
    sora_context = sora_path.open("w", encoding="utf-8") if sora_path else nullcontext(None)
    daily_sora_context = daily_sora_path.open("w", encoding="utf-8") if daily_sora_path else nullcontext(None)
    with out_path.open("w", encoding="utf-8") as fh, sora_context as sfh, daily_sora_context as dsfh:
        for input_value in args.input:
            input_dir = Path(input_value)
            for path in sorted(input_dir.rglob("*.scn")):
                decoded = decode_scn_container(path)
                if args.legacy_cp932_scan:
                    items = extract_cp932_strings(decoded)
                else:
                    items = extract_psb_strings(decoded)
                for item in items:
                    text = str(item["text"])
                    kind = str(item.get("kind", "unknown"))
                    stat = source_stats.setdefault(str(path), {})
                    stat[kind] = stat.get(kind, 0) + 1
                    if args.contains and not any(s in text for s in args.contains):
                        continue
                    row = {
                        "file": str(path),
                        "index": item.get("index", item.get("offset")),
                        "kind": item.get("kind", "legacy"),
                        "sora_candidate_score": item.get("sora_candidate_score", 0),
                        "daily_style_safe": item.get("daily_style_safe", True),
                        "text": text,
                    }
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                    count += 1
                    if sfh is not None and int(row["sora_candidate_score"]) > 0:
                        sfh.write(json.dumps(row, ensure_ascii=False) + "\n")
                        sora_rows.append(row)
                        sora_count += 1
                    if (
                        dsfh is not None
                        and int(row["sora_candidate_score"]) > 0
                        and bool(row.get("daily_style_safe", True))
                        and path.name not in daily_excluded_files
                    ):
                        dsfh.write(json.dumps(row, ensure_ascii=False) + "\n")
                        daily_sora_rows.append(row)
    if args.summary:
        summary_path = Path(args.summary)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_rows = daily_sora_rows if daily_sora_rows else sora_rows
        top_rows = sorted(
            summary_rows,
            key=lambda row: (int(row["sora_candidate_score"]), len(str(row["text"]))),
            reverse=True,
        )[:80]
        lines = [
            "# 新海天风格候选提取摘要",
            "",
            f"- 全量文本行数: {count}",
            f"- 新海天候选行数: {sora_count}",
            f"- 日常风格候选行数: {len(daily_sora_rows)}",
            f"- 输入文件数: {len(source_stats)}",
            "",
            "## 风格观察",
            "",
            "- 高频气质不是温顺，而是“顶嘴、黏人、撒娇、行动快、很会把哥哥架住”。",
            "- 对哥哥常用短促反击和连续追问，不喜欢平铺直叙解释动机。",
            "- 句尾常带夸张口语节奏：拉长音、重复、突然转折、先怼后贴近。",
            "- 害怕或被戳穿时会慌，但慌张通常会被嘴硬和吐槽盖过去。",
            "",
            "## 高置信候选行",
            "",
        ]
        for row in top_rows:
            lines.append(
                f"- `{Path(str(row['file'])).name}#{row['index']}` score={row['sora_candidate_score']}: {row['text']}"
            )
        summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {count} rows to {out_path}")
    if sora_path:
        print(f"wrote {sora_count} sora candidate rows to {sora_path}")
    if daily_sora_path:
        print(f"wrote {len(daily_sora_rows)} daily sora candidate rows to {daily_sora_path}")
    if args.summary:
        print(f"wrote summary to {args.summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
