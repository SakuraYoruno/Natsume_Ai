import { formatDateTime } from '../../../../shared/formatters.js'
import { formatAlertTitle, humanizeToken } from '../../../../utils/digitalLife.js'
import {
  getLatestPrimaryTraceNode,
  getLatestTraceNode,
  getLiveTraceFlow,
  normalizeTraceGroupNodeId,
} from './traceFlowModel.js'
import { getTraceNodeDisplayName } from './traceNodeText.js'

const LIVE_TRACE_KEY_NODE_IDS = [
  'airlock_ingress',
  'governor',
  'immune_gate',
  'query_planner',
  'agentic_rag',
  'recall_fusion',
  'emotion_context',
  'persona_resident_memory',
  'motive_state',
  'render_policy',
  'cortex_orchestrator',
  'executive_router',
  'sub_agent_code',
  'sub_agent_translate',
  'sub_agent_intent',
  'sub_agent_vision',
  'surface_reply',
  'prompt_builder',
  'llm_stream',
  'airlock_egress',
]

const LIVE_TRACE_KEY_NODE_INDEX = Object.freeze(Object.fromEntries(
  LIVE_TRACE_KEY_NODE_IDS.map((nodeId, index) => [nodeId, index])
))

const RECENT_TRACE_TIME_OPTIONS = Object.freeze({
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
})

export function getLiveTraceStageClasses(stage, group) {
  if (stage.status === 'error') {
    return 'border-red-200 bg-red-50 text-red-600'
  }
  if (stage.status === 'running') {
    return 'border-blue-300 bg-blue-50 text-blue-700'
  }
  if (stage.status === 'done') {
    return ''
  }
  return 'border-primary-200 bg-primary-50/60 text-primary/60'
}

export function getLiveTraceOutputPreview(traceData) {
  const summary = traceData?.summary ?? {}
  const output = summary.output_text
  if (typeof output === 'string' && output.trim()) return output.trim()
  if (typeof summary.failure_event === 'string' && summary.failure_event.trim()) return summary.failure_event.trim()
  const latestNode = getLatestPrimaryTraceNode(traceData) || getLatestTraceNode(traceData)
  if (typeof latestNode?.reason_code === 'string' && latestNode.reason_code.trim()) {
    return formatAlertTitle(latestNode.reason_code.trim())
  }
  if (typeof latestNode?.error === 'string' && latestNode.error.trim()) return latestNode.error.trim()
  return ''
}

export function getLiveTraceCurrentLabel(traceData) {
  const latestNode = getLatestPrimaryTraceNode(traceData) || getLatestTraceNode(traceData)
  if (!latestNode) return '等待节点上报'
  const status = String(latestNode.status || '').toUpperCase()
  return `${getTraceNodeDisplayName(latestNode)} · ${humanizeToken(status || '未知')}`
}

function getLiveTraceKeyNodeSortKey(node) {
  const normalizedId = normalizeTraceGroupNodeId(node?.node_id, node?.node_type)
  if (LIVE_TRACE_KEY_NODE_INDEX[normalizedId] != null) return LIVE_TRACE_KEY_NODE_INDEX[normalizedId]
  if (String(node?.node_id || '').startsWith('sub_agent:')) {
    return (LIVE_TRACE_KEY_NODE_INDEX.surface_reply ?? 999) - 0.5
  }
  return 999
}

function buildLiveTraceKeyNodeSequence(traceData) {
  const nodes = Array.isArray(traceData?.nodes) ? [...traceData.nodes] : []
  const seen = new Set()
  return nodes
    .filter((node) => {
      const normalizedId = normalizeTraceGroupNodeId(node?.node_id, node?.node_type)
      if (String(node?.side || node?.layer || '').toLowerCase() === 'edge') return false
      if (LIVE_TRACE_KEY_NODE_INDEX[normalizedId] != null) return true
      return String(node?.node_id || '').startsWith('sub_agent:')
    })
    .sort((a, b) => {
      const rankDiff = getLiveTraceKeyNodeSortKey(a) - getLiveTraceKeyNodeSortKey(b)
      if (rankDiff !== 0) return rankDiff
      return (a?.ts_ms ?? 0) - (b?.ts_ms ?? 0)
    })
    .filter((node) => {
      const dedupeKey = String(node?.node_id || normalizeTraceGroupNodeId(node?.node_id, node?.node_type))
      if (!dedupeKey || seen.has(dedupeKey)) return false
      seen.add(dedupeKey)
      return true
    })
    .map((node) => ({
      nodeId: node.node_id,
      label: getTraceNodeDisplayName(node),
      status: node.status,
      preview: extractLiveTraceNodePreview(node),
    }))
}

