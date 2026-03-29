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
  CheckCircle, ArrowLeft, Clock3,
} from 'lucide-react'
import CustomNode from '../components/trace/CustomNode.jsx'
import NodeDetailPanel from '../components/trace/NodeDetailPanel.jsx'
import StatusBadge from '../components/StatusBadge.jsx'
import TraceRequestDetail from './TraceRequestDetail.jsx'
import { applyDagreLayout } from '../utils/traceLayout.js'
import { connectTraceLiveFeed, fetchTraceNodes, fetchTraceStreamEvents, fetchGraphSchema, fetchCheckpoints, fetchChainStats, fetchObsMetrics, fetchSessions } from '../api/obs.js'
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
    traceNodeIds: ['input_reporter'],
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
    traceNodeIds: ['host_input', 'governor'],
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
    traceNodeIds: ['retrieval_embedding', 'recall_fusion', 'retrieval_rerank', 'retrieval_evidence_pack', 'retrieval_pipeline'],
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
    traceNodeIds: ['llm_gateway'],
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
    traceNodeIds: ['airlock', 'episodic_store', 'bond_update', 'audit_snapshot', 'tts', 'vts_driver'],
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
function getTraceGroupNodeIds(group) {
  return group?.traceNodeIds ?? group?.nodeIds ?? []
}

function normalizeTraceGroupNodeId(nodeId = '') {
  if (nodeId.startsWith('voice_')) return 'input_reporter'
  if (nodeId.startsWith('episodic_store')) return 'episodic_store'
  if (nodeId.startsWith('bond_update')) return 'bond_update'
  return nodeId
}

function getTraceGroupState(group, traceData) {
  if (!group || !traceData) {
    return {
      groupNodes: [],
      groupEdges: [],
      activeNodeIds: [],
      activeCount: 0,
      traceNodeMap: {},
    }
  }

  const allowedIds = new Set(getTraceGroupNodeIds(group))
  const groupNodes = (traceData.nodes ?? []).filter((node) => allowedIds.has(normalizeTraceGroupNodeId(node.node_id)))
  const groupNodeIdSet = new Set(groupNodes.map((node) => node.node_id))
  const groupEdges = (traceData.edges ?? []).filter((edge) => groupNodeIdSet.has(edge.source) && groupNodeIdSet.has(edge.target))
  const activeNodeIds = [...new Set(groupNodes.map((node) => normalizeTraceGroupNodeId(node.node_id)))]

  return {
    groupNodes,
    groupEdges,
    activeNodeIds,
    activeCount: activeNodeIds.length,
    traceNodeMap: Object.fromEntries(groupNodes.map((node) => [node.node_id, node])),
  }
}

function getObservedTraceSchema(traceData) {
  if (!traceData) return FULL_CHAIN_SCHEMA
  return {
    nodes: (traceData.nodes ?? []).map((node) => ({
      ...node,
      layer: node.layer ?? node.side ?? 'host',
    })),
    edges: traceData.edges ?? [],
  }
}

const LIVE_TRACE_GROUP_ORDER = ['edge', 'entry', 'recall', 'agent', 'llm', 'postprocess']

function normalizeBadgeStatus(status) {
  const value = String(status || '').toUpperCase()
  if (['OK', 'UP', 'SUCCESS'].includes(value)) return 'healthy'
  if (['RUNNING', 'STARTED', 'ACCEPTED', 'DISPATCHED'].includes(value)) return 'running'
  if (['DEGRADED'].includes(value)) return 'degraded'
  if (['FAIL', 'FAILED', 'ERROR', 'DOWN', 'REJECTED'].includes(value)) return 'error'
  return 'unknown'
}

function getLiveTraceStageLabel(groupId) {
  return {
    edge: '输入',
    entry: '入口',
    recall: '召回',
    agent: 'Agent',
    llm: '推理',
    postprocess: '输出',
  }[groupId] ?? groupId
}

function getLatestTraceNode(traceData) {
  const nodes = Array.isArray(traceData?.nodes) ? [...traceData.nodes] : []
  if (nodes.length === 0) return null
  nodes.sort((a, b) => (a?.ts_ms ?? 0) - (b?.ts_ms ?? 0))
  return nodes[nodes.length - 1]
}

function getLiveTraceFlow(traceData) {
  const latestNode = getLatestTraceNode(traceData)
  const latestNormalizedNodeId = latestNode ? normalizeTraceGroupNodeId(latestNode.node_id) : null

  return LIVE_TRACE_GROUP_ORDER
    .map((groupId) => GROUPS.find((group) => group.id === groupId))
    .filter(Boolean)
    .map((group) => {
      const traceGroupState = getTraceGroupState(group, traceData)
      const hasError = traceGroupState.groupNodes.some((node) => ['FAIL', 'ERROR', 'DOWN'].includes(String(node.status || '').toUpperCase()))
      const isCurrent = latestNormalizedNodeId ? getTraceGroupNodeIds(group).includes(latestNormalizedNodeId) : false
      let status = 'idle'
      if (traceGroupState.activeCount > 0) {
        status = hasError ? 'error' : (isCurrent ? 'running' : 'done')
      }
      return {
        groupId: group.id,
        label: getLiveTraceStageLabel(group.id),
        count: traceGroupState.activeCount,
        total: Math.max(getTraceGroupNodeIds(group).length, 1),
        status,
      }
    })
}

function getLiveTraceStageClasses(stage, group) {
  if (stage.status === 'error') {
    return 'border-red-200 bg-red-50 text-red-600'
  }
  if (stage.status === 'running') {
    return 'border-blue-300 bg-blue-50 text-blue-700'
  }
  if (stage.status === 'done') {
    return ''
  }
  return 'border-gray-200 bg-gray-50 text-gray-400'
}

function getLiveTraceOutputPreview(traceData) {
  const summary = traceData?.summary ?? {}
  const output = summary.output_text
  if (typeof output === 'string' && output.trim()) return output.trim()
  if (typeof summary.failure_event === 'string' && summary.failure_event.trim()) return summary.failure_event.trim()
  const latestNode = getLatestTraceNode(traceData)
  if (typeof latestNode?.reason_code === 'string' && latestNode.reason_code.trim()) return latestNode.reason_code.trim()
  if (typeof latestNode?.error === 'string' && latestNode.error.trim()) return latestNode.error.trim()
  return ''
}

function getLiveTraceCurrentLabel(traceData) {
  const latestNode = getLatestTraceNode(traceData)
  if (!latestNode) return '等待节点上报'
  const status = String(latestNode.status || '').toUpperCase()
  return `${latestNode.display_name || latestNode.node_id} · ${status || 'UNKNOWN'}`
}

function buildLiveTraceMetrics(session, traceData) {
  const flow = traceData ? getLiveTraceFlow(traceData) : []
  return {
    flow,
    currentLabel: traceData ? getLiveTraceCurrentLabel(traceData) : '等待链路详情',
    outputPreview: traceData ? getLiveTraceOutputPreview(traceData) : '',
    nodeCount: traceData?.node_count ?? 0,
    latencyMs: traceData?.total_latency_ms ?? session?.total_latency_ms ?? null,
    status: traceData?.status ?? session?.status ?? 'UNKNOWN',
  }
}

