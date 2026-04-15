"""chat_context_style.py — 风格绑定子模块。

从 chat_context_builder.py 拆分，负责节点风格绑定的构建与归一化。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from host_brain.app.dependencies import get_ledger
from host_brain.app.graph_config_builder import CHAT_DIRECT_NODE_ID

try:
    from host_brain.memory.canon_style_store import StyleCanonStore  # noqa: PLC0415
except Exception:  # pragma: no cover
    StyleCanonStore = None  # type: ignore[assignment]


def normalize_node_style_bindings(
    style_bindings: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for node_id, row in dict(style_bindings or {}).items():
        if not isinstance(row, Mapping):
            continue
        normalized[str(node_id)] = {
            "canon_id": str(row.get("canon_id") or "").strip(),
            "version": row.get("version"),
            "hash": str(row.get("hash") or "").strip(),
            "tone_only": bool(row.get("tone_only", True)),
            "unsafe_as_fact": bool(row.get("unsafe_as_fact", True)),
            "style_instruction": str(row.get("style_instruction") or "").strip(),
            "citation": str(row.get("citation") or "").strip(),
        }
    return normalized


def _load_v5_style_instruction(render_lang: str) -> str:
    """从 style_profile_v5.json 加载对应语言的渲染文本。

    返回空字符串表示 v5 不可用，应走旧版 fallback。
    """
    import json as _json

    raw_v5_path = str(
        os.environ.get("NATSUME_STYLE_V5_PATH")
        or os.environ.get("STYLE_V5_PATH")
        or os.environ.get("STYLE_V5_ROOT")
        or "host_brain/canon/style/style_profile_v5.json"
    ).strip()
    v5_path = Path(raw_v5_path)
    if v5_path.suffix.lower() != ".json":
        v5_path = v5_path / "style_profile_v5.json"

    if not v5_path.exists():
        return ""

    try:
        data = _json.loads(v5_path.read_text(encoding="utf-8"))
        key = "instruction_ja" if render_lang == "ja" else "instruction_cn"
        text = str(data.get(key) or "").strip()
        return text if len(text) > 100 else ""
    except Exception:
        return ""


def build_node_style_bindings(*, render_target_lang: str | None = None) -> dict[str, dict[str, Any]]:
    resolved_render_lang = str(render_target_lang or "").strip().lower()

    # ── Phase XIII v5 优先：从蒸馏产物直接读取风格指导文本 ──
    v5_instruction = _load_v5_style_instruction(resolved_render_lang)
    if v5_instruction:
        v5_entry = {
            "canon_id": f"natsume_ai_{'ja' if resolved_render_lang == 'ja' else 'cn'}_v5",
            "version": 5,
            "hash": "",
            "tone_only": True,
            "unsafe_as_fact": True,
            "style_instruction": v5_instruction,
            "citation": f"style_canon:v5_distilled:{'ja' if resolved_render_lang == 'ja' else 'cn'}",
        }
        return {
            "cortex_orchestrator": v5_entry,
            "core_reasoner_primary": v5_entry,
            "core_reasoner_secondary": v5_entry,
            CHAT_DIRECT_NODE_ID: v5_entry,
            "renderer_ja_c_style": v5_entry,
        }

    # ── Canonical v5 source ──
    style_root = str(os.environ.get("Natsume_STYLE_CANON_ROOT") or "host_brain/canon/style").strip()
    core_canon_id = str(os.environ.get("HOST_STYLE_CORE_CANON_ID") or "natsume_ai_cn").strip()
    explicit_render_canon_id = str(os.environ.get("HOST_STYLE_RENDER_CANON_ID") or "").strip()
    if explicit_render_canon_id:
        render_canon_id = explicit_render_canon_id
    elif resolved_render_lang == "zh":
        render_canon_id = (
            str(os.environ.get("HOST_STYLE_RENDER_CN_CANON_ID") or "").strip()
            or core_canon_id
            or "natsume_ai_cn"
        )
    else:
        render_canon_id = str(os.environ.get("HOST_STYLE_RENDER_JA_CANON_ID") or "natsume_ai_ja").strip()
    secondary_canon_id = str(os.environ.get("HOST_STYLE_SECONDARY_CANON_ID") or core_canon_id).strip()

    # 兼容路径：允许通过 StyleCanonStore seam 覆写
    legacy_store = None
    if callable(StyleCanonStore):
        try:
            legacy_store = StyleCanonStore(style_root=style_root, ledger=get_ledger())
        except Exception:
            legacy_store = None

    # T-143: 默认通过 SocialBrainAgent.resolve_style_canon() 族化调用
    social_agent = None
    if legacy_store is None:
        try:
            from host_brain.tribe.social_brain_agent import get_social_brain_agent  # noqa: PLC0415
            social_agent = get_social_brain_agent()
        except Exception:
            social_agent = None

    resolved: dict[str, dict[str, Any]] = {}
    mapping = [
        ("core_reasoner_primary", core_canon_id),
        ("core_reasoner_secondary", secondary_canon_id),
        (CHAT_DIRECT_NODE_ID, render_canon_id),
        ("renderer_ja_c_style", render_canon_id),
    ]
    for node_id, canon_id in mapping:
        if not canon_id:
            continue
        entry_dict = None
        if legacy_store is not None:
            try:
                entry = legacy_store.get(canon_id)
            except Exception:
                entry = None
            if entry is not None:
                entry_dict = {
                    "canon_id": getattr(entry, "canon_id", canon_id),
                    "version": getattr(entry, "version", 0),
                    "hash": getattr(entry, "hash", ""),
                    "tone_only": getattr(entry, "tone_only", True),
                    "unsafe_as_fact": getattr(entry, "unsafe_as_fact", True),
                    "content": getattr(entry, "content", ""),
                    "citation": f"style_canon:{canon_id}",
                }
        if entry_dict is None and social_agent is not None:
            try:
                entry_dict = social_agent.resolve_style_canon(
                    canon_id=canon_id,
                    style_root=style_root,
                )
            except Exception:
                entry_dict = None
            if not entry_dict or entry_dict.get("status") in ("NOT_FOUND", "ERROR", "NO_AGENT"):
                entry_dict = None
        if entry_dict is None:
            continue
        resolved[node_id] = {
            "canon_id": str(entry_dict.get("canon_id") or canon_id),
            "version": int(entry_dict.get("version") or 0),
            "hash": str(entry_dict.get("hash") or ""),
            "tone_only": bool(entry_dict.get("tone_only", True)),
            "unsafe_as_fact": bool(entry_dict.get("unsafe_as_fact", True)),
            "style_instruction": str(entry_dict.get("content") or ""),
            "citation": str(entry_dict.get("citation") or f"style_canon:{canon_id}"),
        }
    return resolved


__all__ = [
    "normalize_node_style_bindings",
    "build_node_style_bindings",
    "_load_v5_style_instruction",
]
