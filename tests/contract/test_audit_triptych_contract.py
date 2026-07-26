import json
import unittest
from pathlib import Path

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    GOVERNED_SKILL_TASK_AUTHORITY_REQUIRED,
    TRACE_MISSING_INTENT,
)
from host_brain.governor.decision import IntentDecisionEngine
from host_brain.governor.tokens import TokenService
from host_brain.skills.loader import SkillLoader
from host_brain.skills.registry import SkillMeta, SkillRegistry


ROOT = Path(__file__).resolve().parents[2]
INTENT_FIX = ROOT / "tests" / "fixtures" / "intent" / "good" / "intent_v1_valid.json"


def _load_intent() -> dict:
    return json.loads(INTENT_FIX.read_text(encoding="utf-8-sig"))


def _read_only_status(args: dict, _ctx: dict | None) -> dict:
    return {"status": "ok", "scope": args.get("scope", "unknown")}


class TestAuditTriptychContract(unittest.TestCase):
    def setUp(self) -> None:
        self.now_ms = 1_800_000_000_000
        self.ledger = SQLiteLedger(db_path=":memory:")
        self.tokens = TokenService(approval_ttl_ms=20_000, confirm_ttl_ms=20_000, undo_ttl_ms=20_000)
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
                handler=_read_only_status,
            )
        )
        self.loader = SkillLoader(registry=registry, token_service=self.tokens, ledger=self.ledger)

    def test_t_audit_01_request_chain_is_replayable(self) -> None:
        intent = _load_intent()
        intent["id"] = "intent_audit_001"
        intent["seq"] = 1
        intent["nonce"] = "nonce_audit_001"
        intent["ts_ms"] = self.now_ms - 1_000

        request_id = "req_audit_001"
        governor = self.engine.evaluate_intent(
            intent,
            request_id=request_id,
            thread_id="thread_audit_001",
            turn_id="turn_audit_001",
            current_ms=self.now_ms,
        )
        self.assertEqual("APPROVE", governor["decision"])

        intent_with_chain = dict(intent)
        intent_with_chain["request_id"] = request_id
        intent_with_chain["thread_id"] = "thread_audit_001"
        intent_with_chain["turn_id"] = "turn_audit_001"
        skill_result = self.loader.execute(
            intent=intent_with_chain,
            governor_output=governor,
            now_ts_ms=self.now_ms,
        )
        self.assertEqual("REJECTED", skill_result.status)
        self.assertEqual(
            GOVERNED_SKILL_TASK_AUTHORITY_REQUIRED,
            skill_result.result_code,
        )

        trace = self.ledger.trace_request(request_id)
        self.assertEqual("COMPLETE", trace["status"])
        self.assertEqual(1, len(trace["intent_log"]))
        self.assertEqual(1, len(trace["governor_log"]))
        self.assertEqual(1, len(trace["skill_exec_log"]))
        self.assertEqual(intent["id"], trace["intent_log"][0]["intent_id"])
        self.assertEqual(intent["id"], trace["governor_log"][0]["intent_id"])
        self.assertEqual(intent["id"], trace["skill_exec_log"][0]["intent_id"])
        self.assertEqual(
            GOVERNED_SKILL_TASK_AUTHORITY_REQUIRED,
            trace["skill_exec_log"][0]["result_code"],
        )

    def test_trace_missing_intent_is_diagnosable(self) -> None:
        trace = self.ledger.trace_request("req_missing_001")
        self.assertEqual("INCOMPLETE", trace["status"])
        self.assertEqual(TRACE_MISSING_INTENT, trace["reason_code"])
