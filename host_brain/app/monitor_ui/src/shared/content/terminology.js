export const NAV_LABELS = Object.freeze({
  cockpit: '驾驶舱',
  lifeTimeline: '时间轴',
  pathologies: '病灶',
  pathologyDetail: '病灶详情',
  emotion: '情绪',
  relationship: '关系',
  memory: '记忆',
  lifeRuntime: '运行态',
  runtimeMode: '存在态',
  innerWorld: '内在世界',
  responsePolicy: '回复策略',
  selfModel: '自我模型',
  overview: '观测总览',
  sessions: '会话 Sessions',
  trace: '链路 Trace',
  bioOps: 'BioOps',
  metrics: '指标 Metrics',
  insights: '洞察 Insights',
  chatDebug: '聊天调试',
  alerts: '告警',
  logs: '日志',
  prompt: '提示词',
  persona: '人格 Persona',
  system: '系统底座',
  canon: '设定库 Canon',
  gateway: '网关 Gateway',
  skills: '技能 Skills',
  tools: '工具 Tools',
  automation: '自动化',
  agentLoop: '智能体循环',
  tribe: '群体协作',
  edgeCommand: '边缘命令',
  immune: '安全免疫',
  body: '身体集成',
})

export const DATA_STATE_COPY = Object.freeze({
  loading: {
    title: '正在同步',
    hint: '首次加载中，页面会在数据返回后自动切换到可操作状态。',
    variant: 'info',
  },
  refreshing: {
    title: '正在刷新',
    hint: '保留最近一次成功数据，同时拉取最新快照。',
    variant: 'info',
  },
  empty: {
    title: '暂无数据',
    hint: '当前接口没有返回可展示内容。',
    variant: 'empty',
  },
  no_sample: {
    title: '暂无样本',
    hint: '接口可用，但当前时间窗口没有产生观测样本。',
    variant: 'empty',
  },
  unavailable: {
    title: '暂不可用',
    hint: '当前模块未启用、未连接或暂未产出快照。',
    variant: 'warning',
  },
  backend_off: {
    title: '后端未连接',
    hint: '前端已降级展示。启动 Host Main / Observability Backend 后可恢复实时数据。',
    variant: 'warning',
  },
  unauthorized: {
    title: '权限不足',
    hint: '当前操作需要更高权限或显式审批。',
    variant: 'warning',
  },
  lab_disabled: {
    title: 'Lab Mode 未开启',
    hint: '高风险阶段只在授权靶场和审批轨道内可执行。',
    variant: 'warning',
  },
  error: {
    title: '加载失败',
    hint: '请求失败，请查看错误信息或重试。',
    variant: 'error',
  },
})

export const LIFE_COPY = Object.freeze({
  cockpitTitle: '数字生命体总览',
  cockpitSubtitle: '把运行快照、器官状态、病灶、事件链和策略动作放在同一个工作台。',
  cockpitEyebrow: 'Cockpit / Life Systems',
  commandRailTitle: '现在先处理什么',
  commandRailDescription: '首屏优先回答三件事：数据是否可信、哪个器官或病灶最该看、下一步入口在哪里。',
  focusLensTitle: 'Cockpit Focus Lens',
  focusLensDescription: '在同一页切换优先链、全景和事件视角，让下方器官图、病灶和时间轴只展示当前判断需要的样本。',
  gateTitle: '观测 Gate',
  quickLinksTitle: '常用入口',
  guidanceTitle: '优先动作',
  lifeNavTitle: 'Life Systems',
  lifeNavHint: '按系统域快速跳转，标签与左侧主导航保持一致。',
  quickLinks: [
    { key: 'timeline', to: '/life/timeline', label: '时间轴', detail: '按事件顺序回看状态变化。' },
    { key: 'pathologies', to: '/life/pathologies', label: '病灶', detail: '只看已经形成的异常模式。' },
    { key: 'runtime', to: '/life/runtime', label: '运行态', detail: '检查 runtime 与 memory 接入。' },
    { key: 'memory', to: '/life/memory', label: '记忆', detail: '定位记忆召回和样本质量。' },
  ],
  navItems: [
    { to: '/cockpit', labelKey: 'cockpit' },
    { to: '/life/timeline', labelKey: 'lifeTimeline' },
    { to: '/life/pathologies', labelKey: 'pathologies' },
    { to: '/life/emotion', labelKey: 'emotion' },
    { to: '/life/relationship', labelKey: 'relationship' },
    { to: '/life/memory', labelKey: 'memory' },
    { to: '/life/runtime', labelKey: 'lifeRuntime' },
    { to: '/life/runtime-mode', labelKey: 'runtimeMode' },
    { to: '/life/inner-world', labelKey: 'innerWorld' },
    { to: '/life/response-policy', labelKey: 'responsePolicy' },
    { to: '/life/self-model', labelKey: 'selfModel' },
  ],
})

