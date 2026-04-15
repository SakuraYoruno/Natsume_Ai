"""prompt_builder_utils.py — Prompt 构建工具函数与常量。

拆分自 prompt_builder.py（P2-10）：
  - 场景常量
  - 文本工具函数（截断、语言检测、模板加载等）
  - 角色人设加载
  - 节点角色指令构建
  - JSON 提取工具
  - 节点 Runtime Payload 提取
  - 双轨语言常量与辅助函数
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Mapping

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
PERSONA_RENDERER_NODE_ID = "persona_renderer"

SCENE_CHAT = "CHAT"
SCENE_EXEC_INTENT = "EXEC_INTENT"
SCENE_WORK_TRANSLATE = "WORK_TRANSLATE"
SCENE_WORK_CODE = "WORK_CODE"
SCENE_VISION_UI = "VISION_UI"
SCENE_GATEWAY = "GATEWAY"

# ---------------------------------------------------------------------------
# Phase XV：双轨语言常量
# ---------------------------------------------------------------------------
TEXT_OUTPUT_LANG: str = "zh"
TTS_INPUT_LANG: str = "ja"


def is_dual_lang_enabled() -> bool:
    """Phase XV P6：双轨语言输出永久启用。"""
    return True


# ---------------------------------------------------------------------------
# 常驻角色人设缓存
# ---------------------------------------------------------------------------
_CHARACTER_PROFILE_CACHE: dict[str, str] = {}


def _load_character_profile(render_lang: str) -> str:
    """加载常驻角色人设文本（固定中文版）。"""
    cache_key = "zh"
    if cache_key in _CHARACTER_PROFILE_CACHE:
        return _CHARACTER_PROFILE_CACHE[cache_key]

    profile_path = Path(
        os.environ.get("NATSUME_CHARACTER_PROFILE_PATH")
        or str(Path(__file__).resolve().parent.parent / "canon" / "character" / "character_profile.json")
    )
    try:
        if profile_path.exists():
            data = json.loads(profile_path.read_text(encoding="utf-8"))
            text = str(data.get("instruction_cn") or "").strip()
            if len(text) > 100:
                _CHARACTER_PROFILE_CACHE[cache_key] = text
                return text
    except Exception:
        pass
    _CHARACTER_PROFILE_CACHE[cache_key] = ""
    return ""


# ---------------------------------------------------------------------------
# 动态人设补丁
# ---------------------------------------------------------------------------
def _build_character_dynamic_patch(
    *,
    emotion_tag: str = "",
    is_holiday: bool = False,
    holiday_name: str = "",
) -> str:
    """生成角色人设的动态补丁文本，追加在静态人设之后。"""
    import datetime as _dt

    templates = _load_template_mapping("prompt_builder.character_dynamic_patch.templates")
    season_templates = _mapping_section(templates, "seasons")
    holiday_templates = _mapping_section(templates, "holiday_patches")
    emotion_templates = _mapping_section(templates, "emotion_patches")
    parts: list[str] = []

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

    if is_holiday and holiday_name:
        patch = str(holiday_templates.get(holiday_name) or "").strip()
        if patch:
            parts.append(patch)
        else:
            parts.append(_mapping_text(templates, "holiday_fallback", holiday_name=holiday_name))

    tag = str(emotion_tag or "").strip().upper()
    if tag in emotion_templates:
        parts.append(str(emotion_templates.get(tag) or ""))

    parts = [str(item).strip() for item in parts if str(item or "").strip()]
    if not parts:
        return ""

    return "\n\n" + str(templates.get("title") or "【今日状态补丁】") + "\n" + "\n".join(f"- {p}" for p in parts)


# ---------------------------------------------------------------------------
# Ref #70: 线程局部变量 — 注入检测结果
# ---------------------------------------------------------------------------
_injection_meta_local = threading.local()


def get_last_injection_meta() -> dict[str, Any]:
    """获取当前线程最近一次 MemoryGuard 注入检测结果。"""
    return getattr(_injection_meta_local, "meta", {})


def _set_injection_meta(meta: dict[str, Any]) -> None:
    """内部：将注入检测结果写入线程局部变量。"""
    _injection_meta_local.meta = meta


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
        {str(key): "" if value is None else str(value) for key, value in kwargs.items()}
    )
    try:
        return text.format_map(payload)
    except Exception:
        return text


def _load_template_mapping(template_id: str) -> dict[str, Any]:
    raw = resolve_prompt_template_json_merged(template_id)
    return dict(raw) if isinstance(raw, Mapping) else {}


def _mapping_section(mapping: Mapping[str, Any], key: str) -> dict[str, Any]:
    raw = mapping.get(key)
    return dict(raw) if isinstance(raw, Mapping) else {}


def _mapping_text(mapping: Mapping[str, Any], key: str, *, default: str = "", **kwargs: Any) -> str:
    return _format_template_text(mapping.get(key, default), **kwargs)


def _mapping_list(mapping: Mapping[str, Any], key: str) -> list[str]:
    raw = mapping.get(key)
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


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
    return "ja"


def _contains_any_token(text: str, tokens: tuple[str, ...]) -> bool:
    normalized = str(text or "").lower()
    return any(token in normalized for token in tokens)


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
    if resolved_node_id == PERSONA_RENDERER_NODE_ID:
        return _mapping_text(_mapping_section(templates, "persona_renderer"), lang_key)
    if resolved_node_id == "cortex_orchestrator":
        return _mapping_text(_mapping_section(templates, "cortex_orchestrator"), lang_key)

    role_instructions = _mapping_section(templates, "generic_roles")
    base_instruction = str(role_instructions.get(resolved_node_id, _mapping_text(templates, "generic_default")))

    if resolved_node_id == "tool_planner" and skill_manifest:
        skill_lines: list[str] = []
        for entry in skill_manifest:
            name = str(entry.get("name") or "")
            desc = str(entry.get("description") or "")
            risk = int(entry.get("risk_level") or 1)
            confirm = bool(entry.get("require_confirm", False))
            line = _mapping_text(templates, "tool_planner_skill_line", name=name, desc=desc, risk=risk, confirm=confirm)
            skill_lines.append(line)
        skills_block = "\n".join(skill_lines)
        base_instruction = (
            f"{base_instruction}\n\n"
            f"{_mapping_text(templates, 'tool_planner_skills_header')}\n{skills_block}"
        )

    return base_instruction


# ---------------------------------------------------------------------------
# 直接回复优先级构建
# ---------------------------------------------------------------------------
def _build_direct_response_priority(
    *,
    user_message: str,
    render_target_lang: str,
    follow_up_hint: str = "",
    bond_stage: str = "",
) -> str:
    base_rules = resolve_prompt_template_json("prompt_builder.direct_response.base_rules")
    rules: list[str] = [str(item).strip() for item in (base_rules or []) if str(item).strip()]

    _close_stages = {"CLOSE_FRIEND", "CONFIDANT", "SOULMATE", "FRIEND"}
    if bond_stage.upper() in _close_stages:
        rules.append(resolve_prompt_template_text("prompt_builder.direct_response.close_followup_rule"))
    elif follow_up_hint:
        rules.append(f"{resolve_prompt_template_text('prompt_builder.direct_response.followup_hint_prefix')}{follow_up_hint}")
    else:
        rules.append(resolve_prompt_template_text("prompt_builder.direct_response.default_followup_rule"))

    if _contains_any_token(user_message, ("短く", "简短", "短い", "brief", "short", "一文", "一句", "一言", "one sentence")):
        rules.append(resolve_prompt_template_text("prompt_builder.direct_response.brief_rule"))

    if _contains_any_token(user_message, ("気分", "調子", "状态", "心情", "感觉", "感受", "今日", "今天", "最近", "どうだった", "怎么样", "有没有", "今日どう", "今日は", "最近どう", "过得", "过的", "今天过")):
        rules.append(resolve_prompt_template_text("prompt_builder.direct_response.daily_life_rule"))

    if _contains_any_token(user_message, ("我爱你", "爱你", "喜欢你", "好喜欢你", "想你", "抱抱", "亲亲", "陪陪我", "愛してる", "好き", "会いたい")):
        affection_rules = resolve_prompt_template_json("prompt_builder.direct_response.affection_rules")
        rules.extend(str(item).strip() for item in (affection_rules or []) if str(item).strip())

    if _contains_any_token(user_message, ("優先", "优先", "priority", "prioritize", "まず")):
        rules.append(resolve_prompt_template_text("prompt_builder.direct_response.priority_rule"))

    if str(render_target_lang).strip().lower() == "ja":
        rules.append(resolve_prompt_template_text("prompt_builder.direct_response.ja_output_rule"))
    else:
        rules.append(resolve_prompt_template_text("prompt_builder.direct_response.zh_output_rule"))

    block_title = resolve_prompt_template_text("prompt_builder.direct_response.block_title")
    return f"{block_title}\n" + "\n".join(f"- {rule}" for rule in rules) + "\n\n"


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
        snippet = text[first: last + 1]
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

    return json.dumps({"narrative": {"text": narrative_text}, "intent_draft": intent_draft, "meta": safe_meta}, ensure_ascii=False, sort_keys=True)


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
