from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

REPOSITORY_IMPORT_BOUNDARIES = {
    "host_brain/persistence/evolution_governance_ledger.py": (
        "host_brain.evolution",
        "host_brain.contracts.evolution",
    ),
    "host_brain/persistence/evolution_activation_v2_feedback.py": (
        "host_brain.evolution",
        "host_brain.contracts.evolution",
    ),
    "host_brain/persistence/evolution_activation_v2_knowledge.py": (
        "host_brain.evolution",
        "host_brain.contracts.evolution",
    ),
    "host_brain/persistence/evolution_activation_v2_identity.py": (
        "host_brain.evolution",
        "host_brain.contracts.evolution",
    ),
    "host_brain/persistence/evolution_activation_v2_decision.py": (
        "host_brain.evolution",
        "host_brain.contracts.evolution",
    ),
    "host_brain/persistence/evolution_lab_ledger_repository.py": (
        "tools.evolution_lab",
        "host_brain.contracts.evolution_lab",
    ),
    "host_brain/persistence/evolution_lab_v2_repository.py": (
        "tools.evolution_lab",
        "host_brain.contracts.evolution_lab",
    ),
    "host_brain/persistence/skill_lifecycle_v2_repository.py": (
        "host_brain.skills.skill_lifecycle_v2",
        "host_brain.contracts.skill_lifecycle_v2",
    ),
}

CONTRACT_MODULES = (
    "host_brain/contracts/evolution.py",
    "host_brain/contracts/evolution_lab.py",
    "host_brain/contracts/skill_lifecycle_v2.py",
    "host_brain/contracts/runtime_context.py",
    "host_brain/contracts/semantic_recall.py",
)

def _tree(relative_path: str) -> ast.Module:
    path = ROOT / relative_path
    return ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))


def _absolute_imports(tree: ast.Module) -> set[str]:
    modules = {
        str(node.module or "")
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0
    }
    modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    return modules


def test_persistence_repositories_depend_on_neutral_contracts() -> None:
    for relative_path, (forbidden_prefix, required_module) in (
        REPOSITORY_IMPORT_BOUNDARIES.items()
    ):
        imports = _absolute_imports(_tree(relative_path))
        assert required_module in imports
        assert not any(
            module == forbidden_prefix or module.startswith(f"{forbidden_prefix}.")
            for module in imports
        )
    world_observation_imports = _absolute_imports(
        _tree("host_brain/persistence/world_observation_journal.py")
    )
    assert "host_brain.contracts.runtime_context" in world_observation_imports


def test_neutral_contract_modules_have_no_upper_layer_dependency() -> None:
    for relative_path in CONTRACT_MODULES:
        imports = _absolute_imports(_tree(relative_path))
        assert not any(module.startswith("host_brain.") for module in imports)
