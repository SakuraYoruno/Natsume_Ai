"""Fail-closed evaluator for the M48-B Entry Gate packet.

This module validates preparation evidence and can report that the gate is
open only when an external MASTER approval reference is present.  It does not
implement any TASK-0487+ production behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


M48B_ENTRY_GATE_PACKET_SCHEMA_VERSION = "natsume.m48b.entry-gate-packet.v1"
M48B_ENTRY_GATE_RESULT_SCHEMA_VERSION = "natsume.m48b.entry-gate-result.v1"

EXPECTED_DOMAINS = frozenset(
    {"gateway", "obs", "pentest", "skill_provider", "bridge"}
)
EXPECTED_P0_TASKS = frozenset(f"TASK-{task:04d}" for task in range(487, 496))
EXPECTED_MIGRATION_OWNERS = {
    "0058_secure_ingress.sql": "TASK-0487",
    "0059_gateway_transport.sql": "TASK-0488",
    "0060_skill_supply_chain.sql": "TASK-0490",
    "0061_pentest_run_store.sql": "TASK-0491",
    "0062_service_catalog.sql": "TASK-0492",
}


@dataclass(frozen=True)
class M48BEntryGateIssue:
    reason_code: str
    detail: str
    clause: int | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "reason_code": self.reason_code,
            "detail": self.detail,
        }
        if self.clause is not None:
            payload["clause"] = self.clause
        return payload


@dataclass(frozen=True)
class M48BEntryGateResult:
    status: str
    clause_status: Mapping[str, str]
    issues: tuple[M48BEntryGateIssue, ...]
    checked_ownership_scopes: int
    checked_migrations: int
    checked_cutover_surfaces: int
    checked_isolation_domains: int
    checked_p0_slices: int
    master_approval_required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": M48B_ENTRY_GATE_RESULT_SCHEMA_VERSION,
            "milestone": "M48-B",
            "status": self.status,
            "master_approval_required": True,
            "clause_status": dict(self.clause_status),
            "issues": [issue.to_dict() for issue in self.issues],
            "checked_ownership_scopes": self.checked_ownership_scopes,
            "checked_migrations": self.checked_migrations,
            "checked_cutover_surfaces": self.checked_cutover_surfaces,
            "checked_isolation_domains": self.checked_isolation_domains,
            "checked_p0_slices": self.checked_p0_slices,
        }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _objects(value: Any, *, field: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{field} must be an array")
    if not all(isinstance(item, Mapping) for item in value):
        raise ValueError(f"{field} entries must be objects")
    return list(value)


def _nonempty(item: Mapping[str, Any], fields: Sequence[str]) -> bool:
    return all(isinstance(item.get(name), str) and bool(str(item[name]).strip()) for name in fields)


def _add(issues: list[M48BEntryGateIssue], code: str, detail: str, clause: int) -> None:
    issues.append(M48BEntryGateIssue(reason_code=code, detail=detail, clause=clause))


def evaluate_m48b_entry_gate(
    *,
    root: str | Path,
    packet: Mapping[str, Any],
) -> M48BEntryGateResult:
    """Validate the five frozen Entry Gate clauses without changing state."""

    root_path = Path(root).resolve()
    issues: list[M48BEntryGateIssue] = []
    clause_status = {str(number): "PASS" for number in range(1, 6)}

    if packet.get("schema_version") != M48B_ENTRY_GATE_PACKET_SCHEMA_VERSION:
        raise ValueError("unsupported M48-B Entry Gate packet schema")

    baseline = packet.get("baseline")
    if not isinstance(baseline, Mapping):
        raise ValueError("baseline must be an object")
    manifest_ref = baseline.get("manifest_ref")
    manifest_path = root_path / str(manifest_ref or "")
    if baseline.get("task_0461_status") != "DONE":
        _add(issues, "M48B_BASELINE_NOT_FROZEN", "TASK-0461 is not recorded as DONE", 1)
    if not manifest_ref or not manifest_path.is_file():
        _add(issues, "M48B_BASELINE_MANIFEST_MISSING", "frozen baseline manifest is missing", 1)
    elif _sha256(manifest_path) != baseline.get("manifest_sha256"):
        _add(issues, "M48B_BASELINE_MANIFEST_DRIFT", "frozen baseline manifest hash changed", 1)
    else:
        manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        entries = manifest_payload.get("entries") if isinstance(manifest_payload, Mapping) else None
        if not isinstance(entries, list) or len(entries) != baseline.get("protected_entry_count"):
            _add(issues, "M48B_BASELINE_OWNER_COUNT_DRIFT", "frozen dirty-owner entry count changed", 1)

    ownership = _objects(packet.get("file_ownership"), field="file_ownership")
    scope_ids = [str(row.get("scope_id", "")) for row in ownership]
    if not ownership or len(scope_ids) != len(set(scope_ids)) or "" in scope_ids:
        _add(issues, "M48B_FILE_OWNER_AMBIGUOUS", "file ownership scope IDs must be unique", 2)
    for row in ownership:
        if not _nonempty(row, ("scope_id", "path_scope", "primary_owner", "coordination")):
            _add(issues, "M48B_FILE_OWNER_INCOMPLETE", f"incomplete file owner row: {row.get('scope_id')}", 2)
    m48a_ownership = packet.get("m48a_frozen_ownership")
    if not isinstance(m48a_ownership, Mapping) or not _nonempty(
        m48a_ownership,
        ("commit_range", "primary_owner", "transfer_policy"),
    ) or not isinstance(m48a_ownership.get("changed_file_count"), int):
        _add(issues, "M48A_FROZEN_OWNER_INCOMPLETE", "M48-A frozen ownership range is not registered", 2)

    migrations = _objects(packet.get("migration_registry"), field="migration_registry")
    migration_map = {
        str(row.get("asset")): str(row.get("primary_owner"))
        for row in migrations
        if row.get("asset")
    }
    if len(migrations) != len(EXPECTED_MIGRATION_OWNERS) or migration_map != EXPECTED_MIGRATION_OWNERS:
        _add(issues, "M48B_MIGRATION_OWNER_INCOMPLETE", "0058-0062 reservations do not match frozen owners", 2)

    cutovers = _objects(packet.get("cutover_matrix"), field="cutover_matrix")
    cutover_ids = [str(row.get("surface_id", "")) for row in cutovers]
    if not cutovers or len(cutover_ids) != len(set(cutover_ids)) or "" in cutover_ids:
        _add(issues, "M48B_CUTOVER_OWNER_AMBIGUOUS", "cutover surfaces must be unique", 2)
    for row in cutovers:
        if not _nonempty(row, ("surface_id", "primary_owner", "cutover", "rollback")):
            _add(issues, "M48B_CUTOVER_INCOMPLETE", f"incomplete cutover row: {row.get('surface_id')}", 2)

    approval = packet.get("master_approval")
    if not isinstance(approval, Mapping):
        raise ValueError("master_approval must be an object")
    approval_ref = str(approval.get("approval_ref", ""))
    if (
        approval.get("required") is not True
        or approval.get("status") != "APPROVED"
        or approval.get("approver_role") != "MASTER"
        or not approval_ref
        or approval_ref.startswith("unavailable://")
    ):
        _add(issues, "M48B_MASTER_APPROVAL_PENDING", "MASTER has not approved the owner/migration/cutover matrix", 2)

    isolation = _objects(packet.get("isolation_fixtures"), field="isolation_fixtures")
    isolation_by_domain = {str(row.get("domain")): row for row in isolation}
    if len(isolation) != len(EXPECTED_DOMAINS) or set(isolation_by_domain) != EXPECTED_DOMAINS:
        _add(issues, "M48B_ISOLATION_DOMAIN_SET_INVALID", "all five isolation domains must be registered exactly once", 3)
    harness_ref = packet.get("isolation_harness_ref")
    if not isinstance(harness_ref, str) or not (root_path / harness_ref).is_file():
        _add(issues, "M48B_ISOLATION_HARNESS_MISSING", "shared isolation harness is missing", 3)
    for domain, row in isolation_by_domain.items():
        if not _nonempty(row, ("principal", "test_secret_source", "channel", "network_policy", "process_policy")):
            _add(issues, "M48B_ISOLATION_FIXTURE_INCOMPLETE", f"incomplete isolation fixture: {domain}", 3)
        if row.get("principal") != f"test:m48b:{domain}" or row.get("channel") != f"test+loopback://m48b/{domain}":
            _add(issues, "M48B_ISOLATION_IDENTITY_UNSAFE", f"principal/channel is not test-only: {domain}", 3)
        if not str(row.get("test_secret_source", "")).startswith("ephemeral "):
            _add(issues, "M48B_TEST_SECRET_NOT_EPHEMERAL", f"test secret is not ephemeral: {domain}", 3)
        if row.get("network_policy") != "loopback_only" or row.get("process_policy") != "deny":
            _add(issues, "M48B_ISOLATION_POLICY_UNSAFE", f"unsafe network/process policy: {domain}", 3)
        if row.get("physical_effects_allowed") is not False:
            _add(issues, "M48B_PHYSICAL_EFFECTS_NOT_DENIED", f"physical effects not denied: {domain}", 3)

    catalog = packet.get("migration_catalog")
    if not isinstance(catalog, Mapping):
        raise ValueError("migration_catalog must be an object")
    if catalog.get("path") != "host_brain/audit/migrations" or catalog.get("allow_secondary_catalog") is not False:
        _add(issues, "M48B_MIGRATION_CATALOG_INVALID", "M48-B must use the frozen central catalog only", 4)
    if catalog.get("change_policy") != "expand_only":
        _add(issues, "M48B_MIGRATION_POLICY_INVALID", "M48-B migrations must be expand-only", 4)
    debt = catalog.get("baseline_debt")
    if not isinstance(debt, Mapping) or not _nonempty(debt, ("owner", "disposition", "stop_condition")):
        _add(issues, "M48B_CATALOG_DEBT_UNREGISTERED", "existing duplicate/gap debt needs an owner and disposition", 4)

    p0_slices = _objects(packet.get("p0_registry"), field="p0_registry")
    p0_by_task = {str(row.get("task_id")): row for row in p0_slices}
    if len(p0_slices) != len(EXPECTED_P0_TASKS) or set(p0_by_task) != EXPECTED_P0_TASKS:
        _add(issues, "M48B_P0_TASK_SET_INVALID", "TASK-0487 through TASK-0495 must each be registered", 5)
    feature_flags = [str(row.get("feature_flag", "")) for row in p0_slices]
    evidence_paths = [str(row.get("evidence_path", "")) for row in p0_slices]
    if len(feature_flags) != len(set(feature_flags)) or len(evidence_paths) != len(set(evidence_paths)):
        _add(issues, "M48B_P0_CONTROL_AMBIGUOUS", "feature flags and evidence paths must be unique", 5)
    for task_id, row in p0_by_task.items():
        if not _nonempty(row, ("feature_flag", "default", "stop_condition", "evidence_path", "rollback")):
            _add(issues, "M48B_P0_CONTROL_INCOMPLETE", f"missing flag/stop/evidence/rollback: {task_id}", 5)

    for issue in issues:
        clause_key = str(issue.clause)
        if issue.reason_code == "M48B_MASTER_APPROVAL_PENDING":
            if clause_status[clause_key] != "FAILED":
                clause_status[clause_key] = "BLOCKED"
        else:
            clause_status[clause_key] = "FAILED"
    status = "OPEN" if not issues else "BLOCKED"
    return M48BEntryGateResult(
        status=status,
        clause_status=clause_status,
        issues=tuple(issues),
        checked_ownership_scopes=len(ownership),
        checked_migrations=len(migrations),
        checked_cutover_surfaces=len(cutovers),
        checked_isolation_domains=len(isolation),
        checked_p0_slices=len(p0_slices),
    )


def load_m48b_entry_gate_packet(path: str | Path) -> Mapping[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("M48-B Entry Gate packet must be a JSON object")
    return payload
