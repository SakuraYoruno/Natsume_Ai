受众：CODEX
标题：WebUI 数字生命重构留痕（2026-04-06）

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

## 第六批增量（2026-04-07 继续）

继续打通“生命态异常 ↔ 运维观测”：

- 新增共享的数字生命运行风险摘要组件
- 新增 runtime 风险工具层，统一把以下信号翻译成可观测异常：
  - mode degraded
  - runtime slot 非 live
  - self reflection guard 风险
  - topic resistance
  - withdrawn reply policy
  - prompt injection / high risk build
- `Overview` 接入数字生命运行摘要，不再只看服务健康
- `Alerts` 将生命态风险并入告警中心，新增“生命态异常”统计
- `Insights` 顶部接入同一份数字生命运行摘要，和分组洞察统一

到这一轮为止，`Overview / Alerts / Insights` 三张观测页已经能直接看到：

- 当前 mode / Bond / reply posture
- prompt 安全风险
- 护栏风险
- runtime slot 降级
- 与生命态相关的告警跳转入口

## 第七批增量（2026-04-07 继续）

继续把 `Observe` 组最后一张关键页 `Trace` 收口到同一套数字生命语义下：

- `Trace` 首页顶部接入统一的数字生命运行摘要卡
- 与链路流、请求总览并排展示当前生命态关键信号：
  - 当前 `mode`
  - 当前 `reply posture`
  - `self reflection guard` 风险级别
  - 非 `live` slot 数
- `Trace` 页并行拉取：
  - `GET /obs/runtime-snapshot`
  - `GET /obs/prompt-builder/stats`
- 当链路观察页排查异常时，不再只能看“请求跑到了哪一跳”，还能同时判断：
  - 这次异常是不是处在降级 mode
  - 当前是否存在 prompt injection / high-risk build 风险
  - 回应策略是否已经进入收敛/抗拒态

到这一轮为止，`Observe` 组的关键页面已经形成统一闭环：

- `Overview`：先看整体运行态
- `Alerts`：集中看异常
- `Insights`：看统计洞察
- `Trace`：回到单条链路时，也能同步看到生命态上下文

## 第八批增量（2026-04-07 继续）

继续补一层“抬头可见”的风险感知：

- 顶层导航的 `Life` 分组标题接入运行中生命态风险 badge
- `Alerts` 导航项同步展示同一份风险计数，方便直接跳到告警中心
- badge 统一由：
  - `GET /obs/runtime-snapshot`
  - `GET /obs/prompt-builder/stats`
  - `buildDigitalLifeAlerts(...)`
  推导，不额外新增后端聚合接口

这样处理后，用户即使不在 `Overview / Alerts / Trace` 页面里，也能在导航层第一时间看到：

- 当前是否存在生命态异常
- 风险数量是否还在持续存在
- 应该优先进入 `Life` 还是直接打开 `Alerts`

## 第九批增量（2026-04-07 继续）

继续把 `Observe` 组里剩余高频页收口到同一套运行语义：

- `Sessions` 顶部接入统一的数字生命运行摘要卡
- `Metrics` 顶部接入统一的数字生命运行摘要卡
- 两页都并行复用：
  - `GET /obs/runtime-snapshot`
  - `GET /obs/prompt-builder/stats`

这样处理后，`Observe` 组的高频入口不再只有“数据表 / 图表”，而是统一具备：

- 当前生命态上下文
- prompt 安全风险计数
- 回应姿态 / 护栏风险 / slot 降级提示

也就是说，用户在：

- 看会话列表时
- 看指标走势时
- 看链路细节时

都不会再脱离“此刻夏目蓝正处于什么状态”这一层解释框架。

## 第十批增量（2026-04-07 继续）

继续把 `Observe` 组的交互页收口：

- `ChatDebug` 顶部接入统一生命态摘要卡
- `LogStream` 顶部接入统一生命态摘要卡
- 这样在“直接调一轮对话”与“盯实时日志”时，也能同时看到：
  - 当前 mode
  - 当前 reply posture
  - 护栏风险等级
  - slot 降级 / prompt 风险计数

到这里，`Observe` 组中用户最常进入的页面已经基本被同一套数字生命上下文串起来：

- `Overview`
- `Alerts`
- `Insights`
- `Trace`
- `Sessions`
- `Metrics`
- `ChatDebug`
- `LogStream`

## 第十一批增量（2026-04-07 继续）

在高频页都接上运行摘要后，继续做了一次实现层收口：

- 新增共享 hook：`useDigitalLifeRuntimeSignals`
- 统一封装：
  - `GET /obs/runtime-snapshot`
  - `GET /obs/prompt-builder/stats`
  - 首次加载 + 轮询刷新
- 已切到共享 hook 的页面包括：
  - 顶层导航 `Layout`
  - `Trace`
  - `Sessions`
  - `Metrics`
  - `ChatDebug`
  - `LogStream`

