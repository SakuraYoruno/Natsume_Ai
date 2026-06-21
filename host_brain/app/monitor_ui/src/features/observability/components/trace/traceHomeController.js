import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  formatRecentTraceTime,
  getLiveTraceCurrentLabel,
  getLiveTraceOutputPreview,
  getSessionDisplayTitle,
  getTraceActiveStageMeta,
  getTraceIssueCount,
  getTraceRunningCount,
  isTribeEventLinkedToSelection,
} from './traceLiveSurfaceModel.js'
import { GROUPS } from './traceFlowModel.js'
import {
  buildSelectedTraceRequestContext,
  buildTraceDetailPath,
  buildTraceGroupStats,
  buildTraceHistoryViewState,
  buildTraceInfoFromTrace,
  buildTracePulseNodes,
  buildTraceRecentRequests,
  buildTraceSearchState,
  buildTraceTribeFeedParams,
  getLiveTraceItemFocusAction,
  getNextAutoTraceSelection,
  getSelectedTraceSessionForQuery,
  getTraceLiveFeedStatus,
  getTraceLiveModeLabel,
  getTraceOverviewFocusAction,
  getTraceStatusHealth,
} from './traceHomeModel.js'
import { useTraceHomeLiveFeed } from './useTraceHomeLiveFeed.js'
import useDigitalLifeRuntimeSignals from '../../../../hooks/useDigitalLifeRuntimeSignals.js'
import { useSessionsListQuery } from '../../sessionsQueries.js'
import { useTraceSnapshotQuery, useTraceTribeFeedQuery } from '../../traceQueries.js'
import { sanitizeRecentRequests } from '../../../../utils/sessionList.js'
import useAppStore from '../../../../store/useAppStore.js'

