"""Independent durable verification and acceptance for governed workers.

The worker that produced a COMMIT event is not an acceptance authority.  Both
ports below reopen the immutable public projection through a read-only port and
rebuild its digest before they make a decision.  Acceptance deliberately
reopens the projection a second time instead of trusting the verification
call's in-memory value.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from typing import Any

from host_brain.app.governed_host_contracts import _ACCEPTANCE_CRITERION
from host_brain.contracts.agent_runtime.acceptance_judge import (
    ACCEPTANCE_JUDGEMENT_SCHEMA_VERSION,
    AcceptanceJudgement,
)
from host_brain.contracts.agent_runtime.node_artifact import (
    NodeArtifactContractError,
    build_node_artifact_payload,
)
from host_brain.contracts.agent_runtime.node_input_attestation import (
    NodeInputArtifactAttestationError,
    build_node_input_artifact_attestation,
)
from host_brain.contracts.agent_runtime.verification_contract import (
    VERIFICATION_RESULT_SCHEMA_VERSION,
    VerificationResult,
)
from host_brain.contracts.agent_runtime.control_route_execution_policy import (
    ControlRouteExecutionPolicyError,
    require_control_route_execution_policy,
)
from host_brain.persistence.worker_projection_repository import (
    WorkerProjectionStoreError,
    validate_worker_projection_payload,
)
from host_brain.task_runtime.worker_registry import (
    GovernedWorkerRegistryError,
    GovernedWorkerRoute,
)
from natsume_contracts.task import HostCommandSurface


GOVERNED_WORKER_PROJECTION_NOT_FOUND = "GOVERNED_WORKER_PROJECTION_NOT_FOUND"
GOVERNED_WORKER_PROJECTION_UNAVAILABLE = (
    "GOVERNED_WORKER_PROJECTION_UNAVAILABLE"
)
GOVERNED_WORKER_PROJECTION_INVALID = "GOVERNED_WORKER_PROJECTION_INVALID"
GOVERNED_WORKER_COMMIT_DRIFT = "GOVERNED_WORKER_COMMIT_DRIFT"
GOVERNED_WORKER_AUTHORITY_DRIFT = "GOVERNED_WORKER_AUTHORITY_DRIFT"
GOVERNED_WORKER_VERIFICATION_POLICY_INVALID = (
    "GOVERNED_WORKER_VERIFICATION_POLICY_INVALID"
)
GOVERNED_WORKER_OUTPUT_CONTRACT_INVALID = (
    "GOVERNED_WORKER_OUTPUT_CONTRACT_INVALID"
)
GOVERNED_WORKER_VERIFICATION_RESULT_INVALID = (
    "GOVERNED_WORKER_VERIFICATION_RESULT_INVALID"
)
GOVERNED_ACCEPTANCE_CRITERION_UNPROVEN = (
    "GOVERNED_ACCEPTANCE_CRITERION_UNPROVEN"
)

_PROJECTION_BODY_FIELDS = (
    "phase",
    "done",
    "decision",
    "reason_code",
    "command_id",
    "request_id",
    "narrative",
    "route_type",
    "target_id",
    "graph_id",
    "graph_revision",
    "node_id",
    "attempt_id",
    "node_grant_ref",
)
_OPTIONAL_PROJECTION_FIELDS = (
    "acceptance_effect_intents",
    "budget_usage",
)
_WORKER_COMMIT_FIELDS = frozenset(
    (
        *_PROJECTION_BODY_FIELDS,
        *_OPTIONAL_PROJECTION_FIELDS,
        "audit_refs",
        "receipt_ref",
    )
)
_COMMIT_AUDIT_EXTRAS = frozenset({"path", "worker_projection_ref"})
_STRUCTURAL_DURABLE_PROJECTION_CRITERIA = frozenset(
    {
        _ACCEPTANCE_CRITERION,
        # Host route contracts emitted this wording before the app-local
        # governed builder and orchestration builder were unified.  It has the
        # same machine meaning and remains valid for durable replay.
        "frozen worker returned a durable accepted projection",
    }
)


class GovernedWorkerEvidenceError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or GOVERNED_WORKER_PROJECTION_INVALID)
        self.detail = str(detail or "governed worker evidence is invalid")
        super().__init__(f"{self.reason_code}: {self.detail}")


@dataclass(frozen=True, slots=True)
class _ProjectionInspection:
    receipt_ref: str
    payload_digest: str
    projection: dict[str, Any]
    evidence_refs: tuple[str, ...]


class DurableWorkerCommitVerifier:
    """Verify a COMMIT against an independently reopened durable projection."""

    __slots__ = ("_projection_reader",)

    def __init__(self, *, projection_reader: Any) -> None:
        if not callable(getattr(projection_reader, "load", None)):
            raise TypeError("projection_reader must expose load(receipt_ref)")
        self._projection_reader = projection_reader

    def verify(
        self,
        *,
        command: Any,
        work_order: Any,
        execution_context: Mapping[str, Any],
        worker_commit: Mapping[str, Any],
        evidence_refs: Sequence[str],
    ) -> dict[str, Any]:
        started_at_ms = _non_negative_timestamp(command)
        checks: list[dict[str, Any]] = []
        reason_code: str | None = None
        inspection: _ProjectionInspection | None = None
        try:
            inspection = _inspect_projection(
                projection_reader=self._projection_reader,
                command=command,
                work_order=work_order,
                execution_context=execution_context,
                worker_commit=worker_commit,
                evidence_refs=evidence_refs,
            )
            checks.extend(
                (
                    _check("durable-projection-schema"),
                    _check("command-graph-node-attempt-binding"),
                    _check("route-target-grant-binding"),
                    _check("worker-commit-replay-binding"),
                    _check("verification-policy-and-output-contract"),
                )
            )
        except GovernedWorkerEvidenceError as exc:
            reason_code = exc.reason_code
            checks.append(_check("durable-worker-projection", reason_code))

        refs = (
            inspection.evidence_refs
            if inspection is not None
            else _failure_evidence_refs(worker_commit, evidence_refs)
        )
        result = {
            "schema_version": VERIFICATION_RESULT_SCHEMA_VERSION,
            "verification_id": _stable_id(
                "verify",
                command=command,
                execution_context=execution_context,
            ),
            "work_order_id": _required_context_text(
                execution_context,
                "work_order_id",
            ),
            "task_graph_id": _required_context_text(
                execution_context,
                "graph_id",
            ),
            "task_node_id": _required_context_text(
                execution_context,
                "node_id",
            ),
            "attempt_id": _required_context_text(
                execution_context,
                "attempt_id",
            ),
            "required": True,
            "status": "PASS" if reason_code is None else "FAIL",
            "reason_code": reason_code,
            "evidence_refs": list(refs),
            "checks": checks,
            "started_at_ms": started_at_ms,
            "finished_at_ms": started_at_ms,
        }
        return VerificationResult.from_mapping(result).to_dict()


class DurableWorkerCommitAcceptance:
    """Judge only evidence-backed criteria after a second durable reopen."""

    __slots__ = ("_projection_reader",)

    def __init__(self, *, projection_reader: Any) -> None:
        if not callable(getattr(projection_reader, "load", None)):
            raise TypeError("projection_reader must expose load(receipt_ref)")
        self._projection_reader = projection_reader

    def judge(
        self,
        *,
        command: Any,
        work_order: Any,
        execution_context: Mapping[str, Any],
        worker_commit: Mapping[str, Any],
        verification_result: Mapping[str, Any],
        evidence_refs: Sequence[str],
    ) -> dict[str, Any]:
        started_at_ms = _non_negative_timestamp(command)
        criteria_texts = _acceptance_criteria(execution_context)
        reason_code: str | None = None
        inspection: _ProjectionInspection | None = None
        try:
            verified = VerificationResult.from_mapping(verification_result)
            _assert_verification_binding(
                verified=verified,
                execution_context=execution_context,
                evidence_refs=evidence_refs,
            )
            inspection = _inspect_projection(
                projection_reader=self._projection_reader,
                command=command,
                work_order=work_order,
                execution_context=execution_context,
                worker_commit=worker_commit,
                evidence_refs=evidence_refs,
            )
        except (GovernedWorkerEvidenceError, ValueError) as exc:
            reason_code = getattr(
                exc,
                "reason_code",
                GOVERNED_WORKER_VERIFICATION_RESULT_INVALID,
            )

        refs = (
            inspection.evidence_refs
            if inspection is not None
            else _failure_evidence_refs(worker_commit, evidence_refs)
        )
        unsupported = ()
        if reason_code is None and inspection is not None:
            unsupported = tuple(
                criterion
                for criterion in criteria_texts
                if not _criterion_is_proven(
                    criterion,
                    inspection=inspection,
                    work_order=work_order,
                    execution_context=execution_context,
                )
            )
        if reason_code is None and unsupported:
            reason_code = GOVERNED_ACCEPTANCE_CRITERION_UNPROVEN
        accepted = reason_code is None
        criteria = [
            {
                "criterion_id": (
                    "criterion-governed-durable-projection-"
                    + str(index)
                ),
                "criterion_text": criterion_text,
                "verdict": "PASS" if accepted else "UNKNOWN",
                "evidence_refs": list(refs),
                "deterministic": accepted,
                "reason_code": None if accepted else reason_code,
                "critic_verdict": None,
            }
            for index, criterion_text in enumerate(criteria_texts, start=1)
        ]
        result = {
            "schema_version": ACCEPTANCE_JUDGEMENT_SCHEMA_VERSION,
            "judgement_id": _stable_id(
                "accept",
                command=command,
                execution_context=execution_context,
            ),
            "work_order_id": _required_context_text(
                execution_context,
                "work_order_id",
            ),
            "task_graph_id": _required_context_text(
                execution_context,
                "graph_id",
            ),
            "task_node_id": _required_context_text(
                execution_context,
                "node_id",
            ),
            "attempt_id": _required_context_text(
                execution_context,
                "attempt_id",
            ),
            "decision": "ACCEPT" if accepted else "REVIEW",
            "reason_code": None if accepted else reason_code,
            "evidence_refs": list(refs),
            "criteria": criteria,
            "critic_candidate": (
                None
                if accepted
                else {
                    "source": "durable_worker_acceptance",
                    "unsupported_criteria": list(unsupported),
                }
            ),
            "started_at_ms": started_at_ms,
            "finished_at_ms": started_at_ms,
        }
        return AcceptanceJudgement.from_mapping(result).to_dict()


def _inspect_projection(
    *,
    projection_reader: Any,
    command: Any,
    work_order: Any,
    execution_context: Mapping[str, Any],
    worker_commit: Mapping[str, Any],
    evidence_refs: Sequence[str],
) -> _ProjectionInspection:
    if getattr(command, "surface", None) in {
        HostCommandSurface.INTERNAL,
        HostCommandSurface.OPERATOR,
        HostCommandSurface.SCHEDULER,
    }:
        try:
            require_control_route_execution_policy(
                command=command,
                work_order=work_order,
            )
        except ControlRouteExecutionPolicyError as exc:
            _blocked(
                GOVERNED_WORKER_AUTHORITY_DRIFT,
                exc.detail,
            )
    if not isinstance(worker_commit, Mapping):
        _blocked(
            GOVERNED_WORKER_COMMIT_DRIFT,
            "worker COMMIT must be an object",
        )
    receipt_ref = str(worker_commit.get("receipt_ref") or "").strip()
    if not receipt_ref:
        _blocked(
            GOVERNED_WORKER_COMMIT_DRIFT,
            "worker COMMIT has no durable receipt",
        )
    try:
        loaded = projection_reader.load(receipt_ref)
    except WorkerProjectionStoreError as exc:
        _blocked(
            GOVERNED_WORKER_PROJECTION_INVALID,
            f"durable worker projection could not be validated: {exc.reason_code}",
        )
    except Exception as exc:
        _blocked(
            GOVERNED_WORKER_PROJECTION_UNAVAILABLE,
            "durable worker projection reader failed: "
            + type(exc).__name__,
        )
    if not isinstance(loaded, Mapping):
        _blocked(
            GOVERNED_WORKER_PROJECTION_NOT_FOUND,
            "committed worker receipt has no durable projection",
        )
    try:
        canonical = validate_worker_projection_payload(
            loaded,
            expected_projection_id=receipt_ref,
        )
    except WorkerProjectionStoreError as exc:
        _blocked(
            GOVERNED_WORKER_PROJECTION_INVALID,
            f"durable worker projection is invalid: {exc.reason_code}",
        )
    payload_digest = str(canonical["payload_digest"])
    if receipt_ref.rsplit("/", 1)[-1] != payload_digest.removeprefix("sha256:"):
        _blocked(
            GOVERNED_WORKER_PROJECTION_INVALID,
            "worker projection identity is not bound to its payload digest",
        )
    _assert_authority_binding(
        canonical=canonical,
        command=command,
        work_order=work_order,
        execution_context=execution_context,
    )
    _assert_commit_binding(
        canonical=canonical,
        worker_commit=worker_commit,
        receipt_ref=receipt_ref,
    )
    _assert_verification_policy_and_output(
        canonical=canonical,
        receipt_ref=receipt_ref,
        work_order=work_order,
        execution_context=execution_context,
    )
    refs = _normalized_evidence_refs(
        evidence_refs,
        receipt_ref=receipt_ref,
        payload_digest=payload_digest,
    )
    return _ProjectionInspection(
        receipt_ref=receipt_ref,
        payload_digest=payload_digest,
        projection=canonical,
        evidence_refs=refs,
    )


def _assert_authority_binding(
    *,
    canonical: Mapping[str, Any],
    command: Any,
    work_order: Any,
    execution_context: Mapping[str, Any],
) -> None:
    try:
        route = GovernedWorkerRoute.parse(
            _required_context_text(execution_context, "worker_route")
        )
    except GovernedWorkerRegistryError as exc:
        _blocked(
            GOVERNED_WORKER_AUTHORITY_DRIFT,
            f"frozen worker route is invalid: {exc.reason_code}",
        )
    expected_target = route.target_id or _required_context_text(
        execution_context,
        "target_id",
    )
    expected_revision = _positive_context_int(
        execution_context,
        "graph_revision",
    )
    expected_node_grant = _required_context_text(
        execution_context,
        "node_grant_ref",
    )
    bindings = (
        (canonical.get("command_id"), getattr(command, "command_id", None)),
        (canonical.get("request_id"), getattr(command, "request_id", None)),
        (
            canonical.get("graph_id"),
            _required_context_text(execution_context, "graph_id"),
        ),
        (canonical.get("graph_revision"), expected_revision),
        (
            canonical.get("node_id"),
            _required_context_text(execution_context, "node_id"),
        ),
        (
            canonical.get("attempt_id"),
            _required_context_text(execution_context, "attempt_id"),
        ),
        (str(canonical.get("route_type") or "").lower(), route.kind),
        (canonical.get("target_id"), expected_target),
        (canonical.get("node_grant_ref"), expected_node_grant),
    )
    if any(str(actual) != str(expected) for actual, expected in bindings):
        _blocked(
            GOVERNED_WORKER_AUTHORITY_DRIFT,
            "durable worker projection execution identity drifted",
        )
    if (
        getattr(command, "graph_id", None)
        != _required_context_text(execution_context, "graph_id")
        or getattr(command, "graph_revision", None) != expected_revision
        or getattr(command, "root_work_order_id", None)
        != getattr(work_order, "root_work_order_id", None)
        or getattr(work_order, "work_order_id", None)
        != _required_context_text(execution_context, "work_order_id")
        or getattr(work_order, "task_graph_id", None)
        != _required_context_text(execution_context, "graph_id")
        or getattr(work_order, "graph_revision", None) != expected_revision
    ):
        _blocked(
            GOVERNED_WORKER_AUTHORITY_DRIFT,
            "command, WorkOrder and execution context lineage drifted",
        )
    audit_refs = canonical.get("audit_refs")
    if (
        not isinstance(audit_refs, Mapping)
        or str(audit_refs.get("node_grant_ref") or "").strip()
        != expected_node_grant
    ):
        _blocked(
            GOVERNED_WORKER_AUTHORITY_DRIFT,
            "durable worker projection audit does not bind the node grant",
        )


def _assert_commit_binding(
    *,
    canonical: Mapping[str, Any],
    worker_commit: Mapping[str, Any],
    receipt_ref: str,
) -> None:
    if set(worker_commit) - _WORKER_COMMIT_FIELDS:
        _blocked(
            GOVERNED_WORKER_COMMIT_DRIFT,
            "worker COMMIT contains fields absent from the durable projection contract",
        )
    if str(worker_commit.get("receipt_ref") or "").strip() != receipt_ref:
        _blocked(
            GOVERNED_WORKER_COMMIT_DRIFT,
            "worker COMMIT receipt differs from the durable projection",
        )
    fields = (
        *_PROJECTION_BODY_FIELDS,
        *(
            field_name
            for field_name in _OPTIONAL_PROJECTION_FIELDS
            if field_name in canonical or field_name in worker_commit
        ),
    )
    if any(
        _canonical_json(worker_commit.get(field_name))
        != _canonical_json(canonical.get(field_name))
        for field_name in fields
    ):
        _blocked(
            GOVERNED_WORKER_COMMIT_DRIFT,
            "worker COMMIT body differs from the durable projection",
        )
    durable_audit = canonical.get("audit_refs")
    commit_audit = worker_commit.get("audit_refs")
    if not isinstance(durable_audit, Mapping) or not isinstance(
        commit_audit,
        Mapping,
    ):
        _blocked(
            GOVERNED_WORKER_COMMIT_DRIFT,
            "worker COMMIT audit references are invalid",
        )
    durable_keys = set(durable_audit)
    extra_keys = set(commit_audit) - durable_keys
    if (
        any(commit_audit.get(key) != durable_audit.get(key) for key in durable_keys)
        or extra_keys - _COMMIT_AUDIT_EXTRAS
        or (
            "worker_projection_ref" in commit_audit
            and str(commit_audit.get("worker_projection_ref") or "").strip()
            != receipt_ref
        )
    ):
        _blocked(
            GOVERNED_WORKER_COMMIT_DRIFT,
            "worker COMMIT audit differs from the durable projection",
        )
    if (
        canonical.get("decision") != "APPROVE"
        or not str(canonical.get("narrative") or "").strip()
    ):
        _blocked(
            GOVERNED_WORKER_COMMIT_DRIFT,
            "only an approved non-empty durable projection can be verified",
        )


def _assert_verification_policy_and_output(
    *,
    canonical: Mapping[str, Any],
    receipt_ref: str,
    work_order: Any,
    execution_context: Mapping[str, Any],
) -> None:
    plan = getattr(work_order, "verification_plan", None)
    if (
        not isinstance(plan, Mapping)
        or plan.get("required") is not True
        or str(plan.get("policy") or "").strip()
        != "durable_worker_projection"
    ):
        _blocked(
            GOVERNED_WORKER_VERIFICATION_POLICY_INVALID,
            "WorkOrder does not require the durable worker projection policy",
        )
    output_contract = execution_context.get("node_output_contract")
    if not isinstance(output_contract, Mapping) or not output_contract:
        _blocked(
            GOVERNED_WORKER_OUTPUT_CONTRACT_INVALID,
            "TaskNode output contract is absent from frozen execution context",
        )
    try:
        build_node_artifact_payload(
            canonical,
            receipt_ref=receipt_ref,
            output_contract=output_contract,
        )
    except NodeArtifactContractError as exc:
        _blocked(
            GOVERNED_WORKER_OUTPUT_CONTRACT_INVALID,
            f"durable projection violates TaskNode output contract: {exc}",
        )


def _assert_verification_binding(
    *,
    verified: VerificationResult,
    execution_context: Mapping[str, Any],
    evidence_refs: Sequence[str],
) -> None:
    expected_refs = tuple(
        dict.fromkeys(
            str(value or "").strip()
            for value in evidence_refs
            if str(value or "").strip()
        )
    )
    if (
        verified.status.value != "PASS"
        or verified.required is not True
        or verified.work_order_id
        != _required_context_text(execution_context, "work_order_id")
        or verified.task_graph_id
        != _required_context_text(execution_context, "graph_id")
        or verified.task_node_id
        != _required_context_text(execution_context, "node_id")
        or verified.attempt_id
        != _required_context_text(execution_context, "attempt_id")
        or any(value not in verified.evidence_refs for value in expected_refs)
    ):
        _blocked(
            GOVERNED_WORKER_VERIFICATION_RESULT_INVALID,
            "Acceptance received a non-PASS or identity-drifted VerificationResult",
        )


def _acceptance_criteria(
    execution_context: Mapping[str, Any],
) -> tuple[str, ...]:
    raw = execution_context.get("node_acceptance_criteria")
    if not isinstance(raw, (list, tuple)):
        return (_ACCEPTANCE_CRITERION,)
    values = tuple(
        str(value or "").strip()
        for value in raw
        if str(value or "").strip()
    )
    return values or (_ACCEPTANCE_CRITERION,)


def _criterion_is_proven(
    criterion: str,
    *,
    inspection: _ProjectionInspection,
    work_order: Any,
    execution_context: Mapping[str, Any],
) -> bool:
    if criterion in _STRUCTURAL_DURABLE_PROJECTION_CRITERIA:
        return True
    route_type = str(
        inspection.projection.get("route_type") or ""
    ).strip().lower()
    if criterion in {
        "return a durable governed capability receipt",
        "return one durable final response receipt",
    }:
        return route_type in {"skill", "chat"}
    if criterion == (
        "remain within the frozen capability catalog and task objective"
    ):
        return _planning_capability_binding_is_exact(
            inspection=inspection,
            work_order=work_order,
            execution_context=execution_context,
        )
    if criterion == "synthesize only the accepted dependency result":
        return (
            route_type == "chat"
            and _input_artifact_attestation_is_exact(
                inspection=inspection,
                execution_context=execution_context,
            )
        )
    return False


def _planning_capability_binding_is_exact(
    *,
    inspection: _ProjectionInspection,
    work_order: Any,
    execution_context: Mapping[str, Any],
) -> bool:
    metadata = getattr(work_order, "metadata", None)
    input_pack = getattr(work_order, "input_pack", None)
    if not isinstance(metadata, Mapping) or not isinstance(
        input_pack,
        Mapping,
    ):
        return False
    lineage = metadata.get("planning_lineage")
    bindings = (
        lineage.get("node_bindings")
        if isinstance(lineage, Mapping)
        else None
    )
    if not isinstance(bindings, list):
        return False
    node_id = _required_context_text(execution_context, "node_id")
    matches = [
        item
        for item in bindings
        if isinstance(item, Mapping)
        and str(item.get("node_id") or "").strip() == node_id
    ]
    if len(matches) != 1:
        return False
    binding = matches[0]
    target_id = str(
        inspection.projection.get("target_id") or ""
    ).strip()
    route_type = str(
        inspection.projection.get("route_type") or ""
    ).strip().lower()
    capability_plan = input_pack.get("capability_planning")
    catalog_ref = (
        str(capability_plan.get("catalog_ref") or "").strip()
        if isinstance(capability_plan, Mapping)
        else ""
    )
    catalog_digest = (
        str(capability_plan.get("catalog_digest") or "").strip()
        if isinstance(capability_plan, Mapping)
        else ""
    )
    capabilities = tuple(
        str(value or "").strip()
        for value in tuple(
            getattr(work_order, "capability_requirements", ()) or ()
        )
        if str(value or "").strip()
    )
    return (
        route_type == "skill"
        and str(binding.get("worker_route") or "").strip().lower()
        == route_type
        and str(binding.get("target_id") or "").strip() == target_id
        and target_id in capabilities
        and catalog_ref.startswith("capability-catalog://")
        and len(catalog_digest) == 64
        and all(character in "0123456789abcdef" for character in catalog_digest)
    )


def _input_artifact_attestation_is_exact(
    *,
    inspection: _ProjectionInspection,
    execution_context: Mapping[str, Any],
) -> bool:
    artifacts = execution_context.get("node_input_artifacts")
    if (
        not isinstance(artifacts, Sequence)
        or isinstance(artifacts, (str, bytes, bytearray))
        or not artifacts
        or any(not isinstance(item, Mapping) for item in artifacts)
    ):
        return False
    try:
        expected = build_node_input_artifact_attestation(artifacts)
    except NodeInputArtifactAttestationError:
        return False
    audit_refs = inspection.projection.get("audit_refs")
    trace = (
        audit_refs.get("trace_stage_meta")
        if isinstance(audit_refs, Mapping)
        else None
    )
    actual = (
        trace.get("node_input_artifact_attestation")
        if isinstance(trace, Mapping)
        else None
    )
    return isinstance(actual, Mapping) and dict(actual) == expected


def _normalized_evidence_refs(
    evidence_refs: Sequence[str],
    *,
    receipt_ref: str,
    payload_digest: str,
) -> tuple[str, ...]:
    digest_ref = (
        "sha256://worker-projection/"
        + payload_digest.removeprefix("sha256:")
    )
    return tuple(
        dict.fromkeys(
            (
                *(
                    str(value or "").strip()
                    for value in evidence_refs
                    if str(value or "").strip()
                ),
                receipt_ref,
                digest_ref,
            )
        )
    )


def _failure_evidence_refs(
    worker_commit: Mapping[str, Any],
    evidence_refs: Sequence[str],
) -> tuple[str, ...]:
    receipt_ref = (
        str(worker_commit.get("receipt_ref") or "").strip()
        if isinstance(worker_commit, Mapping)
        else ""
    )
    refs = tuple(
        dict.fromkeys(
            (
                *(
                    str(value or "").strip()
                    for value in evidence_refs
                    if str(value or "").strip()
                ),
                *((receipt_ref,) if receipt_ref else ()),
            )
        )
    )
    if refs:
        return refs
    return ("evidence://governed-worker/missing",)


def _check(
    check_id: str,
    reason_code: str | None = None,
) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "status": "PASS" if reason_code is None else "FAIL",
        "reason_code": reason_code,
    }


def _stable_id(
    prefix: str,
    *,
    command: Any,
    execution_context: Mapping[str, Any],
) -> str:
    subject = {
        "command_id": getattr(command, "command_id", None),
        "graph_id": execution_context.get("graph_id"),
        "graph_revision": execution_context.get("graph_revision"),
        "node_id": execution_context.get("node_id"),
        "attempt_id": execution_context.get("attempt_id"),
    }
    digest = hashlib.sha256(
        json.dumps(
            subject,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return f"{prefix}_{digest[:32]}"


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        _blocked(
            GOVERNED_WORKER_COMMIT_DRIFT,
            "worker COMMIT contains non-canonical JSON",
        )
        raise AssertionError("unreachable") from exc


def _required_context_text(
    execution_context: Mapping[str, Any],
    field_name: str,
) -> str:
    value = str(execution_context.get(field_name) or "").strip()
    if not value:
        _blocked(
            GOVERNED_WORKER_AUTHORITY_DRIFT,
            f"execution context has no {field_name}",
        )
    return value


def _positive_context_int(
    execution_context: Mapping[str, Any],
    field_name: str,
) -> int:
    value = execution_context.get(field_name)
    if type(value) is not int or value < 1:
        _blocked(
            GOVERNED_WORKER_AUTHORITY_DRIFT,
            f"execution context has invalid {field_name}",
        )
    return value


def _non_negative_timestamp(command: Any) -> int:
    value = getattr(command, "created_at_ms", 0)
    return value if type(value) is int and value >= 0 else 0


def _blocked(reason_code: str, detail: str) -> None:
    raise GovernedWorkerEvidenceError(reason_code, detail)


__all__ = [
    "DurableWorkerCommitAcceptance",
    "DurableWorkerCommitVerifier",
    "GOVERNED_ACCEPTANCE_CRITERION_UNPROVEN",
    "GOVERNED_WORKER_AUTHORITY_DRIFT",
    "GOVERNED_WORKER_COMMIT_DRIFT",
    "GOVERNED_WORKER_OUTPUT_CONTRACT_INVALID",
    "GOVERNED_WORKER_PROJECTION_INVALID",
    "GOVERNED_WORKER_PROJECTION_NOT_FOUND",
    "GOVERNED_WORKER_PROJECTION_UNAVAILABLE",
    "GOVERNED_WORKER_VERIFICATION_POLICY_INVALID",
    "GOVERNED_WORKER_VERIFICATION_RESULT_INVALID",
    "GovernedWorkerEvidenceError",
]
