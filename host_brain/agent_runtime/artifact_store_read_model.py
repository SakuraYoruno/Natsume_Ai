"""Focused read-side aggregation for governed Agent artifacts.

The canonical filesystem authority and all mutation primitives remain owned by
``AgentArtifactStore``.  This module only composes its verified reopen helpers
into bounded inventory, promotion, continuation, and tombstone read models.
"""

from __future__ import annotations

import re
from typing import Any

_ACCEPTED_REF = re.compile(
    r"^agent-artifact://([A-Za-z0-9._-]{1,120})/"
    r"([A-Za-z0-9._-]{1,240})/sha256/([0-9a-f]{64})$"
)
_DURABLE_REF = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://"
    r"[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{0,1023}$"
)


def _artifact_store_module() -> Any:
    # Imported lazily to avoid an artifact_store import cycle.
    from . import artifact_store

    return artifact_store


def inventory_governed_seals(
    store: Any,
    *,
    namespace: str,
    limit: int = 100,
    after_stage_ref: str | None = None,
) -> tuple[Any, ...]:
    """Return a bounded, fully verified, read-only seal inventory."""

    core = _artifact_store_module()
    authority_namespace = str(namespace or "").strip().lower()
    if authority_namespace not in {"staging", "accepted"}:
        raise core.GovernedAgentArtifactError(
            "inventory namespace must be staging or accepted"
        )
    if isinstance(limit, bool) or type(limit) is not int or not 1 <= limit <= 1000:
        raise core.GovernedAgentArtifactError(
            "inventory limit must be within [1, 1000]"
        )
    cursor = str(after_stage_ref or "").strip()
    if cursor and core._STAGE_REF.fullmatch(cursor) is None:
        raise core.GovernedAgentArtifactError("inventory cursor is invalid")

    governed_root = store._governed_root()
    if not core._path_lexists(governed_root):
        return ()
    core._require_regular_directory(governed_root, root=store.runs_root)
    for child in governed_root.iterdir():
        if child.name not in {"staging", "accepted", "abandoned"}:
            raise core.GovernedAgentArtifactError(
                "governed artifact root contains an unknown namespace"
            )
        core._require_regular_directory(child, root=store.runs_root)

    authority_root = governed_root / authority_namespace
    if not core._path_lexists(authority_root):
        return ()
    core._require_regular_directory(authority_root, root=store.runs_root)
    candidates: list[tuple[str, Any]] = []
    allowed_attempt_files = {
        "owner.json",
        *core._SEALED_FILES,
        core._PROJECTION_RECOVERY_FILE,
        *core._WORKSPACE_WRITE_FILES,
        "seal.json",
        "promotion.json",
        ".tmp",
    }
    for workspace_dir in authority_root.iterdir():
        core._portable_segment(
            workspace_dir.name,
            field="workspace_id",
            maximum=120,
        )
        core._require_regular_directory(workspace_dir, root=store.runs_root)
        for attempt_dir in workspace_dir.iterdir():
            if re.fullmatch(r"attempt-[0-9a-f]{32}", attempt_dir.name) is None:
                raise core.GovernedAgentArtifactError(
                    "governed inventory contains an invalid attempt directory"
                )
            core._require_regular_directory(attempt_dir, root=store.runs_root)
            entries = list(attempt_dir.iterdir())
            names: set[str] = set()
            for entry in entries:
                if (
                    entry.name not in allowed_attempt_files
                    or not core._is_regular_file_without_reparse(entry)
                ):
                    raise core.GovernedAgentArtifactError(
                        "governed inventory contains unknown attempt material"
                    )
                names.add(entry.name)
            if "owner.json" not in names:
                raise core.GovernedAgentArtifactError(
                    "governed inventory attempt has no owner"
                )
            if "promotion.json" in names and "seal.json" not in names:
                raise core.GovernedAgentArtifactError(
                    "governed inventory promotion has no seal"
                )
            if authority_namespace == "accepted" and not {
                "seal.json",
                "promotion.json",
            }.issubset(names):
                raise core.GovernedAgentArtifactError(
                    "accepted inventory contains an incomplete artifact"
                )
            owner = core._read_json_object(
                attempt_dir / "owner.json",
                field="stage owner",
            )
            stage_ref = str(owner.get("stage_ref") or "").strip()
            loaded = store._load_stage(stage_ref, allow_accepted=True)
            if loaded.stage_dir != attempt_dir:
                raise core.GovernedAgentArtifactError(
                    "governed inventory owner resolved to another authority"
                )
            if "seal.json" in names:
                candidates.append((stage_ref, attempt_dir))

    seals: list[Any] = []
    for stage_ref, expected_dir in sorted(candidates):
        if cursor and stage_ref <= cursor:
            continue
        seal = store.inspect_governed_stage(
            stage_ref=stage_ref,
            seal_digest=store._sealed_digest_for_ref(stage_ref),
            discard_stale_temp=False,
        )
        if seal.stage_dir != expected_dir or (
            authority_namespace == "accepted"
        ) != (seal.accepted_dir is not None):
            raise core.GovernedAgentArtifactError(
                "governed inventory namespace identity drifted"
            )
        if authority_namespace == "accepted":
            store.inspect_governed_promotion(
                stage_ref=seal.stage_ref,
                seal_digest=seal.seal_digest,
            )
        seals.append(seal)
        if len(seals) >= limit:
            break
    return tuple(seals)


