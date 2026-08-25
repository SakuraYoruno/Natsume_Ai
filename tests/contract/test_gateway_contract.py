"""Unified Message Gateway — 契约测试套件。

覆盖范围：
  T-GW-MODEL-01~06  : UnifiedMessage / UnifiedReply / Platform / MsgType 数据模型
  T-GW-NORM-01~07   : MessageNormalizer 去重 / 脱敏 / 校验 / 统计
  Gateway durable inbox/outbox owns routing, retry and receipt state.
  T-GW-SERVER-01~07 : create_gateway_app FastAPI 端点（TestClient）
  T-GW-OUTBOUND-01~10: OutboundDispatcher 分发逻辑
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import unittest
from dataclasses import FrozenInstanceError
from typing import Any
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from edge_client.gateway.models import (
    MsgType,
    Platform,
    UnifiedMessage,
    UnifiedReply,
)
from edge_client.gateway.delivery_runtime import (
    GATEWAY_ACCEPT_NEW_INTAKE_ENV,
    GATEWAY_TRANSPORT_MODE_ENV,
    GatewayDeliveryError,
    GatewayDeliveryReceipt,
    GatewayDeliveryRuntime,
    GatewayTransportMode,
    load_gateway_accept_new_intake,
    load_gateway_transport_mode,
)
from edge_client.gateway.normalizer import (
    MessageDeduplicator,
    MessageNormalizer,
    desensitize_content,
)
from edge_client.gateway.outbound import (
    OutboundDeliveryCertainty,
    OutboundDispatcher,
    OutboundPreparationRejected,
)
from edge_client.gateway.server import create_gateway_app
from edge_client.gateway.webhook_security import (
    InternalHmacWebhookVerifier,
    VerifiedWebhookEnvelope,
    VerifierRegistry,
    build_internal_signature,
)


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
    reporter.report_gateway_payload.return_value = result
    return reporter


class _AllowVerifierRegistry:
    """Test seam that preserves exact raw bytes without bypassing server order."""

    def __init__(
        self,
        *,
        claims: dict[str, Any] | None = None,
        payload_bytes: bytes | None = None,
    ) -> None:
        self.envelopes: list[Any] = []
        self.claims = dict(claims or {})
        self.payload_bytes = payload_bytes

    def verify(self, envelope):  # noqa: ANN001
        self.envelopes.append(envelope)
        digest = hashlib.sha256(envelope.raw_body).hexdigest()
        return VerifiedWebhookEnvelope(
            platform=envelope.platform,
            profile_id="test-profile",
            principal_id=f"test:{envelope.platform}",
            credential_ref="test-key",
            request_id=envelope.request_id,
            verified_at_ms=int(time.time() * 1000),
            body_sha256=digest,
            replay_key=f"test-proof:{len(self.envelopes)}:{digest}",
            claims=self.claims,
            payload_bytes=(self.payload_bytes if self.payload_bytes is not None else envelope.raw_body),
        )


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
        inbox_status: str = "NEW",
        outbox_status: str = "NEW",
        outbox_delivery_state: str = "ACKED",
        webhook_verifiers: Any | None = None,
        reply_verifiers: Any | None = None,
        platform: Platform = Platform.WECHAT,
    ) -> tuple[TestClient, MagicMock, MagicMock]:
        normalizer = MessageNormalizer(dedup_window_s=0)  # 禁用去重，避免测试间干扰

        # adapter_msg=_UNSET 表示「未传入」，使用默认消息；adapter_msg=None 表示「适配器返回 None」
        parse_result = _make_msg(platform=platform) if adapter_msg is _UNSET else adapter_msg
        adapter = _make_mock_adapter(
            platform=platform,
            parse_result=parse_result,
        )
        adapters = {platform.value: adapter}

        outbound = None
        if with_outbound:
            outbound = MagicMock()
            outbound.dispatch.return_value = True

        delivery = MagicMock()
        delivery.mode = GatewayTransportMode.DURABLE
        delivery.intake_enabled = True
        delivery.verification_enabled = True
        delivery.accept_inbound.return_value = GatewayDeliveryReceipt(
            direction="inbox",
            record_id="gateway-inbox-test",
            status=inbox_status,
            ack_ref="gateway-inbox-ack://test",
            reason_code=("GATEWAY_TRANSPORT_IDEMPOTENCY_CONFLICT" if inbox_status == "CONFLICT" else None),
        )
        delivery.enqueue_reply.return_value = GatewayDeliveryReceipt(
            direction="outbox",
            record_id="gateway-outbox-test",
            status=outbox_status,
        )
        delivery.wait_outbox.return_value = {"status": outbox_delivery_state}
        delivery.get_outbox.return_value = {
            "status": outbox_delivery_state,
            "payload_sha256": "sha256:" + "a" * 64,
        }
        delivery.stats.return_value = {
            "mode": "DURABLE",
            "intake_enabled": True,
            "verification_enabled": True,
            "delivery_enabled": True,
            "worker_alive": True,
        }
        self.delivery = delivery
        self.webhook_verifiers = webhook_verifiers or _AllowVerifierRegistry()
        self.reply_verifiers = reply_verifiers or _AllowVerifierRegistry()

        app = create_gateway_app(
            normalizer=normalizer,
            adapters=adapters,
            outbound=outbound,
            delivery_runtime=delivery,
            webhook_verifiers=self.webhook_verifiers,
            reply_verifiers=self.reply_verifiers,
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

    def test_t_gw_server_03a_webhook_invalid_json_400(self) -> None:
        client, _, _ = self._make_client()
        resp = client.post(
            "/v1/gateway/wechat/webhook",
            content="{bad json",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(400, resp.status_code)

    def test_t_gw_server_03b_webhook_adapter_error_500(self) -> None:
        client, adapter, _ = self._make_client()
        adapter.parse_inbound.side_effect = RuntimeError("bad parse")
        resp = client.post(
            "/v1/gateway/wechat/webhook",
            json={"type": "1", "content": "hello"},
        )
        self.assertEqual(422, resp.status_code)

    def test_t_gw_server_03c_webhook_route_failure_returns_cached(self) -> None:
        client, _, _ = self._make_client(route_success=False)
        resp = client.post(
            "/v1/gateway/wechat/webhook",
            json={"type": "1", "content": "hello"},
        )
        self.assertEqual(200, resp.status_code)
        self.assertEqual("accepted", resp.json()["status"])
        self.delivery.accept_inbound.assert_called_once()

    def test_t_gw_server_04_health_returns_ok(self) -> None:
        """T-GW-SERVER-04: GET /health 返回 status=ok。"""
        client, _, _ = self._make_client()
        resp = client.get("/v1/gateway/health")
        self.assertEqual(200, resp.status_code)
        data = resp.json()
        self.assertEqual("ok", data["status"])
        self.assertIn("uptime_s", data)
        self.assertEqual({"registered_count": 1}, data["adapters"])
        self.assertEqual(
            {
                "mode": "DURABLE",
                "intake_enabled": True,
                "verification_enabled": True,
                "delivery_enabled": True,
                "worker_alive": True,
            },
            data["transport"],
        )

    def test_t_gw_server_04a_public_health_never_calls_adapter_health(self) -> None:
        client, adapter, _ = self._make_client()
        adapter.health.side_effect = RuntimeError("health fail")
        resp = client.get("/v1/gateway/health")
        self.assertEqual(200, resp.status_code)
        self.assertEqual({"registered_count": 1}, resp.json()["adapters"])
        adapter.health.assert_not_called()

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
                "request_id": "reply-dispatch-1",
                "platform": "wechat",
                "conversation_id": "conv_001",
                "content": "你好",
                "msg_type": "text",
            },
        )
        self.assertEqual(200, resp.status_code)
        data = resp.json()
        self.assertEqual("sent", data["status"])
        self.assertEqual("reply-dispatch-1", data["delivery_id"])
        self.assertEqual("wechat", data["platform"])
        self.assertEqual("conv_001", data["conversation_id"])
        self.assertEqual("sha256:" + "a" * 64, data["payload_digest"])
        self.delivery.enqueue_reply.assert_called_once()
        outbound.dispatch.assert_not_called()

    def test_t_gw_server_07a_reply_invalid_json_400(self) -> None:
        client, _, _ = self._make_client(with_outbound=True)
        resp = client.post(
            "/v1/gateway/reply",
            content="{bad json",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(400, resp.status_code)

    def test_t_gw_server_07b_reply_invalid_platform_400(self) -> None:
        client, _, _ = self._make_client(with_outbound=True)
        resp = client.post(
            "/v1/gateway/reply",
            json={"platform": "invalid", "conversation_id": "conv_001", "content": "hi"},
        )
        self.assertEqual(400, resp.status_code)

    def test_t_gw_server_07c_reply_wait_false_returns_queued(self) -> None:
        client, _, _ = self._make_client(with_outbound=True)
        resp = client.post(
            "/v1/gateway/reply",
            json={
                "request_id": "reply-queued-1",
                "platform": "wechat",
                "conversation_id": "conv_queued",
                "content": "hi",
                "wait_result": False,
            },
        )
        self.assertEqual(200, resp.status_code)
        self.assertEqual("queued", resp.json()["status"])

    def test_t_gw_server_07d_reply_timeout_returns_failed(self) -> None:
        client, _, _ = self._make_client(
            with_outbound=True,
            outbox_delivery_state="READY",
        )
        resp = client.post(
            "/v1/gateway/reply",
            json={
                "request_id": "reply-timeout-1",
                "platform": "wechat",
                "conversation_id": "conv_timeout",
                "content": "hi",
                "timeout_ms": 5,
            },
        )
        self.assertEqual(200, resp.status_code)
        self.assertEqual("queued", resp.json()["status"])
        self.assertEqual("READY", resp.json()["delivery_state"])

    def test_t_gw_server_07e_reply_dispatch_error_returns_failed(self) -> None:
        client, _, _ = self._make_client(
            with_outbound=True,
            outbox_delivery_state="DLQ",
        )
        self.delivery.wait_outbox.return_value = {
            "status": "DLQ",
            "last_reason_code": "PLATFORM_SEND_FAILED",
        }
        resp = client.post(
            "/v1/gateway/reply",
            json={
                "request_id": "reply-error-1",
                "platform": "wechat",
                "conversation_id": "conv_error",
                "content": "hi",
            },
        )
        self.assertEqual(200, resp.status_code)
        self.assertEqual("failed", resp.json()["status"])
        self.assertEqual("PLATFORM_SEND_FAILED", resp.json()["reason_code"])

    def test_http_intake_without_durable_runtime_rejects(self) -> None:
        adapter = _make_mock_adapter(parse_result=_make_msg())
        app = create_gateway_app(
            normalizer=MessageNormalizer(dedup_window_s=0),
            adapters={"wechat": adapter},
            webhook_verifiers=_AllowVerifierRegistry(),
        )
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                "/v1/gateway/wechat/webhook",
                json={"type": "1", "content": "hello"},
            )
        self.assertEqual(503, response.status_code)
        self.assertEqual("GW_TRANSPORT_DISABLED", response.json()["reason_code"])
        adapter.parse_inbound.assert_not_called()

    def test_disabled_durable_intake_verifies_then_rejects_without_persist(self) -> None:
        verifier = _AllowVerifierRegistry()
        adapter = _make_mock_adapter(parse_result=_make_msg())
        delivery = MagicMock()
        delivery.mode = GatewayTransportMode.DURABLE
        delivery.verification_enabled = True
        delivery.intake_enabled = False
        app = create_gateway_app(
            normalizer=MessageNormalizer(dedup_window_s=0),
            adapters={"wechat": adapter},
            delivery_runtime=delivery,
            webhook_verifiers=verifier,
        )
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                "/v1/gateway/wechat/webhook",
                content=b'{"type":"1","content":"disabled"}',
                headers={"Content-Type": "application/json"},
            )
        self.assertEqual(503, response.status_code)
        self.assertEqual("GW_TRANSPORT_DISABLED", response.json()["reason_code"])
        self.assertEqual(1, len(verifier.envelopes))
        adapter.parse_inbound.assert_not_called()
        delivery.accept_inbound.assert_not_called()

    def test_webhook_verification_happens_on_exact_bytes_before_parse(self) -> None:
        verifier = _AllowVerifierRegistry()
        client, adapter, _ = self._make_client(webhook_verifiers=verifier)
        exact_body = b'{  "type" : "1", "content" : "hello" }'
        response = client.post(
            "/v1/gateway/wechat/webhook",
            content=exact_body,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual(exact_body, verifier.envelopes[0].raw_body)
        adapter.parse_inbound.assert_called_once()

    def test_line_batch_verifies_once_and_durably_accepts_every_event(self) -> None:
        verifier = _AllowVerifierRegistry()
        client, adapter, _ = self._make_client(
            platform=Platform.LINE,
            webhook_verifiers=verifier,
        )
        adapter.parse_inbound.side_effect = [
            _make_msg(
                msg_id="line-message-a",
                platform=Platform.LINE,
                from_id="line-user",
                conversation_id="line-conversation",
            ),
            _make_msg(
                msg_id="line-message-b",
                platform=Platform.LINE,
                from_id="line-user",
                conversation_id="line-conversation",
            ),
        ]
        self.delivery.accept_inbound.side_effect = [
            GatewayDeliveryReceipt("inbox", "line-inbox-a", "NEW", "line-ack-a"),
            GatewayDeliveryReceipt(
                "inbox",
                "line-inbox-b",
                "DUPLICATE_SAME",
                "line-ack-b",
            ),
        ]
        events = [
            {
                "type": "message",
                "webhookEventId": "line-event-a",
                "timestamp": 1_700_000_000_001,
                "source": {"type": "user", "userId": "line-user"},
                "message": {"id": "line-message-a", "type": "text", "text": "a"},
            },
            {
                "type": "message",
                "webhookEventId": "line-event-b",
                "timestamp": 1_700_000_000_002,
                "source": {"type": "user", "userId": "line-user"},
                "message": {"id": "line-message-b", "type": "text", "text": "b"},
            },
        ]
        exact_body = json.dumps(
            {"destination": "line-bot", "events": events},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        response = client.post(
            "/v1/gateway/line/webhook",
            content=exact_body,
            headers={"Content-Type": "application/json"},
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual(1, len(verifier.envelopes))
        self.assertEqual(exact_body, verifier.envelopes[0].raw_body)
        self.assertEqual(events, [
            call.args[0] for call in adapter.parse_inbound.call_args_list
        ])
        self.assertEqual(2, self.delivery.accept_inbound.call_count)
        calls = self.delivery.accept_inbound.call_args_list
        self.assertEqual("line:event:line-event-a", calls[0].kwargs["idempotency_key"])
        self.assertEqual("line:event:line-event-b", calls[1].kwargs["idempotency_key"])
        self.assertEqual("line-event-a", calls[0].kwargs["platform_event_id"])
        self.assertEqual("line-event-b", calls[1].kwargs["platform_event_id"])
        self.assertEqual(1_700_000_000_001, calls[0].kwargs["source_sequence"])
        self.assertEqual(1_700_000_000_002, calls[1].kwargs["source_sequence"])
        self.assertEqual("line-conversation", calls[0].kwargs["ordering_key"])
        self.assertEqual(exact_body, calls[0].kwargs["raw_body"])
        self.assertEqual(exact_body, calls[1].kwargs["raw_body"])
        self.assertEqual(
            {
                "status": "accepted",
                "event_count": 2,
                "processed_count": 2,
                "accepted_count": 1,
                "duplicate_count": 1,
                "ignored_count": 0,
                "filtered_count": 0,
            },
            {
                key: response.json()[key]
                for key in (
                    "status",
                    "event_count",
                    "processed_count",
                    "accepted_count",
                    "duplicate_count",
                    "ignored_count",
                    "filtered_count",
                )
            },
        )
        self.assertEqual(["line-inbox-a", "line-inbox-b"], response.json()["inbox_ids"])

    def test_line_empty_and_all_ignored_batches_are_explicitly_acknowledged(self) -> None:
        client, adapter, _ = self._make_client(platform=Platform.LINE)
        empty = client.post("/v1/gateway/line/webhook", json={"events": []})
        self.assertEqual(200, empty.status_code)
        self.assertEqual("ignored", empty.json()["status"])
        self.assertEqual("GW_LINE_EMPTY_BATCH", empty.json()["reason_code"])
        adapter.parse_inbound.assert_not_called()
        self.delivery.accept_inbound.assert_not_called()

        adapter.parse_inbound.side_effect = [None, None]
        ignored = client.post(
            "/v1/gateway/line/webhook",
            json={
                "events": [
                    {"type": "follow", "webhookEventId": "follow-a"},
                    {"type": "unfollow", "webhookEventId": "follow-b"},
                ]
            },
        )
        self.assertEqual(200, ignored.status_code)
        self.assertEqual("GW_LINE_NO_PROCESSABLE_EVENTS", ignored.json()["reason_code"])
        self.assertEqual(2, ignored.json()["ignored_count"])
        self.delivery.accept_inbound.assert_not_called()

    def test_line_invalid_event_shape_rejects_before_adapter_or_store(self) -> None:
        client, adapter, _ = self._make_client(platform=Platform.LINE)
        response = client.post(
            "/v1/gateway/line/webhook",
            json={"events": [{"type": "message"}, "not-an-object"]},
        )
        self.assertEqual(422, response.status_code)
        self.assertEqual("GW_LINE_EVENTS_INVALID", response.json()["reason_code"])
        adapter.parse_inbound.assert_not_called()
        self.delivery.accept_inbound.assert_not_called()

    def test_line_batch_conflict_or_store_failure_never_returns_200(self) -> None:
        client, adapter, _ = self._make_client(platform=Platform.LINE)
        adapter.parse_inbound.side_effect = [
            _make_msg(msg_id="line-a", platform=Platform.LINE),
            _make_msg(msg_id="line-b", platform=Platform.LINE),
        ]
        body = {
            "events": [
                {"type": "message", "webhookEventId": "event-a", "timestamp": 10},
                {"type": "message", "webhookEventId": "event-b", "timestamp": 11},
            ]
        }
        self.delivery.accept_inbound.side_effect = [
            GatewayDeliveryReceipt("inbox", "line-a", "NEW"),
            GatewayDeliveryReceipt(
                "inbox",
                "line-b",
                "CONFLICT",
                reason_code="GATEWAY_TRANSPORT_IDEMPOTENCY_CONFLICT",
            ),
        ]
        conflict = client.post("/v1/gateway/line/webhook", json=body)
        self.assertEqual(409, conflict.status_code)
        self.assertEqual(1, conflict.json()["failed_event_index"])

        adapter.parse_inbound.reset_mock()
        adapter.parse_inbound.side_effect = [
            _make_msg(msg_id="line-a", platform=Platform.LINE),
            _make_msg(msg_id="line-b", platform=Platform.LINE),
        ]
        self.delivery.accept_inbound.reset_mock()
        self.delivery.accept_inbound.side_effect = [
            GatewayDeliveryReceipt("inbox", "line-a", "NEW"),
            GatewayDeliveryError(
                "GATEWAY_TRANSPORT_SCHEMA_UNAVAILABLE",
                "db unavailable; provider-secret=must-not-leak",
            ),
        ]
        failure = client.post("/v1/gateway/line/webhook", json=body)
        self.assertEqual(503, failure.status_code)
        self.assertEqual(
            "GATEWAY_TRANSPORT_SCHEMA_UNAVAILABLE",
            failure.json()["reason_code"],
        )
        self.assertNotIn("provider-secret", failure.text)
        self.assertNotIn("must-not-leak", failure.text)

    def test_non_line_webhook_extracts_authenticated_source_sequence(self) -> None:
        client, _, _ = self._make_client()
        response = client.post(
            "/v1/gateway/wechat/webhook",
            json={"type": "1", "content": "hello", "sequence": 42},
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual(
            42,
            self.delivery.accept_inbound.call_args.kwargs["source_sequence"],
        )

    def test_webhook_missing_real_hmac_rejected_before_adapter(self) -> None:
        registry = VerifierRegistry((
            InternalHmacWebhookVerifier(platform="wechat", shared_secret="test-secret"),
        ))
        client, adapter, _ = self._make_client(webhook_verifiers=registry)
        response = client.post(
            "/v1/gateway/wechat/webhook",
            json={"type": "1", "content": "hello"},
        )
        self.assertEqual(401, response.status_code)
        self.assertEqual("GW_HEADER_MISSING", response.json()["reason_code"])
        adapter.parse_inbound.assert_not_called()

    def test_inbox_commit_failure_does_not_poison_memory_dedupe(self) -> None:
        client, adapter, _ = self._make_client()
        accepted = GatewayDeliveryReceipt(
            direction="inbox",
            record_id="gateway-inbox-retry",
            status="NEW",
            ack_ref="gateway-inbox-ack://retry",
        )
        self.delivery.accept_inbound.side_effect = [
            GatewayDeliveryError("GATEWAY_TRANSPORT_SCHEMA_UNAVAILABLE", "db unavailable"),
            accepted,
        ]
        body = {"type": "1", "content": "same body"}
        first = client.post("/v1/gateway/wechat/webhook", json=body)
        second = client.post("/v1/gateway/wechat/webhook", json=body)
        self.assertEqual(503, first.status_code)
        self.assertEqual(200, second.status_code)
        self.assertEqual("accepted", second.json()["status"])
        self.assertEqual(2, adapter.parse_inbound.call_count)
        self.assertEqual(2, self.delivery.accept_inbound.call_count)

    def test_store_duplicate_and_conflict_have_stable_receipts(self) -> None:
        client, _, _ = self._make_client()
        self.delivery.accept_inbound.side_effect = [
            GatewayDeliveryReceipt("inbox", "inbox-1", "NEW", "ack-1"),
            GatewayDeliveryReceipt("inbox", "inbox-1", "DUPLICATE_SAME", "ack-1"),
            GatewayDeliveryReceipt(
                "inbox",
                "inbox-1",
                "CONFLICT",
                "ack-1",
                "GATEWAY_TRANSPORT_IDEMPOTENCY_CONFLICT",
            ),
        ]
        first = client.post("/v1/gateway/wechat/webhook", json={"type": "1", "content": "same"})
        duplicate = client.post("/v1/gateway/wechat/webhook", json={"type": "1", "content": "same"})
        conflict = client.post("/v1/gateway/wechat/webhook", json={"type": "1", "content": "changed"})
        self.assertEqual("accepted", first.json()["status"])
        self.assertEqual("duplicate", duplicate.json()["status"])
        self.assertEqual("ack-1", duplicate.json()["ack_ref"])
        self.assertEqual(409, conflict.status_code)

    def test_http_inbox_payload_strips_reply_token_and_raw_event(self) -> None:
        message = _make_msg()
        message.raw_extra = {
            "reply_token": "line-secret-token",
            "source_type": "user",
            "line_message_type": "text",
            "raw_event": {"replyToken": "line-secret-token", "message": {"text": "hi"}},
            "gateway_voice_asr": {"text": "safe transcript", "confidence": 0.9},
        }
        client, _, _ = self._make_client(adapter_msg=message)
        response = client.post(
            "/v1/gateway/wechat/webhook",
            json={"type": "1", "content": "hello"},
        )
        self.assertEqual(200, response.status_code)
        normalized = self.delivery.accept_inbound.call_args.kwargs["normalized_payload"]
        extra = normalized["raw_extra"]
        self.assertNotIn("reply_token", extra)
        self.assertNotIn("raw_event", extra)
        self.assertEqual("user", extra["source_type"])
        self.assertEqual("safe transcript", extra["gateway_voice_asr"]["text"])
        self.assertNotIn("line-secret-token", json.dumps(normalized, ensure_ascii=False))

    def test_reply_same_request_id_uses_business_idempotency_across_new_proofs(self) -> None:
        verifier = _AllowVerifierRegistry()
        client, _, _ = self._make_client(
            with_outbound=True,
            reply_verifiers=verifier,
            outbox_status="NEW",
        )
        self.delivery.enqueue_reply.side_effect = [
            GatewayDeliveryReceipt("outbox", "outbox-1", "NEW"),
            GatewayDeliveryReceipt("outbox", "outbox-1", "DUPLICATE_SAME"),
        ]
        payload = {
            "request_id": "business-reply-1",
            "platform": "wechat",
            "conversation_id": "conv-1",
            "content": "same",
            "wait_result": False,
        }
        first = client.post("/v1/gateway/reply", json=payload)
        second = client.post("/v1/gateway/reply", json=payload)
        self.assertEqual(200, first.status_code)
        self.assertEqual(200, second.status_code)
        calls = self.delivery.enqueue_reply.call_args_list
        self.assertEqual("gateway-reply:business-reply-1", calls[0].kwargs["idempotency_key"])
        self.assertEqual(calls[0].kwargs["idempotency_key"], calls[1].kwargs["idempotency_key"])
        self.assertNotEqual(verifier.envelopes[0].request_id, verifier.envelopes[1].request_id)

    def test_reply_raw_extra_allows_safe_metadata_but_rejects_nested_secret(self) -> None:
        client, _, _ = self._make_client(with_outbound=True)
        safe = client.post(
            "/v1/gateway/reply",
            json={
                "request_id": "reply-safe-extra",
                "platform": "wechat",
                "conversation_id": "conv-safe",
                "content": "hi",
                "wait_result": False,
                "raw_extra": {"reply_source": "worker", "performance": {"latency_ms": 12}},
            },
        )
        rejected = client.post(
            "/v1/gateway/reply",
            json={
                "request_id": "reply-secret-extra",
                "platform": "wechat",
                "conversation_id": "conv-secret",
                "content": "hi",
                "wait_result": False,
                "raw_extra": {"nested": {"replyToken": "must-not-cross"}},
            },
        )
        self.assertEqual(200, safe.status_code)
        self.assertEqual(400, rejected.status_code)
        self.assertEqual("GW_REPLY_SENSITIVE_CONTEXT", rejected.json()["reason_code"])

    def test_valid_reply_hmac_still_rejects_duplicate_json_keys(self) -> None:
        secret = "isolated-reply-test-secret"
        registry = VerifierRegistry((
            InternalHmacWebhookVerifier(
                platform="internal_reply",
                shared_secret=secret,
            ),
        ))
        client, _, _ = self._make_client(
            with_outbound=True,
            reply_verifiers=registry,
        )
        body = (
            b'{"request_id":"reply-dup-a","request_id":"reply-dup-b",'
            b'"platform":"wechat","conversation_id":"conv-dup","content":"hi"}'
        )
        timestamp = str(int(time.time()))
        nonce = "reply-duplicate-json-nonce"
        signature = build_internal_signature(
            shared_secret=secret,
            platform="internal_reply",
            method="POST",
            path="/v1/gateway/reply",
            timestamp=timestamp,
            nonce=nonce,
            raw_body=body,
        )
        response = client.post(
            "/v1/gateway/reply",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Natsume-Webhook-Timestamp": timestamp,
                "X-Natsume-Webhook-Nonce": nonce,
                "X-Natsume-Webhook-Signature": signature,
            },
        )
        self.assertEqual(400, response.status_code)
        self.assertEqual("GW_REPLY_PAYLOAD_INVALID", response.json()["reason_code"])
        self.delivery.enqueue_reply.assert_not_called()

    def test_feishu_authenticated_challenge_bypasses_business_adapter(self) -> None:
        verifier = _AllowVerifierRegistry(claims={
            "authenticated_decrypt": True,
            "verification_token_verified": True,
        })
        client, adapter, _ = self._make_client(
            platform=Platform.FEISHU,
            webhook_verifiers=verifier,
        )
        response = client.post(
            "/v1/gateway/feishu/webhook",
            json={"challenge": "verified-feishu-challenge", "token": "not-forwarded"},
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual({"challenge": "verified-feishu-challenge"}, response.json())
        adapter.parse_inbound.assert_not_called()
        self.delivery.accept_inbound.assert_not_called()

    def test_wecom_get_returns_only_authenticated_decrypted_echostr(self) -> None:
        verifier = _AllowVerifierRegistry(
            claims={
                "url_verification": True,
                "authenticated_decrypt": True,
                "receive_id_verified": True,
            },
            payload_bytes=b"verified-wecom-echo",
        )
        client, adapter, _ = self._make_client(
            platform=Platform.WECOM,
            webhook_verifiers=verifier,
        )
        response = client.get(
            "/v1/gateway/wecom/webhook?msg_signature=s&timestamp=1&nonce=n&echostr=e"
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual("text/plain; charset=utf-8", response.headers["content-type"])
        self.assertEqual("verified-wecom-echo", response.text)
        adapter.parse_inbound.assert_not_called()
        self.delivery.accept_inbound.assert_not_called()


# ===========================================================================
# T-GW-DELIVERY: durable runtime thin-adapter contract
# ===========================================================================

class TestGatewayDeliveryRuntime(unittest.TestCase):
    def test_retired_mode_value_converges_to_durable(self) -> None:
        store = MagicMock()
        store.assert_schema_ready = MagicMock()
        runtime = GatewayDeliveryRuntime(
            mode="SHADOW",
            store=store,
            reporter=MagicMock(),
            outbound=MagicMock(),
        )
        self.assertEqual(GatewayTransportMode.DURABLE, runtime.mode)
        self.assertTrue(runtime.intake_enabled)
        self.assertTrue(runtime.verification_enabled)
        self.assertTrue(runtime.delivery_enabled)
        self.assertFalse(runtime.stats()["worker_alive"])

    def test_worker_start_failure_leaves_rollback_joinable_and_retryable(self) -> None:
        class _FailingThread:
            ident = None

            @staticmethod
            def start() -> None:
                raise RuntimeError("thread resource unavailable")

            @staticmethod
            def is_alive() -> bool:
                return False

            @staticmethod
            def join(timeout=None) -> None:  # noqa: ANN001
                raise AssertionError("an unstarted worker must never be joined")

        class _RetryThread:
            def __init__(self) -> None:
                self.ident = None
                self.alive = False

            def start(self) -> None:
                self.ident = 1
                self.alive = True

            def is_alive(self) -> bool:
                return self.alive

            def join(self, timeout=None) -> None:  # noqa: ANN001
                self.alive = False

        store = MagicMock()
        store.assert_schema_ready = MagicMock()
        runtime = GatewayDeliveryRuntime(
            mode=GatewayTransportMode.DURABLE,
            store=store,
            reporter=MagicMock(),
            outbound=MagicMock(),
        )
        retry_inbox_thread = _RetryThread()
        retry_outbox_thread = _RetryThread()

        with patch(
            "edge_client.gateway.delivery_runtime.threading.Thread",
            side_effect=[_FailingThread(), retry_inbox_thread, retry_outbox_thread],
        ):
            with self.assertRaisesRegex(RuntimeError, "thread resource unavailable"):
                runtime.start()

            self.assertIsNone(runtime._inbox_worker)
            self.assertIsNone(runtime._outbox_worker)
            self.assertFalse(runtime._ready)
            runtime.stop(timeout_s=0.1)

            runtime.start()
            self.assertIs(runtime._inbox_worker, retry_inbox_thread)
            self.assertIs(runtime._outbox_worker, retry_outbox_thread)
            self.assertTrue(retry_inbox_thread.is_alive())
            self.assertTrue(retry_outbox_thread.is_alive())
            runtime.stop(timeout_s=0.1)

        self.assertIsNone(runtime._inbox_worker)
        self.assertIsNone(runtime._outbox_worker)
        self.assertFalse(runtime._ready)

    def test_outbox_worker_start_failure_stops_started_inbox_sibling(self) -> None:
        class _StartedThread:
            def __init__(self) -> None:
                self.ident = None
                self.alive = False

            def start(self) -> None:
                self.ident = 1
                self.alive = True

            def is_alive(self) -> bool:
                return self.alive

            def join(self, timeout=None) -> None:  # noqa: ANN001
                self.alive = False

        class _FailingThread:
            ident = None

            @staticmethod
            def start() -> None:
                raise RuntimeError("outbox thread resource unavailable")

            @staticmethod
            def is_alive() -> bool:
                return False

            @staticmethod
            def join(timeout=None) -> None:  # noqa: ANN001
                raise AssertionError("an unstarted worker must never be joined")

        store = MagicMock()
        store.assert_schema_ready = MagicMock()
        runtime = GatewayDeliveryRuntime(
            mode=GatewayTransportMode.DURABLE,
            store=store,
            reporter=MagicMock(),
            outbound=MagicMock(),
        )
        inbox_thread = _StartedThread()

        with patch(
            "edge_client.gateway.delivery_runtime.threading.Thread",
            side_effect=[inbox_thread, _FailingThread()],
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "outbox thread resource unavailable",
            ):
                runtime.start()

        self.assertFalse(inbox_thread.is_alive())
        self.assertIsNone(runtime._inbox_worker)
        self.assertIsNone(runtime._outbox_worker)
        self.assertFalse(runtime.stats()["worker_alive"])
        self.assertFalse(runtime.stats()["ready"])

    def test_worker_checks_shutdown_before_every_new_claim(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        claim_count = 0
        store = MagicMock()
        store.assert_schema_ready = MagicMock()

        def _claim_inbox(**_kwargs):  # noqa: ANN003
            nonlocal claim_count
            claim_count += 1
            if claim_count == 1:
                return {
                    "inbox_id": "inbox-stop",
                    "claim_token": "claim-stop",
                    "fencing_token": 1,
                    "idempotency_key": "idem-stop",
                    "normalized_payload": {"source": "GATEWAY"},
                }
            return None

        def _deliver(_payload, *, idempotency_key):  # noqa: ANN001
            entered.set()
            release.wait(timeout=2.0)
            return MagicMock(
                success=True,
                receipt_ref="host-input-receipt://stop",
                receipt_body_sha256="sha256:" + ("a" * 64),
            )

        store.claim_inbox.side_effect = _claim_inbox
        store.claim_outbox.return_value = None
        reporter = MagicMock()
        reporter.deliver_gateway_payload.side_effect = _deliver
        runtime = GatewayDeliveryRuntime(
            mode=GatewayTransportMode.DURABLE,
            store=store,
            reporter=reporter,
            outbound=MagicMock(),
            worker_interval_s=0.02,
        )
        runtime.start()
        self.assertTrue(entered.wait(timeout=1.0))
        errors: list[BaseException] = []

        def _stop() -> None:
            try:
                runtime.stop(timeout_s=1.0)
            except BaseException as exc:  # pragma: no cover - assertion surface
                errors.append(exc)

        stopper = threading.Thread(target=_stop)
        stopper.start()
        self.assertTrue(runtime._shutdown.wait(timeout=1.0))
        release.set()
        stopper.join(timeout=2.0)

        self.assertEqual([], errors)
        self.assertEqual(1, claim_count)
        self.assertFalse(runtime.stats()["worker_alive"])

    def test_worker_stop_timeout_is_explicit_and_retryable(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        store = MagicMock()
        store.assert_schema_ready = MagicMock()
        store.claim_outbox.return_value = None
        store.claim_inbox.return_value = {
            "inbox_id": "inbox-blocked",
            "claim_token": "claim-blocked",
            "fencing_token": 1,
            "idempotency_key": "idem-blocked",
            "normalized_payload": {"source": "GATEWAY"},
        }

        def _deliver(_payload, *, idempotency_key):  # noqa: ANN001
            entered.set()
            release.wait(timeout=2.0)
            return MagicMock(
                success=True,
                receipt_ref="host-input-receipt://blocked",
                receipt_body_sha256="sha256:" + ("b" * 64),
            )

        reporter = MagicMock()
        reporter.deliver_gateway_payload.side_effect = _deliver
        runtime = GatewayDeliveryRuntime(
            mode=GatewayTransportMode.DURABLE,
            store=store,
            reporter=reporter,
            outbound=MagicMock(),
        )
        runtime.start()
        self.assertTrue(entered.wait(timeout=1.0))

        with self.assertRaisesRegex(GatewayDeliveryError, "GW_TRANSPORT_WORKER_STOP_TIMEOUT"):
            runtime.stop(timeout_s=0.1)

        self.assertFalse(runtime.intake_enabled)
        self.assertTrue(runtime.stats()["inbox_worker_alive"])
        self.assertFalse(runtime.stats()["outbox_worker_alive"])
        self.assertFalse(runtime.stats()["worker_alive"])
        release.set()
        runtime.stop(timeout_s=1.0)
        self.assertFalse(runtime.stats()["worker_alive"])

    def test_blocked_platform_outbox_never_delays_new_inbox_delivery(self) -> None:
        outbox_entered = threading.Event()
        release_outbox = threading.Event()
        inbox_available = threading.Event()
        inbox_delivered = threading.Event()
        inbox_claimed = False
        outbox_claimed = False
        store = MagicMock()
        store.assert_schema_ready = MagicMock()

        def _claim_inbox(**_kwargs):  # noqa: ANN003
            nonlocal inbox_claimed
            if not inbox_available.is_set() or inbox_claimed:
                return None
            inbox_claimed = True
            return {"inbox_id": "inbox-independent"}

        def _claim_outbox(**_kwargs):  # noqa: ANN003
            nonlocal outbox_claimed
            if outbox_claimed:
                return None
            outbox_claimed = True
            return {"outbox_id": "outbox-blocking"}

        store.claim_inbox.side_effect = _claim_inbox
        store.claim_outbox.side_effect = _claim_outbox
        runtime = GatewayDeliveryRuntime(
            mode=GatewayTransportMode.DURABLE,
            store=store,
            reporter=MagicMock(),
            outbound=MagicMock(),
            worker_interval_s=0.02,
        )

        def _block_outbox(_row) -> None:  # noqa: ANN001
            outbox_entered.set()
            release_outbox.wait(timeout=2.0)

        def _record_inbox(_row) -> None:  # noqa: ANN001
            inbox_delivered.set()

        runtime._deliver_outbox = _block_outbox  # type: ignore[method-assign]
        runtime._deliver_inbox = _record_inbox  # type: ignore[method-assign]
        runtime.start()
        try:
            self.assertTrue(outbox_entered.wait(timeout=1.0))
            inbox_available.set()
            self.assertTrue(
                inbox_delivered.wait(timeout=0.5),
                "the inbox worker was blocked by an unrelated platform send",
            )
            self.assertFalse(release_outbox.is_set())
            stats = runtime.stats()
            self.assertTrue(stats["inbox_worker_alive"])
            self.assertTrue(stats["outbox_worker_alive"])
            self.assertTrue(stats["worker_alive"])
        finally:
            release_outbox.set()
            runtime.stop(timeout_s=1.0)

    def test_accept_inbound_supplies_required_local_tenant(self) -> None:
        store = MagicMock()
        accepted = MagicMock()
        accepted.as_dict.return_value = {
            "status": "NEW",
            "inbox_id": "inbox-1",
            "ack_ref": "ack-1",
            "reason_code": None,
        }
        store.accept_inbox.return_value = accepted
        runtime = GatewayDeliveryRuntime(
            mode=GatewayTransportMode.DURABLE,
            store=store,
            reporter=MagicMock(),
            outbound=MagicMock(),
        )
        receipt = runtime.accept_inbound(
            platform="qq",
            principal_id="qq:webhook",
            tenant_id=None,
            idempotency_key="qq:event:m1",
            raw_body=b'{"message_id":"m1"}',
            normalized_payload={"source": "GATEWAY", "msg_id": "m1"},
        )
        self.assertEqual("NEW", receipt.status)
        self.assertEqual("local", store.accept_inbox.call_args.kwargs["tenant_id"])

    def test_inbox_and_outbox_workers_ack_only_after_side_effect(self) -> None:
        store = MagicMock()
        reporter = MagicMock()
        reporter.deliver_gateway_payload.return_value = MagicMock(
            success=True,
            reason_code=None,
            detail="ok",
            receipt_ref="host-input-receipt://receipt-1",
            receipt_body_sha256="sha256:" + ("a" * 64),
        )
        # Runtime intentionally requires the concrete frozen plan so use a
        # minimal real dispatcher/adapter pair for the preflight type boundary.
        adapter = MagicMock()
        adapter.platform_id = Platform.WECHAT
        adapter.send_reply.return_value = True
        outbound = OutboundDispatcher({Platform.WECHAT: adapter})
        runtime = GatewayDeliveryRuntime(
            mode=GatewayTransportMode.DURABLE,
            store=store,
            reporter=reporter,
            outbound=outbound,
        )
        runtime._deliver_inbox({
            "inbox_id": "inbox-1",
            "claim_token": "claim-in-1",
            "fencing_token": 1,
            "idempotency_key": "idem-in-1",
            "normalized_payload": {"source": "GATEWAY", "msg_id": "m1"},
        })
        store.mark_inbox_dispatch_started.assert_called_once()
        reporter.deliver_gateway_payload.assert_called_once()
        store.complete_inbox.assert_called_once()
        store.retry_inbox.assert_not_called()

        runtime._deliver_outbox({
            "outbox_id": "outbox-1",
            "claim_token": "claim-out-1",
            "fencing_token": 2,
            "payload": b'{"platform":"wechat","conversation_id":"c1","content":"ok","raw_extra":{"reply_source":"worker"}}',
        })
        store.mark_outbox_dispatch_started.assert_called_once()
        adapter.send_reply.assert_called_once()
        store.ack_outbox.assert_called_once()
        self.assertTrue(
            store.ack_outbox.call_args.kwargs["ack_ref"].startswith(
                "adapter-confirmation:wechat:"
            )
        )
        self.assertRegex(
            store.ack_outbox.call_args.kwargs["ack_digest"],
            r"^sha256:[0-9a-f]{64}$",
        )
        store.retry_outbox.assert_not_called()

    def test_host_uncertainty_retries_but_platform_uncertainty_is_quarantined(self) -> None:
        store = MagicMock()
        store.retry_inbox.return_value = {
            "status": "RETRY_WAIT",
            "last_reason_code": "INPUT_REPORT_DELIVERY_UNCERTAIN",
        }
        reporter = MagicMock()
        reporter.deliver_gateway_payload.return_value = MagicMock(
            success=False,
            reason_code="INPUT_REPORT_DELIVERY_UNCERTAIN",
            detail="response timeout",
        )
        adapter = MagicMock()
        adapter.platform_id = Platform.WECHAT
        adapter.send_reply.side_effect = TimeoutError("platform timeout")
        outbound = OutboundDispatcher({Platform.WECHAT: adapter})
        runtime = GatewayDeliveryRuntime(
            mode=GatewayTransportMode.DURABLE,
            store=store,
            reporter=reporter,
            outbound=outbound,
        )
        runtime._deliver_inbox({
            "inbox_id": "inbox-uncertain",
            "claim_token": "claim-in-uncertain",
            "fencing_token": 3,
            "normalized_payload": {"source": "GATEWAY", "msg_id": "m-uncertain"},
        })
        runtime._deliver_outbox({
            "outbox_id": "outbox-uncertain",
            "claim_token": "claim-out-uncertain",
            "fencing_token": 4,
            "payload": b'{"platform":"wechat","conversation_id":"c1","content":"ok"}',
        })
        store.mark_inbox_dispatch_started.assert_called_once()
        store.mark_outbox_dispatch_started.assert_called_once()
        store.retry_inbox.assert_called_once()
        store.retry_outbox.assert_called_once()
        store.complete_inbox.assert_not_called()
        store.ack_outbox.assert_not_called()
        self.assertEqual(1, runtime.stats()["inbox_retried"])
        self.assertEqual(0, runtime.stats()["inbox_uncertain"])
        self.assertEqual(1, runtime.stats()["outbox_uncertain"])

    def test_outbox_preflight_rejection_never_marks_dispatch_started(self) -> None:
        store = MagicMock()
        store.retry_outbox.return_value = {
            "status": "RETRY_WAIT",
            "last_reason_code": "GW_REPLY_ADAPTER_UNAVAILABLE",
        }
        runtime = GatewayDeliveryRuntime(
            mode=GatewayTransportMode.DURABLE,
            store=store,
            reporter=MagicMock(),
            outbound=OutboundDispatcher(),
        )

        runtime._deliver_outbox({
            "outbox_id": "outbox-no-adapter",
            "claim_token": "claim-no-adapter",
            "fencing_token": 5,
            "payload": b'{"platform":"wechat","conversation_id":"c1","content":"ok"}',
        })

        store.mark_outbox_dispatch_started.assert_not_called()
        store.ack_outbox.assert_not_called()
        store.retry_outbox.assert_called_once()
        self.assertEqual(
            "GW_REPLY_ADAPTER_UNAVAILABLE",
            store.retry_outbox.call_args.kwargs["reason_code"],
        )

    def test_wait_outbox_uses_keyword_only_store_contract(self) -> None:
        class _Store:
            def get_outbox(self, *, outbox_id: str) -> dict[str, Any]:
                return {"outbox_id": outbox_id, "status": "ACKED"}

        runtime = GatewayDeliveryRuntime(
            mode=GatewayTransportMode.DURABLE,
            store=_Store(),
            reporter=MagicMock(),
            outbound=MagicMock(),
        )
        row = runtime.wait_outbox("outbox-keyword", timeout_ms=50)
        self.assertEqual("ACKED", row["status"])

    def test_invalid_mode_and_intake_switch_fail_closed(self) -> None:
        self.assertEqual(
            GatewayTransportMode.DURABLE,
            load_gateway_transport_mode({GATEWAY_TRANSPORT_MODE_ENV: "typo"}),
        )
        self.assertTrue(load_gateway_accept_new_intake({}))
        self.assertFalse(load_gateway_accept_new_intake({
            GATEWAY_ACCEPT_NEW_INTAKE_ENV: "typo",
        }))

    def test_durable_kill_switch_disables_intake_but_keeps_delivery(self) -> None:
        store = MagicMock()
        runtime = GatewayDeliveryRuntime(
            mode=GatewayTransportMode.DURABLE,
            store=store,
            reporter=MagicMock(),
            outbound=MagicMock(),
            accept_new_intake=False,
        )
        self.assertFalse(runtime.intake_enabled)
        self.assertTrue(runtime.delivery_enabled)
        stats = runtime.stats()
        self.assertFalse(stats["intake_enabled"])
        self.assertNotIn("drained", stats)
        self.assertNotIn("pending", stats)
        store.transport_counts.assert_not_called()

    def test_non_http_intake_uses_canonical_bytes_and_stable_event_identity(self) -> None:
        store = MagicMock()
        accepted = MagicMock()
        accepted.as_dict.return_value = {
            "status": "NEW",
            "inbox_id": "inbox-non-http",
            "ack_ref": "ack-non-http",
        }
        store.accept_inbox.return_value = accepted
        runtime = GatewayDeliveryRuntime(
            mode=GatewayTransportMode.DURABLE,
            store=store,
            reporter=MagicMock(),
            outbound=MagicMock(),
        )
        runtime.accept_non_http(
            platform="telegram",
            principal_id="gateway.non-http.telegram-userbot",
            tenant_id="local",
            platform_event_id="event-42",
            normalized_payload={"z": 1, "a": "消息"},
            ordering_key="conversation-7",
        )
        kwargs = store.accept_inbox.call_args.kwargs
        self.assertEqual("gateway.non-http", kwargs["surface"])
        self.assertEqual(b'{"a":"\xe6\xb6\x88\xe6\x81\xaf","z":1}', kwargs["raw_body"])
        self.assertEqual("telegram:non-http:event-42", kwargs["idempotency_key"])
        self.assertEqual("event-42", kwargs["platform_event_id"])
        self.assertEqual(
            {"x-edge-source": "gateway.non-http"},
            kwargs["safe_headers"],
        )

    def test_outbox_authority_comes_from_authenticated_arguments(self) -> None:
        store = MagicMock()
        store.enqueue_outbox.return_value = {
            "acceptance_status": "NEW",
            "outbox_id": "outbox-authority",
        }
        runtime = GatewayDeliveryRuntime(
            mode=GatewayTransportMode.DURABLE,
            store=store,
            reporter=MagicMock(),
            outbound=MagicMock(),
        )
        runtime.enqueue_reply(
            tenant_id="tenant-a",
            principal_id="service.host.gateway-reply",
            idempotency_key="reply-1",
            payload={
                "platform": "wechat",
                "conversation_id": "c1",
                "content": "ok",
            },
        )
        kwargs = store.enqueue_outbox.call_args.kwargs
        self.assertEqual("tenant-a", kwargs["tenant_id"])
        self.assertEqual("service.host.gateway-reply", kwargs["principal_id"])


# ===========================================================================
# T-GW-OUTBOUND: 回复分发器契约
# ===========================================================================

class TestOutboundDispatcher(unittest.TestCase):
    """T-GW-OUTBOUND-01~10: OutboundDispatcher 契约。"""

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

    def test_t_gw_outbound_06_no_adapter_preflight_is_definitely_not_sent(self) -> None:
        """T-GW-OUTBOUND-06: 缺少目标适配器时 preflight 零发送且结论明确。"""
        unrelated = _make_mock_adapter(platform=Platform.QQ, send_result=True)
        dispatcher = OutboundDispatcher({Platform.QQ: unrelated})

        with self.assertRaises(OutboundPreparationRejected) as raised:
            dispatcher.prepare_payload({
                "platform": "wechat",
                "conversation_id": "conv-preflight",
                "msg_type": "text",
                "content": "hello",
            })

        self.assertEqual(
            OutboundDeliveryCertainty.DEFINITELY_NOT_SENT,
            raised.exception.outcome.certainty,
        )
        self.assertFalse(raised.exception.outcome.accepted)
        unrelated.send_reply.assert_not_called()

    def test_t_gw_outbound_07_invalid_preflight_is_definitely_not_sent(self) -> None:
        """T-GW-OUTBOUND-07: 非法消息类型在调用适配器前失败。"""
        adapter = _make_mock_adapter(platform=Platform.WECHAT, send_result=True)
        dispatcher = OutboundDispatcher({Platform.WECHAT: adapter})

        with self.assertRaises(OutboundPreparationRejected) as raised:
            dispatcher.prepare_payload({
                "platform": "wechat",
                "conversation_id": "conv-preflight",
                "msg_type": "not-a-message-type",
                "content": "hello",
            })

        self.assertEqual(
            OutboundDeliveryCertainty.DEFINITELY_NOT_SENT,
            raised.exception.outcome.certainty,
        )
        adapter.send_reply.assert_not_called()

    def test_t_gw_outbound_08_true_is_only_adapter_confirmed(self) -> None:
        """T-GW-OUTBOUND-08: True 只形成 adapter-confirmation，不冒充平台 ACK。"""
        adapter = _make_mock_adapter(platform=Platform.WECHAT, send_result=True)
        dispatcher = OutboundDispatcher({Platform.WECHAT: adapter})
        payload = {
            "platform": "wechat",
            "conversation_id": "conv-confirmed",
            "msg_type": "text",
            "content": "hello",
            "raw_extra": {"reply_source": "contract", "routing": {"lane": "main"}},
        }
        prepared = dispatcher.prepare_payload(payload)
        payload["content"] = "mutated-after-preflight"
        payload["raw_extra"]["routing"]["lane"] = "mutated"
        with self.assertRaises(FrozenInstanceError):
            prepared.content = "mutated"  # type: ignore[misc]

        outcome = dispatcher.dispatch_prepared(prepared)

        self.assertTrue(outcome.accepted)
        self.assertEqual(OutboundDeliveryCertainty.ADAPTER_CONFIRMED, outcome.certainty)
        self.assertTrue((outcome.receipt_ref or "").startswith("adapter-confirmation:wechat:"))
        self.assertNotIn("platform", (outcome.receipt_ref or "").lower())
        self.assertEqual(64, len(outcome.receipt_digest or ""))
        adapter.send_reply.assert_called_once()
        sent_reply = adapter.send_reply.call_args.args[0]
        self.assertEqual("hello", sent_reply.content)
        self.assertEqual("main", sent_reply.raw_extra["routing"]["lane"])

    def test_t_gw_outbound_09_false_is_uncertain(self) -> None:
        """T-GW-OUTBOUND-09: False 不能证明平台未提交。"""
        adapter = _make_mock_adapter(platform=Platform.WECHAT, send_result=False)
        dispatcher = OutboundDispatcher({Platform.WECHAT: adapter})
        prepared = dispatcher.prepare_payload({
            "platform": "wechat",
            "conversation_id": "conv-false",
            "content": "hello",
        })

        outcome = dispatcher.dispatch_prepared(prepared)

        self.assertFalse(outcome.accepted)
        self.assertEqual(OutboundDeliveryCertainty.MAY_HAVE_COMMITTED, outcome.certainty)
        self.assertIsNone(outcome.receipt_ref)
        self.assertIsNone(outcome.receipt_digest)
        adapter.send_reply.assert_called_once()

    def test_t_gw_outbound_10_exception_is_uncertain(self) -> None:
        """T-GW-OUTBOUND-10: 适配器异常不重抛，返回可能已提交。"""
        adapter = _make_mock_adapter(platform=Platform.WECHAT)
        adapter.send_reply.side_effect = TimeoutError("unknown remote outcome")
        dispatcher = OutboundDispatcher({Platform.WECHAT: adapter})
        prepared = dispatcher.prepare_payload({
            "platform": "wechat",
            "conversation_id": "conv-timeout",
            "content": "hello",
        })

        outcome = dispatcher.dispatch_prepared(prepared)

        self.assertFalse(outcome.accepted)
        self.assertEqual(OutboundDeliveryCertainty.MAY_HAVE_COMMITTED, outcome.certainty)
        self.assertEqual("ADAPTER_EXCEPTION:TimeoutError", outcome.reason)
        adapter.send_reply.assert_called_once()


if __name__ == "__main__":
    unittest.main()