这样做的价值不是“多一个抽象”，而是为了防止后续继续扩页时再次把轮询逻辑复制一遍，降低：

- 取数窗口不一致
- 刷新间隔不一致
- 未来改字段时多点漂移

## 第十二批增量（2026-04-07 继续）

继续把核心观测页切到同一套共享实现：

- `Overview`
- `Alerts`
- `Insights`

以上页面的数字生命运行摘要现在统一复用 `useDigitalLifeRuntimeSignals`，不再各自手写：

- runtime snapshot 拉取
- prompt builder stats 拉取
- 轮询刷新逻辑

到这一轮为止，数字生命运行摘要在“高频页 + 核心观测页”上已经基本统一到一条实现链里。

## 第十三批增量（2026-04-07 继续）

继续把“页面层体验”和“实现层一致性”往前推了一轮：

- `PromptConfig` 接入统一生命态摘要卡
- `PersonaPromptOps` 接入统一生命态摘要卡
- `SystemConfig` 接入统一生命态摘要卡
- `DigitalLifeOverview` / `EmotionDashboard` / `InnerWorldDashboard` / `ResponsePolicyDashboard` / `SelfModelDashboard`
  继续切到共享的 `useDigitalLifeRuntimeSignals`

这一轮的目标不是再加新页面，而是把以下体验做一致：

- 运营页调 Prompt / Persona 时，能同步看到当前 mode / reply posture / 风险态
- 专题页刷新按钮和轮询逻辑不再各写一套
- 生命态数据入口继续收敛到同一条共享链，减少页面间漂移

## 第十四批增量（2026-04-07 继续）

继续做了一轮前端装载性能收口：

- `App.jsx` 改为页面级懒加载
- 新增统一路由加载占位
- 图表组件改为组件级懒加载 `echarts-for-react`
- `vite.config.js` 关闭入口 `modulepreload`

这一轮之后的直接效果：

- 主入口 JS 从此前约 `30.8 kB` 继续降到约 `28.6 kB`
- `static/monitor/index.html` 不再在首页预加载 `echarts-vendor`
- 用户打开监控面板首页时，先加载数字生命驾驶舱本身，图表大包延后到真正进入图表页时再取

## 第十五批增量（2026-04-07 继续）

继续收了一轮页面观感一致性：

- 新增共享头图组件：`PageHero`
- 先替换以下高频页顶部专题头图：
  - `PromptConfig`
  - `PersonaPromptOps`
  - `SystemConfig`
  - `InnerWorldDashboard`
  - `ResponsePolicyDashboard`
  - `SelfModelDashboard`

这轮的重点不是功能新增，而是把“数字生命驾驶舱”的视觉层级做统一：

- 同一套专题页头语言
- 同一套顶部信息密度
- 同一套 badge / 说明 / meta pills 结构

这样后面继续改页面时，不会再出现“每一页都像不同阶段临时拼出来”的割裂感。

## 第十六批增量（2026-04-07 继续）

继续把重复展示层往共享组件收：

- 新增共享展示组件：
  - `MetricCard`
  - `KeyValueRow`
- 第一批已替换页面：
  - `PromptConfig`
  - `PersonaPromptOps`
  - `SystemConfig`
  - `DigitalLifeOverview`
  - `InnerWorldDashboard`
  - `ResponsePolicyDashboard`
  - `SelfModelDashboard`

这轮主要收口的是此前重复最多、也最容易继续漂移的两类结构：

- 顶部摘要指标卡
- 卡片内部的 key/value 明细行

这样做之后的直接收益：

- 颜色、字级、留白和信息密度更一致
- 后续调数字生命专题页时，展示层不需要每页各改一份
- 可以继续把 `Insights / Gateway / MemoryBrowser / WeChat` 上的同类卡片逐步并入同一套组件

本轮已重新执行 `npm run build`，构建通过。

## 第十七批增量（2026-04-07 继续）

继续把监控页里仍然重复的展示壳收口到共享组件：

- 新增共享标题组件：`SectionHeading`
- `Insights` 内部本地 `StatCard / SectionTitle` 改为复用：
  - `MetricCard`
  - `SectionHeading`
- 继续替换以下页面的统计卡片到 `MetricCard`：
  - `Gateway`
  - `MemoryBrowser`
  - `WeChat`

这一轮的目标是把“运营/专题页”和“监控页”统一到同一套展示基元，减少样式与语义漂移：

- 指标卡统一使用 `MetricCard`
- 分组标题统一使用 `SectionHeading`

本轮已再次执行 `npm run build`，构建通过。

## 第四十一批增量（2026-04-07 继续）

这一轮继续收口“模式口径”和“导航入口语义”，重点把用户第一眼能看到的入口统一到数字生命升级后的定义：

- 顶层导航分组：
  - `生命态` 改为 `数字生命`
