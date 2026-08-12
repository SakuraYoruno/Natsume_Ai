"""面向模型提示词的严格注册表。

未知 ID、缺失变量与错误 renderer 输出都是编程错误，绝不降级为空提示词。注册表同时是
提示词版本、输出契约、缓存策略和渲染摘要的唯一校验点。
"""

from __future__ import annotations

from copy import deepcopy
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
    compilation_source: Any | None = None


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
    # 静态 PromptSpec 可采用几种声明形状，但都在注册期收敛成受限消息序列；业务模块
    # 无法借对象类型或自定义 role 向模型平面注入未定义结构。
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
    # role/content 在渲染前后重复验证，确保动态 renderer 与静态模板面对同一消息契约；
    # 空消息或未知角色不降级为可发送文本。
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
    # 预先发现模板变量，使调用时能拒绝缺失/额外字段；禁止位置占位符，避免变量顺序
    # 改变时把不相关数据填入提示词。
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
    # 递归格式化只作用于已声明快照；StrictVariables 将未知占位符转为明确错误，
    # 而不是生成带残留花括号的提示词继续调用模型。
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
    # PromptRef 只能进一步收紧缓存作用域/TTL，不能把本不允许共享的提示词扩大为全局
    # 缓存，从而保护用户上下文和受治理请求的隔离。
    if requested is None:
        return registered
    if _cache_rank(requested) > _cache_rank(registered):
        raise PromptSchemaError("PromptRef cache policy cannot broaden the registered policy")
    if requested.scope is registered.scope and requested.ttl_ms > registered.ttl_ms:
        raise PromptSchemaError("PromptRef cache ttl cannot exceed the registered ttl")
    return requested


class PromptRegistry:
    """静态 PromptSpec 与动态 renderer 的线程安全注册表。

    renderer 可生成消息，但不能改写注册身份、版本、输出契约或缓存上限；这些不变量使
    同一请求可在重试/恢复时证明使用了何种模型输入。
    """

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
        compilation_source: Any | None = None,
        replace_existing: bool = False,
    ) -> PromptDefinitionInfo:
        """注册静态消息模板，并从占位符自动收集必需变量。"""
        # 注册时固定模板、自动发现变量和输出/缓存契约；后续 render 只引用该版本身份，
        # 不能由调用方临时替换结构化输出规则。
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
            compilation_source=compilation_source,
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
        compilation_source: Any | None = None,
        replace_existing: bool = False,
    ) -> PromptDefinitionInfo:
        """注册动态 renderer；其运行产物仍必须回到本注册表进行身份校验。"""
        # renderer 仅能生成内容快照，注册表仍拥有 ID、版本、契约与缓存策略的权威；
        # 因而动态提示词不会成为绕过登记的第二条入口。
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
            compilation_source=compilation_source,
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
        compilation_source: Any | None,
        replace_existing: bool,
    ) -> PromptDefinitionInfo:
        """原子写入定义；除非明确迁移，否则拒绝同 ID 覆盖。"""
        # 定义在锁内原子替换；同 ID 默认拒绝覆盖，迁移必须显式声明 replace_existing，
        # 以免运行中重放/审计引用的 prompt 身份悄然漂移。
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
            compilation_source=deepcopy(compilation_source),
        )
        with self._lock:
            if normalized_id in self._definitions and not replace_existing:
                raise DuplicatePromptError(f"prompt already registered: {normalized_id}")
            self._definitions[normalized_id] = definition
        return info

    def render(self, ref: PromptRef | str, variables: Mapping[str, Any] | None = None) -> RenderedPrompt:
        """渲染注册定义并验证变量、契约、缓存限制和最终摘要。

        调用方的 PromptRef 只能断言已注册版本或进一步收紧缓存，不能注入额外变量或
        改变结构化输出要求；摘要失配立即拒绝以保护可重放身份。
        """
        # PromptRef 只引用已登记 ID，并携带受检查的变量/版本/摘要断言；渲染从注册表
        # 读取定义，而非把调用方提供的模板直接跨模型边界。
        prompt_ref = ref if isinstance(ref, PromptRef) else PromptRef(str(ref), variables=variables or {})
        with self._lock:
            definition = self._definitions.get(prompt_ref.prompt_id)
        if definition is None:
            # 未登记提示词 fail-closed，防止拼写错误或任意 ID 退化为默认模板后丢失审计
            # 与输出契约。
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

        # 变量集合必须精确匹配，既避免模板渲染失败，也阻止额外业务数据无意进入 prompt；
        # cache/output 断言随后只能验证或收紧注册值。
        cache_policy = _restricted_cache_policy(definition.info.cache_policy, prompt_ref.cache_policy)
        if prompt_ref.output_contract is not None and prompt_ref.output_contract != definition.info.output_contract:
            raise PromptSchemaError("PromptRef output contract does not match the registered contract")

        if definition.message_templates is not None:
            rendered_value = _format_value(definition.message_templates, prompt_ref.variables)
            messages = _normalize_messages(rendered_value, context=f"rendered prompt {prompt_ref.prompt_id}")
        else:
            # 动态 renderer 接收变量副本；若直接返回 RenderedPrompt，仍需回到 validate
            # 路径核对 ID、版本、契约、缓存与摘要，不能自我认证。
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

        # 摘要覆盖渲染后的消息与不可变契约；请求声明的 digest 不匹配即停止，使重试、
        # 缓存和审计可证明实际发送的是同一提示词快照。
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
        """确认渲染快照仍属于已注册定义，才允许其进入模型平面。"""
        # 语法有效的 ``RenderedPrompt`` 不足以成为模型平面权威：调用方可从注册提示词
        # 派生修复消息，但不得在注册表外铸造 ID/版本或替换输出契约与缓存策略。
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

    def compilation_source(self, prompt_id: str) -> Any:
        """返回注册提示词编译源的不可变快照。

        生产 PromptSpec 携带其精确代码侧来源，领域编译器无需维护第二套模板存储；未知
        ID 或没有编译源的定义均是编程错误。
        """

        # 返回深拷贝而非注册表内部对象，领域编译器可以阅读代码侧来源，但不能修改
        # 后续模型渲染所依赖的登记定义。
        normalized_id = str(prompt_id or "").strip()
        with self._lock:
            definition = self._definitions.get(normalized_id)
        if definition is None:
            raise UnknownPromptError(f"unknown prompt: {normalized_id}")
        if definition.compilation_source is None:
            raise PromptSchemaError(
                f"prompt has no registered compilation source: {normalized_id}"
            )
        return deepcopy(definition.compilation_source)

    def list_definitions(self) -> tuple[PromptDefinitionInfo, ...]:
        # 稳定排序的目录适用于启动投影和配置比较；不暴露 renderer 或可变模板内部状态。
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
