"""T-CHAN-01~05: 微信通道 I/O 事件化单元测试（M14-A）。

覆盖需求：FR-CHANNEL-001
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from host_brain.channels.wechat import (
    WeChatChannelEvent,
    receive_wechat_event_v2,
    send_wechat_message_authorized,
)
from host_brain.core.errors import (
    CHANNEL_INTENT_MISSING,
    CHANNEL_SEND_UNAUTHORIZED,
    CHANNEL_THREAD_KEY_MISSING,
)


# ── 夹具 ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def ledger():
    m = MagicMock()
    m.has_channel_message.return_value = False
    m.log_channel_io = MagicMock()
    return m


@pytest.fixture
def good_raw_event():
    return {
        "message_id": "msg_001",
        "from_user_id": "user_alice",
        "to_user_id": "bot",
        "content": "你好",
        "thread_key": "wechat::user_alice::bot",
    }


# ── T-CHAN-01: receive 成功，返回 WeChatChannelEvent（inbound），写审计链 ──────

class TestReceiveWeChatEventV2:
    """T-CHAN-01: receive 成功，返回 WeChatChannelEvent（inbound），写审计链。"""

    def test_returns_channel_event(self, ledger, good_raw_event):
        result = receive_wechat_event_v2(good_raw_event, ledger=ledger)
        assert isinstance(result, WeChatChannelEvent)

    def test_event_type_is_inbound(self, ledger, good_raw_event):
        result = receive_wechat_event_v2(good_raw_event, ledger=ledger)
        assert result.event_type == "inbound"

    def test_thread_key_present(self, ledger, good_raw_event):
        result = receive_wechat_event_v2(good_raw_event, ledger=ledger)
        assert result.thread_key == "wechat::user_alice::bot"

    def test_request_id_present(self, ledger, good_raw_event):
        result = receive_wechat_event_v2(good_raw_event, ledger=ledger)
        assert result.request_id

    def test_audit_ref_present(self, ledger, good_raw_event):
        result = receive_wechat_event_v2(good_raw_event, ledger=ledger)
        assert result.audit_ref.startswith("channel_io_log::")

    def test_ledger_log_called(self, ledger, good_raw_event):
        receive_wechat_event_v2(good_raw_event, ledger=ledger)
        ledger.log_channel_io.assert_called_once()

    def test_no_reason_code_on_success(self, ledger, good_raw_event):
        result = receive_wechat_event_v2(good_raw_event, ledger=ledger)
        assert result.reason_code is None


# ── T-CHAN-02: send 成功，返回 WeChatChannelEvent（outbound），写审计链 ─────────

class TestSendWeChatMessageAuthorized:
    """T-CHAN-02: send 成功，返回 WeChatChannelEvent（outbound），写审计链。"""

    def test_returns_channel_event(self, ledger):
        result = send_wechat_message_authorized(
            thread_key="wechat::user_alice::bot",
            message_id="msg_out_001",
            content="你好呀",
            intent_id="intent_001",
            approval_token="tok_abc",
            ledger=ledger,
        )
        assert isinstance(result, WeChatChannelEvent)

    def test_event_type_is_outbound(self, ledger):
        result = send_wechat_message_authorized(
            thread_key="wechat::user_alice::bot",
            message_id="msg_out_001",
            content="你好呀",
            intent_id="intent_001",
            approval_token="tok_abc",
            ledger=ledger,
        )
        assert result.event_type == "outbound"

    def test_audit_ref_present(self, ledger):
        result = send_wechat_message_authorized(
            thread_key="wechat::user_alice::bot",
            message_id="msg_out_001",
            content="你好呀",
            intent_id="intent_001",
            approval_token="tok_abc",
            ledger=ledger,
        )
        assert result.audit_ref.startswith("channel_io_log::")

    def test_ledger_log_called(self, ledger):
        send_wechat_message_authorized(
            thread_key="wechat::user_alice::bot",
            message_id="msg_out_001",
            content="你好呀",
            intent_id="intent_001",
            approval_token="tok_abc",
            ledger=ledger,
        )
        ledger.log_channel_io.assert_called_once()

    def test_intent_id_in_result(self, ledger):
        result = send_wechat_message_authorized(
            thread_key="wechat::user_alice::bot",
            message_id="msg_out_001",
            content="你好呀",
            intent_id="intent_001",
            approval_token="tok_abc",
            ledger=ledger,
        )
        assert result.intent_id == "intent_001"


# ── T-CHAN-03: send 无 approval_token → CHANNEL_SEND_UNAUTHORIZED ─────────────

class TestSendUnauthorized:
    """T-CHAN-03: send 无 approval_token → CHANNEL_SEND_UNAUTHORIZED。"""

    def test_empty_token_returns_unauthorized(self, ledger):
        result = send_wechat_message_authorized(
            thread_key="wechat::user_alice::bot",
            message_id="msg_out_002",
            content="你好",
            intent_id="intent_002",
            approval_token="",
            ledger=ledger,
        )
        assert result.reason_code == CHANNEL_SEND_UNAUTHORIZED

    def test_none_token_returns_unauthorized(self, ledger):
        result = send_wechat_message_authorized(
            thread_key="wechat::user_alice::bot",
            message_id="msg_out_002",
            content="你好",
            intent_id="intent_002",
            approval_token=None,  # type: ignore[arg-type]
            ledger=ledger,
        )
        assert result.reason_code == CHANNEL_SEND_UNAUTHORIZED

    def test_no_ledger_write_on_unauthorized(self, ledger):
        send_wechat_message_authorized(
            thread_key="wechat::user_alice::bot",
            message_id="msg_out_002",
            content="你好",
            intent_id="intent_002",
            approval_token="",
            ledger=ledger,
        )
        ledger.log_channel_io.assert_not_called()


# ── T-CHAN-04: send 无 intent_id → CHANNEL_INTENT_MISSING ────────────────────

class TestSendIntentMissing:
    """T-CHAN-04: send 无 intent_id → CHANNEL_INTENT_MISSING。"""

    def test_empty_intent_id(self, ledger):
        result = send_wechat_message_authorized(
            thread_key="wechat::user_alice::bot",
            message_id="msg_out_003",
            content="你好",
            intent_id="",
            approval_token="tok_abc",
            ledger=ledger,
        )
        assert result.reason_code == CHANNEL_INTENT_MISSING

    def test_none_intent_id(self, ledger):
        result = send_wechat_message_authorized(
            thread_key="wechat::user_alice::bot",
            message_id="msg_out_003",
            content="你好",
            intent_id=None,  # type: ignore[arg-type]
            approval_token="tok_abc",
            ledger=ledger,
        )
        assert result.reason_code == CHANNEL_INTENT_MISSING


# ── T-CHAN-05: 缺 thread_key → CHANNEL_THREAD_KEY_MISSING ────────────────────

class TestSendThreadKeyMissing:
    """T-CHAN-05: 缺 thread_key → CHANNEL_THREAD_KEY_MISSING。"""

    def test_empty_thread_key(self, ledger):
        result = send_wechat_message_authorized(
            thread_key="",
            message_id="msg_out_004",
            content="你好",
            intent_id="intent_004",
            approval_token="tok_abc",
            ledger=ledger,
        )
        assert result.reason_code == CHANNEL_THREAD_KEY_MISSING

    def test_none_thread_key(self, ledger):
        result = send_wechat_message_authorized(
            thread_key=None,  # type: ignore[arg-type]
            message_id="msg_out_004",
            content="你好",
            intent_id="intent_004",
            approval_token="tok_abc",
            ledger=ledger,
        )
        assert result.reason_code == CHANNEL_THREAD_KEY_MISSING
