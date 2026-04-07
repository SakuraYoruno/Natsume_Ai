"""StyleDistiller — 从角色台词语料中深度蒸馏风格规则。

与旧版 import_natsume_style_canon.py 的统计蒸馏不同，本模块提取的是
**可直接指导 LLM 说话方式**的具体规则：
  - 句式模板（按情感场景分类）
  - 情感回应模式（安慰/开心/傲娇/日常等）
  - 特征词汇表（高频用词 vs 禁用词）
  - 节奏模式（短长短呼吸感、开场收束）
  - 口癖/惯用语
  - 中日差异对比

输入：*_speaker_lines.jsonl（蒸馏脚本产物）
输出：StyleProfile JSON（可直接注入 prompt 或写入 StyleCanon v5）
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from host_brain.app.prompt_template_store import resolve_prompt_template_json_merged

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
DEFAULT_CN_LINES_PATH = "runtime/style_canon/natsume_ai_cn_speaker_lines.jsonl"
DEFAULT_JA_LINES_PATH = "runtime/style_canon/natsume_ai_ja_speaker_lines.jsonl"

# 情感场景关键词（中文）— 用于给台词打粗标签
EMOTION_SCENE_KEYWORDS_CN: dict[str, list[str]] = {
    "comfort": [
        "没关系", "不要紧", "别担心", "没事的", "辛苦了", "休息",
        "不要勉强", "不必在意", "安慰", "心疼", "好好休息", "别哭",
        "在这里", "陪着你", "放心", "不用害怕",
    ],
    "happy": [
        "太好了", "真高兴", "开心", "好棒", "太棒了", "恭喜",
        "太厉害了", "真不错", "好开心", "嘻嘻", "呵呵",
    ],
    "tsundere": [
        "才不是", "哼", "别误会", "笨蛋", "不是为了你",
        "只是顺便", "别自作多情", "真是的", "真拿你没办法",
    ],
    "sadness": [
        "难过", "伤心", "对不起", "抱歉", "遗憾", "可惜",
        "不甘心", "失去", "再也", "无法挽回",
    ],
    "anger": [
        "不能原谅", "绝对不行", "够了", "不许", "不允许",
        "混蛋", "过分", "岂有此理",
    ],
    "surprise": [
        "诶", "啊", "什么", "真的吗", "不会吧", "居然",
        "怎么可能", "想不到",
    ],
    "affection": [
        "喜欢", "在意", "重要的人", "不想分开", "心跳",
        "紧张", "脸红", "害羞", "想见你",
    ],
    "daily": [],  # 兜底：不匹配任何关键词的归入日常
}

# 情感场景关键词（日语）
EMOTION_SCENE_KEYWORDS_JA: dict[str, list[str]] = {
    "comfort": [
        "大丈夫", "心配しないで", "無理しないで", "ここにいる",
        "そばにいる", "泣かないで", "安心して", "休んで",
    ],
    "happy": [
        "嬉しい", "よかった", "素敵", "すごい", "楽しい",
        "最高", "おめでとう",
    ],
    "tsundere": [
        "別に", "違う", "バカ", "勘違い", "たまたま",
        "しょうがない", "仕方ない",
    ],
    "sadness": [
        "悲しい", "ごめん", "すまない", "残念", "失って",
        "もう二度と",
    ],
    "anger": [
        "許さない", "ダメ", "やめろ", "ふざけるな",
    ],
    "surprise": [
        "えっ", "まさか", "本当", "信じられない", "嘘",
    ],
    "affection": [
        "好き", "大切", "離れたくない", "ドキドキ", "恥ずかしい",
        "会いたい",
    ],
    "daily": [],
}

# 句尾语气词
CN_PARTICLES = ("呢", "啊", "吧", "呀", "哦", "嘛", "呐", "喔", "哇", "啦", "嗯", "哎")
JA_PARTICLES = ("ね", "よ", "な", "か", "わ", "ぞ", "さ", "ぜ", "よね", "かな", "だろ", "ぞ")

# 省略号变体
ELLIPSIS_PATTERNS = re.compile(r"[…。]{2,}|\.{3,}|……")

# 标点
PUNCT_END = re.compile(r"[。！？!?…]+$")


class _SafeTemplateDict(dict[str, Any]):
    def __missing__(self, key: object) -> str:
        return "{" + str(key) + "}"


def _format_template_text(template_text: Any, **kwargs: Any) -> str:
    text = str(template_text or "")
    if not kwargs:
        return text
    payload = _SafeTemplateDict(
        {
            str(key): "" if value is None else str(value)
            for key, value in kwargs.items()
        }
    )
    try:
        return text.format_map(payload)
    except Exception:
        return text


def _load_render_instruction_templates() -> dict[str, Any]:
    raw = resolve_prompt_template_json_merged("style_distiller.render_instruction.templates")
    return dict(raw) if isinstance(raw, Mapping) else {}


def _mapping_section(mapping: Mapping[str, Any], key: str) -> dict[str, Any]:
    raw = mapping.get(key)
    return dict(raw) if isinstance(raw, Mapping) else {}


def _mapping_list(mapping: Mapping[str, Any], key: str) -> list[str]:
    raw = mapping.get(key)
    if isinstance(raw, list):
        return [str(item) for item in raw if str(item or "").strip()]
    return []


def _mapping_text(
    mapping: Mapping[str, Any],
    key: str,
    *,
    default: str = "",
    **kwargs: Any,
) -> str:
    return _format_template_text(mapping.get(key, default), **kwargs)


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class UtteranceLine:
    """单条台词。"""
    file_rel_path: str
    block_id: str
    line_index: int
    cn_text: str
    jp_text: str
    speaker_cn: str = ""
    speaker_jp: str = ""
    emotion_tag: str = "daily"  # 蒸馏时打上的情感标签


@dataclass
class SentencePattern:
    """句式模板。"""
    pattern: str           # 模板描述
    examples_cn: list[str] = field(default_factory=list)
    examples_ja: list[str] = field(default_factory=list)
    frequency: float = 0.0  # 在语料中出现频率


@dataclass
class EmotionResponsePattern:
    """情感回应模式。"""
    scene: str             # 情感场景
    strategy: str          # 回应策略描述
    sentence_patterns: list[str] = field(default_factory=list)
    examples_cn: list[str] = field(default_factory=list)
    examples_ja: list[str] = field(default_factory=list)
    avg_length: float = 0.0


@dataclass
class StyleProfile:
    """深度风格画像。"""
    # 基础统计
    total_cn: int = 0
    total_ja: int = 0

    # 句式模板（按类型）
    sentence_patterns: list[SentencePattern] = field(default_factory=list)

    # 情感回应模式（按场景）
    emotion_patterns: list[EmotionResponsePattern] = field(default_factory=list)

    # 特征词汇
    high_freq_words_cn: list[str] = field(default_factory=list)
    high_freq_words_ja: list[str] = field(default_factory=list)
    signature_phrases_cn: list[str] = field(default_factory=list)  # 口癖/惯用语
    signature_phrases_ja: list[str] = field(default_factory=list)

    # 节奏模式
    rhythm_description: str = ""
    avg_sentence_length_cn: float = 0.0
    avg_sentence_length_ja: float = 0.0
    short_ratio: float = 0.0       # ≤10字占比
    medium_ratio: float = 0.0      # 11-30字占比
    long_ratio: float = 0.0        # >30字占比

    # 语气词统计
    particle_freq_cn: dict[str, float] = field(default_factory=dict)
    particle_freq_ja: dict[str, float] = field(default_factory=dict)

    # 标点习惯
    question_ratio: float = 0.0
    exclaim_ratio: float = 0.0
    ellipsis_ratio: float = 0.0

    # 开场/收束模式
    opening_patterns: list[str] = field(default_factory=list)
    closing_patterns: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典。"""
        return {
            "total_cn": self.total_cn,
            "total_ja": self.total_ja,
            "sentence_patterns": [
                {
                    "pattern": sp.pattern,
                    "examples_cn": sp.examples_cn[:3],
                    "examples_ja": sp.examples_ja[:3],
                    "frequency": round(sp.frequency, 4),
                }
                for sp in self.sentence_patterns
            ],
            "emotion_patterns": [
                {
                    "scene": ep.scene,
                    "strategy": ep.strategy,
                    "sentence_patterns": ep.sentence_patterns[:5],
                    "examples_cn": ep.examples_cn[:5],
                    "examples_ja": ep.examples_ja[:3],
                    "avg_length": round(ep.avg_length, 1),
                }
                for ep in self.emotion_patterns
            ],
            "high_freq_words_cn": self.high_freq_words_cn[:30],
            "high_freq_words_ja": self.high_freq_words_ja[:30],
            "signature_phrases_cn": self.signature_phrases_cn[:15],
            "signature_phrases_ja": self.signature_phrases_ja[:15],
            "rhythm": {
                "description": self.rhythm_description,
                "avg_cn": round(self.avg_sentence_length_cn, 1),
                "avg_ja": round(self.avg_sentence_length_ja, 1),
                "short_ratio": round(self.short_ratio, 3),
                "medium_ratio": round(self.medium_ratio, 3),
                "long_ratio": round(self.long_ratio, 3),
            },
            "particle_freq_cn": {k: round(v, 3) for k, v in self.particle_freq_cn.items()},
            "particle_freq_ja": {k: round(v, 3) for k, v in self.particle_freq_ja.items()},
            "punctuation": {
                "question_ratio": round(self.question_ratio, 3),
                "exclaim_ratio": round(self.exclaim_ratio, 3),
                "ellipsis_ratio": round(self.ellipsis_ratio, 3),
            },
            "opening_patterns": self.opening_patterns[:10],
            "closing_patterns": self.closing_patterns[:10],
        }


