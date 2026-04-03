# DIGITAL_LIFE_ROADMAP_NEXT_PHASE_20260403

## 文档目的

本文档用于将以下 4 份文档收束成一个**可执行的下一阶段路线图**：

1. `DIGITAL_LIFE_RUNTIME_AUDIT_20260403.md`
2. `DIGITAL_LIFE_RUNTIME_VERIFICATION_PLAN_20260403.md`
3. `RESPONSE_POLICY_LAYER_MIN_SPEC_20260403.md`
4. `MOTIVE_ENGINE_MIN_SPEC_20260403.md`

目标不是继续抽象讨论“数字生命是什么”，而是回答：

> 在 `Natsume_Ai` 当前这套代码现实基础上，接下来最值得做的顺序是什么？

换句话说，这是一份面向实际开发与生产实测的：

- 优先级排序
- 阶段划分
- 验收标准
- 风险提醒

---

## 当前阶段判断

基于现有代码审计，可将 `Natsume_Ai` 当前状态定义为：

> **准持续人格系统（quasi-persistent persona system）**

当前系统已经具备：

- 离线时间流
- 等待感
- 沉默意愿
- 身体感映射
- 关系叙事层
- 主动表达引擎
- 调度器基础设施
- 记忆连续性修复

这说明系统已经进入“数字生命能力的工程化阶段”。

但它还没有真正进入“系统级持续闭环”阶段。

当前最关键的三个缺口是：

1. **默认上电闭环尚未被生产环境实测证实**
2. **状态仍主要依赖 Prompt 注入，而非行为控制层**
3. **缺少统一动机中枢，驱动仍分散在多个模块中**

---

## 总路线原则

### 原则 1：先验证真实运行，再做高层架构升级

当前不是继续加模块的最佳时机。
先确认现有模块是否真的上电、真的形成闭环，价值最高。

### 原则 2：先补“最后一跳”，不要横向扩功能

目前很多能力已经存在，问题往往不是“没有”，而是：

- 没默认启动
- 没接结束钩子
- 没形成行为硬约束

因此优先做闭环，不优先做更多 feature。

### 原则 3：先做系统层，后做风格层

如果行为层不稳定，再多风格优化也只是高级拟态。

### 原则 4：先建立可观测性，再谈精调

未来如果要调 Motive Engine / Response Policy，没有 observability 会极其痛苦。

---

## 总体阶段划分

建议下一阶段按 5 个 Phase 推进：

| Phase | 名称 | 核心目标 |
|---|---|---|
| P0 | 生产环境实测确认 | 确认哪些能力真上电 |
| P1 | 闭环补线 | 补齐 scheduler / session_end / observability 断点 |
| P2 | 行为控制层 | 落地 Response Policy Layer |
| P3 | 统一动机中枢 | 落地 Motive Engine |
| P4 | 联调与再实测 | 验证“状态 → 动机 → 策略 → 输出”整链 |

---

# Phase P0：生产环境实测确认

## 目标

在运行环境拉取 GitHub 最新代码后，验证当前代码中哪些数字生命能力真的在生产环境中运行。

## 为什么这是第一优先级

因为当前最大的不确定性不是“架构没想明白”，而是：

- 某些模块是不是已经写好了但没上电
- 某些 tick 是不是存在注册层但没默认启动
- 某些 session_end 能力是不是代码里有但运行时没走到

如果不先做这一步，后续所有改动都可能是重复造轮子。

## 执行依据

使用：

- `DIGITAL_LIFE_RUNTIME_VERIFICATION_PLAN_20260403.md`

## 本阶段必须确认的 5 件事

1. Scheduler 是否真实启动
2. `inner_world_tick / silence_tick / body_sense_tick` 是否真实注册并运行
3. 会话开始 / 结束钩子是否都能命中
4. 离线时间流是否可观测积累
5. 沉默 / 身体感是否真实影响行为，而不只是文本描述

