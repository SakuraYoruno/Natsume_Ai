"""Canonical runtime responsibility extracted from the former app hotspot."""

from __future__ import annotations

from typing import Any, Callable, Sequence
from collections.abc import Mapping
from threading import Lock
import uuid

from host_brain.agent_runtime.runtime_texts import (
    _logger,
)
from host_brain.agent_runtime.application_ports import AgentRuntimeApplicationPorts


class _SubAgentGatewayAdapter:
    def __init__(
        self,
        *,
        provider_name: str,
        model_id: str,
        api_key: str,
        timeout_ms: int,
        base_url_override: str | None,
        application_ports: AgentRuntimeApplicationPorts,
        request_id: str | None = None,
    ) -> None:
        # Provider/model arguments remain accepted as a compatibility shape for
        # the existing sub-agent composition call.  They are intentionally not
        # model authority: ModelAttemptService resolves the server-owned route.
        self.provider_name = str(provider_name or "").strip().lower()
        self._model_id = str(model_id or "").strip() or "default"
        self._timeout_ms = max(1_000, int(timeout_ms or 30_000))
        self._application_ports = application_ports
        self._request_id = str(request_id or "").strip()
        self._request_index = 0
        self._request_lock = Lock()
        # Never retain secrets or provider endpoints on the compatibility port.
        del api_key, base_url_override

    def complete(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        from host_brain.agent_runtime.agent_prompt import (  # noqa: PLC0415
            SUBAGENT_GATEWAY_PROMPT_ID,
            build_agent_model_prompt_ref,
        )
        from host_brain.model_runtime import (  # noqa: PLC0415
            ModelAttemptRequest,
            model_attempt_result_trace,
        )

        ports = self._application_ports
        # 消息先编译为受注册表约束的 prompt 引用；网关不直接把调用方提供的文本
        # 发送给某个指定供应商，从而让审计、模板与模型选择保持在运行时边界内。
        registry = ports.prompt_registry()
        prompt_ref = build_agent_model_prompt_ref(
            registry,
            prompt_id=SUBAGENT_GATEWAY_PROMPT_ID,
            messages=messages,
        )
        result = ports.model_attempt_service().complete_sync(
            ModelAttemptRequest(
                request_id=self._next_request_id(),
                scene_type="SUBAGENT",
                cognitive_tier="B",
                prompt=prompt_ref,
                deadline_ms=self._timeout_ms,
                foreground=False,
                metadata={"caller": "subagent_gateway"},
            )
        )
        trace = model_attempt_result_trace(result)
        if not result.success:
            # 网关只暴露一次受治理尝试失败，不在这里切换 provider 或自行重试；
            # 重试与降级属于 ModelAttemptService，防止嵌套调用绕开预算和路由策略。
            raise RuntimeError(
                f"sub-agent model attempt failed: {result.error_code or 'MODEL_ATTEMPT_FAILED'}"
            )
        return {
            "content": str(result.content or "").strip(),
            "model_attempt": trace,
            "provider": trace["final_provider"],
            "model": trace["final_model"],
        }

    def _next_request_id(self) -> str:
        if not self._request_id:
            # 没有父请求时使用独立随机 ID，避免不同无上下文调用错误共享幂等域。
            return f"subagent-{uuid.uuid4().hex}"
        with self._request_lock:
            # 同一父请求可并发发起子尝试；单调序号在进程内保证每次模型调用拥有
            # 不同 request_id，避免服务端把两个 prompt 误判为同一重放。
            self._request_index += 1
            index = self._request_index
        return f"{self._request_id}:subagent-model:{index}"


def _build_subagent_gateway_from_runtime(
    llm_runtime: Mapping[str, Any],
    *,
    application_ports: AgentRuntimeApplicationPorts,
    adapter_class: type[Any] | None = None,
    request_id: str | None = None,
) -> Any | None:
    try:
        # 组成层只从运行时读取超时上限；provider、模型、密钥与端点全部固定为
        # 占位兼容值，真正的服务器端路由由 ModelAttemptService 决定。
        timeout_ms = int(llm_runtime.get("timeout_ms") or 30_000)
        _adapter_class = adapter_class or _SubAgentGatewayAdapter
        return _adapter_class(
            provider_name="model_attempt_service",
            model_id="server_owned_route",
            api_key="",
            timeout_ms=timeout_ms,
            base_url_override=None,
            application_ports=application_ports,
            request_id=request_id,
        )
    except Exception as exc:
        # 构造失败只让可选子代理能力不可用，不制造伪适配器或回退到直连模型；
        # 调用链可据 None 选择受控的降级行为。
        _logger.debug("sub-agent gateway adapter unavailable: %s", exc)
        return None
