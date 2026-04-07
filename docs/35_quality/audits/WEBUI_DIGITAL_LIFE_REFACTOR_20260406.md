# WebUI 数字生命重构留痕（2026-04-06）

## 背景

当前 `WebUI` 的信息架构仍以“系统监控 / 服务健康 / Trace 运维”视角为主。  
虽然数字生命相关能力已经在 Host 侧持续升级，但前端主入口没有同步升级为“数字生命驾驶舱”，导致以下问题：

- 首页仍是全服务健康总览，无法直接看到夏目蓝当前生命状态。
- `EmotionDashboard` 中“当前情感状态”仍为前端模拟值，不是后端真实状态。
- `inner_world`、`body_sense`、`emotional_silence`、`self_model`、`growth / drift` 等核心域在导航与首页中没有对应入口。
- 运营配置能力（`PromptConfig` / `PersonaPromptOps`）已经存在，但没有被纳入“数字生命运营”主视角。

## 本次结论

`WebUI` 不应继续只做“运维监控面板”，而应重构为双层视角：

1. **数字生命**
   - 作为默认首页
   - 聚合展示人格、情绪、关系、记忆、模式、运营配置
   - 后续继续接入内心世界、身体感、沉默机制、自我模型、成长/漂移
2. **系统运维**
   - 保留原有服务健康、Trace、Logs、Gateway、Automation、Tools、Agent 等
   - 用于排障和链路观察

## 现状判定

### 已具备基础数据源

- `sys_mode`
- `bond`
- `vts`
- `memory`
- `prompt_builder`
- `services_health`
- `subsystems_stats`

这些数据足以支撑第一批“数字生命首页”改造。

### 尚未形成首页聚合模型的域

- `inner_world`
- `body_sense`
- `emotional_silence`
- `self_model`
- `trait_drift_guard`
- `growth / evolution / desire`
- `proactive_voice`

这些能力大量存在于后端，但前端没有统一读取模型与展示页。

## 重构策略

### Phase 1：先改信息架构与默认入口

- 新增 `Digital Life Overview`
- 将默认首页切换到数字生命视角
- 导航新增 `Life`
- 原 `Overview` 保留为运维总览
- 在数字生命首页展示“已接入第一批 / 待接入下一批”

### Phase 2：从“伪状态”改成“真实状态”

- 替换 `EmotionDashboard` 中前端写死的情绪 / Bond 演示值
- 建立统一的数字生命聚合接口或前端聚合层
- 明确各能力域的 SSOT 和刷新策略

### Phase 3：补齐核心域页面

- 内心世界
- 身体感
- 沉默机制
- 自我模型 / 人格漂移
- 成长 / 演化 / 欲望
- 主动语音 / 关系推进

## 本次实际落地范围

本次先落第一批：

- 新增一页真正的数字生命首页
- 默认路由切到数字生命首页
- 导航新增 `Life`
- 原 `Overview` 继续保留，作为运维视角入口
- 数字生命首页显式区分：
  - 已接入：运行模式、Bond、VTS/情绪、记忆、Prompt/Persona 运营
  - 待接入：Inner World、Body Sense、Silence、Self Model、Growth/Drift

## 第二批增量（同日继续）

在第一批默认入口切换完成后，继续完成了第二批“真实状态接线”：

- 前端接入现有的 `GET /obs/runtime-snapshot`
- `EmotionDashboard` 移除前端 mock 状态，改为基于真实 runtime snapshot + `VTS` 统计拼接
- 数字生命首页新增真实运行态区块：
  - `Inner World`
  - `Body Sense`
  - `Self Model`
  - `Self Reflection`
  - `Silence / Motive / Response Policy`

这意味着当前 WebUI 已经不只是“默认入口换皮”，而是开始真正消费数字生命运行时快照。

## 第三批增量（同日继续）

在第二批真实状态接线完成后，继续完成了第三批“总览 → 专题页”收口：

- 新增数字生命二级导航，形成统一专题入口：
  - 总览
  - 情绪
  - Bond
  - 记忆
  - 模式
  - Inner World
  - 回应策略
  - Self Model
  - Persona
- 新增 3 个数字生命专题页：
  - `InnerWorldDashboard`
  - `ResponsePolicyDashboard`
  - `SelfModelDashboard`
- 数字生命首页中的 `Inner World / Body Sense / Self Model / Growth` 卡片已改成可直接跳转的专题入口
- `EmotionDashboard`、`MemoryBrowser`、`Bond`、`RuntimeMode` 已接入同一套数字生命导航，不再是彼此割裂的页面

## 编码与可读性补偿

为降低后端历史文本编码污染对前端观测的影响，本次前端还新增了一个保守的响应文本修复层：

- 在 `obs` API 请求层对 JSON 响应做递归文本修复
- 尝试修复典型 UTF-8 / Latin-1 错位造成的中文乱码
- 目标是先保证 WebUI 观测值可读，再继续逐步回收后端原始文本污染

这不是后端源头治理的替代品，但能明显改善当前监控与运营页面的可读性。

## 第四批增量（2026-04-07 继续）

围绕“数字生命运营视角”继续补完了运营与底座页：

- `PromptConfig` 改为面向运营的提示词/兜底配置页：
  - 接入数字生命二级导航
  - 新增模板总数、override 数、JSON 数、guard/fallback 数摘要
  - 强化“可调文案 / 不可破坏结构”的运营提示
- `PersonaPromptOps` 改为面向人格运营的文案工作台：
  - 接入数字生命二级导航
  - 新增 bundle/template/override/json 摘要
  - 强化“像不像夏目蓝”这一层的运营边界
- `SystemConfig` 改为“数字生命系统底座”视角：
  - 接入数字生命二级导航
  - 叠加 runtime snapshot，展示当前 mode / reply posture / 非 live slot 数
  - 保留模型路由、检索对齐、限流与延迟分析
- `DigitalLifeOverview` 新增“运营控制”分组：
  - Prompt / Fallback
  - Persona 文案
  - 系统底座

这意味着当前 WebUI 已初步形成：

1. **生命态总览**
2. **生命域专题页**
3. **人格体验运营页**
4. **底层运行底座页**

## 第五批增量（2026-04-07 继续）

顶层导航信息架构继续收口：

- 将原本平铺的一排顶级导航，改为分组导航
- 当前分组为：
  - `Life`
  - `Operate`
  - `Observe`
  - `Infra`
- 桌面端导航改为“分组容器 + 导航胶囊”
- 移动端抽屉同步改成同样的分组结构

这样处理后，WebUI 的顶层结构已经从“堆页面链接”变成“数字生命 / 运营 / 观测 / 基础设施”四层认知模型，更接近一个可持续演进的数字生命驾驶舱。

## 后续验收重点

- 打开 `WebUI` 时，第一屏看到的是“夏目蓝当前状态”，而不是服务列表。
- 任何“会影响人格与体验”的能力域，都能在数字生命视角被看见。
- 运维能力继续可用，但不再占据默认入口。
- 专题页之间应保持同一套导航和一致的数据来源，避免再次退回“单点页面 / 伪状态演示”。
