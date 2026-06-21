import React, { useMemo } from 'react'
import { Clock3 } from 'lucide-react'
import EmptyStateCard from '../../../../../shared/ui/EmptyStateCard.jsx'
import StatusBadge from '../../../../../shared/ui/StatusBadge.jsx'
import { TraceSectionCard as SectionCard } from '../TraceSurfaces.jsx'
import { getTraceNodeDisplayName } from '../traceNodeText.js'
import {
  ISSUE_STATUSES,
  buildStageTimelineSections,
  formatDateTime,
  getNodePreview,
  normalizeStatus,
} from '../TraceRequestDetailModel.jsx'

export default function NodeTimelinePanel({ nodes, selectedNode, onSelectNode }) {
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
