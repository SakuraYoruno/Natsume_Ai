import {
  Activity,
  Bot,
  Database,
  Filter,
  Layers3,
  ShieldAlert,
  Sparkles,
  Wand2,
} from 'lucide-react'
import CustomNode from './CustomNode.jsx'
import { getTraceNodeDisplayName, normalizeTraceNodeKey } from './traceNodeText.js'

export const ISSUE_STATUSES = new Set(['DEGRADED', 'FAIL', 'FAILED', 'ERROR', 'DOWN', 'REJECT', 'REJECTED'])
const RUNNING_STATUSES = new Set(['RUNNING', 'STARTED'])
const TRACE_NODE_TYPES = { customNode: CustomNode }
const TRACE_STAGE_GROUPS = [
  {
    id: 'edge',
    label: '边缘输入',
    description: '网关接收、转发与完成回写等边缘事件。',
    icon: Activity,
    nodeIds: ['asr', 'gateway_webhook', 'input_reporter'],
  },
  {
    id: 'entry',
    label: '入口守护',
    description: '输入进入 Host 后的净化与 Governor 判定。',
    icon: ShieldAlert,
    nodeIds: ['host_input', 'airlock_ingress', 'governor'],
  },
  {
    id: 'immune',
    label: '免疫门禁',
    description: 'Immune Gate 风险扫描与拦截/报告决策。',
    icon: ShieldAlert,
    nodeIds: ['immune_gate'],
  },
  {
    id: 'retrieval',
    label: '检索规划',
    description: 'Query Planner、Agentic RAG 与 Recall Fusion 的检索收口。',
    icon: Filter,
    nodeIds: ['query_planner', 'agentic_rag', 'recall_fusion', 'retrieval_embedding', 'retrieval_evidence_pack', 'retrieval_pipeline'],
  },
  {
    id: 'persona',
    label: '人格上下文',
    description: '情感、人设常驻记忆与动机状态的注入结果。',
    icon: Sparkles,
    nodeIds: ['emotion_context', 'persona_resident_memory', 'motive_state'],
  },
  {
    id: 'orchestration',
    label: '编排决策',
    description: '响应策略与 Cortex Orchestrator 的主链路判断、路由结果。',
    icon: Bot,
    nodeIds: ['render_policy', 'cortex_orchestrator'],
  },
  {
    id: 'agent',
    label: '代理调度',
    description: 'Executive Router、各类 Sub Agent 与 Surface Reply 的实际执行。',
    icon: Layers3,
    nodeIds: ['executive_router', 'sub_agent_code', 'sub_agent_translate', 'sub_agent_intent', 'sub_agent_vision', 'surface_reply'],
  },
  {
    id: 'llm',
    label: '提示与模型',
    description: 'Prompt Builder 与 LLM Stream 的最终出文过程。',
    icon: Wand2,
    nodeIds: ['prompt_builder', 'llm_stream', 'llm_gateway', 'cortex_orchestrator_llm_stream'],
  },
  {
    id: 'postprocess',
    label: '收口输出',
    description: '出口净化、记忆/Bond 写入与 TTS/VTS 收尾。',
    icon: Database,
    nodeIds: ['airlock_egress', 'pii_filter', 'episodic_store', 'bond_update', 'audit_snapshot', 'tts', 'vts_driver'],
  },
]
const TRACE_STAGE_OWNER_BY_NODE_ID = Object.fromEntries(
  TRACE_STAGE_GROUPS.flatMap((group) => group.nodeIds.map((nodeId) => [nodeId, group.id]))
)
const TRACE_STAGE_GROUP_META_BY_ID = Object.fromEntries(
  TRACE_STAGE_GROUPS.map((group) => [group.id, group])
)
const TRACE_TIMELINE_STAGE_ORDER = [...TRACE_STAGE_GROUPS.map((group) => group.id), 'other']

function normalizeTraceStageNodeId(node) {
  if (!node) return ''
  const rawId = String(node?.node_id || node?.id || '').trim()
  const normalizedId = normalizeTraceNodeKey(rawId)
  if (normalizedId.startsWith('sub_agent:code')) return 'sub_agent_code'
  if (normalizedId.startsWith('sub_agent:translate')) return 'sub_agent_translate'
  if (normalizedId.startsWith('sub_agent:intent')) return 'sub_agent_intent'
  if (normalizedId.startsWith('sub_agent:vision')) return 'sub_agent_vision'
  if (normalizedId.startsWith('sub_agent:')) return 'sub_agent_intent'
  if (normalizedId.startsWith('bond_update')) return 'bond_update'
  if (normalizedId.startsWith('episodic_store')) return 'episodic_store'
  return normalizedId
}

