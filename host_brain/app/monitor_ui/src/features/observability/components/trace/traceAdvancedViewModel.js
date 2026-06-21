import { MarkerType } from '@xyflow/react'
import { applyDagreLayout } from '../../../../utils/traceLayout.js'
import { getTraceNodeDisplayName } from './traceNodeText.js'
import { resolveTraceNodeTypeColor } from './traceVisualMeta.js'
import {
  FULL_CHAIN_SCHEMA,
  buildImmuneStageMeta,
  getTraceGroupNodeIds,
} from './traceFlowModel.js'

export function buildTraceAdvancedFlowData(schemaNodes, schemaEdges, onSelect, activeNodeIds = null, traceNodeMap = {}) {
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

export function computeTraceGanttTimeline(traceData) {
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

export function filterTraceGanttTimeline(timeline = [], filter = '') {
  const keyword = String(filter || '').trim().toLowerCase()
  if (!keyword) return timeline
  return timeline.filter((node) => {
    const searchText = `${getTraceNodeDisplayName(node)} ${node.node_id || ''}`.toLowerCase()
    return searchText.includes(keyword)
  })
}

export function buildTraceGanttTicks(totalMs) {
  return Array.from({ length: 6 }, (_, index) => Math.round(totalMs * index / 5))
}

export function buildTraceGanttRowView(node, totalMs) {
  const leftPct = (node.start_ms / totalMs) * 100
  const widthPct = Math.max(((node.latency_ms ?? 0) / totalMs) * 100, 0.5)
  return {
    displayName: getTraceNodeDisplayName(node),
    latencyLabel: `${node.latency_ms ?? 0}ms`,
    barStyle: {
      left: `${leftPct}%`,
      width: `${widthPct}%`,
      backgroundColor: resolveTraceNodeTypeColor(node.node_type, '#667085'),
      opacity: 0.85,
      minWidth: '4px',
    },
    title: `${getTraceNodeDisplayName(node)}: ${node.start_ms}ms -> ${node.end_ms}ms (${node.latency_ms ?? 0}ms) [${node.status ?? '未知'}]`,
    statusClassName: (node.status ?? '').toUpperCase() === 'OK' ? 'text-green-600' : 'text-red-500',
  }
}

export function buildGroupDagFlowInputs({ group, traceData, traceGroupState }) {
  const hasObservedTrace = traceGroupState && traceGroupState.groupNodes.length > 0
  const nodeIdSet = new Set(group.nodeIds)
  return {
    groupNodes: hasObservedTrace
      ? traceGroupState.groupNodes.map((node) => ({ ...node, layer: node.layer ?? node.side ?? 'host' }))
      : FULL_CHAIN_SCHEMA.nodes.filter((node) => nodeIdSet.has(node.node_id)),
    groupEdges: hasObservedTrace
      ? traceGroupState.groupEdges
      : FULL_CHAIN_SCHEMA.edges.filter((edge) => nodeIdSet.has(edge.source) && nodeIdSet.has(edge.target)),
    activeNodeIds: hasObservedTrace
      ? traceGroupState.groupNodes.map((node) => node.node_id)
      : (traceData ? (traceData.nodes ?? []).map((node) => node.node_id) : null),
    traceNodeMap: hasObservedTrace
      ? traceGroupState.traceNodeMap
      : (traceData ? Object.fromEntries((traceData.nodes ?? []).map((node) => [node.node_id, node])) : {}),
  }
}

export function buildGroupDagHeaderMeta({ group, traceData, traceGroupState }) {
  return {
    activeCount: traceGroupState ? traceGroupState.activeCount : 0,
    totalNodeCount: traceData ? getTraceGroupNodeIds(group).length : group.nodeIds.length,
    immuneGroupMeta: group.id === 'immune'
      ? buildImmuneStageMeta(traceGroupState?.groupNodes ?? [])
      : null,
  }
}
