-- M26-C: LLM 路由配置热更新审计表迁移脚本
-- 幂等：CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS

CREATE TABLE IF NOT EXISTS llm_route_config_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id     TEXT    NOT NULL,
    before_hash    TEXT    NOT NULL,
    after_hash     TEXT    NOT NULL,
    changed_fields TEXT    NOT NULL,
    ts_ms          INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_llm_route_config_log_request_id
    ON llm_route_config_log (request_id);

CREATE INDEX IF NOT EXISTS idx_llm_route_config_log_ts_ms
    ON llm_route_config_log (ts_ms);
