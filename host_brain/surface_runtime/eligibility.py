"""Surface 主链的资格判定与场景消费边界。

此模块只判断用户可见文本是否应进入 Surface，以及如何把已冻结 Scene 投影为表达限制；
它不生成内容、不重新决策场景，也不把子代理的确定性结果包装成陪伴对话。
"""

from __future__ import annotations

from typing import Any
from collections.abc import Mapping

from host_brain.persona.response_form_contract import normalize_response_form
from host_brain.runtime.scene_consumer_projection import (
    SceneConsumerProjectionError,
    project_scene_decision_v2,
    surface_fail_closed_projection,
)
import os

_TEXT_RESPONSE_FORMS = {"", "text", "plain_text", "chat_text", "zh_text"}
_DETERMINISTIC_RESULT_AGENT_IDS = {
    "system_info",
    "system_resource_snapshot",
    "process_resource_top",
    "resource_pressure_analysis",
    "resource_manager_status",
    "resource_release",
    "process_list",
    "network_status",
    "open_app",
    "plan_execution",
    "recent_turn_direct",
    "task_progress_direct",
}


def _safe_mapping(value: Any) -> dict[str, Any]:
    """把外部可选对象复制为普通字典；错误形状不传播到资格判断。"""
    return dict(value) if isinstance(value, Mapping) else {}


def _reply_plan(agent_input: Any) -> dict[str, Any]:
    """按已冻结控制包优先级读取回复计划，不在资格层创造新的计划。"""
    for raw in (
        getattr(agent_input, "surface_task_control", None),
        getattr(agent_input, "reply_plan", None),
        getattr(agent_input, "render_policy", None),
    ):
        if isinstance(raw, Mapping) and raw:
            # surface_task_control 优先，确保后续兼容字段不能覆盖主链已同步的控制面。
            return dict(raw)
    return {}


def _response_form(agent_input: Any) -> str:
    """从运行时、回复计划和输入字段解析规范回复形式，优先使用已冻结决定。"""
    runtime = _safe_mapping(getattr(agent_input, "llm_runtime", None))
    return normalize_response_form(
        str(
            getattr(agent_input, "response_form", "")
            or runtime.get("response_form")
            or _reply_plan(agent_input).get("response_form")
            or "text"
        )
    )


def _has_multimodal_payload(agent_input: Any) -> bool:
    """判断是否存在附件/观测输入；是否可叙述仍由后续证据检查决定。"""
    return bool(
        getattr(agent_input, "attachments", None)
        or _safe_mapping(getattr(agent_input, "multimodal_observations", None))
    )


def _multimodal_surface_main_chain_supported(agent_input: Any) -> bool:
    # Surface 只能消费已有摘要或观测；不能凭未知附件猜测图像、贴纸或语音内容。
    """确认多模态输入已有足够摘要/观察，避免 Surface 对未知附件作无依据叙述。"""
    observations = _safe_mapping(getattr(agent_input, "multimodal_observations", None))
    if str(getattr(agent_input, "attachment_summary", "") or "").strip():
        return True
    if str(observations.get("core_observation") or "").strip() or list(observations.get("items") or []):
        return True
    attachments = list(getattr(agent_input, "attachments", None) or [])
    types = {
        str(_safe_mapping(item).get("type") or "").strip().lower()
        for item in attachments
        if item
    }
    if types & {"image", "emoji", "sticker"}:
        return True
    return str(getattr(agent_input, "user_message", "") or "").strip() in {
        "[图片消息]", "[表情消息]", "[贴纸消息]", "[动画表情]"
    }


def _surface_user_visible_reply_context(agent_input: Any) -> bool:
    # 无用户可见上下文时不为附件失败等内部状态生成陪伴式文本，避免误导性主动输出。
    return bool(
        str(getattr(agent_input, "user_message", "") or "").strip()
        or getattr(agent_input, "attachments", None)
        or str(getattr(agent_input, "attachment_summary", "") or "").strip()
        or _safe_mapping(getattr(agent_input, "multimodal_observations", None))
    )


def _surface_main_chain_default_enabled() -> bool:
    """读取运维开关；默认启用，显式 legacy/off 才允许关闭主链。"""
    raw = str(os.environ.get("HOST_SURFACE_MAIN_CHAIN_ENABLED") or "").strip().lower()
    # 默认开启，仅显式 legacy/off 才关闭；部署缺变量不能意外回退到旧未治理路径。
    return raw not in {"0", "false", "no", "off", "legacy"}
def surface_main_chain_enabled(agent_input: Any, *, sub_agent_ctx: Any | None = None) -> bool:
    # 同时满足部署开关和请求资格时才启用主链。
    """综合部署开关和请求资格决定 Surface 是否可用。"""
    if not _surface_main_chain_default_enabled():
        return False
    return surface_main_chain_required(agent_input, sub_agent_ctx=sub_agent_ctx)


def surface_main_chain_required(agent_input: Any, *, sub_agent_ctx: Any | None = None) -> bool:
    # 不支持的多模态只能选择有文本证据的安全回退，不能以附件存在作为事实依据。
    """仅让用户可见文本进入主链；不支持的多模态输入保留文本安全回退。"""
    if _sub_agent_result_should_bypass_surface(sub_agent_ctx):
        # 确定性工具结果应原样交付；再经人格化改写会损害翻译、状态查询等准确性。
        return False
    response_form = _response_form(agent_input)
    if _has_multimodal_payload(agent_input) and not _multimodal_surface_main_chain_supported(agent_input):
        # 附件不可解释时仅允许保守文本回应，不把“有附件”本身当成视觉事实。
        return response_form in _TEXT_RESPONSE_FORMS and _surface_user_visible_reply_context(agent_input)
    return response_form in _TEXT_RESPONSE_FORMS


def _sub_agent_result_should_bypass_surface(sub_agent_ctx: Any | None) -> bool:
    # 此绕过只覆盖已知确定性 Agent；未知 Agent 默认仍走常规资格链而非擅自跳过。
    """确定性工具结果跳过陪伴 Surface，避免翻译/状态查询被二次改写。"""
    if sub_agent_ctx is None:
        return False
    agent_id = str(getattr(sub_agent_ctx, "agent_id", "") or "").strip()
    if not agent_id:
        return False
    if agent_id in {"translate_agent", "intent_agent"}:
        return True
    return agent_id in _DETERMINISTIC_RESULT_AGENT_IDS


def _surface_scene_guardrails(
    *,
    scene_decision_v2: Any,
    scene_identity_v2: Any,
    user_goal_ref: str,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    # Scene 决策只能投影为表达约束；投影/身份校验失败时退到保守 guardrails，不重新裁决场景。
    """把已冻结 Scene 投影为仅表达层限制；投影失败即闭合为保守场景约束。"""

    try:
        projection = project_scene_decision_v2(
            scene_decision_v2,
            expected_identity=scene_identity_v2,
        )
    except SceneConsumerProjectionError as exc:
        # fail-closed 投影仍保留 user_goal_ref，使安全降级可与原始目标关联而不暴露决策正文。
        return surface_fail_closed_projection(exc, user_goal_ref=user_goal_ref)
    return (
        projection.expression_projection(user_goal_ref=user_goal_ref),
        projection.surface_guardrails(),
    )