# ---------------------------------------------------------------------------
# 加载台词
# ---------------------------------------------------------------------------
def load_utterance_lines(
    cn_path: str | Path = DEFAULT_CN_LINES_PATH,
    ja_path: str | Path = DEFAULT_JA_LINES_PATH,
) -> list[UtteranceLine]:
    """从 JSONL 文件加载台词。"""
    lines: list[UtteranceLine] = []
    cn_map: dict[str, dict[str, Any]] = {}  # key = file:block:index
    ja_map: dict[str, dict[str, Any]] = {}

    for path, target_map in [(Path(cn_path), cn_map), (Path(ja_path), ja_map)]:
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            key = f"{row.get('file_rel_path', '')}:{row.get('block_id', '')}:{row.get('line_index', 0)}"
            target_map[key] = row

    # 合并中日台词
    all_keys = set(cn_map.keys()) | set(ja_map.keys())
    for key in sorted(all_keys):
        cn_row = cn_map.get(key, {})
        ja_row = ja_map.get(key, {})
        merged = cn_row or ja_row
        line = UtteranceLine(
            file_rel_path=str(merged.get("file_rel_path", "")),
            block_id=str(merged.get("block_id", "")),
            line_index=int(merged.get("line_index", 0)),
            cn_text=str(cn_row.get("cn_text", "") or ja_row.get("cn_text", "")).strip(),
            jp_text=str(ja_row.get("jp_text", "") or cn_row.get("jp_text", "")).strip(),
            speaker_cn=str(merged.get("speaker_cn", "")),
            speaker_jp=str(merged.get("speaker_jp", "")),
        )
        if line.cn_text or line.jp_text:
            lines.append(line)

    return lines


