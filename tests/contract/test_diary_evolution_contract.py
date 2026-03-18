"""T-Diary-03/04: 日记 Evolution 提案写入 + QA 拦截 contract 测试（TASK-0115）。

覆盖点：
  T-Diary-03:
    03a - 日记含合规 drift_suggestion 时，canon_proposal_log 写入成功；
    03b - 写入的提案 source_intent_id 可追溯到日记 intent_id；
    03c - 提案 status=pending；
    03d - 提案 proposer_node_id=diary_writer；
    03e - fixture diary_with_drift_suggestion.json 字段合规性验证；

  T-Diary-04:
    04a - drift_suggestion.suggested_body 含伪执行句时，提案被丢弃（返回 None）；
    04b - 日记正常写入（status=EXECUTED），不受 drift_suggestion 失败影响；
    04c - WCANON_PROPOSAL_INVALID 可统计（ledger.log_error 被调用）；
    04d - 无 drift_suggestion 时日记正常写入，evolution_proposal=None；
    04e - fixture diary_drift_suggestion_bad.json 字段合规性验证；
    04f - _extract_drift_suggestion 对缺字段的 dict 返回 None；
    04g - _extract_drift_suggestion 对非 dict 类型返回 None。
"""

from __future__ import annotations

import json
import tempfile
import shutil
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
SSOT_WORLD = ROOT / "host_brain" / "canon" / "world"
FIXTURE_DIARY = ROOT / "tests" / "fixtures" / "diary"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _make_temp_store():
    """创建使用临时目录和内存 ledger 的 WorldCanonStore。"""
    from host_brain.memory.canon_world_store import WorldCanonStore
    from host_brain.audit.sqlite_ledger import SQLiteLedger

    tmp_dir = tempfile.mkdtemp(prefix="diary_evo_test_")
    for f in SSOT_WORLD.glob("*.json"):
        shutil.copy(f, tmp_dir)

    ledger = SQLiteLedger(db_path=":memory:")
    store = WorldCanonStore(world_root=tmp_dir, ledger=ledger)
    return store, ledger, tmp_dir


def _make_diary_context(
    *,
    llm_result: dict[str, Any],
    canon_world_store: Any,
    ledger: Any,
    intent_id: str = "test-intent-diary-001",
) -> dict[str, Any]:
    """构造 execute_diary_writer 所需的 context。"""
    def llm_generator(inputs: dict) -> dict:
        return llm_result

    def audit_reader(start_ts: int, end_ts: int) -> list:
        return [{"summary": "今天和 Master 讨论了系统架构"}]

    def episodic_reader(start_ts: int, end_ts: int, limit: int) -> list:
        return []

    def episodic_writer(entry: dict) -> dict:
        return {"status": "ok"}

    return {
        "intent_id": intent_id,
        "request_id": f"req-{intent_id}",
        "llm_generator": llm_generator,
        "audit_reader": audit_reader,
        "episodic_reader": episodic_reader,
        "episodic_writer": episodic_writer,
        "canon_world_store": canon_world_store,
        "ledger": ledger,
    }


