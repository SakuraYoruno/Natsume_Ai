"""SQLite 审计账本 Schema 定义模块。

从 sqlite_ledger.py 拆分，包含所有建表/索引 DDL 语句。
"""

from __future__ import annotations

LEDGER_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS intent_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    thread_id TEXT,
    turn_id TEXT,
    intent_id TEXT,
    session_id TEXT,
    seq INTEGER,
    ts_ms INTEGER,
    nonce TEXT,
    parse_stage TEXT,
    raw_text TEXT,
    normalized_json TEXT,
    proposer_node_id TEXT,
    model_snapshot TEXT,
    prompt_profile_id TEXT,
    tool_visibility_hash TEXT,
    proposed_intent_draft_json TEXT,
    lint_result TEXT
);

CREATE TABLE IF NOT EXISTS governor_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    thread_id TEXT,
    turn_id TEXT,
    intent_id TEXT,
    decision TEXT,
    reason_code TEXT,
    rule_trace TEXT,
    tokens TEXT,
    snapshot_refs TEXT,
    stage TEXT,
    ts_ms INTEGER
);

CREATE TABLE IF NOT EXISTS skill_exec_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    thread_id TEXT,
    turn_id TEXT,
    intent_id TEXT,
    event_id TEXT,
    skill_name TEXT,
    approval_token_ref TEXT,
    confirm_token_ref TEXT,
    undo_token_ref TEXT,
    result_status TEXT,
    result_code TEXT,
    result_digest TEXT,
    evidence_ref TEXT,
    manifest_ref TEXT,
    ts_ms INTEGER
);

CREATE TABLE IF NOT EXISTS model_control_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    node_id TEXT,
    model_node_id TEXT,
    model_profile_id TEXT,
    provider TEXT,
    model_id TEXT,
    expected_param_hash TEXT,
    actual_param_hash TEXT,
    drift_detected INTEGER,
    approval_token_ref TEXT,
    evidence_ref TEXT,
    reason_code TEXT,
    status TEXT,
    stage TEXT,
    ts_ms INTEGER
);

CREATE TABLE IF NOT EXISTS error_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    stage TEXT,
    reason_code TEXT,
    detail TEXT,
    ts_ms INTEGER
);

CREATE TABLE IF NOT EXISTS qa_violation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    code TEXT,
    detail TEXT,
    field TEXT,
    ts_ms INTEGER
);

CREATE TABLE IF NOT EXISTS canon_proposal_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id TEXT,
    canon_kind TEXT,
    target_id TEXT,
    base_version INTEGER,
    proposed_version INTEGER,
    proposed_hash    TEXT,
    source_intent_id TEXT,
    proposer_node_id TEXT,
    summary TEXT,
    status TEXT,
    ts_ms INTEGER
);

CREATE TABLE IF NOT EXISTS canon_change_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    change_id TEXT,
    proposal_id TEXT,
    canon_kind TEXT,
    target_id TEXT,
    before_version INTEGER,
    after_version INTEGER,
    before_hash TEXT,
    after_hash TEXT,
    before_body TEXT,
    after_body TEXT,
    source_path TEXT,
    ts_ms INTEGER
);

CREATE TABLE IF NOT EXISTS world_canon_doc (
    doc_id TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    hash TEXT NOT NULL,
    status TEXT NOT NULL,
    source_path TEXT NOT NULL,
    updated_ts INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_ops_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    intent_id TEXT,
    op_kind TEXT,
    memory_id TEXT,
    target_memory_id TEXT,
    source_memory_ids TEXT,
    evidence_refs TEXT,
    status TEXT,
    reason_code TEXT,
    detail TEXT,
    decay_profile_id TEXT,
    visibility TEXT,
    ts_ms INTEGER
);

CREATE TABLE IF NOT EXISTS vault_manifest_index (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    manifest_id TEXT,
    intent_id TEXT,
    integrity_digest TEXT,
    manifest_path TEXT,
    items_count INTEGER,
    evidence_refs TEXT,
    policy_profile_id TEXT,
    undo_ref TEXT,
    reversible INTEGER,
    skill_name TEXT,
    session_id TEXT,
    seq INTEGER,
    status TEXT,
    ts_ms INTEGER
);

CREATE TABLE IF NOT EXISTS vault_gc_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    policy_profile_id TEXT,
    deleted_manifest_ids TEXT,
    deleted_artifact_ids TEXT,
    blocked_manifest_ids TEXT,
    evidence_refs TEXT,
    status TEXT,
    ts_ms INTEGER
);

