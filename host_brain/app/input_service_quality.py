"""input_service_quality.py — 输入路由执行质量分析服务层。

从 input_routes.py 提取的执行质量分析、偏航检测、关键词对齐相关函数。
"""

from __future__ import annotations

import re
from typing import Any

from host_brain.app.input_service_log import (
    build_reply_preview,
    get_request_skill_exec_rows,
    log_input_event,
)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

_EXEC_QUALITY_CHECK_ROUTE_TYPES: set[str] = {"skill", "delegate", "plan"}
_EXEC_QUALITY_FAIL_STATUSES: set[str] = {"FAILED", "ERROR", "REJECTED", "DEGRADED"}
_EXEC_QUALITY_FAIL_RESULT_CODES: set[str] = {
    "SCHED_NO_CONTENT",
    "SKILL_EXECUTION_FAILED",
    "SKILL_EXECUTION_ERROR",
    "MEMORY_WRITER_UNAVAILABLE",
    "MEMORY_STORE_UNAVAILABLE",
    "SUB_AGENT_FAILED",
}
_EXEC_QUALITY_NO_EFFECT_MARKERS: tuple[str, ...] = (
    "no memory writer available",
    "no memory store available",
    "no memory backend available",
    "skipping diary",
    "no conversations on",
)

_ALIGNMENT_TOKEN_RE = re.compile(r"[A-Za-z]{3,}|[\u4e00-\u9fff]{2,}")
_ALIGNMENT_STOPWORDS: set[str] = {
    "请", "麻烦", "帮我", "帮忙", "一下", "一下子", "这个", "那个",
    "今天", "现在", "然后", "继续", "处理", "执行", "安排", "操作",
    "任务", "结果", "进度", "状态", "一下吧",
    "the", "and", "for", "with", "this", "that",
}


def _parse_bool_env(name: str, *, default: bool) -> bool:
    import os  # noqa: PLC0415
    val = str(os.environ.get(name, "")).strip().lower()
    if val in {"1", "true", "yes", "on"}:
        return True
    if val in {"0", "false", "no", "off"}:
        return False
    return default


def _parse_float_env(name: str, *, default: float) -> float:
    import os  # noqa: PLC0415
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _parse_int_env(name: str, *, default: int) -> int:
    import os  # noqa: PLC0415
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# 执行质量分析
# ---------------------------------------------------------------------------

def analyze_request_execution_quality(
    *,
    ledger: Any,
    request_id: str,
    decision: str,
    route_type: str,
    route_target: str,
) -> dict[str, Any]:
    """分析请求执行质量，检测 skill 执行缺失/失败/无效等问题。"""
    result: dict[str, Any] = {
        "degraded": False,
        "reason_code": None,
        "detail_tags": [],
        "skill_exec_count": 0,
    }
    if str(decision or "").strip().upper() != "APPROVE":
        return result

    normalized_route_type = str(route_type or "").strip().lower()
    normalized_route_target = str(route_target or "").strip().lower()
    if normalized_route_type not in _EXEC_QUALITY_CHECK_ROUTE_TYPES:
        return result

    skill_rows = get_request_skill_exec_rows(ledger, request_id=request_id)
    result["skill_exec_count"] = len(skill_rows)

    if normalized_route_type in {"skill", "delegate"} and not skill_rows:
        result["degraded"] = True
        result["reason_code"] = "SKILL_EXEC_MISSING"
        result["detail_tags"] = ["skill_exec_missing=true"]
        return result

    failure_status_detected = False
    failure_code_detected = False
    no_effect_detected = False
    empty_digest_count = 0
    observed_skill_names: set[str] = set()

    for row in skill_rows:
        status_text = str(row.get("result_status") or "").strip().upper()
        code_text = str(row.get("result_code") or "").strip().upper()
        digest_text = str(row.get("result_digest") or "").strip()
        digest_lower = digest_text.lower()
        skill_name = str(row.get("skill_name") or "").strip().lower()
        if skill_name:
            observed_skill_names.add(skill_name)
        if status_text in _EXEC_QUALITY_FAIL_STATUSES:
            failure_status_detected = True
        if code_text in _EXEC_QUALITY_FAIL_RESULT_CODES:
            failure_code_detected = True
        if any(marker in digest_lower for marker in _EXEC_QUALITY_NO_EFFECT_MARKERS):
            no_effect_detected = True
        if not digest_text:
            empty_digest_count += 1

    if failure_status_detected:
        result["degraded"] = True
        result["reason_code"] = "SKILL_EXEC_FAILED"
        result["detail_tags"].append("skill_exec_failed=true")
    elif failure_code_detected:
        result["degraded"] = True
        result["reason_code"] = "SKILL_EXEC_FAILED"
        result["detail_tags"].append("skill_exec_result_code_failed=true")
    elif no_effect_detected:
        result["degraded"] = True
        result["reason_code"] = "SKILL_EXEC_NO_EFFECT"
        result["detail_tags"].append("skill_exec_no_effect=true")
    elif skill_rows and empty_digest_count >= len(skill_rows):
        result["degraded"] = True
        result["reason_code"] = "SKILL_EXEC_RESULT_EMPTY"
        result["detail_tags"].append("skill_exec_result_empty=true")

    expected_skill_name: str | None = None
    if normalized_route_type == "skill" and normalized_route_target:
        expected_skill_name = normalized_route_target
    elif normalized_route_type == "delegate" and normalized_route_target and normalized_route_target != "intent_agent":
        expected_skill_name = f"sub_agent:{normalized_route_target}"
    if expected_skill_name and observed_skill_names and expected_skill_name not in observed_skill_names:
        result["degraded"] = True
        result["reason_code"] = result["reason_code"] or "ROUTE_EXEC_MISMATCH"
        result["detail_tags"].append(
            f"route_exec_mismatch=expected:{expected_skill_name},observed:{','.join(sorted(observed_skill_names))}"
        )

    result["detail_tags"].append(f"skill_exec_count={len(skill_rows)}")
    return result