# ---------------------------------------------------------------------------
# 情感标签打标
# ---------------------------------------------------------------------------
def tag_emotion(text: str, lang: str = "cn") -> str:
    """根据关键词给台词打情感标签。"""
    keywords_map = EMOTION_SCENE_KEYWORDS_CN if lang == "cn" else EMOTION_SCENE_KEYWORDS_JA
    text_lower = text.lower()

    scores: dict[str, int] = {}
    for scene, keywords in keywords_map.items():
        if scene == "daily":
            continue
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[scene] = score

    if not scores:
        return "daily"
    return max(scores, key=scores.get)  # type: ignore[arg-type]


def tag_all_lines(lines: list[UtteranceLine]) -> list[UtteranceLine]:
    """给所有台词打情感标签。"""
    for line in lines:
        # 优先用中文判断，中文空时用日语
        text = line.cn_text or line.jp_text
        lang = "cn" if line.cn_text else "ja"
        line.emotion_tag = tag_emotion(text, lang=lang)
    return lines


# ---------------------------------------------------------------------------
# 句式分析
# ---------------------------------------------------------------------------
def _clean_length(text: str) -> int:
    """去除空白后的纯字符长度。"""
    return len(re.sub(r"\s+", "", text))


def _extract_particle(text: str, lang: str = "cn") -> str:
    """提取句尾语气词。"""
    cleaned = PUNCT_END.sub("", text.strip())
    if not cleaned:
        return ""
    particles = CN_PARTICLES if lang == "cn" else JA_PARTICLES
    for p in sorted(particles, key=len, reverse=True):
        if cleaned.endswith(p):
            return p
    return ""


def _is_question(text: str) -> bool:
    return "？" in text or "?" in text


def _is_exclaim(text: str) -> bool:
    return "！" in text or "!" in text


def _has_ellipsis(text: str) -> bool:
    return bool(ELLIPSIS_PATTERNS.search(text))


def analyze_sentence_patterns(lines: list[UtteranceLine]) -> list[SentencePattern]:
    """分析句式模板。"""
    patterns: list[SentencePattern] = []
    total = len(lines) or 1
    cn_texts = [l.cn_text for l in lines if l.cn_text]
    templates = _load_render_instruction_templates()
    sentence_pattern_templates = _mapping_section(templates, "sentence_patterns")

    # 1. 短句应答（≤8字）：如"是呢" "嗯" "我知道了"
    short = [t for t in cn_texts if _clean_length(t) <= 8]
    if short:
        patterns.append(SentencePattern(
            pattern=_mapping_text(
                sentence_pattern_templates,
                "short_reply",
            ),
            examples_cn=sorted(set(short), key=len)[:6],
            examples_ja=[l.jp_text for l in lines if l.cn_text in short[:6] and l.jp_text][:6],
            frequency=len(short) / len(cn_texts) if cn_texts else 0,
        ))

    # 2. 省略号停顿句
    ellipsis_lines = [l for l in lines if l.cn_text and _has_ellipsis(l.cn_text)]
    if ellipsis_lines:
        patterns.append(SentencePattern(
            pattern=_mapping_text(
                sentence_pattern_templates,
                "ellipsis_pause",
            ),
            examples_cn=[l.cn_text for l in ellipsis_lines[:6]],
            examples_ja=[l.jp_text for l in ellipsis_lines[:6] if l.jp_text],
            frequency=len(ellipsis_lines) / total,
        ))

    # 3. 疑问句（轻追问）
    questions = [l for l in lines if l.cn_text and _is_question(l.cn_text)]
    if questions:
        patterns.append(SentencePattern(
            pattern=_mapping_text(
                sentence_pattern_templates,
                "light_followup",
            ),
            examples_cn=[l.cn_text for l in questions[:6]],
            examples_ja=[l.jp_text for l in questions[:6] if l.jp_text],
            frequency=len(questions) / total,
        ))

    # 4. 感叹句（情绪抬升）
    exclaims = [l for l in lines if l.cn_text and _is_exclaim(l.cn_text) and not _is_question(l.cn_text)]
    if exclaims:
        patterns.append(SentencePattern(
            pattern=_mapping_text(
                sentence_pattern_templates,
                "emotion_exclaim",
            ),
            examples_cn=[l.cn_text for l in exclaims[:6]],
            examples_ja=[l.jp_text for l in exclaims[:6] if l.jp_text],
            frequency=len(exclaims) / total,
        ))

    # 5. 转折句（先肯定后转折）
    pivot_keywords = ["不过", "但是", "可是", "然而", "虽然", "虽说", "尽管"]
    pivots = [l for l in lines if l.cn_text and any(kw in l.cn_text for kw in pivot_keywords)]
    if pivots:
        patterns.append(SentencePattern(
            pattern=_mapping_text(
                sentence_pattern_templates,
                "gentle_pivot",
            ),
            examples_cn=[l.cn_text for l in pivots[:6]],
            examples_ja=[l.jp_text for l in pivots[:6] if l.jp_text],
            frequency=len(pivots) / total,
        ))

    # 6. 口语化缩略
    colloquial_markers = ["呵呵", "嘿咻", "哎", "哇", "嘛", "来", "好了"]
    colloquials = [l for l in lines if l.cn_text and any(m in l.cn_text for m in colloquial_markers)]
    if colloquials:
        patterns.append(SentencePattern(
            pattern=_mapping_text(
                sentence_pattern_templates,
                "colloquial",
            ),
            examples_cn=[l.cn_text for l in colloquials[:6]],
            examples_ja=[l.jp_text for l in colloquials[:6] if l.jp_text],
            frequency=len(colloquials) / total,
        ))

    return patterns


