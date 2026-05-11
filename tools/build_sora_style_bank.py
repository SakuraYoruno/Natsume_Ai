from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.persona.style_reference_bank import (  # noqa: E402
    DEFAULT_STYLE_BANK_PATH,
    StyleReference,
    style_reference_from_raw_row,
)


DEFAULT_INPUTS = (
    ROOT / "artifacts" / "vn_sora_refresh" / "sora_daily_candidates.jsonl",
    ROOT / "artifacts" / "vn_sora" / "skycity_ep2_20260509_161713" / "sora_daily_candidates.jsonl",
    ROOT / "artifacts" / "vn_sora" / "nine_newEP_20260509_161027" / "sora_daily_candidates.jsonl",
)


def _display_input_path(path: Path) -> str:
    normalized = path.as_posix()
    root_prefix = ROOT.as_posix().rstrip("/") + "/"
    if normalized.lower().startswith(root_prefix.lower()):
        return normalized[len(root_prefix) :]
    return normalized


def _read_jsonl(path: Path) -> Iterable[Mapping[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as fh:
        for lineno, raw_line in enumerate(fh, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno}: invalid JSONL row") from exc
            if isinstance(payload, Mapping):
                yield payload


def build_style_references(paths: Iterable[Path]) -> list[StyleReference]:
    refs_by_id: dict[str, StyleReference] = {}
    for path in paths:
        if not path.exists():
            continue
        lang_hint = "zh" if "chinese" in path.name.lower() else ""
        for row in _read_jsonl(path):
            ref = style_reference_from_raw_row(row, lang_hint=lang_hint)
            if ref is None:
                continue
            current = refs_by_id.get(ref.ref_id)
            if current is None or ref.confidence > current.confidence:
                refs_by_id[ref.ref_id] = ref
    return sorted(
        refs_by_id.values(),
        key=lambda ref: (ref.lang, -ref.confidence, ref.source_ref, ref.ref_id),
    )


def write_jsonl(path: Path, refs: Iterable[StyleReference]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for ref in refs:
            fh.write(json.dumps(ref.as_dict(), ensure_ascii=False, sort_keys=True) + "\n")


def write_summary(path: Path, refs: list[StyleReference], *, inputs: list[Path]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lang_counts = Counter(ref.lang for ref in refs)
    intent_counts = Counter(tag for ref in refs for tag in ref.intent_tags)
    emotion_counts = Counter(tag for ref in refs for tag in ref.emotion_tags)
    move_counts = Counter(tag for ref in refs for tag in ref.style_moves)
    lines = [
        "# Sora StyleReferenceBank Summary",
        "",
        "This bank stores derived style features only. It intentionally does not duplicate raw VN dialogue text.",
        "",
        "## Inputs",
        "",
    ]
    lines.extend(f"- `{_display_input_path(path)}`" for path in inputs)
    lines.extend(
        [
            "",
            "## Counts",
            "",
            f"- total: {len(refs)}",
        ]
    )
    for lang, count in sorted(lang_counts.items()):
        lines.append(f"- {lang}: {count}")
    lines.extend(["", "## Top Intent Tags", ""])
    lines.extend(f"- {tag}: {count}" for tag, count in intent_counts.most_common(12))
    lines.extend(["", "## Top Emotion Tags", ""])
    lines.extend(f"- {tag}: {count}" for tag, count in emotion_counts.most_common(12))
    lines.extend(["", "## Top Style Moves", ""])
    lines.extend(f"- {tag}: {count}" for tag, count in move_counts.most_common(16))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build derived Sora StyleReferenceBank from extracted VN candidates.")
    parser.add_argument("--input", action="append", help="Input extracted candidate JSONL. Can be repeated.")
    parser.add_argument("--out", default=str(DEFAULT_STYLE_BANK_PATH), help="Output derived style bank JSONL.")
    parser.add_argument("--summary", default="", help="Optional markdown summary path.")
    args = parser.parse_args()

    inputs = [Path(item) for item in args.input] if args.input else list(DEFAULT_INPUTS)
    refs = build_style_references(inputs)
    out_path = Path(args.out)
    write_jsonl(out_path, refs)
    summary_path = Path(args.summary) if args.summary else out_path.with_suffix(".summary.md")
    write_summary(summary_path, refs, inputs=inputs)
    print(f"wrote {len(refs)} derived style references to {out_path}")
    print(f"wrote summary to {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
