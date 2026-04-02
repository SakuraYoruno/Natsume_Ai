"""Draft-only fallback LLM RPC contract for edge endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from edge_client.rpc.resource_gate import TASK_FALLBACK_LLM, VoiceResourceGate
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    VOICE_DECISION_FIELD_FORBIDDEN,
    VOICE_DRAFT_ONLY_REQUIRED,
    VOICE_EXECUTION_CLAIM_FORBIDDEN,
    VOICE_FALLBACK_UNAVAILABLE,
    VOICE_SCHEMA_INVALID,
)
from host_brain.core.ids import now_ms


STATUS_ACCEPTED = "ACCEPTED"
STATUS_REJECTED = "REJECTED"
DIRECTION_HOST_TO_EDGE = "HOST_TO_EDGE"

EVENT_FALLBACK_PRESENCE = "FALLBACK_PRESENCE"
EVENT_FALLBACK_ASR_COMPILE = "FALLBACK_ASR_COMPILE"
EVENT_FALLBACK_RETRIEVAL_HINTS = "FALLBACK_RETRIEVAL_HINTS"
EVENT_FALLBACK_CONTEXT_COMPRESS = "FALLBACK_CONTEXT_COMPRESS"

S_VOICE_EDGE_FALLBACK_PRESENCE = "S_VOICE_EDGE_FALLBACK_PRESENCE"
S_VOICE_EDGE_FALLBACK_ASR_COMPILE = "S_VOICE_EDGE_FALLBACK_ASR_COMPILE"
S_VOICE_EDGE_FALLBACK_RETRIEVAL_HINTS = "S_VOICE_EDGE_FALLBACK_RETRIEVAL_HINTS"
S_VOICE_EDGE_FALLBACK_CONTEXT_COMPRESS = "S_VOICE_EDGE_FALLBACK_CONTEXT_COMPRESS"

_FORBIDDEN_DECISION_FIELDS = frozenset(
    {"decision", "allow", "deny", "confirm_required", "approval_token_ref"}
)
_EXECUTION_CLAIM_MARKERS = (
    "已执行",
    "已经执行",
    "执行完成",
    "保存完成",
    "already executed",
    "already saved",
    "has been executed",
)

FallbackGenerator = Callable[[str, dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class EdgeFallbackResult:
    success: bool
    status: str
    reason_code: str | None
    stage: str
    detail: str
    event: dict[str, Any]


class EdgeFallbackRpcContract:
    """Validates draft-only fallback RPC responses and logs replayable events."""

    def __init__(
        self,
        *,
        ledger: SQLiteLedger | None = None,
        resource_gate: VoiceResourceGate | None = None,
        available: bool = True,
        default_unloaded: bool = False,
        generator: FallbackGenerator | None = None,
    ) -> None:
        self.ledger = ledger
        self.resource_gate = resource_gate
        self.available = bool(available)
        self.default_unloaded = bool(default_unloaded)
        self.generator = generator or _default_generator

    def presence(
        self,
        *,
        request_id: str,
        payload: dict[str, Any] | None = None,
        ts_ms: int | None = None,
    ) -> EdgeFallbackResult:
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        normalized_payload = dict(payload or {})
        seq = _coerce_seq(normalized_payload.get("seq"))
        event = {
            "event_type": EVENT_FALLBACK_PRESENCE,
            "direction": DIRECTION_HOST_TO_EDGE,
            "seq": seq,
            "available": self.available,
            "draft_only": True,
            "ts_ms": now_value,
        }
        request_hint = str(normalized_payload.get("request_hint") or "").strip()
        if request_hint:
            event["request_hint"] = request_hint
        reason_code = None if self.available else VOICE_FALLBACK_UNAVAILABLE
        detail = (
            "fallback model available for draft-only hints"
            if self.available
            else "fallback model unavailable"
        )
        return self._accept(
            request_id=request_id,
            session_id="",
            event_type=EVENT_FALLBACK_PRESENCE,
            seq=seq,
            stage=S_VOICE_EDGE_FALLBACK_PRESENCE,
            reason_code=reason_code,
            detail=detail,
            event=event,
            ts_ms=now_value,
        )

    def asr_compile(
        self,
        *,
        request_id: str,
        payload: dict[str, Any],
        ts_ms: int | None = None,
    ) -> EdgeFallbackResult:
        return self._run_draft_endpoint(
            request_id=request_id,
            payload=payload,
            ts_ms=ts_ms,
            endpoint="asr_compile",
            event_type=EVENT_FALLBACK_ASR_COMPILE,
            stage=S_VOICE_EDGE_FALLBACK_ASR_COMPILE,
            required_fields=("session_id", "text"),
        )

    def retrieval_hints(
        self,
        *,
        request_id: str,
        payload: dict[str, Any],
        ts_ms: int | None = None,
    ) -> EdgeFallbackResult:
        return self._run_draft_endpoint(
            request_id=request_id,
            payload=payload,
            ts_ms=ts_ms,
            endpoint="retrieval_hints",
            event_type=EVENT_FALLBACK_RETRIEVAL_HINTS,
            stage=S_VOICE_EDGE_FALLBACK_RETRIEVAL_HINTS,
            required_fields=("query",),
        )

    def context_compress(
        self,
        *,
        request_id: str,
        payload: dict[str, Any],
        ts_ms: int | None = None,
    ) -> EdgeFallbackResult:
        return self._run_draft_endpoint(
            request_id=request_id,
            payload=payload,
            ts_ms=ts_ms,
            endpoint="context_compress",
            event_type=EVENT_FALLBACK_CONTEXT_COMPRESS,
            stage=S_VOICE_EDGE_FALLBACK_CONTEXT_COMPRESS,
            required_fields=("context",),
        )

    def _run_draft_endpoint(
        self,
        *,
        request_id: str,
        payload: dict[str, Any],
        ts_ms: int | None,
        endpoint: str,
        event_type: str,
        stage: str,
        required_fields: tuple[str, ...],
    ) -> EdgeFallbackResult:
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        normalized_payload = dict(payload or {})
        session_id = str(normalized_payload.get("session_id") or "").strip()
        seq = _coerce_seq(normalized_payload.get("seq"))

        if not _payload_is_valid(endpoint=endpoint, payload=normalized_payload, required_fields=required_fields):
            return self._reject(
                request_id=request_id,
                session_id=session_id,
                event_type=event_type,
                seq=seq,
                stage=stage,
                reason_code=VOICE_SCHEMA_INVALID,
                detail=f"{endpoint} payload missing required fields",
                payload=normalized_payload,
                ts_ms=now_value,
            )

        if not self.available:
            detail = "fallback model unavailable (default unloaded)" if self.default_unloaded else "fallback model unavailable"
            return self._reject(
                request_id=request_id,
                session_id=session_id,
                event_type=event_type,
                seq=seq,
                stage=stage,
                reason_code=VOICE_FALLBACK_UNAVAILABLE,
                detail=detail,
                payload=normalized_payload,
                ts_ms=now_value,
            )

        gate_decision = self._request_gate(
            request_id=request_id,
            session_id=session_id,
            payload=normalized_payload,
            ts_ms=now_value,
        )
        if gate_decision is not None and not gate_decision.admitted:
            return self._reject(
                request_id=request_id,
                session_id=session_id,
                event_type=event_type,
                seq=seq,
                stage=gate_decision.stage,
                reason_code=str(gate_decision.reason_code or VOICE_FALLBACK_UNAVAILABLE),
                detail=f"{endpoint} blocked by resource gate: {gate_decision.detail}",
                payload=normalized_payload,
                ts_ms=now_value,
            )

        raw_output = self.generator(endpoint, dict(normalized_payload))
        if not isinstance(raw_output, dict):
            return self._reject(
                request_id=request_id,
                session_id=session_id,
                event_type=event_type,
                seq=seq,
                stage=stage,
                reason_code=VOICE_SCHEMA_INVALID,
                detail=f"{endpoint} generator must return dict",
                payload=normalized_payload,
                ts_ms=now_value,
            )
        if raw_output.get("draft_only") is not True:
            return self._reject(
                request_id=request_id,
                session_id=session_id,
                event_type=event_type,
                seq=seq,
                stage=stage,
                reason_code=VOICE_DRAFT_ONLY_REQUIRED,
                detail=f"{endpoint} output must declare draft_only=true",
                payload=raw_output,
                ts_ms=now_value,
            )
        if _contains_forbidden_decision_fields(raw_output):
            return self._reject(
                request_id=request_id,
                session_id=session_id,
                event_type=event_type,
                seq=seq,
                stage=stage,
                reason_code=VOICE_DECISION_FIELD_FORBIDDEN,
                detail=f"{endpoint} output contains forbidden decision fields",
                payload=raw_output,
                ts_ms=now_value,
            )

        draft = _extract_draft(raw_output)
        if _contains_forbidden_decision_fields(draft):
            return self._reject(
                request_id=request_id,
                session_id=session_id,
                event_type=event_type,
                seq=seq,
                stage=stage,
                reason_code=VOICE_DECISION_FIELD_FORBIDDEN,
                detail=f"{endpoint} draft contains forbidden decision fields",
                payload=raw_output,
                ts_ms=now_value,
            )
        if _contains_execution_claim(raw_output) or _contains_execution_claim(draft):
            return self._reject(
                request_id=request_id,
                session_id=session_id,
                event_type=event_type,
                seq=seq,
                stage=stage,
                reason_code=VOICE_EXECUTION_CLAIM_FORBIDDEN,
                detail=f"{endpoint} draft claims execution or persistence",
                payload=raw_output,
                ts_ms=now_value,
            )

        event = {
            "event_type": event_type,
            "direction": DIRECTION_HOST_TO_EDGE,
            "seq": seq,
            "draft_only": True,
            "draft": draft,
            "ts_ms": now_value,
        }
        if session_id:
            event["session_id"] = session_id
        if endpoint == "retrieval_hints":
            event["query"] = str(normalized_payload.get("query") or "").strip()
        if endpoint == "context_compress":
            event["context_items"] = len(normalized_payload.get("context") or [])
        return self._accept(
            request_id=request_id,
            session_id=session_id,
            event_type=event_type,
            seq=seq,
            stage=stage,
            detail=f"{endpoint} draft accepted",
            event=event,
            ts_ms=now_value,
        )

    def _request_gate(
        self,
        *,
        request_id: str,
        session_id: str,
        payload: dict[str, Any],
        ts_ms: int,
    ) -> Any:
        if self.resource_gate is None:
            return None
        return self.resource_gate.request(
            request_id=request_id,
            task=TASK_FALLBACK_LLM,
            session_id=session_id,
            metrics={
                "vram_total_mb": payload.get("vram_total_mb", 0),
                "vram_used_mb": payload.get("vram_used_mb", 0),
                "queue_depth": payload.get("queue_depth", 0),
            },
            hold=False,
            ts_ms=ts_ms,
        )

    def _accept(
        self,
        *,
        request_id: str,
        session_id: str,
        event_type: str,
        seq: int | None,
        stage: str,
        detail: str,
        event: dict[str, Any],
        ts_ms: int,
        reason_code: str | None = None,
    ) -> EdgeFallbackResult:
        self._log_event(
            request_id=request_id,
            session_id=session_id,
            event_type=event_type,
            seq=seq,
            status=STATUS_ACCEPTED,
            reason_code=reason_code,
            stage=stage,
            detail=detail,
            payload=event,
            ts_ms=ts_ms,
        )
        return EdgeFallbackResult(
            success=True,
            status=STATUS_ACCEPTED,
            reason_code=reason_code,
            stage=stage,
            detail=detail,
            event=event,
        )

    def _reject(
        self,
        *,
        request_id: str,
        session_id: str,
        event_type: str,
        seq: int | None,
        stage: str,
        reason_code: str,
        detail: str,
        payload: dict[str, Any],
        ts_ms: int,
    ) -> EdgeFallbackResult:
        event = {
            "event_type": event_type,
            "direction": DIRECTION_HOST_TO_EDGE,
            "seq": seq,
            "ts_ms": ts_ms,
        }
        self._log_event(
            request_id=request_id,
            session_id=session_id,
            event_type=event_type,
            seq=seq,
            status=STATUS_REJECTED,
            reason_code=reason_code,
            stage=stage,
            detail=detail,
            payload=payload,
            ts_ms=ts_ms,
        )
        return EdgeFallbackResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code=reason_code,
            stage=stage,
            detail=detail,
            event=event,
        )

    def _log_event(
        self,
        *,
        request_id: str,
        session_id: str,
        event_type: str,
        seq: int | None,
        status: str,
        reason_code: str | None,
        stage: str,
        detail: str,
        payload: dict[str, Any],
        ts_ms: int,
    ) -> None:
        if self.ledger is None:
            return
        self.ledger.log_voice_event(
            {
                "request_id": request_id,
                "session_id": session_id,
                "event_type": event_type,
                "direction": DIRECTION_HOST_TO_EDGE,
                "seq": seq,
                "status": status,
                "reason_code": reason_code,
                "stage": stage,
                "payload": payload,
                "detail": detail,
                "ts_ms": ts_ms,
            }
        )


def _default_generator(endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
    if endpoint == "asr_compile":
        text = str(payload.get("text") or "").strip()
        return {
            "draft_only": True,
            "draft": {
                "summary": f"Draft transcript: {text[:80]}".strip(),
                "session_id": str(payload.get("session_id") or "").strip(),
            },
        }
    if endpoint == "retrieval_hints":
        query = str(payload.get("query") or "").strip()
        return {
            "draft_only": True,
            "draft": {
                "summary": f"Draft retrieval hints for: {query[:80]}".strip(),
                "hints": [query] if query else [],
            },
        }
    context = payload.get("context") or []
    return {
        "draft_only": True,
        "draft": {
            "summary": f"Draft context compressed from {len(context)} items",
            "compressed_context": list(context)[:3],
        },
    }


def _payload_is_valid(
    *,
    endpoint: str,
    payload: dict[str, Any],
    required_fields: tuple[str, ...],
) -> bool:
    for field in required_fields:
        value = payload.get(field)
        if field == "context":
            if not isinstance(value, list) or not value:
                return False
            continue
        if not str(value or "").strip():
            return False
    if endpoint == "retrieval_hints":
        top_k = payload.get("top_k")
        if top_k is not None:
            try:
                if int(top_k) <= 0:
                    return False
            except (TypeError, ValueError):
                return False
    return True


def _coerce_seq(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    return None


def _extract_draft(raw_output: dict[str, Any]) -> dict[str, Any]:
    draft = raw_output.get("draft")
    if isinstance(draft, dict):
        return dict(draft)
    return {
        key: value
        for key, value in raw_output.items()
        if key != "draft_only"
    }


def _contains_forbidden_decision_fields(payload: dict[str, Any]) -> bool:
    return any(key in _FORBIDDEN_DECISION_FIELDS for key in payload.keys())


def _contains_execution_claim(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_contains_execution_claim(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_execution_claim(item) for item in value)
    if not isinstance(value, str):
        return False
    lowered = value.strip().lower()
    if not lowered:
        return False
    for marker in _EXECUTION_CLAIM_MARKERS:
        if marker in value or marker in lowered:
            return True
    return False
