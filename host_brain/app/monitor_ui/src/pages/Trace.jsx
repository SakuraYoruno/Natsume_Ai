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
import { Search, Maximize2, Minimize2, RefreshCw, GitBranch, BarChart2, Bookmark, Activity, Users } from 'lucide-react'
import CustomNode from '../components/trace/CustomNode.jsx'
import NodeDetailPanel from '../components/trace/NodeDetailPanel.jsx'
import { applyDagreLayout } from '../utils/traceLayout.js'
import { fetchTraceNodes, fetchTraceStreamEvents, fetchGraphSchema, fetchCheckpoints, fetchChainStats, fetchObsMetrics } from '../api/obs.js'
import useAppStore from '../store/useAppStore.js'

const NODE_TYPES = { customNode: CustomNode }

// ===== 双端全链路静态图定义（含 ASR/七路召回/Agent/TTS/VTS 等所有节点）=====
// 这是系统的完整拓扑，页面加载时直接展示，请求时高亮经过的链路
const FULL_CHAIN_SCHEMA = {
  graph_id: 'natsume.full_chain',
  version: 'v1',
  entry_node: 'asr',
  nodes: [
    // ── Edge 端节点 ──
    { node_id: 'asr',              node_type: 'asr',       display_name: 'ASR 语音识别',       layer: 'edge' },
    { node_id: 'wechat_listener',  node_type: 'system',    display_name: '微信监听',            layer: 'edge' },
    { node_id: 'input_reporter',   node_type: 'system',    display_name: '输入上报',            layer: 'edge' },
    // ── Host 入口 ──
    { node_id: 'host_input',       node_type: 'router',    display_name: 'Host Input 路由',    layer: 'host' },
    { node_id: 'pre_classifier',   node_type: 'intent',    display_name: '预分类器',            layer: 'host' },
    { node_id: 'governor',         node_type: 'governor',  display_name: 'Governor 鉴权',      layer: 'host' },
    { node_id: 'scene_router',     node_type: 'router',    display_name: 'Scene Router',       layer: 'host' },
    // ── 七路召回 ──
    { node_id: 'recall_episodic',  node_type: 'recall',    display_name: '情节记忆召回',        layer: 'host' },
    { node_id: 'recall_semantic',  node_type: 'recall',    display_name: '语义向量召回',        layer: 'host' },
    { node_id: 'recall_keyword',   node_type: 'recall',    display_name: '关键词召回',          layer: 'host' },
    { node_id: 'recall_bond',      node_type: 'recall',    display_name: 'Bond 关系召回',      layer: 'host' },
    { node_id: 'recall_canon',     node_type: 'recall',    display_name: 'Canon 世界观召回',   layer: 'host' },
    { node_id: 'recall_skill',     node_type: 'recall',    display_name: 'Skill 工具召回',     layer: 'host' },
    { node_id: 'recall_emotion',   node_type: 'recall',    display_name: '情感上下文召回',      layer: 'host' },
    { node_id: 'recall_rerank',    node_type: 'retrieval', display_name: 'Rerank 融合',        layer: 'host' },
    // ── 多 Agent 编排 ──
    { node_id: 'supervisor_dispatch', node_type: 'agent',  display_name: 'Supervisor 分派',   layer: 'host' },
    { node_id: 'agent_dispatch',   node_type: 'agent',     display_name: 'Agent Dispatch',    layer: 'host' },
    { node_id: 'chat_agent',       node_type: 'agent',     display_name: 'Chat Agent',        layer: 'host' },
    { node_id: 'code_agent',       node_type: 'agent',     display_name: 'Code Agent',        layer: 'host' },
    { node_id: 'translate_agent',  node_type: 'agent',     display_name: 'Translate Agent',   layer: 'host' },
    { node_id: 'retrieval_agent',  node_type: 'agent',     display_name: 'Retrieval Agent',   layer: 'host' },
    { node_id: 'judge_agent',      node_type: 'judge',     display_name: 'Judge Agent',       layer: 'host' },
    // ── LLM 推理 ──
    { node_id: 'llm_local',        node_type: 'llm',       display_name: 'LLM 本地 (qwen3:8b)', layer: 'host', provider: 'openai', model_id: 'qwen3:8b' },
    { node_id: 'llm_cloud',        node_type: 'llm',       display_name: 'LLM 云端 (Gemini)',  layer: 'host', provider: 'gemini', model_id: 'gemini-2.5-flash' },
    // ── 后处理 ──
    { node_id: 'airlock',          node_type: 'governor',  display_name: 'Airlock 检疫',       layer: 'host' },
    { node_id: 'episodic_write',   node_type: 'memory',    display_name: '情节记忆写入',        layer: 'host' },
    { node_id: 'bond_update',      node_type: 'bond',      display_name: 'Bond 积分更新',      layer: 'host' },
    { node_id: 'vts_ctrl',         node_type: 'vts',       display_name: 'VTS 控制',           layer: 'host' },
    { node_id: 'chat_render',      node_type: 'system',    display_name: 'Chat Render 输出',   layer: 'host' },
    // ── Edge 端输出 ──
    { node_id: 'tts',              node_type: 'tts',       display_name: 'TTS 语音合成',       layer: 'edge' },
    { node_id: 'vts_driver',       node_type: 'vts',       display_name: 'VTS 表情驱动',       layer: 'edge' },
  ],
  edges: [
    // Edge → Host 输入
    { source: 'asr',             target: 'host_input'       },
    { source: 'wechat_listener', target: 'host_input'       },
    { source: 'input_reporter',  target: 'host_input'       },
    // Host 入口链路
    { source: 'host_input',      target: 'pre_classifier'   },
    { source: 'pre_classifier',  target: 'governor'         },
    { source: 'governor',        target: 'scene_router'     },
    // 七路召回（并行）
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
    // 多 Agent 编排
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
    // LLM 推理
    { source: 'recall_rerank',   target: 'llm_local'        },
    { source: 'recall_rerank',   target: 'llm_cloud'        },
    { source: 'judge_agent',     target: 'llm_local'        },
    { source: 'judge_agent',     target: 'llm_cloud'        },
    // 后处理（并行）
    { source: 'llm_local',       target: 'airlock'          },
    { source: 'llm_cloud',       target: 'airlock'          },
    { source: 'airlock',         target: 'episodic_write'   },
    { source: 'airlock',         target: 'bond_update'      },
    { source: 'airlock',         target: 'vts_ctrl'         },
    { source: 'airlock',         target: 'chat_render'      },
    // Host → Edge 输出
    { source: 'chat_render',     target: 'tts'              },
    { source: 'vts_ctrl',        target: 'vts_driver'       },
  ],
}