- 数字生命入口里的 `RuntimeMode`：
  - 顶层导航项从 `模式` 改为 `存在态`
  - 二级导航项从 `模式` 改为 `存在态`
- 共享运行摘要卡：
  - 摘要 fallback 文案从“当前处于 xx 模式”改为“当前存在态为 xx”
  - 指标 pill 从 `模式` 改为 `存在态`
  - 绿色安全提示从“生命态风险摘要”改为“数字生命运行风险”
- 同时修复了一处之前就存在的导航 badge 挂载问题：
  - 分组 badge key 改为和中文分组标题一致
  - 顶层 `数字生命` 分组现在会正常显示运行风险计数

这一轮的边界仍然保持不变：

- **不改后端接口枚举**
- **不改 `sys_mode` 作为底层字段名**
- **只改 WebUI 的用户可见语义层**

本轮已再次执行 `npm run build`，构建通过。

## 第四十批增量（2026-04-07 继续）
这一轮继续收口“原始枚举 / 原因码 / 调试字段直出”，仍然坚持 **只改前端显示层，不改 API 契约**：

- 共享格式化能力继续扩展到 `src/utils/digitalLife.js`：
  - 新增 `formatEmotionTag`
  - 新增 `formatTrend`
  - 新增 `formatTransitionStatus`
  - 新增 `formatDecision`
  - 新增 `formatEventPhase`
  - 新增 `formatSceneType`
  - 新增 `formatServiceStatus`
- `src/utils/runtimeHealth.js` 改为直接复用共享格式化函数：
  - 不再保留一套重复的本地映射
  - 告警标题、降级原因、非在线槽位、运行时信号统一走共享口径
  - 反思护栏风险标签改为先格式化后展示，避免再出现原始 reason tag

本批已接入收口的页面：

- `DigitalLifeOverview`
  - 主情绪从原始 `emotion_tag` 改为中文标签
  - 关系趋势 / 最近情绪趋势 / 主导近期情绪统一格式化
  - 自我反思触发原因改走共享标签格式化
- `EmotionDashboard`
  - 主情绪与情绪分布列表不再直出英文情绪枚举
- `SelfModelDashboard`
  - 最近情绪趋势、主导近期情绪、关系趋势、反思触发原因改为共享格式化输出
- `Bond`
  - 关系事件提交结果里的阶段变更与 `transition_status` 改为可读文案
- `ChatDebug`
  - 场景、决策、阶段、原因码不再裸露工程字段，改为格式化后的可读标签
- `Alerts`
  - 告警卡片标题优先展示格式化标题
  - 服务状态告警从英文 `SERVICE_DOWN / SERVICE_DEGRADED` 改为中文表达
  - “原因码分布”改为“触发类型分布”，列表展示格式化后的用户可读名称
- `Insights`
  - 拒绝原因 / 错误原因图表改为显示格式化后的触发类型
  - 最近错误列表中的环节、原因码改为可读文案
  - 情感标签图表不再直出 `emotion_tag`
- `LogStream / Sessions / WeChat`
  - 日志流、会话列表、微信事件表中的原因码统一走共享标题格式化
  - 微信事件状态从 `OK / SENT / DELIVERED / RATE_LIMITED` 改为中文状态
- `Trace / TraceRequestDetail`
  - 活跃链路预览、节点时间线、阶段卡片、检索回退原因不再直出原始 `reason_code`
  - 链路当前状态中的英文状态码改为可读标签

这一步的目标是继续压缩 WebUI 中“工程码直接露出”的面积，让数字生命专题页、调试页、告警页尽量统一成一套可运营、可理解、可修改的显示口径。

## 第三十九批增量（2026-04-07 继续）
继续清理“数字生命运行摘要”卡片里的内部枚举直出与运行态工程码暴露：

- 基础语义映射恢复并统一到前端工具层：
  - `src/utils/digitalLife.js`
  - `src/utils/runtimeHealth.js`
- 修复内容：
  - `sys_mode` / `bond_stage` / `reply_posture` / `dominant_motive` / `risk_level` / `absence_band`
    不再直接展示英文枚举，而是转为中文语义标签
  - `RUNTIME_MODE_DEGRADED` / `RUNTIME_SLOT_NOT_LIVE` / `REFLECTION_GUARD_ACTIVE`
    等原因码不再直接作为卡片标题，而是改成中文告警标题
  - `mode=normal | bond=STRANGER | offline=24h_plus | motive=continuity | reply=focused`
    这类机器摘要不再直接上屏，改为中文自然句
  - `current_topic:` / `awakening_hint:` / `growth_note:` / `bond_narrative:`
    这类 signal 前缀改为中文标签
  - `slot_key:status` 与 `degraded_reasons` 改为“槽位名 + 中文状态”展示
- 展示层同步收口：
  - `src/components/DigitalLifeRuntimeCard.jsx`
  - `src/pages/Alerts.jsx`