# ---------------------------------------------------------------------------
# 情感回应模式分析
# ---------------------------------------------------------------------------
def analyze_emotion_patterns(lines: list[UtteranceLine]) -> list[EmotionResponsePattern]:
    """按情感场景分析回应模式。"""
    # 按 emotion_tag 分组
    groups: dict[str, list[UtteranceLine]] = {}
    for line in lines:
        groups.setdefault(line.emotion_tag, []).append(line)

    patterns: list[EmotionResponsePattern] = []
    templates = _load_render_instruction_templates()
    emotion_strategy_templates = _mapping_section(templates, "emotion_strategies")
    emotion_scene_hint_templates = _mapping_section(templates, "emotion_scene_pattern_hints")
    strategy_map = {
        "comfort": _mapping_text(
            emotion_strategy_templates,
            "comfort",
        ),
        "happy": _mapping_text(
            emotion_strategy_templates,
            "happy",
        ),
        "tsundere": _mapping_text(
            emotion_strategy_templates,
            "tsundere",
        ),
        "sadness": _mapping_text(
            emotion_strategy_templates,
            "sadness",
        ),
        "anger": _mapping_text(
            emotion_strategy_templates,
            "anger",
        ),
        "surprise": _mapping_text(
            emotion_strategy_templates,
            "surprise",
        ),
        "affection": _mapping_text(
            emotion_strategy_templates,
            "affection",
        ),
        "daily": _mapping_text(
            emotion_strategy_templates,
            "daily",
        ),
    }

    for scene, strategy in strategy_map.items():
        scene_lines = groups.get(scene, [])
        if not scene_lines:
            continue

        cn_texts = [l.cn_text for l in scene_lines if l.cn_text]
        ja_texts = [l.jp_text for l in scene_lines if l.jp_text]
        lengths = [_clean_length(t) for t in cn_texts if t]
        avg_len = sum(lengths) / len(lengths) if lengths else 0

        # 提取该场景的句式特点
        scene_sentence_patterns: list[str] = []
        q_count = sum(1 for t in cn_texts if _is_question(t))
        e_count = sum(1 for t in cn_texts if _has_ellipsis(t))
        ex_count = sum(1 for t in cn_texts if _is_exclaim(t))

        if len(cn_texts) > 0:
            if q_count / len(cn_texts) > 0.3:
                scene_sentence_patterns.append(
                    _mapping_text(
                        emotion_scene_hint_templates,
                        "question",
                    )
                )
            if e_count / len(cn_texts) > 0.2:
                scene_sentence_patterns.append(
                    _mapping_text(
                        emotion_scene_hint_templates,
                        "ellipsis",
                    )
                )
            if ex_count / len(cn_texts) > 0.2:
                scene_sentence_patterns.append(
                    _mapping_text(
                        emotion_scene_hint_templates,
                        "exclaim",
                    )
                )
            if avg_len <= 15:
                scene_sentence_patterns.append(
                    _mapping_text(
                        emotion_scene_hint_templates,
                        "short",
                    )
                )
            elif avg_len <= 25:
                scene_sentence_patterns.append(
                    _mapping_text(
                        emotion_scene_hint_templates,
                        "medium",
                    )
                )
            else:
                scene_sentence_patterns.append(
                    _mapping_text(
                        emotion_scene_hint_templates,
                        "long",
                    )
                )

        patterns.append(EmotionResponsePattern(
            scene=scene,
            strategy=strategy,
            sentence_patterns=scene_sentence_patterns,
            examples_cn=cn_texts[:8],
            examples_ja=ja_texts[:5],
            avg_length=avg_len,
        ))

    # 按场景台词数量降序排列
    patterns.sort(key=lambda p: len(p.examples_cn), reverse=True)
    return patterns


# ---------------------------------------------------------------------------
# 词汇分析
# ---------------------------------------------------------------------------
def _segment_simple(text: str) -> list[str]:
    """极简分词：2-4字 n-gram + 标点切分。用于无 jieba 环境。"""
    # 先按标点切分
    segments = re.split(r"[，。！？、；：…\s,.:;!?]+", text)
    words: list[str] = []
    for seg in segments:
        seg = seg.strip()
        if len(seg) <= 4 and len(seg) >= 2:
            words.append(seg)
        elif len(seg) > 4:
            # 滑窗 2-gram
            for i in range(len(seg) - 1):
                words.append(seg[i:i + 2])
    return words


