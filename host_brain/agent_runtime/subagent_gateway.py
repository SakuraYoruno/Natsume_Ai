"""Canonical runtime responsibility extracted from the former app hotspot."""

from __future__ import annotations

from typing import Any, Callable, Sequence
from collections.abc import Mapping
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
    ) -> None:
        # Provider/model arguments remain accepted as a compatibility shape for
        # the existing sub-agent composition call.  They are intentionally not
        # model authority: ModelAttemptService resolves the server-owned route.
        self.provider_name = str(provider_name or "").strip().lower()
        self._model_id = str(model_id or "").strip() or "default"
        self._timeout_ms = max(1_000, int(timeout_ms or 30_000))
        self._application_ports = application_ports
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
        registry = ports.prompt_registry()
        prompt_ref = build_agent_model_prompt_ref(
            registry,
            prompt_id=SUBAGENT_GATEWAY_PROMPT_ID,
            messages=messages,
        )
        result = ports.model_attempt_service().complete_sync(
            ModelAttemptRequest(
                request_id=f"subagent-{uuid.uuid4().hex}",
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
            raise RuntimeError(
                f"sub-agent model attempt failed: {result.error_code or 'MODEL_ATTEMPT_FAILED'}"
            )
        return {
            "content": str(result.content or "").strip(),
            "model_attempt": trace,
            "provider": trace["final_provider"],
            "model": trace["final_model"],
        }


def _build_subagent_gateway_from_runtime(
    llm_runtime: Mapping[str, Any],
    *,
    application_ports: AgentRuntimeApplicationPorts,
    adapter_class: type[Any] | None = None,
) -> Any | None:
    try:
        timeout_ms = int(llm_runtime.get("timeout_ms") or 30_000)
        _adapter_class = adapter_class or _SubAgentGatewayAdapter
        return _adapter_class(
            provider_name="model_attempt_service",
            model_id="server_owned_route",
            api_key="",
            timeout_ms=timeout_ms,
            base_url_override=None,
            application_ports=application_ports,
        )
    except Exception as exc:
        _logger.debug("sub-agent gateway adapter unavailable: %s", exc)
        return None
