from __future__ import annotations

from pathlib import Path

from host_brain.contracts.agent_runtime.work_order import AgentWorkOrder
from host_brain.agent_runtime.workspace_policy import WorkspacePolicy
from host_brain.bio_ops.safety import build_proposal_only_policy, is_bio_ops_sensitive_path
from host_brain.evolution_lab.policy import validate_evolution_branch
from tools.organ_ops.organ_registry import build_default_organ_registry


def test_workspace_policy_creates_temp_workspace(tmp_path) -> None:
    order = AgentWorkOrder.from_mapping(
        {
            "work_order_id": "wo-temp",
            "domain": "code",
            "run_mode": "inspect",
            "objective": "inspect",
            "workspace": {"strategy": "temp_workspace", "runs_root": str(tmp_path)},
        }
    )

    resolved = WorkspacePolicy().resolve(order)

    assert resolved.source == "temp_workspace"
    assert resolved.path == (tmp_path / "wo-temp" / "workspace").resolve()
    assert resolved.path.exists()


def test_workspace_policy_blocks_write_without_write_action(monkeypatch, tmp_path) -> None:
    import host_brain.agent_runtime.workspace_policy as workspace_policy_module

    monkeypatch.setattr(workspace_policy_module, "DEFAULT_CODEX_RUNS_ROOT", tmp_path / "runs")
    order = AgentWorkOrder.from_mapping(
        {
            "work_order_id": "wo-block",
            "domain": "code",
            "run_mode": "worker",
            "objective": "change files",
            "workspace": {"strategy": "temp_workspace"},
            "allowed_actions": ["read_files"],
            "forbidden_actions": ["modify_secret_files"],
            "approval_policy": {"require_confirm": False},
        }
    )
    resolved = WorkspacePolicy().resolve(order)

    allowed, reason = WorkspacePolicy().enforce_write_policy(order, resolved)

    assert allowed is False
    assert "write_files" in str(reason)


def test_workspace_policy_accepts_confirmed_bounded_write(monkeypatch, tmp_path) -> None:
    import host_brain.agent_runtime.workspace_policy as workspace_policy_module

    monkeypatch.setattr(workspace_policy_module, "DEFAULT_CODEX_RUNS_ROOT", tmp_path / "runs")
    order = AgentWorkOrder.from_mapping(
        {
            "work_order_id": "wo-write",
            "domain": "code",
            "run_mode": "worker",
            "objective": "change files",
            "workspace": {"strategy": "temp_workspace"},
            "allowed_actions": ["read_files", "write_files"],
            "forbidden_actions": ["modify_secret_files"],
            "approval_policy": {"require_confirm": True, "confirmed": True},
        }
    )
    resolved = WorkspacePolicy().resolve(order)

    allowed, reason = WorkspacePolicy().enforce_write_policy(order, resolved)

    assert allowed is True
    assert reason is None


def test_evolution_lab_requires_experiment_branch() -> None:
    assert validate_evolution_branch("codex/evolve-runtime-v2") == (True, None)
    assert validate_evolution_branch("main") == (False, "EVOLUTION_EXPERIMENT_BRANCH_REQUIRED")


def test_organ_registry_indexes_tribe_agent_organs() -> None:
    organs = build_default_organ_registry().list_organs()

    assert len(organs) == 23
    assert any(organ["organ_id"] == "immune" for organ in organs)
    assert all(not organ["module"].endswith(".base_agent") for organ in organs)


def test_bio_ops_policy_is_proposal_only_and_blocks_sensitive_paths() -> None:
    policy = build_proposal_only_policy()

    assert "write_files" in policy["forbidden_actions"]
    assert is_bio_ops_sensitive_path("host_brain/persona/personality_core.yaml") is True
    assert is_bio_ops_sensitive_path("host_brain/app/internal_codex_routes.py") is False
