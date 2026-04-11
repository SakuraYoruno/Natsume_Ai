import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  Background,
  Controls,
  MarkerType,
  ReactFlow,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import {
  Activity,
  ArrowLeft,
  Bot,
  Clock3,
  Database,
  Filter,
  GitBranch,
  Layers3,
  MessageSquareText,
  RefreshCw,
  ShieldAlert,
  Sparkles,
  Wand2,
} from 'lucide-react'
import {
  connectTraceRequestLive,
  fetchImmuneGateReplay,
  fetchTribeEvents,
  fetchTraceNodes,
  fetchTraceStreamEvents,
  fetchRetrievalRuntime,
} from '../api/obs.js'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import LivingPulseStrip from '../components/LivingPulseStrip.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import CustomNode from '../components/trace/CustomNode.jsx'
import NodeDetailPanel from '../components/trace/NodeDetailPanel.jsx'
import PageHero from '../components/PageHero.jsx'
import StatusBadge from '../components/StatusBadge.jsx'
import { getTraceNodeDisplayName, normalizeTraceNodeKey } from '../components/trace/traceNodeText.js'
import { applyDagreLayout } from '../utils/traceLayout.js'

const ISSUE_STATUSES = new Set(['DEGRADED', 'FAIL', 'FAILED', 'ERROR', 'DOWN', 'REJECT', 'REJECTED'])
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
    nodeIds: ['query_planner', 'agentic_rag', 'recall_fusion', 'retrieval_embedding', 'retrieval_rerank', 'retrieval_evidence_pack', 'retrieval_pipeline'],
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
    description: '响应策略与 Top Agent 的主链路判断、路由结果。',
    icon: Bot,
    nodeIds: ['response_policy', 'top_agent'],
  },
  {
    id: 'agent',
    label: '代理调度',
    description: 'Dispatch Agent、各类 Sub Agent 与 Chat Agent 的实际执行。',
    icon: Layers3,
    nodeIds: ['dispatch_agent', 'sub_agent_code', 'sub_agent_translate', 'sub_agent_intent', 'sub_agent_vision', 'chat_agent'],
  },
  {
    id: 'llm',
    label: '提示与模型',
    description: 'Prompt Builder 与 LLM Stream 的最终出文过程。',
    icon: Wand2,
    nodeIds: ['prompt_builder', 'llm_stream', 'llm_gateway', 'top_agent_llm_stream'],
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
  if (merged.includes('response_policy') || merged.includes('top_agent') || merged.includes('cortex_agent')) return 'orchestration'
  if (merged.includes('dispatch') || merged.includes('sub_agent') || merged.includes('chat_agent') || merged.includes('ans_agent')) return 'agent'
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
    rerankerModel:
      latest?.rerank_model_id
      || retrievalRuntime?.reranker?.routes?.[0]?.model_id
      || '未观测到',
    embeddingProvider:
      latest?.effective_embedding_provider
      || latest?.embedding_provider
      || retrievalRuntime?.embedding?.routes?.[0]?.provider
      || '-',
    rerankerProvider:
      latest?.effective_rerank_provider
      || latest?.rerank_provider
      || retrievalRuntime?.reranker?.routes?.[0]?.provider
      || '-',
    alignment: retrievalRuntime?.read_write_model_aligned ? '读写对齐' : '读写漂移',
    candidateCount: latest?.candidate_count ?? null,
    rerankTopK: latest?.rerank_topk ?? null,
    embeddingFallbacks: retrievalRuntime?.recent_embedding_fallbacks ?? 0,
    rerankerFallbacks: retrievalRuntime?.recent_reranker_fallbacks ?? 0,
    lastObservedAt: latest?.ts_ms ?? retrievalRuntime?.timestamp ?? null,
    latest,
  }
}

function JsonBlock({ data, emptyLabel = '暂无数据' }) {
  if (data == null || data === '') return <div className="text-xs text-gray-400">{emptyLabel}</div>
  const text = typeof data === 'string' ? data : safeStringify(data)
  return <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all rounded-lg border border-primary-100/70 bg-white/75 p-3 text-xs text-gray-700">{text}</pre>
}

function SectionCard({ icon: Icon, title, description, aside = null, children, className = '' }) {
  return (
    <section className={`card overflow-hidden rounded-2xl ${className}`}>
      <div className="flex items-start gap-3 border-b border-primary-100/70 px-4 py-4">
        <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-primary-100/70 bg-white/80 text-blue-600">
          <Icon size={18} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-gray-900">{title}</div>
          {description ? <div className="mt-1 text-xs leading-5 text-gray-500">{description}</div> : null}
        </div>
        {aside}
      </div>
      <div className="lifeline-rail mx-4 mt-3 h-1.5">
        <div className="lifeline-wave h-full w-full" />
      </div>
      <div className="p-4">{children}</div>
    </section>
  )
}

function MetricCard({ label, value, hint = null, valueClassName = '' }) {
  return (
    <div className="rounded-xl border border-primary-100/70 bg-white/82 p-4">
      <div className="text-xs font-medium text-gray-500">{label}</div>
      <div className={`mt-1 break-all text-lg font-semibold text-gray-900 ${valueClassName}`}>{value ?? '-'}</div>
      {hint ? <div className="mt-1 text-xs text-gray-400">{hint}</div> : null}
    </div>
  )
}

function MiniStat({ label, value, hint = null, toneClassName = 'text-gray-900' }) {
  return (
    <div className="rounded-xl border border-primary-100/70 bg-white/72 p-3">
      <div className="text-xs font-medium text-gray-500">{label}</div>
      <div className={`mt-1 break-all text-sm font-semibold ${toneClassName}`}>{value ?? '-'}</div>
      {hint ? <div className="mt-1 text-xs text-gray-500">{hint}</div> : null}
    </div>
  )
}

function TextPreviewCard({ label, text, emptyLabel, tone = 'default' }) {
  const toneClassName = tone === 'error'
    ? 'border-red-200 bg-red-50/65'
    : 'border-primary-100/70 bg-white/72'
  const labelToneClassName = tone === 'error' ? 'text-red-600' : 'text-gray-500'
  return (
    <div className={`rounded-xl border p-4 ${toneClassName}`}>
      <div className={`text-xs font-medium ${labelToneClassName}`}>{label}</div>
      <div className="mt-2 whitespace-pre-wrap break-all text-sm leading-6 text-gray-800">
        {text || <span className="text-gray-400">{emptyLabel}</span>}
      </div>
    </div>
  )
}

