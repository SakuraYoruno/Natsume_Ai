"""EdgeRpcClient 单元测试。

覆盖：
- HMAC 签名构建
- post_vts / post_speak / post_screenshot / post_automate 的正常路径
- httpx 不可用时的降级路径
- Edge 返回非 200 时的降级路径
- vts.py 中 resolve_vts_expression 成功时触发 fire-and-forget
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# 辅助：构建最小 SQLiteLedger mock
# ---------------------------------------------------------------------------

def _make_ledger() -> MagicMock:
    ledger = MagicMock()
    ledger.log_avatar_action = MagicMock()
    ledger.log_voice_event = MagicMock()
    return ledger


# ---------------------------------------------------------------------------
# 测试 HMAC 签名
# ---------------------------------------------------------------------------

class TestBuildSignature(unittest.TestCase):
    def test_signature_deterministic(self):
        from host_brain.channels.edge_rpc_client import _build_signature
        sig1 = _build_signature(
            request_id="req-001",
            ts_ms=1700000000000,
            nonce="nonce-abc",
            payload={"action": "set_expression", "emotion_tag": "HAPPY"},
            shared_key="test-secret",
        )
        sig2 = _build_signature(
            request_id="req-001",
            ts_ms=1700000000000,
            nonce="nonce-abc",
            payload={"action": "set_expression", "emotion_tag": "HAPPY"},
            shared_key="test-secret",
        )
        self.assertEqual(sig1, sig2)

    def test_signature_changes_with_payload(self):
        from host_brain.channels.edge_rpc_client import _build_signature
        sig1 = _build_signature(
            request_id="req-001",
            ts_ms=1700000000000,
            nonce="nonce-abc",
            payload={"action": "set_expression", "emotion_tag": "HAPPY"},
            shared_key="test-secret",
        )
        sig2 = _build_signature(
            request_id="req-001",
            ts_ms=1700000000000,
            nonce="nonce-abc",
            payload={"action": "set_expression", "emotion_tag": "SAD"},
            shared_key="test-secret",
        )
        self.assertNotEqual(sig1, sig2)

    def test_signature_matches_manual_calculation(self):
        from host_brain.channels.edge_rpc_client import _build_signature
        payload = {"action": "reset"}
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        message = f"req-002\n1700000001000\nnonce-xyz\n{canonical}"
        expected = hmac.new(
            b"my-key",
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        actual = _build_signature(
            request_id="req-002",
            ts_ms=1700000001000,
            nonce="nonce-xyz",
            payload=payload,
            shared_key="my-key",
        )
        self.assertEqual(expected, actual)


# ---------------------------------------------------------------------------
# 测试 _build_envelope
# ---------------------------------------------------------------------------

class TestBuildEnvelope(unittest.TestCase):
    def test_insecure_envelope_has_insecure_signature(self):
        from host_brain.channels.edge_rpc_client import _build_envelope
        env = _build_envelope(
            request_id="req-003",
            payload={"action": "talk_start"},
            shared_key=None,
            allow_insecure=True,
        )
        self.assertEqual(env["signature"], "insecure")
        self.assertEqual(env["request_id"], "req-003")
        self.assertIn("nonce", env)
        self.assertIn("ts_ms", env)

    def test_secure_envelope_has_real_signature(self):
        from host_brain.channels.edge_rpc_client import _build_envelope
        env = _build_envelope(
            request_id="req-004",
            payload={"action": "talk_end"},
            shared_key="secret-key",
            allow_insecure=False,
        )
        self.assertNotEqual(env["signature"], "insecure")
        self.assertEqual(len(env["signature"]), 64)  # SHA256 hex = 64 chars


# ---------------------------------------------------------------------------
# 测试 post_vts（mock httpx）
# ---------------------------------------------------------------------------

class TestPostVts(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def test_post_vts_success(self):
        from host_brain.channels.edge_rpc_client import post_vts

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "status": "ACCEPTED",
            "reason_code": None,
            "detail": "vts ok",
            "event": {"expression_id": "exp_happy_01"},
            "ts_ms": 1700000000000,
        }
        mock_response.headers = {"content-type": "application/json"}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch.dict(os.environ, {"EDGE_ALLOW_INSECURE_AUTH": "true"}):
            with patch("httpx.AsyncClient", return_value=mock_client):
                result = self._run(post_vts(
                    action="set_expression",
                    emotion_tag="HAPPY",
                    request_id="req-vts-001",
                ))

        self.assertTrue(result.success)
        self.assertEqual(result.status, "ACCEPTED")
        self.assertGreaterEqual(result.latency_ms, 0)

    def test_post_vts_degraded_on_connection_error(self):
        from host_brain.channels.edge_rpc_client import post_vts

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=ConnectionRefusedError("refused"))

        with patch.dict(os.environ, {"EDGE_ALLOW_INSECURE_AUTH": "true", "HOST_EDGE_RPC_RETRIES": "0"}):
            with patch("httpx.AsyncClient", return_value=mock_client):
                result = self._run(post_vts(
                    action="set_expression",
                    emotion_tag="HAPPY",
                    request_id="req-vts-002",
                ))

        self.assertFalse(result.success)
        self.assertEqual(result.status, "DEGRADED")
        self.assertEqual(result.reason_code, "EDGE_RPC_CALL_FAILED")

    def test_post_vts_degraded_when_httpx_not_installed(self):
        from host_brain.channels.edge_rpc_client import post_vts

        with patch.dict(sys.modules, {"httpx": None}):
            result = self._run(post_vts(
                action="set_expression",
                emotion_tag="NEUTRAL",
                request_id="req-vts-003",
            ))

        self.assertFalse(result.success)
        self.assertEqual(result.reason_code, "HTTPX_NOT_INSTALLED")


# ---------------------------------------------------------------------------
# 测试 post_speak
# ---------------------------------------------------------------------------

class TestPostSpeak(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def test_post_speak_success(self):
        from host_brain.channels.edge_rpc_client import post_speak

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "status": "ACCEPTED",
            "reason_code": None,
            "detail": "speak queued",
            "event": {},
            "ts_ms": 1700000000000,
        }
        mock_response.headers = {"content-type": "application/json"}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch.dict(os.environ, {"EDGE_ALLOW_INSECURE_AUTH": "true"}):
            with patch("httpx.AsyncClient", return_value=mock_client):
                result = self._run(post_speak(
                    text="こんにちは！",
                    request_id="req-speak-001",
                    emotion_tag="HAPPY",
                ))

        self.assertTrue(result.success)
        self.assertEqual(result.status, "ACCEPTED")

    def test_post_speak_payload_contains_text(self):
        """验证 post_speak 发送的 payload 包含正确的 text 字段。"""
        from host_brain.channels.edge_rpc_client import post_speak

        captured_payload: dict = {}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True, "status": "ACCEPTED",
            "reason_code": None, "detail": "ok", "event": {}, "ts_ms": 0,
        }
        mock_response.headers = {"content-type": "application/json"}

        async def _capture_post(url, json=None, **kwargs):
            nonlocal captured_payload
            captured_payload = json or {}
            return mock_response

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = _capture_post

        with patch.dict(os.environ, {"EDGE_ALLOW_INSECURE_AUTH": "true"}):
            with patch("httpx.AsyncClient", return_value=mock_client):
                self._run(post_speak(text="テスト", request_id="req-speak-002"))

        self.assertIn("payload", captured_payload)
        self.assertEqual(captured_payload["payload"]["text"], "テスト")


# ---------------------------------------------------------------------------
# 测试 post_wechat_command
# ---------------------------------------------------------------------------

class TestPostWeChatCommand(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def test_post_wechat_command_success_and_payload(self):
        from host_brain.channels.edge_rpc_client import post_wechat_command

        captured_payload: dict = {}
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "status": "ACCEPTED",
            "reason_code": None,
            "detail": "wechat command queued",
            "event": {"command_id": "wechatcmd-1"},
            "ts_ms": 1700000000000,
        }
        mock_response.headers = {"content-type": "application/json"}

        async def _capture_post(url, json=None, **kwargs):
            nonlocal captured_payload
            captured_payload = dict(json or {})
            return mock_response

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = _capture_post

        with patch.dict(
            os.environ,
            {
                "EDGE_ALLOW_INSECURE_AUTH": "true",
                "HOST_EDGE_RPC_BLOCK_LOOPBACK_WECHAT": "false",
            },
        ):
            with patch("httpx.AsyncClient", return_value=mock_client):
                result = self._run(
                    post_wechat_command(
                        action="send_text",
                        target="alice",
                        content="hello",
                        request_id="req-wechat-001",
                        wait_result=True,
                        timeout_ms=9000,
                    )
                )

        self.assertTrue(result.success)
        self.assertEqual("send_text", captured_payload["payload"]["action"])
        self.assertEqual("alice", captured_payload["payload"]["target"])
        self.assertEqual("hello", captured_payload["payload"]["content"])
        self.assertEqual(9000, captured_payload["payload"]["timeout_ms"])
        self.assertTrue(captured_payload["payload"]["wait_result"])

    def test_post_wechat_command_accepts_target_candidates(self):
        from host_brain.channels.edge_rpc_client import post_wechat_command

        captured_payload: dict = {}
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "status": "ACCEPTED",
            "reason_code": None,
            "detail": "wechat command queued",
            "event": {"command_id": "wechatcmd-1"},
            "ts_ms": 1700000000000,
        }
        mock_response.headers = {"content-type": "application/json"}

        async def _capture_post(url, json=None, **kwargs):
            nonlocal captured_payload
            captured_payload = dict(json or {})
            return mock_response

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = _capture_post

        with patch.dict(
            os.environ,
            {
                "EDGE_ALLOW_INSECURE_AUTH": "true",
                "HOST_EDGE_RPC_BLOCK_LOOPBACK_WECHAT": "false",
            },
        ):
            with patch("httpx.AsyncClient", return_value=mock_client):
                result = self._run(
                    post_wechat_command(
                        action="send_text",
                        target="wxid_alice",
                        target_candidates=["wxid_alice", "Alice", "Alice"],
                        content="hello",
                        request_id="req-wechat-002",
                        wait_result=True,
                        timeout_ms=9000,
                    )
                )

        self.assertTrue(result.success)
        self.assertEqual(
            ["wxid_alice", "Alice"],
            captured_payload["payload"].get("target_candidates"),
        )

    def test_post_wechat_command_blocks_loopback_endpoint_by_default(self):
        from host_brain.channels.edge_rpc_client import post_wechat_command

        async_client_mock = AsyncMock()
        with patch.dict(
            os.environ,
            {
                "EDGE_ALLOW_INSECURE_AUTH": "true",
                "HOST_EDGE_RPC_BASE_URL": "http://127.0.0.1:17800",
                "HOST_EDGE_RPC_BLOCK_LOOPBACK_WECHAT": "true",
            },
            clear=False,
        ), patch("httpx.AsyncClient", return_value=async_client_mock):
            result = self._run(
                post_wechat_command(
                    action="send_text",
                    target="alice",
                    content="hello",
                    request_id="req-wechat-block-local-001",
                    wait_result=False,
                    timeout_ms=3000,
                )
            )

        self.assertFalse(result.success)
        self.assertEqual("REJECTED", result.status)
        self.assertEqual("EDGE_RPC_LOCAL_BLOCKED", result.reason_code)
        async_client_mock.assert_not_called()


# ---------------------------------------------------------------------------
# 测试 post_screenshot
# ---------------------------------------------------------------------------

class TestPostScreenshot(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def test_post_screenshot_success(self):
        from host_brain.channels.edge_rpc_client import post_screenshot

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "status": "ACCEPTED",
            "reason_code": None,
            "detail": "screenshot captured",
            "event": {"image_b64": "abc123", "width": 1920, "height": 1080},
            "ts_ms": 1700000000000,
        }
        mock_response.headers = {"content-type": "application/json"}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch.dict(os.environ, {"EDGE_ALLOW_INSECURE_AUTH": "true"}):
            with patch("httpx.AsyncClient", return_value=mock_client):
                result = self._run(post_screenshot(monitor_index=1))

        self.assertTrue(result.success)
        self.assertEqual(result.event.get("image_b64"), "abc123")


# ---------------------------------------------------------------------------
# 测试 vts.py 中 resolve_vts_expression 触发 fire-and-forget
# ---------------------------------------------------------------------------

class TestResolveVtsExpressionFireAndForget(unittest.TestCase):
    def test_applied_result_triggers_edge_vts(self):
        """resolve_vts_expression 成功时，应 fire-and-forget 调用 post_vts。"""
        from host_brain.channels.vts import resolve_vts_expression

        ledger = _make_ledger()
        fired_calls: list[dict] = []

        async def _mock_post_vts(*, action, emotion_tag=None, request_id=None):
            fired_calls.append({"action": action, "emotion_tag": emotion_tag})
            from host_brain.channels.edge_rpc_client import EdgeRpcCallResult
            return EdgeRpcCallResult(
                success=True, status="ACCEPTED", reason_code=None,
                detail="ok", event={}, latency_ms=1,
            )

        async def _run():
            with patch("host_brain.channels.edge_rpc_client.post_vts", _mock_post_vts):
                result = resolve_vts_expression(
                    emotion_tag="HAPPY",
                    ledger=ledger,
                    request_id="req-vts-fire-001",
                    edge_available=True,
                )
                # 等待 fire-and-forget 任务完成
                await asyncio.sleep(0.05)
            return result

        result = asyncio.run(_run())
        self.assertEqual(result.status, "APPLIED")
        self.assertEqual(result.expression_id, "exp_happy_01")
        # fire-and-forget 应已触发
        self.assertEqual(len(fired_calls), 1)
        self.assertEqual(fired_calls[0]["action"], "set_expression")
        self.assertEqual(fired_calls[0]["emotion_tag"], "HAPPY")

    def test_degraded_result_does_not_trigger_edge_vts(self):
        """edge_available=False 时，结果为 DEGRADED，不触发 Edge VTS HTTP 调用。"""
        from host_brain.channels.vts import resolve_vts_expression

        ledger = _make_ledger()
        fired_calls: list[dict] = []

        async def _mock_post_vts(*, action, emotion_tag=None, request_id=None):
            fired_calls.append({"action": action})
            from host_brain.channels.edge_rpc_client import EdgeRpcCallResult
            return EdgeRpcCallResult(
                success=True, status="ACCEPTED", reason_code=None,
                detail="ok", event={}, latency_ms=1,
            )

        async def _run():
            with patch("host_brain.channels.edge_rpc_client.post_vts", _mock_post_vts):
                result = resolve_vts_expression(
                    emotion_tag="SAD",
                    ledger=ledger,
                    request_id="req-vts-fire-002",
                    edge_available=False,
                )
                await asyncio.sleep(0.05)
            return result

        result = asyncio.run(_run())
        self.assertEqual(result.status, "DEGRADED")
        # edge_available=False 时不触发 HTTP 调用
        self.assertEqual(len(fired_calls), 0)


# ---------------------------------------------------------------------------
# 测试 probe_edge_rpc_health
# ---------------------------------------------------------------------------

class TestProbeEdgeRpcHealth(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def test_probe_returns_available_on_200(self):
        from host_brain.app.dependencies import probe_edge_rpc_health

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "OK",
            "event": {
                "components": {
                    "asr": {
                        "enabled": True,
                        "available": True,
                        "status": "ok",
                        "detail": "component loaded",
                    }
                }
            },
        }
        mock_response.headers = {"content-type": "application/json"}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = self._run(probe_edge_rpc_health())

        self.assertTrue(result["available"])
        self.assertEqual(result["status"], "OK")
        self.assertEqual("component loaded", result["event"]["components"]["asr"]["detail"])

    def test_probe_returns_degraded_on_connection_error(self):
        from host_brain.app.dependencies import probe_edge_rpc_health

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=ConnectionRefusedError("refused"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = self._run(probe_edge_rpc_health())

        self.assertFalse(result["available"])
        self.assertEqual(result["status"], "DEGRADED")

    def test_build_edge_dependency_probe_statuses_uses_component_health_for_asr(self):
        from host_brain.app.dependencies import build_edge_dependency_probe_statuses

        statuses = build_edge_dependency_probe_statuses(
            {
                "available": True,
                "detail": "edge rpc reachable",
                "latency_ms": 18,
                "event": {
                    "services": {
                        "edge.rpc.asr": {
                            "available": True,
                            "detail": "edge asr enabled",
                        },
                        "edge.rpc.tts": {
                            "available": True,
                            "detail": "edge tts enabled",
                        },
                    },
                    "components": {
                        "asr": {
                            "enabled": True,
                            "available": False,
                            "status": "down",
                            "detail": "component unavailable",
                        },
                        "tts": {
                            "enabled": True,
                            "available": True,
                            "status": "ok",
                            "detail": "component loaded",
                        },
                    },
                },
            }
        )

        self.assertTrue(statuses["edge.rpc.gateway"]["available"])
        self.assertFalse(statuses["edge.rpc.asr"]["available"])
        self.assertEqual("dependency", statuses["edge.rpc.asr"]["unavailable_kind"])
        self.assertIn("component unavailable", statuses["edge.rpc.asr"]["detail"])
        self.assertTrue(statuses["edge.rpc.tts"]["available"])
        self.assertIn("component loaded", statuses["edge.rpc.tts"]["detail"])


if __name__ == "__main__":
    unittest.main()
