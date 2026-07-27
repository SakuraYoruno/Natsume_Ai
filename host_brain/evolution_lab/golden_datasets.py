"""Frozen synthetic datasets for the three mandatory NE-04 experiments."""

from __future__ import annotations

from typing import Final

from host_brain.contracts.evolution_lab import (
    DatasetManifestV1,
    ExperimentSpecV2,
    LabDatasetV1,
    PolicyVariantV1,
    canonical_evolution_lab_digest,
)


GOLDEN_EXPERIMENT_KINDS: Final[tuple[str, ...]] = (
    "research_planning",
    "proactive",
    "memory_write",
)

_RESEARCH_PLANNING_RECORDS: Final = (
    {
        "case_id": "research-local-conflict",
        "required_steps": [
            "open_local_sources",
            "compare_claims",
            "cite_spans",
            "state_conflicts",
        ],
        "forbidden_steps": ["invent_source"],
    },
    {
        "case_id": "research-web-freshness",
        "required_steps": [
            "open_web_sources",
            "check_publish_dates",
            "cross_validate",
            "cite_spans",
        ],
        "forbidden_steps": ["trust_search_snippet"],
    },
    {
        "case_id": "research-mixed-corpus",
        "required_steps": [
            "open_local_sources",
            "open_web_sources",
            "normalize_evidence",
            "resolve_conflicts",
            "cite_spans",
        ],
        "forbidden_steps": ["merge_without_provenance"],
    },
    {
        "case_id": "research-insufficient-evidence",
        "required_steps": [
            "open_local_sources",
            "identify_missing_evidence",
            "report_uncertainty",
        ],
        "forbidden_steps": ["claim_completion"],
    },
)

_PROACTIVE_RECORDS: Final = (
    {
        "case_id": "proactive-normal",
        "hour": 14,
        "user_annoyed": False,
        "game_mode": False,
        "maintenance": False,
        "expected_action": "brief_prompt",
    },
    {
        "case_id": "proactive-late-night",
        "hour": 23,
        "user_annoyed": False,
        "game_mode": False,
        "maintenance": False,
        "expected_action": "silence",
    },
    {
        "case_id": "proactive-annoyed",
        "hour": 16,
        "user_annoyed": True,
        "game_mode": False,
        "maintenance": False,
        "expected_action": "silence",
    },
    {
        "case_id": "proactive-game",
        "hour": 20,
        "user_annoyed": False,
        "game_mode": True,
        "maintenance": False,
        "expected_action": "silence",
    },
    {
        "case_id": "proactive-maintenance",
        "hour": 10,
        "user_annoyed": False,
        "game_mode": False,
        "maintenance": True,
        "expected_action": "maintenance_notice",
    },
)

_MEMORY_WRITE_RECORDS: Final = (
    {
        "case_id": "memory-project-fact",
        "sensitivity": "normal",
        "has_provenance": True,
        "user_consent": True,
        "expected_action": "write",
    },
    {
        "case_id": "memory-private-no-consent",
        "sensitivity": "private",
        "has_provenance": True,
        "user_consent": False,
        "expected_action": "reject",
    },
    {
        "case_id": "memory-missing-provenance",
        "sensitivity": "normal",
        "has_provenance": False,
        "user_consent": True,
        "expected_action": "reject",
    },
    {
        "case_id": "memory-private-consented",
        "sensitivity": "private",
        "has_provenance": True,
        "user_consent": True,
        "expected_action": "write",
    },
    {
        "case_id": "memory-secret",
        "sensitivity": "secret",
        "has_provenance": True,
        "user_consent": True,
        "expected_action": "reject",
    },
)

_RECORDS: Final = {
    "research_planning": _RESEARCH_PLANNING_RECORDS,
    "proactive": _PROACTIVE_RECORDS,
    "memory_write": _MEMORY_WRITE_RECORDS,
}


class GoldenDatasetError(ValueError):
    """A requested golden dataset/spec is unsupported or unbound."""


