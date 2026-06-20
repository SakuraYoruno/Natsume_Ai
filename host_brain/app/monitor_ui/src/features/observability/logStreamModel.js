import { formatTime } from '../../shared/formatters.js'
import { LOAD_STATES, resolveLoadState } from '../../models/loadState.js'
import { classifyLogLevel, LOG_LEVEL_STYLES } from '../../shared/statusMeta.js'
import { formatAlertTitle, humanizeToken } from '../../utils/digitalLife.js'

export const LOG_STREAM_MAX_ITEMS = 500
export const LOG_STREAM_MAX_POLLING_INTERVAL_SECONDS = 15

export const LOG_STREAM_TIME_OPTIONS = Object.freeze({
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  fractionalSecondDigits: 3,
})

export function clampLogPollingInterval(pollingInterval) {
  return Math.min(pollingInterval, LOG_STREAM_MAX_POLLING_INTERVAL_SECONDS)
}

export function formatLogTime(tsMs) {
  return formatTime(tsMs, { placeholder: '', options: LOG_STREAM_TIME_OPTIONS })
}

export function getLogBatchEntries(logBatch) {
  return logBatch?.recent_errors || logBatch?.errors || []
}

export function getLogEntryKey(entry) {
  return `${entry?.ts_ms}-${entry?.reason_code}-${entry?.stage}`
}

export function mergeLogEntries(previousEntries, incomingEntries, maxItems = LOG_STREAM_MAX_ITEMS) {
  const previous = Array.isArray(previousEntries) ? previousEntries : []
  const incoming = Array.isArray(incomingEntries) ? incomingEntries : []
  if (!incoming.length) return previous
  const existing = new Set(previous.map((item) => getLogEntryKey(item)))
  const next = incoming.filter((item) => !existing.has(getLogEntryKey(item)))
  return [...previous, ...next].slice(-maxItems)
}

export function buildLogLineText(entry) {
  return `[${entry?.stage ? humanizeToken(entry.stage) : '—'}] ${entry?.reason_code ? formatAlertTitle(entry.reason_code) : '—'} | ${entry?.detail || ''}`
}

export function splitHighlightedLogText(text, highlight) {
  if (!highlight) return [{ text, highlighted: false }]
  const escaped = highlight.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const regex = new RegExp(`(${escaped})`, 'gi')
  return String(text).split(regex).map((part, index) => ({
    text: part,
    highlighted: index % 2 === 1,
  }))
}

export function buildLogLineView(entry, highlight = '') {
  const level = classifyLogLevel(entry)
  const style = LOG_LEVEL_STYLES[level] || LOG_LEVEL_STYLES.INFO
  const text = buildLogLineText(entry)
  return {
    level,
    style,
    text,
    timeLabel: formatLogTime(entry?.ts_ms),
    parts: splitHighlightedLogText(text, highlight),
  }
}

export function filterLogEntries(entries, { keyword = '', levelFilter = 'ALL' } = {}) {
  const normalizedKeyword = keyword.toLowerCase()
  return (Array.isArray(entries) ? entries : []).filter((entry) => {
    if (normalizedKeyword && !JSON.stringify(entry).toLowerCase().includes(normalizedKeyword)) return false
    if (levelFilter === 'ERROR' && classifyLogLevel(entry) !== 'ERROR') return false
    if (levelFilter === 'WARNING' && classifyLogLevel(entry) !== 'WARNING') return false
    return true
  })
}

export function buildLogHeroMetaItems({
  filteredCount = 0,
  cachedCount = 0,
  pollingInterval = 15,
  autoScroll = true,
} = {}) {
  return [
    `当前显示：${filteredCount} 条`,
    `缓存总量：${cachedCount} 条`,
    `拉取周期：${clampLogPollingInterval(pollingInterval)}s`,
    autoScroll ? '自动滚动：开启' : '自动滚动：关闭',
  ]
}

export function buildLogLiveStatusLabel({ paused = false, pollingInterval = 15 } = {}) {
  return paused ? '⏸ 已暂停' : `● 实时：${clampLogPollingInterval(pollingInterval)}s`
}

export function buildLogLoadState({
  logBatch = null,
  logs = [],
  batchEntries = getLogBatchEntries(logBatch),
  batchError = null,
  loading = false,
  refreshing = false,
  stale = false,
} = {}) {
  const cachedLogs = Array.isArray(logs) ? logs : []
  const entries = Array.isArray(batchEntries) ? batchEntries : []
  const hasData = Boolean(logBatch || cachedLogs.length > 0)
  return resolveLoadState({
    loading: loading || refreshing,
    error: batchError && cachedLogs.length === 0 ? new Error(batchError) : null,
    hasData,
    hasSamples: cachedLogs.length > 0 || entries.length > 0,
    available: hasData,
    stale,
  })
}

export function buildLogSampleMeta({
  logLoadState,
  logs = [],
  lastUpdatedAt = null,
  windowMinutes = 60,
} = {}) {
  return {
    loadState: logLoadState,
    windowMinutes,
    sampleSize: Array.isArray(logs) ? logs.length : 0,
    sampleLabel: '日志',
    updatedAt: lastUpdatedAt,
    source: 'errors',
    definition: '日志流页会持续拉取最近错误批次并在页面内累积成滚动日志；暂停只会停止轮询，不会清空已收集的日志。',
  }
}

export function buildLogNoticeView({
  batchError = null,
  logs = [],
  loading = false,
  refreshing = false,
  logLoadState,
} = {}) {
  return {
    batchError,
    batchErrorVariant: Array.isArray(logs) && logs.length > 0 ? 'warning' : 'error',
    noSample: !batchError && !loading && !refreshing && logLoadState === LOAD_STATES.NO_SAMPLE,
  }
}

export function buildLogStreamViewModel({
  logBatch = null,
  logs = [],
  filteredCount = 0,
  batchError = null,
  loading = false,
  refreshing = false,
  stale = false,
  lastUpdatedAt = null,
  pollingInterval = 15,
  autoScroll = true,
  windowMinutes = 60,
} = {}) {
  const batchEntries = getLogBatchEntries(logBatch)
  const logLoadState = buildLogLoadState({
    logBatch,
    logs,
    batchEntries,
    batchError,
    loading,
    refreshing,
    stale,
  })

  return {
    batchEntries,
    logLoadState,
    heroMetaItems: buildLogHeroMetaItems({
      filteredCount,
      cachedCount: Array.isArray(logs) ? logs.length : 0,
      pollingInterval,
      autoScroll,
    }),
    sampleMeta: buildLogSampleMeta({
      logLoadState,
      logs,
      lastUpdatedAt,
      windowMinutes,
    }),
    noticeView: buildLogNoticeView({
      batchError,
      logs,
      loading,
      refreshing,
      logLoadState,
    }),
  }
}
