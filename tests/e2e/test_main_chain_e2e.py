import json
import unittest
from pathlib import Path

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    EXPIRED_INTENT,
    REPLAY_DETECTED,
    SERVICE_UNAVAILABLE_COLD_START,
    SKILL_TOKEN_MISSING,
    TRUST_LOW,
)
from host_brain.governor.decision import IntentDecisionEngine
from host_brain.governor.tokens import TokenService
from host_brain.integrations.llm_gateway import LlmGatewayContract, PROVIDER_OPENAI
from host_brain.skills.loader import SkillLoader
from host_brain.skills.registry import SkillMeta, SkillRegistry


ROOT = Path(__file__).resolve().parents[2]
INTENT_FIX = ROOT / "tests" / "fixtures" / "intent" / "good" / "intent_v1_valid.json"
PROMPT_FIX = ROOT / "tests" / "fixtures" / "llm_gateway" / "good" / "prompt_payload.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_only_status(args: dict, _ctx: dict | None) -> dict:
    return {"status": "ok", "scope": args.get("scope", "unknown")}


class TestMainChainE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.now_ms = 1_800_050_000_000
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

    def _new_intent(self, *, seq: int, intent_id: str, nonce: str) -> dict:
        payload = _load_json(INTENT_FIX)
        payload["id"] = intent_id
        payload["seq"] = seq
        payload["nonce"] = nonce
        payload["ts_ms"] = self.now_ms - 1_000
        return payload

    def test_e2e_approve_execute_and_audit_trace(self) -> None:
        intent = self._new_intent(seq=1, intent_id="intent_e2e_001", nonce="nonce_e2e_001")
        request_id = "req_e2e_001"

        governor = self.engine.evaluate_intent(
            intent,
            request_id=request_id,
            thread_id="thread_e2e",
            turn_id="turn_e2e_001",
            current_ms=self.now_ms,
        )
        self.assertEqual("APPROVE", governor["decision"])

        run_intent = dict(intent)
        run_intent.update(
            {
                "request_id": request_id,
                "thread_id": "thread_e2e",
                "turn_id": "turn_e2e_001",
            }
        )
        exec_result = self.loader.execute(
            intent=run_intent,
            governor_output=governor,
            now_ts_ms=self.now_ms,
        )
        self.assertEqual("SUCCESS", exec_result.status)
        self.assertTrue(exec_result.evidence_ref and exec_result.evidence_ref.startswith("evidence::"))

        trace = self.ledger.trace_request(request_id)
        self.assertEqual("COMPLETE", trace["status"])
        self.assertEqual(1, len(trace["intent_log"]))
        self.assertEqual(1, len(trace["governor_log"]))
        self.assertEqual(1, len(trace["skill_exec_log"]))
        self.assertEqual("SUCCESS", trace["skill_exec_log"][0]["result_status"])

    def test_e2e_replay_is_rejected(self) -> None:
        intent = self._new_intent(seq=1, intent_id="intent_e2e_002", nonce="nonce_e2e_002")
        first = self.engine.evaluate_intent(intent, request_id="req_e2e_002", current_ms=self.now_ms)
        second = self.engine.evaluate_intent(intent, request_id="req_e2e_002_repeat", current_ms=self.now_ms)
        self.assertEqual("APPROVE", first["decision"])
        self.assertEqual("REJECT", second["decision"])
        self.assertEqual(REPLAY_DETECTED, second["reason_code"])

    def test_e2e_expired_intent_is_rejected(self) -> None:
        intent = self._new_intent(seq=1, intent_id="intent_e2e_003", nonce="nonce_e2e_003")
        intent["ts_ms"] = self.now_ms - 301_000
        result = self.engine.evaluate_intent(intent, request_id="req_e2e_003", current_ms=self.now_ms)
        self.assertEqual("REJECT", result["decision"])
        self.assertEqual(EXPIRED_INTENT, result["reason_code"])

    def test_e2e_missing_ticket_is_rejected_at_skill_loader(self) -> None:
        intent = self._new_intent(seq=1, intent_id="intent_e2e_004", nonce="nonce_e2e_004")
        request_id = "req_e2e_004"
        governor = self.engine.evaluate_intent(intent, request_id=request_id, current_ms=self.now_ms)
        self.assertEqual("APPROVE", governor["decision"])

        no_token_governor = dict(governor)
        no_token_governor["tokens"] = {}
        run_intent = dict(intent)
        run_intent["request_id"] = request_id

        exec_result = self.loader.execute(
            intent=run_intent,
            governor_output=no_token_governor,
            now_ts_ms=self.now_ms,
        )
        self.assertEqual("REJECTED", exec_result.status)
        self.assertEqual(SKILL_TOKEN_MISSING, exec_result.result_code)
        self.assertIsNone(exec_result.evidence_ref)

    def test_e2e_governor_gate_reject_blocks_execution(self) -> None:
        intent = self._new_intent(seq=1, intent_id="intent_e2e_005", nonce="nonce_e2e_005")
        request_id = "req_e2e_005"
        governor = self.engine.evaluate_intent(
            intent,
            request_id=request_id,
            current_ms=self.now_ms,
            governance_snapshot={"trust_level": 0},
        )
        self.assertEqual("REJECT", governor["decision"])
        self.assertEqual(TRUST_LOW, governor["reason_code"])

        run_intent = dict(intent)
        run_intent["request_id"] = request_id
        exec_result = self.loader.execute(
            intent=run_intent,
            governor_output=governor,
            now_ts_ms=self.now_ms,
        )
        self.assertEqual("REJECTED", exec_result.status)
        self.assertEqual("GOVERNOR_REJECTED", exec_result.result_code)

    def test_e2e_degraded_gateway_path_is_still_json(self) -> None:
        prompt_payload = _load_json(PROMPT_FIX)
        gateway = LlmGatewayContract(
            ledger=self.ledger,
            provider_name=PROVIDER_OPENAI,
            api_key=None,
            available=True,
        )
        result = gateway.generate(
            request_id="req_e2e_006",
            model_profile_id="mp.openai.v1",
            prompt_payload=prompt_payload,
            ts_ms=self.now_ms,
        )
        self.assertFalse(result.success)
        self.assertEqual("DEGRADED", result.status)
        self.assertEqual(SERVICE_UNAVAILABLE_COLD_START, result.reason_code)
        self.assertTrue(result.output["draft_only"])
        json.loads(json.dumps(result.output, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
