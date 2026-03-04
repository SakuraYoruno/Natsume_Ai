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
import { Search, Maximize2, Minimize2, RefreshCw } from 'lucide-react'
import CustomNode from '../components/trace/CustomNode.jsx'
import NodeDetailPanel from '../components/trace/NodeDetailPanel.jsx'
import { applyDagreLayout } from '../utils/traceLayout.js'
import { fetchTraceNodes } from '../api/obs.js'
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
      setTraceInfo({ status: data.status, latency: data.total_latency_ms, nodeCount: (data.nodes ?? []).length })
      const { nodes: ln, edges: le } = buildFlowData(data, handleSelect)
      setNodes(ln)
      setEdges(le)
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
      </div>

      {/* 主体区域 */}
      <div
        ref={containerRef}
        className="flex gap-4"
        style={{ height: fullscreen ? '100vh' : 'calc(100vh - 200px)' }}
      >
        {/* React Flow 画布 */}
        <div className="flex-1 card overflow-hidden relative">
          {/* 全屏按钮 */}
          <button
            className="absolute top-3 right-3 z-10 btn-secondary p-1.5"
            onClick={toggleFullscreen}
            title={fullscreen ? '退出全屏' : '全屏'}
          >
            {fullscreen ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
          </button>

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
          {!loading && !error && nodes.length === 0 && (
            <div className="h-full flex flex-col items-center justify-center text-gray-400 text-sm gap-2">
              <Search size={32} className="text-gray-200" />
              <span>请输入 request_id 查询链路</span>
              <span className="text-xs text-gray-300">可输入 mock-req-001 查看示例</span>
            </div>
          )}
          {!loading && nodes.length > 0 && (
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
        </div>

        {/* 节点详情侧边面板 */}
        {selectedNode && (
          <NodeDetailPanel node={selectedNode} onClose={() => setSelectedNode(null)} />
        )}
      </div>
    </div>
  )
}
