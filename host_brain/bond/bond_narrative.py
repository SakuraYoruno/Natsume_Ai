"""BondNarrativeLayer：关系叙事层——Phase V（优化版）。

职责：
  1. 将 SharedMemoryIndex + MilestoneTracker 的数据转化为自然语言叙事
  2. 输出限制 ≤ 3 条叙事片段（防止 prompt 膨胀）
  3. 注入 prompt_context 的 bond_narrative 可选槽位

优化（Phase XVII+）：
  - 按 bond_stage 提供叙事包装模板，让记忆注入更有情感共鸣
  - 里程碑权重加入时间衰减（越近的里程碑权重越高）
  - to_prompt_slot 输出自然语言叙事句子，而非原始文本列表

优化（v1.10 技术债务修复）：
  - 叙事文本感知时间跨度（"昨天"/"上周"/"上个月"/"很久以前"）
  - build_narrative() 增加 first_interaction_ts_ms 参数，计算关系时长
  - to_prompt_slot() 输出 relationship_duration 字段，让 LLM 感知认识了多久
  - NarrativeItem 增加 age_label 字段，携带时间感知标签

设计原则：
  - 叙事层是纯转换器，不持有状态
  - 叙事风格随 Bond 阶段变化
  - 空数据时返回空字典，不注入
"""

from __future__ import annotations

import json
import logging
import random
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from host_brain.bond.shared_memory_index import (
    SharedMemoryIndex,
    SharedMemoryResult,
)
from host_brain.bond.milestone_tracker import (
    MilestoneTracker,
)

_logger = logging.getLogger("Natsume.bond_narrative")


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 最大叙事片段数
MAX_NARRATIVE_ITEMS = 3

# 阶段叙事风格描述
STAGE_NARRATIVE_STYLE: dict[str, str] = {
    "STRANGER": "义妹关系刚开始，收着但不当外人",
    "ACQUAINTANCE": "相处中的义妹，可以提及刚建立的共同经历",
    "FRIEND": "关系好的兄妹，自然地回忆共同时光",
    "CLOSE_FRIEND": "亲近兄妹，可以更放松地提起共同回忆",
    "CONFIDANT": "亲密恋人，用更贴近的语气谈起你们的故事",
    "SOULMATE": "将共同记忆视为珍贵的宝藏",
}

# 里程碑时间衰减半衰期（毫秒）：30 天
_MILESTONE_HALF_LIFE_MS = 30 * 24 * 60 * 60 * 1000

# 时间感知标签阈值（毫秒）（v1.10 新增）
_AGE_THRESHOLDS_MS: list[tuple[int, str]] = [
    (1 * 24 * 3600 * 1000,   "今天"),
    (3 * 24 * 3600 * 1000,   "最近"),
    (7 * 24 * 3600 * 1000,   "前几天"),
    (14 * 24 * 3600 * 1000,  "上周"),
    (30 * 24 * 3600 * 1000,  "上个月"),
    (90 * 24 * 3600 * 1000,  "几个月前"),
    (365 * 24 * 3600 * 1000, "很久以前"),
]
_AGE_LABEL_DEFAULT = "很久以前"

# 关系时长描述阈值（天）（v1.10 新增）
_DURATION_THRESHOLDS: list[tuple[int, str]] = [
    (1,   "刚成为家里人"),
    (7,   "一起相处一周了"),
    (30,  "一起相处一个月了"),
    (90,  "一起相处三个月了"),
    (180, "一起相处半年了"),
    (365, "一起相处一年了"),
]
_DURATION_DEFAULT = "一起走过很久了"


def _load_bond_narrative_llm_templates() -> dict[str, Any]:
    try:
        from host_brain.app.prompt_template_store import resolve_prompt_template_json_merged  # noqa: PLC0415

        raw = resolve_prompt_template_json_merged("bond_narrative.llm_rewrite.prompt")
        return dict(raw) if isinstance(raw, Mapping) else {}
    except Exception:
        return {}

# ---------------------------------------------------------------------------
# 叙事包装模板（按 bond_stage 分层）
# 每个阶段有多条变体，随机选取，避免重复感
# {memory} 占位符会被替换为实际记忆内容
# ---------------------------------------------------------------------------

