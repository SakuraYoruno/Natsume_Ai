from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INTENT_PATH = ROOT / "host_brain" / "app" / "handlers" / "intent_agent_handler.py"
GOVERNED_SKILL_PATH = (
    ROOT / "host_brain" / "app" / "governed_skill_execution.py"
)
EXECUTIVE_WIRING_PATH = ROOT / "host_brain" / "app" / "runtime_port_wiring.py"


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
    source = GOVERNED_SKILL_PATH.read_text(encoding="utf-8-sig")
    assert "get_sub_agent_dispatcher" not in source
    assert "sub_agent_request_factory" not in source
    assert "_dispatch_to_sub_agent" not in source
