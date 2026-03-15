# F-11 情感值驱动 VTS 表情（历史 bootstrap 排障记录）

- 执行批次：`20260314_batch02_voice_preflight`
- 调用：`talk_start`
- 请求 ID：`req_sealtest_vts_probe`
- 结论：`BLOCK`

## 运行结果

1. 通过真实 Edge RPC 调用 `post_vts(action='talk_start')`。
2. Edge 返回：
   - `status=REJECTED`
   - `reason_code=VTS_CONNECTION_FAILED`
   - `detail=cannot start talk: VTS connection error: [WinError 1225] 远程计算机拒绝网络连接。`
3. 当前没有桌面侧 VTS 窗口、表情切换或口型变化可观察，因此白盒观测不能开始。

## 判定

`F-11` 当前不是“表情映射错误”，而是前置连接就失败。VTS API 端口未建立、桌面白盒不可见，故本项直接 `BLOCK`。

## 证据

- `70_raw_evidence/F-11_vts_probe_failure_20260314.json`
- `runtime/edge/logs/sealtest_edge.stderr.log`
