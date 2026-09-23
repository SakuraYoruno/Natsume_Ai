from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from host_brain.agent_runtime.artifact_store import (
    AgentArtifactStore,
    GovernedAgentArtifactError,
)


def _authority(*, attempt_id: str) -> dict[str, object]:
    return {
        "root_work_order_id": "work-order-cleanup-read-model",
        "graph_id": "graph-cleanup-read-model",
        "graph_revision": 1,
        "node_id": "node-cleanup-read-model",
        "attempt_id": attempt_id,
        "lease_id": f"lease-{attempt_id}",
        "command_id": f"command-{attempt_id}",
        "principal_id": "principal-cleanup-read-model",
        "lease_owner_id": "owner-cleanup-read-model",
        "fencing_token": 7,
    }


def _workspace_id(authority: dict[str, object]) -> str:
    digest = hashlib.sha256(
        f"{authority['graph_id']}\n{authority['graph_revision']}".encode("utf-8")
    ).hexdigest()[:48]
    return f"workspace-{digest}"


def _sealed_stage(
    store: AgentArtifactStore,
    *,
    authority: dict[str, object],
):
    stage = store.prepare_governed_stage(
        workspace_id=_workspace_id(authority),
        execution_authority=authority,
    )
    store.write_json(stage.stage_dir / "work_order.json", {"objective": "private"})
    store.write_text(stage.stage_dir / "prompt.txt", "private prompt")
    store.write_json(stage.stage_dir / "metadata.json", {"safe": True})
    store.write_json(stage.stage_dir / "result.json", {"status": "EXECUTED"})
    store.write_json(stage.stage_dir / "changed_files.json", {"changed_files": []})
    store.write_json(stage.stage_dir / "verification.json", {"status": "PASS"})
    return store.seal_governed_stage(stage)


def _file_snapshot(root: Path) -> tuple[tuple[str, bytes], ...]:
    return tuple(
        sorted(
            (path.relative_to(root).as_posix(), path.read_bytes())
            for path in root.rglob("*")
            if path.is_file()
        )
    )


def test_read_model_reopen_paths_keep_staging_and_accepted_seals_read_only(
    tmp_path: Path,
) -> None:
    authority = _authority(attempt_id="attempt-cleanup-read-model")
    store = AgentArtifactStore(tmp_path / "runs")
    seal = _sealed_stage(store, authority=authority)

    staging_before = _file_snapshot(store.runs_root)
    staging_inventory = store.inventory_governed_seals(namespace="staging")
    staging_by_authority = store.inspect_governed_stage_for_authority(
        workspace_id=seal.workspace_id,
        execution_authority=authority,
    )

    assert staging_inventory == (seal,)
    assert staging_by_authority == seal
    assert _file_snapshot(store.runs_root) == staging_before

    accepted_ref = store.promote_governed_stage(
        stage_ref=seal.stage_ref,
        seal_digest=seal.seal_digest,
        acceptance_ref="acceptance://cleanup/read-model",
    )
    assert accepted_ref.startswith("agent-artifact://")

    accepted_before = _file_snapshot(store.runs_root)
    accepted_inventory = store.inventory_governed_seals(namespace="accepted")
    accepted_by_authority = store.inspect_governed_stage_for_authority(
        workspace_id=seal.workspace_id,
        execution_authority=authority,
    )

    assert accepted_inventory[0].seal_digest == seal.seal_digest
    assert accepted_by_authority.seal_digest == seal.seal_digest
    assert accepted_by_authority.accepted_dir is not None
    assert _file_snapshot(store.runs_root) == accepted_before


@pytest.mark.parametrize("corruption", ("missing", "invalid_json", "invalid_digest"))
def test_read_model_digest_rejections_leave_files_untouched(
    tmp_path: Path,
    corruption: str,
) -> None:
    authority = _authority(attempt_id=f"attempt-cleanup-{corruption}")
    store = AgentArtifactStore(tmp_path / "runs")
    seal = _sealed_stage(store, authority=authority)
    seal_path = seal.stage_dir / "seal.json"

    if corruption == "missing":
        seal_path.unlink()
    elif corruption == "invalid_json":
        seal_path.write_text("{", encoding="utf-8")
    else:
        payload = json.loads(seal_path.read_text(encoding="utf-8"))
        payload["seal_digest"] = "not-a-digest"
        seal_path.write_text(json.dumps(payload), encoding="utf-8")

    before = _file_snapshot(store.runs_root)
    with pytest.raises(GovernedAgentArtifactError):
        store.inspect_governed_stage_for_authority(
            workspace_id=seal.workspace_id,
            execution_authority=authority,
        )
    assert _file_snapshot(store.runs_root) == before


def test_read_model_rejects_owner_workspace_drift_without_writing(
    tmp_path: Path,
) -> None:
    authority = _authority(attempt_id="attempt-cleanup-owner-drift")
    store = AgentArtifactStore(tmp_path / "runs")
    seal = _sealed_stage(store, authority=authority)
    owner_path = seal.stage_dir / "owner.json"
    owner = json.loads(owner_path.read_text(encoding="utf-8"))
    owner["workspace_id"] = "workspace-other"
    owner_path.write_text(json.dumps(owner), encoding="utf-8")

    before = _file_snapshot(store.runs_root)
    with pytest.raises(GovernedAgentArtifactError, match="attribution drifted"):
        store.inspect_governed_stage_for_authority(
            workspace_id=seal.workspace_id,
            execution_authority=authority,
        )
    assert _file_snapshot(store.runs_root) == before
