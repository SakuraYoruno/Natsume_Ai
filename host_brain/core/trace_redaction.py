"""Redacted trace contracts shared by runtime and public adapters.

Raw model reasoning remains ephemeral inside an executor.  Every value that can
be persisted or propagated is reduced to deterministic summaries, references,
and hashes by this module.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import re
from typing import Any


TRACE_SCHEMA_VERSION = "nle.trace.redacted.v1"
AGENT_OUTPUT_SCHEMA_VERSION = "nle.agent-output.redacted.v1"
TRACE_STEP_PUBLIC_FIELDS = frozenset(
    {
        "schema_version",
        "step",
        "decision_summary",
        "input_refs",
        "output_refs",
        "evidence_refs",
        "payload_hash",
        "action_type",
        "tool_id",
        "tool_ids",
        "outcome",
        "duration_ms",
        "ts_ms",
    }
)
TRACE_PUBLIC_FIELDS = frozenset(
    {
        "schema_version",
        "agent_id",
        "decision_summary",
        "input_refs",
        "output_refs",
        "evidence_refs",
        "payload_hash",
        "steps",
        "total_steps",
        "stopped_reason",
        "confidence",
        "risk_flags",
        "ts_ms",
    }
)

_RAW_REASONING_KEYS = frozenset(
    {
        "thought",
        "raw_thought",
        "raw_reasoning",
        "reasoning",
        "reasoning_content",
        "reasoning_trace",
        "chain_of_thought",
        "chainofthought",
        "cot",
    }
)
_SENSITIVE_BODY_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "cookies",
        "cookie_jar",
        "set_cookie",
        "secret",
        "secret_value",
        "token",
        "access_token",
        "refresh_token",
        "session_token",
        "api_key",
        "password",
        "passwd",
        "private_key",
        "private_body",
        "private_memory",
        "private_memory_body",
        "memory_private_body",
    }
)
_REF_KEYS = frozenset(
    {
        "artifact_ref",
        "body_ref",
        "document_ref",
        "evidence_ref",
        "evidence_refs",
        "file_ref",
        "input_ref",
        "input_refs",
        "output_ref",
        "output_refs",
        "request_ref",
        "source_ref",
        "source_refs",
    }
)
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:/#@+~-]{1,512}$")
_UNSAFE_TEXT_PATTERNS = (
    re.compile(
        r"(?i)\b(?:api[_-]?key|secret|token|access[_-]?token|refresh[_-]?token|password|passwd|authorization|cookie|set-cookie|private[_ -]?memory)\b\s*[:=]\s*[^\s,;]+"
    ),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{6,}"),
    re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"-----BEGIN(?: [A-Z]+)? PRIVATE KEY-----"),
    re.compile(r"(?i)<private[-_ ]?memory>|\[private[-_ ]?memory\]"),
    re.compile(
        r'''(?i)["']?(?:thought|raw_thought|raw_reasoning|reasoning_content|chain_of_thought)["']?\s*:'''
    ),
)


def stable_payload_hash(value: Any) -> str:
    """Return a stable SHA-256 for arbitrary JSON-like payloads."""
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8", errors="replace")
    return hashlib.sha256(encoded).hexdigest()


def content_ref(kind: str, value: Any) -> str:
    normalized_kind = re.sub(r"[^a-z0-9-]+", "-", str(kind or "trace").lower()).strip("-") or "trace"
    return f"{normalized_kind}://sha256/{stable_payload_hash(value)}"


def contains_sensitive_trace_content(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = _normalized_key(key)
            if normalized in _RAW_REASONING_KEYS or normalized in _SENSITIVE_BODY_KEYS:
                if item not in (None, "", [], {}):
                    return True
            if contains_sensitive_trace_content(item):
                return True
        return False
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(contains_sensitive_trace_content(item) for item in value)
    if isinstance(value, (bytes, bytearray)):
        text = bytes(value).decode("utf-8", errors="replace")
    elif isinstance(value, str):
        text = value
    else:
        return False
    return any(pattern.search(text) for pattern in _UNSAFE_TEXT_PATTERNS)


def public_decision_summary(
    value: Any,
    *,
    fallback: str = "Trace decision recorded.",
    max_chars: int = 280,
) -> str:
    """Build a publishable summary without echoing sensitive bodies."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return fallback
    if contains_sensitive_trace_content(text):
        return "Sensitive trace content withheld; inspect the authorized evidence reference."
    if len(text) <= max_chars:
        return text
    return text[: max(1, max_chars - 1)].rstrip() + "…"