def analyze_vocabulary(
    lines: list[UtteranceLine],
) -> tuple[list[str], list[str], list[str], list[str]]:
    """分析词汇特征。

    Returns:
        (high_freq_cn, high_freq_ja, signature_cn, signature_ja)
    """
    cn_counter: Counter[str] = Counter()
    ja_counter: Counter[str] = Counter()

    for line in lines:
        if line.cn_text:
            cn_counter.update(_segment_simple(line.cn_text))
        if line.jp_text:
            ja_counter.update(_segment_simple(line.jp_text))

    # 过滤太短或太通用的
    stopwords_cn = {"的", "了", "是", "我", "你", "他", "她", "在", "也", "就", "都", "不", "和", "有", "这"}
    stopwords_ja = {"の", "は", "が", "を", "に", "で", "と", "も", "か", "な", "だ", "する", "ある", "いる"}

    high_freq_cn = [
        w for w, _ in cn_counter.most_common(80)
        if w not in stopwords_cn and len(w) >= 2
    ][:30]
    high_freq_ja = [
        w for w, _ in ja_counter.most_common(80)
        if w not in stopwords_ja and len(w) >= 2
    ][:30]

    # 口癖/惯用语：出现频率高但不是通用词的短语
    total_cn = sum(cn_counter.values()) or 1
    signature_cn: list[str] = []
    for word, count in cn_counter.most_common(200):
        if word in stopwords_cn or len(word) < 2:
            continue
        freq = count / total_cn
        # 口癖：频率 > 0.5% 且长度 2-6 字
        if freq > 0.005 and 2 <= len(word) <= 6:
            signature_cn.append(word)
        if len(signature_cn) >= 15:
            break

    total_ja = sum(ja_counter.values()) or 1
    signature_ja: list[str] = []
    for word, count in ja_counter.most_common(200):
        if word in stopwords_ja or len(word) < 2:
            continue
        freq = count / total_ja
        if freq > 0.005 and 2 <= len(word) <= 6:
            signature_ja.append(word)
        if len(signature_ja) >= 15:
            break

    return high_freq_cn, high_freq_ja, signature_cn, signature_ja


# ---------------------------------------------------------------------------
# 节奏分析
# ---------------------------------------------------------------------------
def analyze_rhythm(lines: list[UtteranceLine]) -> dict[str, Any]:
    """分析语言节奏模式。"""
    cn_lengths = [_clean_length(l.cn_text) for l in lines if l.cn_text]
    ja_lengths = [_clean_length(l.jp_text) for l in lines if l.jp_text]

    if not cn_lengths:
        cn_lengths = [0]

    total = len(cn_lengths)
    short_count = sum(1 for n in cn_lengths if n <= 10)
    medium_count = sum(1 for n in cn_lengths if 11 <= n <= 30)
    long_count = sum(1 for n in cn_lengths if n > 30)

    # 分析相邻台词的长度变化模式
    transitions: Counter[str] = Counter()
    for i in range(1, len(cn_lengths)):
        prev = "S" if cn_lengths[i - 1] <= 10 else ("M" if cn_lengths[i - 1] <= 30 else "L")
        curr = "S" if cn_lengths[i] <= 10 else ("M" if cn_lengths[i] <= 30 else "L")
        transitions[f"{prev}→{curr}"] += 1

    # 找到最常见的过渡模式
    top_transitions = transitions.most_common(5)
    templates = _load_render_instruction_templates()
    rhythm_templates = _mapping_section(templates, "rhythm_descriptions")
    rhythm_desc = _mapping_text(
        rhythm_templates,
        "default",
    )
    if top_transitions:
        dominant = top_transitions[0][0]
        if "S→M" in dominant or "M→S" in dominant:
            rhythm_desc = _mapping_text(
                rhythm_templates,
                "short_medium_mix",
            )
        elif "S→S" in dominant:
            rhythm_desc = _mapping_text(
                rhythm_templates,
                "short_chain",
            )
        elif "M→M" in dominant:
            rhythm_desc = _mapping_text(
                rhythm_templates,
                "medium_chain",
            )

    return {
        "avg_cn": sum(cn_lengths) / len(cn_lengths),
        "avg_ja": sum(ja_lengths) / len(ja_lengths) if ja_lengths else 0,
        "short_ratio": short_count / total,
        "medium_ratio": medium_count / total,
        "long_ratio": long_count / total,
        "rhythm_description": rhythm_desc,
        "top_transitions": top_transitions,
    }


# ---------------------------------------------------------------------------
# 开场/收束模式
# ---------------------------------------------------------------------------
def analyze_openings_and_closings(lines: list[UtteranceLine]) -> tuple[list[str], list[str]]:
    """分析台词开头和结尾的常见模式。"""
    opening_counter: Counter[str] = Counter()
    closing_counter: Counter[str] = Counter()

    for line in lines:
        text = line.cn_text
        if not text or _clean_length(text) < 4:
            continue

        # 提取开头 2-3 个字
        cleaned = text.lstrip("「」""''")
        for n in (3, 2):
            if len(cleaned) >= n:
                opening_counter[cleaned[:n]] += 1
                break

        # 提取结尾模式（去标点后最后 2-3 字 + 标点）
        stripped = PUNCT_END.sub("", text).strip()
        if stripped:
            last_char = text.strip()[-1] if text.strip() else ""
            for n in (3, 2):
                if len(stripped) >= n:
                    closing_counter[stripped[-n:] + last_char] += 1
                    break

    # 取频率最高的开场和收束
    openings = [f"「{w}...」" for w, _ in opening_counter.most_common(15) if opening_counter[w] >= 3]
    closings = [f"「...{w}」" for w, _ in closing_counter.most_common(15) if closing_counter[w] >= 3]

    return openings[:10], closings[:10]


