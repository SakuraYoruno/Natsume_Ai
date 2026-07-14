from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TRIBE_ROOT = ROOT / "host_brain" / "tribe"


def test_tribe_production_modules_do_not_import_application_package() -> None:
    violations: list[str] = []
    for path in sorted(TRIBE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and str(node.module or "").startswith(
                "host_brain.app"
            ):
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}:{node.module}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("host_brain.app"):
                        violations.append(
                            f"{path.relative_to(ROOT)}:{node.lineno}:{alias.name}"
                        )
    assert violations == []


def test_tribe_runtime_services_are_explicitly_composed() -> None:
    adapter_source = (ROOT / "host_brain" / "app" / "tribe_composition.py").read_text(
        encoding="utf-8"
    )
    runtime_source = (
        ROOT / "host_brain" / "composition" / "tribe_runtime.py"
    ).read_text(encoding="utf-8")
    assert "TribeApplicationPorts(" in adapter_source
    assert "agent.application_ports = ports" in runtime_source
    assert (
        "model_attempt_service=process_services.get_model_attempt_service"
        in adapter_source
    )
    assert "compose_process_tribe_runtime(" in adapter_source
    assert "state_registry=get_global_registry()" in adapter_source
    assert "tribe_registry=get_host_tribe_registry()" in adapter_source


def test_tribe_handoff_has_no_lazy_global_or_implicit_registry_fallback() -> None:
    source = (TRIBE_ROOT / "handoff.py").read_text(encoding="utf-8")
    assert "_HANDOFF_COORDINATOR" not in source
    assert "get_tribe_handoff_coordinator" not in source
    assert "state_registry: TribeStateRegistry," in source
    assert "tribe_registry: HostTribeRegistry," in source
    assert "state_registry or TribeStateRegistry()" not in source


def test_tribe_owns_event_bus_and_relational_classifier() -> None:
    event_bus = (TRIBE_ROOT / "event_bus.py").read_text(encoding="utf-8")
    routes = (ROOT / "host_brain" / "app" / "internal_tribe_routes.py").read_text(
        encoding="utf-8"
    )
    assert "def get_tribe_event_bus" in event_bus
    assert "def set_tribe_event_bus" in event_bus
    assert "def get_tribe_event_bus" not in routes
    assert not (ROOT / "host_brain" / "app" / "relational_chat_detector.py").exists()
    assert (ROOT / "host_brain" / "persona" / "relational_chat.py").is_file()
