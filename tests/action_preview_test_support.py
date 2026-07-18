from __future__ import annotations

from pathlib import Path
from typing import Any

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.governor.action_preview import ActionPreviewService
from host_brain.integrations.runtime_events import RuntimeEventStore
from host_brain.persistence.embodiment_outbox_repository import (
    EmbodimentOutboxRepository,
)
from host_brain.runtime.action_preview_authority import (
    require_scheduler_derived_action,
)
from host_brain.runtime.action_preview_resolver import (
    DERIVED_EFFECT_AUTHORITY_SCHEMA,
    EFFECT_DESCRIPTOR_SCHEMA,
    NODE_AUTHORITY_SCHEMA,
    ResolvedActionPreviewAuthority,
)
from host_brain.runtime.runtime_guardian import RuntimeGuardian
from natsume_contracts.edge.embodiment_v1 import EmbodimentCommand


ACTION_PREVIEW_TEST_NOW_MS = 1_900_000_000_000


class _StaticSchedulerAuthorityResolver:
    """Test-only canonical authority source backed by the real durable service."""

    def resolve(self, **kwargs: Any) -> ResolvedActionPreviewAuthority:
        context = dict(kwargs["authority_context"])
        derived = context.get("schema_version") == DERIVED_EFFECT_AUTHORITY_SCHEMA
        descriptors = () if derived else (
            {
                "schema_version": EFFECT_DESCRIPTOR_SCHEMA,
                "action_kind": "scheduler.life_tick",
                "scope_mode": "EXACT_RUNTIME_VALUE",
                "max_risk_level": 1,
                "guardian_capability_id": "scheduler_life_tick",
                "scope_max_length": 512,
                "args_max_bytes": 65_536,
                "forbidden_arg_key_fragments": [
                    "authorization",
                    "credential",
                    "secret",
                    "token",
                ],
            },
        )
        return ResolvedActionPreviewAuthority(
            authority_kind="DERIVED_EFFECT" if derived else "TASK_NODE",
            action_kind=kwargs["action_kind"],
            action_scope=kwargs["action_scope"],
            risk_level=kwargs["risk_level"],
            guardian_capability_id=kwargs["guardian_capability_id"],
            context_pack_digest=kwargs["context_pack_digest"],
            scene_identity=kwargs["scene_identity"],
            task_graph_id=kwargs["task_graph_id"],
            graph_revision=kwargs["graph_revision"],
            task_node_id=kwargs["task_node_id"],
            parent_governor_grant_ref=kwargs["parent_governor_grant_ref"],
            node_grant_ref=kwargs["node_grant_ref"],
            node_grant=kwargs["node_grant"],
            expires_at_ms=ACTION_PREVIEW_TEST_NOW_MS + 60_000,
            binding={
                "schema_version": "Natsume.action_preview.authority_binding.v1",
                "authority_kind": "DERIVED_EFFECT" if derived else "TASK_NODE",
                "context": context,
                "origin_receipt_digest": str(
                    dict(kwargs.get("origin_receipt") or {}).get(
                        "receipt_digest",
                        "",
                    )
                ),
            },
            effect_descriptors=descriptors,
            origin_receipt_ref=str(context.get("origin_receipt_ref") or ""),
        )


