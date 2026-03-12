import React, { useEffect, useState, useCallback, useRef } from 'react'
import { useParams } from 'react-router-dom'
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  MarkerType,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { Search, Maximize2, Minimize2, RefreshCw, GitBranch, BarChart2, Bookmark } from 'lucide-react'
import CustomNode from '../components/trace/CustomNode.jsx'
import NodeDetailPanel from '../components/trace/NodeDetailPanel.jsx'
import { applyDagreLayout } from '../utils/traceLayout.js'
import { fetchTraceNodes, fetchCheckpoints } from '../api/obs.js'
import useAppStore from '../store/useAppStore.js'

const NODE_TYPES = { customNode: CustomNode }

// ===== Mock 数据（开发阶段） =====
const MOCK_TRACE = {
  request_id: 'mock-req-001',
  status: 'OK',
  total_latency_ms: 1240,
  nodes: [
    { node_id: 'intent',    node_type: 'intent',   display_name: 'Intent Parser',   status: 'OK',   latency_ms: 45,  input_data: { text: '你好，今天天气怎么样？' }, output_data: { action: 'chat', type: 'DIALOGUE' } },
    { node_id: 'governor',  node_type: 'governor', display_name: 'Governor Gate',   status: 'OK',   latency_ms: 12,  input_data: { intent_id: 'i-001' }, output_data: { decision: 'APPROVE' } },
    { node_id: 'memory',    node_type: 'memory',   display_name: 'Memory Recall',   status: 'OK',   latency_ms: 89,  input_data: { query: '天气' }, output_data: { evidence_count: 3 } },
    { node_id: 'llm_main',  node_type: 'llm',      display_name: 'Core Reasoner',   status: 'OK',   latency_ms: 820, provider: 'openai',   model_id: 'gpt-5.2',    input_data: { messages: ['...'] }, output_data: { narrative: '今天天气晴朗...' } },
    { node_id: 'llm_safe',  node_type: 'llm',      display_name: 'Safety Verifier', status: 'OK',   latency_ms: 95,  provider: 'openai',   model_id: 'gpt-4.1-mini', input_data: { text: '...' }, output_data: { safe: true } },
    { node_id: 'tts',       node_type: 'tts',      display_name: 'TTS Synthesis',   status: 'OK',   latency_ms: 180, input_data: { text: '今天天气晴朗...' }, output_data: { audio_ms: 2400 } },
    { node_id: 'vts',       node_type: 'vts',      display_name: 'VTS Expression',  status: 'OK',   latency_ms: 22,  input_data: { emotion_tag: 'happy' }, output_data: { expression_id: 'smile_01' } },
  ],
  edges: [
    { source: 'intent',   target: 'governor'  },
    { source: 'governor', target: 'memory'    },
    { source: 'governor', target: 'llm_safe'  },
    { source: 'memory',   target: 'llm_main'  },
    { source: 'llm_main', target: 'tts'       },
    { source: 'llm_safe', target: 'tts'       },
    { source: 'tts',      target: 'vts'       },
  ],
}

// ===== 将 API 数据转换为 React Flow 格式 =====
function buildFlowData(traceData, onSelect) {
  const rawNodes = traceData.nodes ?? []
  const rawEdges = traceData.edges ?? []

  const flowNodes = rawNodes.map((n) => ({
    id: n.node_id,
    type: 'customNode',
    position: { x: 0, y: 0 }, // Dagre 会覆盖
    data: { ...n, onSelect },
  }))

  const flowEdges = rawEdges.map((e) => ({
    id: `e-${e.source}-${e.target}`,
    source: e.source,
    target: e.target,
    type: 'smoothstep',
    markerEnd: { type: MarkerType.ArrowClosed, color: '#D0D5DD', width: 16, height: 16 },
    style: { stroke: '#D0D5DD', strokeWidth: 1.5 },
  }))

  return applyDagreLayout(flowNodes, flowEdges)
}

// ===== 甘特图：基于 edges 拓扑推算各节点开始时间 =====
function computeGanttTimeline(traceData) {
  const nodes = traceData.nodes ?? []
  const edges = traceData.edges ?? []

  // 构建入边映射：node_id → [source_node_id, ...]
  const inEdges = {}
  nodes.forEach((n) => { inEdges[n.node_id] = [] })
  edges.forEach((e) => {
    if (inEdges[e.target] !== undefined) {
      inEdges[e.target].push(e.source)
    }
  })

  // 拓扑排序 + 计算最早开始时间（基于前驱节点的 start + latency）
  const latencyMap = {}
  nodes.forEach((n) => { latencyMap[n.node_id] = n.latency_ms ?? 0 })

  const startMap = {}
  const visited = new Set()

  function calcStart(nodeId) {
    if (visited.has(nodeId)) return startMap[nodeId] ?? 0
    visited.add(nodeId)
    const preds = inEdges[nodeId] ?? []
    if (preds.length === 0) {
      startMap[nodeId] = 0
    } else {
      startMap[nodeId] = Math.max(...preds.map((p) => calcStart(p) + (latencyMap[p] ?? 0)))
    }
    return startMap[nodeId]
  }

  nodes.forEach((n) => calcStart(n.node_id))

  const totalMs = Math.max(...nodes.map((n) => (startMap[n.node_id] ?? 0) + (n.latency_ms ?? 0)), 1)

  return nodes.map((n) => ({
    ...n,
    start_ms: startMap[n.node_id] ?? 0,
    end_ms: (startMap[n.node_id] ?? 0) + (n.latency_ms ?? 0),
    total_ms: totalMs,
  }))
}