export function buildLiveTraceMetrics(session, traceData) {
  const flow = traceData ? getLiveTraceFlow(traceData) : []
  return {
    flow,
    keyNodeSequence: traceData ? buildLiveTraceKeyNodeSequence(traceData) : [],
    currentLabel: traceData ? getLiveTraceCurrentLabel(traceData) : '等待链路详情',
    outputPreview: traceData ? getLiveTraceOutputPreview(traceData) : '',
    nodeCount: traceData?.node_count ?? 0,
    latencyMs: traceData?.total_latency_ms ?? session?.total_latency_ms ?? null,
    status: traceData?.status ?? session?.status ?? '未知',
  }
}

export function getTraceIssueCount(traceData) {
  return Array.isArray(traceData?.nodes)
    ? traceData.nodes.filter((node) => ['DEGRADED', 'FAIL', 'FAILED', 'ERROR', 'DOWN', 'REJECTED', 'REJECT'].includes(String(node?.status || '').toUpperCase())).length
    : 0
}

export function getTraceRunningCount(traceData) {
  return Array.isArray(traceData?.nodes)
    ? traceData.nodes.filter((node) => ['RUNNING', 'STARTED'].includes(String(node?.status || '').toUpperCase())).length
    : 0
}

export function getTraceActiveStageMeta(traceData) {
  const flow = traceData ? getLiveTraceFlow(traceData) : []
  const activeStages = flow.filter((stage) => stage.count > 0)
  const currentStage = flow.find((stage) => stage.status === 'running') || activeStages[activeStages.length - 1] || null
  return {
    flow,
    activeStageCount: activeStages.length,
    currentStageLabel: currentStage?.label || '等待链路推进',
  }
}

export function getSessionDisplayTitle(session, traceData) {
  const summary = traceData?.summary ?? {}
  const candidates = [
    summary.input_text,
    session?.input_preview,
    session?.request_id,
    traceData?.request_id,
  ]
  for (const candidate of candidates) {
    const preview = pickLiveTracePreviewValue(candidate)
    if (preview) return preview
  }
  return '未命名请求'
}

function truncateLiveTraceText(value, maxLength = 140) {
  if (typeof value !== 'string') return ''
  const normalized = value.trim().replace(/\s+/g, ' ')
  if (!normalized) return ''
  return normalized.length > maxLength ? `${normalized.slice(0, maxLength - 1)}…` : normalized
}

function pickLiveTracePreviewValue(value) {
  if (typeof value === 'string') return truncateLiveTraceText(value)
  if (value == null) return ''
  if (Array.isArray(value)) {
    for (const item of value) {
      const preview = pickLiveTracePreviewValue(item)
      if (preview) return preview
    }
    return ''
  }
  if (typeof value === 'object') {
    const candidates = [
      value.output_text,
      value.text,
      value.message,
      value.content,
      value.detail,
      value.error,
      value.reason,
      value.reason_code ? formatAlertTitle(value.reason_code) : value.reason_code,
      value.label,
      value.summary,
      value.value,
    ]
    for (const candidate of candidates) {
      const preview = pickLiveTracePreviewValue(candidate)
      if (preview) return preview
    }
    try {
      return truncateLiveTraceText(JSON.stringify(value))
    } catch {
      return ''
    }
  }
  return truncateLiveTraceText(String(value))
}

function extractLiveTraceNodePreview(node) {
  if (!node) return ''
  const candidates = [
    node.reason_code ? formatAlertTitle(node.reason_code) : node.reason_code,
    node.error,
    pickLiveTracePreviewValue(node.output_data),
    pickLiveTracePreviewValue(node.input_data),
  ]
  return candidates.find((candidate) => typeof candidate === 'string' && candidate.trim()) || ''
}

function buildSnapshotTimelineEntries(requestId, traceData) {
  const latestNodes = Array.isArray(traceData?.nodes)
    ? [...traceData.nodes].sort((a, b) => (b?.ts_ms ?? 0) - (a?.ts_ms ?? 0)).slice(0, 8)
    : []
  return latestNodes.map((node, index) => ({
    entryId: `snapshot:${requestId}:${node.node_id}:${node.ts_ms ?? index}:${node.status ?? 'UNKNOWN'}`,
    changeType: 'snapshot',
    label: '快照',
    nodeId: node.node_id,
    displayName: getTraceNodeDisplayName(node),
    status: node.status,
    previousStatus: null,
    tsMs: node.ts_ms ?? null,
    preview: extractLiveTraceNodePreview(node),
  }))
}

