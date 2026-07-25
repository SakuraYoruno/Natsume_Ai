from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from host_brain.agent_runtime.artifact_store import (
    AgentArtifactStore,
    GovernedAgentArtifactError,
)


def _authority(*, attempt_id: str = "attempt-internal-stage") -> dict[str, object]:
    return {
        "root_work_order_id": "work-order-internal-stage",
        "graph_id": "graph-internal-stage",
        "graph_revision": 1,
        "node_id": "node-internal-stage",
        "attempt_id": attempt_id,
        "lease_id": f"lease-{attempt_id}",
        "command_id": f"command-{attempt_id}",
        "principal_id": "principal-internal-stage",
        "lease_owner_id": "owner-internal-stage",
        "fencing_token": 7,
    }


def _workspace_id(authority: dict[str, object]) -> str:
    digest = hashlib.sha256(
        f"{authority['graph_id']}\n{authority['graph_revision']}".encode()
    ).hexdigest()[:48]
    return f"workspace-{digest}"


def _attempt_path_segment(attempt_id: str) -> str:
    return "attempt-" + hashlib.sha256(attempt_id.encode()).hexdigest()[:32]


def _symlink_or_skip(target: Path, link: Path, *, directory: bool) -> None:
    try:
        os.symlink(target, link, target_is_directory=directory)
    except (NotImplementedError, OSError) as exc:
        if os.name == "nt" and directory:
            completed = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode == 0:
                return
            detail = (completed.stderr or completed.stdout).strip()
            pytest.skip(f"当前环境无法创建测试用 junction：{detail or exc}")
        pytest.skip(f"当前环境无法创建测试用符号链接：{exc}")


def _sealed(
    store: AgentArtifactStore,
    *,
    attempt_id: str = "attempt-internal-stage",
):
    authority = _authority(attempt_id=attempt_id)
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


@pytest.mark.parametrize(
    "fault_point",
    ["before_owner_replace", "after_owner_replace"],
)
def test_owner_metadata_atomic_replace_recovers(
    tmp_path: Path,
    fault_point: str,
) -> None:
    fired = False

    def fault(point: str) -> None:
        nonlocal fired
        if point == fault_point and not fired:
            fired = True
            raise RuntimeError(f"injected-{point}")

    authority = _authority()
    store = AgentArtifactStore(tmp_path / "runs", fault_injector=fault)
    with pytest.raises(RuntimeError, match=fault_point):
        store.prepare_governed_stage(
            workspace_id=_workspace_id(authority),
            execution_authority=authority,
        )
    store._fault_injector = None
    recovered = store.prepare_governed_stage(
        workspace_id=_workspace_id(authority),
        execution_authority=authority,
    )
    assert recovered.execution_authority == authority


@pytest.mark.parametrize(
    "fault_point",
    ["before_seal_replace", "after_seal_replace"],
)
def test_seal_metadata_atomic_replace_recovers(
    tmp_path: Path,
    fault_point: str,
) -> None:
    fired = False

    def fault(point: str) -> None:
        nonlocal fired
        if point == fault_point and not fired:
            fired = True
            raise RuntimeError(f"injected-{point}")

    store = AgentArtifactStore(tmp_path / "runs", fault_injector=fault)
    authority = _authority()
    stage = store.prepare_governed_stage(
        workspace_id=_workspace_id(authority),
        execution_authority=authority,
    )
    store.write_json(stage.stage_dir / "work_order.json", {})
    store.write_text(stage.stage_dir / "prompt.txt", "prompt")
    store.write_json(stage.stage_dir / "metadata.json", {})
    store.write_json(stage.stage_dir / "result.json", {})
    store.write_json(stage.stage_dir / "changed_files.json", {})
    store.write_json(stage.stage_dir / "verification.json", {})
    with pytest.raises(RuntimeError, match=fault_point):
        store.seal_governed_stage(stage)
    store._fault_injector = None
    recovered = store.seal_governed_stage(stage)
    assert store.inspect_governed_stage(
        stage_ref=recovered.stage_ref,
        seal_digest=recovered.seal_digest,
    ) == recovered


def test_governed_stage_seals_exact_inventory_and_refuses_worker_rerun(
    tmp_path: Path,
) -> None:
    store = AgentArtifactStore(tmp_path / "runs")
    seal = _sealed(store)

    reopened = store.inspect_governed_stage(
        stage_ref=seal.stage_ref,
        seal_digest=seal.seal_digest,
    )
    assert reopened.execution_authority == _authority()
    with pytest.raises(GovernedAgentArtifactError, match="cannot be reopened"):
        store.prepare_governed_stage(
            workspace_id=seal.workspace_id,
            execution_authority=_authority(),
        )

    (seal.stage_dir / "result.json").write_text("{}", encoding="utf-8")
    with pytest.raises(GovernedAgentArtifactError, match="changed after execution"):
        store.inspect_governed_stage(
            stage_ref=seal.stage_ref,
            seal_digest=seal.seal_digest,
        )


