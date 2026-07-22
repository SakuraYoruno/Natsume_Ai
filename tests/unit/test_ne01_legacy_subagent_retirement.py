from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_PATH = ROOT / "host_brain" / "app" / "sub_agent_protocol.py"
INTENT_PATH = ROOT / "host_brain" / "app" / "handlers" / "intent_agent_handler.py"
EXECUTIVE_SKILL_PATH = ROOT / "host_brain" / "executive_runtime" / "skill_execution.py"
EXECUTIVE_WIRING_PATH = ROOT / "host_brain" / "app" / "runtime_port_wiring.py"


def _class_methods(path: Path, class_name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    target = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    return {
        node.name
        for node in target.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_legacy_sub_agent_dispatcher_exposes_only_fail_closed_dispatch() -> None:
    source = PROTOCOL_PATH.read_text(encoding="utf-8-sig")

    assert _class_methods(PROTOCOL_PATH, "SubAgentDispatcher") == {"dispatch"}
    for forbidden in (
        "_handler_map",
        "_ensure_handlers",
        "_ensure_runtime_gateway",
        "_log_dispatch",
        "get_sub_agent_dispatcher",
        "asyncio.to_thread",
        "resolve_host_llm_runtime_async",
        "get_ledger",
    ):
        assert forbidden not in source


def test_intent_handoff_has_no_legacy_model_execution_surface() -> None:
    source = INTENT_PATH.read_text(encoding="utf-8-sig")

    for forbidden in (
        "executive_router_dispatch",
        "gateway.complete",
        "resolve_prompt_contract_text",
        "model_backed_governed_execution_handoff",
    ):
        assert forbidden not in source
    assert '"execution_surface": "governed_runtime_admission_required"' in source


def test_retired_delegate_ports_are_removed_from_executive_compatibility_wiring() -> None:
    assert not EXECUTIVE_WIRING_PATH.exists()
    assert not (ROOT / "host_brain" / "executive_runtime" / "ports.py").exists()
    source = EXECUTIVE_SKILL_PATH.read_text(encoding="utf-8-sig")
    assert "get_sub_agent_dispatcher" not in source
    assert "sub_agent_request_factory" not in source
    assert "_dispatch_to_sub_agent" not in source
