import { resolveLoadState } from '../../models/loadState.js'
import { buildTokenMetricView } from '../../shared/tokenMetrics.js'
import { formatMode, formatReplyPosture } from '../../utils/digitalLife.js'

export const SYSTEM_CONFIG_WINDOW_OPTIONS = [
  { label: '1h', value: 60 },
  { label: '6h', value: 360 },
  { label: '24h', value: 1440 },
  { label: '7d', value: 10080 },
]

export const SYSTEM_CONFIG_FALLBACK_TEXT = '—'

export function getSystemConfigCollections({ routeData, callData, rateData } = {}) {
  return {
    routeStats: Array.isArray(routeData?.model_routes) ? routeData.model_routes : [],
    callStats: Array.isArray(callData?.models) ? callData.models : [],
    rateKeys: Array.isArray(rateData?.top_keys) ? rateData.top_keys : [],
  }
}

export function countNotLiveRuntimeSlots(runtimeSnapshot) {
  return Object.entries(runtimeSnapshot?.slot_status || {}).filter(
    ([key, status]) => key !== '_operator_privacy' && status !== 'live',
  ).length
}

export function getSystemConfigRuntimeLabels(runtimeSnapshot) {
  return {
    replyPosture: formatReplyPosture(runtimeSnapshot?.runtime_summary?.reply_posture?.style || SYSTEM_CONFIG_FALLBACK_TEXT),
    sysMode: formatMode(runtimeSnapshot?.sys_mode || SYSTEM_CONFIG_FALLBACK_TEXT),
  }
}

export function getSystemConfigUpdatedAt(lastUpdatedAt, runtimeUpdatedAt) {
  return Math.max(lastUpdatedAt || 0, runtimeUpdatedAt || 0) || null
}

export function buildSystemConfigLoadState({
  routeData,
  callData,
  rateData,
  healthData,
  retrievalRuntime,
  routeStats = [],
  callStats = [],
  loading = false,
  refreshing = false,
  error = null,
  stale = false,
} = {}) {
  return resolveLoadState({
    loading: loading || refreshing,
    error,
    hasData: Boolean(routeData || callData || rateData || healthData || retrievalRuntime),
    hasSamples: Boolean(routeStats.length || callStats.length || healthData?.total || retrievalRuntime?.latest),
    available: true,
    stale,
  })
}

export function buildSystemConfigHeroDescription({ sysMode, replyPosture, notLiveSlots }) {
  return `当前硬状态为 ${sysMode}，回复姿态为 ${replyPosture}，未在线槽位数量为 ${notLiveSlots}。`
}

export function buildSystemConfigHeroMetaItems({ healthData, rateData, retrievalRuntime } = {}) {
  return [
    `器官：${healthData ? `${healthData.up}/${healthData.total}` : SYSTEM_CONFIG_FALLBACK_TEXT}`,
    `未知器官：${healthData?.unknown ?? SYSTEM_CONFIG_FALLBACK_TEXT}`,
    `限流后端：${rateData?.backend_type || SYSTEM_CONFIG_FALLBACK_TEXT}`,
    `检索套件：${retrievalRuntime?.suite_profile || '自定义'}`,
  ]
}

export function getSystemConfigSampleSize({ routeStats = [], callStats = [], healthData } = {}) {
  return routeStats.length + callStats.length + (healthData?.total ?? 0)
}

export function buildSystemConfigMetricCards({ retrievalRuntime, rateData, routeStats = [], callStats = [], healthData } = {}) {
  return [
    {
      key: 'write_embedding',
      label: '写入嵌入模型',
      value: retrievalRuntime?.episodic_write_embedding_model_id || SYSTEM_CONFIG_FALLBACK_TEXT,
      iconKey: 'database',
      variant: 'stacked',
      tone: retrievalRuntime?.read_write_model_aligned ? 'text-green-600' : 'text-orange-500',
      hint: retrievalRuntime?.episodic_write_embedding_provider || SYSTEM_CONFIG_FALLBACK_TEXT,
    },
    {
      key: 'read_embedding',
      label: '读取嵌入模型',
      value: retrievalRuntime?.latest?.embedding_model_id || SYSTEM_CONFIG_FALLBACK_TEXT,
      iconKey: 'search',
      variant: 'stacked',
      tone: retrievalRuntime?.latest?.fallback_embedding_used ? 'text-orange-500' : 'text-blue-600',
      hint: retrievalRuntime?.latest?.effective_embedding_provider || SYSTEM_CONFIG_FALLBACK_TEXT,
    },
    {
      key: 'alignment',
      label: '读写一致性',
      value: retrievalRuntime?.read_write_model_aligned ? '对齐' : '漂移',
      iconKey: 'zap',
      variant: 'stacked',
      tone: retrievalRuntime?.read_write_model_aligned ? 'text-green-600' : 'text-orange-500',
      hint: retrievalRuntime?.suite_profile || '自定义',
    },
    {
      key: 'rate_backend',
      label: '限流后端',
      value: rateData?.backend_type || SYSTEM_CONFIG_FALLBACK_TEXT,
      iconKey: 'shield',
      variant: 'stacked',
      tone: rateData?.is_degraded ? 'text-orange-500' : 'text-green-600',
      hint: rateData?.is_degraded ? `降级：${rateData.degrade_reason}` : '正常',
    },
    {
      key: 'route_count',
      label: '模型路由数',
      value: routeStats.length || SYSTEM_CONFIG_FALLBACK_TEXT,
      iconKey: 'git_branch',
      variant: 'stacked',
      tone: 'text-blue-600',
    },
    {
      key: 'call_count',
      label: '调用模型数',
      value: callStats.length || SYSTEM_CONFIG_FALLBACK_TEXT,
      iconKey: 'cpu',
      variant: 'stacked',
      tone: 'text-purple-600',
    },
    {
      key: 'organ_health',
      label: '器官健康',
      value: healthData?.up ?? SYSTEM_CONFIG_FALLBACK_TEXT,
      iconKey: 'zap',
      variant: 'stacked',
      tone: 'text-green-600',
      hint: healthData ? `${healthData.total} 总计 / ${healthData.degraded} 降级 / ${healthData.down} 离线 / ${healthData.unknown} 未知` : '',
    },
  ]
}

