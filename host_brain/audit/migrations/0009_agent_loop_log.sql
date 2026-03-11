-- Agent Loop 中间状态持久化表（每轮写一行，支持断点续跑和历史回溯）
CREATE TABLE IF NOT EXISTS agent_loop_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id       TEXT    NOT NULL,
    session_id       TEXT    NOT NULL,
    goal             TEXT    NOT NULL,
    round            INTEGER NOT NULL DEFAULT 0,
    phase            TEXT    NOT NULL,
    status           TEXT,
    goal_achieved    INTEGER NOT NULL DEFAULT 0,
    action_plan_json TEXT,
    report_json      TEXT,
    action_count     INTEGER,
    success_count    INTEGER,
    failed_count     INTEGER,
    latency_ms       INTEGER,
    error            TEXT,
    extra_json       TEXT,
    ts_ms            INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_loop_request_id ON agent_loop_log (request_id);
CREATE INDEX IF NOT EXISTS idx_agent_loop_session_id ON agent_loop_log (session_id);
CREATE INDEX IF NOT EXISTS idx_agent_loop_ts_ms      ON agent_loop_log (ts_ms);
