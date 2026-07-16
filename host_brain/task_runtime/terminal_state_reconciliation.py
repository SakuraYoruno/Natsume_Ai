"""Signed or exact-system repair of inventoried terminal Graph half-states."""

from __future__ import annotations

import base64
from collections.abc import Mapping
import hashlib
import re
from typing import Any, Callable

from host_brain.contracts.agent_runtime.terminal_reconciliation import (
    TerminalReconciliationStoreRejected,
    build_terminal_reconciliation_attestation_binding,
)

from .ports import TerminalReconciliationStorePort, TaskOutboxFactoryPort


TERMINAL_RECONCILIATION_INVALID = "TERMINAL_RECONCILIATION_INVALID"
TERMINAL_RECONCILIATION_REJECTED = "TERMINAL_RECONCILIATION_REJECTED"
TERMINAL_RECONCILIATION_UNCERTAIN = "TERMINAL_RECONCILIATION_UNCERTAIN"

_DURABLE_REFERENCE_PATTERN = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://"
    r"[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{0,511}$"
)
_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_SIGNATURE_PATTERN = re.compile(r"^ed25519:[A-Za-z0-9_-]{86}==$")
_ACTIONS = {
    "RELEASE_BUDGET",
    "FAIL_GRAPH",
    "FAIL_GRAPH_AND_RELEASE_BUDGET",
    "NONE",
}


class TerminalStateReconciliationError(RuntimeError):
    """Nominal reconciliation failure with explicit mutation uncertainty."""

    def __init__(
        self,
        reason_code: str,
        detail: str,
        *,
        side_effects_may_have_run: bool,
    ) -> None:
        self.reason_code = str(reason_code or TERMINAL_RECONCILIATION_REJECTED)
        self.detail = str(detail or "terminal reconciliation failed")
        self.side_effects_may_have_run = bool(side_effects_may_have_run)
        super().__init__(f"{self.reason_code}: {self.detail}")


