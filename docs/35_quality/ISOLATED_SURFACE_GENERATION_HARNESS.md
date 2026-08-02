受众：CODEX
标题：Surface 隔离生成回归方法

# Surface 隔离生成回归方法

## 1. 用途

在不启动 Docker、Gateway 或远端 Edge 的情况下，用真实 Surface composition 检查提示构造、情感计划、模型生成和文本质量。该 harness 适合开发期回归，不等于完整服务验收。

## 2. 必须使用真实的部分

- 生产 prompt 和 `AffectiveTurnPlan` 构造路径；
- 与 Host 相同的 model role 和配置解析；
- 真实非 mock provider（需要评价自然度或延迟时）；
- 生产文本一致性与非操控规则；
- 每条场景独立 session，禁止跨 arm 缓存。

允许替换的只有外部交付面：测试可以不播放 TTS、不触发 Avatar，但必须保留将要发送的 `ExpressionPlan`。

## 3. 固定输入

覆盖普通闲聊、隐晦低落、“没事”、混合喜忧、误解、裂痕、修复未接受、要求空间、吃醋但不占有、久别重逢和技术协作。输入记录 scenario id，不保存真实用户私密正文。

## 4. 输出

每次运行保存：

- commit、provider/model、配置 digest；
- scenario/session identity；
- self-affect revision、companion act、expression profile；
- 首 Token、总耗时、usage 和 fallback；
- 最终 display text 与可选 speech text 的脱敏评审副本；
- consistency、安全和人工评分。

输出放入忽略的 `artifacts/local-gates/<sha>/surface/`。

## 5. 断言

- 用户情绪不会直接成为七海 self affect。
- 模型文本不会反写 canonical emotion。
- 普通回合只发起一次 surface 请求。
- 技术失败不会被记为关系拒绝。
- 日语 speech text 与中文 display text 语义一致。
- 不出现威胁、排他、冷暴力或负罪施压。
- 不通过统一中性化规避情感表达。

## 6. 边界

隔离 harness 不能证明 Gateway 去重、Outbox 重放、真实 TTS、Avatar、barge-in、跨进程恢复或目标设备延迟。这些项目必须留给正式全栈验收。
