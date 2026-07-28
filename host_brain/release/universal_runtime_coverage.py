"""Fail-closed coverage gate for the Universal Governed Runtime program.

The Core Infrastructure receipt proves that selected slices are internally
consistent.  This gate answers the different question that was previously
missing: whether every production execution route crosses every required
governance control.  A route with one missing control is incomplete.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from host_brain.release.universal_route_acceptance import (
    UniversalRouteAcceptanceError,
    validate_universal_route_acceptance_receipt,
)


COVERAGE_SCHEMA_VERSION = "natsume.universal-governed-runtime-coverage.v3"
PROGRAM_COMPLETE = "COMPLETE"
PROGRAM_INCOMPLETE = "INCOMPLETE"
CONTROL_PASS = "PASS"
CONTROL_MISSING = "MISSING"
CONTROL_PARTIAL = "PARTIAL"
CONTROL_IMPLEMENTED_NOT_WIRED = "IMPLEMENTED_NOT_WIRED"
_MIGRATION_HEAD_RE = re.compile(r"^\d{4}_.+\.sql$")

REQUIRED_ROUTES = (
    "chat",
    "skill",
    "plan",
    "delegate",
    "scheduler",
    "internal",
    "operator",
)
REQUIRED_ROUTE_CONTROLS = (
    "host_command",
    "immutable_task_graph",
    "canonical_execution_authority",
    "durable_attempt_lease",
    "node_bound_grant",
    "budget_enforcement",
    "review_resume",
    "verification",
    "acceptance",
    "receipt",
    "transactional_side_effects",
    "commit_bound_acceptance",
)
REQUIRED_ARCHITECTURE_CONTROLS = (
    "generic_three_layer_planning_search",
    "native_multi_node_task_graph",
    "visual_agent_direct_execution_authority",
    "revision_plus_one_replan",
    "single_durable_task_truth",
    "conversation_acceptance_outbox",
    "composition_without_global_service_locator",
)


class UniversalRuntimeCoverageError(RuntimeError):
    """Raised when coverage evidence is invalid or not complete."""


@dataclass(frozen=True, slots=True)
class UniversalRuntimeCoverageReport:
    status: str
    evaluated_commit_sha: str
    migration_head: str
    accepted_migration_head: str
    route_statuses: Mapping[str, str]
    missing_route_controls: Mapping[str, tuple[str, ...]]
    missing_architecture_controls: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return self.status == PROGRAM_COMPLETE

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": COVERAGE_SCHEMA_VERSION,
            "status": self.status,
            "evaluated_commit_sha": self.evaluated_commit_sha,
            "migration_head": self.migration_head,
            "accepted_migration_head": self.accepted_migration_head,
            "route_statuses": dict(self.route_statuses),
            "missing_route_controls": {
                route: list(controls)
                for route, controls in self.missing_route_controls.items()
            },
            "missing_architecture_controls": list(
                self.missing_architecture_controls
            ),
            "evidence_refs": list(self.evidence_refs),
        }


def default_coverage_manifest_path(root: str | Path) -> Path:
    return Path(root).resolve() / "configs/release/universal_governed_runtime_coverage.json"


def load_universal_runtime_coverage(
    root: str | Path,
    *,
    manifest_path: str | Path | None = None,
) -> UniversalRuntimeCoverageReport:
    repository_root = Path(root).resolve()
    path = (
        Path(manifest_path).resolve()
        if manifest_path is not None
        else default_coverage_manifest_path(repository_root)
    )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UniversalRuntimeCoverageError(
            f"universal runtime coverage manifest is unavailable: {path}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise UniversalRuntimeCoverageError("coverage manifest must be an object")
    if payload.get("schema_version") != COVERAGE_SCHEMA_VERSION:
        raise UniversalRuntimeCoverageError("coverage manifest schema is not supported")

    (
        evaluated_commit_sha,
        accepted_migration_head,
        catalog_migration_head,
    ) = _evidence_binding(
        payload,
        repository_root,
    )

    routes = _required_mapping(payload, "routes")
    if set(routes) != set(REQUIRED_ROUTES):
        raise UniversalRuntimeCoverageError(
            "coverage manifest must contain exactly: " + ", ".join(REQUIRED_ROUTES)
        )
    evidence_refs: set[str] = set()
    route_statuses: dict[str, str] = {}
    missing_by_route: dict[str, tuple[str, ...]] = {}
    for route in REQUIRED_ROUTES:
        record = _mapping_record(routes[route], subject=f"route {route}")
        controls = _required_mapping(record, "controls")
        if set(controls) != set(REQUIRED_ROUTE_CONTROLS):
            raise UniversalRuntimeCoverageError(
                f"route {route} does not declare the complete control set"
            )
        missing = tuple(
            control
            for control in REQUIRED_ROUTE_CONTROLS
            if _control_status(controls[control], subject=f"{route}.{control}")
            != CONTROL_PASS
        )
        derived = PROGRAM_INCOMPLETE if missing else PROGRAM_COMPLETE
        declared = str(record.get("status") or "").strip().upper()
        if declared != derived:
            raise UniversalRuntimeCoverageError(
                f"route {route} declares {declared or '<empty>'} but controls derive {derived}"
            )
        route_statuses[route] = derived
        if missing:
            missing_by_route[route] = missing
        if (
            _control_status(
                controls["commit_bound_acceptance"],
                subject=f"{route}.commit_bound_acceptance",
            )
            == CONTROL_PASS
        ):
            evidence_refs.add(
                _validate_route_acceptance_binding(
                    record,
                    repository_root,
                    route=route,
                    evaluated_commit_sha=evaluated_commit_sha,
                    accepted_migration_head=accepted_migration_head,
                )
            )
        evidence_refs.update(
            _evidence_refs(record, repository_root, subject=f"route {route}")
        )

    architecture = _required_mapping(payload, "architecture_controls")
    if set(architecture) != set(REQUIRED_ARCHITECTURE_CONTROLS):
        raise UniversalRuntimeCoverageError(
            "coverage manifest does not declare the complete architecture control set"
        )
    missing_architecture: list[str] = []
    for control in REQUIRED_ARCHITECTURE_CONTROLS:
        record = _mapping_record(
            architecture[control], subject=f"architecture control {control}"
        )
        if _control_status(record.get("status"), subject=control) != CONTROL_PASS:
            missing_architecture.append(control)
        evidence_refs.update(
            _evidence_refs(record, repository_root, subject=f"architecture {control}")
        )

    status = (
        PROGRAM_COMPLETE
        if not missing_by_route and not missing_architecture
        else PROGRAM_INCOMPLETE
    )
    declared_program_status = str(
        payload.get("declared_program_status") or ""
    ).strip().upper()
    if declared_program_status != status:
        raise UniversalRuntimeCoverageError(
            "declared program status differs from route and architecture evidence"
        )
    return UniversalRuntimeCoverageReport(
        status=status,
        evaluated_commit_sha=evaluated_commit_sha,
        migration_head=catalog_migration_head,
        accepted_migration_head=accepted_migration_head,
        route_statuses=route_statuses,
        missing_route_controls=missing_by_route,
        missing_architecture_controls=tuple(missing_architecture),
        evidence_refs=tuple(sorted(evidence_refs)),
    )


def require_full_architecture_ready(root: str | Path) -> UniversalRuntimeCoverageReport:
    report = load_universal_runtime_coverage(root)
    if not report.complete:
        missing_routes = ", ".join(sorted(report.missing_route_controls)) or "none"
        missing_architecture = ", ".join(report.missing_architecture_controls) or "none"
        raise UniversalRuntimeCoverageError(
            "FULL_ARCHITECTURE_INCOMPLETE: "
            f"routes={missing_routes}; architecture={missing_architecture}"
        )
    return report


def _required_mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    selected = value.get(key)
    if not isinstance(selected, Mapping):
        raise UniversalRuntimeCoverageError(f"coverage field {key} must be an object")
    return selected


def _mapping_record(value: Any, *, subject: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise UniversalRuntimeCoverageError(f"{subject} must be an object")
    return value


def _control_status(value: Any, *, subject: str) -> str:
    status = str(value or "").strip().upper()
    if status not in {
        CONTROL_PASS,
        CONTROL_MISSING,
        CONTROL_PARTIAL,
        CONTROL_IMPLEMENTED_NOT_WIRED,
    }:
        raise UniversalRuntimeCoverageError(
            f"{subject} must be explicitly PASS, PARTIAL, "
            "IMPLEMENTED_NOT_WIRED, or MISSING"
        )
    return status


def _evidence_refs(
    record: Mapping[str, Any],
    root: Path,
    *,
    subject: str,
) -> tuple[str, ...]:
    refs = record.get("evidence_refs")
    if not isinstance(refs, list) or not refs:
        raise UniversalRuntimeCoverageError(f"{subject} requires evidence_refs")
    normalized: list[str] = []
    for raw in refs:
        ref = str(raw or "").strip().replace("\\", "/")
        if not ref or ref.startswith("/") or ".." in Path(ref).parts:
            raise UniversalRuntimeCoverageError(f"{subject} has an invalid evidence ref")
        if not (root / ref).is_file():
            raise UniversalRuntimeCoverageError(
                f"{subject} evidence ref does not exist: {ref}"
            )
        normalized.append(ref)
    return tuple(normalized)


def _evidence_binding(
    payload: Mapping[str, Any],
    root: Path,
) -> tuple[str, str, str]:
    binding = _required_mapping(payload, "evidence_binding")
    if set(binding) != {
        "evaluated_commit_sha",
        "accepted_migration_head",
        "catalog_migration_head",
    }:
        raise UniversalRuntimeCoverageError(
            "evidence_binding must contain evaluated_commit_sha, "
            "accepted_migration_head and catalog_migration_head"
        )

    evaluated_commit_sha = str(binding.get("evaluated_commit_sha") or "").strip()
    if re.fullmatch(r"[0-9a-f]{40}", evaluated_commit_sha) is None:
        raise UniversalRuntimeCoverageError(
            "evidence_binding.evaluated_commit_sha must be a full lowercase Git SHA"
        )
    _require_git_ancestor(root, evaluated_commit_sha)

    accepted_migration_head = str(
        binding.get("accepted_migration_head") or ""
    ).strip()
    if _MIGRATION_HEAD_RE.fullmatch(accepted_migration_head) is None:
        raise UniversalRuntimeCoverageError(
            "evidence_binding.accepted_migration_head must be a numbered SQL migration"
        )
    catalog_migration_head = str(
        binding.get("catalog_migration_head") or ""
    ).strip()
    actual_migration_head = _current_migration_head(root)
    if catalog_migration_head != actual_migration_head:
        raise UniversalRuntimeCoverageError(
            "evidence_binding.catalog_migration_head differs from repository "
            "migration head: "
            f"declared={catalog_migration_head or '<empty>'}; "
            f"actual={actual_migration_head}"
        )
    return evaluated_commit_sha, accepted_migration_head, catalog_migration_head


def _require_git_ancestor(root: Path, commit_sha: str) -> None:
    try:
        object_check = subprocess.run(
            ["git", "-C", str(root), "cat-file", "-e", f"{commit_sha}^{{commit}}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        ancestor_check = subprocess.run(
            ["git", "-C", str(root), "merge-base", "--is-ancestor", commit_sha, "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UniversalRuntimeCoverageError(
            "Git is required to validate the coverage evidence commit"
        ) from exc
    if object_check.returncode != 0:
        raise UniversalRuntimeCoverageError(
            "coverage evidence commit is not available in this repository"
        )
    if ancestor_check.returncode != 0:
        raise UniversalRuntimeCoverageError(
            "coverage evidence commit is not an ancestor of the current HEAD"
        )


def _current_migration_head(root: Path) -> str:
    migration_root = root / "host_brain" / "audit" / "migrations"
    candidates: list[tuple[int, str]] = []
    for path in migration_root.glob("*.sql"):
        matched = re.fullmatch(r"(?P<number>\d{4})_.+\.sql", path.name)
        if matched is not None:
            candidates.append((int(matched.group("number")), path.name))
    if not candidates:
        raise UniversalRuntimeCoverageError(
            "repository migration catalog has no numbered SQL migrations"
        )
    highest_number = max(number for number, _ in candidates)
    heads = sorted(name for number, name in candidates if number == highest_number)
    if len(heads) != 1:
        raise UniversalRuntimeCoverageError(
            f"repository migration catalog has duplicate head {highest_number:04d}"
        )
    return heads[0]


def _validate_route_acceptance_binding(
    record: Mapping[str, Any],
    root: Path,
    *,
    route: str,
    evaluated_commit_sha: str,
    accepted_migration_head: str,
) -> str:
    binding = record.get("acceptance_binding")
    if not isinstance(binding, Mapping):
        raise UniversalRuntimeCoverageError(
            f"route {route} cannot PASS commit_bound_acceptance without acceptance_binding"
        )
    if set(binding) != {"receipt_ref"}:
        raise UniversalRuntimeCoverageError(
            f"route {route} acceptance_binding must contain exactly receipt_ref"
        )
    receipt_ref = str(binding.get("receipt_ref") or "").strip().replace("\\", "/")
    if not receipt_ref or receipt_ref.startswith("/") or ".." in Path(receipt_ref).parts:
        raise UniversalRuntimeCoverageError(
            f"route {route} acceptance_binding has an invalid receipt_ref"
        )
    receipt_path = root / receipt_ref
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UniversalRuntimeCoverageError(
            f"route {route} acceptance receipt is unavailable: {receipt_ref}"
        ) from exc
    try:
        receipt = validate_universal_route_acceptance_receipt(receipt)
    except UniversalRouteAcceptanceError as exc:
        raise UniversalRuntimeCoverageError(
            f"route {route} acceptance receipt is invalid: {exc.reason_code}"
        ) from exc
    if str(receipt.get("commit_sha") or "").strip() != evaluated_commit_sha:
        raise UniversalRuntimeCoverageError(
            f"route {route} acceptance receipt commit SHA does not match evidence_binding"
        )
    if (
        str(receipt.get("migration_head") or "").strip()
        != accepted_migration_head
    ):
        raise UniversalRuntimeCoverageError(
            f"route {route} acceptance receipt migration head does not match evidence_binding"
        )
    route_acceptance = receipt.get("route_acceptance")
    route_record = (
        route_acceptance.get(route)
        if isinstance(route_acceptance, Mapping)
        else None
    )
    if not isinstance(route_record, Mapping) or (
        str(route_record.get("status") or "").strip().upper() != CONTROL_PASS
    ):
        raise UniversalRuntimeCoverageError(
            f"route {route} acceptance receipt does not contain a PASS route result"
        )
    return receipt_ref


__all__ = [
    "COVERAGE_SCHEMA_VERSION",
    "CONTROL_IMPLEMENTED_NOT_WIRED",
    "CONTROL_PARTIAL",
    "PROGRAM_COMPLETE",
    "PROGRAM_INCOMPLETE",
    "REQUIRED_ARCHITECTURE_CONTROLS",
    "REQUIRED_ROUTE_CONTROLS",
    "REQUIRED_ROUTES",
    "UniversalRuntimeCoverageError",
    "UniversalRuntimeCoverageReport",
    "load_universal_runtime_coverage",
    "require_full_architecture_ready",
]
