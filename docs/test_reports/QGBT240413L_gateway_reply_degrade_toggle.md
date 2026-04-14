# QGBT240413L Gateway 回包失败降级开关验证

日期：2026-04-13

## 背景

- 当前联调阶段 Edge 不稳定/离线时，`GATEWAY_REPLY_FAILED` 会把整条输入标记为 `INPUT_COMPLETED/DEGRADED`，污染 Host 技能链路验收。
- 新增开关：`HOST_GATEWAY_REPLY_FAILURE_DEGRADE_INPUT`（默认 `true`）。
  - `true`：保持原行为（回包失败会降级整条输入）。
  - `false`：仅记录 gateway 失败明细，不降级主流程。

## 代码变更

- `host_brain/app/input_routes.py`
  - 在 `_dispatch_to_graph_worker` 的完成态构建逻辑中引入开关判断。
  - 当 `HOST_GATEWAY_REPLY_FAILURE_DEGRADE_INPUT=false` 且 gateway 回包失败时：
    - `completion_status` 维持 `OK`
    - 细节中追加 `gateway_failure_ignored=true`
    - 仍保留 `gateway_status/gateway_latency_ms/reply_preview` 等观测字段
- `.env.example`
  - 增加开关注释说明。
- `tests/unit/test_input_routes_worker.py`
  - 新增用例：`test_dispatch_worker_gateway_reply_failure_can_be_ignored_for_completion_status`

## 单测结果

命令：

```powershell
pytest tests/unit/test_input_routes_worker.py -q
```

结果：`24 passed`

## 运行时冒烟（真实 Host）

- 环境：`.env` 设置 `HOST_GATEWAY_REPLY_FAILURE_DEGRADE_INPUT=false`，重启 host。
- 冒烟 request_id：`req_c21b0cbcbc0446bbab65360a774fef8c`

关键事件：

1. `INPUT_ACCEPTED`
2. `INPUT_DISPATCHED`
3. `INPUT_COMPLETED / OK`（CHAT_API）
4. `INPUT_ROUTE_DECIDED / OK`
5. `INPUT_COMPLETED / OK`（cortex_orchestrator 最终态，含 `gateway_failure_ignored=true`，`gateway_status=failed`）

结论：

- 开关生效：Gateway 回包失败不再把主流程判为 `DEGRADED`。
- 观测信息完整保留，可继续用于排查 Gateway 故障。