def inventory_unsealed_governed_stages(
    store: Any,
    *,
    limit: int = 100,
    after_stage_ref: str | None = None,
) -> tuple[Any, ...]:
    """Return exact unsealed staging candidates without taking mutation authority."""

    core = _artifact_store_module()
    if isinstance(limit, bool) or type(limit) is not int or not 1 <= limit <= 1000:
        raise core.GovernedAgentArtifactError(
            "inventory limit must be within [1, 1000]"
        )
    cursor = str(after_stage_ref or "").strip()
    if cursor and core._STAGE_REF.fullmatch(cursor) is None:
        raise core.GovernedAgentArtifactError("inventory cursor is invalid")
    governed_root = store._governed_root()
    if not core._path_lexists(governed_root):
        return ()
    core._require_regular_directory(governed_root, root=store.runs_root)
    for child in governed_root.iterdir():
        if child.name not in {"staging", "accepted", "abandoned"}:
            raise core.GovernedAgentArtifactError(
                "governed artifact root contains an unknown namespace"
            )
        core._require_regular_directory(child, root=store.runs_root)
    staging_root = governed_root / "staging"
    if not core._path_lexists(staging_root):
        return ()
    core._require_regular_directory(staging_root, root=store.runs_root)
    allowed = {
        "owner.json",
        *core._SEALED_FILES,
        core._PROJECTION_RECOVERY_FILE,
        *core._WORKSPACE_WRITE_FILES,
        "seal.json",
        "promotion.json",
        ".tmp",
    }
    candidates: list[tuple[str, Any]] = []
    for workspace_dir in staging_root.iterdir():
        core._portable_segment(
            workspace_dir.name,
            field="workspace_id",
            maximum=120,
        )
        core._require_regular_directory(workspace_dir, root=store.runs_root)
        for attempt_dir in workspace_dir.iterdir():
            if re.fullmatch(r"attempt-[0-9a-f]{32}", attempt_dir.name) is None:
                raise core.GovernedAgentArtifactError(
                    "governed inventory contains an invalid attempt directory"
                )
            core._require_regular_directory(attempt_dir, root=store.runs_root)
            names: set[str] = set()
            for entry in attempt_dir.iterdir():
                if entry.name not in allowed or not core._is_regular_file_without_reparse(entry):
                    raise core.GovernedAgentArtifactError(
                        "governed inventory contains unknown attempt material"
                    )
                names.add(entry.name)
            if "owner.json" not in names:
                raise core.GovernedAgentArtifactError(
                    "governed inventory attempt has no owner"
                )
            if "promotion.json" in names and "seal.json" not in names:
                raise core.GovernedAgentArtifactError(
                    "governed inventory promotion has no seal"
                )
            if "seal.json" in names:
                continue
            owner = core._read_json_object(
                attempt_dir / "owner.json",
                field="stage owner",
            )
            stage_ref = str(owner.get("stage_ref") or "").strip()
            stage = store._load_stage(stage_ref)
            if stage.stage_dir != attempt_dir:
                raise core.GovernedAgentArtifactError(
                    "governed inventory owner resolved to another authority"
                )
            candidates.append((stage_ref, stage))
    return tuple(
        stage
        for stage_ref, stage in sorted(candidates)
        if not cursor or stage_ref > cursor
    )[:limit]


