"""Read-only Operator projection for trusted Evolution Lab evidence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from host_brain.app.observability_runtime.domain_state_projection import (
    read_domain_projection_from_store,
)
from host_brain.observability.projection_store import QueryProjectionStore
from host_brain.persistence.evolution_lab_trusted_query_repository import (
    TrustedEvolutionLabOperatorQueryRepository,
    TrustedEvolutionLabQueryError,
    decode_evolution_lab_suite_cursor,
    encode_evolution_lab_suite_cursor,
)


EVOLUTION_LAB_OPERATOR_KIND = "evolution_lab_operator"
EVOLUTION_LAB_OPERATOR_KEY = "trusted-suites"
EVOLUTION_LAB_OPERATOR_MAX_AGE_MS = 120_000
EVOLUTION_LAB_OPERATOR_SOURCE_LIMIT = 100
EVOLUTION_LAB_OPERATOR_ROUTE_MAX_LIMIT = 50


def load_evolution_lab_operator_projection_source(
    repository: TrustedEvolutionLabOperatorQueryRepository,
) -> tuple[dict[str, Any], int]:
    """Read and verify a bounded canonical page for background projection."""

    if not isinstance(repository, TrustedEvolutionLabOperatorQueryRepository):
        raise TypeError(
            "repository must be TrustedEvolutionLabOperatorQueryRepository"
        )
    page = repository.list_suites(
        limit=EVOLUTION_LAB_OPERATOR_SOURCE_LIMIT,
    )
    items = list(page.get("items") or [])
    cursor = max(
        (int(item.get("created_at_ms") or 0) for item in items),
        default=0,
    )
    return (
        {
            "schema_version": "Natsume.evolution_lab.operator_snapshot.v1",
            "available": True,
            "read_only": True,
            "proposal_only": True,
            "production_influence_enabled": False,
            "suite_count": len(items),
            "source_truncated": bool(page.get("has_more")),
            "source_next_cursor": page.get("next_cursor"),
            "items": items,
        },
        cursor,
    )


def read_evolution_lab_operator_projection(
    store: QueryProjectionStore,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Read the current public projection without touching canonical writers."""

    return read_domain_projection_from_store(
        store,
        kind=EVOLUTION_LAB_OPERATOR_KIND,
        key=EVOLUTION_LAB_OPERATOR_KEY,
        max_age_ms=EVOLUTION_LAB_OPERATOR_MAX_AGE_MS,
    )


def paginate_evolution_lab_operator_projection(
    payload: Mapping[str, Any],
    *,
    limit: int,
    cursor: str | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Apply stable cursor/workspace pagination to one bounded snapshot."""

    if not isinstance(payload, Mapping):
        raise TrustedEvolutionLabQueryError(
            "EVOLUTION_LAB_PROJECTION_INVALID",
            "operator projection must be an object",
        )
    if payload.get("schema_version") != (
        "Natsume.evolution_lab.operator_snapshot.v1"
    ):
        raise TrustedEvolutionLabQueryError(
            "EVOLUTION_LAB_PROJECTION_SCHEMA_INVALID",
            "operator projection schema is unsupported",
        )
    if (
        payload.get("read_only") is not True
        or payload.get("proposal_only") is not True
        or payload.get("production_influence_enabled") is not False
    ):
        raise TrustedEvolutionLabQueryError(
            "EVOLUTION_LAB_PROJECTION_AUTHORITY_INVALID",
            "operator projection attempted to expose mutation authority",
        )
    normalized_limit = int(limit)
    if (
        isinstance(limit, bool)
        or normalized_limit < 1
        or normalized_limit > EVOLUTION_LAB_OPERATOR_ROUTE_MAX_LIMIT
    ):
        raise TrustedEvolutionLabQueryError(
            "EVOLUTION_LAB_QUERY_LIMIT_INVALID",
            (
                "limit must be between 1 and "
                f"{EVOLUTION_LAB_OPERATOR_ROUTE_MAX_LIMIT}"
            ),
        )
    workspace = str(workspace_id or "").strip()
    if len(workspace) > 160:
        raise TrustedEvolutionLabQueryError(
            "EVOLUTION_LAB_WORKSPACE_FILTER_INVALID",
            "workspace_id is too large",
        )
    boundary = (
        decode_evolution_lab_suite_cursor(cursor) if cursor else None
    )
    items_raw = payload.get("items")
    if not isinstance(items_raw, list):
        raise TrustedEvolutionLabQueryError(
            "EVOLUTION_LAB_PROJECTION_INVALID",
            "operator projection items must be an array",
        )
    eligible: list[dict[str, Any]] = []
    for item_raw in items_raw:
        if not isinstance(item_raw, Mapping):
            raise TrustedEvolutionLabQueryError(
                "EVOLUTION_LAB_PROJECTION_INVALID",
                "operator suite item must be an object",
            )
        item = dict(item_raw)
        created_at_ms = int(item.get("created_at_ms") or 0)
        suite_id = str(item.get("suite_id") or "")
        if not suite_id or created_at_ms < 0:
            raise TrustedEvolutionLabQueryError(
                "EVOLUTION_LAB_PROJECTION_INVALID",
                "operator suite identity is invalid",
            )
        if boundary is not None and not (
            created_at_ms < int(boundary["created_at_ms"])
            or (
                created_at_ms == int(boundary["created_at_ms"])
                and suite_id < str(boundary["suite_id"])
            )
        ):
            continue
        workspace_ids = item.get("workspace_ids")
        if not isinstance(workspace_ids, list):
            raise TrustedEvolutionLabQueryError(
                "EVOLUTION_LAB_PROJECTION_INVALID",
                "operator suite workspace identities are invalid",
            )
        if workspace and workspace not in {
            str(value) for value in workspace_ids
        }:
            continue
        eligible.append(item)
    eligible.sort(
        key=lambda item: (
            int(item.get("created_at_ms") or 0),
            str(item.get("suite_id") or ""),
        ),
        reverse=True,
    )
    selected = eligible[:normalized_limit]
    next_cursor = None
    if len(eligible) > normalized_limit and selected:
        tail = selected[-1]
        next_cursor = encode_evolution_lab_suite_cursor(
            created_at_ms=int(tail["created_at_ms"]),
            suite_id=str(tail["suite_id"]),
        )
    return {
        "schema_version": "Natsume.evolution_lab.operator_page.v1",
        "available": payload.get("available") is True,
        "read_only": True,
        "proposal_only": True,
        "production_influence_enabled": False,
        "workspace_id": workspace or None,
        "items": selected,
        "next_cursor": next_cursor,
        "has_more": next_cursor is not None,
        "page_size": len(selected),
        "source_truncated": payload.get("source_truncated") is True,
    }


__all__ = [
    "EVOLUTION_LAB_OPERATOR_KEY",
    "EVOLUTION_LAB_OPERATOR_KIND",
    "EVOLUTION_LAB_OPERATOR_MAX_AGE_MS",
    "EVOLUTION_LAB_OPERATOR_ROUTE_MAX_LIMIT",
    "EVOLUTION_LAB_OPERATOR_SOURCE_LIMIT",
    "load_evolution_lab_operator_projection_source",
    "paginate_evolution_lab_operator_projection",
    "read_evolution_lab_operator_projection",
]
