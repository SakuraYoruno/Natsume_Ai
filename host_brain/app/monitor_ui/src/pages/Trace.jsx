import React, { useEffect, useState, useCallback, useRef } from 'react'
import { useNavigate, Routes, Route, NavLink, useLocation } from 'react-router-dom'
import {
  Search, RefreshCw, GitBranch,
  Activity, Bookmark, Users, ChevronRight, Layers, Volume2,
  Video, Database, CheckCircle, Clock3, Shield,
} from 'lucide-react'
import DigitalLifeRuntimeCard from '../components/DigitalLifeRuntimeCard.jsx'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import LivingPulseStrip from '../components/LivingPulseStrip.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import PageHero from '../components/PageHero.jsx'
import StatusBadge from '../components/StatusBadge.jsx'
import { getTraceNodeDisplayName } from '../components/trace/traceNodeText.js'
import {
  GROUPS,
  FULL_CHAIN_SCHEMA,
  buildImmuneStageMeta,
  getLatestPrimaryTraceNode,
  getLatestTraceNode,
  getLiveTraceFlow,
  getTraceGroupNodeIds,
  getTraceGroupState,
  normalizeBadgeStatus,
  normalizeTraceGroupNodeId,
} from '../components/trace/traceFlowModel.js'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import { formatAlertTitle, humanizeToken } from '../utils/digitalLife.js'
import { useSessionsListQuery } from '../queries/sessionsQueries.js'
import { useTraceLiveSnapshotQuery, useTraceSnapshotQuery, useTraceTribeFeedQuery } from '../queries/traceQueries.js'
import { sanitizeRecentRequests } from '../utils/sessionList.js'
import {
  connectTraceLiveFeed,
} from '../api/obs.js'
import useAppStore from '../store/useAppStore.js'

const TraceRequestDetail = React.lazy(() => import('./TraceRequestDetail.jsx'))
const TraceFullChainView = React.lazy(() => import('../components/trace/TraceAdvancedViews.jsx').then((module) => ({ default: module.FullChainView })))
const TraceGroupDagRoute = React.lazy(() => import('../components/trace/TraceAdvancedViews.jsx').then((module) => ({ default: module.GroupDagRoute })))
const TraceInlineFullChainOverview = React.lazy(() => import('../components/trace/TraceAdvancedViews.jsx').then((module) => ({ default: module.InlineFullChainOverview })))

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
  'chat_agent',
  'prompt_builder',
  'llm_stream',
  'airlock_egress',
]
const LIVE_TRACE_KEY_NODE_INDEX = Object.fromEntries(LIVE_TRACE_KEY_NODE_IDS.map((nodeId, index) => [nodeId, index]))

