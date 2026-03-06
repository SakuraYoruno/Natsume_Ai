-- Migration 0020: llm_call_log 表（M41 Evidence Plane 分级写入优化）
-- 用于记录 LLM 调用日志，支持 Evidence Plane 分级写入和 token 消耗追踪。
-- 关联：DevSpec §11.1；TASK-0417~0424（M41）；graph_evidence.py。

CREATE TABLE IF NOT EXISTS llm_call_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           TEXT    NOT NULL,
    node_id          TEXT    NOT NULL,
    model_id         TEXT,
    prompt_tokens    INTEGER,
    completion_tokens INTEGER,
    latency_ms       INTEGER,
    ts_ms            INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_lcl_run_id
    ON llm_call_log(run_id);

CREATE INDEX IF NOT EXISTS idx_lcl_node_id
    ON llm_call_log(node_id);
