from __future__ import annotations

import base64
import hashlib
import json
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlparse

from host_brain.model_runtime.prompt_builder_utils import extract_langchain_content_text
from host_brain.model_runtime.prompt_specs import resolve_prompt_spec_mapping
from host_brain.integrations.llm_gateway import (
    PROVIDER_GEMINI,
)


_logger = logging.getLogger("Natsume.app.multimodal_observer_backends")

_IMAGE_TYPES = {"image", "emoji"}
_VOICE_TYPES = {"voice", "audio"}
_VIDEO_TYPES = {"video"}
_FILE_TYPES = {"file"}
_TEXT_FILE_SUFFIXES = {
    ".txt",
    ".md",
    ".markdown",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".csv",
    ".log",
    ".ini",
    ".cfg",
    ".conf",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".html",
    ".css",
    ".xml",
}
_SCREENSHOT_HINTS = ("截图", "screenshot", "screen", "界面", "window", "chat")
_EMOJI_TONE_HINTS: dict[str, str] = {
    "哭": "看起来像在委屈或难过",
    "笑": "整体更像轻松或开心的语气",
    "怒": "语气可能带一点不满或吐槽",
    "哈": "更像在打趣或吐槽",
}
_GREETING_HINTS = ("你好", "您好", "早上好", "晚上好", "挥手", "招手", "打招呼")
_GREETING_ENGLISH_WORDS = ("hello", "hi")
_TESSERACT_LANG = "chi_sim+jpn+eng"
_IMAGE_FILE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


def _coerce_float(value: Any, *, default: float = 0.0) -> float:
    if value is None or value == "":
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _coerce_boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value or "").strip().lower()
    if not normalized:
        return False
    if normalized in {"0", "false", "no", "off", "none", "null"}:
        return False
    return True


