from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8-sig")


def test_ne05_production_composition_uses_only_v2_lifecycle() -> None:
    host = _source("host_brain/composition/host_application.py")
    intake = _source(
        "host_brain/skills/signed_intake_composition.py"
    )
    assert "SQLiteSkillLifecycleV2Repository" in host
    assert "LifecycleBoundSkillRegistry" in host
    assert "SQLiteSkillLifecycleV2Repository" in intake
    assert "SkillLifecycleV2Service" in intake
    assert "SQLiteSkillLifecycleRepository" not in intake
    assert "SkillLifecycleService(" not in intake


def test_ne05_old_runtime_mutation_authorities_are_physically_absent() -> None:
    assert not (ROOT / "host_brain/skills/hot_loader.py").exists()
    assert not (
        ROOT / "host_brain/skills/skill_lifecycle.py"
    ).exists()
    assert not (
        ROOT / "host_brain/persistence/skill_lifecycle_repository.py"
    ).exists()

    supply_tree = ast.parse(
        _source("host_brain/persistence/supply_chain_repository.py")
    )
    public_methods = {
        node.name
        for item in supply_tree.body
        if isinstance(item, ast.ClassDef)
        and item.name == "SQLiteSkillSupplyChainStore"
        for node in item.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    }
    assert "activate_bundle" not in public_methods
    assert "reject_bundle" not in public_methods


def test_ne05_forge_has_no_public_activation_or_route_override() -> None:
    tree = ast.parse(_source("host_brain/app/capability_forge.py"))
    public_methods = {
        node.name
        for item in tree.body
        if isinstance(item, ast.ClassDef)
        and item.name == "CapabilityForgeService"
        for node in item.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    }
    assert {
        "approve_and_promote",
        "rollback",
        "record_runtime_result",
        "get_runtime_route_override",
    }.isdisjoint(public_methods)
