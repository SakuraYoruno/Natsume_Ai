"""Release manifest 辅助函数。

从 release_manifest.py 拆分，包含哈希、文件读取和验证工具函数。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Mapping

logger = logging.getLogger(__name__)

from host_brain.core.errors import (
    RELEASE_MODEL_PLANE_DIGEST_MISMATCH,
    RELEASE_MODEL_PLANE_VERSION_INVALID,
)

ALLOWED_FLOW_ENVS = ("dev", "staging", "prod")
CHECKSUM_PREFIX = "sha256:"
MODEL_PLANE_PROFILES_PATH = "configs/model_plane/model_profiles.yaml"
MODEL_PLANE_NODE_REGISTRY_PATH = "configs/model_plane/node_registry.yaml"
_MIGRATION_EXTS = frozenset({".sql", ".cypher"})
M9_MIGRATION_DIRS = (
    "host_brain/audit/migrations",
    "host_brain/bond/migrations",
)
M9_CANON_ASSET_DIRS = (
    "host_brain/canon/world",
)
_CANON_EXTS = frozenset({".json"})
S_RELEASE_CHECKLIST = "S_RELEASE_CHECKLIST"
_VERSION_PATTERN = re.compile(r"^\s*#\s*版本\s*[：:]\s*(\S+)\s*$")


def _load_active_pointer(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if set(data.keys()) != set(ALLOWED_FLOW_ENVS):
        raise ValueError("active.pointer must contain dev/staging/prod only")
    return {env: str(data[env]) for env in ALLOWED_FLOW_ENVS}


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return CHECKSUM_PREFIX + digest


def _load_model_plane_meta(root: Path) -> dict[str, Any] | None:
    profile_path = root / MODEL_PLANE_PROFILES_PATH
    registry_path = root / MODEL_PLANE_NODE_REGISTRY_PATH

    if not profile_path.exists():
        logger.warning(
            "_load_model_plane_meta: %s not found, skipping model_plane assets",
            MODEL_PLANE_PROFILES_PATH,
        )
        return None
    if not registry_path.exists():
        logger.warning(
            "_load_model_plane_meta: %s not found, skipping model_plane assets",
            MODEL_PLANE_NODE_REGISTRY_PATH,
        )
        return None

    profile_text = profile_path.read_text(encoding="utf-8-sig")
    registry_text = registry_path.read_text(encoding="utf-8-sig")

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


def _get_git_commit_hash(repo_root: Path) -> str | None:
    """Read git commit hash from .git/HEAD (no subprocess dependency)."""
    try:
        head_file = repo_root / ".git" / "HEAD"
        if not head_file.exists():
            return None
        head_text = head_file.read_text(encoding="utf-8-sig").strip()
        if head_text.startswith("ref: "):
            ref_path = repo_root / ".git" / head_text[5:].strip()
            if ref_path.exists():
                return ref_path.read_text(encoding="utf-8-sig").strip()
            return None
        if len(head_text) == 40 and all(c in "0123456789abcdef" for c in head_text.lower()):
            return head_text
        return None
    except Exception:
        return None
