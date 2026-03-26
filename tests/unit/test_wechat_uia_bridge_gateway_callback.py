from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


def _load_bridge_module():
    module_path = Path(__file__).resolve().parents[2] / "tools" / "wechat_uia_bridge.py"
    spec = importlib.util.spec_from_file_location("wechat_uia_bridge_callback_test_module", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load wechat_uia_bridge module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_post_to_edge_callback_targets_gateway_webhook(monkeypatch) -> None:
    bridge = _load_bridge_module()

    class _FakeResponse:
        def __init__(self, body: dict[str, object]) -> None:
            self.status = 200
            self._body = json.dumps(body).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return self._body

    seen: dict[str, object] = {}

    def _fake_urlopen(req, timeout: float):
        seen["url"] = req.full_url
        seen["timeout"] = timeout
        return _FakeResponse({"status": "accepted"})

    monkeypatch.setattr(bridge.urllib.request, "urlopen", _fake_urlopen)

    ok = bridge._post_to_edge_callback(
        "http://127.0.0.1:19000",
        {"msg_id": "msg-1", "content": "hello"},
        timeout_s=1.5,
    )

    assert ok is True
    assert str(seen["url"]).endswith("/v1/gateway/wechat/webhook")


def test_post_to_edge_callback_rejects_legacy_rejected_payload(monkeypatch) -> None:
    bridge = _load_bridge_module()

    class _FakeResponse:
        def __init__(self, body: dict[str, object]) -> None:
            self.status = 200
            self._body = json.dumps(body).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return self._body

    monkeypatch.setattr(
        bridge.urllib.request,
        "urlopen",
        lambda req, timeout: _FakeResponse(
            {"success": False, "status": "rejected", "event": {"deprecated": True}}
        ),
    )

    ok = bridge._post_to_edge_callback(
        "http://127.0.0.1:19000",
        {"msg_id": "msg-2", "content": "hello"},
        timeout_s=1.0,
    )

    assert ok is False
