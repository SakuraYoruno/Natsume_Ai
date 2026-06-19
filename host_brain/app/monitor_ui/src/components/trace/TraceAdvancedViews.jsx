import React, { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  Background,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlow,
  useEdgesState,
  useNodesState,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import {
  ArrowLeft,
  BarChart2,
  GitBranch,
  Layers,
  Maximize2,
  Minimize2,
  Search,
} from 'lucide-react'
import CustomNode from './CustomNode.jsx'
import NodeDetailPanel from './NodeDetailPanel.jsx'
import EmptyStateCard from '../EmptyStateCard.jsx'
import { getTraceNodeDisplayName } from './traceNodeText.js'
import { applyDagreLayout } from '../../utils/traceLayout.js'
import {
  GROUPS,
  FULL_CHAIN_SCHEMA,
  buildImmuneStageMeta,
  getLiveTraceFlow,
  getObservedTraceSchema,
  getTraceGroupNodeIds,
  getTraceGroupState,
} from './traceFlowModel.js'

const NODE_TYPES = { customNode: CustomNode }

function buildFlowData(schemaNodes, schemaEdges, onSelect, activeNodeIds = null, traceNodeMap = {}) {
  const activeSet = activeNodeIds ? new Set(activeNodeIds) : null

  const flowNodes = schemaNodes.map((node) => {
    const isActive = activeSet ? activeSet.has(node.node_id) : false
    const isInactive = activeSet ? !isActive : false
    const traceNode = traceNodeMap[node.node_id]
    return {
      id: node.node_id,
      type: 'customNode',
      position: { x: 0, y: 0 },
      data: {
        ...node,
        ...(isActive && traceNode ? traceNode : {}),
        onSelect,
        inactive: isInactive,
        active: isActive,
      },
    }
  })

  const flowEdges = schemaEdges.map((edge) => {
    const isActiveEdge = activeSet
      ? activeSet.has(edge.source) && activeSet.has(edge.target)
      : false
    return {
      id: `e-${edge.source}-${edge.target}`,
      source: edge.source,
      target: edge.target,
      type: 'smoothstep',
      animated: isActiveEdge,
      markerEnd: {
        type: MarkerType.ArrowClosed,
        color: isActiveEdge ? '#2970FF' : '#94A3B8',
        width: 16,
        height: 16,
      },
      style: {
        stroke: isActiveEdge ? '#2970FF' : '#94A3B8',
        strokeWidth: isActiveEdge ? 2.5 : 1.5,
        opacity: isActiveEdge ? 1 : 0.55,
      },
    }
  })

  return applyDagreLayout(flowNodes, flowEdges)
}

function computeGanttTimeline(traceData) {
  const nodes = traceData.nodes ?? []
  const edges = traceData.edges ?? []
  const inEdges = {}
  nodes.forEach((node) => { inEdges[node.node_id] = [] })
  edges.forEach((edge) => {
    if (inEdges[edge.target] !== undefined) inEdges[edge.target].push(edge.source)
  })
  const latencyMap = {}
  nodes.forEach((node) => { latencyMap[node.node_id] = node.latency_ms ?? 0 })
  const startMap = {}
  const visited = new Set()

  function calcStart(nodeId) {
    if (visited.has(nodeId)) return startMap[nodeId] ?? 0
    visited.add(nodeId)
    const preds = inEdges[nodeId] ?? []
    startMap[nodeId] = preds.length === 0
      ? 0
      : Math.max(...preds.map((pred) => calcStart(pred) + (latencyMap[pred] ?? 0)))
    return startMap[nodeId]
  }

  nodes.forEach((node) => calcStart(node.node_id))
  const totalMs = Math.max(...nodes.map((node) => (startMap[node.node_id] ?? 0) + (node.latency_ms ?? 0)), 1)
  return nodes.map((node) => ({
    ...node,
    start_ms: startMap[node.node_id] ?? 0,
    end_ms: (startMap[node.node_id] ?? 0) + (node.latency_ms ?? 0),
    total_ms: totalMs,
  }))
}

const NODE_TYPE_COLOR = {
  intent: '#2970FF',
  governor: '#7A5AF8',
  memory: '#0BA5EC',
  immune: '#EA580C',
  llm: '#F79009',
  tts: '#12B76A',
  vts: '#EE46BC',
  recall: '#0891B2',
  retrieval: '#0891B2',
  bond: '#D97706',
  asr: '#059669',
  system: '#475467',
  agent: '#7C3AED',
  skill: '#0891B2',
  router: '#475467',
  judge: '#059669',
  edge: '#667085',
  branch: '#0891B2',
  default: '#667085',
}

function nodeColor(nodeType) {
  return NODE_TYPE_COLOR[nodeType] ?? NODE_TYPE_COLOR.default
}

function GanttView({ traceData, onSelectNode }) {
  const timeline = React.useMemo(() => computeGanttTimeline(traceData), [traceData])
  const [filter, setFilter] = useState('')

  if (timeline.length === 0) return null

  const totalMs = timeline[0]?.total_ms ?? 1
  const filtered = filter.trim()
    ? timeline.filter((node) => {
      const searchText = `${getTraceNodeDisplayName(node)} ${node.node_id || ''}`.toLowerCase()
      return searchText.includes(filter.toLowerCase())
    })
    : timeline
  const ticks = Array.from({ length: 6 }, (_, index) => Math.round(totalMs * index / 5))

  return (
    <div className="h-full overflow-auto bg-white p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <span className="text-xs font-medium text-gray-500">节点执行甘特图（总耗时 {totalMs}ms）</span>
        <div className="relative">
          <Search size={11} className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            className="w-36 rounded border border-gray-300 py-1 pl-6 pr-2 text-xs focus:outline-none focus:ring-1 focus:ring-blue-200"
            placeholder="过滤节点…"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          />
        </div>
      </div>
      <div className="mb-2 flex pl-40">
        {ticks.map((tick, index) => (
          <div key={index} className="flex-1 border-l border-gray-200 text-center text-xs text-gray-400 first:border-l-0">
            {tick}ms
          </div>
        ))}
      </div>
      <div className="space-y-1.5">
        {filtered.map((node) => {
          const leftPct = (node.start_ms / totalMs) * 100
          const widthPct = Math.max(((node.latency_ms ?? 0) / totalMs) * 100, 0.5)
          const color = nodeColor(node.node_type)
          return (
            <div
              key={node.node_id}
              className="flex cursor-pointer items-center gap-2 rounded border border-gray-200 bg-gray-50 transition-shadow hover:shadow-sm"
              style={{ padding: '4px 8px' }}
              onClick={() => onSelectNode?.(node)}
              title={`${getTraceNodeDisplayName(node)}: ${node.start_ms}ms → ${node.end_ms}ms (${node.latency_ms ?? 0}ms) [${node.status ?? '未知'}]`}
            >
              <div className="w-36 shrink-0">
                <div className="truncate text-xs font-medium text-gray-700">{getTraceNodeDisplayName(node)}</div>
                <div className="text-xs text-gray-400">{node.latency_ms ?? 0}ms</div>
              </div>
              <div className="relative h-6 flex-1 overflow-hidden rounded bg-gray-100">
                <div
                  className="absolute top-0 h-full rounded"
                  title={`${node.latency_ms ?? 0}ms`}
                  style={{ left: `${leftPct}%`, width: `${widthPct}%`, backgroundColor: color, opacity: 0.85, minWidth: '4px' }}
                />
              </div>
              <span className={`w-10 shrink-0 text-right text-xs font-medium ${(node.status ?? '').toUpperCase() === 'OK' ? 'text-green-600' : 'text-red-500'}`}>
                {node.status ?? '—'}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function GroupDagView({ groupId, traceData, onSelectNode }) {
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
    const hasObservedTrace = traceGroupState && traceGroupState.groupNodes.length > 0
    const nodeIdSet = new Set(group.nodeIds)
    const groupNodes = hasObservedTrace
      ? traceGroupState.groupNodes.map((node) => ({ ...node, layer: node.layer ?? node.side ?? 'host' }))
      : FULL_CHAIN_SCHEMA.nodes.filter((node) => nodeIdSet.has(node.node_id))
    const groupEdges = hasObservedTrace
      ? traceGroupState.groupEdges
      : FULL_CHAIN_SCHEMA.edges.filter((edge) => nodeIdSet.has(edge.source) && nodeIdSet.has(edge.target))
    const activeNodeIds = hasObservedTrace
      ? traceGroupState.groupNodes.map((node) => node.node_id)
      : (traceData ? (traceData.nodes ?? []).map((node) => node.node_id) : null)
    const traceNodeMap = hasObservedTrace
      ? traceGroupState.traceNodeMap
      : (traceData ? Object.fromEntries((traceData.nodes ?? []).map((node) => [node.node_id, node])) : {})

    const { nodes: flowNodes, edges: flowEdges } = buildFlowData(groupNodes, groupEdges, handleSelect, activeNodeIds, traceNodeMap)
    setNodes(flowNodes)
    setEdges(flowEdges)
  }, [group, traceData, traceGroupState, handleSelect, setEdges, setNodes])

  if (!group) {
    return <EmptyStateCard title="分组不存在" hint="请返回链路总览重新选择分组。" className="p-8" />
  }

  const Icon = group.icon
  const activeCount = traceGroupState ? traceGroupState.activeCount : 0
  const totalNodeCount = traceData ? getTraceGroupNodeIds(group).length : group.nodeIds.length
  const immuneGroupMeta = group.id === 'immune'
    ? buildImmuneStageMeta(traceGroupState?.groupNodes ?? [])
    : null

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

      <div className="flex h-full min-h-0 flex-1 gap-4" style={{ height: 'calc(100vh - 260px)' }}>
        <div className="card h-full flex-1 overflow-hidden" style={{ borderColor: group.border, background: '#FAFAFA' }}>
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

export function FullChainView({ traceData, onSelectNode }) {
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
    const { nodes: flowNodes, edges: flowEdges } = buildFlowData(
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
      <div ref={containerRef} className="flex min-h-0 flex-1 gap-4" style={{ height: fullscreen ? '100vh' : 'calc(100vh - 240px)' }}>
        <div className="card h-full flex-1 overflow-hidden">
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
        {selectedNode && (
          <NodeDetailPanel node={selectedNode} onClose={() => setSelectedNode(null)} />
        )}
      </div>
    </div>
  )
}

export function InlineFullChainOverview({
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
    const { nodes: flowNodes, edges: flowEdges } = buildFlowData(
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
              {traceData.summary?.input_text || requestId || traceData.request_id}
            </div>
            <div className="mt-2 break-all font-mono text-xs text-gray-500">{traceData.request_id}</div>
            <div className="mt-3 grid grid-cols-2 gap-3 text-xs">
              <div>
                <div className="text-gray-400">输入来源</div>
                <div className="mt-1 text-gray-700">{traceData.summary?.source || 'GATEWAY/ASR'}</div>
              </div>
              <div>
                <div className="text-gray-400">最终状态</div>
                <div className={`mt-1 font-medium ${currentHealthy ? 'text-green-600' : 'text-amber-600'}`}>{currentStatus || '未知'}</div>
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
