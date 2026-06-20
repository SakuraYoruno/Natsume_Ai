import { resolveLoadState } from '../../models/loadState.js'

export const AGENT_LOOP_PHASE_BADGE_CLASSES = Object.freeze({
  AGENT_INIT: 'bg-blue-100 text-blue-700',
  LOOP_START: 'bg-indigo-100 text-indigo-700',
  ROUND_START: 'bg-purple-100 text-purple-700',
  SCREENSHOT_DONE: 'bg-cyan-100 text-cyan-700',
  PLAN_GENERATED: 'bg-yellow-100 text-yellow-700',
  EXECUTE_DONE: 'bg-orange-100 text-orange-700',
  ROUND_DONE: 'bg-teal-100 text-teal-700',
  LOOP_DONE: 'bg-green-100 text-green-700',
  ROUTE_ERROR: 'bg-red-100 text-red-700',
})

export function getAgentLoopPhaseClass(phase) {
  return AGENT_LOOP_PHASE_BADGE_CLASSES[phase] || 'bg-gray-100 text-gray-600'
}

export function isAgentLoopDoneEvent(event) {
  return String(event?.phase || '').toUpperCase() === 'LOOP_DONE'
}

export function buildAgentLoopRunSummary(loopResult) {
  if (!loopResult) return null
  return {
    success: Boolean(loopResult.goal_achieved),
    rounds: loopResult.total_rounds ?? '—',
    latency: loopResult.total_latency_ms ?? '—',
  }
}

export function buildAgentLoopLoadState({
  agentInfo,
  agentLoopStats,
  uiAssertionData,
  loopHistory = [],
  loading = false,
  refreshing = false,
  error = null,
  infoError = null,
  statsError = null,
  stale = false,
} = {}) {
  const historyRows = Array.isArray(loopHistory) ? loopHistory : []
  return resolveLoadState({
    loading: loading || refreshing,
    error: error || (infoError ? new Error(infoError) : null) || (statsError ? new Error(statsError) : null),
    hasData: Boolean(agentInfo || agentLoopStats || uiAssertionData || historyRows.length > 0),
    hasSamples: Boolean((agentLoopStats?.total_loops ?? 0) > 0 || historyRows.length > 0 || (uiAssertionData?.total_assertions ?? 0) > 0),
    available: true,
    stale,
  })
}

export function buildAgentLoopHeroTitle(agentInfo) {
  return agentInfo ? `默认 ${agentInfo.default_max_rounds} 轮 / ${agentInfo.default_round_timeout_sec}s` : '等待配置加载'
}

export function buildAgentLoopHeroMetaItems({ agentLoopStats, uiAssertionData } = {}) {
  return [
    `总执行：${agentLoopStats?.total_loops ?? 0}`,
    `达成率：${agentLoopStats?.achievement_rate != null ? `${agentLoopStats.achievement_rate}%` : '—'}`,
    `UI 断言：${uiAssertionData?.total_assertions ?? 0}`,
    `敏感拦截：${uiAssertionData?.sensitive_blocked ?? 0}`,
  ]
}

export function getAgentLoopSampleSize({ agentLoopStats, loopHistory = [] } = {}) {
  const historyRows = Array.isArray(loopHistory) ? loopHistory : []
  return (agentLoopStats?.total_loops ?? 0) + historyRows.length
}

export function buildAgentLoopMetricCards({ agentLoopStats, uiAssertionData } = {}) {
  return [
    {
      key: 'achievement',
      label: '目标达成率',
      value: agentLoopStats?.achievement_rate != null ? `${agentLoopStats.achievement_rate}%` : '—',
      hint: `成功 ${agentLoopStats?.achieved ?? 0} / ${agentLoopStats?.total_loops ?? 0}`,
      tone: 'text-green-600',
    },
    {
      key: 'rounds',
      label: '平均轮次',
      value: agentLoopStats?.avg_rounds ?? '—',
      hint: `失败 ${agentLoopStats?.failed ?? 0}`,
      tone: 'text-blue-600',
    },
    {
      key: 'sensitive_blocked',
      label: '敏感 UI 拦截',
      value: uiAssertionData?.sensitive_blocked ?? '—',
      hint: `拦截率 ${uiAssertionData?.block_rate != null ? `${uiAssertionData.block_rate}%` : '—'}`,
      tone: 'text-orange-500',
    },
    {
      key: 'postcondition_failed',
      label: '后置失败',
      value: uiAssertionData?.postcondition_failed ?? '—',
      hint: `前置失败 ${uiAssertionData?.precondition_failed ?? 0}`,
      tone: 'text-rose-600',
    },
  ]
}

export function isAgentLoopRunReady(goal) {
  return String(goal || '').trim().length > 0
}

export function buildAgentLoopRunRequest({
  goal,
  maxRounds,
  roundTimeout,
  totalTimeout,
  useMock,
  useEdgeRpc,
  imageConsent,
} = {}) {
  const trimmedGoal = String(goal || '').trim()
  if (!isAgentLoopRunReady(goal)) return null
  return {
    goal: trimmedGoal,
    max_rounds: maxRounds,
    round_timeout_sec: roundTimeout,
    total_timeout_sec: totalTimeout,
    use_mock: useMock,
    use_edge_rpc: useEdgeRpc,
    image_upload_consent: imageConsent,
  }
}

export function buildAgentLoopRouteErrorEvent(error, timestamp = Date.now()) {
  return {
    phase: 'ROUTE_ERROR',
    error: error?.message || '执行失败',
    _ts: timestamp,
  }
}

export function buildAgentLoopConsoleViewModel({
  agentInfo,
  agentLoopStats,
  uiAssertionData,
  loopHistory,
  loading,
  refreshing,
  error,
  infoError,
  statsError,
  stale,
} = {}) {
  return {
    loadState: buildAgentLoopLoadState({
      agentInfo,
      agentLoopStats,
      uiAssertionData,
      loopHistory,
      loading,
      refreshing,
      error,
      infoError,
      statsError,
      stale,
    }),
    heroTitle: buildAgentLoopHeroTitle(agentInfo),
    heroMetaItems: buildAgentLoopHeroMetaItems({ agentLoopStats, uiAssertionData }),
    sampleSize: getAgentLoopSampleSize({ agentLoopStats, loopHistory }),
    metricCards: buildAgentLoopMetricCards({ agentLoopStats, uiAssertionData }),
  }
}
