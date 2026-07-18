"""Typed in-process carrier for one exact ActionPreview execution boundary.

The carrier is deliberately not serializable authority.  It only keeps the
app-local ``ActionPreviewService`` together with the durable event identity and
the exact boundary that the terminal adapter must reopen immediately before
performing network or device I/O.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from host_brain.governor.action_preview import ActionPreviewService


_EXECUTION_BOUNDARY_FIELDS = frozenset(
    {
        "action_kind",
        "action_scope",
        "risk_level",
        "action_args",
        "context_pack_digest",
        "scene_identity",
        "task_graph_id",
        "graph_revision",
        "task_node_id",
        "parent_governor_grant_ref",
        "node_grant_ref",
        "node_grant",
        "authority_context",
    }
)


@dataclass(frozen=True, slots=True)
class ActionPreviewExecutionProof:
    """One app-local service plus its exact durable execution boundary."""

    service: ActionPreviewService
    event_id: str
    boundary: Mapping[str, Any]

    def __post_init__(self) -> None:
        from host_brain.governor.action_preview import (  # noqa: PLC0415
            ActionPreviewService,
        )

        if not isinstance(self.service, ActionPreviewService):
            raise TypeError(
                "ActionPreviewExecutionProof requires app-local ActionPreviewService"
            )
        event_id = str(self.event_id or "").strip()
        if not event_id:
            raise ValueError("ActionPreviewExecutionProof requires event_id")
        if not isinstance(self.boundary, Mapping):
            raise TypeError("ActionPreviewExecutionProof boundary must be a mapping")
        boundary = deepcopy(dict(self.boundary))
        if frozenset(boundary) != _EXECUTION_BOUNDARY_FIELDS:
            raise ValueError("ActionPreviewExecutionProof boundary fields drifted")
        if not isinstance(boundary.get("action_args"), Mapping):
            raise ValueError("ActionPreviewExecutionProof action_args must be a mapping")
        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "boundary", boundary)

    @property
    def action_kind(self) -> str:
        return str(self.boundary.get("action_kind") or "")

    @property
    def action_scope(self) -> str:
        return str(self.boundary.get("action_scope") or "")

    @property
    def action_args(self) -> dict[str, Any]:
        return deepcopy(dict(self.boundary.get("action_args") or {}))

    def boundary_dict(self) -> dict[str, Any]:
        return deepcopy(dict(self.boundary))


__all__ = ["ActionPreviewExecutionProof"]
