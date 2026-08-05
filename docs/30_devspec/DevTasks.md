受众：PROJECT
标题：Natsume AI 当前任务清单

# 当前任务清单

本清单只记录从当前代码到“七海实时情感数字生命可真实验收”的剩余工作，不重复历史阶段流水账。

## 当前收口

- [x] 分离用户情绪观察与七海 self affect。
- [x] 建立情感 episode、关系后效应、profile 版本和兼容写入计量。
- [x] 普通聊天恢复轻量实时路径，外部副作用保留 Governed Runtime。
- [x] 选择 GPT-SoVITS V4 参考条件方案 B，并完成本机 CUDA 生成样例与人工试听。
- [x] 将数字生命主链合入 `dev`，清理本地与远端临时分支。
- [x] 通过 PR #209 重整当前文档、固定证据和历史归档，并在合并后的 `dev` 上复核文档 Gate。

当前声线仍使用预训练权重和参考音频，不是七海专属微调 checkpoint；下一步从目标 Edge 的真实语音与 Avatar 接线开始。

## P0：目标 Edge 三模态验收

- [ ] 将已选择的声线配置、参考音频和所需权重同步到 Edge 电脑的非仓库模型目录。
- [ ] 在 Edge 上验证中文 ASR → 日语 TTS 的完整语言链路。
- [ ] 实测普通、开心、安慰、委屈、边界、修复、安静陪伴等表达计划。
- [ ] 接通 VTS/VRM，确认与 TTS 共用 `synchronization_id`。
- [ ] 实测播放中打断、TTS 断线、Avatar 断线和 Host 重连。
- [ ] 测量首段 TTS、barge-in 和 Voice／Avatar 起始偏差。

约束：Edge 在另一台电脑上。本轮双端验收已经用户明确授权；后续新一轮远程启动或可能影响设备的操作仍需遵守当轮授权边界。

## 2026-08-04 已完成的双端观测切片

- [x] Life Body 从 canonical `hard_state.emotion_tag` 派生人格上下文，并在真实 OBS／Nginx Monitor 浏览器中消除“待连接／待恢复”。
- [x] Gateway 完成事件公开最小 Reply Outbox commit marker；回复正文与私有字段不进入观测投影。
- [x] 用真实签名 Gateway 分别跑通翻译 Delegate、Memory Store 与多 Skill Plan；最新有效桶的 Reply Outbox、Closed Loop、Completion 均为 100%。
- [x] 保留 360 分钟历史失败，不降低阈值，不把 Outbox commit 标作外部平台 ACK。
- [x] Monitor read-model 27 项、生产构建、真实后端主链与核心交互浏览器用例通过。

本切片不关闭 VTS 情绪热键、barge-in、常驻 ASR 噪声、PlanningSearch 收益、人类盲测或最终 SHA 故障矩阵。

## 2026-08-05 Architecture 2.0 当前 SHA 前置门禁

- [x] 将 migration catalog／覆盖合同推进到现有 `0153`，不改写历史 migration。
- [x] 对 A03 报出的 15 个生产热点执行真实职责拆分，未扩大 cohesive 白名单。
- [x] 当前工作树 A01～A14 静态架构门禁通过。
- [x] 拆分后的受影响波次回归 306 项通过，pytest 全量收集通过。
- [ ] 提交并绑定新 SHA 后执行 A15 完整服务栈与故障矩阵；通过前不宣称 Architecture 2.0 完成。

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
