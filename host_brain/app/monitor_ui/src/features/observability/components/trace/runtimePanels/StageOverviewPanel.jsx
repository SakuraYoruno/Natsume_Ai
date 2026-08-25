import { useMemo } from 'react'
import { Layers3 } from 'lucide-react'
import StatusBadge from '../../../../../shared/ui/StatusBadge.jsx'
import { TraceSectionCard as SectionCard } from '../TraceSurfaces.jsx'
import { getTraceNodeDisplayName } from '../traceNodeText.js'
import {
  buildImmuneStageMeta,
  buildStageSnapshots,
  formatDateTime,
  normalizeStatus,
} from '../TraceRequestDetailModel.jsx'

export default function StageOverviewPanel({ nodes, selectedNode, onSelectNode }) {
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