function normalizeStatus(status) {
  const value = String(status || '').toUpperCase()
  if (['OK', 'UP', 'SUCCESS', 'APPROVE', 'SKIPPED'].includes(value)) return 'healthy'
  if (RUNNING_STATUSES.has(value)) return 'running'
  if (value === 'DEGRADED') return 'degraded'
  if (ISSUE_STATUSES.has(value)) return 'error'
  return 'unknown'
}

function formatDateTime(tsMs) {
  if (!tsMs) return '-'
  return new Date(tsMs).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

function safeStringify(value, spacing = 2) {
  try {
    return JSON.stringify(value, null, spacing)
  } catch {
    return String(value)
  }
}

function truncateText(value, maxLength = 180) {
  const normalized = String(value || '').trim().replace(/\s+/g, ' ')
  if (!normalized) return ''
  return normalized.length > maxLength ? `${normalized.slice(0, maxLength - 1)}…` : normalized
}

function pickPreviewText(value) {
  if (typeof value === 'string') return truncateText(value)
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (value == null) return ''
  if (Array.isArray(value)) {
    for (const item of value) {
      const preview = pickPreviewText(item)
      if (preview) return preview
    }
    return ''
  }
  if (typeof value === 'object') {
    const candidates = [
      value.output_text,
      value.final_response,
      value.reply_text,
      value.reply_preview,
      value.text,
      value.message,
      value.content,
      value.detail,
      value.reason,
      value.error,
      value.summary,
      value.raw_text,
      value.value,
    ]
    for (const candidate of candidates) {
      const preview = pickPreviewText(candidate)
      if (preview) return preview
    }
    return truncateText(safeStringify(value, 0))
  }
  return truncateText(value)
}

function formatIntent(intent) {
  if (typeof intent === 'string') return truncateText(intent, 80)
  if (intent && typeof intent === 'object') {
    return truncateText(
      intent.label
      || intent.intent
      || intent.normalized_intent
      || intent.name
      || safeStringify(intent, 0),
      80
    )
  }
  return '-'
}

function getSummaryInputText(summary) {
  return (
    truncateText(
      summary?.input_text
      || summary?.audit_snapshot?.input_text
      || summary?.audit_snapshot?.input_preview,
      280
    )
    || ''
  )
}

function getSummaryOutputText(summary) {
  return (
    truncateText(
      summary?.output_text
      || summary?.failure_event
      || summary?.audit_snapshot?.output_text
      || summary?.audit_snapshot?.reply_preview,
      280
    )
    || ''
  )
}

function getNodePreview(node) {
  return (
    pickPreviewText(node?.output_data)
    || pickPreviewText(node?.input_data)
    || truncateText(node?.reason_code)
    || truncateText(node?.error)
    || ''
  )
}

function getEventPreview(event) {
  return (
    truncateText(event?.chunk, 140)
    || truncateText(event?.detail, 140)
    || truncateText(event?.message, 140)
    || ''
  )
}

function getTribePayloadPreview(payload) {
  if (!payload || typeof payload !== 'object') return ''
  return (
    pickPreviewText(payload)
    || truncateText(safeStringify(payload, 0), 180)
  )
}

function getStatusSeverity(status) {
  const normalized = normalizeStatus(status)
  return {
    error: 4,
    degraded: 3,
    running: 2,
    healthy: 1,
    unknown: 0,
  }[normalized] ?? 0
}

function getStageStatus(nodes) {
  if (!Array.isArray(nodes) || nodes.length === 0) return 'unknown'
  return nodes.reduce((selected, current) => (
    getStatusSeverity(current?.status) > getStatusSeverity(selected?.status) ? current : selected
  )).status
}

function getStagePreview(nodes) {
  const ordered = [...(Array.isArray(nodes) ? nodes : [])].sort((a, b) => (a?.ts_ms ?? 0) - (b?.ts_ms ?? 0))
  for (let index = ordered.length - 1; index >= 0; index -= 1) {
    const preview = getNodePreview(ordered[index])
    if (preview) return preview
  }
  return ''
}

function buildStageSnapshots(nodes) {
  const nodeList = Array.isArray(nodes) ? nodes : []
  return TRACE_STAGE_GROUPS.map((group) => {
    const observedNodes = nodeList
      .filter((node) => {
        const groupId = String(node?.group_id || '').trim()
        const normalizedId = normalizeTraceStageNodeId(node)
        const explicitOwner = TRACE_STAGE_OWNER_BY_NODE_ID[normalizedId]
        if (explicitOwner) return explicitOwner === group.id
        return groupId === group.id
      })
      .sort((a, b) => (a?.ts_ms ?? 0) - (b?.ts_ms ?? 0))

    const expectedEntries = group.nodeIds.map((nodeId) => {
      const matchedNodes = observedNodes.filter((node) => normalizeTraceStageNodeId(node) === nodeId)
      const latestNode = matchedNodes[matchedNodes.length - 1] ?? null
      return {
        nodeId,
        label: getTraceNodeDisplayName(nodeId),
        node: latestNode,
        hit: matchedNodes.length > 0,
        status: latestNode?.status ?? 'UNKNOWN',
      }
    })

    const dynamicNodes = observedNodes.filter((node) => !group.nodeIds.includes(normalizeTraceStageNodeId(node)))
    const latestNode = observedNodes[observedNodes.length - 1] ?? null
    return {
      ...group,
      observedNodes,
      expectedEntries,
      dynamicNodes,
      latestNode,
      preview: getStagePreview(observedNodes),
      status: getStageStatus(observedNodes),
    }
  })
}

function buildImmuneStageMeta(nodes = []) {
  const orderedNodes = [...(Array.isArray(nodes) ? nodes : [])]
    .sort((a, b) => (a?.ts_ms ?? 0) - (b?.ts_ms ?? 0))

  let decision = ''
  let riskLevel = ''
  const reasonTags = []
  const seenReasonTags = new Set()

  const pushReasonTag = (value) => {
    const normalized = String(value || '').trim()
    if (!normalized || seenReasonTags.has(normalized)) return
    seenReasonTags.add(normalized)
    reasonTags.push(normalized)
  }

  orderedNodes.forEach((node) => {
    const payload = (node?.output_data && typeof node.output_data === 'object') ? node.output_data : {}
    const nestedImmune = (payload?.immune_gate && typeof payload.immune_gate === 'object') ? payload.immune_gate : {}
    const mergedPayload = { ...nestedImmune, ...payload }

    const payloadDecision = String(mergedPayload.decision || '').trim().toUpperCase()
    const payloadRisk = String(mergedPayload.risk_level || '').trim().toLowerCase()
    if (payloadDecision) decision = payloadDecision
    if (payloadRisk) riskLevel = payloadRisk

    const reasonCode = String(node?.reason_code || mergedPayload.reason_code || '').trim()
    if (reasonCode) pushReasonTag(reasonCode)

    const reasonCodes = Array.isArray(mergedPayload.reason_codes) ? mergedPayload.reason_codes : []
    reasonCodes.forEach((code) => pushReasonTag(code))
  })

  return {
    decision: decision || null,
    riskLevel: riskLevel || null,
    reasonTags: reasonTags.slice(0, 3),
  }
}

function resolveTraceStageGroupId(node) {
  const normalizedId = normalizeTraceStageNodeId(node)
  const explicitOwner = TRACE_STAGE_OWNER_BY_NODE_ID[normalizedId]
  if (explicitOwner) return explicitOwner
  const groupId = String(node?.group_id || '').trim()
  if (TRACE_STAGE_GROUP_META_BY_ID[groupId]) return groupId
  const side = String(node?.side || node?.layer || '').trim().toLowerCase()
  if (side === 'edge') return 'edge'
  return 'other'
}

function buildStageTimelineSections(nodes) {
  const sectionMap = Object.fromEntries(
    TRACE_TIMELINE_STAGE_ORDER.map((stageId) => [stageId, []])
  )

  ;(Array.isArray(nodes) ? nodes : []).forEach((node) => {
    const stageId = resolveTraceStageGroupId(node)
    if (!sectionMap[stageId]) sectionMap[stageId] = []
    sectionMap[stageId].push(node)
  })

  return TRACE_TIMELINE_STAGE_ORDER
    .map((stageId) => {
      const items = (sectionMap[stageId] || []).sort((a, b) => (a?.ts_ms ?? 0) - (b?.ts_ms ?? 0))
      if (items.length === 0) return null
      const stageMeta = TRACE_STAGE_GROUP_META_BY_ID[stageId]
      const latestNode = items[items.length - 1] ?? null
      const issueCount = items.filter((node) => ISSUE_STATUSES.has(String(node?.status || '').toUpperCase())).length
      return {
        stageId,
        label: stageMeta?.label || '其他节点',
        description: stageMeta?.description || '未归入主链阶段的节点。',
        icon: stageMeta?.icon || Layers3,
        nodes: items,
        latestNode,
        issueCount,
      }
    })
    .filter(Boolean)
}

const STABLE_STAGE_LABEL_BY_ID = {
  edge: '边缘输入',
  entry: '入口守护',
  immune: '免疫门禁',
  retrieval: '检索规划',
  persona: '人格上下文',
  orchestration: '编排决策',
  agent: '代理调度',
  llm: '提示与模型',
  postprocess: '收口输出',
  other: '其他',
}

function getStableStageLabel(stageId) {
  return STABLE_STAGE_LABEL_BY_ID[stageId] || stageId || '其他'
}

function resolveTribeEventStageId(event) {
  const eventType = String(event?.event_type || '').toLowerCase()
  const sourceAgent = String(event?.source_agent || '').toLowerCase()
  const targetAgent = String(event?.target_agent || '').toLowerCase()
  const payload = event?.payload && typeof event.payload === 'object' ? event.payload : {}
  const payloadText = safeStringify(payload, 0).toLowerCase()
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

function resolveTribeEventStatus(event) {
  const text = String(event?.event_type || '').toLowerCase()
  if (text.includes('degraded') || text.includes('fail') || text.includes('error') || text.includes('reject') || text.includes('blocked')) return 'DEGRADED'
  if (text.includes('start') || text.includes('received') || text.includes('update') || text.includes('end') || text.includes('complete')) return 'OK'
  return 'UNKNOWN'
}

function buildUnifiedTimelineItems(nodes, tribeEvents) {
  const nodeItems = (Array.isArray(nodes) ? nodes : []).map((node, index) => ({
    type: 'node',
    key: `node:${node.node_id}:${node.ts_ms ?? 0}:${index}`,
    ts_ms: Number(node?.ts_ms || 0),
    stageId: resolveTraceStageGroupId(node),
    title: getTraceNodeDisplayName(node),
    subtitle: node?.node_id || '-',
    preview: getNodePreview(node),
    status: String(node?.status || 'UNKNOWN'),
    node,
  }))

  const tribeItems = (Array.isArray(tribeEvents) ? tribeEvents : []).map((event, index) => ({
    type: 'tribe',
    key: `tribe:${event.event_id || index}`,
    ts_ms: Number(event?.ts_ms || 0),
    stageId: resolveTribeEventStageId(event),
    title: event?.event_type || '-',
    subtitle: `${event?.source_agent || '-'} -> ${event?.target_agent || '-'}`,
    preview: getTribePayloadPreview(event?.payload),
    status: resolveTribeEventStatus(event),
    event,
  }))

  return [...nodeItems, ...tribeItems].sort((a, b) => (b.ts_ms ?? 0) - (a.ts_ms ?? 0))
}

function buildRetrievalRuntimeMeta(retrievalRuntime) {
  const latest = retrievalRuntime?.latest ?? null
  return {
    suiteProfile: retrievalRuntime?.suite_profile || '自定义',
    embeddingModel:
      latest?.embedding_model_id
      || retrievalRuntime?.embedding?.routes?.[0]?.model_id
      || '未观测到',
    embeddingProvider:
      latest?.effective_embedding_provider
      || latest?.embedding_provider
      || retrievalRuntime?.embedding?.routes?.[0]?.provider
      || '-',
    alignment: retrievalRuntime?.read_write_model_aligned ? '读写对齐' : '读写漂移',
    candidateCount: latest?.candidate_count ?? null,
    embeddingFallbacks: retrievalRuntime?.recent_embedding_fallbacks ?? 0,
    lastObservedAt: latest?.ts_ms ?? retrievalRuntime?.timestamp ?? null,
    latest,
  }
}

export {
  TRACE_NODE_TYPES,
  TRACE_STAGE_GROUPS,
  TRACE_TIMELINE_STAGE_ORDER,
  buildImmuneStageMeta,
  buildRetrievalRuntimeMeta,
  buildStageSnapshots,
  buildStageTimelineSections,
  buildUnifiedTimelineItems,
  formatDateTime,
  formatIntent,
  getEventPreview,
  getNodePreview,
  getStableStageLabel,
  getSummaryInputText,
  getSummaryOutputText,
  getTribePayloadPreview,
  normalizeStatus,
  safeStringify,
  truncateText,
}