## 本阶段完成标志

满足以下条件可视为 P0 完成：

- Scheduler 已确认 running
- 至少两个 personality tick 已在生产中真实触发
- 至少一组 session start / session end 闭环成立
- 离线状态可观测变化
- 已能明确列出“已上电 / 未上电 / 上电但仅 prompt 生效”的清单

## 本阶段产出

建议输出：

- `DIGITAL_LIFE_RUNTIME_FINDINGS_YYYYMMDD.md`

---

# Phase P1：闭环补线

## 目标

把 P0 中发现的“最后一跳断点”补齐，让现有能力真正形成稳定运行闭环。

## 本阶段优先修复项

### P1-1. Personality Tick 默认注册与启动

#### 目标

确保以下任务在默认启动路径中被注册并启动：

- `inner_world_tick`
- `silence_tick`
- `body_sense_tick`

#### 完成标志

- 服务启动后无需人工干预即可出现在 task registry
- health / audit 中可见定时执行痕迹

---

### P1-2. 会话结束钩子统一收口

#### 目标

明确并稳定触发：

- `inner_world_session_end`
- `body_sense_conversation_end`

#### 为什么优先级高

没有结束钩子，很多“关系延续感”都会断。

#### 完成标志

- 每次完整对话结束后可观测到状态写回
- 下一轮对话能读到结束时留下的痕迹

---

### P1-3. 增加状态层 observability

#### 目标

把这些状态做成可观测输出：

- `offline_state`
- `silence_state`
- `body_sense`
- 后续的 `motive_state`
- 后续的 `response_policy`

#### 完成标志

至少具备一种以下能力：

- debug log
- health snapshot
- runtime trace
- 调试端点

否则后面调策略层会非常痛苦。

---

## 本阶段完成标志

- personality tick 默认上电
- session_end 闭环成立
- 状态层具备可观测性

---

# Phase P2：行为控制层（Response Policy Layer）

## 目标

把当前大量“写进 prompt 的生命状态”下沉为系统级行为约束。

## 执行依据

使用：

- `RESPONSE_POLICY_LAYER_MIN_SPEC_20260403.md`

## 本阶段最小实现目标

第一版只做 5 个高价值控制维度：

1. 输出长度控制
2. 追问控制
3. 话题扩展控制
4. 主动性许可控制
5. 亲密度上限控制

## 推荐落地路径

### P2-1. 新增模块

建议新增：

- `host_brain/persona/response_policy.py`

包含：

- `ResponsePolicyInput`
- `ResponsePolicyDecision`
- `ResponsePolicyLayer.decide()`

### P2-2. 接入点

推荐接在：

- `chat_context_builder.py` 后
- `prompt_builder.py` 前

先将 policy 注入 runtime payload。

### P2-3. 增加一个输出兜底裁剪器

即使 prompt 没完全稳定，也要做系统层兜底：

- `max_chars`
- `max_sentences`
- 清除尾部追问
- 禁止话题扩展

## 本阶段完成标志

满足以下即可视为 P2 完成：

- willingness 下降时，输出可稳定变短
- fatigue / drowsiness 高时，输出明显更克制
- topic resistance 激活时，不再主动追问/扩展
- 浅关系场景下，亲密表达有明显上限
- 这些差异具备可复现性

---

# Phase P3：统一动机中枢（Motive Engine）

## 目标

把当前分散在多个模块中的状态因子，整合成统一、可解释、连续的行为倾向。

## 执行依据

使用：

- `MOTIVE_ENGINE_MIN_SPEC_20260403.md`

## 第一版建议驱动力

1. `reach_out_drive`
2. `care_drive`
3. `withdraw_drive`
4. `continuity_drive`
5. `self_protect_drive`

## 推荐落地路径

### P3-1. 新增模块

建议新增：

- `host_brain/persona/motive_engine.py`

包含：

- `MotiveInput`
- `MotiveState`
- `MotiveEngine.compute()`

