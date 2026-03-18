import React, { useEffect, useState, useCallback, useRef } from 'react'
import { useParams, useNavigate, Routes, Route, NavLink, useLocation } from 'react-router-dom'
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
import {
  Search, Maximize2, Minimize2, RefreshCw, GitBranch, BarChart2,
  Bookmark, Activity, Users, ChevronRight, Layers, Mic, Volume2,
  Video, Brain, Shield, Database, Zap, ArrowRightLeft, Filter,
  CheckCircle, ArrowLeft,
} from 'lucide-react'
import CustomNode from '../components/trace/CustomNode.jsx'
import NodeDetailPanel from '../components/trace/NodeDetailPanel.jsx'
import { applyDagreLayout } from '../utils/traceLayout.js'
import { fetchTraceNodes, fetchTraceStreamEvents, fetchGraphSchema, fetchCheckpoints, fetchChainStats, fetchObsMetrics } from '../api/obs.js'
import useAppStore from '../store/useAppStore.js'

const NODE_TYPES = { customNode: CustomNode }

// ===== 分组定义 =====
const GROUPS = [
  {
    id: 'edge',
    label: 'Edge 端输入',
    desc: 'ASR 语音识别 · 微信监听 · 输入上报',
    icon: Mic,
    color: '#059669',
    bg: '#ECFDF5',
    border: '#6EE7B7',
    nodeIds: ['asr', 'wechat_listener', 'input_reporter'],
  },
  {
    id: 'entry',
    label: '入口链路',
    desc: 'Host Input 路由 · 预分类器 · Governor 鉴权 · Scene Router',
    icon: ArrowRightLeft,
    color: '#475467',
    bg: '#F8FAFC',
    border: '#CBD5E1',
    nodeIds: ['host_input', 'pre_classifier', 'governor', 'scene_router'],
  },
  {
    id: 'recall',
    label: '七路召回',
    desc: '情节/语义/关键词/Bond/Canon/Skill/情感 → Rerank 融合',
    icon: Filter,
    color: '#0891B2',
    bg: '#ECFEFF',
    border: '#67E8F9',
    nodeIds: ['recall_episodic', 'recall_semantic', 'recall_keyword', 'recall_bond', 'recall_canon', 'recall_skill', 'recall_emotion', 'recall_rerank'],
  },
  {
    id: 'agent',
    label: 'Agent 编排',
    desc: 'Supervisor → Dispatch → Chat/Code/Translate/Retrieval → Judge',
    icon: Users,
    color: '#7C3AED',
    bg: '#F5F3FF',
    border: '#C4B5FD',
    nodeIds: ['supervisor_dispatch', 'agent_dispatch', 'chat_agent', 'code_agent', 'translate_agent', 'retrieval_agent', 'judge_agent'],
  },
  {
    id: 'llm',
    label: 'LLM 推理',
    desc: 'LLM 本地 (qwen3:8b) · LLM 云端 (Gemini)',
    icon: Brain,
    color: '#2970FF',
    bg: '#EEF4FF',
    border: '#93C5FD',
    nodeIds: ['llm_local', 'llm_cloud'],
  },
  {
    id: 'postprocess',
    label: '后处理 & 输出',
    desc: 'Airlock 检疫 · 记忆写入 · Bond 更新 · VTS 控制 · TTS · VTS 驱动',
    icon: Zap,
    color: '#D97706',
    bg: '#FFFBEB',
    border: '#FCD34D',
    nodeIds: ['airlock', 'episodic_write', 'bond_update', 'vts_ctrl', 'chat_render', 'tts', 'vts_driver'],
  },
]

