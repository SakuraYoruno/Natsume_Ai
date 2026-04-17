from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from host_brain.channels.edge_rpc_client import (
    EdgeRpcCallResult,
    _EDGE_CLOCK_OFFSET_CACHE,
    _invalidate_edge_clock_offset_cache,
    _normalize_base_url,
    _post_to_edge,
    _post_to_gateway,
    _resolve_edge_base_url,
    _resolve_edge_base_urls,
    _resolve_edge_clock_offset_ms,
    _resolve_gateway_base_urls,
    _resolve_retries,
    _resolve_timeout_ms,
    post_automate,
    post_cancel_call,
    post_fallback_presence,
    post_gateway_reply,
    post_speak,
    post_video_call,
    post_visual_locate,
    post_voice_call,
)


class TestEdgeRpcConfigHelpers(unittest.TestCase):
    def setUp(self) -> None:
        _EDGE_CLOCK_OFFSET_CACHE.update(
            {
                "base_url": "",
                "offset_ms": 0,
                "expires_at_ms": 0,
            }
        )

    def test_invalidate_clock_offset_cache_respects_base_url_guard(self) -> None:
        _EDGE_CLOCK_OFFSET_CACHE.update(
            {
                "base_url": "http://edge-a",
                "offset_ms": 123,
                "expires_at_ms": 999,
            }
        )

        _invalidate_edge_clock_offset_cache(base_url="http://edge-b")
        self.assertEqual("http://edge-a", _EDGE_CLOCK_OFFSET_CACHE["base_url"])
        self.assertEqual(123, _EDGE_CLOCK_OFFSET_CACHE["offset_ms"])

        _invalidate_edge_clock_offset_cache(base_url="http://edge-a")
        self.assertEqual("", _EDGE_CLOCK_OFFSET_CACHE["base_url"])
        self.assertEqual(0, _EDGE_CLOCK_OFFSET_CACHE["offset_ms"])
        self.assertEqual(0, _EDGE_CLOCK_OFFSET_CACHE["expires_at_ms"])

    def test_resolve_edge_base_urls_adds_local_fallbacks_and_dedupes(self) -> None:
        with patch.dict(
            os.environ,
            {
                "HOST_EDGE_RPC_BASE_URL": "",
                "EDGE_RPC_HOST": "10.0.0.9",
                "EDGE_RPC_PORT": "17890",
                "HOST_EDGE_RPC_ALLOW_LOCAL_FALLBACK": "true",
            },
            clear=False,
        ):
            urls = _resolve_edge_base_urls()

        self.assertEqual(
            [
                "http://10.0.0.9:17890",
                "http://127.0.0.1:17890",
                "http://localhost:17890",
            ],
            urls,
        )

    def test_resolve_gateway_base_urls_normalizes_override_and_invalid_port(self) -> None:
        with patch.dict(
            os.environ,
            {
                "HOST_EDGE_GATEWAY_BASE_URL": "https://gw.example/some/path",
                "EDGE_GATEWAY_HOST": "10.0.0.10",
                "EDGE_GATEWAY_PORT": "bad-port",
                "HOST_EDGE_RPC_ALLOW_LOCAL_FALLBACK": "true",
            },
            clear=False,
        ):
            urls = _resolve_gateway_base_urls()

        self.assertEqual(
            [
                "https://gw.example",
                "http://10.0.0.10:19000",
                "http://127.0.0.1:19000",
                "http://localhost:19000",
            ],
            urls,
        )

    def test_simple_result_and_config_helpers(self) -> None:
        result = EdgeRpcCallResult(
            success=True,
            status="ACCEPTED",
            reason_code=None,
            detail="ok",
            event={"kind": "voice"},
            latency_ms=12,
        )
        payload = result.as_dict()
        self.assertEqual("ACCEPTED", payload["status"])
        payload["event"]["kind"] = "mutated"
        self.assertEqual("voice", result.event["kind"])

        self.assertEqual("", _normalize_base_url(""))
        self.assertEqual("", _normalize_base_url("http://"))
        self.assertEqual("http://edge.local:17800", _normalize_base_url("edge.local:17800/path"))

        with patch.dict(
            os.environ,
            {
                "HOST_EDGE_RPC_BASE_URL": "",
                "EDGE_RPC_HOST": "edge-host",
                "EDGE_RPC_PORT": "bad-port",
            },
            clear=False,
        ):
            self.assertEqual("http://edge-host:17800", _resolve_edge_base_url())

        with patch.dict(os.environ, {"HOST_EDGE_RPC_TIMEOUT_MS": "bad"}, clear=False):
            self.assertEqual(3000, _resolve_timeout_ms())
        with patch.dict(os.environ, {"HOST_EDGE_RPC_TIMEOUT_MS": "-5"}, clear=False):
            self.assertEqual(200, _resolve_timeout_ms())
        with patch.dict(os.environ, {"HOST_EDGE_RPC_TIMEOUT_MS": "999999"}, clear=False):
            self.assertEqual(30000, _resolve_timeout_ms())

        with patch.dict(os.environ, {"HOST_EDGE_RPC_RETRIES": "bad"}, clear=False):
            self.assertEqual(1, _resolve_retries())
        with patch.dict(os.environ, {"HOST_EDGE_RPC_RETRIES": "-3"}, clear=False):
            self.assertEqual(0, _resolve_retries())
        with patch.dict(os.environ, {"HOST_EDGE_RPC_RETRIES": "9"}, clear=False):
            self.assertEqual(3, _resolve_retries())


