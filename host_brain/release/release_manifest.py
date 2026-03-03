"""Release checklist and rollback mapping contracts for M0~M6 delivery."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from host_brain.core.config_snapshot import CONFIG_SNAPSHOT_DIGEST_PREFIX
from host_brain.core.errors import (
    RELEASE_ASSET_MISSING,
    RELEASE_CHECKSUM_DRIFT,
    RELEASE_CONFIG_SNAPSHOT_INVALID,
    RELEASE_FLOW_POINTER_MISMATCH,
    RELEASE_MODEL_PLANE_DIGEST_MISMATCH,
    RELEASE_MODEL_PLANE_VERSION_INVALID,
    RELEASE_ROLLBACK_DIGEST_MISSING,
    RELEASE_ROLLBACK_TARGET_MISSING,
)
from host_brain.core.ids import now_ms


S_RELEASE_CHECKLIST = "S_RELEASE_CHECKLIST"
STATUS_READY = "READY"
STATUS_FAILED = "FAILED"
ALLOWED_MILESTONES = {"M0", "M1", "M2", "M3", "M4", "M5", "M6"}
ALLOWED_FLOW_ENVS = ("dev", "staging", "prod")
CHECKSUM_PREFIX = "sha256:"
MODEL_PLANE_PROFILES_PATH = "configs/model_plane/model_profiles.yaml"
MODEL_PLANE_NODE_REGISTRY_PATH = "configs/model_plane/node_registry.yaml"
_VERSION_PATTERN = re.compile(r"^\s*#\s*版本\s*[：:]\s*(\S+)\s*$")


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
    include_model_plane_assets: bool = True,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    repo_root = Path(root).resolve()
    normalized_milestone = str(milestone).strip().upper()
    if normalized_milestone not in ALLOWED_MILESTONES:
        return {
            "stage": S_RELEASE_CHECKLIST,
            "status": STATUS_FAILED,
            "reason_code": "RELEASE_MILESTONE_INVALID",
            "milestone": normalized_milestone,
            "release_id": str(release_id),
            "assets": [],
            "checksums": {},
            "required_services": [],
            "flow_refs": {},
            "model_plane": None,
            "config_snapshot_refs": [],
            "ts_ms": int(ts_ms if ts_ms is not None else now_ms()),
        }

    flow_refs = _load_active_pointer(repo_root / active_pointer_path)
    asset_list = sorted({str(item).replace("\\", "/") for item in assets})
    if include_active_flow_assets:
        for env in ALLOWED_FLOW_ENVS:
            flow_path = str(flow_refs[env])
            if flow_path not in asset_list:
                asset_list.append(flow_path)

    model_plane = _load_model_plane_meta(repo_root) if include_model_plane_assets else None
    if model_plane is not None:
        for rel_path in model_plane["assets"]:
            if rel_path not in asset_list:
                asset_list.append(rel_path)

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
        "model_plane": model_plane,
        "config_snapshot_refs": sorted({str(ref) for ref in config_snapshot_refs}),
        "ts_ms": int(ts_ms if ts_ms is not None else now_ms()),
    }


def build_rollback_mapping(
    *,
    release_id: str,
    rollback_target: str,
    impact_scope: Iterable[str],
    milestone: str,
    model_plane_asset_digest: str | None = None,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    return {
        "stage": S_RELEASE_CHECKLIST,
        "release_id": str(release_id),
        "rollback_target": str(rollback_target),
        "impact_scope": sorted({str(item) for item in impact_scope}),
        "milestone": str(milestone).strip().upper(),
        "model_plane_asset_digest": (
            str(model_plane_asset_digest).strip() if model_plane_asset_digest else None
        ),
        "ts_ms": int(ts_ms if ts_ms is not None else now_ms()),
    }


def validate_release_bundle(
    *,
    root: str | Path,
    checklist: Mapping[str, Any],
    rollback_mapping: Mapping[str, Any],
    known_release_ids: set[str] | None = None,
    known_config_snapshot_refs: set[str] | None = None,
    known_model_plane_digests: set[str] | None = None,
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

    expected_model_plane = dict(checklist.get("model_plane") or {})
    current_model_plane = _load_model_plane_meta(repo_root)
    if expected_model_plane:
        _validate_model_plane_meta(
            issues=issues,
            expected_model_plane=expected_model_plane,
            current_model_plane=current_model_plane,
            known_model_plane_digests=known_model_plane_digests,
        )

    rollback_digest = str(rollback_mapping.get("model_plane_asset_digest") or "").strip()
    if not rollback_digest:
        issues.append(
            _issue(
                reason_code=RELEASE_ROLLBACK_DIGEST_MISSING,
                failed_artifact="rollback_mapping.model_plane_asset_digest",
                detail="rollback mapping must bind model_plane_asset_digest",
            )
        )
    elif rollback_digest != current_model_plane["asset_digest"]:
        issues.append(
            _issue(
                reason_code=RELEASE_MODEL_PLANE_DIGEST_MISMATCH,
                failed_artifact="rollback_mapping.model_plane_asset_digest",
                detail=(
                    "rollback mapping digest mismatch: "
                    f"expected={current_model_plane['asset_digest']}, actual={rollback_digest}"
                ),
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
            include_model_plane_assets=bool(raw.get("include_model_plane_assets", True)),
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


def _load_model_plane_meta(root: Path) -> dict[str, Any]:
    profile_path = root / MODEL_PLANE_PROFILES_PATH
    registry_path = root / MODEL_PLANE_NODE_REGISTRY_PATH
    profile_text = profile_path.read_text(encoding="utf-8")
    registry_text = registry_path.read_text(encoding="utf-8")

    profile_version = _extract_version_tag(profile_text)
    registry_version = _extract_version_tag(registry_text)

    digest = hashlib.sha256()
    digest.update(profile_text.encode("utf-8"))
    digest.update(b"\n---\n")
    digest.update(registry_text.encode("utf-8"))
    return {
        "profile_version": profile_version,
        "node_registry_version": registry_version,
        "asset_digest": CHECKSUM_PREFIX + digest.hexdigest(),
        "assets": [MODEL_PLANE_PROFILES_PATH, MODEL_PLANE_NODE_REGISTRY_PATH],
    }


def _extract_version_tag(text: str) -> str:
    for line in text.splitlines():
        match = _VERSION_PATTERN.match(line)
        if match:
            return match.group(1)
    return "unknown"


def _validate_model_plane_meta(
    *,
    issues: list[dict[str, Any]],
    expected_model_plane: Mapping[str, Any],
    current_model_plane: Mapping[str, Any],
    known_model_plane_digests: set[str] | None,
) -> None:
    expected_digest = str(expected_model_plane.get("asset_digest") or "")
    current_digest = str(current_model_plane.get("asset_digest") or "")
    if expected_digest != current_digest:
        issues.append(
            _issue(
                reason_code=RELEASE_MODEL_PLANE_DIGEST_MISMATCH,
                failed_artifact="model_plane.asset_digest",
                detail=f"model_plane digest mismatch: expected={expected_digest}, actual={current_digest}",
            )
        )

    for key in ("profile_version", "node_registry_version"):
        expected = str(expected_model_plane.get(key) or "")
        actual = str(current_model_plane.get(key) or "")
        if expected != actual:
            issues.append(
                _issue(
                    reason_code=RELEASE_MODEL_PLANE_VERSION_INVALID,
                    failed_artifact=f"model_plane.{key}",
                    detail=f"model_plane version mismatch: expected={expected}, actual={actual}",
                )
            )

    if known_model_plane_digests is not None and current_digest not in known_model_plane_digests:
        issues.append(
            _issue(
                reason_code=RELEASE_MODEL_PLANE_VERSION_INVALID,
                failed_artifact="model_plane.asset_digest",
                detail="model_plane digest not found in known digest set",
            )
        )


def _normalize(path: str | Path) -> str:
    return str(path).replace("\\", "/")


def _issue(*, reason_code: str, failed_artifact: str, detail: str) -> dict[str, Any]:
    return {
        "check_id": S_RELEASE_CHECKLIST,
        "reason_code": reason_code,
        "failed_artifact": str(failed_artifact),
        "detail": str(detail),
    }


# ---------------------------------------------------------------------------
# M8 TASK-0110: M8 发布清单生成
# ---------------------------------------------------------------------------

M8_MIGRATION_DIRS = (
    "host_brain/audit/migrations",
    "host_brain/bond/migrations",
)
_MIGRATION_EXTS = frozenset({".sql", ".cypher"})


def generate_m8_manifest(
    *,
    root: str | Path,
    test_passed_count: int,
    latency_baseline_snapshot: dict[str, Any] | None = None,
    service_registry_snapshot: list[dict[str, Any]] | None = None,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    """Generate M8 release manifest.

    Locks:
    - git commit hash (via git rev-parse HEAD)
    - migration file hashes (SQL + Cypher)
    - test_passed_count
    - latency_baseline_snapshot (from T-PERF-01)
    - service_registry_snapshot (from ServiceRegistry.all())
    - generated_at (ISO 8601)
    """
    repo_root = Path(root).resolve()
    now_value = int(ts_ms if ts_ms is not None else now_ms())
    generated_at = datetime.fromtimestamp(now_value / 1000, tz=timezone.utc).isoformat()

    # ── git commit hash ──────────────────────────────────────────────────────
    git_commit_hash = _get_git_commit_hash(repo_root)

    # ── migration file hashes ────────────────────────────────────────────────
    migration_hashes: dict[str, str] = {}
    for migration_dir in M8_MIGRATION_DIRS:
        dir_path = repo_root / migration_dir
        if not dir_path.is_dir():
            continue
        for file_path in sorted(dir_path.iterdir()):
            if file_path.suffix.lower() not in _MIGRATION_EXTS:
                continue
            rel = _normalize(file_path.relative_to(repo_root))
            migration_hashes[rel] = _digest_file(file_path)

    return {
        "milestone": "M8",
        "git_commit_hash": git_commit_hash,
        "migration_hashes": migration_hashes,
        "migration_file_count": len(migration_hashes),
        "test_passed_count": int(test_passed_count),
        "latency_baseline_snapshot": latency_baseline_snapshot,
        "service_registry_snapshot": service_registry_snapshot or [],
        "generated_at": generated_at,
        "ts_ms": now_value,
    }


def _get_git_commit_hash(repo_root: Path) -> str | None:
    """Read git commit hash from .git/HEAD (no subprocess dependency)."""
    try:
        head_file = repo_root / ".git" / "HEAD"
        if not head_file.exists():
            return None
        head_content = head_file.read_text(encoding="utf-8").strip()
        # 直接 commit hash（detached HEAD）
        if not head_content.startswith("ref:"):
            return head_content if len(head_content) == 40 else None
        # ref: refs/heads/<branch>
        ref_path = head_content.split("ref:", 1)[1].strip()
        ref_file = repo_root / ".git" / ref_path
        if ref_file.exists():
            return ref_file.read_text(encoding="utf-8").strip()
        # packed-refs fallback
        packed_refs = repo_root / ".git" / "packed-refs"
        if packed_refs.exists():
            for line in packed_refs.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.endswith(ref_path):
                    parts = line.split()
                    if parts:
                        return parts[0]
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# M9 TASK-0115（收尾）: M9 发布清单生成
# ---------------------------------------------------------------------------

M9_MIGRATION_DIRS = (
    "host_brain/audit/migrations",
    "host_brain/bond/migrations",
)

M9_CANON_ASSET_DIRS = (
    "host_brain/canon/world",
)
_CANON_EXTS = frozenset({".json"})


def generate_m9_manifest(
    *,
    root: str | Path,
    test_passed_count: int,
    latency_baseline_snapshot: dict[str, Any] | None = None,
    service_registry_snapshot: list[dict[str, Any]] | None = None,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    """Generate M9 release manifest.

    Locks:
    - git commit hash (via .git/HEAD)
    - migration file hashes（SQL + Cypher，含 M9 新增的 0015/0016）
    - world_canon_asset_hashes（host_brain/canon/world/*.json 种子条目）
    - test_passed_count
    - latency_baseline_snapshot
    - service_registry_snapshot
    - generated_at (ISO 8601)
    """
    repo_root = Path(root).resolve()
    now_value = int(ts_ms if ts_ms is not None else now_ms())
    generated_at = datetime.fromtimestamp(now_value / 1000, tz=timezone.utc).isoformat()

    # ── git commit hash ──────────────────────────────────────────────────────
    git_commit_hash = _get_git_commit_hash(repo_root)

    # ── migration file hashes ────────────────────────────────────────────────
    migration_hashes: dict[str, str] = {}
    for migration_dir in M9_MIGRATION_DIRS:
        dir_path = repo_root / migration_dir
        if not dir_path.is_dir():
            continue
        for file_path in sorted(dir_path.iterdir()):
            if file_path.suffix.lower() not in _MIGRATION_EXTS:
                continue
            rel = _normalize(file_path.relative_to(repo_root))
            migration_hashes[rel] = _digest_file(file_path)

    # ── World Canon 种子条目 hashes ──────────────────────────────────────────
    world_canon_asset_hashes: dict[str, str] = {}
    for canon_dir in M9_CANON_ASSET_DIRS:
        dir_path = repo_root / canon_dir
        if not dir_path.is_dir():
            continue
        for file_path in sorted(dir_path.iterdir()):
            if file_path.suffix.lower() not in _CANON_EXTS:
                continue
            rel = _normalize(file_path.relative_to(repo_root))
            world_canon_asset_hashes[rel] = _digest_file(file_path)

    return {
        "milestone": "M9",
        "git_commit_hash": git_commit_hash,
        "migration_hashes": migration_hashes,
        "migration_file_count": len(migration_hashes),
        "world_canon_asset_hashes": world_canon_asset_hashes,
        "world_canon_asset_count": len(world_canon_asset_hashes),
        "test_passed_count": int(test_passed_count),
        "latency_baseline_snapshot": latency_baseline_snapshot,
        "service_registry_snapshot": service_registry_snapshot or [],
        "generated_at": generated_at,
        "ts_ms": now_value,
    }


def generate_m10a_manifest(
    *,
    root: str | Path,
    test_passed_count: int,
    latency_baseline_snapshot: dict[str, Any] | None = None,
    service_registry_snapshot: list[dict[str, Any]] | None = None,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    """Generate M10-A release manifest.

    M10-A 新增：Canon 治理 REST API（/canon/proposals + /canon/documents）。
    Locks:
    - git commit hash (via .git/HEAD)
    - migration file hashes（SQL + Cypher，含 M9 新增的 0015/0016）
    - world_canon_asset_hashes（host_brain/canon/world/*.json 种子条目）
    - canon_routes_hash（host_brain/app/canon_routes.py 文件 hash，锁定 API 实现）
    - test_passed_count（1041）
    - latency_baseline_snapshot
    - service_registry_snapshot（含 host.canon.proposals_api 新服务）
    - generated_at (ISO 8601)
    """
    repo_root = Path(root).resolve()
    now_value = int(ts_ms if ts_ms is not None else now_ms())
    generated_at = datetime.fromtimestamp(now_value / 1000, tz=timezone.utc).isoformat()

    # ── git commit hash ──────────────────────────────────────────────────────
    git_commit_hash = _get_git_commit_hash(repo_root)

    # ── migration file hashes（复用 M9 目录，M10-A 无新 migration）────────────
    migration_hashes: dict[str, str] = {}
    for migration_dir in M9_MIGRATION_DIRS:
        dir_path = repo_root / migration_dir
        if not dir_path.is_dir():
            continue
        for file_path in sorted(dir_path.iterdir()):
            if file_path.suffix.lower() not in _MIGRATION_EXTS:
                continue
            rel = _normalize(file_path.relative_to(repo_root))
            migration_hashes[rel] = _digest_file(file_path)

    # ── World Canon 种子条目 hashes ──────────────────────────────────────────
    world_canon_asset_hashes: dict[str, str] = {}
    for canon_dir in M9_CANON_ASSET_DIRS:
        dir_path = repo_root / canon_dir
        if not dir_path.is_dir():
            continue
        for file_path in sorted(dir_path.iterdir()):
            if file_path.suffix.lower() not in _CANON_EXTS:
                continue
            rel = _normalize(file_path.relative_to(repo_root))
            world_canon_asset_hashes[rel] = _digest_file(file_path)

    # ── M10-A 新增：canon_routes.py 文件 hash（锁定 API 实现）────────────────
    canon_routes_path = repo_root / "host_brain" / "app" / "canon_routes.py"
    canon_routes_hash = (
        _digest_file(canon_routes_path) if canon_routes_path.exists() else None
    )

    return {
        "milestone": "M10-A",
        "git_commit_hash": git_commit_hash,
        "migration_hashes": migration_hashes,
        "migration_file_count": len(migration_hashes),
        "world_canon_asset_hashes": world_canon_asset_hashes,
        "world_canon_asset_count": len(world_canon_asset_hashes),
        "canon_routes_hash": canon_routes_hash,
        "test_passed_count": int(test_passed_count),
        "latency_baseline_snapshot": latency_baseline_snapshot,
        "service_registry_snapshot": service_registry_snapshot or [],
        "generated_at": generated_at,
        "ts_ms": now_value,
    }


# ---------------------------------------------------------------------------
# M10-B TASK-0125: M10-B 发布清单生成
# ---------------------------------------------------------------------------

def generate_m10b_manifest(
    *,
    root: str | Path,
    test_passed_count: int,
    latency_baseline_snapshot: dict[str, Any] | None = None,
    service_registry_snapshot: list[dict[str, Any]] | None = None,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    """Generate M10-B release manifest.

    M10-B 新增：ASR 热词注入 World Canon 关键词。
    Locks:
    - git commit hash (via .git/HEAD)
    - migration file hashes（SQL + Cypher，含 M10-B 新增的 0017）
    - world_canon_asset_hashes（host_brain/canon/world/*.json 种子条目）
    - canon_routes_hash（host_brain/app/canon_routes.py，含 /canon/hotwords 端点）
    - asr_hash（edge_client/senses/asr.py，含 inject_canon_hotwords）
    - test_passed_count（1054）
    - latency_baseline_snapshot
    - service_registry_snapshot（含 host.canon.hotwords_api 新服务）
    - generated_at (ISO 8601)
    """
    repo_root = Path(root).resolve()
    now_value = int(ts_ms if ts_ms is not None else now_ms())
    generated_at = datetime.fromtimestamp(now_value / 1000, tz=timezone.utc).isoformat()

    git_commit_hash = _get_git_commit_hash(repo_root)

    # ── migration file hashes ────────────────────────────────────────────────
    migration_hashes: dict[str, str] = {}
    for migration_dir in M9_MIGRATION_DIRS:
        dir_path = repo_root / migration_dir
        if not dir_path.is_dir():
            continue
        for file_path in sorted(dir_path.iterdir()):
            if file_path.suffix.lower() not in _MIGRATION_EXTS:
                continue
            rel = _normalize(file_path.relative_to(repo_root))
            migration_hashes[rel] = _digest_file(file_path)

    # ── World Canon 种子条目 hashes ──────────────────────────────────────────
    world_canon_asset_hashes: dict[str, str] = {}
    for canon_dir in M9_CANON_ASSET_DIRS:
        dir_path = repo_root / canon_dir
        if not dir_path.is_dir():
            continue
        for file_path in sorted(dir_path.iterdir()):
            if file_path.suffix.lower() not in _CANON_EXTS:
                continue
            rel = _normalize(file_path.relative_to(repo_root))
            world_canon_asset_hashes[rel] = _digest_file(file_path)

    # ── M10-B 新增：canon_routes.py + asr.py 文件 hash ──────────────────────
    canon_routes_path = repo_root / "host_brain" / "app" / "canon_routes.py"
    canon_routes_hash = (
        _digest_file(canon_routes_path) if canon_routes_path.exists() else None
    )
    asr_path = repo_root / "edge_client" / "senses" / "asr.py"
    asr_hash = _digest_file(asr_path) if asr_path.exists() else None

    return {
        "milestone": "M10-B",
        "git_commit_hash": git_commit_hash,
        "migration_hashes": migration_hashes,
        "migration_file_count": len(migration_hashes),
        "world_canon_asset_hashes": world_canon_asset_hashes,
        "world_canon_asset_count": len(world_canon_asset_hashes),
        "canon_routes_hash": canon_routes_hash,
        "asr_hash": asr_hash,
        "test_passed_count": int(test_passed_count),
        "latency_baseline_snapshot": latency_baseline_snapshot,
        "service_registry_snapshot": service_registry_snapshot or [],
        "generated_at": generated_at,
        "ts_ms": now_value,
    }


# ---------------------------------------------------------------------------
# M10-C: M10-C 发布清单生成
# ---------------------------------------------------------------------------

def generate_m10c_manifest(
    *,
    root: str | Path,
    test_passed_count: int,
    latency_baseline_snapshot: dict[str, Any] | None = None,
    service_registry_snapshot: list[dict[str, Any]] | None = None,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    """Generate M10-C release manifest.

    M10-C 新增：记忆蒸馏系统（MemoryDistillationEngine + semantic_memory 表 + context_pack.semantic_slot）。
    Locks:
    - git commit hash (via .git/HEAD)
    - migration file hashes（SQL + Cypher，含 M10-B 新增的 0017）
    - world_canon_asset_hashes（host_brain/canon/world/*.json 种子条目）
    - distillation_hash（host_brain/memory/distillation.py 文件 hash）
    - context_pack_hash（host_brain/core/context_pack.py 文件 hash，含 semantic_slot）
    - sqlite_ledger_hash（host_brain/audit/sqlite_ledger.py 文件 hash，含 semantic_memory 表）
    - test_passed_count（1062）
    - latency_baseline_snapshot
    - service_registry_snapshot（含 host.memory.distillation 新服务）
    - generated_at (ISO 8601)
    """
    repo_root = Path(root).resolve()
    now_value = int(ts_ms if ts_ms is not None else now_ms())
    generated_at = datetime.fromtimestamp(now_value / 1000, tz=timezone.utc).isoformat()

    git_commit_hash = _get_git_commit_hash(repo_root)

    # ── migration file hashes ────────────────────────────────────────────────
    migration_hashes: dict[str, str] = {}
    for migration_dir in M9_MIGRATION_DIRS:
        dir_path = repo_root / migration_dir
        if not dir_path.is_dir():
            continue
        for file_path in sorted(dir_path.iterdir()):
            if file_path.suffix.lower() not in _MIGRATION_EXTS:
                continue
            rel = _normalize(file_path.relative_to(repo_root))
            migration_hashes[rel] = _digest_file(file_path)

    # ── World Canon 种子条目 hashes ──────────────────────────────────────────
    world_canon_asset_hashes: dict[str, str] = {}
    for canon_dir in M9_CANON_ASSET_DIRS:
        dir_path = repo_root / canon_dir
        if not dir_path.is_dir():
            continue
        for file_path in sorted(dir_path.iterdir()):
            if file_path.suffix.lower() not in _CANON_EXTS:
                continue
            rel = _normalize(file_path.relative_to(repo_root))
            world_canon_asset_hashes[rel] = _digest_file(file_path)

    # ── M10-C 新增：distillation.py + context_pack.py + sqlite_ledger.py hash ─
    distillation_path = repo_root / "host_brain" / "memory" / "distillation.py"
    distillation_hash = (
        _digest_file(distillation_path) if distillation_path.exists() else None
    )
    context_pack_path = repo_root / "host_brain" / "core" / "context_pack.py"
    context_pack_hash = (
        _digest_file(context_pack_path) if context_pack_path.exists() else None
    )
    sqlite_ledger_path = repo_root / "host_brain" / "audit" / "sqlite_ledger.py"
    sqlite_ledger_hash = (
        _digest_file(sqlite_ledger_path) if sqlite_ledger_path.exists() else None
    )

    return {
        "milestone": "M10-C",
        "git_commit_hash": git_commit_hash,
        "migration_hashes": migration_hashes,
        "migration_file_count": len(migration_hashes),
        "world_canon_asset_hashes": world_canon_asset_hashes,
        "world_canon_asset_count": len(world_canon_asset_hashes),
        "distillation_hash": distillation_hash,
        "context_pack_hash": context_pack_hash,
        "sqlite_ledger_hash": sqlite_ledger_hash,
        "test_passed_count": int(test_passed_count),
        "latency_baseline_snapshot": latency_baseline_snapshot,
        "service_registry_snapshot": service_registry_snapshot or [],
        "generated_at": generated_at,
        "ts_ms": now_value,
    }
