"""Cortex route decider: 根据族上下文决定意图路由策略。

从 cortex_agent.py 拆分，负责综合内分泌门控、沉默意愿、Bond 阶段等
状态信息，决定路由策略（chat/skill/silent/throttle）。

Phase XV 扩展：增加双层任务意图分类（规则快速预判 + LLM 兜底），
统一承接所有来源（ASR/Web/GATEWAY）的意图识别，替代已废弃的
pre_intent_classifier 和 build_gateway_intent_draft。
"""

from __future__ import annotations

import logging
import re
from typing import Any

_logger = logging.getLogger("Natsume.cortex_route_decider")

# ---------------------------------------------------------------------------
# 任务意图规则集（迁移自 input_service_gateway.py）
# ---------------------------------------------------------------------------

_TASK_TRANSLATE_RE = re.compile(
    r"(翻译|译成|翻成|translate|translation|中译英|英译中|中译日|日译中)",
    re.IGNORECASE,
)
_TASK_MEMORY_RE = re.compile(
    r"(记住|记一下|记下来|写入记忆|保存.*记忆|记到记忆|remember this|save this memory|偏好是|我的偏好)",
    re.IGNORECASE,
)
_TASK_CODE_RE = re.compile(
    r"(报错|错误|异常|修复|修一下|改代码|写代码|单测|测试|diff|patch|traceback|stack trace|compile|build|修.*bug|bug.*修"
    r"|代码|编程|写.*函数|函数|脚本|调试|实现|开发|code|coding|programming|function|script|program"
    r"|python|javascript|typescript|java|golang|rust|c\+\+|debug|bug|class|algorithm|implement|develop)",
    re.IGNORECASE,
)
_TASK_VISION_UI_RE = re.compile(
    r"(screenshot|screen shot|截图|屏幕|界面|画面|看一下屏幕|看看这个界面|desktop|ui|window|interface|capture)",
    re.IGNORECASE,
)
_TASK_RECENT_TURN_RE = re.compile(
    r"(我刚刚|上一句|上一条|上句话|你刚刚|what did i just say|what did you just say)",
    re.IGNORECASE,
)
_TASK_PROGRESS_QUERY_RE = re.compile(
    r"(进度|进展|状态|结果呢|有结果吗|完成了?吗|做完了?吗|好了没|搞定了?吗|到哪了|还要多久|eta|done\??|status|progress|update)",
    re.IGNORECASE,
)
_TASK_EXEC_ACTION_MARKERS: tuple[str, ...] = (
    "请", "麻烦", "帮我", "帮忙", "需要你", "执行", "处理", "安排", "规划",
    "调用", "查询", "查一下", "查下", "检索", "搜索", "总结", "归纳",
    "翻译", "改写", "写一个", "生成", "实现", "改造", "优化", "修复",
    "排查", "测试", "验证", "启动", "停止", "重启",
    "run", "execute", "build", "fix", "test", "deploy", "search",
)
_TASK_ENTERTAINMENT_RE = re.compile(
    r"(动漫|漫画|番剧|动画|アニメ|マンガ|电影|电视剧|剧集|综艺|映画|ドラマ"
    r"|音乐|歌曲|歌手|专辑|音楽|小说|读书|游戏|ゲーム|爱好|兴趣|趣味)",
    re.IGNORECASE,
)


