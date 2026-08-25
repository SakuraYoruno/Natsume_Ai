"""Canonical runtime responsibility extracted from the former app hotspot."""

from __future__ import annotations

def _is_short_assistant_reply_followup(user_message: str) -> bool:
    try:
        from host_brain.persona.context.interaction_thread import detect_assistant_reply_followup_kind  # noqa: PLC0415

        return bool(detect_assistant_reply_followup_kind(str(user_message or "").strip()))
    except Exception:
        return False


def _memory_recall_plan_should_bypass_direct_status_fastlane(user_message: str, *, task_mode: str | None = None) -> bool:
    """Return True when a direct recent/task route would hide required memory."""

    message = str(user_message or "").strip()
    if message and (
        any(token in message for token in ("回忆检查", "记忆验收", "同会话验收", "新会话验收", "最终同会话验收"))
        or ("验收" in message and any(token in message for token in ("列出", "复述", "回答", "说出")))
        or (
            any(token in message for token in ("测试代号", "海边灯塔", "预热指标", "推荐格式"))
            and any(token in message for token in ("是什么", "指什么", "列出", "复述", "当前", "刚才", "刚刚"))
        )
    ):
        return True
    try:
        from host_brain.memory.life_memory_query import plan_life_memory_query  # noqa: PLC0415
    except Exception:
        return False
    try:
        plan = plan_life_memory_query(user_message, task_mode=task_mode)
    except Exception:
        return False
    if not bool(getattr(plan, "should_recall", False)):
        return False
    contract_id = str(getattr(plan, "recall_contract_id", "") or "").strip()
    reason = str(getattr(plan, "reason", "") or "").strip()
    query_kind = str(getattr(plan, "query_kind", "") or "").strip()
    strong_contracts = {
        "anaphoric_selection",
        "collection_date",
        "collection_recent",
        "correction",
        "evidence_review",
        "failure_debug",
        "open_loop",
        "preference",
        "explicit_memory",
        "self_change",
    }
    return (
        contract_id in strong_contracts
        or reason in {"collection_memory_recall", "correction_recall", "evidence_review_recall", "failure_debug_recall"}
        or query_kind in {"correction", "evidence_review", "explicit_memory", "failure_debug", "open_loop", "preference_lookup"}
    )
