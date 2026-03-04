-- Migration 0018: scheduler_tasks 持久化表（M16-C TASK-0180）
-- 用于 NatsumeScheduler CRON 任务持久化，进程重启后自动恢复任务注册。
-- F-40 修复：CRON 任务进程重启后不持久化（内存态）。

CREATE TABLE IF NOT EXISTS scheduler_tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id         TEXT NOT NULL UNIQUE,
    task_name       TEXT NOT NULL,
    trigger_mode    TEXT NOT NULL,
    trigger_config  TEXT NOT NULL,   -- JSON
    intent_template TEXT NOT NULL,   -- JSON
    priority        TEXT NOT NULL DEFAULT 'NORMAL',
    enabled         INTEGER NOT NULL DEFAULT 1,
    max_consecutive_failures INTEGER NOT NULL DEFAULT 3,
    registered_at   INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL,
    registered_by   TEXT NOT NULL DEFAULT 'scheduler'
);

CREATE INDEX IF NOT EXISTS idx_scheduler_tasks_enabled
    ON scheduler_tasks (enabled);

CREATE INDEX IF NOT EXISTS idx_scheduler_tasks_trigger_mode
    ON scheduler_tasks (trigger_mode);
