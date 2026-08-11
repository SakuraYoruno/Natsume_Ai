"""Durable, replay-safe references to official Governor decisions."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import time
from typing import Any


GOVERNOR_GRANT_MISSING = "GOVERNOR_GRANT_MISSING"
GOVERNOR_GRANT_INVALID = "GOVERNOR_GRANT_INVALID"
GOVERNOR_GRANT_BINDING_MISSING = "GOVERNOR_GRANT_BINDING_MISSING"
GOVERNOR_GRANT_SCOPE_MISMATCH = "GOVERNOR_GRANT_SCOPE_MISMATCH"
_BINDING_SCHEMA_VERSION = "Natsume.governed_action.binding.v1"
_PERSISTED_BINDING_KEY = "approved_action_binding"


class GovernorGrantError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or GOVERNOR_GRANT_INVALID)
        self.detail = str(detail or "Governor grant validation failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


class DurableGovernorGrantResolver:
    """Resolve an immutable approved decision from the canonical audit ledger."""

    def __init__(self, ledger: Any) -> None:
        if not callable(getattr(ledger, "trace_request", None)):
            raise TypeError("ledger must expose trace_request")
        self._ledger = ledger

    def require(self, *, request_id: str, expected_ref: str) -> dict[str, Any]:
        request_ref = str(request_id or "").strip()
        grant_ref = str(expected_ref or "").strip()
        if not request_ref or not grant_ref:
            raise GovernorGrantError(
                GOVERNOR_GRANT_MISSING,
                "request_id and expected Governor grant reference are required",
            )
        trace = self._ledger.trace_request(request_ref)
        rows = list(trace.get("governor_log") or []) if isinstance(trace, Mapping) else []
        approved = [
            dict(row)
            for row in rows
            if isinstance(row, Mapping)
            and str(row.get("decision") or "").strip().upper() == "APPROVE"
        ]
        approved.sort(key=lambda row: int(row.get("ts_ms") or 0), reverse=True)
        for decision in approved:
            if governor_grant_ref(request_ref, decision) == grant_ref:
                binding = durable_governor_binding(decision)
                if binding:
                    decision[_PERSISTED_BINDING_KEY] = binding
                return decision
        raise GovernorGrantError(
            GOVERNOR_GRANT_INVALID,
            "Governor grant does not match any durable approved decision",
        )

    def require_bound(
        self,
        *,
        request_id: str,
        expected_ref: str,
        expected_person_id: str,
        expected_action_kind: str,
        expected_target: str,
        expected_intent_args: Mapping[str, Any],
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        decision = self.require(request_id=request_id, expected_ref=expected_ref)
        binding = durable_governor_binding(decision)
        if not binding:
            raise GovernorGrantError(
                GOVERNOR_GRANT_BINDING_MISSING,
                "durable approved action binding is required",
            )
        if str(binding.get("schema_version") or "") != _BINDING_SCHEMA_VERSION:
            raise GovernorGrantError(GOVERNOR_GRANT_BINDING_MISSING, "unsupported grant binding schema")
        current_ms = int(now_ms if now_ms is not None else time.time() * 1000)
        try:
            expires_at_ms = int(binding.get("expires_at_ms") or 0)
        except (TypeError, ValueError):
            expires_at_ms = 0
        if expires_at_ms <= current_ms:
            raise GovernorGrantError(GOVERNOR_GRANT_INVALID, "durable grant binding is expired")
        expected = {
            "person_id": str(expected_person_id or "").strip(),
            "action_kind": str(expected_action_kind or "").strip(),
            "target": str(expected_target or "").strip().lower(),
            "intent_args_digest": f"sha256:{canonical_payload_digest(expected_intent_args)}",
        }
        actual = {
            "person_id": str(binding.get("person_id") or "").strip(),
            "action_kind": str(binding.get("action_kind") or "").strip(),
            "target": str(binding.get("scope") or "").strip().lower(),
            "intent_args_digest": str(binding.get("intent_args_digest") or "").strip(),
        }
        principal_id = str(binding.get("principal_id") or "").strip()
        if not principal_id or not expected["person_id"] or actual != expected:
            raise GovernorGrantError(
                GOVERNOR_GRANT_SCOPE_MISMATCH,
                "durable grant principal/person/action/target/payload binding mismatch",
            )
        decision[_PERSISTED_BINDING_KEY] = binding
        return decision

    def require_delivery_bound(
        self,
        *,
        request_id: str,
        expected_ref: str,
        authority_kind: str,
        authority_principal_id: str,
        person_id: str,
        destination: str,
        payload: Mapping[str, Any],
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        """Validate the final delivery against a server-owned authority map."""
        authority = str(authority_kind or "").strip().lower()
        person = str(person_id or "").strip()
        principal = str(authority_principal_id or "").strip()
        target = str(destination or "").strip().lower()
        body = dict(payload or {})
        if authority == "governed_life_http":
            if target != "proactive.gateway":
                raise GovernorGrantError(GOVERNOR_GRANT_SCOPE_MISMATCH, "HTTP grant destination is not allowed")
            decision = self.require_bound(
                request_id=request_id,
                expected_ref=expected_ref,
                expected_person_id=person,
                expected_action_kind="proactive_attempt",
                expected_target=target,
                expected_intent_args=canonical_proactive_intent_args(
                    person_id=person,
                    platform=str(body.get("platform") or ""),
                    conversation_id=str(body.get("conversation_id") or ""),
                    content=str(body.get("content") or ""),
                    source_kind=str(body.get("source_kind") or "manual_proactive_attempt"),
                ),
                now_ms=now_ms,
            )
            if str(durable_governor_binding(decision).get("principal_id") or "").strip() != principal:
                raise GovernorGrantError(
                    GOVERNOR_GRANT_SCOPE_MISMATCH,
                    "HTTP delivery authority principal does not match durable binding",
                )
            return decision

        policies = {
            "scheduler_host_command": {
                "destinations": {"edge.proactive", "proactive.gateway", "edge.gateway_reply"},
                "actions": {"exec_intent"},
                "scopes": {"system"},
            },
            "surface_governor": {
                "destinations": {"vts.emotion", "mateengine.state"},
                "actions": {
                    "narrative_only",
                    "exec_intent",
                    "work_code",
                    "work_translate",
                    "read_only_status",
                },
                "scopes": {"host"},
            },
        }
        policy = policies.get(authority)
        if policy is None or target not in policy["destinations"]:
            raise GovernorGrantError(GOVERNOR_GRANT_SCOPE_MISMATCH, "delivery authority/destination is not allowed")
        decision = self.require(request_id=request_id, expected_ref=expected_ref)
        binding = durable_governor_binding(decision)
        if not binding:
            raise GovernorGrantError(GOVERNOR_GRANT_BINDING_MISSING, "durable delivery binding is required")
        current_ms = int(now_ms if now_ms is not None else time.time() * 1000)
        try:
            expires = int(binding.get("expires_at_ms") or 0)
        except (TypeError, ValueError):
            expires = 0
        actual_person = str(binding.get("person_id") or "").strip()
        actual_principal = str(binding.get("principal_id") or "").strip()
        action = str(binding.get("action_kind") or "").strip()
        scope = str(binding.get("scope") or "").strip().lower()
        if (
            str(binding.get("schema_version") or "") != _BINDING_SCHEMA_VERSION
            or expires <= current_ms
            or not person
            or not principal
            or actual_person != person
            or actual_principal != principal
            or action not in policy["actions"]
            or scope not in policy["scopes"]
        ):
            raise GovernorGrantError(
                GOVERNOR_GRANT_SCOPE_MISMATCH,
                "delivery binding expired or person/principal/action/scope mismatch",
            )
        decision[_PERSISTED_BINDING_KEY] = binding
        return decision


def governor_grant_ref(request_id: str, decision: Mapping[str, Any]) -> str:
    binding = durable_governor_binding(decision)
    payload = json.dumps(
        {
            "decision": str(decision.get("decision") or "").strip().upper(),
            "intent_id": str(decision.get("intent_id") or "").strip(),
            "request_id": str(request_id or "").strip(),
            "ts_ms": int(decision.get("ts_ms") or 0),
            "binding_digest": canonical_payload_digest(binding) if binding else "",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"governor-decision://sha256/{digest}"


def canonical_payload_digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        dict(value or {}),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def canonical_proactive_intent_args(
    *,
    person_id: str,
    platform: str,
    conversation_id: str,
    content: str,
    source_kind: str,
) -> dict[str, str]:
    return {
        "person_id": str(person_id or "").strip(),
        "platform": str(platform or "").strip().lower(),
        "conversation_id": str(conversation_id or "").strip(),
        "content": str(content or "").strip(),
        "source_kind": str(source_kind or "").strip(),
    }


def durable_governor_binding(decision: Mapping[str, Any]) -> dict[str, Any]:
    direct = decision.get(_PERSISTED_BINDING_KEY)
    if isinstance(direct, Mapping):
        return dict(direct)
    tokens = decision.get("tokens")
    if isinstance(tokens, str):
        try:
            tokens = json.loads(tokens)
        except json.JSONDecodeError:
            tokens = None
    if isinstance(tokens, Mapping) and isinstance(tokens.get(_PERSISTED_BINDING_KEY), Mapping):
        return dict(tokens[_PERSISTED_BINDING_KEY])
    snapshots = decision.get("snapshot_refs")
    if isinstance(snapshots, Mapping) and isinstance(snapshots.get(_PERSISTED_BINDING_KEY), Mapping):
        return dict(snapshots[_PERSISTED_BINDING_KEY])
    return {}


__all__ = [
    "GOVERNOR_GRANT_INVALID",
    "GOVERNOR_GRANT_BINDING_MISSING",
    "GOVERNOR_GRANT_SCOPE_MISMATCH",
    "GOVERNOR_GRANT_MISSING",
    "DurableGovernorGrantResolver",
    "GovernorGrantError",
    "governor_grant_ref",
    "canonical_payload_digest",
    "canonical_proactive_intent_args",
    "durable_governor_binding",
]
