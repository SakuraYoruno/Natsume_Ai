from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_production_has_one_broker_constructor_and_no_private_fallbacks() -> None:
    constructors: list[str] = []
    for path in (ROOT / "host_brain").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "CapabilityBroker":
                constructors.append(path.relative_to(ROOT).as_posix())

    assert constructors == ["host_brain/capability_plane/production_composition.py"]
    forbidden = (
        "_PRODUCTION_CAPABILITY_BROKER",
        "_SKILL_CAPABILITY_BROKER",
        "_get_skill_capability_broker",
        "capability_broker or CapabilityBroker",
    )
    targets = (
        ROOT / "host_brain" / "domain_packs" / "production_runtime.py",
        ROOT / "host_brain" / "skills" / "executor_registry.py",
    )
    combined = "\n".join(path.read_text(encoding="utf-8") for path in targets)
    assert not [token for token in forbidden if token in combined]