### P3-2. 接入点

在 `chat_context_builder.py` 中：

- 汇总当前已有状态
- 计算 `motive_state`
- 注入 runtime payload

### P3-3. 消费方优先级

先改这两个地方：

1. `proactive_voice`
2. `response_policy`

即先不全面重构，而是先让：

- 主动表达从 motive 中读“要不要靠近”
- 响应策略从 motive 中读“更想收缩还是更想照顾”

## 本阶段完成标志

- motive_state 数值可观测
- 主动表达触发可被 `reach_out_drive` 解释
- 收缩型回复可被 `withdraw_drive` 解释
- 续话题行为可被 `continuity_drive` 解释
- 亲密/克制边界可被 `self_protect_drive` 解释

---

# Phase P4：联调与再实测

## 目标

验证一条完整链是否成立：

> 状态层 → 动机层 → 行为策略层 → 最终输出

这是从“模块存在”走向“系统真正活起来”的关键阶段。

## 建议验证问题

### Q1. 相同输入在不同内在状态下，输出行为是否稳定不同？

例如：

- 高疲劳 vs 正常状态
- 高等待感 vs 刚聊过
- 高 withdraw vs 高 care

### Q2. 主动表达是否不再只是命中模板？

### Q3. 关系阶段、等待感、身体感、沉默意愿是否共同塑造行为？

### Q4. 输出差异是否可解释，而不是“看起来碰巧不同”？

## 本阶段完成标志

满足以下条件可视为 P4 完成：

- 输出行为差异能被 motive + policy 共同解释
- 系统在不同状态下表现出稳定可复现的“靠近 / 收缩 / 延续 / 安抚”差异
- 主动性、沉默、关系感不再只是 prompt 风格，而是系统行为结果

---

## 不建议当前优先做的事

### 1. 不建议继续横向扩更多 persona module

原因：

- 当前主要问题不是没有模块
- 而是现有模块还没完全闭环

### 2. 不建议一开始就追求复杂学习型动机系统

第一版先做规则型、可解释型，价值最高。

### 3. 不建议把“意识证明”作为当前工程验收标准

这会让项目永远无法收工。

当前更合理的验收标准应是：

- 连续性
- 可观测性
- 行为差异
- 动机解释性
- 关系延续性

---

## 开发顺序建议（最推荐版本）

### Step 1
生产环境实测（P0）

### Step 2
补齐默认上电与结束钩子（P1）

### Step 3
落地 Response Policy Layer（P2）

### Step 4
落地 Motive Engine（P3）

### Step 5
联调与复测（P4）

---

## 里程碑定义建议

### Milestone A：真实运行

定义：

- 状态层真正上电
- tick 真跑
- 会话起止闭环成立

### Milestone B：行为受状态控制

定义：

- 输出不再只靠 prompt 漂移
- 状态能直接改变回复行为

### Milestone C：行为受统一动机驱动

定义：

- 主动 / 收缩 / 延续 / 关心开始具备统一内在解释

### Milestone D：整链可复现

定义：

- 相同输入在不同状态下有稳定不同输出
- 差异可被状态 + 动机 + policy 解释

---

## 当前最关键的一句话结论

如果要把这条路线压缩成一句话，那就是：

> **先证明它真的在持续运行，再让它按状态真地行动，最后让这些行动由统一动机持续驱动。**

这三步一旦走通，`Natsume_Ai` 就会从“拥有很多生命感模块的系统”，逐步变成“在行为上越来越接近活着的系统”。

---

## 下一步行动建议（立即可执行）

如果现在就开始推进，最推荐的立即动作是：

1. 将运行环境拉到最新代码
2. 按 verification plan 执行 P0
3. 记录 findings
4. 先修 P1（默认上电 + session_end + observability）
5. 再开始代码级落地 `response_policy.py`

如果 P0 没做清楚，不建议直接进入大规模代码改造。