CREATE TABLE IF NOT EXISTS airlock_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    direction TEXT,
    reason_code TEXT,
    removed_fields TEXT,
    redactions TEXT,
    payload_digest TEXT,
    quarantine_digest TEXT,
    ingress_classification TEXT,
    blocked INTEGER,
    detail TEXT,
    ts_ms INTEGER
);

CREATE TABLE IF NOT EXISTS channel_io_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    channel TEXT,
    direction TEXT,
    thread_key TEXT,
    message_id TEXT,
    user_id TEXT,
    status TEXT,
    reason_code TEXT,
    stage TEXT,
    detail TEXT,
    payload_digest TEXT,
    ts_ms INTEGER
);

CREATE TABLE IF NOT EXISTS avatar_action_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    trigger TEXT,
    emotion_tag TEXT,
    expression_id TEXT,
    status TEXT,
    reason_code TEXT,
    stage TEXT,
    detail TEXT,
    ts_ms INTEGER
);

CREATE TABLE IF NOT EXISTS clawd_action_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    intent_id TEXT,
    target_desc TEXT,
    action_name TEXT,
    confidence REAL,
    before_artifact_ref TEXT,
    after_artifact_ref TEXT,
    manifest_ref TEXT,
    status TEXT,
    reason_code TEXT,
    stage TEXT,
    detail TEXT,
    ts_ms INTEGER
);

CREATE TABLE IF NOT EXISTS service_health_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    service_id TEXT,
    owner TEXT,
    required INTEGER,
    status TEXT,
    reason_code TEXT,
    stage TEXT,
    detail TEXT,
    ts_ms INTEGER
);

CREATE TABLE IF NOT EXISTS migration_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    scope TEXT,
    migration_id TEXT,
    status TEXT,
    reason_code TEXT,
    stage TEXT,
    detail TEXT,
    latency_ms INTEGER,
    ts_ms INTEGER
);

CREATE TABLE IF NOT EXISTS config_snapshot_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    component TEXT,
    env TEXT,
    version TEXT,
    digest TEXT,
    override_sources TEXT,
    redacted_keys TEXT,
    snapshot_json TEXT,
    diff_json TEXT,
    status TEXT,
    reason_code TEXT,
    stage TEXT,
    detail TEXT,
    ts_ms INTEGER
);

CREATE TABLE IF NOT EXISTS voice_event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    session_id TEXT,
    event_type TEXT,
    direction TEXT,
    seq INTEGER,
    status TEXT,
    reason_code TEXT,
    stage TEXT,
    payload TEXT,
    detail TEXT,
    ts_ms INTEGER
);

CREATE TABLE IF NOT EXISTS retrieval_event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    intent_id TEXT,
    op_kind TEXT,
    status TEXT,
    reason_code TEXT,
    stage TEXT,
    payload TEXT,
    detail TEXT,
    ts_ms INTEGER
);

CREATE TABLE IF NOT EXISTS vision_event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    intent_id TEXT,
    event_type TEXT,
    status TEXT,
    reason_code TEXT,
    stage TEXT,
    source_ref TEXT,
    payload TEXT,
    detail TEXT,
    ts_ms INTEGER
);

CREATE TABLE IF NOT EXISTS bond_update_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    intent_id TEXT,
    session_id TEXT,
    seq INTEGER,
    event_id TEXT,
    user_id TEXT,
    topic_id TEXT,
    capability_hint TEXT,
    update_kind TEXT,
    status TEXT,
    reason_code TEXT,
    stage TEXT,
    evidence_refs TEXT,
    payload TEXT,
    detail TEXT,
    ts_ms INTEGER
);

CREATE TABLE IF NOT EXISTS sandbox_exec_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    intent_id TEXT,
    sandbox_id TEXT,
    status TEXT,
    exit_code INTEGER,
    reason_code TEXT,
    evidence_ref TEXT,
    code_digest TEXT,
    stdout_preview TEXT,
    stderr_preview TEXT,
    duration_ms INTEGER,
    resource_usage TEXT,
    truncated_stdout INTEGER,
    truncated_stderr INTEGER,
    ts_ms INTEGER
);

CREATE TABLE IF NOT EXISTS replay_guard (
    intent_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    nonce TEXT NOT NULL,
    ts_ms INTEGER NOT NULL,
    UNIQUE(session_id, nonce)
);

