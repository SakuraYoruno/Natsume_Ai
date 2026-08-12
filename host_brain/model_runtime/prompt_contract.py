"""Strict access to immutable code-side prompt contracts.

Prompt compiler sources are model-domain data.  Reading them must not force
application composition, ledger construction, provider binding, or a global
registry lookup.  Runtime rendering still belongs to ``PromptRegistry``;
these helpers only expose defensive copies of declared compiler sources.
"""

from __future__ import annotations

from typing import Any

from host_brain.model_runtime.prompt_registry import PromptSchemaError
from host_brain.model_runtime.prompt_specs import get_prompt_spec, resolve_prompt_spec_json


def prompt_contract_exists(prompt_id: str) -> bool:
    # 这是纯代码侧存在性查询，不触发 registry、provider 或应用组合；编译阶段可用它
    # 安全地决定是否引用一个版本化 PromptSpec。
    try:
        get_prompt_spec(prompt_id)
    except KeyError:
        return False
    return True


def resolve_prompt_contract_json(prompt_id: str) -> Any:
    """Return the registered code-side compiler source for ``prompt_id``."""

    # 先核对声明输出类型，再返回 prompt_specs 提供的防御性副本；文本契约不能被
    # 调用方强制解释为 JSON 来改变领域编译器的输入语义。
    try:
        spec = get_prompt_spec(prompt_id)
    except KeyError as exc:
        raise PromptSchemaError(f"unknown prompt contract: {prompt_id}") from exc
    if spec.output_type == "text":
        raise PromptSchemaError(f"prompt contract is text, not JSON: {prompt_id}")
    value = resolve_prompt_spec_json(prompt_id)
    if not isinstance(value, (dict, list, tuple)):
        raise PromptSchemaError(f"prompt compilation source is not structured JSON: {prompt_id}")
    return value


def resolve_prompt_contract_text(prompt_id: str) -> str:
    """Return a registered text compiler source with strict type validation."""

    # 与 JSON 入口对称地拒绝结构化契约，保持“编译源类型”是登记定义的一部分，
    # 而不是消费方根据方便程度自行转换的选择。
    try:
        spec = get_prompt_spec(prompt_id)
    except KeyError as exc:
        raise PromptSchemaError(f"unknown prompt contract: {prompt_id}") from exc
    if spec.output_type != "text":
        raise PromptSchemaError(f"prompt contract is not text: {prompt_id}")
    value = resolve_prompt_spec_json(prompt_id)
    if not isinstance(value, str):
        raise PromptSchemaError(f"prompt compilation source is not text: {prompt_id}")
    return value


__all__ = [
    "prompt_contract_exists",
    "resolve_prompt_contract_json",
    "resolve_prompt_contract_text",
]
