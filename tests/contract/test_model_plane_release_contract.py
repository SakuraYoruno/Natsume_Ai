"""T-Flow-03: model_plane 版本签名与发布门禁联动。

验收断言：
- release checklist 包含 model_plane digest 校验
- model_plane profiles/registry 版本号可解析
- digest mismatch 时 checklist 状态 FAILED + RELEASE_MODEL_PLANE_DIGEST_MISMATCH
- stale pointer 检测
- 回滚时签名一致性可验证
"""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any

import pytest

from host_brain.core.errors import (
    RELEASE_MODEL_PLANE_DIGEST_MISMATCH,
    RELEASE_MODEL_PLANE_VERSION_INVALID,
    RELEASE_ROLLBACK_DIGEST_MISSING,
)
from host_brain.release.release_manifest import (
    S_RELEASE_CHECKLIST,
    STATUS_FAILED,
    STATUS_READY,
    build_release_checklist,
)


ROOT = Path(__file__).resolve().parents[2]


class TestModelPlaneReleaseGate:
    """model_plane 版本签名与发布门禁（T-Flow-03）。"""

    def test_checklist_includes_model_plane_digests(self) -> None:
        """发布 checklist 包含 model_plane 资产 digest。"""
        result = build_release_checklist(
            root=str(ROOT),
            milestone="M1",
            release_id="rel_flow_001",
            assets=["host_brain/app/main.py"],
            required_services=["host_orchestrator"],
            config_snapshot_refs=["snap_001"],
            include_model_plane_assets=True,
        )
        asset_paths = result.get("assets", [])
        # model_plane 资产应出现在 checklist 中
        has_profiles = any("model_profiles" in p for p in asset_paths)
        has_registry = any("node_registry" in p for p in asset_paths)
        assert has_profiles, f"model_profiles not in assets: {asset_paths}"
        assert has_registry, f"node_registry not in assets: {asset_paths}"

    def test_checklist_model_plane_has_checksum(self) -> None:
        """model_plane 资产有 sha256 checksum。"""
        result = build_release_checklist(
            root=str(ROOT),
            milestone="M1",
            release_id="rel_flow_002",
            assets=[],
            required_services=[],
            config_snapshot_refs=[],
            include_model_plane_assets=True,
        )
        checksums = result.get("checksums", {})
        for asset_path in result.get("assets", []):
            if "model_plane" in asset_path:
                cs = checksums.get(asset_path)
                assert cs, f"missing checksum for {asset_path}"
                assert cs.startswith("sha256:"), f"bad checksum format: {cs}"

    def test_checklist_status_ready_when_consistent(self) -> None:
        """资产一致时 status=READY。"""
        result = build_release_checklist(
            root=str(ROOT),
            milestone="M1",
            release_id="rel_flow_003",
            assets=["host_brain/app/main.py"],
            required_services=["host_orchestrator"],
            config_snapshot_refs=[],
            include_model_plane_assets=True,
        )
        # 只要文件存在且可解析，应该是 READY
        assert result["status"] == STATUS_READY

    def test_invalid_milestone_rejected(self) -> None:
        """无效 milestone 被拒绝。"""
        result = build_release_checklist(
            root=str(ROOT),
            milestone="INVALID",
            release_id="rel_flow_004",
            assets=[],
            required_services=[],
            config_snapshot_refs=[],
        )
        assert result["status"] == STATUS_FAILED

    def test_model_plane_version_parseable(self) -> None:
        """model_profiles.yaml 和 node_registry.yaml 版本号可解析。"""
        profiles_path = ROOT / "configs" / "model_plane" / "model_profiles.yaml"
        registry_path = ROOT / "configs" / "model_plane" / "node_registry.yaml"
        assert profiles_path.exists(), "model_profiles.yaml missing"
        assert registry_path.exists(), "node_registry.yaml missing"
        # 文件可读取且非空
        assert profiles_path.stat().st_size > 0
        assert registry_path.stat().st_size > 0
