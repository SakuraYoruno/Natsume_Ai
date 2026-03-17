# VTS 语音 / 表情 / 口型 不生效排查

当「只有语音，表情和口型都没变」或「表情和口型都没有变化」时，按下面顺序排查。

## 0. 表情和口型都没有变化（两者都不动）

- **请求里没有带 emotion_tag**  
  - 默认会使用 NEUTRAL。且 NEUTRAL 在 Edge 侧可能只做 `reset_to_neutral()`，若被判定为「全局重置」热键会**不触发热键**（避免覆盖用户手动表情），所以画面上表情不会变。  
  - **处理**：请求体里带上 `emotion_tag`（如 `HAPPY`、`SAD`），或依赖 Host 的「根据回复文案推断情感」：当当前为 NEUTRAL 时，会用回复中的关键词（哈哈/开心、难过/哭、谢谢/感动等）推断一个 tag 再下发给 Edge，从而驱动表情。

- **口型需要 Edge 有音频**  
  - 口型依赖 `synth_event.audio_data`。若 Edge 未配置本地 TTS（如 SoVITS）或 synthesizer 不返回 `audio_data`，口型不会动。  
  - **处理**：在 Edge 启用 SoVITS 等能产出 `audio_data` 的 TTS，或由 Host 下发完整音频并写入 Edge 的 synth_event。

- **Edge 日志确认**  
  - 在 `runtime/edge/logs/edge.stdout.log` 中可看到：  
    - `VTS start: emotion_tag=XXX synth_has_audio=True/False`：当前请求用的表情 tag 以及是否有口型用音频。  
    - `VTS expression: emotion_tag=XXX -> hotkey_id=YYY`：实际触发的 VTS 热键。  
    - `mouth sync started: envelope_points=N param_names=[...]`：口型包络点数与使用的 VTS 参数名。  
  - 若 `param_names` 为空或为 `(fallback)`，且模型嘴部仍不动，需在环境变量中配置 `EDGE_VTS_MOUTH_PARAM_CANDIDATES` 为当前 VTS 模型实际使用的嘴部参数名。

## 1. 表情（Expression）不随情绪变化

- **Host 是否下发 emotion_tag**  
  - 流式聊天会通过 `post_speak(..., emotion_tag=_etag)` 传给 Edge，`_etag` 来自 `emotion_context.emotion_tag`（默认 NEUTRAL）。  
  - 若始终为 NEUTRAL，只会执行 `reset_to_neutral()`，表情不会切换。

- **Edge → VTS 映射**  
  - 表情由 `host_brain/channels/vts_runtime_mapping.py` 或 `EMOTION_TO_EXPRESSION_ID` 映射到 VTS 热键/表情 ID。  
  - 若 emotion_tag 不在映射表，或当前 VTS 模型没有对应热键，`set_expression` 会报 `unsupported emotion_tag` 并降级。

- **VTS 是否已连接**  
  - Edge 启动时会连 VTube Studio（默认 `localhost:8001`）。  
  - 未连接时只会“排队”表情，不会真正驱动 VTS。查看 Edge 日志是否有 `VTS connection failed` 或 `expression queued (VTS not connected)`。

**建议**：确认 `emotion_context` 中有非 NEUTRAL 的 `emotion_tag`，且该 tag 在 VTS 映射表与当前模型中存在对应热键。

## 2. 口型（Mouth / Lip Sync）不动

口型依赖「当前播放句子的音频」做包络，驱动 VTS 的嘴部参数。

- **Edge 是否有 `audio_data`**  
  - 口型在 Edge 的 `_start_mouth_sync(synth_event)` 里用 `synth_event["audio_data"]` 和 `sample_rate` 算包络。  
  - 若 Edge 未配置本地 TTS（如 SoVITS），`_synthesizer` 可能是默认实现，只返回 `audio_ref`，**没有 `audio_data`**，则不会做口型同步。  
  - 日志中会出现：`VTS mouth sync skipped: no synth_event` 或 `empty_envelope (synth_event has no audio_data or sample_rate)`。

**结论**：  
- 若希望口型随语音动，需要 **Edge 侧有能产出 `audio_data` 的 TTS**（例如启用 SoVITS），或由 Host 下发完整音频并在 Edge 填入 `synth_event.audio_data`。  
- 仅 Host 发 `post_speak(text=...)`、Edge 用默认 synthesizer 时，通常只有语音（若另有播放路径）而没有口型。

- **VTS 嘴部参数名**  
  - Edge 会向 VTS 的 Input Parameter 发值（如 MouthOpen、ParamMouthOpenY 等）。  
  - 若当前 VTS 模型使用的参数名不同，可通过环境变量 `EDGE_VTS_MOUTH_PARAM_CANDIDATES`、`EDGE_VTS_MOUTH_VOLUME_PARAM_CANDIDATES` 等配置为当前模型实际参数名。

## 3. 快速核对清单

| 现象           | 可能原因                     | 处理方向 |
|----------------|------------------------------|----------|
| 有语音无表情   | emotion_tag 始终 NEUTRAL     | 检查 Host emotion_context / 情绪推理 |
| 有语音无表情   | emotion_tag 不在 VTS 映射表  | 配置 vts_runtime_mapping 或模型热键 |
| 有语音无表情   | VTS 未连接                   | 确认 VTube Studio 已开、端口 8001、Edge 连接成功 |
| 有语音无口型   | synth_event 无 audio_data    | Edge 启用 SoVITS 或提供带 audio_data 的 TTS 输出 |
| 有语音无口型   | VTS 嘴部参数名不匹配         | 设置 EDGE_VTS_MOUTH_PARAM_CANDIDATES 等 |

## 4. 相关代码位置

- Host 下发 TTS + emotion：`host_brain/app/chat_stream_handler.py`（`_run_tts`、`post_speak(emotion_tag=..., emotion_intensity=...)`）。
- Edge 收 speak、驱动 VTS：`edge_client/app/fastapi_app.py`（`_handle_speak`）→ `edge_client/runtime/voice_pipeline.py`（`enqueue_speak` → `_start_vts_for_payload`）。
- 口型包络与线程：`voice_pipeline._start_mouth_sync`、`_build_mouth_envelope`；表情与嘴开合：`edge_client/actuators/vts_driver.py`（`set_expression`、`talk_start`、`set_mouth_open_level`）。
- 表情映射：`host_brain/channels/vts_runtime_mapping.py`、`edge_client/actuators/vts_driver.py` 中 `EMOTION_TO_EXPRESSION_ID`。
