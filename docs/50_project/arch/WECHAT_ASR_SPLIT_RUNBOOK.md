# WeChat / ASR 分流运行清单

## 1) 关键配置（Host）

以下配置用于“微信只直回、ASR不触发微信回复”：

```env
HOST_INPUT_WECHAT_AUTO_REPLY=true
HOST_INPUT_WECHAT_DIRECT_REPLY_ONLY=true
HOST_INPUT_WECHAT_DIRECT_REPLY_TEXT=收到
HOST_INPUT_WECHAT_BLOCK_FILEHELPER=true
HOST_INPUT_WECHAT_SUPPRESS_EDGE_TTS=true
HOST_INPUT_WECHAT_SUPPRESS_EDGE_VTS=true
HOST_INPUT_WECHAT_REPLY_WAIT_RESULT=true
HOST_INPUT_WECHAT_REPLY_TIMEOUT_MS=12000
```

> 已在 `scripts/runtime/start_host_side.ps1` 内置同名默认值兜底。

## 2) 启动顺序

1. 在 Edge 机启动 Edge：`scripts/runtime/start_edge.ps1`
2. 在 Host 机启动 Host：`scripts/runtime/start_host_side.ps1`
3. 确认 Host API 存活：`GET http://127.0.0.1:18080/health`

## 3) 输入链路 Smoke（手工）

### WECHAT 输入

```powershell
$wechatBody = @{
  source   = "WECHAT"
  from_wxid = "wxid_user_a"
  to_wxid   = "wxid_bot"
  content   = "测试微信消息"
  msg_type  = "text"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://127.0.0.1:18080/v1/host/input" `
  -Method Post `
  -ContentType "application/json" `
  -Body $wechatBody
```

预期：Host 返回 `ACCEPTED`，并触发 WeChat 命令发送（直回文本）。

### ASR 输入

```powershell
$asrBody = @{
  source     = "ASR"
  session_id = "asr_session_1"
  text       = "测试语音输入"
  confidence = 0.95
  is_final   = $true
  seq        = 1
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://127.0.0.1:18080/v1/host/input" `
  -Method Post `
  -ContentType "application/json" `
  -Body $asrBody
```

预期：Host 返回 `ACCEPTED`，不触发任何 WeChat 回复动作。

## 4) 观测点

- Host 日志包含：`run_main_chain_from_input: source=WECHAT` / `source=ASR`
- WeChat 直回场景包含：`wechat direct reply sent`
- ASR 场景不应出现：`wechat auto reply sent` / `wechat direct reply sent`

## 5) 回滚

若需要恢复“微信走完整链路再回发”：

```env
HOST_INPUT_WECHAT_DIRECT_REPLY_ONLY=false
```