def test_governed_stage_rejects_unsealed_extra_file(tmp_path: Path) -> None:
    store = AgentArtifactStore(tmp_path / "runs")
    authority = _authority()
    stage = store.prepare_governed_stage(
        workspace_id=_workspace_id(authority),
        execution_authority=authority,
    )
    for name in (
        "work_order.json",
        "metadata.json",
        "result.json",
        "changed_files.json",
        "verification.json",
    ):
        store.write_json(stage.stage_dir / name, {})
    store.write_text(stage.stage_dir / "prompt.txt", "prompt")
    store.write_text(stage.stage_dir / "unsealed.txt", "must not move")
    with pytest.raises(GovernedAgentArtifactError, match="inventory"):
        store.seal_governed_stage(stage)


def test_unsealed_inventory_reopens_exact_stage_and_discards_stale_temp(
    tmp_path: Path,
) -> None:
    store = AgentArtifactStore(tmp_path / "runs")
    authority = _authority()
    stage = store.prepare_governed_stage(
        workspace_id=_workspace_id(authority),
        execution_authority=authority,
    )
    (stage.stage_dir / ".tmp").write_text("stale metadata", encoding="utf-8")

    inventory = store.inventory_unsealed_governed_stages(limit=10)

    assert inventory == (stage,)
    store.discard_unsealed_governed_stage(inventory[0])
    assert not stage.stage_dir.exists()


def test_unsealed_inventory_never_returns_a_sealed_stage(tmp_path: Path) -> None:
    store = AgentArtifactStore(tmp_path / "runs")
    _sealed(store)

    assert store.inventory_unsealed_governed_stages(limit=10) == ()


@pytest.mark.parametrize(
    "fault_point",
    [
        "before_promotion_replace",
        "after_promotion_replace",
        "after_promotion_record",
        "after_promotion_rename",
    ],
)
def test_promotion_crash_edges_exactly_replay(
    tmp_path: Path,
    fault_point: str,
) -> None:
    fired = False

    def fault(point: str) -> None:
        nonlocal fired
        if point == fault_point and not fired:
            fired = True
            raise RuntimeError(f"injected-{point}")

    store = AgentArtifactStore(tmp_path / "runs", fault_injector=fault)
    seal = _sealed(store, attempt_id=f"attempt-{fault_point}")
    with pytest.raises(RuntimeError, match=fault_point):
        store.promote_governed_stage(
            stage_ref=seal.stage_ref,
            seal_digest=seal.seal_digest,
            acceptance_ref="task-artifact://accepted/internal",
        )

    store._fault_injector = None
    first = store.promote_governed_stage(
        stage_ref=seal.stage_ref,
        seal_digest=seal.seal_digest,
        acceptance_ref="task-artifact://accepted/internal",
    )
    replay = store.promote_governed_stage(
        stage_ref=seal.stage_ref,
        seal_digest=seal.seal_digest,
        acceptance_ref="task-artifact://accepted/internal",
    )
    assert replay == first
    assert first.startswith("agent-artifact://")


def test_staging_and_accepted_double_authority_fails_closed(tmp_path: Path) -> None:
    store = AgentArtifactStore(tmp_path / "runs")
    seal = _sealed(store)
    store.promote_governed_stage(
        stage_ref=seal.stage_ref,
        seal_digest=seal.seal_digest,
        acceptance_ref="task-artifact://accepted/internal",
    )
    accepted = store._accepted_dir(seal.workspace_id, seal.attempt_id)
    staging = store._stage_dir(seal.workspace_id, seal.attempt_id)
    shutil.copytree(accepted, staging)

    with pytest.raises(GovernedAgentArtifactError, match="multiple authorities"):
        store.inspect_governed_stage(
            stage_ref=seal.stage_ref,
            seal_digest=seal.seal_digest,
        )


