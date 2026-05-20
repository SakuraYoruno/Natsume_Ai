"""提示词构建模块。

负责非主 chat 路径的 LLM 提示词渲染逻辑。
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Mapping

from host_brain.app.chat_runtime_input import (
    get_chat_runtime_prompt_context,
    get_chat_runtime_prompt_context_validation,
    get_chat_runtime_emotion_snapshot,
    get_chat_runtime_emotion_state,
    resolve_prompt_context_style_row,
    get_chat_runtime_render_target_lang,
    get_chat_runtime_retrieval,
    get_chat_runtime_skill_manifest,
    get_chat_runtime_user_message,
)
from host_brain.app.prompt_template_store import (
    resolve_prompt_template_json,
    resolve_prompt_template_json_merged,
    resolve_prompt_template_text,
)

# ---------------------------------------------------------------------------
# 场景常量
# ---------------------------------------------------------------------------
CHAT_DIRECT_NODE_ID = "chat_direct_ja"
CHAT_RENDER_NODE_ID = "renderer_ja_c_style"
CHAT_INTENT_SOURCE_NODE_ID = "core_reasoner_primary"
PERSONA_RENDERER_NODE_ID = "persona_renderer"  # Phase XIV：拟人化渲染节点

SCENE_CHAT = "CHAT"
SCENE_EXEC_INTENT = "EXEC_INTENT"
SCENE_WORK_TRANSLATE = "WORK_TRANSLATE"
SCENE_WORK_CODE = "WORK_CODE"
SCENE_VISION_UI = "VISION_UI"
SCENE_GATEWAY = "GATEWAY"

# ---------------------------------------------------------------------------
# 动态人设补丁（季节 / 节日 / 当日情绪）
# ---------------------------------------------------------------------------

def _build_character_dynamic_patch(
    *,
    emotion_tag: str = "",
    is_holiday: bool = False,
    holiday_name: str = "",
    render_target_lang: str = "zh",
) -> str:
    """生成角色人设的动态补丁文本，追加在静态人设之后。

    补丁内容：
      1. 季节感知（根据当前月份推断）
      2. 节日感知（由外部传入 is_holiday / holiday_name）
      3. 当日情绪感知（根据 Natsume 当前情感标签）

    Returns:
        补丁文本（可能为空字符串）
    """
    import datetime as _dt

    templates = _load_template_mapping(
        "prompt_builder.character_dynamic_patch.templates",
        render_target_lang=render_target_lang,
    )
    season_templates = _mapping_section(templates, "seasons")
    holiday_templates = _mapping_section(templates, "holiday_patches")
    emotion_templates = _mapping_section(templates, "emotion_patches")
    parts: list[str] = []

    # ── 1. 季节感知 ──────────────────────────────────────────────────────
    try:
        month = _dt.datetime.now().month
        if month in (12, 1, 2):
            parts.append(str(season_templates.get("winter") or ""))
        elif month in (3, 4, 5):
            parts.append(str(season_templates.get("spring") or ""))
        elif month in (6, 7, 8):
            parts.append(str(season_templates.get("summer") or ""))
        elif month in (9, 10, 11):
            parts.append(str(season_templates.get("autumn") or ""))
    except Exception:
        pass

    # ── 2. 节日感知 ──────────────────────────────────────────────────────
    if is_holiday and holiday_name:
        patch = str(holiday_templates.get(holiday_name) or "").strip()
        if patch:
            parts.append(patch)
        else:
            parts.append(
                _mapping_text(
                    templates,
                    "holiday_fallback",
                    holiday_name=holiday_name,
                )
            )

    # ── 3. 当日情绪感知 ──────────────────────────────────────────────────
    tag = str(emotion_tag or "").strip().upper()
    if tag in emotion_templates:
        parts.append(str(emotion_templates.get(tag) or ""))

    parts = [str(item).strip() for item in parts if str(item or "").strip()]
    if not parts:
        return ""

    return "\n\n" + str(templates.get("title") or "【今日状态补丁】") + "\n" + "\n".join(f"- {p}" for p in parts)


def _build_current_local_time_context(
    *,
    render_target_lang: str = "zh",
    line_kind: str = "full",
) -> str:
    import datetime as _dt

    templates = _load_template_mapping("prompt_builder.current_time_context.templates")
    lang_key = _normalize_template_lang(render_target_lang)
    localized = templates.get(lang_key) if isinstance(templates.get(lang_key), Mapping) else {}
    localized = dict(localized) if isinstance(localized, Mapping) else {}
    daypart_mapping = localized.get("daypart") if isinstance(localized.get("daypart"), Mapping) else {}
    daypart_mapping = dict(daypart_mapping) if isinstance(daypart_mapping, Mapping) else {}

    now = _dt.datetime.now().astimezone()
    hour = int(now.hour)
    if 5 <= hour < 11:
        bucket = "morning"
    elif 11 <= hour < 18:
        bucket = "daytime"
    elif 18 <= hour < 24:
        bucket = "evening"
    else:
        bucket = "late_night"

    current_local_time = now.strftime("%Y-%m-%d %H:%M")
    time_of_day_label = str(daypart_mapping.get(bucket) or bucket).strip()
    template_text = str(localized.get(f"{line_kind}_line") or "").strip()
    if not template_text:
        return ""
    return _format_template_text(
        template_text,
        current_local_time=current_local_time,
        time_of_day_label=time_of_day_label,
    )


# ---------------------------------------------------------------------------
# Ref #70: 线程局部变量 — 存储最近一次注入检测结果，供调用方读取并写入 audit_tags
# ---------------------------------------------------------------------------
_injection_meta_local = threading.local()


def get_last_injection_meta() -> dict[str, Any]:
    """获取当前线程最近一次 MemoryGuard 注入检测结果（无则返回空字典）。"""
    return getattr(_injection_meta_local, "meta", {})


def _set_injection_meta(meta: dict[str, Any]) -> None:
    """内部：将注入检测结果写入线程局部变量（同时写入 utils 模块，保持一致性）。"""
    _injection_meta_local.meta = meta
    try:
        from host_brain.app.prompt_builder_utils import _injection_meta_local as _utils_local  # noqa: PLC0415
        _utils_local.meta = meta
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 文本工具函数
# ---------------------------------------------------------------------------
def _truncate_for_prompt(value: Any, *, limit: int = 240) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 3)]}..."


def _get_prompt_evidence_limit(*, default: int = 8) -> int:
    try:
        value = int(str(os.environ.get("HOST_RETR_EVIDENCE_ITEMS_MAX") or str(default)).strip())
    except ValueError:
        value = int(default)
    return max(1, min(value, 12))


def _contains_kana(text: str) -> bool:
    for ch in str(text or ""):
        cp = ord(ch)
        if 0x3040 <= cp <= 0x30FF:
            return True
    return False


def _contains_cjk_ideograph(text: str) -> bool:
    for ch in str(text or ""):
        cp = ord(ch)
        if 0x4E00 <= cp <= 0x9FFF:
            return True
    return False


def _contains_cjk_or_kana(text: str) -> bool:
    return _contains_cjk_ideograph(text) or _contains_kana(text)


def repair_utf8_latin1_mojibake(text: str) -> str:
    raw = str(text or "")
    if not raw:
        return raw
    if _contains_cjk_or_kana(raw):
        return raw
    try:
        repaired = raw.encode("latin-1").decode("utf-8")
    except UnicodeError:
        return raw
    if _contains_cjk_or_kana(repaired):
        return repaired
    return raw


class _SafeTemplateDict(dict[str, str]):
    def __missing__(self, key: object) -> str:
        return "{" + str(key) + "}"


def _format_template_text(template_text: Any, **kwargs: Any) -> str:
    text = str(template_text or "")
    if not kwargs:
        return text
    payload = _SafeTemplateDict(
        {
            str(key): "" if value is None else str(value)
            for key, value in kwargs.items()
        }
    )
    try:
        return text.format_map(payload)
    except Exception:
        return text


def _normalize_template_lang(render_target_lang: str) -> str:
    return "ja" if str(render_target_lang or "").strip().lower() == "ja" else "zh"


def _merge_localized_mapping(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    merged = {str(key): value for key, value in base.items()}
    for key, value in overlay.items():
        key_text = str(key)
        if key_text in {"zh", "ja"}:
            continue
        current = merged.get(key_text)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            merged[key_text] = _merge_localized_mapping(current, value)
        else:
            merged[key_text] = value
    return merged


def _resolve_localized_template_payload(template_id: str, *, render_target_lang: str) -> Any:
    lang_key = _normalize_template_lang(render_target_lang)
    raw = resolve_prompt_template_json_merged(template_id)
    if isinstance(raw, Mapping):
        localized = raw.get(lang_key)
        if localized is not None:
            if isinstance(localized, Mapping):
                return _merge_localized_mapping(localized, raw)
            return localized
    return raw


def _load_template_mapping(
    template_id: str,
    *,
    render_target_lang: str | None = None,
) -> dict[str, Any]:
    raw = (
        _resolve_localized_template_payload(
            template_id,
            render_target_lang=render_target_lang or "zh",
        )
        if render_target_lang
        else resolve_prompt_template_json_merged(template_id)
    )
    return dict(raw) if isinstance(raw, Mapping) else {}


def _mapping_section(
    mapping: Mapping[str, Any],
    key: str,
    *,
    render_target_lang: str | None = None,
) -> dict[str, Any]:
    raw = mapping.get(key)
    if isinstance(raw, Mapping) and render_target_lang:
        localized = raw.get(_normalize_template_lang(render_target_lang))
        if isinstance(localized, Mapping):
            raw = localized
    return dict(raw) if isinstance(raw, Mapping) else {}


def _mapping_text(
    mapping: Mapping[str, Any],
    key: str,
    *,
    default: str = "",
    render_target_lang: str | None = None,
    **kwargs: Any,
) -> str:
    value = mapping.get(key, default)
    if isinstance(value, Mapping) and render_target_lang:
        lang_key = _normalize_template_lang(render_target_lang)
        value = value.get(lang_key, value.get("zh", default))
    return _format_template_text(value, **kwargs)


def _mapping_list(
    mapping: Mapping[str, Any],
    key: str,
    *,
    render_target_lang: str | None = None,
) -> list[str]:
    raw = mapping.get(key)
    if isinstance(raw, Mapping) and render_target_lang:
        raw = raw.get(_normalize_template_lang(render_target_lang), raw.get("zh"))
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


# ---------------------------------------------------------------------------
# Phase XV：双轨语言常量
# ---------------------------------------------------------------------------
# 文字通道永远中文（用户看的文字，中文是母语）
TEXT_OUTPUT_LANG: str = "zh"
# 语音通道默认中文；日语仅作为显式日文内容的兼容声线。
TTS_INPUT_LANG: str = "zh"


def is_dual_lang_enabled() -> bool:
    """Phase XV P6：双轨语言输出永久启用。

    文字通道与语音通道都以中文人格表达为主。
    text_ja 只作为旧字段保留，新的 TTS 路由优先使用 text_zh。
    """
    return True


def infer_render_target_lang(*, user_message: str, core_narrative: str = "") -> str:
    """ModelPlane §0.4: 二次式输出强制日文渲染。"""
    override = str(os.environ.get("HOST_RENDER_TARGET_LANG") or "").strip().lower()
    if override in {"zh", "ja"}:
        return override
    combined = f"{str(user_message or '')}\n{str(core_narrative or '')}".strip().lower()
    if any(token in combined for token in ("中文", "汉语", "简体", "chinese", "mandarin")):
        return "zh"
    if any(token in combined for token in ("日文", "日语", "日本語", "japanese")):
        return "ja"
    if _contains_kana(combined):
        return "ja"
    if _contains_cjk_ideograph(combined):
        return "zh"
    return "zh"


def _contains_any_token(text: str, tokens: tuple[str, ...]) -> bool:
    normalized = str(text or "").lower()
    return any(token in normalized for token in tokens)


def _build_direct_response_priority(
    *,
    user_message: str,
    render_target_lang: str,
    follow_up_hint: str = "",
    bond_stage: str = "",
) -> str:
    from host_brain.app.prompt_builder_utils import _build_direct_response_priority as _build_direct_response_priority_shared  # noqa: PLC0415

    return _build_direct_response_priority_shared(
        user_message=user_message,
        render_target_lang=render_target_lang,
        follow_up_hint=follow_up_hint,
        bond_stage=bond_stage,
    )


# ---------------------------------------------------------------------------
# 节点角色指令
# ---------------------------------------------------------------------------
def _build_node_role_instruction(
    node_id: str,
    *,
    render_target_lang: str = "ja",
    skill_manifest: list[dict] | None = None,
) -> str:
    resolved_node_id = str(node_id or "").strip()
    lang_key = "zh" if str(render_target_lang).strip().lower() == "zh" else "ja"
    templates = _load_template_mapping("prompt_builder.node_role.instructions")
    if resolved_node_id == CHAT_DIRECT_NODE_ID:
        return _mapping_text(_mapping_section(templates, "chat_direct"), lang_key)
    if resolved_node_id == CHAT_RENDER_NODE_ID:
        return _mapping_text(_mapping_section(templates, "renderer"), lang_key)

    # Phase XIV：拟人化渲染节点（父子级 Agent 架构）
    if resolved_node_id == PERSONA_RENDERER_NODE_ID:
        return _mapping_text(_mapping_section(templates, "persona_renderer"), lang_key)

    # Phase XIV v4：Cortex Orchestrator 使用专属 role instruction block.
    if resolved_node_id == "cortex_orchestrator":
        return _mapping_text(_mapping_section(templates, "cortex_orchestrator"), lang_key)

    role_instructions = _mapping_section(templates, "generic_roles")
    base_instruction = str(
        role_instructions.get(
            resolved_node_id,
            _mapping_text(
                templates,
                "generic_default",
            ),
        )
    )

    # 为 tool_planner 节点追加 AVAILABLE_SKILLS 列表
    if resolved_node_id == "tool_planner" and skill_manifest:
        skill_lines: list[str] = []
        for entry in skill_manifest:
            name = str(entry.get("name") or "")
            desc = str(entry.get("description") or "")
            risk = int(entry.get("risk_level") or 1)
            confirm = bool(entry.get("require_confirm", False))
            line = _mapping_text(
                templates,
                "tool_planner_skill_line",
                name=name,
                desc=desc,
                risk=risk,
                confirm=confirm,
            )
            skill_lines.append(line)
        skills_block = "\n".join(skill_lines)
        base_instruction = (
            f"{base_instruction}\n\n"
            f"{_mapping_text(templates, 'tool_planner_skills_header')}\n{skills_block}"
        )

    return base_instruction


# ---------------------------------------------------------------------------
# JSON 提取工具
# ---------------------------------------------------------------------------
def extract_json_object_from_text(raw_text: str) -> dict[str, Any]:
    text = str(raw_text or "").strip()
    if not text:
        raise RuntimeError("provider returned empty content")

    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    decoder = json.JSONDecoder()
    try:
        parsed, _ = decoder.raw_decode(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    first = text.find("{")
    last = text.rfind("}")
    if first >= 0 and last > first:
        snippet = text[first : last + 1]
        parsed = json.loads(snippet)
        if isinstance(parsed, dict):
            return parsed
    raise RuntimeError("provider content does not contain a valid json object")


def coerce_gateway_payload_json_text(raw_text: str) -> str:
    parsed = extract_json_object_from_text(raw_text)
    narrative_node = parsed.get("narrative")
    narrative_text = ""
    if isinstance(narrative_node, dict):
        narrative_text = str(narrative_node.get("text") or "").strip()
    elif isinstance(narrative_node, str):
        narrative_text = narrative_node.strip()
    if not narrative_text:
        narrative_text = str(parsed.get("text") or "").strip()
    narrative_text = repair_utf8_latin1_mojibake(narrative_text)
    if not narrative_text:
        raise RuntimeError("provider output missing narrative.text")

    intent_draft = parsed.get("intent_draft")
    if not isinstance(intent_draft, dict):
        intent_draft = {}
    meta = parsed.get("meta")
    if not isinstance(meta, dict):
        meta = {}
    safe_meta = dict(meta)
    safe_meta["draft_only"] = True

    contract_payload = {
        "narrative": {"text": narrative_text},
        "intent_draft": intent_draft,
        "meta": safe_meta,
    }
    return json.dumps(contract_payload, ensure_ascii=False, sort_keys=True)


def extract_langchain_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            text_value = item.get("text")
            if text_value is not None:
                parts.append(str(text_value))
        return "".join(parts)
    return str(content)


# ---------------------------------------------------------------------------
# 节点 Runtime Payload 提取
# ---------------------------------------------------------------------------
def extract_node_runtime_payload(runtime_payload: Mapping[str, Any], *, node_id: str) -> dict[str, Any]:
    node_root = runtime_payload.get(node_id)
    if not isinstance(node_root, Mapping):
        return {}
    payload_raw = node_root.get("payload")
    if isinstance(payload_raw, Mapping):
        return dict(payload_raw)
    return {}


def extract_node_narrative_text(runtime_payload: Mapping[str, Any], *, node_id: str) -> str:
    node_payload = extract_node_runtime_payload(runtime_payload, node_id=node_id)
    return extract_gateway_narrative(node_payload)


def extract_gateway_narrative(payload: dict[str, Any] | None) -> str:
    node = dict(payload or {})
    narrative = node.get("narrative")
    if isinstance(narrative, dict):
        text = repair_utf8_latin1_mojibake(str(narrative.get("text") or "").strip())
        if text:
            return text
    elif isinstance(narrative, str):
        text = repair_utf8_latin1_mojibake(narrative.strip())
        if text:
            return text
    return ""


# ---------------------------------------------------------------------------
# Host 生成提示词（简单版，无 prompt_context）
# ---------------------------------------------------------------------------
def render_host_generation_prompt(prompt_payload: dict[str, Any]) -> str:
    user_message = _truncate_for_prompt(prompt_payload.get("user_message"), limit=1000)
    injection_templates = _load_template_mapping("prompt_builder.injection_guard.templates")
    host_prompt_templates = _load_template_mapping("prompt_builder.host_generation.blocks")

    # ── Ref #64：MemoryGuard 提示词注入检测（简单版）────────────────────
    try:
        from host_brain.app.memory.memory_guard import MemoryGuard  # noqa: PLC0415
        _mg_h = MemoryGuard()
        _h_inj = _mg_h.check_prompt_injection(user_message)
        if bool(_h_inj.get("detected")) and str(_h_inj.get("risk_level") or "").upper() in ("HIGH", "CRITICAL"):
            _h_patterns = list(_h_inj.get("matched_patterns") or [])
            _alert_text = _mapping_text(
                injection_templates,
                "security_alert_template",
                risk_level=_h_inj.get("risk_level"),
                matched_patterns=_h_patterns[:3],
            )
            user_message = (
                f"{user_message}\n\n"
                f"{_alert_text}"
            )
        # Ref #70: 将注入检测结果写入线程局部变量
        _set_injection_meta({
            "mode": "ACTIVE",
            "detected": bool(_h_inj.get("detected", False)),
            "risk_level": str(_h_inj.get("risk_level") or "LOW").upper(),
            "matched_patterns": list(_h_inj.get("matched_patterns") or []),
        })
    except Exception:
        _set_injection_meta({"mode": "DEGRADED"})

    render_target_lang = str(prompt_payload.get("render_target_lang") or "").strip().lower()
    if render_target_lang not in {"zh", "ja"}:
        render_target_lang = infer_render_target_lang(user_message=user_message)

    retrieval_raw = prompt_payload.get("retrieval")
    retrieval = dict(retrieval_raw) if isinstance(retrieval_raw, dict) else {}
    retrieval_status = str(retrieval.get("status") or "UNKNOWN").strip() or "UNKNOWN"
    retrieval_reason = str(retrieval.get("reason_code") or "").strip() or "NONE"
    evidence_items = retrieval.get("evidence_items")
    rows = evidence_items if isinstance(evidence_items, list) else []
    evidence_lines: list[str] = []
    for row in rows[: _get_prompt_evidence_limit()]:
        if not isinstance(row, dict):
            continue
        evidence_ref = _truncate_for_prompt(row.get("evidence_ref") or row.get("source_ref") or "unknown", limit=80)
        snippet = _truncate_for_prompt(row.get("snippet"), limit=240)
        if not snippet:
            continue
        evidence_lines.append(f"- [{evidence_ref}] {snippet}")
    evidence_block = "\n".join(evidence_lines) if evidence_lines else _mapping_text(
        host_prompt_templates,
        "evidence_empty",
    )

    # Phase XV P6 后续修正：文字与语音都以中文人格表达为主。
    # text_ja 只保留为旧字段，避免旧解析器看到缺字段。
    language_rule = (
        "4. Output DUAL LANGUAGE: narrative must contain BOTH text_zh (Simplified Chinese, "
        "for text display and TTS voice synthesis) AND text_ja (legacy compatibility mirror, may be empty). "
        "Schema: {\"narrative\":{\"text_zh\":\"...\",\"text_ja\":\"...\"}, ...}. "
        "text_zh: concise spoken Simplified Chinese, TTS-friendly, 1-3 sentences. "
        "text_ja: only fill this when the user explicitly asks for Japanese."
    )

    host_rules = _mapping_list(host_prompt_templates, "rules")

    return (
        _mapping_text(host_prompt_templates, "intro")
        + _mapping_text(
            host_prompt_templates,
            "top_level_keys_instruction",
        )
        + _mapping_text(host_prompt_templates, "required_schema_title")
        + _mapping_text(
            host_prompt_templates,
            "required_schema",
        )
        + _mapping_text(host_prompt_templates, "rules_title")
        + "".join(f"{idx}. {rule}\n" for idx, rule in enumerate(host_rules, 1))
        + "\n"
        + _mapping_text(host_prompt_templates, "user_message_title")
        + f"{user_message}\n\n"
        + _mapping_text(
            host_prompt_templates,
            "retrieval_status_label",
            retrieval_status=retrieval_status,
        )
        + _mapping_text(
            host_prompt_templates,
            "retrieval_reason_label",
            retrieval_reason=retrieval_reason,
        )
        + _mapping_text(host_prompt_templates, "evidence_title")
        + f"{evidence_block}\n"
    )


# ---------------------------------------------------------------------------
# 节点生成提示词（完整版，含 prompt_context）
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 聚合导入（P2-10 拆分后向后兼容）
# ---------------------------------------------------------------------------
# render_node_generation_prompt 已移至 node_generation_prompt.py
from host_brain.app.node_generation_prompt import render_node_generation_prompt  # noqa: F401, E402

# 工具函数已移至 prompt_builder_utils.py，此处重新导出以保持向后兼容
from host_brain.app.prompt_builder_utils import (  # noqa: F401, E402
    CHAT_DIRECT_NODE_ID as _CHAT_DIRECT_NODE_ID_ALIAS,
    CHAT_RENDER_NODE_ID as _CHAT_RENDER_NODE_ID_ALIAS,
    CHAT_INTENT_SOURCE_NODE_ID as _CHAT_INTENT_SOURCE_NODE_ID_ALIAS,
    PERSONA_RENDERER_NODE_ID as _PERSONA_RENDERER_NODE_ID_ALIAS,
    get_last_injection_meta,
    extract_json_object_from_text,
    coerce_gateway_payload_json_text,
    extract_langchain_content_text,
    extract_node_runtime_payload,
    extract_node_narrative_text,
    extract_gateway_narrative,
    repair_utf8_latin1_mojibake,
    infer_render_target_lang,
    is_dual_lang_enabled,
    TEXT_OUTPUT_LANG,
    TTS_INPUT_LANG,
)