本轮已执行 `npm run build`，构建通过。

## 第三十五批增量（2026-04-07 继续）

继续收口 `monitor_ui` 中高可见的工程术语与英文直出，重点处理：

- `AgentLoop`
  - `Agent Loop 管理` 收为 `自动化循环管理`
  - `ClawdBot` 收为 `执行端地址`
  - `Mock 模式 / Edge RPC 模式` 收为 `模拟模式 / 边缘 RPC 模式`
  - 空态、配置同步失败、历史记录加载失败全部切到统一 `NoticeBanner / EmptyStateCard`
- `Metrics`
  - `reason_code 分布` 收为 `原因码分布`
  - `总 Key 数 / 活跃 Key / Key` 收为 `总键数 / 活跃键数 / 键名`
  - `MemoryGuard / StateArbiter / ContextCompressor / PromptBuilder / EpisodicStore / Canon` 高可见标题改为中文运营口径
  - `SemanticRecaller / OK / DEGRADED / Top-K / Mock` 等状态与标签改为 `语义召回 / 正常 / 降级 / 召回上限 / 模拟`
- `Insights`
  - `LLM 成本 / LLM 路由 / RecallPack Token Budget / FailureMatrix / PermissionGuard` 等高可见标题继续收为中文
  - `Talk Gate` 空态、`AUDIT_SNAPSHOT` 提示等内部名不再直接暴露给页面
  - `Dream 拦截 / NARRATIVE_ONLY / 节省轮次` 等标签改为更贴近实际含义的中文说明

本轮再次执行 `npm run build`，构建通过；关键产物体积：

- `Insights` 约 `52.14 kB`
- `Metrics` 约 `23.08 kB`
- `AgentLoop` 约 `14.06 kB`
- `LineChart` 仍保持在约 `499.99 kB`，未触发单包超限告警

## 第三十六批增量（2026-04-07 继续）

继续收口“第一眼就能看到”的入口层与首页文案，重点处理：

- `Layout`
  - 顶部导航分组从 `Life / Operate / Observe / Infra` 收为 `生命态 / 运营 / 观测 / 基础设施`
  - 导航项把 `Bond / Canon / Prompts / Persona / Trace / Metrics / Insights / Alerts / Logs / Skills / Auto / Agent` 收为统一中文口径
- `DigitalLifeOverview`
  - `关系与 Bond / 情绪 / VTS / Prompt / Persona / Growth / Evolution` 全部改为中文自然表达
  - 入口按钮与指标标签同步改为 `关系 / 表情 / 提示 / 人格` 口径
  - 去掉 `SSOT / Memory Browser` 等会打断用户理解的内部实现名
- `Overview`
  - `LLM 云节点` 收为 `模型云节点`
  - `多 Agent` 收为 `多代理协同`
  - 健康评分问题项把 `Governor 拦截率` 收为 `入口守护拦截率`
- `Alerts / SystemConfig / SelfModelDashboard`
  - `reason_code 分布` 收为 `原因码分布`
  - `ALL UP / 活跃 Key / Bond 关系摘要 / Prompt 指令` 分别收为 `全部在线 / 活跃键 / 关系摘要 / 提示指令`

本轮再次执行 `npm run build`，构建通过；关键产物体积保持稳定：

- `DigitalLifeOverview` 约 `17.94 kB`
- `Overview` 约 `24.38 kB`
- `Alerts` 约 `7.40 kB`
- `LineChart` 仍保持约 `499.99 kB`

## 第三十七批增量（2026-04-07 继续）

继续收口链路观测页，把“节点英文名来自后端直出”这个根因一起处理：

- 新增共享映射：
  - `src/components/trace/traceNodeText.js`
  - 统一管理链路节点 `node_id / display_name / node_type / layer` 的中文展示名
  - 前端即使收到后端英文节点名，也会被稳定映射到统一中文口径
- `Trace`
  - 分组标题与说明把 `Airlock / Governor / LLM / Bond / VTS / PII` 收为中文自然表达
  - 静态全链路图默认节点名同步切到 `入口净化 / 入口守护 / 模型流式生成 / 隐私过滤 / 关系更新 / 表情驱动`
  - 实时链路卡片、时间线、甘特图搜索与提示统一走共享节点名映射
- `CustomNode / NodeDetailPanel`
  - 节点类型默认标签改为中文
  - `Edge / Host / Both / Provider / Input / Output` 等通用标签改为中文
  - 节点详情里的 `类型 / 端侧 / 原因码` 现在也会显示中文规范名

本轮再次执行 `npm run build`，构建通过；关键产物体积：

- `Trace` 约 `125.16 kB`
- `LineChart` 仍保持约 `499.99 kB`

## 第三十八批增量（2026-04-07 继续）

最后做一轮全局复扫，继续处理残留在标题层和状态层的高可见英文：

