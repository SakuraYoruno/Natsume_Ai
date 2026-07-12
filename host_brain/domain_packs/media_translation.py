"""Media/Translation pack using only platform-injected task execution."""

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


MEDIA_TRANSLATION_PACK_ID = "media-translation"


@dataclass(frozen=True, slots=True)
class MediaTaskHandler:
    task_id: str

    def execute(self, request: DomainPackRequest, ports: DomainPackPlatformPorts):  # noqa: ANN201
        task_port = ports.require("task_port")
        return task_port.execute(self.task_id, {
            **dict(request.payload),
            "domain_capability_id": request.capability_id,
        })


_CAPABILITY_TASKS = {
    "agent_lightnovel_recommender": "media.lightnovel_recommender.worker",
    "agent_epub_builder": "media.epub_builder.worker",
}

_CAPABILITY_STAGES = {
    "agent_lightnovel_recommender": (
        DomainLifecycleStageManifest(
            stage_id="media.lightnovel-recommender.postprocess",
            phase="postprocess",
            task_id="media.lightnovel_recommender.postprocess",
        ),
        DomainLifecycleStageManifest(
            stage_id="media.lightnovel-recommender.recover",
            phase="recovery",
            task_id="media.lightnovel_recommender.recover",
        ),
        DomainLifecycleStageManifest(
            stage_id="media.lightnovel-recommender.gateway-delivery",
            phase="delivery",
            task_id="media.lightnovel_recommender.gateway-delivery",
            required=False,
            user_visible=True,
        ),
    ),
    "agent_epub_builder": (
        DomainLifecycleStageManifest(
            stage_id="media.epub-builder.gateway-delivery",
            phase="delivery",
            task_id="media.epub_builder.gateway-delivery",
            required=False,
            user_visible=True,
        ),
    ),
}


def build_media_translation_pack(*, version: str = "1.0.0") -> DomainPack:
    capabilities = tuple(
        DomainCapabilityManifest(
            capability_id=capability_id,
            handler_id=f"media.{capability_id}",
            description="Governed media production capability.",
            risk_level=4 if capability_id == "agent_epub_builder" else 2,
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
            pack_id=MEDIA_TRANSLATION_PACK_ID,
            version=version,
            domain="media-translation",
            capabilities=capabilities,
            required_ports=("capability_broker", "task_port"),
        ),
        handlers={
            item.handler_id: MediaTaskHandler(_CAPABILITY_TASKS[item.capability_id])
            for item in capabilities
        },
    )


__all__ = ["MEDIA_TRANSLATION_PACK_ID", "MediaTaskHandler", "build_media_translation_pack"]