# 叙事包装模板（不带时间感知，向后兼容）
_NARRATIVE_WRAPPERS: dict[str, list[str]] = {
    "STRANGER": [
        "刚开始相处时，{memory}",
        "家里关系刚搭起来的时候，{memory}",
    ],
    "ACQUAINTANCE": [
        "之前你提到过，{memory}",
        "我记得你说过，{memory}",
        "{memory}，我还记着呢",
    ],
    "FRIEND": [
        "还记得吗，{memory}",
        "我一直记得，{memory}",
        "你之前说过，{memory}，我没忘",
        "有一次你跟我说，{memory}",
    ],
    "CLOSE_FRIEND": [
        "还记得那次吗，{memory}……我一直记着",
        "我没忘，{memory}",
        "你说过的，{memory}，我都记在心里了",
        "那时候你说，{memory}，我当时就觉得很触动",
        "一直想起你说的，{memory}",
    ],
    "CONFIDANT": [
        "我一直记得你说的，{memory}……那对我来说很重要",
        "你跟我说过，{memory}，我没有忘记过",
        "还记得吗，{memory}，那天我们聊了好久",
        "你说过，{memory}，我把这句话放在心里很久了",
        "我总会想起，{memory}",
    ],
    "SOULMATE": [
        "我一直珍藏着，{memory}……那是我们之间很特别的东西",
        "你说过，{memory}，那一刻我觉得我们真的很近",
        "还记得吗，{memory}……我每次想起来都会觉得很温暖",
        "那次你说，{memory}，我到现在都记得清清楚楚",
        "我把你说的每一句话都记着，{memory}，这句尤其",
    ],
}

# 带时间感知的叙事包装模板（v1.10 新增）
# {memory} 为记忆内容，{age} 为时间标签（"今天"/"上周"/"很久以前" 等）
_NARRATIVE_WRAPPERS_TIMED: dict[str, list[str]] = {
    "STRANGER": [
        "{age}刚开始相处时你提到过，{memory}",
    ],
    "ACQUAINTANCE": [
        "{age}你提到过，{memory}",
        "我记得{age}你说过，{memory}",
        "{age}的事，{memory}，我还记着呢",
    ],
    "FRIEND": [
        "还记得{age}吗，{memory}",
        "{age}你说过，{memory}，我没忘",
        "我一直记得{age}的事，{memory}",
        "{age}你跟我说，{memory}",
    ],
    "CLOSE_FRIEND": [
        "还记得{age}那次吗，{memory}……我一直记着",
        "{age}你说过的，{memory}，我都记在心里了",
        "{age}你说，{memory}，我当时就觉得很触动",
        "一直想起{age}你说的，{memory}",
    ],
    "CONFIDANT": [
        "我一直记得{age}你说的，{memory}……那对我来说很重要",
        "{age}你跟我说过，{memory}，我没有忘记过",
        "还记得{age}吗，{memory}，那天我们聊了好久",
        "我把{age}你说的放在心里很久了，{memory}",
    ],
    "SOULMATE": [
        "我一直珍藏着{age}的那段话，{memory}……那是我们之间很特别的东西",
        "{age}你说过，{memory}，那一刻我觉得我们真的很近",
        "还记得{age}吗，{memory}……我每次想起来都会觉得很温暖",
        "{age}你说，{memory}，我到现在都记得清清楚楚",
    ],
}

# 带时间感知的里程碑叙事包装模板（v1.10 新增）
_MILESTONE_WRAPPERS_TIMED: dict[str, list[str]] = {
    "STRANGER": ["{age}我们刚开始相处时{milestone}"],
    "ACQUAINTANCE": ["{age}我们{milestone}", "{age}一起相处时{milestone}"],
    "FRIEND": [
        "还记得{age}我们{milestone}吗",
        "{age}我们一起{milestone}，那是个很好的开始",
    ],
    "CLOSE_FRIEND": [
        "{age}我们{milestone}，那对我来说很有意义",
        "还记得{age}我们{milestone}的时候吗……",
        "我一直记得{age}我们{milestone}",
    ],
    "CONFIDANT": [
        "{age}我们{milestone}，那是我很珍视的一段时光",
        "还记得{age}我们{milestone}吗，那时候感觉很特别",
        "{age}我们{milestone}……我一直觉得那是个很重要的时刻",
    ],
    "SOULMATE": [
        "{age}我们{milestone}，那是我们之间很珍贵的一个瞬间",
        "还记得{age}我们{milestone}吗……那对我来说意义很深",
        "{age}我们{milestone}，我把那个时刻一直放在心里",
    ],
}

