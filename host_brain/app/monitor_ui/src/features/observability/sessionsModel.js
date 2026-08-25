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

function getSessionLatency(item) {
  const value = Number(item?.total_latency_ms)
  return Number.isFinite(value) && value > 0 ? value : null
}

function getSessionPreview(item) {
  return compactText(item?.input_preview || item?.message || item?.request_id || '暂无输入摘要', 110)
}

function findLeadSession(sessions = []) {
  const safeSessions = Array.isArray(sessions) ? sessions : []
  const issueSession = safeSessions.find((item) => normalizeSessionStatus(item?.status) === 'error')
    || safeSessions.find((item) => normalizeSessionStatus(item?.status) === 'degraded')
  if (issueSession) return { item: issueSession, reason: 'issue' }

  const slowest = safeSessions
    .map((item) => ({ item, latency: getSessionLatency(item) }))
    .filter((entry) => entry.latency != null)
    .sort((a, b) => b.latency - a.latency)[0]
  if (slowest?.item) return { item: slowest.item, reason: 'slow' }

  return safeSessions[0] ? { item: safeSessions[0], reason: 'latest' } : { item: null, reason: 'empty' }
}

export function buildSessionsTriageRailModel({
  sessions = [],
  summary = {},
  total = 0,
  keyword = '',
  status = '',
  windowMinutes = 1440,
  loading = false,
  refreshing = false,
  listError = null,
} = {}) {
  const safeSessions = Array.isArray(sessions) ? sessions : []
  const activeFilter = Boolean(keyword || status)
  const { item: leadSession, reason } = findLeadSession(safeSessions)
  const hasSessions = safeSessions.length > 0
  const issueCount = (summary.errorCount || 0) + (summary.degradedCount || 0)
  const slowestLatency = safeSessions
    .map(getSessionLatency)
    .filter((value) => value != null)
    .sort((a, b) => b - a)[0] ?? null

  let railStatus = 'healthy'
  let headline = '会话流稳定，优先打开最新请求核对链路'
  let description = '当前页没有明显异常。可以从推荐 request 进入 Trace，确认输入、阶段链路和 runtime 证据是否完整。'
  let primaryAction = leadSession
    ? {
        kind: 'openSession',
        requestId: leadSession.request_id,
        label: '打开推荐 Trace',
        detail: `${leadSession.request_id || DASH} · ${getSessionPreview(leadSession)}`,
      }
    : {
        kind: 'refresh',
        label: '刷新会话流',
        detail: '等待新的请求沉积后再进入 Trace。',
      }

  if (listError && !hasSessions) {
    railStatus = 'blocked'
    headline = '会话流不可用，先恢复数据读取'
    description = 'Sessions 接口当前没有可用样本。先刷新数据源，恢复后再进入 Trace 详情。'
    primaryAction = { kind: 'refresh', label: '重新拉取 Sessions', detail: listError }
  } else if (summary.errorCount > 0) {
    railStatus = 'attention'
    headline = `先处理 ${summary.errorCount} 条异常请求`
    description = `${getSessionPreview(leadSession)} 是当前最值得先打开的会话。先进入 Trace 看节点错误、护栏回放和流式事件。`
  } else if (summary.degradedCount > 0) {
    railStatus = 'degraded'
    headline = `先复核 ${summary.degradedCount} 条拒绝/降级请求`
    description = `${getSessionPreview(leadSession)} 可能来自策略拒绝、检索漂移或上下文缺口。先进入 Trace 再对照 Immune Ops。`
  } else if (!hasSessions) {
    railStatus = activeFilter ? 'empty-filter' : 'empty'
    headline = activeFilter ? '当前筛选没有命中会话' : '等待新的会话沉积'
    description = activeFilter
      ? '筛选条件过窄时会看起来像空页。先清空筛选，或扩大时间窗后再排查。'
      : '没有请求样本时先保留空态和刷新动作，避免误判页面失效。'
    primaryAction = activeFilter
      ? { kind: 'clearFilters', label: '清空筛选', detail: '恢复全部状态和关键词。' }
      : { kind: 'refresh', label: '刷新会话流', detail: '等待新请求进入系统。' }
  } else if (reason === 'slow' && slowestLatency >= 1000) {
    railStatus = 'slow'
    headline = `优先查看最慢请求：${slowestLatency} ms`
    description = `${getSessionPreview(leadSession)} 的耗时明显偏高。进入 Trace 后先看检索、模型和代理阶段。`
  }

  const secondaryAction = (() => {
    if (activeFilter && hasSessions) return { kind: 'clearFilters', label: '清空筛选', detail: '恢复全部会话样本。' }
    if ((summary.errorCount || 0) > 0) return { kind: 'setStatus', status: 'error', label: '只看异常', detail: '收敛到 error 请求。' }
    if ((summary.degradedCount || 0) > 0) return { kind: 'setStatus', status: 'rejected', label: '只看拒绝', detail: '收敛到 rejected 请求。' }
    return { kind: 'refresh', label: refreshing || loading ? '刷新中' : '刷新', detail: '重新拉取当前时间窗。' }
  })()

  return {
    status: railStatus,
    eyebrow: 'Sessions Triage',
    title: '会话排障入口',
    headline,
    description,
    primaryAction,
    secondaryAction,
    cards: [
      {
        key: 'visible',
        label: '当前页样本',
        value: `${safeSessions.length}/${total || safeSessions.length}`,
        detail: `时间窗 ${getSessionsWindowLabel(windowMinutes)}。`,
        tone: hasSessions ? 'ready' : 'blocked',
      },
      {
        key: 'issues',
        label: '异常/拒绝',
        value: String(issueCount),
        detail: `异常 ${summary.errorCount || 0} · 拒绝 ${summary.degradedCount || 0}`,
        tone: issueCount > 0 ? 'degraded' : 'ready',
      },
      {
        key: 'latency',
        label: '平均耗时',
        value: summary.avgLatency != null ? `${summary.avgLatency} ms` : DASH,
        detail: slowestLatency != null ? `最慢 ${slowestLatency} ms` : '暂无耗时样本。',
        tone: slowestLatency != null && slowestLatency >= 1000 ? 'degraded' : 'ready',
      },
      {
        key: 'lead',
        label: '推荐 request',
        value: leadSession?.request_id?.slice(0, 18) || DASH,
        detail: leadSession ? getSessionPreview(leadSession) : '暂无可下钻请求。',
        tone: leadSession ? 'ready' : 'blocked',
      },
    ],
    gates: [
      {
        label: '数据',
        state: listError && !hasSessions ? 'blocked' : (hasSessions ? 'ready' : 'warning'),
        detail: listError && !hasSessions ? 'Sessions 接口不可用。' : (hasSessions ? '会话样本已可读。' : '等待样本进入。'),
      },
      {
        label: '筛选',
        state: activeFilter ? 'warning' : 'ready',
        detail: activeFilter ? '筛选已生效，可能隐藏其他异常。' : '当前查看全部状态。',
      },
      {
        label: 'Trace',
        state: leadSession?.request_id ? 'ready' : 'blocked',
        detail: leadSession?.request_id ? '推荐请求可直接下钻。' : '缺少 request_id。',
      },
      {
        label: '节律',
        state: refreshing || loading ? 'warning' : 'ready',
        detail: refreshing || loading ? '会话流正在刷新。' : '当前快照稳定。',
      },
    ],
  }
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