def build_scheduler_action_preview(
    tmp_path: Path,
    *,
    name: str,
) -> tuple[ActionPreviewService, dict[str, Any]]:
    """Build a real SQLite Preview service and one exact Scheduler origin."""

    safe_name = str(name).strip().replace("/", "-").replace("\\", "-")
    db_path = tmp_path / f"{safe_name}-action-preview.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id=f"preview-support-{safe_name}",
        ts_ms=ACTION_PREVIEW_TEST_NOW_MS,
    )
    assert migrated.success, migrated.as_dict()

    scope = f"runtime-scope-preview-{safe_name}"
    worker_id = f"preview-owner-{safe_name}"
    repository = EmbodimentOutboxRepository(db_path, runtime_scope_id=scope)
    lease = repository.acquire_runtime_lease(
        worker_id=worker_id,
        lease_ms=600_000,
        now_ms=ACTION_PREVIEW_TEST_NOW_MS,
    )
    assert lease is not None

    def authority_provider() -> dict[str, Any]:
        authority = repository.validate_runtime_lease(
            worker_id=str(lease["owner_id"]),
            lease_id=str(lease["lease_id"]),
            fencing_token=int(lease["fencing_token"]),
            now_ms=ACTION_PREVIEW_TEST_NOW_MS,
        )
        assert authority is not None
        return {**authority, "runtime_scope_id": scope}

    store = RuntimeEventStore(
        db_path,
        clock=lambda: ACTION_PREVIEW_TEST_NOW_MS,
    )
    guardian = RuntimeGuardian(
        event_store=store,
        runtime_scope_id=scope,
        runtime_authority_provider=authority_provider,
        clock=lambda: ACTION_PREVIEW_TEST_NOW_MS,
    )
    service = ActionPreviewService(
        runtime_event_store=store,
        guardian=guardian,
        authority_resolver=_StaticSchedulerAuthorityResolver(),
        clock=lambda: ACTION_PREVIEW_TEST_NOW_MS,
    )

    scene_identity = {
        "schema": "natsume.scene-identity.v2",
        "workspace_id": f"workspace-{safe_name}",
        "subject_id": f"subject-{safe_name}",
        "scene_id": "release-maintenance",
        "revision": 1,
        "decision_digest": "sha256:" + "3" * 64,
    }
    graph_id = f"graph-{safe_name}"
    node_id = f"node-{safe_name}"
    node_grant_ref = f"governor-grant://preview-child/{safe_name}"
    boundary = {
        "action_kind": "scheduler.life_tick",
        "action_scope": "life_command",
        "risk_level": 1,
        "action_args": {"occurrence_digest": "sha256:" + "1" * 64},
        "context_pack_digest": "sha256:" + "2" * 64,
        "scene_identity": scene_identity,
        "task_graph_id": graph_id,
        "graph_revision": 1,
        "task_node_id": node_id,
        "parent_governor_grant_ref": f"governor-grant://preview-parent/{safe_name}",
        "node_grant_ref": node_grant_ref,
        "node_grant": {
            "grant_ref": node_grant_ref,
            "graph_id": graph_id,
            "graph_revision": 1,
            "node_id": node_id,
        },
        "authority_context": {
            "schema_version": NODE_AUTHORITY_SCHEMA,
            "purpose": "scheduler.execute",
        },
    }
    issued = service.issue_receipt(
        **boundary,
        guardian_capability_id="scheduler_life_tick",
    )
    return service, {
        "schema_version": "Natsume.scheduler.action_preview_context.v1",
        "origin_event_id": issued["event_id"],
        "origin_receipt_ref": issued["receipt_ref"],
        "origin_boundary": boundary,
        "node_grant_expires_at_ms": ACTION_PREVIEW_TEST_NOW_MS + 60_000,
    }


def issue_embodiment_action_preview(
    *,
    service: ActionPreviewService,
    scheduler_context: dict[str, Any],
    command: EmbodimentCommand,
) -> dict[str, Any]:
    descriptor = {
        ("avatar", "set_expression"): (
            "embodiment.vts.set_expression",
            "embodiment_vts_expression",
        ),
        ("mateengine", "command"): (
            "embodiment.mateengine.command",
            "embodiment_mateengine_command",
        ),
    }.get((str(command.kind), str(command.action)))
    if descriptor is None:
        raise ValueError("test Preview helper only supports governed VTS/MateEngine actions")
    action_kind, capability_id = descriptor
    return require_scheduler_derived_action(
        service=service,
        preview_context=scheduler_context,
        action_kind=action_kind,
        action_scope=str(command.idempotency_key),
        risk_level=4,
        action_args={
            "delivery_key": str(command.idempotency_key),
            "command": command.as_dict(),
        },
        guardian_capability_id=capability_id,
    )


def terminal_preview_kwargs(
    service: ActionPreviewService,
    issued: dict[str, Any],
) -> dict[str, Any]:
    return {
        "action_preview_service": service,
        "action_preview_event_id": issued["event_id"],
        "action_preview_boundary": issued["_terminal_execution_boundary"],
    }


__all__ = [
    "build_scheduler_action_preview",
    "issue_embodiment_action_preview",
    "terminal_preview_kwargs",
]