// ===== 双端全链路静态图定义 =====
const FULL_CHAIN_SCHEMA = {
  graph_id: 'natsume.full_chain',
  nodes: [
    { node_id: 'asr',              node_type: 'asr',       display_name: 'ASR 语音识别',       layer: 'edge' },
    { node_id: 'wechat_listener',  node_type: 'system',    display_name: '微信监听',            layer: 'edge' },
    { node_id: 'input_reporter',   node_type: 'system',    display_name: '输入上报',            layer: 'edge' },
    { node_id: 'host_input',       node_type: 'router',    display_name: 'Host Input 路由',    layer: 'host' },
    { node_id: 'pre_classifier',   node_type: 'intent',    display_name: '预分类器',            layer: 'host' },
    { node_id: 'governor',         node_type: 'governor',  display_name: 'Governor 鉴权',      layer: 'host' },
    { node_id: 'scene_router',     node_type: 'router',    display_name: 'Scene Router',       layer: 'host' },
    { node_id: 'recall_episodic',  node_type: 'recall',    display_name: '情节记忆召回',        layer: 'host' },
    { node_id: 'recall_semantic',  node_type: 'recall',    display_name: '语义向量召回',        layer: 'host' },
    { node_id: 'recall_keyword',   node_type: 'recall',    display_name: '关键词召回',          layer: 'host' },
    { node_id: 'recall_bond',      node_type: 'recall',    display_name: 'Bond 关系召回',      layer: 'host' },
    { node_id: 'recall_canon',     node_type: 'recall',    display_name: 'Canon 世界观召回',   layer: 'host' },
    { node_id: 'recall_skill',     node_type: 'recall',    display_name: 'Skill 工具召回',     layer: 'host' },
    { node_id: 'recall_emotion',   node_type: 'recall',    display_name: '情感上下文召回',      layer: 'host' },
    { node_id: 'recall_rerank',    node_type: 'retrieval', display_name: 'Rerank 融合',        layer: 'host' },
    { node_id: 'supervisor_dispatch', node_type: 'agent',  display_name: 'Supervisor 分派',   layer: 'host' },
    { node_id: 'agent_dispatch',   node_type: 'agent',     display_name: 'Agent Dispatch',    layer: 'host' },
    { node_id: 'chat_agent',       node_type: 'agent',     display_name: 'Chat Agent',        layer: 'host' },
    { node_id: 'code_agent',       node_type: 'agent',     display_name: 'Code Agent',        layer: 'host' },
    { node_id: 'translate_agent',  node_type: 'agent',     display_name: 'Translate Agent',   layer: 'host' },
    { node_id: 'retrieval_agent',  node_type: 'agent',     display_name: 'Retrieval Agent',   layer: 'host' },
    { node_id: 'judge_agent',      node_type: 'judge',     display_name: 'Judge Agent',       layer: 'host' },
    { node_id: 'llm_local',        node_type: 'llm',       display_name: 'LLM 本地 (qwen3:8b)', layer: 'host', provider: 'openai', model_id: 'qwen3:8b' },
    { node_id: 'llm_cloud',        node_type: 'llm',       display_name: 'LLM 云端 (Gemini)',  layer: 'host', provider: 'gemini', model_id: 'gemini-2.5-flash' },
    { node_id: 'airlock',          node_type: 'governor',  display_name: 'Airlock 检疫',       layer: 'host' },
    { node_id: 'episodic_write',   node_type: 'memory',    display_name: '情节记忆写入',        layer: 'host' },
    { node_id: 'bond_update',      node_type: 'bond',      display_name: 'Bond 积分更新',      layer: 'host' },
    { node_id: 'vts_ctrl',         node_type: 'vts',       display_name: 'VTS 控制',           layer: 'host' },
    { node_id: 'chat_render',      node_type: 'system',    display_name: 'Chat Render 输出',   layer: 'host' },
    { node_id: 'tts',              node_type: 'tts',       display_name: 'TTS 语音合成',       layer: 'edge' },
    { node_id: 'vts_driver',       node_type: 'vts',       display_name: 'VTS 表情驱动',       layer: 'edge' },
  ],
  edges: [
    { source: 'asr',             target: 'host_input'       },
    { source: 'wechat_listener', target: 'host_input'       },
    { source: 'input_reporter',  target: 'host_input'       },
    { source: 'host_input',      target: 'pre_classifier'   },
    { source: 'pre_classifier',  target: 'governor'         },
    { source: 'governor',        target: 'scene_router'     },
    { source: 'scene_router',    target: 'recall_episodic'  },
    { source: 'scene_router',    target: 'recall_semantic'  },
    { source: 'scene_router',    target: 'recall_keyword'   },
    { source: 'scene_router',    target: 'recall_bond'      },
    { source: 'scene_router',    target: 'recall_canon'     },
    { source: 'scene_router',    target: 'recall_skill'     },
    { source: 'scene_router',    target: 'recall_emotion'   },
    { source: 'recall_episodic', target: 'recall_rerank'    },
    { source: 'recall_semantic', target: 'recall_rerank'    },
    { source: 'recall_keyword',  target: 'recall_rerank'    },
    { source: 'recall_bond',     target: 'recall_rerank'    },
    { source: 'recall_canon',    target: 'recall_rerank'    },
    { source: 'recall_skill',    target: 'recall_rerank'    },
    { source: 'recall_emotion',  target: 'recall_rerank'    },
    { source: 'scene_router',    target: 'supervisor_dispatch' },
    { source: 'supervisor_dispatch', target: 'agent_dispatch' },
    { source: 'agent_dispatch',  target: 'chat_agent'       },
    { source: 'agent_dispatch',  target: 'code_agent'       },
    { source: 'agent_dispatch',  target: 'translate_agent'  },
    { source: 'agent_dispatch',  target: 'retrieval_agent'  },
    { source: 'chat_agent',      target: 'judge_agent'      },
    { source: 'code_agent',      target: 'judge_agent'      },
    { source: 'translate_agent', target: 'judge_agent'      },
    { source: 'retrieval_agent', target: 'judge_agent'      },
    { source: 'recall_rerank',   target: 'llm_local'        },
    { source: 'recall_rerank',   target: 'llm_cloud'        },
    { source: 'judge_agent',     target: 'llm_local'        },
    { source: 'judge_agent',     target: 'llm_cloud'        },
    { source: 'llm_local',       target: 'airlock'          },
    { source: 'llm_cloud',       target: 'airlock'          },
    { source: 'airlock',         target: 'episodic_write'   },
    { source: 'airlock',         target: 'bond_update'      },
    { source: 'airlock',         target: 'vts_ctrl'         },
    { source: 'airlock',         target: 'chat_render'      },
    { source: 'chat_render',     target: 'tts'              },
    { source: 'vts_ctrl',        target: 'vts_driver'       },
  ],
}