class TestEdgeClockCalibration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        _EDGE_CLOCK_OFFSET_CACHE.update(
            {
                "base_url": "",
                "offset_ms": 0,
                "expires_at_ms": 0,
            }
        )

    async def test_resolve_edge_clock_offset_supports_async_response_and_cache(self) -> None:
        async def _raise_for_status() -> None:
            return None

        async def _json_body() -> dict[str, int]:
            return {"event": {"ts_ms": 100_500}}

        response = MagicMock()
        response.raise_for_status.side_effect = lambda: _raise_for_status()
        response.json.side_effect = lambda: _json_body()

        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False
        client.get = AsyncMock(return_value=response)

        with patch("httpx.AsyncClient", return_value=client):
            with patch("time.time", side_effect=[100.0, 100.1, 100.2, 100.21]):
                offset = await _resolve_edge_clock_offset_ms(
                    base_url="http://edge.test",
                    timeout_ms=5000,
                )
                cached_offset = await _resolve_edge_clock_offset_ms(
                    base_url="http://edge.test",
                    timeout_ms=5000,
                )

        self.assertEqual(350, offset)
        self.assertEqual(offset, cached_offset)
        self.assertEqual("http://edge.test", _EDGE_CLOCK_OFFSET_CACHE["base_url"])
        self.assertEqual(350, _EDGE_CLOCK_OFFSET_CACHE["offset_ms"])
        self.assertEqual(1, client.get.await_count)

    async def test_resolve_edge_clock_offset_returns_cached_value_after_probe_error(self) -> None:
        _EDGE_CLOCK_OFFSET_CACHE.update(
            {
                "base_url": "http://edge.test",
                "offset_ms": 77,
                "expires_at_ms": 0,
            }
        )

        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False
        client.get = AsyncMock(side_effect=RuntimeError("probe failed"))

        with patch("httpx.AsyncClient", return_value=client):
            with patch("time.time", return_value=200.0):
                offset = await _resolve_edge_clock_offset_ms(
                    base_url="http://edge.test",
                    timeout_ms=2000,
                )

        self.assertEqual(77, offset)


