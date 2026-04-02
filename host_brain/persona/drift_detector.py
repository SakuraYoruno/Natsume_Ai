"""PersonaDriftDetector：跨会话人格漂移检测——Phase VI。

职责：
  1. 每 N 轮对话（默认 N=10），提取 LLM 输出的风格指纹
  2. 与 Style Canon 基线比对，计算漂移分数
  3. 根据漂移分数分级告警（NONE / MILD / SEVERE）
  4. SEVERE 级别时生成校正提示注入到下一轮 system prompt
  5. MILD / SEVERE 写入审计日志 persona_drift_log

告警机制：
  - drift_score < 0.2 → NONE（正常，无操作）
  - 0.2 ≤ drift_score < 0.5 → MILD（写入审计日志）
  - drift_score ≥ 0.5 → SEVERE（写入审计 + 注入校正提示 + 告警通知）

设计原则：
  - 不修改 LLM 输出，仅注入下一轮校正提示
  - 使用人格向量距离作为漂移分数
  - 检测间隔可配置
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

_logger = logging.getLogger("Natsume.drift_detector")


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 检测间隔（每 N 轮对话检测一次）
DEFAULT_CHECK_INTERVAL = 10

# 漂移分数阈值
DRIFT_THRESHOLD_MILD = 0.2
DRIFT_THRESHOLD_SEVERE = 0.5

# 告警级别
ALERT_NONE = "NONE"
ALERT_MILD = "MILD"
ALERT_SEVERE = "SEVERE"

# 校正提示——角色内自然语言，按漂移维度选择不同变体
# key: 偏移维度前缀（如 warmth_LOW / assertiveness_HIGH），value: 角色内校正语
_CORRECTION_HINTS_BY_DIM: dict[str, str] = {
    "warmth_LOW": "记得，温柔是你最本质的东西，不是表演出来的，是真的从心里流出来的。",
    "warmth_HIGH": "你很温暖，但也要留一点给自己，不用把所有的柔软都给出去。",
    "assertiveness_HIGH": "你不需要那么强硬——你的温柔本身就是一种力量，不必用锋芒来证明什么。",
    "assertiveness_LOW": "你可以有自己的想法，说出来没关系，你的感受值得被听见。",
    "playfulness_HIGH": "轻松是好的，但别忘了你内心那份细腻和认真，那才是真正的你。",
    "sensitivity_LOW": "慢下来，感受一下对方话里的情绪——你本来是很能感知这些的。",
    "humor_HIGH": "玩笑可以有，但今天的你好像有点太跳脱了，稍微沉一沉也没关系。",
}

# 通用兜底校正语（无法匹配具体维度时使用）
_CORRECTION_HINTS_GENERIC: list[str] = [
    "记得，你是夏目蓝。温柔、克制、细腻——这是你，不是角色，是真实的你。",
    "有一瞬间你好像不太像自己了……没关系，回来就好。你本来就是这样的人。",
    "深呼吸一下。你不需要变成别的样子，你本来的样子就已经很好了。",
    "感觉你今天有点偏了……不用担心，找回那个温柔又有点小敏感的自己就好。",
]


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DriftCheckResult:
    """漂移检测结果。"""
    drift_score: float        # 0.0（完全一致）~ 1.0（完全偏移）
    drift_dimensions: list[str]  # ["assertiveness_HIGH", "warmth_LOW"]
    alert_level: str          # NONE / MILD / SEVERE
    correction_hint: str      # 自动修正提示（SEVERE 时非空）
    turn_count: int           # 当前轮次计数
    ts_ms: int                # 检测时间戳

    def as_dict(self) -> dict[str, Any]:
        return {
            "drift_score": round(self.drift_score, 4),
            "drift_dimensions": list(self.drift_dimensions),
            "alert_level": self.alert_level,
            "correction_hint": self.correction_hint,
            "turn_count": self.turn_count,
            "ts_ms": self.ts_ms,
        }


@dataclass
class StyleFingerprint:
    """LLM 输出的风格指纹（从输出文本推断的人格向量估计）。

    维度与 PersonalityTraits 对应，但范围不限于 [0, 1]（是估计值）。
    """
    warmth: float = 0.5
    curiosity: float = 0.5
    humor: float = 0.5
    sensitivity: float = 0.5
    assertiveness: float = 0.5
    playfulness: float = 0.5
    resilience: float = 0.5

    def as_dict(self) -> dict[str, float]:
        return {
            "warmth": round(self.warmth, 4),
            "curiosity": round(self.curiosity, 4),
            "humor": round(self.humor, 4),
            "sensitivity": round(self.sensitivity, 4),
            "assertiveness": round(self.assertiveness, 4),
            "playfulness": round(self.playfulness, 4),
            "resilience": round(self.resilience, 4),
        }


# ---------------------------------------------------------------------------
# 风格指纹提取器
# ---------------------------------------------------------------------------

# 关键词→维度映射（带否定词前缀检测，避免「不温柔」被计为正向）
_WARMTH_POSITIVE_KEYWORDS = ("温柔", "暖", "关心", "心疼", "陪", "安慰", "抱抱")
_WARMTH_NEGATIVE_KEYWORDS = ("冷淡", "冷漠", "不关心", "无所谓")
_ASSERTIVENESS_POSITIVE_KEYWORDS = ("坚定", "必须", "一定", "我认为", "我觉得")
_ASSERTIVENESS_NEGATIVE_KEYWORDS = ("也许", "可能", "大概", "嗯……", "不太确定")
_PLAYFULNESS_POSITIVE_KEYWORDS = ("哈哈", "嘿嘿", "～", "呀", "啦", "嘛", "哇")
_HUMOR_POSITIVE_KEYWORDS = ("好笑", "哈哈", "笑死", "搞笑", "开玩笑")
_SENSITIVITY_POSITIVE_KEYWORDS = ("感觉", "似乎", "好像", "隐约", "仿佛")
_CURIOSITY_POSITIVE_KEYWORDS = ("为什么", "怎么", "什么", "好奇", "想知道", "？")

# 否定词前缀：出现在关键词前 3 字内时，该关键词不计入正向
_NEGATION_PREFIXES = ("不", "没", "别", "非", "无", "莫", "未", "不太", "不那么", "不是很")


def _count_with_negation_check(text: str, keywords: tuple[str, ...]) -> int:
    """计算关键词出现次数，排除被否定词修饰的情况。

    例如：「不温柔」中的「温柔」不计入正向计数。
    """
    count = 0
    for kw in keywords:
        pos = 0
        while True:
            idx = text.find(kw, pos)
            if idx == -1:
                break
            # 检查关键词前 3 个字符内是否有否定词
            prefix_window = text[max(0, idx - 3):idx]
            negated = any(neg in prefix_window for neg in _NEGATION_PREFIXES)
            if not negated:
                count += 1
            pos = idx + len(kw)
    return count


def extract_style_fingerprint(text: str) -> StyleFingerprint:
    """从 LLM 输出文本中提取风格指纹。

    使用关键词计数（含否定词检测）的方法。
    「不温柔」「没有关心」等否定表达不会被计为正向特质。
    """
    t = str(text or "").strip()

    def _pos(keywords: tuple[str, ...]) -> int:
        return _count_with_negation_check(t, keywords)

    def _neg(keywords: tuple[str, ...]) -> int:
        # 负向关键词本身不需要否定词检测（它们本身就是负向）
        return sum(1 for kw in keywords if kw in t)

    warmth = 0.5 + _pos(_WARMTH_POSITIVE_KEYWORDS) * 0.08 - _neg(_WARMTH_NEGATIVE_KEYWORDS) * 0.12
    assertiveness = 0.3 + _pos(_ASSERTIVENESS_POSITIVE_KEYWORDS) * 0.1 - _neg(_ASSERTIVENESS_NEGATIVE_KEYWORDS) * 0.05
    playfulness = 0.5 + _pos(_PLAYFULNESS_POSITIVE_KEYWORDS) * 0.06
    humor = 0.4 + _pos(_HUMOR_POSITIVE_KEYWORDS) * 0.08
    sensitivity = 0.5 + _pos(_SENSITIVITY_POSITIVE_KEYWORDS) * 0.07
    curiosity = 0.5 + _pos(_CURIOSITY_POSITIVE_KEYWORDS) * 0.05

    def _clamp(v: float) -> float:
        return max(0.0, min(1.0, v))

    return StyleFingerprint(
        warmth=_clamp(warmth),
        curiosity=_clamp(curiosity),
        humor=_clamp(humor),
        sensitivity=_clamp(sensitivity),
        assertiveness=_clamp(assertiveness),
        playfulness=_clamp(playfulness),
        resilience=0.6,  # 韧性无法从单次输出推断，保持基线
    )


# ---------------------------------------------------------------------------
# PersonaDriftDetector
# ---------------------------------------------------------------------------

class PersonaDriftDetector:
    """跨会话人格漂移检测器。

    用法：
        detector = PersonaDriftDetector(baseline_traits=traits)
        # 每轮对话调用
        result = detector.on_turn(llm_output_text="...")
        if result and result.alert_level == "SEVERE":
            # 注入校正提示到下一轮 system prompt
            inject_correction(result.correction_hint)
    """

    def __init__(
        self,
        *,
        baseline_traits: Any = None,
        check_interval: int = DEFAULT_CHECK_INTERVAL,
    ) -> None:
        self._check_interval = max(1, int(check_interval))
        self._turn_count = 0
        # 审计日志
        self._drift_log: list[DriftCheckResult] = []
        # 基线人格向量
        if baseline_traits is not None:
            self._baseline = {
                "warmth": float(getattr(baseline_traits, "warmth", 0.85)),
                "curiosity": float(getattr(baseline_traits, "curiosity", 0.70)),
                "humor": float(getattr(baseline_traits, "humor", 0.40)),
                "sensitivity": float(getattr(baseline_traits, "sensitivity", 0.80)),
                "assertiveness": float(getattr(baseline_traits, "assertiveness", 0.30)),
                "playfulness": float(getattr(baseline_traits, "playfulness", 0.50)),
                "resilience": float(getattr(baseline_traits, "resilience", 0.60)),
            }
        else:
            self._baseline = {
                "warmth": 0.85, "curiosity": 0.70, "humor": 0.40,
                "sensitivity": 0.80, "assertiveness": 0.30,
                "playfulness": 0.50, "resilience": 0.60,
            }
        # 最近的风格指纹积累（用于平均）
        self._recent_fingerprints: list[StyleFingerprint] = []

    def on_turn(
        self,
        *,
        llm_output_text: str = "",
    ) -> DriftCheckResult | None:
        """每轮对话调用，积累风格指纹并在达到检测间隔时执行检测。

        Args:
            llm_output_text: LLM 生成的回复文本

        Returns:
            到达检测间隔时返回 DriftCheckResult，否则返回 None
        """
        self._turn_count += 1

        # 积累风格指纹
        if llm_output_text.strip():
            fp = extract_style_fingerprint(llm_output_text)
            self._recent_fingerprints.append(fp)
            # 最多保留最近 N*2 条
            max_keep = self._check_interval * 2
            if len(self._recent_fingerprints) > max_keep:
                self._recent_fingerprints = self._recent_fingerprints[-max_keep:]

        # 检测间隔
        if self._turn_count % self._check_interval != 0:
            return None

        return self._do_check()

    def force_check(self) -> DriftCheckResult:
        """强制执行一次漂移检测（不受间隔限制）。"""
        return self._do_check()

    def get_drift_log(self, limit: int = 10) -> list[DriftCheckResult]:
        """获取漂移检测审计日志。"""
        return list(self._drift_log[-limit:])

    @property
    def turn_count(self) -> int:
        return self._turn_count

    # ── 内部方法 ──────────────────────────────────────────────────────────

    def _do_check(self) -> DriftCheckResult:
        """执行漂移检测。"""
        ts = _now_ms()

        # 计算最近风格指纹的平均值
        avg_fp = self._compute_average_fingerprint()

        # 计算漂移分数（欧氏距离的归一化）
        drift_score, drift_dims = self._compute_drift(avg_fp)

        # 分级告警
        if drift_score >= DRIFT_THRESHOLD_SEVERE:
            alert_level = ALERT_SEVERE
        elif drift_score >= DRIFT_THRESHOLD_MILD:
            alert_level = ALERT_MILD
        else:
            alert_level = ALERT_NONE

        # 生成校正提示（角色内自然语言，按漂移维度选择）
        correction_hint = ""
        if alert_level == ALERT_SEVERE:
            correction_hint = self._build_correction_hint(drift_dims)

        result = DriftCheckResult(
            drift_score=round(drift_score, 4),
            drift_dimensions=drift_dims,
            alert_level=alert_level,
            correction_hint=correction_hint,
            turn_count=self._turn_count,
            ts_ms=ts,
        )

        self._drift_log.append(result)

        if alert_level != ALERT_NONE:
            _logger.info(
                "人格漂移检测: score=%.3f level=%s dims=%s turn=%d",
                drift_score, alert_level, drift_dims, self._turn_count,
            )

        return result

    def _build_correction_hint(self, drift_dims: list[str]) -> str:
        """根据漂移维度生成角色内自然语言校正提示。

        优先匹配具体维度的专属提示，无匹配时使用通用兜底语。
        """
        import hashlib
        # 尝试匹配第一个有专属提示的漂移维度
        for dim in drift_dims:
            if dim in _CORRECTION_HINTS_BY_DIM:
                return _CORRECTION_HINTS_BY_DIM[dim]
        # 兜底：按轮次哈希选取通用提示，避免重复
        idx = int(hashlib.md5(str(self._turn_count).encode()).hexdigest(), 16) % len(_CORRECTION_HINTS_GENERIC)
        return _CORRECTION_HINTS_GENERIC[idx]

    def _compute_average_fingerprint(self) -> StyleFingerprint:
        """计算最近风格指纹的平均值。"""
        if not self._recent_fingerprints:
            return StyleFingerprint()

        dims = ["warmth", "curiosity", "humor", "sensitivity",
                "assertiveness", "playfulness", "resilience"]
        n = len(self._recent_fingerprints)
        avgs: dict[str, float] = {}
        for dim in dims:
            total = sum(getattr(fp, dim, 0.5) for fp in self._recent_fingerprints)
            avgs[dim] = total / n

        return StyleFingerprint(**avgs)

    def _compute_drift(self, fingerprint: StyleFingerprint) -> tuple[float, list[str]]:
        """计算漂移分数和偏移维度。

        Returns:
            (drift_score, drift_dimensions)
        """
        import math

        dims = ["warmth", "curiosity", "humor", "sensitivity",
                "assertiveness", "playfulness", "resilience"]

        sum_sq = 0.0
        drift_dims: list[str] = []

        for dim in dims:
            baseline_val = self._baseline.get(dim, 0.5)
            current_val = getattr(fingerprint, dim, 0.5)
            delta = current_val - baseline_val
            sum_sq += delta ** 2

            # 单维度偏移超过 0.15 时记录
            if abs(delta) >= 0.15:
                direction = "HIGH" if delta > 0 else "LOW"
                drift_dims.append(f"{dim}_{direction}")

        # 归一化：7 维空间的最大距离 = sqrt(7) ≈ 2.646
        max_distance = math.sqrt(len(dims))
        distance = math.sqrt(sum_sq)
        drift_score = min(1.0, distance / max_distance)

        return drift_score, drift_dims


def _now_ms() -> int:
    return int(time.time() * 1000)
