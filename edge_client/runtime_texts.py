from __future__ import annotations

from typing import Any


_EDGE_RUNTIME_TEXTS: dict[str, dict[str, str]] = {
    "desktop_controller": {
        "screenshot_success": "截图成功",
        "action_success": "执行成功",
    },
    "input_reporter": {
        "report_success": "上报成功",
    },
    "fastapi_app": {
        "vlm_removed_detail": "Edge 端 VLM 模型已移除，视觉定位功能不可用",
    },
    "line_adapter": {
        "image_fallback": "[图片]",
        "image_content": "[图片]",
        "video_content": "[视频]",
        "audio_content": "[语音]",
        "sticker_template": "[贴图:{package_id}/{sticker_id}]",
        "default_file_name": "未知文件",
        "file_template": "[文件:{file_name}]",
        "default_location_title": "位置",
        "location_template": "[位置:{title} {address} ({lat},{lng})]",
    },
    "telegram_adapter": {
        "image_content": "[图片]",
        "voice_content": "[语音消息]",
        "video_content": "[视频]",
        "file_template": "[文件: {file_name}]",
        "file_fallback": "[文件]",
        "sticker_content": "[贴纸]",
    },
}


def edge_runtime_text(section: str, key: str, *, default: str = "", **kwargs: Any) -> str:
    text = str(_EDGE_RUNTIME_TEXTS.get(section, {}).get(key, default) or "")
    if not kwargs:
        return text
    try:
        return text.format_map({str(k): "" if v is None else str(v) for k, v in kwargs.items()})
    except Exception:
        return text