// Mock trace 数据
const MOCK_TRACE = {
  request_id: 'mock-req-001',
  status: 'OK',
  total_latency_ms: 1580,
  nodes: [
    { node_id: 'asr',             node_type: 'asr',       display_name: 'ASR 语音识别',       status: 'OK', latency_ms: 35,  layer: 'edge' },
    { node_id: 'host_input',      node_type: 'router',    display_name: 'Host Input 路由',    status: 'OK', latency_ms: 5,   layer: 'host' },
    { node_id: 'pre_classifier',  node_type: 'intent',    display_name: '预分类器',            status: 'OK', latency_ms: 8,   layer: 'host' },
    { node_id: 'governor',        node_type: 'governor',  display_name: 'Governor 鉴权',      status: 'OK', latency_ms: 22,  layer: 'host' },
    { node_id: 'scene_router',    node_type: 'router',    display_name: 'Scene Router',       status: 'OK', latency_ms: 6,   layer: 'host' },
    { node_id: 'recall_episodic', node_type: 'recall',    display_name: '情节记忆召回',        status: 'OK', latency_ms: 45,  layer: 'host' },
    { node_id: 'recall_semantic', node_type: 'recall',    display_name: '语义向量召回',        status: 'OK', latency_ms: 120, layer: 'host' },
    { node_id: 'recall_bond',     node_type: 'recall',    display_name: 'Bond 关系召回',      status: 'OK', latency_ms: 18,  layer: 'host' },
    { node_id: 'recall_emotion',  node_type: 'recall',    display_name: '情感上下文召回',      status: 'OK', latency_ms: 12,  layer: 'host' },
    { node_id: 'recall_rerank',   node_type: 'retrieval', display_name: 'Rerank 融合',        status: 'OK', latency_ms: 35,  layer: 'host' },
    { node_id: 'llm_local',       node_type: 'llm',       display_name: 'LLM 本地 (qwen3:8b)', status: 'OK', latency_ms: 820, layer: 'host', provider: 'openai', model_id: 'qwen3:8b' },
    { node_id: 'airlock',         node_type: 'governor',  display_name: 'Airlock 检疫',       status: 'OK', latency_ms: 15,  layer: 'host' },
    { node_id: 'episodic_write',  node_type: 'memory',    display_name: '情节记忆写入',        status: 'OK', latency_ms: 28,  layer: 'host' },
    { node_id: 'bond_update',     node_type: 'bond',      display_name: 'Bond 积分更新',      status: 'OK', latency_ms: 12,  layer: 'host' },
    { node_id: 'vts_ctrl',        node_type: 'vts',       display_name: 'VTS 控制',           status: 'OK', latency_ms: 8,   layer: 'host' },
    { node_id: 'chat_render',     node_type: 'system',    display_name: 'Chat Render 输出',   status: 'OK', latency_ms: 5,   layer: 'host' },
    { node_id: 'tts',             node_type: 'tts',       display_name: 'TTS 语音合成',       status: 'OK', latency_ms: 180, layer: 'edge' },
    { node_id: 'vts_driver',      node_type: 'vts',       display_name: 'VTS 表情驱动',       status: 'OK', latency_ms: 22,  layer: 'edge' },
  ],
  edges: [
    { source: 'asr',             target: 'host_input'       },
    { source: 'host_input',      target: 'pre_classifier'   },
    { source: 'pre_classifier',  target: 'governor'         },
    { source: 'governor',        target: 'scene_router'     },
    { source: 'scene_router',    target: 'recall_episodic'  },
    { source: 'scene_router',    target: 'recall_semantic'  },
    { source: 'scene_router',    target: 'recall_bond'      },
    { source: 'scene_router',    target: 'recall_emotion'   },
    { source: 'recall_episodic', target: 'recall_rerank'    },
    { source: 'recall_semantic', target: 'recall_rerank'    },
    { source: 'recall_bond',     target: 'recall_rerank'    },
    { source: 'recall_emotion',  target: 'recall_rerank'    },
    { source: 'recall_rerank',   target: 'llm_local'        },
    { source: 'llm_local',       target: 'airlock'          },
    { source: 'airlock',         target: 'episodic_write'   },
    { source: 'airlock',         target: 'bond_update'      },
    { source: 'airlock',         target: 'vts_ctrl'         },
    { source: 'airlock',         target: 'chat_render'      },
    { source: 'chat_render',     target: 'tts'              },
    { source: 'vts_ctrl',        target: 'vts_driver'       },
  ],
}

