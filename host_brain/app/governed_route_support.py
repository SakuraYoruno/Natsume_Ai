"""Shared fail-closed validation helpers for frozen route executors."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from typing import Any

from host_brain.task_runtime.governed_workers import FrozenNodeInput


GOVERNED_ROUTE_INPUT_INVALID = "GOVERNED_ROUTE_INPUT_INVALID"
GOVERNED_ROUTE_OUTPUT_INVALID = "GOVERNED_ROUTE_OUTPUT_INVALID"
GOVERNED_ROUTE_ROUTE_DRIFT = "GOVERNED_ROUTE_ROUTE_DRIFT"


class GovernedRouteExecutorError(RuntimeError):
    """Fail-closed error raised before a frozen executor can commit output."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or GOVERNED_ROUTE_OUTPUT_INVALID)
        self.detail = str(detail or "governed route execution failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


def _strict_non_negative_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool):
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_OUTPUT_INVALID,
            f"task-bound {field_name} must be an integer",
        )
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_OUTPUT_INVALID,
            f"task-bound {field_name} must be an integer",
        ) from exc
    if parsed < 0:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_OUTPUT_INVALID,
            f"task-bound {field_name} must be non-negative",
        )
    return parsed


def _strict_positive_int(value: Any, *, field_name: str) -> int:
    parsed = _strict_non_negative_int(value, field_name=field_name)
    if parsed < 1:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_OUTPUT_INVALID,
            f"task-bound {field_name} must be positive",
        )
    return parsed


def _buffer_narrative(
    event: Mapping[str, Any],
    render_fragments: list[str],
    sentence_fragments: list[str],
) -> None:
    phase = str(event.get("phase") or "").strip().upper()
    chunk = str(event.get("chunk") or event.get("delta") or "")
    sentence = str(event.get("sentence") or "")
    if chunk and phase in {"", "DELTA", "RENDER", "TOKEN"}:
        render_fragments.append(chunk)
    if sentence and phase == "SENTENCE":
        sentence_fragments.append(sentence.strip())


def _split_complete_sse_frames(value: str) -> tuple[tuple[str, ...], str]:
    frames: list[str] = []
    remainder = value
    while "\n\n" in remainder:
        frame, remainder = remainder.split("\n\n", 1)
        if frame.strip():
            frames.append(frame)
    return tuple(frames), remainder


def _decode_event_frame(frame: str) -> tuple[dict[str, Any], ...]:
    text = str(frame or "").strip()
    if not text:
        return ()
    if text.startswith("{"):
        data = text
    else:
        data_lines = [
            line[5:].lstrip()
            for line in text.split("\n")
            if line.startswith("data:")
        ]
        if not data_lines:
            return ()
        data = "\n".join(data_lines).strip()
    if not data or data == "[DONE]":
        return ()
    try:
        loaded = json.loads(data)
    except (TypeError, ValueError) as exc:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_OUTPUT_INVALID,
            "chat stream emitted malformed SSE JSON",
        ) from exc
    if not isinstance(loaded, Mapping):
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_OUTPUT_INVALID,
            "chat SSE data must be a mapping",
        )
    return (dict(loaded),)


def _assert_no_route_drift(event: Mapping[str, Any], *, frozen: FrozenNodeInput) -> None:
    supplied_route = str(event.get("route_type") or event.get("route") or "").strip().lower()
    supplied_target = str(event.get("target_id") or "").strip()
    if supplied_route and supplied_route != frozen.route:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_ROUTE_DRIFT,
            f"frozen {frozen.route} executor returned route {supplied_route}",
        )
    if supplied_target and supplied_target != frozen.target_id:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_ROUTE_DRIFT,
            f"frozen executor returned target {supplied_target}",
        )


def _require_frozen_route(frozen: FrozenNodeInput, *, expected: str) -> None:
    if not isinstance(frozen, FrozenNodeInput):
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_INPUT_INVALID,
            "FrozenNodeInput is required",
        )
    if frozen.route != expected or not frozen.target_id:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_INPUT_INVALID,
            f"executor requires frozen {expected}:target input",
        )


def _input_pack(frozen: FrozenNodeInput) -> dict[str, Any]:
    pack = frozen.input_pack
    if not isinstance(pack, Mapping):
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_INPUT_INVALID,
            "frozen input_pack must be a mapping",
        )
    return _safe_mapping(pack)


def _required_narrative(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_OUTPUT_INVALID,
            f"{field_name} is required",
        )
    return text


def _safe_mapping(value: Any) -> dict[str, Any]:
    raw = dict(value) if isinstance(value, Mapping) else {}
    sanitized = _scrub_authority(raw)
    return dict(sanitized) if isinstance(sanitized, Mapping) else {}


def _safe_sequence(value: Any) -> tuple[Any, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    sanitized = _scrub_authority(list(value))
    return tuple(sanitized) if isinstance(sanitized, list) else ()


def _json_copy(value: Any) -> Any:
    try:
        return json.loads(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )
    except (TypeError, ValueError) as exc:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_OUTPUT_INVALID,
            "chat acceptance effect payload must be canonical JSON",
        ) from exc


def _scrub_authority(value: Any) -> Any:
    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for raw_key, child in value.items():
            key = str(raw_key)
            normalized = key.strip().lower()
            if (
                "delivery_author" in normalized
                or normalized in {
                    "governor_result",
                    "governor_grant_ref",
                    # Sub-agent context exposes a safe authorization summary,
                    # but authority metadata still does not belong in the
                    # public worker projection.  Counts remain available in
                    # the surrounding runtime-event summary.
                    "context_authorization",
                    "delivery_claim",
                    "delivery_token",
                    "approval_token",
                }
            ):
                continue
            cleaned[key] = _scrub_authority(child)
        return cleaned
    if isinstance(value, list):
        return [_scrub_authority(child) for child in value]
    if isinstance(value, tuple):
        return [_scrub_authority(child) for child in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise GovernedRouteExecutorError(
        GOVERNED_ROUTE_INPUT_INVALID,
        "frozen route payload must remain JSON-safe",
    )


def _non_negative_int(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)