function truncateLiveTraceText(value, maxLength = 140) {
  if (typeof value !== 'string') return ''
  const normalized = value.trim().replace(/\s+/g, ' ')
  if (!normalized) return ''
  return normalized.length > maxLength ? `${normalized.slice(0, maxLength - 1)}…` : normalized
}

function pickLiveTracePreviewValue(value) {
  if (typeof value === 'string') return truncateLiveTraceText(value)
  if (value == null) return ''
  if (Array.isArray(value)) {
    for (const item of value) {
      const preview = pickLiveTracePreviewValue(item)
      if (preview) return preview
    }
    return ''
  }
  if (typeof value === 'object') {
    const candidates = [
      value.output_text,
      value.text,
      value.message,
      value.content,
      value.detail,
      value.error,
      value.reason,
      value.reason_code,
      value.label,
      value.summary,
      value.value,
    ]
    for (const candidate of candidates) {
      const preview = pickLiveTracePreviewValue(candidate)
      if (preview) return preview
    }
    try {
      return truncateLiveTraceText(JSON.stringify(value))
    } catch {
      return ''
    }
  }
  return truncateLiveTraceText(String(value))
}

function extractLiveTraceNodePreview(node) {
  if (!node) return ''
  const candidates = [
    node.reason_code,
    node.error,
    pickLiveTracePreviewValue(node.output_data),
    pickLiveTracePreviewValue(node.input_data),
  ]
  return candidates.find((candidate) => typeof candidate === 'string' && candidate.trim()) || ''
}

function buildSnapshotTimelineEntries(requestId, traceData) {
  const latestNodes = Array.isArray(traceData?.nodes)
    ? [...traceData.nodes].sort((a, b) => (b?.ts_ms ?? 0) - (a?.ts_ms ?? 0)).slice(0, 8)
    : []
  return latestNodes.map((node, index) => ({
    entryId: `snapshot:${requestId}:${node.node_id}:${node.ts_ms ?? index}:${node.status ?? 'UNKNOWN'}`,
    changeType: 'snapshot',
    label: '快照',
    nodeId: node.node_id,
    displayName: node.display_name || node.node_id,
    status: node.status,
    previousStatus: null,
    tsMs: node.ts_ms ?? null,
    preview: extractLiveTraceNodePreview(node),
  }))
}

function buildDeltaTimelineEntries(deltaItem) {
  const requestId = deltaItem?.request_id || deltaItem?.session?.request_id
  const changes = Array.isArray(deltaItem?.changes)
    ? [...deltaItem.changes].sort((a, b) => (b?.node?.ts_ms ?? 0) - (a?.node?.ts_ms ?? 0))
    : []
  return changes.map((change, index) => {
    const node = change?.node ?? {}
    const changeType = change?.change_type || 'updated'
    return {
      entryId: `${changeType}:${requestId}:${node.node_id}:${node.ts_ms ?? index}:${node.status ?? 'UNKNOWN'}`,
      changeType,
      label: changeType === 'added' ? '新增' : '更新',
      nodeId: node.node_id,
      displayName: node.display_name || node.node_id,
      status: node.status,
      previousStatus: change?.previous_status || null,
      tsMs: node.ts_ms ?? null,
      preview: extractLiveTraceNodePreview(node),
    }
  })
}

function mergeLiveTraceTimelineEntries(previous, deltaItems) {
  const next = { ...previous }
  for (const deltaItem of deltaItems) {
    const requestId = deltaItem?.request_id || deltaItem?.session?.request_id
    if (!requestId) continue
    const incomingEntries = buildDeltaTimelineEntries(deltaItem)
    if (incomingEntries.length === 0) continue
    const mergedEntries = [...incomingEntries, ...(next[requestId] ?? [])]
    const dedupedEntries = []
    const seen = new Set()
    for (const entry of mergedEntries) {
      if (!entry?.entryId || seen.has(entry.entryId)) continue
      seen.add(entry.entryId)
      dedupedEntries.push(entry)
      if (dedupedEntries.length >= 18) break
    }
    next[requestId] = dedupedEntries
  }
  return next
}

function getLiveTraceTimelineEntries(item, timelineEntries) {
  if (Array.isArray(timelineEntries) && timelineEntries.length > 0) {
    return timelineEntries
  }
  const requestId = item?.session?.request_id || item?.trace?.request_id || 'trace'
  return buildSnapshotTimelineEntries(requestId, item?.trace)
}

function getLiveTraceChangeBadgeClass(changeType) {
  if (changeType === 'added') return 'border-emerald-200 bg-emerald-50 text-emerald-700'
  if (changeType === 'updated') return 'border-blue-200 bg-blue-50 text-blue-700'
  return 'border-gray-200 bg-gray-50 text-gray-500'
}

