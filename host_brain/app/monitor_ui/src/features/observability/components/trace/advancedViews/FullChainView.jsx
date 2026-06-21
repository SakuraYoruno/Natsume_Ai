import React, { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  useEdgesState,
  useNodesState,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { ArrowLeft, Layers, Maximize2, Minimize2 } from 'lucide-react'
import CustomNode from '../CustomNode.jsx'
import NodeDetailPanel from '../NodeDetailPanel.jsx'
import { getObservedTraceSchema } from '../traceFlowModel.js'
import { buildTraceAdvancedFlowData } from '../traceAdvancedViewModel.js'

const NODE_TYPES = { customNode: CustomNode }

export default function FullChainView({ traceData, onSelectNode }) {
  const [nodes, setNodes, onNodesChange] = useNodesState([])
  const [edges, setEdges, onEdgesChange] = useEdgesState([])
  const [selectedNode, setSelectedNode] = useState(null)
  const [fullscreen, setFullscreen] = useState(false)
  const containerRef = useRef(null)
  const navigate = useNavigate()

  const handleSelect = useCallback((data) => {
    setSelectedNode(data)
    onSelectNode?.(data)
  }, [onSelectNode])

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

  return (
    <div className="flex h-full min-h-0 flex-1 flex-col">
      <div className="card mb-4 space-y-3 p-4">
        <div className="flex flex-wrap items-center gap-3">
          <button className="btn-secondary text-xs" onClick={() => navigate('/observability/trace')}>
            <ArrowLeft size={14} />
            返回
          </button>
          <div className="flex items-center gap-2">
            <Layers size={16} className="text-blue-600" />
            <h2 className="text-base font-semibold text-gray-900">双端全链路拓扑图</h2>
          </div>
          <div className="ml-auto flex items-center gap-3 text-xs text-gray-500">
            <div className="flex items-center gap-1.5">
              <div className="h-3 w-3 rounded border-2 border-gray-300" />
              <span>未激活</span>
            </div>
            <div className="flex items-center gap-1.5">
              <div className="h-3 w-3 rounded border-2 border-blue-500" style={{ boxShadow: '0 0 4px rgba(41,112,255,0.4)' }} />
              <span>激活链路</span>
            </div>
          </div>
          <button
            className="btn-secondary p-1.5"
            onClick={() => {
              if (!fullscreen) containerRef.current?.requestFullscreen?.()
              else document.exitFullscreen?.()
              setFullscreen(!fullscreen)
            }}
          >
            {fullscreen ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
          </button>
        </div>
        <div className="lifeline-rail h-1.5">
          <div className="lifeline-wave h-full w-full" />
        </div>
      </div>
      <div ref={containerRef} className="flex min-h-0 flex-1 gap-4" style={{ height: fullscreen ? '100vh' : '34rem', minHeight: fullscreen ? '100vh' : '30rem' }}>
        <div className="card flex-1 overflow-hidden" style={{ minHeight: fullscreen ? '100vh' : '30rem' }}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            nodeTypes={NODE_TYPES}
            fitView
            fitViewOptions={{ padding: 0.08, minZoom: 0.42 }}
            proOptions={{ hideAttribution: true }}
            minZoom={0.42}
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
        {selectedNode && (
          <NodeDetailPanel node={selectedNode} onClose={() => setSelectedNode(null)} />
        )}
      </div>
    </div>
  )
}