function buildDeltaTimelineEntries(deltaItem) {
  const requestId = deltaItem?.request_id || deltaItem?.session?.request_id
  const changes = Array.isArray(deltaItem?.changes)
    ? [...deltaItem.changes].sort((a, b) => (b?.node?.ts_ms ?? 0) - (a?.node?.ts_ms ?? 0))
    : []
  return changes.map((change, index) => {
    const node = change?.node ?? {}
    const changeType = change?.change_type || 'updated'
    return {
      entryId: `${changeType}:${requestId}:${node.node_id}:${node.ts_ms ?? index}:${node.status ?? 'UNKNOWN'}`,
      changeType,
      label: changeType === 'added' ? '新增' : '更新',
      nodeId: node.node_id,
      displayName: getTraceNodeDisplayName(node),
      status: node.status,
      previousStatus: change?.previous_status || null,
      tsMs: node.ts_ms ?? null,
      preview: extractLiveTraceNodePreview(node),
    }
  })
}

export function mergeLiveTraceTimelineEntries(previous, deltaItems) {
  const next = { ...previous }
  for (const deltaItem of deltaItems) {
    const requestId = deltaItem?.request_id || deltaItem?.session?.request_id
    if (!requestId) continue
    const incomingEntries = buildDeltaTimelineEntries(deltaItem)
    if (incomingEntries.length === 0) continue
    const mergedEntries = [...incomingEntries, ...(next[requestId] ?? [])]
    const dedupedEntries = []
    const seen = new Set()
    for (const entry of mergedEntries) {
      if (!entry?.entryId || seen.has(entry.entryId)) continue
      seen.add(entry.entryId)
      dedupedEntries.push(entry)
      if (dedupedEntries.length >= 18) break
    }
    next[requestId] = dedupedEntries
  }
  return next
}

export function getLiveTraceTimelineEntries(item, timelineEntries) {
  if (Array.isArray(timelineEntries) && timelineEntries.length > 0) {
    return timelineEntries
  }
  const requestId = item?.session?.request_id || item?.trace?.request_id || 'trace'
  return buildSnapshotTimelineEntries(requestId, item?.trace)
}

export function getLiveTraceChangeBadgeClass(changeType) {
  if (changeType === 'added') return 'border-emerald-200 bg-emerald-50 text-emerald-700'
  if (changeType === 'updated') return 'border-blue-200 bg-blue-50 text-blue-700'
  return 'border-primary-200 bg-primary-50/60 text-primary-700'
}

export function formatEventPayloadPreview(payload, maxLength = 180) {
  if (!payload || typeof payload !== 'object') return '暂无 payload'
  try {
    const raw = JSON.stringify(payload)
    if (!raw) return '暂无 payload'
    return raw.length > maxLength ? `${raw.slice(0, maxLength)}...` : raw
  } catch {
    return 'payload 无法序列化'
  }
}

export function getRecentRequestStatusMeta(status) {
  const normalized = String(status || '').toLowerCase()
  if (normalized === 'ok' || normalized === 'success') {
    return { label: '成功', className: 'text-green-600' }
  }
  if (normalized === 'rejected') {
    return { label: '拒绝', className: 'text-orange-500' }
  }
  if (normalized === 'error' || normalized === 'fail') {
    return { label: '异常', className: 'text-red-500' }
  }
  return { label: String(status || '未知'), className: 'text-gray-500' }
}

export function formatRecentTraceTime(tsMs) {
  return formatDateTime(tsMs, {
    placeholder: '-',
    options: RECENT_TRACE_TIME_OPTIONS,
  })
}

function normalizeOptionalKeyValue(payload, keys) {
  if (!payload || typeof payload !== 'object') return ''
  for (const key of keys) {
    const value = payload[key]
    if (value == null) continue
    const normalized = String(value).trim()
    if (normalized) return normalized
  }
  return ''
}

export function isTribeEventLinkedToSelection(event, { requestId, sessionId, conversationId }) {
  const payload = event?.payload ?? {}
  const payloadRequestId = normalizeOptionalKeyValue(payload, ['request_id', 'requestId'])
  const payloadSessionId = normalizeOptionalKeyValue(payload, ['session_id', 'sessionId'])
  const payloadConversationId = normalizeOptionalKeyValue(payload, ['conversation_id', 'conversationId'])

  if (requestId && payloadRequestId === requestId) return true
  if (sessionId && payloadSessionId === sessionId) return true
  if (conversationId && payloadConversationId === conversationId) return true
  return false
}
