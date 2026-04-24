from __future__ import annotations

from pathlib import Path

from host_brain.app.multimodal_observer import MultimodalObserver
from host_brain.app.multimodal_observer_backends import MultimodalObserverInput


class _StaticBackend:
    backend_name = "static"

    def __init__(self, *, supported_types: set[str], result_factory, supports_phase: str | None = None) -> None:
        self.supported_types = set(supported_types)
        self.result_factory = result_factory
        self.supports_phase = supports_phase
        self.calls: list[MultimodalObserverInput] = []

    def supports(self, payload: MultimodalObserverInput) -> bool:
        item_type = str(payload.attachment.get("type") or "").strip().lower()
        if item_type not in self.supported_types:
            return False
        if self.supports_phase is not None and payload.phase != self.supports_phase:
            return False
        return True

    def observe_attachment(self, payload: MultimodalObserverInput) -> dict[str, object]:
        self.calls.append(payload)
        return dict(self.result_factory(payload))


def _fallback_result(payload: MultimodalObserverInput) -> dict[str, object]:
    attachment = dict(payload.attachment or {})
    item_type = str(attachment.get("type") or "").strip().lower()
    if item_type == "file":
        excerpt = str(attachment.get("parsed_text") or "").strip()
        if not excerpt and str(attachment.get("local_path") or "").strip():
            path = Path(str(attachment["local_path"]))
            if path.exists():
                excerpt = path.read_text(encoding="utf-8")
        summary = f"文件内容：{excerpt}" if excerpt else "文件暂时只有元数据。"
        return {
            "attachment_id": attachment.get("attachment_id"),
            "type": item_type,
            "backend": "rule_fallback",
            "text_excerpt": excerpt or None,
            "summary": summary,
            "confidence": 0.4 if excerpt else 0.2,
            "safety_flags": [],
            "followup_required": False,
            "artifacts": {},
        }
    if item_type == "voice":
        transcript = str(attachment.get("transcript") or "").strip()
        return {
            "attachment_id": attachment.get("attachment_id"),
            "type": item_type,
            "backend": "rule_fallback",
            "transcript": transcript or None,
            "summary": f"收到一段语音。转写内容大致是：{transcript}" if transcript else "收到一段语音。",
            "confidence": 0.88 if transcript else 0.2,
            "safety_flags": [],
            "followup_required": False,
            "artifacts": {},
        }
    ocr_text = str(attachment.get("ocr_text") or "").strip()
    return {
        "attachment_id": attachment.get("attachment_id"),
        "type": item_type or "image",
        "backend": "rule_fallback",
        "ocr_text": ocr_text or None,
        "summary": f"收到一张图片。图里大致能读到这些字：{ocr_text}" if ocr_text else "收到一张图片。",
        "confidence": 0.42 if ocr_text else 0.2,
        "safety_flags": [],
        "followup_required": False,
        "artifacts": {},
    }


def _model_result(payload: MultimodalObserverInput) -> dict[str, object]:
    attachment = dict(payload.attachment or {})
    item_type = str(attachment.get("type") or "").strip().lower()
    if item_type == "video":
        return {
            "attachment_id": attachment.get("attachment_id"),
            "type": item_type,
            "backend": "multimodal_model",
            "model_provider": "gemini",
            "model_id": "gemini-sync",
            "visual_summary": "画面里有人在看手机",
            "audio_summary": "有人在说今晚八点开会",
            "visible_text": "晚上八点",
            "scene_tags": ["video", "meeting"],
            "summary": "收到一个视频。画面大致是：画面里有人在看手机 音频大致在说：有人在说今晚八点开会",
            "confidence": 0.81,
            "safety_flags": [],
            "followup_required": False,
            "artifacts": {},
        }
    return {
        "attachment_id": attachment.get("attachment_id"),
        "type": item_type,
        "backend": "multimodal_model",
        "model_provider": "gemini",
        "model_id": "gemini-sync",
        "visual_summary": "一张聊天截图",
        "visible_text": "你好，夏目蓝",
        "meme_intent": "轻松打趣" if item_type == "emoji" else "",
        "scene_tags": ["screenshot", "chat"] if item_type != "emoji" else ["meme"],
        "summary": "收到一张图片。画面里主要是：一张聊天截图 图中文字大致是：你好，夏目蓝",
        "confidence": 0.77,
        "safety_flags": [],
        "followup_required": False,
        "artifacts": {},
    }


def test_multimodal_observer_sync_image_uses_ocr_text() -> None:
    observer = MultimodalObserver(
        model_backend=_StaticBackend(supported_types=set(), result_factory=_model_result),
        fallback_backend=_StaticBackend(supported_types={"image", "file", "voice", "video"}, result_factory=_fallback_result),
    )

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
    assert result["observations"]["items"][0]["backend"] == "rule_fallback"


