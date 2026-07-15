import { LOAD_STATES, resolveLoadState } from '../../models/loadState.js'

export function buildPermissionGuardStatsView({
  data,
  loading,
  refreshing,
  stale,
  error,
} = {}) {
  const loadState = resolveLoadState({
    loading: loading || refreshing,
    error,
    hasData: Boolean(data),
    hasSamples: Number(data?.total_checks ?? 0) > 0,
    available: Boolean(data),
    stale,
  })

  return {
    loadState,
    showNoSampleNotice: !error && !loading && !refreshing && loadState === LOAD_STATES.NO_SAMPLE,
    totalChecks: data?.total_checks ?? 0,
    topDeniedTools: data?.top_denied_tools ?? [],
    statCards: [
      { label: '总校验次数', value: data?.total_checks, color: 'text-gray-700' },
      { label: '允许次数', value: data?.allowed_count, color: 'text-green-600' },
      { label: '拦截次数', value: data?.denied_count, color: 'text-red-600' },
      { label: '调度器拦截', value: data?.scheduler_denied_count, color: 'text-orange-500' },
    ],
  }
}

export function buildActionPlanStatsView({
  data,
  loading,
  refreshing,
  stale,
  error,
} = {}) {
  const loadState = resolveLoadState({
    loading: loading || refreshing,
    error,
    hasData: Boolean(data),
    hasSamples: Number(data?.total_plans ?? 0) > 0,
    available: Boolean(data),
    stale,
  })

  return {
    loadState,
    showNoSampleNotice: !error && !loading && !refreshing && loadState === LOAD_STATES.NO_SAMPLE,
    totalPlans: data?.total_plans ?? 0,
    topActionTypes: data?.top_action_types ?? [],
    statCards: [
      { label: '总计划数', value: data?.total_plans, color: 'text-gray-700' },
      { label: '成功数', value: data?.success_count, color: 'text-green-600' },
      { label: '降级数', value: data?.fallback_count, color: 'text-orange-500' },
      {
        label: '成功率',
        value: data?.success_rate != null ? `${data.success_rate}%` : '—',
        color: data?.success_rate > 80 ? 'text-green-600' : 'text-red-500',
      },
    ],
  }
}
