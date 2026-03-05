-- 0022_llm_route_log.sql
-- M25-A: LLM 多模型路由决策审计表
-- 记录每次路由决策（主模型/备用/降级原因/延迟）
-- 幂等：重复执行不报错

CREATE TABLE IF NOT EXISTS llm_route_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id  TEXT    NOT NULL,
    model_id    TEXT    NOT NULL,
    reason      TEXT    NOT NULL,
    latency_ms  INTEGER NOT NULL DEFAULT 0,
    fallback_used INTEGER NOT NULL DEFAULT 0,  -- 0=false, 1=true
    ts_ms       INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_llm_route_log_request_id ON llm_route_log(request_id);
CREATE INDEX IF NOT EXISTS idx_llm_route_log_ts_ms      ON llm_route_log(ts_ms);
