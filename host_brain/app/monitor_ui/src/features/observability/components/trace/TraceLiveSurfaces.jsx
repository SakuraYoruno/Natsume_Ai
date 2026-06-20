import React from 'react'
import { ChevronRight } from 'lucide-react'
import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'
import StatusBadge from '../../../../shared/ui/StatusBadge.jsx'
import RenderValue from '../../../../shared/ui/RenderValue.jsx'
import {
  buildLiveTraceMetrics,
  formatEventPayloadPreview,
  formatRecentTraceTime,
  getLiveTraceChangeBadgeClass,
  getLiveTraceCurrentLabel,
  getLiveTraceOutputPreview,
  getLiveTraceStageClasses,
  getLiveTraceTimelineEntries,
  getRecentRequestStatusMeta,
  getSessionDisplayTitle,
  getTraceActiveStageMeta,
  getTraceIssueCount,
  getTraceRunningCount,
  isTribeEventLinkedToSelection,
  mergeLiveTraceTimelineEntries,
} from './traceLiveSurfaceModel.js'
import {
  GROUPS,
  normalizeBadgeStatus,
} from './traceFlowModel.js'
import { getTraceNodeDisplayName } from './traceNodeText.js'

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
          <RenderValue value={code} />
        </span>
      ))}
    </>
  )
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
