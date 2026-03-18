受众：MASTER
标题：Chat 发布回滚运行手册（2026-03-10）

# Chat 发布回滚运行手册（2026-03-10）

## 1. 发布门禁（先判定）

基于周稳定性报告执行发布门禁：

```powershell
python tools/release_rollback_gate.py `
  --stability-report artifacts/trace/WEEKLY_STABILITY_REPORT_20260310_005912.json `
  --max-alert-count 0 `
  --max-timeout-rate-pct 10 `
  --max-degraded-rate-pct 20 `
  --max-failure-rate-pct 15
```

判定结果：
- `decision=PASS`：允许继续发布
- `decision=ROLLBACK_REQUIRED`：立即执行第 2 节回滚命令

## 2. 回滚命令（PowerShell）

```powershell
$env:HOST_CHAT_FORCE_CLOUD='true'
$env:HOST_CHAT_HOST_8B_ENABLED='false'
$env:HOST_GAMING_EDGE_CHAT_ENABLED='false'
$env:HOST_CHAT_GRAY_ENABLED='false'
$env:HOST_CHAT_GRAY_PERCENT='100'
```

## 3. 回滚后验证

1. 调用 `/obs/metrics`，确认 `governor/retrieval` 失败率下降。
2. 调用 `/v1/chat` 与 `/v1/chat/stream`，确认路由原因为 `HOST_CHAT_FORCE_CLOUD` 或 Cloud 路径原因码。
3. 重新运行门禁脚本，确认 `decision=PASS` 后再考虑恢复灰度。
