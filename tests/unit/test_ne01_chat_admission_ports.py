from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CHAT_HANDLER_PATH = ROOT / "host_brain/app/chat_handler.py"
ASGI_PATH = ROOT / "host_brain/app/asgi.py"
INPUT_PORTS_PATH = ROOT / "host_brain/input_runtime/ports.py"
INPUT_COMPOSITION_PATH = ROOT / "host_brain/app/input_runtime_composition.py"
HTTP_HOOKS_PATH = ROOT / "host_brain/app/http_hooks.py"


def test_chat_admission_uses_typed_input_runtime_ports_without_locator() -> None:
    source = CHAT_HANDLER_PATH.read_text(encoding="utf-8-sig")
    tree = ast.parse(source)

    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    assert "host_brain.composition.process_services" not in imported_modules
    assert "host_brain.app.emotion_context" not in imported_modules
    assert "get_thalamus_agent" not in imported_names
    assert "get_ans_agent" not in imported_names
    assert "InputRuntimeApplicationPorts" in source
    assert "HOST_INPUT_RUNTIME_PORTS_NOT_INJECTED" in source
    assert "input_runtime_ports.require_thalamus_agent()" in source
    assert "input_runtime_ports.require_ans_agent()" not in source
    assert "input_runtime_ports.get_intent_engine()" in source
    assert "input_runtime_ports.evaluate_governor_with_timing(" in source
    assert "input_runtime_ports.resolve_chat_emotion_context(" in source


def test_input_and_host_boundaries_do_not_inject_ans_for_normal_chat() -> None:
    input_ports = INPUT_PORTS_PATH.read_text(encoding="utf-8-sig")
    input_composition = INPUT_COMPOSITION_PATH.read_text(encoding="utf-8-sig")
    http_hooks = HTTP_HOOKS_PATH.read_text(encoding="utf-8-sig")

    assert "get_ans_agent" not in input_ports
    assert "require_ans_agent" not in input_ports
    assert '"get_ans_agent"' not in input_composition
    assert "get_ans_agent=get_ans_agent" not in input_composition
    assert "require_ans_agent" not in http_hooks
    assert "set_event_loop(" not in http_hooks


def test_asgi_forwards_one_input_runtime_port_bundle_to_chat_handler() -> None:
    source = ASGI_PATH.read_text(encoding="utf-8-sig")

    assert "input_runtime_ports=input_runtime_ports" in source
    assert "tribe_runtime_agent_ports=tribe_runtime_agent_ports" in source
