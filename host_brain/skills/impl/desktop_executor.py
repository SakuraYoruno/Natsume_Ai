"""Governed Host authoring adapter for Edge-owned desktop effects.

The Skill plane may normalize and authorize a desktop request, but only Edge
may interpret it as screenshot, mouse, keyboard, or clipboard I/O. This module
therefore creates a versioned ``EmbodimentCommand`` and never imports a device
driver or performs the effect locally.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from host_brain.channels.embodiment import queue_desktop_command


_logger = logging.getLogger("Natsume.skills.desktop_executor")
_AUTHORITY_CONTEXT_KEY = "_skill_execution_authority"
_DESKTOP_AUTHORITY_MISSING = "DESKTOP_SKILL_AUTHORITY_MISSING"
_DESKTOP_COMMAND_FAILED = "DESKTOP_EMBODIMENT_COMMAND_FAILED"


def execute_desktop_skill(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Submit one already-authorized desktop effect to the Edge authority."""

    payload = dict(args or {})
    action = str(payload.get("action") or "").strip().lower()
    manifest_ref = str(payload.get("manifest_ref") or f"desktop::{action}")
    authority = _validated_authority(context)
    if authority is None:
        return {
            "status": "REJECTED",
            "reason_code": _DESKTOP_AUTHORITY_MISSING,
            "manifest_ref": manifest_ref,
            "action": action,
            "detail": "desktop effects require Broker-validated Skill authority",
        }

    try:
        command_action, command_payload = _command_payload(action, payload)
        receipt = queue_desktop_command(
            action=command_action,
            payload=command_payload,
            request_id=str(authority["request_id"]),
            authority_ref=str(authority["authority_ref"]),
            grant_ref=str(authority["grant_ref"]),
            deadline_ms=int(authority["expires_at_ms"]),
        )
    except Exception as exc:
        _logger.warning("Desktop Edge command submission failed (action=%s): %s", action, exc)
        return {
            "status": "FAILED",
            "reason_code": _DESKTOP_COMMAND_FAILED,
            "manifest_ref": manifest_ref,
            "action": action,
            "detail": str(exc),
        }

    receipt_payload = receipt.as_dict()
    return {
        "status": "QUEUED" if receipt.success else "FAILED",
        "reason_code": receipt.reason_code,
        "manifest_ref": manifest_ref,
        "action": action,
        "detail": dict(receipt.event or {}),
        "embodiment_outbox": receipt_payload,
    }


def _validated_authority(context: dict[str, Any] | None) -> Mapping[str, Any] | None:
    snapshot = (context or {}).get(_AUTHORITY_CONTEXT_KEY)
    if not isinstance(snapshot, Mapping):
        return None
    required = ("request_id", "authority_ref", "grant_ref", "expires_at_ms")
    if any(not str(snapshot.get(key) or "").strip() for key in required):
        return None
    return snapshot


def _command_payload(action: str, payload: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    if action == "screenshot":
        return "screenshot", {
            "monitor_index": max(1, int(payload.get("monitor") or 1)),
            "region": payload.get("region") if isinstance(payload.get("region"), Mapping) else None,
            "with_cursor": bool(payload.get("with_cursor", False)),
            "return_base64": bool(payload.get("return_base64", True)),
            "save_path": str(payload.get("save_path") or "").strip() or None,
        }
    if action == "click":
        click_action = str(payload.get("click_action") or "click").strip().lower()
        if click_action not in {"click", "double_click", "right_click", "move", "scroll"}:
            click_action = "click"
        desktop_action: dict[str, Any] = {
            "type": click_action,
            "x": _optional_int(payload.get("x")),
            "y": _optional_int(payload.get("y")),
            "duration_ms": max(0, int(payload.get("move_duration_ms") or 0)),
        }
        if click_action == "scroll":
            desktop_action["amount"] = int(payload.get("amount") or 3)
        return "automate", {"actions": [desktop_action], "stop_on_error": True}
    if action == "type":
        actions: list[dict[str, Any]] = [{
            "type": "type",
            "text": str(payload.get("text") or ""),
            "wait_after_ms": max(0, int(payload.get("interval_ms") or 0)),
        }]
        if bool(payload.get("press_enter", False)):
            actions.append({"type": "key", "text": "enter"})
        return "automate", {"actions": actions, "stop_on_error": True}
    if action == "hotkey":
        keys = payload.get("keys")
        normalized_keys = [str(key).strip() for key in keys] if isinstance(keys, list) else []
        repeat = max(1, int(payload.get("repeat") or 1))
        return "automate", {
            "actions": [
                {
                    "type": "key",
                    "text": "+".join(normalized_keys),
                    "duration_ms": max(0, int(payload.get("hold_ms") or 0)),
                }
                for _ in range(repeat)
            ],
            "stop_on_error": True,
        }
    if action in {"clipboard_read", "clipboard_write"}:
        # Clipboard ownership is also Edge-only. The command remains explicit
        # even when an older Edge version rejects the newly declared action.
        body = {"content_type": str(payload.get("content_type") or "text")}
        if action == "clipboard_write":
            body["text"] = str(payload.get("text") or "")
        return action, body
    raise ValueError(f"unsupported desktop action: {action}")


def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None


__all__ = ["execute_desktop_skill"]
