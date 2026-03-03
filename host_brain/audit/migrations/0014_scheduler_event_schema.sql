-- 调度器事件审计表（scheduler_event_log）
-- 记录每次调度任务触发的状态、结果与门控信息

CREATE TABLE IF NOT EXISTS scheduler_event_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT,
    task_id         TEXT,
    trigger_ts      INTEGER,
    intent_id       TEXT,
    status          TEXT,
    reason_code     TEXT,
    mode_at_trigger TEXT,
    error_detail    TEXT,
    ts_ms           INTEGER
);

CREATE INDEX IF NOT EXISTS idx_scheduler_event_task_id ON scheduler_event_log(task_id);
CREATE INDEX IF NOT EXISTS idx_scheduler_event_ts ON scheduler_event_log(ts_ms);
CREATE INDEX IF NOT EXISTS idx_scheduler_event_intent ON scheduler_event_log(intent_id);
CREATE INDEX IF NOT EXISTS idx_scheduler_event_status ON scheduler_event_log(status);