- `SkillCatalog`
  - 页头 `Skills Hub` 收为 `技能中心`
  - `ON / OFF` 收为 `开启 / 关闭`
- `PromptConfig`
  - 页头 `Prompt / Fallback 运营` 收为 `提示与兜底运营`
  - `Persona 运营 / Prompt 运营台` 收为 `人格运营 / 提示运营台`
- `WeChat / Bond`
  - 通道状态 `UP / DEGRADED / DOWN / UNKNOWN` 收为 `在线 / 降级 / 离线 / 未知`
  - `Bond` 健康态文案补齐中文状态映射

本轮再次执行 `npm run build`，构建通过；关键产物体积保持稳定：

- `SkillCatalog` 约 `6.75 kB`
- `PromptConfig` 约 `11.90 kB`
- `Bond` 约 `12.75 kB`
- `LineChart` 仍保持约 `499.99 kB`

## 第三十一批增量（2026-04-07 继续）

这一轮继续收口高可见链路页与工具页的残余英文，把“工程字段直出”再往“运营可读”推一步：

- `TraceRequestDetail`
  - 将节点摘要里的 `pack_id / candidate_count / evidence_refs_count / route` 改为自然中文短句
  - 将召回候选卡片里的 `score / confidence` 改为 `分数 / 置信度`
  - 检索运行时里把布尔回退值从 `true / false` 改为 `是 / 否`
  - 将空态提示里的 `embedding / reranker` 收回为 `嵌入 / 重排`
- `Tools`
  - 将 `READ / WRITE` 的显示文案统一为 `只读 / 写入`
  - 将 `Intent ID / Session ID / Approval Token Ref` 收为中文表单标签
  - 将 `Provider / Skill Provider / Custom Providers / SkillRegistry` 等高可见英文统一改为中文运营表达
  - 权限级别改为 `无需确认 / 审批确认 / 高风险确认`
  - `PermissionGuard / ActionPlan` 两块统计标题也收回到更自然中文
- `Trace`
  - 将 `Gateway Webhook / Recall Fusion / Prompt Builder / Code Agent / Translate Agent / Intent Agent / Vision Agent` 等静态拓扑节点名改为中文
  - 将分组标签中的 `Prompt & LLM / Agent / DAG / nodes / edges / UNKNOWN` 等残余英文改为中文显示
  - 活跃链路与全链路总览中的计数单位统一为 `节点 / 连线 / 个节点`

本轮已执行 `npm run build`，构建通过：

- `Tools` chunk 约 `21.03 kB`
- `Trace` chunk 约 `122.71 kB`
- `Insights` chunk 约 `52.20 kB`

## 第三十二批增量（2026-04-07 继续）

这一轮继续收 `Canon / MemoryBrowser` 两个高可见记忆域页面，把状态枚举和页头残余技术词再往中文化推进：

- `Canon`
  - 将提案状态从原始枚举改为中文显示：`待审核 / 已生效 / 已拒绝 / 已回滚`
  - 将变更清单状态从 `ACTIVE / UNDONE / GC` 改为 `生效中 / 已撤销 / 已回收`
  - 页头元信息中的 `Vault` 改为 `清单`
  - `提交新 Canon 提案`、`文档查看` 等标题继续收口为更自然中文
  - 提交表单标签示例从英文标签样例改为中文场景样例
- `MemoryBrowser`
  - Hero 文案中的 `Canon 文档 / Recall 槽位 / Episodic 写入` 改为 `主设定文档 / 召回槽位 / 情景写入`
  - `召回包 Token 预算` 改为 `召回包预算`
  - `tok / Pack / Token` 等显示单位改为 `词元 / 总召回包 / 节省词元`
  - `Episodic 写入摘要` 改为 `情景记忆写入摘要`
  - 主设定文档状态显示统一为 `生效中`

本轮已再次执行 `npm run build`，构建通过：

- `Canon` chunk 约 `19.76 kB`
- `MemoryBrowser` chunk 约 `8.65 kB`
- `Trace` chunk 约 `122.71 kB`

## 第三十三批增量（2026-04-07 继续）

这一轮继续收 `Bond / EmotionDashboard / Gateway / ChatDebug` 的高可见文案，把页面入口处最容易撞到的内部术语往中文运营口径收：

- `Bond`
  - 页面标题从 `Bond 关系` 收为 `关系状态`
  - `Bond 数据加载失败 / Bond 事件` 等标题统一改为 `关系数据 / 关系事件`
  - 表单示例从纯英文占位改为更贴近实际的中文场景样例
- `EmotionDashboard`
  - 页头说明中的 `VAD / Bond / VTS` 收为 `情绪三维 / 关系阶段 / 表情聚合`
  - `VTS 样本`、`VTS 降级率` 改为 `表情样本`、`表情驱动降级率`
  - `Valence / Arousal / Dominance` 去掉括号英文，仅保留中文标签
