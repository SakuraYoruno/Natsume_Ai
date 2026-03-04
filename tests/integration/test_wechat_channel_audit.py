"""T-CHAN-AUDIT-01~03: 微信通道审计链集成测试（M14-A）。

覆盖需求：FR-CHANNEL-001
验证：outbound/inbound 事件可在审计链追溯到 thread_key + request_id；
      失败事件含 reason_code + stage，可回放到 intent_id。
"""

from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest

from host_brain.channels.wechat import (
    receive_wechat_event_v2,
    send_wechat_message_authorized,
)
from host_brain.core.errors import CHANNEL_SEND_UNAUTHORIZED


# ── 夹具 ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def ledger():
    m = MagicMock()
    m.has_channel_message.return_value = False
    m.log_channel_io = MagicMock()
    return m


# ── T-CHAN-AUDIT-01: outbound 事件可在审计链追溯到 thread_key + request_id ────

class TestOutboundAuditChain:
    """T-CHAN-AUDIT-01: outbound 事件可在审计链追溯到 thread_key + request_id。"""

    def test_ledger_receives_thread_key(self, ledger):
        result = send_wechat_message_authorized(
            thread_key="wechat::user_alice::bot",
            message_id="msg_out_audit_01",
            content="审计测试",
            intent_id="intent_audit_01",
            approval_token="tok_audit",
            ledger=ledger,
            request_id="req_audit_01",
        )
        logged = ledger.log_channel_io.call_args[0][0]
        assert logged["thread_key"] == "wechat::user_alice::bot"

    def test_ledger_receives_request_id(self, ledger):
        result = send_wechat_message_authorized(
            thread_key="wechat::user_alice::bot",
            message_id="msg_out_audit_01",
            content="审计测试",
            intent_id="intent_audit_01",
            approval_token="tok_audit",
            ledger=ledger,
            request_id="req_audit_01",
        )
        logged = ledger.log_channel_io.call_args[0][0]
        assert logged["request_id"] == "req_audit_01"

    def test_audit_ref_contains_request_id(self, ledger):
        result = send_wechat_message_authorized(
            thread_key="wechat::user_alice::bot",
            message_id="msg_out_audit_01",
            content="审计测试",
            intent_id="intent_audit_01",
            approval_token="tok_audit",
            ledger=ledger,
            request_id="req_audit_01",
        )
        assert "req_audit_01" in result.audit_ref

    def test_direction_is_egress(self, ledger):
        send_wechat_message_authorized(
            thread_key="wechat::user_alice::bot",
            message_id="msg_out_audit_01",
            content="审计测试",
            intent_id="intent_audit_01",
            approval_token="tok_audit",
            ledger=ledger,
        )
        logged = ledger.log_channel_io.call_args[0][0]
        assert logged["direction"] == "EGRESS"


# ── T-CHAN-AUDIT-02: inbound 事件可在审计链追溯到 thread_key + request_id ─────

class TestInboundAuditChain:
    """T-CHAN-AUDIT-02: inbound 事件可在审计链追溯到 thread_key + request_id。"""

    def test_ledger_receives_thread_key(self, ledger):
        raw = {
            "message_id": "msg_in_audit_02",
            "from_user_id": "user_bob",
            "to_user_id": "bot",
            "content": "你好",
            "thread_key": "wechat::user_bob::bot",
        }
        receive_wechat_event_v2(raw, ledger=ledger, request_id="req_audit_02")
        logged = ledger.log_channel_io.call_args[0][0]
        assert logged["thread_key"] == "wechat::user_bob::bot"

    def test_ledger_receives_request_id(self, ledger):
        raw = {
            "message_id": "msg_in_audit_02",
            "from_user_id": "user_bob",
            "to_user_id": "bot",
            "content": "你好",
        }
        receive_wechat_event_v2(raw, ledger=ledger, request_id="req_audit_02")
        logged = ledger.log_channel_io.call_args[0][0]
        assert logged["request_id"] == "req_audit_02"

    def test_direction_is_ingress(self, ledger):
        raw = {
            "message_id": "msg_in_audit_02",
            "from_user_id": "user_bob",
            "content": "你好",
        }
        receive_wechat_event_v2(raw, ledger=ledger)
        logged = ledger.log_channel_io.call_args[0][0]
        assert logged["direction"] == "INGRESS"


# ── T-CHAN-AUDIT-03: 失败事件含 reason_code + stage，可回放到 intent_id ────────

class TestFailureAuditChain:
    """T-CHAN-AUDIT-03: 失败事件含 reason_code + stage，可回放到 intent_id。"""

    def test_unauthorized_has_reason_code(self, ledger):
        result = send_wechat_message_authorized(
            thread_key="wechat::user_alice::bot",
            message_id="msg_fail_03",
            content="失败测试",
            intent_id="intent_fail_03",
            approval_token="",
            ledger=ledger,
        )
        assert result.reason_code == CHANNEL_SEND_UNAUTHORIZED

    def test_unauthorized_has_stage(self, ledger):
        result = send_wechat_message_authorized(
            thread_key="wechat::user_alice::bot",
            message_id="msg_fail_03",
            content="失败测试",
            intent_id="intent_fail_03",
            approval_token="",
            ledger=ledger,
        )
        assert result.stage is not None

    def test_unauthorized_intent_id_preserved(self, ledger):
        result = send_wechat_message_authorized(
            thread_key="wechat::user_alice::bot",
            message_id="msg_fail_03",
            content="失败测试",
            intent_id="intent_fail_03",
            approval_token="",
            ledger=ledger,
        )
        # 即使失败，intent_id 仍可追溯
        assert result.intent_id == "intent_fail_03"

    def test_failed_send_has_reason_code_in_result(self, ledger):
        result = send_wechat_message_authorized(
            thread_key="wechat::user_alice::bot",
            message_id="msg_fail_03b",
            content="失败测试",
            intent_id="intent_fail_03b",
            approval_token="tok_valid",
            ledger=ledger,
            ack_success=False,
        )
        assert result.reason_code is not None
