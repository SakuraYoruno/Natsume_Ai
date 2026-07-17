from __future__ import annotations

from types import MappingProxyType

import pytest

from host_brain.domain_packs.production_runtime import (
    GOVERNED_PRODUCTION_EXECUTION_MODE,
)
from host_brain.skills import executor_registry as registry_module


class _ModelAttemptService:
    def __init__(self, prompt_registry: object) -> None:
        self.prompt_registry = prompt_registry


def _trusted_context(*, model_attempt_service: object) -> dict[str, object]:
    return {
        "_skill_execution_authority": MappingProxyType(
            {"authority_id": "skill-authority://sealed/d15"}
        ),
        "_governed_execution_context": MappingProxyType(
            {"execution_mode": GOVERNED_PRODUCTION_EXECUTION_MODE}
        ),
        "model_attempt_service": model_attempt_service,
    }


def test_governed_codex_runtime_receives_model_ports_only_from_composed_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompt_registry = object()
    model_attempt_service = _ModelAttemptService(prompt_registry)
    captured: dict[str, object] = {}

    def fake_execute(payload, *, model_attempt_service, prompt_registry):  # noqa: ANN001
        captured["payload"] = payload
        captured["model_attempt_service"] = model_attempt_service
        captured["prompt_registry"] = prompt_registry
        return {"status": "EXECUTED"}

    monkeypatch.setattr(
        registry_module,
        "execute_agent_runtime_payload",
        fake_execute,
    )
    context = _trusted_context(model_attempt_service=model_attempt_service)
    context["prompt_registry"] = object()

    result = registry_module._execute_agent_runtime_with_context(  # noqa: SLF001
        {"tool": "codex_runtime", "_context": context}
    )

    assert result == {"status": "EXECUTED"}
    assert captured["model_attempt_service"] is model_attempt_service
    assert captured["prompt_registry"] is prompt_registry


@pytest.mark.parametrize(
    "context",
    [
        {},
        {
            "_governed_execution_context": {
                "execution_mode": GOVERNED_PRODUCTION_EXECUTION_MODE
            },
            "model_attempt_service": _ModelAttemptService(object()),
        },
        {
            "_skill_execution_authority": {"authority_id": "sealed"},
            "model_attempt_service": _ModelAttemptService(object()),
        },
        {
            "_skill_execution_authority": {"authority_id": "sealed"},
            "_governed_execution_context": {
                "execution_mode": "legacy_conversational_runtime"
            },
            "model_attempt_service": _ModelAttemptService(object()),
        },
        {
            "_skill_execution_authority": {"authority_id": "sealed"},
            "_governed_execution_context": {
                "execution_mode": GOVERNED_PRODUCTION_EXECUTION_MODE
            },
        },
        {
            "_skill_execution_authority": {"authority_id": "sealed"},
            "_governed_execution_context": {
                "execution_mode": GOVERNED_PRODUCTION_EXECUTION_MODE
            },
            "model_attempt_service": object(),
        },
    ],
)
def test_codex_runtime_rejects_uncomposed_or_non_governed_ports(
    context: dict[str, object],
) -> None:
    with pytest.raises(RuntimeError, match=r"^AGENT_RUNTIME_PORTS_NOT_COMPOSED$"):
        registry_module._execute_agent_runtime_with_context(  # noqa: SLF001
            {"tool": "codex_runtime", "_context": context}
        )


def test_codex_runtime_rejects_legacy_direct_executor_payload() -> None:
    with pytest.raises(RuntimeError, match=r"^AGENT_RUNTIME_PORTS_NOT_COMPOSED$"):
        registry_module._execute_agent_runtime_with_context(  # noqa: SLF001
            {"tool": "codex_runtime", "work_order": {}}
        )
