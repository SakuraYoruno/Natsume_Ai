import { Layers3 } from 'lucide-react'
import EmptyStateCard from '../../../../../shared/ui/EmptyStateCard.jsx'
import StatusBadge from '../../../../../shared/ui/StatusBadge.jsx'
import { TraceSectionCard as SectionCard } from '../TraceSurfaces.jsx'
import { getTraceNodeDisplayName } from '../traceNodeText.js'
import { formatDateTime, getNodePreview, normalizeStatus } from '../TraceRequestDetailModel.jsx'
import SubgraphMiniFlow from './SubgraphMiniFlow.jsx'

export default function SubgraphsPanel({ subgraphs, selectedNode, onSelectNode }) {
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
