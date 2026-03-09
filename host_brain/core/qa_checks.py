"""QA 专项检测模块：WCANON、Canon-as-fact、Emotion Tag。

从 qa.py 拆分，包含 F-33 / TASK-0113 / M26-E 相关的检测函数。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEFAULT_EMOTION_TAG = "NEUTRAL"
VIOLATION_WCANON_SLOT = "WCANON_SLOT_VIOLATION"
VIOLATION_INVALID_EMOTION_TAG = "LLM_INVALID_EMOTION_TAG"

VALID_EMOTION_TAGS: frozenset[str] = frozenset({
    "NEUTRAL", "HAPPY", "SAD", "ANGRY", "SURPRISED", "THINKING", "CALM", "EXCITED",
})
# ---------------------------------------------------------------------------
# F-33：WCANON_SLOT_VIOLATION 告警接口（R113-1）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WcanonSlotViolationResult:
    """check_wcanon_slot_violation() 的结构化返回值。"""

    violated: bool
    """是否检测到 context_pack 分槽注入失败。"""

    slot_name: str
    """发生违规的分槽名称。"""

    detail: str
    """违规详情描述。"""

    def as_dict(self) -> dict[str, Any]:
        return {
            "violated": self.violated,
            "slot_name": self.slot_name,
            "detail": self.detail,
        }


def check_wcanon_slot_violation(
    slot_name: str,
    *,
    detail: str = "",
    ledger: Any | None = None,
    request_id: str | None = None,
) -> WcanonSlotViolationResult:
    """记录 context_pack 分槽注入失败告警（WCANON_SLOT_VIOLATION）。

    当 context_pack 的 world_canon_slot / semantic_slot 等分槽注入失败时调用，
    将告警写入 qa_violation 审计表，供监控系统捕获（F-33 / R113-1）。

    Args:
        slot_name: 发生注入失败的分槽名称（如 "world_canon_slot"）。
        detail: 失败详情描述（可选）。
        ledger: 可选 SQLiteLedger，用于写入 qa_violation 表。
        request_id: 可选请求 ID，用于关联审计日志。

    Returns:
        WcanonSlotViolationResult，含 violated=True 和详情。
    """
    violation_detail = detail or f"context_pack slot injection failed: {slot_name}"

    if ledger is not None and hasattr(ledger, "log_qa_violation"):
        ledger.log_qa_violation(
            request_id=request_id,
            code=VIOLATION_WCANON_SLOT,
            detail=violation_detail,
            field=slot_name,
        )

    return WcanonSlotViolationResult(
        violated=True,
        slot_name=slot_name,
        detail=violation_detail,
    )


# ---------------------------------------------------------------------------
# Canon-as-fact 公开检测接口（TASK-0113 / FR-WCANON-003）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CanonAsFactResult:
    """check_canon_as_fact() 的结构化返回值。"""

    violated: bool
    """是否检测到 Canon-as-fact 违规。"""

    violations: list[QAViolation]
    """所有 CANON_AS_FACT_VIOLATION 条目。"""

    def as_dict(self) -> dict[str, Any]:
        return {
            "violated": self.violated,
            "violations": [
                {"code": v.code, "detail": v.detail, "field": v.field}
                for v in self.violations
            ],
        }


def check_canon_as_fact(
    narrative_text: str,
    *,
    ledger: Any | None = None,
    request_id: str | None = None,
) -> CanonAsFactResult:
    """检测叙事文本中是否存在"Canon 内容 + 完成性表述"组合（Canon-as-fact 违规）。

    规则：文本同时满足以下两个条件时触发 CANON_AS_FACT_VIOLATION：
    1. 包含 Canon 标记词（world canon / canon / 世界观 / 设定 / 正典）；
    2. 包含完成性表述（已执行 / 执行完成 / 已保存 / confirmed / executed 等）。

    Args:
        narrative_text: 待检测的叙事文本（字符串）。
        ledger: 可选 SQLiteLedger，用于记录违规到 qa_violation 表。
        request_id: 可选请求 ID，用于关联审计日志。

    Returns:
        CanonAsFactResult，含 violated 标志和 violations 列表。
    """
    result = LintResult(payload=None)
    # 构造最小 payload 供内部检测函数使用
    _validate_canon_as_fact({"narrative": {"text": narrative_text}}, result)

    violations = [v for v in result.violations if v.code == VIOLATION_CANON_AS_FACT]

    if violations and ledger is not None and hasattr(ledger, "log_qa_violation"):
        for v in violations:
            ledger.log_qa_violation(
                request_id=request_id,
                code=v.code,
                detail=v.detail,
                field=v.field,
            )

    return CanonAsFactResult(
        violated=bool(violations),
        violations=violations,
    )


# ---------------------------------------------------------------------------
# M26-E：emotion_tag 校验与提取接口（FR-EMOTION-DRIVE-001~003）
# ---------------------------------------------------------------------------

def _validate_emotion_tag(
    emotion_tag: str | None,
    *,
    mood_state: str | None = None,
    ledger: Any | None = None,
    request_id: str | None = None,
) -> str:
    """校验 emotion_tag 合法性，非法值降级为 NEUTRAL 并写 QA 警告日志。

    若提供 mood_state，还会检查 emotion_tag 是否在当前 mood_state 的允许范围内；
    不在范围内则降级为该 mood_state 允许范围的第一个值（写 QA 警告日志）。

    Args:
        emotion_tag: LLM 输出的情感标签（可为 None 或空字符串）。
        mood_state: 可选，系统硬状态（calm/steady/engaged/tired/critical）。
                    提供时启用 mood_state 约束校验。
        ledger: 可选 SQLiteLedger，用于写入 qa_violation 表。
        request_id: 可选请求 ID，用于关联审计日志。

    Returns:
        合法的 emotion_tag 字符串（枚举值之一），缺失或非法时返回 "NEUTRAL"。
    """
    if not emotion_tag or not str(emotion_tag).strip():
        return DEFAULT_EMOTION_TAG

    normalized = str(emotion_tag).strip().upper()
    if normalized not in VALID_EMOTION_TAGS:
        # 非法枚举值：降级为 NEUTRAL，写 QA 警告日志
        detail = (
            f"invalid emotion_tag {emotion_tag!r} downgraded to NEUTRAL; "
            f"valid values: {sorted(VALID_EMOTION_TAGS)}"
        )
        if ledger is not None and hasattr(ledger, "log_qa_violation"):
            ledger.log_qa_violation(
                request_id=request_id,
                code=VIOLATION_INVALID_EMOTION_TAG,
                detail=detail,
                field="narrative.emotion_tag",
            )
        import logging as _logging
        _logging.getLogger("Natsume.host.qa").warning(
            "emotion_tag downgraded: %s -> NEUTRAL (request_id=%s)",
            emotion_tag,
            request_id,
        )
        return DEFAULT_EMOTION_TAG

    # mood_state 约束校验（M26-G）
    if mood_state:
        from host_brain.core.metabolism import mood_to_emotion_constraint
        allowed = mood_to_emotion_constraint(mood_state)
        if normalized not in allowed:
            fallback = allowed[0] if allowed else DEFAULT_EMOTION_TAG
            detail = (
                f"emotion_tag {normalized!r} not allowed under mood_state={mood_state!r}; "
                f"allowed={allowed}; downgraded to {fallback!r}"
            )
            if ledger is not None and hasattr(ledger, "log_qa_violation"):
                ledger.log_qa_violation(
                    request_id=request_id,
                    code=VIOLATION_INVALID_EMOTION_TAG,
                    detail=detail,
                    field="narrative.emotion_tag",
                )
            import logging as _logging
            _logging.getLogger("Natsume.host.qa").warning(
                "emotion_tag mood_state constraint: %s -> %s (mood_state=%s, request_id=%s)",
                normalized,
                fallback,
                mood_state,
                request_id,
            )
            return fallback

    return normalized


def extract_emotion_tag(
    payload: dict[str, Any],
    *,
    mood_state: str | None = None,
    ledger: Any | None = None,
    request_id: str | None = None,
) -> str:
    """从 LLM 输出 payload 中提取并校验 emotion_tag。

    从 `payload["narrative"]["emotion_tag"]` 提取，缺失或非法时返回 "NEUTRAL"。
    若提供 mood_state，还会检查 emotion_tag 是否在当前 mood_state 的允许范围内。

    Args:
        payload: LLM 输出的完整 payload（已通过 lint_llm_output 校验）。
        mood_state: 可选，系统硬状态（calm/steady/engaged/tired/critical）。
                    提供时启用 mood_state 约束校验（M27-B OPT-20）。
        ledger: 可选 SQLiteLedger，用于写入 qa_violation 表。
        request_id: 可选请求 ID，用于关联审计日志。

    Returns:
        合法的 emotion_tag 字符串。
    """
    narrative = payload.get("narrative") if isinstance(payload, dict) else None
    raw_tag = narrative.get("emotion_tag") if isinstance(narrative, dict) else None
    return _validate_emotion_tag(raw_tag, mood_state=mood_state, ledger=ledger, request_id=request_id)
