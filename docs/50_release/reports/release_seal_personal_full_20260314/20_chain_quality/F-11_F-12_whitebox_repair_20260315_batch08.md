# F-11 / F-12 白盒修复记录（batch08）

- 时间：2026-03-15
- 目标：修复 Edge 节点白盒语音不可听、VTS 可视证据链缺口。

## 已确认根因

- Edge 节点默认 `sounddevice` 输出设备不是物理扬声器，而是 `VB-Audio Virtual Cable`。
- 远端 `sounddevice` 默认设备快照为 `default=[1, 6]`，其中输出 `index=6` 为 `扬声器 (2- VB-Audio Virtual Cable)`。
- 这会导致系统侧 `TTS_PLAYBACK_STARTED` 成功，但用户桌面侧不一定能真实听到声音。
- Edge 端 `voice_pipeline` 之前没有把 `TTS_PLAYBACK_STARTED / TTS_PLAYBACK_FINISHED` 上报回 Host，导致 Host 侧白盒证据链缺口。
- Host 侧 `edge_rpc_client` 遇到 `EDGE_RPC_AUTH_EXPIRED` 时没有主动清时钟偏移缓存，Edge 重启后首条 TTS 请求容易因旧偏移失败。

## 已执行修复

- 本地代码已修复：
  - `host_brain/channels/edge_rpc_client.py`
  - `edge_client/runtime/voice_pipeline.py`
- 远端运行副本已同步：
  - 将本地 `edge_client/runtime/voice_pipeline.py` 同步到远端 `D:\agent\edge_client\runtime\voice_pipeline.py`
- 远端 Edge 配置已调整：
  - `D:\agent\.env` 写入 `EDGE_TTS_PLAYBACK_DEVICE_INDEX=8`
  - `index=8` 对应 `扬声器 (Realtek(R) Audio)`
- 远端 Edge 已重启：
  - 新进程 PID：`9532`

## 已验证结果

- 远端物理扬声器直连测试音已执行成功：
  - 在 Edge 节点上直接使用 `sounddevice` 向 `device=8` 播放 2 秒 440Hz 测试音
  - 返回结果：`beep-ok`
- 这证明：
  - 物理输出设备已从虚拟声卡切到真实扬声器
  - Edge 节点当前具备“真实可听”的最小播放条件

## 当前剩余问题

- 正式双节点 `Host -> Edge` 的 HTTP 连通性仍不稳定：
  - 本机直接访问 `http://192.168.1.101:17800` 出现 timeout
  - WinRM 远控会话内访问 `127.0.0.1:17800` / `192.168.1.101:17800` 也出现拒绝或超时
- 因此本批次还不能把 `F-11 / F-12` 判为 `PASS`：
  - 还缺用户桌面侧对白盒语音“真实听到”的确认
  - 还缺用户桌面侧对白盒 VTS 表情/口型“真实看到”的确认
  - 还缺修复后 `TTS_PLAYBACK_STARTED / FINISHED` 回传到 Host 的正式双节点证据

## 当前结论

- `已修复`：Edge 白盒音频输出设备选错问题。
- `已修复`：Host 时钟重校补偿缺口。
- `已修复并部署`：Edge 播放开始/结束事件回传逻辑。
- `未闭环`：正式双节点网络路径与最终桌面白盒确认。
