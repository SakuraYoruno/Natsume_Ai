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
  Layers3,
  MessageSquareText,
  RefreshCw,
  ShieldAlert,
  Sparkles,
  Wand2,
} from 'lucide-react'
import {
  connectTraceRequestLive,
  fetchTraceNodes,
  fetchTraceStreamEvents,
  fetchRetrievalRuntime,
} from '../api/obs.js'
import DigitalLifeNav from '../components/DigitalLifeNav.jsx'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
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
  return <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all rounded-lg border border-gray-200 bg-gray-50 p-3 text-xs text-gray-700">{text}</pre>
}

function SectionCard({ icon: Icon, title, description, aside = null, children, className = '' }) {
  return (
    <section className={`overflow-hidden rounded-2xl border border-gray-200 bg-white ${className}`}>
      <div className="flex items-start gap-3 border-b border-gray-100 px-4 py-4">
        <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-gray-100 text-blue-600">
          <Icon size={18} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-gray-900">{title}</div>
          {description ? <div className="mt-1 text-xs leading-5 text-gray-500">{description}</div> : null}
        </div>
        {aside}
      </div>
      <div className="p-4">{children}</div>
    </section>
  )
}

function MetricCard({ label, value, hint = null, valueClassName = '' }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4">
      <div className="text-xs font-medium text-gray-500">{label}</div>
      <div className={`mt-1 break-all text-lg font-semibold text-gray-900 ${valueClassName}`}>{value ?? '-'}</div>
      {hint ? <div className="mt-1 text-xs text-gray-400">{hint}</div> : null}
    </div>
  )
}

function MiniStat({ label, value, hint = null, toneClassName = 'text-gray-900' }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-gray-50 p-3">
      <div className="text-xs font-medium text-gray-500">{label}</div>
      <div className={`mt-1 break-all text-sm font-semibold ${toneClassName}`}>{value ?? '-'}</div>
      {hint ? <div className="mt-1 text-xs text-gray-500">{hint}</div> : null}
    </div>
  )
}

