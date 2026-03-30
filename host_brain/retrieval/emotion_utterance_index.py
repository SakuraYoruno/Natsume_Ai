"""EmotionTaggedUtteranceIndex — 按情感场景检索台词。

替代原有的 embedding 语义相似度检索方式。
核心思路：台词不是用来"找语义相似的回复"，而是用来"提供说话风格参考"。

工作方式：
  1. 离线阶段：从 JSONL 加载台词 → 用关键词给每条打情感标签
  2. 运行时：传入当前 emotion_tag → 按标签匹配 → 随机取 N 条同场景台词
  3. 注入 prompt：作为 few-shot 风格参考，不经过 Reranker

优势：
  - 不依赖 embedding / ChromaDB
  - 不经过 Reranker 竞争（台词走独立通道）
  - 按情感场景匹配比语义匹配更合理
  - 每次随机取样，LLM 看到的参考台词有变化性
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from host_brain.retrieval.style_distiller import (
    DEFAULT_CN_LINES_PATH,
    DEFAULT_JA_LINES_PATH,
    UtteranceLine,
    load_utterance_lines,
    tag_all_lines,
)

# ---------------------------------------------------------------------------
# 默认配置
# ---------------------------------------------------------------------------
DEFAULT_SAMPLE_SIZE = 5          # 每次取多少条参考台词
DEFAULT_MAX_TOKEN_BUDGET = 400   # 台词参考的 token 预算（约 200 中文字）

# emotion_tag → 对应的场景映射
# 当运行时的 emotion_tag 不完全匹配时，按相似度回退
EMOTION_FALLBACK_MAP: dict[str, list[str]] = {
    "HAPPY": ["happy", "daily"],
    "SAD": ["sadness", "comfort", "daily"],
    "ANGRY": ["anger", "daily"],
    "FEAR": ["comfort", "sadness", "daily"],
    "SURPRISE": ["surprise", "happy", "daily"],
    "DISGUST": ["anger", "daily"],
    "NEUTRAL": ["daily", "comfort"],
    "COMFORT": ["comfort", "sadness", "daily"],
    "LOVE": ["affection", "happy", "daily"],
    "EXCITEMENT": ["happy", "surprise", "daily"],
    "NOSTALGIA": ["sadness", "affection", "daily"],
    "GRATITUDE": ["happy", "affection", "daily"],
    "LONELINESS": ["sadness", "comfort", "daily"],
    "CALM": ["daily", "comfort"],
    "ANXIETY": ["comfort", "sadness", "daily"],
    "TSUNDERE": ["tsundere", "daily"],
}


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class UtteranceSample:
    """一条台词样本。"""
    cn_text: str
    jp_text: str
    emotion_tag: str
    speaker: str = "夏目藍"


@dataclass
class UtteranceRetrievalResult:
    """台词检索结果。"""
    samples: list[UtteranceSample] = field(default_factory=list)
    query_emotion: str = ""
    matched_scene: str = ""
    total_in_scene: int = 0
    fallback_used: bool = False


# ---------------------------------------------------------------------------
# 索引类
# ---------------------------------------------------------------------------
class EmotionTaggedUtteranceIndex:
    """按情感标签索引的台词库。

    使用方式：
        index = EmotionTaggedUtteranceIndex()
        index.build()  # 或 build_from_lines(lines)
        result = index.retrieve("HAPPY", lang="cn", n=5)
    """

    def __init__(
        self,
        cn_path: str | Path = DEFAULT_CN_LINES_PATH,
        ja_path: str | Path = DEFAULT_JA_LINES_PATH,
    ) -> None:
        self._cn_path = str(cn_path)
        self._ja_path = str(ja_path)
        self._index: dict[str, list[UtteranceLine]] = {}
        self._all_lines: list[UtteranceLine] = []
        self._built = False

    @property
    def is_built(self) -> bool:
        return self._built

    @property
    def total_lines(self) -> int:
        return len(self._all_lines)

    @property
    def scene_counts(self) -> dict[str, int]:
        return {scene: len(lines) for scene, lines in self._index.items()}

    def build(self) -> None:
        """从 JSONL 文件构建索引。"""
        lines = load_utterance_lines(self._cn_path, self._ja_path)
        self.build_from_lines(lines)

    def build_from_lines(self, lines: list[UtteranceLine]) -> None:
        """从已加载的台词列表构建索引。"""
        tagged = tag_all_lines(lines)
        self._all_lines = tagged
        self._index.clear()

        for line in tagged:
            self._index.setdefault(line.emotion_tag, []).append(line)

        self._built = True

    def retrieve(
        self,
        emotion_tag: str,
        *,
        lang: str = "cn",
        n: int = DEFAULT_SAMPLE_SIZE,
        max_chars: int = DEFAULT_MAX_TOKEN_BUDGET,
    ) -> UtteranceRetrievalResult:
        """按情感标签检索台词样本。

        Args:
            emotion_tag: 当前情感标签（如 HAPPY, SAD, NEUTRAL 等）
            lang: 目标语言 cn/ja
            n: 最大返回条数
            max_chars: 最大字符数预算

        Returns:
            UtteranceRetrievalResult
        """
        if not self._built:
            return UtteranceRetrievalResult(query_emotion=emotion_tag)

        normalized_tag = str(emotion_tag or "NEUTRAL").strip().upper()

        # 查找匹配的场景
        fallback_chain = EMOTION_FALLBACK_MAP.get(normalized_tag, ["daily"])
        matched_scene = ""
        candidates: list[UtteranceLine] = []
        fallback_used = False

        for scene in fallback_chain:
            scene_lines = self._index.get(scene, [])
            if scene_lines:
                candidates = scene_lines
                matched_scene = scene
                fallback_used = scene != fallback_chain[0]
                break

        if not candidates:
            # 最终兜底：从所有台词中随机取
            candidates = self._all_lines
            matched_scene = "all"
            fallback_used = True

        # 按语言过滤（确保目标语言有内容）
        if lang == "ja":
            filtered = [l for l in candidates if l.jp_text]
        else:
            filtered = [l for l in candidates if l.cn_text]

        if not filtered:
            filtered = candidates

        # 随机采样
        sample_size = min(n, len(filtered))
        sampled = random.sample(filtered, sample_size) if sample_size > 0 else []

        # 按字符预算截断
        result_samples: list[UtteranceSample] = []
        char_count = 0
        for line in sampled:
            text = line.jp_text if lang == "ja" else line.cn_text
            if not text:
                text = line.cn_text or line.jp_text
            char_count += len(text)
            if char_count > max_chars and result_samples:
                break
            result_samples.append(UtteranceSample(
                cn_text=line.cn_text,
                jp_text=line.jp_text,
                emotion_tag=line.emotion_tag,
                speaker=line.speaker_cn or line.speaker_jp or "夏目藍",
            ))

        return UtteranceRetrievalResult(
            samples=result_samples,
            query_emotion=normalized_tag,
            matched_scene=matched_scene,
            total_in_scene=len(candidates),
            fallback_used=fallback_used,
        )

    def retrieve_multi_scene(
        self,
        emotion_tag: str,
        *,
        lang: str = "cn",
        primary_n: int = 3,
        daily_n: int = 2,
        max_chars: int = DEFAULT_MAX_TOKEN_BUDGET,
    ) -> UtteranceRetrievalResult:
        """混合检索：主场景 + 日常场景。

        确保既有情感相关的参考，也有日常对话的基调。
        """
        # 主场景台词
        primary = self.retrieve(emotion_tag, lang=lang, n=primary_n, max_chars=max_chars)

        # 补充日常台词
        remaining_chars = max_chars - sum(
            len(s.cn_text if lang == "cn" else s.jp_text)
            for s in primary.samples
        )
        if remaining_chars > 50 and daily_n > 0:
            daily = self.retrieve("NEUTRAL", lang=lang, n=daily_n, max_chars=remaining_chars)
            primary.samples.extend(daily.samples)

        return primary

    def render_for_prompt(
        self,
        result: UtteranceRetrievalResult,
        *,
        lang: str = "cn",
    ) -> str:
        """将检索结果渲染为 prompt 注入文本。

        输出格式设计为让 LLM 理解这是风格参考而非内容参考：
        - 明确标注"风格参考"
        - 每条台词标注情感场景
        - 指示 LLM 学习语气和节奏，而非逐字复制
        """
        if not result.samples:
            return ""

        lines: list[str] = [
            "PERSONA_STYLE_EXAMPLES:",
            "- 以下台词仅作为说话风格和语气参考，展示这个角色的语言习惯。",
            "- 学习她的句式节奏、语气词用法、情感表达方式，而非逐字复制。",
            "- 根据当前对话情境自然地融入这些语言特质。",
        ]

        for sample in result.samples:
            text = sample.jp_text if lang == "ja" else sample.cn_text
            if not text:
                text = sample.cn_text or sample.jp_text
            lines.append(f"- [{sample.emotion_tag}] {sample.speaker}: {text}")

        lines.append("")  # 尾部空行
        return "\n".join(lines)

    def to_summary(self) -> dict[str, Any]:
        """生成索引摘要。"""
        return {
            "built": self._built,
            "total_lines": self.total_lines,
            "scene_counts": self.scene_counts,
            "scenes": list(self._index.keys()),
        }


# ---------------------------------------------------------------------------
# 全局单例（懒加载）
# ---------------------------------------------------------------------------
_global_index: EmotionTaggedUtteranceIndex | None = None


def get_utterance_index(
    cn_path: str | Path = DEFAULT_CN_LINES_PATH,
    ja_path: str | Path = DEFAULT_JA_LINES_PATH,
) -> EmotionTaggedUtteranceIndex:
    """获取全局台词索引（懒加载）。"""
    global _global_index
    if _global_index is None or not _global_index.is_built:
        _global_index = EmotionTaggedUtteranceIndex(cn_path=cn_path, ja_path=ja_path)
        try:
            _global_index.build()
        except Exception:
            pass  # 构建失败时返回空索引
    return _global_index


def reset_global_index() -> None:
    """重置全局索引（用于测试或重新加载）。"""
    global _global_index
    _global_index = None
