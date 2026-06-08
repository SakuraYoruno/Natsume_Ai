受众：MASTER
标题：当前运行链路验收报告（2026-05-31）

# 当前运行链路验收报告（2026-05-31）

版本：v1.0
维护日期：2026-05-31
标签：runtime-acceptance、host、edge、codex-app-server、main-chain、observability

## 结论

当前运行链路总体可用。

本次实测覆盖：

- Host `/v1/chat` 主链 COMMIT、graph trace、retrieval event、LLM node log。
- 真实外部模型调用路径的 token usage 记账。
- Codex CLI app-server 复用路径。
- Host 到 Edge、Edge 回连 Host 的双向连通。

需要继续跟进的点：

- Codex app-server 进程未新增，说明复用存在；但两轮 `codex exec` 仍耗时约 `46-52s`，不符合“热连接应明显降低冷启动体感”的目标，需要继续区分 Host 后台任务路径与直接 CLI `exec` 路径。
- 主链存在 deterministic renderer 与 surface memory direct renderer，这些路径不会产生 `llm_token_usage_log`；验收 token 记账时必须使用真实外部模型路径。

## 运行面

- Host：`http://127.0.0.1:18080`
- Edge RPC：`http://192.168.1.101:17800`
- Edge Gateway：`http://192.168.1.101:19000`
- Edge SSH：`edge-lan`
- Host LAN IP：`192.168.1.188`
- Codex Home：`C:\Users\34145\.codex-natsume`

## Host 主链验收

### 1. 无记忆防幻觉路径

请求目标：验证当 `person_id` 下没有足够贴合的偏好记忆时，主链不会硬说“记得”。

结果：

- request_id：`req_59a2af81dace4fee807b0675fd7a9f78`
- COMMIT：`APPROVE`
- graph_run_trace：`OK`
- node_count：`18`
- retrieval_event_log：存在
- retrieval pipeline：`DEGRADED / SURFACE_MEMORY_RECALL_COVERAGE_FAILED`
- 回复：`我没找到足够贴合这句的偏好记忆，不能硬说记得。`
- llm_node_call_log：`surface_memory_direct / surface-memory-direct-renderer`
- llm_token_usage_log：无

判定：通过防幻觉验收。该路径无 token log 是预期行为，因为没有调用外部模型。

### 2. deterministic renderer 路径

请求目标：验证 Host 能直接组合运行状态回复，并落 graph trace 与 retrieval event。

结果：

- request_id：`req_091ec9a296104512b242a81c86c75db8`
- COMMIT：`APPROVE`
- graph_run_trace：`OK`
- node_count：`19`
- retrieval_event_log：`SKIPPED / SURFACE_MEMORY_RECALL_SKIPPED`
- query_plan：`NO_RECALL / fresh_topic`
- llm_node_call_log：`host / deterministic-plan-renderer`
- llm_token_usage_log：无

判定：通过 deterministic 主链验收。该路径无 token log 是预期行为，因为没有调用外部模型。

### 3. 真实 LLM token 记账路径

请求目标：验证真正调用外部模型时，`llm_token_usage_log` 与 `llm_node_call_log` 都能落库。

结果：

- request_id：`req_565109d5adbc48c2925d5a340de04ad1`
- graph_run_trace：`OK`
- node_count：`19`
- total_latency_ms：`1755`
- retrieval_event_log：`SKIPPED / SURFACE_MEMORY_RECALL_SKIPPED`
- llm_node_call_log：
  - `llm_pool:chat_surface / deepseek / deepseek-chat / OK`
  - `cortex_orchestrator_llm_stream / deepseek / deepseek-chat / OK`
- llm_token_usage_log：
  - scene_type：`CHAT_SURFACE`
  - cognitive_tier：`A`
  - provider：`deepseek`
  - model_id：`deepseek-chat`
  - total_tokens：`6406`
  - billable_total_tokens：`6406`