def public_output_text(value: Any, *, fallback: str = "") -> str:
    """Return the complete publishable output, redacting sensitive bodies.

    Unlike a trace decision summary this channel is not shortened.  It carries
    the user-facing result that dispatchers need, independently from the trace
    scratchpad and its raw ephemeral reasoning.
    """
    text = str(value or "").strip()
    if not text:
        return fallback
    if contains_sensitive_trace_content(text):
        return "Sensitive output withheld; inspect the authorized output reference."
    return text


def build_public_agent_output(value: Any) -> dict[str, Any]:
    raw_text = str(value or "")
    return {
        "schema_version": AGENT_OUTPUT_SCHEMA_VERSION,
        "text": public_output_text(raw_text),
        "decision_summary": public_decision_summary(raw_text),
        "output_refs": [content_ref("agent-output", raw_text)] if raw_text else [],
        "payload_hash": stable_payload_hash(raw_text),
        "redacted": contains_sensitive_trace_content(raw_text),
    }


def normalize_trace_refs(values: Any) -> list[str]:
    """Keep only reference-shaped values and hash any unsafe reference."""
    if values is None:
        return []
    if isinstance(values, str):
        raw_values: Sequence[Any] = [values]
    elif isinstance(values, Sequence) and not isinstance(values, (bytes, bytearray)):
        raw_values = values
    else:
        raw_values = [values]

    refs: list[str] = []
    for value in raw_values:
        text = str(value or "").strip()
        if not text:
            continue
        if contains_sensitive_trace_content(text):
            candidate = content_ref("redacted", text)
        elif _SAFE_TOKEN_RE.fullmatch(text):
            candidate = text
        else:
            continue
        if candidate not in refs:
            refs.append(candidate)
    return refs


def refs_from_payload(value: Any) -> list[str]:
    refs: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            if _normalized_key(key) in _REF_KEYS:
                refs.extend(normalize_trace_refs(item))
            refs.extend(refs_from_payload(item))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            refs.extend(refs_from_payload(item))
    return list(dict.fromkeys(refs))


