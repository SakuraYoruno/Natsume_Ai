import React, { useMemo } from 'react'
import { Background, Controls, ReactFlow } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import EmptyStateCard from '../../../../../shared/ui/EmptyStateCard.jsx'
import { buildSubgraphFlow } from '../traceRequestRuntimePanelModel.js'
import { TRACE_NODE_TYPES } from '../TraceRequestDetailModel.jsx'

export default function SubgraphMiniFlow({ subgraph, selectedNode, onSelectNode }) {
  const flow = useMemo(
    () => buildSubgraphFlow(subgraph, onSelectNode, selectedNode),
    [subgraph, onSelectNode, selectedNode]
  )

  if (!flow.nodes.length) {
    return <EmptyStateCard title="子图无节点" hint="该子图没有可渲染的内部节点。" className="p-4" />
  }

  return (
    <div className="h-64 overflow-hidden rounded-xl border border-primary-100/70 bg-white/86">
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
