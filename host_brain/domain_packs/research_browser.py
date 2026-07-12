"""Research/Browser pack: planning stays declarative; work runs via TaskPort."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import (
    DomainCapabilityManifest,
    DomainLifecycleStageManifest,
    DomainPack,
    DomainPackManifest,
    DomainPackRequest,
)
from .ports import DomainPackPlatformPorts


RESEARCH_BROWSER_PACK_ID = "research-browser"


@dataclass(frozen=True, slots=True)
class ResearchTaskHandler:
    task_id: str

    def execute(self, request: DomainPackRequest, ports: DomainPackPlatformPorts):  # noqa: ANN201
        task_port = ports.require("task_port")
        return task_port.execute(self.task_id, {
            **dict(request.payload),
            "domain_capability_id": request.capability_id,
        })


_CAPABILITY_TASKS = {
    "browser_research": "research.browser.worker",
    "agent_workbench_brief": "research.agent.worker",
    "agent_repo_sentinel": "research.agent.worker",
    "agent_memory_curator": "research.agent.worker",
    "agent_document_digest": "research.agent.worker",
    "agent_frontend_review": "research.agent.worker",
    "agent_frontend_acceptance": "research.agent.worker",
    "agent_web_corpus_collector": "research.agent.worker",
}

_CAPABILITY_STAGES = {
    "browser_research": (
        DomainLifecycleStageManifest(
            stage_id="research.browser.gateway-delivery",
            phase="delivery",
            task_id="research.browser.gateway-delivery",
            required=False,
            user_visible=True,
        ),
    ),
}


def build_research_browser_pack(*, version: str = "1.0.0") -> DomainPack:
    capabilities = tuple(
        DomainCapabilityManifest(
            capability_id=capability_id,
            handler_id=f"research.{capability_id}",
            description="Governed research/browser production capability.",
            risk_level=2,
            allowed_task_ids=(
                _CAPABILITY_TASKS[capability_id],
                *(stage.task_id for stage in _CAPABILITY_STAGES.get(capability_id, ())),
            ),
            lifecycle_stages=_CAPABILITY_STAGES.get(capability_id, ()),
        )
        for capability_id in _CAPABILITY_TASKS
    )
    return DomainPack(
        manifest=DomainPackManifest(
            pack_id=RESEARCH_BROWSER_PACK_ID,
            version=version,
            domain="research-browser",
            capabilities=capabilities,
            required_ports=("capability_broker", "task_port"),
        ),
        handlers={
            item.handler_id: ResearchTaskHandler(_CAPABILITY_TASKS[item.capability_id])
            for item in capabilities
        },
    )


__all__ = ["RESEARCH_BROWSER_PACK_ID", "ResearchTaskHandler", "build_research_browser_pack"]
