"""T-E2E-02: 执行链路 Smoke Test（approval_token + undo）。

验收标准（DevSpec §18.2.1）：
- 执行链路完整：APPROVE → approval_token → skill_exec → manifest_ref
- undo 后文件恢复
- vault_gc_log 不删有效 undo 窗口材料
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "e2e"


class TestExecChainE2E:
    """T-E2E-02: 执行链路 + undo 端到端 Smoke。"""

    def test_exec_chain_input_fixture_valid(self):
        """执行链路输入 fixture 为合法 JSON，包含必须字段。"""
        path = FIXTURES_DIR / "exec_chain_input.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "request_id" in data
        assert "approval_token" in data
        assert "undo_token" in data
        intent = data.get("intent") or {}
        assert "action" in intent
        assert "risk_level" in intent

    def test_approval_token_schema(self):
        """approval_token 结构：绑定 intent_id/skill_name/session_id/seq。"""
        from host_brain.governor.tokens import TokenService

        svc = TokenService()
        token_ref = svc.issue_approval_token(
            intent_id="intent-exec-001",
            skill_name="desktop_note",
            session_id="sess-exec-001",
            seq=1,
        )
        assert isinstance(token_ref, str)
        assert token_ref.startswith("approval::")

        # 验证 token 绑定正确
        result = svc.validate_bound_token(
            token_ref,
            expected_type="approval",
            intent_id="intent-exec-001",
            skill_name="desktop_note",
            session_id="sess-exec-001",
            seq=1,
        )
        assert result.valid is True
        assert result.token is not None
        assert result.token.intent_id == "intent-exec-001"
        assert result.token.skill_name == "desktop_note"

    def test_exec_chain_audit_trail(self):
        """执行链路审计：intent_log → governor_log → skill_exec_log 可串联。"""
        from host_brain.audit.sqlite_ledger import SQLiteLedger
        from host_brain.core.ids import new_request_id

        ledger = SQLiteLedger(db_path=":memory:")
        request_id = new_request_id()
        intent_id = "intent-exec-chain-001"

        # 写入 intent_log
        ledger.log_intent({
            "request_id": request_id,
            "intent_id": intent_id,
            "session_id": "sess-exec-001",
            "ts_ms": 2000,
            "proposer_node_id": "host",
            "proposed_intent_draft_json": json.dumps({"action": "desktop_note"}),
            "lint_result": "ok",
        })

        # 写入 governor_log（APPROVE）
        ledger.log_governor({
            "request_id": request_id,
            "intent_id": intent_id,
            "decision": "APPROVE",
            "reason_code": None,
            "rule_trace": json.dumps([{"gate_id": "R1_TRUST", "hit": False}]),
            "ts_ms": 2001,
        })

        # 写入 skill_exec_log
        ledger.log_skill_exec({
            "request_id": request_id,
            "intent_id": intent_id,
            "skill_name": "desktop_note",
            "result_status": "SUCCESS",
            "result_code": None,
            "manifest_ref": "manifest-exec-001",
            "evidence_ref": "evidence-exec-001",
            "ts_ms": 2002,
        })

        # 断言三联画可串联
        intent_rows = [r for r in ledger.intent_log if r.get("request_id") == request_id]
        gov_rows = [r for r in ledger.governor_log if r.get("request_id") == request_id]
        skill_rows = [r for r in ledger.skill_exec_log if r.get("request_id") == request_id]

        assert len(intent_rows) >= 1
        assert len(gov_rows) >= 1
        assert gov_rows[-1].get("decision") == "APPROVE"
        assert len(skill_rows) >= 1
        assert skill_rows[-1].get("manifest_ref") == "manifest-exec-001"

    def test_vault_gc_does_not_delete_undo_window(self):
        """vault_gc_log 不删有效 undo 窗口材料。"""
        from host_brain.audit.sqlite_ledger import SQLiteLedger
        from host_brain.governor.tokens import TokenService
        from host_brain.lsp.vault import ArtifactVault

        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = SQLiteLedger(db_path=":memory:")
            token_svc = TokenService(undo_ttl_ms=600_000)
            vault = ArtifactVault(root_dir=tmpdir, ledger=ledger)

            # 签发 undo token
            undo_ref = token_svc.issue_undo_token(
                intent_id="intent-exec-001",
                skill_name="desktop_note",
                session_id="sess-exec-001",
                seq=1,
            )

            # 创建 reversible manifest
            result = vault.create_manifest(
                request_id="req-exec-001",
                intent_id="intent-exec-001",
                skill_name="desktop_note",
                reversible=True,
                undo_ref=undo_ref,
                items=[{"kind": "file", "content": "hello", "artifact_id": "art-001"}],
                evidence_refs=["evidence-exec-001"],
                policy_profile_id="default",
            )
            assert result.success is True, f"manifest 创建失败: {result.detail}"

            # 尝试 GC（retention_ms=0 表示立即过期，但 undo 窗口内应被保护）
            gc_result = vault.garbage_collect(
                policy_profile_id="default",
                evidence_refs=["evidence-exec-001"],
                retention_ms=0,
                request_id="req-exec-001",
                token_service=token_svc,
            )

            # 有效 undo 窗口内的 manifest 应被保护，不被 GC 删除
            blocked = gc_result.get("blocked_manifest_ids") or []
            assert len(blocked) >= 1, "有效 undo 窗口内的 manifest 应被保护，不被 GC 删除"

    def test_undo_token_binding(self):
        """undo_token 绑定 intent_id/skill_name，不可串用。"""
        from host_brain.governor.tokens import TokenService

        svc = TokenService()
        token_ref = svc.issue_undo_token(
            intent_id="intent-exec-001",
            skill_name="desktop_note",
            session_id="sess-exec-001",
            seq=1,
        )
        assert token_ref.startswith("undo::")

        # 串用（不同 intent_id）应失败
        result = svc.validate_bound_token(
            token_ref,
            expected_type="undo",
            intent_id="intent-exec-WRONG",
            skill_name="desktop_note",
            session_id="sess-exec-001",
            seq=1,
        )
        assert result.valid is False
        assert result.reason_code == "TOKEN_INVALID"
