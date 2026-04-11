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
import DigitalLifeRuntimeCard from '../components/DigitalLifeRuntimeCard.jsx'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import LivingPulseStrip from '../components/LivingPulseStrip.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import NodeDetailPanel from '../components/trace/NodeDetailPanel.jsx'
import PageHero from '../components/PageHero.jsx'
import StatusBadge from '../components/StatusBadge.jsx'
import { getTraceNodeDisplayName } from '../components/trace/traceNodeText.js'
import TraceRequestDetail from './TraceRequestDetail.jsx'
import useDigitalLifeRuntimeSignals from '../hooks/useDigitalLifeRuntimeSignals.js'
import { applyDagreLayout } from '../utils/traceLayout.js'
import { formatAlertTitle, humanizeToken } from '../utils/digitalLife.js'
import { sanitizeRecentRequests } from '../utils/sessionList.js'
import {
  connectTraceLiveFeed,
  fetchTraceNodes,
  fetchTraceStreamEvents,
  fetchGraphSchema,
  fetchCheckpoints,
  fetchChainStats,
  fetchObsMetrics,
  fetchSessions,
  fetchTribeEvents,
} from '../api/obs.js'
import useAppStore from '../store/useAppStore.js'

const NODE_TYPES = { customNode: CustomNode }

const GROUPS = [
  {
    id: 'edge',
    label: '边缘输入',
    desc: 'ASR · 网关回调 · 输入上报',
    icon: Mic,
    color: '#059669',
    bg: '#ECFDF5',
    border: '#6EE7B7',
    nodeIds: ['asr', 'gateway_webhook', 'input_reporter'],
    traceNodeIds: ['asr', 'gateway_webhook', 'input_reporter'],
  },
  {
    id: 'entry',
    label: '入口守护',
    desc: 'Airlock Ingress · Governor',
    icon: ArrowRightLeft,
    color: '#475467',
    bg: '#F8FAFC',
    border: '#CBD5E1',
    nodeIds: ['airlock_ingress', 'governor'],
    traceNodeIds: ['airlock_ingress', 'governor', 'host_input'],
  },
  {
    id: 'immune',
    label: '免疫门禁',
    desc: 'Immune Gate · 风险扫描',
    icon: Shield,
    color: '#EA580C',
    bg: '#FFF7ED',
    border: '#FDBA74',
    nodeIds: ['immune_gate'],
    traceNodeIds: ['immune_gate'],
  },
  {
    id: 'retrieval',
    label: '检索规划',
    desc: 'Query Planner · Agentic RAG · Recall Fusion',
    icon: Filter,
    color: '#0891B2',
    bg: '#ECFEFF',
    border: '#67E8F9',
    nodeIds: ['query_planner', 'agentic_rag', 'recall_fusion'],
    traceNodeIds: ['query_planner', 'agentic_rag', 'recall_fusion'],
  },
  {
    id: 'persona',
    label: '人格上下文',
    desc: '情感 · 常驻记忆 · 动机状态',
    icon: Bookmark,
    color: '#0F766E',
    bg: '#F0FDFA',
    border: '#5EEAD4',
    nodeIds: ['emotion_context', 'persona_resident_memory', 'motive_state'],
    traceNodeIds: ['emotion_context', 'persona_resident_memory', 'motive_state'],
  },
  {
    id: 'orchestration',
    label: '编排决策',
    desc: '响应策略 · Top Agent',
    icon: Shield,
    color: '#7C3AED',
    bg: '#F5F3FF',
    border: '#C4B5FD',
    nodeIds: ['response_policy', 'top_agent'],
    traceNodeIds: ['response_policy', 'top_agent'],
  },
  {
    id: 'agent',
    label: '代理调度',
    desc: 'Dispatch Agent · Sub Agent · Chat Agent',
    icon: Users,
    color: '#9333EA',
    bg: '#FAF5FF',
    border: '#D8B4FE',
    nodeIds: ['dispatch_agent', 'sub_agent_code', 'sub_agent_translate', 'sub_agent_intent', 'sub_agent_vision', 'chat_agent'],
    traceNodeIds: ['dispatch_agent', 'sub_agent_code', 'sub_agent_translate', 'sub_agent_intent', 'sub_agent_vision', 'chat_agent'],
  },
  {
    id: 'llm',
    label: '提示与模型',
    desc: 'Prompt Builder · LLM Stream',
    icon: Brain,
    color: '#2970FF',
    bg: '#EEF4FF',
    border: '#93C5FD',
    nodeIds: ['prompt_builder', 'llm_stream'],
    traceNodeIds: ['prompt_builder', 'llm_stream', 'llm_gateway', 'top_agent_llm_stream'],
  },
  {
    id: 'postprocess',
    label: '收口输出',
    desc: '出口净化 · PII · 记忆/Bond · TTS/VTS',
    icon: Zap,
    color: '#D97706',
    bg: '#FFFBEB',
    border: '#FCD34D',
    nodeIds: ['airlock_egress', 'pii_filter', 'episodic_store', 'bond_update', 'tts', 'vts_driver'],
    traceNodeIds: ['airlock_egress', 'pii_filter', 'episodic_store', 'bond_update', 'audit_snapshot', 'tts', 'vts_driver'],
  },
]

const FULL_CHAIN_SCHEMA = {
  graph_id: 'natsume.digital_life_trace_v2',
  nodes: [
    { node_id: 'asr', node_type: 'asr', display_name: '语音识别', layer: 'edge' },
    { node_id: 'gateway_webhook', node_type: 'system', display_name: '网关回调', layer: 'edge' },
    { node_id: 'input_reporter', node_type: 'system', display_name: '输入上报', layer: 'edge' },
    { node_id: 'airlock_ingress', node_type: 'governor', display_name: '入口净化', layer: 'host' },
    { node_id: 'governor', node_type: 'governor', display_name: '入口守护', layer: 'host' },
    { node_id: 'immune_gate', node_type: 'immune', display_name: 'Immune Gate', layer: 'host' },
    { node_id: 'query_planner', node_type: 'retrieval', display_name: '查询规划器', layer: 'host' },
    { node_id: 'agentic_rag', node_type: 'retrieval', display_name: 'Agentic RAG', layer: 'host' },
    { node_id: 'recall_fusion', node_type: 'recall', display_name: '召回融合', layer: 'host' },
    { node_id: 'emotion_context', node_type: 'system', display_name: '情感上下文', layer: 'host' },
    { node_id: 'persona_resident_memory', node_type: 'memory', display_name: '人设常驻记忆', layer: 'host' },
    { node_id: 'motive_state', node_type: 'system', display_name: '动机状态', layer: 'host' },
    { node_id: 'response_policy', node_type: 'system', display_name: '响应策略', layer: 'host' },
    { node_id: 'top_agent', node_type: 'system', display_name: 'Top Agent', layer: 'host' },
    { node_id: 'dispatch_agent', node_type: 'agent', display_name: 'Dispatch Agent', layer: 'host' },
    { node_id: 'sub_agent_code', node_type: 'agent', display_name: '代码代理', layer: 'host' },
    { node_id: 'sub_agent_translate', node_type: 'agent', display_name: '翻译代理', layer: 'host' },
    { node_id: 'sub_agent_intent', node_type: 'agent', display_name: '意图代理', layer: 'host' },
    { node_id: 'sub_agent_vision', node_type: 'agent', display_name: '视觉代理', layer: 'host' },
    { node_id: 'chat_agent', node_type: 'agent', display_name: 'Chat Agent', layer: 'host' },
    { node_id: 'prompt_builder', node_type: 'system', display_name: '提示构建器', layer: 'host' },
    { node_id: 'llm_stream', node_type: 'llm', display_name: '模型流式生成', layer: 'host' },
    { node_id: 'airlock_egress', node_type: 'governor', display_name: '出口净化', layer: 'host' },
    { node_id: 'pii_filter', node_type: 'governor', display_name: '隐私过滤', layer: 'host' },
    { node_id: 'episodic_store', node_type: 'memory', display_name: '记忆写入', layer: 'host' },
    { node_id: 'bond_update', node_type: 'bond', display_name: '关系更新', layer: 'host' },
    { node_id: 'tts', node_type: 'tts', display_name: '语音合成', layer: 'edge' },
    { node_id: 'vts_driver', node_type: 'vts', display_name: '表情驱动', layer: 'edge' },
  ],
  edges: [
    { source: 'asr', target: 'airlock_ingress' },
    { source: 'gateway_webhook', target: 'airlock_ingress' },
    { source: 'input_reporter', target: 'airlock_ingress' },
    { source: 'airlock_ingress', target: 'governor' },
    { source: 'governor', target: 'immune_gate' },
    { source: 'immune_gate', target: 'query_planner' },
    { source: 'query_planner', target: 'agentic_rag' },
    { source: 'agentic_rag', target: 'recall_fusion' },
    { source: 'recall_fusion', target: 'emotion_context' },
    { source: 'emotion_context', target: 'persona_resident_memory' },
    { source: 'persona_resident_memory', target: 'motive_state' },
    { source: 'motive_state', target: 'response_policy' },
    { source: 'response_policy', target: 'top_agent' },
    { source: 'top_agent', target: 'dispatch_agent' },
    { source: 'top_agent', target: 'prompt_builder' },
    { source: 'dispatch_agent', target: 'sub_agent_code' },
    { source: 'dispatch_agent', target: 'sub_agent_translate' },
    { source: 'dispatch_agent', target: 'sub_agent_intent' },
    { source: 'dispatch_agent', target: 'sub_agent_vision' },
    { source: 'dispatch_agent', target: 'chat_agent' },
    { source: 'sub_agent_code', target: 'chat_agent' },
    { source: 'sub_agent_translate', target: 'chat_agent' },
    { source: 'sub_agent_intent', target: 'chat_agent' },
    { source: 'sub_agent_vision', target: 'chat_agent' },
    { source: 'chat_agent', target: 'prompt_builder' },
    { source: 'prompt_builder', target: 'llm_stream' },
    { source: 'llm_stream', target: 'airlock_egress' },
    { source: 'airlock_egress', target: 'pii_filter' },
    { source: 'airlock_egress', target: 'episodic_store' },
    { source: 'airlock_egress', target: 'bond_update' },
    { source: 'airlock_egress', target: 'tts' },
    { source: 'airlock_egress', target: 'vts_driver' },
  ],
}

