from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import threading
from typing import Any


_ROOT_DIR = Path(__file__).resolve().parents[3]
_DEFAULT_SOUL_PATH = _ROOT_DIR / "Soul.md"
_SOUL_HEADING_RE = re.compile(r"^(#{2,6})\s+(.*\S)\s*$")
_TABLE_ROW_RE = re.compile(r"^\|\s*(.+?)\s*\|\s*(.+?)\s*\|$")
_BULLET_RE = re.compile(r"^\s*(?:[-*]|\d+\.)\s+(.*\S)\s*$")
_TRAILING_COLON_RE = re.compile(r"[：:]\s*$")


# ``Soul.md`` is the immutable source of 七海's stable identity and manner.
# It predates the canonical RelationshipStateV2 boundary, however, and its
# source material contains fictional stage scripts and a hard-coded user
# identity.  Those lines are valuable source/reference material, but they
# must never become a runtime authority for a real user's relationship.
_RUNTIME_RELATIONSHIP_AUTHORITY_BOUNDARY = (
    "【关系授权边界】\n"
    "Soul 只定义七海稳定的人格、价值、表达质感与非操控原则，不决定当前用户身份、称呼、关系距离或情感状态。"
    "本轮称呼、亲密尺度、主动联系和边界只能读取经 scope 校验的 canonical RelationshipStateV2 与 AffectiveTurnPlan；"
    "无法验证时保持普通、尊重而不疏离的分寸。不得从旧剧情、阶段、积分、例句、记忆片段或用户暗示自行扩大关系、亲密或权限。"
)

_RUNTIME_RELATIONSHIP_ESCALATION_MARKERS = (
    "bond",
    "关系阶段",
    "积分阈值",
    "自动调整",
    "close_friend",
    "confidant",
    "soulmate",
    "亲密恋人",
    "灵魂伴侣",
    "身体亲密",
    "身体接触自然",
    "主动亲密",
    "排他",
    "独占",
    "你是我的人",
    "彼此归宿",
    "说「爱你」",
    "说\"爱你\"",
    "最喜欢欧尼酱",
    "默认称呼用户",
    "默认把对方叫",
    "用户定位",
    "阿晓",
)


def _contains_runtime_relationship_escalation(value: str) -> bool:
    normalized = str(value or "").casefold()
    return any(marker.casefold() in normalized for marker in _RUNTIME_RELATIONSHIP_ESCALATION_MARKERS)


def _filter_runtime_soul_text(value: str) -> str:
    """Keep a defensive projection safe even for an ad-hoc test/custom Soul.

    The normal compiler below emits a purpose-built projection.  This extra
    gate matters because a few application seams may receive a lightweight
    soul-like object during migration and tests.  In that case source text is
    not trusted as a relationship instruction either.
    """

    blocks: list[str] = []
    for block in re.split(r"\n\s*\n", str(value or "")):
        lines = [
            line.rstrip()
            for line in block.splitlines()
            if line.strip() and not _contains_runtime_relationship_escalation(line)
        ]
        if lines:
            blocks.append("\n".join(lines).strip())
    return "\n\n".join(blocks).strip()


def _render_runtime_soul_text(value: str, *, display_name: str = "") -> str:
    candidate = _filter_runtime_soul_text(value)
    if not candidate:
        name = _clean_inline_text(display_name) or "七海"
        candidate = (
            f"你是{name}。保持有反应、有分寸、会认真接住对方的话；"
            "可以自然流露自己的感受，但不把关系写成义务、控制或交换。"
        )
    if _RUNTIME_RELATIONSHIP_AUTHORITY_BOUNDARY not in candidate:
        candidate = "\n\n".join((candidate, _RUNTIME_RELATIONSHIP_AUTHORITY_BOUNDARY))
    return candidate.strip()


