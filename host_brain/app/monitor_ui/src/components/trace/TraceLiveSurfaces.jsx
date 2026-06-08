import React from 'react'
import { ChevronRight } from 'lucide-react'
import EmptyStateCard from '../EmptyStateCard.jsx'
import StatusBadge from '../StatusBadge.jsx'
import { formatAlertTitle, humanizeToken } from '../../utils/digitalLife.js'
import {
  GROUPS,
  getLatestPrimaryTraceNode,
  getLatestTraceNode,
  getLiveTraceFlow,
  normalizeBadgeStatus,
  normalizeTraceGroupNodeId,
} from './traceFlowModel.js'
import { getTraceNodeDisplayName } from './traceNodeText.js'

function getLiveTraceStageClasses(stage, group) {
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

function renderImmuneStageTags(stage, keyPrefix) {
  if (stage.groupId !== 'immune') return null
  const decision = String(stage.immuneDecision || '').trim().toUpperCase()
  const riskLevel = String(stage.immuneRiskLevel || '').trim().toLowerCase()
  const reasonTags = Array.isArray(stage.immuneReasonTags) ? stage.immuneReasonTags : []
  if (!decision && !riskLevel && reasonTags.length === 0) return null

  return (
    <>
      {(decision || riskLevel) ? (
        <span
          key={`${keyPrefix}-immune-meta`}
          className="inline-flex items-center rounded-full border border-orange-200 bg-orange-50 px-2 py-1 text-[11px] font-medium text-orange-700"
        >
          {decision || 'IMMUNE'}
          {riskLevel ? `/${riskLevel}` : ''}
        </span>
      ) : null}
      {reasonTags.slice(0, 2).map((code, index) => (
        <span
          key={`${keyPrefix}-immune-reason-${index}`}
          className="inline-flex items-center rounded-full border border-amber-200 bg-amber-50 px-2 py-1 text-[11px] font-medium text-amber-700"
        >
          {code}
        </span>
      ))}
    </>
  )
}

function getLiveTraceOutputPreview(traceData) {
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

function getLiveTraceCurrentLabel(traceData) {
  const latestNode = getLatestPrimaryTraceNode(traceData) || getLatestTraceNode(traceData)
  if (!latestNode) return '等待节点上报'
  const status = String(latestNode.status || '').toUpperCase()
  return `${getTraceNodeDisplayName(latestNode)} · ${humanizeToken(status || '未知')}`
}

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
const LIVE_TRACE_KEY_NODE_INDEX = Object.fromEntries(LIVE_TRACE_KEY_NODE_IDS.map((nodeId, index) => [nodeId, index]))

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

function buildLiveTraceMetrics(session, traceData) {
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

function KeyNodeSequence({ nodes = [] }) {
  if (!Array.isArray(nodes) || nodes.length === 0) return null
  return (
    <div>
      <div className="text-xs font-medium text-gray-500">关键节点链</div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {nodes.map((node, index) => (
          <React.Fragment key={`${node.nodeId}-${index}`}>
            <span className="inline-flex items-center gap-1 rounded-full border border-primary-200 bg-primary-50/45 px-2.5 py-1 text-xs text-primary-700">
              <span>{node.label}</span>
              <StatusBadge status={normalizeBadgeStatus(node.status)} />
            </span>
            {index < nodes.length - 1 ? <ChevronRight size={12} className="text-gray-300" /> : null}
          </React.Fragment>
        ))}
      </div>
    </div>
  )
}

function getTraceIssueCount(traceData) {
  return Array.isArray(traceData?.nodes)
    ? traceData.nodes.filter((node) => ['DEGRADED', 'FAIL', 'FAILED', 'ERROR', 'DOWN', 'REJECTED', 'REJECT'].includes(String(node?.status || '').toUpperCase())).length
    : 0
}

function getTraceRunningCount(traceData) {
  return Array.isArray(traceData?.nodes)
    ? traceData.nodes.filter((node) => ['RUNNING', 'STARTED'].includes(String(node?.status || '').toUpperCase())).length
    : 0
}

function getTraceActiveStageMeta(traceData) {
  const flow = traceData ? getLiveTraceFlow(traceData) : []
  const activeStages = flow.filter((stage) => stage.count > 0)
  const currentStage = flow.find((stage) => stage.status === 'running') || activeStages[activeStages.length - 1] || null
  return {
    flow,
    activeStageCount: activeStages.length,
    currentStageLabel: currentStage?.label || '等待链路推进',
  }
}

function getSessionDisplayTitle(session, traceData) {
  const summary = traceData?.summary ?? {}
  return (
    summary.input_text
    || session?.input_preview
    || session?.request_id
    || traceData?.request_id
    || '未命名请求'
  )
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

function mergeLiveTraceTimelineEntries(previous, deltaItems) {
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

function getLiveTraceTimelineEntries(item, timelineEntries) {
  if (Array.isArray(timelineEntries) && timelineEntries.length > 0) {
    return timelineEntries
  }
  const requestId = item?.session?.request_id || item?.trace?.request_id || 'trace'
  return buildSnapshotTimelineEntries(requestId, item?.trace)
}

function getLiveTraceChangeBadgeClass(changeType) {
  if (changeType === 'added') return 'border-emerald-200 bg-emerald-50 text-emerald-700'
  if (changeType === 'updated') return 'border-blue-200 bg-blue-50 text-blue-700'
  return 'border-primary-200 bg-primary-50/60 text-primary-700'
}

function formatEventPayloadPreview(payload, maxLength = 180) {
  if (!payload || typeof payload !== 'object') return '暂无 payload'
  try {
    const raw = JSON.stringify(payload)
    if (!raw) return '暂无 payload'
    return raw.length > maxLength ? `${raw.slice(0, maxLength)}...` : raw
  } catch {
    return 'payload 无法序列化'
  }
}

const LiveTraceCard = React.memo(function LiveTraceCard({ item, selected, onSelect, onOpenDetail }) {
  const { session, trace } = item
  const metrics = buildLiveTraceMetrics(session, trace)
  const inputPreview = getSessionDisplayTitle(session, trace)

  return (
    <div
      className={`relative overflow-hidden rounded-xl border p-4 transition-all ${
        selected
          ? 'border-blue-300 bg-blue-50/60 shadow-sm shadow-blue-100/40'
          : 'border-primary-100/70 bg-white/88 hover:-translate-y-0.5 hover:border-primary-200 hover:bg-white'
      }`}
    >
      <div className="flex items-start gap-3">
        <button className="min-w-0 flex-1 text-left" onClick={onSelect}>
          <div className="text-sm font-semibold text-gray-900 break-all">{inputPreview}</div>
          <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-gray-500">
            <span className="font-mono">{session.request_id.slice(0, 18)}...</span>
            {session.session_id ? <span>{session.session_id}</span> : null}
            <span>{formatRecentTraceTime(session.ts_ms)}</span>
            {metrics.latencyMs != null ? <span>{metrics.latencyMs}ms</span> : null}
          </div>
        </button>
        <div className="shrink-0 flex items-center gap-2">
          <StatusBadge status={normalizeBadgeStatus(metrics.status)} label={getRecentRequestStatusMeta(metrics.status).label} />
          <button className="btn-secondary text-xs" onClick={onOpenDetail}>
            详情
          </button>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        {metrics.flow.map((stage) => {
          const group = GROUPS.find((itemGroup) => itemGroup.id === stage.groupId)
          const className = getLiveTraceStageClasses(stage, group)
          const style = stage.status === 'done' && group
            ? { background: group.bg, color: group.color, borderColor: group.border }
            : undefined
          return (
            <div key={`${session.request_id}-${stage.groupId}`} className="inline-flex items-center gap-1.5">
              <span
                className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium ${className}`}
                style={style}
              >
                {stage.label}
                {stage.count > 0 ? `${stage.count}/${stage.total}` : '0'}
              </span>
              {renderImmuneStageTags(stage, `${session.request_id}-${stage.groupId}`)}
            </div>
          )
        })}
      </div>

      <div className="mt-3">
        <KeyNodeSequence nodes={metrics.keyNodeSequence} />
      </div>

      <button className="mt-3 block w-full text-left" onClick={onSelect}>
        <div className="text-xs font-medium text-gray-500">当前节点</div>
        <div className="mt-1 text-sm text-gray-700 break-all">{metrics.currentLabel}</div>
        <div className="mt-2 text-xs font-medium text-gray-500">结果摘要</div>
        <div className="mt-1 text-sm text-gray-600 break-all">
          {metrics.outputPreview || '暂无输出，链路仍在推进或尚未落盘。'}
        </div>
      </button>
      <div className="lifeline-rail mt-3 h-1.5">
        <div className="lifeline-wave h-full w-full" />
      </div>
    </div>
  )
})

function LiveTracePreview({ item, timelineEntries = [], onHighlight, onOpenDetail }) {
  if (!item) {
    return (
      <div className="rounded-xl border border-primary-100/70 bg-white/88 p-5">
        <div className="text-sm font-semibold text-gray-900">活跃预览</div>
        <div className="mt-3">
          <EmptyStateCard title="等待最近请求载入" hint="实时链路启动后会自动展示。" className="p-4" />
        </div>
      </div>
    )
  }

  const { session, trace } = item
  const metrics = buildLiveTraceMetrics(session, trace)
  const summary = trace?.summary ?? {}
  const displayTitle = getSessionDisplayTitle(session, trace)
  const recentTimelineEntries = getLiveTraceTimelineEntries(item, timelineEntries)
  const latestNodes = Array.isArray(trace?.nodes)
    ? [...trace.nodes].sort((a, b) => (b?.ts_ms ?? 0) - (a?.ts_ms ?? 0)).slice(0, 6)
    : []

  return (
    <div className="rounded-xl border border-primary-100/70 bg-white/88 p-5 space-y-4">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-gray-900 break-all">{displayTitle}</div>
          <div className="mt-1 text-xs text-gray-500 font-mono break-all">{session.request_id}</div>
        </div>
        <StatusBadge status={normalizeBadgeStatus(metrics.status)} label={getRecentRequestStatusMeta(metrics.status).label} />
      </div>
      <div className="lifeline-rail h-1.5">
        <div className="lifeline-wave h-full w-full" />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-lg border border-primary-100/70 bg-white/70 p-3">
          <div className="text-xs font-medium text-gray-500">当前节点</div>
          <div className="mt-1 text-sm text-gray-800 break-all">{metrics.currentLabel}</div>
        </div>
        <div className="rounded-lg border border-primary-100/70 bg-white/70 p-3">
          <div className="text-xs font-medium text-gray-500">链路规模</div>
          <div className="mt-1 text-sm text-gray-800">{metrics.nodeCount} 个节点</div>
          <div className="mt-1 text-xs text-gray-500">{metrics.latencyMs != null ? `${metrics.latencyMs}ms` : '等待耗时落盘'}</div>
        </div>
      </div>

      <div>
        <div className="text-xs font-medium text-gray-500">阶段进度</div>
        <div className="mt-2 flex flex-wrap gap-2">
          {metrics.flow.map((stage) => {
            const group = GROUPS.find((itemGroup) => itemGroup.id === stage.groupId)
            const className = getLiveTraceStageClasses(stage, group)
            const style = stage.status === 'done' && group
              ? { background: group.bg, color: group.color, borderColor: group.border }
              : undefined
            return (
              <div key={`preview-${session.request_id}-${stage.groupId}`} className="inline-flex items-center gap-1.5">
                <span
                  className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium ${className}`}
                  style={style}
                >
                  {stage.label}
                  {stage.count > 0 ? `${stage.count}/${stage.total}` : '0'}
                </span>
                {renderImmuneStageTags(stage, `preview-${session.request_id}-${stage.groupId}`)}
              </div>
            )
          })}
        </div>
      </div>

      <KeyNodeSequence nodes={metrics.keyNodeSequence} />

      <div className="grid grid-cols-1 gap-3">
        <div>
          <div className="text-xs font-medium text-gray-500">输入</div>
          <div className="mt-1 rounded-lg border border-primary-100/70 bg-white/70 p-3 text-sm text-gray-700 break-all">
            {displayTitle}
          </div>
        </div>
        <div>
          <div className="text-xs font-medium text-gray-500">输出</div>
          <div className="mt-1 rounded-lg border border-primary-100/70 bg-white/70 p-3 text-sm text-gray-700 break-all">
            {metrics.outputPreview || '暂无输出，可能仍在执行或本次未产出文本。'}
          </div>
        </div>
      </div>

      <div>
        <div className="text-xs font-medium text-gray-500">最近节点</div>
        <div className="mt-2 space-y-2">
          {latestNodes.length === 0 ? (
            <EmptyStateCard title="暂无节点详情" hint="当前链路还未上报可展示节点。" className="p-4" />
          ) : latestNodes.map((node) => (
            <div key={`${session.request_id}-${node.node_id}-${node.ts_ms}`} className="rounded-lg border border-primary-100/70 bg-white/70 px-3 py-2">
              <div className="flex items-center gap-2">
                <div className="text-sm font-medium text-gray-800">{getTraceNodeDisplayName(node)}</div>
                <StatusBadge status={normalizeBadgeStatus(node.status)} />
                <div className="ml-auto text-xs text-gray-400">{formatRecentTraceTime(node.ts_ms)}</div>
              </div>
              <div className="mt-1 text-xs text-gray-500 break-all">{node.node_id}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="flex items-center gap-2">
        <button className="btn-secondary text-xs" onClick={onHighlight}>显示总览图</button>
        <button className="btn-primary text-xs" onClick={onOpenDetail}>打开详情</button>
      </div>
    </div>
  )
}

function LiveTraceStreamPreview({ item, timelineEntries = [], onHighlight, onOpenDetail }) {
  if (!item) {
    return (
      <div className="rounded-xl border border-primary-100/70 bg-white/88 p-5">
        <div className="text-sm font-semibold text-gray-900">活跃预览</div>
        <div className="mt-3">
          <EmptyStateCard title="等待最近请求载入" hint="实时链路启动后会自动展示。" className="p-4" />
        </div>
      </div>
    )
  }

  const { session, trace } = item
  const metrics = buildLiveTraceMetrics(session, trace)
  const summary = trace?.summary ?? {}
  const displayTitle = getSessionDisplayTitle(session, trace)
  const recentTimelineEntries = getLiveTraceTimelineEntries(item, timelineEntries)

  return (
    <div className="rounded-xl border border-primary-100/70 bg-white/88 p-5 space-y-4">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-gray-900 break-all">{displayTitle}</div>
          <div className="mt-1 text-xs text-gray-500 font-mono break-all">{session.request_id}</div>
        </div>
        <StatusBadge status={normalizeBadgeStatus(metrics.status)} label={getRecentRequestStatusMeta(metrics.status).label} />
      </div>
      <div className="lifeline-rail h-1.5">
        <div className="lifeline-wave h-full w-full" />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-lg border border-primary-100/70 bg-white/70 p-3">
          <div className="text-xs font-medium text-gray-500">当前节点</div>
          <div className="mt-1 text-sm text-gray-800 break-all">{metrics.currentLabel}</div>
        </div>
        <div className="rounded-lg border border-primary-100/70 bg-white/70 p-3">
          <div className="text-xs font-medium text-gray-500">链路规模</div>
          <div className="mt-1 text-sm text-gray-800">{metrics.nodeCount} 个节点</div>
          <div className="mt-1 text-xs text-gray-500">{metrics.latencyMs != null ? `${metrics.latencyMs}ms` : '等待耗时落盘'}</div>
        </div>
      </div>

      <div>
        <div className="text-xs font-medium text-gray-500">阶段进度</div>
        <div className="mt-2 flex flex-wrap gap-2">
          {metrics.flow.map((stage) => {
            const group = GROUPS.find((itemGroup) => itemGroup.id === stage.groupId)
            const className = getLiveTraceStageClasses(stage, group)
            const style = stage.status === 'done' && group
              ? { background: group.bg, color: group.color, borderColor: group.border }
              : undefined
            return (
              <div key={`preview-stream-${session.request_id}-${stage.groupId}`} className="inline-flex items-center gap-1.5">
                <span
                  className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium ${className}`}
                  style={style}
                >
                  {stage.label}
                  {stage.count > 0 ? `${stage.count}/${stage.total}` : '0'}
                </span>
                {renderImmuneStageTags(stage, `preview-stream-${session.request_id}-${stage.groupId}`)}
              </div>
            )
          })}
        </div>
      </div>

      <KeyNodeSequence nodes={metrics.keyNodeSequence} />

      <div className="grid grid-cols-1 gap-3">
        <div>
          <div className="text-xs font-medium text-gray-500">输入</div>
          <div className="mt-1 rounded-lg border border-primary-100/70 bg-white/70 p-3 text-sm text-gray-700 break-all">
            {displayTitle}
          </div>
        </div>
        <div>
          <div className="text-xs font-medium text-gray-500">输出</div>
          <div className="mt-1 rounded-lg border border-primary-100/70 bg-white/70 p-3 text-sm text-gray-700 break-all">
            {metrics.outputPreview || '暂无输出，可能仍在执行或这次没有文本结果。'}
          </div>
        </div>
      </div>

      <div>
        <div className="text-xs font-medium text-gray-500">最新节点变化</div>
        <div className="mt-2 space-y-2">
          {recentTimelineEntries.length === 0 ? (
            <EmptyStateCard title="暂无节点变化" hint="等待节点事件进入时间线。" className="p-4" />
          ) : recentTimelineEntries.map((entry) => (
            <div key={entry.entryId} className="rounded-lg border border-primary-100/70 bg-white/70 px-3 py-2">
              <div className="flex items-center gap-2">
                <div className="text-sm font-medium text-gray-800">{entry.displayName || entry.nodeId}</div>
                <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${getLiveTraceChangeBadgeClass(entry.changeType)}`}>
                  {entry.label}
                </span>
                <StatusBadge status={normalizeBadgeStatus(entry.status)} />
                <div className="ml-auto text-xs text-gray-400">{formatRecentTraceTime(entry.tsMs)}</div>
              </div>
              <div className="mt-1 text-xs text-gray-500 break-all">{entry.nodeId}</div>
              {entry.previousStatus && entry.previousStatus !== entry.status ? (
                <div className="mt-1 text-xs text-gray-500">
                  {entry.previousStatus} -&gt; {entry.status}
                </div>
              ) : null}
              {entry.preview ? (
                <div className="mt-1 text-xs text-gray-600 break-all">{entry.preview}</div>
              ) : null}
            </div>
          ))}
        </div>
      </div>

      <div className="flex items-center gap-2">
        <button className="btn-secondary text-xs" onClick={onHighlight}>显示总览图</button>
        <button className="btn-primary text-xs" onClick={onOpenDetail}>打开详情</button>
      </div>
    </div>
  )
}

function getRecentRequestStatusMeta(status) {
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

function formatRecentTraceTime(tsMs) {
  if (!tsMs) return '-'
  return new Date(tsMs).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
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

function isTribeEventLinkedToSelection(event, { requestId, sessionId, conversationId }) {
  const payload = event?.payload ?? {}
  const payloadRequestId = normalizeOptionalKeyValue(payload, ['request_id', 'requestId'])
  const payloadSessionId = normalizeOptionalKeyValue(payload, ['session_id', 'sessionId'])
  const payloadConversationId = normalizeOptionalKeyValue(payload, ['conversation_id', 'conversationId'])

  if (requestId && payloadRequestId === requestId) return true
  if (sessionId && payloadSessionId === sessionId) return true
  if (conversationId && payloadConversationId === conversationId) return true
  return false
}

// ===== 主导出：Trace 路由容器 =====

export {
  LiveTraceCard,
  LiveTracePreview,
  LiveTraceStreamPreview,
  formatEventPayloadPreview,
  formatRecentTraceTime,
  getLiveTraceCurrentLabel,
  getLiveTraceOutputPreview,
  getLiveTraceStageClasses,
  getRecentRequestStatusMeta,
  getSessionDisplayTitle,
  getTraceActiveStageMeta,
  getTraceIssueCount,
  getTraceRunningCount,
  isTribeEventLinkedToSelection,
  mergeLiveTraceTimelineEntries,
  renderImmuneStageTags,
}
