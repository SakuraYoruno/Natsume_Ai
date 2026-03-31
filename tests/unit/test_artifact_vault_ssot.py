"""M31-A：Artifact Vault SSOT 接口测试（T-VAULT-SSOT-01~03）。

DevSpec 8.0/8.8：recall_fusion._search_artifact_vault() 必须通过
self.artifact_vault.search() SSOT 接口读取，不得直接访问 ledger.vault_manifest_index。
"""
from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from host_brain.retrieval.recall_fusion import RecallFusionServiceContract


# ── T-VAULT-SSOT-01：静态扫描断言 ──────────────────────────────────────────

def test_vault_ssot_01_no_direct_ledger_access():
    """T-VAULT-SSOT-01：_search_artifact_vault() 调用路径中不出现 ledger.vault_manifest_index（代码行，排除注释）。"""
    source_path = Path(__file__).parent.parent.parent / "host_brain" / "retrieval" / "recall_fusion.py"
    source = source_path.read_text(encoding="utf-8-sig")
    tree = ast.parse(source)

    # 找到 _search_artifact_vault 方法体
    method_node = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "_search_artifact_vault":
                method_node = node
                break

    assert method_node is not None, "_search_artifact_vault 方法未找到"

    # 提取方法体的代码行（排除注释行）
    source_lines = source.split("\n")
    start_line = method_node.lineno - 1  # 0-indexed
    end_line = method_node.end_lineno    # 0-indexed exclusive
    method_lines = source_lines[start_line:end_line]

    # 过滤掉注释行（以 # 开头的行，去除前导空格后）
    code_lines = [line for line in method_lines if not line.strip().startswith("#")]
    code_text = "\n".join(code_lines)

    assert "vault_manifest_index" not in code_text, (
        "T-VAULT-SSOT-01 FAILED: _search_artifact_vault() 代码中仍直接访问 ledger.vault_manifest_index，"
        "违反 DevSpec 8.0/8.8 SSOT 接口约束"
    )


# ── T-VAULT-SSOT-02：artifact_vault=None 时返回空列表 ──────────────────────

def test_vault_ssot_02_none_vault_returns_empty():
    """T-VAULT-SSOT-02：self.artifact_vault=None 时，_search_artifact_vault() 返回空列表，不抛异常。"""
    svc = RecallFusionServiceContract(artifact_vault=None)
    result = svc._search_artifact_vault(query="test query", top_k=5)
    assert result == [], f"T-VAULT-SSOT-02 FAILED: 期望空列表，实际返回 {result}"


# ── T-VAULT-SSOT-03：artifact_vault 提供有效数据时正确返回候选列表 ──────────

def test_vault_ssot_03_valid_vault_returns_candidates():
    """T-VAULT-SSOT-03：self.artifact_vault 提供有效数据时，_search_artifact_vault() 正确返回候选列表。"""
    mock_vault = MagicMock()
    mock_vault.search.return_value = [
        {
            "manifest_id": "manifest_abc123",
            "artifact_id": "artifact_xyz",
            "kind": "text",
            "hash": "deadbeef",
            "created_ts": 1700000000000,
            "evidence_refs": ["evd://test/001"],
            "integrity_digest": "deadbeef",
            "intent_id": "intent_001",
            "policy_profile_id": "policy_default",
        }
    ]

    svc = RecallFusionServiceContract(artifact_vault=mock_vault)
    result = svc._search_artifact_vault(query="manifest_abc123", top_k=5)

    # 验证调用了 SSOT 接口
    mock_vault.search.assert_called_once()

    # 验证返回了候选
    assert len(result) >= 1, "T-VAULT-SSOT-03 FAILED: 期望至少 1 个候选"
    candidate = result[0]
    assert candidate["memory_id"] == "manifest_abc123"
    assert candidate["metadata"]["store_id"] == "artifact_vault"
    assert candidate["score"] > 0.0
