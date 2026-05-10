"""自我反思链（Phase XI 方案 1）。

职责：Generate → Reflect → Revise 循环，在回复输出前进行结构化自我评估。
设计：可选启用，仅在高风险场景开启（情绪负面/深度对话）。
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

_logger = logging.getLogger("Natsume.reasoning.reflection_chain")

# ---------------------------------------------------------------------------
# 评估维度
# ---------------------------------------------------------------------------
DIMENSION_EMOTION_FIT = "emotion_fit"       # 情绪契合度
DIMENSION_CANON_COMPLIANCE = "canon_compliance"  # Canon 合规性
DIMENSION_STYLE_CONSISTENCY = "style_consistency"  # 风格一致性
DIMENSION_INFO_ACCURACY = "info_accuracy"    # 信息准确性
DIMENSION_LENGTH_APPROPRIATE = "length_appropriate"  # 长度适当性

ALL_DIMENSIONS = (
    DIMENSION_EMOTION_FIT,
    DIMENSION_CANON_COMPLIANCE,
    DIMENSION_STYLE_CONSISTENCY,
    DIMENSION_INFO_ACCURACY,
    DIMENSION_LENGTH_APPROPRIATE,
)

# ---------------------------------------------------------------------------
# Canon 违规检测模式
# ---------------------------------------------------------------------------
_AI_SELF_REF_PATTERN = re.compile(
    r"(我是(一个|一名)?AI|人工智能|虚拟助手|语言模型|大模型|ChatGPT|GPT|LLM|"
    r"作为(一个)?AI|作为人工智能|我没有感情|我没有情感|我是程序|我是系统|"
    r"仮想アシスタント|AIです|人工知能)",
    re.IGNORECASE,
)
_CUSTOMER_SERVICE_PATTERN = re.compile(
    r"(您好，请问|有什么可以帮助您|感谢您的|期待您的|如有问题|请稍等|"
    r"为您服务|客服|工单|反馈渠道)",
)
_SYSTEM_STATUS_PATTERN = re.compile(
    r"(系统(正在|已经)|功能(暂时|目前)|版本号|更新日志|服务器|接口|API)",
)
_FALLBACK_GREETING_RE = re.compile(r"^(你好|你好啊|你好呀|您好|嗨|哈喽|早上好|中午好|晚上好)[!！。…~]*$")
_FALLBACK_PRESENCE_RE = re.compile(r"^(在吗|在不在|还在吗|忙吗|在嘛)[?？!！。…~]*$")

# ---------------------------------------------------------------------------
# 触发条件
# ---------------------------------------------------------------------------
HIGH_RISK_EMOTION_TAGS = frozenset({
    "sadness", "sad", "anger", "angry", "fear", "afraid", "disgust", "anxiety",
    "loneliness", "frustration", "grief", "despair",
})
HIGH_RISK_INTENTS = frozenset({
    "emotion", "recall_memory", "complex",
})

_AFFECTION_QUESTION_MARKERS = (
    "你喜欢我吗",
    "喜欢我吗",
    "你会喜欢我吗",
    "你爱我吗",
    "你在意我吗",
)
_AFFECTION_DISCLOSURE_MARKERS = (
    "我喜欢你",
    "我好喜欢你",
    "我真的喜欢你",
    "我爱你",
    "我想你",
)
_AFFECTION_STANCE_MARKERS = (
    "喜欢你",
    "在意你",
    "有好感",
    "心动",
    "会认真对待",
    "会认真记住",
    "会认真记着",
    "慢慢走近",
    "慢慢了解",
    "还太早",
    "还没到",
    "不想把话说得太满",
    "不想把话说得很满",
    "把话说得很满",
)
_AFFECTION_EVASION_MARKERS = (
    "我在这里",
    "珍惜能这样和你说话",
    "能这样和你说话",
    "很高兴能这样和你说话",
)


def _is_affection_question(user_message: str) -> bool:
    normalized = str(user_message or "").strip()
    return any(marker in normalized for marker in _AFFECTION_QUESTION_MARKERS)


def _is_affection_disclosure(user_message: str) -> bool:
    normalized = str(user_message or "").strip()
    return any(marker in normalized for marker in _AFFECTION_DISCLOSURE_MARKERS)


def _contains_affection_stance(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    if any(marker in normalized for marker in _AFFECTION_STANCE_MARKERS):
        return True
    if "不讨厌" in normalized and "你" in normalized:
        return True
    if "现在" in normalized and ("太早" in normalized or "说得太满" in normalized or "说得很满" in normalized):
        return True
    if "我做不到" in normalized and ("说得太满" in normalized or "说得很满" in normalized):
        return True
    return False


def _looks_like_affection_evasion(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    if any(marker in normalized for marker in _AFFECTION_EVASION_MARKERS):
        return True
    if normalized.startswith("嗯……谢谢你") and not _contains_affection_stance(normalized):
        return True
    return False

def _load_reflection_llm_templates() -> dict[str, Any]:
    """Legacy hook retained for tests/import stability.

    Reflection is no longer an authoring path. Returning an empty mapping keeps
    old LLM-reflection template lookups disabled unless a unit test explicitly
    injects a fake payload into the private hook.
    """
    return {}


def _coerce_score(value: Any, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return float(default)


def _merge_issue_lists(*lists: list[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for items in lists:
        for item in list(items or []):
            normalized = str(item or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            merged.append(normalized)
    return merged


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class DimensionScore:
    """单个评估维度的分数。"""
    dimension: str
    score: float  # [0.0, 1.0]
    issues: list[str] = field(default_factory=list)


@dataclass
class ReflectionResult:
    """反思评估结果。"""
    overall_score: float  # [0.0, 1.0]
    passed: bool
    dimension_scores: list[DimensionScore] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    revision_hints: list[str] = field(default_factory=list)
    should_revise: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "overall_score": round(self.overall_score, 3),
            "passed": self.passed,
            "should_revise": self.should_revise,
            "dimensions": {
                d.dimension: round(d.score, 3)
                for d in self.dimension_scores
            },
            "issues_count": len(self.issues),
            "revision_hints": list(self.revision_hints[:3]),
        }


@dataclass
class RevisionResult:
    """Deterministic revision result produced after reflection."""

    revised_text: str
    changed: bool
    operations: list[str] = field(default_factory=list)
    reflection_before: ReflectionResult | None = None
    reflection_after: ReflectionResult | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "changed": self.changed,
            "operations": list(self.operations[:6]),
            "before": self.reflection_before.as_dict() if self.reflection_before else None,
            "after": self.reflection_after.as_dict() if self.reflection_after else None,
        }


# ---------------------------------------------------------------------------
# ReflectionChain
# ---------------------------------------------------------------------------
class ReflectionChain:
    """Generate → Reflect → Revise 自我反思链。

    用法::

        chain = ReflectionChain()

        # 1. 判断是否需要反思
        if chain.should_reflect(emotion_tag="sadness", intent="emotion"):
            # 2. 评估回复质量
            result = chain.reflect(
                user_message="我今天心情很不好",
                ai_response="没关系的，我在这里陪你。",
                emotion_tag="sadness",
                bond_stage="CLOSE_FRIEND",
            )
            if result.should_revise:
                # 3. 生成修正提示给 LLM 重新生成
                hints = result.revision_hints
    """

    def __init__(
        self,
        *,
        pass_threshold: float = 0.75,
        weights: dict[str, float] | None = None,
        use_llm: bool = False,
        llm_timeout_ms: int = 2800,
    ) -> None:
        self._threshold = float(pass_threshold)
        self._weights = dict(weights or {
            DIMENSION_EMOTION_FIT: 0.30,
            DIMENSION_CANON_COMPLIANCE: 0.25,
            DIMENSION_STYLE_CONSISTENCY: 0.20,
            DIMENSION_INFO_ACCURACY: 0.15,
            DIMENSION_LENGTH_APPROPRIATE: 0.10,
        })
        self._use_llm = bool(use_llm)
        self._llm_timeout_ms = max(800, int(llm_timeout_ms))

    def should_reflect(
        self,
        *,
        emotion_tag: str = "",
        intent: str = "",
        bond_stage: str = "",
        turn_count: int = 1,
    ) -> bool:
        """判断是否需要开启反思链。

        仅在高风险场景启用，避免日常闲聊的额外延迟。
        """
        etag = str(emotion_tag or "").strip().lower()
        if etag in HIGH_RISK_EMOTION_TAGS:
            return True
        if str(intent or "").strip().lower() in HIGH_RISK_INTENTS:
            return True
        # 深度对话（多轮+亲密关系）
        stage = str(bond_stage or "").strip().upper()
        if turn_count >= 5 and stage in ("CONFIDANT", "SOULMATE"):
            return True
        return False

    def reflect(
        self,
        *,
        user_message: str,
        ai_response: str,
        emotion_tag: str = "",
        bond_stage: str = "",
        canon_rules: list[str] | None = None,
    ) -> ReflectionResult:
        """对 AI 回复进行结构化反思评估。"""
        user_msg = str(user_message or "").strip()
        ai_resp = str(ai_response or "").strip()
        dimensions: list[DimensionScore] = []
        all_issues: list[str] = []
        revision_hints: list[str] = []

        # 1. 情绪契合度
        d1 = self._eval_emotion_fit(user_msg, ai_resp, emotion_tag)
        dimensions.append(d1)
        all_issues.extend(d1.issues)

        # 2. Canon 合规性
        d2 = self._eval_canon_compliance(ai_resp)
        dimensions.append(d2)
        all_issues.extend(d2.issues)

        # 3. 风格一致性
        d3 = self._eval_style_consistency(ai_resp, bond_stage)
        dimensions.append(d3)
        all_issues.extend(d3.issues)

        # 4. 信息准确性
        d4 = self._eval_info_accuracy(user_msg, ai_resp)
        dimensions.append(d4)
        all_issues.extend(d4.issues)

        # 5. 长度适当性
        d5 = self._eval_length(user_msg, ai_resp)
        dimensions.append(d5)
        all_issues.extend(d5.issues)

        # 加权总分
        overall = sum(
            d.score * self._weights.get(d.dimension, 0.1)
            for d in dimensions
        )
        passed = overall >= self._threshold
        should_revise = not passed
        if _is_affection_question(user_msg) and (
            not _contains_affection_stance(ai_resp) or _looks_like_affection_evasion(ai_resp)
        ):
            should_revise = True
        if _is_affection_disclosure(user_msg) and (
            not _contains_affection_stance(ai_resp) or _looks_like_affection_evasion(ai_resp)
        ):
            should_revise = True

        # 生成修正提示
        if should_revise:
            for d in sorted(dimensions, key=lambda x: x.score):
                if d.score < 0.7 and d.issues:
                    revision_hints.append(
                        f"[{d.dimension}] {'; '.join(d.issues[:2])}"
                    )

        _logger.debug(
            "Reflection: overall=%.2f passed=%s dims=%s",
            overall, passed,
            {d.dimension: f"{d.score:.2f}" for d in dimensions},
        )

        result = ReflectionResult(
            overall_score=overall,
            passed=passed,
            dimension_scores=dimensions,
            issues=all_issues,
            revision_hints=revision_hints[:3],
            should_revise=should_revise,
        )
        llm_payload = self._maybe_llm_reflect(
            user_message=user_msg,
            ai_response=ai_resp,
            emotion_tag=emotion_tag,
            bond_stage=bond_stage,
            deterministic_result=result,
        )
        if llm_payload is not None:
            return self._merge_llm_reflection(result, llm_payload)
        return result

    def revise(
        self,
        *,
        user_message: str,
        ai_response: str,
        emotion_tag: str = "",
        bond_stage: str = "",
        canon_rules: list[str] | None = None,
        render_target_lang: str = "zh",
    ) -> RevisionResult:
        """Clean unsafe surface text without authoring a replacement reply.

        The old reflection chain used to write fallback/persona text when the
        model response was empty, too short, evasive, or cleaned down to
        nothing. That made reflection a hidden second author. The main chat
        path now owns generation, so this method only strips obvious runtime
        leaks and can return an empty string to let the caller produce a
        structured degraded/no-reply result.
        """
        before = self.reflect(
            user_message=user_message,
            ai_response=ai_response,
            emotion_tag=emotion_tag,
            bond_stage=bond_stage,
            canon_rules=canon_rules,
        )
        original = str(ai_response or "").strip()
        operations: list[str] = []
        revised = original

        if not original:
            operations.append("no_authoring_empty")
            revised = ""
        else:
            cleaned_structural_leak = False
            if _AI_SELF_REF_PATTERN.search(revised):
                revised = _AI_SELF_REF_PATTERN.sub("", revised)
                cleaned_structural_leak = True
                operations.append("strip_ai_self_reference")
            if _CUSTOMER_SERVICE_PATTERN.search(revised):
                revised = _CUSTOMER_SERVICE_PATTERN.sub("", revised)
                cleaned_structural_leak = True
                operations.append("strip_customer_service_tone")
            if _SYSTEM_STATUS_PATTERN.search(revised):
                revised = _SYSTEM_STATUS_PATTERN.sub("", revised)
                cleaned_structural_leak = True
                operations.append("strip_system_status_language")

            revised = re.sub(r"\s+", " ", revised).strip()
            if cleaned_structural_leak:
                revised = revised.lstrip(" ，,、;；:：").strip()

            if len(revised) < 6 and str(user_message or "").strip():
                revised = ""
                operations.append("drop_too_short_reply")
            elif before.should_revise and _looks_like_affection_evasion(revised):
                operations.append("audit_affection_evasion")

        after = self.reflect(
            user_message=user_message,
            ai_response=revised,
            emotion_tag=emotion_tag,
            bond_stage=bond_stage,
            canon_rules=canon_rules,
        )
        changed = revised != original
        return RevisionResult(
            revised_text=revised,
            changed=changed,
            operations=operations,
            reflection_before=before,
            reflection_after=after,
        )

    def _maybe_llm_reflect(
        self,
        *,
        user_message: str,
        ai_response: str,
        emotion_tag: str,
        bond_stage: str,
        deterministic_result: ReflectionResult,
    ) -> dict[str, Any] | None:
        if not self._use_llm:
            return None
        try:
            from host_brain.integrations.llm_helpers import complete_llm_with_pool_sync  # noqa: PLC0415

            templates = _load_reflection_llm_templates()
            reflect_tpl = dict(templates.get("reflect") or {})
            system_prompt = str(reflect_tpl.get("system_prompt") or "").strip()
            user_tpl = str(reflect_tpl.get("user_prompt_template") or "").strip()
            if not system_prompt or not user_tpl:
                return None
            prompt = user_tpl.format(
                user_message=user_message,
                ai_response=ai_response,
                emotion_tag=str(emotion_tag or "neutral"),
                bond_stage=str(bond_stage or "STRANGER"),
                deterministic_result_json=json.dumps(deterministic_result.as_dict(), ensure_ascii=False),
                dimensions_json=json.dumps(
                    {
                        item.dimension: {
                            "score": round(item.score, 3),
                            "issues": list(item.issues or []),
                        }
                        for item in deterministic_result.dimension_scores
                    },
                    ensure_ascii=False,
                ),
            )
            result = complete_llm_with_pool_sync(
                request_id=f"reflection::{int(time.time() * 1000)}",
                scene_type="REFLECTION",
                cognitive_tier="B",
                timeout_ms=self._llm_timeout_ms,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                response_format="json_object",
                gen_params={"temperature": 0.1, "top_p": 0.9},
            )
            if result.pool_exhausted or not str(result.content or "").strip():
                return None
            parsed = json.loads(str(result.content or "{}"))
            return dict(parsed) if isinstance(parsed, Mapping) else None
        except Exception as exc:
            _logger.debug("Reflection llm reflect skipped: %s", exc)
            return None

    def _merge_llm_reflection(
        self,
        deterministic_result: ReflectionResult,
        llm_payload: Mapping[str, Any],
    ) -> ReflectionResult:
        raw_dimensions = llm_payload.get("dimensions")
        dimension_map = dict(raw_dimensions) if isinstance(raw_dimensions, Mapping) else {}
        merged_dimensions: list[DimensionScore] = []
        for item in deterministic_result.dimension_scores:
            llm_dimension = dimension_map.get(item.dimension)
            llm_score = item.score
            llm_issues: list[str] = []
            if isinstance(llm_dimension, Mapping):
                llm_score = _coerce_score(llm_dimension.get("score"), item.score)
                raw_issues = llm_dimension.get("issues")
                if isinstance(raw_issues, list):
                    llm_issues = [str(entry).strip() for entry in raw_issues if str(entry or "").strip()]
            merged_dimensions.append(
                DimensionScore(
                    dimension=item.dimension,
                    score=(item.score + llm_score) / 2.0,
                    issues=_merge_issue_lists(item.issues, llm_issues),
                )
            )
        llm_overall = llm_payload.get("overall_score")
        overall_score = deterministic_result.overall_score
        if llm_overall is not None:
            overall_score = (overall_score + _coerce_score(llm_overall, overall_score)) / 2.0
        issues = _merge_issue_lists(
            deterministic_result.issues,
            [str(item).strip() for item in list(llm_payload.get("issues") or []) if str(item or "").strip()],
        )
        hints = _merge_issue_lists(
            deterministic_result.revision_hints,
            [str(item).strip() for item in list(llm_payload.get("revision_hints") or []) if str(item or "").strip()],
        )[:3]
        should_revise = bool(llm_payload.get("should_revise", deterministic_result.should_revise))
        passed = bool(llm_payload.get("passed", overall_score >= self._threshold))
        if should_revise:
            passed = False
        return ReflectionResult(
            overall_score=overall_score,
            passed=passed,
            dimension_scores=merged_dimensions,
            issues=issues,
            revision_hints=hints,
            should_revise=should_revise,
        )

    # ── 各维度评估 ────────────────────────────────────────────────────────

    def _eval_emotion_fit(
        self, user_msg: str, ai_resp: str, emotion_tag: str,
    ) -> DimensionScore:
        """情绪契合度：回复的情感基调是否匹配用户情绪。"""
        issues: list[str] = []
        score = 0.8  # 基础分

        etag = str(emotion_tag or "").strip().lower()
        resp_lower = ai_resp.lower()

        # 负面情绪时检查是否有共情表达
        if etag in ("sadness", "grief", "despair", "loneliness"):
            empathy_markers = ("没关系", "我在", "陪你", "心疼", "辛苦", "抱抱",
                               "不要紧", "别难过", "理解", "懂你")
            if not any(m in resp_lower for m in empathy_markers):
                score -= 0.3
                issues.append("负面情绪下缺少共情表达")

            # 检查是否有不合时宜的兴奋语气
            if any(m in resp_lower for m in ("太好了", "恭喜", "开心", "哈哈")):
                score -= 0.4
                issues.append("在用户难过时表达了不恰当的喜悦")

        # 用户开心时检查是否有共同庆祝
        if etag in ("happiness", "excitement"):
            if any(m in resp_lower for m in ("别", "小心", "注意", "但是")):
                score -= 0.2
                issues.append("用户开心时泼冷水")

        return DimensionScore(
            dimension=DIMENSION_EMOTION_FIT,
            score=max(0.0, min(1.0, score)),
            issues=issues,
        )

    def _eval_canon_compliance(self, ai_resp: str) -> DimensionScore:
        """Canon 合规性：有没有说"我是 AI"等违规内容。"""
        issues: list[str] = []
        score = 1.0

        if _AI_SELF_REF_PATTERN.search(ai_resp):
            score = 0.0
            issues.append("包含 AI 自我指称（严重违规）")

        if _CUSTOMER_SERVICE_PATTERN.search(ai_resp):
            score -= 0.5
            issues.append("使用了客服式措辞")

        if _SYSTEM_STATUS_PATTERN.search(ai_resp):
            score -= 0.3
            issues.append("使用了系统/技术用语")

        return DimensionScore(
            dimension=DIMENSION_CANON_COMPLIANCE,
            score=max(0.0, score),
            issues=issues,
        )

    def _eval_style_consistency(
        self, ai_resp: str, bond_stage: str,
    ) -> DimensionScore:
        """风格一致性：是否符合当前 Bond 阶段。"""
        issues: list[str] = []
        score = 0.9
        stage = str(bond_stage or "").strip().upper()

        intimate_markers = ("笨蛋", "人家", "哼", "撒娇", "亲爱的")
        formal_markers = ("您好", "请问", "非常感谢", "期待")

        if stage in ("STRANGER", "ACQUAINTANCE"):
            if any(m in ai_resp for m in intimate_markers):
                score -= 0.4
                issues.append(f"在{stage}阶段使用了过于亲密的表达")
        elif stage in ("SOULMATE", "CONFIDANT"):
            if any(m in ai_resp for m in formal_markers):
                score -= 0.3
                issues.append(f"在{stage}阶段使用了过于正式的表达")

        return DimensionScore(
            dimension=DIMENSION_STYLE_CONSISTENCY,
            score=max(0.0, score),
            issues=issues,
        )

    @staticmethod
    def _eval_info_accuracy(user_msg: str, ai_resp: str) -> DimensionScore:
        """信息准确性：检测明显的编造痕迹。"""
        issues: list[str] = []
        score = 0.9

        fabrication_markers = (
            "你之前说过", "你上次提到", "你告诉过我",
            "根据我的记忆", "如果我没记错",
        )
        for marker in fabrication_markers:
            if marker in ai_resp:
                score -= 0.2
                issues.append(f"可能编造记忆（含'{marker}'）")

        if _is_affection_question(user_msg):
            if not _contains_affection_stance(ai_resp):
                score -= 0.55
                issues.append("没有正面回答喜欢类直问，只是在回避题意")
            elif _looks_like_affection_evasion(ai_resp):
                score -= 0.35
                issues.append("把喜欢类直问答成了泛化陪伴")

        if _is_affection_disclosure(user_msg):
            if not _contains_affection_stance(ai_resp):
                score -= 0.45
                issues.append("对告白没有表明态度，只做了泛化感谢")
            elif _looks_like_affection_evasion(ai_resp):
                score -= 0.25
                issues.append("对告白的回应过于泛化，像在回避题意")

        return DimensionScore(
            dimension=DIMENSION_INFO_ACCURACY,
            score=max(0.0, score),
            issues=issues,
        )

    @staticmethod
    def _eval_length(user_msg: str, ai_resp: str) -> DimensionScore:
        """长度适当性：回复不应过短或过长。"""
        issues: list[str] = []
        user_len = len(user_msg)
        resp_len = len(ai_resp)

        if resp_len < 5:
            return DimensionScore(
                dimension=DIMENSION_LENGTH_APPROPRIATE,
                score=0.3, issues=["回复过短（<5字）"],
            )

        # 用户短消息→回复不应太长
        if user_len < 10 and resp_len > 200:
            score = 0.6
            issues.append("用户消息简短但回复过长")
        # 用户长消息→回复不应太短
        elif user_len > 50 and resp_len < 20:
            score = 0.5
            issues.append("用户消息较长但回复过短")
        else:
            score = 0.9

        return DimensionScore(
            dimension=DIMENSION_LENGTH_APPROPRIATE,
            score=score, issues=issues,
        )
