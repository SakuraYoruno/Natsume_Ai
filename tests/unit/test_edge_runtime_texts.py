from __future__ import annotations

from edge_client.app.fastapi_app import _handle_visual_locate
from edge_client.channels.input_reporter import EdgeInputReporter, InputSourceType
from edge_client.gateway.adapters.line_adapter import LineAdapter
from edge_client.runtime_texts import edge_runtime_text


def test_edge_runtime_texts_are_clean() -> None:
    assert edge_runtime_text("desktop_controller", "screenshot_success") == "截图成功"
    assert edge_runtime_text("desktop_controller", "action_success") == "执行成功"
    assert edge_runtime_text("input_reporter", "report_success") == "上报成功"
    assert edge_runtime_text("fastapi_app", "vlm_removed_detail") == "Edge 端 VLM 模型已移除，视觉定位功能不可用"
    assert edge_runtime_text("line_adapter", "sticker_template", package_id="1", sticker_id="2") == "[贴图:1/2]"
    assert edge_runtime_text("telegram_adapter", "file_template", file_name="demo.txt") == "[文件: demo.txt]"


def test_line_adapter_placeholders_use_runtime_texts() -> None:
    assert LineAdapter._extract_content("image", {}) == "[图片]"
    assert LineAdapter._extract_content("video", {}) == "[视频]"
    assert LineAdapter._extract_content("audio", {}) == "[语音]"
    assert LineAdapter._extract_content("file", {"fileName": "demo.txt"}) == "[文件:demo.txt]"
    assert LineAdapter._extract_content(
        "location",
        {"title": "", "address": "Shanghai", "latitude": "1", "longitude": "2"},
    ) == "[位置:位置 Shanghai (1,2)]"


def test_input_reporter_send_success_uses_runtime_text() -> None:
    reporter = EdgeInputReporter(host_url="http://example.com")
    reporter._do_http_post = lambda _payload: None  # type: ignore[method-assign]

    result = reporter._send({}, InputSourceType.ASR)

    assert result.success is True
    assert result.detail == "上报成功"


def test_handle_visual_locate_uses_runtime_text() -> None:
    result = _handle_visual_locate(None, None)  # type: ignore[arg-type]

    assert result.success is False
    assert result.reason_code == "VLM_REMOVED"
    assert result.detail == "Edge 端 VLM 模型已移除，视觉定位功能不可用"