- `Gateway`
  - 网关平台状态标签从 `UP / DEGRADED / DOWN / UNKNOWN` 改为 `在线 / 降级 / 离线 / 未知`
  - 平台卡中的 `Bridge` 改为 `桥接`
  - Hero 元信息中的 `active / humanize / window / ON / OFF / CPS` 改为中文说明
- `ChatDebug`
  - 调试页副标题、右侧标题与空态提示去掉 `SSE` 直出，统一改为 `实时事件流`
  - 事件卡里的 `scene=`、`node=` 改为 `场景=`、`节点=`
  - 输入框提示从 `Enter 发送` 改为 `回车发送`
  - 请求快链标题从 `request_id` 改为 `请求 ID`

本轮已再次执行 `npm run build`，构建通过：

- `Bond` chunk 约 `12.70 kB`
- `EmotionDashboard` chunk 约 `10.31 kB`
- `Gateway` chunk 约 `9.43 kB`
- `ChatDebug` chunk 约 `7.59 kB`

## 第三十四批增量（2026-04-07 继续）

这一轮继续收首页入口与人格文案页的命名口径，减少运营面板直接暴露内部术语：

- `PersonaPromptOps`
  - 页面标题从 `Persona 文案运营` 收为 `人格文案运营`
  - `source / type` 徽章改为 `默认 / 已覆盖`、`文本 / 结构化`
  - 成功与错误提示里的 `Persona` 统一收回为 `人格文案`
  - 空态标题中的 `Persona 模板` 收为 `人格模板`
- `Overview`
  - 多代理卡片中的 `Agent Loop / Handoff / hop` 等表述改为 `自动化循环 / 交接 / 平均交接`
  - `多 Agent 告警` 改为 `多代理告警`
  - `Prompt 安全摘要` 改为 `提示安全摘要`
  - `Canon 提案状态` 改为 `主设定提案状态`

本轮已再次执行 `npm run build`，构建通过：

- `PersonaPromptOps` chunk 约 `12.88 kB`
- `Overview` chunk 约 `24.36 kB`
- `Insights` chunk 约 `52.20 kB`

## 第二十七批增量（2026-04-07 继续）
这一轮继续收“残余散点 + 构建产物治理”：

- `AgentLoop`
  - 补齐 `Agent` 配置同步失败提示
  - 实时事件流空态改为统一 `NoticeBanner`
  - 历史记录面板改为始终可见，并补齐加载态 / 错误态 / 空态
- `RuntimeMode`
  - 增加初始加载态，避免在快照未返回前渲染默认模式误导
  - 把“灰色不可点击说明”统一成 `NoticeBanner`
- 组件层
  - `InsightChartCard` 改用 `EmptyStateCard` 承载图表空态
  - `EmptyStateCard` 新增 `iconClassName`，便于复用到图表/专题页
- 运营文案细修
  - `Overview` 子系统摘要文案从英文统一为中文
  - `DigitalLifeOverview` 继续收口说明块与标签命名
  - `TraceRequestDetail` 补齐 `Aligned` 字段中文化

另外修复了构建产物长期堆积的根因：

- `host_brain/app/monitor_ui/vite.config.js`
  - 将 `build.emptyOutDir` 从 `false` 改为 `true`
  - 现在每次构建会先清空 `host_brain/app/static/monitor`，不再无限堆积旧 hash 产物

本轮已执行 `npm run build`，构建通过。

## 第二十八批增量（2026-04-07 继续）

这一轮继续收“数字生命总览 / 生命态专题”的剩余技术化文案，把首页与专题页的表达从“开发字段视角”收回到“运营可读视角”：

- `DigitalLifeRuntimeCard`
  - 将 `Mode / Bond / Reply / Risk / Slots` 统一改为中文标签
  - 兜底摘要从 `mode=... · bond=... · reply=...` 改为自然中文短句
  - 操作按钮统一成 `数字生命总览 / 回应策略 / 自我模型`
  - “无明显风险”状态改用更合理的 `ShieldCheck` 图标
- `SelfModelDashboard`
  - 页面标题改为 `自我模型`
  - Hero 元信息从 `stage / shared memory / risk` 改为 `阶段 / 共享记忆 / 风险`
  - “成长里程碑”指标提示改为自然中文，补上“暂无深聊记录”的友好提示
  - `Prompt Directives` 改为 `Prompt 指令`
  - 修正 `bondSummary.shared_memory_count` 的 `0` 值展示，避免被误判为空
- `DigitalLifeOverview`
  - `DomainCard` 的降级状态文案从 `待收口` 改为更自然的 `部分接入`
  - 将多处 `key=value` 风格说明改为中文短句，例如：
    - `事件 12 次`
    - `降级率 3% · 放行 5`
    - `回应意愿 high · 姿态 warm`
    - `字数上限 120 · 追问 开 · 转题 关`
  - 将 `后续补聚合接口与专页` 改为更自然的跳转占位说明
  - 将 `Inner World / Body Sense / Self Model / Self Reflection` 等显眼英文标题改为中文
  - 将 `未接入首屏 SSOT` 改为 `首页快照待接入`
  - 将 `打开 Ops` 改为 `运维总览`