function TagList({ label, items, emptyLabel = '暂无', tone = 'default' }) {
  const values = Array.isArray(items)
    ? items.map((item) => String(item || '').trim()).filter(Boolean)
    : []
  const toneClassName = tone === 'warn'
    ? 'border-amber-200 bg-amber-50 text-amber-700'
    : tone === 'error'
      ? 'border-red-200 bg-red-50 text-red-700'
      : 'border-primary-200 bg-primary-50/65 text-primary-700'
  return (
    <div className="rounded-xl border border-primary-100/70 bg-white/82 p-4">
      <div className="text-xs font-medium text-gray-500">{label}</div>
      {values.length === 0 ? (
        <div className="mt-2 text-sm text-gray-400">{emptyLabel}</div>
      ) : (
        <div className="mt-2 flex flex-wrap gap-2">
          {values.map((item) => (
            <span key={`${label}:${item}`} className={`rounded-full border px-2.5 py-1 text-xs ${toneClassName}`}>
              {item}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

function RawJsonDetails({ title, data, emptyLabel = '暂无数据' }) {
  return (
    <details className="rounded-xl border border-primary-100/70 bg-white/82">
      <summary className="cursor-pointer list-none px-4 py-3 text-sm font-medium text-gray-700">
        {title}
      </summary>
      <div className="border-t border-primary-100/70 p-4">
        <JsonBlock data={data} emptyLabel={emptyLabel} />
      </div>
    </details>
  )
}

function extractRecallEvaluation(recall) {
  const stages = Array.isArray(recall?.stages) ? recall.stages : []
  for (const stage of stages) {
    const payload = stage?.payload
    const evaluation = payload?.agentic_meta?.final_evaluation || payload?.final_evaluation
    if (evaluation && typeof evaluation === 'object') return evaluation
  }
  return null
}

function extractRecallTopItems(recall) {
  if (Array.isArray(recall?.evidence_topk) && recall.evidence_topk.length > 0) return recall.evidence_topk
  if (Array.isArray(recall?.topk) && recall.topk.length > 0) return recall.topk
  if (Array.isArray(recall?.items) && recall.items.length > 0) return recall.items
  return []
}

function RecallSummaryPanel({ recall }) {
  const evaluation = extractRecallEvaluation(recall)
  const topItems = extractRecallTopItems(recall).slice(0, 4)
  const selectedRefs = Array.isArray(recall?.selected_refs) ? recall.selected_refs : []
  const evidenceRefs = Array.isArray(recall?.evidence_refs) ? recall.evidence_refs : []

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
        <MiniStat label="候选数" value={recall?.candidate_count ?? '-'} hint={recall?.content_available ? '已持久化片段' : '仅阶段载荷'} />
        <MiniStat label="路由数" value={recall?.route_count ?? '-'} hint={recall?.route_error_count != null ? `错误 ${recall.route_error_count}` : '暂无路由错误'} />
        <MiniStat label="证据包" value={(recall?.evidence_refs_count ?? evidenceRefs.length) || '-'} hint={recall?.pack_id || '未生成 pack_id'} />
        <MiniStat label="Rerank TopK" value={recall?.topk_count ?? recall?.rerank_topk ?? '-'} hint={recall?.rerank_provider || '未记录 reranker'} />
        <MiniStat label="覆盖信心" value={evaluation?.confidence != null ? `${Math.round(Number(evaluation.confidence) * 100)}%` : '-'} hint={evaluation?.suggestion || '无建议'} toneClassName={evaluation?.confidence != null && Number(evaluation.confidence) < 0.6 ? 'text-amber-600' : 'text-gray-900'} />
        <MiniStat label="覆盖结论" value={evaluation?.sufficient == null ? '-' : (evaluation.sufficient ? '充分' : '不足')} hint={evaluation?.top_score != null ? `Top score ${evaluation.top_score}` : '未记录 top score'} toneClassName={evaluation?.sufficient === false ? 'text-amber-600' : 'text-gray-900'} />
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <TagList label="缺失维度" items={evaluation?.missing_aspects} emptyLabel="无缺失项" tone="warn" />
        <TagList label="Selected Refs" items={selectedRefs.length > 0 ? selectedRefs : evidenceRefs} emptyLabel="暂无证据引用" />
      </div>

      <div className="rounded-xl border border-primary-100/70 bg-white/78 p-4">
        <div className="text-xs font-medium text-gray-500">Top 召回片段</div>
        <div className="lifeline-rail mt-2 h-1">
          <div className="lifeline-wave h-full w-full" />
        </div>
        {topItems.length === 0 ? (
          <div className="mt-2 text-sm text-gray-400">暂无可展示片段</div>
        ) : (
          <div className="mt-3 space-y-3">
            {topItems.map((item, index) => (
              <div key={`recall-top-${index}`} className="rounded-lg border border-primary-100/70 bg-white/92 p-3">
                <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
                  <span className="rounded-full border border-blue-200 bg-blue-50 px-2 py-0.5 text-blue-700">#{index + 1}</span>
                  {item?.score != null ? <span>score {item.score}</span> : null}
                  {item?.confidence != null ? <span>confidence {item.confidence}</span> : null}
                  {item?.metadata?.store_id ? <span>{item.metadata.store_id}</span> : null}
                  {item?.doc_id ? <span className="font-mono">{item.doc_id}</span> : null}
                </div>
                <div className="mt-2 whitespace-pre-wrap break-all text-sm leading-6 text-gray-700">
                  {truncateText(item?.snippet || item?.text || item?.content || '', 280) || '暂无摘要文本'}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function AuditSummaryPanel({ summary }) {
  const nodeTimingEntries = Object.entries(summary?.node_timings_ms || {})
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
        <MiniStat label="任务类型" value={summary?.task_type || '-'} />
        <MiniStat label="决策原因码" value={summary?.reason_code || '-'} hint={summary?.decision || '未记录决策'} />
        <MiniStat label="Governor" value={summary?.governor_passed == null ? '-' : (summary.governor_passed ? '通过' : '拦截')} hint={summary?.airlock_blocked ? 'Airlock 拦截' : 'Airlock 未拦截'} toneClassName={summary?.failure_event ? 'text-red-600' : 'text-gray-900'} />
        <MiniStat label="失败事件" value={summary?.failure_event || '-'} hint={summary?.final_response_chars != null ? `输出 ${summary.final_response_chars} chars` : '未记录输出长度'} toneClassName={summary?.failure_event ? 'text-red-600' : 'text-gray-900'} />
        <MiniStat label="路由选择" value={summary?.routing_selected || '-'} />
        <MiniStat label="审计标签数" value={Array.isArray(summary?.audit_tags) ? summary.audit_tags.length : 0} hint={Array.isArray(summary?.metadata_keys) ? `${summary.metadata_keys.length} 个 metadata key` : '无 metadata key'} />
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <TagList label="Audit Tags" items={summary?.audit_tags} emptyLabel="暂无审计标签" />
        <TagList label="Emotion Tags" items={summary?.emotion_tags} emptyLabel="暂无情感标签" />
        <TagList label="Metadata Keys" items={summary?.metadata_keys} emptyLabel="暂无 metadata keys" />
        <TagList label="节点计时" items={nodeTimingEntries.map(([key, value]) => `${key}: ${value}ms`)} emptyLabel="暂无节点计时" />
      </div>
    </div>
  )
}

function RetrievalRuntimeOverviewPanel({ retrievalMeta, retrievalRuntime }) {
  const latest = retrievalMeta?.latest || {}
  const selectedRefs = Array.isArray(latest?.selected_refs) ? latest.selected_refs : []
  const evidenceRefs = Array.isArray(latest?.evidence_refs) ? latest.evidence_refs : []
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
        <MiniStat label="输出状态" value={latest?.output_status || latest?.status || '-'} hint={latest?.query_length != null ? `query length ${latest.query_length}` : '未记录 query_length'} />
        <MiniStat label="Recall 候选" value={latest?.recall_candidates ?? latest?.candidate_count ?? '-'} hint={latest?.recall_candidates_deduped != null ? `去重后 ${latest.recall_candidates_deduped}` : '未记录去重后候选'} />
        <MiniStat label="证据引用" value={latest?.evidence_refs ?? latest?.evidence_refs_count ?? '-'} hint={selectedRefs.length > 0 ? `selected ${selectedRefs.length}` : '未记录 selected refs'} />
        <MiniStat label="Embedding 提供方" value={latest?.effective_embedding_provider || latest?.embedding_provider || retrievalMeta?.embeddingProvider || '-'} hint={latest?.embedding_model_id || retrievalMeta?.embeddingModel || '未记录 embedding model'} toneClassName={latest?.fallback_embedding_used ? 'text-amber-600' : 'text-gray-900'} />
        <MiniStat label="Reranker 提供方" value={latest?.effective_rerank_provider || latest?.rerank_provider || retrievalMeta?.rerankerProvider || '-'} hint={latest?.rerank_model_id || retrievalMeta?.rerankerModel || '未记录 reranker model'} toneClassName={latest?.fallback_reranker_used ? 'text-amber-600' : 'text-gray-900'} />
        <MiniStat label="Fallback" value={(latest?.fallback_embedding_used || latest?.fallback_reranker_used) ? '已发生' : '未发生'} hint={latest?.fallback_embedding_reason_code || latest?.fallback_reranker_reason_code || '无 fallback reason'} toneClassName={(latest?.fallback_embedding_used || latest?.fallback_reranker_used) ? 'text-amber-600' : 'text-gray-900'} />
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <TagList label="Selected Refs" items={selectedRefs} emptyLabel="暂无 selected refs" />
        <TagList label="Evidence Refs" items={evidenceRefs} emptyLabel="暂无 evidence refs" />
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <RawJsonDetails title="原始运行时快照" data={latest} emptyLabel="暂无本次运行时观测" />
        <RawJsonDetails title="原始运行时总览" data={retrievalRuntime} emptyLabel="暂无检索运行时数据" />
      </div>
    </div>
  )
}

function dedupeStreamEvents(...sources) {
  const merged = []
  const seen = new Set()
  for (const source of sources) {
    for (const item of Array.isArray(source) ? source : []) {
      const key = `${item?.ts_ms ?? ''}|${item?.phase ?? ''}|${item?.event_type ?? ''}|${item?.node_id ?? ''}|${item?.chunk ?? ''}`
      if (seen.has(key)) continue
      seen.add(key)
      merged.push(item)
    }
  }
  return merged.sort((a, b) => (b?.ts_ms ?? 0) - (a?.ts_ms ?? 0))
}

function buildDeltaEntries(deltaItem) {
  return (Array.isArray(deltaItem?.changes) ? deltaItem.changes : []).map((change, index) => {
    const node = change?.node ?? {}
    const changeType = change?.change_type || 'updated'
    return {
      entryId: `${changeType}:${node.node_id}:${node.ts_ms ?? index}`,
      label: changeType === 'added' ? '新增' : '更新',
      nodeId: node.node_id,
      displayName: getTraceNodeDisplayName(node),
      status: node.status,
      previousStatus: change?.previous_status || null,
      tsMs: node.ts_ms ?? null,
      preview: getNodePreview(node),
    }
  })
}

function mergeDeltaEntries(previous, incoming) {
  const merged = [...incoming, ...previous]
  const deduped = []
  const seen = new Set()
  for (const entry of merged) {
    if (!entry?.entryId || seen.has(entry.entryId)) continue
    seen.add(entry.entryId)
    deduped.push(entry)
    if (deduped.length >= 20) break
  }
  return deduped
}

function StageOverviewPanel({ nodes, selectedNode, onSelectNode }) {
  const stageSnapshots = useMemo(() => buildStageSnapshots(nodes), [nodes])

  return (
    <SectionCard
      icon={Layers3}
      title="阶段链路总览"
      description="把当前 request 拆成边缘 → 入口 → 检索 → 人格 → 编排 → 代理 → 模型 → 输出八层，直接看每层有没有跑到、跑得是否合理。"
      aside={<div className="text-xs text-gray-400">{stageSnapshots.filter((item) => item.observedNodes.length > 0).length}/{stageSnapshots.length} 层已命中</div>}
    >
      <div className="space-y-3">
        {stageSnapshots.map((stage) => {
          const Icon = stage.icon
          const activeNodeId = selectedNode?.node_id
          const isSelected = stage.observedNodes.some((node) => node.node_id === activeNodeId)
          const latestNode = stage.latestNode
          const immuneMeta = stage.id === 'immune' ? buildImmuneStageMeta(stage.observedNodes) : null
          return (
            <div
              key={stage.id}
              className={`rounded-2xl border p-4 transition-colors ${isSelected ? 'border-blue-300 bg-blue-50/55 shadow-[0_10px_24px_rgba(59,130,246,0.12)]' : 'border-primary-100/80 bg-white/82 hover:border-primary-200/80'}`}
            >
              <div className="flex items-start gap-3">
                <span className={`mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${stage.observedNodes.length > 0 ? 'border border-primary-100/70 bg-white/95 text-blue-600 shadow-sm' : 'border border-primary-100/70 bg-primary-50/70 text-primary-300'}`}>
                  <Icon size={18} />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <div className="text-sm font-semibold text-gray-900">{stage.label}</div>
                    <StatusBadge status={normalizeStatus(stage.status)} label={stage.observedNodes.length > 0 ? stage.status : '未命中'} />
                    <span className="text-xs text-gray-400">{stage.observedNodes.length} 个节点</span>
                    {latestNode ? <span className="text-xs text-gray-400">最近 {formatDateTime(latestNode.ts_ms)}</span> : null}
                  </div>
                  <div className="mt-1 text-xs leading-5 text-gray-500">{stage.description}</div>
                  {stage.preview ? <div className="mt-2 break-all text-sm leading-6 text-gray-700">{stage.preview}</div> : null}
                  {immuneMeta && (immuneMeta.decision || immuneMeta.riskLevel || immuneMeta.reasonTags.length > 0) ? (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {(immuneMeta.decision || immuneMeta.riskLevel) ? (
                        <span className="inline-flex items-center rounded-full border border-orange-200 bg-orange-50 px-2 py-0.5 text-[11px] font-medium text-orange-700">
                          {(immuneMeta.decision || 'IMMUNE').toUpperCase()}
                          {immuneMeta.riskLevel ? `/${String(immuneMeta.riskLevel).toLowerCase()}` : ''}
                        </span>
                      ) : null}
                      {immuneMeta.reasonTags.slice(0, 2).map((reasonCode) => (
                        <span key={`${stage.id}-${reasonCode}`} className="inline-flex items-center rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700">
                          {reasonCode}
                        </span>
                      ))}
                    </div>
                  ) : null}

                  <div className="mt-3 flex flex-wrap gap-2">
                    {stage.expectedEntries.map((entry) => (
                      <button
                        key={entry.nodeId}
                        className={`inline-flex items-center gap-2 rounded-full border px-2.5 py-1 text-xs transition-colors ${entry.hit ? 'border-blue-200 bg-white text-gray-700 hover:border-blue-300' : 'border-dashed border-primary-200/70 bg-primary-50/45 text-primary-400'}`}
                        onClick={() => entry.node && onSelectNode?.(entry.node)}
                        disabled={!entry.node}
                      >
                        <span>{entry.label}</span>
                        <StatusBadge status={normalizeStatus(entry.status)} />
                      </button>
                    ))}
                    {stage.dynamicNodes.map((node) => (
                      <button
                        key={node.node_id}
                        className="inline-flex items-center gap-2 rounded-full border border-violet-200 bg-violet-50 px-2.5 py-1 text-xs text-violet-700 transition-colors hover:border-violet-300"
                        onClick={() => onSelectNode?.(node)}
                      >
                        <span>{getTraceNodeDisplayName(node)}</span>
                        <StatusBadge status={normalizeStatus(node.status)} />
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </SectionCard>
  )
}

function StreamEventsPanel({ events, loading, error }) {
  const latestEvent = events[0] ?? null

  return (
    <SectionCard
      icon={Activity}
      title="流式事件"
      description="保留 request 级别的实时事件明细，便于核对输出流、阶段切换和节点补报。"
      aside={<div className="text-xs text-gray-400">{latestEvent ? `最新 ${formatDateTime(latestEvent.ts_ms)}` : '等待事件'}</div>}
    >
      <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <MiniStat label="事件总数" value={events.length} />
        <MiniStat label="最新阶段" value={latestEvent?.phase || '-'} hint={latestEvent?.event_type || '暂无事件'} />
        <MiniStat label="最新节点" value={latestEvent?.node_id || '-'} hint={latestEvent ? formatDateTime(latestEvent.ts_ms) : '等待事件'} />
      </div>

      {loading ? <NoticeBanner variant="info" title="加载中">正在拉取流式事件…</NoticeBanner> : null}
      {error ? <NoticeBanner variant="error" title="流式事件拉取失败">{error}</NoticeBanner> : null}
      {!loading && !error && events.length === 0 ? <EmptyStateCard title="暂无流式事件" hint="该请求还没有可展示的事件记录。" className="p-4" /> : null}
      {!loading && !error && events.length > 0 ? (
        <div className="space-y-2">
          {events.map((event, index) => (
            <div key={`${event.event_type}-${event.ts_ms}-${index}`} className="rounded-xl border border-primary-100/70 bg-white/78 px-3 py-3">
              <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
                <span className="rounded-full border border-sky-200 bg-sky-50 px-2 py-0.5 text-sky-700">{event.phase || '-'}</span>
                <span>{event.event_type || '-'}</span>
                <span className="font-mono">{event.node_id || '-'}</span>
                <span className="ml-auto">{formatDateTime(event.ts_ms)}</span>
              </div>
              {getEventPreview(event) ? <div className="mt-2 break-all text-sm text-gray-700">{getEventPreview(event)}</div> : null}
            </div>
          ))}
        </div>
      ) : null}
    </SectionCard>
  )
}

function ImmuneReplayPanel({ replay, loading, error, requestId, onReload }) {
  const summary = replay?.summary || {}
  const decisions = Array.isArray(replay?.decisions) ? replay.decisions : []
  const reasonCodes = Array.isArray(summary?.reason_codes) ? summary.reason_codes : []
  const matchedRules = Array.isArray(summary?.matched_rules) ? summary.matched_rules : []

  return (
    <SectionCard
      icon={ShieldAlert}
      title="Immune 回放"
      description="按当前 request 回放免疫决策链，快速定位误拦截与规则命中。"
      aside={(
        <button className="btn-secondary text-xs" onClick={onReload} disabled={loading}>
          {loading ? '加载中...' : '刷新回放'}
        </button>
      )}
    >
      {loading ? <NoticeBanner variant="info" title="加载中">正在拉取 immune replay…</NoticeBanner> : null}
      {error ? <NoticeBanner variant="error" title="回放拉取失败">{error}</NoticeBanner> : null}
      {!loading && !error && !replay ? (
        <EmptyStateCard title="当前请求暂无 immune 回放" hint="若未命中免疫流程，这里会保持为空。" className="p-4" />
      ) : null}
      {replay ? (
        <div className="space-y-4">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <MiniStat label="request_id" value={requestId || '-'} />
            <MiniStat label="会话" value={replay?.session_id || '-'} />
            <MiniStat label="首/末决策" value={`${summary?.first_decision || '-'} → ${summary?.last_decision || '-'}`} />
            <MiniStat label="事件数 / max_score" value={`${summary?.event_count ?? 0} / ${summary?.max_score ?? '-'}`} />
          </div>

          {replay?.input_preview ? (
            <TextPreviewCard
              label="输入预览"
              text={String(replay.input_preview || '')}
              emptyLabel="暂无输入预览"
            />
          ) : null}

          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <TagList label="Reason Codes" items={reasonCodes} emptyLabel="暂无 reason code" tone="warn" />
            <TagList label="Matched Rules" items={matchedRules} emptyLabel="暂无规则命中" />
          </div>

          <div className="rounded-xl border border-primary-100/70 bg-white/74 p-4">
            <div className="mb-2 text-xs font-medium text-gray-500">决策时间线</div>
            {decisions.length === 0 ? (
              <div className="text-sm text-gray-400">暂无决策事件</div>
            ) : (
              <div className="space-y-2">
                {decisions.map((item, index) => (
                  <div key={`${item.ts_ms}-${item.event_type}-${index}`} className="rounded-lg border border-primary-100/70 bg-white/85 px-3 py-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="rounded-full border border-blue-200 bg-blue-50 px-2 py-0.5 text-xs text-blue-700">
                        {item.event_type || '-'}
                      </span>
                      <span className="rounded-full border border-orange-200 bg-orange-50 px-2 py-0.5 text-xs text-orange-700">
                        {item.decision || '-'}{item.risk_level ? `/${item.risk_level}` : ''}
                      </span>
                      <span className="text-xs text-gray-500">score={item.score ?? '-'}</span>
                      <span className="ml-auto text-xs text-gray-400">{formatDateTime(item.ts_ms)}</span>
                    </div>
                    <div className="mt-1 text-xs text-gray-500">
                      {(item.stage || '-')}{item.phase ? ` · ${item.phase}` : ''}{item.direction ? ` · ${item.direction}` : ''}
                    </div>
                    {Array.isArray(item.reason_codes) && item.reason_codes.length > 0 ? (
                      <div className="mt-1 flex flex-wrap gap-1.5">
                        {item.reason_codes.slice(0, 4).map((code) => (
                          <span key={`${item.ts_ms}-reason-${code}`} className="rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11px] text-amber-700">
                            {code}
                          </span>
                        ))}
                      </div>
                    ) : null}
                    {Array.isArray(item.matched_rules) && item.matched_rules.length > 0 ? (
                      <div className="mt-1 flex flex-wrap gap-1.5">
                        {item.matched_rules.slice(0, 3).map((rule) => (
                          <span key={`${item.ts_ms}-rule-${rule}`} className="rounded-full border border-sky-200 bg-sky-50 px-2 py-0.5 text-[11px] text-sky-700">
                            {rule}
                          </span>
                        ))}
                      </div>
                    ) : null}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      ) : null}
    </SectionCard>
  )
}

function TribeEventsPanel({ requestId, events, loading, error }) {
  const latestEvent = events[0] ?? null

  return (
    <SectionCard
      icon={GitBranch}
      title="Tribe 事件联动"
      description="展示当前 request 关联的跨智能体事件，帮助对齐 Edge/Host 之间的状态衔接。"
      aside={<div className="text-xs text-gray-400">{latestEvent ? `最新 ${formatDateTime(latestEvent.ts_ms)}` : '等待事件'}</div>}
    >
      <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <MiniStat label="关联 request_id" value={requestId || '-'} />
        <MiniStat label="事件数" value={events.length} hint={latestEvent?.event_type || '暂无事件'} />
        <MiniStat
          label="最新 source -> target"
          value={latestEvent ? `${latestEvent.source_agent || '-'} -> ${latestEvent.target_agent || '-'}` : '-'}
          hint={latestEvent ? formatDateTime(latestEvent.ts_ms) : '等待事件'}
        />
      </div>

      {loading ? <NoticeBanner variant="info" title="加载中">正在拉取 Tribe 事件…</NoticeBanner> : null}
      {error ? <NoticeBanner variant="error" title="Tribe 事件拉取失败">{error}</NoticeBanner> : null}
      {!loading && !error && events.length === 0 ? (
        <EmptyStateCard title="暂无 Tribe 事件" hint="当前请求暂未观测到跨智能体事件。" className="p-4" />
      ) : null}
      {!loading && !error && events.length > 0 ? (
        <div className="space-y-2">
          {events.map((event) => {
            const payloadPreview = getTribePayloadPreview(event?.payload)
            return (
              <div key={event.event_id} className="rounded-xl border border-primary-100/70 bg-white/76 px-3 py-3">
                <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
                  <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-emerald-700">
                    {event.event_type || '-'}
                  </span>
                  <span className="font-mono">{event.source_agent || '-'}</span>
                  <span>{'->'}</span>
                  <span className="font-mono">{event.target_agent || '-'}</span>
                  <span className="ml-auto">{formatDateTime(event.ts_ms)}</span>
                </div>
                {payloadPreview ? <div className="mt-2 break-all text-sm text-gray-700">{payloadPreview}</div> : null}
              </div>
            )
          })}
        </div>
      ) : null}
    </SectionCard>
  )
}

function buildSubgraphFlow(subgraph, onSelectNode, selectedNode) {
  const sourceNodes = Array.isArray(subgraph?.nodes) ? subgraph.nodes : []
  const sourceEdges = Array.isArray(subgraph?.edges) ? subgraph.edges : []
  const flowNodes = sourceNodes.map((node) => ({
    id: node.node_id,
    type: 'customNode',
    data: {
      ...node,
      active: selectedNode?.node_id === node.node_id,
      onSelect: () => onSelectNode?.(node),
      layer: node.layer || node.side || 'host',
    },
    position: { x: 0, y: 0 },
  }))
  const flowEdges = sourceEdges.map((edge, index) => ({
    id: `${subgraph?.subgraph_id || 'sg'}:${edge.source}:${edge.target}:${index}`,
    source: edge.source,
    target: edge.target,
    animated: false,
    style: { stroke: '#C7D2FE', strokeWidth: 1.5 },
    markerEnd: {
      type: MarkerType.ArrowClosed,
      color: '#A5B4FC',
      width: 16,
      height: 16,
    },
  }))
  return applyDagreLayout(flowNodes, flowEdges)
}

function SubgraphMiniFlow({ subgraph, selectedNode, onSelectNode }) {
  const flow = useMemo(
    () => buildSubgraphFlow(subgraph, onSelectNode, selectedNode),
    [subgraph, onSelectNode, selectedNode]
  )

  if (!flow.nodes.length) {
    return <EmptyStateCard title="子图无节点" hint="该子图没有可渲染的内部节点。" className="p-4" />
  }

  return (
    <div className="h-64 overflow-hidden rounded-xl border border-primary-100/70 bg-white/86">
      <ReactFlow
        nodes={flow.nodes}
        edges={flow.edges}
        nodeTypes={TRACE_NODE_TYPES}
        fitView
        fitViewOptions={{ padding: 0.18 }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        zoomOnScroll={false}
        panOnDrag
        proOptions={{ hideAttribution: true }}
        defaultEdgeOptions={{ type: 'smoothstep' }}
      >
        <Background color="#F1F5F9" gap={18} size={1} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  )
}

function SubgraphsPanel({ subgraphs, selectedNode, onSelectNode }) {
  return (
    <SectionCard
      icon={Layers3}
      title="子代理内部子图"
      description="展示 sub agent 内部图的提交结果，可直接点选内部节点联动右侧详情。"
      aside={<div className="text-xs text-gray-400">共 {subgraphs.length} 张</div>}
    >
      {subgraphs.length === 0 ? <EmptyStateCard title="暂无子图" hint="当前请求尚未写入可展示的子代理内部图。" className="p-4" /> : (
        <div className="space-y-4">
          {subgraphs.map((subgraph) => {
            const subgraphNodes = Array.isArray(subgraph.nodes) ? subgraph.nodes : []
            const ownerLabel = subgraph.owner_node_id ? getTraceNodeDisplayName(subgraph.owner_node_id) : null
            return (
              <div key={subgraph.subgraph_id} className="rounded-xl border border-primary-100/70 bg-white/76 p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <div className="text-sm font-semibold text-gray-900">{subgraph.subgraph_id}</div>
                  {subgraph.owner_node_id ? (
                    <span className="rounded-full border border-violet-200 bg-violet-50 px-2 py-0.5 text-xs text-violet-700">
                      {ownerLabel}{ownerLabel !== subgraph.owner_node_id ? ` · ${subgraph.owner_node_id}` : ''}
                    </span>
                  ) : null}
                  <span className="rounded-full border border-primary-200 bg-primary-50/60 px-2 py-0.5 text-xs text-primary-700">{subgraph.node_count ?? subgraphNodes.length} 节点</span>
                  <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-xs text-emerald-700">成功 {subgraph.success_count ?? 0}</span>
                  <span className="rounded-full border border-red-200 bg-red-50 px-2 py-0.5 text-xs text-red-600">失败 {subgraph.fail_count ?? 0}</span>
                  <span className="ml-auto text-xs text-gray-400">{formatDateTime(subgraph.committed_at_ms)}</span>
                </div>
                <div className="mt-3">
                  <SubgraphMiniFlow subgraph={subgraph} selectedNode={selectedNode} onSelectNode={onSelectNode} />
                </div>
                <div className="mt-3 space-y-2">
                  {subgraphNodes.map((node, index) => (
                    <button
                      key={`${subgraph.subgraph_id}:${node.node_id}:${index}`}
                      className="w-full rounded-lg border border-primary-100/70 bg-white/86 px-3 py-2 text-left transition-colors hover:bg-blue-50/35"
                      onClick={() => onSelectNode?.(node)}
                    >
                      <div className="flex items-center gap-2">
                        <div className="text-sm font-medium text-gray-800">{getTraceNodeDisplayName(node)}</div>
                        <StatusBadge status={normalizeStatus(node.status)} />
                        <div className="ml-auto text-xs text-gray-400">{formatDateTime(node.ts_ms)}</div>
                      </div>
                      <div className="mt-1 break-all text-xs text-gray-500">{node.node_id}</div>
                      {getNodePreview(node) ? <div className="mt-1 break-all text-xs text-gray-600">{getNodePreview(node)}</div> : null}
                    </button>
                  ))}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </SectionCard>
  )
}

function LiveEntriesPanel({ liveEntries, liveConnected, liveLastTs }) {
  return (
    <SectionCard
      icon={Activity}
      title="实时节点变化"
      description="优先展示 SSE 增量补报，帮助你快速确认当前 request 正在推进到哪一层。"
      aside={(
        <span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs ${liveConnected ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-amber-200 bg-amber-50 text-amber-700'}`}>
          {liveConnected ? 'SSE 已连接' : '重连中 / 轮询补齐'}
        </span>
      )}
    >
      <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <MiniStat label="增量条目" value={liveEntries.length} />
        <MiniStat label="连接状态" value={liveConnected ? '实时流在线' : '实时流重连中'} hint={liveLastTs ? formatDateTime(liveLastTs) : '等待心跳'} />
        <MiniStat label="最新变更" value={liveEntries[0]?.displayName || '-'} hint={liveEntries[0]?.label || '暂无变化'} />
      </div>

      {liveEntries.length > 0 ? (
        <div className="space-y-2">
          {liveEntries.map((entry) => (
            <div key={entry.entryId} className="rounded-xl border border-primary-100/70 bg-white/76 px-3 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <div className="text-sm font-medium text-gray-800">{entry.displayName}</div>
                <StatusBadge status={normalizeStatus(entry.status)} />
                <span className="text-xs text-gray-400">{entry.label}</span>
                {entry.previousStatus ? <span className="text-xs text-gray-400">{entry.previousStatus} → {entry.status}</span> : null}
                <div className="ml-auto text-xs text-gray-400">{formatDateTime(entry.tsMs)}</div>
              </div>
              <div className="mt-1 break-all font-mono text-xs text-gray-500">{entry.nodeId}</div>
              {entry.preview ? <div className="mt-2 break-all text-sm text-gray-700">{entry.preview}</div> : null}
            </div>
          ))}
        </div>
      ) : <EmptyStateCard title="等待节点变化" hint="实时链路就绪后会自动追加节点事件。" className="p-4" />}
    </SectionCard>
  )
}

function UnifiedTimelinePanel({ nodes, tribeEvents, selectedNode, onSelectNode }) {
  const [typeFilter, setTypeFilter] = useState('all')
  const [stageFilter, setStageFilter] = useState('all')
  const [keyword, setKeyword] = useState('')

  const items = useMemo(
    () => buildUnifiedTimelineItems(nodes, tribeEvents),
    [nodes, tribeEvents]
  )
  const stageOptions = useMemo(() => {
    const stageIds = new Set(items.map((item) => item.stageId).filter(Boolean))
    const ordered = TRACE_TIMELINE_STAGE_ORDER.filter((stageId) => stageIds.has(stageId))
    const extras = [...stageIds].filter((stageId) => !ordered.includes(stageId)).sort()
    return [...ordered, ...extras]
  }, [items])
  const filteredItems = useMemo(() => {
    const targetKeyword = keyword.trim().toLowerCase()
    return items.filter((item) => {
      if (typeFilter !== 'all' && item.type !== typeFilter) return false
      if (stageFilter !== 'all' && item.stageId !== stageFilter) return false
      if (!targetKeyword) return true
      const text = `${item.title || ''} ${item.subtitle || ''} ${item.preview || ''} ${getStableStageLabel(item.stageId)} ${item.type || ''}`.toLowerCase()
      return text.includes(targetKeyword)
    })
  }, [items, keyword, stageFilter, typeFilter])

  return (
    <SectionCard
      icon={Clock3}
      title="同轴时间线（节点 + Tribe）"
      description="把节点执行与 Tribe 事件放到同一时间轴，快速定位跨层状态衔接是否顺滑。"
      aside={<div className="text-xs text-gray-400">{filteredItems.length} / {items.length} 条</div>}
    >
      <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <MiniStat label="节点条目" value={nodes.length} />
        <MiniStat label="Tribe 条目" value={tribeEvents.length} />
        <MiniStat label="最新时间" value={items[0]?.ts_ms ? formatDateTime(items[0].ts_ms) : '-'} />
      </div>

      <div className="mb-4 rounded-xl border border-primary-100/70 bg-white/74 p-3">
        <div className="grid grid-cols-1 gap-3 md:grid-cols-[8rem_12rem_minmax(0,1fr)_auto]">
          <select
            className="w-full rounded-lg border border-primary-100/70 bg-white/88 px-3 py-2 text-sm text-gray-700 focus:border-blue-300 focus:outline-none focus:ring-2 focus:ring-blue-100"
            value={typeFilter}
            onChange={(event) => setTypeFilter(event.target.value)}
          >
            <option value="all">全部类型</option>
            <option value="node">仅节点</option>
            <option value="tribe">仅Tribe</option>
          </select>
          <select
            className="w-full rounded-lg border border-primary-100/70 bg-white/88 px-3 py-2 text-sm text-gray-700 focus:border-blue-300 focus:outline-none focus:ring-2 focus:ring-blue-100"
            value={stageFilter}
            onChange={(event) => setStageFilter(event.target.value)}
          >
            <option value="all">全部阶段</option>
            {stageOptions.map((stageId) => (
              <option key={stageId} value={stageId}>{getStableStageLabel(stageId)}</option>
            ))}
          </select>
          <input
            className="w-full rounded-lg border border-primary-100/70 bg-white/88 px-3 py-2 text-sm text-gray-700 focus:border-blue-300 focus:outline-none focus:ring-2 focus:ring-blue-100"
            placeholder="搜索标题、节点ID、payload 关键词"
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
          />
          <button
            className="btn-secondary text-xs"
            onClick={() => {
              setTypeFilter('all')
              setStageFilter('all')
              setKeyword('')
            }}
          >
            重置
          </button>
        </div>
      </div>

      {filteredItems.length === 0 ? (
        <EmptyStateCard title="暂无可对齐数据" hint="等待节点执行或 Tribe 事件上报后会自动出现。" className="p-4" />
      ) : (
        <div className="space-y-2">
          {filteredItems.map((item) => {
            const stageLabel = getStableStageLabel(item.stageId)
            const isNode = item.type === 'node'
            const isSelectedNode = isNode && selectedNode?.node_id === item?.node?.node_id
            const containerClassName = isNode
              ? (isSelectedNode ? 'border-blue-200 bg-blue-50' : 'border-primary-100/70 bg-white/88 hover:bg-blue-50/30')
              : 'border-emerald-200 bg-emerald-50/60'
            return (
              <button
                key={item.key}
                className={`w-full rounded-xl border px-3 py-3 text-left transition-colors ${containerClassName}`}
                onClick={() => { if (isNode) onSelectNode?.(item.node) }}
              >
                <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
                  <span className={`rounded-full border px-2 py-0.5 ${isNode ? 'border-blue-200 bg-blue-50 text-blue-700' : 'border-emerald-200 bg-emerald-50 text-emerald-700'}`}>
                    {isNode ? 'NODE' : 'TRIBE'}
                  </span>
                  <span className="rounded-full border border-primary-200 bg-primary-50/60 px-2 py-0.5 text-primary-700">{stageLabel}</span>
                  <StatusBadge status={normalizeStatus(item.status)} />
                  <span className="ml-auto">{formatDateTime(item.ts_ms)}</span>
                </div>
                <div className="mt-2 text-sm font-semibold text-gray-900 break-all">{item.title}</div>
                <div className="mt-1 text-xs font-mono text-gray-500 break-all">{item.subtitle}</div>
                {item.preview ? <div className="mt-2 text-sm text-gray-700 break-all">{item.preview}</div> : null}
              </button>
            )
          })}
        </div>
      )}
    </SectionCard>
  )
}

function NodeTimelinePanel({ nodes, selectedNode, onSelectNode }) {
  const sections = useMemo(() => buildStageTimelineSections(nodes), [nodes])

  return (
    <SectionCard
      icon={Clock3}
      title="节点时间线"
      description="按阶段分组后再按时间顺序展开，先看当前 request 跑到哪一层，再钻到单个节点。"
      aside={<div className="text-xs text-gray-400">{nodes.length} 个节点</div>}
    >
      {nodes.length === 0 ? (
        <EmptyStateCard title="暂无节点时间线" hint="请求节点上报后会在这里展示。" className="p-4" />
      ) : (
        <div className="space-y-4">
          {sections.map((section) => {
            const Icon = section.icon
            return (
              <div key={section.stageId} className="overflow-hidden rounded-xl border border-primary-100/70 bg-white/72">
                <div className="flex flex-wrap items-start gap-3 border-b border-primary-100/70 bg-white/88 px-4 py-3">
                  <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-primary-100/70 bg-white text-blue-600">
                    <Icon size={18} />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <div className="text-sm font-semibold text-gray-900">{section.label}</div>
                      <span className="rounded-full border border-primary-200 bg-primary-50/60 px-2 py-0.5 text-xs text-primary-700">{section.nodes.length} 个节点</span>
                      {section.issueCount > 0 ? (
                        <span className="rounded-full border border-red-200 bg-red-50 px-2 py-0.5 text-xs text-red-600">
                          异常/降级 {section.issueCount}
                        </span>
                      ) : null}
                      {section.latestNode?.ts_ms ? <span className="text-xs text-gray-400">最近 {formatDateTime(section.latestNode.ts_ms)}</span> : null}
                    </div>
                    <div className="mt-1 text-xs leading-5 text-gray-500">{section.description}</div>
                  </div>
                </div>

                <div className="divide-y divide-primary-100/70">
                  {section.nodes.map((node, index) => {
                    const isSelected = selectedNode?.node_id === node.node_id
                    const isIssue = ISSUE_STATUSES.has(String(node.status || '').toUpperCase())
                    const preview = getNodePreview(node)
                    return (
                      <button
                        key={`${section.stageId}:${node.node_id}:${index}`}
                        className={`w-full px-4 py-4 text-left transition-colors ${isSelected ? 'bg-blue-50/85' : isIssue ? 'bg-red-50/60 hover:bg-red-50' : 'bg-white/92 hover:bg-primary-50/45'}`}
                        onClick={() => onSelectNode(node)}
                      >
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-xs text-gray-400">{String(index + 1).padStart(2, '0')}</span>
                          <div className="min-w-0 flex-1">
                            <div className="truncate text-sm font-semibold text-gray-900">{getTraceNodeDisplayName(node)}</div>
                            <div className="text-xs text-gray-500">{node.node_id}{node.group_id ? ` / ${node.group_id}` : ''}</div>
                          </div>
                          <div className="flex items-center gap-2">
                            {node.latency_ms != null ? <span className="text-xs text-gray-500">{node.latency_ms}ms</span> : null}
                            <span className="text-xs text-gray-400">{formatDateTime(node.ts_ms)}</span>
                            <StatusBadge status={normalizeStatus(node.status)} />
                          </div>
                        </div>
                        {preview ? <div className="mt-2 break-all pl-6 text-sm leading-6 text-gray-600">{preview}</div> : null}
                      </button>
                    )
                  })}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </SectionCard>
  )
}

export default function TraceRequestDetail() {
  const navigate = useNavigate()
  const { requestId } = useParams()
  const [traceData, setTraceData] = useState(null)
  const [fallbackEvents, setFallbackEvents] = useState([])
  const [tribeEvents, setTribeEvents] = useState([])
  const [retrievalRuntime, setRetrievalRuntime] = useState(null)
  const [immuneReplay, setImmuneReplay] = useState(null)
  const [selectedNode, setSelectedNode] = useState(null)
  const [liveEntries, setLiveEntries] = useState([])
  const [liveConnected, setLiveConnected] = useState(false)
  const [liveLastTs, setLiveLastTs] = useState(null)
  const [loading, setLoading] = useState(true)
  const [eventsLoading, setEventsLoading] = useState(false)
  const [tribeEventsLoading, setTribeEventsLoading] = useState(false)
  const [immuneReplayLoading, setImmuneReplayLoading] = useState(false)
  const [error, setError] = useState(null)
  const [eventsError, setEventsError] = useState(null)
  const [tribeEventsError, setTribeEventsError] = useState(null)
  const [immuneReplayError, setImmuneReplayError] = useState(null)

  const loadTrace = useCallback(async ({ showLoading = false } = {}) => {
    if (!requestId) return
    if (showLoading) setLoading(true)
    try {
      const traceResponse = await fetchTraceNodes(requestId)
      setTraceData(traceResponse)
      setError(null)
      void fetchRetrievalRuntime({ request_id: requestId, limit: 5, include_legacy: true })
        .then((runtimeResponse) => {
          setRetrievalRuntime(runtimeResponse)
        })
        .catch(() => {
          setRetrievalRuntime(null)
        })
    } catch (err) {
      setError(err.message)
      setTraceData(null)
      setRetrievalRuntime(null)
    } finally {
      if (showLoading) setLoading(false)
    }
  }, [requestId])

  const loadEvents = useCallback(async ({ showLoading = false } = {}) => {
    if (!requestId) return
    if (showLoading) setEventsLoading(true)
    try {
      const data = await fetchTraceStreamEvents(requestId)
      setFallbackEvents(data.events ?? [])
      setEventsError(null)
    } catch (err) {
      setFallbackEvents([])
      setEventsError(err.message)
    } finally {
      if (showLoading) setEventsLoading(false)
    }
  }, [requestId])

  const loadTribeEvents = useCallback(async ({ showLoading = false } = {}) => {
    if (!requestId) return
    if (showLoading) setTribeEventsLoading(true)
    try {
      const data = await fetchTribeEvents({ request_id: requestId, limit: 120 })
      setTribeEvents(Array.isArray(data?.items) ? data.items : [])
      setTribeEventsError(null)
    } catch (err) {
      setTribeEvents([])
      setTribeEventsError(err.message)
    } finally {
      if (showLoading) setTribeEventsLoading(false)
    }
  }, [requestId])

  const loadImmuneReplay = useCallback(async ({ showLoading = false } = {}) => {
    if (!requestId) return
    if (showLoading) setImmuneReplayLoading(true)
    try {
      const payload = await fetchImmuneGateReplay(requestId, { limit: 160 })
      setImmuneReplay(payload)
      setImmuneReplayError(null)
    } catch (err) {
      const message = String(err?.message || '')
      if (message.includes('HTTP 404')) {
        setImmuneReplay(null)
        setImmuneReplayError(null)
      } else {
        setImmuneReplay(null)
        setImmuneReplayError(message || 'immune replay load failed')
      }
    } finally {
      if (showLoading) setImmuneReplayLoading(false)
    }
  }, [requestId])

  useEffect(() => {
    if (!requestId) return
    setLoading(true)
    void loadTrace({ showLoading: true })
    void loadEvents({ showLoading: true })
    void loadTribeEvents({ showLoading: true })
    void loadImmuneReplay({ showLoading: true })
  }, [requestId, loadTrace, loadEvents, loadTribeEvents, loadImmuneReplay])

  useEffect(() => {
    if (!requestId) return undefined
    const timer = setInterval(() => {
      void loadTribeEvents()
      void loadImmuneReplay()
    }, 10000)
    return () => clearInterval(timer)
  }, [requestId, loadTribeEvents, loadImmuneReplay])

  useEffect(() => {
    if (!requestId) return undefined
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
  }, [requestId])

  const nodes = useMemo(() => {
    const items = Array.isArray(traceData?.nodes) ? [...traceData.nodes] : []
    items.sort((a, b) => (a?.ts_ms ?? 0) - (b?.ts_ms ?? 0))
    return items
  }, [traceData])

  useEffect(() => {
    if (!nodes.length) return setSelectedNode(null)
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

  return (
    <div className="space-y-4">
      <PageHero
        icon={Layers3}
        eyebrow="单请求链路详情"
        title="请求级 Trace 深入查看"
        description="把单条 request 的输入、检索、节点时间线、流式事件和子代理内部图收成一屏，便于逐条排查。"
        gradientClass="from-sky-50 via-white to-violet-50"
        metaItems={[
          traceData?.status ? `状态：${traceData.status}` : '状态：待加载',
          `节点：${traceData?.node_count ?? nodes.length}`,
          `异常/降级：${issueNodeCount}`,
          liveModeText,
        ]}
      />

      <LivingPulseStrip
        title="请求脉冲"
        subtitle="这条 request 的生命流已经收束到单页里，先看它现在的节奏，再继续下钻到节点和事件。"
        nodes={tracePulseNodes}
      />

      <div className="card space-y-3 p-4">
        <div className="flex items-center gap-3 flex-wrap">
          <button className="btn-secondary text-xs" onClick={() => navigate('/trace')}>
            <ArrowLeft size={14} />
            返回
          </button>
          <div className="min-w-0">
            <div className="text-xs text-gray-500">请求链路</div>
            <div className="break-all font-mono text-sm text-gray-900">{requestId}</div>
          </div>
          <div className="ml-auto flex items-center gap-2 flex-wrap">
            {traceData ? <StatusBadge status={normalizeStatus(traceData.status)} label={traceData.status} /> : null}
            {requestId ? (
              <a
                href={`#/automation?immune_replay_request_id=${encodeURIComponent(requestId)}`}
                className="btn-secondary text-xs"
              >
                打开 Immune Ops
              </a>
            ) : null}
            <button className="btn-secondary flex items-center gap-1 text-xs" onClick={() => { void loadTrace({ showLoading: true }); void loadEvents({ showLoading: true }); void loadTribeEvents({ showLoading: true }); void loadImmuneReplay({ showLoading: true }) }}>
              <RefreshCw size={12} />
              刷新
            </button>
          </div>
        </div>
        <div className="lifeline-rail h-1.5">
          <div className="lifeline-wave h-full w-full" />
        </div>
        <div className="grid grid-cols-2 gap-2 text-xs text-gray-500 md:grid-cols-4">
          <div className="rounded-lg border border-primary-100/70 bg-white/70 px-3 py-2">节点：{traceData?.node_count ?? nodes.length}</div>
          <div className="rounded-lg border border-primary-100/70 bg-white/70 px-3 py-2">异常/降级：{issueNodeCount}</div>
          <div className="rounded-lg border border-primary-100/70 bg-white/70 px-3 py-2">流式事件：{streamEvents.length}</div>
          <div className="rounded-lg border border-primary-100/70 bg-white/70 px-3 py-2">{liveModeText}</div>
        </div>
      </div>

      {loading ? <NoticeBanner variant="info" title="加载中">正在加载请求链路详情…</NoticeBanner> : null}
      {error ? <NoticeBanner variant="error" title="链路详情加载失败">{error}</NoticeBanner> : null}
      {!loading && !error && traceDegraded ? (
        <NoticeBanner variant="warning" title="当前为降级链路视图">
          {degradedMessage || '主链路数据已返回，部分扩展数据稍后补齐。'}
          {degradedReason ? `（${degradedReason}）` : ''}
        </NoticeBanner>
      ) : null}

      {!loading && !error && traceData ? (
        <>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-6">
            <MetricCard label="状态" value={traceData.status} hint={`决策 ${summary.decision || '-'}`} valueClassName={issueNodeCount > 0 ? 'text-orange-600' : ''} />
            <MetricCard label="会话" value={traceData.session_id || summary.session_id || '-'} hint={summary.task_type || '无 task_type'} />
            <MetricCard label="总耗时" value={traceData.total_latency_ms != null ? `${traceData.total_latency_ms}ms` : '-'} hint={formatDateTime(traceData.ts_ms)} />
            <MetricCard label="节点覆盖" value={`${traceData.node_count ?? nodes.length}`} hint={`运行中 ${runningNodeCount} · 异常 ${issueNodeCount}`} />
            <MetricCard label="流式事件" value={streamEvents.length} hint={latestStreamEvent ? `最新 ${formatDateTime(latestStreamEvent.ts_ms)}` : '等待事件'} />
            <MetricCard label="子图数" value={subgraphs.length} hint={liveConnected ? '实时连接中' : '轮询模式'} />
          </div>

          <StageOverviewPanel nodes={nodes} selectedNode={selectedNode} onSelectNode={setSelectedNode} />

          <ImmuneReplayPanel
            replay={immuneReplay}
            loading={immuneReplayLoading}
            error={immuneReplayError}
            requestId={requestId}
            onReload={() => { void loadImmuneReplay({ showLoading: true }) }}
          />

          <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
            <SectionCard
              icon={MessageSquareText}
              title="请求概览"
              description="先看本次输入、输出和决策摘要，确认这条链路在语义层面是否走对。"
              aside={summary.failure_event ? <StatusBadge status="error" label="含失败事件" /> : null}
            >
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                <TextPreviewCard label="输入文本" text={inputPreview} emptyLabel="暂无输入文本" />
                <TextPreviewCard
                  label={summary.failure_event ? '输出 / 失败摘要' : '输出文本'}
                  text={outputPreview}
                  emptyLabel="暂无输出文本"
                  tone={summary.failure_event ? 'error' : 'default'}
                />
              </div>

              <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
                <MiniStat label="意图" value={formatIntent(summary.intent)} />
                <MiniStat label="路由选择" value={summary.routing_selected || '-'} hint={summary.reason_code || '无原因码'} />
                <MiniStat label="Bond 阶段" value={summary.bond_stage || '-'} hint={summary.bond_score != null ? `总分 ${summary.bond_score}` : '暂无分值'} />
                <MiniStat label="LLM 调用" value={summary.llm_calls ?? 0} hint={summary.governor_passed == null ? '未记录 governor 结果' : (summary.governor_passed ? 'Governor 通过' : 'Governor 拦截')} />
              </div>

              <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
                <div className="space-y-4">
                  <div className="text-xs font-medium text-gray-500">召回摘要</div>
                  <RecallSummaryPanel recall={summary.recall} />
                  <RawJsonDetails title="原始召回摘要" data={summary.recall} emptyLabel="暂无召回摘要" />
                </div>
                <div className="space-y-4">
                  <div className="text-xs font-medium text-gray-500">审计与摘要</div>
                  <AuditSummaryPanel summary={summary} />
                  <RawJsonDetails title="原始摘要详情" data={summary} emptyLabel="暂无摘要" />
                </div>
              </div>
            </SectionCard>

            <SectionCard
              icon={Database}
              title="检索与运行时"
              description="这里聚合当前请求实际使用的 embedding / reranker 模型、fallback 情况和最近观测值。"
              aside={retrievalMeta.lastObservedAt ? <div className="text-xs text-gray-400">{formatDateTime(retrievalMeta.lastObservedAt)}</div> : null}
            >
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-2">
                <MiniStat label="模型套件" value={retrievalMeta.suiteProfile} hint={retrievalMeta.alignment} />
                <MiniStat label="候选数 / TopK" value={retrievalMeta.candidateCount != null ? `${retrievalMeta.candidateCount}` : '-'} hint={retrievalMeta.rerankTopK != null ? `Rerank TopK ${retrievalMeta.rerankTopK}` : '未记录 TopK'} />
                <MiniStat label="Embedding 模型" value={retrievalMeta.embeddingModel} hint={retrievalMeta.embeddingProvider} toneClassName={retrievalRuntime?.latest?.fallback_embedding_used ? 'text-orange-600' : 'text-gray-900'} />
                <MiniStat label="Reranker 模型" value={retrievalMeta.rerankerModel} hint={retrievalMeta.rerankerProvider} toneClassName={retrievalRuntime?.latest?.fallback_reranker_used ? 'text-orange-600' : 'text-gray-900'} />
                <MiniStat label="Embedding fallback" value={retrievalMeta.embeddingFallbacks} hint={retrievalRuntime?.latest?.fallback_embedding_reason_code || '最近窗口统计'} toneClassName={retrievalMeta.embeddingFallbacks > 0 ? 'text-orange-600' : 'text-gray-900'} />
                <MiniStat label="Reranker fallback" value={retrievalMeta.rerankerFallbacks} hint={retrievalRuntime?.latest?.fallback_reranker_reason_code || '最近窗口统计'} toneClassName={retrievalMeta.rerankerFallbacks > 0 ? 'text-orange-600' : 'text-gray-900'} />
              </div>

              <div className="mt-4">
                <RetrievalRuntimeOverviewPanel retrievalMeta={retrievalMeta} retrievalRuntime={retrievalRuntime} />
              </div>
            </SectionCard>
          </div>

          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <SubgraphsPanel subgraphs={subgraphs} selectedNode={selectedNode} onSelectNode={setSelectedNode} />
            <StreamEventsPanel events={streamEvents} loading={eventsLoading} error={eventsError} />
          </div>

          <TribeEventsPanel
            requestId={requestId}
            events={tribeEvents}
            loading={tribeEventsLoading}
            error={tribeEventsError}
          />

          <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-[minmax(0,1.5fr)_20rem]">
            <div className="space-y-4">
              <LiveEntriesPanel liveEntries={liveEntries} liveConnected={liveConnected} liveLastTs={liveLastTs} />
              <UnifiedTimelinePanel
                nodes={nodes}
                tribeEvents={tribeEvents}
                selectedNode={selectedNode}
                onSelectNode={setSelectedNode}
              />
              <NodeTimelinePanel nodes={nodes} selectedNode={selectedNode} onSelectNode={setSelectedNode} />
            </div>

            <div className="xl:sticky xl:top-4">
              {selectedNode ? <NodeDetailPanel node={selectedNode} onClose={() => setSelectedNode(null)} /> : <EmptyStateCard title="请选择节点" hint="选中左侧节点后可查看输入、输出与链路元数据。" className="p-6" />}
            </div>
          </div>
        </>
      ) : null}
    </div>
  )
}
