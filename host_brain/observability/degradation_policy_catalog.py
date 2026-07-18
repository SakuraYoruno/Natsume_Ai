"""Immutable dependency fault-policy catalog for degradation governance."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Any


_FAILURE_STATUSES = frozenset(
    {"DOWN", "DEGRADED", "FAILED", "ERROR", "UNAVAILABLE", "UNHEALTHY"}
)
_HEALTHY_STATUSES = frozenset({"UP", "OK", "READY", "HEALTHY", "ACCEPTED"})
_IGNORED_STATUSES = frozenset({"", "UNKNOWN", "SKIPPED", "DEFERRED", "DISABLED"})
_NON_HEALTHY_SUCCESS_MARKERS = (
    "disabled by config",
    "=false",
    "skipped",
    "lazy init",
    "no dedicated",
    "gateway-only",
)

@dataclass(frozen=True, slots=True)
class FaultPolicy:
    dependency_id: str
    service_ids: tuple[str, ...]
    affected_capabilities: tuple[str, ...]
    high_risk_capabilities: tuple[str, ...]
    safe_fallbacks: tuple[str, ...]
    failure_statuses: tuple[str, ...] = tuple(sorted(_FAILURE_STATUSES))
    healthy_statuses: tuple[str, ...] = tuple(sorted(_HEALTHY_STATUSES))

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_FAULT_POLICIES: dict[str, FaultPolicy] = {
    "MODEL": FaultPolicy(
        dependency_id="MODEL",
        service_ids=(
            "model",
            "model_provider",
            "llm_gateway",
            "host.integrations.llm_gateway",
            "host.governor.decision",
        ),
        affected_capabilities=(
            "model_inference",
            "autonomous_planning",
            "tool_planning",
            "capability_upgrade",
            "high_risk_execution",
        ),
        high_risk_capabilities=(
            "autonomous_planning",
            "tool_planning",
            "capability_upgrade",
            "high_risk_execution",
        ),
        safe_fallbacks=("deterministic_rule", "human_review"),
    ),
    "MEMORY": FaultPolicy(
        dependency_id="MEMORY",
        service_ids=(
            "memory",
            "chroma",
            "memory_guard",
            "recall_fusion",
            "host.retrieval.recall_fusion",
        ),
        affected_capabilities=(
            "memory_recall",
            "memory_write",
            "canon_apply",
            "domain_knowledge_write",
        ),
        high_risk_capabilities=(
            "memory_write",
            "canon_apply",
            "domain_knowledge_write",
        ),
        safe_fallbacks=("context_only", "read_only", "human_review"),
    ),
    "VTS": FaultPolicy(
        dependency_id="VTS",
        service_ids=("vts", "edge.rpc.vts", "live2d", "vts_live2d"),
        affected_capabilities=("avatar_expression", "avatar_control"),
        high_risk_capabilities=("avatar_control",),
        safe_fallbacks=("text_only", "neutral_avatar"),
    ),
    "TTS": FaultPolicy(
        dependency_id="TTS",
        service_ids=("tts", "edge.rpc.tts", "voice_output"),
        affected_capabilities=("voice_render", "voice_output", "voice_broadcast"),
        high_risk_capabilities=("voice_broadcast",),
        safe_fallbacks=("text_only",),
    ),
    "SSE": FaultPolicy(
        dependency_id="SSE",
        service_ids=(
            "sse",
            "runtime_event_sse",
            "host.runtime_event.sse",
            "runtime_event_stream",
        ),
        affected_capabilities=("runtime_event_stream", "remote_review_resume"),
        high_risk_capabilities=("remote_review_resume",),
        safe_fallbacks=("cursor_polling", "local_review"),
    ),
    "DOMAIN": FaultPolicy(
        dependency_id="DOMAIN",
        service_ids=(
            "domain",
            "domain_store",
            "pentest_domain_store",
            "host.pentest.domain_store",
        ),
        affected_capabilities=(
            "domain_read",
            "domain_write",
            "pentest_execution",
            "canon_apply",
        ),
        high_risk_capabilities=(
            "domain_write",
            "pentest_execution",
            "canon_apply",
        ),
        safe_fallbacks=("domain_read_only", "lab_disabled", "human_review"),
    ),
}
FAULT_POLICIES: Mapping[str, FaultPolicy] = MappingProxyType(_FAULT_POLICIES)

_SERVICE_TO_DEPENDENCY = {
    service_id.lower(): dependency_id
    for dependency_id, policy in FAULT_POLICIES.items()
    for service_id in policy.service_ids
}

_STARTUP_MODULE_TO_DEPENDENCY = {
    "chat_llm_runtime": "MODEL",
    "chat_llm_runtime_warmup": "MODEL",
    "codex_exec_intent": "MODEL",
    "codex_exec_intent_warmup": "MODEL",
    "retrieval_runtime": "MEMORY",
    "retrieval_runtime_warmup": "MEMORY",
    "semantic_recaller": "MEMORY",
    "memory_guard": "MEMORY",
    "vts": "VTS",
    "tts": "TTS",
    "runtime_event_sse": "SSE",
    "pentest_domain_store": "DOMAIN",
}
