from __future__ import annotations

from edge_client.gateway.adapters.line_adapter import LineAdapter
from edge_client.runtime_texts import edge_runtime_text


def test_edge_runtime_texts_are_clean() -> None:
    assert edge_runtime_text("desktop_controller", "screenshot_success") == "截图成功"
    assert edge_runtime_text("desktop_controller", "action_success") == "执行成功"
    assert edge_runtime_text("input_reporter", "report_success") == "上报成功"
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
