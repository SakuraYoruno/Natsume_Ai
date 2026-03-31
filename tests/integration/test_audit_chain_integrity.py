"""
M26-A 审计链完整性校验
T-AUDIT-CHAIN-01~03：request_id 串联三联画日志（intent/governor/skill_exec）
TASK-0222 / FR-AUDIT-001
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import TRACE_MISSING_INTENT
from host_brain.governor.decision import IntentDecisionEngine
from host_brain.governor.tokens import TokenService
from host_brain.skills.loader import SkillLoader
from host_brain.skills.registry import SkillMeta, SkillRegistry

ROOT = Path(__file__).resolve().parents[2]
INTENT_FIX = ROOT / "tests" / "fixtures" / "intent" / "good" / "intent_v1_valid.json"


def _load_intent() -> dict:
    return json.loads(INTENT_FIX.read_text(encoding="utf-8-sig"))


def _noop_skill(args: dict, _ctx: dict | None) -> dict:
    return {"status": "ok"}


class TestAuditChainIntegrityM26(unittest.TestCase):
    """T-AUDIT-CHAIN-01~03：审计链完整性校验"""

    def setUp(self) -> None:
        self.now_ms = 1_800_200_000_000
        self.ledger = SQLiteLedger(db_path=":memory:")
        self.tokens = TokenService(approval_ttl_ms=30_000, confirm_ttl_ms=30_000, undo_ttl_ms=30_000)
        self.engine = IntentDecisionEngine(
            ledger=self.ledger,
            token_service=self.tokens,
            expiry_window_ms=300_000,
        )
        registry = SkillRegistry()
        registry.register(
            SkillMeta(
                skill_name="read_only_status",
                clearance="LOW",
                risk_level=1,
                scope="host",
                reversible=False,
                handler=_noop_skill,
            )
        )
        self.loader = SkillLoader(registry=registry, token_service=self.tokens, ledger=self.ledger)

    # ------------------------------------------------------------------
    # T-AUDIT-CHAIN-01：request_id 串联三联画日志（intent/governor/skill_exec）
    # ------------------------------------------------------------------
    def test_t_audit_chain_01_request_id_links_triptych(self) -> None:
        """request_id 在 intent_log / governor_log / skill_exec_log 三表中一致"""
        intent = _load_intent()
        intent["id"] = "chain_01_intent"
        intent["seq"] = 1
        intent["nonce"] = "chain_01_nonce"
        intent["ts_ms"] = self.now_ms - 500

        request_id = "chain_01_req"
        governor = self.engine.evaluate_intent(
            intent,
            request_id=request_id,
            thread_id="chain_thread_01",
            turn_id="chain_turn_01",
            current_ms=self.now_ms,
        )
        self.assertEqual("APPROVE", governor["decision"])

        run_intent = dict(intent)
        run_intent.update({
            "request_id": request_id,
            "thread_id": "chain_thread_01",
            "turn_id": "chain_turn_01",
        })
        self.loader.execute(intent=run_intent, governor_output=governor, now_ts_ms=self.now_ms)

        trace = self.ledger.trace_request(request_id)
        self.assertEqual("COMPLETE", trace["status"])

        # 三联画 intent_id 一致
        self.assertEqual(intent["id"], trace["intent_log"][0]["intent_id"])
        self.assertEqual(intent["id"], trace["governor_log"][0]["intent_id"])
        self.assertEqual(intent["id"], trace["skill_exec_log"][0]["intent_id"])

    # ------------------------------------------------------------------
    # T-AUDIT-CHAIN-02：多轮对话 request_id 各自独立，不互相污染
    # ------------------------------------------------------------------
    def test_t_audit_chain_02_multi_turn_request_ids_isolated(self) -> None:
        """多轮对话：不同 request_id 的审计记录互不污染"""
        for i in range(1, 4):
            intent = _load_intent()
            intent["id"] = f"chain_02_intent_{i}"
            intent["seq"] = i
            intent["nonce"] = f"chain_02_nonce_{i}"
            intent["ts_ms"] = self.now_ms - 500

            request_id = f"chain_02_req_{i}"
            governor = self.engine.evaluate_intent(
                intent,
                request_id=request_id,
                thread_id="chain_thread_02",
                turn_id=f"chain_turn_02_{i}",
                current_ms=self.now_ms,
            )
            run_intent = dict(intent)
            run_intent["request_id"] = request_id
            self.loader.execute(intent=run_intent, governor_output=governor, now_ts_ms=self.now_ms)

        # 每个 request_id 只有自己的记录
        for i in range(1, 4):
            trace = self.ledger.trace_request(f"chain_02_req_{i}")
            self.assertEqual("COMPLETE", trace["status"])
            self.assertEqual(1, len(trace["intent_log"]))
            self.assertEqual(1, len(trace["governor_log"]))
            self.assertEqual(1, len(trace["skill_exec_log"]))
            self.assertEqual(f"chain_02_intent_{i}", trace["intent_log"][0]["intent_id"])

    # ------------------------------------------------------------------
    # T-AUDIT-CHAIN-03：缺失 intent 的 request_id 返回 INCOMPLETE + reason_code
    # ------------------------------------------------------------------
    def test_t_audit_chain_03_missing_intent_trace_is_diagnosable(self) -> None:
        """缺失 intent 的 request_id：trace 返回 INCOMPLETE + TRACE_MISSING_INTENT"""
        trace = self.ledger.trace_request("chain_03_nonexistent_req")
        self.assertEqual("INCOMPLETE", trace["status"])
        self.assertEqual(TRACE_MISSING_INTENT, trace["reason_code"])
        self.assertEqual([], trace["intent_log"])
        self.assertEqual([], trace["governor_log"])
        self.assertEqual([], trace["skill_exec_log"])

    # ------------------------------------------------------------------
    # 额外：REJECT 链路审计记录完整（intent + governor，无 skill_exec）
    # ------------------------------------------------------------------
    def test_t_audit_chain_04_reject_chain_has_intent_and_governor_only(self) -> None:
        """REJECT 链路：intent_log + governor_log 有记录，skill_exec_log 为空"""
        intent = _load_intent()
        intent["id"] = "chain_04_intent"
        intent["seq"] = 1
        intent["nonce"] = "chain_04_nonce"
        intent["ts_ms"] = self.now_ms - 500

        request_id = "chain_04_req"
        governor = self.engine.evaluate_intent(
            intent,
            request_id=request_id,
            current_ms=self.now_ms,
            governance_snapshot={"trust_level": 0},
        )
        self.assertEqual("REJECT", governor["decision"])

        run_intent = dict(intent)
        run_intent["request_id"] = request_id
        self.loader.execute(intent=run_intent, governor_output=governor, now_ts_ms=self.now_ms)

        trace = self.ledger.trace_request(request_id)
        # intent + governor 有记录
        self.assertGreaterEqual(len(trace["intent_log"]), 1)
        self.assertGreaterEqual(len(trace["governor_log"]), 1)
        # skill_exec 无成功记录（REJECTED 不写 skill_exec_log 或写 REJECTED 状态）
        for rec in trace.get("skill_exec_log", []):
            self.assertNotEqual("SUCCESS", rec.get("result_status"))


if __name__ == "__main__":
    unittest.main()
