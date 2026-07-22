from __future__ import annotations

import ast
import inspect
from pathlib import Path

from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter, RuntimeExecutor
from host_brain.app.settings.core import CoreSettings
from host_brain.lifecycle_runtime.ports import LifecycleRuntimeApplicationPorts
from host_brain.input_runtime.ports import InputRuntimeApplicationPorts
from host_brain.persistence.task_repository import DurableTaskStore
from natsume_contracts.task import EXECUTOR_ASSIGNMENT_SCHEMA_VERSION, TaskExecutor


ROOT = Path(__file__).resolve().parents[2]


def test_task_runtime_has_one_canonical_assignment_authority() -> None:
    assignment = CanonicalRuntimeRouter(clock=lambda: 123).assign_new_task("work-order-aw08")

    assert assignment.executor is RuntimeExecutor.CANONICAL is TaskExecutor.CANONICAL
    assert assignment.schema_version == EXECUTOR_ASSIGNMENT_SCHEMA_VERSION
    assert assignment.to_dict() == {
        "schema_version": "Natsume.agent.runtime_assignment.v2",
        "work_order_id": "work-order-aw08",
        "executor": "canonical",
        "assignment_id": assignment.assignment_id,
        "assigned_at_ms": 123,
        "is_new_task": True,
        "reason_code": "RUNTIME_NEW_TASK_CANONICAL",
    }


def test_legacy_runtime_modules_settings_and_store_api_are_deleted() -> None:
    assert not (ROOT / "host_brain/agent_runtime/job_registry.py").exists()
    assert not (ROOT / "host_brain/agent_runtime/task_scheduler.py").exists()
    assert not hasattr(CoreSettings(), "task_runtime_mode")
    assert not hasattr(CoreSettings(), "task_runtime_shadow_owner")
    assert not hasattr(CoreSettings(), "task_runtime_canary_percent")
    assert not hasattr(CoreSettings(), "task_runtime_kill_switch")
    assert not hasattr(DurableTaskStore, "mirror_legacy_job")
    assert not hasattr(DurableTaskStore, "get_legacy_job_shadow")
    assert not hasattr(DurableTaskStore, "reconcile_legacy_job")


def test_production_worker_resolvers_have_no_legacy_fallback() -> None:
    paths = (
        "host_brain/app/governed_task_adapter.py",
        "host_brain/app/internal_task_runtime.py",
        "host_brain/scheduler/worker_registry.py",
        "host_brain/scheduler/governed_runtime.py",
        "host_brain/task_runtime/coordinator.py",
    )
    for relative in paths:
        source = (ROOT / relative).read_text(encoding="utf-8-sig")
        assert "legacy_worker" not in source
        assert "executor_name=\"legacy\"" not in source


def test_runtime_router_contains_no_shadow_canary_or_kill_switch_branch() -> None:
    source = inspect.getsource(__import__("host_brain.agent_runtime.runtime_router", fromlist=["*"]))
    for marker in ("LEGACY", "SHADOW", "canary", "kill_switch"):
        assert marker not in source


def test_legacy_plan_graph_runtime_flag_and_resolver_are_deleted() -> None:
    assert not (ROOT / "host_brain/app/plan_graph.py").exists()
    assert not hasattr(CoreSettings(), "canonical_planner_adapter_enabled")

    violations: list[str] = []
    markers = (
        "host_brain.app.plan_graph",
        "run_plan_graph",
        "HOST_CANONICAL_PLANNER_ADAPTER_ENABLED",
    )
    for path in sorted((ROOT / "host_brain").rglob("*.py")):
        source = path.read_text(encoding="utf-8-sig")
        for marker in markers:
            if marker in source:
                violations.append(f"{path.relative_to(ROOT)}:{marker}")
    assert violations == []

    assert not (ROOT / "host_brain/executive_runtime/conversation_execution.py").exists()


def test_retired_plan_graph_adapter_and_prompt_contract_are_deleted() -> None:
    adapter_source = (
        ROOT / "host_brain/agent_runtime/planner_adapter.py"
    ).read_text(encoding="utf-8-sig")
    prompt_source = (
        ROOT / "host_brain/model_runtime/prompt_specs.py"
    ).read_text(encoding="utf-8-sig")

    assert "to_plan_graph_input" not in adapter_source
    assert "plan_graph.synthesize.prompt_templates" not in prompt_source


def test_retired_executive_runtime_has_no_application_port_graph() -> None:
    runtime_root = ROOT / "host_brain/executive_runtime"
    violations: list[str] = []
    assert not (runtime_root / "ports.py").exists()
    assert not (ROOT / "host_brain/app/runtime_port_wiring.py").exists()

    for path in sorted(runtime_root.glob("*.py")):
        source = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"dependency", "resolve"}
            ):
                violations.append(f"{path.name}:dynamic_locator:{node.func.id}")
            module = ""
            if isinstance(node, ast.ImportFrom):
                module = str(node.module or "")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(("host_brain.app", "host_brain.composition")):
                        violations.append(f"{path.name}:{alias.name}")
            if module.startswith(("host_brain.app", "host_brain.composition")):
                violations.append(f"{path.name}:{module}")

    assert violations == []


