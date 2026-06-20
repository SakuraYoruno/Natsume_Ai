import { normalizeSessionStatus, resolveSessionStatusTone } from '../../shared/statusMeta.js'
import { compactText, DASH, formatAlertTitle, formatDateTime } from '../../utils/digitalLife.js'
import { LOAD_STATES, resolveLoadState } from '../../models/loadState.js'
import { sanitizeRecentRequests } from '../../utils/sessionList.js'

export const SESSIONS_PAGE_SIZE = 20

export const SESSIONS_WINDOW_OPTIONS = [
  { label: '1h', value: 60, timeRange: '1h' },
  { label: '6h', value: 360, timeRange: '6h' },
  { label: '24h', value: 1440, timeRange: '24h' },
  { label: '7d', value: 10080, timeRange: '7d' },
]

export function getSessionsWindowLabel(minutes) {
  return SESSIONS_WINDOW_OPTIONS.find((option) => option.value === minutes)?.label || `${minutes}m`
}

export function getSuppressedSessionCount(rawItems, sessions) {
  return Math.max(0, (rawItems?.length || 0) - (sessions?.length || 0))
}

export function buildSessionsPaginationState({
  sessionsData = null,
  page = 1,
  pageSize = SESSIONS_PAGE_SIZE,
} = {}) {
  const total = sessionsData?.total || 0
  const currentPage = sessionsData?.page || page
  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  return { total, currentPage, totalPages }
}

export function buildSessionsLoadState({
  sessionsData = null,
  listError = null,
  loading = false,
  refreshing = false,
  total = 0,
  sessions = [],
  stale = false,
} = {}) {
  return resolveLoadState({
    loading: loading || refreshing,
    error: listError && !sessionsData ? new Error(listError) : null,
    hasData: Boolean(sessionsData),
    hasSamples: total > 0 || sessions.length > 0,
    available: Boolean(sessionsData),
    stale,
  })
}

export function buildSessionsSummary(sessions = []) {
  const safeSessions = Array.isArray(sessions) ? sessions : []
  const healthyCount = safeSessions.filter((item) => normalizeSessionStatus(item?.status) === 'healthy').length
  const degradedCount = safeSessions.filter((item) => normalizeSessionStatus(item?.status) === 'degraded').length
  const errorCount = safeSessions.filter((item) => normalizeSessionStatus(item?.status) === 'error').length
  const latencyValues = safeSessions
    .map((item) => Number(item?.total_latency_ms))
    .filter((value) => Number.isFinite(value) && value > 0)
  const avgLatency = latencyValues.length > 0
    ? Math.round(latencyValues.reduce((sum, value) => sum + value, 0) / latencyValues.length)
    : null

  return { healthyCount, degradedCount, errorCount, avgLatency }
}

export function buildSessionsPulseNodes({
  sessions = [],
  summary = {},
  windowMinutes = 1440,
  pageSize = SESSIONS_PAGE_SIZE,
} = {}) {
  return [
    { label: '当前页', value: `${sessions.length}/${pageSize}`, tone: 'primary', active: true },
    { label: '顺流', value: String(summary.healthyCount || 0), tone: 'teal', active: (summary.healthyCount || 0) > 0 },
    { label: '回撤', value: String(summary.degradedCount || 0), tone: 'amber', active: (summary.degradedCount || 0) > 0 },
    { label: '异常', value: String(summary.errorCount || 0), tone: 'rose', active: (summary.errorCount || 0) > 0 },
    {
      label: '平均耗时',
      value: summary.avgLatency != null ? `${summary.avgLatency}ms` : '--',
      tone: 'violet',
      active: summary.avgLatency != null,
    },
    { label: '时间窗', value: getSessionsWindowLabel(windowMinutes), tone: 'gray', active: true },
  ]
}

export function buildSessionsHeroTitle({ windowMinutes = 1440, total = 0 } = {}) {
  return `最近 ${getSessionsWindowLabel(windowMinutes)} 共 ${total} 条请求`
}

export function buildSessionsHeroDescription({ keyword = '', status = '' } = {}) {
  if (keyword || status) {
    return `当前过滤生效${keyword ? `，关键词“${keyword}”` : ''}${status ? `，状态 ${status}` : ''}。`
  }
  return '可按关键词、状态和时间窗口筛选请求，再进入神经链路查看完整传导路径。'
}