const FULL_CHAIN_NODE_MAP = Object.fromEntries(FULL_CHAIN_SCHEMA.nodes.map((node) => [node.node_id, node]))
const FULL_CHAIN_NODE_ID_SET = new Set(FULL_CHAIN_SCHEMA.nodes.map((node) => node.node_id))

function getTraceGroupNodeIds(group) {
  return group?.traceNodeIds ?? group?.nodeIds ?? []
}

function normalizeTraceGroupNodeId(nodeId = '', nodeType = '') {
  const normalized = String(nodeId || '').trim()
  const normalizedType = String(nodeType || '').trim().toLowerCase()
  if (normalizedType === 'tts' || normalized.startsWith('voice_tts_')) return 'tts'
  if (normalizedType === 'vts' || normalized.startsWith('voice_vts_')) return 'vts_driver'
  if (normalizedType === 'asr' || normalized.startsWith('voice_asr_')) return 'asr'
  if (normalizedType === 'immune' || normalized.startsWith('voice_immune_')) return 'immune_gate'
  if (normalized.startsWith('voice_edge_')) return 'input_reporter'
  if (normalized.startsWith('voice_')) return 'input_reporter'
  if (normalized.startsWith('episodic_store')) return 'episodic_store'
  if (normalized.startsWith('bond_update')) return 'bond_update'
  if (normalized.startsWith('sub_agent:code') || normalized === 'sub_agent_code') return 'sub_agent_code'
  if (normalized.startsWith('sub_agent:translate') || normalized === 'sub_agent_translate') return 'sub_agent_translate'
  if (normalized.startsWith('sub_agent:intent') || normalized === 'sub_agent_intent') return 'sub_agent_intent'
  if (normalized.startsWith('sub_agent:vision') || normalized === 'sub_agent_vision') return 'sub_agent_vision'
  if (normalized.startsWith('sub_agent:')) return 'sub_agent_intent'
  if (normalized.startsWith('airlock')) return normalized.includes('egress') ? 'airlock_egress' : 'airlock_ingress'
  if (normalized === 'host_input') return 'airlock_ingress'
  if (normalized === 'llm_gateway' || normalized === 'top_agent_llm_stream') return 'llm_stream'
  if (normalized.startsWith('retrieval_') || normalized === 'recall_fusion') return 'recall_fusion'
  return normalized
}

function getTraceGroupState(group, traceData) {
  if (!group || !traceData) {
    return { groupNodes: [], groupEdges: [], activeNodeIds: [], activeCount: 0, traceNodeMap: {} }
  }

  const allowedIds = new Set(getTraceGroupNodeIds(group))
  const groupNodes = (traceData.nodes ?? []).filter((node) => {
    const explicitGroupId = String(node?.group_id || '').trim()
    if (explicitGroupId) return explicitGroupId === group.id
    return allowedIds.has(normalizeTraceGroupNodeId(node.node_id, node.node_type))
  })
  const groupNodeIdSet = new Set(groupNodes.map((node) => node.node_id))
  const groupEdges = (traceData.edges ?? []).filter((edge) => groupNodeIdSet.has(edge.source) && groupNodeIdSet.has(edge.target))
  const activeNodeIds = [...new Set(groupNodes.map((node) => normalizeTraceGroupNodeId(node.node_id, node.node_type)))]

  return {
    groupNodes,
    groupEdges,
    activeNodeIds,
    activeCount: activeNodeIds.length,
    traceNodeMap: Object.fromEntries(groupNodes.map((node) => [node.node_id, node])),
  }
}

function getTraceStatusPriority(status = '') {
  return {
    error: 5,
    degraded: 4,
    running: 3,
    healthy: 2,
    unknown: 1,
  }[normalizeBadgeStatus(status)] ?? 0
}

function mergeObservedNodesIntoCanonicalTraceNode(canonicalNode, observedNodes) {
  const orderedNodes = [...observedNodes].sort((a, b) => (a?.ts_ms ?? 0) - (b?.ts_ms ?? 0))
  const latestNode = orderedNodes[orderedNodes.length - 1] ?? {}
  const highlightedNode = orderedNodes.reduce((selected, current) => {
    if (!selected) return current
    const selectedPriority = getTraceStatusPriority(selected?.status)
    const currentPriority = getTraceStatusPriority(current?.status)
    if (currentPriority !== selectedPriority) {
      return currentPriority > selectedPriority ? current : selected
    }
    return (current?.ts_ms ?? 0) >= (selected?.ts_ms ?? 0) ? current : selected
  }, null)
  const observedNodeIds = orderedNodes
    .map((node) => String(node?.node_id || '').trim())
    .filter(Boolean)
  const statusBreakdown = orderedNodes.reduce((accumulator, node) => {
    const key = String(node?.status || 'UNKNOWN').toUpperCase()
    accumulator[key] = (accumulator[key] ?? 0) + 1
    return accumulator
  }, {})
  const cumulativeLatencyMs = orderedNodes.reduce((total, node) => (
    total + (Number.isFinite(Number(node?.latency_ms)) ? Number(node.latency_ms) : 0)
  ), 0)

  return {
    ...canonicalNode,
    ...latestNode,
    node_id: canonicalNode.node_id,
    display_name: canonicalNode.display_name,
    node_type: canonicalNode.node_type,
    layer: canonicalNode.layer,
    side: canonicalNode.layer,
    status: highlightedNode?.status ?? latestNode?.status ?? 'UNKNOWN',
    reason_code: highlightedNode?.reason_code ?? latestNode?.reason_code ?? null,
    error: highlightedNode?.error ?? highlightedNode?.reason_code ?? latestNode?.error ?? null,
    latency_ms: cumulativeLatencyMs > 0 ? cumulativeLatencyMs : (latestNode?.latency_ms ?? null),
    input_data: orderedNodes.length > 1 ? {
      observed_node_count: orderedNodes.length,
      latest_observed_node_id: latestNode?.node_id ?? null,
      latest_input: latestNode?.input_data ?? null,
    } : (latestNode?.input_data ?? null),
    output_data: orderedNodes.length > 1 ? {
      observed_node_count: orderedNodes.length,
      observed_node_ids: observedNodeIds,
      latest_observed_node_id: latestNode?.node_id ?? null,
      latest_display_name: latestNode?.display_name ?? null,
      latest_status: latestNode?.status ?? null,
      latest_output: latestNode?.output_data ?? null,
      status_breakdown: statusBreakdown,
    } : (latestNode?.output_data ?? null),
  }
}

function getObservedTraceSchema(traceData) {
  if (!traceData) {
    return {
      ...FULL_CHAIN_SCHEMA,
      activeNodeIds: null,
      traceNodeMap: {},
      rawNodeCount: 0,
      rawEdgeCount: 0,
      canonicalized: true,
    }
  }

  const observedNodes = Array.isArray(traceData?.nodes)
    ? traceData.nodes.map((node) => ({ ...node, layer: node.layer ?? node.side ?? 'host' }))
    : []
  const observedEdges = Array.isArray(traceData?.edges) ? traceData.edges : []
  const groupedObservedNodes = {}

  observedNodes.forEach((node) => {
    const canonicalId = normalizeTraceGroupNodeId(node.node_id, node.node_type)
    if (!FULL_CHAIN_NODE_ID_SET.has(canonicalId)) return
    if (!groupedObservedNodes[canonicalId]) groupedObservedNodes[canonicalId] = []
    groupedObservedNodes[canonicalId].push(node)
  })

  const activeNodeIds = Object.keys(groupedObservedNodes)

  if (activeNodeIds.length === 0) {
    return {
      nodes: observedNodes,
      edges: observedEdges,
      activeNodeIds: observedNodes.map((node) => node.node_id),
      traceNodeMap: Object.fromEntries(observedNodes.map((node) => [node.node_id, node])),
      rawNodeCount: observedNodes.length,
      rawEdgeCount: observedEdges.length,
      canonicalized: false,
    }
  }

  return {
    nodes: FULL_CHAIN_SCHEMA.nodes,
    edges: FULL_CHAIN_SCHEMA.edges,
    activeNodeIds,
    traceNodeMap: Object.fromEntries(
      activeNodeIds.map((nodeId) => [
        nodeId,
        mergeObservedNodesIntoCanonicalTraceNode(FULL_CHAIN_NODE_MAP[nodeId], groupedObservedNodes[nodeId]),
      ]),
    ),
    rawNodeCount: observedNodes.length,
    rawEdgeCount: observedEdges.length,
    canonicalized: true,
  }
}

