"""Governor intent decision entrypoint with intent+gate+audit contracts."""

from __future__ import annotations

from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.ids import new_request_id, new_turn_id, now_ms
from host_brain.core.metabolism import (
    build_governance_snapshot,
    governance_snapshot_ref,
    hard_state_ref,
    normalize_hard_state,
)
from host_brain.governor.rules import evaluate_governor_gates
from host_brain.governor.schemas import (
    DECISION_APPROVE,
    EXPIRED_INTENT,
    INTENT_PARSE_FAILED,
    REPLAY_DETECTED,
    SEQ_INVALID,
    lenient_parse_intent,
    strict_validate_intent,
    validate_governor_output,
)
from host_brain.governor.tokens import TokenService


class IntentDecisionEngine:
    """Governor engine for intent validation and R1-R7 gate evaluation."""

    def __init__(
        self,
        *,
        ledger: SQLiteLedger | None = None,
        token_service: TokenService | None = None,
        expiry_window_ms: int = 5 * 60 * 1000,
    ) -> None:
        self.ledger = ledger or SQLiteLedger()
        self.token_service = token_service
        self.expiry_window_ms = expiry_window_ms

    def evaluate_intent(
        self,
        raw_intent: str | dict[str, Any],
        *,
        request_id: str | None = None,
        thread_id: str | None = None,
        turn_id: str | None = None,
        current_ms: int | None = None,
        proposer_node_id: str = "core_reasoner_primary",
        model_snapshot: str = "unknown_model",
        prompt_profile_id: str = "default_profile",
        tool_visibility_hash: str = "",
        governance_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        req_id = request_id or new_request_id()
        now = current_ms if current_ms is not None else now_ms()
        turn = turn_id or new_turn_id()
        snapshot_refs = _derive_snapshot_refs(governance_snapshot)

        parse = lenient_parse_intent(raw_intent)
        normalized = parse.normalized_json or {}
        session_id = normalized.get("session_id")
        seq = normalized.get("seq")
        intent_id = normalized.get("id")
        nonce = normalized.get("nonce")

        proposal_row = {
            "request_id": req_id,
            "thread_id": thread_id,
            "turn_id": turn,
            "intent_id": intent_id,
            "session_id": session_id,
            "seq": seq,
            "ts_ms": normalized.get("ts_ms"),
            "nonce": nonce,
            "parse_stage": parse.parse_stage,
            "raw_text": raw_intent if isinstance(raw_intent, str) else None,
            "normalized_json": parse.normalized_json,
            "proposer_node_id": proposer_node_id,
            "model_snapshot": model_snapshot,
            "prompt_profile_id": prompt_profile_id,
            "tool_visibility_hash": tool_visibility_hash,
            "proposed_intent_draft_json": parse.normalized_json,
            "lint_result": parse.lint_result,
        }

        if parse.reason_code == INTENT_PARSE_FAILED:
            self.ledger.log_intent(proposal_row)
            return self._reject(
                req_id,
                intent_id,
                INTENT_PARSE_FAILED,
                stage="S_INTENT_PARSE",
                turn_id=turn,
                thread_id=thread_id,
                rule_trace=[{"gate_id": "INTENT_PARSE", "hit": True, "reason_hint": INTENT_PARSE_FAILED}],
                ts_ms=now,
                snapshot_refs=snapshot_refs,
            )

        envelope, reason_code, lint = strict_validate_intent(normalized)
        proposal_row["lint_result"] = lint
        self.ledger.log_intent(proposal_row)
        if reason_code is not None:
            return self._reject(
                req_id,
                intent_id,
                reason_code,
                stage="S_INTENT_VALIDATE",
                turn_id=turn,
                thread_id=thread_id,
                rule_trace=[{"gate_id": "INTENT_VALIDATE", "hit": True, "reason_hint": reason_code}],
                ts_ms=now,
                snapshot_refs=snapshot_refs,
            )

        assert envelope is not None

        if self.ledger.has_replay(
            intent_id=envelope.id,
            session_id=envelope.session_id,
            nonce=envelope.nonce,
        ):
            return self._reject(
                req_id,
                envelope.id,
                REPLAY_DETECTED,
                stage="S_INTENT_REPLAY",
                turn_id=turn,
                thread_id=thread_id,
                rule_trace=[{"gate_id": "INTENT_REPLAY", "hit": True, "reason_hint": REPLAY_DETECTED}],
                ts_ms=now,
                snapshot_refs=snapshot_refs,
            )

        if self.ledger.is_seq_invalid(session_id=envelope.session_id, seq=envelope.seq):
            return self._reject(
                req_id,
                envelope.id,
                SEQ_INVALID,
                stage="S_INTENT_SEQ",
                turn_id=turn,
                thread_id=thread_id,
                rule_trace=[{"gate_id": "INTENT_SEQ", "hit": True, "reason_hint": SEQ_INVALID}],
                ts_ms=now,
                snapshot_refs=snapshot_refs,
            )

        if _is_expired(ts_ms=envelope.ts_ms, now_ms_value=now, ttl_ms=self.expiry_window_ms):
            return self._reject(
                req_id,
                envelope.id,
                EXPIRED_INTENT,
                stage="S_INTENT_EXPIRED",
                turn_id=turn,
                thread_id=thread_id,
                rule_trace=[{"gate_id": "INTENT_EXPIRY", "hit": True, "reason_hint": EXPIRED_INTENT}],
                ts_ms=now,
                snapshot_refs=snapshot_refs,
            )

        gates_input = _build_gate_input(governance_snapshot, target_scope=envelope.target)
        snapshot_refs = {
            "governance_snapshot": governance_snapshot_ref(gates_input),
            "hard_state": hard_state_ref(_hard_state_from_snapshot(governance_snapshot)),
        }
        gate_eval = evaluate_governor_gates(gates_input)
        if gate_eval["decision"] == "REJECT":
            return self._reject(
                req_id,
                envelope.id,
                gate_eval["reason_code"],
                stage="S_GOV_GATES",
                turn_id=turn,
                thread_id=thread_id,
                rule_trace=gate_eval["rule_trace"],
                ts_ms=now,
                snapshot_refs=snapshot_refs,
            )

        self.ledger.mark_intent_seen(
            intent_id=envelope.id,
            session_id=envelope.session_id,
            nonce=envelope.nonce,
        )
        self.ledger.update_seq(session_id=envelope.session_id, seq=envelope.seq)

        approval_token_ref = f"approval::{envelope.id}"
        if self.token_service is not None:
            approval_token_ref = self.token_service.issue_approval_token(
                intent_id=envelope.id,
                skill_name=envelope.action,
                session_id=envelope.session_id,
                seq=envelope.seq,
                now_ts_ms=now,
            )

        row = {
            "request_id": req_id,
            "thread_id": thread_id,
            "turn_id": turn,
            "intent_id": envelope.id,
            "decision": DECISION_APPROVE,
            "reason_code": None,
            "rule_trace": gate_eval["rule_trace"],
            "tokens": {"approval_token_ref": approval_token_ref},
            "snapshot_refs": snapshot_refs,
            "stage": "S_INTENT_OK",
            "ts_ms": now,
        }
        return self._finalize_governor_row(row)

    def _reject(
        self,
        request_id: str,
        intent_id: str | None,
        reason_code: str,
        *,
        stage: str,
        thread_id: str | None,
        turn_id: str,
        rule_trace: list[dict[str, Any]],
        ts_ms: int,
        snapshot_refs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        row = {
            "request_id": request_id,
            "thread_id": thread_id,
            "turn_id": turn_id,
            "intent_id": intent_id,
            "decision": "REJECT",
            "reason_code": reason_code,
            "rule_trace": rule_trace,
            "snapshot_refs": snapshot_refs or {"governance_snapshot": "runtime"},
            "stage": stage,
            "ts_ms": ts_ms,
        }
        return self._finalize_governor_row(row)

    def _finalize_governor_row(self, row: dict[str, Any]) -> dict[str, Any]:
        validation = validate_governor_output(row)
        if not validation.valid:
            row = {
                "request_id": row.get("request_id"),
                "thread_id": row.get("thread_id"),
                "turn_id": row.get("turn_id"),
                "intent_id": row.get("intent_id"),
                "decision": "REJECT",
                "reason_code": validation.reason_code,
                "rule_trace": [
                    {
                        "gate_id": "GOVERNOR_OUTPUT_CONTRACT",
                        "hit": True,
                        "reason_hint": validation.reason_code,
                        "violations": validation.violations,
                        "threshold_summary": {"contract": "strict"},
                        "snapshot_summary": {"source": "governor_output"},
                    }
                ],
                "snapshot_refs": row.get("snapshot_refs") or {"governance_snapshot": "runtime"},
                "stage": "S_GOV_OUTPUT_INVALID",
                "ts_ms": now_ms(),
            }
        self.ledger.log_governor(row)
        return row


def _is_expired(*, ts_ms: int, now_ms_value: int, ttl_ms: int) -> bool:
    if ts_ms > now_ms_value + 60_000:
        return True
    return now_ms_value - ts_ms > ttl_ms


def _hard_state_from_snapshot(governance_snapshot: dict[str, Any] | None):
    if isinstance(governance_snapshot, dict):
        raw_state = governance_snapshot.get("hard_state")
        if isinstance(raw_state, dict):
            return normalize_hard_state(raw_state)
        return normalize_hard_state(governance_snapshot)
    return normalize_hard_state({})


def _build_gate_input(
    governance_snapshot: dict[str, Any] | None,
    *,
    target_scope: str,
) -> dict[str, Any]:
    source = dict(governance_snapshot or {})
    state = _hard_state_from_snapshot(governance_snapshot)
    gates_input = build_governance_snapshot(state, overrides=source)
    if "scope" not in source:
        gates_input["scope"] = str(target_scope).strip().lower() or "host"
    return gates_input


def _derive_snapshot_refs(governance_snapshot: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(governance_snapshot, dict):
        return {"governance_snapshot": "runtime"}
    state = _hard_state_from_snapshot(governance_snapshot)
    gates_input = _build_gate_input(governance_snapshot, target_scope="host")
    return {
        "governance_snapshot": governance_snapshot_ref(gates_input),
        "hard_state": hard_state_ref(state),
    }
