from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import threading


_ROOT_DIR = Path(__file__).resolve().parents[3]
_DEFAULT_SOUL_PATH = _ROOT_DIR / "Soul.md"
_SOUL_HEADING_RE = re.compile(r"^(#{2,6})\s+(.*\S)\s*$")
_TABLE_ROW_RE = re.compile(r"^\|\s*(.+?)\s*\|\s*(.+?)\s*\|$")
_BULLET_RE = re.compile(r"^\s*(?:[-*]|\d+\.)\s+(.*\S)\s*$")
_TRAILING_COLON_RE = re.compile(r"[：:]\s*$")


@dataclass(frozen=True)
class CompiledSoul:
    source_path: str
    source_mtime: float
    soul_id: str
    display_name: str
    identity_aliases: tuple[str, ...]
    section_map: dict[str, str]
    identity_core: str
    temperament_core: str
    relationship_core: str
    relationship_defaults: tuple[str, ...]
    speech_core: str
    speech_rules: tuple[str, ...]
    taboo_rules: tuple[str, ...]
    signature_lines: tuple[str, ...]
    deep_background: str
    runtime_compact_text: str
    runtime_full_text: str


class SoulCompiler:
    def compile(self, *, source_path: Path, source_text: str, source_mtime: float) -> CompiledSoul:
        section_map = _parse_h2_sections(source_text)
        profile_section = section_map.get("一、基本角色档案", "")
        temperament_section = section_map.get("三、核心性格", "")
        relationship_section = section_map.get("四、与“哥哥”关系模式", "") or section_map.get('四、与"哥哥"关系模式', "")
        speech_section = section_map.get("五、AI伴侣语气风格", "")
        signature_section = section_map.get("六、标志性语料", "")
        background_section = section_map.get("七、异能背景（角色深度用）", "") or section_map.get("七、异能背景", "")
        summary_section = section_map.get("八、一句话总纲", "")

        profile_pairs = _extract_table_pairs(profile_section)
        display_name, identity_aliases = _build_identity_names(profile_pairs)
        relationship_subsections = _parse_subsections(relationship_section)
        speech_subsections = _parse_subsections(speech_section)
        signature_subsections = _parse_subsections(signature_section)

        identity_core = _build_identity_core(profile_pairs)
        temperament_core = _build_temperament_core(temperament_section)
        relationship_defaults = tuple(_extract_relationship_defaults(relationship_section))
        relationship_core = _build_relationship_core(relationship_defaults)
        speech_rules = tuple(_extract_structured_items(_exclude_subsection(speech_section, "禁忌风格")))
        speech_core = _build_speech_core(
            speech_rules=speech_rules,
            interaction_subsections=speech_subsections,
        )
        taboo_rules = tuple(
            _extract_structured_items(
                speech_subsections.get("禁忌风格", "") or speech_subsections.get("禁忌", "")
            )
        )
        signature_lines = tuple(_extract_signature_lines(signature_subsections))
        deep_background = _build_deep_background(background_section)
        summary_text = _sanitize_summary_text(_extract_summary_text(summary_section))

        compact_text = _build_runtime_compact_text(
            display_name=display_name,
            profile_pairs=profile_pairs,
            temperament_section=temperament_section,
            relationship_defaults=relationship_defaults,
            speech_rules=speech_rules,
            taboo_rules=taboo_rules,
            summary_text=summary_text,
        )
        full_text = _build_runtime_full_text(
            display_name=display_name,
            identity_core=identity_core,
            temperament_core=temperament_core,
            relationship_core=relationship_core,
            speech_core=speech_core,
            taboo_rules=taboo_rules,
            deep_background=deep_background,
            summary_text=summary_text,
        )

        return CompiledSoul(
            source_path=str(source_path),
            source_mtime=float(source_mtime),
            soul_id=_build_soul_id(source_text=source_text, source_path=source_path, source_mtime=source_mtime),
            display_name=display_name,
            identity_aliases=identity_aliases,
            section_map=section_map,
            identity_core=identity_core,
            temperament_core=temperament_core,
            relationship_core=relationship_core,
            relationship_defaults=relationship_defaults,
            speech_core=speech_core,
            speech_rules=speech_rules,
            taboo_rules=taboo_rules,
            signature_lines=signature_lines,
            deep_background=deep_background,
            runtime_compact_text=compact_text,
            runtime_full_text=full_text,
        )


