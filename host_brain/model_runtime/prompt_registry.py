"""Strict prompt registry for model-facing prompts.

Unknown ids, missing variables and malformed renderer output are programming
errors.  They are never converted to an empty prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from string import Formatter
import threading
from typing import Any, Callable, Iterable, Mapping, Sequence

from host_brain.model_runtime.contracts import (
    CachePolicy,
    CacheScope,
    JsonMapping,
    OutputContract,
    PromptMessage,
    PromptRef,
    RenderedPrompt,
    rendered_prompt_digest,
)


class PromptRegistryError(ValueError):
    """Base error for prompt definition and rendering failures."""


class UnknownPromptError(PromptRegistryError):
    pass


class DuplicatePromptError(PromptRegistryError):
    pass


class PromptVersionError(PromptRegistryError):
    pass


class PromptDigestError(PromptRegistryError):
    pass


class PromptVariableError(PromptRegistryError):
    pass


class PromptSchemaError(PromptRegistryError):
    pass


PromptRenderer = Callable[[Mapping[str, Any]], Any]


@dataclass(frozen=True, slots=True)
class PromptDefinitionInfo:
    prompt_id: str
    version: str
    kind: str
    required_variables: tuple[str, ...]
    output_contract: OutputContract
    cache_policy: CachePolicy
    metadata: JsonMapping = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _PromptDefinition:
    info: PromptDefinitionInfo
    message_templates: tuple[PromptMessage, ...] | None = None
    renderer: PromptRenderer | None = None


def _required_text(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise PromptSchemaError(f"{field_name} must not be empty")
    return text


def _normalize_output_contract(value: OutputContract | Mapping[str, Any] | None) -> OutputContract:
    if value is None:
        return OutputContract()
    if isinstance(value, OutputContract):
        return value
    try:
        return OutputContract(**dict(value))
    except (TypeError, ValueError) as exc:
        raise PromptSchemaError(f"invalid output contract: {exc}") from exc


def _normalize_cache_policy(value: CachePolicy | Mapping[str, Any] | None) -> CachePolicy:
    if value is None:
        return CachePolicy()
    if isinstance(value, CachePolicy):
        return value
    try:
        return CachePolicy(**dict(value))
    except (TypeError, ValueError) as exc:
        raise PromptSchemaError(f"invalid cache policy: {exc}") from exc


def _message_templates_from_static(content: Any) -> tuple[dict[str, Any], ...]:
    if isinstance(content, str):
        return ({"role": "user", "content": content},)

    if isinstance(content, Mapping):
        payload = dict(content)
        if "messages" in payload:
            return _normalize_messages(payload["messages"], context="static messages")
        if "system_prompt" in payload and (
            "user_prompt_template" in payload or "user_prompt" in payload
        ):
            user_content = payload.get("user_prompt_template", payload.get("user_prompt"))
            return _normalize_messages(
                (
                    {"role": "system", "content": payload["system_prompt"]},
                    {"role": "user", "content": user_content},
                ),
                context="static system/user prompt",
            )
        if "template" in payload:
            return _normalize_messages(
                ({"role": payload.get("role") or "user", "content": payload["template"]},),
                context="static template",
            )
        if "role" in payload and "content" in payload:
            return _normalize_messages((payload,), context="static message")
        raise PromptSchemaError(
            "static prompt mapping must define messages, system/user prompts, template, or role/content"
        )

    if isinstance(content, Sequence) and not isinstance(content, (str, bytes, bytearray)):
        return _normalize_messages(content, context="static message sequence")
    raise PromptSchemaError(f"unsupported static prompt content: {type(content).__name__}")


def _normalize_messages(value: Any, *, context: str) -> tuple[dict[str, Any], ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise PromptSchemaError(f"{context} must be a message sequence")
    messages: list[dict[str, Any]] = []
    allowed_roles = {"system", "developer", "user", "assistant", "tool"}
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise PromptSchemaError(f"{context}[{index}] must be an object")
        message = dict(raw)
        role = str(message.get("role") or "").strip().lower()
        if role not in allowed_roles:
            raise PromptSchemaError(f"{context}[{index}] has unsupported role: {role or '<empty>'}")
        if "content" not in message or message["content"] is None:
            raise PromptSchemaError(f"{context}[{index}] requires content")
        if isinstance(message["content"], str) and not message["content"].strip():
            raise PromptSchemaError(f"{context}[{index}] content must not be empty")
        message["role"] = role
        messages.append(message)
    if not messages:
        raise PromptSchemaError(f"{context} must contain at least one message")
    return tuple(messages)


def _format_fields(value: Any) -> set[str]:
    fields: set[str] = set()
    if isinstance(value, str):
        try:
            parsed = Formatter().parse(value)
            for _literal, field_name, _format_spec, _conversion in parsed:
                if field_name is None:
                    continue
                root = str(field_name).split(".", 1)[0].split("[", 1)[0].strip()
                if not root:
                    raise PromptSchemaError("positional prompt placeholders are not supported")
                fields.add(root)
        except ValueError as exc:
            raise PromptSchemaError(f"invalid prompt format string: {exc}") from exc
    elif isinstance(value, Mapping):
        for item in value.values():
            fields.update(_format_fields(item))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            fields.update(_format_fields(item))
    return fields


class _StrictVariables(dict[str, Any]):
    def __missing__(self, key: str) -> Any:
        raise PromptVariableError(f"missing prompt variable: {key}")


def _format_value(value: Any, variables: Mapping[str, Any]) -> Any:
    if isinstance(value, str):
        try:
            return value.format_map(_StrictVariables(variables))
        except PromptVariableError:
            raise
        except (KeyError, IndexError, AttributeError, ValueError) as exc:
            raise PromptVariableError(f"failed to format prompt value: {exc}") from exc
    if isinstance(value, Mapping):
        return {key: _format_value(item, variables) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_format_value(item, variables) for item in value)
    if isinstance(value, list):
        return [_format_value(item, variables) for item in value]
    return value


def _cache_rank(policy: CachePolicy) -> int:
    return {
        CacheScope.NONE: 0,
        CacheScope.REQUEST: 1,
        CacheScope.GLOBAL: 2,
    }[policy.scope]


def _restricted_cache_policy(registered: CachePolicy, requested: CachePolicy | None) -> CachePolicy:
    if requested is None:
        return registered
    if _cache_rank(requested) > _cache_rank(registered):
        raise PromptSchemaError("PromptRef cache policy cannot broaden the registered policy")
    if requested.scope is registered.scope and requested.ttl_ms > registered.ttl_ms:
        raise PromptSchemaError("PromptRef cache ttl cannot exceed the registered ttl")
    return requested


class PromptRegistry:
    """Thread-safe registry for static prompt specs and dynamic renderers."""

    def __init__(self) -> None:
        self._definitions: dict[str, _PromptDefinition] = {}
        self._lock = threading.RLock()

    def register_static(
        self,
        prompt_id: str,
        content: Any,
        *,
        version: str = "v1.0.0",
        output_contract: OutputContract | Mapping[str, Any] | None = None,
        cache_policy: CachePolicy | Mapping[str, Any] | None = None,
        required_variables: Iterable[str] = (),
        metadata: Mapping[str, Any] | None = None,
        replace_existing: bool = False,
    ) -> PromptDefinitionInfo:
        templates = _message_templates_from_static(content)
        discovered = _format_fields(templates)
        declared = {_required_text(item, field_name="required variable") for item in required_variables}
        return self._register(
            prompt_id=prompt_id,
            version=version,
            kind="static",
            required_variables=discovered | declared,
            output_contract=_normalize_output_contract(output_contract),
            cache_policy=_normalize_cache_policy(cache_policy),
            metadata=metadata,
            message_templates=templates,
            renderer=None,
            replace_existing=replace_existing,
        )

    def register_renderer(
        self,
        prompt_id: str,
        renderer: PromptRenderer,
        *,
        version: str = "v1.0.0",
        output_contract: OutputContract | Mapping[str, Any] | None = None,
        cache_policy: CachePolicy | Mapping[str, Any] | None = None,
        required_variables: Iterable[str] = (),
        metadata: Mapping[str, Any] | None = None,
        replace_existing: bool = False,
    ) -> PromptDefinitionInfo:
        if not callable(renderer):
            raise PromptSchemaError("dynamic prompt renderer must be callable")
        required = {_required_text(item, field_name="required variable") for item in required_variables}
        return self._register(
            prompt_id=prompt_id,
            version=version,
            kind="dynamic",
            required_variables=required,
            output_contract=_normalize_output_contract(output_contract),
            cache_policy=_normalize_cache_policy(cache_policy),
            metadata=metadata,
            message_templates=None,
            renderer=renderer,
            replace_existing=replace_existing,
        )

    def _register(
        self,
        *,
        prompt_id: str,
        version: str,
        kind: str,
        required_variables: set[str],
        output_contract: OutputContract,
        cache_policy: CachePolicy,
        metadata: Mapping[str, Any] | None,
        message_templates: tuple[PromptMessage, ...] | None,
        renderer: PromptRenderer | None,
        replace_existing: bool,
    ) -> PromptDefinitionInfo:
        normalized_id = _required_text(prompt_id, field_name="prompt_id")
        normalized_version = _required_text(version, field_name="version")
        info = PromptDefinitionInfo(
            prompt_id=normalized_id,
            version=normalized_version,
            kind=kind,
            required_variables=tuple(sorted(required_variables)),
            output_contract=output_contract,
            cache_policy=cache_policy,
            metadata=dict(metadata or {}),
        )
        definition = _PromptDefinition(
            info=info,
            message_templates=message_templates,
            renderer=renderer,
        )
        with self._lock:
            if normalized_id in self._definitions and not replace_existing:
                raise DuplicatePromptError(f"prompt already registered: {normalized_id}")
            self._definitions[normalized_id] = definition
        return info

    def render(self, ref: PromptRef | str, variables: Mapping[str, Any] | None = None) -> RenderedPrompt:
        prompt_ref = ref if isinstance(ref, PromptRef) else PromptRef(str(ref), variables=variables or {})
        with self._lock:
            definition = self._definitions.get(prompt_ref.prompt_id)
        if definition is None:
            raise UnknownPromptError(f"unknown prompt: {prompt_ref.prompt_id}")
        if prompt_ref.version is not None and prompt_ref.version != definition.info.version:
            raise PromptVersionError(
                f"prompt version mismatch for {prompt_ref.prompt_id}: "
                f"expected {prompt_ref.version}, registered {definition.info.version}"
            )
        missing = set(definition.info.required_variables) - set(prompt_ref.variables)
        if missing:
            raise PromptVariableError(
                f"missing prompt variables for {prompt_ref.prompt_id}: {', '.join(sorted(missing))}"
            )
        unexpected = set(prompt_ref.variables) - set(definition.info.required_variables)
        if unexpected:
            raise PromptVariableError(
                f"unexpected prompt variables for {prompt_ref.prompt_id}: "
                f"{', '.join(sorted(unexpected))}"
            )

        cache_policy = _restricted_cache_policy(definition.info.cache_policy, prompt_ref.cache_policy)
        if prompt_ref.output_contract is not None and prompt_ref.output_contract != definition.info.output_contract:
            raise PromptSchemaError("PromptRef output contract does not match the registered contract")

        if definition.message_templates is not None:
            rendered_value = _format_value(definition.message_templates, prompt_ref.variables)
            messages = _normalize_messages(rendered_value, context=f"rendered prompt {prompt_ref.prompt_id}")
        else:
            assert definition.renderer is not None
            try:
                rendered_value = definition.renderer(dict(prompt_ref.variables))
            except PromptRegistryError:
                raise
            except Exception as exc:
                raise PromptSchemaError(
                    f"renderer failed for {prompt_ref.prompt_id}: {type(exc).__name__}: {exc}"
                ) from exc
            if isinstance(rendered_value, RenderedPrompt):
                rendered = self.validate_rendered(rendered_value)
                if rendered.prompt_id != prompt_ref.prompt_id:
                    raise PromptSchemaError("dynamic renderer returned a different prompt_id")
                if rendered.version != definition.info.version:
                    raise PromptSchemaError("dynamic renderer returned a different prompt version")
                if rendered.output_contract != definition.info.output_contract:
                    raise PromptSchemaError("dynamic renderer returned a different output contract")
                if rendered.cache_policy != cache_policy:
                    raise PromptSchemaError("dynamic renderer returned a different cache policy")
                if prompt_ref.digest is not None and rendered.digest != prompt_ref.digest:
                    raise PromptDigestError(f"prompt digest mismatch for {prompt_ref.prompt_id}")
                return rendered
            messages = _message_templates_from_static(rendered_value)

        digest = rendered_prompt_digest(
            prompt_id=definition.info.prompt_id,
            version=definition.info.version,
            messages=messages,
            output_contract=definition.info.output_contract,
            cache_policy=cache_policy,
        )
        if prompt_ref.digest is not None and prompt_ref.digest != digest:
            raise PromptDigestError(f"prompt digest mismatch for {prompt_ref.prompt_id}")
        rendered = RenderedPrompt(
            prompt_id=definition.info.prompt_id,
            version=definition.info.version,
            digest=digest,
            messages=messages,
            output_contract=definition.info.output_contract,
            cache_policy=cache_policy,
            metadata={"registry_kind": definition.info.kind, **dict(definition.info.metadata)},
        )
        return self.validate_rendered(rendered)

    def validate_rendered(self, prompt: RenderedPrompt) -> RenderedPrompt:
        # A syntactically valid ``RenderedPrompt`` is not sufficient authority
        # to enter the model plane.  Callers may derive repair messages from a
        # registered prompt, but they may not mint a new prompt id/version or
        # replace its output contract/cache policy outside this registry.
        with self._lock:
            definition = self._definitions.get(prompt.prompt_id)
        if definition is None:
            raise UnknownPromptError(f"unknown prompt: {prompt.prompt_id}")
        if prompt.version != definition.info.version:
            raise PromptVersionError(
                f"prompt version mismatch for {prompt.prompt_id}: "
                f"rendered {prompt.version}, registered {definition.info.version}"
            )
        if prompt.output_contract != definition.info.output_contract:
            raise PromptSchemaError(
                "rendered prompt output contract does not match the registered contract"
            )
        _restricted_cache_policy(definition.info.cache_policy, prompt.cache_policy)
        messages = _normalize_messages(prompt.messages, context=f"rendered prompt {prompt.prompt_id}")
        expected_digest = rendered_prompt_digest(
            prompt_id=prompt.prompt_id,
            version=prompt.version,
            messages=messages,
            output_contract=prompt.output_contract,
            cache_policy=prompt.cache_policy,
        )
        if prompt.digest != expected_digest:
            raise PromptDigestError(f"rendered prompt digest mismatch for {prompt.prompt_id}")
        return prompt

    def definition(self, prompt_id: str) -> PromptDefinitionInfo:
        normalized_id = str(prompt_id or "").strip()
        with self._lock:
            definition = self._definitions.get(normalized_id)
        if definition is None:
            raise UnknownPromptError(f"unknown prompt: {normalized_id}")
        return definition.info

    def list_definitions(self) -> tuple[PromptDefinitionInfo, ...]:
        with self._lock:
            values = [definition.info for definition in self._definitions.values()]
        return tuple(sorted(values, key=lambda item: item.prompt_id))

    def __contains__(self, prompt_id: object) -> bool:
        if not isinstance(prompt_id, str):
            return False
        with self._lock:
            return prompt_id.strip() in self._definitions


__all__ = [
    "DuplicatePromptError",
    "PromptDefinitionInfo",
    "PromptDigestError",
    "PromptRegistry",
    "PromptRegistryError",
    "PromptRenderer",
    "PromptSchemaError",
    "PromptVariableError",
    "PromptVersionError",
    "UnknownPromptError",
]
