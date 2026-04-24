from __future__ import annotations

import logging
import os
from typing import Any, Sequence

from host_brain.app.multimodal_observer_backends import (
    MultimodalObserverBackend,
    MultimodalObserverInput,
    OpenAICompatMultimodalBackend,
    RuleBasedMultimodalObserverBackend,
    _attachment_kind_from_type,
    _coerce_int,
    _default_attachment_id,
    _guess_file_name,
    _guess_mime_type,
)


_logger = logging.getLogger("Natsume.app.multimodal_observer")


def _parse_int_env(name: str, *, default: int, minimum: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    try:
        parsed = int(raw) if raw else int(default)
    except (TypeError, ValueError):
        parsed = int(default)
    return max(int(minimum), parsed)


def _parse_bool_env(name: str, *, default: bool) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


class MultimodalObserver:
    """Gateway 附件观察层。

    负责：
    - 统一附件归一化
    - 同步/异步决策
    - 多模态模型后端与规则 fallback 的编排
    - 输出主聊天链可消费的结构化观察结果
    """

    def __init__(
        self,
        *,
        sync_file_limit_bytes: int | None = None,
        max_sync_attachments: int = 3,
        text_extract_limit_chars: int = 2200,
        video_max_duration_seconds: int | None = None,
        model_backend: MultimodalObserverBackend | None = None,
        fallback_backend: MultimodalObserverBackend | None = None,
    ) -> None:
        if sync_file_limit_bytes is None:
            sync_file_limit_bytes = _parse_int_env(
                "HOST_MULTIMODAL_FILE_SYNC_MAX_BYTES",
                default=4 * 1024 * 1024,
                minimum=64 * 1024,
            )
        if video_max_duration_seconds is None:
            video_max_duration_seconds = _parse_int_env(
                "HOST_MULTIMODAL_VIDEO_MAX_DURATION_SECONDS",
                default=120,
                minimum=10,
            )
        self._sync_file_limit_bytes = int(sync_file_limit_bytes)
        self._max_sync_attachments = int(max_sync_attachments)
        self._text_extract_limit_chars = int(text_extract_limit_chars)
        self._video_max_duration_seconds = int(video_max_duration_seconds)
        self._fallback_backend = fallback_backend or RuleBasedMultimodalObserverBackend(
            text_extract_limit_chars=text_extract_limit_chars
        )
        self._model_backend = model_backend or OpenAICompatMultimodalBackend(
            provider_name=str(os.environ.get("HOST_MULTIMODAL_PROVIDER") or "gemini").strip().lower() or "gemini",
            sync_model_id=(
                str(os.environ.get("HOST_MULTIMODAL_MODEL_ID_SYNC") or os.environ.get("HOST_GEMINI_MODEL_ID") or "gemini-2.5-flash-lite").strip()
                or "gemini-2.5-flash-lite"
            ),
            async_model_id=(
                str(os.environ.get("HOST_MULTIMODAL_MODEL_ID_ASYNC") or os.environ.get("HOST_CHAT_AGENT_MODEL_ID") or os.environ.get("HOST_GEMINI_MODEL_ID") or "gemini-2.5-pro").strip()
                or "gemini-2.5-pro"
            ),
            timeout_ms=_parse_int_env("HOST_MULTIMODAL_TIMEOUT_MS", default=15000, minimum=3000),
            image_max_bytes=_parse_int_env("HOST_MULTIMODAL_IMAGE_MAX_BYTES", default=4 * 1024 * 1024, minimum=64 * 1024),
            max_video_frames=_parse_int_env("HOST_MULTIMODAL_VIDEO_MAX_FRAMES", default=4, minimum=1),
        )

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
                    "confidence": 0.0,
                    "followup_required": False,
                    "artifacts": {},
                    "primary_modality": "text",
                    "modalities": ["text"],
                    "emotion_signals": [],
                },
                "pending_jobs": [],
                "summary_for_chat": "",
                "safety_flags": [],
                "confidence": 0.0,
                "followup_required": False,
                "artifacts": {},
            }

        mode = self._decide_mode(normalized_attachments, processing_mode_hint=hint)
        phase = "background" if hint == "background" else "sync"
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
            observed_types = [item["type"] for item in normalized_attachments]
            observations = {
                "request_id": request_id,
                "platform": platform,
                "conversation_id": conversation_id,
                "items": [],
                "attachment_count": len(normalized_attachments),
                "observed_types": observed_types,
                "summary_for_chat": summary_for_chat,
                "safety_flags": ["ASYNC_PROCESSING_REQUIRED"],
                "confidence": 0.0,
                "followup_required": True,
                "artifacts": {"pending_jobs": pending},
                "primary_modality": observed_types[0] if observed_types else "text",
                "modalities": observed_types or ["text"],
                "emotion_signals": [],
            }
            return {
                "mode": "async",
                "observations": observations,
                "pending_jobs": pending,
                "summary_for_chat": summary_for_chat,
                "safety_flags": ["ASYNC_PROCESSING_REQUIRED"],
                "confidence": 0.0,
                "followup_required": True,
                "artifacts": {"pending_jobs": pending},
            }

        item_observations: list[dict[str, Any]] = []
        safety_flags: list[str] = []
        summary_parts: list[str] = []
        confidence_values: list[float] = []
        followup_required = False
        artifacts: dict[str, Any] = {"backend_usage": []}
        emotion_signals: list[str] = []

        for item in normalized_attachments:
            observed = self._observe_attachment_with_backends(
                request_id=request_id,
                platform=platform,
                conversation_id=conversation_id,
                user_text=user_text,
                item=item,
                render_target_lang=render_target_lang,
                phase=phase,
            )
            item_observations.append(observed)
            summary = str(observed.get("summary") or "").strip()
            if summary:
                summary_parts.append(summary)
            safety_flags.extend(list(observed.get("safety_flags") or []))
            confidence_values.append(float(observed.get("confidence") or 0.0))
            followup_required = followup_required or bool(observed.get("followup_required"))
            backend_name = str(observed.get("backend") or "").strip() or "unknown"
            artifacts["backend_usage"].append(
                {
                    "attachment_id": observed.get("attachment_id"),
                    "type": observed.get("type"),
                    "backend": backend_name,
                    "model_provider": observed.get("model_provider"),
                    "model_id": observed.get("model_id"),
                }
            )
            if str(observed.get("meme_intent") or "").strip():
                emotion_signals.append(str(observed.get("meme_intent")).strip())
            if str(observed.get("tone_hint") or "").strip():
                emotion_signals.append(str(observed.get("tone_hint")).strip())

        observed_types = [item["type"] for item in normalized_attachments]
        primary_item = item_observations[0] if item_observations else {}
        summary_for_chat = self._compose_summary_for_chat(
            user_text=user_text,
            observed_types=observed_types,
            summary_parts=summary_parts,
            primary_item=primary_item,
        )
        core_observation = self._derive_core_observation(
            observed_types=observed_types,
            primary_item=primary_item,
            fallback_summary=summary_for_chat,
        )
        scene_tags = [str(tag).strip().lower() for tag in list(primary_item.get("scene_tags") or []) if str(tag).strip()]
        artifact_type = "screenshot" if "screenshot" in scene_tags else str(primary_item.get("artifact_type") or "").strip().lower()
        overall_confidence = round(sum(confidence_values) / len(confidence_values), 4) if confidence_values else 0.0
        observations = {
            "request_id": request_id,
            "platform": platform,
            "conversation_id": conversation_id,
            "items": item_observations,
            "attachment_count": len(normalized_attachments),
            "observed_types": observed_types,
            "summary_for_chat": summary_for_chat,
            "safety_flags": list(dict.fromkeys(safety_flags)),
            "confidence": overall_confidence,
            "followup_required": followup_required,
            "artifacts": artifacts,
            "primary_modality": observed_types[0] if observed_types else "text",
            "modalities": observed_types or ["text"],
            "emotion_signals": list(dict.fromkeys(emotion_signals)),
            "core_observation": core_observation or None,
            "visible_text": str(primary_item.get("visible_text") or primary_item.get("ocr_text") or "").strip() or None,
            "artifact_type": artifact_type or None,
        }
        return {
            "mode": "sync",
            "observations": observations,
            "pending_jobs": [],
            "summary_for_chat": summary_for_chat,
            "safety_flags": list(dict.fromkeys(safety_flags)),
            "confidence": overall_confidence,
            "followup_required": followup_required,
            "artifacts": artifacts,
        }

    def _compose_summary_for_chat(
        self,
        *,
        user_text: str,
        observed_types: list[str],
        summary_parts: list[str],
        primary_item: dict[str, Any],
    ) -> str:
        base_summary = "；".join(part for part in summary_parts if part)
        if not observed_types:
            return base_summary
        visual_summary = str(primary_item.get("visual_summary") or "").strip()
        visible_text = str(primary_item.get("visible_text") or primary_item.get("ocr_text") or "").strip()
        meme_intent = str(primary_item.get("meme_intent") or primary_item.get("tone_hint") or "").strip()
        summary_bits = [bit for bit in [meme_intent, visual_summary, visible_text] if bit]
        concise_observation = "；".join(summary_bits[:2]).strip() or base_summary
        return concise_observation or base_summary

    def _derive_core_observation(
        self,
        *,
        observed_types: list[str],
        primary_item: dict[str, Any],
        fallback_summary: str,
    ) -> str:
        if not observed_types:
            return ""
        primary_type = str(observed_types[0] or "").strip().lower()
        if primary_type not in {"image", "emoji"}:
            return ""
        bits = [
            str(primary_item.get("meme_intent") or primary_item.get("tone_hint") or "").strip(),
            str(primary_item.get("visual_summary") or "").strip(),
            str(primary_item.get("visible_text") or primary_item.get("ocr_text") or "").strip(),
        ]
        concise = "；".join(bit for bit in bits if bit).strip()
        if concise:
            return concise
        return str(fallback_summary or "").strip()

    def _normalize_attachments(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for index, raw in enumerate(items):
            payload = dict(raw or {})
            extra = dict(payload.get("extra") or {})
            local_path = str(payload.get("local_path") or "").strip() or None
            remote_url = str(payload.get("remote_url") or "").strip() or None
            file_name = str(payload.get("file_name") or "").strip() or _guess_file_name(local_path, remote_url)
            normalized.append(
                {
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
                    "parsed_text": str(payload.get("parsed_text") or extra.get("parsed_text") or "").strip() or None,
                    "page_summaries": [
                        str(item).strip()
                        for item in list(payload.get("page_summaries") or extra.get("page_summaries") or [])
                        if str(item).strip()
                    ],
                    "audio_transcript": str(payload.get("audio_transcript") or extra.get("audio_transcript") or "").strip() or None,
                    "frame_paths": [
                        str(path).strip()
                        for path in list(payload.get("frame_paths") or extra.get("frame_paths") or [])
                        if str(path).strip()
                    ],
                    "preview_image_paths": [
                        str(path).strip()
                        for path in list(payload.get("preview_image_paths") or extra.get("preview_image_paths") or [])
                        if str(path).strip()
                    ],
                    "extra": extra,
                }
            )
        return normalized

    def _decide_mode(self, attachments: list[dict[str, Any]], *, processing_mode_hint: str) -> str:
        if processing_mode_hint == "sync":
            return "sync"
        if processing_mode_hint == "async":
            return "async"
        if len(attachments) > self._max_sync_attachments:
            return "async"
        for item in attachments:
            if item["type"] == "video":
                return "async"
            size_bytes = int(item.get("size_bytes") or 0)
            if size_bytes > self._sync_file_limit_bytes:
                return "async"
        return "sync"

    def _observe_attachment_with_backends(
        self,
        *,
        request_id: str,
        platform: str,
        conversation_id: str,
        user_text: str,
        item: dict[str, Any],
        render_target_lang: str,
        phase: str,
    ) -> dict[str, Any]:
        payload = MultimodalObserverInput(
            request_id=request_id,
            platform=platform,
            conversation_id=conversation_id,
            user_text=user_text,
            attachment=dict(item or {}),
            render_target_lang=render_target_lang,
            phase=phase,
        )

        if str(item.get("type") or "") == "video":
            duration_ms = int(item.get("duration_ms") or 0)
            if duration_ms > 0 and self._video_max_duration_seconds > 0:
                duration_s = float(duration_ms) / 1000.0
                if duration_s > float(self._video_max_duration_seconds):
                    degraded = dict(self._fallback_backend.observe_attachment(payload))
                    safety_flags = [str(flag).strip() for flag in list(degraded.get("safety_flags") or []) if str(flag).strip()]
                    safety_flags.append("VIDEO_DURATION_EXCEEDS_MODEL_LIMIT")
                    degraded["safety_flags"] = list(dict.fromkeys(safety_flags))
                    degraded["summary"] = (
                        str(degraded.get("summary") or "").strip()
                        + f" 这段视频大约 {round(duration_s, 1)} 秒，超出了当前多模态模型的时长上限，所以我先只记录元数据。"
                    ).strip()
                    degraded["followup_required"] = True
                    return degraded

        if str(item.get("type") or "") == "file":
            base_file = self._fallback_backend.observe_attachment(payload)
            should_try_model = (
                not str(base_file.get("text_excerpt") or "").strip()
                and self._model_backend.supports(payload)
            )
            if should_try_model:
                try:
                    model_file = self._model_backend.observe_attachment(payload)
                    model_file.setdefault("summary", base_file.get("summary"))
                    return model_file
                except Exception as exc:
                    _logger.debug(
                        "multimodal model backend file fallback request_id=%s attachment_id=%s err=%s",
                        request_id,
                        item.get("attachment_id"),
                        exc,
                    )
            return base_file

        if self._model_backend.supports(payload):
            try:
                return self._model_backend.observe_attachment(payload)
            except Exception as exc:
                _logger.warning(
                    "multimodal model backend degraded request_id=%s attachment_id=%s type=%s err=%s",
                    request_id,
                    item.get("attachment_id"),
                    item.get("type"),
                    exc,
                )
        return self._fallback_backend.observe_attachment(payload)

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


__all__ = ["MultimodalObserver"]