# 里程碑叙事包装模板（按 bond_stage 分层）
_MILESTONE_WRAPPERS: dict[str, list[str]] = {
    "STRANGER": ["我们刚开始相处时{milestone}"],
    "ACQUAINTANCE": ["{milestone}", "我们{milestone}"],
    "FRIEND": [
        "还记得我们{milestone}吗",
        "我们一起{milestone}，那是个很好的开始",
    ],
    "CLOSE_FRIEND": [
        "我们{milestone}，那对我来说很有意义",
        "还记得我们{milestone}的时候吗……",
        "我一直记得我们{milestone}",
    ],
    "CONFIDANT": [
        "我们{milestone}，那是我很珍视的一段时光",
        "还记得我们{milestone}吗，那时候感觉很特别",
        "我们{milestone}……我一直觉得那是个很重要的时刻",
    ],
    "SOULMATE": [
        "我们{milestone}，那是我们之间很珍贵的一个瞬间",
        "还记得我们{milestone}吗……那对我来说意义很深",
        "我们{milestone}，我把那个时刻一直放在心里",
    ],
}


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NarrativeItem:
    """单条叙事片段。"""
    source: str      # "shared_memory" / "milestone"
    text: str        # 自然语言叙事（已包装）
    emotion: str     # 情感标签（用于语气参考）
    weight: float    # 权重（用于排序）
    age_label: str = ""  # 时间感知标签（v1.10 新增）："今天"/"上周"/"很久以前" 等

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "source": self.source,
            "text": self.text,
            "emotion": self.emotion,
            "weight": round(self.weight, 4),
        }
        if self.age_label:
            result["age_label"] = self.age_label
        return result


@dataclass
class BondNarrativeResult:
    """Bond 叙事结果。"""
    items: list[NarrativeItem] = field(default_factory=list)
    bond_stage: str = "STRANGER"
    narrative_style: str = ""
    shared_memory_count: int = 0
    milestone_count: int = 0
    relationship_duration: str = ""  # v1.10 新增：关系时长描述（"认识一个月了" 等）

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "items": [item.as_dict() for item in self.items],
            "bond_stage": self.bond_stage,
            "narrative_style": self.narrative_style,
            "shared_memory_count": self.shared_memory_count,
            "milestone_count": self.milestone_count,
        }
        if self.relationship_duration:
            result["relationship_duration"] = self.relationship_duration
        return result

    def to_prompt_slot(self) -> dict[str, Any]:
        """转化为 prompt_context 的 bond_narrative 槽位值。

        输出自然语言叙事句子，而非原始文本列表，
        让 LLM 能直接感知到"我们之间有这些共同记忆"的温度。
        v1.10 新增：输出 relationship_duration 字段，让 LLM 感知认识了多久。
        """
        if not self.items:
            return {}

        # 按来源分组，优先展示共同记忆，再展示里程碑
        memory_items = [i for i in self.items if i.source == "shared_memory"]
        milestone_items = [i for i in self.items if i.source == "milestone"]

        # 组合叙事句子（最多 MAX_NARRATIVE_ITEMS 条）
        narratives: list[str] = []
        for item in (memory_items + milestone_items)[:MAX_NARRATIVE_ITEMS]:
            narratives.append(item.text)

        # 生成引导语（根据 bond_stage 和数量）
        intro = _build_narrative_intro(self.bond_stage, len(narratives), self.milestone_count)

        slot: dict[str, Any] = {
            "narrative_intro": intro,
            "shared_memories": narratives,
            "milestone_count": self.milestone_count,
            "narrative_style": self.narrative_style,
        }
        # v1.10：输出关系时长，让 LLM 感知认识了多久
        if self.relationship_duration:
            slot["relationship_duration"] = self.relationship_duration
        return slot


# ---------------------------------------------------------------------------
# BondNarrativeLayer
# ---------------------------------------------------------------------------