export const OBSERVABILITY_COPY = Object.freeze({
  overviewTitle: '观测总览',
  overviewSubtitle: '从健康快照、子系统、Trace、指标和告警判断当前系统应先追哪条线。',
  overviewEyebrow: 'Observability / Control Plane',
  heroEyebrow: 'Observability Control Plane',
  heroDescription: '这页把实时健康、服务状态、执行护栏和运行摘要收敛成一个排障入口。先看 Gate，再决定进入 Trace、Metrics、Alerts 或 Sessions。',
  commandRailTitle: '排障入口',
  commandRailDescription: '首屏先回答：观测链是否连通、样本是否足够、当前更像服务问题还是执行护栏问题。',
  gateTitle: '观测 Gate',
  quickLinksTitle: '常用入口',
  focusTitle: '建议路径',
  evidenceHandoffKicker: 'Observability Evidence Handoff',
  evidenceHandoffTitle: '排障证据交接',
  quickLinks: [
    { key: 'trace', to: '/observability/trace', label: '链路 Trace', detail: '按 request / node / stage 追调用链。' },
    { key: 'metrics', to: '/observability/metrics', label: '指标 Metrics', detail: '看请求量、拒绝率和延迟走势。' },
    { key: 'alerts', to: '/observability/alerts', label: '告警', detail: '聚焦需要处理的异常与原因码。' },
    { key: 'sessions', to: '/observability/sessions', label: '会话 Sessions', detail: '回看会话流和执行上下文。' },
  ],
})

export const TRACE_COPY = Object.freeze({
  heroEyebrow: 'Observability / Trace',
  heroTitle: '请求链路排障台',
  heroDescription: '围绕 request_id 追踪输入、检索、规划、工具调用、护栏和输出节点，先判断链路是否可观测，再进入单条请求或完整链路图。',
  commandRailTitle: 'Trace 排障入口',
  commandRailDescription: '首屏先回答：实时流是否连通、当前有没有样本、选中的请求是否健康，以及下一步应该看详情、总览图还是会话样本。',
  gateTitle: 'Trace Gate',
  quickLinksTitle: '常用入口',
  focusTitle: '建议路径',
  quickLinks: [
    { key: 'full', to: '/observability/trace/full', label: '完整链路图', detail: '打开大图模式，按阶段查看请求路径。' },
    { key: 'sessions', to: '/observability/sessions', label: '会话样本', detail: '从 session / conversation 回看上下文。' },
    { key: 'metrics', to: '/observability/metrics', label: '指标趋势', detail: '用延迟、失败率和拒绝率确认影响面。' },
    { key: 'safety', to: '/observability/insights/safety', label: '护栏洞察', detail: '检查 policy、immune 与 UI assertion 拦截。' },
  ],
})

export const EDGE_COMMAND_COPY = Object.freeze({
  heroEyebrow: 'Execution / Edge Command',
  heroTitle: '边缘命令控制台',
  heroDescription: '展示 Edge 下行 authority、显式控制门、payload 校验和回执边界；进程级直推已退役，只有受治理 TaskGraph 才能获得执行资格。',
  commandRailTitle: 'Edge Command Gate',
  commandRailDescription: '先确认 Host 能读到 authority 状态；若返回 governed-only，本页保持只读并引导到 TaskGraph 执行审查，不会绕过 Preview proof 直推。',
  gateTitle: '发送 Gate',
  focusTitle: '建议路径',
  quickLinksTitle: '相关入口',
  quickLinks: [
    { key: 'gateway', to: '/execution/gateway', label: 'Gateway', detail: '检查 Edge / WeChat 网关事件和通道健康。' },
    { key: 'taskRuntime', to: '/execution/task-runtime', label: '执行审查', detail: '从受控任务运行时复核端侧动作和执行回执。' },
    { key: 'tools', to: '/execution/tools', label: 'Tools', detail: '查看工具能力、side-effect 和执行入口。' },
    { key: 'trace', to: '/observability/trace', label: 'Trace', detail: '追踪命令相关 request 和 edge 节点。' },
  ],
})