判定：通过真实 LLM 记账验收。

## Codex app-server 验收

### 命令口径

```powershell
$env:CODEX_HOME='C:\Users\34145\.codex-natsume'
$env:HOST_CODEX_CLI_APP_SERVER_ENABLED='true'
$env:CODEX_CLI_APP_SERVER_ENABLED='true'

codex exec --skip-git-repo-check --sandbox danger-full-access `
  "Print exactly NATSUME_HOTLINK_ACCEPTANCE_1 and nothing else."

codex exec --skip-git-repo-check --sandbox danger-full-access `
  "Print exactly NATSUME_HOTLINK_ACCEPTANCE_2 and nothing else."
```

结果：

- turn1 output：`NATSUME_HOTLINK_ACCEPTANCE_1`
- turn2 output：`NATSUME_HOTLINK_ACCEPTANCE_2`
- turn1 elapsed：约 `51683ms`
- turn2 elapsed：约 `46158ms`
- before/mid/after PID 集合未新增。

当前 app-server 进程：

- Desktop app-server：`codex.exe app-server --analytics-default-enabled`
- 项目侧 stdio app-server：
  - `cmd.exe ... codex.cmd app-server ... --listen stdio://`
  - `node.exe ... @openai/codex/bin/codex.js app-server ... --listen stdio://`
  - `codex.exe app-server ... --listen stdio://`

判定：

- 功能可用：通过。
- 进程复用：通过。
- 热连接体感：未达理想，需要继续优化或改测 Host 后台任务实际调用路径。

## Codex app-server 慢定位

### 定位结论

慢点不是认证问题，也不是 app-server worker 本体不可用。

当前问题拆成三层：

- 裸 `codex exec` 不等于 Host app-server 热连接；它每次都会启动一次 CLI 非交互执行链路，并创建新的 Codex session。
- Host LLM provider 的 `invoke_codex_cli_text()` app-server 路径可用，同一进程同一 feature 两轮实测约 `3872ms / 2461ms`。
- Host 当前不是“一个 app-server 复用多线程”，而是按 `feature_key/session_key` 启了多组 stdio app-server 进程；这会造成后台任务越多，Codex 进程组越多。

### 证据

裸 CLI 测试：

- `codex exec` 两轮输出正确。
- elapsed：约 `51683ms / 46158ms`。
- stdout 显示每轮是新的 `session id`。
- 该测试没有命中 Host 内部 `invoke_codex_cli_text()` worker 缓存，因此不能作为 app-server 热连接性能口径。

Host provider 直连 app-server 测试：

```powershell
$env:HOST_CODEX_CLI_HOME='C:/Users/34145/.codex-natsume'
$env:HOST_CODEX_CLI_APP_SERVER_ENABLED='true'
$env:HOST_CODEX_CLI_EXEC_FALLBACK_ENABLED='false'
$env:HOST_CODEX_MODEL_ID='gpt-5.5'
```

同一 Python 进程内连续调用 `invoke_codex_cli_text()` 两轮：

- turn1：`3872ms`
- turn2：`2461ms`
- 输出分别为 `APP_SERVER_DIRECT_DIAG_1` / `APP_SERVER_DIRECT_DIAG_2`

当前 Host 子进程中存在多组 stdio app-server：

- `pool:INNER_WORLD:B`
- `inner_activity`
- `subagent_gateway`
- `pool:EVOLUTION_DESIRE:B`

这些进程均由 Host 进程派生，命令形态为：

```text
codex.cmd app-server --disable plugins --disable apps --disable multi_agent --disable tool_suggest --disable shell_snapshot --disable personality --enable fast_mode ... --listen stdio://
```

每组 app-server 对应一个 `command.json`：

```text
C:\Users\34145\AppData\Local\Temp\natsume_codex_cli_lm\app-server-*\command.json
```

`command.json` 显示 `session_key` 当前包含：

