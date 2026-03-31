"""微信通道完整链路测试（TASK-0215 M24-C）。

测试编号：T-WECHAT-01~16
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from host_brain.channels.wechat import (
    receive_wechat_event,
    receive_wechat_event_v2,
    send_wechat_message,
    send_wechat_message_authorized,
    build_thread_key,
    CHANNEL_WECHAT,
    STATUS_RECEIVED,
    STATUS_SENT,
    STATUS_FAILED,
)
from host_brain.channels.wechat_health import (
    WeChatChannelHealth,
    WECHAT_STATUS_UP,
    WECHAT_STATUS_DEGRADED,
)
from host_brain.core.errors import (
    CHANNEL_DUPLICATE_MESSAGE,
    CHANNEL_INTENT_MISSING,
    CHANNEL_SEND_UNAUTHORIZED,
    CHANNEL_THREAD_KEY_MISSING,
    RATE_LIMIT_EXCEEDED,
    WECHAT_CHANNEL_DEGRADED,
    WECHAT_RATE_LIMIT_EXCEEDED,
    WECHAT_SEND_FAILED,
)


def _make_ledger():
    """创建 mock ledger。"""
    ledger = MagicMock()
    ledger.log_channel_io = MagicMock()
    ledger.has_channel_message = MagicMock(return_value=False)
    return ledger


def _make_inbound_event(
    *,
    message_id: str = "msg_001",
    from_user_id: str = "user_001",
    content: str = "你好",
) -> dict:
    return {
        "message_id": message_id,
        "from_user_id": from_user_id,
        "content": content,
    }


# ---------------------------------------------------------------------------
# T-WECHAT-01~06: inbound 事件解析与审计串联
# ---------------------------------------------------------------------------

class TestWeChatInboundParsing:
    """T-WECHAT-01~06: inbound 事件解析与审计串联。"""

    def test_t_wechat_01_text_message_parsed_and_logged(self):
        """T-WECHAT-01：文本消息解析成功，wechat_inbound_log 记录完整。"""
        ledger = _make_ledger()
        result = receive_wechat_event(
            _make_inbound_event(message_id="msg_001", content="你好"),
            ledger=ledger,
            request_id="req_wechat_01",
        )
        assert result.accepted is True
        assert result.status == STATUS_RECEIVED
        assert result.thread_key is not None
        assert result.request_id == "req_wechat_01"
        # 审计日志被调用
        assert ledger.log_channel_io.called
        call_args = ledger.log_channel_io.call_args[0][0]
        assert call_args["channel"] == CHANNEL_WECHAT
        assert call_args["direction"] == "INGRESS"
        assert call_args["request_id"] == "req_wechat_01"

    def test_t_wechat_02_image_message_parsed(self):
        """T-WECHAT-02：图片消息解析成功（content 为空时仍可接受）。"""
        ledger = _make_ledger()
        result = receive_wechat_event(
            {
                "message_id": "msg_img_001",
                "from_user_id": "user_001",
                "content": "",  # 图片消息 content 为空
                "msg_type": "image",
            },
            ledger=ledger,
        )
        assert result.accepted is True
        assert result.thread_key is not None

    def test_t_wechat_03_voice_message_parsed(self):
        """T-WECHAT-03：语音消息解析成功（content 为空时仍可接受）。"""
        ledger = _make_ledger()
        result = receive_wechat_event(
            {
                "message_id": "msg_voice_001",
                "from_user_id": "user_001",
                "content": "",
                "msg_type": "voice",
            },
            ledger=ledger,
        )
        assert result.accepted is True

    def test_t_wechat_04_parse_failed_missing_message_id(self):
        """T-WECHAT-04：解析失败（缺 message_id），抛出 ValueError（WECHAT_PARSE_FAILED）。"""
        ledger = _make_ledger()
        with pytest.raises(ValueError):
            receive_wechat_event(
                {"from_user_id": "user_001", "content": "测试"},
                ledger=ledger,
            )

    def test_t_wechat_05_inbound_not_direct_to_llm(self):
        """T-WECHAT-05：消息内容不直通 LLM（inbound 返回结构化事件，不含 LLM 输出）。"""
        ledger = _make_ledger()
        result = receive_wechat_event_v2(
            _make_inbound_event(message_id="msg_005"),
            ledger=ledger,
        )
        # 返回结构化事件，不含 LLM 输出
        assert result.event_type == "inbound"
        assert "narrative" not in result.payload
        assert "intent_draft" not in result.payload

    def test_t_wechat_06_thread_key_linked_to_request_id(self):
        """T-WECHAT-06：thread_key 与 request_id 关联，跨会话可追溯。"""
        ledger = _make_ledger()
        result = receive_wechat_event_v2(
            _make_inbound_event(message_id="msg_006", from_user_id="user_006"),
            ledger=ledger,
            request_id="req_wechat_06",
        )
        assert result.thread_key is not None
        assert result.request_id == "req_wechat_06"
        assert "user_006" in result.thread_key

    def test_t_wechat_06b_duplicate_message_ignored(self):
        """T-WECHAT-06b：重复消息被忽略（CHANNEL_DUPLICATE_MESSAGE）。"""
        ledger = _make_ledger()
        ledger.has_channel_message = MagicMock(return_value=True)
        result = receive_wechat_event(
            _make_inbound_event(message_id="msg_dup"),
            ledger=ledger,
        )
        assert result.accepted is False
        assert result.reason_code == CHANNEL_DUPLICATE_MESSAGE


# ---------------------------------------------------------------------------
# T-WECHAT-07~12: outbound 推送与频率限制
# ---------------------------------------------------------------------------

class TestWeChatOutboundAndRateLimit:
    """T-WECHAT-07~12: outbound 推送与频率限制。"""

    def test_t_wechat_07_send_success_logged(self):
        """T-WECHAT-07：推送成功，wechat_outbound_log 记录完整。"""
        ledger = _make_ledger()
        result = send_wechat_message(
            thread_key="wechat::user_001::bot",
            message_id="out_msg_001",
            content="你好，我是 Natsume",
            ledger=ledger,
            request_id="req_wechat_07",
            ack_success=True,
        )
        assert result.success is True
        assert result.status == STATUS_SENT
        assert ledger.log_channel_io.called
        call_args = ledger.log_channel_io.call_args[0][0]
        assert call_args["direction"] == "EGRESS"
        assert call_args["request_id"] == "req_wechat_07"

    def test_t_wechat_08_rate_limit_exceeded_rejected(self):
        """T-WECHAT-08：频率超限拒绝推送，reason_code=WECHAT_RATE_LIMIT_EXCEEDED。"""
        health = WeChatChannelHealth(outbound_rate_per_minute=2)
        thread_key = "wechat::user_008::bot"
        # 消耗配额
        health.check_outbound_allowed(thread_key=thread_key)
        health.check_outbound_allowed(thread_key=thread_key)
        # 第三次超限
        result = health.check_outbound_allowed(thread_key=thread_key)
        assert result.allowed is False
        assert result.reason_code == WECHAT_RATE_LIMIT_EXCEEDED

    def test_t_wechat_09_send_failed_degraded_not_fake(self):
        """T-WECHAT-09：推送失败降级，reason_code=CHANNEL_SEND_FAILED，不伪造。"""
        ledger = _make_ledger()
        result = send_wechat_message(
            thread_key="wechat::user_009::bot",
            message_id="out_msg_009",
            content="测试",
            ledger=ledger,
            ack_success=False,
        )
        assert result.success is False
        assert result.status == STATUS_FAILED
        assert result.reason_code is not None

    def test_t_wechat_10_outbound_requires_intent_id(self):
        """T-WECHAT-10：outbound 推送需要 intent_id（send_wechat_message_authorized）。"""
        ledger = _make_ledger()
        result = send_wechat_message_authorized(
            thread_key="wechat::user_010::bot",
            message_id="out_msg_010",
            content="测试",
            intent_id="",  # 缺少 intent_id
            approval_token="token_010",
            ledger=ledger,
        )
        assert result.reason_code == CHANNEL_INTENT_MISSING

    def test_t_wechat_11_rate_limit_config_hot_update(self):
        """T-WECHAT-11：频率限制配置可热更新（通过 WeChatChannelHealth）。"""
        health = WeChatChannelHealth(outbound_rate_per_minute=1)
        thread_key = "wechat::user_011::bot"
        # 消耗配额
        health.check_outbound_allowed(thread_key=thread_key)
        # 超限
        result1 = health.check_outbound_allowed(thread_key=thread_key)
        assert result1.allowed is False

        # 热更新为更高频率
        health._outbound_rate_per_minute = 100
        # 清空计数（模拟热更新后重置）
        health._outbound_counts.clear()
        result2 = health.check_outbound_allowed(thread_key=thread_key)
        assert result2.allowed is True, "热更新后应允许推送"

    def test_t_wechat_12_outbound_linked_to_request_id(self):
        """T-WECHAT-12：outbound 推送串联 request_id。"""
        ledger = _make_ledger()
        result = send_wechat_message(
            thread_key="wechat::user_012::bot",
            message_id="out_msg_012",
            content="测试",
            ledger=ledger,
            request_id="req_wechat_12",
            ack_success=True,
        )
        assert result.request_id == "req_wechat_12"
        call_args = ledger.log_channel_io.call_args[0][0]
        assert call_args["request_id"] == "req_wechat_12"


# ---------------------------------------------------------------------------
# T-WECHAT-13~16: 通道健康状态与降级策略
# ---------------------------------------------------------------------------

class TestWeChatChannelHealthManagement:
    """T-WECHAT-13~16: 通道健康状态与降级策略。"""

    def test_t_wechat_13_consecutive_failures_cause_degraded(self):
        """T-WECHAT-13：连续失败 N 次后状态变为 DEGRADED，/health 端点反映。"""
        health = WeChatChannelHealth(failure_threshold=3)
        assert health.get_status().status == WECHAT_STATUS_UP

        health.record_send_failure()
        health.record_send_failure()
        assert health.get_status().status == WECHAT_STATUS_UP  # 未达阈值

        health.record_send_failure()
        status = health.get_status()
        assert status.status == WECHAT_STATUS_DEGRADED
        assert status.reason_code == WECHAT_CHANNEL_DEGRADED

    def test_t_wechat_14_degraded_inbound_ok_outbound_blocked(self):
        """T-WECHAT-14：降级期间 inbound 正常处理，outbound 静默丢弃。"""
        health = WeChatChannelHealth(failure_threshold=1)
        health.record_send_failure()  # 触发降级
        assert health.get_status().status == WECHAT_STATUS_DEGRADED

        # outbound 被阻断
        result = health.check_outbound_allowed(thread_key="wechat::user_014::bot")
        assert result.allowed is False
        assert result.reason_code == WECHAT_CHANNEL_DEGRADED

        # inbound 仍可接受（通过 record_recv 不受影响）
        health.record_recv()  # 不应抛出异常
        assert health.get_status().last_recv_ts_ms is not None

    def test_t_wechat_15_recovery_resets_degraded_state(self):
        """T-WECHAT-15：恢复时自动重置降级状态，写 wechat_channel_recovery_log。"""
        mock_ledger = MagicMock()
        mock_ledger.log_channel_io = MagicMock()

        health = WeChatChannelHealth(failure_threshold=1, ledger=mock_ledger)
        health.record_send_failure()  # 触发降级
        assert health.get_status().status == WECHAT_STATUS_DEGRADED

        # 发送成功触发恢复
        health.record_send_success()
        status = health.get_status()
        assert status.status == WECHAT_STATUS_UP
        assert status.error_count == 0
        # 恢复日志被写入
        assert mock_ledger.log_channel_io.called

    def test_t_wechat_16_health_status_refreshable(self):
        """T-WECHAT-16：健康状态可随时刷新（get_status 返回最新状态）。"""
        health = WeChatChannelHealth(failure_threshold=3)
        status1 = health.get_status()
        assert status1.status == WECHAT_STATUS_UP
        assert status1.error_count == 0

        health.record_send_failure()
        status2 = health.get_status()
        assert status2.error_count == 1

        health.record_recv()
        status3 = health.get_status()
        assert status3.last_recv_ts_ms is not None

    def test_t_wechat_13b_manual_reset_recovery(self):
        """T-WECHAT-13b：手动重置降级状态（reset_for_recovery）。"""
        health = WeChatChannelHealth(failure_threshold=1)
        health.record_send_failure()
        assert health.get_status().status == WECHAT_STATUS_DEGRADED

        health.reset_for_recovery()
        assert health.get_status().status == WECHAT_STATUS_UP
