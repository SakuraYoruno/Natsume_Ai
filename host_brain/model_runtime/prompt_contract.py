"""严格读取不可变的代码侧提示词契约。

提示词编译源属于模型领域数据。读取它们不能触发应用组合、账本构造、provider 绑定或
全局注册表查找；运行时渲染仍归 ``PromptRegistry`` 所有，这些辅助函数只暴露已声明
编译源的防御性副本。
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
    """返回 ``prompt_id`` 已登记的代码侧结构化编译源。"""

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
    """返回通过严格类型校验的已登记文本编译源。"""

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
