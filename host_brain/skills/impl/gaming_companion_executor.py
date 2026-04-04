from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable


def _coerce_scene_awareness(raw_scene: Any) -> dict[str, Any]:
    if isinstance(raw_scene, Mapping):
        return dict(raw_scene)
    return {}


def execute_gaming_companion(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ctx = context or {}
    scene_awareness = _coerce_scene_awareness(ctx.get("scene_awareness"))
    capture_provider = ctx.get("gaming_capture_provider")
    analyzer = ctx.get("gaming_companion_analyzer")
    ui_pusher = ctx.get("ui_pusher")
    monitor = (args or {}).get("monitor")

    if not callable(capture_provider):
        return {
            "status": "SKIPPED",
            "reason_code": "GAMING_COMPANION_CAPTURE_UNAVAILABLE",
            "scene_awareness": scene_awareness,
            "monitor": monitor,
        }

    try:
        capture_result = capture_provider(monitor=monitor, context=ctx)
    except TypeError:
        capture_result = capture_provider(monitor)
    except Exception as exc:
        return {
            "status": "DEGRADED",
            "reason_code": "GAMING_COMPANION_CAPTURE_FAILED",
            "detail": str(exc),
        }

    if not isinstance(capture_result, dict):
        capture_result = {"raw_result": capture_result}

    analysis_result: dict[str, Any] = {}
    if callable(analyzer):
        try:
            analyzed = analyzer(capture_result=capture_result, context=ctx)
        except TypeError:
            analyzed = analyzer(capture_result)
        except Exception as exc:
            analyzed = {
                "status": "DEGRADED",
                "reason_code": "GAMING_COMPANION_ANALYZE_FAILED",
                "detail": str(exc),
            }
        if isinstance(analyzed, dict):
            analysis_result = dict(analyzed)
        else:
            analysis_result = {"raw_result": analyzed}

    pushed = None
    suggested_message = str(analysis_result.get("message") or "").strip()
    if suggested_message and callable(ui_pusher):
        try:
            pushed = ui_pusher({"content": suggested_message, "source": "gaming_companion_tick"})
        except Exception as exc:
            pushed = {"status": "DEGRADED", "detail": str(exc)}

    status = "EXECUTED" if suggested_message else "OK"
    return {
        "status": status,
        "monitor": monitor,
        "scene_awareness": scene_awareness,
        "capture_result": capture_result,
        "analysis_result": analysis_result,
        "push_result": pushed,
    }
