"""obs_routes_stats_capability.py — 可观测性统计端点聚合入口（P2-9 拆分后）。

子模块已拆分为：
  - obs_routes_stats_capability_skill.py：Skill/工具/调度/规划类统计
  - obs_routes_stats_capability_agent.py：Agent能力/Forge/检索/免疫门类统计

本文件作为向后兼容的聚合入口，导入并重新导出子模块中的所有路由和模型。
"""
from __future__ import annotations

# 导入子模块以触发路由注册（副作用导入）
import host_brain.app.obs_routes_stats_capability_skill  # noqa: F401
import host_brain.app.obs_routes_stats_capability_agent  # noqa: F401

# 向后兼容：重新导出子模块中的公共符号
from host_brain.app.obs_routes_stats_capability_skill import (
    UIAssertionEventItem, UIAssertionStatsResponse, get_ui_assertion_stats,
    FailureEventItem, FailureStatsResponse, get_failure_stats,
    PermissionDeniedItem, PermissionStatsResponse, get_permission_stats,
    SchedulerTaskItem, SchedulerReasonItem, SchedulerGateItem, SchedulerHistoryResponse, get_scheduler_history,
    ActionTypeItem, ActionPlanStatsResponse, get_action_plan_stats,
    MemoryGuardRejectItem, MemoryGuardStatsResponse, get_memory_guard_stats,
    StateSourceItem, StateArbiterStatsResponse, get_state_arbiter_stats,
    ContextCompressorStatsResponse, get_context_compressor_stats,
    PromptBuilderStatsResponse, get_prompt_builder_stats,
    EpisodicStoreStatsResponse, get_episodic_store_stats,
    ChainNodeStatItem, ChainStatsResponse, get_chain_stats,
    AgentLoopHistoryItem, AgentLoopHistoryResponse, get_agent_loop_history,
    ErrorHeatmapCell, ErrorHeatmapResponse, get_error_heatmap,
)
from host_brain.app.obs_routes_stats_capability_agent import (
    SemanticRecallStatsResponse, get_semantic_recall_stats,
    ImmuneGateReasonItem, ImmuneGateRiskItem, ImmuneGateRecentItem,
    ImmuneGateReplayDecisionItem, ImmuneGateReplaySummary, ImmuneGateReplayResponse,
    ImmuneGateRuntimeState, ImmuneGateStatsResponse,
    get_immune_gate_stats, get_immune_gate_replay,
    RetrievalRuntimeRouteItem, RetrievalRuntimeConfiguredSection,
    RetrievalRuntimeObservedItem, RetrievalRuntimeResponse, get_retrieval_runtime,
    CapabilityGapStatItem, CapabilityGapHotspotItem, CapabilityGapCandidateThresholds,
    CapabilityGapCandidateExecutorItem, CapabilityGapForgeCandidateItem,
    CapabilityGapForgeRuntimeQualityItem, CapabilityGapForgeRuntimeQualitySnapshot,
    CapabilityGapStatsResponse, CapabilityGapForgeCandidatesResponse,
    CapabilityForgePreviewRequest, CapabilityForgeCreateRequest,
    CapabilityForgeApproveRequest, CapabilityForgeRollbackRequest,
    CapabilityForgeRuntimeMatchRequest, CapabilityForgePreviewResponse,
    CapabilityForgeProposalResponse, CapabilityForgeProposalListResponse,
    CapabilityForgeRuntimeMatchResponse, CapabilityForgeRuntimeStatsResponse,
    CapabilityForgeRuntimeTuningResponse, CapabilityForgeRuntimeQualityResponse,
    get_capability_gaps_stats, get_capability_gap_forge_candidates,
    preview_capability_forge_proposal, create_capability_forge_proposal,
    list_capability_forge_proposals, get_capability_forge_proposal,
    approve_capability_forge_proposal, rollback_capability_forge_proposal,
    capability_forge_runtime_match, capability_forge_runtime_stats,
    capability_forge_runtime_tuning_stats, capability_forge_runtime_quality_stats,
)

__all__ = [
    "UIAssertionEventItem", "UIAssertionStatsResponse", "get_ui_assertion_stats",
    "FailureEventItem", "FailureStatsResponse", "get_failure_stats",
    "PermissionDeniedItem", "PermissionStatsResponse", "get_permission_stats",
    "SchedulerTaskItem", "SchedulerReasonItem", "SchedulerGateItem", "SchedulerHistoryResponse", "get_scheduler_history",
    "ActionTypeItem", "ActionPlanStatsResponse", "get_action_plan_stats",
    "MemoryGuardRejectItem", "MemoryGuardStatsResponse", "get_memory_guard_stats",
    "StateSourceItem", "StateArbiterStatsResponse", "get_state_arbiter_stats",
    "ContextCompressorStatsResponse", "get_context_compressor_stats",
    "PromptBuilderStatsResponse", "get_prompt_builder_stats",
    "EpisodicStoreStatsResponse", "get_episodic_store_stats",
    "ChainNodeStatItem", "ChainStatsResponse", "get_chain_stats",
    "AgentLoopHistoryItem", "AgentLoopHistoryResponse", "get_agent_loop_history",
    "ErrorHeatmapCell", "ErrorHeatmapResponse", "get_error_heatmap",
    "SemanticRecallStatsResponse", "get_semantic_recall_stats",
    "ImmuneGateReasonItem", "ImmuneGateRiskItem", "ImmuneGateRecentItem",
    "ImmuneGateReplayDecisionItem", "ImmuneGateReplaySummary", "ImmuneGateReplayResponse",
    "ImmuneGateRuntimeState", "ImmuneGateStatsResponse",
    "get_immune_gate_stats", "get_immune_gate_replay",
    "RetrievalRuntimeRouteItem", "RetrievalRuntimeConfiguredSection",
    "RetrievalRuntimeObservedItem", "RetrievalRuntimeResponse", "get_retrieval_runtime",
    "CapabilityGapStatItem", "CapabilityGapHotspotItem", "CapabilityGapCandidateThresholds",
    "CapabilityGapCandidateExecutorItem", "CapabilityGapForgeCandidateItem",
    "CapabilityGapForgeRuntimeQualityItem", "CapabilityGapForgeRuntimeQualitySnapshot",
    "CapabilityGapStatsResponse", "CapabilityGapForgeCandidatesResponse",
    "get_capability_gaps_stats", "get_capability_gap_forge_candidates",
    "preview_capability_forge_proposal", "create_capability_forge_proposal",
    "list_capability_forge_proposals", "get_capability_forge_proposal",
    "approve_capability_forge_proposal", "rollback_capability_forge_proposal",
    "capability_forge_runtime_match", "capability_forge_runtime_stats",
    "capability_forge_runtime_tuning_stats", "capability_forge_runtime_quality_stats",
]