def test_multimodal_observer_sync_image_prefers_model_backend_when_available() -> None:
    model_backend = _StaticBackend(supported_types={"image"}, result_factory=_model_result)
    fallback_backend = _StaticBackend(supported_types={"image", "file", "voice", "video"}, result_factory=_fallback_result)
    observer = MultimodalObserver(model_backend=model_backend, fallback_backend=fallback_backend)

    result = observer.observe(
        request_id="req_mm_img_model",
        platform="qq",
        conversation_id="qq_dm_mm_img_model",
        user_text="你看这张图",
        attachments=[
            {
                "attachment_id": "att_img_model",
                "type": "image",
                "local_path": str(Path(__file__)),
                "ocr_text": "你好，夏目蓝",
            }
        ],
        render_target_lang="zh",
    )

    assert result["mode"] == "sync"
    assert result["observations"]["items"][0]["backend"] == "multimodal_model"
    assert result["observations"]["items"][0]["visible_text"] == "你好，夏目蓝"
    assert len(model_backend.calls) == 1
    assert len(fallback_backend.calls) == 0


def test_multimodal_observer_capability_check_guides_summary() -> None:
    model_backend = _StaticBackend(supported_types={"image"}, result_factory=_model_result)
    fallback_backend = _StaticBackend(supported_types={"image", "file", "voice", "video"}, result_factory=_fallback_result)
    observer = MultimodalObserver(model_backend=model_backend, fallback_backend=fallback_backend)

    result = observer.observe(
        request_id="req_mm_img_capability",
        platform="qq",
        conversation_id="qq_dm_mm_img_capability",
        user_text="你能看到图片中的内容吗",
        attachments=[
            {
                "attachment_id": "att_img_capability",
                "type": "image",
                "local_path": str(Path(__file__)),
                "ocr_text": "hello",
                "caption": "动漫女孩在挥手",
            }
        ],
        render_target_lang="zh",
    )

    assert result["observations"]["artifact_type"] in {None, "", "screenshot"}
    summary_text = str(result["summary_for_chat"])
    assert "一张聊天截图" in summary_text or "你好，夏目蓝" in summary_text


def test_multimodal_observer_emoji_meaning_prefers_social_intent() -> None:
    model_backend = _StaticBackend(supported_types={"emoji"}, result_factory=_model_result)
    fallback_backend = _StaticBackend(supported_types={"image", "file", "voice", "video", "emoji"}, result_factory=_fallback_result)
    observer = MultimodalObserver(model_backend=model_backend, fallback_backend=fallback_backend)

    result = observer.observe(
        request_id="req_mm_emoji_meaning",
        platform="qq",
        conversation_id="qq_dm_mm_emoji_meaning",
        user_text="这个表情包什么意思",
        attachments=[
            {
                "attachment_id": "att_emoji_meaning",
                "type": "emoji",
                "local_path": str(Path(__file__)),
                "ocr_text": "hello",
                "caption": "动漫女孩在挥手",
            }
        ],
        render_target_lang="zh",
    )

    assert result["observations"]["primary_modality"] == "emoji"
    assert "轻松打趣" in str(result["summary_for_chat"])


def test_multimodal_observer_image_only_without_question_does_not_default_to_literal_description() -> None:
    def _neutral_image_result(payload: MultimodalObserverInput) -> dict[str, object]:
        attachment = dict(payload.attachment or {})
        return {
            "attachment_id": attachment.get("attachment_id"),
            "type": "image",
            "backend": "multimodal_model",
            "model_provider": "gemini",
            "model_id": "gemini-sync",
            "visual_summary": "一只放在桌上的白色马克杯",
            "visible_text": "",
            "scene_tags": [],
            "summary": "收到一张图片。画面里主要是：一只放在桌上的白色马克杯",
            "confidence": 0.73,
            "safety_flags": [],
            "followup_required": False,
            "artifacts": {},
        }

    model_backend = _StaticBackend(supported_types={"image"}, result_factory=_neutral_image_result)
    fallback_backend = _StaticBackend(supported_types={"image", "file", "voice", "video"}, result_factory=_fallback_result)
    observer = MultimodalObserver(model_backend=model_backend, fallback_backend=fallback_backend)

    result = observer.observe(
        request_id="req_mm_image_only",
        platform="qq",
        conversation_id="qq_dm_mm_image_only",
        user_text="[图片消息]",
        attachments=[
            {
                "attachment_id": "att_img_only",
                "type": "image",
                "local_path": str(Path(__file__)),
                "ocr_text": "hello",
                "caption": "动漫女孩在挥手",
            }
        ],
        render_target_lang="zh",
    )

    assert result["observations"]["primary_modality"] == "image"
    assert "白色马克杯" in str(result["summary_for_chat"])
    assert result["observations"]["core_observation"]


