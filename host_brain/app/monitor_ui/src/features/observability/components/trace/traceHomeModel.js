import { GROUPS, buildImmuneStageMeta, getTraceGroupNodeIds, getTraceGroupState } from './traceFlowModel.js'

export function buildTraceRecentRequests({
  recentKeyword = '',
  recentRequestsBase = [],
  liveTraceItems = [],
} = {}) {
  if (recentKeyword) return recentRequestsBase
  const previousById = new Map(recentRequestsBase.map((item) => [item.request_id, item]))
  const merged = liveTraceItems
    .map((item) => item?.session)
    .filter(Boolean)
    .map((session) => ({
      ...(previousById.get(session.request_id) ?? {}),
      ...session,
    }))
  recentRequestsBase.forEach((session) => {
    if (!merged.some((item) => item.request_id === session.request_id)) {
      merged.push(session)
    }
  })
  return merged.slice(0, 12)
}

export function getSelectedTraceSessionForQuery({
  liveTraceItems = [],
  recentRequests = [],
  selectedRequestId,
} = {}) {
  return liveTraceItems.find((item) => item?.session?.request_id === selectedRequestId)?.session
    ?? recentRequests.find((item) => item?.request_id === selectedRequestId)
    ?? null
}

export function buildTraceTribeFeedParams({
  selectedRequestId,
  selectedSessionIdForQuery,
  selectedConversationIdForQuery,
} = {}) {
  const params = { limit: selectedRequestId ? 200 : 60 }
  if (selectedRequestId) {
    params.request_id = selectedRequestId
  } else if (selectedSessionIdForQuery) {
    params.session_id = selectedSessionIdForQuery
  } else if (selectedConversationIdForQuery) {
    params.conversation_id = selectedConversationIdForQuery
  }
  return params
}

export function buildTraceLiveSnapshotParams({
  liveTraceLimit = 6,
  recentKeyword = '',
} = {}) {
  return {
    limit: liveTraceLimit,
    window_minutes: 1440,
    active_within_ms: 60000,
    keyword: recentKeyword || undefined,
  }
}

export function buildTraceLiveFeedConnectParams(options = {}) {
  return {
    ...buildTraceLiveSnapshotParams(options),
    interval_ms: 1000,
  }
}

export function getTracePayloadItems(payload) {
  return Array.isArray(payload?.items) ? payload.items : []
}

export function pruneTraceTimelineEntriesForSnapshot(previous = {}, items = []) {
  if (items.length === 0) return previous
  const activeRequestIds = new Set(items.map((item) => item?.session?.request_id).filter(Boolean))
  if (activeRequestIds.size === 0) return {}
  const next = {}
  for (const [requestId, entries] of Object.entries(previous)) {
    if (activeRequestIds.has(requestId)) {
      next[requestId] = entries
    }
  }
  return next
}

export function buildTraceInfoFromTrace(trace) {
  if (!trace) return null
  return {
    status: trace.status,
    latency: trace.total_latency_ms,
    nodeCount: (trace.nodes ?? []).length,
  }
}

export function buildTraceSearchState(inputId = '') {
  const value = String(inputId || '').trim()
  const looksLikeRequestId = value.startsWith('req_')
  return {
    value,
    looksLikeRequestId,
    action: !value ? 'clear' : looksLikeRequestId ? 'load' : 'filter',
    submitLabel: looksLikeRequestId ? '显示总览图' : '筛选请求',
  }
}

export function buildTraceDetailPath(requestId) {
  return `/observability/trace/${requestId}`
}

export function getTraceOverviewFocusAction({
  requestId,
  traceData,
  liveTraceItems = [],
} = {}) {
  if (!requestId) return { type: 'none' }
  if (traceData?.request_id === requestId) {
    return { type: 'reveal-current', requestId }
  }
  const matchedItem = liveTraceItems.find((item) => item?.session?.request_id === requestId)
  if (matchedItem?.trace) {
    return { type: 'select-live', item: matchedItem, requestId }
  }
  return { type: 'load', requestId }
}

