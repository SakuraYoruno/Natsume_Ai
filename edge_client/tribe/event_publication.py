"""Shared observation-event publication for Edge Tribe agents."""

from __future__ import annotations

from typing import Any, Mapping


def publish_tribe_observation(
    agent: Any,
    *,
    event_type: str,
    payload: Mapping[str, Any],
    target_agent: str | None = None,
    source_agent: str | None = None,
) -> None:
    """Publish an observation when a bridge is available, without retry or replay.

    The bridge is an observation channel, never evidence that a local device
    or platform action succeeded.  Its absence therefore leaves the caller's
    local state untouched and must not create an implicit replay obligation.
    """

    bridge = agent.bridge
    if bridge is None or not hasattr(bridge, "publish_event"):
        return
    bridge.publish_event(
        event_type=event_type,
        source_agent=source_agent or agent.descriptor.agent_id,
        target_agent=target_agent,
        payload=dict(payload),
    )


__all__ = ["publish_tribe_observation"]