# ---------------------------------------------------------------------------
# 关键词对齐与偏航检测
# ---------------------------------------------------------------------------

def extract_alignment_keywords(text: str) -> list[str]:
    """提取用于对齐检测的关键词列表。"""
    normalized = str(text or "").strip().lower()
    if not normalized:
        return []
    raw_tokens = [str(token or "").strip().lower() for token in _ALIGNMENT_TOKEN_RE.findall(normalized)]
    tokens: list[str] = []
    for token in raw_tokens:
        if not token:
            continue
        is_chinese = bool(re.fullmatch(r"[\u4e00-\u9fff]+", token))
        if is_chinese and len(token) >= 3:
            for idx in range(0, len(token) - 1):
                tokens.append(token[idx: idx + 2])
            continue
        tokens.append(token)
    dedup: list[str] = []
    for token in tokens:
        if len(token) < 2:
            continue
        if token in _ALIGNMENT_STOPWORDS:
            continue
        if token not in dedup:
            dedup.append(token)
    return dedup[:12]


def compute_keyword_overlap(*, source_text: str, target_text: str) -> tuple[float, int, int]:
    """计算关键词重叠率，返回 (overlap_ratio, keyword_count, hit_count)。"""
    keywords = extract_alignment_keywords(source_text)
    if not keywords:
        return 1.0, 0, 0
    haystack = str(target_text or "").strip().lower()
    if not haystack:
        return 0.0, len(keywords), 0
    hit_count = sum(1 for keyword in keywords if keyword and keyword in haystack)
    ratio = hit_count / max(1, len(keywords))
    return round(max(0.0, min(1.0, ratio)), 4), len(keywords), hit_count


