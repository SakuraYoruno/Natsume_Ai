from __future__ import annotations

import json
import time

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

    recovered = EdgeInputReporter(
        host_url="http://host-up.invalid",
        cache_file=str(cache_file),
        retry_interval_s=0.01,
        max_retry=3,
    )
    sent_payloads: list[dict] = []

    def _success(payload):  # noqa: ANN001
        sent_payloads.append(dict(payload))

    recovered._do_http_post = _success  # type: ignore[method-assign]
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
