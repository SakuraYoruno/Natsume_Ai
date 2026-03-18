受众：MASTER
标题：VTS 语音 / 表情 / 口型排查（代码对齐版）

版本：v1.1
日期：2026-03-18
适用分支：当前仓库 `codex`

# 🎭 VTS 语音 / 表情 / 口型排查（代码对齐版）

## 0. 快速定位

- 🧠 Host 表情解析：`host_brain/channels/vts.py::resolve_vts_expression()`
- 🎙️ Edge 驱动入口：`edge_client/actuators/vts_driver.py::VTubeStudioDriver`
- 🔊 语音执行链路：`edge_client/runtime/voice_pipeline.py`

## 1. 📌 当前代码口径（先看这里）

- Host 侧表情解析入口：`host_brain/channels/vts.py::resolve_vts_expression()`
- Edge 侧驱动入口：`edge_client/actuators/vts_driver.py::VTubeStudioDriver`
- Edge 语音链路入口：`edge_client/runtime/voice_pipeline.py`
- 当前主映射来源：Host/Edge 两侧代码常量 `EMOTION_TO_EXPRESSION_ID`（16 标签对齐）
- 旧文件 `host_brain/channels/vts_runtime_mapping.py` 目前不在主调用路径

## 2. ✅ 行为基线（与代码一致）

- Host 在 `edge_available=False` 时返回 `status=DEGRADED`，并降级到 neutral 映射（可审计）。
- Host 遇到未知 `emotion_tag` 时同样走 `DEGRADED + neutral`，而不是直接中断整链路。
- Edge 驱动主能力是 `talk_start` / `talk_end` / `set_expression` / `trigger_hotkey`。
- Edge 口型参数由驱动内部直接写入 `MouthOpen`，不再依赖外部 `set_mouth_open_level` 调用。

## 3. 🧪 常见故障定位

| 现象 | 主要原因 | 快速检查 |
|---|---|---|
| 有语音，无表情 | `emotion_tag` 未下发/未命中映射 | 看 Host `resolve_vts_expression` 入参和输出 |
| 有语音，无表情 | Edge 未连接 VTS | 看 Edge 驱动状态 `AUTHENTICATED` 与连接错误 |
| 表情总是 neutral | 触发了 Host 降级路径 | 查 `status=DEGRADED`、`reason_code`、`expression_id` |
| 有语音，无口型 | 语音事件缺少可用音频数据 | 看 `voice_pipeline` 的 synth/playback 事件内容 |
| sub_emotion 无效 | 热键 ID 未配置或不存在 | 看 `trigger_hotkey` 调用参数与 VTS 侧热键配置 |

## 4. 🪜 建议的最小排查顺序

1. 先看 Host：`resolve_vts_expression` 是否输出预期 `expression_id`。  
2. 再看 Edge：驱动状态是否为 `AUTHENTICATED`。  
3. 看语音事件：是否有可用于口型的音频 payload。  
4. 看热键配置：`trigger_hotkey` 的 ID 是否与 VTS 模型实际热键一致。  

## 5. 🧾 审计与日志关键点

- Host 审计：关注 `avatar_action_log` 中 `status/reason_code/stage/detail`。  
- Edge 日志：关注 VTS 连接、表达式触发、talk gate 开合、重连事件。  
- 重连机制：`VTubeStudioDriver` 支持指数退避自动重连，重连成功后会恢复 neutral。  

## 6. 🔒 维护约束

- 文档提到的表情映射，必须以以下两处代码常量为准：  
  - `host_brain/channels/vts.py`  
  - `edge_client/actuators/vts_driver.py`
- 新增/删除情感标签时，必须同步更新：
  - Host 映射常量
  - Edge 映射常量
  - `tests/TESTPLAN.md` 的 T-Avatar 条目
  - `tests/fixtures/README.md` 的 channels 说明
