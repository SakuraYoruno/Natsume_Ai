from __future__ import annotations

from dataclasses import replace

import pytest

from host_brain.model_runtime import (
    CachePolicy,
    OutputContract,
    PromptDigestError,
    PromptRef,
    PromptRegistry,
    PromptSchemaError,
    PromptVariableError,
    UnknownPromptError,
)


def test_static_prompt_is_versioned_rendered_and_digest_checked() -> None:
    registry = PromptRegistry()
    registry.register_static(
        "chat.reply",
        {
            "system_prompt": "Reply as {persona}.",
            "user_prompt_template": "User said: {message}",
        },
        version="v2.1.0",
        output_contract=OutputContract(kind="json_object"),
        cache_policy=CachePolicy(scope="request", ttl_ms=1000),
    )

    rendered = registry.render(
        PromptRef(
            "chat.reply",
            {"persona": "Natsume", "message": "hello"},
            version="v2.1.0",
        )
    )

    assert rendered.version == "v2.1.0"
    assert rendered.messages == (
        {"role": "system", "content": "Reply as Natsume."},
        {"role": "user", "content": "User said: hello"},
    )
    assert rendered.output_contract.kind.value == "json_object"
    assert len(rendered.digest) == 64
    assert registry.render(
        PromptRef(
            "chat.reply",
            {"persona": "Natsume", "message": "hello"},
            digest=rendered.digest,
        )
    ).digest == rendered.digest

    with pytest.raises(PromptDigestError):
        registry.validate_rendered(replace(rendered, digest="0" * 64))


def test_registry_fails_closed_for_unknown_missing_variables_and_broader_cache() -> None:
    registry = PromptRegistry()
    registry.register_static("strict.prompt", "Hello {name}")

    with pytest.raises(UnknownPromptError):
        registry.render(PromptRef("missing.prompt"))
    with pytest.raises(PromptVariableError, match="name"):
        registry.render(PromptRef("strict.prompt"))
    with pytest.raises(PromptVariableError, match="unexpected.*renamed_name"):
        registry.render(PromptRef("strict.prompt", {"name": "A", "renamed_name": "B"}))
    with pytest.raises(PromptSchemaError, match="cannot broaden"):
        registry.render(
            PromptRef(
                "strict.prompt",
                {"name": "A"},
                cache_policy=CachePolicy(scope="global", ttl_ms=1000),
            )
        )


def test_dynamic_renderer_uses_same_strict_contract_and_digest_authority() -> None:
    registry = PromptRegistry()
    calls: list[dict[str, object]] = []

    def _renderer(variables):
        calls.append(dict(variables))
        return [
            {"role": "system", "content": "Dynamic"},
            {"role": "user", "content": str(variables["payload"])},
        ]

    registry.register_renderer(
        "agent.dynamic",
        _renderer,
        required_variables=("payload",),
        output_contract=OutputContract(
            kind="json_schema",
            schema={
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
                "additionalProperties": False,
            },
        ),
    )

    rendered = registry.render(PromptRef("agent.dynamic", {"payload": "work"}))

    assert calls == [{"payload": "work"}]
    assert rendered.messages[-1]["content"] == "work"
    assert rendered.output_contract.kind.value == "json_schema"


def test_invalid_static_and_output_schemas_are_rejected_at_registration() -> None:
    registry = PromptRegistry()
    with pytest.raises(PromptSchemaError):
        registry.register_static("bad.mapping", {"not_a_prompt": "value"})
    with pytest.raises(ValueError, match="missing from properties"):
        OutputContract(
            kind="json_schema",
            schema={"type": "object", "properties": {}, "required": ["missing"]},
        )
