import {
  buildRetrievalRuntimeMeta,
  getSummaryInputText,
  getSummaryOutputText,
  ISSUE_STATUSES,
  RUNNING_STATUSES,
} from './components/trace/TraceRequestDetailModel.jsx'
import { dedupeStreamEvents } from './components/trace/traceRequestRuntimePanelModel.js'

export function getSortedTraceNodes(traceData) {
  const items = Array.isArray(traceData?.nodes) ? [...traceData.nodes] : []
  items.sort((a, b) => (a?.ts_ms ?? 0) - (b?.ts_ms ?? 0))
  return items
}

export function buildTraceRequestDetailViewModel({
  traceData,
  fallbackEvents = [],
  tribeEvents = [],
  retrievalRuntime = null,
  liveConnected = false,
} = {}) {
  const nodes = getSortedTraceNodes(traceData)
  const streamEvents = dedupeStreamEvents(traceData?.stream_events, fallbackEvents)
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

  return {
    nodes,
    streamEvents,
    summary,
    inputPreview,
    outputPreview,
    latestStreamEvent,
    issueNodeCount,
    runningNodeCount,
    subgraphs,
    retrievalMeta,
    liveModeText,
    tracePulseNodes,
    traceDegraded,
    degradedMessage,
    degradedReason,
  }
}
