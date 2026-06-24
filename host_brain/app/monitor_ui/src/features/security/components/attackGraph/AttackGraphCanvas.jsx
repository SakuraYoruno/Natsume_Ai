import React, { useMemo, useState } from 'react'
import {
  Background,
  Controls,
  MarkerType,
  ReactFlow,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import dagre from '@dagrejs/dagre'
import { Activity, Bug, GitBranch, LockKeyhole } from 'lucide-react'
import { PENTEST_COPY } from '../../../../shared/content/terminology.js'
import AttackGraphNode from './AttackGraphNode.jsx'

const NODE_TYPES = { attackGraphNode: AttackGraphNode }
const NODE_WIDTH = 190
const NODE_HEIGHT = 92

function graphDirection(nodes = []) {
  return nodes.length >= 9 ? 'TB' : 'LR'
}

function graphMinZoom(nodes = [], direction) {
  if (direction === 'TB') return nodes.length >= 11 ? 0.38 : 0.46
  if (nodes.length >= 7) return 0.52
  if (nodes.length >= 5) return 0.62
  return 0.72
}

function applyAttackGraphLayout(nodes, edges, direction) {
  const graph = new dagre.graphlib.Graph()
  graph.setDefaultEdgeLabel(() => ({}))
  graph.setGraph({
    rankdir: direction,
    nodesep: direction === 'TB' ? 22 : 20,
    ranksep: direction === 'TB' ? 48 : 58,
    marginx: direction === 'TB' ? 34 : 36,
    marginy: direction === 'TB' ? 30 : 34,
  })

  nodes.forEach((node) => {
    graph.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT })
  })
  edges.forEach((edge) => {
    graph.setEdge(edge.source, edge.target)
  })
  dagre.layout(graph)

  return {
    nodes: nodes.map((node) => {
      const position = graph.node(node.id)
      return {
        ...node,
        position: {
          x: position.x - NODE_WIDTH / 2,
          y: position.y - NODE_HEIGHT / 2,
        },
      }
    }),
    edges,
  }
}

function edgeColor(edge) {
  if (edge.status === 'blocked') return '#DC2626'
  if (edge.status === 'gated' || String(edge.relation || '').includes('exploit')) return '#EA580C'
  if (edge.status === 'finding' || String(edge.relation || '').includes('finding')) return '#E11D48'
  return '#0891B2'
}

function buildFlowData(graph, onSelect, direction) {
  const nodes = (graph?.nodes || []).map((node) => ({
    id: node.id,
    type: 'attackGraphNode',
    position: { x: 0, y: 0 },
    focusable: false,
    selectable: false,
    ariaRole: 'presentation',
    data: {
      ...node,
      layoutDirection: direction,
      onSelect,
    },
  }))
  const edges = (graph?.edges || []).map((edge) => {
    const color = edgeColor(edge)
    return {
      id: edge.id,
      source: edge.source,
      target: edge.target,
      type: 'smoothstep',
      label: edge.relation,
      ariaLabel: null,
      ariaRole: 'presentation',
      focusable: false,
      selectable: false,
      animated: edge.status === 'gated' || edge.status === 'finding',
      markerEnd: {
        type: MarkerType.ArrowClosed,
        color,
        width: 16,
        height: 16,
      },
      style: {
        stroke: color,
        strokeWidth: edge.status === 'blocked' ? 2.4 : 1.8,
        opacity: edge.status === 'blocked' ? 0.95 : 0.75,
      },
      labelStyle: {
        fill: '#334155',
        fontSize: 10,
        fontWeight: 600,
      },
      labelBgStyle: {
        fill: '#FFFFFF',
        fillOpacity: 0.86,
      },
    }
  })
  return applyAttackGraphLayout(nodes, edges, direction)
}

function SummaryTile({ icon: Icon, label, value, tone }) {
  return (
    <div className={`rounded-lg border px-3 py-2 ${tone}`}>
      <div className="flex items-center gap-1.5 text-xs font-semibold">
        <Icon size={13} />
        {label}
      </div>
      <div className="mt-1 text-lg font-bold leading-none">{value}</div>
    </div>
  )
}

