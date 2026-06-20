import React, { useEffect, useState, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Search, RefreshCw,
  Activity, Bookmark, Layers,
  Clock3, Shield,
} from 'lucide-react'
import DigitalLifeRuntimeCard from '../../../life/components/DigitalLifeRuntimeCard.jsx'
import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'
import LivingPulseStrip from '../../../../shared/ui/LivingPulseStrip.jsx'
import NoticeBanner from '../../../../shared/ui/NoticeBanner.jsx'
import PageHero from '../../../../shared/ui/PageHero.jsx'
import {
  HomeSummaryCard,
  TracePageFallback,
  TraceSection,
} from './TraceSurfaces.jsx'
import {
  LiveTraceCard, LiveTraceStreamPreview, formatRecentTraceTime, getLiveTraceCurrentLabel, getLiveTraceOutputPreview,
  getLiveTraceStageClasses, getSessionDisplayTitle, getTraceActiveStageMeta, getTraceIssueCount, getTraceRunningCount, isTribeEventLinkedToSelection, mergeLiveTraceTimelineEntries, renderImmuneStageTags,
} from './TraceLiveSurfaces.jsx'
import TraceAdvancedDrilldownPanel from './TraceAdvancedDrilldownPanel.jsx'
import TraceHistoryPanel from './TraceHistoryPanel.jsx'
import TraceLinkedTribeEventsPanel from './TraceLinkedTribeEventsPanel.jsx'
import { GROUPS } from './traceFlowModel.js'
import {
  buildTraceGroupStats,
  buildTraceHistoryViewState,
  buildTraceInfoFromTrace,
  buildTraceDetailPath,
  buildTraceLiveFeedConnectParams,
  buildTraceLiveSnapshotParams,
  buildTracePulseNodes,
  buildTraceRecentRequests,
  buildTraceSearchState,
  buildTraceTribeFeedParams,
  buildSelectedTraceRequestContext,
  getLiveTraceItemFocusAction,
  getTracePayloadItems,
  getNextAutoTraceSelection,
  getSelectedTraceSessionForQuery,
  getTraceOverviewFocusAction,
  getTraceLiveFeedStatus,
  getTraceLiveModeLabel,
  getTraceStatusHealth,
  pruneTraceTimelineEntriesForSnapshot,
} from './traceHomeModel.js'
import useDigitalLifeRuntimeSignals from '../../../../hooks/useDigitalLifeRuntimeSignals.js'
import { useSessionsListQuery } from '../../sessionsQueries.js'
import { useTraceLiveSnapshotQuery, useTraceSnapshotQuery, useTraceTribeFeedQuery } from '../../traceQueries.js'
import { sanitizeRecentRequests } from '../../../../utils/sessionList.js'
import { connectTraceLiveFeed } from '../../traceApi.js'
import useAppStore from '../../../../store/useAppStore.js'

