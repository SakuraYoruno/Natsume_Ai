from __future__ import annotations

import json
import base64
import hashlib
import time
import urllib.request

import pytest

from edge_client.channels.input_reporter import EdgeInputReporter


def test_input_reporter_persists_and_retries_gateway_reports(tmp_path) -> None:
    cache_file = tmp_path / "gateway_input_retry_queue.json"
    reporter = EdgeInputReporter(
        host_url="http://host-down.invalid",
        cache_file=str(cache_file),
        retry_interval_s=0.01,
        max_retry=3,
    )

    def _fail(_payload):  # noqa: ANN001
        raise TimeoutError("host down")

    reporter._do_http_post = _fail  # type: ignore[method-assign]
    result = reporter.report_gateway(
        platform="qq",
        conversation_id="qq_dm_1",
        content="[图片消息]",
        msg_type="image",
        msg_id="m1",
        from_id="u1",
        to_id="bot",
    )

    assert result.cached is True
    persisted = json.loads(cache_file.read_text(encoding="utf-8"))
    assert persisted[0]["payload"]["msg_id"] == "m1"
    assert persisted[0]["source"] == "GATEWAY"
    assert base64.b64decode(persisted[0]["body_b64"])
    assert persisted[0]["idempotency_key"].startswith("edge-host:")

    recovered = EdgeInputReporter(
        host_url="http://host-up.invalid",
        cache_file=str(cache_file),
        retry_interval_s=0.01,
        max_retry=3,
    )
    sent_payloads: list[dict] = []

    def _success(payload, *, body, idempotency_key):  # noqa: ANN001
        sent_payloads.append(dict(payload))
        assert b'"msg_id":"m1"' in body
        assert idempotency_key == persisted[0]["idempotency_key"]
        # Crash during the network call must leave the record recoverable.
        assert json.loads(cache_file.read_text(encoding="utf-8"))[0]["payload"]["msg_id"] == "m1"

    recovered._do_http_post_frozen = _success  # type: ignore[method-assign]
    recovered.start_retry_loop()
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and recovered.cache_size() != 0:
        time.sleep(0.02)
    recovered.stop_retry_loop()

    assert sent_payloads and sent_payloads[0]["msg_id"] == "m1"
    stats = recovered.stats()
    assert stats["cache_size"] == 0
    assert stats["total_sent"] == 1
    assert stats["last_result"]["success"] is True
    assert "retry_success" in stats["last_result"]["detail"]
    assert json.loads(cache_file.read_text(encoding="utf-8")) == []