class TestEdgeRpcCalls(unittest.IsolatedAsyncioTestCase):
    async def test_post_to_edge_rejects_loopback_when_blocked(self) -> None:
        with patch.dict(
            os.environ,
            {
                "HOST_EDGE_RPC_BASE_URL": "",
                "EDGE_RPC_HOST": "127.0.0.1",
                "EDGE_RPC_PORT": "17800",
            },
            clear=False,
        ):
            result = await _post_to_edge(
                path="/v1/edge/wechat/command",
                payload={"action": "send_text"},
                request_id="req-loopback",
                block_loopback=True,
            )

        self.assertFalse(result.success)
        self.assertEqual("REJECTED", result.status)
        self.assertEqual("EDGE_RPC_LOCAL_BLOCKED", result.reason_code)

    async def test_post_to_edge_recalibrates_once_after_auth_expired(self) -> None:
        auth_expired = MagicMock()
        auth_expired.status_code = 200
        auth_expired.json.return_value = {
            "success": False,
            "status": "REJECTED",
            "reason_code": "EDGE_RPC_AUTH_EXPIRED",
            "detail": "expired",
            "event": {},
        }
        accepted = MagicMock()
        accepted.status_code = 200
        accepted.json.return_value = {
            "success": True,
            "status": "ACCEPTED",
            "reason_code": None,
            "detail": "ok",
            "event": {"kind": "speak"},
        }

        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False
        client.post = AsyncMock(side_effect=[auth_expired, accepted])

        with patch("httpx.AsyncClient", return_value=client):
            with patch(
                "host_brain.channels.edge_rpc_client._resolve_edge_base_urls",
                return_value=["http://edge.remote:17800"],
            ):
                with patch(
                    "host_brain.channels.edge_rpc_client._resolve_edge_clock_offset_ms",
                    new=AsyncMock(side_effect=[12, 24]),
                ) as resolve_offset:
                    with patch(
                        "host_brain.channels.edge_rpc_client._invalidate_edge_clock_offset_cache",
                    ) as invalidate_cache:
                        with patch(
                            "host_brain.channels.edge_rpc_client.now_ms",
                            return_value=1_000,
                        ):
                            with patch.dict(
                                os.environ,
                                {
                                    "HOST_EDGE_RPC_RETRIES": "1",
                                    "EDGE_ALLOW_INSECURE_AUTH": "true",
                                },
                                clear=False,
                            ):
                                result = await _post_to_edge(
                                    path="/v1/edge/speak",
                                    payload={"text": "hello"},
                                    request_id="req-auth-expired",
                                )

        self.assertTrue(result.success)
        self.assertEqual("ACCEPTED", result.status)
        self.assertEqual(2, client.post.await_count)
        self.assertEqual(2, resolve_offset.await_count)
        invalidate_cache.assert_called_once_with(base_url="http://edge.remote:17800")

    async def test_post_to_gateway_maps_failed_status_and_transport_error(self) -> None:
        failed_response = MagicMock()
        failed_response.status_code = 200
        failed_response.json.return_value = {
            "status": "failed",
            "detail": "adapter timeout",
        }

        failing_client = AsyncMock()
        failing_client.__aenter__.return_value = failing_client
        failing_client.__aexit__.return_value = False
        failing_client.post = AsyncMock(return_value=failed_response)

        transport_client = AsyncMock()
        transport_client.__aenter__.return_value = transport_client
        transport_client.__aexit__.return_value = False
        transport_client.post = AsyncMock(side_effect=TimeoutError("gateway late"))

        with patch(
            "host_brain.channels.edge_rpc_client._resolve_gateway_base_urls",
            return_value=["http://gateway.remote:19000"],
        ):
            with patch("httpx.AsyncClient", return_value=failing_client):
                with patch("host_brain.channels.edge_rpc_client._resolve_retries", return_value=0):
                    failed = await _post_to_gateway(
                        path="/v1/gateway/reply",
                        payload={"content": "hello"},
                    )

            with patch("httpx.AsyncClient", return_value=transport_client):
                with patch("host_brain.channels.edge_rpc_client._resolve_retries", return_value=0):
                    degraded = await _post_to_gateway(
                        path="/v1/gateway/reply",
                        payload={"content": "hello"},
                    )

        self.assertFalse(failed.success)
        self.assertEqual("failed", failed.status)
        self.assertEqual("GATEWAY_REPLY_FAILED", failed.reason_code)
        self.assertIn("adapter timeout", failed.detail)
        self.assertFalse(degraded.success)
        self.assertEqual("GATEWAY_REPLY_CALL_FAILED", degraded.reason_code)
        self.assertIn("TimeoutError", degraded.detail)