CREATE TABLE IF NOT EXISTS session_seq_guard (
    session_id TEXT PRIMARY KEY,
    seq INTEGER NOT NULL,
    updated_ts_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_intent_request ON intent_log(request_id);
CREATE INDEX IF NOT EXISTS idx_intent_intent_id ON intent_log(intent_id);
CREATE INDEX IF NOT EXISTS idx_governor_request ON governor_log(request_id);
CREATE INDEX IF NOT EXISTS idx_governor_intent_id ON governor_log(intent_id);
CREATE INDEX IF NOT EXISTS idx_skill_request ON skill_exec_log(request_id);
CREATE INDEX IF NOT EXISTS idx_skill_intent_id ON skill_exec_log(intent_id);
CREATE INDEX IF NOT EXISTS idx_skill_evidence_ref ON skill_exec_log(evidence_ref);
CREATE INDEX IF NOT EXISTS idx_model_control_request ON model_control_log(request_id);
CREATE INDEX IF NOT EXISTS idx_model_control_profile ON model_control_log(model_profile_id);
CREATE INDEX IF NOT EXISTS idx_canon_proposal_target ON canon_proposal_log(canon_kind, target_id);
CREATE INDEX IF NOT EXISTS idx_canon_proposal_status ON canon_proposal_log(status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_canon_proposal_id_unique ON canon_proposal_log(proposal_id);
CREATE INDEX IF NOT EXISTS idx_canon_change_target ON canon_change_log(canon_kind, target_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_canon_change_id_unique ON canon_change_log(change_id);

-- M10-C: semantic memory table (distillation result).
CREATE TABLE IF NOT EXISTS semantic_memory (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    semantic_id         TEXT NOT NULL,
    summary             TEXT NOT NULL,
    source_memory_ids   TEXT NOT NULL,
    source_hash         TEXT NOT NULL,
    distill_strategy    TEXT NOT NULL DEFAULT 'merge_top_k',
    confidence          REAL NOT NULL DEFAULT 0.0,
    tags                TEXT NOT NULL DEFAULT '[]',
    ts_ms               INTEGER NOT NULL,
    operator_node_id    TEXT NOT NULL DEFAULT 'distillation_engine',
    deleted_at          INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_semantic_memory_id ON semantic_memory(semantic_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_semantic_memory_source_hash ON semantic_memory(source_hash);
CREATE INDEX IF NOT EXISTS idx_semantic_memory_ts ON semantic_memory(ts_ms);
CREATE INDEX IF NOT EXISTS idx_memory_ops_memory_id ON memory_ops_log(memory_id);
CREATE INDEX IF NOT EXISTS idx_memory_ops_request ON memory_ops_log(request_id);
CREATE INDEX IF NOT EXISTS idx_vault_manifest_id ON vault_manifest_index(manifest_id);
CREATE INDEX IF NOT EXISTS idx_vault_manifest_intent ON vault_manifest_index(intent_id);
CREATE INDEX IF NOT EXISTS idx_vault_gc_ts ON vault_gc_log(ts_ms);
CREATE INDEX IF NOT EXISTS idx_airlock_request ON airlock_log(request_id);
CREATE INDEX IF NOT EXISTS idx_airlock_direction ON airlock_log(direction);
CREATE INDEX IF NOT EXISTS idx_airlock_ts ON airlock_log(ts_ms);
CREATE INDEX IF NOT EXISTS idx_channel_io_request ON channel_io_log(request_id);
CREATE INDEX IF NOT EXISTS idx_channel_io_thread ON channel_io_log(thread_key);
CREATE INDEX IF NOT EXISTS idx_channel_io_message ON channel_io_log(channel, direction, message_id);
CREATE INDEX IF NOT EXISTS idx_channel_io_ts ON channel_io_log(ts_ms);
CREATE INDEX IF NOT EXISTS idx_avatar_action_request ON avatar_action_log(request_id);
CREATE INDEX IF NOT EXISTS idx_avatar_action_emotion ON avatar_action_log(emotion_tag);
CREATE INDEX IF NOT EXISTS idx_avatar_action_ts ON avatar_action_log(ts_ms);
CREATE INDEX IF NOT EXISTS idx_clawd_action_request ON clawd_action_log(request_id);
CREATE INDEX IF NOT EXISTS idx_clawd_action_intent ON clawd_action_log(intent_id);
CREATE INDEX IF NOT EXISTS idx_clawd_action_ts ON clawd_action_log(ts_ms);
CREATE INDEX IF NOT EXISTS idx_service_health_request ON service_health_log(request_id);
CREATE INDEX IF NOT EXISTS idx_service_health_service_id ON service_health_log(service_id);
CREATE INDEX IF NOT EXISTS idx_service_health_ts ON service_health_log(ts_ms);
CREATE INDEX IF NOT EXISTS idx_migration_request ON migration_log(request_id);
CREATE INDEX IF NOT EXISTS idx_migration_scope ON migration_log(scope);
CREATE INDEX IF NOT EXISTS idx_migration_id ON migration_log(migration_id);
CREATE INDEX IF NOT EXISTS idx_migration_ts ON migration_log(ts_ms);
CREATE INDEX IF NOT EXISTS idx_config_snapshot_request ON config_snapshot_log(request_id);
CREATE INDEX IF NOT EXISTS idx_config_snapshot_component ON config_snapshot_log(component, env);
CREATE INDEX IF NOT EXISTS idx_config_snapshot_digest ON config_snapshot_log(digest);
CREATE INDEX IF NOT EXISTS idx_config_snapshot_ts ON config_snapshot_log(ts_ms);
CREATE INDEX IF NOT EXISTS idx_voice_event_request ON voice_event_log(request_id);
CREATE INDEX IF NOT EXISTS idx_voice_event_session ON voice_event_log(session_id);
CREATE INDEX IF NOT EXISTS idx_voice_event_type ON voice_event_log(event_type);
CREATE INDEX IF NOT EXISTS idx_voice_event_ts ON voice_event_log(ts_ms);
CREATE INDEX IF NOT EXISTS idx_retrieval_event_request ON retrieval_event_log(request_id);
CREATE INDEX IF NOT EXISTS idx_retrieval_event_op_kind ON retrieval_event_log(op_kind);
CREATE INDEX IF NOT EXISTS idx_retrieval_event_ts ON retrieval_event_log(ts_ms);
CREATE INDEX IF NOT EXISTS idx_vision_event_request ON vision_event_log(request_id);
CREATE INDEX IF NOT EXISTS idx_vision_event_type ON vision_event_log(event_type);
CREATE INDEX IF NOT EXISTS idx_vision_event_source_ref ON vision_event_log(source_ref);
CREATE INDEX IF NOT EXISTS idx_vision_event_ts ON vision_event_log(ts_ms);
CREATE INDEX IF NOT EXISTS idx_bond_update_request ON bond_update_log(request_id);
CREATE INDEX IF NOT EXISTS idx_bond_update_intent ON bond_update_log(intent_id);
CREATE INDEX IF NOT EXISTS idx_bond_update_event ON bond_update_log(event_id);
CREATE INDEX IF NOT EXISTS idx_bond_update_user ON bond_update_log(user_id);
CREATE INDEX IF NOT EXISTS idx_bond_update_ts ON bond_update_log(ts_ms);
CREATE INDEX IF NOT EXISTS idx_sandbox_exec_request ON sandbox_exec_log(request_id);
CREATE INDEX IF NOT EXISTS idx_sandbox_exec_sandbox_id ON sandbox_exec_log(sandbox_id);
CREATE INDEX IF NOT EXISTS idx_sandbox_exec_evidence ON sandbox_exec_log(evidence_ref);
CREATE INDEX IF NOT EXISTS idx_sandbox_exec_ts ON sandbox_exec_log(ts_ms);

CREATE INDEX IF NOT EXISTS idx_intent_ts ON intent_log(ts_ms);
CREATE INDEX IF NOT EXISTS idx_governor_ts ON governor_log(ts_ms);
CREATE INDEX IF NOT EXISTS idx_skill_exec_ts ON skill_exec_log(ts_ms);
CREATE INDEX IF NOT EXISTS idx_model_control_ts ON model_control_log(ts_ms);
CREATE INDEX IF NOT EXISTS idx_error_ts ON error_log(ts_ms);
CREATE INDEX IF NOT EXISTS idx_qa_violation_ts ON qa_violation_log(ts_ms);
CREATE INDEX IF NOT EXISTS idx_memory_ops_ts ON memory_ops_log(ts_ms);

CREATE TABLE IF NOT EXISTS scheduler_tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id         TEXT NOT NULL UNIQUE,
    task_name       TEXT NOT NULL,
    trigger_mode    TEXT NOT NULL,
    trigger_config  TEXT NOT NULL,
    intent_template TEXT NOT NULL,
    priority        TEXT NOT NULL DEFAULT 'MEDIUM',
    enabled         INTEGER NOT NULL DEFAULT 1,
    max_consecutive_failures INTEGER NOT NULL DEFAULT 3,
    registered_at   INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL,
    registered_by   TEXT NOT NULL DEFAULT 'scheduler'
);
CREATE INDEX IF NOT EXISTS idx_scheduler_tasks_enabled ON scheduler_tasks (enabled);
CREATE INDEX IF NOT EXISTS idx_scheduler_tasks_trigger_mode ON scheduler_tasks (trigger_mode);

-- Capability Forge proposal event log (P4 runtime persistence/audit)
CREATE TABLE IF NOT EXISTS capability_forge_proposal_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id     TEXT    NOT NULL,
    event_type      TEXT    NOT NULL,
    status          TEXT    NOT NULL,
    stage           TEXT    NOT NULL,
    session_id      TEXT,
    candidate_key   TEXT,
    reason_code     TEXT,
    actor           TEXT,
    payload         TEXT,
    ts_ms           INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_capability_forge_proposal_id ON capability_forge_proposal_log (proposal_id);
CREATE INDEX IF NOT EXISTS idx_capability_forge_status ON capability_forge_proposal_log (status);
CREATE INDEX IF NOT EXISTS idx_capability_forge_stage ON capability_forge_proposal_log (stage);
CREATE INDEX IF NOT EXISTS idx_capability_forge_ts ON capability_forge_proposal_log (ts_ms);

-- M18-A: LLM 云节点调用记录表
CREATE TABLE IF NOT EXISTS llm_node_call_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id      TEXT NOT NULL,
    node_id         TEXT NOT NULL,
    provider        TEXT NOT NULL,
    model_id        TEXT NOT NULL,
    status          TEXT NOT NULL,
    reason_code     TEXT,
    latency_ms      INTEGER,
    input_digest    TEXT,
    output_digest   TEXT,
    ts_ms           INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_node_call_request ON llm_node_call_log (request_id);
CREATE INDEX IF NOT EXISTS idx_llm_node_call_node_id ON llm_node_call_log (node_id);
CREATE INDEX IF NOT EXISTS idx_llm_node_call_ts ON llm_node_call_log (ts_ms);

-- M18-A: LangGraph 会话链路追踪表
CREATE TABLE IF NOT EXISTS graph_run_trace (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id      TEXT NOT NULL UNIQUE,
    session_id      TEXT,
    total_latency_ms INTEGER,
    status          TEXT NOT NULL,
    reason_code     TEXT,
    node_count      INTEGER,
    nodes_json      TEXT,
    edges_json      TEXT,
    ts_ms           INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_graph_run_trace_request ON graph_run_trace (request_id);
CREATE INDEX IF NOT EXISTS idx_graph_run_trace_session ON graph_run_trace (session_id);
CREATE INDEX IF NOT EXISTS idx_graph_run_trace_ts ON graph_run_trace (ts_ms);

-- M25-A: LLM 多模型路由决策审计表
CREATE TABLE IF NOT EXISTS llm_route_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id    TEXT    NOT NULL,
    model_id      TEXT    NOT NULL,
    reason        TEXT    NOT NULL,
    latency_ms    INTEGER NOT NULL DEFAULT 0,
    fallback_used INTEGER NOT NULL DEFAULT 0,
    ts_ms         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_route_log_request_id ON llm_route_log (request_id);
CREATE INDEX IF NOT EXISTS idx_llm_route_log_ts_ms ON llm_route_log (ts_ms);

-- M26-C: LLM 路由配置热更新审计表
CREATE TABLE IF NOT EXISTS llm_route_config_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id     TEXT    NOT NULL,
    before_hash    TEXT    NOT NULL,
    after_hash     TEXT    NOT NULL,
    changed_fields TEXT    NOT NULL,
    ts_ms          INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_route_config_log_request_id ON llm_route_config_log (request_id);
CREATE INDEX IF NOT EXISTS idx_llm_route_config_log_ts_ms ON llm_route_config_log (ts_ms);

-- Agent Loop 中间状态持久化表（每轮写一行，支持断点续跑和历史回溯）
CREATE TABLE IF NOT EXISTS agent_loop_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id       TEXT    NOT NULL,          -- Agent Loop 请求 ID
    session_id       TEXT    NOT NULL,          -- ClawdBot 会话 ID
    goal             TEXT    NOT NULL,          -- 自动化目标
    round            INTEGER NOT NULL DEFAULT 0, -- 当前轮次（0=loop级事件）
    phase            TEXT    NOT NULL,          -- 事件阶段（LOOP_START/ROUND_START/...）
    status           TEXT,                      -- 本轮/本阶段状态（OK/FAILED/SUCCESS/CONTINUE）
    goal_achieved    INTEGER NOT NULL DEFAULT 0, -- 是否达成目标（0/1）
    action_plan_json TEXT,                      -- ActionPlan JSON（PLAN_GENERATED 阶段写入）
    report_json      TEXT,                      -- ActionReport JSON（EXECUTE_DONE 阶段写入）
    action_count     INTEGER,                   -- 本轮指令数量
    success_count    INTEGER,                   -- 成功执行数量
    failed_count     INTEGER,                   -- 失败执行数量
    latency_ms       INTEGER,                   -- 本轮耗时（毫秒）
    error            TEXT,                      -- 错误信息（如有）
    extra_json       TEXT,                      -- 扩展字段（JSON）
    ts_ms            INTEGER NOT NULL           -- 事件时间戳（毫秒）
);
CREATE INDEX IF NOT EXISTS idx_agent_loop_request_id ON agent_loop_log (request_id);
CREATE INDEX IF NOT EXISTS idx_agent_loop_session_id ON agent_loop_log (session_id);
CREATE INDEX IF NOT EXISTS idx_agent_loop_ts_ms      ON agent_loop_log (ts_ms);

-- P5-4: Supervisor 决策观测表（rule/llm/llm_fallback_rule、failure_code、selected_agent）
CREATE TABLE IF NOT EXISTS supervisor_decision_log (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id                TEXT    NOT NULL,
    ts_ms                     INTEGER NOT NULL,
    supervisor_mode           TEXT    NOT NULL,
    supervisor_decision_source TEXT  NOT NULL,
    supervisor_failure_code   TEXT,
    supervisor_selected_agent TEXT   NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_supervisor_decision_request ON supervisor_decision_log (request_id);
CREATE INDEX IF NOT EXISTS idx_supervisor_decision_ts ON supervisor_decision_log (ts_ms);

-- P8-1: subgraph/local loop 失败分布聚合表
-- 每次 run_graph 完成后，若存在 subgraph_failure，写入一行；
-- error_code 前缀为 LOCAL_LOOP_* 的归入 log_category='local_loop'，其余归入 'subgraph'
CREATE TABLE IF NOT EXISTS p8_subgraph_failure_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id      TEXT    NOT NULL,
    ts_ms           INTEGER NOT NULL,
    execution_profile TEXT,
    log_category    TEXT    NOT NULL,   -- 'subgraph' | 'local_loop'（归类优先级见 P8_SCOPE §4.1）
    error_code      TEXT    NOT NULL,
    failed_node     TEXT,
    detail          TEXT
);
CREATE INDEX IF NOT EXISTS idx_p8_subgraph_failure_request ON p8_subgraph_failure_log (request_id);
CREATE INDEX IF NOT EXISTS idx_p8_subgraph_failure_category ON p8_subgraph_failure_log (log_category);
CREATE INDEX IF NOT EXISTS idx_p8_subgraph_failure_code ON p8_subgraph_failure_log (error_code);
CREATE INDEX IF NOT EXISTS idx_p8_subgraph_failure_ts ON p8_subgraph_failure_log (ts_ms);

-- P8-1: 三图 run_graph 执行记录表（延迟聚合 + Intent budget breach 聚合）
-- 每次 run_graph 完成后写入一行，记录 execution_profile、总耗时、budget breach 信息
CREATE TABLE IF NOT EXISTS p8_graph_run_log (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id           TEXT    NOT NULL,
    ts_ms                INTEGER NOT NULL,
    execution_profile    TEXT,           -- CHAT_FAST | WORKFLOW_SUPERVISED | INTENT_EXEC
    latency_ms           INTEGER,        -- run_graph 总耗时（毫秒）
    status               TEXT,           -- OK | FAILED 等
    budget_breach_code   TEXT            -- Intent 图 budget breach 失败码（若有）
);
CREATE INDEX IF NOT EXISTS idx_p8_graph_run_request ON p8_graph_run_log (request_id);
CREATE INDEX IF NOT EXISTS idx_p8_graph_run_profile ON p8_graph_run_log (execution_profile);
CREATE INDEX IF NOT EXISTS idx_p8_graph_run_ts ON p8_graph_run_log (ts_ms);
"""


def apply_schema(conn) -> None:
    """将所有建表/索引 DDL 应用到给定的 SQLite 连接。"""
    with conn:
        conn.executescript(LEDGER_SCHEMA_SQL)
