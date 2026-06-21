import { getTraceNodeDisplayName } from './traceNodeText.js'
import {
  TRACE_TIMELINE_STAGE_ORDER,
  getStableStageLabel,
  resolveTraceStageGroupId,
} from './traceStageModel.js'

function stringifyForClassification(value) {
  try {
    return JSON.stringify(value)
  } catch {
    return String(value || '')
  }
}

export function resolveTribeEventStageId(event) {
  const eventType = String(event?.event_type || '').toLowerCase()
  const sourceAgent = String(event?.source_agent || '').toLowerCase()
  const targetAgent = String(event?.target_agent || '').toLowerCase()
  const payload = event?.payload && typeof event.payload === 'object' ? event.payload : {}
  const payloadText = stringifyForClassification(payload).toLowerCase()
  const merged = `${eventType} ${sourceAgent} ${targetAgent} ${payloadText}`

  if (merged.includes('voice_') || merged.includes('asr') || merged.includes('gateway') || merged.includes('channel_message')) return 'edge'
  if (merged.includes('airlock') || merged.includes('governor') || merged.includes('ingress')) return 'entry'
  if (merged.includes('immune')) return 'immune'
  if (merged.includes('query_planner') || merged.includes('agentic_rag') || merged.includes('retrieval') || merged.includes('recall')) return 'retrieval'
  if (merged.includes('emotion') || merged.includes('body_sense') || merged.includes('motive') || merged.includes('persona') || merged.includes('limbic')) return 'persona'
  if (merged.includes('render_policy') || merged.includes('cortex_orchestrator') || merged.includes('cortex_agent')) return 'orchestration'
  if (merged.includes('executive_router') || merged.includes('sub_agent') || merged.includes('surface_reply') || merged.includes('ans_agent')) return 'agent'
  if (merged.includes('prompt') || merged.includes('llm')) return 'llm'
  if (merged.includes('egress') || merged.includes('pii') || merged.includes('episodic') || merged.includes('bond') || merged.includes('tts') || merged.includes('vts')) return 'postprocess'
  return 'other'
}

export function resolveTribeEventStatus(event) {
  const text = String(event?.event_type || '').toLowerCase()
  if (text.includes('degraded') || text.includes('fail') || text.includes('error') || text.includes('reject') || text.includes('blocked')) return 'DEGRADED'
  if (text.includes('start') || text.includes('received') || text.includes('update') || text.includes('end') || text.includes('complete')) return 'OK'
  return 'UNKNOWN'
}

export function buildUnifiedTimelineItems(nodes, tribeEvents, options = {}) {
  const {
    getNodeTitle = getTraceNodeDisplayName,
    getNodePreview = () => '',
    getTribePayloadPreview = () => '',
  } = options

  const nodeItems = (Array.isArray(nodes) ? nodes : []).map((node, index) => {
    const stageId = resolveTraceStageGroupId(node)
    return {
      type: 'node',
      key: `node:${node.node_id}:${node.ts_ms ?? 0}:${index}`,
      ts_ms: Number(node?.ts_ms || 0),
      stageId,
      stageLabel: getStableStageLabel(stageId),
      title: getNodeTitle(node),
      subtitle: node?.node_id || '-',
      preview: getNodePreview(node),
      status: String(node?.status || 'UNKNOWN'),
      node,
    }
  })

  const tribeItems = (Array.isArray(tribeEvents) ? tribeEvents : []).map((event, index) => {
    const stageId = resolveTribeEventStageId(event)
    return {
      type: 'tribe',
      key: `tribe:${event.event_id || index}`,
      ts_ms: Number(event?.ts_ms || 0),
      stageId,
      stageLabel: getStableStageLabel(stageId),
      title: event?.event_type || '-',
      subtitle: `${event?.source_agent || '-'} -> ${event?.target_agent || '-'}`,
      preview: getTribePayloadPreview(event?.payload),
      status: resolveTribeEventStatus(event),
      event,
    }
  })

  return [...nodeItems, ...tribeItems].sort((a, b) => (b.ts_ms ?? 0) - (a.ts_ms ?? 0))
}

export function buildUnifiedTimelineStageOptions(items = []) {
  const stageIds = new Set((Array.isArray(items) ? items : []).map((item) => item?.stageId).filter(Boolean))
  const ordered = TRACE_TIMELINE_STAGE_ORDER.filter((stageId) => stageIds.has(stageId))
  const extras = [...stageIds].filter((stageId) => !ordered.includes(stageId)).sort()
  return [...ordered, ...extras].map((stageId) => ({
    stageId,
    label: getStableStageLabel(stageId),
  }))
}

export function filterUnifiedTimelineItems(items = [], {
  typeFilter = 'all',
  stageFilter = 'all',
  keyword = '',
} = {}) {
  const targetKeyword = String(keyword || '').trim().toLowerCase()
  return (Array.isArray(items) ? items : []).filter((item) => {
    if (typeFilter !== 'all' && item?.type !== typeFilter) return false
    if (stageFilter !== 'all' && item?.stageId !== stageFilter) return false
    if (!targetKeyword) return true
    const text = [
      item?.title,
      item?.subtitle,
      item?.preview,
      item?.stageLabel || getStableStageLabel(item?.stageId),
      item?.type,
    ].filter(Boolean).join(' ').toLowerCase()
    return text.includes(targetKeyword)
  })
}

export function buildUnifiedTimelineView({ items, typeFilter, stageFilter, keyword } = {}) {
  const sourceItems = Array.isArray(items) ? items : []
  return {
    stageOptions: buildUnifiedTimelineStageOptions(sourceItems),
    filteredItems: filterUnifiedTimelineItems(sourceItems, { typeFilter, stageFilter, keyword }),
    totalCount: sourceItems.length,
  }
}
