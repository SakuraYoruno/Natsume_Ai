from __future__ import annotations

import inspect

from host_brain.agent_runtime.agent_registry import AGENT_ID_RETRIEVAL
from host_brain.app.handlers import retrieval_agent_handler as module
from host_brain.app.handlers.retrieval_agent_handler import (
    RETRIEVAL_CAPABILITY_ID,
    RETRIEVAL_DOMAIN_HANDOFF_REQUIRED,
    RETRIEVAL_RESULT_READY,
    retrieval_agent_handler,
)
from host_brain.domain_packs.research_browser import RESEARCH_BROWSER_PACK_ID


def _retrieval_result(*, items: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "gateway_status": "OK",
        "payload": {
            "source": "vector_store",
            "results": items if items is not None else [{"id": "doc-1"}, {"id": "doc-2"}],
            "response_text": "找到相关资料。",
            "confidence": 0.88,
        },
    }


def test_projects_readonly_retrieval_result() -> None:
    result = retrieval_agent_handler(
        {
            "request_id": "retrieve-001",
            "user_input": "查询 GraphRAG",
            "workbench_retrieval": _retrieval_result(),
        }
    )

    trace = result["retrieval_agent_trace"]
    assert trace["agent_id"] == AGENT_ID_RETRIEVAL
    assert trace["mode"] == "readonly_result"
    assert trace["reason_code"] == RETRIEVAL_RESULT_READY
    assert trace["retrieval_source"] == "vector_store"
    assert trace["result_count"] == 2
    assert trace["side_effects_executed"] is False
    assert result["agent_output"]["text"] == "找到相关资料。"
    assert "react_scratchpad" not in result


def test_empty_readonly_result_keeps_stable_contract() -> None:
    result = retrieval_agent_handler(
        {
            "request_id": "retrieve-002",
            "workbench_retrieval": {
                "gateway_status": "OK",
                "payload": {"source": "knowledge_base", "results": []},
            },
        }
    )

    trace = result["retrieval_agent_trace"]
    assert trace["result_count"] == 0
    assert trace["retrieval_source"] == "knowledge_base"
    assert result["agent_artifacts"][AGENT_ID_RETRIEVAL]["artifact_ref"].startswith(
        "artifact://retrieve-002/"
    )


def test_missing_result_prepares_canonical_domain_handoff() -> None:
    result = retrieval_agent_handler(
        {"request_id": "retrieve-003", "user_input": "查一下 GraphRAG"}
    )

    handoff = result["retrieval_domain_handoff"]
    assert handoff["domain_pack"] == {
        "pack_id": RESEARCH_BROWSER_PACK_ID,
        "capability_id": RETRIEVAL_CAPABILITY_ID,
        "task_id": "research.browser.worker",
        "request_contract": "host_brain.domain_packs.contracts.DomainPackRequest",
        "authority_path": "CapabilityBroker -> DomainPackRegistry -> TaskPort",
    }
    assert handoff["request_material"]["query"] == "查一下 GraphRAG"
    assert handoff["request_material"]["trusted_authority"] is False
    assert handoff["side_effects_executed"] is False
    assert result["retrieval_agent_trace"]["reason_code"] == RETRIEVAL_DOMAIN_HANDOFF_REQUIRED


def test_handoff_does_not_claim_capability_execution() -> None:
    result = retrieval_agent_handler({"request_id": "retrieve-004", "query": "x"})

    assert result["retrieval_agent_trace"]["gateway_status"] == "NOT_EXECUTED"
    assert result["retrieval_agent_trace"]["side_effects_executed"] is False
    assert result["agent_artifacts"][AGENT_ID_RETRIEVAL]["confidence"] == 0.5


def test_handler_has_no_react_or_process_locator_dependency() -> None:
    source = inspect.getsource(module)
    assert "process_services" not in source
