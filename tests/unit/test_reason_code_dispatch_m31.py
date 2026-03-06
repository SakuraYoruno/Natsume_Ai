"""M31-B：各库 reason_code 专属分发测试（T-REASON-01~06）。

DevSpec 8.1：decision=REJECT 时 reason_code 必须非空且来自稳定枚举，
各库异常必须返回专属 reason_code，不得统一归并为 RETR_RECALL_UNAVAILABLE。
"""
from __future__ import annotations

import pytest

from host_brain.core.errors import (
    CHROMA_ENDPOINT_UNAVAILABLE,
    RETR_BOND_UNAVAILABLE,
    RETR_LEDGER_UNAVAILABLE,
    RETR_RECALL_UNAVAILABLE,
    RETR_STYLE_UNAVAILABLE,
    RETR_VAULT_UNAVAILABLE,
    RETR_WORLD_UNAVAILABLE,
)
from host_brain.retrieval.recall_fusion import _map_route_exception


# ── T-REASON-01：sqlite_ledger 异常 → RETR_LEDGER_UNAVAILABLE ──────────────

def test_reason_01_sqlite_ledger():
    """T-REASON-01：store_id="sqlite_ledger" 异常 → RETR_LEDGER_UNAVAILABLE。"""
    exc = RuntimeError("sqlite ledger unavailable")
    result = _map_route_exception(exc, store_id="sqlite_ledger")
    assert result == RETR_LEDGER_UNAVAILABLE, (
        f"T-REASON-01 FAILED: 期望 {RETR_LEDGER_UNAVAILABLE}，实际 {result}"
    )


# ── T-REASON-02：neo4j_bond 异常 → RETR_BOND_UNAVAILABLE ──────────────────

def test_reason_02_neo4j_bond():
    """T-REASON-02：store_id="neo4j_bond" 异常 → RETR_BOND_UNAVAILABLE。"""
    exc = RuntimeError("neo4j connection refused")
    result = _map_route_exception(exc, store_id="neo4j_bond")
    assert result == RETR_BOND_UNAVAILABLE, (
        f"T-REASON-02 FAILED: 期望 {RETR_BOND_UNAVAILABLE}，实际 {result}"
    )


# ── T-REASON-03：style_canon 异常 → RETR_STYLE_UNAVAILABLE ────────────────

def test_reason_03_style_canon():
    """T-REASON-03：store_id="style_canon" 异常 → RETR_STYLE_UNAVAILABLE。"""
    exc = RuntimeError("style store unavailable")
    result = _map_route_exception(exc, store_id="style_canon")
    assert result == RETR_STYLE_UNAVAILABLE, (
        f"T-REASON-03 FAILED: 期望 {RETR_STYLE_UNAVAILABLE}，实际 {result}"
    )


# ── T-REASON-04：world_canon 异常 → RETR_WORLD_UNAVAILABLE ────────────────

def test_reason_04_world_canon():
    """T-REASON-04：store_id="world_canon" 异常 → RETR_WORLD_UNAVAILABLE。"""
    exc = RuntimeError("world store unavailable")
    result = _map_route_exception(exc, store_id="world_canon")
    assert result == RETR_WORLD_UNAVAILABLE, (
        f"T-REASON-04 FAILED: 期望 {RETR_WORLD_UNAVAILABLE}，实际 {result}"
    )


# ── T-REASON-05：artifact_vault 异常 → RETR_VAULT_UNAVAILABLE ─────────────

def test_reason_05_artifact_vault():
    """T-REASON-05：store_id="artifact_vault" 异常 → RETR_VAULT_UNAVAILABLE。"""
    exc = RuntimeError("vault unavailable")
    result = _map_route_exception(exc, store_id="artifact_vault")
    assert result == RETR_VAULT_UNAVAILABLE, (
        f"T-REASON-05 FAILED: 期望 {RETR_VAULT_UNAVAILABLE}，实际 {result}"
    )


# ── T-REASON-06：episodic_vector 异常 → 原有 Chroma 错误码（向后兼容）──────

def test_reason_06_episodic_vector_backward_compat():
    """T-REASON-06：store_id="episodic_vector" 异常 → 原有 CHROMA_ENDPOINT_UNAVAILABLE（向后兼容）。"""
    exc = RuntimeError("chroma endpoint unreachable")
    result = _map_route_exception(exc, store_id="episodic_vector")
    assert result == CHROMA_ENDPOINT_UNAVAILABLE, (
        f"T-REASON-06 FAILED: 期望 {CHROMA_ENDPOINT_UNAVAILABLE}，实际 {result}"
    )


def test_reason_06_none_store_id_defaults_to_episodic():
    """T-REASON-06 补充：store_id=None 时默认按 episodic_vector 处理（向后兼容）。"""
    exc = RuntimeError("unknown error")
    result = _map_route_exception(exc, store_id=None)
    # store_id=None 时走 episodic_vector 分支，返回 Chroma 错误码
    assert result == CHROMA_ENDPOINT_UNAVAILABLE, (
        f"T-REASON-06 补充 FAILED: 期望 {CHROMA_ENDPOINT_UNAVAILABLE}，实际 {result}"
    )