// ===== 构建 Flow 数据 =====
function buildFlowData(schemaNodes, schemaEdges, onSelect, activeNodeIds = null, traceNodeMap = {}) {
  const activeSet = activeNodeIds ? new Set(activeNodeIds) : null

  const flowNodes = schemaNodes.map((n) => {
    const isActive   = activeSet ? activeSet.has(n.node_id) : false
    // activeSet 为 null 表示没有 trace 数据，所有节点正常显示（非 inactive）
    const isInactive = activeSet ? !isActive : false
    const traceNode  = traceNodeMap[n.node_id]
    return {
      id: n.node_id,
      type: 'customNode',
      position: { x: 0, y: 0 },
      data: {
        ...n,
        ...(isActive && traceNode ? traceNode : {}),
        onSelect,
        inactive: isInactive,
        active: isActive,
      },
    }
  })

  const flowEdges = schemaEdges.map((e) => {
    const isActiveEdge = activeSet
      ? activeSet.has(e.source) && activeSet.has(e.target)
      : false
    return {
      id: `e-${e.source}-${e.target}`,
      source: e.source,
      target: e.target,
      type: 'smoothstep',
      animated: isActiveEdge,
      markerEnd: {
        type: MarkerType.ArrowClosed,
        color: isActiveEdge ? '#2970FF' : '#94A3B8',
        width: 16, height: 16,
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

// ===== 甘特图 =====
function computeGanttTimeline(traceData) {
  const nodes = traceData.nodes ?? []
  const edges = traceData.edges ?? []
  const inEdges = {}
  nodes.forEach((n) => { inEdges[n.node_id] = [] })
  edges.forEach((e) => { if (inEdges[e.target] !== undefined) inEdges[e.target].push(e.source) })
  const latencyMap = {}
  nodes.forEach((n) => { latencyMap[n.node_id] = n.latency_ms ?? 0 })
  const startMap = {}
  const visited = new Set()
  function calcStart(nodeId) {
    if (visited.has(nodeId)) return startMap[nodeId] ?? 0
    visited.add(nodeId)
    const preds = inEdges[nodeId] ?? []
    startMap[nodeId] = preds.length === 0
      ? 0
      : Math.max(...preds.map((p) => calcStart(p) + (latencyMap[p] ?? 0)))
    return startMap[nodeId]
  }
  nodes.forEach((n) => calcStart(n.node_id))
  const totalMs = Math.max(...nodes.map((n) => (startMap[n.node_id] ?? 0) + (n.latency_ms ?? 0)), 1)
  return nodes.map((n) => ({ ...n, start_ms: startMap[n.node_id] ?? 0, end_ms: (startMap[n.node_id] ?? 0) + (n.latency_ms ?? 0), total_ms: totalMs }))
}

const NODE_TYPE_COLOR = {
  intent: '#2970FF', governor: '#7A5AF8', memory: '#0BA5EC',
  llm: '#F79009', tts: '#12B76A', vts: '#EE46BC', default: '#667085',
}
function nodeColor(t) { return NODE_TYPE_COLOR[t] ?? NODE_TYPE_COLOR.default }

function GanttView({ traceData, onSelectNode }) {
  const timeline = computeGanttTimeline(traceData)
  const [filter, setFilter] = React.useState('')
  if (timeline.length === 0) return null
  const totalMs = timeline[0]?.total_ms ?? 1
  const filtered = filter.trim()
    ? timeline.filter(n => (n.display_name || n.node_id || '').toLowerCase().includes(filter.toLowerCase()))
    : timeline
  const ticks = Array.from({ length: 6 }, (_, i) => Math.round(totalMs * i / 5))
  return (
    <div className="h-full overflow-auto p-4 bg-white">
      <div className="flex items-center justify-between mb-3 gap-3">
        <span className="text-xs font-medium text-gray-500">节点执行甘特图（总耗时 {totalMs}ms）</span>
        <div className="relative">
          <Search size={11} className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-400" />
          <input className="pl-6 pr-2 py-1 text-xs border border-gray-300 rounded w-36 focus:outline-none focus:ring-1 focus:ring-blue-200"
            placeholder="过滤节点…" value={filter} onChange={e => setFilter(e.target.value)} />
        </div>
      </div>
      <div className="flex mb-2 pl-40">
        {ticks.map((t, i) => (
          <div key={i} className="flex-1 text-xs text-gray-400 text-center border-l border-gray-200 first:border-l-0">{t}ms</div>
        ))}
      </div>
      <div className="space-y-1.5">
        {filtered.map((node) => {
          const leftPct = (node.start_ms / totalMs) * 100
          const widthPct = Math.max((node.latency_ms ?? 0) / totalMs * 100, 0.5)
          const color = nodeColor(node.node_type)
          return (
            <div key={node.node_id}
              className="flex items-center gap-2 rounded border cursor-pointer hover:shadow-sm transition-shadow bg-gray-50 border-gray-200"
              style={{ padding: '4px 8px' }}
              onClick={() => onSelectNode?.(node)}
            >
              <div className="w-36 shrink-0">
                <div className="text-xs font-medium text-gray-700 truncate">{node.display_name || node.node_id}</div>
                <div className="text-xs text-gray-400">{node.latency_ms ?? 0}ms</div>
              </div>
              <div className="flex-1 relative h-6 bg-gray-100 rounded overflow-hidden">
                <div className="absolute top-0 h-full rounded"
                  style={{ left: `${leftPct}%`, width: `${widthPct}%`, backgroundColor: color, opacity: 0.85, minWidth: '4px' }} />
              </div>
              <span className={`text-xs font-medium w-10 text-right shrink-0 ${(node.status ?? '').toUpperCase() === 'OK' ? 'text-green-600' : 'text-red-500'}`}>
                {node.status ?? '—'}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ===== 分组 DAG 子页面 =====
function GroupDagView({ groupId, traceData, onSelectNode }) {
  const group = GROUPS.find(g => g.id === groupId)
  const [nodes, setNodes, onNodesChange] = useNodesState([])
  const [edges, setEdges, onEdgesChange] = useEdgesState([])
  const [selectedNode, setSelectedNode] = useState(null)
  const [viewMode, setViewMode] = useState('dag')
  const navigate = useNavigate()

  const handleSelect = useCallback((data) => {
    setSelectedNode(data)
    onSelectNode?.(data)
  }, [onSelectNode])

  useEffect(() => {
    if (!group) return
    const nodeIdSet = new Set(group.nodeIds)
    // 过滤出该分组的节点和边（包含跨分组的连接边）
    const groupNodes = FULL_CHAIN_SCHEMA.nodes.filter(n => nodeIdSet.has(n.node_id))
    const groupEdges = FULL_CHAIN_SCHEMA.edges.filter(e => nodeIdSet.has(e.source) && nodeIdSet.has(e.target))

    const activeNodeIds = traceData ? (traceData.nodes ?? []).map(n => n.node_id) : null
    const traceNodeMap  = traceData ? Object.fromEntries((traceData.nodes ?? []).map(n => [n.node_id, n])) : {}

    const { nodes: fn, edges: fe } = buildFlowData(groupNodes, groupEdges, handleSelect, activeNodeIds, traceNodeMap)
    setNodes(fn)
    setEdges(fe)
  }, [groupId, traceData, handleSelect, setNodes, setEdges])

  if (!group) return <div className="p-8 text-gray-400">分组不存在</div>

  const Icon = group.icon
  const activeCount = traceData
    ? (traceData.nodes ?? []).filter(n => group.nodeIds.includes(n.node_id)).length
    : 0

  return (
    <div className="flex flex-col h-full flex-1">
      {/* 子页面头部 */}
      <div className="flex items-center gap-3 mb-4 flex-wrap">
        <button
          className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-800 transition-colors"
          onClick={() => navigate('/trace')}
        >
          <ArrowLeft size={14} />
          返回
        </button>
        <div className="flex items-center gap-2">
          <span className="flex items-center justify-center w-8 h-8 rounded-lg"
            style={{ background: group.bg, color: group.color }}>
            <Icon size={16} />
          </span>
          <div>
            <h2 className="text-base font-semibold text-gray-900">{group.label}</h2>
            <p className="text-xs text-gray-500">{group.desc}</p>
          </div>
        </div>
        {traceData && (
          <div className="ml-auto flex items-center gap-2 text-xs text-gray-500">
            <span className="px-2 py-1 rounded-full font-medium"
              style={{ background: group.bg, color: group.color }}>
              {activeCount} / {group.nodeIds.length} 节点激活
            </span>
          </div>
        )}
        {/* 视图切换 */}
        {traceData && (
          <div className="flex rounded border border-gray-300 overflow-hidden text-xs">
            <button className={`flex items-center gap-1 px-2.5 py-1.5 transition-colors ${viewMode === 'dag' ? 'bg-blue-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
              onClick={() => setViewMode('dag')}>
              <GitBranch size={11} /> DAG
            </button>
            <button className={`flex items-center gap-1 px-2.5 py-1.5 transition-colors ${viewMode === 'gantt' ? 'bg-blue-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
              onClick={() => setViewMode('gantt')}>
              <BarChart2 size={11} /> 甘特图
            </button>
          </div>
        )}
      </div>

      {/* 主体 */}
      <div className="flex gap-4 flex-1 min-h-0" style={{ height: 'calc(100vh - 260px)' }}>
        <div className="flex-1 rounded-xl border-2 overflow-hidden h-full"
          style={{ borderColor: group.border, background: '#FAFAFA' }}>
          {viewMode === 'dag' && (
            <ReactFlow
              nodes={nodes} edges={edges}
              onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
              nodeTypes={NODE_TYPES}
              fitView fitViewOptions={{ padding: 0.2 }}
              proOptions={{ hideAttribution: true }}
              minZoom={0.2} maxZoom={3}
            >
              <Background color="#E2E8F0" gap={20} size={1} />
              <Controls showInteractive={false} />
              <MiniMap
                nodeColor={(n) => n.data?.active ? group.color : '#CBD5E1'}
                maskColor="rgba(249,250,251,0.7)"
              />
            </ReactFlow>
          )}
          {viewMode === 'gantt' && traceData && (
            <GanttView
              traceData={{ ...traceData, nodes: (traceData.nodes ?? []).filter(n => group.nodeIds.includes(n.node_id)) }}
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

// ===== 全链路 DAG 子页面 =====
function FullChainView({ traceData, onSelectNode }) {
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

  useEffect(() => {
    const activeNodeIds = traceData ? (traceData.nodes ?? []).map(n => n.node_id) : null
    const traceNodeMap  = traceData ? Object.fromEntries((traceData.nodes ?? []).map(n => [n.node_id, n])) : {}
    const { nodes: fn, edges: fe } = buildFlowData(
      FULL_CHAIN_SCHEMA.nodes, FULL_CHAIN_SCHEMA.edges, handleSelect, activeNodeIds, traceNodeMap
    )
    setNodes(fn)
    setEdges(fe)
  }, [traceData, handleSelect, setNodes, setEdges])

  return (
    <div className="flex flex-col h-full flex-1">
      <div className="flex items-center gap-3 mb-4">
        <button className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-800 transition-colors"
          onClick={() => navigate('/trace')}>
          <ArrowLeft size={14} /> 返回
        </button>
        <div className="flex items-center gap-2">
          <Layers size={16} className="text-blue-600" />
          <h2 className="text-base font-semibold text-gray-900">双端全链路拓扑图</h2>
        </div>
        <div className="flex items-center gap-3 text-xs text-gray-500 ml-auto">
          <div className="flex items-center gap-1.5">
            <div className="w-3 h-3 rounded border-2 border-gray-300" />
            <span>未激活</span>
          </div>
          <div className="flex items-center gap-1.5">
            <div className="w-3 h-3 rounded border-2 border-blue-500" style={{ boxShadow: '0 0 4px rgba(41,112,255,0.4)' }} />
            <span>激活链路</span>
          </div>
        </div>
        <button className="btn-secondary p-1.5" onClick={() => {
          if (!fullscreen) containerRef.current?.requestFullscreen?.()
          else document.exitFullscreen?.()
          setFullscreen(!fullscreen)
        }}>
          {fullscreen ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
        </button>
      </div>
      <div ref={containerRef} className="flex gap-4 flex-1 min-h-0"
        style={{ height: fullscreen ? '100vh' : 'calc(100vh - 240px)' }}>
        <div className="flex-1 card overflow-hidden h-full">
          <ReactFlow
            nodes={nodes} edges={edges}
            onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
            nodeTypes={NODE_TYPES}
            fitView fitViewOptions={{ padding: 0.12 }}
            proOptions={{ hideAttribution: true }}
            minZoom={0.08} maxZoom={2}
          >
            <Background color="#F2F4F7" gap={20} size={1} />
            <Controls showInteractive={false} />
            <MiniMap
              nodeColor={(n) => n.data?.active ? '#2970FF' : n.data?.inactive ? '#CBD5E1' : '#94A3B8'}
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

// ===== Trace 主页：卡片分组概览 =====
function TraceHome({ traceData, setTraceData, traceInfo, setTraceInfo, loading, setLoading, error, setError }) {
  const [inputId, setInputId] = useState('')
  const navigate = useNavigate()

  const load = useCallback(async (id) => {
    if (!id?.trim()) return
    setLoading(true)
    setError(null)
    try {
      let data
      if (id === 'mock-req-001' || id.startsWith('mock')) {
        data = MOCK_TRACE
      } else {
        data = await fetchTraceNodes(id)
      }
      setTraceData(data)
      setTraceInfo({ status: data.status, latency: data.total_latency_ms, nodeCount: (data.nodes ?? []).length })
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [setLoading, setError, setTraceData, setTraceInfo])

  // 计算每个分组的激活节点数
  const groupStats = GROUPS.map(g => {
    const activeCount = traceData
      ? (traceData.nodes ?? []).filter(n => g.nodeIds.includes(n.node_id)).length
      : 0
    const hasError = traceData
      ? (traceData.nodes ?? []).some(n => g.nodeIds.includes(n.node_id) && ['FAIL', 'ERROR', 'DOWN'].includes((n.status ?? '').toUpperCase()))
      : false
    return { ...g, activeCount, hasError }
  })

  return (
    <div className="space-y-5">
      {/* 页头 + 搜索 */}
      <div className="flex items-center gap-3 flex-wrap">
        <h1 className="text-xl font-semibold text-gray-900 shrink-0">链路可视化</h1>
        <div className="flex items-center gap-2 flex-1 min-w-64">
          <div className="relative flex-1">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              className="w-full pl-8 pr-3 py-1.5 text-sm border border-gray-300 rounded font-mono focus:outline-none focus:ring-2 focus:ring-primary-200 focus:border-primary"
              placeholder="输入 request_id 高亮链路（试试 mock-req-001）…"
              value={inputId}
              onChange={(e) => setInputId(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && load(inputId)}
            />
          </div>
          <button className="btn-primary shrink-0" onClick={() => load(inputId)} disabled={loading}>
            {loading ? <RefreshCw size={13} className="animate-spin" /> : '高亮链路'}
          </button>
          {traceData && (
            <button className="btn-secondary shrink-0 text-xs flex items-center gap-1"
              onClick={() => { setTraceData(null); setTraceInfo(null) }}>
              <RefreshCw size={12} /> 重置
            </button>
          )}
        </div>
        {/* 链路摘要 */}
        {traceInfo && (
          <div className="flex items-center gap-3 text-xs text-gray-500">
            <span>激活节点 {traceInfo.nodeCount}</span>
            {traceInfo.latency != null && <span>总耗时 {traceInfo.latency}ms</span>}
            <span className={traceInfo.status === 'OK' ? 'text-green-600 font-medium' : 'text-red-500 font-medium'}>
              {traceInfo.status}
            </span>
          </div>
        )}
        {error && <span className="text-xs text-red-500">{error}</span>}
      </div>

      {/* 分组卡片网格 */}
      <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
        {groupStats.map((g) => {
          const Icon = g.icon
          const isActive = g.activeCount > 0
          return (
            <button
              key={g.id}
              className="text-left rounded-xl border-2 p-4 transition-all hover:shadow-md hover:-translate-y-0.5 active:translate-y-0 cursor-pointer"
              style={{
                borderColor: isActive ? g.color : g.border,
                background: isActive ? g.bg : '#FFFFFF',
                boxShadow: isActive ? `0 0 0 1px ${g.color}22, 0 2px 8px ${g.color}11` : undefined,
              }}
              onClick={() => navigate(`/trace/group/${g.id}`)}
            >
              <div className="flex items-start justify-between mb-3">
                <span className="flex items-center justify-center w-9 h-9 rounded-lg"
                  style={{ background: isActive ? g.color : g.bg, color: isActive ? '#fff' : g.color }}>
                  <Icon size={18} />
                </span>
                <div className="flex items-center gap-1.5">
                  {g.hasError && (
                    <span className="text-xs px-1.5 py-0.5 rounded-full bg-red-50 text-red-600 border border-red-200 font-medium">
                      异常
                    </span>
                  )}
                  {isActive && (
                    <span className="text-xs px-1.5 py-0.5 rounded-full font-medium"
                      style={{ background: g.bg, color: g.color, border: `1px solid ${g.border}` }}>
                      {g.activeCount}/{g.nodeIds.length}
                    </span>
                  )}
                  <ChevronRight size={14} className="text-gray-400" />
                </div>
              </div>
              <div className="font-semibold text-gray-900 text-sm mb-1">{g.label}</div>
              <div className="text-xs text-gray-500 leading-relaxed line-clamp-2">{g.desc}</div>
              {/* 节点激活进度条 */}
              {traceData && (
                <div className="mt-3 h-1 rounded-full bg-gray-100 overflow-hidden">
                  <div className="h-1 rounded-full transition-all"
                    style={{ width: `${(g.activeCount / g.nodeIds.length) * 100}%`, background: g.color }} />
                </div>
              )}
            </button>
          )
        })}
      </div>

      {/* 全链路图入口卡片 */}
      <button
        className="w-full text-left rounded-xl border-2 border-dashed border-gray-300 p-4 hover:border-blue-400 hover:bg-blue-50 transition-all flex items-center gap-4"
        onClick={() => navigate('/trace/full')}
      >
        <span className="flex items-center justify-center w-10 h-10 rounded-lg bg-blue-50 text-blue-600 shrink-0">
          <Layers size={20} />
        </span>
        <div className="flex-1">
          <div className="font-semibold text-gray-800 text-sm">双端全链路拓扑图</div>
          <div className="text-xs text-gray-500 mt-0.5">展示所有 {FULL_CHAIN_SCHEMA.nodes.length} 个节点的完整 DAG，包含 Edge/Host 双端全链路</div>
        </div>
        <ChevronRight size={16} className="text-gray-400 shrink-0" />
      </button>

      {/* 底部统计卡片 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {[
          { label: '总节点数', value: FULL_CHAIN_SCHEMA.nodes.length, color: 'text-blue-600' },
          { label: '总边数', value: FULL_CHAIN_SCHEMA.edges.length, color: 'text-indigo-600' },
          { label: '激活节点', value: traceData ? (traceData.nodes ?? []).length : '—', color: 'text-green-600' },
          { label: '总耗时', value: traceInfo?.latency != null ? `${traceInfo.latency}ms` : '—', color: 'text-orange-500' },
        ].map(({ label, value, color }) => (
          <div key={label} className="card p-4 text-center">
            <div className={`text-2xl font-bold ${color}`}>{value}</div>
            <div className="text-xs text-gray-500 mt-1">{label}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ===== 主导出：Trace 路由容器 =====
export default function Trace() {
  const [traceData, setTraceData] = useState(null)
  const [traceInfo, setTraceInfo] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const sharedProps = { traceData, setTraceData, traceInfo, setTraceInfo, loading, setLoading, error, setError }

  return (
    <Routes>
      <Route index element={<TraceHome {...sharedProps} />} />
      <Route path="full" element={<FullChainView traceData={traceData} />} />
      <Route path="group/:groupId" element={<GroupDagViewWrapper traceData={traceData} />} />
    </Routes>
  )
}

function GroupDagViewWrapper({ traceData }) {
  const { groupId } = useParams()
  return <GroupDagView groupId={groupId} traceData={traceData} />
}