export function buildSystemConfigRouteRows(routeStats) {
  const rows = Array.isArray(routeStats) ? routeStats : []
  return rows.map((route) => ({
    key: route?.model_id || route?.route_id,
    id: route?.model_id || route?.route_id,
    selectCount: route?.select_count ?? 0,
    degradeCount: route?.degrade_count ?? 0,
  }))
}

export function buildSystemConfigCallRows(callStats) {
  const rows = Array.isArray(callStats) ? callStats : []
  return rows.map((model) => {
    const callCount = model?.call_count ?? 0
    const totalTokenMetric = buildTokenMetricView({
      value: model?.total_tokens,
      measuredCalls: model?.total_tokens_measured_calls,
      totalCalls: callCount,
      isLowerBound: model?.total_tokens_is_lower_bound,
    })
    return {
      key: model?.model_id,
      modelId: model?.model_id,
      callCount,
      totalTokens: totalTokenMetric.value,
      totalTokensLabel: totalTokenMetric.label,
      totalTokensAvailable: totalTokenMetric.available,
      totalTokensIsLowerBound: totalTokenMetric.isLowerBound,
      totalTokensCoverageLabel: totalTokenMetric.coverageLabel,
      totalTokensDetailLabel: totalTokenMetric.detailLabel,
      latencyLabel: model?.avg_latency_ms != null ? `${Number(model.avg_latency_ms).toFixed(0)} ms` : SYSTEM_CONFIG_FALLBACK_TEXT,
    }
  })
}

export function buildSystemConfigRateKeyRows(rateKeys) {
  const rows = Array.isArray(rateKeys) ? rateKeys : []
  return rows.map((keyItem, index) => ({
    key: keyItem?.key || `rate-key-${index}`,
    label: keyItem?.key,
    current: keyItem?.current,
    limit: keyItem?.limit,
    exhausted: (keyItem?.remaining ?? 0) <= 0,
  }))
}

export function buildSystemConfigRetrievalRuntimeView(retrievalRuntime) {
  if (!retrievalRuntime) return null
  return {
    suiteProfile: retrievalRuntime.suite_profile || '自定义',
    readEmbeddingProvider: retrievalRuntime.latest?.effective_embedding_provider || SYSTEM_CONFIG_FALLBACK_TEXT,
  }
}

export function buildSystemConfigViewModel({
  routeData,
  callData,
  rateData,
  healthData,
  retrievalRuntime,
  runtimeSnapshot,
  loading = false,
  refreshing = false,
  error = null,
  stale = false,
  lastUpdatedAt = null,
  runtimeUpdatedAt = null,
} = {}) {
  const { routeStats, callStats, rateKeys } = getSystemConfigCollections({ routeData, callData, rateData })
  const notLiveSlots = countNotLiveRuntimeSlots(runtimeSnapshot)
  const runtimeLabels = getSystemConfigRuntimeLabels(runtimeSnapshot)
  const updatedAt = getSystemConfigUpdatedAt(lastUpdatedAt, runtimeUpdatedAt)
  const loadState = buildSystemConfigLoadState({
    routeData,
    callData,
    rateData,
    healthData,
    retrievalRuntime,
    routeStats,
    callStats,
    loading,
    refreshing,
    error,
    stale,
  })

  return {
    routeStats,
    callStats,
    rateKeys,
    notLiveSlots,
    ...runtimeLabels,
    updatedAt,
    loadState,
    heroDescription: buildSystemConfigHeroDescription({
      sysMode: runtimeLabels.sysMode,
      replyPosture: runtimeLabels.replyPosture,
      notLiveSlots,
    }),
    heroMetaItems: buildSystemConfigHeroMetaItems({ healthData, rateData, retrievalRuntime }),
    sampleSize: getSystemConfigSampleSize({ routeStats, callStats, healthData }),
    metricCards: buildSystemConfigMetricCards({ retrievalRuntime, rateData, routeStats, callStats, healthData }),
    routeRows: buildSystemConfigRouteRows(routeStats),
    callRows: buildSystemConfigCallRows(callStats),
    rateKeyRows: buildSystemConfigRateKeyRows(rateKeys),
    retrievalRuntimeView: buildSystemConfigRetrievalRuntimeView(retrievalRuntime),
  }
}
