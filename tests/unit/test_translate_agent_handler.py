from __future__ import annotations

import inspect

from host_brain.agent_runtime.agent_registry import AGENT_ID_TRANSLATE
from host_brain.app.handlers import translate_agent_handler as module
from host_brain.app.handlers.translate_agent_handler import (
    TRANSLATE_MODEL_NODE_FAILED,
    TRANSLATE_MODEL_NODE_UNAVAILABLE,
    TRANSLATE_RESULT_READY,
    translate_agent_handler,
)


def _model_result(text: str = "Artificial intelligence") -> dict[str, object]:
    return {
        "gateway_status": "OK",
        "payload": {"response_text": text, "confidence": 0.91},
    }


def test_projects_precomputed_governed_model_result() -> None:
    result = translate_agent_handler(
        {
            "request_id": "translate-001",
            "user_input": "翻译：人工智能",
            "workbench_translate": _model_result(),
        }
    )

    assert result["agent_output"]["text"] == "Artificial intelligence"
    assert result["agent_artifacts"][AGENT_ID_TRANSLATE]["confidence"] == 0.91
    assert result["translate_agent_trace"] == {
        **result["translate_agent_trace"],
        "agent_id": AGENT_ID_TRANSLATE,
        "mode": "governed_model_node",
        "reason_code": TRANSLATE_RESULT_READY,
        "model_node_id": "workbench_translate",
        "gateway_status": "OK",
        "side_effects_executed": False,
    }
    assert "react_scratchpad" not in result


def test_uses_explicit_graph_model_node_port() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def invoke(node_id: str, payload: dict[str, object]) -> dict[str, object]:
        calls.append((node_id, dict(payload)))
        return {"workbench_translate": _model_result("AI")}

    result = translate_agent_handler(
        {"request_id": "translate-002", "user_input": "翻译 AI"},
        {"invoke_model_node": invoke},
    )

    assert calls == [("workbench_translate", {"request_id": "translate-002", "user_input": "翻译 AI"})]
    assert result["agent_output"]["text"] == "AI"


def test_missing_model_node_fails_closed_without_execution() -> None:
    result = translate_agent_handler({"request_id": "translate-003", "user_input": "翻译"})

    trace = result["translate_agent_trace"]
    assert trace["reason_code"] == TRANSLATE_MODEL_NODE_UNAVAILABLE
    assert trace["gateway_status"] == "DEGRADED"
    assert trace["side_effects_executed"] is False
    assert result["agent_artifacts"][AGENT_ID_TRANSLATE]["confidence"] == 0.0


def test_model_node_exception_is_redacted_to_stable_failure() -> None:
    def fail(_node_id: str, _payload: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("provider secret must not escape")

    result = translate_agent_handler(
        {"request_id": "translate-004", "user_input": "翻译"},
        {"invoke_model_node": fail},
    )

    trace = result["translate_agent_trace"]
    assert trace["reason_code"] == TRANSLATE_MODEL_NODE_FAILED
    assert "provider secret" not in str(result)
    assert result["agent_artifacts"][AGENT_ID_TRANSLATE]["risk_flags"] == [
        "MODEL_NODE_ERROR:RuntimeError"
    ]


def test_handler_has_no_react_or_process_locator_dependency() -> None:
    source = inspect.getsource(module)
    assert "process_services" not in source
