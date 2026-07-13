from __future__ import annotations

import ast
import inspect
from pathlib import Path

from edge_client.gateway.bootstrap import start_gateway
from edge_client.runtime.tribe_composition import compose_edge_tribe
from edge_client.tribe.edge_tribe_bridge import EdgeTribeBridge


_ROOT = Path(__file__).resolve().parents[2]


def test_edge_tribe_bridge_has_one_explicit_production_constructor() -> None:
    owners: list[str] = []
    for path in (_ROOT / "edge_client").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "from_env":
                continue
            owner = node.func.value
            if isinstance(owner, ast.Name) and owner.id == "EdgeTribeBridge":
                owners.append(path.relative_to(_ROOT).as_posix())

    assert owners == ["edge_client/runtime/tribe_composition.py"]


def test_edge_tribe_composition_injects_one_bridge_everywhere(monkeypatch) -> None:
    bridge = EdgeTribeBridge(host_base_url="", enabled=False)
    monkeypatch.setattr(
        EdgeTribeBridge,
        "from_env",
        classmethod(lambda _cls: bridge),
    )

    composition = compose_edge_tribe()

    assert composition.bridge is bridge
    assert composition.registry.bridge is bridge
    assert composition.registry.somatic.bridge is bridge
    assert composition.registry.peripheral.bridge is bridge
    assert composition.registry.channel.bridge is bridge
    assert composition.registry.voice.bridge is bridge


def test_gateway_requires_runtime_owned_tribe_bridge() -> None:
    parameter = inspect.signature(start_gateway).parameters["tribe_bridge"]

    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty
