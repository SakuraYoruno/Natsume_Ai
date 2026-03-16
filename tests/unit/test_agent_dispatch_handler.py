"""P3：agent_dispatch_handler 单元测试（allowlist、安全降级）。"""

from __future__ import annotations

from host_brain.app.agents.agent_registry import AGENT_ID_CODE, AGENT_ID_INTENT, AGENT_ID_TRANSLATE
from host_brain.app.handlers.agent_dispatch_handler import (
    DEFAULT_AGENT_DISPATCH_ALLOWLIST,
    agent_dispatch_handler,
)


def test_allowlist_contains_only_code_and_translate() -> None:
    """默认 allowlist 仅含 code_agent、translate_agent，不含 intent/chat。"""
    assert AGENT_ID_CODE in DEFAULT_AGENT_DISPATCH_ALLOWLIST
    assert AGENT_ID_TRANSLATE in DEFAULT_AGENT_DISPATCH_ALLOWLIST
    assert AGENT_ID_INTENT not in DEFAULT_AGENT_DISPATCH_ALLOWLIST
    assert "chat_agent" not in DEFAULT_AGENT_DISPATCH_ALLOWLIST


def test_fallback_when_active_agent_not_in_allowlist() -> None:
    """active_agent_id 不在 allowlist 时安全降级，不跨图逃逸，返回含 risk_flags 的 artifact 并写 __next_node__。"""
    payload = {
        "active_agent_id": AGENT_ID_INTENT,
        "request_id": "test-req",
        "supervisor_dispatch": {"need_judge": True},
    }
    out = agent_dispatch_handler(payload, {"request_id": "test-req", "node_id": "agent_dispatch_node"})
    assert out.get("agent_artifacts") is not None
    art = out["agent_artifacts"].get(AGENT_ID_INTENT) or out["agent_artifacts"].get("unknown")
    assert art is not None, out
    assert "AGENT_NOT_IN_SUPERVISED_ALLOWLIST" in (art.get("risk_flags") or [])
    assert out.get("__next_node__") in ("judge_agent_node", "renderer_ja_c_style")


def test_fallback_when_active_agent_empty() -> None:
    """active_agent_id 为空时按 unknown 降级，仍写 __next_node__。"""
    payload = {"request_id": "test-req"}
    out = agent_dispatch_handler(payload, {"request_id": "test-req"})
    artifacts = out.get("agent_artifacts") or {}
    assert "unknown" in artifacts or any("fallback" in str(a.get("artifact_ref", "")) for a in artifacts.values())
    assert out.get("__next_node__") in ("judge_agent_node", "renderer_ja_c_style")


def test_fallback_when_invoke_handler_unavailable() -> None:
    """context 无 invoke_handler 时安全降级。"""
    payload = {"active_agent_id": AGENT_ID_CODE, "request_id": "test-req"}
    out = agent_dispatch_handler(payload, {"request_id": "test-req"})  # no invoke_handler
    artifacts = out.get("agent_artifacts") or {}
    code_art = artifacts.get(AGENT_ID_CODE)
    assert code_art is not None
    assert "invoke_handler_unavailable" in str(code_art.get("risk_flags") or [])
