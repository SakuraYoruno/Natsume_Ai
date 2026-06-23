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
import {
  buildSelectedTraceRequestContext,
  buildTraceHistoryViewState,
  buildTracePulseNodes,
  buildTraceSearchState,
  getTraceLiveFeedStatus,
  getTraceLiveModeLabel,
  getTraceStatusHealth,
} from './traceHomeModel.js'

export function buildTraceHomeControllerView({
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
}) {
  const latestRequest = recentRequests[0] ?? null
  const traceSearch = buildTraceSearchState(inputId)
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
  const historyView = buildTraceHistoryViewState({
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

  return {
    ...historyView,
    currentTraceHealthy,
    latestRequest,
    linkedTribeEventList,
    linkedTribeEvents,
    liveFeedStatusClass,
    liveFeedStatusText,
    liveModeCaption,
    liveModeLabel,
    selectedConversationId,
    selectedCurrentNodeLabel,
    selectedIssueCount,
    selectedLiveTraceItem,
    selectedLiveTraceTimelineEntries,
    selectedLiveTraceTitle,
    selectedOutputPreview,
    selectedRunningCount,
    selectedSessionId,
    selectedTraceStageMeta,
    tracePulseNodes,
    traceSearch,
  }
}
