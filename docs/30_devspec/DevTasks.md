受众：PROJECT
标题：Natsume AI 当前任务清单

# 当前任务清单

本清单只记录从当前代码到“七海实时情感数字生命可真实验收”的剩余工作，不重复历史阶段流水账。

## 当前收口

- [x] 分离用户情绪观察与七海 self affect。
- [x] 建立情感 episode、关系后效应、profile 版本和兼容写入计量。
- [x] 普通聊天恢复轻量实时路径，外部副作用保留 Governed Runtime。
- [x] 选择 GPT-SoVITS V4 方案 B，并完成本机 CUDA 生成样例。
- [ ] 将本分支压缩合入 `dev`，清理临时分支和无关仓库实验文件。
- [ ] 在合并后的 `dev` 上确认文档、机器状态与定向测试一致。

## P0：目标 Edge 三模态验收

- [ ] 将已选择的声线配置、参考音频和所需权重同步到 Edge 电脑的非仓库模型目录。
- [ ] 在 Edge 上验证中文 ASR → 日语 TTS 的完整语言链路。
- [ ] 实测普通、开心、安慰、委屈、边界、修复、安静陪伴等表达计划。
- [ ] 接通 VTS/VRM，确认与 TTS 共用 `synchronization_id`。
- [ ] 实测播放中打断、TTS 断线、Avatar 断线和 Host 重连。
- [ ] 测量首段 TTS、barge-in 和 Voice／Avatar 起始偏差。

约束：Edge 在另一台电脑上，用户未明确允许前不得启动或远程操作。

## P0：真实对话体验

- [ ] 使用至少一个真实非 mock provider 跑固定 72 条场景。
- [ ] 修复真实 provider 首字延迟和异常回退，不以缓存数据替代。
- [ ] 从 72 条场景中冻结 48 条多轮盲测集。
- [ ] 邀请至少 3 名评审，与稳定基线盲测并达到 PRD 门槛。
- [ ] 重点检查情绪变化幅度、混合情绪、裂痕／修复和技术任务中的人格连续性。

## P1：完整本地服务栈

- [ ] 以隔离 acceptance 数据根启动 Neo4j、Chroma、Redis、Host、OBS、Monitor 和 Gateway。
- [ ] 对三项 Docker 服务完成真实写入、读取、重启恢复和清理 namespace。
- [ ] 验证 Monitor 展示真实 task、budget、verification、acceptance、effect 和 receipt 数据。
- [ ] 验证 Gateway 重复入站、Outbox 重放和网络恢复不会重复外发。
- [ ] 注入 Host 崩溃、Lease 过期、未知副作用和回滚失败。

## P1：情感生命清零项

- [ ] 在真实运行中观察兼容情感 writer 使用量，确认旧 writer 为 0。
- [ ] 验证 profile apply → canary → rollback 会改变并恢复下一轮文本、TTS 和 Avatar。
- [ ] 验证长期运行状态有界，原始音视频和用户正文不会进入 affect journal。
- [ ] 验证跨用户、workspace、session 泄漏为 0。

## 完成定义

任务只有同时满足“生产调用点、真实环境、恢复／打断、体验指标、文档同步”才可勾选。静态合同、fixture、scripted replay 或单次样例不得替代目标环境验收。

最终发布状态仍为 `INCOMPLETE`，直到 [执行合同](NEXT_GENERATION_EVOLUTION_EXECUTION.md) 的退出条件全部满足。