# ---------------------------------------------------------------------------
# 语气词分析
# ---------------------------------------------------------------------------
def analyze_particles(lines: list[UtteranceLine]) -> tuple[dict[str, float], dict[str, float]]:
    """统计句尾语气词频率。"""
    cn_particle_counter: Counter[str] = Counter()
    ja_particle_counter: Counter[str] = Counter()
    cn_total = 0
    ja_total = 0

    for line in lines:
        if line.cn_text:
            cn_total += 1
            p = _extract_particle(line.cn_text, "cn")
            if p:
                cn_particle_counter[p] += 1
        if line.jp_text:
            ja_total += 1
            p = _extract_particle(line.jp_text, "ja")
            if p:
                ja_particle_counter[p] += 1

    cn_freq = {p: c / cn_total for p, c in cn_particle_counter.most_common(10)} if cn_total else {}
    ja_freq = {p: c / ja_total for p, c in ja_particle_counter.most_common(10)} if ja_total else {}

    return cn_freq, ja_freq


# ---------------------------------------------------------------------------
# 主入口：完整蒸馏
# ---------------------------------------------------------------------------
def distill_style_profile(
    cn_path: str | Path = DEFAULT_CN_LINES_PATH,
    ja_path: str | Path = DEFAULT_JA_LINES_PATH,
) -> StyleProfile:
    """从台词语料中蒸馏完整风格画像。"""
    global _ja_lines_cache  # noqa: PLW0603

    # 1. 加载台词
    lines = load_utterance_lines(cn_path, ja_path)
    if not lines:
        return StyleProfile()

    # 2. 打情感标签
    lines = tag_all_lines(lines)
    _ja_lines_cache = lines  # 缓存供日语渲染函数使用

    # 3. 句式模板分析
    sentence_patterns = analyze_sentence_patterns(lines)

    # 4. 情感回应模式
    emotion_patterns = analyze_emotion_patterns(lines)

    # 5. 词汇分析
    high_freq_cn, high_freq_ja, sig_cn, sig_ja = analyze_vocabulary(lines)

    # 6. 节奏分析
    rhythm = analyze_rhythm(lines)

    # 7. 开场/收束
    openings, closings = analyze_openings_and_closings(lines)

    # 8. 语气词
    particle_cn, particle_ja = analyze_particles(lines)

    # 9. 标点统计
    cn_texts = [l.cn_text for l in lines if l.cn_text]
    total = len(cn_texts) or 1
    q_ratio = sum(1 for t in cn_texts if _is_question(t)) / total
    e_ratio = sum(1 for t in cn_texts if _is_exclaim(t)) / total
    el_ratio = sum(1 for t in cn_texts if _has_ellipsis(t)) / total

    return StyleProfile(
        total_cn=sum(1 for l in lines if l.cn_text),
        total_ja=sum(1 for l in lines if l.jp_text),
        sentence_patterns=sentence_patterns,
        emotion_patterns=emotion_patterns,
        high_freq_words_cn=high_freq_cn,
        high_freq_words_ja=high_freq_ja,
        signature_phrases_cn=sig_cn,
        signature_phrases_ja=sig_ja,
        rhythm_description=rhythm["rhythm_description"],
        avg_sentence_length_cn=rhythm["avg_cn"],
        avg_sentence_length_ja=rhythm["avg_ja"],
        short_ratio=rhythm["short_ratio"],
        medium_ratio=rhythm["medium_ratio"],
        long_ratio=rhythm["long_ratio"],
        particle_freq_cn=particle_cn,
        particle_freq_ja=particle_ja,
        question_ratio=q_ratio,
        exclaim_ratio=e_ratio,
        ellipsis_ratio=el_ratio,
        opening_patterns=openings,
        closing_patterns=closings,
    )


