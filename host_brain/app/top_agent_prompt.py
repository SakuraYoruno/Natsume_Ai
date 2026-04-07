"""top_agent_prompt.py — 顶级 Agent prompt 构建（Phase XIV v4）。

核心策略：**复用旧链路完整的 prompt 构建管线**：
  build_chat_runtime_input()
    → build_chat_prompt_runtime_payload()   ← 会调用 build_prompt_intelligence()
       → 注入 predictive_state / self_reflection / bond_narrative /
         persona_expression_hint / inner_monologue / emotion_vector 等全部槽位
    → render_node_generation_prompt(node_id="top_agent")

与旧链路唯一的差异：
  1. node_id 使用 "top_agent"（走顶级 Agent 专属 role_instruction）
  2. 追加子 Agent 能力描述块
"""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping

from host_brain.app.prompt_template_store import (
    resolve_prompt_template_json_merged,
    resolve_prompt_template_text,
)
from host_brain.app.sub_agent_protocol import get_agent_capabilities

_logger = logging.getLogger("Natsume.top_agent_prompt")

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
TOP_AGENT_NODE_ID = "top_agent"
DEFAULT_CONTINUITY_RECENT_TURNS = 16
DEFAULT_CONTINUITY_RECENT_MAX_TOKENS = 6000
DEFAULT_CONTINUITY_OLDER_SUMMARY_MAX_TOKENS = 3000
DEFAULT_CONTINUITY_TOTAL_MAX_TOKENS = (
    DEFAULT_CONTINUITY_RECENT_MAX_TOKENS + DEFAULT_CONTINUITY_OLDER_SUMMARY_MAX_TOKENS
)
DEFAULT_CONTINUITY_FETCH_ROWS = 128


def _get_style_correction_block(render_target_lang: str) -> str:
    if str(render_target_lang or "").strip().lower() == "ja":
        return resolve_prompt_template_text("top_agent_prompt.style_correction.ja")
    return resolve_prompt_template_text("top_agent_prompt.style_correction.cn")


def _format_template_text(template_text: Any, **kwargs: Any) -> str:
    text = str(template_text or "")
    if not kwargs:
        return text
    try:
        return text.format_map({str(key): "" if value is None else str(value) for key, value in kwargs.items()})
    except Exception:
        return text


def _load_template_mapping(template_id: str) -> dict[str, Any]:
    raw = resolve_prompt_template_json_merged(template_id)
    return dict(raw) if isinstance(raw, Mapping) else {}


def _load_continuity_templates() -> dict[str, str]:
    raw = _load_template_mapping("top_agent_prompt.continuity_sections")
    return {
        "user_label": str(raw.get("user_label") or "User"),
        "assistant_label": str(raw.get("assistant_label") or "Assistant"),
        "recent_session_title": str(raw.get("recent_session_title") or "Recent session context:"),
        "recent_continuity_title": str(raw.get("recent_continuity_title") or "Recent continuity context:"),
        "earlier_summary_title": str(raw.get("earlier_summary_title") or "Earlier continuity summary:"),
    }


def _resolve_continuity_role_label(*, role: str, templates: Mapping[str, str]) -> str:
    return str(templates.get("user_label") or "User") if role == "user" else str(
        templates.get("assistant_label") or "Assistant"
    )


def _normalize_recent_context_limit(raw_value: Any) -> int:
    try:
        limit = int(raw_value)
    except (TypeError, ValueError):
        limit = 4
    return max(1, min(limit, 8))


def _coerce_payload_dict(raw_payload: Any) -> dict[str, Any]:
    if isinstance(raw_payload, Mapping):
        return dict(raw_payload)
    if isinstance(raw_payload, str):
        try:
            parsed = json.loads(raw_payload)
        except Exception:
            return {}
        if isinstance(parsed, Mapping):
            return dict(parsed)
    return {}


