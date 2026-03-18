"""
M26-A 端到端集成冒烟测试套件
覆盖"用户输入 → Intent → Governor → Skill → Audit"完整链路
T-E2E-SMOKE-01~04 / TASK-0222 / FR-CORE-001
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    EXPIRED_INTENT,
    REPLAY_DETECTED,
    SERVICE_UNAVAILABLE_COLD_START,
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
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _read_only_status(args: dict, _ctx: dict | None) -> dict:
    return {"status": "ok", "scope": args.get("scope", "unknown")}


def _make_suite() -> tuple:
    """构建标准测试套件（ledger/tokens/engine/loader）"""
    now_ms = 1_800_100_000_000
    ledger = SQLiteLedger(db_path=":memory:")
    tokens = TokenService(approval_ttl_ms=30_000, confirm_ttl_ms=30_000, undo_ttl_ms=30_000)
    engine = IntentDecisionEngine(
        ledger=ledger,
        token_service=tokens,
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
    loader = SkillLoader(registry=registry, token_service=tokens, ledger=ledger)
    return now_ms, ledger, tokens, engine, loader


class TestE2ESmokeM26(unittest.TestCase):
    """T-E2E-SMOKE-01~04：端到端集成冒烟测试"""

    def setUp(self) -> None:
        self.now_ms, self.ledger, self.tokens, self.engine, self.loader = _make_suite()
        self._intent_base = _load_json(INTENT_FIX)

    def _new_intent(self, *, seq: int, intent_id: str, nonce: str) -> dict:
        payload = dict(self._intent_base)
        payload["id"] = intent_id
        payload["seq"] = seq
        payload["nonce"] = nonce
        payload["ts_ms"] = self.now_ms - 1_000
        return payload

    # ------------------------------------------------------------------
    # T-E2E-SMOKE-01：普通对话链路（无执行）完整走通，审计链可串联
    # ------------------------------------------------------------------
    def test_t_e2e_smoke_01_normal_dialogue_chain(self) -> None:
        """普通对话链路：Intent → Governor(APPROVE) → Skill(SUCCESS) → Audit(COMPLETE)"""
        intent = self._new_intent(seq=1, intent_id="smoke_01_intent", nonce="smoke_01_nonce")
        request_id = "smoke_01_req"

        governor = self.engine.evaluate_intent(
            intent,
            request_id=request_id,
            thread_id="smoke_thread_01",
            turn_id="smoke_turn_01",
            current_ms=self.now_ms,
        )
        self.assertEqual("APPROVE", governor["decision"])

        run_intent = dict(intent)
        run_intent.update({
            "request_id": request_id,
            "thread_id": "smoke_thread_01",
            "turn_id": "smoke_turn_01",
        })
        exec_result = self.loader.execute(
            intent=run_intent,
            governor_output=governor,
            now_ts_ms=self.now_ms,
        )
        self.assertEqual("SUCCESS", exec_result.status)

        # 审计链可串联
        trace = self.ledger.trace_request(request_id)
        self.assertEqual("COMPLETE", trace["status"])
        self.assertEqual(1, len(trace["intent_log"]))
        self.assertEqual(1, len(trace["governor_log"]))
        self.assertEqual(1, len(trace["skill_exec_log"]))
        # request_id 串联三联画
        self.assertEqual(intent["id"], trace["intent_log"][0]["intent_id"])
        self.assertEqual(intent["id"], trace["governor_log"][0]["intent_id"])
        self.assertEqual(intent["id"], trace["skill_exec_log"][0]["intent_id"])

    # ------------------------------------------------------------------
    # T-E2E-SMOKE-02：执行链路（APPROVE + Skill）完整走通，token_ref 可追溯
    # ------------------------------------------------------------------
    def test_t_e2e_smoke_02_execute_chain_token_traceable(self) -> None:
        """执行链路：APPROVE → Skill SUCCESS → evidence_ref 可追溯"""
        intent = self._new_intent(seq=2, intent_id="smoke_02_intent", nonce="smoke_02_nonce")
        request_id = "smoke_02_req"

        governor = self.engine.evaluate_intent(
            intent,
            request_id=request_id,
            thread_id="smoke_thread_02",
            turn_id="smoke_turn_02",
            current_ms=self.now_ms,
        )
        self.assertEqual("APPROVE", governor["decision"])
        # token 已分配
        self.assertIn("tokens", governor)

        run_intent = dict(intent)
        run_intent["request_id"] = request_id
        exec_result = self.loader.execute(
            intent=run_intent,
            governor_output=governor,
            now_ts_ms=self.now_ms,
        )
        self.assertEqual("SUCCESS", exec_result.status)
        # evidence_ref 可追溯
        self.assertIsNotNone(exec_result.evidence_ref)
        self.assertTrue(exec_result.evidence_ref.startswith("evidence::"))

        trace = self.ledger.trace_request(request_id)
        self.assertEqual("COMPLETE", trace["status"])
        self.assertEqual("SUCCESS", trace["skill_exec_log"][0]["result_status"])

    # ------------------------------------------------------------------
    # T-E2E-SMOKE-03：拒绝链路（REJECT + reason_code）完整走通
    # ------------------------------------------------------------------
    def test_t_e2e_smoke_03_reject_chain_with_reason_code(self) -> None:
        """拒绝链路：TRUST_LOW → REJECT → Skill REJECTED → reason_code 稳定"""
        intent = self._new_intent(seq=3, intent_id="smoke_03_intent", nonce="smoke_03_nonce")
        request_id = "smoke_03_req"

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
        # 拒绝时无 evidence_ref
        self.assertIsNone(exec_result.evidence_ref)

    # ------------------------------------------------------------------
    # T-E2E-SMOKE-04：降级链路（LLM 不可用 → 降级输出）完整走通
    # ------------------------------------------------------------------
    def test_t_e2e_smoke_04_degraded_llm_output_is_stable(self) -> None:
        """降级链路：LLM 不可用 → 降级输出 draft_only=true，结构稳定可解析"""
        prompt_payload = _load_json(PROMPT_FIX)
        gateway = LlmGatewayContract(
            ledger=self.ledger,
            provider_name=PROVIDER_OPENAI,
            api_key=None,
            available=True,
        )
        result = gateway.generate(
            request_id="smoke_04_req",
            model_profile_id="mp.openai.v1",
            prompt_payload=prompt_payload,
            ts_ms=self.now_ms,
        )
        # 降级输出结构稳定
        self.assertFalse(result.success)
        self.assertEqual("DEGRADED", result.status)
        self.assertEqual(SERVICE_UNAVAILABLE_COLD_START, result.reason_code)
        self.assertTrue(result.output.get("draft_only"))
        # 可序列化
        json.loads(json.dumps(result.output, ensure_ascii=False))

    # ------------------------------------------------------------------
    # 额外：重放攻击被拒绝
    # ------------------------------------------------------------------
    def test_t_e2e_smoke_05_replay_attack_rejected(self) -> None:
        """重放攻击：同一 nonce 第二次提交被 REJECT(REPLAY_DETECTED)"""
        intent = self._new_intent(seq=4, intent_id="smoke_05_intent", nonce="smoke_05_nonce")
        first = self.engine.evaluate_intent(intent, request_id="smoke_05_req_a", current_ms=self.now_ms)
        second = self.engine.evaluate_intent(intent, request_id="smoke_05_req_b", current_ms=self.now_ms)
        self.assertEqual("APPROVE", first["decision"])
        self.assertEqual("REJECT", second["decision"])
        self.assertEqual(REPLAY_DETECTED, second["reason_code"])

    # ------------------------------------------------------------------
    # 额外：过期 Intent 被拒绝
    # ------------------------------------------------------------------
    def test_t_e2e_smoke_06_expired_intent_rejected(self) -> None:
        """过期 Intent：ts_ms 超出 expiry_window 被 REJECT(EXPIRED_INTENT)"""
        intent = self._new_intent(seq=5, intent_id="smoke_06_intent", nonce="smoke_06_nonce")
        intent["ts_ms"] = self.now_ms - 400_000  # 超出 300s 窗口
        result = self.engine.evaluate_intent(intent, request_id="smoke_06_req", current_ms=self.now_ms)
        self.assertEqual("REJECT", result["decision"])
        self.assertEqual(EXPIRED_INTENT, result["reason_code"])


if __name__ == "__main__":
    unittest.main()