# ---------------------------------------------------------------------------
# 渲染为 prompt 可用的风格指导文本
# ---------------------------------------------------------------------------
def render_style_instruction_v5(profile: StyleProfile) -> str:
    """将 StyleProfile 渲染为 v5 版风格指导文本。

    不再是统计数字，而是具体的、可操作的说话方式指导。
    """
    templates = _load_render_instruction_templates()
    render_templates = _mapping_section(templates, "render_prompt_cn")
    core_lines = _mapping_section(render_templates, "core_lines")
    punctuation_lines = _mapping_section(render_templates, "punctuation_lines")
    forbidden_lines = _mapping_list(render_templates, "forbidden_lines")
    parts: list[str] = []

    parts.append(
        _mapping_text(
            render_templates,
            "intro_template",
            cn=profile.total_cn,
            ja=profile.total_ja,
        )
    )

    # ── 核心语感 ──
    parts.append("\n" + _mapping_text(render_templates, "core_title"))
    parts.append(
        _mapping_text(
            core_lines,
            "gentle",
        )
    )
    parts.append(
        _mapping_text(
            core_lines,
            "colloquial",
        )
    )
    parts.append(
        _mapping_text(
            core_lines,
            "rhythm_template",
            rhythm_description=profile.rhythm_description,
        )
    )
    parts.append(
        _mapping_text(
            core_lines,
            "sentence_distribution_template",
            short_ratio=f"{profile.short_ratio:.0%}",
            medium_ratio=f"{profile.medium_ratio:.0%}",
            long_ratio=f"{profile.long_ratio:.0%}",
        )
    )

    # ── 句式模板 ──
    if profile.sentence_patterns:
        parts.append("\n" + _mapping_text(render_templates, "sentence_patterns_title"))
        for sp in profile.sentence_patterns[:6]:
            examples = "、".join(f"「{e}」" for e in sp.examples_cn[:3])
            parts.append(
                _mapping_text(
                    render_templates,
                    "sentence_pattern_item_template",
                    pattern=sp.pattern,
                    frequency=f"{sp.frequency:.0%}",
                    examples=examples,
                )
            )

    # ── 情感回应模式 ──
    if profile.emotion_patterns:
        parts.append("\n" + _mapping_text(render_templates, "emotion_patterns_title"))
        for ep in profile.emotion_patterns:
            if not ep.examples_cn:
                continue
            examples = "；".join(f"「{e}」" for e in ep.examples_cn[:3])
            pattern_hint = "、".join(ep.sentence_patterns[:3]) if ep.sentence_patterns else ""
            pattern_hint_section = ""
            if pattern_hint:
                pattern_hint_section = _mapping_text(
                    render_templates,
                    "pattern_hint_wrapper",
                    pattern_hint=pattern_hint,
                )
            parts.append(
                _mapping_text(
                    render_templates,
                    "emotion_pattern_item_template",
                    scene=ep.scene,
                    strategy=ep.strategy,
                    pattern_hint_section=pattern_hint_section,
                    examples=examples,
                )
            )

    # ── 语气词 ──
    if profile.particle_freq_cn:
        top_particles = list(profile.particle_freq_cn.items())[:5]
        particle_str = "、".join(f"「{p}」{f:.0%}" for p, f in top_particles)
        parts.append(
            "\n"
            + _mapping_text(render_templates, "particle_title")
            + "\n"
            + _mapping_text(
                render_templates,
                "particle_line_template",
                particle_str=particle_str,
            )
        )

    # ── 口癖/惯用语 ──
    if profile.signature_phrases_cn:
        sig_str = "、".join(f"「{p}」" for p in profile.signature_phrases_cn[:10])
        parts.append(
            "\n"
            + _mapping_text(render_templates, "signature_title")
            + "\n"
            + _mapping_text(
                render_templates,
                "signature_line_template",
                signature_str=sig_str,
            )
        )

    # ── 标点习惯 ──
    parts.append(
        "\n"
        + _mapping_text(render_templates, "punctuation_title")
        + "\n"
        + _mapping_text(
            punctuation_lines,
            "question_template",
            question_ratio=f"{profile.question_ratio:.0%}",
        )
        + "\n"
        + _mapping_text(
            punctuation_lines,
            "ellipsis_template",
            ellipsis_ratio=f"{profile.ellipsis_ratio:.0%}",
        )
        + "\n"
        + _mapping_text(
            punctuation_lines,
            "exclaim_template",
            exclaim_ratio=f"{profile.exclaim_ratio:.0%}",
        )
    )

    # ── 边界约束 ──
    parts.append(
        "\n"
        + _mapping_text(render_templates, "forbidden_title")
        + "\n"
        + "\n".join(forbidden_lines)
    )

    return "\n".join(parts)


