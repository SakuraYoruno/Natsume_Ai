"""运行 StyleDistiller 蒸馏并生成 StyleCanon v5。

用法：在仓库根目录运行
  node run_style_distill.js  (不需要 Python，因为我们直接用 Node 调用)

或直接：
  从 host_brain.retrieval.style_distiller 导入运行
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 确保仓库根目录在 sys.path
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from host_brain.retrieval.style_distiller import (
    distill_style_profile,
    render_style_instruction_v5,
    render_style_instruction_v5_ja,
)
from host_brain.retrieval.emotion_utterance_index import (
    EmotionTaggedUtteranceIndex,
)


def main() -> int:
    cn_path = REPO_ROOT / "runtime" / "style_canon" / "natsume_ai_cn_speaker_lines.jsonl"
    ja_path = REPO_ROOT / "runtime" / "style_canon" / "natsume_ai_ja_speaker_lines.jsonl"

    if not cn_path.exists() and not ja_path.exists():
        print(f"[error] 台词文件不存在", file=sys.stderr)
        return 1

    print("[1/4] 蒸馏风格画像...")
    profile = distill_style_profile(cn_path, ja_path)

    print(f"  CN: {profile.total_cn} 条, JA: {profile.total_ja} 条")
    print(f"  句式模板: {len(profile.sentence_patterns)} 种")
    print(f"  情感模式: {len(profile.emotion_patterns)} 种")
    print(f"  高频词(CN): {profile.high_freq_words_cn[:10]}")
    print(f"  口癖(CN): {profile.signature_phrases_cn[:10]}")
    print(f"  节奏: {profile.rhythm_description}")
    print(f"  短/中/长: {profile.short_ratio:.1%} / {profile.medium_ratio:.1%} / {profile.long_ratio:.1%}")

    # 输出 JSON
    output_dir = REPO_ROOT / "runtime" / "style_canon"
    output_dir.mkdir(parents=True, exist_ok=True)

    profile_path = output_dir / "style_profile_v5.json"
    profile_path.write_text(
        json.dumps(profile.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"\n[2/4] JSON → {profile_path}")

    # 输出风格指导文本（中文版）
    instruction = render_style_instruction_v5(profile)
    instruction_path = output_dir / "style_instruction_v5.txt"
    instruction_path.write_text(instruction, encoding="utf-8")
    print(f"[3/5] Instruction (CN) → {instruction_path}")

    # 输出风格指导文本（日语版）
    instruction_ja = render_style_instruction_v5_ja(profile)
    instruction_ja_path = output_dir / "style_instruction_v5_ja.txt"
    instruction_ja_path.write_text(instruction_ja, encoding="utf-8")
    print(f"[4/5] Instruction (JA) → {instruction_ja_path}")

    # 构建台词索引并输出摘要
    print("\n[5/5] 构建情感台词索引...")
    index = EmotionTaggedUtteranceIndex(cn_path=cn_path, ja_path=ja_path)
    index.build()
    summary = index.to_summary()
    print(f"  总台词: {summary['total_lines']} 条")
    print(f"  场景分布: {json.dumps(summary['scene_counts'], ensure_ascii=False)}")

    # 测试检索
    for tag in ["HAPPY", "SAD", "NEUTRAL", "TSUNDERE"]:
        result = index.retrieve(tag, lang="cn", n=3)
        texts = [s.cn_text for s in result.samples]
        print(f"\n  [{tag}] → {result.matched_scene} ({result.total_in_scene}条)")
        for t in texts:
            print(f"    「{t}」")

    print(f"\n[完成] 风格指导文本预览:\n{'='*60}")
    print(instruction[:1000])
    print(f"{'='*60}\n...（共 {len(instruction)} 字）")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
