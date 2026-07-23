"""Durable paired Research execution over preregistered D20c evidence.

The runner owns only experiment orchestration.  It never chooses a provider,
does not persist a prompt or model response, and cannot grant production
influence.  A caller must preregister the pair first; this module reopens that
immutable specification, runs its encoded order through the canonical external
model adapter, and compiles only durable usage/citation receipts into 0107.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Protocol

from host_brain.contracts.planning_search.research_benchmark_contracts import (
    PairedResearchRunReceiptV1,
    ResearchCitationSpanV1,
    ResearchModelExecutionReceiptV1,
    ResearchVerifierReceiptV1,
)
from host_brain.persistence.research_shadow_ledger_repository import (
    ResearchArmExecutionSnapshot,
    ResearchArmOperationSnapshot,
    ResearchArmOperationState,
    ResearchPairedRunSpecSnapshot,
    ResearchShadowLedgerError,
    SQLiteResearchShadowLedger,
)
from host_brain.planning_search.attested_external_research_adapter import (
    AttestedExternalResearchAdapter,
    AttestedExternalResearchRun,
)


class ResearchPairedExecutionError(RuntimeError):
    """A preregistered Research pair cannot safely advance."""


class ResearchCitationExtractor(Protocol):
    """Parse transient model output into independently verifiable spans."""

    @property
    def output_schema_digest(self) -> str: ...

    def __call__(self, output_text: str) -> Sequence[ResearchCitationSpanV1]: ...


class ResearchCitationVerifierPort(Protocol):
    """Narrow verifier dependency; factories must create a fresh verifier."""

    def verify(
        self,
        *,
        execution_id: str,
        citations: Sequence[ResearchCitationSpanV1],
    ) -> ResearchVerifierReceiptV1: ...


@dataclass(frozen=True, slots=True)
class ResearchArmRuntimeInputV1:
    """Private, non-persisted input for one preregistered Research arm.

    ``messages`` may contain user or corpus-derived text.  It intentionally
    has no serialization method and is never handed to the Research ledger.
    The only durable prompt representation is the canonical model attempt's
    rendered prompt digest, which must match the preregistered profile.
    """

    prompt_id: str
    messages: tuple[Mapping[str, Any], ...]
    deadline_ms: int
    cognitive_tier: str = "RESEARCH"

    def __post_init__(self) -> None:
        object.__setattr__(self, "prompt_id", _text(self.prompt_id, field="prompt_id", limit=240))
        if isinstance(self.messages, (str, bytes, bytearray)):
            raise ResearchPairedExecutionError("messages must be an explicit sequence")
        try:
            messages = tuple(dict(item) for item in self.messages)
        except (TypeError, ValueError) as exc:
            raise ResearchPairedExecutionError(
                "messages must contain mapping values"
            ) from exc
        if not messages:
            raise ResearchPairedExecutionError("messages must not be empty")
        if isinstance(self.deadline_ms, bool) or not isinstance(self.deadline_ms, int) or self.deadline_ms <= 0:
            raise ResearchPairedExecutionError("deadline_ms must be a positive integer")
        object.__setattr__(self, "messages", messages)
        object.__setattr__(
            self,
            "cognitive_tier",
            _text(self.cognitive_tier, field="cognitive_tier", limit=80).upper(),
        )


@dataclass(frozen=True, slots=True)
class ResearchPairedExecutionResult:
    """Public-safe outcome of one completed or exactly replayed pair."""

    run_id: str
    receipt_ref: str
    receipt: PairedResearchRunReceiptV1
    replayed_terminal_receipt: bool


class ResearchPairedExecutionRunner:
    """Execute exactly the durable paired RunSpec, never caller-provided plans.

    The concrete :class:`AttestedExternalResearchAdapter` is deliberately a
    constructor requirement.  This prevents a broad execution callback from
    manufacturing D20c "external" evidence without crossing canonical model
    attempt and audit authority.
    """

    def __init__(
        self,
        *,
        ledger: SQLiteResearchShadowLedger,
        external_adapter: AttestedExternalResearchAdapter,
        citation_extractor: ResearchCitationExtractor,
        verifier_factory: Callable[[], ResearchCitationVerifierPort],
        lease_duration_ms: int = 60_000,
    ) -> None:
        if not isinstance(ledger, SQLiteResearchShadowLedger):
            raise TypeError("ledger must be SQLiteResearchShadowLedger")
        if not isinstance(external_adapter, AttestedExternalResearchAdapter):
            raise TypeError("external_adapter must be AttestedExternalResearchAdapter")
        if not callable(citation_extractor):
            raise TypeError("citation_extractor must be callable")
        if not _is_digest(getattr(citation_extractor, "output_schema_digest", None)):
            raise TypeError("citation_extractor must expose a SHA-256 output_schema_digest")
        if not callable(verifier_factory):
            raise TypeError("verifier_factory must be callable")
        if (
            isinstance(lease_duration_ms, bool)
            or not isinstance(lease_duration_ms, int)
            or not 0 < lease_duration_ms <= 86_400_000
        ):
            raise ValueError("lease_duration_ms must be a positive bounded integer")
        self._ledger = ledger
        self._external_adapter = external_adapter
        self._citation_extractor = citation_extractor
        self._verifier_factory = verifier_factory
        self._lease_duration_ms = lease_duration_ms

    @staticmethod
    def execution_id_for(*, run_id: str, role: str, arm_id: str) -> str:
        """Return the stable external identity for one stored pair arm."""

        return _execution_id_for(run_id=run_id, role=role, arm_id=arm_id)

    async def execute_preregistered(
        self,
        *,
        run_id: str,
        arm_inputs: Mapping[str, ResearchArmRuntimeInputV1],
        owner_id: str,
    ) -> ResearchPairedExecutionResult:
        """Complete a persisted pair or return its exact terminal replay.

        Callers supply only transient prompt/messages and a worker owner.  The
        pair's binding, plans, random order, budget and external execution IDs
        all come from the persisted RunSpec and operation journal.
        """

        normalized_run_id = _text(run_id, field="run_id", limit=255)
        normalized_owner_id = _text(owner_id, field="owner_id", limit=255)
        spec = self._ledger.get_paired_run_spec(run_id=normalized_run_id)
        terminal = self._ledger.get_terminal_receipt(run_id=normalized_run_id)
        if terminal is not None:
            return ResearchPairedExecutionResult(
                run_id=terminal.run_id,
                receipt_ref=terminal.receipt_ref,
                receipt=terminal.receipt,
                replayed_terminal_receipt=True,
            )

        if not spec.owner_randomized:
            raise ResearchPairedExecutionError(
                "Research paired execution requires owner-generated arm randomization"
            )
        if self._citation_extractor.output_schema_digest != spec.profile.output_schema_digest:
            raise ResearchPairedExecutionError(
                "Research citation extractor schema does not bind the preregistered profile"
            )
        inputs = _arm_inputs(arm_inputs)
        verifier_instances: list[ResearchCitationVerifierPort] = []
        for role in spec.arm_order:
            await self._ensure_arm_evidence(
                spec=spec,
                role=role,
                runtime_input=inputs[role],
                owner_id=normalized_owner_id,
                verifier_instances=verifier_instances,
            )

        baseline = self._ledger.get_arm_execution(run_id=spec.run_id, role="BASELINE")
        selected = self._ledger.get_arm_execution(run_id=spec.run_id, role="SELECTED")
        if baseline is None or selected is None:
            raise ResearchPairedExecutionError("Research paired arms are incomplete after execution")
        receipt = _paired_receipt(spec=spec, baseline=baseline, selected=selected)
        try:
            receipt_ref = self._ledger.complete(run_id=spec.run_id, receipt=receipt)
        except ResearchShadowLedgerError as exc:
            raise ResearchPairedExecutionError(
                "Research paired terminal receipt could not be persisted"
            ) from exc
        return ResearchPairedExecutionResult(
            run_id=spec.run_id,
            receipt_ref=receipt_ref,
            receipt=receipt,
            replayed_terminal_receipt=False,
        )

    async def _ensure_arm_evidence(
        self,
        *,
        spec: ResearchPairedRunSpecSnapshot,
        role: str,
        runtime_input: ResearchArmRuntimeInputV1,
        owner_id: str,
        verifier_instances: list[ResearchCitationVerifierPort],
    ) -> ResearchArmExecutionSnapshot:
        plan = spec.baseline if role == "BASELINE" else spec.selected
        execution_id = self.execution_id_for(
            run_id=spec.run_id,
            role=role,
            arm_id=plan.arm_id,
        )
        try:
            operation = self._ledger.reserve_arm_operation(
                run_id=spec.run_id,
                role=role,
                plan=plan,
                execution_id=execution_id,
            )
            persisted = self._ledger.get_arm_execution(run_id=spec.run_id, role=role)
        except ResearchShadowLedgerError as exc:
            raise ResearchPairedExecutionError("Research arm reservation could not be reopened") from exc

        if operation.state is ResearchArmOperationState.EVIDENCE_PERSISTED:
            if persisted is None or persisted.model.execution_id != operation.execution_id:
                raise ResearchPairedExecutionError("Research evidence operation is internally unbound")
            _assert_persisted_arm_matches_spec(
                spec=spec,
                arm=persisted,
                expected_execution_id=operation.execution_id,
            )
            return persisted
        if operation.state is ResearchArmOperationState.RECONCILIATION_REQUIRED:
            raise ResearchPairedExecutionError(
                "Research arm requires external-effect reconciliation: "
                f"{operation.reconciliation_reason or 'UNKNOWN'}"
            )
        if persisted is not None:
            if (
                operation.state is not ResearchArmOperationState.EXTERNAL_CALL_STARTED
                or persisted.model.execution_id != operation.execution_id
            ):
                raise ResearchPairedExecutionError(
                    "Research evidence exists without a matching durable external-call state"
                )
            try:
                self._ledger.mark_arm_evidence_persisted(operation_id=operation.operation_id)
            except ResearchShadowLedgerError as exc:
                raise ResearchPairedExecutionError(
                    "Research persisted evidence could not close its operation journal"
                ) from exc
            _assert_persisted_arm_matches_spec(
                spec=spec,
                arm=persisted,
                expected_execution_id=operation.execution_id,
            )
            return persisted

        if operation.state is ResearchArmOperationState.EXTERNAL_CALL_STARTED:
            operation = self._recover_started_operation(operation=operation)

        if operation.state not in {
            ResearchArmOperationState.RESERVED,
            ResearchArmOperationState.CALL_CLAIMED,
            ResearchArmOperationState.RETRY_AUTHORIZED_NO_AUDIT,
        }:
            raise ResearchPairedExecutionError("Research arm operation is not executable")

        try:
            claim = self._ledger.claim_arm_operation(
                operation_id=operation.operation_id,
                owner_id=owner_id,
                lease_duration_ms=self._lease_duration_ms,
            )
            started = self._ledger.begin_external_call(
                operation_id=operation.operation_id,
                claim_id=claim.claim_id or "",
            )
        except ResearchShadowLedgerError as exc:
            raise ResearchPairedExecutionError("Research arm call claim could not be acquired") from exc

        try:
            external_run = await self._external_adapter.execute(
                execution_id=started.execution_id,
                arm_id=plan.arm_id,
                prompt_id=runtime_input.prompt_id,
                messages=runtime_input.messages,
                deadline_ms=runtime_input.deadline_ms,
                cognitive_tier=runtime_input.cognitive_tier,
            )
            _assert_attested_arm_within_budget(
                external_run=external_run,
                operation=started,
                spec=spec,
                plan_arm_id=plan.arm_id,
            )
            citations = _citations_from_output(
                citation_extractor=self._citation_extractor,
                output_text=external_run.output_text,
            )
            verifier = self._fresh_verifier(verifier_instances)
            verifier_receipt = verifier.verify(
                execution_id=started.execution_id,
                citations=citations,
            )
            if (
                not isinstance(verifier_receipt, ResearchVerifierReceiptV1)
                or verifier_receipt.execution_id != started.execution_id
            ):
                raise ResearchPairedExecutionError(
                    "Research citation verifier receipt does not bind the arm execution"
                )
            self._ledger.append_arm_execution(
                run_id=spec.run_id,
                role=role,
                plan=plan,
                model=external_run.model_receipt,
                verifier=verifier_receipt,
            )
            self._ledger.mark_arm_evidence_persisted(operation_id=started.operation_id)
            persisted = self._ledger.get_arm_execution(run_id=spec.run_id, role=role)
            if persisted is None:
                raise ResearchPairedExecutionError("Research arm evidence vanished after persistence")
            _assert_persisted_arm_matches_spec(
                spec=spec,
                arm=persisted,
                expected_execution_id=started.execution_id,
            )
            return persisted
        except Exception as exc:
            recovered = self._recover_after_started_failure(
                operation=started,
                run_id=spec.run_id,
                role=role,
                spec=spec,
            )
            if recovered is not None:
                return recovered
            if isinstance(exc, ResearchPairedExecutionError):
                raise
            raise ResearchPairedExecutionError(
                "Research external arm did not produce durable evidence"
            ) from exc

    def _recover_started_operation(
        self,
        *,
        operation: ResearchArmOperationSnapshot,
    ) -> ResearchArmOperationSnapshot:
        try:
            records = self._external_adapter.canonical_audit_records(
                execution_id=operation.execution_id
            )
        except Exception as exc:
            try:
                self._ledger.mark_reconciliation_required(
                    operation_id=operation.operation_id,
                    reason_code="CANONICAL_AUDIT_LOOKUP_FAILED",
                )
            except ResearchShadowLedgerError:
                pass
            raise ResearchPairedExecutionError(
                "Research arm audit cannot be inspected for recovery"
            ) from exc
        try:
            if records:
                self._ledger.mark_reconciliation_required(
                    operation_id=operation.operation_id,
                    reason_code="CANONICAL_AUDIT_PRESENT_WITHOUT_ARM_EVIDENCE",
                )
                raise ResearchPairedExecutionError(
                    "Research arm has canonical audit without durable evidence; reconciliation required"
                )
            return self._ledger.authorize_retry_without_audit(
                operation_id=operation.operation_id,
            )
        except ResearchShadowLedgerError as exc:
            raise ResearchPairedExecutionError(
                "Research arm recovery transition could not be persisted"
            ) from exc

    def _recover_after_started_failure(
        self,
        *,
        operation: ResearchArmOperationSnapshot,
        run_id: str,
        role: str,
        spec: ResearchPairedRunSpecSnapshot,
    ) -> ResearchArmExecutionSnapshot | None:
        """Record an unambiguous retry or freeze an unknown external effect."""

        try:
            persisted = self._ledger.get_arm_execution(run_id=run_id, role=role)
            if persisted is not None and persisted.model.execution_id == operation.execution_id:
                self._ledger.mark_arm_evidence_persisted(operation_id=operation.operation_id)
                _assert_persisted_arm_matches_spec(
                    spec=spec,
                    arm=persisted,
                    expected_execution_id=operation.execution_id,
                )
                return persisted
            records = self._external_adapter.canonical_audit_records(
                execution_id=operation.execution_id
            )
            if records:
                self._ledger.mark_reconciliation_required(
                    operation_id=operation.operation_id,
                    reason_code="CANONICAL_AUDIT_PRESENT_WITHOUT_ARM_EVIDENCE",
                )
                return None
            self._ledger.authorize_retry_without_audit(operation_id=operation.operation_id)
            return None
        except Exception:
            try:
                self._ledger.mark_reconciliation_required(
                    operation_id=operation.operation_id,
                    reason_code="CANONICAL_AUDIT_LOOKUP_FAILED",
                )
            except ResearchShadowLedgerError:
                pass
            return None

    def _fresh_verifier(
        self,
        verifier_instances: list[ResearchCitationVerifierPort],
    ) -> ResearchCitationVerifierPort:
        try:
            verifier = self._verifier_factory()
        except Exception as exc:
            raise ResearchPairedExecutionError("Research verifier factory failed") from exc
        if not callable(getattr(verifier, "verify", None)):
            raise ResearchPairedExecutionError("Research verifier factory returned an invalid verifier")
        if any(verifier is prior for prior in verifier_instances):
            raise ResearchPairedExecutionError(
                "Research verifier factory must create a fresh verifier for each arm"
            )
        verifier_instances.append(verifier)
        return verifier


def _paired_receipt(
    *,
    spec: ResearchPairedRunSpecSnapshot,
    baseline: ResearchArmExecutionSnapshot,
    selected: ResearchArmExecutionSnapshot,
) -> PairedResearchRunReceiptV1:
    _assert_persisted_arm_matches_spec(
        spec=spec,
        arm=baseline,
        expected_execution_id=baseline.model.execution_id,
    )
    _assert_persisted_arm_matches_spec(
        spec=spec,
        arm=selected,
        expected_execution_id=selected.model.execution_id,
    )
    _assert_pair_budget(spec=spec, baseline=baseline, selected=selected)
    return PairedResearchRunReceiptV1(
        corpus_binding_digest=spec.binding.binding_digest,
        profile_digest=spec.profile.profile_digest,
        budget_digest=spec.budget.budget_digest,
        baseline_execution_id=baseline.model.execution_id,
        selected_execution_id=selected.model.execution_id,
        baseline_model=baseline.model,
        selected_model=selected.model,
        baseline_verifier=baseline.verifier,
        selected_verifier=selected.verifier,
        arm_order=spec.arm_order,
        randomized_arm_order=True,
    )


def _assert_attested_arm_within_budget(
    *,
    external_run: AttestedExternalResearchRun,
    operation: ResearchArmOperationSnapshot,
    spec: ResearchPairedRunSpecSnapshot,
    plan_arm_id: str,
) -> None:
    if not isinstance(external_run, AttestedExternalResearchRun):
        raise ResearchPairedExecutionError("Research adapter returned an invalid external run")
    model = external_run.model_receipt
    attestation = external_run.attestation
    if (
        external_run.execution_id != operation.execution_id
        or external_run.arm_id != plan_arm_id
        or model.execution_id != operation.execution_id
        or model.arm_id != plan_arm_id
        or model.attestation != attestation
        or not isinstance(external_run.output_text, str)
    ):
        raise ResearchPairedExecutionError(
            "Research external receipt does not bind the preregistered arm profile"
        )
    _assert_model_matches_spec(
        spec=spec,
        model=model,
        expected_arm_id=plan_arm_id,
        expected_execution_id=operation.execution_id,
    )


def _assert_persisted_arm_matches_spec(
    *,
    spec: ResearchPairedRunSpecSnapshot,
    arm: ResearchArmExecutionSnapshot,
    expected_execution_id: str,
) -> None:
    _assert_model_matches_spec(
        spec=spec,
        model=arm.model,
        expected_arm_id=arm.plan.arm_id,
        expected_execution_id=expected_execution_id,
    )


def _assert_model_matches_spec(
    *,
    spec: ResearchPairedRunSpecSnapshot,
    model: ResearchModelExecutionReceiptV1,
    expected_arm_id: str,
    expected_execution_id: str,
) -> None:
    attestation = model.attestation
    if (
        model.execution_id != expected_execution_id
        or model.arm_id != expected_arm_id
        or model.evidence_class != "ATTESTED_EXTERNAL"
        or model.prompt_digest != spec.profile.prompt_digest
        or model.total_tokens is None
        or model.tool_call_count is None
        or attestation is None
    ):
        raise ResearchPairedExecutionError(
            "Research arm evidence does not bind the preregistered execution profile"
        )
    if (
        model.total_tokens > spec.budget.arm_max_tokens
        or model.tool_call_count > spec.budget.arm_max_tool_calls
        or attestation.total_latency_ms > spec.budget.arm_max_wall_time_ms
    ):
        raise ResearchPairedExecutionError("Research arm evidence exceeded its preregistered budget")


def _assert_pair_budget(
    *,
    spec: ResearchPairedRunSpecSnapshot,
    baseline: ResearchArmExecutionSnapshot,
    selected: ResearchArmExecutionSnapshot,
) -> None:
    models = (baseline.model, selected.model)
    if any(
        model.evidence_class != "ATTESTED_EXTERNAL"
        or model.total_tokens is None
        or model.attestation is None
        for model in models
    ):
        raise ResearchPairedExecutionError("Research pair requires attested external model receipts")
    total_tokens = sum(model.total_tokens or 0 for model in models)
    total_latency_ms = sum(model.attestation.total_latency_ms for model in models if model.attestation)
    if (
        total_tokens > spec.budget.pair_max_tokens
        or total_latency_ms > spec.budget.pair_max_wall_time_ms
    ):
        raise ResearchPairedExecutionError("Research pair exceeded its preregistered total budget")


def _citations_from_output(
    *,
    citation_extractor: ResearchCitationExtractor,
    output_text: str,
) -> tuple[ResearchCitationSpanV1, ...]:
    try:
        citations = tuple(citation_extractor(output_text))
    except Exception as exc:
        raise ResearchPairedExecutionError("Research citation extraction failed") from exc
    if not all(isinstance(item, ResearchCitationSpanV1) for item in citations):
        raise ResearchPairedExecutionError(
            "Research citation extractor returned an invalid citation proposal"
        )
    return citations


def _arm_inputs(value: Mapping[str, ResearchArmRuntimeInputV1]) -> dict[str, ResearchArmRuntimeInputV1]:
    if not isinstance(value, Mapping):
        raise ResearchPairedExecutionError("arm_inputs must be a mapping")
    result = {str(role or "").strip().upper(): item for role, item in value.items()}
    if len(result) != len(value) or set(result) != {"BASELINE", "SELECTED"}:
        raise ResearchPairedExecutionError(
            "arm_inputs must contain BASELINE and SELECTED exactly once"
        )
    if not all(isinstance(item, ResearchArmRuntimeInputV1) for item in result.values()):
        raise ResearchPairedExecutionError("arm_inputs must contain ResearchArmRuntimeInputV1 values")
    return result


def _execution_id_for(*, run_id: str, role: str, arm_id: str) -> str:
    payload = {
        "schema": "natsume.research-paired-execution.v1",
        "run_id": run_id,
        "role": role,
        "arm_id": arm_id,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"researchexec_{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:32]}"


def _text(value: Any, *, field: str, limit: int) -> str:
    result = str(value or "").strip()
    if not result or len(result) > limit:
        raise ResearchPairedExecutionError(f"{field} must be non-empty and bounded")
    return result


def _is_digest(value: Any) -> bool:
    result = str(value or "").strip().lower()
    return len(result) == 64 and all(char in "0123456789abcdef" for char in result)


__all__ = [
    "ResearchArmRuntimeInputV1",
    "ResearchCitationExtractor",
    "ResearchCitationVerifierPort",
    "ResearchPairedExecutionError",
    "ResearchPairedExecutionResult",
    "ResearchPairedExecutionRunner",
]