def render_style_instruction_v5_ja(profile: StyleProfile) -> str:
    """将 StyleProfile 渲染为日语版 v5 风格指导文本。

    与中文版的关键差异：
    - 示例台词使用日语原文
    - 语气词统计使用日语（か/よ/な/ね）
    - 节奏统计基于日语字符长度
    - 边界约束用日语表述
    """
    templates = _load_render_instruction_templates()
    render_templates = _mapping_section(templates, "render_prompt_ja")
    core_lines = _mapping_section(render_templates, "core_lines")
    punctuation_lines = _mapping_section(render_templates, "punctuation_lines")
    sentence_pattern_map = _mapping_section(render_templates, "sentence_pattern_map")
    emotion_strategy_map = _mapping_section(render_templates, "emotion_strategy_map")
    forbidden_lines = _mapping_list(render_templates, "forbidden_lines")
    rhythm_description_map = _mapping_section(render_templates, "rhythm_description_map")
    scene_hint_map = _mapping_section(render_templates, "emotion_scene_pattern_hints_ja_map")
    parts: list[str] = []

    # ── 日语台词的独立统计 ──
    ja_lines = [l for l in _ja_lines_cache if l.jp_text]
    ja_total = len(ja_lines) or 1
    ja_lengths = [_clean_length(l.jp_text) for l in ja_lines]
    ja_short = sum(1 for n in ja_lengths if n <= 12) / ja_total if ja_lengths else 0
    ja_medium = sum(1 for n in ja_lengths if 13 <= n <= 35) / ja_total if ja_lengths else 0
    ja_long = sum(1 for n in ja_lengths if n > 35) / ja_total if ja_lengths else 0
    ja_q_ratio = sum(1 for l in ja_lines if _is_question(l.jp_text)) / ja_total
    ja_ex_ratio = sum(1 for l in ja_lines if _is_exclaim(l.jp_text)) / ja_total
    ja_el_ratio = sum(1 for l in ja_lines if _has_ellipsis(l.jp_text)) / ja_total

    parts.append(
        _mapping_text(
            render_templates,
            "intro_template",
            ja=profile.total_ja,
        )
    )

    # ── 核心語感 ──
    parts.append("\n" + _mapping_text(render_templates, "core_title"))
    parts.append(
        _mapping_text(
            core_lines,
            "gentle",
        )
    )
    parts.append(
        _mapping_text(
            core_lines,
            "colloquial",
        )
    )
    ja_rhythm_description = str(
        rhythm_description_map.get(profile.rhythm_description, profile.rhythm_description)
    )
    parts.append(
        _mapping_text(
            core_lines,
            "rhythm_template",
            rhythm_description=ja_rhythm_description,
        )
    )
    parts.append(
        _mapping_text(
            core_lines,
            "sentence_distribution_template",
            short_ratio=f"{ja_short:.0%}",
            medium_ratio=f"{ja_medium:.0%}",
            long_ratio=f"{ja_long:.0%}",
        )
    )

    # ── 句式テンプレート ──
    if profile.sentence_patterns:
        parts.append("\n" + _mapping_text(render_templates, "sentence_patterns_title"))
        for sp in profile.sentence_patterns[:6]:
            ja_desc = str(sentence_pattern_map.get(sp.pattern.split("：")[0], sp.pattern))
            examples = sp.examples_ja[:3] if sp.examples_ja else sp.examples_cn[:3]
            examples_str = "、".join(f"「{e}」" for e in examples)
            parts.append(
                _mapping_text(
                    render_templates,
                    "sentence_pattern_item_template",
                    pattern=ja_desc,
                    frequency=f"{sp.frequency:.0%}",
                    examples=examples_str,
                )
            )

    # ── 感情応答パターン ──
    if profile.emotion_patterns:
        parts.append("\n" + _mapping_text(render_templates, "emotion_patterns_title"))
        for ep in profile.emotion_patterns:
            examples = ep.examples_ja[:3] if ep.examples_ja else ep.examples_cn[:3]
            if not examples:
                continue
            ja_strategy = str(emotion_strategy_map.get(ep.scene, ep.strategy))
            examples_str = "；".join(f"「{e}」" for e in examples)
            localized_pattern_hints = [
                str(scene_hint_map.get(pattern, pattern))
                for pattern in ep.sentence_patterns[:3]
            ]
            pattern_hint = "、".join(localized_pattern_hints) if localized_pattern_hints else ""
            pattern_hint_section = ""
            if pattern_hint:
                pattern_hint_section = _mapping_text(
                    render_templates,
                    "pattern_hint_wrapper",
                    pattern_hint=pattern_hint,
                )
            parts.append(
                _mapping_text(
                    render_templates,
                    "emotion_pattern_item_template",
                    scene=ep.scene,
                    strategy=ja_strategy,
                    pattern_hint_section=pattern_hint_section,
                    examples=examples_str,
                )
            )

    # ── 終助詞 ──
    if profile.particle_freq_ja:
        top_particles = list(profile.particle_freq_ja.items())[:5]
        particle_str = "、".join(f"「{p}」{f:.0%}" for p, f in top_particles)
        parts.append(
            "\n"
            + _mapping_text(render_templates, "particle_title")
            + "\n"
            + _mapping_text(
                render_templates,
                "particle_line_template",
                particle_str=particle_str,
            )
        )

    # ── 特徴的な表現 ──
    if profile.signature_phrases_ja:
        sig_str = "、".join(f"「{p}」" for p in profile.signature_phrases_ja[:10])
        parts.append(
            "\n"
            + _mapping_text(render_templates, "signature_title")
            + "\n"
            + _mapping_text(
                render_templates,
                "signature_line_template",
                signature_str=sig_str,
            )
        )

    # ── 句読点リズム ──
    parts.append(
        "\n"
        + _mapping_text(render_templates, "punctuation_title")
        + "\n"
        + _mapping_text(
            punctuation_lines,
            "question_template",
            question_ratio=f"{ja_q_ratio:.0%}",
        )
        + "\n"
        + _mapping_text(
            punctuation_lines,
            "ellipsis_template",
            ellipsis_ratio=f"{ja_el_ratio:.0%}",
        )
        + "\n"
        + _mapping_text(
            punctuation_lines,
            "exclaim_template",
            exclaim_ratio=f"{ja_ex_ratio:.0%}",
        )
    )

    # ── 絶対禁止 ──
    parts.append(
        "\n"
        + _mapping_text(render_templates, "forbidden_title")
        + "\n"
        + "\n".join(forbidden_lines)
    )

    return "\n".join(parts)


# 日语蒸馏用的缓存（在 distill_style_profile 调用后设置）
_ja_lines_cache: list[UtteranceLine] = []


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------
def main() -> None:
    """命令行运行：蒸馏风格画像并输出 JSON + 风格指导文本。"""
    import sys

    repo_root = Path(__file__).resolve().parents[2]
    cn_path = repo_root / DEFAULT_CN_LINES_PATH
    ja_path = repo_root / DEFAULT_JA_LINES_PATH

    if not cn_path.exists() and not ja_path.exists():
        sys.stderr.write(f"[error] 台词文件不存在: {cn_path} / {ja_path}\n")
        sys.exit(1)

    profile = distill_style_profile(cn_path, ja_path)

    # 输出 JSON
    output_dir = repo_root / "runtime" / "style_canon"
    output_dir.mkdir(parents=True, exist_ok=True)

    profile_path = output_dir / "style_profile_v5.json"
    profile_path.write_text(
        json.dumps(profile.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # 输出风格指导文本
    instruction = render_style_instruction_v5(profile)
    instruction_path = output_dir / "style_instruction_v5.txt"
    instruction_path.write_text(instruction, encoding="utf-8")

    sys.stdout.write(f"[OK] StyleProfile → {profile_path}\n")
    sys.stdout.write(f"[OK] StyleInstruction v5 → {instruction_path}\n")
    sys.stdout.write(f"[stats] CN: {profile.total_cn}, JA: {profile.total_ja}\n")
    sys.stdout.write(f"[stats] Emotion patterns: {len(profile.emotion_patterns)}\n")
    sys.stdout.write(f"[stats] Sentence patterns: {len(profile.sentence_patterns)}\n")
    sys.stdout.write(f"[instruction preview]\n{instruction[:500]}...\n")


if __name__ == "__main__":
    main()