本轮已执行 `npm run build`，构建通过。

## 第二十九批增量（2026-04-07 继续）

这一轮继续收口“数字生命导航 + 专题页乱码重写”，把仍残留在 `Bond / ResponsePolicy / DigitalLifeNav` 里的编码污染和英文散点一次性清干净：

- `DigitalLifeNav`
  - 全部导航标签改为干净中文：`总览 / 情绪 / 关系 / 记忆 / 模式 / 内心世界 / 回应策略 / 自我模型 / 人格文案`
  - 顶部说明从 `Digital Life` 改为 `数字生命导航`
- `ResponsePolicyDashboard`
  - 整页重写为 UTF-8 干净文本
  - 将 `Policy Tags / Motive Tags / Why Reply / Decision Chain` 全部改为中文
  - 将 `willingness / chars / warmth / intimacy / humor / reach_out / self_protect` 等字段式英文收回为自然中文说明
  - 空态、错误态与指标提示统一到现有共享组件
- `Bond`
  - 整页重写为 UTF-8 干净文本
  - 保留原有能力：引擎状态、阶段分布、用户详情、Bond 事件提交
  - 提交表单增加中文标签与更自然的结果提示
  - 用户详情区统一为专题页风格，避免继续混用老旧样式和乱码注释

本轮已执行 `npm run build`，构建通过。

## 第三十批增量（2026-04-07 继续）

这一轮继续把数字生命高频专题页收成统一风格，并清掉残余乱码：

- `EmotionDashboard`
  - 整页重写为 UTF-8 干净文本
  - 时间窗改为复用 `TimeWindowSelect`
  - 增加 `PageHero + MetricCard + NoticeBanner` 统一结构
  - 将情绪态、VTS 分布、关系温度表达改为更自然中文
- `InnerWorldDashboard`
  - 整页重写为 UTF-8 干净文本
  - 统一 `内心世界 / 离线状态 / 内在漂移 / 期待话题` 的标题与提示
  - 将 `Tick` 等技术化表述收回为更自然的用户可读文案
- `SystemConfig`
  - 整页重写为 UTF-8 干净文本
  - 将底座视图统一成数字生命专题风格
  - 把 `custom / mock / RateLimit / LLM` 等残余技术化提示尽量收回为中文运营表达
  - 保留原有指标能力：模型路由、调用统计、检索运行时、限流键、延迟趋势

本轮已执行 `npm run build`，构建通过。

## 第十八批增量（2026-04-07 继续）

对 `Insights` 再做了一次实现层清理：

- 将本地 `StatCard / SectionTitle` 函数声明改为轻量包装写法
- 持续复用共享 `MetricCard / SectionHeading`

这步的目的主要是减少页内“看起来像独立组件、实际只是转发”的维护负担，为后续继续拆 `Insights` 子块做准备。

本轮已再次执行 `npm run build`，构建通过。

## 第十九批增量（2026-04-07 继续）

继续推进“`Insights` 结构拆分 + 图表包体积收口”：

- 新增 `Insights` 复用组件：
  - `src/components/insights/InsightSection.jsx`
  - `src/components/insights/InsightChartCard.jsx`
- `Insights` 第一批接入（先收高频块）：
  - `Governor 决策漏斗`
  - `全局错误追踪`
  - `QA 违规统计`
  - `VTS 通道统计`
  - `LLM 调用成本统计` 的“各模型 Token 消耗”图块空态

继续做了图表依赖的按需化：

- 新增 `src/components/charts/echartsCore.js`
- `LineChart` / `BarChart` 改为 `echarts-for-react/lib/core + echarts/core` 注册模式
- `vite` 分包从单一 `echarts-vendor` 拆为：
  - `echarts-core-vendor`
  - `echarts-react-vendor`

量化结果（同一轮构建对比）：

- `echarts` 主包从约 `1051 kB` 降到约 `521 kB`
- `echarts-react` 包独立约 `15 kB`
- 首屏入口仍维持轻量，图表包按需加载路径保持不变

本轮已执行 `npm run build`，构建通过。

## 第二十批增量（2026-04-07 继续）

继续把 `Insights` 页面主分组往可复用结构推进：

- `InsightSection` 继续接入：
  - `子系统健康矩阵`
  - `Memory 子系统统计`
  - `Agent Loop 统计`
  - `LLM 调用成本统计`
- 本页首批高频段已经形成“统一 section 壳 + 统一 chart card 壳”的组合模式，后续可继续平移到剩余子统计段。

再次执行 `npm run build`，构建通过，分包结果保持稳定：

