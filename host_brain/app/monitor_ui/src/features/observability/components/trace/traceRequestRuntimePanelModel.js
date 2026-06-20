import { MarkerType } from '@xyflow/react'
import { applyDagreLayout } from '../../../../utils/traceLayout.js'
import { getTraceNodeDisplayName } from './traceNodeText.js'
import { getNodePreview } from './tracePreviewModel.js'

export function dedupeStreamEvents(...sources) {
  const merged = []
  const seen = new Set()
  for (const source of sources) {
    for (const item of Array.isArray(source) ? source : []) {
      const key = `${item?.ts_ms ?? ''}|${item?.phase ?? ''}|${item?.event_type ?? ''}|${item?.node_id ?? ''}|${item?.chunk ?? ''}`
      if (seen.has(key)) continue
      seen.add(key)
      merged.push(item)
    }
  }
  return merged.sort((a, b) => (b?.ts_ms ?? 0) - (a?.ts_ms ?? 0))
}

export function buildDeltaEntries(deltaItem) {
  return (Array.isArray(deltaItem?.changes) ? deltaItem.changes : []).map((change, index) => {
    const node = change?.node ?? {}
    const changeType = change?.change_type || 'updated'
    return {
      entryId: `${changeType}:${node.node_id}:${node.ts_ms ?? index}`,
      label: changeType === 'added' ? '新增' : '更新',
      nodeId: node.node_id,
      displayName: getTraceNodeDisplayName(node),
      status: node.status,
      previousStatus: change?.previous_status || null,
      tsMs: node.ts_ms ?? null,
      preview: getNodePreview(node),
    }
  })
}

export function mergeDeltaEntries(previous, incoming) {
  const merged = [...incoming, ...previous]
  const deduped = []
  const seen = new Set()
  for (const entry of merged) {
    if (!entry?.entryId || seen.has(entry.entryId)) continue
    seen.add(entry.entryId)
    deduped.push(entry)
    if (deduped.length >= 20) break
  }
  return deduped
}

export function buildSubgraphFlow(subgraph, onSelectNode, selectedNode) {
  const sourceNodes = Array.isArray(subgraph?.nodes) ? subgraph.nodes : []
  const sourceEdges = Array.isArray(subgraph?.edges) ? subgraph.edges : []
  const flowNodes = sourceNodes.map((node) => ({
    id: node.node_id,
    type: 'customNode',
    data: {
      ...node,
      active: selectedNode?.node_id === node.node_id,
      onSelect: () => onSelectNode?.(node),
      layer: node.layer || node.side || 'host',
    },
    position: { x: 0, y: 0 },
  }))
  const flowEdges = sourceEdges.map((edge, index) => ({
    id: `${subgraph?.subgraph_id || 'sg'}:${edge.source}:${edge.target}:${index}`,
    source: edge.source,
    target: edge.target,
    animated: false,
    style: { stroke: '#C7D2FE', strokeWidth: 1.5 },
    markerEnd: {
      type: MarkerType.ArrowClosed,
      color: '#A5B4FC',
      width: 16,
      height: 16,
    },
  }))
  return applyDagreLayout(flowNodes, flowEdges)
}
