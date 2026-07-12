from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from host_brain.agent_runtime.agent_executor import AgentExecutor, AgentStep
from host_brain.agent_runtime.result_contract import AgentResult
from host_brain.agent_runtime.work_order import AgentWorkOrder
from host_brain.app.agents.react_loop import ReactResult, ReactStep, astream_react_loop
from host_brain.app.sub_agent_protocol import SubAgentDispatcher, SubAgentRequest, SubAgentResponse
from host_brain.core.trace_redaction import (
    TRACE_PUBLIC_FIELDS,
    TRACE_SCHEMA_VERSION,
    TRACE_STEP_PUBLIC_FIELDS,
    build_public_agent_output,
    build_redacted_trace_step,
    read_decision_summary,
    redact_legacy_trace_payload,
    sanitize_public_payload,
    stable_payload_hash,
)


ROOT = Path(__file__).resolve().parents[2]
LEAKAGE_FIXTURES = ROOT / "tests" / "fixtures" / "nle_trace_leakage_cases.json"
FORBIDDEN_KEYS = {
    "thought",
    "raw_thought",
    "raw_reasoning",
    "reasoning",
    "reasoning_content",
    "reasoning_trace",
    "chain_of_thought",
    "cookie",
    "cookies",
    "secret",
    "token",
    "access_token",
    "api_key",
    "private_body",
    "private_memory_body",
}


def _fixtures() -> list[dict[str, object]]:
    return list(json.loads(LEAKAGE_FIXTURES.read_text(encoding="utf-8")))


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return {str(key).lower() for key in value} | {
            nested
            for item in value.values()
            for nested in _all_keys(item)
        }
    if isinstance(value, list):
        return {nested for item in value for nested in _all_keys(item)}
    return set()


def test_t_nle_12_agent_step_keeps_reasoning_ephemeral() -> None:
    step = AgentStep(
        step=1,
        thought="private chain of thought",
        tool_id="agent_read_file",
        tool_args={"path": "README.md", "input_ref": "work-order://wo-1"},
        observation={"evidence_refs": ["artifact://read/1"], "content": "file body"},
        duration_ms=7,
    )

    public = step.to_dict(input_refs=["request://req-1"])

    assert step.thought == "private chain of thought"
    assert set(public) == TRACE_STEP_PUBLIC_FIELDS
    assert public["schema_version"] == TRACE_SCHEMA_VERSION
    assert public["tool_id"] == "agent_read_file"
    assert public["input_refs"] == ["request://req-1", "work-order://wo-1"]
    assert public["evidence_refs"]
    assert "thought" not in public
    assert "private chain of thought" not in json.dumps(public, ensure_ascii=False)


def test_t_nle_12_react_scratchpad_uses_strict_redacted_contract() -> None:
    ephemeral = ReactStep(
        step=1,
        thought="inspect the private scratchpad",
        action_type="tool_call",
        action={"tool": "search_knowledge", "args": {"query": "safe query"}},
        observation={"evidence_ref": "knowledge://hit/1", "body": "not durable"},
        final_answer=None,
        ts_ms=123,
    )
    result = ReactResult(
        agent_id="retrieval_agent",
        final_answer="Knowledge lookup completed.",
        steps=[ephemeral],
        stopped_reason="FINAL_ANSWER",
        total_steps=1,
        confidence=0.85,
        input_refs=["request://req-2", "trace-input://sha256/abc"],
        ts_ms=456,
    )

    public = result.to_scratchpad_dict()

    assert ephemeral.thought == "inspect the private scratchpad"
    assert set(public) == TRACE_PUBLIC_FIELDS
    assert set(public["steps"][0]) == TRACE_STEP_PUBLIC_FIELDS
    assert public["decision_summary"] == "Knowledge lookup completed."
    assert public["output_refs"][0].startswith("trace-output://sha256/")
    assert public["steps"][0]["tool_id"] == "search_knowledge"
    assert FORBIDDEN_KEYS.isdisjoint(_all_keys(public))
    assert "inspect the private scratchpad" not in json.dumps(public, ensure_ascii=False)