def inspect_governed_promotion(
    store: Any,
    *,
    stage_ref: str,
    seal_digest: str,
) -> dict[str, str]:
    """Reopen one exact accepted promotion without mutating storage."""

    core = _artifact_store_module()
    seal = store.inspect_governed_stage(
        stage_ref=stage_ref,
        seal_digest=seal_digest,
        discard_stale_temp=False,
    )
    if seal.accepted_dir is None:
        raise core.GovernedAgentArtifactError(
            "promotion inspection requires accepted material"
        )
    record = core._read_json_object(
        seal.stage_dir / "promotion.json",
        field="stage promotion",
    )
    expected_ref = (
        f"agent-artifact://{seal.workspace_id}/{seal.attempt_id}/sha256/"
        f"{seal.seal_digest.split(':', 1)[1]}"
    )
    acceptance_ref = str(record.get("acceptance_ref") or "").strip()
    expected = {
        "schema_version": core.GOVERNED_AGENT_STAGE_SCHEMA,
        "stage_ref": seal.stage_ref,
        "seal_digest": seal.seal_digest,
        "accepted_ref": expected_ref,
        "acceptance_ref": acceptance_ref,
    }
    accepted_match = _ACCEPTED_REF.fullmatch(expected_ref)
    if (
        record != expected
        or accepted_match is None
        or _DURABLE_REF.fullmatch(acceptance_ref) is None
    ):
        raise core.GovernedAgentArtifactError("stage promotion attribution drifted")
    return expected


def inspect_governed_stage_for_authority(
    store: Any,
    *,
    workspace_id: str,
    execution_authority: dict[str, Any],
) -> Any:
    """Reopen the exact sealed stage derived from immutable Task authority."""

    core = _artifact_store_module()
    workspace = core._portable_segment(
        workspace_id,
        field="workspace_id",
        maximum=120,
    )
    authority = core._canonical_authority(execution_authority)
    expected_workspace = core._workspace_id_for(
        graph_id=authority["graph_id"],
        graph_revision=authority["graph_revision"],
    )
    if workspace != expected_workspace:
        raise core.GovernedAgentArtifactError(
            "stage workspace differs from the canonical graph epoch"
        )
    binding = {
        "schema_version": core.GOVERNED_AGENT_STAGE_SCHEMA,
        "workspace_id": workspace,
        "execution_authority": authority,
    }
    binding_digest = core._canonical_digest(binding)
    stage_ref = (
        f"agent-stage://{workspace}/{core._attempt_segment(authority['attempt_id'])}/"
        f"sha256/{binding_digest.split(':', 1)[1]}"
    )
    return store.inspect_governed_stage(
        stage_ref=stage_ref,
        seal_digest=store._sealed_digest_for_ref(stage_ref),
    )


def read_projection_recovery(store: Any, seal: Any) -> dict[str, Any]:
    """Read the seal-bound recovery envelope; legacy seals fail closed."""

    core = _artifact_store_module()
    reopened = store.inspect_governed_stage(
        stage_ref=seal.stage_ref,
        seal_digest=seal.seal_digest,
    )
    if (
        reopened.execution_authority != seal.execution_authority
        or reopened.stage_dir != seal.stage_dir
    ):
        raise core.GovernedAgentArtifactError(
            "projection recovery stage identity drifted"
        )
    path = reopened.stage_dir / core._PROJECTION_RECOVERY_FILE
    if not core._path_lexists(path):
        raise core.GovernedAgentArtifactError(
            "sealed stage has no projection recovery envelope"
        )
    return core._read_json_object(path, field="projection recovery envelope")


def read_sealed_json(store: Any, seal: Any, relative: str) -> dict[str, Any]:
    """Read one fixed protected JSON member after revalidating the seal."""

    core = _artifact_store_module()
    name = str(relative or "").strip()
    if name not in {
        *core._SEALED_FILES,
        core._PROJECTION_RECOVERY_FILE,
    } or not name.endswith(".json"):
        raise core.GovernedAgentArtifactError("sealed JSON member is not allowed")
    reopened = store.inspect_governed_stage(
        stage_ref=seal.stage_ref,
        seal_digest=seal.seal_digest,
    )
    return core._read_json_object(
        reopened.stage_dir / name,
        field=f"sealed {name}",
    )


def sealed_digest_for_ref(store: Any, stage_ref: str) -> str:
    core = _artifact_store_module()
    stage = store._load_stage(stage_ref, allow_accepted=True)
    seal = core._read_json_object(stage.stage_dir / "seal.json", field="stage seal")
    digest = str(seal.get("seal_digest") or "").strip().lower()
    if not core._DIGEST.fullmatch(digest):
        raise core.GovernedAgentArtifactError("stage seal digest is invalid")
    return digest


