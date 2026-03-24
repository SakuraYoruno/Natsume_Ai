"""Unified Message Gateway — 契约测试套件。

覆盖范围：
  T-GW-MODEL-01~06  : UnifiedMessage / UnifiedReply / Platform / MsgType 数据模型
  T-GW-NORM-01~07   : MessageNormalizer 去重 / 脱敏 / 校验 / 统计
  T-GW-ROUTER-01~04 : GatewayRouter 路由逻辑（Mock Reporter）
  T-GW-SERVER-01~07 : create_gateway_app FastAPI 端点（TestClient）
  T-GW-OUTBOUND-01~05: OutboundDispatcher 分发逻辑
"""

from __future__ import annotations

import time
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from edge_client.gateway.models import (
    MsgType,
    Platform,
    UnifiedMessage,
    UnifiedReply,
)
from edge_client.gateway.normalizer import (
    MessageDeduplicator,
    MessageNormalizer,
    desensitize_content,
)
from edge_client.gateway.outbound import OutboundDispatcher
from edge_client.gateway.router import GatewayRouter
from edge_client.gateway.server import create_gateway_app


# ---------------------------------------------------------------------------
# 辅助工厂
# ---------------------------------------------------------------------------

def _make_msg(
    *,
    msg_id: str = "msg_001",
    platform: Platform = Platform.WECHAT,
    from_id: str = "wxid_user1",
    to_id: str = "wxid_bot",
    conversation_id: str = "conv_001",
    msg_type: MsgType = MsgType.TEXT,
    content: str = "你好",
    ts_ms: int = 1_700_000_000_000,
    is_group: bool = False,
) -> UnifiedMessage:
    return UnifiedMessage(
        msg_id=msg_id,
        platform=platform,
        from_id=from_id,
        to_id=to_id,
        conversation_id=conversation_id,
        msg_type=msg_type,
        content=content,
        ts_ms=ts_ms,
        is_group=is_group,
    )


def _make_reply(
    *,
    conversation_id: str = "conv_001",
    platform: Platform = Platform.WECHAT,
    content: str = "回复内容",
) -> UnifiedReply:
    return UnifiedReply(
        conversation_id=conversation_id,
        platform=platform,
        content=content,
    )


def _make_mock_reporter(*, success: bool = True, cached: bool = False) -> MagicMock:
    """创建 Mock EdgeInputReporter。"""
    reporter = MagicMock()
    result = MagicMock()
    result.success = success
    result.cached = cached
    reporter._send.return_value = result
    return reporter


def _make_mock_adapter(
    *,
    platform: Platform = Platform.WECHAT,
    parse_result: UnifiedMessage | None = None,
    send_result: bool = True,
) -> MagicMock:
    """创建 Mock 平台适配器。"""
    adapter = MagicMock()
    adapter.platform_id = platform
    adapter.parse_inbound.return_value = parse_result
    adapter.send_reply.return_value = send_result
    adapter.health.return_value = {"platform": platform.value, "status": "ok"}
    return adapter


# ===========================================================================
# T-GW-MODEL: 数据模型契约
# ===========================================================================