const LIVE_TRACE_GROUP_ORDER = ['edge', 'entry', 'immune', 'retrieval', 'persona', 'orchestration', 'agent', 'llm', 'postprocess']

function normalizeBadgeStatus(status) {
  const value = String(status || '').toUpperCase()
  if (['OK', 'UP', 'SUCCESS', 'APPROVE', 'APPROVED', 'APPLIED', 'ACCEPTED', 'RECEIVED', 'DISPATCHED', 'QUEUED', 'SENT', 'SKIPPED'].includes(value)) return 'healthy'
  if (['RUNNING', 'STARTED'].includes(value)) return 'running'
  if (['DEGRADED'].includes(value)) return 'degraded'
  if (['FAIL', 'FAILED', 'ERROR', 'DOWN', 'REJECTED', 'REJECT'].includes(value)) return 'error'
  return 'unknown'
}

function getLiveTraceStageLabel(groupId) {
  return {
    edge: '输入',
    entry: '入口',
    immune: '免疫',
    retrieval: '检索',
    persona: '人格',
    orchestration: '编排',
    agent: '代理',
    llm: '模型',
    postprocess: '输出',
  }[groupId] ?? groupId
}

function getLatestTraceNode(traceData) {
  const nodes = Array.isArray(traceData?.nodes) ? [...traceData.nodes] : []
  if (nodes.length === 0) return null
  nodes.sort((a, b) => (a?.ts_ms ?? 0) - (b?.ts_ms ?? 0))
  return nodes[nodes.length - 1]
}

function buildImmuneStageMeta(groupNodes = []) {
  const orderedNodes = [...(Array.isArray(groupNodes) ? groupNodes : [])]
    .sort((a, b) => (a?.ts_ms ?? 0) - (b?.ts_ms ?? 0))

  let decision = ''
  let riskLevel = ''
  const reasonTags = []
  const seenReasonTags = new Set()

  const pushReasonTag = (value) => {
    const normalized = String(value || '').trim()
    if (!normalized || seenReasonTags.has(normalized)) return
    seenReasonTags.add(normalized)
    reasonTags.push(normalized)
  }

  orderedNodes.forEach((node) => {
    const payload = (node?.output_data && typeof node.output_data === 'object') ? node.output_data : {}
    const nestedImmune = (payload?.immune_gate && typeof payload.immune_gate === 'object') ? payload.immune_gate : {}
    const mergedPayload = { ...nestedImmune, ...payload }

    const payloadDecision = String(mergedPayload.decision || '').trim().toUpperCase()
    const payloadRisk = String(mergedPayload.risk_level || '').trim().toLowerCase()
    if (payloadDecision) decision = payloadDecision
    if (payloadRisk) riskLevel = payloadRisk

    const reasonCode = String(node?.reason_code || mergedPayload.reason_code || '').trim()
    if (reasonCode) pushReasonTag(reasonCode)

    const reasonCodes = Array.isArray(mergedPayload.reason_codes) ? mergedPayload.reason_codes : []
    reasonCodes.forEach((code) => pushReasonTag(code))
  })

  return {
    decision: decision || null,
    riskLevel: riskLevel || null,
    reasonTags: reasonTags.slice(0, 3),
  }
}

function isOptionalTraceNode(node) {
  const nodeType = String(node?.node_type || '').toLowerCase()
  if (nodeType === 'tts' || nodeType === 'vts') return true
  if (nodeType === 'recall') {
    const candidateCount = node?.output_data?.candidate_count
    return !node?.reason_code && Number(candidateCount || 0) === 0
  }
  return false
}

function getLatestPrimaryTraceNode(traceData) {
  const nodes = Array.isArray(traceData?.nodes) ? [...traceData.nodes] : []
  if (nodes.length === 0) return null
  nodes.sort((a, b) => (a?.ts_ms ?? 0) - (b?.ts_ms ?? 0))
  for (let index = nodes.length - 1; index >= 0; index -= 1) {
    if (!isOptionalTraceNode(nodes[index])) return nodes[index]
  }
  return nodes[nodes.length - 1]
}