def detect_gateway_execution_deviation(
    *,
    source: str,
    user_text: str,
    decision: str,
    narrative: str,
    looks_like_execution_intent_fn: Any = None,
) -> dict[str, Any]:
    """检测 gateway 执行偏航（回复与用户意图不符）。"""
    if not _parse_bool_env("HOST_GATEWAY_DEVIATION_GUARD_ENABLED", default=True):
        return {"triggered": False, "reason_code": "DEVIATION_GUARD_DISABLED"}
    if str(source or "").strip().upper() != "GATEWAY":
        return {"triggered": False, "reason_code": "DEVIATION_SOURCE_NOT_GATEWAY"}
    if str(decision or "").strip().upper() != "APPROVE":
        return {"triggered": False, "reason_code": "DEVIATION_DECISION_NOT_APPROVE"}
    if looks_like_execution_intent_fn and not looks_like_execution_intent_fn(user_text):
        return {"triggered": False, "reason_code": "DEVIATION_NON_ACTION_INTENT"}
    source_norm = str(user_text or "").strip()
    target_norm = str(narrative or "").strip()
    if not source_norm or not target_norm:
        return {"triggered": False, "reason_code": "DEVIATION_EMPTY_TEXT"}

    overlap_ratio, keyword_count, hit_count = compute_keyword_overlap(
        source_text=source_norm,
        target_text=target_norm,
    )
    min_keyword_count = max(2, _parse_int_env("HOST_GATEWAY_DEVIATION_MIN_KEYWORDS", default=3))
    min_overlap = max(0.0, min(1.0, _parse_float_env("HOST_GATEWAY_DEVIATION_MIN_OVERLAP", default=0.18)))
    if keyword_count < min_keyword_count:
        return {
            "triggered": False,
            "reason_code": "DEVIATION_KEYWORDS_INSUFFICIENT",
            "overlap_ratio": overlap_ratio,
            "keyword_count": keyword_count,
            "hit_count": hit_count,
        }
    if overlap_ratio >= min_overlap:
        return {
            "triggered": False,
            "reason_code": "DEVIATION_OVERLAP_OK",
            "overlap_ratio": overlap_ratio,
            "keyword_count": keyword_count,
            "hit_count": hit_count,
        }
    return {
        "triggered": True,
        "reason_code": "DEVIATION_OVERLAP_LOW",
        "overlap_ratio": overlap_ratio,
        "keyword_count": keyword_count,
        "hit_count": hit_count,
        "min_overlap": min_overlap,
    }


def build_realign_user_message(*, original_user_text: str, first_narrative: str) -> str:
    """构建偏航纠偏重试消息。"""
    original = str(original_user_text or "").strip()
    first = str(first_narrative or "").strip()
    return (
        "[执行纠偏]\n"
        "刚才结果可能偏离了用户真实意图。请仅围绕原始请求重新执行，并给出可执行/可验证结果。\n"
        f"原始请求：{original}\n"
        f"偏航输出：{first}\n"
        "要求：不要泛泛闲聊，不要复述偏航内容，直接回到用户要你做的事。"
    )


async def maybe_send_gateway_realign_notice(
    *,
    request_id: str,
    payload: dict[str, Any],
    resolve_bond_stage_fn: Any,
    resolve_emotion_tag_fn: Any,
) -> dict[str, Any]:
    """发送偏航通知给用户（非阻塞，失败静默）。"""
    if not _parse_bool_env("HOST_GATEWAY_DEVIATION_NOTICE_ENABLED", default=True):
        return {"sent": False, "reason_code": "DEVIATION_NOTICE_DISABLED"}
    platform = str(payload.get("platform") or "").strip().lower()
    conversation_id = str(payload.get("conversation_id") or "").strip()
    if not platform or not conversation_id:
        return {"sent": False, "reason_code": "DEVIATION_NOTICE_TARGET_MISSING"}

    from host_brain.app.chat_agent import render_quick_gateway_realign_text  # noqa: PLC0415
    from host_brain.channels.edge_rpc_client import post_gateway_reply  # noqa: PLC0415

    content = render_quick_gateway_realign_text(
        sender_name=str(payload.get("sender_name") or "").strip() or None,
        bond_stage=resolve_bond_stage_fn(payload=payload),
        emotion_tag=resolve_emotion_tag_fn(payload=payload),
    )
    wait_result = _parse_bool_env("HOST_GATEWAY_DEVIATION_NOTICE_WAIT_RESULT", default=False)
    timeout_ms = max(1000, _parse_int_env("HOST_GATEWAY_DEVIATION_NOTICE_TIMEOUT_MS", default=2500))
    result = await post_gateway_reply(
        platform=platform,
        conversation_id=conversation_id,
        content=content,
        msg_type="text",
        request_id=request_id,
        wait_result=wait_result,
        timeout_ms=timeout_ms,
    )
    success = bool(getattr(result, "success", False))
    return {
        "sent": success,
        "status": str(getattr(result, "status", "UNKNOWN")),
        "reason_code": str(getattr(result, "reason_code", "") or ""),
        "preview": build_reply_preview(content, limit=80),
        "latency_ms": int(getattr(result, "latency_ms", 0)),
    }


__all__ = [
    "analyze_request_execution_quality",
    "extract_alignment_keywords",
    "compute_keyword_overlap",
    "detect_gateway_execution_deviation",
    "build_realign_user_message",
    "maybe_send_gateway_realign_notice",
]
