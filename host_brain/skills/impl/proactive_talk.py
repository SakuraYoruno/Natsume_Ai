"""Proactive Talk Skill——主动对话。

由调度器在空闲/Normal 时触发；根据时段 + 上次对话间隔
→ LLM 生成主动问候/关心 → 推送到 UI 队列。
空闲阈值 < 4h 则跳过，避免打扰。
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, Callable

from host_brain.core.errors import SCHED_CONDITION_UNMET, SCHED_NO_CONTENT
from host_brain.core.ids import now_ms, new_event_id

logger = logging.getLogger(__name__)

# Skill 元数据常量
SKILL_NAME = "proactive_talk"
SKILL_CLEARANCE = "LOW"
SKILL_RISK_LEVEL = 1
SKILL_SCOPE = "chat"
SKILL_REVERSIBLE = False

# 状态
STATUS_EXECUTED = "EXECUTED"
STATUS_REJECTED = "REJECTED"
STATUS_DEGRADED = "DEGRADED"

# 默认空闲阈值（毫秒）: 4 小时
DEFAULT_IDLE_THRESHOLD_MS = 4 * 60 * 60 * 1000

# 允许主动对话的 sys_mode
ALLOWED_MODES = {"normal", "sleep"}

# 回调类型
LLMGenerator = Callable[[dict[str, Any]], dict[str, Any]]
UIPusher = Callable[[dict[str, Any]], dict[str, Any]]


def _get_time_period() -> str:
    """获取当前时段标签。"""
    hour = datetime.datetime.now().hour
    if 5 <= hour < 9:
        return "morning"
    elif 9 <= hour < 12:
        return "forenoon"
    elif 12 <= hour < 14:
        return "noon"
    elif 14 <= hour < 18:
        return "afternoon"
    elif 18 <= hour < 22:
        return "evening"
    else:
        return "night"


def execute_proactive_talk(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """执行主动对话。

    args:
        idle_threshold_ms: 空闲阈值（毫秒），默认 4h
        topic_hint: 话题提示（可选）

    context:
        sys_mode: 当前系统模式
        last_interaction_ts: 上次对话时间戳（ms）
        request_id / intent_id
        llm_generator: LLM 生成回调
        ui_pusher: UI 推送回调
        bond_summary: Bond 摘要（可选）
        user_name: 用户昵称
        time_period: 时段（覆盖自动检测）
    """
    ctx = context or {}
    ts = now_ms()
    event_id = new_event_id()
    request_id = ctx.get("request_id")
    intent_id = ctx.get("intent_id")

    # sys_mode 检查
    sys_mode = str(ctx.get("sys_mode", "normal")).strip().lower()
    if sys_mode not in ALLOWED_MODES:
        return {
            "status": STATUS_REJECTED,
            "event_id": event_id,
            "reason_code": SCHED_CONDITION_UNMET,
            "detail": f"proactive_talk not allowed in sys_mode={sys_mode}",
            "request_id": request_id,
            "ts_ms": ts,
        }

    # 空闲时间检查
    idle_threshold = int(args.get("idle_threshold_ms", DEFAULT_IDLE_THRESHOLD_MS))
    last_ts = ctx.get("last_interaction_ts")
    if last_ts is not None:
        elapsed = ts - int(last_ts)
        if elapsed < idle_threshold:
            return {
                "status": STATUS_REJECTED,
                "event_id": event_id,
                "reason_code": SCHED_CONDITION_UNMET,
                "detail": f"idle time {elapsed}ms < threshold {idle_threshold}ms",
                "elapsed_ms": elapsed,
                "request_id": request_id,
                "ts_ms": ts,
            }

    # 时段检测
    time_period = str(ctx.get("time_period", "")).strip() or _get_time_period()
    user_name = str(ctx.get("user_name", "主人")).strip()

    # LLM 生成主动问候
    llm_generator: LLMGenerator | None = ctx.get("llm_generator")
    greeting: str = ""

    if llm_generator:
        try:
            llm_input = {
                "task": "proactive_talk",
                "time_period": time_period,
                "user_name": user_name,
                "topic_hint": args.get("topic_hint"),
                "bond_summary": ctx.get("bond_summary", {}),
                "ts_ms": ts,
            }
            llm_result = llm_generator(llm_input)
            greeting = str(llm_result.get("greeting", llm_result.get("content", "")))
        except Exception as exc:
            logger.warning("LLM proactive talk failed: %s", exc)
            return {
                "status": STATUS_DEGRADED,
                "event_id": event_id,
                "reason_code": "PROACTIVE_LLM_UNAVAILABLE",
                "detail": f"LLM generation failed: {exc}",
                "request_id": request_id,
                "ts_ms": ts,
            }
    else:
        # 降级模板
        templates = {
            "morning": f"{user_name}早上好～新的一天开始了呢",
            "noon": f"{user_name}中午了，记得吃饭哦～",
            "evening": f"{user_name}晚上好～今天过得怎么样？",
            "night": f"{user_name}夜深了，注意休息哦～",
        }
        greeting = templates.get(time_period, f"{user_name}，好久没聊了，在忙什么呢？")

    if not greeting.strip():
        return {
            "status": STATUS_REJECTED,
            "event_id": event_id,
            "reason_code": SCHED_NO_CONTENT,
            "detail": "LLM produced empty greeting",
            "request_id": request_id,
            "ts_ms": ts,
        }

    # 推送到 UI
    message = {
        "type": "proactive_talk",
        "content": greeting,
        "time_period": time_period,
        "event_id": event_id,
        "ts_ms": ts,
    }

    ui_pusher: UIPusher | None = ctx.get("ui_pusher")
    push_result: dict[str, Any] | None = None
    if ui_pusher:
        try:
            push_result = ui_pusher(message)
        except Exception as exc:
            logger.warning("UI push failed for proactive talk: %s", exc)
            push_result = {"error": str(exc)}

    return {
        "status": STATUS_EXECUTED,
        "event_id": event_id,
        "reason_code": None,
        "detail": f"proactive talk sent: {time_period}",
        "message": message,
        "push_result": push_result,
        "request_id": request_id,
        "intent_id": intent_id,
        "ts_ms": ts,
    }