```text
{feature_key}|model={model_id}|sandbox={sandbox}|cwd={workdir}
```

这意味着不同 feature 会各自启动独立 app-server worker。

### 代码根因

- `host_brain/integrations/codex_cli_provider.py` 的 app-server worker 缓存以完整 `session_key` 为 key。
- `session_key` 默认包含 `feature_key`。
- 因此 `inner_activity`、`pool:INNER_WORLD:B`、`subagent_gateway`、`pool:EVOLUTION_DESIRE:B` 会各开一组 app-server 进程。
- `host_brain/codex_runtime/executor.py` 的 WorkOrder runtime 仍直接构造 `codex exec` 并 `subprocess.Popen()`，没有走 `invoke_codex_cli_text()` 的 app-server worker。

### 临时规避方案

如果短期目标是“强制只开一组 Host Codex LLM app-server”，可在 Host 环境里设置：

```powershell
HOST_CODEX_CLI_SESSION_KEY=natsume-host-codex-lm
HOST_CODEX_APP_SERVER_ROLLBACK_AFTER_TURN=true
```

影响：

- 所有 feature 共用同一个 app-server worker 与同一个 thread key。
- 因为当前默认每轮后 `thread/rollback`，理论上不会保留跨 feature 对话历史。
- 风险是 rollback 失败时可能出现跨 feature 上下文污染，所以这只能作为临时规避，不是最终设计。

### 已落地修复方案

2026-05-31 已在 `host_brain/integrations/codex_cli_provider.py` 落地 server/thread key 拆分：

- `server_key`：只包含 `command + codex_home + model + sandbox + workdir + disabled_features + reasoning/web_search`，用于复用同一个 app-server 进程。
- `thread_key`：继续使用原 `session_key`，保留 `feature_key + response_format` 等 feature 维度，用于同一 app-server 进程内的 thread 隔离。

预期效果：

- 相同模型、沙箱、工作目录和 app-server 配置下，不再按 `feature_key` 各启一组 app-server 进程。
- `inner_activity`、`pool:INNER_WORLD:B`、`subagent_gateway`、`pool:EVOLUTION_DESIRE:B` 等后台 feature 可以共享同一个 app-server 进程。
- 每个 feature 仍有独立 thread；默认每轮后 `thread/rollback`，降低跨 feature 上下文污染风险。

### 修复后实测

Host 已重启并加载新代码。

provider 级验证：

- 连续触发 4 个不同 `feature_key`：`diag_feature_a`、`diag_feature_b`、`diag_feature_c`、`diag_feature_d`
- 输出均正确：`SERVER_KEY_FIX_1` 到 `SERVER_KEY_FIX_4`
- `_APP_SERVER_WORKERS` 数量：`1`
- 该 worker 内 `thread_count`：`4`
- 说明：不同 feature 已共享同一个 app-server 进程，并保留独立 thread。

系统进程验证：

- Host PID：`44776`
- Host 子进程中仅发现一组 stdio app-server：
  - `cmd.exe ... codex.cmd app-server ... --listen stdio://`
  - `node.exe ... @openai/codex/bin/codex.js app-server ... --listen stdio://`
  - `codex.exe app-server ... --listen stdio://`
- 旧的 `pool:INNER_WORLD:B`、`inner_activity`、`subagent_gateway`、`pool:EVOLUTION_DESIRE:B` 多组 app-server 进程未复现。

### 启动预热方案

2026-05-31 继续补齐 Host 启动预热：

- `host_brain/integrations/codex_cli_provider.py` 新增 `prewarm_codex_app_server()`。
- `host_brain/app/startup.py` 的 `warmup_codex_exec_intent_pool()` 会在 Host startup 时先预建共享 app-server 和常用 feature thread，再执行原有 EXEC_INTENT 真实模型 warmup。

默认预热 feature：