export const TRIBE_COPY = Object.freeze({
  heroEyebrow: 'Execution / Tribe',
  heroTitle: 'Tribe 协作总线',
  heroDescription: '把 Tribe state、event bus、订阅者和最近事件收敛成协作诊断台。先判断总线是否可观测，再按事件类型或 agent 追协作链。',
  commandRailTitle: 'Tribe 协作入口',
  commandRailDescription: '首屏先回答：状态接口是否可用、事件总线有没有样本、订阅者是否在线，以及下一步该看事件、订阅还是执行链路。',
  gateTitle: 'Tribe Gate',
  focusTitle: '建议路径',
  quickLinksTitle: '相关入口',
  quickLinks: [
    { key: 'taskRuntime', to: '/execution/task-runtime', label: '执行审查', detail: '查看受控任务和最近执行历史。' },
    { key: 'tools', to: '/execution/tools', label: 'Tools', detail: '核对工具能力和 side-effect 元数据。' },
    { key: 'trace', to: '/observability/trace', label: 'Trace', detail: '追踪协作事件关联的 request 链路。' },
    { key: 'logs', to: '/observability/logs', label: 'Logs', detail: '查看总线异常、订阅失败和后端日志。' },
  ],
})

export const GATEWAY_COPY = Object.freeze({
  heroEyebrow: 'Execution / Gateway',
  heroTitle: '统一消息网关',
  heroSubtitle: '六平台消息路由 · 拟人化发送 · 语音视频通话',
  heroDescription: '把跨平台消息路由、平台健康、WeChat 频率限制和 Trace 交接放在同一个观测入口。先判断通道是否可信，再进入平台、Trace 或 Edge Command。',
  commandRailTitle: 'Gateway Routing Gate',
  commandRailDescription: '首屏先回答：网关观测是否连通、平台是否有离线或降级、WeChat 是否触发限制、下一步该看平台健康还是 Trace。',
  gateTitle: '路由 Gate',
  focusTitle: '建议路径',
  quickLinksTitle: '相关入口',
  handoffTitle: 'Gateway Routing Handoff',
  handoffDescription: '把数据源、平台覆盖、风险热点、WeChat 通道和下一步入口连成一条可复核路由链路。',
  quickLinks: [
    { key: 'trace', to: '/observability/trace', label: 'Trace', detail: '追踪 Gateway 前后的 request 级链路。' },
    { key: 'edgeCommand', to: '/execution/edge-command', label: 'Edge Command', detail: '检查下行命令状态和 gated push。' },
    { key: 'tribe', to: '/execution/tribe', label: 'Tribe', detail: '确认协作事件是否继续流转。' },
    { key: 'logs', to: '/observability/logs', label: 'Logs', detail: '查看网关、桥接和适配器异常。' },
  ],
})

export const EXECUTION_TOOLS_COPY = Object.freeze({
  heroEyebrow: 'Execution / Tools',
  heroTitle: '工具管理',
  heroDescription: '把工具目录、只读/写入权限、Pentest 元信息和执行归属放在同一个工作台。目录保持只读，实际工具调用统一进入受治理 TaskGraph。',
  commandRailTitle: 'Tools Execution Gate',
  commandRailDescription: '首屏先回答：工具目录是否可信、当前筛选是否影响样本，以及普通、写入或 Pentest 能力分别由哪条受治理路径接管。',
  gateTitle: '执行 Gate',
  focusTitle: '下一步动作',
  catalogTitle: 'Tool Catalog',
  detailTitle: 'Tool Detail',
  pentestTitle: 'Pentest Metadata',
  runtimeTitle: 'Runtime Context',
  quickLinks: [
    { key: 'catalog', label: 'Catalog', targetId: 'tools-catalog-workspace', detail: '搜索、筛选并选择工具。' },
    { key: 'detail', label: 'Detail', targetId: 'tools-detail-panel', detail: '查看权限、schema 与执行入口。' },
    { key: 'pentest', label: 'Pentest', targetId: 'tools-pentest-metadata', detail: '只展示元信息，安全态与证据在 Security Immune 复核。' },
    { key: 'runtime', label: 'Runtime', targetId: 'tools-runtime-context', detail: '展开工具器官和运行态上下文。' },
  ],
})