const TraceInlineFullChainOverview = React.lazy(() => import('./TraceAdvancedViews.jsx').then((module) => ({ default: module.InlineFullChainOverview })))
export default function TraceHome({ traceData, setTraceData, traceInfo, setTraceInfo, loading, setLoading, error, setError }) {
  const [inputId, setInputId] = useState('')
  const [recentKeyword, setRecentKeyword] = useState('')
  const [recentStatusFilter, setRecentStatusFilter] = useState('')
  const [historyExpanded, setHistoryExpanded] = useState(false)
  const [inlineOverviewVisible, setInlineOverviewVisible] = useState(false)
  const [advancedViewsExpanded, setAdvancedViewsExpanded] = useState(false)
  const [liveTraceItems, setLiveTraceItems] = useState([])
  const [liveTraceTimelineEntries, setLiveTraceTimelineEntries] = useState({})
  const [liveFeedError, setLiveFeedError] = useState(null)
  const [liveFeedConnected, setLiveFeedConnected] = useState(false)
  const [liveFeedLastTs, setLiveFeedLastTs] = useState(null)
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
  const liveSnapshotParams = React.useMemo(() => (
    buildTraceLiveSnapshotParams({ liveTraceLimit, recentKeyword })
  ), [liveTraceLimit, recentKeyword])
  const liveSnapshotQuery = useTraceLiveSnapshotQuery({
    queryParams: liveSnapshotParams,
    enabled: true,
    pollingEnabled: liveFeedEnabled,
    pollingTick,
    pollingInterval: Math.max(10, pollingInterval),
  })
  const recentRequestsBase = React.useMemo(() => (
    sanitizeRecentRequests(recentRequestsQuery.sessionsData?.items, { keyword: recentKeyword })
  ), [recentKeyword, recentRequestsQuery.sessionsData?.items])
  const recentRequests = React.useMemo(() => buildTraceRecentRequests({
    recentKeyword,
    recentRequestsBase,
    liveTraceItems,
  }), [liveTraceItems, recentKeyword, recentRequestsBase])
  const recentLoading = recentRequestsQuery.loading || recentRequestsQuery.refreshing
  const liveTraceLoading = liveSnapshotQuery.loading || liveSnapshotQuery.refreshing
  const liveTraceError = liveFeedError || liveSnapshotQuery.liveSnapshotError
  const selectedSessionForQuery = React.useMemo(() => getSelectedTraceSessionForQuery({
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
  const tribeFeedParams = React.useMemo(() => buildTraceTribeFeedParams({
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
  }, [setLoading, setError, setSelectedRequestId, setTraceData, setTraceInfo])

  useEffect(() => {
    if (!liveFeedEnabled) {
      setLiveFeedConnected(false)
      return undefined
    }

    let closed = false
    let connection
    setLiveFeedError(null)

    try {
      connection = connectTraceLiveFeed(
        buildTraceLiveFeedConnectParams({ liveTraceLimit, recentKeyword }),
        {
          onOpen: () => {
            if (closed) return
            setLiveFeedConnected(true)
            setLiveFeedError(null)
          },
          onSnapshot: (payload) => {
            if (closed) return
            const items = getTracePayloadItems(payload)
            setLiveFeedConnected(true)
            setLiveFeedLastTs(payload?.timestamp ?? Date.now())
            // SSE 可能返回空快照（例如当前无“活跃”窗口），此时保留已有列表，
            // 后续再由 fallback 拉取 recentRequests 对应链路，避免首页长时间空载。
            if (items.length > 0) {
              setLiveTraceItems(items)
            }
            setLiveTraceTimelineEntries((previous) => pruneTraceTimelineEntriesForSnapshot(previous, items))
            setLiveFeedError(null)
          },
          onDelta: (payload) => {
            if (closed) return
            const items = getTracePayloadItems(payload)
            if (items.length === 0) return
            setLiveFeedConnected(true)
            setLiveFeedLastTs(payload?.timestamp ?? Date.now())
            setLiveTraceTimelineEntries((previous) => mergeLiveTraceTimelineEntries(previous, items))
          },
          onHeartbeat: (payload) => {
            if (closed) return
            setLiveFeedConnected(true)
            setLiveFeedLastTs(payload?.timestamp ?? Date.now())
          },
          onError: (payload) => {
            if (closed) return
            setLiveFeedError(payload?.detail || '实时链路流异常')
          },
          onConnectionError: () => {
            if (closed) return
            setLiveFeedConnected(false)
          },
        }
      )
    } catch (connectionError) {
      setLiveFeedConnected(false)
      setLiveFeedError(connectionError.message)
      return undefined
    }

    return () => {
      closed = true
      connection?.close?.()
    }
  }, [liveFeedEnabled, recentKeyword, liveTraceLimit])

  useEffect(() => {
    const items = getTracePayloadItems(liveSnapshotQuery.liveSnapshot)
    if (items.length === 0) {
      if (!liveFeedConnected) {
        setLiveTraceItems([])
      }
      return
    }
    if (liveFeedConnected && liveTraceItems.length > 0) return
    setLiveTraceItems(items)
    if (!liveFeedLastTs && liveSnapshotQuery.liveSnapshot?.timestamp) {
      setLiveFeedLastTs(liveSnapshotQuery.liveSnapshot.timestamp)
    }
  }, [liveFeedConnected, liveFeedLastTs, liveSnapshotQuery.liveSnapshot, liveTraceItems.length])

  useEffect(() => {
    setLiveTraceItems([])
    setLiveTraceTimelineEntries({})
  }, [recentKeyword])

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

  const groupStats = React.useMemo(() => buildTraceGroupStats(traceData), [traceData])

  const openRequestTrace = (requestId) => {
    setSelectedRequestId(requestId)
    navigate(buildTraceDetailPath(requestId))
  }

  const latestRequest = recentRequests[0] ?? null
  const traceSearch = buildTraceSearchState(inputId)

  const handleTraceSearch = () => {
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
  }

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

  return (
    <div className="space-y-5">
      <PageHero
        icon={Activity}
        eyebrow="神经传导 Trace"
        title="数字生命体神经链路可视化"
        description="从感官输入、检索规划、人格上下文到响应输出，统一查看当前请求的完整神经传导路径。"
        gradientClass="from-blue-50 via-white to-violet-50"
        metaItems={[
          `活跃请求：${liveTraceItems.length}`,
          `当前节点：${traceInfo?.nodeCount ?? 0}`,
          selectedRequestId ? '已选中请求' : '未选中请求',
          currentTraceHealthy ? '当前链路：健康' : '当前链路：待关注',
        ]}
      />

      <LivingPulseStrip
        title="链路脉冲"
        subtitle="先看这一条神经流现在怎么流动，再下钻到单条请求或节点细节。"
        nodes={tracePulseNodes}
      />

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-5">
        <HomeSummaryCard
          icon={Bookmark}
          title="当前请求"
          value={selectedLiveTraceTitle || '等待选中请求'}
          caption={selectedRequestId || '从下方活跃请求流选择一条 request'}
        />
        <HomeSummaryCard
          icon={Layers}
          title="阶段覆盖"
          value={`${selectedTraceStageMeta.activeStageCount}/${GROUPS.length}`}
          caption={`当前阶段：${selectedTraceStageMeta.currentStageLabel}`}
        />
        <HomeSummaryCard
          icon={Shield}
          title="待关注节点"
          value={selectedIssueCount > 0 ? `${selectedIssueCount} 个异常/降级` : '无异常节点'}
          caption={selectedRunningCount > 0 ? `运行中 ${selectedRunningCount} 个节点` : (traceInfo?.latency != null ? `总耗时 ${traceInfo.latency}ms` : '等待节点继续推进')}
          toneClassName={selectedIssueCount > 0 ? 'text-orange-600' : 'text-emerald-600'}
        />
        <HomeSummaryCard
          icon={Activity}
          title="当前节点"
          value={selectedCurrentNodeLabel}
          caption={selectedOutputPreview || '暂无输出摘要，可能仍在执行或尚未落盘。'}
        />
        <HomeSummaryCard
          icon={Clock3}
          title="实时模式"
          value={liveModeLabel}
          caption={liveModeCaption}
          toneClassName={!pollingEnabled ? 'text-gray-700' : liveFeedConnected ? 'text-emerald-600' : 'text-amber-600'}
        />
      </div>

      <TraceSection
        title="链路控制台"
        hint="筛选请求、查看当前会话和 Tribe 联动事件。"
      >
      <div className="flex items-center gap-3 flex-wrap">
        <h1 className="text-xl font-semibold text-gray-900 shrink-0">链路可视化</h1>
        <div className="flex items-center gap-2 flex-1 min-w-64">
          <div className="relative flex-1">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              className="w-full rounded-lg border border-primary-100/70 bg-white/92 py-1.5 pl-8 pr-3 font-mono text-sm focus:border-blue-300 focus:outline-none focus:ring-2 focus:ring-primary-200"
              placeholder="输入消息内容、session_id 或 request_id"
              value={inputId}
              onChange={(e) => setInputId(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleTraceSearch()}
            />
          </div>
          <button className="btn-primary shrink-0" onClick={handleTraceSearch} disabled={loading || recentLoading}>
            {(loading || recentLoading) ? <RefreshCw size={13} className="animate-spin" /> : traceSearch.submitLabel}
          </button>
          <select
            className="shrink-0 rounded-lg border border-primary-100/70 bg-white/92 px-2 py-1.5 text-sm focus:border-blue-300 focus:outline-none focus:ring-2 focus:ring-primary-200"
            value={recentStatusFilter}
            onChange={(e) => { setRecentStatusFilter(e.target.value) }}
          >
            <option value="">全部状态</option>
            <option value="ok">成功</option>
            <option value="rejected">拒绝</option>
            <option value="error">错误</option>
          </select>
          {isHistorySearchMode && (
            <button
              className="btn-secondary shrink-0 text-xs"
              onClick={() => {
                setInputId('')
                setHistoryExpanded(false)
                setRecentKeyword('')
              }}
              disabled={recentLoading}
            >
              清除筛选
            </button>
          )}
          {traceData && (
            <button className="btn-secondary shrink-0 text-xs flex items-center gap-1"
              onClick={() => { setTraceData(null); setTraceInfo(null); setInlineOverviewVisible(false) }}>
              <RefreshCw size={12} /> 重置
            </button>
          )}
        </div>
        {/* 链路摘要 */}
        {traceInfo && (
          <div className="flex items-center gap-3 text-xs text-gray-500">
            <span>激活节点 {traceInfo.nodeCount}</span>
            {traceInfo.latency != null && <span>总耗时 {traceInfo.latency}ms</span>}
            <span className={currentTraceHealthy ? 'text-green-600 font-medium' : 'text-red-500 font-medium'}>
              {traceInfo.status}
            </span>
          </div>
        )}
        {error && (
          <NoticeBanner variant="error" title="链路查询异常" className="w-full md:w-auto">
            {error}
          </NoticeBanner>
        )}
      </div>

      {runtimeSnapshot && (
        <DigitalLifeRuntimeCard
          runtimeSnapshot={runtimeSnapshot}
          promptStats={promptBuilderData}
          compact
        />
      )}

      {selectedRequestId && (
        <div className="card flex items-center gap-3 flex-wrap px-4 py-3">
          <div className="min-w-0">
            <div className="text-xs font-medium text-blue-700">当前会话</div>
            <div className="text-sm font-semibold text-blue-900 break-all">{selectedLiveTraceTitle}</div>
            <div className="mt-1 font-mono text-xs text-blue-800 break-all">{selectedRequestId}</div>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <button className="btn-secondary text-xs" onClick={() => focusRequestOverview(selectedRequestId)} disabled={loading}>
              显示总览图
            </button>
            <button className="btn-primary text-xs" onClick={() => openRequestTrace(selectedRequestId)}>
              打开详情
            </button>
          </div>
          <div className="lifeline-rail h-1.5 w-full">
            <div className="lifeline-wave h-full w-full" />
          </div>
        </div>
      )}

      <TraceLinkedTribeEventsPanel
        selectedRequestId={selectedRequestId}
        selectedSessionId={selectedSessionId}
        selectedConversationId={selectedConversationId}
        linkedTribeEvents={linkedTribeEvents}
        linkedTribeEventList={linkedTribeEventList}
        tribeEventsLoading={tribeEventsLoading}
        tribeEventsError={tribeEventsError}
        onRefresh={() => { void tribeFeedQuery.refetch() }}
        onOpenInsights={() => navigate('/observability/insights/safety')}
      />
      </TraceSection>

      <div className="card relative overflow-hidden p-4 md:p-5">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_left,_rgba(41,112,255,0.05),_transparent_38%),radial-gradient(circle_at_bottom_right,_rgba(16,185,129,0.05),_transparent_34%)]" />
        <div className="relative">
        <div className="flex items-center gap-3 flex-wrap">
          <div className="flex items-center gap-2 min-w-0">
            <span className="flex items-center justify-center w-9 h-9 rounded-xl bg-blue-600 text-white shadow-sm">
              <Activity size={18} />
            </span>
            <div>
              <h2 className="text-base font-semibold text-gray-900">{activeFeedTitle}</h2>
              <p className="text-xs text-gray-500">
                {activeFeedDescription}
              </p>
            </div>
          </div>
          <div className="ml-auto flex items-center gap-2 text-xs text-gray-500">
            <span className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 ${liveFeedStatusClass}`}>
              <RefreshCw size={12} className={liveFeedConnected ? 'animate-spin' : ''} />
              {liveFeedStatusText}
            </span>
            {liveFeedLastTs ? <span>最近事件 {formatRecentTraceTime(liveFeedLastTs)}</span> : null}
            <span>{liveTraceItems.length} {activeCountLabel}</span>
          </div>
        </div>

        <div className="lifeline-rail mt-3 h-4 px-2">
          <div className="lifeline-wave absolute inset-0 opacity-65" />
          <div className="relative flex h-full items-center justify-around">
            {[0, 1, 2, 3].map((index) => (
              <span key={`active-feed-${index}`} className="lifeline-node" style={{ animationDelay: `${index * 0.24}s` }} />
            ))}
          </div>
        </div>

        <div className="mt-4 grid grid-cols-1 xl:grid-cols-[minmax(0,1.35fr)_24rem] gap-4">
          <div className="rounded-2xl border border-primary-100/70 bg-white/88 p-3 shadow-sm shadow-blue-100/40">
            <div className="flex items-center justify-between gap-3 border-b border-primary-100/70 px-1 pb-3">
              <div>
                <div className="text-sm font-semibold text-gray-900">{activeListTitle}</div>
                <div className="text-xs text-gray-500">{activeListDescription}</div>
              </div>
              <button
                className="btn-secondary text-xs"
                onClick={() => {
                  void Promise.allSettled([recentRequestsQuery.refetch(), liveSnapshotQuery.refetch()])
                }}
                disabled={recentLoading || liveTraceLoading}
              >
                <RefreshCw size={12} className={(recentLoading || liveTraceLoading) ? 'animate-spin' : ''} />
                刷新
              </button>
            </div>

            <div className="mt-3 space-y-3">
              {liveTraceLoading && liveTraceItems.length === 0 && (
                <NoticeBanner variant="info" title="同步中" className="px-4 py-8">
                  正在加载最近请求的链路详情…
                </NoticeBanner>
              )}
              {!liveTraceLoading && liveTraceError && liveTraceItems.length === 0 && (
                <NoticeBanner variant="error" title="活跃链路流加载失败" className="px-4 py-8">
                  {liveTraceError}
                </NoticeBanner>
              )}
              {!liveTraceLoading && !liveTraceError && liveTraceItems.length === 0 && (
                <EmptyStateCard title="暂无可展示请求链路" hint="有新的请求后会自动显示在这里。" className="p-6" />
              )}
              {liveTraceItems.map((item) => (
                <LiveTraceCard
                  key={item.session.request_id}
                  item={item}
                  selected={item.session.request_id === selectedLiveTraceItem?.session.request_id}
                  onSelect={() => focusLiveTraceItem(item, { showOverview: true })}
                  onOpenDetail={() => openRequestTrace(item.session.request_id)}
                />
              ))}
            </div>
          </div>

          <div className="rounded-2xl border border-primary-100/70 bg-white/88 p-3 shadow-sm shadow-blue-100/40">
            <div className="border-b border-primary-100/70 px-1 pb-3">
              <div className="text-sm font-semibold text-gray-900">当前链路预览</div>
              <div className="text-xs text-gray-500">右侧固定看板会跟随选中请求切换，便于快速定位当前跑到哪一层。</div>
            </div>
            <div className="mt-3">
              <LiveTraceStreamPreview
                item={selectedLiveTraceItem}
                timelineEntries={selectedLiveTraceTimelineEntries}
                onHighlight={() => selectedLiveTraceItem && focusLiveTraceItem(selectedLiveTraceItem, { showOverview: true })}
                onOpenDetail={() => selectedLiveTraceItem && openRequestTrace(selectedLiveTraceItem.session.request_id)}
              />
            </div>
          </div>
        </div>
        </div>
      </div>

      {inlineOverviewVisible && traceData && (
        <div ref={inlineOverviewRef}>
          <React.Suspense
            fallback={(
              <TracePageFallback>正在加载当前请求总览图…</TracePageFallback>
            )}
          >
            <TraceInlineFullChainOverview
              traceData={traceData}
              requestId={selectedRequestId}
              onOpenDetail={() => selectedRequestId && openRequestTrace(selectedRequestId)}
              onOpenStandalone={() => navigate('/observability/trace/full')}
              onHide={() => setInlineOverviewVisible(false)}
              renderImmuneStageTags={renderImmuneStageTags}
              getLiveTraceStageClasses={getLiveTraceStageClasses}
            />
          </React.Suspense>
        </div>
      )}

      <TraceHistoryPanel
        canCollapseHistory={canCollapseHistory}
        historySectionHint={historySectionHint}
        isHistorySearchMode={isHistorySearchMode}
        latestRequest={latestRequest}
        onOpenRequest={openRequestTrace}
        onRefresh={() => { void recentRequestsQuery.refetch() }}
        recentLoading={recentLoading}
        recentRequests={recentRequests}
        setHistoryExpanded={setHistoryExpanded}
        showHistoryPanel={showHistoryPanel}
      />

      <TraceAdvancedDrilldownPanel
        advancedViewsExpanded={advancedViewsExpanded}
        groupStats={groupStats}
        navigate={navigate}
        setAdvancedViewsExpanded={setAdvancedViewsExpanded}
        traceData={traceData}
        traceInfo={traceInfo}
      />

    </div>
  )
}
