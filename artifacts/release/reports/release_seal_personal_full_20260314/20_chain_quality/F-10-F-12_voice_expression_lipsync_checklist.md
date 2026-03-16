# F-10～F-12 封测执行清单：情感、表情、口型与语音

**范围**：情感信号全链路、情感驱动 VTS 表情、TTS→VTS 口型同步、语音可听。  
**环境**：局域网双节点（Host 一台 + Edge/VTS 一台），白盒观测在 Edge 电脑侧。

---

## 一、前置条件

- [ ] Host 节点已启动，`/health` 200，`/ready` 可 DEGRADED 但无未解释 503。
- [ ] Edge 节点已启动，`/v1/edge/health` 200，VTS 状态为 `AUTHENTICATED`。
- [ ] 两台机器 LAN 互通，Host 能访问 `EDGE_BASE_URL`（如 `http://<Edge_IP>:17800`）。
- [ ] Edge 电脑扬声器可听、VTS 窗口可见（不最小化、不隐藏）。

---

## 二、语音（TTS）— 必须能听到

| 步骤 | 操作 | 预期（Edge 电脑侧） | 证据 |
|------|------|---------------------|------|
| 1 | 在 Host 侧发起一条完整对话（或 Panel 发消息），触发一次回复 | Edge 电脑**实际听到**该句日文语音 | 记录：听到 / 未听到；若未听到记 Host/Edge 日志片段 |
| 2 | 查看 Edge 响应：`/v1/edge/speak` 返回的 `event` 中是否有 `vts_talk_result.success=true`、`vts_mouth_sync.state` | 有成功状态且与“听到”一致 | 截图或保存 JSON 片段到 `20_chain_quality/` |

**不通过**：若日志显示成功但 Edge 电脑侧听不到 → 按封测原则判 F-10/F-12 不通过，需查 TTS 播放与 Edge 音频输出。

---

## 三、表情（VTS 情感驱动）— 必须能看到变化

**链路**：Host 流式输出 → `emotion_context.emotion_tag` → `post_speak(..., emotion_tag=...)` → Edge `enqueue_speak(payload)` → `_start_vts_for_payload` 中 `set_expression(emotion_tag)` → VTS 热键。

| 步骤 | 操作 | 预期（Edge 电脑侧） | 证据 |
|------|------|---------------------|------|
| 1 | 用**不同情绪**的 query 触发多轮回复（如开心一句、难过一句、惊讶一句） | 每轮在 VTS 窗口内**可见表情切换**（非始终同一表情） | 录屏或截图：不同回合对应不同表情 |
| 2 | 在 Host 日志或 Edge 返回中确认每轮 `emotion_tag`（如 HAPPY / SAD / SURPRISED） | 与 VTS 热键映射一致（见 `configs/vts/vts_hotkey_config.yaml` 的 `emotion_mapping`） | 保存 1～2 条 `event.vts_expression_result` 或 Host 侧 post_speak 的 payload |
| 3 | 若始终为同一表情（如一直 happy） | 排查：① Host 是否每轮都下发不同 `emotion_tag`；② Edge 是否收到并写入 payload；③ VTS 热键 ID 与模型内热键是否一致 | 记录排查结论到本目录 |

**常见原因「表情一直是 happy」**：

- Host 侧 `emotion_context.emotion_tag` 多为 NEUTRAL/HAPPY（LLM 或默认未变）。
- NEUTRAL 映射为「移除所有按键表情」，若 VTS 模型默认脸是“笑”，则看起来像一直 happy。
- 建议：用下方「单独 speak 诊断」对 Edge 直接 POST 不同 `emotion_tag`，确认 VTS 是否按标签切换，再回查 Host 下发的 tag。

---

## 四、口型同步（TTS 与 VTS 嘴型）— 偏差 ≤ 200ms