export const OPERATIONS_PROMPT_COPY = Object.freeze({
  heroEyebrow: 'Operations / PromptSpec',
  heroTitle: '提示与兜底模板总览',
  heroDescription: '只读核对 code-side PromptSpec、当前生效内容、守卫兜底和现存 override。先确认真源与筛选范围，再进入单个模板差异。',
  commandRailTitle: 'PromptSpec Decision Rail',
  commandRailDescription: '首屏先回答：模板目录是否可信、筛选是否缩小了判断范围、当前模板是否存在 runtime override、详情是否已经同步。',
  gateTitle: 'Prompt Gate',
  focusTitle: '建议动作',
  catalogTitle: 'Catalog',
  selectionTitle: 'Selection',
  detailTitle: 'Resolved Detail',
  sourceTitle: 'Source Truth',
  quickLinksTitle: '相关入口',
  quickLinks: [
    { key: 'catalog', label: 'Catalog', targetId: 'prompt-template-catalog', detail: '搜索、筛选并选择模板。' },
    { key: 'detail', label: 'Detail', targetId: 'prompt-template-detail', detail: '查看 YAML 默认值、当前生效值和 override。' },
    { key: 'summary', label: 'Summary', targetId: 'prompt-summary-cards', detail: '核对数量、覆盖、JSON 和 Guard/Fallback 分布。' },
    { key: 'runtime', label: 'Runtime', targetId: 'prompt-runtime-context', detail: '检查 prompt builder 与运行态上下文。' },
  ],
})

export const INTEGRATIONS_COPY = Object.freeze({
  bodyTitle: '身体集成',
  bodyEyebrow: 'Integrations / Body Bridge',
  bodyDescription: '把 MateEngine 能力、桥接健康、事件/动作目录和桌宠身体动作放在同一个观测台。实际写入由受治理 TaskGraph 与 Edge command 下发。',
  commandRailTitle: 'Body Bridge Gate',
  commandRailDescription: '首屏先确认四件事：MateEngine 是否在线、动作目录是否返回、事件桥是否有样本，以及动作写 authority 是否已进入受治理链路。',
  gateTitle: '发送 Gate',
  focusTitle: '建议动作',
  quickLinksTitle: '相关入口',
  quickLinks: [
    { key: 'edgeCommand', to: '/execution/edge-command', label: 'Edge Command', detail: '检查边缘命令状态和 gated push。' },
    { key: 'gateway', to: '/execution/gateway', label: 'Gateway', detail: '核对 Edge / WeChat 网关通道健康。' },
    { key: 'trace', to: '/observability/trace', label: 'Trace', detail: '用可追踪的 request 和 edge 节点验证身体动作链路。' },
    { key: 'cockpit', to: '/cockpit', label: 'Cockpit', detail: '回到数字生命体总览确认运行态影响。' },
  ],
})

export const SECURITY_IMMUNE_COPY = Object.freeze({
  heroTitle: '安全免疫',
  heroEyebrow: 'Security Lab / Immune',
  heroDescription: '把安全运行态、资产样本和 incident 队列收敛成一个只读值班台。先判断免疫链是否可信，再处理 High+ 事件。',
  commandRailTitle: 'Security Immune Gate',
  commandRailDescription: '首屏先回答：安全 runtime 是否可读、资产样本是否足够、incident 是否需要人工处置，以及主动巡检由哪条受治理任务接管。',
  triageTitle: 'Incident Triage Workbench',
  triageDescription: '按风险、资产、证据和建议动作复核 incident，优先处理 High+ 与缺少证据的异常。',
  runtimeTitle: 'Runtime Snapshot',
  assetsTitle: 'Asset Evidence',
  manualHuntTitle: 'Manual Hunt',
  manualHuntDescription: '旧人工直推命令已退役；这里仅说明迁移状态，安全巡检由受治理 Internal/Scheduler 任务触发。',
  quickLinks: [
    { key: 'incidents', label: 'Incidents', targetId: 'security-immune-incidents', detail: '复核需要人工确认的安全事件。' },
    { key: 'runtime', label: 'Runtime', targetId: 'security-immune-runtime', detail: '确认 /obs/security/runtime 是否可信。' },
    { key: 'assets', label: 'Assets', targetId: 'security-immune-assets', detail: '核对 endpoint asset 快照。' },
    { key: 'hunt', label: 'Manual Hunt', targetId: 'security-immune-hunt', detail: '查看主动巡检写入口迁移状态。' },
  ],
})

export function copyFor(map, key, fallback = key) {
  return map?.[key] ?? fallback
}
