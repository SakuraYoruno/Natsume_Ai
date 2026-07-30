from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CHAT_HANDLER_PATH = ROOT / "host_brain/app/chat_handler.py"
ASGI_PATH = ROOT / "host_brain/app/asgi.py"


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


def test_asgi_forwards_one_input_runtime_port_bundle_to_chat_handler() -> None:
    source = ASGI_PATH.read_text(encoding="utf-8-sig")

    assert "input_runtime_ports=input_runtime_ports" in source
    assert "tribe_runtime_agent_ports=tribe_runtime_agent_ports" in source
