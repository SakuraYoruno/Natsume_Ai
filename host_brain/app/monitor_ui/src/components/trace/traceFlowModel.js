import {
  ArrowRightLeft,
  Bookmark,
  Brain,
  Filter,
  Mic,
  Shield,
  Users,
  Zap,
} from 'lucide-react'

export const GROUPS = [
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
    desc: '响应策略 · Cortex Orchestrator',
    icon: Shield,
    color: '#7C3AED',
    bg: '#F5F3FF',
    border: '#C4B5FD',
    nodeIds: ['render_policy', 'cortex_orchestrator'],
    traceNodeIds: ['render_policy', 'cortex_orchestrator'],
  },
  {
    id: 'agent',
    label: '代理调度',
    desc: 'Executive Router · Sub Agent · Chat Agent',
    icon: Users,
    color: '#9333EA',
    bg: '#FAF5FF',
    border: '#D8B4FE',
    nodeIds: ['executive_router', 'sub_agent_code', 'sub_agent_translate', 'sub_agent_intent', 'sub_agent_vision', 'chat_agent'],
    traceNodeIds: ['executive_router', 'sub_agent_code', 'sub_agent_translate', 'sub_agent_intent', 'sub_agent_vision', 'chat_agent'],
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
    traceNodeIds: ['prompt_builder', 'llm_stream', 'llm_gateway', 'cortex_orchestrator_llm_stream'],
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

export const FULL_CHAIN_SCHEMA = {
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
    { node_id: 'render_policy', node_type: 'system', display_name: '响应策略', layer: 'host' },
    { node_id: 'cortex_orchestrator', node_type: 'system', display_name: 'Cortex Orchestrator', layer: 'host' },
    { node_id: 'executive_router', node_type: 'agent', display_name: 'Executive Router', layer: 'host' },
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
    { source: 'motive_state', target: 'render_policy' },
    { source: 'render_policy', target: 'cortex_orchestrator' },
    { source: 'cortex_orchestrator', target: 'executive_router' },
    { source: 'cortex_orchestrator', target: 'prompt_builder' },
    { source: 'executive_router', target: 'sub_agent_code' },
    { source: 'executive_router', target: 'sub_agent_translate' },
    { source: 'executive_router', target: 'sub_agent_intent' },
    { source: 'executive_router', target: 'sub_agent_vision' },
    { source: 'executive_router', target: 'chat_agent' },
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
const LIVE_TRACE_GROUP_ORDER = ['edge', 'entry', 'immune', 'retrieval', 'persona', 'orchestration', 'agent', 'llm', 'postprocess']

export function getTraceGroupNodeIds(group) {
  return group?.traceNodeIds ?? group?.nodeIds ?? []
}

export function normalizeTraceGroupNodeId(nodeId = '', nodeType = '') {
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
  if (normalized === 'llm_gateway' || normalized === 'cortex_orchestrator_llm_stream') return 'llm_stream'
  if (normalized.startsWith('retrieval_') || normalized === 'recall_fusion') return 'recall_fusion'
  return normalized
}

export function normalizeBadgeStatus(status) {
  const value = String(status || '').toUpperCase()
  if (['OK', 'UP', 'SUCCESS', 'APPROVE', 'APPROVED', 'APPLIED', 'ACCEPTED', 'RECEIVED', 'DISPATCHED', 'QUEUED', 'SENT', 'SKIPPED'].includes(value)) return 'healthy'
  if (['RUNNING', 'STARTED'].includes(value)) return 'running'
  if (['DEGRADED'].includes(value)) return 'degraded'
  if (['FAIL', 'FAILED', 'ERROR', 'DOWN', 'REJECTED', 'REJECT'].includes(value)) return 'error'
  return 'unknown'
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

export function getTraceGroupState(group, traceData) {
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

export function getObservedTraceSchema(traceData) {
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

export function getLatestTraceNode(traceData) {
  const nodes = Array.isArray(traceData?.nodes) ? [...traceData.nodes] : []
  if (nodes.length === 0) return null
  nodes.sort((a, b) => (a?.ts_ms ?? 0) - (b?.ts_ms ?? 0))
  return nodes[nodes.length - 1]
}

export function buildImmuneStageMeta(groupNodes = []) {
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

export function getLatestPrimaryTraceNode(traceData) {
  const nodes = Array.isArray(traceData?.nodes) ? [...traceData.nodes] : []
  if (nodes.length === 0) return null
  nodes.sort((a, b) => (a?.ts_ms ?? 0) - (b?.ts_ms ?? 0))
  for (let index = nodes.length - 1; index >= 0; index -= 1) {
    if (!isOptionalTraceNode(nodes[index])) return nodes[index]
  }
  return nodes[nodes.length - 1]
}

export function getLiveTraceFlow(traceData) {
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