| 步骤 | 操作 | 预期（Edge 电脑侧） | 证据 |
|------|------|---------------------|------|
| 1 | 触发一句 3～8 秒的回复，同时看 VTS 嘴型与听语音 | 嘴型开合与**语音起止**大致同步（封测阈值：起播偏差 ≤200ms，结束偏差 ≤200ms） | 录屏 + 结论：通过 / 未通过；若未通过记录“提前/滞后/不闭合” |
| 2 | 查看 Edge 返回的 `vts_mouth_sync`：`state`、`envelope_points`、`frames_sent`、`reason` | `state` 为 `running` 或 `finished`，无 `skipped`/`empty_envelope` | 保存一条完整 speak 响应的 `vts_mouth_sync` 到本目录 |

**可能原因「口型不同步」**（代码层面）：

- Edge 侧口型线程在 **TTS 播放开始之后** 才启动（`voice_pipeline`: 先 `EVENT_TTS_PLAYBACK_STARTED`，再 `_start_vts_for_payload` → `talk_start` → `_start_mouth_sync`），存在约几百 ms 延迟，嘴型会**滞后**于声音。
- 口型按固定帧率（约 24fps）推 envelope，若音频时长与 envelope 长度×帧间隔不一致，尾部可能对不齐。
- 若 VTS 内使用「根据音量/静音」的嘴型，且与我们的 `MouthOpen`/`VoiceVolumePlusMouthOpen` 同时作用，可能打架，需在 VTS 中只保留一套控制。

---

## 五、单独 speak 诊断（不经过完整 Chat）

在 **Host 节点** 或能访问 Edge 的机器上，用 curl 直接调 Edge，可单独验证「不同 emotion_tag 是否驱动表情」「TTS 是否出声」「口型是否有 envelope」。

```bash
# 将 <EDGE_IP> 换为 Edge 节点 IP，端口默认 17800

# 1) 固定 HAPPY
curl -s -X POST "http://<EDGE_IP>:17800/v1/edge/speak" \
  -H "Content-Type: application/json" \
  -d '{"text":"今日はいい天気ですね。","session_id":"seal-test-1","utterance_id":"utt-1","emotion_tag":"HAPPY","speed":1.0,"pitch":0}'

# 2) 固定 SAD
curl -s -X POST "http://<EDGE_IP>:17800/v1/edge/speak" \
  -H "Content-Type: application/json" \
  -d '{"text":"ちょっと悲しいです。","session_id":"seal-test-2","utterance_id":"utt-2","emotion_tag":"SAD","speed":1.0,"pitch":0}'

# 3) NEUTRAL
curl -s -X POST "http://<EDGE_IP>:17800/v1/edge/speak" \
  -H "Content-Type: application/json" \
  -d '{"text":"了解しました。","session_id":"seal-test-3","utterance_id":"utt-3","emotion_tag":"NEUTRAL","speed":1.0,"pitch":0}'
```

**观测**：在 Edge 电脑上听是否出声，看 VTS 表情是否在 HAPPY / SAD / NEUTRAL 间切换。响应体中的 `vts_expression_result`、`vts_talk_result`、`vts_mouth_sync` 可作为证据保存。

---

## 六、证据归档

- 每条结论写在本目录下的 `F-10_F-12_<日期>_<结论>.md` 或同名 JSON 片段。
- 录屏/截图建议放在 `70_raw_evidence/` 或本目录，文件名带 `hostnode`/`edgenode`/`edgepc` 区分。
- 最终 F-10/F-11/F-12 的通过/阻断结论写在总索引 `00_index/`。

---

## 七、与封测计划对应关系

| 本清单 | 封测计划 ID | 内容 |
|--------|-------------|------|
| 二 | F-10 / F-12 | 情感全链路 + 语音可听 |
| 三 | F-11 | 情感值驱动 VTS 表情，可见切换 |
| 四 | F-12 | TTS→VTS 口型同步，偏差 ≤200ms |
| 五 | 诊断 | 不替代正式封测，仅用于定位表情/口型问题 |