def test_t_nle_12_agent_executor_persists_only_redacted_trace(monkeypatch, tmp_path: Path) -> None:
    thought_sentinel = "agent executor raw thought sentinel"
    long_answer = "publishable executor result " * 40

    from host_brain.model_runtime import build_live_production_prompt_registry

    class _AttemptService:
        def complete_sync(self, _request):  # noqa: ANN001, ANN202
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "thought": thought_sentinel,
                        "tool_id": "agent_finish",
                        "tool_args": {},
                        "final_answer": long_answer,
                    }
                ),
                success=True,
            )

    registry = build_live_production_prompt_registry()
    monkeypatch.setattr("host_brain.app.dependencies.get_prompt_registry", lambda: registry)
    monkeypatch.setattr("host_brain.app.dependencies.get_model_attempt_service", lambda: _AttemptService())
    monkeypatch.setattr(
        "host_brain.agent_runtime.agent_executor.model_attempt_result_trace",
        lambda _result: {
            "route_decision_id": "route-redaction-test",
            "final_provider": "test-provider",
            "final_model": "test-model",
            "attempts": [],
        },
    )
    order = AgentWorkOrder.from_mapping(
        {
            "work_order_id": "wo-trace-redaction",
            "domain": "code",
            "run_mode": "inspect",
            "objective": "inspect the repository",
            "metadata": {"request_id": "req-trace-redaction"},
        }
    )

    result = AgentExecutor().execute(order, workspace=tmp_path)
    public = result.to_dict()
    serialized = json.dumps(public, ensure_ascii=False)
    trace = public["metadata"]["trace"]

    assert result.result_text == long_answer.strip()
    assert public["result_text"] == long_answer.strip()
    assert thought_sentinel not in serialized
    assert FORBIDDEN_KEYS.isdisjoint(_all_keys(public))
    assert set(trace) == TRACE_PUBLIC_FIELDS
    assert trace["steps"][0]["action_type"] == "finish"
    assert trace["steps"][0]["output_refs"]
    assert trace["output_refs"] == result.metadata["trace"]["output_refs"]
    assert trace["payload_hash"] == result.metadata["trace"]["payload_hash"]


@pytest.mark.parametrize("fixture", _fixtures(), ids=lambda row: str(row["case_id"]))
def test_t_nle_12_secret_cookie_and_private_memory_are_referenced(fixture: dict[str, object]) -> None:
    public = build_redacted_trace_step(dict(fixture["step"]))
    serialized = json.dumps(public, ensure_ascii=False, sort_keys=True)

    assert set(public) == TRACE_STEP_PUBLIC_FIELDS
    assert str(fixture["sentinel"]) not in serialized
    assert FORBIDDEN_KEYS.isdisjoint(_all_keys(public))
    assert any(ref.startswith("redacted://sha256/") for ref in public["evidence_refs"])
    assert len(public["payload_hash"]) == 64


def test_t_nle_12_payload_hash_is_stable_across_mapping_order() -> None:
    left = {"b": [2, 1], "a": {"y": True, "x": "value"}}
    right = {"a": {"x": "value", "y": True}, "b": [2, 1]}

    assert stable_payload_hash(left) == stable_payload_hash(right)


def test_t_nle_12_agent_result_compatibility_does_not_republish_legacy_thought() -> None:
    result = AgentResult(
        status="EXECUTED",
        work_order_id="wo-legacy",
        summary="completed",
        result_text="safe result",
        metadata={
            "pool_provider": "test",
            "steps": [
                {
                    "step": 1,
                    "thought": "legacy raw thought sentinel",
                    "tool_id": "agent_finish",
                    "final_answer": "done",
                }
            ],
            "stopped_reason": "FINISH",
            "total_steps": 1,
        },
    )

    public = result.to_dict()
    serialized = json.dumps(public, ensure_ascii=False)

    assert "legacy raw thought sentinel" not in serialized
    assert FORBIDDEN_KEYS.isdisjoint(_all_keys(public))
    assert public["metadata"]["trace_schema_version"] == TRACE_SCHEMA_VERSION
    assert public["metadata"]["pool_provider"] == "test"
    assert public["metadata"]["steps"][0]["payload_hash"]


def test_t_nle_12_legacy_scratchpad_read_is_compatible_but_redacted() -> None:
    legacy = {
        "agent_id": "translate_agent",
        "final_answer": "api_key=sk-live-legacy-secret",
        "steps": [{"step": 1, "thought": "legacy thought", "action_type": "final_answer"}],
        "total_steps": 1,
        "stopped_reason": "FINAL_ANSWER",
    }

    public = redact_legacy_trace_payload(legacy)

    assert read_decision_summary(legacy) == (
        "Sensitive trace content withheld; inspect the authorized evidence reference."
    )
    assert "sk-live-legacy-secret" not in json.dumps(public, ensure_ascii=False)
    assert FORBIDDEN_KEYS.isdisjoint(_all_keys(public))