def test_lifecycle_runtime_uses_typed_explicit_application_ports() -> None:
    from host_brain.lifecycle_runtime.life_jobs import (
        _build_llm_generator,
        run_life_memory_sleep_replay_handler,
        run_long_term_distillation_handler,
    )
    from host_brain.lifecycle_runtime.proactive_dispatch import (
        run_proactive_intent_dispatch,
    )
    from host_brain.lifecycle_runtime.runtime_snapshot import (
        build_digital_life_runtime_snapshot,
    )
    from host_brain.lifecycle_runtime.scheduler_handlers import (
        _build_scheduler_episodic_writer,
        _build_scheduler_narrative_llm_generator,
    )

    runtime_root = ROOT / "host_brain/lifecycle_runtime"
    violations: list[str] = []
    ports_source = (runtime_root / "ports.py").read_text(encoding="utf-8-sig")

    for path in sorted(runtime_root.glob("*.py")):
        source = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"dependency", "resolve"}
            ):
                violations.append(f"{path.name}:dynamic_locator:{node.func.id}")
            module = ""
            if isinstance(node, ast.ImportFrom):
                module = str(node.module or "")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(("host_brain.app", "host_brain.composition")):
                        violations.append(f"{path.name}:{alias.name}")
            if module.startswith(("host_brain.app", "host_brain.composition")):
                violations.append(f"{path.name}:{module}")

    assert LifecycleRuntimeApplicationPorts.__dataclass_params__.frozen is True
    for marker in (
        "_dependency_resolver",
        "_symbol_resolver",
        "def configure(",
        "def dependency(",
        "def resolve(",
    ):
        assert marker not in ports_source
    assert violations == []

    for function in (
        build_digital_life_runtime_snapshot,
        run_proactive_intent_dispatch,
        run_long_term_distillation_handler,
        run_life_memory_sleep_replay_handler,
        _build_llm_generator,
        _build_scheduler_episodic_writer,
        _build_scheduler_narrative_llm_generator,
    ):
        parameter = inspect.signature(function).parameters["ports"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is inspect.Signature.empty

    asgi_source = (ROOT / "host_brain/app/asgi.py").read_text(encoding="utf-8-sig")
    assert "lifecycle_runtime_ports = build_lifecycle_runtime_application_ports(" in asgi_source
    assert "activity_monitor_provider=host_lifecycle.get_activity_monitor" in asgi_source
    assert "ports=lifecycle_runtime_ports" in asgi_source


def test_input_runtime_uses_one_typed_explicit_application_boundary() -> None:
    from host_brain.input_runtime.cortex_dispatch import (
        _maybe_realign_gateway_pipeline,
        _run_cortex_orchestrator_pipeline,
    )
    from host_brain.input_runtime.gateway_delivery import _deliver_gateway_reply
    from host_brain.input_runtime.ingress import receive_input
    from host_brain.input_runtime.progress import build_gateway_progress_reply_text
    from host_brain.input_runtime.worker_dispatch import (
        _dispatch_to_graph,
        _dispatch_to_graph_worker,
        create_input_router,
    )

    runtime_root = ROOT / "host_brain/input_runtime"
    ports_source = (runtime_root / "ports.py").read_text(encoding="utf-8-sig")
    violations: list[str] = []
    for path in sorted(runtime_root.glob("*.py")):
        source = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            module = ""
            if isinstance(node, ast.ImportFrom):
                module = str(node.module or "")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(("host_brain.app", "host_brain.composition")):
                        violations.append(f"{path.name}:{alias.name}")
            if module.startswith(("host_brain.app", "host_brain.composition")):
                violations.append(f"{path.name}:{module}")

    assert InputRuntimeApplicationPorts.__dataclass_params__.frozen is True
    assert "__slots__" in vars(InputRuntimeApplicationPorts)
    for marker in (
        "_dependency_resolver",
        "_symbol_resolver",
        "def configure(",
        "def dependency(",
        "def resolve(",
        "ContextVar",
    ):
        assert marker not in ports_source
    assert not (runtime_root / "composition.py").exists()
    assert violations == []

    for function in (
        receive_input,
        _dispatch_to_graph,
        _dispatch_to_graph_worker,
        _run_cortex_orchestrator_pipeline,
        _maybe_realign_gateway_pipeline,
        _deliver_gateway_reply,
        build_gateway_progress_reply_text,
    ):
        parameter = inspect.signature(function).parameters["ports"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is inspect.Signature.empty

    application_ports = inspect.signature(create_input_router).parameters[
        "application_ports"
    ]
    assert application_ports.kind is inspect.Parameter.KEYWORD_ONLY
    assert application_ports.default is inspect.Signature.empty

    asgi_source = (ROOT / "host_brain/app/asgi.py").read_text(encoding="utf-8-sig")
    assert "input_runtime_ports = build_input_runtime_application_ports(" in asgi_source
    assert "host_services=host_services" in asgi_source
    assert "notify_user_interaction_callback=(" in asgi_source
    assert "application.state.input_runtime_ports = input_runtime_ports" in asgi_source
    input_composition_source = (
        ROOT / "host_brain/app/input_runtime_composition.py"
    ).read_text(encoding="utf-8")
    assert "host_brain.composition.process_services" not in input_composition_source
    model_provider_source = (
        ROOT / "host_brain/app/model_provider_composition.py"
    ).read_text(encoding="utf-8")
    assert "host_brain.composition.process_services" not in model_provider_source
    assert "model_attempt_service=host_services.model_attempt_service" in asgi_source
