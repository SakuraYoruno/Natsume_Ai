import { buildImmuneStageMeta } from './traceImmuneModel.js'
import {
  FULL_CHAIN_NODE_ID_SET,
  FULL_CHAIN_NODE_MAP,
  FULL_CHAIN_SCHEMA,
  GROUPS,
  LIVE_TRACE_GROUP_ORDER,
} from './traceFlowSchema.js'

export { FULL_CHAIN_SCHEMA, GROUPS } from './traceFlowSchema.js'
export { buildImmuneStageMeta } from './traceImmuneModel.js'


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
