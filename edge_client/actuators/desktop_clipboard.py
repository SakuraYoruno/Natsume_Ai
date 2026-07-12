"""Edge-owned clipboard effects for desktop embodiment commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


DESKTOP_CLIPBOARD_ACTION_INVALID = "DESKTOP_CLIPBOARD_ACTION_INVALID"
DESKTOP_CLIPBOARD_UNAVAILABLE = "DESKTOP_CLIPBOARD_UNAVAILABLE"
DESKTOP_CLIPBOARD_FAILED = "DESKTOP_CLIPBOARD_FAILED"
_SUPPORTED_ACTIONS = frozenset({"clipboard_read", "clipboard_write"})


@dataclass(frozen=True, slots=True)
class DesktopClipboardEffect:
    success: bool
    status: str
    reason_code: str | None
    detail: str
    event: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "status": self.status,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "event": dict(self.event),
        }


def execute_desktop_clipboard_effect(
    *,
    action: str,
    payload: Mapping[str, Any] | None,
) -> DesktopClipboardEffect:
    """Execute one validated clipboard command on Edge, never on Host."""

    normalized_action = str(action or "").strip().lower()
    if normalized_action not in _SUPPORTED_ACTIONS:
        return _rejected(
            reason_code=DESKTOP_CLIPBOARD_ACTION_INVALID,
            detail=f"unsupported desktop clipboard action: {normalized_action}",
        )

    try:
        import pyperclip
    except ImportError:
        return _rejected(
            reason_code=DESKTOP_CLIPBOARD_UNAVAILABLE,
            detail="Edge clipboard dependency is unavailable",
        )

    body = dict(payload or {})
    try:
        if normalized_action == "clipboard_read":
            content_type = str(body.get("content_type") or "text").strip().lower()
            if content_type not in {"text", "any"}:
                return _rejected(
                    reason_code=DESKTOP_CLIPBOARD_ACTION_INVALID,
                    detail=f"unsupported clipboard content_type: {content_type}",
                )
            text = str(pyperclip.paste() or "")
            return DesktopClipboardEffect(
                success=True,
                status="COMPLETED",
                reason_code=None,
                detail="Edge clipboard read completed",
                event={"content_type": "text", "text": text, "text_length": len(text)},
            )

        text = str(body.get("text") or "")
        pyperclip.copy(text)
        return DesktopClipboardEffect(
            success=True,
            status="COMPLETED",
            reason_code=None,
            detail="Edge clipboard write completed",
            # Do not reflect copied content into receipts or audit evidence.
            event={"content_type": "text", "text_length": len(text)},
        )
    except Exception as exc:
        return _rejected(
            reason_code=DESKTOP_CLIPBOARD_FAILED,
            detail=f"Edge clipboard operation failed: {type(exc).__name__}",
        )


def _rejected(*, reason_code: str, detail: str) -> DesktopClipboardEffect:
    return DesktopClipboardEffect(
        success=False,
        status="REJECTED",
        reason_code=reason_code,
        detail=detail,
        event={},
    )


__all__ = [
    "DESKTOP_CLIPBOARD_ACTION_INVALID",
    "DESKTOP_CLIPBOARD_FAILED",
    "DESKTOP_CLIPBOARD_UNAVAILABLE",
    "DesktopClipboardEffect",
    "execute_desktop_clipboard_effect",
]
