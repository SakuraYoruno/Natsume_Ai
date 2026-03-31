"""M31-D：artifact_id 字段修复测试（T-VAULT-ID-01~02）。

DevSpec 8.6：manifest 最小字段要求 artifact_id 必须从 vault 数据中读取，不得硬编码 None。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from host_brain.retrieval.recall_fusion import RecallFusionServiceContract


# ── T-VAULT-ID-01：vault 返回含 artifact_id 的 manifest 时，候选结果中 artifact_id 非 None ──

def test_vault_id_01_artifact_id_extracted():
    """T-VAULT-ID-01：vault 返回含 artifact_id 的 manifest 时，候选结果中 artifact_id 非 None。"""
    mock_vault = MagicMock()
    mock_vault.search.return_value = [
        {
            "manifest_id": "manifest_001",
            "artifact_id": "artifact_real_id_001",
            "kind": "text",
            "hash": "sha256:abc",
            "created_ts": 1700000000000,
            "evidence_refs": ["evd://test/001"],
            "integrity_digest": "sha256:abc",
            "intent_id": "intent_001",
            "policy_profile_id": "policy_default",
        }
    ]

    svc = RecallFusionServiceContract(artifact_vault=mock_vault)
    result = svc._search_artifact_vault(query="manifest_001", top_k=5)

    assert len(result) >= 1, "T-VAULT-ID-01 FAILED: 期望至少 1 个候选"
    candidate = result[0]
    artifact_id = candidate["metadata"].get("artifact_id")
    assert artifact_id is not None, (
        "T-VAULT-ID-01 FAILED: artifact_id 为 None，期望从 vault 数据中提取"
    )
    assert artifact_id == "artifact_real_id_001", (
        f"T-VAULT-ID-01 FAILED: artifact_id 期望 'artifact_real_id_001'，实际 {artifact_id!r}"
    )


# ── T-VAULT-ID-02：vault 返回不含 artifact_id 的旧格式数据时，降级为空字符串，不报错 ──

def test_vault_id_02_missing_artifact_id_degrades_gracefully():
    """T-VAULT-ID-02：vault 返回不含 artifact_id 的旧格式数据时，artifact_id 降级为 None，不报错。"""
    mock_vault = MagicMock()
    mock_vault.search.return_value = [
        {
            "manifest_id": "manifest_002",
            # 不含 artifact_id 字段（旧格式）
            "hash": "sha256:def",
            "created_ts": 1700000000001,
            "evidence_refs": ["evd://test/002"],
            "integrity_digest": "sha256:def",
            "intent_id": "intent_002",
        }
    ]

    svc = RecallFusionServiceContract(artifact_vault=mock_vault)
    # 不应抛出异常
    result = svc._search_artifact_vault(query="manifest_002", top_k=5)

    assert len(result) >= 1, "T-VAULT-ID-02 FAILED: 期望至少 1 个候选"
    candidate = result[0]
    artifact_id = candidate["metadata"].get("artifact_id")
    # 旧格式数据：artifact_id 应降级为 None（空字符串转为 None）
    assert artifact_id is None or artifact_id == "", (
        f"T-VAULT-ID-02 FAILED: artifact_id 期望 None 或空字符串，实际 {artifact_id!r}"
    )
