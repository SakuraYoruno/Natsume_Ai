import { resolveLoadState } from '../../models/loadState.js'

export const METRICS_TIME_RANGES = [
  { label: '1h', value: '1h', minutes: 60 },
  { label: '6h', value: '6h', minutes: 360 },
  { label: '24h', value: '24h', minutes: 1440 },
  { label: '7d', value: '7d', minutes: 10080 },
]

export const METRICS_FALLBACK_TEXT = '—'

export function getMetricsRangeConfig(timeRange) {
  return METRICS_TIME_RANGES.find((range) => range.value === timeRange) ?? METRICS_TIME_RANGES[0]
}

export function getMetricsSummary(data) {
  return data?.summary ?? {}
}

export function formatMetricsPercent(value) {
  if (value == null) return METRICS_FALLBACK_TEXT
  const numeric = Number(value)
  return Number.isFinite(numeric) ? `${numeric.toFixed(1)}%` : METRICS_FALLBACK_TEXT
}

export function buildMetricsTimeseriesView(data) {
  const timeseries = data?.timeseries ?? {}
  return {
    xData: timeseries.timestamps ?? [],
    requestSeries: timeseries.total ?? [],
    rejectionSeries: timeseries.rejections ?? [],
    rejectionRateSeries: timeseries.rejection_rate ?? [],
  }
}

export function buildMetricsReasonCodeChartView(data) {
  const rows = Array.isArray(data?.reason_codes) ? data.reason_codes : []
  return {
    categories: rows.map((row) => row?.code),
    counts: rows.map((row) => row?.count),
  }
}

export function buildMetricsLatencyChartView(data) {
  const rows = Array.isArray(data?.latency) ? data.latency : []
  return {
    services: rows.map((row) => row?.service),
    p50: rows.map((row) => row?.p50),
    p95: rows.map((row) => row?.p95),
  }
}

export function buildMetricsLoadState({
  data,
  summary = {},
  reasonCodeCategories = [],
  latencyServices = [],
  loading = false,
  refreshing = false,
  error = null,
  stale = false,
} = {}) {
  return resolveLoadState({
    loading: loading || refreshing,
    error,
    hasData: Boolean(data),
    hasSamples: (
      (summary.total_requests ?? 0) > 0
      || reasonCodeCategories.length > 0
      || latencyServices.length > 0
    ),
    available: true,
    stale,
  })
}

export function isMetricsInitialState({ loading = false, refreshing = false, error = null, summary = {} } = {}) {
  return !loading && !refreshing && !error && (summary.total_requests ?? 0) === 0
}

export function buildMetricsHeroMetaItems({ timeRange, summary = {}, isMock = false } = {}) {
  return [
    `时间窗口：${timeRange}`,
    `总请求：${summary.total_requests ?? 0}`,
    `总拒绝：${summary.total_rejections ?? 0}`,
    `拒绝率：${formatMetricsPercent(summary.rejection_rate)}`,
    isMock ? '当前为兜底样本' : '当前为实时数据',
  ]
}

export function buildMetricsSummaryCards(summary = {}) {
  return [
    { label: '总请求数', value: summary.total_requests ?? METRICS_FALLBACK_TEXT, color: 'text-gray-800' },
    { label: '总拒绝数', value: summary.total_rejections ?? METRICS_FALLBACK_TEXT, color: 'text-orange-500' },
    { label: '拒绝率', value: formatMetricsPercent(summary.rejection_rate), color: 'text-red-500' },
  ]
}

export function buildRateLimitBackendView(rateLimitData) {
  if (!rateLimitData) return null
  const topKeys = Array.isArray(rateLimitData.top_keys) ? rateLimitData.top_keys : []
  const degraded = Boolean(rateLimitData.is_degraded)

  return {
    backendType: rateLimitData.backend_type,
    degraded,
    degradeReason: rateLimitData.degrade_reason,
    badgeClassName: degraded
      ? 'bg-orange-50 text-orange-700 border-orange-200'
      : 'bg-green-50 text-green-700 border-green-200',
    totalKeys: rateLimitData.total_keys,
    activeKeys: rateLimitData.active_keys,
    statusLabel: degraded ? '降级' : '正常',
    statusClassName: degraded ? 'text-orange-500' : 'text-green-600',
    topKeyRows: topKeys.map((row, index) => ({
      key: row?.key || `rate-key-${index}`,
      name: row?.key,
      currentCount: row?.current_count,
      windowLabel: row?.window_ms != null ? `${row.window_ms / 1000}s` : METRICS_FALLBACK_TEXT,
    })),
  }
}

export function buildMetricsOverviewViewModel({
  data,
  rateLimitData,
  timeRange = '1h',
  rangeConfig = null,
  loading = false,
  refreshing = false,
  error = null,
  stale = false,
  isMock = false,
} = {}) {
  const summary = getMetricsSummary(data)
  const timeseriesView = buildMetricsTimeseriesView(data)
  const reasonCodeChart = buildMetricsReasonCodeChartView(data)
  const latencyChart = buildMetricsLatencyChartView(data)

  return {
    rangeConfig: rangeConfig ?? getMetricsRangeConfig(timeRange),
    summary,
    ...timeseriesView,
    reasonCodeCategories: reasonCodeChart.categories,
    reasonCodeCounts: reasonCodeChart.counts,
    latencyServices: latencyChart.services,
    latencyP50: latencyChart.p50,
    latencyP95: latencyChart.p95,
    pageLoadState: buildMetricsLoadState({
      data,
      summary,
      reasonCodeCategories: reasonCodeChart.categories,
      latencyServices: latencyChart.services,
      loading,
      refreshing,
      error,
      stale,
    }),
    isInitialState: isMetricsInitialState({ loading, refreshing, error, summary }),
    heroMetaItems: buildMetricsHeroMetaItems({ timeRange, summary, isMock }),
    summaryCards: buildMetricsSummaryCards(summary),
    rateLimitView: buildRateLimitBackendView(rateLimitData),
  }
}