class TestDiaryEvolutionT03(unittest.TestCase):
    """T-Diary-03: 提案写入 + source_intent_id 串联。"""

    def setUp(self) -> None:
        self.store, self.ledger, self.tmp_dir = _make_temp_store()
        self.intent_id = "test-intent-diary-t03"
        self.fixture = _load_json(FIXTURE_DIARY / "diary_with_drift_suggestion.json")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _run_diary(self, llm_result: dict) -> dict:
        from host_brain.skills.impl.diary_writer import execute_diary_writer
        ctx = _make_diary_context(
            llm_result=llm_result,
            canon_world_store=self.store,
            ledger=self.ledger,
            intent_id=self.intent_id,
        )
        return execute_diary_writer(
            args={"date": "2026-03-03", "base_dir": self.tmp_dir},
            context=ctx,
        )

    def test_t_diary_03a_proposal_written_to_canon_proposal_log(self) -> None:
        """日记含合规 drift_suggestion 时，canon_proposal_log 写入成功。"""
        result = self._run_diary(self.fixture)
        self.assertEqual(result["status"], "EXECUTED")
        proposal = result.get("evolution_proposal")
        self.assertIsNotNone(proposal, msg="evolution_proposal 应不为 None")
        self.assertEqual(proposal["canon_kind"], "world")
        self.assertEqual(proposal["target_id"], "wc-001-system-terms")

    def test_t_diary_03b_source_intent_id_traceable(self) -> None:
        """写入的提案 source_intent_id 可追溯到日记 intent_id。"""
        result = self._run_diary(self.fixture)
        proposal = result.get("evolution_proposal")
        self.assertIsNotNone(proposal)
        self.assertEqual(
            proposal.get("source_intent_id"),
            self.intent_id,
            msg="source_intent_id 应等于日记 intent_id",
        )

        persisted = self.ledger.get_proposal(proposal["proposal_id"])
        self.assertIsNotNone(persisted)
        self.assertEqual(
            persisted.get("source_intent_id"),
            self.intent_id,
            msg="canon_proposal_log.source_intent_id should persist to DB",
        )


    def test_t_diary_03c_proposal_status_pending(self) -> None:
        """写入的提案 status=pending。"""
        result = self._run_diary(self.fixture)
        proposal = result.get("evolution_proposal")
        self.assertIsNotNone(proposal)
        self.assertEqual(proposal.get("status"), "pending")

    def test_t_diary_03d_proposer_node_id_is_diary_writer(self) -> None:
        """提案 proposer_node_id=diary_writer。"""
        result = self._run_diary(self.fixture)
        proposal = result.get("evolution_proposal")
        self.assertIsNotNone(proposal)
        self.assertEqual(proposal.get("proposer_node_id"), "diary_writer")

    def test_t_diary_03e_fixture_fields_valid(self) -> None:
        """fixture diary_with_drift_suggestion.json 字段合规性验证。"""
        fixture_path = FIXTURE_DIARY / "diary_with_drift_suggestion.json"
        self.assertTrue(fixture_path.exists())
        data = _load_json(fixture_path)
        drift = data.get("drift_suggestion")
        self.assertIsNotNone(drift, msg="fixture 应含 drift_suggestion")
        self.assertIn("doc_id", drift)
        self.assertIn("suggested_body", drift)
        self.assertIn("reason", drift)
        self.assertLessEqual(len(drift["reason"]), 200, msg="reason 不超过 200 字")