def build_redacted_trace_step(
    step_payload: Mapping[str, Any] | Any,
    *,
    input_refs: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Convert one ephemeral/legacy step to the strict public step allowlist."""
    raw = _as_step_mapping(step_payload)
    action = raw.get("action") if isinstance(raw.get("action"), Mapping) else {}
    tool_id = str(raw.get("tool_id") or action.get("tool") or "").strip()
    if not _SAFE_TOKEN_RE.fullmatch(tool_id):
        tool_id = ""
    raw_tool_ids = action.get("tool_ids") if isinstance(action, Mapping) else []
    if not isinstance(raw_tool_ids, Sequence) or isinstance(raw_tool_ids, (str, bytes, bytearray)):
        raw_tool_ids = []
    tool_ids = [
        text
        for text in (str(value or "").strip() for value in [tool_id, *raw_tool_ids])
        if text and _SAFE_TOKEN_RE.fullmatch(text)
    ]
    tool_ids = list(dict.fromkeys(tool_ids))
    default_action_type = "finish" if tool_id == "agent_finish" else ("tool_call" if tool_id else "final_answer")
    action_type = str(raw.get("action_type") or default_action_type).strip().lower()
    if action_type.endswith("_event"):
        action_type = "event"
    if action_type not in {"tool_call", "final_answer", "finish", "error", "event"}:
        action_type = "error"

    observation = raw.get("observation")
    outcome = _step_outcome(action_type=action_type, observation=observation)
    decision_summary = _step_decision_summary(
        action_type=action_type,
        tool_id=tool_id,
        outcome=outcome,
    )
    normalized_inputs = normalize_trace_refs(input_refs)
    normalized_inputs.extend(normalize_trace_refs(raw.get("input_refs")))
    normalized_inputs.extend(refs_from_payload(raw.get("tool_args")))
    normalized_inputs.extend(refs_from_payload(action.get("args") if isinstance(action, Mapping) else None))
    normalized_inputs = list(dict.fromkeys(normalized_inputs))

    output_refs: list[str] = normalize_trace_refs(raw.get("output_refs"))
    final_answer = raw.get("final_answer")
    if final_answer not in (None, ""):
        output_refs.append(content_ref("trace-output", final_answer))
    evidence_refs = normalize_trace_refs(raw.get("evidence_refs"))
    evidence_refs.extend(refs_from_payload(observation))
    if contains_sensitive_trace_content(raw):
        evidence_refs.append(content_ref("redacted", raw))

    public: dict[str, Any] = {
        "schema_version": TRACE_SCHEMA_VERSION,
        "step": _safe_int(raw.get("step"), default=0),
        "decision_summary": decision_summary,
        "input_refs": normalized_inputs,
        "output_refs": list(dict.fromkeys(output_refs)),
        "evidence_refs": list(dict.fromkeys(evidence_refs)),
        "payload_hash": stable_payload_hash(raw),
        "action_type": action_type,
        "tool_id": tool_id,
        "tool_ids": tool_ids,
        "outcome": outcome,
        "duration_ms": max(0, _safe_int(raw.get("duration_ms"), default=0)),
        "ts_ms": max(0, _safe_int(raw.get("ts_ms"), default=0)),
    }
    return {key: public[key] for key in TRACE_STEP_PUBLIC_FIELDS}


def build_redacted_trace(
    *,
    agent_id: str,
    steps: Sequence[Mapping[str, Any] | Any],
    stopped_reason: str,
    confidence: float,
    risk_flags: Sequence[str] | None = None,
    input_refs: Sequence[str] | None = None,
    final_answer: Any = "",
    total_steps: int | None = None,
    ts_ms: int = 0,
) -> dict[str, Any]:
    """Build a complete redacted trace using a fixed public field allowlist."""
    normalized_inputs = normalize_trace_refs(input_refs)
    redacted_steps = [
        build_redacted_trace_step(step, input_refs=normalized_inputs)
        for step in list(steps or [])
    ]
    safe_reason = _safe_code(stopped_reason, fallback="UNKNOWN")
    count = len(redacted_steps) if total_steps is None else max(0, int(total_steps))
    summary = public_decision_summary(
        final_answer,
        fallback=f"Agent trace stopped with {safe_reason} after {count} step(s).",
    )
    output_refs = [content_ref("trace-output", final_answer)] if final_answer not in (None, "") else []
    evidence_refs = list(
        dict.fromkeys(
            ref
            for step in redacted_steps
            for ref in list(step.get("evidence_refs") or [])
        )
    )
    raw_trace = {
        "agent_id": agent_id,
        "steps": [_as_step_mapping(step) for step in list(steps or [])],
        "stopped_reason": stopped_reason,
        "confidence": confidence,
        "risk_flags": list(risk_flags or []),
        "input_refs": list(input_refs or []),
        "final_answer": final_answer,
        "total_steps": count,
        "ts_ms": ts_ms,
    }
    public: dict[str, Any] = {
        "schema_version": TRACE_SCHEMA_VERSION,
        "agent_id": _safe_code(agent_id, fallback="agent_unknown", lowercase=True),
        "decision_summary": summary,
        "input_refs": normalized_inputs,
        "output_refs": output_refs,
        "evidence_refs": evidence_refs,
        "payload_hash": stable_payload_hash(raw_trace),
        "steps": redacted_steps,
        "total_steps": count,
        "stopped_reason": safe_reason,
        "confidence": _safe_confidence(confidence),
        "risk_flags": [
            value
            for value in (_safe_code(item, fallback="") for item in list(risk_flags or []))
            if value
        ],
        "ts_ms": max(0, int(ts_ms or 0)),
    }
    return {key: public[key] for key in TRACE_PUBLIC_FIELDS}


def redact_legacy_trace_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Read legacy scratchpad fields and emit only the current redacted schema."""
    raw = dict(payload or {})
    if raw.get("schema_version") == TRACE_SCHEMA_VERSION:
        return _sanitize_current_trace(raw)
    steps = raw.get("steps")
    if not isinstance(steps, Sequence) or isinstance(steps, (str, bytes, bytearray)):
        steps = []
    return build_redacted_trace(
        agent_id=str(raw.get("agent_id") or "agent_unknown"),
        steps=list(steps),
        stopped_reason=str(raw.get("stopped_reason") or "LEGACY"),
        confidence=_safe_confidence(raw.get("confidence")),
        risk_flags=list(raw.get("risk_flags") or []),
        input_refs=list(raw.get("input_refs") or []),
        final_answer=raw.get("decision_summary") or raw.get("final_answer") or "",
        total_steps=_safe_int(raw.get("total_steps"), default=len(steps)),
        ts_ms=_safe_int(raw.get("ts_ms"), default=0),
    )


def sanitize_public_payload(value: Any) -> Any:
    """Recursively remove raw reasoning and reference sensitive bodies.

    This is the compatibility boundary for historical AgentResult/job payloads.
    It deliberately preserves unknown non-sensitive fields so old readers keep
    working, while forbidden legacy fields can never be re-published.
    """
    if isinstance(value, Mapping):
        raw = dict(value)
        if _looks_like_trace_payload(raw):
            return redact_legacy_trace_payload(raw)

        public: dict[str, Any] = {}
        redaction_refs: list[str] = []
        for key, item in raw.items():
            normalized = _normalized_key(key)
            if normalized in _RAW_REASONING_KEYS or normalized in _SENSITIVE_BODY_KEYS:
                if item not in (None, "", [], {}):
                    redaction_refs.append(content_ref("redacted", item))
                continue
            if normalized == "steps" and isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
                public[str(key)] = [build_redacted_trace_step(step) for step in item]
                public.setdefault("trace_schema_version", TRACE_SCHEMA_VERSION)
                continue
            public[str(key)] = sanitize_public_payload(item)
        if redaction_refs:
            existing = normalize_trace_refs(public.get("evidence_refs"))
            public["evidence_refs"] = list(dict.fromkeys(existing + redaction_refs))
        return public
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [sanitize_public_payload(item) for item in value]
    if isinstance(value, (bytes, bytearray)):
        return content_ref("redacted", bytes(value).decode("utf-8", errors="replace"))
    if isinstance(value, str) and contains_sensitive_trace_content(value):
        return content_ref("redacted", value)
    return value


def read_decision_summary(payload: Mapping[str, Any] | None, *, fallback: str = "") -> str:
    """Read current or legacy summary fields without re-exposing raw thought."""
    raw = dict(payload or {})
    if raw.get("decision_summary") not in (None, ""):
        return public_decision_summary(raw.get("decision_summary"), fallback=fallback)
    if raw.get("final_answer") not in (None, ""):
        return public_decision_summary(raw.get("final_answer"), fallback=fallback)
    if raw.get("summary") not in (None, ""):
        return public_decision_summary(raw.get("summary"), fallback=fallback)
    if any(raw.get(key) not in (None, "") for key in _RAW_REASONING_KEYS):
        return fallback or "Legacy raw reasoning withheld."
    return fallback


def read_public_agent_output(payload: Mapping[str, Any] | None, *, fallback: str = "") -> str:
    """Read new output envelopes or historical output fields safely and fully."""
    raw = dict(payload or {})
    if raw.get("text") not in (None, ""):
        return public_output_text(raw.get("text"), fallback=fallback)
    if raw.get("final_answer") not in (None, ""):
        return public_output_text(raw.get("final_answer"), fallback=fallback)
    if raw.get("result_text") not in (None, ""):
        return public_output_text(raw.get("result_text"), fallback=fallback)
    return fallback


def trace_input_refs(context: Mapping[str, Any] | None, *, raw_input: Any = "") -> list[str]:
    ctx = dict(context or {})
    refs = normalize_trace_refs(ctx.get("input_refs"))
    for key, kind in (("request_id", "request"), ("session_id", "session"), ("work_order_id", "work-order")):
        value = str(ctx.get(key) or "").strip()
        if not value:
            continue
        if _SAFE_TOKEN_RE.fullmatch(value) and not contains_sensitive_trace_content(value):
            refs.append(f"{kind}://{value}")
        else:
            refs.append(content_ref(kind, value))
    refs.extend(refs_from_payload(ctx.get("agent_context_envelope")))
    if raw_input not in (None, ""):
        refs.append(content_ref("trace-input", raw_input))
    return list(dict.fromkeys(normalize_trace_refs(refs)))


def _looks_like_trace_payload(payload: Mapping[str, Any]) -> bool:
    return "steps" in payload and (
        payload.get("schema_version") == TRACE_SCHEMA_VERSION
        or any(key in payload for key in ("agent_id", "final_answer", "decision_summary"))
    )


def _sanitize_current_trace(payload: Mapping[str, Any]) -> dict[str, Any]:
    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, Sequence) or isinstance(raw_steps, (str, bytes, bytearray)):
        raw_steps = []
    steps = [_sanitize_current_trace_step(step) for step in raw_steps]
    raw_hash = str(payload.get("payload_hash") or "").strip().lower()
    public: dict[str, Any] = {
        "schema_version": TRACE_SCHEMA_VERSION,
        "agent_id": _safe_code(payload.get("agent_id"), fallback="agent_unknown", lowercase=True),
        "decision_summary": public_decision_summary(payload.get("decision_summary")),
        "input_refs": normalize_trace_refs(payload.get("input_refs")),
        "output_refs": normalize_trace_refs(payload.get("output_refs")),
        "evidence_refs": normalize_trace_refs(payload.get("evidence_refs")),
        "payload_hash": raw_hash if re.fullmatch(r"[0-9a-f]{64}", raw_hash) else stable_payload_hash(payload),
        "steps": steps,
        "total_steps": max(0, _safe_int(payload.get("total_steps"), default=len(steps))),
        "stopped_reason": _safe_code(payload.get("stopped_reason"), fallback="UNKNOWN"),
        "confidence": _safe_confidence(payload.get("confidence")),
        "risk_flags": [
            value
            for value in (_safe_code(item, fallback="") for item in list(payload.get("risk_flags") or []))
            if value
        ],
        "ts_ms": max(0, _safe_int(payload.get("ts_ms"), default=0)),
    }
    return {key: public[key] for key in TRACE_PUBLIC_FIELDS}


