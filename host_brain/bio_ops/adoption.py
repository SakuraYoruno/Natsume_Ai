"""Owner-gated adoption queue for Bio-Ops candidates."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from host_brain.bio_ops.artifact_store import BioOpsArtifactStore
from host_brain.bio_ops.contracts import BioOpsTask, build_bio_ops_task_id
from host_brain.bio_ops.safety import BIO_OPS_OWNER_ORGANS, validate_bio_ops_task_safety
from host_brain.core.ids import now_ms


BIO_OPS_CANDIDATE_CAPABILITIES: dict[str, str] = {
    "habit_compiler": "social_brain_agent",
    "emotional_scar_memory": "immune_agent",
    "forgetting_designer": "glymphatic_agent",
    "autobiography_compiler": "hippocampus_agent",
    "immune_memory_builder": "immune_agent",
    "promise_ledger": "prefrontal_agent",
}

BIO_OPS_TRACE_ONLY_CAPABILITIES: frozenset[str] = frozenset(
    {
        "attention_budget_governor",
        "response_breathing_policy",
        "active_silence_system",
        "internal_weather_system",
        "reflex_arc_compiler",
    }
)

BIO_OPS_REPORT_ONLY_CAPABILITIES: frozenset[str] = frozenset(
    {
        "aesthetic_consistency_auditor",
        "body_map_system",
        "capability_self_awareness",
        "competence_humility",
        "consent_gradient",
        "context_gravity",
        "dream_rehearsal",
        "dignity_guard",
        "embodiment_director",
        "growth_stage_system",
        "identity_fork_guard",
        "inner_parliament",
        "inner_ethics_review",
        "life_pulse",
        "memory_ethics_auditor",
        "memory_texture",
        "mood_inertia",
        "organ_ct_scan",
        "organ_wiring_audit",
        "persona_antibody",
        "persona_drift_microscope",
        "attachment_style_calibrator",
        "relationship_friction",
        "relationship_boundary_guardian",
        "repair_curve",
        "self_explanation_layer",
        "self_model",
        "self_boundary_membrane",
        "silence_memory",
        "skill_microbiome",
        "social_contract",
        "symbolic_memory",
        "personal_mythology",
        "subconscious_bias_sampler",
        "self_interrupt",
        "subjective_time",
        "truthfulness_cortex",
        "uncertainty_ritual",
        "value_hierarchy",
        "shadow_persona_test",
        "idea_fermentation_pool",
        "inner_outer_world_boundary",
    }
)

ADOPTION_STATUS_PENDING_REVIEW = "PENDING_REVIEW"
ADOPTION_STATUS_BLOCKED = "BLOCKED"
ADOPTION_STATUS_NOT_APPLICABLE = "NOT_APPLICABLE"
ADOPTION_DECISION_ACCEPTED = "OWNER_ACCEPTED"
ADOPTION_DECISION_REJECTED = "OWNER_REJECTED"


@dataclass(slots=True)
class BioOpsAdoptionCandidate:
    candidate_id: str
    task_id: str
    capability: str
    owner_organ: str
    output_kind: str
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)
    evidence_refs: list[str] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    review_required: bool = True
    next_actions: list[str] = field(default_factory=list)
    status: str = ADOPTION_STATUS_PENDING_REVIEW
    reason_code: str | None = None
    detail: str | None = None
    created_at_ms: int = field(default_factory=now_ms)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BioOpsAdoptionDecision:
    candidate_id: str
    owner_organ: str
    decision: str
    reason: str = ""
    decided_at_ms: int = field(default_factory=now_ms)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def resolve_bio_ops_adoption_policy(capability: str) -> dict[str, Any]:
    normalized = str(capability or "").strip().lower()
    if normalized in BIO_OPS_CANDIDATE_CAPABILITIES:
        return {
            "capability": normalized,
            "mode": "owner_candidate",
            "owner_organ": BIO_OPS_CANDIDATE_CAPABILITIES[normalized],
            "direct_write_allowed": False,
            "review_required": True,
        }
    if normalized in BIO_OPS_TRACE_ONLY_CAPABILITIES:
        return {
            "capability": normalized,
            "mode": "trace_only",
            "owner_organ": None,
            "direct_write_allowed": False,
            "review_required": False,
        }
    return {
        "capability": normalized,
        "mode": "report_only",
        "owner_organ": None,
        "direct_write_allowed": False,
        "review_required": False,
    }


def _proposal_payload(result_payload: Mapping[str, Any]) -> dict[str, Any]:
    proposals = result_payload.get("proposals")
    if isinstance(proposals, list) and proposals:
        first = proposals[0]
        if isinstance(first, Mapping):
            return dict(first)
    return {}


def _list_text(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _build_blocked_candidate(
    *,
    task: BioOpsTask,
    result_payload: Mapping[str, Any],
    reason_code: str,
    detail: str | None = None,
) -> BioOpsAdoptionCandidate:
    return BioOpsAdoptionCandidate(
        candidate_id=build_bio_ops_task_id("adopt_blocked"),
        task_id=task.task_id,
        capability=task.capability,
        owner_organ=str(task.owner_organ or ""),
        output_kind=str(_proposal_payload(result_payload).get("kind") or ""),
        summary="BioOps adoption blocked before owner review.",
        payload={},
        evidence_refs=_list_text(result_payload.get("evidence_refs")),
        artifacts=list(result_payload.get("artifacts") or []),
        risk_flags=[*_list_text(result_payload.get("risk_flags")), "bio_ops_adoption_block"],
        review_required=True,
        next_actions=["Provide an owner organ, evidence, and non-sensitive target before adoption."],
        status=ADOPTION_STATUS_BLOCKED,
        reason_code=reason_code,
        detail=detail,
    )


def build_bio_ops_adoption_candidate(
    task: BioOpsTask,
    result_payload: Mapping[str, Any],
) -> BioOpsAdoptionCandidate | None:
    policy = resolve_bio_ops_adoption_policy(task.capability)
    if str(result_payload.get("status") or "") != "CANDIDATE":
        return None
    if policy["mode"] != "owner_candidate":
        return None

    allowed, reason_code, detail = validate_bio_ops_task_safety(task)
    if not allowed:
        return _build_blocked_candidate(
            task=task,
            result_payload=result_payload,
            reason_code=reason_code or "BIO_OPS_ADOPTION_POLICY_BLOCKED",
            detail=detail,
        )

    expected_owner = str(policy.get("owner_organ") or "").strip()
    owner = str(task.owner_organ or expected_owner).strip()
    if not owner:
        return _build_blocked_candidate(
            task=task,
            result_payload=result_payload,
            reason_code="BIO_OPS_OWNER_REQUIRED",
            detail="Candidate adoption requires an owner organ.",
        )
    if owner not in BIO_OPS_OWNER_ORGANS:
        return _build_blocked_candidate(
            task=task,
            result_payload=result_payload,
            reason_code="BIO_OPS_OWNER_UNKNOWN",
            detail=owner,
        )
    if expected_owner and owner != expected_owner:
        return _build_blocked_candidate(
            task=task,
            result_payload=result_payload,
            reason_code="BIO_OPS_OWNER_MISMATCH",
            detail=f"expected={expected_owner} actual={owner}",
        )

    evidence_refs = _list_text(result_payload.get("evidence_refs"))
    if not evidence_refs:
        return _build_blocked_candidate(
            task=task,
            result_payload=result_payload,
            reason_code="BIO_OPS_EVIDENCE_REQUIRED",
            detail="Candidate adoption requires evidence_refs.",
        )

    proposal = _proposal_payload(result_payload)
    return BioOpsAdoptionCandidate(
        candidate_id=build_bio_ops_task_id("adopt"),
        task_id=task.task_id,
        capability=task.capability,
        owner_organ=owner,
        output_kind=str(proposal.get("kind") or policy["mode"]),
        summary=str(result_payload.get("summary") or "BioOps candidate staged for owner review.").strip(),
        payload={
            "proposal": proposal,
            "findings": list(result_payload.get("findings") or []),
            "metadata": dict(result_payload.get("metadata") or {}),
        },
        evidence_refs=evidence_refs,
        artifacts=list(result_payload.get("artifacts") or []),
        risk_flags=_list_text(result_payload.get("risk_flags")),
        review_required=True,
        next_actions=[
            f"{owner} must explicitly accept or reject this candidate.",
            "Do not write long-term state until owner acceptance is recorded.",
        ],
    )


class BioOpsAdoptionQueue:
    def __init__(self, *, artifact_store: BioOpsArtifactStore | None = None) -> None:
        self.artifact_store = artifact_store or BioOpsArtifactStore()
        self._candidates: dict[str, BioOpsAdoptionCandidate] = {}
        self._decisions: dict[str, BioOpsAdoptionDecision] = {}

    def stage(
        self,
        *,
        task: BioOpsTask,
        result_payload: Mapping[str, Any],
        run_dir: Path,
    ) -> dict[str, Any]:
        policy = resolve_bio_ops_adoption_policy(task.capability)
        candidate = build_bio_ops_adoption_candidate(task, result_payload)
        if candidate is None:
            return {
                "status": ADOPTION_STATUS_NOT_APPLICABLE,
                "policy": policy,
                "artifact": None,
            }

        self._candidates[candidate.candidate_id] = candidate
        artifact = self.artifact_store.write_json(
            Path(run_dir) / "adoption_candidate.json",
            candidate.to_dict(),
            artifact_type="adoption_candidate",
        )
        return {
            "status": candidate.status,
            "policy": policy,
            "candidate": candidate.to_dict(),
            "artifact": artifact.to_dict(),
        }

    def record_owner_decision(
        self,
        *,
        candidate_id: str,
        owner_organ: str,
        decision: str,
        reason: str = "",
        run_dir: Path | None = None,
    ) -> dict[str, Any]:
        normalized_decision = str(decision or "").strip().upper()
        if normalized_decision not in {ADOPTION_DECISION_ACCEPTED, ADOPTION_DECISION_REJECTED}:
            raise ValueError("decision must be OWNER_ACCEPTED or OWNER_REJECTED")

        candidate = self._candidates.get(str(candidate_id or "").strip())
        if candidate is None:
            raise KeyError(f"unknown BioOps adoption candidate: {candidate_id}")
        if str(owner_organ or "").strip() != candidate.owner_organ:
            raise PermissionError("Only the candidate owner organ can record adoption decisions.")

        record = BioOpsAdoptionDecision(
            candidate_id=candidate.candidate_id,
            owner_organ=candidate.owner_organ,
            decision=normalized_decision,
            reason=str(reason or "").strip(),
        )
        self._decisions[candidate.candidate_id] = record
        payload = record.to_dict()
        if run_dir is not None:
            artifact = self.artifact_store.write_json(
                Path(run_dir) / "adoption_decision.json",
                payload,
                artifact_type="adoption_decision",
            )
            payload["artifact"] = artifact.to_dict()
        return payload

    def get_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        candidate = self._candidates.get(str(candidate_id or "").strip())
        return candidate.to_dict() if candidate else None


__all__ = [
    "ADOPTION_DECISION_ACCEPTED",
    "ADOPTION_DECISION_REJECTED",
    "ADOPTION_STATUS_BLOCKED",
    "ADOPTION_STATUS_NOT_APPLICABLE",
    "ADOPTION_STATUS_PENDING_REVIEW",
    "BIO_OPS_CANDIDATE_CAPABILITIES",
    "BIO_OPS_REPORT_ONLY_CAPABILITIES",
    "BIO_OPS_TRACE_ONLY_CAPABILITIES",
    "BioOpsAdoptionCandidate",
    "BioOpsAdoptionDecision",
    "BioOpsAdoptionQueue",
    "build_bio_ops_adoption_candidate",
    "resolve_bio_ops_adoption_policy",
]
