"""Test/CI-only static FastAPI route-entry guard for the TASK-0489 Obs query boundary.

The audit intentionally works on every ``APIRoute`` entry before de-duplicating
paths.  This prevents a shadowed writer from disappearing behind an earlier
query route.  HTTP methods alone are insufficient because a small, explicit
set of Obs POST endpoints only reads authority or computes previews; those
routes are the only non-GET query exceptions registered here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

from fastapi.routing import APIRoute


OBS_QUERY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass(frozen=True, order=True, slots=True)
class HttpRouteKey:
    method: str
    path: str

    def __post_init__(self) -> None:
        method = str(self.method or "").strip().upper()
        path = str(self.path or "").strip()
        if not method:
            raise ValueError("HTTP route method is required")
        if not path.startswith("/"):
            raise ValueError("HTTP route path must be absolute")
        object.__setattr__(self, "method", method)
        object.__setattr__(self, "path", path)


@dataclass(frozen=True, slots=True)
class MutationRouteSpec:
    key: HttpRouteKey
    endpoint_module: str
    endpoint_name: str

    @property
    def endpoint_identity(self) -> tuple[str, str]:
        return (self.endpoint_module, self.endpoint_name)


@dataclass(frozen=True, slots=True)
class PureQueryRouteSpec:
    key: HttpRouteKey
    endpoint_module: str
    endpoint_name: str

    @property
    def endpoint_identity(self) -> tuple[str, str]:
        return (self.endpoint_module, self.endpoint_name)


OBS_PURE_QUERY_NON_GET_SPECS = (
    PureQueryRouteSpec(
        HttpRouteKey("POST", "/obs/capability-gaps/forge-proposals/preview"),
        "host_brain.app.obs_routes_stats_capability_agent",
        "preview_capability_forge_proposal",
    ),
    PureQueryRouteSpec(
        HttpRouteKey("POST", "/obs/life-evolution/action-preview"),
        "host_brain.app.obs_routes_life_evolution",
        "post_life_evolution_action_preview",
    ),
    PureQueryRouteSpec(
        HttpRouteKey("POST", "/observability/trace"),
        "host_brain.app.obs_fastapi_app",
        "request_trace",
    ),
    PureQueryRouteSpec(
        HttpRouteKey("POST", "/obs/operator-session/bootstrap"),
        "host_brain.observability.operator_session_bootstrap",
        "bootstrap_operator_session",
    ),
)
OBS_PURE_QUERY_NON_GET_ROUTES = frozenset(
    spec.key for spec in OBS_PURE_QUERY_NON_GET_SPECS
)
OBS_PURE_QUERY_NON_GET_ENDPOINTS = frozenset(
    spec.endpoint_identity for spec in OBS_PURE_QUERY_NON_GET_SPECS
)
_PURE_QUERY_ENDPOINT_BY_ROUTE = {
    spec.key: spec.endpoint_identity for spec in OBS_PURE_QUERY_NON_GET_SPECS
}


OBS_BASELINE_MUTATION_SPECS = (
    MutationRouteSpec(
        HttpRouteKey("POST", "/obs/avatar/behavior"),
        "host_brain.app.obs_routes_stats_system",
        "post_avatar_behavior_control",
    ),
    MutationRouteSpec(
        HttpRouteKey("POST", "/obs/wechat/reset"),
        "host_brain.app.obs_routes_stats_system",
        "reset_wechat_channel",
    ),
    MutationRouteSpec(
        HttpRouteKey("POST", "/obs/capability-gaps/forge-proposals"),
        "host_brain.app.obs_routes_stats_capability_agent",
        "create_capability_forge_proposal",
    ),
    MutationRouteSpec(
        HttpRouteKey("POST", "/obs/capability-gaps/forge-proposals/{proposal_id}/approve"),
        "host_brain.app.obs_routes_stats_capability_agent",
        "approve_capability_forge_proposal",
    ),
    MutationRouteSpec(
        HttpRouteKey("POST", "/obs/capability-gaps/forge-proposals/{proposal_id}/rollback"),
        "host_brain.app.obs_routes_stats_capability_agent",
        "rollback_capability_forge_proposal",
    ),
    MutationRouteSpec(
        HttpRouteKey("POST", "/obs/capability-gaps/forge-proposals/runtime-match"),
        "host_brain.app.obs_routes_stats_capability_agent",
        "capability_forge_runtime_match",
    ),
    MutationRouteSpec(
        HttpRouteKey("POST", "/obs/sys-mode"),
        "host_brain.app.obs_routes_sysmode",
        "switch_sys_mode",
    ),
    MutationRouteSpec(
        HttpRouteKey("PUT", "/obs/sys-mode/auto-rules"),
        "host_brain.app.obs_routes_sysmode",
        "update_auto_rules",
    ),
    MutationRouteSpec(
        HttpRouteKey("POST", "/obs/sys-mode/event"),
        "host_brain.app.obs_routes_sysmode",
        "post_sys_mode_event",
    ),
    MutationRouteSpec(
        HttpRouteKey("POST", "/obs/codex-bio-ops/daily-cycle/run"),
        "host_brain.app.obs_routes_agent_bio_ops",
        "run_codex_bio_ops_daily_cycle",
    ),
    MutationRouteSpec(
        HttpRouteKey("POST", "/obs/codex-bio-ops/adoptions/{candidate_id}/accept"),
        "host_brain.app.obs_routes_agent_bio_ops",
        "accept_codex_bio_ops_adoption",
    ),
    MutationRouteSpec(
        HttpRouteKey("POST", "/obs/codex-bio-ops/adoptions/{candidate_id}/reject"),
        "host_brain.app.obs_routes_agent_bio_ops",
        "reject_codex_bio_ops_adoption",
    ),
    MutationRouteSpec(
        HttpRouteKey("POST", "/obs/codex-bio-ops/owner-inbox/materialize"),
        "host_brain.app.obs_routes_agent_bio_ops",
        "materialize_codex_bio_ops_owner_inbox",
    ),
    MutationRouteSpec(
        HttpRouteKey("POST", "/obs/codex-bio-ops/owner-inbox/intake-packets"),
        "host_brain.app.obs_routes_agent_bio_ops",
        "materialize_codex_bio_ops_owner_intake_packets",
    ),
    MutationRouteSpec(
        HttpRouteKey("POST", "/obs/codex-bio-ops/owner-queues/consume"),
        "host_brain.app.obs_routes_agent_bio_ops",
        "consume_codex_bio_ops_owner_queues",
    ),
    MutationRouteSpec(
        HttpRouteKey("POST", "/obs/codex-bio-ops/owner-state/import"),
        "host_brain.app.obs_routes_agent_bio_ops",
        "import_codex_bio_ops_owner_state",
    ),
    MutationRouteSpec(
        HttpRouteKey("POST", "/obs/codex-bio-ops/owner-state/{candidate_id}/accept"),
        "host_brain.app.obs_routes_agent_bio_ops",
        "accept_codex_bio_ops_owner_state",
    ),
    MutationRouteSpec(
        HttpRouteKey("POST", "/obs/codex-bio-ops/owner-state/{candidate_id}/reject"),
        "host_brain.app.obs_routes_agent_bio_ops",
        "reject_codex_bio_ops_owner_state",
    ),
)
OBS_BASELINE_MUTATION_ROUTES = frozenset(
    spec.key for spec in OBS_BASELINE_MUTATION_SPECS
)
OBS_BASELINE_MUTATION_ENDPOINTS = frozenset(
    spec.endpoint_identity for spec in OBS_BASELINE_MUTATION_SPECS
)


if len(OBS_BASELINE_MUTATION_ROUTES) != len(OBS_BASELINE_MUTATION_SPECS):
    raise RuntimeError("TASK-0489 mutation route manifest contains duplicate method/path entries")
if len(OBS_BASELINE_MUTATION_ENDPOINTS) != len(OBS_BASELINE_MUTATION_SPECS):
    raise RuntimeError("TASK-0489 mutation route manifest contains duplicate endpoint identities")
if len(OBS_PURE_QUERY_NON_GET_ROUTES) != len(OBS_PURE_QUERY_NON_GET_SPECS):
    raise RuntimeError("TASK-0489 pure-query manifest contains duplicate method/path entries")
if len(OBS_PURE_QUERY_NON_GET_ENDPOINTS) != len(OBS_PURE_QUERY_NON_GET_SPECS):
    raise RuntimeError("TASK-0489 pure-query manifest contains duplicate endpoint identities")
if OBS_BASELINE_MUTATION_ROUTES & OBS_PURE_QUERY_NON_GET_ROUTES:
    raise RuntimeError("TASK-0489 mutation and pure-query route manifests overlap")


class ObsRouteEntryKind(str, Enum):
    QUERY = "QUERY"
    PURE_QUERY_NON_GET = "PURE_QUERY_NON_GET"
    KNOWN_MUTATION = "KNOWN_MUTATION"
    UNDECLARED_MUTATION = "UNDECLARED_MUTATION"


@dataclass(frozen=True, slots=True)
class FastAPIRouteEntry:
    entry_index: int
    key: HttpRouteKey
    endpoint_module: str
    endpoint_name: str
    route_name: str
    kind: ObsRouteEntryKind

    @property
    def endpoint_identity(self) -> tuple[str, str]:
        return (self.endpoint_module, self.endpoint_name)

    @property
    def is_mutation(self) -> bool:
        return self.kind in {
            ObsRouteEntryKind.KNOWN_MUTATION,
            ObsRouteEntryKind.UNDECLARED_MUTATION,
        }


@dataclass(frozen=True, slots=True)
class DuplicateRouteEntries:
    key: HttpRouteKey
    entries: tuple[FastAPIRouteEntry, ...]

    @property
    def contains_writer(self) -> bool:
        return any(entry.is_mutation for entry in self.entries) or (
            self.key in OBS_PURE_QUERY_NON_GET_ROUTES
            and len(self.entries) > 1
        )


@dataclass(frozen=True, slots=True)
class ObsQueryBoundaryAudit:
    entries: tuple[FastAPIRouteEntry, ...]
    mutation_entries: tuple[FastAPIRouteEntry, ...]
    known_mutation_entries: tuple[FastAPIRouteEntry, ...]
    undeclared_mutation_entries: tuple[FastAPIRouteEntry, ...]
    pure_query_non_get_entries: tuple[FastAPIRouteEntry, ...]
    duplicate_routes: tuple[DuplicateRouteEntries, ...]
    duplicate_writer_routes: tuple[DuplicateRouteEntries, ...]

    @property
    def entry_count(self) -> int:
        return len(self.entries)

    @property
    def unique_route_count(self) -> int:
        return len({entry.key for entry in self.entries})

    @property
    def is_query_only(self) -> bool:
        return not self.mutation_entries and not self.duplicate_writer_routes


class ObsQueryBoundaryViolation(RuntimeError):
    def __init__(self, audit: ObsQueryBoundaryAudit) -> None:
        self.audit = audit
        mutation_refs = ", ".join(
            f"{entry.key.method} {entry.key.path} -> "
            f"{entry.endpoint_module}.{entry.endpoint_name}"
            for entry in audit.mutation_entries
        )
        duplicate_refs = ", ".join(
            f"{group.key.method} {group.key.path} x{len(group.entries)}"
            for group in audit.duplicate_writer_routes
        )
        detail = "; ".join(
            part
            for part in (
                f"mutation_entries=[{mutation_refs}]" if mutation_refs else "",
                f"duplicate_writer_routes=[{duplicate_refs}]" if duplicate_refs else "",
            )
            if part
        )
        super().__init__(f"OBS_QUERY_ONLY_BOUNDARY_VIOLATION: {detail}")


def _resolve_routes(target: Any) -> Iterable[Any]:
    routes = getattr(target, "routes", None)
    if routes is not None:
        return routes
    return target


def _classify_route_entry(
    *,
    key: HttpRouteKey,
    endpoint_identity: tuple[str, str],
) -> ObsRouteEntryKind:
    if (
        key in OBS_BASELINE_MUTATION_ROUTES
        or endpoint_identity in OBS_BASELINE_MUTATION_ENDPOINTS
    ):
        return ObsRouteEntryKind.KNOWN_MUTATION
    expected_query_endpoint = _PURE_QUERY_ENDPOINT_BY_ROUTE.get(key)
    if expected_query_endpoint is not None:
        if endpoint_identity == expected_query_endpoint:
            return ObsRouteEntryKind.PURE_QUERY_NON_GET
        return ObsRouteEntryKind.UNDECLARED_MUTATION
    if key.method in OBS_QUERY_METHODS:
        return ObsRouteEntryKind.QUERY
    return ObsRouteEntryKind.UNDECLARED_MUTATION


def audit_fastapi_route_entries(target: Any) -> ObsQueryBoundaryAudit:
    """Audit every HTTP route entry on a FastAPI app/router or route iterable."""

    entries: list[FastAPIRouteEntry] = []
    for route in _resolve_routes(target):
        if not isinstance(route, APIRoute):
            continue
        endpoint = route.endpoint
        endpoint_module = str(getattr(endpoint, "__module__", "") or "")
        endpoint_name = str(
            getattr(endpoint, "__name__", None)
            or getattr(endpoint, "__qualname__", None)
            or route.name
            or "<anonymous>"
        )
        identity = (endpoint_module, endpoint_name)
        for method in sorted(route.methods or ()):
            key = HttpRouteKey(method, route.path)
            entries.append(
                FastAPIRouteEntry(
                    entry_index=len(entries),
                    key=key,
                    endpoint_module=endpoint_module,
                    endpoint_name=endpoint_name,
                    route_name=str(route.name or endpoint_name),
                    kind=_classify_route_entry(
                        key=key,
                        endpoint_identity=identity,
                    ),
                )
            )

    grouped: dict[HttpRouteKey, list[FastAPIRouteEntry]] = {}
    for entry in entries:
        grouped.setdefault(entry.key, []).append(entry)
    duplicate_routes = tuple(
        DuplicateRouteEntries(key=key, entries=tuple(grouped_entries))
        for key, grouped_entries in sorted(grouped.items())
        if len(grouped_entries) > 1
    )
    mutation_entries = tuple(entry for entry in entries if entry.is_mutation)
    known_mutation_entries = tuple(
        entry
        for entry in mutation_entries
        if entry.kind is ObsRouteEntryKind.KNOWN_MUTATION
    )
    undeclared_mutation_entries = tuple(
        entry
        for entry in mutation_entries
        if entry.kind is ObsRouteEntryKind.UNDECLARED_MUTATION
    )
    pure_query_non_get_entries = tuple(
        entry
        for entry in entries
        if entry.kind is ObsRouteEntryKind.PURE_QUERY_NON_GET
    )
    duplicate_writer_routes = tuple(
        group for group in duplicate_routes if group.contains_writer
    )
    return ObsQueryBoundaryAudit(
        entries=tuple(entries),
        mutation_entries=mutation_entries,
        known_mutation_entries=known_mutation_entries,
        undeclared_mutation_entries=undeclared_mutation_entries,
        pure_query_non_get_entries=pure_query_non_get_entries,
        duplicate_routes=duplicate_routes,
        duplicate_writer_routes=duplicate_writer_routes,
    )


def require_query_only_routes(target: Any) -> ObsQueryBoundaryAudit:
    """Return the audit or raise when any HTTP mutation entry is registered."""

    audit = audit_fastapi_route_entries(target)
    if not audit.is_query_only:
        raise ObsQueryBoundaryViolation(audit)
    return audit


__all__ = [
    "FastAPIRouteEntry",
    "DuplicateRouteEntries",
    "HttpRouteKey",
    "MutationRouteSpec",
    "PureQueryRouteSpec",
    "OBS_BASELINE_MUTATION_ENDPOINTS",
    "OBS_BASELINE_MUTATION_ROUTES",
    "OBS_BASELINE_MUTATION_SPECS",
    "OBS_PURE_QUERY_NON_GET_ENDPOINTS",
    "OBS_PURE_QUERY_NON_GET_ROUTES",
    "OBS_PURE_QUERY_NON_GET_SPECS",
    "OBS_QUERY_METHODS",
    "ObsQueryBoundaryAudit",
    "ObsQueryBoundaryViolation",
    "ObsRouteEntryKind",
    "audit_fastapi_route_entries",
    "require_query_only_routes",
]