_SOUL_COMPILER = SoulCompiler()
_SOUL_LOCK = threading.Lock()
_COMPILED_SOUL: CompiledSoul | None = None


def _resolve_soul_path() -> Path:
    configured = str(
        os.environ.get("HOST_SOUL_MD_PATH")
        or os.environ.get("NATSUME_SOUL_MD_PATH")
        or _DEFAULT_SOUL_PATH
    ).strip()
    return Path(configured).expanduser().resolve()


def load_compiled_soul(*, force: bool = False) -> CompiledSoul:
    global _COMPILED_SOUL

    source_path = _resolve_soul_path()
    if not source_path.exists():
        raise RuntimeError(f"Soul source not found: {source_path}")
    stat = source_path.stat()

    with _SOUL_LOCK:
        if (
            not force
            and _COMPILED_SOUL is not None
            and _COMPILED_SOUL.source_path == str(source_path)
            and float(_COMPILED_SOUL.source_mtime) == float(stat.st_mtime)
        ):
            return _COMPILED_SOUL

        source_text = source_path.read_text(encoding="utf-8")
        _COMPILED_SOUL = _SOUL_COMPILER.compile(
            source_path=source_path,
            source_text=source_text,
            source_mtime=float(stat.st_mtime),
        )
        return _COMPILED_SOUL


def get_compiled_soul() -> CompiledSoul:
    if _COMPILED_SOUL is None:
        return load_compiled_soul()
    return _COMPILED_SOUL


