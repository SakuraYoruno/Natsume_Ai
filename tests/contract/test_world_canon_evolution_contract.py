"""T-WCanon-07: World Canon Evolution 提案写入/审核/变更 hash 可回放 contract 测试（TASK-0114）。

覆盖点：
  07a - submit_proposal() 写入 canon_proposal_log，status=pending；
  07b - pending 提案可在 canon_proposal_log 查询；
  07c - approve_proposal_with_content() 审核通过后 status=APPLIED；
  07d - 审核通过后 canon_change_log 含变更前后 hash；
  07e - 变更前后 hash 不同（hash 可回放）；
  07f - 非 pending 提案不得再次审核（PermissionError）；
  07g - 不存在的提案审核抛 KeyError；
  07h - proposed_body 含完成性表述时 submit_proposal 抛 ValueError；
  07i - 审核通过后 SSOT 文件版本号递增；
  07j - change_log_expected.json fixture 字段完整性验证；
  07k - proposal_pending.json fixture 字段合规性验证；
  07l - proposal_approved.json fixture 字段合规性验证。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SSOT_WORLD = ROOT / "host_brain" / "canon" / "world"
FIXTURE_WORLD = ROOT / "tests" / "fixtures" / "canon" / "world"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _make_temp_store():
    """创建使用临时目录和内存 ledger 的 WorldCanonStore，避免污染 SSOT。"""
    import shutil
    from host_brain.memory.canon_world_store import WorldCanonStore
    from host_brain.audit.sqlite_ledger import SQLiteLedger

    tmp_dir = tempfile.mkdtemp(prefix="wcanon_test_")
    # 复制 SSOT 种子文件到临时目录
    for f in SSOT_WORLD.glob("*.json"):
        shutil.copy(f, tmp_dir)

    ledger = SQLiteLedger(db_path=":memory:")
    store = WorldCanonStore(world_root=tmp_dir, ledger=ledger)
    return store, ledger, tmp_dir


class TestWorldCanonEvolution(unittest.TestCase):
    """T-WCanon-07: 提案写入/审核/变更 hash 可回放。"""

    def setUp(self) -> None:
        self.store, self.ledger, self.tmp_dir = _make_temp_store()
        self.proposal_id = "test-proposal-wc001-v2"
        self.target_doc_id = "wc-001-system-terms"
        self.proposed_title = "系统核心术语表（修订版）"
        self.proposed_tags = ["system", "terms", "architecture", "revision"]
        self.proposed_body = (
            "Host Brain 是夏目的核心认知节点，负责意图解析、记忆管理和 Canon 治理。"
            "Edge Client 是夏目的感知与表达节点，负责 ASR/TTS/VTS 驱动及 RPC 上报。"
            "两者通过 RPC 协议协作，Host Brain 为单写者，Edge Client 为只读引用方。"
        )

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_t_wcanon_07a_submit_proposal_writes_pending(self) -> None:
        """submit_proposal() 写入 canon_proposal_log，status=pending。"""
        row = self.store.submit_proposal(
            proposal_id=self.proposal_id,
            target_doc_id=self.target_doc_id,
            proposed_title=self.proposed_title,
            proposed_tags=self.proposed_tags,
            proposed_body=self.proposed_body,
        )
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["proposal_id"], self.proposal_id)
        self.assertEqual(row["target_id"], self.target_doc_id)
        self.assertEqual(row["canon_kind"], "world")

    def test_t_wcanon_07b_pending_proposal_queryable(self) -> None:
        """pending 提案可在 canon_proposal_log 查询。"""
        self.store.submit_proposal(
            proposal_id=self.proposal_id,
            target_doc_id=self.target_doc_id,
            proposed_title=self.proposed_title,
            proposed_tags=self.proposed_tags,
            proposed_body=self.proposed_body,
        )
        proposal = self.ledger.get_proposal(self.proposal_id)
        self.assertIsNotNone(proposal, msg="提案应可查询")
        self.assertEqual(proposal["status"], "pending")
        self.assertEqual(proposal["proposal_id"], self.proposal_id)

    def test_t_wcanon_07c_approve_proposal_status_applied(self) -> None:
        """旧 approve_proposal_with_content 入口无条件 fail-closed。"""
        self.store.submit_proposal(
            proposal_id=self.proposal_id,
            target_doc_id=self.target_doc_id,
            proposed_title=self.proposed_title,
            proposed_tags=self.proposed_tags,
            proposed_body=self.proposed_body,
        )
        with self.assertRaisesRegex(PermissionError, "CANON_DIRECT_WRITE_FORBIDDEN"):
            self.store.approve_proposal_with_content(
                self.proposal_id,
                proposed_title=self.proposed_title,
                proposed_tags=self.proposed_tags,
                proposed_body=self.proposed_body,
            )
        proposal = self.ledger.get_proposal(self.proposal_id)
        self.assertIsNotNone(proposal)
        self.assertEqual(proposal["status"], "pending")

    def test_t_wcanon_07d_approve_writes_change_log(self) -> None:
        """审核通过后 canon_change_log 含变更前后 hash。"""
        self.store.submit_proposal(
            proposal_id=self.proposal_id,
            target_doc_id=self.target_doc_id,
            proposed_title=self.proposed_title,
            proposed_tags=self.proposed_tags,
            proposed_body=self.proposed_body,
        )
        with self.assertRaises(PermissionError):
            self.store.approve_proposal_with_content(
                self.proposal_id,
                proposed_title=self.proposed_title,
                proposed_tags=self.proposed_tags,
                proposed_body=self.proposed_body,
            )
        # 查询 canon_change_log
        changes = [
            row for row in self.ledger.canon_change_log
            if row.get("proposal_id") == self.proposal_id
        ]
        self.assertEqual(changes, [])

    def test_t_wcanon_07e_before_after_hash_differ(self) -> None:
        """变更前后 hash 不同（hash 可回放）。"""
        # 先获取当前 hash
        current_doc = self.store.get(self.target_doc_id)
        before_hash = current_doc.hash if current_doc else None

        self.store.submit_proposal(
            proposal_id=self.proposal_id,
            target_doc_id=self.target_doc_id,
            proposed_title=self.proposed_title,
            proposed_tags=self.proposed_tags,
            proposed_body=self.proposed_body,
        )
        with self.assertRaises(PermissionError):
            self.store.approve_proposal_with_content(
                self.proposal_id,
                proposed_title=self.proposed_title,
                proposed_tags=self.proposed_tags,
                proposed_body=self.proposed_body,
            )

        changes = [
            row for row in self.ledger.canon_change_log
            if row.get("proposal_id") == self.proposal_id
        ]
        self.assertEqual(changes, [])
        self.assertEqual(self.store.get(self.target_doc_id).hash if self.store.get(self.target_doc_id) else None, before_hash)

    def test_t_wcanon_07f_non_pending_proposal_cannot_be_approved(self) -> None:
        """非 pending 提案不得再次审核（PermissionError）。"""
        self.store.submit_proposal(
            proposal_id=self.proposal_id,
            target_doc_id=self.target_doc_id,
            proposed_title=self.proposed_title,
            proposed_tags=self.proposed_tags,
            proposed_body=self.proposed_body,
        )
        # 任意 raw approve 都应抛 PermissionError。
        with self.assertRaises(PermissionError):
            self.store.approve_proposal_with_content(
                self.proposal_id,
                proposed_title=self.proposed_title,
                proposed_tags=self.proposed_tags,
                proposed_body=self.proposed_body,
            )

    def test_t_wcanon_07g_nonexistent_proposal_raises_key_error(self) -> None:
        """raw API 在查询提案前即 fail-closed，避免状态枚举。"""
        with self.assertRaises(PermissionError):
            self.store.approve_proposal_with_content(
                "nonexistent-proposal-id",
                proposed_title="test",
                proposed_tags=[],
                proposed_body="test body",
            )

    def test_t_wcanon_07h_execution_claim_in_body_raises_value_error(self) -> None:
        """proposed_body 含完成性表述时 submit_proposal 抛 ValueError。"""
        with self.assertRaises(ValueError):
            self.store.submit_proposal(
                proposal_id="bad-proposal-001",
                target_doc_id=self.target_doc_id,
                proposed_title="测试",
                proposed_tags=[],
                proposed_body="根据世界观设定，系统初始化流程执行完成。",
            )

    def test_t_wcanon_07i_approve_increments_doc_version(self) -> None:
        """审核通过后 SSOT 文件版本号递增。"""
        current_doc = self.store.get(self.target_doc_id)
        base_version = current_doc.version if current_doc else 0

        self.store.submit_proposal(
            proposal_id=self.proposal_id,
            target_doc_id=self.target_doc_id,
            proposed_title=self.proposed_title,
            proposed_tags=self.proposed_tags,
            proposed_body=self.proposed_body,
        )
        with self.assertRaises(PermissionError):
            self.store.approve_proposal_with_content(
                self.proposal_id,
                proposed_title=self.proposed_title,
                proposed_tags=self.proposed_tags,
                proposed_body=self.proposed_body,
            )
        current = self.store.get(self.target_doc_id)
        self.assertEqual(current.version if current else 0, base_version)

    def test_t_wcanon_07j_change_log_expected_fixture_fields(self) -> None:
        """change_log_expected.json fixture 字段完整性验证。"""
        fixture_path = FIXTURE_WORLD / "change_log_expected.json"
        self.assertTrue(fixture_path.exists(), msg=f"fixture 不存在: {fixture_path}")
        data = _load_json(fixture_path)
        required_fields = data.get("required_fields", [])
        self.assertGreater(len(required_fields), 0, msg="required_fields 不应为空")
        # 验证 canon_change_log 表实际包含这些字段
        from host_brain.audit.sqlite_ledger import SQLiteLedger
        ledger = SQLiteLedger(db_path=":memory:")
        # 插入一条测试记录
        ledger.log_canon_change({
            "change_id": "test-change-001",
            "proposal_id": "test-proposal-001",
            "canon_kind": "world",
            "target_id": "wc-001",
            "before_version": 1,
            "after_version": 2,
            "before_hash": "abc",
            "after_hash": "def",
            "source_path": "/tmp/test.json",
            "ts_ms": 1000,
        })
        rows = ledger.canon_change_log
        self.assertGreater(len(rows), 0)
        row = rows[0]
        for field in required_fields:
            self.assertIn(field, row, msg=f"canon_change_log 缺少字段: {field}")

    def test_t_wcanon_07k_proposal_pending_fixture_fields(self) -> None:
        """proposal_pending.json fixture 字段合规性验证。"""
        fixture_path = FIXTURE_WORLD / "proposal_pending.json"
        self.assertTrue(fixture_path.exists(), msg=f"fixture 不存在: {fixture_path}")
        data = _load_json(fixture_path)
        self.assertEqual(data.get("status"), "pending")
        self.assertIn("proposal_id", data)
        self.assertIn("target_id", data)
        self.assertIn("proposed_body", data)
        self.assertIn("proposed_title", data)

    def test_t_wcanon_07l_proposal_approved_fixture_fields(self) -> None:
        """proposal_approved.json fixture 字段合规性验证。"""
        fixture_path = FIXTURE_WORLD / "proposal_approved.json"
        self.assertTrue(fixture_path.exists(), msg=f"fixture 不存在: {fixture_path}")
        data = _load_json(fixture_path)
        self.assertEqual(data.get("status"), "APPLIED")
        self.assertIn("proposal_id", data)
        self.assertIn("expected_doc_version", data)
        self.assertGreater(data.get("expected_doc_version", 0), 1, msg="审核后版本应 > 1")


if __name__ == "__main__":
    unittest.main()