def test_t_nle_12_generic_api_adapter_removes_nested_forbidden_fields() -> None:
    historical_job = {
        "job_id": "job-1",
        "result": {
            "metadata": {
                "thought": "do not expose",
                "cookie": "session=do-not-expose",
                "safe_code": "OK",
            }
        },
    }

    public = sanitize_public_payload(historical_job)
    serialized = json.dumps(public, ensure_ascii=False)

    assert public["result"]["metadata"]["safe_code"] == "OK"
    assert "do not expose" not in serialized
    assert "session=do-not-expose" not in serialized
    assert FORBIDDEN_KEYS.isdisjoint(_all_keys(public))
    assert public["result"]["metadata"]["evidence_refs"]


def test_t_nle_12_stream_never_yields_ephemeral_react_objects(monkeypatch) -> None:
    sentinel = "stream raw thought must stay ephemeral"
    result = ReactResult(
        agent_id="retrieval_agent",
        final_answer="safe streamed output",
        steps=[
            ReactStep(
                step=1,
                thought=sentinel,
                action_type="final_answer",
                action=None,
                observation=None,
                final_answer="safe streamed output",
            )
        ],
        stopped_reason="FINAL_ANSWER",
        total_steps=1,
    )
    monkeypatch.setattr(
        "host_brain.app.agents.react_loop.run_react_loop",
        lambda **_kwargs: result,
    )

    async def _collect() -> list[dict[str, object]]:
        return [
            item
            async for item in astream_react_loop(
                agent_id="retrieval_agent",
                user_input="safe input",
                tools=[],
                gateway=None,
            )
        ]

    items = asyncio.run(_collect())
    serialized = json.dumps(items, ensure_ascii=False)

    assert len(items) == 2
    assert all(isinstance(item, dict) for item in items)
    assert sentinel not in serialized
    assert FORBIDDEN_KEYS.isdisjoint(_all_keys(items))
    assert set(items[-1]) == TRACE_PUBLIC_FIELDS


def test_t_nle_12_dispatcher_preserves_long_safe_output_without_using_trace() -> None:
    long_output = "safe translation paragraph " * 80
    dispatcher = SubAgentDispatcher()

    def _handler(_payload, _context):  # noqa: ANN001
        return {
            "agent_output": build_public_agent_output(long_output),
            "react_scratchpad": {
                "schema_version": TRACE_SCHEMA_VERSION,
                "decision_summary": "short trace summary",
            },
        }

    async def _no_gateway(**_kwargs) -> None:  # noqa: ANN003
        return None

    dispatcher._handler_map["translate_agent"] = _handler
    dispatcher._ensure_runtime_gateway = _no_gateway  # type: ignore[method-assign]
    response = asyncio.run(
        dispatcher.dispatch(
            SubAgentRequest(
                agent_id="translate_agent",
                task="translate",
                request_id="req-long-output",
            )
        )
    )

    assert response.success is True
    assert response.result == long_output.strip()
    assert len(response.result) > 280


def test_t_nle_12_dispatch_audit_persists_summary_and_output_ref_only(monkeypatch) -> None:
    writes: list[dict[str, object]] = []

    class _Ledger:
        def log_skill_exec(self, payload):  # noqa: ANN001
            writes.append(dict(payload))

    monkeypatch.setattr("host_brain.app.dependencies.get_ledger", lambda: _Ledger())
    dispatcher = SubAgentDispatcher()
    dispatcher._log_dispatch(
        SubAgentRequest(agent_id="translate_agent", task="translate", request_id="req-audit"),
        SubAgentResponse(
            success=True,
            agent_id="translate_agent",
            result="api_key=sk-TNLE12-AUDIT-SECRET",
        ),
    )

    assert len(writes) == 1
    serialized = json.dumps(writes[0], ensure_ascii=False)
    assert "sk-TNLE12-AUDIT-SECRET" not in serialized
    assert writes[0]["result_digest"].startswith("Sensitive trace content withheld")
    assert str(writes[0]["evidence_ref"]).startswith("sub-agent-output://sha256/")


@pytest.mark.parametrize("fixture", _fixtures(), ids=lambda row: f"output-{row['case_id']}")
def test_t_nle_12_publishable_output_references_sensitive_bodies(fixture: dict[str, object]) -> None:
    sentinel = str(fixture["sentinel"])
    sensitive_outputs = {
        "secret": f"api_key={sentinel}",
        "cookie": f"Cookie: session={sentinel}",
        "private_memory": f"<private-memory>{sentinel}</private-memory>",
    }
    output = build_public_agent_output(sensitive_outputs[str(fixture["case_id"])])

    assert sentinel not in output["text"]
    assert output["redacted"] is True
    assert output["output_refs"][0].startswith("agent-output://sha256/")