// ===== 节点类型颜色映射 =====
const NODE_TYPE_COLOR = {
  intent:   '#2970FF',
  governor: '#7A5AF8',
  memory:   '#0BA5EC',
  llm:      '#F79009',
  tts:      '#12B76A',
  vts:      '#EE46BC',
  default:  '#667085',
}

function nodeColor(nodeType) {
  return NODE_TYPE_COLOR[nodeType] ?? NODE_TYPE_COLOR.default
}

function statusBg(status) {
  const s = (status ?? '').toUpperCase()
  if (s === 'OK' || s === 'SUCCESS') return 'bg-green-50 border-green-200'
  if (s === 'FAIL' || s === 'ERROR') return 'bg-red-50 border-red-200'
  if (s === 'DEGRADED') return 'bg-orange-50 border-orange-200'
  return 'bg-gray-50 border-gray-200'
}

// ===== 甘特图组件 =====
function GanttView({ traceData, onSelectNode }) {
  const timeline = computeGanttTimeline(traceData)
  if (timeline.length === 0) return null

  const totalMs = timeline[0]?.total_ms ?? 1

  // 时间轴刻度（5 个刻度）
  const ticks = Array.from({ length: 6 }, (_, i) => Math.round(totalMs * i / 5))

  return (
    <div className="h-full overflow-auto p-4 bg-white">
      {/* 时间轴标题 */}
      <div className="flex items-center justify-between mb-4">
        <span className="text-xs font-medium text-gray-500">节点执行甘特图（总耗时 {totalMs}ms）</span>
        <span className="text-xs text-gray-400">点击行查看节点详情</span>
      </div>

      {/* 时间轴刻度 */}
      <div className="flex mb-2 pl-40">
        {ticks.map((t, i) => (
          <div
            key={i}
            className="flex-1 text-xs text-gray-400 text-center border-l border-gray-200 first:border-l-0"
            style={{ minWidth: 0 }}
          >
            {t}ms
          </div>
        ))}
      </div>

      {/* 甘特条 */}
      <div className="space-y-1.5">
        {timeline.map((node) => {
          const leftPct = (node.start_ms / totalMs) * 100
          const widthPct = Math.max((node.latency_ms ?? 0) / totalMs * 100, 0.5)
          const color = nodeColor(node.node_type)

          return (
            <div
              key={node.node_id}
              className={`flex items-center gap-2 rounded border cursor-pointer hover:shadow-sm transition-shadow ${statusBg(node.status)}`}
              style={{ padding: '4px 8px' }}
              onClick={() => onSelectNode?.(node)}
            >
              {/* 节点名称（固定宽度） */}
              <div className="w-36 shrink-0">
                <div className="text-xs font-medium text-gray-700 truncate" title={node.display_name}>
                  {node.display_name || node.node_id}
                </div>
                <div className="text-xs text-gray-400">{node.latency_ms ?? 0}ms</div>
              </div>

              {/* 甘特条区域 */}
              <div className="flex-1 relative h-6 bg-gray-100 rounded overflow-hidden">
                <div
                  className="absolute top-0 h-full rounded flex items-center justify-end pr-1"
                  style={{
                    left: `${leftPct}%`,
                    width: `${widthPct}%`,
                    backgroundColor: color,
                    opacity: 0.85,
                    minWidth: '4px',
                  }}
                >
                  {widthPct > 8 && (
                    <span className="text-white text-xs font-mono truncate px-1">
                      {node.latency_ms}ms
                    </span>
                  )}
                </div>
              </div>

              {/* 状态标签 */}
              <div className="w-12 shrink-0 text-right">
                <span
                  className={`text-xs font-medium ${
                    (node.status ?? '').toUpperCase() === 'OK' ? 'text-green-600' :
                    (node.status ?? '').toUpperCase() === 'FAIL' ? 'text-red-500' :
                    'text-orange-500'
                  }`}
                >
                  {node.status ?? '—'}
                </span>
              </div>
            </div>
          )
        })}
      </div>

      {/* 图例 */}
      <div className="flex flex-wrap gap-3 mt-4 pt-3 border-t border-gray-100">
        {Object.entries(NODE_TYPE_COLOR).filter(([k]) => k !== 'default').map(([type, color]) => (
          <div key={type} className="flex items-center gap-1.5">
            <div className="w-3 h-3 rounded" style={{ backgroundColor: color }} />
            <span className="text-xs text-gray-500">{type}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ===== 主页面 =====
export default function Trace() {
  const { requestId: paramId } = useParams()
  const storeId = useAppStore((s) => s.selectedRequestId)
  const initialId = paramId ?? storeId ?? ''

  const [inputId, setInputId] = useState(initialId)
  const [nodes, setNodes, onNodesChange] = useNodesState([])
  const [edges, setEdges, onEdgesChange] = useEdgesState([])
  const [selectedNode, setSelectedNode] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [fullscreen, setFullscreen] = useState(false)
  const [traceInfo, setTraceInfo] = useState(null)
  const [traceData, setTraceData] = useState(null)   // 原始 trace 数据（甘特图用）
  const [viewMode, setViewMode] = useState('dag')    // 'dag' | 'gantt'
  const [checkpoints, setCheckpoints] = useState([]) // TODO-42: checkpoint 列表
  const containerRef = useRef(null)

  const handleSelect = useCallback((data) => setSelectedNode(data), [])

  const load = useCallback(async (id) => {
    if (!id?.trim()) return
    setLoading(true)
    setError(null)
    setSelectedNode(null)
    try {
      let data
      if (id === 'mock-req-001' || id.startsWith('mock')) {
        data = MOCK_TRACE
      } else {
        data = await fetchTraceNodes(id)
      }
      setTraceData(data)
      setTraceInfo({ status: data.status, latency: data.total_latency_ms, nodeCount: (data.nodes ?? []).length })
      const { nodes: ln, edges: le } = buildFlowData(data, handleSelect)
      setNodes(ln)
      setEdges(le)
      // TODO-42: 加载与该 request_id 关联的 checkpoint
      try {
        const cpData = await fetchCheckpoints({ limit: 10 })
        const relatedCps = (cpData?.items ?? []).filter(
          (cp) => !cp.request_id || cp.request_id === id
        )
        setCheckpoints(relatedCps)
      } catch {
        setCheckpoints([])
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [setNodes, setEdges, handleSelect])

  useEffect(() => {
    if (initialId) load(initialId)
  }, []) // eslint-disable-line

  const toggleFullscreen = () => {
    if (!fullscreen) {
      containerRef.current?.requestFullscreen?.()
    } else {
      document.exitFullscreen?.()
    }
    setFullscreen(!fullscreen)
  }

  const hasData = !loading && !error && (viewMode === 'dag' ? nodes.length > 0 : traceData != null)

  return (
    <div className="space-y-4">
      {/* 页头 + 搜索栏 */}
      <div className="flex items-center gap-3 flex-wrap">
        <h1 className="text-xl font-semibold text-gray-900 shrink-0">链路可视化</h1>
        <div className="flex items-center gap-2 flex-1 min-w-64">
          <div className="relative flex-1">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              className="w-full pl-8 pr-3 py-1.5 text-sm border border-gray-300 rounded font-mono
                         focus:outline-none focus:ring-2 focus:ring-primary-200 focus:border-primary"
              placeholder="输入 request_id（试试 mock-req-001）…"
              value={inputId}
              onChange={(e) => setInputId(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && load(inputId)}
            />
          </div>
          <button className="btn-primary shrink-0" onClick={() => load(inputId)}>
            查询
          </button>
        </div>

        {/* 链路摘要 */}
        {traceInfo && (
          <div className="flex items-center gap-3 text-xs text-gray-500">
            <span>节点 {traceInfo.nodeCount}</span>
            {traceInfo.latency != null && <span>总耗时 {traceInfo.latency}ms</span>}
            <span className={traceInfo.status === 'OK' ? 'text-green-600' : 'text-red-500'}>
              {traceInfo.status}
            </span>
          </div>
        )}

        {/* 视图切换：DAG / 甘特图 */}
        {traceData && (
          <div className="flex rounded border border-gray-300 overflow-hidden text-sm ml-auto">
            <button
              className={`flex items-center gap-1.5 px-3 py-1.5 transition-colors ${
                viewMode === 'dag'
                  ? 'bg-primary text-white'
                  : 'bg-white text-gray-600 hover:bg-gray-50'
              }`}
              onClick={() => setViewMode('dag')}
              title="DAG 拓扑图"
            >
              <GitBranch size={13} />
              DAG
            </button>
            <button
              className={`flex items-center gap-1.5 px-3 py-1.5 transition-colors ${
                viewMode === 'gantt'
                  ? 'bg-primary text-white'
                  : 'bg-white text-gray-600 hover:bg-gray-50'
              }`}
              onClick={() => setViewMode('gantt')}
              title="时序甘特图"
            >
              <BarChart2 size={13} />
              甘特图
            </button>
          </div>
        )}
      </div>

      {/* 主体区域 */}
      <div
        ref={containerRef}
        className="flex gap-4"
        style={{ height: fullscreen ? '100vh' : 'calc(100vh - 200px)' }}
      >
        {/* 画布区域 */}
        <div className="flex-1 card overflow-hidden relative">
          {/* 全屏按钮（仅 DAG 模式） */}
          {viewMode === 'dag' && (
            <button
              className="absolute top-3 right-3 z-10 btn-secondary p-1.5"
              onClick={toggleFullscreen}
              title={fullscreen ? '退出全屏' : '全屏'}
            >
              {fullscreen ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
            </button>
          )}

          {loading && (
            <div className="h-full flex items-center justify-center text-gray-400 text-sm">
              <RefreshCw size={16} className="animate-spin mr-2" />加载中…
            </div>
          )}
          {error && (
            <div className="h-full flex items-center justify-center text-red-500 text-sm px-8 text-center">
              {error}
            </div>
          )}
          {!loading && !error && !traceData && (
            <div className="h-full flex flex-col items-center justify-center text-gray-400 text-sm gap-2">
              <Search size={32} className="text-gray-200" />
              <span>请输入 request_id 查询链路</span>
              <span className="text-xs text-gray-300">可输入 mock-req-001 查看示例</span>
            </div>
          )}

          {/* DAG 视图 */}
          {!loading && viewMode === 'dag' && nodes.length > 0 && (
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
              maxZoom={2}
            >
              <Background color="#F2F4F7" gap={20} size={1} />
              <Controls showInteractive={false} />
              <MiniMap
                nodeColor={(n) => {
                  const s = n.data?.status ?? ''
                  if (['OK', 'UP', 'success', 'healthy'].includes(s)) return '#12B76A'
                  if (['FAIL', 'DOWN', 'error'].includes(s)) return '#F04438'
                  if (['DEGRADED', 'degraded'].includes(s)) return '#F79009'
                  return '#2970FF'
                }}
                maskColor="rgba(249,250,251,0.7)"
              />
            </ReactFlow>
          )}

          {/* 甘特图视图 */}
          {!loading && viewMode === 'gantt' && traceData && (
            <GanttView
              traceData={traceData}
              onSelectNode={handleSelect}
            />
          )}
        </div>

        {/* 节点详情侧边面板 */}
        {selectedNode && (
          <NodeDetailPanel node={selectedNode} onClose={() => setSelectedNode(null)} />
        )}

        {/* TODO-42: Checkpoint 侧边栏（有 checkpoint 时显示） */}
        {!selectedNode && checkpoints.length > 0 && (
          <div className="w-72 shrink-0 card overflow-hidden flex flex-col">
            <div className="px-4 py-2.5 bg-amber-50 border-b border-amber-200 flex items-center gap-2">
              <Bookmark size={13} className="text-amber-600" />
              <span className="text-xs font-semibold text-amber-700">Checkpoints ({checkpoints.length})</span>
            </div>
            <div className="flex-1 overflow-y-auto divide-y divide-gray-100">
              {checkpoints.map((cp) => (
                <div key={cp.checkpoint_id} className={`p-3 text-xs ${cp.is_expired ? 'opacity-50' : ''}`}>
                  <div className="flex items-center justify-between mb-1">
                    <span className="font-mono text-blue-600 truncate max-w-[140px]" title={cp.checkpoint_id}>
                      {cp.checkpoint_id.slice(0, 16)}…
                    </span>
                    <span className={`px-1.5 py-0.5 rounded border text-xs font-medium ${
                      cp.is_expired
                        ? 'bg-gray-100 text-gray-500 border-gray-200'
                        : 'bg-amber-50 text-amber-700 border-amber-200'
                    }`}>
                      {cp.is_expired ? '已超时' : '等待恢复'}
                    </span>
                  </div>
                  {cp.interrupt_reason && (
                    <div className="text-orange-600 mb-1 truncate" title={cp.interrupt_reason}>
                      中断原因：{cp.interrupt_reason}
                    </div>
                  )}
                  {cp.resume_deadline_at && (
                    <div className="text-gray-400">
                      截止：{new Date(cp.resume_deadline_at).toLocaleString('zh-CN', {
                        month: '2-digit', day: '2-digit',
                        hour: '2-digit', minute: '2-digit',
                      })}
                    </div>
                  )}
                  {cp.payload_summary && (
                    <div className="mt-1 text-gray-400 truncate" title={cp.payload_summary}>
                      {cp.payload_summary}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