def test_multimodal_observer_emoji_only_without_question_prefers_social_reply() -> None:
    model_backend = _StaticBackend(supported_types={"emoji"}, result_factory=_model_result)
    fallback_backend = _StaticBackend(supported_types={"image", "file", "voice", "video", "emoji"}, result_factory=_fallback_result)
    observer = MultimodalObserver(model_backend=model_backend, fallback_backend=fallback_backend)

    result = observer.observe(
        request_id="req_mm_emoji_only",
        platform="qq",
        conversation_id="qq_dm_mm_emoji_only",
        user_text="[图片消息]",
        attachments=[
            {
                "attachment_id": "att_emoji_only",
                "type": "emoji",
                "local_path": str(Path(__file__)),
                "ocr_text": "hello",
                "caption": "动漫女孩在挥手",
            }
        ],
        render_target_lang="zh",
    )

    assert result["observations"]["primary_modality"] == "emoji"
    assert "轻松打趣" in str(result["summary_for_chat"])


def test_multimodal_observer_emotive_image_without_question_prefers_social_reply() -> None:
    def _emotive_image_result(payload: MultimodalObserverInput) -> dict[str, object]:
        attachment = dict(payload.attachment or {})
        return {
            "attachment_id": attachment.get("attachment_id"),
            "type": "image",
            "backend": "multimodal_model",
            "model_provider": "gemini",
            "model_id": "gemini-sync",
            "visual_summary": "一个动漫女孩哭得很委屈，眼角挂着眼泪",
            "visible_text": "",
            "scene_tags": [],
            "summary": "收到一张图片。画面里主要是：一个动漫女孩哭得很委屈，眼角挂着眼泪",
            "confidence": 0.78,
            "safety_flags": [],
            "followup_required": False,
            "artifacts": {},
        }

    model_backend = _StaticBackend(supported_types={"image"}, result_factory=_emotive_image_result)
    fallback_backend = _StaticBackend(supported_types={"image", "file", "voice", "video"}, result_factory=_fallback_result)
    observer = MultimodalObserver(model_backend=model_backend, fallback_backend=fallback_backend)

    result = observer.observe(
        request_id="req_mm_emotive_image_only",
        platform="qq",
        conversation_id="qq_dm_mm_emotive_image_only",
        user_text="[图片消息]",
        attachments=[
            {
                "attachment_id": "att_img_emotive_only",
                "type": "image",
                "local_path": str(Path(__file__)),
                "caption": "委屈地哭哭脸",
            }
        ],
        render_target_lang="zh",
    )

    assert result["observations"]["primary_modality"] == "image"
    assert "委屈" in str(result["summary_for_chat"])


def test_multimodal_observer_sync_text_file_extracts_excerpt(tmp_path: Path) -> None:
    path = tmp_path / "sample.txt"
    path.write_text("第一行\n第二行\n第三行", encoding="utf-8")
    model_backend = _StaticBackend(supported_types={"file"}, result_factory=_model_result)
    fallback_backend = _StaticBackend(supported_types={"image", "file", "voice", "video"}, result_factory=_fallback_result)
    observer = MultimodalObserver(model_backend=model_backend, fallback_backend=fallback_backend)

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
    assert result["observations"]["items"][0]["backend"] == "rule_fallback"
    assert len(model_backend.calls) == 0


def test_multimodal_observer_file_without_text_can_upgrade_to_model_backend() -> None:
    model_backend = _StaticBackend(supported_types={"file"}, result_factory=_model_result)
    fallback_backend = _StaticBackend(supported_types={"image", "file", "voice", "video"}, result_factory=_fallback_result)
    observer = MultimodalObserver(model_backend=model_backend, fallback_backend=fallback_backend)

    result = observer.observe(
        request_id="req_mm_file_preview",
        platform="qq",
        conversation_id="qq_dm_mm_file_preview",
        user_text="帮我看看这个文件",
        attachments=[
            {
                "attachment_id": "att_file_preview",
                "type": "file",
                "file_name": "scan.pdf",
                "preview_image_paths": [str(Path(__file__))],
            }
        ],
        render_target_lang="zh",
    )

    assert result["mode"] == "sync"
    assert result["observations"]["items"][0]["backend"] == "multimodal_model"
    assert len(model_backend.calls) == 1


