"""Pure validation records for durable DAG scheduling."""

from __future__ import annotations

from collections.abc import Mapping

from .admission import AdmittedExecution
from .coordinator_records import _text
from .errors import GOVERNED_ASSIGNMENT_CONFLICT, GovernedRuntimeError


def _validated_node_grant_refs(
    admitted: AdmittedExecution,
    value: Mapping[str, str] | None,
) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise GovernedRuntimeError(
            GOVERNED_ASSIGNMENT_CONFLICT,
            "node_grant_refs must be an exact TaskNode mapping",
        )
    refs = {
        _text(node_id): _text(grant_ref)
        for node_id, grant_ref in value.items()
        if _text(node_id) and _text(grant_ref)
    }
    expected = {binding.node_id for binding in admitted.nodes}
    if set(refs) != expected or len(refs) != len(value):
        raise GovernedRuntimeError(
            GOVERNED_ASSIGNMENT_CONFLICT,
            "node_grant_refs must cover the admitted TaskGraph exactly",
        )
    return refs


__all__ = ["_validated_node_grant_refs"]
# End of module.
