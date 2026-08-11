from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

import pytest

from host_brain.app.chat_immune_gate import evaluate_immune_gate_for_chat
from host_brain.app.input_runtime_composition import (
    build_input_runtime_application_ports,
)
from host_brain.app.schemas import ChatRequest
from host_brain.app.tribe_composition import TribeRuntimeAgentPorts
from host_brain.input_runtime.contracts_and_normalization import (
    _evaluate_immune_gate_for_input,
)


ROOT = Path(__file__).resolve().parents[2]
CHAT_IMMUNE_GATE_PATH = ROOT / "host_brain/app/chat_immune_gate.py"
INPUT_CONTRACTS_PATH = ROOT / "host_brain/input_runtime/contracts_and_normalization.py"
ASGI_PATH = ROOT / "host_brain/app/asgi.py"


class _ImmuneAgent:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def inspect_text(self, **kwargs):  # noqa: ANN003, ANN201
        self.calls.append(dict(kwargs))
        return {"decision": "ALLOW", "reason_codes": []}


def test_chat_immune_gate_uses_an_explicit_host_composed_agent_port() -> None:
    agent = _ImmuneAgent()

    result = evaluate_immune_gate_for_chat(
        ChatRequest(
            message="请审查这个输入",
            session_id="session-ne01-immune",
            person_id="person-ne01-immune",
            input_source="HTTP",
        ),
        immune_agent_provider=lambda: agent,
    )

    assert result == {"decision": "ALLOW", "reason_codes": []}
    assert agent.calls == [
        {
            "text": "请审查这个输入",
            "source": "http.v1.chat",
            "metadata": {
                "session_id": "session-ne01-immune",
                "person_id": "person-ne01-immune",
                "input_source": "HTTP",
            },
        }
    ]


def test_tribe_runtime_ports_fail_closed_when_chat_immune_agent_is_missing() -> None:
    ports = TribeRuntimeAgentPorts(
        get_thalamus_agent=object,
    )

    with pytest.raises(RuntimeError, match="HOST_TRIBE_IMMUNE_AGENT_NOT_INJECTED"):
        ports.require_immune_agent()


def test_input_immune_gate_uses_an_explicit_input_runtime_port() -> None:
    agent = _ImmuneAgent()
    ports = replace(
        build_input_runtime_application_ports(),
        get_immune_agent=lambda: agent,
    )

    result = _evaluate_immune_gate_for_input(
        text="请审查这个网关输入",
        source="GATEWAY",
        session_id="session-ne01-input-immune",
        person_id="person-ne01-input-immune",
        ports=ports,
    )

    assert result == {"decision": "ALLOW", "reason_codes": []}
    assert agent.calls == [
        {
            "text": "请审查这个网关输入",
            "source": "http.v1.host.input.gateway",
            "metadata": {
                "session_id": "session-ne01-input-immune",
                "person_id": "person-ne01-input-immune",
            },
        }
    ]


def test_input_immune_gate_fails_closed_when_port_is_missing() -> None:
    with pytest.raises(RuntimeError, match="HOST_INPUT_IMMUNE_AGENT_NOT_INJECTED"):
        _evaluate_immune_gate_for_input(
            text="请审查这个输入",
            source="ASR",
            session_id="session-ne01-input-missing",
            ports=build_input_runtime_application_ports(),
        )


def test_chat_immune_gate_has_no_request_time_singleton_import() -> None:
    source = CHAT_IMMUNE_GATE_PATH.read_text(encoding="utf-8-sig")
    tree = ast.parse(source)
    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    assert "get_immune_agent" not in imported_names
    assert "immune_agent_provider" in source
    assert "tribe_runtime_agent_ports.require_immune_agent" in ASGI_PATH.read_text(
        encoding="utf-8-sig"
    )

    input_source = INPUT_CONTRACTS_PATH.read_text(encoding="utf-8-sig")
    input_tree = ast.parse(input_source)
    input_imported_names = {
        alias.name
        for node in ast.walk(input_tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "get_immune_agent" not in input_imported_names
    assert "ports.require_immune_agent()" in input_source