export function buildSessionsHeroMetaItems({
  currentPage = 1,
  totalPages = 1,
  summary = {},
} = {}) {
  return [
    `当前页：${currentPage} / ${totalPages}`,
    `顺流：${summary.healthyCount || 0}`,
    `回撤：${summary.degradedCount || 0}`,
    `异常：${summary.errorCount || 0}`,
  ]
}

export function buildSessionsSampleMeta({
  sessionsLoadState,
  windowMinutes = 1440,
  total = 0,
  lastUpdatedAt = null,
} = {}) {
  return {
    loadState: sessionsLoadState,
    windowMinutes,
    sampleSize: total,
    sampleLabel: '请求',
    updatedAt: lastUpdatedAt,
    source: 'sessions',
    definition: '会话页按时间窗、关键词和状态聚合最近请求流；筛掉的噪声记录会单独提示，不会混入有效样本。',
  }
}

export function shouldShowSessionsNoSampleNotice({
  listError = null,
  loading = false,
  refreshing = false,
  sessionsLoadState = null,
} = {}) {
  return !listError && !loading && !refreshing && sessionsLoadState === LOAD_STATES.NO_SAMPLE
}

export function buildSessionsQueryView({
  sessionsData = null,
  listError = null,
  loading = false,
  refreshing = false,
  stale = false,
  lastUpdatedAt = null,
  page = 1,
  pageSize = SESSIONS_PAGE_SIZE,
  keyword = '',
  status = '',
  windowMinutes = 1440,
} = {}) {
  const rawItems = sessionsData?.items || []
  const sessions = sanitizeRecentRequests(rawItems, { keyword })
  const suppressedCount = getSuppressedSessionCount(rawItems, sessions)
  const pagination = buildSessionsPaginationState({
    sessionsData,
    page,
    pageSize,
  })
  const sessionsLoadState = buildSessionsLoadState({
    sessionsData,
    listError,
    loading,
    refreshing,
    total: pagination.total,
    sessions,
    stale,
  })
  const summary = buildSessionsSummary(sessions)
  const pulseNodes = buildSessionsPulseNodes({
    sessions,
    summary,
    windowMinutes,
    pageSize,
  })
  const heroTitle = buildSessionsHeroTitle({ windowMinutes, total: pagination.total })
  const heroDescription = buildSessionsHeroDescription({ keyword, status })
  const heroMetaItems = buildSessionsHeroMetaItems({
    currentPage: pagination.currentPage,
    totalPages: pagination.totalPages,
    summary,
  })

  return {
    rawItems,
    sessions,
    suppressedCount,
    total: pagination.total,
    currentPage: pagination.currentPage,
    totalPages: pagination.totalPages,
    sessionsLoadState,
    summary,
    pulseNodes,
    heroTitle,
    heroDescription,
    heroMetaItems,
    showNoSampleNotice: shouldShowSessionsNoSampleNotice({
      listError,
      loading,
      refreshing,
      sessionsLoadState,
    }),
    sampleMeta: buildSessionsSampleMeta({
      sessionsLoadState,
      windowMinutes,
      total: pagination.total,
      lastUpdatedAt,
    }),
  }
}

export function buildSessionsExportParams({
  windowMinutes = 1440,
  keyword = '',
  status = '',
} = {}) {
  return {
    window_minutes: windowMinutes,
    keyword: keyword || undefined,
    status: status || undefined,
  }
}

export function getPreviousSessionsPage(currentPage) {
  return Math.max(1, currentPage - 1)
}

export function getNextSessionsPage(currentPage, totalPages) {
  return Math.min(totalPages, currentPage + 1)
}

export function buildSessionTracePath(requestId) {
  return `/observability/trace/${requestId}`
}

export function buildSessionCardView(item = {}) {
  const tone = resolveSessionStatusTone(item.status)
  const preview = compactText(item.input_preview || '暂无输入摘要', 180)
  return {
    tone,
    preview,
    requestLabel: item.request_id?.slice(0, 18) || DASH,
    sessionLabel: item.session_id || DASH,
    timeLabel: formatDateTime(item.ts_ms),
    normalizedStatus: normalizeSessionStatus(item.status),
    statusLabel: item.status || 'UNKNOWN',
    latencyLabel: item.total_latency_ms != null ? `${item.total_latency_ms} ms` : DASH,
    reasonLabel: item.reason_code ? formatAlertTitle(item.reason_code) : '暂无显式原因',
  }
}