def _sanitize_current_trace_step(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("schema_version") != TRACE_SCHEMA_VERSION:
        return build_redacted_trace_step(value)
    payload = dict(value)
    action_type = str(payload.get("action_type") or "error").strip().lower()
    if action_type not in {"tool_call", "final_answer", "finish", "error", "event"}:
        action_type = "error"
    tool_id = str(payload.get("tool_id") or "").strip()
    if not _SAFE_TOKEN_RE.fullmatch(tool_id):
        tool_id = ""
    raw_tool_ids = payload.get("tool_ids")
    if not isinstance(raw_tool_ids, Sequence) or isinstance(raw_tool_ids, (str, bytes, bytearray)):
        raw_tool_ids = []
    tool_ids = [
        text
        for text in (str(item or "").strip() for item in raw_tool_ids)
        if text and _SAFE_TOKEN_RE.fullmatch(text)
    ]
    if tool_id and tool_id not in tool_ids:
        tool_ids.insert(0, tool_id)
    raw_hash = str(payload.get("payload_hash") or "").strip().lower()
    public: dict[str, Any] = {
        "schema_version": TRACE_SCHEMA_VERSION,
        "step": max(0, _safe_int(payload.get("step"), default=0)),
        "decision_summary": public_decision_summary(payload.get("decision_summary")),
        "input_refs": normalize_trace_refs(payload.get("input_refs")),
        "output_refs": normalize_trace_refs(payload.get("output_refs")),
        "evidence_refs": normalize_trace_refs(payload.get("evidence_refs")),
        "payload_hash": raw_hash if re.fullmatch(r"[0-9a-f]{64}", raw_hash) else stable_payload_hash(payload),
        "action_type": action_type,
        "tool_id": tool_id,
        "tool_ids": list(dict.fromkeys(tool_ids)),
        "outcome": _safe_code(payload.get("outcome"), fallback="UNKNOWN"),
        "duration_ms": max(0, _safe_int(payload.get("duration_ms"), default=0)),
        "ts_ms": max(0, _safe_int(payload.get("ts_ms"), default=0)),
    }
    return {key: public[key] for key in TRACE_STEP_PUBLIC_FIELDS}


def _as_step_mapping(value: Mapping[str, Any] | Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    names = (
        "step",
        "thought",
        "tool_id",
        "tool_args",
        "observation",
        "final_answer",
        "duration_ms",
        "action_type",
        "action",
        "ts_ms",
    )
    return {name: getattr(value, name) for name in names if hasattr(value, name)}


def _normalized_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _step_outcome(*, action_type: str, observation: Any) -> str:
    if action_type == "error":
        return "ERROR"
    if isinstance(observation, Mapping) and observation.get("error") not in (None, ""):
        return "REJECTED" if str(observation.get("error") or "").startswith("GOV_REJECTED") else "ERROR"
    if action_type in {"final_answer", "finish"}:
        return "COMPLETED"
    if action_type == "event":
        return "OBSERVED"
    return "OBSERVED" if observation not in (None, {}) else "PROPOSED"


def _step_decision_summary(*, action_type: str, tool_id: str, outcome: str) -> str:
    if action_type == "tool_call":
        return f"Tool call {tool_id or 'unknown'} recorded with outcome {outcome}."
    if action_type in {"final_answer", "finish"}:
        return "Agent produced a publishable final decision."
    if action_type == "event":
        return "Agent runtime event recorded by reference."
    return f"Agent step ended with outcome {outcome}."


def _safe_code(value: Any, *, fallback: str, lowercase: bool = False) -> str:
    text = re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(value or "").strip())[:128].strip("_")
    if lowercase:
        text = text.lower()
    return text or fallback


def _safe_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _safe_confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


__all__ = [
    "AGENT_OUTPUT_SCHEMA_VERSION",
    "TRACE_PUBLIC_FIELDS",
    "TRACE_SCHEMA_VERSION",
    "TRACE_STEP_PUBLIC_FIELDS",
    "build_redacted_trace",
    "build_redacted_trace_step",
    "build_public_agent_output",
    "contains_sensitive_trace_content",
    "content_ref",
    "normalize_trace_refs",
    "public_decision_summary",
    "public_output_text",
    "read_decision_summary",
    "read_public_agent_output",
    "redact_legacy_trace_payload",
    "sanitize_public_payload",
    "stable_payload_hash",
    "trace_input_refs",
]