class TestDiaryEvolutionT04(unittest.TestCase):
    """T-Diary-04: QA 拦截 + 日记正常写入。"""

    def setUp(self) -> None:
        self.store, self.ledger, self.tmp_dir = _make_temp_store()
        self.intent_id = "test-intent-diary-t04"
        self.bad_fixture = _load_json(FIXTURE_DIARY / "diary_drift_suggestion_bad.json")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _run_diary(self, llm_result: dict, intent_id: str | None = None) -> dict:
        from host_brain.skills.impl.diary_writer import execute_diary_writer
        ctx = _make_diary_context(
            llm_result=llm_result,
            canon_world_store=self.store,
            ledger=self.ledger,
            intent_id=intent_id or self.intent_id,
        )
        return execute_diary_writer(
            args={"date": "2026-03-03", "base_dir": self.tmp_dir},
            context=ctx,
        )

    def test_t_diary_04a_bad_drift_suggestion_proposal_discarded(self) -> None:
        """drift_suggestion.suggested_body 含伪执行句时，提案被丢弃（evolution_proposal=None）。"""
        result = self._run_diary(self.bad_fixture)
        self.assertEqual(result["status"], "EXECUTED", msg="日记应正常写入")
        self.assertIsNone(
            result.get("evolution_proposal"),
            msg="含伪执行句的 drift_suggestion 应被丢弃",
        )

    def test_t_diary_04b_diary_written_normally_despite_bad_drift(self) -> None:
        """日记正常写入（status=EXECUTED），不受 drift_suggestion 失败影响。"""
        result = self._run_diary(self.bad_fixture)
        self.assertEqual(result["status"], "EXECUTED")
        self.assertIsNotNone(result.get("manifest"))
        self.assertGreater(result["manifest"].get("narrative_length", 0), 0)

    def test_t_diary_04c_wcanon_proposal_invalid_logged(self) -> None:
        """WCANON_PROPOSAL_INVALID 可统计（ledger.log_error 被调用）。"""
        mock_ledger = MagicMock()
        mock_ledger.log_canon_proposal = MagicMock()
        mock_ledger.log_error = MagicMock()

        from host_brain.skills.impl.diary_writer import execute_diary_writer

        def llm_generator(inputs: dict) -> dict:
            return self.bad_fixture

        def audit_reader(start_ts: int, end_ts: int) -> list:
            return [{"summary": "test"}]

        ctx = {
            "intent_id": self.intent_id,
            "request_id": f"req-{self.intent_id}",
            "llm_generator": llm_generator,
            "audit_reader": audit_reader,
            "episodic_reader": lambda s, e, l: [],
            "episodic_writer": lambda x: {"status": "ok"},
            "canon_world_store": self.store,
            "ledger": mock_ledger,
        }
        execute_diary_writer(
            args={"date": "2026-03-03", "base_dir": self.tmp_dir},
            context=ctx,
        )
        mock_ledger.log_error.assert_called()
        call_kwargs = mock_ledger.log_error.call_args
        # 验证 reason_code 包含 WCANON_PROPOSAL_INVALID
        from host_brain.core.errors import WCANON_PROPOSAL_INVALID
        args_flat = str(call_kwargs)
        self.assertIn(WCANON_PROPOSAL_INVALID, args_flat)

    def test_t_diary_04d_no_drift_suggestion_diary_normal(self) -> None:
        """无 drift_suggestion 时日记正常写入，evolution_proposal=None。"""
        llm_result_no_drift = {
            "narrative": "今天和 Master 进行了一次讨论。",
            "intent_draft": {},
            "meta": {"draft_only": False, "date": "2026-03-03"},
        }
        result = self._run_diary(llm_result_no_drift, intent_id="test-intent-no-drift")
        self.assertEqual(result["status"], "EXECUTED")
        self.assertIsNone(result.get("evolution_proposal"))

    def test_t_diary_04e_bad_fixture_fields_valid(self) -> None:
        """fixture diary_drift_suggestion_bad.json 字段合规性验证。"""
        fixture_path = FIXTURE_DIARY / "diary_drift_suggestion_bad.json"
        self.assertTrue(fixture_path.exists())
        data = _load_json(fixture_path)
        drift = data.get("drift_suggestion")
        self.assertIsNotNone(drift)
        # 验证 suggested_body 确实含完成性表述
        from host_brain.core.qa import FALSE_EXECUTION_PATTERNS
        body = drift.get("suggested_body", "")
        has_claim = any(p.search(body) for p in FALSE_EXECUTION_PATTERNS)
        self.assertTrue(has_claim, msg="bad fixture 的 suggested_body 应含完成性表述")

    def test_t_diary_04f_extract_drift_suggestion_missing_fields(self) -> None:
        """_extract_drift_suggestion 对缺字段的 dict 返回 None。"""
        from host_brain.skills.impl.diary_writer import _extract_drift_suggestion
        # 缺 doc_id
        self.assertIsNone(_extract_drift_suggestion({"suggested_body": "test"}))
        # 缺 suggested_body
        self.assertIsNone(_extract_drift_suggestion({"doc_id": "wc-001"}))
        # 空 doc_id
        self.assertIsNone(_extract_drift_suggestion({"doc_id": "", "suggested_body": "test"}))

    def test_t_diary_04g_extract_drift_suggestion_non_dict(self) -> None:
        """_extract_drift_suggestion 对非 dict 类型返回 None。"""
        from host_brain.skills.impl.diary_writer import _extract_drift_suggestion
        self.assertIsNone(_extract_drift_suggestion(None))
        self.assertIsNone(_extract_drift_suggestion("string"))
        self.assertIsNone(_extract_drift_suggestion(123))
        self.assertIsNone(_extract_drift_suggestion([]))


if __name__ == "__main__":
    unittest.main()
