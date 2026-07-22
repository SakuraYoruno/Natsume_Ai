"""Append-only SQLite owner for D20b Research shadow evidence."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable, Iterator

from host_brain.core.ids import now_ms
from host_brain.contracts.planning_search.research_benchmark_contracts import (
    PairedResearchRunReceiptV1,
    ResearchArmPlanV1,
    ResearchCorpusBindingV1,
    ResearchExecutionBudgetV1,
    ResearchExecutionProfileV1,
    ResearchModelExecutionReceiptV1,
    ResearchVerifierReceiptV1,
)

RESEARCH_SHADOW_MIGRATION_ASSET_KEY = "0107#research_shadow_ledger"


class ResearchShadowLedgerError(RuntimeError):
    """Durable Research evidence failed closed."""


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

    def preregister_run(
        self,
        *,
        registration_id: str,
        baseline: ResearchArmPlanV1,
        selected: ResearchArmPlanV1,
        randomized_arm_order: bool,
    ) -> str:
        if not randomized_arm_order:
            raise ResearchShadowLedgerError("Research paired run requires randomized arm order")
        if baseline.arm_id == selected.arm_id:
            raise ResearchShadowLedgerError("Research paired arms require different ids")
        run_payload = {
            "registration_id": registration_id,
            "baseline": baseline.to_dict(),
            "selected": selected.to_dict(),
            "randomized_arm_order": True,
            "production_influence_enabled": False,
        }
        run_id = f"researchrun_{_digest(run_payload)[:32]}"
        spec_digest = _digest(run_payload)
        with self._transaction() as conn:
            existing = conn.execute(
                "SELECT spec_json FROM planning_search_research_run_spec WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            encoded = _canonical(run_payload)
            if existing is not None:
                if str(existing[0]) != encoded:
                    raise ResearchShadowLedgerError("Research RunSpec replay drift")
                return run_id
            conn.execute(
                """
                INSERT INTO planning_search_research_run_spec
                (run_id, spec_digest, registration_id, baseline_arm_id, selected_arm_id,
                 spec_json, randomized_arm_order, production_influence_enabled, created_at_ms)
                VALUES (?, ?, ?, ?, ?, ?, 1, 0, ?)
                """,
                (run_id, spec_digest, registration_id, baseline.arm_id, selected.arm_id, encoded, self._clock()),
            )
        return run_id

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
            self._assert_terminal_receipt_bindings(conn=conn, run_id=run_id, receipt=receipt)
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

    def _assert_terminal_receipt_bindings(
        self,
        *,
        conn: sqlite3.Connection,
        run_id: str,
        receipt: PairedResearchRunReceiptV1,
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
                run.registration_id,
                run.baseline_arm_id,
                run.selected_arm_id,
                run.spec_json,
                run.randomized_arm_order AS run_randomized_arm_order,
                registration.binding_digest,
                registration.profile_digest,
                registration.budget_digest,
                registration.registration_json
            FROM planning_search_research_run_spec AS run
            JOIN planning_search_research_registration AS registration
              ON registration.registration_id = run.registration_id
            WHERE run.run_id = ?
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise ResearchShadowLedgerError("Research terminal receipt run is not preregistered")
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
            plan=_run_spec_plan(run_spec, key="baseline"),
            expected_arm_id=str(row["baseline_arm_id"]),
            receipt_model=receipt.baseline_model.to_dict(),
            receipt_verifier=receipt.baseline_verifier.to_dict(),
        )
        self._assert_terminal_arm_binding(
            row=arm_rows["SELECTED"],
            plan=_run_spec_plan(run_spec, key="selected"),
            expected_arm_id=str(row["selected_arm_id"]),
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


def _canonical_object(raw: Any, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ResearchShadowLedgerError(f"{label} JSON is unreadable") from exc
    if not isinstance(value, dict) or _canonical(value) != str(raw):
        raise ResearchShadowLedgerError(f"{label} JSON is not canonical")
    return value


def _run_spec_plan(run_spec: dict[str, Any], *, key: str) -> dict[str, Any]:
    value = run_spec.get(key)
    if not isinstance(value, dict):
        raise ResearchShadowLedgerError("Research RunSpec is missing a paired arm plan")
    return value


__all__ = ["RESEARCH_SHADOW_MIGRATION_ASSET_KEY", "ResearchShadowLedgerError", "SQLiteResearchShadowLedger"]
