"""Edge fallback LLM RPC contract (draft-only and fast-fail by default)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    VOICE_DECISION_FIELD_FORBIDDEN,
    VOICE_DRAFT_ONLY_REQUIRED,
    VOICE_EXECUTION_CLAIM_FORBIDDEN,
    VOICE_FALLBACK_UNAVAILABLE,
    VOICE_GATE_PRIORITY_BLOCKED,
    VOICE_GATE_RESOURCE_LIMITED,
    VOICE_SCHEMA_INVALID,
)
from host_brain.core.ids import now_ms
from host_brain.core.qa import first_false_execution_claim, find_forbidden_keys
from edge_client.rpc.resource_gate import TASK_FALLBACK_LLM, VoiceResourceGate


EVENT_FALLBACK_PRESENCE = "FALLBACK_PRESENCE"
EVENT_FALLBACK_ASR_COMPILE = "FALLBACK_ASR_COMPILE"
EVENT_FALLBACK_RETRIEVAL_HINTS = "FALLBACK_RETRIEVAL_HINTS"
EVENT_FALLBACK_CONTEXT_COMPRESS = "FALLBACK_CONTEXT_COMPRESS"

DIRECTION_HOST_TO_EDGE = "HOST_TO_EDGE"

STATUS_ACCEPTED = "ACCEPTED"
STATUS_REJECTED = "REJECTED"

S_VOICE_EDGE_FALLBACK_PRESENCE = "S_VOICE_EDGE_FALLBACK_PRESENCE"
S_VOICE_EDGE_FALLBACK_ASR_COMPILE = "S_VOICE_EDGE_FALLBACK_ASR_COMPILE"
S_VOICE_EDGE_FALLBACK_RETRIEVAL_HINTS = "S_VOICE_EDGE_FALLBACK_RETRIEVAL_HINTS"
S_VOICE_EDGE_FALLBACK_CONTEXT_COMPRESS = "S_VOICE_EDGE_FALLBACK_CONTEXT_COMPRESS"

FORBIDDEN_DECISION_FIELDS = frozenset(
    {
        "decision",
        "allow",
        "deny",
        "confirm_required",
        "approval_token_ref",
        "confirm_token",
        "undo_token",
    }
)

FallbackGenerator = Callable[[str, dict[str, Any]], dict[str, Any] | str]


@dataclass(frozen=True)
class EdgeFallbackResult:
    success: bool
    status: str
    reason_code: str | None
    stage: str
    detail: str
    event: dict[str, Any]


class EdgeFallbackRpcContract:
    """Deterministic fallback LLM endpoints with draft-only guarantees."""

    def __init__(
        self,
        *,
        ledger: SQLiteLedger | None = None,
        resource_gate: VoiceResourceGate | None = None,
        available: bool = False,
        model_id: str = "ollama:3b-q4",
        default_unloaded: bool = True,
        generator: FallbackGenerator | None = None,
    ) -> None:
        self.ledger = ledger
        self.resource_gate = resource_gate
        self.available = bool(available)
        self.model_id = str(model_id)
        self.default_unloaded = bool(default_unloaded)
        self._generator = generator or _default_generator

    def set_available(self, available: bool) -> None:
        self.available = bool(available)

    def presence(
        self,
        *,
        request_id: str,
        payload: dict[str, Any] | None = None,
        ts_ms: int | None = None,
    ) -> EdgeFallbackResult:
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        input_payload = dict(payload or {})
        event = {
            "event_type": EVENT_FALLBACK_PRESENCE,
            "direction": DIRECTION_HOST_TO_EDGE,
            "available": bool(self.available),
            "default_unloaded": bool(self.default_unloaded),
            "model_id": self.model_id,
            "draft_only": True,
            "request_hint": str(input_payload.get("request_hint") or ""),
            "ts_ms": now_value,
        }
        reason_code = None if self.available else VOICE_FALLBACK_UNAVAILABLE
        detail = "fallback llm is available" if self.available else "fallback llm unavailable (default unloaded)"
        self._log_event(
            request_id=request_id,
            event_type=EVENT_FALLBACK_PRESENCE,
            status=STATUS_ACCEPTED,
            reason_code=reason_code,
            stage=S_VOICE_EDGE_FALLBACK_PRESENCE,
            detail=detail,
            payload=event,
            ts_ms=now_value,
            seq=_optional_int(input_payload.get("seq")),
        )
        return EdgeFallbackResult(
            success=True,
            status=STATUS_ACCEPTED,
            reason_code=reason_code,
            stage=S_VOICE_EDGE_FALLBACK_PRESENCE,
            detail=detail,
            event=event,
        )

    def asr_compile(
        self,
        *,
        request_id: str,
        payload: dict[str, Any],
        ts_ms: int | None = None,
    ) -> EdgeFallbackResult:
        return self._invoke(
            request_id=request_id,
            endpoint="asr_compile",
            event_type=EVENT_FALLBACK_ASR_COMPILE,
            stage=S_VOICE_EDGE_FALLBACK_ASR_COMPILE,
            payload=payload,
            ts_ms=ts_ms,
            required_fields=("session_id", "text"),
        )

    def retrieval_hints(
        self,
        *,
        request_id: str,
        payload: dict[str, Any],
        ts_ms: int | None = None,
    ) -> EdgeFallbackResult:
        return self._invoke(
            request_id=request_id,
            endpoint="retrieval_hints",
            event_type=EVENT_FALLBACK_RETRIEVAL_HINTS,
            stage=S_VOICE_EDGE_FALLBACK_RETRIEVAL_HINTS,
            payload=payload,
            ts_ms=ts_ms,
            required_fields=("query",),
        )

    def context_compress(
        self,
        *,
        request_id: str,
        payload: dict[str, Any],
        ts_ms: int | None = None,
    ) -> EdgeFallbackResult:
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        context = payload.get("context")
        context_ok = (
            isinstance(context, str)
            and bool(context.strip())
            or isinstance(context, list)
            and bool(context)
        )
        if not context_ok:
            return self._reject(
                request_id=request_id,
                event_type=EVENT_FALLBACK_CONTEXT_COMPRESS,
                stage=S_VOICE_EDGE_FALLBACK_CONTEXT_COMPRESS,
                reason_code=VOICE_SCHEMA_INVALID,
                detail="context_compress requires non-empty context (str|list)",
                payload=payload,
                ts_ms=now_value,
            )
        return self._invoke(
            request_id=request_id,
            endpoint="context_compress",
            event_type=EVENT_FALLBACK_CONTEXT_COMPRESS,
            stage=S_VOICE_EDGE_FALLBACK_CONTEXT_COMPRESS,
            payload=payload,
            ts_ms=now_value,
            required_fields=(),
        )

    def _invoke(
        self,
        *,
        request_id: str,
        endpoint: str,
        event_type: str,
        stage: str,
        payload: dict[str, Any],
        ts_ms: int | None,
        required_fields: tuple[str, ...],
    ) -> EdgeFallbackResult:
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        for field in required_fields:
            value = payload.get(field)
            if not isinstance(value, str) or not value.strip():
                return self._reject(
                    request_id=request_id,
                    event_type=event_type,
                    stage=stage,
                    reason_code=VOICE_SCHEMA_INVALID,
                    detail=f"{endpoint} requires non-empty field: {field}",
                    payload=payload,
                    ts_ms=now_value,
                )

        if not self.available:
            return self._reject(
                request_id=request_id,
                event_type=event_type,
                stage=stage,
                reason_code=VOICE_FALLBACK_UNAVAILABLE,
                detail="fallback llm unavailable (fast-fail to host fallback)",
                payload=payload,
                ts_ms=now_value,
            )

        if self.resource_gate is not None:
            gate_decision = self.resource_gate.request(
                request_id=request_id,
                task=TASK_FALLBACK_LLM,
                session_id=str(payload.get("session_id") or ""),
                metrics=_extract_gate_metrics(payload),
                hold=False,
                ts_ms=now_value,
            )
            if not gate_decision.admitted:
                reason_code = gate_decision.reason_code or VOICE_GATE_PRIORITY_BLOCKED
                if reason_code not in {
                    VOICE_GATE_PRIORITY_BLOCKED,
                    VOICE_GATE_RESOURCE_LIMITED,
                }:
                    reason_code = VOICE_GATE_PRIORITY_BLOCKED
                return self._reject(
                    request_id=request_id,
                    event_type=event_type,
                    stage=gate_decision.stage,
                    reason_code=reason_code,
                    detail=f"resource gate blocked fallback call: {gate_decision.detail}",
                    payload=payload,
                    ts_ms=now_value,
                )

        raw_draft = self._generator(endpoint, dict(payload))
        try:
            draft = self._normalize_draft(raw_draft)
        except ValueError as exc:
            return self._reject(
                request_id=request_id,
                event_type=event_type,
                stage=stage,
                reason_code=VOICE_SCHEMA_INVALID,
                detail=str(exc),
                payload={"raw_draft_type": type(raw_draft).__name__},
                ts_ms=now_value,
            )

        if "draft_only" in draft and draft.get("draft_only") is not True:
            return self._reject(
                request_id=request_id,
                event_type=event_type,
                stage=stage,
                reason_code=VOICE_DRAFT_ONLY_REQUIRED,
                detail="fallback output must keep draft_only=true",
                payload=draft,
                ts_ms=now_value,
            )
        draft["draft_only"] = True

        forbidden_hits = find_forbidden_keys(draft, forbidden_keys=FORBIDDEN_DECISION_FIELDS)
        if forbidden_hits:
            return self._reject(
                request_id=request_id,
                event_type=event_type,
                stage=stage,
                reason_code=VOICE_DECISION_FIELD_FORBIDDEN,
                detail=f"forbidden decision field detected: {forbidden_hits[0]}",
                payload=draft,
                ts_ms=now_value,
            )

        claim = first_false_execution_claim(draft)
        if claim is not None:
            return self._reject(
                request_id=request_id,
                event_type=event_type,
                stage=stage,
                reason_code=VOICE_EXECUTION_CLAIM_FORBIDDEN,
                detail=f"forbidden execution claim in fallback draft: {claim[:80]}",
                payload=draft,
                ts_ms=now_value,
            )

        event = {
            "event_type": event_type,
            "direction": DIRECTION_HOST_TO_EDGE,
            "endpoint": endpoint,
            "request_payload": dict(payload),
            "draft": draft,
            "draft_only": True,
            "model_id": self.model_id,
            "ts_ms": now_value,
        }
        self._log_event(
            request_id=request_id,
            event_type=event_type,
            status=STATUS_ACCEPTED,
            reason_code=None,
            stage=stage,
            detail=f"{endpoint} draft accepted",
            payload=event,
            ts_ms=now_value,
            seq=_optional_int(payload.get("seq")),
        )
        return EdgeFallbackResult(
            success=True,
            status=STATUS_ACCEPTED,
            reason_code=None,
            stage=stage,
            detail=f"{endpoint} draft accepted",
            event=event,
        )

    def _normalize_draft(self, raw_draft: dict[str, Any] | str | Any) -> dict[str, Any]:
        if isinstance(raw_draft, dict):
            return dict(raw_draft)
        if isinstance(raw_draft, str):
            text = raw_draft.strip()
            if not text:
                raise ValueError("fallback output string is empty")
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                raise ValueError("fallback output is not valid JSON")
            if not isinstance(parsed, dict):
                raise ValueError("fallback output JSON root must be object")
            return dict(parsed)
        raise ValueError(f"fallback output must be dict|JSON string, got {type(raw_draft).__name__}")

    def _reject(
        self,
        *,
        request_id: str,
        event_type: str,
        stage: str,
        reason_code: str,
        detail: str,
        payload: dict[str, Any],
        ts_ms: int,
    ) -> EdgeFallbackResult:
        event = {
            "event_type": event_type,
            "direction": DIRECTION_HOST_TO_EDGE,
            "ts_ms": ts_ms,
        }
        self._log_event(
            request_id=request_id,
            event_type=event_type,
            status=STATUS_REJECTED,
            reason_code=reason_code,
            stage=stage,
            detail=detail,
            payload=payload,
            ts_ms=ts_ms,
            seq=_optional_int(payload.get("seq")),
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
        event_type: str,
        status: str,
        reason_code: str | None,
        stage: str,
        detail: str,
        payload: dict[str, Any],
        ts_ms: int,
        seq: int | None,
    ) -> None:
        if self.ledger is None:
            return
        self.ledger.log_voice_event(
            {
                "request_id": request_id,
                "session_id": "",
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
            "summary": text[:120],
            "keywords": [token for token in text.split()[:5]],
        }
    if endpoint == "retrieval_hints":
        query = str(payload.get("query") or "").strip()
        top_k = _coerce_top_k(payload.get("top_k"))
        return {
            "hints": [query, f"query_length:{len(query)}"],
            "top_k": top_k,
        }
    if endpoint == "context_compress":
        context = payload.get("context")
        if isinstance(context, list):
            merged = " ".join(str(item) for item in context)
        else:
            merged = str(context or "")
        return {
            "compressed_context": merged[:160],
            "compression_ratio_hint": 0.5,
        }
    return {"note": "fallback endpoint not recognized"}


def _coerce_top_k(value: Any) -> int:
    if isinstance(value, int) and value > 0:
        return value
    return 3


def _optional_int(value: Any) -> int | None:
    return int(value) if isinstance(value, int) else None


def _extract_gate_metrics(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "vram_total_mb": payload.get("vram_total_mb", 0),
        "vram_used_mb": payload.get("vram_used_mb", 0),
        "queue_depth": payload.get("queue_depth", 0),
    }