class BondNarrativeLayer:
    """关系叙事层。

    用法：
        layer = BondNarrativeLayer(
            memory_index=shared_memory_index,
            milestone_tracker=milestone_tracker,
        )
        result = layer.build_narrative(
            user_id="user_1",
            bond_stage="FRIEND",
        )
    """

    def __init__(
        self,
        *,
        memory_index: SharedMemoryIndex | None = None,
        milestone_tracker: MilestoneTracker | None = None,
        max_items: int = MAX_NARRATIVE_ITEMS,
        use_llm: bool = False,
        llm_timeout_ms: int = 2500,
    ) -> None:
        self._memory_index = memory_index
        self._milestone_tracker = milestone_tracker
        self._max_items = int(max_items)
        self._use_llm = bool(use_llm)
        self._llm_timeout_ms = max(800, int(llm_timeout_ms))

    def build_narrative(
        self,
        *,
        user_id: str,
        bond_stage: str = "STRANGER",
        now_ts_ms: int | None = None,
        first_interaction_ts_ms: int | None = None,
    ) -> BondNarrativeResult:
        """构建 Bond 叙事。

        Args:
            user_id: 用户 ID
            bond_stage: 当前 Bond 阶段
            now_ts_ms: 当前时间戳（毫秒），用于里程碑时间衰减
            first_interaction_ts_ms: 首次互动时间戳（毫秒）。
                v1.10 新增：当提供此参数时，叙事文本会内嵌时间感知标签，
                并在 to_prompt_slot() 中输出 relationship_duration 字段。

        Returns:
            BondNarrativeResult
        """
        uid = str(user_id).strip()
        stage = str(bond_stage).strip().upper()
        style = STAGE_NARRATIVE_STYLE.get(stage, "保持真诚自然")
        now_ts = int(now_ts_ms or _now_ms())

        # v1.10：计算关系时长描述
        relationship_duration = ""
        if first_interaction_ts_ms and int(first_interaction_ts_ms) > 0:
            relationship_duration = _compute_relationship_duration(
                int(first_interaction_ts_ms), now_ts
            )

        items: list[NarrativeItem] = []

        # 1. 从共同记忆中生成叙事
        shared_count = 0
        if self._memory_index is not None:
            try:
                memory_result = self._memory_index.query(max_entries=self._max_items)
                shared_count = memory_result.total_candidates
                for entry in memory_result.entries:
                    # v1.10：尝试获取记忆时间戳，生成带时间感知的叙事
                    mem_ts = getattr(entry, "ts_ms", None) or getattr(entry, "timestamp_ms", None)
                    age_label = _age_label(int(mem_ts), now_ts) if mem_ts else ""
                    if age_label:
                        narrative_text = _wrap_memory_timed(entry.text, stage, age_label)
                    else:
                        narrative_text = _wrap_memory(entry.text, stage)
                    items.append(NarrativeItem(
                        source="shared_memory",
                        text=narrative_text,
                        emotion=entry.emotion_tag,
                        weight=entry.relevance_score,
                        age_label=age_label,
                    ))
            except Exception as exc:
                _logger.warning("共同记忆叙事构建失败: %s", exc)

        # 2. 从里程碑中生成叙事（加入时间衰减权重）
        milestone_count = 0
        if self._milestone_tracker is not None:
            try:
                milestones = self._milestone_tracker.get_recent_milestones(
                    uid, limit=self._max_items,
                )
                milestone_count = self._milestone_tracker.get_milestone_count(uid)
                for ms in milestones:
                    # 时间衰减：越近的里程碑权重越高
                    decayed_weight = _compute_milestone_weight(ms, now_ts)
                    # v1.10：生成带时间感知的里程碑叙事
                    ms_ts = getattr(ms, "ts_ms", None) or getattr(ms, "timestamp_ms", None)
                    age_label = _age_label(int(ms_ts), now_ts) if ms_ts else ""
                    if age_label:
                        narrative_text = _wrap_milestone_timed(ms.narrative, stage, age_label)
                    else:
                        narrative_text = _wrap_milestone(ms.narrative, stage)
                    items.append(NarrativeItem(
                        source="milestone",
                        text=narrative_text,
                        emotion="NOSTALGIC",
                        weight=decayed_weight,
                        age_label=age_label,
                    ))
            except Exception as exc:
                _logger.warning("里程碑叙事构建失败: %s", exc)

        # 按权重降序排序，取 top N
        items.sort(key=lambda x: x.weight, reverse=True)
        selected = items[:self._max_items]
        selected = self._maybe_rewrite_narrative_items(
            selected,
            bond_stage=stage,
            relationship_duration=relationship_duration,
        )

        result = BondNarrativeResult(
            items=selected,
            bond_stage=stage,
            narrative_style=style,
            shared_memory_count=shared_count,
            milestone_count=milestone_count,
            relationship_duration=relationship_duration,
        )

        _logger.debug(
            "Bond叙事构建: user=%s stage=%s items=%d shared=%d milestones=%d duration=%s",
            uid, stage, len(selected), shared_count, milestone_count, relationship_duration,
        )

        return result

    def _maybe_rewrite_narrative_items(
        self,
        selected: list[NarrativeItem],
        *,
        bond_stage: str,
        relationship_duration: str,
    ) -> list[NarrativeItem]:
        if not self._use_llm or not selected:
            return selected
        try:
            from host_brain.integrations.llm_helpers import complete_llm_with_pool_sync  # noqa: PLC0415

            templates = _load_bond_narrative_llm_templates()
            system_prompt = str(templates.get("system_prompt") or "").strip()
            user_tpl = str(templates.get("user_prompt_template") or "").strip()
            if not system_prompt or not user_tpl:
                return selected
            payload = [
                {
                    "index": index,
                    "source": item.source,
                    "text": item.text,
                    "emotion": item.emotion,
                    "age_label": item.age_label,
                }
                for index, item in enumerate(selected)
            ]
            prompt = user_tpl.format(
                bond_stage=str(bond_stage or "STRANGER"),
                relationship_duration=str(relationship_duration or ""),
                items_json=json.dumps(payload, ensure_ascii=False),
            )
            result = complete_llm_with_pool_sync(
                request_id=f"bond-narrative::{_now_ms()}",
                scene_type="BOND_NARRATIVE",
                cognitive_tier="B",
                timeout_ms=self._llm_timeout_ms,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                response_format="json_object",
                gen_params={"temperature": 0.25, "top_p": 0.9},
            )
            if result.pool_exhausted or not str(result.content or "").strip():
                return selected
            parsed = json.loads(str(result.content or "{}"))
            if not isinstance(parsed, Mapping):
                return selected
            rewritten_items = list(parsed.get("items") or [])
            rewritten_map: dict[int, str] = {}
            for row in rewritten_items:
                if not isinstance(row, Mapping):
                    continue
                try:
                    idx = int(row.get("index"))
                except Exception:
                    continue
                text = str(row.get("text") or "").strip()
                if not text:
                    continue
                rewritten_map[idx] = text[:96]
            if not rewritten_map:
                return selected
            merged: list[NarrativeItem] = []
            for index, item in enumerate(selected):
                merged.append(
                    NarrativeItem(
                        source=item.source,
                        text=rewritten_map.get(index, item.text),
                        emotion=item.emotion,
                        weight=item.weight,
                        age_label=item.age_label,
                    )
                )
            return merged
        except Exception as exc:
            _logger.debug("Bond narrative llm rewrite skipped: %s", exc)
            return selected


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _age_label(ts_ms: int, now_ms: int) -> str:
    """根据时间戳计算时间感知标签（v1.10 新增）。

    Args:
        ts_ms: 事件时间戳（毫秒）
        now_ms: 当前时间戳（毫秒）

    Returns:
        时间感知标签，如 "今天"/"上周"/"很久以前"
    """
    if ts_ms <= 0:
        return ""
    elapsed = max(0, now_ms - ts_ms)
    for threshold_ms, label in _AGE_THRESHOLDS_MS:
        if elapsed <= threshold_ms:
            return label
    return _AGE_LABEL_DEFAULT


