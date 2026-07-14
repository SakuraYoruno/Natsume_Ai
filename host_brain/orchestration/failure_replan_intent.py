"""Parse one explicit, Governor-bound failure-replan reference.

The reference selects only an existing durable graph revision.  Failure
evidence, graph status and lineage are never accepted from the caller; the
production recovery service derives them from the canonical TaskStore.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Any


FAILURE_REPLAN_SCHEMA_VERSION = "Natsume.planning.failure_replan.v1"
FAILURE_REPLAN_INTENT_INVALID = "FAILURE_REPLAN_INTENT_INVALID"

_GRAPH_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")
_FIELDS = frozenset({"schema_version", "graph_id", "previous_revision"})


class FailureReplanIntentError(ValueError):
    def __init__(self, detail: str) -> None:
        self.reason_code = FAILURE_REPLAN_INTENT_INVALID
        self.detail = str(detail or "failure replan reference is invalid")
        super().__init__(f"{self.reason_code}: {self.detail}")


@dataclass(frozen=True, slots=True)
class FailureReplanReference:
    graph_id: str
    previous_revision: int
    schema_version: str = FAILURE_REPLAN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        graph_id = str(self.graph_id or "").strip()
        if not _GRAPH_ID.fullmatch(graph_id):
            raise FailureReplanIntentError("graph_id is not a canonical identifier")
        if (
            isinstance(self.previous_revision, bool)
            or not isinstance(self.previous_revision, int)
            or self.previous_revision < 1
        ):
            raise FailureReplanIntentError("previous_revision must be an exact positive integer")
        if self.schema_version != FAILURE_REPLAN_SCHEMA_VERSION:
            raise FailureReplanIntentError("failure replan schema version is unsupported")
        object.__setattr__(self, "graph_id", graph_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "graph_id": self.graph_id,
            "previous_revision": self.previous_revision,
        }


def failure_replan_reference_from_intent(
    intent_draft: Mapping[str, Any] | None,
) -> FailureReplanReference | None:
    draft = dict(intent_draft or {})
    direct = draft.get("failure_replan")
    args = draft.get("args")
    nested = args.get("failure_replan") if isinstance(args, Mapping) else None
    if direct is None and nested is None:
        return None
    if direct is not None and nested is not None and direct != nested:
        raise FailureReplanIntentError(
            "top-level and args failure_replan references differ"
        )
    raw = direct if direct is not None else nested
    if not isinstance(raw, Mapping):
        raise FailureReplanIntentError("failure_replan must be an object")
    payload = dict(raw)
    if set(payload) != _FIELDS:
        raise FailureReplanIntentError(
            "failure_replan must contain exactly schema_version, graph_id and previous_revision"
        )
    return FailureReplanReference(
        schema_version=str(payload.get("schema_version") or ""),
        graph_id=str(payload.get("graph_id") or ""),
        previous_revision=payload.get("previous_revision"),
    )


__all__ = [
    "FAILURE_REPLAN_INTENT_INVALID",
    "FAILURE_REPLAN_SCHEMA_VERSION",
    "FailureReplanIntentError",
    "FailureReplanReference",
    "failure_replan_reference_from_intent",
]
