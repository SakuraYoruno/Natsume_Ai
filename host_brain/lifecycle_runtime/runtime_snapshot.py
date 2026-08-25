"""Digital-life runtime snapshot assembly."""

from typing import Any

from .canonical_life_snapshot import (
    build_canonical_life_runtime_snapshot,
    resolve_runtime_relationship_subject_id,
)
from .ports import LifecycleRuntimeApplicationPorts
from host_brain.core.ids import now_ms as _now_ms


def build_digital_life_runtime_snapshot(
    *,
    ports: LifecycleRuntimeApplicationPorts,
    current_topic: str = "",
) -> dict[str, Any]:
    """Build the OBS projection from the canonical Life reader only.

    Runtime OBS must not reconstruct self-affect or relationship state from
    retired Bond, EmotionTimeline, Limbic, or Prefrontal projections.  Missing
    or failing canonical providers are handled by the canonical builder as a
    degraded, fail-closed result.
    """

    now_ms = _now_ms()
    normalized_topic = str(current_topic or "").strip()
    persona_user_id, subject_status, subject_detail = (
        resolve_runtime_relationship_subject_id()
    )
    return build_canonical_life_runtime_snapshot(
        ports=ports,
        current_topic=normalized_topic,
        now_ms=now_ms,
        relationship_subject_id=persona_user_id,
        subject_status=subject_status,
        subject_detail=subject_detail,
    )


__all__ = ["build_digital_life_runtime_snapshot"]
