import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import useAppStore from '../../store/useAppStore.js'
import useImmuneGateReplayQuery from '../../shared/query/useImmuneGateReplayQuery.js'
import { connectTraceRequestLive } from './traceApi.js'
import {
  useTraceRetrievalRuntimeQuery,
  useTraceSnapshotQuery,
  useTraceStreamEventsQuery,
  useTraceTribeEventsQuery,
} from './traceQueries.js'
import {
  buildDeltaEntries,
  dedupeStreamEvents,
  mergeDeltaEntries,
} from './components/trace/TraceRequestRuntimePanels.jsx'
import {
  ISSUE_STATUSES,
  RUNNING_STATUSES,
  buildRetrievalRuntimeMeta,
  getSummaryInputText,
  getSummaryOutputText,
} from './components/trace/TraceRequestDetailModel.jsx'

export function useTraceRequestDetailController() {
  const navigate = useNavigate()
  const { requestId } = useParams()
  const [traceData, setTraceData] = useState(null)
  const [fallbackEvents, setFallbackEvents] = useState([])
  const [selectedNode, setSelectedNode] = useState(null)
  const [liveEntries, setLiveEntries] = useState([])
  const [liveConnected, setLiveConnected] = useState(false)
  const [liveLastTs, setLiveLastTs] = useState(null)
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const pollingTick = useAppStore((state) => state.pollingTick)
  const pageVisible = useAppStore((state) => state.pageVisible)
  const detailPollingEnabled = pollingEnabled && pageVisible

  const traceQuery = useTraceSnapshotQuery({
    requestId,
    enabled: Boolean(requestId),
    pollingEnabled: detailPollingEnabled && !liveConnected,
    pollingTick,
    pollingInterval,
  })
  const streamEventsQuery = useTraceStreamEventsQuery({
    requestId,
    enabled: Boolean(requestId),
    pollingEnabled: detailPollingEnabled && !liveConnected,
    pollingTick,
    pollingInterval,
  })
  const tribeEventsQuery = useTraceTribeEventsQuery({
    requestId,
    limit: 120,
    enabled: Boolean(requestId),
    pollingEnabled: detailPollingEnabled,
    pollingTick,
    pollingInterval: Math.max(10, pollingInterval),
  })
  const retrievalRuntimeQuery = useTraceRetrievalRuntimeQuery({
    requestId,
    limit: 5,
    enabled: Boolean(requestId),
    pollingEnabled: detailPollingEnabled,
    pollingTick,
    pollingInterval: Math.max(15, pollingInterval),
  })
  const immuneReplayQuery = useImmuneGateReplayQuery({
    requestId,
    limit: 160,
    pollingEnabled: detailPollingEnabled,
    pollingTick,
    pollingInterval: Math.max(10, pollingInterval),
  })

  const loading = traceQuery.loading
  const error = traceQuery.traceError
  const eventsLoading = streamEventsQuery.loading
  const eventsError = streamEventsQuery.streamEventsError
  const tribeEventsLoading = tribeEventsQuery.loading
  const tribeEventsError = tribeEventsQuery.tribeEventsError
  const immuneReplayLoading = immuneReplayQuery.loading
  const immuneReplayError = (() => {
    const message = String(immuneReplayQuery.replayError || '')
    return message.includes('HTTP 404') ? null : (message || null)
  })()
  const tribeEvents = Array.isArray(tribeEventsQuery.tribeEventsPayload?.items)
    ? tribeEventsQuery.tribeEventsPayload.items
    : []
  const retrievalRuntime = retrievalRuntimeQuery.retrievalRuntime
  const immuneReplay = immuneReplayQuery.replayPayload

  useEffect(() => {
    setFallbackEvents(streamEventsQuery.streamEventsPayload?.events ?? [])
  }, [streamEventsQuery.streamEventsPayload])

  useEffect(() => {
    if (!requestId) {
      setTraceData(null)
      return
    }
    setTraceData(traceQuery.traceSnapshot)
  }, [requestId, traceQuery.traceSnapshot])

  useEffect(() => {
    if (!requestId || !pageVisible) {
      setLiveConnected(false)
      return undefined
    }
    let closed = false
    let connection
    try {
      connection = connectTraceRequestLive(requestId, {
        onOpen: () => { if (!closed) setLiveConnected(true) },
        onSnapshot: (payload) => {
          if (closed) return
          const matched = Array.isArray(payload?.items)
            ? payload.items.find((item) => item?.session?.request_id === requestId || item?.trace?.request_id === requestId)
            : null
          if (matched?.trace) {
            setTraceData(matched.trace)
            setFallbackEvents((previous) => dedupeStreamEvents(matched.trace.stream_events, previous))
          }
          setLiveLastTs(payload?.timestamp ?? Date.now())
        },
        onDelta: (payload) => {
          if (closed) return
          const matched = Array.isArray(payload?.items)
            ? payload.items.find((item) => item?.request_id === requestId || item?.session?.request_id === requestId)
            : null
          if (matched) setLiveEntries((previous) => mergeDeltaEntries(previous, buildDeltaEntries(matched)))
          setLiveLastTs(payload?.timestamp ?? Date.now())
        },
        onHeartbeat: (payload) => { if (!closed) setLiveLastTs(payload?.timestamp ?? Date.now()) },
        onConnectionError: () => { if (!closed) setLiveConnected(false) },
      })
    } catch {
      setLiveConnected(false)
      return undefined
    }
    return () => {
      closed = true
      connection?.close?.()
    }
  }, [pageVisible, requestId])

  const nodes = useMemo(() => {
    const items = Array.isArray(traceData?.nodes) ? [...traceData.nodes] : []
    items.sort((a, b) => (a?.ts_ms ?? 0) - (b?.ts_ms ?? 0))
    return items
  }, [traceData])

  useEffect(() => {
    if (!nodes.length) {
      setSelectedNode(null)
      return
    }
    setSelectedNode((previous) => nodes.find((node) => node.node_id === previous?.node_id) || nodes[nodes.length - 1])
  }, [nodes])

  const streamEvents = useMemo(
    () => dedupeStreamEvents(traceData?.stream_events, fallbackEvents),
    [traceData, fallbackEvents]
  )
  const summary = traceData?.summary ?? {}
  const degradedReason = String(summary?.degraded_reason || '').trim()
  const degradedMessage = String(summary?.degraded_message || '').trim()
  const traceDegraded = Boolean(summary?.degraded)
  const subgraphs = Array.isArray(traceData?.subgraphs) ? traceData.subgraphs : []
  const inputPreview = getSummaryInputText(summary)
  const outputPreview = getSummaryOutputText(summary)
  const latestStreamEvent = streamEvents[0] ?? null
  const issueNodeCount = nodes.filter((node) => ISSUE_STATUSES.has(String(node.status || '').toUpperCase())).length
  const runningNodeCount = nodes.filter((node) => RUNNING_STATUSES.has(String(node.status || '').toUpperCase())).length
  const retrievalMeta = buildRetrievalRuntimeMeta(retrievalRuntime)
  const liveModeText = liveConnected ? 'SSE 实时流已连接' : '实时流重连中 / 轮询补齐'
  const tracePulseNodes = [
    {
      label: '请求状态',
      value: traceData?.status || '待加载',
      tone: 'primary',
      active: Boolean(traceData?.status),
    },
    {
      label: '节点覆盖',
      value: `${traceData?.node_count ?? nodes.length}`,
      tone: 'teal',
      active: (traceData?.node_count ?? nodes.length) > 0,
    },
    {
      label: '异常节点',
      value: String(issueNodeCount),
      tone: 'rose',
      active: issueNodeCount > 0,
    },
    {
      label: '流式事件',
      value: String(streamEvents.length),
      tone: 'violet',
      active: streamEvents.length > 0,
    },
    {
      label: 'Tribe 联动',
      value: String(tribeEvents.length),
      tone: 'amber',
      active: tribeEvents.length > 0,
    },
    {
      label: '实时模式',
      value: liveModeText,
      tone: 'primary',
      active: liveConnected,
    },
  ]
  const handleRefresh = useCallback(async () => {
    await Promise.allSettled([
      traceQuery.refetch(),
      streamEventsQuery.refetch(),
      tribeEventsQuery.refetch(),
      retrievalRuntimeQuery.refetch(),
      immuneReplayQuery.refetch(),
    ])
  }, [immuneReplayQuery, retrievalRuntimeQuery, streamEventsQuery, traceQuery, tribeEventsQuery])

  return {
    requestId,
    traceData,
    nodes,
    summary,
    inputPreview,
    outputPreview,
    issueNodeCount,
    runningNodeCount,
    streamEvents,
    latestStreamEvent,
    subgraphs,
    tribeEvents,
    retrievalMeta,
    retrievalRuntime,
    immuneReplay,
    immuneReplayLoading,
    immuneReplayError,
    eventsLoading,
    eventsError,
    tribeEventsLoading,
    tribeEventsError,
    loading,
    error,
    traceDegraded,
    degradedMessage,
    degradedReason,
    liveModeText,
    tracePulseNodes,
    liveConnected,
    liveEntries,
    liveLastTs,
    selectedNode,
    onSelectNode: setSelectedNode,
    onNavigateBack: () => navigate('/observability/trace'),
    onRefresh: () => { void handleRefresh() },
    onReloadImmuneReplay: () => { void immuneReplayQuery.refetch() },
  }
}
