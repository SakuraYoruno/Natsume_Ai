-- Migration 0019: subgraph_trace 表（M41 Evidence Plane 分级写入优化）
-- 用于记录 LangGraph 子图执行轨迹，支持 Evidence Plane 分级写入和延迟测量。
-- 关联：DevSpec §11.1；TASK-0417~0424（M41）；graph_evidence.py。

CREATE TABLE IF NOT EXISTS subgraph_trace (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT    NOT NULL,
    parent_path TEXT,
    node_id     TEXT    NOT NULL,
    input_digest  TEXT,
    output_digest TEXT,
    latency_ms  INTEGER,
    ts_ms       INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sgt_run_id
    ON subgraph_trace(run_id);

CREATE INDEX IF NOT EXISTS idx_sgt_parent_path
    ON subgraph_trace(parent_path);