def plan_governed_abandonment(
    store: Any,
    *,
    stage_ref: str,
    seal_digest: str,
    reason_code: str,
) -> str:
    """Return an exact ref without deleting material or writing authority."""

    core = _artifact_store_module()
    workspace, attempt_id, expected = core._governed_tombstone_record(
        stage_ref=stage_ref,
        seal_digest=seal_digest,
        reason_code=reason_code,
    )
    store._guard_authority_paths(workspace, attempt_id)
    if core._path_lexists(store._accepted_dir(workspace, attempt_id)):
        raise core.GovernedAgentArtifactError("accepted artifact cannot be abandoned")
    tombstone_path = store._tombstone_path(workspace, attempt_id)
    if core._path_lexists(tombstone_path):
        existing = core._validate_tombstone(
            core._read_json_object(tombstone_path, field="stage tombstone")
        )
        if existing != expected:
            raise core.GovernedAgentArtifactError(
                "stage tombstone attribution drifted"
            )
        return str(existing["tombstone_ref"])
    seal = store.inspect_governed_stage(
        stage_ref=stage_ref,
        seal_digest=seal_digest,
    )
    if seal.accepted_dir is not None:
        raise core.GovernedAgentArtifactError("accepted artifact cannot be abandoned")
    return str(expected["tombstone_ref"])


def inspect_governed_tombstone(
    store: Any,
    *,
    stage_ref: str,
    seal_digest: str,
    reason_code: str,
) -> str:
    """Reopen an exact abandonment tombstone without mutating storage."""

    core = _artifact_store_module()
    workspace, attempt_id, expected = core._governed_tombstone_record(
        stage_ref=stage_ref,
        seal_digest=seal_digest,
        reason_code=reason_code,
    )
    store._guard_authority_paths(workspace, attempt_id)
    if core._path_lexists(store._stage_dir(workspace, attempt_id)):
        raise core.GovernedAgentArtifactError(
            "abandonment tombstone still has staging material"
        )
    if core._path_lexists(store._accepted_dir(workspace, attempt_id)):
        raise core.GovernedAgentArtifactError(
            "abandonment tombstone conflicts with accepted material"
        )
    tombstone_path = store._tombstone_path(workspace, attempt_id)
    core._require_regular_file(tombstone_path, root=store.runs_root)
    actual = core._validate_tombstone(
        core._read_json_object(tombstone_path, field="stage tombstone")
    )
    if actual != expected:
        raise core.GovernedAgentArtifactError("stage tombstone attribution drifted")
    return str(actual["tombstone_ref"])


def inspect_governed_tombstone_ref(
    store: Any,
    tombstone_ref: str,
) -> dict[str, str]:
    """Resolve a tombstone reference into its canonical read-only proof."""

    core = _artifact_store_module()
    reference = str(tombstone_ref or "").strip()
    match = core._TOMBSTONE_REF.fullmatch(reference)
    if match is None:
        raise core.GovernedAgentArtifactError("tombstone_ref is invalid")
    workspace, attempt_id, _ = match.groups()
    store._guard_authority_paths(workspace, attempt_id)
    if core._path_lexists(store._stage_dir(workspace, attempt_id)):
        raise core.GovernedAgentArtifactError(
            "abandonment tombstone still has staging material"
        )
    if core._path_lexists(store._accepted_dir(workspace, attempt_id)):
        raise core.GovernedAgentArtifactError(
            "abandonment tombstone conflicts with accepted material"
        )
    tombstone_path = store._tombstone_path(workspace, attempt_id)
    core._require_regular_file(tombstone_path, root=store.runs_root)
    actual = core._validate_tombstone(
        core._read_json_object(tombstone_path, field="stage tombstone")
    )
    if str(actual.get("tombstone_ref") or "") != reference:
        raise core.GovernedAgentArtifactError(
            "stage tombstone reference drifted"
        )
    return {
        "stage_ref": str(actual["stage_ref"]),
        "seal_digest": str(actual["seal_digest"]),
        "reason_code": str(actual["reason_code"]),
        "tombstone_ref": reference,
    }


__all__ = [
    "inspect_governed_promotion",
    "inspect_governed_stage_for_authority",
    "inspect_governed_tombstone",
    "inspect_governed_tombstone_ref",
    "inventory_governed_seals",
    "plan_governed_abandonment",
    "read_projection_recovery",
    "read_sealed_json",
    "sealed_digest_for_ref",
]
