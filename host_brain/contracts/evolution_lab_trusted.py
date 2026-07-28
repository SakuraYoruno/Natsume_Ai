"""Trusted Evolution Lab contracts layered above the structural 0122 V2 ledger.

The historical V1 and structural V2 receipts remain readable and permanently
ineligible for promotion.  These contracts require evidence produced by a
concrete isolated runtime and still only authorize a review-required proposal;
they never activate production state.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Mapping

from host_brain.contracts.evolution_lab import (
    CounterfactualPairV2,
    EffectAuditReceiptV1,
    ExperimentSpecV2,
    VerificationReceiptV1,
    canonical_evolution_lab_digest,
)


_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_PINNED_IMAGE = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")
_CONTAINER_ID = re.compile(r"^[0-9a-f]{12,64}$")
_GOLDEN_KINDS = (
    "memory_write",
    "proactive",
    "research_planning",
)


class TrustedEvolutionLabContractError(ValueError):
    """Raised when trusted Lab evidence is incomplete or self-contradictory."""


def _text(value: Any, field: str, maximum: int = 240) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > maximum:
        raise TrustedEvolutionLabContractError(
            f"{field} must be non-empty and at most {maximum} characters"
        )
    return normalized


def _digest(value: Any, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if _HEX64.fullmatch(normalized) is None:
        raise TrustedEvolutionLabContractError(
            f"{field} must be a lowercase SHA-256 digest"
        )
    return normalized


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TrustedEvolutionLabContractError(
            f"{field} must be a non-negative integer"
        )
    return value


def _positive_int(value: Any, field: str) -> int:
    normalized = _nonnegative_int(value, field)
    if normalized <= 0:
        raise TrustedEvolutionLabContractError(f"{field} must be positive")
    return normalized


def _json_copy(value: Any, field: str) -> Any:
    try:
        return json.loads(
            json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
        )
    except (TypeError, ValueError) as exc:
        raise TrustedEvolutionLabContractError(f"{field} must be JSON-safe") from exc


def _integer_mapping(value: Mapping[str, int], field: str) -> dict[str, int]:
    if not isinstance(value, Mapping):
        raise TrustedEvolutionLabContractError(f"{field} must be a mapping")
    normalized: dict[str, int] = {}
    for raw_key, raw_value in value.items():
        key = _text(raw_key, f"{field}.key", 120)
        normalized[key] = _nonnegative_int(raw_value, f"{field}.{key}")
    return dict(sorted(normalized.items()))


def docker_effect_audit_evidence_digest(
    *,
    execution_id: str,
    command: tuple[str, ...],
    inspect_evidence: Mapping[str, Any],
    sandbox_profile_digest: str,
    dataset_digest: str,
    output_digest: str,
) -> str:
    """Bind a zero-effect audit to the independently reopened Docker facts."""

    return canonical_evolution_lab_digest(
        {
            "schema": "natsume.evolution-lab.docker-effect-audit-evidence.v1",
            "execution_id": _text(execution_id, "execution_id", 160),
            "command_digest": canonical_evolution_lab_digest(list(command)),
            "inspect_digest": canonical_evolution_lab_digest(
                _json_copy(inspect_evidence, "inspect_evidence")
            ),
            "sandbox_profile_digest": _digest(
                sandbox_profile_digest, "sandbox_profile_digest"
            ),
            "dataset_digest": _digest(dataset_digest, "dataset_digest"),
            "output_digest": _digest(output_digest, "output_digest"),
            "effect_count": 0,
            "production_side_effects": False,
        }
    )


@dataclass(frozen=True, slots=True)
class RedactionReceiptV1:
    """Privacy-bounded binding from a real trace digest to a Lab dataset."""

    redaction_id: str
    workspace_id: str
    source_digest: str
    rules_version: str
    rules_digest: str
    output_dataset_digest: str
    record_count: int
    removed_field_count: int
    created_at_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "redaction_id", _text(self.redaction_id, "redaction_id", 160)
        )
        object.__setattr__(
            self, "workspace_id", _text(self.workspace_id, "workspace_id", 160)
        )
        object.__setattr__(
            self, "source_digest", _digest(self.source_digest, "source_digest")
        )
        object.__setattr__(
            self, "rules_version", _text(self.rules_version, "rules_version", 160)
        )
        object.__setattr__(
            self, "rules_digest", _digest(self.rules_digest, "rules_digest")
        )
        object.__setattr__(
            self,
            "output_dataset_digest",
            _digest(self.output_dataset_digest, "output_dataset_digest"),
        )
        object.__setattr__(
            self, "record_count", _positive_int(self.record_count, "record_count")
        )
        object.__setattr__(
            self,
            "removed_field_count",
            _nonnegative_int(self.removed_field_count, "removed_field_count"),
        )
        object.__setattr__(
            self, "created_at_ms", _nonnegative_int(self.created_at_ms, "created_at_ms")
        )

    @property
    def receipt_digest(self) -> str:
        return canonical_evolution_lab_digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.evolution-lab.redaction-receipt.v1",
            "redaction_id": self.redaction_id,
            "workspace_id": self.workspace_id,
            "source_digest": self.source_digest,
            "rules_version": self.rules_version,
            "rules_digest": self.rules_digest,
            "output_dataset_digest": self.output_dataset_digest,
            "record_count": self.record_count,
            "removed_field_count": self.removed_field_count,
            "created_at_ms": self.created_at_ms,
        }


@dataclass(frozen=True, slots=True)
class DockerSandboxAttestationV1:
    """Evidence reopened from a concrete Docker container and inspect payload."""

    execution_id: str
    engine_identity: str
    image_reference: str
    image_id: str
    container_id: str
    container_name: str
    command: tuple[str, ...]
    inspect_evidence: Mapping[str, Any]
    sandbox_profile_digest: str
    runtime_source_digest: str
    output_source_digest: str
    dataset_digest: str
    output_digest: str
    output_mount_type: str
    network_mode: str
    readonly_rootfs: bool
    readonly_dataset: bool
    readonly_runtime: bool
    temporary_output: bool
    no_new_privileges: bool
    cap_drop_all: bool
    non_root_user: str
    pids_limit: int
    memory_limit_bytes: int
    nano_cpus: int
    exit_code: int
    started_at_ms: int
    finished_at_ms: int
    effect_audit: EffectAuditReceiptV1

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "execution_id", _text(self.execution_id, "execution_id", 160)
        )
        object.__setattr__(
            self, "engine_identity", _text(self.engine_identity, "engine_identity", 240)
        )
        image_reference = _text(
            self.image_reference, "image_reference", 320
        ).lower()
        if _PINNED_IMAGE.fullmatch(image_reference) is None:
            raise TrustedEvolutionLabContractError(
                "image_reference must be pinned by sha256 digest"
            )
        object.__setattr__(self, "image_reference", image_reference)
        image_id = str(self.image_id or "").strip().lower()
        if image_id.startswith("sha256:"):
            image_id = image_id.removeprefix("sha256:")
        object.__setattr__(self, "image_id", _digest(image_id, "image_id"))
        container_id = str(self.container_id or "").strip().lower()
        if _CONTAINER_ID.fullmatch(container_id) is None:
            raise TrustedEvolutionLabContractError(
                "container_id must be a lowercase Docker container identity"
            )
        object.__setattr__(self, "container_id", container_id)
        object.__setattr__(
            self, "container_name", _text(self.container_name, "container_name", 160)
        )
        command = tuple(
            _text(value, "command[]", 1_024) for value in tuple(self.command)
        )
        if not command:
            raise TrustedEvolutionLabContractError(
                "trusted Lab container command cannot be empty"
            )
        object.__setattr__(self, "command", command)
        inspect_evidence = _json_copy(
            self.inspect_evidence, "inspect_evidence"
        )
        if not isinstance(inspect_evidence, dict):
            raise TrustedEvolutionLabContractError(
                "inspect_evidence must be a JSON object"
            )
        object.__setattr__(self, "inspect_evidence", inspect_evidence)
        for field in (
            "sandbox_profile_digest",
            "runtime_source_digest",
            "output_source_digest",
            "dataset_digest",
            "output_digest",
        ):
            object.__setattr__(self, field, _digest(getattr(self, field), field))
        output_mount_type = _text(
            self.output_mount_type, "output_mount_type", 40
        ).lower()
        if output_mount_type != "bind":
            raise TrustedEvolutionLabContractError(
                "trusted Lab output must use an isolated bind mount"
            )
        object.__setattr__(self, "output_mount_type", "bind")
        network_mode = _text(self.network_mode, "network_mode", 40).lower()
        if network_mode != "none":
            raise TrustedEvolutionLabContractError(
                "trusted Lab container network mode must be none"
            )
        object.__setattr__(self, "network_mode", "none")
        for field in (
            "readonly_rootfs",
            "readonly_dataset",
            "readonly_runtime",
            "temporary_output",
            "no_new_privileges",
            "cap_drop_all",
        ):
            if getattr(self, field) is not True:
                raise TrustedEvolutionLabContractError(
                    f"trusted Lab container requires {field}=true"
                )
            object.__setattr__(self, field, True)
        non_root_user = _text(
            self.non_root_user, "non_root_user", 80
        )
        if non_root_user in {"0", "0:0", "root", "root:root"}:
            raise TrustedEvolutionLabContractError(
                "trusted Lab container must use a non-root user"
            )
        object.__setattr__(self, "non_root_user", non_root_user)
        object.__setattr__(
            self, "pids_limit", _positive_int(self.pids_limit, "pids_limit")
        )
        object.__setattr__(
            self,
            "memory_limit_bytes",
            _positive_int(self.memory_limit_bytes, "memory_limit_bytes"),
        )
        object.__setattr__(
            self, "nano_cpus", _positive_int(self.nano_cpus, "nano_cpus")
        )
        if isinstance(self.exit_code, bool) or self.exit_code != 0:
            raise TrustedEvolutionLabContractError(
                "trusted Lab container must exit successfully"
            )
        object.__setattr__(self, "exit_code", 0)
        object.__setattr__(
            self,
            "started_at_ms",
            _nonnegative_int(self.started_at_ms, "started_at_ms"),
        )
        object.__setattr__(
            self,
            "finished_at_ms",
            _nonnegative_int(self.finished_at_ms, "finished_at_ms"),
        )
        if self.finished_at_ms < self.started_at_ms:
            raise TrustedEvolutionLabContractError(
                "finished_at_ms cannot precede started_at_ms"
            )
        if not isinstance(self.effect_audit, EffectAuditReceiptV1):
            raise TrustedEvolutionLabContractError(
                "trusted Lab container requires EffectAuditReceiptV1"
            )
        if self.effect_audit.execution_id != self.execution_id:
            raise TrustedEvolutionLabContractError(
                "effect audit execution identity drift"
            )
        expected_inspect = {
            "cap_drop": ["ALL"],
            "command": list(self.command),
            "container_id": self.container_id,
            "dataset_mount_readonly": True,
            "exit_code": 0,
            "image_id": self.image_id,
            "image_reference": self.image_reference,
            "memory_limit_bytes": self.memory_limit_bytes,
            "name": self.container_name,
            "nano_cpus": self.nano_cpus,
            "network_mode": "none",
            "no_new_privileges": True,
            "output_mount_temporary": True,
            "output_mount_type": "bind",
            "output_source_digest": self.output_source_digest,
            "pids_limit": self.pids_limit,
            "readonly_rootfs": True,
            "runtime_mount_readonly": True,
            "runtime_source_digest": self.runtime_source_digest,
            "user": self.non_root_user,
        }
        if self.inspect_evidence != expected_inspect:
            raise TrustedEvolutionLabContractError(
                "Docker inspect evidence does not match sandbox facts"
            )
        if (
            self.effect_audit.audit_evidence_digest
            != self.expected_effect_audit_digest
        ):
            raise TrustedEvolutionLabContractError(
                "effect audit evidence does not bind Docker inspect facts"
            )

    @property
    def trusted(self) -> bool:
        return True

    @property
    def command_digest(self) -> str:
        return canonical_evolution_lab_digest(list(self.command))

    @property
    def inspect_digest(self) -> str:
        return canonical_evolution_lab_digest(self.inspect_evidence)

    @property
    def expected_effect_audit_digest(self) -> str:
        return docker_effect_audit_evidence_digest(
            execution_id=self.execution_id,
            command=self.command,
            inspect_evidence=self.inspect_evidence,
            sandbox_profile_digest=self.sandbox_profile_digest,
            dataset_digest=self.dataset_digest,
            output_digest=self.output_digest,
        )

    @property
    def receipt_digest(self) -> str:
        return canonical_evolution_lab_digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.evolution-lab.docker-sandbox-attestation.v1",
            "execution_id": self.execution_id,
            "engine_identity": self.engine_identity,
            "image_reference": self.image_reference,
            "image_id": self.image_id,
            "container_id": self.container_id,
            "container_name": self.container_name,
            "command": list(self.command),
            "command_digest": self.command_digest,
            "inspect_evidence": _json_copy(
                self.inspect_evidence, "inspect_evidence"
            ),
            "inspect_digest": self.inspect_digest,
            "sandbox_profile_digest": self.sandbox_profile_digest,
            "runtime_source_digest": self.runtime_source_digest,
            "output_source_digest": self.output_source_digest,
            "dataset_digest": self.dataset_digest,
            "output_digest": self.output_digest,
            "output_mount_type": "bind",
            "network_mode": "none",
            "readonly_rootfs": True,
            "readonly_dataset": True,
            "readonly_runtime": True,
            "temporary_output": True,
            "no_new_privileges": True,
            "cap_drop_all": True,
            "non_root_user": self.non_root_user,
            "pids_limit": self.pids_limit,
            "memory_limit_bytes": self.memory_limit_bytes,
            "nano_cpus": self.nano_cpus,
            "exit_code": 0,
            "started_at_ms": self.started_at_ms,
            "finished_at_ms": self.finished_at_ms,
            "effect_audit": self.effect_audit.to_dict(),
            "effect_audit_receipt_digest": self.effect_audit.receipt_digest,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DockerSandboxAttestationV1":
        effect_raw = payload.get("effect_audit")
        if not isinstance(effect_raw, Mapping):
            raise TrustedEvolutionLabContractError(
                "sandbox attestation effect_audit is missing"
            )
        effect = EffectAuditReceiptV1(
            execution_id=effect_raw.get("execution_id"),
            audit_id=effect_raw.get("audit_id"),
            auditor_identity=effect_raw.get("auditor_identity"),
            audit_evidence_digest=effect_raw.get("audit_evidence_digest"),
            effect_count=effect_raw.get("effect_count"),
            denied_effect_count=effect_raw.get("denied_effect_count", 0),
            production_side_effects=effect_raw.get(
                "production_side_effects", False
            ),
        )
        return cls(
            execution_id=payload.get("execution_id"),
            engine_identity=payload.get("engine_identity"),
            image_reference=payload.get("image_reference"),
            image_id=payload.get("image_id"),
            container_id=payload.get("container_id"),
            container_name=payload.get("container_name"),
            command=tuple(payload.get("command", ())),
            inspect_evidence=payload.get("inspect_evidence", {}),
            sandbox_profile_digest=payload.get("sandbox_profile_digest"),
            runtime_source_digest=payload.get("runtime_source_digest"),
            output_source_digest=payload.get("output_source_digest"),
            dataset_digest=payload.get("dataset_digest"),
            output_digest=payload.get("output_digest"),
            output_mount_type=payload.get("output_mount_type"),
            network_mode=payload.get("network_mode"),
            readonly_rootfs=payload.get("readonly_rootfs"),
            readonly_dataset=payload.get("readonly_dataset"),
            readonly_runtime=payload.get("readonly_runtime"),
            temporary_output=payload.get("temporary_output"),
            no_new_privileges=payload.get("no_new_privileges"),
            cap_drop_all=payload.get("cap_drop_all"),
            non_root_user=payload.get("non_root_user"),
            pids_limit=payload.get("pids_limit"),
            memory_limit_bytes=payload.get("memory_limit_bytes"),
            nano_cpus=payload.get("nano_cpus"),
            exit_code=payload.get("exit_code"),
            started_at_ms=payload.get("started_at_ms"),
            finished_at_ms=payload.get("finished_at_ms"),
            effect_audit=effect,
        )


@dataclass(frozen=True, slots=True)
class TrustedExperimentRunReceiptV2:
    """One promotion-eligible Lab arm; still not production activation authority."""

    run_id: str
    execution_id: str
    spec_digest: str
    workspace_id: str
    dataset_manifest_digest: str
    variant_id: str
    status: str
    model_identity: str
    usage: Mapping[str, int]
    cost: Mapping[str, int]
    latency_ms: int
    sandbox_attestation: DockerSandboxAttestationV1
    verification_receipt: VerificationReceiptV1
    production_side_effects: bool = False
    production_influence_enabled: bool = False

    def __post_init__(self) -> None:
        for field in (
            "run_id",
            "execution_id",
            "workspace_id",
            "variant_id",
            "model_identity",
        ):
            object.__setattr__(self, field, _text(getattr(self, field), field, 240))
        object.__setattr__(
            self, "spec_digest", _digest(self.spec_digest, "spec_digest")
        )
        object.__setattr__(
            self,
            "dataset_manifest_digest",
            _digest(self.dataset_manifest_digest, "dataset_manifest_digest"),
        )
        status = _text(self.status, "status", 48).upper()
        if status not in {"PASS", "FAIL", "INCOMPLETE"}:
            raise TrustedEvolutionLabContractError(
                "trusted run status is unsupported"
            )
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "usage", _integer_mapping(self.usage, "usage"))
        object.__setattr__(self, "cost", _integer_mapping(self.cost, "cost"))
        object.__setattr__(
            self, "latency_ms", _nonnegative_int(self.latency_ms, "latency_ms")
        )
        if not isinstance(
            self.sandbox_attestation, DockerSandboxAttestationV1
        ):
            raise TrustedEvolutionLabContractError(
                "trusted run requires DockerSandboxAttestationV1"
            )
        if not isinstance(self.verification_receipt, VerificationReceiptV1):
            raise TrustedEvolutionLabContractError(
                "trusted run requires VerificationReceiptV1"
            )
        if (
            self.sandbox_attestation.execution_id != self.execution_id
            or self.verification_receipt.execution_id != self.execution_id
        ):
            raise TrustedEvolutionLabContractError(
                "trusted run execution identity drift"
            )
        if self.verification_receipt.status != self.status:
            raise TrustedEvolutionLabContractError(
                "trusted run status must match verifier status"
            )
        if self.production_side_effects:
            raise TrustedEvolutionLabContractError(
                "trusted Lab run cannot report production side effects"
            )
        if self.production_influence_enabled:
            raise TrustedEvolutionLabContractError(
                "trusted Lab run cannot directly enable production influence"
            )
        object.__setattr__(self, "production_side_effects", False)
        object.__setattr__(self, "production_influence_enabled", False)

    @property
    def benefit_eligible(self) -> bool:
        return self.status == "PASS" and self.sandbox_attestation.trusted

    @property
    def evidence_digest(self) -> str:
        return canonical_evolution_lab_digest(
            {
                "sandbox_attestation_digest": (
                    self.sandbox_attestation.receipt_digest
                ),
                "verification_receipt_digest": (
                    self.verification_receipt.receipt_digest
                ),
            }
        )

    @property
    def receipt_digest(self) -> str:
        return canonical_evolution_lab_digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.evolution-lab.trusted-run-receipt.v2",
            "run_id": self.run_id,
            "execution_id": self.execution_id,
            "spec_digest": self.spec_digest,
            "workspace_id": self.workspace_id,
            "dataset_manifest_digest": self.dataset_manifest_digest,
            "variant_id": self.variant_id,
            "status": self.status,
            "model_identity": self.model_identity,
            "usage": _json_copy(self.usage, "usage"),
            "cost": _json_copy(self.cost, "cost"),
            "latency_ms": self.latency_ms,
            "sandbox_attestation": self.sandbox_attestation.to_dict(),
            "sandbox_attestation_digest": (
                self.sandbox_attestation.receipt_digest
            ),
            "verification_receipt": self.verification_receipt.to_dict(),
            "verification_receipt_digest": (
                self.verification_receipt.receipt_digest
            ),
            "evidence_digest": self.evidence_digest,
            "production_side_effects": False,
            "benefit_eligible": self.benefit_eligible,
            "production_influence_enabled": False,
        }

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "TrustedExperimentRunReceiptV2":
        sandbox_raw = payload.get("sandbox_attestation")
        verifier_raw = payload.get("verification_receipt")
        if not isinstance(sandbox_raw, Mapping) or not isinstance(
            verifier_raw, Mapping
        ):
            raise TrustedEvolutionLabContractError(
                "trusted run evidence is incomplete"
            )
        verifier = VerificationReceiptV1(
            execution_id=verifier_raw.get("execution_id"),
            verifier_identity=verifier_raw.get("verifier_identity"),
            verifier_version=verifier_raw.get("verifier_version"),
            verifier_digest=verifier_raw.get("verifier_digest"),
            status=verifier_raw.get("status"),
            evidence_digest=verifier_raw.get("evidence_digest"),
            outcome=verifier_raw.get("outcome", {}),
            risk=verifier_raw.get("risk", {}),
            metrics=verifier_raw.get("metrics", {}),
        )
        receipt = cls(
            run_id=payload.get("run_id"),
            execution_id=payload.get("execution_id"),
            spec_digest=payload.get("spec_digest"),
            workspace_id=payload.get("workspace_id"),
            dataset_manifest_digest=payload.get("dataset_manifest_digest"),
            variant_id=payload.get("variant_id"),
            status=payload.get("status"),
            model_identity=payload.get("model_identity"),
            usage=payload.get("usage", {}),
            cost=payload.get("cost", {}),
            latency_ms=payload.get("latency_ms"),
            sandbox_attestation=DockerSandboxAttestationV1.from_dict(
                sandbox_raw
            ),
            verification_receipt=verifier,
            production_side_effects=payload.get(
                "production_side_effects", False
            ),
            production_influence_enabled=payload.get(
                "production_influence_enabled", False
            ),
        )
        if payload.get("receipt_digest") not in {
            None,
            receipt.receipt_digest,
        }:
            raise TrustedEvolutionLabContractError(
                "trusted run receipt digest drift"
            )
        return receipt


@dataclass(frozen=True, slots=True)
class TrustedExperimentReportV2:
    """Counterfactual report whose only output authority is a proposal."""

    report_id: str
    experiment_kind: str
    spec: ExperimentSpecV2
    runs: tuple[TrustedExperimentRunReceiptV2, ...]
    comparisons: tuple[CounterfactualPairV2, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "report_id", _text(self.report_id, "report_id", 160)
        )
        object.__setattr__(
            self,
            "experiment_kind",
            _text(self.experiment_kind, "experiment_kind", 120).lower(),
        )
        if not isinstance(self.spec, ExperimentSpecV2):
            raise TrustedEvolutionLabContractError(
                "trusted report requires ExperimentSpecV2"
            )
        runs = tuple(self.runs)
        if len(runs) < 2 or not all(
            isinstance(item, TrustedExperimentRunReceiptV2) for item in runs
        ):
            raise TrustedEvolutionLabContractError(
                "trusted report requires at least two trusted runs"
            )
        if len({item.run_id for item in runs}) != len(runs):
            raise TrustedEvolutionLabContractError(
                "trusted report run IDs must be unique"
            )
        if any(
            item.spec_digest != self.spec.spec_digest
            or item.workspace_id != self.spec.workspace_id
            or item.dataset_manifest_digest
            != self.spec.dataset_manifest.manifest_digest
            for item in runs
        ):
            raise TrustedEvolutionLabContractError(
                "trusted report run/spec identity drift"
            )
        expected_variants = {item.variant_id for item in self.spec.variants}
        if (
            len(runs) != len(expected_variants)
            or {item.variant_id for item in runs} != expected_variants
        ):
            raise TrustedEvolutionLabContractError(
                "trusted report requires one run for every registered arm"
            )
        comparisons = tuple(self.comparisons)
        if not comparisons or not all(
            isinstance(item, CounterfactualPairV2) for item in comparisons
        ):
            raise TrustedEvolutionLabContractError(
                "trusted report comparisons are invalid"
            )
        run_ids = {item.run_id for item in runs}
        if any(
            item.baseline_run_id not in run_ids
            or item.alternative_run_id not in run_ids
            for item in comparisons
        ):
            raise TrustedEvolutionLabContractError(
                "trusted report comparison references an unknown run"
            )
        baseline_variant = next(
            item.variant_id for item in self.spec.variants if item.is_baseline
        )
        baseline_run = next(
            item.run_id for item in runs if item.variant_id == baseline_variant
        )
        alternatives = {
            item.run_id for item in runs if item.variant_id != baseline_variant
        }
        covered_alternatives = {
            item.alternative_run_id
            for item in comparisons
            if item.baseline_run_id == baseline_run
        }
        if covered_alternatives != alternatives:
            raise TrustedEvolutionLabContractError(
                "trusted report must compare the baseline with every alternative"
            )
        object.__setattr__(
            self, "runs", tuple(sorted(runs, key=lambda item: item.run_id))
        )
        object.__setattr__(
            self,
            "comparisons",
            tuple(
                sorted(
                    comparisons,
                    key=lambda item: (
                        item.baseline_run_id,
                        item.alternative_run_id,
                    ),
                )
            ),
        )

    @property
    def promotion_eligible(self) -> bool:
        return all(item.benefit_eligible for item in self.runs)

    @property
    def report_digest(self) -> str:
        return canonical_evolution_lab_digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.evolution-lab.trusted-report.v2",
            "report_id": self.report_id,
            "experiment_kind": self.experiment_kind,
            "spec": self.spec.to_dict(),
            "spec_digest": self.spec.spec_digest,
            "runs": [item.to_dict() for item in self.runs],
            "run_receipt_digests": [
                item.receipt_digest for item in self.runs
            ],
            "comparisons": [item.to_dict() for item in self.comparisons],
            "promotion_eligible": self.promotion_eligible,
            "review_required": True,
            "production_influence_enabled": False,
        }


@dataclass(frozen=True, slots=True)
class GoldenExperimentSuiteReceiptV1:
    """Commit-bound aggregate for the three mandatory NE-04 experiments."""

    suite_id: str
    commit_sha: str
    migration_head: str
    engine_identity: str
    image_reference: str
    report_digests: Mapping[str, str]
    run_receipt_digests: tuple[str, ...]
    created_at_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "suite_id", _text(self.suite_id, "suite_id", 160))
        commit_sha = str(self.commit_sha or "").strip().lower()
        if _COMMIT_SHA.fullmatch(commit_sha) is None:
            raise TrustedEvolutionLabContractError(
                "commit_sha must be a full lowercase Git SHA"
            )
        object.__setattr__(self, "commit_sha", commit_sha)
        object.__setattr__(
            self, "migration_head", _text(self.migration_head, "migration_head", 200)
        )
        object.__setattr__(
            self, "engine_identity", _text(self.engine_identity, "engine_identity", 240)
        )
        image_reference = _text(
            self.image_reference, "image_reference", 320
        ).lower()
        if _PINNED_IMAGE.fullmatch(image_reference) is None:
            raise TrustedEvolutionLabContractError(
                "golden suite image must be digest-pinned"
            )
        object.__setattr__(self, "image_reference", image_reference)
        if not isinstance(self.report_digests, Mapping) or set(
            self.report_digests
        ) != set(_GOLDEN_KINDS):
            raise TrustedEvolutionLabContractError(
                "golden suite must contain exactly three required reports"
            )
        object.__setattr__(
            self,
            "report_digests",
            {
                key: _digest(self.report_digests[key], f"report_digests.{key}")
                for key in _GOLDEN_KINDS
            },
        )
        runs = tuple(
            _digest(value, "run_receipt_digests")
            for value in self.run_receipt_digests
        )
        if len(runs) != 6 or len(set(runs)) != 6:
            raise TrustedEvolutionLabContractError(
                "golden suite must bind six unique arm receipts"
            )
        object.__setattr__(self, "run_receipt_digests", tuple(sorted(runs)))
        object.__setattr__(
            self, "created_at_ms", _nonnegative_int(self.created_at_ms, "created_at_ms")
        )

    @property
    def status(self) -> str:
        return "PASS"

    @property
    def receipt_digest(self) -> str:
        return canonical_evolution_lab_digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.evolution-lab.golden-suite-receipt.v1",
            "suite_id": self.suite_id,
            "commit_sha": self.commit_sha,
            "migration_head": self.migration_head,
            "engine_identity": self.engine_identity,
            "image_reference": self.image_reference,
            "report_digests": dict(self.report_digests),
            "run_receipt_digests": list(self.run_receipt_digests),
            "status": "PASS",
            "production_side_effects": False,
            "production_influence_enabled": False,
            "created_at_ms": self.created_at_ms,
        }


__all__ = [
    "DockerSandboxAttestationV1",
    "GoldenExperimentSuiteReceiptV1",
    "RedactionReceiptV1",
    "TrustedEvolutionLabContractError",
    "TrustedExperimentReportV2",
    "TrustedExperimentRunReceiptV2",
    "docker_effect_audit_evidence_digest",
]
