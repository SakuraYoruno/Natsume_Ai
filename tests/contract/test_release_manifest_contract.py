"""T-REL-01: M8 发布清单字段完整性 + hash 格式合法 + migration 文件数量一致。

验收标准（DevSpec §18.4.1）：
- m8_release.json 包含所有必须字段
- migration_hashes 中文件数量与实际 migration 文件数一致
- git_commit_hash 格式合法（40 位 hex 或 None）
- generate_m8_manifest() 可正常调用并返回合法结构
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "release"
REPO_ROOT = Path(__file__).parent.parent.parent

MANIFEST_REQUIRED_FIELDS = {
    "milestone",
    "git_commit_hash",
    "migration_hashes",
    "migration_file_count",
    "test_passed_count",
    "latency_baseline_snapshot",
    "service_registry_snapshot",
    "generated_at",
    "ts_ms",
}

_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT_HASH_PATTERN = re.compile(r"^[0-9a-f]{40}$")

EXPECTED_MIGRATION_COUNT = 15  # 14 SQL + 1 Cypher（不含 .gitkeep）


class TestM8ManifestExpectedFixture:
    """T-REL-01-A: m8_manifest_expected.json fixture 字段完整性。"""

    def setup_method(self):
        path = FIXTURES_DIR / "m8_manifest_expected.json"
        self.data = json.loads(path.read_text(encoding="utf-8-sig"))

    def test_required_fields_present(self):
        """所有必须字段均存在。"""
        for field in MANIFEST_REQUIRED_FIELDS:
            assert field in self.data, f"缺少必须字段: {field}"

    def test_milestone_is_m8(self):
        assert self.data["milestone"] == "M8"

    def test_migration_file_count_matches_hashes(self):
        """migration_file_count 与 migration_hashes 键数量一致。"""
        hashes = self.data["migration_hashes"]
        count = self.data["migration_file_count"]
        assert len(hashes) == count, (
            f"migration_file_count={count} 与 migration_hashes 键数 {len(hashes)} 不一致"
        )

    def test_migration_file_count_expected(self):
        """migration_file_count 等于期望值（14 SQL + 1 Cypher）。"""
        assert self.data["migration_file_count"] == EXPECTED_MIGRATION_COUNT

    def test_test_passed_count_positive(self):
        assert self.data["test_passed_count"] > 0

    def test_generated_at_is_string(self):
        assert isinstance(self.data["generated_at"], str)
        assert len(self.data["generated_at"]) > 0

    def test_service_registry_snapshot_is_list(self):
        assert isinstance(self.data["service_registry_snapshot"], list)

    def test_latency_baseline_snapshot_structure(self):
        baseline = self.data.get("latency_baseline_snapshot")
        if baseline is not None:
            assert "sample_count" in baseline
            assert "sampled_at" in baseline


class TestGenerateM8Manifest:
    """T-REL-01-B: generate_m8_manifest() 函数单元测试。"""

    def test_generate_m8_manifest_returns_dict(self):
        """generate_m8_manifest() 返回合法 dict。"""
        from host_brain.release.release_manifest import generate_m8_manifest

        result = generate_m8_manifest(
            root=REPO_ROOT,
            test_passed_count=914,
        )
        assert isinstance(result, dict)

    def test_generate_m8_manifest_required_fields(self):
        """generate_m8_manifest() 返回包含所有必须字段的 dict。"""
        from host_brain.release.release_manifest import generate_m8_manifest

        result = generate_m8_manifest(
            root=REPO_ROOT,
            test_passed_count=914,
        )
        for field in MANIFEST_REQUIRED_FIELDS:
            assert field in result, f"缺少必须字段: {field}"

    def test_generate_m8_manifest_milestone(self):
        from host_brain.release.release_manifest import generate_m8_manifest

        result = generate_m8_manifest(root=REPO_ROOT, test_passed_count=914)
        assert result["milestone"] == "M8"

    def test_generate_m8_manifest_migration_hashes_format(self):
        """migration_hashes 中每个 hash 格式为 sha256:<64 hex>。"""
        from host_brain.release.release_manifest import generate_m8_manifest

        result = generate_m8_manifest(root=REPO_ROOT, test_passed_count=914)
        hashes = result["migration_hashes"]
        assert isinstance(hashes, dict)
        assert len(hashes) > 0, "migration_hashes 不能为空"

        for rel_path, hash_val in hashes.items():
            assert _SHA256_PATTERN.match(hash_val), (
                f"{rel_path}: hash 格式不合法: {hash_val!r}"
            )

    def test_generate_m8_manifest_migration_count_matches_disk(self):
        """migration_file_count 与磁盘上实际 migration 文件数一致。"""
        from host_brain.release.release_manifest import (
            M8_MIGRATION_DIRS,
            _MIGRATION_EXTS,
            generate_m8_manifest,
        )

        # 统计磁盘上实际文件数
        actual_count = 0
        for migration_dir in M8_MIGRATION_DIRS:
            dir_path = REPO_ROOT / migration_dir
            if not dir_path.is_dir():
                continue
            for f in dir_path.iterdir():
                if f.suffix.lower() in _MIGRATION_EXTS:
                    actual_count += 1

        result = generate_m8_manifest(root=REPO_ROOT, test_passed_count=914)
        assert result["migration_file_count"] == actual_count, (
            f"migration_file_count={result['migration_file_count']} "
            f"与磁盘实际文件数 {actual_count} 不一致"
        )

    def test_generate_m8_manifest_git_hash_format(self):
        """git_commit_hash 为 40 位 hex 或 None。"""
        from host_brain.release.release_manifest import generate_m8_manifest

        result = generate_m8_manifest(root=REPO_ROOT, test_passed_count=914)
        git_hash = result["git_commit_hash"]
        if git_hash is not None:
            assert _GIT_HASH_PATTERN.match(git_hash), (
                f"git_commit_hash 格式不合法: {git_hash!r}"
            )

    def test_generate_m8_manifest_with_latency_snapshot(self):
        """传入 latency_baseline_snapshot 时正确写入。"""
        from host_brain.release.release_manifest import generate_m8_manifest

        snapshot = {
            "retrieval_p50_ms": 180,
            "retrieval_p95_ms": 420,
            "sample_count": 100,
        }
        result = generate_m8_manifest(
            root=REPO_ROOT,
            test_passed_count=914,
            latency_baseline_snapshot=snapshot,
        )
        assert result["latency_baseline_snapshot"] == snapshot

    def test_generate_m8_manifest_with_service_registry(self):
        """传入 service_registry_snapshot 时正确写入。"""
        from host_brain.release.release_manifest import generate_m8_manifest

        registry = [
            {"service_id": "host.audit.sqlite_ledger", "owner": "host", "required": True},
        ]
        result = generate_m8_manifest(
            root=REPO_ROOT,
            test_passed_count=914,
            service_registry_snapshot=registry,
        )
        assert result["service_registry_snapshot"] == registry

    def test_generate_m8_manifest_serializable(self):
        """generate_m8_manifest() 返回值可序列化为 JSON。"""
        from host_brain.release.release_manifest import generate_m8_manifest

        result = generate_m8_manifest(root=REPO_ROOT, test_passed_count=914)
        json_str = json.dumps(result)
        parsed = json.loads(json_str)
        assert isinstance(parsed, dict)