class TestEdgeRpcWrappers(unittest.IsolatedAsyncioTestCase):
    async def test_wrapper_payloads_and_timeouts(self) -> None:
        accepted = EdgeRpcCallResult(
            success=True,
            status="ACCEPTED",
            reason_code=None,
            detail="ok",
            event={},
            latency_ms=1,
        )

        with patch(
            "host_brain.channels.edge_rpc_client._post_to_edge",
            new=AsyncMock(return_value=accepted),
        ) as post_to_edge:
            await post_fallback_presence(request_id="req-fallback", payload=None)
            await post_visual_locate(
                instruction="find confirm button",
                auto_click=True,
                request_id="req-visual",
            )

        self.assertEqual(2, post_to_edge.await_count)
        self.assertEqual({}, post_to_edge.await_args_list[0].kwargs["payload"])
        self.assertEqual("/v1/edge/fallback/presence", post_to_edge.await_args_list[0].kwargs["path"])
        self.assertEqual("/v1/edge/visual_locate", post_to_edge.await_args_list[1].kwargs["path"])
        self.assertEqual(30000, post_to_edge.await_args_list[1].kwargs["request_timeout_ms"])
        self.assertEqual(
            {
                "instruction": "find confirm button",
                "monitor_index": 1,
                "auto_click": True,
            },
            post_to_edge.await_args_list[1].kwargs["payload"],
        )

        with patch(
            "host_brain.channels.edge_rpc_client._post_to_gateway",
            new=AsyncMock(return_value=accepted),
        ) as post_to_gateway:
            with patch("host_brain.channels.edge_rpc_client._resolve_timeout_ms", return_value=5000):
                await post_gateway_reply(
                    platform="WeChat",
                    conversation_id=" Conv-1 ",
                    content="Hello",
                    msg_type="TEXT",
                    request_id="req-gateway",
                    wait_result=True,
                    timeout_ms=15000,
                )

        self.assertEqual("/v1/gateway/reply", post_to_gateway.await_args.kwargs["path"])
        self.assertEqual(18000, post_to_gateway.await_args.kwargs["request_timeout_ms"])
        self.assertEqual(
            {
                "request_id": "req-gateway",
                "platform": "wechat",
                "conversation_id": "Conv-1",
                "content": "Hello",
                "msg_type": "text",
                "wait_result": True,
                "timeout_ms": 15000,
            },
            post_to_gateway.await_args.kwargs["payload"],
        )

    async def test_post_speak_and_post_automate_payloads(self) -> None:
        accepted = EdgeRpcCallResult(
            success=True,
            status="ACCEPTED",
            reason_code=None,
            detail="ok",
            event={},
            latency_ms=2,
        )

        with patch(
            "host_brain.channels.edge_rpc_client._post_to_edge",
            new=AsyncMock(return_value=accepted),
        ) as post_to_edge:
            with patch(
                "host_brain.channels.edge_rpc_client.new_request_id",
                return_value="req-auto-speak",
            ):
                await post_speak(
                    text=" A〜B～C\u200b ",
                    voice_id="voice-a",
                    speed=1.25,
                    pitch=-0.25,
                    emotion_tag="happy",
                    emotion_intensity=1.5,
                    style_note="soft",
                    lang_hint="JA",
                )
            await post_automate(
                actions=[{"type": "click", "x": 12, "y": 24}],
                stop_on_error=False,
                request_id="req-automate",
            )

        self.assertEqual("/v1/edge/speak", post_to_edge.await_args_list[0].kwargs["path"])
        self.assertEqual("req-auto-speak", post_to_edge.await_args_list[0].kwargs["request_id"])
        self.assertEqual(60000, post_to_edge.await_args_list[0].kwargs["request_timeout_ms"])
        self.assertEqual(
            {
                "session_id": "sess::req-auto-speak",
                "utterance_id": "utt::req-auto-speak",
                "text": "AーBーC",
                "speed": 1.25,
                "pitch": -0.25,
                "voice_id": "voice-a",
                "emotion_tag": "HAPPY",
                "emotion_intensity": 1.0,
                "style_note": "soft",
                "lang_hint": "ja",
            },
            post_to_edge.await_args_list[0].kwargs["payload"],
        )

        self.assertEqual("/v1/edge/automate", post_to_edge.await_args_list[1].kwargs["path"])
        self.assertEqual(
            {
                "actions": [{"type": "click", "x": 12, "y": 24}],
                "stop_on_error": False,
            },
            post_to_edge.await_args_list[1].kwargs["payload"],
        )

    async def test_call_wrappers_delegate_to_gateway_reply(self) -> None:
        accepted = EdgeRpcCallResult(
            success=True,
            status="sent",
            reason_code=None,
            detail="sent",
            event={},
            latency_ms=1,
        )

        with patch(
            "host_brain.channels.edge_rpc_client.post_gateway_reply",
            new=AsyncMock(return_value=accepted),
        ) as gateway_reply:
            await post_voice_call(
                target="alice",
                conversation_id=None,
                request_id="req-voice-call",
            )
            await post_video_call(
                target="bob",
                conversation_id="conv-bob",
                request_id="req-video-call",
            )
            await post_cancel_call(
                request_id="req-cancel-call",
                timeout_ms=9000,
            )

        self.assertEqual(3, gateway_reply.await_count)
        self.assertEqual("voice_call", gateway_reply.await_args_list[0].kwargs["msg_type"])
        self.assertEqual("alice", gateway_reply.await_args_list[0].kwargs["conversation_id"])
        self.assertEqual("video_call", gateway_reply.await_args_list[1].kwargs["msg_type"])
        self.assertEqual("conv-bob", gateway_reply.await_args_list[1].kwargs["conversation_id"])
        self.assertEqual("cancel_call", gateway_reply.await_args_list[2].kwargs["msg_type"])
        self.assertEqual("_cancel_call", gateway_reply.await_args_list[2].kwargs["conversation_id"])


if __name__ == "__main__":
    unittest.main()