def test_rejected_stage_becomes_safe_exact_tombstone(tmp_path: Path) -> None:
    store = AgentArtifactStore(tmp_path / "runs")
    seal = _sealed(store)
    ref = store.abandon_governed_stage(
        stage_ref=seal.stage_ref,
        seal_digest=seal.seal_digest,
        reason_code="INTERNAL_AGENT_REJECTED",
    )
    replay = store.abandon_governed_stage(
        stage_ref=seal.stage_ref,
        seal_digest=seal.seal_digest,
        reason_code="INTERNAL_AGENT_REJECTED",
    )

    assert replay == ref
    assert ref.startswith("agent-artifact-abandon://")
    assert not seal.stage_dir.exists()
    assert not (tmp_path / "runs" / "governed" / "accepted").exists()

    with pytest.raises(GovernedAgentArtifactError, match="abandoned artifact"):
        store.prepare_governed_stage(
            workspace_id=seal.workspace_id,
            execution_authority=_authority(),
        )
    with pytest.raises(GovernedAgentArtifactError, match="attribution drifted"):
        store.abandon_governed_stage(
            stage_ref=seal.stage_ref,
            seal_digest=seal.seal_digest,
            reason_code="INTERNAL_AGENT_CANCELLED",
        )


@pytest.mark.parametrize(
    "fault_point",
    [
        "before_tombstone_replace",
        "after_tombstone_replace",
        "after_tombstone_record",
    ],
)
def test_abandonment_metadata_crash_reopens_exact_stage_before_delete(
    tmp_path: Path,
    fault_point: str,
) -> None:
    fired = False

    def fault(point: str) -> None:
        nonlocal fired
        if point == fault_point and not fired:
            fired = True
            raise RuntimeError(f"injected-{point}")

    store = AgentArtifactStore(tmp_path / "runs", fault_injector=fault)
    seal = _sealed(store)
    with pytest.raises(RuntimeError, match=fault_point):
        store.abandon_governed_stage(
            stage_ref=seal.stage_ref,
            seal_digest=seal.seal_digest,
            reason_code="INTERNAL_AGENT_REJECTED",
        )
    assert seal.stage_dir.exists()

    store._fault_injector = None
    ref = store.abandon_governed_stage(
        stage_ref=seal.stage_ref,
        seal_digest=seal.seal_digest,
        reason_code="INTERNAL_AGENT_REJECTED",
    )
    assert ref.startswith("agent-artifact-abandon://")
    assert not seal.stage_dir.exists()


def test_accepted_artifact_is_never_deleted_by_abandonment(tmp_path: Path) -> None:
    store = AgentArtifactStore(tmp_path / "runs")
    seal = _sealed(store)
    store.promote_governed_stage(
        stage_ref=seal.stage_ref,
        seal_digest=seal.seal_digest,
        acceptance_ref="task-artifact://accepted/internal",
    )
    accepted = store._accepted_dir(seal.workspace_id, seal.attempt_id)

    with pytest.raises(GovernedAgentArtifactError, match="cannot be abandoned"):
        store.abandon_governed_stage(
            stage_ref=seal.stage_ref,
            seal_digest=seal.seal_digest,
            reason_code="INTERNAL_AGENT_REJECTED",
        )
    assert accepted.exists()


@pytest.mark.parametrize("redirected_namespace", ["governed", "staging"])
def test_prepare_rejects_prepositioned_namespace_symlink(
    tmp_path: Path,
    redirected_namespace: str,
) -> None:
    runs_root = tmp_path / "runs"
    outside = tmp_path / "outside"
    runs_root.mkdir()
    outside.mkdir()
    if redirected_namespace == "governed":
        link = runs_root / "governed"
    else:
        (runs_root / "governed").mkdir()
        link = runs_root / "governed" / "staging"
    _symlink_or_skip(outside, link, directory=True)
    authority = _authority()

    with pytest.raises(GovernedAgentArtifactError, match="link or reparse"):
        AgentArtifactStore(runs_root).prepare_governed_stage(
            workspace_id=_workspace_id(authority),
            execution_authority=authority,
        )

    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("redirected_parent", ["workspace", "attempt"])
def test_prepare_rejects_workspace_or_attempt_parent_symlink(
    tmp_path: Path,
    redirected_parent: str,
) -> None:
    runs_root = tmp_path / "runs"
    outside = tmp_path / "outside"
    staging = runs_root / "governed" / "staging"
    staging.mkdir(parents=True)
    outside.mkdir()
    authority = _authority()
    workspace = _workspace_id(authority)
    attempt = _attempt_path_segment(str(authority["attempt_id"]))
    if redirected_parent == "workspace":
        link = staging / workspace
    else:
        (staging / workspace).mkdir()
        link = staging / workspace / attempt
    _symlink_or_skip(outside, link, directory=True)

    with pytest.raises(GovernedAgentArtifactError, match="link or reparse"):
        AgentArtifactStore(runs_root).prepare_governed_stage(
            workspace_id=workspace,
            execution_authority=authority,
        )

    assert list(outside.iterdir()) == []


