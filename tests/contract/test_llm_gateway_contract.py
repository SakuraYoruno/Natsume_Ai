import json
import unittest
from pathlib import Path

from host_brain.airlock.sanitizer import EgressGateResult
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    AIRLOCK_BLOCKED_EGRESS,
    AIRLOCK_BLOCKED_INGRESS,
    SERVICE_UNAVAILABLE_COLD_START,
)
from host_brain.core.qa import REASON_FORMAT_DRIFT, find_forbidden_keys
from host_brain.integrations.llm_gateway import (
    PROVIDER_DEEPSEEK,
    PROVIDER_GEMINI,
    PROVIDER_QWEN,
    S_LLM_GATEWAY_EGRESS,
    S_LLM_GATEWAY_PROVIDER,
    S_LLM_GATEWAY_QA,
    STATUS_DEGRADED,
    STATUS_OK,
    PROVIDER_MOCK,
    PROVIDER_OPENAI,
    LlmGatewayContract,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "llm_gateway"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestLlmGatewayContract(unittest.TestCase):
    def test_gateway_success_is_airlocked_and_draft_only(self) -> None:
        prompt = _load_json(FIXTURES / "good" / "prompt_payload.json")
        provider_response = _load_json(FIXTURES / "good" / "provider_response.json")
        ledger = SQLiteLedger(db_path=":memory:")

        gateway = LlmGatewayContract(
            ledger=ledger,
            provider=lambda _payload: json.dumps(provider_response, ensure_ascii=False),
            provider_name=PROVIDER_MOCK,
            api_key=None,
            available=True,
        )
        result = gateway.generate(
            request_id="req_llm_gateway_001",
            model_profile_id="mp.mock.v1",
            prompt_payload=prompt,
            ts_ms=1_800_030_000_000,
        )

        self.assertTrue(result.success)
        self.assertEqual(STATUS_OK, result.status)
        self.assertEqual(S_LLM_GATEWAY_QA, result.stage)
        self.assertIsNone(result.reason_code)
        self.assertTrue(result.output["draft_only"])
        self.assertEqual("mp.mock.v1", result.output["model_profile_id"])
        self.assertEqual("mock", result.output["provider"])
        self.assertEqual("这是外部模型返回的草案。", result.output["payload"]["narrative"]["text"])
        self.assertEqual([], find_forbidden_keys(result.output["payload"]))
        json.loads(json.dumps(result.output, ensure_ascii=False))

        self.assertEqual(2, len(ledger.airlock_log))
        self.assertEqual("EGRESS", ledger.airlock_log[0]["direction"])
        self.assertEqual(0, ledger.airlock_log[0]["blocked"])
        self.assertIn("api_key", ledger.airlock_log[0]["redactions"])
        self.assertEqual("INGRESS", ledger.airlock_log[1]["direction"])
        self.assertEqual(1, ledger.airlock_log[1]["blocked"])
        self.assertEqual(AIRLOCK_BLOCKED_INGRESS, ledger.airlock_log[1]["reason_code"])
        ledger.close()

    def test_gateway_provider_unavailable_cold_start_degrades(self) -> None:
        prompt = _load_json(FIXTURES / "good" / "prompt_payload.json")
        ledger = SQLiteLedger(db_path=":memory:")

        gateway = LlmGatewayContract(
            ledger=ledger,
            provider_name=PROVIDER_OPENAI,
            api_key=None,
            available=True,
        )
        result = gateway.generate(
            request_id="req_llm_gateway_002",
            model_profile_id="mp.openai.v1",
            prompt_payload=prompt,
            ts_ms=1_800_030_000_001,
        )

        self.assertFalse(result.success)
        self.assertEqual(STATUS_DEGRADED, result.status)
        self.assertEqual(S_LLM_GATEWAY_PROVIDER, result.stage)
        self.assertEqual(SERVICE_UNAVAILABLE_COLD_START, result.reason_code)
        self.assertTrue(result.output["draft_only"])
        self.assertEqual(SERVICE_UNAVAILABLE_COLD_START, result.output["reason_code"])
        self.assertTrue(
            any(
                row["stage"] == S_LLM_GATEWAY_PROVIDER
                and row["reason_code"] == SERVICE_UNAVAILABLE_COLD_START
                for row in ledger.error_log
            ),
            msg=ledger.error_log,
        )
        ledger.close()

    def test_gateway_provider_unavailable_deepseek_cold_start_degrades(self) -> None:
        prompt = _load_json(FIXTURES / "good" / "prompt_payload.json")
        ledger = SQLiteLedger(db_path=":memory:")

        gateway = LlmGatewayContract(
            ledger=ledger,
            provider_name=PROVIDER_DEEPSEEK,
            api_key=None,
            available=True,
        )
        result = gateway.generate(
            request_id="req_llm_gateway_005",
            model_profile_id="mp.deepseek.v1",
            prompt_payload=prompt,
            ts_ms=1_800_030_000_004,
        )

        self.assertFalse(result.success)
        self.assertEqual(STATUS_DEGRADED, result.status)
        self.assertEqual(S_LLM_GATEWAY_PROVIDER, result.stage)
        self.assertEqual(SERVICE_UNAVAILABLE_COLD_START, result.reason_code)
        self.assertEqual("deepseek", result.output["provider"])
        self.assertTrue(result.output["draft_only"])
        self.assertEqual(SERVICE_UNAVAILABLE_COLD_START, result.output["reason_code"])
        self.assertTrue(
            any(
                row["stage"] == S_LLM_GATEWAY_PROVIDER
                and row["reason_code"] == SERVICE_UNAVAILABLE_COLD_START
                for row in ledger.error_log
            ),
            msg=ledger.error_log,
        )
        ledger.close()

    def test_gateway_provider_unavailable_qwen_or_gemini_cold_start_degrades(self) -> None:
        prompt = _load_json(FIXTURES / "good" / "prompt_payload.json")
        for provider_name, profile_id in (
            (PROVIDER_QWEN, "mp.qwen.v1"),
            (PROVIDER_GEMINI, "mp.gemini.v1"),
        ):
            ledger = SQLiteLedger(db_path=":memory:")
            gateway = LlmGatewayContract(
                ledger=ledger,
                provider_name=provider_name,
                api_key=None,
                available=True,
            )
            result = gateway.generate(
                request_id=f"req_llm_gateway_{provider_name}",
                model_profile_id=profile_id,
                prompt_payload=prompt,
                ts_ms=1_800_030_000_005,
            )
            self.assertFalse(result.success)
            self.assertEqual(STATUS_DEGRADED, result.status)
            self.assertEqual(S_LLM_GATEWAY_PROVIDER, result.stage)
            self.assertEqual(SERVICE_UNAVAILABLE_COLD_START, result.reason_code)
            self.assertEqual(provider_name, result.output["provider"])
            self.assertTrue(result.output["draft_only"])
            ledger.close()

    def test_gateway_egress_blocked_returns_airlock_reason(self) -> None:
        payload = _load_json(FIXTURES / "bad" / "egress_blocked_payload.json")

        def blocked_gate(prompt_payload: dict, **_kwargs) -> EgressGateResult:
            return EgressGateResult(
                allowed=False,
                reason_code=AIRLOCK_BLOCKED_EGRESS,
                payload=prompt_payload,
                sanitization_report_json=None,
                detail="missing sanitization_report_json",
            )

        gateway = LlmGatewayContract(
            ledger=None,
            provider_name=PROVIDER_MOCK,
            egress_gate_fn=blocked_gate,
        )
        result = gateway.generate(
            request_id="req_llm_gateway_003",
            model_profile_id="mp.mock.v1",
            prompt_payload=payload,
            ts_ms=1_800_030_000_002,
        )

        self.assertFalse(result.success)
        self.assertEqual(STATUS_DEGRADED, result.status)
        self.assertEqual(S_LLM_GATEWAY_EGRESS, result.stage)
        self.assertEqual(AIRLOCK_BLOCKED_EGRESS, result.reason_code)

    def test_gateway_format_drift_is_downgraded_by_qa(self) -> None:
        prompt = _load_json(FIXTURES / "good" / "prompt_payload.json")
        drift_text = _load_text(FIXTURES / "bad" / "provider_format_drift.txt")
        ledger = SQLiteLedger(db_path=":memory:")

        gateway = LlmGatewayContract(
            ledger=ledger,
            provider=lambda _payload: drift_text,
            provider_name=PROVIDER_MOCK,
            available=True,
        )
        result = gateway.generate(
            request_id="req_llm_gateway_004",
            model_profile_id="mp.mock.v1",
            prompt_payload=prompt,
            ts_ms=1_800_030_000_003,
        )

        self.assertFalse(result.success)
        self.assertEqual(STATUS_DEGRADED, result.status)
        self.assertEqual(S_LLM_GATEWAY_QA, result.stage)
        self.assertEqual(REASON_FORMAT_DRIFT, result.reason_code)
        self.assertTrue(result.output["draft_only"])
        self.assertTrue(result.output["payload"]["meta"]["degraded"])
        self.assertTrue(
            any(
                row["stage"] == S_LLM_GATEWAY_QA and row["reason_code"] == REASON_FORMAT_DRIFT
                for row in ledger.error_log
            ),
            msg=ledger.error_log,
        )
        ledger.close()


if __name__ == "__main__":
    unittest.main()
