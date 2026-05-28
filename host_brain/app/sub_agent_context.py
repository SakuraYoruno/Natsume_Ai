"""Compact context envelope for sub-agent dispatch.

Sub-agents should receive a bounded task context, not raw chat history, raw
retrieval rows, or arbitrary runtime dictionaries. This module builds a small
envelope that can be passed through legacy handler APIs and rendered into the
ReAct prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any


MAX_RECENT_TURNS = 8
MAX_TURN_CHARS = 600
MAX_SUMMARY_CHARS = 900
MAX_FACTS = 8
MAX_FACT_CHARS = 280
MAX_EVIDENCE_ITEMS = 8
MAX_EVIDENCE_CHARS = 520
MAX_PROMPT_CHARS = 6_000
MAX_LEGACY_VALUE_CHARS = 500

_SENSITIVE_KEY_RE = re.compile(
    r"(api[_-]?key|secret|token|cookie|authorization|password|passwd|credential|密钥|密码)",
    re.IGNORECASE,
)
_BULKY_CONTEXT_KEYS = {
    "recent_turns",
    "history_turns",
    "conversation_history",
    "conversation_window",
    "older_summary",
    "mid_term_summary",
    "memory_packet",
    "memory_recall_packet",
    "surface_memory_recall_packet",
    "episode_recall_packet",
    "retrieval_evidence_items",
    "retrieval_summary",
    "evidence_pack",
    "raw_history",
    "raw_logs",
    "retrieval_rows",
    "topk",
    "agent_context_envelope",
}


@dataclass(frozen=True)
class AgentContextEnvelope:
    schema_version: str
    agent_id: str
    request_id: str
    session_id: str
    task_brief: str
    user_message: str
    conversation_window: dict[str, Any] = field(default_factory=dict)
    memory_packet: dict[str, Any] = field(default_factory=dict)
    semantic_facts: list[str] = field(default_factory=list)
    evidence_pack: dict[str, Any] = field(default_factory=dict)
    tool_budget: dict[str, Any] = field(default_factory=dict)
    forbidden_context: list[str] = field(default_factory=list)
    scratchpad_summary: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    trace: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "agent_id": self.agent_id,
            "request_id": self.request_id,
            "session_id": self.session_id,
            "task_brief": self.task_brief,
            "user_message": self.user_message,
            "conversation_window": dict(self.conversation_window),
            "memory_packet": dict(self.memory_packet),
            "semantic_facts": list(self.semantic_facts),
            "evidence_pack": dict(self.evidence_pack),
            "tool_budget": dict(self.tool_budget),
            "forbidden_context": list(self.forbidden_context),
            "scratchpad_summary": self.scratchpad_summary,
            "metadata": dict(self.metadata),
            "trace": dict(self.trace),
        }


def build_agent_context_envelope(
    *,
    agent_id: str,
    task: str,
    user_message: str = "",
    request_id: str = "",
    session_id: str = "",
    context: Mapping[str, Any] | None = None,
    timeout_ms: int | None = None,
) -> AgentContextEnvelope:
    raw = dict(context or {})
    trace: dict[str, Any] = {
        "source_keys": sorted(str(key) for key in raw.keys()),
        "truncated_fields": [],
        "dropped_legacy_keys": sorted(key for key in raw.keys() if str(key) in _BULKY_CONTEXT_KEYS),
    }
    conversation_window = _build_conversation_window(raw, trace=trace)
    memory_packet = _build_memory_packet(raw, trace=trace)
    semantic_facts = _semantic_facts(raw, memory_packet=memory_packet, trace=trace)
    evidence_pack = _build_evidence_pack(raw, trace=trace)
    tool_budget = _build_tool_budget(raw, timeout_ms=timeout_ms)
    forbidden_context = _forbidden_context(raw)
    metadata = _metadata_projection(raw, trace=trace)
    return AgentContextEnvelope(
        schema_version="Natsume.sub_agent.context.v1",
        agent_id=_text(agent_id, limit=80, trace=trace, field="agent_id"),
        request_id=_text(request_id, limit=120, trace=trace, field="request_id"),
        session_id=_text(session_id, limit=160, trace=trace, field="session_id"),
        task_brief=_text(task, limit=1_200, trace=trace, field="task_brief"),
        user_message=_text(user_message or task, limit=1_200, trace=trace, field="user_message"),
        conversation_window=conversation_window,
        memory_packet=memory_packet,
        semantic_facts=semantic_facts,
        evidence_pack=evidence_pack,
        tool_budget=tool_budget,
        forbidden_context=forbidden_context,
        scratchpad_summary=_text(raw.get("scratchpad_summary"), limit=700, trace=trace, field="scratchpad_summary"),
        metadata=metadata,
        trace=trace,
    )


def project_legacy_context_for_sub_agent(context: Mapping[str, Any] | None) -> dict[str, Any]:
    """Keep only small legacy fields for old handlers; bulky data lives in the envelope."""

    projected: dict[str, Any] = {}
    for key, value in dict(context or {}).items():
        key_text = str(key or "").strip()
        if not key_text or key_text in _BULKY_CONTEXT_KEYS:
            continue
        if key_text in {"gateway", "_gateway"}:
            projected[key_text] = value
            continue
        if _SENSITIVE_KEY_RE.search(key_text):
            projected[key_text] = "[REDACTED]"
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            projected[key_text] = _text(value, limit=MAX_LEGACY_VALUE_CHARS)
            continue
        if isinstance(value, Mapping):
            small = _small_mapping(value, depth=2)
            if small:
                projected[key_text] = small
            continue
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            small_list = [_small_value(item, depth=1) for item in list(value)[:8]]
            small_list = [item for item in small_list if item not in (None, "", [], {})]
            if small_list:
                projected[key_text] = small_list
    return projected


def hydrate_sub_agent_context(
    context: Mapping[str, Any] | None,
    *,
    request_id: str,
    session_id: str,
    task: str = "",
    user_message: str = "",
    parent_agent_id: str = "",
    timeout_ms: int | None = None,
    history_max_turns: int = MAX_RECENT_TURNS,
) -> dict[str, Any]:
    """Fill common sub-agent context anchors before the envelope is built.

    This is intentionally best-effort. If conversation history or retrieval
    state is unavailable, the caller still gets a small, valid context.
    """

    hydrated = dict(context or {})
    if parent_agent_id:
        hydrated.setdefault("parent_agent_id", _text(parent_agent_id, limit=80))
    hydrated.setdefault("source", "sub_agent_context_hydrator")
    if task:
        hydrated.setdefault("task_brief", _text(task, limit=1_200))
    if user_message:
        hydrated.setdefault("user_message", _text(user_message, limit=1_200))
    if timeout_ms is not None:
        budget = dict(hydrated.get("tool_budget") or hydrated.get("agent_budget") or {})
        budget.setdefault("timeout_ms", int(timeout_ms))
        hydrated["tool_budget"] = budget

    retrieval_summary = hydrated.get("retrieval_summary")
    if isinstance(retrieval_summary, Mapping):
        packet = retrieval_summary.get("surface_memory_recall_packet")
        if isinstance(packet, Mapping) and "surface_memory_recall_packet" not in hydrated:
            hydrated["surface_memory_recall_packet"] = dict(packet)

    if session_id and not isinstance(hydrated.get("conversation_window"), Mapping) and "recent_turns" not in hydrated:
        window = _fetch_conversation_window(
            session_id=session_id,
            request_id=request_id,
            max_turns=max(1, int(history_max_turns or MAX_RECENT_TURNS)),
        )
        if window:
            hydrated["conversation_window"] = window
    return hydrated


def render_agent_context_envelope_for_prompt(context: Mapping[str, Any] | None) -> str:
    raw = dict(context or {})
    envelope = raw.get("agent_context_envelope")
    if not isinstance(envelope, Mapping):
        return ""
    compact = _drop_empty(dict(envelope))
    if not compact:
        return ""
    payload = json.dumps(compact, ensure_ascii=False, sort_keys=True)
    if len(payload) > MAX_PROMPT_CHARS:
        payload = payload[: MAX_PROMPT_CHARS - 1].rstrip() + "…"
    return (
        "## AgentContextEnvelope\n"
        "Use this bounded context only as task background. Do not assume raw history, "
        "raw logs, secrets, or unlisted retrieval rows are available.\n"
        f"{payload}"
    )


def _fetch_conversation_window(*, session_id: str, request_id: str, max_turns: int) -> dict[str, Any]:
    try:
        from host_brain.app.dependencies import get_conversation_history  # noqa: PLC0415

        window = get_conversation_history().fetch_window_for_messages(
            session_id=session_id,
            max_turns=max_turns,
            exclude_request_id=request_id or None,
            render_target_lang="zh",
        )
    except Exception:
        return {}
    turns = []
    for turn in list(getattr(window, "recent_turns", []) or []):
        role = _text(getattr(turn, "role", ""), limit=32)
        content = _text(getattr(turn, "content", ""), limit=MAX_TURN_CHARS)
        if content:
            turns.append({"role": role or "context", "content": content})
    return _drop_empty(
        {
            "recent_turns": turns[-MAX_RECENT_TURNS:],
            "older_summary": _text(getattr(window, "older_summary", ""), limit=MAX_SUMMARY_CHARS),
            "older_turn_count": int(getattr(window, "older_turn_count", 0) or 0),
            "turns_injected": len(turns[-MAX_RECENT_TURNS:]),
            "fetched_turns": int(getattr(window, "fetched_count", 0) or len(turns)),
        }
    )


def _build_conversation_window(raw: Mapping[str, Any], *, trace: dict[str, Any]) -> dict[str, Any]:
    source = raw.get("conversation_window")
    recent: Any = None
    older_summary = raw.get("older_summary") or raw.get("mid_term_summary") or ""
    older_turn_count = 0
    if isinstance(source, Mapping):
        recent = source.get("recent_turns") or source.get("turns")
        older_summary = source.get("older_summary") or source.get("summary") or older_summary
        try:
            older_turn_count = int(source.get("older_turn_count") or 0)
        except (TypeError, ValueError):
            older_turn_count = 0
    if recent is None:
        recent = raw.get("recent_turns") or raw.get("history_turns") or []
    turns = _recent_turns(recent, trace=trace)
    return _drop_empty(
        {
            "recent_turns": turns,
            "older_summary": _text(older_summary, limit=MAX_SUMMARY_CHARS, trace=trace, field="conversation_window.older_summary"),
            "older_turn_count": older_turn_count,
            "turns_injected": len(turns),
        }
    )


def _build_memory_packet(raw: Mapping[str, Any], *, trace: dict[str, Any]) -> dict[str, Any]:
    packet = (
        raw.get("episode_recall_packet")
        or raw.get("surface_memory_recall_packet")
        or raw.get("memory_recall_packet")
        or raw.get("memory_packet")
        or {}
    )
    if not isinstance(packet, Mapping):
        return {}
    return _drop_empty(
        {
            "facts": _text_list(packet.get("facts"), limit=MAX_FACTS, max_chars=MAX_FACT_CHARS, trace=trace, field="memory_packet.facts"),
            "relationship_clues": _text_list(packet.get("relationship_clues"), limit=4, max_chars=MAX_FACT_CHARS, trace=trace, field="memory_packet.relationship_clues"),
            "style_anchor": _text_list(packet.get("style_anchor"), limit=4, max_chars=180, trace=trace, field="memory_packet.style_anchor"),
            "must_not_do": _text_list(packet.get("must_not_do"), limit=6, max_chars=180, trace=trace, field="memory_packet.must_not_do"),
            "evidence_refs": _text_list(packet.get("evidence_refs"), limit=MAX_FACTS, max_chars=180, trace=trace, field="memory_packet.evidence_refs"),
            "source_refs": _text_list(packet.get("source_refs"), limit=MAX_FACTS, max_chars=180, trace=trace, field="memory_packet.source_refs"),
            "confidence": packet.get("confidence"),
            "reason_code": _text(packet.get("reason_code"), limit=120),
        }
    )


def _semantic_facts(raw: Mapping[str, Any], *, memory_packet: Mapping[str, Any], trace: dict[str, Any]) -> list[str]:
    facts: list[str] = []
    facts.extend(_text_list(raw.get("semantic_facts"), limit=MAX_FACTS, max_chars=MAX_FACT_CHARS, trace=trace, field="semantic_facts"))
    facts.extend(_text_list(raw.get("long_term_facts"), limit=MAX_FACTS, max_chars=MAX_FACT_CHARS, trace=trace, field="long_term_facts"))
    for fact in list(memory_packet.get("facts") or []):
        text = _text(fact, limit=MAX_FACT_CHARS)
        if text.startswith(("长期记忆", "长期事实", "用户偏好", "稳定事实")):
            facts.append(text)
    return list(dict.fromkeys(facts))[:MAX_FACTS]


def _build_evidence_pack(raw: Mapping[str, Any], *, trace: dict[str, Any]) -> dict[str, Any]:
    source = raw.get("evidence_pack") or raw.get("retrieval_evidence_items") or []
    if isinstance(source, Mapping):
        raw_items = source.get("candidates") or source.get("items") or source.get("evidence_items") or []
        pack_id = _text(source.get("pack_id"), limit=120)
    else:
        raw_items = source
        pack_id = ""
    items: list[dict[str, Any]] = []
    if isinstance(raw_items, Sequence) and not isinstance(raw_items, (str, bytes, bytearray)):
        for index, item in enumerate(list(raw_items)[:MAX_EVIDENCE_ITEMS]):
            if not isinstance(item, Mapping):
                continue
            snippet = _text(item.get("snippet") or item.get("text") or item.get("content"), limit=MAX_EVIDENCE_CHARS, trace=trace, field=f"evidence_pack.items.{index}.snippet")
            source_ref = _text(item.get("source_ref") or item.get("evidence_ref"), limit=180)
            if not snippet and not source_ref:
                continue
            items.append(
                _drop_empty(
                    {
                        "doc_id": _text(item.get("doc_id") or item.get("memory_id"), limit=120),
                        "source_ref": source_ref,
                        "snippet": snippet,
                        "score": item.get("score"),
                        "confidence": item.get("confidence"),
                    }
                )
            )
    return _drop_empty({"pack_id": pack_id, "items": items, "item_count": len(items)})


def _build_tool_budget(raw: Mapping[str, Any], *, timeout_ms: int | None) -> dict[str, Any]:
    budget = raw.get("tool_budget") or raw.get("agent_budget") or {}
    payload = dict(budget) if isinstance(budget, Mapping) else {}
    if timeout_ms is not None:
        payload.setdefault("timeout_ms", int(timeout_ms))
    return _small_mapping(payload, max_items=12, depth=2)


def _forbidden_context(raw: Mapping[str, Any]) -> list[str]:
    defaults = [
        "raw_history_not_available",
        "raw_logs_not_available",
        "secret_values_redacted",
        "unbounded_retrieval_rows_not_available",
    ]
    extras = _text_list(raw.get("forbidden_context"), limit=8, max_chars=120)
    return list(dict.fromkeys([*defaults, *extras]))


def _metadata_projection(raw: Mapping[str, Any], *, trace: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "source",
        "parent_agent_id",
        "route_reason",
        "scene_type",
        "render_target_lang",
        "person_id",
        "platform",
        "reply_channel",
        "conversation_id",
    )
    projected: dict[str, Any] = {}
    for key in keys:
        if key in raw:
            projected[key] = _text(raw.get(key), limit=180, trace=trace, field=f"metadata.{key}")
    return _drop_empty(projected)


def _recent_turns(raw_turns: Any, *, trace: dict[str, Any]) -> list[dict[str, str]]:
    if isinstance(raw_turns, (str, bytes, bytearray)) or not isinstance(raw_turns, Sequence):
        return []
    rows = list(raw_turns)
    selected = rows[-MAX_RECENT_TURNS:]
    result: list[dict[str, str]] = []
    for index, raw in enumerate(selected):
        if isinstance(raw, Mapping):
            role = _text(raw.get("role") or raw.get("speaker"), limit=32)
            content = _text(raw.get("content") or raw.get("text"), limit=MAX_TURN_CHARS, trace=trace, field=f"conversation_window.recent_turns.{index}.content")
        else:
            role = "context"
            content = _text(raw, limit=MAX_TURN_CHARS, trace=trace, field=f"conversation_window.recent_turns.{index}.content")
        if content:
            result.append({"role": role or "context", "content": content})
    if len(rows) > len(selected):
        trace["recent_turns_dropped"] = len(rows) - len(selected)
    return result


def _text_list(raw: Any, *, limit: int, max_chars: int, trace: dict[str, Any] | None = None, field: str = "") -> list[str]:
    if raw is None:
        return []
    values = raw if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)) else [raw]
    rows: list[str] = []
    for index, item in enumerate(list(values)[: max(0, int(limit))]):
        text = _text(item, limit=max_chars, trace=trace, field=f"{field}.{index}" if field else "")
        if text:
            rows.append(text)
    return list(dict.fromkeys(rows))


def _small_mapping(raw: Mapping[str, Any], *, max_items: int = 16, depth: int = 1) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in list(dict(raw).items())[:max_items]:
        key_text = str(key or "").strip()
        if not key_text:
            continue
        if _SENSITIVE_KEY_RE.search(key_text):
            payload[key_text] = "[REDACTED]"
        else:
            small = _small_value(value, depth=depth)
            if small not in (None, "", [], {}):
                payload[key_text] = small
    return payload


def _small_value(value: Any, *, depth: int) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return _text(value, limit=MAX_LEGACY_VALUE_CHARS)
    if depth <= 0:
        return _text(value, limit=160)
    if isinstance(value, Mapping):
        return _small_mapping(value, max_items=12, depth=depth - 1)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        rows = [_small_value(item, depth=depth - 1) for item in list(value)[:8]]
        return [item for item in rows if item not in (None, "", [], {})]
    return _text(value, limit=160)


def _text(value: Any, *, limit: int, trace: dict[str, Any] | None = None, field: str = "") -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if limit > 0 and len(text) > limit:
        if trace is not None and field:
            trace.setdefault("truncated_fields", []).append(field)
        return text[: max(0, int(limit) - 1)].rstrip() + "…"
    return text


def _drop_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if item not in (None, "", [], {})
    }


__all__ = [
    "AgentContextEnvelope",
    "build_agent_context_envelope",
    "hydrate_sub_agent_context",
    "project_legacy_context_for_sub_agent",
    "render_agent_context_envelope_for_prompt",
]
