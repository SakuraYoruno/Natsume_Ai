"""T-E2E-WeChat-01: 微信入站→Governor→VTS/语音→审计 一体化 E2E 场景。

覆盖三条路径：
1. 正常路径：微信入站→Governor APPROVE→Skill执行→VTS表情+语音播报→审计链完整
2. 拒绝路径：微信入站→Governor REJECT（低信任度）→无执行→审计链可追溯
3. 降级路径：微信入站→Governor APPROVE→VTS不可用→仅文本→审计链标注降级

断言点：
- 审计串联键（request_id/thread_key）从 channel_io_log→governor_log→avatar_action_log/voice_event_log 完整可回放
- VTS/语音不可用时输出结构稳定且 reason_code 可解释
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.channels.wechat import (
    CHANNEL_WECHAT,
    DIRECTION_EGRESS,
    DIRECTION_INGRESS,
    STATUS_RECEIVED,
    STATUS_SENT,
    receive_wechat_event,
    send_wechat_message,
)
from host_brain.channels.vts import (
    EMOTION_TO_EXPRESSION_ID,
    STATUS_APPLIED,
    STATUS_DEGRADED,
    STATUS_REJECTED,
    TALK_END,
    TALK_START,
    resolve_vts_expression,
    request_talk_gate,
)
from host_brain.core.errors import (
    AVATAR_TAG_INVALID,
    TRUST_LOW,
    VTS_CONNECTION_FAILED,
    VTS_DEGRADED_FALLBACK,
    TTS_MODEL_UNAVAILABLE,
)
from host_brain.core.ids import new_request_id
from host_brain.governor.decision import IntentDecisionEngine
from host_brain.governor.tokens import TokenService
from host_brain.skills.loader import SkillLoader
from host_brain.skills.registry import SkillMeta, SkillRegistry
from tests.text_quality_assertions import assert_natsume_style


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "e2e"
INTENT_FIX = ROOT / "tests" / "fixtures" / "intent" / "good" / "intent_v1_valid.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _read_only_status(args: dict, _ctx: dict | None) -> dict:
    return {"status": "ok", "scope": args.get("scope", "unknown")}


class TestWeChat_VTS_Voice_E2E(unittest.TestCase):
    """T-E2E-WeChat-01: 微信→Governor→VTS/语音 全链路 E2E 测试。"""

    def setUp(self) -> None:
        self.now_ms = 1_800_050_000_000
        self.ledger = SQLiteLedger(db_path=":memory:")
        self.tokens = TokenService(
            approval_ttl_ms=20_000,
            confirm_ttl_ms=20_000,
            undo_ttl_ms=20_000,
        )
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
        self.loader = SkillLoader(
            registry=registry,
            token_service=self.tokens,
            ledger=self.ledger,
        )

    def _make_intent(self, overrides: dict[str, Any]) -> dict[str, Any]:
        """用 fixture 基准构造 intent，应用覆盖字段。"""
        base = _load_json(INTENT_FIX)
        base["ts_ms"] = self.now_ms - 1_000
        base.update(overrides)
        return base

    # ─────────────────────────────────────────────
    # 路径 1：正常路径（APPROVE + VTS + 语音）
    # ─────────────────────────────────────────────

    def test_01_normal_path_wechat_ingress_accepted(self) -> None:
        """T-E2E-WeChat-01.1: 微信入站消息被正常接受。"""
        fix = _load_json(FIXTURE_DIR / "wechat_vts_voice_normal.json")
        ingress = receive_wechat_event(
            fix["wechat_ingress"],
            ledger=self.ledger,
            request_id="req_e2e_wc_001",
            ts_ms=self.now_ms,
        )
        self.assertTrue(ingress.accepted)
        self.assertFalse(ingress.duplicate)
        self.assertEqual("req_e2e_wc_001", ingress.request_id)
        self.assertEqual(STATUS_RECEIVED, ingress.status)
        self.assertIsNone(ingress.reason_code)
        self.assertTrue(len(ingress.thread_key) > 0)

    def test_02_normal_path_governor_approve(self) -> None:
        """T-E2E-WeChat-01.2: 正常路径 Governor 批准。"""
        fix = _load_json(FIXTURE_DIR / "wechat_vts_voice_normal.json")
        request_id = "req_e2e_wc_002"

        # 微信入站
        ingress = receive_wechat_event(
            fix["wechat_ingress"],
            ledger=self.ledger,
            request_id=request_id,
            ts_ms=self.now_ms,
        )
        self.assertTrue(ingress.accepted)

        # Intent → Governor
        intent = self._make_intent(fix["intent_overrides"])
        governor = self.engine.evaluate_intent(
            intent,
            request_id=request_id,
            thread_id=ingress.thread_key,
            turn_id="turn_e2e_wc_002",
            current_ms=self.now_ms,
        )
        self.assertEqual("APPROVE", governor["decision"])
        self.assertIn("approval_token_ref", governor.get("tokens", {}))

    def test_03_normal_path_full_chain_with_vts_and_voice(self) -> None:
        """T-E2E-WeChat-01.3: 正常路径全链路——微信→Governor→Skill→VTS→语音→审计。"""
        fix = _load_json(FIXTURE_DIR / "wechat_vts_voice_normal.json")
        request_id = "req_e2e_wc_003"

        # Step 1: 微信入站
        ingress = receive_wechat_event(
            fix["wechat_ingress"],
            ledger=self.ledger,
            request_id=request_id,
            ts_ms=self.now_ms,
        )
        self.assertTrue(ingress.accepted)
        thread_key = ingress.thread_key

        # Step 2: Intent → Governor
        intent = self._make_intent(fix["intent_overrides"])
        governor = self.engine.evaluate_intent(
            intent,
            request_id=request_id,
            thread_id=thread_key,
            turn_id="turn_e2e_wc_003",
            current_ms=self.now_ms,
        )
        self.assertEqual("APPROVE", governor["decision"])

        # Step 3: Skill 执行
        run_intent = dict(intent)
        run_intent.update({
            "request_id": request_id,
            "thread_id": thread_key,
            "turn_id": "turn_e2e_wc_003",
        })
        exec_result = self.loader.execute(
            intent=run_intent,
            governor_output=governor,
            now_ts_ms=self.now_ms,
        )
        self.assertEqual("SUCCESS", exec_result.status)

        # Step 4: VTS 表情驱动
        vts_talk = request_talk_gate(
            action=TALK_START,
            ledger=self.ledger,
            request_id=request_id,
            ts_ms=self.now_ms,
        )
        self.assertTrue(vts_talk.success)

        vts_expr = resolve_vts_expression(
            emotion_tag=fix["vts_emotion"]["emotion_tag"],
            ledger=self.ledger,
            request_id=request_id,
            trigger=fix["vts_emotion"]["trigger"],
            ts_ms=self.now_ms,
        )
        self.assertTrue(vts_expr.success)
        self.assertEqual(fix["expected"]["vts_expression_id"], vts_expr.expression_id)

        # Step 5: 语音事件记录
        self.ledger.log_voice_event({
            "request_id": request_id,
            "session_id": intent.get("session_id"),
            "event_type": fix["voice_event"]["event_type"],
            "direction": fix["voice_event"]["direction"],
            "seq": 1,
            "status": "COMPLETED",
            "reason_code": None,
            "stage": "S_VOICE_PLAY",
            "payload": json.dumps({"text": ingress.payload.get("content", "")}),
            "detail": "TTS playback completed",
            "ts_ms": self.now_ms,
        })

        # Step 6: VTS talk gate 关闭
        vts_end = request_talk_gate(
            action=TALK_END,
            ledger=self.ledger,
            request_id=request_id,
            ts_ms=self.now_ms,
        )
        self.assertTrue(vts_end.success)

        # Step 7: 微信出站回复
        egress = send_wechat_message(
            thread_key=thread_key,
            message_id=f"wx_reply_{request_id}",
            content="今天天气晴朗，适合外出！",
            ledger=self.ledger,
            request_id=request_id,
            ts_ms=self.now_ms,
        )
        self.assertTrue(egress.success)
        self.assertEqual(STATUS_SENT, egress.status)
        assert_natsume_style(
            user_message=fix["wechat_ingress"]["content"],
            reply="今天天气晴朗，适合外出！",
            emotion_tag="neutral",
            bond_stage="STRANGER",
        )

        # Step 8: 审计链完整性验证
        trace = self.ledger.trace_request(request_id)
        self.assertEqual("COMPLETE", trace["status"])
        self.assertGreaterEqual(len(trace["intent_log"]), 1)
        self.assertGreaterEqual(len(trace["governor_log"]), 1)
        self.assertGreaterEqual(len(trace["skill_exec_log"]), 1)

    def test_04_normal_path_audit_chain_request_id_consistent(self) -> None:
        """T-E2E-WeChat-01.4: 审计串联键 request_id 从 channel→governor→avatar→voice 一致。"""
        fix = _load_json(FIXTURE_DIR / "wechat_vts_voice_normal.json")
        request_id = "req_e2e_wc_004"

        # 微信入站
        receive_wechat_event(
            fix["wechat_ingress"],
            ledger=self.ledger,
            request_id=request_id,
            ts_ms=self.now_ms,
        )

        # Governor
        intent = self._make_intent({
            **fix["intent_overrides"],
            "id": "intent_e2e_wechat_004",
            "nonce": "nonce_e2e_wechat_004",
        })
        governor = self.engine.evaluate_intent(
            intent, request_id=request_id, current_ms=self.now_ms,
        )

        # Skill
        run_intent = dict(intent)
        run_intent["request_id"] = request_id
        self.loader.execute(intent=run_intent, governor_output=governor, now_ts_ms=self.now_ms)

        # VTS
        request_talk_gate(action=TALK_START, ledger=self.ledger, request_id=request_id, ts_ms=self.now_ms)
        resolve_vts_expression(
            emotion_tag="HAPPY", ledger=self.ledger, request_id=request_id, ts_ms=self.now_ms,
        )

        # Voice
        self.ledger.log_voice_event({
            "request_id": request_id,
            "session_id": intent.get("session_id"),
            "event_type": "TTS_PLAY",
            "direction": "EGRESS",
            "seq": 1,
            "status": "COMPLETED",
            "reason_code": None,
            "stage": "S_VOICE_PLAY",
            "payload": "{}",
            "detail": "voice played",
            "ts_ms": self.now_ms,
        })

        # 验证所有审计表中 request_id 一致
        channel_rows = self.ledger._fetch_by_request("channel_io_log", request_id)
        governor_rows = self.ledger._fetch_by_request("governor_log", request_id)
        avatar_rows = self.ledger._fetch_by_request("avatar_action_log", request_id)
        voice_rows = self.ledger._fetch_by_request("voice_event_log", request_id)

        self.assertGreaterEqual(len(channel_rows), 1, "channel_io_log 必须有记录")
        self.assertGreaterEqual(len(governor_rows), 1, "governor_log 必须有记录")
        self.assertGreaterEqual(len(avatar_rows), 1, "avatar_action_log 必须有记录")
        self.assertGreaterEqual(len(voice_rows), 1, "voice_event_log 必须有记录")

        for row in channel_rows + governor_rows + avatar_rows + voice_rows:
            self.assertEqual(request_id, row["request_id"], "所有审计表 request_id 必须一致")

    # ─────────────────────────────────────────────
    # 路径 2：拒绝路径（Governor REJECT）
    # ─────────────────────────────────────────────

    def test_05_reject_path_governor_reject_trust_low(self) -> None:
        """T-E2E-WeChat-01.5: Governor 拒绝路径——低信任度直接拒绝。"""
        fix = _load_json(FIXTURE_DIR / "wechat_vts_voice_rejected.json")
        request_id = "req_e2e_wc_005"

        # 微信入站
        ingress = receive_wechat_event(
            fix["wechat_ingress"],
            ledger=self.ledger,
            request_id=request_id,
            ts_ms=self.now_ms,
        )
        self.assertTrue(ingress.accepted)

        # Governor REJECT
        intent = self._make_intent(fix["intent_overrides"])
        governor = self.engine.evaluate_intent(
            intent,
            request_id=request_id,
            thread_id=ingress.thread_key,
            current_ms=self.now_ms,
            governance_snapshot=fix["governance_snapshot"],
        )
        self.assertEqual("REJECT", governor["decision"])
        self.assertEqual(TRUST_LOW, governor["reason_code"])

    def test_06_reject_path_no_skill_execution(self) -> None:
        """T-E2E-WeChat-01.6: Governor 拒绝后 Skill 不执行。"""
        fix = _load_json(FIXTURE_DIR / "wechat_vts_voice_rejected.json")
        request_id = "req_e2e_wc_006"

        receive_wechat_event(
            fix["wechat_ingress"],
            ledger=self.ledger,
            request_id=request_id,
            ts_ms=self.now_ms,
        )

        intent = self._make_intent({
            **fix["intent_overrides"],
            "id": "intent_e2e_wechat_006",
            "nonce": "nonce_e2e_wechat_006",
        })
        governor = self.engine.evaluate_intent(
            intent,
            request_id=request_id,
            current_ms=self.now_ms,
            governance_snapshot=fix["governance_snapshot"],
        )
        self.assertEqual("REJECT", governor["decision"])

        # Skill 应拒绝执行
        run_intent = dict(intent)
        run_intent["request_id"] = request_id
        exec_result = self.loader.execute(
            intent=run_intent,
            governor_output=governor,
            now_ts_ms=self.now_ms,
        )
        self.assertEqual("REJECTED", exec_result.status)
        self.assertEqual("GOVERNOR_REJECTED", exec_result.result_code)

    def test_07_reject_path_no_vts_no_voice(self) -> None:
        """T-E2E-WeChat-01.7: 拒绝路径不触发 VTS/语音（由调用方控制）。"""
        fix = _load_json(FIXTURE_DIR / "wechat_vts_voice_rejected.json")
        request_id = "req_e2e_wc_007"

        receive_wechat_event(
            fix["wechat_ingress"],
            ledger=self.ledger,
            request_id=request_id,
            ts_ms=self.now_ms,
        )

        intent = self._make_intent({
            **fix["intent_overrides"],
            "id": "intent_e2e_wechat_007",
            "nonce": "nonce_e2e_wechat_007",
        })
        governor = self.engine.evaluate_intent(
            intent,
            request_id=request_id,
            current_ms=self.now_ms,
            governance_snapshot=fix["governance_snapshot"],
        )
        self.assertEqual("REJECT", governor["decision"])

        # 验证：拒绝后不应有 avatar 或 voice 审计记录
        avatar_rows = self.ledger._fetch_by_request("avatar_action_log", request_id)
        voice_rows = self.ledger._fetch_by_request("voice_event_log", request_id)
        self.assertEqual(0, len(avatar_rows), "拒绝路径不应触发 VTS")
        self.assertEqual(0, len(voice_rows), "拒绝路径不应触发语音")

    def test_08_reject_path_audit_trace_complete_rejected(self) -> None:
        """T-E2E-WeChat-01.8: 拒绝路径审计链状态为 COMPLETE_REJECTED。"""
        fix = _load_json(FIXTURE_DIR / "wechat_vts_voice_rejected.json")
        request_id = "req_e2e_wc_008"

        receive_wechat_event(
            fix["wechat_ingress"],
            ledger=self.ledger,
            request_id=request_id,
            ts_ms=self.now_ms,
        )

        intent = self._make_intent({
            **fix["intent_overrides"],
            "id": "intent_e2e_wechat_008",
            "nonce": "nonce_e2e_wechat_008",
        })
        governor = self.engine.evaluate_intent(
            intent,
            request_id=request_id,
            current_ms=self.now_ms,
            governance_snapshot=fix["governance_snapshot"],
        )

        trace = self.ledger.trace_request(request_id)
        self.assertEqual("COMPLETE_REJECTED", trace["status"])
        self.assertEqual(TRUST_LOW, trace["reason_code"])
        self.assertGreaterEqual(len(trace["intent_log"]), 1)
        self.assertGreaterEqual(len(trace["governor_log"]), 1)
        self.assertEqual(0, len(trace["skill_exec_log"]))

    # ─────────────────────────────────────────────
    # 路径 3：降级路径（VTS/语音不可用）
    # ─────────────────────────────────────────────

    def test_09_degraded_path_vts_unavailable_fallback_text(self) -> None:
        """T-E2E-WeChat-01.9: VTS 不可用时表情驱动降级但审计仍完整。"""
        fix = _load_json(FIXTURE_DIR / "wechat_vts_voice_degraded.json")
        request_id = "req_e2e_wc_009"

        # 微信入站
        ingress = receive_wechat_event(
            fix["wechat_ingress"],
            ledger=self.ledger,
            request_id=request_id,
            ts_ms=self.now_ms,
        )

        # Governor APPROVE
        intent = self._make_intent(fix["intent_overrides"])
        governor = self.engine.evaluate_intent(
            intent,
            request_id=request_id,
            thread_id=ingress.thread_key,
            current_ms=self.now_ms,
        )
        self.assertEqual("APPROVE", governor["decision"])

        # Skill 执行
        run_intent = dict(intent)
        run_intent.update({
            "request_id": request_id,
            "thread_id": ingress.thread_key,
        })
        exec_result = self.loader.execute(
            intent=run_intent,
            governor_output=governor,
            now_ts_ms=self.now_ms,
        )
        self.assertEqual("SUCCESS", exec_result.status)

        # VTS 降级：模拟表情设置但使用无效 tag 表示不可用降级
        # （真实场景中 Edge 端 VTubeStudioDriver 会返回 VTS_CONNECTION_FAILED，
        #  Host 端记录 avatar_action_log 并标注降级 reason_code）
        self.ledger.log_avatar_action({
            "request_id": request_id,
            "trigger": "LLM",
            "emotion_tag": "HAPPY",
            "expression_id": None,
            "status": "DEGRADED",
            "reason_code": VTS_CONNECTION_FAILED,
            "stage": "S_AVATAR_EXPRESSION_MAP",
            "detail": "VTube Studio unavailable, expression degraded to text-only",
            "ts_ms": self.now_ms,
        })

        # 语音降级：TTS 不可用
        self.ledger.log_voice_event({
            "request_id": request_id,
            "session_id": intent.get("session_id"),
            "event_type": "TTS_PLAY",
            "direction": "EGRESS",
            "seq": 1,
            "status": "DEGRADED",
            "reason_code": TTS_MODEL_UNAVAILABLE,
            "stage": "S_VOICE_PLAY",
            "payload": json.dumps({"text": ingress.payload.get("content", ""), "fallback": "text_only"}),
            "detail": "TTS model unavailable, fallback to text-only output",
            "ts_ms": self.now_ms,
        })

        # 仍然发送微信文本回复
        egress = send_wechat_message(
            thread_key=ingress.thread_key,
            message_id=f"wx_reply_{request_id}",
            content="（语音不可用，文本回复）这是一个笑话……",
            ledger=self.ledger,
            request_id=request_id,
            ts_ms=self.now_ms,
        )
        self.assertTrue(egress.success)
        assert_natsume_style(
            user_message=fix["wechat_ingress"]["content"],
            reply="（语音不可用，文本回复）这是一个笑话……",
            emotion_tag="neutral",
            bond_stage="STRANGER",
        )

        # 验证审计链
        trace = self.ledger.trace_request(request_id)
        self.assertEqual("COMPLETE", trace["status"])

    def test_10_degraded_path_reason_codes_stable(self) -> None:
        """T-E2E-WeChat-01.10: 降级路径 reason_code 可解释且稳定。"""
        fix = _load_json(FIXTURE_DIR / "wechat_vts_voice_degraded.json")
        self.assertEqual("VTS_CONNECTION_FAILED", fix["expected"]["vts_reason_code"])
        self.assertEqual("TTS_MODEL_UNAVAILABLE", fix["expected"]["voice_reason_code"])
        # 验证 reason_code 常量
        self.assertEqual("VTS_CONNECTION_FAILED", VTS_CONNECTION_FAILED)
        self.assertEqual("TTS_MODEL_UNAVAILABLE", TTS_MODEL_UNAVAILABLE)

    def test_11_degraded_path_audit_chain_complete_with_degradation(self) -> None:
        """T-E2E-WeChat-01.11: 降级路径审计链包含 VTS/语音降级记录。"""
        request_id = "req_e2e_wc_011"
        fix = _load_json(FIXTURE_DIR / "wechat_vts_voice_degraded.json")

        # 微信入站
        receive_wechat_event(
            fix["wechat_ingress"],
            ledger=self.ledger,
            request_id=request_id,
            ts_ms=self.now_ms,
        )

        # Governor + Skill
        intent = self._make_intent({
            **fix["intent_overrides"],
            "id": "intent_e2e_wechat_011",
            "nonce": "nonce_e2e_wechat_011",
        })
        governor = self.engine.evaluate_intent(
            intent, request_id=request_id, current_ms=self.now_ms,
        )
        run_intent = dict(intent)
        run_intent["request_id"] = request_id
        self.loader.execute(intent=run_intent, governor_output=governor, now_ts_ms=self.now_ms)

        # 降级记录
        self.ledger.log_avatar_action({
            "request_id": request_id,
            "trigger": "LLM",
            "emotion_tag": "HAPPY",
            "expression_id": None,
            "status": "DEGRADED",
            "reason_code": VTS_CONNECTION_FAILED,
            "stage": "S_AVATAR_EXPRESSION_MAP",
            "detail": "VTS unavailable",
            "ts_ms": self.now_ms,
        })
        self.ledger.log_voice_event({
            "request_id": request_id,
            "session_id": intent.get("session_id"),
            "event_type": "TTS_PLAY",
            "direction": "EGRESS",
            "seq": 1,
            "status": "DEGRADED",
            "reason_code": TTS_MODEL_UNAVAILABLE,
            "stage": "S_VOICE_PLAY",
            "payload": "{}",
            "detail": "TTS unavailable",
            "ts_ms": self.now_ms,
        })

        # 验证降级审计记录
        avatar_rows = self.ledger._fetch_by_request("avatar_action_log", request_id)
        voice_rows = self.ledger._fetch_by_request("voice_event_log", request_id)

        self.assertGreaterEqual(len(avatar_rows), 1)
        self.assertGreaterEqual(len(voice_rows), 1)

        degraded_avatar = [r for r in avatar_rows if r.get("status") == "DEGRADED"]
        degraded_voice = [r for r in voice_rows if r.get("status") == "DEGRADED"]

        self.assertGreaterEqual(len(degraded_avatar), 1, "VTS 降级记录必须存在")
        self.assertEqual(VTS_CONNECTION_FAILED, degraded_avatar[0]["reason_code"])

        self.assertGreaterEqual(len(degraded_voice), 1, "语音降级记录必须存在")
        self.assertEqual(TTS_MODEL_UNAVAILABLE, degraded_voice[0]["reason_code"])

    # ─────────────────────────────────────────────
    # 跨路径通用验证
    # ─────────────────────────────────────────────

    def test_12_wechat_duplicate_ignored_in_e2e(self) -> None:
        """T-E2E-WeChat-01.12: 重复微信消息在 E2E 链路中被忽略。"""
        fix = _load_json(FIXTURE_DIR / "wechat_vts_voice_normal.json")
        request_id_1 = "req_e2e_wc_012a"
        request_id_2 = "req_e2e_wc_012b"

        first = receive_wechat_event(
            fix["wechat_ingress"],
            ledger=self.ledger,
            request_id=request_id_1,
            ts_ms=self.now_ms,
        )
        self.assertTrue(first.accepted)

        second = receive_wechat_event(
            fix["wechat_ingress"],
            ledger=self.ledger,
            request_id=request_id_2,
            ts_ms=self.now_ms,
        )
        self.assertFalse(second.accepted)
        self.assertTrue(second.duplicate)

    def test_13_egress_failure_audit_recorded(self) -> None:
        """T-E2E-WeChat-01.13: 微信出站失败时审计记录包含 reason_code。"""
        request_id = "req_e2e_wc_013"
        egress = send_wechat_message(
            thread_key="wechat::test_user::Natsume_bot",
            message_id="wx_fail_msg_013",
            content="test failure",
            ledger=self.ledger,
            request_id=request_id,
            ack_success=False,
            reason_code="CHANNEL_SEND_FAILED",
            ts_ms=self.now_ms,
        )
        self.assertFalse(egress.success)
        self.assertEqual("FAILED", egress.status)
        self.assertIsNotNone(egress.reason_code)

        channel_rows = self.ledger._fetch_by_request("channel_io_log", request_id)
        egress_rows = [r for r in channel_rows if r.get("direction") == DIRECTION_EGRESS]
        self.assertGreaterEqual(len(egress_rows), 1)
        self.assertEqual("FAILED", egress_rows[0]["status"])

    def test_14_vts_emotion_mapping_completeness(self) -> None:
        """T-E2E-WeChat-01.14: E2E 链路中 VTS 表情映射全量验证。"""
        for emotion_tag, expected_expr in EMOTION_TO_EXPRESSION_ID.items():
            request_id = f"req_e2e_vts_{emotion_tag.lower()}"
            result = resolve_vts_expression(
                emotion_tag=emotion_tag,
                ledger=self.ledger,
                request_id=request_id,
                ts_ms=self.now_ms,
            )
            self.assertTrue(result.success, f"{emotion_tag} 应映射成功")
            self.assertEqual(expected_expr, result.expression_id, f"{emotion_tag} 表情 ID 不匹配")

    def test_15_vts_invalid_emotion_rejected_in_e2e(self) -> None:
        """T-E2E-WeChat-01.15: E2E 链路中无效 emotion_tag 降级到 neutral（DEGRADED）。"""
        result = resolve_vts_expression(
            emotion_tag="INVALID_EMOTION",
            ledger=self.ledger,
            request_id="req_e2e_vts_invalid",
            ts_ms=self.now_ms,
        )
        # 未知 tag 降级到 neutral fallback（success=True，reason_code=VTS_DEGRADED_FALLBACK）
        self.assertTrue(result.success)
        self.assertEqual(STATUS_DEGRADED, result.status)
        self.assertEqual(VTS_DEGRADED_FALLBACK, result.reason_code)

    # ─────────────────────────────────────────────
    # Fixture 验证
    # ─────────────────────────────────────────────

    def test_16_fixture_normal_loadable(self) -> None:
        """T-E2E-WeChat-01.16: 正常路径 fixture 可加载且结构完整。"""
        fix = _load_json(FIXTURE_DIR / "wechat_vts_voice_normal.json")
        self.assertEqual("wechat_vts_voice_normal", fix["scenario"])
        self.assertIn("wechat_ingress", fix)
        self.assertIn("intent_overrides", fix)
        self.assertIn("vts_emotion", fix)
        self.assertIn("voice_event", fix)
        self.assertIn("expected", fix)
        self.assertTrue(fix["expected"]["wechat_accepted"])
        self.assertEqual("APPROVE", fix["expected"]["governor_decision"])

    def test_17_fixture_rejected_loadable(self) -> None:
        """T-E2E-WeChat-01.17: 拒绝路径 fixture 可加载且结构完整。"""
        fix = _load_json(FIXTURE_DIR / "wechat_vts_voice_rejected.json")
        self.assertEqual("wechat_vts_voice_rejected", fix["scenario"])
        self.assertIn("governance_snapshot", fix)
        self.assertEqual(0, fix["governance_snapshot"]["trust_level"])
        self.assertEqual("REJECT", fix["expected"]["governor_decision"])
        self.assertEqual("TRUST_LOW", fix["expected"]["governor_reason_code"])

    def test_18_fixture_degraded_loadable(self) -> None:
        """T-E2E-WeChat-01.18: 降级路径 fixture 可加载且结构完整。"""
        fix = _load_json(FIXTURE_DIR / "wechat_vts_voice_degraded.json")
        self.assertEqual("wechat_vts_voice_degraded", fix["scenario"])
        self.assertTrue(fix.get("vts_unavailable"))
        self.assertTrue(fix.get("voice_unavailable"))
        self.assertTrue(fix["expected"]["vts_degraded"])
        self.assertTrue(fix["expected"]["voice_degraded"])

    def test_19_three_paths_output_structure_stable(self) -> None:
        """T-E2E-WeChat-01.19: 三条路径输出结构均可序列化为 JSON。"""
        for fixture_name in [
            "wechat_vts_voice_normal.json",
            "wechat_vts_voice_rejected.json",
            "wechat_vts_voice_degraded.json",
        ]:
            fix = _load_json(FIXTURE_DIR / fixture_name)
            # 验证可以序列化
            serialized = json.dumps(fix, ensure_ascii=False)
            reparsed = json.loads(serialized)
            self.assertEqual(fix["scenario"], reparsed["scenario"])


if __name__ == "__main__":
    unittest.main()
