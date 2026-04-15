"""input_service_gateway.py — 输入路由 Gateway 意图路由服务层。

从 input_routes.py 提取的 gateway 意图路由、规则路由、LLM 路由、强制覆盖相关函数。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

_logger = logging.getLogger("Natsume.input_routes")

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

_ALLOWED_LLM_ROUTE_TYPES: set[str] = {
    "chat", "delegate", "skill", "plan", "recent_turn", "task_progress",
}
_ALLOWED_DELEGATE_TARGETS: set[str] = {
    "intent_agent", "code_agent", "translate_agent",
    "vision_agent", "repair_agent", "retrieval_agent", "judge_agent",
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
_GATEWAY_ACK_PROGRESS_QUERY_RE = re.compile(
    r"(进度|进展|状态|结果呢|有结果吗|完成了?吗|做完了?吗|好了没|搞定了?吗|到哪了|还要多久|eta|done\??|status|progress|update)",
    re.IGNORECASE,
)
_GATEWAY_ACK_PROGRESS_MARKERS: tuple[str, ...] = (
    "进度", "进展", "状态", "结果呢", "有结果吗", "完成了吗", "做完了吗",
    "搞定了吗", "好了没", "到哪了", "到哪一步", "还要多久", "什么时候好",
    "status", "progress", "update", "done yet", "eta",
)
_GATEWAY_ACK_ACTION_MARKERS: tuple[str, ...] = (
    "请", "麻烦", "帮我", "帮忙", "需要你", "执行", "处理", "安排", "规划",
    "调用", "查询", "查一下", "查下", "检索", "搜索", "总结", "归纳",
    "翻译", "改写", "写一个", "生成", "实现", "改造", "优化", "修复",
    "排查", "测试", "验证", "启动", "停止", "重启",
    "run", "execute", "build", "fix", "test", "deploy", "search",
)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _parse_bool_env(name: str, *, default: bool) -> bool:
    import os  # noqa: PLC0415
    val = str(os.environ.get(name, "")).strip().lower()
    if val in {"1", "true", "yes", "on"}:
        return True
    if val in {"0", "false", "no", "off"}:
        return False
    return default


def _parse_int_env(name: str, *, default: int) -> int:
    import os  # noqa: PLC0415
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_format_template(text: str, **kwargs: Any) -> str:
    try:
        return str(text or "").format(**kwargs)
    except Exception:
        return str(text or "")


def _load_template_mapping(template_id: str) -> dict[str, Any]:
    from host_brain.app.prompt_template_store import resolve_prompt_template_json_merged  # noqa: PLC0415
    raw = resolve_prompt_template_json_merged(template_id)
    return dict(raw) if isinstance(raw, dict) else {}


def normalize_route_confidence(value: Any) -> float | None:
    """归一化路由置信度到 [0, 1]。"""
    confidence = _coerce_float(value)
    if confidence is None:
        return None
    return max(0.0, min(1.0, float(confidence)))


# ---------------------------------------------------------------------------
# 进度/执行意图判断
# ---------------------------------------------------------------------------

def looks_like_progress_query(text: str) -> bool:
    """判断是否为进度查询消息。"""
    compact = re.sub(r"\s+", "", str(text or "")).strip().lower()
    if not compact:
        return False
    if _GATEWAY_ACK_PROGRESS_QUERY_RE.search(compact):
        return True
    return any(marker in compact for marker in _GATEWAY_ACK_PROGRESS_MARKERS)


def looks_like_execution_intent(text: str) -> bool:
    """判断是否为执行意图消息。"""
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized:
        return False
    if looks_like_progress_query(normalized):
        return False
    compact = normalized.replace(" ", "").lower()
    if normalized.startswith(("/", "!", "run ", "exec ")):
        return True
    return any(marker in compact for marker in _GATEWAY_ACK_ACTION_MARKERS)


# ---------------------------------------------------------------------------
# 技能清单构建
# ---------------------------------------------------------------------------

def build_gateway_skill_manifest_lines(*, skill_registry: Any) -> list[str]:
    """构建 gateway 路由 LLM 使用的技能清单文本行。"""
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
            line = _safe_format_template(line_with_description, skill_name=skill_name, description=description)
        else:
            line = _safe_format_template(line_without_description, skill_name=skill_name)
        normalized_line = str(line).strip()
        if normalized_line:
            lines.append(normalized_line)
    return lines or fallback_lines


# ---------------------------------------------------------------------------
# JSON 解析
# ---------------------------------------------------------------------------

def extract_first_json_object(raw_text: str) -> dict[str, Any] | None:
    """从文本中提取第一个 JSON 对象。"""
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
                    candidate = text[start: idx + 1]
                    try:
                        parsed = json.loads(candidate)
                        if isinstance(parsed, dict):
                            return parsed
                    except Exception:
                        break
        start = text.find("{", start + 1)
    return None


# ---------------------------------------------------------------------------
# LLM 路由决策归一化
# ---------------------------------------------------------------------------

def normalize_gateway_llm_route_decision(
    *,
    raw_decision: dict[str, Any],
    skill_registry: Any,
) -> dict[str, Any] | None:
    """归一化 LLM 路由决策，验证合法性。"""
    route = str(raw_decision.get("route") or raw_decision.get("route_type") or "").strip().lower()
    if route not in _ALLOWED_LLM_ROUTE_TYPES:
        return None
    target_id = str(
        raw_decision.get("target_id") or raw_decision.get("skill_id")
        or raw_decision.get("agent_id") or raw_decision.get("delegate_agent") or ""
    ).strip()
    task = str(raw_decision.get("task") or raw_decision.get("query") or raw_decision.get("text") or "").strip()
    reason = str(raw_decision.get("reason") or raw_decision.get("reason_code") or "LLM_ROUTE").strip() or "LLM_ROUTE"
    confidence = normalize_route_confidence(raw_decision.get("confidence"))

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


# ---------------------------------------------------------------------------
# 规则路由决策
# ---------------------------------------------------------------------------

def build_gateway_rule_route_decision(
    *,
    text: str,
    skill_registry: Any,
) -> dict[str, Any]:
    """基于规则构建 gateway 路由决策（不调用 LLM）。"""
    normalized = str(text or "").strip()
    if not normalized:
        return {"route": "chat", "target_id": "", "task": "", "reason_code": "RULE_EMPTY_TEXT",
                "confidence": None, "route_source": "rule", "llm_route_used": False}
    if looks_like_progress_query(normalized):
        return {"route": "task_progress", "target_id": "", "task": "", "reason_code": "RULE_TASK_PROGRESS",
                "confidence": 0.98, "route_source": "rule", "llm_route_used": False}
    if _GATEWAY_RECENT_TURN_RE.search(normalized):
        return {"route": "recent_turn", "target_id": "", "task": "", "reason_code": "RULE_RECENT_TURN",
                "confidence": 0.95, "route_source": "rule", "llm_route_used": False}
    if _GATEWAY_FORCE_TRANSLATE_RE.search(normalized):
        return {"route": "delegate", "target_id": "translate_agent", "task": normalized,
                "reason_code": "RULE_FORCE_TRANSLATE_AGENT", "confidence": 0.95,
                "route_source": "rule", "llm_route_used": False}
    if _GATEWAY_FORCE_MEMORY_RE.search(normalized):
        memory_skill_exists = False
        try:
            memory_skill_exists = bool(skill_registry and skill_registry.get("memory_store") is not None)
        except Exception:
            pass
        if memory_skill_exists:
            return {"route": "skill", "target_id": "memory_store", "task": normalized,
                    "reason_code": "RULE_FORCE_MEMORY_STORE", "confidence": 0.95,
                    "route_source": "rule", "llm_route_used": False}
        return {"route": "delegate", "target_id": "intent_agent", "task": normalized,
                "reason_code": "RULE_FORCE_MEMORY_FALLBACK_INTENT", "confidence": 0.90,
                "route_source": "rule", "llm_route_used": False}
    if _GATEWAY_FORCE_CODE_RE.search(normalized):
        return {"route": "delegate", "target_id": "code_agent", "task": normalized,
                "reason_code": "RULE_FORCE_CODE_AGENT", "confidence": 0.95,
                "route_source": "rule", "llm_route_used": False}
    compact = normalized.lower()
    if compact.startswith(_GATEWAY_ROUTE_ACTION_PREFIXES) or looks_like_execution_intent(normalized):
        return {"route": "delegate", "target_id": "intent_agent", "task": normalized,
                "reason_code": "RULE_ACTION_INTENT", "confidence": 0.85,
                "route_source": "rule", "llm_route_used": False}
    return {"route": "chat", "target_id": "", "task": "", "reason_code": "RULE_CHAT_DEFAULT",
            "confidence": 0.75, "route_source": "rule", "llm_route_used": False}


# ---------------------------------------------------------------------------
# LLM 路由调用
# ---------------------------------------------------------------------------

async def run_gateway_intent_route_llm(
    *,
    request_id: str,
    session_id: str,
    text: str,
    skill_registry: Any,
) -> dict[str, Any] | None:
    """调用 LLM 进行 gateway 意图路由判断。"""
    if not _parse_bool_env("HOST_GATEWAY_INTENT_LLM_ENABLED", default=True):
        return None

    from host_brain.app.graph_config_builder import (  # noqa: PLC0415
        resolve_executive_router_llm_runtime,
        resolve_host_llm_runtime_async,
    )
    from host_brain.integrations.llm_helpers import astream_llm_chunks  # noqa: PLC0415
    from host_brain.app.input_service_log import build_reply_preview  # noqa: PLC0415

    llm_runtime = await resolve_host_llm_runtime_async(rollout_key=session_id, scene_type="EXEC_INTENT")
    router_runtime = resolve_executive_router_llm_runtime(base_runtime=llm_runtime)
    if not bool(router_runtime.get("available")):
        return None

    provider_name = str(router_runtime.get("provider_name") or "").strip()
    model_id = str(router_runtime.get("model_id") or "").strip()
    api_key = str(router_runtime.get("llm_api_key") or "").strip()
    if not provider_name or not model_id or not api_key:
        return None

    timeout_ms = max(800, _parse_int_env(
        "HOST_GATEWAY_INTENT_LLM_TIMEOUT_MS",
        default=int(router_runtime.get("timeout_ms") or 5000),
    ))
    base_url_override = str(router_runtime.get("base_url_override") or "").strip() or None
    agent_gen_params = dict(router_runtime.get("agent_gen_params") or {"temperature": 0.1, "top_p": 0.8})

    templates = _load_template_mapping("executive_router.routing_prompt.templates")
    skill_lines = build_gateway_skill_manifest_lines(skill_registry=skill_registry)
    skill_manifest_text = "\n".join(skill_lines) if skill_lines else "- intent_agent: fallback"
    system_prompt_template = str(
        templates.get("system_prompt_template") or
        "你是路由分类器。输出单个 JSON 对象。"
        " route 只能是 chat/delegate/skill/task_progress/recent_turn/plan。"
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
    parsed_obj = extract_first_json_object(raw_text)
    if not isinstance(parsed_obj, dict):
        _logger.warning("[%s] gateway_intent_llm_parse_failed output=%s", request_id,
                        build_reply_preview(raw_text, limit=180))
        return None
    return normalize_gateway_llm_route_decision(raw_decision=parsed_obj, skill_registry=skill_registry)


# ---------------------------------------------------------------------------
# 强制覆盖
# ---------------------------------------------------------------------------

def apply_gateway_force_override(
    *,
    text: str,
    decision: dict[str, Any],
    skill_registry: Any,
) -> dict[str, Any]:
    """对路由决策应用强制覆盖规则（优先级高于 LLM 决策）。"""
    normalized = str(text or "").strip()
    if not normalized:
        return dict(decision or {})
    route = str(decision.get("route") or "").strip().lower()
    target_id = str(decision.get("target_id") or "").strip()
    patched = dict(decision or {})

    if looks_like_progress_query(normalized):
        if route != "task_progress":
            patched.update({"route": "task_progress", "target_id": "", "task": "",
                            "reason_code": "FORCE_OVERRIDE_TASK_PROGRESS",
                            "route_source": "override", "route_override_reason": "FORCE_OVERRIDE_TASK_PROGRESS"})
        return patched

    if _GATEWAY_RECENT_TURN_RE.search(normalized):
        if route != "recent_turn":
            patched.update({"route": "recent_turn", "target_id": "", "task": "",
                            "reason_code": "FORCE_OVERRIDE_RECENT_TURN",
                            "route_source": "override", "route_override_reason": "FORCE_OVERRIDE_RECENT_TURN"})
        return patched

    if _GATEWAY_FORCE_TRANSLATE_RE.search(normalized):
        if not (route == "delegate" and target_id == "translate_agent"):
            patched.update({"route": "delegate", "target_id": "translate_agent", "task": normalized,
                            "reason_code": "FORCE_OVERRIDE_TRANSLATE_AGENT",
                            "route_source": "override", "route_override_reason": "FORCE_OVERRIDE_TRANSLATE_AGENT"})
        return patched

    memory_skill_exists = False
    try:
        memory_skill_exists = bool(skill_registry and skill_registry.get("memory_store") is not None)
    except Exception:
        pass
    if _GATEWAY_FORCE_MEMORY_RE.search(normalized):
        if memory_skill_exists and not (route == "skill" and target_id == "memory_store"):
            patched.update({"route": "skill", "target_id": "memory_store", "task": normalized,
                            "reason_code": "FORCE_OVERRIDE_MEMORY_STORE",
                            "route_source": "override", "route_override_reason": "FORCE_OVERRIDE_MEMORY_STORE"})
            return patched
        if not memory_skill_exists and not (route == "delegate" and target_id == "intent_agent"):
            patched.update({"route": "delegate", "target_id": "intent_agent", "task": normalized,
                            "reason_code": "FORCE_OVERRIDE_MEMORY_FALLBACK_INTENT",
                            "route_source": "override",
                            "route_override_reason": "FORCE_OVERRIDE_MEMORY_FALLBACK_INTENT"})
            return patched

    if _GATEWAY_FORCE_CODE_RE.search(normalized) and not (route == "delegate" and target_id == "code_agent"):
        patched.update({"route": "delegate", "target_id": "code_agent", "task": normalized,
                        "reason_code": "FORCE_OVERRIDE_CODE_AGENT",
                        "route_source": "override", "route_override_reason": "FORCE_OVERRIDE_CODE_AGENT"})
        return patched

    if route == "chat" and looks_like_execution_intent(normalized):
        patched.update({"route": "delegate", "target_id": "intent_agent", "task": normalized,
                        "reason_code": "FORCE_OVERRIDE_ACTION_INTENT",
                        "route_source": "override", "route_override_reason": "FORCE_OVERRIDE_ACTION_INTENT"})
        return patched
    return patched


# ---------------------------------------------------------------------------
# 意图草稿构建（主入口）
# ---------------------------------------------------------------------------

def get_skill_registry_safe() -> Any:
    """安全获取 skill_registry，失败时返回 None。"""
    try:
        from host_brain.app.dependencies import get_skill_registry  # noqa: PLC0415
        return get_skill_registry()
    except Exception:
        return None


async def build_gateway_intent_draft(
    *,
    request_id: str,
    session_id: str,
    source: str,
    text: str,
) -> dict[str, Any]:
    """构建 gateway 意图草稿（两层路由：规则 → LLM → 强制覆盖）。"""
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

    skill_registry = get_skill_registry_safe()
    route_decision = build_gateway_rule_route_decision(text=text, skill_registry=skill_registry)
    llm_decision: dict[str, Any] | None = None
    try:
        llm_decision = await run_gateway_intent_route_llm(
            request_id=request_id,
            session_id=session_id,
            text=text,
            skill_registry=skill_registry,
        )
    except Exception as exc:
        _logger.warning("[%s] gateway_intent_llm_route_failed: %s", request_id, exc)

    if llm_decision is not None:
        route_decision = dict(llm_decision)
    route_decision = apply_gateway_force_override(
        text=text, decision=route_decision, skill_registry=skill_registry,
    )

    route = str(route_decision.get("route") or "chat").strip().lower()
    target_id = str(route_decision.get("target_id") or "").strip()
    task = str(route_decision.get("task") or "").strip()
    route_reason = str(route_decision.get("reason_code") or "ROUTE_UNKNOWN").strip() or "ROUTE_UNKNOWN"
    route_source = str(route_decision.get("route_source") or "rule").strip() or "rule"
    route_confidence = normalize_route_confidence(route_decision.get("confidence"))
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


__all__ = [
    "looks_like_progress_query",
    "looks_like_execution_intent",
    "build_gateway_skill_manifest_lines",
    "extract_first_json_object",
    "normalize_route_confidence",
    "normalize_gateway_llm_route_decision",
    "build_gateway_rule_route_decision",
    "run_gateway_intent_route_llm",
    "apply_gateway_force_override",
    "build_gateway_intent_draft",
    "get_skill_registry_safe",
]
