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
TTS_INPUT_LANG: str = "zh"


def is_dual_lang_enabled() -> bool:
    """Phase XV P6：双轨语言输出永久启用。"""
    return True


# ---------------------------------------------------------------------------
# 动态人设补丁
# ---------------------------------------------------------------------------
def _build_character_dynamic_patch(
    *,
    emotion_tag: str = "",
    is_holiday: bool = False,
    holiday_name: str = "",
    render_target_lang: str = "zh",
) -> str:
    """生成角色人设的动态补丁文本，追加在静态人设之后。"""
    import datetime as _dt

    templates = _load_template_mapping(
        "prompt_builder.character_dynamic_patch.templates",
        render_target_lang=render_target_lang,
    )
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


def _load_template_mapping(
    template_id: str,
    *,
    render_target_lang: str | None = None,
) -> dict[str, Any]:
    raw = (
        _resolve_template_payload_with_mirror(
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


def _template_has_enabled_override(template_id: str) -> bool:
    try:
        from host_brain.app.dependencies import get_prompt_template_store  # noqa: PLC0415

        template = get_prompt_template_store().get_template(template_id)
    except Exception:
        return False
    return bool(template.get("has_override")) and bool(template.get("enabled"))


def _resolve_template_payload_with_mirror(template_id: str, *, render_target_lang: str) -> Any:
    lang_key = _normalize_template_lang(render_target_lang)
    mirror_id = f"{template_id}.{lang_key}"
    base_raw = resolve_prompt_template_json_merged(template_id)
    try:
        mirror_raw = resolve_prompt_template_json_merged(mirror_id)
    except Exception:
        mirror_raw = None

    if mirror_raw is None:
        return _resolve_localized_template_payload(template_id, render_target_lang=lang_key)

    base_has_override = _template_has_enabled_override(template_id)
    mirror_has_override = _template_has_enabled_override(mirror_id)

    if isinstance(base_raw, Mapping) and isinstance(mirror_raw, Mapping):
        if base_has_override and not mirror_has_override:
            return _merge_localized_mapping(mirror_raw, base_raw)
        return _merge_localized_mapping(base_raw, mirror_raw)

    if base_has_override and not mirror_has_override:
        return base_raw
    return mirror_raw


def _resolve_localized_template_text(template_id: str, *, render_target_lang: str) -> str:
    payload = _resolve_template_payload_with_mirror(template_id, render_target_lang=render_target_lang)
    if isinstance(payload, str):
        return payload
    return resolve_prompt_template_text(template_id)


def _resolve_localized_template_list(template_id: str, *, render_target_lang: str) -> list[str]:
    payload = _resolve_template_payload_with_mirror(template_id, render_target_lang=render_target_lang)
    if isinstance(payload, list):
        return [str(item).strip() for item in payload if str(item).strip()]
    raw = resolve_prompt_template_json(template_id)
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    return []


def _resolve_localized_template_mapping(template_id: str, *, render_target_lang: str) -> dict[str, Any]:
    payload = _resolve_template_payload_with_mirror(template_id, render_target_lang=render_target_lang)
    return dict(payload) if isinstance(payload, Mapping) else {}


def infer_render_target_lang(*, user_message: str, core_narrative: str = "") -> str:
    """Resolve render language; production voice/text output is Chinese-only by default."""
    override = str(os.environ.get("HOST_RENDER_TARGET_LANG") or "").strip().lower()
    allow_japanese = str(os.environ.get("HOST_ALLOW_JAPANESE_OUTPUT") or "").strip().lower() in {"1", "true", "yes", "on"}
    if override == "zh":
        return "zh"
    if override == "ja" and allow_japanese:
        return override
    combined = f"{str(user_message or '')}\n{str(core_narrative or '')}".strip().lower()
    if any(token in combined for token in ("中文", "汉语", "简体", "chinese", "mandarin")):
        return "zh"
    if allow_japanese and any(token in combined for token in ("日文", "日语", "日本語", "japanese")):
        return "ja"
    if allow_japanese and _contains_kana(combined):
        return "ja"
    if _contains_cjk_ideograph(combined):
        return "zh"
    return "zh"


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
    base_rules = _resolve_localized_template_list(
        "prompt_builder.direct_response.base_rules",
        render_target_lang=render_target_lang,
    )
    rules: list[str] = [str(item).strip() for item in base_rules if str(item).strip()]
    scenario_rules = _resolve_localized_template_mapping(
        "prompt_builder.direct_response.scenario_rules",
        render_target_lang=render_target_lang,
    )

    _close_stages = {"CLOSE_FRIEND", "CONFIDANT", "SOULMATE", "FRIEND"}
    if bond_stage.upper() in _close_stages:
        rules.append(
            _resolve_localized_template_text(
                "prompt_builder.direct_response.close_followup_rule",
                render_target_lang=render_target_lang,
            )
        )
    elif follow_up_hint:
        rules.append(
            f"{_resolve_localized_template_text('prompt_builder.direct_response.followup_hint_prefix', render_target_lang=render_target_lang)}{follow_up_hint}"
        )
    else:
        rules.append(
            _resolve_localized_template_text(
                "prompt_builder.direct_response.default_followup_rule",
                render_target_lang=render_target_lang,
            )
        )

    if _contains_any_token(user_message, ("短く", "简短", "短い", "brief", "short", "一文", "一句", "一言", "one sentence")):
        rules.append(
            _resolve_localized_template_text(
                "prompt_builder.direct_response.brief_rule",
                render_target_lang=render_target_lang,
            )
        )

    if _contains_any_token(user_message, ("気分", "調子", "状态", "心情", "感觉", "感受", "今日", "今天", "最近", "どうだった", "怎么样", "有没有", "今日どう", "今日は", "最近どう", "过得", "过的", "今天过")):
        rules.append(
            _resolve_localized_template_text(
                "prompt_builder.direct_response.daily_life_rule",
                render_target_lang=render_target_lang,
            )
        )

    if _contains_any_token(user_message, ("我爱你", "爱你", "喜欢你", "好喜欢你", "想你", "抱抱", "亲亲", "陪陪我", "愛してる", "好き", "会いたい")):
        affection_rules = _resolve_localized_template_list(
            "prompt_builder.direct_response.affection_rules",
            render_target_lang=render_target_lang,
        )
        rules.extend(str(item).strip() for item in affection_rules if str(item).strip())

    if _contains_any_token(
        user_message,
        ("难受", "做不好", "累", "委屈", "想哭", "心情不好", "不开心", "安慰我", "抱抱我", "陪陪我", "つらい", "しんどい", "疲れた"),
    ):
        comfort_rule = str(scenario_rules.get("comfort") or "").strip()
        if comfort_rule:
            rules.append(comfort_rule)

    if _contains_any_token(
        user_message,
        ("好久不见", "有想我吗", "想我吗", "会想我", "想过我吗", "会不会想我", "会想起我吗", "ひさしぶり", "会いたかった", "恋しかった"),
    ):
        reunion_rule = str(scenario_rules.get("reunion") or "").strip()
        if reunion_rule:
            rules.append(reunion_rule)

    if _contains_any_token(
        user_message,
        ("争执", "吵架", "别扭", "在意之前", "那次争执", "还在意", "けんか", "気にしてる", "引っかかってる"),
    ):
        rift_repair_rule = str(scenario_rules.get("rift_repair") or "").strip()
        if rift_repair_rule:
            rules.append(rift_repair_rule)

    if _contains_any_token(
        user_message,
        ("逗我开心", "逗我", "哄我开心", "更会哄", "更会逗", "逗开心", "からかう", "笑わせて", "元気にして"),
    ):
        playful_rule = str(scenario_rules.get("playful") or "").strip()
        if playful_rule:
            rules.append(playful_rule)

    if _contains_any_token(
        user_message,
        ("认真聊", "认真说", "慢慢聊", "慢慢说", "陪我说下去", "陪我聊", "最近的状态", "想认真聊聊", "想和你认真聊"),
    ):
        deep_companionship_rule = str(scenario_rules.get("deep_companionship") or "").strip()
        if deep_companionship_rule:
            rules.append(deep_companionship_rule)

    if _contains_any_token(user_message, ("優先", "优先", "priority", "prioritize", "まず")):
        rules.append(
            _resolve_localized_template_text(
                "prompt_builder.direct_response.priority_rule",
                render_target_lang=render_target_lang,
            )
        )

    if str(render_target_lang).strip().lower() == "ja":
        rules.append(
            _resolve_localized_template_text(
                "prompt_builder.direct_response.ja_output_rule",
                render_target_lang=render_target_lang,
            )
        )
    else:
        rules.append(
            _resolve_localized_template_text(
                "prompt_builder.direct_response.zh_output_rule",
                render_target_lang=render_target_lang,
            )
        )

    block_title = _resolve_localized_template_text(
        "prompt_builder.direct_response.block_title",
        render_target_lang=render_target_lang,
    )
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
