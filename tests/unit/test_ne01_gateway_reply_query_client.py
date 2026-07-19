from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace

from edge_client.gateway.webhook_security import (
    INTERNAL_NONCE_HEADER,
    INTERNAL_SIGNATURE_HEADER,
    INTERNAL_TIMESTAMP_HEADER,
    build_internal_signature,
)
from host_brain.channels.edge_transport import (
    _submit_gateway_payload,
    query_gateway_reply_delivery,
)


class _Response:
    status_code = 200

    @staticmethod
    def json() -> dict[str, object]:
        return {
            "status": "unknown",
            "schema_version": "Natsume.gateway.reply-query.v1",
            "effect_id": "effect-ne01-query-client",
            "delivery_id": "reply:ne01-query-client",
            "platform": "qq",
            "conversation_id": "conversation-ne01-query-client",
            "payload_digest": "sha256:" + "1" * 64,
            "effect_status": "UNKNOWN",
            "reason_code": "GW_REPLY_QUERY_NOT_FOUND",
            "outbox_id": None,
            "delivery_state": "NOT_FOUND",
            "status_version": None,
            "fencing_token": None,
            "delivery_receipt_digest": None,
        }


def test_query_client_uses_signed_query_mode_without_weakening_reply_success(
    monkeypatch,
) -> None:
    calls: list[tuple[str, bytes, dict[str, str]]] = []

    class _Client:
        def __init__(self, *_args, **_kwargs) -> None:  # noqa: ANN002, ANN003
            pass

        async def __aenter__(self):  # noqa: ANN201
            return self

        async def __aexit__(self, *_args) -> None:  # noqa: ANN002
            return None

        async def post(
            self,
            url: str,
            *,
            content: bytes,
            headers: dict[str, str],
        ) -> _Response:
            calls.append((url, bytes(content), dict(headers)))
            return _Response()

    secret = "ne01-query-client-secret-0123456789abcdef"
    monkeypatch.setenv("HOST_GATEWAY_REPLY_HMAC_SECRET", secret)
    monkeypatch.setenv("HOST_EDGE_GATEWAY_BASE_URL", "http://127.0.0.1:19000")
    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(AsyncClient=_Client))

    query_result = asyncio.run(
        query_gateway_reply_delivery(
            effect_id="effect-ne01-query-client",
            delivery_id="reply:ne01-query-client",
            platform="qq",
            conversation_id="conversation-ne01-query-client",
            payload_digest="sha256:" + "1" * 64,
        )
    )
    assert query_result.success is True
    assert query_result.status == "unknown"
    assert query_result.event["effect_status"] == "UNKNOWN"
    url, body, headers = calls[0]
    assert url == "http://127.0.0.1:19000/v1/gateway/reply/query"
    assert json.loads(body) == {
        "schema_version": "Natsume.gateway.reply-query.v1",
        "effect_id": "effect-ne01-query-client",
        "delivery_id": "reply:ne01-query-client",
        "platform": "qq",
        "conversation_id": "conversation-ne01-query-client",
        "payload_digest": "sha256:" + "1" * 64,
    }
    normalized_headers = {key.lower(): value for key, value in headers.items()}
    assert normalized_headers[INTERNAL_SIGNATURE_HEADER] == build_internal_signature(
        shared_secret=secret,
        platform="internal_reply",
        method="POST",
        path="/v1/gateway/reply/query",
        timestamp=normalized_headers[INTERNAL_TIMESTAMP_HEADER],
        nonce=normalized_headers[INTERNAL_NONCE_HEADER],
        raw_body=body,
    )

    # The same HTTP body/status must remain a failure on the mutating reply
    # path.  Query's accepted UNKNOWN read status is path-scoped.
    reply_result = asyncio.run(
        _submit_gateway_payload(
            path="/v1/gateway/reply",
            payload={"request_id": "ne01-query-client-ordinary-reply"},
            timeout_ms=1_000,
        )
    )
    assert reply_result.success is False
    assert reply_result.status == "unknown"
