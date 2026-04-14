"""Constants for SQLite ledger: column tuples, JSON field maps, and WAL config."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

INTENT_COLUMNS = (
    "request_id",
    "thread_id",
    "turn_id",
    "intent_id",
    "session_id",
    "seq",
    "ts_ms",
    "nonce",
    "parse_stage",
    "raw_text",
    "normalized_json",
    "proposer_node_id",
    "model_snapshot",
    "prompt_profile_id",
    "tool_visibility_hash",
    "proposed_intent_draft_json",
    "lint_result",
)

GOVERNOR_COLUMNS = (
    "request_id",
    "thread_id",
    "turn_id",
    "intent_id",
    "decision",
    "reason_code",
    "rule_trace",
    "tokens",
    "snapshot_refs",
    "stage",
    "ts_ms",
    # FIX-4.2：TRIBE 族参数快照（裁决时使用的 L3 动态参数，供审计追溯）
    "tribe_params_snapshot",
)

SKILL_EXEC_COLUMNS = (
    "request_id",
    "thread_id",
    "turn_id",
    "intent_id",
    "event_id",
    "skill_name",
    "approval_token_ref",
    "confirm_token_ref",
    "undo_token_ref",
    "result_status",
    "result_code",
    "result_digest",
    "evidence_ref",
    "manifest_ref",
    "ts_ms",
)

CANON_PROPOSAL_COLUMNS = (
    "proposal_id",
    "canon_kind",
    "target_id",
    "base_version",
    "proposed_version",
    "proposed_hash",
    "source_intent_id",
    "proposer_node_id",
    "summary",
    "status",
    "ts_ms",
)

CANON_CHANGE_COLUMNS = (
    "change_id",
    "proposal_id",
    "canon_kind",
    "target_id",
    "before_version",
    "after_version",
    "before_hash",
    "after_hash",
    "before_body",
    "after_body",
    "source_path",
    "ts_ms",
)

WORLD_CANON_DOC_COLUMNS = (
    "doc_id",
    "version",
    "hash",
    "status",
    "source_path",
    "updated_ts",
)

MEMORY_OP_COLUMNS = (
    "request_id",
    "intent_id",
    "op_kind",
    "memory_id",
    "target_memory_id",
    "source_memory_ids",
    "evidence_refs",
    "status",
    "reason_code",
    "detail",
    "decay_profile_id",
    "visibility",
    "ts_ms",
)

VAULT_MANIFEST_COLUMNS = (
    "request_id",
    "manifest_id",
    "intent_id",
    "integrity_digest",
    "manifest_path",
    "items_count",
    "evidence_refs",
    "policy_profile_id",
    "undo_ref",
    "reversible",
    "skill_name",
    "session_id",
    "seq",
    "status",
    "ts_ms",
)

VAULT_GC_COLUMNS = (
    "request_id",
    "policy_profile_id",
    "deleted_manifest_ids",
    "deleted_artifact_ids",
    "blocked_manifest_ids",
    "evidence_refs",
    "status",
    "ts_ms",
)

AIRLOCK_COLUMNS = (
    "request_id",
    "direction",
    "reason_code",
    "removed_fields",
    "redactions",
    "payload_digest",
    "quarantine_digest",
    "ingress_classification",
    "blocked",
    "detail",
    "ts_ms",
)

CHANNEL_IO_COLUMNS = (
    "request_id",
    "channel",
    "direction",
    "thread_key",
    "message_id",
    "user_id",
    "status",
    "reason_code",
    "stage",
    "detail",
    "payload_digest",
    "ts_ms",
)

AVATAR_ACTION_COLUMNS = (
    "request_id",
    "trigger",
    "emotion_tag",
    "expression_id",
    "status",
    "reason_code",
    "stage",
    "detail",
    "ts_ms",
)

CLAWD_ACTION_COLUMNS = (
    "request_id",
    "intent_id",
    "target_desc",
    "action_name",
    "confidence",
    "before_artifact_ref",
    "after_artifact_ref",
    "manifest_ref",
    "status",
    "reason_code",
    "stage",
    "detail",
    "ts_ms",
)

AGENT_LOOP_COLUMNS = (
    "request_id",
    "session_id",
    "goal",
    "round",
    "phase",
    "status",
    "goal_achieved",
    "action_plan_json",
    "report_json",
    "action_count",
    "success_count",
    "failed_count",
    "latency_ms",
    "error",
    "extra_json",
    "ts_ms",
)

SERVICE_HEALTH_COLUMNS = (
    "request_id",
    "service_id",
    "owner",
    "required",
    "status",
    "reason_code",
    "stage",
    "detail",
    "ts_ms",
)

MIGRATION_COLUMNS = (
    "request_id",
    "scope",
    "migration_id",
    "status",
    "reason_code",
    "stage",
    "detail",
    "latency_ms",
    "ts_ms",
)

CONFIG_SNAPSHOT_COLUMNS = (
    "request_id",
    "component",
    "env",
    "version",
    "digest",
    "override_sources",
    "redacted_keys",
    "snapshot_json",
    "diff_json",
    "status",
    "reason_code",
    "stage",
    "detail",
    "ts_ms",
)

VOICE_EVENT_COLUMNS = (
    "request_id",
    "session_id",
    "event_type",
    "direction",
    "seq",
    "status",
    "reason_code",
    "stage",
    "payload",
    "detail",
    "ts_ms",
)

RETRIEVAL_EVENT_COLUMNS = (
    "request_id",
    "intent_id",
    "op_kind",
    "status",
    "reason_code",
    "stage",
    "payload",
    "detail",
    "ts_ms",
)

VISION_EVENT_COLUMNS = (
    "request_id",
    "intent_id",
    "event_type",
    "status",
    "reason_code",
    "stage",
    "source_ref",
    "payload",
    "detail",
    "ts_ms",
)

BOND_UPDATE_COLUMNS = (
    "request_id",
    "intent_id",
    "session_id",
    "seq",
    "event_id",
    "user_id",
    "topic_id",
    "capability_hint",
    "update_kind",
    "status",
    "reason_code",
    "stage",
    "evidence_refs",
    "payload",
    "detail",
    "ts_ms",
)

SANDBOX_EXEC_COLUMNS = (
    "request_id",
    "intent_id",
    "sandbox_id",
    "status",
    "exit_code",
    "reason_code",
    "evidence_ref",
    "code_digest",
    "stdout_preview",
    "stderr_preview",
    "duration_ms",
    "resource_usage",
    "truncated_stdout",
    "truncated_stderr",
    "ts_ms",
)

SCHEDULER_EVENT_COLUMNS = (
    "event_id",
    "task_id",
    "trigger_ts",
    "intent_id",
    "status",
    "reason_code",
    "mode_at_trigger",
    "error_detail",
    "ts_ms",
)

CAPABILITY_FORGE_PROPOSAL_COLUMNS = (
    "proposal_id",
    "event_type",
    "status",
    "stage",
    "session_id",
    "candidate_key",
    "reason_code",
    "actor",
    "payload",
    "ts_ms",
)

# P8-1: subgraph/local loop 失败分布聚合表列定义
P8_SUBGRAPH_FAILURE_LOG_COLUMNS = (
    "request_id",
    "ts_ms",
    "execution_profile",
    "log_category",
    "error_code",
    "failed_node",
    "detail",
)

# P8-1: 三图 run_graph 执行记录表列定义
P8_GRAPH_RUN_LOG_COLUMNS = (
    "request_id",
    "ts_ms",
    "execution_profile",
    "latency_ms",
    "status",
    "budget_breach_code",
)

# P8-1: P6 封板的 local loop 失败码（与 graph_contract.LOCAL_LOOP_FAILURE_CODES 保持一致）
# 归类优先级：error_code 属于此集合 → 归入 local_loop_failure_metrics，不计入 subgraph_failure_metrics
P8_LOCAL_LOOP_FAILURE_CODES: frozenset[str] = frozenset({
    "LOCAL_LOOP_TIMEOUT",
    "LOCAL_LOOP_MAX_STEPS",
    "LOCAL_LOOP_INTERNAL_ERROR",
})

# P8-1: 表名常量
P8_TABLE_SUBGRAPH_FAILURE_LOG = "p8_subgraph_failure_log"
P8_TABLE_GRAPH_RUN_LOG = "p8_graph_run_log"

MODEL_CONTROL_COLUMNS = (
    "request_id",
    "node_id",
    "model_node_id",
    "model_profile_id",
    "provider",
    "model_id",
    "expected_param_hash",
    "actual_param_hash",
    "drift_detected",
    "approval_token_ref",
    "evidence_ref",
    "reason_code",
    "status",
    "stage",
    "ts_ms",
)

JSON_FIELDS_BY_TABLE: dict[str, set[str]] = {
    "p8_subgraph_failure_log": set(),
    "p8_graph_run_log": set(),
    "intent_log": {"normalized_json", "proposed_intent_draft_json", "lint_result"},
    "governor_log": {"rule_trace", "tokens", "snapshot_refs"},
    "skill_exec_log": set(),
    "error_log": set(),
    "qa_violation_log": set(),
    "memory_ops_log": {"source_memory_ids", "evidence_refs"},
    "vault_manifest_index": {"evidence_refs"},
    "vault_gc_log": {"deleted_manifest_ids", "deleted_artifact_ids", "blocked_manifest_ids", "evidence_refs"},
    "airlock_log": {"removed_fields", "redactions"},
    "channel_io_log": set(),
    "avatar_action_log": set(),
    "clawd_action_log": set(),
    "service_health_log": set(),
    "migration_log": set(),
    "config_snapshot_log": {"override_sources", "redacted_keys", "snapshot_json", "diff_json"},
    "voice_event_log": {"payload"},
    "retrieval_event_log": {"payload"},
    "vision_event_log": {"payload"},
    "bond_update_log": {"evidence_refs", "payload"},
    "model_control_log": set(),
    "sandbox_exec_log": {"resource_usage"},
    "scheduler_event_log": set(),
    "capability_forge_proposal_log": {"payload"},
    "llm_route_log": set(),
    "llm_node_call_log": set(),
    "graph_run_trace": {"nodes_json", "edges_json"},
}

_ALLOWED_TABLES: frozenset[str] = frozenset({
    "intent_log",
    "governor_log",
    "skill_exec_log",
    "model_control_log",
    "error_log",
    "qa_violation_log",
    "canon_proposal_log",
    "canon_change_log",
    "world_canon_doc",
    "memory_ops_log",
    "vault_manifest_index",
    "vault_gc_log",
    "airlock_log",
    "channel_io_log",
    "avatar_action_log",
    "clawd_action_log",
    "agent_loop_log",
    "service_health_log",
    "migration_log",
    "config_snapshot_log",
    "voice_event_log",
    "retrieval_event_log",
    "vision_event_log",
    "bond_update_log",
    "sandbox_exec_log",
    "scheduler_event_log",
    "capability_forge_proposal_log",
    "scheduler_tasks",
    "replay_guard",
    "session_seq_guard",
    "llm_route_log",
    "llm_node_call_log",
    "graph_run_trace",
    "supervisor_decision_log",
    # P8-1: 新增聚合表
    "p8_subgraph_failure_log",
    "p8_graph_run_log",
})


@dataclass(frozen=True)
class WalConfig:
    """WAL-related PRAGMA tunables."""
    journal_mode: str = "WAL"
    synchronous: str = "NORMAL"
    wal_autocheckpoint: int = 1000
    busy_timeout_ms: int = 5000
    cache_size_kb: int = 2000

    def as_dict(self) -> dict[str, Any]:
        return {
            "journal_mode": self.journal_mode,
            "synchronous": self.synchronous,
            "wal_autocheckpoint": self.wal_autocheckpoint,
            "busy_timeout_ms": self.busy_timeout_ms,
            "cache_size_kb": self.cache_size_kb,
        }


DEFAULT_WAL_CONFIG = WalConfig()

WRITE_RETRY_MAX = 3
WRITE_RETRY_DELAY_S = 0.05
