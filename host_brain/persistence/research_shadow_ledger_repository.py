"""Append-only SQLite owner for D20b Research shadow evidence."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
from typing import Any, Callable, Iterator

from host_brain.core.ids import now_ms
from host_brain.contracts.planning_search.research_benchmark_contracts import (
    ExternalProviderRunAttestationV1,
    PairedResearchRunReceiptV1,
    ResearchArmPlanV1,
    ResearchCorpusBindingV1,
    ResearchExecutionBudgetV1,
    ResearchExecutionProfileV1,
    ResearchModelExecutionReceiptV1,
    ResearchVerifierReceiptV1,
)
from host_brain.persistence.research_shadow_ledger_codec import (
    ResearchArmExecutionSnapshot,
    ResearchArmOperationSnapshot,
    ResearchArmOperationState,
    ResearchPairedRunSpecSnapshot,
    ResearchShadowLedgerError,
    ResearchTerminalReceiptSnapshot,
    arm_execution_snapshot_from_row as _arm_execution_snapshot_from_row,
    as_sequence as _as_sequence,
    bounded_positive_int as _bounded_positive_int,
    canonical_object as _canonical_object,
    non_negative_operation_int as _non_negative_operation_int,
    operation_digest as _operation_digest,
    operation_role as _operation_role,
    operation_text as _operation_text,
    paired_arm_order as _paired_arm_order,
    paired_receipt_from_dict as _paired_receipt_from_dict,
    research_arm_plan_from_dict as _research_arm_plan_from_dict,
    research_binding_from_dict as _research_binding_from_dict,
    research_budget_from_dict as _research_budget_from_dict,
    research_model_receipt_from_dict as _research_model_receipt_from_dict,
    research_profile_from_dict as _research_profile_from_dict,
    research_verifier_receipt_from_dict as _research_verifier_receipt_from_dict,
    run_spec_plan as _run_spec_plan,
)

RESEARCH_SHADOW_MIGRATION_ASSET_KEY = "0107#research_shadow_ledger"
RESEARCH_SHADOW_ARM_OPERATION_MIGRATION_ASSET_KEY = "0121#research_shadow_arm_operation"
_OWNER_RANDOMIZED_RUN_SPEC_SCHEMA = "natsume.research-run-spec.owner-randomized.v1"
_OWNER_RANDOMIZATION_ALGORITHM = "PYTHON_SECRETS_RANDBELOW_V1"


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class SQLiteResearchShadowLedger:
    """Persist registration, run spec, arm receipts and terminal pair receipt."""

    def __init__(self, db_path: str | Path, *, clock: Callable[[], int] = now_ms) -> None:
        raw = str(db_path or "").strip()
        if not raw or raw == ":memory:":
            raise ResearchShadowLedgerError("Research ledger requires a durable SQLite path")
        self.db_path = Path(raw).expanduser().resolve()
        self._clock = clock
        self.assert_schema_ready()

    def assert_schema_ready(self) -> None:
        if not self.db_path.is_file():
            raise ResearchShadowLedgerError("Research ledger database does not exist")
        with self._connection() as conn:
            names = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table','trigger')"
                )
            }
            required = {
                "planning_search_research_registration",
                "planning_search_research_run_spec",
                "planning_search_research_arm_execution",
                "planning_search_research_terminal_receipt",
                "research_run_spec_registration_bound",
                "research_arm_execution_bound",
                "research_terminal_pair_complete",
            }
            if not required.issubset(names):
                raise ResearchShadowLedgerError("migration 0107 Research shadow schema is not ready")

    def register(
        self,
        binding: ResearchCorpusBindingV1,
        profile: ResearchExecutionProfileV1,
        budget: ResearchExecutionBudgetV1,
    ) -> str:
        registration_digest = _digest(
            {"binding": binding.to_dict(), "profile": profile.to_dict(), "budget": budget.to_dict()}
        )
        registration_id = f"researchreg_{registration_digest[:32]}"
        payload = {
            "schema": "natsume.research-benchmark.shadow.v1",
            "registration_id": registration_id,
            "binding": binding.to_dict(),
            "profile": profile.to_dict(),
            "budget": budget.to_dict(),
            "production_influence_enabled": False,
        }
        with self._transaction() as conn:
            existing = conn.execute(
                "SELECT registration_json FROM planning_search_research_registration WHERE registration_id = ?",
                (registration_id,),
            ).fetchone()
            encoded = _canonical(payload)
            if existing is not None:
                if str(existing[0]) != encoded:
                    raise ResearchShadowLedgerError("Research registration replay drift")
                return registration_id
            conn.execute(
                """
                INSERT INTO planning_search_research_registration
                (registration_id, binding_digest, profile_digest, budget_digest,
                 registration_json, production_influence_enabled, created_at_ms)
                VALUES (?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    registration_id,
                    binding.binding_digest,
                    profile.profile_digest,
                    budget.budget_digest,
                    encoded,
                    self._clock(),
                ),
            )
        return registration_id

    def preregister_randomized_run(
        self,
        *,
        registration_id: str,
        baseline: ResearchArmPlanV1,
        selected: ResearchArmPlanV1,
        cohort_run_key: str,
    ) -> str:
        """Create or reopen a pair whose order is chosen by this ledger owner.

        The caller supplies only a cohort identity.  Its raw value is never
        persisted; the stable run identity binds its digest while the actual
        order is chosen under the same SQLite write transaction.  Exact
        replays reopen the existing spec before entropy is requested again.
        """

        if not isinstance(baseline, ResearchArmPlanV1) or not isinstance(
            selected,
            ResearchArmPlanV1,
        ):
            raise ResearchShadowLedgerError("Research paired run requires ResearchArmPlanV1 arms")
        if baseline.arm_id == selected.arm_id:
            raise ResearchShadowLedgerError("Research paired arms require different ids")
        normalized_registration_id = _operation_text(
            registration_id,
            field="registration_id",
            limit=255,
        )
        cohort_run_key_digest = _digest(
            {
                "cohort_run_key": _operation_text(
                    cohort_run_key,
                    field="cohort_run_key",
                    limit=512,
                )
            }
        )
        identity = _owner_randomized_run_identity(
            registration_id=normalized_registration_id,
            baseline=baseline,
            selected=selected,
            cohort_run_key_digest=cohort_run_key_digest,
        )
        run_id = f"researchrun_{_digest(identity)[:32]}"
        with self._transaction() as conn:
            existing = conn.execute(
                """
                SELECT spec_json, spec_digest, registration_id, baseline_arm_id, selected_arm_id,
                       randomized_arm_order, production_influence_enabled
                FROM planning_search_research_run_spec
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            if existing is not None:
                expected = _assert_owner_randomized_run_replay(
                    raw=_canonical_object(existing[0], label="Research RunSpec"),
                    identity=identity,
                )
                if (
                    str(existing["spec_digest"]) != _digest(expected)
                    or str(existing["registration_id"]) != normalized_registration_id
                    or str(existing["baseline_arm_id"]) != baseline.arm_id
                    or str(existing["selected_arm_id"]) != selected.arm_id
                    or int(existing["randomized_arm_order"]) != 1
                    or int(existing["production_influence_enabled"]) != 0
                ):
                    raise ResearchShadowLedgerError("Research RunSpec replay drift")
                return run_id

            randomization_draw = secrets.randbelow(2)
            arm_order = _owner_randomized_arm_order(randomization_draw)
            run_payload = _owner_randomized_run_payload(
                identity=identity,
                arm_order=arm_order,
                randomization_draw=randomization_draw,
            )
            conn.execute(
                """
                INSERT INTO planning_search_research_run_spec
                (run_id, spec_digest, registration_id, baseline_arm_id, selected_arm_id,
                 spec_json, randomized_arm_order, production_influence_enabled, created_at_ms)
                VALUES (?, ?, ?, ?, ?, ?, 1, 0, ?)
                """,
                (
                    run_id,
                    _digest(run_payload),
                    normalized_registration_id,
                    baseline.arm_id,
                    selected.arm_id,
                    _canonical(run_payload),
                    self._clock(),
                ),
            )
        return run_id

    def get_paired_run_spec(self, *, run_id: str) -> ResearchPairedRunSpecSnapshot:
        """Reopen the exact preregistered pair before any arm is executed.

        The runner consumes this projection rather than caller-supplied plans,
        budgets or order.  It prevents a process restart from silently using a
        different arm order or a drifted registration while retaining no raw
        prompt, output or corpus body in the ledger.
        """

        normalized_run_id = _operation_text(run_id, field="run_id", limit=255)
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT
                    run.run_id,
                    run.spec_digest,
                    run.registration_id,
                    run.baseline_arm_id,
                    run.selected_arm_id,
                    run.spec_json,
                    run.randomized_arm_order AS run_randomized_arm_order,
                    run.production_influence_enabled AS run_production_influence_enabled,
                    registration.binding_digest,
                    registration.profile_digest,
                    registration.budget_digest,
                    registration.registration_json,
                    registration.production_influence_enabled
                      AS registration_production_influence_enabled
                FROM planning_search_research_run_spec AS run
                JOIN planning_search_research_registration AS registration
                  ON registration.registration_id = run.registration_id
                WHERE run.run_id = ?
                """,
                (normalized_run_id,),
            ).fetchone()
            if row is None:
                raise ResearchShadowLedgerError("Research RunSpec is not preregistered")
            return _paired_run_spec_from_row(row)

    def get_arm_execution(
        self,
        *,
        run_id: str,
        role: str,
    ) -> ResearchArmExecutionSnapshot | None:
        """Reconstruct exact arm evidence without trusting serialized flags."""

        spec = self.get_paired_run_spec(run_id=run_id)
        normalized_role = _operation_role(role)
        expected_plan = spec.baseline if normalized_role == "BASELINE" else spec.selected
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT arm_id, evidence_digest, model_receipt_json, verifier_receipt_json
                FROM planning_search_research_arm_execution
                WHERE run_id = ? AND arm_role = ?
                """,
                (spec.run_id, normalized_role),
            ).fetchone()
            if row is None:
                return None
            return _arm_execution_snapshot_from_row(
                run_id=spec.run_id,
                role=normalized_role,
                expected_plan=expected_plan,
                row=row,
            )

    def get_terminal_receipt(
        self,
        *,
        run_id: str,
    ) -> ResearchTerminalReceiptSnapshot | None:
        """Reopen a terminal pair only after revalidating all durable bindings."""

        normalized_run_id = _operation_text(run_id, field="run_id", limit=255)
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT receipt_digest, receipt_json
                FROM planning_search_research_terminal_receipt
                WHERE run_id = ?
                """,
                (normalized_run_id,),
            ).fetchone()
            if row is None:
                return None
            raw = _canonical_object(row["receipt_json"], label="Research terminal receipt")
            receipt = _paired_receipt_from_dict(raw)
            if _digest(receipt.to_dict()) != str(row["receipt_digest"]):
                raise ResearchShadowLedgerError("Research terminal receipt digest drift")
            self._assert_terminal_receipt_bindings(
                conn=conn,
                run_id=normalized_run_id,
                receipt=receipt,
            )
            return ResearchTerminalReceiptSnapshot(
                run_id=normalized_run_id,
                receipt=receipt,
                receipt_digest=str(row["receipt_digest"]),
            )

    def append_arm_execution(
        self,
        *,
        run_id: str,
        role: str,
        plan: ResearchArmPlanV1,
        model: ResearchModelExecutionReceiptV1,
        verifier: ResearchVerifierReceiptV1,
    ) -> None:
        role = str(role or "").strip().upper()
        if role not in {"BASELINE", "SELECTED"}:
            raise ResearchShadowLedgerError("Research arm role is invalid")
        if model.arm_id != plan.arm_id or verifier.execution_id != model.execution_id:
            raise ResearchShadowLedgerError("Research arm receipts are not bound")
        evidence = {"plan": plan.to_dict(), "model": model.to_dict(), "verifier": verifier.to_dict()}
        evidence_digest = _digest(evidence)
        model_json = _canonical(model.to_dict())
        verifier_json = _canonical(verifier.to_dict())
        with self._transaction() as conn:
            existing = conn.execute(
                """
                SELECT arm_id, evidence_digest, model_receipt_json, verifier_receipt_json
                FROM planning_search_research_arm_execution
                WHERE run_id = ? AND arm_role = ?
                """,
                (run_id, role),
            ).fetchone()
            if existing is not None:
                replay = (
                    str(existing["arm_id"]) == plan.arm_id
                    and str(existing["evidence_digest"]) == evidence_digest
                    and str(existing["model_receipt_json"]) == model_json
                    and str(existing["verifier_receipt_json"]) == verifier_json
                )
                if not replay:
                    raise ResearchShadowLedgerError("Research arm execution replay drift")
                return
            conn.execute(
                """
                INSERT INTO planning_search_research_arm_execution
                (run_id, arm_role, arm_id, evidence_digest, model_receipt_json,
                 verifier_receipt_json, created_at_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, role, plan.arm_id, evidence_digest, model_json, verifier_json, self._clock()),
            )

    def complete(self, *, run_id: str, receipt: PairedResearchRunReceiptV1) -> str:
        if not str(run_id or "").strip():
            raise ResearchShadowLedgerError("Research terminal receipt requires run_id")
        payload = receipt.to_dict()
        digest = _digest(payload)
        with self._transaction() as conn:
            self._assert_terminal_receipt_bindings(
                conn=conn,
                run_id=run_id,
                receipt=receipt,
                require_owner_randomization=True,
            )
            encoded = _canonical(payload)
            existing = conn.execute(
                """
                SELECT receipt_digest, receipt_json
                FROM planning_search_research_terminal_receipt
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["receipt_digest"]) != digest
                    or str(existing["receipt_json"]) != encoded
                ):
                    raise ResearchShadowLedgerError("Research terminal receipt replay drift")
                return f"research-receipt://sha256/{digest}"
            conn.execute(
                """
                INSERT INTO planning_search_research_terminal_receipt
                (run_id, receipt_digest, receipt_json, production_influence_enabled, created_at_ms)
                VALUES (?, ?, ?, 0, ?)
                """,
                (run_id, digest, encoded, self._clock()),
            )
        return f"research-receipt://sha256/{digest}"

    def reserve_arm_operation(
        self,
        *,
        run_id: str,
        role: str,
        plan: ResearchArmPlanV1,
        execution_id: str,
    ) -> ResearchArmOperationSnapshot:
        """Reserve one deterministic external-arm identity before any call.

        Reservation is append-only and replay-safe.  It is intentionally
        separate from the 0107 evidence row: a process crash after a model
        request cannot be mistaken for a never-started arm merely because no
        public response/evidence row exists yet.
        """

        normalized_run_id = _operation_text(run_id, field="run_id", limit=255)
        normalized_role = _operation_role(role)
        if not isinstance(plan, ResearchArmPlanV1):
            raise ResearchShadowLedgerError("Research arm operation requires ResearchArmPlanV1")
        normalized_execution_id = _operation_text(
            execution_id,
            field="execution_id",
            limit=160,
        )
        payload = {
            "schema": "natsume.research-shadow.arm-operation.v1",
            "run_id": normalized_run_id,
            "arm_role": normalized_role,
            "arm_id": plan.arm_id,
            "execution_id": normalized_execution_id,
            "plan": plan.to_dict(),
            "production_influence_enabled": False,
        }
        operation_digest = _digest(payload)
        operation_id = f"researchop_{operation_digest[:32]}"
        encoded = _canonical(payload)
        with self._transaction() as conn:
            self._assert_operation_schema_ready(conn)
            self._assert_operation_plan_binding(
                conn=conn,
                run_id=normalized_run_id,
                role=normalized_role,
                plan=plan,
            )
            existing = conn.execute(
                """
                SELECT operation_id, operation_digest, run_id, arm_role, arm_id,
                       execution_id, operation_json
                FROM planning_search_research_arm_operation
                WHERE run_id = ? AND arm_role = ?
                """,
                (normalized_run_id, normalized_role),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["operation_id"]) != operation_id
                    or str(existing["operation_digest"]) != operation_digest
                    or str(existing["run_id"]) != normalized_run_id
                    or str(existing["arm_role"]) != normalized_role
                    or str(existing["arm_id"]) != plan.arm_id
                    or str(existing["execution_id"]) != normalized_execution_id
                    or str(existing["operation_json"]) != encoded
                ):
                    raise ResearchShadowLedgerError("Research arm operation replay drift")
                return self._read_operation_snapshot(conn=conn, operation_id=operation_id)
            conn.execute(
                """
                INSERT INTO planning_search_research_arm_operation
                (operation_id, operation_digest, run_id, arm_role, arm_id,
                 execution_id, operation_json, production_influence_enabled, created_at_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    operation_id,
                    operation_digest,
                    normalized_run_id,
                    normalized_role,
                    plan.arm_id,
                    normalized_execution_id,
                    encoded,
                    self._clock(),
                ),
            )
            self._append_operation_event(
                conn=conn,
                operation_id=operation_id,
                event_kind="RESERVED",
                event_id=f"{operation_id}:reserved",
                detail={"state": "RESERVED"},
                at_ms=self._clock(),
            )
            return self._read_operation_snapshot(conn=conn, operation_id=operation_id)

    def get_arm_operation(
        self,
        *,
        run_id: str,
        role: str,
    ) -> ResearchArmOperationSnapshot | None:
        """Reopen a durable arm-operation projection without mutating it."""

        normalized_run_id = _operation_text(run_id, field="run_id", limit=255)
        normalized_role = _operation_role(role)
        with self._connection() as conn:
            self._assert_operation_schema_ready(conn)
            row = conn.execute(
                """
                SELECT operation_id
                FROM planning_search_research_arm_operation
                WHERE run_id = ? AND arm_role = ?
                """,
                (normalized_run_id, normalized_role),
            ).fetchone()
            if row is None:
                return None
            return self._read_operation_snapshot(conn=conn, operation_id=str(row["operation_id"]))

    def claim_arm_operation(
        self,
        *,
        operation_id: str,
        owner_id: str,
        lease_duration_ms: int,
        at_ms: int | None = None,
    ) -> ResearchArmOperationSnapshot:
        """Fence one caller before it may record an external-call start."""

        normalized_operation_id = _operation_text(
            operation_id,
            field="operation_id",
            limit=255,
        )
        normalized_owner_id = _operation_text(owner_id, field="owner_id", limit=255)
        duration = _bounded_positive_int(
            lease_duration_ms,
            field="lease_duration_ms",
            maximum=86_400_000,
        )
        now_value = self._operation_time(at_ms)
        with self._transaction() as conn:
            self._assert_operation_schema_ready(conn)
            snapshot = self._read_operation_snapshot(conn=conn, operation_id=normalized_operation_id)
            if snapshot.state is ResearchArmOperationState.CALL_CLAIMED:
                if snapshot.claim_expires_at_ms is None or snapshot.claim_fencing_token is None:
                    raise ResearchShadowLedgerError("Research arm claim projection is incomplete")
                if snapshot.claim_expires_at_ms > now_value:
                    if snapshot.claim_owner_id == normalized_owner_id:
                        return snapshot
                    raise ResearchShadowLedgerError("Research arm operation is claimed by another owner")
            elif snapshot.state not in {
                ResearchArmOperationState.RESERVED,
                ResearchArmOperationState.RETRY_AUTHORIZED_NO_AUDIT,
            }:
                raise ResearchShadowLedgerError("Research arm operation is not claimable")

            next_fence = int(snapshot.claim_fencing_token or 0) + 1
            claim_identity = {
                "operation_id": normalized_operation_id,
                "owner_id": normalized_owner_id,
                "fencing_token": next_fence,
            }
            claim_id = f"researchclaim_{_digest(claim_identity)[:32]}"
            expires_at_ms = now_value + duration
            self._append_operation_event(
                conn=conn,
                operation_id=normalized_operation_id,
                event_kind="CALL_CLAIMED",
                event_id=f"{normalized_operation_id}:claim:{next_fence}",
                detail={
                    "claim_id": claim_id,
                    "owner_id": normalized_owner_id,
                    "fencing_token": next_fence,
                    "claimed_at_ms": now_value,
                    "expires_at_ms": expires_at_ms,
                },
                at_ms=now_value,
            )
            return self._read_operation_snapshot(conn=conn, operation_id=normalized_operation_id)

    def begin_external_call(
        self,
        *,
        operation_id: str,
        claim_id: str,
        at_ms: int | None = None,
    ) -> ResearchArmOperationSnapshot:
        """Persist the point after which a restart must inspect model audit."""

        normalized_operation_id = _operation_text(
            operation_id,
            field="operation_id",
            limit=255,
        )
        normalized_claim_id = _operation_text(claim_id, field="claim_id", limit=255)
        now_value = self._operation_time(at_ms)
        with self._transaction() as conn:
            self._assert_operation_schema_ready(conn)
            snapshot = self._read_operation_snapshot(conn=conn, operation_id=normalized_operation_id)
            if snapshot.state is ResearchArmOperationState.EXTERNAL_CALL_STARTED:
                if snapshot.claim_id == normalized_claim_id:
                    return snapshot
                raise ResearchShadowLedgerError("Research arm external call is owned by another claim")
            if snapshot.state is not ResearchArmOperationState.CALL_CLAIMED:
                raise ResearchShadowLedgerError("Research arm external call is not claimable")
            if (
                snapshot.claim_id != normalized_claim_id
                or snapshot.claim_fencing_token is None
                or snapshot.claim_expires_at_ms is None
                or snapshot.claim_expires_at_ms <= now_value
            ):
                raise ResearchShadowLedgerError("Research arm claim is stale or mismatched")
            self._append_operation_event(
                conn=conn,
                operation_id=normalized_operation_id,
                event_kind="EXTERNAL_CALL_STARTED",
                event_id=(
                    f"{normalized_operation_id}:external-started:"
                    f"{snapshot.claim_fencing_token}"
                ),
                detail={
                    "claim_id": normalized_claim_id,
                    "owner_id": snapshot.claim_owner_id,
                    "fencing_token": snapshot.claim_fencing_token,
                    "expires_at_ms": snapshot.claim_expires_at_ms,
                },
                at_ms=now_value,
            )
            return self._read_operation_snapshot(conn=conn, operation_id=normalized_operation_id)

    def authorize_retry_without_audit(
        self,
        *,
        operation_id: str,
        reason_code: str = "CANONICAL_AUDIT_EMPTY",
        at_ms: int | None = None,
    ) -> ResearchArmOperationSnapshot:
        """Record the only automatic retry case: canonical audit is empty.

        The caller must perform the fresh audit lookup before invoking this
        method.  The operation event preserves that recovery disposition; the
        next external request still needs a new fenced claim and the adapter's
        own empty-audit preflight.
        """

        normalized_operation_id = _operation_text(
            operation_id,
            field="operation_id",
            limit=255,
        )
        normalized_reason = _operation_text(reason_code, field="reason_code", limit=128)
        now_value = self._operation_time(at_ms)
        with self._transaction() as conn:
            self._assert_operation_schema_ready(conn)
            snapshot = self._read_operation_snapshot(conn=conn, operation_id=normalized_operation_id)
            if snapshot.state is ResearchArmOperationState.RETRY_AUTHORIZED_NO_AUDIT:
                return snapshot
            if snapshot.state is not ResearchArmOperationState.EXTERNAL_CALL_STARTED:
                raise ResearchShadowLedgerError("Research arm retry authorization is invalid")
            if snapshot.claim_fencing_token is None:
                raise ResearchShadowLedgerError("Research arm retry authorization lacks claim fence")
            self._append_operation_event(
                conn=conn,
                operation_id=normalized_operation_id,
                event_kind="RETRY_AUTHORIZED_NO_AUDIT",
                event_id=(
                    f"{normalized_operation_id}:retry-no-audit:"
                    f"{snapshot.claim_fencing_token}"
                ),
                detail={
                    "reason_code": normalized_reason,
                    "prior_claim_id": snapshot.claim_id,
                    "prior_fencing_token": snapshot.claim_fencing_token,
                },
                at_ms=now_value,
            )
            return self._read_operation_snapshot(conn=conn, operation_id=normalized_operation_id)

    def mark_arm_evidence_persisted(
        self,
        *,
        operation_id: str,
        at_ms: int | None = None,
    ) -> ResearchArmOperationSnapshot:
        """Advance an external arm only after exact 0107 evidence exists."""

        normalized_operation_id = _operation_text(
            operation_id,
            field="operation_id",
            limit=255,
        )
        now_value = self._operation_time(at_ms)
        with self._transaction() as conn:
            self._assert_operation_schema_ready(conn)
            snapshot = self._read_operation_snapshot(conn=conn, operation_id=normalized_operation_id)
            if snapshot.state is ResearchArmOperationState.EVIDENCE_PERSISTED:
                return snapshot
            if snapshot.state is not ResearchArmOperationState.EXTERNAL_CALL_STARTED:
                raise ResearchShadowLedgerError("Research arm evidence state is invalid")
            evidence_row = conn.execute(
                """
                SELECT evidence_digest, model_receipt_json
                FROM planning_search_research_arm_execution
                WHERE run_id = ? AND arm_role = ? AND arm_id = ?
                """,
                (snapshot.run_id, snapshot.arm_role, snapshot.arm_id),
            ).fetchone()
            if evidence_row is None:
                raise ResearchShadowLedgerError("Research arm evidence row is absent")
            model = _canonical_object(
                evidence_row["model_receipt_json"],
                label="Research arm operation model receipt",
            )
            if str(model.get("execution_id") or "") != snapshot.execution_id:
                raise ResearchShadowLedgerError("Research arm evidence execution identity drift")
            evidence_digest = str(evidence_row["evidence_digest"])
            self._append_operation_event(
                conn=conn,
                operation_id=normalized_operation_id,
                event_kind="EVIDENCE_PERSISTED",
                event_id=f"{normalized_operation_id}:evidence-persisted",
                detail={"evidence_digest": evidence_digest},
                at_ms=now_value,
            )
            return self._read_operation_snapshot(conn=conn, operation_id=normalized_operation_id)

    def mark_reconciliation_required(
        self,
        *,
        operation_id: str,
        reason_code: str,
        at_ms: int | None = None,
    ) -> ResearchArmOperationSnapshot:
        """Freeze an ambiguous external arm until explicit reconciliation."""

        normalized_operation_id = _operation_text(
            operation_id,
            field="operation_id",
            limit=255,
        )
        normalized_reason = _operation_text(reason_code, field="reason_code", limit=128)
        now_value = self._operation_time(at_ms)
        with self._transaction() as conn:
            self._assert_operation_schema_ready(conn)
            snapshot = self._read_operation_snapshot(conn=conn, operation_id=normalized_operation_id)
            if snapshot.state is ResearchArmOperationState.RECONCILIATION_REQUIRED:
                if snapshot.reconciliation_reason == normalized_reason:
                    return snapshot
                raise ResearchShadowLedgerError("Research arm reconciliation replay drift")
            if snapshot.state is not ResearchArmOperationState.EXTERNAL_CALL_STARTED:
                raise ResearchShadowLedgerError("Research arm reconciliation state is invalid")
            evidence = conn.execute(
                """
                SELECT 1
                FROM planning_search_research_arm_execution
                WHERE run_id = ? AND arm_role = ?
                """,
                (snapshot.run_id, snapshot.arm_role),
            ).fetchone()
            if evidence is not None:
                raise ResearchShadowLedgerError(
                    "Research arm with persisted evidence cannot require unknown-effect reconciliation"
                )
            self._append_operation_event(
                conn=conn,
                operation_id=normalized_operation_id,
                event_kind="RECONCILIATION_REQUIRED",
                event_id=f"{normalized_operation_id}:reconciliation-required",
                detail={"reason_code": normalized_reason},
                at_ms=now_value,
            )
            return self._read_operation_snapshot(conn=conn, operation_id=normalized_operation_id)

    def _assert_terminal_receipt_bindings(
        self,
        *,
        conn: sqlite3.Connection,
        run_id: str,
        receipt: PairedResearchRunReceiptV1,
        require_owner_randomization: bool = False,
    ) -> None:
        """Fail closed unless a terminal receipt exactly reopens its arm evidence.

        The 0107 trigger proves that two arm rows exist, but a terminal receipt
        must also bind the same registration, RunSpec, plan identity, model
        receipt and verifier receipt.  This owner-side check closes that
        semantic gap without assigning the shadow ledger any promotion power.
        """

        row = conn.execute(
            """
            SELECT
                run.run_id,
                run.spec_digest,
                run.registration_id,
                run.baseline_arm_id,
                run.selected_arm_id,
                run.spec_json,
                run.randomized_arm_order AS run_randomized_arm_order,
                run.production_influence_enabled AS run_production_influence_enabled,
                registration.binding_digest,
                registration.profile_digest,
                registration.budget_digest,
                registration.registration_json,
                registration.production_influence_enabled
                  AS registration_production_influence_enabled
            FROM planning_search_research_run_spec AS run
            JOIN planning_search_research_registration AS registration
              ON registration.registration_id = run.registration_id
            WHERE run.run_id = ?
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise ResearchShadowLedgerError("Research terminal receipt run is not preregistered")
        spec = _paired_run_spec_from_row(row)
        if require_owner_randomization and not spec.owner_randomized:
            raise ResearchShadowLedgerError(
                "Research terminal receipt requires owner-generated arm randomization"
            )
        registration = _canonical_object(
            row["registration_json"],
            label="Research registration",
        )
        run_spec = _canonical_object(row["spec_json"], label="Research RunSpec")
        registration_id = str(row["registration_id"])
        if (
            registration.get("registration_id") != registration_id
            or run_spec.get("registration_id") != registration_id
        ):
            raise ResearchShadowLedgerError("Research registration and RunSpec binding drift")
        profile = registration.get("profile")
        if (
            _digest(registration.get("binding")) != str(row["binding_digest"])
            or not isinstance(profile, dict)
            or str(profile.get("profile_digest") or "") != str(row["profile_digest"])
            or _digest(registration.get("budget")) != str(row["budget_digest"])
        ):
            raise ResearchShadowLedgerError("Research registration digest binding drift")
        if (
            receipt.corpus_binding_digest != str(row["binding_digest"])
            or receipt.profile_digest != str(row["profile_digest"])
            or receipt.budget_digest != str(row["budget_digest"])
        ):
            raise ResearchShadowLedgerError("Research terminal receipt does not bind its registration")
        if not bool(row["run_randomized_arm_order"]) or not receipt.randomized_arm_order:
            raise ResearchShadowLedgerError("Research terminal receipt requires randomized paired arms")
        if receipt.arm_order != spec.arm_order:
            raise ResearchShadowLedgerError(
                "Research terminal receipt does not bind the preregistered arm order"
            )

        arm_rows = {
            str(arm["arm_role"]): arm
            for arm in conn.execute(
                """
                SELECT arm_role, arm_id, evidence_digest, model_receipt_json, verifier_receipt_json
                FROM planning_search_research_arm_execution
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchall()
        }
        if set(arm_rows) != {"BASELINE", "SELECTED"}:
            raise ResearchShadowLedgerError("Research terminal receipt requires exactly two paired arms")
        self._assert_terminal_arm_binding(
            row=arm_rows["BASELINE"],
            plan=spec.baseline.to_dict(),
            expected_arm_id=spec.baseline.arm_id,
            receipt_model=receipt.baseline_model.to_dict(),
            receipt_verifier=receipt.baseline_verifier.to_dict(),
        )
        self._assert_terminal_arm_binding(
            row=arm_rows["SELECTED"],
            plan=spec.selected.to_dict(),
            expected_arm_id=spec.selected.arm_id,
            receipt_model=receipt.selected_model.to_dict(),
            receipt_verifier=receipt.selected_verifier.to_dict(),
        )

    @staticmethod
    def _assert_terminal_arm_binding(
        *,
        row: sqlite3.Row,
        plan: dict[str, Any],
        expected_arm_id: str,
        receipt_model: dict[str, Any],
        receipt_verifier: dict[str, Any],
    ) -> None:
        arm_id = str(plan.get("arm_id") or "")
        if not arm_id or arm_id != expected_arm_id or str(row["arm_id"]) != arm_id:
            raise ResearchShadowLedgerError("Research arm does not bind its preregistered plan")
        model = _canonical_object(row["model_receipt_json"], label="Research model receipt")
        verifier = _canonical_object(row["verifier_receipt_json"], label="Research verifier receipt")
        if (
            _digest({"plan": plan, "model": model, "verifier": verifier})
            != str(row["evidence_digest"])
        ):
            raise ResearchShadowLedgerError("Research arm evidence digest drift")
        if (
            _canonical(receipt_model) != _canonical(model)
            or _canonical(receipt_verifier) != _canonical(verifier)
        ):
            raise ResearchShadowLedgerError("Research terminal receipt does not match persisted arm evidence")
        if (
            str(model.get("arm_id") or "") != arm_id
            or str(verifier.get("execution_id") or "")
            != str(model.get("execution_id") or "")
        ):
            raise ResearchShadowLedgerError("Research persisted arm receipts are internally unbound")

    @staticmethod
    def _assert_operation_schema_ready(conn: sqlite3.Connection) -> None:
        names = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','trigger')"
            )
        }
        required = {
            "planning_search_research_arm_operation",
            "planning_search_research_arm_operation_event",
            "research_arm_operation_run_bound",
            "research_arm_operation_event_chain",
        }
        if not required.issubset(names):
            raise ResearchShadowLedgerError(
                "migration 0121 Research arm operation schema is not ready"
            )

    @staticmethod
    def _assert_operation_plan_binding(
        *,
        conn: sqlite3.Connection,
        run_id: str,
        role: str,
        plan: ResearchArmPlanV1,
    ) -> None:
        row = conn.execute(
            "SELECT spec_json FROM planning_search_research_run_spec WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise ResearchShadowLedgerError("Research arm operation run is not preregistered")
        spec = _canonical_object(row["spec_json"], label="Research RunSpec")
        key = "baseline" if role == "BASELINE" else "selected"
        expected_plan = _run_spec_plan(spec, key=key)
        if _canonical(expected_plan) != _canonical(plan.to_dict()):
            raise ResearchShadowLedgerError(
                "Research arm operation does not bind its preregistered plan"
            )

    def _append_operation_event(
        self,
        *,
        conn: sqlite3.Connection,
        operation_id: str,
        event_kind: str,
        event_id: str,
        detail: dict[str, Any],
        at_ms: int,
    ) -> None:
        allowed = {
            "RESERVED",
            "CALL_CLAIMED",
            "EXTERNAL_CALL_STARTED",
            "RETRY_AUTHORIZED_NO_AUDIT",
            "EVIDENCE_PERSISTED",
            "RECONCILIATION_REQUIRED",
        }
        if event_kind not in allowed:
            raise ResearchShadowLedgerError("Research arm operation event is unsupported")
        if not isinstance(detail, dict):
            raise ResearchShadowLedgerError("Research arm operation event detail is invalid")
        protected = {"schema", "operation_id", "event_kind"}
        if protected.intersection(detail):
            raise ResearchShadowLedgerError("Research arm operation event detail overrides identity")
        payload = {
            "schema": "natsume.research-shadow.arm-operation-event.v1",
            "operation_id": operation_id,
            "event_kind": event_kind,
            **detail,
        }
        encoded = _canonical(payload)
        digest = _digest(payload)
        existing = conn.execute(
            """
            SELECT event_digest, detail_json
            FROM planning_search_research_arm_operation_event
            WHERE event_id = ?
            """,
            (event_id,),
        ).fetchone()
        if existing is not None:
            if str(existing["event_digest"]) != digest or str(existing["detail_json"]) != encoded:
                raise ResearchShadowLedgerError("Research arm operation event replay drift")
            return
        conn.execute(
            """
            INSERT INTO planning_search_research_arm_operation_event
            (event_id, operation_id, event_kind, event_digest, detail_json, created_at_ms)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (event_id, operation_id, event_kind, digest, encoded, at_ms),
        )

    def _read_operation_snapshot(
        self,
        *,
        conn: sqlite3.Connection,
        operation_id: str,
    ) -> ResearchArmOperationSnapshot:
        row = conn.execute(
            """
            SELECT operation_id, operation_digest, run_id, arm_role, arm_id,
                   execution_id, operation_json
            FROM planning_search_research_arm_operation
            WHERE operation_id = ?
            """,
            (operation_id,),
        ).fetchone()
        if row is None:
            raise ResearchShadowLedgerError("Research arm operation is not reserved")
        operation = _canonical_object(
            row["operation_json"],
            label="Research arm operation",
        )
        if (
            _digest(operation) != str(row["operation_digest"])
            or operation.get("schema") != "natsume.research-shadow.arm-operation.v1"
            or "operation_id" in operation
            or operation.get("run_id") != str(row["run_id"])
            or operation.get("arm_role") != str(row["arm_role"])
            or operation.get("arm_id") != str(row["arm_id"])
            or operation.get("execution_id") != str(row["execution_id"])
            or operation.get("production_influence_enabled") is not False
            or not isinstance(operation.get("plan"), dict)
        ):
            raise ResearchShadowLedgerError("Research arm operation payload drift")
        raw_plan = operation["plan"]
        if (
            not isinstance(raw_plan.get("candidate_ids"), list)
            or not isinstance(raw_plan.get("source_ids"), list)
        ):
            raise ResearchShadowLedgerError("Research arm operation plan payload is invalid")
        try:
            plan = ResearchArmPlanV1(
                arm_id=str(raw_plan.get("arm_id") or ""),
                candidate_ids=tuple(raw_plan["candidate_ids"]),
                source_ids=tuple(raw_plan["source_ids"]),
                graph_digest=str(raw_plan.get("graph_digest") or ""),
                path_digest=str(raw_plan.get("path_digest") or ""),
                generated_by=str(raw_plan.get("generated_by") or ""),
            )
        except (TypeError, ValueError) as exc:
            raise ResearchShadowLedgerError("Research arm operation plan payload is invalid") from exc
        if (
            plan.arm_id != str(row["arm_id"])
            or _canonical(plan.to_dict()) != _canonical(raw_plan)
        ):
            raise ResearchShadowLedgerError("Research arm operation plan payload drift")
        self._assert_operation_plan_binding(
            conn=conn,
            run_id=str(row["run_id"]),
            role=str(row["arm_role"]),
            plan=plan,
        )
        events = conn.execute(
            """
            SELECT event_sequence, event_id, event_kind, event_digest, detail_json, created_at_ms
            FROM planning_search_research_arm_operation_event
            WHERE operation_id = ?
            ORDER BY event_sequence ASC
            """,
            (operation_id,),
        ).fetchall()
        if not events:
            raise ResearchShadowLedgerError("Research arm operation lacks reservation event")

        state: ResearchArmOperationState | None = None
        claim_id: str | None = None
        claim_owner_id: str | None = None
        claim_fence: int | None = None
        claim_expires_at_ms: int | None = None
        reconciliation_reason: str | None = None
        last_sequence = 0
        for index, event in enumerate(events):
            sequence = int(event["event_sequence"])
            if sequence <= last_sequence:
                raise ResearchShadowLedgerError("Research arm operation event sequence drift")
            last_sequence = sequence
            kind = str(event["event_kind"])
            detail = _canonical_object(
                event["detail_json"],
                label="Research arm operation event",
            )
            if (
                _digest(detail) != str(event["event_digest"])
                or detail.get("schema") != "natsume.research-shadow.arm-operation-event.v1"
                or detail.get("operation_id") != operation_id
                or detail.get("event_kind") != kind
            ):
                raise ResearchShadowLedgerError("Research arm operation event payload drift")
            if kind == "RESERVED":
                if index != 0 or state is not None:
                    raise ResearchShadowLedgerError("Research arm operation reservation order drift")
                state = ResearchArmOperationState.RESERVED
                continue
            if kind == "CALL_CLAIMED":
                if state not in {
                    ResearchArmOperationState.RESERVED,
                    ResearchArmOperationState.CALL_CLAIMED,
                    ResearchArmOperationState.RETRY_AUTHORIZED_NO_AUDIT,
                }:
                    raise ResearchShadowLedgerError("Research arm operation claim order drift")
                next_claim_id = _operation_text(
                    detail.get("claim_id"),
                    field="claim_id",
                    limit=255,
                )
                next_owner_id = _operation_text(
                    detail.get("owner_id"),
                    field="owner_id",
                    limit=255,
                )
                next_fence = _bounded_positive_int(
                    detail.get("fencing_token"),
                    field="fencing_token",
                    maximum=2_147_483_647,
                )
                claimed_at_ms = _non_negative_operation_int(
                    detail.get("claimed_at_ms"),
                    field="claimed_at_ms",
                )
                next_expires_at_ms = _non_negative_operation_int(
                    detail.get("expires_at_ms"),
                    field="expires_at_ms",
                )
                if (
                    claimed_at_ms != int(event["created_at_ms"])
                    or next_expires_at_ms <= claimed_at_ms
                    or (claim_fence is not None and next_fence <= claim_fence)
                ):
                    raise ResearchShadowLedgerError("Research arm operation claim payload drift")
                claim_id = next_claim_id
                claim_owner_id = next_owner_id
                claim_fence = next_fence
                claim_expires_at_ms = next_expires_at_ms
                state = ResearchArmOperationState.CALL_CLAIMED
                continue
            if kind == "EXTERNAL_CALL_STARTED":
                if state is not ResearchArmOperationState.CALL_CLAIMED:
                    raise ResearchShadowLedgerError("Research arm external call order drift")
                if (
                    detail.get("claim_id") != claim_id
                    or detail.get("owner_id") != claim_owner_id
                    or detail.get("fencing_token") != claim_fence
                    or detail.get("expires_at_ms") != claim_expires_at_ms
                ):
                    raise ResearchShadowLedgerError("Research arm external call claim drift")
                state = ResearchArmOperationState.EXTERNAL_CALL_STARTED
                continue
            if kind == "RETRY_AUTHORIZED_NO_AUDIT":
                if state is not ResearchArmOperationState.EXTERNAL_CALL_STARTED:
                    raise ResearchShadowLedgerError("Research arm retry authorization order drift")
                if (
                    detail.get("prior_claim_id") != claim_id
                    or detail.get("prior_fencing_token") != claim_fence
                ):
                    raise ResearchShadowLedgerError("Research arm retry authorization claim drift")
                _operation_text(detail.get("reason_code"), field="reason_code", limit=128)
                state = ResearchArmOperationState.RETRY_AUTHORIZED_NO_AUDIT
                continue
            if kind == "EVIDENCE_PERSISTED":
                if state is not ResearchArmOperationState.EXTERNAL_CALL_STARTED:
                    raise ResearchShadowLedgerError("Research arm evidence event order drift")
                evidence_digest = _operation_digest(detail.get("evidence_digest"), field="evidence_digest")
                persisted = conn.execute(
                    """
                    SELECT evidence_digest
                    FROM planning_search_research_arm_execution
                    WHERE run_id = ? AND arm_role = ? AND arm_id = ?
                    """,
                    (str(row["run_id"]), str(row["arm_role"]), str(row["arm_id"])),
                ).fetchone()
                if persisted is None or str(persisted["evidence_digest"]) != evidence_digest:
                    raise ResearchShadowLedgerError("Research arm evidence event does not bind evidence row")
                state = ResearchArmOperationState.EVIDENCE_PERSISTED
                continue
            if kind == "RECONCILIATION_REQUIRED":
                if state is not ResearchArmOperationState.EXTERNAL_CALL_STARTED:
                    raise ResearchShadowLedgerError("Research arm reconciliation order drift")
                reconciliation_reason = _operation_text(
                    detail.get("reason_code"),
                    field="reason_code",
                    limit=128,
                )
                state = ResearchArmOperationState.RECONCILIATION_REQUIRED
                continue
            raise ResearchShadowLedgerError("Research arm operation contains an unknown event")
        if state is None:
            raise ResearchShadowLedgerError("Research arm operation state is absent")
        return ResearchArmOperationSnapshot(
            operation_id=str(row["operation_id"]),
            operation_digest=str(row["operation_digest"]),
            run_id=str(row["run_id"]),
            arm_role=str(row["arm_role"]),
            arm_id=str(row["arm_id"]),
            execution_id=str(row["execution_id"]),
            state=state,
            event_sequence=last_sequence,
            claim_id=claim_id,
            claim_owner_id=claim_owner_id,
            claim_fencing_token=claim_fence,
            claim_expires_at_ms=claim_expires_at_ms,
            reconciliation_reason=reconciliation_reason,
        )

    def _operation_time(self, value: int | None) -> int:
        return _non_negative_operation_int(
            self._clock() if value is None else value,
            field="operation_time_ms",
        )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _owner_randomized_run_identity(
    *,
    registration_id: str,
    baseline: ResearchArmPlanV1,
    selected: ResearchArmPlanV1,
    cohort_run_key_digest: str,
) -> dict[str, Any]:
    return {
        "schema": _OWNER_RANDOMIZED_RUN_SPEC_SCHEMA,
        "registration_id": registration_id,
        "baseline": baseline.to_dict(),
        "selected": selected.to_dict(),
        "cohort_run_key_digest": _operation_digest(
            cohort_run_key_digest,
            field="cohort_run_key_digest",
        ),
        "production_influence_enabled": False,
    }


def _owner_randomized_arm_order(randomization_draw: Any) -> tuple[str, str]:
    if (
        isinstance(randomization_draw, bool)
        or not isinstance(randomization_draw, int)
        or randomization_draw not in {0, 1}
    ):
        raise ResearchShadowLedgerError("Research randomization draw must be 0 or 1")
    return (
        ("BASELINE", "SELECTED")
        if randomization_draw == 0
        else ("SELECTED", "BASELINE")
    )


def _owner_randomized_run_payload(
    *,
    identity: dict[str, Any],
    arm_order: tuple[str, str],
    randomization_draw: int,
) -> dict[str, Any]:
    expected_arm_order = _owner_randomized_arm_order(randomization_draw)
    if _paired_arm_order(arm_order) != expected_arm_order:
        raise ResearchShadowLedgerError("Research randomization draw and arm order drift")
    return {
        **identity,
        "randomization_algorithm": _OWNER_RANDOMIZATION_ALGORITHM,
        "randomization_draw": randomization_draw,
        "arm_order": list(expected_arm_order),
        "randomized_arm_order": True,
        "owner_randomized": True,
    }


def _assert_owner_randomized_run_replay(
    *,
    raw: dict[str, Any],
    identity: dict[str, Any],
) -> dict[str, Any]:
    randomization_draw = raw.get("randomization_draw")
    arm_order = _paired_arm_order(raw.get("arm_order"))
    expected = _owner_randomized_run_payload(
        identity=identity,
        arm_order=arm_order,
        randomization_draw=randomization_draw,
    )
    if _canonical(raw) != _canonical(expected):
        raise ResearchShadowLedgerError("Research RunSpec replay drift")
    return expected


def _paired_run_spec_from_row(row: sqlite3.Row) -> ResearchPairedRunSpecSnapshot:
    registration_id = _operation_text(
        row["registration_id"],
        field="registration_id",
        limit=255,
    )
    run_id = _operation_text(row["run_id"], field="run_id", limit=255)
    registration = _canonical_object(
        row["registration_json"],
        label="Research registration",
    )
    run_spec = _canonical_object(row["spec_json"], label="Research RunSpec")
    binding = _research_binding_from_dict(registration.get("binding"))
    profile = _research_profile_from_dict(registration.get("profile"))
    budget = _research_budget_from_dict(registration.get("budget"))
    expected_registration = {
        "schema": "natsume.research-benchmark.shadow.v1",
        "registration_id": registration_id,
        "binding": binding.to_dict(),
        "profile": profile.to_dict(),
        "budget": budget.to_dict(),
        "production_influence_enabled": False,
    }
    if (
        _canonical(registration) != _canonical(expected_registration)
        or str(row["binding_digest"]) != binding.binding_digest
        or str(row["profile_digest"]) != profile.profile_digest
        or str(row["budget_digest"]) != budget.budget_digest
        or int(row["registration_production_influence_enabled"]) != 0
    ):
        raise ResearchShadowLedgerError("Research registration payload drift")
    baseline = _research_arm_plan_from_dict(run_spec.get("baseline"))
    selected = _research_arm_plan_from_dict(run_spec.get("selected"))
    arm_order = _paired_arm_order(run_spec.get("arm_order"))
    owner_randomized = False
    randomization_algorithm: str | None = None
    randomization_draw: int | None = None
    cohort_run_key_digest: str | None = None
    if "schema" not in run_spec:
        expected_run_spec = {
            "registration_id": registration_id,
            "baseline": baseline.to_dict(),
            "selected": selected.to_dict(),
            "arm_order": list(arm_order),
            "randomized_arm_order": True,
            "production_influence_enabled": False,
        }
        expected_run_id = f"researchrun_{_digest(expected_run_spec)[:32]}"
    else:
        cohort_run_key_digest = _operation_digest(
            run_spec.get("cohort_run_key_digest"),
            field="cohort_run_key_digest",
        )
        randomization_algorithm = _operation_text(
            run_spec.get("randomization_algorithm"),
            field="randomization_algorithm",
            limit=128,
        )
        if randomization_algorithm != _OWNER_RANDOMIZATION_ALGORITHM:
            raise ResearchShadowLedgerError("Research randomization algorithm drift")
        randomization_draw = run_spec.get("randomization_draw")
        identity = _owner_randomized_run_identity(
            registration_id=registration_id,
            baseline=baseline,
            selected=selected,
            cohort_run_key_digest=cohort_run_key_digest,
        )
        expected_run_spec = _owner_randomized_run_payload(
            identity=identity,
            arm_order=arm_order,
            randomization_draw=randomization_draw,
        )
        expected_run_id = f"researchrun_{_digest(identity)[:32]}"
        owner_randomized = True
    if (
        _canonical(run_spec) != _canonical(expected_run_spec)
        or str(row["spec_digest"]) != _digest(expected_run_spec)
        or run_id != expected_run_id
        or str(row["baseline_arm_id"]) != baseline.arm_id
        or str(row["selected_arm_id"]) != selected.arm_id
        or int(row["run_randomized_arm_order"]) != 1
        or int(row["run_production_influence_enabled"]) != 0
    ):
        raise ResearchShadowLedgerError("Research RunSpec payload drift")
    return ResearchPairedRunSpecSnapshot(
        run_id=run_id,
        registration_id=registration_id,
        binding=binding,
        profile=profile,
        budget=budget,
        baseline=baseline,
        selected=selected,
        arm_order=arm_order,
        owner_randomized=owner_randomized,
        randomization_algorithm=randomization_algorithm,
        randomization_draw=randomization_draw,
        cohort_run_key_digest=cohort_run_key_digest,
    )


__all__ = [
    "RESEARCH_SHADOW_ARM_OPERATION_MIGRATION_ASSET_KEY",
    "RESEARCH_SHADOW_MIGRATION_ASSET_KEY",
    "ResearchArmExecutionSnapshot",
    "ResearchArmOperationSnapshot",
    "ResearchArmOperationState",
    "ResearchPairedRunSpecSnapshot",
    "ResearchShadowLedgerError",
    "ResearchTerminalReceiptSnapshot",
    "SQLiteResearchShadowLedger",
]