function TextPreviewCard({ label, text, emptyLabel, tone = 'default' }) {
  const toneClassName = tone === 'error'
    ? 'border-red-200 bg-red-50/70'
    : 'border-gray-200 bg-gray-50'
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
      : 'border-gray-200 bg-gray-50 text-gray-700'
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4">
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
    <details className="rounded-xl border border-gray-200 bg-white">
      <summary className="cursor-pointer list-none px-4 py-3 text-sm font-medium text-gray-700">
        {title}
      </summary>
      <div className="border-t border-gray-100 p-4">
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

      <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
        <div className="text-xs font-medium text-gray-500">Top 召回片段</div>
        {topItems.length === 0 ? (
          <div className="mt-2 text-sm text-gray-400">暂无可展示片段</div>
        ) : (
          <div className="mt-3 space-y-3">
            {topItems.map((item, index) => (
              <div key={`recall-top-${index}`} className="rounded-lg border border-gray-200 bg-white p-3">
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
          return (
            <div
              key={stage.id}
              className={`rounded-2xl border p-4 transition-colors ${isSelected ? 'border-blue-300 bg-blue-50/70' : 'border-gray-200 bg-gray-50/70'}`}
            >
              <div className="flex items-start gap-3">
                <span className={`mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${stage.observedNodes.length > 0 ? 'bg-white text-blue-600 shadow-sm' : 'bg-gray-100 text-gray-400'}`}>
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

                  <div className="mt-3 flex flex-wrap gap-2">
                    {stage.expectedEntries.map((entry) => (
                      <button
                        key={entry.nodeId}
                        className={`inline-flex items-center gap-2 rounded-full border px-2.5 py-1 text-xs transition-colors ${entry.hit ? 'border-blue-200 bg-white text-gray-700 hover:border-blue-300' : 'border-dashed border-gray-200 bg-gray-100 text-gray-400'}`}
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
            <div key={`${event.event_type}-${event.ts_ms}-${index}`} className="rounded-xl border border-gray-200 bg-gray-50 px-3 py-3">
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
    <div className="h-64 overflow-hidden rounded-xl border border-gray-200 bg-white">
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
              <div key={subgraph.subgraph_id} className="rounded-xl border border-gray-200 bg-gray-50 p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <div className="text-sm font-semibold text-gray-900">{subgraph.subgraph_id}</div>
                  {subgraph.owner_node_id ? (
                    <span className="rounded-full border border-violet-200 bg-violet-50 px-2 py-0.5 text-xs text-violet-700">
                      {ownerLabel}{ownerLabel !== subgraph.owner_node_id ? ` · ${subgraph.owner_node_id}` : ''}
                    </span>
                  ) : null}
                  <span className="rounded-full border border-gray-200 bg-white px-2 py-0.5 text-xs text-gray-600">{subgraph.node_count ?? subgraphNodes.length} 节点</span>
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
                      className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-left transition-colors hover:bg-gray-50"
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
            <div key={entry.entryId} className="rounded-xl border border-gray-200 bg-gray-50 px-3 py-3">
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
              <div key={section.stageId} className="overflow-hidden rounded-xl border border-gray-200 bg-gray-50/70">
                <div className="flex flex-wrap items-start gap-3 border-b border-gray-200 bg-white px-4 py-3">
                  <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-gray-100 text-blue-600">
                    <Icon size={18} />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <div className="text-sm font-semibold text-gray-900">{section.label}</div>
                      <span className="rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-xs text-gray-600">{section.nodes.length} 个节点</span>
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

                <div className="divide-y divide-gray-100">
                  {section.nodes.map((node, index) => {
                    const isSelected = selectedNode?.node_id === node.node_id
                    const isIssue = ISSUE_STATUSES.has(String(node.status || '').toUpperCase())
                    const preview = getNodePreview(node)
                    return (
                      <button
                        key={`${section.stageId}:${node.node_id}:${index}`}
                        className={`w-full px-4 py-4 text-left transition-colors ${isSelected ? 'bg-blue-50' : isIssue ? 'bg-red-50/60 hover:bg-red-50' : 'bg-white hover:bg-gray-50'}`}
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
  const [retrievalRuntime, setRetrievalRuntime] = useState(null)
  const [selectedNode, setSelectedNode] = useState(null)
  const [liveEntries, setLiveEntries] = useState([])
  const [liveConnected, setLiveConnected] = useState(false)
  const [liveLastTs, setLiveLastTs] = useState(null)
  const [loading, setLoading] = useState(true)
  const [eventsLoading, setEventsLoading] = useState(false)
  const [error, setError] = useState(null)
  const [eventsError, setEventsError] = useState(null)

  const loadTrace = useCallback(async ({ showLoading = false } = {}) => {
    if (!requestId) return
    if (showLoading) setLoading(true)
    try {
      const [traceResponse, runtimeResponse] = await Promise.all([
        fetchTraceNodes(requestId),
        fetchRetrievalRuntime({ request_id: requestId, limit: 5, include_legacy: true }).catch(() => null),
      ])
      setTraceData(traceResponse)
      setRetrievalRuntime(runtimeResponse)
      setError(null)
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

  useEffect(() => {
    if (!requestId) return
    setLoading(true)
    setEventsLoading(true)
    Promise.all([loadTrace(), loadEvents({ showLoading: true })]).finally(() => setLoading(false))
  }, [requestId, loadTrace, loadEvents])

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
  const subgraphs = Array.isArray(traceData?.subgraphs) ? traceData.subgraphs : []
  const inputPreview = getSummaryInputText(summary)
  const outputPreview = getSummaryOutputText(summary)
  const latestStreamEvent = streamEvents[0] ?? null
  const issueNodeCount = nodes.filter((node) => ISSUE_STATUSES.has(String(node.status || '').toUpperCase())).length
  const runningNodeCount = nodes.filter((node) => RUNNING_STATUSES.has(String(node.status || '').toUpperCase())).length
  const retrievalMeta = buildRetrievalRuntimeMeta(retrievalRuntime)
  const liveModeText = liveConnected ? 'SSE 实时流已连接' : '实时流重连中 / 轮询补齐'

  return (
    <div className="space-y-4">
      <DigitalLifeNav />

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

      <div className="flex items-center gap-3 flex-wrap">
        <button className="flex items-center gap-1.5 text-sm text-gray-500 transition-colors hover:text-gray-800" onClick={() => navigate('/trace')}>
          <ArrowLeft size={14} />
          返回
        </button>
        <div className="min-w-0">
          <div className="text-xs text-gray-500">请求链路</div>
          <div className="break-all font-mono text-sm text-gray-900">{requestId}</div>
        </div>
        <div className="ml-auto flex items-center gap-2 flex-wrap">
          {traceData ? <StatusBadge status={normalizeStatus(traceData.status)} label={traceData.status} /> : null}
          <button className="btn-secondary flex items-center gap-1 text-xs" onClick={() => { void loadTrace({ showLoading: true }); void loadEvents({ showLoading: true }) }}>
            <RefreshCw size={12} />
            刷新
          </button>
        </div>
      </div>

      {loading ? <NoticeBanner variant="info" title="加载中">正在加载请求链路详情…</NoticeBanner> : null}
      {error ? <NoticeBanner variant="error" title="链路详情加载失败">{error}</NoticeBanner> : null}

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

          <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-[minmax(0,1.5fr)_20rem]">
            <div className="space-y-4">
              <LiveEntriesPanel liveEntries={liveEntries} liveConnected={liveConnected} liveLastTs={liveLastTs} />
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