- `echarts-core-vendor` 约 `521 kB`
- `echarts-react-vendor` 约 `15 kB`

## 第二十一批增量（2026-04-07 继续）

继续完成 `Insights` 剩余统计段的结构统一：

- 将所有子统计函数的外层从原生 `section + SectionTitle` 统一到 `InsightSection`
- 现在 `Insights` 页面主区和子区共用同一套 section 壳，标题样式/间距完全一致
- 已覆盖的子统计段包括：
  - Route / RecallPack / Failure / Permission / MemoryGuard
  - StateArbiter / ContextCompressor / PromptBuilder / EpisodicStore
  - UIAssertion / ChainStats / CanonProposal / ErrorHeatmap

再次执行 `npm run build`，构建通过；`Insights` chunk 进一步收口到约 `52.8 kB`。

## 第二十二批增量（2026-04-07 继续）

继续做页面观感统一，把运营/监控页头图区块补齐到同一风格：

- `Gateway` 接入 `PageHero`
- `MemoryBrowser` 接入 `PageHero`
- `WeChat` 接入 `PageHero`

这轮后，`Operate / Observe` 里最常用的几页在信息层级上保持一致：

- 页头（标题 + 控件）
- Hero（当前窗口关键态）
- 指标网格
- 详情卡片/表格

再次执行 `npm run build`，构建通过。

## 第二十三批增量（2026-04-07 继续）

继续做中段结构统一，减少页内“各写各的标题样式”：

- `Gateway` 接入 `SectionHeading`（平台状态分段）
- `MemoryBrowser` 接入 `SectionHeading`（RecallPack / Canon / 压缩 / 蒸馏）
- `WeChat` 接入 `SectionHeading`（Top 活跃 wxid 分段）

这样现在这三页都统一为：

- `PageHero` 顶部摘要
- `SectionHeading` 分段标题
- `MetricCard` 指标网格

再次执行 `npm run build`，构建通过（`Insights` 约 `52.6 kB`，保持收敛）。

## 第二十四批增量（2026-04-07 继续）

继续做 `echarts` 体积与告警收口，改为“按图型核心拆分”：

- 新增：
  - `src/components/charts/echartsLineCore.js`
  - `src/components/charts/echartsBarCore.js`
- `LineChart` / `BarChart` 分别接入自己的核心注册
- 删除旧的合并核心：`src/components/charts/echartsCore.js`
- `vite` 保持自动分包（不再强制手动合并 `echarts`）

结果：

- 图表大包从此前约 `521 kB` 进一步降到约 `499.9 kB`
- 构建阶段不再触发 `>500k` 的单 chunk 告警

本轮已执行 `npm run build`，构建通过。

## 第二十五批增量（2026-04-07 继续）

继续减少页面级重复控件，新增共享时间窗口下拉：

- 新增组件：
  - `src/components/TimeWindowSelect.jsx`
- 已替换页面：
  - `DigitalLifeOverview`
  - `SystemConfig`
  - `Gateway`
  - `MemoryBrowser`

这一轮后，时间窗口选择的交互与样式都由共享组件托管，后续加新窗口档位或调整样式只需要改一处。

再次执行 `npm run build`，构建通过（图表大包保持约 `499.9 kB`）。

## 后续验收重点

- 打开 `WebUI` 时，第一屏看到的是“夏目蓝当前状态”，而不是服务列表。
- 任何“会影响人格与体验”的能力域，都能在数字生命视角被看见。
- 运维能力继续可用，但不再占据默认入口。
- 专题页之间应保持同一套导航和一致的数据来源，避免再次退回“单点页面 / 伪状态演示”。

## 第二十六批增量（2026-04-07 继续）
这一轮继续收口“提示层统一 + 中文编码清理”，重点处理容易直接影响体验的页面：

- `TraceRequestDetail`：
  - 把加载态、错误态、空态统一切到 `NoticeBanner` / `EmptyStateCard`
  - 清理英文散点提示，统一为中文运营风格
- `Trace`：
  - 补齐活跃链路、历史请求、分组不存在、实时流异常等散点提示
  - 继续减少页面内直接写死的提示块
- 整页重写并恢复 UTF-8：
  - `PromptConfig`
  - `PersonaPromptOps`
  - `SelfModelDashboard`
  - `InnerWorldDashboard`
- 组件层修复：
  - `EmptyStateCard` 默认标题恢复为干净中文 `暂无数据`

同时补了一轮总览层的小收口：

- `Overview` 把“未活跃子系统”提示块并入统一 `NoticeBanner`
- `DigitalLifeOverview`：
  - 把运行时真实性说明切到 `NoticeBanner`
  - 把 `anticipation topics / drifted valence / drifted arousal` 改成中文标签
  - 在无快照时补 `EmptyStateCard`

本轮已再次执行 `npm run build`，构建通过。
