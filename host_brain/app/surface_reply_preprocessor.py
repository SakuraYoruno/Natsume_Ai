"""surface_reply_preprocessor.py — Surface reply 输入结构与低延迟文本工具。

旧 SurfaceReply 主体已移除；本模块仅保留 Surface 主链仍需要的数据结构与工具函数：
  - 输入/输出结构体（SurfaceSubAgentContext、SurfaceReplyInput）
  - 工具函数（SSE、净化、截断、策略守卫等）
  - 子 Agent 结果渲染（确定性回复、Hub Skill 回复、Plan 执行回复）
  - 进度/纠偏文本渲染
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from host_brain.persona.relational_chat import is_relational_chat_message
from host_brain.model_runtime.prompt_specs import resolve_prompt_spec_mapping
from host_brain.model_runtime.prompt_builder_utils import (
    _format_template_text,
    _normalize_template_lang,
)
from host_brain.agent_runtime.skill_ids import AGENT_RUNTIME_SKILL_IDS, is_agent_runtime_skill
from host_brain.core.runtime_helpers import _coerce_mapping as _mapping_from_any

_DELEGATE_MARKER_RE = re.compile(r"\[DELEGATE:[^\]]+\]", re.IGNORECASE)
# ---------------------------------------------------------------------------
# 输入/输出结构
# ---------------------------------------------------------------------------
@dataclass
class SurfaceSubAgentContext:
    """子 Agent 执行结果，供 Surface reply 主链或确定性渲染使用。"""
    agent_id: str = ""
    task: str = ""
    result_text: str = ""
    success: bool = True
    latency_ms: int = 0
    error: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SurfaceReplyInput:
    """Surface reply 的完整输入上下文。"""
    request_id: str = ""
    session_id: str = ""
    user_message: str = ""
    person_id: str = ""
    workspace_id: str = ""
    emotion_context: Dict[str, Any] = field(default_factory=dict)
    emotion_snapshot: Dict[str, Any] = field(default_factory=dict)
    bond_summary: Dict[str, Any] = field(default_factory=dict)
    retrieval_evidence_items: List[Dict[str, Any]] = field(default_factory=list)
    retrieval_summary: Dict[str, Any] = field(default_factory=lambda: {"status": "SKIPPED"})
    sub_agent_context: Optional[SurfaceSubAgentContext] = None
    render_target_lang: str = "zh"
    reply_channel: Optional[str] = None
    reply_target: Optional[str] = None
    response_form: str = ""
    suppress_edge_tts: Optional[bool] = None
    suppress_edge_vts: Optional[bool] = None
    defer_effects_to_task_runtime: bool = False
    # Opaque, app-local authority injected only by a frozen governed Chat
    # worker.  It stages an attempt/lease/grant-bound sentence before SSE
    # visibility; it is never caller data or an external delivery port.
    governed_delivery_authority: Any | None = field(default=None, repr=False)
    thread_id: Optional[str] = None
    ts_ms: int = 0
    intent_draft: Dict[str, Any] = field(default_factory=dict)
    trace_stage_meta: Dict[str, Any] = field(default_factory=dict)
    governor_result: Dict[str, Any] = field(default_factory=dict)
    delivery_authorization: Dict[str, Any] = field(default_factory=dict)
    llm_runtime: Optional[Dict[str, Any]] = None
    render_policy: Dict[str, Any] = field(default_factory=dict)
    surface_task_control: Dict[str, Any] = field(default_factory=dict)
    scene_awareness: Dict[str, Any] = field(default_factory=dict)
    attachments: List[Dict[str, Any]] = field(default_factory=list)
    multimodal_observations: Dict[str, Any] = field(default_factory=dict)
    attachment_summary: str = ""
    eval_history_turns: List[Dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _strip_internal_delegate_markers(text: str) -> str:
    normalized = str(text or "")
    normalized = _DELEGATE_MARKER_RE.sub("", normalized)
    normalized = normalized.replace("{agent_result}", "")
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
    return normalized


def _build_reply_preview(text: str, *, limit: int = 120) -> str:
    normalized = str(text or "").strip().replace("\r", " ").replace("\n", " ")
    if len(normalized) <= int(limit):
        return normalized
    return normalized[: max(1, int(limit) - 3)].rstrip() + "..."


def _resolve_localized_template_value(value: Any, *, render_target_lang: str | None = None) -> Any:
    if isinstance(value, Mapping):
        keys = {str(k).strip() for k in value.keys()}
        if keys.issubset({"zh", "ja", "default"}) and ("zh" in keys or "ja" in keys):
            lang_key = _normalize_template_lang(render_target_lang)
            return value.get(lang_key) or value.get("zh") or value.get("ja") or value.get("default")
    return value


def _mapping_section(mapping: Mapping[str, Any], key: str, *, render_target_lang: str | None = None) -> dict[str, Any]:
    raw = _resolve_localized_template_value(mapping.get(key), render_target_lang=render_target_lang)
    return dict(raw) if isinstance(raw, Mapping) else {}


def _mapping_text(
    mapping: Mapping[str, Any],
    key: str,
    *,
    default: str = "",
    render_target_lang: str | None = None,
    **kwargs: Any,
) -> str:
    raw = _resolve_localized_template_value(mapping.get(key, default), render_target_lang=render_target_lang)
    return _format_template_text(raw, **kwargs)


def _runtime_text(key: str, *, render_target_lang: str | None = None, **kwargs: Any) -> str:
    templates = resolve_prompt_spec_mapping("surface_reply.runtime_texts")
    return _mapping_text(templates, key, render_target_lang=render_target_lang, **kwargs)


# ---------------------------------------------------------------------------
# 子 Agent 结果渲染
# ---------------------------------------------------------------------------
def _normalize_plan_step_description(description: str, *, render_target_lang: str = "zh") -> str:
    text = str(description or "").strip()
    if not text:
        return ""
    text = re.sub(r"^\s*(?:旧角色|旧角色|夏目藍|小蓝|阿蓝)[，,、:：\s]*", "", text)
    lang = _normalize_template_lang(render_target_lang)
    prefixes = ("先", "然后", "再", "接着", "顺便", "帮我", "给我", "请") if lang == "zh" else ("まず", "それから", "次に", "続けて", "ついでに", "私に", "どうか", "お願いします")
    changed = True
    while changed:
        changed = False
        for prefix in prefixes:
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
                changed = True
    return text.strip(" 　。！？!?，,；;：:")


def _render_plan_step_success_cn(step: Dict[str, Any], *, render_target_lang: str = "zh") -> str:
    templates = resolve_prompt_spec_mapping("surface_reply.plan_execution.reply_templates")
    success_templates = _mapping_section(templates, "step_success", render_target_lang=render_target_lang)
    step_index = int(step.get("step_index") or 0)
    target_id = str(step.get("target_id") or _runtime_text("default_step_target", render_target_lang=render_target_lang)).strip()
    description = _normalize_plan_step_description(str(step.get("description") or step.get("task_description") or ""), render_target_lang=render_target_lang)
    result_text = str(step.get("result_text") or "").strip()
    description_or_target = description or target_id
    # 特殊 target_id 处理
    special_targets = {"web_search", "memory_store", "system_info", "system_resource_snapshot", "process_resource_top", "resource_pressure_analysis", "resource_manager_status", "resource_release", "process_list", "network_status", "file_read", "file_write", "file_list", *AGENT_RUNTIME_SKILL_IDS, "intent_agent"}
    if target_id in special_targets:
        if target_id == "web_search":
            topic = description or _runtime_text("default_topic", render_target_lang=render_target_lang)
            if _normalize_template_lang(render_target_lang) == "ja":
                topic = re.sub(r"^(?:検索して|探して|調べて|見つけて)\s*", "", topic).strip() or _runtime_text("default_topic", render_target_lang=render_target_lang)
            else:
                topic = re.sub(r"^(?:搜索(?:一下)?|查(?:一下)?|帮我搜索(?:一下)?|帮我查(?:一下)?|给我搜索(?:一下)?|给我查(?:一下)?)\s*", "", topic)
            return _mapping_text(success_templates, "web_search", render_target_lang=render_target_lang, step_index=step_index, topic=(topic or _runtime_text("default_topic", render_target_lang=render_target_lang)))
        if target_id == "memory_store":
            remember_prefix = "覚えて" if _normalize_template_lang(render_target_lang) == "ja" else "记住"
            if description.startswith(remember_prefix):
                remembered = description.removeprefix(remember_prefix).strip()
                if remembered:
                    if _normalize_template_lang(render_target_lang) == "zh" and remembered.startswith("我"):
                        remembered = "你" + remembered[1:]
                    return _mapping_text(success_templates, "memory_store.remembered", render_target_lang=render_target_lang, step_index=step_index, remembered=remembered)
            return _mapping_text(success_templates, "memory_store.default", render_target_lang=render_target_lang, step_index=step_index)
        if is_agent_runtime_skill(target_id):
            return _mapping_text(success_templates, "agent_worker", render_target_lang=render_target_lang, step_index=step_index)
        return _mapping_text(success_templates, target_id, render_target_lang=render_target_lang, step_index=step_index)
    if result_text:
        return _mapping_text(success_templates, "__with_result", render_target_lang=render_target_lang, step_index=step_index, description_or_target=description_or_target)
    return _mapping_text(success_templates, "__default", render_target_lang=render_target_lang, step_index=step_index, description_or_target=description_or_target)


def _render_plan_step_failure_cn(step: Dict[str, Any], *, render_target_lang: str = "zh") -> str:
    templates = resolve_prompt_spec_mapping("surface_reply.plan_execution.reply_templates")
    failure_templates = _mapping_section(templates, "step_failure", render_target_lang=render_target_lang)
    step_index = int(step.get("step_index") or 0)
    target_id = str(step.get("target_id") or _runtime_text("default_step_target", render_target_lang=render_target_lang)).strip()
    description = _normalize_plan_step_description(str(step.get("description") or step.get("task_description") or ""), render_target_lang=render_target_lang)
    error_text = str(step.get("error") or "").strip()
    recovery_text = str(step.get("recovery_result_text") or "").strip()
    base = _mapping_text(failure_templates, "base", render_target_lang=render_target_lang, step_index=step_index, description_or_target=(description or target_id))
    if error_text:
        base += _mapping_text(failure_templates, "error_suffix", render_target_lang=render_target_lang, error_text=error_text)
    if recovery_text:
        base += _mapping_text(failure_templates, "recovery_suffix", render_target_lang=render_target_lang, recovery_text=recovery_text)
    return base


def _build_plan_execution_reply_cn(ctx: SurfaceSubAgentContext, *, render_target_lang: str = "zh") -> str | None:
    templates = resolve_prompt_spec_mapping("surface_reply.plan_execution.reply_templates")
    summary_templates = _mapping_section(templates, "summary", render_target_lang=render_target_lang)
    payload = dict(ctx.payload or {})
    steps = list(payload.get("steps") or [])
    if not steps:
        return None
    total_steps = int(payload.get("total_steps") or len(steps))
    parallel_enabled = bool(payload.get("parallel_enabled"))
    success_count = int(payload.get("success_count") or 0)
    intro = (_mapping_text(summary_templates, "intro_parallel", render_target_lang=render_target_lang, total_steps=total_steps) if parallel_enabled and total_steps > 1 else _mapping_text(summary_templates, "intro_sequential", render_target_lang=render_target_lang, total_steps=total_steps))
    parts = [intro]
    for raw_step in steps:
        step = dict(raw_step or {})
        if bool(step.get("success")):
            parts.append(_render_plan_step_success_cn(step, render_target_lang=render_target_lang))
        else:
            parts.append(_render_plan_step_failure_cn(step, render_target_lang=render_target_lang))
    parts.append(_mapping_text(summary_templates, "all_done", render_target_lang=render_target_lang) if success_count == len(steps) else _mapping_text(summary_templates, "partial_done", render_target_lang=render_target_lang))
    return "".join(parts)


def _infer_open_app_name(task_text: str, *, render_target_lang: str) -> str:
    normalized = str(task_text or "").strip()
    if not normalized:
        return _mapping_text(
            resolve_prompt_spec_mapping("surface_reply.system_skill.reply_templates"),
            "default_app_name",
            render_target_lang=render_target_lang,
        )
    normalized = re.sub(r"^(?:旧角色|旧角色|夏目藍|natsume ai|natsume)[，,\s:：]*", "", normalized, flags=re.IGNORECASE).strip()
    normalized = re.sub(r"(可以|可不可以|能不能|能否|帮我|替我|把|一下|一下子|本地的|本地|应用程序|应用|软件|程序)", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"(打开|启动|运行|launch|open)", "", normalized, flags=re.IGNORECASE)
    normalized = normalized.strip("，。；！？,.!? ：:　 ")
    return normalized or _mapping_text(
        resolve_prompt_spec_mapping("surface_reply.system_skill.reply_templates"),
        "default_app_name",
        render_target_lang=render_target_lang,
    )


_SYSTEM_RESULT_SKILL_IDS = {
    "system_info",
    "system_resource_snapshot",
    "process_resource_top",
    "resource_pressure_analysis",
    "resource_manager_status",
    "resource_release",
    "process_list",
    "network_status",
    "open_app",
}


def _skill_result_detail(ctx: SurfaceSubAgentContext) -> dict[str, Any]:
    payload = _mapping_from_any(ctx.payload)
    result = _mapping_from_any(payload.get("result"))
    detail = result.get("detail")
    if isinstance(detail, Mapping):
        return dict(detail)
    payload_detail = payload.get("detail")
    if isinstance(payload_detail, Mapping):
        return dict(payload_detail)
    return result


def _format_percent(value: Any) -> str:
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "-"


def _format_gb(value: Any) -> str:
    try:
        return f"{float(value):.2f}GB"
    except (TypeError, ValueError):
        return "-"


def _format_runtime_capability(value: Any) -> str:
    status = str(_mapping_from_any(value).get("status") or "UNKNOWN").strip().upper()
    return {
        "READY": "可用",
        "UP": "可用",
        "DOWN": "不可用",
        "DEGRADED": "降级",
        "DISABLED": "已关闭",
        "DEFER": "建议延后",
        "CAUTION": "谨慎执行",
        "UNKNOWN": "未知",
    }.get(status, status or "未知")


def _build_system_info_vitals_reply(
    detail: dict[str, Any],
    *,
    render_target_lang: str = "zh",
) -> str | None:
    vitals = _mapping_from_any(detail.get("runtime_vitals"))
    if not vitals:
        return None
    resources = _mapping_from_any(vitals.get("resource_pressure"))
    capabilities = _mapping_from_any(vitals.get("capabilities"))
    gateway = _mapping_from_any(vitals.get("gateway"))
    qq_onebot = _mapping_from_any(gateway.get("qq_onebot"))
    issues = list(vitals.get("issues") or []) if isinstance(vitals.get("issues"), list) else []
    recommendations = (
        list(vitals.get("recommendations") or [])
        if isinstance(vitals.get("recommendations"), list)
        else []
    )

    status = str(vitals.get("status") or "UNKNOWN").strip().upper()
    headline = str(vitals.get("headline") or "").strip()
    if _normalize_template_lang(render_target_lang) == "ja":
        prefix = "システム体調診断"
    else:
        prefix = "体征诊断"

    cpu_text = _format_percent(resources.get("cpu_percent"))
    mem_text = _format_percent(resources.get("memory_percent"))
    disk_text = _format_percent(resources.get("disk_percent"))
    resource_status = str(resources.get("status") or "UNKNOWN").strip().upper()

    capability_parts = [
        f"QQ通讯{_format_runtime_capability(capabilities.get('qq_communication'))}",
        f"工具执行{_format_runtime_capability(capabilities.get('tool_execution'))}",
        f"记忆召回{_format_runtime_capability(capabilities.get('memory_recall'))}",
        f"TTS{_format_runtime_capability(capabilities.get('voice_tts'))}",
        f"VTS{_format_runtime_capability(capabilities.get('avatar_vts'))}",
    ]
    qq_status = str(qq_onebot.get("status") or "").strip().upper()
    if qq_status and qq_status not in {"UNKNOWN", "SKIPPED"}:
        capability_parts.insert(0, f"OneBot{_format_runtime_capability(qq_onebot)}")

    issue_parts: list[str] = []
    for raw_issue in issues[:3]:
        issue = _mapping_from_any(raw_issue)
        component = str(issue.get("component") or "组件").strip()
        status_text = str(issue.get("status") or "").strip()
        detail_text = str(issue.get("detail") or "").strip()
        if detail_text:
            issue_parts.append(f"{component}={status_text}（{_build_reply_preview(detail_text, limit=48)}）")
        else:
            issue_parts.append(f"{component}={status_text}")

    rec_text = ""
    if recommendations:
        rec_text = f"建议：{_build_reply_preview(str(recommendations[0]), limit=90)}"

    parts = [
        f"{prefix}：{status}，{headline or '运行状态已读取'}",
        "能力：" + "；".join(capability_parts),
        f"资源：CPU {cpu_text}，内存 {mem_text}，磁盘 {disk_text}，压力 {resource_status}",
    ]
    if issue_parts:
        parts.append("问题：" + "；".join(issue_parts))
    if rec_text:
        parts.append(rec_text)

    return "。".join(part.rstrip("。") for part in parts if part).rstrip("。") + "。"


def _build_system_info_reply(ctx: SurfaceSubAgentContext, *, render_target_lang: str = "zh") -> str | None:
    detail = _skill_result_detail(ctx)
    if not detail:
        return None
    vitals_reply = _build_system_info_vitals_reply(detail, render_target_lang=render_target_lang)
    if vitals_reply:
        return vitals_reply
    platform = _mapping_from_any(detail.get("platform"))
    cpu = _mapping_from_any(detail.get("cpu"))
    memory = _mapping_from_any(detail.get("memory"))
    disk = _mapping_from_any(detail.get("disk"))
    parts: list[str] = []
    system_label = " ".join(
        item for item in (
            str(platform.get("system") or "").strip(),
            str(platform.get("release") or "").strip(),
            str(platform.get("machine") or "").strip(),
        )
        if item
    )
    if system_label:
        parts.append(system_label)
    if cpu:
        logical = cpu.get("count_logical")
        physical = cpu.get("count_physical")
        core_text = ""
        if physical or logical:
            core_text = f"（{physical or '-'}核/{logical or '-'}线程）"
        parts.append(f"CPU {_format_percent(cpu.get('percent'))}{core_text}")
    if memory:
        parts.append(
            f"内存 {_format_percent(memory.get('percent'))}"
            f"（可用 {_format_gb(memory.get('available_gb'))}/总 {_format_gb(memory.get('total_gb'))}）"
        )
    if disk:
        parts.append(
            f"磁盘 {_format_percent(disk.get('percent'))}"
            f"（空闲 {_format_gb(disk.get('free_gb'))}/总 {_format_gb(disk.get('total_gb'))}）"
        )
    if not parts:
        return None
    if _normalize_template_lang(render_target_lang) == "ja":
        return "システム状態を確認したよ：" + "；".join(parts) + "。"
    return "系统状态查到了：" + "；".join(parts) + "。"


def _format_mb(value: Any) -> str:
    try:
        return f"{float(value):.0f}MB"
    except (TypeError, ValueError):
        return "-"


def _format_process_resource_preview(item: Any) -> str:
    row = _mapping_from_any(item)
    name = str(row.get("name") or "process").strip()
    pid = row.get("pid")
    mem = _format_mb(row.get("memory_mb"))
    vram = _format_mb(row.get("vram_mb"))
    cpu = _format_percent(row.get("cpu_percent"))
    role = str(row.get("role") or "").strip()
    suffix = f"，{role}" if role else ""
    return f"{name}(PID {pid or '-'}，CPU {cpu}，内存 {mem}，显存 {vram}{suffix})"


def _build_system_resource_snapshot_reply(ctx: SurfaceSubAgentContext) -> str | None:
    detail = _skill_result_detail(ctx)
    if not detail:
        return None
    snapshot = _mapping_from_any(detail.get("snapshot"))
    pressure = _mapping_from_any(detail.get("pressure"))
    memory = _mapping_from_any(snapshot.get("memory"))
    cpu = _mapping_from_any(snapshot.get("cpu"))
    gpu = _mapping_from_any(snapshot.get("gpu"))
    gpu_summary = _mapping_from_any(gpu.get("summary"))
    process_top = _mapping_from_any(detail.get("process_top"))
    processes = list(process_top.get("processes") or []) if isinstance(process_top.get("processes"), list) else []
    recommendations = list(detail.get("recommendations") or []) if isinstance(detail.get("recommendations"), list) else []

    parts = [
        f"整机资源快照：压力 {str(pressure.get('status') or 'UNKNOWN').upper()}",
        f"CPU {_format_percent(cpu.get('percent'))}",
        (
            f"内存 {_format_percent(memory.get('percent'))}"
            f"（可用 {_format_gb(memory.get('available_gb'))}/总 {_format_gb(memory.get('total_gb'))}）"
        ),
    ]
    if gpu_summary:
        parts.append(
            f"GPU {_format_percent(gpu_summary.get('utilization_percent'))}"
            f"，显存 {_format_mb(gpu_summary.get('memory_used_mb'))}/{_format_mb(gpu_summary.get('memory_total_mb'))}"
        )
    if processes:
        parts.append("热点：" + "；".join(_format_process_resource_preview(item) for item in processes[:4]))
    if recommendations:
        parts.append("建议：" + _build_reply_preview(str(recommendations[0]), limit=90))
    return "。".join(part.rstrip("。") for part in parts if part).rstrip("。") + "。"


def _build_process_resource_top_reply(ctx: SurfaceSubAgentContext) -> str | None:
    detail = _skill_result_detail(ctx)
    processes = list(detail.get("processes") or []) if isinstance(detail.get("processes"), list) else []
    if not processes:
        return None
    sort_by = str(detail.get("sort_by") or "memory").strip()
    preview = "；".join(_format_process_resource_preview(item) for item in processes[:5])
    return f"资源热点进程查到了（按 {sort_by}）：{preview}。"


def _build_resource_pressure_analysis_reply(ctx: SurfaceSubAgentContext) -> str | None:
    detail = _skill_result_detail(ctx)
    if not detail:
        return None
    summary = str(detail.get("summary") or "").strip()
    hypotheses = list(detail.get("hypotheses") or []) if isinstance(detail.get("hypotheses"), list) else []
    actions = _mapping_from_any(detail.get("actions"))
    parts: list[str] = []
    if summary:
        parts.append(summary)
    if hypotheses:
        previews = []
        for item in hypotheses[:3]:
            row = _mapping_from_any(item)
            title = str(row.get("title") or "").strip()
            confidence = row.get("confidence")
            severity = str(row.get("severity") or "").strip()
            if title:
                previews.append(f"{title}（{severity}，置信 {confidence}）")
        if previews:
            parts.append("主要根因：" + "；".join(previews))
    next_steps = list(actions.get("next_steps") or []) if isinstance(actions.get("next_steps"), list) else []
    if next_steps:
        parts.append("下一步：" + _build_reply_preview(str(next_steps[0]), limit=100))
    do_not_touch = list(actions.get("do_not_touch") or []) if isinstance(actions.get("do_not_touch"), list) else []
    if do_not_touch:
        parts.append("不可自动动：" + _build_reply_preview("；".join(str(item) for item in do_not_touch[:3]), limit=110))
    if not parts:
        return None
    return "。".join(part.rstrip("。") for part in parts if part).rstrip("。") + "。"


def _build_resource_manager_status_reply(ctx: SurfaceSubAgentContext) -> str | None:
    detail = _skill_result_detail(ctx)
    if not detail:
        return None
    if not bool(detail.get("registered")):
        return "整机资源管家当前没有注册到 Host 进程里。"
    running = "运行中" if bool(detail.get("running")) else "未运行"
    decision = _mapping_from_any(detail.get("last_decision"))
    snapshot = _mapping_from_any(detail.get("last_snapshot"))
    pressure = _mapping_from_any(snapshot.get("pressure"))
    summary = str(decision.get("summary") or "").strip()
    if summary:
        summary = "，最近判断：" + _build_reply_preview(summary, limit=90)
    return (
        f"整机资源管家{running}：采样间隔 {detail.get('interval_s')}s，"
        f"LLM分析={'开' if bool(detail.get('llm_enabled')) else '关'}，"
        f"自动GC={'开' if bool(detail.get('auto_gc_enabled')) else '关'}，"
        f"自动工作集回收={'开' if bool(detail.get('auto_trim_enabled')) else '关'}，"
        f"最近压力 {str(pressure.get('status') or 'UNKNOWN').upper()}{summary}。"
    )


def _build_resource_release_reply(ctx: SurfaceSubAgentContext) -> str | None:
    detail = _skill_result_detail(ctx)
    if not detail:
        return None
    if bool(detail.get("dry_run")):
        candidates = list(detail.get("release_candidates") or []) if isinstance(detail.get("release_candidates"), list) else []
        recommendations = list(detail.get("recommendations") or []) if isinstance(detail.get("recommendations"), list) else []
        preview = "；".join(
            f"{_mapping_from_any(item).get('name')}(PID {_mapping_from_any(item).get('pid')}，{_mapping_from_any(item).get('recommended_action')})"
            for item in candidates[:4]
        )
        base = "资源释放预案已生成：默认不会自动强杀进程"
        if preview:
            base += "，候选：" + preview
        if recommendations:
            base += "。建议：" + _build_reply_preview(str(recommendations[0]), limit=90)
        return base.rstrip("。") + "。"
    mode = str(detail.get("mode") or "").strip()
    if bool(detail.get("success")):
        return f"资源释放动作已执行：mode={mode}，结果 {detail.get('trimmed', detail.get('collected', 'OK'))}。"
    return f"资源释放未执行：{detail.get('reason_code') or detail.get('detail') or '需要确认'}。"


def _build_process_list_reply(ctx: SurfaceSubAgentContext, *, render_target_lang: str = "zh") -> str | None:
    detail = _skill_result_detail(ctx)
    processes = list(detail.get("processes") or []) if isinstance(detail.get("processes"), list) else []
    count = detail.get("count")
    if not processes and count is None:
        return None
    previews: list[str] = []
    for item in processes[:5]:
        row = _mapping_from_any(item)
        name = str(row.get("name") or "process").strip()
        pid = row.get("pid")
        cpu = _format_percent(row.get("cpu_percent"))
        mem = _format_percent(row.get("memory_percent"))
        previews.append(f"{name}(PID {pid or '-'}，CPU {cpu}，内存 {mem})")
    total = count if count is not None else len(processes)
    if _normalize_template_lang(render_target_lang) == "ja":
        intro = f"プロセス一覧を確認したよ：{total} 件"
    else:
        intro = f"进程列表查到了：共 {total} 个"
    if previews:
        intro += "，前几项：" + "；".join(previews)
    return intro + "。"


def _build_network_status_reply(ctx: SurfaceSubAgentContext) -> str | None:
    detail = _skill_result_detail(ctx)
    if not detail:
        return None
    host = str(detail.get("host") or "").strip() or "目标主机"
    port = detail.get("port")
    reachable = detail.get("reachable")
    if reachable is None and not host:
        return None
    endpoint = f"{host}:{port}" if port else host
    if bool(reachable):
        base = f"网络检测完成：{endpoint} 可达"
    else:
        base = f"网络检测完成：{endpoint} 不可达"
    error = str(detail.get("error") or "").strip()
    if error and not bool(reachable):
        base += f"，原因：{error}"
    public_ip = str(detail.get("public_ip") or "").strip()
    if public_ip:
        base += f"，公网 IP {public_ip}"
    return base + "。"


def _build_system_skill_reply(ctx: SurfaceSubAgentContext, *, render_target_lang: str = "zh") -> str | None:
    agent_id = str(ctx.agent_id or "").strip()
    if agent_id == "system_info":
        return _build_system_info_reply(ctx, render_target_lang=render_target_lang)
    if agent_id == "system_resource_snapshot":
        return _build_system_resource_snapshot_reply(ctx)
    if agent_id == "process_resource_top":
        return _build_process_resource_top_reply(ctx)
    if agent_id == "resource_pressure_analysis":
        return _build_resource_pressure_analysis_reply(ctx)
    if agent_id == "resource_manager_status":
        return _build_resource_manager_status_reply(ctx)
    if agent_id == "resource_release":
        return _build_resource_release_reply(ctx)
    if agent_id == "process_list":
        return _build_process_list_reply(ctx, render_target_lang=render_target_lang)
    if agent_id == "network_status":
        return _build_network_status_reply(ctx)
    if agent_id != "open_app":
        return None
    templates = resolve_prompt_spec_mapping("surface_reply.system_skill.reply_templates")
    payload = dict(ctx.payload or {})
    result = dict(payload.get("result") or {})
    detail_source = result.get("detail") or payload.get("detail") or result
    detail = dict(detail_source or {}) if isinstance(detail_source, Mapping) else {}
    success = bool(detail.get("success", ctx.success))
    app_name = str(
        detail.get("app_name")
        or payload.get("app_name")
        or _infer_open_app_name(ctx.task, render_target_lang=render_target_lang)
    ).strip() or _mapping_text(templates, "default_app_name", render_target_lang=render_target_lang)
    message = str(
        detail.get("detail")
        or detail.get("message")
        or result.get("error")
        or ctx.error
        or ctx.result_text
        or ""
    ).strip()
    if success:
        return _mapping_text(templates, "open_app_success", render_target_lang=render_target_lang, name=app_name)
    reply = _mapping_text(templates, "open_app_failure", render_target_lang=render_target_lang, name=app_name)
    generic_failure_messages = {
        "抱歉，我这边没能把这个应用打开。",
        "I couldn't open the app.",
    }
    if message and message not in generic_failure_messages:
        reply += _mapping_text(
            templates,
            "open_app_failure_reason",
            render_target_lang=render_target_lang,
            message=str(message).rstrip("。.!！?？"),
        )
    return reply


def _looks_like_internal_sub_agent_result(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    if normalized.startswith("[ReAct"):
        return True
    if normalized.startswith("ReAct 已达到最大步数限制"):
        return True
    if normalized.startswith("[RepairAgent"):
        return True
    return any(marker in normalized for marker in ("stopped_reason=", "agent_id=", "repair_agent_trace"))


def _should_preserve_sub_agent_result_for_user(*, user_message: str, agent_id: str) -> bool:
    normalized_agent_id = str(agent_id or "").strip()
    if normalized_agent_id != "intent_agent":
        return True
    return not is_relational_chat_message(user_message)


def _build_sub_agent_result_preserving_fallback_reply(ctx: SurfaceSubAgentContext | None, *, render_target_lang: str, user_message: str = "") -> str | None:
    if ctx is None:
        return None
    if not bool(ctx.success) or str(ctx.error or "").strip():
        return None
    agent_id = str(ctx.agent_id or "").strip()
    if not _should_preserve_sub_agent_result_for_user(user_message=user_message, agent_id=agent_id):
        return None
    if agent_id in _SYSTEM_RESULT_SKILL_IDS:
        return _build_system_skill_reply(ctx, render_target_lang=render_target_lang)
    if agent_id == "plan_execution":
        return _build_plan_execution_reply_cn(ctx, render_target_lang=render_target_lang)
    result_text = _strip_internal_delegate_markers(str(ctx.result_text or ""))
    result_text = _extract_visible_text(result_text, render_target_lang=render_target_lang)
    result_text = re.sub(r"\n{3,}", "\n\n", result_text).strip()
    if not result_text or _looks_like_internal_sub_agent_result(result_text):
        return None
    return result_text


def _build_deterministic_sub_agent_reply(ctx: SurfaceSubAgentContext | None, *, render_target_lang: str, user_message: str = "") -> str | None:
    if ctx is None:
        return None
    agent_id = str(ctx.agent_id or "").strip()
    result_text = str(ctx.result_text or "").strip()
    if agent_id in _SYSTEM_RESULT_SKILL_IDS:
        return _build_system_skill_reply(ctx, render_target_lang=render_target_lang)
    if agent_id == "plan_execution":
        return _build_plan_execution_reply_cn(ctx, render_target_lang=render_target_lang)
    if is_agent_runtime_skill(agent_id) or agent_id in {"translate_agent", "intent_agent"}:
        return _build_sub_agent_result_preserving_fallback_reply(ctx, render_target_lang=render_target_lang, user_message=user_message)
    if agent_id in {"recent_turn_direct", "task_progress_direct"}:
        return result_text or None
    return None


# ---------------------------------------------------------------------------
# LLM 输出提取
# ---------------------------------------------------------------------------
def _extract_visible_text(raw: str, *, render_target_lang: str = "zh") -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    # Some upstream providers may leak chain-of-thought style blocks when they
    # degrade or retry. Strip them at the final visible-text boundary too.
    text = re.sub(r"(?is)<think\b[^>]*>.*?</think>", "", text).strip()
    text = re.sub(r"(?is)^<think\b[^>]*>.*", "", text).strip()
    if not text:
        return ""
    if text.startswith("```"):
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL | re.IGNORECASE)
        if fenced:
            text = str(fenced.group(1) or "").strip()
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                narrative = parsed.get("narrative")
                if isinstance(narrative, dict):
                    lang = _normalize_template_lang(render_target_lang)
                    if lang == "ja":
                        return str(narrative.get("text_ja") or narrative.get("text") or narrative.get("text_zh") or "").strip()
                    return str(narrative.get("text_zh") or narrative.get("text") or narrative.get("text_ja") or "").strip()
                for key in ("final_answer", "result", "output", "text", "message"):
                    value = parsed.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
        except (json.JSONDecodeError, ValueError):
            pass
    return text
