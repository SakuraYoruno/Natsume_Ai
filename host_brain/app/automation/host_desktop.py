"""Host desktop command proxy; physical desktop access exists only on Edge."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from host_brain.channels.embodiment import queue_desktop_command


class DesktopActionType(str, Enum):
    CLICK = "click"
    RIGHT_CLICK = "right_click"
    DOUBLE_CLICK = "double_click"
    MOVE = "move"
    TYPE = "type"
    KEY = "key"
    SCROLL = "scroll"
    DRAG = "drag"
    SCREENSHOT = "screenshot"
    WAIT = "wait"


@dataclass
class DesktopAction:
    type: DesktopActionType
    x: int | None = None
    y: int | None = None
    x2: int | None = None
    y2: int | None = None
    text: str | None = None
    amount: int | None = None
    duration_ms: int = 0
    wait_after_ms: int = 100

    def as_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "type": self.type.value,
                "x": self.x, "y": self.y, "x2": self.x2, "y2": self.y2,
                "text": self.text, "amount": self.amount,
                "duration_ms": self.duration_ms, "wait_after_ms": self.wait_after_ms,
            }.items()
            if value is not None
        }


@dataclass(frozen=True)
class ActionExecuteResult:
    action_index: int
    action_type: str
    success: bool
    reason_code: str | None
    detail: str
    latency_ms: int

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class DesktopExecuteReport:
    total_actions: int
    success_count: int
    failed_count: int
    total_latency_ms: int
    results: list[ActionExecuteResult] = field(default_factory=list)
    error: str | None = None

    @property
    def all_success(self) -> bool:
        return self.failed_count == 0

    def as_dict(self) -> dict[str, Any]:
        return {
            **{key: value for key, value in self.__dict__.items() if key != "results"},
            "all_success": self.all_success,
            "results": [result.as_dict() for result in self.results],
        }


@dataclass(frozen=True)
class ScreenshotResult:
    success: bool
    image_b64: str
    image_mime: str
    width: int
    height: int
    monitor_index: int
    latency_ms: int
    reason_code: str | None = None
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class HostDesktopController:
    def __init__(self, *, monitor_index: int = 1, **_: Any) -> None:
        self._monitor_index = int(monitor_index)

    def capture_screenshot(self, monitor_index: int | None = None) -> ScreenshotResult:
        target = int(monitor_index if monitor_index is not None else self._monitor_index)
        receipt = queue_desktop_command(
            action="screenshot",
            payload={"monitor_index": target},
        )
        event = dict(receipt.event or {})
        return ScreenshotResult(
            success=receipt.success,
            image_b64=str(event.get("image_b64") or ""),
            image_mime=str(event.get("image_mime") or "image/png"),
            width=int(event.get("width") or 0),
            height=int(event.get("height") or 0),
            monitor_index=int(event.get("monitor_index") or target),
            latency_ms=receipt.latency_ms,
            reason_code=receipt.reason_code,
            detail=receipt.detail,
        )

    def execute_actions(
        self, actions: list[DesktopAction], *, stop_on_error: bool = True
    ) -> DesktopExecuteReport:
        payload = {
            "actions": [
                action.as_dict() if hasattr(action, "as_dict") else dict(action)
                for action in actions
            ],
            "stop_on_error": bool(stop_on_error),
        }
        receipt = queue_desktop_command(action="automate", payload=payload)
        event = dict(receipt.event or {})
        result_rows = list(event.get("results") or [])
        results = [
            ActionExecuteResult(
                action_index=int(row.get("action_index") or index),
                action_type=str(row.get("action_type") or ""),
                success=bool(row.get("success")),
                reason_code=(str(row.get("reason_code") or "").strip() or None),
                detail=str(row.get("detail") or ""),
                latency_ms=int(row.get("latency_ms") or 0),
            )
            for index, row in enumerate(result_rows)
            if isinstance(row, dict)
        ]
        total = len(actions)
        succeeded = int(event.get("success_count") or sum(item.success for item in results))
        failed = int(event.get("failed_count") or max(0, total - succeeded))
        return DesktopExecuteReport(
            total_actions=total,
            success_count=succeeded,
            failed_count=failed,
            total_latency_ms=receipt.latency_ms,
            results=results,
            error=None if receipt.success else receipt.detail,
        )


DesktopController = HostDesktopController

__all__ = [
    "HostDesktopController", "DesktopController", "DesktopAction",
    "DesktopActionType", "DesktopExecuteReport", "ActionExecuteResult",
    "ScreenshotResult",
]
