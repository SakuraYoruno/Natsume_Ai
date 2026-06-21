import { resolveLoadState } from '../../models/loadState.js'

const TRACE_ISSUE_STATUSES = new Set(['DEGRADED', 'FAIL', 'FAILED', 'ERROR', 'DOWN', 'REJECT', 'REJECTED'])

export function truncateDebugText(value, maxLength = 160) {
  const normalized = String(value || '').trim().replace(/\s+/g, ' ')
  if (!normalized) return ''
  return normalized.length > maxLength ? `${normalized.slice(0, maxLength - 1)}…` : normalized
}

export function getChatDebugChainEvents(events = []) {
  const items = Array.isArray(events) ? events : []
  return items.filter((event) => event.node_id || String(event.phase || '').toUpperCase() === 'NODE')
}

export function getLatestTraceNode(traceSnapshot) {
  const nodes = Array.isArray(traceSnapshot?.nodes) ? [...traceSnapshot.nodes] : []
  if (nodes.length === 0) return null
  nodes.sort((a, b) => (a?.ts_ms ?? 0) - (b?.ts_ms ?? 0))
  return nodes[nodes.length - 1]
}

export function getTraceIssueCount(traceSnapshot) {
  return Array.isArray(traceSnapshot?.nodes)
    ? traceSnapshot.nodes.filter((node) => TRACE_ISSUE_STATUSES.has(String(node?.status || '').toUpperCase())).length
    : 0
}

export function getLatestChatContent(chatHistory, role) {
  return (Array.isArray(chatHistory) ? chatHistory : [])
    .filter((item) => item.role === role)
    .slice(-1)[0]?.content
}

export function buildChatDebugTracePreviews({
  traceSummary,
  chatHistory,
  currentResponse,
}) {
  return {
    traceInputPreview: truncateDebugText(
      traceSummary?.input_text || getLatestChatContent(chatHistory, 'user'),
      180
    ),
    traceOutputPreview: truncateDebugText(
      traceSummary?.output_text
      || traceSummary?.failure_event
      || currentResponse
      || getLatestChatContent(chatHistory, 'assistant'),
      180
    ),
  }
}

export function buildChatDebugHeroMetaItems({
  chatHistory,
  sseEvents,
  traceSnapshot,
  chainEventCount,
  requestId,
}) {
  return [
    `历史消息：${Array.isArray(chatHistory) ? chatHistory.length : 0}`,
    `SSE 事件：${Array.isArray(sseEvents) ? sseEvents.length : 0}`,
    traceSnapshot?.status ? `Trace：${traceSnapshot.status}` : `链路节点：${chainEventCount}`,
    requestId ? '已有 Request ID' : '等待新请求',
  ]
}

export function buildChatDebugTraceLoadState({
  traceSnapshot = null,
  traceError = null,
  traceLoading = false,
  traceRefreshing = false,
  traceStale = false,
  requestId = null,
} = {}) {
  return resolveLoadState({
    loading: traceLoading || traceRefreshing,
    error: traceError,
    hasData: Boolean(traceSnapshot),
    hasSamples: (traceSnapshot?.node_count ?? traceSnapshot?.nodes?.length ?? 0) > 0,
    available: Boolean(requestId),
    stale: traceStale,
  })
}

export function buildChatDebugTraceView({
  sseEvents = [],
  chatHistory = [],
  currentResponse = '',
  traceSnapshot = null,
  traceError = null,
  traceLoading = false,
  traceRefreshing = false,
  traceStale = false,
  requestId = null,
} = {}) {
  const chainEvents = getChatDebugChainEvents(sseEvents)
  const latestEvent = Array.isArray(sseEvents) ? (sseEvents[sseEvents.length - 1] ?? null) : null
  const traceSummary = traceSnapshot?.summary ?? {}
  const traceLatestNode = getLatestTraceNode(traceSnapshot)
  const traceIssueCount = getTraceIssueCount(traceSnapshot)
  const traceNodeCount = traceSnapshot?.node_count ?? chainEvents.length
  const traceBusy = Boolean(traceLoading || traceRefreshing)
  const tracePreviews = buildChatDebugTracePreviews({
    traceSummary,
    chatHistory,
    currentResponse,
  })
  const traceLoadState = buildChatDebugTraceLoadState({
    traceSnapshot,
    traceError,
    traceLoading,
    traceRefreshing,
    traceStale,
    requestId,
  })

  return {
    chainEvents,
    latestEvent,
    traceSummary,
    traceLatestNode,
    traceIssueCount,
    traceNodeCount,
    traceBusy,
    traceInputPreview: tracePreviews.traceInputPreview,
    traceOutputPreview: tracePreviews.traceOutputPreview,
    traceLoadState,
    heroMetaItems: buildChatDebugHeroMetaItems({
      chatHistory,
      sseEvents,
      traceSnapshot,
      chainEventCount: chainEvents.length,
      requestId,
    }),
  }
}