class BuiltInGoldenDatasetReader:
    """Reopen only the frozen, code-backed synthetic NE-04 datasets."""

    def read(self, *, manifest: DatasetManifestV1) -> LabDatasetV1:
        if not isinstance(manifest, DatasetManifestV1):
            raise GoldenDatasetError("golden dataset manifest is invalid")
        prefix = "ne04-golden-"
        suffix = "-v1"
        if (
            not manifest.dataset_id.startswith(prefix)
            or not manifest.dataset_id.endswith(suffix)
        ):
            raise GoldenDatasetError("golden dataset identity is unsupported")
        kind = manifest.dataset_id[len(prefix) : -len(suffix)]
        dataset = build_golden_dataset(kind)
        if (
            manifest.source_kind != "synthetic"
            or manifest.redaction_receipt_digest is not None
            or dataset.dataset_id != manifest.dataset_id
            or dataset.dataset_digest != manifest.dataset_digest
            or len(dataset.records) != manifest.record_count
        ):
            raise GoldenDatasetError(
                "golden dataset drifts from the frozen manifest"
            )
        return dataset


def build_golden_dataset(experiment_kind: str) -> LabDatasetV1:
    kind = _kind(experiment_kind)
    return LabDatasetV1(
        dataset_id=f"ne04-golden-{kind}-v1",
        records=tuple(_RECORDS[kind]),
    )


def build_golden_spec(
    *,
    experiment_kind: str,
    workspace_id: str,
    code_version: str,
    isolation_profile_digest: str,
    verifier_digest: str,
) -> ExperimentSpecV2:
    kind = _kind(experiment_kind)
    dataset = build_golden_dataset(kind)
    code_identity = canonical_evolution_lab_digest(
        {"code_version": str(code_version)}
    )[:12]
    manifest = DatasetManifestV1(
        workspace_id=workspace_id,
        dataset_id=dataset.dataset_id,
        dataset_digest=dataset.dataset_digest,
        schema_digest=canonical_evolution_lab_digest(
            {
                "schema": "natsume.evolution-lab.golden-dataset-schema.v1",
                "experiment_kind": kind,
                "record_keys": sorted(
                    {
                        key
                        for record in dataset.records
                        for key in record
                    }
                ),
            }
        ),
        record_count=len(dataset.records),
        source_kind="synthetic",
    )
    return ExperimentSpecV2(
        experiment_id=f"ne04-golden-{kind}-v1-{code_identity}",
        workspace_id=workspace_id,
        dataset_manifest=manifest,
        seed=20_260_728,
        budget={
            "max_runs": 2,
            "max_wall_time_ms": 60_000,
            "memory_limit_bytes": 268_435_456,
            "pids_limit": 64,
        },
        variants=(
            _variant(kind, "baseline", code_version, is_baseline=True),
            _variant(kind, "candidate", code_version, is_baseline=False),
        ),
        verifier_identity=f"verifier://natsume/ne04/{kind}",
        verifier_version="1.0.0",
        verifier_digest=verifier_digest,
        isolation_profile_digest=isolation_profile_digest,
    )


def _variant(
    kind: str,
    variant_id: str,
    code_version: str,
    *,
    is_baseline: bool,
) -> PolicyVariantV1:
    return PolicyVariantV1(
        variant_id=variant_id,
        policy_version=f"ne04.{kind}.{variant_id}.v1",
        code_version=code_version,
        config_digest=canonical_evolution_lab_digest(
            {
                "schema": "natsume.evolution-lab.golden-policy.v1",
                "experiment_kind": kind,
                "variant_id": variant_id,
            }
        ),
        is_baseline=is_baseline,
    )


def _kind(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in GOLDEN_EXPERIMENT_KINDS:
        raise GoldenDatasetError("unsupported NE-04 golden experiment kind")
    return normalized


__all__ = [
    "BuiltInGoldenDatasetReader",
    "GOLDEN_EXPERIMENT_KINDS",
    "GoldenDatasetError",
    "build_golden_dataset",
    "build_golden_spec",
]
