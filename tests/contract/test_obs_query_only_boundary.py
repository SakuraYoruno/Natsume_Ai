from __future__ import annotations

from fastapi import FastAPI
import pytest

from host_brain.app.obs_fastapi_app import app as obs_app
from host_brain.app.obs_routes_stats_capability_agent import (
    preview_capability_forge_proposal,
)
from host_brain.app.obs_routes_stats_system import post_avatar_behavior_control
from host_brain.observability.query_boundary import (
    HttpRouteKey,
    OBS_BASELINE_MUTATION_ENDPOINTS,
    OBS_BASELINE_MUTATION_ROUTES,
    OBS_BASELINE_MUTATION_SPECS,
    OBS_PURE_QUERY_NON_GET_ENDPOINTS,
    OBS_PURE_QUERY_NON_GET_ROUTES,
    OBS_PURE_QUERY_NON_GET_SPECS,
    ObsQueryBoundaryViolation,
    ObsRouteEntryKind,
    audit_fastapi_route_entries,
    require_query_only_routes,
)


EXPECTED_PURE_QUERY_POSTS = {
    HttpRouteKey("POST", "/obs/capability-gaps/forge-proposals/preview"),
    HttpRouteKey("POST", "/obs/life-evolution/action-preview"),
    HttpRouteKey("POST", "/observability/trace"),
    HttpRouteKey("POST", "/obs/operator-session/bootstrap"),
}
EXPECTED_PURE_QUERY_ENDPOINTS = {
    (
        "host_brain.app.obs_routes_stats_capability_agent",
        "preview_capability_forge_proposal",
    ),
    (
        "host_brain.app.obs_routes_life_evolution",
        "post_life_evolution_action_preview",
    ),
    ("host_brain.app.obs_fastapi_app", "request_trace"),
    (
        "host_brain.observability.operator_session_bootstrap",
        "bootstrap_operator_session",
    ),
}

EXPECTED_BASELINE_MUTATION_ROUTES = {
    HttpRouteKey("POST", "/obs/avatar/behavior"),
    HttpRouteKey("POST", "/obs/wechat/reset"),
    HttpRouteKey("POST", "/obs/capability-gaps/forge-proposals"),
    HttpRouteKey("POST", "/obs/capability-gaps/forge-proposals/{proposal_id}/approve"),
    HttpRouteKey("POST", "/obs/capability-gaps/forge-proposals/{proposal_id}/rollback"),
    HttpRouteKey("POST", "/obs/capability-gaps/forge-proposals/runtime-match"),
    HttpRouteKey("POST", "/obs/sys-mode"),
    HttpRouteKey("PUT", "/obs/sys-mode/auto-rules"),
    HttpRouteKey("POST", "/obs/sys-mode/event"),
    HttpRouteKey("POST", "/obs/codex-bio-ops/daily-cycle/run"),
    HttpRouteKey("POST", "/obs/codex-bio-ops/adoptions/{candidate_id}/accept"),
    HttpRouteKey("POST", "/obs/codex-bio-ops/adoptions/{candidate_id}/reject"),
    HttpRouteKey("POST", "/obs/codex-bio-ops/owner-inbox/materialize"),
    HttpRouteKey("POST", "/obs/codex-bio-ops/owner-inbox/intake-packets"),
    HttpRouteKey("POST", "/obs/codex-bio-ops/owner-queues/consume"),
    HttpRouteKey("POST", "/obs/codex-bio-ops/owner-state/import"),
    HttpRouteKey("POST", "/obs/codex-bio-ops/owner-state/{candidate_id}/accept"),
    HttpRouteKey("POST", "/obs/codex-bio-ops/owner-state/{candidate_id}/reject"),
}

EXPECTED_DUPLICATE_QUERY_ROUTES: set[HttpRouteKey] = set()
EXPECTED_EFFECT_RECONCILIATION_QUERY_ROUTES = {
    HttpRouteKey("GET", "/obs/task-runtime/effect-reconciliation/candidates"),
    HttpRouteKey("GET", "/obs/task-runtime/effect-reconciliation/cases"),
    HttpRouteKey("GET", "/obs/task-runtime/effect-reconciliation/challenge"),
}
EXPECTED_ACCEPTANCE_EFFECT_RECONCILIATION_QUERY_ROUTES = {
    HttpRouteKey(
        "GET",
        "/obs/task-runtime/acceptance-effect-reconciliation/candidates",
    ),
    HttpRouteKey(
        "GET",
        "/obs/task-runtime/acceptance-effect-reconciliation/challenge",
    ),
}
EXPECTED_TERMINAL_RECONCILIATION_QUERY_ROUTES = {
    HttpRouteKey("GET", "/obs/task-runtime/terminal-reconciliation/candidates"),
    HttpRouteKey("GET", "/obs/task-runtime/terminal-reconciliation/cases"),
    HttpRouteKey("GET", "/obs/task-runtime/terminal-reconciliation/challenge"),
}


def test_task0489_baseline_manifest_covers_all_18_observed_mutation_handlers() -> None:
    assert len(OBS_BASELINE_MUTATION_SPECS) == 18
    assert OBS_BASELINE_MUTATION_ROUTES == frozenset(EXPECTED_BASELINE_MUTATION_ROUTES)
    assert len(OBS_BASELINE_MUTATION_ENDPOINTS) == 18
    assert OBS_BASELINE_MUTATION_ROUTES.isdisjoint(OBS_PURE_QUERY_NON_GET_ROUTES)
    assert len(OBS_PURE_QUERY_NON_GET_SPECS) == 4
    assert OBS_PURE_QUERY_NON_GET_ENDPOINTS == frozenset(EXPECTED_PURE_QUERY_ENDPOINTS)