export default function AttackGraphCanvas({ graph, className = '' }) {
  const [selectedNode, setSelectedNode] = useState(null)
  const nodes = graph?.nodes || []
  const layoutDirection = useMemo(() => graphDirection(nodes), [nodes])
  const flowData = useMemo(() => buildFlowData(graph, setSelectedNode, layoutDirection), [graph, layoutDirection])
  const summary = graph?.summary || {}
  const minZoom = graphMinZoom(nodes, layoutDirection)
  const shouldFitView = nodes.length <= 4
  const defaultViewport = layoutDirection === 'TB'
    ? { x: 24, y: 28, zoom: minZoom }
    : { x: 28, y: 110, zoom: minZoom }

  return (
    <div className={`space-y-3 ${className}`}>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <SummaryTile icon={GitBranch} label="节点" value={summary.nodes ?? nodes.length} tone="border-cyan-100 bg-cyan-50/70 text-cyan-800" />
        <SummaryTile icon={Activity} label="连线" value={summary.edges ?? graph?.edges?.length ?? 0} tone="border-blue-100 bg-blue-50/70 text-blue-800" />
        <SummaryTile icon={Bug} label="发现" value={summary.findings ?? 0} tone="border-rose-100 bg-rose-50/70 text-rose-800" />
        <SummaryTile icon={LockKeyhole} label="门禁" value={summary.gated ?? 0} tone="border-orange-100 bg-orange-50/70 text-orange-800" />
      </div>

      <div className="grid grid-cols-1 gap-2 md:grid-cols-4">
        {PENTEST_COPY.graphLegend.map((item) => (
          <div key={item.key} className={`rounded-lg border px-3 py-2 ${item.className}`}>
            <div className="text-xs font-semibold">{item.label}</div>
            <div className="mt-0.5 text-[11px] leading-4 opacity-80">{item.detail}</div>
          </div>
        ))}
      </div>

      <div className={`${layoutDirection === 'TB' ? 'h-[560px] md:h-[480px]' : 'h-[260px] md:h-[300px]'} overflow-hidden rounded-xl border border-gray-200 bg-slate-50`}>
        <ReactFlow
          key={graph?.graphId || 'attack-graph'}
          nodes={flowData.nodes}
          edges={flowData.edges}
          nodeTypes={NODE_TYPES}
          fitView={shouldFitView}
          defaultViewport={defaultViewport}
          fitViewOptions={{ padding: layoutDirection === 'TB' ? 0.05 : 0.08, minZoom, maxZoom: 1.05 }}
          minZoom={minZoom}
          maxZoom={1.8}
          nodesDraggable={false}
          nodesConnectable={false}
          nodesFocusable={false}
          edgesFocusable={false}
          elementsSelectable={false}
          selectNodesOnDrag={false}
          autoPanOnNodeFocus={false}
          deleteKeyCode={null}
          selectionKeyCode={null}
          multiSelectionKeyCode={null}
          onPaneClick={() => setSelectedNode(null)}
          proOptions={{ hideAttribution: true }}
        >
          <Background color="#CBD5E1" gap={20} size={1} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>

      {selectedNode ? (
        <div className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-semibold text-gray-900">{selectedNode.label}</span>
            <span className="rounded bg-gray-100 px-2 py-0.5 text-xs text-gray-600">{selectedNode.kind}</span>
            <span className={`rounded border px-2 py-0.5 text-xs font-semibold ${selectedNode.riskClassName}`}>
              {selectedNode.riskLabel}
            </span>
          </div>
          <div className="mt-1 text-xs text-gray-500">{selectedNode.meta || '暂无节点元信息'}</div>
          {selectedNode.evidenceCount ? (
            <div className="mt-1 text-xs font-semibold text-cyan-700">{selectedNode.evidenceCount} 条 evidence refs</div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
