"""Pure legacy Visual ActionPlan parsing helpers and a sealed compatibility API.

The former ``generate_action_plan`` function resolved a process-global model and
returned executable desktop actions outside HostCommand/TaskGraph admission.
That authority is retired.  Parsing helpers remain pure for future governed
proposal compilation and historical contract tests; they do not execute a model
or an actuator and their output grants no execution authority.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Mapping, Optional

from .clawdbot_protocol import (
    Action,
    ActionPlan,
    ActionType,
    VISUAL_AGENT_GOVERNED_ADMISSION_REQUIRED,
)

_logger = logging.getLogger("Natsume.action_planner")
# ---------------------------------------------------------------------------
# 核心函数：生成 ActionPlan
# ---------------------------------------------------------------------------

async def generate_action_plan(
    *,
    request_id: str,
    session_id: str,
    goal: str,
    vision_context: Optional[Dict[str, Any]],
    llm_runtime: Dict[str, Any],
    image_upload_allowed: bool = False,
    stop_on_error: bool = True,
    need_screenshot_feedback: bool = False,
) -> ActionPlan:
    """Fail closed; a governed compiler must own all future model execution."""

    _ = (
        request_id,
        session_id,
        goal,
        vision_context,
        llm_runtime,
        image_upload_allowed,
        stop_on_error,
        need_screenshot_feedback,
    )
    raise RuntimeError(VISUAL_AGENT_GOVERNED_ADMISSION_REQUIRED)


# ---------------------------------------------------------------------------
# 内部辅助：解析 LLM 返回的 JSON
# ---------------------------------------------------------------------------

def _parse_llm_response(
    *,
    raw_text: str,
    request_id: str,
    session_id: str,
    goal: str,
    stop_on_error: bool,
    need_screenshot_feedback: bool,
    model_attempt: Mapping[str, Any] | None = None,
) -> ActionPlan:
    """将 LLM 返回的 JSON 文本解析为 ActionPlan。"""
    if not raw_text:
        return _make_fallback_plan(
            request_id,
            session_id,
            goal,
            reason="EMPTY_RESPONSE",
            model_attempt=model_attempt,
        )

    # 提取 JSON 块（LLM 有时会在 JSON 前后加说明文字）
    json_text = _extract_json_block(raw_text)
    if not json_text:
        _logger.warning("action_planner: 无法从 LLM 响应中提取 JSON", extra={"request_id": request_id, "raw": raw_text[:200]})
        return _make_fallback_plan(
            request_id,
            session_id,
            goal,
            reason="JSON_PARSE_FAILED",
            model_attempt=model_attempt,
        )

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        _logger.warning("action_planner: JSON 解析失败", extra={"request_id": request_id, "error": str(exc)})
        return _make_fallback_plan(
            request_id,
            session_id,
            goal,
            reason="JSON_DECODE_ERROR",
            model_attempt=model_attempt,
        )

    # 解析 actions 列表
    raw_actions_value = data.get("actions") or []
    if isinstance(raw_actions_value, dict):
        raw_actions: List[Dict[str, Any]] = [raw_actions_value]
    elif isinstance(raw_actions_value, list):
        raw_actions = [item for item in raw_actions_value if isinstance(item, dict)]
    else:
        raw_actions = []
    actions: List[Action] = []
    for item in raw_actions:
        try:
            action_type_str = str(item.get("type") or "").lower()
            # 跳过未知类型
            if action_type_str not in {t.value for t in ActionType}:
                _logger.debug("action_planner: 跳过未知操作类型 %s", action_type_str)
                continue
            actions.append(Action(
                type=ActionType(action_type_str),
                target_desc=str(item.get("target_desc") or ""),
                x=item.get("x"),
                y=item.get("y"),
                to_x=item.get("to_x"),
                to_y=item.get("to_y"),
                text=item.get("text"),
                key=item.get("key"),
                direction=item.get("direction"),
                steps=item.get("steps"),
                wait_ms=item.get("wait_ms"),
                assert_text=item.get("assert_text"),
                assert_element=item.get("assert_element"),
                screenshot_label=item.get("screenshot_label"),
                extra=dict(item.get("extra") or {}),
            ))
        except Exception as exc:
            _logger.debug("action_planner: 跳过无效指令 %s: %s", item, exc)
            continue

    if not actions:
        return _make_fallback_plan(
            request_id,
            session_id,
            goal,
            reason="NO_VALID_ACTIONS",
            model_attempt=model_attempt,
        )

    return ActionPlan(
        request_id=request_id,
        session_id=session_id,
        goal=goal,
        actions=actions,
        stop_on_error=stop_on_error,
        need_screenshot_feedback=need_screenshot_feedback,
        extra={
            "execution_authority": "NONE_PROPOSAL_ONLY",
            "goal_understood": str(data.get("goal_understood") or ""),
            "visual_summary": str(data.get("visual_summary") or data.get("screen_summary") or data.get("scene_summary") or ""),
            "confidence": _safe_float(data.get("confidence"), default=0.0),
            "notes": str(data.get("notes") or ""),
            "model_attempt": dict(model_attempt or {}),
        },
    )


def _safe_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _extract_json_block(text: str) -> str:
    """从文本中提取第一个完整的 JSON 对象块。"""
    # 尝试直接解析
    text = text.strip()
    if text.startswith("{"):
        return text

    # 查找 ```json ... ``` 代码块
    import re
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        return match.group(1).strip()

    # 查找第一个 { 到最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]

    return ""


def _make_fallback_plan(
    request_id: str,
    session_id: str,
    goal: str,
    reason: str = "UNKNOWN",
    model_attempt: Mapping[str, Any] | None = None,
) -> ActionPlan:
    """生成降级 ActionPlan（仅包含一条 DONE 指令，标记失败）。"""
    return ActionPlan(
        request_id=request_id,
        session_id=session_id,
        goal=goal,
        actions=[
            Action(
                type=ActionType.DONE,
                target_desc=f"指令生成失败（{reason}），无法执行自动化",
            )
        ],
        stop_on_error=True,
        need_screenshot_feedback=False,
        extra={
            "execution_authority": "NONE_PROPOSAL_ONLY",
            "fallback": True,
            "reason": reason,
            "model_attempt": dict(model_attempt or {}),
        },
    )