def _classify_task_intent_by_rules(text: str) -> dict[str, Any] | None:
    """第一层：规则快速预判（< 1ms）。命中则直接返回，不走 LLM。

    返回 None 表示规则未命中，需要走 LLM 兜底。
    """
    normalized = str(text or "").strip()
    if not normalized:
        return None

    compact = re.sub(r"\s+", "", normalized).lower()

    # 进度查询（最高优先级）
    if _TASK_PROGRESS_QUERY_RE.search(compact):
        return {"route": "task_progress", "target_id": "", "task": normalized,
                "reason_code": "RULE_TASK_PROGRESS", "confidence": 0.98, "route_source": "rule"}

    # 最近对话回溯
    if _TASK_RECENT_TURN_RE.search(normalized):
        return {"route": "recent_turn", "target_id": "", "task": normalized,
                "reason_code": "RULE_RECENT_TURN", "confidence": 0.95, "route_source": "rule"}

    # 视觉/截图
    if _TASK_VISION_UI_RE.search(normalized):
        return {"route": "delegate", "target_id": "vision_agent", "task": normalized,
                "reason_code": "RULE_VISION_UI", "confidence": 0.92, "route_source": "rule"}

    # 翻译（优先于代码，避免"翻译这段代码"被误判为 code）
    if _TASK_TRANSLATE_RE.search(normalized):
        return {"route": "delegate", "target_id": "translate_agent", "task": normalized,
                "reason_code": "RULE_FORCE_TRANSLATE_AGENT", "confidence": 0.95, "route_source": "rule"}

    # 记忆存储
    if _TASK_MEMORY_RE.search(normalized):
        return {"route": "skill", "target_id": "memory_store", "task": normalized,
                "reason_code": "RULE_FORCE_MEMORY_STORE", "confidence": 0.95, "route_source": "rule"}

    # 代码/编程（排除娱乐内容）
    if _TASK_CODE_RE.search(normalized) and not _TASK_ENTERTAINMENT_RE.search(normalized):
        return {"route": "delegate", "target_id": "code_agent", "task": normalized,
                "reason_code": "RULE_FORCE_CODE_AGENT", "confidence": 0.93, "route_source": "rule"}

    # 执行意图（命令前缀或动作关键词）
    if normalized.startswith(("/", "!", "run ", "exec ")):
        return {"route": "delegate", "target_id": "intent_agent", "task": normalized,
                "reason_code": "RULE_ACTION_PREFIX", "confidence": 0.90, "route_source": "rule"}

    compact_lower = normalized.lower().replace(" ", "")
    if any(marker in compact_lower for marker in _TASK_EXEC_ACTION_MARKERS):
        # 动作关键词命中，但置信度较低，交给 LLM 确认
        return None

    # 规则未命中
    return None


