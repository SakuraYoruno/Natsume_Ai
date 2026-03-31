"""chat_stream_handler.py — 薄代理（Phase XIV v4 重构后）。

原 80KB 的旧链路 SSE 生成器已废弃。此文件仅保留：
1. gateway_orchestrator_handler.py 仍需的 prompt 构建函数
2. 向后兼容 re-export（指向新的独立模块）

待 gateway_orchestrator_handler.py 清理后，此文件可完全删除。
"""

from __future__ import annotations

import logging
import os
import string
from typing import Any, AsyncGenerator, Dict, Mapping, Optional

from host_brain.app.prompt_builder import (
    _truncate_for_prompt,
    repair_utf8_latin1_mojibake,
)

_logger = logging.getLogger("Natsume.chat_stream_handler")

# ---------------------------------------------------------------------------
# 向后兼容 re-export — 指向新的独立模块
# ---------------------------------------------------------------------------
from host_brain.app.chat_retrieval_helpers import (  # noqa: F401
    _run_retrieval,
    _build_recall_pack_inputs,
    _build_skipped_retrieval_summary,
    _build_degraded_retrieval_summary,
    _recall_pack_to_retrieval_evidence_items,
)
from host_brain.app.state_arbiter_queries import (  # noqa: F401
    _resolve_state_arbiter_world_fact,
    _resolve_state_arbiter_style_fact,
    _resolve_state_arbiter_bond_fact,
    _resolve_state_arbiter_episodic_fact,
)
from host_brain.app.vts_emotion_driver import (  # noqa: F401
    _drive_stream_vts_emotion,
    _wrap_vts_with_latency,
    _infer_emotion_tag_for_tts,
)


# ---------------------------------------------------------------------------
# gateway_orchestrator_handler.py 仍需的函数（待 gateway 清理后删除）
# ---------------------------------------------------------------------------

def _decode_partial_json_string(raw: str) -> str:
    """解码部分 JSON 字符串中的转义序列。"""
    out: list[str] = []
    idx = 0
    while idx < len(raw):
        ch = raw[idx]
        if ch != "\\" or idx + 1 >= len(raw):
            out.append(ch)
            idx += 1
            continue
        nxt = raw[idx + 1]
        mapped = {
            '"': '"',
            "\\": "\\",
            "/": "/",
            "b": "\b",
            "f": "\f",
            "n": "\n",
            "r": "\r",
            "t": "\t",
        }.get(nxt)
        out.append(mapped if mapped is not None else nxt)
        idx += 2
    return "".join(out)


def _extract_stream_render_text(raw_text: str) -> str:
    """从 LLM 流式输出中提取可见文本（处理 JSON 包装的 narrative.text）。"""
    normalized = str(raw_text or "")
    if not normalized:
        return ""
    stripped = normalized.lstrip()
    token = '"text":"'
    marker = stripped.find(token)
    if marker < 0:
        for prefix_marker in ('{"narrative"', '{\\"narrative\\"', '[{"narrative"', '[{\\"narrative\\"'):
            prefix_index = stripped.find(prefix_marker)
            if prefix_index > 0:
                visible_prefix = stripped[:prefix_index].rstrip()
                if visible_prefix:
                    return repair_utf8_latin1_mojibake(visible_prefix)
        if stripped.startswith("{"):
            return ""
        return repair_utf8_latin1_mojibake(normalized)

    cursor = marker + len(token)
    collected: list[str] = []
    escaped = False
    while cursor < len(stripped):
        ch = stripped[cursor]
        if ch == '"' and not escaped:
            break
        collected.append(ch)
        if escaped:
            escaped = False
        elif ch == "\\":
            escaped = True
        cursor += 1
    return repair_utf8_latin1_mojibake(_decode_partial_json_string("".join(collected)))


async def _build_stream_chat_prompt(
    *,
    runtime_input: Any,
) -> tuple[str, str, dict, dict, dict[str, Any]]:
    """构建流式聊天的 prompt、system_prompt 和生成参数。

    返回: (prompt_text, system_prompt, gen_params, emotion_context, prompt_meta)
    """
    from host_brain.app.chat_runtime_input import (  # noqa: PLC0415
        get_chat_runtime_prompt_context_observability,
        get_chat_runtime_prompt_context_validation,
        get_chat_runtime_emotion_snapshot,
    )
    from host_brain.app.chat_context_builder import (  # noqa: PLC0415
        build_chat_prompt_runtime_payload as _build_prompt_runtime_payload,
    )
    from host_brain.app.prompt_builder import (  # noqa: PLC0415
        render_node_generation_prompt,
        _build_node_role_instruction,
    )
    from host_brain.app.graph_config_builder import CHAT_DIRECT_NODE_ID  # noqa: PLC0415
    from host_brain.integrations.llm_helpers import adjust_gen_params_for_emotion  # noqa: PLC0415

    resolved_emotion_context = dict(getattr(runtime_input, "emotion_state", {}) or {})

    render_target_lang = str(getattr(runtime_input, "render_target_lang", "") or "ja").strip().lower() or "ja"
    prompt_payload = _build_prompt_runtime_payload(runtime_input=runtime_input)
    if resolved_emotion_context:
        prompt_payload = adjust_gen_params_for_emotion(prompt_payload, resolved_emotion_context)

    gen_params = dict(prompt_payload.get("gen_params") or {})
    prompt_text = render_node_generation_prompt(
        node_id=CHAT_DIRECT_NODE_ID,
        runtime_payload=prompt_payload,
    )
    prompt_meta = {
        "prompt_context_validation": get_chat_runtime_prompt_context_validation(prompt_payload),
        "prompt_context_observability": get_chat_runtime_prompt_context_observability(prompt_payload),
        "emotion_snapshot": get_chat_runtime_emotion_snapshot(prompt_payload),
    }
    system_prompt = _build_node_role_instruction(
        CHAT_DIRECT_NODE_ID,
        render_target_lang=render_target_lang,
    )
    return prompt_text, system_prompt, gen_params, resolved_emotion_context, prompt_meta