def test_raw_write_revalidates_final_file_and_refuses_symlink(
    tmp_path: Path,
) -> None:
    store = AgentArtifactStore(tmp_path / "runs")
    authority = _authority()
    stage = store.prepare_governed_stage(
        workspace_id=_workspace_id(authority),
        execution_authority=authority,
    )
    outside = tmp_path / "outside.txt"
    outside.write_text("untouched", encoding="utf-8")
    _symlink_or_skip(outside, stage.stage_dir / "prompt.txt", directory=False)

    with pytest.raises(GovernedAgentArtifactError, match="link or reparse"):
        store.write_text(stage.stage_dir / "prompt.txt", "private prompt")

    assert outside.read_text(encoding="utf-8") == "untouched"


@pytest.mark.parametrize("redirected_name", ["seal.json", ".tmp"])
def test_metadata_write_revalidates_target_and_temporary_file(
    tmp_path: Path,
    redirected_name: str,
) -> None:
    store = AgentArtifactStore(tmp_path / "runs")
    authority = _authority()
    stage = store.prepare_governed_stage(
        workspace_id=_workspace_id(authority),
        execution_authority=authority,
    )
    outside = tmp_path / "outside.json"
    outside.write_text('{"untouched":true}', encoding="utf-8")
    _symlink_or_skip(
        outside,
        stage.stage_dir / redirected_name,
        directory=False,
    )

    with pytest.raises(GovernedAgentArtifactError, match="link or reparse"):
        store._write_governed_metadata(
            stage.stage_dir / "seal.json",
            {"private": True},
            fault_prefix="seal",
        )

    assert outside.read_text(encoding="utf-8") == '{"untouched":true}'


@pytest.mark.parametrize("write_kind", ["raw", "metadata"])
def test_write_rejects_attempt_directory_swapped_to_reparse_point(
    tmp_path: Path,
    write_kind: str,
) -> None:
    store = AgentArtifactStore(tmp_path / "runs")
    authority = _authority()
    stage = store.prepare_governed_stage(
        workspace_id=_workspace_id(authority),
        execution_authority=authority,
    )
    original = stage.stage_dir.with_name(stage.stage_dir.name + "-original")
    stage.stage_dir.rename(original)
    outside = tmp_path / "outside-stage"
    outside.mkdir()
    _symlink_or_skip(outside, stage.stage_dir, directory=True)

    with pytest.raises(GovernedAgentArtifactError, match="link or reparse"):
        if write_kind == "raw":
            store.write_text(stage.stage_dir / "prompt.txt", "private prompt")
        else:
            store._write_governed_metadata(
                stage.stage_dir / "seal.json",
                {"private": True},
                fault_prefix="seal",
            )

    assert list(outside.iterdir()) == []


def test_legacy_alias_back_into_governed_stage_cannot_bypass_writer_guard(
    tmp_path: Path,
) -> None:
    store = AgentArtifactStore(tmp_path / "runs")
    authority = _authority()
    stage = store.prepare_governed_stage(
        workspace_id=_workspace_id(authority),
        execution_authority=authority,
    )
    canonical_result = stage.stage_dir / "result.json"
    store.write_json(canonical_result, {"safe": True})
    alias = store.runs_root / "legacy-alias"
    _symlink_or_skip(stage.stage_dir, alias, directory=True)

    with pytest.raises(GovernedAgentArtifactError, match="lexical root"):
        store.write_json(alias / "result.json", {"safe": False})

    assert canonical_result.read_text(encoding="utf-8") == '{\n  "safe": true\n}'


def test_governed_writer_rejects_dotdot_escape_before_legacy_fallback(
    tmp_path: Path,
) -> None:
    store = AgentArtifactStore(tmp_path / "runs")
    authority = _authority()
    stage = store.prepare_governed_stage(
        workspace_id=_workspace_id(authority),
        execution_authority=authority,
    )
    escaped = stage.stage_dir / ".." / ".." / ".." / ".." / "escaped.json"

    with pytest.raises(GovernedAgentArtifactError, match="lexical root"):
        store.write_json(escaped, {"private": True})

    assert not (store.runs_root / "escaped.json").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction 专项验证")
def test_prepare_rejects_windows_staging_junction(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    outside = tmp_path / "outside"
    governed = runs_root / "governed"
    governed.mkdir(parents=True)
    outside.mkdir()
    junction = governed / "staging"
    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.skip(
            "当前环境无法创建 Windows junction："
            + (completed.stderr or completed.stdout).strip()
        )
    authority = _authority()
    try:
        with pytest.raises(GovernedAgentArtifactError, match="link or reparse"):
            AgentArtifactStore(runs_root).prepare_governed_stage(
                workspace_id=_workspace_id(authority),
                execution_authority=authority,
            )
        assert list(outside.iterdir()) == []
    finally:
        if junction.exists():
            os.rmdir(junction)
