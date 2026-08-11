受众：PROJECT
标题：目标 Edge 的 Voice 与 VTS 排障

# 目标 Edge 的 Voice 与 VTS 排障

## 顺序

1. 确认用户已允许启动远端 Edge。
2. 检查 Edge health、GPU、模型路径和 voice bank digest。
3. 单独合成一条 GPT-SoVITS V4 日语语音。
4. 单独触发一个 VTS/VRM 表情或动作。
5. 再连接 Host，核对 `synchronization_id` 与 physical receipt。

## 常见问题

| 现象 | 检查 |
|---|---|
| 炸音／失真 | 参考音频削波、采样率、响度、显存与推理精度 |
| 棒读 | 参考片段情绪、文本停顿、profile 匹配和 prosody 参数 |
| 中文猎奇口音 | 确认 speech language 为 `ja`，不要让日语模型直接合成中文 |
| 第二组声线异常 | 对比原始参考、切分边界、噪声和 transcript |
| VTS 无动作 | WebSocket 权限、hotkey 名称、模型加载与 receipt |
| `1001 going away` 后错误持续增长 | 确认 Edge 已识别连接关闭并进入有界 `RECONNECTING`；动作线程不得逐帧重试，VTS 恢复后无需重启 Edge |
| VTS 晚于 Edge 启动 | health 应先显示 `waiting_for_vts／RECONNECTING`，VTS 8001 就绪后自动恢复 `AUTHENTICATED` |
| Voice 与 Avatar 不同步 | synchronization id、队列时间戳和启动偏差 |
| 打断后仍播放 | barge-in cancel、已排队 chunk 和旧 session fence |

## 边界

Host outbox、RPC 200 或端口可达都不等于物理播放成功。只有 Edge 返回与当前 session/fence 绑定的 receipt，Monitor 才能显示通过。

模型权重、参考音频、生成样例、设备日志和密钥不得提交仓库。
