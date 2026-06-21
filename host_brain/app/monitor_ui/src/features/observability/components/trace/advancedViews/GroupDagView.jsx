import React, { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  useEdgesState,
  useNodesState,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { ArrowLeft, BarChart2, GitBranch } from 'lucide-react'
import CustomNode from '../CustomNode.jsx'
import EmptyStateCard from '../../../../../shared/ui/EmptyStateCard.jsx'
import NodeDetailPanel from '../NodeDetailPanel.jsx'
import { GROUPS, getTraceGroupState } from '../traceFlowModel.js'
import {
  buildGroupDagFlowInputs,
  buildGroupDagHeaderMeta,
  buildTraceAdvancedFlowData,
} from '../traceAdvancedViewModel.js'
import GanttView from './GanttView.jsx'

const NODE_TYPES = { customNode: CustomNode }

export default function GroupDagView({ groupId, traceData, onSelectNode }) {
  const group = GROUPS.find((item) => item.id === groupId)
  const [nodes, setNodes, onNodesChange] = useNodesState([])
  const [edges, setEdges, onEdgesChange] = useEdgesState([])
  const [selectedNode, setSelectedNode] = useState(null)
  const [viewMode, setViewMode] = useState('dag')
  const navigate = useNavigate()
  const traceGroupState = group && traceData ? getTraceGroupState(group, traceData) : null

  const handleSelect = useCallback((data) => {
    setSelectedNode(data)
    onSelectNode?.(data)
  }, [onSelectNode])

  useEffect(() => {
    if (!group) return
    const {
      groupNodes,
      groupEdges,
      activeNodeIds,
      traceNodeMap,
    } = buildGroupDagFlowInputs({ group, traceData, traceGroupState })

    const { nodes: flowNodes, edges: flowEdges } = buildTraceAdvancedFlowData(groupNodes, groupEdges, handleSelect, activeNodeIds, traceNodeMap)
    setNodes(flowNodes)
    setEdges(flowEdges)
  }, [group, traceData, traceGroupState, handleSelect, setEdges, setNodes])

  if (!group) {
    return <EmptyStateCard title="分组不存在" hint="请返回链路总览重新选择分组。" className="p-8" />
  }

  const Icon = group.icon
  const { activeCount, totalNodeCount, immuneGroupMeta } = buildGroupDagHeaderMeta({
    group,
    traceData,
    traceGroupState,
  })

  return (
    <div className="flex h-full min-h-0 flex-1 flex-col">
      <div className="card mb-4 space-y-3 p-4">
        <div className="flex flex-wrap items-center gap-3">
          <button className="btn-secondary text-xs" onClick={() => navigate('/observability/trace')}>
            <ArrowLeft size={14} />
            返回
          </button>
          <div className="flex items-center gap-2">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg" style={{ background: group.bg, color: group.color }}>
              <Icon size={16} />
            </span>
            <div>
              <h2 className="text-base font-semibold text-gray-900">{group.label}</h2>
              <p className="text-xs text-gray-500">{group.desc}</p>
            </div>
          </div>
          {traceData && (
            <div className="ml-auto flex items-center gap-2 text-xs text-gray-500">
              <span className="rounded-full px-2 py-1 font-medium" style={{ background: group.bg, color: group.color }}>
                {activeCount} / {totalNodeCount} 节点激活
              </span>
              {immuneGroupMeta && (immuneGroupMeta.decision || immuneGroupMeta.riskLevel) ? (
                <span className="inline-flex items-center rounded-full border border-orange-200 bg-orange-50 px-2 py-1 font-medium text-orange-700">
                  {(immuneGroupMeta.decision || 'IMMUNE').toUpperCase()}
                  {immuneGroupMeta.riskLevel ? `/${String(immuneGroupMeta.riskLevel).toLowerCase()}` : ''}
                </span>
              ) : null}
              {(immuneGroupMeta?.reasonTags || []).slice(0, 2).map((reasonCode) => (
                <span key={`immune-group-meta-${reasonCode}`} className="inline-flex items-center rounded-full border border-amber-200 bg-amber-50 px-2 py-1 font-medium text-amber-700">
                  {reasonCode}
                </span>
              ))}
            </div>
          )}
          {traceData && (
            <div className="flex overflow-hidden rounded-lg border border-primary-100/80 text-xs">
              <button
                className={`flex items-center gap-1 px-2.5 py-1.5 transition-colors ${viewMode === 'dag' ? 'bg-blue-600 text-white' : 'bg-white/90 text-gray-600 hover:bg-blue-50'}`}
                onClick={() => setViewMode('dag')}
              >
                <GitBranch size={11} />
                拓扑图
              </button>
              <button
                className={`flex items-center gap-1 px-2.5 py-1.5 transition-colors ${viewMode === 'gantt' ? 'bg-blue-600 text-white' : 'bg-white/90 text-gray-600 hover:bg-blue-50'}`}
                onClick={() => setViewMode('gantt')}
              >
                <BarChart2 size={11} />
                甘特图
              </button>
            </div>
          )}
        </div>
        <div className="lifeline-rail h-1.5">
          <div className="lifeline-wave h-full w-full" />
        </div>
      </div>

      <div className="flex min-h-0 flex-1 gap-4" style={{ height: '32rem', minHeight: '28rem' }}>
        <div className="card flex-1 overflow-hidden" style={{ borderColor: group.border, background: '#FAFAFA', minHeight: '28rem' }}>
          {viewMode === 'dag' && (
            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              nodeTypes={NODE_TYPES}
              fitView
              fitViewOptions={{ padding: 0.2 }}
              proOptions={{ hideAttribution: true }}
              minZoom={0.2}
              maxZoom={3}
            >
              <Background color="#E2E8F0" gap={20} size={1} />
              <Controls showInteractive={false} />
              <MiniMap
                nodeColor={(node) => node.data?.active ? group.color : '#CBD5E1'}
                maskColor="rgba(249,250,251,0.7)"
              />
            </ReactFlow>
          )}
          {viewMode === 'gantt' && traceData && (
            <GanttView
              traceData={{ ...traceData, nodes: traceGroupState?.groupNodes ?? [], edges: traceGroupState?.groupEdges ?? [] }}
              onSelectNode={handleSelect}
            />
          )}
        </div>
        {selectedNode && (
          <NodeDetailPanel node={selectedNode} onClose={() => setSelectedNode(null)} />
        )}
      </div>
    </div>
  )
}

export function GroupDagRoute({ traceData }) {
  const { groupId } = useParams()
  return <GroupDagView groupId={groupId} traceData={traceData} />
}
