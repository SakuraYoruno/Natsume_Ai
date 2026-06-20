import { resolveLoadState } from '../../models/loadState.js'

export const INSIGHTS_WINDOW_OPTIONS = [
  { label: '1h', value: 60 },
  { label: '6h', value: 360 },
  { label: '24h', value: 1440 },
  { label: '7d', value: 10080 },
]

export const INSIGHT_GROUP_DEFS = [
  {
    id: 'global',
    title: '全局与异常',
    iconKey: 'alert',
    desc: '入口守护决策、全局异常追踪、热力图、请求链路统计、失败事件',
    color: 'text-red-600',
    bg: 'bg-gradient-to-br from-red-50 to-red-100 border border-red-100',
    iconBg: 'bg-red-100',
  },
  {
    id: 'memory',
    title: '器官与记忆',
    iconKey: 'database',
    desc: '器官健康、记忆统计、情景写入、上下文压缩',
    color: 'text-teal-700',
    bg: 'bg-gradient-to-br from-teal-50 to-teal-100 border border-teal-100',
    iconBg: 'bg-teal-100',
  },
  {
    id: 'llm',
    title: '执行与推理',
    iconKey: 'bot',
    desc: '执行循环、模型成本、路由决策、提示构建、五路召回',
    color: 'text-indigo-700',
    bg: 'bg-gradient-to-br from-indigo-50 to-indigo-100 border border-indigo-100',
    iconBg: 'bg-indigo-100',
  },
  {
    id: 'safety',
    title: '安全与护栏',
    iconKey: 'shield',
    desc: '质检违规、权限拒绝、规则校验、界面断言安全',
    color: 'text-emerald-700',
    bg: 'bg-gradient-to-br from-emerald-50 to-emerald-100 border border-emerald-100',
    iconBg: 'bg-emerald-100',
  },
  {
    id: 'state',
    title: '状态与规范',
    iconKey: 'gitMerge',
    desc: '关系查询统计、主设定提案',
    color: 'text-cyan-700',
    bg: 'bg-gradient-to-br from-cyan-50 to-cyan-100 border border-cyan-100',
    iconBg: 'bg-cyan-100',
  },
  {
    id: 'vts',
    title: '表情通道',
    iconKey: 'tv',
    desc: '表情通道统计',
    color: 'text-pink-700',
    bg: 'bg-gradient-to-br from-pink-50 to-pink-100 border border-pink-100',
    iconBg: 'bg-pink-100',
  },
]

export function getInsightsCurrentGroup(pathname = '') {
  return String(pathname || '').split('/').pop()
}

export function getInsightsFocusTarget(search = '') {
  return new URLSearchParams(search).get('focus')
}

export function isInsightsRootGroup(currentGroup) {
  return currentGroup === 'insights' || currentGroup === ''
}

export function getInsightsGroupMeta(currentGroup) {
  return INSIGHT_GROUP_DEFS.find((group) => group.id === currentGroup) || null
}

export function buildInsightPulseNodes({
  windowMinutes = 60,
  errData,
  memoryData,
  agentData,
  vtsData,
  llmCallData,
} = {}) {
  return [
    {
      label: '窗口',
      value: `${windowMinutes}m`,
      tone: 'primary',
      active: true,
    },
    {
      label: '全局异常',
      value: String(errData?.total_errors ?? 0),
      tone: 'rose',
      active: (errData?.total_errors ?? 0) > 0,
    },
    {
      label: '记忆器官',
      value: String(memoryData?.subsystems?.length ?? 0),
      tone: 'teal',
      active: Boolean(memoryData?.subsystems?.length),
    },
    {
      label: '执行循环',
      value: String(agentData?.total_loops ?? 0),
      tone: 'violet',
      active: (agentData?.total_loops ?? 0) > 0,
    },
    {
      label: '表情通道',
      value: String(vtsData?.total_events ?? 0),
      tone: 'pink',
      active: (vtsData?.total_events ?? 0) > 0,
    },
    {
      label: '模型呼吸',
      value: String(llmCallData?.total_calls ?? 0),
      tone: 'amber',
      active: (llmCallData?.total_calls ?? 0) > 0,
    },
  ]
}

export function getInsightsUpdatedAt(lastUpdatedAt, runtimeUpdatedAt) {
  return Math.max(lastUpdatedAt || 0, runtimeUpdatedAt || 0) || null
}

export function countInsightsSources({
  govData,
  errData,
  subsysData,
  agentData,
  memoryData,
  vtsData,
  qaData,
  llmCallData,
} = {}) {
  return [
    govData,
    errData,
    subsysData,
    agentData,
    memoryData,
    vtsData,
    qaData,
    llmCallData,
  ].filter(Boolean).length
}

