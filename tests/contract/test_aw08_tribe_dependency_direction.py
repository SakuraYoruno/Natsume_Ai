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
    runtime_source = (ROOT / "host_brain" / "app" / "tribe_runtime_scope.py").read_text(
        encoding="utf-8"
    )
    assert "TribeApplicationPorts(" in adapter_source
    assert "agent.application_ports = ports" in runtime_source
    assert 'model_attempt_service=require_host_provider(host_services, "model_attempt_service")' in adapter_source
    assert "host_brain.composition import process_services" not in adapter_source
    assert "get_process_service_container" not in runtime_source
    assert not (ROOT / "host_brain" / "composition" / "tribe_runtime.py").exists()
    assert "host_services=host_services" in (
        ROOT / "host_brain" / "app" / "asgi.py"
    ).read_text(encoding="utf-8")
    assert "build_production_tribe_runtime_scope" in adapter_source
    assert "state_registry=TribeStateRegistry()" in adapter_source
    assert "tribe_registry=HostTribeRegistry()" in adapter_source
    assert "event_bus=InMemoryTribeEventBus(" in adapter_source
    assert '("limbic_agent", LimbicAgent)' in adapter_source
    assert '("limbic_agent", get_limbic_agent)' not in adapter_source
    assert "tribe_runtime=tribe_runtime" in (
        ROOT / "host_brain" / "app" / "asgi.py"
    ).read_text(encoding="utf-8")


def test_tribe_handoff_has_no_lazy_global_or_implicit_registry_fallback() -> None:
    source = (TRIBE_ROOT / "handoff.py").read_text(encoding="utf-8")
    assert "_HANDOFF_COORDINATOR" not in source
    assert "get_tribe_handoff_coordinator" not in source
    assert "state_registry: TribeStateRegistry," in source
    assert "tribe_registry: HostTribeRegistry," in source
    assert "state_registry or TribeStateRegistry()" not in source


def test_tribe_owns_event_bus_and_relational_classifier() -> None:
    event_bus = (TRIBE_ROOT / "event_bus.py").read_text(encoding="utf-8")
    registry = (TRIBE_ROOT / "registry.py").read_text(encoding="utf-8")
    state = (TRIBE_ROOT / "tribe_state.py").read_text(encoding="utf-8")
    routes = (ROOT / "host_brain" / "app" / "internal_tribe_routes.py").read_text(
        encoding="utf-8"
    )
    assert "def get_tribe_event_bus" in event_bus
    assert "def set_tribe_event_bus" not in event_bus
    assert "def set_host_tribe_registry" not in registry
    module_globals = {
        node.target.id
        for source in (event_bus, registry, state)
        for node in ast.parse(source).body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
    }
    assert "_EVENT_BUS" not in module_globals
    assert "_HOST_TRIBE_REGISTRY" not in module_globals
    assert "_GLOBAL_REGISTRY" not in module_globals
    assert "def get_tribe_event_bus" not in routes
    assert not (ROOT / "host_brain" / "app" / "relational_chat_detector.py").exists()
    assert (ROOT / "host_brain" / "persona" / "relational_chat.py").is_file()


def test_host_lifecycle_uses_scope_owned_tribe_bus_and_registry() -> None:
    hooks = (ROOT / "host_brain" / "app" / "http_hooks.py").read_text(
        encoding="utf-8"
    )
    context = (TRIBE_ROOT / "runtime_context.py").read_text(encoding="utf-8")
    application_ports = (TRIBE_ROOT / "application_ports.py").read_text(
        encoding="utf-8"
    )
    asgi = (ROOT / "host_brain" / "app" / "asgi.py").read_text(
        encoding="utf-8"
    )

    assert "tribe_runtime.event_bus" in hooks
    assert "tribe_runtime.tribe_registry" in hooks
    assert "tribe_runtime.activate_execution_context" in hooks
    assert "tribe_runtime.deactivate_execution_context" in hooks
    assert "set_tribe_event_bus" not in hooks
    assert "set_host_tribe_registry" not in hooks
    assert "shutdown_process_runtime" in hooks
    assert "ProcessRuntimeShutdownDependencies" in hooks
    assert "process_runtime_shutdown_adapter" not in hooks
    assert "class TribeRuntimeContext" in context
    assert "class TribeAgentCatalog" in context
    assert "ContextVar[" in context
    assert "_ACTIVE_OWNER" not in context
    assert "_ACTIVE_CONTEXT" not in context
    assert "TRIBE_RUNTIME_CONTEXT_ALREADY_BOUND" in context
    assert "_APPLICATION_PORT_BINDING_FENCE" not in application_ports
    assert "application_port_binding_fence" not in application_ports
    assert "@application.middleware(\"http\")" in asgi
    assert "bind_tribe_execution_context" in asgi


def test_all_tribe_agent_getters_resolve_the_active_host_catalog() -> None:
    agent_modules: list[Path] = []
    for path in sorted(TRIBE_ROOT.glob("*_agent.py")):
        source = path.read_text(encoding="utf-8")
        if "\ndef get_" not in source:
            continue
        agent_modules.append(path)
        tree = ast.parse(source, filename=str(path))
        assert "require_active_tribe_agent" in source, path.name
        assert "resolve_active_tribe_agent" not in source, path.name
        assert "assert_tribe_agent_override_allowed" not in source, path.name
        assert not any(
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id.endswith("_AGENT")
            and node.target.id.isupper()
            for node in tree.body
        ), path.name
        assert not any(
            isinstance(node, ast.FunctionDef)
            and node.name.startswith("set_")
            and node.name.endswith("_agent")
            for node in tree.body
        ), path.name

    assert len(agent_modules) == 23
