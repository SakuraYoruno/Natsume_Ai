# F-02 启动、配置与密钥

- 执行批次：`20260314_batch01_health_gate`
- 执行时间：`2026-03-14 23:46 CST`
- 结论：`BLOCK`

## 已确认项

1. 早期排障阶段曾临时使用过本机 bootstrap，但该路径现已废弃，不再属于封测方案。
2. `HOST_EDGE_RPC_SHARED_KEY` 与 `EDGE_HMAC_SHARED_KEY` 已设置，且 `EDGE_ALLOW_INSECURE_AUTH=false`。
3. `host` 启动快照显示 `llm_provider=gemini`，`key_profile_status=resolved`，关键 Host LLM 密钥已解析。

## 阻断点

1. 运行依赖配置存在明显漂移：仓库基线 `.env` 指向远端 `192.168.1.101:17800`，这才是正式目标拓扑；封测不得再切换到本机替代模式。
2. 启动日志出现实际运行告警：
   - `AuditRetention scheduler registration failed: SchedulerTaskConfig.__init__() got an unexpected keyword argument 'display_name'`
   - 启动后仍打印降级模块列表：`state_arbiter_sources`、`edge_rpc`、`distillation_engine_scheduler`、`long_term_distiller_scheduler`、`audit_retention_scheduler`、`proactive_talk_scheduler`
3. `host-scheduler/health` 当前返回 `running=false`，与“全功能高可用”目标不一致。

## 判定

密钥本身不是当前主阻断项，但配置漂移和启动期调度器异常仍未清除，`F-02` 判定为 `BLOCK`。

## 证据

- `10_health_runtime/F-02_config_snapshot.json`
- `runtime/host/start_host_sealtest.ps1`
- `runtime/edge/start_edge_sealtest.ps1`
- `runtime/host/logs/sealtest_host.stdout.log`
