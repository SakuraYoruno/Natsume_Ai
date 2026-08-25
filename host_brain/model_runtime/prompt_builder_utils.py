"""提示构建的纯函数工具层。

拆分自 prompt_builder.py（P2-10）：
  - 场景常量
  - 文本工具函数（截断、语言检测、模板加载等）
  - 角色人设加载
  - 节点角色指令构建
  - JSON 提取工具
  - 节点 Runtime Payload 提取
  - 双轨语言常量与辅助函数

这里集中处理「不可信模型文本 / 契约模板 / 节点载荷」之间的形状转换。
函数保持无副作用；唯一的线程局部元数据仅用于让同一请求线程读取最近的
注入检测结果，避免把诊断状态串到并发请求中。
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Mapping

from host_brain.model_runtime.prompt_contract import (
    prompt_contract_exists,
    resolve_prompt_contract_json,
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

# ---------------------------------------------------------------------------
# Ref #70: 线程局部变量 — 注入检测结果
# ---------------------------------------------------------------------------
_injection_meta_local = threading.local()


def get_last_injection_meta() -> dict[str, Any]:
    """读取当前请求线程最近一次 MemoryGuard 注入检测结果。

    此处不返回跨线程的全局状态：ASGI 的并发请求必须各自保留诊断归属。
    """
    return getattr(_injection_meta_local, "meta", {})


def _set_injection_meta(meta: dict[str, Any]) -> None:
    """内部：将注入检测结果写入线程局部变量。"""
    _injection_meta_local.meta = meta


# ---------------------------------------------------------------------------
# 文本工具函数
# ---------------------------------------------------------------------------
def _truncate_for_prompt(value: Any, *, limit: int = 240) -> str:
    """将任意证据压成单行且有上限的提示片段，控制上下文预算。"""
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 3)]}..."


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
    """仅在原文不含中日字符时尝试修复 UTF-8 被 Latin-1 误解码的乱码。

    已含 CJK 字符的文本不做二次转码，避免“修复”本来正确的用户或模型输出。
    """
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
    """宽容地填充契约模板：未知占位符原样保留而非令请求失败。"""
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
    """加载映射型提示契约；非映射载荷降级为空，隔离配置形状错误。"""
    raw = (
        _resolve_template_payload_with_mirror(
            template_id,
            render_target_lang=render_target_lang or "zh",
        )
        if render_target_lang
        else resolve_prompt_contract_json(template_id)
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
    """递归叠加语言镜像，同时忽略语言根键避免把选择器写回最终模板。"""
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
    raw = resolve_prompt_contract_json(template_id)
    if isinstance(raw, Mapping):
        localized = raw.get(lang_key)
        if localized is not None:
            if isinstance(localized, Mapping):
                return _merge_localized_mapping(localized, raw)
            return localized
    return raw


def _resolve_template_payload_with_mirror(template_id: str, *, render_target_lang: str) -> Any:
    """优先合并独立语言镜像；没有镜像时兼容同一契约内的语言分支。

    这让语言新增只影响模板数据，不改变调用方的字段读取方式。
    """
    lang_key = _normalize_template_lang(render_target_lang)
    mirror_id = f"{template_id}.{lang_key}"
    base_raw = resolve_prompt_contract_json(template_id)
    if not prompt_contract_exists(mirror_id):
        return _resolve_localized_template_payload(template_id, render_target_lang=lang_key)
    mirror_raw = resolve_prompt_contract_json(mirror_id)

    if isinstance(base_raw, Mapping) and isinstance(mirror_raw, Mapping):
        return _merge_localized_mapping(base_raw, mirror_raw)
    return mirror_raw


def _resolve_prompt_contract_json_localized(
    template_id: str,
    *,
    render_target_lang: str = "zh",
) -> Any:
    """Resolve a localized prompt while preserving the old empty-pack fallback."""

    try:
        return _resolve_template_payload_with_mirror(
            template_id,
            render_target_lang=render_target_lang,
        )
    except Exception:
        return {}


def _load_localized_prompt_mapping(
    template_id: str,
    *,
    render_target_lang: str = "zh",
) -> dict[str, Any]:
    """Load a localized prompt mapping while preserving fail-soft semantics."""

    raw = _resolve_prompt_contract_json_localized(
        template_id,
        render_target_lang=render_target_lang,
    )
    return dict(raw) if isinstance(raw, Mapping) else {}


def infer_render_target_lang(*, user_message: str, core_narrative: str = "") -> str:
    """推断本轮渲染语言，并以中文作为生产语音/文本的保守默认值。

    日语输出必须同时经过显式开关和用户意图/假名信号；环境覆盖也不能绕过该
    授权边界，避免部署配置意外改变面对用户的默认语言。
    """
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
    """按节点身份取回最小角色指令，并只向工具规划节点附加技能清单。

    技能描述属于规划上下文，不能泄漏给普通渲染节点，否则会污染其自然语言职责。
    """
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
# JSON 提取工具
# ---------------------------------------------------------------------------
def extract_json_object_from_text(raw_text: str) -> dict[str, Any]:
    """从供应商文本中提取首个 JSON 对象，兼容 Markdown 围栏和前后杂文本。

    不接受数组或标量，调用方依赖对象字段做后续权限与叙事校验；无法证明形状时
    显式失败，不把任意文本伪装成有效网关载荷。
    """
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
    """把供应商 JSON 收敛为网关可消费的草稿叙事载荷。

    ``draft_only`` 是权限边界：模型给出的 intent 只能作为草案，不能借此直接
    获得执行权；缺少叙事正文则拒绝输出，避免下游误把空回复当成功。
    """
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
    """归一化 LangChain 多段 content，忽略不能安全映射为文本的结构项。"""
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
    """从某个节点的运行时封套复制 payload，隔离调用方对共享状态的修改。"""
    node_root = runtime_payload.get(node_id)
    if not isinstance(node_root, Mapping):
        return {}
    payload_raw = node_root.get("payload")
    if isinstance(payload_raw, Mapping):
        return dict(payload_raw)
    return {}


def extract_node_narrative_text(runtime_payload: Mapping[str, Any], *, node_id: str) -> str:
    """提取指定节点的叙事文本，缺失或形状错误时返回空串供上游降级。"""
    node_payload = extract_node_runtime_payload(runtime_payload, node_id=node_id)
    return extract_gateway_narrative(node_payload)


def extract_gateway_narrative(payload: dict[str, Any] | None) -> str:
    """读取网关叙事字段并在边界处修复已知转码乱码，不伪造缺失正文。"""
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