function getLiveTraceKeyNodeSortKey(node) {
  const normalizedId = normalizeTraceGroupNodeId(node?.node_id, node?.node_type)
  if (LIVE_TRACE_KEY_NODE_INDEX[normalizedId] != null) return LIVE_TRACE_KEY_NODE_INDEX[normalizedId]
  if (String(node?.node_id || '').startsWith('sub_agent:')) {
    return (LIVE_TRACE_KEY_NODE_INDEX.chat_agent ?? 999) - 0.5
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

function HomeSummaryCard({ icon: Icon, title, value, caption, toneClassName = 'text-gray-900' }) {
  return (
    <div className="card p-4">
      <div className="flex items-start gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-primary-100/70 bg-white text-blue-600">
          <Icon size={18} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-xs font-medium text-gray-500">{title}</div>
          <div className={`mt-1 break-all text-sm font-semibold leading-6 ${toneClassName}`}>{value}</div>
          {caption ? <div className="mt-1 break-all text-xs leading-5 text-gray-500">{caption}</div> : null}
        </div>
      </div>
    </div>
  )
}

function TraceSection({ title, hint, aside = null, children }) {
  return (
    <section className="card relative overflow-hidden p-4 md:p-5">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_left,_rgba(41,112,255,0.05),_transparent_38%),radial-gradient(circle_at_bottom_right,_rgba(16,185,129,0.05),_transparent_34%)]" />
      <div className="relative">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="lifeline-node" />
          <h2 className="text-sm font-semibold text-gray-900">{title}</h2>
          {aside ? <div className="ml-auto">{aside}</div> : null}
        </div>
        {hint ? <p className="mt-1 text-xs text-gray-500">{hint}</p> : null}
        <div className="lifeline-rail mt-3 h-4 px-2">
          <div className="lifeline-wave absolute inset-0 opacity-65" />
          <div className="relative flex h-full items-center justify-around">
            {[0, 1, 2, 3].map((index) => (
              <span key={index} className="lifeline-node" style={{ animationDelay: `${index * 0.24}s` }} />
            ))}
          </div>
        </div>
        <div className="mt-4">{children}</div>
      </div>
    </section>
  )
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

function TraceHome({ traceData, setTraceData, traceInfo, setTraceInfo, loading, setLoading, error, setError }) {
  const [inputId, setInputId] = useState('')
  const [recentKeyword, setRecentKeyword] = useState('')
  const [recentStatusFilter, setRecentStatusFilter] = useState('')
  const [historyExpanded, setHistoryExpanded] = useState(false)
  const [inlineOverviewVisible, setInlineOverviewVisible] = useState(false)
  const [advancedViewsExpanded, setAdvancedViewsExpanded] = useState(false)
  const [liveTraceItems, setLiveTraceItems] = useState([])
  const [liveTraceTimelineEntries, setLiveTraceTimelineEntries] = useState({})
  const [liveFeedError, setLiveFeedError] = useState(null)
  const [liveFeedConnected, setLiveFeedConnected] = useState(false)
  const [liveFeedLastTs, setLiveFeedLastTs] = useState(null)
  const inlineOverviewRef = useRef(null)
  const navigate = useNavigate()
  const pollingEnabled = useAppStore((s) => s.pollingEnabled)
  const pollingInterval = useAppStore((s) => s.pollingInterval)
  const pollingTick = useAppStore((s) => s.pollingTick)
  const pageVisible = useAppStore((s) => s.pageVisible)
  const selectedRequestId = useAppStore((s) => s.selectedRequestId)
  const setSelectedRequestId = useAppStore((s) => s.setSelectedRequestId)
  const liveFeedEnabled = pollingEnabled && pageVisible
  const liveTraceLimit = recentKeyword ? 12 : 6
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
  const liveSnapshotParams = React.useMemo(() => ({
    limit: liveTraceLimit,
    window_minutes: 1440,
    active_within_ms: 60000,
    keyword: recentKeyword || undefined,
  }), [liveTraceLimit, recentKeyword])
  const liveSnapshotQuery = useTraceLiveSnapshotQuery({
    queryParams: liveSnapshotParams,
    enabled: true,
    pollingEnabled: liveFeedEnabled,
    pollingTick,
    pollingInterval: Math.max(10, pollingInterval),
  })
  const recentRequestsBase = React.useMemo(() => (
    sanitizeRecentRequests(recentRequestsQuery.sessionsData?.items, { keyword: recentKeyword })
  ), [recentKeyword, recentRequestsQuery.sessionsData?.items])
  const recentRequests = React.useMemo(() => {
    if (recentKeyword) return recentRequestsBase
    const previousById = new Map(recentRequestsBase.map((item) => [item.request_id, item]))
    const merged = liveTraceItems
      .map((item) => item?.session)
      .filter(Boolean)
      .map((session) => ({
        ...(previousById.get(session.request_id) ?? {}),
        ...session,
      }))
    recentRequestsBase.forEach((session) => {
      if (!merged.some((item) => item.request_id === session.request_id)) {
        merged.push(session)
      }
    })
    return merged.slice(0, 12)
  }, [liveTraceItems, recentKeyword, recentRequestsBase])
  const recentLoading = recentRequestsQuery.loading || recentRequestsQuery.refreshing
  const liveTraceLoading = liveSnapshotQuery.loading || liveSnapshotQuery.refreshing
  const liveTraceError = liveFeedError || liveSnapshotQuery.liveSnapshotError
  const selectedSessionForQuery = React.useMemo(() => (
    liveTraceItems.find((item) => item?.session?.request_id === selectedRequestId)?.session
    ?? recentRequests.find((item) => item?.request_id === selectedRequestId)
    ?? null
  ), [liveTraceItems, recentRequests, selectedRequestId])
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
  const tribeFeedParams = React.useMemo(() => {
    const params = { limit: selectedRequestId ? 200 : 60 }
    if (selectedRequestId) {
      params.request_id = selectedRequestId
    } else if (selectedSessionIdForQuery) {
      params.session_id = selectedSessionIdForQuery
    } else if (selectedConversationIdForQuery) {
      params.conversation_id = selectedConversationIdForQuery
    }
    return params
  }, [selectedConversationIdForQuery, selectedRequestId, selectedSessionIdForQuery])
  const tribeFeedQuery = useTraceTribeFeedQuery({
    queryParams: tribeFeedParams,
    enabled: Boolean(selectedRequestId || selectedSessionIdForQuery || selectedConversationIdForQuery),
    pollingEnabled: liveFeedEnabled,
    pollingTick,
    pollingInterval: Math.max(10, pollingInterval),
  })
  const tribeEvents = Array.isArray(tribeFeedQuery.tribeFeedPayload?.items) ? tribeFeedQuery.tribeFeedPayload.items : []
  const tribeEventsLoading = tribeFeedQuery.loading || tribeFeedQuery.refreshing
  const tribeEventsError = tribeFeedQuery.tribeFeedError
  const selectedTraceQuery = useTraceSnapshotQuery({
    requestId: selectedRequestId,
    enabled: Boolean(selectedRequestId),
    pollingEnabled: liveFeedEnabled && !liveFeedConnected,
    pollingTick,
    pollingInterval,
  })

  const load = useCallback(async (id, options = {}) => {
    if (!id?.trim()) return
    const showOverview = options.showOverview ?? true
    setError(null)
    setSelectedRequestId(id)
    if (showOverview) {
      setInlineOverviewVisible(true)
    }
  }, [setLoading, setError, setSelectedRequestId, setTraceData, setTraceInfo])

  useEffect(() => {
    if (!liveFeedEnabled) {
      setLiveFeedConnected(false)
      return undefined
    }

    let closed = false
    let connection
    setLiveFeedError(null)

    try {
      connection = connectTraceLiveFeed(
        {
          limit: liveTraceLimit,
          window_minutes: 1440,
          active_within_ms: 60000,
          interval_ms: 1000,
          keyword: recentKeyword || undefined,
        },
        {
          onOpen: () => {
            if (closed) return
            setLiveFeedConnected(true)
            setLiveFeedError(null)
          },
          onSnapshot: (payload) => {
            if (closed) return
            const items = Array.isArray(payload?.items) ? payload.items : []
            setLiveFeedConnected(true)
            setLiveFeedLastTs(payload?.timestamp ?? Date.now())
            // SSE 可能返回空快照（例如当前无“活跃”窗口），此时保留已有列表，
            // 后续再由 fallback 拉取 recentRequests 对应链路，避免首页长时间空载。
            if (items.length > 0) {
              setLiveTraceItems(items)
            }
            setLiveTraceTimelineEntries((previous) => {
              if (items.length === 0) return previous
              const activeRequestIds = new Set(items.map((item) => item?.session?.request_id).filter(Boolean))
              if (activeRequestIds.size === 0) return {}
              const next = {}
              for (const [requestId, entries] of Object.entries(previous)) {
                if (activeRequestIds.has(requestId)) {
                  next[requestId] = entries
                }
              }
              return next
            })
            setLiveFeedError(null)
          },
          onDelta: (payload) => {
            if (closed) return
            const items = Array.isArray(payload?.items) ? payload.items : []
            if (items.length === 0) return
            setLiveFeedConnected(true)
            setLiveFeedLastTs(payload?.timestamp ?? Date.now())
            setLiveTraceTimelineEntries((previous) => mergeLiveTraceTimelineEntries(previous, items))
          },
          onHeartbeat: (payload) => {
            if (closed) return
            setLiveFeedConnected(true)
            setLiveFeedLastTs(payload?.timestamp ?? Date.now())
          },
          onError: (payload) => {
            if (closed) return
            setLiveFeedError(payload?.detail || '实时链路流异常')
          },
          onConnectionError: () => {
            if (closed) return
            setLiveFeedConnected(false)
          },
        }
      )
    } catch (connectionError) {
      setLiveFeedConnected(false)
      setLiveFeedError(connectionError.message)
      return undefined
    }

    return () => {
      closed = true
      connection?.close?.()
    }
  }, [liveFeedEnabled, recentKeyword, liveTraceLimit])

  useEffect(() => {
    const items = Array.isArray(liveSnapshotQuery.liveSnapshot?.items) ? liveSnapshotQuery.liveSnapshot.items : []
    if (items.length === 0) {
      if (!liveFeedConnected) {
        setLiveTraceItems([])
      }
      return
    }
    if (liveFeedConnected && liveTraceItems.length > 0) return
    setLiveTraceItems(items)
    if (!liveFeedLastTs && liveSnapshotQuery.liveSnapshot?.timestamp) {
      setLiveFeedLastTs(liveSnapshotQuery.liveSnapshot.timestamp)
    }
  }, [liveFeedConnected, liveFeedLastTs, liveSnapshotQuery.liveSnapshot, liveTraceItems.length])

  useEffect(() => {
    setLiveTraceItems([])
    setLiveTraceTimelineEntries({})
  }, [recentKeyword])

  useEffect(() => {
    setLoading(selectedTraceQuery.loading)
    setError(selectedTraceQuery.traceError)
  }, [selectedTraceQuery.loading, selectedTraceQuery.traceError, setError, setLoading])

  useEffect(() => {
    if (!selectedRequestId) {
      setLoading(false)
      return
    }
    if (!selectedTraceQuery.traceSnapshot) return
    setTraceData(selectedTraceQuery.traceSnapshot)
    setTraceInfo({
      status: selectedTraceQuery.traceSnapshot.status,
      latency: selectedTraceQuery.traceSnapshot.total_latency_ms,
      nodeCount: (selectedTraceQuery.traceSnapshot.nodes ?? []).length,
    })
  }, [selectedRequestId, selectedTraceQuery.traceSnapshot, setLoading, setTraceData, setTraceInfo])

  const revealInlineOverview = useCallback(() => {
    setInlineOverviewVisible(true)
    requestAnimationFrame(() => {
      inlineOverviewRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
  }, [])

  const applyTraceSelection = useCallback((item, options = {}) => {
    if (!item?.session?.request_id || !item.trace) return false
    setSelectedRequestId(item.session.request_id)
    setTraceData(item.trace)
    setTraceInfo({
      status: item.trace.status,
      latency: item.trace.total_latency_ms,
      nodeCount: (item.trace.nodes ?? []).length,
    })
    if (options.showOverview ?? true) {
      revealInlineOverview()
    }
    return true
  }, [setSelectedRequestId, setTraceData, setTraceInfo, revealInlineOverview])

  useEffect(() => {
    if (!liveTraceItems.length) return
    const matchedItem = liveTraceItems.find((item) => item.session.request_id === selectedRequestId)
    const nextItem = matchedItem ?? (!selectedRequestId ? liveTraceItems[0] : null)
    if (!nextItem?.trace) return

    if (traceData?.request_id === nextItem.trace.request_id && traceData?.timestamp === nextItem.trace.timestamp) {
      return
    }

    applyTraceSelection(nextItem, { showOverview: false })
  }, [applyTraceSelection, liveTraceItems, selectedRequestId, traceData])

  useEffect(() => {
    if (!inlineOverviewVisible || !traceData?.request_id) return undefined
    const frame = requestAnimationFrame(() => {
      inlineOverviewRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
    return () => cancelAnimationFrame(frame)
  }, [inlineOverviewVisible, traceData?.request_id])

  // 计算每个分组的激活节点数
  const groupStats = GROUPS.map(g => {
    const traceGroupState = traceData ? getTraceGroupState(g, traceData) : null
    const activeCount = traceGroupState ? traceGroupState.activeCount : 0
    const totalNodeCount = traceData ? getTraceGroupNodeIds(g).length : g.nodeIds.length
    const hasError = traceGroupState
      ? traceGroupState.groupNodes.some(n => ['FAIL', 'ERROR', 'DOWN'].includes((n.status ?? '').toUpperCase()))
      : false
    const immuneMeta = g.id === 'immune'
      ? buildImmuneStageMeta(traceGroupState?.groupNodes ?? [])
      : null
    return { ...g, activeCount, totalNodeCount, hasError, immuneMeta }
  })

  const openRequestTrace = (requestId) => {
    setSelectedRequestId(requestId)
    navigate(`/trace/${requestId}`)
  }

  const latestRequest = recentRequests[0] ?? null
  const searchValue = inputId.trim()
  const searchLooksLikeRequestId = searchValue.startsWith('req_')

  const handleTraceSearch = () => {
    const value = searchValue
    if (!value) {
      setHistoryExpanded(false)
      setRecentKeyword('')
      return
    }
    if (searchLooksLikeRequestId) {
      load(value)
      return
    }
    setHistoryExpanded(false)
    setRecentKeyword(value)
  }

  const currentTraceStatus = String(traceInfo?.status || '').toUpperCase()
  const currentTraceHealthy = ['OK', 'UP', 'SUCCESS'].includes(currentTraceStatus)
  const liveFeedStatusClass = !pollingEnabled
    ? 'border-primary-200 bg-primary-50/45 text-primary-700'
    : !pageVisible
      ? 'border-slate-200 bg-slate-50 text-slate-600'
      : liveFeedConnected
        ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
        : 'border-amber-200 bg-amber-50 text-amber-700'
  const liveFeedStatusText = !pollingEnabled
    ? '实时流已暂停'
    : !pageVisible
      ? '页面隐藏，实时流已停'
      : liveFeedConnected
        ? 'SSE 实时流已连接'
        : '实时流重连中，当前降级轮询'
  const selectedLiveTraceItem = liveTraceItems.find((item) => item.session.request_id === selectedRequestId)
    ?? liveTraceItems[0]
    ?? null
  const selectedSessionFallback = selectedLiveTraceItem?.session
    ?? recentRequests.find((item) => item.request_id === selectedRequestId)
    ?? (selectedRequestId ? { request_id: selectedRequestId } : null)
  const selectedTraceTitleSource = selectedLiveTraceItem?.trace
    ?? (traceData?.request_id === selectedRequestId ? traceData : null)
  const selectedLiveTraceTitle = selectedRequestId
    ? getSessionDisplayTitle(selectedSessionFallback, selectedTraceTitleSource)
    : null
  const selectedLiveTraceTimelineEntries = selectedLiveTraceItem?.session?.request_id
    ? (liveTraceTimelineEntries[selectedLiveTraceItem.session.request_id] ?? [])
    : []
  const selectedSessionId = String(
    selectedSessionFallback?.session_id
    ?? selectedTraceTitleSource?.summary?.session_id
    ?? ''
  ).trim()
  const selectedConversationId = String(
    selectedSessionFallback?.conversation_id
    ?? selectedTraceTitleSource?.summary?.conversation_id
    ?? ''
  ).trim()
  const linkedTribeEvents = selectedRequestId
    ? tribeEvents.filter((event) => isTribeEventLinkedToSelection(event, {
      requestId: selectedRequestId,
      sessionId: selectedSessionId,
      conversationId: selectedConversationId,
    }))
    : []
  const linkedTribeEventList = linkedTribeEvents.slice(0, 20)
  const isHistorySearchMode = Boolean(recentKeyword.trim())
  const shouldAutoShowHistory = !isHistorySearchMode && (!liveFeedConnected || liveTraceItems.length === 0 || Boolean(liveTraceError))
  const showHistoryPanel = !isHistorySearchMode && (historyExpanded || shouldAutoShowHistory)
  const activeFeedTitle = isHistorySearchMode ? '历史请求结果' : '实时活跃链路流'
  const activeFeedDescription = isHistorySearchMode
    ? `按 "${recentKeyword}" 匹配到的请求会直接在这里展示，方便先筛选再点进单条详情。`
    : '首页只保留当前活跃链路，先看现在在跑什么，再点进单条请求追踪。'
  const activeListTitle = isHistorySearchMode ? '历史结果列表' : '活跃请求流'
  const activeListDescription = isHistorySearchMode
    ? '搜索命中的请求结果会替换活跃流列表，避免同层再放一份最近请求。'
    : '优先展示最近活跃的链路进度、当前节点和输出摘要。'
  const activeCountLabel = isHistorySearchMode ? '条搜索结果' : '条活跃请求'
  const historySectionHint = shouldAutoShowHistory
    ? '当前没有活跃链路，自动回退展示历史请求（已默认隐藏测试会话与纯问号占位消息）。'
    : '历史请求默认收起；需要回看刚才跑过什么时，再从这里展开。'
  const canCollapseHistory = historyExpanded && !shouldAutoShowHistory
  const selectedTraceStageMeta = getTraceActiveStageMeta(traceData)
  const selectedIssueCount = getTraceIssueCount(traceData)
  const selectedRunningCount = getTraceRunningCount(traceData)
  const selectedCurrentNodeLabel = traceData ? getLiveTraceCurrentLabel(traceData) : '等待选中请求'
  const selectedOutputPreview = traceData ? getLiveTraceOutputPreview(traceData) : ''
  const liveModeLabel = !pollingEnabled
    ? '实时暂停'
    : !pageVisible
      ? '页面隐藏'
      : liveFeedConnected
        ? 'SSE 实时流'
        : '轮询兜底'
  const liveModeCaption = liveFeedLastTs
    ? `最近事件 ${formatRecentTraceTime(liveFeedLastTs)}`
    : (liveTraceItems.length > 0 ? `${liveTraceItems.length} 条活跃请求` : '等待活跃请求')
  const tracePulseNodes = [
    {
      label: '当前请求',
      value: selectedLiveTraceTitle || '等待选中',
      tone: 'primary',
      active: Boolean(selectedLiveTraceTitle),
    },
    {
      label: '当前节点',
      value: selectedCurrentNodeLabel,
      tone: 'teal',
      active: Boolean(selectedCurrentNodeLabel && selectedCurrentNodeLabel !== '等待选中请求'),
    },
    {
      label: '阶段覆盖',
      value: `${selectedTraceStageMeta.activeStageCount}/${GROUPS.length}`,
      tone: 'violet',
      active: selectedTraceStageMeta.activeStageCount > 0,
    },
    {
      label: '实时模式',
      value: liveModeLabel,
      tone: 'amber',
      active: liveFeedEnabled,
    },
    {
      label: '联动事件',
      value: `${linkedTribeEvents.length} 条`,
      tone: 'rose',
      active: linkedTribeEvents.length > 0,
    },
  ]

  const focusRequestOverview = useCallback((requestId) => {
    if (!requestId) return
    if (traceData?.request_id === requestId) {
      setSelectedRequestId(requestId)
      revealInlineOverview()
      return
    }
    const matchedItem = liveTraceItems.find((item) => item.session.request_id === requestId)
    if (matchedItem?.trace) {
      applyTraceSelection(matchedItem, { showOverview: true })
      return
    }
    load(requestId, { showOverview: true })
  }, [applyTraceSelection, liveTraceItems, load, revealInlineOverview, setSelectedRequestId, traceData])

  const focusLiveTraceItem = useCallback((item, options = {}) => {
    if (!item?.session?.request_id) return
    if (!item.trace) {
      load(item.session.request_id, { showOverview: options.showOverview ?? true })
      return
    }
    applyTraceSelection(item, options)
  }, [applyTraceSelection, load])

  return (
    <div className="space-y-5">
      <PageHero
        icon={Activity}
        eyebrow="神经传导 Trace"
        title="数字生命体神经链路可视化"
        description="从感官输入、检索规划、人格上下文到响应输出，统一查看当前请求的完整神经传导路径。"
        gradientClass="from-blue-50 via-white to-violet-50"
        metaItems={[
          `活跃请求：${liveTraceItems.length}`,
          `当前节点：${traceInfo?.nodeCount ?? 0}`,
          selectedRequestId ? '已选中请求' : '未选中请求',
          currentTraceHealthy ? '当前链路：健康' : '当前链路：待关注',
        ]}
      />

      <LivingPulseStrip
        title="链路脉冲"
        subtitle="先看这一条神经流现在怎么流动，再下钻到单条请求或节点细节。"
        nodes={tracePulseNodes}
      />

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-5">
        <HomeSummaryCard
          icon={Bookmark}
          title="当前请求"
          value={selectedLiveTraceTitle || '等待选中请求'}
          caption={selectedRequestId || '从下方活跃请求流选择一条 request'}
        />
        <HomeSummaryCard
          icon={Layers}
          title="阶段覆盖"
          value={`${selectedTraceStageMeta.activeStageCount}/${GROUPS.length}`}
          caption={`当前阶段：${selectedTraceStageMeta.currentStageLabel}`}
        />
        <HomeSummaryCard
          icon={Shield}
          title="待关注节点"
          value={selectedIssueCount > 0 ? `${selectedIssueCount} 个异常/降级` : '无异常节点'}
          caption={selectedRunningCount > 0 ? `运行中 ${selectedRunningCount} 个节点` : (traceInfo?.latency != null ? `总耗时 ${traceInfo.latency}ms` : '等待节点继续推进')}
          toneClassName={selectedIssueCount > 0 ? 'text-orange-600' : 'text-emerald-600'}
        />
        <HomeSummaryCard
          icon={Activity}
          title="当前节点"
          value={selectedCurrentNodeLabel}
          caption={selectedOutputPreview || '暂无输出摘要，可能仍在执行或尚未落盘。'}
        />
        <HomeSummaryCard
          icon={Clock3}
          title="实时模式"
          value={liveModeLabel}
          caption={liveModeCaption}
          toneClassName={!pollingEnabled ? 'text-gray-700' : liveFeedConnected ? 'text-emerald-600' : 'text-amber-600'}
        />
      </div>

      <TraceSection
        title="链路控制台"
        hint="筛选请求、查看当前会话和 Tribe 联动事件。"
      >
      <div className="flex items-center gap-3 flex-wrap">
        <h1 className="text-xl font-semibold text-gray-900 shrink-0">链路可视化</h1>
        <div className="flex items-center gap-2 flex-1 min-w-64">
          <div className="relative flex-1">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              className="w-full rounded-lg border border-primary-100/70 bg-white/92 py-1.5 pl-8 pr-3 font-mono text-sm focus:border-blue-300 focus:outline-none focus:ring-2 focus:ring-primary-200"
              placeholder="输入消息内容、session_id 或 request_id"
              value={inputId}
              onChange={(e) => setInputId(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleTraceSearch()}
            />
          </div>
          <button className="btn-primary shrink-0" onClick={handleTraceSearch} disabled={loading || recentLoading}>
            {(loading || recentLoading) ? <RefreshCw size={13} className="animate-spin" /> : (searchLooksLikeRequestId ? '显示总览图' : '筛选请求')}
          </button>
          <select
            className="shrink-0 rounded-lg border border-primary-100/70 bg-white/92 px-2 py-1.5 text-sm focus:border-blue-300 focus:outline-none focus:ring-2 focus:ring-primary-200"
            value={recentStatusFilter}
            onChange={(e) => { setRecentStatusFilter(e.target.value) }}
          >
            <option value="">全部状态</option>
            <option value="ok">成功</option>
            <option value="rejected">拒绝</option>
            <option value="error">错误</option>
          </select>
          {isHistorySearchMode && (
            <button
              className="btn-secondary shrink-0 text-xs"
              onClick={() => {
                setInputId('')
                setHistoryExpanded(false)
                setRecentKeyword('')
              }}
              disabled={recentLoading}
            >
              清除筛选
            </button>
          )}
          {traceData && (
            <button className="btn-secondary shrink-0 text-xs flex items-center gap-1"
              onClick={() => { setTraceData(null); setTraceInfo(null); setInlineOverviewVisible(false) }}>
              <RefreshCw size={12} /> 重置
            </button>
          )}
        </div>
        {/* 链路摘要 */}
        {traceInfo && (
          <div className="flex items-center gap-3 text-xs text-gray-500">
            <span>激活节点 {traceInfo.nodeCount}</span>
            {traceInfo.latency != null && <span>总耗时 {traceInfo.latency}ms</span>}
            <span className={currentTraceHealthy ? 'text-green-600 font-medium' : 'text-red-500 font-medium'}>
              {traceInfo.status}
            </span>
          </div>
        )}
        {error && (
          <NoticeBanner variant="error" title="链路查询异常" className="w-full md:w-auto">
            {error}
          </NoticeBanner>
        )}
      </div>

      {runtimeSnapshot && (
        <DigitalLifeRuntimeCard
          runtimeSnapshot={runtimeSnapshot}
          promptStats={promptBuilderData}
          compact
        />
      )}

      {selectedRequestId && (
        <div className="card flex items-center gap-3 flex-wrap px-4 py-3">
          <div className="min-w-0">
            <div className="text-xs font-medium text-blue-700">当前会话</div>
            <div className="text-sm font-semibold text-blue-900 break-all">{selectedLiveTraceTitle}</div>
            <div className="mt-1 font-mono text-xs text-blue-800 break-all">{selectedRequestId}</div>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <button className="btn-secondary text-xs" onClick={() => focusRequestOverview(selectedRequestId)} disabled={loading}>
              显示总览图
            </button>
            <button className="btn-primary text-xs" onClick={() => openRequestTrace(selectedRequestId)}>
              打开详情
            </button>
          </div>
          <div className="lifeline-rail h-1.5 w-full">
            <div className="lifeline-wave h-full w-full" />
          </div>
        </div>
      )}

      {selectedRequestId && (
        <div className="card rounded-2xl p-4 md:p-5">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div className="min-w-0">
              <h2 className="text-sm font-semibold text-emerald-900">Tribe 事件联动</h2>
              <p className="text-xs text-emerald-700/80 mt-1">
                当前请求关联：{linkedTribeEvents.length} 条（request/session/conversation 维度匹配）
              </p>
            </div>
            <div className="flex items-center gap-2">
              <button
                className="btn-secondary text-xs"
                onClick={() => { void tribeFeedQuery.refetch() }}
                disabled={tribeEventsLoading}
              >
                <RefreshCw size={12} className={tribeEventsLoading ? 'animate-spin' : ''} />
                刷新事件
              </button>
              <button
                className="btn-secondary text-xs"
                onClick={() => navigate('/insights/safety')}
              >
                打开 Insights
              </button>
            </div>
          </div>
          <div className="lifeline-rail mt-3 h-1.5">
            <div className="lifeline-wave h-full w-full" />
          </div>

          <div className="mt-3 flex items-center gap-3 text-xs text-emerald-800/90 flex-wrap">
            <span>request_id: {selectedRequestId}</span>
            {selectedSessionId ? <span>session_id: {selectedSessionId}</span> : null}
            {selectedConversationId ? <span>conversation_id: {selectedConversationId}</span> : null}
          </div>

          {tribeEventsError && (
            <NoticeBanner variant="error" title="Tribe 事件读取失败" className="mt-3">
              {tribeEventsError}
            </NoticeBanner>
          )}

          {!tribeEventsLoading && !tribeEventsError && linkedTribeEventList.length === 0 && (
            <EmptyStateCard
              title="当前请求暂无 Tribe 事件"
              hint="若请求刚开始执行，可稍后刷新；也可到 Insights 查看全局事件窗口。"
              className="mt-3 p-4 bg-white"
            />
          )}

          {linkedTribeEventList.length > 0 && (
            <div className="mt-3 space-y-3">
              {linkedTribeEventList.map((event) => {
                const payloadPreview = formatEventPayloadPreview(event?.payload, 220)
                return (
                  <div key={event.event_id} className="rounded-2xl border border-primary-100/70 bg-white/80 p-4 shadow-sm">
                    <div className="flex items-start gap-3">
                      <div className="mt-1 h-2.5 w-2.5 shrink-0 rounded-full bg-emerald-400 ring-4 ring-emerald-50" />
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <div className="text-sm font-semibold text-emerald-900">
                            {event.event_type}
                          </div>
                          <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700">
                            {formatRecentTraceTime(event.ts_ms)}
                          </span>
                        </div>
                        <div className="mt-2 flex flex-wrap gap-2 text-xs">
                          <span className="rounded-full border border-blue-100 bg-blue-50 px-2 py-1 font-medium text-blue-700">
                            source {event.source_agent || '—'}
                          </span>
                          <span className="rounded-full border border-purple-100 bg-purple-50 px-2 py-1 font-medium text-purple-700">
                            target {event.target_agent || '—'}
                          </span>
                          {event.request_id ? (
                            <span className="rounded-full border border-primary-200 bg-primary-50/55 px-2 py-1 font-mono text-primary-700">
                              {String(event.request_id).slice(0, 18)}
                            </span>
                          ) : null}
                        </div>
                        <div className="mt-3 break-all rounded-xl border border-primary-100/70 bg-white/86 px-3 py-2 text-xs leading-5 text-gray-600">
                          {payloadPreview}
                        </div>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}
      </TraceSection>

      <div className="card relative overflow-hidden p-4 md:p-5">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_left,_rgba(41,112,255,0.05),_transparent_38%),radial-gradient(circle_at_bottom_right,_rgba(16,185,129,0.05),_transparent_34%)]" />
        <div className="relative">
        <div className="flex items-center gap-3 flex-wrap">
          <div className="flex items-center gap-2 min-w-0">
            <span className="flex items-center justify-center w-9 h-9 rounded-xl bg-blue-600 text-white shadow-sm">
              <Activity size={18} />
            </span>
            <div>
              <h2 className="text-base font-semibold text-gray-900">{activeFeedTitle}</h2>
              <p className="text-xs text-gray-500">
                {activeFeedDescription}
              </p>
            </div>
          </div>
          <div className="ml-auto flex items-center gap-2 text-xs text-gray-500">
            <span className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 ${liveFeedStatusClass}`}>
              <RefreshCw size={12} className={liveFeedConnected ? 'animate-spin' : ''} />
              {liveFeedStatusText}
            </span>
            {liveFeedLastTs ? <span>最近事件 {formatRecentTraceTime(liveFeedLastTs)}</span> : null}
            <span>{liveTraceItems.length} {activeCountLabel}</span>
          </div>
        </div>

        <div className="lifeline-rail mt-3 h-4 px-2">
          <div className="lifeline-wave absolute inset-0 opacity-65" />
          <div className="relative flex h-full items-center justify-around">
            {[0, 1, 2, 3].map((index) => (
              <span key={`active-feed-${index}`} className="lifeline-node" style={{ animationDelay: `${index * 0.24}s` }} />
            ))}
          </div>
        </div>

        <div className="mt-4 grid grid-cols-1 xl:grid-cols-[minmax(0,1.35fr)_24rem] gap-4">
          <div className="rounded-2xl border border-primary-100/70 bg-white/88 p-3 shadow-sm shadow-blue-100/40">
            <div className="flex items-center justify-between gap-3 border-b border-primary-100/70 px-1 pb-3">
              <div>
                <div className="text-sm font-semibold text-gray-900">{activeListTitle}</div>
                <div className="text-xs text-gray-500">{activeListDescription}</div>
              </div>
              <button
                className="btn-secondary text-xs"
                onClick={() => {
                  void Promise.allSettled([recentRequestsQuery.refetch(), liveSnapshotQuery.refetch()])
                }}
                disabled={recentLoading || liveTraceLoading}
              >
                <RefreshCw size={12} className={(recentLoading || liveTraceLoading) ? 'animate-spin' : ''} />
                刷新
              </button>
            </div>

            <div className="mt-3 space-y-3">
              {liveTraceLoading && liveTraceItems.length === 0 && (
                <NoticeBanner variant="info" title="加载中" className="px-4 py-8">
                  正在加载最近请求的链路详情…
                </NoticeBanner>
              )}
              {!liveTraceLoading && liveTraceError && liveTraceItems.length === 0 && (
                <NoticeBanner variant="error" title="活跃链路流加载失败" className="px-4 py-8">
                  {liveTraceError}
                </NoticeBanner>
              )}
              {!liveTraceLoading && !liveTraceError && liveTraceItems.length === 0 && (
                <EmptyStateCard title="暂无可展示请求链路" hint="有新的请求后会自动显示在这里。" className="p-6" />
              )}
              {liveTraceItems.map((item) => (
                <LiveTraceCard
                  key={item.session.request_id}
                  item={item}
                  selected={item.session.request_id === selectedLiveTraceItem?.session.request_id}
                  onSelect={() => focusLiveTraceItem(item, { showOverview: true })}
                  onOpenDetail={() => openRequestTrace(item.session.request_id)}
                />
              ))}
            </div>
          </div>

          <div className="rounded-2xl border border-primary-100/70 bg-white/88 p-3 shadow-sm shadow-blue-100/40">
            <div className="border-b border-primary-100/70 px-1 pb-3">
              <div className="text-sm font-semibold text-gray-900">当前链路预览</div>
              <div className="text-xs text-gray-500">右侧固定看板会跟随选中请求切换，便于快速定位当前跑到哪一层。</div>
            </div>
            <div className="mt-3">
              <LiveTraceStreamPreview
                item={selectedLiveTraceItem}
                timelineEntries={selectedLiveTraceTimelineEntries}
                onHighlight={() => selectedLiveTraceItem && focusLiveTraceItem(selectedLiveTraceItem, { showOverview: true })}
                onOpenDetail={() => selectedLiveTraceItem && openRequestTrace(selectedLiveTraceItem.session.request_id)}
              />
            </div>
          </div>
        </div>
        </div>
      </div>

      {inlineOverviewVisible && traceData && (
        <div ref={inlineOverviewRef}>
          <React.Suspense
            fallback={(
              <div className="card p-4">
                <NoticeBanner variant="info" title="加载中">
                  正在加载当前请求总览图…
                </NoticeBanner>
              </div>
            )}
          >
            <TraceInlineFullChainOverview
              traceData={traceData}
              requestId={selectedRequestId}
              onOpenDetail={() => selectedRequestId && openRequestTrace(selectedRequestId)}
              onOpenStandalone={() => navigate('/trace/full')}
              onHide={() => setInlineOverviewVisible(false)}
              renderImmuneStageTags={renderImmuneStageTags}
              getLiveTraceStageClasses={getLiveTraceStageClasses}
            />
          </React.Suspense>
        </div>
      )}

      {!isHistorySearchMode && !showHistoryPanel && (
        <button
          className="card w-full rounded-xl border-dashed border-primary-200 bg-white/85 px-4 py-4 text-left transition-colors hover:border-primary-300 hover:bg-primary-50/35"
          onClick={() => setHistoryExpanded(true)}
        >
          <div className="flex items-center gap-3">
            <span className="flex items-center justify-center w-9 h-9 rounded-xl border border-primary-100/70 bg-white text-blue-600">
              <Clock3 size={16} />
            </span>
            <div className="min-w-0 flex-1">
              <div className="text-sm font-semibold text-gray-900">查看历史请求</div>
              <div className="text-xs text-gray-500">{historySectionHint}</div>
            </div>
            <span className="text-xs font-medium text-blue-600">展开</span>
          </div>
        </button>
      )}

      {!isHistorySearchMode && showHistoryPanel && (
        <div className="card overflow-hidden rounded-xl">
          <div className="flex items-center gap-2 border-b border-primary-100/70 bg-white/80 px-4 py-3 flex-wrap">
            <Clock3 size={15} className="text-blue-600" />
            <div className="text-sm font-semibold text-gray-900">历史请求</div>
            <div className="text-xs text-gray-400">{historySectionHint}</div>
            <div className="ml-auto flex items-center gap-2">
              {latestRequest && (
                <button
                  className="btn-primary text-xs"
                  onClick={() => openRequestTrace(latestRequest.request_id)}
                >
                  打开最新
                </button>
              )}
              <button
                className="btn-secondary text-xs"
                onClick={() => { void recentRequestsQuery.refetch() }}
                disabled={recentLoading}
              >
                <RefreshCw size={12} className={recentLoading ? 'animate-spin' : ''} />
                刷新
              </button>
              {canCollapseHistory && (
                <button
                  className="btn-secondary text-xs"
                  onClick={() => setHistoryExpanded(false)}
                >
                  收起
                </button>
              )}
            </div>
          </div>
          <div className="divide-y divide-primary-100/70">
            {recentLoading && recentRequests.length === 0 && (
              <div className="px-4 py-4">
                <NoticeBanner variant="info" title="加载中">
                  正在加载历史请求…
                </NoticeBanner>
              </div>
            )}
            {!recentLoading && recentRequests.length === 0 && (
              <div className="px-4 py-4">
                <EmptyStateCard title="暂无历史请求" hint="链路请求完成后会在这里沉淀。" className="p-4" />
              </div>
            )}
            {recentRequests.map((item) => {
              const statusMeta = getRecentRequestStatusMeta(item.status)
              return (
                <button
                  key={item.request_id}
                  className="w-full px-4 py-3 text-left transition-colors hover:bg-primary-50/45"
                  onClick={() => openRequestTrace(item.request_id)}
                >
                  <div className="flex items-start gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium text-gray-900 break-all">
                        {item.input_preview || item.request_id}
                      </div>
                      <div className="mt-1 flex items-center gap-3 text-xs text-gray-500 flex-wrap">
                        <span className="font-mono">{item.request_id.slice(0, 18)}...</span>
                        {item.session_id ? <span>{item.session_id}</span> : null}
                        <span>{formatRecentTraceTime(item.ts_ms)}</span>
                        {item.total_latency_ms != null ? <span>{item.total_latency_ms}ms</span> : null}
                      </div>
                    </div>
                    <div className="shrink-0 flex items-center gap-2">
                      <span className={`text-xs font-medium ${statusMeta.className}`}>
                        {statusMeta.label}
                      </span>
                      <ChevronRight size={14} className="text-gray-400" />
                    </div>
                  </div>
                </button>
              )
            })}
          </div>
        </div>
      )}

      <div className="card overflow-hidden rounded-2xl">
        <button
          className="w-full px-4 py-4 text-left transition-colors hover:bg-primary-50/45"
          onClick={() => setAdvancedViewsExpanded((value) => !value)}
        >
          <div className="flex items-center gap-3">
            <span className="flex items-center justify-center w-9 h-9 rounded-xl border border-primary-100/70 bg-white text-blue-600">
              <GitBranch size={16} />
            </span>
            <div className="min-w-0 flex-1">
              <div className="text-sm font-semibold text-gray-900">高级钻取视图</div>
              <div className="text-xs text-gray-500">只有需要按分组拆开排查时再展开。</div>
            </div>
            <span className="text-xs font-medium text-blue-600">{advancedViewsExpanded ? '收起' : '展开'}</span>
          </div>
        </button>

        {advancedViewsExpanded && (
          <div className="space-y-4 border-t border-primary-100/70 bg-white/75 p-4">
            <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
              {groupStats.map((g) => {
                const Icon = g.icon
                const isActive = g.activeCount > 0
                return (
                  <button
                    key={g.id}
                    className="text-left rounded-xl border-2 p-4 transition-all hover:shadow-md hover:-translate-y-0.5 active:translate-y-0 cursor-pointer"
                    style={{
                      borderColor: isActive ? g.color : g.border,
                      background: isActive ? g.bg : '#FFFFFF',
                      boxShadow: isActive ? `0 0 0 1px ${g.color}22, 0 2px 8px ${g.color}11` : undefined,
                    }}
                    onClick={() => navigate(`/trace/group/${g.id}`)}
                  >
                    <div className="flex items-start justify-between mb-3">
                      <span
                        className="flex items-center justify-center w-9 h-9 rounded-lg"
                        style={{ background: isActive ? g.color : g.bg, color: isActive ? '#fff' : g.color }}
                      >
                        <Icon size={18} />
                      </span>
                      <div className="flex items-center gap-1.5">
                        {g.hasError && (
                          <span className="text-xs px-1.5 py-0.5 rounded-full bg-red-50 text-red-600 border border-red-200 font-medium">
                            异常
                          </span>
                        )}
                        {isActive && (
                          <span
                            className="text-xs px-1.5 py-0.5 rounded-full font-medium"
                            style={{ background: g.bg, color: g.color, border: `1px solid ${g.border}` }}
                          >
                            {g.activeCount}/{g.totalNodeCount}
                          </span>
                        )}
                        <ChevronRight size={14} className="text-gray-400" />
                      </div>
                    </div>
                    <div className="font-semibold text-gray-900 text-sm mb-1">{g.label}</div>
                    <div className="text-xs text-gray-500 leading-relaxed line-clamp-2">{g.desc}</div>
                    {g.id === 'immune' && (
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {g.immuneMeta?.decision || g.immuneMeta?.riskLevel ? (
                          <span className="inline-flex items-center rounded-full border border-orange-200 bg-orange-50 px-2 py-0.5 text-[11px] font-medium text-orange-700">
                            {(g.immuneMeta?.decision || 'IMMUNE').toUpperCase()}
                            {g.immuneMeta?.riskLevel ? `/${String(g.immuneMeta.riskLevel).toLowerCase()}` : ''}
                          </span>
                        ) : null}
                        {(g.immuneMeta?.reasonTags || []).slice(0, 2).map((reasonCode) => (
                          <span key={`${g.id}-${reasonCode}`} className="inline-flex items-center rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700">
                            {reasonCode}
                          </span>
                        ))}
                      </div>
                    )}
                    {traceData && (
                      <div className="mt-3 h-1 overflow-hidden rounded-full bg-primary-100/70">
                        <div
                          className="h-1 rounded-full transition-all"
                          style={{ width: `${(g.activeCount / Math.max(g.totalNodeCount, 1)) * 100}%`, background: g.color }}
                        />
                      </div>
                    )}
                  </button>
                )
              })}
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_18rem] gap-4">
              <button
                className="flex w-full items-center gap-4 rounded-xl border-2 border-dashed border-primary-200 p-4 text-left transition-all hover:border-blue-400 hover:bg-blue-50"
                onClick={() => navigate('/trace/full')}
              >
                <span className="flex items-center justify-center w-10 h-10 rounded-lg bg-blue-50 text-blue-600 shrink-0">
                  <Layers size={20} />
                </span>
                <div className="flex-1">
                  <div className="font-semibold text-gray-800 text-sm">双端全链路拓扑图</div>
                  <div className="text-xs text-gray-500 mt-0.5">需要单独放大排查静态拓扑图时再进入。</div>
                </div>
                <ChevronRight size={16} className="text-gray-400 shrink-0" />
              </button>

              <div className="grid grid-cols-2 gap-3">
                {[
                  { label: '总节点数', value: FULL_CHAIN_SCHEMA.nodes.length, color: 'text-blue-600' },
                  { label: '总边数', value: FULL_CHAIN_SCHEMA.edges.length, color: 'text-indigo-600' },
                  { label: '激活节点', value: traceData ? (traceData.nodes ?? []).length : '—', color: 'text-green-600' },
                  { label: '总耗时', value: traceInfo?.latency != null ? `${traceInfo.latency}ms` : '—', color: 'text-orange-500' },
                ].map(({ label, value, color }) => (
                  <div key={label} className="rounded-xl border border-primary-100/70 bg-white/90 p-4 text-center">
                    <div className={`text-2xl font-bold ${color}`}>{value}</div>
                    <div className="text-xs text-gray-500 mt-1">{label}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
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
export default function Trace() {
  const [traceData, setTraceData] = useState(null)
  const [traceInfo, setTraceInfo] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const sharedProps = { traceData, setTraceData, traceInfo, setTraceInfo, loading, setLoading, error, setError }

  return (
    <Routes>
      <Route index element={<TraceHome {...sharedProps} />} />
      <Route
        path="full"
        element={(
          <React.Suspense
            fallback={(
              <div className="card p-4">
                <NoticeBanner variant="info" title="加载中">
                  正在加载全链路拓扑图…
                </NoticeBanner>
              </div>
            )}
          >
            <TraceFullChainView traceData={traceData} />
          </React.Suspense>
        )}
      />
      <Route
        path="group/:groupId"
        element={(
          <React.Suspense
            fallback={(
              <div className="card p-4">
                <NoticeBanner variant="info" title="加载中">
                  正在加载分组链路图…
                </NoticeBanner>
              </div>
            )}
          >
            <TraceGroupDagRoute traceData={traceData} />
          </React.Suspense>
        )}
      />
      <Route
        path=":requestId"
        element={(
          <React.Suspense
            fallback={(
              <div className="card p-4">
                <NoticeBanner variant="info" title="加载中">
                  正在打开请求级 Trace 详情…
                </NoticeBanner>
              </div>
            )}
          >
            <TraceRequestDetail />
          </React.Suspense>
        )}
      />
    </Routes>
  )
}

