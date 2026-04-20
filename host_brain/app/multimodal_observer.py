from __future__ import annotations

import hashlib
import logging
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


_logger = logging.getLogger("Natsume.app.multimodal_observer")

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
_DOCUMENT_SUFFIXES = {".pdf", ".docx"}
_SCREENSHOT_HINTS = ("截图", "screenshot", "screen", "界面", "window", "chat")
_EMOJI_TONE_HINTS: dict[str, str] = {
    "哭": "看起来像在委屈或难过",
    "笑": "整体更像轻松或开心的语气",
    "怒": "语气可能带一点不满或吐槽",
    "哈": "更像在打趣或吐槽",
}
_TESSERACT_LANG = "chi_sim+jpn+eng"


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _shorten(value: str, *, limit: int = 180) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


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


def _safe_read_bytes(path: Path, *, limit: int) -> bytes:
    with path.open("rb") as handle:
        return handle.read(limit)


class MultimodalObserver:
    """Gateway 附件观察层。

    只负责把附件变成结构化观察结果，不直接产出最终人格回复。
    """

    def __init__(
        self,
        *,
        sync_file_limit_bytes: int = 4 * 1024 * 1024,
        max_sync_attachments: int = 3,
        text_extract_limit_chars: int = 2200,
    ) -> None:
        self._sync_file_limit_bytes = int(sync_file_limit_bytes)
        self._max_sync_attachments = int(max_sync_attachments)
        self._text_extract_limit_chars = int(text_extract_limit_chars)

    def observe(
        self,
        *,
        request_id: str,
        platform: str,
        conversation_id: str,
        user_text: str,
        attachments: list[dict[str, Any]] | None,
        render_target_lang: str = "zh",
        processing_mode_hint: str | None = None,
    ) -> dict[str, Any]:
        normalized_attachments = self._normalize_attachments(list(attachments or []))
        hint = str(processing_mode_hint or "").strip().lower()
        if not normalized_attachments:
            return {
                "mode": "sync",
                "observations": {
                    "request_id": request_id,
                    "platform": platform,
                    "conversation_id": conversation_id,
                    "items": [],
                    "attachment_count": 0,
                    "observed_types": [],
                    "summary_for_chat": "",
                    "safety_flags": [],
                },
                "pending_jobs": [],
                "summary_for_chat": "",
                "safety_flags": [],
            }

        mode = self._decide_mode(normalized_attachments, processing_mode_hint=hint)
        if hint == "background":
            mode = "sync"

        if mode == "async":
            pending = [
                {
                    "attachment_id": item["attachment_id"],
                    "type": item["type"],
                    "file_name": item.get("file_name"),
                    "size_bytes": item.get("size_bytes"),
                }
                for item in normalized_attachments
            ]
            summary_for_chat = self._build_pending_summary(
                attachments=normalized_attachments,
                user_text=user_text,
                render_target_lang=render_target_lang,
            )
            observations = {
                "request_id": request_id,
                "platform": platform,
                "conversation_id": conversation_id,
                "items": [],
                "attachment_count": len(normalized_attachments),
                "observed_types": [item["type"] for item in normalized_attachments],
                "summary_for_chat": summary_for_chat,
                "safety_flags": ["ASYNC_PROCESSING_REQUIRED"],
            }
            return {
                "mode": "async",
                "observations": observations,
                "pending_jobs": pending,
                "summary_for_chat": summary_for_chat,
                "safety_flags": ["ASYNC_PROCESSING_REQUIRED"],
            }

        item_observations: list[dict[str, Any]] = []
        safety_flags: list[str] = []
        summary_parts: list[str] = []
        for item in normalized_attachments:
            observed = self._observe_attachment(item=item, user_text=user_text)
            item_observations.append(observed)
            summary = str(observed.get("summary") or "").strip()
            if summary:
                summary_parts.append(summary)
            safety_flags.extend(list(observed.get("safety_flags") or []))

        summary_for_chat = "；".join(part for part in summary_parts if part)
        observations = {
            "request_id": request_id,
            "platform": platform,
            "conversation_id": conversation_id,
            "items": item_observations,
            "attachment_count": len(normalized_attachments),
            "observed_types": [item["type"] for item in normalized_attachments],
            "summary_for_chat": summary_for_chat,
            "safety_flags": list(dict.fromkeys(safety_flags)),
        }
        return {
            "mode": "sync",
            "observations": observations,
            "pending_jobs": [],
            "summary_for_chat": summary_for_chat,
            "safety_flags": list(dict.fromkeys(safety_flags)),
        }

    def _normalize_attachments(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for index, raw in enumerate(items):
            payload = dict(raw or {})
            local_path = str(payload.get("local_path") or "").strip() or None
            remote_url = str(payload.get("remote_url") or "").strip() or None
            file_name = str(payload.get("file_name") or "").strip() or _guess_file_name(local_path, remote_url)
            attachment = {
                "attachment_id": str(payload.get("attachment_id") or "").strip() or _default_attachment_id(payload, index),
                "type": _attachment_kind_from_type(payload.get("type")),
                "mime_type": str(payload.get("mime_type") or "").strip() or _guess_mime_type(file_name, local_path),
                "local_path": local_path,
                "remote_url": remote_url,
                "file_name": file_name,
                "size_bytes": _coerce_int(payload.get("size_bytes")),
                "duration_ms": _coerce_int(payload.get("duration_ms")),
                "width": _coerce_int(payload.get("width")),
                "height": _coerce_int(payload.get("height")),
                "sha256": str(payload.get("sha256") or "").strip() or None,
                "caption": str(payload.get("caption") or "").strip() or None,
                "ocr_text": str(payload.get("ocr_text") or "").strip() or None,
                "transcript": str(payload.get("transcript") or "").strip() or None,
                "extra": dict(payload.get("extra") or {}),
            }
            normalized.append(attachment)
        return normalized

    def _decide_mode(self, attachments: list[dict[str, Any]], *, processing_mode_hint: str) -> str:
        if processing_mode_hint == "sync":
            return "sync"
        if processing_mode_hint == "async":
            return "async"
        if len(attachments) > self._max_sync_attachments:
            return "async"
        for item in attachments:
            if item["type"] in _VIDEO_TYPES:
                return "async"
            size_bytes = int(item.get("size_bytes") or 0)
            if size_bytes > self._sync_file_limit_bytes:
                return "async"
        return "sync"

    def _observe_attachment(self, *, item: dict[str, Any], user_text: str) -> dict[str, Any]:
        item_type = item["type"]
        if item_type in _IMAGE_TYPES:
            return self._observe_image_like(item=item, user_text=user_text)
        if item_type == "voice":
            return self._observe_voice(item=item, user_text=user_text)
        if item_type == "file":
            return self._observe_file(item=item)
        if item_type == "video":
            return self._observe_video(item=item)
        return {
            "attachment_id": item["attachment_id"],
            "type": item_type,
            "summary": "收到一个暂不支持细看的附件。",
            "safety_flags": ["UNSUPPORTED_ATTACHMENT_TYPE"],
        }

    def _observe_image_like(self, *, item: dict[str, Any], user_text: str) -> dict[str, Any]:
        file_name = str(item.get("file_name") or "").strip()
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
            "file_name": file_name or None,
            "ocr_text": text_hint or None,
            "caption": str(item.get("caption") or "").strip() or None,
            "is_screenshot": bool(is_screenshot),
            "tone_hint": tone_hint or None,
            "summary": summary,
            "safety_flags": [],
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
            "transcript": transcript or None,
            "summary": summary,
            "safety_flags": ([] if transcript else ["VOICE_TRANSCRIPT_MISSING"]),
        }

    def _observe_file(self, *, item: dict[str, Any]) -> dict[str, Any]:
        local_path = str(item.get("local_path") or "").strip()
        file_name = str(item.get("file_name") or "").strip() or (Path(local_path).name if local_path else "")
        suffix = Path(file_name).suffix.lower()
        summary = f"收到一个文件：{file_name or '未命名文件'}。"
        extracted_text = ""
        safety_flags: list[str] = []

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
            "file_name": file_name or None,
            "mime_type": item.get("mime_type"),
            "text_excerpt": extracted_text or None,
            "summary": summary,
            "safety_flags": safety_flags,
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
            "file_name": file_name or None,
            "duration_ms": duration_ms,
            "summary": summary,
            "safety_flags": ["ASYNC_VIDEO_DEFAULT"],
        }

    def _build_pending_summary(
        self,
        *,
        attachments: list[dict[str, Any]],
        user_text: str,
        render_target_lang: str,
    ) -> str:
        _ = render_target_lang
        attachment_types = [item["type"] for item in attachments]
        if any(item_type == "video" for item_type in attachment_types):
            return "我先看看这个视频，整理好再和你说。"
        if any(item_type == "file" for item_type in attachment_types):
            return "我先把你发来的文件过一遍，整理好再和你说。"
        if str(user_text or "").strip():
            return "我先看看你发来的内容，整理一下再接着说。"
        return "我先看看你发来的附件，整理一下再接着说。"

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


__all__ = ["MultimodalObserver"]