def test_multimodal_observer_voice_keeps_parser_path_not_model_backend() -> None:
    model_backend = _StaticBackend(supported_types=set(), result_factory=_model_result)
    fallback_backend = _StaticBackend(supported_types={"image", "file", "voice", "video"}, result_factory=_fallback_result)
    observer = MultimodalObserver(model_backend=model_backend, fallback_backend=fallback_backend)

    result = observer.observe(
        request_id="req_mm_voice",
        platform="qq",
        conversation_id="qq_dm_mm_voice",
        user_text="这段语音在说什么？",
        attachments=[
            {
                "attachment_id": "att_voice_1",
                "type": "voice",
                "transcript": "这段语音在说今天晚上八点开会",
            }
        ],
        render_target_lang="zh",
    )

    assert result["mode"] == "sync"
    assert result["observations"]["items"][0]["backend"] == "rule_fallback"
    assert "今天晚上八点开会" in str(result["summary_for_chat"])
    assert len(model_backend.calls) == 0


def test_multimodal_observer_video_defaults_to_async() -> None:
    observer = MultimodalObserver(
        model_backend=_StaticBackend(supported_types={"video"}, result_factory=_model_result, supports_phase="background"),
        fallback_backend=_StaticBackend(supported_types={"image", "file", "voice", "video"}, result_factory=_fallback_result),
    )

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


def test_multimodal_observer_large_file_defaults_to_async() -> None:
    observer = MultimodalObserver(
        sync_file_limit_bytes=1024,
        model_backend=_StaticBackend(supported_types={"file"}, result_factory=_model_result),
        fallback_backend=_StaticBackend(supported_types={"image", "file", "voice", "video"}, result_factory=_fallback_result),
    )

    result = observer.observe(
        request_id="req_mm_large_file",
        platform="qq",
        conversation_id="qq_dm_mm_large_file",
        user_text="帮我看看这个文件",
        attachments=[
            {
                "attachment_id": "att_large_file",
                "type": "file",
                "file_name": "big.pdf",
                "size_bytes": 2048,
            }
        ],
        render_target_lang="zh",
    )

    assert result["mode"] == "async"
    assert result["pending_jobs"][0]["type"] == "file"


def test_multimodal_observer_background_video_uses_model_backend() -> None:
    model_backend = _StaticBackend(supported_types={"video"}, result_factory=_model_result, supports_phase="background")
    fallback_backend = _StaticBackend(supported_types={"image", "file", "voice", "video"}, result_factory=_fallback_result)
    observer = MultimodalObserver(model_backend=model_backend, fallback_backend=fallback_backend)

    result = observer.observe(
        request_id="req_mm_video_bg",
        platform="qq",
        conversation_id="qq_dm_mm_video_bg",
        user_text="你先看看这个视频",
        attachments=[
            {
                "attachment_id": "att_video_bg",
                "type": "video",
                "file_name": "demo.mp4",
                "duration_ms": 8000,
                "frame_paths": [str(Path(__file__))],
                "audio_transcript": "今晚八点开会",
            }
        ],
        render_target_lang="zh",
        processing_mode_hint="background",
    )

    assert result["mode"] == "sync"
    assert result["observations"]["items"][0]["backend"] == "multimodal_model"
    assert result["observations"]["items"][0]["audio_summary"] == "有人在说今晚八点开会"
    assert len(model_backend.calls) == 1


def test_multimodal_observer_background_video_over_limit_uses_metadata_fallback() -> None:
    model_backend = _StaticBackend(supported_types={"video"}, result_factory=_model_result, supports_phase="background")
    fallback_backend = _StaticBackend(supported_types={"image", "file", "voice", "video"}, result_factory=_fallback_result)
    observer = MultimodalObserver(
        model_backend=model_backend,
        fallback_backend=fallback_backend,
        video_max_duration_seconds=60,
    )

    result = observer.observe(
        request_id="req_mm_video_limit",
        platform="qq",
        conversation_id="qq_dm_mm_video_limit",
        user_text="你先看看这个视频",
        attachments=[
            {
                "attachment_id": "att_video_limit",
                "type": "video",
                "file_name": "long_demo.mp4",
                "duration_ms": 90000,
                "frame_paths": [str(Path(__file__))],
            }
        ],
        render_target_lang="zh",
        processing_mode_hint="background",
    )

    assert result["mode"] == "sync"
    assert result["observations"]["items"][0]["backend"] == "rule_fallback"
    assert "VIDEO_DURATION_EXCEEDS_MODEL_LIMIT" in result["observations"]["items"][0]["safety_flags"]
    assert len(model_backend.calls) == 0