export function buildInsightsLoadState({
  loading = false,
  refreshing = false,
  error = null,
  insightsSourceCount = 0,
  stale = false,
} = {}) {
  return resolveLoadState({
    loading: loading || refreshing,
    error,
    hasData: insightsSourceCount > 0,
    hasSamples: insightsSourceCount > 0,
    available: true,
    stale,
  })
}

export function buildInsightsHeaderStats({ windowMinutes = 60, errData, subsysData, agentData } = {}) {
  return [
    { label: '窗口', value: `${windowMinutes}m` },
    { label: '全局异常', value: errData?.total_errors ?? 0 },
    { label: '子系统', value: subsysData?.subsystems?.length ?? 0 },
    { label: '执行循环', value: agentData?.total_loops ?? 0 },
  ]
}

export function buildInsightsHeroView({
  isRoot = true,
  currentGroupMeta = null,
  windowMinutes = 60,
  errData,
  subsysData,
  agentData,
  vtsData,
} = {}) {
  return {
    iconKey: currentGroupMeta?.iconKey || 'activity',
    eyebrow: isRoot ? '数字生命体综合洞察' : `${currentGroupMeta?.title || '器官分组'} 深度洞察`,
    title: isRoot ? '数字生命体器官级洞察面板' : currentGroupMeta?.title || '器官分组',
    description: isRoot
      ? '从全局异常、记忆器官、安全守卫到 VTS 通道，先看总览，再按分组继续下钻。'
      : currentGroupMeta?.desc || '查看该分组下的详细指标、图表与最近事件。',
    metaItems: [
      `时间窗口：${windowMinutes} 分钟`,
      `全局错误：${errData?.total_errors ?? 0}`,
      `子系统：${subsysData?.subsystems?.length ?? 0}`,
      `Agent Loop：${agentData?.total_loops ?? 0}`,
      `VTS 事件：${vtsData?.total_events ?? 0}`,
    ],
  }
}

export function buildInsightsSampleMeta({
  insightsLoadState,
  windowMinutes = 60,
  insightsSourceCount = 0,
  insightsUpdatedAt = null,
} = {}) {
  return {
    loadState: insightsLoadState,
    windowMinutes,
    sampleSize: insightsSourceCount,
    sampleLabel: '摘要分区',
    updatedAt: insightsUpdatedAt,
    source: 'insights summary + runtime',
    definition: 'Insights 的页首摘要已经切到统一 query 层，后续各分组 section 会继续逐步迁移。',
  }
}

export function buildInsightsDashboardViewModel({
  pathname = '',
  search = '',
  windowMinutes = 60,
  govData,
  errData,
  subsysData,
  agentData,
  memoryData,
  vtsData,
  qaData,
  llmCallData,
  loading = false,
  refreshing = false,
  error = null,
  stale = false,
  lastUpdatedAt = null,
  runtimeUpdatedAt = null,
} = {}) {
  const currentGroup = getInsightsCurrentGroup(pathname)
  const focusTarget = getInsightsFocusTarget(search)
  const isRoot = isInsightsRootGroup(currentGroup)
  const currentGroupMeta = getInsightsGroupMeta(currentGroup)
  const insightsUpdatedAt = getInsightsUpdatedAt(lastUpdatedAt, runtimeUpdatedAt)
  const insightsSourceCount = countInsightsSources({
    govData,
    errData,
    subsysData,
    agentData,
    memoryData,
    vtsData,
    qaData,
    llmCallData,
  })
  const insightsLoadState = buildInsightsLoadState({
    loading,
    refreshing,
    error,
    insightsSourceCount,
    stale,
  })

  return {
    currentGroup,
    focusTarget,
    isRoot,
    currentGroupMeta,
    insightGroups: INSIGHT_GROUP_DEFS,
    headerStats: buildInsightsHeaderStats({ windowMinutes, errData, subsysData, agentData }),
    pulseNodes: buildInsightPulseNodes({
      windowMinutes,
      errData,
      memoryData,
      agentData,
      vtsData,
      llmCallData,
    }),
    insightsSourceCount,
    insightsUpdatedAt,
    insightsLoadState,
    heroView: buildInsightsHeroView({
      isRoot,
      currentGroupMeta,
      windowMinutes,
      errData,
      subsysData,
      agentData,
      vtsData,
    }),
    sampleMeta: buildInsightsSampleMeta({
      insightsLoadState,
      windowMinutes,
      insightsSourceCount,
      insightsUpdatedAt,
    }),
  }
}
