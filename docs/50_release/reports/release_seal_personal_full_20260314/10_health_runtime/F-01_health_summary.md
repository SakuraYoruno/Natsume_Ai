# F-01 Host/Edge/VTS 健康

- 执行批次：`20260314_batch01_health_gate`
- 执行时间：`2026-03-14 23:47 CST`
- 结论：`BLOCK`

## 观察结果

1. `host` 主健康端点可达：`/health`、`/ready`、`/tools/health`、`/bond/health`、`/scheduler/health`、`/host-scheduler/health` 均返回 `200`。
2. `edge` 本机 RPC 已打通，`/v1/edge/health` 返回 `200`，但 `event.status=degraded`。
3. `edge` 明确阻断点：
   - `tts.status=degraded`
   - `tts.health.state=UNLOADED`
   - `vts.health.state=DISCONNECTED`
4. `host-scheduler/health` 返回 `running=false`，当前不满足“已启用组件全健康、无未解释降级”的放行标准。

## 判定

按计划 `F-01` 的阈值，已启用组件不允许出现未解释 `DEGRADED/503`。当前 `edge` 语音侧仍处于降级态，且 VTS 未建立真实连接，因此 `F-01` 判定为 `BLOCK`。

## 证据

- `10_health_runtime/F-01_health_snapshot.json`
- `runtime/edge/logs/sealtest_edge.stderr.log`
- `runtime/host/logs/sealtest_host.stdout.log`