function getLiveTraceFlow(traceData) {
  const latestNode = getLatestPrimaryTraceNode(traceData)
  const latestNormalizedNodeId = latestNode ? normalizeTraceGroupNodeId(latestNode.node_id, latestNode.node_type) : null
  const latestGroupId = String(latestNode?.group_id || '').trim() || null

  return LIVE_TRACE_GROUP_ORDER
    .map((groupId) => GROUPS.find((group) => group.id === groupId))
    .filter(Boolean)
    .map((group) => {
      const traceGroupState = getTraceGroupState(group, traceData)
      const hasError = traceGroupState.groupNodes.some((node) => ['FAIL', 'ERROR', 'DOWN'].includes(String(node.status || '').toUpperCase()))
      const isCurrent = latestGroupId ? latestGroupId === group.id : (latestNormalizedNodeId ? getTraceGroupNodeIds(group).includes(latestNormalizedNodeId) : false)
      const immuneMeta = group.id === 'immune' ? buildImmuneStageMeta(traceGroupState.groupNodes) : null
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
        immuneDecision: immuneMeta?.decision ?? null,
        immuneRiskLevel: immuneMeta?.riskLevel ?? null,
        immuneReasonTags: immuneMeta?.reasonTags ?? [],
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
  return 'border-primary-200 bg-primary-50/60 text-primary/60'
}

function renderImmuneStageTags(stage, keyPrefix) {
  if (stage.groupId !== 'immune') return null
  const decision = String(stage.immuneDecision || '').trim().toUpperCase()
  const riskLevel = String(stage.immuneRiskLevel || '').trim().toLowerCase()
  const reasonTags = Array.isArray(stage.immuneReasonTags) ? stage.immuneReasonTags : []
  if (!decision && !riskLevel && reasonTags.length === 0) return null

  return (
    <>
      {(decision || riskLevel) ? (
        <span
          key={`${keyPrefix}-immune-meta`}
          className="inline-flex items-center rounded-full border border-orange-200 bg-orange-50 px-2 py-1 text-[11px] font-medium text-orange-700"
        >
          {decision || 'IMMUNE'}
          {riskLevel ? `/${riskLevel}` : ''}
        </span>
      ) : null}
      {reasonTags.slice(0, 2).map((code, index) => (
        <span
          key={`${keyPrefix}-immune-reason-${index}`}
          className="inline-flex items-center rounded-full border border-amber-200 bg-amber-50 px-2 py-1 text-[11px] font-medium text-amber-700"
        >
          {code}
        </span>
      ))}
    </>
  )
}

function getLiveTraceOutputPreview(traceData) {
  const summary = traceData?.summary ?? {}
  const output = summary.output_text
  if (typeof output === 'string' && output.trim()) return output.trim()
  if (typeof summary.failure_event === 'string' && summary.failure_event.trim()) return summary.failure_event.trim()
  const latestNode = getLatestPrimaryTraceNode(traceData) || getLatestTraceNode(traceData)
  if (typeof latestNode?.reason_code === 'string' && latestNode.reason_code.trim()) {
    return formatAlertTitle(latestNode.reason_code.trim())
  }
  if (typeof latestNode?.error === 'string' && latestNode.error.trim()) return latestNode.error.trim()
  return ''
}

function getLiveTraceCurrentLabel(traceData) {
  const latestNode = getLatestPrimaryTraceNode(traceData) || getLatestTraceNode(traceData)
  if (!latestNode) return '等待节点上报'
  const status = String(latestNode.status || '').toUpperCase()
  return `${getTraceNodeDisplayName(latestNode)} · ${humanizeToken(status || '未知')}`
}

const LIVE_TRACE_KEY_NODE_IDS = [
  'airlock_ingress',
  'governor',
  'immune_gate',
  'query_planner',
  'agentic_rag',
  'recall_fusion',
  'emotion_context',
  'persona_resident_memory',
  'motive_state',
  'response_policy',
  'top_agent',
  'dispatch_agent',
  'sub_agent_code',
  'sub_agent_translate',
  'sub_agent_intent',
  'sub_agent_vision',
  'chat_agent',
  'prompt_builder',
  'llm_stream',
  'airlock_egress',
]
const LIVE_TRACE_KEY_NODE_INDEX = Object.fromEntries(LIVE_TRACE_KEY_NODE_IDS.map((nodeId, index) => [nodeId, index]))

function getLiveTraceKeyNodeSortKey(node) {
  const normalizedId = normalizeTraceGroupNodeId(node?.node_id, node?.node_type)
  if (LIVE_TRACE_KEY_NODE_INDEX[normalizedId] != null) return LIVE_TRACE_KEY_NODE_INDEX[normalizedId]
  if (String(node?.node_id || '').startsWith('sub_agent:')) {
    return (LIVE_TRACE_KEY_NODE_INDEX.chat_agent ?? 999) - 0.5
  }
  return 999
}

function buildLiveTraceKeyNodeSequence(traceData) {
  const nodes = Array.isArray(traceData?.nodes) ? [...traceData.nodes] : []
  const seen = new Set()
  return nodes
    .filter((node) => {
      const normalizedId = normalizeTraceGroupNodeId(node?.node_id, node?.node_type)
      if (String(node?.side || node?.layer || '').toLowerCase() === 'edge') return false
      if (LIVE_TRACE_KEY_NODE_INDEX[normalizedId] != null) return true
      return String(node?.node_id || '').startsWith('sub_agent:')
    })
    .sort((a, b) => {
      const rankDiff = getLiveTraceKeyNodeSortKey(a) - getLiveTraceKeyNodeSortKey(b)
      if (rankDiff !== 0) return rankDiff
      return (a?.ts_ms ?? 0) - (b?.ts_ms ?? 0)
    })
    .filter((node) => {
      const dedupeKey = String(node?.node_id || normalizeTraceGroupNodeId(node?.node_id, node?.node_type))
      if (!dedupeKey || seen.has(dedupeKey)) return false
      seen.add(dedupeKey)
      return true
    })
    .map((node) => ({
      nodeId: node.node_id,
      label: getTraceNodeDisplayName(node),
      status: node.status,
      preview: extractLiveTraceNodePreview(node),
    }))
}

function buildLiveTraceMetrics(session, traceData) {
  const flow = traceData ? getLiveTraceFlow(traceData) : []
  return {
    flow,
    keyNodeSequence: traceData ? buildLiveTraceKeyNodeSequence(traceData) : [],
    currentLabel: traceData ? getLiveTraceCurrentLabel(traceData) : '等待链路详情',
    outputPreview: traceData ? getLiveTraceOutputPreview(traceData) : '',
    nodeCount: traceData?.node_count ?? 0,
    latencyMs: traceData?.total_latency_ms ?? session?.total_latency_ms ?? null,
    status: traceData?.status ?? session?.status ?? '未知',
  }
}

function KeyNodeSequence({ nodes = [] }) {
  if (!Array.isArray(nodes) || nodes.length === 0) return null
  return (
    <div>
      <div className="text-xs font-medium text-gray-500">关键节点链</div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {nodes.map((node, index) => (
          <React.Fragment key={`${node.nodeId}-${index}`}>
            <span className="inline-flex items-center gap-1 rounded-full border border-primary-200 bg-primary-50/45 px-2.5 py-1 text-xs text-primary-700">
              <span>{node.label}</span>
              <StatusBadge status={normalizeBadgeStatus(node.status)} />
            </span>
            {index < nodes.length - 1 ? <ChevronRight size={12} className="text-gray-300" /> : null}
          </React.Fragment>
        ))}
      </div>
    </div>
  )
}

function getTraceIssueCount(traceData) {
  return Array.isArray(traceData?.nodes)
    ? traceData.nodes.filter((node) => ['DEGRADED', 'FAIL', 'FAILED', 'ERROR', 'DOWN', 'REJECTED', 'REJECT'].includes(String(node?.status || '').toUpperCase())).length
    : 0
}

function getTraceRunningCount(traceData) {
  return Array.isArray(traceData?.nodes)
    ? traceData.nodes.filter((node) => ['RUNNING', 'STARTED'].includes(String(node?.status || '').toUpperCase())).length
    : 0
}

function getTraceActiveStageMeta(traceData) {
  const flow = traceData ? getLiveTraceFlow(traceData) : []
  const activeStages = flow.filter((stage) => stage.count > 0)
  const currentStage = flow.find((stage) => stage.status === 'running') || activeStages[activeStages.length - 1] || null
  return {
    flow,
    activeStageCount: activeStages.length,
    currentStageLabel: currentStage?.label || '等待链路推进',
  }
}

function getSessionDisplayTitle(session, traceData) {
  const summary = traceData?.summary ?? {}
  return (
    summary.input_text
    || session?.input_preview
    || session?.request_id
    || traceData?.request_id
    || '未命名请求'
  )
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
      value.reason_code ? formatAlertTitle(value.reason_code) : value.reason_code,
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
    node.reason_code ? formatAlertTitle(node.reason_code) : node.reason_code,
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
    displayName: getTraceNodeDisplayName(node),
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
      displayName: getTraceNodeDisplayName(node),
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
  return 'border-primary-200 bg-primary-50/60 text-primary-700'
}

function formatEventPayloadPreview(payload, maxLength = 180) {
  if (!payload || typeof payload !== 'object') return '暂无 payload'
  try {
    const raw = JSON.stringify(payload)
    if (!raw) return '暂无 payload'
    return raw.length > maxLength ? `${raw.slice(0, maxLength)}...` : raw
  } catch {
    return 'payload 无法序列化'
  }
}

function HomeSummaryCard({ icon: Icon, title, value, caption, toneClassName = 'text-gray-900' }) {
  return (
    <div className="card p-4">
      <div className="flex items-start gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-primary-100/70 bg-white text-blue-600">
          <Icon size={18} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-xs font-medium text-gray-500">{title}</div>
          <div className={`mt-1 break-all text-sm font-semibold leading-6 ${toneClassName}`}>{value}</div>
          {caption ? <div className="mt-1 break-all text-xs leading-5 text-gray-500">{caption}</div> : null}
        </div>
      </div>
    </div>
  )
}

function TraceSection({ title, hint, aside = null, children }) {
  return (
    <section className="card relative overflow-hidden p-4 md:p-5">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_left,_rgba(41,112,255,0.05),_transparent_38%),radial-gradient(circle_at_bottom_right,_rgba(16,185,129,0.05),_transparent_34%)]" />
      <div className="relative">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="lifeline-node" />
          <h2 className="text-sm font-semibold text-gray-900">{title}</h2>
          {aside ? <div className="ml-auto">{aside}</div> : null}
        </div>
        {hint ? <p className="mt-1 text-xs text-gray-500">{hint}</p> : null}
        <div className="lifeline-rail mt-3 h-4 px-2">
          <div className="lifeline-wave absolute inset-0 opacity-65" />
          <div className="relative flex h-full items-center justify-around">
            {[0, 1, 2, 3].map((index) => (
              <span key={index} className="lifeline-node" style={{ animationDelay: `${index * 0.24}s` }} />
            ))}
          </div>
        </div>
        <div className="mt-4">{children}</div>
      </div>
    </section>
  )
}