def _looks_corrupted_recent_preview(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return True
    replacement_count = normalized.count("�")
    question_count = normalized.count("?")
    if replacement_count >= 1:
        return True
    if question_count >= 4 and question_count >= max(4, len(normalized) // 3):
        return True
    return False


def _sanitize_recent_preview(*, text: str, render_target_lang: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""

    from host_brain.app.prompt_builder import (  # noqa: PLC0415
        _contains_kana,
        repair_utf8_latin1_mojibake,
    )

    repaired = repair_utf8_latin1_mojibake(normalized).strip()
    if _looks_corrupted_recent_preview(repaired):
        return ""

    if str(render_target_lang or "").strip().lower() == "zh" and _contains_kana(repaired):
        return ""

    return repaired


def _normalize_continuity_turn_limit(raw_value: Any) -> int:
    try:
        limit = int(raw_value)
    except (TypeError, ValueError):
        limit = DEFAULT_CONTINUITY_RECENT_TURNS
    return max(1, min(limit, 32))


def _fetch_continuity_rows(
    *,
    ledger: Any,
    session_id: str,
    person_id: str,
    request_id: str,
    fetch_limit: int,
) -> list[dict[str, Any]]:
    resolved_session_id = str(session_id or "").strip()
    resolved_person_id = str(person_id or "").strip()
    request_id_text = str(request_id or "").strip()
    rows: list[dict[str, Any]] = []
    conn = getattr(ledger, "_conn", None)
    if conn is not None:
        if resolved_person_id:
            fetched = conn.execute(
                """
                SELECT request_id, session_id, payload, ts_ms
                FROM voice_event_log
                WHERE event_type = 'INPUT_COMPLETED'
                  AND request_id <> ?
                ORDER BY ts_ms DESC
                LIMIT ?
                """,
                (request_id_text, int(fetch_limit)),
            ).fetchall()
            filtered_rows: list[dict[str, Any]] = []
            for row in list(fetched):
                current = dict(row)
                payload = _coerce_payload_dict(current.get("payload"))
                if str(payload.get("person_id") or "").strip() != resolved_person_id:
                    continue
                filtered_rows.append(current)
            rows = list(reversed(filtered_rows))
        else:
            fetched = conn.execute(
                """
                SELECT request_id, session_id, payload, ts_ms
                FROM voice_event_log
                WHERE session_id = ?
                  AND event_type = 'INPUT_COMPLETED'
                  AND request_id <> ?
                ORDER BY ts_ms DESC
                LIMIT ?
                """,
                (resolved_session_id, request_id_text, int(fetch_limit)),
            ).fetchall()
            rows = [dict(row) for row in reversed(list(fetched))]
    elif hasattr(ledger, "fetch_by_time_range"):
        fetched = ledger.fetch_by_time_range("voice_event_log", limit=2000)
        filtered: list[dict[str, Any]] = []
        for row in list(fetched or []):
            if not isinstance(row, Mapping):
                continue
            current = dict(row)
            row_session_id = str(current.get("session_id") or "").strip()
            payload = _coerce_payload_dict(current.get("payload"))
            row_person_id = str(payload.get("person_id") or "").strip()
            if resolved_person_id:
                if row_person_id != resolved_person_id:
                    continue
            elif row_session_id != resolved_session_id:
                continue
            if str(current.get("event_type") or "").strip() != "INPUT_COMPLETED":
                continue
            if str(current.get("request_id") or "").strip() == request_id_text:
                continue
            filtered.append(current)
        rows = filtered[-int(fetch_limit):]
    return rows


def _voice_rows_to_turns(*, rows: list[dict[str, Any]], render_target_lang: str) -> list[dict[str, str]]:
    turns: list[dict[str, str]] = []
    for row in list(rows or []):
        payload = _coerce_payload_dict(row.get("payload"))
        input_preview = _sanitize_recent_preview(
            text=str(payload.get("input_preview") or "").strip(),
            render_target_lang=render_target_lang,
        )
        reply_preview = _sanitize_recent_preview(
            text=str(payload.get("reply_preview") or "").strip(),
            render_target_lang=render_target_lang,
        )
        if input_preview:
            turns.append({"role": "user", "content": input_preview})
        if reply_preview:
            turns.append({"role": "assistant", "content": reply_preview})
    return turns


def _build_older_history_summary(
    turns: list[dict[str, Any]],
    *,
    render_target_lang: str,
    max_tokens: int = DEFAULT_CONTINUITY_OLDER_SUMMARY_MAX_TOKENS,
) -> str:
    from host_brain.app.recall import truncate_to_token_limit  # noqa: PLC0415

    continuity_templates = _load_continuity_templates()
    lines: list[str] = []
    for turn in list(turns or []):
        role = str(turn.get("role") or "user").strip().lower()
        content = _sanitize_recent_preview(
            text=str(turn.get("content") or turn.get("text") or "").strip(),
            render_target_lang=render_target_lang,
        )
        if not content:
            continue
        label = _resolve_continuity_role_label(role=role, templates=continuity_templates)
        compact = " ".join(content.split())
        if len(compact) > 120:
            compact = compact[:117].rstrip() + "..."
        lines.append(f"{label}: {compact}")
    if not lines:
        return ""
    text = "\n".join(lines)
    truncated, _, _ = truncate_to_token_limit(text, int(max_tokens))
    return truncated.strip()


def _build_recent_session_context_block(
    *,
    session_id: str,
    person_id: str | None = None,
    request_id: str,
    render_target_lang: str = "ja",
    max_turns: int | None = None,
) -> str:
    resolved_session_id = str(session_id or "").strip()
    resolved_person_id = str(person_id or "").strip()
    if not resolved_session_id and not resolved_person_id:
        return ""

    continuity_templates = _load_continuity_templates()
    turn_limit = _normalize_continuity_turn_limit(max_turns)
    request_id_text = str(request_id or "").strip()

    try:
        from host_brain.app.dependencies import get_ledger  # noqa: PLC0415

        ledger = get_ledger()
    except Exception as exc:
        _logger.debug("recent session context skipped: %s", exc)
        return ""

    try:
        rows = _fetch_continuity_rows(
            ledger=ledger,
            session_id=resolved_session_id,
            person_id=resolved_person_id,
            request_id=request_id_text,
            fetch_limit=max(turn_limit * 8, DEFAULT_CONTINUITY_FETCH_ROWS),
        )
    except Exception as exc:
        _logger.debug("recent session context query skipped: %s", exc)
        return ""

    turns = _voice_rows_to_turns(rows=rows, render_target_lang=render_target_lang)
    if not turns:
        return ""

    from host_brain.memory.context_compressor import ContextCompressor, STRATEGY_HYBRID  # noqa: PLC0415

    compressor = ContextCompressor(
        summary_provider=lambda history: _build_older_history_summary(
            history,
            render_target_lang=render_target_lang,
            max_tokens=DEFAULT_CONTINUITY_OLDER_SUMMARY_MAX_TOKENS,
        ),
        max_tokens=DEFAULT_CONTINUITY_TOTAL_MAX_TOKENS,
        recent_turns=turn_limit,
        pre_compress_save_enabled=False,
        episodic_writer=None,
        ledger=None,
    )
    compressed = compressor.compress(
        turns,
        max_tokens=DEFAULT_CONTINUITY_TOTAL_MAX_TOKENS,
        strategy=STRATEGY_HYBRID,
        session_id=(resolved_session_id or None),
    )

    raw_lines: list[str] = []
    for turn in list(compressed.turns or []):
        role = str(turn.get("role") or "user").strip().lower()
        content = _sanitize_recent_preview(
            text=str(turn.get("content") or "").strip(),
            render_target_lang=render_target_lang,
        )
        if not content:
            continue
        label = _resolve_continuity_role_label(role=role, templates=continuity_templates)
        raw_lines.append(f"{label}: {content}")

    parts: list[str] = []
    if raw_lines:
        title = (
            continuity_templates["recent_continuity_title"]
            if resolved_person_id
            else continuity_templates["recent_session_title"]
        )
        parts.append(title + "\n" + "\n".join(raw_lines))
    if compressed.summary:
        parts.append(continuity_templates["earlier_summary_title"] + "\n" + str(compressed.summary).strip())

    return "\n\n".join(part for part in parts if str(part).strip())


def build_top_agent_prompt(
    *,
    user_message: str,
    render_target_lang: str = "ja",
    emotion_context: dict[str, Any] | None = None,
    emotion_snapshot: dict[str, Any] | None = None,
    retrieval_summary: dict[str, Any] | None = None,
    retrieval_evidence_items: list[dict[str, Any]] | None = None,
    person_id: str = "",
    session_id: str = "",
    request_id: str = "",
    thread_id: str | None = None,
    bond_summary: dict[str, Any] | None = None,
    recall_slot_text: dict[str, str] | None = None,
    extra_context_prompt: str = "",
    include_runtime_payload: bool = False,
) -> tuple[str, str, dict[str, Any]] | tuple[str, str, dict[str, Any], dict[str, Any]]:
    """构建顶级 Agent 的 prompt。

    **走完整的 prompt 构建管线**（和旧链路 _build_stream_chat_prompt 等价）：
    ChatRuntimeInput → build_chat_prompt_runtime_payload() → render_node_generation_prompt()

    这确保 build_prompt_intelligence() 被调用，注入全部 20+ 个槽位。

    Returns:
        默认返回 `(prompt_text, system_prompt, gen_params)`；
        当 `include_runtime_payload=True` 时，额外返回 `runtime_payload`。
    """
    from host_brain.app.chat_runtime_input import build_chat_runtime_input  # noqa: PLC0415
    from host_brain.app.chat_context_builder import (  # noqa: PLC0415
        build_chat_prompt_runtime_payload,
    )
    from host_brain.app.prompt_builder import (  # noqa: PLC0415
        render_node_generation_prompt,
        _build_node_role_instruction,
    )
    from host_brain.integrations.llm_helpers import adjust_gen_params_for_emotion  # noqa: PLC0415

    emotion_context = emotion_context or {}
    emotion_snapshot = emotion_snapshot or {}

    # ── 1. 构建完整的 runtime_input ──────────────────────────────────
    runtime_input = build_chat_runtime_input(
        request_id=request_id,
        person_id=person_id,
        session_id=session_id,
        thread_id=thread_id,
        user_message=user_message,
        render_target_lang=render_target_lang,
        retrieval_summary=retrieval_summary or {"status": "SKIPPED"},
        retrieval_evidence_items=retrieval_evidence_items or [],
        emotion_state=emotion_context,
        emotion_snapshot=emotion_snapshot,
        recall_slot_text=recall_slot_text,
        bond_summary=bond_summary,
        scene_type="CHAT",
    )

    # ── 2. 走完整的 prompt 构建管线 ─────────────────────────────────
    # build_chat_prompt_runtime_payload 内部会调用 build_prompt_intelligence()，
    # 注入 predictive_state / self_reflection_pulse / bond_narrative /
    # persona_expression_hint / inner_monologue / emotion_vector 等全部数据。
    prompt_payload = build_chat_prompt_runtime_payload(runtime_input=runtime_input)

    # 情感调制 gen_params（和旧链路一致）
    if emotion_context:
        prompt_payload = adjust_gen_params_for_emotion(prompt_payload, emotion_context)

    gen_params = dict(prompt_payload.get("gen_params") or {})

    # ── 3. 渲染完整 prompt（包含全部 20+ 个注入槽位）─────────────────
    full_prompt = render_node_generation_prompt(
        node_id=TOP_AGENT_NODE_ID,
        runtime_payload=prompt_payload,
    )

    extra_context_text = str(extra_context_prompt or "").strip()
    if extra_context_text:
        full_prompt = full_prompt + "\n\n" + extra_context_text

    recent_session_context = _build_recent_session_context_block(
        person_id=person_id,
        session_id=session_id,
        request_id=request_id,
        render_target_lang=render_target_lang,
    )
    if recent_session_context:
        full_prompt = full_prompt + "\n\n" + recent_session_context

    # ── 4. 追加子 Agent 能力描述 ─────────────────────────────────────
    agent_block = _build_agent_capabilities_block()
    full_prompt = full_prompt + "\n\n" + agent_block

    # ── 5. 拆分 system_prompt 和 prompt_text ─────────────────────────
    # Phase XV：角色人设 + 风格蒸馏必须在 system_prompt 中（LLM 最高优先级），
    # 不走检索，确保每次对话都能生效。
    from host_brain.app.prompt_builder import _load_character_profile  # noqa: PLC0415
    from pathlib import Path as _Path  # noqa: PLC0415

    _cp_text = _load_character_profile("zh")

    # 常驻风格蒸馏（统一从 v5 canonical source 加载，避免与 style bindings 脱节）
    _style_text = ""
    try:
        from host_brain.app.chat_context_builder import _load_v5_style_instruction  # noqa: PLC0415

        _style_text = _load_v5_style_instruction(render_target_lang)
    except Exception:
        pass

    _node_role = _build_node_role_instruction(
        TOP_AGENT_NODE_ID,
        render_target_lang=render_target_lang,
    )
    _section_templates = _load_template_mapping("top_agent_prompt.sections")

    _parts = []
    if _cp_text:
        _parts.append(
            _format_template_text(
                _section_templates.get("profile_title", "【角色人设】\n")
            )
            + _cp_text
        )
    if _style_text:
        _parts.append(
            _format_template_text(
                _section_templates.get("style_title", "【说话风格（必须遵守）】\n")
            )
            + _style_text
        )
    _parts.append(_get_style_correction_block(render_target_lang))
    _parts.append(_node_role)
    system_prompt = "\n\n".join(_parts)

    prompt_text = (
        full_prompt
        + "\n\n"
        + _format_template_text(
            _section_templates.get("user_message_title", "【用户消息】\n")
        )
        + user_message
    )

    if include_runtime_payload:
        return prompt_text, system_prompt, gen_params, dict(prompt_payload)
    return prompt_text, system_prompt, gen_params


# ---------------------------------------------------------------------------
# 子 Agent 能力描述
# ---------------------------------------------------------------------------

def _build_agent_capabilities_block() -> str:
    """构建子 Agent 能力描述块。"""
    templates = _load_template_mapping("top_agent_prompt.delegate.block_templates")
    lines = [_format_template_text(templates.get("title"))]
    lines.append(_format_template_text(templates.get("delegate_rule")))
    lines.append(_format_template_text(templates.get("direct_reply_rule")))
    for agent_id, desc in get_agent_capabilities().items():
        lines.append(_format_template_text(templates.get("agent_line"), agent_id=agent_id, desc=desc))
    lines.append(_format_template_text(templates.get("delegate_example")))
    return "\n".join(lines)
