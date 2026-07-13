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

from host_brain.composition.process_services import get_model_attempt_service, get_prompt_registry
from host_brain.model_runtime.prompt_contract import resolve_prompt_contract_json
from host_brain.model_runtime import (
    ModelAttemptRequest,
    OutputContract,
    PromptRef,
    VISION_ACTION_PLANNER_PROMPT_ID,
    model_attempt_result_trace,
)

from .clawdbot_protocol import (
    Action,
    ActionPlan,
    ActionType,
)

_logger = logging.getLogger("Natsume.action_planner")

_DEFAULT_SYSTEM_PROMPT = """你是一个谨慎的桌面视觉自动化规划器。
只输出一个 JSON 对象，不要输出 Markdown 或额外解释。
如果目标只是观察、描述或要求不要操作，返回单个 done 动作。
允许的 actions[].type: click, right_click, double_click, type, key, scroll, drag, wait, screenshot, done。
不要编造屏幕上不存在的元素；不确定时用 done 并在 target_desc 说明原因。
"""

_DEFAULT_USER_PROMPT_TEMPLATE = """用户目标：
{goal}

视觉上下文：
{vision_context_section}

请返回 JSON：
{{
  "goal_understood": "一句话复述目标",
  "visual_summary": "一句话说明你从截图或视觉上下文里实际看到了什么；如果没有图像，只说明文字上下文",
  "confidence": 0.0,
  "notes": "必要时说明不确定性",
  "actions": [
    {{"type": "done", "target_desc": "只观察，不执行操作"}}
  ]
}}
"""


def _load_template_mapping(template_id: str) -> dict[str, Any]:
    raw = resolve_prompt_contract_json(template_id)
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
    timeout_ms = int(llm_runtime.get("cloud_timeout_ms") or llm_runtime.get("timeout_ms") or 20000)
    templates = _load_template_mapping("vision.action_planner.prompt_templates")

    # 构建 Prompt
    from host_brain.app.chat_handler_vision import _build_vision_context_section, _has_image_input
    vision_section = _build_vision_context_section(vision_context)
    user_prompt = _mapping_text(
        templates,
        "user_prompt_template",
        default=_DEFAULT_USER_PROMPT_TEMPLATE,
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
                default=_DEFAULT_SYSTEM_PROMPT,
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

    try:
        prompt_ref = PromptRef(
            prompt_id=VISION_ACTION_PLANNER_PROMPT_ID,
            version="v1.0.0",
            variables={"messages": tuple(messages)},
            output_contract=OutputContract(kind="json_object"),
        )
        get_prompt_registry().render(prompt_ref)
        result = await get_model_attempt_service().complete(
            ModelAttemptRequest(
                request_id=f"{request_id}:action-plan",
                scene_type="VISION_ACTION_PLANNER",
                cognitive_tier="B",
                prompt=prompt_ref,
                deadline_ms=max(5_000, timeout_ms),
                foreground=True,
                metadata={
                    "caller": "vision_action_planner",
                    "parent_request_id": request_id,
                    "session_id": session_id,
                    "has_image": bool(has_image),
                },
            )
        )
        attempt_trace = model_attempt_result_trace(result)
        if not result.success:
            return _make_fallback_plan(
                request_id,
                session_id,
                goal,
                reason=f"MODEL_ATTEMPT_ERROR:{result.error_code or 'FAILED'}",
                model_attempt=attempt_trace,
            )

        # 提取 LLM 返回的 JSON 文本
        raw_text = str(result.content or "").strip()

        return _parse_llm_response(
            raw_text=raw_text,
            request_id=request_id,
            session_id=session_id,
            goal=goal,
            stop_on_error=stop_on_error,
            need_screenshot_feedback=need_screenshot_feedback,
            model_attempt=attempt_trace,
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
            "fallback": True,
            "reason": reason,
            "model_attempt": dict(model_attempt or {}),
        },
    )