class TestGatewayModels(unittest.TestCase):
    """T-GW-MODEL-01~06: 数据模型契约。"""

    def test_t_gw_model_01_platform_enum_values(self) -> None:
        """T-GW-MODEL-01: Platform 枚举值正确。"""
        self.assertEqual("wechat", Platform.WECHAT.value)
        self.assertEqual("qq", Platform.QQ.value)
        self.assertEqual("feishu", Platform.FEISHU.value)
        self.assertEqual("wecom", Platform.WECOM.value)

    def test_t_gw_model_02_msgtype_from_wx_type_mapping(self) -> None:
        """T-GW-MODEL-02: MsgType.from_wx_type 微信类型映射正确。"""
        self.assertEqual(MsgType.TEXT, MsgType.from_wx_type(1))
        self.assertEqual(MsgType.TEXT, MsgType.from_wx_type("1"))
        self.assertEqual(MsgType.IMAGE, MsgType.from_wx_type(3))
        self.assertEqual(MsgType.VOICE, MsgType.from_wx_type(34))
        self.assertEqual(MsgType.VIDEO, MsgType.from_wx_type(43))
        self.assertEqual(MsgType.EMOJI, MsgType.from_wx_type(47))
        self.assertEqual(MsgType.LINK, MsgType.from_wx_type(49))
        self.assertEqual(MsgType.SYSTEM, MsgType.from_wx_type(10000))
        self.assertEqual(MsgType.SYSTEM, MsgType.from_wx_type(10002))
        self.assertEqual(MsgType.UNKNOWN, MsgType.from_wx_type(9999))

    def test_t_gw_model_03_unified_message_dedup_key(self) -> None:
        """T-GW-MODEL-03: UnifiedMessage.dedup_key 格式为 platform:msg_id。"""
        msg = _make_msg(platform=Platform.QQ, msg_id="qq_123")
        self.assertEqual("qq:qq_123", msg.dedup_key())

    def test_t_gw_model_04_unified_message_as_report_payload_required_fields(self) -> None:
        """T-GW-MODEL-04: as_report_payload 包含所有必填字段。"""
        msg = _make_msg()
        payload = msg.as_report_payload()
        for key in ("source", "platform", "msg_id", "from_id", "to_id",
                    "conversation_id", "msg_type", "content", "is_group", "ts_ms"):
            self.assertIn(key, payload, f"缺少字段: {key}")
        self.assertEqual("GATEWAY", payload["source"])
        self.assertEqual("wechat", payload["platform"])

    def test_t_gw_model_05_unified_message_as_report_payload_optional_fields(self) -> None:
        """T-GW-MODEL-05: as_report_payload 可选字段按需包含。"""
        msg = _make_msg()
        payload_no_opt = msg.as_report_payload()
        self.assertNotIn("sender_name", payload_no_opt)
        self.assertNotIn("media_url", payload_no_opt)

        msg.sender_name = "张三"
        msg.media_url = "/path/to/img.jpg"
        msg.reply_to_msg_id = "ref_001"
        payload_with_opt = msg.as_report_payload()
        self.assertEqual("张三", payload_with_opt["sender_name"])
        self.assertEqual("/path/to/img.jpg", payload_with_opt["media_url"])
        self.assertEqual("ref_001", payload_with_opt["reply_to_msg_id"])

    def test_t_gw_model_06_unified_reply_defaults(self) -> None:
        """T-GW-MODEL-06: UnifiedReply 默认值正确。"""
        reply = _make_reply()
        self.assertEqual(MsgType.TEXT, reply.msg_type)
        self.assertIsNone(reply.media_data)
        self.assertIsNone(reply.media_path)
        self.assertIsNone(reply.reply_to_msg_id)


# ===========================================================================
# T-GW-NORM: 消息标准化器契约
# ===========================================================================