export function useTraceHomeController({
  traceData,
  setTraceData,
  traceInfo,
  setTraceInfo,
  loading,
  setLoading,
  error,
  setError,
}) {
  const [inputId, setInputId] = useState('')
  const [recentKeyword, setRecentKeyword] = useState('')
  const [recentStatusFilter, setRecentStatusFilter] = useState('')
  const [historyExpanded, setHistoryExpanded] = useState(false)
  const [inlineOverviewVisible, setInlineOverviewVisible] = useState(false)
  const [advancedViewsExpanded, setAdvancedViewsExpanded] = useState(false)
  const inlineOverviewRef = useRef(null)
  const navigate = useNavigate()
  const pollingEnabled = useAppStore((s) => s.pollingEnabled)
  const pollingInterval = useAppStore((s) => s.pollingInterval)
  const pollingTick = useAppStore((s) => s.pollingTick)
  const pageVisible = useAppStore((s) => s.pageVisible)
  const selectedRequestId = useAppStore((s) => s.selectedRequestId)
  const setSelectedRequestId = useAppStore((s) => s.setSelectedRequestId)
  const liveFeedEnabled = pollingEnabled && pageVisible
  const liveTraceLimit = recentKeyword ? 12 : 6
  const {
    liveFeedConnected,
    liveFeedLastTs,
    liveTraceError,
    liveTraceItems,
    liveTraceLoading,
    liveTraceTimelineEntries,
    refreshLiveSnapshot,
  } = useTraceHomeLiveFeed({
    liveFeedEnabled,
    liveTraceLimit,
    pollingTick,
    pollingInterval,
    recentKeyword,
  })
  const { runtimeSnapshot, promptStats: promptBuilderData } = useDigitalLifeRuntimeSignals({
    pollingEnabled: liveFeedEnabled,
    pollingInterval,
    windowMinutes: 60,
    minIntervalSeconds: 10,
  })
  const recentRequestsQuery = useSessionsListQuery({
    page: 1,
    pageSize: 12,
    keyword: recentKeyword,
    status: recentStatusFilter,
    windowMinutes: 1440,
    pollingEnabled: liveFeedEnabled,
    pollingTick,
    pollingInterval,
  })
  const recentRequestsBase = useMemo(() => (
    sanitizeRecentRequests(recentRequestsQuery.sessionsData?.items, { keyword: recentKeyword })
  ), [recentKeyword, recentRequestsQuery.sessionsData?.items])
  const recentRequests = useMemo(() => buildTraceRecentRequests({
    recentKeyword,
    recentRequestsBase,
    liveTraceItems,
  }), [liveTraceItems, recentKeyword, recentRequestsBase])
  const recentLoading = recentRequestsQuery.loading || recentRequestsQuery.refreshing
  const selectedSessionForQuery = useMemo(() => getSelectedTraceSessionForQuery({
    liveTraceItems,
    recentRequests,
    selectedRequestId,
  }), [liveTraceItems, recentRequests, selectedRequestId])
  const selectedSessionIdForQuery = String(
    selectedSessionForQuery?.session_id
    ?? traceData?.summary?.session_id
    ?? ''
  ).trim()
  const selectedConversationIdForQuery = String(
    selectedSessionForQuery?.conversation_id
    ?? traceData?.summary?.conversation_id
    ?? ''
  ).trim()
  const tribeFeedParams = useMemo(() => buildTraceTribeFeedParams({
    selectedRequestId,
    selectedSessionIdForQuery,
    selectedConversationIdForQuery,
  }), [selectedConversationIdForQuery, selectedRequestId, selectedSessionIdForQuery])
  const tribeFeedQuery = useTraceTribeFeedQuery({
    queryParams: tribeFeedParams,
    enabled: Boolean(selectedRequestId || selectedSessionIdForQuery || selectedConversationIdForQuery),
    pollingEnabled: liveFeedEnabled,
    pollingTick,
    pollingInterval: Math.max(10, pollingInterval),
  })
  const tribeEvents = Array.isArray(tribeFeedQuery.tribeFeedPayload?.items) ? tribeFeedQuery.tribeFeedPayload.items : []
  const tribeEventsLoading = tribeFeedQuery.loading || tribeFeedQuery.refreshing
  const tribeEventsError = tribeFeedQuery.tribeFeedError
  const selectedTraceQuery = useTraceSnapshotQuery({
    requestId: selectedRequestId,
    enabled: Boolean(selectedRequestId),
    pollingEnabled: liveFeedEnabled && !liveFeedConnected,
    pollingTick,
    pollingInterval,
  })

  const load = useCallback(async (id, options = {}) => {
    if (!id?.trim()) return
    const showOverview = options.showOverview ?? true
    setError(null)
    setSelectedRequestId(id)
    if (showOverview) {
      setInlineOverviewVisible(true)
    }
  }, [setError, setSelectedRequestId])

  useEffect(() => {
    setLoading(selectedTraceQuery.loading)
    setError(selectedTraceQuery.traceError)
  }, [selectedTraceQuery.loading, selectedTraceQuery.traceError, setError, setLoading])

  useEffect(() => {
    if (!selectedRequestId) {
      setLoading(false)
      return
    }
    if (!selectedTraceQuery.traceSnapshot) return
    setTraceData(selectedTraceQuery.traceSnapshot)
    setTraceInfo(buildTraceInfoFromTrace(selectedTraceQuery.traceSnapshot))
  }, [selectedRequestId, selectedTraceQuery.traceSnapshot, setLoading, setTraceData, setTraceInfo])

  const revealInlineOverview = useCallback(() => {
    setInlineOverviewVisible(true)
    requestAnimationFrame(() => {
      inlineOverviewRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
  }, [])

  const applyTraceSelection = useCallback((item, options = {}) => {
    if (!item?.session?.request_id || !item.trace) return false
    setSelectedRequestId(item.session.request_id)
    setTraceData(item.trace)
    setTraceInfo(buildTraceInfoFromTrace(item.trace))
    if (options.showOverview ?? true) {
      revealInlineOverview()
    }
    return true
  }, [setSelectedRequestId, setTraceData, setTraceInfo, revealInlineOverview])

  useEffect(() => {
    const nextItem = getNextAutoTraceSelection({ liveTraceItems, selectedRequestId, traceData })
    if (!nextItem) return
    applyTraceSelection(nextItem, { showOverview: false })
  }, [applyTraceSelection, liveTraceItems, selectedRequestId, traceData])

  useEffect(() => {
    if (!inlineOverviewVisible || !traceData?.request_id) return undefined
    const frame = requestAnimationFrame(() => {
      inlineOverviewRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
    return () => cancelAnimationFrame(frame)
  }, [inlineOverviewVisible, traceData?.request_id])

  const groupStats = useMemo(() => buildTraceGroupStats(traceData), [traceData])

  const openRequestTrace = useCallback((requestId) => {
    setSelectedRequestId(requestId)
    navigate(buildTraceDetailPath(requestId))
  }, [navigate, setSelectedRequestId])

  const latestRequest = recentRequests[0] ?? null
  const traceSearch = buildTraceSearchState(inputId)

  const handleTraceSearch = useCallback(() => {
    if (traceSearch.action === 'clear') {
      setHistoryExpanded(false)
      setRecentKeyword('')
      return
    }
    if (traceSearch.action === 'load') {
      load(traceSearch.value)
      return
    }
    setHistoryExpanded(false)
    setRecentKeyword(traceSearch.value)
  }, [load, traceSearch.action, traceSearch.value])

  const { healthy: currentTraceHealthy } = getTraceStatusHealth(traceInfo)
  const { className: liveFeedStatusClass, text: liveFeedStatusText } = getTraceLiveFeedStatus({
    pollingEnabled,
    pageVisible,
    liveFeedConnected,
  })
  const {
    selectedLiveTraceItem,
    selectedSessionFallback,
    selectedTraceTitleSource,
    selectedLiveTraceTimelineEntries,
    selectedSessionId,
    selectedConversationId,
    linkedTribeEvents,
    linkedTribeEventList,
  } = buildSelectedTraceRequestContext({
    liveTraceItems,
    recentRequests,
    selectedRequestId,
    traceData,
    liveTraceTimelineEntries,
    tribeEvents,
    matchTribeEvent: isTribeEventLinkedToSelection,
  })
  const selectedLiveTraceTitle = selectedRequestId
    ? getSessionDisplayTitle(selectedSessionFallback, selectedTraceTitleSource)
    : null
  const {
    isHistorySearchMode,
    shouldAutoShowHistory,
    showHistoryPanel,
    activeFeedTitle,
    activeFeedDescription,
    activeListTitle,
    activeListDescription,
    activeCountLabel,
    historySectionHint,
    canCollapseHistory,
  } = buildTraceHistoryViewState({
    recentKeyword,
    liveFeedConnected,
    liveTraceItems,
    liveTraceError,
    historyExpanded,
  })
  const selectedTraceStageMeta = getTraceActiveStageMeta(traceData)
  const selectedIssueCount = getTraceIssueCount(traceData)
  const selectedRunningCount = getTraceRunningCount(traceData)
  const selectedCurrentNodeLabel = traceData ? getLiveTraceCurrentLabel(traceData) : '等待选中请求'
  const selectedOutputPreview = traceData ? getLiveTraceOutputPreview(traceData) : ''
  const liveModeLabel = getTraceLiveModeLabel({ pollingEnabled, pageVisible, liveFeedConnected })
  const liveModeCaption = liveFeedLastTs
    ? `最近事件 ${formatRecentTraceTime(liveFeedLastTs)}`
    : (liveTraceItems.length > 0 ? `${liveTraceItems.length} 条活跃请求` : '等待活跃请求')
  const tracePulseNodes = buildTracePulseNodes({
    selectedLiveTraceTitle,
    selectedCurrentNodeLabel,
    selectedTraceStageMeta,
    liveModeLabel,
    liveFeedEnabled,
    linkedTribeEvents,
  })

  const focusRequestOverview = useCallback((requestId) => {
    const action = getTraceOverviewFocusAction({ requestId, traceData, liveTraceItems })
    if (action.type === 'none') return
    if (action.type === 'reveal-current') {
      setSelectedRequestId(action.requestId)
      revealInlineOverview()
      return
    }
    if (action.type === 'select-live') {
      applyTraceSelection(action.item, { showOverview: true })
      return
    }
    load(action.requestId, { showOverview: true })
  }, [applyTraceSelection, liveTraceItems, load, revealInlineOverview, setSelectedRequestId, traceData])

  const focusLiveTraceItem = useCallback((item, options = {}) => {
    const action = getLiveTraceItemFocusAction({
      item,
      showOverview: options.showOverview ?? true,
    })
    if (action.type === 'none') return
    if (action.type === 'load') {
      load(action.requestId, { showOverview: action.showOverview })
      return
    }
    applyTraceSelection(action.item, { showOverview: action.showOverview })
  }, [applyTraceSelection, load])

  return {
    activeCountLabel,
    activeFeedDescription,
    activeFeedTitle,
    activeListDescription,
    activeListTitle,
    advancedViewsExpanded,
    canCollapseHistory,
    currentTraceHealthy,
    error,
    focusLiveTraceItem,
    focusRequestOverview,
    groupStats,
    handleTraceSearch,
    historySectionHint,
    inlineOverviewRef,
    inlineOverviewVisible,
    inputId,
    isHistorySearchMode,
    latestRequest,
    linkedTribeEventList,
    linkedTribeEvents,
    liveFeedConnected,
    liveFeedLastTs,
    liveFeedStatusClass,
    liveFeedStatusText,
    liveModeCaption,
    liveModeLabel,
    liveTraceError,
    liveTraceItems,
    liveTraceLoading,
    loading,
    navigate,
    openRequestTrace,
    pollingEnabled,
    promptBuilderData,
    recentLoading,
    recentRequests,
    recentStatusFilter,
    runtimeSnapshot,
    selectedConversationId,
    selectedCurrentNodeLabel,
    selectedIssueCount,
    selectedLiveTraceItem,
    selectedLiveTraceTimelineEntries,
    selectedLiveTraceTitle,
    selectedOutputPreview,
    selectedRequestId,
    selectedRunningCount,
    selectedSessionId,
    selectedTraceStageMeta,
    setAdvancedViewsExpanded,
    setHistoryExpanded,
    setInlineOverviewVisible,
    setInputId,
    setRecentKeyword,
    setRecentStatusFilter,
    setTraceData,
    setTraceInfo,
    showHistoryPanel,
    traceGroupCount: GROUPS.length,
    traceInfo,
    tracePulseNodes,
    traceSearch,
    tribeEventsError,
    tribeEventsLoading,
    refreshLiveData: () => Promise.allSettled([recentRequestsQuery.refetch(), refreshLiveSnapshot()]),
    refreshRecentRequests: recentRequestsQuery.refetch,
    refreshTribeEvents: tribeFeedQuery.refetch,
  }
}