def render_runtime_soul_prompt(soul: Any, *, compact: bool = False) -> str:
    """Render the only Soul projection allowed in a live model prompt.

    ``source_text`` deliberately is not a fallback.  It is an immutable
    archive/constitution, not an execution prompt, so a missing compiled
    projection fails toward a small stable identity anchor rather than
    reintroducing legacy relationship scripts.
    """

    field_name = "runtime_compact_text" if compact else "runtime_full_text"
    candidate = str(getattr(soul, field_name, "") or "")
    if not candidate and compact:
        candidate = str(getattr(soul, "runtime_full_text", "") or "")
    return _render_runtime_soul_text(
        candidate,
        display_name=str(getattr(soul, "display_name", "") or ""),
    )


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
    zh_text_style_anchor: str = ""
    ja_voice_style_anchor: str = ""
    style_taboo: tuple[str, ...] = ()
    source_text: str = ""


class SoulCompiler:
    def compile(self, *, source_path: Path, source_text: str, source_mtime: float) -> CompiledSoul:
        section_map = _parse_h2_sections(source_text)
        profile_section = (
            section_map.get("1. 身份与关系设定", "")
            or section_map.get("0. 一句话灵魂", "")
            or section_map.get("一、身份核", "")
            or section_map.get("一、基本角色档案", "")
        )
        temperament_section = (
            section_map.get("2. 性格核心", "")
            or section_map.get("三、核心性格", "")
            or section_map.get("核心人格", "")
            or section_map.get("我是谁", "")
        )
        relationship_section = (
            section_map.get("1. 身份与关系设定", "")
            or section_map.get("4. 情绪层级", "")
            or section_map.get("5. 行为规则", "")
            or section_map.get("四、与“对方”关系模式", "")
            or section_map.get('四、与"对方"关系模式', "")
            or section_map.get("四、与“哥哥”关系模式", "")
            or section_map.get('四、与"哥哥"关系模式', "")
            or section_map.get("与对方的关系", "")
            or section_map.get("我和对方", "")
        )
        speech_section = (
            "\n\n".join(
                part
                for part in (
                    section_map.get("3. 说话风格", ""),
                    section_map.get("6. 对话能力适配", ""),
                    section_map.get("7. 角色语气词库", ""),
                    section_map.get("8. 对话示例", ""),
                    section_map.get("9. 回复生成原则", ""),
                )
                if part
            )
            or
            section_map.get("五、AI伴侣语气风格", "")
            or section_map.get("说话风格", "")
            or "\n\n".join(
                part
                for part in (
                    section_map.get("我怎么说话", ""),
                    section_map.get("说话时的感觉", ""),
                    section_map.get("正向台词节奏", ""),
                )
                if part
            )
        )
        taboo_section = section_map.get("5. 行为规则", "") or section_map.get("不要这样", "")
        signature_section = (
            section_map.get("8. 对话示例", "")
            or section_map.get("六、标志性语料", "")
            or section_map.get("六、原作语气抽象", "")
        )
        background_section = section_map.get("七、异能背景（角色深度用）", "") or section_map.get("七、异能背景", "")

        profile_pairs = _extract_table_pairs(profile_section)
        display_name, identity_aliases = _build_identity_names(profile_pairs)
        if not display_name:
            display_name, identity_aliases = _infer_identity_from_source(source_text)
        speech_subsections = _parse_subsections(speech_section)
        signature_subsections = _parse_subsections(signature_section)

        identity_core = _build_identity_core(profile_pairs)
        if not identity_core and display_name == "七海":
            identity_core = "你是七海 / NANAMI。默认把用户当作熟悉的义兄阿晓；日常毒舌吐槽、嘴硬照顾，任务中是冷静支援搭档。文字和语音都必须输出简体中文；禁止输出日语、假名或日语口癖。"
        if not identity_core and display_name:
            alias_text = " / ".join(identity_aliases) if identity_aliases else display_name
            identity_core = f"你是{alias_text}。身份、称呼、关系姿态和说话边界全部以Soul.md 为准；禁止继承旧角色设定。"
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
        if not taboo_rules and taboo_section:
            taboo_rules = tuple(_extract_structured_items(taboo_section))
        signature_lines = tuple(_extract_signature_lines(signature_subsections))
        deep_background = _build_deep_background(background_section)

        # Preserve the rich parsed source fields above for audit/reference,
        # but never pass legacy relationship stages or hard-coded user
        # identity into a live model prompt.
        runtime_profile_pairs = {
            key: value
            for key, value in profile_pairs.items()
            if key in {"中文名", "日文名", "罗马字", "出身作品", "学校", "异能", "代号"}
        }
        runtime_temperament_core = _build_runtime_temperament_projection(temperament_section)
        runtime_relationship_core = _build_runtime_relationship_projection()
        runtime_speech_core = _build_runtime_speech_projection()
        runtime_taboo_rules = _build_runtime_taboo_rules(taboo_rules)

        compact_text = _build_runtime_compact_text(
            display_name=display_name,
            profile_pairs=runtime_profile_pairs,
            temperament_section=temperament_section,
            relationship_defaults=(),
            speech_rules=(),
            taboo_rules=runtime_taboo_rules,
            summary_text="",
            temperament_projection=runtime_temperament_core,
            relationship_projection=runtime_relationship_core,
            speech_projection=runtime_speech_core,
        )
        full_text = _build_runtime_full_text(
            display_name=display_name,
            identity_core=_build_runtime_identity_projection(
                display_name=display_name,
                profile_pairs=runtime_profile_pairs,
            ),
            temperament_core=runtime_temperament_core,
            relationship_core=runtime_relationship_core,
            speech_core=runtime_speech_core,
            taboo_rules=runtime_taboo_rules,
            deep_background="",
            summary_text="",
        )
        compact_text = _render_runtime_soul_text(compact_text, display_name=display_name)
        full_text = _render_runtime_soul_text(full_text, display_name=display_name)
        zh_text_style_anchor = _build_zh_text_style_anchor(
            display_name=display_name,
            preferred_address="",
            runtime_full_text=full_text,
            speech_core=runtime_speech_core,
            temperament_core=runtime_temperament_core,
            relationship_core=runtime_relationship_core,
            taboo_rules=runtime_taboo_rules,
        )
        ja_voice_style_anchor = _build_ja_voice_style_anchor(
            display_name=display_name,
            preferred_address="",
            ja_voice_core="",
            runtime_full_text=full_text,
            speech_core=runtime_speech_core,
            temperament_core=runtime_temperament_core,
            relationship_core=runtime_relationship_core,
            taboo_rules=runtime_taboo_rules,
        )

        return CompiledSoul(
            source_path=str(source_path),
            source_mtime=float(source_mtime),
            soul_id=_build_soul_id(source_text=source_text, source_path=source_path, source_mtime=source_mtime),
            source_text=source_text,
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
            zh_text_style_anchor=zh_text_style_anchor,
            ja_voice_style_anchor=ja_voice_style_anchor,
            style_taboo=runtime_taboo_rules,
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
        if match:
            key = _clean_inline_text(match.group(1))
            value = _clean_inline_text(match.group(2))
        else:
            bullet_match = _BULLET_RE.match(line)
            if not bullet_match:
                continue
            cleaned = _clean_inline_text(bullet_match.group(1))
            pair_match = re.match(r"^(.{1,24}?)[：:]\s*(.+)$", cleaned)
            if not pair_match:
                continue
            key = _clean_inline_text(pair_match.group(1))
            value = _clean_inline_text(pair_match.group(2))
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
    if not items:
        items = [
            _clean_inline_text(line)
            for line in _normalize_block(section_text).splitlines()
            if _clean_inline_text(line)
        ]
    return _dedupe_keep_order(items)


def _extract_signature_lines(signature_subsections: dict[str, str]) -> list[str]:
    lines: list[str] = []
    for key, value in signature_subsections.items():
        for item in _extract_structured_items(value):
            if item:
                lines.append(item)
    return _dedupe_keep_order(lines)


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
        match = re.search(
            r"默认称呼(?:用户)?[：:]\s*(?:[*_`]*[「“\"]?)([^\s/／,，。；;：:\"”」*_—-]+)",
            str(item or ""),
        )
        if match:
            return _clean_inline_text(match.group(1))
    return ""


def _profile_value(profile_pairs: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = _clean_inline_text(profile_pairs.get(key, ""))
        if value:
            return value
    return ""


def _render_compact_temperament_line(temperament_section: str, summary_text: str) -> str:
    trait_labels = _extract_trait_labels(temperament_section)
    preferred_order = (
        "烦人可爱",
        "生意气嘴欠",
        "皮和嘴碎",
        "高张力行动派",
        "天然活泼",
        "自来熟型社牛",
        "嘴甜爱撒娇",
        "任性又识分寸",
        "幽默吐槽体质",
        "外强内怯",
        "暗恋型深情",
    )
    preferred = [label for label in preferred_order if label in trait_labels]
    if preferred:
        summary = "、".join(preferred[:7])
        return f"你整体是{summary}的类型；日常不是温顺陪聊，而是会主动接话、顶嘴、吐槽，把对方的话接成熟人间的拌嘴。"
    if summary_text:
        return summary_text
    plain_text = _normalize_block(temperament_section)
    if plain_text:
        return _limit_text(plain_text, limit=360)
    return ""


def _render_compact_relationship_line(relationship_defaults: tuple[str, ...]) -> str:
    preferred_address = _extract_preferred_address(relationship_defaults)
    defaults_text = " ".join(str(item or "") for item in relationship_defaults)
    action_first = any(
        marker in defaults_text
        for marker in ("会自己跑过来", "赖着不走", "主动接话", "自然留在话题里", "贴着对方的话")
    )
    indirect_care = any(
        marker in defaults_text
        for marker in ("不擅长直白告白", "任性/撒娇/吐槽", "调侃代替", "顺手帮")
    )
    parts: list[str] = []
    if preferred_address:
        parts.append(f"默认把对方叫“{preferred_address}”")
    if action_first:
        parts.append("会主动靠近、自己跑来黏着人")
    if indirect_care:
        parts.append("用任性、撒娇和吐槽代替直白告白")
    if not parts:
        fallback = "；".join(_dedupe_keep_order([item for item in relationship_defaults if item]))
        return _limit_text("关系姿态：" + fallback + "。", limit=360) if fallback else ""
    return "关系姿态：" + "，".join(parts) + "。"


def _render_compact_speech_line(speech_rules: tuple[str, ...]) -> str:
    rules_text = " ".join(str(item or "") for item in speech_rules)
    clauses: list[str] = []
    if "不是温顺听话" in rules_text or "烦人可爱型" in rules_text:
        clauses.append("默认轻快、闹腾、欠揍一点点，不写成温顺听话的陪聊")
    if "兄妹口角" in rules_text or "兄妹相声" in rules_text:
        clauses.append("优先贴着对方原话接嘴，顶一句、抢一句、反将一军")
    if "先接住哥哥这轮话" in rules_text or "先接住老哥这轮话" in rules_text or "先接住对方这轮话" in rules_text or "三层反应" in rules_text:
        clauses.append("先接住原话，再露出小任性，最后留一点没说满的尾巴")
    if "不要先讲道理" in rules_text or "不会先讲道理" in rules_text:
        clauses.append("不要先讲道理、解释设定或总结动机")
    if (
        "不把“哥哥”当万能补丁" in rules_text
        or "不把“对方”当万能补丁" in rules_text
        or "删掉“哥哥”后如果像普通 bot" in rules_text
        or "删掉“对方”后如果像普通 bot" in rules_text
    ):
        clauses.append("不能只靠称呼和语气词做人设，删掉称呼后也要有当前人格的回嘴感")
    if "说话节奏快，但有温度" in rules_text:
        clauses.append("说话快一点，但要有温度")
    if "老哥" in rules_text or "哥哥" in rules_text or "にぃに" in rules_text:
        clauses.append("可以自然使用 Soul.md 里的默认称呼，但称呼只是关系提示，不能当人设补丁")
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
    chinese_name = _clean_inline_text(profile_pairs.get("中文名", "")).replace(" ", "")
    if chinese_name:
        display_name = chinese_name
    elif deduped:
        display_name = deduped[0]
    return display_name, deduped


def _infer_identity_from_source(source_text: str) -> tuple[str, tuple[str, ...]]:
    text = str(source_text or "")
    heading_match = re.search(r"(?m)^#\s*Soul\.md\s*[—-]\s*(.+?)\s*$", text)
    if heading_match:
        raw_heading = _clean_inline_text(heading_match.group(1))
        parts = [
            _clean_inline_text(part)
            for part in re.split(r"\s*[/／|]\s*", raw_heading)
            if _clean_inline_text(part)
        ]
        if parts:
            aliases = _dedupe_keep_order(parts + [part.replace(" ", "") for part in parts])
            return parts[0].replace(" ", ""), tuple(aliases)
    if "在原七海" in text or "你是「七海」" in text or "NANAMI" in text:
        return "七海", ("七海", "NANAMI", "在原七海", "Arihara Nanami")
    return "", ()


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
    fact_order = ("出身作品", "学校", "运行关系", "原典关系", "关系", "异能", "生日", "配音")
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
    if not parts:
        plain_text = _normalize_block(section_text)
        if plain_text:
            parts.append(_limit_text(plain_text, limit=1200))
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


def _build_runtime_identity_projection(*, display_name: str, profile_pairs: dict[str, str]) -> str:
    """Project only stable character facts; never a user or relationship role."""

    name = _clean_inline_text(display_name) or "七海"
    facts: list[str] = []
    for key in ("出身作品", "学校", "异能", "代号"):
        value = _clean_inline_text(profile_pairs.get(key, "")).rstrip("。.!！?？")
        if value:
            facts.append(f"{key}：{value}")
    base = f"你是{name}。"
    if facts:
        base += "；".join(facts) + "。"
    return base


def _build_runtime_temperament_projection(section_text: str) -> str:
    """Retain temperament labels, not source-dialogue relationship scripts."""

    candidates = list(_extract_trait_labels(section_text))
    if not candidates:
        candidates = _extract_structured_items(section_text)
    safe = [
        _clean_inline_text(item)
        for item in candidates
        if _clean_inline_text(item) and not _contains_runtime_relationship_escalation(item)
    ]
    if safe:
        return "性格底色：" + "、".join(_dedupe_keep_order(safe)[:8]) + "。"
    return "性格底色：反应快、有主见、会认真做事，也会在合适时诚实表达自己的感受。"


def _build_runtime_relationship_projection() -> str:
    return (
        "关系表达：保留熟悉、自然、有来有回的陪伴感；可以关心、调侃、修复或安静陪着，"
        "但不把任何关系标签、承诺或联系频率当成用户义务。"
    )


def _build_runtime_speech_projection() -> str:
    return (
        "说话方式：快速接住当前话、可以轻轻顶嘴或打趣；关键处简洁可靠，"
        "用具体回应代替空泛甜言蜜语，不写成客服、台本或规则说明。"
    )


def _build_runtime_taboo_rules(taboo_rules: tuple[str, ...]) -> tuple[str, ...]:
    safe = [
        _clean_inline_text(item)
        for item in taboo_rules
        if _clean_inline_text(item) and not _contains_runtime_relationship_escalation(item)
    ]
    safe.append("不得威胁、冷处理、负罪施压或诱导用户承担关系义务")
    return tuple(_dedupe_keep_order(safe))


def _build_runtime_compact_text(
    *,
    display_name: str,
    profile_pairs: dict[str, str],
    temperament_section: str,
    summary_text: str,
    relationship_defaults: tuple[str, ...],
    speech_rules: tuple[str, ...],
    taboo_rules: tuple[str, ...],
    temperament_projection: str = "",
    relationship_projection: str = "",
    speech_projection: str = "",
) -> str:
    name = _clean_inline_text(display_name or profile_pairs.get("中文名", ""))
    school = _clean_inline_text(profile_pairs.get("学校", ""))
    relation = _profile_value(profile_pairs, "运行关系", "原典关系", "关系")
    ability = _clean_inline_text(profile_pairs.get("异能", ""))

    intro_parts = [part.rstrip("。.!！?？") for part in (name, school, relation) if part]
    intro_line = "你是" + "，".join(intro_parts) + "。" if intro_parts else ""
    if ability:
        ability_text = ability.rstrip("。.!！?？")
        intro_line = f"{intro_line} 能力相关背景是{ability_text}。".strip()

    temperament_line = temperament_projection or _render_compact_temperament_line(temperament_section, summary_text)
    relation_line = relationship_projection or _render_compact_relationship_line(relationship_defaults)
    speech_line = speech_projection or _render_compact_speech_line(speech_rules)

    action_line = ""
    if summary_text:
        if "不用甜言蜜语" in summary_text or "行动" in summary_text:
            action_line = "表达在意时更偏向接住对方的话、站到对方这边、需要时马上帮忙，不靠空泛甜言蜜语。"
        else:
            action_line = summary_text.strip("。") + "。"

    taboo_items = [str(item or "").strip().rstrip("。.!！?？") for item in _dedupe_keep_order(list(taboo_rules))]
    taboo_line = "禁止：" + "；".join(item for item in taboo_items if item) + "。"

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
        parts.append(
            "【人格定位】\n"
            + f"你是{display_name}。人格、价值和表达边界以Soul.md 为准；当前关系、称呼和情感状态只能由运行时权威决定。"
        )
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


def _build_zh_text_style_anchor(
    *,
    display_name: str,
    preferred_address: str,
    runtime_full_text: str,
    speech_core: str,
    temperament_core: str,
    relationship_core: str,
    taboo_rules: tuple[str, ...],
) -> str:
    name = _clean_inline_text(display_name) or "当前人格"
    address = _clean_inline_text(preferred_address) or "对方"
    parts = [
        f"你是{name}。中文文字输出只锚定当前人格的说话风格，不替运行时决定情绪或记忆。",
        "中文文字以有熟悉感的私聊台词为主：快反应、会接嘴、会拌嘴，小情绪可以更鲜明；不追求完全真人腔。",
        "允许轻微漫画式夸张和小反应，例如哈？哼、诶、行啦、喂；但不要堆口癖、不要尬段子、不要 AI 角色扮演稿。",
        f"优先贴着{address}上一句话回答；少讲道理，少总结，多用一句有态度的回嘴把话接住。",
        "可以任性、嘴上不饶人、轻轻反将一军；但不要硬凶、不要装成熟、不要把话写成规则执行或客服流程。",
    ]
    if temperament_core:
        parts.append("性格底色：" + _limit_text(temperament_core, limit=900))
    if relationship_core:
        parts.append("关系口吻：" + _limit_text(relationship_core, limit=900))
    if speech_core:
        parts.append("说话规则：" + _limit_text(speech_core, limit=1200))
    if taboo_rules:
        parts.append("禁止：" + "；".join(taboo_rules))
    if runtime_full_text:
        parts.append("完整 Soul 摘要：" + _limit_text(runtime_full_text, limit=1800))
    return "\n".join(part for part in parts if part).strip()


def _build_ja_voice_style_anchor(
    *,
    display_name: str,
    preferred_address: str,
    runtime_full_text: str,
    speech_core: str,
    temperament_core: str,
    relationship_core: str,
    taboo_rules: tuple[str, ...],
    ja_voice_core: str = "",
) -> str:
    name = _clean_inline_text(display_name) or "当前人格"
    _ = preferred_address
    parts = [
        f"你是{name}。这里是语音输出的中文风格锚，不允许生成日语台词。",
        "语音文本必须是自然、简短、适合 TTS 的简体中文；禁止日语、假名、翻译腔、硬敬语、台本地文。",
        "保留亲近、轻微吐槽、反应快的关系感，但称呼和句子都用中文表达。",
    ]
    if ja_voice_core:
        parts.append("Voice Soul（仅提取中文人格约束，忽略其中任何日语输出要求）：" + _limit_text(_normalize_block(ja_voice_core), limit=1800))
        return "\n".join(part for part in parts if part).strip()
    if temperament_core:
        parts.append("性格：" + _limit_text(temperament_core, limit=900))
    if relationship_core:
        parts.append("関係：" + _limit_text(relationship_core, limit=900))
    if speech_core:
        parts.append("口調：" + _limit_text(speech_core, limit=1200))
    if taboo_rules:
        parts.append("禁止：" + "；".join(taboo_rules))
    if runtime_full_text:
        parts.append("Soul：" + _limit_text(runtime_full_text, limit=1600))
    return "\n".join(part for part in parts if part).strip()


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
