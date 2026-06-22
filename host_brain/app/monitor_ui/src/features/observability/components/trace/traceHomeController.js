import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { GROUPS } from './traceFlowModel.js'
import {
  buildTraceDetailPath,
  buildTraceGroupStats,
  buildTraceInfoFromTrace,
  getLiveTraceItemFocusAction,
  getNextAutoTraceSelection,
  getTraceOverviewFocusAction,
} from './traceHomeModel.js'
import { buildTraceHomeControllerView } from './traceHomeControllerView.js'
import { useTraceHomeDataSources } from './useTraceHomeDataSources.js'
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
  const {
    liveFeedConnected,
    liveFeedLastTs,
    liveTraceError,
    liveTraceItems,
    liveTraceLoading,
    liveTraceTimelineEntries,
    promptBuilderData,
    recentLoading,
    recentRequests,
    recentRequestsQuery,
    refreshLiveSnapshot,
    runtimeSnapshot,
    selectedConversationIdForQuery,
    selectedSessionIdForQuery,
    selectedTraceQuery,
    tribeEvents,
    tribeEventsError,
    tribeEventsLoading,
    tribeFeedQuery,
  } = useTraceHomeDataSources({
    liveFeedEnabled,
    pollingTick,
    pollingInterval,
    recentKeyword,
    recentStatusFilter,
    selectedRequestId,
    traceData,
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

  const traceHomeView = buildTraceHomeControllerView({
    historyExpanded,
    inputId,
    liveFeedConnected,
    liveFeedEnabled,
    liveFeedLastTs,
    liveTraceError,
    liveTraceItems,
    liveTraceTimelineEntries,
    pageVisible,
    pollingEnabled,
    recentKeyword,
    recentRequests,
    selectedRequestId,
    traceData,
    traceInfo,
    tribeEvents,
  })
  const { traceSearch } = traceHomeView

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
    advancedViewsExpanded,
    error,
    focusLiveTraceItem,
    focusRequestOverview,
    groupStats,
    handleTraceSearch,
    inlineOverviewRef,
    inlineOverviewVisible,
    inputId,
    liveFeedConnected,
    liveFeedLastTs,
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
    selectedRequestId,
    setAdvancedViewsExpanded,
    setHistoryExpanded,
    setInlineOverviewVisible,
    setInputId,
    setRecentKeyword,
    setRecentStatusFilter,
    setTraceData,
    setTraceInfo,
    traceGroupCount: GROUPS.length,
    ...traceHomeView,
    traceInfo,
    tribeEventsError,
    tribeEventsLoading,
    refreshLiveData: () => Promise.allSettled([recentRequestsQuery.refetch(), refreshLiveSnapshot()]),
    refreshRecentRequests: recentRequestsQuery.refetch,
    refreshTribeEvents: tribeFeedQuery.refetch,
  }
}
