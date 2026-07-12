"""Graph adapter for the canonical CapabilityBroker tool boundary."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from host_brain.capability_plane.adapters import object_tool_adapter
from host_brain.capability_plane.broker import (
    CapabilityBroker,
    CapabilityDescriptor,
    InMemoryCapabilityAuditSink,
)
from host_brain.capability_plane.contracts import CapabilityKind, CapabilityRequest
from host_brain.capability_plane.journal import CapabilityExecutionJournal
from host_brain.capability_plane.policies import GovernorToolCapabilityPolicy


class _EvidencePlaneSink(InMemoryCapabilityAuditSink):
    def __init__(self, evidence_plane: Any) -> None:
        super().__init__()
        self._plane = evidence_plane

    def record(self, attempt, evidence, receipt) -> None:  # noqa: ANN001
        super().record(attempt, evidence, receipt)
        if self._plane is None:
            return
        if callable(getattr(self._plane, "write_capability_execution", None)):
            self._plane.write_capability_execution(
                attempt=attempt,
                evidence=evidence,
                receipt=receipt,
            )
            return
        if callable(getattr(self._plane, "write_tool_execution", None)):
            self._plane.write_tool_execution(
                request_id=attempt.request_id,
                tool_calls=[{"tool": attempt.capability_id}],
                tool_results=[receipt.to_dict()],
                ts_ms=attempt.finished_at_ms,
            )


class GovernedToolNode:
    """Execute each graph tool call through one fail-closed broker."""

    def __init__(
        self,
        *,
        tools: list[Any] | None = None,
        governor: Any = None,
        evidence_plane: Any = None,
        governor_rules_cache: Any = None,
        capability_broker: CapabilityBroker | None = None,
        execution_journal: CapabilityExecutionJournal | None = None,
    ) -> None:
        self._governor_rules_cache = governor_rules_cache
        policy = GovernorToolCapabilityPolicy(governor, rules_provider=self._get_tribe_params)
        self._broker = capability_broker or CapabilityBroker(
            policy=policy,
            audit_sink=_EvidencePlaneSink(evidence_plane),
            execution_journal=execution_journal,
        )
        for tool in list(tools or []):
            name = str(getattr(tool, "name", None) or "").strip()
            if not name:
                continue
            self._broker.register(
                CapabilityDescriptor(
                    capability_id=name,
                    kind=CapabilityKind.TOOL,
                    version=str(getattr(tool, "version", "1") or "1"),
                    risk_level=int(getattr(tool, "risk_level", 5) or 5),
                    adapter=object_tool_adapter(tool),
                )
            )

    def _get_tribe_params(self) -> dict[str, Any]:
        try:
            cache = self._governor_rules_cache
            if cache is None:
                from host_brain.integrations.governor_rules_cache import get_governor_rules_cache  # noqa: PLC0415

                cache = get_governor_rules_cache()
            return dict(cache.get_effective_rules() or {})
        except Exception:
            return {}

    def __call__(self, state: dict[str, Any]) -> dict[str, Any]:
        tool_calls = list(state.get("tool_calls") or [])
        request_id = str(state.get("request_id") or "").strip()
        if not request_id and tool_calls:
            return {**state, "committed_route_token": "CAPABILITY_REQUEST_ID_MISSING", "tool_results": []}

        results: list[dict[str, Any]] = []
        route_token = "GOV_APPROVE"
        deadline_at_ms = state.get("tool_deadline_at_ms")
        for index, raw in enumerate(tool_calls):
            call = dict(raw) if isinstance(raw, dict) else {}
            tool_name = str(call.get("tool") or "").strip()
            tool_args = dict(call.get("args") or {})
            args_digest = hashlib.sha256(
                json.dumps(tool_args, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()
            attempt_id = f"{request_id}:tool:{index}:{tool_name or 'unknown'}"
            try:
                request = CapabilityRequest(
                    request_id=request_id,
                    attempt_id=attempt_id,
                    idempotency_key=f"graph-tool::{request_id}::{index}::{tool_name}::{args_digest}",
                    capability_id=tool_name or "unknown",
                    kind=CapabilityKind.TOOL,
                    args=tool_args,
                    context={"agent_id": str(state.get("agent_id") or "")},
                    created_at_ms=int(time.time() * 1000),
                    deadline_at_ms=int(deadline_at_ms) if deadline_at_ms is not None else None,
                )
                receipt = self._broker.execute(request)
            except Exception as exc:
                route_token = "CAPABILITY_REQUEST_INVALID"
                results.append({
                    "tool": tool_name,
                    "args": tool_args,
                    "result": {"error": f"CAPABILITY_REQUEST_INVALID:{type(exc).__name__}", "result": None},
                    "ts_ms": int(time.time() * 1000),
                })
                continue
            if not receipt.success:
                route_token = route_token if route_token != "GOV_APPROVE" else str(receipt.reason_code or "CAPABILITY_REJECTED")
                output = {"error": str(receipt.reason_code or receipt.detail), "result": None}
            else:
                output = receipt.output
            results.append({
                "tool": tool_name,
                "args": tool_args,
                "result": output,
                "ts_ms": int(time.time() * 1000),
                "capability_receipt": receipt.to_dict(),
            })
        merged = {**state, "tool_results": results}
        if route_token != "GOV_APPROVE":
            merged["committed_route_token"] = route_token
        elif tool_calls:
            merged["committed_route_token"] = "GOV_APPROVE"
        return merged


__all__ = ["GovernedToolNode"]
