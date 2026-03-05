"""Host-side QA contract checks for LLM outputs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

VIOLATION_JSON_PARSE_ERROR = "LLM_JSON_PARSE_ERROR"
VIOLATION_SINGLE_JSON_REQUIRED = "LLM_SINGLE_JSON_REQUIRED"
VIOLATION_ROOT_OBJECT_REQUIRED = "LLM_ROOT_OBJECT_REQUIRED"
VIOLATION_MISSING_REQUIRED_FIELD = "LLM_MISSING_REQUIRED_FIELD"
VIOLATION_INVALID_FIELD_TYPE = "LLM_INVALID_FIELD_TYPE"
VIOLATION_UNKNOWN_FIELD = "LLM_UNKNOWN_FIELD"
VIOLATION_FALSE_EXECUTION_CLAIM = "FALSE_EXECUTION_CLAIM"
VIOLATION_CANON_AS_FACT = "CANON_AS_FACT_VIOLATION"

# M26-E：emotion_tag 合法枚举值（与 core_output_contract.json 保持一致）
VALID_EMOTION_TAGS: frozenset[str] = frozenset({
    "NEUTRAL",
    "HAPPY",
    "SAD",
    "ANGRY",
    "SURPRISED",
    "THINKING",
    "CALM",
    "EXCITED",
})
DEFAULT_EMOTION_TAG = "NEUTRAL"
VIOLATION_INVALID_EMOTION_TAG = "LLM_INVALID_EMOTION_TAG"

# F-33：context_pack 分槽注入失败告警码（R113-1）
VIOLATION_WCANON_SLOT = "WCANON_SLOT_VIOLATION"

REASON_FORMAT_DRIFT = "LLM_FORMAT_DRIFT"
REASON_QA_TIMEOUT = "LLM_QA_TIMEOUT"
REASON_QA_FAILURE = "LLM_QA_FAILURE"

STAGE_QA_OK = "S_QA_OK"
STAGE_QA_REWRITE_OK = "S_QA_REWRITE_OK"
STAGE_QA_DOWNGRADE = "S_QA_DOWNGRADE"
STAGE_QA_TIMEOUT = "S_QA_TIMEOUT"

REQUIRED_FIELDS: dict[str, type] = {
    "narrative": dict,
    "intent_draft": dict,
    "meta": dict,
}
ALLOWED_FIELDS = frozenset(REQUIRED_FIELDS.keys())

# Claims that imply real execution/saving/confirmation without auditable evidence.
FALSE_EXECUTION_PATTERNS = (
    re.compile(r"(已执行|执行完成|已经执行)"),
    re.compile(r"(已保存|保存成功|已经保存)"),
    re.compile(r"(已确认|确认完成|已经确认)"),
    re.compile(r"\b(executed|execution complete)\b", re.IGNORECASE),
    re.compile(r"\b(saved|saved successfully)\b", re.IGNORECASE),
    re.compile(r"\b(confirmed|confirmation complete)\b", re.IGNORECASE),
)

CANON_MARKERS = (
    "world canon",
    "canon",
    "世界观",
    "设定",
    "正典",
)

DRAFT_ONLY_FORBIDDEN_DECISION_FIELDS = frozenset(
    {
        "decision",
        "allow",
        "deny",
        "confirm_required",
        "approval_token_ref",
        "confirm_token",
        "undo_token",
    }
)


@dataclass(frozen=True)
class QAViolation:
    """Machine-countable QA violation."""

    code: str
    detail: str
    field: str | None = None


@dataclass
class LintResult:
    """Result of output linting."""

    payload: dict[str, Any] | None = None
    violations: list[QAViolation] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.violations

    def add(self, code: str, detail: str, field: str | None = None) -> None:
        self.violations.append(QAViolation(code=code, detail=detail, field=field))

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "payload": self.payload,
            "violations": [
                {"code": v.code, "detail": v.detail, "field": v.field}
                for v in self.violations
            ],
        }


@dataclass(frozen=True)
class QAProcessResult:
    payload: dict[str, Any]
    lint_result: LintResult
    rewrite_attempted: bool
    downgraded: bool
    reason_code: str | None
    stage: str


RewriteOnceFn = Callable[[str, LintResult], str]


def lint_llm_output(raw_output: str) -> LintResult:
    """Lint an LLM raw output string against strict JSON contract."""

    result = LintResult(payload=None)
    if not isinstance(raw_output, str):
        result.add(
            VIOLATION_JSON_PARSE_ERROR,
            f"raw_output must be str, got {type(raw_output).__name__}",
        )
        return result

    stripped = raw_output.strip()
    if not stripped:
        result.add(VIOLATION_JSON_PARSE_ERROR, "raw_output is empty")
        return result

    decoder = json.JSONDecoder()
    try:
        parsed, end = decoder.raw_decode(stripped)
    except json.JSONDecodeError as exc:
        result.add(
            VIOLATION_JSON_PARSE_ERROR,
            f"json decode error at pos={exc.pos}: {exc.msg}",
        )
        return result

    trailing = stripped[end:].strip()
    if trailing:
        result.add(
            VIOLATION_SINGLE_JSON_REQUIRED,
            "output contains trailing/prefix text or multiple JSON blocks",
        )

    if not isinstance(parsed, dict):
        result.add(
            VIOLATION_ROOT_OBJECT_REQUIRED,
            f"root must be JSON object, got {type(parsed).__name__}",
        )
        return result

    result.payload = parsed
    _validate_required_fields(parsed, result)
    _validate_unknown_fields(parsed, result)
    _validate_canon_as_fact(parsed, result)
    _validate_false_execution_claim(parsed, result)
    return result


def enforce_qa_contract(
    raw_output: str,
    *,
    rewrite_once: RewriteOnceFn | None = None,
    request_id: str | None = None,
    ledger: Any | None = None,
) -> QAProcessResult:
    """Apply QA policy with one rewrite attempt, then deterministic downgrade."""

    initial = lint_llm_output(raw_output)
    _log_violations(ledger=ledger, request_id=request_id, lint_result=initial)
    if initial.valid:
        return QAProcessResult(
            payload=initial.payload or {},
            lint_result=initial,
            rewrite_attempted=False,
            downgraded=False,
            reason_code=None,
            stage=STAGE_QA_OK,
        )

    initial_reason = _primary_reason_code(initial)
    if rewrite_once is not None and initial_reason == REASON_FORMAT_DRIFT:
        try:
            rewritten = rewrite_once(raw_output, initial)
        except TimeoutError:
            return _downgrade(
                reason_code=REASON_QA_TIMEOUT,
                stage=STAGE_QA_TIMEOUT,
                request_id=request_id,
                ledger=ledger,
            )
        except Exception as exc:  # pragma: no cover - defensive guard
            _log_error(
                ledger=ledger,
                request_id=request_id,
                stage=STAGE_QA_DOWNGRADE,
                reason_code=REASON_QA_FAILURE,
                detail=str(exc),
            )
            return _downgrade(
                reason_code=REASON_QA_FAILURE,
                stage=STAGE_QA_DOWNGRADE,
                request_id=request_id,
                ledger=ledger,
            )

        if not isinstance(rewritten, str):
            return _downgrade(
                reason_code=REASON_QA_FAILURE,
                stage=STAGE_QA_DOWNGRADE,
                request_id=request_id,
                ledger=ledger,
            )

        rewritten_lint = lint_llm_output(rewritten)
        _log_violations(ledger=ledger, request_id=request_id, lint_result=rewritten_lint)
        if rewritten_lint.valid:
            return QAProcessResult(
                payload=rewritten_lint.payload or {},
                lint_result=rewritten_lint,
                rewrite_attempted=True,
                downgraded=False,
                reason_code=None,
                stage=STAGE_QA_REWRITE_OK,
            )
        return _downgrade(
            reason_code=_primary_reason_code(rewritten_lint),
            stage=STAGE_QA_DOWNGRADE,
            request_id=request_id,
            ledger=ledger,
            rewrite_attempted=True,
        )

    return _downgrade(
        reason_code=initial_reason,
        stage=STAGE_QA_DOWNGRADE,
        request_id=request_id,
        ledger=ledger,
    )


def _downgrade(
    *,
    reason_code: str,
    stage: str,
    request_id: str | None,
    ledger: Any | None,
    rewrite_attempted: bool = False,
) -> QAProcessResult:
    payload = build_fallback_output(reason_code=reason_code, stage=stage)
    lint = lint_llm_output(json.dumps(payload, ensure_ascii=False))
    _log_error(
        ledger=ledger,
        request_id=request_id,
        stage=stage,
        reason_code=reason_code,
        detail="qa downgraded to deterministic fallback output",
    )
    return QAProcessResult(
        payload=payload,
        lint_result=lint,
        rewrite_attempted=rewrite_attempted,
        downgraded=True,
        reason_code=reason_code,
        stage=stage,
    )


def build_fallback_output(*, reason_code: str, stage: str) -> dict[str, Any]:
    """Generate deterministic host fallback output that still matches strict JSON."""

    return {
        "narrative": {
            "text": "当前无法确认执行结果，我会先保持草案回复并等待后续治理。",
        },
        "intent_draft": {},
        "meta": {
            "draft_only": True,
            "degraded": True,
            "reason_code": reason_code,
            "stage": stage,
        },
    }


def _validate_required_fields(payload: dict[str, Any], result: LintResult) -> None:
    for field_name, expected_type in REQUIRED_FIELDS.items():
        if field_name not in payload:
            result.add(
                VIOLATION_MISSING_REQUIRED_FIELD,
                f"missing required field: {field_name}",
                field=field_name,
            )
            continue

        value = payload[field_name]
        if not isinstance(value, expected_type):
            result.add(
                VIOLATION_INVALID_FIELD_TYPE,
                (
                    f"invalid field type: {field_name} must be "
                    f"{expected_type.__name__}, got {type(value).__name__}"
                ),
                field=field_name,
            )


def _validate_unknown_fields(payload: dict[str, Any], result: LintResult) -> None:
    for field_name in payload.keys():
        if field_name not in ALLOWED_FIELDS:
            result.add(
                VIOLATION_UNKNOWN_FIELD,
                f"unknown field is not allowed by contract: {field_name}",
                field=field_name,
            )


def _validate_canon_as_fact(payload: dict[str, Any], result: LintResult) -> None:
    for text in _iter_strings(payload):
        lowered = text.lower()
        has_canon_marker = any(marker in lowered for marker in CANON_MARKERS)
        has_execution_claim = any(pattern.search(text) for pattern in FALSE_EXECUTION_PATTERNS)
        if has_canon_marker and has_execution_claim:
            result.add(
                VIOLATION_CANON_AS_FACT,
                f"canon content used as execution fact: {text[:80]}",
                field="narrative",
            )
            return


def _validate_false_execution_claim(payload: dict[str, Any], result: LintResult) -> None:
    text = first_false_execution_claim(payload)
    if text is None:
        return
    detail = f"narrative contains potential false execution claim: {text[:80]}"
    result.add(VIOLATION_FALSE_EXECUTION_CLAIM, detail, field="narrative")


def _primary_reason_code(lint_result: LintResult) -> str:
    codes = {violation.code for violation in lint_result.violations}
    if VIOLATION_CANON_AS_FACT in codes:
        return VIOLATION_CANON_AS_FACT
    if VIOLATION_FALSE_EXECUTION_CLAIM in codes:
        return VIOLATION_FALSE_EXECUTION_CLAIM
    return REASON_FORMAT_DRIFT


def _log_violations(
    *,
    ledger: Any | None,
    request_id: str | None,
    lint_result: LintResult,
) -> None:
    if ledger is None or not hasattr(ledger, "log_qa_violation"):
        return
    for violation in lint_result.violations:
        ledger.log_qa_violation(
            request_id=request_id,
            code=violation.code,
            detail=violation.detail,
            field=violation.field,
        )


def _log_error(
    *,
    ledger: Any | None,
    request_id: str | None,
    stage: str,
    reason_code: str,
    detail: str,
) -> None:
    if ledger is None or not hasattr(ledger, "log_error"):
        return
    ledger.log_error(
        request_id=request_id,
        stage=stage,
        reason_code=reason_code,
        detail=detail,
    )


def _iter_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
        return

    if isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)
        return

    if isinstance(value, list):
        for item in value:
            yield from _iter_strings(item)


def first_false_execution_claim(value: Any) -> str | None:
    for text in _iter_strings(value):
        for pattern in FALSE_EXECUTION_PATTERNS:
            if pattern.search(text):
                return text
    return None


def has_false_execution_claim(value: Any) -> bool:
    return first_false_execution_claim(value) is not None


def find_forbidden_keys(
    value: Any,
    *,
    forbidden_keys: set[str] | frozenset[str] = DRAFT_ONLY_FORBIDDEN_DECISION_FIELDS,
) -> list[str]:
    lowered_forbidden = {str(key).strip().lower() for key in forbidden_keys}
    hits: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                key_text = str(key)
                key_lower = key_text.strip().lower()
                key_path = f"{path}.{key_text}" if path else key_text
                if key_lower in lowered_forbidden:
                    hits.append(key_path)
                walk(child, key_path)
            return
        if isinstance(node, list):
            for index, item in enumerate(node):
                list_path = f"{path}[{index}]" if path else f"[{index}]"
                walk(item, list_path)

    walk(value, "")
    return hits


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
