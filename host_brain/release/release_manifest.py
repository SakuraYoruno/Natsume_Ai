"""Release checklist and rollback mapping contracts for M0~M3 delivery."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from host_brain.core.config_snapshot import CONFIG_SNAPSHOT_DIGEST_PREFIX
from host_brain.core.errors import (
    RELEASE_ASSET_MISSING,
    RELEASE_CHECKSUM_DRIFT,
    RELEASE_CONFIG_SNAPSHOT_INVALID,
    RELEASE_FLOW_POINTER_MISMATCH,
    RELEASE_ROLLBACK_TARGET_MISSING,
)
from host_brain.core.ids import now_ms


S_RELEASE_CHECKLIST = "S_RELEASE_CHECKLIST"
STATUS_READY = "READY"
STATUS_FAILED = "FAILED"
ALLOWED_MILESTONES = {"M0", "M1", "M2", "M3"}
ALLOWED_FLOW_ENVS = ("dev", "staging", "prod")
CHECKSUM_PREFIX = "sha256:"


def build_release_checklist(
    *,
    root: str | Path,
    milestone: str,
    release_id: str,
    assets: Iterable[str],
    required_services: Iterable[str],
    config_snapshot_refs: Iterable[str],
    active_pointer_path: str | Path = "flows/active.pointer",
    include_active_flow_assets: bool = True,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    repo_root = Path(root).resolve()
    normalized_milestone = str(milestone).strip().upper()
    if normalized_milestone not in ALLOWED_MILESTONES:
        raise ValueError(f"milestone must be one of {sorted(ALLOWED_MILESTONES)}")

    flow_refs = _load_active_pointer(repo_root / active_pointer_path)
    asset_list = sorted({str(item).replace("\\", "/") for item in assets})
    if include_active_flow_assets:
        for env in ALLOWED_FLOW_ENVS:
            flow_path = str(flow_refs[env])
            if flow_path not in asset_list:
                asset_list.append(flow_path)
        asset_list.sort()

    checksums: dict[str, str | None] = {}
    for rel_path in asset_list:
        abs_path = repo_root / rel_path
        checksums[rel_path] = _digest_file(abs_path) if abs_path.exists() else None

    return {
        "stage": S_RELEASE_CHECKLIST,
        "status": STATUS_READY,
        "reason_code": None,
        "milestone": normalized_milestone,
        "release_id": str(release_id),
        "assets": asset_list,
        "checksums": checksums,
        "required_services": sorted({str(service) for service in required_services}),
        "flow_refs": {env: str(flow_refs[env]) for env in ALLOWED_FLOW_ENVS},
        "config_snapshot_refs": sorted({str(ref) for ref in config_snapshot_refs}),
        "ts_ms": int(ts_ms if ts_ms is not None else now_ms()),
    }


def build_rollback_mapping(
    *,
    release_id: str,
    rollback_target: str,
    impact_scope: Iterable[str],
    milestone: str,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    return {
        "stage": S_RELEASE_CHECKLIST,
        "release_id": str(release_id),
        "rollback_target": str(rollback_target),
        "impact_scope": sorted({str(item) for item in impact_scope}),
        "milestone": str(milestone).strip().upper(),
        "ts_ms": int(ts_ms if ts_ms is not None else now_ms()),
    }


def validate_release_bundle(
    *,
    root: str | Path,
    checklist: Mapping[str, Any],
    rollback_mapping: Mapping[str, Any],
    known_release_ids: set[str] | None = None,
    known_config_snapshot_refs: set[str] | None = None,
    active_pointer_path: str | Path = "flows/active.pointer",
    flow_index_path: str | Path = "docs/60_trace/flows/index.md",
) -> dict[str, Any]:
    repo_root = Path(root).resolve()
    issues: list[dict[str, Any]] = []

    active_pointer = _load_active_pointer(repo_root / active_pointer_path)
    flow_refs = dict(checklist.get("flow_refs") or {})
    if flow_refs != active_pointer:
        issues.append(
            _issue(
                reason_code=RELEASE_FLOW_POINTER_MISMATCH,
                failed_artifact=_normalize(active_pointer_path),
                detail="checklist.flow_refs must match flows/active.pointer",
            )
        )

    index_text = (repo_root / flow_index_path).read_text(encoding="utf-8")
    for env in ALLOWED_FLOW_ENVS:
        pointer_path = str(active_pointer[env])
        if pointer_path not in index_text:
            issues.append(
                _issue(
                    reason_code=RELEASE_FLOW_POINTER_MISMATCH,
                    failed_artifact=_normalize(flow_index_path),
                    detail=f"flow index missing active path for {env}: {pointer_path}",
                )
            )

    assets = [str(item).replace("\\", "/") for item in checklist.get("assets", [])]
    checksums = dict(checklist.get("checksums") or {})
    for rel_path in sorted(assets):
        abs_path = repo_root / rel_path
        if not abs_path.exists():
            issues.append(
                _issue(
                    reason_code=RELEASE_ASSET_MISSING,
                    failed_artifact=rel_path,
                    detail="release asset missing on disk",
                )
            )
            continue
        expected = str(checksums.get(rel_path) or "")
        actual = _digest_file(abs_path)
        if expected != actual:
            issues.append(
                _issue(
                    reason_code=RELEASE_CHECKSUM_DRIFT,
                    failed_artifact=rel_path,
                    detail=f"checksum drift: expected={expected}, actual={actual}",
                )
            )

    config_snapshot_refs = [str(ref) for ref in checklist.get("config_snapshot_refs", [])]
    if not config_snapshot_refs:
        issues.append(
            _issue(
                reason_code=RELEASE_CONFIG_SNAPSHOT_INVALID,
                failed_artifact="config_snapshot_refs",
                detail="config snapshot refs are required",
            )
        )
    for ref in sorted(config_snapshot_refs):
        if not ref.startswith(CONFIG_SNAPSHOT_DIGEST_PREFIX):
            issues.append(
                _issue(
                    reason_code=RELEASE_CONFIG_SNAPSHOT_INVALID,
                    failed_artifact=ref,
                    detail="config snapshot digest prefix invalid",
                )
            )
            continue
        if known_config_snapshot_refs is not None and ref not in known_config_snapshot_refs:
            issues.append(
                _issue(
                    reason_code=RELEASE_CONFIG_SNAPSHOT_INVALID,
                    failed_artifact=ref,
                    detail="config snapshot digest not found in known snapshots",
                )
            )

    rollback_target = str(rollback_mapping.get("rollback_target") or "")
    if known_release_ids is not None and rollback_target not in known_release_ids:
        issues.append(
            _issue(
                reason_code=RELEASE_ROLLBACK_TARGET_MISSING,
                failed_artifact=rollback_target,
                detail="rollback target release_id not found",
            )
        )

    impact_scope = sorted({str(item) for item in rollback_mapping.get("impact_scope", [])})
    if not impact_scope:
        issues.append(
            _issue(
                reason_code=RELEASE_ROLLBACK_TARGET_MISSING,
                failed_artifact="impact_scope",
                detail="impact_scope must include at least one service",
            )
        )

    issues.sort(
        key=lambda row: (
            str(row.get("failed_artifact") or ""),
            str(row.get("reason_code") or ""),
            str(row.get("detail") or ""),
        )
    )
    status = STATUS_FAILED if issues else STATUS_READY
    return {
        "stage": S_RELEASE_CHECKLIST,
        "status": status,
        "reason_code": (issues[0]["reason_code"] if issues else None),
        "release_id": str(checklist.get("release_id") or rollback_mapping.get("release_id") or ""),
        "milestone": str(checklist.get("milestone") or rollback_mapping.get("milestone") or ""),
        "summary": {
            "asset_count": len(assets),
            "required_services_count": len(checklist.get("required_services") or []),
            "impact_scope_count": len(impact_scope),
            "issue_count": len(issues),
        },
        "issues": issues,
        "checklist": dict(checklist),
        "rollback_mapping": dict(rollback_mapping),
    }


def build_release_matrix(
    *,
    root: str | Path,
    release_specs: Iterable[Mapping[str, Any]],
    active_pointer_path: str | Path = "flows/active.pointer",
) -> dict[str, Any]:
    repo_root = Path(root).resolve()
    rows: list[dict[str, Any]] = []
    for raw in release_specs:
        checklist = build_release_checklist(
            root=repo_root,
            milestone=str(raw.get("milestone") or ""),
            release_id=str(raw.get("release_id") or ""),
            assets=list(raw.get("assets") or []),
            required_services=list(raw.get("required_services") or []),
            config_snapshot_refs=list(raw.get("config_snapshot_refs") or []),
            active_pointer_path=active_pointer_path,
            include_active_flow_assets=bool(raw.get("include_active_flow_assets", True)),
            ts_ms=raw.get("ts_ms"),
        )
        rows.append(checklist)

    rows.sort(key=lambda row: str(row.get("milestone") or ""))
    return {
        "stage": S_RELEASE_CHECKLIST,
        "status": STATUS_READY,
        "reason_code": None,
        "releases": rows,
        "summary": {
            "release_count": len(rows),
            "milestones": [str(row["milestone"]) for row in rows],
        },
    }


def _load_active_pointer(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if set(data.keys()) != set(ALLOWED_FLOW_ENVS):
        raise ValueError("active.pointer must contain dev/staging/prod only")
    return {env: str(data[env]) for env in ALLOWED_FLOW_ENVS}


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return CHECKSUM_PREFIX + digest


def _normalize(path: str | Path) -> str:
    return str(path).replace("\\", "/")


def _issue(*, reason_code: str, failed_artifact: str, detail: str) -> dict[str, Any]:
    return {
        "check_id": S_RELEASE_CHECKLIST,
        "reason_code": reason_code,
        "failed_artifact": str(failed_artifact),
        "detail": str(detail),
    }