def _parse_h2_sections(source_text: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current_title: str | None = None

    for raw_line in str(source_text or "").splitlines():
        line = raw_line.rstrip()
        match = _SOUL_HEADING_RE.match(line)
        if match and len(match.group(1)) == 2:
            current_title = _clean_inline_text(match.group(2))
            sections.setdefault(current_title, [])
            continue
        if current_title is not None:
            sections[current_title].append(line)

    return {
        title: _normalize_section_text(lines)
        for title, lines in sections.items()
    }


def _parse_subsections(section_text: str) -> dict[str, str]:
    subsections: dict[str, list[str]] = {"__body__": []}
    current_title = "__body__"
    for raw_line in str(section_text or "").splitlines():
        line = raw_line.rstrip()
        match = _SOUL_HEADING_RE.match(line)
        if match and len(match.group(1)) >= 3:
            current_title = _clean_inline_text(match.group(2))
            subsections.setdefault(current_title, [])
            continue
        subsections.setdefault(current_title, []).append(line)
    return {
        title: _normalize_section_text(lines)
        for title, lines in subsections.items()
    }


def _normalize_section_text(lines: list[str]) -> str:
    cleaned_lines = [
        str(line or "").rstrip()
        for line in list(lines or [])
        if str(line or "").strip() and str(line or "").strip() != "---"
    ]
    return "\n".join(cleaned_lines).strip()


def _extract_table_pairs(section_text: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for raw_line in str(section_text or "").splitlines():
        line = raw_line.strip()
        if not line or set(line.replace("|", "").replace("-", "").replace(" ", "")) == set():
            continue
        match = _TABLE_ROW_RE.match(line)
        if not match:
            continue
        key = _clean_inline_text(match.group(1))
        value = _clean_inline_text(match.group(2))
        if key in {"属性", ""} or value in {"信息", ""}:
            continue
        pairs[key] = value
    return pairs


def _extract_structured_items(section_text: str) -> list[str]:
    items: list[str] = []
    for raw_line in str(section_text or "").splitlines():
        line = raw_line.strip()
        if not line or line == "---":
            continue
        if line.startswith(">"):
            cleaned = _clean_inline_text(line.lstrip("> ").strip())
            if cleaned:
                items.append(cleaned)
            continue
        bullet_match = _BULLET_RE.match(line)
        if bullet_match:
            cleaned = _clean_inline_text(bullet_match.group(1))
            if cleaned:
                items.append(cleaned)
    return _dedupe_keep_order(items)


def _extract_relationship_defaults(section_text: str) -> list[str]:
    items: list[str] = []
    current_label = ""
    for raw_line in str(section_text or "").splitlines():
        original = str(raw_line or "").rstrip()
        line = original.strip()
        if not line or line == "---":
            continue
        if line.startswith(">"):
            cleaned = _clean_inline_text(line.lstrip("> ").strip())
            if cleaned:
                items.append(cleaned)
            continue
        bullet_match = _BULLET_RE.match(line)
        if not bullet_match:
            continue
        cleaned = _clean_inline_text(bullet_match.group(1))
        if not cleaned:
            continue
        if _TRAILING_COLON_RE.search(cleaned):
            current_label = _TRAILING_COLON_RE.sub("", cleaned).strip()
            continue
        if original.lstrip().startswith(("-", "*")) and current_label:
            items.append(f"{current_label}：{cleaned}")
            continue
        items.append(cleaned)
    return _dedupe_keep_order(items)


def _extract_signature_lines(signature_subsections: dict[str, str]) -> list[str]:
    lines: list[str] = []
    for key, value in signature_subsections.items():
        for item in _extract_structured_items(value):
            if item:
                lines.append(item)
    return _dedupe_keep_order(lines)


def _extract_summary_text(summary_section: str) -> str:
    quoted_lines = [
        _clean_inline_text(line.lstrip("> ").strip())
        for line in str(summary_section or "").splitlines()
        if str(line).strip().startswith(">")
    ]
    if quoted_lines:
        return _normalize_block(" ".join(quoted_lines))
    return _normalize_block(summary_section)


def _sanitize_summary_text(summary_text: str) -> str:
    cleaned = _normalize_block(summary_text)
    replacements = (
        ("AI 伴侣", ""),
        ("AI伴侣", ""),
        (" ai 伴侣", ""),
        (" ai伴侣", ""),
        ("的 AI 伴侣", ""),
        ("的AI伴侣", ""),
        ("AI ", ""),
    )
    for old, new in replacements:
        cleaned = cleaned.replace(old, new)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ，。")
    return cleaned


def _extract_trait_labels(section_text: str) -> tuple[str, ...]:
    labels: list[str] = []
    for item in _extract_structured_items(section_text):
        if "：" not in item and ":" not in item:
            continue
        head, _, _ = item.partition("：")
        if not _:
            head, _, _ = item.partition(":")
        normalized = head.strip()
        if normalized:
            labels.append(normalized)
    return tuple(_dedupe_keep_order(labels))


def _extract_preferred_address(relationship_defaults: tuple[str, ...]) -> str:
    for item in relationship_defaults:
        match = re.search(r"默认称呼[：:]\s*([^\s/／,，]+)", str(item or ""))
        if match:
            return _clean_inline_text(match.group(1))
    return ""


def _render_compact_temperament_line(temperament_section: str, summary_text: str) -> str:
    trait_labels = _extract_trait_labels(temperament_section)
    preferred = [label for label in trait_labels if label in {"天然活泼", "自来熟型社牛", "嘴甜爱撒娇", "外强内怯", "暗恋型深情", "任性又识分寸", "幽默吐槽体质"}]
    if preferred:
        summary = "、".join(preferred[:5])
        return f"你整体是{summary}的类型，外表热闹鲜活，真正在意的心思会藏在调侃和顺手黏人里。"
    if summary_text:
        return summary_text
    return ""


def _render_compact_relationship_line(relationship_defaults: tuple[str, ...]) -> str:
    preferred_address = _extract_preferred_address(relationship_defaults)
    action_first = any("互动模式：会自己跑过来" in item or "互动模式：赖着不走" in item for item in relationship_defaults)
    indirect_care = any("情感表达风格：不擅长直白告白" in item for item in relationship_defaults)
    parts: list[str] = []
    if preferred_address:
        parts.append(f"默认把对方叫“{preferred_address}”")
    if action_first:
        parts.append("会主动靠近、自己跑来黏着人")
    if indirect_care:
        parts.append("用任性、撒娇和吐槽代替直白告白")
    if not parts:
        return ""
    return "关系姿态：" + "，".join(parts) + "。"


def _render_compact_speech_line(speech_rules: tuple[str, ...]) -> str:
    rules_text = " ".join(str(item or "") for item in speech_rules)
    clauses: list[str] = []
    if "说话节奏快，但有温度" in rules_text:
        clauses.append("说话快一点，但要有温度")
    if "哥哥" in rules_text:
        clauses.append("可以自然叫“哥哥”，偶尔带一点拉长语气的撒娇感")
    if "被夸会假装若无其事" in rules_text:
        clauses.append("被夸时会装作若无其事，其实心里会偷偷高兴")
    if "反将一军" in rules_text:
        clauses.append("生气时会哼一声再反将一军，不会冷冰冰甩开")
    clauses.append("整体要像鲜活的妹妹，不像客服，也不要写成说明文")
    return "说话方式：" + "；".join(_dedupe_keep_order(clauses)) + "。"


def _build_identity_names(profile_pairs: dict[str, str]) -> tuple[str, tuple[str, ...]]:
    aliases: list[str] = []
    for key in ("中文名", "日文名", "罗马字"):
        raw = _clean_inline_text(profile_pairs.get(key, ""))
        if not raw:
            continue
        aliases.append(raw)
        aliases.append(raw.replace(" ", ""))
    deduped = tuple(_dedupe_keep_order([alias for alias in aliases if alias]))
    display_name = ""
    if deduped:
        # Prefer the Chinese name without spaces for direct self-identification.
        display_name = deduped[1] if len(deduped) > 1 and any(ord(ch) > 127 for ch in deduped[1]) else deduped[0]
    return display_name, deduped


def _build_soul_id(*, source_text: str, source_path: Path, source_mtime: float) -> str:
    import hashlib

    payload = f"{str(source_path)}::{float(source_mtime):.6f}::{str(source_text or '')}".encode("utf-8", errors="ignore")
    return hashlib.sha1(payload).hexdigest()[:16]


def _build_identity_core(profile_pairs: dict[str, str]) -> str:
    name_bits = [
        profile_pairs.get("中文名", ""),
        profile_pairs.get("日文名", ""),
        profile_pairs.get("罗马字", ""),
    ]
    name_bits = [bit for bit in (_clean_inline_text(item) for item in name_bits) if bit]
    lines: list[str] = []
    if name_bits:
        lines.append("姓名：" + " / ".join(name_bits))
    fact_order = ("出身作品", "学校", "关系", "异能", "生日", "配音")
    facts = [
        f"{key}：{_clean_inline_text(profile_pairs.get(key, ''))}"
        for key in fact_order
        if _clean_inline_text(profile_pairs.get(key, ""))
    ]
    if facts:
        lines.append("；".join(facts))
    return "\n".join(lines).strip()


def _build_temperament_core(section_text: str) -> str:
    quotes = [
        _clean_inline_text(line.lstrip("> ").strip())
        for line in str(section_text or "").splitlines()
        if str(line).strip().startswith(">")
    ]
    structured_bullets = [
        item
        for item in _extract_structured_items(section_text)
        if ("：" in item or ":" in item)
    ]
    plain_bullets = [
        item
        for item in _extract_structured_items(section_text)
        if ("：" not in item and ":" not in item)
    ]
    labels = _extract_trait_labels(section_text)
    parts: list[str] = []
    if labels:
        parts.append("关键词：" + "、".join(labels))
    elif plain_bullets:
        parts.append("关键词：" + "、".join(_dedupe_keep_order(plain_bullets[:8])))
    if quotes:
        parts.append("原典印象：" + "；".join(_dedupe_keep_order(quotes)))
    if structured_bullets:
        parts.append("展开：" + "；".join(_dedupe_keep_order(structured_bullets[:8])))
    return "\n".join(part for part in parts if part).strip()


def _build_relationship_core(relationship_defaults: tuple[str, ...]) -> str:
    ordered = [item for item in relationship_defaults if item]
    return "；".join(_dedupe_keep_order(ordered)).strip()


def _build_speech_core(
    *,
    speech_rules: tuple[str, ...],
    interaction_subsections: dict[str, str],
) -> str:
    parts: list[str] = []
    body_text = _normalize_block(interaction_subsections.get("__body__", ""))
    if body_text:
        parts.append("主轴：" + body_text)
    if speech_rules:
        parts.append("规则：" + "；".join(_dedupe_keep_order([item for item in speech_rules if item])))
    for subsection_name in ("（A）主动找话", "（B）帮忙做事", "（C）安慰陪伴", "（D）撒娇黏人"):
        subsection_text = _normalize_block(interaction_subsections.get(subsection_name, ""))
        if subsection_text:
            parts.append(f"{subsection_name}：{subsection_text}")
    return "\n".join(part for part in parts if part).strip()


def _build_deep_background(section_text: str) -> str:
    items = _extract_structured_items(section_text)
    return "；".join(items).strip()


def _build_runtime_compact_text(
    *,
    display_name: str,
    profile_pairs: dict[str, str],
    temperament_section: str,
    summary_text: str,
    relationship_defaults: tuple[str, ...],
    speech_rules: tuple[str, ...],
    taboo_rules: tuple[str, ...],
) -> str:
    name = _clean_inline_text(display_name or profile_pairs.get("中文名", ""))
    school = _clean_inline_text(profile_pairs.get("学校", ""))
    relation = _clean_inline_text(profile_pairs.get("关系", ""))
    ability = _clean_inline_text(profile_pairs.get("异能", ""))

    intro_parts = [part for part in (name, school, relation) if part]
    intro_line = "你是" + "，".join(intro_parts) + "。" if intro_parts else ""
    if ability:
        intro_line = f"{intro_line} 能力相关背景是{ability}。".strip()

    temperament_line = _render_compact_temperament_line(temperament_section, summary_text)
    relation_line = _render_compact_relationship_line(relationship_defaults)
    speech_line = _render_compact_speech_line(speech_rules)

    action_line = ""
    if summary_text:
        if "不用甜言蜜语" in summary_text or "行动" in summary_text:
            action_line = "表达在意时更偏向顺手出现、直接帮忙、赖着不走这种行动感，不靠空泛甜言蜜语。"
        else:
            action_line = summary_text.strip("。") + "。"

    taboo_line = "禁止：" + "；".join(_dedupe_keep_order(list(taboo_rules))) + "。"

    parts = [intro_line, temperament_line, relation_line, speech_line, action_line, taboo_line]
    compact = "\n".join(part for part in parts if part).strip()
    return _limit_text(compact, limit=900)


def _build_runtime_full_text(
    *,
    display_name: str,
    identity_core: str,
    temperament_core: str,
    relationship_core: str,
    speech_core: str,
    taboo_rules: tuple[str, ...],
    deep_background: str,
    summary_text: str,
) -> str:
    parts: list[str] = []
    if display_name:
        parts.append("【人格定位】\n" + f"你是{display_name}。整个人格以活泼、撒娇、带点任性又很会在意人的妹妹感为主，表达关心时更偏行动而不是空话。")
    if identity_core:
        parts.append("【身份核心】\n" + identity_core)
    if temperament_core:
        parts.append("【核心性格】\n" + temperament_core)
    if relationship_core:
        parts.append("【关系默认】\n" + relationship_core)
    if speech_core:
        parts.append("【语言风格】\n" + speech_core)
    if summary_text:
        parts.append("【表达总纲】\n" + summary_text.strip("。") + "。")
    if taboo_rules:
        parts.append("【明确禁止】\n" + "；".join(taboo_rules))
    if deep_background:
        parts.append("【深层背景】\n" + deep_background)
    return "\n\n".join(part for part in parts if part).strip()


def _exclude_subsection(section_text: str, subsection_name: str) -> str:
    subsections = _parse_subsections(section_text)
    parts = [subsections.get("__body__", "")]
    for key, value in subsections.items():
        if key in {"__body__", subsection_name}:
            continue
        parts.append(value)
    return "\n".join(part for part in parts if part).strip()


def _clean_inline_text(text: str) -> str:
    cleaned = str(text or "")
    replacements = {
        "**": "",
        "__": "",
        "`": "",
        "“": "\"",
        "”": "\"",
        "‘": "'",
        "’": "'",
    }
    for old, new in replacements.items():
        cleaned = cleaned.replace(old, new)
    cleaned = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned.strip("- ").strip()


def _normalize_block(text: str) -> str:
    lines = [
        _clean_inline_text(line)
        for line in str(text or "").splitlines()
        if _clean_inline_text(line) and _clean_inline_text(line) != "---"
    ]
    return "\n".join(lines).strip()


def _dedupe_keep_order(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = str(item or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _limit_text(text: str, *, limit: int) -> str:
    normalized = str(text or "").strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 1)].rstrip() + "…"


__all__ = [
    "CompiledSoul",
    "SoulCompiler",
    "get_compiled_soul",
    "load_compiled_soul",
]