async def _classify_task_intent_by_llm(
    *,
    request_id: str,
    session_id: str,
    text: str,
    tribe_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """第二层：LLM 兜底（仅在规则未命中时触发）。

    使用 Flash 模型（低延迟），结合族状态做综合判断。
    返回 None 表示 LLM 不可用或解析失败，调用方应降级为 chat。
    """
    try:
        import os  # noqa: PLC0415
        if str(os.environ.get("HOST_CORTEX_INTENT_LLM_ENABLED", "true")).strip().lower() in {"0", "false", "no", "off"}:
            return None

        from host_brain.app.graph_config_builder import (  # noqa: PLC0415
            resolve_executive_router_llm_runtime,
            resolve_host_llm_runtime_async,
        )
        from host_brain.integrations.llm_helpers import astream_llm_chunks  # noqa: PLC0415

        llm_runtime = await resolve_host_llm_runtime_async(rollout_key=session_id, scene_type="EXEC_INTENT")
        router_runtime = resolve_executive_router_llm_runtime(base_runtime=llm_runtime)
        if not bool(router_runtime.get("available")):
            return None

        provider_name = str(router_runtime.get("provider_name") or "").strip()
        model_id = str(router_runtime.get("model_id") or "").strip()
        api_key = str(router_runtime.get("llm_api_key") or "").strip()
        if not provider_name or not model_id or not api_key:
            return None

        # 构建族状态摘要注入 prompt
        ctx = dict(tribe_context or {})
        vital_signs = dict(ctx.get("vital_signs") or {})
        bond_stage = str(vital_signs.get("bond_stage") or ctx.get("bond_stage") or "stranger").strip()
        emotion_label = str(vital_signs.get("emotion_label") or ctx.get("emotion_label") or "neutral").strip()
        tribe_summary = f"bond_stage={bond_stage}, emotion={emotion_label}"

        # 从 YAML 模板加载 prompt（降级时使用内联兜底）
        _FALLBACK_SYSTEM_PROMPT = (
            "你是意图路由分类器。根据用户消息判断意图类型，输出单个 JSON 对象。\n"
            "route 只能是以下之一：chat / delegate / skill / task_progress / recent_turn\n"
            "delegate 时 target_id 只能是：intent_agent / code_agent / translate_agent / vision_agent\n"
            "skill 时 target_id 填技能名称（如 memory_store）\n"
            "严格要求：仅输出 JSON，不要 markdown。\n"
            '字段：{"route","target_id","task","confidence","reason"}\n'
            "confidence 为 0~1 的数字。"
        )
        _FALLBACK_USER_PROMPT_TEMPLATE = "族状态：{tribe_summary}\n用户消息：{text}\n输出 JSON："
        try:
            from host_brain.app.prompt_template_store import resolve_prompt_template_json_merged  # noqa: PLC0415
            from collections.abc import Mapping as _Mapping  # noqa: PLC0415
            _raw = resolve_prompt_template_json_merged("cortex_route_decider.intent_classification_prompts")
            tpl: dict = dict(_raw) if isinstance(_raw, _Mapping) else {}
            system_prompt = str(tpl.get("system_prompt") or _FALLBACK_SYSTEM_PROMPT).strip()
            user_prompt_template = str(tpl.get("user_prompt_template") or _FALLBACK_USER_PROMPT_TEMPLATE).strip()
        except Exception:
            system_prompt = _FALLBACK_SYSTEM_PROMPT
            user_prompt_template = _FALLBACK_USER_PROMPT_TEMPLATE

        user_prompt = user_prompt_template.format(tribe_summary=tribe_summary, text=str(text or "").strip())

        timeout_ms = int(os.environ.get("HOST_CORTEX_INTENT_LLM_TIMEOUT_MS", "4000"))
        base_url_override = str(router_runtime.get("base_url_override") or "").strip() or None
        agent_gen_params = dict(router_runtime.get("agent_gen_params") or {"temperature": 0.1, "top_p": 0.8})

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
            if sum(len(c) for c in raw_chunks) >= 4000:
                break

        raw_text = "".join(raw_chunks).strip()
        from host_brain.app.input_service_gateway import extract_first_json_object  # noqa: PLC0415
        parsed = extract_first_json_object(raw_text)
        if not isinstance(parsed, dict):
            _logger.warning("[%s] cortex_intent_llm_parse_failed output=%.180s", request_id, raw_text)
            return None

        route = str(parsed.get("route") or "").strip().lower()
        if route not in {"chat", "delegate", "skill", "task_progress", "recent_turn"}:
            return None
        target_id = str(parsed.get("target_id") or "").strip()
        task = str(parsed.get("task") or text or "").strip()
        reason = str(parsed.get("reason") or "LLM_INTENT_ROUTE").strip() or "LLM_INTENT_ROUTE"
        try:
            confidence = max(0.0, min(1.0, float(parsed.get("confidence") or 0.7)))
        except (TypeError, ValueError):
            confidence = 0.7

        return {"route": route, "target_id": target_id, "task": task,
                "reason_code": reason, "confidence": confidence, "route_source": "llm"}

    except Exception as exc:
        _logger.warning("cortex_intent_llm_failed request_id=%s error=%s", request_id, exc)
        return None


async def classify_task_intent(
    *,
    request_id: str,
    session_id: str,
    text: str,
    tribe_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """双层任务意图分类（规则快速预判 + LLM 兜底）。

    统一承接所有来源（ASR/Web/GATEWAY）的意图识别。
    返回格式：
      {"route": "chat"|"delegate"|"skill"|"task_progress"|"recent_turn",
       "target_id": str, "task": str,
       "reason_code": str, "confidence": float|None, "route_source": "rule"|"llm"|"default"}
    """
    normalized = str(text or "").strip()

    # 第一层：规则快速预判
    rule_result = _classify_task_intent_by_rules(normalized)
    if rule_result is not None:
        return rule_result

    # 第二层：LLM 兜底
    llm_result = await _classify_task_intent_by_llm(
        request_id=request_id,
        session_id=session_id,
        text=normalized,
        tribe_context=tribe_context,
    )
    if llm_result is not None:
        return llm_result

    # 默认：chat
    return {"route": "chat", "target_id": "", "task": normalized,
            "reason_code": "DEFAULT_CHAT", "confidence": 0.7, "route_source": "default"}


def route_intent(
    *,
    message: str,
    tribe_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Supervisor 意图路由：根据消息内容和族上下文决定路由策略。

    注意：此函数为同步版本，只做生理门控判断。
    任务意图分类（chat/code/translate/exec）请使用异步的 classify_task_intent()。
    """
    ctx = dict(tribe_context or {})
    vital_signs = dict(ctx.get("vital_signs") or {})
    gate_state = str(vital_signs.get("gate_state") or ctx.get("gate_state") or "open").strip().lower()
    silence_willingness = float(vital_signs.get("silence_willingness") or ctx.get("silence_willingness") or 0.5)
    bond_stage = str(vital_signs.get("bond_stage") or ctx.get("bond_stage") or "stranger").strip().lower()
    homeostasis_ok = bool(vital_signs.get("homeostasis_ok", True))
    interoception_state = dict(ctx.get("interoception_state") or {})
    vestibular_state = dict(ctx.get("vestibular_state") or {})
    interoceptive_index = float(interoception_state.get("interoceptive_index") or vital_signs.get("interoceptive_index") or 0.0)
    vestibular_jitter = float(vestibular_state.get("jitter_index") or 0.0)

    if bool(ctx.get("initiative_request", False)):
        try:
            from host_brain.tribe.initiative_governor_agent import get_initiative_governor_agent  # noqa: PLC0415
            decision = get_initiative_governor_agent().evaluate_initiative(
                action=str(ctx.get("initiative_action") or "proactive_talk"),
                motive=ctx.get("motive"),
                session_id=str(ctx.get("session_id") or ""),
                user_id=str(ctx.get("user_id") or ""),
                context=ctx,
            )
            if decision.get("decision") == "BLOCK":
                return {"route": "throttle", "reason": f"initiative_block:{decision.get('reason_code')}", "gate_state": gate_state, "confidence": 0.92}
            if decision.get("decision") == "DEFER":
                return {"route": "silent", "reason": f"initiative_defer:{decision.get('reason_code')}", "gate_state": gate_state, "confidence": 0.78}
        except Exception:
            pass

    if gate_state == "pause":
        return {"route": "throttle", "reason": "endocrine_gate_pause", "gate_state": gate_state, "confidence": 0.95}

    if interoceptive_index >= 0.82:
        return {"route": "throttle", "reason": "interoceptive_overload", "gate_state": gate_state, "confidence": min(0.95, max(0.75, interoceptive_index))}

    if vestibular_jitter >= 0.78:
        return {"route": "throttle", "reason": "vestibular_instability", "gate_state": gate_state, "confidence": min(0.9, max(0.7, vestibular_jitter)), "vestibular_bias": "throttle", "vestibular_jitter": round(vestibular_jitter, 4)}

    route_bias = str(vestibular_state.get("route_bias") or "normal").strip().lower()
    damping_factor = float(vestibular_state.get("damping_factor") or 1.0)

    if route_bias == "safe_only":
        return {"route": "chat", "reason": "vestibular_safe_only", "gate_state": gate_state, "confidence": 0.85, "vestibular_bias": "safe_only", "vestibular_jitter": round(vestibular_jitter, 4), "damping_factor": round(damping_factor, 4)}

    if silence_willingness >= 0.85:
        return {"route": "silent", "reason": "high_silence_willingness", "gate_state": gate_state, "confidence": round(silence_willingness, 4), "vestibular_bias": route_bias, "vestibular_jitter": round(vestibular_jitter, 4), "damping_factor": round(damping_factor, 4)}

    if gate_state == "throttle" or not homeostasis_ok:
        return {"route": "throttle", "reason": "endocrine_gate_throttle" if gate_state == "throttle" else "homeostasis_disrupted", "gate_state": gate_state, "confidence": 0.75, "vestibular_bias": route_bias, "vestibular_jitter": round(vestibular_jitter, 4), "damping_factor": round(damping_factor, 4)}

    return {"route": "chat", "reason": f"normal_flow (bond_stage={bond_stage})", "gate_state": gate_state, "confidence": 0.9, "vestibular_bias": route_bias, "vestibular_jitter": round(vestibular_jitter, 4), "damping_factor": round(damping_factor, 4)}


__all__ = ["route_intent", "classify_task_intent", "_classify_task_intent_by_rules"]
