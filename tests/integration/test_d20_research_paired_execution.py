"""Local mechanics for the D20c paired-run owner.

These tests use an in-process canonical-model double only to exercise durable
recovery mechanics.  They are not an external-provider attestation, cohort or
benefit result; the production acceptance status remains external-pending.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from unittest.mock import patch

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.planning_search.research_benchmark_contracts import (
    ResearchArmPlanV1,
    ResearchCitationSpanV1,
    ResearchCorpusBindingV1,
    ResearchExecutionBudgetV1,
    ResearchExecutionProfileV1,
    ResearchVerifierReceiptV1,
)
from host_brain.model_runtime.contracts import (
    AttemptStatus,
    ModelAttemptRecord,
    ModelAttemptResult,
    ProviderResponse,
    ProviderTarget,
    RouteDecision,
    sha256_digest,
)
from host_brain.persistence.research_shadow_ledger_repository import (
    ResearchArmOperationState,
    SQLiteResearchShadowLedger,
)
from host_brain.planning_search.attested_external_research_adapter import (
    AttestedExternalResearchAdapter,
)
from host_brain.planning_search.research_citation_output_parser import (
    RESEARCH_CITATION_OUTPUT_SCHEMA_DIGEST_V1,
)
from host_brain.planning_search.research_paired_execution import (
    ResearchArmRuntimeInputV1,
    ResearchPairedExecutionError,
    ResearchPairedExecutionRunner,
)


PROMPT_DIGEST = "a" * 64


class _AuditLookup:
    def __init__(self) -> None:
        self.records: dict[str, tuple[ModelAttemptRecord, ...]] = {}

    def records_for_request(self, request_id: str) -> tuple[ModelAttemptRecord, ...]:
        return self.records.get(request_id, ())


class _CanonicalModelService:
    """Test-only stand-in for canonical service output and durable audit rows."""

    def __init__(
        self,
        *,
        audit: _AuditLookup,
        prompt_digests: dict[str, str] | None = None,
        token_counts: dict[str, int] | None = None,
        fail_roles: set[str] | None = None,
    ) -> None:
        self._audit = audit
        self._prompt_digests = dict(prompt_digests or {})
        self._token_counts = dict(token_counts or {})
        self._fail_roles = set(fail_roles or set())
        self.calls: list[str] = []

    async def complete(self, request):  # noqa: ANN201
        role = str(request.metadata["research_arm_id"])
        self.calls.append(role)
        if role in self._fail_roles:
            raise RuntimeError(f"test model failure: {role}")
        target = ProviderTarget("openai-transport", "openai", "d20-research")
        output = f"private-output::{role}::do-not-persist"
        response = ProviderResponse(
            content=output,
            metadata={"provider_latency_ms": 30},
        )
        prompt_digest = self._prompt_digests.get(role, PROMPT_DIGEST)
        tokens = self._token_counts.get(role, 40)
        terminal = ModelAttemptRecord(
            attempt_id=f"attempt::{request.request_id}",
            request_id=request.request_id,
            route_decision_id=f"route::{request.request_id}",
            attempt_index=1,
            status=AttemptStatus.SUCCEEDED,
            retry_reason="initial",
            adapter_id=target.adapter_id,
            provider=target.provider,
            model_id=target.model_id,
            prompt_id=request.prompt.prompt_id,
            prompt_version="1",
            prompt_digest=prompt_digest,
            started_at_ms=100,
            ended_at_ms=160,
            latency_ms=60,
            output_digest=sha256_digest(output),
            metadata={
                "scene_type": "RESEARCH",
                "cognitive_tier": "RESEARCH",
                "provider_budget_measurements": {
                    "token_count": tokens,
                    "completion_tokens": max(1, tokens // 2),
                    "model_cost_micros": tokens,
                    "tool_call_count": 0,
                },
                "provider_transport_measurements": {
                    "provider_latency_ms": 30,
                    "transport_identity_digest": "d" * 64,
                    "transport_external": True,
                },
            },
        )
        started = replace(
            terminal,
            status=AttemptStatus.STARTED,
            ended_at_ms=None,
            latency_ms=None,
            output_digest=None,
            metadata={"scene_type": "RESEARCH", "cognitive_tier": "RESEARCH"},
        )
        self._audit.records[request.request_id] = (started, terminal)
        return ModelAttemptResult(
            request_id=request.request_id,
            success=True,
            route_decision=RouteDecision(
                terminal.route_decision_id,
                (target,),
                "test canonical Research route",
                max_attempts=1,
            ),
            attempts=(terminal,),
            response=response,
            final_target=target,
        )


class _Verifier:
    def __init__(self, *, instance_id: int, calls: list[tuple[int, str, int]]) -> None:
        self.instance_id = instance_id
        self._calls = calls

    def verify(
        self,
        *,
        execution_id: str,
        citations: tuple[ResearchCitationSpanV1, ...],
    ) -> ResearchVerifierReceiptV1:
        self._calls.append((self.instance_id, execution_id, len(citations)))
        return ResearchVerifierReceiptV1(
            execution_id=execution_id,
            citation_count=len(citations),
            cited_span_count=len(citations),
            citation_coverage=1.0 if citations else 0.0,
            citation_validity=1.0 if citations else 0.0,
            evidence_digest=("b" if self.instance_id % 2 else "c") * 64,
        )


class _VerifierFactory:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str, int]] = []
        self._next_id = 0

    def __call__(self) -> _Verifier:
        self._next_id += 1
        return _Verifier(instance_id=self._next_id, calls=self.calls)


def _binding() -> ResearchCorpusBindingV1:
    return ResearchCorpusBindingV1(
        snapshot_digest="1" * 64,
        corpus_ref="artifact://research/corpus/d20c-paired",
        manifest_digest="2" * 64,
        source_ids=("primary",),
        source_refs=("artifact://research/primary",),
        source_digests=("3" * 64,),
        authority_tiers=("primary",),
    )


def _plan(
    arm_id: str,
    candidate: str,
    *,
    prompt_digest: str | None = None,
) -> ResearchArmPlanV1:
    return ResearchArmPlanV1(
        arm_id=arm_id,
        candidate_ids=(candidate,),
        source_ids=("primary",),
        graph_digest="4" * 64,
        path_digest="5" * 64,
        prompt_digest=prompt_digest,
    )


def _ledger_and_run(
    tmp_path,
    *,
    randomization_draw: int = 0,
    pair_max_tokens: int = 200,
    arm_prompt_digests: dict[str, str] | None = None,
) -> tuple[SQLiteResearchShadowLedger, str]:
    db_path = tmp_path / "d20c-paired.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="d20c-paired-runner"
    )
    assert result.success is True
    ledger = SQLiteResearchShadowLedger(db_path)
    profile = ResearchExecutionProfileV1(
        prompt_digest=PROMPT_DIGEST,
        profile_digest="6" * 64,
        output_schema_digest=RESEARCH_CITATION_OUTPUT_SCHEMA_DIGEST_V1,
    )
    budget = ResearchExecutionBudgetV1(
        arm_max_tokens=100,
        arm_max_tool_calls=1,
        arm_max_wall_time_ms=100,
        pair_max_tokens=pair_max_tokens,
        pair_max_wall_time_ms=200,
    )
    registration_id = ledger.register(_binding(), profile, budget)
    with patch(
        "host_brain.persistence.research_shadow_ledger_repository.secrets.randbelow",
        return_value=randomization_draw,
    ):
        run_id = ledger.preregister_randomized_run(
            registration_id=registration_id,
            baseline=_plan(
                "baseline",
                "research.baseline",
                prompt_digest=(arm_prompt_digests or {}).get("baseline"),
            ),
            selected=_plan(
                "selected",
                "research.selected",
                prompt_digest=(arm_prompt_digests or {}).get("selected"),
            ),
            cohort_run_key=f"d20c-paired-{randomization_draw}-{pair_max_tokens}",
        )
    return ledger, run_id


def _insert_legacy_run(ledger: SQLiteResearchShadowLedger) -> str:
    profile = ResearchExecutionProfileV1(
        prompt_digest=PROMPT_DIGEST,
        profile_digest="6" * 64,
        output_schema_digest=RESEARCH_CITATION_OUTPUT_SCHEMA_DIGEST_V1,
    )
    budget = ResearchExecutionBudgetV1(100, 1, 100, 200, 200)
    registration_id = ledger.register(_binding(), profile, budget)
    baseline = _plan("legacy-baseline", "research.legacy.baseline")
    selected = _plan("legacy-selected", "research.legacy.selected")
    payload = {
        "registration_id": registration_id,
        "baseline": baseline.to_dict(),
        "selected": selected.to_dict(),
        "arm_order": ["BASELINE", "SELECTED"],
        "randomized_arm_order": True,
        "production_influence_enabled": False,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    run_id = f"researchrun_{digest[:32]}"
    with ledger._connection() as conn:  # noqa: SLF001 - creates a read-only legacy compatibility fixture.
        conn.execute(
            """
            INSERT INTO planning_search_research_run_spec
            (run_id, spec_digest, registration_id, baseline_arm_id, selected_arm_id,
             spec_json, randomized_arm_order, production_influence_enabled, created_at_ms)
            VALUES (?, ?, ?, ?, ?, ?, 1, 0, 0)
            """,
            (run_id, digest, registration_id, baseline.arm_id, selected.arm_id, encoded),
        )
        conn.commit()
    return run_id


def _inputs() -> dict[str, ResearchArmRuntimeInputV1]:
    return {
        "BASELINE": ResearchArmRuntimeInputV1(
            prompt_id="research.prompt",
            messages=({"role": "user", "content": "private baseline prompt"},),
            deadline_ms=1_000,
        ),
        "SELECTED": ResearchArmRuntimeInputV1(
            prompt_id="research.prompt",
            messages=({"role": "user", "content": "private selected prompt"},),
            deadline_ms=1_000,
        ),
    }


class _CitationExtractor:
    output_schema_digest = RESEARCH_CITATION_OUTPUT_SCHEMA_DIGEST_V1

    def __init__(self, outputs: list[str]) -> None:
        self._outputs = outputs

    def __call__(self, output: str) -> tuple[ResearchCitationSpanV1, ...]:
        self._outputs.append(output)
        return (
            ResearchCitationSpanV1(
                source_id="primary",
                start_byte=0,
                end_byte=1,
                span_digest="d" * 64,
            ),
        )


class _MismatchedCitationExtractor(_CitationExtractor):
    output_schema_digest = "f" * 64


def _runner(
    *,
    ledger: SQLiteResearchShadowLedger,
    audit: _AuditLookup,
    service: _CanonicalModelService,
    outputs: list[str],
    factory: _VerifierFactory,
) -> ResearchPairedExecutionRunner:
    return ResearchPairedExecutionRunner(
        ledger=ledger,
        external_adapter=AttestedExternalResearchAdapter(
            model_attempt_service=service,
            attempt_audit_lookup=audit,
        ),
        citation_extractor=_CitationExtractor(outputs),
        verifier_factory=factory,
        lease_duration_ms=10_000,
    )


async def test_d20c_runner_executes_persisted_order_and_exact_terminal_replay(tmp_path) -> None:
    ledger, run_id = _ledger_and_run(tmp_path, randomization_draw=1)
    audit = _AuditLookup()
    service = _CanonicalModelService(audit=audit)
    outputs: list[str] = []
    factory = _VerifierFactory()
    runner = _runner(
        ledger=ledger,
        audit=audit,
        service=service,
        outputs=outputs,
        factory=factory,
    )

    result = await runner.execute_preregistered(
        run_id=run_id,
        arm_inputs=_inputs(),
        owner_id="worker-a",
    )

    assert result.replayed_terminal_receipt is False
    assert [item.role for item in result.transient_outputs] == ["BASELINE", "SELECTED"]
    assert [item.run.output_text for item in result.transient_outputs] == [
        "private-output::baseline::do-not-persist",
        "private-output::selected::do-not-persist",
    ]
    assert result.receipt.arm_order == ("SELECTED", "BASELINE")
    assert service.calls == ["selected", "baseline"]
    assert [instance_id for instance_id, _, _ in factory.calls] == [1, 2]
    assert outputs == [
        "private-output::selected::do-not-persist",
        "private-output::baseline::do-not-persist",
    ]
    with ledger._connection() as conn:  # noqa: SLF001 - verifies durable public-safe payloads.
        serialized = "\n".join(
            str(row[0])
            for row in conn.execute(
                "SELECT model_receipt_json FROM planning_search_research_arm_execution"
            ).fetchall()
        )
    assert "private-output" not in serialized
    assert "private baseline prompt" not in serialized
    assert "private selected prompt" not in serialized

    replay = await runner.execute_preregistered(
        run_id=run_id,
        arm_inputs=_inputs(),
        owner_id="worker-b",
    )
    assert replay.replayed_terminal_receipt is True
    assert replay.transient_outputs == ()
    assert replay.receipt_ref == result.receipt_ref
    assert service.calls == ["selected", "baseline"]


async def test_d20c_runner_rejects_mismatched_output_schema_before_model_call(tmp_path) -> None:
    ledger, run_id = _ledger_and_run(tmp_path)
    audit = _AuditLookup()
    service = _CanonicalModelService(audit=audit)
    runner = ResearchPairedExecutionRunner(
        ledger=ledger,
        external_adapter=AttestedExternalResearchAdapter(
            model_attempt_service=service,
            attempt_audit_lookup=audit,
        ),
        citation_extractor=_MismatchedCitationExtractor([]),
        verifier_factory=_VerifierFactory(),
        lease_duration_ms=10_000,
    )

    with pytest.raises(ResearchPairedExecutionError, match="schema does not bind"):
        await runner.execute_preregistered(
            run_id=run_id,
            arm_inputs=_inputs(),
            owner_id="worker-a",
        )

    assert service.calls == []


async def test_d20c_runner_refuses_legacy_caller_chosen_order_before_model_call(tmp_path) -> None:
    ledger, _ = _ledger_and_run(tmp_path)
    legacy_run_id = _insert_legacy_run(ledger)
    legacy_spec = ledger.get_paired_run_spec(run_id=legacy_run_id)
    assert legacy_spec.owner_randomized is False
    audit = _AuditLookup()
    service = _CanonicalModelService(audit=audit)
    runner = _runner(
        ledger=ledger,
        audit=audit,
        service=service,
        outputs=[],
        factory=_VerifierFactory(),
    )

    with pytest.raises(ResearchPairedExecutionError, match="owner-generated arm randomization"):
        await runner.execute_preregistered(
            run_id=legacy_run_id,
            arm_inputs=_inputs(),
            owner_id="worker-a",
        )

    assert service.calls == []


async def test_d20c_runner_resumes_after_one_persisted_arm_without_recalling_it(tmp_path) -> None:
    ledger, run_id = _ledger_and_run(tmp_path)
    audit = _AuditLookup()
    first_service = _CanonicalModelService(audit=audit, fail_roles={"selected"})
    first = _runner(
        ledger=ledger,
        audit=audit,
        service=first_service,
        outputs=[],
        factory=_VerifierFactory(),
    )
    with pytest.raises(ResearchPairedExecutionError):
        await first.execute_preregistered(
            run_id=run_id,
            arm_inputs=_inputs(),
            owner_id="worker-a",
        )
    assert first_service.calls == ["baseline", "selected"]
    assert ledger.get_arm_operation(run_id=run_id, role="BASELINE").state is ResearchArmOperationState.EVIDENCE_PERSISTED
    assert ledger.get_arm_execution(run_id=run_id, role="BASELINE") is not None

    resumed_service = _CanonicalModelService(audit=audit)
    resumed = _runner(
        ledger=SQLiteResearchShadowLedger(ledger.db_path),
        audit=audit,
        service=resumed_service,
        outputs=[],
        factory=_VerifierFactory(),
    )
    result = await resumed.execute_preregistered(
        run_id=run_id,
        arm_inputs=_inputs(),
        owner_id="worker-b",
    )
    assert result.replayed_terminal_receipt is False
    assert resumed_service.calls == ["selected"]


async def test_d20c_runner_freezes_started_arm_when_audit_exists_without_evidence(tmp_path) -> None:
    ledger, run_id = _ledger_and_run(tmp_path)
    spec = ledger.get_paired_run_spec(run_id=run_id)
    execution_id = ResearchPairedExecutionRunner.execution_id_for(
        run_id=run_id,
        role="BASELINE",
        arm_id=spec.baseline.arm_id,
    )
    reserved = ledger.reserve_arm_operation(
        run_id=run_id,
        role="BASELINE",
        plan=spec.baseline,
        execution_id=execution_id,
    )
    claim = ledger.claim_arm_operation(
        operation_id=reserved.operation_id,
        owner_id="crashed-worker",
        lease_duration_ms=10_000,
    )
    ledger.begin_external_call(operation_id=reserved.operation_id, claim_id=claim.claim_id or "")
    audit = _AuditLookup()
    audit.records[execution_id] = (_audit_record(execution_id),)
    service = _CanonicalModelService(audit=audit)
    runner = _runner(
        ledger=ledger,
        audit=audit,
        service=service,
        outputs=[],
        factory=_VerifierFactory(),
    )

    with pytest.raises(ResearchPairedExecutionError, match="reconciliation required"):
        await runner.execute_preregistered(
            run_id=run_id,
            arm_inputs=_inputs(),
            owner_id="restarted-worker",
        )

    operation = ledger.get_arm_operation(run_id=run_id, role="BASELINE")
    assert operation is not None
    assert operation.state is ResearchArmOperationState.RECONCILIATION_REQUIRED
    assert service.calls == []


async def test_d20c_runner_rejects_prompt_profile_drift_and_marks_reconciliation(tmp_path) -> None:
    ledger, run_id = _ledger_and_run(tmp_path)
    audit = _AuditLookup()
    service = _CanonicalModelService(audit=audit, prompt_digests={"baseline": "e" * 64})
    runner = _runner(
        ledger=ledger,
        audit=audit,
        service=service,
        outputs=[],
        factory=_VerifierFactory(),
    )

    with pytest.raises(ResearchPairedExecutionError, match="preregistered execution profile"):
        await runner.execute_preregistered(
            run_id=run_id,
            arm_inputs=_inputs(),
            owner_id="worker-a",
        )

    operation = ledger.get_arm_operation(run_id=run_id, role="BASELINE")
    assert operation is not None
    assert operation.state is ResearchArmOperationState.RECONCILIATION_REQUIRED
    assert ledger.get_arm_execution(run_id=run_id, role="BASELINE") is None


async def test_d20c_runner_accepts_distinct_preregistered_arm_prompt_digests(tmp_path) -> None:
    baseline_digest = "7" * 64
    selected_digest = "8" * 64
    ledger, run_id = _ledger_and_run(
        tmp_path,
        arm_prompt_digests={
            "baseline": baseline_digest,
            "selected": selected_digest,
        },
    )
    reopened = SQLiteResearchShadowLedger(ledger.db_path).get_paired_run_spec(
        run_id=run_id
    )
    assert reopened.baseline.prompt_digest == baseline_digest
    assert reopened.selected.prompt_digest == selected_digest
    assert reopened.profile.prompt_digest == PROMPT_DIGEST

    audit = _AuditLookup()
    service = _CanonicalModelService(
        audit=audit,
        prompt_digests={
            "baseline": baseline_digest,
            "selected": selected_digest,
        },
    )
    result = await _runner(
        ledger=ledger,
        audit=audit,
        service=service,
        outputs=[],
        factory=_VerifierFactory(),
    ).execute_preregistered(
        run_id=run_id,
        arm_inputs=_inputs(),
        owner_id="worker-distinct-prompts",
    )

    assert result.receipt.baseline_model.prompt_digest == baseline_digest
    assert result.receipt.selected_model.prompt_digest == selected_digest


async def test_d20c_runner_rejects_pair_budget_after_both_durable_arms(tmp_path) -> None:
    ledger, run_id = _ledger_and_run(tmp_path, pair_max_tokens=150)
    audit = _AuditLookup()
    service = _CanonicalModelService(
        audit=audit,
        token_counts={"baseline": 90, "selected": 90},
    )
    runner = _runner(
        ledger=ledger,
        audit=audit,
        service=service,
        outputs=[],
        factory=_VerifierFactory(),
    )

    with pytest.raises(ResearchPairedExecutionError, match="total budget"):
        await runner.execute_preregistered(
            run_id=run_id,
            arm_inputs=_inputs(),
            owner_id="worker-a",
        )

    assert service.calls == ["baseline", "selected"]
    assert ledger.get_arm_execution(run_id=run_id, role="BASELINE") is not None
    assert ledger.get_arm_execution(run_id=run_id, role="SELECTED") is not None
    assert ledger.get_terminal_receipt(run_id=run_id) is None


def _audit_record(execution_id: str) -> ModelAttemptRecord:
    return ModelAttemptRecord(
        attempt_id=f"orphaned::{execution_id}",
        request_id=execution_id,
        route_decision_id="orphaned-route",
        attempt_index=1,
        status=AttemptStatus.STARTED,
        retry_reason="initial",
        adapter_id="openai-transport",
        provider="openai",
        model_id="d20-research",
        prompt_id="research.prompt",
        prompt_version="1",
        prompt_digest=PROMPT_DIGEST,
        started_at_ms=100,
        metadata={"scene_type": "RESEARCH", "cognitive_tier": "RESEARCH"},
    )
