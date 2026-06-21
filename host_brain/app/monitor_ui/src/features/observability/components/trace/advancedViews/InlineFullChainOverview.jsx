import React, { useCallback, useEffect, useState } from 'react'
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  useEdgesState,
  useNodesState,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { Layers } from 'lucide-react'
import CustomNode from '../CustomNode.jsx'
import EmptyStateCard from '../../../../../shared/ui/EmptyStateCard.jsx'
import NodeDetailPanel from '../NodeDetailPanel.jsx'
import RenderValue from '../../../../../shared/ui/RenderValue.jsx'
import { GROUPS, getLiveTraceFlow, getObservedTraceSchema } from '../traceFlowModel.js'
import { buildTraceAdvancedFlowData } from '../traceAdvancedViewModel.js'

const NODE_TYPES = { customNode: CustomNode }

export default function InlineFullChainOverview({
  traceData,
  requestId,
  onOpenDetail,
  onOpenStandalone,
  onHide,
  renderImmuneStageTags,
  getLiveTraceStageClasses,
}) {
  const [nodes, setNodes, onNodesChange] = useNodesState([])
  const [edges, setEdges, onEdgesChange] = useEdgesState([])
  const [selectedNode, setSelectedNode] = useState(null)

  const handleSelect = useCallback((data) => {
    setSelectedNode(data)
  }, [])

  const overviewSchema = React.useMemo(() => getObservedTraceSchema(traceData), [traceData])

  useEffect(() => {
    const { nodes: flowNodes, edges: flowEdges } = buildTraceAdvancedFlowData(
      overviewSchema.nodes,
      overviewSchema.edges,
      handleSelect,
      overviewSchema.activeNodeIds,
      overviewSchema.traceNodeMap,
    )
    setNodes(flowNodes)
    setEdges(flowEdges)
  }, [overviewSchema, handleSelect, setEdges, setNodes])

  if (!traceData) return null

  const currentStatus = String(traceData.status || '').toUpperCase()
  const currentHealthy = ['OK', 'UP', 'SUCCESS'].includes(currentStatus)
  const flow = getLiveTraceFlow(traceData)
  const activeNodeSet = new Set(overviewSchema.activeNodeIds ?? [])
  const nodeCount = activeNodeSet.size
  const edgeCount = overviewSchema.edges.filter((edge) => activeNodeSet.has(edge.source) && activeNodeSet.has(edge.target)).length
  const rawNodeCount = overviewSchema.rawNodeCount
  const resolveStageClasses = typeof getLiveTraceStageClasses === 'function'
    ? getLiveTraceStageClasses
    : (() => '')
  const renderStageTags = typeof renderImmuneStageTags === 'function'
    ? renderImmuneStageTags
    : () => null

  return (
    <div className="card rounded-2xl p-4 md:p-5">
      <div className="flex flex-wrap items-start gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-blue-600 text-white shadow-sm">
            <Layers size={18} />
          </span>
          <div>
            <h2 className="text-base font-semibold text-gray-900">当前请求总览图</h2>
            <p className="text-xs text-gray-500">
              高亮后直接在首页看完整链路，不再需要先点分组卡片再看效果。
            </p>
          </div>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-medium ${currentHealthy ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-amber-200 bg-amber-50 text-amber-700'}`}>
            {currentStatus || '未知'}
          </span>
          <span className="inline-flex items-center rounded-full border border-gray-200 bg-gray-50 px-2.5 py-1 text-xs text-gray-600">
            {nodeCount} 拓扑节点
          </span>
          <span className="inline-flex items-center rounded-full border border-gray-200 bg-gray-50 px-2.5 py-1 text-xs text-gray-600">
            {edgeCount} 激活连线
          </span>
          <span className="inline-flex items-center rounded-full border border-gray-200 bg-gray-50 px-2.5 py-1 text-xs text-gray-600">
            {rawNodeCount} 原始事件
          </span>
          {traceData.total_latency_ms != null && (
            <span className="inline-flex items-center rounded-full border border-gray-200 bg-gray-50 px-2.5 py-1 text-xs text-gray-600">
              {traceData.total_latency_ms}ms
            </span>
          )}
          <button className="btn-secondary text-xs" onClick={onOpenStandalone}>
            独立大图
          </button>
          {requestId && (
            <button className="btn-primary text-xs" onClick={onOpenDetail}>
              打开详情
            </button>
          )}
          <button className="btn-secondary text-xs" onClick={onHide}>
            收起
          </button>
        </div>
      </div>
      <div className="lifeline-rail mt-3 h-1.5">
        <div className="lifeline-wave h-full w-full" />
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        {flow.map((stage) => {
          const group = GROUPS.find((itemGroup) => itemGroup.id === stage.groupId)
          const className = resolveStageClasses(stage, group)
          const style = stage.status === 'done' && group
            ? { background: group.bg, color: group.color, borderColor: group.border }
            : undefined
          return (
            <div key={`overview-${traceData.request_id}-${stage.groupId}`} className="inline-flex items-center gap-1.5">
              <span
                className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium ${className}`}
                style={style}
              >
                {stage.label}
                {stage.count > 0 ? `${stage.count}/${stage.total}` : '0'}
              </span>
              {renderStageTags(stage, `overview-${traceData.request_id}-${stage.groupId}`)}
            </div>
          )
        })}
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="overflow-hidden rounded-xl border border-primary-100/70 bg-white/80" style={{ height: '34rem' }}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            nodeTypes={NODE_TYPES}
            fitView
            fitViewOptions={{ padding: 0.12 }}
            proOptions={{ hideAttribution: true }}
            minZoom={0.08}
            maxZoom={2}
          >
            <Background color="#F2F4F7" gap={20} size={1} />
            <Controls showInteractive={false} />
            <MiniMap
              nodeColor={(node) => node.data?.active ? '#2970FF' : (node.data?.inactive ? '#CBD5E1' : '#94A3B8')}
              maskColor="rgba(249,250,251,0.7)"
            />
          </ReactFlow>
        </div>

        <div className="space-y-4">
          <div className="rounded-xl border border-primary-100/70 bg-white/80 p-4">
            <div className="text-xs font-medium text-gray-500">当前请求</div>
            <div className="mt-1 break-all text-sm font-semibold text-gray-900">
              <RenderValue value={traceData.summary?.input_text || requestId || traceData.request_id} />
            </div>
            <div className="mt-2 break-all font-mono text-xs text-gray-500">{traceData.request_id}</div>
            <div className="mt-3 grid grid-cols-2 gap-3 text-xs">
              <div>
                <div className="text-gray-400">输入来源</div>
                <div className="mt-1 text-gray-700"><RenderValue value={traceData.summary?.source || 'GATEWAY/ASR'} /></div>
              </div>
              <div>
                <div className="text-gray-400">最终状态</div>
                <div className={`mt-1 font-medium ${currentHealthy ? 'text-green-600' : 'text-amber-600'}`}><RenderValue value={currentStatus || '未知'} /></div>
              </div>
            </div>
          </div>

          {selectedNode ? (
            <NodeDetailPanel node={selectedNode} onClose={() => setSelectedNode(null)} />
          ) : (
            <EmptyStateCard title="请选择节点" hint="点击左侧节点后查看输入、输出与状态。" className="p-5" />
          )}
        </div>
      </div>
    </div>
  )
}
