"""Release checklist and manifest helpers used by drill/release contracts."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from host_brain.release.compatibility_registry import (
    RETIRED_CORE_PATHS,
    validate_registry,
    validate_retired_paths_absent,
)

from host_brain.core.errors import (
    RELEASE_ASSET_MISSING,
    RELEASE_CHECKSUM_DRIFT,
    RELEASE_CONFIG_SNAPSHOT_INVALID,
    RELEASE_MODEL_PLANE_DIGEST_MISMATCH,
    RELEASE_MODEL_PLANE_VERSION_INVALID,
    RELEASE_ROLLBACK_DIGEST_MISSING,
    RELEASE_ROLLBACK_TARGET_MISSING,
)

S_RELEASE_CHECKLIST = "S_RELEASE_CHECKLIST"
STATUS_READY = "READY"
STATUS_FAILED = "FAILED"

_MIGRATION_EXTS = frozenset({".sql", ".cypher"})
M8_MIGRATION_DIRS: tuple[str, ...] = (
    "host_brain/audit/migrations",
    "host_brain/bond/migrations",
)

_VALID_MILESTONE_RE = re.compile(r"^M\d+$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_CONFIG_SNAPSHOT_RE = re.compile(r"^cfg_snap::sha256:[0-9a-f]{64}$")
_VERSION_RE = re.compile(r"v?\d+(?:\.\d+){0,2}")

CORE_RELEASE_MANIFEST_SCHEMA_VERSION = "natsume.core-release-manifest.v1"
CORE_RELEASE_SBOM_SCHEMA_VERSION = "CycloneDX-1.5"
CORE_RELEASE_GATE_COUNT = 15


def generate_m8_manifest(
    *,
    root: str | Path,
    test_passed_count: int,
    latency_baseline_snapshot: dict[str, Any] | None = None,
    service_registry_snapshot: list[dict[str, Any]] | None = None,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    root_path = Path(root).resolve()
    now_ms = int(ts_ms if ts_ms is not None else int(time.time() * 1000))
    generated_at = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).isoformat()
    migration_hashes = _collect_migration_hashes(root_path=root_path)
    return {
        "milestone": "M8",
        "git_commit_hash": _resolve_git_commit_hash(root_path),
        "migration_hashes": migration_hashes,
        "migration_file_count": len(migration_hashes),
        "test_passed_count": int(test_passed_count),
        "latency_baseline_snapshot": dict(latency_baseline_snapshot or {}),
        "service_registry_snapshot": list(service_registry_snapshot or []),
        "generated_at": generated_at,
        "ts_ms": now_ms,
    }


def build_release_checklist(
    *,
    root: str | Path,
    milestone: str,
    release_id: str,
    assets: Iterable[str],
    required_services: Iterable[str],
    config_snapshot_refs: Iterable[str],
    include_model_plane_assets: bool = False,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    root_path = Path(root).resolve()
    milestone_norm = str(milestone or "").strip().upper()
    now_ms = int(ts_ms if ts_ms is not None else int(time.time() * 1000))

    asset_list = [str(item).strip() for item in assets if str(item).strip()]
    model_plane_meta = _load_model_plane_meta(root_path) if include_model_plane_assets else None
    if model_plane_meta is not None:
        for rel_path in model_plane_meta["assets"]:
            if rel_path not in asset_list:
                asset_list.append(rel_path)

    checksums = {rel: _hash_file_relaxed(root_path / rel) for rel in asset_list}
    required_service_rows = [str(item).strip() for item in required_services if str(item).strip()]
    config_refs = [str(item).strip() for item in config_snapshot_refs if str(item).strip()]

    status = STATUS_READY
    reason_code: str | None = None
    if not _VALID_MILESTONE_RE.match(milestone_norm):
        status = STATUS_FAILED
        reason_code = RELEASE_CONFIG_SNAPSHOT_INVALID
    elif include_model_plane_assets and model_plane_meta is None:
        # Graceful degrade: model plane files are optional in local/dev environments.
        status = STATUS_READY
        reason_code = None
    elif model_plane_meta is not None and not _is_model_plane_version_valid(model_plane_meta):
        status = STATUS_FAILED
        reason_code = RELEASE_MODEL_PLANE_VERSION_INVALID

    result = {
        "stage": S_RELEASE_CHECKLIST,
        "status": status,
        "reason_code": reason_code,
        "milestone": milestone_norm,
        "release_id": str(release_id or "").strip(),
        "assets": asset_list,
        "checksums": checksums,
        "required_services": required_service_rows,
        "flow_refs": {
            "dev": "flows/exports/2026-02-28_dev_skeleton/flow.json",
            "staging": "flows/exports/2026-02-28_staging_skeleton/flow.json",
            "prod": "flows/exports/2026-02-28_prod_skeleton/flow.json",
        },
        "config_snapshot_refs": config_refs,
        "model_plane": model_plane_meta,
        "model_plane_asset_digest": (model_plane_meta.get("digest") if isinstance(model_plane_meta, dict) else None),
        "ts_ms": now_ms,
    }
    return result


def validate_release_bundle(
    *,
    root: str | Path,
    checklist: dict[str, Any],
    rollback_mapping: dict[str, Any],
    known_release_ids: set[str] | None = None,
) -> dict[str, Any]:
    root_path = Path(root).resolve()
    issues: list[dict[str, Any]] = []

    asset_rows = checklist.get("assets") or []
    checksum_rows = checklist.get("checksums") or {}
    for rel_path in asset_rows:
        rel = str(rel_path)
        abs_path = root_path / rel
        expected = str(checksum_rows.get(rel) or "")
        if not abs_path.exists():
            issues.append(_issue(RELEASE_ASSET_MISSING, f"asset missing: {rel}", rel))
            continue
        if _SHA256_RE.match(expected):
            actual = _sha256_of_file(abs_path)
            if actual != expected:
                issues.append(_issue(RELEASE_CHECKSUM_DRIFT, f"checksum drift: {rel}", rel))

    for ref in checklist.get("config_snapshot_refs") or []:
        text = str(ref or "").strip()
        if text and not _CONFIG_SNAPSHOT_RE.match(text):
            issues.append(_issue(RELEASE_CONFIG_SNAPSHOT_INVALID, f"invalid config snapshot ref: {text}", text))

    rollback_target = str(rollback_mapping.get("rollback_target") or "").strip()
    known_ids = set(str(item) for item in (known_release_ids or set()))
    if not rollback_target or (known_ids and rollback_target not in known_ids):
        issues.append(
            _issue(
                RELEASE_ROLLBACK_TARGET_MISSING,
                f"rollback target missing or unknown: {rollback_target or '<empty>'}",
                rollback_target or "<empty>",
            )
        )

    model_plane = checklist.get("model_plane") if isinstance(checklist.get("model_plane"), dict) else None
    if model_plane is not None:
        expected_digest = str(rollback_mapping.get("model_plane_asset_digest") or "").strip()
        current_digest = str(
            model_plane.get("digest")
            or checklist.get("model_plane_asset_digest")
            or ""
        ).strip()
        if not expected_digest:
            issues.append(_issue(RELEASE_ROLLBACK_DIGEST_MISSING, "rollback mapping missing model_plane digest"))
        elif current_digest and expected_digest != current_digest:
            issues.append(
                _issue(
                    RELEASE_MODEL_PLANE_DIGEST_MISMATCH,
                    f"model_plane digest mismatch: expected={expected_digest} actual={current_digest}",
                )
            )

    status = STATUS_FAILED if issues else str(checklist.get("status") or STATUS_READY)
    if status != STATUS_FAILED and str(checklist.get("status") or "") == STATUS_FAILED:
        status = STATUS_FAILED
    reason_code = issues[0]["reason_code"] if issues else (checklist.get("reason_code") or None)
    return {
        "stage": S_RELEASE_CHECKLIST,
        "status": status,
        "reason_code": reason_code,
        "release_id": checklist.get("release_id"),
        "milestone": checklist.get("milestone"),
        "issues": issues,
        "ts_ms": int(time.time() * 1000),
    }


def generate_core_release_manifest(
    *,
    root: str | Path,
    architecture_gates: Iterable[dict[str, Any]],
    acceptance_run_id: str | None = None,
    acceptance_result_ref: str | None = None,
    config_path: str | Path = "configs/release/core_release.json",
    ts_ms: int | None = None,
) -> dict[str, Any]:
    """Freeze the complete Core Release input graph.

    The configuration contains only repository-relative globs.  Every matched
    input is hashed individually and each group receives a deterministic
    aggregate digest.  Runtime output and secrets are never included.
    """

    root_path = Path(root).resolve()
    config_file = _resolve_under_root(root_path, config_path)
    config = json.loads(config_file.read_text(encoding="utf-8-sig"))
    if not isinstance(config, dict):
        raise ValueError("core release config must be an object")
    groups: dict[str, Any] = {}
    for name, patterns in dict(config.get("asset_groups") or {}).items():
        files = _expand_release_patterns(root_path, patterns)
        hashes = {path.relative_to(root_path).as_posix(): _sha256_of_file(path) for path in files}
        groups[str(name)] = {
            "file_count": len(hashes),
            "files": hashes,
            "digest": _digest_mapping(hashes),
        }

    now_ms = int(ts_ms if ts_ms is not None else time.time() * 1000)
    gates = [dict(item) for item in architecture_gates]
    compatibility = validate_registry(root_path, require_empty=True)
    retired_paths = validate_retired_paths_absent(root_path, RETIRED_CORE_PATHS)
    return {
        "schema_version": CORE_RELEASE_MANIFEST_SCHEMA_VERSION,
        "release_id": str(config.get("release_id") or "natsume-ai-v1-core"),
        "generated_at": datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).isoformat(),
        "ts_ms": now_ms,
        "git": _core_git_state(root_path),
        "asset_groups": groups,
        "migration_head": _migration_head(groups.get("migrations", {})),
        "contracts": dict(config.get("contracts") or {}),
        "compatibility_registry": compatibility,
        "retired_paths": retired_paths,
        "architecture_gates": gates,
        "rollback_matrix_ref": str(config.get("rollback_matrix_ref") or ""),
        "sbom_ref": str(config.get("sbom_ref") or ""),
        "acceptance": {
            "run_id": str(acceptance_run_id or ""),
            "result_ref": str(acceptance_result_ref or ""),
        },
    }


def validate_core_release_manifest(
    *,
    root: str | Path,
    manifest: dict[str, Any],
    require_acceptance: bool = False,
) -> dict[str, Any]:
    root_path = Path(root).resolve()
    issues: list[str] = []
    if manifest.get("schema_version") != CORE_RELEASE_MANIFEST_SCHEMA_VERSION:
        issues.append("core release schema version mismatch")
    git = dict(manifest.get("git") or {})
    if not re.fullmatch(r"[0-9a-f]{40}", str(git.get("commit") or "")):
        issues.append("git commit is missing or invalid")
    dirty = list(git.get("dirty_paths") or [])
    if dirty:
        issues.append(f"release source tree is dirty: {dirty[:10]}")
    compatibility = dict(manifest.get("compatibility_registry") or {})
    if compatibility.get("status") != "PASS" or compatibility.get("active_count") != 0:
        issues.append("compatibility registry is not empty and clean")
    retired_paths = validate_retired_paths_absent(root_path, RETIRED_CORE_PATHS)
    if retired_paths.get("status") != "PASS":
        issues.append(f"retired core paths were restored: {retired_paths.get('remaining_paths')}")
    gates = list(manifest.get("architecture_gates") or [])
    if len(gates) != CORE_RELEASE_GATE_COUNT:
        issues.append(f"expected {CORE_RELEASE_GATE_COUNT} architecture gates, got {len(gates)}")
    gate_ids = [gate.get("gate") if isinstance(gate, dict) else None for gate in gates]
    if gate_ids != list(range(1, CORE_RELEASE_GATE_COUNT + 1)):
        issues.append(f"architecture gate ids must be unique and ordered 1-15, got {gate_ids}")
    for index, gate in enumerate(gates, start=1):
        if not isinstance(gate, dict) or str(gate.get("status") or "").upper() != "PASS":
            issues.append(f"architecture gate {index} did not pass")
            continue
        if not list(gate.get("evidence_refs") or []):
            issues.append(f"architecture gate {index} has no evidence references")
        if require_acceptance:
            evidence = list(gate.get("evidence") or [])
            if not evidence:
                issues.append(f"architecture gate {index} has no evaluated evidence receipt")
            elif any(
                not isinstance(row, dict)
                or str(row.get("status") or "").upper() != "PASS"
                or not str(row.get("ref") or "").strip()
                for row in evidence
            ):
                issues.append(f"architecture gate {index} contains non-PASS or unbound evidence")
    for group_name, raw_group in dict(manifest.get("asset_groups") or {}).items():
        group = dict(raw_group or {})
        files = dict(group.get("files") or {})
        actual: dict[str, str] = {}
        for relative, expected in files.items():
            path = _resolve_under_root(root_path, relative)
            if not path.is_file():
                issues.append(f"release input missing: {relative}")
                continue
            digest = _sha256_of_file(path)
            actual[str(relative)] = digest
            if digest != expected:
                issues.append(f"release input checksum drift: {relative}")
        if actual and _digest_mapping(actual) != str(group.get("digest") or ""):
            issues.append(f"release group digest drift: {group_name}")
    if require_acceptance:
        acceptance = dict(manifest.get("acceptance") or {})
        if not acceptance.get("run_id") or not acceptance.get("result_ref"):
            issues.append("program acceptance result is not bound")
    return {
        "schema_version": CORE_RELEASE_MANIFEST_SCHEMA_VERSION,
        "status": "PASS" if not issues else "FAIL",
        "issues": issues,
    }


def generate_core_sbom(*, root: str | Path) -> dict[str, Any]:
    """Generate a deterministic, source-backed CycloneDX component inventory."""

    root_path = Path(root).resolve()
    components: dict[tuple[str, str, str], dict[str, Any]] = {}
    for package_json in sorted(root_path.rglob("package.json")):
        if "node_modules" in package_json.parts:
            continue
        try:
            payload = json.loads(package_json.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        for section in ("dependencies", "devDependencies", "optionalDependencies"):
            for name, version in dict(payload.get(section) or {}).items():
                key = ("npm", str(name), str(version))
                components[key] = {
                    "type": "library",
                    "group": "npm",
                    "name": str(name),
                    "version": str(version),
                    "scope": "required" if section == "dependencies" else "optional",
                    "purl": f"pkg:npm/{name}@{str(version).lstrip('^~')}",
                }
    requirements = root_path / "requirements.txt"
    if requirements.is_file():
        for raw_line in requirements.read_text(encoding="utf-8-sig").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            match = re.match(r"([A-Za-z0-9_.-]+)\s*(?:==|>=|~=|<=|>|<)?\s*([^;\s]+)?", line)
            if not match:
                continue
            name, version = match.group(1), match.group(2) or "unspecified"
            key = ("pypi", name.lower(), version)
            components[key] = {
                "type": "library",
                "group": "pypi",
                "name": name,
                "version": version,
                "purl": f"pkg:pypi/{name}@{version}",
            }
    ordered = [components[key] for key in sorted(components)]
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{hashlib.sha256(json.dumps(ordered, sort_keys=True).encode()).hexdigest()[:32]}",
        "version": 1,
        "metadata": {"component": {"type": "application", "name": "natsume-ai-v1-core"}},
        "components": ordered,
    }


def _resolve_under_root(root: Path, relative: str | Path) -> Path:
    path = (root / Path(relative)).resolve()
    if path != root and root not in path.parents:
        raise ValueError(f"release path escapes repository: {relative}")
    return path


def _expand_release_patterns(root: Path, patterns: Any) -> list[Path]:
    values = [patterns] if isinstance(patterns, str) else list(patterns or [])
    matched: dict[str, Path] = {}
    for pattern in values:
        normalized = str(pattern or "").strip().replace("\\", "/")
        if not normalized or normalized.startswith("/") or ".." in Path(normalized).parts:
            raise ValueError(f"invalid release asset pattern: {pattern}")
        for path in root.glob(normalized):
            if path.is_file() and ".git" not in path.parts and "node_modules" not in path.parts:
                matched[path.relative_to(root).as_posix()] = path.resolve()
    return [matched[key] for key in sorted(matched)]


def _digest_mapping(value: dict[str, str]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _migration_head(group: dict[str, Any]) -> str | None:
    files = list(dict(group.get("files") or {}))
    versions: list[tuple[int, str]] = []
    for path in files:
        match = re.search(r"/(\d{4})[^/]*\.(?:sql|cypher)$", str(path))
        if match:
            versions.append((int(match.group(1)), str(path)))
    return max(versions)[1] if versions else None


def _core_git_state(root: Path) -> dict[str, Any]:
    commit = _resolve_git_commit_hash(root)
    proc = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    ignored_outputs = (
        "docs/50_release/core/",
        "host_brain/app/static/monitor/",
    )
    dirty_paths: list[str] = []
    if proc.returncode == 0:
        for line in str(proc.stdout or "").splitlines():
            path = line[3:].strip().replace("\\", "/")
            if path and not path.startswith(ignored_outputs):
                dirty_paths.append(path)
    return {"commit": commit, "dirty_paths": sorted(dirty_paths)}


def _load_model_plane_meta(root: str | Path) -> dict[str, Any] | None:
    root_path = Path(root).resolve()
    profiles_rel = Path("configs/model_plane/model_profiles.yaml")
    registry_rel = Path("configs/model_plane/node_registry.yaml")
    profiles_path = root_path / profiles_rel
    registry_path = root_path / registry_rel
    if not profiles_path.exists() or not registry_path.exists():
        return None

    profile_version = _extract_version_from_text(profiles_path.read_text(encoding="utf-8-sig"))
    registry_version = _extract_version_from_text(registry_path.read_text(encoding="utf-8-sig"))
    asset_digests = {
        str(profiles_rel).replace("\\", "/"): _sha256_of_file(profiles_path),
        str(registry_rel).replace("\\", "/"): _sha256_of_file(registry_path),
    }
    digest_payload = json.dumps(asset_digests, ensure_ascii=False, sort_keys=True).encode("utf-8")
    digest = f"sha256:{hashlib.sha256(digest_payload).hexdigest()}"
    return {
        "profile_version": profile_version,
        "node_registry_version": registry_version,
        "assets": sorted(asset_digests.keys()),
        "asset_digests": asset_digests,
        "digest": digest,
    }


def _collect_migration_hashes(*, root_path: Path) -> dict[str, str]:
    rows: dict[str, str] = {}
    for rel_dir in M8_MIGRATION_DIRS:
        dir_path = root_path / rel_dir
        if not dir_path.is_dir():
            continue
        for file_path in sorted(dir_path.iterdir()):
            if file_path.suffix.lower() not in _MIGRATION_EXTS:
                continue
            rel = file_path.relative_to(root_path).as_posix()
            rows[rel] = _sha256_of_file(file_path)
    return rows


def _resolve_git_commit_hash(root_path: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root_path),
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    value = str(proc.stdout or "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{40}", value):
        return value
    return None


def _sha256_of_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            hasher.update(chunk)
    return f"sha256:{hasher.hexdigest()}"


def _hash_file_relaxed(path: Path) -> str:
    if path.exists():
        return _sha256_of_file(path)
    return "sha256:" + ("0" * 64)


def _extract_version_from_text(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lower = stripped.lower()
        if lower.startswith("version:") or "version" in lower:
            match = _VERSION_RE.search(stripped)
            if match:
                return match.group(0)
    match = _VERSION_RE.search(text)
    if match:
        return match.group(0)
    return None


def _is_model_plane_version_valid(model_plane_meta: dict[str, Any]) -> bool:
    profile_version = str(model_plane_meta.get("profile_version") or "").strip()
    registry_version = str(model_plane_meta.get("node_registry_version") or "").strip()
    return bool(_VERSION_RE.fullmatch(profile_version) and _VERSION_RE.fullmatch(registry_version))


def _issue(reason_code: str, detail: str, failed_artifact: str | None = None) -> dict[str, Any]:
    return {
        "stage": S_RELEASE_CHECKLIST,
        "reason_code": str(reason_code),
        "detail": str(detail),
        "failed_artifact": failed_artifact,
    }


__all__ = [
    "CORE_RELEASE_GATE_COUNT",
    "CORE_RELEASE_MANIFEST_SCHEMA_VERSION",
    "CORE_RELEASE_SBOM_SCHEMA_VERSION",
    "M8_MIGRATION_DIRS",
    "S_RELEASE_CHECKLIST",
    "STATUS_FAILED",
    "STATUS_READY",
    "_MIGRATION_EXTS",
    "_load_model_plane_meta",
    "build_release_checklist",
    "generate_m8_manifest",
    "generate_core_release_manifest",
    "generate_core_sbom",
    "validate_core_release_manifest",
    "validate_release_bundle",
]