def test_actual_obs_fastapi_route_entries_are_query_only_after_writer_detach() -> None:
    audit = audit_fastapi_route_entries(obs_app)

    # Each query route has one canonical owner after the split-router cutover.
    assert audit.entry_count == 114
    assert audit.unique_route_count == 114
    assert EXPECTED_EFFECT_RECONCILIATION_QUERY_ROUTES <= {
        entry.key for entry in audit.entries
    }
    assert EXPECTED_ACCEPTANCE_EFFECT_RECONCILIATION_QUERY_ROUTES <= {
        entry.key for entry in audit.entries
    }
    assert EXPECTED_TERMINAL_RECONCILIATION_QUERY_ROUTES <= {
        entry.key for entry in audit.entries
    }
    assert {entry.key for entry in audit.pure_query_non_get_entries} == EXPECTED_PURE_QUERY_POSTS
    assert OBS_PURE_QUERY_NON_GET_ROUTES == frozenset(EXPECTED_PURE_QUERY_POSTS)
    assert {group.key for group in audit.duplicate_routes} == EXPECTED_DUPLICATE_QUERY_ROUTES
    assert audit.mutation_entries == ()
    assert audit.known_mutation_entries == ()
    assert audit.undeclared_mutation_entries == ()
    assert audit.duplicate_writer_routes == ()
    assert audit.is_query_only is True
    assert require_query_only_routes(obs_app) is not None


def test_known_writer_identity_is_detected_even_if_reattached_as_get() -> None:
    app = FastAPI()
    app.add_api_route(
        "/accidental-query",
        post_avatar_behavior_control,
        methods=["GET"],
    )

    audit = audit_fastapi_route_entries(app)

    assert len(audit.known_mutation_entries) == 1
    assert audit.known_mutation_entries[0].kind is ObsRouteEntryKind.KNOWN_MUTATION
    assert audit.known_mutation_entries[0].key == HttpRouteKey("GET", "/accidental-query")
    with pytest.raises(ObsQueryBoundaryViolation, match="OBS_QUERY_ONLY_BOUNDARY_VIOLATION"):
        require_query_only_routes(app)


def test_undeclared_non_query_method_and_duplicate_writer_fail_closed() -> None:
    app = FastAPI()

    @app.post("/new-writer")
    def first_writer() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/new-writer")
    def second_writer() -> dict[str, bool]:
        return {"ok": True}

    audit = audit_fastapi_route_entries(app)

    assert len(audit.undeclared_mutation_entries) == 2
    assert all(
        entry.kind is ObsRouteEntryKind.UNDECLARED_MUTATION
        for entry in audit.undeclared_mutation_entries
    )
    assert len(audit.duplicate_writer_routes) == 1
    duplicate = audit.duplicate_writer_routes[0]
    assert duplicate.key == HttpRouteKey("POST", "/new-writer")
    assert len(duplicate.entries) == 2
    with pytest.raises(ObsQueryBoundaryViolation, match="duplicate_writer_routes"):
        require_query_only_routes(app)


def test_pure_query_post_path_rejects_replacement_endpoint() -> None:
    app = FastAPI()

    @app.post("/obs/capability-gaps/forge-proposals/preview")
    def replacement_preview() -> dict[str, bool]:
        return {"ok": True}

    audit = audit_fastapi_route_entries(app)

    assert audit.pure_query_non_get_entries == ()
    assert len(audit.undeclared_mutation_entries) == 1
    assert audit.undeclared_mutation_entries[0].endpoint_name == "replacement_preview"
    with pytest.raises(ObsQueryBoundaryViolation, match="UNDECLARED_MUTATION|mutation_entries"):
        require_query_only_routes(app)


def test_duplicate_or_mixed_pure_query_post_registration_fails_closed() -> None:
    key = HttpRouteKey("POST", "/obs/capability-gaps/forge-proposals/preview")

    duplicate_canonical = FastAPI()
    for _ in range(2):
        duplicate_canonical.add_api_route(
            key.path,
            preview_capability_forge_proposal,
            methods=[key.method],
        )
    duplicate_audit = audit_fastapi_route_entries(duplicate_canonical)
    assert duplicate_audit.mutation_entries == ()
    assert len(duplicate_audit.duplicate_writer_routes) == 1
    assert duplicate_audit.duplicate_writer_routes[0].key == key
    with pytest.raises(ObsQueryBoundaryViolation, match="duplicate_writer_routes"):
        require_query_only_routes(duplicate_canonical)

    mixed = FastAPI()
    mixed.add_api_route(key.path, preview_capability_forge_proposal, methods=[key.method])

    @mixed.post(key.path)
    def second_endpoint() -> dict[str, bool]:
        return {"ok": True}

    mixed_audit = audit_fastapi_route_entries(mixed)
    assert len(mixed_audit.undeclared_mutation_entries) == 1
    assert len(mixed_audit.duplicate_writer_routes) == 1
    assert mixed_audit.duplicate_writer_routes[0].key == key
    with pytest.raises(ObsQueryBoundaryViolation, match="duplicate_writer_routes"):
        require_query_only_routes(mixed)
