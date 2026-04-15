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
# 规则路由决策（已废弃，规则逻辑已迁移到 cortex_route_decider._classify_task_intent_by_rules）
# ---------------------------------------------------------------------------

# Phase XV 架构重设计：build_gateway_rule_route_decision、run_gateway_intent_route_llm、
# apply_gateway_force_override、build_gateway_intent_draft 四个函数已删除。
# 意图识别统一由 cortex_route_decider.classify_task_intent() 完成，
# 所有来源（ASR/Web/GATEWAY）走同一套双层意图识别（规则 + LLM 兜底）。


__all__ = [
    "looks_like_progress_query",
    "looks_like_execution_intent",
    "build_gateway_skill_manifest_lines",
    "extract_first_json_object",
    "normalize_route_confidence",
    "normalize_gateway_llm_route_decision",
]