// ===== Mock 数据（开发阶段）— 扩充为完整双端全链路 =====
const MOCK_TRACE = {
  request_id: 'mock-req-001',
  status: 'OK',
  total_latency_ms: 1580,
  nodes: [
    { node_id: 'asr',             node_type: 'asr',       display_name: 'ASR 语音识别',       status: 'OK', latency_ms: 35,  layer: 'edge', input_data: { audio_ms: 1200 }, output_data: { text: '你好，今天天气怎么样？' } },
    { node_id: 'host_input',      node_type: 'router',    display_name: 'Host Input 路由',    status: 'OK', latency_ms: 5,   layer: 'host', input_data: { source: 'asr' }, output_data: { routed: true } },
    { node_id: 'pre_classifier',  node_type: 'intent',    display_name: '预分类器',            status: 'OK', latency_ms: 8,   layer: 'host', input_data: { text: '你好，今天天气怎么样？' }, output_data: { scene: 'SCENE_CHAT' } },
    { node_id: 'governor',        node_type: 'governor',  display_name: 'Governor 鉴权',      status: 'OK', latency_ms: 22,  layer: 'host', input_data: { intent_id: 'i-001' }, output_data: { decision: 'APPROVE' } },
    { node_id: 'scene_router',    node_type: 'router',    display_name: 'Scene Router',       status: 'OK', latency_ms: 6,   layer: 'host', input_data: { scene: 'SCENE_CHAT' }, output_data: { route: 'chat_fast' } },
    { node_id: 'recall_episodic', node_type: 'recall',    display_name: '情节记忆召回',        status: 'OK', latency_ms: 45,  layer: 'host', input_data: { query: '天气' }, output_data: { hits: 2 } },
    { node_id: 'recall_semantic', node_type: 'recall',    display_name: '语义向量召回',        status: 'OK', latency_ms: 120, layer: 'host', input_data: { query: '天气' }, output_data: { hits: 5 } },
    { node_id: 'recall_bond',     node_type: 'recall',    display_name: 'Bond 关系召回',      status: 'OK', latency_ms: 18,  layer: 'host', input_data: { user_id: 'u-001' }, output_data: { bond_stage: 'FAMILIAR' } },
    { node_id: 'recall_emotion',  node_type: 'recall',    display_name: '情感上下文召回',      status: 'OK', latency_ms: 12,  layer: 'host', input_data: {}, output_data: { emotion_tag: 'NEUTRAL' } },
    { node_id: 'recall_rerank',   node_type: 'retrieval', display_name: 'Rerank 融合',        status: 'OK', latency_ms: 35,  layer: 'host', input_data: { candidates: 7 }, output_data: { top_k: 5 } },
    { node_id: 'llm_local',       node_type: 'llm',       display_name: 'LLM 本地 (qwen3:8b)', status: 'OK', latency_ms: 820, layer: 'host', provider: 'openai', model_id: 'qwen3:8b', input_data: { messages: ['...'] }, output_data: { narrative: '今天天气晴朗...' } },
    { node_id: 'airlock',         node_type: 'governor',  display_name: 'Airlock 检疫',       status: 'OK', latency_ms: 15,  layer: 'host', input_data: { text: '...' }, output_data: { safe: true } },
    { node_id: 'episodic_write',  node_type: 'memory',    display_name: '情节记忆写入',        status: 'OK', latency_ms: 28,  layer: 'host', input_data: { narrative: '...' }, output_data: { written: true } },
    { node_id: 'bond_update',     node_type: 'bond',      display_name: 'Bond 积分更新',      status: 'OK', latency_ms: 12,  layer: 'host', input_data: { event: 'chat' }, output_data: { delta: 1 } },
    { node_id: 'vts_ctrl',        node_type: 'vts',       display_name: 'VTS 控制',           status: 'OK', latency_ms: 8,   layer: 'host', input_data: { emotion_tag: 'happy' }, output_data: { expression: 'smile_01' } },
    { node_id: 'chat_render',     node_type: 'system',    display_name: 'Chat Render 输出',   status: 'OK', latency_ms: 5,   layer: 'host', input_data: {}, output_data: { tokens: 42 } },
    { node_id: 'tts',             node_type: 'tts',       display_name: 'TTS 语音合成',       status: 'OK', latency_ms: 180, layer: 'edge', input_data: { text: '今天天气晴朗...' }, output_data: { audio_ms: 2400 } },
    { node_id: 'vts_driver',      node_type: 'vts',       display_name: 'VTS 表情驱动',       status: 'OK', latency_ms: 22,  layer: 'edge', input_data: { expression_id: 'smile_01' }, output_data: { applied: true } },
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

// ===== 将 API 数据转换为 React Flow 格式 =====
// mode: 'full'   → 全图静态展示（所有节点灰色 inactive）
// mode: 'trace'  → 仅展示 trace 经过的节点（正常彩色）
// mode: 'overlay'→ 全图 + 高亮 trace 链路（inactive + active 叠加）
function buildFlowData(traceData, onSelect, mode = 'trace', activeNodeIds = null) {
  const rawNodes = traceData.nodes ?? []
  const rawEdges = traceData.edges ?? []

  const activeSet = activeNodeIds ? new Set(activeNodeIds) : null

  const flowNodes = rawNodes.map((n) => {
    let inactive = false
    let active = false
    if (mode === 'full') {
      inactive = true
    } else if (mode === 'overlay' && activeSet) {
      inactive = !activeSet.has(n.node_id)
      active   =  activeSet.has(n.node_id)
    }
    return {
      id: n.node_id,
      type: 'customNode',
      position: { x: 0, y: 0 }, // Dagre 会覆盖
      data: { ...n, onSelect, inactive, active },
    }
  })

  const flowEdges = rawEdges.map((e) => {
    // overlay 模式：只有两端节点都激活的边才高亮
    const isActiveEdge = mode === 'overlay' && activeSet
      ? activeSet.has(e.source) && activeSet.has(e.target)
      : mode !== 'full'
    return {
      id: `e-${e.source}-${e.target}`,
      source: e.source,
      target: e.target,
      type: 'smoothstep',
      animated: isActiveEdge,
      markerEnd: {
        type: MarkerType.ArrowClosed,
        color: isActiveEdge ? '#2970FF' : '#D0D5DD',
        width: 16, height: 16,
      },
      style: {
        stroke: isActiveEdge ? '#2970FF' : '#D0D5DD',
        strokeWidth: isActiveEdge ? 2.5 : 1.5,
        opacity: mode === 'full' ? 0.3 : (mode === 'overlay' && !isActiveEdge ? 0.2 : 1),
      },
    }
  })

  return applyDagreLayout(flowNodes, flowEdges)
}

// ===== 从 graphSchema 或 FULL_CHAIN_SCHEMA 构建完整静态图（全灰 inactive）=====
function buildFullChainFlowData(schema, onSelect) {
  const rawNodes = schema.nodes ?? []
  const rawEdges = schema.edges ?? []

  const flowNodes = rawNodes.map((n) => ({
    id: n.node_id,
    type: 'customNode',
    position: { x: 0, y: 0 },
    data: { ...n, onSelect, inactive: true, active: false },
  }))

  const flowEdges = rawEdges.map((e) => ({
    id: `e-${e.source}-${e.target}`,
    source: e.source,
    target: e.target,
    type: 'smoothstep',
    animated: false,
    markerEnd: { type: MarkerType.ArrowClosed, color: '#D0D5DD', width: 14, height: 14 },
    style: { stroke: '#D0D5DD', strokeWidth: 1.2, opacity: 0.4 },
  }))

  return applyDagreLayout(flowNodes, flowEdges)
}

// ===== 将 trace 激活节点叠加到全图上（高亮链路）=====
function applyTraceHighlight(fullNodes, fullEdges, traceData) {
  const activeNodeIds = new Set((traceData.nodes ?? []).map((n) => n.node_id))
  const traceNodeMap  = Object.fromEntries((traceData.nodes ?? []).map((n) => [n.node_id, n]))

  const updatedNodes = fullNodes.map((node) => {
    const isActive = activeNodeIds.has(node.id)
    const traceNode = traceNodeMap[node.id]
    return {
      ...node,
      data: {
        ...node.data,
        // 激活节点：合并 trace 数据（含 latency_ms、status 等）
        ...(isActive && traceNode ? traceNode : {}),
        onSelect: node.data.onSelect,
        inactive: !isActive,
        active:    isActive,
      },
    }
  })

  const updatedEdges = fullEdges.map((edge) => {
    const isActiveEdge = activeNodeIds.has(edge.source) && activeNodeIds.has(edge.target)
    return {
      ...edge,
      animated: isActiveEdge,
      markerEnd: {
        type: MarkerType.ArrowClosed,
        color: isActiveEdge ? '#2970FF' : '#D0D5DD',
        width: 16, height: 16,
      },
      style: {
        stroke: isActiveEdge ? '#2970FF' : '#D0D5DD',
        strokeWidth: isActiveEdge ? 2.5 : 1.2,
        opacity: isActiveEdge ? 1 : 0.2,
      },
    }
  })

  return { nodes: updatedNodes, edges: updatedEdges }
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

  // TODO-91: 节点搜索过滤状态
  const [nodeFilter, setNodeFilter] = React.useState('')

  const totalMs = timeline[0]?.total_ms ?? 1

  // 过滤节点
  const filteredTimeline = nodeFilter.trim()
    ? timeline.filter(n =>
        (n.display_name || n.node_id || '').toLowerCase().includes(nodeFilter.trim().toLowerCase()) ||
        (n.node_type || '').toLowerCase().includes(nodeFilter.trim().toLowerCase())
      )
    : timeline

  // 时间轴刻度（5 个刻度）
  const ticks = Array.from({ length: 6 }, (_, i) => Math.round(totalMs * i / 5))

  return (
    <div className="h-full overflow-auto p-4 bg-white">
      {/* 时间轴标题 + TODO-91 搜索框 */}
      <div className="flex items-center justify-between mb-3 gap-3">
        <span className="text-xs font-medium text-gray-500 shrink-0">节点执行甘特图（总耗时 {totalMs}ms）</span>
        <div className="flex items-center gap-2">
          {/* TODO-91: 节点搜索过滤 */}
          <div className="relative">
            <Search size={11} className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              className="pl-6 pr-2 py-1 text-xs border border-gray-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-200 w-36"
              placeholder="过滤节点名/类型…"
              value={nodeFilter}
              onChange={e => setNodeFilter(e.target.value)}
            />
            {nodeFilter && (
              <button
                className="absolute right-1.5 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                onClick={() => setNodeFilter('')}
              >×</button>
            )}
          </div>
          <span className="text-xs text-gray-400">
            {nodeFilter ? `${filteredTimeline.length}/${timeline.length}` : '点击行查看节点详情'}
          </span>
        </div>
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
        {filteredTimeline.map((node) => {
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

// ===== TODO-83: 节点耗时分布直方图 =====
function ChainStatsPanel() {
  const [data, setData] = React.useState(null)
  const [loading, setLoading] = React.useState(false)

  React.useEffect(() => {
    setLoading(true)
    fetchChainStats({ window_minutes: 1440 })
      .then(setData)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  if (loading) return (
    <div className="card p-4 text-center text-xs text-gray-400">
      <RefreshCw size={12} className="animate-spin inline mr-1" />加载主链路统计…
    </div>
  )
  if (!data) return null

  const nodes = data.node_stats ?? []
  const maxAvg = Math.max(...nodes.map(n => n.avg_latency_ms ?? 0), 1)

  return (
    <div className="card p-4">
      <div className="flex items-center gap-2 mb-4">
        <Activity size={14} className="text-blue-500" />
        <span className="text-xs font-semibold text-gray-700">主链路节点耗时分布（近 24h）</span>
        <span className="ml-auto text-xs text-gray-400">{data.total_requests} 次请求</span>
      </div>
      {/* 全局摘要 */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
        {[
          { label: 'Governor 拦截率', value: data.governor_block_rate != null ? `${data.governor_block_rate}%` : '—', color: data.governor_block_rate > 10 ? 'text-red-500' : 'text-gray-700' },
          { label: '记忆写入次数', value: data.episodic_written_count ?? '—', color: 'text-green-600' },
          { label: 'QA 重写次数', value: data.qa_rewrite_count ?? '—', color: 'text-orange-500' },
          { label: 'Canon 提案次数', value: data.canon_proposal_count ?? '—', color: 'text-indigo-600' },
        ].map(({ label, value, color }) => (
          <div key={label} className="text-center">
            <div className={`text-xl font-bold ${color}`}>{value}</div>
            <div className="text-xs text-gray-400 mt-0.5">{label}</div>
          </div>
        ))}
      </div>
      {/* 节点耗时直方图 */}
      <div className="space-y-2">
        {nodes.filter(n => n.total_calls > 0).map((n) => {
          const avgPct = maxAvg > 0 ? Math.max((n.avg_latency_ms ?? 0) / maxAvg * 100, 1) : 0
          const color = nodeColor(n.node_name.replace('_', ''))
          return (
            <div key={n.node_name} className="flex items-center gap-2">
              <div className="w-32 text-xs text-gray-600 truncate shrink-0" title={n.node_name}>
                {n.node_name}
              </div>
              <div className="flex-1 bg-gray-100 rounded-full h-2 overflow-hidden">
                <div
                  className="h-2 rounded-full"
                  style={{ width: `${avgPct}%`, backgroundColor: color, opacity: 0.75 }}
                />
              </div>
              <div className="w-20 text-xs text-right text-gray-500 shrink-0">
                {n.avg_latency_ms != null ? `avg ${n.avg_latency_ms}ms` : '—'}
              </div>
              {n.degraded_rate > 0 && (
                <div className="text-xs text-orange-500 shrink-0">{n.degraded_rate}% 降级</div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ===== TODO-72: Top 5 最慢节点排行榜 =====
function SlowestNodesPanel({ traceData }) {
  if (!traceData) return null
  const nodes = [...(traceData.nodes ?? [])]
    .filter((n) => n.latency_ms != null)
    .sort((a, b) => (b.latency_ms ?? 0) - (a.latency_ms ?? 0))
    .slice(0, 5)
  if (nodes.length === 0) return null

  const maxMs = nodes[0]?.latency_ms ?? 1

  return (
    <div className="card p-4">
      <div className="text-xs font-semibold text-gray-600 mb-3 flex items-center gap-1.5">
        <BarChart2 size={13} className="text-orange-500" />
        Top 5 最慢节点
      </div>
      <div className="space-y-2">
        {nodes.map((n, i) => {
          const pct = Math.max((n.latency_ms / maxMs) * 100, 2)
          const color = nodeColor(n.node_type)
          return (
            <div key={n.node_id} className="flex items-center gap-2">
              <div className="w-4 text-xs text-gray-400 text-right shrink-0">{i + 1}</div>
              <div className="w-28 text-xs text-gray-700 truncate shrink-0" title={n.display_name || n.node_id}>
                {n.display_name || n.node_id}
              </div>
              <div className="flex-1 bg-gray-100 rounded-full h-2 overflow-hidden">
                <div
                  className="h-2 rounded-full"
                  style={{ width: `${pct}%`, backgroundColor: color, opacity: 0.8 }}
                />
              </div>
              <div className="w-16 text-xs text-right font-mono text-gray-600 shrink-0">
                {n.latency_ms}ms
              </div>
            </div>
          )
        })}
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
  const [streamEvents, setStreamEvents] = useState(null) // Chat Stream SSE 事件流
  const [graphSchema, setGraphSchema] = useState(null)   // 图编排（节点/边定义）
  const [agentMetrics, setAgentMetrics] = useState(null) // Agent 编排（选派 / handoff）
  // 全图静态节点/边（页面加载时立即构建，不依赖 trace 查询）
  const [fullChainNodes, setFullChainNodes] = useState([])
  const [fullChainEdges, setFullChainEdges] = useState([])
  const containerRef = useRef(null)

  const handleSelect = useCallback((data) => setSelectedNode(data), [])

  // ===== 构建全图静态拓扑（页面加载时立即展示）=====
  const buildFullChain = useCallback((schema) => {
    const { nodes: fn, edges: fe } = buildFullChainFlowData(schema, handleSelect)
    setFullChainNodes(fn)
    setFullChainEdges(fe)
    // 初始状态：DAG 展示全图（全灰）
    setNodes(fn)
    setEdges(fe)
  }, [handleSelect, setNodes, setEdges])

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

      // 将 trace 激活节点高亮叠加到全图上（overlay 模式）
      if (fullChainNodes.length > 0) {
        const { nodes: hn, edges: he } = applyTraceHighlight(fullChainNodes, fullChainEdges, data)
        setNodes(hn)
        setEdges(he)
      } else {
        // 全图尚未加载时，退回到仅展示 trace 节点
        const { nodes: ln, edges: le } = buildFlowData(data, handleSelect, 'trace')
        setNodes(ln)
        setEdges(le)
      }

      setStreamEvents(null)
      if (id !== 'mock-req-001' && !id.startsWith('mock')) {
        fetchTraceStreamEvents(id).then(setStreamEvents).catch(() => setStreamEvents({ events: [], count: 0 }))
      }
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
  }, [setNodes, setEdges, handleSelect, fullChainNodes, fullChainEdges])

  useEffect(() => {
    if (initialId) load(initialId)
  }, []) // eslint-disable-line

  // 页面加载时立即用 FULL_CHAIN_SCHEMA 构建全图（不等待 graphSchema API）
  useEffect(() => {
    buildFullChain(FULL_CHAIN_SCHEMA)
  }, []) // eslint-disable-line

  // graphSchema API 加载成功后，用真实图定义更新全图（若 API 返回了更多节点）
  useEffect(() => {
    fetchGraphSchema().then((schema) => {
      setGraphSchema(schema)
      // 若 API 返回的节点数比 FULL_CHAIN_SCHEMA 多，则用 API 数据重建全图
      // 否则保持 FULL_CHAIN_SCHEMA（更完整）
      if (schema && (schema.nodes?.length ?? 0) > FULL_CHAIN_SCHEMA.nodes.length) {
        buildFullChain(schema)
      }
    }).catch(() => setGraphSchema(null))
  }, []) // eslint-disable-line

  useEffect(() => {
    fetchObsMetrics().then((d) => setAgentMetrics(d?.agent_metrics ?? null)).catch(() => setAgentMetrics(null))
  }, [])

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
              placeholder="输入 request_id 高亮链路（试试 mock-req-001）…"
              value={inputId}
              onChange={(e) => setInputId(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && load(inputId)}
            />
          </div>
          <button className="btn-primary shrink-0" onClick={() => load(inputId)}>
            高亮链路
          </button>
          {/* 重置全图：清除高亮，恢复全灰静态图 */}
          {traceData && (
            <button
              className="btn-secondary shrink-0 text-xs"
              onClick={() => {
                setTraceData(null)
                setTraceInfo(null)
                setStreamEvents(null)
                setNodes(fullChainNodes)
                setEdges(fullChainEdges)
                setSelectedNode(null)
              }}
              title="清除高亮，恢复全图静态展示"
            >
              <RefreshCw size={12} />
              重置
            </button>
          )}
        </div>

        {/* 图例 */}
        <div className="flex items-center gap-3 text-xs text-gray-500 shrink-0">
          <div className="flex items-center gap-1.5">
            <div className="w-3 h-3 rounded border-2 border-gray-300 opacity-50" />
            <span>未激活节点</span>
          </div>
          <div className="flex items-center gap-1.5">
            <div className="w-3 h-3 rounded border-2 border-blue-500" style={{ boxShadow: '0 0 4px rgba(41,112,255,0.4)' }} />
            <span>激活链路</span>
          </div>
          <div className="flex items-center gap-1.5">
            <div className="w-3 h-3 rounded" style={{ background: '#ECFDF5', border: '1px solid #059669' }} />
            <span className="text-green-600">Edge 端</span>
          </div>
          <div className="flex items-center gap-1.5">
            <div className="w-3 h-3 rounded" style={{ background: '#EEF4FF', border: '1px solid #2970FF' }} />
            <span className="text-blue-600">Host 端</span>
          </div>
        </div>

        {/* 链路摘要 */}
        {traceInfo && (
          <div className="flex items-center gap-3 text-xs text-gray-500">
            <span>激活节点 {traceInfo.nodeCount}</span>
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
          {/* 全图已展示时不再显示空状态提示 */}
          {!loading && !error && nodes.length === 0 && (
            <div className="h-full flex flex-col items-center justify-center text-gray-400 text-sm gap-2">
              <Search size={32} className="text-gray-200" />
              <span>加载全链路拓扑图中…</span>
            </div>
          )}

          {/* DAG 视图：全图始终展示，trace 查询后高亮链路 */}
          {!loading && viewMode === 'dag' && nodes.length > 0 && (
            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              nodeTypes={NODE_TYPES}
              fitView
              fitViewOptions={{ padding: 0.15 }}
              proOptions={{ hideAttribution: true }}
              minZoom={0.1}
              maxZoom={2}
            >
              <Background color="#F2F4F7" gap={20} size={1} />
              <Controls showInteractive={false} />
              <MiniMap
                nodeColor={(n) => {
                  if (n.data?.inactive) return '#E4E7EC'
                  if (n.data?.active) return '#2970FF'
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

      {/* Chat Stream SSE 事件流 + Agent 编排可视化 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* SSE 事件流：当前 request 的 stream 事件 */}
        <div className="card overflow-hidden flex flex-col">
          <div className="px-4 py-2.5 bg-slate-50 border-b border-slate-200 flex items-center gap-2">
            <Activity size={14} className="text-slate-600" />
            <span className="text-sm font-semibold text-slate-700">Chat Stream SSE 事件流</span>
            {streamEvents != null && (
              <span className="text-xs text-slate-500">共 {streamEvents.count ?? streamEvents.events?.length ?? 0} 条</span>
            )}
          </div>
          <div className="flex-1 overflow-y-auto max-h-64 min-h-[120px]">
            {streamEvents == null && traceData && !inputId.startsWith('mock') && (
              <div className="p-4 text-center text-gray-400 text-sm">加载 SSE 事件中…</div>
            )}
            {streamEvents != null && (!streamEvents.events?.length) && (
              <div className="p-4 text-center text-gray-400 text-sm">该请求暂无 SSE 事件记录</div>
            )}
            {streamEvents?.events?.length > 0 && (
              <ul className="divide-y divide-gray-100 text-xs">
                {streamEvents.events.map((ev, i) => (
                  <li key={i} className="px-4 py-2 flex flex-wrap gap-x-3 gap-y-1">
                    <span className="font-mono text-gray-500">{ev.ts_ms != null ? `${ev.ts_ms}ms` : '-'}</span>
                    <span className="text-blue-600">{ev.phase ?? '-'}</span>
                    <span className="text-amber-600">{ev.event_type ?? '-'}</span>
                    {ev.node_id && <span className="text-gray-700">node: {ev.node_id}</span>}
                    {ev.chunk != null && ev.chunk !== '' && (
                      <span className="text-gray-500 truncate max-w-[200px]" title={String(ev.chunk)}>
                        {String(ev.chunk).slice(0, 40)}{String(ev.chunk).length > 40 ? '…' : ''}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
            {!traceData && (
              <div className="p-4 text-center text-gray-400 text-sm">输入 request_id 高亮链路后显示该请求的 SSE 事件</div>
            )}
          </div>
        </div>

        {/* Agent 编排可视化：所有 agent 节点 + 选派统计 */}
        <div className="card overflow-hidden flex flex-col">
          <div className="px-4 py-2.5 bg-slate-50 border-b border-slate-200 flex items-center gap-2">
            <Users size={14} className="text-slate-600" />
            <span className="text-sm font-semibold text-slate-700">Agent 编排</span>
            {agentMetrics && (
              <span className="text-xs text-slate-500">
                Handoff {agentMetrics.agent_hop_count ?? 0} 次 · 均 {agentMetrics.avg_hops_per_request ?? '—'} hop/请求
              </span>
            )}
          </div>
          <div className="flex-1 overflow-y-auto max-h-64 min-h-[120px] p-4">
            {/* Agent 节点拓扑（静态展示所有 agent，激活时高亮） */}
            <div className="mb-3">
              <div className="text-xs font-medium text-gray-500 mb-2">Agent 节点（全部）</div>
              <div className="flex flex-wrap gap-2">
                {[
                  { id: 'supervisor_dispatch', label: 'Supervisor', color: '#7C3AED', bg: '#F5F3FF' },
                  { id: 'agent_dispatch',      label: 'Dispatch',   color: '#0891B2', bg: '#ECFEFF' },
                  { id: 'chat_agent',          label: 'Chat',       color: '#059669', bg: '#ECFDF5' },
                  { id: 'code_agent',          label: 'Code',       color: '#2970FF', bg: '#EEF4FF' },
                  { id: 'translate_agent',     label: 'Translate',  color: '#D97706', bg: '#FFFBEB' },
                  { id: 'retrieval_agent',     label: 'Retrieval',  color: '#0891B2', bg: '#ECFEFF' },
                  { id: 'judge_agent',         label: 'Judge',      color: '#059669', bg: '#ECFDF5' },
                ].map(({ id, label, color, bg }) => {
                  // 判断该 agent 是否在当前 trace 中被激活
                  const isActive = traceData?.nodes?.some((n) => n.node_id === id)
                  const dispatchCount = agentMetrics?.agent_dispatch_count?.[id]
                  return (
                    <div
                      key={id}
                      className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border-2 text-xs font-medium transition-all"
                      style={{
                        borderColor: isActive ? color : '#E4E7EC',
                        background: isActive ? bg : '#F9FAFB',
                        color: isActive ? color : '#98A2B3',
                        opacity: isActive ? 1 : 0.6,
                        boxShadow: isActive ? `0 0 0 2px ${color}22` : undefined,
                      }}
                    >
                      <div
                        className="w-2 h-2 rounded-full"
                        style={{ background: isActive ? color : '#D0D5DD' }}
                      />
                      {label}
                      {dispatchCount != null && (
                        <span className="ml-1 text-xs opacity-70">{dispatchCount}</span>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>

            {/* 选派统计 */}
            {agentMetrics?.agent_dispatch_count && Object.keys(agentMetrics.agent_dispatch_count).length > 0 ? (
              <div className="space-y-1.5 border-t border-gray-100 pt-3">
                <div className="text-xs font-medium text-gray-500">各 Agent 历史选派次数（近 24h）</div>
                {Object.entries(agentMetrics.agent_dispatch_count)
                  .sort((a, b) => b[1] - a[1])
                  .map(([agentId, count]) => {
                    const maxCount = Math.max(...Object.values(agentMetrics.agent_dispatch_count))
                    const pct = maxCount > 0 ? (count / maxCount) * 100 : 0
                    return (
                      <div key={agentId} className="flex items-center gap-2">
                        <div className="w-28 text-xs text-gray-600 truncate shrink-0">{agentId}</div>
                        <div className="flex-1 bg-gray-100 rounded-full h-1.5 overflow-hidden">
                          <div
                            className="h-1.5 rounded-full bg-indigo-400"
                            style={{ width: `${pct}%` }}
                          />
                        </div>
                        <div className="w-8 text-xs text-right text-gray-500 shrink-0">{count}</div>
                      </div>
                    )
                  })}
              </div>
            ) : !agentMetrics ? (
              <div className="text-center text-gray-400 text-sm border-t border-gray-100 pt-3">加载 Agent 指标中…</div>
            ) : (
              <div className="text-sm text-gray-400 border-t border-gray-100 pt-3">暂无 Agent 选派数据</div>
            )}
          </div>
        </div>
      </div>

      {/* TODO-72: Top 5 最慢节点排行榜（有 traceData 时显示） */}
      {traceData && <SlowestNodesPanel traceData={traceData} />}

      {/* TODO-83: 主链路节点耗时分布直方图（始终显示，独立于 traceData） */}
      <ChainStatsPanel />
    </div>
  )
}