export function getLiveTraceItemFocusAction({ item, showOverview = true } = {}) {
  const requestId = item?.session?.request_id
  if (!requestId) return { type: 'none' }
  if (!item.trace) {
    return { type: 'load', requestId, showOverview }
  }
  return { type: 'select-live', item, showOverview }
}

export function buildTraceGroupStats(traceData) {
  return GROUPS.map((group) => {
    const traceGroupState = traceData ? getTraceGroupState(group, traceData) : null
    const activeCount = traceGroupState ? traceGroupState.activeCount : 0
    const totalNodeCount = traceData ? getTraceGroupNodeIds(group).length : group.nodeIds.length
    const hasError = traceGroupState
      ? traceGroupState.groupNodes.some((node) => ['FAIL', 'ERROR', 'DOWN'].includes((node.status ?? '').toUpperCase()))
      : false
    const immuneMeta = group.id === 'immune'
      ? buildImmuneStageMeta(traceGroupState?.groupNodes ?? [])
      : null
    return { ...group, activeCount, totalNodeCount, hasError, immuneMeta }
  })
}

export function getTraceStatusHealth(traceInfo) {
  const status = String(traceInfo?.status || '').toUpperCase()
  return {
    status,
    healthy: ['OK', 'UP', 'SUCCESS'].includes(status),
  }
}

export function buildSelectedTraceRequestContext({
  liveTraceItems = [],
  recentRequests = [],
  selectedRequestId,
  traceData,
  liveTraceTimelineEntries = {},
  tribeEvents = [],
  matchTribeEvent = () => false,
} = {}) {
  const selectedLiveTraceItem = liveTraceItems.find((item) => item?.session?.request_id === selectedRequestId)
    ?? liveTraceItems[0]
    ?? null
  const selectedSessionFallback = selectedLiveTraceItem?.session
    ?? recentRequests.find((item) => item?.request_id === selectedRequestId)
    ?? (selectedRequestId ? { request_id: selectedRequestId } : null)
  const selectedTraceTitleSource = selectedLiveTraceItem?.trace
    ?? (traceData?.request_id === selectedRequestId ? traceData : null)
  const selectedLiveTraceTimelineEntries = selectedLiveTraceItem?.session?.request_id
    ? (liveTraceTimelineEntries[selectedLiveTraceItem.session.request_id] ?? [])
    : []
  const selectedSessionId = String(
    selectedSessionFallback?.session_id
    ?? selectedTraceTitleSource?.summary?.session_id
    ?? ''
  ).trim()
  const selectedConversationId = String(
    selectedSessionFallback?.conversation_id
    ?? selectedTraceTitleSource?.summary?.conversation_id
    ?? ''
  ).trim()
  const linkedTribeEvents = selectedRequestId
    ? tribeEvents.filter((event) => matchTribeEvent(event, {
      requestId: selectedRequestId,
      sessionId: selectedSessionId,
      conversationId: selectedConversationId,
    }))
    : []

  return {
    selectedLiveTraceItem,
    selectedSessionFallback,
    selectedTraceTitleSource,
    selectedLiveTraceTimelineEntries,
    selectedSessionId,
    selectedConversationId,
    linkedTribeEvents,
    linkedTribeEventList: linkedTribeEvents.slice(0, 20),
  }
}

export function getNextAutoTraceSelection({
  liveTraceItems = [],
  selectedRequestId,
  traceData,
} = {}) {
  if (!liveTraceItems.length) return null
  const matchedItem = liveTraceItems.find((item) => item?.session?.request_id === selectedRequestId)
  const nextItem = matchedItem ?? (!selectedRequestId ? liveTraceItems[0] : null)
  if (!nextItem?.trace) return null
  if (traceData?.request_id === nextItem.trace.request_id && traceData?.timestamp === nextItem.trace.timestamp) {
    return null
  }
  return nextItem
}

