"""Canonical runtime responsibility extracted from the former app hotspot."""

from __future__ import annotations

from typing import Any
from collections.abc import AsyncGenerator, Mapping, Sequence

from host_brain.persona.response_form_contract import normalize_response_form
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
    return dict(value) if isinstance(value, Mapping) else {}


def _reply_plan(agent_input: Any) -> dict[str, Any]:
    for raw in (
        getattr(agent_input, "surface_task_control", None),
        getattr(agent_input, "reply_plan", None),
        getattr(agent_input, "render_policy", None),
    ):
        if isinstance(raw, Mapping) and raw:
            return dict(raw)
    return {}


def _response_form(agent_input: Any) -> str:
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
    return bool(
        getattr(agent_input, "attachments", None)
        or _safe_mapping(getattr(agent_input, "multimodal_observations", None))
    )


def _multimodal_surface_main_chain_supported(agent_input: Any) -> bool:
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
    return bool(
        str(getattr(agent_input, "user_message", "") or "").strip()
        or getattr(agent_input, "attachments", None)
        or str(getattr(agent_input, "attachment_summary", "") or "").strip()
        or _safe_mapping(getattr(agent_input, "multimodal_observations", None))
    )


def _surface_main_chain_default_enabled() -> bool:
    raw = str(os.environ.get("HOST_SURFACE_MAIN_CHAIN_ENABLED") or "").strip().lower()
    return raw not in {"0", "false", "no", "off", "legacy"}
def surface_main_chain_enabled(agent_input: Any, *, sub_agent_ctx: Any | None = None) -> bool:
    if not _surface_main_chain_default_enabled():
        return False
    return surface_main_chain_required(agent_input, sub_agent_ctx=sub_agent_ctx)


def surface_main_chain_required(agent_input: Any, *, sub_agent_ctx: Any | None = None) -> bool:
    if _sub_agent_result_should_bypass_surface(sub_agent_ctx):
        return False
    response_form = _response_form(agent_input)
    if _has_multimodal_payload(agent_input) and not _multimodal_surface_main_chain_supported(agent_input):
        return response_form in _TEXT_RESPONSE_FORMS and _surface_user_visible_reply_context(agent_input)
    return response_form in _TEXT_RESPONSE_FORMS


def _sub_agent_result_should_bypass_surface(sub_agent_ctx: Any | None) -> bool:
    if sub_agent_ctx is None:
        return False
    agent_id = str(getattr(sub_agent_ctx, "agent_id", "") or "").strip()
    if not agent_id:
        return False
    if agent_id in {"translate_agent", "intent_agent"}:
        return True
    return agent_id in _DETERMINISTIC_RESULT_AGENT_IDS or agent_id.startswith("skill:hub_skill_")


def _surface_scene_guardrails(
    *,
    scene_awareness: Mapping[str, Any] | None,
    task_control: Mapping[str, Any] | None,
    user_goal_ref: str,
    now_ms: int,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Project SceneDecision into expression-only Surface constraints."""
    from host_brain.persona.scene_policy import build_scene_decision, life_projection_enabled  # noqa: PLC0415

    control = dict(task_control or {}) if isinstance(task_control, Mapping) else {}
    enabled = life_projection_enabled()
    decision = build_scene_decision(
        scene_awareness=scene_awareness,
        task_mode=str(control.get("task_mode") or control.get("response_move") or ""),
        user_goal_ref=user_goal_ref,
        now_ms=int(now_ms),
        enabled=enabled,
    )
    payload = decision.as_dict(now_ms=int(now_ms))
    has_scene_observation = isinstance(scene_awareness, Mapping) and any(
        scene_awareness.get(key)
        for key in ("scene_tags", "current_activity", "source_ref", "source_refs", "observed_at_ms")
    )
    if has_scene_observation:
        try:
            from host_brain.persona.life_evolution_observer import observe_scene_decision  # noqa: PLC0415

            observe_scene_decision(
                payload,
                source_kind="surface_main_chain",
                source_ref=user_goal_ref,
            )
        except Exception:
            pass
    if bool(payload.get("expired")) or not enabled:
        return payload, ()
    policy = str(payload.get("proactive_policy") or "ALLOW").upper()
    expression_mode = str(payload.get("expression_mode") or "natural")
    if policy == "BLOCK":
        return payload, (
            f"SceneDecision({expression_mode})：保持简短、安静、任务优先；不得主动扩展话题或追加追问。",
            "SceneDecision 只能收紧表达，绝不能改写、取消或替换用户已经明确的任务目标。",
        )
    if policy == "CONTEXTUAL":
        return payload, (
            f"SceneDecision({expression_mode})：表达必须贴合当前任务/场景，最多一次必要追问，不发通用主动寒暄。",
            "场景、情绪和关系信号不得扩大权限、改变任务范围或覆盖显式用户目标。",
        )
    return payload, ()
