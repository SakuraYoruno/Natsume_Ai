from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PUSHER = ROOT / "host_brain" / "tribe" / "edge_command_pusher.py"
DIRECT_CONSUMERS = (
    ROOT / "host_brain" / "app" / "http_hooks.py",
    ROOT / "host_brain" / "app" / "internal_tribe_routes.py",
    ROOT
    / "host_brain"
    / "app"
    / "observability_runtime"
    / "domain_command_ports.py",
    ROOT / "host_brain" / "tribe" / "motor_cortex_agent.py",
    ROOT / "host_brain" / "tribe" / "limbic_agent.py",
    ROOT / "host_brain" / "tribe" / "cortex_agent.py",
)
RETIRED_SYMBOLS = {
    "_PUSHER",
    "_PUSHER_LOCK",
    "get_edge_command_pusher",
    "init_edge_command_pusher",
    "set_edge_command_pusher",
}


def _top_level_symbols(source: str) -> set[str]:
    tree = ast.parse(source)
    symbols: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            for target in targets:
                if isinstance(target, ast.Name):
                    symbols.add(target.id)
    return symbols


def _referenced_symbols(source: str) -> set[str]:
    tree = ast.parse(source)
    symbols: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            symbols.add(node.id)
        elif isinstance(node, ast.Attribute):
            symbols.add(node.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            symbols.update(alias.name for alias in node.names)
    return symbols


def test_edge_command_transport_has_no_process_global_owner() -> None:
    source = PUSHER.read_text(encoding="utf-8")

    assert RETIRED_SYMBOLS.isdisjoint(_top_level_symbols(source))
    assert "ActionPreviewExecutionProof" in source
    assert "ACTION_PREVIEW_TASK_AUTHORITY_REQUIRED" in source


def test_production_consumers_do_not_resolve_retired_edge_owner() -> None:
    for path in DIRECT_CONSUMERS:
        source = path.read_text(encoding="utf-8")
        retained = RETIRED_SYMBOLS & _referenced_symbols(source)
        assert retained == set(), f"{path}: retained {sorted(retained)}"


def test_legacy_edge_front_doors_remain_explicitly_fail_closed() -> None:
    routes = (
        ROOT / "host_brain" / "app" / "internal_tribe_routes.py"
    ).read_text(encoding="utf-8")
    domain_ports = (
        ROOT
        / "host_brain"
        / "app"
        / "observability_runtime"
        / "domain_command_ports.py"
    ).read_text(encoding="utf-8")

    assert 'authority_state="GOVERNED_TASKGRAPH_ONLY"' in routes
    assert routes.count("ACTION_PREVIEW_TASK_AUTHORITY_REQUIRED") >= 2
    assert '"edge.command.push": _blocked_legacy_operator_handler' in domain_ports