class TestMessageNormalizer(unittest.TestCase):
    """T-GW-NORM-01~07: MessageNormalizer 契约。"""

    def setUp(self) -> None:
        self.normalizer = MessageNormalizer(
            dedup_window_s=60.0,
            enable_desensitize=True,
            skip_system_msgs=True,
        )

    def test_t_gw_norm_01_valid_message_passes(self) -> None:
        """T-GW-NORM-01: 合法消息通过标准化，返回 UnifiedMessage。"""
        msg = _make_msg()
        result = self.normalizer.normalize(msg)
        self.assertIsNotNone(result)
        self.assertEqual("msg_001", result.msg_id)

    def test_t_gw_norm_02_missing_from_id_rejected(self) -> None:
        """T-GW-NORM-02: from_id 为空时消息被丢弃，返回 None。"""
        msg = _make_msg(from_id="")
        result = self.normalizer.normalize(msg)
        self.assertIsNone(result)
        self.assertEqual(1, self.normalizer.stats()["invalid"])

    def test_t_gw_norm_03_missing_conversation_id_rejected(self) -> None:
        """T-GW-NORM-03: conversation_id 为空时消息被丢弃，返回 None。"""
        msg = _make_msg(conversation_id="")
        result = self.normalizer.normalize(msg)
        self.assertIsNone(result)

    def test_t_gw_norm_04_system_message_skipped(self) -> None:
        """T-GW-NORM-04: 系统消息被跳过（skip_system_msgs=True）。"""
        msg = _make_msg(msg_type=MsgType.SYSTEM)
        result = self.normalizer.normalize(msg)
        self.assertIsNone(result)
        self.assertEqual(1, self.normalizer.stats()["system_skipped"])

    def test_t_gw_norm_05_duplicate_message_filtered(self) -> None:
        """T-GW-NORM-05: 重复消息被过滤，第二次返回 None。"""
        msg1 = _make_msg(msg_id="dup_001")
        msg2 = _make_msg(msg_id="dup_001")  # 相同 msg_id
        r1 = self.normalizer.normalize(msg1)
        r2 = self.normalizer.normalize(msg2)
        self.assertIsNotNone(r1)
        self.assertIsNone(r2)
        self.assertEqual(1, self.normalizer.stats()["deduplicated"])

    def test_t_gw_norm_06_sensitive_content_desensitized(self) -> None:
        """T-GW-NORM-06: 含密码的文本消息被脱敏。"""
        msg = _make_msg(content="password: secret123")
        result = self.normalizer.normalize(msg)
        self.assertIsNotNone(result)
        self.assertIn("[REDACTED]", result.content)
        self.assertNotIn("secret123", result.content)
        self.assertEqual(1, self.normalizer.stats()["desensitized"])

    def test_t_gw_norm_07_stats_accumulate_correctly(self) -> None:
        """T-GW-NORM-07: 统计计数正确累积。"""
        # 1 valid + 1 invalid + 1 system + 1 dup
        self.normalizer.normalize(_make_msg(msg_id="s1"))
        self.normalizer.normalize(_make_msg(msg_id="s2", from_id=""))
        self.normalizer.normalize(_make_msg(msg_id="s3", msg_type=MsgType.SYSTEM))
        self.normalizer.normalize(_make_msg(msg_id="s1"))  # dup
        stats = self.normalizer.stats()
        self.assertEqual(4, stats["received"])
        self.assertEqual(1, stats["passed"])
        self.assertEqual(1, stats["invalid"])
        self.assertEqual(1, stats["system_skipped"])
        self.assertEqual(1, stats["deduplicated"])


class TestMessageDeduplicator(unittest.TestCase):
    """MessageDeduplicator 独立测试。"""

    def test_dedup_first_seen_returns_false(self) -> None:
        """首次见到的 key 返回 False（不重复）。"""
        dedup = MessageDeduplicator(window_s=60.0)
        self.assertFalse(dedup.is_duplicate("wechat:msg_001"))

    def test_dedup_second_seen_returns_true(self) -> None:
        """第二次见到相同 key 返回 True（重复）。"""
        dedup = MessageDeduplicator(window_s=60.0)
        dedup.is_duplicate("wechat:msg_001")
        self.assertTrue(dedup.is_duplicate("wechat:msg_001"))

    def test_dedup_zero_window_never_deduplicates(self) -> None:
        """window_s=0 时不去重，始终返回 False。"""
        dedup = MessageDeduplicator(window_s=0)
        dedup.is_duplicate("wechat:msg_001")
        self.assertFalse(dedup.is_duplicate("wechat:msg_001"))

    def test_desensitize_password_pattern(self) -> None:
        """password: xxx 被替换为 [REDACTED]。"""
        result = desensitize_content("password: abc123")
        self.assertIn("[REDACTED]", result)
        self.assertNotIn("abc123", result)

    def test_desensitize_chinese_verification_code(self) -> None:
        """中文验证码被替换。"""
        result = desensitize_content("验证码：123456")
        self.assertIn("[REDACTED]", result)

    def test_desensitize_clean_text_unchanged(self) -> None:
        """普通文本不被修改。"""
        text = "今天天气真好"
        self.assertEqual(text, desensitize_content(text))


# ===========================================================================
# T-GW-ROUTER: 消息路由器契约
# ===========================================================================