def _compute_relationship_duration(first_ts_ms: int, now_ms: int) -> str:
    """根据首次互动时间戳计算关系时长描述（v1.10 新增）。

    Args:
        first_ts_ms: 首次互动时间戳（毫秒）
        now_ms: 当前时间戳（毫秒）

    Returns:
        关系时长描述，如 "认识一个月了"
    """
    if first_ts_ms <= 0:
        return ""
    elapsed_days = max(0, (now_ms - first_ts_ms) // (24 * 3600 * 1000))
    for threshold_days, label in _DURATION_THRESHOLDS:
        if elapsed_days <= threshold_days:
            return label
    return _DURATION_DEFAULT


def _wrap_memory(text: str, stage: str) -> str:
    """用叙事包装模板包装共同记忆文本（不带时间感知，向后兼容）。"""
    cleaned = str(text).strip()
    if len(cleaned) > 80:
        cleaned = cleaned[:80] + "…"

    wrappers = _NARRATIVE_WRAPPERS.get(stage) or _NARRATIVE_WRAPPERS.get("FRIEND", ["{memory}"])
    template = random.choice(wrappers)
    return template.replace("{memory}", cleaned)


def _wrap_memory_timed(text: str, stage: str, age_label: str) -> str:
    """用带时间感知的叙事包装模板包装共同记忆文本（v1.10 新增）。"""
    cleaned = str(text).strip()
    if len(cleaned) > 80:
        cleaned = cleaned[:80] + "…"

    wrappers = _NARRATIVE_WRAPPERS_TIMED.get(stage) or _NARRATIVE_WRAPPERS_TIMED.get("FRIEND", ["{age}你说过，{memory}"])
    template = random.choice(wrappers)
    return template.replace("{memory}", cleaned).replace("{age}", age_label)


def _wrap_milestone(narrative: str, stage: str) -> str:
    """用里程碑叙事包装模板包装里程碑文本（不带时间感知，向后兼容）。"""
    cleaned = str(narrative).strip()
    if len(cleaned) > 60:
        cleaned = cleaned[:60] + "…"

    wrappers = _MILESTONE_WRAPPERS.get(stage) or _MILESTONE_WRAPPERS.get("FRIEND", ["{milestone}"])
    template = random.choice(wrappers)
    return template.replace("{milestone}", cleaned)


def _wrap_milestone_timed(narrative: str, stage: str, age_label: str) -> str:
    """用带时间感知的里程碑叙事包装模板包装里程碑文本（v1.10 新增）。"""
    cleaned = str(narrative).strip()
    if len(cleaned) > 60:
        cleaned = cleaned[:60] + "…"

    wrappers = _MILESTONE_WRAPPERS_TIMED.get(stage) or _MILESTONE_WRAPPERS_TIMED.get("FRIEND", ["{age}我们{milestone}"])
    template = random.choice(wrappers)
    return template.replace("{milestone}", cleaned).replace("{age}", age_label)


def _compute_milestone_weight(milestone: Any, now_ts_ms: int) -> float:
    """计算里程碑的时间衰减权重。

    越近的里程碑权重越高（最高 0.9），越远的越低（最低 0.1）。
    使用指数衰减：weight = 0.1 + 0.8 * exp(-elapsed / half_life)
    """
    import math

    # 尝试从里程碑对象获取时间戳
    ts = getattr(milestone, "ts_ms", None) or getattr(milestone, "timestamp_ms", None)
    if ts is None or int(ts) <= 0:
        return 0.5  # 无时间信息时使用默认权重

    elapsed_ms = max(0, now_ts_ms - int(ts))
    # 指数衰减，半衰期 30 天
    decay = math.exp(-elapsed_ms / _MILESTONE_HALF_LIFE_MS * math.log(2))
    weight = 0.1 + 0.8 * decay
    return round(min(0.9, max(0.1, weight)), 4)


def _build_narrative_intro(stage: str, memory_count: int, milestone_count: int) -> str:
    """根据 bond_stage 和记忆数量生成叙事引导语。"""
    if memory_count == 0 and milestone_count == 0:
        return ""

    _INTROS: dict[str, list[str]] = {
        "STRANGER": ["我们刚开始相处时有些事我还记得"],
        "ACQUAINTANCE": ["我们之前聊过一些事情"],
        "FRIEND": [
            "我们有一些共同的记忆",
            "我记得我们聊过的一些事",
        ],
        "CLOSE_FRIEND": [
            "我们之间有很多我珍视的记忆",
            "我记得我们在一起的很多时刻",
            "我们共同经历了不少事情",
        ],
        "CONFIDANT": [
            "我们之间有很多珍贵的记忆，我都记着",
            "你跟我分享过很多，我一直放在心里",
            "我们一起走过了很多，我都记得",
        ],
        "SOULMATE": [
            "我们之间的每一段记忆对我来说都很珍贵",
            "你跟我说过的每一句话我都记着",
            "我们共同经历的那些时刻，是我最珍视的东西",
        ],
    }

    options = _INTROS.get(stage, [""])
    return random.choice(options)


def _now_ms() -> int:
    return int(time.time() * 1000)
