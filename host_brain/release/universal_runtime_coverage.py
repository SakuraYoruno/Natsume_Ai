"""Fail-closed coverage gate for the Universal Governed Runtime program.

The Core Infrastructure receipt proves that selected slices are internally
consistent.  This gate answers the different question that was previously
missing: whether every production execution route crosses every required
governance control.  A route with one missing control is incomplete.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping


COVERAGE_SCHEMA_VERSION = "natsume.universal-governed-runtime-coverage.v1"
PROGRAM_COMPLETE = "COMPLETE"
PROGRAM_INCOMPLETE = "INCOMPLETE"
CONTROL_PASS = "PASS"
CONTROL_MISSING = "MISSING"
CONTROL_IMPLEMENTED_NOT_WIRED = "IMPLEMENTED_NOT_WIRED"

REQUIRED_ROUTES = ("chat", "skill", "delegate", "plan")
REQUIRED_ROUTE_CONTROLS = (
    "host_command",
    "immutable_task_graph",
    "durable_attempt_lease",
    "node_bound_grant",
    "budget_enforcement",
    "review_resume",
    "verification",
    "acceptance",
    "receipt",
    "transactional_side_effects",
)
REQUIRED_ARCHITECTURE_CONTROLS = (
    "generic_three_layer_planning_search",
    "native_multi_node_task_graph",
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

    routes = _required_mapping(payload, "routes")
    if set(routes) != set(REQUIRED_ROUTES):
        raise UniversalRuntimeCoverageError(
            "coverage manifest must contain exactly chat, skill, delegate and plan"
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
        CONTROL_IMPLEMENTED_NOT_WIRED,
    }:
        raise UniversalRuntimeCoverageError(
            f"{subject} must be explicitly PASS, IMPLEMENTED_NOT_WIRED, or MISSING"
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


__all__ = [
    "COVERAGE_SCHEMA_VERSION",
    "CONTROL_IMPLEMENTED_NOT_WIRED",
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
