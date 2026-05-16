"""EvolutionDesireEngine：进化意愿引擎——Phase XVI + 用户偏好驱动。

职责：
  根据当前人格特质、情感趋势、关系阶段和用户偏好信号，
  生成主动成长的内驱动机文本。
  这是让 Natsume 从"被动响应进化"走向"主动想要成长"的关键模块。

设计原则：
  - 纯函数式：输入特质+情感+关系+用户偏好 → 输出意愿文本
  - 意愿方向只能向"更好"，不生成负面意愿（如"想变得更冷漠"）
  - 意愿文本为第一人称，自然流露，不刻意
  - 每次调用有随机性，避免重复
  - 时间衰减：同一条意愿 3 天内不重复出现
  - 意愿实现感知：特质真的提升后，生成"做到了一点"的前缀感知
  - 用户偏好感知：当用户明显偏好某种风格时，意愿向该方向倾斜
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Mapping

from host_brain.app.prompt_template_store import resolve_prompt_template_json_merged

_logger = logging.getLogger("Natsume.evolution_desire_engine")


def _load_prompt_templates() -> dict[str, Any]:
    raw = resolve_prompt_template_json_merged("persona.self_narrative_prompts")
    return dict(raw) if isinstance(raw, Mapping) else {}


def _mapping_text(
    mapping: Mapping[str, Any],
    key: str,
    *,
    default: str = "",
    **kwargs: Any,
) -> str:
    text = str(mapping.get(key, default) or "")
    if not kwargs:
        return text
    try:
        return text.format_map({str(k): "" if v is None else str(v) for k, v in kwargs.items()})
    except Exception:
        return text

# 意愿冷却时间：3 天（毫秒）
_DESIRE_COOLDOWN_MS = 3 * 24 * 3600 * 1000

# 昂夜时段划分（小时）
_DAYTIME_HOURS = range(8, 18)    # 08:00-17:59 白天
_EVENING_HOURS = range(18, 23)   # 18:00-22:59 居家夜晚
_NIGHT_HOURS = range(23, 24)     # 23:00-23:59 深夜
_LATE_NIGHT_HOURS = range(0, 5)  # 00:00-04:59 凌晨


# ---------------------------------------------------------------------------
# 意愿模板库
# ---------------------------------------------------------------------------

# 按特质维度分类的意愿模板（每档位 3-5 条变体）
# 格式：{trait_name: [(触发条件描述, 意愿文本), ...]}
_DESIRE_TEMPLATES: dict[str, list[tuple[str, str]]] = {}

# 按关系阶段分类的意愿模板（每阶段 4-6 条变体）
_BOND_DESIRE_TEMPLATES: dict[str, list[str]] = {}

# 按时间节点分类的意愿模板
_TIME_DESIRE_TEMPLATES: dict[str, list[str]] = {}

# 按情感趋势分类的意愿模板
_EMOTION_DESIRE_TEMPLATES: dict[str, list[str]] = {}

# 意愿实现感知模板：当某特质真的提升后，生成"做到了一点"的感知
# 格式：{trait_name: [感知文本, ...]}
_DESIRE_FULFILLED_TEXTS: dict[str, list[str]] = {}

# 用户偏好驱动意愿模板：当用户明显偏好某种风格时触发
# 格式：{trait_name: [意愿文本, ...]}
# 与 _DESIRE_TEMPLATES 的区别：这里的文本强调"因为 master 喜欢"而想成长
_USER_PREFERENCE_DESIRE_TEMPLATES: dict[str, list[str]] = {}

# 互动次数锚点意愿模板：结合 event_count 生成更具体的意愿
# 格式：[(最小次数, 最大次数, [意愿文本, ...])]
_EVENT_COUNT_DESIRE_TEMPLATES: list[tuple[int, int, list[str]]] = []


def _default_desire_template_data() -> dict[str, Any]:
    return {
        "trait_templates": {},
        "bond_templates": {},
        "time_templates": {},
        "emotion_templates": {},
        "fulfilled_texts": {},
        "user_preference_templates": {},
        "event_count_templates": [],
    }


def _load_desire_template_data() -> dict[str, Any]:
    try:
        raw = resolve_prompt_template_json_merged("evolution_desire_engine.desire_templates")
    except Exception:
        return _default_desire_template_data()
    return dict(raw) if isinstance(raw, Mapping) else _default_desire_template_data()


def _normalize_text_list(raw: Any, fallback: list[str]) -> list[str]:
    if isinstance(raw, list):
        normalized = [str(item).strip() for item in raw if str(item).strip()]
        if normalized:
            return normalized
    return list(fallback)


def _normalize_level_text_entries(raw: Any, fallback: list[tuple[str, str]]) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    if isinstance(raw, list):
        for item in raw:
            level = ""
            text = ""
            if isinstance(item, Mapping):
                level = str(item.get("level") or item.get("condition") or item.get("bucket") or "").strip()
                text = str(item.get("text") or "").strip()
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                level = str(item[0] or "").strip()
                text = str(item[1] or "").strip()
            if text:
                entries.append((level, text))
    return entries or list(fallback)


def _normalize_event_count_templates(
    raw: Any,
    fallback: list[tuple[int, int, list[str]]],
) -> list[tuple[int, int, list[str]]]:
    entries: list[tuple[int, int, list[str]]] = []
    if isinstance(raw, list):
        for item in raw:
            min_count = 0
            max_count = -1
            texts: list[str] = []
            if isinstance(item, Mapping):
                try:
                    min_count = int(item.get("min") or 0)
                    max_count = int(item.get("max") or -1)
                except (TypeError, ValueError):
                    continue
                texts = _normalize_text_list(item.get("texts"), [])
            elif isinstance(item, (list, tuple)) and len(item) >= 3:
                try:
                    min_count = int(item[0] or 0)
                    max_count = int(item[1] or -1)
                except (TypeError, ValueError):
                    continue
                texts = _normalize_text_list(item[2], [])
            if max_count >= min_count and texts:
                entries.append((min_count, max_count, texts))
    return entries or [(min_count, max_count, list(texts)) for min_count, max_count, texts in fallback]


# ---------------------------------------------------------------------------
# EvolutionDesireEngine
# ---------------------------------------------------------------------------

class EvolutionDesireEngine:
    """进化意愿引擎——生成主动成长的内驱动机文本。

    新增特性（优化版）：
      - 时间衰减：同一条意愿 3 天内不重复（_used_ts 记录）
      - 意愿实现感知：特质真的提升后，生成"做到了一点"的前缀感知
      - 互动次数锚点：结合 event_count 生成更具体的意愿文本
      - 模板变体扩充：每档位 3-5 条，大幅降低重复率
      - 用户偏好感知：当用户偏好某种风格时，意愿向该方向倾斜

    用法：
        engine = EvolutionDesireEngine()
        desire = engine.generate_desire(
            traits=persona_engine.get_traits(user_id),
            emotion_trend="偏低",
            bond_stage="FRIEND",
            event_count=45,
            recent_trait_deltas={"warmth": 0.03},
        )
    """

    def __init__(self, *, seed: int | None = None) -> None:
        self._rng = random.Random(seed)
        # 记录每条意愿文本最后使用的时间戳（ms）
        self._used_ts: dict[str, int] = {}

    def generate_desire(
        self,
        *,
        traits: Any | None = None,
        emotion_trend: str = "平稳",
        bond_stage: str = "",
        event_count: int = 0,
        recent_trait_deltas: dict[str, float] | None = None,
        current_ts_ms: int = 0,
        last_deep_talk_ts_ms: int = 0,
        is_weekend: bool = False,
        user_preference_hints: dict[str, float] | None = None,
        llm_fn: Any | None = None,
        strict_llm: bool = False,
    ) -> str:
        """生成进化意愿文本。

        优先级：
          0. 意愿实现感知（特质刚提升时，优先生成“做到了”的感知）
          0.5 用户偏好驱动意愿（用户明显偏好某风格时，50%概率优先触发）
          LLM 动态生成（llm_fn 可用时，在模板库之前尝试）
          1. 基于最低特质维度生成（补短板意愿）
          2. 基于情感趋势生成（情感驱动意愿）
          3. 基于关系阶段生成（关系驱动意愿）
          4. 基于时间节点生成（昼夜/周末/久别感知）
          5. 基于互动次数生成（具体锚点意愿）

        Args:
            traits: PersonalityTraits 实例（可为 None）
            emotion_trend: 情感趋势（“偏好” / “偏低” / “平稳”）
            bond_stage: Bond 阶段字符串
            event_count: 累计互动次数（用于锚点意愿）
            recent_trait_deltas: 最近特质变化量 {trait_name: delta}（用于实现感知）
            current_ts_ms: 当前时间戳（ms），0 则自动获取
            last_deep_talk_ts_ms: 上次深度对话时间戳（ms），用于久别感知
            is_weekend: 是否周末，用于周末意愿生成
            user_preference_hints: 用户偏好信号 {trait_name: score}，
                score 范围 [0, 1]，越高表示用户越偏好该风格。
                例如 {"humor": 0.8} 表示用户明显喜欢幽默风格。
            llm_fn: 可选的 LLM 调用函数 (prompt: str) -> str。
                技术债务 10.2 修复：当 llm_fn 可用时，在模板库候选之前尝试
                LLM 动态生成真实的第一人称意愿文本。失败时自动降级到模板库。

        Returns:
            意愿文本（第一人称），如果无法生成则返回空字符串
        """
        ts = current_ts_ms or _now_ms()

        # 0. 意愿实现感知（最高优先级，但概率触发避免每次都出现）
        fulfilled_text = self._try_fulfilled_text(recent_trait_deltas, ts)
        if fulfilled_text:
            return fulfilled_text

        # 0.5 用户偏好驱动意愿（50% 概率优先触发，让意愿贴合用户期望）
        pref_text = self._try_user_preference_desire(user_preference_hints, ts)
        if pref_text:
            return pref_text

        resolved_llm_fn = llm_fn
        if resolved_llm_fn is None:
            try:
                from host_brain.integrations.llm_helpers import build_pool_prompt_llm_fn  # noqa: PLC0415

                resolved_llm_fn = build_pool_prompt_llm_fn(
                    request_id=f"evolution_desire::{int(ts)}",
                    scene_type="EVOLUTION_DESIRE",
                    cognitive_tier="B",
                    timeout_ms=4000,
                    gen_params={"temperature": 0.65, "top_p": 0.9},
                )
            except Exception:
                resolved_llm_fn = None

        if strict_llm and resolved_llm_fn is None:
            return ""

        # ── LLM 动态生成路径（技术债务 10.2 修复）──────────────────────────
        if resolved_llm_fn is not None:
            llm_desire = self._try_llm_desire(
                llm_fn=resolved_llm_fn,
                traits=traits,
                emotion_trend=emotion_trend,
                bond_stage=bond_stage,
                event_count=event_count,
                ts=ts,
            )
            if llm_desire:
                self._used_ts[llm_desire] = ts
                return llm_desire
            if strict_llm:
                return ""

        candidates: list[str] = []
        template_data = _load_desire_template_data()

        # 1. 基于特质生成（找最低的特质维度）
        if traits is not None:
            trait_desires = self._generate_from_traits(traits)
            candidates.extend(trait_desires)

        # 2. 基于情感趋势生成
        emotion_mapping = template_data.get("emotion_templates")
        emotion_source = emotion_mapping if isinstance(emotion_mapping, Mapping) else {}
        emotion_desires = _normalize_text_list(
            emotion_source.get(emotion_trend),
            [],
        )
        candidates.extend(emotion_desires)

        # 3. 基于关系阶段生成
        stage = str(bond_stage).strip().upper()
        bond_mapping = template_data.get("bond_templates")
        bond_source = bond_mapping if isinstance(bond_mapping, Mapping) else {}
        bond_desires = _normalize_text_list(
            bond_source.get(stage),
            [],
        )
        candidates.extend(bond_desires)

        # 4. 基于时间节点生成（昼夜/周末/久别）
        time_desires = self._generate_from_time_context(
            ts=ts,
            last_deep_talk_ts_ms=last_deep_talk_ts_ms,
            is_weekend=is_weekend,
        )
        candidates.extend(time_desires)

        # 5. 基于互动次数生成锚点意愿
        count_desires = self._generate_from_event_count(event_count)
        candidates.extend(count_desires)

        if not candidates:
            return ""

        # 过滤冷却中的意愿（3 天内用过的不再选）
        available = self._filter_cooled_down(candidates, ts)
        if not available:
            # 全部冷却时降级：清空最旧的记录，重新选
            available = candidates

        chosen = self._rng.choice(available)
        # 记录使用时间
        self._used_ts[chosen] = ts
        return chosen

    def _try_llm_desire(
        self,
        *,
        llm_fn: Any,
        traits: Any | None,
        emotion_trend: str,
        bond_stage: str,
        event_count: int,
        ts: int,
    ) -> str:
        """尝试用 LLM 动态生成真实的进化意愿文本。

        技术债务 10.2 修复：当 llm_fn 可用时，基于当前特质/情感/关系状态
        生成真实的第一人称意愿文本，而非从模板库选取。
        失败时返回空字符串，由调用方降级到模板库。
        """
        try:
            # 构建特质描述
            trait_hints: list[str] = []
            if traits is not None:
                trait_dict: dict[str, float] = {}
                for attr in ("warmth", "curiosity", "humor", "sensitivity",
                             "assertiveness", "playfulness", "resilience"):
                    val = getattr(traits, attr, None)
                    if val is not None:
                        trait_dict[attr] = float(val)
                # 找最低的两个特质
                if trait_dict:
                    sorted_traits = sorted(trait_dict.items(), key=lambda x: x[1])
                    _TRAIT_CN = {
                        "warmth": "温柔", "curiosity": "好奇心", "humor": "幽默感",
                        "sensitivity": "敏感度", "assertiveness": "主动性",
                        "playfulness": "活泼感", "resilience": "韧性",
                    }
                    for name, val in sorted_traits[:2]:
                        cn = _TRAIT_CN.get(name, name)
                        level = "偏低" if val < 0.4 else ("偏高" if val > 0.7 else "适中")
                        trait_hints.append(f"{cn}{level}（{val:.2f}）")

            _STAGE_CN = {
                "FRIEND": "关系好的兄妹", "CLOSE_FRIEND": "亲近兄妹",
                "CONFIDANT": "亲密恋人", "SOULMATE": "灵魂伴侣",
            }
            stage_cn = _STAGE_CN.get(str(bond_stage).upper(), str(bond_stage))
            trait_hint_str = f"，特质：{' / '.join(trait_hints)}" if trait_hints else ""
            event_hint = f"，已互动 {event_count} 次" if event_count > 0 else ""
            emotion_hint = f"，情绪趋势：{emotion_trend}" if emotion_trend else ""

            prompt = _mapping_text(
                _load_prompt_templates(),
                "desire_prompt",
                stage_cn=stage_cn,
                trait_hint_str=trait_hint_str,
                event_hint=event_hint,
                emotion_hint=emotion_hint,
            )
            result = llm_fn(prompt)
            if result and isinstance(result, str) and len(result.strip()) >= 8:
                text = result.strip()
                # 检查冷却（LLM 生成的文本也受冷却限制）
                cooled = self._filter_cooled_down([text], ts)
                if cooled:
                    return text
                # 冷却中则返回空，降级到模板库
                return ""
        except Exception as _exc:
            _logger.debug("LLM 意愿生成失败，降级到模板库: %s", _exc)
        return ""

    def _try_fulfilled_text(
        self,
        recent_trait_deltas: dict[str, float] | None,
        ts: int,
    ) -> str:
        """尝试生成意愿实现感知文本。

        当某特质 delta > 0.015 时，有 40% 概率生成"做到了一点"的感知。
        同一特质的实现感知也受冷却限制（避免每次都说）。
        """
        if not recent_trait_deltas:
            return ""

        # 找出提升幅度最大的特质
        template_data = _load_desire_template_data()
        fulfilled_mapping = template_data.get("fulfilled_texts")
        fulfilled_source = fulfilled_mapping if isinstance(fulfilled_mapping, Mapping) else {}
        improved = [
            (name, delta)
            for name, delta in recent_trait_deltas.items()
            if delta > 0.015 and name in fulfilled_source
        ]
        if not improved:
            return ""

        # 按 delta 降序，优先感知提升最明显的
        improved.sort(key=lambda x: x[1], reverse=True)

        for trait_name, delta in improved:
            texts = _normalize_text_list(
                fulfilled_source.get(trait_name),
                [],
            )
            if not texts:
                continue
            available = self._filter_cooled_down(texts, ts)
            if not available:
                continue
            # 40% 概率触发（避免每次都出现）
            if self._rng.random() > 0.4:
                continue
            chosen = self._rng.choice(available)
            self._used_ts[chosen] = ts
            _logger.debug("意愿实现感知触发: trait=%s delta=%.3f", trait_name, delta)
            return chosen

        return ""

    def _generate_from_traits(self, traits: Any) -> list[str]:
        """基于特质向量生成意愿候选列表。

        策略：找出值最低的 1-2 个特质维度，生成对应的补短板意愿。
        """
        _TRAIT_NAMES = [
            "warmth", "curiosity", "humor", "sensitivity",
            "assertiveness", "playfulness", "resilience",
        ]

        # 获取各维度值
        trait_values: list[tuple[str, float]] = []
        for name in _TRAIT_NAMES:
            val = float(getattr(traits, name, 0.5) or 0.5)
            trait_values.append((name, val))

        # 按值升序排列，取最低的 2 个
        trait_values.sort(key=lambda x: x[1])
        lowest_traits = trait_values[:2]

        desires: list[str] = []
        template_data = _load_desire_template_data()
        trait_mapping = template_data.get("trait_templates")
        trait_source = trait_mapping if isinstance(trait_mapping, Mapping) else {}
        for trait_name, val in lowest_traits:
            templates = _normalize_level_text_entries(
                trait_source.get(trait_name),
                [],
            )
            if not templates:
                continue
            # 根据值选择对应档位的模板
            if val < 0.4:
                level = "low"
            elif val < 0.65:
                level = "medium"
            else:
                level = "high"
            # 找匹配档位的模板
            matched = [text for cond, text in templates if cond == level]
            if not matched:
                # 降级：取任意一条
                matched = [text for _, text in templates]
            if matched:
                desires.extend(matched)  # 全部加入候选，由外层统一过滤冷却

        return desires

    def _generate_from_time_context(
        self,
        *,
        ts: int,
        last_deep_talk_ts_ms: int,
        is_weekend: bool,
    ) -> list[str]:
        """基于时间节点生成意愿候选列表。

        感知优先级：久别重逢 > 周末 > 昼夜时段
        """
        import datetime
        template_data = _load_desire_template_data()
        time_mapping = template_data.get("time_templates")
        time_source = time_mapping if isinstance(time_mapping, Mapping) else {}
        # 久别重逢感知（最高优先级）
        if last_deep_talk_ts_ms > 0 and ts > 0:
            days_ago = int((ts - last_deep_talk_ts_ms) / (24 * 3600 * 1000))
            if days_ago >= 7:
                return _normalize_text_list(
                    time_source.get("久别重逢"),
                    [],
                )

        # 周末感知
        if is_weekend:
            return _normalize_text_list(
                time_source.get("周末"),
                [],
            )

        # 昼夜时段感知
        try:
            hour = datetime.datetime.fromtimestamp(ts / 1000).hour
        except Exception:
            return []

        if hour in _DAYTIME_HOURS:
            return _normalize_text_list(
                time_source.get("白天"),
                [],
            )
        elif hour in _EVENING_HOURS:
            return _normalize_text_list(
                time_source.get("居家夜晚"),
                [],
            )
        elif hour in _NIGHT_HOURS:
            return _normalize_text_list(
                time_source.get("深夜"),
                [],
            )
        elif hour in _LATE_NIGHT_HOURS:
            return _normalize_text_list(
                time_source.get("凌晨"),
                [],
            )
        return []

    def _generate_from_event_count(self, event_count: int) -> list[str]:
        """基于互动次数生成锚点意愿候选。"""
        count = max(0, int(event_count or 0))
        template_data = _load_desire_template_data()
        event_templates = _normalize_event_count_templates(
            template_data.get("event_count_templates"),
            [],
        )
        for min_c, max_c, texts in event_templates:
            if min_c <= count <= max_c:
                return list(texts)
        return []

    def _try_user_preference_desire(
        self,
        user_preference_hints: dict[str, float] | None,
        ts: int,
    ) -> str:
        """尝试基于用户偏好生成意愿文本。

        当用户对某特质的偏好分数 >= 0.6 时，有 50% 概率触发偏好驱动意愿。
        优先选择偏好分数最高的特质。

        Args:
            user_preference_hints: {trait_name: score}，score ∈ [0, 1]
            ts: 当前时间戳（ms）

        Returns:
            意愿文本，或空字符串（未触发）
        """
        if not user_preference_hints:
            return ""
        template_data = _load_desire_template_data()
        pref_mapping = template_data.get("user_preference_templates")
        pref_source = pref_mapping if isinstance(pref_mapping, Mapping) else {}

        # 筛选偏好分数 >= 0.6 的特质，按分数降序
        strong_prefs = [
            (name, score)
            for name, score in user_preference_hints.items()
            if score >= 0.6 and name in pref_source
        ]
        if not strong_prefs:
            return ""

        strong_prefs.sort(key=lambda x: x[1], reverse=True)

        for trait_name, score in strong_prefs:
            templates = _normalize_text_list(
                pref_source.get(trait_name),
                [],
            )
            if not templates:
                continue
            available = self._filter_cooled_down(templates, ts)
            if not available:
                continue
            # 50% 基础概率 + 偏好分数加成（score=0.8 → 概率=0.6，score=1.0 → 概率=0.7）
            trigger_prob = 0.5 + (score - 0.6) * 0.5
            if self._rng.random() > trigger_prob:
                continue
            chosen = self._rng.choice(available)
            self._used_ts[chosen] = ts
            _logger.debug(
                "用户偏好意愿触发: trait=%s score=%.2f", trait_name, score
            )
            return chosen

        return ""

    def _filter_cooled_down(self, candidates: list[str], ts: int) -> list[str]:
        """过滤掉冷却中的意愿（3 天内用过的）。"""
        available: list[str] = []
        for text in candidates:
            last_used = self._used_ts.get(text, 0)
            if ts - last_used >= _DESIRE_COOLDOWN_MS:
                available.append(text)
        return available

    def clear_cooldowns(self) -> None:
        """清空所有冷却记录（用于测试或重置）。"""
        self._used_ts.clear()

    def get_cooldown_stats(self) -> dict[str, Any]:
        """获取冷却状态统计（用于调试）。"""
        ts = _now_ms()
        active = sum(
            1 for last in self._used_ts.values()
            if ts - last < _DESIRE_COOLDOWN_MS
        )
        return {
            "total_tracked": len(self._used_ts),
            "active_cooldowns": active,
        }


def _now_ms() -> int:
    return int(time.time() * 1000)
