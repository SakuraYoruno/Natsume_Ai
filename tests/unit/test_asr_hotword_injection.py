"""M10-B ASR 热词注入 — inject_canon_hotwords() 单元测试（mock HTTP）。

T-ASR-HW-01：正常注入 — mock 返回热词列表时，inject_canon_hotwords 返回注入词数。
T-ASR-HW-02：Host 不可达 — 连接失败时降级返回 0，不抛异常。
T-ASR-HW-03：热词列表为空 — Host 返回空列表时返回 0。
"""

from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from edge_client.senses.asr import inject_canon_hotwords, get_canon_hotwords, _CANON_HOTWORDS_CACHE


# ---------------------------------------------------------------------------
# 辅助：构造 mock HTTP 响应
# ---------------------------------------------------------------------------

def _mock_response(hotwords: list[str], status: int = 200):
    """构造 urllib.request.urlopen 的 mock 返回值。"""
    body = json.dumps({"hotwords": hotwords, "total": len(hotwords), "request_id": "test", "timestamp": 0})
    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.status = status
    mock_resp.read.return_value = body.encode("utf-8")
    return mock_resp


# ---------------------------------------------------------------------------
# T-ASR-HW-01：正常注入
# ---------------------------------------------------------------------------

def test_inject_canon_hotwords_success():
    """mock 返回热词列表时，inject_canon_hotwords 应返回注入词数并更新缓存。"""
    hotwords = ["夏目贵志", "猫咪老师", "妖怪"]
    _CANON_HOTWORDS_CACHE.clear()

    with patch("urllib.request.urlopen", return_value=_mock_response(hotwords)):
        count = inject_canon_hotwords("http://localhost:8000")

    assert count == 3
    cached = get_canon_hotwords()
    assert cached == hotwords


# ---------------------------------------------------------------------------
# T-ASR-HW-02：Host 不可达
# ---------------------------------------------------------------------------

def test_inject_canon_hotwords_unreachable():
    """Host 不可达时应降级返回 0，不抛异常。"""
    import urllib.error

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")):
        count = inject_canon_hotwords("http://localhost:9999")

    assert count == 0


# ---------------------------------------------------------------------------
# T-ASR-HW-03：热词列表为空
# ---------------------------------------------------------------------------

def test_inject_canon_hotwords_empty():
    """Host 返回空热词列表时应返回 0。"""
    _CANON_HOTWORDS_CACHE.clear()

    with patch("urllib.request.urlopen", return_value=_mock_response([])):
        count = inject_canon_hotwords("http://localhost:8000")

    assert count == 0