class TerminalStateReconciliationService:
    """Apply one immutable case without becoming a second execution runtime."""

    __slots__ = ("_clock", "_outbox_factory", "_store")

    def __init__(
        self,
        *,
        task_store: TerminalReconciliationStorePort,
        outbox_factory: TaskOutboxFactoryPort,
        clock_ms: Callable[[], int],
    ) -> None:
        if not callable(clock_ms):
            raise TypeError("clock_ms must be callable")
        self._store = task_store
        self._outbox_factory = outbox_factory
        self._clock = clock_ms

    def reconcile_terminal_state(
        self,
        *,
        case_id: str,
        expected_case_snapshot_sha256: str,
        expected_subject_snapshot_sha256: str,
        action: str,
        target_graph_status: str | None,
        target_budget_status: str | None,
        reconciler_actor_id: str,
        issued_ts_ms: int,
        attestation_ref: str,
        attestation_key_id: str,
        attestation_signature: str,
    ) -> Mapping[str, Any]:
        """Apply an Operator-signed repair or exactly replay it."""

        request = self._normalize_request(
            case_id=case_id,
            expected_case_snapshot_sha256=expected_case_snapshot_sha256,
            expected_subject_snapshot_sha256=expected_subject_snapshot_sha256,
            action=action,
            target_graph_status=target_graph_status,
            target_budget_status=target_budget_status,
            authority_kind="SIGNED_OPERATOR",
            reconciler_actor_id=reconciler_actor_id,
            issued_ts_ms=issued_ts_ms,
            attestation_ref=attestation_ref,
            attestation_key_id=attestation_key_id,
            attestation_signature=attestation_signature,
        )
        return self._apply(request)

    def build_operator_challenge(
        self,
        *,
        case_id: str,
        reconciler_actor_id: str,
        issued_ts_ms: int,
        attestation_ref: str,
        attestation_key_id: str,
    ) -> Mapping[str, Any]:
        """Return exact bytes an offline Operator can sign without writing.

        The case's recommended action and target matrix are server-derived.
        A second trusted read rejects a challenge if its case or current
        subject changed while the deterministic artifacts were being built.
        """

        identity = self._normalize_operator_challenge_identity(
            case_id=case_id,
            reconciler_actor_id=reconciler_actor_id,
            issued_ts_ms=issued_ts_ms,
            attestation_ref=attestation_ref,
            attestation_key_id=attestation_key_id,
        )
        current = self._read_operator_challenge_case(identity["case_id"])
        case = self._mapping(current.get("case"), field_name="case")
        subject = self._mapping(current.get("subject"), field_name="subject")
        if isinstance(current.get("proof"), Mapping):
            self._reject("terminal reconciliation case already has a proof")
        action = self._text(case.get("recommended_action")).upper()
        target_graph, target_budget = self._targets_for_action(
            action=action,
            graph_status=self._text(case.get("graph_status")).upper(),
        )
        request = {
            "case_id": identity["case_id"],
            "expected_case_snapshot_sha256": self._text(
                case.get("snapshot_sha256")
            ),
            "expected_subject_snapshot_sha256": self._text(
                subject.get("snapshot_sha256")
            ),
            "action": action,
            "target_graph_status": target_graph,
            "target_budget_status": target_budget,
            "authority_kind": "SIGNED_OPERATOR",
            "reconciler_actor_id": identity["reconciler_actor_id"],
            "issued_ts_ms": identity["issued_ts_ms"],
            "attestation_ref": identity["attestation_ref"],
            "attestation_key_id": identity["attestation_key_id"],
            "attestation_signature": None,
        }
        if (
            not _DIGEST_PATTERN.fullmatch(
                request["expected_case_snapshot_sha256"]
            )
            or not _DIGEST_PATTERN.fullmatch(
                request["expected_subject_snapshot_sha256"]
            )
        ):
            self._reject("terminal reconciliation challenge digest is invalid")
        self._validate_current(case=case, subject=subject, request=request)
        artifacts = self._build_artifacts(
            case=case,
            subject=subject,
            request=request,
        )
        try:
            binding, binding_bytes = (
                build_terminal_reconciliation_attestation_binding(
                    case=case,
                    subject_snapshot_sha256=request[
                        "expected_subject_snapshot_sha256"
                    ],
                    action=action,
                    target_graph_status=target_graph,
                    target_budget_status=target_budget,
                    authority_kind="SIGNED_OPERATOR",
                    reconciler_actor_id=identity["reconciler_actor_id"],
                    attestation_ref=identity["attestation_ref"],
                    attestation_key_id=identity["attestation_key_id"],
                    issued_ts_ms=identity["issued_ts_ms"],
                    graph_outbox=artifacts["graph_outbox"],
                    budget_outbox=artifacts["budget_outbox"],
                )
            )
        except (TypeError, ValueError) as exc:
            raise TerminalStateReconciliationError(
                TERMINAL_RECONCILIATION_REJECTED,
                "terminal reconciliation challenge could not be encoded",
                side_effects_may_have_run=False,
            ) from exc

        confirmed = self._read_operator_challenge_case(identity["case_id"])
        confirmed_case = self._mapping(
            confirmed.get("case"), field_name="confirmed case"
        )
        confirmed_subject = self._mapping(
            confirmed.get("subject"), field_name="confirmed subject"
        )
        if isinstance(confirmed.get("proof"), Mapping):
            self._reject("terminal reconciliation case was proved during challenge")
        self._validate_current(
            case=confirmed_case,
            subject=confirmed_subject,
            request=request,
        )
        binding_digest = "sha256:" + hashlib.sha256(binding_bytes).hexdigest()
        return {
            "schema_version": (
                "Natsume.task_runtime.terminal_reconciliation.operator_challenge.v1"
            ),
            "case_id": identity["case_id"],
            "case_snapshot_sha256": request[
                "expected_case_snapshot_sha256"
            ],
            "subject_snapshot_sha256": request[
                "expected_subject_snapshot_sha256"
            ],
            "action": action,
            "target_graph_status": target_graph,
            "target_budget_status": target_budget,
            "authority": {
                "authority_kind": "SIGNED_OPERATOR",
                "reconciler_actor_id": identity["reconciler_actor_id"],
                "attestation_ref": identity["attestation_ref"],
                "attestation_key_id": identity["attestation_key_id"],
                "issued_ts_ms": identity["issued_ts_ms"],
            },
            "proof_id": artifacts["proof_id"],
            "graph_outbox_id": (
                artifacts["graph_outbox"].outbox_id
                if artifacts["graph_outbox"] is not None
                else None
            ),
            "budget_outbox_id": (
                artifacts["budget_outbox"].outbox_id
                if artifacts["budget_outbox"] is not None
                else None
            ),
            "binding": binding,
            "signing_material": {
                "encoding": "base64url-canonical-json",
                "payload_b64": base64.urlsafe_b64encode(binding_bytes).decode(
                    "ascii"
                ),
                "payload_sha256": binding_digest,
            },
        }

    def reconcile_exact_cases(self, *, limit: int = 10_000) -> Mapping[str, Any]:
        """Repair only exact failure-bundle cases when the auto flag is enabled."""

        maximum = max(1, min(int(limit), 10_000))
        results: list[Mapping[str, Any]] = []
        observed_count = 0
        drained = False
        while len(results) < maximum:
            batch_size = min(500, maximum - len(results))
            try:
                candidates = self._store.list_terminal_reconciliation_candidates(
                    classification="EXACT_REPAIRABLE",
                    limit=batch_size,
                )
            except Exception as exc:
                committed = (
                    f" after {len(results)} committed case(s)"
                    if results
                    else ""
                )
                raise TerminalStateReconciliationError(
                    TERMINAL_RECONCILIATION_REJECTED,
                    "terminal reconciliation candidates could not be read"
                    + committed,
                    side_effects_may_have_run=bool(results),
                ) from exc
            if not candidates:
                drained = True
                break
            observed_count += len(candidates)
            for aggregate in candidates:
                case = self._mapping(aggregate.get("case"), field_name="case")
                if str(case.get("classification") or "") != "EXACT_REPAIRABLE":
                    self._reject("exact reconciliation query returned a non-exact case")
                subject = self._mapping(
                    aggregate.get("subject"),
                    field_name="subject",
                )
                request = self._normalize_request(
                    case_id=str(case.get("case_id") or ""),
                    expected_case_snapshot_sha256=str(
                        case.get("snapshot_sha256") or ""
                    ),
                    expected_subject_snapshot_sha256=str(
                        subject.get("snapshot_sha256") or ""
                    ),
                    action=str(case.get("recommended_action") or ""),
                    target_graph_status=str(case.get("graph_status") or "") or None,
                    target_budget_status="RELEASED",
                    authority_kind="SYSTEM_DURABLE_EVIDENCE",
                    reconciler_actor_id=(
                        "system://task-runtime/terminal-reconciliation"
                    ),
                    # System evidence must converge across processes.  The
                    # immutable case creation time is part of the durable
                    # inventory and therefore produces one stable proof ID;
                    # a process-local startup clock would create competing
                    # requests for the same case.
                    issued_ts_ms=max(0, int(case.get("created_ts_ms") or 0)),
                    attestation_ref=None,
                    attestation_key_id=None,
                    attestation_signature=None,
                )
                try:
                    results.append(self._apply(request, aggregate=aggregate))
                except TerminalStateReconciliationError as exc:
                    if not results or exc.side_effects_may_have_run:
                        raise
                    raise TerminalStateReconciliationError(
                        exc.reason_code,
                        "terminal reconciliation batch stopped after "
                        f"{len(results)} committed case(s): {exc.detail}",
                        side_effects_may_have_run=True,
                    ) from exc
        remaining = False
        if not drained:
            try:
                remaining = bool(
                    self._store.list_terminal_reconciliation_candidates(
                        classification="EXACT_REPAIRABLE",
                        limit=1,
                    )
                )
            except Exception as exc:
                committed = (
                    f" after {len(results)} committed case(s)"
                    if results
                    else ""
                )
                raise TerminalStateReconciliationError(
                    TERMINAL_RECONCILIATION_REJECTED,
                    "terminal reconciliation completion could not be verified"
                    + committed,
                    side_effects_may_have_run=bool(results),
                ) from exc
        return {
            "status": "TRUNCATED" if remaining else "COMPLETED",
            "candidate_count": observed_count,
            "reconciled_count": len(results),
            "results": tuple(results),
            "remaining_exact_cases": remaining,
        }

    def _apply(
        self,
        request: Mapping[str, Any],
        *,
        aggregate: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        try:
            current = (
                aggregate
                if aggregate is not None
                else self._store.read_terminal_reconciliation_case(request["case_id"])
            )
            case = self._mapping(current.get("case"), field_name="case")
            subject = self._mapping(current.get("subject"), field_name="subject")
            proof = current.get("proof")
        except TerminalStateReconciliationError:
            raise
        except Exception as exc:
            raise TerminalStateReconciliationError(
                TERMINAL_RECONCILIATION_REJECTED,
                "terminal reconciliation case could not be read",
                side_effects_may_have_run=False,
            ) from exc
        self._validate_current(case=case, subject=subject, request=request)
        artifacts = self._build_artifacts(case=case, subject=subject, request=request)
        if isinstance(proof, Mapping):
            self._validate_terminal_proof(proof, request=request, artifacts=artifacts)
            return self._success(current, replayed=True)
        try:
            settled = self._store.record_terminal_reconciliation_proof(
                request["case_id"],
                expected_case_snapshot_sha256=request[
                    "expected_case_snapshot_sha256"
                ],
                expected_subject_snapshot_sha256=request[
                    "expected_subject_snapshot_sha256"
                ],
                action=request["action"],
                target_graph_status=request["target_graph_status"],
                target_budget_status=request["target_budget_status"],
                authority_kind=request["authority_kind"],
                reconciler_actor_id=request["reconciler_actor_id"],
                issued_ts_ms=request["issued_ts_ms"],
                proof_id=artifacts["proof_id"],
                graph_outbox=artifacts["graph_outbox"],
                budget_outbox=artifacts["budget_outbox"],
                attestation_ref=request["attestation_ref"],
                attestation_key_id=request["attestation_key_id"],
                attestation_signature=request["attestation_signature"],
            )
        except TerminalReconciliationStoreRejected as exc:
            raise TerminalStateReconciliationError(
                exc.reason_code,
                exc.detail,
                side_effects_may_have_run=False,
            ) from exc
        except Exception as exc:
            replay = self._terminal_after_unknown_outcome(request, artifacts=artifacts)
            if replay is not None:
                return self._success(replay, replayed=True)
            raise TerminalStateReconciliationError(
                TERMINAL_RECONCILIATION_UNCERTAIN,
                "terminal reconciliation outcome is uncertain",
                side_effects_may_have_run=True,
            ) from exc
        try:
            settled_mapping = self._mapping(settled, field_name="settled result")
            proof = self._mapping(
                settled_mapping.get("proof"),
                field_name="settled proof",
            )
            self._validate_terminal_proof(proof, request=request, artifacts=artifacts)
        except Exception as exc:
            if isinstance(exc, TerminalStateReconciliationError):
                raise
            raise TerminalStateReconciliationError(
                TERMINAL_RECONCILIATION_UNCERTAIN,
                "Store returned a mismatched terminal reconciliation result",
                side_effects_may_have_run=True,
            ) from exc
        return self._success(settled_mapping, replayed=False)

    def _terminal_after_unknown_outcome(
        self,
        request: Mapping[str, Any],
        *,
        artifacts: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        try:
            aggregate = self._store.read_terminal_reconciliation_case(
                request["case_id"]
            )
            proof = self._mapping(aggregate.get("proof"), field_name="replay proof")
            self._validate_terminal_proof(proof, request=request, artifacts=artifacts)
            return aggregate
        except Exception:
            return None

    def _validate_current(
        self,
        *,
        case: Mapping[str, Any],
        subject: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> None:
        if (
            str(case.get("case_id") or "") != request["case_id"]
            or str(case.get("snapshot_sha256") or "")
            != request["expected_case_snapshot_sha256"]
            or str(subject.get("snapshot_sha256") or "")
            != request["expected_subject_snapshot_sha256"]
            or str(case.get("recommended_action") or "") != request["action"]
            or str(case.get("classification") or "")
            == "QUARANTINED_STRUCTURAL"
        ):
            self._reject("case, current subject, or requested action drifted")
        classification = str(case.get("classification") or "")
        authority = request["authority_kind"]
        if authority == "SYSTEM_DURABLE_EVIDENCE":
            if classification != "EXACT_REPAIRABLE":
                self._reject("system authority is limited to exact repairable cases")
        elif classification not in {"EXACT_REPAIRABLE", "SIGNED_REPAIR_REQUIRED"}:
            self._reject("signed authority cannot repair a quarantined case")
        graph_status = str(case.get("graph_status") or "")
        action = request["action"]
        expected_targets = self._targets_for_action(
            action=action,
            graph_status=graph_status,
        )
        if (
            request["target_graph_status"],
            request["target_budget_status"],
        ) != expected_targets:
            self._reject("requested target matrix is invalid")

    def _build_artifacts(
        self,
        *,
        case: Mapping[str, Any],
        subject: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        authority_ref = request["attestation_ref"] or request["reconciler_actor_id"]
        identity = "\n".join(
            (
                request["case_id"],
                request["expected_subject_snapshot_sha256"],
                request["action"],
                request["authority_kind"],
                authority_ref,
                str(request["issued_ts_ms"]),
            )
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        proof_id = f"terminal-reconciliation-proof://sha256/{digest}"
        evidence_refs = tuple(
            item
            for item in (request["case_id"], proof_id, request["attestation_ref"])
            if item
        )
        graph_outbox = None
        if request["action"] in {"FAIL_GRAPH", "FAIL_GRAPH_AND_RELEASE_BUDGET"}:
            graph_version = int(case["graph_status_version"]) + 1
            graph_id = str(case["graph_id"])
            graph_revision = int(case["graph_revision"])
            graph_outbox = self._outbox_factory.create(
                aggregate_type="TASK_GRAPH",
                aggregate_id=graph_id,
                aggregate_version=graph_version,
                graph_revision=graph_revision,
                event_type="TASK_GRAPH_FAILED",
                payload_ref=(
                    f"taskstore://event/TASK_GRAPH/{graph_id}@{graph_revision}/"
                    f"{graph_version}"
                ),
                idempotency_key=f"terminal-reconciliation:graph:{digest}",
                attempt_id=None,
                evidence_refs=evidence_refs,
                outbox_id=f"outbox::terminal-reconciliation::graph::{digest}",
            )
        budget_outbox = None
        if request["action"] in {
            "RELEASE_BUDGET",
            "FAIL_GRAPH_AND_RELEASE_BUDGET",
        }:
            budget_id = str(case.get("budget_id") or "")
            if not budget_id:
                self._reject("Budget release action lacks a case Budget")
            budget_version = int(case["budget_status_version"]) + 1
            budget_outbox = self._outbox_factory.create(
                aggregate_type="BUDGET_RESERVATION",
                aggregate_id=budget_id,
                aggregate_version=budget_version,
                event_type="BUDGET_RESERVATION_RELEASED",
                payload_ref=(
                    "taskstore://event/BUDGET_RESERVATION/"
                    f"{budget_id}/{budget_version}"
                ),
                idempotency_key=f"terminal-reconciliation:budget:{digest}",
                evidence_refs=evidence_refs,
                outbox_id=f"outbox::terminal-reconciliation::budget::{digest}",
            )
        return {
            "proof_id": proof_id,
            "identity_digest": digest,
            "graph_outbox": graph_outbox,
            "budget_outbox": budget_outbox,
            "subject": subject,
        }

    def _validate_terminal_proof(
        self,
        proof: Mapping[str, Any],
        *,
        request: Mapping[str, Any],
        artifacts: Mapping[str, Any],
    ) -> None:
        graph_outbox = artifacts["graph_outbox"]
        budget_outbox = artifacts["budget_outbox"]
        if (
            str(proof.get("proof_id") or "") != artifacts["proof_id"]
            or str(proof.get("case_id") or "") != request["case_id"]
            or str(proof.get("case_snapshot_sha256") or "")
            != request["expected_case_snapshot_sha256"]
            or str(proof.get("subject_snapshot_sha256") or "")
            != request["expected_subject_snapshot_sha256"]
            or str(proof.get("action") or "") != request["action"]
            or self._optional_text(proof.get("target_graph_status"))
            != request["target_graph_status"]
            or self._optional_text(proof.get("target_budget_status"))
            != request["target_budget_status"]
            or str(proof.get("authority_kind") or "")
            != request["authority_kind"]
            or str(proof.get("reconciler_actor_id") or "")
            != request["reconciler_actor_id"]
            or self._safe_int(proof.get("issued_ts_ms"))
            != request["issued_ts_ms"]
            or self._optional_text(proof.get("attestation_ref"))
            != request["attestation_ref"]
            or self._optional_text(proof.get("attestation_key_id"))
            != request["attestation_key_id"]
            or self._optional_text(proof.get("attestation_signature"))
            != request["attestation_signature"]
            or self._optional_text(proof.get("graph_outbox_id"))
            != (graph_outbox.outbox_id if graph_outbox is not None else None)
            or self._optional_text(proof.get("budget_outbox_id"))
            != (budget_outbox.outbox_id if budget_outbox is not None else None)
        ):
            raise TerminalStateReconciliationError(
                TERMINAL_RECONCILIATION_UNCERTAIN,
                "persisted terminal reconciliation proof differs from the request",
                side_effects_may_have_run=True,
            )

    def _read_operator_challenge_case(self, case_id: str) -> Mapping[str, Any]:
        try:
            value = self._store.read_terminal_reconciliation_case(case_id)
            return self._mapping(value, field_name="terminal reconciliation aggregate")
        except TerminalStateReconciliationError:
            raise
        except Exception as exc:
            raise TerminalStateReconciliationError(
                TERMINAL_RECONCILIATION_REJECTED,
                "terminal reconciliation challenge case could not be read",
                side_effects_may_have_run=False,
            ) from exc

    @classmethod
    def _normalize_operator_challenge_identity(cls, **raw: Any) -> dict[str, Any]:
        normalized = {
            "case_id": cls._text(raw.get("case_id")),
            "reconciler_actor_id": cls._text(raw.get("reconciler_actor_id")),
            "issued_ts_ms": raw.get("issued_ts_ms"),
            "attestation_ref": cls._text(raw.get("attestation_ref")),
            "attestation_key_id": cls._text(raw.get("attestation_key_id")),
        }
        if (
            not _DURABLE_REFERENCE_PATTERN.fullmatch(normalized["case_id"])
            or not normalized["reconciler_actor_id"]
            or type(normalized["issued_ts_ms"]) is not int
            or normalized["issued_ts_ms"] < 0
            or not _DURABLE_REFERENCE_PATTERN.fullmatch(
                normalized["attestation_ref"]
            )
            or not normalized["attestation_key_id"]
        ):
            raise TerminalStateReconciliationError(
                TERMINAL_RECONCILIATION_INVALID,
                "terminal reconciliation challenge identity is invalid",
                side_effects_may_have_run=False,
            )
        return normalized

    @classmethod
    def _targets_for_action(
        cls,
        *,
        action: str,
        graph_status: str,
    ) -> tuple[str | None, str | None]:
        targets = {
            "RELEASE_BUDGET": (graph_status, "RELEASED"),
            "FAIL_GRAPH": ("FAILED", None),
            "FAIL_GRAPH_AND_RELEASE_BUDGET": ("FAILED", "RELEASED"),
            "NONE": (None, None),
        }
        try:
            return targets[action]
        except KeyError:
            cls._reject("terminal reconciliation case has no signable action")
            raise AssertionError("unreachable")

    @classmethod
    def _normalize_request(cls, **raw: Any) -> dict[str, Any]:
        case_id = cls._text(raw.get("case_id"))
        case_digest = cls._text(raw.get("expected_case_snapshot_sha256"))
        subject_digest = cls._text(raw.get("expected_subject_snapshot_sha256"))
        action = cls._text(raw.get("action")).upper()
        authority_kind = cls._text(raw.get("authority_kind")).upper()
        actor = cls._text(raw.get("reconciler_actor_id"))
        target_graph = cls._optional_text(raw.get("target_graph_status"))
        target_budget = cls._optional_text(raw.get("target_budget_status"))
        if target_graph is not None:
            target_graph = target_graph.upper()
        if target_budget is not None:
            target_budget = target_budget.upper()
        issued = raw.get("issued_ts_ms")
        attestation_ref = cls._optional_text(raw.get("attestation_ref"))
        key_id = cls._optional_text(raw.get("attestation_key_id"))
        signature = cls._optional_text(raw.get("attestation_signature"))
        common_invalid = (
            not _DURABLE_REFERENCE_PATTERN.fullmatch(case_id)
            or not _DIGEST_PATTERN.fullmatch(case_digest)
            or not _DIGEST_PATTERN.fullmatch(subject_digest)
            or action not in _ACTIONS
            or authority_kind
            not in {"SYSTEM_DURABLE_EVIDENCE", "SIGNED_OPERATOR"}
            or not actor
            or type(issued) is not int
            or issued < 0
        )
        signed_invalid = authority_kind == "SIGNED_OPERATOR" and (
            attestation_ref is None
            or not _DURABLE_REFERENCE_PATTERN.fullmatch(attestation_ref)
            or not key_id
            or signature is None
            or not _SIGNATURE_PATTERN.fullmatch(signature)
        )
        system_invalid = authority_kind == "SYSTEM_DURABLE_EVIDENCE" and any(
            value is not None for value in (attestation_ref, key_id, signature)
        )
        if common_invalid or signed_invalid or system_invalid:
            raise TerminalStateReconciliationError(
                TERMINAL_RECONCILIATION_INVALID,
                "terminal reconciliation request fields are invalid",
                side_effects_may_have_run=False,
            )
        return {
            "case_id": case_id,
            "expected_case_snapshot_sha256": case_digest,
            "expected_subject_snapshot_sha256": subject_digest,
            "action": action,
            "target_graph_status": target_graph,
            "target_budget_status": target_budget,
            "authority_kind": authority_kind,
            "reconciler_actor_id": actor,
            "issued_ts_ms": issued,
            "attestation_ref": attestation_ref,
            "attestation_key_id": key_id,
            "attestation_signature": signature,
        }

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        if type(value) is not int:
            return None
        return value

    @staticmethod
    def _success(value: Mapping[str, Any], *, replayed: bool) -> Mapping[str, Any]:
        case = dict(value.get("case") or {})
        proof = dict(value.get("proof") or {})
        graph = dict(value.get("graph") or {})
        budget_raw = value.get("budget")
        budget = dict(budget_raw or {}) if budget_raw is not None else None
        return {
            "status": "RECONCILED",
            "case": {
                "case_id": case.get("case_id"),
                "case_kind": case.get("case_kind"),
                "classification": case.get("classification"),
                "graph_id": case.get("graph_id"),
                "graph_revision": case.get("graph_revision"),
            },
            "proof": {
                "proof_id": proof.get("proof_id"),
                "authority_kind": proof.get("authority_kind"),
                "action": proof.get("action"),
                "target_graph_status": proof.get("target_graph_status"),
                "target_budget_status": proof.get("target_budget_status"),
                "case_snapshot_sha256": proof.get("case_snapshot_sha256"),
                "subject_snapshot_sha256": proof.get("subject_snapshot_sha256"),
                "graph_outbox_id": proof.get("graph_outbox_id"),
                "budget_outbox_id": proof.get("budget_outbox_id"),
                "issued_ts_ms": proof.get("issued_ts_ms"),
            },
            "graph": {
                "graph_id": graph.get("graph_id") or case.get("graph_id"),
                "graph_revision": (
                    graph.get("revision")
                    or graph.get("graph_revision")
                    or case.get("graph_revision")
                ),
                "status": graph.get("status"),
                "status_version": graph.get("status_version"),
            },
            "budget": (
                {
                    "budget_id": budget.get("budget_id") or case.get("budget_id"),
                    "status": budget.get("status"),
                    "status_version": budget.get("status_version"),
                }
                if budget is not None
                else None
            ),
            "replayed": bool(replayed),
        }

    @staticmethod
    def _mapping(value: Any, *, field_name: str) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError(f"{field_name} must be a mapping")
        return value

    @staticmethod
    def _text(value: Any) -> str:
        return str(getattr(value, "value", value) or "").strip()

    @classmethod
    def _optional_text(cls, value: Any) -> str | None:
        return cls._text(value) or None

    @staticmethod
    def _reject(detail: str) -> None:
        raise TerminalStateReconciliationError(
            TERMINAL_RECONCILIATION_REJECTED,
            detail,
            side_effects_may_have_run=False,
        )


__all__ = [
    "TERMINAL_RECONCILIATION_INVALID",
    "TERMINAL_RECONCILIATION_REJECTED",
    "TERMINAL_RECONCILIATION_UNCERTAIN",
    "TerminalStateReconciliationError",
    "TerminalStateReconciliationService",
]
