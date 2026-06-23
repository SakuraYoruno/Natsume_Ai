import { useMemo } from 'react'

import useDigitalLifeRuntimeSignals from '../../../../hooks/useDigitalLifeRuntimeSignals.js'
import { sanitizeRecentRequests } from '../../../../utils/sessionList.js'
import { useSessionsListQuery } from '../../sessionsQueries.js'
import { useTraceSnapshotQuery, useTraceTribeFeedQuery } from '../../traceQueries.js'
import {
  buildTraceRecentRequests,
  buildTraceTribeFeedParams,
  getSelectedTraceSessionForQuery,
} from './traceHomeModel.js'
import { useTraceHomeLiveFeed } from './useTraceHomeLiveFeed.js'

export function useTraceHomeDataSources({
  liveFeedEnabled,
  liveFeedConnectedFallback = false,
  pollingTick,
  pollingInterval,
  recentKeyword,
  recentStatusFilter,
  selectedRequestId,
  traceData,
}) {
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
  const selectedTraceQuery = useTraceSnapshotQuery({
    requestId: selectedRequestId,
    enabled: Boolean(selectedRequestId),
    pollingEnabled: liveFeedEnabled && !(liveFeedConnected || liveFeedConnectedFallback),
    pollingTick,
    pollingInterval,
  })

  return {
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
    tribeEventsError: tribeFeedQuery.tribeFeedError,
    tribeEventsLoading: tribeFeedQuery.loading || tribeFeedQuery.refreshing,
    tribeFeedQuery,
  }
}
