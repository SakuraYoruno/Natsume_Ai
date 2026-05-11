"""Surface-chain recall exports.

The old Agentic RAG pipeline/orchestrator stack has been removed from this
package. Current text generation uses RecallFusionServiceContract through
host_brain.persona.surface_memory_recall and passes only compressed facts into
ReplyControlPacket.
"""

from host_brain.retrieval.recall_fusion import (
    RET_OP_RECALL,
    S_RET_RECALL,
    S_RET_RECALL_CONFIG,
    RecallConfigIssue,
    RecallConfigLoadResult,
    RecallFusionResult,
    RecallFusionServiceContract,
    RecallRouteConfig,
    RecallRuntimeConfig,
    load_recall_runtime_config,
    resolve_recall_routes,
)

__all__ = [
    "RET_OP_RECALL",
    "S_RET_RECALL",
    "S_RET_RECALL_CONFIG",
    "RecallConfigIssue",
    "RecallConfigLoadResult",
    "RecallFusionResult",
    "RecallFusionServiceContract",
    "RecallRouteConfig",
    "RecallRuntimeConfig",
    "load_recall_runtime_config",
    "resolve_recall_routes",
]