export function getTraceLiveFeedStatus({ pollingEnabled, pageVisible, liveFeedConnected } = {}) {
  if (!pollingEnabled) {
    return {
      className: 'border-primary-200 bg-primary-50/45 text-primary-700',
      text: '实时流已暂停',
    }
  }
  if (!pageVisible) {
    return {
      className: 'border-slate-200 bg-slate-50 text-slate-600',
      text: '页面隐藏，实时流已停',
    }
  }
  if (liveFeedConnected) {
    return {
      className: 'border-emerald-200 bg-emerald-50 text-emerald-700',
      text: 'SSE 实时流已连接',
    }
  }
  return {
    className: 'border-amber-200 bg-amber-50 text-amber-700',
    text: '实时流重连中，当前降级轮询',
  }
}

export function buildTraceHistoryViewState({
  recentKeyword = '',
  liveFeedConnected,
  liveTraceItems = [],
  liveTraceError,
  historyExpanded,
} = {}) {
  const isHistorySearchMode = Boolean(recentKeyword.trim())
  const shouldAutoShowHistory = !isHistorySearchMode && (!liveFeedConnected || liveTraceItems.length === 0 || Boolean(liveTraceError))
  const showHistoryPanel = !isHistorySearchMode && (historyExpanded || shouldAutoShowHistory)
  return {
    isHistorySearchMode,
    shouldAutoShowHistory,
    showHistoryPanel,
    activeFeedTitle: isHistorySearchMode ? '历史请求结果' : '实时活跃链路流',
    activeFeedDescription: isHistorySearchMode
      ? `按 "${recentKeyword}" 匹配到的请求会直接在这里展示，方便先筛选再点进单条详情。`
      : '首页只保留当前活跃链路，先看现在在跑什么，再点进单条请求追踪。',
    activeListTitle: isHistorySearchMode ? '历史结果列表' : '活跃请求流',
    activeListDescription: isHistorySearchMode
      ? '搜索命中的请求结果会替换活跃流列表，避免同层再放一份最近请求。'
      : '优先展示最近活跃的链路进度、当前节点和输出摘要。',
    activeCountLabel: isHistorySearchMode ? '条搜索结果' : '条活跃请求',
    historySectionHint: shouldAutoShowHistory
      ? '当前没有活跃链路，自动回退展示历史请求（已默认隐藏测试会话与纯问号空态消息）。'
      : '历史请求默认收起；需要回看刚才跑过什么时，再从这里展开。',
    canCollapseHistory: historyExpanded && !shouldAutoShowHistory,
  }
}

export function getTraceLiveModeLabel({ pollingEnabled, pageVisible, liveFeedConnected } = {}) {
  if (!pollingEnabled) return '实时暂停'
  if (!pageVisible) return '页面隐藏'
  if (liveFeedConnected) return 'SSE 实时流'
  return '轮询兜底'
}

export function buildTracePulseNodes({
  selectedLiveTraceTitle,
  selectedCurrentNodeLabel,
  selectedTraceStageMeta,
  liveModeLabel,
  liveFeedEnabled,
  linkedTribeEvents = [],
} = {}) {
  return [
    {
      label: '当前请求',
      value: selectedLiveTraceTitle || '等待选中',
      tone: 'primary',
      active: Boolean(selectedLiveTraceTitle),
    },
    {
      label: '当前节点',
      value: selectedCurrentNodeLabel,
      tone: 'teal',
      active: Boolean(selectedCurrentNodeLabel && selectedCurrentNodeLabel !== '等待选中请求'),
    },
    {
      label: '阶段覆盖',
      value: `${selectedTraceStageMeta?.activeStageCount ?? 0}/${GROUPS.length}`,
      tone: 'violet',
      active: (selectedTraceStageMeta?.activeStageCount ?? 0) > 0,
    },
    {
      label: '实时模式',
      value: liveModeLabel,
      tone: 'amber',
      active: liveFeedEnabled,
    },
    {
      label: '联动事件',
      value: `${linkedTribeEvents.length} 条`,
      tone: 'rose',
      active: linkedTribeEvents.length > 0,
    },
  ]
}
