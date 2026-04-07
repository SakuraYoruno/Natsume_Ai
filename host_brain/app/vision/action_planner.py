"""视觉指令生成器（Action Planner）。

职责：
  接收用户目标 + 屏幕截图 → 调用 LLM → 生成结构化 ActionPlan
  供 ClawdBot 直接执行（response_format="actions" 模式）

与 chat_handler_vision.py 的区别：
  chat_handler_vision  → response_format="text"  → 返回自然语言建议（给用户看）
  action_planner       → response_format="actions" → 返回 ActionPlan JSON（给 ClawdBot 执行）
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Mapping, Optional

from host_brain.app.prompt_template_store import resolve_prompt_template_json_merged

from .clawdbot_protocol import (
    Action,
    ActionPlan,
    ActionType,
)

_logger = logging.getLogger("Natsume.action_planner")


def _load_template_mapping(template_id: str) -> dict[str, Any]:
    raw = resolve_prompt_template_json_merged(template_id)
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

# ---------------------------------------------------------------------------
# LLM Prompt：指令生成
# ---------------------------------------------------------------------------

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
    """调用 LLM 分析截图，生成结构化 ActionPlan。

    Args:
        goal:                    用户的自动化目标（自然语言）
        vision_context:          视觉上下文（含截图 base64/url 或文字描述）
        llm_runtime:             LLM 运行时配置
        image_upload_allowed:    是否允许上传图片给 LLM
        stop_on_error:           遇到错误时是否停止
        need_screenshot_feedback: 是否需要执行后截图反馈（Agent Loop 模式）

    Returns:
        ActionPlan 实例（即使 LLM 失败也会返回降级计划）
    """
    import asyncio as _asyncio

    provider_fn = llm_runtime.get("cloud_provider_fn") or llm_runtime.get("provider_fn")
    model_id = str(llm_runtime.get("cloud_model_id") or llm_runtime.get("model_id") or "gemini-2.5-flash")
    api_key = str(llm_runtime.get("cloud_llm_api_key") or llm_runtime.get("llm_api_key") or "").strip()
    timeout_ms = int(llm_runtime.get("cloud_timeout_ms") or llm_runtime.get("timeout_ms") or 20000)
    available = bool(llm_runtime.get("cloud_available", llm_runtime.get("available", False)))
    templates = _load_template_mapping("vision.action_planner.prompt_templates")

    # LLM 不可用时返回降级计划
    if not available or not api_key or provider_fn is None:
        _logger.warning("action_planner: LLM 不可用，返回降级计划", extra={"request_id": request_id})
        return _make_fallback_plan(request_id, session_id, goal, reason="LLM_UNAVAILABLE")

    # 构建 Prompt
    from host_brain.app.chat_handler_vision import _build_vision_context_section, _has_image_input
    vision_section = _build_vision_context_section(vision_context)
    user_prompt = _mapping_text(
        templates,
        "user_prompt_template",
        goal=goal,
        vision_context_section=vision_section,
    )

    # 构建消息（支持多模态）
    has_image = _has_image_input(vision_context) and image_upload_allowed
    image_base64 = str((vision_context or {}).get("image_base64") or "").strip() if has_image else ""
    image_url = str((vision_context or {}).get("image_url") or "").strip() if has_image else ""
    image_mime = str((vision_context or {}).get("image_mime") or "image/png").strip()

    messages: list[dict] = [
        {
            "role": "system",
            "content": _mapping_text(
                templates,
                "system_prompt",
            ),
        },
    ]
    if has_image and image_base64:
        messages.append({
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:{image_mime};base64,{image_base64}"}},
                {"type": "text", "text": user_prompt},
            ],
        })
    elif has_image and image_url:
        messages.append({
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": image_url}},
                {"type": "text", "text": user_prompt},
            ],
        })
    else:
        messages.append({"role": "user", "content": user_prompt})

    payload = {
        "messages": messages,
        "model": model_id,
        "max_tokens": 1200,
        "temperature": 0.2,  # 指令生成需要低温度，保证稳定性
    }

    try:
        timeout_sec = max(5.0, float(timeout_ms) / 1000.0)
        result = await _asyncio.wait_for(
            _asyncio.to_thread(provider_fn, payload),
            timeout=timeout_sec,
        )

        # 提取 LLM 返回的 JSON 文本
        raw_text = ""
        if isinstance(result, dict):
            choices = result.get("choices") or []
            if choices and isinstance(choices[0], dict):
                msg = choices[0].get("message") or {}
                raw_text = str(msg.get("content") or "").strip()
            if not raw_text:
                raw_text = str(result.get("content") or result.get("text") or "").strip()

        return _parse_llm_response(
            raw_text=raw_text,
            request_id=request_id,
            session_id=session_id,
            goal=goal,
            stop_on_error=stop_on_error,
            need_screenshot_feedback=need_screenshot_feedback,
        )

    except Exception as exc:
        _logger.warning(
            "action_planner: LLM 调用失败",
            extra={"request_id": request_id, "error": str(exc)},
        )
        return _make_fallback_plan(request_id, session_id, goal, reason=f"LLM_ERROR:{type(exc).__name__}")


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
) -> ActionPlan:
    """将 LLM 返回的 JSON 文本解析为 ActionPlan。"""
    if not raw_text:
        return _make_fallback_plan(request_id, session_id, goal, reason="EMPTY_RESPONSE")

    # 提取 JSON 块（LLM 有时会在 JSON 前后加说明文字）
    json_text = _extract_json_block(raw_text)
    if not json_text:
        _logger.warning("action_planner: 无法从 LLM 响应中提取 JSON", extra={"request_id": request_id, "raw": raw_text[:200]})
        return _make_fallback_plan(request_id, session_id, goal, reason="JSON_PARSE_FAILED")

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        _logger.warning("action_planner: JSON 解析失败", extra={"request_id": request_id, "error": str(exc)})
        return _make_fallback_plan(request_id, session_id, goal, reason="JSON_DECODE_ERROR")

    # 解析 actions 列表
    raw_actions: List[Dict[str, Any]] = data.get("actions") or []
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
        return _make_fallback_plan(request_id, session_id, goal, reason="NO_VALID_ACTIONS")

    return ActionPlan(
        request_id=request_id,
        session_id=session_id,
        goal=goal,
        actions=actions,
        stop_on_error=stop_on_error,
        need_screenshot_feedback=need_screenshot_feedback,
        extra={
            "goal_understood": str(data.get("goal_understood") or ""),
            "confidence": float(data.get("confidence") or 0.0),
            "notes": str(data.get("notes") or ""),
        },
    )


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
        extra={"fallback": True, "reason": reason},
    )
