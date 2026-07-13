"""Code-side production prompt catalog bootstrap.

The catalog is deliberately assembled only from :mod:`prompt_specs`.  It does
not inspect files, environment variables, databases, or runtime overrides.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from string import Formatter
from typing import Any, Mapping, Sequence

from host_brain.model_runtime.prompt_specs import PromptSpec, list_prompt_specs
from host_brain.model_runtime.contracts import OutputContract, sha256_digest
from host_brain.model_runtime.prompt_registry import (
    PromptRegistry,
    PromptSchemaError,
    PromptVariableError,
    UnknownPromptError,
)


_OPERATIONAL_VARIABLES = ("entry", "variables")


@dataclass(frozen=True, slots=True)
class PromptCatalogEntry:
    prompt_id: str
    version: str
    definition_digest: str
    render_mode: str
    required_variables: tuple[str, ...]
    output_kind: str
    purpose: str
    category: str
    source_path: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "prompt_id": self.prompt_id,
            "version": self.version,
            "definition_digest": self.definition_digest,
            "render_mode": self.render_mode,
            "required_variables": list(self.required_variables),
            "output_kind": self.output_kind,
            "purpose": self.purpose,
            "category": self.category,
            "source_path": self.source_path,
        }


@dataclass(frozen=True, slots=True)
class _BoundSpec:
    spec: PromptSpec
    content: Any
    inventory: PromptCatalogEntry


def _is_direct(content: Any) -> bool:
    if isinstance(content, str):
        return True
    if not isinstance(content, Mapping):
        return False
    return bool(
        "messages" in content
        or ("system_prompt" in content and ("user_prompt_template" in content or "user_prompt" in content))
        or "template" in content
        or ("role" in content and "content" in content)
    )


def _fields(value: Any) -> set[str]:
    fields: set[str] = set()
    if isinstance(value, str):
        try:
            for _literal, field_name, _format_spec, _conversion in Formatter().parse(value):
                if field_name is not None:
                    root = field_name.split(".", 1)[0].split("[", 1)[0].strip()
                    if not root:
                        raise PromptSchemaError("positional prompt placeholders are not supported")
                    fields.add(root)
        except ValueError as exc:
            raise PromptSchemaError(f"invalid catalog format string: {exc}") from exc
    elif isinstance(value, Mapping):
        for item in value.values():
            fields.update(_fields(item))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            fields.update(_fields(item))
    return fields


def _definition_digest(spec: PromptSpec, content: Any) -> str:
    return sha256_digest(
        {
            "prompt_id": spec.spec_id,
            "version": spec.version,
            "purpose": spec.purpose,
            "output_kind": spec.output_type,
            "category": spec.category,
            "scope": spec.scope,
            "owner": spec.owner,
            "source_path": spec.source_path,
            "content": content,
        }
    )


def _bound_specs() -> tuple[_BoundSpec, ...]:
    bound: list[_BoundSpec] = []
    for spec in list_prompt_specs():
        content = deepcopy(spec.content)
        direct = _is_direct(content)
        required = tuple(sorted(_fields(content))) if direct and not isinstance(content, str) else ()
        bound.append(
            _BoundSpec(
                spec=spec,
                content=content,
                inventory=PromptCatalogEntry(
                    prompt_id=spec.spec_id,
                    version=spec.version,
                    definition_digest=_definition_digest(spec, content),
                    render_mode="static" if direct else "operational_renderer",
                    required_variables=required if direct else _OPERATIONAL_VARIABLES,
                    output_kind=spec.output_type,
                    purpose=spec.purpose,
                    category=spec.category,
                    source_path=spec.source_path,
                ),
            )
        )
    return tuple(sorted(bound, key=lambda item: item.spec.spec_id))


class _StrictValues(dict[str, Any]):
    def __missing__(self, key: str) -> Any:
        raise PromptVariableError(f"missing prompt variable: {key}")


def _format(value: Any, variables: Mapping[str, Any]) -> Any:
    if isinstance(value, str):
        try:
            return value.format_map(_StrictValues(variables))
        except PromptVariableError:
            raise
        except (KeyError, IndexError, AttributeError, ValueError) as exc:
            raise PromptVariableError(f"failed to format catalog template: {exc}") from exc
    if isinstance(value, Mapping):
        return {key: _format(item, variables) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_format(item, variables) for item in value)
    if isinstance(value, list):
        return [_format(item, variables) for item in value]
    return value


def _exact_variables(expected: set[str], raw: Any, *, prompt_id: str) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping) or any(not isinstance(key, str) for key in raw):
        raise PromptVariableError(f"{prompt_id} variables must be a string-keyed mapping")
    actual = set(raw)
    missing = expected - actual
    unexpected = actual - expected
    if missing:
        raise PromptVariableError(f"missing prompt variables for {prompt_id}: {', '.join(sorted(missing))}")
    if unexpected:
        raise PromptVariableError(
            f"unexpected prompt variables for {prompt_id}: {', '.join(sorted(unexpected))}"
        )
    return raw


class ProductionPromptCatalogAdapter:
    """Strict adapter for structured operational/template bundles."""

    def __init__(self) -> None:
        self._specs = {item.spec.spec_id: item for item in _bound_specs()}

    def inventory(self) -> tuple[PromptCatalogEntry, ...]:
        return tuple(item.inventory for item in self._specs.values())

    def render(self, prompt_id: str, *, entry: str, variables: Mapping[str, Any]) -> Any:
        try:
            bound = self._specs[str(prompt_id or "").strip()]
        except KeyError as exc:
            raise UnknownPromptError(f"unknown prompt: {prompt_id}") from exc
        value: Any = bound.content
        path = str(entry or "").strip()
        if not path or any(part in {"", ".", ".."} for part in path.split("/")):
            raise PromptSchemaError("operational prompt entry must be a safe non-empty path")
        for part in path.split("/"):
            if not isinstance(value, Mapping) or part not in value:
                raise PromptSchemaError(f"unknown operational prompt entry: {prompt_id}/{path}")
            value = value[part]
        expected = _fields(value)
        resolved = _exact_variables(expected, variables, prompt_id=f"{prompt_id}/{path}")
        rendered = _format(value, resolved)
        if isinstance(rendered, str):
            role = "system" if "system" in path.rsplit("/", 1)[-1] else "user"
            return ({"role": role, "content": rendered},)
        if isinstance(rendered, Mapping) and _is_direct(rendered):
            return rendered
        if isinstance(rendered, Sequence) and not isinstance(rendered, (str, bytes, bytearray)):
            return rendered
        raise PromptSchemaError(f"operational prompt entry is not renderable: {prompt_id}/{path}")


def production_prompt_catalog_inventory() -> tuple[PromptCatalogEntry, ...]:
    return tuple(item.inventory for item in _bound_specs())


def production_prompt_catalog_digest() -> str:
    return sha256_digest([item.as_dict() for item in production_prompt_catalog_inventory()])


def build_production_prompt_registry() -> PromptRegistry:
    """Build a fresh, deterministic registry from the code-side catalog."""

    registry = PromptRegistry()
    adapter = ProductionPromptCatalogAdapter()
    for bound in _bound_specs():
        metadata = {
            "definition_digest": bound.inventory.definition_digest,
            "purpose": bound.spec.purpose,
            "category": bound.spec.category,
            "source_path": bound.spec.source_path,
        }
        output_contract = OutputContract(kind=bound.spec.output_type)
        if bound.inventory.render_mode == "static":
            content = bound.content
            # Text PromptSpecs are literal fragments; braces are not variables.
            if isinstance(content, str):
                content = {"role": "system", "content": content.replace("{", "{{").replace("}", "}}")}
            registry.register_static(
                bound.spec.spec_id,
                content,
                version=bound.spec.version,
                output_contract=output_contract,
                metadata=metadata,
                compilation_source=bound.content,
            )
            continue

        prompt_id = bound.spec.spec_id

        def _render(values: Mapping[str, Any], *, _prompt_id: str = prompt_id) -> Any:
            return adapter.render(
                _prompt_id,
                entry=str(values["entry"]),
                variables=values["variables"],
            )

        registry.register_renderer(
            prompt_id,
            _render,
            version=bound.spec.version,
            required_variables=_OPERATIONAL_VARIABLES,
            output_contract=output_contract,
            metadata=metadata,
            compilation_source=bound.content,
        )
    return registry


__all__ = [
    "ProductionPromptCatalogAdapter",
    "PromptCatalogEntry",
    "build_production_prompt_registry",
    "production_prompt_catalog_digest",
    "production_prompt_catalog_inventory",
]