function LiveTraceCard({ item, selected, onSelect, onOpenDetail }) {
  const { session, trace } = item
  const metrics = buildLiveTraceMetrics(session, trace)
  const inputPreview = session?.input_preview || trace?.summary?.input_text || session?.request_id

  return (
    <div
      className={`rounded-xl border p-4 transition-colors ${selected ? 'border-blue-300 bg-blue-50/60' : 'border-gray-200 bg-white hover:bg-gray-50'}`}
    >
      <div className="flex items-start gap-3">
        <button className="min-w-0 flex-1 text-left" onClick={onSelect}>
          <div className="text-sm font-semibold text-gray-900 break-all">{inputPreview}</div>
          <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-gray-500">
            <span className="font-mono">{session.request_id.slice(0, 18)}...</span>
            {session.session_id ? <span>{session.session_id}</span> : null}
            <span>{formatRecentTraceTime(session.ts_ms)}</span>
            {metrics.latencyMs != null ? <span>{metrics.latencyMs}ms</span> : null}
          </div>
        </button>
        <div className="shrink-0 flex items-center gap-2">
          <StatusBadge status={normalizeBadgeStatus(metrics.status)} label={getRecentRequestStatusMeta(metrics.status).label} />
          <button className="btn-secondary text-xs" onClick={onOpenDetail}>
            详情
          </button>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        {metrics.flow.map((stage) => {
          const group = GROUPS.find((itemGroup) => itemGroup.id === stage.groupId)
          const className = getLiveTraceStageClasses(stage, group)
          const style = stage.status === 'done' && group
            ? { background: group.bg, color: group.color, borderColor: group.border }
            : undefined
          return (
            <span
              key={`${session.request_id}-${stage.groupId}`}
              className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium ${className}`}
              style={style}
            >
              {stage.label}
              {stage.count > 0 ? `${stage.count}/${stage.total}` : '0'}
            </span>
          )
        })}
      </div>

      <button className="mt-3 block w-full text-left" onClick={onSelect}>
        <div className="text-xs font-medium text-gray-500">当前节点</div>
        <div className="mt-1 text-sm text-gray-700 break-all">{metrics.currentLabel}</div>
        <div className="mt-2 text-xs font-medium text-gray-500">结果摘要</div>
        <div className="mt-1 text-sm text-gray-600 break-all">
          {metrics.outputPreview || '暂无输出，链路仍在推进或尚未落盘。'}
        </div>
      </button>
    </div>
  )
}

function LiveTracePreview({ item, timelineEntries = [], onHighlight, onOpenDetail }) {
  if (!item) {
    return (
      <div className="rounded-xl border border-gray-200 bg-white p-5">
        <div className="text-sm font-semibold text-gray-900">活跃预览</div>
        <div className="mt-3 text-sm text-gray-400">等待最近请求载入。</div>
      </div>
    )
  }

  const { session, trace } = item
  const metrics = buildLiveTraceMetrics(session, trace)
  const summary = trace?.summary ?? {}
  const recentTimelineEntries = getLiveTraceTimelineEntries(item, timelineEntries)

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-5 space-y-4">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-gray-900 break-all">{session.input_preview || summary.input_text || session.request_id}</div>
          <div className="mt-1 text-xs text-gray-500 font-mono break-all">{session.request_id}</div>
        </div>
        <StatusBadge status={normalizeBadgeStatus(metrics.status)} label={getRecentRequestStatusMeta(metrics.status).label} />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-lg border border-gray-200 bg-gray-50 p-3">
          <div className="text-xs font-medium text-gray-500">当前节点</div>
          <div className="mt-1 text-sm text-gray-800 break-all">{metrics.currentLabel}</div>
        </div>
        <div className="rounded-lg border border-gray-200 bg-gray-50 p-3">
          <div className="text-xs font-medium text-gray-500">链路规模</div>
          <div className="mt-1 text-sm text-gray-800">{metrics.nodeCount} nodes</div>
          <div className="mt-1 text-xs text-gray-500">{metrics.latencyMs != null ? `${metrics.latencyMs}ms` : '等待耗时落盘'}</div>
        </div>
      </div>

      <div>
        <div className="text-xs font-medium text-gray-500">阶段进度</div>
        <div className="mt-2 flex flex-wrap gap-2">
          {metrics.flow.map((stage) => {
            const group = GROUPS.find((itemGroup) => itemGroup.id === stage.groupId)
            const className = getLiveTraceStageClasses(stage, group)
            const style = stage.status === 'done' && group
              ? { background: group.bg, color: group.color, borderColor: group.border }
              : undefined
            return (
              <span
                key={`preview-${session.request_id}-${stage.groupId}`}
                className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium ${className}`}
                style={style}
              >
                {stage.label}
                {stage.count > 0 ? `${stage.count}/${stage.total}` : '0'}
              </span>
            )
          })}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3">
        <div>
          <div className="text-xs font-medium text-gray-500">输入</div>
          <div className="mt-1 rounded-lg border border-gray-200 bg-gray-50 p-3 text-sm text-gray-700 break-all">
            {summary.input_text || session.input_preview || session.request_id}
          </div>
        </div>
        <div>
          <div className="text-xs font-medium text-gray-500">输出</div>
          <div className="mt-1 rounded-lg border border-gray-200 bg-gray-50 p-3 text-sm text-gray-700 break-all">
            {metrics.outputPreview || '暂无输出，可能仍在执行或本次未产出文本。'}
          </div>
        </div>
      </div>

      <div>
        <div className="text-xs font-medium text-gray-500">最近节点</div>
        <div className="mt-2 space-y-2">
          {latestNodes.length === 0 ? (
            <div className="text-sm text-gray-400">暂无节点详情。</div>
          ) : latestNodes.map((node) => (
            <div key={`${session.request_id}-${node.node_id}-${node.ts_ms}`} className="rounded-lg border border-gray-200 px-3 py-2">
              <div className="flex items-center gap-2">
                <div className="text-sm font-medium text-gray-800">{node.display_name || node.node_id}</div>
                <StatusBadge status={normalizeBadgeStatus(node.status)} />
                <div className="ml-auto text-xs text-gray-400">{formatRecentTraceTime(node.ts_ms)}</div>
              </div>
              <div className="mt-1 text-xs text-gray-500 break-all">{node.node_id}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="flex items-center gap-2">
        <button className="btn-secondary text-xs" onClick={onHighlight}>显示总览图</button>
        <button className="btn-primary text-xs" onClick={onOpenDetail}>打开详情</button>
      </div>
    </div>
  )
}

function LiveTraceStreamPreview({ item, timelineEntries = [], onHighlight, onOpenDetail }) {
  if (!item) {
    return (
      <div className="rounded-xl border border-gray-200 bg-white p-5">
        <div className="text-sm font-semibold text-gray-900">活跃预览</div>
        <div className="mt-3 text-sm text-gray-400">等待最近请求载入。</div>
      </div>
    )
  }

  const { session, trace } = item
  const metrics = buildLiveTraceMetrics(session, trace)
  const summary = trace?.summary ?? {}
  const recentTimelineEntries = getLiveTraceTimelineEntries(item, timelineEntries)

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-5 space-y-4">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-gray-900 break-all">{session.input_preview || summary.input_text || session.request_id}</div>
          <div className="mt-1 text-xs text-gray-500 font-mono break-all">{session.request_id}</div>
        </div>
        <StatusBadge status={normalizeBadgeStatus(metrics.status)} label={getRecentRequestStatusMeta(metrics.status).label} />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-lg border border-gray-200 bg-gray-50 p-3">
          <div className="text-xs font-medium text-gray-500">当前节点</div>
          <div className="mt-1 text-sm text-gray-800 break-all">{metrics.currentLabel}</div>
        </div>
        <div className="rounded-lg border border-gray-200 bg-gray-50 p-3">
          <div className="text-xs font-medium text-gray-500">链路规模</div>
          <div className="mt-1 text-sm text-gray-800">{metrics.nodeCount} nodes</div>
          <div className="mt-1 text-xs text-gray-500">{metrics.latencyMs != null ? `${metrics.latencyMs}ms` : '等待耗时落盘'}</div>
        </div>
      </div>

      <div>
        <div className="text-xs font-medium text-gray-500">阶段进度</div>
        <div className="mt-2 flex flex-wrap gap-2">
          {metrics.flow.map((stage) => {
            const group = GROUPS.find((itemGroup) => itemGroup.id === stage.groupId)
            const className = getLiveTraceStageClasses(stage, group)
            const style = stage.status === 'done' && group
              ? { background: group.bg, color: group.color, borderColor: group.border }
              : undefined
            return (
              <span
                key={`preview-stream-${session.request_id}-${stage.groupId}`}
                className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium ${className}`}
                style={style}
              >
                {stage.label}
                {stage.count > 0 ? `${stage.count}/${stage.total}` : '0'}
              </span>
            )
          })}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3">
        <div>
          <div className="text-xs font-medium text-gray-500">输入</div>
          <div className="mt-1 rounded-lg border border-gray-200 bg-gray-50 p-3 text-sm text-gray-700 break-all">
            {summary.input_text || session.input_preview || session.request_id}
          </div>
        </div>
        <div>
          <div className="text-xs font-medium text-gray-500">输出</div>
          <div className="mt-1 rounded-lg border border-gray-200 bg-gray-50 p-3 text-sm text-gray-700 break-all">
            {metrics.outputPreview || '暂无输出，可能仍在执行或这次没有文本结果。'}
          </div>
        </div>
      </div>

      <div>
        <div className="text-xs font-medium text-gray-500">最新节点变化</div>
        <div className="mt-2 space-y-2">
          {recentTimelineEntries.length === 0 ? (
            <div className="text-sm text-gray-400">暂无节点变化。</div>
          ) : recentTimelineEntries.map((entry) => (
            <div key={entry.entryId} className="rounded-lg border border-gray-200 px-3 py-2">
              <div className="flex items-center gap-2">
                <div className="text-sm font-medium text-gray-800">{entry.displayName || entry.nodeId}</div>
                <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${getLiveTraceChangeBadgeClass(entry.changeType)}`}>
                  {entry.label}
                </span>
                <StatusBadge status={normalizeBadgeStatus(entry.status)} />
                <div className="ml-auto text-xs text-gray-400">{formatRecentTraceTime(entry.tsMs)}</div>
              </div>
              <div className="mt-1 text-xs text-gray-500 break-all">{entry.nodeId}</div>
              {entry.previousStatus && entry.previousStatus !== entry.status ? (
                <div className="mt-1 text-xs text-gray-500">
                  {entry.previousStatus} -&gt; {entry.status}
                </div>
              ) : null}
              {entry.preview ? (
                <div className="mt-1 text-xs text-gray-600 break-all">{entry.preview}</div>
              ) : null}
            </div>
          ))}
        </div>
      </div>

      <div className="flex items-center gap-2">
        <button className="btn-secondary text-xs" onClick={onHighlight}>显示总览图</button>
        <button className="btn-primary text-xs" onClick={onOpenDetail}>打开详情</button>
      </div>
    </div>
  )
}

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
      : FULL_CHAIN_SCHEMA.nodes.filter(n => nodeIdSet.has(n.node_id))
    const groupEdges = hasObservedTrace
      ? traceGroupState.groupEdges
      : FULL_CHAIN_SCHEMA.edges.filter(e => nodeIdSet.has(e.source) && nodeIdSet.has(e.target))

    const activeNodeIds = hasObservedTrace
      ? traceGroupState.groupNodes.map((node) => node.node_id)
      : (traceData ? (traceData.nodes ?? []).map(n => n.node_id) : null)
    const traceNodeMap  = hasObservedTrace
      ? traceGroupState.traceNodeMap
      : (traceData ? Object.fromEntries((traceData.nodes ?? []).map(n => [n.node_id, n])) : {})

    const { nodes: fn, edges: fe } = buildFlowData(groupNodes, groupEdges, handleSelect, activeNodeIds, traceNodeMap)
    setNodes(fn)
    setEdges(fe)
  }, [groupId, traceData, handleSelect, setNodes, setEdges])

  if (!group) return <div className="p-8 text-gray-400">分组不存在</div>

  const Icon = group.icon
  const activeCount = traceGroupState ? traceGroupState.activeCount : 0
  const totalNodeCount = traceData ? getTraceGroupNodeIds(group).length : group.nodeIds.length

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
              {activeCount} / {totalNodeCount} 节点激活
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
    const schema = getObservedTraceSchema(traceData)
    const activeNodeIds = traceData ? schema.nodes.map((node) => node.node_id) : null
    const traceNodeMap  = traceData ? Object.fromEntries(schema.nodes.map((node) => [node.node_id, node])) : {}
    const { nodes: fn, edges: fe } = buildFlowData(
      schema.nodes, schema.edges, handleSelect, activeNodeIds, traceNodeMap
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
function InlineFullChainOverview({ traceData, requestId, onOpenDetail, onOpenStandalone, onHide }) {
  const [nodes, setNodes, onNodesChange] = useNodesState([])
  const [edges, setEdges, onEdgesChange] = useEdgesState([])
  const [selectedNode, setSelectedNode] = useState(null)

  const handleSelect = useCallback((data) => {
    setSelectedNode(data)
  }, [])

  useEffect(() => {
    const schema = getObservedTraceSchema(traceData)
    const activeNodeIds = traceData ? schema.nodes.map((node) => node.node_id) : null
    const traceNodeMap = traceData ? Object.fromEntries(schema.nodes.map((node) => [node.node_id, node])) : {}
    const { nodes: fn, edges: fe } = buildFlowData(
      schema.nodes,
      schema.edges,
      handleSelect,
      activeNodeIds,
      traceNodeMap,
    )
    setNodes(fn)
    setEdges(fe)
  }, [traceData, handleSelect, setNodes, setEdges])

  if (!traceData) return null

  const currentStatus = String(traceData.status || '').toUpperCase()
  const currentHealthy = ['OK', 'UP', 'SUCCESS'].includes(currentStatus)
  const flow = getLiveTraceFlow(traceData)
  const nodeCount = (traceData.nodes ?? []).length
  const edgeCount = (traceData.edges ?? []).length

  return (
    <div className="rounded-2xl border border-gray-200 bg-white p-4 md:p-5 shadow-sm shadow-slate-100/60">
      <div className="flex items-start gap-3 flex-wrap">
        <div className="flex items-center gap-2 min-w-0">
          <span className="flex items-center justify-center w-9 h-9 rounded-xl bg-blue-600 text-white shadow-sm">
            <Layers size={18} />
          </span>
          <div>
            <h2 className="text-base font-semibold text-gray-900">当前请求总览图</h2>
            <p className="text-xs text-gray-500">
              高亮后直接在首页看完整链路，不再需要先点分组卡片再看效果。
            </p>
          </div>
        </div>
        <div className="ml-auto flex items-center gap-2 flex-wrap">
          <span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-medium ${currentHealthy ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-amber-200 bg-amber-50 text-amber-700'}`}>
            {currentStatus || 'UNKNOWN'}
          </span>
          <span className="inline-flex items-center rounded-full border border-gray-200 bg-gray-50 px-2.5 py-1 text-xs text-gray-600">
            {nodeCount} nodes
          </span>
          <span className="inline-flex items-center rounded-full border border-gray-200 bg-gray-50 px-2.5 py-1 text-xs text-gray-600">
            {edgeCount} edges
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

      <div className="mt-4 flex flex-wrap gap-2">
        {flow.map((stage) => {
          const group = GROUPS.find((itemGroup) => itemGroup.id === stage.groupId)
          const className = getLiveTraceStageClasses(stage, group)
          const style = stage.status === 'done' && group
            ? { background: group.bg, color: group.color, borderColor: group.border }
            : undefined
          return (
            <span
              key={`overview-${traceData.request_id}-${stage.groupId}`}
              className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium ${className}`}
              style={style}
            >
              {stage.label}
              {stage.count > 0 ? `${stage.count}/${stage.total}` : '0'}
            </span>
          )
        })}
      </div>

      <div className="mt-4 grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_22rem] gap-4">
        <div className="rounded-xl border border-gray-200 overflow-hidden bg-gray-50" style={{ height: '34rem' }}>
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
              nodeColor={(n) => n.data?.active ? '#2970FF' : n.data?.inactive ? '#CBD5E1' : '#94A3B8'}
              maskColor="rgba(249,250,251,0.7)"
            />
          </ReactFlow>
        </div>

        <div className="space-y-4">
          <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
            <div className="text-xs font-medium text-gray-500">当前请求</div>
            <div className="mt-1 text-sm font-semibold text-gray-900 break-all">
              {traceData.summary?.input_text || requestId || traceData.request_id}
            </div>
            <div className="mt-2 text-xs text-gray-500 font-mono break-all">{traceData.request_id}</div>
            <div className="mt-3 grid grid-cols-2 gap-3 text-xs">
              <div>
                <div className="text-gray-400">输入来源</div>
                <div className="mt-1 text-gray-700">{traceData.summary?.source || 'GATEWAY/ASR'}</div>
              </div>
              <div>
                <div className="text-gray-400">最终状态</div>
                <div className={`mt-1 font-medium ${currentHealthy ? 'text-green-600' : 'text-amber-600'}`}>{currentStatus || 'UNKNOWN'}</div>
              </div>
            </div>
          </div>

          {selectedNode ? (
            <NodeDetailPanel node={selectedNode} onClose={() => setSelectedNode(null)} />
          ) : (
            <div className="rounded-xl border border-dashed border-gray-300 bg-white p-5 text-sm text-gray-400">
              点击左侧节点，直接查看该节点的输入、输出和状态。
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function TraceHome({ traceData, setTraceData, traceInfo, setTraceInfo, loading, setLoading, error, setError }) {
  const [inputId, setInputId] = useState('')
  const [recentRequests, setRecentRequests] = useState([])
  const [recentLoading, setRecentLoading] = useState(false)
  const [recentKeyword, setRecentKeyword] = useState('')
  const [historyExpanded, setHistoryExpanded] = useState(false)
  const [inlineOverviewVisible, setInlineOverviewVisible] = useState(false)
  const [advancedViewsExpanded, setAdvancedViewsExpanded] = useState(false)
  const [liveTraceItems, setLiveTraceItems] = useState([])
  const [liveTraceTimelineEntries, setLiveTraceTimelineEntries] = useState({})
  const [liveTraceLoading, setLiveTraceLoading] = useState(false)
  const [liveTraceError, setLiveTraceError] = useState(null)
  const [liveFeedConnected, setLiveFeedConnected] = useState(false)
  const [liveFeedLastTs, setLiveFeedLastTs] = useState(null)
  const inlineOverviewRef = useRef(null)
  const navigate = useNavigate()
  const pollingEnabled = useAppStore((s) => s.pollingEnabled)
  const pollingInterval = useAppStore((s) => s.pollingInterval)
  const selectedRequestId = useAppStore((s) => s.selectedRequestId)
  const setSelectedRequestId = useAppStore((s) => s.setSelectedRequestId)

  const load = useCallback(async (id, options = {}) => {
    if (!id?.trim()) return
    const showOverview = options.showOverview ?? true
    setLoading(true)
    setError(null)
    try {
      let data
      if (id === 'mock-req-001' || id.startsWith('mock')) {
        data = MOCK_TRACE
      } else {
        data = await fetchTraceNodes(id)
      }
      setSelectedRequestId(id)
      setTraceData(data)
      setTraceInfo({ status: data.status, latency: data.total_latency_ms, nodeCount: (data.nodes ?? []).length })
      if (showOverview) {
        setInlineOverviewVisible(true)
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [setLoading, setError, setSelectedRequestId, setTraceData, setTraceInfo])

  const loadRecentRequests = useCallback(async (keyword = '') => {
    setRecentLoading(true)
    try {
      const data = await fetchSessions({
        page: 1,
        page_size: 12,
        time_range: '24h',
        keyword: keyword || undefined,
      })
      setRecentRequests(data.items ?? [])
      setRecentKeyword(keyword)
    } finally {
      setRecentLoading(false)
    }
  }, [])

  useEffect(() => {
    loadRecentRequests()
  }, [loadRecentRequests])

  useEffect(() => {
    if (!pollingEnabled) return
    const timer = setInterval(() => loadRecentRequests(recentKeyword), pollingInterval * 1000)
    return () => clearInterval(timer)
  }, [pollingEnabled, pollingInterval, loadRecentRequests, recentKeyword])

  const liveTraceLimit = recentKeyword ? 12 : 6

  const loadLiveTraces = useCallback(async () => {
    const candidates = recentRequests.slice(0, liveTraceLimit)
    if (candidates.length === 0) {
      setLiveTraceItems([])
      setLiveTraceError(null)
      return
    }

    setLiveTraceLoading(true)
    setLiveTraceError(null)
    try {
      const items = await Promise.all(
        candidates.map(async (session) => {
          try {
            const trace = session.request_id === 'mock-req-001'
              ? MOCK_TRACE
              : await fetchTraceNodes(session.request_id)
            return { session, trace }
          } catch (loadError) {
            return { session, trace: null, error: loadError.message }
          }
        })
      )
      setLiveTraceItems(items)
    } catch (loadError) {
      setLiveTraceItems([])
      setLiveTraceError(loadError.message)
    } finally {
      setLiveTraceLoading(false)
    }
  }, [recentRequests, liveTraceLimit])

  useEffect(() => {
    if (!pollingEnabled) {
      setLiveFeedConnected(false)
      return undefined
    }

    let closed = false
    let connection
    setLiveTraceLoading(true)

    try {
      connection = connectTraceLiveFeed(
        {
          limit: liveTraceLimit,
          window_minutes: 1440,
          interval_ms: 1000,
          keyword: recentKeyword || undefined,
        },
        {
          onOpen: () => {
            if (closed) return
            setLiveFeedConnected(true)
            setLiveTraceError(null)
          },
          onSnapshot: (payload) => {
            if (closed) return
            const items = Array.isArray(payload?.items) ? payload.items : []
            setLiveFeedConnected(true)
            setLiveFeedLastTs(payload?.timestamp ?? Date.now())
            setLiveTraceItems(items)
            setLiveTraceTimelineEntries((previous) => {
              const activeRequestIds = new Set(items.map((item) => item?.session?.request_id).filter(Boolean))
              if (activeRequestIds.size === 0) return {}
              const next = {}
              for (const [requestId, entries] of Object.entries(previous)) {
                if (activeRequestIds.has(requestId)) {
                  next[requestId] = entries
                }
              }
              return next
            })
            setLiveTraceLoading(false)
            setLiveTraceError(null)
            if (!recentKeyword) {
              const streamedSessions = items.map((item) => item.session).filter(Boolean)
              setRecentRequests((previous) => {
                const previousById = new Map(previous.map((item) => [item.request_id, item]))
                const merged = streamedSessions.map((session) => ({
                  ...(previousById.get(session.request_id) ?? {}),
                  ...session,
                }))
                previous.forEach((session) => {
                  if (!merged.some((item) => item.request_id === session.request_id)) {
                    merged.push(session)
                  }
                })
                return merged.slice(0, 12)
              })
            }
          },
          onDelta: (payload) => {
            if (closed) return
            const items = Array.isArray(payload?.items) ? payload.items : []
            if (items.length === 0) return
            setLiveFeedConnected(true)
            setLiveFeedLastTs(payload?.timestamp ?? Date.now())
            setLiveTraceTimelineEntries((previous) => mergeLiveTraceTimelineEntries(previous, items))
          },
          onHeartbeat: (payload) => {
            if (closed) return
            setLiveFeedConnected(true)
            setLiveFeedLastTs(payload?.timestamp ?? Date.now())
          },
          onError: (payload) => {
            if (closed) return
            setLiveTraceError(payload?.detail || 'trace live feed error')
          },
          onConnectionError: () => {
            if (closed) return
            setLiveFeedConnected(false)
          },
        }
      )
    } catch (connectionError) {
      setLiveFeedConnected(false)
      setLiveTraceError(connectionError.message)
      setLiveTraceLoading(false)
      return undefined
    }

    return () => {
      closed = true
      connection?.close?.()
    }
  }, [pollingEnabled, recentKeyword, liveTraceLimit])

  useEffect(() => {
    if (liveFeedConnected) return
    loadLiveTraces()
  }, [liveFeedConnected, loadLiveTraces])

  const revealInlineOverview = useCallback(() => {
    setInlineOverviewVisible(true)
    requestAnimationFrame(() => {
      inlineOverviewRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
  }, [])

  const applyTraceSelection = useCallback((item, options = {}) => {
    if (!item?.session?.request_id || !item.trace) return false
    setSelectedRequestId(item.session.request_id)
    setTraceData(item.trace)
    setTraceInfo({
      status: item.trace.status,
      latency: item.trace.total_latency_ms,
      nodeCount: (item.trace.nodes ?? []).length,
    })
    if (options.showOverview ?? true) {
      revealInlineOverview()
    }
    return true
  }, [setSelectedRequestId, setTraceData, setTraceInfo, revealInlineOverview])

  useEffect(() => {
    if (!liveTraceItems.length) return
    const matchedItem = liveTraceItems.find((item) => item.session.request_id === selectedRequestId)
    const nextItem = matchedItem ?? (!selectedRequestId ? liveTraceItems[0] : null)
    if (!nextItem?.trace) return

    if (traceData?.request_id === nextItem.trace.request_id && traceData?.timestamp === nextItem.trace.timestamp) {
      return
    }

    applyTraceSelection(nextItem, { showOverview: false })
  }, [applyTraceSelection, liveTraceItems, selectedRequestId, traceData])

  useEffect(() => {
    if (!inlineOverviewVisible || !traceData?.request_id) return undefined
    const frame = requestAnimationFrame(() => {
      inlineOverviewRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
    return () => cancelAnimationFrame(frame)
  }, [inlineOverviewVisible, traceData?.request_id])

  // 计算每个分组的激活节点数
  const groupStats = GROUPS.map(g => {
    const traceGroupState = traceData ? getTraceGroupState(g, traceData) : null
    const activeCount = traceGroupState ? traceGroupState.activeCount : 0
    const totalNodeCount = traceData ? getTraceGroupNodeIds(g).length : g.nodeIds.length
    const hasError = traceGroupState
      ? traceGroupState.groupNodes.some(n => ['FAIL', 'ERROR', 'DOWN'].includes((n.status ?? '').toUpperCase()))
      : false
    return { ...g, activeCount, totalNodeCount, hasError }
  })

  const openRequestTrace = (requestId) => {
    setSelectedRequestId(requestId)
    navigate(`/trace/${requestId}`)
  }

  const latestRequest = recentRequests[0] ?? null
  const searchValue = inputId.trim()
  const searchLooksLikeRequestId = searchValue.startsWith('req_') || searchValue.startsWith('mock')

  const handleTraceSearch = () => {
    const value = searchValue
    if (!value) {
      setHistoryExpanded(false)
      loadRecentRequests('')
      return
    }
    if (searchLooksLikeRequestId) {
      load(value)
      return
    }
    setHistoryExpanded(false)
    loadRecentRequests(value)
  }

  const currentTraceStatus = String(traceInfo?.status || '').toUpperCase()
  const currentTraceHealthy = ['OK', 'UP', 'SUCCESS'].includes(currentTraceStatus)
  const liveFeedStatusClass = !pollingEnabled
    ? 'border-gray-200 bg-white text-gray-500'
    : liveFeedConnected
      ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
      : 'border-amber-200 bg-amber-50 text-amber-700'
  const liveFeedStatusText = !pollingEnabled
    ? '实时流已暂停'
    : liveFeedConnected
      ? 'SSE 实时流已连接'
      : '实时流重连中，当前降级轮询'
  const selectedLiveTraceItem = liveTraceItems.find((item) => item.session.request_id === selectedRequestId)
    ?? liveTraceItems[0]
    ?? null
  const selectedLiveTraceTimelineEntries = selectedLiveTraceItem?.session?.request_id
    ? (liveTraceTimelineEntries[selectedLiveTraceItem.session.request_id] ?? [])
    : []
  const isHistorySearchMode = Boolean(recentKeyword.trim())
  const shouldAutoShowHistory = !isHistorySearchMode && (!liveFeedConnected || liveTraceItems.length === 0 || Boolean(liveTraceError))
  const showHistoryPanel = !isHistorySearchMode && (historyExpanded || shouldAutoShowHistory)
  const activeFeedTitle = isHistorySearchMode ? '历史请求结果' : '实时活跃链路流'
  const activeFeedDescription = isHistorySearchMode
    ? `按 "${recentKeyword}" 匹配到的请求会直接在这里展示，方便先筛选再点进单条详情。`
    : '首页只保留当前活跃链路，先看现在在跑什么，再点进单条请求追踪。'
  const activeListTitle = isHistorySearchMode ? '历史结果列表' : '活跃请求流'
  const activeListDescription = isHistorySearchMode
    ? '搜索命中的请求结果会替换活跃流列表，避免同层再放一份最近请求。'
    : '优先展示最近活跃的链路进度、当前节点和输出摘要。'
  const activeCountLabel = isHistorySearchMode ? '条搜索结果' : '条活跃请求'
  const historySectionHint = shouldAutoShowHistory
    ? '当前没有活跃链路，自动回退展示历史请求。'
    : '历史请求默认收起；需要回看刚才跑过什么时，再从这里展开。'
  const canCollapseHistory = historyExpanded && !shouldAutoShowHistory

  const focusRequestOverview = useCallback((requestId) => {
    if (!requestId) return
    if (traceData?.request_id === requestId) {
      setSelectedRequestId(requestId)
      revealInlineOverview()
      return
    }
    const matchedItem = liveTraceItems.find((item) => item.session.request_id === requestId)
    if (matchedItem?.trace) {
      applyTraceSelection(matchedItem, { showOverview: true })
      return
    }
    load(requestId, { showOverview: true })
  }, [applyTraceSelection, liveTraceItems, load, revealInlineOverview, setSelectedRequestId, traceData])

  const focusLiveTraceItem = useCallback((item, options = {}) => {
    if (!item?.session?.request_id) return
    if (!item.trace) {
      load(item.session.request_id, { showOverview: options.showOverview ?? true })
      return
    }
    applyTraceSelection(item, options)
  }, [applyTraceSelection, load])

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
              placeholder="输入消息内容、session_id 或 request_id"
              value={inputId}
              onChange={(e) => setInputId(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleTraceSearch()}
            />
          </div>
          <button className="btn-primary shrink-0" onClick={handleTraceSearch} disabled={loading || recentLoading}>
            {(loading || recentLoading) ? <RefreshCw size={13} className="animate-spin" /> : (searchLooksLikeRequestId ? '显示总览图' : '筛选请求')}
          </button>
          {isHistorySearchMode && (
            <button
              className="btn-secondary shrink-0 text-xs"
              onClick={() => {
                setInputId('')
                setHistoryExpanded(false)
                loadRecentRequests('')
              }}
              disabled={recentLoading}
            >
              清除筛选
            </button>
          )}
          {traceData && (
            <button className="btn-secondary shrink-0 text-xs flex items-center gap-1"
              onClick={() => { setTraceData(null); setTraceInfo(null); setInlineOverviewVisible(false) }}>
              <RefreshCw size={12} /> 重置
            </button>
          )}
        </div>
        {/* 链路摘要 */}
        {traceInfo && (
          <div className="flex items-center gap-3 text-xs text-gray-500">
            <span>激活节点 {traceInfo.nodeCount}</span>
            {traceInfo.latency != null && <span>总耗时 {traceInfo.latency}ms</span>}
            <span className={currentTraceHealthy ? 'text-green-600 font-medium' : 'text-red-500 font-medium'}>
              {traceInfo.status}
            </span>
          </div>
        )}
        {error && <span className="text-xs text-red-500">{error}</span>}
      </div>

      {selectedRequestId && (
        <div className="rounded-xl border border-blue-200 bg-blue-50 px-4 py-3 flex items-center gap-3 flex-wrap">
          <div className="min-w-0">
            <div className="text-xs font-medium text-blue-700">当前会话 request_id</div>
            <div className="font-mono text-sm text-blue-900 break-all">{selectedRequestId}</div>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <button className="btn-secondary text-xs" onClick={() => focusRequestOverview(selectedRequestId)} disabled={loading}>
              显示总览图
            </button>
            <button className="btn-primary text-xs" onClick={() => openRequestTrace(selectedRequestId)}>
              打开详情
            </button>
          </div>
        </div>
      )}

      <div className="rounded-2xl border border-gray-200 bg-gradient-to-br from-slate-50 via-white to-blue-50/60 p-4 md:p-5">
        <div className="flex items-center gap-3 flex-wrap">
          <div className="flex items-center gap-2 min-w-0">
            <span className="flex items-center justify-center w-9 h-9 rounded-xl bg-blue-600 text-white shadow-sm">
              <Activity size={18} />
            </span>
            <div>
              <h2 className="text-base font-semibold text-gray-900">{activeFeedTitle}</h2>
              <p className="text-xs text-gray-500">
                {activeFeedDescription}
              </p>
            </div>
          </div>
          <div className="ml-auto flex items-center gap-2 text-xs text-gray-500">
            <span className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 ${liveFeedStatusClass}`}>
              <RefreshCw size={12} className={liveFeedConnected ? 'animate-spin' : ''} />
              {liveFeedStatusText}
            </span>
            {liveFeedLastTs ? <span>最近事件 {formatRecentTraceTime(liveFeedLastTs)}</span> : null}
            <span>{liveTraceItems.length} {activeCountLabel}</span>
          </div>
        </div>

        <div className="mt-4 grid grid-cols-1 xl:grid-cols-[minmax(0,1.35fr)_24rem] gap-4">
          <div className="rounded-2xl border border-white/70 bg-white/90 p-3 shadow-sm shadow-blue-100/40">
            <div className="flex items-center justify-between gap-3 border-b border-gray-100 px-1 pb-3">
              <div>
                <div className="text-sm font-semibold text-gray-900">{activeListTitle}</div>
                <div className="text-xs text-gray-500">{activeListDescription}</div>
              </div>
              <button
                className="btn-secondary text-xs"
                onClick={() => loadRecentRequests(recentKeyword)}
                disabled={recentLoading || liveTraceLoading}
              >
                <RefreshCw size={12} className={(recentLoading || liveTraceLoading) ? 'animate-spin' : ''} />
                刷新
              </button>
            </div>

            <div className="mt-3 space-y-3">
              {liveTraceLoading && liveTraceItems.length === 0 && (
                <div className="rounded-xl border border-dashed border-gray-200 bg-gray-50 px-4 py-8 text-center text-sm text-gray-400">
                  正在加载最近请求的链路详情...
                </div>
              )}
              {!liveTraceLoading && liveTraceError && liveTraceItems.length === 0 && (
                <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-8 text-center text-sm text-red-500">
                  活跃链路流加载失败：{liveTraceError}
                </div>
              )}
              {!liveTraceLoading && !liveTraceError && liveTraceItems.length === 0 && (
                <div className="rounded-xl border border-dashed border-gray-200 bg-gray-50 px-4 py-8 text-center text-sm text-gray-400">
                  最近没有可展示的请求链路。
                </div>
              )}
              {liveTraceItems.map((item) => (
                <LiveTraceCard
                  key={item.session.request_id}
                  item={item}
                  selected={item.session.request_id === selectedLiveTraceItem?.session.request_id}
                  onSelect={() => focusLiveTraceItem(item, { showOverview: true })}
                  onOpenDetail={() => openRequestTrace(item.session.request_id)}
                />
              ))}
            </div>
          </div>

          <div className="rounded-2xl border border-white/70 bg-white/90 p-3 shadow-sm shadow-blue-100/40">
            <div className="border-b border-gray-100 px-1 pb-3">
              <div className="text-sm font-semibold text-gray-900">当前链路预览</div>
              <div className="text-xs text-gray-500">右侧固定看板会跟随选中请求切换，便于快速定位当前跑到哪一层。</div>
            </div>
            <div className="mt-3">
              <LiveTraceStreamPreview
                item={selectedLiveTraceItem}
                timelineEntries={selectedLiveTraceTimelineEntries}
                onHighlight={() => selectedLiveTraceItem && focusLiveTraceItem(selectedLiveTraceItem, { showOverview: true })}
                onOpenDetail={() => selectedLiveTraceItem && openRequestTrace(selectedLiveTraceItem.session.request_id)}
              />
            </div>
          </div>
        </div>
      </div>

      {inlineOverviewVisible && traceData && (
        <div ref={inlineOverviewRef}>
          <InlineFullChainOverview
            traceData={traceData}
            requestId={selectedRequestId}
            onOpenDetail={() => selectedRequestId && openRequestTrace(selectedRequestId)}
            onOpenStandalone={() => navigate('/trace/full')}
            onHide={() => setInlineOverviewVisible(false)}
          />
        </div>
      )}

      {!isHistorySearchMode && !showHistoryPanel && (
        <button
          className="w-full rounded-xl border border-dashed border-gray-300 bg-white px-4 py-4 text-left transition-colors hover:border-blue-300 hover:bg-blue-50/40"
          onClick={() => setHistoryExpanded(true)}
        >
          <div className="flex items-center gap-3">
            <span className="flex items-center justify-center w-9 h-9 rounded-xl bg-gray-100 text-blue-600">
              <Clock3 size={16} />
            </span>
            <div className="min-w-0 flex-1">
              <div className="text-sm font-semibold text-gray-900">查看历史请求</div>
              <div className="text-xs text-gray-500">{historySectionHint}</div>
            </div>
            <span className="text-xs font-medium text-blue-600">展开</span>
          </div>
        </button>
      )}

      {!isHistorySearchMode && showHistoryPanel && (
        <div className="rounded-xl border border-gray-200 bg-white overflow-hidden">
          <div className="flex items-center gap-2 px-4 py-3 border-b border-gray-200 bg-gray-50 flex-wrap">
            <Clock3 size={15} className="text-blue-600" />
            <div className="text-sm font-semibold text-gray-900">历史请求</div>
            <div className="text-xs text-gray-400">{historySectionHint}</div>
            <div className="ml-auto flex items-center gap-2">
              {latestRequest && (
                <button
                  className="btn-primary text-xs"
                  onClick={() => openRequestTrace(latestRequest.request_id)}
                >
                  打开最新
                </button>
              )}
              <button
                className="btn-secondary text-xs"
                onClick={() => loadRecentRequests(recentKeyword)}
                disabled={recentLoading}
              >
                <RefreshCw size={12} className={recentLoading ? 'animate-spin' : ''} />
                刷新
              </button>
              {canCollapseHistory && (
                <button
                  className="btn-secondary text-xs"
                  onClick={() => setHistoryExpanded(false)}
                >
                  收起
                </button>
              )}
            </div>
          </div>
          <div className="divide-y divide-gray-100">
            {recentLoading && recentRequests.length === 0 && (
              <div className="px-4 py-8 text-center text-sm text-gray-400">加载历史请求中...</div>
            )}
            {!recentLoading && recentRequests.length === 0 && (
              <div className="px-4 py-8 text-center text-sm text-gray-400">最近没有历史请求</div>
            )}
            {recentRequests.map((item) => {
              const statusMeta = getRecentRequestStatusMeta(item.status)
              return (
                <button
                  key={item.request_id}
                  className="w-full text-left px-4 py-3 hover:bg-gray-50 transition-colors"
                  onClick={() => openRequestTrace(item.request_id)}
                >
                  <div className="flex items-start gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium text-gray-900 break-all">
                        {item.input_preview || item.request_id}
                      </div>
                      <div className="mt-1 flex items-center gap-3 text-xs text-gray-500 flex-wrap">
                        <span className="font-mono">{item.request_id.slice(0, 18)}...</span>
                        {item.session_id ? <span>{item.session_id}</span> : null}
                        <span>{formatRecentTraceTime(item.ts_ms)}</span>
                        {item.total_latency_ms != null ? <span>{item.total_latency_ms}ms</span> : null}
                      </div>
                    </div>
                    <div className="shrink-0 flex items-center gap-2">
                      <span className={`text-xs font-medium ${statusMeta.className}`}>
                        {statusMeta.label}
                      </span>
                      <ChevronRight size={14} className="text-gray-400" />
                    </div>
                  </div>
                </button>
              )
            })}
          </div>
        </div>
      )}

      <div className="rounded-2xl border border-gray-200 bg-white overflow-hidden">
        <button
          className="w-full px-4 py-4 text-left transition-colors hover:bg-gray-50"
          onClick={() => setAdvancedViewsExpanded((value) => !value)}
        >
          <div className="flex items-center gap-3">
            <span className="flex items-center justify-center w-9 h-9 rounded-xl bg-gray-100 text-blue-600">
              <GitBranch size={16} />
            </span>
            <div className="min-w-0 flex-1">
              <div className="text-sm font-semibold text-gray-900">高级钻取视图</div>
              <div className="text-xs text-gray-500">只有需要按分组拆开排查时再展开。</div>
            </div>
            <span className="text-xs font-medium text-blue-600">{advancedViewsExpanded ? '收起' : '展开'}</span>
          </div>
        </button>

        {advancedViewsExpanded && (
          <div className="border-t border-gray-100 bg-gray-50/60 p-4 space-y-4">
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
                      <span
                        className="flex items-center justify-center w-9 h-9 rounded-lg"
                        style={{ background: isActive ? g.color : g.bg, color: isActive ? '#fff' : g.color }}
                      >
                        <Icon size={18} />
                      </span>
                      <div className="flex items-center gap-1.5">
                        {g.hasError && (
                          <span className="text-xs px-1.5 py-0.5 rounded-full bg-red-50 text-red-600 border border-red-200 font-medium">
                            异常
                          </span>
                        )}
                        {isActive && (
                          <span
                            className="text-xs px-1.5 py-0.5 rounded-full font-medium"
                            style={{ background: g.bg, color: g.color, border: `1px solid ${g.border}` }}
                          >
                            {g.activeCount}/{g.totalNodeCount}
                          </span>
                        )}
                        <ChevronRight size={14} className="text-gray-400" />
                      </div>
                    </div>
                    <div className="font-semibold text-gray-900 text-sm mb-1">{g.label}</div>
                    <div className="text-xs text-gray-500 leading-relaxed line-clamp-2">{g.desc}</div>
                    {traceData && (
                      <div className="mt-3 h-1 rounded-full bg-gray-100 overflow-hidden">
                        <div
                          className="h-1 rounded-full transition-all"
                          style={{ width: `${(g.activeCount / Math.max(g.totalNodeCount, 1)) * 100}%`, background: g.color }}
                        />
                      </div>
                    )}
                  </button>
                )
              })}
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_18rem] gap-4">
              <button
                className="w-full text-left rounded-xl border-2 border-dashed border-gray-300 p-4 hover:border-blue-400 hover:bg-blue-50 transition-all flex items-center gap-4"
                onClick={() => navigate('/trace/full')}
              >
                <span className="flex items-center justify-center w-10 h-10 rounded-lg bg-blue-50 text-blue-600 shrink-0">
                  <Layers size={20} />
                </span>
                <div className="flex-1">
                  <div className="font-semibold text-gray-800 text-sm">双端全链路拓扑图</div>
                  <div className="text-xs text-gray-500 mt-0.5">需要单独放大排查静态 DAG 时再进入。</div>
                </div>
                <ChevronRight size={16} className="text-gray-400 shrink-0" />
              </button>

              <div className="grid grid-cols-2 gap-3">
                {[
                  { label: '总节点数', value: FULL_CHAIN_SCHEMA.nodes.length, color: 'text-blue-600' },
                  { label: '总边数', value: FULL_CHAIN_SCHEMA.edges.length, color: 'text-indigo-600' },
                  { label: '激活节点', value: traceData ? (traceData.nodes ?? []).length : '—', color: 'text-green-600' },
                  { label: '总耗时', value: traceInfo?.latency != null ? `${traceInfo.latency}ms` : '—', color: 'text-orange-500' },
                ].map(({ label, value, color }) => (
                  <div key={label} className="rounded-xl border border-gray-200 bg-white p-4 text-center">
                    <div className={`text-2xl font-bold ${color}`}>{value}</div>
                    <div className="text-xs text-gray-500 mt-1">{label}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function getRecentRequestStatusMeta(status) {
  const normalized = String(status || '').toLowerCase()
  if (normalized === 'ok' || normalized === 'success') {
    return { label: '成功', className: 'text-green-600' }
  }
  if (normalized === 'rejected') {
    return { label: '拒绝', className: 'text-orange-500' }
  }
  if (normalized === 'error' || normalized === 'fail') {
    return { label: '异常', className: 'text-red-500' }
  }
  return { label: String(status || '未知'), className: 'text-gray-500' }
}

function formatRecentTraceTime(tsMs) {
  if (!tsMs) return '-'
  return new Date(tsMs).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
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
      <Route path=":requestId" element={<TraceRequestDetail />} />
    </Routes>
  )
}

function GroupDagViewWrapper({ traceData }) {
  const { groupId } = useParams()
  return <GroupDagView groupId={groupId} traceData={traceData} />
}
