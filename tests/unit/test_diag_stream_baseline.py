from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import httpx


def _load_module():
    path = Path(__file__).resolve().parents[2] / "tools" / "diag_stream_baseline.py"
    spec = importlib.util.spec_from_file_location("diag_stream_baseline", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_stream_probe_posts_production_chat_contract() -> None:
    module = _load_module()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/chat"
        assert json.loads(request.content) == {
            "message": "在吗？",
            "session_id": "probe-1",
        }
        body = (
            'data: {"phase":"RENDER","chunk":"在。","request_id":"req-1"}\n\n'
            'data: {"phase":"COMMIT","decision":"APPROVE","request_id":"req-1",'
            '"audit_refs":{"path":"surface_reply:native_affective_stream",'
            '"model_id":"deepseek-v4-flash","first_token_ms":321}}\n\n'
        )
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        row = module._read_sse_run(
            client=client,
            url="http://host.test/v1/chat",
            message="在吗？",
            run_index=1,
            session_prefix="probe",
            timeout_s=1.0,
        )

    assert row.ok is True
    assert row.request_id == "req-1"
    assert row.path == "surface_reply:native_affective_stream"
    assert row.model_id == "deepseek-v4-flash"
    assert row.server_first_token_ms == 321
    assert row.first_render_ms is not None
    assert row.commit_ms is not None


def test_stream_probe_fails_when_commit_has_no_visible_render() -> None:
    module = _load_module()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text='data: {"phase":"COMMIT","decision":"NO_REPLY","request_id":"req-2"}\n\n',
            headers={"content-type": "text/event-stream"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        row = module._read_sse_run(
            client=client,
            url="http://host.test/v1/chat",
            message="测试",
            run_index=2,
            session_prefix="probe",
            timeout_s=1.0,
        )

    assert row.ok is False
    assert row.error == "STREAM_RENDER_MISSING"
    assert row.commit_ms is not None