class TestGatewayRouter(unittest.TestCase):
    """T-GW-ROUTER-01~04: GatewayRouter 契约。"""

    def test_t_gw_router_01_route_success_increments_routed(self) -> None:
        """T-GW-ROUTER-01: 路由成功时 stats.routed 递增。"""
        reporter = _make_mock_reporter(success=True)
        router = GatewayRouter(reporter)
        msg = _make_msg()
        result = router.route(msg)
        self.assertTrue(result.success)
        self.assertEqual(1, router.stats()["routed"])
        self.assertEqual(0, router.stats()["failed"])

    def test_t_gw_router_02_route_failure_increments_failed(self) -> None:
        """T-GW-ROUTER-02: 路由失败时 stats.failed 递增。"""
        reporter = _make_mock_reporter(success=False, cached=False)
        router = GatewayRouter(reporter)
        msg = _make_msg()
        result = router.route(msg)
        self.assertFalse(result.success)
        self.assertEqual(1, router.stats()["failed"])

    def test_t_gw_router_03_route_cached_increments_cached(self) -> None:
        """T-GW-ROUTER-03: 路由缓存时 stats.cached 递增。"""
        reporter = _make_mock_reporter(success=False, cached=True)
        router = GatewayRouter(reporter)
        msg = _make_msg()
        router.route(msg)
        self.assertEqual(1, router.stats()["cached"])

    def test_t_gw_router_04_payload_contains_gateway_source(self) -> None:
        """T-GW-ROUTER-04: 上报 payload 中 source 字段为 GATEWAY。"""
        reporter = _make_mock_reporter(success=True)
        router = GatewayRouter(reporter)
        msg = _make_msg()
        router.route(msg)
        # 验证 _send 被调用，且第一个参数 payload 包含 source=GATEWAY
        call_args = reporter._send.call_args
        payload = call_args[0][0]
        self.assertEqual("GATEWAY", payload["source"])


# ===========================================================================
# T-GW-SERVER: Gateway FastAPI Server 契约
# ===========================================================================

_UNSET = object()  # sentinel，区分「未传入」和「传入 None」


class TestGatewayServer(unittest.TestCase):
    """T-GW-SERVER-01~07: create_gateway_app 端点契约。"""

    def _make_client(
        self,
        *,
        adapter_msg: UnifiedMessage | None | object = _UNSET,
        route_success: bool = True,
        with_outbound: bool = False,
    ) -> tuple[TestClient, MagicMock, MagicMock]:
        reporter = _make_mock_reporter(success=route_success)
        router = GatewayRouter(reporter)
        normalizer = MessageNormalizer(dedup_window_s=0)  # 禁用去重，避免测试间干扰

        # adapter_msg=_UNSET 表示「未传入」，使用默认消息；adapter_msg=None 表示「适配器返回 None」
        parse_result = _make_msg() if adapter_msg is _UNSET else adapter_msg
        adapter = _make_mock_adapter(
            platform=Platform.WECHAT,
            parse_result=parse_result,
        )
        adapters = {"wechat": adapter}

        outbound = None
        if with_outbound:
            outbound = MagicMock()
            outbound.dispatch.return_value = True

        app = create_gateway_app(
            router=router,
            normalizer=normalizer,
            adapters=adapters,
            outbound=outbound,
        )
        client = TestClient(app, raise_server_exceptions=False)
        return client, adapter, outbound

    def test_t_gw_server_01_webhook_accepted(self) -> None:
        """T-GW-SERVER-01: 合法消息 POST /webhook/wechat 返回 accepted。"""
        client, _, _ = self._make_client()
        resp = client.post(
            "/v1/gateway/wechat/webhook",
            json={"type": "1", "content": "hello"},
        )
        self.assertEqual(200, resp.status_code)
        data = resp.json()
        self.assertEqual("accepted", data["status"])
        self.assertIn("msg_id", data)

    def test_t_gw_server_02_webhook_unknown_platform_404(self) -> None:
        """T-GW-SERVER-02: 未注册平台返回 404。"""
        client, _, _ = self._make_client()
        resp = client.post(
            "/v1/gateway/unknown_platform/webhook",
            json={"type": "1"},
        )
        self.assertEqual(404, resp.status_code)

    def test_t_gw_server_03_webhook_adapter_returns_none_ignored(self) -> None:
        """T-GW-SERVER-03: 适配器返回 None 时响应 ignored。"""
        client, _, _ = self._make_client(adapter_msg=None)
        resp = client.post(
            "/v1/gateway/wechat/webhook",
            json={"type": "10000"},
        )
        self.assertEqual(200, resp.status_code)
        self.assertEqual("ignored", resp.json()["status"])

    def test_t_gw_server_04_health_returns_ok(self) -> None:
        """T-GW-SERVER-04: GET /health 返回 status=ok。"""
        client, _, _ = self._make_client()
        resp = client.get("/v1/gateway/health")
        self.assertEqual(200, resp.status_code)
        data = resp.json()
        self.assertEqual("ok", data["status"])
        self.assertIn("uptime_s", data)
        self.assertIn("adapters", data)

    def test_t_gw_server_05_stats_returns_normalizer_and_router(self) -> None:
        """T-GW-SERVER-05: GET /stats 返回 normalizer 和 router 统计。"""
        client, _, _ = self._make_client()
        resp = client.get("/v1/gateway/stats")
        self.assertEqual(200, resp.status_code)
        data = resp.json()
        self.assertIn("normalizer", data)
        self.assertIn("router", data)

    def test_t_gw_server_06_reply_without_outbound_503(self) -> None:
        """T-GW-SERVER-06: 未配置 OutboundDispatcher 时 POST /reply 返回 503。"""
        client, _, _ = self._make_client(with_outbound=False)
        resp = client.post(
            "/v1/gateway/reply",
            json={"platform": "wechat", "conversation_id": "conv_001", "content": "hi"},
        )
        self.assertEqual(503, resp.status_code)

    def test_t_gw_server_07_reply_with_outbound_dispatches(self) -> None:
        """T-GW-SERVER-07: 配置 OutboundDispatcher 后 POST /reply 成功分发。"""
        client, _, outbound = self._make_client(with_outbound=True)
        resp = client.post(
            "/v1/gateway/reply",
            json={
                "platform": "wechat",
                "conversation_id": "conv_001",
                "content": "你好",
                "msg_type": "text",
            },
        )
        self.assertEqual(200, resp.status_code)
        data = resp.json()
        self.assertEqual("sent", data["status"])
        outbound.dispatch.assert_called_once()


