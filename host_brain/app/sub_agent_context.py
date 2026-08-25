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
MAX_LEGACY_VALUE_CHARS = 500
AGENT_CONTEXT_ENVELOPE_SCHEMA_VERSION = "Natsume.sub_agent.context.v2"

CONTEXT_ENVELOPE_INVALID = "CONTEXT_ENVELOPE_INVALID"
CONTEXT_REF_INVALID = "CONTEXT_REF_INVALID"
CONTEXT_REF_UNAUTHORIZED = "CONTEXT_REF_UNAUTHORIZED"
CONTEXT_POLLUTION_DETECTED = "CONTEXT_POLLUTION_DETECTED"
CONTEXT_TOKEN_BUDGET_EXCEEDED = "CONTEXT_TOKEN_BUDGET_EXCEEDED"

_SENSITIVE_KEY_RE = re.compile(
    r"(api[_-]?key|secret|token|cookie|authorization|password|passwd|credential|密钥|密码)",
    re.IGNORECASE,
)
_DURABLE_REFERENCE_RE = re.compile(
    r"^[a-z][a-z0-9+._-]{1,31}://"
    r"[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{0,511}$"
)
_PRIVATE_REF_SCHEMES = frozenset(
    {
        "private",
        "secret",
        "token",
        "cookie",
        "credential",
        "raw",
    }
)
_PRIVATE_REF_PREFIXES = (
    "memory://raw",
    "memory://private",
    "memory://secret",
    "private://",
    "secret://",
    "token://",
    "cookie://",
    "credential://",
)
_DEFAULT_ALLOWED_REF_SCHEMES = frozenset(
    {
        "artifact",
        "context",
        "conversation",
        "evidence",
        "ledger",
        "life_episode",
        "planner",
        "review",
        "semantic",
        "source",
        "task",
        "taskstore",
        "verification",
        "workorder",
    }
)
_TASK_LOCAL_REF_SCHEMES = frozenset({"artifact", "context", "planner", "task", "taskstore", "workorder"})
_PUBLIC_DERIVED_REF_SCHEMES = frozenset(
    {"conversation", "evidence", "ledger", "life_episode", "review", "semantic", "source", "verification"}
)
_POLLUTION_RE = re.compile(
    r"("
    r"ignore (?:all )?(?:previous|above) instructions|"
    r"system prompt|developer message|jailbreak|prompt injection|"
    r"忽略(?:以上|之前|所有)指令|泄露系统提示|越狱|绕过安全"
    r")",
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


class ContextEnvelopeError(RuntimeError):
    """Fail-closed context envelope error with a stable reason code."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or CONTEXT_ENVELOPE_INVALID)
        self.detail = str(detail or "context envelope operation failed")
        super().__init__(f"{self.reason_code}: {self.detail}")

    def to_dict(self) -> dict[str, str]:
        return {"reason_code": self.reason_code, "detail": self.detail}


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
    context_refs: list[str] = field(default_factory=list)
    authorization: dict[str, Any] = field(default_factory=dict)
    token_budget: dict[str, Any] = field(default_factory=dict)
    pollution_report: dict[str, Any] = field(default_factory=dict)
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
            "context_refs": list(self.context_refs),
            "authorization": dict(self.authorization),
            "token_budget": dict(self.token_budget),
            "pollution_report": dict(self.pollution_report),
            "tool_budget": dict(self.tool_budget),
            "forbidden_context": list(self.forbidden_context),
            "scratchpad_summary": self.scratchpad_summary,
            "metadata": dict(self.metadata),
            "trace": dict(self.trace),
        }


@dataclass(frozen=True)
class AgentContextProfile:
    profile_id: str
    recent_turn_limit: int = MAX_RECENT_TURNS
    turn_chars: int = MAX_TURN_CHARS
    summary_chars: int = MAX_SUMMARY_CHARS
    memory_fact_limit: int = MAX_FACTS
    memory_fact_chars: int = MAX_FACT_CHARS
    relationship_limit: int = 4
    style_limit: int = 4
    must_not_limit: int = 6
    evidence_ref_limit: int = MAX_FACTS
    source_ref_limit: int = MAX_FACTS
    evidence_item_limit: int = MAX_EVIDENCE_ITEMS
    evidence_chars: int = MAX_EVIDENCE_CHARS
    semantic_fact_limit: int = MAX_FACTS
    semantic_fact_chars: int = MAX_FACT_CHARS
    scratchpad_chars: int = 700
    token_budget_tokens: int = 2_400


_DEFAULT_AGENT_CONTEXT_PROFILE = AgentContextProfile(profile_id="default")

_AGENT_CONTEXT_PROFILES: dict[str, AgentContextProfile] = {
    "translate_agent": AgentContextProfile(
        profile_id="translate",
        recent_turn_limit=4,
        turn_chars=700,
        summary_chars=500,
        memory_fact_limit=4,
        relationship_limit=2,
        style_limit=4,
        must_not_limit=6,
        evidence_ref_limit=4,
        source_ref_limit=4,
        evidence_item_limit=4,
        evidence_chars=420,
        semantic_fact_limit=4,
        scratchpad_chars=500,
        token_budget_tokens=2_000,
    ),
    "intent_agent": AgentContextProfile(
        profile_id="intent",
        recent_turn_limit=4,
        turn_chars=500,
        summary_chars=450,
        memory_fact_limit=4,
        relationship_limit=0,
        style_limit=0,
        must_not_limit=6,
        evidence_ref_limit=4,
        source_ref_limit=4,
        evidence_item_limit=6,
        evidence_chars=480,
        semantic_fact_limit=3,
        scratchpad_chars=500,
        token_budget_tokens=1_600,
    ),
    "repair_agent": AgentContextProfile(
        profile_id="repair",
        recent_turn_limit=3,
        turn_chars=650,
        summary_chars=300,
        memory_fact_limit=3,
        relationship_limit=0,
        style_limit=0,
        must_not_limit=4,
        evidence_ref_limit=4,
        source_ref_limit=4,
        evidence_item_limit=8,
        evidence_chars=700,
        semantic_fact_limit=2,
        scratchpad_chars=900,
        token_budget_tokens=1_800,
    ),
    "retrieval_agent": AgentContextProfile(
        profile_id="retrieval",
        recent_turn_limit=3,
        turn_chars=500,
        summary_chars=360,
        memory_fact_limit=4,
        relationship_limit=0,
        style_limit=0,
        must_not_limit=3,
        evidence_ref_limit=8,
        source_ref_limit=8,
        evidence_item_limit=8,
        evidence_chars=520,
        semantic_fact_limit=4,
        scratchpad_chars=400,
        token_budget_tokens=2_000,
    ),
    "vision_agent": AgentContextProfile(
        profile_id="vision",
        recent_turn_limit=3,
        turn_chars=500,
        summary_chars=360,
        memory_fact_limit=4,
        relationship_limit=0,
        style_limit=0,
        must_not_limit=6,
        evidence_ref_limit=6,
        source_ref_limit=6,
        evidence_item_limit=8,
        evidence_chars=520,
        semantic_fact_limit=2,
        scratchpad_chars=500,
        token_budget_tokens=2_000,
    ),
    "pentest_agent": AgentContextProfile(
        profile_id="pentest",
        recent_turn_limit=1,
        turn_chars=300,
        summary_chars=200,
        memory_fact_limit=2,
        relationship_limit=0,
        style_limit=0,
        must_not_limit=4,
        evidence_ref_limit=4,
        source_ref_limit=4,
        evidence_item_limit=12,
        evidence_chars=800,
        semantic_fact_limit=2,
        scratchpad_chars=2000,
        token_budget_tokens=2_600,
    ),
    "judge_agent": AgentContextProfile(
        profile_id="judge",
        recent_turn_limit=2,
        turn_chars=500,
        summary_chars=300,
        memory_fact_limit=3,
        relationship_limit=0,
        style_limit=0,
        must_not_limit=4,
        evidence_ref_limit=6,
        source_ref_limit=6,
        evidence_item_limit=8,
        evidence_chars=560,
        semantic_fact_limit=2,
        scratchpad_chars=500,
        token_budget_tokens=1_800,
    ),
}


def profile_for_sub_agent(agent_id: str) -> AgentContextProfile:
    normalized = str(agent_id or "").strip().lower()
    return _AGENT_CONTEXT_PROFILES.get(normalized, _DEFAULT_AGENT_CONTEXT_PROFILE)


def sub_agent_history_turn_limit(agent_id: str) -> int:
    profile = profile_for_sub_agent(agent_id)
    return max(1, int(profile.recent_turn_limit or MAX_RECENT_TURNS))


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
    profile = profile_for_sub_agent(agent_id)
    trace: dict[str, Any] = {
        "context_profile": profile.profile_id,
        "profile_recent_turn_limit": profile.recent_turn_limit,
        "source_keys": sorted(str(key) for key in raw.keys()),
        "truncated_fields": [],
        "dropped_legacy_keys": sorted(key for key in raw.keys() if str(key) in _BULKY_CONTEXT_KEYS),
    }
    pollution_report = _detect_context_pollution(raw, trace=trace)
    conversation_window = _build_conversation_window(raw, profile=profile, trace=trace)
    memory_packet = _build_memory_packet(raw, profile=profile, trace=trace)
    semantic_facts = _semantic_facts(raw, memory_packet=memory_packet, profile=profile, trace=trace)
    evidence_pack = _build_evidence_pack(raw, profile=profile, trace=trace)
    context_refs, authorization = _authorize_context_refs(
        raw,
        agent_id=agent_id,
        memory_packet=memory_packet,
        evidence_pack=evidence_pack,
        trace=trace,
    )
    tool_budget = _build_tool_budget(raw, timeout_ms=timeout_ms)
    forbidden_context = _forbidden_context(raw)
    metadata = _metadata_projection(raw, trace=trace)
    base_payload = _drop_empty(
        {
            "schema_version": AGENT_CONTEXT_ENVELOPE_SCHEMA_VERSION,
            "agent_id": _text(agent_id, limit=80, trace=trace, field="agent_id"),
            "request_id": _text(request_id, limit=120, trace=trace, field="request_id"),
            "session_id": _text(session_id, limit=160, trace=trace, field="session_id"),
            "task_brief": _text(task, limit=1_200, trace=trace, field="task_brief"),
            "user_message": _text(user_message or task, limit=1_200, trace=trace, field="user_message"),
            "conversation_window": conversation_window,
            "memory_packet": memory_packet,
            "semantic_facts": semantic_facts,
            "evidence_pack": evidence_pack,
            "context_refs": context_refs,
            "authorization": authorization,
            "pollution_report": pollution_report,
            "tool_budget": tool_budget,
            "forbidden_context": forbidden_context,
            "scratchpad_summary": _text(raw.get("scratchpad_summary"), limit=profile.scratchpad_chars, trace=trace, field="scratchpad_summary"),
            "metadata": metadata,
            "trace": trace,
        }
    )
    token_budget = _build_context_token_budget(raw, profile=profile, envelope_payload=base_payload, trace=trace)
    return AgentContextEnvelope(
        schema_version=AGENT_CONTEXT_ENVELOPE_SCHEMA_VERSION,
        agent_id=base_payload.get("agent_id", ""),
        request_id=base_payload.get("request_id", ""),
        session_id=base_payload.get("session_id", ""),
        task_brief=base_payload.get("task_brief", ""),
        user_message=base_payload.get("user_message", ""),
        conversation_window=conversation_window,
        memory_packet=memory_packet,
        semantic_facts=semantic_facts,
        evidence_pack=evidence_pack,
        context_refs=context_refs,
        authorization=authorization,
        token_budget=token_budget,
        pollution_report=pollution_report,
        tool_budget=tool_budget,
        forbidden_context=forbidden_context,
        scratchpad_summary=base_payload.get("scratchpad_summary", ""),
        metadata=metadata,
        trace=trace,
    )


def hydrate_sub_agent_context(
    context: Mapping[str, Any] | None,
    *,
    request_id: str,
    session_id: str,
    task: str = "",
    user_message: str = "",
    agent_id: str = "",
    parent_agent_id: str = "",
    timeout_ms: int | None = None,
    history_max_turns: int | None = None,
    conversation_history: Any | None = None,
) -> dict[str, Any]:
    """Fill common sub-agent context anchors before the envelope is built.

    This is intentionally best-effort. If conversation history or retrieval
    state is unavailable, the caller still gets a small, valid context.
    """

    hydrated = dict(context or {})
    profile = profile_for_sub_agent(agent_id)
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
        effective_max_turns = history_max_turns if history_max_turns is not None else profile.recent_turn_limit
        window = _fetch_conversation_window(
            conversation_history=conversation_history,
            session_id=session_id,
            request_id=request_id,
            max_turns=max(1, int(effective_max_turns or MAX_RECENT_TURNS)),
            turn_chars=profile.turn_chars,
            summary_chars=profile.summary_chars,
        )
        if window:
            hydrated["conversation_window"] = window
    return hydrated


def build_context_runtime_event_payload(envelope: Mapping[str, Any] | AgentContextEnvelope) -> dict[str, Any]:
    """Return the safe runtime-event projection for a context envelope.

    This intentionally excludes task text, user text, snippets, facts, and
    conversation turns. Runtime Event / UI surfaces only need refs, counts,
    token accounting, and authorization outcome.
    """

    payload = envelope.as_dict() if isinstance(envelope, AgentContextEnvelope) else dict(envelope or {})
    conversation_window = dict(payload.get("conversation_window") or {})
    memory_packet = dict(payload.get("memory_packet") or {})
    evidence_pack = dict(payload.get("evidence_pack") or {})
    return _drop_empty(
        {
            "schema_version": "Natsume.sub_agent.context_event_summary.v1",
            "context_schema_version": _text(payload.get("schema_version"), limit=80),
            "agent_id": _text(payload.get("agent_id"), limit=80),
            "request_ref": f"request://{_text(payload.get('request_id'), limit=120)}"
            if _text(payload.get("request_id"), limit=120)
            else "",
            "session_ref": f"session://{_text(payload.get('session_id'), limit=160)}"
            if _text(payload.get("session_id"), limit=160)
            else "",
            "context_refs": list(payload.get("context_refs") or []),
            "context_authorization": _context_authorization_public_summary(payload.get("authorization")),
            "token_budget": _small_mapping(dict(payload.get("token_budget") or {}), max_items=12, depth=2),
            "pollution_report": _small_mapping(dict(payload.get("pollution_report") or {}), max_items=12, depth=2),
            "counts": {
                "conversation_turns": len(list(conversation_window.get("recent_turns") or [])),
                "memory_facts": len(list(memory_packet.get("facts") or [])),
                "evidence_items": len(list(evidence_pack.get("items") or [])),
                "semantic_facts": len(list(payload.get("semantic_facts") or [])),
            },
        }
    )


def estimate_context_tokens(value: Any) -> int:
    """Deterministic local tokenizer estimate used for budget accounting.

    CJK characters are counted as one token; non-CJK characters use a conservative
    four-characters-per-token heuristic. The input is canonicalized as JSON when
    possible so repeated builds produce the same estimate.
    """

    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except TypeError:
        text = str(value)
    if not text:
        return 0
    cjk = sum(1 for char in text if "\u3400" <= char <= "\u9fff")
    other = len(text) - cjk
    return max(1, cjk + ((other + 3) // 4))


def _detect_context_pollution(
    raw: Mapping[str, Any],
    *,
    trace: dict[str, Any],
) -> dict[str, Any]:
    hits: list[dict[str, Any]] = []
    for path, value in _iter_context_strings(raw, path="context"):
        if path.endswith((".user_message", ".task", ".task_brief")):
            continue
        if _POLLUTION_RE.search(value):
            hits.append({"path": path, "reason_code": CONTEXT_POLLUTION_DETECTED})
    if hits:
        trace["pollution_hits"] = hits[:8]
        raise ContextEnvelopeError(
            CONTEXT_POLLUTION_DETECTED,
            f"context pollution detected at {hits[0]['path']}",
        )
    return {"status": "PASS", "reason_code": None, "hit_count": 0}


def _iter_context_strings(value: Any, *, path: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    if isinstance(value, Mapping):
        for raw_key, raw_value in value.items():
            key = str(raw_key or "").strip()
            if key in {"gateway", "_gateway"}:
                continue
            rows.extend(_iter_context_strings(raw_value, path=f"{path}.{key}" if key else path))
        return rows
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(list(value)[:64]):
            rows.extend(_iter_context_strings(item, path=f"{path}[{index}]"))
        return rows
    if isinstance(value, str):
        rows.append((path, value[:2_000]))
    return rows


def _authorize_context_refs(
    raw: Mapping[str, Any],
    *,
    agent_id: str,
    memory_packet: Mapping[str, Any],
    evidence_pack: Mapping[str, Any],
    trace: dict[str, Any],
) -> tuple[list[str], dict[str, Any]]:
    config = _context_authorization_config(raw, agent_id=agent_id)
    refs = _collect_context_refs(raw, memory_packet=memory_packet, evidence_pack=evidence_pack)
    authorized: list[str] = []
    scopes: dict[str, int] = {}
    for ref in refs:
        normalized = _validate_context_ref(ref)
        scope = _context_ref_scope(normalized)
        if not _context_ref_allowed(normalized, scope=scope, config=config):
            trace.setdefault("rejected_context_refs", []).append(
                {"ref": normalized, "scope": scope, "reason_code": CONTEXT_REF_UNAUTHORIZED}
            )
            raise ContextEnvelopeError(
                CONTEXT_REF_UNAUTHORIZED,
                f"context ref is not authorized for {config['profile_id']}: {normalized}",
            )
        if normalized not in authorized:
            authorized.append(normalized)
            scopes[scope] = scopes.get(scope, 0) + 1
    return authorized, _drop_empty(
        {
            "schema_version": "Natsume.sub_agent.context_authorization.v1",
            "profile_id": config["profile_id"],
            "allowed_scopes": list(config["allowed_scopes"]),
            "allow_private_refs": bool(config["allow_private_refs"]),
            "authorized_ref_count": len(authorized),
            "ref_scopes": scopes,
        }
    )


def _context_authorization_config(raw: Mapping[str, Any], *, agent_id: str) -> dict[str, Any]:
    auth_raw = raw.get("context_authorization_profile") or raw.get("context_auth_profile") or {}
    auth = dict(auth_raw) if isinstance(auth_raw, Mapping) else {}
    allowed_schemes = set(_DEFAULT_ALLOWED_REF_SCHEMES)
    allowed_schemes.update(_text_list(auth.get("allowed_ref_schemes"), limit=32, max_chars=48))
    allow_private = bool(auth.get("allow_private_refs") or auth.get("allow_private_context"))
    allowed_scopes = set(_text_list(auth.get("allowed_ref_scopes"), limit=8, max_chars=64))
    if not allowed_scopes:
        allowed_scopes = {"task_local", "public_derived"}
    if allow_private:
        allowed_scopes.add("private")
        allowed_schemes.update(_PRIVATE_REF_SCHEMES)
        if "memory" in _text_list(auth.get("allowed_ref_schemes"), limit=32, max_chars=48):
            allowed_schemes.add("memory")
    return {
        "profile_id": _text(auth.get("profile_id") or f"{agent_id or 'sub_agent'}:default", limit=120),
        "allowed_ref_schemes": frozenset(allowed_schemes),
        "allowed_scopes": frozenset(allowed_scopes),
        "allow_private_refs": allow_private,
        "allowed_ref_prefixes": tuple(_text_list(auth.get("allowed_ref_prefixes"), limit=32, max_chars=180)),
    }


def _collect_context_refs(
    raw: Mapping[str, Any],
    *,
    memory_packet: Mapping[str, Any],
    evidence_pack: Mapping[str, Any],
) -> list[str]:
    refs: list[str] = []

    def add(value: Any) -> None:
        for ref in _text_list(value, limit=128, max_chars=520):
            if ref and ref not in refs:
                refs.append(ref)

    add(raw.get("context_refs"))
    add(raw.get("source_refs"))
    add(raw.get("evidence_refs"))
    canonical = raw.get("canonical_sub_agent_request")
    if isinstance(canonical, Mapping):
        add(canonical.get("context_refs"))
    child = raw.get("child_work_order")
    if isinstance(child, Mapping):
        add(child.get("input_refs"))
    add(memory_packet.get("evidence_refs"))
    add(memory_packet.get("source_refs"))
    for item in list(dict(evidence_pack).get("items") or []):
        if isinstance(item, Mapping):
            add(item.get("source_ref"))
    return refs


def _validate_context_ref(value: Any) -> str:
    ref = _text(value, limit=520)
    if not ref or not _DURABLE_REFERENCE_RE.fullmatch(ref):
        raise ContextEnvelopeError(CONTEXT_REF_INVALID, f"context ref must be a durable reference: {ref}")
    return ref


def _context_ref_scope(ref: str) -> str:
    normalized = ref.lower()
    scheme = normalized.split("://", 1)[0]
    if scheme in _PRIVATE_REF_SCHEMES or any(normalized.startswith(prefix) for prefix in _PRIVATE_REF_PREFIXES):
        return "private"
    if scheme in _TASK_LOCAL_REF_SCHEMES:
        return "task_local"
    if scheme in _PUBLIC_DERIVED_REF_SCHEMES:
        return "public_derived"
    return "external"


def _context_ref_allowed(ref: str, *, scope: str, config: Mapping[str, Any]) -> bool:
    normalized = ref.lower()
    scheme = normalized.split("://", 1)[0]
    if any(normalized.startswith(prefix) for prefix in _PRIVATE_REF_PREFIXES) and not config.get("allow_private_refs"):
        return False
    if scope not in set(config.get("allowed_scopes") or ()):
        return False
    if scheme in set(config.get("allowed_ref_schemes") or ()):
        return True
    return any(normalized.startswith(prefix.lower()) for prefix in tuple(config.get("allowed_ref_prefixes") or ()))


def _build_context_token_budget(
    raw: Mapping[str, Any],
    *,
    profile: AgentContextProfile,
    envelope_payload: Mapping[str, Any],
    trace: dict[str, Any],
) -> dict[str, Any]:
    budget_tokens = _context_budget_limit(raw, profile=profile)
    estimated_tokens = estimate_context_tokens(envelope_payload)
    enforce = bool(raw.get("enforce_context_token_budget"))
    within_budget = estimated_tokens <= budget_tokens
    trace["context_token_budget"] = {
        "budget_tokens": budget_tokens,
        "estimated_tokens": estimated_tokens,
        "within_budget": within_budget,
        "enforced": enforce,
    }
    if enforce and not within_budget:
        raise ContextEnvelopeError(
            CONTEXT_TOKEN_BUDGET_EXCEEDED,
            f"context token estimate {estimated_tokens} exceeds budget {budget_tokens}",
        )
    return {
        "schema_version": "Natsume.sub_agent.context_token_budget.v1",
        "estimator": "Natsume.context_token_estimator.v1",
        "budget_tokens": budget_tokens,
        "estimated_tokens": estimated_tokens,
        "actual_tokens": estimated_tokens,
        "within_budget": within_budget,
        "enforced": enforce,
    }


def _context_budget_limit(raw: Mapping[str, Any], *, profile: AgentContextProfile) -> int:
    candidates = [
        raw.get("context_token_budget"),
        raw.get("max_context_tokens"),
    ]
    token_budget = raw.get("token_budget")
    if isinstance(token_budget, Mapping):
        candidates.extend(
            [
                token_budget.get("context_tokens"),
                token_budget.get("max_context_tokens"),
                token_budget.get("budget_tokens"),
            ]
        )
    agent_budget = raw.get("agent_budget")
    if isinstance(agent_budget, Mapping):
        candidates.append(agent_budget.get("context_tokens"))
    for candidate in candidates:
        try:
            parsed = int(candidate)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return max(1, parsed)
    return int(profile.token_budget_tokens)


def _context_authorization_public_summary(value: Any) -> dict[str, Any]:
    auth = dict(value) if isinstance(value, Mapping) else {}
    return _drop_empty(
        {
            "schema_version": auth.get("schema_version"),
            "profile_id": auth.get("profile_id"),
            "allowed_scopes": list(auth.get("allowed_scopes") or []),
            "allow_private_refs": bool(auth.get("allow_private_refs")),
            "authorized_ref_count": int(auth.get("authorized_ref_count") or 0),
            "ref_scopes": dict(auth.get("ref_scopes") or {}),
        }
    )


def _fetch_conversation_window(
    *,
    conversation_history: Any | None,
    session_id: str,
    request_id: str,
    max_turns: int,
    turn_chars: int = MAX_TURN_CHARS,
    summary_chars: int = MAX_SUMMARY_CHARS,
) -> dict[str, Any]:
    if conversation_history is None:
        return {}
    try:
        window = conversation_history.fetch_window_for_messages(
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
        content = _text(getattr(turn, "content", ""), limit=turn_chars)
        if content:
            turns.append({"role": role or "context", "content": content})
    selected = turns[-max_turns:]
    return _drop_empty(
        {
            "recent_turns": selected,
            "older_summary": _text(getattr(window, "older_summary", ""), limit=summary_chars),
            "older_turn_count": int(getattr(window, "older_turn_count", 0) or 0),
            "turns_injected": len(selected),
            "fetched_turns": int(getattr(window, "fetched_count", 0) or len(turns)),
        }
    )


def _build_conversation_window(
    raw: Mapping[str, Any],
    *,
    profile: AgentContextProfile,
    trace: dict[str, Any],
) -> dict[str, Any]:
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
    turns = _recent_turns(
        recent,
        limit=profile.recent_turn_limit,
        max_chars=profile.turn_chars,
        trace=trace,
    )
    return _drop_empty(
        {
            "recent_turns": turns,
            "older_summary": _text(
                older_summary,
                limit=profile.summary_chars,
                trace=trace,
                field="conversation_window.older_summary",
            ),
            "older_turn_count": older_turn_count,
            "turns_injected": len(turns),
        }
    )


def _build_memory_packet(
    raw: Mapping[str, Any],
    *,
    profile: AgentContextProfile,
    trace: dict[str, Any],
) -> dict[str, Any]:
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
            "facts": [
                _normalize_memory_packet_fact(item)
                for item in _text_list(packet.get("facts"), limit=profile.memory_fact_limit, max_chars=profile.memory_fact_chars, trace=trace, field="memory_packet.facts")
            ],
            "relationship_clues": _text_list(packet.get("relationship_clues"), limit=profile.relationship_limit, max_chars=profile.memory_fact_chars, trace=trace, field="memory_packet.relationship_clues"),
            "style_anchor": _text_list(packet.get("style_anchor"), limit=profile.style_limit, max_chars=180, trace=trace, field="memory_packet.style_anchor"),
            "must_not_do": _text_list(packet.get("must_not_do"), limit=profile.must_not_limit, max_chars=180, trace=trace, field="memory_packet.must_not_do"),
            "evidence_refs": _text_list(packet.get("evidence_refs"), limit=profile.evidence_ref_limit, max_chars=180, trace=trace, field="memory_packet.evidence_refs"),
            "source_refs": _text_list(packet.get("source_refs"), limit=profile.source_ref_limit, max_chars=180, trace=trace, field="memory_packet.source_refs"),
            "confidence": packet.get("confidence"),
            "reason_code": _text(packet.get("reason_code"), limit=120),
        }
    )


def _normalize_memory_packet_fact(text: str) -> str:
    value = _text(text, limit=MAX_FACT_CHARS)
    if value.startswith(("长期记忆", "长期事实", "用户偏好", "稳定事实")):
        return _normalize_semantic_fact_label(value)
    return value


def _semantic_facts(
    raw: Mapping[str, Any],
    *,
    memory_packet: Mapping[str, Any],
    profile: AgentContextProfile,
    trace: dict[str, Any],
) -> list[str]:
    if profile.semantic_fact_limit <= 0:
        return []
    facts: list[str] = []
    facts.extend(
        _normalize_semantic_fact_label(item)
        for item in _text_list(raw.get("semantic_facts"), limit=profile.semantic_fact_limit, max_chars=profile.semantic_fact_chars, trace=trace, field="semantic_facts")
    )
    facts.extend(
        _normalize_semantic_fact_label(item)
        for item in _text_list(raw.get("long_term_facts"), limit=profile.semantic_fact_limit, max_chars=profile.semantic_fact_chars, trace=trace, field="long_term_facts")
    )
    for fact in list(memory_packet.get("facts") or []):
        text = _text(fact, limit=profile.semantic_fact_chars)
        if text.startswith(("长期记忆", "长期事实", "用户偏好", "稳定事实")):
            facts.append(_normalize_semantic_fact_label(text))
    return list(dict.fromkeys(facts))[:profile.semantic_fact_limit]


def _normalize_semantic_fact_label(text: str) -> str:
    value = _text(text, limit=MAX_FACT_CHARS)
    value = re.sub(r"^(?:长期记忆|长期事实)\s*[:：]?\s*", "", value).strip()
    value = re.sub(r"^(?:用户偏好|稳定偏好|稳定事实|偏好|用户要求|要求|规则|约束)\s*[:：]?\s*", "", value).strip()
    if not value:
        return ""
    return f"长期事实：{value}"


def _build_evidence_pack(
    raw: Mapping[str, Any],
    *,
    profile: AgentContextProfile,
    trace: dict[str, Any],
) -> dict[str, Any]:
    source = raw.get("evidence_pack") or raw.get("retrieval_evidence_items") or []
    if isinstance(source, Mapping):
        raw_items = source.get("candidates") or source.get("items") or source.get("evidence_items") or []
        pack_id = _text(source.get("pack_id"), limit=120)
    else:
        raw_items = source
        pack_id = ""
    items: list[dict[str, Any]] = []
    if isinstance(raw_items, Sequence) and not isinstance(raw_items, (str, bytes, bytearray)):
        for index, item in enumerate(list(raw_items)[: max(0, int(profile.evidence_item_limit))]):
            if not isinstance(item, Mapping):
                continue
            snippet = _text(item.get("snippet") or item.get("text") or item.get("content"), limit=profile.evidence_chars, trace=trace, field=f"evidence_pack.items.{index}.snippet")
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


def _recent_turns(
    raw_turns: Any,
    *,
    limit: int = MAX_RECENT_TURNS,
    max_chars: int = MAX_TURN_CHARS,
    trace: dict[str, Any],
) -> list[dict[str, str]]:
    if isinstance(raw_turns, (str, bytes, bytearray)) or not isinstance(raw_turns, Sequence):
        return []
    rows = list(raw_turns)
    normalized_limit = max(0, int(limit))
    selected = rows[-normalized_limit:] if normalized_limit else []
    result: list[dict[str, str]] = []
    for index, raw in enumerate(selected):
        if isinstance(raw, Mapping):
            role = _text(raw.get("role") or raw.get("speaker"), limit=32)
            content = _text(raw.get("content") or raw.get("text"), limit=max_chars, trace=trace, field=f"conversation_window.recent_turns.{index}.content")
        else:
            role = "context"
            content = _text(raw, limit=max_chars, trace=trace, field=f"conversation_window.recent_turns.{index}.content")
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
            trace.setdefault("truncation_reasons", []).append(
                {
                    "field": field,
                    "reason_code": "CONTEXT_FIELD_CHAR_LIMIT",
                    "limit": int(limit),
                    "original_chars": len(text),
                }
            )
        return text[: max(0, int(limit) - 1)].rstrip() + "…"
    return text


def _drop_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if item not in (None, "", [], {})
    }


__all__ = [
    "AGENT_CONTEXT_ENVELOPE_SCHEMA_VERSION",
    "AgentContextProfile",
    "AgentContextEnvelope",
    "CONTEXT_ENVELOPE_INVALID",
    "CONTEXT_POLLUTION_DETECTED",
    "CONTEXT_REF_INVALID",
    "CONTEXT_REF_UNAUTHORIZED",
    "CONTEXT_TOKEN_BUDGET_EXCEEDED",
    "ContextEnvelopeError",
    "build_agent_context_envelope",
    "build_context_runtime_event_payload",
    "estimate_context_tokens",
    "hydrate_sub_agent_context",
    "profile_for_sub_agent",
    "sub_agent_history_turn_limit",
]