- `pool:EXEC_INTENT:C`
- `inner_activity`
- `subagent_gateway`
- `pool:INNER_WORLD:B`
- `pool:EVOLUTION_DESIRE:B`
- `memory_distillation`
- `pool:SCHEDULER_NARRATIVE:D`

新增配置：

```text
HOST_STARTUP_CODEX_APP_SERVER_PREWARM_ENABLED=true
HOST_CODEX_APP_SERVER_PREWARM_FEATURE_KEYS=pool:EXEC_INTENT:C,inner_activity,subagent_gateway,pool:INNER_WORLD:B,pool:EVOLUTION_DESIRE:B,memory_distillation,pool:SCHEDULER_NARRATIVE:D
HOST_CODEX_APP_SERVER_PREWARM_TIMEOUT_MS=30000
```

注意：

- `HOST_STARTUP_CODEX_APP_SERVER_PREWARM_ENABLED` 控制低成本 app-server/thread 预热。
- `HOST_STARTUP_CODEX_EXEC_INTENT_WARMUP_ENABLED` 只控制 EXEC_INTENT 真实模型 warmup。
- 即使 `HOST_STARTUP_CODEX_EXEC_INTENT_WARMUP_ENABLED=false`，Host 仍会执行 app-server/thread 预热，避免把通用热连接误关掉。

预期效果：

- 用户首轮不再承担 app-server 进程启动成本。
- 常用后台 feature 首轮不再承担 `thread/start` 成本。
- EXEC_INTENT 仍保留一次真实模型 warmup，继续预热模型调用链路。

启动后实测：

- Host restart elapsed：约 `13662ms`
- Host PID：`45800`
- `prewarm_status=OK`
- `prewarm_latency_ms=665`
- `prewarm_features=7`
- `prewarm_threads=7`
- `prewarm_workers=1`
- `prewarm_pid=46760`
- Host 子进程中仅有一组 app-server：`cmd.exe -> node.exe -> codex.exe`
- `/ready` 返回 `READY`

### 后续架构建议

WorkOrder runtime 另需单独处理：

- inspect/research/audit/translate 这类只读 WorkOrder 可迁移到 app-server。
- worker/self_heal/self_evolve 这类写能力 WorkOrder 暂时保留 `codex exec`，或另做支持工具调用/文件写入的 app-server 安全协议。

## Host 与 Edge 双向连通

### Host 本地健康

- `GET http://127.0.0.1:18080/health?skip_dependency_probes=1`
- 状态：`200`
- summary：`overall_status=UP`
- required_total：`12`
- required_unavailable：`0`

### Host ready

- `GET http://127.0.0.1:18080/ready`
- 状态：`200`
- status：`READY`
- node_count：`2`

### Host 到 Edge RPC

- `GET http://192.168.1.101:17800/v1/edge/health`
- 状态：`200`
- status：`ACCEPTED`
- Edge RPC gateway、ASR、TTS、resource gate 均返回可用信号。

### Host 到 Edge Gateway

- `GET http://192.168.1.101:19000/v1/gateway/health`
- 状态：`200`
- status：`ok`

### Edge 回连 Host

命令：

```powershell
ssh edge-lan 'hostname && curl -sS -m 8 -w "\nHTTP_STATUS:%{http_code}\n" "http://192.168.1.188:18080/health?skip_dependency_probes=1"'
```

结果：

- Edge hostname：`Strix`
- HTTP_STATUS：`200`
- Host health summary：`overall_status=UP`
- required_total：`12`
- required_unavailable：`0`

判定：Host ↔ Edge 双向连通通过。

## 后续建议

1. 为 Host 后台任务增加一个专门的 Codex app-server acceptance 脚本，直接测 Host 任务调度路径，而不是只测裸 `codex exec`。
2. 把主链验收拆成三类固定脚本：`memory_guard_no_hallucination`、`deterministic_renderer`、`llm_token_accounting`。
3. 将 Edge 双向连通命令固化到 runtime checklist，避免误用 `edge-lan` 作为 HTTP 主机名。