# ===========================================================================
# T-GW-OUTBOUND: 回复分发器契约
# ===========================================================================

class TestOutboundDispatcher(unittest.TestCase):
    """T-GW-OUTBOUND-01~05: OutboundDispatcher 契约。"""

    def test_t_gw_outbound_01_dispatch_success(self) -> None:
        """T-GW-OUTBOUND-01: 适配器发送成功时返回 True，stats.dispatched 递增。"""
        adapter = _make_mock_adapter(platform=Platform.WECHAT, send_result=True)
        dispatcher = OutboundDispatcher({Platform.WECHAT: adapter})
        reply = _make_reply(platform=Platform.WECHAT)
        result = dispatcher.dispatch(reply)
        self.assertTrue(result)
        self.assertEqual(1, dispatcher.stats()["dispatched"])

    def test_t_gw_outbound_02_dispatch_failure(self) -> None:
        """T-GW-OUTBOUND-02: 适配器发送失败时返回 False，stats.failed 递增。"""
        adapter = _make_mock_adapter(platform=Platform.QQ, send_result=False)
        dispatcher = OutboundDispatcher({Platform.QQ: adapter})
        reply = _make_reply(platform=Platform.QQ)
        result = dispatcher.dispatch(reply)
        self.assertFalse(result)
        self.assertEqual(1, dispatcher.stats()["failed"])

    def test_t_gw_outbound_03_no_adapter_returns_false(self) -> None:
        """T-GW-OUTBOUND-03: 未注册平台时返回 False，stats.no_adapter 递增。"""
        dispatcher = OutboundDispatcher()
        reply = _make_reply(platform=Platform.FEISHU)
        result = dispatcher.dispatch(reply)
        self.assertFalse(result)
        self.assertEqual(1, dispatcher.stats()["no_adapter"])

    def test_t_gw_outbound_04_register_adapter(self) -> None:
        """T-GW-OUTBOUND-04: register() 后可正常分发。"""
        dispatcher = OutboundDispatcher()
        adapter = _make_mock_adapter(platform=Platform.WECOM, send_result=True)
        dispatcher.register(adapter)
        reply = _make_reply(platform=Platform.WECOM)
        result = dispatcher.dispatch(reply)
        self.assertTrue(result)

    def test_t_gw_outbound_05_adapter_exception_returns_false(self) -> None:
        """T-GW-OUTBOUND-05: 适配器抛出异常时返回 False，stats.failed 递增。"""
        adapter = _make_mock_adapter(platform=Platform.WECHAT)
        adapter.send_reply.side_effect = RuntimeError("网络超时")
        dispatcher = OutboundDispatcher({Platform.WECHAT: adapter})
        reply = _make_reply(platform=Platform.WECHAT)
        result = dispatcher.dispatch(reply)
        self.assertFalse(result)
        self.assertEqual(1, dispatcher.stats()["failed"])


if __name__ == "__main__":
    unittest.main()