def _coerce_safety_flags(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        return [
            str(key).strip()
            for key, enabled in value.items()
            if str(key).strip() and _coerce_boolish(enabled)
        ]
    if isinstance(value, (list, tuple, set)):
        return [str(flag).strip() for flag in value if str(flag).strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _shorten(value: str, *, limit: int = 180) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _derive_meme_intent(*, visual_summary: str, visible_text: str, fallback_hint: str = "") -> str:
    haystack = _normalize_whitespace(" ".join([visual_summary, visible_text, fallback_hint])).lower()
    if any(token in haystack for token in _GREETING_HINTS) or _has_english_word(haystack, _GREETING_ENGLISH_WORDS):
        return "像是在打招呼，语气轻松友好"
    if any(token in haystack for token in ("委屈", "难过", "伤心", "哭", "泪", "可怜", "呜呜", "哭唧唧", "撒娇", "抱抱", "摸摸")):
        return "更像是在委屈、撒娇，想让人接一下情绪"
    if any(token in haystack for token in ("无语", "尴尬", "汗颜", "窘", "社死", "沉默", "裂开")):
        return "更像是在无语、尴尬，想让人顺着接一句"
    if any(token in haystack for token in ("怒", "生气", "火大", "不爽", "气鼓鼓", "炸毛", "服了")):
        return "更像是在吐槽或表达不满，但不是真的要吵起来"
    if any(token in haystack for token in ("笑", "哈哈", "开心", "乐", "偷笑", "坏笑", "得意", "比耶")):
        return "更像是在轻松打趣，带一点逗人的意思"
    if any(token in haystack for token in ("惊讶", "震惊", "吓", "懵", "呆住", "愣住")):
        return "更像是在惊讶或被整不会了，想让人接一下这个反应"
    return ""


def _has_english_word(text: str, words: tuple[str, ...]) -> bool:
    haystack = str(text or "").lower()
    for word in words:
        token = re.escape(str(word or "").lower())
        if token and re.search(rf"(?<![a-z0-9]){token}(?![a-z0-9])", haystack):
            return True
    return False


def _safe_read_bytes(path: Path, *, limit: int) -> bytes:
    with path.open("rb") as handle:
        return handle.read(limit)


def _guess_mime_type(file_name: str | None, local_path: str | None) -> str | None:
    candidate = str(file_name or local_path or "").strip()
    if not candidate:
        return None
    guessed, _ = mimetypes.guess_type(candidate)
    return guessed or None


def _guess_file_name(local_path: str | None, remote_url: str | None) -> str | None:
    if local_path:
        return Path(local_path).name
    if remote_url:
        parsed = urlparse(remote_url)
        name = Path(parsed.path).name
        return name or None
    return None


def _attachment_kind_from_type(raw_type: Any) -> str:
    normalized = str(raw_type or "").strip().lower()
    if normalized in _IMAGE_TYPES:
        return normalized
    if normalized in _VOICE_TYPES:
        return "voice"
    if normalized in _VIDEO_TYPES:
        return "video"
    if normalized in _FILE_TYPES:
        return "file"
    return normalized or "file"


def _default_attachment_id(item: dict[str, Any], index: int) -> str:
    source = "|".join(
        [
            str(item.get("type") or "").strip().lower(),
            str(item.get("local_path") or "").strip(),
            str(item.get("remote_url") or "").strip(),
            str(item.get("file_name") or "").strip(),
            str(index),
        ]
    )
    return hashlib.sha1(source.encode("utf-8")).hexdigest()[:16]


def _extract_json_object(raw_text: str) -> dict[str, Any] | None:
    text = str(raw_text or "").strip()
    if not text:
        return None
    candidates = [text]
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    candidates.extend(fenced)
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _mapping_text(mapping: Mapping[str, Any], key: str, *, default: str = "", **kwargs: Any) -> str:
    text = str(mapping.get(key, default) or "")
    if not kwargs:
        return text
    payload = {str(k): "" if v is None else str(v) for k, v in kwargs.items()}
    try:
        return text.format_map(payload)
    except Exception:
        return text


@dataclass
class MultimodalObserverInput:
    request_id: str
    platform: str
    conversation_id: str
    user_text: str
    attachment: dict[str, Any]
    render_target_lang: str = "zh"
    phase: str = "sync"


class MultimodalObserverBackend(Protocol):
    backend_name: str

    def supports(self, payload: MultimodalObserverInput) -> bool:
        ...

    def observe_attachment(self, payload: MultimodalObserverInput) -> dict[str, Any]:
        ...


class RuleBasedMultimodalObserverBackend:
    backend_name = "rule_fallback"

    def __init__(self, *, text_extract_limit_chars: int = 2200) -> None:
        self._text_extract_limit_chars = int(text_extract_limit_chars)

    def supports(self, payload: MultimodalObserverInput) -> bool:
        _ = payload
        return True

    def observe_attachment(self, payload: MultimodalObserverInput) -> dict[str, Any]:
        item = dict(payload.attachment or {})
        item_type = str(item.get("type") or "").strip().lower()
        if item_type in _IMAGE_TYPES:
            return self._observe_image_like(item=item, user_text=payload.user_text)
        if item_type == "voice":
            return self._observe_voice(item=item, user_text=payload.user_text)
        if item_type == "file":
            return self._observe_file(item=item)
        if item_type == "video":
            return self._observe_video(item=item)
        return {
            "attachment_id": item.get("attachment_id"),
            "type": item_type or "file",
            "backend": self.backend_name,
            "confidence": 0.1,
            "summary": "收到一个暂不支持细看的附件。",
            "safety_flags": ["UNSUPPORTED_ATTACHMENT_TYPE"],
            "followup_required": False,
            "artifacts": {},
        }

    def _observe_image_like(self, *, item: dict[str, Any], user_text: str) -> dict[str, Any]:
        file_name = str(item.get("file_name") or "").strip()
        caption = str(item.get("caption") or "").strip()
        ocr_text = str(item.get("ocr_text") or "").strip()
        if not ocr_text and item.get("local_path"):
            ocr_text = self._ocr_image(str(item["local_path"]))
        text_hint = _shorten(ocr_text, limit=160)
        is_screenshot = any(token in (file_name + " " + str(user_text or "")).lower() for token in _SCREENSHOT_HINTS)
        tone_hint = ""
        if item["type"] == "emoji":
            for keyword, hint in _EMOJI_TONE_HINTS.items():
                if keyword in text_hint:
                    tone_hint = hint
                    break
        if not tone_hint and not is_screenshot:
            tone_hint = _derive_meme_intent(
                visual_summary=caption,
                visible_text=text_hint,
                fallback_hint=file_name,
            )
        summary = "收到一张图片。"
        if item["type"] == "emoji":
            summary = "收到一个表情包。"
        elif is_screenshot:
            summary = "收到一张截图。"
        if text_hint:
            summary += f" 图里大致能读到这些字：{text_hint}"
        elif tone_hint:
            summary += f" {tone_hint}"

        return {
            "attachment_id": item["attachment_id"],
            "type": item["type"],
            "backend": self.backend_name,
            "file_name": file_name or None,
            "ocr_text": text_hint or None,
            "caption": caption or None,
            "is_screenshot": bool(is_screenshot),
            "scene_tags": (["screenshot"] if is_screenshot else []),
            "meme_intent": tone_hint or None,
            "tone_hint": tone_hint or None,
            "confidence": 0.42 if text_hint or tone_hint else 0.2,
            "summary": summary,
            "safety_flags": [],
            "followup_required": False,
            "artifacts": {},
        }

    def _observe_voice(self, *, item: dict[str, Any], user_text: str) -> dict[str, Any]:
        transcript = str(item.get("transcript") or "").strip()
        if not transcript and str(user_text or "").strip():
            transcript = str(user_text).strip()
        summary = "收到一段语音。"
        if transcript:
            summary += f" 转写内容大致是：{_shorten(transcript, limit=180)}"
        else:
            summary += " 现在还没有拿到可用的转写文本。"
        return {
            "attachment_id": item["attachment_id"],
            "type": item["type"],
            "backend": self.backend_name,
            "transcript": transcript or None,
            "confidence": 0.88 if transcript else 0.15,
            "summary": summary,
            "safety_flags": ([] if transcript else ["VOICE_TRANSCRIPT_MISSING"]),
            "followup_required": False,
            "artifacts": {},
        }

    def _observe_file(self, *, item: dict[str, Any]) -> dict[str, Any]:
        local_path = str(item.get("local_path") or "").strip()
        file_name = str(item.get("file_name") or "").strip() or (Path(local_path).name if local_path else "")
        suffix = Path(file_name).suffix.lower()
        summary = f"收到一个文件：{file_name or '未命名文件'}。"
        extracted_text = str(item.get("parsed_text") or "").strip()
        page_summaries = [str(part).strip() for part in list(item.get("page_summaries") or []) if str(part).strip()]
        safety_flags: list[str] = []

        if not extracted_text and page_summaries:
            extracted_text = "\n".join(page_summaries[:3]).strip()

        if not extracted_text:
            if local_path:
                path = Path(local_path)
                if path.exists():
                    if suffix in _TEXT_FILE_SUFFIXES:
                        extracted_text = self._read_text_file(path)
                    elif suffix == ".pdf":
                        extracted_text = self._read_pdf(path)
                    elif suffix == ".docx":
                        extracted_text = self._read_docx(path)
                    else:
                        safety_flags.append("FILE_METADATA_ONLY")
                else:
                    safety_flags.append("FILE_LOCAL_PATH_MISSING")
            else:
                safety_flags.append("FILE_LOCAL_PATH_MISSING")

        if extracted_text:
            summary += f" 我先读到的内容是：{_shorten(extracted_text, limit=200)}"
        elif "FILE_METADATA_ONLY" in safety_flags:
            summary += " 目前只能先看到文件元数据，还没拿到可读正文。"
        elif "FILE_LOCAL_PATH_MISSING" in safety_flags:
            summary += " 这边暂时还拿不到文件正文。"

        return {
            "attachment_id": item["attachment_id"],
            "type": item["type"],
            "backend": self.backend_name,
            "file_name": file_name or None,
            "mime_type": item.get("mime_type"),
            "text_excerpt": extracted_text or None,
            "page_summaries": page_summaries or None,
            "confidence": 0.75 if extracted_text else 0.24,
            "summary": summary,
            "safety_flags": safety_flags,
            "followup_required": bool(not extracted_text and "FILE_METADATA_ONLY" in safety_flags),
            "artifacts": {},
        }

    def _observe_video(self, *, item: dict[str, Any]) -> dict[str, Any]:
        file_name = str(item.get("file_name") or "").strip()
        duration_ms = item.get("duration_ms")
        summary = "收到一个视频。"
        if file_name:
            summary += f" 文件名是 {file_name}。"
        if duration_ms:
            summary += f" 时长大约 {round(float(duration_ms) / 1000.0, 1)} 秒。"
        summary += " 这类内容默认会走后台整理。"
        return {
            "attachment_id": item["attachment_id"],
            "type": item["type"],
            "backend": self.backend_name,
            "file_name": file_name or None,
            "duration_ms": duration_ms,
            "confidence": 0.12,
            "summary": summary,
            "safety_flags": ["ASYNC_VIDEO_DEFAULT"],
            "followup_required": True,
            "artifacts": {},
        }

    def _ocr_image(self, local_path: str) -> str:
        try:
            from PIL import Image  # noqa: PLC0415
        except Exception:
            return ""
        try:
            import pytesseract  # noqa: PLC0415
        except Exception:
            return ""
        try:
            path = Path(local_path)
            if not path.exists():
                return ""
            with Image.open(path) as image:
                text = pytesseract.image_to_string(image, lang=_TESSERACT_LANG)
            return _shorten(re.sub(r"\s+", " ", text), limit=240)
        except Exception as exc:
            _logger.debug("multimodal image ocr failed path=%s err=%s", local_path, exc)
            return ""

    def _read_text_file(self, path: Path) -> str:
        try:
            data = _safe_read_bytes(path, limit=self._text_extract_limit_chars * 4)
        except Exception as exc:
            _logger.debug("multimodal read text file failed path=%s err=%s", path, exc)
            return ""
        for encoding in ("utf-8", "utf-8-sig", "gb18030", "gbk", "shift_jis"):
            try:
                text = data.decode(encoding, errors="strict")
                return _shorten(text, limit=self._text_extract_limit_chars)
            except Exception:
                continue
        return _shorten(data.decode("utf-8", errors="ignore"), limit=self._text_extract_limit_chars)

    def _read_pdf(self, path: Path) -> str:
        try:
            from pypdf import PdfReader  # noqa: PLC0415
        except Exception:
            try:
                from PyPDF2 import PdfReader  # type: ignore[no-redef]  # noqa: PLC0415
            except Exception:
                return ""
        try:
            reader = PdfReader(str(path))
            parts: list[str] = []
            for page in list(reader.pages)[:3]:
                parts.append(str(page.extract_text() or "").strip())
            return _shorten("\n".join(part for part in parts if part), limit=self._text_extract_limit_chars)
        except Exception as exc:
            _logger.debug("multimodal read pdf failed path=%s err=%s", path, exc)
            return ""

    def _read_docx(self, path: Path) -> str:
        try:
            from docx import Document  # noqa: PLC0415
        except Exception:
            return ""
        try:
            document = Document(str(path))
            text = "\n".join(str(paragraph.text or "").strip() for paragraph in document.paragraphs[:30])
            return _shorten(text, limit=self._text_extract_limit_chars)
        except Exception as exc:
            _logger.debug("multimodal read docx failed path=%s err=%s", path, exc)
            return ""


class OpenAICompatMultimodalBackend:
    backend_name = "multimodal_model"

    def __init__(
        self,
        *,
        provider_name: str,
        sync_model_id: str,
        async_model_id: str,
        timeout_ms: int = 15000,
        image_max_bytes: int = 4 * 1024 * 1024,
        max_video_frames: int = 4,
        model_attempt_service_provider: Callable[[], Any] | None = None,
    ) -> None:
        self._provider_name = str(provider_name or "").strip().lower() or PROVIDER_GEMINI
        self._sync_model_id = str(sync_model_id or "").strip()
        self._async_model_id = str(async_model_id or "").strip() or self._sync_model_id
        self._timeout_ms = max(3000, int(timeout_ms))
        self._image_max_bytes = max(64 * 1024, int(image_max_bytes))
        self._max_video_frames = max(1, int(max_video_frames))
        self._model_attempt_service_provider = model_attempt_service_provider
        self._templates = resolve_prompt_spec_mapping(
            "multimodal_observer.model_prompt.templates"
        )

    @property
    def available(self) -> bool:
        return bool(self._sync_model_id or self._async_model_id)

    def supports(self, payload: MultimodalObserverInput) -> bool:
        if not self.available:
            return False
        attachment = dict(payload.attachment or {})
        item_type = str(attachment.get("type") or "").strip().lower()
        if item_type in _IMAGE_TYPES:
            return bool(self._build_image_content_parts(attachment))
        if item_type == "video" and payload.phase == "background":
            return True
        if item_type == "file":
            if self._looks_like_image_file(attachment):
                return True
            preview_paths = list(attachment.get("preview_image_paths") or [])
            return bool(preview_paths)
        return False

    def observe_attachment(self, payload: MultimodalObserverInput) -> dict[str, Any]:
        attachment = dict(payload.attachment or {})
        item_type = str(attachment.get("type") or "").strip().lower()
        if item_type in _IMAGE_TYPES or (item_type == "file" and self._looks_like_image_file(attachment)):
            return self._observe_image_like(payload)
        if item_type == "video":
            return self._observe_video(payload)
        if item_type == "file":
            return self._observe_file_preview(payload)
        raise ValueError(f"unsupported multimodal model attachment type: {item_type}")

    def _observe_image_like(self, payload: MultimodalObserverInput) -> dict[str, Any]:
        attachment = dict(payload.attachment or {})
        file_name = str(attachment.get("file_name") or "").strip()
        is_emoji = str(attachment.get("type") or "").strip().lower() == "emoji"
        is_screenshot = any(token in (file_name + " " + str(payload.user_text or "")).lower() for token in _SCREENSHOT_HINTS)
        content_parts = self._build_image_content_parts(attachment)
        if not content_parts:
            raise RuntimeError("no image payload available for multimodal model")
        prompt_text = _mapping_text(
            self._templates,
            "image_user_prompt",
            user_text=str(payload.user_text or "").strip() or "请看看这个附件。",
            attachment_type=("emoji" if is_emoji else "image"),
            file_name=file_name or "UNKNOWN",
            caption=str(attachment.get("caption") or "").strip() or "NONE",
            ocr_text=str(attachment.get("ocr_text") or "").strip() or "NONE",
            screenshot_hint=("yes" if is_screenshot else "no"),
        )
        messages = [
            {"role": "system", "content": _mapping_text(self._templates, "image_system_prompt")},
            {"role": "user", "content": [*content_parts, {"type": "text", "text": prompt_text}]},
        ]
        raw_text, model_trace = self._invoke_model(messages=messages)
        parsed = _extract_json_object(raw_text)
        if not parsed:
            raise RuntimeError("multimodal image backend returned non-json content")
        visual_summary = _shorten(str(parsed.get("visual_summary") or "").strip(), limit=180)
        visible_text = _shorten(str(parsed.get("visible_text") or "").strip(), limit=200)
        meme_intent = _shorten(str(parsed.get("meme_intent") or "").strip(), limit=120)
        if not meme_intent and not is_screenshot:
            meme_intent = _derive_meme_intent(
                visual_summary=visual_summary,
                visible_text=visible_text,
                fallback_hint=str(attachment.get("caption") or ""),
            )
        scene_tags = [str(item).strip() for item in list(parsed.get("scene_tags") or []) if str(item).strip()]
        confidence = max(0.0, min(1.0, _coerce_float(parsed.get("confidence"), default=0.72)))

        summary = "收到一张图片。"
        if is_emoji:
            summary = "收到一个表情包。"
        elif is_screenshot:
            summary = "收到一张截图。"
        if visual_summary:
            summary += f" 画面里主要是：{visual_summary}"
        if visible_text:
            summary += f" 图中文字大致是：{visible_text}"
        if is_emoji and meme_intent:
            summary += f" 这个表情包更像是在表达：{meme_intent}"

        return {
            "attachment_id": attachment.get("attachment_id"),
            "type": attachment.get("type"),
            "backend": self.backend_name,
            "model_provider": model_trace["final_provider"],
            "model_id": model_trace["final_model"],
            "file_name": file_name or None,
            "visual_summary": visual_summary or None,
            "visible_text": visible_text or None,
            "meme_intent": meme_intent or None,
            "scene_tags": scene_tags,
            "is_screenshot": bool(is_screenshot),
            "confidence": confidence,
            "summary": summary,
            "safety_flags": _coerce_safety_flags(parsed.get("safety_flags")),
            "followup_required": bool(parsed.get("followup_required", False)),
            "artifacts": {"raw_model_output": raw_text[:1200], "model_attempt": model_trace},
        }

    def _observe_video(self, payload: MultimodalObserverInput) -> dict[str, Any]:
        attachment = dict(payload.attachment or {})
        transcript = str(
            attachment.get("audio_transcript")
            or attachment.get("transcript")
            or (dict(attachment.get("extra") or {}).get("audio_transcript"))
            or ""
        ).strip()
        frame_paths = [str(path).strip() for path in list(attachment.get("frame_paths") or []) if str(path).strip()]
        cleanup_dir: str | None = None
        if not frame_paths:
            frame_paths, cleanup_dir = self._extract_video_frames(attachment)

        try:
            content_parts: list[dict[str, Any]] = []
            for path in frame_paths[: self._max_video_frames]:
                content_parts.extend(self._build_image_parts_from_path(path))
            prompt_text = _mapping_text(
                self._templates,
                "video_user_prompt",
                user_text=str(payload.user_text or "").strip() or "请先看看这个视频。",
                file_name=str(attachment.get("file_name") or "").strip() or "UNKNOWN",
                transcript=(transcript or "NONE"),
                frame_count=len(frame_paths),
            )
            if not content_parts and not transcript:
                raise RuntimeError("no usable frame or transcript available for video understanding")

            messages = [
                {"role": "system", "content": _mapping_text(self._templates, "video_system_prompt")},
                {"role": "user", "content": [*content_parts, {"type": "text", "text": prompt_text}]},
            ]
            raw_text, model_trace = self._invoke_model(messages=messages)
            parsed = _extract_json_object(raw_text)
            if not parsed:
                raise RuntimeError("multimodal video backend returned non-json content")

            visual_summary = _shorten(str(parsed.get("visual_summary") or "").strip(), limit=220)
            audio_summary = _shorten(str(parsed.get("audio_summary") or "").strip(), limit=180)
            visible_text = _shorten(str(parsed.get("visible_text") or "").strip(), limit=200)
            scene_tags = [str(item).strip() for item in list(parsed.get("scene_tags") or []) if str(item).strip()]
            confidence = max(0.0, min(1.0, _coerce_float(parsed.get("confidence"), default=0.68)))
            summary = "收到一个视频。"
            if visual_summary:
                summary += f" 画面大致是：{visual_summary}"
            if audio_summary:
                summary += f" 音频大致在说：{audio_summary}"
            elif transcript:
                summary += f" 音轨转写大致是：{_shorten(transcript, limit=160)}"
            if visible_text:
                summary += f" 画面里能读到这些字：{visible_text}"

            return {
                "attachment_id": attachment.get("attachment_id"),
                "type": attachment.get("type"),
                "backend": self.backend_name,
                "model_provider": model_trace["final_provider"],
                "model_id": model_trace["final_model"],
                "file_name": str(attachment.get("file_name") or "").strip() or None,
                "visual_summary": visual_summary or None,
                "audio_summary": audio_summary or (transcript or None),
                "visible_text": visible_text or None,
                "scene_tags": scene_tags,
                "confidence": confidence,
                "summary": summary,
                "safety_flags": _coerce_safety_flags(parsed.get("safety_flags")),
                "followup_required": bool(parsed.get("followup_required", False)),
                "artifacts": {
                    "frame_count": len(frame_paths),
                    "raw_model_output": raw_text[:1200],
                    "model_attempt": model_trace,
                },
            }
        finally:
            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)

    def _observe_file_preview(self, payload: MultimodalObserverInput) -> dict[str, Any]:
        attachment = dict(payload.attachment or {})
        preview_paths = [str(path).strip() for path in list(attachment.get("preview_image_paths") or []) if str(path).strip()]
        if not preview_paths and self._looks_like_image_file(attachment):
            preview_paths = [str(attachment.get("local_path") or "").strip()]
        content_parts: list[dict[str, Any]] = []
        for path in preview_paths[:2]:
            content_parts.extend(self._build_image_parts_from_path(path))
        if not content_parts:
            raise RuntimeError("file preview model backend missing preview images")
        prompt_text = _mapping_text(
            self._templates,
            "image_user_prompt",
            user_text=str(payload.user_text or "").strip() or "请看看这个附件。",
            attachment_type="file",
            file_name=str(attachment.get("file_name") or "").strip() or "UNKNOWN",
            caption="NONE",
            ocr_text=str(attachment.get("parsed_text") or "").strip() or "NONE",
            screenshot_hint="no",
        )
        messages = [
            {"role": "system", "content": _mapping_text(self._templates, "image_system_prompt")},
            {"role": "user", "content": [*content_parts, {"type": "text", "text": prompt_text}]},
        ]
        raw_text, model_trace = self._invoke_model(messages=messages)
        parsed = _extract_json_object(raw_text)
        if not parsed:
            raise RuntimeError("multimodal file backend returned non-json content")
        visual_summary = _shorten(str(parsed.get("visual_summary") or "").strip(), limit=180)
        visible_text = _shorten(str(parsed.get("visible_text") or "").strip(), limit=200)
        confidence = max(0.0, min(1.0, _coerce_float(parsed.get("confidence"), default=0.66)))
        summary = f"收到一个文件：{str(attachment.get('file_name') or '').strip() or '未命名文件'}。"
        if visual_summary:
            summary += f" 页面上主要是：{visual_summary}"
        if visible_text:
            summary += f" 能读到的文字大致是：{visible_text}"
        return {
            "attachment_id": attachment.get("attachment_id"),
            "type": attachment.get("type"),
            "backend": self.backend_name,
            "model_provider": model_trace["final_provider"],
            "model_id": model_trace["final_model"],
            "file_name": str(attachment.get("file_name") or "").strip() or None,
            "visual_summary": visual_summary or None,
            "visible_text": visible_text or None,
            "scene_tags": [str(item).strip() for item in list(parsed.get("scene_tags") or []) if str(item).strip()],
            "confidence": confidence,
            "summary": summary,
            "safety_flags": _coerce_safety_flags(parsed.get("safety_flags")),
            "followup_required": bool(parsed.get("followup_required", False)),
            "artifacts": {"raw_model_output": raw_text[:1200], "model_attempt": model_trace},
        }

    def _invoke_model(self, *, messages: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
        from host_brain.model_runtime.invocation import complete_compiled_prompt_sync  # noqa: PLC0415
        from host_brain.model_runtime.live_prompts import MULTIMODAL_OBSERVER_PROMPT_ID  # noqa: PLC0415
        from host_brain.model_runtime.trace import model_attempt_result_trace  # noqa: PLC0415

        if self._model_attempt_service_provider is None:
            raise RuntimeError("MULTIMODAL_MODEL_ATTEMPT_SERVICE_NOT_COMPOSED")
        attempt_service = self._model_attempt_service_provider()
        if attempt_service is None:
            raise RuntimeError("MULTIMODAL_MODEL_ATTEMPT_SERVICE_UNAVAILABLE")
        result = complete_compiled_prompt_sync(
            attempt_service=attempt_service,
            prompt_id=MULTIMODAL_OBSERVER_PROMPT_ID,
            request_id=f"multimodal::{self.backend_name}::{int(time.time() * 1000)}",
            scene_type="MULTIMODAL",
            cognitive_tier="B",
            deadline_ms=self._timeout_ms,
            messages=messages,
        )
        if not result.success:
            raise RuntimeError(result.error_code or "MODEL_ATTEMPT_FAILED")
        return (
            str(extract_langchain_content_text(result.content) or "").strip(),
            model_attempt_result_trace(result),
        )

    def _build_image_content_parts(self, attachment: Mapping[str, Any]) -> list[dict[str, Any]]:
        local_path = str(attachment.get("local_path") or "").strip()
        if local_path:
            return self._build_image_parts_from_path(local_path)
        remote_url = str(attachment.get("remote_url") or "").strip()
        if remote_url:
            return self._build_image_parts_from_remote_url(remote_url)
        return []

    def _build_image_parts_from_path(self, local_path: str) -> list[dict[str, Any]]:
        path = Path(str(local_path or "").strip())
        if not path.exists() or not path.is_file():
            return []
        stat = path.stat()
        if stat.st_size <= 0 or stat.st_size > self._image_max_bytes:
            return []
        mime_type = _guess_mime_type(path.name, str(path)) or "image/png"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return [{"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}}]

    def _build_image_parts_from_remote_url(self, remote_url: str) -> list[dict[str, Any]]:
        url = str(remote_url or "").strip()
        if not url:
            return []
        if url.startswith("data:"):
            return [{"type": "image_url", "image_url": {"url": url}}]
        if not url.startswith(("http://", "https://")):
            return []

        fetched = self._fetch_remote_image_as_data_url(url)
        if fetched:
            return [{"type": "image_url", "image_url": {"url": fetched}}]
        if _coerce_boolish(os.environ.get("HOST_MULTIMODAL_ALLOW_REMOTE_URL_FALLBACK")):
            return [{"type": "image_url", "image_url": {"url": url}}]
        _logger.debug("multimodal remote image url fallback disabled url=%s", _shorten(url, limit=160))
        return []

    def _fetch_remote_image_as_data_url(self, remote_url: str) -> str:
        timeout_s = max(1.0, _coerce_float(os.environ.get("HOST_MULTIMODAL_REMOTE_FETCH_TIMEOUT_MS"), default=3000.0) / 1000.0)
        request = urllib.request.Request(
            str(remote_url),
            headers={"User-Agent": "NatsumeAi-MultimodalObserver/1.0"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - URL comes from trusted gateway payload
                content_type = str(response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
                data = response.read(self._image_max_bytes + 1)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            _logger.debug("multimodal remote image fetch skipped url=%s err=%s", _shorten(remote_url, limit=160), exc)
            return ""
        if not data or len(data) > self._image_max_bytes:
            return ""
        if content_type and not content_type.startswith("image/"):
            return ""
        mime_type = content_type or (_guess_mime_type("", remote_url) or "image/jpeg")
        if not str(mime_type or "").lower().startswith("image/"):
            return ""
        encoded = base64.b64encode(data).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    def _looks_like_image_file(self, attachment: Mapping[str, Any]) -> bool:
        mime_type = str(attachment.get("mime_type") or "").strip().lower()
        if mime_type.startswith("image/"):
            return True
        file_name = str(attachment.get("file_name") or attachment.get("local_path") or "").strip()
        return Path(file_name).suffix.lower() in _IMAGE_FILE_SUFFIXES

    def _extract_video_frames(self, attachment: Mapping[str, Any]) -> tuple[list[str], str | None]:
        local_path = str(attachment.get("local_path") or "").strip()
        if not local_path:
            return [], None
        source_path = Path(local_path)
        if not source_path.exists():
            return [], None
        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            return [], None
        duration_ms = _coerce_float(attachment.get("duration_ms"), default=0.0)
        duration_s = max(1.0, duration_ms / 1000.0) if duration_ms > 0 else 8.0
        fps = min(1.0, max(float(self._max_video_frames) / duration_s, 0.2))
        tmpdir = tempfile.mkdtemp(prefix="natsume_mm_video_")
        output_pattern = str(Path(tmpdir) / "frame_%02d.jpg")
        cmd = [
            ffmpeg_path,
            "-y",
            "-i",
            str(source_path),
            "-vf",
            f"fps={fps}",
            "-frames:v",
            str(self._max_video_frames),
            output_pattern,
        ]
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=max(10, int(duration_s * 2)),
                check=False,
            )
        except Exception as exc:
            _logger.debug("video frame extraction failed path=%s err=%s", source_path, exc)
            shutil.rmtree(tmpdir, ignore_errors=True)
            return [], None
        if completed.returncode != 0:
            _logger.debug(
                "video frame extraction returned non-zero code path=%s code=%s stderr=%s",
                source_path,
                completed.returncode,
                _shorten(completed.stderr, limit=240),
            )
            shutil.rmtree(tmpdir, ignore_errors=True)
            return [], None
        frames = [str(path) for path in sorted(Path(tmpdir).glob("frame_*.jpg")) if path.exists()]
        if not frames:
            shutil.rmtree(tmpdir, ignore_errors=True)
            return [], None
        return frames, tmpdir


__all__ = [
    "MultimodalObserverBackend",
    "MultimodalObserverInput",
    "OpenAICompatMultimodalBackend",
    "RuleBasedMultimodalObserverBackend",
    "_attachment_kind_from_type",
    "_default_attachment_id",
    "_guess_file_name",
    "_guess_mime_type",
]