const LiveTraceCard = React.memo(function LiveTraceCard({ item, selected, onSelect, onOpenDetail }) {
  const { session, trace } = item
  const metrics = buildLiveTraceMetrics(session, trace)
  const inputPreview = getSessionDisplayTitle(session, trace)

  return (
    <div
      className={`relative overflow-hidden rounded-xl border p-4 transition-all ${
        selected
          ? 'border-blue-300 bg-blue-50/60 shadow-sm shadow-blue-100/40'
          : 'border-primary-100/70 bg-white/88 hover:-translate-y-0.5 hover:border-primary-200 hover:bg-white'
      }`}
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
            <div key={`${session.request_id}-${stage.groupId}`} className="inline-flex items-center gap-1.5">
              <span
                className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium ${className}`}
                style={style}
              >
                {stage.label}
                {stage.count > 0 ? `${stage.count}/${stage.total}` : '0'}
              </span>
              {renderImmuneStageTags(stage, `${session.request_id}-${stage.groupId}`)}
            </div>
          )
        })}
      </div>

      <div className="mt-3">
        <KeyNodeSequence nodes={metrics.keyNodeSequence} />
      </div>

      <button className="mt-3 block w-full text-left" onClick={onSelect}>
        <div className="text-xs font-medium text-gray-500">当前节点</div>
        <div className="mt-1 text-sm text-gray-700 break-all">{metrics.currentLabel}</div>
        <div className="mt-2 text-xs font-medium text-gray-500">结果摘要</div>
        <div className="mt-1 text-sm text-gray-600 break-all">
          {metrics.outputPreview || '暂无输出，链路仍在推进或尚未落盘。'}
        </div>
      </button>
      <div className="lifeline-rail mt-3 h-1.5">
        <div className="lifeline-wave h-full w-full" />
      </div>
    </div>
  )
})

function LiveTracePreview({ item, timelineEntries = [], onHighlight, onOpenDetail }) {
  if (!item) {
    return (
      <div className="rounded-xl border border-primary-100/70 bg-white/88 p-5">
        <div className="text-sm font-semibold text-gray-900">活跃预览</div>
        <div className="mt-3">
          <EmptyStateCard title="等待最近请求载入" hint="实时链路启动后会自动展示。" className="p-4" />
        </div>
      </div>
    )
  }

  const { session, trace } = item
  const metrics = buildLiveTraceMetrics(session, trace)
  const summary = trace?.summary ?? {}
  const displayTitle = getSessionDisplayTitle(session, trace)
  const recentTimelineEntries = getLiveTraceTimelineEntries(item, timelineEntries)
  const latestNodes = Array.isArray(trace?.nodes)
    ? [...trace.nodes].sort((a, b) => (b?.ts_ms ?? 0) - (a?.ts_ms ?? 0)).slice(0, 6)
    : []

  return (
    <div className="rounded-xl border border-primary-100/70 bg-white/88 p-5 space-y-4">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-gray-900 break-all">{displayTitle}</div>
          <div className="mt-1 text-xs text-gray-500 font-mono break-all">{session.request_id}</div>
        </div>
        <StatusBadge status={normalizeBadgeStatus(metrics.status)} label={getRecentRequestStatusMeta(metrics.status).label} />
      </div>
      <div className="lifeline-rail h-1.5">
        <div className="lifeline-wave h-full w-full" />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-lg border border-primary-100/70 bg-white/70 p-3">
          <div className="text-xs font-medium text-gray-500">当前节点</div>
          <div className="mt-1 text-sm text-gray-800 break-all">{metrics.currentLabel}</div>
        </div>
        <div className="rounded-lg border border-primary-100/70 bg-white/70 p-3">
          <div className="text-xs font-medium text-gray-500">链路规模</div>
          <div className="mt-1 text-sm text-gray-800">{metrics.nodeCount} 个节点</div>
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
              <div key={`preview-${session.request_id}-${stage.groupId}`} className="inline-flex items-center gap-1.5">
                <span
                  className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium ${className}`}
                  style={style}
                >
                  {stage.label}
                  {stage.count > 0 ? `${stage.count}/${stage.total}` : '0'}
                </span>
                {renderImmuneStageTags(stage, `preview-${session.request_id}-${stage.groupId}`)}
              </div>
            )
          })}
        </div>
      </div>

      <KeyNodeSequence nodes={metrics.keyNodeSequence} />

      <div className="grid grid-cols-1 gap-3">
        <div>
          <div className="text-xs font-medium text-gray-500">输入</div>
          <div className="mt-1 rounded-lg border border-primary-100/70 bg-white/70 p-3 text-sm text-gray-700 break-all">
            {displayTitle}
          </div>
        </div>
        <div>
          <div className="text-xs font-medium text-gray-500">输出</div>
          <div className="mt-1 rounded-lg border border-primary-100/70 bg-white/70 p-3 text-sm text-gray-700 break-all">
            {metrics.outputPreview || '暂无输出，可能仍在执行或本次未产出文本。'}
          </div>
        </div>
      </div>

      <div>
        <div className="text-xs font-medium text-gray-500">最近节点</div>
        <div className="mt-2 space-y-2">
          {latestNodes.length === 0 ? (
            <EmptyStateCard title="暂无节点详情" hint="当前链路还未上报可展示节点。" className="p-4" />
          ) : latestNodes.map((node) => (
            <div key={`${session.request_id}-${node.node_id}-${node.ts_ms}`} className="rounded-lg border border-primary-100/70 bg-white/70 px-3 py-2">
              <div className="flex items-center gap-2">
                <div className="text-sm font-medium text-gray-800">{getTraceNodeDisplayName(node)}</div>
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
      <div className="rounded-xl border border-primary-100/70 bg-white/88 p-5">
        <div className="text-sm font-semibold text-gray-900">活跃预览</div>
        <div className="mt-3">
          <EmptyStateCard title="等待最近请求载入" hint="实时链路启动后会自动展示。" className="p-4" />
        </div>
      </div>
    )
  }

  const { session, trace } = item
  const metrics = buildLiveTraceMetrics(session, trace)
  const summary = trace?.summary ?? {}
  const displayTitle = getSessionDisplayTitle(session, trace)
  const recentTimelineEntries = getLiveTraceTimelineEntries(item, timelineEntries)

  return (
    <div className="rounded-xl border border-primary-100/70 bg-white/88 p-5 space-y-4">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-gray-900 break-all">{displayTitle}</div>
          <div className="mt-1 text-xs text-gray-500 font-mono break-all">{session.request_id}</div>
        </div>
        <StatusBadge status={normalizeBadgeStatus(metrics.status)} label={getRecentRequestStatusMeta(metrics.status).label} />
      </div>
      <div className="lifeline-rail h-1.5">
        <div className="lifeline-wave h-full w-full" />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-lg border border-primary-100/70 bg-white/70 p-3">
          <div className="text-xs font-medium text-gray-500">当前节点</div>
          <div className="mt-1 text-sm text-gray-800 break-all">{metrics.currentLabel}</div>
        </div>
        <div className="rounded-lg border border-primary-100/70 bg-white/70 p-3">
          <div className="text-xs font-medium text-gray-500">链路规模</div>
          <div className="mt-1 text-sm text-gray-800">{metrics.nodeCount} 个节点</div>
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
              <div key={`preview-stream-${session.request_id}-${stage.groupId}`} className="inline-flex items-center gap-1.5">
                <span
                  className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium ${className}`}
                  style={style}
                >
                  {stage.label}
                  {stage.count > 0 ? `${stage.count}/${stage.total}` : '0'}
                </span>
                {renderImmuneStageTags(stage, `preview-stream-${session.request_id}-${stage.groupId}`)}
              </div>
            )
          })}
        </div>
      </div>

      <KeyNodeSequence nodes={metrics.keyNodeSequence} />

      <div className="grid grid-cols-1 gap-3">
        <div>
          <div className="text-xs font-medium text-gray-500">输入</div>
          <div className="mt-1 rounded-lg border border-primary-100/70 bg-white/70 p-3 text-sm text-gray-700 break-all">
            {displayTitle}
          </div>
        </div>
        <div>
          <div className="text-xs font-medium text-gray-500">输出</div>
          <div className="mt-1 rounded-lg border border-primary-100/70 bg-white/70 p-3 text-sm text-gray-700 break-all">
            {metrics.outputPreview || '暂无输出，可能仍在执行或这次没有文本结果。'}
          </div>
        </div>
      </div>

      <div>
        <div className="text-xs font-medium text-gray-500">最新节点变化</div>
        <div className="mt-2 space-y-2">
          {recentTimelineEntries.length === 0 ? (
            <EmptyStateCard title="暂无节点变化" hint="等待节点事件进入时间线。" className="p-4" />
          ) : recentTimelineEntries.map((entry) => (
            <div key={entry.entryId} className="rounded-lg border border-primary-100/70 bg-white/70 px-3 py-2">
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
  immune: '#EA580C',
  llm: '#F79009', tts: '#12B76A', vts: '#EE46BC',
  recall: '#0891B2', retrieval: '#0891B2', bond: '#D97706',
  asr: '#059669', system: '#475467', agent: '#7C3AED',
  skill: '#0891B2', router: '#475467', judge: '#059669',
  edge: '#667085', branch: '#0891B2', default: '#667085',
}
function nodeColor(t) { return NODE_TYPE_COLOR[t] ?? NODE_TYPE_COLOR.default }

function GanttView({ traceData, onSelectNode }) {
  const timeline = React.useMemo(() => computeGanttTimeline(traceData), [traceData])
  const [filter, setFilter] = React.useState('')
  if (timeline.length === 0) return null
  const totalMs = timeline[0]?.total_ms ?? 1
  const filtered = filter.trim()
    ? timeline.filter((node) => {
      const searchText = `${getTraceNodeDisplayName(node)} ${node.node_id || ''}`.toLowerCase()
      return searchText.includes(filter.toLowerCase())
    })
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
              title={`${getTraceNodeDisplayName(node)}: ${node.start_ms}ms → ${node.end_ms}ms (${node.latency_ms ?? 0}ms) [${node.status ?? '未知'}]`}
            >
              <div className="w-36 shrink-0">
                <div className="text-xs font-medium text-gray-700 truncate">{getTraceNodeDisplayName(node)}</div>
                <div className="text-xs text-gray-400">{node.latency_ms ?? 0}ms</div>
              </div>
              <div className="flex-1 relative h-6 bg-gray-100 rounded overflow-hidden">
                <div className="absolute top-0 h-full rounded"
                  title={`${node.latency_ms ?? 0}ms`}
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

  if (!group) return <EmptyStateCard title="分组不存在" hint="请返回链路总览重新选择分组。" className="p-8" />

  const Icon = group.icon
  const activeCount = traceGroupState ? traceGroupState.activeCount : 0
  const totalNodeCount = traceData ? getTraceGroupNodeIds(group).length : group.nodeIds.length
  const immuneGroupMeta = group.id === 'immune'
    ? buildImmuneStageMeta(traceGroupState?.groupNodes ?? [])
    : null

  return (
    <div className="flex flex-col h-full flex-1">
      {/* 子页面头部 */}
      <div className="card mb-4 space-y-3 p-4">
        <div className="flex items-center gap-3 flex-wrap">
          <button
            className="btn-secondary text-xs"
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
          {/* 视图切换 */}
          {traceData && (
            <div className="flex rounded-lg border border-primary-100/80 overflow-hidden text-xs">
              <button className={`flex items-center gap-1 px-2.5 py-1.5 transition-colors ${viewMode === 'dag' ? 'bg-blue-600 text-white' : 'bg-white/90 text-gray-600 hover:bg-blue-50'}`}
                onClick={() => setViewMode('dag')}>
                <GitBranch size={11} /> 拓扑图
              </button>
              <button className={`flex items-center gap-1 px-2.5 py-1.5 transition-colors ${viewMode === 'gantt' ? 'bg-blue-600 text-white' : 'bg-white/90 text-gray-600 hover:bg-blue-50'}`}
                onClick={() => setViewMode('gantt')}>
                <BarChart2 size={11} /> 甘特图
              </button>
            </div>
          )}
        </div>
        <div className="lifeline-rail h-1.5">
          <div className="lifeline-wave h-full w-full" />
        </div>
      </div>

      {/* 主体 */}
      <div className="flex gap-4 flex-1 min-h-0" style={{ height: 'calc(100vh - 260px)' }}>
        <div className="flex-1 card overflow-hidden h-full"
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

  const overviewSchema = React.useMemo(() => getObservedTraceSchema(traceData), [traceData])

  useEffect(() => {
    const { nodes: fn, edges: fe } = buildFlowData(
      overviewSchema.nodes,
      overviewSchema.edges,
      handleSelect,
      overviewSchema.activeNodeIds,
      overviewSchema.traceNodeMap,
    )
    setNodes(fn)
    setEdges(fe)
  }, [overviewSchema, handleSelect, setNodes, setEdges])

  return (
    <div className="flex flex-col h-full flex-1">
      <div className="card mb-4 space-y-3 p-4">
        <div className="flex items-center gap-3 flex-wrap">
        <button className="btn-secondary text-xs"
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
        <div className="lifeline-rail h-1.5">
          <div className="lifeline-wave h-full w-full" />
        </div>
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

  const overviewSchema = React.useMemo(() => getObservedTraceSchema(traceData), [traceData])

  useEffect(() => {
    const { nodes: fn, edges: fe } = buildFlowData(
      overviewSchema.nodes,
      overviewSchema.edges,
      handleSelect,
      overviewSchema.activeNodeIds,
      overviewSchema.traceNodeMap,
    )
    setNodes(fn)
    setEdges(fe)
  }, [overviewSchema, handleSelect, setNodes, setEdges])

  if (!traceData) return null

  const currentStatus = String(traceData.status || '').toUpperCase()
  const currentHealthy = ['OK', 'UP', 'SUCCESS'].includes(currentStatus)
  const flow = getLiveTraceFlow(traceData)
  const activeNodeSet = new Set(overviewSchema.activeNodeIds ?? [])
  const nodeCount = activeNodeSet.size
  const edgeCount = overviewSchema.edges.filter((edge) => activeNodeSet.has(edge.source) && activeNodeSet.has(edge.target)).length
  const rawNodeCount = overviewSchema.rawNodeCount

  return (
    <div className="card rounded-2xl p-4 md:p-5">
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
          const className = getLiveTraceStageClasses(stage, group)
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
              {renderImmuneStageTags(stage, `overview-${traceData.request_id}-${stage.groupId}`)}
            </div>
          )
        })}
      </div>

      <div className="mt-4 grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_22rem] gap-4">
        <div className="rounded-xl border border-primary-100/70 overflow-hidden bg-white/80" style={{ height: '34rem' }}>
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
          <div className="rounded-xl border border-primary-100/70 bg-white/80 p-4">
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

function TraceHome({ traceData, setTraceData, traceInfo, setTraceInfo, loading, setLoading, error, setError }) {
  const [inputId, setInputId] = useState('')
  const [recentRequests, setRecentRequests] = useState([])
  const [recentLoading, setRecentLoading] = useState(false)
  const [recentKeyword, setRecentKeyword] = useState('')
  const [recentStatusFilter, setRecentStatusFilter] = useState('')
  const [historyExpanded, setHistoryExpanded] = useState(false)
  const [inlineOverviewVisible, setInlineOverviewVisible] = useState(false)
  const [advancedViewsExpanded, setAdvancedViewsExpanded] = useState(false)
  const [liveTraceItems, setLiveTraceItems] = useState([])
  const [liveTraceTimelineEntries, setLiveTraceTimelineEntries] = useState({})
  const [liveTraceLoading, setLiveTraceLoading] = useState(false)
  const [liveTraceError, setLiveTraceError] = useState(null)
  const [liveFeedConnected, setLiveFeedConnected] = useState(false)
  const [liveFeedLastTs, setLiveFeedLastTs] = useState(null)
  const [tribeEvents, setTribeEvents] = useState([])
  const [tribeEventsLoading, setTribeEventsLoading] = useState(false)
  const [tribeEventsError, setTribeEventsError] = useState(null)
  const inlineOverviewRef = useRef(null)
  const navigate = useNavigate()
  const pollingEnabled = useAppStore((s) => s.pollingEnabled)
  const pollingInterval = useAppStore((s) => s.pollingInterval)
  const selectedRequestId = useAppStore((s) => s.selectedRequestId)
  const setSelectedRequestId = useAppStore((s) => s.setSelectedRequestId)
  const { runtimeSnapshot, promptStats: promptBuilderData } = useDigitalLifeRuntimeSignals({
    pollingEnabled,
    pollingInterval,
    windowMinutes: 60,
    minIntervalSeconds: 10,
  })

  const load = useCallback(async (id, options = {}) => {
    if (!id?.trim()) return
    const showOverview = options.showOverview ?? true
    setLoading(true)
    setError(null)
    try {
      const data = await fetchTraceNodes(id)
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
        status: recentStatusFilter || undefined,
      })
      setRecentRequests(sanitizeRecentRequests(data.items, { keyword }))
      setRecentKeyword(keyword)
    } catch (fetchError) {
      console.warn('[Trace] failed to load recent requests:', fetchError)
      setRecentRequests([])
    } finally {
      setRecentLoading(false)
    }
  }, [recentStatusFilter])

  useEffect(() => {
    loadRecentRequests()
  }, [loadRecentRequests])

  useEffect(() => {
    if (!pollingEnabled) return
    const timer = setInterval(() => loadRecentRequests(recentKeyword), pollingInterval * 1000)
    return () => clearInterval(timer)
  }, [pollingEnabled, pollingInterval, loadRecentRequests, recentKeyword])

  const loadTribeEvents = useCallback(async () => {
    setTribeEventsLoading(true)
    try {
      const selectedSession =
        liveTraceItems.find((item) => item?.session?.request_id === selectedRequestId)?.session
        ?? recentRequests.find((item) => item?.request_id === selectedRequestId)
        ?? null
      const sessionId = String(
        selectedSession?.session_id
        ?? traceData?.summary?.session_id
        ?? ''
      ).trim()
      const conversationId = String(
        selectedSession?.conversation_id
        ?? traceData?.summary?.conversation_id
        ?? ''
      ).trim()
      const params = { limit: selectedRequestId ? 200 : 60 }
      if (selectedRequestId) {
        params.request_id = selectedRequestId
      } else if (sessionId) {
        params.session_id = sessionId
      } else if (conversationId) {
        params.conversation_id = conversationId
      }
      const data = await fetchTribeEvents(params)
      setTribeEvents(Array.isArray(data?.items) ? data.items : [])
      setTribeEventsError(null)
    } catch (loadError) {
      setTribeEvents([])
      setTribeEventsError(loadError.message || 'Tribe 事件加载失败')
    } finally {
      setTribeEventsLoading(false)
    }
  }, [liveTraceItems, recentRequests, selectedRequestId, traceData])

  useEffect(() => {
    loadTribeEvents()
  }, [loadTribeEvents])

  useEffect(() => {
    if (!pollingEnabled) return
    const timer = setInterval(loadTribeEvents, Math.max(pollingInterval, 10) * 1000)
    return () => clearInterval(timer)
  }, [pollingEnabled, pollingInterval, loadTribeEvents])

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
            const trace = await fetchTraceNodes(session.request_id)
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
          active_within_ms: 60000,
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
            // SSE 可能返回空快照（例如当前无“活跃”窗口），此时保留已有列表，
            // 后续再由 fallback 拉取 recentRequests 对应链路，避免首页长时间空载。
            if (items.length > 0) {
              setLiveTraceItems(items)
            }
            setLiveTraceTimelineEntries((previous) => {
              if (items.length === 0) return previous
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
            setLiveTraceError(payload?.detail || '实时链路流异常')
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
    if (liveTraceLoading) return
    // SSE 已连接但快照为空时也需要兜底拉取，避免“已连接但无卡片”。
    if (liveFeedConnected && liveTraceItems.length > 0) return
    loadLiveTraces()
  }, [liveFeedConnected, liveTraceItems.length, liveTraceLoading, loadLiveTraces])

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
    const immuneMeta = g.id === 'immune'
      ? buildImmuneStageMeta(traceGroupState?.groupNodes ?? [])
      : null
    return { ...g, activeCount, totalNodeCount, hasError, immuneMeta }
  })

  const openRequestTrace = (requestId) => {
    setSelectedRequestId(requestId)
    navigate(`/trace/${requestId}`)
  }

  const latestRequest = recentRequests[0] ?? null
  const searchValue = inputId.trim()
  const searchLooksLikeRequestId = searchValue.startsWith('req_')

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
    ? 'border-primary-200 bg-primary-50/45 text-primary-700'
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
  const selectedSessionFallback = selectedLiveTraceItem?.session
    ?? recentRequests.find((item) => item.request_id === selectedRequestId)
    ?? (selectedRequestId ? { request_id: selectedRequestId } : null)
  const selectedTraceTitleSource = selectedLiveTraceItem?.trace
    ?? (traceData?.request_id === selectedRequestId ? traceData : null)
  const selectedLiveTraceTitle = selectedRequestId
    ? getSessionDisplayTitle(selectedSessionFallback, selectedTraceTitleSource)
    : null
  const selectedLiveTraceTimelineEntries = selectedLiveTraceItem?.session?.request_id
    ? (liveTraceTimelineEntries[selectedLiveTraceItem.session.request_id] ?? [])
    : []
  const selectedSessionId = String(
    selectedSessionFallback?.session_id
    ?? selectedTraceTitleSource?.summary?.session_id
    ?? ''
  ).trim()
  const selectedConversationId = String(
    selectedSessionFallback?.conversation_id
    ?? selectedTraceTitleSource?.summary?.conversation_id
    ?? ''
  ).trim()
  const linkedTribeEvents = selectedRequestId
    ? tribeEvents.filter((event) => isTribeEventLinkedToSelection(event, {
      requestId: selectedRequestId,
      sessionId: selectedSessionId,
      conversationId: selectedConversationId,
    }))
    : []
  const linkedTribeEventList = linkedTribeEvents.slice(0, 20)
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
    ? '当前没有活跃链路，自动回退展示历史请求（已默认隐藏测试会话与纯问号占位消息）。'
    : '历史请求默认收起；需要回看刚才跑过什么时，再从这里展开。'
  const canCollapseHistory = historyExpanded && !shouldAutoShowHistory
  const selectedTraceStageMeta = getTraceActiveStageMeta(traceData)
  const selectedIssueCount = getTraceIssueCount(traceData)
  const selectedRunningCount = getTraceRunningCount(traceData)
  const selectedCurrentNodeLabel = traceData ? getLiveTraceCurrentLabel(traceData) : '等待选中请求'
  const selectedOutputPreview = traceData ? getLiveTraceOutputPreview(traceData) : ''
  const liveModeLabel = !pollingEnabled ? '实时暂停' : liveFeedConnected ? 'SSE 实时流' : '轮询兜底'
  const liveModeCaption = liveFeedLastTs
    ? `最近事件 ${formatRecentTraceTime(liveFeedLastTs)}`
    : (liveTraceItems.length > 0 ? `${liveTraceItems.length} 条活跃请求` : '等待活跃请求')
  const tracePulseNodes = [
    {
      label: '当前请求',
      value: selectedLiveTraceTitle || '等待选中',
      tone: 'primary',
      active: Boolean(selectedLiveTraceTitle),
    },
    {
      label: '当前节点',
      value: selectedCurrentNodeLabel,
      tone: 'teal',
      active: Boolean(selectedCurrentNodeLabel && selectedCurrentNodeLabel !== '等待选中请求'),
    },
    {
      label: '阶段覆盖',
      value: `${selectedTraceStageMeta.activeStageCount}/${GROUPS.length}`,
      tone: 'violet',
      active: selectedTraceStageMeta.activeStageCount > 0,
    },
    {
      label: '实时模式',
      value: liveModeLabel,
      tone: 'amber',
      active: pollingEnabled,
    },
    {
      label: '联动事件',
      value: `${linkedTribeEvents.length} 条`,
      tone: 'rose',
      active: linkedTribeEvents.length > 0,
    },
  ]

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
      <PageHero
        icon={Activity}
        eyebrow="神经传导 Trace"
        title="数字生命体神经链路可视化"
        description="从感官输入、检索规划、人格上下文到响应输出，统一查看当前请求的完整神经传导路径。"
        gradientClass="from-blue-50 via-white to-violet-50"
        metaItems={[
          `活跃请求：${liveTraceItems.length}`,
          `当前节点：${traceInfo?.nodeCount ?? 0}`,
          selectedRequestId ? '已选中请求' : '未选中请求',
          currentTraceHealthy ? '当前链路：健康' : '当前链路：待关注',
        ]}
      />

      <LivingPulseStrip
        title="链路脉冲"
        subtitle="先看这一条神经流现在怎么流动，再下钻到单条请求或节点细节。"
        nodes={tracePulseNodes}
      />

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-5">
        <HomeSummaryCard
          icon={Bookmark}
          title="当前请求"
          value={selectedLiveTraceTitle || '等待选中请求'}
          caption={selectedRequestId || '从下方活跃请求流选择一条 request'}
        />
        <HomeSummaryCard
          icon={Layers}
          title="阶段覆盖"
          value={`${selectedTraceStageMeta.activeStageCount}/${GROUPS.length}`}
          caption={`当前阶段：${selectedTraceStageMeta.currentStageLabel}`}
        />
        <HomeSummaryCard
          icon={Shield}
          title="待关注节点"
          value={selectedIssueCount > 0 ? `${selectedIssueCount} 个异常/降级` : '无异常节点'}
          caption={selectedRunningCount > 0 ? `运行中 ${selectedRunningCount} 个节点` : (traceInfo?.latency != null ? `总耗时 ${traceInfo.latency}ms` : '等待节点继续推进')}
          toneClassName={selectedIssueCount > 0 ? 'text-orange-600' : 'text-emerald-600'}
        />
        <HomeSummaryCard
          icon={Activity}
          title="当前节点"
          value={selectedCurrentNodeLabel}
          caption={selectedOutputPreview || '暂无输出摘要，可能仍在执行或尚未落盘。'}
        />
        <HomeSummaryCard
          icon={Clock3}
          title="实时模式"
          value={liveModeLabel}
          caption={liveModeCaption}
          toneClassName={!pollingEnabled ? 'text-gray-700' : liveFeedConnected ? 'text-emerald-600' : 'text-amber-600'}
        />
      </div>

      <TraceSection
        title="链路控制台"
        hint="筛选请求、查看当前会话和 Tribe 联动事件。"
      >
      <div className="flex items-center gap-3 flex-wrap">
        <h1 className="text-xl font-semibold text-gray-900 shrink-0">链路可视化</h1>
        <div className="flex items-center gap-2 flex-1 min-w-64">
          <div className="relative flex-1">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              className="w-full rounded-lg border border-primary-100/70 bg-white/92 py-1.5 pl-8 pr-3 font-mono text-sm focus:border-blue-300 focus:outline-none focus:ring-2 focus:ring-primary-200"
              placeholder="输入消息内容、session_id 或 request_id"
              value={inputId}
              onChange={(e) => setInputId(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleTraceSearch()}
            />
          </div>
          <button className="btn-primary shrink-0" onClick={handleTraceSearch} disabled={loading || recentLoading}>
            {(loading || recentLoading) ? <RefreshCw size={13} className="animate-spin" /> : (searchLooksLikeRequestId ? '显示总览图' : '筛选请求')}
          </button>
          <select
            className="shrink-0 rounded-lg border border-primary-100/70 bg-white/92 px-2 py-1.5 text-sm focus:border-blue-300 focus:outline-none focus:ring-2 focus:ring-primary-200"
            value={recentStatusFilter}
            onChange={(e) => { setRecentStatusFilter(e.target.value); loadRecentRequests(recentKeyword) }}
          >
            <option value="">全部状态</option>
            <option value="ok">成功</option>
            <option value="rejected">拒绝</option>
            <option value="error">错误</option>
          </select>
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
        {error && (
          <NoticeBanner variant="error" title="链路查询异常" className="w-full md:w-auto">
            {error}
          </NoticeBanner>
        )}
      </div>

      {runtimeSnapshot && (
        <DigitalLifeRuntimeCard
          runtimeSnapshot={runtimeSnapshot}
          promptStats={promptBuilderData}
          compact
        />
      )}

      {selectedRequestId && (
        <div className="card flex items-center gap-3 flex-wrap px-4 py-3">
          <div className="min-w-0">
            <div className="text-xs font-medium text-blue-700">当前会话</div>
            <div className="text-sm font-semibold text-blue-900 break-all">{selectedLiveTraceTitle}</div>
            <div className="mt-1 font-mono text-xs text-blue-800 break-all">{selectedRequestId}</div>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <button className="btn-secondary text-xs" onClick={() => focusRequestOverview(selectedRequestId)} disabled={loading}>
              显示总览图
            </button>
            <button className="btn-primary text-xs" onClick={() => openRequestTrace(selectedRequestId)}>
              打开详情
            </button>
          </div>
          <div className="lifeline-rail h-1.5 w-full">
            <div className="lifeline-wave h-full w-full" />
          </div>
        </div>
      )}

      {selectedRequestId && (
        <div className="card rounded-2xl p-4 md:p-5">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div className="min-w-0">
              <h2 className="text-sm font-semibold text-emerald-900">Tribe 事件联动</h2>
              <p className="text-xs text-emerald-700/80 mt-1">
                当前请求关联：{linkedTribeEvents.length} 条（request/session/conversation 维度匹配）
              </p>
            </div>
            <div className="flex items-center gap-2">
              <button
                className="btn-secondary text-xs"
                onClick={loadTribeEvents}
                disabled={tribeEventsLoading}
              >
                <RefreshCw size={12} className={tribeEventsLoading ? 'animate-spin' : ''} />
                刷新事件
              </button>
              <button
                className="btn-secondary text-xs"
                onClick={() => navigate('/insights/safety')}
              >
                打开 Insights
              </button>
            </div>
          </div>
          <div className="lifeline-rail mt-3 h-1.5">
            <div className="lifeline-wave h-full w-full" />
          </div>

          <div className="mt-3 flex items-center gap-3 text-xs text-emerald-800/90 flex-wrap">
            <span>request_id: {selectedRequestId}</span>
            {selectedSessionId ? <span>session_id: {selectedSessionId}</span> : null}
            {selectedConversationId ? <span>conversation_id: {selectedConversationId}</span> : null}
          </div>

          {tribeEventsError && (
            <NoticeBanner variant="error" title="Tribe 事件读取失败" className="mt-3">
              {tribeEventsError}
            </NoticeBanner>
          )}

          {!tribeEventsLoading && !tribeEventsError && linkedTribeEventList.length === 0 && (
            <EmptyStateCard
              title="当前请求暂无 Tribe 事件"
              hint="若请求刚开始执行，可稍后刷新；也可到 Insights 查看全局事件窗口。"
              className="mt-3 p-4 bg-white"
            />
          )}

          {linkedTribeEventList.length > 0 && (
            <div className="mt-3 space-y-3">
              {linkedTribeEventList.map((event) => {
                const payloadPreview = formatEventPayloadPreview(event?.payload, 220)
                return (
                  <div key={event.event_id} className="rounded-2xl border border-primary-100/70 bg-white/80 p-4 shadow-sm">
                    <div className="flex items-start gap-3">
                      <div className="mt-1 h-2.5 w-2.5 shrink-0 rounded-full bg-emerald-400 ring-4 ring-emerald-50" />
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <div className="text-sm font-semibold text-emerald-900">
                            {event.event_type}
                          </div>
                          <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700">
                            {formatRecentTraceTime(event.ts_ms)}
                          </span>
                        </div>
                        <div className="mt-2 flex flex-wrap gap-2 text-xs">
                          <span className="rounded-full border border-blue-100 bg-blue-50 px-2 py-1 font-medium text-blue-700">
                            source {event.source_agent || '—'}
                          </span>
                          <span className="rounded-full border border-purple-100 bg-purple-50 px-2 py-1 font-medium text-purple-700">
                            target {event.target_agent || '—'}
                          </span>
                          {event.request_id ? (
                            <span className="rounded-full border border-primary-200 bg-primary-50/55 px-2 py-1 font-mono text-primary-700">
                              {String(event.request_id).slice(0, 18)}
                            </span>
                          ) : null}
                        </div>
                        <div className="mt-3 break-all rounded-xl border border-primary-100/70 bg-white/86 px-3 py-2 text-xs leading-5 text-gray-600">
                          {payloadPreview}
                        </div>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}
      </TraceSection>

      <div className="card relative overflow-hidden p-4 md:p-5">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_left,_rgba(41,112,255,0.05),_transparent_38%),radial-gradient(circle_at_bottom_right,_rgba(16,185,129,0.05),_transparent_34%)]" />
        <div className="relative">
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

        <div className="lifeline-rail mt-3 h-4 px-2">
          <div className="lifeline-wave absolute inset-0 opacity-65" />
          <div className="relative flex h-full items-center justify-around">
            {[0, 1, 2, 3].map((index) => (
              <span key={`active-feed-${index}`} className="lifeline-node" style={{ animationDelay: `${index * 0.24}s` }} />
            ))}
          </div>
        </div>

        <div className="mt-4 grid grid-cols-1 xl:grid-cols-[minmax(0,1.35fr)_24rem] gap-4">
          <div className="rounded-2xl border border-primary-100/70 bg-white/88 p-3 shadow-sm shadow-blue-100/40">
            <div className="flex items-center justify-between gap-3 border-b border-primary-100/70 px-1 pb-3">
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
                <NoticeBanner variant="info" title="加载中" className="px-4 py-8">
                  正在加载最近请求的链路详情…
                </NoticeBanner>
              )}
              {!liveTraceLoading && liveTraceError && liveTraceItems.length === 0 && (
                <NoticeBanner variant="error" title="活跃链路流加载失败" className="px-4 py-8">
                  {liveTraceError}
                </NoticeBanner>
              )}
              {!liveTraceLoading && !liveTraceError && liveTraceItems.length === 0 && (
                <EmptyStateCard title="暂无可展示请求链路" hint="有新的请求后会自动显示在这里。" className="p-6" />
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

          <div className="rounded-2xl border border-primary-100/70 bg-white/88 p-3 shadow-sm shadow-blue-100/40">
            <div className="border-b border-primary-100/70 px-1 pb-3">
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
          className="card w-full rounded-xl border-dashed border-primary-200 bg-white/85 px-4 py-4 text-left transition-colors hover:border-primary-300 hover:bg-primary-50/35"
          onClick={() => setHistoryExpanded(true)}
        >
          <div className="flex items-center gap-3">
            <span className="flex items-center justify-center w-9 h-9 rounded-xl border border-primary-100/70 bg-white text-blue-600">
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
        <div className="card overflow-hidden rounded-xl">
          <div className="flex items-center gap-2 border-b border-primary-100/70 bg-white/80 px-4 py-3 flex-wrap">
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
          <div className="divide-y divide-primary-100/70">
            {recentLoading && recentRequests.length === 0 && (
              <div className="px-4 py-4">
                <NoticeBanner variant="info" title="加载中">
                  正在加载历史请求…
                </NoticeBanner>
              </div>
            )}
            {!recentLoading && recentRequests.length === 0 && (
              <div className="px-4 py-4">
                <EmptyStateCard title="暂无历史请求" hint="链路请求完成后会在这里沉淀。" className="p-4" />
              </div>
            )}
            {recentRequests.map((item) => {
              const statusMeta = getRecentRequestStatusMeta(item.status)
              return (
                <button
                  key={item.request_id}
                  className="w-full px-4 py-3 text-left transition-colors hover:bg-primary-50/45"
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

      <div className="card overflow-hidden rounded-2xl">
        <button
          className="w-full px-4 py-4 text-left transition-colors hover:bg-primary-50/45"
          onClick={() => setAdvancedViewsExpanded((value) => !value)}
        >
          <div className="flex items-center gap-3">
            <span className="flex items-center justify-center w-9 h-9 rounded-xl border border-primary-100/70 bg-white text-blue-600">
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
          <div className="space-y-4 border-t border-primary-100/70 bg-white/75 p-4">
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
                    {g.id === 'immune' && (
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {g.immuneMeta?.decision || g.immuneMeta?.riskLevel ? (
                          <span className="inline-flex items-center rounded-full border border-orange-200 bg-orange-50 px-2 py-0.5 text-[11px] font-medium text-orange-700">
                            {(g.immuneMeta?.decision || 'IMMUNE').toUpperCase()}
                            {g.immuneMeta?.riskLevel ? `/${String(g.immuneMeta.riskLevel).toLowerCase()}` : ''}
                          </span>
                        ) : null}
                        {(g.immuneMeta?.reasonTags || []).slice(0, 2).map((reasonCode) => (
                          <span key={`${g.id}-${reasonCode}`} className="inline-flex items-center rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700">
                            {reasonCode}
                          </span>
                        ))}
                      </div>
                    )}
                    {traceData && (
                      <div className="mt-3 h-1 overflow-hidden rounded-full bg-primary-100/70">
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
                className="flex w-full items-center gap-4 rounded-xl border-2 border-dashed border-primary-200 p-4 text-left transition-all hover:border-blue-400 hover:bg-blue-50"
                onClick={() => navigate('/trace/full')}
              >
                <span className="flex items-center justify-center w-10 h-10 rounded-lg bg-blue-50 text-blue-600 shrink-0">
                  <Layers size={20} />
                </span>
                <div className="flex-1">
                  <div className="font-semibold text-gray-800 text-sm">双端全链路拓扑图</div>
                  <div className="text-xs text-gray-500 mt-0.5">需要单独放大排查静态拓扑图时再进入。</div>
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
                  <div key={label} className="rounded-xl border border-primary-100/70 bg-white/90 p-4 text-center">
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

function normalizeOptionalKeyValue(payload, keys) {
  if (!payload || typeof payload !== 'object') return ''
  for (const key of keys) {
    const value = payload[key]
    if (value == null) continue
    const normalized = String(value).trim()
    if (normalized) return normalized
  }
  return ''
}

function isTribeEventLinkedToSelection(event, { requestId, sessionId, conversationId }) {
  const payload = event?.payload ?? {}
  const payloadRequestId = normalizeOptionalKeyValue(payload, ['request_id', 'requestId'])
  const payloadSessionId = normalizeOptionalKeyValue(payload, ['session_id', 'sessionId'])
  const payloadConversationId = normalizeOptionalKeyValue(payload, ['conversation_id', 'conversationId'])

  if (requestId && payloadRequestId === requestId) return true
  if (sessionId && payloadSessionId === sessionId) return true
  if (conversationId && payloadConversationId === conversationId) return true
  return false
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
