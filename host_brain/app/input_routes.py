"""Unified Host input route for ASR / Gateway events."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from host_brain.app.prompt_template_store import resolve_prompt_template_json_merged
from host_brain.core.ids import new_request_id

_logger = logging.getLogger("Natsume.input_routes")

router = APIRouter(prefix="/v1/host", tags=["input"])

_PLACEHOLDER_GATEWAY_CONVERSATIONS: set[str] = {
    "active_chat",
    "active chat",
    "current_chat",
    "current chat",
    "current_session",
    "default",
    "unknown",
    "none",
    "null",
    "-",
    "_",
    "na",
    "n/a",
    "当前聊天",
    "当前会话",
}
_ASR_PLACEHOLDER_PATTERN = re.compile(r"^\[[A-Z0-9_:\-]{3,}\]$")
_ASR_SPEECH_CHAR_PATTERN = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]")
_GATEWAY_ACK_PROGRESS_QUERY_RE = re.compile(
    r"(进度|进展|状态|结果呢|有结果吗|完成了?吗|做完了?吗|好了没|搞定了?吗|到哪了|还要多久|eta|done\??|status|progress|update)",
    re.IGNORECASE,
)
_GATEWAY_ACK_PROGRESS_MARKERS: tuple[str, ...] = (
    "进度",
    "进展",
    "状态",
    "结果呢",
    "有结果吗",
    "完成了吗",
    "做完了吗",
    "搞定了吗",
    "好了没",
    "到哪了",
    "到哪一步",
    "还要多久",
    "什么时候好",
    "status",
    "progress",
    "update",
    "done yet",
    "eta",
)
_GATEWAY_ACK_ACTION_MARKERS: tuple[str, ...] = (
    "请",
    "麻烦",
    "帮我",
    "帮忙",
    "需要你",
    "执行",
    "处理",
    "安排",
    "规划",
    "调用",
    "查询",
    "查一下",
    "查下",
    "检索",
    "搜索",
    "总结",
    "归纳",
    "翻译",
    "改写",
    "写一个",
    "生成",
    "实现",
    "改造",
    "优化",
    "修复",
    "排查",
    "测试",
    "验证",
    "启动",
    "停止",
    "重启",
    "run",
    "execute",
    "build",
    "fix",
    "test",
    "deploy",
    "search",
)
_ALIGNMENT_TOKEN_RE = re.compile(r"[A-Za-z]{3,}|[\u4e00-\u9fff]{2,}")
_ALIGNMENT_STOPWORDS: set[str] = {
    "请",
    "麻烦",
    "帮我",
    "帮忙",
    "一下",
    "一下子",
    "这个",
    "那个",
    "今天",
    "现在",
    "然后",
    "继续",
    "处理",
    "执行",
    "安排",
    "操作",
    "任务",
    "结果",
    "进度",
    "状态",
    "一下吧",
    "the",
    "and",
    "for",
    "with",
    "this",
    "that",
}

_GATEWAY_ROUTE_ACTION_PREFIXES: tuple[str, ...] = ("/", "!", "run ", "exec ")
_GATEWAY_FORCE_TRANSLATE_RE = re.compile(
    r"(翻译|译成|翻成|translate|translation|中译英|英译中|中译日|日译中)",
    re.IGNORECASE,
)
_GATEWAY_FORCE_MEMORY_RE = re.compile(
    r"(记住|记一下|记下来|写入记忆|保存.*记忆|记到记忆|remember this|save this memory|偏好是|我的偏好)",
    re.IGNORECASE,
)
_GATEWAY_FORCE_CODE_RE = re.compile(
    r"(报错|错误|异常|修复|修一下|改代码|写代码|单测|测试|diff|patch|traceback|stack trace|compile|build|修.*bug|bug.*修)",
    re.IGNORECASE,
)
_GATEWAY_RECENT_TURN_RE = re.compile(
    r"(我刚刚|上一句|上一条|上句话|你刚刚|what did i just say|what did you just say)",
    re.IGNORECASE,
)
_ALLOWED_LLM_ROUTE_TYPES: set[str] = {
    "chat",
    "delegate",
    "skill",
    "plan",
    "recent_turn",
    "task_progress",
}
_ALLOWED_DELEGATE_TARGETS: set[str] = {
    "intent_agent",
    "code_agent",
    "translate_agent",
    "vision_agent",
    "repair_agent",
    "retrieval_agent",
    "judge_agent",
}
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


def _load_template_mapping(template_id: str) -> dict[str, Any]:
    raw = resolve_prompt_template_json_merged(template_id)
    return dict(raw) if isinstance(raw, dict) else {}


class InputPayload(BaseModel):
    """Ingress payload reported by Edge."""

    source: str = Field(..., description="Input source: ASR or GATEWAY")
    person_id: str | None = Field(None, description="Optional canonical person id")

    session_id: str | None = Field(None, description="[ASR] Session ID")
    text: str | None = Field(None, description="[ASR] Transcript")
    confidence: float | None = Field(None, description="[ASR] Confidence 0~1")
    is_final: bool | None = Field(None, description="[ASR] Final segment")
    seq: int | None = Field(None, description="[ASR] Sequence")
    alternates: list[dict[str, Any]] | None = Field(None, description="[ASR] N-best candidates")
    evidence_ref: str | None = Field(None, description="[ASR] Evidence ref")

    platform: str | None = Field(None, description="[GATEWAY] Platform: wechat/qq/feishu/wecom")
    msg_id: str | None = Field(None, description="[GATEWAY] Message ID")
    from_id: str | None = Field(None, description="[GATEWAY] Sender ID")
    to_id: str | None = Field(None, description="[GATEWAY] Receiver ID")
    conversation_id: str | None = Field(None, description="[GATEWAY] Conversation ID")
    content: str | None = Field(None, description="[GATEWAY] Message content")
    msg_type: str | None = Field(None, description="[GATEWAY] Message type")
    is_group: bool | None = Field(None, description="[GATEWAY] Is group message")
    sender_name: str | None = Field(None, description="[GATEWAY] Sender display name")
    media_url: str | None = Field(None, description="[GATEWAY] Media URL")
    reply_to_msg_id: str | None = Field(None, description="[GATEWAY] Reply-to message ID")
    raw_extra: dict[str, Any] | None = Field(None, description="[GATEWAY] Extra fields")

    ts_ms: int | None = Field(None, description="Event timestamp in ms")


class InputResponse(BaseModel):
    """Ingress response."""

    success: bool
    request_id: str
    source: str
    status: str
    detail: str
    ts_ms: int


def _parse_bool_env(name: str, *, default: bool) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _parse_float_env(name: str, *, default: float) -> float:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def _parse_int_env(name: str, *, default: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(default)


def _gateway_auto_reply_enabled() -> bool:
    return _parse_bool_env("HOST_INPUT_GATEWAY_AUTO_REPLY", default=True)


def _coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    raw = str(value or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _looks_like_asr_placeholder_text(text: str) -> bool:
    normalized = str(text or "").strip().upper()
    if not normalized:
        return True
    return _ASR_PLACEHOLDER_PATTERN.fullmatch(normalized) is not None


def _contains_speech_chars(text: str) -> bool:
    return _ASR_SPEECH_CHAR_PATTERN.search(str(text or "")) is not None


def _evaluate_asr_noise_gate(
    *,
    text: str,
    payload: dict[str, Any] | None,
) -> tuple[bool, str | None]:
    text_value = str(text or "").strip()
    if not text_value:
        return False, "ASR_TEXT_EMPTY"
    if _looks_like_asr_placeholder_text(text_value):
        return False, "ASR_PLACEHOLDER_TEXT"

    min_text_len = max(1, _parse_int_env("HOST_INPUT_ASR_MIN_TEXT_LEN", default=2))
    if len(text_value) < min_text_len:
        return False, "ASR_TEXT_TOO_SHORT"

    require_speech_chars = _parse_bool_env("HOST_INPUT_ASR_REQUIRE_SPEECH_CHARS", default=True)
    if require_speech_chars and not _contains_speech_chars(text_value):
        return False, "ASR_NON_SPEECH_TEXT"

    payload_dict = dict(payload or {})
    reason_code = str(payload_dict.get("reason_code") or "").strip().upper()
    if reason_code in {"ASR_VAD_NO_SPEECH", "ASR_LOW_CONFIDENCE"}:
        return False, reason_code

    low_confidence = _coerce_bool(payload_dict.get("low_confidence"), default=False)
    if low_confidence:
        return False, "ASR_LOW_CONFIDENCE"

    min_confidence = max(
        0.0,
        min(1.0, _parse_float_env("HOST_INPUT_ASR_MIN_CONFIDENCE", default=0.75)),
    )
    require_confidence = _parse_bool_env("HOST_INPUT_ASR_REQUIRE_CONFIDENCE", default=False)
    confidence = _coerce_float(payload_dict.get("confidence"))
    if confidence is None and require_confidence:
        return False, "ASR_CONFIDENCE_MISSING"
    if confidence is not None and confidence < min_confidence:
        return False, "ASR_LOW_CONFIDENCE"

    return True, None


def _is_invalid_gateway_conversation_id(value: str | None) -> bool:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return True
    return normalized in _PLACEHOLDER_GATEWAY_CONVERSATIONS


def _evaluate_immune_gate_for_input(
    *,
    text: str,
    source: str,
    session_id: str,
    person_id: str | None = None,
) -> dict[str, Any]:
    from host_brain.tribe.immune_agent import get_immune_agent  # noqa: PLC0415

    return get_immune_agent().inspect_text(
        text=text,
        source=f"http.v1.host.input.{str(source or '').lower()}",
        metadata={
            "session_id": str(session_id or "").strip() or None,
            "person_id": str(person_id or "").strip() or None,
        },
    )


def _get_skill_registry_safe():
    try:
        from host_brain.app.dependencies import get_skill_registry  # noqa: PLC0415

        return get_skill_registry()
    except Exception:
        return None


def _safe_format_template(text: str, **kwargs: Any) -> str:
    try:
        return str(text or "").format(**kwargs)
    except Exception:
        return str(text or "")


def _build_gateway_skill_manifest_lines(*, skill_registry: Any) -> list[str]:
    templates = _load_template_mapping("executive_router.routing_prompt.templates")
    line_with_description = str(templates.get("skill_line_with_description") or "- {skill_name}: {description}")
    line_without_description = str(templates.get("skill_line_without_description") or "- {skill_name}")
    fallback_lines = [
        str(item).strip()
        for item in list(templates.get("fallback_skill_lines") or [])
        if str(item).strip()
    ]
    if skill_registry is None:
        return fallback_lines

    try:
        max_items = max(8, min(120, _parse_int_env("HOST_GATEWAY_INTENT_SKILL_MANIFEST_MAX", default=64)))
        max_risk_level = max(1, min(5, _parse_int_env("HOST_GATEWAY_INTENT_SKILL_MAX_RISK_LEVEL", default=5)))
        entries = skill_registry.build_skill_manifest(
            scopes=["host", "memory", "system"],
            max_risk_level=max_risk_level,
        )
    except Exception:
        return fallback_lines

    lines: list[str] = []
    for row in list(entries or [])[:max_items]:
        skill_name = str(row.get("name") or "").strip()
        if not skill_name:
            continue
        description = str(row.get("description") or "").strip()
        if description:
            line = _safe_format_template(
                line_with_description,
                skill_name=skill_name,
                description=description,
            )
        else:
            line = _safe_format_template(
                line_without_description,
                skill_name=skill_name,
            )
        normalized_line = str(line).strip()
        if normalized_line:
            lines.append(normalized_line)
    return lines or fallback_lines


def _extract_first_json_object(raw_text: str) -> dict[str, Any] | None:
    text = str(raw_text or "").strip()
    if not text:
        return None

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    fenced_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, re.IGNORECASE)
    if fenced_match:
        try:
            parsed = json.loads(str(fenced_match.group(1) or "").strip())
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

    start = text.find("{")
    while start >= 0:
        depth = 0
        in_string = False
        escaped = False
        for idx in range(start, len(text)):
            char = text[idx]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : idx + 1]
                    try:
                        parsed = json.loads(candidate)
                        if isinstance(parsed, dict):
                            return parsed
                    except Exception:
                        break
        start = text.find("{", start + 1)
    return None


def _normalize_route_confidence(value: Any) -> float | None:
    confidence = _coerce_float(value)
    if confidence is None:
        return None
    return max(0.0, min(1.0, float(confidence)))


def _normalize_gateway_llm_route_decision(
    *,
    raw_decision: dict[str, Any],
    skill_registry: Any,
) -> dict[str, Any] | None:
    route = str(raw_decision.get("route") or raw_decision.get("route_type") or "").strip().lower()
    if route not in _ALLOWED_LLM_ROUTE_TYPES:
        return None

    target_id = str(
        raw_decision.get("target_id")
        or raw_decision.get("skill_id")
        or raw_decision.get("agent_id")
        or raw_decision.get("delegate_agent")
        or ""
    ).strip()
    task = str(raw_decision.get("task") or raw_decision.get("query") or raw_decision.get("text") or "").strip()
    reason = str(raw_decision.get("reason") or raw_decision.get("reason_code") or "LLM_ROUTE").strip() or "LLM_ROUTE"
    confidence = _normalize_route_confidence(raw_decision.get("confidence"))

    if route in {"chat", "recent_turn", "task_progress"}:
        target_id = ""
    elif route == "plan":
        target_id = target_id or "skill_dag"
    elif route == "delegate":
        if target_id not in _ALLOWED_DELEGATE_TARGETS:
            target_id = "intent_agent"
            reason = "LLM_ROUTE_INVALID_DELEGATE_TARGET"
    elif route == "skill":
        if not target_id:
            return None
        if skill_registry is None:
            return None
        try:
            if skill_registry.get(target_id) is None:
                return None
        except Exception:
            return None

    return {
        "route": route,
        "target_id": target_id,
        "task": task,
        "reason_code": reason,
        "confidence": confidence,
        "route_source": "llm",
        "llm_route_used": True,
    }


def _build_gateway_rule_route_decision(
    *,
    text: str,
    skill_registry: Any,
) -> dict[str, Any]:
    normalized = str(text or "").strip()
    if not normalized:
        return {
            "route": "chat",
            "target_id": "",
            "task": "",
            "reason_code": "RULE_EMPTY_TEXT",
            "confidence": None,
            "route_source": "rule",
            "llm_route_used": False,
        }
    if _looks_like_progress_query_for_ack_skip(normalized):
        return {
            "route": "task_progress",
            "target_id": "",
            "task": "",
            "reason_code": "RULE_TASK_PROGRESS",
            "confidence": 0.98,
            "route_source": "rule",
            "llm_route_used": False,
        }
    if _GATEWAY_RECENT_TURN_RE.search(normalized):
        return {
            "route": "recent_turn",
            "target_id": "",
            "task": "",
            "reason_code": "RULE_RECENT_TURN",
            "confidence": 0.95,
            "route_source": "rule",
            "llm_route_used": False,
        }
    if _GATEWAY_FORCE_TRANSLATE_RE.search(normalized):
        return {
            "route": "delegate",
            "target_id": "translate_agent",
            "task": normalized,
            "reason_code": "RULE_FORCE_TRANSLATE_AGENT",
            "confidence": 0.95,
            "route_source": "rule",
            "llm_route_used": False,
        }
    if _GATEWAY_FORCE_MEMORY_RE.search(normalized):
        memory_skill_exists = False
        try:
            memory_skill_exists = bool(skill_registry and skill_registry.get("memory_store") is not None)
        except Exception:
            memory_skill_exists = False
        if memory_skill_exists:
            return {
                "route": "skill",
                "target_id": "memory_store",
                "task": normalized,
                "reason_code": "RULE_FORCE_MEMORY_STORE",
                "confidence": 0.95,
                "route_source": "rule",
                "llm_route_used": False,
            }
        return {
            "route": "delegate",
            "target_id": "intent_agent",
            "task": normalized,
            "reason_code": "RULE_FORCE_MEMORY_FALLBACK_INTENT",
            "confidence": 0.90,
            "route_source": "rule",
            "llm_route_used": False,
        }
    if _GATEWAY_FORCE_CODE_RE.search(normalized):
        return {
            "route": "delegate",
            "target_id": "code_agent",
            "task": normalized,
            "reason_code": "RULE_FORCE_CODE_AGENT",
            "confidence": 0.95,
            "route_source": "rule",
            "llm_route_used": False,
        }
    compact = normalized.lower()
    if compact.startswith(_GATEWAY_ROUTE_ACTION_PREFIXES) or _looks_like_execution_intent_for_ack(normalized):
        return {
            "route": "delegate",
            "target_id": "intent_agent",
            "task": normalized,
            "reason_code": "RULE_ACTION_INTENT",
            "confidence": 0.85,
            "route_source": "rule",
            "llm_route_used": False,
        }
    return {
        "route": "chat",
        "target_id": "",
        "task": "",
        "reason_code": "RULE_CHAT_DEFAULT",
        "confidence": 0.75,
        "route_source": "rule",
        "llm_route_used": False,
    }


async def _run_gateway_intent_route_llm(
    *,
    request_id: str,
    session_id: str,
    text: str,
    skill_registry: Any,
) -> dict[str, Any] | None:
    if not _parse_bool_env("HOST_GATEWAY_INTENT_LLM_ENABLED", default=True):
        return None

    from host_brain.app.graph_config_builder import (  # noqa: PLC0415
        resolve_executive_router_llm_runtime,
        resolve_host_llm_runtime_async,
    )
    from host_brain.integrations.llm_helpers import astream_llm_chunks  # noqa: PLC0415

    llm_runtime = await resolve_host_llm_runtime_async(
        rollout_key=session_id,
        scene_type="EXEC_INTENT",
    )
    router_runtime = resolve_executive_router_llm_runtime(base_runtime=llm_runtime)
    if not bool(router_runtime.get("available")):
        return None

    provider_name = str(router_runtime.get("provider_name") or "").strip()
    model_id = str(router_runtime.get("model_id") or "").strip()
    api_key = str(router_runtime.get("llm_api_key") or "").strip()
    if not provider_name or not model_id or not api_key:
        return None

    timeout_ms = max(
        800,
        _parse_int_env(
            "HOST_GATEWAY_INTENT_LLM_TIMEOUT_MS",
            default=int(router_runtime.get("timeout_ms") or 5000),
        ),
    )
    base_url_override = str(router_runtime.get("base_url_override") or "").strip() or None
    agent_gen_params = dict(router_runtime.get("agent_gen_params") or {"temperature": 0.1, "top_p": 0.8})

    templates = _load_template_mapping("executive_router.routing_prompt.templates")
    skill_lines = _build_gateway_skill_manifest_lines(skill_registry=skill_registry)
    skill_manifest_text = "\n".join(skill_lines) if skill_lines else "- intent_agent: fallback"
    system_prompt_template = str(
        templates.get("system_prompt_template")
        or (
            "你是路由分类器。输出单个 JSON 对象。"
            " route 只能是 chat/delegate/skill/task_progress/recent_turn/plan。"
        )
    )
    if "{skill_manifest}" in system_prompt_template:
        system_prompt = system_prompt_template.replace("{skill_manifest}", skill_manifest_text)
    else:
        system_prompt = _safe_format_template(system_prompt_template, skill_manifest=skill_manifest_text)
    system_prompt += (
        "\n\n严格要求："
        "\n1) 仅输出 JSON 对象，不要 markdown。"
        '\n2) JSON 字段必须包含 {"route","target_id","task","confidence","reason"}。'
        "\n3) confidence 为 0~1 的数字。"
    )
    user_prompt_template = str(templates.get("user_prompt_template") or "用户消息：{message}\n输出 JSON：")
    if "{message}" in user_prompt_template:
        user_prompt = user_prompt_template.replace("{message}", str(text or "").strip())
    else:
        user_prompt = _safe_format_template(user_prompt_template, message=str(text or "").strip())

    raw_chunks: list[str] = []
    async for chunk in astream_llm_chunks(
        provider_name=provider_name,
        model_id=model_id,
        api_key=api_key,
        timeout_ms=timeout_ms,
        prompt_text=user_prompt,
        system_prompt=system_prompt,
        gen_params=agent_gen_params,
        base_url_override=base_url_override,
    ):
        raw_chunks.append(str(chunk or ""))
        if sum(len(item) for item in raw_chunks) >= 8000:
            break
    raw_text = "".join(raw_chunks).strip()
    parsed_obj = _extract_first_json_object(raw_text)
    if not isinstance(parsed_obj, dict):
        _logger.warning("[%s] gateway_intent_llm_parse_failed output=%s", request_id, _build_reply_preview(raw_text, limit=180))
        return None
    return _normalize_gateway_llm_route_decision(
        raw_decision=parsed_obj,
        skill_registry=skill_registry,
    )


def _apply_gateway_force_override(
    *,
    text: str,
    decision: dict[str, Any],
    skill_registry: Any,
) -> dict[str, Any]:
    normalized = str(text or "").strip()
    if not normalized:
        return dict(decision or {})
    route = str(decision.get("route") or "").strip().lower()
    target_id = str(decision.get("target_id") or "").strip()
    patched = dict(decision or {})
    has_translate_intent = _GATEWAY_FORCE_TRANSLATE_RE.search(normalized) is not None
    has_recent_turn_intent = _GATEWAY_RECENT_TURN_RE.search(normalized) is not None

    if _looks_like_progress_query_for_ack_skip(normalized):
        if route != "task_progress":
            patched.update(
                {
                    "route": "task_progress",
                    "target_id": "",
                    "task": "",
                    "reason_code": "FORCE_OVERRIDE_TASK_PROGRESS",
                    "route_source": "override",
                    "route_override_reason": "FORCE_OVERRIDE_TASK_PROGRESS",
                }
            )
        return patched

    # Recent-turn queries are deterministic: force direct recent_turn and never degrade to chat/delegate.
    if has_recent_turn_intent:
        if route != "recent_turn":
            patched.update(
                {
                    "route": "recent_turn",
                    "target_id": "",
                    "task": "",
                    "reason_code": "FORCE_OVERRIDE_RECENT_TURN",
                    "route_source": "override",
                    "route_override_reason": "FORCE_OVERRIDE_RECENT_TURN",
                }
            )
        return patched

    # Translation intent has higher priority than code/debug keywords in mixed sentences.
    if has_translate_intent:
        if not (route == "delegate" and target_id == "translate_agent"):
            patched.update(
                {
                    "route": "delegate",
                    "target_id": "translate_agent",
                    "task": normalized,
                    "reason_code": "FORCE_OVERRIDE_TRANSLATE_AGENT",
                    "route_source": "override",
                    "route_override_reason": "FORCE_OVERRIDE_TRANSLATE_AGENT",
                }
            )
        return patched

    memory_skill_exists = False
    try:
        memory_skill_exists = bool(skill_registry and skill_registry.get("memory_store") is not None)
    except Exception:
        memory_skill_exists = False
    if _GATEWAY_FORCE_MEMORY_RE.search(normalized):
        if memory_skill_exists and not (route == "skill" and target_id == "memory_store"):
            patched.update(
                {
                    "route": "skill",
                    "target_id": "memory_store",
                    "task": normalized,
                    "reason_code": "FORCE_OVERRIDE_MEMORY_STORE",
                    "route_source": "override",
                    "route_override_reason": "FORCE_OVERRIDE_MEMORY_STORE",
                }
            )
            return patched
        if not memory_skill_exists and not (route == "delegate" and target_id == "intent_agent"):
            patched.update(
                {
                    "route": "delegate",
                    "target_id": "intent_agent",
                    "task": normalized,
                    "reason_code": "FORCE_OVERRIDE_MEMORY_FALLBACK_INTENT",
                    "route_source": "override",
                    "route_override_reason": "FORCE_OVERRIDE_MEMORY_FALLBACK_INTENT",
                }
            )
            return patched

    if _GATEWAY_FORCE_CODE_RE.search(normalized) and not (route == "delegate" and target_id == "code_agent"):
        patched.update(
            {
                "route": "delegate",
                "target_id": "code_agent",
                "task": normalized,
                "reason_code": "FORCE_OVERRIDE_CODE_AGENT",
                "route_source": "override",
                "route_override_reason": "FORCE_OVERRIDE_CODE_AGENT",
            }
        )
        return patched

    if route == "chat" and _looks_like_execution_intent_for_ack(normalized):
        patched.update(
            {
                "route": "delegate",
                "target_id": "intent_agent",
                "task": normalized,
                "reason_code": "FORCE_OVERRIDE_ACTION_INTENT",
                "route_source": "override",
                "route_override_reason": "FORCE_OVERRIDE_ACTION_INTENT",
            }
        )
        return patched
    return patched


async def _build_gateway_intent_draft(
    *,
    request_id: str,
    session_id: str,
    source: str,
    text: str,
) -> dict[str, Any]:
    default_draft: dict[str, Any] = {
        "id": f"intent::{request_id}",
        "action": "narrative_only",
        "source": "input_routes",
        "input_source": source,
        "route": "chat",
        "target_id": "",
        "task": "",
        "route_reason": "SOURCE_NON_GATEWAY_DEFAULT_CHAT",
        "route_source": "default",
        "route_confidence": None,
        "llm_route_used": False,
    }
    if str(source or "").strip().upper() != "GATEWAY":
        return default_draft

    skill_registry = _get_skill_registry_safe()
    route_decision = _build_gateway_rule_route_decision(text=text, skill_registry=skill_registry)
    llm_decision: dict[str, Any] | None = None
    try:
        llm_decision = await _run_gateway_intent_route_llm(
            request_id=request_id,
            session_id=session_id,
            text=text,
            skill_registry=skill_registry,
        )
    except Exception as exc:
        _logger.warning("[%s] gateway_intent_llm_route_failed: %s", request_id, exc)

    if llm_decision is not None:
        route_decision = dict(llm_decision)
    route_decision = _apply_gateway_force_override(
        text=text,
        decision=route_decision,
        skill_registry=skill_registry,
    )

    route = str(route_decision.get("route") or "chat").strip().lower()
    target_id = str(route_decision.get("target_id") or "").strip()
    task = str(route_decision.get("task") or "").strip()
    route_reason = str(route_decision.get("reason_code") or "ROUTE_UNKNOWN").strip() or "ROUTE_UNKNOWN"
    route_source = str(route_decision.get("route_source") or "rule").strip() or "rule"
    route_confidence = _normalize_route_confidence(route_decision.get("confidence"))
    llm_route_used = bool(route_decision.get("llm_route_used"))
    action = "narrative_only" if route == "chat" else (target_id or route)

    intent_draft: dict[str, Any] = {
        "id": f"intent::{request_id}",
        "action": action,
        "source": "input_routes",
        "input_source": source,
        "route": route,
        "target_id": target_id,
        "task": task,
        "route_reason": route_reason,
        "route_source": route_source,
        "route_confidence": route_confidence,
        "llm_route_used": llm_route_used,
    }
    override_reason = str(route_decision.get("route_override_reason") or "").strip()
    if override_reason:
        intent_draft["route_override_reason"] = override_reason
    if route == "delegate" and target_id:
        intent_draft["delegate_agent"] = target_id
        intent_draft["agent_id"] = target_id
    elif route == "skill" and target_id:
        intent_draft["skill_id"] = target_id
        intent_draft["skill_name"] = target_id
    return intent_draft


@router.post("/input", response_model=InputResponse)
async def receive_input(body: InputPayload, request: Request) -> InputResponse:
    _ = request
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)
    source = str(body.source or "").upper().strip()

    _logger.info("input_routes: received edge input source=%s request_id=%s", source, request_id)

    if source == "ASR":
        if not body.text or not body.session_id:
            return InputResponse(
                success=False,
                request_id=request_id,
                source=source,
                status="REJECTED",
                detail="ASR input missing required fields: text, session_id",
                ts_ms=ts_ms,
            )
        text = str(body.text).strip()
        session_id = str(body.session_id).strip()
        if body.is_final is not True:
            return InputResponse(
                success=True,
                request_id=request_id,
                source=source,
                status="IGNORED",
                detail="ASR non-final segment ignored",
                ts_ms=ts_ms,
            )
    elif source == "GATEWAY":
        if not body.content or not body.conversation_id or not body.platform:
            return InputResponse(
                success=False,
                request_id=request_id,
                source=source,
                status="REJECTED",
                detail="GATEWAY input missing required fields: content, conversation_id, platform",
                ts_ms=ts_ms,
            )
        if _is_invalid_gateway_conversation_id(body.conversation_id):
            return InputResponse(
                success=False,
                request_id=request_id,
                source=source,
                status="REJECTED",
                detail=f"invalid gateway conversation_id: {body.conversation_id}",
                ts_ms=ts_ms,
            )
        text = str(body.content).strip()
        session_id = str(body.conversation_id).strip()
    else:
        return InputResponse(
            success=False,
            request_id=request_id,
            source=source,
            status="REJECTED",
            detail=f"unsupported source: {source}, only ASR/GATEWAY are accepted",
            ts_ms=ts_ms,
        )

    payload = body.model_dump(exclude_none=True)
    if source == "ASR":
        allow_asr, reject_reason = _evaluate_asr_noise_gate(text=text, payload=payload)
        if not allow_asr:
            _logger.info(
                "input_routes: ASR ignored by noise gate request_id=%s reason=%s",
                request_id,
                reject_reason,
            )
            return InputResponse(
                success=True,
                request_id=request_id,
                source=source,
                status="IGNORED",
                detail=f"ASR ignored by noise gate: {reject_reason}",
                ts_ms=ts_ms,
            )

    immune_result: dict[str, Any] = {}
    try:
        immune_result = _evaluate_immune_gate_for_input(
            text=text,
            source=source,
            session_id=session_id,
            person_id=str(body.person_id or "").strip() or None,
        )
    except Exception as exc:
        _logger.warning("input_routes: immune gate evaluate degraded request_id=%s: %s", request_id, exc)
        immune_result = {"decision": "ALLOW", "reason_codes": ["IMMUNE_EVALUATE_DEGRADED"]}
    immune_decision = str(immune_result.get("decision") or "ALLOW").strip().upper()
    payload["immune_gate"] = {
        "decision": immune_decision,
        "risk_level": immune_result.get("risk_level"),
        "score": immune_result.get("score"),
        "reason_codes": list(immune_result.get("reason_codes") or []),
    }
    if immune_decision == "BLOCK":
        _log_input_event(
            ledger=_get_ledger_safe(),
            request_id=request_id,
            session_id=session_id,
            source=source,
            input_payload=payload,
            phase="REJECTED",
            status="REJECTED",
            reason_code="IMMUNE_BLOCKED_HIGH_RISK",
            detail="immune gate blocked high-risk input",
            ts_ms=ts_ms,
        )
        return InputResponse(
            success=False,
            request_id=request_id,
            source=source,
            status="REJECTED",
            detail="immune gate blocked high-risk input",
            ts_ms=ts_ms,
        )
    if immune_decision == "REPORT":
        _logger.warning(
            "input_routes: immune gate report request_id=%s source=%s reasons=%s",
            request_id,
            source,
            immune_result.get("reason_codes"),
        )

    try:
        from host_brain.bond.user_identity import resolve_person_id  # noqa: PLC0415
        from host_brain.app.dependencies import notify_user_interaction  # noqa: PLC0415

        payload["person_id"] = resolve_person_id(
            str(body.person_id or "").strip() or None,
            session_id=session_id,
            platform=str(body.platform or "").strip() or None,
            from_id=str(body.from_id or "").strip() or None,
            conversation_id=str(body.conversation_id or "").strip() or None,
        )
        notify_user_interaction()
    except Exception:
        pass

    if source == "GATEWAY":
        handled_by_progress_fastpath = await _try_handle_gateway_progress_fastpath(
            request_id=request_id,
            session_id=session_id,
            source=source,
            text=text,
            payload=payload,
            ledger=_get_ledger_safe(),
            ts_ms=ts_ms,
        )
        if handled_by_progress_fastpath:
            return InputResponse(
                success=True,
                request_id=request_id,
                source=source,
                status="COMPLETED",
                detail="gateway progress fastpath replied",
                ts_ms=int(time.time() * 1000),
            )

    try:
        await _dispatch_to_graph(
            request_id=request_id,
            session_id=session_id,
            source=source,
            text=text,
            payload=payload,
            ts_ms=ts_ms,
        )
        return InputResponse(
            success=True,
            request_id=request_id,
            source=source,
            status="ACCEPTED",
            detail="input accepted for processing",
            ts_ms=ts_ms,
        )
    except Exception as exc:
        _logger.error("input_routes: dispatch failed source=%s request_id=%s: %s", source, request_id, exc)
        return InputResponse(
            success=False,
            request_id=request_id,
            source=source,
            status="ERROR",
            detail=f"dispatch failed: {exc}",
            ts_ms=ts_ms,
        )


def _get_ledger_safe():
    try:
        from host_brain.app.dependencies import get_ledger

        return get_ledger()
    except Exception:
        return None


def _log_input_event(
    *,
    ledger,
    request_id: str,
    session_id: str,
    source: str,
    input_payload: dict[str, Any] | None = None,
    phase: str,
    status: str,
    detail: str = "",
    reason_code: str | None = None,
    latency_ms: int | None = None,
    ts_ms: int,
) -> None:
    if ledger is None:
        return
    try:
        payload: dict[str, Any] = {
            "source": source,
            "phase": phase,
            "latency_ms": latency_ms,
        }
        input_preview = _build_input_preview(input_payload, source=source)
        if input_preview:
            payload["input_preview"] = input_preview
        person_id = str((input_payload or {}).get("person_id") or "").strip()
        if person_id:
            payload["person_id"] = person_id
        route_fields = (
            "route_type",
            "route_target",
            "route_task",
            "route_source",
            "route_reason",
            "route_confidence",
            "route_override_reason",
            "llm_route_used",
            "exec_quality_degraded",
            "exec_quality_reason_code",
            "exec_quality_tags",
            "exec_quality_skill_exec_count",
        )
        for field in route_fields:
            value = (input_payload or {}).get(field)
            if value is not None:
                payload[field] = value
        if source == "GATEWAY":
            gateway_fields = (
                "platform",
                "conversation_id",
                "msg_id",
                "msg_type",
                "from_id",
                "to_id",
                "sender_name",
                "media_url",
                "reply_to_msg_id",
                "is_group",
                "decision",
                "gateway_status",
                "gateway_latency_ms",
                "reply_text_len",
                "reply_preview",
                "immediate_ack_status",
                "immediate_ack_latency_ms",
                "immediate_ack_preview",
                "immediate_ack_reason",
                "progress_fastpath_status",
                "progress_fastpath_latency_ms",
                "progress_fastpath_preview",
                "progress_fastpath_reason",
                "progress_push_status",
                "progress_push_reason",
                "progress_push_preview",
                "progress_push_updates",
                "deviation_reason",
                "deviation_overlap_ratio",
                "deviation_retry_applied",
                "deviation_retry_overlap_ratio",
            )
            for field in gateway_fields:
                value = (input_payload or {}).get(field)
                if value is not None:
                    payload[field] = value
        ledger.log_voice_event(
            {
                "request_id": request_id,
                "session_id": session_id,
                "event_type": f"INPUT_{phase}",
                "direction": "EDGE_TO_HOST",
                "status": status,
                "reason_code": reason_code,
                "stage": f"input_dispatch.{phase.lower()}",
                "detail": detail[:400] if detail else None,
                "payload": payload,
                "ts_ms": ts_ms,
            }
        )
    except Exception as log_exc:
        _logger.debug("input_routes: ledger log failed (non-blocking): %s", log_exc)


def _log_dispatch_task_exception(task: asyncio.Task) -> None:
    """Log fire-and-forget task exceptions."""

    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        _logger.error("input_routes: dispatch task failed silently: %s", exc, exc_info=exc)


class _NoopMetrics:
    """Minimal metrics shim for cortex orchestrator direct invocation."""

    def __getattr__(self, _name: str):
        def _noop(*_args: Any, **_kwargs: Any) -> None:
            return None

        return _noop


def _build_input_preview(payload: dict[str, Any] | None, *, source: str, limit: int = 120) -> str | None:
    payload_dict = dict(payload or {})
    preview_source = None
    if str(source or "").upper() == "GATEWAY":
        preview_source = payload_dict.get("content")
    elif str(source or "").upper() == "ASR":
        preview_source = payload_dict.get("text")
    preview = _build_reply_preview(str(preview_source or ""), limit=limit).strip()
    return preview or None


def _parse_sse_data_event(raw_event: str) -> dict[str, Any] | None:
    line = str(raw_event or "").strip()
    if not line.startswith("data:"):
        return None
    payload = line.removeprefix("data:").strip()
    if not payload:
        return None
    try:
        obj = json.loads(payload)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _build_gateway_reply_text(
    *,
    decision: str,
    narrative: str,
    reason_code: str | None,
) -> str:
    templates = _load_template_mapping("input_routes.fallback_texts")
    reply_text = str(narrative or "").strip()
    if reply_text:
        return reply_text
    if str(decision or "").upper() != "APPROVE":
        return str(templates.get("reject_default") or "")
    _ = reason_code
    return str(templates.get("approve_empty") or "")


def _build_reply_preview(text: str, *, limit: int = 120) -> str:
    normalized = str(text or "").strip().replace("\r", " ").replace("\n", " ")
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: max(0, limit - 3)]}..."


def _get_request_skill_exec_rows(ledger: Any, *, request_id: str) -> list[dict[str, Any]]:
    if ledger is None:
        return []
    rid = str(request_id or "").strip()
    if not rid:
        return []

    try:
        if hasattr(ledger, "trace_request"):
            trace = ledger.trace_request(rid)
            rows = trace.get("skill_exec_log") if isinstance(trace, dict) else None
            if isinstance(rows, list):
                return [dict(row or {}) for row in rows if isinstance(row, dict)]
    except Exception:
        pass

    try:
        fallback_rows = getattr(ledger, "skill_exec_log", None)
        if isinstance(fallback_rows, list):
            matched: list[dict[str, Any]] = []
            for row in fallback_rows:
                if not isinstance(row, dict):
                    continue
                if str(row.get("request_id") or "").strip() == rid:
                    matched.append(dict(row))
            return matched
    except Exception:
        pass
    return []


def _analyze_request_execution_quality(
    *,
    ledger: Any,
    request_id: str,
    decision: str,
    route_type: str,
    route_target: str,
) -> dict[str, Any]:
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

    skill_rows = _get_request_skill_exec_rows(ledger, request_id=request_id)
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


def _looks_like_progress_query_for_ack_skip(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or "")).strip().lower()
    if not compact:
        return False
    if _GATEWAY_ACK_PROGRESS_QUERY_RE.search(compact):
        return True
    return any(marker in compact for marker in _GATEWAY_ACK_PROGRESS_MARKERS)


def _looks_like_execution_intent_for_ack(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized:
        return False
    if _looks_like_progress_query_for_ack_skip(normalized):
        return False

    compact = normalized.replace(" ", "").lower()
    if normalized.startswith(("/", "!", "run ", "exec ")):
        return True
    return any(marker in compact for marker in _GATEWAY_ACK_ACTION_MARKERS)


def _resolve_bond_stage_for_ack(*, payload: dict[str, Any]) -> str:
    stage = str((dict(payload.get("bond_summary") or {})).get("stage") or "").strip().upper()
    if stage:
        return stage

    person_id = str(payload.get("person_id") or "").strip()
    if not person_id:
        return "STRANGER"

    try:
        from host_brain.tribe.social_brain_agent import get_social_brain_agent  # noqa: PLC0415

        return get_social_brain_agent().resolve_bond_stage(person_id=person_id, default="STRANGER")
    except Exception:
        return "STRANGER"


def _resolve_emotion_tag_for_ack(*, payload: dict[str, Any]) -> str:
    for key in ("emotion_tag", "emotion", "mood"):
        value = str(payload.get(key) or "").strip().upper()
        if value:
            return value
    return "NEUTRAL"


def _format_elapsed_compact(elapsed_ms: int) -> str:
    total_seconds = max(0, int(elapsed_ms // 1000))
    if total_seconds < 60:
        return f"{total_seconds}s"
    minutes, seconds = divmod(total_seconds, 60)
    if minutes < 60:
        return f"{minutes}m{seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def _extract_alignment_keywords(text: str) -> list[str]:
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
            # 中文连续串拆成双字片段，提升关键词覆盖判定灵敏度。
            for idx in range(0, len(token) - 1):
                tokens.append(token[idx : idx + 2])
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


def _compute_keyword_overlap(*, source_text: str, target_text: str) -> tuple[float, int, int]:
    keywords = _extract_alignment_keywords(source_text)
    if not keywords:
        return 1.0, 0, 0
    haystack = str(target_text or "").strip().lower()
    if not haystack:
        return 0.0, len(keywords), 0
    hit_count = sum(1 for keyword in keywords if keyword and keyword in haystack)
    ratio = hit_count / max(1, len(keywords))
    return round(max(0.0, min(1.0, ratio)), 4), len(keywords), hit_count


def _detect_gateway_execution_deviation(
    *,
    source: str,
    user_text: str,
    decision: str,
    narrative: str,
) -> dict[str, Any]:
    if not _parse_bool_env("HOST_GATEWAY_DEVIATION_GUARD_ENABLED", default=True):
        return {"triggered": False, "reason_code": "DEVIATION_GUARD_DISABLED"}
    if str(source or "").strip().upper() != "GATEWAY":
        return {"triggered": False, "reason_code": "DEVIATION_SOURCE_NOT_GATEWAY"}
    if str(decision or "").strip().upper() != "APPROVE":
        return {"triggered": False, "reason_code": "DEVIATION_DECISION_NOT_APPROVE"}
    if not _looks_like_execution_intent_for_ack(user_text):
        return {"triggered": False, "reason_code": "DEVIATION_NON_ACTION_INTENT"}
    source_norm = str(user_text or "").strip()
    target_norm = str(narrative or "").strip()
    if not source_norm or not target_norm:
        return {"triggered": False, "reason_code": "DEVIATION_EMPTY_TEXT"}

    overlap_ratio, keyword_count, hit_count = _compute_keyword_overlap(
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


def _build_realign_user_message(*, original_user_text: str, first_narrative: str) -> str:
    original = str(original_user_text or "").strip()
    first = str(first_narrative or "").strip()
    guidance = (
        "[执行纠偏]\n"
        "刚才结果可能偏离了用户真实意图。请仅围绕原始请求重新执行，并给出可执行/可验证结果。\n"
        f"原始请求：{original}\n"
        f"偏航输出：{first}\n"
        "要求：不要泛泛闲聊，不要复述偏航内容，直接回到用户要你做的事。"
    )
    return guidance


async def _maybe_send_gateway_realign_notice(
    *,
    request_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if not _gateway_auto_reply_enabled():
        return {"sent": False, "reason_code": "DEVIATION_NOTICE_AUTO_REPLY_DISABLED"}
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
        bond_stage=_resolve_bond_stage_for_ack(payload=payload),
        emotion_tag=_resolve_emotion_tag_for_ack(payload=payload),
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
        "preview": _build_reply_preview(content, limit=80),
        "latency_ms": int(getattr(result, "latency_ms", 0)),
    }


def _should_send_gateway_immediate_ack(
    *,
    source: str,
    text: str,
    payload: dict[str, Any],
) -> tuple[bool, str]:
    if not _gateway_auto_reply_enabled():
        return False, "ACK_AUTO_REPLY_DISABLED"
    if not _parse_bool_env("HOST_GATEWAY_IMMEDIATE_ACK_ENABLED", default=True):
        return False, "ACK_DISABLED"
    if str(source or "").strip().upper() != "GATEWAY":
        return False, "ACK_SOURCE_NOT_GATEWAY"

    text_value = str(text or payload.get("content") or "").strip()
    if not text_value:
        return False, "ACK_EMPTY_TEXT"
    if _looks_like_progress_query_for_ack_skip(text_value):
        return False, "ACK_PROGRESS_QUERY"
    if not _looks_like_execution_intent_for_ack(text_value):
        return False, "ACK_NON_ACTION_INTENT"
    return True, "ACK_ACTION_INTENT"


async def _deliver_gateway_immediate_ack(
    *,
    request_id: str,
    payload: dict[str, Any],
    text: str,
) -> dict[str, Any]:
    from host_brain.app.chat_agent import render_quick_gateway_ack_text  # noqa: PLC0415
    from host_brain.channels.edge_rpc_client import post_gateway_reply  # noqa: PLC0415

    platform = str(payload.get("platform") or "").strip().lower()
    conversation_id = str(payload.get("conversation_id") or "").strip()
    if not platform or not conversation_id:
        return {
            "success": False,
            "status": "SKIPPED",
            "reason_code": "ACK_TARGET_MISSING",
            "latency_ms": 0,
            "ack_preview": "",
        }

    ack_text = render_quick_gateway_ack_text(
        user_message=text,
        bond_stage=_resolve_bond_stage_for_ack(payload=payload),
        emotion_tag=_resolve_emotion_tag_for_ack(payload=payload),
        sender_name=str(payload.get("sender_name") or "").strip() or None,
    )
    normalized_ack = str(ack_text or "").strip()
    if not normalized_ack:
        return {
            "success": False,
            "status": "SKIPPED",
            "reason_code": "ACK_TEXT_EMPTY",
            "latency_ms": 0,
            "ack_preview": "",
        }

    wait_result = _parse_bool_env("HOST_GATEWAY_IMMEDIATE_ACK_WAIT_RESULT", default=False)
    timeout_ms = max(1000, _parse_int_env("HOST_GATEWAY_IMMEDIATE_ACK_TIMEOUT_MS", default=2500))
    result = await post_gateway_reply(
        platform=platform,
        conversation_id=conversation_id,
        content=normalized_ack,
        msg_type="text",
        request_id=request_id,
        wait_result=wait_result,
        timeout_ms=timeout_ms,
    )
    return {
        "success": bool(getattr(result, "success", False)),
        "status": str(getattr(result, "status", "UNKNOWN")),
        "reason_code": str(getattr(result, "reason_code", "") or ""),
        "latency_ms": int(getattr(result, "latency_ms", 0)),
        "ack_preview": _build_reply_preview(normalized_ack, limit=60),
    }


def _log_gateway_ack_task_exception(task: asyncio.Task) -> None:
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        _logger.warning("input_routes: immediate gateway ack task failed silently: %s", exc, exc_info=exc)


def _build_gateway_progress_reply_text(
    *,
    request_id: str,
    session_id: str,
    payload: dict[str, Any],
    exclude_request_id: str | None = None,
) -> tuple[str, str, dict[str, Any] | None]:
    from host_brain.app.chat_agent import render_quick_gateway_progress_text  # noqa: PLC0415
    from host_brain.app.dependencies import get_runtime_task_registry  # noqa: PLC0415

    person_id = str(payload.get("person_id") or "").strip()
    summary = None
    if person_id:
        summary = get_runtime_task_registry().summarize_for_chat(
            person_id=person_id,
            session_id=session_id,
            exclude_request_id=exclude_request_id,
        )
    if not isinstance(summary, dict):
        return (
            render_quick_gateway_progress_text(
                status="NONE",
                bond_stage=_resolve_bond_stage_for_ack(payload=payload),
                emotion_tag=_resolve_emotion_tag_for_ack(payload=payload),
                sender_name=str(payload.get("sender_name") or "").strip() or None,
            ),
            "PROGRESS_NO_SUMMARY",
            None,
        )

    status = str(summary.get("status") or "UNKNOWN").strip().upper() or "UNKNOWN"
    target_id = str(summary.get("target_id") or "").strip()
    progress_message = str(summary.get("progress_message") or summary.get("detail") or "").strip()
    result_text = str(summary.get("result_text") or "").strip()
    elapsed_ms = int(summary.get("elapsed_ms") or 0)
    if not progress_message and status in {"RUNNING", "RECOVERING"}:
        progress_message = f"已持续 {_format_elapsed_compact(elapsed_ms)}。"
    reply_text = render_quick_gateway_progress_text(
        status=status,
        target_id=target_id,
        progress_message=progress_message,
        result_text=result_text,
        elapsed_ms=elapsed_ms,
        bond_stage=_resolve_bond_stage_for_ack(payload=payload),
        emotion_tag=_resolve_emotion_tag_for_ack(payload=payload),
        sender_name=str(payload.get("sender_name") or "").strip() or None,
    )
    return reply_text, "PROGRESS_SUMMARY_FOUND", dict(summary)


async def _try_handle_gateway_progress_fastpath(
    *,
    request_id: str,
    session_id: str,
    source: str,
    text: str,
    payload: dict[str, Any],
    ledger: Any,
    ts_ms: int,
) -> bool:
    if not _gateway_auto_reply_enabled():
        return False
    if str(source or "").strip().upper() != "GATEWAY":
        return False
    if not _parse_bool_env("HOST_GATEWAY_PROGRESS_FASTPATH_ENABLED", default=True):
        return False
    if not _looks_like_progress_query_for_ack_skip(text):
        return False

    platform = str(payload.get("platform") or "").strip().lower()
    conversation_id = str(payload.get("conversation_id") or "").strip()
    if not platform or not conversation_id:
        _log_input_event(
            ledger=ledger,
            request_id=request_id,
            session_id=session_id,
            source=source,
            input_payload={**dict(payload or {}), "progress_fastpath_reason": "PROGRESS_TARGET_MISSING"},
            phase="PROGRESS_FASTPATH_SKIPPED",
            status="IGNORED",
            reason_code="PROGRESS_TARGET_MISSING",
            detail="gateway progress fastpath skipped: missing platform/conversation_id",
            ts_ms=ts_ms,
        )
        return False

    try:
        from host_brain.channels.edge_rpc_client import post_gateway_reply  # noqa: PLC0415
    except Exception:
        return False

    try:
        reply_text, reason, _summary = _build_gateway_progress_reply_text(
            request_id=request_id,
            session_id=session_id,
            payload=payload,
            exclude_request_id=request_id,
        )
    except Exception as exc:
        _log_input_event(
            ledger=ledger,
            request_id=request_id,
            session_id=session_id,
            source=source,
            input_payload={**dict(payload or {}), "progress_fastpath_reason": "PROGRESS_BUILD_FAILED"},
            phase="PROGRESS_FASTPATH_FAILED",
            status="FAILED",
            reason_code="PROGRESS_BUILD_FAILED",
            detail=f"{type(exc).__name__}: {str(exc)[:200]}",
            ts_ms=int(time.time() * 1000),
        )
        return False

    wait_result = _parse_bool_env("HOST_GATEWAY_PROGRESS_FASTPATH_WAIT_RESULT", default=True)
    timeout_ms = max(1000, _parse_int_env("HOST_GATEWAY_PROGRESS_FASTPATH_TIMEOUT_MS", default=6000))
    started_ms = int(time.time() * 1000)
    result = await post_gateway_reply(
        platform=platform,
        conversation_id=conversation_id,
        content=reply_text,
        msg_type="text",
        request_id=request_id,
        wait_result=wait_result,
        timeout_ms=timeout_ms,
    )
    success = bool(getattr(result, "success", False))
    finished_ms = int(time.time() * 1000)
    latency_ms = max(0, finished_ms - started_ms)
    payload_with_result = dict(payload or {})
    payload_with_result["progress_fastpath_status"] = str(getattr(result, "status", "UNKNOWN"))
    payload_with_result["progress_fastpath_latency_ms"] = int(getattr(result, "latency_ms", 0))
    payload_with_result["progress_fastpath_preview"] = _build_reply_preview(reply_text, limit=100)
    payload_with_result["progress_fastpath_reason"] = reason

    _log_input_event(
        ledger=ledger,
        request_id=request_id,
        session_id=session_id,
        source=source,
        input_payload=payload_with_result,
        phase="PROGRESS_FASTPATH",
        status=("OK" if success else "DEGRADED"),
        reason_code=(None if success else str(getattr(result, "reason_code", "") or "PROGRESS_REPLY_FAILED")),
        detail=f"gateway progress fastpath status={getattr(result, 'status', 'UNKNOWN')} success={success}",
        latency_ms=latency_ms,
        ts_ms=finished_ms,
    )
    return success


def _should_start_gateway_progress_push(
    *,
    source: str,
    text: str,
    payload: dict[str, Any],
) -> tuple[bool, str]:
    if not _gateway_auto_reply_enabled():
        return False, "PROGRESS_PUSH_AUTO_REPLY_DISABLED"
    if not _parse_bool_env("HOST_GATEWAY_PROGRESS_PUSH_ENABLED", default=True):
        return False, "PROGRESS_PUSH_DISABLED"
    if str(source or "").strip().upper() != "GATEWAY":
        return False, "PROGRESS_PUSH_SOURCE_NOT_GATEWAY"
    if _looks_like_progress_query_for_ack_skip(text):
        return False, "PROGRESS_PUSH_PROGRESS_QUERY"
    if not _looks_like_execution_intent_for_ack(text):
        return False, "PROGRESS_PUSH_NON_ACTION_INTENT"
    platform = str(payload.get("platform") or "").strip().lower()
    conversation_id = str(payload.get("conversation_id") or "").strip()
    if not platform or not conversation_id:
        return False, "PROGRESS_PUSH_TARGET_MISSING"
    return True, "PROGRESS_PUSH_ENABLED"


def _log_progress_push_task_exception(task: asyncio.Task) -> None:
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        _logger.warning("input_routes: progress push task failed silently: %s", exc, exc_info=exc)


async def _run_gateway_proactive_progress_push(
    *,
    request_id: str,
    session_id: str,
    source: str,
    text: str,
    payload: dict[str, Any],
    ledger: Any,
    stop_event: asyncio.Event,
) -> None:
    from host_brain.channels.edge_rpc_client import post_gateway_reply  # noqa: PLC0415

    platform = str(payload.get("platform") or "").strip().lower()
    conversation_id = str(payload.get("conversation_id") or "").strip()
    if not platform or not conversation_id:
        return

    warmup_ms = max(0, _parse_int_env("HOST_GATEWAY_PROGRESS_PUSH_WARMUP_MS", default=15000))
    interval_ms = max(2000, _parse_int_env("HOST_GATEWAY_PROGRESS_PUSH_INTERVAL_MS", default=15000))
    max_updates = max(1, min(8, _parse_int_env("HOST_GATEWAY_PROGRESS_PUSH_MAX_UPDATES", default=2)))
    wait_result = _parse_bool_env("HOST_GATEWAY_PROGRESS_PUSH_WAIT_RESULT", default=False)
    timeout_ms = max(1000, _parse_int_env("HOST_GATEWAY_PROGRESS_PUSH_TIMEOUT_MS", default=4000))

    if warmup_ms > 0:
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=float(warmup_ms) / 1000.0)
            return
        except asyncio.TimeoutError:
            pass

    sent_count = 0
    last_signature = ""
    while not stop_event.is_set() and sent_count < max_updates:
        reply_text, reason, summary = _build_gateway_progress_reply_text(
            request_id=request_id,
            session_id=session_id,
            payload=payload,
            exclude_request_id=None,
        )
        summary_dict = dict(summary or {})
        status = str(summary_dict.get("status") or "NONE").strip().upper()
        target_id = str(summary_dict.get("target_id") or "").strip()
        progress_message = str(summary_dict.get("progress_message") or summary_dict.get("detail") or "").strip()
        signature = f"{status}|{target_id}|{progress_message}"

        if reason == "PROGRESS_SUMMARY_FOUND" and status in {"RUNNING", "RECOVERING"} and signature != last_signature:
            started_ms = int(time.time() * 1000)
            result = await post_gateway_reply(
                platform=platform,
                conversation_id=conversation_id,
                content=reply_text,
                msg_type="text",
                request_id=request_id,
                wait_result=wait_result,
                timeout_ms=timeout_ms,
            )
            success = bool(getattr(result, "success", False))
            finished_ms = int(time.time() * 1000)
            latency_ms = max(0, finished_ms - started_ms)
            push_payload = dict(payload or {})
            push_payload["progress_push_status"] = str(getattr(result, "status", "UNKNOWN"))
            push_payload["progress_push_reason"] = reason
            push_payload["progress_push_preview"] = _build_reply_preview(reply_text, limit=100)
            push_payload["progress_push_updates"] = sent_count + 1
            _log_input_event(
                ledger=ledger,
                request_id=request_id,
                session_id=session_id,
                source=source,
                input_payload=push_payload,
                phase="PROGRESS_PUSHED",
                status=("OK" if success else "DEGRADED"),
                reason_code=(None if success else str(getattr(result, "reason_code", "") or "PROGRESS_PUSH_REPLY_FAILED")),
                detail=f"gateway progress push status={getattr(result, 'status', 'UNKNOWN')} success={success}",
                latency_ms=latency_ms,
                ts_ms=finished_ms,
            )
            sent_count += 1
            last_signature = signature
        elif reason == "PROGRESS_SUMMARY_FOUND" and status in {"COMPLETED", "FAILED"}:
            return

        if sent_count >= max_updates:
            return

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=float(interval_ms) / 1000.0)
            return
        except asyncio.TimeoutError:
            continue


async def _stop_progress_push_task(
    *,
    stop_event: asyncio.Event | None,
    task: asyncio.Task | None,
) -> None:
    if stop_event is not None:
        stop_event.set()
    if task is None:
        return
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=0.4)
    except asyncio.TimeoutError:
        task.cancel()
    except Exception:
        return


def _maybe_schedule_gateway_immediate_ack(
    *,
    request_id: str,
    session_id: str,
    source: str,
    text: str,
    payload: dict[str, Any],
    ledger: Any,
) -> None:
    should_send, reason = _should_send_gateway_immediate_ack(source=source, text=text, payload=payload)
    now_ms = int(time.time() * 1000)
    if not should_send:
        _log_input_event(
            ledger=ledger,
            request_id=request_id,
            session_id=session_id,
            source=source,
            input_payload={**dict(payload or {}), "immediate_ack_reason": reason},
            phase="ACK_SKIPPED",
            status="IGNORED",
            reason_code=reason,
            detail=f"gateway immediate ack skipped: {reason}",
            ts_ms=now_ms,
        )
        return

    _log_input_event(
        ledger=ledger,
        request_id=request_id,
        session_id=session_id,
        source=source,
        input_payload=payload,
        phase="ACK_PENDING",
        status="ACCEPTED",
        detail="gateway immediate ack scheduled",
        ts_ms=now_ms,
    )

    async def _runner() -> None:
        start_ms = int(time.time() * 1000)
        try:
            ack_result = await _deliver_gateway_immediate_ack(
                request_id=request_id,
                payload=payload,
                text=text,
            )
        except Exception as exc:
            finish_ms = int(time.time() * 1000)
            latency_ms = max(0, finish_ms - start_ms)
            _log_input_event(
                ledger=ledger,
                request_id=request_id,
                session_id=session_id,
                source=source,
                input_payload=payload,
                phase="ACK_FAILED",
                status="FAILED",
                reason_code="ACK_DELIVERY_EXCEPTION",
                detail=f"{type(exc).__name__}: {str(exc)[:200]}",
                latency_ms=latency_ms,
                ts_ms=finish_ms,
            )
            return

        finish_ms = int(time.time() * 1000)
        latency_ms = max(0, finish_ms - start_ms)
        ack_payload = dict(payload or {})
        ack_payload["immediate_ack_status"] = ack_result.get("status")
        ack_payload["immediate_ack_latency_ms"] = ack_result.get("latency_ms")
        ack_payload["immediate_ack_preview"] = ack_result.get("ack_preview")
        ack_payload["immediate_ack_reason"] = ack_result.get("reason_code")
        ack_success = bool(ack_result.get("success"))
        _log_input_event(
            ledger=ledger,
            request_id=request_id,
            session_id=session_id,
            source=source,
            input_payload=ack_payload,
            phase=("ACKED" if ack_success else "ACK_FAILED"),
            status=("OK" if ack_success else "FAILED"),
            reason_code=(str(ack_result.get("reason_code") or "") or None),
            detail=f"gateway immediate ack status={ack_result.get('status')} success={ack_success}",
            latency_ms=latency_ms,
            ts_ms=finish_ms,
        )

    asyncio.create_task(_runner()).add_done_callback(_log_gateway_ack_task_exception)


async def _run_cortex_orchestrator_pipeline(
    *,
    request_id: str,
    session_id: str,
    source: str,
    text: str,
    payload: dict[str, Any],
    ts_ms: int,
) -> dict[str, Any]:
    from host_brain.app.emotion_context import (  # noqa: PLC0415
        build_chat_emotion_snapshot,
        resolve_chat_emotion_context,
    )
    from host_brain.bond.user_identity import resolve_person_id  # noqa: PLC0415
    from host_brain.app.schemas import ChatRequest  # noqa: PLC0415
    from host_brain.app.cortex_orchestrator import cortex_orchestrator_sse_generator  # noqa: PLC0415

    reply_channel = str(payload.get("platform") or "").strip() or None
    reply_target = str(payload.get("conversation_id") or "").strip() or None
    person_id = resolve_person_id(
        str(payload.get("person_id") or "").strip() or None,
        session_id=session_id,
        platform=reply_channel,
        from_id=str(payload.get("from_id") or "").strip() or None,
        conversation_id=reply_target,
    )
    request_body = ChatRequest(
        message=text,
        person_id=person_id,
        session_id=session_id,
        input_source=source,
        reply_channel=reply_channel,
        reply_target=reply_target,
        suppress_edge_tts=(True if source == "GATEWAY" else None),
        suppress_edge_vts=(True if source == "GATEWAY" else None),
        # Phase XV P4：Gateway 通道文字永远中文，注入 render_target_lang
        # ASR/Web 通道保持 None，由 infer_render_target_lang() 推断（向后兼容）
        render_target_lang=("zh" if source == "GATEWAY" else None),
    )
    emotion_context = resolve_chat_emotion_context(request_body=request_body)
    emotion_snapshot = build_chat_emotion_snapshot(
        emotion_context=emotion_context,
        source=f"input_routes.{str(source or '').lower()}",
        ts_ms=ts_ms,
    )
    intent_draft = await _build_gateway_intent_draft(
        request_id=request_id,
        session_id=session_id,
        source=source,
        text=text,
    )
    governor_result: dict[str, Any] = {"decision": "APPROVE", "source": "input_routes"}

    commit_event: dict[str, Any] | None = None
    async for event in cortex_orchestrator_sse_generator(
        request_body=request_body,
        request_id=request_id,
        session_id=session_id,
        ts_ms=ts_ms,
        intent_draft=intent_draft,
        emotion_context=emotion_context,
        emotion_snapshot=emotion_snapshot,
        governor_result=governor_result,
        intent_id=request_id,
        metrics=_NoopMetrics(),
    ):
        parsed = _parse_sse_data_event(event)
        if parsed and str(parsed.get("phase") or "").upper() == "COMMIT":
            commit_event = parsed

    if commit_event is None:
        raise RuntimeError("cortex_orchestrator_commit_missing")

    return {
        "decision": str(commit_event.get("decision") or "").strip().upper() or "REJECT",
        "reason_code": str(commit_event.get("reason_code") or "").strip() or None,
        "narrative": str(commit_event.get("narrative") or "").strip(),
        "commit_event": commit_event,
        "intent_draft": dict(intent_draft or {}),
        "route_decision": {
            "route": str(intent_draft.get("route") or "chat").strip().lower() or "chat",
            "target_id": str(intent_draft.get("target_id") or "").strip(),
            "task": str(intent_draft.get("task") or "").strip(),
            "reason_code": str(intent_draft.get("route_reason") or "").strip() or None,
            "source": str(intent_draft.get("route_source") or "").strip() or None,
            "confidence": _normalize_route_confidence(intent_draft.get("route_confidence")),
            "override_reason": str(intent_draft.get("route_override_reason") or "").strip() or None,
            "llm_route_used": bool(intent_draft.get("llm_route_used")),
        },
    }


async def _run_cortex_pipeline(
    *,
    request_id: str,
    session_id: str,
    source: str,
    text: str,
    payload: dict[str, Any],
    ts_ms: int,
) -> dict[str, Any]:
    return await _run_cortex_orchestrator_pipeline(
        request_id=request_id,
        session_id=session_id,
        source=source,
        text=text,
        payload=payload,
        ts_ms=ts_ms,
    )


async def _maybe_realign_gateway_pipeline(
    *,
    request_id: str,
    session_id: str,
    source: str,
    user_text: str,
    payload: dict[str, Any],
    ts_ms: int,
    pipeline_result: dict[str, Any],
    ledger: Any,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    decision = str(pipeline_result.get("decision") or "REJECT").strip().upper()
    narrative = str(pipeline_result.get("narrative") or "").strip()
    detection = _detect_gateway_execution_deviation(
        source=source,
        user_text=user_text,
        decision=decision,
        narrative=narrative,
    )
    if not bool(detection.get("triggered")):
        return pipeline_result, detection

    now_ms = int(time.time() * 1000)
    detection_payload = dict(payload or {})
    detection_payload["deviation_reason"] = detection.get("reason_code")
    detection_payload["deviation_overlap_ratio"] = detection.get("overlap_ratio")
    detection_payload["deviation_keyword_count"] = detection.get("keyword_count")
    detection_payload["deviation_hit_count"] = detection.get("hit_count")
    _log_input_event(
        ledger=ledger,
        request_id=request_id,
        session_id=session_id,
        source=source,
        input_payload=detection_payload,
        phase="DEVIATION_DETECTED",
        status="DEGRADED",
        reason_code=str(detection.get("reason_code") or "DEVIATION_DETECTED"),
        detail=(
            f"overlap={detection.get('overlap_ratio')} "
            f"keywords={detection.get('keyword_count')} hits={detection.get('hit_count')}"
        ),
        ts_ms=now_ms,
    )

    notice_result = await _maybe_send_gateway_realign_notice(
        request_id=request_id,
        payload=payload,
    )
    detection["notice"] = notice_result

    if not _parse_bool_env("HOST_GATEWAY_DEVIATION_RETRY_ENABLED", default=True):
        detection["retry_applied"] = False
        detection["retry_reason"] = "DEVIATION_RETRY_DISABLED"
        return pipeline_result, detection

    try:
        retry_message = _build_realign_user_message(
            original_user_text=user_text,
            first_narrative=narrative,
        )
        retry_result = await _run_cortex_orchestrator_pipeline(
            request_id=f"{request_id}::realign",
            session_id=session_id,
            source=source,
            text=retry_message,
            payload=payload,
            ts_ms=int(time.time() * 1000),
        )
    except Exception as exc:
        detection["retry_applied"] = False
        detection["retry_reason"] = "DEVIATION_RETRY_FAILED"
        detection["retry_error"] = f"{type(exc).__name__}:{str(exc)[:160]}"
        return pipeline_result, detection

    retry_decision = str(retry_result.get("decision") or "REJECT").strip().upper()
    retry_narrative = str(retry_result.get("narrative") or "").strip()
    retry_overlap, retry_kw_count, retry_hit_count = _compute_keyword_overlap(
        source_text=user_text,
        target_text=retry_narrative,
    )
    min_overlap = float(detection.get("min_overlap") or _parse_float_env("HOST_GATEWAY_DEVIATION_MIN_OVERLAP", default=0.18))
    retry_ok = retry_decision == "APPROVE" and bool(retry_narrative) and retry_overlap >= min_overlap
    detection["retry_applied"] = retry_ok
    detection["retry_overlap_ratio"] = retry_overlap
    detection["retry_keyword_count"] = retry_kw_count
    detection["retry_hit_count"] = retry_hit_count

    if retry_ok:
        _log_input_event(
            ledger=ledger,
            request_id=request_id,
            session_id=session_id,
            source=source,
            input_payload={**dict(payload or {}), "deviation_retry_overlap_ratio": retry_overlap},
            phase="DEVIATION_RECOVERED",
            status="OK",
            reason_code="DEVIATION_RECOVERED",
            detail=f"retry decision={retry_decision} overlap={retry_overlap}",
            ts_ms=int(time.time() * 1000),
        )
        return retry_result, detection
    return pipeline_result, detection


async def _deliver_gateway_reply(
    *,
    request_id: str,
    payload: dict[str, Any],
    decision: str,
    reason_code: str | None,
    narrative: str,
    # Phase XV P4：ReplyFormDecider 所需的上下文
    emotion_tag: str = "NEUTRAL",
    bond_stage: str = "STRANGER",
    user_msg_type: str = "text",
    # Phase XV P6：双轨语言，Gateway 文字永远中文（narrative 已是中文）
    # tts_text_ja 为日语 TTS 文本（双语输出时使用，当前为占位）
    tts_text_ja: str | None = None,
) -> dict[str, Any]:
    platform = str(payload.get("platform") or "").strip().lower()
    conversation_id = str(payload.get("conversation_id") or "").strip()

    reply_text = _build_gateway_reply_text(
        decision=decision,
        narrative=narrative,
        reason_code=reason_code,
    )
    preview = _build_reply_preview(reply_text, limit=120)
    if not _gateway_auto_reply_enabled():
        return {
            "success": True,
            "status": "SKIPPED",
            "reason_code": "GATEWAY_REPLY_DISABLED",
            "detail": "gateway reply skipped: HOST_INPUT_GATEWAY_AUTO_REPLY=false",
            "latency_ms": 0,
            "reply_text_len": len(reply_text),
            "reply_preview": preview,
            "reply_form": "text",
            "reply_form_reason": "auto_reply_disabled",
            "voice_sent": False,
        }
    if not platform or not conversation_id:
        return {
            "success": False,
            "status": "FAILED",
            "reason_code": "GATEWAY_REPLY_MISSING_ROUTE",
            "detail": "gateway reply skipped: missing platform or conversation_id",
            "latency_ms": 0,
            "reply_text_len": len(reply_text),
            "reply_preview": preview,
            "reply_form": "text",
            "reply_form_reason": "missing_route",
            "voice_sent": False,
        }

    from host_brain.channels.edge_rpc_client import post_gateway_reply  # noqa: PLC0415
    from host_brain.app.reply_form_decider import (  # noqa: PLC0415
        get_reply_form_decider, MsgType,
    )

    # ── Phase XV P4：ReplyFormDecider ──────────────────────────────────
    # 将用户消息类型字符串转换为 MsgType
    try:
        user_mtype = MsgType(str(user_msg_type or "text").strip().lower())
    except ValueError:
        user_mtype = MsgType.TEXT

    decider = get_reply_form_decider()
    form_decision = decider.decide(
        user_msg_type=user_mtype,
        narrative_zh=reply_text,
        emotion_tag=emotion_tag,
        bond_stage=bond_stage,
    )

    # ── 主回复（文字/语音）─────────────────────────────────────────────
    primary_content = reply_text
    primary_msg_type = form_decision.primary.value

    result = await post_gateway_reply(
        platform=platform,
        conversation_id=conversation_id,
        content=primary_content,
        msg_type=primary_msg_type,
        request_id=request_id,
        wait_result=True,
        timeout_ms=12000,
    )
    if not bool(getattr(result, "success", False)):
        status = str(getattr(result, "status", "FAILED"))
        rc = str(getattr(result, "reason_code", "") or "") or "GATEWAY_REPLY_FAILED"
        detail = str(getattr(result, "detail", "") or "")
        return {
            "success": False,
            "status": status,
            "reason_code": rc,
            "detail": detail,
            "latency_ms": int(getattr(result, "latency_ms", 0)),
            "reply_text_len": len(reply_text),
            "reply_preview": preview,
            "reply_form": form_decision.primary.value,
            "reply_form_reason": form_decision.reason,
            "voice_sent": False,
        }

    # ── 双发模式：先文字字幕，再日语语音（不阻塞主回复）────────────
    voice_sent = False
    if form_decision.is_dual:
        # P3/P6 完整实现：用 tts_text_ja 做 TTS 合成后发语音
        # P4 当前：发送一条文字作为语音消息的占位（保留接口，等 P6 双语输出完成后启用真正的 TTS）
        _voice_text = tts_text_ja or reply_text  # P6 完成后改为只用 tts_text_ja
        if _voice_text and form_decision.secondary == MsgType.VOICE:
            try:
                # 异步发送，不等待结果，不阻塞主回复
                import asyncio as _asyncio  # noqa: PLC0415
                _asyncio.create_task(_send_gateway_voice_reply(
                    platform=platform,
                    conversation_id=conversation_id,
                    tts_text=_voice_text,
                    request_id=request_id,
                    emotion_tag=emotion_tag,
                ))
                voice_sent = True
            except Exception:
                pass  # 语音发送失败不影响主回复

    return {
        "success": True,
        "status": str(getattr(result, "status", "OK")),
        "reason_code": None,
        "detail": str(getattr(result, "detail", "") or ""),
        "latency_ms": int(getattr(result, "latency_ms", 0)),
        "reply_text_len": len(reply_text),
        "reply_preview": preview,
        "reply_form": form_decision.primary.value,
        "reply_form_reason": form_decision.reason,
        "voice_sent": voice_sent,
    }


async def _send_gateway_voice_reply(
    *,
    platform: str,
    conversation_id: str,
    tts_text: str,
    request_id: str,
    emotion_tag: str = "NEUTRAL",
) -> None:
    """向 Gateway 发送语音消息（异步，fire-and-forget）。

    Phase XV P4 当前实现：
      - 调用 Edge TTS 合成（post_speak），等待合成完成
      - 当 Edge 端支持 TTS→音频文件 后，改为发送语音文件
      - 当前 post_gateway_reply msg_type="voice" 需要 content 为音频 base64 或文件路径
      - 临时方案：发送 msg_type="text" 的语音字幕（标记 🎵 前缀区分）

    Phase XV P6/P7 完整实现：
      - Edge TTS 合成 → 返回音频文件路径/base64
      - post_gateway_reply(msg_type="voice", content=audio_data)
    """
    import logging as _logging  # noqa: PLC0415
    _vlogger = _logging.getLogger("Natsume.gateway_voice")
    try:
        from host_brain.channels.edge_rpc_client import post_speak  # noqa: PLC0415
        # 触发 TTS 合成（本地扬声器）——Gateway 用户在外面听不到，
        # 但这样可以验证 TTS 合成链路正常工作
        # P6/P7 完成后：获取合成音频 → 发送 voice 消息
        _tts_text_normalized = str(tts_text or "").strip()
        if not _tts_text_normalized:
            return

        await post_speak(
            text=_tts_text_normalized,
            request_id=request_id,
            lang_hint="ja",  # Phase XV P6: 语音通道永远日语
            emotion_tag=emotion_tag,
        )
        _vlogger.debug(
            "gateway_voice TTS triggered (P4 placeholder): platform=%s len=%d",
            platform, len(_tts_text_normalized),
        )
    except Exception as exc:
        _vlogger.debug("gateway_voice send failed (non-critical): %s", exc)


async def _dispatch_to_graph(
    *,
    request_id: str,
    session_id: str,
    source: str,
    text: str,
    payload: dict[str, Any],
    ts_ms: int,
) -> None:
    ledger = _get_ledger_safe()
    source_upper = str(source or "").upper().strip()
    if source_upper == "ASR":
        allow_asr, reject_reason = _evaluate_asr_noise_gate(text=text, payload=payload)
        if not allow_asr:
            ignored_ts = int(time.time() * 1000)
            _log_input_event(
                ledger=ledger,
                request_id=request_id,
                session_id=session_id,
                source=source,
                input_payload=payload,
                phase="IGNORED",
                status="IGNORED",
                reason_code=reject_reason,
                detail=f"ASR ignored by noise gate: {reject_reason}",
                ts_ms=ignored_ts,
            )
            _logger.info(
                "input_routes: dispatch ignored request_id=%s source=%s reason=%s",
                request_id,
                source_upper,
                reject_reason,
            )
            return

    run_in_thread = _parse_bool_env("HOST_INPUT_DISPATCH_IN_THREAD", default=True)
    dispatch_mode = "fire_and_forget.threaded" if run_in_thread else "fire_and_forget.inline"

    _log_input_event(
        ledger=ledger,
        request_id=request_id,
        session_id=session_id,
        source=source,
        input_payload=payload,
        phase="ACCEPTED",
        status="ACCEPTED",
        detail=f"source={source} text_len={len(text)}",
        ts_ms=ts_ms,
    )
    if source_upper == "GATEWAY":
        _maybe_schedule_gateway_immediate_ack(
            request_id=request_id,
            session_id=session_id,
            source=source_upper,
            text=text,
            payload=payload,
            ledger=ledger,
        )

    if run_in_thread:
        asyncio.create_task(
            _dispatch_to_graph_worker(
                request_id=request_id,
                session_id=session_id,
                source=source,
                text=text,
                payload=payload,
                ts_ms=ts_ms,
                ledger=ledger,
                dispatch_mode=dispatch_mode,
                run_in_thread=run_in_thread,
            )
        ).add_done_callback(_log_dispatch_task_exception)
        return

    await _dispatch_to_graph_worker(
        request_id=request_id,
        session_id=session_id,
        source=source,
        text=text,
        payload=payload,
        ts_ms=ts_ms,
        ledger=ledger,
        dispatch_mode=dispatch_mode,
        run_in_thread=run_in_thread,
    )


async def _dispatch_to_graph_worker(
    *,
    request_id: str,
    session_id: str,
    source: str,
    text: str,
    payload: dict[str, Any],
    ts_ms: int,
    ledger: Any,
    dispatch_mode: str,
    run_in_thread: bool,
) -> None:
    """Run cortex orchestrator pipeline and optionally deliver Gateway reply."""

    dispatch_ts = int(time.time() * 1000)
    source_upper = str(source or "").strip().upper()
    progress_push_stop_event: asyncio.Event | None = None
    progress_push_task: asyncio.Task | None = None
    _log_input_event(
        ledger=ledger,
        request_id=request_id,
        session_id=session_id,
        source=source,
        input_payload=payload,
        phase="DISPATCHED",
        status="DISPATCHED",
        detail=f"dispatch_mode={dispatch_mode} source={source}",
        ts_ms=dispatch_ts,
    )
    if source_upper == "GATEWAY":
        should_push, push_reason = _should_start_gateway_progress_push(
            source=source_upper,
            text=text,
            payload=payload,
        )
        if should_push:
            _log_input_event(
                ledger=ledger,
                request_id=request_id,
                session_id=session_id,
                source=source,
                input_payload=payload,
                phase="PROGRESS_PUSH_PENDING",
                status="ACCEPTED",
                detail="gateway proactive progress push scheduled",
                ts_ms=dispatch_ts,
            )
            progress_push_stop_event = asyncio.Event()
            progress_push_task = asyncio.create_task(
                _run_gateway_proactive_progress_push(
                    request_id=request_id,
                    session_id=session_id,
                    source=source,
                    text=text,
                    payload=payload,
                    ledger=ledger,
                    stop_event=progress_push_stop_event,
                )
            )
            progress_push_task.add_done_callback(_log_progress_push_task_exception)
        else:
            _log_input_event(
                ledger=ledger,
                request_id=request_id,
                session_id=session_id,
                source=source,
                input_payload={**dict(payload or {}), "progress_push_reason": push_reason},
                phase="PROGRESS_PUSH_SKIPPED",
                status="IGNORED",
                reason_code=push_reason,
                detail=f"gateway proactive progress push skipped: {push_reason}",
                ts_ms=dispatch_ts,
            )
    try:
        pipeline_result = await _run_cortex_orchestrator_pipeline(
            request_id=request_id,
            session_id=session_id,
            source=source,
            text=text,
            payload=payload,
            ts_ms=ts_ms,
        )
        deviation_meta: dict[str, Any] | None = None
        if source_upper == "GATEWAY":
            pipeline_result, deviation_meta = await _maybe_realign_gateway_pipeline(
                request_id=request_id,
                session_id=session_id,
                source=source,
                user_text=text,
                payload=payload,
                ts_ms=ts_ms,
                pipeline_result=pipeline_result,
                ledger=ledger,
            )
        decision = str(pipeline_result.get("decision") or "REJECT").strip().upper()
        reason_code = str(pipeline_result.get("reason_code") or "").strip() or None
        narrative = str(pipeline_result.get("narrative") or "").strip()
        route_decision = dict(pipeline_result.get("route_decision") or {})
        route_type = str(route_decision.get("route") or "chat").strip().lower() or "chat"
        route_target = str(route_decision.get("target_id") or "").strip()
        route_task = str(route_decision.get("task") or "").strip()
        route_source = str(route_decision.get("source") or "").strip() or None
        route_reason = str(route_decision.get("reason_code") or "").strip() or None
        route_override_reason = str(route_decision.get("override_reason") or "").strip() or None
        route_confidence = _normalize_route_confidence(route_decision.get("confidence"))
        route_llm_used = bool(route_decision.get("llm_route_used"))

        _log_input_event(
            ledger=ledger,
            request_id=request_id,
            session_id=session_id,
            source=source,
            input_payload={
                **dict(payload or {}),
                "route_type": route_type,
                "route_target": route_target,
                "route_task": route_task,
                "route_source": route_source,
                "route_reason": route_reason,
                "route_confidence": route_confidence,
                "route_override_reason": route_override_reason,
                "llm_route_used": route_llm_used,
            },
            phase="ROUTE_DECIDED",
            status="OK",
            reason_code=route_reason,
            detail=(
                f"route={route_type} target={route_target or '-'} "
                f"source={route_source or '-'} llm_used={str(route_llm_used).lower()}"
            ),
            ts_ms=int(time.time() * 1000),
        )

        await _stop_progress_push_task(
            stop_event=progress_push_stop_event,
            task=progress_push_task,
        )
        progress_push_stop_event = None
        progress_push_task = None

        gateway_delivery: dict[str, Any] | None = None
        if source == "GATEWAY":
            # Phase XV P4：从 pipeline_result 提取情感和 Bond 上下文
            commit_event = pipeline_result.get("commit_event") or {}
            audit_refs = commit_event.get("audit_refs") or {}
            _emotion_tag = str(audit_refs.get("emotion_tag") or "NEUTRAL").strip().upper()
            # bond_stage 从 payload 的 bond_summary 取（若存在），否则用 STRANGER
            _bond_stage = str((payload.get("bond_summary") or {}).get("stage") or "STRANGER").strip().upper()
            _user_msg_type = str(payload.get("msg_type") or "text").strip().lower()

            try:
                gateway_delivery = await _deliver_gateway_reply(
                    request_id=request_id,
                    payload=payload,
                    decision=decision,
                    reason_code=reason_code,
                    narrative=narrative,
                    emotion_tag=_emotion_tag,
                    bond_stage=_bond_stage,
                    user_msg_type=_user_msg_type,
                )
            except Exception as exc:
                gateway_delivery = {
                    "success": False,
                    "status": "FAILED",
                    "reason_code": "GATEWAY_REPLY_EXCEPTION",
                    "detail": f"{type(exc).__name__}: {str(exc)[:180]}",
                    "latency_ms": 0,
                    "reply_text_len": 0,
                    "reply_preview": "",
                    "reply_form": "text",
                    "reply_form_reason": "exception",
                    "voice_sent": False,
                }

        complete_ts = int(time.time() * 1000)
        latency_ms = complete_ts - dispatch_ts
        completion_status = "OK" if decision == "APPROVE" else "DEGRADED"
        completion_reason_code = reason_code
        detail_parts = [
            f"cortex_orchestrator decision={decision}",
            f"mode={dispatch_mode}",
            f"thread_flag={str(bool(run_in_thread)).lower()}",
            f"latency_ms={latency_ms}",
        ]
        if reason_code:
            detail_parts.append(f"reason_code={reason_code}")
        detail_parts.append(f"route_type={route_type}")
        if route_target:
            detail_parts.append(f"route_target={route_target}")
        if route_source:
            detail_parts.append(f"route_source={route_source}")
        if route_reason:
            detail_parts.append(f"route_reason={route_reason}")
        if route_override_reason:
            detail_parts.append(f"route_override_reason={route_override_reason}")
        if route_confidence is not None:
            detail_parts.append(f"route_confidence={route_confidence:.3f}")
        detail_parts.append(f"route_llm_used={str(route_llm_used).lower()}")
        if isinstance(deviation_meta, dict):
            detail_parts.append(f"deviation_reason={deviation_meta.get('reason_code')}")
            detail_parts.append(f"deviation_retry_applied={bool(deviation_meta.get('retry_applied'))}")
            if deviation_meta.get("overlap_ratio") is not None:
                detail_parts.append(f"deviation_overlap={deviation_meta.get('overlap_ratio')}")
            if deviation_meta.get("retry_overlap_ratio") is not None:
                detail_parts.append(f"deviation_retry_overlap={deviation_meta.get('retry_overlap_ratio')}")
        if gateway_delivery is not None:
            gateway_success = bool(gateway_delivery.get("success", True))
            gateway_reason_code = str(gateway_delivery.get("reason_code") or "")
            gateway_detail = str(gateway_delivery.get("detail") or "")
            if not gateway_success:
                degrade_on_gateway_failure = _parse_bool_env(
                    "HOST_GATEWAY_REPLY_FAILURE_DEGRADE_INPUT",
                    default=True,
                )
                if degrade_on_gateway_failure:
                    completion_status = "DEGRADED"
                    if not completion_reason_code:
                        completion_reason_code = gateway_reason_code or "GATEWAY_REPLY_DELIVERY_FAILED"
                else:
                    detail_parts.append("gateway_failure_ignored=true")
            detail_parts.append(f"gateway_success={str(gateway_success).lower()}")
            detail_parts.append(f"gateway_status={gateway_delivery.get('status')}")
            detail_parts.append(f"gateway_latency_ms={gateway_delivery.get('latency_ms')}")
            detail_parts.append(f"reply_text_len={gateway_delivery.get('reply_text_len')}")
            detail_parts.append(f"reply_preview={gateway_delivery.get('reply_preview')}")
            if gateway_reason_code:
                detail_parts.append(f"gateway_reason_code={gateway_reason_code}")
            if gateway_detail:
                detail_parts.append(f"gateway_detail={_build_reply_preview(gateway_detail, limit=160)}")

        execution_quality = {
            "degraded": False,
            "reason_code": None,
            "detail_tags": [],
            "skill_exec_count": 0,
        }
        if (
            _parse_bool_env("HOST_INPUT_EXEC_QUALITY_GATE_ENABLED", default=True)
            and isinstance(pipeline_result.get("commit_event"), dict)
        ):
            execution_quality = _analyze_request_execution_quality(
                ledger=ledger,
                request_id=request_id,
                decision=decision,
                route_type=route_type,
                route_target=route_target,
            )
            if bool(execution_quality.get("degraded")):
                completion_status = "DEGRADED"
                if not completion_reason_code:
                    completion_reason_code = str(
                        execution_quality.get("reason_code") or "EXECUTION_QUALITY_DEGRADED"
                    )
            detail_parts.append(f"exec_quality_degraded={str(bool(execution_quality.get('degraded'))).lower()}")
            if execution_quality.get("reason_code"):
                detail_parts.append(f"exec_quality_reason_code={execution_quality.get('reason_code')}")
            for tag in list(execution_quality.get("detail_tags") or ()):
                if str(tag or "").strip():
                    detail_parts.append(f"exec_quality_tag={tag}")

        completed_payload = dict(payload or {})
        completed_payload["decision"] = decision
        if completion_reason_code:
            completed_payload["reason_code"] = completion_reason_code
        completed_payload["route_type"] = route_type
        completed_payload["route_target"] = route_target
        completed_payload["route_task"] = route_task
        completed_payload["route_source"] = route_source
        completed_payload["route_reason"] = route_reason
        completed_payload["route_confidence"] = route_confidence
        completed_payload["route_override_reason"] = route_override_reason
        completed_payload["llm_route_used"] = route_llm_used
        completed_payload["exec_quality_degraded"] = bool(execution_quality.get("degraded"))
        completed_payload["exec_quality_reason_code"] = execution_quality.get("reason_code")
        completed_payload["exec_quality_tags"] = list(execution_quality.get("detail_tags") or [])
        completed_payload["exec_quality_skill_exec_count"] = int(execution_quality.get("skill_exec_count") or 0)
        if isinstance(deviation_meta, dict):
            completed_payload["deviation_reason"] = deviation_meta.get("reason_code")
            completed_payload["deviation_overlap_ratio"] = deviation_meta.get("overlap_ratio")
            completed_payload["deviation_retry_applied"] = bool(deviation_meta.get("retry_applied"))
            completed_payload["deviation_retry_overlap_ratio"] = deviation_meta.get("retry_overlap_ratio")
        if gateway_delivery is not None:
            completed_payload["gateway_success"] = bool(gateway_delivery.get("success", True))
            completed_payload["gateway_status"] = gateway_delivery.get("status")
            completed_payload["gateway_reason_code"] = gateway_delivery.get("reason_code")
            completed_payload["gateway_detail"] = gateway_delivery.get("detail")
            completed_payload["gateway_latency_ms"] = gateway_delivery.get("latency_ms")
            completed_payload["reply_text_len"] = gateway_delivery.get("reply_text_len")
            completed_payload["reply_preview"] = gateway_delivery.get("reply_preview")

        _log_input_event(
            ledger=ledger,
            request_id=request_id,
            session_id=session_id,
            source=source,
            input_payload=completed_payload,
            phase="COMPLETED",
            status=completion_status,
            reason_code=completion_reason_code,
            detail=" ".join(detail_parts),
            latency_ms=latency_ms,
            ts_ms=complete_ts,
        )
        _logger.info(
            "input_routes: dispatch completed request_id=%s source=%s decision=%s latency_ms=%d mode=%s",
            request_id,
            source,
            decision,
            latency_ms,
            dispatch_mode,
        )
    except Exception as exc:
        await _stop_progress_push_task(
            stop_event=progress_push_stop_event,
            task=progress_push_task,
        )
        fail_ts = int(time.time() * 1000)
        latency_ms = fail_ts - dispatch_ts
        _log_input_event(
            ledger=ledger,
            request_id=request_id,
            session_id=session_id,
            source=source,
            input_payload=payload,
            phase="FAILED",
            status="FAILED",
            reason_code="INPUT_GRAPH_ERROR",
            detail=f"{type(exc).__name__}: {str(exc)[:200]}",
            latency_ms=latency_ms,
            ts_ms=fail_ts,
        )
        _logger.error("input_routes: dispatch failed request_id=%s: %s", request_id, exc)
