from __future__ import annotations

from pathlib import Path

from host_brain.app.multimodal_observer import MultimodalObserver


def test_multimodal_observer_sync_image_uses_ocr_text() -> None:
    observer = MultimodalObserver()

    result = observer.observe(
        request_id="req_mm_img",
        platform="qq",
        conversation_id="qq_dm_mm_img",
        user_text="你看这张图",
        attachments=[
            {
                "attachment_id": "att_img_1",
                "type": "image",
                "ocr_text": "你好，夏目蓝",
            }
        ],
        render_target_lang="zh",
    )

    assert result["mode"] == "sync"
    assert "你好，夏目蓝" in str(result["summary_for_chat"])
    assert result["observations"]["items"][0]["ocr_text"] == "你好，夏目蓝"


def test_multimodal_observer_sync_text_file_extracts_excerpt(tmp_path: Path) -> None:
    path = tmp_path / "sample.txt"
    path.write_text("第一行\n第二行\n第三行", encoding="utf-8")
    observer = MultimodalObserver()

    result = observer.observe(
        request_id="req_mm_file",
        platform="qq",
        conversation_id="qq_dm_mm_file",
        user_text="帮我看看这个文件",
        attachments=[
            {
                "attachment_id": "att_file_1",
                "type": "file",
                "local_path": str(path),
                "file_name": path.name,
            }
        ],
        render_target_lang="zh",
    )

    assert result["mode"] == "sync"
    assert "第一行" in str(result["summary_for_chat"])
    assert "第二行" in str(result["observations"]["items"][0]["text_excerpt"])


def test_multimodal_observer_video_defaults_to_async() -> None:
    observer = MultimodalObserver()

    result = observer.observe(
        request_id="req_mm_video",
        platform="qq",
        conversation_id="qq_dm_mm_video",
        user_text="你先看看这个视频",
        attachments=[
            {
                "attachment_id": "att_video_1",
                "type": "video",
                "file_name": "demo.mp4",
                "duration_ms": 8000,
            }
        ],
        render_target_lang="zh",
    )

    assert result["mode"] == "async"
    assert result["pending_jobs"][0]["type"] == "video"
    assert "视频" in str(result["summary_for_chat"])