def test_legacy_retry_exhaustion_moves_record_to_atomic_sibling_dlq(tmp_path) -> None:
    cache_file = tmp_path / "gateway_input_retry_queue.json"
    reporter = EdgeInputReporter(
        host_url="http://host-down.invalid",
        cache_file=str(cache_file),
        retry_interval_s=0.01,
        max_retry=1,
    )

    def _fail_initial(_payload):  # noqa: ANN001
        raise TimeoutError("host down")

    def _fail_retry(_payload, *, body, idempotency_key):  # noqa: ANN001
        assert body and idempotency_key
        raise TimeoutError("host still down")

    reporter._do_http_post = _fail_initial  # type: ignore[method-assign]
    reporter._do_http_post_frozen = _fail_retry  # type: ignore[method-assign]
    result = reporter.report_gateway(
        platform="qq",
        conversation_id="qq-dlq",
        content="legacy pending",
        msg_id="legacy-dlq-1",
    )
    assert result.cached is True
    reporter.start_retry_loop()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and reporter.cache_size() != 0:
        time.sleep(0.02)
    reporter.stop_retry_loop()

    assert reporter.cache_size() == 0
    assert json.loads(cache_file.read_text(encoding="utf-8")) == []
    dlq_file = cache_file.with_suffix(cache_file.suffix + ".dlq.jsonl")
    rows = [json.loads(line) for line in dlq_file.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["reason_code"] == "INPUT_REPORT_RETRY_EXHAUSTED"
    assert rows[0]["report"]["payload"]["msg_id"] == "legacy-dlq-1"
    assert "content_preview" not in reporter.stats()["last_payload"]


def test_durable_reporter_emits_task0487_canonical_wire_headers(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Response:
        status = 200

        def __init__(self, content: bytes) -> None:
            self._content = content

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return self._content

    def _urlopen(request: urllib.request.Request, *, timeout: float):
        captured["request"] = request
        captured["timeout"] = timeout
        headers = {name.lower(): value for name, value in request.header_items()}
        receipt = {
            "success": True,
            "status": "ACCEPTED",
            "source": "GATEWAY",
            "receipt_ref": "host-input-result://request/ACCEPTED",
            "body_sha256": hashlib.sha256(bytes(request.data or b"")).hexdigest(),
            "idempotency_sha256": hashlib.sha256(
                headers["x-natsume-idempotency-key"].encode("utf-8")
            ).hexdigest(),
        }
        return _Response(json.dumps(receipt, separators=(",", ":")).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    reporter = EdgeInputReporter(
        host_url="http://127.0.0.1:8000",
        hmac_key="edge-host-test-secret",
    )
    result = reporter.deliver_gateway_payload(
        {
            "source": "GATEWAY",
            "platform": "qq",
            "msg_id": "m-secure-1",
            "conversation_id": "conv-secure-1",
            "content": "hello",
            "msg_type": "text",
        },
        idempotency_key="gateway-inbox-secure-1",
    )
    assert result.success is True
    request = captured["request"]
    assert isinstance(request, urllib.request.Request)
    headers = {name.lower(): value for name, value in request.header_items()}
    expected = {
        "x-natsume-identity",
        "x-natsume-proof",
        "x-natsume-idempotency-key",
        "x-natsume-nonce",
        "x-natsume-correlation-id",
    }
    assert expected <= set(headers)
    assert len(headers["x-natsume-proof"]) == 64

    from host_brain.secure_ingress.credential import build_hmac_sha256_signature
    from host_brain.secure_ingress.surfaces import IngressSurface, build_surface_request
    from natsume_contracts.identity import IngressIdentityEnvelope

    encoded_identity = headers["x-natsume-identity"]
    identity_bytes = base64.urlsafe_b64decode(encoded_identity + "=" * (-len(encoded_identity) % 4))
    identity = IngressIdentityEnvelope.from_canonical_json(identity_bytes)
    secure_request = build_surface_request(
        surface=IngressSurface.HOST,
        identity=identity,
        credential=None,
        method="POST",
        normalized_path="/v1/host/input",
        idempotency_key=headers["x-natsume-idempotency-key"],
        nonce=headers["x-natsume-nonce"],
        raw_body=bytes(request.data or b""),
        received_at_epoch_ms=identity.credential_proof.issued_at_epoch_ms,
        transport_key=f"principal:{identity.principal.principal_id}",
        correlation_id=headers["x-natsume-correlation-id"],
    )
    assert build_hmac_sha256_signature(
        secure_request,
        "edge-host-test-secret",
    ) == headers["x-natsume-proof"]


@pytest.mark.parametrize(
    "response_body",
    (
        b"",
        b"not-json",
        b'[{"success":true}]',
        b'{"success":true,"status":"ACCEPTED","source":"GATEWAY"}',
        b'{"success":true,"status":"ACCEPTED","source":"GATEWAY",'
        b'"receipt_ref":"host-input-result://wrong/ACCEPTED",'
        b'"body_sha256":"bad","idempotency_sha256":"bad"}',
    ),
)
def test_durable_reporter_never_acks_missing_or_mismatched_host_receipt(
    monkeypatch,
    response_body: bytes,
) -> None:
    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return response_body

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: _Response())
    reporter = EdgeInputReporter(
        host_url="http://127.0.0.1:8000",
        hmac_key="edge-host-test-secret",
    )
    result = reporter.deliver_gateway_payload(
        {
            "source": "GATEWAY",
            "platform": "qq",
            "msg_id": "receipt-negative",
            "conversation_id": "receipt-negative",
            "content": "must not false ACK",
            "msg_type": "text",
        },
        idempotency_key="gateway-inbox-receipt-negative",
    )

    assert result.success is False
    assert result.reason_code == "INPUT_REPORT_DELIVERY_UNCERTAIN"
    assert result.cached is False
