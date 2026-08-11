-- AW-03: catalog-owned Graph Evidence persistence.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS graph_trace (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    graph_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    status TEXT NOT NULL,
    reason_code TEXT,
    detail TEXT,
    ts_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS subgraph_summary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    subgraph_id TEXT NOT NULL,
    node_count INTEGER NOT NULL,
    success_count INTEGER NOT NULL,
    fail_count INTEGER NOT NULL,
    committed_at_ms INTEGER NOT NULL,
    graph_trace_flat_json TEXT
);

CREATE TABLE IF NOT EXISTS clock_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    graph_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    node_id TEXT NOT NULL,
    elapsed_ms INTEGER NOT NULL,
    threshold_ms INTEGER NOT NULL,
    ts_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS branch_manifest (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    graph_id TEXT NOT NULL,
    total_count INTEGER NOT NULL,
    success_count INTEGER NOT NULL,
    fail_count INTEGER NOT NULL,
    committed_at_ms INTEGER NOT NULL,
    branches_json TEXT
);

CREATE TABLE IF NOT EXISTS stream_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    graph_id TEXT NOT NULL,
    phase TEXT NOT NULL,
    event_type TEXT NOT NULL,
    node_id TEXT,
    chunk TEXT,
    ts_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    graph_id TEXT NOT NULL,
    interrupt_reason TEXT,
    resume_deadline_at INTEGER,
    created_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS barrier_join_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    graph_id TEXT NOT NULL,
    passed INTEGER NOT NULL,
    failed_branches_json TEXT,
    join_policy_ref TEXT,
    reason TEXT,
    ts_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS subgraph_trace (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    parent_path TEXT,
    node_id TEXT NOT NULL,
    input_digest TEXT,
    output_digest TEXT,
    latency_ms INTEGER,
    ts_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_call_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    model_id TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    latency_ms INTEGER,
    ts_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_graph_trace_request
    ON graph_trace(request_id, id);
CREATE INDEX IF NOT EXISTS idx_subgraph_summary_request
    ON subgraph_summary(request_id, id);
CREATE INDEX IF NOT EXISTS idx_clock_events_request
    ON clock_events(request_id, id);
CREATE INDEX IF NOT EXISTS idx_branch_manifest_request
    ON branch_manifest(request_id, id);
CREATE INDEX IF NOT EXISTS idx_stream_events_request
    ON stream_events(request_id, id);
CREATE INDEX IF NOT EXISTS idx_stream_events_time_type
    ON stream_events(ts_ms, event_type);
CREATE INDEX IF NOT EXISTS idx_checkpoints_request
    ON checkpoints(request_id);
CREATE INDEX IF NOT EXISTS idx_bjl_run_id
    ON barrier_join_log(request_id);
CREATE INDEX IF NOT EXISTS idx_bjl_ts
    ON barrier_join_log(ts_ms);
CREATE INDEX IF NOT EXISTS idx_sgt_run_id
    ON subgraph_trace(run_id);
CREATE INDEX IF NOT EXISTS idx_sgt_parent_path
    ON subgraph_trace(parent_path);
CREATE INDEX IF NOT EXISTS idx_lcl_run_id
    ON llm_call_log(run_id);
CREATE INDEX IF NOT EXISTS idx_lcl_node_id
    ON llm_call_log(node_id);

COMMIT;
